# SPDX-License-Identifier: AGPL-3.0-or-later
"""The files still waiting for a fingerprint are a counted kind this slice registers with the
kernel, so the number the catch-up card draws is one user's, hides what its vault hides, and is
exact after every change a file can undergo."""

from __future__ import annotations

import pytest

from sift.kernel.access import visibility
from sift.kernel.db import Database, registered_components
from sift.slices.music.schema import WAITING_KIND
from sift.slices.music.store import MusicStore
from sift.testing.fixtures import Actors, World, hide

pytestmark = pytest.mark.integration


async def _nothing_differs(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        found = await visibility.differences(connection)
    assert found == [], f"the stored verdict disagrees with the facts: {found[:5]}"


def test_the_slice_registered_its_kind() -> None:
    assert any(one.kind == WAITING_KIND for one in visibility.counted())
    assert "music" in registered_components()["visibility"].depends_on


async def test_the_waiting_count_is_one_accounts_and_hides_what_the_vault_hides(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """Three files read with a sound track: an admin sees three waiting; one fingerprinted, two;
    one concealed from that admin, one: the vault's files left out in every mode."""
    store = MusicStore(temp_db)
    for asset_id in (world.solo, world.twin, world.loose):
        await temp_db.execute(
            "UPDATE assets SET acodec = 'aac', probed_at = 1 WHERE id = ?", (asset_id,)
        )
    await _nothing_differs(temp_db)
    assert await store.waiting_for(actors.admin.id) == 3
    await temp_db.execute(
        "INSERT INTO audio_fingerprints (asset_id, algorithm, tool, duration_ms, fingerprint,"
        " computed_at) VALUES (?, 1, 't', 0, x'', 0)",
        (world.loose,),
    )
    assert await store.waiting_for(actors.admin.id) == 2
    await hide(temp_db, "asset", world.twin, actors.admin.id)
    await _nothing_differs(temp_db)
    assert await store.waiting_for(actors.admin.id) == 1
    assert await store.any_waiting()
