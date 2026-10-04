# SPDX-License-Identifier: AGPL-3.0-or-later
"""A History line names what it knows: every named thing with a page is a link, no line says "a
file", "something" or "someone" when the name is known, and one act reads one sentence on the feed
and on the file's own tab.

## Why these are gates over a seeded LIBRARY, not over the sentence table

Three problems a History line can have. The sentence table (`sift.kernel.tests.test_sentences.SAID`)
holds what each builder says for the arguments a test hands it, and every one of these faults
happens BETWEEN its builder and the screen: a reader that looks the name up on the feed and not on
the entity pages ("Cover set to a file" on a person's page); a reader that hands a builder no name,
so it says "something"; the feed's own copy of the words drifting from the file tab's ("something"
on the end of a line). A table of builder calls cannot see a reader. So a small
library is built (a file, a person, a tag, a Site, a Collection and a Photo Set, each named, one
act each) and EVERY reader is asked for its lines: the six History tabs and the Settings feed.
What they answer is judged:

1. **Every named thing with a page is a link.** A piece whose words are one of the library's names
   and which carries no kind is a name drawn as plain words: nothing to press. A piece of a kind
   that has a page, with neither an id nor an address and not gone, is a link that goes nowhere.
2. **No line says "a file", "something" or "someone"**: every name in this library is known, so
   any of the three is a reader or a builder that dropped one (the feed's rule, applied to every
   surface).
3. **The feed and the file tab say one sentence**, apart from the vantage word: an act on the file
   read in the feed, with the file's name replaced by "this file", is a line on the file's own tab.
   Comparing two copies of the words cannot catch that drift; reading ONE builder from two
   vantages can.

## And two ratchets over the builders, which need no library

- **A count with no link** says why (`NO_LINK_BECAUSE`): a number in a line ("12 faces", "3 files")
  is the run somebody most wants to press, because it is the only way to see what was counted. Every
  line of the sentence table that says a count in plain words is listed with the reason there is
  nowhere to go, and a reason whose line has gone or has gained its link is refused as stale.
- **"a file", "something", "someone" in the source** (`history_ratchets.py`, `unnamed_words`): a
  builder holding the words is a builder that can say them; the count per file may only fall.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import pytest

# Registering the tables a feature owns, so a kernel database has them. See `test_history.py`.
import sift.slices.download.schema
import sift.slices.faces.schema
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository, Viewer
from sift.kernel.access import sentences as say
from sift.kernel.access.history import history_of_asset
from sift.kernel.access.history_entity import (
    history_of_collection,
    history_of_photo_set,
    history_of_site,
    history_of_tag,
)
from sift.kernel.access.history_person import history_of_person
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.ledger import Actor, Object, Reversal, record_event
from sift.kernel.log_settings import DETAIL_KEY
from sift.kernel.settings_registry import get_registered
from sift.kernel.sorting import sort_key
from sift.kernel.tests.test_sentences import SAID
from sift.kernel.vocabulary import Subject
from sift.kernel.workbench import Workbench
from sift.slices.workbench.router import SETTINGS_PANES, ledger, setting_href
from sift.testing.fixtures import Actors
from tests.gates import history_ratchets, server_copy

pytestmark = [pytest.mark.gate, pytest.mark.integration]

_EPOCH = 1_700_000_000

#: The kinds a line can name that have a page (or, for a file, a panel) to go to.
PAGED = frozenset({"asset", "person", "tag", "site", "collection", "photo_set", "song"})

#: The words that stand in for a thing instead of naming it. The ratchet's own pattern.
UNNAMED = history_ratchets.UNNAMED


class _Piece(Protocol):
    """A piece as either side holds it: the kernel's `sentences.Piece` or the wire's."""

    text: str
    kind: str | None
    id: str | None
    href: str | None
    gone: bool
    lead: str

    @property
    def rest(self) -> Sequence[Any]: ...


def pieces_of(line: Iterable[_Piece]) -> list[_Piece]:
    """Every piece of a line, the ones folded behind "and N more" included."""
    found: list[_Piece] = []
    for one in line:
        found.append(one)
        found.extend(pieces_of(one.rest))
    return found


def words_of(line: Iterable[_Piece]) -> str:
    """The line as it reads opened: every run's words, the fold's rest in place of its count."""
    out: list[str] = []
    for one in line:
        out.append(words_of(one.rest) if one.rest else one.lead + one.text)
    return "".join(out)


def _stands_in(name: str, text: str) -> bool:
    """Whether `name` stands whole in `text`: no letter or digit touching it on either side."""
    return re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text) is not None


def unlinked(line: Iterable[_Piece], names: Iterable[str]) -> list[str]:
    """Every named thing in one line that goes nowhere: a name in plain words, or a thing of a
    kind with a page that carries neither an id nor an address."""
    found: list[str] = []
    for one in pieces_of(line):
        if one.kind is None:
            found.extend(
                f"{name!r} is plain words in {one.text!r}"
                for name in names
                if _stands_in(name, one.text)
            )
        elif one.kind in PAGED and not one.gone and not one.id and not one.href:
            found.append(f"{one.text!r} is a {one.kind} with nowhere to go")
    return found


def unnamed(line: Iterable[_Piece]) -> list[str]:
    """Every word in a line's plain words that stands in for a thing instead of naming it."""
    return [
        match.group(0)
        for one in pieces_of(line)
        if one.kind is None
        for match in UNNAMED.finditer(one.text)
    ]


