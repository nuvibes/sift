# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which library folders Sift may write in, asked of the disk when a chooser reads the list."""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.main import create_app
from sift.slices.library_roots.router import _REACHABLE_TIMEOUT
from sift.testing.auth import establish_session

pytestmark = [pytest.mark.integration]

FOLDERS = "/api/library/folders"
ROUTER = "sift.slices.library_roots.router"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    app: FastAPI = create_app()
    with TestClient(app) as c:
        db_path = c.app.state.database.path  # type: ignore[attr-defined]
        _, token, csrf = establish_session(
            db_path, role="admin", username="library-admin", password="Library-Test-Passw0rd!"
        )
        c.cookies.set(SESSION_COOKIE_NAME, token)
        c.headers[CSRF_HEADER_NAME] = csrf
        yield c
    get_settings.cache_clear()


def libraries(client: TestClient, tmp_path: Path, *names: str) -> None:
    for name in names:
        (tmp_path / name).mkdir()
        added = client.post("/api/library/roots", json={"abs_path": str(tmp_path / name)})
        assert added.status_code == 201, added.text


def test_a_chooser_is_told_which_folders_sift_may_write_in(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    libraries(client, tmp_path, "open", "shut")
    monkeypatch.setattr(sys.modules[ROUTER], "is_writable", lambda path: path.name != "shut")

    asked = client.get(FOLDERS, params={"writable": "true"}).json()["folders"]

    assert {folder["name"]: folder["writable"] for folder in asked} == {"open": True, "shut": False}


def test_the_tree_does_not_ask_the_disk(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    libraries(client, tmp_path, "open")

    def refused(_path: Path) -> bool:
        raise AssertionError("the tree asked the disk")

    monkeypatch.setattr(sys.modules[ROUTER], "is_writable", refused)

    assert [folder["writable"] for folder in client.get(FOLDERS).json()["folders"]] == [None]


def test_a_library_that_does_not_answer_reads_as_not_writable_without_holding_the_list(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    libraries(client, tmp_path, "silent")

    def slowly(_path: Path) -> bool:
        time.sleep(_REACHABLE_TIMEOUT * 2)
        return True

    monkeypatch.setattr(sys.modules[ROUTER], "is_writable", slowly)
    started = time.monotonic()

    asked = client.get(FOLDERS, params={"writable": "true"}).json()["folders"]

    assert [folder["writable"] for folder in asked] == [False]
    assert time.monotonic() - started < _REACHABLE_TIMEOUT * 2
