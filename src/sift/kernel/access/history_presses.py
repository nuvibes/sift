# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who pressed the passes over a file, the line each press says where no pass line claims it, and
the press marks the record keeps for the feed's fold."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from typing import Final

from sift.kernel import presses
from sift.kernel.access import sentences as say
from sift.kernel.access.history_actors import _names_of, _Who
from sift.kernel.access.history_events import (
    _FOLD_KEY,
    _SETTING_KEY,
    FEED_FOLD_GAP,
    FEED_SITTING_GAP,
    LedgerEvent,
    presses_of_asset,
)
from sift.kernel.access.history_line import Actor, Event, by_of
from sift.kernel.access.sentences import SIFT
from sift.kernel.access.viewer import Viewer
from sift.kernel.db import Connection, Database, point_read
from sift.kernel.sql_splice import splice

#: How far outside a press's stretch a pass's own row may be dated and still be that press's work,
#: in seconds: the clock on a machine can step backwards by a few seconds, and a pass may date
#: its row a moment before the worker took its job.
PRESS_SLACK = 60


@dataclass(frozen=True, slots=True)
class Press:
    """One press of a pass over one file, as the ledger keeps it (`kernel.presses`): the act's id,
    who pressed, the passes the job ran, and the stretch its work ran in."""

    id: str
    user_id: str
    passes: tuple[str, ...]
    began: int
    ended: int


def press_of(event: LedgerEvent) -> Press | None:
    """The press a `pressed` act records, or None for an act whose payload says no pass."""
    payload = say.payload_of(event.payload)
    passes = payload.get(presses.PASSES)
    began = payload.get(presses.BEGAN)
    if event.actor_id is None or not isinstance(passes, list):
        return None
    named = tuple(one for one in passes if isinstance(one, str))
    if not named:
        return None
    return Press(
        id=event.id,
        user_id=event.actor_id,
        passes=named,
        began=min(began, event.at) if isinstance(began, int) else event.at,
        ended=event.at,
    )


@dataclass(frozen=True, slots=True)
class Pressers:
    """Who pressed which pass on one file, as one reader may be told it. Empty: nobody pressed any.

    A pass's line names its presser where the pass's own row was written inside the stretch the
    pressed job ran in (`of`); a row outside it reads as Sift. Who is said by `_Who`. `claimed` is
    the presses a pass line has said so far; the rest are lines of their own (`press_lines`).
    """

    presses: Sequence[Press] = ()
    who: _Who | None = None
    claimed: set[str] = field(default_factory=set)

    def of(self, said_by: str, at: int | None) -> tuple[Actor, str | None] | None:
        """Who pressed the pass whose line is read off `said_by` (a History read, or a ledger act
        as `ledger:<verb>`) and dated `at`, as an actor and a name; None where Sift ran it."""
        if at is None or self.who is None:
            return None
        passes = passes_said_by(said_by)
        found: Press | None = None
        for press in self.presses:
            if passes.isdisjoint(press.passes):
                continue
            if not press.began - PRESS_SLACK <= at <= press.ended + PRESS_SLACK:
                continue
            if found is None or press.ended > found.ended:
                found = press
        if found is None:
            return None
        self.claimed.add(found.id)
        return self.who.of(found.user_id)

    def of_verdict(self, product: str, at: int | None) -> tuple[Actor, str | None] | None:
        """Who pressed the pass that gave up on this file, by the product its verdict names."""
        said_by = verdict_said_by(product)
        return None if said_by is None else self.of(said_by, at)


#: The answer where nobody pressed anything; safe to share, since nothing is ever added to `claimed`.
NOBODY_PRESSED = Pressers()


