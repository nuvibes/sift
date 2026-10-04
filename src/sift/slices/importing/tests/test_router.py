# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Importing screen's four endpoints.

## What this file covers

The slice's own logic is covered beside it; this is everything between a request and that logic,
which is where a 404, a 400 and an admin check live.

## Why the routes are called directly rather than through a client

Both, and each for what only it can answer. The two below that go through a `TestClient` are about
WIRING: that the dependency helpers reach the parts the application assembled, and that a guest is
refused, and neither can be answered by calling a function whose arguments were handed in. The
rest are about what the route DECIDES, and calling them directly is what lets a real policy, a real
preferences store and a real probed file stand behind them without a scan having to run first.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from sift.kernel.access import AssetFilter, Repository, Role, Viewer, Where
from sift.kernel.config import Settings, get_settings
from sift.kernel.content import ContentStore, LibraryStore
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.kernel.jobs import Family, JobQueue
from sift.kernel.jobs.families import AGAIN
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.wiring import part_of_app
from sift.main import create_app
from sift.slices.importing.jobs import GENERATE, GENERATE_FILE, IDENTIFY, IDENTIFY_FILE
from sift.slices.importing.models import (
    BuildRequest,
    RetryRequest,
    RunNowRequest,
    RunNowStarted,
    SetFolderAnswers,
)
from sift.slices.importing.products import PRODUCTS, ProductRegistry, Reading

# NOT `from sift.slices.importing import router`: the package's `__init__` binds that name to
# the APIRouter OBJECT, so the import succeeds and every attribute read off it is missing.
from sift.slices.importing.router import (
    _runs_that_made,
    _switch_label,
    _wall_seconds_per_file,
    build_sheet,
    folder_answers,
    retry_build,
    run_now,
    run_now_passes,
    set_folder_answers,
    start_build,
)
from sift.slices.importing.store import RootPreferences
from sift.slices.importing.tests.test_build import Made, a_probed_file, registry_of
from sift.slices.importing.tests.test_policy import (
    Preferences,
    policy_over,
)
from sift.testing.auth import establish_session
from sift.testing.fixtures import LibraryRoot

pytestmark = pytest.mark.anyio

#: Not a viewer any route reads. `require_admin` has already answered by the time these run, and
#: every one of them takes the viewer only so that FastAPI declares the dependency, which is what
#: the two `TestClient` tests at the foot of this file are for.
NOBODY = cast(Viewer, None)


class _Wall:
    """The Files wall's count, as the sheet asks it, over a library where every file is visible.

    The sheet counts a product's given-up files as the wall its line opens counts them
    (`Repository.count_visible`). Whether that count keeps to the vault's rule is the vault gate's
    question (`test_every_count_hides_what_the_vault_hides`), over the real application; this
    stands in for the wall so these tests ask only what the sheet does with the answer, and it
    writes down what it was asked, so a sheet that asked about the wrong set fails here.
    """

    def __init__(self, content: ContentStore) -> None:
        self._content = content
        self.asked: list[AssetFilter] = []

    async def count_visible(self, viewer: Viewer, asset_filter: AssetFilter) -> int:
        self.asked.append(asset_filter)
        where = asset_filter.where
        assert isinstance(where, Where) and where.key == "left_out"
        return await self._content.verdict_count(str(where.values[0]))


def _wall(content: ContentStore) -> Repository:
    return cast(Repository, _Wall(content))


#: The viewer a PRESS is made by. Starting a Build reads who pressed it: the run it queues names
#: them (`jobs.requested_by`), so those calls take a real one rather than `NOBODY`.
PRESSER = Viewer(id="acct-presser", role=Role.ADMIN)


# --- what the routes decide ---------------------------------------------------------------------


async def test_a_folder_is_listed_with_the_answers_it_gives_differently(
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    library_root: LibraryRoot,
) -> None:
    """Every folder, and the keys a folder is allowed to answer."""
    prefs = RootPreferences(temp_db)
    await prefs.set(library_root.id, {"importing.generate": False})
    policy = policy_over(Preferences(), content_store, prefs)

    listed = await folder_answers(library=library_store, policy=policy, prefs=prefs, viewer=NOBODY)

    assert [one.root_id for one in listed.folders] == [library_root.id]
    assert listed.folders[0].answers == {"importing.generate": False}
    assert "importing.generate" in listed.keys


async def test_answering_for_a_folder_nobody_has_is_a_404(
    temp_db: Database, content_store: ContentStore, library_store: LibraryStore
) -> None:
    """Refused before anything is stored. A row against a folder that is not there describes a
    decision about nothing, and nothing would ever read it back."""
    prefs = RootPreferences(temp_db)
    policy = policy_over(Preferences(), content_store, prefs)

    with pytest.raises(HTTPException) as refused:
        await set_folder_answers(
            root_id=new_id(),
            body=SetFolderAnswers(answers={"importing.generate": False}),
            library=library_store,
            policy=policy,
            prefs=prefs,
            viewer=NOBODY,
        )

    assert refused.value.status_code == 404


async def test_a_key_nothing_gates_is_refused_rather_than_stored(
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    library_root: LibraryRoot,
) -> None:
    """The route's own words: an override no job reads is a row that looks like a decision and is
    not one. Named in the refusal, so the answer says which key it would not take."""
    prefs = RootPreferences(temp_db)
    policy = policy_over(Preferences(), content_store, prefs)

    with pytest.raises(HTTPException) as refused:
        await set_folder_answers(
            root_id=library_root.id,
            body=SetFolderAnswers(answers={"importing.nonsense": False}),
            library=library_store,
            policy=policy,
            prefs=prefs,
            viewer=NOBODY,
        )

    assert refused.value.status_code == 400
    assert "importing.nonsense" in str(refused.value.detail)
    # And nothing was written: a refused answer leaves the folder following the library.
    assert await prefs.for_root(library_root.id) == {}


