# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a produced copy inherits from the file it was made from.

The rule under test is one sentence: what conceals or describes follows the copy, what exposes it
to somebody else does not. The concealment half is the one that matters most, and it is the one
that cannot be noticed by looking at a screen: a copy that should have been hidden and is not
looks exactly like an ordinary file, and the only person who would spot it is the person it was
being hidden from.
"""

from __future__ import annotations

import pytest

from sift.kernel.access import Repository, Role, lineage
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.vocabulary import ACT_COMPRESS
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.anyio

NOW = 1_700_000_000


async def _an_asset(database: Database, digest: str) -> str:
    asset_id = new_id()
    await database.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', ?)",
        (asset_id, digest, NOW),
    )
    return asset_id


async def _a_tag(database: Database, name: str) -> str:
    tag_id = new_id()
    await database.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)", (tag_id, name, NOW)
    )
    return tag_id


async def _a_person(database: Database, name: str) -> str:
    person_id = new_id()
    await database.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)", (person_id, name, NOW)
    )
    return person_id


async def _a_folder(database: Database, root_id: str, name: str) -> str:
    """One folder directly under a root. Created with its root the first time it is asked for."""
    await database.execute(
        "INSERT OR IGNORE INTO library_roots (id, name, abs_path, created_at)"
        " VALUES (?, 'lib', ?, ?)",
        (root_id, f"/lib/{root_id}", NOW),
    )
    folder_id = new_id()
    await database.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, NULL, ?, ?)",
        (folder_id, root_id, name, name),
    )
    return folder_id


async def _place(
    database: Database, asset_id: str, *, root_id: str, folder: str, name: str
) -> None:
    """Put a file somewhere. Nothing is visible to anybody until it is somewhere.

    That is a rule of the resolver rather than of this module (an asset with no location is the
    record of a file that is no longer anywhere, and no screen shows one), but it is why every
    test below builds a library instead of only rows.
    """
    await database.execute(
        "INSERT INTO asset_locations"
        " (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id(), asset_id, root_id, folder, f"{folder}/{name}", name, NOW, NOW),
    )


async def _state(database: Database, asset_id: str, user_id: str) -> dict[str, object]:
    row = await database.fetch_one(
        "SELECT rating, hidden FROM asset_user_state WHERE asset_id = ? AND user_id = ?",
        (asset_id, user_id),
    )
    return {} if row is None else {"rating": row["rating"], "hidden": row["hidden"]}


@pytest.fixture
async def prepared(temp_db: Database) -> Database:
    await temp_db.initialize_schema()
    return temp_db


async def test_a_copy_of_a_hidden_file_arrives_hidden(
    prepared: Database, access: Repository
) -> None:
    """The one that is a disclosure if it is missing, and a silent one.

    Mutate the hidden column out of what is carried and this fails, which is the whole reason it
    is a test of its own rather than a line in a broader one.
    """
    admin = await create_user(prepared, Role.ADMIN)
    original = await _an_asset(prepared, "digest-one")
    copy = await _an_asset(prepared, "digest-two")
    await prepared.execute(
        "INSERT INTO asset_user_state (asset_id, user_id, hidden, hidden_at, updated_at)"
        " VALUES (?, ?, 1, ?, ?)",
        (original, admin.id, NOW, NOW),
    )

    inherited = await lineage.inherit(
        prepared, access, source_asset_id=original, copy_asset_id=copy, now=NOW
    )

    assert inherited.hidden_for == 1
    assert await _state(prepared, copy, admin.id) == {"rating": None, "hidden": 1}


async def test_a_copy_of_a_file_hidden_from_two_users_is_hidden_from_both(
    prepared: Database, access: Repository
) -> None:
    """Concealment is per user, so carrying it is per user too."""
    admin = await create_user(prepared, Role.ADMIN)
    guest = await create_user(prepared, Role.GUEST)
    original = await _an_asset(prepared, "digest-one")
    copy = await _an_asset(prepared, "digest-two")
    for user in (admin, guest):
        await prepared.execute(
            "INSERT INTO asset_user_state (asset_id, user_id, hidden, hidden_at, updated_at)"
            " VALUES (?, ?, 1, ?, ?)",
            (original, user.id, NOW, NOW),
        )

    inherited = await lineage.inherit(
        prepared, access, source_asset_id=original, copy_asset_id=copy, now=NOW
    )

    assert inherited.hidden_for == 2
    assert (await _state(prepared, copy, admin.id))["hidden"] == 1
    assert (await _state(prepared, copy, guest.id))["hidden"] == 1


async def test_a_copy_of_a_file_nobody_hid_is_not_hidden(
    prepared: Database, access: Repository
) -> None:
    """The other direction, which a version that carried everything would get wrong."""
    admin = await create_user(prepared, Role.ADMIN)
    original = await _an_asset(prepared, "digest-one")
    copy = await _an_asset(prepared, "digest-two")
    await prepared.execute(
        "INSERT INTO asset_user_state (asset_id, user_id, rating, hidden, updated_at)"
        " VALUES (?, ?, 4, 0, ?)",
        (original, admin.id, NOW),
    )

    inherited = await lineage.inherit(
        prepared, access, source_asset_id=original, copy_asset_id=copy, now=NOW
    )

    assert inherited.hidden_for == 0
    assert await _state(prepared, copy, admin.id) == {"rating": 4, "hidden": 0}


async def test_a_copy_carries_the_tags_and_the_people(
    prepared: Database, access: Repository
) -> None:
    """It is the same content, so it is the same tags and the same people in it."""
    original = await _an_asset(prepared, "digest-one")
    copy = await _an_asset(prepared, "digest-two")
    tag = await _a_tag(prepared, "holiday")
    person = await _a_person(prepared, "Someone")
    await prepared.execute(
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (original, tag)
    )
    await prepared.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (original, person)
    )

    inherited = await lineage.inherit(
        prepared, access, source_asset_id=original, copy_asset_id=copy, now=NOW
    )

    assert inherited.tags == 1
    assert inherited.people == 1
    tags = await prepared.fetch_all("SELECT tag_id FROM asset_tags WHERE asset_id = ?", (copy,))
    assert [str(row["tag_id"]) for row in tags] == [tag]
    people = await prepared.fetch_all(
        "SELECT person_id FROM asset_people WHERE asset_id = ?", (copy,)
    )
    assert [str(row["person_id"]) for row in people] == [person]


async def test_a_copy_carries_how_each_tag_and_each_person_got_there_and_when(
    prepared: Database, access: Repository
) -> None:
    """Otherwise every inherited row reads as a decision somebody made.

    A blank source means "a person put this here", so a copy that dropped it would turn a
    stash-box's work into somebody's own choice, and a sweep for what a box added would miss the
    copies. This holds it for the PERSON as well as the tag.

    The moment comes across unchanged rather than being restamped with today. What the column
    records is when somebody decided this file is of that thing, and the copy is of the same thing;
    today's date would say a decision was taken that nobody took.
    """
    original = await _an_asset(prepared, "digest-one")
    copy = await _an_asset(prepared, "digest-two")
    tag = await _a_tag(prepared, "holiday")
    person = await _a_person(prepared, "Someone")
    await prepared.execute(
        "INSERT INTO asset_tags (asset_id, tag_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (original, tag, "stash_box", NOW - 5000),
    )
    await prepared.execute(
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (original, person, "folder", None),
    )

    await lineage.inherit(prepared, access, source_asset_id=original, copy_asset_id=copy, now=NOW)

    tags = await prepared.fetch_all(
        "SELECT source, decided_at FROM asset_tags WHERE asset_id = ?", (copy,)
    )
    assert [(row["source"], row["decided_at"]) for row in tags] == [("stash_box", NOW - 5000)]
    people = await prepared.fetch_all(
        "SELECT source, decided_at FROM asset_people WHERE asset_id = ?", (copy,)
    )
    assert [(row["source"], row["decided_at"]) for row in people] == [("folder", None)]


async def test_a_copy_joins_the_collections_its_source_is_in_at_the_end(
    prepared: Database, access: Repository
) -> None:
    """A collection is a claim about the content, and a copy is the same content: a copy left
    outside every collection would look as if it belonged and not be there. It lands at the end,
    after what was already there."""
    from sift.kernel.access import Role
    from sift.testing.fixtures import create_user

    admin = await create_user(prepared, Role.ADMIN)
    original = await _an_asset(prepared, "digest-one")
    other = await _an_asset(prepared, "digest-other")
    copy = await _an_asset(prepared, "digest-two")
    collection = new_id()
    await prepared.execute(
        "INSERT INTO collections (id, name, owner_id, created_at) VALUES (?, 'Beach', ?, ?)",
        (collection, admin.id, NOW),
    )
    await prepared.execute(
        "INSERT INTO collection_items (collection_id, asset_id, position) VALUES (?, ?, 0)",
        (collection, original),
    )
    await prepared.execute(
        "INSERT INTO collection_items (collection_id, asset_id, position) VALUES (?, ?, 1)",
        (collection, other),
    )

    inherited = await lineage.inherit(
        prepared, access, source_asset_id=original, copy_asset_id=copy, now=NOW
    )

    assert inherited.collections == 1
    rows = await prepared.fetch_all(
        "SELECT asset_id, position FROM collection_items WHERE collection_id = ? ORDER BY position",
        (collection,),
    )
    assert [(str(row["asset_id"]), int(row["position"])) for row in rows] == [
        (original, 0),
        (other, 1),
        (copy, 2),
    ]


async def test_a_copy_does_not_inherit_a_share(prepared: Database, access: Repository) -> None:
    """A grant is a deliberate decision about a specific file.

    Extending one to a file that did not exist when it was made is deciding on somebody's behalf,
    and it is the one direction of this feature that would hand somebody access rather than take
    it away.
    """
    guest = await create_user(prepared, Role.GUEST)
    original = await _an_asset(prepared, "digest-one")
    copy = await _an_asset(prepared, "digest-two")
    await prepared.execute(
        "INSERT INTO acl_grants"
        " (id, object_type, object_id, subject_user_id, effect, created_at)"
        " VALUES (?, 'item', ?, ?, 'share', ?)",
        (new_id(), original, guest.id, NOW),
    )

    await lineage.inherit(prepared, access, source_asset_id=original, copy_asset_id=copy, now=NOW)

    grants = await prepared.fetch_all("SELECT id FROM acl_grants WHERE object_id = ?", (copy,))
    assert grants == []


async def test_a_copy_does_not_inherit_a_view_history(
    prepared: Database, access: Repository
) -> None:
    """Those are records of watching THAT file. They are not true of a copy nobody has opened."""
    admin = await create_user(prepared, Role.ADMIN)
    original = await _an_asset(prepared, "digest-one")
    copy = await _an_asset(prepared, "digest-two")
    await prepared.execute(
        "INSERT INTO asset_user_state"
        " (asset_id, user_id, favorite, rating, view_count, watched_ms, resume_ms, updated_at)"
        " VALUES (?, ?, 1, 5, 12, 90000, 45000, ?)",
        (original, admin.id, NOW),
    )

    await lineage.inherit(prepared, access, source_asset_id=original, copy_asset_id=copy, now=NOW)

    row = await prepared.fetch_one(
        "SELECT favorite, view_count, watched_ms, resume_ms, rating"
        " FROM asset_user_state WHERE asset_id = ? AND user_id = ?",
        (copy, admin.id),
    )
    assert row is not None
    assert row["favorite"] == 0
    assert row["view_count"] == 0
    assert row["watched_ms"] == 0
    assert row["resume_ms"] is None
    # The rating is the one thing on that row that describes the content rather than the watching.
    assert row["rating"] == 5


async def test_inheriting_from_a_file_with_nothing_on_it_writes_nothing(
    prepared: Database, access: Repository
) -> None:
    original = await _an_asset(prepared, "digest-one")
    copy = await _an_asset(prepared, "digest-two")

    inherited = await lineage.inherit(
        prepared, access, source_asset_id=original, copy_asset_id=copy, now=NOW
    )

    assert inherited == lineage.Inherited(tags=0, people=0, hidden_for=0, ratings=0)


async def test_inheriting_twice_is_the_same_as_inheriting_once(
    prepared: Database, access: Repository
) -> None:
    """A retried job must not double anything, and the join tables are what would."""
    original = await _an_asset(prepared, "digest-one")
    copy = await _an_asset(prepared, "digest-two")
    tag = await _a_tag(prepared, "holiday")
    await prepared.execute(
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (original, tag)
    )

    await lineage.inherit(prepared, access, source_asset_id=original, copy_asset_id=copy, now=NOW)
    await lineage.inherit(prepared, access, source_asset_id=original, copy_asset_id=copy, now=NOW)

    tags = await prepared.fetch_all("SELECT tag_id FROM asset_tags WHERE asset_id = ?", (copy,))
    assert len(tags) == 1


# --- the routes into the vault that a copy does NOT inherit -------------------------------------
#
# Copying the hidden flag, the tags and the People across covers most of concealment and not all of
# it. A file is concealed by any of six things and only some of them travel with a copy: the flag
# does, the tags and the People do, the folder does because the copy lands in it, and a
# collection, a site, and a second copy of the original sitting somewhere hidden do not.
#
# These three are that gap. Each one is a file that is nowhere to be seen, compressed, producing a
# copy that sits on the ordinary grid in plain sight. There is nothing on any screen that would say
# so, which is why they are tests rather than something to notice later.


async def test_a_copy_of_a_file_hidden_by_its_collection_is_concealed_by_joining_it(
    prepared: Database, access: Repository
) -> None:
    """A hidden collection conceals what is in it, and the copy joins it. So the copy is concealed
    by the same thing the original is, before the comparison at the end has anything to do."""
    admin = await create_user(prepared, Role.ADMIN)
    original = await _an_asset(prepared, "digest-one")
    copy = await _an_asset(prepared, "digest-two")
    root = new_id()
    folder = await _a_folder(prepared, root, "clips")
    await _place(prepared, original, root_id=root, folder=folder, name="clip.mp4")
    await _place(prepared, copy, root_id=root, folder=folder, name="clip-10MB.mp4")
    collection = new_id()
    await prepared.execute(
        "INSERT INTO collections (id, name, created_at) VALUES (?, 'Private', ?)",
        (collection, NOW),
    )
    await prepared.execute(
        "INSERT INTO collection_items (collection_id, asset_id) VALUES (?, ?)",
        (collection, original),
    )
    await prepared.execute(
        "INSERT INTO collection_user_state (collection_id, user_id, hidden, hidden_at, updated_at)"
        " VALUES (?, ?, 1, ?, ?)",
        (collection, admin.id, NOW, NOW),
    )
    assert await access.is_concealed(admin.id, original), "the original is not actually concealed"

    inherited = await lineage.inherit(
        prepared, access, source_asset_id=original, copy_asset_id=copy, now=NOW
    )

    # No flag was carried (there is none on the original's own row), and the comparison at the
    # end found nothing to do: the copy joined the hidden collection, and that conceals it.
    assert inherited.hidden_for == 0
    assert inherited.collections == 1
    assert inherited.concealed_for == 0
    assert await access.is_concealed(admin.id, copy)


async def test_a_copy_of_a_file_hidden_by_its_site_is_concealed_too(
    prepared: Database, access: Repository
) -> None:
    """A site reaches a file through two hops, and a copy has neither of them."""
    admin = await create_user(prepared, Role.ADMIN)
    original = await _an_asset(prepared, "digest-one")
    copy = await _an_asset(prepared, "digest-two")
    root = new_id()
    folder = await _a_folder(prepared, root, "clips")
    await _place(prepared, original, root_id=root, folder=folder, name="clip.mp4")
    await _place(prepared, copy, root_id=root, folder=folder, name="clip-10MB.mp4")
    site, username = new_id(), new_id()
    await prepared.execute("INSERT INTO sites (id, name) VALUES (?, 'somewhere')", (site,))
    await prepared.execute(
        "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, 'someone', ?)",
        (username, site, NOW),
    )
    await prepared.execute(
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)", (original, username)
    )
    await prepared.execute(
        "INSERT INTO site_user_state (site_id, user_id, hidden, hidden_at, updated_at)"
        " VALUES (?, ?, 1, ?, ?)",
        (site, admin.id, NOW, NOW),
    )
    assert await access.is_concealed(admin.id, original), "the original is not actually concealed"

    inherited = await lineage.inherit(
        prepared, access, source_asset_id=original, copy_asset_id=copy, now=NOW
    )

    assert inherited.concealed_for == 1
    assert await access.is_concealed(admin.id, copy)


async def test_a_copy_is_concealed_when_the_originals_other_copy_sits_somewhere_hidden(
    prepared: Database, access: Repository
) -> None:
    """The same bytes in two folders, one of them hidden, conceals the file everywhere.

    The produced copy lands beside ONE of them (the visible one), so landing in the same folder
    as the original is not the guarantee it looks like when the original is in more than one.
    """
    admin = await create_user(prepared, Role.ADMIN)
    original = await _an_asset(prepared, "digest-one")
    copy = await _an_asset(prepared, "digest-two")
    root = new_id()
    open_folder = await _a_folder(prepared, root, "open")
    hidden_folder = await _a_folder(prepared, root, "private")
    await _place(prepared, original, root_id=root, folder=open_folder, name="clip.mp4")
    await _place(prepared, original, root_id=root, folder=hidden_folder, name="clip.mp4")
    # The copy goes beside the VISIBLE one, which is where the write path puts it.
    await _place(prepared, copy, root_id=root, folder=open_folder, name="clip-10MB.mp4")
    await prepared.execute(
        "INSERT INTO folder_user_state (folder_id, user_id, hidden, hidden_at, updated_at)"
        " VALUES (?, ?, 1, ?, ?)",
        (hidden_folder, admin.id, NOW, NOW),
    )
    assert await access.is_concealed(admin.id, original), "the original is not actually concealed"

    inherited = await lineage.inherit(
        prepared, access, source_asset_id=original, copy_asset_id=copy, now=NOW
    )

    assert inherited.concealed_for == 1
    assert await access.is_concealed(admin.id, copy)


async def test_a_copy_concealed_by_a_tag_it_inherited_is_not_hidden_a_second_time(
    prepared: Database, access: Repository
) -> None:
    """When what the copy inherited already conceals it, nothing further is written, on purpose.

    A hidden tag conceals everything under it, and the copy carries the tag, so it is concealed the
    moment it is tagged. Stamping a hidden flag on the copy as well would look harmless and would
    quietly split the two apart: un-hiding the tag would bring the original back and leave the copy
    concealed by a flag nobody remembers setting, on a screen that gives no reason for it.
    """
    admin = await create_user(prepared, Role.ADMIN)
    original = await _an_asset(prepared, "digest-one")
    copy = await _an_asset(prepared, "digest-two")
    root = new_id()
    folder = await _a_folder(prepared, root, "clips")
    await _place(prepared, original, root_id=root, folder=folder, name="clip.mp4")
    await _place(prepared, copy, root_id=root, folder=folder, name="clip-10MB.mp4")
    tag = await _a_tag(prepared, "private")
    await prepared.execute(
        "INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", (original, tag)
    )
    await prepared.execute(
        "INSERT INTO tag_user_state (tag_id, user_id, hidden, hidden_at, updated_at)"
        " VALUES (?, ?, 1, ?, ?)",
        (tag, admin.id, NOW, NOW),
    )
    assert await access.is_concealed(admin.id, original), "the original is not actually concealed"

    inherited = await lineage.inherit(
        prepared, access, source_asset_id=original, copy_asset_id=copy, now=NOW
    )

    assert inherited.concealed_for == 0
    assert await access.is_concealed(admin.id, copy), "the tag should be doing this on its own"
    assert await _state(prepared, copy, admin.id) == {}


async def test_a_copy_of_a_file_nobody_concealed_is_left_alone(
    prepared: Database, access: Repository
) -> None:
    """The other direction, and the one a comparison that answered yes to everything would fail.

    A version of this that concealed every copy would pass all three tests above and be useless,
    so this is the line that says the comparison is reading something.
    """
    admin = await create_user(prepared, Role.ADMIN)
    original = await _an_asset(prepared, "digest-one")
    copy = await _an_asset(prepared, "digest-two")
    root = new_id()
    folder = await _a_folder(prepared, root, "clips")
    await _place(prepared, original, root_id=root, folder=folder, name="clip.mp4")
    await _place(prepared, copy, root_id=root, folder=folder, name="clip-10MB.mp4")
    collection = new_id()
    await prepared.execute(
        "INSERT INTO collections (id, name, created_at) VALUES (?, 'Ordinary', ?)",
        (collection, NOW),
    )
    await prepared.execute(
        "INSERT INTO collection_items (collection_id, asset_id) VALUES (?, ?)",
        (collection, original),
    )

    inherited = await lineage.inherit(
        prepared, access, source_asset_id=original, copy_asset_id=copy, now=NOW
    )

    assert inherited.concealed_for == 0
    assert not await access.is_concealed(admin.id, copy)
    assert await _state(prepared, copy, admin.id) == {}


async def test_a_disabled_user_is_not_something_to_conceal_from(
    prepared: Database, access: Repository
) -> None:
    """There is nobody to hide from, and building a viewer for them would answer nothing."""
    admin = await create_user(prepared, Role.ADMIN)
    original = await _an_asset(prepared, "digest-one")
    copy = await _an_asset(prepared, "digest-two")
    await prepared.execute(
        "INSERT INTO asset_user_state (asset_id, user_id, hidden, hidden_at, updated_at)"
        " VALUES (?, ?, 1, ?, ?)",
        (original, admin.id, NOW, NOW),
    )
    await prepared.execute("UPDATE users SET disabled = 1 WHERE id = ?", (admin.id,))

    inherited = await lineage.inherit(
        prepared, access, source_asset_id=original, copy_asset_id=copy, now=NOW
    )

    assert inherited.concealed_for == 0
    assert not await access.is_concealed(admin.id, original)


# --- the tag every copy carries ----------------------------------------------------------------


async def test_the_tag_is_made_the_first_time_and_reused_after(
    prepared: Database, access: Repository
) -> None:
    first = await _an_asset(prepared, "digest-one")
    second = await _an_asset(prepared, "digest-two")

    one = await lineage.tag_produced(
        prepared, asset_id=first, tag_name="Compressed", act=ACT_COMPRESS, now=NOW, new_id=new_id()
    )
    two = await lineage.tag_produced(
        prepared, asset_id=second, tag_name="Compressed", act=ACT_COMPRESS, now=NOW, new_id=new_id()
    )

    assert one == two
    rows = await prepared.fetch_all("SELECT id FROM tags WHERE name = ?", ("Compressed",))
    assert len(rows) == 1


async def test_tagging_the_same_file_twice_attaches_it_once(
    prepared: Database, access: Repository
) -> None:
    asset = await _an_asset(prepared, "digest-one")
    await lineage.tag_produced(
        prepared, asset_id=asset, tag_name="Compressed", act=ACT_COMPRESS, now=NOW, new_id=new_id()
    )
    await lineage.tag_produced(
        prepared, asset_id=asset, tag_name="Compressed", act=ACT_COMPRESS, now=NOW, new_id=new_id()
    )
    rows = await prepared.fetch_all("SELECT tag_id FROM asset_tags WHERE asset_id = ?", (asset,))
    assert len(rows) == 1


async def test_the_tag_is_an_ordinary_one(prepared: Database, access: Repository) -> None:
    """Not a special marker: it is in the tags table, so it filters and sorts like every other."""
    asset = await _an_asset(prepared, "digest-one")
    tag_id = await lineage.tag_produced(
        prepared, asset_id=asset, tag_name="Compressed", act=ACT_COMPRESS, now=NOW, new_id=new_id()
    )
    row = await prepared.fetch_one("SELECT name FROM tags WHERE id = ?", (tag_id,))
    assert row is not None
    assert str(row["name"]) == "Compressed"
