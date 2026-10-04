# SPDX-License-Identifier: AGPL-3.0-or-later
"""History lines that could say something false, each held to the true one.

Lines on the History threads that could be not merely thin but WRONG: the wrong actor named, the
guest in the "by" position, "You" for a table that records no user, "here" on a page that is not
the file, a raw field key, a mark and a tooltip saying "Settled at the workbench" about a pause, a
shelf's own History that does not show a file going in, and saved titles still carrying words the
application retired. One test per line, each asserting the true sentence or actor rather than only
the absence of the false one: a line that stopped saying anything would pass a test of absence.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Registering the tables a feature owns, so a kernel database has them. See `test_history.py`.
import sift.slices.download.schema
import sift.slices.faces.schema
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Effect, ObjectType, Repository, sentences
from sift.kernel.access.history import (
    _EVENT_KINDS,
    KINDS,
    Actor,
    Link,
    field_word,
    history_of_asset,
    the_file_named,
)
from sift.kernel.access.history_entity import (
    history_of_collection,
    history_of_photo_set,
    history_of_site,
)
from sift.kernel.access.history_person import history_of_person
from sift.kernel.access.sentences import (
    FROM_PASS,
    STALE_IN_A_TITLE,
    said,
    text_of,
    today_words,
    watermark_refused,
)
from sift.kernel.db import Database
from sift.kernel.ledger import VERBS, Object, Reversal, record_event
from sift.kernel.ledger import Actor as ActorOf
from sift.kernel.vocabulary import Subject, SubjectKind
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio

#: Invented for this file; see `tests/gates/data/names_cast.txt`.
SOMEBODY = "Neve Alder"
PERSON = "01HX0000000000000000000901"
SITE = "01HX0000000000000000000902"
AT = 1_700_000_000

#: The client's table of marks and their words, read as text: the one place a kind's glyph and
#: tooltip are written. See `test_every_kind_has_its_own_mark_and_words`.
_HISTORY_TS = (
    Path(__file__).resolve().parents[4] / "frontend" / "src" / "lib" / "components" / "common"
) / "history.ts"


async def _event(database: Database, **named: object) -> str:
    async with database.write() as connection:
        return await record_event(connection, **named)  # type: ignore[arg-type]


async def _made_by_sift(database: Database, via: str) -> None:
    await database.execute(
        "INSERT INTO people (id, name, created_at, created_by_kind, created_by_via)"
        " VALUES (?, ?, ?, 'sift', ?)",
        (PERSON, SOMEBODY, AT, via),
    )


# --- 1. the wrong actor named ----------------------------------------------------------------------


async def test_a_person_sift_made_from_a_folder_says_which_pass(
    temp_db: Database, actors: Actors
) -> None:
    """A person Sift made from a folder says which pass made them, not a bare "Sift"."""
    await _made_by_sift(temp_db, "folder")

    (arrival,) = [
        one for one in await history_of_person(temp_db, actors.admin, PERSON) if one.kind == "added"
    ]

    assert (arrival.actor, arrival.actor_name) == (Actor.SIFT, "Sift, from a folder name")


async def test_a_site_sift_made_from_a_download_says_which_pass(
    temp_db: Database, actors: Actors
) -> None:
    await temp_db.execute(
        "INSERT INTO sites (id, name, created_at, created_by_kind, created_by_via)"
        " VALUES (?, 'Sunsetter', ?, 'sift', 'download')",
        (SITE, AT),
    )

    (arrival,) = [
        one for one in await history_of_site(temp_db, actors.admin, SITE) if one.kind == "added"
    ]

    assert (arrival.actor, arrival.actor_name) == (Actor.SIFT, "Sift, from a download")


async def test_a_decision_sift_made_names_sift_on_a_person_s_page(
    temp_db: Database, actors: Actors
) -> None:
    """A card reading `user_id` alone would show nobody for every decision Sift made while the feed
    says "Sift" about the same press."""
    await _made_by_sift(temp_db, "folder")
    await _event(
        temp_db,
        actor=ActorOf.sift("faces"),
        verb="linked",
        subject=[Subject(kind="person", id=PERSON, name=SOMEBODY)],
        object=Object(kind="person", id=PERSON, name=SOMEBODY),
        receipt=Reversal(queue="identified", title="Sift matched 3 more faces", detail="."),
    )

    (card,) = [
        one
        for one in await history_of_person(temp_db, actors.admin, PERSON)
        if one.kind == "decided"
    ]

    assert (card.actor, card.actor_name) == (Actor.SIFT, "Sift, from a face")


async def test_a_naming_sift_made_from_a_folder_says_so_beside_the_line(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """A link row Sift decided names Sift and the pass, as every other Sift line does."""
    await temp_db.execute(
        "UPDATE asset_people SET source = 'folder', decided_at = ? WHERE asset_id = ?",
        (AT, world.solo),
    )

    (named,) = [
        one
        for one in await history_of_asset(temp_db, access, actors.admin, world.solo)
        if one.kind == "named"
    ]

    assert (named.actor, named.actor_name) == (Actor.SIFT, "Sift, from a folder name")


# --- 2. the guest in the "by" position -------------------------------------------------------------


async def test_a_share_names_who_made_it_and_never_the_guest(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """`acl_grants` records who a grant is FOR; the ledger records who made it."""
    await access.grant(
        ObjectType.SITE,
        world.site,
        actors.guest.id,
        Effect.SHARE,
        event=None,
    )
    await _event(
        temp_db,
        actor=ActorOf.user(actors.admin.id),
        verb="shared",
        subject=Subject(kind="site", id=world.site),
        object=Object(kind="login", id=actors.guest.id, name="guest"),
    )

    (share,) = [
        one
        for one in await history_of_site(temp_db, actors.admin, world.site)
        if one.kind == "shared"
    ]

    assert (share.actor, share.actor_name) == (Actor.YOU, None)


async def test_a_share_nothing_recorded_is_somebody_s_and_names_no_one(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """A grant older than the record has no maker anywhere: SOMEBODY, and the recipient's name is
    not borrowed to fill the gap."""
    await access.grant(ObjectType.ITEM, world.solo, actors.guest.id, Effect.SHARE)

    (share,) = [
        one
        for one in await history_of_asset(temp_db, access, actors.admin, world.solo)
        if one.kind == "shared"
    ]

    assert (share.actor, share.actor_name) == (Actor.SOMEBODY, None)


# --- 3. "You" for a table that records no user -----------------------------------------------------


def test_a_refused_watermark_does_not_say_you() -> None:
    line = text_of(watermark_refused())

    assert not line.startswith("You")
    assert line == "The filing from its watermark was permanently undone"


# --- 6. "here" on a page that is not the file ------------------------------------------------------


async def test_a_person_s_card_says_the_file_rather_than_here(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """ "Sift named Neve Alder here, 76% sure" on HER page would mean her page; it means the
    file."""
    await _made_by_sift(temp_db, "folder")
    # A judgement that is not a face match: a face match on her page is said by its own sentence
    # (`history.recognized_lines`), and this is the rule for every other title with "here" in it.
    await _event(
        temp_db,
        actor=ActorOf.user(actors.admin.id),
        verb="decided",
        subject=[
            Subject(kind="person", id=PERSON, name=SOMEBODY),
            Subject(kind="asset", id=world.solo),
        ],
        receipt=Reversal(queue="folders", title=f"Named {SOMEBODY} here from a folder", detail="."),
    )

    (card,) = [
        one
        for one in await history_of_person(temp_db, actors.admin, PERSON)
        if one.kind == "decided"
    ]

    assert card.what == f"Named {SOMEBODY} in solo.mp4 from a folder"
    assert ("asset", world.solo) in [(one.kind, one.id) for one in card.links]


async def test_a_person_s_card_about_a_file_that_has_gone_says_so(
    temp_db: Database, actors: Actors
) -> None:
    """An admin is shown a receipt whose file was deleted; its "here" is the file as recorded."""
    await _made_by_sift(temp_db, "folder")
    await _event(
        temp_db,
        actor=ActorOf.user(actors.admin.id),
        verb="decided",
        subject=[
            Subject(kind="person", id=PERSON, name=SOMEBODY),
            Subject(kind="asset", id="01HX0000000000000000000999", name="gone.mp4"),
        ],
        receipt=Reversal(queue="folders", title=f"Named {SOMEBODY} here", detail="."),
    )

    (card,) = [
        one
        for one in await history_of_person(temp_db, actors.admin, PERSON)
        if one.kind == "decided"
    ]

    # The name the record kept, as plain words: there is nowhere left to go.
    assert card.what == f"Named {SOMEBODY} in gone.mp4"
    assert not [one for one in card.links if one.kind == "asset"]


def test_a_title_with_no_here_is_left_as_it_was() -> None:
    file = Link(kind="asset", id="a", name="solo.mp4")

    assert text_of(the_file_named(said("Sift matched 3 more faces"), file)) == (
        "Sift matched 3 more faces"
    )
    # "there" and "where" are not "here".
    assert text_of(the_file_named(said("Kept there"), file)) == "Kept there"


def test_a_card_about_a_file_that_has_gone_says_so_and_links_nothing() -> None:
    line = the_file_named(said("Named here"), "a file that is gone")

    assert text_of(line) == "Named in a file that is gone"
    assert all(one.kind is None for one in line)


async def test_a_card_about_two_files_keeps_its_title(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """ "here" in a title about several files is not one of them, so nothing is guessed."""
    await _made_by_sift(temp_db, "folder")
    await _event(
        temp_db,
        actor=ActorOf.user(actors.admin.id),
        verb="decided",
        subject=[
            Subject(kind="person", id=PERSON, name=SOMEBODY),
            Subject(kind="asset", id=world.solo),
            Subject(kind="asset", id=world.loose),
        ],
        receipt=Reversal(queue="folders", title="Named here twice", detail="."),
    )

    (card,) = [
        one
        for one in await history_of_person(temp_db, actors.admin, PERSON)
        if one.kind == "decided"
    ]

    assert card.what == "Named here twice"


# --- 8. a raw field key ----------------------------------------------------------------------------


def test_a_field_is_said_by_its_own_words_and_never_its_key() -> None:
    assert field_word("person", "breast_type") == "breast type"
    # A key nothing declares, and a subject nothing knows, keep their spelling opened out.
    assert field_word("person", "shoe_size") == "shoe size"
    assert field_word("shelf", "shoe_size") == "shoe size"


async def test_a_person_s_disagreement_names_the_field_in_its_own_words(
    temp_db: Database, actors: Actors
) -> None:
    await _made_by_sift(temp_db, "folder")
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES ('box', 'StashDB', ?, ?)",
        ("https://example.invalid/graphql", AT),
    )
    await temp_db.execute(
        "INSERT INTO stash_box_kept (subject, local_id, box_id, key, mine, theirs, decided_at)"
        " VALUES ('person', ?, 'box', 'breast_type', 'NATURAL', 'FAKE', ?)",
        (PERSON, AT),
    )

    (kept,) = [
        one
        for one in await history_of_person(temp_db, actors.admin, PERSON)
        if one.kind == "kept_mine"
    ]

    # The line's wording is the sentence table's; what is held here is WHICH WORD names the field.
    assert "breast type" in kept.what
    assert "breast_type" not in kept.what


# --- 9. the wrong mark and tooltip -----------------------------------------------------------------


def test_every_verb_the_ledger_knows_has_a_kind_of_its_own() -> None:
    """A verb missing from `_EVENT_KINDS` would fall through to `decided` and wear the Organize
    tray.

    `linked` is the one verb read by what it was made TO (see `history._LINKED_KINDS`).
    """
    assert VERBS - {"linked"} <= set(_EVENT_KINDS)
    assert set(_EVENT_KINDS.values()) <= set(KINDS)


@pytest.mark.parametrize(
    "verb",
    [
        "paused",
        "resumed",
        "cookies_saved",
        "cookies_replaced",
        "cookies_forgotten",
        "canceled",
        "ran",
    ],
)
def test_the_seven_that_wore_the_workbench_mark_have_kinds_of_their_own(verb: str) -> None:
    assert _EVENT_KINDS[verb] == verb
    assert verb in KINDS


def test_every_kind_has_its_own_mark_and_words() -> None:
    """Every kind the server sends has a glyph in the client's table and words in the server's.

    The glyph is read as text, because the table is TypeScript: a kind with no entry falls through
    to "info", a mark saying nothing about a line that says something. The WORDS are the server's
    (`sentences.MEANS`, sent as each line's `means`), so a kind with no words there would say
    "Something happened".
    """
    source = _HISTORY_TS.read_text(encoding="utf-8")
    marks = source[source.index("const MARKS") : source.index("const COPY_MARKS")]
    for kind in KINDS:
        entry = re.compile(rf"^\s*{kind}:", re.MULTILINE)
        assert entry.search(marks), f"no mark for {kind}"
        assert kind in sentences.MEANS, f"no words for {kind}"
    assert "Settled at the workbench" not in " ".join(sentences.MEANS.values())


# --- 10. a shelf's own History shows a file going in ----------------------------------------------


@pytest.mark.parametrize(
    ("kind", "read"),
    [("collection", history_of_collection), ("photo_set", history_of_photo_set)],
)
async def test_a_file_added_is_on_the_shelf_s_own_history(
    temp_db: Database, world: World, actors: Actors, kind: SubjectKind, read: object
) -> None:
    shelf = world.collection if kind == "collection" else world.photo_set
    await _event(
        temp_db,
        actor=ActorOf.user(actors.admin.id),
        verb="linked",
        subject=Subject(kind="asset", id=world.loose, name="loose.mp4"),
        object=Object(kind=kind, id=shelf, name="shelf"),
    )

    events = await read(temp_db, actors.admin, shelf)  # type: ignore[operator]
    # The wording is the sentence table's; what is held here is that the addition is DRAWN.
    added = [one for one in events if "loose.mp4" in one.what and one.kind != "removed"]

    assert len(added) == 1
    assert added[0].actor is Actor.YOU


async def test_a_person_s_page_still_counts_rather_than_lists_its_links(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """The other side of the same rule: a page that COUNTS its links keeps the event off."""
    await _event(
        temp_db,
        actor=ActorOf.user(actors.admin.id),
        verb="linked",
        subject=Subject(kind="asset", id=world.loose, name="loose.mp4"),
        object=Object(kind="person", id=world.person, name="person"),
    )

    events = await history_of_person(temp_db, actors.admin, world.person)

    assert not [one for one in events if "loose.mp4" in one.what]


# --- 12. saved titles carrying retired words -------------------------------------------------------


def test_a_saved_title_s_retired_words_are_said_the_way_they_are_now() -> None:
    assert today_words("Filed under harlowquin from the file's own name") == (
        f"Filed under harlowquin {FROM_PASS['filename']}"
    )
    assert today_words("A group of 90 faces set aside") == "A group of 90 faces discarded"
    assert today_words("Set aside 2 groups") == "Discarded 2 groups"
    assert "own name" not in FROM_PASS["filename"]
    assert all(stale not in now for stale, now in STALE_IN_A_TITLE.items())


async def test_a_card_on_a_file_says_today_s_words(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await _event(
        temp_db,
        actor=ActorOf.sift("faces"),
        verb="decided",
        subject=Subject(kind="asset", id=world.solo),
        receipt=Reversal(queue="faces", title="A group of 2 faces set aside", detail="."),
    )

    lines = [
        one.what
        for one in await history_of_asset(temp_db, access, actors.admin, world.solo)
        if one.kind == "decided"
    ]

    assert lines == ["A group of 2 faces discarded"]
