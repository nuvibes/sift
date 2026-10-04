# SPDX-License-Identifier: AGPL-3.0-or-later
"""The capture endpoints, over a real application: what each way in accepts and refuses.

The app is the real one, booted with real workers, and a signed-in admin drives it: the same path
a request takes in production. Who may call these at all is proved by the authorization matrix; what
is here is what an admin gets back for each shape of request.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.main import create_app
from sift.testing.auth import establish_session
from sift.testing.library import seed_root

from .conftest import CORPUS

pytestmark = [pytest.mark.integration]

A_ROOT = "01HX0000000000000000000010"
A_FOLDER = "01HX0000000000000000000011"
NO_SUCH_FOLDER = "01HX0000000000000000000099"


class FakeDownloader:
    """Stands in for the downloader, which is a separate feature and may not be present.

    A double here is honest: capture's contract with the downloader is a single call, and what the
    downloader then does with a URL is proved where the downloader lives. This records that it was
    called, and with what, and hands back a row id to watch.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        #: Who each link was captured by, so its landing is recorded as theirs.
        self.requested_by: list[str | None] = []

    async def submit_url(
        self, *, url: str, dest_folder_id: str | None, requested_by: str | None = None
    ) -> str:
        self.calls.append((url, dest_folder_id))
        self.requested_by.append(requested_by)
        return "01HX0000000000000000000042"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI, tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(app) as active:
        db_path = active.app.state.database.path  # type: ignore[attr-defined]
        library = tmp_path / "library"
        library.mkdir(exist_ok=True)
        seed_root(db_path, A_ROOT, folder_id=A_FOLDER, path=library)
        yield active


def sign_in(client: TestClient) -> str:
    """Sign in as the capturing admin, and answer with their user id."""
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    user_id, token, csrf = establish_session(
        db_path, role="admin", username="capture-admin", password="Capture-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


def _media() -> bytes:
    return (CORPUS / "accepted.png").read_bytes()


# --- uploads ------------------------------------------------------------------------------------


def test_uploading_a_file_is_accepted_and_returns_a_job_to_watch(client: TestClient) -> None:
    sign_in(client)
    response = client.post(
        "/api/capture/import/file",
        files={"file": ("clip.png", _media(), "image/png")},
        data={"dest_folder_id": A_FOLDER},
    )
    assert response.status_code == 202, response.text
    assert response.json()["job_id"]


def test_a_screenshot_of_something_that_is_not_a_file_id_is_refused(client: TestClient) -> None:
    sign_in(client)
    response = client.post(
        "/api/capture/import/file",
        files={"file": ("frame.png", _media(), "image/png")},
        data={"dest_folder_id": A_FOLDER, "screenshot_of": "../elsewhere"},
    )
    assert response.status_code == 400, response.text


def test_a_screenshot_upload_naming_the_file_it_was_taken_of_is_accepted(
    client: TestClient,
) -> None:
    sign_in(client)
    response = client.post(
        "/api/capture/import/file",
        files={"file": ("frame.png", _media(), "image/png")},
        data={"dest_folder_id": A_FOLDER, "screenshot_of": "01HX0000000000000000000042"},
    )
    assert response.status_code == 202, response.text


def test_uploading_into_a_folder_that_is_not_there_is_refused(client: TestClient) -> None:
    sign_in(client)
    response = client.post(
        "/api/capture/import/file",
        files={"file": ("clip.png", _media(), "image/png")},
        data={"dest_folder_id": NO_SUCH_FOLDER},
    )
    assert response.status_code == 400, response.text


# --- links --------------------------------------------------------------------------------------


def test_a_link_is_handed_to_the_downloader(client: TestClient) -> None:
    downloader = FakeDownloader()
    client.app.state.downloads = downloader  # type: ignore[attr-defined]
    user_id = sign_in(client)

    response = client.post(
        "/api/capture/import/url",
        json={"url": "https://example.com/a/photo", "dest_folder_id": A_FOLDER},
    )
    assert response.status_code == 202, response.text
    assert response.json()["download_id"] == "01HX0000000000000000000042"
    assert downloader.calls == [("https://example.com/a/photo", A_FOLDER)]
    assert downloader.requested_by == [user_id]


def test_a_link_when_the_downloader_is_not_wired_is_unavailable(client: TestClient) -> None:
    # Boot wires a downloader onto app.state; clear it to stand in for one that failed to start.
    client.app.state.downloads = None  # type: ignore[attr-defined]
    sign_in(client)
    response = client.post(
        "/api/capture/import/url",
        json={"url": "https://example.com/a/photo", "dest_folder_id": A_FOLDER},
    )
    assert response.status_code == 503, response.text


def test_a_link_sift_cannot_fetch_is_refused(client: TestClient) -> None:
    client.app.state.downloads = FakeDownloader()  # type: ignore[attr-defined]
    sign_in(client)
    response = client.post(
        "/api/capture/import/url",
        json={"url": "blob:https://example.com/abc", "dest_folder_id": A_FOLDER},
    )
    assert response.status_code == 400, response.text


def test_a_link_into_a_folder_that_is_not_there_is_refused(client: TestClient) -> None:
    client.app.state.downloads = FakeDownloader()  # type: ignore[attr-defined]
    sign_in(client)
    response = client.post(
        "/api/capture/import/url",
        json={"url": "https://example.com/a/photo", "dest_folder_id": NO_SUCH_FOLDER},
    )
    assert response.status_code == 400, response.text


# --- clipboard ----------------------------------------------------------------------------------


def test_clipboard_prefers_a_link_over_pasted_bytes(client: TestClient) -> None:
    downloader = FakeDownloader()
    client.app.state.downloads = downloader  # type: ignore[attr-defined]
    user_id = sign_in(client)

    response = client.post(
        "/api/capture/import/clipboard",
        data={"url": "https://example.com/a/photo", "dest_folder_id": A_FOLDER},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["download_id"] == "01HX0000000000000000000042"
    assert body["job_id"] is None
    assert downloader.calls == [("https://example.com/a/photo", A_FOLDER)]
    assert downloader.requested_by == [user_id]


def test_clipboard_imports_pasted_bytes_when_there_is_no_usable_link(client: TestClient) -> None:
    sign_in(client)
    response = client.post(
        "/api/capture/import/clipboard",
        files={"file": ("pasted.png", _media(), "image/png")},
        data={"dest_folder_id": A_FOLDER, "origin": "paste"},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["job_id"]
    assert body["download_id"] is None


def test_clipboard_bytes_into_a_folder_that_is_not_there_is_refused(client: TestClient) -> None:
    sign_in(client)
    response = client.post(
        "/api/capture/import/clipboard",
        files={"file": ("pasted.png", _media(), "image/png")},
        data={"dest_folder_id": NO_SUCH_FOLDER},
    )
    assert response.status_code == 400, response.text


def test_clipboard_with_nothing_to_add_is_refused(client: TestClient) -> None:
    sign_in(client)
    response = client.post("/api/capture/import/clipboard", data={})
    assert response.status_code == 400, response.text


def test_a_pasted_link_is_bounded(client: TestClient) -> None:
    """Without a maximum this route, which takes whatever was pasted or dragged, would carry a
    string of any size as far as the resolver."""
    sign_in(client)

    refused = client.post(
        "/api/capture/import/url",
        json={"url": "https://example.test/" + "a" * 5000},
    )

    assert refused.status_code == 422
