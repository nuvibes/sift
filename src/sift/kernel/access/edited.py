# SPDX-License-Identifier: AGPL-3.0-or-later
"""When a thing was last EDITED, kept by the database, which is what "Recently edited" orders by.

An edit is a change to what a thing IS: its name, its fields, its cover, its other names, its
links, its tags. Made by a person or by one of Sift's passes, it is the same kind of act History
records. It is NOT a look at the thing, not a sitting with it, not a viewer's opinion of it (the
heart, the pin, the stars), and not a file arriving under it: a scan filing a thousand files under
a person has changed what is filed under her and nothing about her.

## Where the moment lives

`edited_at` on each table that holds a record (people, Sites, tags, collections, Photo Sets,
usernames, loops), in whole seconds, NULL until the first edit. A file's record lives on `assets`,
which belongs to the content component, so a file's moment is a row of `asset_edits` instead, made
at its first edit: a column there would be a second component's schema moved for this one's
purpose, and a sparse table costs nothing for the files nobody has touched.

## Why triggers, and not a write at each caller

A person's fields are written by the record form, a stash-box, a Stash library, a swap, a merge and
a folder rule, through more statements in more modules than any list of callers would stay true
to; a tag is put on a person, a Site, a collection and a file by more writers still. The moment is
attached to the ROW instead: an `AFTER UPDATE OF <the record's columns>` on each record table, and
an insert or delete on each table a record's list lives in (its other names, its links, its tags).
A writer added tomorrow meets them without knowing to. `keep_true` writes them again at every
boot, because some of the tables they sit on (`assets` and the visibility-rebuilt membership
tables) belong to other components, and a table rebuilt the SQLite way takes its triggers with it,
silently.

## What is not an edit, and how each is told apart

- **A column that is not what the thing is**: the sort key (it moves with the name), who made the
  row, "Do not enrich" and "Do not swap" (refusals about what may leave the machine), the rule's
  own default cover. `NOT_AN_EDIT` lists them per table and a test holds every column to one list
  or the other, so a column added later is placed on purpose.
- **A viewer's opinion**: the heart, the pin, the hidden flag and the stars all sit in that
  viewer's `*_user_state` row (`OPINIONS`), and none of them is what the thing is. The stars
  are an opinion exactly as the heart is, and one user's rating would otherwise move the thing up
  every other user's list, so no trigger here watches those tables.
- **Sift's default cover**: the rule writes `cover_by_default` beside the cover it picks, and no
  other writer touches that column, so a cover write that moved it is the rule's and counts for
  nothing (see `default_covers`, "the pick is a default, not an act").
- **A cascade from something deleted**: a file removed from the library sets a cover to NULL; a
  tag deleted takes its rows off every person; a person deleted takes her name off her usernames.
  Each is the OTHER thing's deletion, which History records once, on that thing. So a pointer
  cleared counts only while what it pointed at still exists, and a link row's delete counts only
  while both its ends do (SQLite has removed the parent row by the time a cascade's trigger runs).
- **An idempotent re-filing**: `INSERT OR IGNORE` of a row that is there fires no AFTER trigger,
  and a changed value is compared with `IS NOT`, so a save that wrote what was already there
  moves nothing.

## The order

Most recently edited first, and a thing never edited behind every thing that was, by when it was
made (its id, a ULID, which is creation order whatever the clock did). Creation is deliberately
not an edit: Sift makes usernames and Sites by the thousand as files arrive, and counting that
would bury the few somebody changed by hand under everything a scan made that morning.

## The first moment, for a library made before this

`backfill` reads each thing's newest record edit off the ledger (History's own rows), once, where
the table is there to read; a rating is an opinion and is not read. What the ledger never recorded
stays unknown and sorts as never edited, which is the honest answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from sift.kernel.db import Connection, register_schema_invariant
from sift.kernel.log import get_logger
from sift.kernel.migrations import table_exists
from sift.kernel.sql_splice import splice

log = get_logger(__name__)

#: A file's last edit. Sparse: a row appears at the file's first edit and goes with the file.
CREATE_ASSET_EDITS = """
CREATE TABLE IF NOT EXISTS asset_edits (
  asset_id  TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  edited_at INTEGER NOT NULL
) WITHOUT ROWID
"""

#: The record tables that carry `edited_at` themselves.
EDITED_TABLES: tuple[str, ...] = (
    "people",
    "sites",
    "tags",
    "collections",
    "photo_sets",
    "songs",
    "usernames",
    "loops",
)

#: Every trigger here is named with this, so `keep_true` can find its own and only its own.
_PREFIX = "edited_"

#: The cover columns, which count only when the default-cover rule did not write them.
_COVER = ("cover_asset_id", "cover_at_ms", "cover_upload_id", "cover_frame", "cover_cleared_at")


@dataclass(frozen=True)
class _Record:
    """One table whose record columns live on it.

    `columns` are the edits; `pointers` the ones among them a cascade can clear, with the table
    each points into; `covered` whether the cover columns are here too (plus `cover_track_id` on a
    person, the face her cover may be).
    """

    table: str
    columns: tuple[str, ...]
    pointers: tuple[tuple[str, str], ...] = ()
    covered: bool = False
    face: bool = False


_RECORDS: tuple[_Record, ...] = (
    _Record(
        "people",
        (
            "name",
            "notes",
            "disambiguation",
            "gender",
            "birth_date",
            "country",
            "ethnicity",
            "eye_color",
            "hair_color",
            "height_cm",
            "measurements",
            "breast_type",
            "career_start_year",
            "career_end_year",
            "tattoos",
            "piercings",
            "pmv_creator",
        ),
        covered=True,
        face=True,
    ),
    _Record("sites", ("name", "notes"), pointers=(("parent_id", "sites"),), covered=True),
    _Record(
        "tags",
        ("name", "description", "category"),
        pointers=(("parent_id", "tags"),),
        covered=True,
    ),
    _Record("collections", ("name",), covered=True),
    _Record("photo_sets", ("name", "notes"), covered=True),
    _Record("songs", ("name", "notes"), covered=True),
    _Record(
        "usernames",
        ("name", "display_name", "url", "number"),
        pointers=(("person_id", "people"), ("site_id", "sites")),
    ),
    _Record("loops", ("name", "start_ms", "end_ms")),
)

#: The columns of each record table that are NOT an edit, each group with its reason. Every column
#: of every table above is in exactly one of the two lists (`test_edited`), so a column added
#: later is placed on purpose rather than left out by accident.
NOT_AN_EDIT: dict[str, tuple[str, ...]] = {
    # The row's own identity and the moments kept about it; the sort key, which moves with the
    # name; who made the row; the two refusals; the default-cover rule's own pick.
    "people": (
        "id",
        "created_at",
        "edited_at",
        "name_sort",
        "created_by_box_id",
        "created_by_kind",
        "created_by_user_id",
        "created_by_via",
        "keep_local",
        "keep_from_swaps",
        "cover_by_default",
    ),
    # `kind` and `drawn_by_pack` are read by nothing (see the table's note).
    "sites": (
        "id",
        "created_at",
        "edited_at",
        "name_sort",
        "created_by_box_id",
        "created_by_kind",
        "created_by_user_id",
        "created_by_via",
        "keep_local",
        "keep_from_swaps",
        "cover_by_default",
        "kind",
        "drawn_by_pack",
    ),
    "tags": (
        "id",
        "created_at",
        "edited_at",
        "name_sort",
        "created_by_box_id",
        "created_by_kind",
        "created_by_user_id",
        "created_by_via",
        "created_by_act",
        "keep_local",
        "keep_from_swaps",
        "cover_by_default",
    ),
    # Whose collection it is, which is not what it is.
    "collections": (
        "id",
        "created_at",
        "edited_at",
        "name_sort",
        "owner_id",
        "created_by_kind",
        "created_by_user_id",
        "created_by_via",
        "cover_by_default",
    ),
    # Where a set came from (a folder, an archive, a page) is a fact about how it was made.
    "photo_sets": (
        "id",
        "created_at",
        "edited_at",
        "name_sort",
        "origin",
        "origin_url",
        "folder_id",
        "archive_root_id",
        "archive_rel_path",
        "created_by_kind",
        "created_by_user_id",
        "created_by_via",
        "cover_by_default",
    ),
    # Which AcoustID recording a song is was told by AcoustID, never edited: it rides with the
    # song the way a set's origin does.
    "songs": (
        "id",
        "created_at",
        "edited_at",
        "name_sort",
        "recording_id",
        "created_by_kind",
        "created_by_user_id",
        "created_by_via",
        "cover_by_default",
    ),
    # How a number was learned and how many pictures agreed ride with the number itself.
    "usernames": (
        "id",
        "created_at",
        "edited_at",
        "name_sort",
        "number_via",
        "number_agreed",
    ),
    # Which file a Loop is of, and who made it, are what it is cut from rather than what it is.
    "loops": ("id", "asset_id", "created_by", "created_at", "edited_at"),
}

#: The per-viewer state tables, where the heart, the pin, the hidden flag, the views and the stars
#: are kept. None of it is an edit: each is one viewer's opinion of the thing, not what the thing
#: is, and the stars are no different from the heart beside them. So no trigger sits on these
#: tables and the first-moment step reads no rating (`test_edited` holds both).
OPINIONS: tuple[str, ...] = (
    "person_user_state",
    "site_user_state",
    "tag_user_state",
    "collection_user_state",
    "photo_set_user_state",
    "song_user_state",
    "asset_user_state",
)

#: A file's record fields on `assets`; not `music`, the song's name, which the song keeps.
ASSET_RECORD_COLUMNS: tuple[str, ...] = (
    "title",
    "download_url",
    "release_date",
    "details",
    "production_date",
    "site_code",
)


@dataclass(frozen=True)
class _List:
    """One table a record's list lives in, and the record each row belongs to.

    `owner` is the record table (or `asset_edits` for a file), `key` the column naming the record,
    `other` the table and column of the row's other end where it has one (a deleted tag takes its
    rows away, and that is the tag's deletion), and `updates` the columns whose change is an edit
    of a row that stays. A viewer's own state is never one of these (`OPINIONS`).
    """

    table: str
    owner: str
    key: str
    other: tuple[str, str] | None = None
    updates: tuple[str, ...] = ()


_ASSET = "asset_edits"

_LISTS: tuple[_List, ...] = (
    _List("people_aliases", "people", "person_id", updates=("alias",)),
    _List("people_links", "people", "person_id", updates=("url", "label")),
    _List("person_tags", "people", "person_id", other=("tags", "tag_id")),
    _List("site_aliases", "sites", "site_id", updates=("alias",)),
    _List("site_links", "sites", "site_id", updates=("url", "label")),
    _List("site_tags", "sites", "site_id", other=("tags", "tag_id")),
    _List("tag_aliases", "tags", "tag_id", updates=("alias",)),
    _List("collection_tags", "collections", "collection_id", other=("tags", "tag_id")),
    _List("photo_set_tags", "photo_sets", "photo_set_id", other=("tags", "tag_id")),
    _List("loop_tags", "loops", "loop_id", other=("tags", "tag_id")),
    _List("asset_tags", _ASSET, "asset_id", other=("tags", "tag_id")),
    _List("asset_people", _ASSET, "asset_id", other=("people", "person_id")),
    # A file's song, as its people and tags: put on, taken off, or moved by hand. A move by hand
    # stamps `added_at`; a merge moves `song_id` alone, which is the songs' edit, not the file's.
    _List("song_files", _ASSET, "asset_id", other=("songs", "song_id"), updates=("added_at",)),
    _List("asset_links", _ASSET, "asset_id", updates=("url", "label")),
)

# --- the statements ---------------------------------------------------------------------------

_TRIGGER = (
    "CREATE TRIGGER IF NOT EXISTS {{NAME}} AFTER {{EVENT}} ON {{ON}}{{WHEN}} BEGIN {{BODY}} END"
)
_DROP_TRIGGER = "DROP TRIGGER IF EXISTS {{NAME}}"

#: A record's own moment, now.
_TOUCH = "UPDATE {{TABLE}} SET edited_at = unixepoch() WHERE id = {{ID}};"

#: A file's moment, now: moved where it has one, made where it has none and the file is there.
#: Two statements guarded by NOT EXISTS rather than an upsert: a trigger a cascade reaches runs
#: under the cascade's own conflict handling, which overrides a clause written here.
_TOUCH_FILE = (
    "UPDATE asset_edits SET edited_at = unixepoch() WHERE asset_id = {{ID}};"
    " INSERT INTO asset_edits (asset_id, edited_at) SELECT {{ID}}, unixepoch()"
    " WHERE NOT EXISTS (SELECT 1 FROM asset_edits WHERE asset_id = {{ID}})"
    " AND EXISTS (SELECT 1 FROM assets WHERE id = {{ID}});"
)

_CHANGED = "OLD.{{COLUMN}} IS NOT NEW.{{COLUMN}}"
#: A pointer cleared by a cascade is the other thing's deletion: it counts while that thing exists.
_POINTER_CHANGED = (
    "(OLD.{{COLUMN}} IS NOT NEW.{{COLUMN}} AND (NEW.{{COLUMN}} IS NOT NULL"
    " OR EXISTS (SELECT 1 FROM {{INTO}} WHERE id = OLD.{{COLUMN}})))"
)
_STILL_THERE = "EXISTS (SELECT 1 FROM {{TABLE}} WHERE id = OLD.{{COLUMN}})"


def _touch(owner: str, row: str) -> str:
    """The body that moves `owner`'s moment for the record the expression `row` names."""
    if owner == _ASSET:
        return splice(_TOUCH_FILE, ID=row)
    return splice(_TOUCH, TABLE=owner, ID=row)


