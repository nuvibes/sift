# SPDX-License-Identifier: AGPL-3.0-or-later
"""What happened to a site, a tag, a shelf or a Photo Set, in order.

Four subjects through one shape, so what can go wrong here is mostly what the shape does with a
kind: a count landing under the wrong actor, a sentence naming the wrong thing, a grant an admin may
read reaching a guest, and the two thin ones quietly answering nothing at all because a statement
was wrong rather than because the table has no moment in it.

THE ONE ASYMMETRY IS THE SITE: its arrival line is drawn where there IS a moment and left out where
there is not, because `sites.created_at` alone is nullable: an older row's value was read off the
earliest signal it had, and a site with no signal has none. So both cases are here, and the KINDS
are asserted rather than the length.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

# Imported for its side effect: registering the tables the stash-box feature owns, so a kernel
# database has them. Every install creates them whether the feature is switched on or not, and the
# guarded case is proved by dropping them.
import sift.slices.stash_boxes.schema

# And the workbench's, for the same reason: the receipt source these four threads read is
# `workbench_decisions`, and a kernel database has the table only once the slice
# that owns it has registered.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository, Viewer
from sift.kernel.access.history import Actor, Event
from sift.kernel.access.history_entity import (
    history_count_of_entity,
    history_of_collection,
    history_of_photo_set,
    history_of_site,
    history_of_song,
    history_of_tag,
)
from sift.kernel.access.history_events import events_of_entity
from sift.kernel.access.history_person import history_of_person
from sift.kernel.db import Database
from sift.kernel.ledger import Actor as ActorOf
from sift.kernel.ledger import Object, record_event
from sift.kernel.records import Subject
from sift.kernel.secret_store import SecretStore
from sift.kernel.vocabulary import VIA_MUSIC_LOOKUP
from sift.kernel.vocabulary import Subject as LedgerSubject
from sift.slices.stash_boxes.service import StashBoxService
from sift.slices.tags_ratings.enrich import TagWriter
from sift.slices.tags_ratings.service import TagService
from sift.testing.fixtures import Actors, hide

pytestmark = pytest.mark.anyio

#: A fixed clock, so every assertion is about order rather than about when the test ran.
MADE_AT = 1_700_000_000
PUT_AT = MADE_AT + 100
LINKED_AT = MADE_AT + 200
SHARED_AT = MADE_AT + 300

TAG = "01HX0000000000000000000701"
SITE = "01HX0000000000000000000702"
COLLECTION = "01HX0000000000000000000703"
PHOTO_SET = "01HX0000000000000000000704"
BOX = "01HX0000000000000000000705"
USERNAME = "01HX0000000000000000000706"
MISSING = "01HX0000000000000000000799"

A_DAY = 86400

#: The root every file in this module sits loose in. See `make_file`.
LIBRARY_ROOT = "01HX0000000000000000000790"


async def make_file(database: Database, asset_id: str) -> None:
    await database.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
        " VALUES (?, ?, 1, 'video', ?)",
        (asset_id, f"digest-{asset_id}", MADE_AT),
    )
    # IN THE LIBRARY, loose in one root: a count of files counts only the files the reader may be
    # shown, and a file with no location is shown to nobody: the stored verdict has no row for
    # it. So a file here is where an admin sees it and a guest sees it only if shared.
    await database.execute(
        "INSERT OR IGNORE INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (LIBRARY_ROOT, "library", "/library", 0),
    )
    await database.execute(
        "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename,"
        " first_seen_at, last_seen_at) VALUES (?, ?, ?, NULL, ?, ?, 0, 0)",
        (f"loc-{asset_id}", asset_id, LIBRARY_ROOT, f"{asset_id}.mp4", f"{asset_id}.mp4"),
    )


async def make_tag(database: Database, name: str = "poolside") -> None:
    await database.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)", (TAG, name, MADE_AT)
    )


async def tag_a_file(
    database: Database, asset_id: str, *, source: str | None, at: int | None
) -> None:
    await make_file(database, asset_id)
    await database.execute(
        "INSERT INTO asset_tags (asset_id, tag_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (asset_id, TAG, source, at),
    )


async def make_site(database: Database, name: str = "Studio") -> None:
    await database.execute("INSERT INTO sites (id, name) VALUES (?, ?)", (SITE, name))


async def make_username(
    database: Database, username_id: str = USERNAME, *, name: str = "harlowquin", at: int = MADE_AT
) -> None:
    await database.execute(
        "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, ?)",
        (username_id, SITE, name, at),
    )


async def file_a_file(
    database: Database, asset_id: str, *, source: str | None, at: int | None
) -> None:
    await make_file(database, asset_id)
    await database.execute(
        "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (asset_id, USERNAME, source, at),
    )


async def make_box(database: Database, name: str = "StashDB") -> None:
    await database.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, ?)",
        (BOX, name, "https://example.invalid/graphql", MADE_AT),
    )


async def link_tag(database: Database) -> None:
    await make_box(database)
    await database.execute(
        "INSERT INTO tag_stash_box_links (tag_id, box_id, remote_id, payload, fetched_at)"
        " VALUES (?, ?, 'remote', '{}', ?)",
        (TAG, BOX, LINKED_AT),
    )


async def link_site(database: Database) -> None:
    await make_box(database)
    await database.execute(
        "INSERT INTO site_stash_box_links (site_id, box_id, remote_id, payload, fetched_at)"
        " VALUES (?, ?, 'remote', '{}', ?)",
        (SITE, BOX, LINKED_AT),
    )


async def make_collection(database: Database, *, owner: str | None) -> None:
    await database.execute(
        "INSERT INTO collections (id, name, owner_id, created_at) VALUES (?, ?, ?, ?)",
        (COLLECTION, "Best of", owner, MADE_AT),
    )


async def make_photo_set(database: Database, *, origin: str = "manual") -> None:
    await database.execute(
        "INSERT INTO photo_sets (id, name, origin, created_at) VALUES (?, ?, ?, ?)",
        (PHOTO_SET, "Pool shoot", origin, MADE_AT),
    )


async def grant(
    database: Database, object_type: str, object_id: str, *, user_id: str, effect: str = "share"
) -> None:
    await database.execute(
        "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (f"g-{object_type}-{effect}", object_type, object_id, user_id, effect, SHARED_AT),
    )


async def only_admin(access: Repository, actors: Actors) -> Viewer:
    """An admin reloaded from the database, so the role is whatever the row says."""
    loaded = await access.load_viewer(actors.admin.id)
    assert loaded is not None
    return loaded


# --- a tag ---------------------------------------------------------------------------------


async def test_a_tag_with_one_of_everything_comes_back_oldest_first(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    await make_tag(temp_db)
    await tag_a_file(temp_db, "01HX0000000000000000000710", source=None, at=PUT_AT)
    await link_tag(temp_db)
    await grant(temp_db, "tag", TAG, user_id=actors.guest.id)

    events = await history_of_tag(temp_db, await only_admin(access, actors), TAG)

    assert [(event.kind, event.at) for event in events] == [
        ("added", MADE_AT),
        ("tagged", PUT_AT),
        ("enriched", LINKED_AT),
        ("shared", SHARED_AT),
    ]
    assert events[0].what == "poolside was added to the library"
    assert events[1].what == "It was added to 1 file"
    # No run recorded who linked it, and nothing recorded who shared it: both passive, and the
    # guest is never the one who did it. A link with no run behind it says that honestly: the link
    # is older than the record of what a box fills in.
    assert events[2].what == "StashDB was linked before Sift recorded what a stash-box fills in"
    assert events[3].what.startswith("It was shared with guest ")


async def record_the_ask(database: Database, *, subject: str, local_id: str, applied: str) -> None:
    """What an ask FILLED IN: `enrichment_runs.applied`, version 52 of the catalog."""
    await database.execute(
        "INSERT INTO enrichment_runs (id, subject, local_id, box_id, at, automatic, applied)"
        " VALUES ('run-1', ?, ?, ?, ?, 0, ?)",
        (subject, local_id, BOX, LINKED_AT, applied),
    )


async def test_a_box_that_filled_a_tag_s_fields_in_names_them(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """ "its" and not "their": a tag is a thing. The person thread next door says the other word.

    The labels are the record registry's own, in the record's own order, so renaming a field
    renames this line rather than leaving a screen saying a word nothing else uses.
    """
    await make_tag(temp_db)
    await link_tag(temp_db)
    await record_the_ask(
        temp_db, subject="tag", local_id=TAG, applied='["description", "category"]'
    )

    events = await history_of_tag(temp_db, await only_admin(access, actors), TAG)

    assert (
        said_of(events)
        == "StashDB filled in its category and description when you applied its answer"
    )


async def test_a_box_that_filled_a_site_s_fields_in_says_so_the_same_way(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The same sentence on the other entity thread, which is the point of one reader for both."""
    await make_site(temp_db)
    await link_site(temp_db)
    await record_the_ask(temp_db, subject="site", local_id=SITE, applied='["details"]')

    events = await history_of_site(temp_db, await only_admin(access, actors), SITE)

    assert said_of(events) == "StashDB filled in its details when you applied its answer"


