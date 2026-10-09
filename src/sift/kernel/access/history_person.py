# SPDX-License-Identifier: AGPL-3.0-or-later
"""What happened to one person, in order, counted per source and day rather than listed per file."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from sift.kernel.access import sentences as say
from sift.kernel.access.history import (
    DEFAULT_LIMIT,
    MADE_BY_BOX,
    MAX_LIMIT,
    Actor,
    Event,
    FaceAnswer,
    _decision_events,
    _names_of,
    _via_of_source,
    _Who,
    actor_of_source,
    by_of,
    files_of_decisions,
    files_of_filing,
    kept_events,
    ledger_events,
    maker_of,
    one_line_per_face_answer,
    one_line_per_kept,
    ordered,
    recognized_lines,
    runs_not_drawn,
    stash_box_tables_in,
)

# Reached past the underscore so a person's thread and a file's say one act the same way.
from sift.kernel.access.history_boxes import linked_line, unshown_said
from sift.kernel.access.history_entity import NAMES_A_FILE
from sift.kernel.access.history_events import NOTHING_HIDDEN, verdict_of
from sift.kernel.access.history_folds import (
    NO_RECEIPT,
    RECEIPT_OF_A_NAMING,
    counted_apart_from_receipts,
    drawn_receipts,
)
from sift.kernel.access.history_sources import _folders_seen
from sift.kernel.access.repository import Repository
from sift.kernel.access.sentences import (
    FACES_CONFIRMED,
    SIFT,
    VANTAGE_PERSON,
    Line,
    counted_faces,
    faces_of_person,
    files,
)
from sift.kernel.access.viewer import Viewer
from sift.kernel.access.visibility import FILE_SEEN_BY_VIEWER, seen_by
from sift.kernel.access.worded import decided_said, lines_under
from sift.kernel.db import Database, Row
from sift.kernel.records import Subject
from sift.kernel.sql_splice import splice
from sift.kernel.vocabulary import FACE_SAID_NO, FACE_SAID_YES
from sift.kernel.where import folder_said

if TYPE_CHECKING:  # pragma: no cover (the registry type, for the annotation only)
    from sift.kernel.workbench import Workbench

#: The tables a feature owns, absent in a process that never imported its slice.
_FEATURE_TABLES = (
    "face_confirmations",
    "folder_people",
    "face_references",
    "stash_box_kept",
    "face_rejections",
    "person_stash_box_links",
    "stash_boxes",
    "workbench_decisions",
    "workbench_decision_subjects",
)

_TABLES = "SELECT name FROM sqlite_master WHERE type = 'table'"

_PERSON = (
    "SELECT name, created_at, created_by_kind, created_by_via, created_by_user_id,"
    " created_by_box_id"
    " FROM people WHERE id = ?"
)

#: The face tables keep milliseconds, so their moment is divided down to seconds first.

#: Every file this person was put on, by source, box and day, counting only files this viewer may be
#: shown.
# Each group carries its receipt (`history_folds.RECEIPT_OF_A_NAMING`) so a card on this page
# absorbs its own namings.
_NAMED_SQL = """
SELECT link.source AS source,
       CASE WHEN link.source = 'stash_box' THEN (
         CASE WHEN link.box_id IS NOT NULL THEN (
           SELECT b.name FROM stash_boxes b WHERE b.id = link.box_id
         ) ELSE (
           SELECT CASE WHEN COUNT(*) = 1 THEN MIN(b.name) END
             FROM asset_stash_box_matches m
             JOIN stash_boxes b ON b.id = m.box_id
            WHERE m.asset_id = link.asset_id AND m.state = 'applied'
         ) END
       ) END AS box,
       unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 AS day,
       {{RECEIPT}} AS receipt,
       COUNT(*) AS files,
       MAX(link.decided_at) AS at
  FROM asset_people link
 WHERE link.person_id = :person AND {{SEEN}}
 GROUP BY source, box, day, receipt
