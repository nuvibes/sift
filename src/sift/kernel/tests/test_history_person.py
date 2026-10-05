# SPDX-License-Identifier: AGPL-3.0-or-later
"""What happened to one person, in order.

The read gathers four tables that were never meant to be read together (the catalog's
`asset_people` from the far end, the two face decision tables, and a stash-box link), and unlike a
file's history it COUNTS rather than lists. So the things that can go wrong here are a group landing
under the wrong actor, a count that counts the wrong rows, and a time in the wrong unit.

The unit is the trap worth naming twice: the face tables keep MILLISECONDS, and a history that
forgot to divide would put every face event fifty thousand years into the future and sort it last.
A test that only checked the ORDER of two events would not notice, so the moment itself is asserted.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

# Imported for their side effect: registering the tables a feature owns, so a kernel database has
# them. Every install creates them whether the feature is switched on or not, and the guarded case
# is proved by dropping them.
import sift.slices.faces.schema
import sift.slices.stash_boxes.schema
import sift.slices.suggestions.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository, Viewer
from sift.kernel.access.history import DEFAULT_LIMIT, MAX_LIMIT, Actor, Event
from sift.kernel.access.history_person import history_of_person
from sift.kernel.access.viewer import Effect, ObjectType
from sift.kernel.db import Database
from sift.testing.fixtures import Actors, hide

pytestmark = pytest.mark.anyio

#: A fixed clock, so every assertion is about order rather than about when the test ran.
CREATED_AT = 1_700_000_000
NAMED_AT = CREATED_AT + 100
CONFIRMED_AT = CREATED_AT + 200
REJECTED_AT = CREATED_AT + 300
LINKED_AT = CREATED_AT + 400

PERSON = "01HX0000000000000000000601"
OTHER = "01HX0000000000000000000602"
BOX = "01HX0000000000000000000603"
TRACK = "01HX0000000000000000000604"

A_DAY = 86400

#: The root every file in this module sits loose in. See `make_file`.
LIBRARY_ROOT = "01HX0000000000000000000690"


async def make_person(
    database: Database, person_id: str = PERSON, name: str = "Neve Alder"
) -> None:
    await database.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)",
        (person_id, name, CREATED_AT),
    )


async def make_file(database: Database, asset_id: str, added_at: int = CREATED_AT) -> None:
    await database.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
        " VALUES (?, ?, 1, 'video', ?)",
        (asset_id, f"digest-{asset_id}", added_at),
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


async def name_on(
    database: Database, asset_id: str, *, source: str | None, at: int | None, person: str = PERSON
) -> None:
    await make_file(database, asset_id)
    await database.execute(
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (asset_id, person, source, at),
    )


async def confirm_face(database: Database, asset_id: str, *, at: int) -> None:
    """A face agreed to be them. `created_at` is MILLISECONDS, as the face tables all are."""
    await make_file(database, asset_id)
    await database.execute(
        "INSERT INTO face_confirmations (id, asset_id, person_id, embedding, created_at)"
        " VALUES (?, ?, ?, X'00', ?)",
        (f"c-{asset_id}", asset_id, PERSON, at * 1000),
    )


async def reject_face(database: Database, track_id: str, *, at: int) -> None:
    await make_file(database, f"asset-of-{track_id}")
    await database.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, created_at)"
        " VALUES (?, ?, 0, 1, 1, 1.0, ?)",
        (track_id, f"asset-of-{track_id}", at * 1000),
    )
    await database.execute(
        "INSERT INTO face_rejections (track_id, person_id, created_at) VALUES (?, ?, ?)",
        (track_id, PERSON, at * 1000),
    )


async def link_box(database: Database, *, at: int = LINKED_AT, name: str = "StashDB") -> None:
    await database.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, ?)",
        (BOX, name, "https://example.invalid/graphql", CREATED_AT),
    )
    await database.execute(
        "INSERT INTO person_stash_box_links (person_id, box_id, remote_id, payload, fetched_at)"
        " VALUES (?, ?, 'remote', '{}', ?)",
        (PERSON, BOX, at),
    )


async def record_the_ask(
    database: Database,
    *,
    applied: str | None,
    at: int = LINKED_AT,
    run: str = "run-1",
    automatic: int = 1,
) -> None:
    """One row in `enrichment_runs`, which is where what an ask FILLED IN is written down.

    Written as SQL rather than through the stash-box service, for the reason every other helper in
    this file is: these tests are about what the READ says, and reaching a slice's service to seed
    one row would make a kernel test fail when that slice moves.
    """
    await database.execute(
        "INSERT INTO enrichment_runs (id, subject, local_id, box_id, at, automatic, applied)"
        " VALUES (?, 'person', ?, ?, ?, ?, ?)",
        (run, PERSON, BOX, at, automatic, applied),
    )


async def only_admin(access: Repository, actors: Actors) -> Viewer:
    loaded = await access.load_viewer(actors.admin.id)
    assert loaded is not None
    return loaded


async def _linked_line(database: Database, access: Repository, actors: Actors) -> str:
    """The one `enriched` line on this person's thread."""
    events = await history_of_person(database, await only_admin(access, actors), PERSON)
    said = [one.what for one in events if one.kind == "enriched"]
    assert len(said) == 1
    return said[0]


