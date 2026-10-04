# SPDX-License-Identifier: AGPL-3.0-or-later
"""Insights says only what it can prove: the statement rules, held over the PAGE a reader is sent.

## Why a gate over the page, when the builders have a table of their own

`insights/tests/test_statements.py` holds every builder to the rules for the arguments a test hands
it. Every fault that matters here happens BETWEEN a builder and the screen: a reader that ranks a
hidden person before dropping her, a block that says a figure below its floor, a comparison drawn
against a month nobody recorded, a name handed to a builder as words instead of as a thing. A table
of builder calls cannot see a reader, which is why History is held the same way
(`test_history_names_what_it_knows.py`). So a month of `insight_days` rows is written the way the
adder-up writes them, over a small library with a person, a Site, a tag, a Collection, a Photo
Set, three files and a saved wall, and the real route is asked for it by four readers: an admin
with the vault open, the same admin locked in each of the two vault modes, and a guest. Everything
the page says is judged.

## The rules

1. **No statement below its floor**: 10 sittings for anything about viewing, 5 decisions for
   organizing, 10 things for a share. Below it a block says "Not enough yet to say." and carries no
   figure, chart or list a person could read a claim into. Held on both sides of each boundary, so
   a floor moved one way or the other is red.
2. **No decimal**, anywhere a person reads. A length is whole minutes or whole hours.
3. **No adjective without its figure**: a sentence that ranks or compares carries the number it
   rests on. And no judgement at all ("only", "great", "should" ...).
4. **No comparison with a period that was not recorded whole**, and none past a period below its
   own floor. Proved with the comparison that IS said once the month before is whole, so the
   absence is not vacuous.
5. **"Viewed", never "watched"**: on the server here, and on the Insights screens through the
   `insights_screens` scope in `data/vocabulary.json`.
6. **Every named thing is a link**, and no thing is named by a stand-in ("a file") where its
   name is known.
7. **What Sift did is an admin's**: a guest's page carries no machine block and no figure from it.
8. **A hidden person is absent while the vault is locked**: her name, her id and her hours. In the
   default mode ("Show nothing") nothing on the page hints that anything was left out; in
   placeholder mode the one line is said, once. Proved against the unlocked page, where she IS
   named, so the absence is hers and not a page that names nobody.

And the words: every sentence is read through the screen's word checks and the History word table
(`tests/gates/vocabulary.py`), because the page's sentences reach the screen through History's
component.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Iterator
from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

import sift.main  # noqa: F401 (every component registers its tables on import)
from sift.kernel import wiring
from sift.kernel.access import Concealment, Repository, Role, Viewer
from sift.kernel.access.sentences import text_of
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.sorting import sort_key
from sift.kernel.wiring import provide
from sift.kernel.workbench import Workbench
from sift.slices.auth import current_viewer
from sift.slices.insights import router as page
from sift.slices.insights import statements as st
from sift.slices.insights.store import local_today
from sift.slices.settings_hub.service import SettingsService
from sift.testing.fixtures import World, build_world, create_user, hide
from tests.gates import history_ratchets, test_one_word_per_thing, vocabulary

pytestmark = [pytest.mark.gate, pytest.mark.integration]

HOUR = 3_600_000

#: A split stamp no User reaches here, so the page reads each split exactly as written.
SETTLED = 10**12

#: The person the vault hides. She has MORE hours than the visible person, so a reader that ranked
#: before it dropped her would name her.
HIDDEN_NAME = "Neve Alder"

#: The saved wall, named so a statement about Theater has a thing to name.
WALL_NAME = "Nine up"

#: The kinds that have a page (or a panel) to go to, and so must be a link wherever they are named.
PAGED = frozenset(
    {"asset", "person", "tag", "site", "collection", "photo_set", "song", "wall", "recap"}
)

#: Words standing in for a thing instead of naming it (the History rule, `history_ratchets`).
STAND_INS = history_ratchets.UNNAMED

#: A word that ranks or compares, which is a claim, and so carries its figure (rule 3).
_RANKS = re.compile(r"\b(?:mostly|most|more|fewer|busiest|earliest|latest)\b", re.IGNORECASE)

#: A figure: a numeral, or one of the two counts said as a word.
_FIGURE = re.compile(r"\d|\b(?:once|twice)\b", re.IGNORECASE)

#: A judgement, which no statement makes: a figure is stated, never judged.
_JUDGED = re.compile(
    r"\b(?:only|just|great|impressive|too much|should|well done|keep it up|don't lose)\b",
    re.IGNORECASE,
)

#: A decimal, a percentage: neither is said. A share is said as "12 of those times".
_DECIMAL = re.compile(r"\d\.\d|%|\bper ?cent\b", re.IGNORECASE)

#: The words that would say the vault holds something.
_HINTS = re.compile(r"\bhidden\b|\bunlock|\bvault\b|\blocked\b", re.IGNORECASE)


# --- the library and the month ---------------------------------------------------------------------


class Library:
    """The small library, the two readers' ids, and the ids the page must never say while locked."""

    def __init__(self, world: World, admin: Viewer, guest: Viewer, hidden_person: str, wall: str):
        self.world = world
        self.admin = admin
        self.guest = guest
        self.hidden_person = hidden_person
        self.wall = wall

    def names(self) -> dict[str, str]:
        """What each visible thing is called, as the page must say it: as a link."""
        return {
            "person": "person",
            "site": "site",
            "tag": "tag",
            "collection": "collection",
            "photo_set": "photo set",
            "wall": WALL_NAME,
        }


