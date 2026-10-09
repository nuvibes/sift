# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one door every act in the library is written through: a closed verb list, few subjects
per event, and snapshots of names rather than keys, so a row outlives what it is about."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from sift.kernel.client import current as current_client
from sift.kernel.db import Connection, in_clause
from sift.kernel.ids import new_id
from sift.kernel.vocabulary import (
    LEDGER_QUEUE,
    SIFT_ACTS_BY,
    Subject,
    SubjectKind,
)

#: Every word an event's verb may be, closed so two areas cannot spell one act two ways.
VERBS: Final = frozenset(
    {
        "added",
        "removed",
        "renamed",
        "named",
        "filed",
        "moved",
        "linked",
        "unlinked",
        "hidden",
        "revealed",
        "shared",
        "unshared",
        "kept_local",
        "allowed",
        "kept_from_swaps",
        "allowed_in_swaps",
        "merged",
        "edited",
        "enriched",
        "asked",
        "scanned",
        "produced",
        "deleted",
        "forgot",
        # Two words for the two endings, because a reader filters on the verb.
        "downloaded",
        "download_failed",
        "paused",
        "resumed",
        # Which judgement lives in the queue's own payload.
        "decided",
        "cookies_saved",
        "cookies_replaced",
        "cookies_forgotten",
        "canceled",
        "saved",
        "ran",
        "pressed",
        "face_run",
        "song_named",
        # A swap's payload never holds an address, a token or the code.
        "swap_started",
        "swap_ended",
        # Retired sends; kept so the older lines still read.
        "wall_sent",
        "restored",
        "adopted",
        # Acts on the computer running Sift (`kernel/machine_acts.py`), not on the library.
        "sharing_turned_on",
        "sharing_turned_off",
        "start_with_windows_on",
        "start_with_windows_off",
        "firewall_opened",
        "storage_moved",
        "update_started",
        "library_opened",
        "restarted",
        "recounted",
    }
)

#: The acts Sift may record without naming its task, each saying how the task is already known.
SIFT_WITHOUT_A_TASK: Final[Mapping[str, str]] = {
    "ran": "the subject is the run itself, named with the task's own word",
}

#: The kinds that always have a name: nobody can name one afterwards, so a nameless one is refused.
NAMED_KINDS: Final = frozenset(
    {"asset", "person", "tag", "site", "collection", "photo_set", "song"}
)

#: What each kind is called now, the one definition the writer and the history reader share.
#: An asset answers in the file page's order: title, name on disk, then the name it arrived under.
NAME_NOW: Final[Mapping[str, str]] = {
    "asset": (
        "SELECT a.id AS id, COALESCE(NULLIF(a.title, ''),"
        " (SELECT l.filename FROM asset_locations l"  # nosemgrep: sift-no-asset-sql-outside-kernel
        "  WHERE l.asset_id = a.id AND l.status = 'present' ORDER BY l.first_seen_at LIMIT 1),"
        " a.original_filename) AS name"
        " FROM assets a WHERE a.id IN (?*)"  # nosemgrep: sift-no-asset-sql-outside-kernel
    ),
    "person": "SELECT id AS id, name AS name FROM people WHERE id IN (?*)",
    "site": "SELECT id AS id, name AS name FROM sites WHERE id IN (?*)",
    "tag": "SELECT id AS id, name AS name FROM tags WHERE id IN (?*)",
    "collection": "SELECT id AS id, name AS name FROM collections WHERE id IN (?*)",
    "photo_set": "SELECT id AS id, name AS name FROM photo_sets WHERE id IN (?*)",
    "song": "SELECT id AS id, name AS name FROM songs WHERE id IN (?*)",
    "username": "SELECT id AS id, name AS name FROM usernames WHERE id IN (?*)",
    "login": "SELECT id AS id, username AS name FROM users WHERE id IN (?*)",
    "folder": "SELECT id AS id, name AS name FROM folders WHERE id IN (?*)",  # nosemgrep: sift-no-asset-sql-outside-kernel
    "download": (
        "SELECT id AS id, COALESCE(NULLIF(filename, ''), url) AS name"
        " FROM downloads WHERE id IN (?*) AND hidden_at IS NULL"
    ),
}

