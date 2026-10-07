# SPDX-License-Identifier: AGPL-3.0-or-later
"""App History's feed, one line per press: the page, what each folded line holds, and Undo all.

Which acts are one press is the record's rule (`history_events`, the fold's key and its gaps); this
reads a page of presses under it, with the vault's answer applied as every read of the record does.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from sift.kernel.access.history_events import (
    _EVENT_COLUMNS,
    _FILTERED,
    DEFAULT_LIMIT,
    NOTHING_HIDDEN,
    LedgerEvent,
    Thing,
    _event,
    _kept,
    subjects_of,
    verdict_of,
)
from sift.kernel.access.viewer import Viewer
from sift.kernel.db import Database, in_clause
from sift.kernel.sql_splice import splice
from sift.kernel.vocabulary import LEDGER_QUEUE

# The acts of the press `{{C}}` closes, as a condition on `d`: its key, from its opening (the newest
# act of the key at or before the closing that a press opens on) to the closing. Seeks on the marks
# the record keeps (`history_presses.PRESS_TRIGGERS`), so a press costs its own acts.
_IN_PRESS = """d.fold_key IS {{C}}.fold_key
     AND (d.decided_at, d.id) <= ({{C}}.decided_at, {{C}}.id)
     AND (d.decided_at, d.id) >= (
           SELECT o.decided_at, o.id FROM workbench_decisions o
            WHERE o.fold_key IS {{C}}.fold_key AND o.opens = 1
              AND (o.decided_at, o.id) <= ({{C}}.decided_at, {{C}}.id)
            ORDER BY o.decided_at DESC, o.id DESC LIMIT 1)"""

# A press is on the feed when the vault and the narrowing leave it an act. Its verb and its queue
# are its key's, so those two narrow on the closing before any act is read.
_SHOWN = """(:verb IS NULL OR {{C}}.verb = :verb)
   AND (:decisions = 0 OR {{C}}.queue <> :ledger)
   AND EXISTS (SELECT 1 FROM workbench_decisions d WHERE {{IN_PRESS}}
{{NOTHING_HIDDEN}}
{{FILTERED}})"""

# Each press on a page as its line: the newest and oldest act of it the feed holds, and how many.
_LINES = """
SELECT (SELECT d.id FROM workbench_decisions d WHERE {{IN_PRESS}}
{{NOTHING_HIDDEN}}
{{FILTERED}}
         ORDER BY d.decided_at DESC, d.id DESC LIMIT 1) AS id,
       (SELECT d.id FROM workbench_decisions d WHERE {{IN_PRESS}}
{{NOTHING_HIDDEN}}
{{FILTERED}}
         ORDER BY d.decided_at, d.id LIMIT 1) AS first,
       (SELECT COUNT(*) FROM workbench_decisions d WHERE {{IN_PRESS}}
{{NOTHING_HIDDEN}}
{{FILTERED}}) AS folded,
       p.fold_key AS fold_key,
       {{TOTAL}} AS total
  FROM page p
 ORDER BY p.decided_at DESC, p.id DESC
"""


def _shown(press: str) -> str:
    """`_SHOWN` for the closing named `press`."""
    return splice(
        _SHOWN, IN_PRESS=_IN_PRESS, C=press, NOTHING_HIDDEN=NOTHING_HIDDEN, FILTERED=_FILTERED
    )


def _lines(total: str) -> str:
    """`_LINES` with the pager's count read by `total`."""
    return splice(
        _LINES,
        IN_PRESS=_IN_PRESS,
        C="p",
        NOTHING_HIDDEN=NOTHING_HIDDEN,
        FILTERED=_FILTERED,
        TOTAL=total,
    )


# One page of presses, newest first by their newest act: a walk down the closings that stops at the
# page, and the count of every press the feed holds for the pager.
_FOLDED_PAGE = splice(
    """
WITH page AS (
  SELECT c.id AS id, c.decided_at AS decided_at, c.fold_key AS fold_key
    FROM workbench_decisions c
   WHERE c.closes = 1 AND {{SHOWN}}
   ORDER BY c.decided_at DESC, c.id DESC
   LIMIT :limit OFFSET :offset
){{LINES}}""",
    SHOWN=_shown("c"),
    LINES=_lines(
        splice(
            "(SELECT COUNT(*) FROM workbench_decisions t WHERE t.closes = 1 AND {{SHOWN}})",
            SHOWN=_shown("t"),
        )
    ),
)

