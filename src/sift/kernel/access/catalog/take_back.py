# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taking back what one source filed on a file, the shells it leaves, and an Undo's put-back."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sift.kernel.access.stamps import bump_stamps_for_object
from sift.kernel.access.viewer import ObjectType
from sift.kernel.audience import NOBODY
from sift.kernel.changes import About, announce
from sift.kernel.db import Connection, Row, register_schema_invariant
from sift.kernel.sql_splice import splice

# --- TAKING BACK WHAT ONE SOURCE FILED ON A FILE ------------------------------------------------
#
# A source marks every row it writes (`source`), so its rows can be found and taken back off
# without touching one somebody else wrote. These doors, each on the caller's connection, read
# those rows, take them off, find and remove what they leave holding nothing, and put it all back
# for an Undo. What an Undo needs is kept in `undo_rows` under the receipt rather than in its
# payload, which every History page drawing the line would read whole.


@dataclass(frozen=True, slots=True)
class Filed:
    """One row a source put on a file, with the names it can be said by, folded.

    `kind` is `person`, `tag` or `username`. A person's and a tag's names are its own and its other
    names; a username's is its handle (none for a Site's nameless row), and `sites` are the names
    of the Site it is on, its other names too.
    """

    kind: str
    asset_id: str
    target_id: str
    names: frozenset[str]
    sites: frozenset[str] = frozenset()
    site_id: str | None = None
    source: str | None = None
    decided_at: int | None = None
    post_id: str | None = None
    #: The stash-box whose answer made the row, where it says (the note over `asset_people`).
    box_id: str | None = None


@dataclass(frozen=True, slots=True)
class StillSaid:
    """What the answers still standing on a file say, folded: a row one of them says stays on.

    `accounts` are (Site, handle) pairs; `sites` the Sites an answer files the file under by name.
    """

    people: frozenset[str] = frozenset()
    tags: frozenset[str] = frozenset()
    accounts: frozenset[tuple[str, str]] = frozenset()
    sites: frozenset[str] = frozenset()

    def says(self, row: Filed) -> bool:
        """Whether a standing answer names this row, by any of its names."""
        if row.kind == "person":
            return bool(row.names & self.people)
        if row.kind == "tag":
            return bool(row.names & self.tags)
        if not row.names:
            return bool(row.sites & self.sites)
        return any(handle in row.names and site in row.sites for site, handle in self.accounts)


#: What one source filed on one file, a statement per table, each row with its names. Sought on
#: each table's primary key, whose first column is the file.
_FILED_BY: Mapping[str, str] = {
    "person": """
SELECT ap.person_id AS id, ap.decided_at AS decided_at, NULL AS post_id, ap.box_id AS box_id,
       p.name AS name,
       NULL AS site_id, NULL AS site,
       (SELECT json_group_array(a.alias) FROM people_aliases a WHERE a.person_id = p.id) AS aliases
  FROM asset_people ap JOIN people p ON p.id = ap.person_id
 WHERE ap.asset_id = ? AND ap.source = ?
 ORDER BY ap.person_id
""",
    "tag": """
SELECT at.tag_id AS id, at.decided_at AS decided_at, NULL AS post_id, at.box_id AS box_id,
       t.name AS name,
       NULL AS site_id, NULL AS site,
       (SELECT json_group_array(a.alias) FROM tag_aliases a WHERE a.tag_id = t.id) AS aliases
  FROM asset_tags at JOIN tags t ON t.id = at.tag_id
 WHERE at.asset_id = ? AND at.source = ?
 ORDER BY at.tag_id
""",
    "username": """
SELECT au.username_id AS id, au.decided_at AS decided_at, au.post_id AS post_id,
       au.box_id AS box_id, u.name AS name,
       s.id AS site_id, s.name AS site,
       (SELECT json_group_array(a.alias) FROM site_aliases a WHERE a.site_id = s.id) AS aliases
  FROM asset_usernames au JOIN usernames u ON u.id = au.username_id
  LEFT JOIN sites s ON s.id = u.site_id
 WHERE au.asset_id = ? AND au.source = ?
 ORDER BY au.username_id
""",
}

