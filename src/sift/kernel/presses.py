# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who pressed a pass over one file: the one record of it, and the one door it is written through.

A pass over a file keeps its own row (`face_scans`, `watermark_scans`, `derivatives` and the rest),
and not one of those rows says whether a person asked for the work. The job that ran it did
(`jobs.requested_by`), and a job row is pruned a week after it settles, so without this record a
file's History could only say "Sift" for a look somebody had pressed for. A column of who pressed
on each pass's table would be eight columns kept true by eight writers; this is one record,
written by what every pass runs under, the worker pool, when a pressed job over one file is done.

## What it writes

One act in the event ledger per press (`ledger.record_event`, the verb `pressed`): the person as
its actor, the file as its subject, and in its payload the passes the job ran and the moment a
worker took it. The act is done at the moment the job is, so its own moment ends the stretch the
work ran in. Every press stays: a pass's own row keeps only its latest result, so a second look
at a file replaces the first one's row, and the act is what keeps the first look on its History.

A History line reads the acts BESIDE the pass's own row: the line names the presser where its row
was written inside a press's stretch (`history_sources.Pressers`), and every press no line claims
is drawn as a line of its own. That keeps two cases honest that a bare "last pressed by" could
not: a pass Sift ran by itself after the press has its newer row outside the stretch, so it reads
as Sift with the press beside it; and a press that found the work already done wrote no row of
its own, so the older line keeps saying who really ran it and the press is still said.

## What it keeps of the user

The id, as every act the ledger keeps: a user removed since keeps the id on the act and loses
the name, and History says "Another user". A user removed while the work ran has no row the act
may name, so that press is not written.

## Who writes it

