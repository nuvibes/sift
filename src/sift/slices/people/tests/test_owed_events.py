# SPDX-License-Identifier: AGPL-3.0-or-later
"""The eleven acts in this slice that change the library, and what each writes down.

`tests/gates/test_every_write_records_an_event.py` keeps writers that ARE acts apart from the
excuses, so that "not done yet" can never be filed as "this is not an act". Each of these is handed
the user who acted, and writes it.

**Each test reads the event BACK**, through the reader a screen uses rather than off the table, so
what is asserted is that the act is visible on the page somebody would go looking at, not merely
that a row landed. A row nothing can read is the same silence written down twice.

Only an admin is used, and that is not a gap: every one of these routes is admin-only, and what the
vault does to a ledger read has its own tests beside the reader (`kernel/tests/test_history_events`).
"""

from __future__ import annotations

import json

import pytest

# Imported for its side effect: registering the table the ledger is written to. A kernel or slice
# test process that has never imported the workbench slice genuinely does not have it.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository
from sift.kernel.access.history_events import (
    LedgerEvent,
    events_of_asset,
    events_of_entity,
    events_recent,
)
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, Object
from sift.kernel.vocabulary import VIA_STASH
from sift.slices.people.service import PeopleService
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio

A_PERSON = "Ilva Brennan"
A_SITE = "Larkspur"


@pytest.fixture
async def service(temp_db: Database, access: Repository) -> PeopleService:
    await temp_db.initialize_schema()
    return PeopleService(temp_db, access)


async def _a_person(temp_db: Database, name: str = A_PERSON) -> str:
    person = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (person, name)
    )
    return person


async def _a_site(temp_db: Database, name: str = A_SITE) -> str:
    site = new_id()
    await temp_db.execute("INSERT INTO sites (id, name) VALUES (?, ?)", (site, name))
    return site


async def _a_username(temp_db: Database, site_id: str, name: str = "harlowquin") -> str:
    username = new_id()
    await temp_db.execute(
        "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, 0)",
        (username, site_id, name),
    )
    return username


async def _about(temp_db: Database, actors: Actors, kind: str, entity_id: str) -> list[LedgerEvent]:
    return await events_of_entity(temp_db, actors.admin, kind, entity_id)  # type: ignore[arg-type]


def _said(events: list[LedgerEvent]) -> list[tuple[str | None, str | None]]:
    """Each event as its verb and the name its subject was called at the time.

    Read through `events_recent`, which is the feed: it is the one of the three reads that fills
    the subject list, because a per-entity pane already knows its entity and a whole-install feed
    has only what the rows say.
    """
    return [(one.verb, one.subjects[0].name if one.subjects else None) for one in events]


# --- what was removed -------------------------------------------------------------------------