# How many presses, for a page that came back empty (a page past the end carries no total).
_FOLDED_TOTAL = splice(
    "SELECT COUNT(*) AS total FROM workbench_decisions c WHERE c.closes = 1 AND {{SHOWN}}\n",
    SHOWN=_shown("c"),
)

# The presses a narrowing to one kind holds: the closing of each act the feed holds that named a
# thing of that kind, read from the kind's side, so a rare kind costs its own acts.
_KIND_HITS = """
WITH hits AS MATERIALIZED (
  SELECT DISTINCT (SELECT x.id FROM workbench_decisions x
                    WHERE x.fold_key IS d.fold_key AND x.closes = 1
                      AND (x.decided_at, x.id) >= (d.decided_at, d.id)
                    ORDER BY x.decided_at, x.id LIMIT 1) AS id
    FROM workbench_decisions d
   WHERE d.id IN (SELECT s.decision_id FROM workbench_decision_subjects s WHERE s.kind = :kind)
{{NOTHING_HIDDEN}}
{{FILTERED}}
)"""

# `_FOLDED_PAGE` narrowed to one kind.
_FOLDED_KIND_PAGE = splice(
    """{{HITS}},
page AS (
  SELECT c.id AS id, c.decided_at AS decided_at, c.fold_key AS fold_key
    FROM hits h
    JOIN workbench_decisions c ON c.id = h.id
   ORDER BY c.decided_at DESC, c.id DESC
   LIMIT :limit OFFSET :offset
){{LINES}}""",
    HITS=_KIND_HITS,
    LINES=_lines("(SELECT COUNT(*) FROM hits)"),
    NOTHING_HIDDEN=NOTHING_HIDDEN,
    FILTERED=_FILTERED,
)

# `_FOLDED_TOTAL` narrowed to one kind.
_FOLDED_KIND_TOTAL = splice(
    "{{HITS}}\nSELECT COUNT(*) AS total FROM hits\n",
    HITS=_KIND_HITS,
    NOTHING_HIDDEN=NOTHING_HIDDEN,
    FILTERED=_FILTERED,
)

# The acts of each folded line on a page (`:lines`, a JSON list of `[top, key, oldest moment,
# oldest id, newest moment]`), each with its line's newest act as `top`: the acts of the line's key
# from its oldest to its newest, under the same vault and the same narrowing as the page. A seek on
# the key and the moments, so it costs the press and not the record.
_MEMBERS = splice(
    """
WITH lines AS (
  SELECT json_extract(value, '$[0]') AS top, json_extract(value, '$[1]') AS fold_key,
         json_extract(value, '$[2]') AS from_at, json_extract(value, '$[3]') AS from_id,
         json_extract(value, '$[4]') AS to_at
    FROM json_each(:lines)
),
folds AS (
  SELECT d.id AS id, d.decided_at AS at, l.top AS top
    FROM lines l
    JOIN workbench_decisions d
      ON d.fold_key IS l.fold_key AND d.decided_at BETWEEN l.from_at AND l.to_at
   WHERE (d.decided_at, d.id) >= (l.from_at, l.from_id)
     AND (d.decided_at, d.id) <= (l.to_at, l.top)
{{NOTHING_HIDDEN}}
{{FILTERED}}
)""",
    NOTHING_HIDDEN=NOTHING_HIDDEN,
    FILTERED=_FILTERED,
)

# What each folded press was done WITH, one row per thing, with how many acts named it; and how
# many of those acts recorded no task (a backfill says so) and how many are receipts still standing.
_FOLDED_OBJECTS = splice(
    """{{MEMBERS}}
SELECT f.top AS top, d.object_kind AS kind, d.object_id AS id, MAX(d.object_name) AS name,
       COUNT(*) AS acts,
       SUM(CASE WHEN d.actor_id IS NULL THEN 1 ELSE 0 END) AS untold,
       SUM(CASE WHEN d.queue <> :ledger AND d.reversed_at IS NULL THEN 1 ELSE 0 END) AS standing
  FROM folds f
  JOIN workbench_decisions d ON d.id = f.id
 GROUP BY f.top, d.object_kind, d.object_id
""",
    MEMBERS=_MEMBERS,
)

