# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the Stash routes refuse, and why, before anything in the library is changed.

A real application, as the run's own tests use: each refusal is asked for over HTTP and read back
as the status and the sentence the screen shows, then the library is read to see nothing moved.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.wiring import QUEUE, part_of_app
from sift.main import create_app
from sift.slices.stash_migration import SERVICE, STASH_IMPORT
from sift.slices.stash_migration.service import COPY_NAME, FOLDER, PLAN_NAME, REPORT_NAME
from sift.slices.stash_migration.tests.stash_fixture import make_stash
from sift.slices.stash_migration.tests.test_run import (
    _finished,
    _library,
    _read,
    _run_sql,
    _signed_in,
)

pytestmark = [pytest.mark.integration]


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


def _given(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Stash's folder with its database, and this library's folder of the same name."""
    given = tmp_path / "given"
    stash = make_stash(given / "stash-go.sqlite")
    clips = tmp_path / "media" / "Clips"
    clips.mkdir(parents=True)
    return given, stash, clips


def test_a_read_of_a_missing_file_or_of_no_database_is_refused_and_keeps_no_copy(
    app: FastAPI, tmp_path: Path
) -> None:
    """A name with no file behind it, and a file that is not a database, are each refused with
    the sentence the pane shows. The refused copy is not left behind, so the pane does not go on
    showing the read before it as if it were this one."""
    given, stash, clips = _given(tmp_path)
    (given / "notes.sqlite").write_bytes(b"These are notes, not a database. " * 200)
    with TestClient(app) as client:
        database, _user_id = _signed_in(client)
        _library(database, given, clips)
        assert client.post("/api/stash-migration/read", json={"path": str(stash)}).is_success

        missing = client.post(
            "/api/stash-migration/read", json={"path": str(given / "gone.sqlite")}
        )
        assert missing.status_code == 422
        assert "There's no file there." in missing.text

        refused = client.post(
            "/api/stash-migration/read", json={"path": str(given / "notes.sqlite")}
        )
        assert refused.status_code == 422
        assert "isn't a database Sift can read" in refused.text
        assert not (tmp_path / "data" / FOLDER / COPY_NAME).exists()
        assert client.get("/api/stash-migration").json() is None


def test_a_read_whose_record_is_damaged_reads_as_no_read(app: FastAPI, tmp_path: Path) -> None:
    """The pane is drawn from the copy and the record kept beside it. A record that cannot be
    read says nothing was read, so the pane offers a new read rather than a run over a guess."""
    given, stash, clips = _given(tmp_path)
    with TestClient(app) as client:
        database, _user_id = _signed_in(client)
        _library(database, given, clips)
        assert client.post("/api/stash-migration/read", json={"path": str(stash)}).is_success
        assert client.get("/api/stash-migration").json() is not None
        (tmp_path / "data" / FOLDER / PLAN_NAME).write_text("{not json", encoding="utf-8")
        assert client.get("/api/stash-migration").json() is None


def test_a_run_is_refused_with_nothing_read_while_one_waits_or_with_no_blobs_folder(
    app: FastAPI, tmp_path: Path
) -> None:
    """Run asks for one task over the last read: never with nothing read, never a second beside
    one already waiting, and never with a blobs folder that is not a folder, so the task cannot
    start on a choice it would only fail on later."""
    given, stash, clips = _given(tmp_path)
    with TestClient(app) as client:
        database, _user_id = _signed_in(client)
        _library(database, given, clips)
        first = client.post("/api/stash-migration/run")
        assert first.status_code == 409
        assert first.json()["detail"] == "Read a Stash database first."

        assert client.post("/api/stash-migration/read", json={"path": str(stash)}).is_success
        (given / "blobs.txt").write_text("not a folder", encoding="utf-8")
        no_folder = client.post(
            "/api/stash-migration/run",
            json={"pictures": True, "blobs": str(given / "blobs.txt")},
        )
        assert no_folder.status_code == 409
        assert "There's no folder there." in no_folder.text

        queue = part_of_app(app, QUEUE)
        portal = client.portal
        assert portal is not None
        portal.call(lambda: queue.enqueue(STASH_IMPORT, {}, run_after=int(time.time()) + 3600))
        second = client.post("/api/stash-migration/run")
        assert second.status_code == 409
        assert second.json()["detail"] == "A Stash library is already being imported."
    assert len(_read(database, "SELECT id FROM jobs WHERE type = ?", (STASH_IMPORT,))) == 1


def test_a_new_library_is_refused_before_anything_is_made(app: FastAPI, tmp_path: Path) -> None:
    """A new library is only made for a read whose folders match folders here (else it would
    have no files to scan), and only where Sift can make one."""
    given, stash, clips = _given(tmp_path)
    with TestClient(app) as client:
        database, _user_id = _signed_in(client)
        unread = client.post("/api/stash-migration/new-library", json={"name": "Other"})
        assert unread.status_code == 409
        assert unread.json()["detail"] == "Read a Stash database first."

        # Read with no folder here named as Stash's is: nothing matches.
        _run_sql(
            database,
            [
                (
                    "INSERT INTO browse_grants (id, abs_path, granted_at) VALUES (?, ?, 1700000000)",
                    (new_id(), str(given)),
                )
            ],
        )
        assert client.post("/api/stash-migration/read", json={"path": str(stash)}).is_success
        unmatched = client.post("/api/stash-migration/new-library", json={"name": "Other"})
        assert unmatched.status_code == 409
        assert "None of Stash's folders is a folder of this library" in unmatched.text

        _run_sql(
            database,
            [
                (
                    "INSERT INTO library_roots (id, name, abs_path, created_at)"
                    " VALUES (?, 'Clips', ?, 1700000000)",
                    (new_id(), str(clips)),
                )
            ],
        )
        migration = part_of_app(app, SERVICE)
        doors, migration.doors = migration.doors, None
        try:
            nowhere = client.post("/api/stash-migration/new-library", json={"name": "Other"})
        finally:
            migration.doors = doors
        assert nowhere.status_code == 409
        assert nowhere.json()["detail"] == "Sift can't make a library from here."


def test_a_new_library_is_made_with_the_matched_folders_and_switched_to(
    app: FastAPI, tmp_path: Path
) -> None:
    """The route hands the library maker the name and a seed that gives the new library the
    folders the read matched, under their names here. It answers 202: the switch is arranged."""
    given, stash, clips = _given(tmp_path)
    made = tmp_path / "libraries" / "Other" / "data"
    made.mkdir(parents=True)
    fresh = made / "sift.sqlite3"
    asked: list[str] = []

    async def new_library(name: str, actor: Any, seed: Any) -> str:
        asked.append(name)
        opened = Database(fresh, readers=1)
        await opened.connect()
        try:
            await opened.initialize_schema()
        finally:
            await opened.close()
        await seed(fresh, made)
        return "01NEWLIBRARY"

    with TestClient(app) as client:
        database, _user_id = _signed_in(client)
        _library(database, given, clips)
        assert client.post("/api/stash-migration/read", json={"path": str(stash)}).is_success
        migration = part_of_app(app, SERVICE)
        migration.doors.new_library = new_library  # type: ignore[method-assign,union-attr]
        switched = client.post(
            "/api/stash-migration/new-library", json={"name": "Other", "pictures": True}
        )
        assert switched.status_code == 202, switched.text
        assert switched.json() == {"switching": True, "library": "01NEWLIBRARY"}
    assert asked == ["Other"]
    assert _read(fresh, "SELECT name, abs_path FROM library_roots") == [
        ("Clips", str(clips.resolve()))
    ]
    # The choice about pictures is kept with the read before it goes with the new library.
    assert json.loads((made / FOLDER / PLAN_NAME).read_text(encoding="utf-8"))["pictures"] is True


def test_a_run_without_the_other_features_says_what_it_left_undone(
    app: FastAPI, tmp_path: Path
) -> None:
    """Favourites, ids, markers, filters, groups and pictures are each written by another
    feature. A run that cannot reach them still brings the files' own records across, and its
    report names each part it could not do rather than counting it as nothing."""
    given, stash, clips = _given(tmp_path)
    with TestClient(app) as client:
        database, _user_id = _signed_in(client)
        _library(database, given, clips)
        assert client.post("/api/stash-migration/read", json={"path": str(stash)}).is_success
        part_of_app(app, SERVICE).doors = None
        started = client.post("/api/stash-migration/run")
        assert started.status_code == 202, started.text
        assert _finished(database, started.json()["job_id"])[0] == "done"
    report = json.loads((tmp_path / "data" / FOLDER / REPORT_NAME).read_text(encoding="utf-8"))
    assert report["not_done"] == [
        "favorites",
        "stash-box ids",
        "markers",
        "saved filters",
        "groups",
        "pictures",
    ]
    assert report["scenes_matched"] == 2
    assert (report["marks"], report["saved_searches"], report["collections"]) == (0, 0, 0)
    assert report["filters_not_brought"] == [
        "Not organized (organized)",
        "Favorite people (a filter over performers)",
    ]
