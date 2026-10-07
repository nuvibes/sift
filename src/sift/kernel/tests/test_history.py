# SPDX-License-Identifier: AGPL-3.0-or-later
"""What happened to one file, in order.

The read joins seven sources never meant to be read together, so what goes wrong is a row in the
wrong order, actor or time unit; each is asserted on a file carrying one of everything. The face
tables keep MILLISECONDS where every other decision is in seconds.
"""

from __future__ import annotations

from collections.abc import Sequence

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
from sift.kernel.access import Effect, ObjectType, Repository, Role, Viewer
from sift.kernel.access.catalog import _DECISIONS_FOR_FILES
from sift.kernel.access.history import (
    _DECIDED,
    DEFAULT_LIMIT,
    KINDS,
    MAX_LIMIT,
    VIAS,
    Actor,
    Detail,
    Event,
    Link,
    files_called,
    history_of_asset,
)
from sift.kernel.access.history_folds import _receipt_of_object

# The wording lives next door: one table for all three histories. See `sentences.py`.
from sift.kernel.access.sentences import matched_sentence
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.testing.fixtures import Actors

pytestmark = pytest.mark.anyio

#: A fixed clock, so every assertion below is about order rather than about when the test ran.
ADDED_AT = 1_700_000_000
NAMED_AT = ADDED_AT + 100
TAGGED_AT = ADDED_AT + 200
FILED_AT = ADDED_AT + 300
ENRICHED_AT = ADDED_AT + 400
SCANNED_AT = ADDED_AT + 500
MOVED_AT = ADDED_AT + 600
UNDONE_AT = ADDED_AT + 700
DECIDED_AT = ADDED_AT + 800
REVERSED_AT = ADDED_AT + 900

ASSET = "01HX0000000000000000000501"
PERSON = "01HX0000000000000000000502"
TAG = "01HX0000000000000000000503"
SITE = "01HX0000000000000000000504"
USERNAME = "01HX0000000000000000000505"
BOX = "01HX0000000000000000000506"
ROOT = "01HX0000000000000000000507"
MOVE = "01HX0000000000000000000508"
TRACK = "01HX0000000000000000000509"
DECISION = "01HX0000000000000000000510"
SOURCE = "01HX0000000000000000000511"
#: The library every `make_file` file sits loose in. Its own root rather than `ROOT`, which the move
#: tests write with a plain INSERT and would collide with.
LIBRARY = "01HX0000000000000000000590"


async def make_file(database: Database, asset_id: str = ASSET) -> None:
    """One file in the library, loose in one root, with nothing decided about it: a receipt is
    shown only to a reader who may see every file it names, and a file with no location to nobody."""
    await database.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
        " VALUES (?, ?, 1, 'video', ?)",
        (asset_id, f"digest-{asset_id}", ADDED_AT),
    )
    await database.execute(
        "INSERT OR IGNORE INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (LIBRARY, "files", "/files", ADDED_AT),
    )
    await database.execute(
        "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename,"
        " first_seen_at, last_seen_at) VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
        (new_id(), asset_id, LIBRARY, f"{asset_id}.mp4", f"{asset_id}.mp4", ADDED_AT, ADDED_AT),
    )


async def name_person(database: Database, *, source: str | None, at: int | None) -> None:
    await database.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)",
        (PERSON, "Neve Alder", ADDED_AT),
    )
    await database.execute(
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (ASSET, PERSON, source, at),
    )


async def tag_it(database: Database, *, source: str | None, at: int | None) -> None:
    await database.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)", (TAG, "poolside", ADDED_AT)
    )
    await database.execute(
        "INSERT INTO asset_tags (asset_id, tag_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (ASSET, TAG, source, at),
    )


async def file_under_site(
    database: Database,
    *,
    name: str = "harlowquin",
    site: str | None = "Studio",
    source: str | None = None,
) -> None:
    """A filing on the file. `source` is the pass that made it, which is what draws the `via` mark."""
    if site is not None:
        await database.execute("INSERT INTO sites (id, name) VALUES (?, ?)", (SITE, site))
    await database.execute(
        "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, ?)",
        (USERNAME, SITE if site is not None else None, name, ADDED_AT),
    )
    await database.execute(
        "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (ASSET, USERNAME, source, FILED_AT),
    )


async def enrich(database: Database) -> None:
    await database.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, ?)",
        (BOX, "StashDB", "https://example.invalid/graphql", ADDED_AT),
    )
    await database.execute(
        "INSERT INTO asset_stash_box_matches"
        " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
        " VALUES (?, ?, 'remote', '{}', 'certain', 'applied', ?, ?)",
        (ASSET, BOX, ENRICHED_AT, ENRICHED_AT),
    )


async def ask_a_box(database: Database, *, found: bool, at: int = ENRICHED_AT) -> None:
    """A box ASKED about the file, and what it answered, written by `scan_one` on every ask. Every
    ask is a row of its own, so the moment is a parameter."""
    await database.execute(
        "INSERT OR IGNORE INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, ?)",
        (BOX, "StashDB", "https://example.invalid/graphql", ADDED_AT),
    )
    await database.execute(
        "INSERT INTO stash_box_scans (id, asset_id, box_id, scanned_at, found)"
        " VALUES (?, ?, ?, ?, ?)",
        (new_id(), ASSET, BOX, at, 1 if found else 0),
    )


async def scan_faces(
    database: Database,
    *,
    found: int,
    small: int | None = None,
    closer: int | None = None,
    why: tuple[int | None, int | None, int | None, int | None] = (None, None, None, None),
) -> None:
    """A face pass over the file. `scanned_at` is MILLISECONDS, as the face tables all are.

    `small` and `closer` are what the pass refused at each gate; None is a scan from before those
    were kept. `why` is the biggest face too small, then the closer look's blurred, turned-away and
    off-the-edge counts, None on a scan from before they were kept."""
    await database.execute(
        "INSERT INTO face_scans (asset_id, status, depth, coverage, frames_sampled, track_count,"
        " identified_count, detector, recognizer, settings_digest, scanned_at, refused_small,"
        " refused_closer, refused_largest, refused_blurred, refused_turned, refused_edge)"
        " VALUES (?, 'no_faces', 'fast', 1.0, 10, ?, 0, 'd', 'r', 'x', ?, ?, ?, ?, ?, ?, ?)",
        (ASSET, found, SCANNED_AT * 1000, small, closer, *why),
    )


async def landed_in(database: Database, to_rel_path: str) -> None:
    """The folders a move landed in, as rows, the way a library that holds the file has them.

    A move names its folder only as far as the reader may see it (`history._landed`), read off the
    folder rows, so a move into a folder with no row is said as one nobody may see.
    """
    parent: str | None = None
    parts = to_rel_path.split("/")[:-1]
    for depth in range(len(parts)):
        folder_id = new_id()
        await database.execute(
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
            (folder_id, ROOT, parent, "/".join(parts[: depth + 1]), parts[depth]),
        )
        parent = folder_id


async def move_file(
    database: Database, *, kind: str, by: str | None, undone: int | None = None
) -> None:
    await database.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (ROOT, "library", "/library", ADDED_AT),
    )
    await landed_in(database, "new/clip.mp4")
    await database.execute(
        "INSERT INTO file_moves (id, kind, location_id, asset_id, root_id, from_rel_path,"
        " to_rel_path, moved_by, moved_at, undone_at) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)",
        (MOVE, kind, ASSET, ROOT, "old/clip.mp4", "new/clip.mp4", by, MOVED_AT, undone),
    )


async def move_it(database: Database, *, to: str, kind: str = "move") -> None:
    """A move landing at one path. `move_file` above fixes the path; these tests vary it."""
    await database.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (ROOT, "library", "/library", ADDED_AT),
    )
    await landed_in(database, to)
    await database.execute(
        "INSERT INTO file_moves (id, kind, location_id, asset_id, root_id, from_rel_path,"
        " to_rel_path, moved_by, moved_at, undone_at) VALUES (?, ?, NULL, ?, ?, ?, ?, NULL, ?,"
        " NULL)",
        (MOVE, kind, ASSET, ROOT, "old/clip.mp4", to, MOVED_AT),
    )


async def only_admin(temp_db: Database, access: Repository, actors: Actors) -> Viewer:
    """An admin, reloaded from the database so the role is whatever the row says."""
    loaded = await access.load_viewer(actors.admin.id)
    assert loaded is not None
    return loaded


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


async def agree_to_a_face(
    database: Database, *, at: int, person: str = PERSON, asset: str = ASSET
) -> None:
    """A face on this file agreed to be somebody. Milliseconds, as the face tables all are."""
    await database.execute(
        "INSERT INTO face_confirmations (id, asset_id, person_id, embedding, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (new_id(), asset, person, b"\x00", at * 1000),
    )


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


