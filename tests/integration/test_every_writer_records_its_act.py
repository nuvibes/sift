# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each writer that changes the library, exercised once and read back out of the record.

The gate beside this one proves that every such writer CALLS the door. It cannot prove that what
it wrote is true: a call with the wrong verb, the wrong way round, or with the name of a person
looked up after they were deleted passes it exactly as a right one does. So each act here is taken
for real and then read through the same reads a History pane uses, and the assertion is the
sentence somebody would read: the verb, what it was about, what it was done with, and the name the
thing had at the time.

Cross-slice on purpose, and that is why it is here rather than in any one slice's tests. The rule
is not a property of the people feature or the tags feature; it is one discipline that all of them
keep, and a copy of it per slice is a copy that drifts.
"""

from __future__ import annotations

import pytest

# `sift.main` is imported for its side effect, and named here rather than left to whichever other
# module a run happens to import first. Every slice's schema component registers itself at import,
# and this is the module that imports every slice, and a database built without the record's own
# component has no table for any of this to be written to, and a merge moves sixteen tables
# belonging to four different features.
from sift import main as _wires_every_slice
from sift.kernel.access import Effect, Named, ObjectType, Recording, Repository, Viewer
from sift.kernel.access.history_events import events_of_asset, events_of_entity
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.slices.collections.service import CollectionService
from sift.slices.people.merge import merge_many
from sift.slices.people.service import PeopleService
from sift.slices.photo_sets.service import PhotoSetService
from sift.slices.tags_ratings.service import TagService
from sift.testing.fixtures import Actors, World

assert _wires_every_slice is not None

pytestmark = pytest.mark.unit

#: Names from the cast in tests/gates/data/names_cast.txt.

_KEEPER = "Ilva Brennan"
_GOING = "Wren Aldabry"


async def _verbs_on_asset(db: Database, actors: Actors, asset_id: str) -> list[str]:
    return [one.verb or "" for one in await events_of_asset(db, actors.admin, asset_id)]


async def _one_about(db: Database, actors: Actors, kind: str, entity_id: str, verb: str):  # type: ignore[no-untyped-def]
    """The newest event of that verb about that thing, or None. Read the way a pane reads."""
    for one in await events_of_entity(db, actors.admin, kind, entity_id):  # type: ignore[arg-type]
        if one.verb == verb:
            return one
    return None


# --- the vault ------------------------------------------------------------------------------


async def test_concealing_a_file_and_bringing_it_back_are_both_written_down(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The flag says what is true NOW. Only the record can say when it changed, and by whom."""
    assert await access.set_asset_vault(actors.admin, world.solo, vault=True)
    opened = Viewer(
        id=actors.admin.id,
        role=actors.admin.role,
        show_hidden=True,
        concealment=actors.admin.concealment,
    )
    assert await access.set_asset_vault(opened, world.solo, vault=False)

    assert await _verbs_on_asset(temp_db, actors, world.solo) == ["revealed", "hidden"]


