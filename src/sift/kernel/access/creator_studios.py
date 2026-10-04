# SPDX-License-Identifier: AGPL-3.0-or-later
"""A stash-box studio that is one creator's own store, read as what it is: her username.

A stash-box keeps many creators as a studio of their own, named after them, with her pages on the
sites she sells on as the studio's links. Read the way any other studio is read, every file of hers
is filed under a Site called by her name, and the Sites wall fills with people. A creator filed as
a studio UNDER A NETWORK is read as a username by the stash-box slice (`adapter.creator_account`);
this is the reading for a creator's studio with no network above it.

## The rule

Two signs, read off what the box said about the studio and its scenes:

- **Her name.** The studio's name, or one of its aliases, is a performer credited on its scenes,
  and the SHARE of its scenes that credit her is what counts: a creator is in nearly every scene
  of her own store, and a producer's studio credits whoever it filmed.
- **Her store.** One of the studio's links is a creator's own page on a store
  (`urls.CREATOR_STORE_HOSTS`): a Clips4Sale studio page, a ManyVids profile, an OnlyFans page.

Both, with her in at least `HERS` of its scenes: it is her username on the store's Site, and the
files go there. Neither, or a store with nobody of its name credited: it stays a Site, which is
what a producer selling on Clips4Sale is. Anything between (a store and her in fewer than `HERS`
of its scenes, or her name in at least `ASK_FROM` of them with no store but a page of hers
somewhere) is filed as before and ASKED about, so nothing is decided silently either way.

One scene is a reading too, and the lookup reads each scene so: both signs on that scene make
the username at once (`stash_boxes.adapter`), and anything less is a Site as before, which the
question then asks about once its scenes are counted.

## What it does to a library that has such Sites

`repair` is catalog version 80: every Site a stash-box made whose reading is a username has the
box's filings moved to the username, the Site goes where nothing else is filed under it, and each
move is one History line with an Undo that puts the Site back whole (`put_back`).

`creator_studios` remembers each answer by the studio's name, so a lookup still running, or a
match kept from before, files the studio's next scene under the username rather than making the
Site again (`account_for`), and a studio somebody said is a Site is not asked about again.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sift.kernel.access.catalog import (
    Made,
    by_sift,
    link_username_to_asset_on,
    link_username_to_person,
    seed_site_username_on,
    site_home,
)
from sift.kernel.db import Connection, Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, Object, Reversal, record_event
from sift.kernel.log import get_logger
from sift.kernel.migrations import table_exists
from sift.kernel.site_icons import name_for
from sift.kernel.urls import is_a_creator_store, is_a_reference_page, username_in
from sift.kernel.vocabulary import VIA_UPDATE, Subject

log = get_logger(__name__)

#: The share of a studio's scenes that must credit her by its name for her store to be her
#: username. Four in five: a creator's own store credits her on nearly every clip, with a guest
#: now and then, and a producer's studio is nowhere near it.
HERS = 0.8

#: The share from which her name alone, with no store, is worth a question. Half: a studio named
#: after somebody who is in half of what it released is either hers or a studio named after its
#: lead, and only a person can say which.
ASK_FROM = 0.5

#: The receipts' queue: the name the stash-box slice registers the question and its Undo under.
QUEUE = "studios"

#: The word a stash-box's filing carries in `asset_usernames.source`. Spelled here as the slice
#: spells it (`stash_boxes.enrich.IMPORTED`), because the kernel may not import the slice.
BOX_FILING = "stash_box"

#: The table the answers are kept in. A studio's name is what a box's scene says (its `site`), so
#: it is the key; the username is where its files go, and NULL is the answer that it is a Site.
CREATE_CREATOR_STUDIOS = """
CREATE TABLE IF NOT EXISTS creator_studios (
  name        TEXT PRIMARY KEY COLLATE NOCASE,
  username_id TEXT REFERENCES usernames(id) ON DELETE CASCADE,
  decided_at  INTEGER NOT NULL
)
"""


class Verdict(StrEnum):
    """What a studio is, read off its signs."""

    USERNAME = "username"
    SITE = "site"
    ASK = "ask"


@dataclass(frozen=True, slots=True)
class Home:
    """Where her files would go: her username on a Site, and her page there."""

    site: str
    handle: str
    url: str


@dataclass(frozen=True, slots=True)
class Reading:
    """A studio read: the verdict, where the username would be, and the numbers behind it."""

    verdict: Verdict
    home: Home | None
    #: How many of its scenes were read, and how many of them credit her by the studio's name.
    scenes: int
    credited: int
    #: How many other people its scenes credit. Said beside a question, never decided on.
    others: int
    #: Whether `home` is a store page rather than some other page of hers.
    store: bool


def fold(name: str) -> str:
    """A name as compared here: its letters and digits, lower case, one space between words.

    So "Esme Wrenfield", "esme-wrenfield" and "Esme  Wrenfield's" compare as one name, the way a
    store spells a page after the person who keeps it.
    """
    return " ".join(re.findall(r"[0-9a-z]+", name.casefold().replace("'", "")))


def _homes(
    name: str, aliases: Sequence[str], links: Sequence[str]
) -> tuple[Home | None, Home | None]:
    """Her store page and, failing one, any other page of hers on a Site Sift knows.

    A page on the Site the studio IS (a studio called OnlyFans linking onlyfans.com) is never a home:
    that is the Site's own address. A page about her in a reference database is not one either. Of
    several store pages the one named after her (the studio's name or an alias) is chosen, else the
    first, so one reading always picks one place.
    """
    own = fold(name)
    names = {fold(one) for one in (name, *aliases)} - {""}
    stores: list[Home] = []
    pages: list[Home] = []
    for url in links:
        site = name_for(url)
        handle = username_in(url)
        if not site or not handle or fold(site) == own or is_a_reference_page(url):
            continue
        (stores if is_a_creator_store(url) else pages).append(Home(site, handle, url))
    store = next((one for one in stores if fold(one.handle) in names), None)
    return store or (stores[0] if stores else None), pages[0] if pages else None


def read_studio(
    name: str, aliases: Sequence[str], links: Sequence[str], credits: Sequence[Sequence[str]]
) -> Reading:
    """The rule (see the module's note), over a studio's name, aliases, links and credits.

    `credits` is one list of names per scene read: the performers each scene credits.
    """
    names = {fold(one) for one in (name, *aliases)} - {""}
    scenes = len(credits)
    credited = sum(1 for scene in credits if names & {fold(one) for one in scene})
    others = len({fold(one) for scene in credits for one in scene} - names - {""})
    store, page = _homes(name, aliases, links)
    share = credited / scenes if scenes else 0.0
    if store is not None and credited and share >= HERS:
        verdict, home = Verdict.USERNAME, store
    elif store is not None and credited:
        verdict, home = Verdict.ASK, store
    elif store is None and page is not None and scenes and share >= ASK_FROM:
        verdict, home = Verdict.ASK, page
    else:
        verdict, home = Verdict.SITE, None
    return Reading(
        verdict=verdict,
        home=home,
        scenes=scenes,
        credited=credited,
        others=others,
        store=home is not None and home is store,
    )


# --- Reading a Site a stash-box made ------------------------------------------------------------

#: Every Site a stash-box made, by the provenance its row carries: made by a box, or by Sift while
#: applying a box's answer.
_BOX_SITES = (
    "SELECT id, name FROM sites WHERE created_by_kind = 'box' OR created_by_via = 'stash'"
    " ORDER BY name"
)
_SITE = "SELECT id, name FROM sites WHERE id = ?"
_ALIASES = "SELECT alias FROM site_aliases WHERE site_id = ? ORDER BY id"
_LINKS = "SELECT url FROM site_links WHERE site_id = ? ORDER BY id"
#: The Site's own nameless row: where "filed under a Site" files land when nobody posted them.
_NAMELESS = "SELECT id FROM usernames WHERE site_id = ? AND name = '' ORDER BY id LIMIT 1"
#: What each file the box filed here was said to be: the applied answers kept beside them.
_SCENES = """
SELECT link.asset_id AS asset_id, m.payload AS payload FROM asset_usernames link
  JOIN asset_stash_box_matches m ON m.asset_id = link.asset_id AND m.state = 'applied'
 WHERE link.username_id = ? AND link.source = 'stash_box'
"""
_FILINGS = (
    "SELECT asset_id, source, decided_at, post_id, box_id FROM asset_usernames"
    " WHERE username_id = ? AND source = 'stash_box' ORDER BY asset_id"
)
_ANSWERED = "SELECT username_id FROM creator_studios WHERE name = ?"


@dataclass(frozen=True, slots=True)
class Signs:
    """A Site as the rule reads it, with the row's id and its nameless row's."""

    site_id: str
    name: str
    nameless_id: str | None
    files: int
    reading: Reading


def _credits(payload: str, names: set[str]) -> list[str] | None:
    """The performers one kept answer credits, where its studio is this Site; None otherwise."""
    try:
        records: Any = json.loads(payload)
    except ValueError:
        return None
    record = records[0] if isinstance(records, list) and records else {}
    fields = record.get("fields") if isinstance(record, dict) else None
    if not isinstance(fields, dict) or fold(str(fields.get("site") or "")) not in names:
        return None
    people = fields.get("people")
    return [str(one) for one in people if str(one).strip()] if isinstance(people, list) else []


async def signs_of(connection: Connection, site_id: str) -> Signs | None:
    """One Site read by the rule, from what the boxes said about it and about its files."""
    site = await (await connection.execute(_SITE, (site_id,))).fetchone()
    if site is None:
        return None
    return await _signs(connection, site_id, str(site["name"]))


async def _signs(connection: Connection, site_id: str, name: str) -> Signs:
    """A Site that is there, read by the rule."""
    aliases = [str(row[0]) for row in await connection.execute_fetchall(_ALIASES, (site_id,))]
    links = [str(row[0]) for row in await connection.execute_fetchall(_LINKS, (site_id,))]
    nameless = await (await connection.execute(_NAMELESS, (site_id,))).fetchone()
    nameless_id = None if nameless is None else str(nameless["id"])
    # One scene per FILE: a file two boxes recognised carries two kept answers, and counting both
    # would count her twice for one clip.
    by_file: dict[str, list[str]] = {}
    files: set[str] = set()
    if nameless_id is not None and await table_exists(connection, "asset_stash_box_matches"):
        names = {fold(one) for one in (name, *aliases)} - {""}
        for row in await connection.execute_fetchall(_SCENES, (nameless_id,)):
            asset_id = str(row["asset_id"])
            files.add(asset_id)
            credited = _credits(str(row["payload"]), names)
            if credited is not None:
                by_file.setdefault(asset_id, []).extend(credited)
    credits = list(by_file.values())
    return Signs(
        site_id=site_id,
        name=name,
        nameless_id=nameless_id,
        files=len(files),
        reading=read_studio(name, aliases, links, credits),
    )


async def box_sites(connection: Connection) -> list[Signs]:
    """Every Site a stash-box made, each read by the rule."""
    rows = await connection.execute_fetchall(_BOX_SITES)
    return [await _signs(connection, str(row["id"]), str(row["name"])) for row in rows]


async def answered(connection: Connection, name: str) -> bool:
    """Whether somebody (or the repair) has already said what this studio is."""
    return await (await connection.execute(_ANSWERED, (name,))).fetchone() is not None


# --- Moving a Site's files to her username ------------------------------------------------------

#: The Site's row, every column, for the Undo to put back as it was.
_SITE_COLUMNS = (
    "id",
    "name",
    "kind",
    "cover_asset_id",
    "notes",
    "parent_id",
    "cover_at_ms",
    "name_sort",
    "cover_upload_id",
    "created_by_box_id",
    "created_at",
    "created_by_kind",
    "created_by_user_id",
    "created_by_via",
    "keep_local",
    "cover_frame",
    "keep_from_swaps",
    "cover_cleared_at",
    "cover_by_default",
    "drawn_by_pack",
    "edited_at",
)
_SITE_ROW = (
    "SELECT id, name, kind, cover_asset_id, notes, parent_id, cover_at_ms, name_sort, "
    "cover_upload_id, created_by_box_id, created_at, created_by_kind, created_by_user_id, "
    "created_by_via, keep_local, cover_frame, keep_from_swaps, cover_cleared_at, "
    "cover_by_default, drawn_by_pack, edited_at FROM sites WHERE id = ?"
)
_PUT_SITE_BACK = (
    "INSERT INTO sites (id, name, kind, cover_asset_id, notes, parent_id, cover_at_ms, "
    "name_sort, cover_upload_id, created_by_box_id, created_at, created_by_kind, "
    "created_by_user_id, created_by_via, keep_local, cover_frame, keep_from_swaps, "
    "cover_cleared_at, cover_by_default, drawn_by_pack, edited_at) VALUES (?, ?, ?, ?, ?, "
    "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING"
)
_ALIAS_ROWS = "SELECT id, alias, alias_sort, added_at FROM site_aliases WHERE site_id = ?"
_LINK_ROWS = "SELECT id, url, label, created_at FROM site_links WHERE site_id = ?"
_BOX_LINK_ROWS = (
    "SELECT box_id, remote_id, payload, fetched_at FROM site_stash_box_links WHERE site_id = ?"
)
_NAMELESS_ROW = "SELECT id, created_at FROM usernames WHERE id = ?"
_PUT_ALIAS_BACK = (
    "INSERT INTO site_aliases (id, site_id, alias, alias_sort, added_at) VALUES (?, ?, ?, ?, ?)"
    " ON CONFLICT DO NOTHING"
)
_PUT_LINK_BACK = (
    "INSERT INTO site_links (id, site_id, url, label, created_at) VALUES (?, ?, ?, ?, ?)"
    " ON CONFLICT DO NOTHING"
)
_PUT_BOX_LINK_BACK = (
    "INSERT INTO site_stash_box_links (site_id, box_id, remote_id, payload, fetched_at)"
    " VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING"
)
_PUT_NAMELESS_BACK = (
    "INSERT INTO usernames (id, site_id, name, name_sort, created_at) VALUES (?, ?, '', '', ?)"
    " ON CONFLICT DO NOTHING"
)
_PUT_FILING_BACK = (
    "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at, post_id, box_id)"
    " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(asset_id, username_id) DO NOTHING"
)
_TAKE_FILING = "DELETE FROM asset_usernames WHERE asset_id = ? AND username_id = ?"
_TAKE_BOX_FILINGS = "DELETE FROM asset_usernames WHERE username_id = ? AND source = 'stash_box'"
_DELETE_USERNAME = "DELETE FROM usernames WHERE id = ?"
_KEEP_EDITED_AT = "UPDATE sites SET edited_at = ? WHERE id = ?"
_DELETE_SITE = "DELETE FROM sites WHERE id = ?"
_SITE_NAMED = "SELECT id FROM sites WHERE name = ?"
_USERNAME_FOLDED = (
    "SELECT id, name FROM usernames WHERE site_id = ? AND name = ? COLLATE NOCASE"
    " ORDER BY id LIMIT 1"
)
_SET_URL = "UPDATE usernames SET url = ? WHERE id = ? AND (url IS NULL OR url = '')"
_ADD_HOME = (
    "INSERT INTO site_links (id, site_id, url, created_at) VALUES (?, ?, ?, ?)"
    " ON CONFLICT DO NOTHING"
)
_PERSON_NAMED = "SELECT id FROM people WHERE name = ? COLLATE NOCASE LIMIT 2"
_UNLINK_PERSON = "UPDATE usernames SET person_id = NULL WHERE id = ? AND person_id = ?"
_REMEMBER = (
    "INSERT INTO creator_studios (name, username_id, decided_at) VALUES (?, ?, ?)"
    " ON CONFLICT(name) DO UPDATE SET username_id = excluded.username_id,"
    " decided_at = excluded.decided_at"
)
_FORGET = "DELETE FROM creator_studios WHERE name = ?"
_HOLDS_FILES = "SELECT 1 FROM asset_usernames WHERE username_id = ? LIMIT 1"
_HOLDS_USERNAMES = "SELECT 1 FROM usernames WHERE site_id = ? LIMIT 1"

#: What keeps a Site standing after its box filings have gone: anything somebody or something
#: else put on it. Each is one statement over one table, read only where the table exists (some
#: belong to features a library may never have switched on).
_ELSE_ON_IT: tuple[tuple[str, str], ...] = (
    ("usernames", "SELECT 1 FROM usernames WHERE site_id = ? AND name <> '' LIMIT 1"),
    ("sites", "SELECT 1 FROM sites WHERE parent_id = ? LIMIT 1"),
    ("site_tags", "SELECT 1 FROM site_tags WHERE site_id = ? LIMIT 1"),
    ("site_connections", "SELECT 1 FROM site_connections WHERE site_id = ? LIMIT 1"),
    (
        "site_user_state",
        "SELECT 1 FROM site_user_state WHERE site_id = ?"
        " AND (favorite <> 0 OR rating IS NOT NULL OR hidden <> 0 OR pinned <> 0) LIMIT 1",
    ),
    ("download_sites", "SELECT 1 FROM download_sites WHERE site_id = ? LIMIT 1"),
    ("downloads", "SELECT 1 FROM downloads WHERE site_id = ? LIMIT 1"),
    ("people_links", "SELECT 1 FROM people_links WHERE site_id = ? LIMIT 1"),
    ("watermark_reads", "SELECT 1 FROM watermark_reads WHERE site_id = ? LIMIT 1"),
    (
        "acl_grants",
        "SELECT 1 FROM acl_grants WHERE object_type = 'site' AND object_id = ? LIMIT 1",
    ),
)


async def _else_on_it(connection: Connection, site_id: str, nameless_id: str) -> bool:
    """Whether the Site holds anything once the box's filings are off it."""
    if await (await connection.execute(_HOLDS_FILES, (nameless_id,))).fetchone() is not None:
        return True
    for table, statement in _ELSE_ON_IT:
        if not await table_exists(connection, table):
            continue
        if await (await connection.execute(statement, (site_id,))).fetchone() is not None:
            return True
    return False


@dataclass(frozen=True, slots=True)
class Turned:
    """One Site's files moved to a username: what the History line and the counts say."""

    site_id: str
    site_name: str
    username_id: str
    handle: str
    host: str
    files: int
    #: Whether the Site itself went (nothing else was filed under it).
    site_gone: bool
    receipt_id: str


def _said(name: str, home: Home, files: int, gone: bool) -> tuple[str, str]:
    """The receipt's title and detail. Composed now, as every receipt's are."""
    counted = "1 file" if files == 1 else f"{files:,} files"
    title = f"{name} is a username on {home.site}, not a Site"
    detail = (
        f"{counted} moved to {home.handle} on {home.site}."
        + (f" The Site {name} was removed." if gone else f" The Site {name} keeps the rest.")
        + " Undo puts the Site back with its files."
    )
    return title, detail


async def _site_as_it_was(
    connection: Connection, site_id: str, nameless_id: str, site_row: Any
) -> dict[str, object]:
    """The receipt's copy of a Site about to go: its row, names, links and box links, and when its
    nameless username was made, so the Undo puts it back as it was."""
    nameless_row = await (await connection.execute(_NAMELESS_ROW, (nameless_id,))).fetchone()
    return {
        "site": None if site_row is None else [site_row[column] for column in _SITE_COLUMNS],
        "aliases": [
            list(row) for row in await connection.execute_fetchall(_ALIAS_ROWS, (site_id,))
        ],
        "links": [list(row) for row in await connection.execute_fetchall(_LINK_ROWS, (site_id,))],
        "box_links": [
            list(row) for row in await connection.execute_fetchall(_BOX_LINK_ROWS, (site_id,))
        ],
        "nameless_created_at": None if nameless_row is None else nameless_row["created_at"],
    }


async def turn_into_username(
    connection: Connection, signs: Signs, *, made: Made, actor: Actor
) -> Turned | None:
    """Move what a stash-box filed under this Site to her username, on the caller's connection.

    The box's filings move, and only those: a file somebody filed here by hand stays. The username
    is made where it is missing, said to be the one person called what the studio is called where
    there is exactly one, and given her page. The Site goes when nothing else is on it, and its
    row, names, links and box link are written into the receipt so the Undo puts it back as it
    was. None when the reading has nowhere to put her (no home) or the box filed nothing here.
    """
    home = signs.reading.home
    if home is None or signs.nameless_id is None:
        return None
    nameless_id = signs.nameless_id
    filings = list(await connection.execute_fetchall(_FILINGS, (nameless_id,)))
    if not filings:
        return None
    site_row = await (await connection.execute(_SITE_ROW, (signs.site_id,))).fetchone()
    host_was = await (await connection.execute(_SITE_NAMED, (home.site,))).fetchone()
    held = (
        None
        if host_was is None
        else await (
            await connection.execute(_USERNAME_FOLDED, (str(host_was["id"]), home.handle))
        ).fetchone()
    )
    host_id, username_id = await seed_site_username_on(
        connection,
        site=home.site,
        name=str(held["name"]) if held is not None else home.handle,
        made=made,
    )
    await connection.execute(_SET_URL, (home.url, username_id))
    address = site_home(home.url)
    if host_was is None and address is not None:
        await connection.execute(_ADD_HOME, (new_id(), host_id, address, int(time.time())))
    moved: list[str] = []
    for row in filings:
        # The box that filed it moves with the filing, so its lines still name the box.
        if await link_username_to_asset_on(
            connection,
            asset_id=str(row["asset_id"]),
            username_id=username_id,
            source=BOX_FILING,
            box_id=None if row["box_id"] is None else str(row["box_id"]),
        ):
            moved.append(str(row["asset_id"]))
    await connection.execute(_TAKE_BOX_FILINGS, (nameless_id,))
    person = list(await connection.execute_fetchall(_PERSON_NAMED, (signs.name,)))
    linked = None
    if len(person) == 1 and await link_username_to_person(
        connection, username_id=username_id, person_id=str(person[0]["id"])
    ):
        linked = str(person[0]["id"])
    gone = not await _else_on_it(connection, signs.site_id, nameless_id)
    payload: dict[str, object] = {
        "kind": "turned",
        "site_id": signs.site_id,
        "site_name": signs.name,
        "nameless_id": nameless_id,
        "filings": [
            [str(row["asset_id"]), row["source"], row["decided_at"], row["post_id"], row["box_id"]]
            for row in filings
        ],
        "username_id": username_id,
        "username_made": held is None,
        "host_id": host_id,
        "host_made": host_was is None,
        "moved": moved,
        "person_linked": linked,
    }
    if gone:
        payload.update(await _site_as_it_was(connection, signs.site_id, nameless_id, site_row))
        await connection.execute(_DELETE_USERNAME, (nameless_id,))
        await connection.execute(_DELETE_SITE, (signs.site_id,))
    await connection.execute(_REMEMBER, (signs.name, username_id, int(time.time())))
    title, detail = _said(signs.name, home, len(filings), gone)
    receipt_id = await record_event(
        connection,
        actor=actor,
        verb="moved",
        subject=[
            Subject(kind="site", id=signs.site_id, name=signs.name),
            Subject(kind="username", id=username_id, name=home.handle),
        ],
        object=Object(kind="site", id=host_id, name=home.site),
        payload=json.dumps(payload),
        receipt=Reversal(queue=QUEUE, title=title, detail=detail),
    )
    return Turned(
        site_id=signs.site_id,
        site_name=signs.name,
        username_id=username_id,
        handle=home.handle,
        host=home.site,
        files=len(filings),
        site_gone=gone,
        receipt_id=receipt_id,
    )


async def keep_as_site(connection: Connection, signs: Signs, *, actor: Actor) -> str:
    """Remember that this studio is a Site, so it is not asked about again. The receipt's id."""
    await connection.execute(_REMEMBER, (signs.name, None, int(time.time())))
    return await record_event(
        connection,
        actor=actor,
        verb="decided",
        subject=Subject(kind="site", id=signs.site_id, name=signs.name),
        payload=json.dumps({"kind": "kept", "site_name": signs.name}),
        receipt=Reversal(
            queue=QUEUE,
            title=f"{signs.name} stays a Site",
            detail="Undo asks about it again.",
        ),
    )


def _read(payload: str) -> dict[str, Any]:
    try:
        recorded: Any = json.loads(payload)
    except ValueError:
        return {}
    return recorded if isinstance(recorded, dict) else {}


async def put_back(connection: Connection, payload: str) -> bool:
    """Undo one answer, from what its receipt wrote down. True when anything went back.

    A move: the Site comes back with its own id, name, picture, names, links and box link where it
    went; the box's filings go back on its nameless row and come off the username, which goes too
    where the move made it and nothing else is on it; the person the move said it was is unsaid;
    the answer is forgotten. A Site of that name made again since is the one the files go back to.
    A Site kept: the answer is forgotten, so it is asked about again.
    """
    recorded = _read(payload)
    name = str(recorded.get("site_name") or "")
    if not name:
        return False
    await connection.execute(_FORGET, (name,))
    if recorded.get("kind") == "kept":
        return True
    if recorded.get("kind") != "turned":
        return False
    site_id = str(recorded.get("site_id") or "")
    site = recorded.get("site")
    if isinstance(site, list) and len(site) == len(_SITE_COLUMNS):
        await connection.execute(_PUT_SITE_BACK, tuple(site))
    again = await (await connection.execute(_SITE_NAMED, (name,))).fetchone()
    if again is None:
        return False
    site_id = str(again["id"])
    for row in recorded.get("aliases") or []:
        await connection.execute(_PUT_ALIAS_BACK, (row[0], site_id, row[1], row[2], row[3]))
    for row in recorded.get("links") or []:
        await connection.execute(_PUT_LINK_BACK, (row[0], site_id, row[1], row[2], row[3]))
    for row in recorded.get("box_links") or []:
        await connection.execute(_PUT_BOX_LINK_BACK, (site_id, row[0], row[1], row[2], row[3]))
    if isinstance(site, list) and len(site) == len(_SITE_COLUMNS) and str(site[0]) == site_id:
        # Its names and links coming back are not an edit of it: the moment it was last edited is
        # the one it had, which the triggers on those rows have just moved.
        await connection.execute(_KEEP_EDITED_AT, (site[_SITE_COLUMNS.index("edited_at")], site_id))
    nameless_id = str(recorded.get("nameless_id") or "")
    created = recorded.get("nameless_created_at") or int(time.time())
    await connection.execute(_PUT_NAMELESS_BACK, (nameless_id, site_id, created))
    held = await (await connection.execute(_NAMELESS, (site_id,))).fetchone()
    nameless_id = nameless_id if held is None else str(held["id"])
    # Four items in a receipt written before the filing named its box, five since.
    for asset_id, source, decided_at, post_id, *box in recorded.get("filings") or []:
        await connection.execute(
            _PUT_FILING_BACK,
            (asset_id, nameless_id, source, decided_at, post_id, box[0] if box else None),
        )
    username_id = str(recorded.get("username_id") or "")
    for asset_id in recorded.get("moved") or []:
        await connection.execute(_TAKE_FILING, (asset_id, username_id))
    if recorded.get("person_linked"):
        await connection.execute(_UNLINK_PERSON, (username_id, recorded["person_linked"]))
    if (
        recorded.get("username_made")
        and await (await connection.execute(_HOLDS_FILES, (username_id,))).fetchone() is None
    ):
        await connection.execute(_DELETE_USERNAME, (username_id,))
        host_id = str(recorded.get("host_id") or "")
        if (
            recorded.get("host_made")
            and await (await connection.execute(_HOLDS_USERNAMES, (host_id,))).fetchone() is None
        ):
            await connection.execute(_DELETE_SITE, (host_id,))
    return True


# --- What a lookup files a studio's next scene under --------------------------------------------

_ACCOUNT_FOR = """
SELECT s.name AS site, u.name AS handle, COALESCE(u.url, '') AS url
  FROM creator_studios c JOIN usernames u ON u.id = c.username_id JOIN sites s ON s.id = u.site_id
 WHERE c.name = ?
"""


async def account_for(database: Database, studio: str) -> dict[str, str] | None:
    """The username a studio's files go to, as an `accounts` entry, or None where it is a Site.

    What a stash-box answer naming this studio is filed by once the studio has been read as hers,
    whichever way the answer reached the writer (a lookup still running, a match kept from before
    the repair, a scene that does not credit her), so the Site is never made again from it.
    """
    if not studio.strip():
        return None
    row = await database.fetch_one(_ACCOUNT_FOR, (studio.strip(),))
    if row is None:
        return None
    return {"site": str(row["site"]), "handle": str(row["handle"]), "url": str(row["url"])}


# --- Catalog version 80 -------------------------------------------------------------------------


async def repair(connection: Connection) -> dict[str, int]:
    """Catalog version 80: every Site a stash-box made that the rule reads as a username, moved.

    The table first, then each Site a box made, read by the rule: a username is moved (one History
    line each, said by Sift as it updated, with its Undo); a question is left for Organize to ask;
    the rest stay Sites. The counts are logged. Nothing is recorded where the ledger's table is
    not there yet (a library that has never written a decision has no Site a box made either).
    """
    await connection.execute(CREATE_CREATOR_STUDIOS)
    counts = {"moved": 0, "files": 0, "kept": 0, "asked": 0, "removed": 0}
    if not await table_exists(connection, "workbench_decisions"):
        log.info("creator_studios.repaired", **counts)
        return counts
    for signs in await box_sites(connection):
        verdict = signs.reading.verdict
        if verdict is Verdict.USERNAME:
            turned = await turn_into_username(
                connection, signs, made=by_sift(VIA_UPDATE), actor=Actor.sift(VIA_UPDATE)
            )
            # A username reading has a home and the box's filings it was read from.
            if turned is not None:  # pragma: no branch
                counts["moved"] += 1
                counts["files"] += turned.files
                counts["removed"] += int(turned.site_gone)
                continue
        counts["asked" if verdict is Verdict.ASK else "kept"] += 1
    log.info("creator_studios.repaired", **counts)
    return counts