"""
_NAMED = splice(_NAMED_SQL, SEEN=FILE_SEEN_BY_VIEWER, RECEIPT=RECEIPT_OF_A_NAMING)
_NAMED_NO_LEDGER = splice(_NAMED_SQL, SEEN=FILE_SEEN_BY_VIEWER, RECEIPT=NO_RECEIPT)

#: The same without the stash-box tables; two fixed statements, since query text is never built.
_NAMED_NO_BOX_SQL = """
SELECT link.source AS source,
       NULL AS box,
       unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 AS day,
       {{RECEIPT}} AS receipt,
       COUNT(*) AS files,
       MAX(link.decided_at) AS at
  FROM asset_people link
 WHERE link.person_id = :person AND {{SEEN}}
 GROUP BY source, box, day, receipt
"""
_NAMED_NO_BOX = splice(_NAMED_NO_BOX_SQL, SEEN=FILE_SEEN_BY_VIEWER, RECEIPT=RECEIPT_OF_A_NAMING)
_NAMED_NO_BOX_NO_LEDGER = splice(_NAMED_NO_BOX_SQL, SEEN=FILE_SEEN_BY_VIEWER, RECEIPT=NO_RECEIPT)

_CONFIRMED = splice(
    """
SELECT unixepoch(link.created_at / 1000, 'unixepoch', 'localtime') / 86400 AS day,
       COUNT(*) AS faces,
       MAX(link.created_at) / 1000 AS at,
       GROUP_CONCAT(link.asset_id) AS assets
  FROM face_confirmations link
 WHERE link.person_id = :person AND {{SEEN}}
 GROUP BY day
""",
    SEEN=FILE_SEEN_BY_VIEWER,
)

_REJECTED = splice(
    """
SELECT unixepoch(r.created_at / 1000, 'unixepoch', 'localtime') / 86400 AS day,
       COUNT(*) AS faces,
       MAX(r.created_at) / 1000 AS at,
       GROUP_CONCAT(link.asset_id) AS assets
  FROM face_rejections r
  JOIN face_tracks link ON link.id = r.track_id
 WHERE r.person_id = :person AND {{SEEN}}
 GROUP BY day
""",
    SEEN=FILE_SEEN_BY_VIEWER,
)

#: A stash-box that knows this person, and what its last ask filled in; a subquery so one box is one
#: line.
_LINKED = """
SELECT b.name AS box, l.box_id AS box_id, l.fetched_at AS at,
       (SELECT r.automatic FROM enrichment_runs r
         WHERE r.subject = 'person' AND r.local_id = l.person_id AND r.box_id = l.box_id
         ORDER BY r.at DESC, r.id DESC LIMIT 1) AS automatic,
       (SELECT r.at FROM enrichment_runs r
         WHERE r.subject = 'person' AND r.local_id = l.person_id AND r.box_id = l.box_id
         ORDER BY r.at DESC, r.id DESC LIMIT 1) AS run_at,
       (SELECT r.applied FROM enrichment_runs r
         WHERE r.subject = 'person' AND r.local_id = l.person_id AND r.box_id = l.box_id
         ORDER BY r.at DESC, r.id DESC LIMIT 1) AS applied
  FROM person_stash_box_links l
  JOIN stash_boxes b ON b.id = l.box_id
 WHERE l.person_id = ?
"""

#: Every bulk judgement that named this person, never an event (`LEDGER_QUEUE`), and only where
#: every file is one this viewer may be shown, since the stored title's count cannot be scoped.
_DECIDED = splice(
    """
SELECT d.id AS id, d.title AS title, d.queue AS queue, d.user_id AS user_id,
       d.actor_kind AS actor_kind, d.actor_id AS actor_id,
       d.decided_at AS decided_at, d.reversed_at AS reversed_at, d.verb AS verb,
       d.object_kind AS object_kind, d.object_id AS object_id, d.payload AS payload,
       d.detail AS detail
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = 'person' AND s.subject_id = :subject AND d.queue <> :ledger
{{NAMES_A_FILE}}
{{NOTHING_HIDDEN}}
 ORDER BY d.decided_at ASC, d.id ASC