async def pressers_of(
    database: Database, viewer: Viewer, asset_id: str, *, present: set[str]
) -> Pressers:
    """Who pressed the passes on this file, read once for the whole pane. Empty where nothing is
    recorded, and where the record is not there at all (a process with no ledger)."""
    if not {"workbench_decisions", "workbench_decision_subjects"} <= present:
        return NOBODY_PRESSED
    found = [press_of(one) for one in await presses_of_asset(database, viewer, asset_id)]
    pressed = [one for one in found if one is not None]
    if not pressed:
        return NOBODY_PRESSED
    names = await _names_of(database, [one.user_id for one in pressed])
    return Pressers(presses=pressed, who=_Who(viewer=viewer, names=names))


def press_lines(pressers: Pressers) -> list[Event]:
    """Every press no pass line said, as a line of its own: "You had Sift look for faces in this
    file". Presses of the same passes by one user within `FEED_FOLD_GAP` fold into one line. A pass's
    row keeps only its latest result, so the line says the press and no more.
    """
    if pressers.who is None:
        return []
    left = sorted(
        (one for one in pressers.presses if one.id not in pressers.claimed),
        key=lambda one: (one.ended, one.id),
    )
    runs: list[list[Press]] = []
    for press in left:
        last = runs[-1][-1] if runs else None
        if (
            last is not None
            and (last.user_id, last.passes) == (press.user_id, press.passes)
            and press.ended - last.ended <= FEED_FOLD_GAP
        ):
            runs[-1].append(press)
        else:
            runs.append([press])
    lines: list[Event] = []
    for run in runs:
        newest = run[-1]
        actor, name = pressers.who.of(newest.user_id)
        lines.append(
            Event(
                at=newest.ended,
                actor=actor,
                actor_name=name,
                kind="pressed",
                pieces=say.pressed_here(by_of(actor, name) or SIFT, newest.passes, len(run)),
            )
        )
    return lines


def by_pressed(pressed: tuple[Actor, str | None] | None) -> tuple[Actor, str | None, str | None]:
    """A pass line's actor, its name and the presser's word for the sentence (`by_of`): Sift's own
    and no word where nobody pressed it."""
    if pressed is None:
        return Actor.SIFT, SIFT, None
    actor, name = pressed
    return actor, name, by_of(actor, name)


#: WHAT A PASS GAVE UP ON for this file, one row per product, so a file a pass could not do reads
#: differently from one nothing tried.
_LEFT_OUT = point_read(
    "history.left_out",
    "SELECT product, transient, at FROM file_verdicts WHERE asset_id = ?",
)

# --- WHAT SAYS THAT A PASS RAN ON A FILE ---------------------------------------------------------
#
# Each pass names the reads, or the ledger's verbs, that say on the file that it ran, its empty
# answer included, so no pass runs in silence (`test_every_pass_says_so_on_a_files_history.py`).
# Keyed by the pass's own key; a read by its registered name, a ledger act as `ledger:<verb>`.

#: The reads and acts that say a pass ran on a file, by the pass.
SAID_ON_A_FILE: Mapping[str, tuple[str, ...]] = {
    # The pictures, as products and as jobs, and the copies Sift makes to play a file.
    "thumbnails": ("history.derivatives", "history.left_out"),
    "previews": ("history.derivatives", "history.left_out"),
    "sprites": ("history.derivatives", "history.left_out"),
    "thumbnail": ("history.derivatives", "history.left_out"),
    "preview": ("history.derivatives", "history.left_out"),
    "sprite": ("history.derivatives", "history.left_out"),
    # A Loop's still is a still of the file at the Loop's moment, kept with the file's pictures.
    "loop_thumbnail": ("history.derivatives",),
    "remux": ("history.derivatives",),
    # The fingerprints: the act `identity.record_fingerprints` records, or the verdict. An arriving
    # file's are part of taking it in and say nothing of their own.
    "fingerprints": ("ledger:scanned", "history.left_out"),
    "fingerprint_file": ("ledger:scanned", "history.left_out"),
    "fingerprint_stash_box": ("ledger:scanned", "history.left_out"),
    # The music fingerprint, made or empty.
    "music": ("history.music_fingerprint",),
    "audio_fingerprint": ("history.music_fingerprint",),
    # AcoustID asked about the file's music: its answer, or the song it named on the file.
    "music_lookup": ("history.music_lookup", "ledger:song_named"),
    # The three looks that identify a file: found, not found, or could not look.
    "faces": ("ledger:face_run", "history.face_scan", "history.left_out"),
    "face_scan": ("ledger:face_run", "history.face_scan", "history.left_out"),
    "meaning": ("history.indexed", "history.left_out"),
    "semantic_describe": ("history.indexed", "history.left_out"),
    "watermarks": ("history.watermark_scan", "history.watermark_read", "history.left_out"),
    "watermark_read": ("history.watermark_scan", "history.watermark_read", "history.left_out"),
    # A file's details read again from disk: Run task's File details, and the arriving file's own
    # first job, whose read is its arrival line.
    "details": ("history.details_read", "history.left_out"),
    "probe": ("history.details_read", "history.left_out"),
    # Enrich on a file: the box's answer applied, nothing matched, or a match waiting.
    "stash_box_scan": ("history.enriched", "history.asked", "history.asked_waiting"),
}