#: How many ids one name lookup binds, well under SQLite's variable limit.
_NAMES_PER_READ: Final = 500


#: The most subjects one event may name, unless it is a receipt: above it, a list is really a pass
#: and hands a count.
MOST_SUBJECTS: Final = 8

#: The three words `created_by_kind` already stores, reused rather than re-spelled.
ACTOR_SIFT: Final = "sift"
ACTOR_USER: Final = "user"
ACTOR_BOX: Final = "box"


@dataclass(frozen=True, slots=True)
class Actor:
    """Who took an act: a snapshot of kind and id (a user, a pass for Sift, a box), never a key."""

    kind: str
    id: str | None = None

    @staticmethod
    def sift(via: str | None = None) -> Actor:
        """Sift itself, and which pass or task (`SIFT_ACTS_BY`)."""
        return Actor(ACTOR_SIFT, via)

    @staticmethod
    def user(user_id: str) -> Actor:
        """Somebody signed in, by their user id."""
        return Actor(ACTOR_USER, user_id)

    @staticmethod
    def box(box_id: str) -> Actor:
        """A stash-box answered, by its id."""
        return Actor(ACTOR_BOX, box_id)


@dataclass(frozen=True, slots=True)
class Object:
    """What an act was done with, and the name it had at the time."""

    kind: SubjectKind
    id: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Reversal:
    """What makes an event a receipt: the queue that can take it back, and the sentence it shows."""

    queue: str
    title: str
    detail: str