""",
    NAMES_A_FILE=NAMES_A_FILE,
    NOTHING_HIDDEN=NOTHING_HIDDEN,
)


_FOLDERS_NAMED = """
SELECT f.id AS id, f.root_id AS root_id, f.rel_path AS path, f.name AS name
  FROM folder_people fp
  JOIN folders f ON f.id = fp.folder_id
 WHERE fp.person_id = ?
 ORDER BY f.rel_path
"""

#: In milliseconds, as `faces.store.now_ms` stamps it.
_TAUGHT = splice(
    """
SELECT unixepoch(link.created_at / 1000, 'unixepoch', 'localtime') / 86400 AS day,
       COUNT(*) AS faces,
       MAX(link.created_at) / 1000 AS at
  FROM face_references link
 WHERE link.person_id = :person AND link.origin != 'seed' AND (link.asset_id IS NULL OR {{SEEN}})
 GROUP BY day
""",
    SEEN=FILE_SEEN_BY_VIEWER,
)

_RULED_OUT = splice(
    """
SELECT unixepoch(link.refused_at, 'unixepoch', 'localtime') / 86400 AS day,
       COUNT(*) AS files,
       MAX(link.refused_at) AS at
  FROM asset_person_refusals link
 WHERE link.person_id = :person AND {{SEEN}}
 GROUP BY day