async def test_an_alias_removed_says_which_name_went(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    """`people_aliases` keeps no moment and no tombstone, so without the event the other name
    somebody answered to would be erased along with the fact that they ever did."""
    person = await _a_person(temp_db)
    alias = await service.add_alias(person, "Ilva B")

    assert await service.remove_alias(person, alias.id, actor=Actor.user(actors.admin.id))

    (event,) = await _about(temp_db, actors, "person", person)
    assert event.verb == "removed"
    assert '"alias": "Ilva B"' in event.payload


async def test_a_link_removed_says_which_address_went(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    person = await _a_person(temp_db)
    link = await service.add_link(person, "https://example.test/ilva")
    assert link is not None

    assert await service.remove_link(person, link.id, actor=Actor.user(actors.admin.id))

    (event,) = await _about(temp_db, actors, "person", person)
    assert event.verb == "unlinked"
    assert "https://example.test/ilva" in event.payload


async def test_a_file_taken_off_a_username_names_the_username(
    service: PeopleService,
    temp_db: Database,
    access: Repository,
    world: World,
    actors: Actors,
) -> None:
    """A filing is a join row and a join row is erased on removal, so without the event "this
    came off there" would have no history at all once somebody took it back."""
    site = await _a_site(temp_db)
    username = await _a_username(temp_db, site)
    await temp_db.execute(
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)", (world.solo, username)
    )

    assert await service.unfile_from_username(
        world.solo, username, actor=Actor.user(actors.admin.id)
    )

    (event,) = await events_of_asset(temp_db, actors.admin, world.solo)
    assert (event.verb, event.object) is not None
    assert event.verb == "unlinked"
    assert event.object is not None and event.object.name == "harlowquin"


async def test_taking_a_file_off_a_username_it_is_not_on_records_nothing(
    service: PeopleService,
    temp_db: Database,
    access: Repository,
    world: World,
    actors: Actors,
) -> None:
    """The route is deliberately quiet where there was nothing to remove, so the record has to be
    too: a line describing an act that did not happen is worse than no line."""
    site = await _a_site(temp_db)
    username = await _a_username(temp_db, site)

    assert not await service.unfile_from_username(
        world.solo, username, actor=Actor.user(actors.admin.id)
    )
    assert await events_of_asset(temp_db, actors.admin, world.solo) == []


async def test_a_username_detached_names_the_person_it_was_joined_to(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    """The pointer is deleted rather than dated, so nothing else can say this username was ever
    joined to that person."""
    person = await _a_person(temp_db)
    site = await _a_site(temp_db)
    username = await _a_username(temp_db, site)
    await service.attach_username(
        username, person_id=person, as_alias=False, actor=Actor.user(actors.admin.id)
    )

    assert await service.detach_username(username, actor=Actor.user(actors.admin.id))

    # Both acts now, and the join first: the read is newest first, so it is the second.
    events = await _about(temp_db, actors, "username", username)
    assert [one.verb for one in events] == ["unlinked", "linked"]
    assert all(one.object is not None and one.object.name == A_PERSON for one in events)


async def test_a_username_joined_and_taken_off_is_on_the_persons_own_thread(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    """A username has no page of its own, so the PERSON's thread is where both acts are read. The
    join writes an event because the pointer has no moment and the next join overwrites it, so
    otherwise nothing anywhere could say this username was ever this person's."""
    person = await _a_person(temp_db)
    site = await _a_site(temp_db)
    username = await _a_username(temp_db, site)

    await service.attach_username(
        username, person_id=person, as_alias=False, actor=Actor.user(actors.admin.id)
    )
    await service.detach_username(username, actor=Actor.user(actors.admin.id))

    on_their_thread = await _about(temp_db, actors, "person", person)
    assert sorted(one.verb or "" for one in on_their_thread) == ["linked", "unlinked"]
    assert all(one.object is not None and one.object.id == person for one in on_their_thread)


# --- what was overwritten ------------------------------------------------------------------------


async def test_a_sites_addresses_saved_whole_say_what_it_ended_up_with(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    site = await _a_site(temp_db)

    await service.set_site_links(
        site, ["https://larkspur.test", "https://mirror.test"], actor=Actor.user(actors.admin.id)
    )

    (event,) = await _about(temp_db, actors, "site", site)
    assert event.verb == "edited"
    assert "https://mirror.test" in event.payload


async def test_a_sites_record_saved_whole_says_what_it_ended_up_with(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    site = await _a_site(temp_db)

    await service.set_site_record(
        site, aliases=["Larkspur Studios"], parent=None, actor=Actor.user(actors.admin.id)
    )

    (event,) = await _about(temp_db, actors, "site", site)
    assert event.verb == "edited"
    assert "Larkspur Studios" in event.payload


async def test_a_sites_record_save_names_only_what_moved(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    """A Site with no parent, saved with new other names, is not said to have had its parent
    edited too; saved again unchanged, it says nothing a second time."""
    site = await _a_site(temp_db)
    admin = Actor.user(actors.admin.id)

    await service.set_site_record(site, aliases=["Larkspur Studios"], parent=None, actor=admin)
    await service.set_site_record(site, aliases=["Larkspur Studios"], parent=None, actor=admin)

    (event,) = await _about(temp_db, actors, "site", site)
    assert json.loads(event.payload) == {"aliases": ["Larkspur Studios"]}


async def test_a_sites_details_saved_leave_a_line(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    site = await _a_site(temp_db)

    assert await service.update_site_details(site, "a note", actor=Actor.user(actors.admin.id))

    assert _said(await events_recent(temp_db, actors.admin)) == [("edited", A_SITE)]


async def test_a_record_filled_from_a_box_leaves_the_event_to_the_run(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    """The record writer writes NO `enriched` event: it knows neither the box nor the rest of what
    the ask landed. The run's writer does, naming both (see `StashBoxService.record_enrichment`
    and its tests). Written here, nameless, a thread could only fold it under the box's latest run,
    so two presses by one box would read as one."""
    person = await _a_person(temp_db)

    assert await service.merge_person_record(person, details="From a box", actor=Actor.box("b1"))

    assert [one.verb for one in await _about(temp_db, actors, "person", person)] == []


async def test_a_sites_record_filled_from_a_box_leaves_the_event_to_the_run(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    site = await _a_site(temp_db)

    assert await service.merge_site_record(site, details="From a box", actor=Actor.box("b1"))

    assert [one.verb for one in await _about(temp_db, actors, "site", site)] == []


# --- what was chosen ----------------------------------------------------------------------------


async def test_a_persons_cover_names_the_picture_it_was_set_to(
    service: PeopleService,
    temp_db: Database,
    access: Repository,
    world: World,
    actors: Actors,
) -> None:
    """The cover column keeps no history of what it held, so a picture chosen for somebody is
    invisible the moment the next one replaces it."""
    person = await _a_person(temp_db)

    assert await service.set_person_cover(person, world.solo, actor=Actor.user(actors.admin.id))

    (event,) = await _about(temp_db, actors, "person", person)
    assert event.verb == "edited"
    assert event.object is not None and event.object.id == world.solo


async def test_a_persons_cover_is_on_the_PICTURES_own_pane_too(
    service: PeopleService,
    temp_db: Database,
    access: Repository,
    world: World,
    actors: Actors,
) -> None:
    """The still is the OBJECT and the person the subject, so this reaches the file's own History
    only through the object-side read, which is what that read is for."""
    person = await _a_person(temp_db)

    await service.set_person_cover(person, world.solo, actor=Actor.user(actors.admin.id))

    assert [one.verb for one in await events_of_asset(temp_db, actors.admin, world.solo)] == [
        "edited"
    ]


async def test_a_cover_cleared_names_no_picture(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    """Clearing one is an act with nothing on the other end of it."""
    person = await _a_person(temp_db)

    await service.set_person_cover(person, None, actor=Actor.user(actors.admin.id))

    (event,) = await _about(temp_db, actors, "person", person)
    assert event.object is None
    # And says it was taken away: an `edited` with no object and no word would read "Edited" alone.
    assert json.loads(event.payload) == {"cover": "none"}


async def test_a_picture_sent_in_as_a_cover_says_so(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    """A picture uploaded, or fetched from a stash-box's answer, is no file in the library, so it
    cannot be the object, and the line must not read "Edited" alone."""
    person = await _a_person(temp_db)

    await service.set_person_cover(person, None, None, "upload-1", actor=Actor.sift(VIA_STASH))

    (event,) = await _about(temp_db, actors, "person", person)
    assert event.object is None
    assert json.loads(event.payload) == {"cover": "picture"}


async def test_a_picture_a_box_supplied_names_the_box(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    """ "Cover set to FansDB's picture", not "a new picture": the box is handed through to the
    write and lands in the payload beside the act."""
    person = await _a_person(temp_db)
    site = await _a_site(temp_db)

    await service.set_person_cover(
        person,
        None,
        None,
        "upload-1",
        actor=Actor.user(actors.admin.id),
        box=Object(kind="box", id="b1", name="Boxone"),
    )
    await service.set_site_cover(
        site, None, None, "upload-2", actor=Actor.sift(VIA_STASH), box=Object(kind="box", id="b1")
    )

    (event,) = await _about(temp_db, actors, "person", person)
    # The user who pressed Keep picture, not Sift.
    assert (event.actor_kind, event.actor_id) == ("user", actors.admin.id)
    assert json.loads(event.payload) == {"cover": "picture", "box": "Boxone", "box_id": "b1"}
    (on_site,) = await _about(temp_db, actors, "site", site)
    # A box whose name was not to hand is still named by id, and the line says "a new picture".
    assert json.loads(on_site.payload) == {"cover": "picture", "box_id": "b1"}


async def test_a_sites_cover_names_the_picture_it_was_set_to(
    service: PeopleService,
    temp_db: Database,
    access: Repository,
    world: World,
    actors: Actors,
) -> None:
    site = await _a_site(temp_db)

    assert await service.set_site_cover(site, world.solo, actor=Actor.user(actors.admin.id))

    (event,) = await _about(temp_db, actors, "site", site)
    assert event.verb == "edited"
    assert event.object is not None and event.object.id == world.solo


# --- what somebody THOUGHT of a person or a Site ------------------------------------------------
#
# The half of the record with something to lose: the heart and the stars say only what is true
# now, and a hide writes `hidden_at` that revealing clears, so the second write would erase the
# fact that the first ever happened. Each writes an `opinions` row
# instead, in its own transaction, carrying the value it replaced. NULL there is not zero: it means
# this user had never thought anything about this person at all.


async def _thought(database: Database, subject_id: str) -> list[tuple[str, str, object, object]]:
    """Every opinion about one thing, oldest first. `ORDER BY id` because the id is a ULID and a
    machine's wall clock can step backwards."""
    rows = await database.fetch_all(
        "SELECT subject_kind, kind, before, after FROM opinions WHERE subject_id = ? ORDER BY id",
        (subject_id,),
    )
    return [
        (str(row["subject_kind"]), str(row["kind"]), row["before"], row["after"]) for row in rows
    ]


async def test_hearting_a_person_says_what_the_heart_was(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    person = await _a_person(temp_db)

    await service.set_person_favorite(person, actors.admin.id, True)
    await service.set_person_favorite(person, actors.admin.id, False)

    assert await _thought(temp_db, person) == [
        ("person", "favorite", None, 1),
        # ZERO now rather than None: by the second press the row exists, so the heart really was on.
        ("person", "favorite", 1, 0),
    ]


async def test_rating_a_person_says_what_the_stars_were(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    person = await _a_person(temp_db)

    await service.set_person_rating(person, actors.admin.id, 7)
    await service.set_person_rating(person, actors.admin.id, 9)
    await service.set_person_rating(person, actors.admin.id, None)

    assert await _thought(temp_db, person) == [
        ("person", "rating", None, 7),
        ("person", "rating", 7, 9),
        ("person", "rating", 9, None),
    ]


async def test_hiding_somebody_and_bringing_them_back_both_leave_a_row(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    """`hidden_at` says when the CURRENT concealment began and nothing else: bringing somebody back
    clears it, and with it every trace that they were ever hidden."""
    person = await _a_person(temp_db)

    await service.set_person_vault(actors.admin, person, vault=True)
    await service.set_person_vault(actors.admin, person, vault=False)

    assert await _thought(temp_db, person) == [
        ("person", "hide", None, 1),
        ("person", "hide", 1, 0),
    ]


async def test_saving_a_persons_record_is_not_a_decision_about_hiding_them(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    """The record editor sends the vault checkbox on every save, so a rename re-asserts the same
    concealment, and a writer that appended a line for it would write a false one into a history.
    Only a change reaches the writer."""
    person = await _a_person(temp_db)

    await service.update_person(actors.admin, person, A_PERSON, vault=False, notes=None)

    assert await _thought(temp_db, person) == []


async def test_saving_a_persons_record_names_the_fields_the_save_moved(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    """The form sends every field on every press; the line names only what moved ("Edited" alone
    says nothing). A key whose value is what it already was is not an edit, and a save that moves
    nothing still records no field at all."""
    person = await _a_person(temp_db)
    await service.update_person(
        actors.admin, person, A_PERSON, vault=False, notes="typed", record={"country": "UA"}
    )
    await service.update_person(
        actors.admin, person, A_PERSON, vault=False, notes="typed", record={"country": "UA"}
    )

    edits = [one for one in await _about(temp_db, actors, "person", person) if one.verb == "edited"]

    assert [json.loads(one.payload) if one.payload else None for one in edits] == [
        None,
        {"fields": [{"field": "country"}, {"field": "details"}]},
    ]


async def test_hearting_a_site_says_what_the_heart_was(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    site = await _a_site(temp_db)

    await service.set_site_favorite(site, actors.admin.id, True)

    assert await _thought(temp_db, site) == [("site", "favorite", None, 1)]


async def test_rating_a_site_says_what_the_stars_were(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    site = await _a_site(temp_db)

    await service.set_site_rating(site, actors.admin.id, 4)
    await service.set_site_rating(site, actors.admin.id, 6)

    assert await _thought(temp_db, site) == [
        ("site", "rating", None, 4),
        ("site", "rating", 4, 6),
    ]


async def test_hiding_a_site_and_bringing_it_back_both_leave_a_row(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    site = await _a_site(temp_db)

    assert await service.set_site_vault(actors.admin, site, vault=True) is True
    assert await service.set_site_vault(actors.admin, site, vault=False) is True

    assert await _thought(temp_db, site) == [
        ("site", "hide", None, 1),
        ("site", "hide", 1, 0),
    ]


async def test_one_users_opinion_of_a_person_is_not_anothers(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    """Per user, like the row it is about: two people hearting one person is two histories, and
    neither can read the other's."""
    person = await _a_person(temp_db)

    await service.set_person_favorite(person, actors.admin.id, True)
    await service.set_person_favorite(person, actors.guest.id, True)

    rows = await temp_db.fetch_all(
        "SELECT user_id FROM opinions WHERE subject_id = ? ORDER BY id", (person,)
    )
    assert [str(row["user_id"]) for row in rows] == [actors.admin.id, actors.guest.id]


async def test_what_is_done_to_a_person_or_site_that_is_not_there_answers_false_and_says_nothing(
    service: PeopleService, temp_db: Database, actors: Actors
) -> None:
    """Deleted before the write landed: nothing was deleted, drawn or written about, so there is no
    act to write down and the answer says so."""
    gone = new_id()
    by = Actor.user(actors.admin.id)

    assert not await service.delete_person(actors.admin, gone)
    assert not await service.delete_site(actors.admin, gone)
    assert not await service.set_person_cover(gone, None, actor=by)
    assert not await service.set_site_cover(gone, None, actor=by)
    assert not await service.update_site_details(gone, "a note", actor=by)

    assert await events_recent(temp_db, actors.admin) == []


async def test_asking_for_the_facts_of_no_usernames_reads_nothing(service: PeopleService) -> None:
    assert await service.username_facts([]) == {}


def test_a_write_by_a_pass_of_sifts_tells_nobody_extra() -> None:
    """The writer always hears of its own write; a pass of Sift's has no user to tell, so the
    audience is what the grants said and nothing more."""
    from sift.kernel.audience import NOBODY, Audience
    from sift.slices.people.service import _and_the_actor

    assert _and_the_actor(NOBODY, Actor.user("acct-1")).users == frozenset({"acct-1"})
    told = Audience.of_user("acct-2")
    assert _and_the_actor(told, Actor.sift("folder")) == told
