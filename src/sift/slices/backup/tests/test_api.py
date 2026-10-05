# SPDX-License-Identifier: AGPL-3.0-or-later
"""The endpoints, against a real application.

The refusals here are the ones the interface must not be trusted for. Hiding the Backup section
from a guest is a courtesy; a guest who types the address gets a refusal from the server, and that
is what these ask about. The round trip is here too, because export and restore are one promise
and testing them apart proves neither.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import Role
from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.jobs import WorkerPool
from sift.main import create_app
from sift.testing.auth import establish_session

pytestmark = [pytest.mark.integration]

PASSWORD = "Corr3ct-Horse!staple9"

#: Every backup route, by the method that reaches it. Written out rather than discovered, so a
#: route added without a thought about who may call it is not silently swept up as covered.
ROUTES = (
    ("POST", "/api/backup/export"),
    ("POST", "/api/backup/restore"),
    ("GET", "/api/backup/schedule"),
    ("PUT", "/api/backup/schedule"),
    ("GET", "/api/backup/unmarked"),
    ("DELETE", "/api/backup/unmarked/sift-backup-20260720-141500-1.0.0.zip"),
    ("GET", "/api/backup/saved/sift-backup-20260720-141500-1.0.0.zip"),
)


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A real application, with the workers idle.

    The pool is stopped because nothing here is about background work, and a running pool writes to
    the database this file exports and replaces underneath it.
    """
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))

    async def no_workers(self: WorkerPool) -> None: ...

    monkeypatch.setattr(WorkerPool, "start", no_workers)
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        yield client
    get_settings.cache_clear()