@cache
def passes_said_by(said_by: str) -> frozenset[str]:
    """Every pass whose line is read off this source, by the passes' declaration above: the keys a
    press is recorded under (`kernel.presses`) for the line drawn from it."""
    return frozenset(name for name, sources in SAID_ON_A_FILE.items() if said_by in sources)


def verdict_said_by(product: str) -> str | None:
    """The source a pass's own line is read off, for the product a verdict of it names: the line
    a pass that gave up would have drawn had it done its work. None for a product no pass declares
    (`identity`, the read that tells a file apart), whose verdict is said as Sift's."""
    return next((one for one in SAID_ON_A_FILE.get(product, ()) if one != _LEFT_OUT.name), None)


# --- the press marks: what the feed's fold reads (`history_events`, the fold's key and gaps) ---

#: The longest gap one press of this act's key may hold: a sitting for a setting, else a minute.
_FOLD_GAP = f"CASE WHEN {_SETTING_KEY} IS NOT NULL THEN {FEED_SITTING_GAP} ELSE {FEED_FOLD_GAP} END"

#: The press marks, as columns added to the record: the key and the gap generated from the act's own
#: row, and whether a press opens or closes on it, kept by `PRESS_TRIGGERS`.
PRESS_COLUMNS: Final = (
    (
        "fold_key",
        "ALTER TABLE workbench_decisions ADD COLUMN fold_key TEXT"
        f" GENERATED ALWAYS AS ({_FOLD_KEY}) VIRTUAL",
    ),
    (
        "fold_gap",
        f"ALTER TABLE workbench_decisions ADD COLUMN fold_gap INTEGER"
        f" GENERATED ALWAYS AS ({_FOLD_GAP}) VIRTUAL",
    ),
    ("opens", "ALTER TABLE workbench_decisions ADD COLUMN opens INTEGER NOT NULL DEFAULT 1"),
    ("closes", "ALTER TABLE workbench_decisions ADD COLUMN closes INTEGER NOT NULL DEFAULT 1"),
)

#: The seeks the marks and the feed make: an act's neighbours of its key, a press's opening, the
#: closings newest first, and the closing of a press one of its acts is in.
PRESS_INDEXES: Final = (
    "CREATE INDEX IF NOT EXISTS ix_workbench_fold ON workbench_decisions(fold_key, decided_at, id)",
    "CREATE INDEX IF NOT EXISTS ix_workbench_openings"
    " ON workbench_decisions(fold_key, decided_at, id) WHERE opens = 1",
    "CREATE INDEX IF NOT EXISTS ix_workbench_closings"
    " ON workbench_decisions(decided_at DESC, id DESC) WHERE closes = 1",
    "CREATE INDEX IF NOT EXISTS ix_workbench_closing_keys"
    " ON workbench_decisions(fold_key, decided_at, id) WHERE closes = 1",
)