def _any_of(terms: list[str]) -> str:
    return "(" + " OR ".join(terms) + ")"


def _record_trigger(record: _Record) -> tuple[str, str]:
    """The one trigger on a record table: any record column changed, the rule's cover aside."""
    pointers = dict(record.pointers)
    plain = [
        splice(_POINTER_CHANGED, COLUMN=column, INTO=pointers[column])
        if column in pointers
        else splice(_CHANGED, COLUMN=column)
        for column in (*record.columns, *(c for c, _ in record.pointers if c not in record.columns))
    ]
    watched = [*record.columns, *(c for c, _ in record.pointers if c not in record.columns)]
    if record.covered:
        cover = [*_COVER, *(("cover_track_id",) if record.face else ())]
        cover_terms = [
            splice(_POINTER_CHANGED, COLUMN=column, INTO="assets")
            if column == "cover_asset_id"
            else splice(_CHANGED, COLUMN=column)
            for column in cover
        ]
        plain.append(
            "(OLD.cover_by_default IS NEW.cover_by_default AND " + _any_of(cover_terms) + ")"
        )
        watched.extend(cover)
    name = _PREFIX + record.table
    return name, splice(
        _TRIGGER,
        NAME=name,
        EVENT="UPDATE OF " + ", ".join(watched),
        ON=record.table,
        WHEN=" WHEN " + _any_of(plain),
        BODY=_touch(record.table, "NEW.id"),
    )


