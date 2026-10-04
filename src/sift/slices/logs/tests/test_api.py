# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one endpoint: the end of the log, and who may read it."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.log import LOG_FILENAME
from sift.main import create_app
from sift.testing.auth import establish_session

pytestmark = pytest.mark.integration

PASSWORD = "A-Log-Test-Passw0rd!"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def app(data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as running:
        yield running


def db_path(client: TestClient) -> Path:
    return client.app.state.database.path  # type: ignore[attr-defined,no-any-return]


def sign_in(client: TestClient, role: str) -> None:
    _user, token, csrf = establish_session(
        db_path(client), role=role, username=f"logs-{role}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf


def write_log(data_dir: Path, lines: list[str]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / LOG_FILENAME).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_an_admin_reads_the_end_of_the_log_oldest_first(client: TestClient, data_dir: Path) -> None:
    """Oldest first, because that is the order it happened in and the order it will be read in."""
    sign_in(client, "admin")
    write_log(
        data_dir,
        [
            json.dumps({"timestamp": "2026-09-04T10:00:00Z", "level": "info", "event": "first"}),
            json.dumps({"timestamp": "2026-09-04T10:00:01Z", "level": "warning", "event": "then"}),
        ],
    )

    answer = client.get("/api/logs", params={"lines": 10})

    assert answer.status_code == 200
    found = answer.json()
    assert found["present"] is True
    assert [one["event"] for one in found["lines"]] == ["first", "then"]
    assert [one["level"] for one in found["lines"]] == ["info", "warning"]
    assert found["size_bytes"] > 0


def test_a_guest_is_refused(client: TestClient, data_dir: Path) -> None:
    """The control, not a courtesy. A log line carries whatever the line that wrote it passed
    (a library path, a file name) and a guest has no business reading the installation's own
    record of itself."""
    sign_in(client, "guest")
    write_log(data_dir, [json.dumps({"event": "something"})])

    assert client.get("/api/logs").status_code == 403


def test_signed_out_is_refused(client: TestClient, data_dir: Path) -> None:
    write_log(data_dir, [json.dumps({"event": "something"})])

    assert client.get("/api/logs").status_code == 401


def test_it_names_the_file_it_read_and_says_it_is_there(client: TestClient) -> None:
    """Where the log is, so somebody who wants the whole thing knows where to look.

    "An absent log says so" cannot be staged here and should not be: a
    RUNNING Sift has already written its boot lines by the time anything can ask, so `present` is
    always true through this route. The absent case is real (a data directory that has not been
    written to yet) and it is asserted where it can be, on `tail_of`.
    """
    sign_in(client, "admin")

    found = client.get("/api/logs").json()

    assert found["path"].endswith(LOG_FILENAME)
    assert found["present"] is True
    assert found["lines"], "a running application has written something about itself"


def test_a_line_that_is_not_one_of_sifts_keeps_its_text(client: TestClient, data_dir: Path) -> None:
    """A log holds whatever was written to it, including output from a library that knows nothing
    about Sift's format. A page that refused to draw because one line was odd would be useless
    exactly when it is wanted."""
    sign_in(client, "admin")
    planted = ["not json at all", json.dumps(["a list, not a record"])]
    write_log(data_dir, planted)

    lines = client.get("/api/logs").json()["lines"]

    # The app under test writes its own lines to this file as it runs (a held event loop on a
    # loaded machine is one), and they are not the question here.
    kept = [one for one in lines if one["raw"] in planted]
    assert [one["raw"] for one in kept] == planted
    assert all(one["event"] is None for one in kept)


def test_asking_for_more_lines_than_allowed_is_refused_rather_than_capped(
    client: TestClient, data_dir: Path
) -> None:
    """A 422 rather than a quiet clamp: a caller asking for ten thousand lines has misunderstood
    something, and being handed five hundred without being told is how that misunderstanding
    survives."""
    sign_in(client, "admin")
    write_log(data_dir, [json.dumps({"event": "one"})])

    assert client.get("/api/logs", params={"lines": 10_000}).status_code == 422
    assert client.get("/api/logs", params={"lines": 0}).status_code == 422


def test_a_downloaded_copy_hides_a_personal_path_and_a_secret_whatever_the_setting(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The log on disk may be written whole; the copy that leaves the machine never is. A record
    is redacted field by field, a line that is not one as text, and the order is kept."""
    from sift.kernel import log as log_module

    sign_in(client, "admin")
    monkeypatch.setattr(log_module, "_redact_personal", False)
    record = json.dumps(
        {
            "path": "C:\\Users\\someone\\Videos\\clip.mp4",
            "detail": "token=hunter22",
            "event": "file.opened",
            "level": "info",
            "timestamp": "2026-10-03T06:00:00Z",
        }
    )
    plain = "Traceback: /home/someone/Videos/clip.mp4 cookie=hunter22"

    answer = client.post("/api/logs/redacted", json={"lines": [record, plain]})

    assert answer.status_code == 200
    lines = answer.json()["lines"]
    assert len(lines) == 2
    for line in lines:
        assert "someone" not in line
        assert "hunter22" not in line
        assert "clip.mp4" in line
    assert json.loads(lines[0])["event"] == "file.opened"
    assert list(json.loads(lines[0])) == list(json.loads(record)), "fields keep their order"


def test_a_guest_cannot_have_a_copy_redacted(client: TestClient) -> None:
    sign_in(client, "guest")

    assert client.post("/api/logs/redacted", json={"lines": ["one"]}).status_code == 403
