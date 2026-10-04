# SPDX-License-Identifier: AGPL-3.0-or-later
"""Migrate from Stash reads a database named by its file or by Stash's folder.

A browser can pick only folders, so a folder named is Stash's own: the database is the one its
`config.yml` names, or `stash-go.sqlite`, found inside that folder by name and never by listing it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.db import execute_blocking
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.main import create_app
from sift.slices.stash_migration.service import _database_in
from sift.slices.stash_migration.tests.stash_fixture import make_stash
from sift.testing.auth import establish_session

pytestmark = [pytest.mark.integration]


def test_the_default_name_is_found_in_the_folder(tmp_path: Path) -> None:
    make_stash(tmp_path / "stash-go.sqlite")
    assert _database_in(tmp_path) == (tmp_path / "stash-go.sqlite").resolve()


def test_the_config_names_the_database(tmp_path: Path) -> None:
    make_stash(tmp_path / "library.sqlite")
    (tmp_path / "config.yml").write_text("port: 9999\ndatabase: library.sqlite\n", encoding="utf-8")
    assert _database_in(tmp_path) == (tmp_path / "library.sqlite").resolve()


def test_a_path_from_a_container_is_taken_by_its_name(tmp_path: Path) -> None:
    """Stash in a container writes its own path (`/root/.stash/...`): the file beside the config
    with that name is the one meant."""
    make_stash(tmp_path / "mine.sqlite")
    (tmp_path / "config.yml").write_text('database: "/root/.stash/mine.sqlite"\n', encoding="utf-8")
    assert _database_in(tmp_path) == (tmp_path / "mine.sqlite").resolve()


def test_nothing_outside_the_folder_is_answered(tmp_path: Path) -> None:
    folder = tmp_path / "stash"
    folder.mkdir()
    make_stash(tmp_path / "elsewhere.sqlite")
    (folder / "config.yml").write_text("database: ../elsewhere.sqlite\n", encoding="utf-8")
    assert _database_in(folder) is None


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


def test_a_folder_picked_in_a_browser_is_read(app: FastAPI, tmp_path: Path) -> None:
    given = tmp_path / "given"
    make_stash(given / "stash-go.sqlite")
    empty = given / "nothing-here"
    empty.mkdir()
    with TestClient(app) as client:
        database = client.app.state.database.path  # type: ignore[attr-defined]
        _user, token, csrf = establish_session(
            database, role="admin", username="stash-admin", password="A-Stash-Test-Passw0rd!"
        )
        client.cookies.set(SESSION_COOKIE_NAME, token)
        client.headers[CSRF_HEADER_NAME] = csrf
        execute_blocking(
            database,
            "INSERT INTO browse_grants (id, abs_path, granted_at) VALUES (?, ?, ?)",
            (new_id(), str(given), 1_700_000_000),
        )

        read = client.post("/api/stash-migration/read", json={"path": str(given)})
        assert read.status_code == 200, read.text
        assert read.json()["summary"]["scenes"] > 0

        refused = client.post("/api/stash-migration/read", json={"path": str(empty)})
        assert refused.status_code == 422
        assert "no Stash database in that folder" in refused.text
