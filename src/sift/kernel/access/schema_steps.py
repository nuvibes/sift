# SPDX-License-Identifier: AGPL-3.0-or-later
"""The steps that bring a catalog from its baseline, version 70, to this build's."""

from __future__ import annotations

import re

from sift.kernel import presses
from sift.kernel.access import creator_studios, default_covers, edited, schema_columns
from sift.kernel.access.schema import (
    _CATALOG_TABLES,
    _COVER_TABLES,
    _CREATE_ARTISTS,
    _CREATE_SONG_ARTISTS,
    _CREATE_SONG_FILES,
    _CREATE_SONG_USER_STATE,
    _CREATE_SONGS,
    _CREATE_UNDO_ROWS,
    file_tags_in_a_tree,
)
from sift.kernel.access.schema_indexes import _CATALOG_82_INDEXES, _SONG_INDEXES
from sift.kernel.content import songs
from sift.kernel.db import Connection
from sift.kernel.migrations import check_allows, column_exists, table_exists, widen_a_check

#: Catalog 82: the fragment of `song_files`' stored CHECK widened for a song arriving by swap.
_SONG_SOURCES_WAS = "source IN ('acoustid', 'site', 'shared')"
_SONG_SOURCES_NOW = "source IN ('acoustid', 'site', 'shared', 'swap')"

_PHOTO_SET_ORIGINS_WAS = "origin IN ('manual','download','folder','archive','filename','shoot')"
_PHOTO_SET_ORIGINS_NOW = (
    "origin IN ('manual','download','folder','archive','filename','shoot','stash_library')"
)

_ADD_SONG_HIDDEN = "ALTER TABLE song_user_state ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0"
_ADD_SONG_HIDDEN_AT = "ALTER TABLE song_user_state ADD COLUMN hidden_at INTEGER"

#: The People, Sites and Tags a Stash library imported, given their own maker word. `{table}` and
#: `{kind}` are this module's own words.
_STASH_LIBRARY_MADE = (
    "UPDATE {table} SET created_by_via = 'stash_library'"
    " WHERE created_by_kind = 'sift' AND created_by_via = 'stash' AND created_by_box_id IS NULL"
    " AND NOT EXISTS (SELECT 1 FROM workbench_decisions d WHERE d.object_kind = '{kind}'"
    " AND d.object_id = {table}.id AND d.verb = 'added' AND d.actor_kind = 'sift'"
    " AND d.actor_id = 'stash'"
    " AND d.decided_at BETWEEN COALESCE({table}.created_at, 0) - 5"
    " AND COALESCE({table}.created_at, 0) + 5)"
)
_STASH_LIBRARY_MADE_WHERE_NOTHING_IS_RECORDED = (
    "UPDATE {table} SET created_by_via = 'stash_library'"
    " WHERE created_by_kind = 'sift' AND created_by_via = 'stash' AND created_by_box_id IS NULL"
)
_MADE_BY_KIND = (("people", "person"), ("sites", "site"), ("tags", "tag"))

#: The people a stash-box filed with no picture given their first file's, as every other filing
#: does.
_COVER_FROM_A_STASH_BOX_FILING = """
UPDATE people
   SET cover_asset_id = (
       SELECT ap.asset_id FROM asset_people ap
        WHERE ap.person_id = people.id AND ap.source = 'stash_box'
        ORDER BY ap.decided_at, ap.asset_id
        LIMIT 1)
 WHERE cover_asset_id IS NULL
   AND cover_upload_id IS NULL
   AND EXISTS (
       SELECT 1 FROM asset_people ap WHERE ap.person_id = people.id AND ap.source = 'stash_box')
"""
_ADD_COVER_CLEARED_AT = "ALTER TABLE {table} ADD COLUMN cover_cleared_at INTEGER"
_ADD_COVER_BY_DEFAULT = "ALTER TABLE {table} ADD COLUMN cover_by_default TEXT"
_ADD_SITES_DRAWN_BY_PACK = "ALTER TABLE sites ADD COLUMN drawn_by_pack INTEGER NOT NULL DEFAULT 0"

#: `{table}` is one of `edited.EDITED_TABLES`.
_ADD_EDITED_AT = "ALTER TABLE {table} ADD COLUMN edited_at INTEGER"