#: The join table, the column naming what is filed, and the object a stamp is bumped for, by kind.
_FILED_IN: Mapping[str, tuple[str, str]] = {
    "person": ("asset_people", "person_id"),
    "tag": ("asset_tags", "tag_id"),
    "username": ("asset_usernames", "username_id"),
}
_TAKE_OFF_FILED: Mapping[str, str] = {
    "person": "DELETE FROM asset_people WHERE asset_id = ? AND person_id = ? AND source = ?",
    "tag": "DELETE FROM asset_tags WHERE asset_id = ? AND tag_id = ? AND source = ?",
    "username": "DELETE FROM asset_usernames WHERE asset_id = ? AND username_id = ? AND source = ?",
}


def _folded(*names: object) -> frozenset[str]:
    return frozenset(str(one).strip().casefold() for one in names if str(one or "").strip())


def _aliases(raw: object) -> list[str]:
    """The other names `json_group_array` gathered: always an array, empty where there are none."""
    return [str(one) for one in json.loads(str(raw)) if one]


async def filed_by_on(connection: Connection, asset_id: str, source: str) -> list[Filed]:
    """Every row this source put on this file: its people, its tags and its usernames."""
    filed: list[Filed] = []
    for kind, statement in _FILED_BY.items():
        for row in await connection.execute_fetchall(statement, (asset_id, source)):
            aliases = _aliases(row["aliases"])
            is_username = kind == "username"
            filed.append(
                Filed(
                    kind=kind,
                    asset_id=asset_id,
                    target_id=str(row["id"]),
                    names=_folded(row["name"], *([] if is_username else aliases)),
                    sites=_folded(row["site"], *aliases) if is_username else frozenset(),
                    site_id=None if row["site_id"] is None else str(row["site_id"]),
                    source=source,
                    decided_at=None if row["decided_at"] is None else int(row["decided_at"]),
                    post_id=None if row["post_id"] is None else str(row["post_id"]),
                    box_id=None if row["box_id"] is None else str(row["box_id"]),
                )
            )
    return filed


def filed_rows(rows: Sequence[Filed]) -> list[dict[str, object]]:
    """These rows as an Undo keeps them (`put_back_on`): each join row whole."""
    kept: list[dict[str, object]] = []
    for one in rows:
        table, column = _FILED_IN[one.kind]
        row: dict[str, object] = {
            "asset_id": one.asset_id,
            column: one.target_id,
            "source": one.source,
            "decided_at": one.decided_at,
            "box_id": one.box_id,
        }
        if one.kind == "username":
            row["post_id"] = one.post_id
        kept.append({"table": table, "row": row})
    return kept


async def take_off_filed_on(connection: Connection, rows: Sequence[Filed]) -> list[Filed]:
    """Take these rows off their files, each only while it still carries its source. The rows that
    went, in order.

    No refusal is recorded for a person, as `detach_person_on` records none: this undoes a decision
    that should not have been made, and a refusal would stop a later answer that is right. What
    each row was filed under has its stamps bumped and is told, inside the caller's write, so a
    file that leaves a shared tag or a hidden person's page leaves it for whoever was looking.
    """
    went: list[Filed] = []
    told = NOBODY
    for one in rows:
        cursor = await connection.execute(
            _TAKE_OFF_FILED[one.kind], (one.asset_id, one.target_id, one.source)
        )
        if not cursor.rowcount:
            continue
        went.append(one)
        if one.kind == "person":
            told |= await bump_stamps_for_object(connection, ObjectType.PERSON, one.target_id)
        elif one.kind == "tag":
            told |= await bump_stamps_for_object(connection, ObjectType.TAG, one.target_id)
        elif one.site_id is not None:
            told |= await bump_stamps_for_object(connection, ObjectType.SITE, one.site_id)
    if went:
        announce(told, About.LIBRARY)
    return went


#: The table a shell's own row is in, by the kind a caller names.
SHELL_TABLES: Mapping[str, str] = {
    "person": "people",
    "username": "usernames",
    "site": "sites",
    "tag": "tags",
}