def _asset_record_trigger() -> tuple[str, str]:
    """A file's record fields, on the content component's table."""
    name = _PREFIX + "asset_record"
    return name, splice(
        _TRIGGER,
        NAME=name,
        EVENT="UPDATE OF " + ", ".join(ASSET_RECORD_COLUMNS),
        ON="assets",
        WHEN=" WHEN " + _any_of([splice(_CHANGED, COLUMN=c) for c in ASSET_RECORD_COLUMNS]),
        BODY=_touch(_ASSET, "NEW.id"),
    )


def _username_owner_trigger() -> tuple[str, str]:
    """A username given to a person, or taken off one, is an edit of both people's lists."""
    name = _PREFIX + "usernames_person"
    return name, splice(
        _TRIGGER,
        NAME=name,
        EVENT="UPDATE OF person_id",
        ON="usernames",
        WHEN=" WHEN OLD.person_id IS NOT NEW.person_id",
        BODY="UPDATE people SET edited_at = unixepoch() WHERE id IN (OLD.person_id, NEW.person_id);",
    )


def _list_triggers(one: _List) -> list[tuple[str, str]]:
    """The inserts, deletes and changes on one list table that are an edit of its record."""
    base = _PREFIX + one.table
    made: list[tuple[str, str]] = []
    made.append(
        (
            base + "_in",
            splice(
                _TRIGGER,
                NAME=base + "_in",
                EVENT="INSERT",
                ON=one.table,
                WHEN="",
                BODY=_touch(one.owner, "NEW." + one.key),
            ),
        )
    )
    # Taken off while both ends stand: the record's own table is read for a file (its moment row
    # has a key into it) and the other end wherever the row has one.
    guards = []
    if one.owner == _ASSET:
        guards.append(splice(_STILL_THERE, TABLE="assets", COLUMN=one.key))
    if one.other is not None:
        guards.append(splice(_STILL_THERE, TABLE=one.other[0], COLUMN=one.other[1]))
    made.append(
        (
            base + "_out",
            splice(
                _TRIGGER,
                NAME=base + "_out",
                EVENT="DELETE",
                ON=one.table,
                WHEN=(" WHEN " + " AND ".join(guards)) if guards else "",
                BODY=_touch(one.owner, "OLD." + one.key),
            ),
        )
    )
    if one.updates:
        made.append(
            (
                base + "_changed",
                splice(
                    _TRIGGER,
                    NAME=base + "_changed",
                    EVENT="UPDATE OF " + ", ".join(one.updates),
                    ON=one.table,
                    WHEN=" WHEN " + _any_of([splice(_CHANGED, COLUMN=c) for c in one.updates]),
                    BODY=_touch(one.owner, "NEW." + one.key),
                ),
            )
        )
    return made


