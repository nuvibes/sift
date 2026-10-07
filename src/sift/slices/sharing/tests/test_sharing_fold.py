# SPDX-License-Identifier: AGPL-3.0-or-later
"""A share too large to count while its press waits: the press answers with the files visible,
and its counts follow, folded after it; a fold that fails leaves them for the boot."""

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
from sift.slices.sharing import SharingService
from sift.testing.fixture_library import FixtureLibrary, fixture_library


@pytest.fixture
async def library(tmp_path: Path) -> AsyncIterator[tuple[FixtureLibrary, Database, SharingService]]:
    lib = await fixture_library(6_000, 7, tmp_path / "library.sqlite3")
    database = Database(lib.path, readers=1)
    await database.connect()
    await database.initialize_schema()
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    service = SharingService(database, Repository(database, ContentStore(database, settings)))
    try:
        yield lib, database, service
    finally:
        await _folded(service)
        await database.close()


async def _folded(service: SharingService) -> None:
    """The fold under way, if one is, finished."""
    if service._folding is not None:
        await service._folding


async def _owed(database: Database) -> int:
    row = await database.fetch_one("SELECT COUNT(*) AS n FROM visibility_owed")
    assert row is not None
    return int(row["n"])


async def _unseen_root(database: Database, guest: str) -> str:
    row = await database.fetch_one(
        "SELECT l.root_id, COUNT(DISTINCT l.asset_id) AS n FROM asset_locations l"
        " WHERE NOT EXISTS (SELECT 1 FROM viewer_assets v WHERE v.user_id = ?"
        " AND v.asset_id = l.asset_id) GROUP BY l.root_id ORDER BY n DESC LIMIT 1",
        (guest,),
    )
    assert row is not None
    return str(row["root_id"])


async def test_a_large_share_answers_and_its_counts_follow(
    library: tuple[FixtureLibrary, Database, SharingService],
) -> None:
    lib, database, service = library
    admin = Viewer(id=lib.admin, role=Role.ADMIN)
    guest = lib.guests[-1]
    root = await _unseen_root(database, guest)
    await _folded(service)
    await service.share(admin, ObjectType.ROOT, root, guest, Effect.SHARE)
    await _folded(service)
    assert await _owed(database) == 0
    async with database.write() as connection:
        assert await visibility.differences(connection) == []
    # Taken back: the same, the other way.
    await service.revoke(admin, ObjectType.ROOT, root, guest, Effect.SHARE)
    await _folded(service)
    assert await _owed(database) == 0


async def test_a_fold_that_fails_leaves_the_counts_owed(
    library: tuple[FixtureLibrary, Database, SharingService], monkeypatch: pytest.MonkeyPatch
) -> None:
    lib, database, service = library

    async def refused(*_args: object) -> object:
        raise RuntimeError("the disk went away")

    monkeypatch.setattr(visibility_settled, "fold_owed", refused)
    root = await _unseen_root(database, lib.guests[-1])
    admin = Viewer(id=lib.admin, role=Role.ADMIN)
    await service.share(admin, ObjectType.ROOT, root, lib.guests[-1], Effect.SHARE)
    await _folded(service)
    assert await _owed(database) > 0