#: Everything in this library that points at a person, a username, a Site or a tag, and what a
#: shell may let go with it. `HOLDS` holds the row: a file, a face somebody confirmed or Sift recognized in a file,
#: a username still filed, a folder read as hers, an opinion, a download, a studio somebody
#: answered. Anything else is what a stash-box brings with a row it made, and goes with the row
#: and comes back with its Undo: every row of that table (`None`), or the rows a condition picks
#: (a starter face is a picture her box keeps of her; a face somebody confirmed holds her).
#:
#: A Site is held by a child Site, a username still on it, a tag, a connection, a person's link, a
#: download, an opinion or a watermark read; a tag by anything it is on, a child tag or an opinion.
#: A Site's nameless row (what files are filed under when no username is known) goes with the Site
#: once nothing is filed under it (`_GOES_WITH`).
#:
#: Held to the database by a test (`test_a_refusal_takes_back_what_it_filed`): a table added
#: tomorrow that points at one of these fails it until it is placed here, so nothing holding a row
#: is let go by omission.
HOLDS = "holds"
POINTING_AT: Mapping[str, tuple[tuple[str, str, str | None], ...]] = {
    "people": (
        ("asset_people", "person_id", HOLDS),
        ("asset_person_refusals", "person_id", HOLDS),
        ("downloads", "person_id", HOLDS),
        ("face_asset_people", "person_id", HOLDS),
        ("face_confirmations", "person_id", HOLDS),
        ("face_pile_proposals", "person_id", HOLDS),
        ("face_references", "person_id", "origin = 'seed'"),
        ("face_rejected", "person_id", HOLDS),
        ("face_rejections", "person_id", HOLDS),
        ("face_starter_refusals", "person_id", None),
        ("face_tracks", "person_id", HOLDS),
        ("folder_claims", "person_id", HOLDS),
        ("folder_people", "person_id", HOLDS),
        ("folder_refusals", "person_id", HOLDS),
        ("pack_entries", "claimed_person_id", HOLDS),
        ("people_aliases", "person_id", None),
        ("people_links", "person_id", None),
        ("person_stash_box_links", "person_id", None),
        ("person_tags", "person_id", HOLDS),
        ("person_user_state", "person_id", HOLDS),
        ("shoot_proposals", "person_id", HOLDS),
        ("usernames", "person_id", HOLDS),
    ),
    "usernames": (
        ("asset_usernames", "username_id", HOLDS),
        ("creator_studios", "username_id", HOLDS),
        ("site_art", "username_id", None),
    ),
    "sites": (
        ("download_sites", "site_id", HOLDS),
        ("downloads", "site_id", HOLDS),
        ("people_links", "site_id", HOLDS),
        ("site_aliases", "site_id", None),
        ("site_connections", "site_id", HOLDS),
        ("site_links", "site_id", None),
        ("site_stash_box_links", "site_id", None),
        ("site_tags", "site_id", HOLDS),
        ("site_user_state", "site_id", HOLDS),
        ("sites", "parent_id", HOLDS),
        (
            "usernames",
            "site_id",
            "name = '' AND NOT EXISTS"
            " (SELECT 1 FROM asset_usernames x WHERE x.username_id = usernames.id)",
        ),
        ("watermark_reads", "site_id", HOLDS),
    ),
    "tags": (
        ("asset_tags", "tag_id", HOLDS),
        ("collection_tags", "tag_id", HOLDS),
        ("loop_tags", "tag_id", HOLDS),
        ("person_tags", "tag_id", HOLDS),
        ("photo_set_tags", "tag_id", HOLDS),
        ("site_tags", "tag_id", HOLDS),
        ("tag_aliases", "tag_id", None),
        ("tag_stash_box_links", "tag_id", None),
        ("tag_user_state", "tag_id", HOLDS),
        ("tags", "parent_id", HOLDS),
    ),
}

#: A brought row a delete would only clear, removed with its shell instead: a Site's nameless row
#: names nothing once its Site is gone, so it goes, and its Undo puts it back after the Site.
_GOES_WITH: Mapping[tuple[str, str], str] = {
    ("usernames", "site_id"): "DELETE FROM usernames WHERE id = ?",
}