def _every_trigger() -> dict[str, str]:
    built = [_record_trigger(record) for record in _RECORDS]
    built.append(_asset_record_trigger())
    built.append(_username_owner_trigger())
    for one in _LISTS:
        built.extend(_list_triggers(one))
    triggers = dict(built)
    if len(triggers) != len(built):  # pragma: no cover (two triggers given one name)
        raise RuntimeError("two edit triggers share a name")
    return triggers


#: Every trigger this module keeps, by name. Built once, from this module's constants only.
TRIGGERS: dict[str, str] = _every_trigger()

#: Every table a trigger here sits on, which `keep_true` needs to exist before it writes any.
_ON: tuple[str, ...] = tuple(
    sorted(
        {record.table for record in _RECORDS}
        | {"assets", "asset_edits"}
        | {o.table for o in _LISTS}
    )
)

_TRIGGERS_PRESENT = "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name LIKE ?"


def _normal(ddl: str) -> str:
    """A trigger's text without the IF NOT EXISTS the engine drops, spacing folded."""
    return " ".join(ddl.replace("IF NOT EXISTS ", "").split())


async def keep_true(connection: Connection) -> None:
    """Every boot: the triggers are the ones this build writes, whatever rebuilt a table under them.

    Cheap when nothing is wrong (one read of the schema). A trigger missing or different is written
    again; one this build no longer declares is dropped. A moment missed while a trigger was gone is
    not recoverable here and is not guessed: the thing keeps the moment it had. Guarded on the
    tables, because the schema tests bring a catalog up with nothing under it.
    """
    for table in _ON:
        if not await table_exists(connection, table):
            return
    rows = await connection.execute_fetchall(_TRIGGERS_PRESENT, (_PREFIX + "%",))
    present = {str(row[0]): _normal(str(row[1])) for row in rows}
    wanted = {name: _normal(ddl) for name, ddl in TRIGGERS.items()}
    gone = sorted(name for name in present if name not in wanted)
    repaired = sorted(name for name in wanted if present.get(name) != wanted[name])
    if not gone and not repaired:
        return
    log.info("edited.triggers_written", written=len(repaired), dropped=len(gone))
    for name in (*gone, *repaired):
        await connection.execute(splice(_DROP_TRIGGER, NAME=name))
    for name in repaired:
        await connection.execute(TRIGGERS[name])