async def test_answering_for_a_folder_stores_it_and_hands_it_back(
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    library_root: LibraryRoot,
) -> None:
    """The happy path, and the answer comes back from the STORE rather than from the request,
    which is what makes a null putting a key back to following the library visible."""
    prefs = RootPreferences(temp_db)
    policy = policy_over(Preferences(), content_store, prefs)

    answered = await set_folder_answers(
        root_id=library_root.id,
        body=SetFolderAnswers(answers={"importing.generate": False}),
        library=library_store,
        policy=policy,
        prefs=prefs,
        viewer=NOBODY,
    )

    assert answered.root_id == library_root.id
    assert answered.name == "library"
    assert answered.answers == {"importing.generate": False}


async def test_the_sheet_counts_each_product_and_ticks_what_the_switches_want(
    temp_db: Database,
    content_store: ContentStore,
    settings: Settings,
    library_root: LibraryRoot,
    job_queue: JobQueue,
) -> None:
    """Every number on the sheet is taken now and exact; the union is what the run is weighed by;
    a machine that has never built has no price rather than a guess."""
    a, b, c = [
        await a_probed_file(library_root, content_store, settings, f"{n}.mp4") for n in "abc"
    ]
    registry = registry_of(Made("pictures", {a, b}), Made("faces", {b, c}, on=False))
    ledger = Ledger(temp_db, families_of={})

    sheet = await build_sheet(
        content=content_store,
        access=_wall(content_store),
        queue=job_queue,
        ledger=ledger,
        products=registry,
        viewer=NOBODY,
    )

    assert [(row.key, row.switched_on, row.files) for row in sheet.rows] == [
        ("pictures", True, 2),
        ("faces", False, 2),
    ]
    # The union over the TICKED rows: faces is off, so b counts once and c, which only faces would
    # touch, is not counted at all. A row that is off still says how many files it would touch.
    assert sheet.files == 2
    assert sheet.rows[0].seconds_per_file is None and sheet.rows[0].slow_seconds is None
    assert sheet.running is False
    assert sheet.identifying == 0
    assert sheet.night_start == "23:00"
    assert sheet.measure_first is False, "a registry with no machine to measure needs none"
    assert sheet.measure_first is False, "a registry with no machine is one already measured"


async def test_the_sheet_says_what_a_product_gave_up_on_and_can_be_told_to_forget_it(
    temp_db: Database,
    content_store: ContentStore,
    settings: Settings,
    library_root: LibraryRoot,
    job_queue: JobQueue,
) -> None:
    """A file a product could not make is its own line on the row and out of the count: folded
    in, it would be offered by every Build for ever. Trying again forgets the verdicts and nothing
    more: the sheet re-reads with the files back among what is lacking."""
    a, b = [await a_probed_file(library_root, content_store, settings, f"{n}.mp4") for n in "ab"]
    registry = registry_of(Made("pictures", {a, b}))
    await content_store.record_verdict(b, "pictures", code="no_frame", reason="No frame.")
    ledger = Ledger(temp_db, families_of={})
    wall = _Wall(content_store)

    sheet = await build_sheet(
        content=content_store,
        access=cast(Repository, wall),
        queue=job_queue,
        ledger=ledger,
        products=registry,
        viewer=NOBODY,
    )
    assert [(row.files, row.cannot) for row in sheet.rows] == [(1, 1)]
    assert sheet.files == 1
    # Counted as the wall the line's link opens counts it: the Files wall, `left_out:pictures`.
    assert wall.asked == [AssetFilter(where=Where("left_out", ("pictures",)))]

    forgotten = await retry_build(
        body=RetryRequest(products=["pictures", "pictures"]),
        content=content_store,
        products=registry,
        viewer=NOBODY,
    )
    assert forgotten.forgotten == {"pictures": 1}

    sheet = await build_sheet(
        content=content_store,
        access=_wall(content_store),
        queue=job_queue,
        ledger=ledger,
        products=registry,
        viewer=NOBODY,
    )
    assert [(row.files, row.cannot) for row in sheet.rows] == [(2, 0)]
    assert await job_queue.unfinished_by_type() == {}, "forgetting queues nothing"


async def test_the_sheet_says_when_the_run_will_measure_the_machine_first(
    temp_db: Database,
    content_store: ContentStore,
    settings: Settings,
    library_root: LibraryRoot,
    job_queue: JobQueue,
) -> None:
    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    registry = registry_of(Made("pictures", {a}))

    class Never:
        async def measured(self) -> bool:
            return False

        async def measure(self) -> None:
            return None

    registry.machine = Never()
    ledger = Ledger(temp_db, families_of={})

    sheet = await build_sheet(
        content=content_store,
        access=_wall(content_store),
        queue=job_queue,
        ledger=ledger,
        products=registry,
        viewer=NOBODY,
    )

    assert sheet.measure_first is True


async def test_the_sheet_prices_a_row_from_the_last_build_on_this_machine(
    temp_db: Database,
    content_store: ContentStore,
    settings: Settings,
    library_root: LibraryRoot,
    job_queue: JobQueue,
) -> None:
    """The per-file figure is WORKER time and the estimate is WALL time, and they are not the same
    number divided by nothing.

    `files x seconds_per_file` would be worker-seconds presented as a wait, out by however many jobs
    run at once. This run did 25 files in 100 seconds of clock and 460 seconds of worker time: 18.4
    a file of work, 4 a file of waiting. The worker arithmetic would say 18 here.
    """
    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    registry = registry_of(Made("pictures", {a}))
    ledger = Ledger(temp_db, profile="abc", families_of={})
    await temp_db.execute(
        "INSERT INTO work_runs (id, family, started_at, updated_at, finished_at, jobs_done,"
        " profile, products, settings) VALUES ('r1', 'generate', 1000, 1100, 1100, 25, 'abc',"
        " ?, ?)",
        ('{"pictures": {"n": 25, "ms": 460000}}', '{"jobs at once": 6}'),
    )

    sheet = await build_sheet(
        content=content_store,
        access=_wall(content_store),
        queue=job_queue,
        ledger=ledger,
        products=registry,
        viewer=NOBODY,
    )

    assert sheet.rows[0].seconds_per_file == 18.4
    assert (sheet.rows[0].quick_seconds, sheet.rows[0].slow_seconds) == (4, 4)
    # What the estimate assumes, so the sentence on screen can say it.
    assert sheet.rows[0].jobs_at_once == 6


