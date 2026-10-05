# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Tags column of a wall of things leads with Has tags and No tags, each the wall it opens."""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

# Imported for their tables, which the walls read.
import sift.slices.faces.schema
import sift.slices.music.schema
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import EntityNarrowing, Repository, Viewer
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.testing.fixtures import Actors, World, hide

LINKS = {
    "person": "INSERT INTO person_tags (person_id, tag_id) VALUES (?, ?)",
    "site": "INSERT INTO site_tags (site_id, tag_id) VALUES (?, ?)",
    "collection": "INSERT INTO collection_tags (collection_id, tag_id) VALUES (?, ?)",
    "photo_set": "INSERT INTO photo_set_tags (photo_set_id, tag_id) VALUES (?, ?)",
}


def _counted(access: Repository, subject: str) -> Callable[..., Awaitable[list[Any]]]:
    return {
        "person": access.people_facets,
        "site": access.site_facets,
        "collection": access.collection_facets,
        "photo_set": access.photo_set_facets,
    }[subject]


async def _wall(access: Repository, viewer: Viewer, subject: str, *picked: str) -> int:
    """How many things the wall holds under these Tags picks, read off a column every row is in."""
    narrowing = EntityNarrowing.of(subject, {"tags": list(picked)})
    rows = await _counted(access, subject)(viewer, "cover", narrowing=narrowing)
    return sum(one.count for one in rows)


async def _heads(access: Repository, viewer: Viewer, subject: str) -> dict[str, int]:
    rows = await _counted(access, subject)(viewer, "tags")
    return {one.value: one.count for one in rows if one.value in ("any", "none")}


@pytest.mark.parametrize("subject", list(LINKS))
async def test_has_and_no_tags_count_the_wall_each_opens(
    temp_db: Database, access: Repository, actors: Actors, world: World, subject: str
) -> None:
    thing = getattr(world, subject)
    await temp_db.execute(LINKS[subject], (thing, world.tag))

    heads = await _heads(access, actors.admin, subject)

    assert heads["any"] == 1
    assert await _wall(access, actors.admin, subject, "any") == 1
    assert await _wall(access, actors.admin, subject, "none") == heads.get("none", 0)
    assert heads["any"] + heads.get("none", 0) == await _wall(access, actors.admin, subject)
    # Refusing Has tags is No tags, and a tag still narrows by its id.
    assert await _wall(access, actors.admin, subject, "-any") == heads.get("none", 0)
    assert await _wall(access, actors.admin, subject, "-none") == 1
    assert await _wall(access, actors.admin, subject, world.tag) == 1


async def test_a_tag_hidden_from_the_viewer_does_not_make_a_person_tagged(
    temp_db: Database, access: Repository, actors: Actors, world: World
) -> None:
    hidden = new_id()
    await temp_db.execute(
        "INSERT INTO tags (id, name, name_sort, created_at) VALUES (?, 'kept', 'kept', 0)",
        (hidden,),
    )
    await temp_db.execute(LINKS["person"], (world.person, hidden))
    await hide(temp_db, "tag", hidden, actors.admin.id)

    closed = await _heads(access, actors.admin, "person")
    opened = dataclasses.replace(actors.admin, show_hidden=True)

    assert "any" not in closed
    assert await _wall(access, actors.admin, "person", "any") == 0
    assert (await _heads(access, opened, "person"))["any"] == 1
    assert await _wall(access, opened, "person", "any") == 1