@pytest.fixture
async def library(temp_db: Database, access: Repository) -> Library:
    built = World(**{name: new_id() for name in World.__slots__})
    await build_world(temp_db, built)
    admin = await create_user(temp_db, Role.ADMIN)
    guest = await create_user(temp_db, Role.GUEST)
    hidden_person = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, 0)",
        (hidden_person, HIDDEN_NAME, sort_key(HIDDEN_NAME)),
    )
    await temp_db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (built.twin, hidden_person)
    )
    await hide(temp_db, "person", hidden_person, admin.id)
    wall = new_id()
    await temp_db.execute(
        "INSERT INTO theater_arrangements (id, user_id, name, layout, created_at, updated_at)"
        " VALUES (?, ?, ?, 'grid-3x3', 0, 0)",
        (wall, admin.id, WALL_NAME),
    )
    return Library(built, admin, guest, hidden_person, wall)


@pytest.fixture
def app(temp_db: Database, access: Repository) -> FastAPI:
    app = FastAPI()
    provide(app, wiring.DATABASE, temp_db)
    provide(app, wiring.ACCESS, access)
    # The page reads each reader's clock from the settings hub; the real one over the same
    # database, so the gate sees the words a default install draws.
    provide(app, wiring.SETTINGS_HUB, SettingsService(temp_db))
    provide(app, wiring.WORKBENCH, Workbench())
    app.include_router(page.router, prefix="/api")
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sift.test") as running:
        yield running


def a_closed_day() -> date:
    """A day in the month before this one: a closed period, every day of it added up."""
    return local_today().replace(day=1) - timedelta(days=20)


async def added_up(
    database: Database, user_id: str, day: date, rows: list[tuple[str, str, int, int]]
) -> None:
    """One day's rows for one User, as the adder-up writes them, and its progress past them."""
    async with database.write() as connection:
        for metric, key, whole, hidden in rows:
            await connection.execute(
                "INSERT INTO insight_days (user_id, day, metric, key, whole, hidden, split_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, day.isoformat(), metric, key, whole, hidden, SETTLED),
            )
        await connection.execute(
            "INSERT INTO insight_progress (user_id, added_up_to) VALUES (?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET added_up_to = excluded.added_up_to",
            (user_id, (local_today() - timedelta(days=1)).isoformat()),
        )