#: An act's two marks read again from its neighbours of the same key: a press opens on it when no
#: act of the key lies within the gap before it, and closes on it when none lies within the gap
#: after. The neighbour within the gap, if any, is the adjacent one, so this is the window's test.
_MARKED = """
UPDATE workbench_decisions
   SET opens = NOT EXISTS (
         SELECT 1 FROM workbench_decisions p
          WHERE p.fold_key IS workbench_decisions.fold_key
            AND p.decided_at >= workbench_decisions.decided_at - workbench_decisions.fold_gap
            AND (p.decided_at, p.id) < (workbench_decisions.decided_at, workbench_decisions.id)),
       closes = NOT EXISTS (
         SELECT 1 FROM workbench_decisions n
          WHERE n.fold_key IS workbench_decisions.fold_key
            AND n.decided_at <= workbench_decisions.decided_at + workbench_decisions.fold_gap
            AND (n.decided_at, n.id) > (workbench_decisions.decided_at, workbench_decisions.id))"""

#: The act of the key just before and just after a place in the record, leaving one act out.
_BEFORE = """(SELECT p.id FROM workbench_decisions p
          WHERE p.fold_key IS {{ROW}}.fold_key AND p.id <> {{SELF}}.id
            AND (p.decided_at, p.id) < ({{ROW}}.decided_at, {{ROW}}.id)
          ORDER BY p.decided_at DESC, p.id DESC LIMIT 1)"""
_AFTER = """(SELECT n.id FROM workbench_decisions n
          WHERE n.fold_key IS {{ROW}}.fold_key AND n.id <> {{SELF}}.id
            AND (n.decided_at, n.id) > ({{ROW}}.decided_at, {{ROW}}.id)
          ORDER BY n.decided_at, n.id LIMIT 1)"""

#: The columns an act's key, gap and place in the record are read from.
_PLACED_BY = (
    "id, queue, title, payload, decided_at, verb, actor_kind, actor_id, object_kind, object_id"
)

#: The marks kept wherever an act arrives, moves (its key or its moment changes) or goes: the act
#: and the neighbours it had and has.
PRESS_TRIGGERS: Final = (
    (
        "workbench_press_arrives",
        splice(
            "CREATE TRIGGER IF NOT EXISTS workbench_press_arrives AFTER INSERT ON workbench_decisions"
            " BEGIN {{MARKED}} WHERE id = NEW.id OR id = {{BEFORE}} OR id = {{AFTER}}; END",
            MARKED=_MARKED,
            BEFORE=splice(_BEFORE, ROW="NEW", SELF="NEW"),
            AFTER=splice(_AFTER, ROW="NEW", SELF="NEW"),
        ),
    ),
    (
        "workbench_press_moves",
        splice(
            f"CREATE TRIGGER IF NOT EXISTS workbench_press_moves AFTER UPDATE OF {_PLACED_BY}"
            " ON workbench_decisions"
            " WHEN OLD.fold_key IS NOT NEW.fold_key OR OLD.decided_at IS NOT NEW.decided_at"
            " OR OLD.id IS NOT NEW.id"
            " BEGIN {{MARKED}} WHERE id = NEW.id OR id = {{BEFORE}} OR id = {{AFTER}}"
            " OR id = {{WAS_BEFORE}} OR id = {{WAS_AFTER}}; END",
            MARKED=_MARKED,
            BEFORE=splice(_BEFORE, ROW="NEW", SELF="NEW"),
            AFTER=splice(_AFTER, ROW="NEW", SELF="NEW"),
            WAS_BEFORE=splice(_BEFORE, ROW="OLD", SELF="NEW"),
            WAS_AFTER=splice(_AFTER, ROW="OLD", SELF="NEW"),
        ),
    ),
    (
        "workbench_press_goes",
        splice(
            "CREATE TRIGGER IF NOT EXISTS workbench_press_goes AFTER DELETE ON workbench_decisions"
            " BEGIN {{MARKED}} WHERE id = {{BEFORE}} OR id = {{AFTER}}; END",
            MARKED=_MARKED,
            BEFORE=splice(_BEFORE, ROW="OLD", SELF="OLD"),
            AFTER=splice(_AFTER, ROW="OLD", SELF="OLD"),
        ),
    ),
)