def misnamed(line: Iterable[_Piece], known: Mapping[str, str]) -> list[str]:
    """Every thing in a line whose name is known and which wears other words: "a file" placed AS
    the file (linked, and still not named) is the stand-in the plain-words rule cannot see.
    Only the kinds with a page (and a username): a count carries the id of what it counts."""
    return [
        f"{one.text!r} stands where {known[one.id]!r} is known"
        for one in pieces_of(line)
        if one.kind in PAGED | {"username"} and one.id in known and one.text != known[one.id]
    ]


def disagreements(
    feed: Iterable[Sequence[_Piece]],
    page: Iterable[Sequence[_Piece]],
    thing_id: str,
    vantage: str,
) -> list[str]:
    """Every feed line about one thing whose words, with that thing's name replaced by the page's
    vantage word, are not a line on that thing's own page."""
    said_there = {words_of(line) for line in page}
    found: list[str] = []
    for line in feed:
        if not any(one.id == thing_id for one in pieces_of(line)):
            continue
        here = "".join(
            vantage
            if one.id == thing_id
            else (words_of(one.rest) if one.rest else one.lead + one.text)
            for one in line
        )
        if here not in said_there:
            found.append(f"the feed says {words_of(line)!r}; the page never says {here!r}")
    return found


# --- the library ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Library:
    """One named thing of each kind with a page, and the file every act is about."""

    root: str
    folder: str
    file: str
    person: str
    tag: str
    site: str
    collection: str
    photo_set: str
    username: str
    #: The Site's "poster unknown" username, which a filing by hand goes under.
    nobody: str


#: What each thing is called: invented, lower case where a capital would make a name the cast list
#: has to declare (`data/names_cast.txt` holds the two that are capitalized).
NAMES: Mapping[str, str] = {
    "asset": "holiday.mp4",
    "person": "Neve Alder",
    "tag": "poolside",
    "site": "Sunsetter",
    "collection": "tidewater reel",
    "photo_set": "harbor walk",
    # No page of its own (a press goes to its person or its files, `sentences.username_opens`),
    # and so a link like any other thing a line names.
    "username": "harlowquin",
}


