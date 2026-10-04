# SPDX-License-Identifier: AGPL-3.0-or-later
"""Activity's Library tasks describe a run somebody pressed over some files by those files.

Run task over forty files, with nothing else going, must not read the library's figures under
Identify's caret or price the library's owed files as the time left. These hold the row to the
presses' own figures while they are all the family is doing, to the library's figures plus the
presses' remade work beside a pass over the library, and to the library's alone otherwise.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.kernel.jobs import register_handler
from sift.kernel.jobs.families import PRODUCT_FAMILIES, PRODUCT_TYPES, Family
from sift.kernel.jobs.switchboard import Switchboard
from sift.kernel.jobs.worker_pool import WorkerPool, registered_families
from sift.main import create_app
from sift.slices.media_jobs.presses import Presses
from sift.slices.media_jobs.router import (
    FamilyOfWork,
    KindOfWork,
    _families,
    not_before_the_read,
    pictured_in_the_read,
)
from sift.testing.auth import establish_session
from sift.testing.library import seed_asset, seed_root, write_rows

ROOT_ID = "01HX0000000000000000000R01"
FOLDER_ID = "01HX0000000000000000000F01"
#: Three files whose ids differ before the last character, which their locations' ids drop.
FILES = [f"01HX000000000000000000{n}A01" for n in range(1, 4)]

#: A pass over the library's page: a top about no file, which nobody pressed file by file.
_PAGE = (
    "INSERT INTO jobs (id, parent_id, type, state, payload, created_at, updated_at, root_id,"
    " requested_by) VALUES (?, NULL, 'generate', 'blocked', ?, 1, 1, ?, NULL)"
)
_ONE_DONE = "UPDATE jobs SET state = 'done' WHERE id = (SELECT MIN(id) FROM jobs WHERE type = ?)"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))

    # No workers: what is pressed stays queued, and the figures read are the press's own.
    async def _no_workers(self: WorkerPool) -> None: ...

    monkeypatch.setattr(WorkerPool, "start", _no_workers)
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _admin_with_files(client: TestClient, tmp_path: Path) -> Path:
    db_path: Path = client.app.state.database.path  # type: ignore[attr-defined]
    _user, token, csrf = establish_session(
        db_path, role="admin", username="presses-admin", password="Presses-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    root = tmp_path / "library"
    root.mkdir(parents=True, exist_ok=True)
    seed_root(db_path, ROOT_ID, folder_id=FOLDER_ID, path=root)
    for n, asset_id in enumerate(FILES):
        seed_asset(
            db_path,
            asset_id,
            root_id=ROOT_ID,
            folder_id=FOLDER_ID,
            root_path=root,
            cache_dir=tmp_path / "cache",
            filename=f"clip-{n}.mp4",
        )
    return db_path


def _generate(client: TestClient) -> dict[str, object]:
    page = client.get("/api/jobs", params={"fold": True}).json()
    family: dict[str, object] = page["families"]["generate"]
    return family


def _lines(family: dict[str, object]) -> dict[str, tuple[int, int]]:
    parts = family["parts"]
    assert isinstance(parts, list)
    return {part["type"]: (part["done"], part["total"]) for part in parts}


@pytest.mark.integration
def test_a_run_pressed_over_some_files_is_drawn_by_those_files_and_beside_a_pass_as_the_sum(
    client: TestClient, tmp_path: Path
) -> None:
    """Thumbnails pressed for three files: Generate reads "0 of 3" on the thumbnails line and has
    three left, not the library's figures; one lands and it reads "1 of 3". A pass over the
    library starts beside it, and the row is the library's again, with the press's remade
    thumbnails added to their line and to what is left: one sum that is the sum."""
    db_path = _admin_with_files(client, tmp_path)
    library = _generate(client)

    pressed = client.post("/api/assets/run", json={"run": "thumbnails", "asset_ids": FILES})
    assert pressed.status_code == 200, pressed.text
    assert pressed.json()["queued"] == 3

    alone = _generate(client)
    assert _lines(alone) == {"thumbnail": (0, 3)}, "the press's own line, and nothing else"
    assert (alone["done"], alone["total"], alone["waiting"]) == (0, 3, 3)
    assert alone["outstanding"] == 3

    write_rows(db_path, [(_ONE_DONE, ("generate_file",))])
    landed = _generate(client)
    assert _lines(landed) == {"thumbnail": (1, 3)}
    assert (landed["done"], landed["total"], landed["waiting"]) == (1, 3, 2)

    page_id = "01HX0000000000000000000P01"
    payload = json.dumps({"products": ["previews"], "files": 3})
    write_rows(db_path, [(_PAGE, (page_id, payload, page_id))])
    beside = _generate(client)
    before = _lines(library)
    assert set(_lines(beside)) == set(before), "the library's lines"
    done, total = before["thumbnail"]
    assert _lines(beside)["thumbnail"] == (done + 1, total + 3), "with the remade ones added"
    assert beside["waiting"] == int(str(library["waiting"])) + 2
    assert (beside["done"], beside["total"]) == (
        int(str(library["done"])) + 1,
        int(str(library["total"])) + 3,
    )


async def _nothing(_context: object) -> None:
    return None


class _Book:
    """A ledger that answers nothing and writes down what each family was priced from."""

    def __init__(self) -> None:
        self.left: dict[str, float] = {}

    async def estimate(self, family: Family, *_args: object, **named: object) -> None:
        left = named.get("left")
        assert isinstance(left, float | int)
        self.left[family.value] = float(left)


async def test_a_press_alone_is_priced_from_its_own_rows_never_the_librarys_owed_files() -> None:
    """Identify's library owes 2,500 files and nothing of it is queued; forty files are pressed
    for both of its passes. The time left is priced from the eighty pressed rows still going, and
    the lines under the caret are the press's: 3 of 40 and 1 of 40."""
    register_handler("peering", _nothing, name="Peering", family=Family.IDENTIFY)
    register_handler("marking", _nothing, name="Marking", family=Family.IDENTIFY)
    work = {
        "peering": KindOfWork(done=98500, outstanding=0, failed=0, waiting=1500, total=100000),
        "marking": KindOfWork(done=99000, outstanding=0, failed=0, waiting=1000, total=100000),
    }
    press = Presses(live=76, parts={"peering": [3, 40], "marking": [1, 40]})
    book = _Book()

    families = await _families(
        work,
        book,  # type: ignore[arg-type]
        Switchboard(),
        presses={Family.IDENTIFY: press},
    )

    identify = families["identify"]
    assert book.left["identify"] == 76.0, "the press's own rows, not 2,500 owed files"
    assert [(part.type, part.done, part.total) for part in identify.parts] == [
        ("peering", 3, 40),
        ("marking", 1, 40),
    ]
    assert (identify.done, identify.total, identify.waiting) == (4, 80, 76)