async def test_a_run_that_made_two_things_shares_its_clock_between_them(
    temp_db: Database,
    content_store: ContentStore,
    settings: Settings,
    library_root: LibraryRoot,
    job_queue: JobQueue,
) -> None:
    """Two products of one run were both made inside the same hundred seconds, so charging each of
    them the whole hundred would price a stage at twice the wait it had. The clock is divided by
    the worker time each spent, which makes the estimates of one stage add up to one run.

    Here: 100 seconds of clock, 300 of worker time on the two products, three quarters of it on
    pictures. So pictures gets 75 seconds over 25 files (3 each) and fingerprints 25 over 20 (1.25
    each).

    `decode_once` is in the row and is NOT one of them. It is the whole-file read shared by every
    product on a file, timed under the same `build.` prefix, so it lands in the same place, and
    counting it as a third claimant would charge it a share of the clock that nothing on the sheet
    would ever spend, and under-price every product.
    """
    a, b = [await a_probed_file(library_root, content_store, settings, f"{n}.mp4") for n in "ab"]
    registry = registry_of(Made("pictures", {a, b}), Made("fingerprints", {a}))
    ledger = Ledger(temp_db, profile="abc", families_of={})
    await temp_db.execute(
        "INSERT INTO work_runs (id, family, started_at, updated_at, finished_at, jobs_done,"
        " profile, products) VALUES ('r1', 'generate', 1000, 1100, 1100, 30, 'abc', ?)",
        (
            '{"pictures": {"n": 25, "ms": 225000},'
            ' "fingerprints": {"n": 20, "ms": 75000},'
            ' "decode_once": {"n": 30, "ms": 900000}}',
        ),
    )

    sheet = await build_sheet(
        content=content_store,
        access=_wall(content_store),
        queue=job_queue,
        ledger=ledger,
        products=registry,
        viewer=NOBODY,
    )

    priced = {row.key: (row.quick_seconds, row.slow_seconds) for row in sheet.rows}
    assert priced == {"pictures": (6, 6), "fingerprints": (1, 1)}
    # Nothing recorded it, so nothing is claimed about it.
    assert sheet.rows[0].jobs_at_once is None


async def test_a_run_too_short_to_time_prices_no_wait(
    temp_db: Database,
    content_store: ContentStore,
    settings: Settings,
    library_root: LibraryRoot,
    job_queue: JobQueue,
) -> None:
    """A run that began and ended inside one second has no clock to divide, so there is no estimate:
    a guess would read as a measurement. What it DID measure, the worker time, still stands."""
    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    registry = registry_of(Made("pictures", {a}))
    ledger = Ledger(temp_db, profile="abc", families_of={})
    await temp_db.execute(
        "INSERT INTO work_runs (id, family, started_at, updated_at, finished_at, jobs_done,"
        " profile, products) VALUES ('r1', 'generate', 1000, 1000, 1000, 4, 'abc', ?)",
        ('{"pictures": {"n": 4, "ms": 6000}}',),
    )

    sheet = await build_sheet(
        content=content_store,
        access=_wall(content_store),
        queue=job_queue,
        ledger=ledger,
        products=registry,
        viewer=NOBODY,
    )

    assert sheet.rows[0].seconds_per_file == 1.5
    assert sheet.rows[0].quick_seconds is None and sheet.rows[0].slow_seconds is None


async def test_a_row_is_a_window_between_the_cheapest_and_dearest_runs_that_made_it(
    temp_db: Database,
    content_store: ContentStore,
    settings: Settings,
    library_root: LibraryRoot,
    job_queue: JobQueue,
) -> None:
    """A run over photographs made pictures at a twentieth of a second of clock each, one over
    videos at five seconds, so two files are a window from nothing to ten seconds, not the newest
    run's one figure. The newest Generate run made only music and hides neither; too few files
    in the runs gives no window at all."""
    a, b = [await a_probed_file(library_root, content_store, settings, f"{n}.mp4") for n in "ab"]
    registry = registry_of(Made("pictures", {a, b}))
    ledger = Ledger(temp_db, profile="abc", families_of={})
    for run_id, began, took, made, at_once in (
        ("r1", 1000, 100, '{"pictures": {"n": 20, "ms": 400000}}', 8),
        ("r2", 2000, 2, '{"pictures": {"n": 40, "ms": 8000}}', 6),
        ("r3", 3000, 10, '{"music": {"n": 3, "ms": 100}}', 3),
    ):
        await temp_db.execute(
            "INSERT INTO work_runs (id, family, started_at, updated_at, finished_at, jobs_done,"
            " profile, products, settings) VALUES (?, 'generate', ?, ?, ?, 1, 'abc', ?, ?)",
            (
                run_id,
                began,
                began + took,
                began + took,
                made,
                json.dumps({"jobs at once": at_once}),
            ),
        )

    sheet = await build_sheet(
        content=content_store,
        access=_wall(content_store),
        queue=job_queue,
        ledger=ledger,
        products=registry,
        viewer=NOBODY,
    )

    row = sheet.rows[0]
    assert (row.quick_seconds, row.slow_seconds) == (0, 10)
    assert row.jobs_at_once == 6, "the newest run that made it"

    await temp_db.execute("DELETE FROM work_runs WHERE id = 'r2'")
    sheet = await build_sheet(
        content=content_store,
        access=_wall(content_store),
        queue=job_queue,
        ledger=ledger,
        products=registry,
        viewer=NOBODY,
    )
    assert sheet.rows[0].slow_seconds == 10, "twenty files are enough"
    await temp_db.execute(
        'UPDATE work_runs SET products = \'{"pictures": {"n": 19, "ms": 400000}}\''
        " WHERE id = 'r1'"
    )
    sheet = await build_sheet(
        content=content_store,
        access=_wall(content_store),
        queue=job_queue,
        ledger=ledger,
        products=registry,
        viewer=NOBODY,
    )
    assert sheet.rows[0].slow_seconds is None, "nineteen are not"


