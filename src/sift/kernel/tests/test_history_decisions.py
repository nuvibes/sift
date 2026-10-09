# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file's History: the decisions that named it, the folds, what a line names, and the count."""

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
from sift.kernel.access import Effect, ObjectType, Repository
from sift.kernel.access.catalog import _DECISIONS_FOR_FILES
from sift.kernel.access.history import (
    _DECIDED,
    KINDS,
    VIAS,
    Actor,
    Link,
    history_of_asset,
)
from sift.kernel.access.history_folds import _receipt_of_object
from sift.kernel.db import Database
from sift.kernel.tests.history_helpers import (
    ADDED_AT,
    ASSET,
    DECIDED_AT,
    DECISION,
    FILED_LINE,
    FILENAME_TITLE,
    NAMED_AT,
    PERSON,
    REVERSED_AT,
    ROOT,
    SITE,
    TAG,
    TAGGED_AT,
    UNDONE_AT,
    USERNAME,
    decide,
    enrich,
    file_under_site,
    make_file,
    move_file,
    move_it,
    name_person,
    scan_faces,
    tag_it,
)
from sift.testing.fixtures import Actors

pytestmark = pytest.mark.anyio


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