register_schema_invariant("catalog_edited_at", keep_true)


# --- the first moment, off the ledger -------------------------------------------------------------

#: The verbs History writes for a record edit, over a subject that is the thing edited. An arrival
#: (`added`), a hiding, a refusal, a move on disk and a deletion are not among them.
_RECORD_VERBS = "('edited','renamed','linked','unlinked','removed','merged','enriched','named')"

#: Each record table and the word the ledger names its things by.
_KINDS: tuple[tuple[str, str], ...] = (
    ("people", "person"),
    ("sites", "site"),
    ("tags", "tag"),
    ("collections", "collection"),
    ("photo_sets", "photo_set"),
    ("songs", "song"),
    ("usernames", "username"),
)

#: A record's newest edit off the ledger, where it has none yet. `{{TABLE}}` and `{{KIND}}` are
#: this module's own words.
_FROM_THE_LEDGER = (
    "UPDATE {{TABLE}} SET edited_at = e.at FROM ("
    " SELECT s.subject_id AS id, MAX(d.decided_at) AS at"
    " FROM workbench_decision_subjects s JOIN workbench_decisions d ON d.id = s.decision_id"
    " WHERE s.kind = '{{KIND}}' AND d.verb IN {{VERBS}} GROUP BY s.subject_id) e"
    " WHERE e.id = {{TABLE}}.id AND {{TABLE}}.edited_at IS NULL"
)

