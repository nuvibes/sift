# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a pass still has ahead of it, the small answers the composition root gives features that may
not ask each other, and a number whose label calls it a limit."""

from __future__ import annotations

# The NAME only, never a connection, for the same reason `wiring/lifespan.py` carries this. `sqlite3.Error`
# is what the settings converger catches, and the test below proves that arm is load-bearing by
# raising one; nothing here opens a database.
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from fastapi import FastAPI

from sift.kernel import attention, media, wiring
from sift.kernel.access import Role, Viewer
from sift.kernel.content import Lack
from sift.kernel.jobs import BACKGROUND_PRIORITY
from sift.kernel.jobs.switchboard import Readiness
from sift.kernel.ledger import Actor
from sift.kernel.tests.test_main_wiring import (
    _CatchUpContent,
    _ChosenShape,
    _Fetches,
    _Hub,
    _Importing,
    _Marks,
    _pool_config,
    _Recognition,
    _Segments,
    _Sets,
    _SettledQueue,
    _storage,
    _Tiles,
)
from sift.slices import (
    download,
    faces,
    importing,
    library_roots,
    media_jobs,
    performance,
    photo_sets,
    suggestions,
)
from sift.slices.faces.service_base import FACES_SWITCHED_OFF
from sift.wiring import (
    catch_up,
    products,
    readiness,
    understanding,
    work_ahead,
    workers,
)

# --- what a pass still has ahead of it ----------------------------------------------------------
#
# The two counters registered for each family's run. Both are here rather than in the importing
# slice because this is the only place that knows both a queue and a product registry exist.


async def test_a_pass_weighs_the_files_the_runs_it_is_HOLDING_still_lack() -> None:
    """The bar does not count what has been QUEUED: the pass queues a page at a time, so it would
    read "246 of 246" from the first page to the last and no estimate of time left would survive.

    The count is the same one the row on Importing shows before the button is pressed, for the
    products of every run of this family the queue is holding, and it falls as tasks land. It is
    read off the QUEUE rather than remembered, so a counter asked after a restart answers for the
    run the queue is still holding.
    """
    asked: list[list[str]] = []

    async def lacking(
        _products: object, keys: list[str], _content: object, *, roots: list[str] | None = None
    ) -> int:
        asked.append(list(keys))
        assert roots is None
        return 7

    class _Queue:
        async def live_payloads(self, job_type: str) -> list[dict[str, object]]:
            assert job_type == "generate"
            # Two runs of this family, one product named twice between them.
            return [
                {"products": ["thumbnails", "fingerprints"]},
                {"products": ["thumbnails", 4, None]},
            ]

    with mock.patch.object(importing, "files_lacking", lacking):
        ahead = await work_ahead._pass_ahead(
            cast(Any, _Queue()), "generate", cast(Any, object()), cast(Any, object())
        )

    assert ahead == 7
    # Each product once, in the order the runs named them, and nothing that is not a product key.
    assert asked == [["thumbnails", "fingerprints"]]


async def test_a_pass_over_some_folders_weighs_only_them_and_one_whole_run_widens_it() -> None:
    """A run over some folders carries them as `roots`; the bar prices only those folders. Two such
    runs price the union of their folders, and one whole run among them prices the library."""
    priced: list[list[str] | None] = []

    async def lacking(
        _products: object, keys: list[str], _content: object, *, roots: list[str] | None = None
    ) -> int:
        priced.append(roots)
        return 3

    class _Queue:
        def __init__(self, payloads: list[dict[str, object]]) -> None:
            self._payloads = payloads

        async def live_payloads(self, job_type: str) -> list[dict[str, object]]:
            return self._payloads

    narrowed: list[dict[str, object]] = [
        {"products": ["thumbnails"], "roots": ["r2", "r1"]},
        {"products": ["thumbnails"], "roots": ["r3", 5]},
    ]
    widened: list[dict[str, object]] = [*narrowed, {"products": ["fingerprints"]}]
    with mock.patch.object(importing, "files_lacking", lacking):
        await work_ahead._pass_ahead(
            cast(Any, _Queue(narrowed)), "generate", cast(Any, object()), cast(Any, object())
        )
        await work_ahead._pass_ahead(
            cast(Any, _Queue(widened)), "generate", cast(Any, object()), cast(Any, object())
        )

    assert priced == [["r1", "r2", "r3"], None]


async def test_a_pass_with_no_run_in_the_queue_weighs_nothing_and_asks_nothing() -> None:
    """Nought without asking the registry at all. `files_lacking` over an empty list of products is
    a question about nothing, and the honest answer is already known."""
    asked = False

    async def lacking(*_args: object) -> int:
        nonlocal asked
        asked = True
        return 99

    class _Empty:
        async def live_payloads(self, _job_type: str) -> list[dict[str, object]]:
            return []

    with mock.patch.object(importing, "files_lacking", lacking):
        ahead = await work_ahead._pass_ahead(
            cast(Any, _Empty()), "generate", cast(Any, object()), cast(Any, object())
        )

    assert ahead == 0
    assert asked is False


async def test_a_queued_PAGE_weighs_nothing_of_its_own() -> None:
    """The task counter beside it carries the files, and a queued page counted as well would be the
    same files twice: a bar that read double and an estimate to match."""
    assert await work_ahead._nothing_ahead() == 0


async def test_generate_left_is_split_by_the_kind_of_every_file_it_is_waiting_on() -> None:
    """The mix Generate is priced by: what its runs still lack, by kind, and one unit for every
    task already queued: a press on chosen files queues tasks and no run, and those files may
    lack nothing because they are being made again."""

    class _Queue:
        async def live_payloads(self, job_type: str) -> list[dict[str, object]]:
            # The runs only: a few rows. Their thousands of tasks are never read as payloads.
            assert job_type == "generate", "every waiting task's payload read to count it"
            # Two runs for one product, and a key that is not a name: asked once, and never.
            return [{"products": ["previews"]}, {"products": ["previews", 7]}]

        async def live_asset_ids(self, job_type: str) -> list[str]:
            assert job_type == "generate_file"
            return ["clip-1", "clip-1", "shot-1", "gone"]

    class _Content:
        async def kinds_of(self, ids: list[str]) -> dict[str, str]:
            known = {"clip-1": "video", "shot-1": "image"}
            return {one: known[one] for one in ids if one in known}

    async def lacking(
        _registry: object, keys: list[str], _content: object, *, roots: list[str] | None = None
    ) -> dict[str, int]:
        assert keys == ["previews"] and roots is None
        return {"video": 10}

    with mock.patch.object(importing, "lacking_by_kind", lacking):
        split = await work_ahead._pass_ahead_by_kind(
            cast(Any, _Queue()),
            "generate",
            "generate_file",
            cast(Any, object()),
            cast(Any, _Content()),
        )

    assert split == {"video": 12, "image": 1}


async def test_a_product_names_how_many_files_want_it() -> None:
    """A product's bar is drawn over the files that WANT it, and a product may say how many.

    The library is the denominator for a picture; it is the wrong one for a fingerprint of the
    sound, which a silent file never wants: counted over every file, the Fingerprint bar would
    read a total near twice the library's size, with no music fingerprint taken at all.
    """
    from sift.kernel.jobs.families import Family
    from sift.slices.importing.products import Product, ProductRegistry

    async def on() -> bool:
        return True

    async def lack() -> object:
        return object()

    async def lacking_among(_ids: object) -> set[str]:
        return set()

    async def make(_context: object) -> None:
        return None

    async def seven(_within: object = None) -> int:
        return 7

    async def eleven() -> str:
        return "23:00"

    products = ProductRegistry(night_start=eleven)
    products.register(
        Product(
            family=Family.FINGERPRINT,
            key="sound",
            label="Sound",
            help="",
            switched_on=on,
            lack=cast(Any, lack),
            lacking_among=lacking_among,
            build=make,
            wants=seven,
        )
    )
    products.register(
        Product(
            family=Family.GENERATE,
            key="pictures",
            label="Pictures",
            help="",
            switched_on=on,
            lack=cast(Any, lack),
            lacking_among=lacking_among,
            build=make,
        )
    )

    class Content:
        async def asset_count(self, _within: object = None) -> int:
            return 100

    content = cast(Any, Content())
    assert await work_ahead._product_wanted(products, content, "sound") == 7
    assert await work_ahead._product_wanted(products, content, "pictures") == 100
    # A product this build does not have wants nothing.
    assert await work_ahead._product_wanted(products, content, "gone") == 0


async def test_a_set_under_the_floor_queues_the_dissolving_pass_in_the_background(
    tmp_path: Path,
) -> None:
    """A library holding a set Sift made under the floor asks for the pass once, as background
    work, and a library at the floor asks for nothing."""
    queue = _SettledQueue()

    await catch_up.catch_up(
        _CatchUpContent(),  # type: ignore[arg-type]
        queue,  # type: ignore[arg-type]
        _Marks(),  # type: ignore[arg-type]
        _Fetches(),  # type: ignore[arg-type]
        _ChosenShape(),  # type: ignore[arg-type]
        tmp_path,
        _Recognition(),  # type: ignore[arg-type]
        _Segments(),  # type: ignore[arg-type]
        _Sets(short=3),  # type: ignore[arg-type]
        _Tiles(),  # type: ignore[arg-type]
    )

    assert queue.asked == [photo_sets.DISSOLVE_UNDER_FLOOR]
    assert queue.priorities == [BACKGROUND_PRIORITY]


class _Registry:
    """The product registry as `_product_left` reads it: one product, and no folder refusing it."""

    def __init__(self, product: Any) -> None:
        self.product = product

    def get(self, key: str) -> Any:
        return self.product if key == self.product.key else None

    async def within(self, _product: Any) -> None:
        return None


class _Lacking:
    """The content store's count of what is lacking: three files that have been read."""

    async def count_lacking(
        self, lacks: list[Any], *, ticked: list[bool], roots: list[str] | None = None
    ) -> Any:
        return SimpleNamespace(each=[3] * len(lacks), files=3 if lacks else 0)