async def test_a_box_that_filled_two_fields_in_names_them(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """What an ask filled in, said from the run's own list (version 52 of the catalog).

    The fields are named in the RECORD'S order and not the stored list's, which is why the two are
    written the other way round here: a line that said "height and birthdate" on one person and
    "birthdate and height" on the next would read as two different facts.
    """
    await make_person(temp_db)
    await link_box(temp_db)
    await record_the_ask(temp_db, applied='["height", "birth_date"]')

    assert await _linked_line(temp_db, access, actors) == (
        "StashDB filled in their birthdate and height automatically"
    )


async def test_a_box_that_filled_nothing_in_says_so(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The empty list is a real outcome and not a missing one.

    A box that agrees with everything already here writes nothing, and the link is still the point.
    Silence would read as a button that did not work.
    """
    await make_person(temp_db)
    await link_box(temp_db)
    await record_the_ask(temp_db, applied="[]")

    assert await _linked_line(temp_db, access, actors) == (
        "Sift linked them to StashDB, which had nothing new to fill in"
    )


async def test_an_ask_written_before_the_column_existed_says_only_that_it_happened(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """NULL is neither of the two above, and the line stops where the record stops.

    This is every link made before the column existed. Reading it as "nothing was filled in" would
    be a newer build putting words in an older row's mouth, on the one screen whose whole promise is
    that it says what really happened.
    """
    await make_person(temp_db)
    await link_box(temp_db)
    await record_the_ask(temp_db, applied=None)

    assert await _linked_line(temp_db, access, actors) == "Sift linked them to StashDB"


async def test_only_the_last_ask_is_read(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Every ask is kept (version 51), and the line says what the NEWEST of them filled in.

    Without this the read would either draw one line per ask (the repetition the pane's folds
    exist to end) or take whichever row the planner happened to reach first.
    """
    await make_person(temp_db)
    await link_box(temp_db)
    await record_the_ask(temp_db, applied="[]", at=LINKED_AT - 100, run="run-old")
    await record_the_ask(temp_db, applied='["height"]', at=LINKED_AT, run="run-new")

    assert await _linked_line(temp_db, access, actors) == (
        "StashDB filled in their height automatically"
    )


async def test_a_box_that_filled_in_three_links_says_how_many(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Not "FansDB filled in their links", which is the same sentence for one address and for
    nine. The writers answer with a count per field and the column stores it, so the
    line says how many. An older row holds a plain list and keeps the bare label:
    it did not know, and a newer build must not put a number in its mouth."""
    await make_person(temp_db)
    await link_box(temp_db)
    await record_the_ask(temp_db, applied='{"birth_date": 1, "links": 3}')

    assert await _linked_line(temp_db, access, actors) == (
        "StashDB filled in their 3 links and birthdate automatically"
    )


async def test_a_person_s_link_says_whether_somebody_pressed_it(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A person's line says "by hand" off the same column a site's thread and a tag's read, about
    the identical act. Three threads, one sentence."""
    await make_person(temp_db)
    await link_box(temp_db)
    await record_the_ask(temp_db, applied='["height"]', automatic=0)

    assert await _linked_line(temp_db, access, actors) == (
        "StashDB filled in their height when you applied its answer"
    )


async def test_somebody_with_one_of_everything_comes_back_oldest_first(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    await make_person(temp_db)
    await name_on(temp_db, "01HX0000000000000000000610", source=None, at=NAMED_AT)
    await confirm_face(temp_db, "01HX0000000000000000000611", at=CONFIRMED_AT)
    await reject_face(temp_db, TRACK, at=REJECTED_AT)
    await link_box(temp_db)

    events = await history_of_person(temp_db, actors.admin, PERSON)

    assert [event.kind for event in events] == [
        "added",
        "named",
        "confirmed",
        "rejected",
        "enriched",
    ]
    assert [event.at for event in events] == [
        CREATED_AT,
        NAMED_AT,
        CONFIRMED_AT,
        REJECTED_AT,
        LINKED_AT,
    ]
    assert events[0].what == "Neve Alder was added to the library"
    # No run recorded what the link filled in, and the line says exactly that rather than a guess.
    assert events[-1].what == ("StashDB was linked before Sift recorded what a stash-box fills in")


async def test_files_named_on_one_day_are_one_event_that_counts_them(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A person is on thousands of files. One line each is not a history anybody can read."""
    await make_person(temp_db)
    for index in range(3):
        await name_on(
            temp_db, f"01HX000000000000000000062{index}", source=None, at=NAMED_AT + index
        )

    named = [
        event
        for event in await history_of_person(temp_db, actors.admin, PERSON)
        if event.kind == "named"
    ]

    assert len(named) == 1
    assert named[0].what == "They were named on 3 files"
    # The newest moment in the group, so the event sits where the last of those decisions was made.
    assert named[0].at == NAMED_AT + 2


async def test_a_different_day_is_a_different_event(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    await make_person(temp_db)
    await name_on(temp_db, "01HX0000000000000000000630", source=None, at=NAMED_AT)
    await name_on(temp_db, "01HX0000000000000000000631", source=None, at=NAMED_AT + A_DAY)

    named = [
        event
        for event in await history_of_person(temp_db, actors.admin, PERSON)
        if event.kind == "named"
    ]

    assert [event.what for event in named] == [
        "They were named on 1 file",
        "They were named on 1 file",
    ]


@pytest.mark.parametrize(
    ("source", "actor", "sentence"),
    [
        (None, Actor.SOMEBODY, "They were named on 1 file"),
        # No folder has been answered as them, so the line says only what it can support. See
        # `sentences.named_on`, which has an arm for each of none, one and several.
        ("folder", Actor.SIFT, "Sift named them on 1 file from a folder name"),
        ("username", Actor.SIFT, "Sift named them on 1 file from their username"),
        ("stash_box", Actor.STASH_BOX, "A stash-box named them on 1 file"),
        ("something_later", Actor.SIFT, "Sift named them on 1 file"),
    ],
)
async def test_each_source_says_who_did_it_in_the_apps_own_words(
    temp_db: Database,
    access: Repository,
    actors: Actors,
    source: str | None,
    actor: Actor,
    sentence: str,
) -> None:
    """The four words the column holds, and a fifth this build has never heard of.

    The unknown word still produces an event, attributed to Sift: every non-null source means a
    pass ran, and dropping the row would lose a file somebody can plainly see on screen.
    """
    await make_person(temp_db)
    await name_on(temp_db, "01HX0000000000000000000640", source=source, at=NAMED_AT)

    named = next(
        event
        for event in await history_of_person(temp_db, actors.admin, PERSON)
        if event.kind == "named"
    )

    assert named.actor is actor
    assert named.what == sentence


async def test_a_stash_box_naming_says_which_box_where_the_files_one_match_says_it(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """`asset_people.source` says only 'stash_box'. The file's ONE applied match names the box,
    so those files say "StashDB named them"; a file with two applied boxes cannot say which of them
    wrote the row, and stays "A stash-box" as its own line (the file thread's rule, the same one)."""
    await make_person(temp_db)
    for box_id, name in (("box-1", "StashDB"), ("box-2", "FansDB")):
        await temp_db.execute(
            "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, ?)",
            (box_id, name, f"https://{box_id}.invalid/graphql", CREATED_AT),
        )
    one, both = "01HX0000000000000000000660", "01HX0000000000000000000661"
    await name_on(temp_db, one, source="stash_box", at=NAMED_AT)
    await name_on(temp_db, both, source="stash_box", at=NAMED_AT)
    for asset_id, box_id in ((one, "box-1"), (both, "box-1"), (both, "box-2")):
        await temp_db.execute(
            "INSERT INTO asset_stash_box_matches"
            " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
            " VALUES (?, ?, 'remote', '{}', 'certain', 'applied', ?, ?)",
            (asset_id, box_id, NAMED_AT, NAMED_AT),
        )

    named = [
        (event.what, event.actor_name)
        for event in await history_of_person(temp_db, actors.admin, PERSON)
        if event.kind == "named"
    ]

    assert sorted(named, key=str) == sorted(
        [("StashDB named them on 1 file", "StashDB"), ("A stash-box named them on 1 file", None)],
        key=str,
    )


async def test_two_sources_on_one_day_stay_two_events(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Grouped by what decided it as well as by when, or a folder read and a person's own decision
    would be one sentence that is true of neither."""
    await make_person(temp_db)
    await name_on(temp_db, "01HX0000000000000000000650", source=None, at=NAMED_AT)
    await name_on(temp_db, "01HX0000000000000000000651", source="folder", at=NAMED_AT)

    named = sorted(
        event.what
        for event in await history_of_person(temp_db, actors.admin, PERSON)
        if event.kind == "named"
    )

    assert named == [
        "Sift named them on 1 file from a folder name",
        "They were named on 1 file",
    ]


async def test_a_row_written_before_the_column_existed_reads_as_unrecorded_and_sorts_first(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    await make_person(temp_db)
    await name_on(temp_db, "01HX0000000000000000000660", source=None, at=None)

    events = await history_of_person(temp_db, actors.admin, PERSON)

    assert events[0].kind == "named"
    assert events[0].at is None


async def test_faces_are_counted_in_seconds_and_not_in_milliseconds(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The face tables keep milliseconds. Undivided, every one of these lands in the year 55,000."""
    await make_person(temp_db)
    await confirm_face(temp_db, "01HX0000000000000000000670", at=CONFIRMED_AT)
    await confirm_face(temp_db, "01HX0000000000000000000671", at=CONFIRMED_AT + 1)
    await reject_face(temp_db, TRACK, at=REJECTED_AT)

    events = await history_of_person(temp_db, actors.admin, PERSON)
    agreed = next(event for event in events if event.kind == "confirmed")
    refused = next(event for event in events if event.kind == "rejected")

    assert agreed.at == CONFIRMED_AT + 1
    assert agreed.what == "2 faces were confirmed as them"
    assert refused.at == REJECTED_AT
    assert refused.what == "1 face was marked as not them"


async def test_nothing_belonging_to_somebody_else_is_counted(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    await make_person(temp_db)
    await make_person(temp_db, OTHER, "Harlow Quin")
    await name_on(temp_db, "01HX0000000000000000000680", source=None, at=NAMED_AT, person=OTHER)

    assert [event.kind for event in await history_of_person(temp_db, actors.admin, PERSON)] == [
        "added"
    ]


async def test_somebody_who_is_not_there_has_no_history(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    assert await history_of_person(temp_db, actors.admin, PERSON) == []


async def test_the_cap_keeps_the_newest_and_the_limit_is_clamped(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The newest are kept, so a long history loses its beginning rather than its end."""
    await make_person(temp_db)
    for index in range(4):
        await name_on(
            temp_db,
            f"01HX000000000000000000069{index}",
            source=None,
            at=NAMED_AT + index * A_DAY,
        )

    two = await history_of_person(temp_db, actors.admin, PERSON, limit=2)
    assert [event.at for event in two] == [NAMED_AT + 2 * A_DAY, NAMED_AT + 3 * A_DAY]

    # Below one and above the ceiling are both clamped rather than refused: the route bounds what a
    # caller may ask for, and this is what stops a direct call asking for none or for everything.
    assert len(await history_of_person(temp_db, actors.admin, PERSON, limit=0)) == 1
    assert len(await history_of_person(temp_db, actors.admin, PERSON, limit=MAX_LIMIT + 1)) == 5
    assert len(await history_of_person(temp_db, actors.admin, PERSON, limit=DEFAULT_LIMIT)) == 5


async def test_a_library_without_a_features_tables_still_has_a_history(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A process that never imported a feature has never registered its schema.

    So `face_confirmations` is genuinely absent rather than empty, and a statement naming it is a
    hard error rather than an empty answer.
    """
    await make_person(temp_db)
    await name_on(temp_db, "01HX0000000000000000000700", source=None, at=NAMED_AT)
    for table in (
        "face_confirmations",
        "face_rejections",
        "person_stash_box_links",
        "stash_boxes",
    ):
        # The name comes from the fixed tuple above, not from anything read: this is a test taking
        # tables away to prove the read stands without them.
        await temp_db.execute(
            f"DROP TABLE IF EXISTS {table}"  # nosemgrep: sift-no-string-built-sql
        )

    events = await history_of_person(temp_db, actors.admin, PERSON)

    assert [event.kind for event in events] == ["added", "named"]


async def test_a_guest_is_told_exactly_what_an_admin_is(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Nothing here is about another USER, so there is no half to withhold.

    Asserted rather than assumed, because it is the one place a person's history differs from a
    file's: a file's sharing names other users and is left out for a guest. If any table behind
    this read ever gains a column saying WHO decided, this test is what goes red.
    """
    await make_person(temp_db)
    await name_on(temp_db, "01HX0000000000000000000710", source=None, at=NAMED_AT)
    await link_box(temp_db)
    # Shown the file, so the one thing that may differ (how many of her files they may see)
    # does not. See the test below for the case where it does.
    await access.grant(ObjectType.GLOBAL, None, actors.guest.id, Effect.SHARE)

    guest = await access.load_viewer(actors.guest.id)
    assert guest is not None
    admin = await only_admin(access, actors)

    assert await history_of_person(temp_db, guest, PERSON) == await history_of_person(
        temp_db, admin, PERSON
    )


async def test_a_count_says_only_the_files_the_reader_may_be_shown(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """ "Named on 7,000 files" must not tell a guest how many files a person is on, hidden ones
    included.

    The number is counted as it is read, through the stored verdict: a guest shown one of her two
    files reads one, and so does an admin with the other in the vault and the vault shut: the
    size of what is kept back is the thing a count must not say. Unlocked, an admin reads both.
    """
    shown, kept = "01HX0000000000000000000720", "01HX0000000000000000000721"
    await make_person(temp_db)
    await name_on(temp_db, shown, source=None, at=NAMED_AT)
    await name_on(temp_db, kept, source=None, at=NAMED_AT + 1)
    await confirm_face(temp_db, "01HX0000000000000000000722", at=CONFIRMED_AT)
    await access.grant(ObjectType.ITEM, shown, actors.guest.id, Effect.SHARE)

    def said(events: list[Event], kind: str) -> list[str]:
        return [one.what for one in events if one.kind == kind]

    guest = await history_of_person(temp_db, actors.guest, PERSON)
    assert said(guest, "named") == ["They were named on 1 file"]
    # The face is on a file the guest was never shown, so its line is not theirs at all.
    assert said(guest, "confirmed") == []

    await hide(temp_db, "asset", kept, actors.admin.id)
    shut = await history_of_person(temp_db, actors.admin, PERSON)
    assert said(shut, "named") == ["They were named on 1 file"]
    unlocked = await history_of_person(temp_db, replace(actors.admin, show_hidden=True), PERSON)
    assert said(unlocked, "named") == ["They were named on 2 files"]


async def test_a_receipt_naming_a_file_kept_from_the_reader_is_not_drawn(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A receipt's number is written when it is taken ("matched 344 more faces"), so it cannot be
    counted again for whoever reads it. It is drawn only where every file it names may be shown
    (the ledger's own rule), so no stored number counts a file the reader was kept from."""
    kept = "01HX0000000000000000000723"
    await make_person(temp_db)
    await make_file(temp_db, kept)
    await decide_about(temp_db, by=None)
    await temp_db.execute(
        "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
        " VALUES (?, 'asset', ?)",
        (DECISION, kept),
    )

    def decided(events: list[Event]) -> int:
        return len([one for one in events if one.kind == "decided"])

    assert decided(await history_of_person(temp_db, actors.admin, PERSON)) == 1
    assert decided(await history_of_person(temp_db, actors.guest, PERSON)) == 0
    await hide(temp_db, "asset", kept, actors.admin.id)
    assert decided(await history_of_person(temp_db, actors.admin, PERSON)) == 0


async def test_they_are_named_in_their_own_first_line_and_it_is_a_way_to_them(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The subject of its own history carries a link to itself. This read does not know which screen
    is drawing it (the same event is a row on their page and a row wherever else a person's thread
    is read), and a sentence that names somebody should carry the way to them wherever it is read.
    """
    await make_person(temp_db)

    arrived = (await history_of_person(temp_db, actors.admin, PERSON))[0]

    assert [(one.kind, one.id, one.name) for one in arrived.links] == [
        ("person", PERSON, "Neve Alder")
    ]


async def test_a_counted_naming_links_its_number_to_the_files_it_counted(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """'Named on 3 files' has no name in it to link, but the number IS a run of the sentence and it
    is the run somebody most wants to press: it is the only way to see what was counted. So the
    link's name is the phrase the sentence already carries, which a client
    finds exactly where it sits, and its address is the wall filtered to exactly the naming rows
    the line counted: this person, by this source, on this day (`files_of_filing`).
    """
    await make_person(temp_db)
    await name_on(temp_db, "01HX0000000000000000000680", source=None, at=NAMED_AT)

    named = next(
        one for one in await history_of_person(temp_db, actors.admin, PERSON) if one.kind == "named"
    )

    assert named.what == "They were named on 1 file"
    assert [(one.kind, one.name, one.href) for one in named.links] == [
        ("files", "1 file", "/browse?named=01HX0000000000000000000601~~2023-11-14")
    ]


@pytest.mark.parametrize(
    ("source", "via"),
    [(None, None), ("stash_box", "stash"), ("folder", "folder"), ("username", None)],
)
async def test_which_of_the_three_ways_a_naming_arrived(
    temp_db: Database, access: Repository, actors: Actors, source: str | None, via: str | None
) -> None:
    """The same mapping the file's history uses, from the same helper: one column meaning one thing
    in three tables, so a second copy here would be a second thing to keep in step with the
    `enriched:` filter, and the symptom of it drifting would be a mark, which nothing fails on."""
    await make_person(temp_db)
    await name_on(temp_db, "01HX0000000000000000000681", source=source, at=NAMED_AT)

    named = next(
        one for one in await history_of_person(temp_db, actors.admin, PERSON) if one.kind == "named"
    )

    assert named.via == via


async def test_a_stash_box_link_wears_the_stash_box_mark(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A stash-box has a row and an id and no page, so it is named in words and not linked, and
    `via` is what puts the filter's own stash-box mark on the row."""
    await make_person(temp_db)
    await link_box(temp_db)

    linked = next(
        one
        for one in await history_of_person(temp_db, actors.admin, PERSON)
        if one.kind == "enriched"
    )

    assert (linked.via, linked.links) == ("stash", ())


# --- a bulk judgement that named this person -----------------------------------------------------

#: What a re-match's receipt says, in the words the pass itself writes it in. The range is part of
#: the title rather than worked out where it is drawn; see `_how_sure` in the faces slice.
A_RUN = "Sift matched 12 more faces to Neve Alder, between 78% and 96% sure"

DECISION = "01HX0000000000000000000690"
DECIDED_AT = CREATED_AT + 500


async def decide_about(
    database: Database,
    *,
    by: str | None,
    title: str = A_RUN,
    queue: str = "identified",
    reversed_at: int | None = None,
    about: str = PERSON,
    decision_id: str = DECISION,
) -> None:
    """One bulk judgement, and the link saying it was about this person.

    Both rows, for the reason the file's own helper gives: a decision with no subjects appears in
    nobody's history, and a subject with no decision is a link to nothing.
    """
    await database.execute(
        "INSERT INTO workbench_decisions"
        " (id, queue, user_id, title, detail, payload, decided_at, reversed_at)"
        " VALUES (?, ?, ?, ?, 'It did something.', '{}', ?, ?)",
        (decision_id, queue, by, title, DECIDED_AT, reversed_at),
    )
    await database.execute(
        "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
        " VALUES (?, 'person', ?)",
        (decision_id, about),
    )


async def about_a_file_the_guest_is_shown(
    database: Database, access: Repository, actors: Actors, asset_id: str
) -> None:
    """The decision names a file, and the guest is shown it."""
    await make_file(database, asset_id)
    await access.grant(ObjectType.ITEM, asset_id, actors.guest.id, Effect.SHARE)
    await database.execute(
        "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
        " VALUES (?, 'asset', ?)",
        (DECISION, asset_id),
    )


async def test_a_pass_over_the_whole_library_is_an_admins_line(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A decision naming no file counts files a guest may not see and asks things of an admin."""
    await make_person(temp_db)
    await decide_about(temp_db, by=None, queue="asked-only")

    def decided(events: list[Event]) -> int:
        return len([one for one in events if one.kind == "decided"])

    assert decided(await history_of_person(temp_db, actors.guest, PERSON)) == 0
    assert decided(await history_of_person(temp_db, actors.admin, PERSON)) == 1
    await about_a_file_the_guest_is_shown(temp_db, access, actors, "01HX0000000000000000000724")
    assert decided(await history_of_person(temp_db, actors.guest, PERSON)) == 1
    assert decided(await history_of_person(temp_db, actors.admin, PERSON)) == 1


async def test_a_run_that_attached_faces_to_them_is_a_line_on_their_own_thread(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A pass that attached faces to somebody without being asked writes a receipt saying so, and
    the receipt belongs on the page of the person it was about as well as on every file it
    touched."""
    await make_person(temp_db)
    await decide_about(temp_db, by=None)

    decided = [
        one
        for one in await history_of_person(temp_db, actors.admin, PERSON)
        if one.kind == "decided"
    ]

    assert [(one.at, one.what) for one in decided] == [(DECIDED_AT, A_RUN)]


async def test_the_count_and_the_range_are_the_receipts_own_words(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Passed through unchanged, which is the whole of why the range is written at the moment of the
    match: the faces move afterwards, and a figure worked out here would describe the state they are
    in now rather than the run it claims to describe."""
    await make_person(temp_db)
    await decide_about(temp_db, by=None, title="Sift matched 1 more face to Neve Alder, 94% sure")

    decided = next(
        one
        for one in await history_of_person(temp_db, actors.admin, PERSON)
        if one.kind == "decided"
    )

    assert decided.what == "Sift matched 1 more face to Neve Alder, 94% sure"


async def test_an_admin_is_offered_the_way_to_take_the_whole_run_back(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The receipt IS the way back, and the person's page is where somebody stands when they wonder
    why a name arrived on three hundred files, so the door is offered there and not only on each
    of the files."""
    await make_person(temp_db)
    await decide_about(temp_db, by=None)

    decided = next(
        one
        for one in await history_of_person(temp_db, actors.admin, PERSON)
        if one.kind == "decided"
    )

    assert decided.undo is not None
    assert (decided.undo.kind, decided.undo.id) == ("decision", DECISION)


async def test_a_guest_is_offered_no_way_to_take_it_back(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The workbench's undo route is admin-only, so the button would be refused on press."""
    await make_person(temp_db)
    await decide_about(temp_db, by=None)
    await about_a_file_the_guest_is_shown(temp_db, access, actors, "01HX0000000000000000000725")

    decided = next(
        one
        for one in await history_of_person(temp_db, actors.guest, PERSON)
        if one.kind == "decided"
    )

    assert decided.undo is None


async def test_a_queue_that_can_take_nothing_back_offers_no_button(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Which queues are final is the application's answer and is handed in, because the kernel has
    no application: the same arrangement the file's history takes it under."""
    await make_person(temp_db)
    await decide_about(temp_db, by=None)

    decided = next(
        one
        for one in await history_of_person(
            temp_db, actors.admin, PERSON, final_queues=["identified"]
        )
        if one.kind == "decided"
    )

    assert decided.undo is None


async def test_a_run_that_was_taken_back_keeps_its_place_and_says_so(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A history that quietly lost its reversals would read as though nothing had ever happened."""
    await make_person(temp_db)
    await decide_about(temp_db, by=None, reversed_at=DECIDED_AT + 10)

    kinds = [
        (one.kind, one.reversed, one.undo)
        for one in await history_of_person(temp_db, actors.admin, PERSON)
        if one.kind in ("decided", "undone")
    ]

    assert kinds == [("decided", True, None), ("undone", False, None)]


async def test_the_user_that_pressed_it_is_named_the_way_a_files_history_names_it(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """`workbench_decisions` records WHO, so there is something to withhold from a guest, and the
    withholding is the kernel's one rule about it: your own act is "you", and somebody else's is named to an admin."""
    await make_person(temp_db)
    await decide_about(temp_db, by=actors.admin.id)
    await about_a_file_the_guest_is_shown(temp_db, access, actors, "01HX0000000000000000000726")

    mine = next(
        one
        for one in await history_of_person(temp_db, actors.admin, PERSON)
        if one.kind == "decided"
    )
    theirs = next(
        one
        for one in await history_of_person(temp_db, actors.guest, PERSON)
        if one.kind == "decided"
    )

    assert (mine.actor, mine.actor_name) == (Actor.YOU, None)
    assert (theirs.actor, theirs.actor_name) == (Actor.ANOTHER_USER, None)


async def test_a_decision_about_somebody_else_is_not_on_this_persons_thread(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The statement seeks on the subject, which is the whole reason the link table is indexed the
    way it is, and the wrong end of that join would put every run in the library on every page."""
    await make_person(temp_db)
    await make_person(temp_db, OTHER, name="Rian Fennick")
    await decide_about(temp_db, by=None, about=OTHER)

    assert not [
        one
        for one in await history_of_person(temp_db, actors.admin, PERSON)
        if one.kind == "decided"
    ]


async def test_a_library_with_no_workbench_still_has_a_history(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A process that never imported the workbench has never registered its schema, so a statement
    naming those tables is a hard error rather than an empty answer."""
    await make_person(temp_db)
    await name_on(temp_db, "01HX0000000000000000000691", source=None, at=NAMED_AT)
    for table in ("workbench_decision_subjects", "workbench_decisions"):
        await temp_db.execute(f"DROP TABLE {table}")  # nosemgrep: sift-no-string-built-sql

    events = await history_of_person(temp_db, actors.admin, PERSON)

    assert [one.kind for one in events] == ["added", "named"]


# --- WHICH FOLDER, AND THE WAY TO WHAT A NUMBER COUNTS ------------------------------------------
#
# "Sift read a folder name and named them on 7000 files" names the folder and leads to the files:
# the folder read keeps a standing rule per folder, and the files are a filter somebody can be
# handed.


async def a_folder_answered_as_them(database: Database, path: str, folder_id: str) -> None:
    """The standing rule the folder read keeps: this folder is them."""
    await database.execute(
        "INSERT OR IGNORE INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        ("01HX0000000000000000000690", "library", "/library", CREATED_AT),
    )
    await database.execute(
        "INSERT OR IGNORE INTO folders (id, root_id, rel_path, name) VALUES (?, ?, ?, ?)",
        (folder_id, "01HX0000000000000000000690", path, path.rsplit("/", 1)[-1]),
    )
    await database.execute(
        "INSERT INTO folder_people (folder_id, person_id, created_at) VALUES (?, ?, ?)",
        (folder_id, PERSON, CREATED_AT),
    )


async def test_a_folder_read_says_which_folder_and_links_the_number_to_those_files(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The line names which folder and links to exactly the files it counted, both answered off
    rows that are already being written."""
    await make_person(temp_db)
    await a_folder_answered_as_them(temp_db, "Neve Alder/Videos", "01HX0000000000000000000691")
    # The folder above it, as a library holds one for every level: a folder is said only where
    # the reader may see it all the way down.
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, rel_path, name) VALUES (?, ?, ?, ?)",
        ("01HX0000000000000000000696", "01HX0000000000000000000690", "Neve Alder", "Neve Alder"),
    )
    await name_on(temp_db, "01HX0000000000000000000692", source="folder", at=NAMED_AT)

    named = next(
        one
        for one in await history_of_person(temp_db, actors.admin, PERSON, access=access)
        if one.kind == "named"
    )

    assert named.what == "Sift named them on 1 file from the folder Neve Alder/Videos"
    assert [(one.kind, one.name, one.href) for one in named.links] == [
        ("files", "1 file", "/browse?named=01HX0000000000000000000601~folder~2023-11-14"),
        # By the folder's id: `in:` reads a path as every folder under it.
        ("folder", "Neve Alder/Videos", "/browse?in=01HX0000000000000000000691"),
    ]


async def test_a_folder_the_reader_may_not_see_is_not_named_on_the_line(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A person can be shown to somebody who may see one of their files and not the folder their
    name was read from. The line keeps its words without the folder, as a file's own history does,
    and names it again for a reader who may see it. With nothing to ask, no folder is named."""
    await make_person(temp_db)
    await a_folder_answered_as_them(temp_db, "Sealed/Neve Alder", "01HX0000000000000000000693")
    await temp_db.execute(
        "INSERT OR IGNORE INTO folders (id, root_id, rel_path, name) VALUES (?, ?, ?, ?)",
        ("01HX0000000000000000000694", "01HX0000000000000000000690", "Sealed", "Sealed"),
    )
    await name_on(temp_db, "01HX0000000000000000000695", source="folder", at=NAMED_AT)

    async def line(viewer: Viewer, *, asking: bool = True) -> str:
        events = await history_of_person(temp_db, viewer, PERSON, access=access if asking else None)
        return next(one.what for one in events if one.kind == "named")

    admin = await only_admin(access, actors)
    assert await line(admin) == "Sift named them on 1 file from the folder Sealed/Neve Alder"
    assert await line(admin, asking=False) == "Sift named them on 1 file from a folder name"

    await hide(temp_db, "folder", "01HX0000000000000000000694", actors.admin.id)
    sealed = await only_admin(access, actors)
    said = await line(sealed)
    assert "Sealed" not in said
    assert said.endswith("from a folder name")


async def test_a_library_s_top_folder_answered_as_them_is_said_by_its_name(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A library's top folder has an empty path inside the library, and the line must not read
    "Sift named them on 200 files from the folder " with nothing after it."""
    await make_person(temp_db)
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        ("01HX0000000000000000000697", "Neve Alder", "/Neve Alder", CREATED_AT),
    )
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, rel_path, name) VALUES (?, ?, ?, ?)",
        ("01HX0000000000000000000698", "01HX0000000000000000000697", "", "Neve Alder"),
    )
    await temp_db.execute(
        "INSERT INTO folder_people (folder_id, person_id, created_at) VALUES (?, ?, ?)",
        ("01HX0000000000000000000698", PERSON, CREATED_AT),
    )
    await name_on(temp_db, "01HX0000000000000000000699", source="folder", at=NAMED_AT)
    await access.grant(ObjectType.ITEM, "01HX0000000000000000000699", actors.guest.id, Effect.SHARE)

    async def line(viewer: Viewer) -> str:
        events = await history_of_person(temp_db, viewer, PERSON, access=access)
        return next(one.what for one in events if one.kind == "named")

    assert await line(actors.admin) == "Sift named them on 1 file from the folder Neve Alder"
    assert await line(actors.guest) == "Sift named them on 1 file from a folder name"


async def test_two_folders_are_named_as_folders_because_one_link_cannot_go_to_both(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The phrase in the sentence is ONE run of characters, so a link on it would take somebody to
    one folder and silently not to the other, which is the rule a file's history follows for a
    face waiting in two groups. The number still goes to every file the folder reads named them on."""
    await make_person(temp_db)
    await a_folder_answered_as_them(temp_db, "Neve Alder/Videos", "01HX0000000000000000000693")
    await a_folder_answered_as_them(temp_db, "Neve Alder/Photos", "01HX0000000000000000000694")
    # The folder above both, so each is one the reader may see all the way down: the line names
    # neither because there are two, not because one is out of sight.
    await temp_db.execute(
        "INSERT INTO folders (id, root_id, rel_path, name) VALUES (?, ?, ?, ?)",
        ("01HX0000000000000000000696", "01HX0000000000000000000690", "Neve Alder", "Neve Alder"),
    )
    await name_on(temp_db, "01HX0000000000000000000695", source="folder", at=NAMED_AT)

    named = next(
        one
        for one in await history_of_person(temp_db, actors.admin, PERSON, access=access)
        if one.kind == "named"
    )

    assert named.what == "Sift named them on 1 file from their folders"
    assert [(one.kind, one.href) for one in named.links] == [
        ("files", "/browse?named=01HX0000000000000000000601~folder~2023-11-14")
    ]


async def test_the_faces_agreed_to_be_them_go_to_the_wall_that_holds_them(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """ "Sift matched 344 more faces" links to just those 344. The filtering is the faces
    screen's own `?show=`, so a line here and that screen's tabs cannot come to disagree about which
    wall holds what."""
    await make_person(temp_db)
    await confirm_face(temp_db, "01HX0000000000000000000696", at=CONFIRMED_AT)

    agreed = next(
        one
        for one in await history_of_person(temp_db, actors.admin, PERSON)
        if one.kind == "confirmed"
    )

    assert agreed.what == "1 face was confirmed as them"
    assert [(one.name, one.href) for one in agreed.links] == [
        ("1 face", f"/organize/known-people/{PERSON}?show=confirmed")
    ]


async def test_a_refusal_has_no_wall_to_go_to_and_is_drawn_without_one(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A refusal takes the name off the face, so there is no wall of "faces refused as them": the
    appearances are back where they were, among everybody else's. A link to the agreed wall would
    go somewhere these faces are not."""
    await make_person(temp_db)
    await reject_face(temp_db, TRACK, at=REJECTED_AT)

    refused = next(
        one
        for one in await history_of_person(temp_db, actors.admin, PERSON)
        if one.kind == "rejected"
    )

    assert refused.links == ()


async def test_what_sift_learnt_them_from_and_where_they_were_ruled_out(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """Two more tables that carry a moment worth a line: the faces Sift recognizes
    them by, and the files somebody said they are not in."""
    await make_person(temp_db)
    await make_file(temp_db, "01HX0000000000000000000697")
    await temp_db.execute(
        "INSERT INTO face_references (id, person_id, crop_digest, embedding, quality, origin,"
        " recognizer, created_at) VALUES (?, ?, 'digest', X'00', 0.9, 'added', 'r', ?)",
        # MILLISECONDS, as `faces.store` stamps every face table: seeded in seconds, a thread
        # reading it AS seconds would date the line in the year 58647 with this test green.
        ("01HX0000000000000000000698", PERSON, NAMED_AT * 1000),
    )
    await temp_db.execute(
        "INSERT INTO asset_person_refusals (asset_id, person_id, refused_at) VALUES (?, ?, ?)",
        ("01HX0000000000000000000697", PERSON, NAMED_AT + 10),
    )

    events = await history_of_person(temp_db, actors.admin, PERSON)
    said = {one.kind: one.what for one in events}

    assert said["taught"] == "Sift learned to recognize them from 1 face"
    # The moment itself, in seconds: the order of two lines cannot see a unit fault.
    assert [one.at for one in events if one.kind == "taught"] == [NAMED_AT]
    assert said["ruled_out"] == "They were marked as not in 1 file"


async def test_a_box_that_was_overruled_says_which_field_was_kept(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The other half of the enrichment story: one line says a box was agreed to know them, this
    says which of its answers was turned down."""
    await make_person(temp_db)
    await link_box(temp_db)
    await temp_db.execute(
        "INSERT INTO stash_box_kept (subject, local_id, box_id, key, mine, theirs, decided_at)"
        " VALUES ('person', ?, ?, 'birthday', 'mine', 'theirs', ?)",
        (PERSON, BOX, LINKED_AT + 10),
    )

    kept = next(
        one
        for one in await history_of_person(temp_db, actors.admin, PERSON)
        if one.kind == "kept_mine"
    )

    assert kept.what == "Your birthday, mine, was kept over StashDB's theirs"


async def test_an_area_is_told_whose_page_its_line_is_on(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A run's line about 91 People is not drawn whole on one of their pages: the area wording it
    is told whose page it is (`Recorded.page`), so it can say the part about them; the vantage word
    stays the reader's."""
    from sift.kernel.workbench import DOER, Named, Recorded, Worded, Workbench

    seen: list[tuple[str, str] | None] = []

    class _Area:
        name = "paged"
        reversible = False

        async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[()]:
            return ()

        async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
            return False

        def worded(self, recorded: Recorded) -> Worded:
            seen.append(recorded.page)
            return Worded(said=(DOER, " did one thing for ", Named(kind="person", id=PERSON)))

    bench = Workbench()
    bench.register_reverser(_Area())
    await make_person(temp_db)
    await decide_about(temp_db, by=None, queue="paged")

    events = await history_of_person(temp_db, actors.admin, PERSON, bench=bench)

    assert seen == [("person", PERSON)]
    assert "Sift did one thing for them" in [one.what for one in events]