async def matched_a_face(
    database: Database,
    *,
    at: int,
    sure: float | None = 0.92,
    person: str = PERSON,
    track: str | None = None,
) -> str:
    """An appearance Sift attached to somebody on its own. Milliseconds, as the face tables all are.

    `attribution` is what tells the three apart and it is the whole point of the fixture: a
    `suggested` row is a question nobody has answered and a `confirmed` one is somebody's answer,
    and neither may draw as a thing Sift decided.
    """
    track_id = track or new_id()
    await database.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, person_id,"
        " confidence, attribution, attributed_at, created_at)"
        " VALUES (?, ?, 0, 1, 1, 1.0, ?, ?, 'matched', ?, ?)",
        (track_id, ASSET, person, sure, at * 1000, ADDED_AT * 1000),
    )
    return track_id


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


async def make_findable(database: Database, asset_id: str, filename: str) -> None:
    """A file with somewhere to be, which is what makes it NAMEABLE: the name comes from where the
    bytes are, so this RENAMES the one location `make_file` gave it rather than adding a second
    whose name the read might choose; a file with no location is `test_a_copy_whose_original_is_gone`."""
    await database.execute(
        "UPDATE asset_locations SET rel_path = ?, filename = ? WHERE asset_id = ?",
        (filename, filename, asset_id),
    )


async def make_copy(
    database: Database, *, copy: str, source: str, by: str, operation: str = "compress"
) -> None:
    """One row of `produced_files`: `copy` was made out of `source`, by that verb."""
    await database.execute(
        "INSERT INTO produced_files (id, asset_id, source_asset_id, operation, produced_by,"
        " produced_at) VALUES (?, ?, ?, ?, ?, ?)",
        (new_id(), copy, source, operation, by, MOVED_AT),
    )


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


# --- a workbench decision that named this file ---------------------------------------------------


async def decide(
    database: Database,
    *,
    queue: str = "folders",
    title: str = "Ilva Brennan - 47 files",
    by: str | None,
    reversed_at: int | None = None,
    about: str = ASSET,
    decision_id: str = DECISION,
    verb: str = "decided",
    object_kind: str | None = None,
    object_id: str | None = None,
    payload: str = "{}",
) -> None:
    """One bulk judgement, and the link saying it named this file: both rows, since either alone
    is a state the application cannot produce; `verb` and the object are what the pane folds on."""
    await database.execute(
        "INSERT INTO workbench_decisions"
        " (id, queue, user_id, title, detail, payload, decided_at, reversed_at,"
        " verb, object_kind, object_id)"
        " VALUES (?, ?, ?, ?, 'It did something.', ?, ?, ?, ?, ?, ?)",
        (
            decision_id,
            queue,
            by,
            title,
            payload,
            DECIDED_AT,
            reversed_at,
            verb,
            object_kind,
            object_id,
        ),
    )
    await database.execute(
        "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
        " VALUES (?, 'asset', ?)",
        (decision_id, about),
    )