async def test_a_products_count_on_activity_takes_in_the_files_still_to_be_read() -> None:
    """A count of what is lacking that read only files already read would count a first import's
    every unread file as done, a thumbnail count near the whole library while few had one.
    The files still to be read lack it as surely, and Activity counts them."""

    async def lack() -> Lack:
        return Lack("(1 = 1)")

    async def five(_within: Any) -> int:
        return 5

    coming = SimpleNamespace(key="thumbnails", lack=lack, coming=five)
    read_only = SimpleNamespace(key="thumbnails", lack=lack, coming=None)

    assert await work_ahead._product_left(_Registry(coming), _Lacking(), "thumbnails") == 8  # type: ignore[arg-type]
    assert await work_ahead._product_left(_Registry(read_only), _Lacking(), "thumbnails") == 3  # type: ignore[arg-type]


# --- the small answers main gives the features that may not ask each other --------------------


async def test_the_music_card_counts_what_this_user_can_see() -> None:
    class _Music:
        async def waiting_for(self, user_id: str) -> int:
            assert user_id == "u1"
            return 3

    viewer = Viewer(id="u1", role=Role.ADMIN)
    assert await products._music_waiting(cast(Any, _Music()), viewer) == 3


async def test_a_run_task_press_is_refused_with_the_features_own_sentence() -> None:
    """Smart Search and Watermarks return quietly while not ready, so a task queued then would
    finish having done nothing: the press is refused with the reason instead."""

    class _Meaning:
        def __init__(self, ready: bool, problem: str | None) -> None:
            self.state = SimpleNamespace(ready=ready, problem=problem)

        async def readiness(self) -> SimpleNamespace:
            return self.state

    assert await readiness.meaning_cannot_run(cast(Any, _Meaning(True, None))) is None
    assert (
        await readiness.meaning_cannot_run(cast(Any, _Meaning(False, None)))
        == readiness.SMART_SEARCH_OFF
    )
    assert (
        await readiness.meaning_cannot_run(cast(Any, _Meaning(False, "No model."))) == "No model."
    )
    assert await readiness.meaning_can_run(cast(Any, _Meaning(True, None))) == Readiness(ready=True)
    assert await readiness.meaning_can_run(cast(Any, _Meaning(False, None))) == Readiness(
        ready=False, problem=readiness.SMART_SEARCH_OFF
    )

    class _Marks:
        def __init__(self, ready: bool, problem: str | None) -> None:
            self.answer = (ready, problem)

        async def ready(self) -> tuple[bool, str | None]:
            return self.answer

    assert await readiness.marks_cannot_run(cast(Any, _Marks(True, None))) is None
    assert await readiness.marks_cannot_run(cast(Any, _Marks(False, "No models."))) == "No models."
    assert (
        await readiness.marks_cannot_run(cast(Any, _Marks(False, None)))
        == "Reading watermarks is switched off."
    )


