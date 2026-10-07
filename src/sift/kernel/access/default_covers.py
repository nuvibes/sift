# SPDX-License-Identifier: AGPL-3.0-or-later
"""The picture a thing is drawn as when nobody chose one: its first file's, kept true by the database.

A person, a collection and a Photo Set each carry a cover, and one with none is drawn as a letter
beside files of its own. So the rule is: **an entity with no cover and at least one file that is
there to read wears the picture of the FIRST file filed under it**, by the filing's time, then by
the arrangement where the kind has one, then by the file's id. A still's own picture, a video's
still at the moment the still was cut by what the frame shows: the cover names the whole file, with
no moment and no window, which is exactly what the tile draws.

## Not a tag, and not a Site

A tag and a Site carry a cover too, and the rule does not reach them. Each is a NAMED thing: a tag
is a word somebody filed files under, a Site is where files came from, and the picture either wears
is one somebody chose, or its letter, or the Site's icon from the shipped pack. A file that happens
to be the first one filed under it says nothing about the word or the place, and on a wall of tags
or Sites the frames read as a wall of unrelated files. A network is a Site, so it is left out with
them. People, collections and Photo Sets keep the rule: what a person looks like, and what a set or
a collection holds, IS a picture of their files.

Catalog version 76 takes the rule's triggers off the two tables and gives back every cover the
rule had put on them (`take_back_tags_and_sites`). `sites.drawn_by_pack`, which only the rule
read, stays on the table unread (see the table's note).

## One exception, stored on the row

**A cover somebody CLEARED by hand stays empty.** The statement that writes a cover writes
`cover_cleared_at` beside it: a time where the cover was taken away, NULL where a picture was
chosen (`kernel/covers.py cleared_mark`). A column rather than a sentinel in a pointer, because
`cover_asset_id` references a file and cannot hold a word, and a made-up upload id would be served
as a picture by every reader that trusts the pointer. The mark goes the moment anybody chooses a
picture again, by the same statement. A tag and a Site keep the mark too: nothing here reads it
for them, but a stash-box filling a gap does (`standing`).

## A file nobody hides, first

A cover is drawn to everybody who sees the entity, and a file in somebody's Hidden is drawn to them
only once they unlock it. So "first" means the first file NOBODY keeps in Hidden, and a file in
Hidden is the cover only where every file under the entity is one. The pick follows Hidden as it
moves: triggers on the stored verdict (`viewer_assets`) ask again whenever a file goes in or out,
and a filing under an entity whose pick is a hidden file asks again too. That verdict belongs to a
component brought up after this one, so a new library's catalog makes the rule without it and the
boot check writes the triggers that read it (`_reads_hidden`).

## Why triggers, and not a call at each filing

A file is filed under a person by the folder pass, a download, a stash-box, a face, a swap, a Stash
library, a derived copy and a hand press, through more statements in more modules than any list of
callers would stay true to. Every one of them writes a membership row, so the rule is attached to
the ROW: an `AFTER INSERT` on each membership table, which a writer added tomorrow meets without
knowing to. The same for a cover taken away without a clear mark (its file deleted from the library,
which `ON DELETE SET NULL` does; a collection losing the item its cover was), and for a copy that
arrives or comes back, so a file filed before its copy was there is picked up when it is.

A move of FILING ROWS is not a filing: a person merge moves `asset_people` rows by UPDATE, which no
trigger here reads, and asks `assign_if_empty` itself once it has carried the going side's own
cover across.

**The pick is a default, not an act**, so nothing is recorded: History says what somebody did, and a
line for every person who gained their first file's picture would bury them. It also yields: the
file the rule picked is kept in `cover_by_default`, and while the cover is still exactly that file,
a better picture that only fills a gap (a stash-box's, see `kernel/covers.py SubjectCovers`) may
take its place. Any other write of the cover leaves the column naming a file the cover is not, which
is the same as no default, so no writer has to know the column exists.

## The whole picture, never a face

A person's cover MAY be a face cut out of a file (`cover_track_id`), and the rule never makes one:
what it gives is the whole first picture, and it writes the face column empty as it does, so a face
a file used to be drawn by cannot outlive it onto the next (the `face` flag on `_Covered`). A cover
nobody chose is the whole first picture of what the entity holds, so nothing unattended writes a
face, and catalog version 79 gives any face cover Sift made on its own back to the rule
(`faces_back_to_the_rule`).

Every statement is composed here from this module's own constants, the way `waiting` composes its
triggers; nothing that arrives at run time is put into any of them.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from dataclasses import dataclass

from sift.kernel.content.presence import HAS_A_PRESENT_COPY
from sift.kernel.db import Connection, Database, PointRead, point_read, register_schema_invariant
from sift.kernel.ledger import MOST_SUBJECTS, Actor, record_event
from sift.kernel.log import get_logger
from sift.kernel.migrations import table_exists
from sift.kernel.sql_splice import splice
from sift.kernel.vocabulary import VIA_UPDATE, Subject, SubjectKind

log = get_logger(__name__)


@dataclass(frozen=True)
class _Covered:
    """One kind of thing that carries a cover: the word the ledger and the records call it, its
    table, and whether its cover may be a face found in a file (a person's), which goes with the
    file."""

    word: SubjectKind
    table: str
    face: bool = False


_PERSON = _Covered("person", "people", face=True)
_SITE = _Covered("site", "sites")
_TAG = _Covered("tag", "tags")
_COLLECTION = _Covered("collection", "collections")
_PHOTO_SET = _Covered("photo_set", "photo_sets")
#: A SONG carries a cover and the rule does not reach it, for the reason a tag does not: it is a
#: named thing, and the picture of the first file that happens to carry it says nothing about the
#: music. One nobody chose is drawn as the music glyph the Music settings use.
_SONG = _Covered("song", "songs")

#: Every kind that carries a cover, which is what `standing` answers for. A tag, a Site and a song
#: are here and not among the kinds the rule reaches: see the module's note.
_COVERED: tuple[_Covered, ...] = (_PERSON, _SITE, _TAG, _COLLECTION, _PHOTO_SET, _SONG)


@dataclass(frozen=True)
class _Kind:
    """One kind of thing the rule reaches, and how a file is filed under it.

    `membership` is the FROM of a filing, aliasing the filing row `m`; `belongs` ties it to the
    entity named by `{{ENTITY}}`; `order` is what "first" means for the kind. `filed_on` is the
    table whose new rows are filings, `entity_of_new` the entity a new row of it names, and
    `entities_of_file` every entity the file `{{FILE}}` is filed under.
    """

    covered: _Covered
    membership: str
    belongs: str
    order: str
    filed_on: str
    entity_of_new: str
    entities_of_file: str

    @property
    def word(self) -> str:
        return self.covered.word

    @property
    def table(self) -> str:
        return self.covered.table


#: The kinds the rule reaches. NOT a tag and NOT a Site (a network included): each is a named thing
#: whose picture is chosen, or is its letter, or is the Site's icon from the shipped pack, and never
#: a file that happens to be the first one filed under it. See the module's note.
_KINDS: tuple[_Kind, ...] = (
    _Kind(
        covered=_PERSON,
        membership="asset_people m",
        belongs="m.person_id = {{ENTITY}}",
        order="m.decided_at",
        filed_on="asset_people",
        entity_of_new="NEW.person_id",
        entities_of_file="SELECT f.person_id FROM asset_people f WHERE f.asset_id = {{FILE}}",
    ),
    # Where several files went in at one moment, a collection takes the oldest file and a Photo
    # Set the first of its own order.
    _Kind(
        covered=_COLLECTION,
        membership="collection_items m",
        belongs="m.collection_id = {{ENTITY}}",
        order="m.added_at",
        filed_on="collection_items",
        entity_of_new="NEW.collection_id",
        entities_of_file=(
            "SELECT f.collection_id FROM collection_items f WHERE f.asset_id = {{FILE}}"
        ),
    ),
    _Kind(
        covered=_PHOTO_SET,
        membership="photo_set_items m",
        belongs="m.photo_set_id = {{ENTITY}}",
        order="m.added_at, m.position",
        filed_on="photo_set_items",
        entity_of_new="NEW.photo_set_id",
        entities_of_file=(
            "SELECT f.photo_set_id FROM photo_set_items f WHERE f.asset_id = {{FILE}}"
        ),
    ),
)

#: Every kind the rule reaches, by the word the ledger and the records call it. A username has no
#: cover column (it has no page to choose one on), so it is not one of them.
KINDS: tuple[str, ...] = tuple(kind.word for kind in _KINDS)

_BY_WORD = {kind.word: kind for kind in _KINDS}

#: What every trigger this module writes is called, so the boot check can tell its own.
_PREFIX = "default_cover_"


# --- the templates ----------------------------------------------------------------------------

#: The first file filed under the entity `{{ENTITY}}` that is still in the library and has a copy
#: there to read. Joined to `assets` so a file whose row is being deleted is never chosen: a cover
#: taken away by that delete's `SET NULL` is decided after the file's row has gone, while its
#: filings may not have followed it yet.
_FIRST = (
    "SELECT m.asset_id FROM {{MEMBERSHIP}} JOIN assets a ON a.id = m.asset_id"
    " WHERE {{BELONGS}} AND {{PRESENT}}{{KEPT}} ORDER BY {{ORDER}}, m.asset_id LIMIT 1"
)

#: A file nobody keeps in Hidden before any file somebody does: the first of the first kind, else
#: the first of all. Each reads the kind's index in its order and stops at the first that passes,
#: where ordering by the Hidden test worked it out for every file filed there.
_FIRST_SHOWN = "SELECT picked FROM (SELECT COALESCE(({{SHOWN}}), ({{ANY}})) AS picked) WHERE picked IS NOT NULL"

#: Each kind's filings in the order its first file is read in, so the pick is a seek.
INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_asset_people_first"
    " ON asset_people(person_id, decided_at, asset_id)",
    "CREATE INDEX IF NOT EXISTS ix_collection_items_first"
    " ON collection_items(collection_id, added_at, asset_id)",
    "CREATE INDEX IF NOT EXISTS ix_psi_first"
    " ON photo_set_items(photo_set_id, added_at, position, asset_id)",
)

#: Whether the file `{{FILE}}` is in somebody's Hidden: concealed for a user, so it is drawn to
#: them only once they unlock it. A cover is drawn to everybody who sees the entity, so a file
#: like that is its cover only where every file under it is one. One probe per user, by the
#: stored verdict's own key.
_IN_HIDDEN = (
    "EXISTS (SELECT 1 FROM users u JOIN viewer_assets v"
    " ON v.user_id = u.id AND v.asset_id = {{FILE}} AND v.concealed = 1)"
)

#: An entity row `{{ROW}}` with no cover of either kind and no clear mark.
_EMPTY = (
    "{{ROW}}.cover_asset_id IS NULL AND {{ROW}}.cover_upload_id IS NULL"
    " AND {{ROW}}.cover_cleared_at IS NULL"
)

#: The entity a new filing names is empty: one read of its row, the guard every filing pays.
_HAS_ROOM = "EXISTS (SELECT 1 FROM {{TABLE}} e WHERE e.id = {{ENTITY}} AND {{EMPTY}})"

#: Give every entity `{{WHO}}` picks out that is empty its first file. Every pointer is written, so
#: a moment, a window or a face left behind by a file that went cannot outlive it onto the new one.
_ASSIGN = (
    "UPDATE {{TABLE}} SET cover_asset_id = ({{FIRST}}), cover_by_default = ({{FIRST}}),"
    " cover_at_ms = NULL, cover_frame = NULL{{FACE}}"
    " WHERE {{WHO}} AND {{EMPTY}} AND EXISTS ({{FIRST}})"
)

#: Let go of the rule's pick when its file stops being filed under the entities `{{WHO}}` picks out
#: (the row `OLD` just deleted): `*_lost` then gives each the next first file, or the letter when
#: none is left. Only the rule's own pick follows the files; a picture somebody chose stays where
#: they put it.
_RELEASE_DEFAULT = (
    "UPDATE {{TABLE}} SET cover_asset_id = NULL, cover_by_default = NULL"
    " WHERE {{WHO}} AND {{TABLE}}.cover_asset_id = OLD.asset_id"
    " AND {{IS_DEFAULT}}"
)

#: The cover is still exactly the file the rule picked: no moment, no window, no face, no upload.
_IS_DEFAULT = (
    "{{ROW}}.cover_by_default IS NOT NULL AND {{ROW}}.cover_asset_id = {{ROW}}.cover_by_default"
    " AND {{ROW}}.cover_upload_id IS NULL AND {{ROW}}.cover_at_ms IS NULL"
    " AND {{ROW}}.cover_frame IS NULL{{FACE}}"
)

_TRIGGER = (
    "CREATE TRIGGER IF NOT EXISTS {{NAME}} AFTER {{EVENT}} ON {{ON}}{{WHEN}} BEGIN {{BODY}}; END"
)
_DROP_TRIGGER = "DROP TRIGGER IF EXISTS {{NAME}}"

#: This module's triggers as the database holds them. The prefix is a parameter, so this is a
#: constant.
_TRIGGERS_PRESENT = (
    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND substr(name, 1, ?) = ?"
)


# --- composing them ---------------------------------------------------------------------------


def _first(kind: _Kind, *, hidden: bool = True) -> str:
    """The kind's first-file subquery, correlated on the entity row of the UPDATE it sits in.
    Without `hidden` for a catalog brought up before the stored verdict it reads exists, where no
    file can be in anybody's Hidden yet."""
    pieces = {
        "MEMBERSHIP": kind.membership,
        "BELONGS": splice(kind.belongs, ENTITY=kind.table + ".id"),
        "PRESENT": HAS_A_PRESENT_COPY,
        "ORDER": kind.order,
    }
    first = splice(_FIRST, KEPT="", **pieces)
    if not hidden:
        return first
    kept = " AND NOT " + splice(_IN_HIDDEN, FILE="m.asset_id")
    return splice(_FIRST_SHOWN, SHOWN=splice(_FIRST, KEPT=kept, **pieces), ANY=first)


def _empty(row: str) -> str:
    return splice(_EMPTY, ROW=row)


def _assign(kind: _Kind, who: str, *, hidden: bool = True) -> str:
    first = _first(kind, hidden=hidden)
    return splice(
        _ASSIGN,
        TABLE=kind.table,
        FIRST=first,
        FACE=", cover_track_id = NULL" if kind.covered.face else "",
        WHO=who,
        EMPTY=_empty(kind.table),
    )


def _is_default(covered: _Covered, row: str) -> str:
    return splice(
        _IS_DEFAULT, ROW=row, FACE=f" AND {row}.cover_track_id IS NULL" if covered.face else ""
    )


def _trigger(name: str, event: str, on: str, body: tuple[str, ...], when: str = "") -> str:
    return splice(
        _TRIGGER,
        NAME=name,
        EVENT=event,
        ON=on,
        WHEN=(" WHEN " + when) if when else "",
        BODY="; ".join(body),
    )


@dataclass(frozen=True)
class _Statements:
    """Everything the rule needs, composed once."""

    triggers: dict[str, str]
    drop_triggers: tuple[str, ...]
    #: Per kind: the one entity bound as `?`, and every entity in one go.
    assign_one: dict[str, str]
    assign_all: dict[str, str]


@functools.cache
def _kind_triggers(kind: _Kind, hidden: bool) -> dict[str, str]:
    """The triggers that keep one kind's default covers: a filing filling a gap, a cover lost, a
    filing taken off, and, where the stored verdict is there, a filing under a hidden pick."""
    triggers: dict[str, str] = {}
    stem = _PREFIX + kind.word
    # A file filed under it: the gap is filled if there is one. The guard is one read of the
    # entity's row, so a filing under something that has a cover costs nothing else.
    triggers[stem + "_filed"] = _trigger(
        stem + "_filed",
        "INSERT",
        kind.filed_on,
        (_assign(kind, f"{kind.table}.id = {kind.entity_of_new}", hidden=hidden),),
        when=splice(_HAS_ROOM, TABLE=kind.table, ENTITY=kind.entity_of_new, EMPTY=_empty("e")),
    )
    # Its cover taken away by anything but a person clearing it: the file deleted from the
    # library (`ON DELETE SET NULL`), or a membership that no longer holds it.
    triggers[stem + "_lost"] = _trigger(
        stem + "_lost",
        "UPDATE OF cover_asset_id, cover_upload_id",
        kind.table,
        (_assign(kind, f"{kind.table}.id = NEW.id", hidden=hidden),),
        when=(
            "NEW.cover_asset_id IS NULL AND NEW.cover_upload_id IS NULL"
            " AND NEW.cover_cleared_at IS NULL"
            " AND (OLD.cover_asset_id IS NOT NULL OR OLD.cover_upload_id IS NOT NULL)"
        ),
    )
    # A filing taken off it: the file taken off a person, out of a collection, a filing undone.
    # The rule's pick goes with the file, so a cover never shows a file the entity no longer
    # holds.
    entity_of_old = kind.entity_of_new.replace("NEW.", "OLD.")
    triggers[stem + "_unfiled"] = _trigger(
        stem + "_unfiled",
        "DELETE",
        kind.filed_on,
        (
            splice(
                _RELEASE_DEFAULT,
                TABLE=kind.table,
                WHO=f"{kind.table}.id = {entity_of_old}",
                IS_DEFAULT=_is_default(kind.covered, kind.table),
            ),
        ),
    )
    if hidden:
        # A file filed under something whose cover is the rule's pick of a file in Hidden: the
        # pick is made again, so a file nobody hides takes its place. The guard is one read of
        # the entity's row and one probe per user.
        triggers[stem + "_filed_seen"] = _trigger(
            stem + "_filed_seen",
            "INSERT",
            kind.filed_on,
            (_pick_again(kind, f"{kind.table}.id = {kind.entity_of_new}"),),
            when=splice(
                _PICKED_IN_HIDDEN,
                TABLE=kind.table,
                ENTITY=kind.entity_of_new,
                IS_DEFAULT=_is_default(kind.covered, "e"),
                HIDDEN=splice(_IN_HIDDEN, FILE="e.cover_asset_id"),
            ),
        )
    return triggers


def _hidden_triggers(picked_again: Callable[[str], tuple[str, ...]]) -> dict[str, str]:
    """The triggers on the stored verdict: a file going into Hidden, out of it, or out of sight."""
    triggers: dict[str, str] = {}
    # A file going into somebody's Hidden, or out of it, or out of their sight altogether:
    # whatever it is filed under whose cover is the rule's pick of a file in Hidden is picked
    # again, so a cover is a file nobody hides wherever one is filed under it. Only a row whose
    # concealment moved fires anything.
    triggers[_PREFIX + "hidden_moved"] = _trigger(
        _PREFIX + "hidden_moved",
        "UPDATE OF concealed",
        "viewer_assets",
        picked_again("NEW.asset_id"),
        when="NEW.concealed IS NOT OLD.concealed",
    )
    triggers[_PREFIX + "hidden_new"] = _trigger(
        _PREFIX + "hidden_new",
        "INSERT",
        "viewer_assets",
        picked_again("NEW.asset_id"),
        when="NEW.concealed = 1",
    )
    triggers[_PREFIX + "hidden_gone"] = _trigger(
        _PREFIX + "hidden_gone",
        "DELETE",
        "viewer_assets",
        picked_again("OLD.asset_id"),
        when="OLD.concealed = 1",
    )
    return triggers


def _statements(hidden: bool = True) -> _Statements:
    """Everything, composed to read who keeps a file in Hidden, or without that where the stored
    verdict is not there yet (`_reads_hidden`)."""
    triggers: dict[str, str] = {}
    for kind in _KINDS:
        triggers.update(_kind_triggers(kind, hidden))

    def arrived(file: str) -> tuple[str, ...]:
        filled = tuple(
            _assign(
                kind,
                f"{kind.table}.id IN ({splice(kind.entities_of_file, FILE=file)})",
                hidden=hidden,
            )
            for kind in _KINDS
        )
        return filled + (picked_again(file) if hidden else ())

    def picked_again(file: str) -> tuple[str, ...]:
        return tuple(
            _pick_again(kind, f"{kind.table}.id IN ({splice(kind.entities_of_file, FILE=file)})")
            for kind in _KINDS
        )

    # A copy arriving, or coming back, or moving to another file: whatever that file is filed under
    # may have been waiting for a file there to read. A scan re-stamping a present copy fires
    # nothing.
    triggers[_PREFIX + "copy_arrived"] = _trigger(
        _PREFIX + "copy_arrived",
        "INSERT",
        "asset_locations",
        arrived("NEW.asset_id"),
        when="NEW.status = 'present'",
    )
    triggers[_PREFIX + "copy_back"] = _trigger(
        _PREFIX + "copy_back",
        "UPDATE OF status, asset_id",
        "asset_locations",
        arrived("NEW.asset_id"),
        when=(
            "NEW.status = 'present'"
            " AND (OLD.status IS NOT 'present' OR NEW.asset_id IS NOT OLD.asset_id)"
        ),
    )
    if hidden:
        triggers.update(_hidden_triggers(picked_again))
    return _Statements(
        triggers=triggers,
        drop_triggers=tuple(splice(_DROP_TRIGGER, NAME=name) for name in triggers),
        assign_one={
            kind.word: _assign(kind, f"{kind.table}.id = ?1", hidden=hidden) for kind in _KINDS
        },
        assign_all={kind.word: _assign(kind, "1 = 1", hidden=hidden) for kind in _KINDS},
    )


async def _reads_hidden(connection: Connection) -> bool:
    """Whether the stored verdict of who sees which file is there to read. It belongs to a
    component brought up after this one, so a new library's catalog is made without it, and a
    statement or trigger naming a table that is not there fails every write it reaches; the boot
    check then writes the triggers that read it (`keep_true`)."""
    return await table_exists(connection, "viewer_assets")


def triggers() -> dict[str, str]:
    """The triggers the rule keeps, by name, as this build writes them."""
    return dict(_statements().triggers)


def drop_triggers() -> tuple[str, ...]:
    """The statements that take every one of those triggers away: what a table rebuild does to
    them, and what a test stands up to be a library from before the rule."""
    return _statements().drop_triggers


# --- running them -------------------------------------------------------------------------------


async def assign_if_empty(connection: Connection, kind: str, entity_id: str) -> bool:
    """Give one entity its first file's picture where it has no cover and no clear mark. True
    when one landed.

    For the one path the triggers leave to their callers, a MERGE (see the module's note), and for
    anything that wants the rule asked on purpose. On the caller's connection, inside its write.
    """
    built = _statements(await _reads_hidden(connection))
    cursor = await connection.execute(built.assign_one[_BY_WORD[kind].word], (entity_id,))
    return bool(cursor.rowcount)


async def fill_every_empty(connection: Connection) -> dict[str, int]:
    """Give every empty entity its first file's picture, per kind. How many landed, by kind.

    The catalog step's backfill and the boot repair's. Idempotent: an entity it gave a picture is
    no longer empty, so a second run gives nothing.
    """
    every = _statements(await _reads_hidden(connection)).assign_all
    filled: dict[str, int] = {}
    for kind in _KINDS:
        cursor = await connection.execute(every[kind.word])
        filled[kind.word] = max(cursor.rowcount, 0)
    return filled


async def _make_triggers(connection: Connection) -> None:
    for ddl in _statements(await _reads_hidden(connection)).triggers.values():
        await connection.execute(ddl)


async def start(connection: Connection) -> dict[str, int]:
    """The catalog step that brings the rule in: the triggers, and every entity with no cover, no
    clear mark and a file given its first file's picture. Answers how many landed per kind, which
    the step logs."""
    await index_the_picks(connection)
    await _make_triggers(connection)
    filled = await fill_every_empty(connection)
    log.info("covers.default.filled", **filled)
    return filled


async def index_the_picks(connection: Connection) -> None:
    """Catalog version 92: each kind's filings indexed in its pick's order (`INDEXES`)."""
    for statement in INDEXES:
        await connection.execute(statement)


# --- giving back what the rule put on a tag or a Site ------------------------------------------

#: The triggers the rule kept on a tag and a Site before catalog version 76, by the names they had.
#: Written out rather than composed, because nothing composes them any more; a name here that a
#: library never had costs one `DROP ... IF EXISTS`.
_RETIRED_TRIGGERS: tuple[str, ...] = tuple(
    _PREFIX + name
    for name in (
        "site_filed",
        "site_lost",
        "site_unfiled",
        "site_username_moved",
        "site_deleted",
        "site_filed_within",
        "site_unfiled_within",
        "site_moved",
        "site_unpacked",
        "tag_filed",
        "tag_lost",
        "tag_unfiled",
    )
)

#: Take away a cover that is still only the rule's pick, and answer each row it took one from, by
#: id and name, for the History line. A cover somebody chose, a moment or a window somebody set, a
#: stash-box's picture: none passes `{{IS_DEFAULT}}`, so none is touched. No clear mark is written:
#: nobody took the picture away, so a picture that only fills a gap may still land.
_GIVE_BACK = (
    "UPDATE {{TABLE}} SET cover_asset_id = NULL, cover_by_default = NULL"
    " WHERE {{IS_DEFAULT}} RETURNING id, name"
)
#: And the rule's note of a pick the cover has since moved away from, which no reader would ever
#: take for a default again, so the column says nothing on either table from here on.
_FORGET_THE_PICK = "UPDATE {{TABLE}} SET cover_by_default = NULL WHERE cover_by_default IS NOT NULL"

_GIVEN_BACK_FROM: tuple[_Covered, ...] = (_TAG, _SITE)
_GIVE_BACK_STATEMENTS: tuple[tuple[_Covered, str, str], ...] = tuple(
    (
        covered,
        splice(_GIVE_BACK, TABLE=covered.table, IS_DEFAULT=_is_default(covered, covered.table)),
        splice(_FORGET_THE_PICK, TABLE=covered.table),
    )
    for covered in _GIVEN_BACK_FROM
)

#: What a cover taken away says on its History line: the shape `kernel/covers.cover_payload` writes
#: for a cover with no picture ("removed its cover"). Written here because `kernel/covers` reads
#: `standing` from this module and cannot be read from it; a test holds the two to one shape.
COVER_TAKEN_AWAY = json.dumps({"cover": "none"})


async def take_back_tags_and_sites(connection: Connection) -> dict[str, int]:
    """Catalog version 76: the rule leaves tags and Sites, and gives back what it gave them.

    The triggers go first, so taking a cover away does not fill it again by the rule it is leaving.
    Every tag and Site whose cover is still exactly the rule's pick goes back to its letter, or to
    its icon where the shipped pack draws the Site (the cover route answers an empty Site with it).
    A cover anybody chose is left where it is. How many of each, logged, and said on History by
    Sift as it updated, on each one's own History and in the library's, a line per few of them
    (`MOST_SUBJECTS`), since the ledger names at most that many things in one line.
    """
    for name in _RETIRED_TRIGGERS:
        await connection.execute(splice(_DROP_TRIGGER, NAME=name))
    # The two that sit on a file's copy wrote into every kind in one go, the retired two included,
    # so they are made again from this build's kinds.
    for statement in drop_triggers():
        await connection.execute(statement)
    await _make_triggers(connection)
    recorded = await table_exists(connection, "workbench_decisions")
    taken: dict[str, int] = {}
    for covered, give_back, forget in _GIVE_BACK_STATEMENTS:
        rows = list(await connection.execute_fetchall(give_back))
        await connection.execute(forget)
        taken[covered.word] = len(rows)
        if not recorded:
            continue
        subjects = [Subject(covered.word, str(row[0]), str(row[1])) for row in rows]
        for at in range(0, len(subjects), MOST_SUBJECTS):
            await record_event(
                connection,
                actor=Actor.sift(VIA_UPDATE),
                verb="edited",
                subject=subjects[at : at + MOST_SUBJECTS],
                payload=COVER_TAKEN_AWAY,
            )
    log.info("covers.default.taken_back", tags=taken["tag"], sites=taken["site"])
    return taken


# --- giving back the faces Sift made covers of --------------------------------------------------

#: Take away every person's cover that is a face Sift cut out of a file, and answer each person it
#: took one from. Only Sift ever wrote one: choosing a picture, uploading one and reframing one each
#: write the face column empty in the same statement (`people/service.py _SET_PERSON_COVER`), and a
#: merge carries across the going side's own. No clear mark is written, because nobody took the
#: picture away, so the rule's `_lost` trigger gives each one the whole first picture filed under
#: them in the same statement, or the letter where no file of theirs is there to read.
_FACES_BACK = (
    "UPDATE people SET cover_asset_id = NULL, cover_track_id = NULL, cover_at_ms = NULL,"
    " cover_frame = NULL, cover_by_default = NULL"
    " WHERE cover_track_id IS NOT NULL AND cover_upload_id IS NULL RETURNING id"
)

#: How many of the people given back now wear a picture, for the log.
_PICTURED = (
    "SELECT COUNT(*) FROM people"
    " WHERE id IN (SELECT value FROM json_each(?)) AND cover_asset_id IS NOT NULL"
)


async def faces_back_to_the_rule(connection: Connection) -> dict[str, int]:
    """Catalog version 79: every face Sift made somebody's cover goes back to the whole first
    picture filed under them. How many faces went, and how many of those people wear a picture now
    (the rest have no file there to read, and wear their letter until one arrives).

    A default, not an act, so nothing is recorded (see the module's note): the log says how many.
    """
    rows = list(await connection.execute_fetchall(_FACES_BACK))
    ids = json.dumps([str(row[0]) for row in rows])
    counted = list(await connection.execute_fetchall(_PICTURED, (ids,)))
    given: dict[str, int] = {"faces": len(rows), "pictured": int(counted[0][0]) if counted else 0}
    log.info("covers.default.faces_given_back", **given)
    return given


# --- a cover in Hidden while a file nobody hides is there ---------------------------------------

#: Give the rule's pick again to every entity `{{WHO}}` picks out whose cover is still only that
#: pick, is a file in somebody's Hidden, and is not the file the rule picks now: which, the rule
#: preferring a file nobody hides, means one of those is filed under it.
_PICK_AGAIN = (
    "UPDATE {{TABLE}} SET cover_asset_id = ({{FIRST}}), cover_by_default = ({{FIRST}}),"
    " cover_at_ms = NULL, cover_frame = NULL{{FACE}}"
    " WHERE {{WHO}} AND {{IS_DEFAULT}} AND {{HIDDEN}}"
    " AND ({{FIRST}}) IS NOT {{TABLE}}.cover_asset_id{{TAIL}}"
)

#: The entity a new filing names wears the rule's pick of a file in Hidden: the guard on a filing.
_PICKED_IN_HIDDEN = (
    "EXISTS (SELECT 1 FROM {{TABLE}} e WHERE e.id = {{ENTITY}} AND {{IS_DEFAULT}} AND {{HIDDEN}})"
)


def _pick_again(kind: _Kind, who: str, tail: str = "") -> str:
    return splice(
        _PICK_AGAIN,
        TAIL=tail,
        TABLE=kind.table,
        FIRST=_first(kind),
        FACE=", cover_track_id = NULL" if kind.covered.face else "",
        WHO=who,
        IS_DEFAULT=_is_default(kind.covered, kind.table),
        HIDDEN=splice(_IN_HIDDEN, FILE=kind.table + ".cover_asset_id"),
    )


async def out_of_hidden(connection: Connection) -> dict[str, int]:
    """Catalog version 89: every cover the rule picked that is a file in somebody's Hidden, while a
    file nobody hides is filed under the same entity, is picked again by the rule as it stands, so
    it is that file. How many moved, per kind, logged.

    The triggers are written again first, so every pick from here on prefers a file nobody hides.
    A default, not an act, so nothing is recorded (see the module's note). Safe to run twice: a
    cover it moved is the rule's pick, and nothing moves it again.
    """
    for statement in drop_triggers():
        await connection.execute(statement)
    await _make_triggers(connection)
    moved: dict[str, int] = {kind.word: 0 for kind in _KINDS}
    if not await _reads_hidden(connection):
        return moved
    for kind in _KINDS:
        picked = _pick_again(kind, "1 = 1", tail=" RETURNING id")
        moved[kind.word] = len(list(await connection.execute_fetchall(picked)))
    log.info("covers.default.out_of_hidden", **moved)
    return moved


def _normal(ddl: str) -> str:
    """A trigger's text without the IF NOT EXISTS the engine drops, spacing folded."""
    return " ".join(ddl.replace("IF NOT EXISTS ", "").split())


async def keep_true(connection: Connection) -> None:
    """Every boot: the triggers say what this build says.

    Three of the tables the triggers sit on belong to other components (`asset_locations` to the
    content component), and a rebuild of a table takes its triggers with it, silently. A trigger
    missing means gaps may have opened while it was gone, so the repair fills them as well. Cheap
    when nothing is wrong: one read of the schema.

    Guarded on the tables, because the schema tests bring a catalog up with no content under it.
    """
    for table in ("people", "asset_locations", "assets"):
        if not await table_exists(connection, table):
            return
    hidden = await _reads_hidden(connection)
    built = _statements(hidden)
    rows = await connection.execute_fetchall(_TRIGGERS_PRESENT, (len(_PREFIX), _PREFIX))
    present = {str(row[0]): _normal(str(row[1])) for row in rows}
    wanted = {name: _normal(ddl) for name, ddl in built.triggers.items()}
    unknown = sorted(name for name in present if name not in wanted)
    if unknown:
        log.error("covers.default.unknown_triggers", names=unknown)
    repaired = sorted(name for name in wanted if present.get(name) != wanted[name])
    # A catalog made before the stored verdict was there (`_reads_hidden`) is not a fault: its
    # triggers are this build's in every other word, and are written again to read it.
    before = {name: _normal(ddl) for name, ddl in _statements(hidden=False).triggers.items()}
    if repaired and hidden and present == before:
        log.info("covers.default.triggers_read_hidden")
    elif repaired:
        log.warning("covers.default.triggers_repaired", repaired=repaired)
    if repaired:
        for statement in built.drop_triggers:
            await connection.execute(statement)
        await _make_triggers(connection)
        await fill_every_empty(connection)


register_schema_invariant("catalog_default_covers", keep_true)


# --- what a gap-filler asks -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Standing:
    """Where an entity's cover stands for a writer that only fills a gap.

    `cleared`: a person took the cover away, and nothing unattended may put one back. `by_default`:
    the cover is the rule's pick and nothing more, so a better picture may take its place.
    """

    cleared: bool = False
    by_default: bool = False


def _standing_read(covered: _Covered) -> PointRead:
    return point_read(
        f"default_covers.standing.{covered.word}",
        splice(
            "SELECT e.cover_cleared_at IS NOT NULL AS cleared, ({{IS_DEFAULT}}) AS by_default"
            " FROM {{TABLE}} e WHERE e.id = ?",
            IS_DEFAULT=_is_default(covered, "e"),
            TABLE=covered.table,
        ),
    )


#: Every kind that carries a cover, the two the rule leaves included: a Site whose cover somebody
#: cleared is one a stash-box must leave empty, whether or not the rule ever reached it.
_STANDING: dict[str, PointRead] = {covered.word: _standing_read(covered) for covered in _COVERED}


async def standing(database: Database, kind: str, entity_id: str) -> Standing:
    """Where one entity's cover stands. The empty answer for a kind with no cover or no such row."""
    read = _STANDING.get(kind)
    if read is None:
        return Standing()
    row = await database.fetch_one(read, (entity_id,))
    if row is None:
        return Standing()
    return Standing(cleared=bool(row["cleared"]), by_default=bool(row["by_default"]))
