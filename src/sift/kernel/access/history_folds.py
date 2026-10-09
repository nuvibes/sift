# SPDX-License-Identifier: AGPL-3.0-or-later
"""Folding a thread so one act is one line, matched by receipt id or by source, never by wording."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import replace
from typing import Any

from sift.kernel.access import sentences as say
from sift.kernel.access.history_line import (
    Actor,
    Detail,
    Event,
    FaceAnswer,
    KeptAnswer,
    Link,
    by_of,
    piece_of,
)
from sift.kernel.access.history_receipts import titled
from sift.kernel.access.sentences import SIFT, Line
from sift.kernel.db import Database, Row
from sift.kernel.vocabulary import KEPT_BY_THE_PRESS, LEDGER_QUEUE, VIA_DOWNLOAD


def one_line_per_kept(events: Sequence[Event]) -> list[Event]:
    """One line per press for a stash-box answer kept: the kept line and its receipt are one act."""
    standing: dict[KeptAnswer, tuple[int, str]] = {}
    for at, one in enumerate(events):
        if one.reversed:
            continue
        for answer, how in one.kept:
            held = standing.get(answer)
            if held is None or (one.at or 0) >= (events[held[0]].at or 0):
                standing[answer] = (at, how)
    if not standing:
        return list(events)
    out = list(events)
    gone: set[int] = set()
    for at, one in enumerate(events):
        if one.kind != "kept_mine" or one.answer is None or one.answer not in standing:
            continue
        into, how = standing[one.answer]
        if how == KEPT_BY_THE_PRESS and out[into].stored_words:
            out[into] = replace(out[into], pieces=one.pieces)
        gone.add(at)
    return [one for at, one in enumerate(out) if at not in gone]


_FACE_ANSWER_KINDS = frozenset({"confirmed", "rejected"})


def one_line_per_face_answer(
    events: Sequence[Event], *, reword: Callable[[Event, int], Event] | None = None
) -> list[Event]:
    """One line per press for a Yes or a No on faces: the answer's line and its receipt are one."""
    standing: Counter[FaceAnswer] = Counter()
    for one in events:
        if one.receipt is None or one.reversed:
            continue
        standing.update(one.faces)
    if not standing:
        return list(events)
    out: list[Event] = []
    for one in events:
        if one.receipt is not None or not one.faces or one.kind not in _FACE_ANSWER_KINDS:
            out.append(one)
            continue
        left = 0
        for face in one.faces:
            if standing[face] > 0:
                standing[face] -= 1
            else:
                left += 1
        if left == len(one.faces):
            out.append(one)
        elif left:
            out.append(reword(one, left) if reword is not None else one)
    return out


#: The kinds a receipt writes beside itself in one press; closed, because folding takes a line off
#: the pane.
_FOLDS_INTO_A_DECISION = ("filed", "named", "tagged", "matched")


#: What an attribution's receipt was about, by its line's kind; `matched` and `named` share one
#: object.
_RECEIPT_OBJECT_OF_KIND: Mapping[str, str] = {
    "named": "person",
    "matched": "person",
    "tagged": "tag",
    "filed": "username",
}


def _receipt_of_object(rows: Sequence[Row]) -> dict[tuple[str, str], str]:
    """Which receipt linked each thing on this file, as `(kind, id) -> receipt`; first wins."""
    found: dict[tuple[str, str], str] = {}
    for row in rows:
        if row["object_kind"] is None or row["object_id"] is None:
            continue
        found.setdefault((str(row["object_kind"]), str(row["object_id"])), str(row["id"]))
    return found


def _receipt_for(by_object: Mapping[tuple[str, str], str], kind: str, object_id: str) -> str | None:
    """The receipt that wrote one attribution, or None where the record does not say."""
    return by_object.get((_RECEIPT_OBJECT_OF_KIND[kind], object_id))