async def _seed(database: Database, admin: Viewer) -> Library:
    """The library, as plain rows, and one act by `admin` on the file toward each other thing.

    Plain rows for the things, and the ledger's own door for the acts: the door is what fills a
    name and refuses a nameless one, so an act recorded around it would prove nothing about what
    the readers are handed.
    """
    lib = Library(**{name: new_id() for name in Library.__slots__})
    await database.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (lib.root, "roll", "/library/roll", _EPOCH),
    )
    await database.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, NULL, ?, ?)",
        (lib.folder, lib.root, "clips", "clips"),
    )
    await database.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, original_filename,"
        " added_at) VALUES (?, ?, 1, 'video', ?, ?)",
        (lib.file, f"digest-{lib.file}", NAMES["asset"], _EPOCH),
    )
    await database.execute(
        "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename,"
        " first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            new_id(),
            lib.file,
            lib.root,
            lib.folder,
            f"clips/{NAMES['asset']}",
            NAMES["asset"],
            _EPOCH,
            _EPOCH,
        ),
    )
    # Four statements written out: query text is never built from a fragment, even here.
    for statement, thing_id, kind in (
        (
            "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
            lib.person,
            "person",
        ),
        ("INSERT INTO tags (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)", lib.tag, "tag"),
        (
            "INSERT INTO sites (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
            lib.site,
            "site",
        ),
        (
            "INSERT INTO collections (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
            lib.collection,
            "collection",
        ),
    ):
        await database.execute(statement, (thing_id, NAMES[kind], sort_key(NAMES[kind]), _EPOCH))
    await database.execute(
        "INSERT INTO photo_sets (id, name, name_sort, origin, created_at)"
        " VALUES (?, ?, ?, 'manual', ?)",
        (lib.photo_set, NAMES["photo_set"], sort_key(NAMES["photo_set"]), _EPOCH),
    )

    # THE ROWS each act leaves, as well as the event, exactly as the writers leave them: a person
    # named or a tag added by hand writes the link row with no source and no moment
    # (`people/service._ASSIGN_PERSON`) and the ledger event with the user; a file filed under a
    # Site by hand goes under the Site's "poster unknown" username, whose name is empty
    # (`people/service.file_under_sites`). A person's, a tag's and a Site's page count their files
    # off these tables, and a library holding the event with no row is one no install has.
    # And a filing a task made, under a named username: no event, only the row a pass leaves.
    for username_id, name, source in (
        (lib.nobody, "", None),
        (lib.username, NAMES["username"], "filename"),
    ):
        await database.execute(
            "INSERT INTO usernames (id, site_id, name, name_sort, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (username_id, lib.site, name, sort_key(name), _EPOCH),
        )
        await database.execute(
            "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at)"
            " VALUES (?, ?, ?, ?)",
            (lib.file, username_id, source, None if source is None else _EPOCH),
        )
    for statement, row in (
        ("INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", lib.person),
        ("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, ?)", lib.tag),
    ):
        await database.execute(statement, (lib.file, row))
    for statement, shelf in (
        (
            "INSERT INTO collection_items (collection_id, asset_id, position, added_at)"
            " VALUES (?, ?, 0, ?)",
            lib.collection,
        ),
        (
            "INSERT INTO photo_set_items (photo_set_id, asset_id, position, added_at)"
            " VALUES (?, ?, 0, ?)",
            lib.photo_set,
        ),
    ):
        await database.execute(statement, (shelf, lib.file, _EPOCH))

    file = Subject(kind="asset", id=lib.file, name=NAMES["asset"])
    by = Actor.user(admin.id)
    async with database.write() as connection:
        for verb, kind, thing_id in (
            ("linked", "person", lib.person),
            ("linked", "tag", lib.tag),
            ("filed", "site", lib.site),
            ("linked", "collection", lib.collection),
            ("linked", "photo_set", lib.photo_set),
        ):
            await record_event(
                connection,
                actor=by,
                verb=verb,
                subject=file,
                object=Object(kind=kind, id=thing_id, name=NAMES[kind]),  # type: ignore[arg-type]
            )
        # And one act on the file alone, with nothing else named in it.
        await record_event(connection, actor=by, verb="saved", subject=file)
        # AND ONE SETTING CHANGED, in the shape the settings hub writes it: a setting has a row
        # rather than a page, and its line on the feed links to that row.
        await record_event(
            connection,
            actor=by,
            verb="edited",
            subject=Subject(kind="setting", id=DETAIL_KEY, name=A_SETTING_LABEL),
            payload=json.dumps({"key": DETAIL_KEY, "before": '"normal"', "after": '"detailed"'}),
        )
        # AND A DECISION SAVED BY AN OLDER BUILD, in the words it saved: a title is kept for good,
        # so every card written before a word was retired still carries it, and what the screen
        # says is the reader's to translate (`sentences.today_words`), never the stored words.
        await record_event(
            connection,
            actor=by,
            verb="decided",
            subject=file,
            receipt=Reversal(queue="faces", title=A_SAVED_TITLE, detail=""),
        )
    return lib