#: What holds a Site or a tag and is no foreign key: a grant names its object by kind and id. The
#: grants are the access layer's own table, there before any of these rows can be, so it is asked
#: without first looking for the table as `_HOLDING` does.
_ALSO_HOLDING: Mapping[str, tuple[tuple[str, str], ...]] = {
    "sites": (
        (
            "acl_grants",
            "SELECT 1 FROM acl_grants WHERE object_type = 'site' AND object_id = ? LIMIT 1",
        ),
    ),
    "tags": (
        (
            "acl_grants",
            "SELECT 1 FROM acl_grants WHERE object_type = 'tag' AND object_id = ? LIMIT 1",
        ),
    ),
}

#: Whether anything that holds it is there, per table: one statement each, written out of the
#: list above at import (`splice`), so nothing that arrives at run time reaches the text.
_HOLDING: Mapping[str, tuple[tuple[str, str], ...]] = {
    table: tuple(
        (
            other,
            splice(
                "SELECT 1 FROM {{TABLE}} WHERE {{COLUMN}} = ?{{UNLESS}} LIMIT 1",
                TABLE=other,
                COLUMN=column,
                UNLESS="" if rule == HOLDS else f" AND NOT ({rule})",
            ),
        )
        for other, column, rule in pointing
        if rule is not None
    )
    for table, pointing in POINTING_AT.items()
}

#: What a shell's box brought, whole, per table: the rows its Undo puts back.
_BROUGHT_ROWS: Mapping[str, tuple[tuple[str, str, str], ...]] = {
    table: tuple(
        (
            other,
            column,
            splice("SELECT * FROM {{TABLE}} WHERE {{COLUMN}} = ?", TABLE=other, COLUMN=column),
        )
        for other, column, rule in pointing
        if rule != HOLDS
    )
    for table, pointing in POINTING_AT.items()
}

_SHELL_ROW: Mapping[str, str] = {
    "people": "SELECT * FROM people WHERE id = ?",
    "usernames": "SELECT * FROM usernames WHERE id = ?",
    "sites": "SELECT * FROM sites WHERE id = ?",
    "tags": "SELECT * FROM tags WHERE id = ?",
}
_REMOVE_SHELL: Mapping[str, str] = {
    "people": "DELETE FROM people WHERE id = ?",
    "usernames": "DELETE FROM usernames WHERE id = ?",
    "sites": "DELETE FROM sites WHERE id = ?",
    "tags": "DELETE FROM tags WHERE id = ?",
}

#: Whether the row is one a source could have made and nobody has written into since: a person, a
#: Site or a tag a box made, with no note or description; a NAMED username with no number learned
#: for it (a Site's nameless row is the Site's own, never a shell). A cover, a name or a link
#: somebody set is an act on the ledger, which `holds_nothing_on` reads.
_SHELL_ITSELF: Mapping[str, str] = {
    "person": "SELECT 1 FROM people WHERE id = ? AND created_by_kind = 'box' AND notes IS NULL",
    "username": "SELECT 1 FROM usernames WHERE id = ? AND name <> '' AND number IS NULL",
    "site": "SELECT 1 FROM sites WHERE id = ? AND created_by_kind = 'box' AND notes IS NULL",
    "tag": "SELECT 1 FROM tags WHERE id = ? AND created_by_kind = 'box' AND description IS NULL",
}

#: Whether a person ever did something to it, off the ledger: a link made by hand, a cover or a note
#: set, a name typed. Each sought on its own index (`ix_workbench_subject`, `ix_workbench_object`).
_TOUCHED_BY_A_PERSON = (
    "SELECT 1 FROM workbench_decision_subjects s JOIN workbench_decisions d"
    " ON d.id = s.decision_id WHERE s.kind = ? AND s.subject_id = ? AND d.actor_kind = 'user'"
    " LIMIT 1",
    "SELECT 1 FROM workbench_decisions WHERE object_kind = ? AND object_id = ?"
    " AND actor_kind = 'user' LIMIT 1",
)