# Every thread folds a link row into the oldest standing receipt on that file whose object is the
# linked thing, so the tabs cannot disagree. The unary plus keeps the planner on
# `ix_workbench_subject`.
RECEIPT_OF_A_NAMING = """(SELECT r.id FROM workbench_decision_subjects rs
     JOIN workbench_decisions r ON r.id = rs.decision_id
    WHERE rs.kind = 'asset' AND rs.subject_id = link.asset_id
      AND r.queue <> :ledger AND r.reversed_at IS NULL
      AND +r.object_kind = 'person' AND +r.object_id = link.person_id
    ORDER BY r.decided_at ASC, r.id ASC LIMIT 1)"""

RECEIPT_OF_A_TAGGING = """(SELECT r.id FROM workbench_decision_subjects rs
     JOIN workbench_decisions r ON r.id = rs.decision_id
    WHERE rs.kind = 'asset' AND rs.subject_id = link.asset_id
      AND r.queue <> :ledger AND r.reversed_at IS NULL
      AND +r.object_kind = 'tag' AND +r.object_id = link.tag_id
    ORDER BY r.decided_at ASC, r.id ASC LIMIT 1)"""

RECEIPT_OF_A_FILING = """(SELECT r.id FROM workbench_decision_subjects rs
     JOIN workbench_decisions r ON r.id = rs.decision_id
    WHERE rs.kind = 'asset' AND rs.subject_id = link.asset_id
      AND r.queue <> :ledger AND r.reversed_at IS NULL
      AND +r.object_kind = 'username' AND +r.object_id = link.username_id
    ORDER BY r.decided_at ASC, r.id ASC LIMIT 1)"""

NO_RECEIPT = "NULL"

_RECEIPTS_ON_A_FILE = """
SELECT d.id AS id, d.object_kind AS object_kind, d.object_id AS object_id
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = 'asset' AND s.subject_id = :subject AND d.queue <> :ledger
   AND d.reversed_at IS NULL AND d.object_kind IS NOT NULL
 ORDER BY d.decided_at ASC, d.id ASC
"""


async def receipts_on_a_file(database: Database, asset_id: str) -> dict[tuple[str, str], str]:
    """The file tab's half of the one fold (see `RECEIPT_OF_A_NAMING`)."""
    rows = await database.fetch_all(
        _RECEIPTS_ON_A_FILE, {"subject": asset_id, "ledger": LEDGER_QUEUE}
    )
    return _receipt_of_object(rows)


def drawn_receipts(events: Sequence[Event]) -> set[str | None]:
    """The receipts a pane draws, the only ones a line may fold into."""
    return {one.receipt for one in events if one.kind == "decided"}


Counted = Mapping[str, Any]


def counted_apart_from_receipts(
    rows: Sequence[Row], drawn: Collection[str | None]
) -> list[Counted]:
    """A counted thread's groups with every row a drawn receipt accounts for taken out."""
    groups: dict[tuple[object, object, object], dict[str, Any]] = {}
    for row in rows:
        receipt = row["receipt"]
        if receipt is not None and str(receipt) in drawn:
            continue
        key = (row["source"], row["box"], row["day"])
        at = None if row["at"] is None else int(row["at"])
        one = groups.get(key)
        if one is None:
            groups[key] = {
                "source": row["source"],
                "box": row["box"],
                "day": row["day"],
                "files": int(row["files"]),
                "at": at,
            }
            continue
        one["files"] += int(row["files"])
        if at is not None and (one["at"] is None or at > one["at"]):
            one["at"] = at
    return list(groups.values())


def _merged(decision: Event, absorbed: Event) -> Event:
    """The decision's line, carrying the `via` mark and links of the line folded into it."""
    return replace(
        decision,
        via=decision.via or absorbed.via,
        pieces=_the_carry(decision, absorbed) if absorbed.source == "copy" else absorbed.pieces,
    )


def _the_carry(decision: Event, absorbed: Event) -> Line:
    """A carry's receipt keeps its title, with the carried row's thing placed on its own words."""
    named = absorbed.links[0] if absorbed.links else None
    return titled(decision.what, named)


