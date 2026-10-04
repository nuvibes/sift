# SPDX-License-Identifier: AGPL-3.0-or-later
"""The whole Shoots pass on a real database, through every statement it uses; only the meaning
index is stood in for. Three near neighbours must not be swept in: somebody else's picture, one
already in a Photo Set, and a video.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from sift.kernel.db import Database
from sift.slices.shoots.jobs import look
from sift.slices.shoots.service import ShootService
from sift.slices.shoots.store import Store

pytestmark = [pytest.mark.integration]

_EPOCH = 1_700_000_000

#: Four named pictures and a widening; the live floor is held in `test_clustering`.
_FLOOR = 3

_PERSON = "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)"
_ASSET = """
INSERT INTO assets (id, identity, media_type, size_bytes, original_filename, added_at)
VALUES (?, ?, ?, 10, ?, ?)
"""
_NAMES = "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)"
_PHOTO_SET = "INSERT INTO photo_sets (id, name, created_at) VALUES (?, ?, ?)"
_IN_SET = """
INSERT INTO photo_set_items (photo_set_id, asset_id, position, added_at) VALUES (?, ?, ?, ?)
"""

#: The creator whose sitting this is, and the person one near picture carries instead.
_CREATOR = "Esme Wrenfield"
_SOMEBODY_ELSE = "Halla Nordquist"

#: The four pictures of the sitting that already carry the creator, in the order they are numbered.
_NAMED = ("shoot-1", "shoot-2", "shoot-3", "shoot-4")

#: Two more of the same sitting that carry nobody at all: the ordinary state of a file that
#: arrived without a caption, and the whole reason the widening read exists.
_UNNAMED = ("shoot-5", "shoot-6")

#: Near the seed and deliberately not part of the answer, one per condition of the statement.
_OTHER_PERSON = "near-other-person"
_ALREADY_FILED = "near-already-filed"
_A_VIDEO = "near-a-video"


class _Index:
    """The meaning index, standing in. Every neighbour of the seed, closest first."""

    def __init__(self, near: tuple[tuple[str, float], ...]) -> None:
        self._near = near
        self.asked: list[str] = []
        self.described: list[tuple[str, ...]] = []

    async def can_answer(self) -> bool:
        return True

    async def describe_many(self, asset_ids: Sequence[str]) -> dict[str, list[float]]:
        """The creator's four named pictures, one sitting: the same direction, a hair apart."""
        self.described.append(tuple(asset_ids))
        return {
            asset_id: [1.0, 0.01 * step, 0.0]
            for step, asset_id in enumerate(_NAMED)
            if asset_id in asset_ids
        }

    async def like_asset(
        self, asset_id: str, *, limit: int
    ) -> tuple[tuple[str, float], ...] | None:
        self.asked.append(asset_id)
        if asset_id != _NAMED[0]:
            # Only the seed leads a group. Everything else is claimed by it, and a real index
            # answering here would be answering a question the rule never asks.
            return ()
        return self._near


class _Settings:
    """The switch that files a shoot without asking. Off, which is how it ships."""

    async def get_app(self, key: str) -> object:
        return False


class _Context:
    """What the job hands the pass. Only the progress line is used."""

    def __init__(self) -> None:
        self.progress: float | None = None

    async def set_progress(self, value: float) -> None:
        self.progress = value


async def _library(tmp_path: Path) -> Database:
    """One sitting, plus the three near pictures the widening must leave alone."""
    database = Database(tmp_path / "shoots-pass.sqlite3")
    await database.connect()
    await database.initialize_schema()
    async with database.write() as connection:
        await connection.execute(_PERSON, ("creator", _CREATOR, _EPOCH))
        await connection.execute(_PERSON, ("other", _SOMEBODY_ELSE, _EPOCH))
        for position, asset_id in enumerate(_NAMED + _UNNAMED, start=1):
            await connection.execute(
                _ASSET,
                (asset_id, f"identity-{asset_id}", "image", f"sitting-{position:02d}.jpg", _EPOCH),
            )
        for asset_id in _NAMED:
            await connection.execute(_NAMES, (asset_id, "creator"))

        await connection.execute(
            _ASSET, (_OTHER_PERSON, "identity-other", "image", "sitting-07.jpg", _EPOCH)
        )
        await connection.execute(_NAMES, (_OTHER_PERSON, "other"))

        await connection.execute(
            _ASSET, (_ALREADY_FILED, "identity-filed", "image", "sitting-08.jpg", _EPOCH)
        )
        await connection.execute(_PHOTO_SET, ("set-1", "A set that already exists", _EPOCH))
        await connection.execute(_IN_SET, ("set-1", _ALREADY_FILED, 0, _EPOCH))

        await connection.execute(
            _ASSET, (_A_VIDEO, "identity-video", "video", "sitting-09.mp4", _EPOCH)
        )
    return database


def _pass(database: Database) -> tuple[ShootService, Store, _Index]:
    near = tuple(
        (asset_id, 0.10)
        for asset_id in (*_NAMED[1:], *_UNNAMED, _OTHER_PERSON, _ALREADY_FILED, _A_VIDEO)
    )
    index = _Index(near)
    store = Store(database, clock=lambda: _EPOCH)
    service = ShootService(
        database=database,
        store=store,
        access=None,  # type: ignore[arg-type]
        semantic=index,  # type: ignore[arg-type]
        photo_sets=None,  # type: ignore[arg-type]
        preferences=_Settings(),  # type: ignore[arg-type]
        recorder=None,  # type: ignore[arg-type]
        least=_FLOOR,
    )
    return service, store, index


@pytest.mark.asyncio
async def test_the_pass_proposes_a_shoot_on_a_real_library(tmp_path: Path) -> None:
    """The pass proposes a shoot on a real database, built here."""
    database = await _library(tmp_path)
    try:
        service, store, _index = _pass(database)
        context = _Context()

        await look(context, service=service)  # type: ignore[arg-type]

        assert context.progress == 1.0
        waiting, total = await store.waiting(limit=10)
        assert total == 1, "the pass proposed exactly one shoot"
        full = await store.one(waiting[0].id)
        assert full is not None
        assert full.name == _CREATOR
        # The creator's own four, in the order the sitting is numbered, then the two the widening
        # imported. A list, not a set: the order is what a card draws.
        assert full.asset_ids == (*_NAMED, *_UNNAMED)
        assert full.unnamed_ids == _UNNAMED
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_the_widening_leaves_a_near_picture_that_is_not_the_creators(tmp_path: Path) -> None:
    """The widening leaves a near picture that is somebody else's, in a Photo Set, or a video."""
    database = await _library(tmp_path)
    try:
        service, store, _index = _pass(database)

        await service.find()

        waiting, _ = await store.waiting(limit=10)
        full = await store.one(waiting[0].id)
        assert full is not None
        assert _OTHER_PERSON not in full.asset_ids, "a picture of somebody else is not this shoot"
        assert _ALREADY_FILED not in full.asset_ids, "a picture already in a Photo Set is grouped"
        assert _A_VIDEO not in full.asset_ids, "a shoot is a sitting of stills"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_the_pass_asks_the_index_once_per_shoot_and_not_once_per_picture(
    tmp_path: Path,
) -> None:
    """The pass reads the pool once and asks the index once per shoot, not per picture."""
    database = await _library(tmp_path)
    try:
        service, _store, index = _pass(database)

        await service.find()

        assert len(index.described) == 1, "the pool's numbers are read once"
        assert index.asked == [_NAMED[0]], "and the index searched once, for the widening"
    finally:
        await database.close()
