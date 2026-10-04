# SPDX-License-Identifier: AGPL-3.0-or-later
"""The reference gallery is built once, not once per file.

The table is small, but the cost is the clustering arithmetic, and done per file it would run
again over unchanged rows every few seconds while a library is being scanned.

The cache is held against a stamp read back off the table rather than against a flag some writer
has to remember to clear. Six methods write a reference today; a rule living in six places holds
until somebody adds the seventh, and a stale cache looks exactly like a fresh one from outside.
"""

from __future__ import annotations

import pytest

from sift.kernel.ids import new_id
from sift.slices.faces.store import Store

pytestmark = [pytest.mark.integration]


async def _reference(store: Store, person_id: str) -> str:
    """One reference face for somebody, written the way the import path writes them."""
    reference_id = new_id()
    async with store._db.write() as connection:
        await connection.execute(
            "INSERT INTO face_references (id, person_id, crop_path, crop_digest, embedding,"
            " quality, origin, recognizer, pixels, created_at)"
            " VALUES (?, ?, ?, ?, ?, 1.0, 'added', 'buffalo_s', 100, 1)",
            (reference_id, person_id, f"{reference_id}.jpg", reference_id, b"\x00" * 8),
        )
    return reference_id


async def test_the_stamp_moves_when_a_reference_is_added(store: Store, person: str) -> None:
    before = await store.reference_stamp()

    await _reference(store, person)

    assert await store.reference_stamp() != before


async def test_the_stamp_moves_when_one_is_removed(store: Store, person: str) -> None:
    reference_id = await _reference(store, person)
    with_one = await store.reference_stamp()

    async with store._db.write() as connection:
        await connection.execute("DELETE FROM face_references WHERE id = ?", (reference_id,))

    assert await store.reference_stamp() != with_one


async def test_swapping_one_for_another_still_moves_the_stamp(store: Store, person: str) -> None:
    """The case a naive count would miss, and the reason the highest id is in the stamp too.

    A removal and an addition in the same moment leaves the count exactly where it was. Ids are
    minted under a floor that never goes down, so the new one is always higher than the old.
    """
    first = await _reference(store, person)
    before = await store.reference_stamp()

    async with store._db.write() as connection:
        await connection.execute("DELETE FROM face_references WHERE id = ?", (first,))
    await _reference(store, person)

    after = await store.reference_stamp()
    assert after[0] == before[0], "the count is deliberately unchanged in this case"
    assert after != before, "and the stamp still has to move, or the cache goes stale silently"


async def test_an_unchanged_table_gives_the_same_stamp(store: Store, person: str) -> None:
    """The half that makes it a cache rather than a slow rebuild: reading twice over untouched
    rows has to answer the same, or nothing is ever reused."""
    await _reference(store, person)

    assert await store.reference_stamp() == await store.reference_stamp()