def _one_line_per_act(events: Sequence[Event]) -> list[Event]:
    """One line for one act: drop a line whose receipt is already on the pane."""
    folded = list(events)
    receipts = {one.receipt: at for at, one in enumerate(folded) if one.kind == "decided"}
    absorbed: set[int] = set()
    for position, event in enumerate(folded):
        if event.kind not in _FOLDS_INTO_A_DECISION or event.receipt is None:
            continue
        into = receipts.get(event.receipt)
        if into is None:
            continue
        folded[into] = _merged(folded[into], event)
        absorbed.add(position)
    return [one for position, one in enumerate(folded) if position not in absorbed]


#: The face acts that already account for a bare naming; `rejected` puts no name anywhere.
_FACE_ACTS_THAT_NAME = ("confirmed", "matched")


def _one_line_per_face_act(events: Sequence[Event]) -> list[Event]:
    """Drop a bare naming that a face act on the same file already accounts for."""
    answered = {
        link.id
        for one in events
        if one.kind in _FACE_ACTS_THAT_NAME
        for link in one.links
        if link.kind == "person"
    }
    if not answered:
        return list(events)
    return [
        one
        for one in events
        if not (
            one.kind == "named"
            and one.source is None
            and any(link.kind == "person" and link.id in answered for link in one.links)
        )
    ]


#: Kinds that are one act when they share a source; a file is filed under one username per press.
_FOLDS_BY_SOURCE = ("named", "tagged")


def _one_press(members: Sequence[Event], line: Line, kind: str, words: str) -> Event:
    """The one line a source press becomes: its sentence, and every name it touched in `detail`."""
    first = members[0]
    if len(members) == 1:
        return replace(first, pieces=line)
    times = [one.at for one in members if one.at is not None]
    links = tuple(link for one in members for link in one.links)
    return replace(
        first,
        at=max(times) if times else None,
        pieces=line,
        detail=(Detail(kind=kind, words=words, links=links),) if links else (),
    )


#: What a box wrote, in the sentence's order; one table so the line and its groups cannot drift.
_BOX_WROTE: tuple[tuple[str, str, Callable[[int], str]], ...] = (
    ("named", "person", say.people),
    ("filed", "site", lambda count: "the site" if count == 1 else f"{count} sites"),
    ("tagged", "tag", say.tags),
)


def _by_the_box(box: Event, taken: Sequence[Event]) -> Event:
    """The stash-box's own line, saying what it wrote and listing it under itself in groups."""
    parts: list[str] = []
    groups: list[Detail] = []
    for act, kind, counted in _BOX_WROTE:
        members = [one for one in taken if one.kind == act]
        if not members:
            continue
        words = counted(len(members))
        parts.append(words)
        links = tuple(link for one in members for link in one.links)
        # A filing whose site has been deleted is counted and not listed.
        if links:
            groups.append(Detail(kind=kind, words=words, links=links))
    # Then the fields the rows cannot account for, from the run's own record.
    parts.extend(box.wrote)
    return replace(
        box,
        pieces=say.recognized(str(box.actor_name), box.by_hand, parts, grade=box.grade),
        detail=box.detail + tuple(groups),
    )


def _one_line_per_download(events: Sequence[Event]) -> list[Event]:
    """Drop the filing a download wrote, which its download line already says."""
    if not any(one.kind == "downloaded" for one in events):
        return list(events)
    arrived = any(one.kind == "downloaded" and one.landed for one in events)
    return [
        one
        for one in events
        if not (one.kind == "filed" and one.source == VIA_DOWNLOAD)
        and not (arrived and one.kind == "added")
    ]


EPISODE_GAP = 600


def episodes(moments: Sequence[int | None]) -> list[list[int]]:
    """The positions of `moments` in time order, split into episodes at `EPISODE_GAP`."""
    order = sorted(range(len(moments)), key=lambda at: (moments[at] is not None, moments[at] or 0))
    runs: list[list[int]] = []
    last: int | None = None
    for position in order:
        moment = moments[position]
        opens = (
            not runs
            or (moment is None) != (last is None)
            or (moment is not None and last is not None and moment - last > EPISODE_GAP)
        )
        if opens:
            runs.append([])
        runs[-1].append(position)
        last = moment
    return runs