#: WHOSE PAGE each reader's lines are on. A thing's own name on its own page is a VALUE, not a
#: reference ("Sift added this file to the library as holiday.mp4" says what the file arrived
#: called, and a link from a page to itself goes nowhere new), so it is the one name a page may
#: say in plain words.
OWN: Mapping[str, str | None] = {
    "the file's tab": "asset",
    "the person's": "person",
    "the tag's": "tag",
    "the Site's": "site",
    "the Collection's": "collection",
    "the Photo Set's": "photo_set",
    "the feed": None,
}


def names_away_from(where: str) -> list[str]:
    """The library's names a line on this page must link: all but the page's own."""
    return [name for kind, name in NAMES.items() if kind != OWN[where]]


#: The setting the library changes, by the label the settings hub snapshots.
A_SETTING_LABEL = "Log detail"

#: A title an older build saved, in a word the History word table has since retired.
A_SAVED_TITLE = "A group of 3 faces set aside"


async def _pages(
    database: Database, access: Repository, viewer: Viewer, lib: Library
) -> dict[str, list[Sequence[_Piece]]]:
    """Every line each of the six History tabs says."""
    return {
        "the file's tab": [
            one.pieces for one in await history_of_asset(database, access, viewer, lib.file)
        ],
        "the person's": [
            one.pieces for one in await history_of_person(database, viewer, lib.person)
        ],
        "the tag's": [one.pieces for one in await history_of_tag(database, viewer, lib.tag)],
        "the Site's": [one.pieces for one in await history_of_site(database, viewer, lib.site)],
        "the Collection's": [
            one.pieces for one in await history_of_collection(database, viewer, lib.collection)
        ],
        "the Photo Set's": [
            one.pieces for one in await history_of_photo_set(database, viewer, lib.photo_set)
        ],
    }


async def _feed(database: Database, viewer: Viewer) -> list[Sequence[_Piece]]:
    """Every line the Settings feed says, from the route itself: the feed has no reader of its
    own below the route, so the route IS the reader. No queue is registered, which every event
    here is not a receipt of."""
    page = await ledger(database=database, bench=Workbench(), runs=Ledger(database), viewer=viewer)
    return [item.pieces for item in page.items]


@pytest.fixture
async def lines(
    temp_db: Database, access: Repository, actors: Actors
) -> tuple[Library, dict[str, list[Sequence[_Piece]]]]:
    lib = await _seed(temp_db, actors.admin)
    said = await _pages(temp_db, access, actors.admin, lib)
    said["the feed"] = await _feed(temp_db, actors.admin)
    return lib, said


async def test_every_reader_answers_for_the_library(
    lines: tuple[Library, dict[str, list[Sequence[_Piece]]]],
) -> None:
    """The canary: a reader that drew nothing, or drew only the arrival, would pass both rules
    below over lines that never named anything. Each thing's page says the act that reached the
    file (a line naming the file, or counting it); the file's tab names all five things; the feed
    says all six acts."""
    lib, said = lines
    assert set(said) == set(OWN)
    for where, found in said.items():
        if OWN[where] in (None, "asset"):
            continue
        kinds = {one.kind for line in found for one in pieces_of(line)}
        assert kinds & {"asset", "files"}, (where, [words_of(one) for one in found])
    on_the_file = {one.kind for line in said["the file's tab"] for one in pieces_of(line)}
    assert {"person", "tag", "site", "collection", "photo_set"} <= on_the_file
    assert len(said["the feed"]) >= 6, [words_of(one) for one in said["the feed"]]
    assert any(one.id == lib.file for line in said["the feed"] for one in pieces_of(line))


def _unlinked_in(said: Mapping[str, list[Sequence[_Piece]]], where_: Iterable[str]) -> list[str]:
    return [
        f"{where}: {words_of(line)!r}: {fault}"
        for where in where_
        for line in said[where]
        for fault in unlinked(line, names_away_from(where))
    ]


def _refuse_unlinked(found: list[str]) -> None:
    assert not found, (
        "\nA History line names a thing that has a page and does not link it:\n\n  "
        + "\n  ".join(found)
        + "\n\nPlace the thing as a piece (`sentences.thing`, or `mention` for a Subject) where it"
        "\nsits in the line; never as plain words.\n"
    )


# Two tests rather than one, by surface, so a fault on one surface cannot hide a guard on the other:
# the feed builds its pieces in the route, the tabs in the three readers.


