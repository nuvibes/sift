# SPDX-License-Identifier: AGPL-3.0-or-later
"""The acts that were invisible, drawn on the threads they belong to, and drawn ONCE.

The event ledger records acts the catalog also records. A file tagged today has a row in
`asset_tags` with a moment on it AND an event beside it saying the same thing, so the question this
file exists to answer is not "does the event reach the pane" but "does the pane say it twice".

**The rule, and it is asserted both ways round on the same file.** While the link row stands, the
link table draws it and the event is dropped; the moment the row is gone (which is the whole
reason for the ledger, because a removal would otherwise erase its own history line), the making
and the taking back are both drawn. One line per act, whichever side of the removal the thread is
read from.

The rest are the acts nothing anywhere could say had happened: a field edited, a rename, a hide and
the showing again, a share taken back, a Photo Set a pass made out of this file.
"""

from __future__ import annotations

import json

import pytest

# Registering the tables a feature owns, so a kernel database has them. See `test_history.py`.
import sift.slices.download.schema
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Effect, ObjectType, Repository
from sift.kernel.access.history import Event, history_of_asset
from sift.kernel.access.history_entity import history_of_site, history_of_tag
from sift.kernel.access.history_person import history_of_person
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, Object, Reversal, record_event
from sift.kernel.vocabulary import VIA_STASH, Subject
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio

ADDED_AT = 1_700_000_000
TAG = "01HX0000000000000000000602"
PERSON = "01HX0000000000000000000603"
SET = "01HX0000000000000000000604"

#: Invented for this file; see `tests/gates/data/names_cast.txt`.
SOMEBODY = "Neve Alder"
A_TAG = "poolside"
#: The Site a download in this file came from, and the row on the queue that fetched it.
A_SITE = "Sunsetter"
A_USERNAME = "harlowquin"
A_DOWNLOAD = "01HX0000000000000000000605"
A_LINK = "example.invalid/x"


# THE FILE IS THE FIXTURE'S, and that is load-bearing rather than convenient: the ledger read
# filters through the STORED VERDICT (`viewer_assets`), which the visibility triggers keep true from
# a file's locations. An asset inserted with no location has no verdict row for anybody, so every
# event about it is correctly withheld, and a test written on one would prove nothing about the
# dedupe rule while passing.


async def make_tag(database: Database) -> None:
    """A second tag, which is deliberately NOT on the file: the world's own tag is."""
    await database.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)", (TAG, A_TAG, ADDED_AT)
    )


async def make_person(database: Database) -> None:
    await database.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)",
        (PERSON, SOMEBODY, ADDED_AT),
    )


async def event(database: Database, **named: object) -> str:
    async with database.write() as connection:
        return await record_event(connection, **named)  # type: ignore[arg-type]


def said(events: list[Event]) -> list[str]:
    return [one.what for one in events]