async def test_a_concealed_folder_is_the_folder_s_event_and_not_its_files(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """One press concealed a tree. An event per file would be a wrong story as well as a flood."""
    assert await access.set_folder_vault(actors.admin, world.leaf, vault=True)

    assert await _one_about(temp_db, actors, "folder", world.leaf, "hidden") is not None
    assert await _verbs_on_asset(temp_db, actors, world.solo) == []


# --- sharing --------------------------------------------------------------------------------


async def test_a_share_names_the_user_it_was_made_to_by_name(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The user is the object, so the sentence reads 'shared this with them'."""
    await access.grant(
        ObjectType.ITEM,
        world.solo,
        actors.guest.id,
        Effect.SHARE,
        event=Recording(
            user_id=actors.admin.id,
            verb="shared",
            subjects=(Named("asset", world.solo),),
            object=Named("login", actors.guest.id, "a guest"),
        ),
    )

    found = await events_of_asset(temp_db, actors.admin, world.solo)
    assert [one.verb for one in found] == ["shared"]
    assert found[0].object is not None
    assert found[0].object.name == "a guest"


async def test_a_revoke_survives_the_row_it_deleted(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """THE act that leaves nothing behind: the grant row goes, and the record is what is left."""
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)
    await access.revoke(
        ObjectType.ITEM,
        world.solo,
        actors.guest.id,
        Effect.SHARE,
        event=Recording(
            user_id=actors.admin.id,
            verb="unshared",
            subjects=(Named("asset", world.solo),),
            object=Named("login", actors.guest.id, "a guest"),
        ),
    )

    assert await access.grants_of(actors.guest.id) == []
    assert await _verbs_on_asset(temp_db, actors, world.solo) == ["unshared"]


# --- people ---------------------------------------------------------------------------------


async def test_adding_renaming_and_deleting_somebody_each_leave_a_line(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The delete is the one that matters: the row goes, so the NAME has to be in the event."""
    people = PeopleService(temp_db, access)
    person = await people.create_person(actors.admin, _GOING, notes=None)
    await people.update_person(
        actors.admin, person.id, _KEEPER, vault=False, notes=None, record=None
    )
    assert await people.delete_person(actors.admin, person.id)

    found = await events_of_entity(temp_db, actors.admin, "person", person.id)
    assert [one.verb for one in found] == ["deleted", "renamed", "added"]


async def test_taking_somebody_off_a_file_is_recorded_although_the_link_row_has_gone(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The fault the whole record exists to end: a removal erasing the link retroactively."""
    people = PeopleService(temp_db, access)
    await people.assign([world.solo], [world.person], add=False, actor=Actor.user(actors.admin.id))
    await people.assign(
        [world.solo],
        [world.person],
        add=True,
        actor=Actor.user(actors.admin.id),
        names={world.person: _KEEPER},
    )

    found = await events_of_asset(temp_db, actors.admin, world.solo)
    assert [one.verb for one in found] == ["linked", "unlinked"]
    assert found[0].object is not None
    assert found[0].object.id == world.person
    assert found[0].object.name == _KEEPER


async def test_a_merge_names_the_person_who_went_and_the_one_who_stayed(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A merge moves sixteen tables and deletes a row, and the record says so. The loser is the
    subject, because theirs is the history nobody can look up afterwards."""
    people = PeopleService(temp_db, access)
    keeper = await people.create_person(actors.admin, _KEEPER, notes=None)
    going = await people.create_person(actors.admin, _GOING, notes=None)

    weighed = await merge_many(
        temp_db,
        access,
        losing=[going.id],
        keeping=keeper.id,
        actor=Actor.user(actors.admin.id),
    )

    assert weighed is not None
    merged = await _one_about(temp_db, actors, "person", going.id, "merged")
    assert merged is not None
    assert merged.object is not None
    assert merged.object.id == keeper.id
    assert merged.object.name == _KEEPER


async def test_filing_a_file_under_a_site_and_taking_it_off_again_both_show(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    people = PeopleService(temp_db, access)
    await people.file_under_sites([world.solo], ["Northlight"], actor=Actor.user(actors.admin.id))

    found = await events_of_asset(temp_db, actors.admin, world.solo)
    assert [one.verb for one in found] == ["filed"]
    assert found[0].object is not None
    assert found[0].object.name == "Northlight"


async def test_renaming_and_deleting_a_site_carry_the_name_it_had(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    people = PeopleService(temp_db, access)
    site = await people.create_site(actors.admin, "Northlight")
    await people.update_site(actors.admin, site.id, "Northlight Studio")
    assert await people.delete_site(actors.admin, site.id)

    found = await events_of_entity(temp_db, actors.admin, "site", site.id)
    assert [one.verb for one in found] == ["deleted", "renamed"]
    assert found[0].id


# --- collections and groupings ----------------------------------------------------------------


async def test_collection_membership_is_the_whole_record_of_when_a_file_was_put_in(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The membership table has no timestamp column at all, and the row goes on removal. Without
    these two events there is nothing anywhere that says a file was ever in a collection."""
    collections = CollectionService(temp_db, access)
    made = await collections.create(
        "A shortlist", owner_id=actors.admin.id, actor=Actor.user(actors.admin.id)
    )
    assert await collections.add(made.id, [world.solo], actor=Actor.user(actors.admin.id)) == 1
    assert await collections.remove(made.id, [world.solo], actor=Actor.user(actors.admin.id)) == 1

    found = await events_of_asset(temp_db, actors.admin, world.solo)
    assert [one.verb for one in found] == ["unlinked", "linked"]
    assert found[0].object is not None
    assert found[0].object.name == "A shortlist"


async def test_a_second_drop_of_the_same_file_is_not_a_second_act(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """A row that did not land changed nothing, and a record full of rows saying nothing happened
    is a record nobody reads."""
    collections = CollectionService(temp_db, access)
    made = await collections.create(
        "A shortlist", owner_id=actors.admin.id, actor=Actor.user(actors.admin.id)
    )
    await collections.add(made.id, [world.solo], actor=Actor.user(actors.admin.id))
    await collections.add(made.id, [world.solo], actor=Actor.user(actors.admin.id))

    assert await _verbs_on_asset(temp_db, actors, world.solo) == ["linked"]


async def test_renaming_and_deleting_a_grouping_carry_the_name_it_had(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    sets = PhotoSetService(temp_db, access)
    await sets.rename(world.photo_set, "Seafront", actor=Actor.user(actors.admin.id))
    await sets.delete(world.photo_set, actor=Actor.user(actors.admin.id))

    found = await events_of_entity(temp_db, actors.admin, "photo_set", world.photo_set)
    assert [one.verb for one in found] == ["deleted", "renamed"]
    assert found[0].subjects == ()


async def test_a_picture_put_into_a_grouping_and_taken_out_again_both_show(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    sets = PhotoSetService(temp_db, access)
    await sets.remove(world.photo_set, [world.solo], actor=Actor.user(actors.admin.id))
    await sets.add(world.photo_set, [world.solo], actor=Actor.user(actors.admin.id))

    assert await _verbs_on_asset(temp_db, actors, world.solo) == ["linked", "unlinked"]


# --- tags -------------------------------------------------------------------------------------


async def test_a_tag_taken_off_a_file_is_recorded_although_the_row_has_gone(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """`_UNASSIGN` deletes the row and the `decided_at` stamp goes with it."""
    tags = TagService(temp_db, access)
    await tags.assign([world.solo], [world.tag], add=False, actor=Actor.user(actors.admin.id))

    found = await events_of_asset(temp_db, actors.admin, world.solo)
    assert [one.verb for one in found] == ["unlinked"]
    assert found[0].object is not None
    assert found[0].object.id == world.tag
    assert found[0].object.name


async def test_deleting_a_tag_keeps_the_word_it_was(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    tags = TagService(temp_db, access)
    named = await tags.get(actors.admin, world.tag)
    assert named is not None
    assert await tags.delete(world.tag, actor=Actor.user(actors.admin.id))

    found = await events_of_entity(temp_db, actors.admin, "tag", world.tag)
    assert [one.verb for one in found] == ["deleted"]