async def test_every_named_thing_the_feed_says_is_a_link(
    lines: tuple[Library, dict[str, list[Sequence[_Piece]]]],
) -> None:
    _lib, said = lines
    _refuse_unlinked(_unlinked_in(said, ["the feed"]))


async def test_a_setting_line_on_the_feed_links_to_its_row_in_settings(
    lines: tuple[Library, dict[str, list[Sequence[_Piece]]]],
) -> None:
    """A setting has no page and does have a row: its line on the feed is a link to that row, the
    address the client's own settings links use (`/settings/<pane>#<key>`), never plain words."""
    _lib, said = lines
    settings = [
        one
        for line in said["the feed"]
        for one in pieces_of(line)
        if one.kind == "setting" and one.id == DETAIL_KEY
    ]
    assert settings, [words_of(line) for line in said["the feed"]]
    assert all(one.text == A_SETTING_LABEL for one in settings)
    assert all(one.href == f"/settings/logs#{DETAIL_KEY}" for one in settings)
    plain = [
        words_of(line)
        for line in said["the feed"]
        for one in pieces_of(line)
        if one.kind is None and _stands_in(A_SETTING_LABEL, one.text)
    ]
    assert not plain, plain


def test_a_setting_with_no_row_is_plain_words_and_a_sites_own_opens_the_site_list() -> None:
    """A key the registry does not know has no row to land on; a Site's own setting (the name
    template a Site keeps) is drawn in the Downloads pane's list of Sites."""
    assert setting_href("no.such.setting") is None
    assert setting_href("site_options.instagram.naming") == (
        "/settings/downloads#downloads.name_template"
    )
    registered = get_registered(DETAIL_KEY)
    assert registered is not None
    assert (
        setting_href(DETAIL_KEY) == f"/settings/{SETTINGS_PANES[registered.section]}#{DETAIL_KEY}"
    )


#: The client's one join of the two lists of sections: `REGISTRY_HOME` in `sections.ts`.
_SECTIONS_TS = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "settings-ui" / "sections.ts"
)
_HOME_ENTRY = re.compile(r"^\t(?:'([^']+)'|([A-Za-z]+)):\s*'([^']+)',?$", re.M)


def test_the_panes_a_setting_line_links_to_are_the_clients_own() -> None:
    """The server's pane per section (`SETTINGS_PANES`) is the client's `REGISTRY_HOME`, entry for
    entry: a pane renamed on one side and not the other would link a line to a pane that is not
    there."""
    text = _SECTIONS_TS.read_text(encoding="utf-8")
    found = re.search(r"export const REGISTRY_HOME\b[^=]*=\s*\{(.*?)\n\};", text, re.S)
    assert found, "REGISTRY_HOME was not found in sections.ts"
    client = {(quoted or bare): pane for quoted, bare, pane in _HOME_ENTRY.findall(found.group(1))}
    assert client, "no entry of REGISTRY_HOME was read"
    assert dict(SETTINGS_PANES) == client


async def test_every_named_thing_a_history_tab_says_is_a_link(
    lines: tuple[Library, dict[str, list[Sequence[_Piece]]]],
) -> None:
    _lib, said = lines
    _refuse_unlinked(_unlinked_in(said, [where for where in said if where != "the feed"]))


async def test_no_line_says_a_file_something_or_someone_when_the_name_is_known(
    lines: tuple[Library, dict[str, list[Sequence[_Piece]]]],
) -> None:
    lib, said = lines
    known = {getattr(lib, kind): name for kind, name in NAMES.items() if kind != "asset"}
    known[lib.file] = NAMES["asset"]
    found = [
        f"{where}: {words_of(line)!r}: {fault}"
        for where, found_there in said.items()
        for line in found_there
        for fault in [*(f"says {word!r}" for word in unnamed(line)), *misnamed(line, known)]
    ]
    assert not found, (
        "\nEvery name in this library is known, and a line still stands in for one:\n\n  "
        + "\n  ".join(found)
        + "\n\nThe reader hands its builder the name (the ledger's `names_now` for a row that kept"
        "\nnone); that builder places it.\n"
    )


