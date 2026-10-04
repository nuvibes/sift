# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which act made a tag Sift put on a file it produced: written, read back, and filled in.

`produced` says only that Sift made a file. The two tags it puts on one (a compressed copy's, an
edited copy's) are told apart by the act that made them, so each one's Created by line wears the
glyph of the verb that made it. A tag made before the act was written down gets it from the files
it was put on, and a tag with nothing left to read keeps the general mark.
"""

from __future__ import annotations

import pytest

import sift.slices.media_edit.schema  # noqa: F401
from sift.kernel.access import Repository, catalog, lineage, schema
from sift.kernel.access.history_entity import history_of_tag
from sift.kernel.access.sentences import text_of
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.vocabulary import ACT_COMPRESS, ACT_EDIT
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio

NOW = 1_700_000_000


async def _act_of(database: Database, tag_id: str) -> str | None:
    row = await database.fetch_one("SELECT created_by_act FROM tags WHERE id = ?", (tag_id,))
    assert row is not None
    return None if row["created_by_act"] is None else str(row["created_by_act"])


async def test_each_act_is_written_on_the_tag_and_read_back_as_its_maker(
    temp_db: Database, world: World, actors: Actors
) -> None:
    smaller = await lineage.tag_produced(
        temp_db,
        asset_id=world.loose,
        tag_name="Smaller",
        act=ACT_COMPRESS,
        now=NOW,
        new_id=new_id(),
    )
    changed = await lineage.tag_produced(
        temp_db, asset_id=world.solo, tag_name="Changed", act=ACT_EDIT, now=NOW, new_id=new_id()
    )

    for tag_id, act in ((smaller, ACT_COMPRESS), (changed, ACT_EDIT)):
        made = await catalog.made_by(temp_db, actors.admin, "tag", tag_id)
        assert made is not None
        assert (made.actor.value, made.via, made.act) == ("sift", "produced", act)


async def test_a_tag_already_there_keeps_what_it_says(
    temp_db: Database, world: World, actors: Actors
) -> None:
    typed = new_id()
    await temp_db.execute(
        "INSERT INTO tags (id, name, created_at, created_by_kind) VALUES (?, 'Smaller', ?, 'user')",
        (typed, NOW),
    )
    found = await lineage.tag_produced(
        temp_db,
        asset_id=world.loose,
        tag_name="Smaller",
        act=ACT_COMPRESS,
        now=NOW,
        new_id=new_id(),
    )
    assert found == typed
    assert await _act_of(temp_db, typed) is None


async def test_an_act_with_no_word_is_refused(temp_db: Database, world: World) -> None:
    with pytest.raises(ValueError, match="no act"):
        await lineage.tag_produced(
            temp_db, asset_id=world.loose, tag_name="Smaller", act="made", now=NOW, new_id=new_id()
        )


async def test_every_other_kind_of_row_says_no_act(
    temp_db: Database, world: World, actors: Actors
) -> None:
    made = await catalog.made_by(temp_db, actors.admin, "person", world.person)
    assert made is None or made.act is None


async def test_the_step_reads_each_tag_s_act_off_the_files_it_was_put_on(
    temp_db: Database, world: World
) -> None:
    async def tag(name: str, kind: str, via: str | None) -> str:
        tag_id = new_id()
        await temp_db.execute(
            "INSERT INTO tags (id, name, created_at, created_by_kind, created_by_via)"
            " VALUES (?, ?, ?, ?, ?)",
            (tag_id, name, NOW, kind, via),
        )
        return tag_id

    async def made(copy: str, operation: str, at: int) -> None:
        await temp_db.execute(
            "INSERT INTO produced_files (id, asset_id, source_asset_id, operation, produced_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (new_id(), copy, world.solo, operation, at),
        )

    async def filed(copy: str, tag_id: str, source: str) -> None:
        await temp_db.execute(
            "INSERT INTO asset_tags (asset_id, tag_id, source, decided_at) VALUES (?, ?, ?, ?)",
            (copy, tag_id, source, NOW),
        )

    smaller = await tag("Smaller", "sift", "produced")
    changed = await tag("Changed", "sift", "produced")
    emptied = await tag("Emptied", "sift", "produced")
    typed = await tag("Typed", "user", None)
    await made(world.loose, "compress", 10)
    await made(world.twin, "trim", 20)
    await filed(world.loose, smaller, "produced")
    await filed(world.twin, changed, "produced")
    # Put on by hand, so it says nothing about how the tag was made.
    await filed(world.loose, changed, "user")
    await filed(world.loose, typed, "produced")

    async with temp_db.write() as connection:
        await schema.initialize_catalog(connection, 77)

    assert await _act_of(temp_db, smaller) == ACT_COMPRESS
    # Every editor operation is the one act.
    assert await _act_of(temp_db, changed) == ACT_EDIT
    # Nothing left to read: the general mark, never a guess.
    assert await _act_of(temp_db, emptied) is None
    # Somebody else's tag is not this step's to describe.
    assert await _act_of(temp_db, typed) is None


async def test_a_made_tags_history_says_the_act_and_wears_its_mark(
    temp_db: Database, world: World, actors: Actors, access: Repository
) -> None:
    """The tag's own History does not say "from a file it created" for both tags under one mark:
    its first line names the act the way the tag's page does, and carries the act for its glyph."""
    viewer = await access.load_viewer(actors.admin.id)
    assert viewer is not None
    for act, words in ((ACT_COMPRESS, "compressed"), (ACT_EDIT, "edited")):
        tag_id = await lineage.tag_produced(
            temp_db,
            asset_id=world.loose,
            tag_name=f"Made by {act}",
            act=act,
            now=NOW,
            new_id=new_id(),
        )
        (made, *_) = await history_of_tag(temp_db, viewer, tag_id)
        assert text_of(made.pieces).endswith(f"to the library from a file it {words}")
        assert (made.kind, made.via, made.how) == ("added", "produced", act)

    # A tag with no act kept says the pass's own words and carries no act.
    await temp_db.execute("UPDATE tags SET created_by_act = NULL WHERE name = 'Made by edit'")
    row = await temp_db.fetch_one("SELECT id FROM tags WHERE name = 'Made by edit'")
    assert row is not None
    (made, *_) = await history_of_tag(temp_db, viewer, str(row["id"]))
    assert text_of(made.pieces).endswith("from a file it created")
    assert (made.via, made.how) == ("produced", None)