#: Which act made a tag the `produced` pass made, read off the earliest of its files.
_ADD_TAGS_CREATED_BY_ACT = "ALTER TABLE tags ADD COLUMN created_by_act TEXT"

_TAGS_CREATED_BY_ACT = """
UPDATE tags SET created_by_act = (
  SELECT CASE made.operation WHEN 'compress' THEN 'compress' ELSE 'edit' END
    FROM asset_tags filed
    JOIN produced_files made ON made.asset_id = filed.asset_id
   WHERE filed.tag_id = tags.id AND filed.source = 'produced'
   ORDER BY made.produced_at, made.asset_id
   LIMIT 1
)
 WHERE created_by_kind = 'sift' AND created_by_via = 'produced' AND created_by_act IS NULL
"""


async def tag_what_an_act_made(connection: Connection) -> None:
    """Step 78: which act made each tag Sift put on a file it produced."""
    if not await column_exists(connection, "tags", "created_by_act"):
        await connection.execute(_ADD_TAGS_CREATED_BY_ACT)
    if await table_exists(connection, "produced_files"):
        await connection.execute(_TAGS_CREATED_BY_ACT)


_CATALOG_TABLE_NAMES = tuple(
    re.findall(
        r"CREATE TABLE IF NOT EXISTS (\w+)",
        "\n".join((*_CATALOG_TABLES, _CREATE_UNDO_ROWS, creator_studios.CREATE_CREATOR_STUDIOS)),
    )
)
_BROKEN_KEYS = "SELECT DISTINCT fkid FROM pragma_foreign_key_check(?)"
_KEY_COLUMNS = (
    'SELECT "table", "from", "to", upper(on_delete) FROM pragma_foreign_key_list(?)'
    " WHERE id = ? ORDER BY seq"
)
_BROKEN_ROWS = "{present} AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE {same})"
_LET_GO = {"SET NULL": "UPDATE {table} SET {cleared} WHERE {where}"}
_LET_GO_ROWS = "DELETE FROM {table} WHERE {where}"


def _named(identifier: object) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


async def let_go_of_broken_references(connection: Connection) -> None:
    """Step 90: a row whose parent is gone gets what its key's ON DELETE would have done."""
    for table in _CATALOG_TABLE_NAMES:
        for broken in await connection.execute_fetchall(_BROKEN_KEYS, (table,)):
            key = list(await connection.execute_fetchall(_KEY_COLUMNS, (table, broken[0])))
            own = [f"{_named(table)}.{_named(one[1])}" for one in key]
            present = " AND ".join(f"{mine} IS NOT NULL" for mine in own)
            pairs = zip(key, own, strict=True)
            same = " AND ".join(f"p.{_named(one[2])} = {mine}" for one, mine in pairs)
            where = _BROKEN_ROWS.format(present=present, parent=_named(key[0][0]), same=same)
            cleared = ", ".join(f"{_named(one[1])} = NULL" for one in key)
            change = _LET_GO.get(str(key[0][3]), _LET_GO_ROWS)
            statement = change.format(table=_named(table), cleared=cleared, where=where)
            # nosemgrep: sift-no-string-built-sql (names off this library's own keys, quoted)
            await connection.execute(statement)


_LATER_STEPS = (
    (90, let_go_of_broken_references),
    (91, schema_columns.forget_the_arrangements),
    (92, default_covers.index_the_picks),
    (93, default_covers.pictures_only),
)


async def from_71(connection: Connection, on_disk: int) -> None:
    """Catalog steps 71 to 75."""
    if 0 < on_disk < 71:
        await file_tags_in_a_tree(connection)
    if 0 < on_disk < 72:
        await schema_columns.keep_from_swaps(connection)
    if 0 < on_disk < 73:
        await _name_what_stash_libraries_made(connection)
    if 0 < on_disk < 74:
        await connection.execute(_COVER_FROM_A_STASH_BOX_FILING)
    if 0 < on_disk < 75:
        await _cover_columns(connection)