async def test_no_line_says_a_retired_word_even_from_a_title_saved_before_it_was_retired(
    lines: tuple[Library, dict[str, list[Sequence[_Piece]]]],
) -> None:
    """The History word table over every line every reader draws, the saved decision title
    included, which an older build wrote in a word the table has since retired. The words on
    screen are today's (`sentences.today_words`); the stored ones never reach it."""
    _lib, said = lines
    found = [
        f"{where}: {words_of(line)!r} says {word!r} -> {instead}"
        for where, found_there in said.items()
        for line in found_there
        for word, instead in history_ratchets.history_words_in(words_of(line))
    ]
    assert any(
        "3 faces" in words_of(line) for found_there in said.values() for line in found_there
    ), "no reader drew the saved decision, so its words were never judged"
    assert not found, (
        "\nA History line says a word the History word table retired:\n\n  "
        + "\n  ".join(found)
        + "\n\nA saved title goes through `sentences.today_words` wherever it is drawn.\n"
    )


def _disagree_about(
    said: Mapping[str, list[Sequence[_Piece]]], lib: Library, kinds: set[str]
) -> list[str]:
    """The feed's lines about the file that name a thing of these kinds (or, for `asset`, nothing
    but the file), against the file's own tab."""
    chosen = [
        line
        for line in said["the feed"]
        if any(one.id == lib.file for one in pieces_of(line))
        and (
            {one.kind for one in pieces_of(line) if one.kind is not None and one.id != lib.file}
            or {"asset"}
        )
        & kinds
    ]
    assert len(chosen) == len(kinds), (kinds, [words_of(one) for one in said["the feed"]])
    return disagreements(chosen, said["the file's tab"], lib.file, say.HERE[say.VANTAGE_FILE])


def _refuse_disagreements(found: list[str]) -> None:
    assert not found, (
        "\nOne act, two sentences: the feed and the file's own tab must say the same line, with"
        '\nonly the file\'s name and "this file" between them.\n\n  ' + "\n  ".join(found) + "\n"
    )


# Two tests by what the act did to the file, for the reason the link rule is two: the three acts a
# LINK TABLE also records (a naming, a tagging, a filing) are read on the file's tab from that table,
# and the three it does not are read from the ledger: two paths to the same sentence.


async def test_the_feed_and_the_file_tab_say_one_sentence_for_what_the_file_went_into(
    lines: tuple[Library, dict[str, list[Sequence[_Piece]]]],
) -> None:
    lib, said = lines
    _refuse_disagreements(_disagree_about(said, lib, {"collection", "photo_set", "asset"}))


async def test_the_feed_and_the_file_tab_say_one_sentence_for_a_naming_a_tagging_and_a_filing(
    lines: tuple[Library, dict[str, list[Sequence[_Piece]]]],
) -> None:
    lib, said = lines
    _refuse_disagreements(_disagree_about(said, lib, {"person", "tag", "site"}))


# --- the three rules can fail ----------------------------------------------------------------------


def test_a_name_in_plain_words_and_a_link_to_nowhere_are_refused() -> None:
    person = say.thing("person", "p1", "Neve Alder")
    assert unlinked(say.said("You named ", person, " in this file"), NAMES.values()) == []
    assert unlinked(say.said("You named Neve Alder in this file"), NAMES.values()) == [
        "'Neve Alder' is plain words in 'You named Neve Alder in this file'"
    ]
    nowhere = say.Piece(text="poolside", kind="tag")
    assert unlinked(say.said("You added ", nowhere), NAMES.values()) == [
        "'poolside' is a tag with nowhere to go"
    ]
    # Folded behind "and N more" is still in the line.
    folded = say.listed([(say.Piece(f"t{n}"),) for n in range(6)] + [(say.Piece("poolside"),)])
    assert unlinked(folded, NAMES.values()) == ["'poolside' is plain words in ', t5 and poolside'"]
    # A name inside a longer word is not the name.
    assert unlinked(say.said("poolsides"), NAMES.values()) == []


def test_a_file_something_and_someone_are_refused_in_words_and_never_in_a_name() -> None:
    assert unnamed(say.said("You removed something from a file")) == ["something", "a file"]
    assert unnamed(say.said("Someone hid this file")) == ["Someone"]
    # A THING whose name holds the words is somebody's typing, not a stand-in...
    assert unnamed(say.said("You added ", say.thing("tag", "t", "something"))) == []
    # ...unless its name is known to be something else: then the thing wears a stand-in.
    file = say.thing("asset", "a1", "a file")
    assert misnamed(say.said("You saved ", file), {"a1": "holiday.mp4"}) == [
        "'a file' stands where 'holiday.mp4' is known"
    ]
    counted = say.thing("files", "a1", "1 file", href="/browse")
    assert misnamed(say.said(counted), {"a1": "holiday.mp4"}) == []


