# SPDX-License-Identifier: AGPL-3.0-or-later
"""Migrate from Stash reads the database FILE chosen, never a folder: several can sit in one.

Stash's `config.yml` chosen stands for the database it names beside it, or `stash-go.sqlite`.
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


def _signed_in(client: TestClient) -> Path:
    database = client.app.state.database.path  # type: ignore[attr-defined]
    _user, token, csrf = establish_session(
        database, role="admin", username="stash-admin", password="A-Stash-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return Path(database)


def test_the_file_chosen_is_read_and_a_folder_is_refused(app: FastAPI, tmp_path: Path) -> None:
    given = tmp_path / "given"
    make_stash(given / "stash-go.sqlite")
    make_stash(given / "stash-go.sqlite.20260101_120000")
    with TestClient(app) as client:
        database = _signed_in(client)
        execute_blocking(
            database,
            "INSERT INTO browse_grants (id, abs_path, granted_at) VALUES (?, ?, ?)",
            (new_id(), str(given), 1_700_000_000),
        )

        older = client.post(
            "/api/stash-migration/read",
            json={"path": str(given / "stash-go.sqlite.20260101_120000")},
        )
        assert older.status_code == 200, older.text
        assert older.json()["source"] == str((given / "stash-go.sqlite.20260101_120000").resolve())

        folder = client.post("/api/stash-migration/read", json={"path": str(given)})
        assert folder.status_code == 422
        assert "Choose Stash's database file" in folder.text


def test_a_config_chosen_stands_for_the_database_it_names(app: FastAPI, tmp_path: Path) -> None:
    given = tmp_path / "given"
    make_stash(given / "library.sqlite")
    (given / "config.yml").write_text("database: library.sqlite\n", encoding="utf-8")
    (tmp_path / "empty").mkdir()
    (tmp_path / "empty" / "config.yml").write_text("port: 9999\n", encoding="utf-8")
    with TestClient(app) as client:
        database = _signed_in(client)
        execute_blocking(
            database,
            "INSERT INTO browse_grants (id, abs_path, granted_at) VALUES (?, ?, ?)",
            (new_id(), str(tmp_path), 1_700_000_000),
        )

        read = client.post("/api/stash-migration/read", json={"path": str(given / "config.yml")})
        assert read.status_code == 200, read.text
        assert read.json()["source"] == str((given / "library.sqlite").resolve())

        none = client.post(
            "/api/stash-migration/read", json={"path": str(tmp_path / "empty" / "config.yml")}
        )
        assert none.status_code == 422
        assert "names no Stash database" in none.text


def test_a_database_in_a_library_folder_is_read_without_a_grant(
    app: FastAPI, tmp_path: Path
) -> None:
    """A folder added by its typed path is a library folder, not a grant: still one Sift has."""
    library = tmp_path / "library"
    make_stash(library / "stash-go.sqlite")
    with TestClient(app) as client:
        database = _signed_in(client)
        execute_blocking(
            database,
            "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
            (new_id(), "library", str(library), 1_700_000_000),
        )

        read = client.post(
            "/api/stash-migration/read", json={"path": str(library / "stash-go.sqlite")}
        )
        assert read.status_code == 200, read.text

        outside = tmp_path / "outside"
        make_stash(outside / "stash-go.sqlite")
        refused = client.post(
            "/api/stash-migration/read", json={"path": str(outside / "stash-go.sqlite")}
        )
        assert refused.status_code == 422
        assert "Browse this device" in refused.text