async def test_recognition_names_the_first_thing_standing_in_its_way() -> None:
    class _Faces:
        def __init__(self, *, on: bool, device: str | None, ready: bool) -> None:
            self.on, self.device, self.models = on, device, ready

        async def enabled(self) -> bool:
            return self.on

        async def device_problem(self) -> str | None:
            return self.device

        async def ready(self) -> bool:
            return self.models

    async def asked(**state: Any) -> Readiness:
        return await readiness.recognition_can_run(cast(Any, _Faces(**state)))

    assert await asked(on=False, device="No card.", ready=False) == Readiness(
        ready=False, problem=FACES_SWITCHED_OFF
    )
    assert await asked(on=True, device="No card.", ready=False) == Readiness(
        ready=False, problem="No card."
    )
    assert await asked(on=True, device=None, ready=False) == Readiness(
        ready=False, problem=faces.MODELS_NOT_INSTALLED
    )
    assert await asked(on=True, device=None, ready=True) == Readiness(ready=True)


async def test_a_set_made_across_the_seam_is_made_one_way_and_undone_as_its_presser() -> None:
    """Both makers go through the photo-set slice's own derivation, and an Undo deletes the set as
    the person who pressed it, never as Sift."""

    class _Sets:
        def __init__(self) -> None:
            self.deleted: list[tuple[str, Actor]] = []

        async def delete(self, photo_set_id: str, *, actor: Actor) -> None:
            self.deleted.append((photo_set_id, actor))

    app = FastAPI()
    service = _Sets()
    wiring.provide(app, photo_sets.SERVICE, cast(Any, service))
    wiring.provide(app, wiring.CONTENT, cast(Any, "the content store"))
    asked: list[tuple[str, list[str], str, object, object]] = []

    async def from_shoot(ids: list[str], *, name: str, content: object, service: object) -> str:
        asked.append(("shoot", list(ids), name, content, service))
        return "set-1"

    async def from_post(
        ids: list[str], *, name: str, content: object, service: object
    ) -> SimpleNamespace | None:
        asked.append(("post", list(ids), name, content, service))
        return SimpleNamespace(id="set-2", name=f"{name} 2") if len(ids) > 1 else None

    maker = understanding._photo_set_maker(app)
    posts = understanding._post_sets(app, _storage(content="the content store"))
    with (
        mock.patch.object(photo_sets, "set_from_shoot", from_shoot),
        mock.patch.object(photo_sets, "set_from_post", from_post),
    ):
        assert await maker.make(["a", "b", "c"], name="A shoot") == "set-1"
        assert await posts.derive(["a", "b"], "A post") == suggestions.MadeSet("set-2", "A post 2")
        assert await posts.derive(["a"], "Too short") is None
    assert asked[0] == ("shoot", ["a", "b", "c"], "A shoot", "the content store", service)
    assert asked[1] == ("post", ["a", "b"], "A post", "the content store", service)

    presser = Viewer(id="u1", role=Role.ADMIN)
    await maker.forget("set-1", by=presser)
    await posts.forget("set-2", presser)
    assert service.deleted == [("set-1", Actor.user("u1")), ("set-2", Actor.user("u1"))]