def test_a_feed_line_that_drifted_from_the_page_is_refused() -> None:
    file = say.thing("asset", "a1", "holiday.mp4")
    feed = [say.said("You hid ", file), say.said("You hid ", file, " and something")]
    page = [say.said("You hid this file")]
    assert disagreements(feed, page, "a1", "this file") == [
        "the feed says 'You hid holiday.mp4 and something'; "
        "the page never says 'You hid this file and something'"
    ]


# --- a count with no link says why (the sentence table; no library needed) -------------------------

#: A NUMBER THAT COUNTS THINGS, in plain words: "12 faces", "3 files", "123456789 bytes".
COUNTED = re.compile(
    r"\b\d[\d,]*\s+(?:more\s+)?(?:files?|faces?|people|tags?|usernames?|photos?|groups?"
    r"|details?|bytes|Photo Sets?|Collections?|Sites?|decisions?|matches|copies|folders?)\b"
)

#: EVERY LINE OF THE SENTENCE TABLE THAT SAYS A COUNT AND LINKS NOTHING, with the reason there is
#: nowhere to go, by the table's own label. A count with no link has its link or its row here; a
#: row is a decision, and its reason is checked against the code.
NO_LINK_BECAUSE: Mapping[str, str] = {
    # --- THE COUNT HEADS WHAT IT COUNTED, opened under the line (`history.Event.detail`, a feed
    # line's groups): the "link" is the fold, and a second address for the same set would be two
    # ways to one list.
    "file: a press of namings": "the people it counts open under the line (its detail)",
    "feed: which stash-box added each filing, recorded": (
        "the files a one-time step gave a box to on update; no filter names that set"
    ),
    "file: a box recognized it": "what the box wrote opens under the line, grouped (its detail)",
    "entity: a day of downloads": "the day's files open under the line (`downloads_folded`)",
    "entity: files a task put in it": "the day's files open under the line (`_addition_events`)",
    "ledger: many fields edited": "the fields open under the line (`edited_folded`)",
    "ledger: a decision": "what the decision was about opens under Show each (its groups)",
    "feed: a decision": "what the decision was about opens under Show each (its groups)",
    "file: a box recognized it, a certain match": "what the box wrote opens under the line",
    # `feed_folded`: ONE PRESS OF MANY ACTS as one line, "with what it stands for under Show each".
    "feed folded: a task filed files under many usernames": "the press opens under Show each",
    "feed folded: files added to one Collection": "the press opens under Show each",
    "feed folded: the floor task's Photo Sets": "the press opens under Show each",
    "feed folded: the backfilled usernames": "the press opens under Show each",
    "feed folded: faces Sift recognized": "the press opens under Show each",
    "feed folded: a song named on many files from the same music": "the files it named open under Show each",
    "feed folded: a song named on many files from AcoustID": "the press opens under Show each",
    "feed folded: a song named on many files from a Site's page": "the press opens under Show each",
    # --- THERE IS NOTHING TO GO TO, and the reason is in the code the line reads.
    # history_person.py `_REJECTED`: a refusal takes the name OFF the face, so the faces are back
    # among everybody's and no wall holds "the faces refused as them".
    "person: faces marked not them": "a refused face carries no name; no wall lists them",
    # `sentences_songs.departures_kept`: the files are deleted, so no wall can list them; the line
    # says the count because kept deletions nobody asked for would otherwise read as a fault.
    "feed: departures kept": "the files it counts are deleted; nothing lists them",
    # `_TAUGHT` counts `face_references`: the crops Sift matches by, uploaded ones with no file at
    # all (`asset_id` NULL). The three `?show=` walls hold appearances, not references.
    "person: learned from faces": "references are crops, some with no file; no wall lists them",
    # `_RULED_OUT` reads `asset_person_refusals`, which no facet or screen but History reads.
    "person: marked not in files": "no screen lists the files a person was marked as not in",
    # `faces_taken_off`: a deleted face ceases to exist.
    "file: faces deleted": "a deleted face no longer exists",
    # `forgot` over Faces: the names are gone, so no filter can find the files they were on.
    "feed: face data deleted": "the names it removed are gone; no filter finds their files now",
    # "fewer than 10 photos" is the floor the task applies, not a set of anything.
    "feed: a Photo Set under the floor": "the number is the task's threshold, not a set",
    # A task's count is of the moment it ran; the folder beside it IS linked, and a filter on it
    # would list the folder's files today rather than the ones the task touched.
    "feed: a task over many files": "the folder is the link; the count is of the moment it ran",
    # A SWAP'S END. A swap has no page (`SubjectKind` "swap"), and no filter narrows the files to
    # one swap: what one swap RECEIVED opens under Show each on its arrivals line (the fold keyed by
    # the session, `history_events._SWAP_SESSION`); what one SENT stayed where it was, and no row on
    # the sending side records which files a swap sent: the host writes only
    # `swap_sessions.sent_files`, a count (`store.add_sent`); manifests are the receiving side's.
    "feed: a swap received its files": "the swap's arrivals line opens them under Show each",
    "feed: a swap sent its files": "no row records which files a swap sent, only how many",
    "feed: a swap this device ended": "the swap's arrivals line opens them under Show each",
    "feed: a swap the other device ended": "no row records which files a swap sent, only how many",
    "feed: a swap that lost the connection": "the swap's arrivals line opens them under Show each",
    "feed folded: files that arrived by swap": "the press opens under Show each",
    # Catalog step 81's one line: a count of the moment the update ran, as a task's is; the songs it
    # made are on the Songs page, each opening its own files.
    "feed: songs moved onto their own rows": "the count is of the moment the update ran",
}