def a_month(
    lib: Library, *, sittings: int = 24, decided: int = 6, pickups: int = 12
) -> list[tuple[str, str, int, int]]:
    """Every metric the page reads, once: a month of viewing and organizing, a part of it hidden.

    The hidden person is on `twin`, so `twin`'s own rows are hidden whole; she has more hours than
    the visible person and was opened first more often, so a reader that ranked before dropping her
    would name her in two places.
    """
    world, hidden = lib.world, lib.hidden_person
    theater = sittings // 2
    return [
        ("sittings", "", sittings, 0),
        ("sittings:kind", "video", sittings - theater, 0),
        ("sittings:kind", "theater", theater, 0),
        ("viewed_ms", "", 41 * HOUR, 8 * HOUR),
        ("viewed_ms:kind", "video", 29 * HOUR, 8 * HOUR),
        ("viewed_ms:kind", "image", 4 * HOUR, 0),
        ("viewed_ms:kind", "gif", 3 * HOUR, 0),
        ("viewed_ms:kind", "theater", 5 * HOUR, 0),
        ("viewed_ms:person", world.person, 6 * HOUR, 0),
        ("viewed_ms:person", hidden, 8 * HOUR, 8 * HOUR),
        ("viewed_ms:site", world.site, 3 * HOUR, 0),
        ("viewed_ms:tag", world.tag, 2 * HOUR, 0),
        ("viewed_ms:collection", world.collection, 2 * HOUR, 0),
        ("viewed_ms:photo_set", world.photo_set, HOUR, 0),
        ("sittings:file", world.solo, 4, 0),
        ("sittings:file", world.loose, 3, 0),
        ("sittings:file", world.twin, 5, 5),
        ("viewed_ms:hour", "22", 5 * HOUR, 0),
        ("viewed_ms:weekday", "4", 12 * HOUR, 0),
        ("theater_ms:wall", lib.wall, 4 * HOUR, 0),
        ("pickups", "", pickups, 0),
        ("first_opened:person", world.person, 5, 0),
        ("first_opened:person", hidden, 6, 6),
        ("earliest_start", "", 7 * 60 + 10, 0),
        ("latest_finish", "", 25 * 60, 0),
        ("rated", "", 4, 0),
        ("starred", "", 2, 0),
        ("starred:file", world.solo, 1, 0),
        ("o", "", 5, 3),
        ("o:file", world.loose, 2, 0),
        ("o:file", world.twin, 3, 3),
        ("decided", "", decided, 0),
        ("faces_named", "", 2, 0),
        ("files_filed", "", 3, 0),
        ("files_added", "", 4, 0),
        ("files_added:site", world.site, 2, 0),
        ("files_removed", "", 1, 0),
        ("work_ms:family", "scan", HOUR, 0),
        ("faces_found", "", 10, 0),
        ("fingerprints_made", "", 3, 0),
    ]


# --- reading what the page says --------------------------------------------------------------------


def unlocked(lib: Library) -> Viewer:
    return Viewer(id=lib.admin.id, role=Role.ADMIN, show_hidden=True)


def leave_nothing(lib: Library) -> Viewer:
    return Viewer(id=lib.admin.id, role=Role.ADMIN)


def placeholder(lib: Library) -> Viewer:
    return Viewer(id=lib.admin.id, role=Role.ADMIN, concealment=Concealment.PLACEHOLDER)


async def asked(
    app: FastAPI, client: httpx.AsyncClient, viewer: Viewer, day: date, span: str = "month"
) -> dict[str, Any]:
    app.dependency_overrides[current_viewer] = lambda: viewer
    answer = await client.get("/api/insights", params={"period": span, "at": day.isoformat()})
    assert answer.status_code == 200, answer.text
    body: dict[str, Any] = answer.json()
    return body


def said(line: list[dict[str, Any]]) -> str:
    return "".join(str(piece["lead"]) + str(piece["text"]) for piece in line)


def sentences(body: dict[str, Any]) -> list[str]:
    """Every sentence the page says: the first ones, and every block's."""
    lines = [*body["first_sentences"]]
    for block in body["blocks"]:
        lines.extend(block["statements"])
    return [said(line) for line in lines]