#: Every foreign key in this library that points at one table: the table-valued pragma, with the
#: table bound, so a schema read is never a statement built from a name.
POINTERS_AT = """
SELECT m.name AS tbl, k."from" AS col, upper(k.on_delete) AS on_delete
  FROM sqlite_master m JOIN pragma_foreign_key_list(m.name) k
 WHERE m.type = 'table' AND k."table" = ?
 ORDER BY m.name, k."from"
"""
_HERE = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"
_COLUMNS = "SELECT name, pk FROM pragma_table_info(?)"
_KEYS = 'SELECT "from" AS col, "table" AS parent, "to" AS target, upper(on_delete) AS on_delete FROM pragma_foreign_key_list(?)'


async def _here(connection: Connection, table: str) -> bool:
    return await (await connection.execute(_HERE, (table,))).fetchone() is not None


async def holds_nothing_on(connection: Connection, kind: str, row_id: str) -> bool:
    """Whether this person, username, Site or tag is a shell: made by a source, holding nothing.

    Nothing but what the box itself brought points at it (`POINTING_AT`), and no person has ever
    done anything to it (the ledger). Asked after the filings have come off, so a username still
    filed under another file holds, and a person a username still names holds.
    """
    table = SHELL_TABLES.get(kind)
    if table is None or not row_id:
        return False
    if await (await connection.execute(_SHELL_ITSELF[kind], (row_id,))).fetchone() is None:
        return False
    if await _held(connection, table, row_id):
        return False
    if await _here(connection, "workbench_decisions"):
        for statement in _TOUCHED_BY_A_PERSON:
            if await (await connection.execute(statement, (kind, row_id))).fetchone() is not None:
                return False
    return True


async def _held(connection: Connection, table: str, row_id: str) -> bool:
    for other, statement in _HOLDING[table]:
        if not await _here(connection, other):
            continue
        if await (await connection.execute(statement, (row_id,))).fetchone() is not None:
            return True
    for _table, statement in _ALSO_HOLDING.get(table, ()):
        if await (await connection.execute(statement, (row_id,))).fetchone() is not None:
            return True
    return False


async def remove_shell_on(
    connection: Connection, kind: str, row_id: str
) -> list[dict[str, object]]:
    """Remove a shell, and answer with every row its Undo puts back, parent first.

    The row whole, then what its box brought (each row whole, or the pointer the delete clears),
    then the delete, which takes the brought rows with it. Only for a row `holds_nothing_on` has
    just said is a shell.
    """
    table = SHELL_TABLES[kind]
    row = await (await connection.execute(_SHELL_ROW[table], (row_id,))).fetchone()
    if row is None:
        return []
    kept: list[dict[str, object]] = [{"table": table, "row": _kept_row(row)}]
    cleared = {
        (str(one["tbl"]), str(one["col"]))
        for one in await connection.execute_fetchall(POINTERS_AT, (table,))
        if str(one["on_delete"]) == "SET NULL"
    }
    for other, column, statement in _BROUGHT_ROWS[table]:
        if not await _here(connection, other):
            continue
        for one in await connection.execute_fetchall(statement, (row_id,)):
            goes = _GOES_WITH.get((other, column))
            if goes is not None:
                kept.append({"table": other, "row": _kept_row(one)})
                await connection.execute(goes, (one["id"],))
            elif (other, column) in cleared:
                key = await _key_of(connection, other, one)
                kept.append({"table": other, "set": column, "to": row_id, "key": key})
            else:
                kept.append({"table": other, "row": _kept_row(one)})
    await connection.execute(_REMOVE_SHELL[table], (row_id,))
    return kept


def _kept_row(row: Row) -> dict[str, object]:
    """A row as JSON can carry it: bytes as their hex under one key, everything else as it is."""
    out: dict[str, object] = {}
    for key in row.keys():  # noqa: SIM118 (a row is not a dict; `keys` is how it names its columns)
        value = row[key]
        if isinstance(value, (bytes, bytearray, memoryview)):
            out[str(key)] = {"$hex": bytes(value).hex()}
        else:
            out[str(key)] = value
    return out


def _value_of(value: object) -> object:
    if isinstance(value, dict) and set(value) == {"$hex"}:
        return bytes.fromhex(str(value["$hex"]))
    return value