async def test_beside_a_pass_over_the_library_the_row_is_the_library_plus_what_is_remade() -> None:
    """A pass over the library is going, and so are forty files pressed again: the library's
    lines stand, with the remade work added, and what is left is the library's owed files and
    the remade rows still going, never the pass's own handed-out tasks a second time."""
    register_handler("peering", _nothing, name="Peering", family=Family.IDENTIFY)
    work = {
        "peering": KindOfWork(done=98500, outstanding=40, failed=0, waiting=1500, total=100000),
    }
    press = Presses(
        live=40,
        again_live=30,
        parts={"peering": [10, 40]},
        again_parts={"peering": [10, 40]},
        library=200,
    )
    book = _Book()

    identify = (
        await _families(work, book, Switchboard(), presses={Family.IDENTIFY: press})  # type: ignore[arg-type]
    )["identify"]

    assert book.left["identify"] == 1500 + 30
    assert [(part.type, part.done, part.total) for part in identify.parts] == [
        ("peering", 98510, 100040)
    ]
    assert (identify.done, identify.total) == (98510, 100040)

    nobody = Presses(library=200)
    at_rest = (
        await _families(work, _Book(), Switchboard(), presses={Family.IDENTIFY: nobody})  # type: ignore[arg-type]
    )["identify"]
    assert (at_rest.done, at_rest.total, at_rest.waiting) == (98500, 100000, 1500)


