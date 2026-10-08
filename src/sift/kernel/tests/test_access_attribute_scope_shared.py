# SPDX-License-Identifier: AGPL-3.0-or-later
"""A thing on one shared file and on forty kept ones costs a guest what a thing on one shared file
alone does: the filter follows only the guest's own files."""

from __future__ import annotations

from typing import Any

import pytest

from sift.kernel.access import Where
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.tests.access_helpers import _EPOCH
from sift.kernel.tests.test_access_attribute_scope import (
    _THINGS,
    _filtered,
    _steps,
    library,  # noqa: F401
)
from sift.testing.fixtures import Actors

_LINK = {
    "people": "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)",
    "usernames": "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)",
    "collections": "INSERT INTO collection_items (asset_id, collection_id) VALUES (?, ?)",
    "photo_sets": "INSERT INTO photo_set_items (asset_id, photo_set_id) VALUES (?, ?)",
    "songs": "INSERT INTO song_files (asset_id, song_id) VALUES (?, ?)",
}


async def _on_the_shared_file(db: Database, key: str, shared: str) -> str:
    """A new thing of this kind on this file alone; for a Site, through a username on it."""
    thing = new_id()
    if key == "people":
        await db.execute(
            "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (thing, "Wren Talbot")
        )
    else:
        await db.execute(_THINGS["sites" if key == "usernames" else key], (thing, key, _EPOCH))
    if key == "sites":
        username = new_id()
        await db.execute(
            "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, ?)",
            (username, thing, "shared", _EPOCH),
        )
        await db.execute(_LINK["usernames"], (shared, username))
        return thing
    if key == "usernames":
        site, thing = thing, new_id()
        await db.execute(
            "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, ?)",
            (thing, site, "shared", _EPOCH),
        )
    await db.execute(_LINK[key], (shared, thing))
    return thing


@pytest.mark.parametrize(
    "key", ["people", "usernames", "sites", "collections", "photo_sets", "songs"]
)
async def test_a_value_on_a_shared_file_and_many_kept_ones_costs_a_guest_what_one_does(
    actors: Actors,
    library: dict[str, Any],  # noqa: F811
    temp_db: Database,
    key: str,
) -> None:
    first, second = library["shown"]
    many = library["person"] if key == "people" else library[key]
    on_many = library["usernames"] if key == "sites" else many
    await temp_db.execute(_LINK["usernames" if key == "sites" else key], (first, on_many))
    alone = await _on_the_shared_file(temp_db, key, second)
    asked = _filtered(Where(key, (many,))), _filtered(Where(key, (alone,)))
    guest = [await _steps(temp_db, actors.guest, one) for one in asked]
    assert [answer for _, answer in guest] == [
        (1, [first], [("video", 1)]),
        (1, [second], [("video", 1)]),
    ]
    assert guest[0][0] == guest[1][0]
    admin = [await _steps(temp_db, actors.admin, one) for one in asked]
    assert admin[0][0] != admin[1][0]