async def test_a_tag_that_is_still_on_the_file_is_drawn_once(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The link table draws it with its own moment, so the event beside it is not a second line."""
    await event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="linked",
        subject=Subject(kind="asset", id=world.solo),
        object=Object(kind="tag", id=world.tag, name="tag"),
    )

    events = await history_of_asset(temp_db, access, actors.admin, world.solo)

    assert [one.kind for one in events].count("tagged") == 1
    assert [one.kind for one in events].count("removed") == 0


async def test_a_tag_that_has_been_taken_off_is_drawn_from_both_events(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The other half of the same rule, and the whole reason the ledger exists.

    The row is gone, so nothing else in the database remembers the tag was ever there, and the
    thread reads as the two acts it was, in the order they happened.
    """
    await make_tag(temp_db)
    for verb in ("linked", "unlinked"):
        await event(
            temp_db,
            actor=Actor.user(actors.admin.id),
            verb=verb,
            subject=Subject(kind="asset", id=world.solo),
            object=Object(kind="tag", id=TAG, name=A_TAG),
        )

    events = await history_of_asset(temp_db, access, actors.admin, world.solo)

    assert said(events)[-2:] == [
        f"You added the tag {A_TAG} to this file",
        f"You removed the tag {A_TAG} from this file",
    ]
    assert [one.links[0].name for one in events[-2:]] == [A_TAG, A_TAG]


async def test_a_tag_that_was_deleted_is_named_in_words_with_nowhere_to_go(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """An event outlives its subject, so the name is the snapshot and there is no link on it."""
    await event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="unlinked",
        subject=Subject(kind="asset", id=world.solo),
        object=Object(kind="tag", id=TAG, name=A_TAG),
    )

    events = await history_of_asset(temp_db, access, actors.admin, world.solo)

    assert said(events)[-1] == f"You removed the tag {A_TAG} from this file"
    assert events[-1].links == ()


async def test_an_edit_names_the_fields_the_save_moved(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The one fact nothing else in the database keeps: the record is overwritten in place."""
    await event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="edited",
        subject=Subject(kind="asset", id=world.solo),
        payload=json.dumps({"fields": [{"field": "title"}, {"field": "release_date"}]}),
    )

    events = await history_of_asset(temp_db, access, actors.admin, world.solo)

    assert said(events)[-1] == "You edited the title and the release date"
    assert events[-1].kind == "edited"


async def test_an_edit_past_three_fields_counts_them_and_lists_every_one_under_the_line(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """Not "Edited 5 of its details" with the five keys in hand and nowhere to see them: the line
    counts and the row's "Show each" lists them, the way a box's "filled in" line does."""
    moved = ["title", "details", "release_date", "site_code", "notes"]
    await event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="edited",
        subject=Subject(kind="asset", id=world.solo),
        payload=json.dumps({"fields": [{"field": one} for one in moved]}),
    )

    events = await history_of_asset(temp_db, access, actors.admin, world.solo)

    assert said(events)[-1] == "You edited 5 details"
    (group,) = events[-1].detail
    assert group.words == "5 details"
    assert [one.name for one in group.links] == [
        "title",
        "details",
        "release date",
        "site code",
        "notes",
    ]
    assert {one.kind for one in group.links} == {"field"}


async def test_a_sites_edit_names_its_fields_off_the_payloads_keys(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """A site's writer records what the site ended up with, under the record's own keys; the line
    reads more than "Edited" alone."""
    await event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="edited",
        subject=Subject(kind="site", id=world.site),
        payload=json.dumps({"aliases": ["fy"], "parent": "Fansly"}),
    )

    events = await history_of_site(temp_db, actors.admin, world.site)

    assert said(events)[-1] == "You edited the other names and the parent Site"


async def test_a_hide_and_a_reveal_are_drawn_only_for_the_user_that_made_them(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """Concealment is per user and invisible to everybody else, which the record has to keep.

    An admin's own two acts read as the two acts they were, and the `hidden_at` column's line is
    not drawn beside them, because it is the same concealment said with less in it.
    """
    for verb in ("hidden", "revealed"):
        await event(
            temp_db,
            actor=Actor.user(actors.admin.id),
            verb=verb,
            subject=Subject(kind="asset", id=world.solo, name="clip.mp4"),
        )
    await event(
        temp_db,
        actor=Actor.user(actors.guest.id),
        verb="hidden",
        subject=Subject(kind="asset", id=world.solo, name="clip.mp4"),
    )

    events = await history_of_asset(temp_db, access, actors.admin, world.solo)

    assert said(events)[-2:] == ["You hid this file", "You unhid this file"]
    assert said(events).count("You hid this file") == 1


async def test_a_share_is_the_grant_s_line_while_the_grant_stands(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """A grant IS a link row: `_GRANTS` draws it while it is there, and the event when it is not."""
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)
    await event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="shared",
        subject=Subject(kind="asset", id=world.solo),
        object=Object(kind="login", id=actors.guest.id, name="guest"),
    )

    standing = await history_of_asset(temp_db, access, actors.admin, world.solo)
    assert [one.kind for one in standing].count("shared") == 1

    await access.revoke(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)
    await event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="unshared",
        subject=Subject(kind="asset", id=world.solo),
        object=Object(kind="login", id=actors.guest.id, name="guest"),
    )

    revoked = await history_of_asset(temp_db, access, actors.admin, world.solo)
    assert said(revoked)[-2:] == [
        "You shared this file with guest guest",
        "You stopped sharing this file with guest guest",
    ]


async def test_a_share_is_not_disclosed_to_a_guest(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """Who else is a user here is not something a file a guest may see should say."""
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)
    await event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="unshared",
        subject=Subject(kind="asset", id=world.solo),
        object=Object(kind="login", id=actors.guest.id, name="guest"),
    )

    events = await history_of_asset(temp_db, access, actors.guest, world.solo)

    assert [one.kind for one in events].count("shared") == 0


async def test_a_receipt_that_carries_a_verb_says_what_the_act_was(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The pattern for an area that writes a title: the words come out of the table.

    The receipt keeps its Undo and its place; what changes is where its sentence comes from, which
    is what makes the wording improvable instead of frozen in a column.
    """
    await temp_db.execute(
        "INSERT INTO photo_sets (id, name, name_sort, origin, created_at) VALUES (?, ?, ?, ?, ?)",
        (SET, "a post", "a post", "filename", ADDED_AT),
    )
    async with temp_db.write() as connection:
        await record_event(
            connection,
            actor=Actor.sift("filename"),
            verb="added",
            subject=[
                Subject(kind="photo_set", id=SET, name="a post"),
                Subject("asset", world.solo),
            ],
            receipt=Reversal(
                queue="filenames",
                title="Made a Photo Set of 2 pictures posted together",
                detail="",
            ),
        )

    events = await history_of_asset(temp_db, access, actors.admin, world.solo)

    assert said(events)[-1] == "Sift added this file to a post from the file's name"
    assert events[-1].links[0].kind == "photo_set"
    assert events[-1].undo is not None


async def test_a_person_s_thread_says_what_they_used_to_be_called(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A rename overwrites the column, so the old name is in the event or nowhere at all."""
    await make_person(temp_db)
    await event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="renamed",
        subject=Subject(kind="person", id=PERSON, name=SOMEBODY),
        payload=json.dumps({"before": "Neve Alderman"}),
    )

    events = await history_of_person(temp_db, actors.admin, PERSON)

    assert said(events)[-1] == "You renamed them from Neve Alderman"


async def test_a_tag_s_own_thread_says_it_was_kept_local(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A flag is a state and this is a decision: the column cannot say when, or who said so."""
    await make_tag(temp_db)
    await event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="kept_local",
        subject=Subject(kind="tag", id=TAG, name=A_TAG),
    )

    events = await history_of_tag(temp_db, actors.admin, TAG)

    assert said(events)[-1] == "You turned off stash-box lookups for it"
    assert events[-1].kind == "kept_local"


async def test_an_arrival_is_the_arrival_line_s_and_is_not_said_twice(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Every one of the three readers opens with an arrival read off the row's own moment."""
    await make_person(temp_db)
    await event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="added",
        subject=Subject(kind="person", id=PERSON, name=SOMEBODY),
    )

    events = await history_of_person(temp_db, actors.admin, PERSON)

    assert said(events) == [f"{SOMEBODY} was added to the library"]


# --- A FILE THAT WENT, ON THE PAGES OF EVERYTHING IT WAS ON --------------------------------------
#
# The delete event names the file AND what the file was on, which is what puts the line on those
# pages: an event is found by the things it names, and a delete naming only the file would appear
# only on the record of something that no longer has one. The file is named in the line and is
# never a link, because there is nothing at the end of one.

A_FILE = "beach-day.mp4"


async def deleted(database: Database, actor_id: str, *on: Subject, more: int = 0) -> str:
    """One file's delete, as the delete service writes it: the file first, then what it was on."""
    return await event(
        database,
        actor=Actor.user(actor_id),
        verb="deleted",
        subject=[Subject(kind="asset", id=GONE, name=A_FILE), *on],
        payload=json.dumps({"more": more}) if more else None,
    )


#: A file that is not the world's, because it has been deleted: there is no row and no location.
GONE = "01HX0000000000000000000605"


async def test_somebody_s_thread_says_a_file_of_theirs_was_deleted(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The line only the event can draw on the person's page: the file is gone, and so is every
    row that said they were ever on it, so the event is the only place either fact survives."""
    await make_person(temp_db)
    await deleted(temp_db, actors.admin.id, Subject(kind="person", id=PERSON, name=SOMEBODY))

    events = await history_of_person(temp_db, actors.admin, PERSON)

    assert said(events)[-1] == f"You deleted their file {A_FILE}"


async def test_the_file_a_delete_names_is_drawn_and_never_linked(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A link built from the kind and the id would land on "no such file", which reads as a broken
    screen. The mention is sent so the words can be struck through, and it carries no way there."""
    await make_person(temp_db)
    await deleted(temp_db, actors.admin.id, Subject(kind="person", id=PERSON, name=SOMEBODY))

    links = (await history_of_person(temp_db, actors.admin, PERSON))[-1].links

    assert [(one.kind, one.name, one.gone, one.href) for one in links] == [
        ("asset", A_FILE, True, None)
    ]


async def test_a_tag_says_it_in_its_own_words(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Four kinds of page draw this act at one vantage, and a tag is not a person to a file."""
    await make_tag(temp_db)
    await deleted(temp_db, actors.admin.id, Subject(kind="tag", id=TAG, name=A_TAG))

    events = await history_of_tag(temp_db, actors.admin, TAG)

    assert said(events)[-1] == f"You deleted {A_FILE}, which had this tag"


async def test_a_delete_is_not_folded_away_by_the_rule_that_holds_the_link_verbs(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The line an entity's counted source owns is the one reached from the OBJECT side, and a
    delete names nothing as its object: everything it names is what the act was about. A rule that
    dropped it would take away the only line a person has about a file of theirs going."""
    await make_person(temp_db)
    await deleted(temp_db, actors.admin.id, Subject(kind="person", id=PERSON, name=SOMEBODY))

    events = await history_of_person(temp_db, actors.admin, PERSON)

    assert [one.kind for one in events if one.kind == "deleted"] == ["deleted"]


# --- a download, which is the only act with a source of its own on one page and none on the other -
#
# A file that Sift fetched carries "Downloaded from <username> on <Site>" off the queue's own row,
# which knows the username. The event beside it would be a second, poorer copy of that line, so it
# is kept off the file's page and drawn on the Site's, where nothing else says that anything was
# fetched from it. A failure has no file at all: the Site's page and the feed are the only places
# it can ever appear.


async def landed(database: Database, asset_id: str) -> None:
    """The queue's own row for a download that landed, which is what the file's thread reads."""
    await database.execute(
        "INSERT INTO downloads (id, url, url_hash, state, site, username, asset_id,"
        " created_at, finished_at) VALUES (?, ?, 'h', 'done', ?, ?, ?, ?, ?)",
        (new_id(), f"https://{A_LINK}", A_SITE, A_USERNAME, asset_id, ADDED_AT, ADDED_AT + 5),
    )


async def a_download_event(database: Database, world: World, *, verb: str) -> str:
    """The event the download job writes, either way it can end.

    Two subjects and the Site as the object, which is the shape the writer uses: the first subject
    is what it is about, the Site is there so the Site's own thread can find it, and the object is
    what the file's line would name and link if the file's line were this one.
    """
    about = (
        Subject(kind="asset", id=world.solo, name=A_FILE)
        if verb == "downloaded"
        else Subject(kind="download", id=A_DOWNLOAD, name=A_LINK)
    )
    return await event(
        database,
        actor=Actor.sift("download"),
        verb=verb,
        subject=[about, Subject(kind="site", id=world.site, name=A_SITE)],
        object=Object(kind="site", id=world.site, name=A_SITE),
        payload=json.dumps({"code": "http-404"} if verb == "download_failed" else {}),
    )


async def test_a_download_that_landed_says_one_line_on_the_file_and_it_is_the_queue_s(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """Both sources are present and the thread says it once, in the words that know the username."""
    await landed(temp_db, world.solo)
    await a_download_event(temp_db, world, verb="downloaded")

    events = await history_of_asset(temp_db, access, actors.admin, world.solo)

    assert [one for one in said(events) if " downloaded " in one] == [
        f"Sift downloaded this file from {A_USERNAME} on {A_SITE}"
    ]


async def test_a_site_says_a_file_was_downloaded_from_it(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The line nothing else can say: a Site's own thread, and what came off it."""
    await a_download_event(temp_db, world, verb="downloaded")

    events = await history_of_site(temp_db, actors.admin, world.site)

    assert "Sift downloaded a file from it" in said(events)


async def downloaded_at(database: Database, world: World, asset_id: str, name: str, at: int) -> str:
    """One landed download of `asset_id`, written by the writer's shape and moved to `at`.

    The ledger stamps the moment itself, so a test about DAYS moves it afterwards: two events
    written a millisecond apart around midnight would otherwise fall on two days by chance.
    """
    written = await event(
        database,
        actor=Actor.sift("download"),
        verb="downloaded",
        subject=[
            Subject(kind="asset", id=asset_id, name=name),
            Subject(kind="site", id=world.site, name=A_SITE),
        ],
        object=Object(kind="site", id=world.site, name=A_SITE),
        payload="{}",
    )
    await database.execute(
        "UPDATE workbench_decisions SET decided_at = ? WHERE id = ?", (at, written)
    )
    return written


async def test_a_day_of_downloads_is_one_line_that_opens_to_its_files(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """Nine "A file was downloaded from it" in a row say nothing the first does not. A day is one
    line counting them, with the day's span and the files under it; a day
    with one download keeps the single sentence and still opens to its one file."""
    day = (ADDED_AT // 86400) * 86400
    await downloaded_at(temp_db, world, world.solo, A_FILE, day + 100)
    await downloaded_at(temp_db, world, world.twin, "twin.mp4", day + 200)
    await downloaded_at(temp_db, world, world.loose, "loose.mp4", day + 86400 + 50)

    events = [
        one
        for one in await history_of_site(temp_db, actors.admin, world.site)
        if one.kind == "downloaded"
    ]

    assert [(one.what, one.since, one.at) for one in events] == [
        ("Sift downloaded 2 files from it", day + 100, day + 200),
        ("Sift downloaded a file from it", None, day + 86400 + 50),
    ]
    assert [
        (group.words, [(link.kind, link.id, link.name) for link in group.links])
        for one in events
        for group in one.detail
    ] == [
        ("2 files", [("asset", world.solo, A_FILE), ("asset", world.twin, "twin.mp4")]),
        ("a file", [("asset", world.loose, "loose.mp4")]),
    ]


async def test_a_site_says_a_download_from_it_failed(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """A failure produces no file, so this page and the feed are the only places it can appear."""
    await a_download_event(temp_db, world, verb="download_failed")

    events = await history_of_site(temp_db, actors.admin, world.site)

    assert "Sift could not download a file from it because it answered 404 Not Found" in said(
        events
    )
    assert [one.kind for one in events if one.kind == "download_failed"] == ["download_failed"]


async def test_a_download_that_gave_up_names_no_file_anywhere(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The subject is the queue row and never the file, which is the honest shape: nothing arrived.

    Asserted on the file the same job would have produced, because the failure mode this guards is
    a writer reaching for the asset id it has in hand, which would put "could not be downloaded"
    on the history of a file that is sitting in the library.
    """
    await a_download_event(temp_db, world, verb="download_failed")

    events = await history_of_asset(temp_db, access, actors.admin, world.solo)

    assert [one for one in said(events) if "download" in one.lower()] == []


#: A stash-box for the enrichment below, and what one press of the chooser wrote. The applied object
#: is the shape `take_fields` records: nine single fields and five other names.
A_BOX_ID = "01HX0000000000000000000606"
ONE_PRESS = json.dumps(
    {
        "birth_date": 1,
        "country": 1,
        "hair_color": 1,
        "measurements": 1,
        "gender": 1,
        "ethnicity": 1,
        "eye_color": 1,
        "height_cm": 1,
        "breast_type": 1,
        "aliases": 5,
    }
)


async def enriched_by_hand(database: Database, admin_id: str, *, linked: bool = True) -> None:
    """One press of Take what is ticked, as the three writers leave it: the box's link and its run,
    the person writer's `enriched` event, and the picture the box's answer carried as the cover."""
    await database.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, 'FansDB', ?, ?)",
        (A_BOX_ID, "https://example.invalid/graphql", ADDED_AT),
    )
    if linked:
        await database.execute(
            "INSERT INTO person_stash_box_links (person_id, box_id, remote_id, payload, fetched_at)"
            " VALUES (?, ?, 'remote', '{}', ?)",
            (PERSON, A_BOX_ID, ADDED_AT + 10),
        )
    await database.execute(
        "INSERT INTO enrichment_runs (id, subject, local_id, box_id, at, automatic, applied)"
        " VALUES ('run-1', 'person', ?, ?, ?, 0, ?)",
        (PERSON, A_BOX_ID, ADDED_AT + 10, ONE_PRESS),
    )
    await event(
        database,
        actor=Actor.user(admin_id),
        verb="enriched",
        subject=Subject(kind="person", id=PERSON, name=SOMEBODY),
    )
    await event(
        database,
        actor=Actor.sift(VIA_STASH),
        verb="edited",
        subject=Subject(kind="person", id=PERSON, name=SOMEBODY),
        payload=json.dumps({"cover": "picture"}),
    )


async def test_one_enrichment_press_is_one_line_that_names_every_field(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Not three lines for one press: "FansDB filled in 10 details", "Enriched from something" and
    "Edited". One line says it, and opens to every field; the cover the
    box's picture became says what it was."""
    await make_person(temp_db)
    await enriched_by_hand(temp_db, actors.admin.id)

    events = await history_of_person(temp_db, actors.admin, PERSON)
    lines = said(events)

    box = [one for one in events if one.kind == "enriched"]
    assert [one.what for one in box] == ["FansDB filled in 10 details when you applied its answer"]
    (group,) = box[0].detail
    assert group.words == "10 details"
    assert group.kind == "field"
    # Every one of them, in the record's order and its words, the list's count included.
    assert [one.name for one in group.links] == [
        "5 aliases",
        "birthdate",
        "nationality",
        "hair color",
        "measurements",
        "gender",
        "ethnicity",
        "eye color",
        "height",
        "breast type",
    ]
    assert not any(one.startswith("Enriched from") for one in lines)
    assert "You filled in their details from a stash-box" not in lines
    assert "Sift chose a new picture as their cover" in lines
    assert "You edited them" not in lines


#: What the box answered about SOMEBODY, as a link keeps it: the record the values are read from.
KEPT_ANSWER = json.dumps(
    [
        {
            "fields": {
                "birth_date": "1991-02-02",
                "gender": "FEMALE",
                "aliases": ["Neve A", "N. Alder"],
            }
        }
    ]
)


async def test_a_box_line_names_what_it_filled_in_with_the_values(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The line names WHICH aliases and WHAT value each field took,
    read from the box's kept answer and the rows the run added in its own moment."""
    await make_person(temp_db)
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, 'FansDB', ?, ?)",
        (A_BOX_ID, "https://example.invalid/graphql", ADDED_AT),
    )
    await temp_db.execute(
        "INSERT INTO person_stash_box_links (person_id, box_id, remote_id, payload, fetched_at)"
        " VALUES (?, ?, 'remote', ?, ?)",
        (PERSON, A_BOX_ID, KEPT_ANSWER, ADDED_AT + 10),
    )
    await temp_db.execute(
        "UPDATE people SET gender = 'FEMALE', birth_date = '1991-02-02' WHERE id = ?", (PERSON,)
    )
    for alias in ("Neve A", "N. Alder", "typed later"):
        await temp_db.execute(
            "INSERT INTO people_aliases (id, person_id, alias, added_at) VALUES (?, ?, ?, ?)",
            (new_id(), PERSON, alias, ADDED_AT + (10 if alias != "typed later" else 9_000)),
        )
    await temp_db.execute(
        "INSERT INTO enrichment_runs (id, subject, local_id, box_id, at, automatic, applied)"
        " VALUES ('run-1', 'person', ?, ?, ?, 1, ?)",
        (PERSON, A_BOX_ID, ADDED_AT + 10, json.dumps({"gender": 1, "aliases": 2})),
    )

    lines = said(await history_of_person(temp_db, actors.admin, PERSON))

    assert (
        "FansDB filled in their 2 aliases (Neve A and N. Alder) and gender (Female) automatically"
        in lines
    )


async def test_a_link_older_than_the_run_record_says_so_and_what_agrees_today(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A link with no run says when it was made in the words of the fact, never "no record"."""
    await make_person(temp_db)
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, 'FansDB', ?, ?)",
        (A_BOX_ID, "https://example.invalid/graphql", ADDED_AT),
    )
    await temp_db.execute(
        "INSERT INTO person_stash_box_links (person_id, box_id, remote_id, payload, fetched_at)"
        " VALUES (?, ?, 'remote', ?, ?)",
        (PERSON, A_BOX_ID, KEPT_ANSWER, ADDED_AT + 10),
    )
    await temp_db.execute("UPDATE people SET birth_date = '1991-02-02' WHERE id = ?", (PERSON,))

    lines = said(await history_of_person(temp_db, actors.admin, PERSON))

    assert (
        "FansDB was linked before Sift recorded what a stash-box fills in, and agrees on"
        " birthdate (1991-02-02)"
    ) in lines


async def test_an_enrichment_whose_box_was_forgotten_still_says_it_happened(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The event is absorbed only by a box line that is DRAWN: with the link gone it is the only
    thing left that says the record was filled in, and it names no box because none was recorded."""
    await make_person(temp_db)
    await enriched_by_hand(temp_db, actors.admin.id, linked=False)

    lines = said(await history_of_person(temp_db, actors.admin, PERSON))

    assert "You filled in their details from a stash-box" in lines


async def two_presses_of_one_box(database: Database, admin_id: str, *, linked: bool = True) -> None:
    """Two runs of one box on one person, as the run's writer leaves them: each run beside an
    `enriched` event naming the box and what that run landed. The
    first was pressed by a user, the second nobody pressed."""
    await database.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, 'FansDB', ?, ?)",
        (A_BOX_ID, "https://example.invalid/graphql", ADDED_AT),
    )
    if linked:
        await database.execute(
            "INSERT INTO person_stash_box_links (person_id, box_id, remote_id, payload, fetched_at)"
            " VALUES (?, ?, 'remote', '{}', ?)",
            (PERSON, A_BOX_ID, ADDED_AT + 10),
        )
    for run, at, automatic, applied, actor in (
        ("run-1", ADDED_AT + 10, 0, {"birth_date": 1}, Actor.user(admin_id)),
        ("run-2", ADDED_AT + 20, 1, {"aliases": 3}, Actor.box(A_BOX_ID)),
    ):
        await database.execute(
            "INSERT INTO enrichment_runs (id, subject, local_id, box_id, at, automatic, applied)"
            " VALUES (?, 'person', ?, ?, ?, ?, ?)",
            (run, PERSON, A_BOX_ID, at, automatic, json.dumps(applied)),
        )
        await event(
            database,
            actor=actor,
            verb="enriched",
            subject=Subject(kind="person", id=PERSON, name=SOMEBODY),
            object=Object(kind="box", id=A_BOX_ID, name="FansDB"),
            payload=json.dumps(applied),
        )


async def test_each_press_of_one_box_is_its_own_line(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Two presses by one box are two lines, each read off its own event, rather than the box's
    LATEST run folding both into one date; the link table's latest-run line gives way to them
    rather than saying the second press twice."""
    await make_person(temp_db)
    await two_presses_of_one_box(temp_db, actors.admin.id)

    events = await history_of_person(temp_db, actors.admin, PERSON)

    assert [one.what for one in events if one.kind == "enriched"] == [
        "FansDB filled in their birthdate when you applied its answer",
        "FansDB filled in their 3 aliases automatically",
    ]
    assert all(one.actor_name == "FansDB" for one in events if one.kind == "enriched")
    assert "You filled in their details from a stash-box" not in said(events)


async def test_a_press_whose_box_link_was_forgotten_still_names_the_box(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The event carries the box, so forgetting the link takes nothing from the thread."""
    await make_person(temp_db)
    await two_presses_of_one_box(temp_db, actors.admin.id, linked=False)

    lines = said(await history_of_person(temp_db, actors.admin, PERSON))

    assert "FansDB filled in their birthdate when you applied its answer" in lines
    assert "FansDB filled in their 3 aliases automatically" in lines


async def test_a_site_draws_each_press_too(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The entity threads read the same event through the same builder, with "its"."""
    site = new_id()
    await temp_db.execute(
        "INSERT INTO sites (id, name, name_sort, created_at) VALUES (?, 'Larkspur', 'larkspur', ?)",
        (site, ADDED_AT),
    )
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, 'FansDB', ?, ?)",
        (A_BOX_ID, "https://example.invalid/graphql", ADDED_AT),
    )
    await temp_db.execute(
        "INSERT INTO site_stash_box_links (site_id, box_id, remote_id, payload, fetched_at)"
        " VALUES (?, ?, 'remote', '{}', ?)",
        (site, A_BOX_ID, ADDED_AT + 10),
    )
    for at, applied in ((ADDED_AT + 10, {"details": 1}), (ADDED_AT + 20, {})):
        await temp_db.execute(
            "INSERT INTO enrichment_runs (id, subject, local_id, box_id, at, automatic, applied)"
            " VALUES (?, 'site', ?, ?, ?, 0, ?)",
            (new_id(), site, A_BOX_ID, at, json.dumps(applied)),
        )
        await event(
            temp_db,
            actor=Actor.user(actors.admin.id),
            verb="enriched",
            subject=Subject(kind="site", id=site, name="Larkspur"),
            object=Object(kind="box", id=A_BOX_ID, name="FansDB"),
            payload=json.dumps(applied),
        )

    events = await history_of_site(temp_db, actors.admin, site)

    # The second press is a re-ask of a box already linked, and it filled nothing: it CHECKED.
    assert [one.what for one in events if one.kind == "enriched"] == [
        "FansDB filled in its details when you applied its answer",
        "You checked FansDB again and it had nothing new",
    ]


async def test_a_cover_a_box_supplied_names_the_box(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Not "Cover set to a new picture", which says nothing of where the picture came from."""
    await make_person(temp_db)
    await event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="edited",
        subject=Subject(kind="person", id=PERSON, name=SOMEBODY),
        payload=json.dumps({"cover": "picture", "box": "FansDB", "box_id": A_BOX_ID}),
    )

    lines = said(await history_of_person(temp_db, actors.admin, PERSON))

    assert "You chose FansDB's picture as their cover" in lines
    assert "You chose a new picture as their cover" not in lines


async def test_an_old_cover_choice_names_the_file_it_chose_while_the_file_is_there(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """A row written before covers kept the picture's name reads "You chose a file as the cover"."""
    await make_person(temp_db)
    chose = await event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="edited",
        subject=Subject(kind="person", id=PERSON, name=SOMEBODY),
        object=Object(kind="asset", id=world.solo),
    )
    # As an older row holds it: the door names the object now, so the snapshot is taken off.
    await temp_db.execute(
        "UPDATE workbench_decisions SET object_name = NULL WHERE id = ?", (chose,)
    )

    lines = said(await history_of_person(temp_db, actors.admin, PERSON))

    assert "You chose solo.mp4 as the cover" in lines
    assert "You chose a file as the cover" not in lines


def test_the_box_expression_is_the_same_in_every_thread() -> None:
    """The statements carry the box-of-a-row expression written out (query text is never built
    from a fragment here); this is what keeps the four copies one expression."""
    import re

    from sift.kernel.access import history, history_entity, history_person

    def folded(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    expression = folded(history.BOX_OF_A_ROW)
    for statement in (
        history_person._NAMED,
        history_person._NAMED_NO_LEDGER,
        history_entity._TAG_FILES,
        history_entity._SITE_FILES,
    ):
        assert expression in folded(statement)
    for statement in (
        history_person._NAMED_NO_BOX,
        history_person._NAMED_NO_BOX_NO_LEDGER,
        history_entity._TAG_FILES_NO_BOX,
        history_entity._SITE_FILES_NO_BOX,
    ):
        assert "NULL AS box" in statement and "stash_boxes" not in statement


async def test_a_save_to_a_device_is_drawn_for_the_user_that_saved_and_for_an_admin(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """What another user took home is not something a file a guest may see should disclose:
    the save log it mirrors is admin-only. The guest reads their own save and not an admin's; an
    admin reads both."""
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)
    for who in (actors.admin, actors.guest):
        await event(
            temp_db,
            actor=Actor.user(who.id),
            verb="saved",
            subject=Subject(kind="asset", id=world.solo, name="clip.mp4"),
        )

    theirs = await history_of_asset(temp_db, access, actors.guest, world.solo)
    everyone = await history_of_asset(temp_db, access, actors.admin, world.solo)

    assert [one.kind for one in theirs].count("saved") == 1
    assert [one.kind for one in everyone].count("saved") == 2


# --- A filing taken off on a stash-box's answer names the box ------------------------------------

BOX_ONE = "01HX0000000000000000000611"
BOX_TWO = "01HX0000000000000000000612"


async def two_boxes_answered(database: Database, asset_id: str, *, state: str) -> None:
    """FansDB's answer names the tag; StashDB's names nobody this file's lines are about."""
    for box_id, name, tags in ((BOX_ONE, "FansDB", [A_TAG]), (BOX_TWO, "StashDB", ["dusk"])):
        await database.execute(
            "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, ?)",
            (box_id, name, f"https://{name}.example.invalid/graphql", ADDED_AT),
        )
        await database.execute(
            "INSERT INTO asset_stash_box_matches"
            " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
            " VALUES (?, ?, 'remote', ?, 'unsure', ?, ?, ?)",
            (
                asset_id,
                box_id,
                json.dumps([{"fields": {"tags": tags}}]),
                state,
                ADDED_AT,
                ADDED_AT,
            ),
        )


async def tag_taken_off(database: Database, asset_id: str, *, boxes: list[str] | None) -> None:
    """Sift took the tag off the file on a stash-box's answer, as builds before 0.1.205 wrote it:
    a removal naming no box, beside the take-back's receipt naming `boxes` (none: no receipt)."""
    if boxes is not None:
        await event(
            database,
            actor=Actor.sift(VIA_STASH),
            verb="removed",
            subject=Subject(kind="asset", id=asset_id),
            object=Object(kind="box", id=BOX_ONE, name=boxes[0]),
            payload=json.dumps({"took_back": "picture", "boxes": boxes}),
        )
    await event(
        database,
        actor=Actor.sift(VIA_STASH),
        verb="unlinked",
        subject=Subject(kind="asset", id=asset_id),
        object=Object(kind="tag", id=TAG, name=A_TAG),
    )


def removal_lines(events: list[Event]) -> list[str]:
    return [one.what for one in events if one.what.startswith("Sift removed")]


async def test_a_removal_on_a_two_box_take_back_names_the_box_whose_answer_named_it(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The take-back named both boxes; the kept answers say which of them named the tag."""
    await make_tag(temp_db)
    await two_boxes_answered(temp_db, world.solo, state="refused")
    await tag_taken_off(temp_db, world.solo, boxes=["FansDB", "StashDB"])

    on_the_tag = removal_lines(await history_of_tag(temp_db, actors.admin, TAG))

    assert on_the_tag == ["Sift removed it from solo.mp4 from FansDB's answer"]


async def test_a_removal_on_a_one_box_take_back_names_that_box_on_the_files_page(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await make_tag(temp_db)
    await two_boxes_answered(temp_db, world.solo, state="refused")
    await tag_taken_off(temp_db, world.solo, boxes=["StashDB"])

    events = await history_of_asset(temp_db, access, actors.admin, world.solo)

    assert any(line.endswith(" from StashDB's answer") for line in removal_lines(events))
    assert not any("a stash-box answer" in line for line in said(events))


async def test_a_removal_with_no_receipt_falls_back_to_the_files_one_applied_answer(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """No take-back to read: the file's one applied answer names it, as `BOX_OF_A_ROW` does; with
    two applied, nothing chooses between them and the line says a stash-box."""
    await make_tag(temp_db)
    await two_boxes_answered(temp_db, world.solo, state="applied")
    await temp_db.execute(
        "UPDATE asset_stash_box_matches SET state = 'refused' WHERE box_id = ?", (BOX_TWO,)
    )
    await tag_taken_off(temp_db, world.solo, boxes=None)

    assert removal_lines(await history_of_tag(temp_db, actors.admin, TAG)) == [
        "Sift removed it from solo.mp4 from FansDB's answer"
    ]

    await temp_db.execute("UPDATE asset_stash_box_matches SET state = 'applied'")

    assert removal_lines(await history_of_tag(temp_db, actors.admin, TAG)) == [
        "Sift removed it from solo.mp4 from a stash-box answer"
    ]


async def test_a_removal_both_answers_named_by_another_name_names_both_boxes(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """StashDB named the tag by one of its other names: both answers said it, so both are named."""
    await make_tag(temp_db)
    await temp_db.execute(
        "INSERT INTO tag_aliases (id, tag_id, alias) VALUES (?, ?, 'dusk')", (new_id(), TAG)
    )
    await two_boxes_answered(temp_db, world.solo, state="refused")
    await tag_taken_off(temp_db, world.solo, boxes=["FansDB", "StashDB"])

    assert removal_lines(await history_of_tag(temp_db, actors.admin, TAG)) == [
        "Sift removed it from solo.mp4 from FansDB's and StashDB's answers"
    ]