async def test_a_photo_set_a_grouping_would_make_is_asked_after_by_the_sets_it_already_holds() -> (
    None
):
    """A shoot proposed twice is one set: the maker asks Photo Sets which set holds these files."""

    class _Sets:
        async def holding(self, asset_ids: list[str]) -> str | None:
            return "set-1" if asset_ids == ["a", "b"] else None

    app = FastAPI()
    wiring.provide(app, photo_sets.SERVICE, cast(Any, _Sets()))
    maker = understanding._photo_set_maker(app)

    assert await maker.holding(["a", "b"]) == "set-1"
    assert await maker.holding(["c"]) is None


async def test_the_folder_pass_reads_the_folders_a_swap_made_through_the_seam() -> None:
    """Two sets of folder ids cross, so the folder reader never imports the swap's rows."""
    database = object()
    asked: list[object] = []

    async def folders_made(read_from: object) -> object:
        asked.append(read_from)
        return SimpleNamespace(containers={"f1"}, people={"f2"})

    store = cast(Any, SimpleNamespace(database=database))
    with mock.patch.object(understanding, "folders_made", folders_made):
        arrivals = await understanding._swap_folders(store).made()

    assert asked == [database]
    assert arrivals == suggestions.Arrivals(containers=frozenset({"f1"}), by_name=frozenset({"f2"}))


