# SPDX-License-Identifier: AGPL-3.0-or-later
"""The face lines of a History thread: who a scan found, what Sift recognized, how sure."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from sift.kernel.access import sentences as say
from sift.kernel.access.history_events import (
    _EVENT_COLUMNS,
    NOTHING_HIDDEN,
    LedgerEvent,
    _event,
    _kept,
    verdict_of,
)
from sift.kernel.access.history_folds import _receipt_for
from sift.kernel.access.history_line import Actor, Event, Link, piece_of
from sift.kernel.access.history_presses import Pressers, by_pressed
from sift.kernel.access.history_reads import _TABLES, _count_or_none, _seconds
from sift.kernel.access.sentences import SIFT, Line
from sift.kernel.access.viewer import Viewer
from sift.kernel.db import Database, Row, point_read
from sift.kernel.sql_splice import splice


def _faces_found(rows: Sequence[Row], *, asks: bool = True) -> Line:
    """Who a scan found, in words with a way to each: the named once each, then the faces Sift only
    suggests, then the unnamed as one phrase linked only when they wait in one group. ("", ()) for
    nothing."""
    named: dict[str, str] = {
        str(row["person_id"]): str(row["name"])
        for row in rows
        if row["person_id"] is not None
        and row["name"] is not None
        and row["attribution"] != SUGGESTED
    }
    maybe: dict[str, tuple[str, int]] = {}
    piles: list[str] = []
    unknown = 0
    for row in rows:
        person = None if row["person_id"] is None else str(row["person_id"])
        if person is not None and row["name"] is not None:
            if person in named:
                continue
            if asks:
                name, count = maybe.get(person, (str(row["name"]), 0))
                maybe[person] = (name, count + 1)
                continue
        unknown += 1
        if row["pile_id"] is not None:
            piles.append(str(row["pile_id"]))
    parts: list[Line] = [(say.thing("person", one, name),) for one, name in named.items()]
    parts += [
        say.may_be_faces(say.thing("person", one, name), one, count)
        for one, (name, count) in maybe.items()
    ]
    if unknown:
        where = set(piles)
        if len(where) == 1 and len(piles) == unknown:
            parts.append((say.thing("face_pile", piles[0], say.faces(unknown)),))
        else:
            parts.append(say.said(say.faces(unknown)))
    return say.listed(parts, None)


#: The word a face's attribution carries while Sift only suggests who it is (`face_tracks`).
SUGGESTED = "suggested"


#: What a look for faces came to, as `face_scans` holds it and as the act of each look keeps it.
SCAN_FACTS = (
    "track_count",
    "refused_small",
    "refused_closer",
    "refused_largest",
    "refused_blurred",
    "refused_turned",
    "refused_edge",
)

#: The ledger act a look for faces is recorded under, as the passes' declaration names it.
FACE_RUN_SAID = "ledger:face_run"

#: Every look for faces at one file, newest first, read from the acts since `face_scans` keeps only
#: the last.
_FACE_RUNS_OF_ASSET = splice(
    """
{{COLUMNS}}
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = 'asset' AND s.subject_id = :subject AND d.verb = 'face_run'
{{NOTHING_HIDDEN}}
 ORDER BY d.decided_at DESC, d.id DESC
 LIMIT :limit
""",
    COLUMNS=_EVENT_COLUMNS,
    NOTHING_HIDDEN=NOTHING_HIDDEN,
)


def looked(facts: Mapping[str, object], seen: Line, by: str | None) -> Line:
    """One look's line from its facts (`SCAN_FACTS`); `seen` names who it found."""
    return say.looked_for_faces(
        int(str(facts.get("track_count") or 0)),
        seen,
        small=_count_or_none(facts.get("refused_small")),
        closer=_count_or_none(facts.get("refused_closer")),
        why=say.Refusals(
            largest=_count_or_none(facts.get("refused_largest")),
            blurred=_count_or_none(facts.get("refused_blurred")),
            turned=_count_or_none(facts.get("refused_turned")),
            edge=_count_or_none(facts.get("refused_edge")),
        ),
        by=by,
    )


def _facts_of(run: LedgerEvent) -> Mapping[str, object]:
    try:
        facts = json.loads(run.payload or "{}")
    except ValueError:
        return {}
    return facts if isinstance(facts, dict) else {}


async def face_run_events(
    database: Database,
    viewer: Viewer,
    asset_id: str,
    *,
    seen: Line,
    pressers: Pressers,
    limit: int,
) -> list[Event]:
    """A line for each look for faces at this file; only the newest names who."""
    rows = await database.fetch_all(
        _FACE_RUNS_OF_ASSET, {**verdict_of(viewer), "subject": asset_id, "limit": _kept(limit)}
    )
    events: list[Event] = []
    for index, run in enumerate(_event(row) for row in rows):
        actor, actor_name, by = by_pressed(pressers.of(FACE_RUN_SAID, run.at))
        events.append(
            Event(
                at=run.at,
                actor=actor,
                actor_name=actor_name,
                kind="face_run",
                pieces=looked(_facts_of(run), seen if index == 0 else (), by),
            )
        )
    return events


