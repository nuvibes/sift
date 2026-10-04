# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bringing a Stash library in, over HTTP, into a library holding two of its three files.

A real application: the writers, the lookups and the task are the ones a run uses, and what is
asserted is what a person would find afterwards, read back from the database.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobHeld
from sift.kernel.wiring import QUEUE, part_of_app
from sift.main import create_app
from sift.slices import stash_migration
from sift.slices.stash_migration import SERVICE, STASH_ARRIVED, STASH_IMPORT
from sift.slices.stash_migration.service import (
    FOLDER,
    MOMENT_TAG,
    PLAN_NAME,
    REPORT_NAME,
    StashMigration,
)
from sift.slices.stash_migration.tests.stash_fixture import (
    ALONE_PERSON,
    ALONE_SITE,
    ALONE_TAG,
    BOX,
    GROUP,
    OSHASH,
    TOP,
    WAITING_PERSON,
    WAITING_PHASH_HEX,
    WAITING_SITE,
    WAITING_TAG,
    WORN_TAG,
    ZIP,
    blobs_folder,
    make_stash,
    picture,
)
from sift.testing.auth import establish_session

pytestmark = [pytest.mark.integration]

_EPOCH = 1_700_000_000


def _run_sql(path: Path, statements: list[tuple[str, tuple[object, ...]]]) -> None:
    async def run() -> None:
        database = Database(path, readers=1)
        await database.connect()
        try:
            async with database.write() as connection:
                for sql, params in statements:
                    await connection.execute(sql, params)
        finally:
            await database.close()

    asyncio.run(run())


