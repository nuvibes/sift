# SPDX-License-Identifier: AGPL-3.0-or-later
"""The picture a person, collection or Photo Set is drawn as when nobody chose one: its first
picture nobody hides, kept true by triggers on the filing rows."""

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
    """One kind that carries a cover: its word, its table, and whether its cover may be a face."""

    word: SubjectKind
    table: str
    face: bool = False


_PERSON = _Covered("person", "people", face=True)
_SITE = _Covered("site", "sites")
_TAG = _Covered("tag", "tags")
_COLLECTION = _Covered("collection", "collections")
_PHOTO_SET = _Covered("photo_set", "photo_sets")
#: A song is a named thing, so the rule does not reach it.
_SONG = _Covered("song", "songs")

#: Every kind that carries a cover, which `standing` answers for.
_COVERED: tuple[_Covered, ...] = (_PERSON, _SITE, _TAG, _COLLECTION, _PHOTO_SET, _SONG)


@dataclass(frozen=True)
class _Kind:
    """One kind the rule reaches and how a file is filed under it; `order` is what first means."""

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


#: The kinds the rule reaches: not a tag or a Site, whose picture is chosen or is its letter.
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
    # At one moment, a collection takes the oldest file and a Photo Set its own first.
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

KINDS: tuple[str, ...] = tuple(kind.word for kind in _KINDS)

_BY_WORD = {kind.word: kind for kind in _KINDS}

_PREFIX = "default_cover_"


# --- the templates

#: The first file under the entity with a copy there to read, joined to `assets` so a file being
#: deleted is never chosen.
_FIRST = (
    "SELECT m.asset_id FROM {{MEMBERSHIP}} JOIN assets a ON a.id = m.asset_id"
    " WHERE {{BELONGS}} AND {{PICTURE}} AND {{PRESENT}}{{KEPT}} ORDER BY {{ORDER}}, m.asset_id"
    " LIMIT 1"
)

_PICTURE = "a.media_type = 'image'"

#: A file nobody keeps in Hidden first; each reads the kind's index in order and stops at the first
#: that passes.
_FIRST_SHOWN = "SELECT picked FROM (SELECT COALESCE(({{SHOWN}}), ({{ANY}})) AS picked) WHERE picked IS NOT NULL"

INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_asset_people_first"
    " ON asset_people(person_id, decided_at, asset_id)",
    "CREATE INDEX IF NOT EXISTS ix_collection_items_first"
    " ON collection_items(collection_id, added_at, asset_id)",
    "CREATE INDEX IF NOT EXISTS ix_psi_first"
    " ON photo_set_items(photo_set_id, added_at, position, asset_id)",
)

#: Whether the file is in somebody's Hidden: then it is the cover only where every file under the
#: entity is.
_IN_HIDDEN = (
    "EXISTS (SELECT 1 FROM users u JOIN viewer_assets v"
    " ON v.user_id = u.id AND v.asset_id = {{FILE}} AND v.concealed = 1)"
)

_EMPTY = (
    "{{ROW}}.cover_asset_id IS NULL AND {{ROW}}.cover_upload_id IS NULL"
    " AND {{ROW}}.cover_cleared_at IS NULL"
)

_HAS_ROOM = "EXISTS (SELECT 1 FROM {{TABLE}} e WHERE e.id = {{ENTITY}} AND {{EMPTY}})"

#: Every pointer is written, so a moment, window or face left by a file that went cannot outlive it.
_ASSIGN = (
    "UPDATE {{TABLE}} SET cover_asset_id = ({{FIRST}}), cover_by_default = ({{FIRST}}),"
    " cover_at_ms = NULL, cover_frame = NULL{{FACE}}"
    " WHERE {{WHO}} AND {{EMPTY}} AND EXISTS ({{FIRST}})"
)

#: Let go of the rule's pick when its file is no longer filed there; a chosen picture stays.
_RELEASE_DEFAULT = (
    "UPDATE {{TABLE}} SET cover_asset_id = NULL, cover_by_default = NULL"
    " WHERE {{WHO}} AND {{TABLE}}.cover_asset_id = OLD.asset_id"
    " AND {{IS_DEFAULT}}"
)

_IS_DEFAULT = (
    "{{ROW}}.cover_by_default IS NOT NULL AND {{ROW}}.cover_asset_id = {{ROW}}.cover_by_default"
    " AND {{ROW}}.cover_upload_id IS NULL AND {{ROW}}.cover_at_ms IS NULL"
    " AND {{ROW}}.cover_frame IS NULL{{FACE}}"
)