async def _columns_of(connection: Connection, table: str) -> dict[str, int]:
    """A table's columns, each with where it sits in the primary key (0: not in it)."""
    return {
        str(one["name"]): int(one["pk"])
        for one in await connection.execute_fetchall(_COLUMNS, (table,))
    }


async def _key_of(connection: Connection, table: str, row: Row) -> dict[str, object]:
    """The primary key of a row, by its columns."""
    return {name: row[name] for name, at in (await _columns_of(connection, table)).items() if at}


#: Every table an Undo here may write into: the three join tables, the shells' own and what their
#: box brought. A kept row naming any other is skipped.
_PUT_BACK_INTO = frozenset(
    {*(table for table, _ in _FILED_IN.values()), *SHELL_TABLES.values()}
    | {other for pointing in POINTING_AT.values() for other, _, rule in pointing if rule != HOLDS}
)
#: The same tables, each by its own entry: a statement takes the name from here, never from a row.
_ALLOWED_TABLE: Mapping[str, str] = {table: table for table in _PUT_BACK_INTO}


async def _parents_there(connection: Connection, table: str, row: dict[str, object]) -> bool:
    """Whether every row this one points at is still there. A pointer that may be cleared is
    cleared where its row has gone (a cover whose file was removed since); any other means the row
    cannot come back (a file deleted since has nothing to put a person back on)."""
    for key in await connection.execute_fetchall(_KEYS, (table,)):
        column = str(key["col"])
        value = row.get(column)
        if value is None:
            continue
        parent = str(key["parent"])
        target = str(key["target"]) if key["target"] else "id"
        if parent not in _PARENTS or target not in _PARENTS[parent]:
            return False
        if await (await connection.execute(_PARENTS[parent][target], (value,))).fetchone():
            continue
        if str(key["on_delete"]) == "SET NULL":
            row[column] = None
            continue
        return False
    return True


#: Whether a parent row is there, by the table and column a kept row points into. Every parent the
#: tables in `_PUT_BACK_INTO` name, written out; a pointer into any other refuses the row, which a
#: test holds to the database (`test_a_refusal_takes_back_what_it_filed`).
_PARENT_TABLES = (
    "assets",
    "face_packs",
    "face_tracks",
    "people",
    "sites",
    "stash_boxes",
    "tags",
    "usernames",
    "users",
)
_PARENTS: Mapping[str, Mapping[str, str]] = {
    table: {"id": splice("SELECT 1 FROM {{TABLE}} WHERE id = ? LIMIT 1", TABLE=table)}
    for table in _PARENT_TABLES
}


async def put_back_on(connection: Connection, kept: Sequence[Mapping[str, object]]) -> int:
    """Put back the rows a take-back kept, in the order kept. How many rows came back.

    A row is written only where it is missing and everything it points at is still there; a
    pointer a delete cleared is set again only where it is still clear. So an Undo pressed after
    somebody filed the same person again, or after a file was removed, puts back what it can and
    overwrites nothing. A person or username put back keeps the moment it was last edited: its
    names and links coming back are not an edit of it.

    The one place a statement here is written at run time, because a kept row holds the columns it
    held. No name in it is the row's own text: the table is the entry of the fixed allow-list
    `_PUT_BACK_INTO` that the row's table equals, and every column is a name from that table's own
    column list (`pragma_table_info`) that the row also holds. A name matching neither stops the row
    before any statement is written; every value is bound.
    """
    put = 0
    edited: list[tuple[str, object, object]] = []
    for one in kept:
        table = _ALLOWED_TABLE.get(str(one.get("table") or ""))
        if table is None:
            continue
        columns = await _columns_of(connection, table)
        held = one.get("row")
        if isinstance(held, Mapping):
            row = {name: _value_of(held[name]) for name in columns if name in held}
            if not row or not await _parents_there(connection, table, row):
                continue
            names = ", ".join(f'"{name}"' for name in row)
            marks = ", ".join("?" for _ in row)
            statement = f'INSERT OR IGNORE INTO "{table}" ({names}) VALUES ({marks})'  # noqa: S608
            # The table from `_ALLOWED_TABLE`, the columns from its own column list, the values bound.
            # nosemgrep: sift-no-string-built-sql
            cursor = await connection.execute(statement, tuple(row.values()))
            put += int(cursor.rowcount or 0)
            if cursor.rowcount and table in SHELL_TABLES.values() and "edited_at" in row:
                edited.append((table, row.get("id"), row["edited_at"]))
            continue
        key = one.get("key")
        if not isinstance(key, Mapping) or not key:
            continue
        column = next((name for name in columns if name == one.get("set")), None)
        by = [name for name in columns if name in key]
        if column is None or len(by) != len(key):
            continue
        where = " AND ".join(f'"{name}" = ?' for name in by)
        statement = f'UPDATE "{table}" SET "{column}" = ? WHERE {where} AND "{column}" IS NULL'  # noqa: S608
        # The table from `_ALLOWED_TABLE`, the columns from its own column list, the values bound.
        # nosemgrep: sift-no-string-built-sql
        cursor = await connection.execute(statement, (one.get("to"), *(key[name] for name in by)))
        put += int(cursor.rowcount or 0)
    for table, row_id, at in edited:
        await connection.execute(_KEEP_EDITED[table], (at, row_id))
    return put