_RECORD = (
    "INSERT INTO workbench_decisions"
    " (id, queue, user_id, title, detail, payload, decided_at, reversed_at,"
    " verb, actor_kind, actor_id, object_kind, object_id, object_name, touched,"
    " client_kind, device_id)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

# `OR IGNORE`: a subject handed twice is the same true thing said twice.
_RECORD_SUBJECT = (
    "INSERT OR IGNORE INTO workbench_decision_subjects (decision_id, kind, subject_id, name)"
    " VALUES (?, ?, ?, ?)"
)


class LedgerError(ValueError):
    """A call the door refuses, with a sentence saying which rule it broke."""


async def record_event(
    connection: Connection,
    *,
    actor: Actor,
    verb: str,
    subject: Subject | Sequence[Subject],
    object: Object | None = None,
    count: int | None = None,
    payload: str | None = None,
    receipt: Reversal | None = None,
) -> str:
    """Write one event on the caller's own connection; its id is also its receipt id."""
    _refuse_actor(actor, verb)

    subjects = [subject] if isinstance(subject, Subject) else list(subject)
    if not subjects and receipt is None:
        raise LedgerError("an event with no subject is an event about nothing")
    if count is not None and len(subjects) > 1:
        raise LedgerError(
            "an event carrying a count names at most one subject: the thing the pass ran over."
            " A pass that can afford to name each file does not need the count"
        )
    if count is not None and count < 1:
        raise LedgerError("a count of nothing is not a pass that ran")
    if receipt is None and len(subjects) > MOST_SUBJECTS:
        raise LedgerError(
            f"{len(subjects)} subjects for one event, and the ceiling is {MOST_SUBJECTS}."
            " Write one event per file, or one event with a count. See MOST_SUBJECTS"
        )

    subjects, object = await _named(connection, subjects, object)

    # Read here so no writer can forget it; only a User's act was taken from a window.
    stamped = current_client() if actor.kind == ACTOR_USER else None

    event_id = new_id()
    happened_at = int(time.time())
    await connection.execute(
        _RECORD,
        (
            event_id,
            LEDGER_QUEUE if receipt is None else receipt.queue,
            actor.id if actor.kind == ACTOR_USER else None,
            "" if receipt is None else receipt.title,
            "" if receipt is None else receipt.detail,
            payload or "",
            happened_at,
            verb,
            actor.kind,
            actor.id,
            None if object is None else object.kind,
            None if object is None else object.id,
            None if object is None else object.name,
            count,
            None if stamped is None else stamped.kind,
            None if stamped is None else stamped.device,
        ),
    )
    await connection.executemany(
        _RECORD_SUBJECT,
        [(event_id, one.kind, one.id, one.name) for one in subjects],
    )
    return event_id


def _refuse_actor(actor: Actor, verb: str) -> None:
    if verb not in VERBS:
        raise LedgerError(
            f"{verb!r} is not a verb the ledger knows. Add it to VERBS in kernel/ledger.py, once,"
            " rather than reaching for the nearest word that is already there"
        )
    if actor.kind not in (ACTOR_SIFT, ACTOR_USER, ACTOR_BOX):
        raise LedgerError(f"{actor.kind!r} is not one of sift, user or box")
    if actor.kind == ACTOR_SIFT and actor.id is not None and actor.id not in SIFT_ACTS_BY:
        raise LedgerError(
            f"{actor.id!r} is not a pass this build has a word for. See MADE_VIAS and TASK_VIAS"
            " in kernel/vocabulary.py, the first of which is the vocabulary a row's provenance uses"
        )
    if actor.kind == ACTOR_SIFT and actor.id is None and verb not in SIFT_WITHOUT_A_TASK:
        raise LedgerError(
            f"Sift {verb} something and did not say which task did it. Name the pass or task"
            " (Actor.sift(VIA_...)). Only the acts in SIFT_WITHOUT_A_TASK may leave it out"
        )
    if actor.kind != ACTOR_SIFT and not actor.id:
        raise LedgerError(f"an actor of kind {actor.kind!r} has to say which one")


async def _named(
    connection: Connection, subjects: list[Subject], object: Object | None
) -> tuple[list[Subject], Object | None]:
    """The same things, each carrying its name; a name the caller handed is kept as handed."""
    missing: dict[str, set[str]] = {}
    for one in subjects:
        if not one.name and one.kind in NAME_NOW:
            missing.setdefault(one.kind, set()).add(one.id)
    if object is not None and not object.name and object.kind in NAME_NOW:
        missing.setdefault(object.kind, set()).add(object.id)

    found, there = await _names_now(connection, missing)

    named = [
        one if one.name else replace(one, name=found.get((one.kind, one.id), one.name))
        for one in subjects
    ]
    if object is not None and not object.name:
        object = replace(object, name=found.get((object.kind, object.id), object.name))

    # Refused only where the row is gone: a row with an empty name is recorded as it is.
    for kind, thing_id, name in [
        *((one.kind, one.id, one.name) for one in named),
        *(() if object is None else ((object.kind, object.id, object.name),)),
    ]:
        if kind in NAMED_KINDS and not name and (kind, thing_id) not in there:
            raise LedgerError(
                f"the {kind} {thing_id} has no name to record, and a {kind} always has one."
                " It is not there to look up either: the writer recorded after it went. Read the"
                " name before the row goes and hand it in (see NAMED_KINDS)"
            )
    return named, object


async def _names_now(
    connection: Connection, missing: dict[str, set[str]]
) -> tuple[dict[tuple[str, str], str], set[tuple[str, str]]]:
    """The names found for the missing ids, and which of those rows are there at all."""
    found: dict[tuple[str, str], str] = {}
    there: set[tuple[str, str]] = set()
    for kind, ids in missing.items():
        ordered = sorted(ids)
        for start in range(0, len(ordered), _NAMES_PER_READ):
            asked, values = in_clause(NAME_NOW[kind], ordered[start : start + _NAMES_PER_READ])
            for row in await connection.execute_fetchall(asked, values):
                there.add((kind, str(row["id"])))
                if row["name"]:
                    found[(kind, str(row["id"]))] = str(row["name"])
    return found, there
