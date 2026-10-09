# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file's History: faces in groups, the boxes asked, and the sources read beside them."""

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
from sift.kernel.access import Repository
from sift.kernel.access.history import (
    Detail,
    Link,
    files_called,
    history_of_asset,
)
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.tests.history_helpers import (
    ADDED_AT,
    ASSET,
    BOX,
    ENRICHED_AT,
    LIBRARY,
    NAMED_AT,
    OTHER_BOX,
    OTHER_PERSON,
    OTHER_PILE,
    PERSON,
    PILE,
    SITE,
    SOURCE,
    TAG,
    TAGGED_AT,
    TRACK,
    a_face,
    ask_a_box,
    build_pictures,
    download_it,
    enrich,
    file_under_site,
    make_file,
    name_another,
    name_person,
    read_a_watermark,
    record_the_ask,
    said_of,
    scan_faces,
    tag_again,
    tag_it,
)
from sift.testing.fixtures import Actors

pytestmark = pytest.mark.anyio


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