_KEEP_EDITED: Mapping[str, str] = {
    "people": "UPDATE people SET edited_at = ? WHERE id = ?",
    "usernames": "UPDATE usernames SET edited_at = ? WHERE id = ?",
    "sites": "UPDATE sites SET edited_at = ? WHERE id = ?",
    "tags": "UPDATE tags SET edited_at = ? WHERE id = ?",
}


_KEEP_FOR_UNDO = "INSERT OR REPLACE INTO undo_rows (receipt_id, seq, row) VALUES (?, ?, ?)"
_KEPT_FOR_UNDO = "SELECT row FROM undo_rows WHERE receipt_id = ? ORDER BY seq"
_FORGET_KEPT = "DELETE FROM undo_rows WHERE receipt_id = ?"


async def keep_for_undo_on(
    connection: Connection, receipt_id: str, kept: Sequence[Mapping[str, object]]
) -> None:
    """Keep what an Undo of this receipt puts back, in its order. See the section's note."""
    await connection.executemany(
        _KEEP_FOR_UNDO,
        [(receipt_id, at, json.dumps(one)) for at, one in enumerate(kept)],
    )


async def kept_for_undo_on(connection: Connection, receipt_id: str) -> list[dict[str, object]]:
    """What a receipt kept for its Undo, in order. Empty where it kept nothing or was undone."""
    out: list[dict[str, object]] = []
    for row in await connection.execute_fetchall(_KEPT_FOR_UNDO, (receipt_id,)):
        try:
            held = json.loads(str(row["row"]))
        except ValueError:
            continue
        if isinstance(held, dict):
            out.append(held)
    return out


async def forget_kept_on(connection: Connection, receipt_id: str) -> None:
    """Let go of what a receipt kept, once its Undo has put it back."""
    await connection.execute(_FORGET_KEPT, (receipt_id,))


#: What a receipt kept goes with the receipt, whatever removes it (the ledger's deliberate forget
#: is the one door today), in the same statement's transaction: a trigger on the ledger's table,
#: so a door added tomorrow cannot leave kept rows behind by not knowing they exist.
_UNDO_ROWS_GO_WITH_THE_RECEIPT = (
    "CREATE TRIGGER IF NOT EXISTS undo_rows_go_with_their_receipt"
    " AFTER DELETE ON workbench_decisions"
    " BEGIN DELETE FROM undo_rows WHERE receipt_id = OLD.id; END"
)


async def _keep_undo_rows_with_their_receipts(connection: Connection) -> None:
    """Every boot: the trigger above, wherever both tables are there.

    An invariant and not a step, because the ledger's table is another component's: a rebuild of
    it takes its triggers with it, and only a boot-time check puts this one back.
    """
    if await _here(connection, "workbench_decisions") and await _here(connection, "undo_rows"):
        await connection.execute(_UNDO_ROWS_GO_WITH_THE_RECEIPT)


register_schema_invariant("catalog_undo_rows_with_receipts", _keep_undo_rows_with_their_receipts)
