# SPDX-License-Identifier: AGPL-3.0-or-later
"""What happened to one file, in order: arrivals, names, moves, faces, copies and sharing."""

from __future__ import annotations

import pytest

# Imported for their side effect: registering the tables a feature owns, so a kernel database has
# them. The application always does (every install creates them whether or not the feature is
# switched on), and the reads guarded on those tables have the other case proved by dropping them.
import sift.slices.download.schema
import sift.slices.faces.schema
import sift.slices.media_edit.schema
import sift.slices.organize.schema
import sift.slices.semantic.schema
import sift.slices.stash_boxes.schema
import sift.slices.suggestions.schema
import sift.slices.watermarks.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Effect, ObjectType, Repository, Role
from sift.kernel.access.history import (
    DEFAULT_LIMIT,
    KINDS,
    MAX_LIMIT,
    VIAS,
    Actor,
    Link,
    history_of_asset,
)
from sift.kernel.access.sentences import matched_sentence
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.tests.history_helpers import (
    ADDED_AT,
    ASSET,
    DECISION,
    ENRICHED_AT,
    FILED_AT,
    MOVE,
    MOVED_AT,
    NAMED_AT,
    OTHER_PERSON,
    PERSON,
    ROOT,
    SCANNED_AT,
    SOURCE,
    TAGGED_AT,
    TRACK,
    UNDONE_AT,
    agree_to_a_face,
    decide,
    enrich,
    file_under_site,
    make_copy,
    make_file,
    make_findable,
    matched_a_face,
    move_file,
    name_another,
    name_person,
    said_of,
    scan_faces,
    tag_it,
)
from sift.testing.fixtures import Actors

pytestmark = pytest.mark.anyio