def _read(path: Path, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    async def run() -> list[tuple[object, ...]]:
        database = Database(path, readers=1)
        await database.connect()
        try:
            return [tuple(row) for row in await database.fetch_all(sql, params)]
        finally:
            await database.close()

    return asyncio.run(run())


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def doors_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    """The application with the run's doors in hand, whether or not the composition root has
    handed them in yet; `test_the_application_hands_the_run_its_doors` holds the root to it."""
    from sift.wiring.stash_doors import stash_doors

    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    made: dict[str, FastAPI] = {}

    class Handed(StashMigration):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            if self.doors is None:
                self.doors = stash_doors(made["app"])

    monkeypatch.setattr(stash_migration, "StashMigration", Handed)
    made["app"] = create_app()
    yield made["app"]
    get_settings.cache_clear()


def test_the_application_hands_the_run_its_doors(app: FastAPI) -> None:
    with TestClient(app):
        assert part_of_app(app, SERVICE).doors is not None


def _asset(asset_id: str, name: str, oshash: str | None = None) -> tuple[str, tuple[object, ...]]:
    return (
        "INSERT INTO assets (id, identity, media_type, size_bytes, original_filename, added_at,"
        " oshash) VALUES (?, ?, 'video', 10, ?, ?, ?)",
        (asset_id, f"digest-{name}", name, _EPOCH, oshash),
    )


def test_a_stash_library_comes_across_onto_the_files_this_library_holds(
    app: FastAPI, tmp_path: Path
) -> None:
    given = tmp_path / "given"
    stash = make_stash(given / "stash-go.sqlite")
    clips = tmp_path / "media" / "Clips"
    clips.mkdir(parents=True)
    with TestClient(app) as client:
        database = client.app.state.database.path  # type: ignore[attr-defined]
        _user_id, token, csrf = establish_session(
            database, role="admin", username="stash-admin", password="A-Stash-Test-Passw0rd!"
        )
        client.cookies.set(SESSION_COOKIE_NAME, token)
        client.headers[CSRF_HEADER_NAME] = csrf
        root, folder, first, second = (new_id() for _ in range(4))
        _run_sql(
            database,
            [
                (
                    "INSERT INTO browse_grants (id, abs_path, granted_at) VALUES (?, ?, ?)",
                    (new_id(), str(given), _EPOCH),
                ),
                (
                    "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
                    (root, "Clips", str(clips), _EPOCH),
                ),
                (
                    "INSERT INTO folders (id, root_id, parent_id, rel_path, name)"
                    " VALUES (?, ?, NULL, '', 'Clips')",
                    (folder, root),
                ),
                _asset(first, "first.mp4"),
                _asset(second, "renamed.mp4", OSHASH),
                (
                    "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path,"
                    " filename, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_id(), first, root, folder, "first.mp4", "first.mp4", _EPOCH, _EPOCH),
                ),
            ],
        )

        read = client.post("/api/stash-migration/read", json={"path": str(stash)})
        assert read.status_code == 200, read.text
        assert read.json()["mapping"] == {"/media/stash/Clips": str(clips)}
        started = client.post("/api/stash-migration/run")
        assert started.status_code == 202, started.text
        job_id = started.json()["job_id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            state = _read(database, "SELECT state, error FROM jobs WHERE id = ?", (job_id,))
            if state and state[0][0] in ("done", "failed", "canceled"):
                break
            time.sleep(0.2)
        assert state[0][0] == "done", state

    assert {row[0] for row in _read(database, "SELECT name FROM people")} >= {
        "Jane Doe",
        "Jane Roe",
    }
    assert _read(
        database,
        "SELECT c.name, p.name FROM sites c JOIN sites p ON p.id = c.parent_id",
    ) == [("Another Studio", "Jane Doe Videos")]
    assert _read(
        database, "SELECT c.name, p.name FROM tags c JOIN tags p ON p.id = c.parent_id"
    ) == [("Evening", "Time of day")]
    tagged = {
        (str(row[0]), str(row[1]))
        for row in _read(
            database,
            "SELECT at.asset_id, t.name FROM asset_tags at JOIN tags t ON t.id = at.tag_id",
        )
    }
    assert tagged == {(first, "Beach"), (first, "Evening")}
    # Filed under the import's own word, so nothing that takes back a stash-box's rows reaches them.
    for table in ("asset_people", "asset_tags", "asset_usernames"):
        assert _read(
            database,
            f"SELECT DISTINCT source FROM {table} WHERE asset_id IN (?, ?)",  # noqa: S608
            (first, second),
        ) == [("stash_library",)], table
    assert _read(
        database,
        "SELECT asset_id, rating, o_count, view_count FROM asset_user_state ORDER BY rating DESC",
    ) == [(first, 10, 2, 1), (second, 2, 0, 0)]
    assert _read(database, "SELECT title FROM assets WHERE id = ?", (second,)) == [
        ("Found by its hash",)
    ]
    # Every row it made says it came from a Stash library, never from a stash-box's answer.
    for table in ("people", "sites", "tags"):
        assert set(
            _read(database, f"SELECT created_by_kind, created_by_via FROM {table}")  # noqa: S608
        ) == {("sift", "stash_library")}, table


def _signed_in(client: TestClient) -> tuple[Path, str]:
    database = client.app.state.database.path  # type: ignore[attr-defined]
    user_id, token, csrf = establish_session(
        database, role="admin", username="stash-admin", password="A-Stash-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return database, user_id


def _library(
    database: Path, given: Path, clips: Path, *, pictures: int = 0
) -> tuple[str, str, str]:
    """This library: the folder Stash scans, the scene here by place, the scene by its hash and
    the picture inside the zip, and the first `pictures` of the fixture's gallery where Stash has
    them. Answers the three files' ids."""
    root, folder, first, second, zipped = (new_id() for _ in range(5))
    held: list[tuple[str, tuple[object, ...]]] = []
    for number in range(1, pictures + 1):
        held += _picture_here(root, folder, picture(number))
    _run_sql(
        database,
        [
            (
                "INSERT INTO browse_grants (id, abs_path, granted_at) VALUES (?, ?, ?)",
                (new_id(), str(given), _EPOCH),
            ),
            (
                "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
                (root, "Clips", str(clips), _EPOCH),
            ),
            (
                "INSERT INTO folders (id, root_id, parent_id, rel_path, name)"
                " VALUES (?, ?, NULL, '', 'Clips')",
                (folder, root),
            ),
            _asset(first, "first.mp4"),
            _asset(second, "renamed.mp4", OSHASH),
            (
                "INSERT INTO assets (id, identity, media_type, size_bytes, original_filename,"
                " added_at) VALUES (?, 'digest-inside', 'image', 10, 'inside.jpg', ?)",
                (zipped, _EPOCH),
            ),
            (
                "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path,"
                " filename, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id(), first, root, folder, "first.mp4", "first.mp4", _EPOCH, _EPOCH),
            ),
            (
                "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path,"
                " filename, first_seen_at, last_seen_at, archive_rel_path, member_path)"
                " VALUES (?, ?, ?, ?, ?, 'inside.jpg', ?, ?, ?, 'inside.jpg')",
                (new_id(), zipped, root, folder, f"{ZIP}/inside.jpg", _EPOCH, _EPOCH, ZIP),
            ),
            (
                "INSERT INTO stash_boxes (id, name, endpoint, enabled, created_at)"
                " VALUES (?, 'A box', ?, 1, ?)",
                (new_id(), BOX, _EPOCH),
            ),
            *held,
        ],
    )
    return first, second, zipped


def _picture_here(root: str, folder: str, name: str) -> list[tuple[str, tuple[object, ...]]]:
    asset_id = new_id()
    return [
        (
            "INSERT INTO assets (id, identity, media_type, size_bytes, original_filename,"
            " added_at) VALUES (?, ?, 'image', 10, ?, ?)",
            (asset_id, f"digest-{name}", name, _EPOCH),
        ),
        (
            "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path,"
            " filename, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id(), asset_id, root, folder, name, name, _EPOCH, _EPOCH),
        ),
    ]


def _finished(database: Path, job_id: str) -> tuple[object, ...]:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        state = _read(database, "SELECT state, error FROM jobs WHERE id = ?", (job_id,))
        if state and state[0][0] in ("done", "failed", "canceled"):
            return state[0]
        time.sleep(0.2)
    return state[0]


def test_markers_opinions_filters_zips_and_ids_come_across_through_their_own_doors(
    doors_app: FastAPI, tmp_path: Path
) -> None:
    given = tmp_path / "given"
    stash = make_stash(given / "stash-go.sqlite")
    clips = tmp_path / "media" / "Clips"
    clips.mkdir(parents=True)
    with TestClient(doors_app) as client:
        database, user_id = _signed_in(client)
        first, _second, zipped = _library(database, given, clips)
        assert client.post("/api/stash-migration/read", json={"path": str(stash)}).is_success
        started = client.post("/api/stash-migration/run")
        assert started.status_code == 202, started.text
        assert _finished(database, started.json()["job_id"])[0] == "done"
        report = json.loads((tmp_path / "data" / FOLDER / REPORT_NAME).read_text(encoding="utf-8"))

    # Every door was there, so nothing is said to be left undone.
    assert report["not_done"] == []
    # The stretch keeps its ends; the moment runs twenty seconds and says where it came from; the
    # marker on a scene this library lacks is counted, never made.
    marks = _read(
        database,
        "SELECT l.start_ms, l.end_ms, l.name, group_concat(t.name, '|') FROM loops l"
        " LEFT JOIN loop_tags lt ON lt.loop_id = l.id LEFT JOIN tags t ON t.id = lt.tag_id"
        " WHERE l.asset_id = ? GROUP BY l.id ORDER BY l.start_ms",
        (first,),
    )
    assert [(one[0], one[1], one[2]) for one in marks] == [
        (12_000, 32_000, "Evening"),
        (30_000, 45_500, "The jump"),
    ]
    assert set(str(marks[0][3]).split("|")) == {"Evening", MOMENT_TAG}
    assert set(str(marks[1][3]).split("|")) == {"Outdoors", "Beach"}
    assert (report["marks"], report["marks_from_moments"], report["marks_not_here"]) == (2, 1, 1)
    # Hearts and stars land on whoever pressed Run.
    assert _read(
        database,
        "SELECT p.name, s.favorite, s.rating FROM person_user_state s"
        " JOIN people p ON p.id = s.person_id WHERE s.user_id = ?",
        (user_id,),
    ) == [("Jane Doe", 1, 8)]
    assert _read(
        database,
        "SELECT favorite, rating FROM site_user_state WHERE user_id = ?",
        (user_id,),
    ) == [(1, 6)]
    assert _read(
        database,
        "SELECT t.name FROM tag_user_state s JOIN tags t ON t.id = s.tag_id"
        " WHERE s.user_id = ? AND s.favorite = 1",
        (user_id,),
    ) == [("Beach",)]
    # The scene filter came across in Sift's words; the other two are named with the reason.
    assert _read(
        database, "SELECT name, query FROM saved_searches WHERE user_id = ?", (user_id,)
    ) == [("Beach days", 'media:video rating:6+ tags:"Beach"')]
    assert report["filters_not_brought"] == [
        "Not organized (organized)",
        "Favorite people (a filter over performers)",
    ]
    # The picture in the zip is the one this library reads from the zip.
    assert report["zip_pictures_matched"] == 1
    assert report["images_in_zips"] == 0
    assert zipped
    # The box here cannot be reached, so the person, the Site and the scene are not asked.
    assert report["links"] == {"not_asked": 2}
    assert report["file_links"] == {"not_asked": 1}
    assert report["scene_box_ids"] == 1


def test_a_new_library_is_seeded_with_the_folders_the_copy_and_the_waiting_run(
    app: FastAPI, tmp_path: Path
) -> None:
    given = tmp_path / "given"
    stash = make_stash(given / "stash-go.sqlite")
    clips = tmp_path / "media" / "Clips"
    clips.mkdir(parents=True)
    with TestClient(app) as client:
        database, user_id = _signed_in(client)
        _library(database, given, clips)
        assert client.post("/api/stash-migration/read", json={"path": str(stash)}).is_success
        migration = part_of_app(app, SERVICE)
        made = tmp_path / "libraries" / "Other" / "data"
        made.mkdir(parents=True)
        fresh = made / "sift.sqlite3"

        async def seed() -> None:
            opened = Database(fresh, readers=1)
            await opened.connect()
            try:
                await opened.initialize_schema()
            finally:
                await opened.close()
            await migration._seed(fresh, made, [(clips, "Clips")], [given, clips], user_id)

        portal = client.portal
        assert portal is not None
        portal.call(seed)

    assert _read(fresh, "SELECT name, abs_path FROM library_roots") == [
        ("Clips", str(clips.resolve()))
    ]
    # Only the grant that holds a folder goes with it.
    assert _read(fresh, "SELECT abs_path FROM browse_grants") == [(str(clips.resolve()),)]
    assert _read(fresh, "SELECT type, state, requested_by FROM jobs") == [
        (STASH_IMPORT, "queued", user_id)
    ]
    plan = json.loads((made / FOLDER / PLAN_NAME).read_text(encoding="utf-8"))
    assert plan["after_first_scan"] is True
    assert plan["mapping"] == {"/media/stash/Clips": str(clips)}
    assert (made / FOLDER / "stash.sqlite").is_file()


def test_the_run_in_a_new_library_waits_for_its_first_scan(app: FastAPI, tmp_path: Path) -> None:
    given = tmp_path / "given"
    stash = make_stash(given / "stash-go.sqlite")
    clips = tmp_path / "media" / "Clips"
    clips.mkdir(parents=True)
    with TestClient(app) as client:
        database, user_id = _signed_in(client)
        _library(database, given, clips)
        assert client.post("/api/stash-migration/read", json={"path": str(stash)}).is_success
        migration = part_of_app(app, SERVICE)
        plan_path = tmp_path / "data" / FOLDER / PLAN_NAME
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["after_first_scan"] = True
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        queue = part_of_app(app, QUEUE)
        context = SimpleNamespace(queue=queue)
        portal = client.portal
        assert portal is not None

        async def wait() -> str:
            try:
                await migration._wait_for_first_scan(context, user_id)  # type: ignore[arg-type]
            except JobHeld as held:
                return str(held)
            return "ran"

        # First look: nothing asked yet, so the Scan task is asked for and the run waits.
        assert portal.call(wait).startswith("Waiting")
        asked = _read(database, "SELECT id FROM jobs WHERE type = 'library_scan'")
        assert len(asked) == 1
        # A scan still waiting in the line holds the run (one held for an hour, so no worker in
        # this test takes it); once nothing of the scan is outstanding the run goes ahead, once.
        _run_sql(database, [("UPDATE jobs SET state = 'canceled' WHERE id = ?", (asked[0][0],))])
        waiting = portal.call(
            lambda: queue.enqueue("library_scan", {}, run_after=int(time.time()) + 3600)
        )
        assert portal.call(wait).startswith("Waiting")
        _run_sql(database, [("UPDATE jobs SET state = 'canceled' WHERE id = ?", (waiting,))])
        assert portal.call(wait) == "ran"
        assert portal.call(wait) == "ran"
        assert json.loads(plan_path.read_text(encoding="utf-8"))["after_first_scan"] is False


def _brought_in(client: TestClient, database: Path) -> dict[str, Any]:
    """One run over the last read, to its end. Answers the run's report."""
    started = client.post("/api/stash-migration/run")
    assert started.status_code == 202, started.text
    assert _finished(database, started.json()["job_id"])[0] == "done"
    data = Path(str(get_settings().data_dir))
    report: dict[str, Any] = json.loads((data / FOLDER / REPORT_NAME).read_text(encoding="utf-8"))
    return report


#: What a run keeps about itself, which a second run adds to by being a second run.
_THE_RUNS_OWN = ("jobs", "work_runs", "schema_version", "sqlite_sequence")

#: The ledger, counted without the one line each finished task writes about itself ("ran"), which
#: is the run's own record in the same sense as the tables above.
_LEDGER = {
    "workbench_decisions": "SELECT COUNT(*) FROM workbench_decisions WHERE verb != 'ran'",
    "workbench_decision_subjects": (
        "SELECT COUNT(*) FROM workbench_decision_subjects s JOIN workbench_decisions d"
        " ON d.id = s.decision_id WHERE d.verb != 'ran'"
    ),
}


def _counts(database: Path) -> dict[str, int]:
    """How many rows every table of the library holds, but the queue's record of the runs. One
    connection for every table: opening one per table costs more than the whole run."""

    async def run() -> dict[str, int]:
        opened = Database(database, readers=1)
        await opened.connect()
        try:
            names = [
                str(row[0])
                for row in await opened.fetch_all(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND sql NOT LIKE"
                    " 'CREATE VIRTUAL%'"
                )
                if not str(row[0]).startswith(_THE_RUNS_OWN)
            ]
            counted: dict[str, int] = {}
            for name in names:
                # Every name comes out of the database's own catalog, never from a caller.
                sql = f'SELECT COUNT(*) FROM "{name}"'  # noqa: S608  # nosemgrep: sift-no-string-built-sql
                # nosemgrep: sift-no-string-built-sql
                row = await opened.fetch_one(_LEDGER.get(name, sql))
                counted[name] = int(row[0]) if row is not None else 0
            return counted
        finally:
            await opened.close()

    return asyncio.run(run())


def _differ(before: dict[str, int], after: dict[str, int]) -> dict[str, tuple[int, int]]:
    return {
        name: (before.get(name, 0), after.get(name, 0))
        for name in sorted(set(before) | set(after))
        if before.get(name, 0) != after.get(name, 0)
    }


def test_nothing_lands_without_its_file_and_what_waits_is_listed(
    doors_app: FastAPI, tmp_path: Path
) -> None:
    given = tmp_path / "given"
    stash = make_stash(given / "stash-go.sqlite", waiting=True, extras=True)
    clips = tmp_path / "media" / "Clips"
    clips.mkdir(parents=True)
    with TestClient(doors_app) as client:
        database, _user_id = _signed_in(client)
        _library(database, given, clips)
        assert client.post("/api/stash-migration/read", json={"path": str(stash)}).is_success
        report = _brought_in(client, database)
        read = client.get("/api/stash-migration").json()
        # The read names the database it came from, so the pane opened later says what it shows.
        assert read["source"] == str(stash)
        # Each wall lists exactly what Stash attached to nothing, and counts it as its own row of
        # Created by, which the Sift row then leaves out, so the rows still add up to the wall.
        walls = {
            wall: {
                str(row["name"])
                for row in client.get(
                    f"/api/{wall}", params={"created": "stash_unattached"}
                ).json()["items"]
            }
            for wall in ("people", "sites", "tags")
        }
        counted = {
            wall: {
                str(one["value"]): int(one["count"])
                for one in client.get(f"/api/{wall}/facets", params={"facet": "created"}).json()[
                    "values"
                ]
            }
            for wall in ("people", "sites", "tags")
        }
        sift_made = {
            wall: len(client.get(f"/api/{wall}", params={"created": "sift"}).json()["items"])
            for wall in ("people", "sites", "tags")
        }
        page = client.get("/api/stash-migration/waiting", params={"limit": 1}).json()
        rest = client.get("/api/stash-migration/waiting", params={"offset": 1}).json()

    # The person, the Site and the tag only the missing scene carries did not come across; the
    # network and the parent tag came with what is filed under them; the person, the Site and the
    # tag Stash attaches to nothing came across whole, counted apart and marked for the walls.
    names = {
        table: {str(row[0]) for row in _read(database, f"SELECT name FROM {table}")}  # noqa: S608
        for table in ("people", "sites", "tags")
    }
    assert WAITING_PERSON not in names["people"]
    assert WAITING_SITE not in names["sites"]
    assert WAITING_TAG not in names["tags"]
    assert {"Jane Doe Videos"} <= names["sites"] and {"Time of day"} <= names["tags"]
    assert (
        report["people_without_files"],
        report["sites_without_files"],
        report["tags_without_files"],
    ) == (1, 1, 1)
    for table, name in (("people", ALONE_PERSON), ("sites", ALONE_SITE), ("tags", ALONE_TAG)):
        assert _read(
            database,
            f"SELECT name FROM {table} WHERE created_by_via = 'stash_unattached'",  # noqa: S608
        ) == [(name,)], table
    assert read["unattached"] == {"people": 1, "sites": 1, "tags": 1}
    assert walls == {"people": {ALONE_PERSON}, "sites": {ALONE_SITE}, "tags": {ALONE_TAG}}
    for wall in ("people", "sites", "tags"):
        assert counted[wall]["stash_unattached"] == 1, wall
        # Created by splits what Sift made by the task that made it; `sift` narrows to all of it.
        by_sift = sum(n for via, n in counted[wall].items() if via != "stash_unattached")
        assert by_sift == sift_made[wall], wall
    # With everything Stash kept on her: the heart, the stars and the stash-box id.
    assert _read(
        database,
        "SELECT s.favorite, s.rating FROM person_user_state s JOIN people p ON p.id = s.person_id"
        " WHERE p.name = ?",
        (ALONE_PERSON,),
    ) == [(1, 4)]
    assert (
        report["waiting_scenes"],
        report["waiting_images"],
        report["people_waiting"],
        report["sites_waiting"],
        report["tags_waiting"],
        report["marks_waiting"],
    ) == (1, 1, 1, 1, 1, 1)
    said = str(read["ran"]["said"])
    assert "Of those, 1 Person, 1 Site and 1 Tag have no files in Stash either." in said
    assert said.endswith("The list is below.")
    assert (
        " 1 file and 1 picture this library doesn't have yet wait, with 1 marker, 1 Person,"
        " 1 Site and 1 Tag on them. Each comes across on its own once its file is in Sift."
    ) in said
    # The screen's list: every waiting scene by name and by Stash's path, with what waits on it.
    assert read["waiting"] == 2 and page["total"] == 2 and len(page["rows"]) == 1
    scene = page["rows"][0]
    assert (scene["kind"], scene["label"], scene["paths"]) == (
        "scene",
        "Waiting on the roof",
        [f"{TOP}/third.mp4"],
    )
    assert scene["markers"] == [{"title": "Elsewhere", "start_ms": 5000, "end_ms": 9000}]
    assert (scene["people"], scene["sites"], scene["tags"], scene["rating"]) == (
        [WAITING_PERSON],
        [WAITING_SITE],
        [WAITING_TAG],
        9,
    )
    assert [one["paths"] for one in rest["rows"]] == [[f"{TOP}/still.jpg"]]
    assert [one["label"] for one in report["waiting"]] == ["Waiting on the roof", "A still"]


def test_a_second_run_adds_nothing_twice(doors_app: FastAPI, tmp_path: Path) -> None:
    given = tmp_path / "given"
    stash = make_stash(given / "stash-go.sqlite", waiting=True, gallery=10)
    clips = tmp_path / "media" / "Clips"
    clips.mkdir(parents=True)
    with TestClient(doors_app) as client:
        database, _user_id = _signed_in(client)
        _library(database, given, clips, pictures=10)
        assert client.post("/api/stash-migration/read", json={"path": str(stash)}).is_success
        first = _brought_in(client, database)
        after_one = _counts(database)
        second = _brought_in(client, database)
        after_two = _counts(database)

    # The first run made something of every kind the test counts, so equal counts mean something.
    made = {
        "people",
        "people_aliases",
        "sites",
        "tags",
        "loops",
        "saved_searches",
        "photo_sets",
        "photo_set_items",
        "asset_user_state",
        "person_user_state",
        "site_user_state",
        "tag_user_state",
        "stash_waiting",
        "stash_waiting_files",
        "stash_waiting_entities",
        "stash_galleries",
    }
    assert {name for name in made if after_one.get(name, 0) == 0} == set()
    assert first["photo_sets"] == 1 and second["photo_sets"] == 0
    assert _differ(after_one, after_two) == {}
    assert (second["marks"], second["saved_searches"], second["ratings"]) == (0, 0, 0)
    assert second["waiting_scenes"] == first["waiting_scenes"] == 1


def test_a_gallery_whose_pictures_are_a_set_here_already_becomes_that_set(
    doors_app: FastAPI, tmp_path: Path
) -> None:
    given = tmp_path / "given"
    stash = make_stash(given / "stash-go.sqlite", waiting=True, gallery=10)
    clips = tmp_path / "media" / "Clips"
    clips.mkdir(parents=True)
    with TestClient(doors_app) as client:
        database, _user_id = _signed_in(client)
        _library(database, given, clips, pictures=10)
        assert client.post("/api/stash-migration/read", json={"path": str(stash)}).is_success
        first = _brought_in(client, database)
        # The set the first run made stands for one a zip or a folder made here before Stash was
        # read: the run keeps no note of it, so only the pictures can say it is the same shoot.
        _run_sql(database, [("DELETE FROM stash_galleries", ())])
        second = _brought_in(client, database)
        sets = _read(database, "SELECT COUNT(*) FROM photo_sets")
    assert first["photo_sets"] == 1 and second["photo_sets"] == 0
    assert sets == [(1,)]


def test_a_gallery_with_no_set_here_makes_a_set_that_says_a_stash_library_made_it(
    doors_app: FastAPI, tmp_path: Path
) -> None:
    given = tmp_path / "given"
    stash = make_stash(given / "stash-go.sqlite", waiting=True, gallery=10)
    clips = tmp_path / "media" / "Clips"
    clips.mkdir(parents=True)
    with TestClient(doors_app) as client:
        database, _user_id = _signed_in(client)
        _library(database, given, clips, pictures=10)
        assert client.post("/api/stash-migration/read", json={"path": str(stash)}).is_success
        assert _brought_in(client, database)["photo_sets"] == 1
        made = _read(database, "SELECT origin, created_by_kind, created_by_via FROM photo_sets")
    assert made == [("stash_library", "sift", "stash_library")]


def test_a_read_taken_before_its_folder_was_added_lands_every_picture_held_at_the_run(
    doors_app: FastAPI, tmp_path: Path
) -> None:
    given = tmp_path / "given"
    stash = make_stash(given / "stash-go.sqlite", gallery=12)
    clips = tmp_path / "media" / "Clips"
    clips.mkdir(parents=True)
    with TestClient(doors_app) as client:
        database, _user_id = _signed_in(client)
        _run_sql(
            database,
            [
                (
                    "INSERT INTO browse_grants (id, abs_path, granted_at) VALUES (?, ?, ?)",
                    (new_id(), str(given), _EPOCH),
                )
            ],
        )
        read = client.post("/api/stash-migration/read", json={"path": str(stash)})
        assert read.is_success and read.json()["mapping"] == {}
        # The folder is added, and its files indexed, after the read and before the run.
        _library(database, tmp_path / "granted later", clips, pictures=12)
        ((root, folder),) = _read(database, "SELECT root_id, id FROM folders")
        _run_sql(database, _picture_here(str(root), str(folder), "still.jpg"))
        assert client.get("/api/stash-migration").json()["mapping"] == {TOP: str(clips)}
        report = _brought_in(client, database)
        after = client.get("/api/stash-migration").json()

    assert report["images_matched"] == report["images"] == 14
    assert report["scenes"] == report["scenes_matched"]
    assert (report["waiting_scenes"], report["waiting_images"], after["waiting"]) == (0, 0, 0)
    assert report["photo_sets"] == 1
    assert report["folders_matched_since_read"] == 1
    said = str(after["ran"]["said"])
    assert "Since Stash was read, 1 of its folders matches a folder in this library." in said
    assert "doesn't have yet" not in said


def test_a_waiting_scene_lands_once_when_its_file_arrives(
    doors_app: FastAPI, tmp_path: Path
) -> None:
    given = tmp_path / "given"
    stash = make_stash(given / "stash-go.sqlite", waiting=True)
    clips = tmp_path / "media" / "Clips"
    clips.mkdir(parents=True)
    arrived = new_id()
    with TestClient(doors_app) as client:
        database, user_id = _signed_in(client)
        _library(database, given, clips)
        assert client.post("/api/stash-migration/read", json={"path": str(stash)}).is_success
        _brought_in(client, database)
        # The file arrives elsewhere and under another name: only its video fingerprint says what
        # it is.
        _run_sql(
            database,
            [
                (
                    "INSERT INTO assets (id, identity, media_type, size_bytes, original_filename,"
                    " added_at, video_phash) VALUES (?, 'digest-arrived', 'video', 10,"
                    " 'renamed-roof.mp4', ?, ?)",
                    (arrived, _EPOCH, WAITING_PHASH_HEX),
                )
            ],
        )
        queue = part_of_app(doors_app, QUEUE)
        portal = client.portal
        assert portal is not None
        landing = portal.call(lambda: queue.enqueue(STASH_ARRIVED, {}))
        assert _finished(database, landing)[0] == "done"
        after_landing = _counts(database)
        again = portal.call(lambda: queue.enqueue(STASH_ARRIVED, {}))
        assert _finished(database, again)[0] == "done"
        after_again = _counts(database)
        _brought_in(client, database)
        after_third_run = _counts(database)

    # Everything the scene carried is on the file: its record, its People, Site and Tags (made
    # now, from what Stash kept of them), its Loop, and the rating and O count of whoever ran it.
    assert _read(database, "SELECT title FROM assets WHERE id = ?", (arrived,)) == [
        ("Waiting on the roof",)
    ]
    for table in ("people", "sites", "tags"):
        assert set(
            _read(database, f"SELECT created_by_via FROM {table}")  # noqa: S608
        ) == {("stash_library",)}, table
    assert _read(
        database,
        "SELECT t.name FROM asset_tags a JOIN tags t ON t.id = a.tag_id WHERE a.asset_id = ?",
        (arrived,),
    ) == [(WAITING_TAG,)]
    assert _read(
        database,
        "SELECT alias FROM people_aliases a JOIN people p ON p.id = a.person_id WHERE p.name = ?",
        (WAITING_PERSON,),
    ) == [("Jane Mow",)]
    assert _read(
        database,
        "SELECT s.favorite, s.rating FROM person_user_state s JOIN people p ON p.id = s.person_id"
        " WHERE p.name = ? AND s.user_id = ?",
        (WAITING_PERSON, user_id),
    ) == [(1, 6)]
    assert _read(
        database, "SELECT start_ms, end_ms, name FROM loops WHERE asset_id = ?", (arrived,)
    ) == [(5_000, 9_000, "Elsewhere")]
    assert _read(
        database,
        "SELECT rating, o_count FROM asset_user_state WHERE asset_id = ? AND user_id = ?",
        (arrived, user_id),
    ) == [(9, 1)]
    # Applied once: the row and its People, Site and Tag no longer wait; the picture still does.
    assert _read(database, "SELECT kind FROM stash_waiting") == [("picture",)]
    assert _read(database, "SELECT COUNT(*) FROM stash_waiting_entities") == [(0,)]
    # And nothing moves after that: the pass again, then a third run of the migration.
    assert _differ(after_landing, after_again) == {}
    assert _differ(after_landing, after_third_run) == {}


def test_resume_groups_pictures_and_the_from_stash_line_come_across_once(
    doors_app: FastAPI, tmp_path: Path
) -> None:
    """What the tool brings besides the records: where a scene was stopped and how long it was
    watched (never a date), a group as a Collection in its own order, the pictures of People and
    Sites as their covers (from the database and from the blobs folder, through the cover door),
    and one "From Stash:" line for what Sift has no field for. A second run adds none of it again."""
    given = tmp_path / "given"
    stash = make_stash(given / "stash-go.sqlite", extras=True)
    blobs = blobs_folder(given / "blobs")
    clips = tmp_path / "media" / "Clips"
    clips.mkdir(parents=True)
    with TestClient(doors_app) as client:
        database, user_id = _signed_in(client)
        first, second, _zipped = _library(database, given, clips)
        read = client.post("/api/stash-migration/read", json={"path": str(stash)})
        assert read.is_success, read.text
        # The folder beside the database is offered, since one picture is kept as a file.
        assert read.json()["blobs"] == str(given / "blobs")
        chosen = {"pictures": True, "blobs": str(blobs)}
        started = client.post("/api/stash-migration/run", json=chosen)
        assert started.status_code == 202, started.text
        assert _finished(database, started.json()["job_id"])[0] == "done"
        report = json.loads((tmp_path / "data" / FOLDER / REPORT_NAME).read_text(encoding="utf-8"))
        again = client.post("/api/stash-migration/run", json=chosen)
        assert _finished(database, again.json()["job_id"])[0] == "done"
        # A blobs folder outside every folder Sift was given is refused before anything runs.
        outside = client.post(
            "/api/stash-migration/run", json={"pictures": True, "blobs": str(tmp_path / "else")}
        )
        assert outside.status_code == 409, outside.text

    assert _read(
        database,
        "SELECT resume_ms, watched_ms FROM asset_user_state WHERE asset_id = ? AND user_id = ?",
        (first, user_id),
    ) == [(42_500, 300_000)]
    assert (report["resume_points"], report["time_watched"]) == (1, 1)
    assert _read(database, "SELECT details FROM assets WHERE id = ?", (first,)) == [
        ("From Stash: director Ada Byron; organized; captions in en; mood: calm",)
    ]
    assert _read(database, "SELECT notes FROM people WHERE name = 'Jane Doe'") == [
        ("Swims.\n\nFrom Stash: weight 55 kg; shoe: 38",)
    ]
    assert _read(
        database,
        "SELECT t.name FROM person_tags pt JOIN tags t ON t.id = pt.tag_id"
        " JOIN people p ON p.id = pt.person_id WHERE p.name = 'Jane Doe'",
    ) == [(WORN_TAG,)]
    # One Collection, owned by whoever pressed Run, with the group's scenes in the group's order.
    assert _read(
        database,
        "SELECT c.name, c.owner_id, i.asset_id FROM collections c"
        " JOIN collection_items i ON i.collection_id = c.id ORDER BY i.position",
    ) == [(GROUP, user_id, second), (GROUP, user_id, first)]
    assert report["collections"] == 1
    # Her picture from the database and the Site's from the blobs folder, each re-encoded.
    covered = _read(
        database,
        "SELECT name FROM people WHERE cover_upload_id IS NOT NULL"
        " UNION ALL SELECT name FROM sites WHERE cover_upload_id IS NOT NULL",
    )
    assert sorted(covered) == [("Another Studio",), ("Jane Doe",)]
    assert (report["pictures"], report["pictures_not_found"]) == (2, 0)
