# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two endpoints over HTTP against a real application: who is refused and what a screen
receives. Nothing reaches a network."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.main import create_app
from sift.slices.update_notify.service import Release
from sift.testing.auth import establish_session

pytestmark = pytest.mark.integration

PASSWORD = "An-Update-Test-Passw0rd!"
NEWER = "99.0.0"
NOTES = "Faster thumbnails.\n\nFixes a crash when a folder disappears mid-scan."


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
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


def sign_in(client: TestClient, role: str) -> str:
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"update-{role}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


def publish(client: TestClient, release: Release | None) -> None:
    """Set what the last run of the check found; the route never fetches itself."""

    async def feed() -> Release | None:
        return release

    client.app.state.update_notify._fetch = feed  # type: ignore[attr-defined]
    client.app.state.update_notify._last_checked = 0  # type: ignore[attr-defined]
    client.app.state.update_notify._known = release  # type: ignore[attr-defined]


def test_the_check_surfaces_the_release_notes_and_its_page(client: TestClient) -> None:
    """What the Updates screen draws: the version, the notes, and the one page they may link to.
    There is no command to paste: installing is the desktop application's, on a press."""
    sign_in(client, "admin")
    page = "https://releases.example/sift/tag/v99.0.0"
    publish(client, Release(version=NEWER, notes=NOTES, page=page))

    body = client.get("/api/update/check").json()

    assert body["update_available"] is True
    assert body["latest_version"] == NEWER
    assert body["notes"] == NOTES
    assert body["release_page"] == page
    assert "command" not in body


def test_with_no_network_the_check_answers_and_nothing_is_blocked(client: TestClient) -> None:
    """Offline is the ordinary case, not the error case. Sift is usable with no outbound network at
    all, so a check that cannot happen must produce an answer, not a failure."""
    sign_in(client, "admin")
    publish(client, None)

    response = client.get("/api/update/check")

    assert response.status_code == 200
    body = response.json()
    assert body["update_available"] is False
    assert body["latest_version"] is None
    assert body["notes"] == ""

    # And the rest of the application is untouched by it.
    assert client.get("/health").status_code == 200
    assert client.get("/api/settings").status_code == 200


def test_repeated_checks_do_not_repeat_the_request(client: TestClient) -> None:
    """A screen that polls must not turn into a request against a public endpoint at all: the
    route reads what the task last found, and only the task fetches."""
    sign_in(client, "admin")
    calls = []

    async def feed() -> Release | None:
        calls.append(1)
        return None

    client.app.state.update_notify._fetch = feed  # type: ignore[attr-defined]
    client.app.state.update_notify._last_checked = 0  # type: ignore[attr-defined]

    for _ in range(10):
        assert client.get("/api/update/check").status_code == 200

    assert calls == []


def test_a_guest_may_not_check(client: TestClient) -> None:
    """Reading is admin-only because it is what makes the server open an outbound connection."""
    sign_in(client, "guest")

    assert client.get("/api/update/check").status_code == 403


def test_a_stranger_may_not_check(client: TestClient) -> None:
    assert client.get("/api/update/check").status_code == 401


def test_dismissing_hides_the_notice_for_that_version_only(client: TestClient) -> None:
    sign_in(client, "admin")
    publish(client, Release(version=NEWER, notes=NOTES))

    assert client.post("/api/update/dismiss", json={"version": NEWER}).status_code == 204

    body = client.get("/api/update/check").json()
    assert body["dismissed"] is True
    # Dismissed is not gone: the Updates section still says what is available.
    assert body["update_available"] is True
    assert body["latest_version"] == NEWER

    publish(client, Release(version="99.1.0", notes=NOTES))
    assert client.get("/api/update/check").json()["dismissed"] is False


def test_a_guest_may_not_dismiss(client: TestClient) -> None:
    sign_in(client, "guest")

    assert client.post("/api/update/dismiss", json={"version": NEWER}).status_code == 403


def test_dismissing_needs_a_version(client: TestClient) -> None:
    sign_in(client, "admin")

    assert client.post("/api/update/dismiss", json={"version": ""}).status_code == 422
    assert client.post("/api/update/dismiss", json={}).status_code == 422


def test_the_running_version_is_readable_by_any_account(client: TestClient) -> None:
    """Any account may read the running version; it makes no outbound request."""
    sign_in(client, "guest")

    answer = client.get("/api/update/version")

    assert answer.status_code == 200
    # A string either way. An install reports its version; a source tree with nothing installed
    # reports the empty string rather than failing, because About has to draw something.
    assert isinstance(answer.json()["version"], str)


def test_a_stranger_is_not_told_which_version_is_running(client: TestClient) -> None:
    """A stranger is not told the running version, which names the flaws published against it."""
    assert client.get("/api/update/version").status_code == 401


def test_there_is_no_route_that_applies_an_update(client: TestClient) -> None:
    """No route applies, installs or upgrades anything: the backend only says what is out."""
    paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]
    update_paths = {path for path in paths if path.startswith("/api/update")}

    # `version` reads the installed package and does nothing else, so it is listed as safe.
    assert update_paths == {"/api/update/check", "/api/update/dismiss", "/api/update/version"}

    # Across the whole API, not only under /update: an apply button added elsewhere would be the
    # same mistake wearing a different address.
    for forbidden in ("upgrade", "self-update", "selfupdate", "reboot"):
        assert not any(forbidden in path for path in paths), forbidden

    # One named exception: restarting the backend fetches, unpacks and runs nothing (it exists for
    # the graphics card's second build of a loaded library). Its one argument is the computer's
    # name, a bounded label, so it cannot be steered into acting.
    restarts = {path for path in paths if "restart" in path}
    assert restarts == {"/api/performance/restart"}, restarts

    verbs = paths["/api/performance/restart"]
    assert set(verbs) == {"post"}, verbs
    parameters = verbs["post"].get("parameters") or []
    assert [(p["name"], p["in"]) for p in parameters] == [("device", "query")], parameters
    label = parameters[0]["schema"]["anyOf"]
    assert {"type": "string", "maxLength": 256} in label, "the name is a bounded label"
    assert {kind.get("type") for kind in label} <= {"string", "null"}, label
    assert not verbs["post"].get("requestBody"), "the restart takes a body"
