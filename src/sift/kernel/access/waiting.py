# SPDX-License-Identifier: AGPL-3.0-or-later
"""The files waiting for a slice's work, as rows of the slice's own table, kept true by triggers
this module writes from a declaration."""

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
    """One slice's question: which files still want its work (`table`), answered by a row in `done`,
    once every `filled` column holds a value."""

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


# --- the templates, filled by `splice` with checked names; `{{FILE}}` is a trigger's row or an
# alias.

#: The rule over file `a`: every named column holds a value, a copy is there to read, no done row.
_RULE = (
    "{{FILLED}} AND {{PRESENT}} AND NOT EXISTS (SELECT 1 FROM {{DONE}} d WHERE d.asset_id = a.id)"
)

_ENTER = (
    "INSERT INTO {{WAITING}} (asset_id) SELECT a.id FROM assets a"
    " WHERE a.id = {{FILE}} AND {{RULE}}"
    " AND NOT EXISTS (SELECT 1 FROM {{WAITING}} w WHERE w.asset_id = a.id)"
)

_LEAVE = (
    "DELETE FROM {{WAITING}} WHERE asset_id = {{FILE}}"
    " AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.id = {{FILE}} AND {{RULE}})"
)

#: AFTER the change, so the rule reads the row as it now is.
_TRIGGER = (
    "CREATE TRIGGER IF NOT EXISTS {{NAME}} AFTER {{EVENT}} ON {{ON}}{{WHEN}} BEGIN {{BODY}}; END"
)

#: The repair's two halves, and what a test compares the table with.
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

#: The triggers one waiting table keeps, by name prefix.
_TRIGGERS_PRESENT = (
    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND substr(name, 1, ?) = ?"
)


# --- composing them


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
        # Only when a named column really moved.
        stem + "file_changed": _trigger(
            stem + "file_changed",
            "UPDATE OF " + ", ".join(spec.filled),
            "assets",
            (leave("NEW.id"), enter("NEW.id")),
            when=_moved(spec.filled),
        ),
        # A file arriving already filled in, such as a restore.
        stem + "file_arrived": _trigger(
            stem + "file_arrived",
            "INSERT",
            "assets",
            (enter("NEW.id"),),
            when=_filled_on("NEW", spec.filled),
        ),
        stem + "copy_arrived": _trigger(
            stem + "copy_arrived", "INSERT", "asset_locations", (enter("NEW.asset_id"),)
        ),
        # Only when the status or the file moved: a scan re-stamps `last_seen_at` on every copy.
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
        stem + "copy_gone": _trigger(
            stem + "copy_gone", "DELETE", "asset_locations", (leave("OLD.asset_id"),)
        ),
        stem + "done": _trigger(
            stem + "done",
            "INSERT",
            spec.done,
            (splice("DELETE FROM {{WAITING}} WHERE asset_id = NEW.asset_id", WAITING=spec.table),),
        ),
        # When the file itself went, the cascade finds no file and puts nothing back.
        stem + "undone": _trigger(stem + "undone", "DELETE", spec.done, (enter("OLD.asset_id"),)),
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


# --- running them


async def _make_triggers(connection: Connection, spec: Waiting) -> None:
    for ddl in _statements(spec).triggers.values():
        await connection.execute(ddl)


async def start(connection: Connection, spec: Waiting) -> None:
    """Bring a waiting table in: its triggers, and its rows filled before visibility comes up."""
    await _make_triggers(connection, spec)
    await connection.execute(_statements(spec).fill_missing)


async def differences(connection: Connection, spec: Waiting) -> list[tuple[str, str]]:
    """Every file the table disagrees with the rule about: ('missing', id) or ('extra', id)."""
    built = _statements(spec)
    missing = await connection.execute_fetchall(built.missing)
    extra = await connection.execute_fetchall(built.extra)
    return [("missing", str(row[0])) for row in missing] + [("extra", str(row[0])) for row in extra]


def _normal(ddl: str) -> str:
    """A trigger's text without the IF NOT EXISTS the engine drops, spacing folded."""
    return " ".join(ddl.replace("IF NOT EXISTS ", "").split())


async def keep_true(connection: Connection, spec: Waiting) -> None:
    """Every boot: the triggers match this build, or they are made again and the rows repaired. An
    unknown trigger is reported, never dropped."""
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
    """A slice declares a table of files waiting for its work, at import."""
    if spec.table in _registered:
        raise ValueError(f"a waiting table named {spec.table!r} is already declared")
    _registered[spec.table] = spec

    async def apply(connection: Connection) -> None:
        await keep_true(connection, spec)

    register_schema_invariant("waiting." + spec.table, apply)