def pieces(body: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Every piece the page carries: in a sentence, behind a fold, and at the head of a list row."""
    stack: list[dict[str, Any]] = []
    for line in body["first_sentences"]:
        stack.extend(line)
    for block in body["blocks"]:
        for line in block["statements"]:
            stack.extend(line)
        for listed in block["lists"]:
            stack.extend(row["piece"] for row in listed["rows"])
    while stack:
        one = stack.pop()
        yield one
        stack.extend(one.get("rest") or [])


def words_on_the_page(body: dict[str, Any]) -> list[str]:
    """Every string a person reads: the sentences, the titles, the figures' and lists' labels and
    every piece, never an id or an address."""
    found = sentences(body)
    for block in body["blocks"]:
        found.append(block["title"])
        found.extend(figure["label"] for figure in block["figures"])
        found.extend(listed["title"] for listed in block["lists"])
        if block["chart"] is not None:
            found.extend(bar["label"] for bar in block["chart"]["bars"])
    found.extend(str(one["text"]) for one in pieces(body))
    return found


def block(body: dict[str, Any], block_id: str) -> dict[str, Any]:
    found: dict[str, Any] = next(one for one in body["blocks"] if one["id"] == block_id)
    return found


def broken_rules(sentence: str) -> list[str]:
    """Every rule one sentence breaks, by the words it says. See the module header."""
    broken = []
    if not sentence.endswith("."):
        broken.append("not a sentence (no full stop)")
    if _DECIMAL.search(sentence):
        broken.append("a decimal or a percentage")
    if _RANKS.search(sentence) and not _FIGURE.search(sentence):
        broken.append("ranks or compares with no figure")
    if _JUDGED.search(sentence):
        broken.append("a judgement")
    if re.search(r"\bwatch", sentence, re.IGNORECASE):
        broken.append("watched, not viewed")
    if " -- " in sentence or "—" in sentence:
        broken.append("a dash joining two ideas")
    for check in (*vocabulary.CHECKS, *vocabulary.HELD_AT_ZERO):
        broken.extend(
            f"{check}: {found!r} -> {instead}"
            for found, instead in vocabulary.offences(check, sentence)
        )
    broken.extend(
        f"History word: {found!r} -> {instead}"
        for found, instead in history_ratchets.history_words_in(sentence)
    )
    return broken


# --- the rules -------------------------------------------------------------------------------------


async def test_every_sentence_on_the_page_keeps_the_rules_for_every_reader(
    app: FastAPI, client: httpx.AsyncClient, temp_db: Database, library: Library
) -> None:
    """Rules 2, 3 and 5 and the words, over everything each reader is told."""
    day = a_closed_day()
    await added_up(temp_db, library.admin.id, day, a_month(library))
    await added_up(temp_db, library.guest.id, day, a_month(library))
    for viewer in (
        unlocked(library),
        leave_nothing(library),
        placeholder(library),
        library.guest,
    ):
        body = await asked(app, client, viewer, day)
        said_here = sentences(body)
        assert len(said_here) >= 8, (viewer, said_here)
        broken = [f"{line!r}: {rule}" for line in said_here for rule in broken_rules(line)]
        assert not broken, f"\n{viewer}:\n  " + "\n  ".join(broken)
        for words in words_on_the_page(body):
            assert not re.search(r"\bwatch", words, re.IGNORECASE), words
            assert not re.search(r"\d\.\d", words), words


async def test_nothing_is_said_below_its_floor_and_everything_is_said_at_it(
    app: FastAPI, client: httpx.AsyncClient, temp_db: Database, library: Library
) -> None:
    """Rule 1, held on both sides of each boundary: 9 sittings and 4 decisions say nothing but the
    one line; 10 and 5 say what they have. A share needs 10 pickups: 9 say no share at all."""
    day = a_closed_day()
    viewer = unlocked(library)
    under_the_floor = a_month(
        library, sittings=st.SITTINGS_FLOOR - 1, decided=st.DECISIONS_FLOOR - 1
    )
    await added_up(temp_db, library.admin.id, day, under_the_floor)
    under = await asked(app, client, viewer, day)
    for block_id in (*page.VIEWING_BLOCKS, "organizing"):
        shown = block(under, block_id)
        assert shown["floor_reached"] is False, block_id
        assert [said(line) for line in shown["statements"]] == [st.NOT_ENOUGH], block_id
        assert (shown["figures"], shown["lists"], shown["chart"]) == ([], [], None), block_id
    assert not any(line.startswith(("You viewed", "You answered")) for line in sentences(under)), (
        sentences(under)
    )

    other = a_closed_day() - timedelta(days=40)
    at_the_floor = a_month(
        library, sittings=st.SITTINGS_FLOOR, decided=st.DECISIONS_FLOOR, pickups=9
    )
    await added_up(temp_db, library.admin.id, other, at_the_floor)
    at = await asked(app, client, viewer, other)
    for block_id in (*page.VIEWING_BLOCKS, "organizing"):
        assert block(at, block_id)["floor_reached"] is True, block_id
    opened = [said(line) for line in block(at, "sittings")["statements"]]
    assert opened and opened[0].startswith("You opened Sift 9 times"), opened
    assert not any("of those times" in line or "Every time" in line for line in opened), opened


async def test_a_comparison_is_said_only_against_a_period_recorded_whole(
    app: FastAPI, client: httpx.AsyncClient, temp_db: Database, library: Library
) -> None:
    """Rule 4. The month before recorded from its 15th: nothing compared. Recorded from its first
    day but below its floor: nothing compared. Whole and past its floor: compared, in figures."""
    admin = library.admin
    viewer = unlocked(library)
    day = a_closed_day()
    month = st.period_of("month", day, local_today(), None)
    before = month.previous()
    assert before is not None
    await added_up(temp_db, admin.id, day, a_month(library))

    async def the_month_before(first: date, sittings: int) -> list[str]:
        """The month before, written afresh from `first`, and what the page then compares."""
        await temp_db.execute(
            "DELETE FROM insight_days WHERE user_id = ? AND day BETWEEN ? AND ?",
            (admin.id, before.start.isoformat(), before.end.isoformat()),
        )
        earlier = [("sittings", "", sittings, 0), ("viewed_ms", "", 29 * HOUR, 0)]
        await added_up(temp_db, admin.id, first, earlier)
        body = await asked(app, client, viewer, day)
        return [line for line in sentences(body) if line.startswith("That's")]

    from_the_15th = before.start + timedelta(days=14)
    assert await the_month_before(from_the_15th, 12) == [], "a month not recorded whole"
    below = st.SITTINGS_FLOOR - 1
    assert await the_month_before(before.start, below) == [], "a month below its floor"
    assert await the_month_before(before.start, st.SITTINGS_FLOOR) == [
        f"That's 12 hours more than {st.named_period(before)}."
    ]


async def test_every_named_thing_is_a_link(
    app: FastAPI, client: httpx.AsyncClient, temp_db: Database, library: Library
) -> None:
    """Rule 6. A piece wearing a thing's name carries its kind and its id, and a piece of a kind
    with a page goes somewhere, the files included."""
    day = a_closed_day()
    await added_up(temp_db, library.admin.id, day, a_month(library))
    body = await asked(app, client, unlocked(library), day)
    every = list(pieces(body))
    names = library.names()
    named = {str(one["text"]): one for one in every if one["kind"]}

    # The two names no plain sentence could say by accident (the others, "person" and "site", are
    # also words a sentence says about a KIND, so they are held by the loop below instead).
    unlinked = [
        str(one["text"])
        for one in every
        if not one["kind"] and (WALL_NAME in str(one["text"]) or HIDDEN_NAME in str(one["text"]))
    ]
    assert not unlinked, f"named in plain words, with nothing to press: {unlinked}"
    assert named[HIDDEN_NAME]["kind"] == "person", "unlocked, the top person is a link"
    for kind, name in names.items():
        assert name in named, f"the {kind} {name!r} is not named as a link anywhere on the page"
        assert named[name]["kind"] == kind, named[name]
    going_nowhere = [
        one for one in every if one["kind"] in PAGED and not (one["id"] or one["href"])
    ]
    assert not going_nowhere, going_nowhere
    # The wall itself, by its id, not the page it lives on.
    assert str(named[WALL_NAME]["href"]).startswith("/theater?wall=")
    files = {str(one["id"]) for one in every if one["kind"] == "asset"}
    assert files >= {library.world.solo, library.world.loose}, files


async def test_no_thing_is_named_by_a_stand_in_where_its_name_is_known(
    app: FastAPI, client: httpx.AsyncClient, temp_db: Database, library: Library
) -> None:
    """Rule 6's other half, History's rule: never "a file" or "something" where the name is
    known. Every file in this library has a name on the disk (`solo.mp4` ...) and none arrived
    under one: the shape of a file Sift downloaded itself."""
    day = a_closed_day()
    await added_up(temp_db, library.admin.id, day, a_month(library))
    body = await asked(app, client, unlocked(library), day)
    stood_in = [
        str(one["text"])
        for one in pieces(body)
        if one["kind"] and STAND_INS.search(str(one["text"]))
    ]
    assert not stood_in, (
        f"a thing named by a stand-in where its name is known: {stood_in}. A file is called what"
        " the file page calls it: its title, its name on the disk, or the name it arrived with."
    )


async def test_what_sift_did_is_an_admins_and_never_a_guests(
    app: FastAPI, client: httpx.AsyncClient, temp_db: Database, library: Library
) -> None:
    """Rule 7, with an admin's page as the known positive."""
    day = a_closed_day()
    await added_up(temp_db, library.admin.id, day, a_month(library))
    await added_up(temp_db, library.guest.id, day, a_month(library))
    admin = await asked(app, client, unlocked(library), day)
    assert "machine" in [one["id"] for one in admin["blocks"]]
    assert any(line.startswith("Sift worked on tasks") for line in sentences(admin))

    guest = await asked(app, client, library.guest, day)
    assert "machine" not in [one["id"] for one in guest["blocks"]]
    said_to_guest = " ".join(words_on_the_page(guest))
    for words in ("Sift worked", "Sift found", "fingerprinted", "What Sift did", "Faces found"):
        assert words not in said_to_guest, words


async def test_a_hidden_person_is_absent_while_locked_and_only_placeholder_mode_says_so(
    app: FastAPI, client: httpx.AsyncClient, temp_db: Database, library: Library
) -> None:
    """Rule 8. Unlocked she is named: the known positive. Locked in Leave-nothing mode her name,
    her id and her hours are gone, the next person down is named, and no word on the page hints
    at the vault. Locked in placeholder mode the one line is said once, and she is still absent."""
    day = a_closed_day()
    await added_up(temp_db, library.admin.id, day, a_month(library))
    hidden = library.hidden_person

    shown = await asked(app, client, unlocked(library), day)
    assert HIDDEN_NAME in json.dumps(shown), "unlocked she is named, or her absence proves nothing"

    locked = await asked(app, client, leave_nothing(library), day)
    dumped = json.dumps(locked)
    assert HIDDEN_NAME not in dumped
    assert hidden not in dumped, "her id reaches a locked page"
    most = [said(line) for line in block(locked, "most_viewed")["statements"]]
    assert most and "was person: 6 hours" in most[0], most
    hints = [words for words in words_on_the_page(locked) if _HINTS.search(words)]
    assert not hints, f"Leave-nothing mode hints at the vault: {hints}"
    assert all(figure["hidden_part"] == 0 for one in locked["blocks"] for figure in one["figures"])
    viewed = block(locked, "overview")["figures"][0]
    assert viewed["value"] == 33 * HOUR, "her hours are still in the locked total"

    marked = await asked(app, client, placeholder(library), day)
    month = st.period_of("month", day, local_today(), None)
    line = text_of(st.some_hidden(month))
    assert sentences(marked).count(line) == 1, sentences(marked)
    assert HIDDEN_NAME not in json.dumps(marked) and hidden not in json.dumps(marked)


# --- the checks can fail ---------------------------------------------------------------------------


def test_the_sentence_rules_find_what_they_are_for() -> None:
    """Known positives, one per rule the words can break, and the lines the page really says."""
    assert broken_rules("You viewed 41.5 hours in August.") == ["a decimal or a percentage"]
    assert broken_rules("You viewed 80% of it.") == ["a decimal or a percentage"]
    assert broken_rules("You spent most of it in Theater.") == ["ranks or compares with no figure"]
    assert broken_rules("You watched 41 hours in August.") == ["watched, not viewed"]
    assert broken_rules("You viewed 41 hours" + " -- " + "a lot.") == ["a dash joining two ideas"]
    assert broken_rules("You only viewed 3 hours.") == ["a judgement"]
    assert broken_rules("You viewed 41 hours in August") == ["not a sentence (no full stop)"]
    assert any(rule.startswith("contractions") for rule in broken_rules("That is 12 hours more."))
    assert any(rule.startswith("History word") for rule in broken_rules("It was cancelled."))
    for fine in (
        "You viewed 41 hours in August: 29 of videos, 9 of pictures, 3 of GIFs.",
        "That's 12 hours fewer than July.",
        "You viewed the most on Fridays: 12 hours.",
        "You opened Sift twice today.",
        "You spent 5 hours in Theater, mostly on your preset 'Nine up'.",
        "Your earliest start was 07:10 on Tuesday; your latest finish 01:40 on Saturday.",
        "Sift found 1,204 faces and fingerprinted 3,000 files.",
    ):
        assert broken_rules(fine) == [], fine


def test_the_insights_screens_are_held_to_viewed() -> None:
    """The client's half of rule 5: the `insights_screens` scope reaches the page and the recaps
    and refuses "watched" there, and nowhere it was not asked to."""
    scope = vocabulary.scope("insights_screens")
    assert any("routes/insights" in fragment for fragment in scope), scope
    markup = "<h2>Time watched this month</h2>"
    assert test_one_word_per_thing.offences(markup, "routes/insights/+page.svelte")
    assert test_one_word_per_thing.offences(markup, "lib/components/insights/RecapCard.svelte")
    assert not test_one_word_per_thing.offences(markup, "lib/components/player/Player.svelte")