`JobQueue.complete`, inside the transaction that marks the job done, so a job recorded as done and
a press left unrecorded (or the other way round) cannot happen. Only for a job somebody pressed,
which is a job whose own row names who asked, or one a pressed job of the same family handed out
(`JobQueue.pressed_by`), and only for a job about ONE file (its payload's `asset_id`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from sift.kernel.db import Connection
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, record_event
from sift.kernel.log import get_logger
from sift.kernel.migrations import table_exists
from sift.kernel.vocabulary import LEDGER_QUEUE, Subject

log = get_logger(__name__)

#: The ledger's word for a press (`ledger.VERBS`).
VERB: Final = "pressed"

#: The payload's two keys: the passes the job ran, and the moment a worker took it.
PASSES: Final = "passes"
BEGAN: Final = "began"

#: Whether the file and the user a press names are both still there, which is what lets the act
#: be written at all: the ledger names the file, and its user column is a key to the user.
#:
#: A press over a file deleted before its job was done, or by a user removed while it ran, is a
#: race nobody can lose by being refused here: the transaction this runs in is the one that marks
#: the job done. The one mention of `assets` in this module is a guard on a write, not a read:
#: nothing of the file's row comes back to anybody, and the job it records was scoped by the
#: access layer when it was pressed.
_BOTH_THERE: Final = (
    "SELECT EXISTS (SELECT 1 FROM assets WHERE id = ?)"  # nosemgrep: sift-no-asset-sql-outside-kernel
    " AND EXISTS (SELECT 1 FROM users WHERE id = ?) AS there"
)


@dataclass(frozen=True, slots=True)
class Pressed:
    """A pressed job over one file, as the record wants it: the file, the passes the job ran on it
    (its own type and every product it carried), who pressed, and when its work began."""

    asset_id: str
    passes: tuple[str, ...]
    user_id: str
    started_at: int


def pressed_job(
    job_type: str, payload: Mapping[str, Any], user_id: str | None, started_at: int | None
) -> Pressed | None:
    """What a job records as pressed, or None where it records nothing.

    Nothing where nobody pressed it, and nothing for a job that is not about one file: a page of a
    pass over the library hands its files to jobs of their own, and those are what is recorded.

    The passes are the job's own type and the products a Build task carried (`products`), so a
    Generate task for a file's thumbnail and hover preview says both, by the keys a file's History
    names its passes by (`history_sources.SAID_ON_A_FILE`).
    """
    asset_id = payload.get("asset_id")
    if user_id is None or not isinstance(asset_id, str) or not asset_id:
        return None
    carried = payload.get("products")
    products = [one for one in carried if isinstance(one, str)] if isinstance(carried, list) else []
    passes = tuple(dict.fromkeys((job_type, *products)))
    return Pressed(
        asset_id=asset_id,
        passes=passes,
        user_id=user_id,
        started_at=int(started_at) if started_at is not None else 0,
    )


async def record_pressed(connection: Connection, pressed: Pressed, *, finished_at: int) -> None:
    """Write the press of one job on its file as one act. Inside the caller's transaction.

    A job with no moment it began (a row claimed before the column existed) is recorded as having
    begun when it ended, which is the narrowest stretch the record can honestly claim.
    """
    rows = list(await connection.execute_fetchall(_BOTH_THERE, (pressed.asset_id, pressed.user_id)))
    if not rows or not rows[0]["there"]:
        return
    began = min(pressed.started_at or finished_at, finished_at)
    await record_event(
        connection,
        actor=Actor.user(pressed.user_id),
        verb=VERB,
        subject=Subject(kind="asset", id=pressed.asset_id),
        payload=json.dumps({PASSES: list(pressed.passes), BEGAN: began}),
    )


#: The record a library kept before every press was an act (catalog 84): the latest press of each
#: pass on each file. One read of the whole table, once, in the step that retires it.
_KEPT_BEFORE: Final = (
    "SELECT p.asset_id AS asset_id, p.pass AS pass, p.user_id AS user_id,"
    " p.started_at AS started_at, p.finished_at AS finished_at,"
    " EXISTS (SELECT 1 FROM users u WHERE u.id = p.user_id) AS user_there"
    " FROM pass_presses p ORDER BY p.finished_at, p.asset_id, p.user_id, p.pass"
)

#: The act and its subject, written as `ledger.record_event` writes them, with the press's own
#: moment where the door would read the clock: a press carried over keeps the moment it was done.
_CARRIED: Final = (
    "INSERT INTO workbench_decisions"
    " (id, queue, user_id, title, detail, payload, decided_at, verb, actor_kind, actor_id)"
    " VALUES (?, ?, ?, '', '', ?, ?, ?, 'user', ?)"
)
_CARRIED_SUBJECT: Final = (
    "INSERT OR IGNORE INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
    " VALUES (?, 'asset', ?, NULL)"
)


async def carry_into_the_ledger(connection: Connection) -> int:
    """Each press the old record holds, written as the act it is, and the old record dropped.

    The rows one job wrote (one file, one user, one stretch) are one press with every pass it ran,
    as `record_pressed` writes it. A user removed since keeps the id as the actor and leaves the
    user column empty, which is what the ledger does for every act of a user removed afterwards.
    Returns how many acts it wrote; safe to run again, since the table is gone after the first.
    """
    if not await table_exists(connection, "pass_presses"):
        return 0
    carried = 0
    if await table_exists(connection, "workbench_decisions"):
        rows = await connection.execute_fetchall(_KEPT_BEFORE, ())
        grouped: dict[tuple[str, str, int, int], tuple[list[str], bool]] = {}
        for row in rows:
            key = (
                str(row["asset_id"]),
                str(row["user_id"]),
                int(row["started_at"]),
                int(row["finished_at"]),
            )
            grouped.setdefault(key, ([], bool(row["user_there"])))[0].append(str(row["pass"]))
        for (asset_id, user_id, began, ended), (passes, user_there) in grouped.items():
            act = new_id()
            await connection.execute(
                _CARRIED,
                (
                    act,
                    LEDGER_QUEUE,
                    user_id if user_there else None,
                    json.dumps({PASSES: passes, BEGAN: began}),
                    ended,
                    VERB,
                    user_id,
                ),
            )
            await connection.execute(_CARRIED_SUBJECT, (act, asset_id))
            carried += 1
    await connection.execute("DROP TABLE pass_presses")
    log.info("presses.carried_into_the_ledger", presses=carried)
    return carried