def sign_in(client: TestClient, role: Role) -> str:
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    user_id, token, csrf = establish_session(
        db_path, role=role.value, username=f"backup-{role.value}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


@pytest.mark.parametrize(("method", "path"), ROUTES, ids=[f"{m} {p}" for m, p in ROUTES])
def test_a_guest_is_refused_every_backup_route(app: TestClient, method: str, path: str) -> None:
    """An export is a copy of the whole library's records. It is not a guest's to take."""
    sign_in(app, Role.GUEST)
    assert app.request(method, path).status_code == 403


@pytest.mark.parametrize(("method", "path"), ROUTES, ids=[f"{m} {p}" for m, p in ROUTES])
def test_nobody_signed_in_is_refused_every_backup_route(
    app: TestClient, method: str, path: str
) -> None:
    """Turned away either as nobody (401) or by the cross-site check that runs first (403). Which
    of the two is not this feature's decision; being refused is."""
    assert app.request(method, path).status_code in {401, 403}


def test_an_admin_exports_and_restores_through_the_api(app: TestClient) -> None:
    """The round trip a person actually makes: press the button, keep the file, put it back."""
    sign_in(app, Role.ADMIN)
    app.post("/api/tags", json={"name": "worth-keeping"})

    exported = app.post("/api/backup/export")
    assert exported.status_code == 200
    answer = exported.json()
    # Into the backup folder, named as saved by hand, so no rule ever deletes it; the answer says
    # where it went rather than carrying the file.
    folder = get_settings().data_dir / "backups"
    assert answer["folder"] == str(folder)
    assert answer["name"].startswith("sift-backup-") and answer["name"].endswith("-saved.zip")
    assert answer["path"] == str(folder / answer["name"])
    assert answer["size_bytes"] == (folder / answer["name"]).stat().st_size
    listed = app.get("/api/backup/unmarked").json()["backups"]
    assert [(one["name"], one["saved"]) for one in listed] == [(answer["name"], True)]

    # A browser on another device is handed a copy when it asks, by the name the list shows.
    copy = app.get(f"/api/backup/saved/{answer['name']}")
    assert copy.status_code == 200
    assert copy.headers["content-disposition"].startswith("attachment")
    assert answer["name"] in unquote(copy.headers["content-disposition"])
    saved = copy.content
    assert saved.startswith(b"PK\x03\x04"), "one file, an archive"
    assert app.get("/api/backup/saved/holiday.zip").status_code == 404

    app.delete(f"/api/tags/{app.get('/api/tags').json()['items'][0]['id']}")
    assert app.get("/api/tags").json()["items"] == []

    put_back = app.post(
        "/api/backup/restore", files={"file": ("backup.zip", saved, "application/octet-stream")}
    )
    assert put_back.status_code == 200
    assert put_back.json()["app_version"]
    assert put_back.json()["carried"] == ["faces/references", "covers"]
    assert app.get("/api/backup/contents").status_code == 200

    # The session survived because it was in the backup too, so the tag can be read straight back.
    assert [tag["name"] for tag in app.get("/api/tags").json()["items"]] == ["worth-keeping"]


def test_the_export_leaves_nothing_staged_in_the_data_directory(app: TestClient) -> None:
    """The one file a press makes is the one in the backup folder: nothing else is kept."""
    sign_in(app, Role.ADMIN)
    staging = Path(app.app.state.settings.data_dir) / "backup-staging"  # type: ignore[attr-defined]

    assert app.post("/api/backup/export").status_code == 200

    assert not staging.exists() or list(staging.iterdir()) == []


def test_a_file_that_is_not_a_backup_is_refused_without_touching_the_library(
    app: TestClient,
) -> None:
    sign_in(app, Role.ADMIN)
    app.post("/api/tags", json={"name": "still-here"})

    refused = app.post(
        "/api/backup/restore", files={"file": ("holiday.mp4", b"\x00 not a database", "video/mp4")}
    )

    assert refused.status_code == 422
    assert [tag["name"] for tag in app.get("/api/tags").json()["items"]] == ["still-here"]


def test_saving_a_schedule_reports_it_back_and_reading_it_agrees(app: TestClient) -> None:
    sign_in(app, Role.ADMIN)

    saved = app.put(
        "/api/backup/schedule",
        json={"every_days": 14, "at": "4:30", "keep": 3, "folder": ""},
    )

    assert saved.status_code == 200
    assert saved.json() == {
        "every_days": 14,
        "at": "04:30",
        "keep": 3,
        "keep_days": 7,
        "folder": "",
        "beside_sift_data": True,
        "working": None,
    }
    assert app.get("/api/backup/schedule").json() == saved.json()

    # How many days, saved with the count, and kept when left out by an older client.
    days = app.put("/api/backup/schedule", json={"keep": 3, "keep_days": 0, "folder": ""}).json()
    assert days["keep_days"] == 0
    assert app.put("/api/backup/schedule", json={"keep": 3, "folder": ""}).json()["keep_days"] == 0

    # Left out, How often and the time of day keep what is stored: the Backup pane does not draw
    # them, and its save must not put back what it read on arrival.
    again = app.put("/api/backup/schedule", json={"keep": 5, "folder": ""}).json()
    assert (again["every_days"], again["at"], again["keep"]) == (14, "04:30", 5)


def test_a_schedule_naming_a_folder_sift_cannot_reach_is_refused_and_nothing_is_saved(
    app: TestClient,
) -> None:
    """Told now, on the screen, rather than by a job failing every night from here on."""
    sign_in(app, Role.ADMIN)
    when = app.put("/api/settings", json={"values": {"tasks.backup.when": "work"}})
    assert when.status_code == 204
    refused = app.put("/api/backup/schedule", json={"every_days": 3, "keep": 3, "folder": "/etc"})

    assert refused.status_code == 409
    assert app.get("/api/backup/schedule").json()["every_days"] == 1


def test_a_schedule_that_is_not_one_is_refused(app: TestClient) -> None:
    sign_in(app, Role.ADMIN)

    assert (
        app.put(
            "/api/backup/schedule", json={"schedule": "daily", "keep": 3, "folder": ""}
        ).status_code
        == 422
    ), "the retired word is not a field of the request"
    for body in (
        {"keep": 0, "folder": ""},
        {"every_days": 0, "keep": 3, "folder": ""},
        {"every_days": 366, "keep": 3, "folder": ""},
        {"at": "25:00", "keep": 3, "folder": ""},
        {"keep": 3, "keep_days": -1, "folder": ""},
        {"keep": 3, "keep_days": 3651, "folder": ""},
    ):
        assert app.put("/api/backup/schedule", json=body).status_code == 422, body


def test_turning_the_schedule_on_queues_the_first_backup(app: TestClient) -> None:
    """Saving the setting is not the feature. A backup actually being due is.

    The task's When decides whether it starts on its own and the schedule says how often, so a
    daily schedule under the default When (only when pressed) queues nothing, and the same save
    once the When is its schedule queues the first run.
    """
    sign_in(app, Role.ADMIN)
    daily = {"every_days": 1, "keep": 3, "folder": ""}

    # `type`, which is what the route takes. Written as `job_type` (the queue's own word for it),
    # this would filter nothing and count every job there was.
    assert app.put("/api/backup/schedule", json=daily).status_code == 200
    assert app.get("/api/jobs", params={"type": "backup_run"}).json()["total"] == 0

    saved = app.put("/api/settings", json={"values": {"tasks.backup.when": "work"}})
    assert saved.status_code == 204
    assert app.put("/api/backup/schedule", json=daily).status_code == 200
    queued = app.get("/api/jobs", params={"type": "backup_run"}).json()
    assert queued["total"] == 1


def test_a_folder_that_could_never_be_one_is_refused_by_the_settings_it_would_be_stored_in(
    app: TestClient,
) -> None:
    """A relative folder is refused even while only a press runs it, where nothing resolves it.

    It would be read against wherever Sift happened to be started from, which is a different
    folder on a different day. The refusal comes from the preference declaration rather than from
    a second copy of the rule here, so the screen and this endpoint cannot come to disagree.
    """
    sign_in(app, Role.ADMIN)

    refused = app.put("/api/backup/schedule", json={"keep": 3, "folder": "backups"})

    assert refused.status_code == 422
    assert app.get("/api/backup/schedule").json()["folder"] == ""


def test_an_export_while_other_library_work_runs_is_a_conflict_and_leaves_nothing_staged(
    app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused, never waited for: somebody is waiting on the answer. The staged file it would
    have been written to goes with the refusal."""
    from sift.slices.backup.service import BackupService, Busy

    class Holding:
        """Another piece of library work, holding it."""

        async def __aenter__(self) -> None:
            raise Busy("A restore is running. Try again when it has finished.")

        async def __aexit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(BackupService, "exclusively", lambda _self, _what: Holding())
    sign_in(app, Role.ADMIN)

    answer = app.post("/api/backup/export")

    assert answer.status_code == 409
    assert "restore is running" in answer.json()["detail"]
    assert list((get_settings().data_dir / "backup-staging").glob("*")) == []


def test_the_unmarked_backups_are_listed_and_one_is_deleted_by_its_name(
    app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The list names the backups no rule deletes; a Delete takes the one named and answers the
    list as it stands. A name the list does not hold is a 404 and deletes nothing."""
    from sift.slices.backup import recycle

    monkeypatch.setattr(recycle, "has_recycle_bin", lambda _place: False)
    sign_in(app, Role.ADMIN)
    folder = get_settings().data_dir / "backups"
    folder.mkdir(parents=True)
    old = folder / "sift-backup-20260720-141500-1.0.0.zip"
    old.write_bytes(b"x" * 2048)
    stranger = folder / "holiday.zip"
    stranger.write_bytes(b"not a backup")

    listed = app.get("/api/backup/unmarked").json()
    assert listed == {
        "backups": [{"name": old.name, "taken_at": 1784556900, "size_bytes": 2048, "saved": False}],
        "recycle_bin": False,
    }

    assert app.delete(f"/api/backup/unmarked/{stranger.name}").status_code == 404
    assert stranger.is_file()

    answered = app.delete(f"/api/backup/unmarked/{old.name}")
    assert answered.status_code == 200
    assert answered.json() == {"backups": [], "recycle_bin": False}
    assert not old.exists()