#: Every act's marks in one walk, for a record that had none or lost its triggers.
_MARK_ALL = """
UPDATE workbench_decisions SET opens = e.opens, closes = e.closes
  FROM (SELECT id,
               CASE WHEN decided_at - LAG(decided_at) OVER w <= fold_gap THEN 0 ELSE 1 END AS opens,
               CASE WHEN LEAD(decided_at - fold_gap) OVER w <= decided_at THEN 0 ELSE 1 END AS closes
          FROM workbench_decisions
        WINDOW w AS (PARTITION BY fold_key ORDER BY decided_at, id)) AS e
 WHERE workbench_decisions.id = e.id
   AND (workbench_decisions.opens <> e.opens OR workbench_decisions.closes <> e.closes)
"""

#: Each press trigger dropped by name, for one whose text this build no longer writes.
_UNTRIGGERED: Final = {name: f"DROP TRIGGER IF EXISTS {name}" for name, _ddl in PRESS_TRIGGERS}

#: What a key generated from an older rule takes with it: the triggers and indexes that read it.
_UNKEYED: Final = (
    "DROP TRIGGER IF EXISTS workbench_press_arrives",
    "DROP TRIGGER IF EXISTS workbench_press_moves",
    "DROP TRIGGER IF EXISTS workbench_press_goes",
    "DROP INDEX IF EXISTS ix_workbench_fold",
    "DROP INDEX IF EXISTS ix_workbench_openings",
    "DROP INDEX IF EXISTS ix_workbench_closing_keys",
    "ALTER TABLE workbench_decisions DROP COLUMN fold_key",
    "ALTER TABLE workbench_decisions DROP COLUMN fold_gap",
)

#: The columns the key is generated from: a table without them (a test's own) is left alone.
_KEYED_FROM: Final = (
    "verb",
    "queue",
    "actor_kind",
    "actor_id",
    "object_kind",
    "object_id",
    "title",
)


async def keep_presses(connection: Connection) -> None:
    """The press marks where they are missing or out of date, and every act marked again then.

    A step and every boot: a rebuild of the table takes its triggers, and a key whose rule changed
    is generated again (its stored definition no longer holds `_FOLD_KEY`), so the rule is the
    code's and never the database's.
    """
    columns = {
        str(row["name"])
        for row in await connection.execute_fetchall(
            "SELECT name FROM pragma_table_xinfo('workbench_decisions')", ()
        )
    }
    if not set(_KEYED_FROM) <= columns:
        return
    table = await connection.execute_fetchall(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'workbench_decisions'", ()
    )
    defined = " ".join(str(row["sql"]) for row in table)
    if "fold_key" in columns and not (_FOLD_KEY in defined and _FOLD_GAP in defined):
        for statement in _UNKEYED:
            await connection.execute(statement)
        columns -= {"fold_key", "fold_gap"}
    added = False
    for column, ddl in PRESS_COLUMNS:
        if column not in columns:
            await connection.execute(ddl)
            added = True
    for index in PRESS_INDEXES:
        await connection.execute(index)
    present = {
        str(row["name"]): str(row["sql"])
        for row in await connection.execute_fetchall(
            "SELECT name, sql FROM sqlite_master"
            " WHERE type = 'trigger' AND tbl_name = 'workbench_decisions'",
            (),
        )
    }
    for name, ddl in PRESS_TRIGGERS:
        # SQLite keeps the text without its IF NOT EXISTS: one that differs was built by older code.
        if present.get(name) != ddl.replace(" IF NOT EXISTS", "", 1):
            await connection.execute(_UNTRIGGERED[name])
            await connection.execute(ddl)
            added = True
    if added:
        await connection.execute(_MARK_ALL)
