# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a run leaves as a person left it: their own rating on a file, and a Collection they
deleted."""

from __future__ import annotations

import asyncio
from typing import Any

from sift.kernel.content.user_state import AssetUserState
from sift.slices.stash_migration.reader import Item
from sift.slices.stash_migration.service import StashMigration
from sift.slices.stash_migration.tally import Tally
from sift.slices.stash_migration.waiting import KeptGroup


class _Ratings:
    def __init__(self, rating: int | None) -> None:
        self.rating = rating
        self.set: list[int] = []

    async def state_of(self, asset_id: str, user_id: str) -> AssetUserState:
        return AssetUserState(asset_id=asset_id, user_id=user_id, rating=self.rating)

    async def set_rating(self, asset_id: str, user_id: str, rating: int) -> None:
        self.set.append(rating)

    async def carry_counts(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def record_watch_time(self, *args: Any, **kwargs: Any) -> None:
        return None


def _rated(held: int | None) -> tuple[list[int], Tally]:
    migration = StashMigration.__new__(StashMigration)
    ratings = _Ratings(held)
    migration._user_state = ratings  # type: ignore[assignment]

    async def merge(*args: Any) -> None:
        return None

    migration._merge = merge  # type: ignore[method-assign, assignment]
    tally = Tally()
    item = Item(stash_id=1, kind="scene", files=(), fields={}, rating=8)
    asyncio.run(migration._write_item(item, "01FILE", None, "01USER", tally))  # type: ignore[arg-type]
    return ratings.set, tally


def test_a_rating_fills_an_empty_one_and_never_replaces_the_persons_own() -> None:
    assert _rated(None) == ([8], Tally(ratings=1))
    assert _rated(3) == ([], Tally())


class _Kept:
    def __init__(self) -> None:
        self.held: KeptGroup | None = None

    async def group(self, source: str, stash_id: int) -> KeptGroup | None:
        return self.held

    async def keep_group(self, source: str, stash_id: int, kept: KeptGroup) -> None:
        self.held = kept


class _Collections:
    def __init__(self) -> None:
        self.made = 0
        self.there = True

    async def make_collection(self, name: str, owner_id: str, *, actor: object) -> str:
        self.made += 1
        return f"01COLLECTION{self.made}"

    async def add_to_collection(self, collection_id: str, asset_ids: object, *, actor: object):  # type: ignore[no-untyped-def]
        return 1 if self.there else None


def test_a_collection_somebody_deleted_is_not_made_again() -> None:
    """Made once; once deleted, a scene that arrives later for its group makes no second one."""
    migration = StashMigration.__new__(StashMigration)
    kept = _Kept()
    doors = _Collections()
    migration._waiting = kept  # type: ignore[assignment]
    migration.doors = doors  # type: ignore[assignment]

    def grow(*scenes: str) -> Tally:
        tally = Tally()
        asyncio.run(
            migration._grow_group("stash", 7, "Lakeside", scenes, None, "01USER", tally)  # type: ignore[arg-type]
        )
        return tally

    assert grow("01A", "01B").collections == 1
    assert kept.held == KeptGroup("Lakeside", "01COLLECTION1", ("01A", "01B"))
    doors.there = False
    grow("01C")
    assert kept.held == KeptGroup("Lakeside", None, ("01A", "01B", "01C"))
    assert grow("01D").collections == 0
    assert doors.made == 1
    assert kept.held == KeptGroup("Lakeside", None, ("01A", "01B", "01C", "01D"))