async def test_a_decision_that_named_this_file_is_in_its_history(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The event this whole link table exists for.

    The sentence is the decision's own title, written by the queue at the moment it happened and
    passed through unchanged: no sentence in a history carries a full stop, so there is nothing to
    add.
    """
    await make_file(temp_db)
    await decide(temp_db, by=actors.admin.id)

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [(event.kind, event.at, event.what) for event in events] == [
        ("added", ADDED_AT, "Sift added this file to the library"),
        ("decided", DECIDED_AT, "Ilva Brennan - 47 files"),
    ]
    assert events[-1].actor is Actor.YOU
    assert "decided" in KINDS


async def test_a_decision_somebody_else_took_is_named_only_to_an_admin(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The same rule a move follows, and for the same reason: who else is a user here is not
    something a file the guest may see should disclose."""
    await make_file(temp_db)
    await access.grant(ObjectType.ITEM, ASSET, actors.guest.id, Effect.SHARE)
    await decide(temp_db, by=actors.admin.id)

    guest = await history_of_asset(temp_db, access, actors.guest, ASSET)
    decided = next(event for event in guest if event.kind == "decided")

    assert decided.actor is Actor.ANOTHER_USER
    assert decided.actor_name is None


async def test_a_decision_a_pass_wrote_names_sift(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A decision no user is recorded against is SIFT's, as the ledger's own reader says.

    Not `SOMEBODY`: a card read by `user_id` alone would show no actor for every decision Sift made
    while the feed says "Sift" about the same press. The migration that widened the table decided a
    row with no user is Sift's (`_BACKFILL_ACTOR`), and the card reads the actor the ledger wrote.
    """
    await make_file(temp_db)
    await decide(temp_db, by=None)

    decided = next(
        event
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if event.kind == "decided"
    )

    assert decided.actor is Actor.SIFT
    assert decided.actor_name == "Sift"


async def test_an_admin_is_offered_the_decision_door(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """`decision`, not `move`. The client sends the two to different addresses and holding only an
    id would make it work out which from the kind, in a language that cannot check it."""
    await make_file(temp_db)
    await decide(temp_db, by=actors.admin.id)

    decided = next(
        event
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if event.kind == "decided"
    )

    assert decided.undo is not None
    assert (decided.undo.kind, decided.undo.id) == ("decision", DECISION)


async def test_a_guest_is_offered_no_way_to_take_a_decision_back(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The workbench's undo route is admin-only, so the button would be refused on press. An
    affordance the server would refuse is worse than none, because the only way to find out is to
    press it."""
    await make_file(temp_db)
    await access.grant(ObjectType.ITEM, ASSET, actors.guest.id, Effect.SHARE)
    await decide(temp_db, by=actors.admin.id)

    decided = next(
        event
        for event in await history_of_asset(temp_db, access, actors.guest, ASSET)
        if event.kind == "decided"
    )

    assert decided.undo is None


async def test_a_queue_whose_decisions_are_final_offers_no_undo(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A copy released from a disk is gone. The registry is the only thing that knows which kinds
    of decision can never be taken back, so the caller reads it and hands the answer over."""
    await make_file(temp_db)
    await decide(temp_db, queue="copies", by=actors.admin.id)

    final = next(
        event
        for event in await history_of_asset(
            temp_db, access, actors.admin, ASSET, final_queues=("copies",)
        )
        if event.kind == "decided"
    )
    offered = next(
        event
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if event.kind == "decided"
    )

    assert final.undo is None
    assert offered.undo is not None


async def test_a_decision_taken_back_keeps_its_place_and_gains_a_line(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A history that quietly loses its reversals reads as though nothing ever happened.

    The reversal names nobody: `workbench_decisions` records who DECIDED and the moment it was
    reversed, and nothing at all about who reversed it.
    """
    await make_file(temp_db)
    await decide(temp_db, by=actors.admin.id, reversed_at=REVERSED_AT)

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [(event.kind, event.at) for event in events] == [
        ("added", ADDED_AT),
        ("decided", DECIDED_AT),
        ("undone", REVERSED_AT),
    ]
    assert events[1].reversed is True
    assert events[1].undo is None
    assert events[2].what == "That decision was undone"
    assert events[2].actor is Actor.SOMEBODY
    assert events[2].actor_name is None


async def test_a_song_name_taken_back_is_said_as_one(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The reversal of a shared song name is a song name put back, not "a decision": the line
    says what was undone, since the file page shows the name gone beside it."""
    await make_file(temp_db)
    await decide(
        temp_db,
        queue="music_names",
        title="Sift named the song Blue - Marla Quist, shared with a.mp4",
        by=None,
        reversed_at=REVERSED_AT,
        verb="song_named",
        payload='{"song": "Blue - Marla Quist", "source": "shared"}',
    )

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    # The receipt wears the song's own mark, never the Organize tray: nobody decided it there.
    assert [event.kind for event in events][-2:] == ["song_named", "undone"]
    assert events[-1].what == "That song name was undone"


#: What the filing line says on its own, and the title the filename pass writes about the same act.
#:
#: TWO STRINGS, and that they differ is the point: the fold keys on the receipt's id, so a title
#: stored long ago and a sentence improved since are still one act. `FILENAME_TITLE` is the stored
#: one, "own" and all, exactly as a stored receipt carries it.
FILED_SENTENCE = "Filed under harlowquin on Studio"
FILENAME_TITLE = f"{FILED_SENTENCE} from the file's own name"
#: The filing's own line, the actor first and the task at the back, and the ONE sentence the fold
#: keeps: the line built from the rows, with the receipt's Undo on it.
FILED_LINE = "Sift filed this file under harlowquin on Studio from the file's name"


async def test_a_filing_and_the_receipt_that_restates_it_are_one_line(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """One act wrote two rows, so the pane must not show it twice.

    The filename pass files the file AND writes a receipt whose title is the file's own line, so
    read straight out the pane said it twice, once with an Undo. The one that says more survives.
    """
    await make_file(temp_db)
    await file_under_site(temp_db, source="filename")
    await decide(
        temp_db,
        queue="filenames",
        title=FILENAME_TITLE,
        by=None,
        verb="filed",
        object_kind="username",
        object_id=USERNAME,
    )

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    # ONE LINE, the one built from the rows as they stand: the stored title said "own", and the
    # line the fold keeps is the filing's own, carrying the receipt's Undo.
    assert [(event.kind, event.what) for event in events] == [
        ("added", "Sift added this file to the library"),
        ("decided", FILED_LINE),
    ]


async def test_the_folded_line_keeps_the_mark_and_the_way_the_dropped_line_carried(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The two things only the filing line said, and the Undo it never had.

    A receipt's title carries neither the `enriched:` word nor the way to the site, so the fold
    keeps both, and the Undo survives because the receipt exists to be taken back."""
    await make_file(temp_db)
    await file_under_site(temp_db, source="filename")
    await decide(
        temp_db,
        queue="filenames",
        title=FILENAME_TITLE,
        by=None,
        verb="filed",
        object_kind="username",
        object_id=USERNAME,
    )

    folded = (await history_of_asset(temp_db, access, actors.admin, ASSET))[-1]

    assert folded.via == "filename"
    # The username is a linked piece, ahead of the Site it belongs to.
    assert folded.links == (
        Link(kind="username", id=USERNAME, name="harlowquin", href=f"/browse?username={USERNAME}"),
        Link(kind="site", id=SITE, name="Studio"),
    )
    assert folded.undo is not None
    assert (folded.undo.kind, folded.undo.id) == ("decision", DECISION)


async def test_a_filing_with_no_receipt_behind_it_is_still_a_line_of_its_own(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The filings that predate the receipt, which is most of them in any library that upgraded.

    Nothing was backfilled into the subject table and nothing could be, so an old filing has no
    decision to fold into. It has to go on being drawn: a fold that quietly needed a receipt
    would empty the older half of every history.
    """
    await make_file(temp_db)
    await file_under_site(temp_db, source="filename")

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [(event.kind, event.what, event.via) for event in events] == [
        ("added", "Sift added this file to the library", None),
        # Its source, at the BACK of the sentence and in the receipt's own words, which is what
        # keeps the fold above exact rather than breaking it. See `_FILED_FROM`.
        ("filed", FILED_LINE, "filename"),
    ]


async def test_a_receipt_that_does_not_restate_a_line_folds_nothing(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A folder claim says something the naming line cannot, so both stay.

    "<person>, 47 files" is how many files one press touched; "<person> was named in this file"
    is what it did to this one. Folding on the clock would drop a line for saying something else.
    """
    await make_file(temp_db)
    await name_person(temp_db, source="folder", at=NAMED_AT)
    await decide(temp_db, title="Neve Alder - 47 files", by=actors.admin.id)

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [event.kind for event in events] == ["added", "named", "decided"]


def test_a_receipt_that_names_half_an_object_is_not_a_key_a_line_could_meet() -> None:
    """A receipt with no object, or with only one half of it, is skipped rather than keyed: a key
    made from the half it has would be an invented one, which a line could meet by accident."""
    rows = [
        {"id": "r1", "object_kind": "person", "object_id": None},
        {"id": "r2", "object_kind": None, "object_id": "p1"},
        {"id": "r3", "object_kind": None, "object_id": None},
        {"id": "r4", "object_kind": "person", "object_id": "p1"},
    ]

    assert _receipt_of_object(rows) == {("person", "p1"): "r4"}  # type: ignore[arg-type]


async def test_the_count_on_the_tab_follows_the_fold(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The number and the pane are one assembly, so a fold cannot make them disagree.

    Asserted against `len(...)` for the reason the other count tests are, with the literal beside
    it so a count answering nothing at all could not pass: two lines off three rows, because the
    filing and its receipt are one act.
    """
    await make_file(temp_db)
    await file_under_site(temp_db, source="filename")
    await decide(
        temp_db,
        queue="filenames",
        title=FILENAME_TITLE,
        by=None,
        verb="filed",
        object_kind="username",
        object_id=USERNAME,
    )

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert len(events) == 2


async def test_a_receipt_naming_a_file_the_reader_may_not_see_is_not_shown(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A receipt's title carries a number counted when it was written ("matched 344 more faces")
    over files this reader may never have been shown, and it cannot be recounted per reader. So a
    guest reading a file they were given is not shown a receipt that also named a file they were
    not; an admin, who may see both, still is. The rule the person and entity threads and the
    event ledger already read (`history_events.NOTHING_HIDDEN`)."""
    other = "01HX0000000000000000000591"
    await make_file(temp_db)
    await make_file(temp_db, asset_id=other)
    await decide(temp_db, title="Sift matched 344 more faces to Neve Alder", by=None)
    await temp_db.execute(
        "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
        " VALUES (?, 'asset', ?)",
        (DECISION, other),
    )
    await access.grant(ObjectType.ITEM, ASSET, actors.guest.id, Effect.SHARE)

    guest = [event.kind for event in await history_of_asset(temp_db, access, actors.guest, ASSET)]
    admin = [event.kind for event in await history_of_asset(temp_db, access, actors.admin, ASSET)]

    assert "decided" not in guest
    assert "decided" in admin


async def test_a_decision_about_another_file_is_not_in_this_ones_history(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The link is what makes the read narrow. Without the subject predicate every file in the
    library would carry every decision anybody ever took."""
    await make_file(temp_db)
    await decide(temp_db, by=actors.admin.id, about="01HX0000000000000000000512")

    assert [
        event.kind for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
    ] == ["added"]


async def test_a_title_arrives_exactly_as_the_queue_wrote_it(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Punctuation and all: no stop is added, and a title with its own stop is the case that
    proves nothing is being trimmed either."""
    await make_file(temp_db)
    await decide(temp_db, title="These are different.", by=actors.admin.id)

    decided = next(
        event
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if event.kind == "decided"
    )

    assert decided.what == "These are different."


async def test_a_decision_with_no_title_at_all_is_a_blank_line_and_not_a_crash(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The column is NOT NULL and every queue writes a real sentence, so this is not a state the
    application produces. It is kept because what it actually asserts is that a blank line is one
    row of the thread and not a broken route."""
    await make_file(temp_db)
    await decide(temp_db, title="", by=actors.admin.id)

    decided = next(
        event
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if event.kind == "decided"
    )

    assert decided.what == ""


async def test_a_library_with_no_workbench_still_has_a_history(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A process that never imported the workbench has never registered its schema, so the two
    tables are genuinely absent and a query naming them is a hard error rather than an empty
    answer. Guarded on both, because the statement names both."""
    await make_file(temp_db)
    for table in ("workbench_decision_subjects", "workbench_decisions"):
        # From the fixed tuple above, not from anything read: this is a test taking tables away to
        # prove the read stands without them.
        await temp_db.execute(
            f"DROP TABLE IF EXISTS {table}"  # nosemgrep: sift-no-string-built-sql
        )

    assert [
        event.kind for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
    ] == ["added"]


async def test_the_per_file_decision_read_seeks_on_its_index(
    temp_db: Database, access: Repository
) -> None:
    """The decisions a file was part of are found by a seek, never by walking every decision.

    Here so a change to the index has a test that sees it. The constraint is
    asserted too: an index on the same columns the other way round is also "used", it just walks."""
    del access  # the repository is what boots the schema the statement joins
    plan = await temp_db.fetch_all(
        "EXPLAIN QUERY PLAN " + _DECIDED.sql,  # nosemgrep: sift-no-string-built-sql
        # The ledger's own queue word is excluded (an event is not a bulk judgement), and the
        # reader's three values are what `NOTHING_HIDDEN` binds. See `_DECIDED`.
        {"subject": ASSET, "ledger": "ledger", "viewer": "v", "reveal": 0, "admin": 0},
    )
    steps = [str(row["detail"]) for row in plan]
    assert any(
        "USING INDEX ix_workbench_subject (kind=? AND subject_id=?)" in step for step in steps
    ), steps
    assert not any(step.startswith("SCAN ") for step in steps), steps


async def test_the_read_of_a_pages_decisions_pins_which_side_it_walks(
    temp_db: Database, access: Repository
) -> None:
    """The same question asked for a whole page of files, and it has to be driven from the files:
    left to choose, SQLite drives it from the decisions side, hundreds of times slower. The KEYWORD
    is asserted and not the plan: an empty database has no statistics and drives from the subjects
    either way, so a plan assertion could not fail; `sqlite_stat1` on a maintained library is what
    flips it, so what is guarded is that the statement still says which side it walks.
    """
    del access, temp_db  # the statement is what is under test, not any database's opinion of it
    assert "CROSS JOIN workbench_decisions" in _DECISIONS_FOR_FILES


# --- what a sentence NAMES, and which of the three ways it arrived ---------------------------


async def test_every_sentence_that_names_an_entity_carries_the_way_to_it(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The name travels beside the id, and it is the same run of characters the sentence holds.

    A client handed only an id would have to work out which part of `Tagged poolside` stands for the
    tag, which is a second mapping written in a language that cannot check it. Handed the name,
    drawing a link is finding that run.
    """
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    await tag_it(temp_db, source=None, at=TAGGED_AT)
    await file_under_site(temp_db)

    named = {
        event.kind: [(one.kind, one.id, one.name) for one in event.links]
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
    }

    assert named["named"] == [("person", PERSON, "Neve Alder")]
    assert named["tagged"] == [("tag", TAG, "poolside")]
    # The SITE and never the username: a site is a row with a page and a username is a name on
    # one with no page anywhere in Sift, so the username stays words.
    assert named["filed"] == [("username", USERNAME, "harlowquin"), ("site", SITE, "Studio")]
    assert named["added"] == []


async def test_a_filing_whose_site_has_gone_names_nothing_to_go_to(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """`usernames.site_id` is ON DELETE SET NULL, so a filing can outlive its site, and the
    sentence already says "Filed under <username>" and no site, so there is nothing to find."""
    await make_file(temp_db)
    await file_under_site(temp_db, name="harlowquin", site=None)

    filed = next(
        event
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if event.kind == "filed"
    )

    # The username is a linked piece; it is the SITE that has nothing to go to.
    assert not [one for one in filed.links if one.kind == "site"]


async def test_a_move_names_the_folder_it_landed_in(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Said by its path and linked by its id: `in:` reads a path as every folder under it, so a
    path-built link opens a wider list than the folder. A folder gone since is said in words."""
    await make_file(temp_db)
    await move_it(temp_db, to="holiday/2024/clip.mp4")
    folder = await temp_db.fetch_one(
        "SELECT id FROM folders WHERE root_id = ? AND rel_path = 'holiday/2024'", (ROOT,)
    )
    assert folder is not None

    moved = next(
        event
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if event.kind == "moved"
    )

    assert moved.what == "This file was moved to holiday/2024"
    assert [(one.kind, one.id, one.name, one.href) for one in moved.links] == [
        ("folder", str(folder["id"]), "holiday/2024", f"/browse?in={folder['id']}")
    ]

    await temp_db.execute("DELETE FROM folders WHERE id = ?", (str(folder["id"]),))
    gone = next(
        event
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if event.kind == "moved"
    )
    assert gone.links == ()


async def test_a_move_to_the_top_of_the_library_names_a_phrase_and_not_a_folder(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """ "The top of the library" is words, not a folder. A link built from it would resolve to
    nothing, and a name that looks like a way somewhere and goes nowhere is worse than plain text."""
    await make_file(temp_db)
    await move_it(temp_db, to="clip.mp4")

    moved = next(
        event
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if event.kind == "moved"
    )

    assert moved.links == ()


async def test_a_rename_names_nothing_because_the_only_thing_it_names_is_this_file(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    await make_file(temp_db)
    await move_it(temp_db, to="new.mp4", kind="rename")

    renamed = next(
        event
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if event.kind == "renamed"
    )

    assert renamed.links == ()


@pytest.mark.parametrize(
    ("source", "via"),
    [(None, None), ("stash_box", "stash"), ("folder", "folder"), ("username", None)],
)
async def test_which_of_the_three_ways_an_attribution_arrived(
    temp_db: Database, access: Repository, actors: Actors, source: str | None, via: str | None
) -> None:
    """The actor separates a stash-box from Sift; the source tells a folder read from a username
    match.

    Only two of the three marks come out of this column, truly: a naming from a face has no source
    here, and the faces slice says it with `confirmed` and `rejected` of its own."""
    await make_file(temp_db)
    await name_person(temp_db, source=source, at=NAMED_AT)

    event = next(
        one
        for one in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if one.kind == "named"
    )

    assert event.via == via


async def test_a_stash_box_match_says_it_was_a_stash_box(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The same read the `enriched:stash` filter reads, so the word is that fact said again rather
    than a guess made about the row."""
    await make_file(temp_db)
    await enrich(temp_db)

    event = next(
        one
        for one in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if one.kind == "enriched"
    )

    assert event.via == "stash"
    assert event.via in VIAS


# --- the number on the tab -----------------------------------------------------------------------


async def test_the_count_is_the_length_of_the_history_with_one_of_everything_in_it(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The one property the tab's number has to have, over a file carrying every kind together.

    Against `len(...)`, because a literal would keep passing the day a source drew two lines for
    one, which is how a tab comes to disagree with its pane. The literal beside it only stops a
    count that answered nothing: ten lines off nine rows (the move and the decision were taken back,
    and the stash-box's tagging is said on the box's own line)."""
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    await tag_it(temp_db, source="stash_box", at=TAGGED_AT)
    await file_under_site(temp_db)
    await enrich(temp_db)
    await scan_faces(temp_db, found=2)
    # Taken back, so this one row is TWO lines: the half a separately assembled count gets wrong.
    await move_file(temp_db, kind="move", by=actors.admin.id, undone=UNDONE_AT)
    await decide(temp_db, by=actors.admin.id, reversed_at=REVERSED_AT)
    await access.grant(ObjectType.ITEM, ASSET, actors.guest.id, Effect.SHARE)

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert len(events) == 10


async def test_the_count_answers_each_viewer_what_that_viewer_would_be_shown(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A guest is not told how many sharing lines an admin can see.

    The sharing half of a history is about other USERS rather than about the file, so the kernel
    leaves it out for anybody but an admin, and a count taken as somebody else would promise a
    guest a row the pane would then not draw.
    """
    await make_file(temp_db)
    await access.grant(ObjectType.ITEM, ASSET, actors.guest.id, Effect.SHARE)

    assert len(await history_of_asset(temp_db, access, actors.admin, ASSET)) == 2
    assert len(await history_of_asset(temp_db, access, actors.guest, ASSET)) == 1


async def test_the_count_is_capped_the_way_the_pane_is(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """It counts what would be DRAWN, not what exists.

    The pane asks for the newest `limit` and the tab has to say the same number, or a busy file
    reads as a pane that lost rows on the way to the screen.
    """
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    await tag_it(temp_db, source=None, at=TAGGED_AT)
    await file_under_site(temp_db)

    assert len(await history_of_asset(temp_db, access, actors.admin, ASSET)) == 4
    assert len(await history_of_asset(temp_db, access, actors.admin, ASSET, limit=2)) == 2


async def test_a_file_that_is_not_there_counts_nothing(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Zero rather than one: there is no arrival line for a file that never arrived."""
    assert await history_of_asset(temp_db, access, actors.admin, "01HX00000000000000000005ZZ") == []


#: A second person, tag and box, for the presses that touch more than one thing.
OTHER_PERSON = "01HX0000000000000000000512"
OTHER_TAG = "01HX0000000000000000000513"
OTHER_BOX = "01HX0000000000000000000514"
PILE = "01HX0000000000000000000515"
OTHER_PILE = "01HX0000000000000000000516"


async def name_another(database: Database, *, source: str | None, at: int | None) -> None:
    """A second person on the same file, so a press can be more than one row."""
    await database.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)",
        (OTHER_PERSON, "Ada Lumen", ADDED_AT),
    )
    await database.execute(
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (ASSET, OTHER_PERSON, source, at),
    )


async def tag_again(database: Database, *, source: str | None, at: int | None) -> None:
    """A second tag on the same file."""
    await database.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)",
        (OTHER_TAG, "split screen", ADDED_AT),
    )
    await database.execute(
        "INSERT INTO asset_tags (asset_id, tag_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (ASSET, OTHER_TAG, source, at),
    )


async def a_face(
    database: Database,
    *,
    track: str,
    person: str | None,
    pile: str | None,
    at: int,
    question: bool = False,
) -> None:
    """One appearance of one face on the file: named, waiting to be, or (a question) only asked."""
    if pile is not None:
        await database.execute(
            "INSERT OR IGNORE INTO face_piles (id, status, centroid, size, created_at, updated_at)"
            " VALUES (?, 'open', ?, 1, ?, ?)",
            (pile, b"\x00", ADDED_AT, ADDED_AT),
        )
    # A named face here is one Sift recognized: a question (attribution suggested) does not name
    # the file, and the "found" line names only what names the file.
    await database.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, person_id,"
        " pile_id, created_at, attribution) VALUES (?, ?, ?, ?, 1, 1.0, ?, ?, ?,"
        " CASE WHEN ? IS NULL THEN NULL WHEN ? THEN 'suggested' ELSE 'matched' END)",
        (track, ASSET, at, at, person, pile, ADDED_AT * 1000, person, question),
    )


def said_of(events: Sequence[Event], kind: str) -> Event:
    """The one event of a kind, so a test can assert about it without counting rows first."""
    found = [one for one in events if one.kind == kind]
    assert len(found) == 1
    return found[0]


async def test_a_question_is_said_as_a_face_that_may_be_them(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A face Sift only ASKED about does not name the file, and it is not nobody either: the scan's
    line names the people Sift recognized, then says the question as a face that may be its person,
    with the way to the person and the way to where the question is answered. To a guest, who is
    not the one the question is for, it is a face nobody has named, as it was."""
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    await name_another(temp_db, source=None, at=NAMED_AT)
    await scan_faces(temp_db, found=2)
    await a_face(temp_db, track=TRACK, person=PERSON, pile=None, at=0)
    await a_face(temp_db, track=new_id(), person=OTHER_PERSON, pile=None, at=10, question=True)

    scan = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "face_run")

    assert (
        scan.what
        == "Sift looked for faces here and found Neve Alder and a face that may be Ada Lumen"
    )
    assert scan.links == (
        Link(kind="person", id=PERSON, name="Neve Alder"),
        Link(
            kind="faces",
            id=OTHER_PERSON,
            name="a face",
            href=f"/organize/known-people/{OTHER_PERSON}?show=suggested",
        ),
        Link(kind="person", id=OTHER_PERSON, name="Ada Lumen"),
    )
    guest = said_of(list(await history_of_asset(temp_db, access, actors.guest, ASSET)), "face_run")
    assert guest.what == "Sift looked for faces here and found Neve Alder and a face"
    assert Link(kind="person", id=OTHER_PERSON, name="Ada Lumen") not in guest.links


async def test_named_faces_come_first_then_the_ones_that_may_be_somebody_then_the_unnamed(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Three kinds in one line, in that order. A person both named and suggested on the file is said
    once, as named: the suggestion was the question the name answers. Two faces that may be one
    person are one phrase, counted."""
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    await name_another(temp_db, source=None, at=NAMED_AT)
    await scan_faces(temp_db, found=5)
    await a_face(temp_db, track=new_id(), person=None, pile=PILE, at=0)
    await a_face(temp_db, track=new_id(), person=OTHER_PERSON, pile=None, at=10, question=True)
    await a_face(temp_db, track=TRACK, person=PERSON, pile=None, at=20)
    await a_face(temp_db, track=new_id(), person=PERSON, pile=None, at=30, question=True)
    await a_face(temp_db, track=new_id(), person=OTHER_PERSON, pile=None, at=40, question=True)

    scan = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "face_run")

    assert scan.what == (
        "Sift looked for faces here and found Neve Alder, 2 faces that may be Ada Lumen and a face"
    )
    assert [(one.kind, one.name) for one in scan.links] == [
        ("person", "Neve Alder"),
        ("faces", "2 faces"),
        ("person", "Ada Lumen"),
        ("face_pile", "a face"),
    ]


async def test_a_scan_names_the_faces_it_found_and_links_each_of_them(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A count is the one thing nobody needs from the scan's sentence.

    "found 3" is a number somebody then has to go and look up. The people it recognised are named
    and go to their pages; what is left goes to the group it is waiting in, which is where somebody
    would put a name to it.
    """
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    await name_another(temp_db, source=None, at=NAMED_AT)
    await scan_faces(temp_db, found=3)
    await a_face(temp_db, track=TRACK, person=PERSON, pile=None, at=0)
    await a_face(temp_db, track=new_id(), person=OTHER_PERSON, pile=None, at=10)
    await a_face(temp_db, track=new_id(), person=None, pile=PILE, at=20)

    scan = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "face_run")

    assert scan.what == "Sift looked for faces here and found Neve Alder, Ada Lumen and a face"
    assert scan.links == (
        Link(kind="person", id=PERSON, name="Neve Alder"),
        Link(kind="person", id=OTHER_PERSON, name="Ada Lumen"),
        Link(kind="face_pile", id=PILE, name="a face"),
    )


async def test_one_person_seen_twice_is_named_once(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A person walking in and out of shot is two tracks and one person.

    Listing them per track would read as two people with the same name, which is the one thing a
    sentence naming people may not do.
    """
    await make_file(temp_db)
    await name_person(temp_db, source=None, at=NAMED_AT)
    await scan_faces(temp_db, found=2)
    await a_face(temp_db, track=TRACK, person=PERSON, pile=None, at=0)
    await a_face(temp_db, track=new_id(), person=PERSON, pile=None, at=90)

    scan = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "face_run")

    assert scan.what == "Sift looked for faces here and found Neve Alder"
    assert scan.links == (Link(kind="person", id=PERSON, name="Neve Alder"),)


async def test_faces_waiting_in_two_groups_are_counted_and_not_linked(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """One phrase cannot go to two places.

    "2 faces" is one run of characters and the two are waiting in different groups, so any link on
    it would take somebody to one of them and silently not to the other, which is the rule a move
    into "the top of the library" already follows. The count is still true.
    """
    await make_file(temp_db)
    await scan_faces(temp_db, found=2)
    await a_face(temp_db, track=TRACK, person=None, pile=PILE, at=0)
    await a_face(temp_db, track=new_id(), person=None, pile=OTHER_PILE, at=10)

    scan = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "face_run")

    assert scan.what == "Sift looked for faces here and found 2 faces"
    assert scan.links == ()


async def test_a_scan_whose_tracks_are_gone_still_says_how_many_it_found(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The count is the fallback, and it is the truth about a scan with nothing left to name.

    Tracks are tidied away; a process that never imported the faces slice has no table to read. The
    number is on the scan row either way, so the line goes on saying what it always said rather
    than claiming the file has no faces in it.
    """
    await make_file(temp_db)
    await scan_faces(temp_db, found=3)

    scan = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "face_run")

    assert scan.what == "Sift looked for faces here and found 3"


async def test_one_passs_naming_press_is_one_line_that_lists_everybody(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Seventeen lines saying the same thing about a different person is one act, drawn once.

    Nothing there repeats another line (each names somebody else), so the fold on sentences
    cannot see it. What makes them one act is the SOURCE, and the names go into `detail` so the row
    opens to the whole list.
    """
    await make_file(temp_db)
    await name_person(temp_db, source="folder", at=NAMED_AT)
    await name_another(temp_db, source="folder", at=NAMED_AT + 5)

    named = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "named")

    assert named.what == "Sift named 2 people here from a folder name"
    # The moment the press FINISHED, so one act sits in one place in the order.
    assert named.at == NAMED_AT + 5
    # Nothing in `links`: the sentence names nobody, and a client looks for a link's name inside it.
    assert named.links == ()
    # One group, headed by the same phrase the sentence counted them in: the heading over the
    # names and the count in the line above them are one string. See `Detail`.
    assert named.detail == (
        Detail(
            kind="person",
            words="2 people",
            links=(
                Link(kind="person", id=PERSON, name="Neve Alder"),
                Link(kind="person", id=OTHER_PERSON, name="Ada Lumen"),
            ),
        ),
    )
    assert named.detail[0].words in named.what


async def test_two_passes_naming_the_same_file_stay_two_lines(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A folder read and somebody typing are two acts, and folding them would say one pass did both."""
    await make_file(temp_db)
    await name_person(temp_db, source="folder", at=NAMED_AT)
    await name_another(temp_db, source=None, at=NAMED_AT + 5)

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)

    assert [one.what for one in events if one.kind == "named"] == [
        "Sift named Neve Alder in this file from a folder name",
        "Ada Lumen was named here",
    ]


async def test_a_stash_boxs_line_says_what_it_wrote_and_lists_it(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """One press by one box is one line, and the lines it wrote are folded into it.

    A line beside the box's own saying "A stash-box named 2 people" would be the same duplicate
    attribution the receipt fold exists to end: the box's line already says it recognised the file,
    so what it wrote belongs there.
    """
    await make_file(temp_db)
    await enrich(temp_db)
    await name_person(temp_db, source="stash_box", at=ENRICHED_AT)
    await name_another(temp_db, source="stash_box", at=ENRICHED_AT)
    await tag_it(temp_db, source="stash_box", at=ENRICHED_AT)
    await tag_again(temp_db, source="stash_box", at=ENRICHED_AT)
    await file_under_site(temp_db, source="stash_box")

    events = list(await history_of_asset(temp_db, access, actors.admin, ASSET))

    assert [one.kind for one in events] == ["added", "enriched"]
    box = said_of(events, "enriched")
    assert (
        box.what
        == "StashDB recognized this file, a certain match, and wrote 2 people, the site and 2 tags"
    )
    # THREE GROUPS, in the sentence's own order, each headed by the phrase the sentence used for it:
    # run together they would be five names with nothing saying which is a person and which a
    # tag.
    assert [(one.kind, one.words) for one in box.detail] == [
        ("person", "2 people"),
        ("site", "the site"),
        ("tag", "2 tags"),
    ]
    assert [[link.name for link in one.links] for one in box.detail] == [
        ["Neve Alder", "Ada Lumen"],
        ["harlowquin", "Studio"],
        ["poolside", "split screen"],
    ]
    # The heading and the count are the same string, not two ways of saying it.
    for one in box.detail:
        assert one.words in box.what


async def test_a_filing_with_no_site_left_is_counted_and_has_no_group_to_open_to(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The act happened and there is nowhere to send anybody, so the line says so and opens to nothing.

    `usernames.site_id` is `ON DELETE SET NULL`, so a filing outlives the site it named. A group
    drawn for it would be a heading with an empty column under it, and dropping the phrase from the
    sentence instead would be the line under-counting what the box actually wrote.
    """
    await make_file(temp_db)
    await enrich(temp_db)
    await name_person(temp_db, source="stash_box", at=ENRICHED_AT)
    await file_under_site(temp_db, site=None, source="stash_box")

    box = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "enriched")

    assert (
        box.what == "StashDB recognized this file, a certain match, and wrote 1 person and the site"
    )
    assert [(one.kind, one.words) for one in box.detail] == [
        ("person", "1 person"),
        ("site", "the site"),
    ]


async def test_a_filing_with_no_handle_and_no_site_left_is_counted_and_opens_to_nothing(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A filing under nobody's name on a site since deleted names nothing a link could open: the
    sentence still counts it, and no group is drawn for it, which would be a heading over nothing."""
    await make_file(temp_db)
    await enrich(temp_db)
    await name_person(temp_db, source="stash_box", at=ENRICHED_AT)
    await file_under_site(temp_db, name="", site=None, source="stash_box")

    box = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "enriched")

    assert box.what.endswith("and wrote 1 person and the site")
    assert [(one.kind, one.words) for one in box.detail] == [("person", "1 person")]


async def record_the_ask(database: Database, *, applied: str | None) -> None:
    """What an ask FILLED IN, which is the half of the fact the file's own rows cannot carry.

    `enrichment_runs.applied`, version 52 of the catalog. Seeded as SQL beside `enrich` above,
    which writes the match this hangs off.
    """
    await database.execute(
        "INSERT INTO enrichment_runs (id, subject, local_id, box_id, at, automatic, applied)"
        " VALUES ('run-1', 'asset', ?, ?, ?, 1, ?)",
        (ASSET, BOX, ENRICHED_AT, applied),
    )


async def test_the_fields_the_rows_cannot_account_for_finish_the_box_s_sentence(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A title and a set of details are written with nothing on the file recording who wrote them.

    They come off the run's own list of what it filled in (version 52 of the catalog), AFTER the
    rows, which are what somebody opens the line to look at.
    """
    await make_file(temp_db)
    await enrich(temp_db)
    await record_the_ask(temp_db, applied='["title", "details"]')
    await name_person(temp_db, source="stash_box", at=ENRICHED_AT)

    box = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "enriched")

    # "automatically" because the seeded run says so: `enrichment_runs` carries that fact and
    # the applied list in the same row, so a test for one always draws the other.
    assert box.what == (
        "StashDB recognized this file, a certain match, and wrote 1 person, the title and the details automatically"
    )
    # ONE GROUP and not three. The title and the details are counted in the sentence and have no
    # page to open to; only the person is a thing with an address. And no site: nothing filed this
    # file under one, and a group whose heading is not in the sentence is a group for nothing.
    assert [(one.kind, one.words) for one in box.detail] == [("person", "1 person")]
    for one in box.detail:
        assert one.words in box.what


async def test_the_people_and_tags_are_counted_off_the_rows_and_never_twice(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A stored list naming the people and the tags must not add a second count of the same act.

    The rows wear the word for the pass that wrote them; the list says what was true then. Counting
    one act from two sources free to disagree is wrong, and `_ROW_FIELDS` is the line between them.
    """
    await make_file(temp_db)
    await enrich(temp_db)
    await record_the_ask(temp_db, applied='["people", "tags", "site", "creator", "title"]')
    await name_person(temp_db, source="stash_box", at=ENRICHED_AT)

    box = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "enriched")

    assert box.what == (
        "StashDB recognized this file, a certain match, and wrote 1 person and the title automatically"
    )


async def test_a_box_that_wrote_nothing_this_read_can_see_keeps_its_old_sentence(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The title, the details and the dates are written with nothing recording who wrote them.

    So they are not claimed. A match with no attributable row behind it says only what is known,
    which is the sentence the line has always carried.
    """
    await make_file(temp_db)
    await enrich(temp_db)

    box = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "enriched")

    assert (
        box.what
        == "StashDB recognized this file, a certain match, before Sift recorded what a match writes"
    )
    assert box.detail == ()


async def test_two_applied_boxes_leave_the_rows_where_they_are(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Nothing says WHICH of two boxes wrote a row, so nothing is absorbed and no box is named.

    The count is still true and the attribution stops short: "A stash-box" is what the row can say
    on a file two boxes have both been applied to.
    """
    await make_file(temp_db)
    await enrich(temp_db)
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, ?)",
        (OTHER_BOX, "FansDB", "https://example.invalid/other", ADDED_AT),
    )
    await temp_db.execute(
        "INSERT INTO asset_stash_box_matches"
        " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
        " VALUES (?, ?, 'remote', '{}', 'certain', 'applied', ?, ?)",
        (ASSET, OTHER_BOX, ENRICHED_AT, ENRICHED_AT),
    )
    await name_person(temp_db, source="stash_box", at=ENRICHED_AT)
    await name_another(temp_db, source="stash_box", at=ENRICHED_AT)

    events = list(await history_of_asset(temp_db, access, actors.admin, ASSET))

    assert said_of(events, "named").what == "A stash-box named 2 people here"
    assert [one.what for one in events if one.kind == "enriched"] == [
        "StashDB recognized this file, a certain match, before Sift recorded what a match writes",
        "FansDB recognized this file, a certain match, before Sift recorded what a match writes",
    ]


async def test_two_applied_boxes_each_take_the_rows_that_name_them(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A row names the box that filed it (`box_id`), so on a file two boxes were applied to each
    box's line says what IT wrote, and neither is "A stash-box"."""
    await make_file(temp_db)
    await enrich(temp_db)
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, ?)",
        (OTHER_BOX, "FansDB", "https://example.invalid/other", ADDED_AT),
    )
    await temp_db.execute(
        "INSERT INTO asset_stash_box_matches"
        " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
        " VALUES (?, ?, 'remote', '{}', 'certain', 'applied', ?, ?)",
        (ASSET, OTHER_BOX, ENRICHED_AT, ENRICHED_AT),
    )
    await name_person(temp_db, source="stash_box", at=ENRICHED_AT)
    await name_another(temp_db, source="stash_box", at=ENRICHED_AT)
    await temp_db.execute("UPDATE asset_people SET box_id = ? WHERE person_id = ?", (BOX, PERSON))
    await temp_db.execute(
        "UPDATE asset_people SET box_id = ? WHERE person_id = ?", (OTHER_BOX, OTHER_PERSON)
    )

    events = list(await history_of_asset(temp_db, access, actors.admin, ASSET))

    assert [one.kind for one in events if one.kind == "named"] == []
    assert sorted(one.what for one in events if one.kind == "enriched") == [
        "FansDB recognized this file, a certain match, and wrote 1 person",
        "StashDB recognized this file, a certain match, and wrote 1 person",
    ]


async def test_one_passs_tagging_press_is_one_line_too(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The same fold on the same word, because a press of tags is the same kind of act."""
    await make_file(temp_db)
    await tag_it(temp_db, source="watermark", at=TAGGED_AT)
    await tag_again(temp_db, source="watermark", at=TAGGED_AT)

    tagged = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "tagged")

    assert tagged.what == "Sift added 2 tags here from a watermark"
    assert [(one.kind, one.words) for one in tagged.detail] == [("tag", "2 tags")]
    assert [link.name for link in tagged.detail[0].links] == ["poolside", "split screen"]


async def test_a_single_tagging_says_its_pass_without_counting(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """One tag is named, not counted: "tagged 1 tag" is a number standing in for a word."""
    await make_file(temp_db)
    await tag_it(temp_db, source="watermark", at=TAGGED_AT)

    tagged = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "tagged")

    assert tagged.what == "Sift added the tag poolside to this file from a watermark"
    assert tagged.links == (Link(kind="tag", id=TAG, name="poolside"),)


# --- A BOX THAT WAS ASKED AND HAD NEVER HEARD OF THE FILE ---------------------------------------
# Every ask is written down (`stash_box_scans`), found or not, and a file's History says so.


async def test_a_box_asked_with_no_match_says_so_with_the_time(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A file three services have never heard of must not read like one nobody asked about."""
    await make_file(temp_db)
    await ask_a_box(temp_db, found=False)

    events = list(await history_of_asset(temp_db, access, actors.admin, ASSET))

    assert [one.kind for one in events] == ["added", "asked"]
    asked = said_of(events, "asked")
    assert asked.what == "Sift asked StashDB and nothing matched"
    assert asked.at == ENRICHED_AT
    # Sift did the asking, and the line says so first.
    assert asked.actor_name == "Sift"


async def test_a_box_that_recognised_the_file_is_not_also_said_to_have_missed_it(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The two lines are opposite outcomes of one act, so a file may never carry both for one box.

    The ask is written whatever comes back, so the row is there on every file a sweep has been
    over, which is exactly how one event becomes two sentences that disagree.
    """
    await make_file(temp_db)
    await enrich(temp_db)
    await ask_a_box(temp_db, found=True)

    events = list(await history_of_asset(temp_db, access, actors.admin, ASSET))

    assert [one.kind for one in events] == ["added", "enriched"]


# --- NINE MORE SOURCES --------------------------------------------------------------------------
# Nine tables, each carrying a moment for an act on the file. They are seeded together on one file
# rather than one test each, because what is being asserted is that the pane says all of it in
# order: nine separate tests would each prove their own line and none of them would notice a line
# missing.


async def read_a_watermark(
    database: Database,
    *,
    found: bool,
    kind: str = "site",
    text: str = "studio.example/harlowquin",
    site: str = "Studio",
    username: str | None = "harlowquin",
) -> None:
    """A look for a site's mark on the picture, and what it read. A site's address by default."""
    await database.execute(
        "INSERT INTO watermark_scans (asset_id, revision, identity, found, scanned_at)"
        " VALUES (?, 'r1', 'i1', ?, ?)",
        (ASSET, 1 if found else 0, SCANNED_AT),
    )
    if found:
        await database.execute(
            "INSERT INTO watermark_reads (asset_id, text, kind, site, username, confidence, read_at)"
            " VALUES (?, ?, ?, ?, ?, 0.9, ?)",
            (ASSET, text, kind, site, username, SCANNED_AT),
        )


async def download_it(database: Database) -> None:
    await database.execute(
        "INSERT INTO downloads (id, url, url_hash, state, site, username, asset_id,"
        " created_at, finished_at) VALUES (?, 'https://example.invalid/x', 'h', 'done',"
        " 'Studio', 'harlowquin', ?, ?, ?)",
        (new_id(), ASSET, ADDED_AT, ADDED_AT + 5),
    )


async def build_pictures(database: Database) -> None:
    for kind, when in (("thumb", ADDED_AT + 10), ("preview", ADDED_AT + 20)):
        await database.execute(
            "INSERT INTO derivatives (id, asset_id, kind, rel_cache_path, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (new_id(), ASSET, kind, f"{kind}/x", when),
        )


async def test_the_sources_that_were_never_read_each_say_what_happened(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """One file with one of each, and the sentence every one of them says.

    A file downloaded, read, prepared, indexed, with faces taken off it and a person ruled out of
    it, says all of it.
    """
    await make_file(temp_db)
    await download_it(temp_db)
    await build_pictures(temp_db)
    await read_a_watermark(temp_db, found=True)
    # And the filing the reading made, which is what lets its line name the Site it filed under,
    # and the Site's id the reading keeps when it files (watermarks v6).
    await file_under_site(temp_db, source="watermark")
    await temp_db.execute(
        "UPDATE watermark_reads SET site_id = ? WHERE asset_id = ?", (SITE, ASSET)
    )
    await temp_db.execute(
        "INSERT INTO semantic_indexed (asset_id, revision, frames, indexed_at) VALUES (?, 'r', 1, ?)",
        # MILLISECONDS, as the index writes them: seeded in seconds, a line that forgot to divide
        # would be dated the year 58587 with this test green.
        (ASSET, (ADDED_AT + 30) * 1000),
    )
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)",
        (PERSON, "Neve Alder", ADDED_AT),
    )
    await temp_db.execute(
        "INSERT INTO asset_person_refusals (asset_id, person_id, refused_at) VALUES (?, ?, ?)",
        (ASSET, PERSON, ADDED_AT + 40),
    )
    await temp_db.execute(
        "INSERT INTO face_removals (id, asset_id, embedding, recognizer, quality, created_at)"
        " VALUES (?, ?, X'00', 'r', 0.5, ?)",
        (new_id(), ASSET, (ADDED_AT + 50) * 1000),
    )
    await temp_db.execute(
        "INSERT INTO face_ignored (id, asset_id, pile_id, embedding, centroid, created_at)"
        " VALUES (?, ?, 'pile', X'00', X'00', ?)",
        (new_id(), ASSET, (ADDED_AT + 60) * 1000),
    )
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, ?)",
        (BOX, "StashDB", "https://example.invalid/graphql", ADDED_AT),
    )
    await temp_db.execute(
        "INSERT INTO stash_box_kept (subject, local_id, box_id, key, mine, theirs, decided_at)"
        " VALUES ('asset', ?, ?, 'title', 'mine', 'theirs', ?)",
        (ASSET, BOX, ADDED_AT + 70),
    )

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)
    said = {one.kind: one.what for one in events}

    assert said["downloaded"] == "Sift downloaded this file from harlowquin on Studio"
    # The download brought the file in, so it is the arrival's line as well.
    assert "added" not in said
    # One sitting of routine lines is one line, dated at its first step, opening to each.
    assert said["ready"] == "Sift processed this file"
    assert {one.kind: one.at for one in events}["ready"] == ADDED_AT + 10
    processed = next(one for one in events if one.kind == "ready")
    steps = [link.name for group in processed.detail for link in group.links]
    assert "Sift indexed this file for Smart Search" in steps
    assert said["watermark"] == "Sift found the watermark harlowquin on Studio"
    assert said["ruled_out"] == "Neve Alder was marked as not in this file"
    assert said["face_off"] == "A face here was discarded"
    assert said["kept_mine"] == "Your title, mine, was kept over StashDB's theirs"


async def test_a_site_a_download_or_a_watermark_names_is_the_way_to_that_site(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Every entity a file's History names is a link to its page, a Site included.

    The download and watermark lines link the Site by the ID their rows keep, so a rename keeps the
    link; a row with no Site stays words, and a Site merely sharing the word is not the row's."""
    await make_file(temp_db)
    await download_it(temp_db)
    await read_a_watermark(temp_db, found=True)
    # Filed by the reading, under a username whose Site row is gone, so nothing links yet, and the
    # watermark line still names it.
    await file_under_site(temp_db, site=None, source="watermark")
    await temp_db.execute(
        "INSERT INTO sites (id, name, created_at) VALUES ('01HX00000000000000000STUDIO', 'Studio', ?)",
        (ADDED_AT,),
    )
    before = {
        one.kind: one.links for one in await history_of_asset(temp_db, access, actors.admin, ASSET)
    }
    # What the download and the reading write when they file: the Site's id.
    for statement in (
        "UPDATE downloads SET site_id = '01HX00000000000000000STUDIO' WHERE asset_id = ?",
        "UPDATE watermark_reads SET site_id = '01HX00000000000000000STUDIO' WHERE asset_id = ?",
    ):
        await temp_db.execute(statement, (ASSET,))
    await temp_db.execute(
        "UPDATE sites SET name = 'Studio North' WHERE id = '01HX00000000000000000STUDIO'"
    )

    events = await history_of_asset(temp_db, access, actors.admin, ASSET)
    links = {one.kind: one.links for one in events}

    assert before["downloaded"] == () and before["watermark"] == ()
    for kind in ("downloaded", "watermark"):
        assert [(one.kind, one.id, one.name) for one in links[kind]] == [
            ("site", "01HX00000000000000000STUDIO", "Studio North")
        ]


async def test_the_pictures_sift_built_are_one_line_and_not_one_each(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Three lines of housekeeping in a thread of things people did is the wrong shape. See
    `sentences.made_ready`. The moment is the FIRST of the sitting, where the work began."""
    await make_file(temp_db)
    await build_pictures(temp_db)

    ready = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "ready")

    assert ready.what == "Sift generated a thumbnail and a hover preview for this file"
    assert ready.at == ADDED_AT + 10


async def test_a_look_for_a_watermark_that_found_nothing_says_so(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A file nothing was read off must not look exactly like a file nobody ever looked at."""
    await make_file(temp_db)
    await read_a_watermark(temp_db, found=False)

    events = list(await history_of_asset(temp_db, access, actors.admin, ASSET))

    assert said_of(events, "watermark").what == "Sift looked for a watermark and found none"


@pytest.mark.parametrize(
    ("kind", "text", "site", "username", "filed_by", "said", "links"),
    [
        (
            "site",
            "studio.example/harlowquin",
            "Studio",
            "harlowquin",
            "watermark",
            "Sift found the watermark harlowquin on Studio",
            True,
        ),
        (
            "site",
            "studio.examp1e/harlowquin",
            "Studio",
            "harlowquin",
            None,
            "Sift found a watermark, studio.examp1e/harlowquin, and filed nothing",
            False,
        ),
        (
            "notice",
            "STUDIO PROTECTED",
            "Studio",
            None,
            "watermark",
            "Sift found a distributor's watermark, which marks Studio material",
            True,
        ),
        (
            "channel",
            "t.me/poolside",
            "",
            "poolside",
            None,
            "Sift found a Telegram channel's watermark, t.me/poolside, which names no Site",
            False,
        ),
        (
            "username",
            "@harlowquin",
            "",
            "harlowquin",
            "watermark",
            "Sift found the username @harlowquin in a watermark",
            False,
        ),
        (
            "username",
            "@harlowquin",
            "",
            "harlowquin",
            None,
            "Sift found the username @harlowquin in a watermark and filed nothing",
            False,
        ),
    ],
    ids=[
        "an address that filed",
        "an address that filed nothing",
        "a distributor's band",
        "a telegram channel",
        "a username one site has",
        "a username no single site has",
    ],
)
async def test_each_kind_of_reading_says_what_it_was_and_what_came_of_it(
    temp_db: Database,
    access: Repository,
    actors: Actors,
    kind: str,
    text: str,
    site: str,
    username: str | None,
    filed_by: str | None,
    said: str,
    links: bool,
) -> None:
    """Every kind of watermark reading, told apart by this line.

    A reading that filed nothing says so in its letters. The filing in each row is made by HAND,
    because "filed nothing" means nothing filed BY THIS PASS."""
    await make_file(temp_db)
    await read_a_watermark(temp_db, found=True, kind=kind, text=text, site=site, username=username)
    await file_under_site(temp_db, site="Studio", source=filed_by)
    # The Site the reading names, by id, as the reading keeps it once it is written.
    await temp_db.execute(
        "UPDATE watermark_reads SET site_id = (SELECT s.id FROM sites s WHERE s.name = site)"
        " WHERE asset_id = ?",
        (ASSET,),
    )

    line = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "watermark")

    assert line.what == said
    assert bool(line.links) is links


async def test_a_watermark_that_was_read_is_not_also_said_to_have_found_nothing(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The scan row is written whatever comes back, so the looking and its answer are one act."""
    await make_file(temp_db)
    await read_a_watermark(temp_db, found=True)

    events = list(await history_of_asset(temp_db, access, actors.admin, ASSET))

    assert [one.kind for one in events] == ["added", "watermark"]


async def test_a_file_hidden_by_somebody_else_is_not_in_this_user_s_history(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """What another user has concealed is that user's business, and this pane is drawn to
    anybody who may see the file."""
    await make_file(temp_db)
    await temp_db.execute(
        "INSERT INTO asset_user_state (asset_id, user_id, hidden, hidden_at, updated_at)"
        " VALUES (?, ?, 1, ?, ?)",
        (ASSET, actors.guest.id, ADDED_AT + 10, ADDED_AT + 10),
    )

    events = list(await history_of_asset(temp_db, access, actors.admin, ASSET))

    assert [one.kind for one in events] == ["added"]


async def test_hiding_a_file_says_so_and_showing_it_again_says_nothing(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """`hidden_at` is written when a thing is concealed and is NOT cleared when it is shown again,
    so a line drawn from a stale stamp would put a date on an act that is over. The showing again
    has no moment anywhere in this database; the event ledger is where it will come from."""
    await make_file(temp_db)
    await temp_db.execute(
        "INSERT INTO asset_user_state (asset_id, user_id, hidden, hidden_at, updated_at)"
        " VALUES (?, ?, 1, ?, ?)",
        (ASSET, actors.admin.id, ADDED_AT + 10, ADDED_AT + 10),
    )

    hidden = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "hidden")
    assert hidden.what == "You hid this file"
    assert hidden.at == ADDED_AT + 10

    await temp_db.execute(
        "UPDATE asset_user_state SET hidden = 0 WHERE asset_id = ? AND user_id = ?",
        (ASSET, actors.admin.id),
    )

    events = list(await history_of_asset(temp_db, access, actors.admin, ASSET))
    assert [one.kind for one in events] == ["added"]


async def test_a_box_asked_five_times_is_one_line_that_says_five(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Every ask is kept (version 12 of the stash-box component), so a file a sweep has put to
    one box five times has five rows, and five identical lines is the repetition this pane's folds
    exist to end. What somebody wants is that it was asked, how lately, and how doggedly."""
    await make_file(temp_db)
    for step in range(5):
        await ask_a_box(temp_db, found=False, at=ENRICHED_AT + step)

    asked = said_of(list(await history_of_asset(temp_db, access, actors.admin, ASSET)), "asked")

    assert asked.what == "Sift asked StashDB 5 times and nothing matched"
    assert asked.at == ENRICHED_AT + 4


async def test_a_writer_calls_a_file_what_history_calls_it(
    temp_db: Database, access: Repository
) -> None:
    """`files_called`: the title somebody typed, its name in the folder now, then the name it
    arrived under: the file page's order (`Repository.names_on_disk`), in the one statement History
    reads, handed to a writer composing a line (a shared song's name names the file it came from).
    A file called nothing, and a file that is gone, are absent."""
    await make_file(temp_db, ASSET)
    await make_file(temp_db, SOURCE)
    await temp_db.execute(
        "UPDATE assets SET original_filename = 'arrived.mp4' WHERE id = ?", (SOURCE,)
    )
    await temp_db.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
        " VALUES (?, 'digest-nameless', 1, 'video', ?)",
        (TRACK, ADDED_AT),
    )
    on_disk = await files_called(temp_db, [ASSET, SOURCE, TRACK, "gone"])
    # Renamed on the disk since it arrived: the name in the folder, never the imported one.
    assert on_disk[SOURCE] != "arrived.mp4"
    assert set(on_disk) == {ASSET, SOURCE}
    # With no copy present, the name it arrived under is the only name anybody has.
    await temp_db.execute(
        "UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?", (SOURCE,)
    )
    assert (await files_called(temp_db, [SOURCE]))[SOURCE] == "arrived.mp4"
    await temp_db.execute("UPDATE assets SET title = 'poolside cut' WHERE id = ?", (SOURCE,))
    assert await files_called(temp_db, [SOURCE]) == {SOURCE: "poolside cut"}
    assert await files_called(temp_db, []) == {}


async def test_a_naming_from_a_folder_names_the_folder_it_was_read_from(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Not "Sift named Neve Alder in this file from a folder name": the folder, by its own name and
    linked to its files. The NEAREST folder answered as her
    above the file, because that is the one whose name was read. A file whose folder has gone
    (no row) keeps the words it had, which the test above holds."""
    await make_file(temp_db)
    await temp_db.execute(
        "UPDATE asset_locations SET rel_path = 'Shoots/Neve/clip.mp4' WHERE asset_id = ?", (ASSET,)
    )
    await name_person(temp_db, source="folder", at=NAMED_AT)
    for folder_id, path in (("f-shoots", "Shoots"), ("f-neve", "Shoots/Neve")):
        await temp_db.execute(
            "INSERT INTO folders (id, root_id, rel_path, name) VALUES (?, ?, ?, ?)",
            (folder_id, LIBRARY, path, path.rsplit("/", 1)[-1]),
        )
        await temp_db.execute(
            "INSERT INTO folder_people (folder_id, person_id, created_at) VALUES (?, ?, ?)",
            (folder_id, PERSON, ADDED_AT),
        )

    named = [
        event
        for event in await history_of_asset(temp_db, access, actors.admin, ASSET)
        if event.kind == "named"
    ]

    assert [event.what for event in named] == [
        "Sift named Neve Alder in this file from the folder Neve"
    ]
    folder = [piece for piece in named[0].pieces if piece.kind == "folder"]
    assert [(piece.id, piece.text, piece.href) for piece in folder] == [
        ("f-neve", "Neve", "/browse?in=f-neve")
    ]