#: A file's: its own record form's saves, and a person, a tag or a song put on it or taken off.
_FILES_FROM_THE_LEDGER = (
    "INSERT INTO asset_edits (asset_id, edited_at)"
    " SELECT s.subject_id, MAX(d.decided_at)"
    " FROM workbench_decision_subjects s JOIN workbench_decisions d ON d.id = s.decision_id"
    " JOIN assets a ON a.id = s.subject_id"
    " WHERE s.kind = 'asset' AND (d.verb = 'edited'"
    " OR (d.verb IN ('linked','unlinked') AND d.object_kind IN ('person','tag','song')))"
    " AND NOT EXISTS (SELECT 1 FROM asset_edits x WHERE x.asset_id = s.subject_id)"
    " GROUP BY s.subject_id"
)


async def backfill(connection: Connection) -> dict[str, int]:
    """Catalog version 77: each thing's newest record edit, read off the ledger where it was kept.

    Once, and only where a thing has no moment yet, so a step replayed over a library that has them
    changes nothing it already knew. The ledger's tables belong to a component brought up after this
    one, so they are read only where they are there to read. A rating in the opinions is not read:
    the stars are an opinion, like the heart (`OPINIONS`). Says how many of each kind it filled.
    """
    filled: dict[str, int] = dict.fromkeys([table for table, _ in _KINDS] + ["assets"], 0)
    ledger = await table_exists(connection, "workbench_decisions") and await table_exists(
        connection, "workbench_decision_subjects"
    )
    if ledger:
        for table, kind in _KINDS:
            cursor = await connection.execute(
                splice(_FROM_THE_LEDGER, TABLE=table, KIND=kind, VERBS=_RECORD_VERBS)
            )
            filled[table] = max(cursor.rowcount, 0)
        cursor = await connection.execute(_FILES_FROM_THE_LEDGER)
        filled["assets"] = max(cursor.rowcount, 0)
    log.info("edited.backfilled", **filled)
    return filled