async def _name_what_stash_libraries_made(connection: Connection) -> None:
    recorded = await table_exists(connection, "workbench_decisions")
    shape = _STASH_LIBRARY_MADE if recorded else _STASH_LIBRARY_MADE_WHERE_NOTHING_IS_RECORDED
    for table, kind in _MADE_BY_KIND:
        # Two module constants filled with two more: nothing from run time reaches the text.
        # nosemgrep: sift-no-string-built-sql
        await connection.execute(shape.format(table=table, kind=kind))


async def _cover_columns(connection: Connection) -> None:
    for table in _COVER_TABLES:
        if not await column_exists(connection, table, "cover_cleared_at"):
            # nosemgrep: sift-no-string-built-sql
            await connection.execute(_ADD_COVER_CLEARED_AT.format(table=table))
        if not await column_exists(connection, table, "cover_by_default"):
            # nosemgrep: sift-no-string-built-sql
            await connection.execute(_ADD_COVER_BY_DEFAULT.format(table=table))
    if not await column_exists(connection, "sites", "drawn_by_pack"):
        await connection.execute(_ADD_SITES_DRAWN_BY_PACK)
    await default_covers.start(connection)


async def from_76(connection: Connection, on_disk: int) -> None:
    """Catalog steps 76 to 80."""
    if 0 < on_disk < 76:
        await default_covers.take_back_tags_and_sites(connection)
    if 0 < on_disk < 77:
        await _edited_columns(connection)
    if 0 < on_disk < 78:
        await tag_what_an_act_made(connection)
    if 0 < on_disk < 79:
        await default_covers.faces_back_to_the_rule(connection)
    if 0 < on_disk < 80:
        await creator_studios.repair(connection)


async def _edited_columns(connection: Connection) -> None:
    for table in edited.EDITED_TABLES:
        if not await column_exists(connection, table, "edited_at"):
            # nosemgrep: sift-no-string-built-sql
            await connection.execute(_ADD_EDITED_AT.format(table=table))
    await connection.execute(edited.CREATE_ASSET_EDITS)
    await edited.backfill(connection)


async def from_81(connection: Connection, on_disk: int) -> None:
    """Catalog steps 81 to 89; 83 and 88 run on a new library too."""
    if 0 < on_disk < 81:
        await _songs_of_their_own(connection)
    if 0 < on_disk < 82:
        await _songs_hidden_and_credited(connection)
    if on_disk < 83:
        await connection.execute(_CREATE_UNDO_ROWS)
    if 0 < on_disk < 85:
        await presses.carry_into_the_ledger(connection)
    if 0 < on_disk < 86 and not await check_allows(connection, "photo_sets", "stash_library"):
        await widen_a_check(
            connection, "photo_sets", was=_PHOTO_SET_ORIGINS_WAS, now=_PHOTO_SET_ORIGINS_NOW
        )
    if 0 < on_disk < 87:
        await schema_columns.name_the_box_on_filings(connection)
    if on_disk < 88:
        await schema_columns.mark_folders(connection)
    if 0 < on_disk < 89:
        await default_covers.out_of_hidden(connection)


async def _songs_of_their_own(connection: Connection) -> None:
    for statement in (
        _CREATE_SONGS,
        _CREATE_SONG_FILES,
        _CREATE_SONG_USER_STATE,
        *_SONG_INDEXES,
    ):
        await connection.execute(statement)
    await songs.start(connection)
    await songs.move_named_songs(connection)


async def _songs_hidden_and_credited(connection: Connection) -> None:
    if not await column_exists(connection, "song_user_state", "hidden"):
        await connection.execute(_ADD_SONG_HIDDEN)
    if not await column_exists(connection, "song_user_state", "hidden_at"):
        await connection.execute(_ADD_SONG_HIDDEN_AT)
    for statement in (_CREATE_ARTISTS, _CREATE_SONG_ARTISTS, *_CATALOG_82_INDEXES):
        await connection.execute(statement)
    # Only the CHECK widens, so the triggers on `song_files` stay.
    if not await check_allows(connection, "song_files", "swap"):
        await widen_a_check(connection, "song_files", was=_SONG_SOURCES_WAS, now=_SONG_SOURCES_NOW)


async def from_90(connection: Connection, on_disk: int) -> None:
    """Catalog steps 90 on."""
    for below, step in _LATER_STEPS:
        if 0 < on_disk < below:
            await step(connection)