""",
    SEEN=FILE_SEEN_BY_VIEWER,
)


async def history_of_person(
    database: Database,
    viewer: Viewer,
    person_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
    final_queues: Sequence[str] = (),
    bench: Workbench | None = None,
    access: Repository | None = None,
) -> list[Event]:
    """Everything that happened to one person, oldest first, the newest `limit` kept."""
    # Imported here: `kernel.workbench` imports `kernel.access`, so a top-level import is a cycle.
    from sift.kernel.vocabulary import LEDGER_QUEUE

    kept = max(1, min(limit, MAX_LIMIT))
    present = {str(row["name"]) for row in await database.fetch_all(_TABLES)}
    here = {name for name in _FEATURE_TABLES if name in present}

    person = await database.fetch_one(_PERSON, (person_id,))
    if person is None:
        return []

    decided = (
        list(
            await database.fetch_all(
                _DECIDED, {**verdict_of(viewer), "subject": person_id, "ledger": LEDGER_QUEUE}
            )
        )
        if {"workbench_decisions", "workbench_decision_subjects"} <= here
        else []
    )
    who = _Who(
        viewer=viewer,
        names=await _names_of(
            database, [str(row["user_id"]) for row in decided if row["user_id"] is not None]
        ),
    )
    events = [await _person_added(database, viewer, person_id, person, here)]
    cards = await _person_decisions(
        database, viewer, person_id, decided, who, final_queues=final_queues, bench=bench
    )
    events.extend(cards)
    seen = {**seen_by(viewer), "person": person_id}
    events.extend(
        await _person_named_events(database, viewer, access, person_id, present, seen, cards)
    )
    events.extend(await _person_face_answers(database, person_id, here, seen))
    # Held until the ledger is read: a box it draws press by press loses its latest-run line.
    linked: list[Event] = []
    if {"person_stash_box_links", "stash_boxes"} <= here:
        for row in await database.fetch_all(_LINKED, (person_id,)):
            linked.append(
                await linked_line(database, Subject.PERSON, person_id, row, "their", viewer)
            )
    events.extend(await _person_runs(database, person_id, here, seen))
    ledger = await _person_ledger(
        database, viewer, person_id, person, kept=kept, box_said=bool(linked)
    )
    events.extend(runs_not_drawn(linked, ledger))
    events.extend(ledger)
    events = one_line_per_kept(events)
    events = one_line_per_face_answer(events, reword=_faces_left)

    return await unshown_said(database, access, viewer, ordered(events)[-kept:])


async def _person_added(
    database: Database, viewer: Viewer, person_id: str, person: Row, here: set[str]
) -> Event:
    """The line the person arrived with: who made them, where the row records it."""
    box = person["created_by_box_id"]
    box_name = None
    if box is not None and "stash_boxes" in here:
        found = await database.fetch_one(MADE_BY_BOX, (str(box),))
        box_name = None if found is None else str(found["name"])
    made_by, made_by_name = maker_of(person, viewer, box_name)
    return Event(
        at=int(person["created_at"]),
        actor=made_by,
        actor_name=made_by_name,
        kind="added",
        pieces=say.person_added(
            by_of(made_by, made_by_name),
            say.thing("person", person_id, str(person["name"])),
            person["created_by_via"],
        ),
    )


async def _person_decisions(
    database: Database,
    viewer: Viewer,
    person_id: str,
    decided: list[Row],
    who: _Who,
    *,
    final_queues: Sequence[str],
    bench: Workbench | None,
) -> list[Event]:
    """Every decision naming the person, worded the one way the feed words it."""
    decided_files = await files_of_decisions(database, decided)
    said = await decided_said(
        database, bench, viewer, decided, here=("person", person_id, say.HERE[say.VANTAGE_PERSON])
    )
    worded = {key: line.pieces for key, line in said.items()}
    recognized = await recognized_lines(
        database, decided, decided_files, page=("person", person_id)
    )
    cards = _decision_events(
        decided,
        who,
        final=frozenset(final_queues),
        files=decided_files,
        lines={**worded, **recognized},
        under=lines_under(said),
    )
    return cards


async def _person_named_events(
    database: Database,
    viewer: Viewer,
    access: Repository | None,
    person_id: str,
    present: set[str],
    seen: dict[str, object],
    cards: list[Event],
) -> list[Event]:
    """A line per source that named the person on files, each count a link to those files."""
    from sift.kernel.vocabulary import LEDGER_QUEUE

    events: list[Event] = []
    # One phrase can link to one place, so with several folders the line says "from their folders".
    answered = (
        await database.fetch_all(_FOLDERS_NAMED, (person_id,))
        if {"folder_people", "folders"} <= present
        else []
    )
    folders = [str(row["path"]) or str(row["name"]) for row in answered]
    only_folder = await _the_one_folder(access, viewer, answered)
    receipts_here = {"workbench_decisions", "workbench_decision_subjects"} <= present
    if stash_box_tables_in(present):
        naming = _NAMED if receipts_here else _NAMED_NO_LEDGER
    else:
        naming = _NAMED_NO_BOX if receipts_here else _NAMED_NO_BOX_NO_LEDGER
    for group in counted_apart_from_receipts(
        await database.fetch_all(naming, {**seen, "ledger": LEDGER_QUEUE}),
        drawn_receipts(cards),
    ):
        source = None if group["source"] is None else str(group["source"])
        folder = only_folder if source == "folder" else None
        count = int(group["files"])
        box = None if group["box"] is None else str(group["box"])
        actor, actor_name = actor_of_source(source, box)
        counted = say.thing(
            "files", person_id, files(count), href=files_of_filing("named", person_id, group)
        )
        line: Line = say.named_on(
            say.by_word(actor.value, actor_name),
            source,
            counted,
            None if folder is None else say.folder_named(*folder),
            len(folders),
        )
        events.append(
            Event(
                at=None if group["at"] is None else int(group["at"]),
                actor=actor,
                actor_name=actor_name,
                kind="named",
                pieces=line,
                via=_via_of_source(source),
            )
        )
    return events


async def _person_face_answers(
    database: Database, person_id: str, here: set[str], seen: dict[str, object]
) -> list[Event]:
    """Each day's Yes and No on the person's faces."""
    events: list[Event] = []
    if "face_confirmations" in here:
        for row in await database.fetch_all(_CONFIRMED, seen):
            events.append(
                Event(
                    at=int(row["at"]),
                    actor=Actor.SOMEBODY,
                    actor_name=None,
                    kind="confirmed",
                    pieces=_faces_agreed(person_id, int(row["faces"])),
                    faces=_faces_of(person_id, row["assets"], FACE_SAID_YES),
                )
            )
    if "face_rejections" in here:
        for row in await database.fetch_all(_REJECTED, seen):
            events.append(
                Event(
                    at=int(row["at"]),
                    actor=Actor.SOMEBODY,
                    actor_name=None,
                    kind="rejected",
                    # No link: a refusal takes the name off the face, so there is nowhere to go.
                    pieces=say.faces_refused(int(row["faces"])),
                    faces=_faces_of(person_id, row["assets"], FACE_SAID_NO),
                )
            )
    return events


async def _person_runs(
    database: Database, person_id: str, here: set[str], seen: dict[str, object]
) -> list[Event]:
    """What a stash-box kept, the faces Sift was taught from, and the files ruled out."""
    events: list[Event] = []
    if {"stash_box_kept", "stash_boxes"} <= here:
        events.extend(await kept_events(database, "person", person_id))
    if "face_references" in here:
        for row in await database.fetch_all(_TAUGHT, seen):
            events.append(
                Event(
                    at=int(row["at"]),
                    actor=Actor.SIFT,
                    actor_name=SIFT,
                    kind="taught",
                    pieces=say.taught_by(int(row["faces"])),
                    via="faces",
                )
            )
    for row in await database.fetch_all(_RULED_OUT, seen):
        events.append(
            Event(
                at=int(row["at"]),
                actor=Actor.SOMEBODY,
                actor_name=None,
                kind="ruled_out",
                pieces=say.ruled_out_of(int(row["files"])),
            )
        )
    return events


async def _person_ledger(
    database: Database, viewer: Viewer, person_id: str, person: Row, *, kept: int, box_said: bool
) -> list[Event]:
    """The ledger's acts on the person: a rename, an edit, a share taken back."""
    return await ledger_events(
        database,
        viewer,
        here=VANTAGE_PERSON,
        kind="person",
        subject_id=person_id,
        grants_as=None,
        limit=kept,
        box_said=box_said,
        name_now=str(person["name"]),
    )