async def test_a_file_with_one_of_everything_comes_back_oldest_first(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The order is the whole product, so it is asserted as a list rather than a set.

    A timeline reads down: the file arriving is the first line and the most recent thing is the
    last, which is where the eye stops. Every source is represented once, which is also what proves
    the face pass was divided into seconds: undivided it would sort after everything, for ever.
    """
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    # A FOLDER'S tagging rather than a stash-box's: a stash-box's tagging is
    # absorbed into the box's own line, so this file would have one kind fewer on it and the
    # ordering this test is about would be proved over a shorter list.
    await tag_it(temp_db, source="folder", at=TAGGED_AT)
    await file_under_site(temp_db)
    await enrich(temp_db)
    await scan_faces(temp_db, found=2)
    await move_file(temp_db, kind="move", by=actors.admin.id)

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [event.kind for event in events] == [
        "added",
        "named",
        "tagged",
        "filed",
        "enriched",
        "face_run",
        "moved",
    ]
    assert [event.at for event in events] == [
        ADDED_AT,
        NAMED_AT,
        TAGGED_AT,
        FILED_AT,
        ENRICHED_AT,
        SCANNED_AT,
        MOVED_AT,
    ]


async def test_each_source_says_who_did_it_in_the_apps_own_words(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The actor and the sentence together, because either one alone can be right and misleading.

    A tag a stash-box put on and a tag somebody chose are the same row but for one word, and the
    whole reason the column exists is that the two must not read the same.
    """
    await make_file(temp_db)
    await name_person(temp_db, source="folder", at=NAMED_AT)
    await tag_it(temp_db, source="stash_box", at=TAGGED_AT)
    await file_under_site(temp_db)
    await enrich(temp_db)

    said = {
        event.kind: (event.actor, event.actor_name, event.what)
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
    }

    assert said["added"] == (Actor.SIFT, "Sift", "Sift added this file to the library")
    # The pass is IN the sentence: the actor says "Sift" and the mark says "folder", and neither of
    # them is the line somebody reads.
    assert said["named"] == (
        Actor.SIFT,
        "Sift, from a folder name",
        # The actor first and the task at the BACK: who did it, what, and how.
        "Sift named Neve Alder in this file from a folder name",
    )
    # The filing records nobody, so it is PASSIVE rather than a guess at who did it.
    assert said["filed"] == (Actor.SOMEBODY, None, "This file was filed under harlowquin on Studio")
    # The tagging is the BOX'S act and is said on the box's own line, once.
    assert "tagged" not in said
    assert said["enriched"] == (
        Actor.STASH_BOX,
        "StashDB",
        "StashDB recognized this file, a certain match, and wrote 1 tag",
    )


async def test_the_first_line_says_what_the_file_arrived_as(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The name a file came in under is something that HAPPENED to it, so it is a line here.

    Not a record row beside the name that can be changed: it is the first thing in the list of
    things that happened to the file, and this is that list."""
    await make_file(temp_db)
    await temp_db.execute(
        "UPDATE assets SET original_filename = ? WHERE id = ?", ("beach-day-0042.mp4", ASSET)
    )

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [(event.kind, event.what) for event in events] == [
        ("added", "Sift added this file to the library as beach-day-0042.mp4")
    ]


async def test_a_file_that_arrived_under_no_name_says_only_what_is_known(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A file Sift downloaded itself came from no folder, so there is no name it arrived under.

    `Added to the library as None` is worse than the shorter sentence: it reports a fault that is
    not there, on the one line every file in the library has.
    """
    await make_file(temp_db)

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [event.what for event in events] == ["Sift added this file to the library"]


async def test_a_row_written_before_the_column_existed_reads_as_unrecorded_and_sorts_first(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """NULL is drawn as "before this was recorded", and it belongs at the top.

    Inventing a time for it would put a made-up date on a screen somebody is reading precisely to
    find out what really happened, and putting it last would claim it is the most recent thing.
    """
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=None)
    await tag_it(temp_db, source=None, at=TAGGED_AT)

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [(event.kind, event.at) for event in events] == [
        ("named", None),
        ("added", ADDED_AT),
        ("tagged", TAGGED_AT),
    ]


async def test_the_cap_keeps_the_newest_and_the_limit_is_clamped(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A long history loses its beginning, never its end.

    What somebody opening a busy file is looking for is what happened recently, so the oldest are
    the ones to drop, and the rows with no time at all are therefore the first to go, which is
    right for the same reason: they can say least.
    """
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=None)
    await tag_it(temp_db, source=None, at=TAGGED_AT)
    await file_under_site(temp_db)

    assert [
        event.kind
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET, limit=2)
    ] == [
        "tagged",
        "filed",
    ]
    # Below the floor and above the ceiling, both clamped rather than refused: a limit is a request
    # for how much to draw, and a screen asking for none of it or all of it means neither. One is
    # the newest, which is the filing.
    fewest = await history_of_asset(temp_db, access, actors.admin, ASSET, limit=0)
    assert [event.kind for event in fewest] == ["filed"]
    assert (
        len(await history_of_asset(temp_db, access, actors.admin, ASSET, limit=MAX_LIMIT + 1)) == 4
    )
    assert DEFAULT_LIMIT < MAX_LIMIT


async def test_a_file_that_is_not_there_has_no_history(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """An empty list rather than a line saying it arrived. The route answers 404 above this."""
    assert await history_of_asset(temp_db, access, actors.admin, "01HX00000000000000000005ZZ") == []


async def test_only_the_last_move_can_be_taken_back_and_only_by_an_admin(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Both halves are the organizer's own refusals, not a guess made here.

    It refuses an undo out of order (a file renamed twice would otherwise go back to a name it
    only ever had in passing), and it refuses one from a guest. An affordance the server would
    refuse is worse than none, because the only way to find out is to press it.
    """
    await make_file(temp_db)
    await move_file(temp_db, kind="rename", by=actors.admin.id)
    await temp_db.execute(
        "INSERT INTO file_moves (id, kind, location_id, asset_id, root_id, from_rel_path,"
        " to_rel_path, moved_by, moved_at, undone_at) VALUES (?, 'move', NULL, ?, ?, ?, ?, ?, ?,"
        " NULL)",
        (new_id(), ASSET, ROOT, "new/clip.mp4", "later/clip.mp4", actors.admin.id, MOVED_AT + 10),
    )

    for_admin = await history_of_asset(temp_db, access, actors.admin, ASSET)
    offered = [event.kind for event in for_admin if event.undo is not None]
    assert offered == ["moved"]
    assert [event.undo.kind for event in for_admin if event.undo is not None] == ["move"]

    for_guest = await history_of_asset(temp_db, access, actors.guest, ASSET)
    assert [event.undo for event in for_guest if event.undo is not None] == []


async def test_a_move_that_was_taken_back_keeps_its_place_and_says_when(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A history that quietly loses its reversals reads as though the file was never moved at all.

    So both are drawn: the move, marked as reversed and with nothing left to press, and the moment
    it was put back, which is a different time and a thing that happened.
    """
    await make_file(temp_db)
    await move_file(temp_db, kind="rename", by=actors.admin.id, undone=UNDONE_AT)

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [(event.kind, event.at, event.reversed) for event in events] == [
        ("added", ADDED_AT, False),
        ("renamed", MOVED_AT, True),
        ("undone", UNDONE_AT, False),
    ]
    assert [event.undo for event in events] == [None, None, None]
    assert events[1].what == "You renamed this file to clip.mp4"
    assert events[2].what == "That rename was undone"
    # The mover is named on the move and NOT on the undo: the table records who moved a file and
    # the moment it was put back, and nothing about who put it back. Saying the mover did both
    # would attribute an act to a named user who may not have done it.
    assert events[1].actor is Actor.YOU
    assert (events[2].actor, events[2].actor_name) == (Actor.SOMEBODY, None)


async def test_a_user_is_named_only_to_an_admin(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Who else is a user here is not something a file a guest may see should disclose.

    Every route that reads the users on this install is admin-only, so naming one here would be
    this read answering a question the sharing screens refuse: through a file they happen to be
    allowed to see. Their own act still says "you", which needs no name.
    """
    await make_file(temp_db)
    await move_file(temp_db, kind="move", by=actors.admin.id)

    as_admin = await history_of_asset(temp_db, access, actors.admin, ASSET)
    assert (as_admin[1].actor, as_admin[1].actor_name) == (Actor.YOU, None)

    as_guest = await history_of_asset(temp_db, access, actors.guest, ASSET)
    assert as_guest[1].actor is Actor.ANOTHER_USER
    assert as_guest[1].actor_name is None

    second_admin = await access.load_viewer(
        (await temp_db.fetch_all("SELECT id FROM users WHERE role = 'admin'"))[0]["id"]
    )
    assert second_admin is not None
    await temp_db.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at) VALUES"
        " (?, 'watcher', 'x', 'admin', ?)",
        ("01HX0000000000000000000510", ADDED_AT),
    )
    watching = await access.load_viewer("01HX0000000000000000000510")
    assert watching is not None
    named = await history_of_asset(temp_db, access, watching, ASSET)
    assert named[1].actor is Actor.ANOTHER_USER
    assert named[1].actor_name is not None


async def test_a_move_by_nobody_says_somebody(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The user who made a move can be gone: `moved_by` is ON DELETE SET NULL.

    An entry has to outlive the user who made it, or removing a guest who once renamed a file
    would either fail or take the history with it. What is left is a true record of something
    somebody did, and saying which is no longer possible.
    """
    await make_file(temp_db)
    await move_file(temp_db, kind="move", by=None)

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert (events[1].actor, events[1].actor_name) == (Actor.SOMEBODY, None)
    assert events[1].what == "This file was moved to new"


async def test_a_move_nobody_asked_for_says_sift(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Sift's own repair of a name writes no user and must not read "Somebody renamed";
    `moved_by_sift` is the flag that tells the two apart."""
    await make_file(temp_db)
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (ROOT, "library", "/library", ADDED_AT),
    )
    await temp_db.execute(
        "INSERT INTO file_moves (id, kind, location_id, asset_id, root_id, from_rel_path,"
        " to_rel_path, moved_by, moved_by_sift, moved_at, undone_at)"
        " VALUES (?, 'rename', NULL, ?, ?, ?, ?, NULL, 1, ?, NULL)",
        (MOVE, ASSET, ROOT, "old/image0 _image0_.jpg", "old/image0.jpg", MOVED_AT),
    )

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert (events[1].actor, events[1].actor_name) == (Actor.SIFT, "Sift")


async def test_a_move_to_the_top_of_a_library_has_a_folder_to_name(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A file sitting directly in a library root has no folder in its path to put in the sentence.

    "Moved to ." is the answer a split on the separator gives, and it is the one a person reads as
    a bug in the screen rather than as a file at the top of a library.
    """
    await make_file(temp_db)
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (ROOT, "library", "/library", ADDED_AT),
    )
    await temp_db.execute(
        "INSERT INTO file_moves (id, kind, location_id, asset_id, root_id, from_rel_path,"
        " to_rel_path, moved_by, moved_at, undone_at) VALUES (?, 'move', NULL, ?, ?, ?, ?, NULL,"
        " ?, NULL)",
        (MOVE, ASSET, ROOT, "old/clip.mp4", "clip.mp4", MOVED_AT),
    )

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert events[1].what == "This file was moved to the top of the library"


async def test_a_filing_says_what_it_can_when_the_site_or_the_username_is_missing(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Two holes, and each has to leave a readable sentence.

    The empty name is the library's own way of writing "from this site, poster unknown", so it
    never becomes part of a sentence, and a username's site is ON DELETE SET NULL, which
    leaves a filing with no site to name.
    """
    await make_file(temp_db)
    await file_under_site(temp_db, name="", site="Studio")
    filed = [
        event
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if event.kind == "filed"
    ]
    assert filed[0].what == "This file was filed under Studio"

    await temp_db.execute("DELETE FROM asset_usernames")
    await temp_db.execute("DELETE FROM usernames")
    await temp_db.execute("DELETE FROM sites")
    await file_under_site(temp_db, name="harlowquin", site=None)
    filed = [
        event
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if event.kind == "filed"
    ]
    assert filed[0].what == "This file was filed under harlowquin"

    await temp_db.execute("DELETE FROM asset_usernames")
    await temp_db.execute("DELETE FROM usernames")
    await file_under_site(temp_db, name="", site=None)
    filed = [
        event
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if event.kind == "filed"
    ]
    assert filed[0].what == "This file was filed under a Site"


async def test_a_face_pass_that_found_nothing_says_so(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """ "Found none" rather than "found 0", because the line is a sentence and not a reading."""
    await make_file(temp_db)
    await scan_faces(temp_db, found=0)

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert events[-1].what == "Sift looked for faces here and found none"


async def test_a_face_pass_that_found_nobody_says_why_when_it_knows(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A clip shot wide that settles as "found none" with faces refused for size must not read as
    a file with nobody in it: the scan's own counts reach the line."""
    await make_file(temp_db)
    await scan_faces(temp_db, found=0, small=16, closer=1)

    scan = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "face_run")

    assert scan.what == "Sift found faces here too small or too unclear to recognize"


async def test_one_face_too_small_says_a_face_and_how_big(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A still with one clear face refused by the size floor: the row settles as no faces, and the
    line says it found one face, too small, and the size it measured, so "too small" can be
    judged against the picture rather than taken on trust."""
    await make_file(temp_db)
    await scan_faces(temp_db, found=0, small=1, closer=0, why=(98, 0, 0, 0))

    scan = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "face_run")

    assert scan.what == "Sift found a face here too small to recognize, 98 pixels across"


@pytest.mark.parametrize(
    ("why", "words"),
    [
        ((0, 1, 0, 0), "Sift found a face here too blurred to recognize"),
        ((0, 0, 1, 0), "Sift found a face here turned too far away to recognize"),
        ((0, 0, 0, 1), "Sift found a face here too far off the edge of the picture to recognize"),
    ],
)
async def test_each_reason_the_closer_look_gives_draws_its_own_words(
    temp_db: Database,
    access: Repository,
    actors: Actors,
    why: tuple[int, int, int, int],
    words: str,
) -> None:
    """Blur, angle and the edge of the picture each say themselves: "too unclear" could not tell a
    person which of the three to fix or forgive."""
    await make_file(temp_db)
    await scan_faces(temp_db, found=0, small=0, closer=1, why=why)

    scan = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "face_run")

    assert scan.what == words


async def test_several_reasons_are_said_together_with_the_largest_size(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    await make_file(temp_db)
    await scan_faces(temp_db, found=0, small=3, closer=3, why=(104, 2, 0, 1))

    scan = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "face_run")

    assert scan.what == (
        "Sift found faces here too small, too blurred or too far off the edge of the picture"
        " to recognize, the largest 104 pixels across"
    )


async def test_a_face_pass_that_refused_nothing_still_found_none(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Zero is a count, and it says there was nobody to refuse: the plain line, like a scan from
    before the counts were kept."""
    await make_file(temp_db)
    await scan_faces(temp_db, found=0, small=0, closer=0)

    scan = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "face_run")

    assert scan.what == "Sift looked for faces here and found none"


async def test_a_face_agreed_to_and_a_face_refused(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Both are decisions a person made about a face, and neither is recorded with a user.

    The times are millisecond stamps like everything else in the face tables, so this is also what
    proves the division happens for all three of them rather than for the scan alone.
    """
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    await temp_db.execute(
        "INSERT INTO face_confirmations (id, asset_id, person_id, embedding, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (new_id(), ASSET, PERSON, b"\x00", (NAMED_AT + 1) * 1000),
    )
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, created_at)"
        " VALUES (?, ?, 0, 1, 1, 1.0, ?)",
        (TRACK, ASSET, ADDED_AT * 1000),
    )
    await temp_db.execute(
        "INSERT INTO face_rejections (track_id, person_id, created_at) VALUES (?, ?, ?)",
        (TRACK, PERSON, (NAMED_AT + 2) * 1000),
    )

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [(event.kind, event.at, event.actor, event.what) for event in events[-2:]] == [
        ("confirmed", NAMED_AT + 1, Actor.SOMEBODY, "A face here was confirmed as Neve Alder"),
        ("rejected", NAMED_AT + 2, Actor.SOMEBODY, "A face here was marked as not Neve Alder"),
    ]


async def test_agreeing_to_a_face_draws_one_line_and_not_two(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """One press writes `face_confirmations` AND, through the reconciliation that brings a file's
    People into line with the faces in it, the `asset_people` row, so the pane would draw "A face
    here was agreed to be Neve Alder" and "Neve Alder was named in this file", one act said twice.
    That naming row carries no source.
    """
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    await agree_to_a_face(temp_db, at=NAMED_AT)

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [event.kind for event in events] == ["added", "confirmed"]
    assert events[-1].what == "A face here was confirmed as Neve Alder"


async def test_the_surviving_line_still_names_her(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The line that is kept is the one that says more, and it carries the way to her, so folding
    takes a sentence away and never a link."""
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    await agree_to_a_face(temp_db, at=NAMED_AT)

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [(one.kind, one.id, one.name) for one in events[-1].links] == [
        ("person", PERSON, "Neve Alder")
    ]


@pytest.mark.parametrize("source", ["folder", "filename", "stash_box", "watermark"])
async def test_a_naming_that_names_its_own_source_keeps_its_line(
    temp_db: Database, access: Repository, actors: Actors, source: str
) -> None:
    """A folder read, a file name, a stash-box or a watermark is an act that happened somewhere
    else, and its line is the only one saying so. Only a naming that can account for itself in no
    other way is the one a confirmation beside it explains."""
    await make_file(temp_db)
    await name_person(temp_db, source=source, at=NAMED_AT)
    await agree_to_a_face(temp_db, at=NAMED_AT)

    kinds = [event.kind for event in await history_of_asset(temp_db, access, actors.admin, ASSET)]

    assert kinds.count("named") == 1
    assert kinds.count("confirmed") == 1


async def test_a_naming_of_somebody_else_is_not_folded_away(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The fold is per PERSON. A file with two People on it, one of whom had a face agreed to, must
    keep the other one's line: folding on the KIND alone would take a name off the screen that
    nothing else in the pane mentions."""
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    somebody_else = "01HX0000000000000000000522"
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)",
        (somebody_else, "Ada Lumen", ADDED_AT),
    )
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (ASSET, somebody_else, None, NAMED_AT),
    )
    await agree_to_a_face(temp_db, at=NAMED_AT)

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)
    named = [event for event in events if event.kind == "named"]

    assert [event.what for event in named] == ["Ada Lumen was named here"]


async def test_a_file_with_no_confirmation_keeps_every_naming(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The ordinary file, which is nearly all of them: nothing to fold into, nothing folded."""
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)

    kinds = [event.kind for event in await history_of_asset(temp_db, access, actors.admin, ASSET)]

    assert kinds == ["added", "named"]


async def test_a_face_sift_matched_on_its_own_says_so_and_says_how_sure(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The one act that happens with nobody in the room draws a line of its own.

    The mark is `faces` (the
    `enriched:` filter's own word), and the actor is Sift, because no user decided it.
    """
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    await matched_a_face(temp_db, at=NAMED_AT + 3, sure=0.9163)

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)
    matched = said_of(list(events), "matched")

    assert matched.what == "Sift recognized Neve Alder here, 92% sure"
    # Its own kind, declared in the vocabulary a client draws marks from. The MARK still comes from
    # `via`, which is why nothing on the client had to learn the word.
    assert matched.kind in KINDS
    assert matched.via in VIAS
    assert (matched.at, matched.actor, matched.actor_name, matched.via) == (
        NAMED_AT + 3,
        Actor.SIFT,
        "Sift",
        "faces",
    )
    assert matched.links == (Link(kind="person", id=PERSON, name="Neve Alder"),)


async def test_a_match_folds_away_the_naming_it_produced(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The reconciliation after a match writes a sourceless `asset_people` row, which the match's
    line already says with who decided it and how sure. A naming with a SOURCE keeps its line, as
    the parametrized test above holds for a confirmation: a pass that got there first is its own
    act."""
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    await matched_a_face(temp_db, at=NAMED_AT + 3)

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [event.kind for event in events] == ["added", "matched"]
    # Folding takes a sentence away and never a link: the surviving line still goes to her.
    assert events[-1].links == (Link(kind="person", id=PERSON, name="Neve Alder"),)


async def test_a_naming_from_a_folder_survives_a_match(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A folder read that got to `asset_people` first is an act that happened somewhere else, and
    its line is the only one saying so."""
    await make_file(temp_db)
    await name_person(temp_db, source="folder", at=NAMED_AT)
    await matched_a_face(temp_db, at=NAMED_AT + 3)

    kinds = [event.kind for event in await history_of_asset(temp_db, access, actors.admin, ASSET)]

    assert kinds.count("named") == 1
    assert kinds.count("matched") == 1


async def test_many_faces_of_one_person_matched_here_are_one_line(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A person walking in and out of shot is several appearances and one act of recognition: the
    range in words, as the re-match receipt writes it, and the moment the LATEST of them."""
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    await matched_a_face(temp_db, at=NAMED_AT + 3, sure=0.84)
    await matched_a_face(temp_db, at=NAMED_AT + 9, sure=0.9163)
    await matched_a_face(temp_db, at=NAMED_AT + 5, sure=0.88)

    matched = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "matched")

    assert matched.what == ("Sift recognized 3 faces here as Neve Alder, between 84% and 92% sure")
    assert matched.at == NAMED_AT + 9
    # The sentence still names her, so the way to her belongs in `links`, and `detail` would be
    # the same name listed three times under a line that already says how many.
    assert matched.links == (Link(kind="person", id=PERSON, name="Neve Alder"),)
    assert matched.detail == ()


async def test_two_people_matched_here_are_two_lines(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The fold is per PERSON. Folding on the kind alone would say Sift matched two faces to one of
    them and take the other name off the screen."""
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    await name_another(temp_db, source=None, at=NAMED_AT)
    await matched_a_face(temp_db, at=NAMED_AT + 3, sure=0.9163)
    await matched_a_face(temp_db, at=NAMED_AT + 4, sure=0.81, person=OTHER_PERSON)

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [event.what for event in events if event.kind == "matched"] == [
        "Sift recognized Neve Alder here, 92% sure",
        "Sift recognized Ada Lumen here, 81% sure",
    ]


async def test_a_face_you_agreed_to_does_not_draw_as_one_sift_matched(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A confirmed appearance is somebody's own answer, which the confirmation line already says in
    the words a person's decision deserves. Claiming it as a thing Sift decided would put the wrong
    name against it, and a suggestion decided nothing at all and is a question still waiting."""
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, person_id,"
        " confidence, attribution, attributed_at, created_at)"
        " VALUES (?, ?, 0, 1, 1, 1.0, ?, 0.97, 'confirmed', ?, ?)",
        (TRACK, ASSET, PERSON, (NAMED_AT + 3) * 1000, ADDED_AT * 1000),
    )
    await temp_db.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, person_id,"
        " confidence, attribution, attributed_at, created_at)"
        " VALUES (?, ?, 0, 1, 1, 1.0, ?, 0.61, 'suggested', ?, ?)",
        (new_id(), ASSET, PERSON, (NAMED_AT + 4) * 1000, ADDED_AT * 1000),
    )
    await agree_to_a_face(temp_db, at=NAMED_AT + 3)

    kinds = [event.kind for event in await history_of_asset(temp_db, access, actors.admin, ASSET)]

    assert "matched" not in kinds
    assert kinds == ["added", "confirmed"]


async def test_a_match_with_no_figure_stored_says_the_sentence_without_one(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """`face_tracks.confidence` is nullable, so the number can be absent, and inventing one would
    put a measurement on screen that nothing measured. A matched appearance ordinarily carries one,
    so this guards the column rather than the application."""
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    await matched_a_face(temp_db, at=NAMED_AT + 3, sure=None)

    matched = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "matched")

    assert matched.what == "Sift recognized Neve Alder here"


async def test_a_scans_receipt_and_the_match_it_recorded_are_one_line(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """End to end: the attach says how sure it was AND can be taken back. The two rows one act
    wrote fold into one line with the mark, the way to her and the Undo; the RECEIPT'S ID pairs
    them, not the sentences, so the two may differ without the pane growing a second line."""
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    await matched_a_face(temp_db, at=NAMED_AT + 3, sure=0.9163)
    await decide(
        temp_db,
        queue="identified",
        title=matched_sentence("Neve Alder", [0.9163]),
        by=None,
        verb="linked",
        object_kind="person",
        object_id=PERSON,
    )

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [(event.kind, event.what) for event in events] == [
        ("added", "Sift added this file to the library"),
        ("decided", "Sift recognized Neve Alder here, 92% sure"),
    ]
    folded = events[-1]
    assert folded.via == "faces"
    assert folded.links == (Link(kind="person", id=PERSON, name="Neve Alder"),)
    assert folded.undo is not None
    assert (folded.undo.kind, folded.undo.id) == ("decision", DECISION)


async def test_a_rematchs_run_wide_receipt_folds_nothing_and_both_lines_stand(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A re-match's record is the RUN, over hundreds of files, and its title says so.

    What one press did to a library and what it did to this file are not each other's repetition, so
    both lines are drawn: that Undo takes the whole run back, never this file's share of it."""
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    await matched_a_face(temp_db, at=NAMED_AT + 3, sure=0.9163)
    await decide(
        temp_db,
        queue="identified",
        title="Sift matched 344 more faces to Neve Alder, between 78% and 96% sure",
        by=None,
    )

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [event.kind for event in events] == ["added", "matched", "decided"]
    assert said_of(events, "matched").undo is None


async def test_a_copy_names_the_file_it_was_made_from(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """And carries it as a link, so the original is one press away.

    Naming a file says it exists, so the name is resolved through the access layer handed to this
    read; the case of a reader who may not be shown the original is the test below.
    """
    await make_file(temp_db)
    await make_file(temp_db, asset_id=SOURCE)
    await make_findable(temp_db, SOURCE, "beach.mp4")
    await make_copy(temp_db, copy=ASSET, source=SOURCE, by=actors.admin.id)

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert events[-1].kind == "copied_from"
    assert events[-1].what == "You compressed this file from beach.mp4"
    assert events[-1].how == "compress"
    assert events[-1].actor is Actor.YOU
    assert events[-1].links == (Link(kind="asset", id=SOURCE, name="beach.mp4"),)


async def test_a_copy_whose_original_this_user_may_not_see_names_nothing(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A guest may be allowed the copy and refused the original, and naming it would say that file
    exists. The line is still drawn, since "this is a copy" is true; it does not say of what."""
    await make_file(temp_db)
    await make_file(temp_db, asset_id=SOURCE)
    await make_findable(temp_db, SOURCE, "beach.mp4")
    await make_copy(temp_db, copy=ASSET, source=SOURCE, by=actors.admin.id)
    await access.grant(ObjectType.ITEM, SOURCE, actors.guest.id, Effect.RESTRICT)

    events = await history_of_asset(temp_db, access, actors.guest, ASSET)

    assert events[-1].kind == "copied_from"
    assert events[-1].what == "Another user compressed this file from another file"
    assert events[-1].links == ()
    assert SOURCE not in events[-1].what


async def test_a_copy_whose_original_is_gone_reads_the_same_way(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Deliberately indistinguishable from the one above.

    The column is `ON DELETE SET NULL`, so a deleted original leaves no id at all, and the wording
    has to be true of both cases, because saying "deleted" would be a lie exactly when the answer is
    the one that matters.
    """
    await make_file(temp_db)
    await temp_db.execute(
        "INSERT INTO produced_files (id, asset_id, source_asset_id, operation, produced_by,"
        " produced_at) VALUES (?, ?, NULL, 'compress', ?, ?)",
        (new_id(), ASSET, actors.admin.id, MOVED_AT),
    )

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert events[-1].kind == "copied_from"
    assert events[-1].what == "You compressed this file from another file"
    assert events[-1].links == ()


async def test_what_was_made_from_this_file_is_a_line_of_its_own(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The same row read from the other end, on the ORIGINAL's page.

    A history is where "what happened to this" is answered, and a copy having been made out of
    a file is one of the things that happened to it.
    """
    await make_file(temp_db, asset_id=SOURCE)
    await make_file(temp_db)
    await make_findable(temp_db, ASSET, "beach (trimmed).mp4")
    await make_copy(temp_db, copy=ASSET, source=SOURCE, by=actors.admin.id, operation="trim")

    events = await history_of_asset(temp_db, access, actors.admin, SOURCE)

    assert events[-1].kind == "copied_into"
    assert events[-1].what == "You trimmed this file into beach (trimmed).mp4"
    assert events[-1].how == "trim"
    assert events[-1].actor is Actor.YOU
    assert events[-1].links == (Link(kind="asset", id=ASSET, name="beach (trimmed).mp4"),)


async def test_a_copy_this_user_may_not_see_is_left_out_entirely(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The one asymmetry between the two directions, and it is deliberate.

    "Made from another file" explains what the file IS and is worth saying with nothing to go to.
    "Something was made from this" with nothing to press explains nothing, cannot be acted on, and
    the copy is invisible to this user precisely because it was not theirs to be told about.
    """
    await make_file(temp_db, asset_id=SOURCE)
    await make_file(temp_db)
    await make_findable(temp_db, ASSET, "beach (trimmed).mp4")
    await make_copy(temp_db, copy=ASSET, source=SOURCE, by=actors.admin.id)
    await access.grant(ObjectType.ITEM, ASSET, actors.guest.id, Effect.RESTRICT)

    kinds = [event.kind for event in await history_of_asset(temp_db, access, actors.guest, SOURCE)]

    assert "copied_into" not in kinds


async def test_a_trimmed_copy_is_said_with_the_verb_that_made_it_from_both_ends(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """One row, read from each end, saying the same verb.

    "Trimmed into", not "Made into". Both directions are asserted together because they are one row
    and one table of verbs: a change that taught one end the word and left the other saying "Made"
    would read as two different things having happened.
    """
    await make_file(temp_db, asset_id=SOURCE)
    await make_file(temp_db)
    await make_findable(temp_db, SOURCE, "beach.mp4")
    await make_findable(temp_db, ASSET, "beach (trimmed).mp4")
    await make_copy(temp_db, copy=ASSET, source=SOURCE, by=actors.admin.id, operation="trim")

    original = await history_of_asset(temp_db, access, actors.admin, SOURCE)
    copy = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert original[-1].what == "You trimmed this file into beach (trimmed).mp4"
    assert original[-1].how == "trim"
    assert copy[-1].what == "You trimmed this file from beach.mp4"
    assert copy[-1].how == "trim"


@pytest.mark.parametrize(
    ("operation", "verb"),
    [("clip", "Clipped"), ("gif", "Animated"), ("crop", "Cropped"), ("resize", "Resized")],
)
async def test_each_verb_the_editor_records_has_its_own_word(
    temp_db: Database, access: Repository, actors: Actors, operation: str, verb: str
) -> None:
    """Every word `produced_files.operation` can carry, said the way the editor says it."""
    await make_file(temp_db, asset_id=SOURCE)
    await make_file(temp_db)
    await make_findable(temp_db, ASSET, "a piece.mp4")
    await make_copy(temp_db, copy=ASSET, source=SOURCE, by=actors.admin.id, operation=operation)

    events = await history_of_asset(temp_db, access, actors.admin, SOURCE)

    assert events[-1].what == f"You {verb.lower()} this file into a piece.mp4"
    assert events[-1].how == operation


async def test_an_operation_with_no_verb_still_reads_the_way_it_always_did(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The fallback IS the plain sentence, and that is what makes naming the verb safe.

    `edit` is what the editor writes when somebody did several things together (genuinely several
    verbs and therefore none), and it takes the same path an operation added after this build does.
    Neither says something untrue; both say what a plain copy says.
    """
    await make_file(temp_db, asset_id=SOURCE)
    await make_file(temp_db)
    await make_findable(temp_db, ASSET, "beach (edited).mp4")
    await make_copy(temp_db, copy=ASSET, source=SOURCE, by=actors.admin.id, operation="edit")

    events = await history_of_asset(temp_db, access, actors.admin, SOURCE)

    assert events[-1].what == "You created beach (edited).mp4 from this file"
    assert events[-1].how == "edit"


async def test_sharing_is_an_admins_half_of_the_history(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A grant is about other USERS rather than about the file.

    A guest allowed to see the file is not thereby allowed to learn who else is a user here,
    so the sharing is left out of their answer while the rest of the history stays theirs to read.
    """
    await make_file(temp_db)
    await access.grant(ObjectType.ITEM, ASSET, actors.guest.id, Effect.SHARE)

    as_admin = await history_of_asset(temp_db, access, actors.admin, ASSET)
    shared = [event for event in as_admin if event.kind == "shared"]
    assert len(shared) == 1
    # Nothing recorded who made this grant, so the line is passive and the guest is never the doer.
    assert shared[0].what.startswith("This file was shared with guest ")
    assert shared[0].actor is Actor.SOMEBODY

    as_guest = await history_of_asset(temp_db, access, actors.guest, ASSET)
    assert [event.kind for event in as_guest] == ["added"]


async def test_a_restrict_reads_as_being_kept_from_somebody(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The other effect, and it must not read as a share with a different word in it."""
    await make_file(temp_db)
    await access.grant(ObjectType.ITEM, ASSET, actors.guest.id, Effect.RESTRICT)

    shared = [
        event
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if event.kind == "shared"
    ]

    assert shared[0].what.startswith("This file was kept private from guest ")


async def test_a_library_without_a_features_tables_still_has_a_history(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A process that never imported a feature has never registered its schema.

    So `face_scans` is genuinely absent rather than empty, and a query naming it is a hard error.
    Guarded on the table being there, which is the same shape the attribution migration uses for
    `folder_people` and for the same reason.
    """
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    for table in (
        "file_moves",
        "asset_stash_box_matches",
        "stash_boxes",
        "face_scans",
        "face_confirmations",
        "face_rejections",
        "face_tracks",
        "produced_files",
    ):
        # The name comes from the fixed tuple above, not from anything read: this is a test taking
        # tables away to prove the read stands without them.
        await temp_db.execute(
            f"DROP TABLE IF EXISTS {table}"  # nosemgrep: sift-no-string-built-sql
        )

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [event.kind for event in events] == ["added", "named"]


async def test_the_viewer_the_repository_hands_back_is_the_one_that_answers(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A guest loaded from the database is a guest here too.

    Asserted through `load_viewer` rather than through a hand-built `Viewer`, because the role is
    what decides half of what this read answers and a test that minted its own would be proving
    something about the test.
    """
    await make_file(temp_db)
    loaded = await access.load_viewer(actors.guest.id)
    assert loaded is not None and loaded.role is Role.GUEST

    assert [event.kind for event in await history_of_asset(temp_db, access, loaded, ASSET)] == [
        "added"
    ]
