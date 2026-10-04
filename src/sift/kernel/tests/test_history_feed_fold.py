# SPDX-License-Identifier: AGPL-3.0-or-later
"""One act, one line, on every History screen.

- THE FEED FOLDS A PRESS: a task's four thousand filings are one line that opens to them, a setting
  moved over a sitting is one change from where it was to where it was left, and the usernames the
  workbench backfilled are one line whoever is credited (`history_feed.presses_recent`).
- A FILE'S ROUTINE is one "Sift processed this file" line that opens to each step.
- A STASH-BOX'S MATCH says how sure it was, and offers the box's own page for the scene.
- A SHELF'S OWN HISTORY says every file put in it, from its membership rows where the record did not.
- A DOWNLOAD'S FILING and a DECISION'S NAMINGS are the download's and the decision's one line.
"""

from __future__ import annotations

import json

import pytest

import sift.slices.download.schema
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository
from sift.kernel.access import sentences as say
from sift.kernel.access.history import (
    Actor as Doer,
)
from sift.kernel.access.history import (
    Event,
    _one_line_per_download,
    _one_processed_line,
)
from sift.kernel.access.history_entity import history_of_collection, history_of_site
from sift.kernel.access.history_feed import press_of, presses_recent
from sift.kernel.access.history_person import history_of_person
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, Object, Reversal, record_event
from sift.kernel.urls import scene_page
from sift.kernel.vocabulary import VIA_DOWNLOAD, VIA_FILENAME, Subject
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio

A_MOMENT = 1_700_000_000
A_USERNAME = "harlowquin"


async def event(database: Database, **named: object) -> str:
    async with database.write() as connection:
        return await record_event(connection, **named)  # type: ignore[arg-type]


async def at(database: Database, event_id: str, moment: int) -> None:
    """Put an event at a moment of the test's choosing: the door reads the wall clock."""
    await database.execute(
        "UPDATE workbench_decisions SET decided_at = ? WHERE id = ?", (moment, event_id)
    )


async def a_username(database: Database, world: World) -> str:
    username = new_id()
    await database.execute(
        "INSERT INTO usernames (id, site_id, name, name_sort, created_at) VALUES (?, ?, ?, ?, ?)",
        (username, world.site, A_USERNAME, A_USERNAME, A_MOMENT),
    )
    return username


# --- the feed's fold ------------------------------------------------------------------------------