async def test_a_picture_that_cannot_be_opened_has_both_fields_empty(tmp_path: Path) -> None:
    settings = cast(Any, object())
    fields = understanding._picture_fields(_storage(content="the content store"), settings)

    async def nowhere(_content: object, _asset_id: str, *, settings: object) -> object:
        raise media.NoReadableCopy("offline")

    async def here(_content: object, asset_id: str, *, settings: object) -> object:
        return SimpleNamespace(path=tmp_path / f"{asset_id}.jpg")

    async def read(path: Path, *, settings: object) -> suggestions.PictureMetadata:
        return suggestions.PictureMetadata(description=path.name)

    with mock.patch.object(suggestions, "read_picture_fields", read):
        with mock.patch.object(media, "resolve_decodable", nowhere):
            assert await fields.read("a1") == suggestions.PictureMetadata()
        with mock.patch.object(media, "resolve_decodable", here):
            assert await fields.read("a1") == suggestions.PictureMetadata(description="a1.jpg")


def test_a_product_nothing_arrives_as_has_no_maker() -> None:
    made_by = SimpleNamespace(key="thumbnails", governed_by="generate_file")
    pressed_only = SimpleNamespace(key="music", governed_by=None)
    assert workers._makers_of(cast(Any, [made_by, pressed_only])) == {
        "generate_file": ("thumbnails",)
    }


async def test_a_products_mix_by_kind_is_the_lacking_count_for_that_product_alone() -> None:
    async def lacking(
        _registry: object, keys: list[str], _content: object, *, roots: list[str] | None = None
    ) -> dict[str, int]:
        assert keys == ["previews"] and roots is None
        return {"video": 4}

    with mock.patch.object(importing, "lacking_by_kind", lacking):
        split = await work_ahead._product_left_by_kind(
            cast(Any, object()), cast(Any, object()), "previews"
        )
    assert split == {"video": 4}


# --- a number whose label calls it a limit is one ----------------------------------------------


async def test_a_typed_scan_limit_is_a_limit_and_never_topped_up_by_an_idle_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typed "Folder scans at once" is never more than itself. Alone on the machine, automatic
    takes every worker; a typed number takes exactly what it says."""
    busy = _Importing({library_roots.SCAN: 50})
    automatic, _ = await _pool_config(monkeypatch, _Hub(), queue=busy)
    typed, _ = await _pool_config(monkeypatch, _Hub(**{performance.SCAN_LIMIT_KEY: 1}), queue=busy)

    workers = automatic["initial_concurrency"]
    assert workers > 1, "a machine this small could not show a top-up"
    assert automatic["initial_limits"][library_roots.SCAN] == workers
    assert typed["initial_limits"][library_roots.SCAN] == 1


async def test_a_thread_count_for_recognition_is_a_limit_on_both_of_its_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A number of threads typed for faces holds the scan's recognition and the pressed run alike,
    however idle the rest of the machine is."""
    hub = _Hub(**{faces.ENABLED_KEY: True, faces.BUDGET_KEY: "threads", faces.THREAD_COUNT_KEY: 1})
    alone, _ = await _pool_config(
        monkeypatch, hub, queue=_Importing({faces.FACE_SCAN: 50, importing.IDENTIFY_FILE: 50})
    )

    assert alone["initial_concurrency"] > 1
    assert alone["initial_limits"][faces.FACE_SCAN] == 1
    assert alone["initial_limits"][importing.IDENTIFY_FILE] == 1


# --- each number on the screen reaches the pool it names ----------------------------------------


