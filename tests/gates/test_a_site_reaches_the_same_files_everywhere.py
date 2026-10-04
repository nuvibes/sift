# SPDX-License-Identifier: AGPL-3.0-or-later
"""A site reaches one set of files, and every question about it gives that same answer.

A network owns labels and no file is filed under the network itself, so "which files does this
network reach" walks `sites.parent_id` downward in the Files tab and related counts, the site's
card,
the sharing membership, the concealment membership, and the redeciding of stored rows when a label
joins a network. A share or a hide that stopped at the network's own usernames would hand a guest
nothing while the tab showed thousands. So this builds a nest, asks every consumer, and moves a
parent.
"""

from __future__ import annotations

import pytest

from sift.kernel.access import Effect, ObjectType, Repository, Viewer, related_filter
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.sorting import sort_key
from sift.testing.fixtures import Actors, hide

pytestmark = [pytest.mark.gate, pytest.mark.integration]

_EPOCH = 1_700_000_000


class Nest:
    """A network, a label under it, a sub-label under that, one file filed under each: three
    levels, since a single non-recursive join gets two right."""

    def __init__(self) -> None:
        self.root = new_id()
        self.folder = new_id()
        self.network = new_id()
        self.label = new_id()
        self.sub = new_id()
        self.file_of_network = new_id()
        self.file_of_label = new_id()
        self.file_of_sub = new_id()

    @property
    def everything(self) -> set[str]:
        """Every file the network reaches: all three."""
        return {self.file_of_network, self.file_of_label, self.file_of_sub}


async def _build(database: Database, nest: Nest, *, label_joins_network: bool = True) -> None:
    """Write the nest into an already-migrated database as plain rows: the state is under test."""
    await database.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (nest.root, "roll", "/library/roll", _EPOCH),
    )
    await database.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, NULL, ?, ?)",
        (nest.folder, nest.root, "roll", "roll"),
    )
    for site_id, name, parent in (
        (nest.network, "network", None),
        (nest.label, "label", nest.network if label_joins_network else None),
        (nest.sub, "sub label", nest.label),
    ):
        await database.execute(
            "INSERT INTO sites (id, name, name_sort, parent_id) VALUES (?, ?, ?, ?)",
            (site_id, name, sort_key(name), parent),
        )
    for site_id, asset_id in (
        (nest.network, nest.file_of_network),
        (nest.label, nest.file_of_label),
        (nest.sub, nest.file_of_sub),
    ):
        username_id = new_id()
        await database.execute(
            "INSERT INTO usernames (id, site_id, name, name_sort, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (username_id, site_id, username_id, sort_key(username_id), _EPOCH),
        )
        await database.execute(
            "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
            " VALUES (?, ?, 1, 'video', ?)",
            (asset_id, f"digest-{asset_id}", _EPOCH),
        )
        await database.execute(
            "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename,"
            " first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id(), asset_id, nest.root, nest.folder, f"roll/{asset_id}.mp4", "x.mp4", 0, 0),
        )
        await database.execute(
            "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
            (asset_id, username_id),
        )


async def _through_the_leaf(access: Repository, viewer: Viewer, site_id: str) -> set[str]:
    """What the Files tab and every related count draw: the `sites` search leaf."""
    page = await access.visible_assets(viewer, limit=50, asset_filter=related_filter(site=site_id))
    return {item.asset.id for item in page.items}


async def _permitted(database: Database, user_id: str, nest: Nest) -> set[str]:
    """What the sharing membership decided: the stored rows a grant writes."""
    rows = await database.fetch_all(
        "SELECT asset_id FROM viewer_assets WHERE user_id = ?", (user_id,)
    )
    return {str(row["asset_id"]) for row in rows} & nest.everything


async def _concealed(database: Database, user_id: str, nest: Nest) -> set[str]:
    """What the concealment membership decided, off the same stored rows."""
    rows = await database.fetch_all(
        "SELECT asset_id FROM viewer_assets WHERE user_id = ? AND concealed = 1", (user_id,)
    )
    return {str(row["asset_id"]) for row in rows} & nest.everything


