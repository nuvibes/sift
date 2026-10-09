# SPDX-License-Identifier: AGPL-3.0-or-later
"""A large widening is filed after its press, a page at a time, so a guest sees less than was
granted until the last page and never more; a narrowing is decided in its own write, any size."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from sift.kernel.access import (
    Effect,
    ObjectType,
    Repository,
    Role,
    Viewer,
    visibility,
    visibility_settled,
)
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.sharing import SharingService
from sift.testing.fixture_library import FixtureLibrary, fixture_library

Library = tuple[FixtureLibrary, Database, SharingService, str, str]


@pytest.fixture
async def library(tmp_path: Path) -> AsyncIterator[Library]:
    """A root one guest sees none of, of more files than a widening decides in its press."""
    lib = await fixture_library(24_000, 7, tmp_path / "library.sqlite3")
    database = Database(lib.path, readers=1)
    await database.connect()
    await database.initialize_schema()
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    service = SharingService(database, Repository(database, ContentStore(database, settings)))
    guest = lib.guests[-1]
    row = await database.fetch_one(
        "SELECT l.root_id, COUNT(DISTINCT l.asset_id) AS n FROM asset_locations l"
        " WHERE NOT EXISTS (SELECT 1 FROM viewer_assets v WHERE v.user_id = ?"
        " AND v.asset_id = l.asset_id) GROUP BY l.root_id ORDER BY n DESC LIMIT 1",
        (guest,),
    )
    assert row is not None and row["n"] >= visibility_settled.DEFER_FROM
    try:
        yield lib, database, service, guest, str(row["root_id"])
    finally:
        if service._folding is not None:
            await service._folding
        await database.close()


async def _seen(database: Database, guest: str, root: str) -> int:
    row = await database.fetch_one(
        "SELECT COUNT(DISTINCT v.asset_id) AS n FROM viewer_assets v"
        " JOIN asset_locations l ON l.asset_id = v.asset_id WHERE v.user_id = ? AND l.root_id = ?",
        (guest, root),
    )
    assert row is not None
    return int(row["n"])


async def _rows_wrong(database: Database) -> set[str]:
    """How the stored rows stand against the facts: rows missing, rows extra, or neither."""
    async with database.write() as connection:
        found = await visibility.differences(connection)
    return {what for what, *_ in found if what in {"missing", "extra"}}


async def _filing(database: Database) -> int:
    row = await database.fetch_one("SELECT COUNT(*) AS n FROM visibility_filing")
    assert row is not None
    return int(row["n"])


def _held_back(service: SharingService, monkeypatch: pytest.MonkeyPatch) -> None:
    """The press answers and nothing follows it, so what a guest reads meanwhile can be asked."""

    async def nothing() -> None:
        return None

    monkeypatch.setattr(service, "_fold_soon", nothing)


async def test_a_guest_reads_only_what_is_filed_until_the_last_page(
    library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib, database, service, guest, root = library
    _held_back(service, monkeypatch)
    admin = Viewer(id=lib.admin, role=Role.ADMIN)
    before = await _seen(database, guest, root)
    await service.share(admin, ObjectType.ROOT, root, guest, Effect.SHARE)
    assert await _seen(database, guest, root) == before and await _filing(database) == 1
    assert await _rows_wrong(database) == {"missing"}, "less than granted, never more"

    assert await service._file_page()
    paged = await _seen(database, guest, root)
    assert before < paged <= before + visibility_settled.FILING_PAGE
    assert await _rows_wrong(database) == {"missing"}
    while await service._file_page():
        pass
    assert await _rows_wrong(database) == set() and await _filing(database) == 0
    while await service._fold_page():
        pass
    async with database.write() as connection:
        assert await visibility.differences(connection) == []


async def test_a_narrowing_is_decided_in_its_own_write_whatever_its_size(
    library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib, database, service, guest, root = library
    admin = Viewer(id=lib.admin, role=Role.ADMIN)
    before = await _seen(database, guest, root)
    await service.share(admin, ObjectType.ROOT, root, guest, Effect.SHARE)
    assert service._folding is not None
    await service._folding
    assert await _seen(database, guest, root) > before

    _held_back(service, monkeypatch)
    await service.revoke(admin, ObjectType.ROOT, root, guest, Effect.SHARE)
    assert await _seen(database, guest, root) == before and await _filing(database) == 0
    assert await _rows_wrong(database) == set()

    # A restrict pressed on a root already shown, and one written by hand while a press could
    # defer: each narrowing whole in its write.
    await service.share(admin, ObjectType.ROOT, root, guest, Effect.SHARE)
    while await service._file_page():
        pass
    await service.share(admin, ObjectType.ROOT, root, guest, Effect.RESTRICT)
    assert await _seen(database, guest, root) == 0 and await _filing(database) == 0
    assert await _rows_wrong(database) == set()
    await database.execute("DELETE FROM acl_grants WHERE subject_user_id = ?", (guest,))
    await database.execute(visibility_settled.MAY_DEFER)
    await database.execute(
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)"
        " SELECT ?, 'root', ?, ?, 'restrict', 0",
        (new_id(), root, guest),
    )
    assert await _seen(database, guest, root) == 0
    assert await _rows_wrong(database) == set() and await _filing(database) == 0


async def test_only_a_press_that_files_defers_and_a_stop_is_filed_at_boot(
    library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib, database, service, guest, root = library
    # Written by anything but the sharing press: decided in its own write.
    await database.execute(
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)"
        " VALUES (?, 'root', ?, ?, 'share', 0)",
        (new_id(), root, guest),
    )
    assert await _rows_wrong(database) == set()
    await database.execute("DELETE FROM acl_grants WHERE object_id = ?", (root,))

    # Pressed and stopped before a page: the boot files it whole and leaves nobody deferring.
    _held_back(service, monkeypatch)
    admin = Viewer(id=lib.admin, role=Role.ADMIN)
    await service.share(admin, ObjectType.ROOT, root, guest, Effect.SHARE)
    await database.execute(visibility_settled.MAY_DEFER)
    assert await _filing(database) == 1
    async with database.write() as connection:
        await visibility.keep_true(connection)
    assert await _rows_wrong(database) == set() and await _filing(database) == 0
    assert await database.fetch_one("SELECT 1 FROM visibility_may_defer") is None


async def test_a_library_at_version_nineteen_gains_the_filing_without_a_rebuild(
    temp_db: Database,
) -> None:
    await temp_db.initialize_schema()
    async with temp_db.write() as connection:
        await connection.execute("DROP TABLE visibility_filing")
        await connection.execute("DROP TABLE visibility_may_defer")
        await visibility.initialize(connection, 19)
        assert await visibility.differences(connection) == []
    assert await _filing(temp_db) == 0
    assert await temp_db.fetch_one("SELECT 1 FROM visibility_may_defer") is None