@pytest.mark.parametrize(
    "made",
    [
        {"n": 0, "ms": 5000},  # counted nothing, so there is no "per file" to divide by
        {"n": 4, "ms": 0},  # made files and spent no measured time on them
    ],
)
def test_a_product_the_run_did_not_really_time_prices_no_wait(made: dict[str, int]) -> None:
    """A run can hold a row for a product and still not be able to price it. Dividing by nought
    files, or giving a share of the clock to a stage that spent none of it, would put a number on
    the sheet that no run measured."""
    from types import SimpleNamespace

    run = SimpleNamespace(seconds=100, products={"pictures": made})
    assert (
        _wall_seconds_per_file(
            run,  # type: ignore[arg-type]
            "pictures",
            products={"pictures"},
        )
        is None
    )


def test_no_run_prices_no_wait() -> None:
    """Nothing has been built on this machine yet, so there is no clock to divide."""
    assert _wall_seconds_per_file(None, "pictures", products={"pictures"}) is None


def test_the_pace_is_read_from_the_newest_runs_until_they_hold_enough_files() -> None:
    """The pace as it is now, not the history: once the newest runs hold enough of this product's
    files, the older ones are not read at all, however many there are."""
    from types import SimpleNamespace

    from sift.kernel.jobs.ledger import PACE_OVER_ITEMS

    newest = SimpleNamespace(products={"pictures": {"n": PACE_OVER_ITEMS - 1}})
    music = SimpleNamespace(products={"music": {"n": 50}})
    enough = SimpleNamespace(products={"pictures": {"n": 1}})
    older = SimpleNamespace(products={"pictures": {"n": 500}})

    made = _runs_that_made([newest, music, enough, older], "pictures")  # type: ignore[list-item]

    assert made == [newest, enough]


async def test_starting_a_build_queues_the_run_with_its_count(
    content_store: ContentStore,
    settings: Settings,
    library_root: LibraryRoot,
    job_queue: JobQueue,
    handlers: None,
) -> None:
    a, b = [await a_probed_file(library_root, content_store, settings, f"{n}.mp4") for n in "ab"]
    registry = registry_of(Made("pictures", {a}), Made("faces", {a, b}))

    started = await start_build(
        body=BuildRequest(products=["faces", "pictures", "faces"]),
        content=content_store,
        queue=job_queue,
        products=registry,
        viewer=PRESSER,
    )

    assert started.queued is True and started.files == 2 and started.starts_at is None
    assert started.job_id is not None
    job = await job_queue.get(started.job_id)
    assert job is not None
    assert job.payload == {"products": ["faces", "pictures"], "files": 2}, (
        "the ticked products once each in the order given, and the count the sheet stated"
    )
    assert job.run_after is None
    assert job.requested_by == PRESSER.id, "the pass the press starts names who pressed it"


@pytest.mark.parametrize("body", [{"at": "quiet"}, {"tonight": True}])
async def test_a_build_asked_for_quiet_hours_waits_for_them_and_pauses_with_them(
    content_store: ContentStore,
    settings: Settings,
    library_root: LibraryRoot,
    job_queue: JobQueue,
    handlers: None,
    body: dict[str, object],
) -> None:
    """Marked on the run itself, so every page and task it hands out waits for the range and stops
    being handed out when it closes. `tonight` is the former spelling and means the same, until the
    client sends `at`."""
    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    registry = registry_of(Made("pictures", {a}))
    started = await start_build(
        body=BuildRequest(products=["pictures"], **body),  # type: ignore[arg-type]
        content=content_store,
        queue=job_queue,
        products=registry,
        viewer=PRESSER,
    )
    assert started.starts_at is not None and started.job_id is not None
    job = await job_queue.get(started.job_id)
    assert job is not None and job.timing == "quiet" and job.run_after is None
    assert job.requested_by == PRESSER.id


async def test_a_family_with_nothing_lacking_starts_no_run_of_its_own(
    content_store: ContentStore,
    settings: Settings,
    library_root: LibraryRoot,
    job_queue: JobQueue,
    handlers: None,
) -> None:
    """One request, two families, and only one of them has anything to do.

    The pane asks for Generate and Identify separately, so a request naming both gets both: each
    weighed by its OWN files. A family with nothing lacking must start no run: a Generate run
    queued over a library that needs no pictures walks the whole library to find nothing, and shows
    up on the Activity screen as work somebody is waiting on.

    The early return above this cannot cover it: that one fires when NOTHING at all is lacking, and
    here something is.
    """
    a = await a_probed_file(library_root, content_store, settings, "a.mp4")
    registry = registry_of(
        Made("pictures", {a}),
        Made("faces", set(), family=Family.IDENTIFY),
    )

    started = await start_build(
        body=BuildRequest(products=["pictures", "faces"]),
        content=content_store,
        queue=job_queue,
        products=registry,
        viewer=PRESSER,
    )

    assert started.queued is True and started.files == 1
    assert started.job_ids is not None and len(started.job_ids) == 1, (
        "a run was started for the family that had nothing to do"
    )
    # And it is the GENERATE run, weighed by its own one file rather than by the request's total.
    job = await job_queue.get(started.job_ids[0])
    assert job is not None
    assert job.type == GENERATE
    assert job.payload == {"products": ["pictures"], "files": 1}


async def test_nothing_missing_queues_nothing(
    content_store: ContentStore, job_queue: JobQueue, handlers: None
) -> None:
    started = await start_build(
        body=BuildRequest(products=["pictures"]),
        content=content_store,
        queue=job_queue,
        products=registry_of(Made("pictures", set())),
        viewer=PRESSER,
    )
    assert started.queued is False and started.files == 0
    assert not await job_queue.is_live(GENERATE)
    assert not await job_queue.is_live(IDENTIFY)


async def test_a_product_nothing_knows_is_refused_rather_than_ignored(
    content_store: ContentStore, job_queue: JobQueue
) -> None:
    with pytest.raises(HTTPException) as refused:
        await start_build(
            body=BuildRequest(products=["pictures", "sandwiches"]),
            content=content_store,
            queue=job_queue,
            products=registry_of(Made("pictures", set())),
            viewer=PRESSER,
        )
    assert refused.value.status_code == 400
    assert "sandwiches" in str(refused.value.detail)


