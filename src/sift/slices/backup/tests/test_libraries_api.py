# SPDX-License-Identifier: AGPL-3.0-or-later
"""The library endpoints, against a real application.

What the service decides is proved beside it (`test_libraries.py`). What is proved here is the
wire: each refusal reaches the page with its own status and its own sentence, a switch is answered
202 because it is arranged rather than done, and an upload never outlives its request. The
supervisor is a recorder, so a test that makes a library does not restart the test process.
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import Role
from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.jobs import WorkerPool
from sift.kernel.wiring import part_of_app, provide
from sift.main import create_app
from sift.slices.backup.libraries import (
    LIBRARIES,
    REGISTRY_FILENAME,
    LibrariesService,
    library_id,
)
from sift.slices.backup.libraries_router import _refusal
from sift.slices.backup.service import SERVICE as BACKUP
from sift.slices.backup.service import Busy, NotABackup
from sift.testing.auth import establish_session

pytestmark = [pytest.mark.integration]

LIBS = "/api/libraries"


class Supervisor:
    """What `sift.kernel.lifecycle` answers: a restart is possible, and asking for one is recorded."""

    def __init__(self) -> None:
        self.asked = 0

    def can(self) -> bool:
        return True

    def ask(self) -> bool:
        self.asked += 1
        return True


@pytest.fixture
def supervisor() -> Supervisor:
    return Supervisor()


@pytest.fixture
def app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, supervisor: Supervisor
) -> Iterator[TestClient]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))

    async def no_workers(self: WorkerPool) -> None: ...

    monkeypatch.setattr(WorkerPool, "start", no_workers)
    get_settings.cache_clear()
    built = create_app()
    with TestClient(built) as client:
        state = built.state
        provide(
            built,
            LIBRARIES,
            LibrariesService(
                state.database,
                get_settings(),
                part_of_app(built, BACKUP),
                can_restart=supervisor.can,
                ask_to_restart=supervisor.ask,
            ),
        )
        db_path = state.database.path
        _user, token, csrf = establish_session(
            db_path, role=Role.ADMIN.value, username="libs-admin", password="Corr3ct-Horse!staple9"
        )
        client.cookies.set(SESSION_COOKIE_NAME, token)
        client.headers[CSRF_HEADER_NAME] = csrf
        yield client
    get_settings.cache_clear()


def test_the_list_names_the_running_library_first_and_whether_a_switch_can_happen(
    app: TestClient,
) -> None:
    answer = app.get(LIBS)

    assert answer.status_code == 200
    body = answer.json()
    assert body["can_switch"] is True
    assert body["libraries"][0]["current"] is True
    assert body["folder"]


def test_making_a_library_is_accepted_as_a_switch_arranged_and_names_it(
    app: TestClient, supervisor: Supervisor
) -> None:
    answer = app.post(LIBS, json={"name": "Work"})

    assert answer.status_code == 202
    assert answer.json()["switching"] is True
    listed = {one["name"]: one["id"] for one in app.get(LIBS).json()["libraries"]}
    assert answer.json()["library"] == listed["Work"]
    assert supervisor.asked == 1


def test_each_refusal_reaches_the_page_with_its_own_status(app: TestClient) -> None:
    """A name that is not one folder name is the request's fault (422); an id on no list is a 404;
    a name already taken is a fact about now (409)."""
    assert app.post(LIBS, json={"name": "con"}).status_code == 422
    assert app.post(f"{LIBS}/open", json={"library": "0123456789abcdef0123"}).status_code == 404
    app.post(LIBS, json={"name": "Work"})
    taken = app.post(LIBS, json={"name": "work"})
    assert taken.status_code == 409
    assert "already a library" in taken.json()["detail"]


def test_opening_the_running_library_is_accepted_and_switches_nothing(
    app: TestClient, supervisor: Supervisor
) -> None:
    running = app.get(LIBS).json()["libraries"][0]["id"]

    answer = app.post(f"{LIBS}/open", json={"library": running})

    assert answer.status_code == 202
    assert answer.json() == {"switching": False, "library": running}
    assert supervisor.asked == 0


def _opened_lines(app: TestClient) -> list[str]:
    """Every "opened a library" line on the Settings feed, as the admin reads it."""
    items = app.get("/api/ledger").json()["items"]
    return [
        "".join(piece["text"] for piece in one["pieces"])
        for one in items
        if one["verb"] == "library_opened"
    ]


def test_opening_another_library_says_who_and_from_which_device_in_history(
    app: TestClient, supervisor: Supervisor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The switcher on the screen changes what the computer running Sift serves, as the app's own
    list does, so it writes the same line: the library by its name, and the device it came from."""
    monkeypatch.setattr("sift.kernel.machine_acts.machine_name", lambda: "DESK-ONE")
    app.post(LIBS, json={"name": "Work"})
    work = {one["name"]: one["id"] for one in app.get(LIBS).json()["libraries"]}["Work"]

    answer = app.post(f"{LIBS}/open", params={"device": "LAPTOP-TWO"}, json={"library": work})

    assert answer.status_code == 202
    assert answer.json()["switching"] is True
    assert _opened_lines(app) == ["You opened the library Work on DESK-ONE, from LAPTOP-TWO"]


def test_opening_the_running_library_writes_no_line(app: TestClient) -> None:
    running = app.get(LIBS).json()["libraries"][0]["id"]

    app.post(f"{LIBS}/open", json={"library": running})

    assert _opened_lines(app) == []


