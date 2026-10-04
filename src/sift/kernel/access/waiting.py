# SPDX-License-Identifier: AGPL-3.0-or-later
"""The files waiting for a slice's work, as rows of the slice's own table, kept true by the database.

A slice that does something to every file of some description (fingerprint every file with a
sound track, say) wants the number still to do, per user and hiding what the vault hides.
The visibility component counts per user whatever a slice registers (`register_counted`), but
only over ROWS: a count of "files matching a rule" is a whole-library read on every draw. So the
slice keeps one row per waiting file in a table of its own, and the count is kept over that.

What decides whether a file is waiting reads `assets`, and a slice never writes SQL against the
asset table: that is the access rule, and it has no exceptions for a statement that only reads a
column. So the slice DECLARES the question here and the kernel writes every statement that names
the file's row: the fill, the triggers that keep the table true wherever the question's inputs
move, the comparison with the rule, and the boot check that puts the triggers back when a rebuild
of either table has taken them away.

The declaration is data, not SQL: the slice's waiting table, the slice's table whose row for a
file means the work is done, and the columns of the file's row that must hold a value. Every name
is checked to be a plain identifier, and every statement is composed in this module from those
names and its own constant templates, the way the visibility component composes its triggers. No
statement is built from anything read at run time.

A file is waiting when every named column of its row holds a value, it has a copy that is there to
read (`kernel.content.presence`), and the done-table has no row for it. The triggers apply that rule
wherever its inputs move: a named column changing on a file, a file arriving already filled in, a
copy arriving, going missing, coming back or leaving, a done row arriving by any path, one going,
and one moving to another file. A file leaving the library takes its waiting row by the slice
table's own cascade (its `asset_id` references the file), so nothing here watches a delete of a
file.

THE COPY IS PART OF THE RULE. A file whose every copy is marked missing is work no pass can do
(the Build hands it nothing), so counting it would have a card say thousands of files to fingerprint
while the run it starts hands out hundreds. The rule is the one the Build's page and count read,
spliced from the same fragment.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass

from sift.kernel.content.presence import HAS_A_PRESENT_COPY
from sift.kernel.db import Connection, register_schema_invariant
from sift.kernel.log import get_logger
from sift.kernel.sql_splice import splice

log = get_logger(__name__)

#: A table or column name this module will write into a statement: lower case, a letter first.
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*")


@dataclass(frozen=True)
class Waiting:
    """One slice's question: which files still want its work.

    `table` is the slice's table of waiting files. It has one column, `asset_id`, the primary key,
    referencing `assets(id)` with `ON DELETE CASCADE`; the slice creates it. `done` is the slice's
    table whose row for a file (its `asset_id` column) means the work is done: an answer, empty or
    not. `filled` is the columns of the file's row that must each hold a value before the file is
    waiting at all: the probe having read it, say, and found a sound track.
    """

    table: str
    done: str
    filled: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.filled:
            raise ValueError(f"the waiting table {self.table!r} names no column of the file's row")
        for name in (self.table, self.done, *self.filled):
            if _IDENTIFIER.fullmatch(name) is None:
                raise ValueError(f"{name!r} is not a plain table or column name")
        if len(set(self.filled)) != len(self.filled):
            raise ValueError(f"the waiting table {self.table!r} names a column twice")


# --- the templates ----------------------------------------------------------------------------
#
# Every statement below is one of these, filled by `splice` with names the declaration checked and
# fragments this module composed. `{{FILE}}` is a trigger's own row reference or a table alias.

#: The rule, over the file `a`: every named column holds a value, a copy is there to read, and no
#: done row exists.
_RULE = (
    "{{FILLED}} AND {{PRESENT}} AND NOT EXISTS (SELECT 1 FROM {{DONE}} d WHERE d.asset_id = a.id)"
)

#: The file named by `{{FILE}}` into the table when the rule holds and it is not there yet.
_ENTER = (
    "INSERT INTO {{WAITING}} (asset_id) SELECT a.id FROM assets a"
    " WHERE a.id = {{FILE}} AND {{RULE}}"
    " AND NOT EXISTS (SELECT 1 FROM {{WAITING}} w WHERE w.asset_id = a.id)"
)

#: And out of it when the rule no longer holds.
_LEAVE = (
    "DELETE FROM {{WAITING}} WHERE asset_id = {{FILE}}"
    " AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.id = {{FILE}} AND {{RULE}})"
)

#: One trigger, AFTER the change so the rule reads the row as it now is.
_TRIGGER = (
    "CREATE TRIGGER IF NOT EXISTS {{NAME}} AFTER {{EVENT}} ON {{ON}}{{WHEN}} BEGIN {{BODY}}; END"
)

#: Every file the rule holds for and the table lacks, and every row the table has that the rule
#: does not hold for: the repair's two halves, and what a test compares the table with.
_MISSING = (
    "SELECT a.id AS asset_id FROM assets a WHERE {{RULE}}"
    " AND NOT EXISTS (SELECT 1 FROM {{WAITING}} w WHERE w.asset_id = a.id)"
)
_EXTRA = (
    "SELECT w.asset_id FROM {{WAITING}} w"
    " WHERE NOT EXISTS (SELECT 1 FROM assets a WHERE a.id = w.asset_id AND {{RULE}})"
)
_FILL_MISSING = "INSERT INTO {{WAITING}} (asset_id) {{MISSING}}"
_DROP_EXTRA = "DELETE FROM {{WAITING}} WHERE asset_id IN ({{EXTRA}})"
_DROP_TRIGGER = "DROP TRIGGER IF EXISTS {{NAME}}"

#: The triggers one waiting table keeps, by the prefix every one of their names starts with. The
#: prefix is a parameter, so this statement is a constant.
_TRIGGERS_PRESENT = (
    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND substr(name, 1, ?) = ?"
)


# --- composing them ---------------------------------------------------------------------------


def _filled_on(row: str, columns: tuple[str, ...]) -> str:
    """Every named column of `row` holds a value."""
    return " AND ".join(row + "." + column + " IS NOT NULL" for column in columns)


def _moved(columns: tuple[str, ...]) -> str:
    """Any named column really changed, so a write that leaves them as they were fires nothing."""
    return " OR ".join("NEW." + column + " IS NOT OLD." + column for column in columns)


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
    """Everything one declaration needs, composed once from it."""

    triggers: dict[str, str]
    drop_triggers: tuple[str, ...]
    fill_missing: str
    drop_extra: str
    missing: str
    extra: str
    prefix: str


@functools.cache
def _statements(spec: Waiting) -> _Statements:
    rule = splice(
        _RULE, FILLED=_filled_on("a", spec.filled), PRESENT=HAS_A_PRESENT_COPY, DONE=spec.done
    )

    def enter(file: str) -> str:
        return splice(_ENTER, WAITING=spec.table, FILE=file, RULE=rule)

    def leave(file: str) -> str:
        return splice(_LEAVE, WAITING=spec.table, FILE=file, RULE=rule)

    stem = spec.table + "_"
    triggers = {
        # A file's named columns written again: the rule may have started or stopped holding. Only
        # when one of them really moved, so a re-read that found the same thing writes nothing.
        stem + "file_changed": _trigger(
            stem + "file_changed",
            "UPDATE OF " + ", ".join(spec.filled),
            "assets",
            (leave("NEW.id"), enter("NEW.id")),
            when=_moved(spec.filled),
        ),
        # A file arriving already filled in (a restore, or anything that writes a whole row at
        # once). Here so that the next writer to take that path is counted without knowing to be.
        stem + "file_arrived": _trigger(
            stem + "file_arrived",
            "INSERT",
            "assets",
            (enter("NEW.id"),),
            when=_filled_on("NEW", spec.filled),
        ),
        # A copy arriving: the file may have its first copy that is there to read.
        stem + "copy_arrived": _trigger(
            stem + "copy_arrived", "INSERT", "asset_locations", (enter("NEW.asset_id"),)
        ),
        # A copy marked missing or present again, or moved to another file: both files are decided
        # again. Only when the status or the file really moved: a scan re-stamps `last_seen_at`
        # on every present copy it walks, and that must fire nothing.
        stem + "copy_changed": _trigger(
            stem + "copy_changed",
            "UPDATE OF status, asset_id",
            "asset_locations",
            (
                leave("OLD.asset_id"),
                enter("OLD.asset_id"),
                leave("NEW.asset_id"),
                enter("NEW.asset_id"),
            ),
            when="NEW.status IS NOT OLD.status OR NEW.asset_id IS NOT OLD.asset_id",
        ),
        # A copy forgotten: the file's last copy may have been the one there to read.
        stem + "copy_gone": _trigger(
            stem + "copy_gone", "DELETE", "asset_locations", (leave("OLD.asset_id"),)
        ),
        # A done row arriving, by whichever path: the file is answered for.
        stem + "done": _trigger(
            stem + "done",
            "INSERT",
            spec.done,
            (splice("DELETE FROM {{WAITING}} WHERE asset_id = NEW.asset_id", WAITING=spec.table),),
        ),
        # One going: the file is waiting again if the rule holds. When the FILE is what went, the
        # cascade that took this row finds no file and puts nothing back.
        stem + "undone": _trigger(stem + "undone", "DELETE", spec.done, (enter("OLD.asset_id"),)),
        # A done row moved to another file: both files are decided again, so a writer that starts
        # doing it cannot leave the table behind.
        stem + "done_moved": _trigger(
            stem + "done_moved",
            "UPDATE OF asset_id",
            spec.done,
            (leave("NEW.asset_id"), enter("OLD.asset_id")),
        ),
    }
    missing = splice(_MISSING, RULE=rule, WAITING=spec.table)
    extra = splice(_EXTRA, WAITING=spec.table, RULE=rule)
    return _Statements(
        triggers=triggers,
        drop_triggers=tuple(splice(_DROP_TRIGGER, NAME=name) for name in triggers),
        fill_missing=splice(_FILL_MISSING, WAITING=spec.table, MISSING=missing),
        drop_extra=splice(_DROP_EXTRA, WAITING=spec.table, EXTRA=extra),
        missing=missing,
        extra=extra,
        prefix=stem,
    )


def triggers(spec: Waiting) -> dict[str, str]:
    """The triggers one declaration keeps, by name, as this build writes them."""
    return dict(_statements(spec).triggers)


# --- running them -------------------------------------------------------------------------------


async def _make_triggers(connection: Connection, spec: Waiting) -> None:
    for ddl in _statements(spec).triggers.values():
        await connection.execute(ddl)


async def start(connection: Connection, spec: Waiting) -> None:
    """The step that brings a waiting table in: its triggers, and its rows filled from the rule.

    Called from the slice's own schema step, straight after it creates the table, so the fill
    lands before the visibility component comes up and is counted by its rebuild once rather than
    row by row through its triggers.
    """
    await _make_triggers(connection, spec)
    await connection.execute(_statements(spec).fill_missing)


async def differences(connection: Connection, spec: Waiting) -> list[tuple[str, str]]:
    """Every file the table disagrees with the rule about: ('missing', id) or ('extra', id).
    Empty is the only acceptable answer. A whole-library read, so a check and not a request."""
    built = _statements(spec)
    missing = await connection.execute_fetchall(built.missing)
    extra = await connection.execute_fetchall(built.extra)
    return [("missing", str(row[0])) for row in missing] + [("extra", str(row[0])) for row in extra]


def _normal(ddl: str) -> str:
    """A trigger's text with the IF NOT EXISTS the engine drops taken out, and the spacing
    folded, so what this build writes and what the database kept compare equal when they are."""
    return " ".join(ddl.replace("IF NOT EXISTS ", "").split())


async def keep_true(connection: Connection, spec: Waiting) -> None:
    """Every boot: the triggers say what this build says, or they are made again and the rows are
    repaired from the rule.

    A migration in another component that rebuilds `assets` takes the triggers on it with it,
    silently, and the table then drifts with nothing failing. Cheap when nothing is wrong (one
    read of the schema), and the repair moves only the rows that disagree, so any count kept over
    the table moves by the same triggers any other change moves it by.

    A trigger under this table's prefix that this build does not write is reported and left: this
    module drops only names it composed, never one it read from the database.
    """
    built = _statements(spec)
    rows = await connection.execute_fetchall(_TRIGGERS_PRESENT, (len(built.prefix), built.prefix))
    present = {str(row[0]): _normal(str(row[1])) for row in rows}
    wanted = {name: _normal(ddl) for name, ddl in built.triggers.items()}
    unknown = sorted(name for name in present if name not in wanted)
    if unknown:
        log.error("waiting.unknown_triggers", table=spec.table, names=unknown)
    repaired = sorted(name for name in wanted if present.get(name) != wanted[name])
    if not repaired:
        return
    log.warning("waiting.triggers_repaired", table=spec.table, repaired=repaired)
    for statement in built.drop_triggers:
        await connection.execute(statement)
    await _make_triggers(connection, spec)
    await connection.execute(built.drop_extra)
    await connection.execute(built.fill_missing)


_registered: dict[str, Waiting] = {}


def registered() -> tuple[Waiting, ...]:
    """Every declaration made in this process."""
    return tuple(_registered.values())


def register_waiting(spec: Waiting) -> None:
    """A slice declares a table of files waiting for its work, at import.

    The slice creates the table and calls `start` from its own schema step; this registers the
    boot check that keeps the table's triggers and rows true from then on.
    """
    if spec.table in _registered:
        raise ValueError(f"a waiting table named {spec.table!r} is already declared")
    _registered[spec.table] = spec

    async def apply(connection: Connection) -> None:
        await keep_true(connection, spec)

    register_schema_invariant("waiting." + spec.table, apply)