async def test_a_box_that_filled_nothing_in_says_so_rather_than_saying_nothing(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The empty list is a real outcome: linked, and everything was already here."""
    await make_tag(temp_db)
    await link_tag(temp_db)
    await record_the_ask(temp_db, subject="tag", local_id=TAG, applied="[]")

    events = await history_of_tag(temp_db, await only_admin(access, actors), TAG)

    assert said_of(events) == "You linked it to StashDB, which had nothing new to fill in"


async def test_a_tag_enriched_through_its_writer_draws_one_line_for_the_press(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The writer and the run together, as a press does them: ONE line, the box's.

    The tag writer writes no event of its own, so "Edited the aliases" is not drawn beside
    "StashDB filled in its aliases" for the same press: the `enriched` event written beside the run
    is the record.
    """
    await make_tag(temp_db)
    await link_tag(temp_db)
    landed = await TagWriter(TagService(temp_db, access)).write(
        TAG, {"aliases": ["seaside"]}, creating=False, actor=ActorOf.user(actors.admin.id)
    )
    boxes = StashBoxService(temp_db, SecretStore(temp_db), None)  # type: ignore[arg-type]
    await boxes.record_enrichment(
        Subject.TAG, TAG, BOX, automatic=False, applied=landed, pressed_by=actors.admin.id
    )

    events = await history_of_tag(temp_db, await only_admin(access, actors), TAG)

    assert [one.kind for one in events if one.kind in {"edited", "enriched"}] == ["enriched"]
    assert said_of(events).startswith("StashDB filled in its aliases")


async def test_two_boxes_of_one_name_do_not_hide_each_other_s_line(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The table line and the ledger's presses are matched by WHICH box, not by what it is called.

    Matched on the name, a press of one box would take the other same-named box's table line off
    the thread, so a link that stood would never be drawn.
    """
    other = "01HX0000000000000000000708"
    await make_tag(temp_db)
    await link_tag(temp_db)
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, ?)",
        (other, "StashDB", "https://other.example.invalid/graphql", MADE_AT),
    )
    await temp_db.execute(
        "INSERT INTO tag_stash_box_links (tag_id, box_id, remote_id, payload, fetched_at)"
        " VALUES (?, ?, 'remote', '{}', ?)",
        (TAG, other, LINKED_AT),
    )
    boxes = StashBoxService(temp_db, SecretStore(temp_db), None)  # type: ignore[arg-type]
    await boxes.record_enrichment(
        Subject.TAG, TAG, BOX, automatic=False, applied=["description"], pressed_by=actors.admin.id
    )

    events = await history_of_tag(temp_db, await only_admin(access, actors), TAG)

    assert sorted(one.box_id or "" for one in events if one.kind == "enriched") == sorted(
        [BOX, other]
    )


async def test_a_second_ask_that_filled_nothing_says_it_checked(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A Refresh re-fetches and fills nothing: it says the box had nothing new, not a new link.

    Drawing "Linked to StashDB" and "by hand" on every press would read as a link made over and
    over. The first press still says it linked.
    """
    await make_tag(temp_db)
    await link_tag(temp_db)
    moments = iter([LINKED_AT, LINKED_AT + A_DAY])
    boxes = StashBoxService(
        temp_db,
        SecretStore(temp_db),
        None,  # type: ignore[arg-type]
        clock=lambda: float(next(moments)),
    )
    for _ in range(2):
        await boxes.record_enrichment(
            Subject.TAG, TAG, BOX, automatic=False, pressed_by=actors.admin.id
        )

    events = await history_of_tag(temp_db, await only_admin(access, actors), TAG)

    assert [one.what for one in events if one.kind == "enriched"] == [
        "You linked it to StashDB",
        "You checked StashDB again and it had nothing new",
    ]


def said_of(events: Sequence[Event]) -> str:
    """The one `enriched` line on a thread."""
    said = [one.what for one in events if one.kind == "enriched"]
    assert len(said) == 1
    return said[0]


async def test_the_tag_names_itself_in_its_own_first_line(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The subject of its own history is linked all the same: the read does not know which screen
    is drawing it, and the same event is a row wherever a tag's thread is read."""
    await make_tag(temp_db)

    arrived = (await history_of_tag(temp_db, actors.admin, TAG))[0]

    assert [(one.kind, one.id, one.name) for one in arrived.links] == [("tag", TAG, "poolside")]


async def test_a_tag_that_is_not_there_has_no_history(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """An empty list rather than a line saying it exists. The route answers 404 above this."""
    assert await history_of_tag(temp_db, actors.admin, MISSING) == []


async def test_each_source_says_who_put_the_tag_on_and_which_of_the_three_ways(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """`via` is the `enriched:` filter's own word, and it is what separates a pass from a person on
    a row whose kind says only that a tag was put on."""
    await make_tag(temp_db)
    for index, source in enumerate((None, "stash_box", "something_later")):
        await tag_a_file(
            temp_db,
            f"01HX000000000000000000072{index}",
            source=source,
            at=PUT_AT + index * A_DAY,
        )

    said = {
        event.what: (event.actor, event.actor_name, event.via)
        for event in await history_of_tag(temp_db, actors.admin, TAG)
        if event.kind == "tagged"
    }

    assert said == {
        "It was added to 1 file": (Actor.SOMEBODY, None, None),
        "A stash-box added it to 1 file": (Actor.STASH_BOX, None, "stash"),
        "Sift added it to 1 file": (Actor.SIFT, "Sift", None),
    }


async def test_files_put_on_one_day_are_one_line_and_not_one_line_each(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A tag on four thousand files is not four thousand lines anybody can read."""
    await make_tag(temp_db)
    for index in range(3):
        await tag_a_file(temp_db, f"01HX000000000000000000073{index}", source=None, at=PUT_AT)

    tagged = [
        one for one in await history_of_tag(temp_db, actors.admin, TAG) if one.kind == "tagged"
    ]

    assert [one.what for one in tagged] == ["It was added to 3 files"]


async def test_a_tag_put_on_before_the_moment_was_recorded_has_no_time(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """`decided_at` is null for a row written before the column existed, and a made-up date on a
    screen somebody is reading to find out what really happened is the one thing a history may not
    do."""
    await make_tag(temp_db)
    await tag_a_file(temp_db, "01HX0000000000000000000740", source=None, at=None)

    tagged = next(
        one for one in await history_of_tag(temp_db, actors.admin, TAG) if one.kind == "tagged"
    )

    assert tagged.at is None


async def test_a_library_with_no_stash_boxes_still_has_a_tag_history(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A process that never imported the stash-box slice has never registered its schema, so the
    link table is genuinely absent and a statement naming it is a hard error, not an empty answer."""
    await make_tag(temp_db)
    for table in ("tag_stash_box_links", "site_stash_box_links"):
        # From the fixed tuple above, not from anything read: this is a test taking tables away to
        # prove the read stands without them.
        await temp_db.execute(f"DROP TABLE {table}")  # nosemgrep: sift-no-string-built-sql

    assert [one.kind for one in await history_of_tag(temp_db, actors.admin, TAG)] == ["added"]


async def test_who_a_tag_was_kept_from_is_an_admins_to_read(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Who else is a user on this install is not something a tag a guest may see discloses."""
    await make_tag(temp_db)
    await grant(temp_db, "tag", TAG, user_id=actors.guest.id, effect="restrict")

    admin = await history_of_tag(temp_db, await only_admin(access, actors), TAG)
    guest = await history_of_tag(temp_db, actors.guest, TAG)

    assert [one.kind for one in admin] == ["added", "shared"]
    assert admin[-1].what.startswith("It was kept private from guest ")
    assert [one.kind for one in guest] == ["added"]


# --- a site --------------------------------------------------------------------------------


async def test_a_site_with_no_moment_on_its_row_still_has_no_arrival_line(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A site with no moment has no arrival line.

    `sites.created_at` is nullable where every other one is not: an older row's value was read off
    the earliest signal it had, and a site with no usernames, no address and no stash-box link has
    no signal of any kind. A line dated to the moment of the upgrade would be a made-up date on a
    screen somebody is reading to find out what happened.
    """
    await make_site(temp_db)
    await make_username(temp_db)

    events = await history_of_site(temp_db, actors.admin, SITE)

    assert [one.kind for one in events] == ["filed"]
    # Named, not counted (`sentences.usernames_added`).
    assert events[0].what == "The username harlowquin was added to it"


async def test_a_site_that_knows_when_it_appeared_says_so_and_says_who_made_it(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The other half: an arrival line, at the site's own moment, naming its maker.

    `Actor.SIFT` here because a site reached through a download or a folder read is made by a pass
    for nobody in particular, which is what the unscoped write path in `kernel/access/catalog.py`
    records. A site somebody typed in reads `you`, and one made before v41 reads `somebody`.
    """
    await make_site(temp_db)
    await temp_db.execute(
        "UPDATE sites SET created_at = ?, created_by_kind = 'sift' WHERE id = ?",
        (MADE_AT - 60, SITE),
    )
    await make_username(temp_db)

    events = await history_of_site(temp_db, actors.admin, SITE)

    assert [one.kind for one in events] == ["added", "filed"]
    assert events[0].what == "Sift added Studio to the library"
    assert events[0].actor is Actor.SIFT


async def test_a_site_that_is_not_there_has_no_history(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    assert await history_of_site(temp_db, actors.admin, MISSING) == []


async def test_usernames_added_on_one_day_are_counted_together(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    await make_site(temp_db)
    await make_username(temp_db, "01HX0000000000000000000750", name="one")
    await make_username(temp_db, "01HX0000000000000000000751", name="two")

    events = await history_of_site(temp_db, actors.admin, SITE)

    assert [one.what for one in events] == ["The usernames one and two were added to it"]


async def test_the_usernames_a_site_gained_are_named_and_each_is_the_way_to_who_it_is(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Not "30 usernames added to it" with none of them named. Each is named, and pressing one
    goes where every username goes (`username_opens`): its person, or the files under it.

    Past five the line says "and N more", and the rest arrive as one detail group under exactly
    those words, which the row opens in place. The blank row (a filing's own "poster unknown")
    is not a username anybody added and is not said.
    """
    await make_site(temp_db)
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES ('01HX0000000000000000000790', ?, ?)",
        ("Neve Alder", MADE_AT),
    )
    for number in range(8):
        await make_username(temp_db, f"01HX00000000000000000007{60 + number}", name=f"name{number}")
    await temp_db.execute(
        "UPDATE usernames SET person_id = '01HX0000000000000000000790' WHERE name = 'name0'"
    )
    await make_username(temp_db, "01HX0000000000000000000770", name="")

    (line,) = await history_of_site(temp_db, actors.admin, SITE)

    assert line.what == (
        "The usernames name0, name1, name2, name3, name4 and 3 more were added to it"
    )
    # The first five are named where they sit; the "3 more" is ONE piece carrying the rest, which
    # the row opens in place: nothing is found by matching its words in the sentence.
    shown = [one for one in line.pieces if one.kind == "username"]
    assert [one.text for one in shown] == [f"name{number}" for number in range(5)]
    assert shown[0].href == "/people/01HX0000000000000000000790"
    assert shown[1].href == "/browse?username=01HX0000000000000000000761"
    (rest,) = [one for one in line.pieces if one.rest]
    assert rest.text == "3 more"
    assert [one.text for one in rest.rest if one.kind == "username"] == ["name5", "name6", "name7"]
    assert line.detail == ()


async def test_a_site_whose_only_row_is_the_blank_one_says_no_username_was_added(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Not "1 username added to it" on a site nobody added a username to: the blank row a filing
    makes for "poster unknown" is not counted as one."""
    await make_site(temp_db)
    await make_username(temp_db, name="")

    assert await history_of_site(temp_db, actors.admin, SITE) == []


async def test_each_source_says_who_filed_files_under_the_site(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The folder arm is here and not on a tag's: a folder read files a site, and nothing reads a
    folder name and reaches a tag."""
    await make_site(temp_db)
    await make_username(temp_db)
    for index, source in enumerate((None, "stash_box", "folder", "something_later")):
        await file_a_file(
            temp_db,
            f"01HX000000000000000000076{index}",
            source=source,
            at=PUT_AT + index * A_DAY,
        )

    said = {
        event.what: (event.actor, event.actor_name, event.via)
        for event in await history_of_site(temp_db, actors.admin, SITE)
        # The username line wears `filed` too, and names its usernames, so it is told apart by
        # what it names, not by a lowercase word it does not say.
        if event.kind == "filed" and not any(one.kind == "username" for one in event.links)
    }

    assert said == {
        "1 file was filed under it": (Actor.SOMEBODY, None, None),
        "A stash-box filed 1 file under it": (Actor.STASH_BOX, None, "stash"),
        "Sift filed 1 file under it from folder names": (Actor.SIFT, "Sift", "folder"),
        "Sift filed 1 file under it": (Actor.SIFT, "Sift", None),
    }


async def test_a_site_carries_its_stash_box_link_and_its_sharing(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    await make_site(temp_db)
    await link_site(temp_db)
    await grant(temp_db, "site", SITE, user_id=actors.guest.id)

    events = await history_of_site(temp_db, await only_admin(access, actors), SITE)

    assert [(one.kind, one.at, one.via) for one in events] == [
        ("enriched", LINKED_AT, "stash"),
        ("shared", SHARED_AT, None),
    ]
    # And a guest sees the link and not the sharing: who else is a user on this install is not
    # something a site they may see should disclose.
    guest = await history_of_site(temp_db, actors.guest, SITE)
    assert [one.kind for one in guest] == ["enriched"]


async def test_a_library_with_no_stash_boxes_still_has_a_site_history(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    await make_site(temp_db)
    await make_username(temp_db)
    for table in ("tag_stash_box_links", "site_stash_box_links"):
        await temp_db.execute(f"DROP TABLE {table}")  # nosemgrep: sift-no-string-built-sql

    assert [one.kind for one in await history_of_site(temp_db, actors.admin, SITE)] == ["filed"]


# --- a shelf -------------------------------------------------------------------------------


async def test_a_shelf_says_it_was_made_and_by_you_when_it_was_yours(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    await make_collection(temp_db, owner=actors.admin.id)

    events = await history_of_collection(temp_db, actors.admin, COLLECTION)

    assert [(one.kind, one.at, one.actor, one.what) for one in events] == [
        ("added", MADE_AT, Actor.YOU, "You created Best of")
    ]
    assert [(one.kind, one.id) for one in events[0].links] == [("collection", COLLECTION)]


async def test_a_shelf_somebody_else_made_names_nobody(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A guest is told a shelf was made without being told by whom, which is the same rule the
    file's history follows for a user that is not the viewer's."""
    await make_collection(temp_db, owner=actors.admin.id)

    made = (await history_of_collection(temp_db, actors.guest, COLLECTION))[0]

    assert (made.actor, made.actor_name) == (Actor.SOMEBODY, None)


async def test_a_shelf_with_no_owner_at_all_names_nobody(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """`owner_id` is `ON DELETE CASCADE` on the user and nullable besides, so a shelf can outlive
    any record of who made it."""
    await make_collection(temp_db, owner=None)

    made = (await history_of_collection(temp_db, actors.admin, COLLECTION))[0]

    assert made.actor is Actor.SOMEBODY


async def test_a_shelf_that_is_not_there_has_no_history(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    assert await history_of_collection(temp_db, actors.admin, MISSING) == []


async def test_a_shelfs_sharing_is_an_admins_to_read(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    await make_collection(temp_db, owner=actors.admin.id)
    await grant(temp_db, "collection", COLLECTION, user_id=actors.guest.id)

    admin = await history_of_collection(temp_db, await only_admin(access, actors), COLLECTION)
    guest = await history_of_collection(temp_db, actors.guest, COLLECTION)

    assert [one.kind for one in admin] == ["added", "shared"]
    assert [one.kind for one in guest] == ["added"]


# --- a Photo Set ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("origin", "actor", "said"),
    [
        ("manual", Actor.SOMEBODY, "Pool shoot was created"),
        ("download", Actor.SIFT, "Sift created Pool shoot from a download"),
        ("folder", Actor.SIFT, "Sift created Pool shoot from a folder"),
        ("archive", Actor.SIFT, "Sift created Pool shoot from an archive"),
    ],
)
async def test_how_a_photo_set_was_made_is_the_first_line(
    temp_db: Database,
    access: Repository,
    actors: Actors,
    origin: str,
    actor: Actor,
    said: str,
) -> None:
    """A set Sift assembled from a folder and one somebody put together by hand are different
    enough that the first line should say which. The words are the column's, which is a CHECK
    constraint, so this list is the whole of what the schema allows."""
    await make_photo_set(temp_db, origin=origin)

    made = (await history_of_photo_set(temp_db, actors.admin, PHOTO_SET))[0]

    assert (made.kind, made.at, made.actor, made.what) == ("added", MADE_AT, actor, said)
    assert made.actor_name == ("Sift" if actor is Actor.SIFT else None)


async def test_a_photo_set_made_from_a_folder_names_the_folder_where_it_may_be_seen(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """ "from the folder Beach days", linked to the folder; a folder since gone, or one the reader
    may not see all the way down, keeps "from a folder", the words the line has without one."""
    await make_photo_set(temp_db, origin="folder")
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES ('root-s', 'L', '/l', 0)"
    )
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name)"
        " VALUES ('folder-beach', 'root-s', NULL, 'Beach days', 'Beach days')"
    )
    await temp_db.execute(
        "UPDATE photo_sets SET folder_id = 'folder-beach' WHERE id = ?", (PHOTO_SET,)
    )

    made = (await history_of_photo_set(temp_db, actors.admin, PHOTO_SET, access=access))[0]
    assert made.what == "Sift created Pool shoot from the folder Beach days"
    # Linked by the folder's id, never its path: `in:` reads a path as every folder under it.
    assert ("folder", "folder-beach", "Beach days", "/browse?in=folder-beach") in [
        (one.kind, one.id, one.name, one.href) for one in made.links
    ]

    unseen = (await history_of_photo_set(temp_db, actors.guest, PHOTO_SET, access=access))[0]
    assert unseen.what.endswith(" from a folder")

    await temp_db.execute("DELETE FROM folders WHERE id = 'folder-beach'")
    made = (await history_of_photo_set(temp_db, actors.admin, PHOTO_SET, access=access))[0]
    assert made.what == "Sift created Pool shoot from a folder"


async def test_a_photo_set_names_itself(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    await make_photo_set(temp_db)

    made = (await history_of_photo_set(temp_db, actors.admin, PHOTO_SET))[0]

    assert [(one.kind, one.id, one.name) for one in made.links] == [
        ("photo_set", PHOTO_SET, "Pool shoot")
    ]


async def test_a_photo_set_that_is_not_there_has_no_history(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    assert await history_of_photo_set(temp_db, actors.admin, MISSING) == []


async def test_a_photo_sets_sharing_is_an_admins_to_read(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    await make_photo_set(temp_db)
    await grant(temp_db, "photo_set", PHOTO_SET, user_id=actors.guest.id)

    admin = await history_of_photo_set(temp_db, await only_admin(access, actors), PHOTO_SET)
    guest = await history_of_photo_set(temp_db, actors.guest, PHOTO_SET)

    assert [one.kind for one in admin] == ["added", "shared"]
    assert [one.kind for one in guest] == ["added"]


# --- the cap -------------------------------------------------------------------------------


async def test_the_cap_keeps_the_newest_so_a_long_thread_loses_its_beginning(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The same rule every other history reads by: a timeline reads down, and what somebody is
    looking for is what happened recently."""
    await make_tag(temp_db)
    for index in range(3):
        await tag_a_file(
            temp_db, f"01HX000000000000000000078{index}", source=None, at=PUT_AT + index * A_DAY
        )

    events = await history_of_tag(temp_db, actors.admin, TAG, limit=2)

    assert len(events) == 2
    assert events[-1].at == PUT_AT + 2 * A_DAY


async def test_a_limit_below_one_still_answers_with_something(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The route refuses it, and the read clamps rather than trusting that: a kernel function is
    callable from anywhere and a slice of zero would be a thread that draws nothing at all."""
    await make_tag(temp_db)

    assert len(await history_of_tag(temp_db, actors.admin, TAG, limit=0)) == 1


# --- THE WAY TO WHAT A NUMBER COUNTS, AND A BOX THAT WAS OVERRULED -------------------------------


async def test_a_tags_count_is_the_way_to_the_files_it_counted(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """ "Put on 4,000 files" with no way to those files is a number somebody then has to go and
    reproduce by hand. The link's name is the phrase the sentence already
    carries, so a client finds it exactly where it sits."""
    await make_tag(temp_db)
    await tag_a_file(temp_db, "01HX0000000000000000000750", source=None, at=PUT_AT)

    put = next(
        one for one in await history_of_tag(temp_db, actors.admin, TAG) if one.kind == "tagged"
    )

    assert put.what == "It was added to 1 file"
    # THIS LINE'S files, not every file the tag is on: by hand (no source), on its UTC day.
    assert [(one.kind, one.name, one.href) for one in put.links] == [
        ("files", "1 file", f"/browse?tagged={TAG}~~2023-11-14")
    ]


async def test_a_sites_count_is_the_way_to_what_is_filed_under_it(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The same for a site, through the facet the filter bar itself writes when a row is clicked."""
    await make_site(temp_db)
    await make_username(temp_db)
    await file_a_file(temp_db, "01HX0000000000000000000751", source=None, at=PUT_AT)

    filed = next(
        one
        for one in await history_of_site(temp_db, actors.admin, SITE)
        if one.kind == "filed" and any(link.kind == "files" for link in one.links)
    )

    assert filed.what == "1 file was filed under it"
    assert [(one.kind, one.href) for one in filed.links] == [
        ("files", f"/browse?filed={SITE}~~2023-11-14")
    ]


async def test_a_tag_s_and_a_site_s_counts_say_only_the_files_the_reader_may_be_shown(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """On the entity threads every count joins the stored verdict for the reader. A guest shown
    one of two files reads one; so does an admin with the other in the vault, shut."""
    shown, kept = "01HX0000000000000000000760", "01HX0000000000000000000761"
    await make_tag(temp_db)
    await tag_a_file(temp_db, shown, source=None, at=PUT_AT)
    await tag_a_file(temp_db, kept, source=None, at=PUT_AT + 1)
    await make_site(temp_db)
    await make_username(temp_db)
    for asset_id in (shown, kept):
        await temp_db.execute(
            "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at)"
            " VALUES (?, ?, NULL, ?)",
            (asset_id, USERNAME, PUT_AT),
        )
    # And a username whose only file is the one kept from the guest: a name that exists only on a
    # file they were not shown is the same disclosure as counting it.
    await make_username(temp_db, "01HX0000000000000000000707", name="quillmoss")
    await temp_db.execute(
        "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at)"
        " VALUES (?, ?, NULL, ?)",
        (kept, "01HX0000000000000000000707", PUT_AT),
    )
    await grant(temp_db, "item", shown, user_id=actors.guest.id)

    def said(events: list[Event], kind: str) -> list[str]:
        return [
            one.what
            for one in events
            if one.kind == kind and any(link.kind == "files" for link in one.links)
        ]

    assert said(await history_of_tag(temp_db, actors.guest, TAG), "tagged") == [
        "It was added to 1 file"
    ]
    guest_site = await history_of_site(temp_db, actors.guest, SITE)
    assert said(guest_site, "filed") == ["1 file was filed under it"]
    assert not [one for one in guest_site if "quillmoss" in one.what]
    admin_site = await history_of_site(temp_db, actors.admin, SITE)
    assert [one for one in admin_site if "quillmoss" in one.what]

    await hide(temp_db, "asset", kept, actors.admin.id)
    shut = await history_of_tag(temp_db, actors.admin, TAG)
    assert said(shut, "tagged") == ["It was added to 1 file"]
    assert said(await history_of_site(temp_db, actors.admin, SITE), "filed") == [
        "1 file was filed under it"
    ]


# --- A COUNT OPENS EXACTLY ITS OWN FILES --------------------------------------------------------
#
# A line saying 4 files opens those 4, not the 500 under the whole Site or tag: each counted line
# opens its own group, and these hold the spelling of that address.


async def test_two_days_of_filings_open_two_different_walls(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """One source on two days is two lines, and each line's number goes to its own day: the whole
    point. An undated filing (written before `decided_at` existed) is a third line whose address
    says so with an empty day, and a filing by hand says so with an empty source."""
    await make_site(temp_db)
    await make_username(temp_db)
    # A folder pass, not a download: a filing whose row says download is the download's own act
    # and is left to its download line (`_NOT_A_DOWNLOAD`).
    await file_a_file(temp_db, "01HX0000000000000000000761", source="folder", at=PUT_AT)
    await file_a_file(temp_db, "01HX0000000000000000000762", source="folder", at=PUT_AT + A_DAY)
    await file_a_file(temp_db, "01HX0000000000000000000763", source=None, at=None)

    hrefs = sorted(
        link.href or ""
        for one in await history_of_site(temp_db, actors.admin, SITE)
        for link in one.links
        if link.kind == "files"
    )

    assert hrefs == sorted(
        [
            f"/browse?filed={SITE}~~",
            f"/browse?filed={SITE}~folder~2023-11-14",
            f"/browse?filed={SITE}~folder~2023-11-15",
        ]
    )


async def test_a_stash_box_line_names_its_box_in_the_address(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A stash-box's lines are split by WHICH box as well, so its address carries the box: "StashDB
    put it on 3 files" and "A stash-box put it on 2 files" on one day are two different sets. The
    name is escaped like any value in an address."""
    await make_tag(temp_db)
    await make_box(temp_db, name="fansdb mirror")
    await tag_a_file(temp_db, "01HX0000000000000000000771", source="stash_box", at=PUT_AT)
    await temp_db.execute(
        "INSERT INTO asset_stash_box_matches (asset_id, box_id, remote_id, payload, grade, state,"
        " found_at) VALUES (?, ?, 'remote', '{}', 'certain', 'applied', ?)",
        ("01HX0000000000000000000771", BOX, PUT_AT),
    )
    # And one the box never matched, which the History says no box for.
    await tag_a_file(temp_db, "01HX0000000000000000000772", source="stash_box", at=PUT_AT)

    hrefs = sorted(
        link.href or ""
        for one in await history_of_tag(temp_db, actors.admin, TAG)
        for link in one.links
        if link.kind == "files"
    )

    assert hrefs == [
        f"/browse?tagged={TAG}~stash_box~2023-11-14",
        f"/browse?tagged={TAG}~stash_box~2023-11-14~fansdb%20mirror",
    ]
    # And the line names the box, because the match table is on this thread's list of feature
    # tables (`_FEATURE_TABLES`); without it every tag's line would say "A stash-box".
    said = sorted(one.what for one in await history_of_tag(temp_db, actors.admin, TAG))
    assert "fansdb mirror added it to 1 file" in said


def test_the_filing_box_is_the_history_box() -> None:
    """The filing leaves ask which box by the History's own expression, written out a second time
    because query text is never built from another module's fragment. This keeps the copies one
    expression: a box the leaf spelled differently from the line would open a different set."""
    import re

    from sift.kernel.access import constraints, history

    def folded(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    expression = folded(history.BOX_OF_A_ROW)
    assert folded(constraints._BOX_OF_THE_ROW) == expression
    for key in ("filed_under_box", "tagged_with_box", "named_as_box"):
        assert expression in folded(constraints.PREDICATES[key])


async def test_a_box_that_was_overruled_about_a_tag_says_which_field_was_kept(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """One line says a box was agreed to know this tag; this says which of its answers was turned
    down, read off `stash_box_kept.decided_at`."""
    await make_tag(temp_db)
    await make_box(temp_db)
    await temp_db.execute(
        "INSERT INTO stash_box_kept (subject, local_id, box_id, key, mine, theirs, decided_at)"
        " VALUES ('tag', ?, ?, 'description', 'mine', 'theirs', ?)",
        (TAG, BOX, LINKED_AT),
    )

    kept = next(
        one for one in await history_of_tag(temp_db, actors.admin, TAG) if one.kind == "kept_mine"
    )

    assert kept.what == "Your description, mine, was kept over StashDB's theirs"


async def test_a_receipt_about_a_tag_is_on_the_tag_s_thread_with_its_undo(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A judgement somebody took ON a tag (sitting on the Organize board with an Undo under it)
    is drawn on the tag's own page. Modelled on the person thread's receipt source, through the same
    `_decision_events`, so the Undo obeys the same three conditions."""
    await make_tag(temp_db)
    await temp_db.execute(
        "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload, decided_at)"
        " VALUES ('01HX0000000000000000000780', 'filenames', NULL, 'Tagged 12 files poolside',"
        " '', '', ?)",
        (PUT_AT,),
    )
    await temp_db.execute(
        "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
        " VALUES ('01HX0000000000000000000780', 'tag', ?, 'poolside')",
        (TAG,),
    )

    decided = [
        one for one in await history_of_tag(temp_db, actors.admin, TAG) if one.kind == "decided"
    ]

    assert [one.what for one in decided] == ["Tagged 12 files poolside"]
    assert decided[0].undo is not None


async def test_a_receipt_s_undo_is_withheld_where_its_queue_can_never_take_one_back(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The same rule a file's thread and a person's read by, and the reason it is the route that
    answers it: an affordance the server would refuse is worse than none."""
    await make_tag(temp_db)
    await temp_db.execute(
        "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload, decided_at)"
        " VALUES ('01HX0000000000000000000781', 'released', NULL, 'Released 3 copies', '', '', ?)",
        (PUT_AT,),
    )
    await temp_db.execute(
        "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
        " VALUES ('01HX0000000000000000000781', 'tag', ?, 'poolside')",
        (TAG,),
    )

    decided = next(
        one
        for one in await history_of_tag(temp_db, actors.admin, TAG, final_queues=["released"])
        if one.kind == "decided"
    )

    assert decided.undo is None


# --- an event read from its OBJECT's page -----------------------------------------------------


async def test_a_merge_read_on_the_survivor_names_what_was_merged_in(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The survivor is the merge's OBJECT, and its page must not say "Merged into poolside" of
    itself. Read from the object's side the line is turned round and names the other tag."""
    await make_tag(temp_db)
    other = "01HX0000000000000000000709"
    async with temp_db.write() as connection:
        await record_event(
            connection,
            actor=ActorOf.user(actors.admin.id),
            verb="merged",
            subject=LedgerSubject(kind="tag", id=other, name="seaside"),
            object=Object(kind="tag", id=TAG, name="poolside"),
        )

    events = await history_of_tag(temp_db, await only_admin(access, actors), TAG)

    merged = [one for one in events if "merged" in one.what]
    assert [one.what for one in merged] == ["You merged seaside into it"]


async def test_a_deleted_file_is_on_the_thread_of_a_person_it_was_on(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A delete names the file AND everything it was on as subjects, so the person's page reads it.

    The deletion writer lists the people, tags, Sites and shelves the file was on, so the
    event is reached from the person's side by the ordinary subject read: no writer change and
    no object-side read are needed. The line names the file, struck through.
    """
    person, clip = "01HX0000000000000000000720", "01HX0000000000000000000721"
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Ada Lumen', ?)", (person, MADE_AT)
    )
    async with temp_db.write() as connection:
        await record_event(
            connection,
            actor=ActorOf.user(actors.admin.id),
            verb="deleted",
            subject=[
                LedgerSubject(kind="asset", id=clip, name="beach-day.mp4"),
                LedgerSubject(kind="person", id=person, name="Ada Lumen"),
            ],
        )
    admin = await only_admin(access, actors)

    assert [one.verb for one in await events_of_entity(temp_db, admin, "person", person)] == [
        "deleted"
    ]
    lines = [
        one for one in await history_of_person(temp_db, admin, person) if one.kind == "deleted"
    ]
    assert len(lines) == 1
    assert "beach-day.mp4" in lines[0].what
    assert [(link.id, link.gone) for link in lines[0].links] == [(clip, True)]


# --- the stash-box tables gone, the record kept ----------------------------------------------


async def test_a_tag_and_a_site_count_their_files_where_the_stash_box_tables_are_not(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A process without the stash-box feature reads the statement that names no box, and still
    counts every file put on the tag and filed under the Site."""
    await make_tag(temp_db)
    await make_site(temp_db)
    await make_username(temp_db)
    for index in range(3):
        await tag_a_file(temp_db, f"01HX000000000000000000075{index}", source=None, at=PUT_AT)
    await file_a_file(temp_db, "01HX0000000000000000000760", source=None, at=PUT_AT)
    await temp_db.execute("DROP TABLE asset_stash_box_matches")

    tagged = [one.what for one in await history_of_tag(temp_db, actors.admin, TAG)]
    filed = [one.what for one in await history_of_site(temp_db, actors.admin, SITE)]

    assert "It was added to 3 files" in tagged
    assert "1 file was filed under it" in filed


# --- a song --------------------------------------------------------------------------------

SONG = "01HX0000000000000000000770"


async def make_song(
    database: Database, *, kind: str | None, via: str | None, user: str | None
) -> None:
    await database.execute(
        "INSERT INTO songs (id, name, created_at, created_by_kind, created_by_via,"
        " created_by_user_id) VALUES (?, 'Tidewater', ?, ?, ?, ?)",
        (SONG, MADE_AT, kind, via, user),
    )


async def name_song_on(
    database: Database, asset_id: str, *, source: str | None, at: int, came_from: str | None = None
) -> None:
    """A file carrying the song, called `<id>.mp4`, put on it with no act the ledger recorded."""
    await make_file(database, asset_id)
    await database.execute(
        "UPDATE assets SET original_filename = ? WHERE id = ?", (f"{asset_id[-3:]}.mp4", asset_id)
    )
    await database.execute(
        "INSERT INTO song_files (asset_id, song_id, source, from_asset_id, added_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (asset_id, SONG, source, came_from, at),
    )


async def test_a_song_says_every_file_it_was_named_on_by_day_and_by_where_the_name_came_from(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    await make_song(temp_db, kind="sift", via=VIA_MUSIC_LOOKUP, user=None)
    await name_song_on(temp_db, "01HX0000000000000000000781", source="acoustid", at=PUT_AT)
    await name_song_on(temp_db, "01HX0000000000000000000782", source="acoustid", at=PUT_AT + 9)
    await name_song_on(
        temp_db,
        "01HX0000000000000000000783",
        source="shared",
        at=PUT_AT + 20,
        came_from="01HX0000000000000000000781",
    )
    await name_song_on(temp_db, "01HX0000000000000000000784", source=None, at=PUT_AT + 30)
    await name_song_on(temp_db, "01HX0000000000000000000785", source="acoustid", at=PUT_AT + A_DAY)

    events = await history_of_song(temp_db, actors.admin, SONG)

    made, *named = events
    assert (made.kind, made.actor) == ("added", Actor.SIFT)
    assert made.what == "Sift created Tidewater from AcoustID"
    assert [(one.actor, one.what, one.at) for one in named] == [
        (Actor.SIFT, "Sift named this song on 781.mp4 and 782.mp4 from AcoustID", PUT_AT + 9),
        (
            Actor.SIFT,
            "Sift named this song on 783.mp4, from the same music as 781.mp4",
            PUT_AT + 20,
        ),
        (Actor.SOMEBODY, "This song was named on 784.mp4", PUT_AT + 30),
        (Actor.SIFT, "Sift named this song on 785.mp4 from AcoustID", PUT_AT + A_DAY),
    ]
    assert all(one.detail == () for one in named)
    assert await history_count_of_entity(temp_db, actors.admin, "song", SONG) == len(events)


async def test_a_day_of_many_files_on_a_song_counts_them_and_opens_to_the_newest(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Past fifty files a day's line counts them; its Show each lists them newest first, and past a
    hundred says it is the newest hundred."""
    await make_song(temp_db, kind="sift", via=VIA_MUSIC_LOOKUP, user=None)
    for index in range(101):
        await name_song_on(
            temp_db, f"01HX00000000000000000{index:05d}", source="acoustid", at=PUT_AT
        )
    for index in range(51):
        asset_id = f"01HX00000000000000001{index:05d}"
        await name_song_on(temp_db, asset_id, source="site", at=PUT_AT + A_DAY + index)

    _made, many, fewer = await history_of_song(temp_db, actors.admin, SONG)

    assert many.what == "Sift named this song on 101 files from AcoustID"
    [listed] = many.detail
    assert listed.words == "The newest 100 of 101 files"
    assert len(listed.links) == 100
    assert listed.links[0].id == "01HX0000000000000000000100"
    assert fewer.what == "Sift named this song on 51 files from their download pages"
    [all_of_them] = fewer.detail
    assert (all_of_them.words, len(all_of_them.links)) == ("51 files", 51)
    assert fewer.at == PUT_AT + A_DAY + 50


@pytest.mark.parametrize(
    ("kind", "is_you", "actor"),
    [("user", True, Actor.YOU), ("user", False, Actor.SOMEBODY), (None, False, Actor.SOMEBODY)],
)
async def test_a_song_somebody_made_says_you_only_to_the_user_who_made_it(
    temp_db: Database,
    access: Repository,
    actors: Actors,
    kind: str | None,
    is_you: bool,
    actor: Actor,
) -> None:
    maker = actors.admin.id if is_you else actors.guest.id
    await make_song(temp_db, kind=kind, via=None, user=maker if kind else None)

    [made] = await history_of_song(temp_db, actors.admin, SONG)

    assert made.actor is actor


async def test_a_song_that_is_not_there_has_no_history_and_one_without_the_record_has_its_arrival(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    assert await history_of_song(temp_db, actors.admin, MISSING) == []
    await make_song(temp_db, kind="sift", via=VIA_MUSIC_LOOKUP, user=None)
    await name_song_on(temp_db, "01HX0000000000000000000781", source="acoustid", at=PUT_AT)
    await temp_db.execute("DROP TABLE workbench_decision_subjects")
    await temp_db.execute("DROP TABLE workbench_decisions")

    assert [one.kind for one in await history_of_song(temp_db, actors.admin, SONG)] == ["added"]