# What each folded press was ABOUT, each thing once, newest first.
_FOLDED_SUBJECTS = splice(
    """{{MEMBERS}}
SELECT f.top AS top, s.kind AS kind, s.subject_id AS id, MAX(s.name) AS name, MAX(f.at) AS at,
       MAX(f.id) AS latest
  FROM folds f
  JOIN workbench_decision_subjects s ON s.decision_id = f.id
 GROUP BY f.top, s.kind, s.subject_id
 ORDER BY at DESC, latest DESC
""",
    MEMBERS=_MEMBERS,
)

# Every act of the press one act belongs to that the feed holds, newest first: what "Undo all" puts
# back. The press is the stretch of the act's key from its opening to the first closing at or after
# the act.
_FOLDED_MEMBERS = splice(
    """
SELECT d.id AS id, d.queue AS queue
  FROM workbench_decisions me
  JOIN workbench_decisions c
    ON c.id = (SELECT x.id FROM workbench_decisions x
                WHERE x.fold_key IS me.fold_key AND x.closes = 1
                  AND (x.decided_at, x.id) >= (me.decided_at, me.id)
                ORDER BY x.decided_at, x.id LIMIT 1)
  JOIN workbench_decisions d ON {{IN_PRESS}}
 WHERE me.id = :event
{{NOTHING_HIDDEN}}
{{FILTERED}}
 ORDER BY d.decided_at DESC, d.id DESC
""",
    IN_PRESS=_IN_PRESS,
    C="c",
    NOTHING_HIDDEN=NOTHING_HIDDEN,
    FILTERED=_FILTERED,
)

# The acts a page of presses is drawn from: each press's newest and, where it folded, its oldest.
_EVENTS_BY_ID = splice(
    "{{COLUMNS}}\n  FROM workbench_decisions d\n WHERE d.id IN (?*)\n",
    COLUMNS=_EVENT_COLUMNS,
)


@dataclass(frozen=True, slots=True)
class FoldedThing:
    """One thing a folded press was done with, and how many of its acts named it."""

    kind: str
    id: str
    name: str | None
    acts: int


@dataclass(frozen=True, slots=True)
class Press:
    """One line of the feed: a press, told by its newest act, and what the rest of it adds.

    `event` is the newest act, which is the line's id, its moment and its receipt; a press of one
    act is that act and nothing else (`folded` 1, the rest empty). A folded press adds its oldest
    act (a setting's "from" is the oldest's), every thing it was done with and every thing it was
    about (each once), and two counts the line may need: the acts that recorded no task (the
    backfill's "before this was recorded") and the receipts still standing (what Undo all has left).
    """

    event: LedgerEvent
    folded: int = 1
    first: LedgerEvent | None = None
    objects: tuple[FoldedThing, ...] = ()
    subjects: tuple[Thing, ...] = ()
    untold: int = 0
    standing: int = 0


def _fold_values(
    viewer: Viewer, kind: str | None, verb: str | None, decisions: bool
) -> dict[str, object]:
    return {
        **verdict_of(viewer),
        "kind": kind,
        "verb": verb,
        "decisions": int(decisions),
        "ledger": LEDGER_QUEUE,
    }