async def test_four_consumers_name_the_same_files(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The tab, the card, the share and the hide all say the same three files."""
    nest = Nest()
    await _build(temp_db, nest)

    assert await _through_the_leaf(access, actors.admin, nest.network) == nest.everything

    page = await access.list_sites(actors.admin, "", limit=20)
    counts = {site.id: site.asset_count for site in page.items}
    assert counts[nest.network] == len(nest.everything)

    await access.grant(ObjectType.SITE, nest.network, actors.guest.id, Effect.SHARE)
    assert await _permitted(temp_db, actors.guest.id, nest) == nest.everything

    await hide(temp_db, "site", nest.network, actors.admin.id)
    assert await _concealed(temp_db, actors.admin.id, nest) == nest.everything


async def test_a_label_joining_a_network_moves_the_grant(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The stored verdict follows `parent_id` when a label joins a network, and back out."""
    nest = Nest()
    await _build(temp_db, nest, label_joins_network=False)
    await access.grant(ObjectType.SITE, nest.network, actors.guest.id, Effect.SHARE)
    assert await _permitted(temp_db, actors.guest.id, nest) == {nest.file_of_network}

    await temp_db.execute("UPDATE sites SET parent_id = ? WHERE id = ?", (nest.network, nest.label))
    assert await _permitted(temp_db, actors.guest.id, nest) == nest.everything

    # And back out: a widening that cannot be undone is a grant nobody can revoke.
    await temp_db.execute("UPDATE sites SET parent_id = NULL WHERE id = ?", (nest.label,))
    assert await _permitted(temp_db, actors.guest.id, nest) == {nest.file_of_network}


async def test_deleting_a_network_takes_its_labels_files_back(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A deleted network revokes what it opened: `ON DELETE SET NULL` rewrites each label's
    `parent_id`, and a foreign-key action fires ordinary triggers, a SQLite property held here."""
    nest = Nest()
    await _build(temp_db, nest)
    await access.grant(ObjectType.SITE, nest.network, actors.guest.id, Effect.SHARE)
    assert await _permitted(temp_db, actors.guest.id, nest) == nest.everything

    await temp_db.execute("DELETE FROM sites WHERE id = ?", (nest.network,))
    assert await _permitted(temp_db, actors.guest.id, nest) == set()


async def test_hiding_a_network_takes_its_labels_with_it(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Hiding a network hides its labels' NAMES too: off the Sites wall, their pages and their
    usernames, each a separate statement. A name is the disclosure."""
    nest = Nest()
    await _build(temp_db, nest)
    before = await access.list_sites(actors.admin, "", limit=20)
    assert {site.id for site in before.items} == {nest.network, nest.label, nest.sub}

    await hide(temp_db, "site", nest.network, actors.admin.id)

    page = await access.list_sites(actors.admin, "", limit=20)
    assert {site.id for site in page.items} == set(), (
        "a label under a hidden network is still named"
    )
    # The label's own page answers as a concealed thing does.
    assert await access.visible_site(actors.admin, nest.label) is None
    assert await access.visible_site(actors.admin, nest.sub) is None
    # And the usernames under it, names of their own.
    usernames = await access.list_usernames(actors.admin, "", limit=20)
    assert {row.id for row in usernames.items} == set()


async def test_unhiding_a_network_gives_its_labels_back(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The walk reads both ways, so a concealment can be undone."""
    nest = Nest()
    await _build(temp_db, nest)
    await hide(temp_db, "site", nest.network, actors.admin.id)
    await hide(temp_db, "site", nest.network, actors.admin.id, hidden=False)

    page = await access.list_sites(actors.admin, "", limit=20)
    assert {site.id for site in page.items} == {nest.network, nest.label, nest.sub}
    assert await access.visible_site(actors.admin, nest.label) is not None


async def test_a_hidden_network_is_named_as_the_reason(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The concealment panel names the hidden network, for the file and for the label: it is on
    neither's record."""
    nest = Nest()
    await _build(temp_db, nest)
    await hide(temp_db, "site", nest.network, actors.admin.id)

    for asset_id in sorted(nest.everything):
        named = await access.vault_sources(actors.admin, ObjectType.ITEM, asset_id)
        assert [(one.source_id, one.here) for one in named] == [(nest.network, False)]

    # The label is concealed, not by anything of its own.
    named = await access.vault_sources(actors.admin, ObjectType.SITE, nest.label)
    assert [(one.source_id, one.here) for one in named] == [(nest.network, False)]
    # The network is concealed by ITSELF, which is what `here` says.
    named = await access.vault_sources(actors.admin, ObjectType.SITE, nest.network)
    assert [(one.source_id, one.here) for one in named] == [(nest.network, True)]