async def test_the_typed_worker_count_is_how_many_workers_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Jobs at the same time` on Settings > Importing is the pool's size, not a hint to it."""
    monkeypatch.setattr(attention.ATTENTION, "_since_input", lambda: None)
    three, _ = await _pool_config(monkeypatch, _Hub(**{performance.WORKER_COUNT_KEY: 3}))
    five, _ = await _pool_config(monkeypatch, _Hub(**{performance.WORKER_COUNT_KEY: 5}))

    assert (three["initial_concurrency"], five["initial_concurrency"]) == (3, 5)


@pytest.mark.parametrize("stepping_back", [True, False])
async def test_the_step_back_switch_decides_whether_a_person_at_the_computer_shrinks_the_pool(
    monkeypatch: pytest.MonkeyPatch, stepping_back: bool
) -> None:
    """Somebody touched the computer a second ago. On, the pool gives way to them; off, it keeps the
    full count whatever they are doing."""
    monkeypatch.setattr(attention.ATTENTION, "_since_input", lambda: 1.0)
    hub = _Hub(**{performance.WORKER_COUNT_KEY: 8, performance.STEP_BACK_KEY: stepping_back})
    config, _ = await _pool_config(monkeypatch, hub)

    assert (config["initial_concurrency"] < 8) is stepping_back


async def test_the_typed_preview_limit_caps_previews_and_strips_alike(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(attention.ATTENTION, "_since_input", lambda: None)
    hub = _Hub(**{performance.WORKER_COUNT_KEY: 8, performance.GENERATION_LIMIT_KEY: 2})
    config, _ = await _pool_config(monkeypatch, hub)

    assert config["initial_limits"][media_jobs.PREVIEW] == 2
    assert config["initial_limits"][media_jobs.SPRITE] == 2


async def test_the_share_recognition_may_use_sizes_its_scans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Share of this device to use` under the Share budget, while folder scans and probing want the
    machine too: an idle machine gives any share all of it, so only competition shows the share."""
    monkeypatch.setattr(attention.ATTENTION, "_since_input", lambda: None)
    queue = _Importing({faces.FACE_SCAN: 50, library_roots.SCAN: 50, media_jobs.PROBE: 50})

    async def scans(share: int) -> int:
        hub = _Hub(
            **{
                performance.WORKER_COUNT_KEY: 8,
                faces.ENABLED_KEY: True,
                faces.BUDGET_KEY: "share",
                faces.CORE_SHARE_KEY: share,
            }
        )
        config, _ = await _pool_config(monkeypatch, hub, queue=queue)
        return int(config["initial_limits"][faces.FACE_SCAN])

    small, whole = await scans(10), await scans(100)
    assert small < whole


@pytest.mark.parametrize(("paused", "expected"), [(True, 0), (False, 2)])
async def test_a_paused_download_queue_starts_nothing(
    monkeypatch: pytest.MonkeyPatch, paused: bool, expected: int
) -> None:
    """The pause on the Downloads page is a limit of none on the pool, over a typed number."""
    hub = _Hub(**{download.AT_ONCE_KEY: 2, download.PAUSED_KEY: paused})
    config, _ = await _pool_config(monkeypatch, hub)

    assert config["initial_limits"][download.DOWNLOAD] == expected


async def test_the_first_benchmark_reads_the_application_only_when_it_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Built before the reactions and the folder count exist, so each is read when used: a
    settings change told only once there is somebody to tell, the folders counted as they stand,
    and the folder's first scan queued for whoever added it, or not at all while scans are off."""
    from sift.kernel.jobs import JobSwitchedOff, worker_pool
    from sift.slices import library_roots, performance
    from sift.wiring import machine

    app = FastAPI()
    wiring.provide(app, performance.SELF_TEST_RUNNER, cast(Any, object()))

    async def roots() -> list[str]:
        return ["r1", "r2"]

    class _Queue:
        def __init__(self) -> None:
            self.enqueued: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
            self.settled: list[Any] = []
            self.off = False

        def listen_for_settled(self, _job_type: str, listener: Any) -> None:
            self.settled.append(listener)

        async def enqueue(self, job_type: str, payload: dict[str, Any], **kwargs: Any) -> str:
            if self.off:
                raise JobSwitchedOff("scans are off")
            self.enqueued.append((job_type, payload, kwargs))
            return "job-1"

    class _Workbench:
        def register_reverser(self, _reverser: object) -> None:
            return None

    queue = _Queue()
    store = cast(Any, SimpleNamespace(library=SimpleNamespace(roots=roots)))
    machine.build_benchmark(
        app, store, cast(Any, queue), cast(Any, object()), cast(Any, _Workbench())
    )

    first_folder = wiring.part_of_app(app, wiring.ON_FOLDER_ADDED)
    assert await first_folder._roots() == 2  # type: ignore[attr-defined]

    [then_scan] = queue.settled
    await then_scan._scan("r1", "u1")
    queue.off = True
    await then_scan._scan("r2", None)
    assert queue.enqueued == [
        (library_roots.SCAN, {"root_id": "r1"}, {"dedupe": True, "requested_by": "u1"})
    ]

    handed: dict[str, Any] = {}

    async def run_benchmark(_context: object, **kwargs: Any) -> None:
        handed.update(kwargs)

    monkeypatch.setattr(performance, "run_benchmark", run_benchmark)
    await worker_pool._HANDLERS[performance.BENCHMARK](cast(Any, object()))
    told: list[set[str]] = []
    await handed["notify"]({"pool.size"})
    assert told == [], "told before there was anybody to tell"

    async def react(changed: set[str]) -> None:
        told.append(changed)

    wiring.provide(app, wiring.ON_SETTINGS_CHANGED, react)
    await handed["notify"]({"pool.size"})
    assert told == [{"pool.size"}]