async def presses_recent(
    database: Database,
    viewer: Viewer,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    kind: str | None = None,
    verb: str | None = None,
    decisions: bool = False,
) -> tuple[list[Press], int]:
    """The feed, one line per press, newest first, and how many presses the filtered feed holds.

    See `history_events` for what a press is. The page is ranked in presses, so `offset` counts
    lines: the feed's pager already counted what it held, which is lines. Four reads for a page:
    the presses, their acts, and (only where a press folded) what they were done with and about,
    the last two a seek on the stretch of time each folded press spans.
    """
    values = _fold_values(viewer, kind, verb, decisions)
    page, total = (
        (_FOLDED_PAGE, _FOLDED_TOTAL) if kind is None else (_FOLDED_KIND_PAGE, _FOLDED_KIND_TOTAL)
    )
    rows = [
        dict(row)
        for row in await database.fetch_all(
            page, {**values, "limit": _kept(limit), "offset": max(0, offset)}
        )
    ]
    if not rows:
        totals = await database.fetch_all(total, values)
        return [], int(dict(totals[0])["total"]) if totals else 0
    folded = {str(row["id"]): int(row["folded"]) for row in rows}
    firsts = {str(row["id"]): str(row["first"]) for row in rows if int(row["folded"]) > 1}
    wanted = sorted({*folded, *firsts.values()})
    statement, bound = in_clause(_EVENTS_BY_ID, wanted)
    events = {
        one.id: one for one in (_event(row) for row in await database.fetch_all(statement, bound))
    }
    named = await subjects_of(database, wanted)
    events = {key: replace(one, subjects=tuple(named.get(key, ()))) for key, one in events.items()}
    held = await _held(
        database,
        values,
        [
            [key, row["fold_key"], events[firsts[key]].at, firsts[key], events[key].at]
            for row in rows
            if (key := str(row["id"])) in firsts and key in events and firsts[key] in events
        ],
    )
    presses: list[Press] = []
    for line in rows:
        key = str(line["id"])
        event = events.get(key)
        if event is None:
            continue
        if folded[key] == 1:
            presses.append(Press(event=event))
            continue
        parts = held.get(key, _Held())
        presses.append(
            Press(
                event=event,
                folded=folded[key],
                first=events.get(firsts[key]),
                # The most acts first: the thing a task did most with leads what it opens to.
                objects=tuple(sorted(parts.objects, key=lambda one: -one.acts)),
                subjects=tuple(parts.subjects),
                untold=parts.untold,
                standing=parts.standing,
            )
        )
    return presses, int(rows[0]["total"])


@dataclass(slots=True)
class _Held:
    """What one folded line holds, gathered off its acts: see `Press`."""

    objects: list[FoldedThing] = field(default_factory=list)
    subjects: list[Thing] = field(default_factory=list)
    untold: int = 0
    standing: int = 0


async def _held(
    database: Database, values: dict[str, object], spans: Sequence[list[object]]
) -> dict[str, _Held]:
    """What each folded line on a page was done with and about, by its newest act's id. Each line
    as `_MEMBERS` seeks it: its newest act, its key, and the moment and id of its oldest, then the
    moment of its newest."""
    if not spans:
        return {}
    bound = {**values, "lines": json.dumps(spans)}
    held: dict[str, _Held] = {}
    for row in await database.fetch_all(_FOLDED_OBJECTS, bound):
        parts = held.setdefault(str(row["top"]), _Held())
        parts.untold += int(row["untold"] or 0)
        parts.standing += int(row["standing"] or 0)
        if row["kind"] is not None and row["id"] is not None:
            parts.objects.append(
                FoldedThing(
                    kind=str(row["kind"]),
                    id=str(row["id"]),
                    name=None if row["name"] is None else str(row["name"]),
                    acts=int(row["acts"]),
                )
            )
    for row in await database.fetch_all(_FOLDED_SUBJECTS, bound):
        held.setdefault(str(row["top"]), _Held()).subjects.append(
            Thing(
                kind=str(row["kind"]),
                id=str(row["id"]),
                name=None if row["name"] is None else str(row["name"]),
            )
        )
    return held


async def press_of(
    database: Database,
    viewer: Viewer,
    event_id: str,
    *,
    kind: str | None = None,
    verb: str | None = None,
    decisions: bool = False,
) -> list[tuple[str, str]]:
    """Every act of the press `event_id` belongs to, newest first, with the queue each was taken
    on, as the feed filtered that way folds it: the line somebody pressed Undo all on. Empty where
    this viewer's feed has no such act."""
    rows = await database.fetch_all(
        _FOLDED_MEMBERS, {**_fold_values(viewer, kind, verb, decisions), "event": event_id}
    )
    acts = [(str(row["id"]), str(row["queue"])) for row in rows]
    # An act the feed does not hold is no line's, so its press is nobody's to undo from here.
    return acts if any(one == event_id for one, _queue in acts) else []