def test_a_busy_library_is_a_conflict_and_any_other_backup_refusal_is_the_files_fault() -> None:
    """Work already running is a fact about now, not about the file; every other backup refusal
    is about what was sent."""
    assert _refusal(Busy("A backup is running.")).status_code == 409
    assert _refusal(NotABackup("That file isn't a Sift backup.")).status_code == 422


def test_an_uploaded_backup_becomes_a_library_and_the_upload_is_not_kept(app: TestClient) -> None:
    # A backup saved by hand answers where it went; the file itself is asked for by its name, as
    # a browser on another device asks for it.
    saved = app.post("/api/backup/export")
    assert saved.status_code == 200
    exported = app.get(f"/api/backup/saved/{saved.json()['name']}")
    assert exported.status_code == 200

    answer = app.post(
        f"{LIBS}/import",
        data={"name": "Given"},
        files={"file": ("given.zip", exported.content, "application/zip")},
    )

    assert answer.status_code == 202, answer.text
    made = [one for one in app.get(LIBS).json()["libraries"] if one["name"] == "Given"]
    assert [one["id"] for one in made] == [answer.json()["library"]]
    assert list((get_settings().data_dir / "backup-staging").glob("*")) == []


def test_an_upload_that_is_no_sift_file_is_refused_and_leaves_no_library(app: TestClient) -> None:
    junk = io.BytesIO()
    with zipfile.ZipFile(junk, "w") as archive:
        archive.writestr("notes.txt", "not a backup")

    answer = app.post(
        f"{LIBS}/import",
        data={"name": "Junk"},
        files={"file": ("junk.zip", junk.getvalue(), "application/zip")},
    )

    assert answer.status_code == 422
    assert "Junk" not in {one["name"] for one in app.get(LIBS).json()["libraries"]}


def test_a_duplicate_is_planned_then_queued_as_a_task(app: TestClient) -> None:
    plan = app.get(f"{LIBS}/duplicate")
    assert plan.status_code == 200
    assert plan.json()["records_bytes"] > 0
    assert plan.json()["refusal"] is None

    queued = app.post(f"{LIBS}/duplicate", json={"name": "Copy", "pictures": False})
    assert queued.status_code == 202
    assert queued.json()["job_id"]

    refused = app.post(f"{LIBS}/duplicate", json={"name": "a/b", "pictures": False})
    assert refused.status_code == 422


def test_the_library_that_opens_at_start_is_chosen_and_answered_with_the_list(
    app: TestClient,
) -> None:
    running = app.get(LIBS).json()["libraries"][0]["id"]

    chosen = app.put(f"{LIBS}/opens-at-start", json={"library": running})
    assert chosen.status_code == 200
    assert [one["opens_at_start"] for one in chosen.json()["libraries"]] == [True]

    cleared = app.put(f"{LIBS}/opens-at-start", json={"library": None})
    assert [one["opens_at_start"] for one in cleared.json()["libraries"]] == [False]

    gone = app.put(f"{LIBS}/opens-at-start", json={"library": "0123456789abcdef0123"})
    assert gone.status_code == 404
    assert "Read the list again" in gone.json()["detail"]


def test_a_library_in_the_folder_is_deleted_and_the_list_comes_back_without_it(
    app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken: list[Path] = []

    def recycle(folder: Path) -> None:
        taken.append(folder)
        shutil.rmtree(folder)

    # The bin is the operating system's; here it is a recorder, so nothing leaves the test.
    real = LibrariesService.delete

    async def into_the_recorder(self: LibrariesService, library: str, typed: str) -> None:
        await real(self, library, typed, recycle=recycle)

    monkeypatch.setattr(LibrariesService, "delete", into_the_recorder)
    work = app.post(LIBS, json={"name": "Work"}).json()["library"]
    running = app.get(LIBS).json()["libraries"][0]["id"]

    wrong = app.post(f"{LIBS}/delete", json={"library": work, "name": "work"})
    assert wrong.status_code == 409
    assert "Type Work exactly" in wrong.json()["detail"]
    open_one = app.post(f"{LIBS}/delete", json={"library": running, "name": "x"})
    assert open_one.status_code == 409
    assert taken == []

    answer = app.post(f"{LIBS}/delete", json={"library": work, "name": "Work"})

    assert answer.status_code == 200
    assert [one.name for one in taken] == ["Work"]
    assert work not in {one["id"] for one in answer.json()["libraries"]}


def test_a_library_kept_elsewhere_is_forgotten_and_the_list_comes_back_without_it(
    app: TestClient, tmp_path: Path
) -> None:
    away = tmp_path / "away" / "data"
    away.mkdir(parents=True)
    folder = Path(app.get(LIBS).json()["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / REGISTRY_FILENAME).write_text(
        json.dumps({"format": 1, "elsewhere": [{"data_dir": str(away), "cache_dir": "c"}]}),
        encoding="utf-8",
    )
    running = app.get(LIBS).json()["libraries"][0]["id"]

    refused = app.post(f"{LIBS}/forget", json={"library": running})
    assert refused.status_code == 409
    assert "folder of its own" in refused.json()["detail"]
    assert app.post(f"{LIBS}/forget", json={"library": "0123456789abcdef0123"}).status_code == 404

    answer = app.post(f"{LIBS}/forget", json={"library": library_id(away)})

    assert answer.status_code == 200
    assert [one["id"] for one in answer.json()["libraries"]] == [running]
    assert away.is_dir()