def test_a_read_somebody_pressed_holds_back_no_pass_after_it() -> None:
    """Scan pressed for some files hands Generate nothing it waits on: Generate keeps its own
    words and its own time left, which a read of the library's arrivals would raise."""

    def family(**over: object) -> FamilyOfWork:
        base: dict[str, object] = {"label": "x", "types": [], "task": None}
        return FamilyOfWork.model_validate({**base, **over})

    def answer() -> dict[str, FamilyOfWork]:
        return {
            "scan": family(outstanding=40, waiting=40, quick_seconds=600, slow_seconds=900),
            "generate": family(outstanding=0, waiting=5, quick_seconds=1, slow_seconds=2),
        }

    held = not_before_the_read(pictured_in_the_read(answer(), {"scan"}), {"scan"})
    assert held["generate"].outstanding == 0
    assert (held["generate"].quick_seconds, held["generate"].slow_seconds) == (1, 2)
    library = not_before_the_read(pictured_in_the_read(answer()))
    assert library["generate"].outstanding == 40, "a read of the library still is Generate's"
    assert library["generate"].quick_seconds == 600


def test_every_product_is_drawn_on_a_line_of_its_own_family(client: TestClient) -> None:
    """The map that puts a coordinator's task on its product's line names a type registered in
    the product's own family, for every product a family is declared for: read from a booted
    application, whose handlers are registered."""
    families = registered_families()
    assert set(PRODUCT_TYPES) == set(PRODUCT_FAMILIES)
    for key, job_type in PRODUCT_TYPES.items():
        assert families.get(job_type) is PRODUCT_FAMILIES[key], key


async def test_beside_a_pass_waiting_for_quiet_hours_the_row_is_the_press_alone() -> None:
    """Generate's pictures for new files wait for quiet hours and forty files are pressed now:
    what runs now is the press, so the row is its figures and its time left; the waiting pass is
    the library's row again once the press is over or the range opens."""
    register_handler("peering", _nothing, name="Peering", family=Family.IDENTIFY)
    work = {
        # Eighty of the pass's rows held, and the forty pressed ones going.
        "peering": KindOfWork(done=98500, outstanding=120, failed=0, waiting=1500, total=100000),
    }

    def press() -> Presses:
        return Presses(live=40, again_live=40, parts={"peering": [5, 40]}, library=80)

    book = _Book()
    waiting = (
        await _families(
            work,
            book,  # type: ignore[arg-type]
            Switchboard(),
            held={"peering": 80},
            presses={Family.IDENTIFY: press()},
        )
    )["identify"]
    assert book.left["identify"] == 40.0
    assert (waiting.done, waiting.total, waiting.reason) == (5, 40, None)

    running = (
        await _families(
            work,
            _Book(),  # type: ignore[arg-type]
            Switchboard(),
            held={"peering": 79},
            presses={Family.IDENTIFY: press()},
        )
    )["identify"]
    assert running.total == 100000, "one of the pass's rows runs: the library's row"


#: A row of work, with who pressed it: a top unless a parent is named.
_ROW = (
    "INSERT INTO jobs (id, parent_id, type, state, payload, created_at, updated_at, root_id,"
    " requested_by) VALUES (?, NULL, ?, ?, ?, 1, 1, ?, ?)"
)


@pytest.mark.integration
def test_a_press_beside_the_librarys_own_work_and_a_chore_reads_each_where_it_belongs(
    client: TestClient, tmp_path: Path
) -> None:
    """Faces and meaning pressed for a file, a face scan of an arriving file, and faces grouped
    again after it. The arriving file's scan is the library's work, so Identify is the library's
    row; the meaning task is Smart Search's and not one of Identify's lines; the regrouping is a
    chore nothing counts, which makes nobody's row the library's."""
    db_path = _admin_with_files(client, tmp_path)
    before = client.get("/api/jobs", params={"fold": True}).json()["families"]

    def row(
        job_id: str, job_type: str, state: str, payload: dict[str, object], by: str | None
    ) -> tuple[str, tuple[object, ...]]:
        return (_ROW, (job_id, job_type, state, json.dumps(payload), job_id, by))

    pressed = {"asset_id": FILES[0], "again": True}
    write_rows(
        db_path,
        [
            row(
                "01HX0000000000000000000J01",
                "identify_file",
                "blocked",
                {**pressed, "products": ["faces"]},
                "presser",
            ),
            row(
                "01HX0000000000000000000J02",
                "identify_file",
                "done",
                {**pressed, "products": ["meaning"]},
                "presser",
            ),
            row("01HX0000000000000000000J03", "face_regroup", "blocked", {}, None),
        ],
    )
    chore_only = client.get("/api/jobs", params={"fold": True}).json()["families"]
    assert [(p["type"], p["done"], p["total"]) for p in chore_only["identify"]["parts"]] == [
        ("face_scan", 0, 1)
    ], "the press alone, its meaning task left to Smart Search, the chore no library pass"

    write_rows(
        db_path,
        [row("01HX0000000000000000000J04", "face_scan", "blocked", {"asset_id": FILES[1]}, None)],
    )
    beside = client.get("/api/jobs", params={"fold": True}).json()["families"]["identify"]
    library = {p["type"]: (p["done"], p["total"]) for p in before["identify"]["parts"]}
    done, total = library["face_scan"]
    assert {p["type"]: (p["done"], p["total"]) for p in beside["parts"]}["face_scan"] == (
        done,
        total + 1,
    ), "an arriving file's scan is the library's: its line, with the remade one added"