#: Every appearance on this file Sift attached somebody to on its own, read off the appearance
#: (`ix_face_tracks_asset`).
_FACE_MATCHES = point_read(
    "history.face_matches",
    """
SELECT p.id AS person_id, p.name AS name, t.confidence AS confidence,
       t.attributed_at AS attributed_at
  FROM face_tracks t
  JOIN people p ON p.id = t.person_id
 WHERE t.asset_id = ? AND t.attribution = 'matched'
 ORDER BY t.attributed_at ASC, t.id ASC
""",
)


async def face_matches_of_asset(
    database: Database,
    asset_id: str,
    *,
    tables: Sequence[str],
    by_receipt: Mapping[tuple[str, str], str] | None = None,
) -> list[Event]:
    """What Sift recognized here on its own: one `matched` line per person, at the latest moment,
    with the range of how sure."""
    if "face_tracks" not in tables:
        return []
    #: Keyed on the person, oldest first.
    found: dict[str, tuple[str, list[int | None], list[float | None]]] = {}
    for row in await database.fetch_all(_FACE_MATCHES, (asset_id,)):
        person_id = str(row["person_id"])
        name, moments, sures = found.setdefault(person_id, (str(row["name"]), [], []))
        moments.append(_seconds(row["attributed_at"]))
        sures.append(None if row["confidence"] is None else float(row["confidence"]))
    events: list[Event] = []
    for person_id, (name, moments, sures) in found.items():
        told = [one for one in moments if one is not None]
        events.append(
            Event(
                # None only where every appearance predates the moment column.
                at=max(told) if told else None,
                actor=Actor.SIFT,
                actor_name=SIFT,
                kind="matched",
                # The one face-match builder every screen uses.
                pieces=say.recognized_face(say.thing("person", person_id, name), "here", sures),
                via="faces",
                receipt=_receipt_for(by_receipt or {}, "matched", person_id),
            )
        )
    return events


async def face_sures(
    database: Database, pairs: Sequence[tuple[str, str]]
) -> dict[tuple[str, str], list[float | None]]:
    """How sure Sift was of each (file, person) match, off the appearances themselves: the file
    line's figures."""
    if not pairs:
        return {}
    tables = {str(row["name"]) for row in await database.fetch_all(_TABLES)}
    if "face_tracks" not in tables:
        return {}
    wanted = set(pairs)
    found: dict[tuple[str, str], list[float | None]] = {}
    for asset_id in sorted({asset for asset, _person in wanted}):
        for row in await database.fetch_all(_FACE_MATCHES, (asset_id,)):
            key = (asset_id, str(row["person_id"]))
            if key in wanted:
                sure = None if row["confidence"] is None else float(row["confidence"])
                found.setdefault(key, []).append(sure)
    return found


#: The receipt shape of a face match: Sift, the faces task, a file linked to a person.
def is_face_match(verb: object, object_kind: object, actor_kind: object, actor_id: object) -> bool:
    """Whether a recorded act is Sift recognizing a person from a face."""
    return (
        verb == "linked"
        and object_kind == "person"
        and actor_kind in (None, "sift")
        and actor_id == "faces"
    )


async def recognized_lines(
    database: Database,
    rows: Sequence[Row],
    files: Mapping[str, Link | str],
    *,
    page: tuple[str, str] | None = None,
    here: str = "them",
) -> dict[str, Line]:
    """The face-match receipts on a page that is not the file, as the one face-match sentence."""
    chosen = [
        row
        for row in rows
        if is_face_match(row["verb"], row["object_kind"], row["actor_kind"], row["actor_id"])
        and isinstance(files.get(str(row["id"])), Link)
        and page == ("person", str(row["object_id"]))
    ]
    if not chosen:
        return {}
    pairs = []
    for row in chosen:
        file = files[str(row["id"])]
        assert isinstance(file, Link)  # noqa: S101 (narrowed by the filter above)
        pairs.append((file.id, str(row["object_id"])))
    sures = await face_sures(database, pairs)
    lines: dict[str, Line] = {}
    for row in chosen:
        file = files[str(row["id"])]
        assert isinstance(file, Link)  # noqa: S101 (narrowed by the filter above)
        person_id = str(row["object_id"])
        lines[str(row["id"])] = say.recognized_face(
            here,
            say.said("in ", piece_of(file)),
            sures.get((file.id, person_id), ()),
        )
    return lines
