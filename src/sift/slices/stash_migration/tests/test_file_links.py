# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stash-box ids Stash kept on its scenes become the files' answers: on the scenes matched by
a run, and on a waiting scene once its file arrives."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from sift.slices.stash_migration.ports import Linked
from sift.slices.stash_migration.reader import BoxId
from sift.slices.stash_migration.service import StashMigration
from sift.slices.stash_migration.tally import Tally
from sift.slices.stash_migration.waiting import Waiting

BOX = "https://stash-box.example/graphql"


class _Doors:
    def __init__(self) -> None:
        self.linked: list[tuple[str, str, str]] = []

    async def link_file(
        self, asset_id: str, endpoint: str, remote_id: str, master_key: bytes | None
    ) -> Linked:
        self.linked.append((asset_id, endpoint, remote_id))
        return Linked.ALREADY if remote_id == "settled" else Linked.LINKED


def _migration() -> tuple[StashMigration, _Doors]:
    migration = StashMigration.__new__(StashMigration)
    doors = _Doors()
    migration.doors = doors  # type: ignore[assignment]
    return migration, doors


def _context() -> Any:
    async def master_key() -> bytes:
        return b"key"

    return SimpleNamespace(master_key=master_key, stopping=lambda: None)


def test_each_matched_scene_s_ids_are_linked_and_counted_by_how_they_went() -> None:
    migration, doors = _migration()
    stashed = SimpleNamespace(
        box_ids={
            "scene": [
                BoxId(1, BOX, "remote-scene"),
                BoxId(1, BOX, "settled"),
                BoxId(2, BOX, "not-here"),
            ]
        }
    )
    tally = Tally()

    asyncio.run(migration._file_links(_context(), stashed, tally, {1: "01FILE"}))  # type: ignore[arg-type]

    assert doors.linked == [("01FILE", BOX, "remote-scene"), ("01FILE", BOX, "settled")]
    assert tally.file_links == {"linked": 1, "already": 1}


def test_a_waiting_scene_s_ids_are_linked_when_its_file_lands() -> None:
    migration, doors = _migration()

    async def nothing(*args: Any, **kwargs: Any) -> None:
        return None

    migration._write_item = nothing  # type: ignore[method-assign]
    row = Waiting(
        id="01ROW",
        kind="scene",
        stash_id=3,
        read_at=0,
        user_id=None,
        label="later.mp4",
        package={"fields": {}, "box_ids": [[BOX, "remote-later"]]},
    )
    tally = Tally()

    asyncio.run(migration._land_one(row, "01LATE", None, b"key", tally))  # type: ignore[arg-type]

    assert doors.linked == [("01LATE", BOX, "remote-later")]
    assert tally.file_links == {"linked": 1}