async def test_a_run_over_one_folder_alone_is_drawn_by_that_folders_files() -> None:
    """A task's Run now over one folder whose files lacked faces seventy times when it was
    pressed, ten made since: the line reads 10 of 70 and the time left is priced on the 60, not
    on the library's owed files."""
    register_handler("peering", _nothing, name="Peering", family=Family.IDENTIFY)
    work = {
        "peering": KindOfWork(done=98500, outstanding=70, failed=0, waiting=1500, total=100000),
    }
    run = Presses(folders=70, folders_done={"face_scan": 10}, folders_total={"face_scan": 70})
    book = _Book()

    identify = (
        await _families(
            work,
            book,  # type: ignore[arg-type]
            Switchboard(),
            presses={Family.IDENTIFY: run},
        )
    )["identify"]

    assert book.left["identify"] == 60.0
    assert [(part.type, part.done, part.total) for part in identify.parts] == [
        ("face_scan", 10, 70)
    ]
    assert (identify.done, identify.total, identify.waiting) == (10, 70, 60)

    beside = (
        await _families(
            work,
            _Book(),  # type: ignore[arg-type]
            Switchboard(),
            presses={
                Family.IDENTIFY: Presses(folders=70, folders_total={"face_scan": 70}, library=200)
            },
        )
    )["identify"]
    assert (beside.done, beside.total, beside.waiting) == (98500, 100000, 1500), (
        "beside a running pass over the library its files are the library's owed ones"
    )


#: A second library folder, and its two files.
OTHER_ROOT = "01HX0000000000000000000R02"
OTHER_FOLDER = "01HX0000000000000000000F02"
OTHER_FILES = [f"01HX000000000000000000{n}B01" for n in range(1, 3)]