_TRIGGER = (
    "CREATE TRIGGER IF NOT EXISTS {{NAME}} AFTER {{EVENT}} ON {{ON}}{{WHEN}} BEGIN {{BODY}}; END"
)
_DROP_TRIGGER = "DROP TRIGGER IF EXISTS {{NAME}}"

_TRIGGERS_PRESENT = (
    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND substr(name, 1, ?) = ?"
)


# --- composing them


def _first(kind: _Kind, *, hidden: bool = True) -> str:
    """The kind's first-file subquery on the UPDATE's entity row; without `hidden` before the stored
    verdict exists."""
    pieces = {
        "MEMBERSHIP": kind.membership,
        "BELONGS": splice(kind.belongs, ENTITY=kind.table + ".id"),
        "PICTURE": _PICTURE,
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
    assign_one: dict[str, str]
    assign_all: dict[str, str]


@functools.cache
def _kind_triggers(kind: _Kind, hidden: bool) -> dict[str, str]:
    """The triggers that keep one kind's default covers."""
    triggers: dict[str, str] = {}
    stem = _PREFIX + kind.word
    # The guard is one read of the entity's row.
    triggers[stem + "_filed"] = _trigger(
        stem + "_filed",
        "INSERT",
        kind.filed_on,
        (_assign(kind, f"{kind.table}.id = {kind.entity_of_new}", hidden=hidden),),
        when=splice(_HAS_ROOM, TABLE=kind.table, ENTITY=kind.entity_of_new, EMPTY=_empty("e")),
    )
    # Taken away by anything but a person clearing it.
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
    # The rule's pick goes with the file.
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
        # A filing under something whose pick is a hidden file: picked again.
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
    # Only a row whose concealment moved fires anything.
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
    """Everything, composed with or without reading who keeps a file in Hidden (`_reads_hidden`)."""
    triggers: dict[str, str] = {}
    for kind in _KINDS:
        triggers.update(_kind_triggers(kind, hidden))

    def filled(file: str) -> tuple[str, ...]:
        return tuple(
            _assign(
                kind,
                f"{kind.table}.id IN ({splice(kind.entities_of_file, FILE=file)})",
                hidden=hidden,
            )
            for kind in _KINDS
        )

    def arrived(file: str) -> tuple[str, ...]:
        return filled(file) + (picked_again(file) if hidden else ())

    def picked_again(file: str) -> tuple[str, ...]:
        return tuple(
            _pick_again(kind, f"{kind.table}.id IN ({splice(kind.entities_of_file, FILE=file)})")
            for kind in _KINDS
        )

    # A scan re-stamping a present copy fires nothing.
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
    triggers[_PREFIX + "kind_moved"] = _trigger(
        _PREFIX + "kind_moved",
        "UPDATE OF media_type",
        "assets",
        tuple(_let_go(kind, "NEW.id") for kind in _KINDS) + filled("NEW.id"),
        when="NEW.media_type IS NOT OLD.media_type",
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
    """Whether the stored verdict is there: another component's, brought up after this one."""
    return await table_exists(connection, "viewer_assets")


def triggers() -> dict[str, str]:
    """The triggers the rule keeps, by name, as this build writes them."""
    return dict(_statements().triggers)


def drop_triggers() -> tuple[str, ...]:
    """The statements that take every one of those triggers away."""
    return _statements().drop_triggers


# --- running them


async def assign_if_empty(connection: Connection, kind: str, entity_id: str) -> bool:
    """Give one entity its first file's picture where it has no cover and no clear mark, for a
    merge."""
    built = _statements(await _reads_hidden(connection))
    cursor = await connection.execute(built.assign_one[_BY_WORD[kind].word], (entity_id,))
    return bool(cursor.rowcount)


async def fill_every_empty(connection: Connection) -> dict[str, int]:
    """Give every empty entity its first file's picture, per kind; idempotent."""
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
    """The catalog step that brings the rule in: the triggers, and the backfill."""
    await index_the_picks(connection)
    await _make_triggers(connection)
    filled = await fill_every_empty(connection)
    log.info("covers.default.filled", **filled)
    return filled


async def index_the_picks(connection: Connection) -> None:
    """Catalog version 92: each kind's filings indexed in its pick's order (`INDEXES`)."""
    for statement in INDEXES:
        await connection.execute(statement)


# --- giving back what the rule put on a tag or a Site

#: The triggers the rule kept on a tag and a Site before catalog version 76.
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

#: Take away a cover that is still only the rule's pick, answering each row for History. No clear
#: mark: nobody took it away.
_GIVE_BACK = (
    "UPDATE {{TABLE}} SET cover_asset_id = NULL, cover_by_default = NULL"
    " WHERE {{IS_DEFAULT}} RETURNING id, name"
)
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

#: What a cover taken away says on History; held to `kernel/covers.cover_payload` by a test.
COVER_TAKEN_AWAY = json.dumps({"cover": "none"})


async def take_back_tags_and_sites(connection: Connection) -> dict[str, int]:
    """Catalog version 76: the rule leaves tags and Sites, giving back what it gave them, said on
    History by Sift."""
    for name in _RETIRED_TRIGGERS:
        await connection.execute(splice(_DROP_TRIGGER, NAME=name))
    # Remade from this build's kinds, without the retired two.
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


# --- giving back the faces Sift made covers of

#: Take away every face cover Sift cut out of a file; `_lost` then gives the whole first picture.
_FACES_BACK = (
    "UPDATE people SET cover_asset_id = NULL, cover_track_id = NULL, cover_at_ms = NULL,"
    " cover_frame = NULL, cover_by_default = NULL"
    " WHERE cover_track_id IS NOT NULL AND cover_upload_id IS NULL RETURNING id"
)

_PICTURED = (
    "SELECT COUNT(*) FROM people"
    " WHERE id IN (SELECT value FROM json_each(?)) AND cover_asset_id IS NOT NULL"
)


async def faces_back_to_the_rule(connection: Connection) -> dict[str, int]:
    """Catalog version 79: every face Sift made somebody's cover goes back to the rule. Logged, not
    recorded."""
    rows = list(await connection.execute_fetchall(_FACES_BACK))
    ids = json.dumps([str(row[0]) for row in rows])
    counted = list(await connection.execute_fetchall(_PICTURED, (ids,)))
    given: dict[str, int] = {"faces": len(rows), "pictured": int(counted[0][0]) if counted else 0}
    log.info("covers.default.faces_given_back", **given)
    return given


# --- a default cover on a GIF or a video

#: Let go of the rule's pick where its file is not a picture.
_LET_GO = (
    "UPDATE {{TABLE}} SET cover_asset_id = NULL, cover_by_default = NULL"
    " WHERE {{IS_DEFAULT}} AND {{TABLE}}.cover_asset_id IN"
    " (SELECT a.id FROM assets a WHERE a.id = {{FILE}} AND NOT {{PICTURE}}){{TAIL}}"
)


def _let_go(kind: _Kind, file: str, tail: str = "") -> str:
    return splice(
        _LET_GO,
        TABLE=kind.table,
        IS_DEFAULT=_is_default(kind.covered, kind.table),
        PICTURE=_PICTURE,
        FILE=file,
        TAIL=tail,
    )


_LET_GO_BACK = tuple(
    (kind, _let_go(kind, kind.table + ".cover_asset_id", " RETURNING id")) for kind in _KINDS
)


async def pictures_only(connection: Connection) -> dict[str, int]:
    """Catalog version 93: every default cover on a GIF or a video goes back to the rule."""
    for statement in drop_triggers():
        await connection.execute(statement)
    await _make_triggers(connection)
    given: dict[str, int] = {}
    people: list[str] = []
    for kind, let_go in _LET_GO_BACK:
        rows = [str(row[0]) for row in await connection.execute_fetchall(let_go)]
        given[kind.word] = len(rows)
        people += rows if kind.covered is _PERSON else []
    counted = list(await connection.execute_fetchall(_PICTURED, (json.dumps(people),)))
    given["pictured"] = int(counted[0][0]) if counted else 0
    log.info("covers.default.pictures_only", **given)
    return given


# --- a cover in Hidden while a file nobody hides is there

#: Pick again for every entity whose cover is still the rule's pick of a file in Hidden while a file
#: nobody hides is filed there.
_PICK_AGAIN = (
    "UPDATE {{TABLE}} SET cover_asset_id = ({{FIRST}}), cover_by_default = ({{FIRST}}),"
    " cover_at_ms = NULL, cover_frame = NULL{{FACE}}"
    " WHERE {{WHO}} AND {{IS_DEFAULT}} AND {{HIDDEN}}"
    " AND ({{FIRST}}) IS NOT {{TABLE}}.cover_asset_id{{TAIL}}"
)

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
    """Catalog version 89: the rule picks again wherever its pick is a hidden file and an unhidden
    one is there."""
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
    """Every boot: the triggers match this build, or they are made again and the gaps filled."""
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
    # A catalog made before the stored verdict existed is not a fault.
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


# --- what a gap-filler asks


@dataclass(frozen=True, slots=True)
class Standing:
    """Where an entity's cover stands for a writer that only fills a gap: `cleared` or
    `by_default`."""

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


#: A Site a person cleared stays empty for a stash-box too.
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