async def _the_one_folder(
    access: Repository | None, viewer: Viewer, answered: Sequence[Row]
) -> tuple[str, str] | None:
    """The one folder answered as this person, where there is one and this reader may be told it."""
    if len(answered) != 1:
        return None
    folder_id = str(answered[0]["id"])
    path, name = str(answered[0]["path"]), str(answered[0]["name"])
    if access is None:
        return None
    seen = await _folders_seen(access, viewer)
    if folder_said(path, seen=seen.get(str(answered[0]["root_id"]), frozenset())) != path:
        return None
    return folder_id, path or name


def _faces_agreed(person_id: str, faces: int) -> Line:
    """The confirmed line's words for a count: the way to exactly the faces it counts."""
    return say.faces_agreed(
        say.thing(
            "faces",
            person_id,
            counted_faces(faces),
            href=faces_of_person(person_id, FACES_CONFIRMED),
        ),
        faces,
    )


def _faces_of(person_id: str, assets: object, how: str) -> tuple[FaceAnswer, ...]:
    """The faces a day's line counts, one `FaceAnswer` per face, off the files the SQL listed."""
    listed = str(assets).split(",") if assets else []
    return tuple((person_id, asset_id, how) for asset_id in listed if asset_id)


def _faces_left(event: Event, left: int) -> Event:
    """A day's face line said again for the faces no receipt on the page accounts for."""
    person_id = event.faces[0][0]
    pieces = (
        _faces_agreed(person_id, left) if event.kind == "confirmed" else say.faces_refused(left)
    )
    return replace(event, pieces=pieces)