@pytest.mark.integration
def test_a_tasks_run_over_one_folder_is_drawn_by_the_count_its_dry_run_states(
    client: TestClient, tmp_path: Path
) -> None:
    """Generate's Run now with one folder ticked: the thumbnails line is what the run has made of
    what that folder's files lacked, and what is left is the count a dry run over the folder
    states (`count_lacking` with its roots), read once by the work ahead: one source for both."""
    from sift.kernel import wiring
    from sift.slices.importing import PRODUCTS
    from sift.slices.importing.products import count_lacking

    db_path = _admin_with_files(client, tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    seed_root(db_path, OTHER_ROOT, folder_id=OTHER_FOLDER, path=other)
    for n, asset_id in enumerate(OTHER_FILES):
        seed_asset(
            db_path,
            asset_id,
            root_id=OTHER_ROOT,
            folder_id=OTHER_FOLDER,
            root_path=other,
            cache_dir=tmp_path / "cache",
            filename=f"other-{n}.mp4",
        )
    # Read, so a count of what they lack can see them.
    write_rows(
        db_path,
        [("UPDATE assets SET probed_at = 1 WHERE id = ?", (one,)) for one in OTHER_FILES],
    )
    import asyncio

    stated = asyncio.run(
        count_lacking(
            wiring.part_of_app(client.app, PRODUCTS),  # type: ignore[arg-type]
            wiring.part_of_app(client.app, wiring.CONTENT),  # type: ignore[arg-type]
            ["thumbnails"],
            ["thumbnails"],
            roots=[OTHER_ROOT],
        )
    ).each["thumbnails"]
    assert stated == len(OTHER_FILES)

    started = client.post("/api/tasks/generate/run", json={"at": "now", "locations": [OTHER_ROOT]})
    assert started.status_code == 200, started.text
    (page_id,) = started.json()["job_ids"]
    assert _lines(_generate(client))["thumbnail"] == (0, stated), "what the dry run states"
    task_id = new_id()  # minted after its page, as a task the page hands out is
    made = json.dumps({"asset_id": OTHER_FILES[0], "products": ["thumbnails"]})
    write_rows(
        db_path,
        [
            (
                "INSERT INTO jobs (id, parent_id, type, state, payload, created_at, updated_at,"
                " root_id) VALUES (?, ?, 'generate_file', 'done', ?, 1, 1, ?)",
                (task_id, page_id, made, page_id),
            ),
        ],
    )
    folder = _generate(client)
    assert _lines(folder)["thumbnail"] == (1, stated)
    assert folder["waiting"] == sum(total - done for done, total in _lines(folder).values())

    # A run asked for before runs carried their counts is drawn as the library's work.
    older_id = "01HX0000000000000000000P03"
    older = json.dumps({"products": ["thumbnails"], "files": 2, "roots": [OTHER_ROOT]})
    write_rows(
        db_path,
        [
            ("UPDATE jobs SET state = 'canceled' WHERE root_id = ?", (page_id,)),
            (_PAGE, (older_id, older, older_id)),
        ],
    )
    assert set(_lines(_generate(client))) == {"preview", "thumbnail", "sprite"}


def test_a_task_making_two_products_is_drawn_on_both_of_their_lines() -> None:
    """A run's task for one file makes its preview and its strip on one read: it is work of both
    of Generate's lines, never of only the last one named."""
    from sift.kernel.jobs import JobState
    from sift.kernel.jobs.queue import PressedWork
    from sift.slices.media_jobs.presses import families_of

    row = PressedWork("generate_file", JobState.DONE, ("previews", "sprites"), False, 3, True)

    assert families_of(row, {"generate_file"}) == {Family.GENERATE: ["preview", "sprite"]}


async def test_a_run_over_folders_reads_only_what_it_carries_and_without_it_is_the_librarys() -> (
    None
):
    """The counts a run over some folders carries are read for the products a family draws,
    and a product nothing maps, or a count that is not one, is passed over. A run that carries
    none, asked for before runs carried them, is the library's work, and what it made is not
    drawn as a run of its own."""
    from sift.kernel.jobs import JobState
    from sift.kernel.jobs.queue import LiveWork, PressedWork
    from sift.slices.media_jobs.presses import read_presses

    register_handler(
        "drawing_task", _nothing, name="Drawing", family=Family.GENERATE, carries_products=True
    )
    line = LiveWork("drawing_task", False, ("thumbnails", "no-such"), False, 2, 10, folders=True)
    made = PressedWork("drawing_task", JobState.DONE, ("thumbnails",), False, 1, folders=True)

    class Queue:
        def __init__(self, tops: list[dict[str, object]]) -> None:
            self.tops = tops

        async def live_by_press(self, _types: list[str]) -> list[LiveWork]:
            return [line]

        async def live_tops(self, _types: list[str]) -> list[dict[str, object]]:
            return self.tops

        async def pressed_since(self, _types: list[str], _since: int) -> list[PressedWork]:
            return [made]

    carried = [
        {"roots": ["r-1"], "each": {"thumbnails": 5, "no-such": 3, "previews": "many"}},
        {"products": ["thumbnails"], "files": 9},
    ]
    found, _lines = await read_presses(Queue(carried), set())  # type: ignore[arg-type]
    run = found[Family.GENERATE]
    assert (run.folders, run.folders_total, run.folders_done) == (
        2,
        {"thumbnail": 5},
        {"thumbnail": 1},
    )
    assert run.folders_left == 4

    older, _lines = await read_presses(Queue([]), set())  # type: ignore[arg-type]
    run = older[Family.GENERATE]
    assert (run.folders, run.library, run.folders_done) == (0, 2, {})