def _one_processed_line(events: Sequence[Event]) -> list[Event]:
    """The file's routine lines as one "Sift processed this file" line per episode."""
    routine = [one for one in events if one.routine]
    kept = [one for one in events if not one.routine]
    for run in episodes([one.at for one in routine]):
        steps = [routine[position] for position in run]
        if len(steps) < 2:
            kept.extend(steps)
            continue
        links = tuple(Link(kind="step", id=str(at), name=one.what) for at, one in enumerate(steps))
        kept.append(
            Event(
                at=steps[0].at,
                actor=Actor.SIFT,
                actor_name=SIFT,
                kind="ready",
                pieces=say.processed(),
                detail=(Detail(kind="step", words=say.processed_steps(len(links)), links=links),),
            )
        )
    return kept


def _group_of(event: Event) -> tuple[str, str, str]:
    """The group a row folds into: its kind, source word and, for a stash-box, the box."""
    box = (event.actor_name or "") if event.via == "stash" else ""
    return event.kind, event.source or "", box


def _taken_by_boxes(events: Sequence[Event]) -> dict[int, list[Event]]:
    """The stash-box rows each box line absorbs, keyed by that line's `id`."""
    boxes = [one for one in events if one.kind == "enriched"]
    by_name = {one.actor_name: one for one in boxes if one.actor_name}

    def box_for(event: Event) -> Event | None:
        if event.via != "stash":
            return None
        if event.actor_name:
            return by_name.get(event.actor_name)
        return boxes[0] if len(boxes) == 1 else None

    taken: dict[int, list[Event]] = {}
    for event in events:
        if event.kind in _FOLDS_BY_SOURCE and (box := box_for(event)) is not None:
            taken.setdefault(id(box), []).append(event)
    for one in events:
        if one.kind == "filed" and (box := box_for(one)) is not None:
            taken.setdefault(id(box), []).append(one)
    return taken


def _said_by(events: Sequence[Event]) -> list[Event]:
    """One line per pass per act; each group keeps its first member's place for the stable sort."""
    taken = _taken_by_boxes(events)
    gone = {id(one) for absorbed in taken.values() for one in absorbed}
    groups: dict[tuple[str, str, str], list[Event]] = {}
    for event in events:
        if event.kind in _FOLDS_BY_SOURCE and id(event) not in gone:
            groups.setdefault(_group_of(event), []).append(event)

    written: dict[tuple[str, str, str], Event] = {}
    for (kind, source, _box), members in groups.items():
        # Grouped on the stored word, since `via` has no word for a username or a copy.
        first = members[0]
        by = by_of(first.actor, first.actor_name)
        naming = kind == "named"
        wanted = "person" if naming else "tag"
        things = [piece_of(link) for one in members for link in one.links if link.kind == wanted]
        places = [[link for link in one.links if link.kind == "folder"] for one in members]
        shared = {(place[0].id, place[0].name) for place in places if len(place) == 1}
        folder = (
            piece_of(places[0][0])
            if naming and len(shared) == 1 and all(len(place) == 1 for place in places)
            else None
        )
        words = say.people(len(things)) if naming else say.tags(len(things))
        who: Line | str = (things[0],) if len(things) == 1 else words
        line = (
            say.named_sentence(by, source or None, who, len(things), folder)
            if naming
            else say.tagged_sentence(by, source or None, who, len(things))
        )
        written[(kind, source, _box)] = _one_press(
            members, line, "person" if naming else "tag", words
        )

    kept: list[Event] = []
    for event in events:
        if id(event) in gone:
            continue
        if event.kind == "enriched" and id(event) in taken:
            kept.append(_by_the_box(event, taken[id(event)]))
            continue
        if event.kind not in _FOLDS_BY_SOURCE:
            kept.append(event)
            continue
        key = _group_of(event)
        one_line = written.pop(key, None)
        if one_line is not None:
            kept.append(one_line)
    return kept