async def test_quiet_hours_that_began_today_next_open_tomorrow() -> None:
    """A start hour already gone by today is tomorrow's: the moment a Build asked for "At quiet
    hours" says it will start at is never in the past."""
    from datetime import datetime, timedelta

    async def midnight() -> str:
        return "00:00"

    opens = await ProductRegistry(night_start=midnight).quiet_opens()
    moment = datetime.fromtimestamp(opens)
    assert (moment.hour, moment.minute) == (0, 0)
    assert moment.date() == (datetime.now() + timedelta(days=1)).date()


async def test_the_registry_says_when_quiet_hours_next_open() -> None:
    """Built without the composition root, it is the next time the start hour comes round."""
    from datetime import datetime

    async def eleven() -> str:
        return "23:00"

    opens = await ProductRegistry(night_start=eleven).quiet_opens()
    moment = datetime.fromtimestamp(opens)
    assert (moment.hour, moment.minute) == (23, 0)
    assert opens > int(datetime.now().timestamp())


# --- the wiring, which only a real application can answer ---------------------------------------


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as one:
        yield one


def _sign_in(client: TestClient, role: str) -> None:
    path = client.app.state.database.path  # type: ignore[attr-defined]
    _, token, csrf = establish_session(
        path, role=role, username=f"importing-{role}", password="Importing-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf


def test_the_routes_reach_the_parts_the_application_assembled(client: TestClient) -> None:
    """`_policy` and `_roots` read the composition root, and a handed-in argument cannot say
    whether they found anything. A fresh install has no folders, which is the honest empty answer
    rather than an error."""
    _sign_in(client, "admin")

    listed = client.get("/api/importing/folders")
    assert listed.status_code == 200, listed.text
    assert listed.json()["folders"] == []
    assert listed.json()["keys"], "the policy said nothing may be answered per folder"
    # Every key comes with what it is called, and a key retired into a task's When is called by
    # the task it became, not printed as the key, which is what a page reading labels off the
    # registered rows would show, since several of these keys no longer have one.
    labels = listed.json()["labels"]
    assert sorted(labels) == sorted(listed.json()["keys"])
    assert not [key for key, label in labels.items() if label == key], labels
    # A key retired into a task's When is called by what a FOLDER's answer does, declared where
    # the key was retired: the task's title would promise a run over the folder, and a folder's
    # answer only decides what happens as a file arrives.
    assert labels["importing.generate"] == "Generate as files arrive"
    helps = listed.json()["helps"]
    assert helps["music.fingerprint"].endswith("when Generate runs for this folder.")
    assert set(helps) <= set(labels)

    sheet = client.get("/api/importing/build")
    assert sheet.status_code == 200, sheet.text
    assert [row["key"] for row in sheet.json()["rows"]] == [
        "thumbnails",
        "previews",
        "sprites",
        "fingerprints",
        "faces",
        "meaning",
        "watermarks",
        "music",
    ], "the products the composition root registers, in the sheet's order"
    # Every product the root registers has a family on the Activity screen, or a Build narrowed
    # to it would count as its coordinator's work there.
    from sift.kernel.jobs.families import PRODUCT_FAMILIES

    assert {row["key"] for row in sheet.json()["rows"]} <= set(PRODUCT_FAMILIES)
    assert sheet.json()["files"] == 0


def test_a_guest_is_refused_every_one_of_them(client: TestClient) -> None:
    """Admin-only, and the SERVER is what says so: the screen is not offered to a guest, which is
    a courtesy rather than a guarantee."""
    _sign_in(client, "guest")

    assert client.get("/api/importing/folders").status_code == 403
    assert client.get("/api/importing/build").status_code == 403
    assert client.post("/api/importing/build", json={"products": ["pictures"]}).status_code == 403
    assert client.put("/api/importing/folders/anything", json={"answers": {}}).status_code == 403


async def test_a_request_that_ticks_nothing_queues_nothing(
    content_store: ContentStore, job_queue: JobQueue, handlers: None
) -> None:
    """Nothing ticked is answered without walking the library: there is no product to count."""
    started = await start_build(
        body=BuildRequest(products=[]),
        content=content_store,
        queue=job_queue,
        products=registry_of(Made("pictures", {new_id()})),
        viewer=PRESSER,
    )

    assert started.queued is False and started.files == 0
    assert not await job_queue.is_live(GENERATE)
    assert not await job_queue.is_live(IDENTIFY)


async def test_try_again_forgets_what_a_product_gave_up_on_and_nothing_else(
    library_root: LibraryRoot, content_store: ContentStore, settings: Settings
) -> None:
    """`Try again` beside the failed files of a task: those files are offered to that product's
    next run, and a verdict another product left stands."""
    one = await a_probed_file(library_root, content_store, settings, "one.mp4")
    await content_store.record_verdict(one, "thumbnails", code="no_frame", reason="No frame.")
    await content_store.record_verdict(one, "faces", code="no_frame", reason="No frame.")

    answer = await retry_build(
        body=RetryRequest(products=["thumbnails"]),
        content=content_store,
        products=registry_of(Made("thumbnails", set())),
        viewer=PRESSER,
    )

    assert answer.forgotten == {"thumbnails": 1}
    assert await content_store.verdict_of(one, "thumbnails") is None
    assert await content_store.verdict_of(one, "faces") is not None


async def test_forgetting_the_verdicts_of_a_product_nothing_knows_is_refused(
    content_store: ContentStore,
) -> None:
    with pytest.raises(HTTPException) as refused:
        await retry_build(
            body=RetryRequest(products=["sandwiches"]),
            content=content_store,
            products=registry_of(Made("pictures", set())),
            viewer=NOBODY,
        )
    assert refused.value.status_code == 400
    assert "sandwiches" in str(refused.value.detail)


# --- "Run now" on a file or a selection ---------------------------------------------------------


class _Seeing:
    """The access layer, as much of it as the press reads: which of these this viewer may open."""

    def __init__(self, *, hidden: set[str] | None = None) -> None:
        self.hidden = hidden or set()

    async def visible_of(self, viewer: Viewer, asset_ids: list[str]) -> set[str]:
        return {one for one in asset_ids if one not in self.hidden}


def _press_registry(*made: Made, cannot: str | None = None) -> ProductRegistry:
    """Products as the composition root declares them for a press: the picture gated by the
    hover-preview switch and made again on a press, faces the same, and a reading of the file."""
    from dataclasses import replace

    registry = registry_of()
    for one in made:
        product = one.product()
        if one.key == "pictures":

            async def refusing() -> str | None:
                return cannot

            product = replace(
                product,
                governed_by="preview",
                doing="Making hover previews for",
                again=True,
                cannot_run=refusing,
            )
        registry.register(product)
    registry.register_reading(
        Reading(key="details", label="File details", help="", doing="Reading", job_type="preview")
    )
    return registry


_ALL_ON = {"importing.generate": True, "performance.generate_previews": True}


@pytest.fixture
def saying(content_store: ContentStore, temp_db: Database) -> Any:
    """The real gate over the real folder answers, with the library's switches as given."""

    def policy(**values: bool) -> Any:
        return policy_over(Preferences(**values), content_store, RootPreferences(temp_db))

    return policy


async def _press(
    registry: ProductRegistry,
    queue: JobQueue,
    run: str,
    ids: list[str],
    *,
    policy: Any,
    seeing: _Seeing | None = None,
) -> RunNowStarted:
    return await run_now(
        body=RunNowRequest(run=run, asset_ids=ids),
        access=cast(Any, seeing or _Seeing()),
        queue=queue,
        policy=policy,
        products=registry,
        viewer=PRESSER,
    )


async def test_the_press_offers_every_per_file_pass_grouped_by_the_stage_that_owns_it(
    handlers: None,
) -> None:
    """The list the menus draw: Scan's reading, then each family's products under the stage's own
    press, in the words the Importing pane uses, read from the declarations, never listed here."""
    registry = _press_registry(
        Made("pictures", set()), Made("faces", set(), family=Family.IDENTIFY)
    )

    listed = await run_now_passes(products=registry, viewer=NOBODY)

    assert [(group.family, group.label) for group in listed.groups] == [
        ("scan", "Scan now"),
        ("generate", "Generate now"),
        ("identify", "Identify now"),
    ]
    assert [[one.key for one in group.passes] for group in listed.groups] == [
        ["details"],
        ["pictures"],
        ["faces"],
    ]
    assert [(group.every.key, group.every.label) for group in listed.groups] == [
        ("scan:all", "Scan all"),
        ("generate:all", "Generate all"),
        ("identify:all", "Identify all"),
    ], "each stage's every-pass press, named by the server"


async def test_a_pass_that_does_not_run_for_one_file_is_refused(
    job_queue: JobQueue, handlers: None, saying: Any
) -> None:
    """A whole-library pass (looking for near duplicates, say) is not a smaller thing for one
    file, and a name nothing declared is the same answer."""
    with pytest.raises(HTTPException) as refused:
        await _press(
            _press_registry(Made("pictures", set())),
            job_queue,
            "dedup",
            [new_id()],
            policy=saying(**_ALL_ON),
        )
    assert refused.value.status_code == 422


async def test_a_file_this_admin_may_not_open_is_the_404_a_made_up_one_gets(
    job_queue: JobQueue, handlers: None, saying: Any
) -> None:
    a, b = new_id(), new_id()
    with pytest.raises(HTTPException) as refused:
        await _press(
            _press_registry(Made("pictures", set())),
            job_queue,
            "pictures",
            [a, b],
            seeing=_Seeing(hidden={b}),
            policy=saying(**_ALL_ON),
        )
    assert refused.value.status_code == 404
    assert not await job_queue.is_live(GENERATE_FILE), "and nothing was queued for the other one"


async def test_a_press_queues_one_task_per_file_ahead_and_named_for_the_presser(
    job_queue: JobQueue, handlers: None, saying: Any
) -> None:
    """Again means again: the task carries the key the picture's maker reads, it runs ahead of the
    library-wide work, it names who pressed, and the sentence is the pass's own words."""
    from sift.kernel.jobs import WAITED_ON_PRIORITY

    a, b = new_id(), new_id()
    started = await _press(
        _press_registry(Made("pictures", set())),
        job_queue,
        "pictures",
        [a, b, a],
        policy=saying(**_ALL_ON),
    )

    assert started.queued == 2 and started.said == "Making hover previews for 2 files."
    tasks = await job_queue.list(job_type=GENERATE_FILE, limit=10)
    assert sorted(job.payload["asset_id"] for job in tasks.jobs) == sorted([a, b])
    for job in tasks.jobs:
        assert job.payload["products"] == ["pictures"] and job.payload[AGAIN] is True
        assert job.priority == WAITED_ON_PRIORITY and job.requested_by == PRESSER.id


async def test_a_file_whose_work_is_already_running_is_not_queued_twice(
    job_queue: JobQueue, handlers: None, saying: Any
) -> None:
    """Whoever started it (an arriving file's own job, a pass over the library, an earlier press),
    it is answering exactly this right now. Alone, the press is refused with a sentence."""
    a, b = new_id(), new_id()
    await job_queue.enqueue("preview", {"asset_id": a})
    assert await job_queue.claim("a-worker") is not None, "a's own work is under way"
    registry = _press_registry(Made("pictures", set()))

    started = await _press(registry, job_queue, "pictures", [a, b], policy=saying(**_ALL_ON))
    assert started.queued == 1 and started.waiting == 1
    assert started.said == "Making hover previews for 1 file. Left out: 1 file already waiting."

    with pytest.raises(HTTPException) as refused:
        await _press(registry, job_queue, "pictures", [a], policy=saying(**_ALL_ON))
    assert refused.value.status_code == 409
    assert refused.value.detail == "Nothing was queued: this file is already waiting for it."


async def test_a_press_pulls_the_files_own_waiting_work_forward(
    job_queue: JobQueue, handlers: None, saying: Any
) -> None:
    """A press is now, even for work already in the queue. An arriving file's pictures held for
    quiet hours are not left there ("already waiting for it") until the range opens: the press
    collapses onto the waiting row instead, runs it now, ahead, named for the presser, and adds no
    second row. Pressed twice, it is still one row."""
    from sift.kernel.jobs import WAITED_ON_PRIORITY

    a, b = new_id(), new_id()
    held = await job_queue.enqueue("preview", {"asset_id": a})
    registry = _press_registry(Made("pictures", set()))

    started = await _press(registry, job_queue, "pictures", [a, b], policy=saying(**_ALL_ON))
    assert started.queued == 2 and started.waiting == 0
    assert started.said == "Making hover previews for 2 files."

    row = await job_queue.get(held)
    assert row is not None and row.timing == "now", "run now, not at quiet hours"
    assert row.priority == WAITED_ON_PRIORITY and row.requested_by == PRESSER.id
    assert (await job_queue.list(job_type="preview", limit=10)).total == 1, "no second row"

    again = await _press(registry, job_queue, "pictures", [b], policy=saying(**_ALL_ON))
    assert again.queued == 1
    assert (await job_queue.list(job_type=GENERATE_FILE, limit=10)).total == 1, "still one row"


async def test_a_task_for_another_product_of_the_same_file_does_not_count_as_waiting(
    job_queue: JobQueue, handlers: None, saying: Any
) -> None:
    a = new_id()
    await job_queue.enqueue(GENERATE_FILE, {"asset_id": a, "products": ["sprites"]})

    started = await _press(
        _press_registry(Made("pictures", set())),
        job_queue,
        "pictures",
        [a],
        policy=saying(**_ALL_ON),
    )

    assert started.queued == 1 and started.waiting == 0


async def test_a_switch_that_says_no_leaves_the_file_out_and_is_named_when_it_refuses_all(
    job_queue: JobQueue, handlers: None, saying: Any
) -> None:
    """Refused the way Importing refuses it, by the same rule: the press names the switch."""
    with pytest.raises(HTTPException) as refused:
        await _press(
            _press_registry(Made("pictures", set())),
            job_queue,
            "pictures",
            [new_id(), new_id()],
            policy=saying(**{**_ALL_ON, "performance.generate_previews": False}),
        )
    assert refused.value.status_code == 409
    assert refused.value.detail == (
        'Nothing was queued: 2 files with "Generate hover previews" switched off.'
    ), "the switch as Settings names it"
    assert _switch_label("no.such.key") == "no.such.key", "an undeclared key is named, not dropped"
    assert not await job_queue.is_live(GENERATE_FILE)


async def test_a_pass_that_cannot_run_here_is_refused_with_its_own_sentence(
    job_queue: JobQueue, handlers: None, saying: Any
) -> None:
    with pytest.raises(HTTPException) as refused:
        await _press(
            _press_registry(Made("pictures", set()), cannot="The graphics card is not there."),
            job_queue,
            "pictures",
            [new_id()],
            policy=saying(**_ALL_ON),
        )
    assert refused.value.status_code == 409
    assert refused.value.detail == "The graphics card is not there."


async def test_a_pass_that_only_fills_gaps_is_offered_only_where_the_file_lacks_it(
    job_queue: JobQueue, handlers: None, saying: Any
) -> None:
    """A maker that skips a file which has what it makes is declared so, and the press says how
    many already had it rather than reporting work it will not do."""
    a, b = new_id(), new_id()
    registry = _press_registry(Made("faces", {a}, family=Family.IDENTIFY))

    started = await _press(registry, job_queue, "faces", [a, b], policy=saying(**_ALL_ON))

    assert (started.queued, started.had) == (1, 1)
    assert started.said == "Faces 1 file. Left out: 1 file already had it."
    [task] = (await job_queue.list(job_type=IDENTIFY_FILE, limit=10)).jobs
    assert task.payload == {"asset_id": a, "products": ["faces"]}, "no AGAIN for a gap-filler"


async def test_what_a_press_left_out_is_one_list_without_semicolons(
    job_queue: JobQueue, handlers: None, saying: Any
) -> None:
    """Two reasons are joined the way a person lists them, never spliced with a semicolon."""
    a, b, c = new_id(), new_id(), new_id()
    await job_queue.enqueue(IDENTIFY_FILE, {"asset_id": c, "products": ["faces"]})
    assert await job_queue.claim("a-worker") is not None, "c's own work is under way"
    registry = _press_registry(Made("faces", {a}, family=Family.IDENTIFY))

    started = await _press(registry, job_queue, "faces", [a, b, c], policy=saying(**_ALL_ON))

    assert started.said == (
        "Faces 1 file. Left out: 1 file already waiting and 1 file already had it."
    )

    with pytest.raises(HTTPException) as refused:
        await _press(registry, job_queue, "faces", [b], policy=saying(**_ALL_ON))
    assert refused.value.detail == "Nothing was queued: this file already has it."


async def test_reading_a_file_again_queues_its_own_read(
    job_queue: JobQueue, handlers: None, saying: Any
) -> None:
    a = new_id()
    started = await _press(_press_registry(), job_queue, "details", [a], policy=saying(**_ALL_ON))

    assert started.said == "Reading 1 file."
    [job] = (await job_queue.list(job_type="preview", limit=10)).jobs
    assert job.payload == {"asset_id": a} and job.requested_by == PRESSER.id


def test_a_press_names_at_most_what_a_bulk_sheet_may() -> None:
    from pydantic import ValidationError

    from sift.slices.importing.models import MAX_RUN_FILES

    RunNowRequest(run="faces", asset_ids=[new_id() for _ in range(MAX_RUN_FILES)])
    with pytest.raises(ValidationError):
        RunNowRequest(run="faces", asset_ids=[new_id() for _ in range(MAX_RUN_FILES + 1)])


def test_run_now_offers_every_product_the_sheet_counts_and_reading_the_file_again(
    client: TestClient,
) -> None:
    """THE REGISTRY GATE, against the application as assembled: every pass the press offers is
    one the Build sheet already counts (whose maker is handed one file's task, `{"asset_id"}`, by
    `jobs.build_file`) or a reading whose job type is a claimed handler reading one file. A pass
    added to the sheet is offered by the same declaration; nothing lists them twice."""
    from sift.kernel.jobs import registered_handlers

    _sign_in(client, "admin")
    listed = client.get("/api/importing/run-now")
    sheet = client.get("/api/importing/build")
    assert listed.status_code == sheet.status_code == 200, listed.text

    offered = {one["key"] for group in listed.json()["groups"] for one in group["passes"]}
    counted = {row["key"] for row in sheet.json()["rows"]}
    assert offered == counted | {"details"}

    products = part_of_app(cast(FastAPI, client.app), PRODUCTS)
    for reading in products.readings():
        assert reading.job_type in registered_handlers(), f"{reading.key} queues a type nobody runs"
    assert (
        client.post("/api/assets/run", json={"run": "details", "asset_ids": [new_id()]}).status_code
        == 404
    ), "a made-up file is missing, not read"


def test_a_guest_is_refused_run_now(client: TestClient) -> None:
    _sign_in(client, "guest")

    assert client.get("/api/importing/run-now").status_code == 403
    assert (
        client.post("/api/assets/run", json={"run": "details", "asset_ids": [new_id()]}).status_code
        == 403
    )


def test_a_pass_name_is_taken_once_whether_by_a_product_or_a_reading() -> None:
    """One press names one pass, so a product and a reading may not share a key, in either order."""
    registry = _press_registry(Made("pictures", set()))
    with pytest.raises(ValueError):
        registry.register_reading(
            Reading(key="pictures", label="", help="", doing="", job_type="preview")
        )
    with pytest.raises(ValueError):
        registry.register_reading(
            Reading(key="details", label="", help="", doing="", job_type="preview")
        )
    with pytest.raises(ValueError):
        registry.register(Made("details", set()).product())


# --- A stage's every pass: "Identify all" --------------------------------------------------------


def _identify_registry(lacking_faces: set[str], *, cannot: str | None = None) -> ProductRegistry:
    """Three Identify passes as a press meets them: faces fills gaps (only `lacking_faces` want
    it), marks is made again and gated by the hover-preview switch, and meaning may not run here."""
    from dataclasses import replace

    async def refusing() -> str | None:
        return cannot

    registry = _press_registry()
    registry.register(Made("faces", lacking_faces, family=Family.IDENTIFY).product())
    registry.register(
        replace(
            Made("marks", set(), family=Family.IDENTIFY).product(),
            governed_by="preview",
            again=True,
        )
    )
    registry.register(
        replace(
            Made("meaning", set(), family=Family.IDENTIFY).product(),
            again=True,
            cannot_run=refusing,
        )
    )
    return registry


async def test_identify_all_runs_every_identify_pass_in_one_press_and_says_so(
    job_queue: JobQueue, handlers: None, saying: Any
) -> None:
    """ONE REQUEST, EVERY PASS OF THE STAGE: the server expands `identify:all` into the passes it
    draws under Identify now, each pressed exactly as a single pass is, and answers with one
    sentence counting FILES: a file handed two passes is one file."""
    a, b = new_id(), new_id()
    registry = _identify_registry({a})

    started = await _press(registry, job_queue, "identify:all", [a, b], policy=saying(**_ALL_ON))

    assert started.passes == ["faces", "marks", "meaning"]
    assert started.queued == 2 and started.had == 1
    assert started.said == (
        "Identifying 2 files: faces, marks, meaning. Faces: 1 file already had it."
    )
    tasks = (await job_queue.list(job_type=IDENTIFY_FILE, limit=20)).jobs
    assert sorted((job.payload["products"][0], job.payload["asset_id"]) for job in tasks) == sorted(
        [("faces", a), ("marks", a), ("marks", b), ("meaning", a), ("meaning", b)]
    ), "one task per pass per file, each the single press's own task"
    assert not await job_queue.is_live(GENERATE_FILE), "and nothing from another stage"


async def test_identify_all_leaves_out_and_names_a_pass_a_switch_refuses_or_that_cannot_run(
    job_queue: JobQueue, handlers: None, saying: Any
) -> None:
    """A pass refused by a switch, or unable to run on this machine, is left out and named as a
    single pass's press names it (the rest still run) and only a press where no pass queued
    anything is refused."""
    a = new_id()
    registry = _identify_registry({a}, cannot="The models are not there.")
    off = saying(**{**_ALL_ON, "performance.generate_previews": False})

    started = await _press(registry, job_queue, "identify:all", [a], policy=off)

    assert started.passes == ["faces"] and started.refused == 1
    assert started.said == (
        "Identifying 1 file: faces."
        ' Marks: "Generate hover previews" is switched off for this file.'
        " Meaning: The models are not there."
    )
    # Under way now: a press has nothing to add to work already running (waiting work it would
    # pull forward instead. See `test_a_press_pulls_the_files_own_waiting_work_forward`).
    assert await job_queue.claim("a-worker") is not None

    with pytest.raises(HTTPException) as refused:
        await _press(registry, job_queue, "identify:all", [a], policy=off)
    assert refused.value.status_code == 409
    assert refused.value.detail == (
        "Nothing was queued. Faces: this file is already waiting for it."
        ' Marks: "Generate hover previews" is switched off for this file.'
        " Meaning: The models are not there."
    )


async def test_a_stage_with_nothing_under_it_or_no_such_stage_is_refused(
    job_queue: JobQueue, handlers: None, saying: Any
) -> None:
    for run in ("identify:all", "dedup:all", "all"):
        with pytest.raises(HTTPException) as refused:
            await _press(_press_registry(), job_queue, run, [new_id()], policy=saying(**_ALL_ON))
        assert refused.value.status_code == 422, run


async def test_scan_all_reads_the_file_again(
    job_queue: JobQueue, handlers: None, saying: Any
) -> None:
    a = new_id()
    started = await _press(_press_registry(), job_queue, "scan:all", [a], policy=saying(**_ALL_ON))

    assert started.said == "Scanning 1 file: file details."
    [job] = (await job_queue.list(job_type="preview", limit=10)).jobs
    assert job.payload == {"asset_id": a}