async def test_a_task_s_filings_are_one_press_and_a_finished_task_never_folds(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """Three filings by one task under two usernames are ONE line standing for three acts, done
    with two usernames and about three files; two finished tasks beside them are two lines."""
    second = await a_username(temp_db, world)
    filed = []
    for asset, username in (
        (world.solo, world.username),
        (world.twin, second),
        (world.loose, second),
    ):
        filed.append(
            await event(
                temp_db,
                actor=Actor.sift(VIA_FILENAME),
                verb="filed",
                subject=Subject(kind="asset", id=asset),
                object=Object(kind="username", id=username),
            )
        )
    for _ in range(2):
        await event(
            temp_db,
            actor=Actor.sift(),
            verb="ran",
            subject=Subject(kind="run", id=new_id(), name="Scan"),
        )

    presses, total = await presses_recent(temp_db, actors.admin)

    assert total == 3
    folded = [one for one in presses if one.folded > 1]
    assert len(folded) == 1
    press = folded[0]
    assert press.folded == 3
    assert {one.id for one in press.objects} == {world.username, second}
    assert {one.id for one in press.subjects} == {world.solo, world.twin, world.loose}
    assert press.first is not None and press.first.id == filed[0]
    assert press.event.id == filed[-1]
    members = await press_of(temp_db, actors.admin, filed[0])
    assert [one for one, _queue in members] == list(reversed(filed))


async def test_a_person_s_acts_on_two_things_are_two_presses(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """A PERSON's key keeps what the act was done with: a file put in one Collection and then in
    another are two acts that share a verb, not one press."""
    other = new_id()
    await temp_db.execute(
        "INSERT INTO collections (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
        (other, "other", "other", A_MOMENT),
    )
    for shelf in (world.collection, other):
        await event(
            temp_db,
            actor=Actor.user(actors.admin.id),
            verb="linked",
            subject=Subject(kind="asset", id=world.solo),
            object=Object(kind="collection", id=shelf),
        )

    presses, total = await presses_recent(temp_db, actors.admin)

    assert total == 2
    assert [one.folded for one in presses] == [1, 1]


async def test_the_backfilled_usernames_are_one_line_whoever_is_credited(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """The workbench's backfill credits a task where it could read one and nobody where it could
    not. It is one press of this application's own, so it is one line, counting the ones untold."""
    second = await a_username(temp_db, world)
    told = await event(
        temp_db,
        actor=Actor.sift(VIA_FILENAME),
        verb="added",
        subject=Subject(kind="username", id=world.username),
        object=Object(kind="site", id=world.site),
        payload=json.dumps({"backfilled": True}),
    )
    untold = await event(
        temp_db,
        actor=Actor.sift(VIA_FILENAME),
        verb="added",
        subject=Subject(kind="username", id=second),
        object=Object(kind="site", id=world.site),
        payload=json.dumps({"backfilled": True}),
    )
    # The backfill writes "before this was recorded" as Sift with no task, which the door refuses
    # to anything else: written as the migration writes it.
    await temp_db.execute("UPDATE workbench_decisions SET actor_id = NULL WHERE id = ?", (untold,))

    presses, total = await presses_recent(temp_db, actors.admin)

    assert total == 1
    assert presses[0].folded == 2
    assert presses[0].untold == 1
    assert {told, untold} == {one for one, _ in await press_of(temp_db, actors.admin, told)}


async def test_a_setting_moved_over_a_sitting_is_one_change_and_a_later_one_is_another(
    temp_db: Database, actors: Actors
) -> None:
    """Three moves within a sitting of each other are one line from the first's "before" to the
    last's "after"; one a sitting later is its own line."""
    moves = []
    for before, after in (("40", "57"), ("57", "13"), ("13", "70"), ("70", "10")):
        moves.append(
            await event(
                temp_db,
                actor=Actor.user(actors.admin.id),
                verb="edited",
                subject=Subject(kind="setting", id="playback.volume", name="playback.volume"),
                payload=json.dumps({"key": "playback.volume", "before": before, "after": after}),
            )
        )
    for moment, one in zip((0, 100, 400, 2000), moves, strict=True):
        await at(temp_db, one, A_MOMENT + moment)

    presses, total = await presses_recent(temp_db, actors.admin)

    assert total == 2
    sitting = next(one for one in presses if one.folded == 3)
    assert sitting.first is not None
    assert json.loads(sitting.first.payload)["before"] == "40"
    assert json.loads(sitting.event.payload)["after"] == "70"


def test_a_box_filling_in_on_its_own_says_the_box_once() -> None:
    """A run of a box's own fills folds to one line whose actor IS its object; naming it twice
    would read "FansDB filled in 3 Sites from FansDB". A person's press still says which box."""
    box = say.thing("box", "b1", "FansDB")
    sites = [
        ("site", say.thing("site", "s1", "Quillhouse")),
        ("site", say.thing("site", "s2", "x")),
    ]
    own = say.feed_folded(
        "enriched",
        by="FansDB",
        acts=2,
        object_kind="box",
        objects=[box],
        subjects=sites,
        subject_counts={"site": 2},
    )
    assert own.what == "FansDB filled in 2 Sites"
    pressed = say.feed_folded(
        "enriched",
        by=say.YOU,
        acts=2,
        object_kind="box",
        objects=[box],
        subjects=sites,
        subject_counts={"site": 2},
    )
    assert pressed.what == "You filled in 2 Sites from FansDB"


def test_a_folded_press_says_the_press() -> None:
    """The words of the three folded shapes."""
    filed = say.feed_folded(
        "filed",
        by=say.SIFT,
        task=VIA_FILENAME,
        acts=4200,
        object_kind="username",
        objects=[say.thing("username", "u1", A_USERNAME), say.thing("username", "u2", "x")],
        objects_total=2,
        subjects=[("asset", say.thing("asset", "a1", "one.mp4"))],
        subject_counts={"asset": 4200},
    )
    assert filed.what == "Sift filed 4,200 files under 2 usernames from file names"
    assert [group.words for group in filed.groups] == [
        "2 usernames",
        "The newest 1 of 4,200 files",
    ]
    floor = say.feed_folded(
        "deleted",
        by=say.SIFT,
        task="photo_set_floor",
        acts=700,
        subjects=[("photo_set", say.thing("photo_set", "p", "a set"))],
        subject_counts={"photo_set": 700},
        payload={"under_floor": 10},
    )
    assert floor.what == (
        "Sift deleted 700 Photo Sets with fewer than 10 photos and left every photo in place"
    )
    backfill = say.feed_folded(
        "added",
        by=say.SIFT,
        acts=3600,
        subjects=[("username", say.thing("username", "u1", A_USERNAME))],
        subject_counts={"username": 3600},
        payload={"backfilled": True},
        untold=3500,
    )
    assert backfill.what == (
        "Sift recorded how 3,600 usernames arrived, 3,500 of them from before this was recorded"
    )


def test_a_folded_press_of_your_own_saves_says_your_device() -> None:
    """A fold of saves is YOUR device when you saved them, as the one-act line beside it says,
    not "You saved 9 files to their device". Somebody else's fold keeps "their"."""
    files = [("asset", say.thing("asset", "a1", "one.mp4"))]
    mine = say.feed_folded("saved", by=say.YOU, acts=9, subjects=files, subject_counts={"asset": 9})
    assert mine.what == "You saved 9 files to your device"
    theirs = say.feed_folded(
        "saved", by="guest1", acts=2, subjects=files, subject_counts={"asset": 2}
    )
    assert theirs.what == "guest1 saved 2 files to their device"


def test_a_folded_press_of_face_matches_says_recognized_once() -> None:
    """Not "Sift recognized 4 people in 8 files from a face": the verb already says it, and one
    match's own line has no such tail (`recognized_face`); the fold says what the one line says."""
    matched = say.feed_folded(
        "linked",
        by=say.SIFT,
        task="faces",
        acts=8,
        object_kind="person",
        objects=[say.thing("person", "p1", "Anouk Vestergaard"), say.thing("person", "p2", "x")],
        objects_total=4,
        subjects=[("asset", say.thing("asset", "a1", "one.jpg"))],
        subject_counts={"asset": 8},
    )
    assert matched.what == "Sift recognized 4 people in 8 files"


# --- a file's own tab -----------------------------------------------------------------------------


def _line(kind: str, words: str, when: int, *, routine: bool = False) -> Event:
    return Event(
        at=when,
        actor=Doer.SIFT,
        actor_name="Sift",
        kind=kind,
        pieces=say.said(words),
        routine=routine,
    )


def test_a_file_s_routine_lines_are_one_line_that_opens_to_each() -> None:
    """Thumbnails, Smart Search, no watermark, no stash-box match: one line for the sitting, dated
    at its first step."""
    events = [
        _line("added", "Sift added this file", 1),
        _line("ready", "Sift generated thumbnails for this file", 2, routine=True),
        _line("ready", "Sift indexed this file for Smart Search", 3, routine=True),
        _line("watermark", "Sift looked for a watermark and found none", 9, routine=True),
    ]
    folded = _one_processed_line(events)
    assert [one.what for one in folded] == ["Sift added this file", "Sift processed this file"]
    line = folded[-1]
    assert (line.at, line.since) == (2, None)
    assert [link.name for link in line.detail[0].links] == [one.what for one in events[1:]]
    # ONE routine line says what it is better than a line that opens to it.
    assert _one_processed_line(events[:2]) == events[:2]


def test_a_download_s_filing_is_the_download_s_line() -> None:
    """The filing a download wrote (its row says `download`) is dropped while a download line
    is here to say it; a filing somebody made by hand, at the same moment under the same Site, is
    a different act and stays. The clock is not read: a filing whose row has no time would never
    match a window, and a hand filing inside the minute would be swallowed."""
    site = say.thing("site", "s1", "Discord")
    download = Event(
        at=100,
        actor=Doer.SIFT,
        actor_name="Sift",
        kind="downloaded",
        pieces=say.said("Sift downloaded this file from ", site),
    )

    def filing(where: say.Piece, when: int | None, source: str | None) -> Event:
        return Event(
            at=when,
            actor=Doer.SOMEBODY,
            actor_name=None,
            kind="filed",
            pieces=say.said("This file was filed under ", where),
            source=source,
        )

    by_hand = filing(site, 100, None)
    kept = _one_line_per_download(
        [download, filing(site, 101, VIA_DOWNLOAD), by_hand, filing(site, None, VIA_DOWNLOAD)]
    )
    assert kept == [download, by_hand]
    # With no download line in the thread, the download's own filing is the only line saying it.
    alone = [filing(site, 101, VIA_DOWNLOAD)]
    assert _one_line_per_download(alone) == alone


def test_a_stash_box_match_says_how_sure_and_where_its_page_is() -> None:
    """The grade stored beside the match, and the box's own page for the scene, only on a box
    whose pages Sift knows."""
    line = say.recognized("PMVStash", None, ["13 people", "9 tags"], grade="certain")
    assert say.text_of(line) == (
        "PMVStash recognized this file, a certain match, and wrote 13 people and 9 tags"
    )
    assert scene_page("https://pmvstash.org/graphql", "abc") == "https://pmvstash.org/scenes/abc"
    assert scene_page("https://stash.example.test/graphql", "abc") is None
    assert scene_page("https://stashdb.org/graphql", None) is None


# --- a shelf, a Site and a person -----------------------------------------------------------------


async def test_a_shelf_says_every_file_put_in_it_once(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """The world's file went into its shelf before the record: said off the membership row. Once
    the record says it, the record's line is the one, never both."""
    before = [
        one.what for one in await history_of_collection(temp_db, actors.admin, world.collection)
    ]
    assert sum(" added to it" in one for one in before) == 1

    await event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="linked",
        subject=Subject(kind="asset", id=world.solo),
        object=Object(kind="collection", id=world.collection),
    )
    after = [
        one.what for one in await history_of_collection(temp_db, actors.admin, world.collection)
    ]
    assert sum(" to it" in one and "added" in one for one in after) == 1
    assert any(one.startswith("You added") for one in after)


async def test_a_download_s_filing_is_not_counted_again_on_its_site(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """The world's file is filed under its Site by hand (no source), so the Site's thread counts
    it; a filing whose row says `download` is the download's and is not counted again, whatever
    the clock says: the row is read, not a time window."""
    filed = [one.what for one in await history_of_site(temp_db, actors.admin, world.site)]
    assert any("filed under it" in one for one in filed)

    await temp_db.execute(
        "UPDATE asset_usernames SET source = ? WHERE asset_id = ?", (VIA_DOWNLOAD, world.solo)
    )
    once = [one.what for one in await history_of_site(temp_db, actors.admin, world.site)]
    assert not any("filed under it" in one for one in once)
    # And the line is gone, not reworded: a filing counted under its download's own words
    # would still be one act said twice.
    assert len(once) == len(filed) - 1


async def test_a_decision_s_namings_are_its_card_on_the_person_s_page(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """A folder answered as somebody names its files in the same transaction as its receipt: the
    card is the line, and the namings are not counted again beside it. Which namings are the
    card's is the one fold every thread keeps: the receipt on the file whose OBJECT is the person
    (`history_folds.RECEIPT_OF_A_NAMING`), so the folder answer's receipt names them as its
    object, as `suggestions.service.confirm` writes it."""
    await temp_db.execute(
        "UPDATE asset_people SET decided_at = ? WHERE person_id = ?", (A_MOMENT, world.person)
    )
    named = [one.kind for one in await history_of_person(temp_db, actors.admin, world.person)]
    assert "named" in named

    receipt = await event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="decided",
        subject=[Subject(kind="asset", id=world.solo), Subject(kind="person", id=world.person)],
        object=Object(kind="person", id=world.person),
        receipt=Reversal(queue="folders", title="A folder answered", detail=""),
    )
    await at(temp_db, receipt, A_MOMENT)
    once = [one.kind for one in await history_of_person(temp_db, actors.admin, world.person)]
    assert "named" not in once
    assert "decided" in once


def test_a_refused_answer_says_both_values_as_the_record_draws_them() -> None:
    """ "Your breast type, Natural, was kept over StashDB's Fake": the two values the kept row stores
    as a stash-box's constants, said as the record draws them (`records.value_said`)."""
    from sift.kernel.records import value_said

    mine = value_said("person", "breast_type", '"NATURAL"')
    theirs = value_said("person", "breast_type", '"FAKE"')
    line = say.kept_mine(None, "StashDB", "breast type", mine, theirs)
    assert say.text_of(line) == "Your breast type, Natural, was kept over StashDB's Fake"
    assert value_said("person", "breast_type", '["a"]') is None


def test_a_save_that_named_no_file_says_the_file_is_gone() -> None:
    """The workbench's v13 backfill writes a save's file only while the save log still names it, so
    a save naming none is a file that went before it could be named, and says so rather than "You
    saved something to your device"."""
    line = say.feed_line("saved", by=say.YOU)
    assert line.what == "You saved a file that is gone to your device"
