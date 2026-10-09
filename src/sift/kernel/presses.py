# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who pressed a pass over one file, kept as a `pressed` act in the ledger.

Written in the transaction that marks a pressed one-file job done, as job rows are pruned."""

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

VERB: Final = "pressed"

PASSES: Final = "passes"
BEGAN: Final = "began"

#: Whether the file and user still exist, a guard on the write; nothing of the file is read.
_BOTH_THERE: Final = (
    "SELECT EXISTS (SELECT 1 FROM assets WHERE id = ?)"  # nosemgrep: sift-no-asset-sql-outside-kernel
    " AND EXISTS (SELECT 1 FROM users WHERE id = ?) AS there"
)


@dataclass(frozen=True, slots=True)
class Pressed:
    """A pressed one-file job: the file, the passes it ran, who pressed, and when it began."""

    asset_id: str
    passes: tuple[str, ...]
    user_id: str
    started_at: int


def pressed_job(
    job_type: str, payload: Mapping[str, Any], user_id: str | None, started_at: int | None
) -> Pressed | None:
    """What a job records as pressed, or None where nobody pressed it or it is not one file."""
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
    """Write one job's press on its file as one act, inside the caller's transaction."""
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


#: The old per-pass record, read once by the step that retires it.
_KEPT_BEFORE: Final = (
    "SELECT p.asset_id AS asset_id, p.pass AS pass, p.user_id AS user_id,"
    " p.started_at AS started_at, p.finished_at AS finished_at,"
    " EXISTS (SELECT 1 FROM users u WHERE u.id = p.user_id) AS user_there"
    " FROM pass_presses p ORDER BY p.finished_at, p.asset_id, p.user_id, p.pass"
)

#: Written as `record_event` writes them, keeping each press's own moment.
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
    """Write each old press as the act it is and drop the old table; safe to run again."""
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