def counts_without_a_link(said: Sequence[tuple[str, str | None, say.Line]]) -> list[str]:
    """The labels of every line that says a count in plain words."""
    return [
        what
        for what, _by, line in said
        if any(one.kind is None and COUNTED.search(one.text) for one in pieces_of(line))
    ]


def test_every_count_is_a_link_or_says_why_not() -> None:
    counted = counts_without_a_link(SAID)
    missing = [what for what in counted if what not in NO_LINK_BECAUSE]
    stale = [what for what in NO_LINK_BECAUSE if what not in counted]
    assert not missing, (
        "\nA line counts things in plain words, and nothing says why the count goes nowhere:\n\n  "
        + "\n  ".join(missing)
        + "\n\nMake the count a thing (`sentences.thing('files'|'faces', ..., href=...)`) that opens"
        "\nexactly what it counted, or add the line to NO_LINK_BECAUSE with the reason.\n"
    )
    assert not stale, (
        f"\nThese reasons outlived their line (renamed, or the count gained its link): {stale}."
        "\nDelete them, so a reason never excuses a line it was not written for.\n"
    )


def test_a_count_in_plain_words_is_found_and_a_linked_one_is_not() -> None:
    plain = ("x", None, say.said("Sift found 12 faces here"))
    linked = ("y", None, say.said(say.thing("faces", "p", "12 faces", href="/x"), " were here"))
    bytes_ = ("z", None, say.said("That freed 123456789 bytes"))
    assert counts_without_a_link([plain, linked, bytes_]) == ["x", "z"]


@pytest.fixture(scope="module")
def held() -> list[server_copy.Copy]:
    return server_copy.history_copy()


def test_no_history_source_says_more_unnamed_words_than_was_recorded(
    held: list[server_copy.Copy],
) -> None:
    failure = history_ratchets.held("unnamed_words", held)
    assert failure is None, failure


def test_no_history_source_says_a_size_in_bytes_more_than_was_recorded(
    held: list[server_copy.Copy],
) -> None:
    """A saved detail that says a size as a raw figure ("123456789 bytes"). Not a count
    of things with a page (a size has none), so its answer is the words, not a link."""
    failure = history_ratchets.held("raw_figures", held)
    assert failure is None, failure


def test_the_two_ratchets_find_their_words() -> None:
    assert history_ratchets.unnamed_words_in("Cover set to a file, by someone") == [
        ("a file", "the thing's name, or the line in the passive"),
        ("someone", "the thing's name, or the line in the passive"),
    ]
    assert [found for found, _ in history_ratchets.raw_figures_in("That freed   bytes.")] == [
        "bytes"
    ]
