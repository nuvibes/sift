# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Site card's network mark: how many Sites are part of it, read with the page's other cells.

The number is the one the Site's own Sites tab carries, so every case here compares the card with
the wall that tab lists (`list_sites(parent=...)`) rather than with a literal.
"""

from __future__ import annotations

import pytest

from sift.kernel.access import Repository, Viewer
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.testing.fixtures import Actors, World, hide

pytestmark = pytest.mark.anyio


async def _network(temp_db: Database, world: World) -> tuple[str, str]:
    """A network over the fixture's Site, and a second label under it with no files at all."""
    network, empty = new_id(), new_id()
    await temp_db.execute(
        "INSERT INTO sites (id, name) VALUES (?, ?)", (network, "Northlight Group")
    )
    await temp_db.execute(
        "INSERT INTO sites (id, name, parent_id) VALUES (?, ?, ?)", (empty, "Quiet Studio", network)
    )
    await temp_db.execute("UPDATE sites SET parent_id = ? WHERE id = ?", (network, world.site))
    return network, empty


async def _agree(access: Repository, viewer: Viewer, network: str) -> int:
    cells = await access.card_counts(viewer, "site", [network])
    tab = (await access.list_sites(viewer, "", limit=1, parent=network)).total
    assert cells[network]["sites_within"] == tab
    return tab


async def test_a_network_card_counts_the_sites_its_tab_lists(
    access: Repository, world: World, actors: Actors, temp_db: Database
) -> None:
    network, _empty = await _network(temp_db, world)
    # An admin's wall lists a label with nothing in it; the card says both.
    assert await _agree(access, actors.admin, network) == 2
    # A label is part of nothing below it: its own mark is nought.
    cells = await access.card_counts(actors.admin, "site", [world.site])
    assert cells[world.site]["sites_within"] == 0


async def test_a_hidden_label_leaves_the_mark_as_it_leaves_the_tab(
    access: Repository, world: World, actors: Actors, temp_db: Database
) -> None:
    network, _empty = await _network(temp_db, world)
    await hide(temp_db, "site", world.site, actors.admin.id)
    assert await _agree(access, actors.admin, network) == 1


async def test_a_guest_counts_only_the_labels_with_something_to_show(
    access: Repository, world: World, actors: Actors, temp_db: Database
) -> None:
    network, _empty = await _network(temp_db, world)
    assert await _agree(access, actors.guest, network) < 2
