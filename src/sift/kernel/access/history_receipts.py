# SPDX-License-Identifier: AGPL-3.0-or-later
"""A workbench decision as a History line."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

from sift.kernel.access import sentences as say
from sift.kernel.access.history_actors import _actor_of_act, _Who
from sift.kernel.access.history_line import (
    LINK_KINDS,
    Actor,
    Event,
    FaceAnswer,
    KeptAnswer,
    Link,
    Undo,
    by_of,
    piece_of,
)
from sift.kernel.access.history_reads import _text_or_none
from sift.kernel.access.sentences import (
    A_GONE,
    A_THING,
    SIFT,
    VANTAGE_FILE,
    Line,
    Piece,
    today_words,
)
from sift.kernel.db import Database, Row, in_clause
from sift.kernel.vocabulary import FACE_SAID_NO, FACE_SAID_YES, RECEIPT_FACES, RECEIPT_KEPT

#: What else a receipt named besides this file; bounded by the `_DECIDED` page.
_RECEIPT_NAMED = (
    "SELECT decision_id, kind, subject_id, name FROM workbench_decision_subjects"
    " WHERE decision_id IN (?*) AND kind <> 'asset'"
)


async def _receipt_objects(
    database: Database, receipt_ids: Sequence[str]
) -> dict[str, tuple[str, str, str | None]]:
    """What each of these receipts was about besides this file, where it still exists."""
    from sift.kernel.access.history_events import subjects_present

    statement, bound = in_clause(_RECEIPT_NAMED, sorted(set(receipt_ids)))
    rows = list(await database.fetch_all(statement, bound))
    if not rows:
        return {}
    wanted: dict[str, list[str]] = {}
    for row in rows:
        wanted.setdefault(str(row["kind"]), []).append(str(row["subject_id"]))
    present = await subjects_present(database, wanted)
    named: dict[str, tuple[str, str, str | None]] = {}
    for row in rows:
        key = str(row["decision_id"])
        kind, subject_id = str(row["kind"]), str(row["subject_id"])
        if key in named or (kind, subject_id) not in present:
            continue
        named[key] = (kind, subject_id, None if row["name"] is None else str(row["name"]))
    return named


#: The one payload key Sift reads: a receipt that wants its stored sentence linked declares the way
#: there itself.
_RECEIPT_LINK = "link"


def kept_answers_of(payload: object) -> tuple[tuple[KeptAnswer, str], ...]:
    """The stash-box answers a receipt says it kept, each with how, off the declared key alone."""
    if not payload:
        return ()
    try:
        held = json.loads(str(payload))
    except ValueError:
        return ()
    listed = held.get(RECEIPT_KEPT) if isinstance(held, dict) else None
    out: list[tuple[KeptAnswer, str]] = []
    for one in listed if isinstance(listed, list) else []:
        if not isinstance(one, dict):
            continue
        words = [one.get(name) for name in ("subject", "local_id", "box_id", "key", "how")]
        if not all(isinstance(word, str) and word for word in words):
            continue
        subject, local_id, box_id, key, how = (str(word) for word in words)
        out.append(((subject, local_id, box_id, key), how))
    return tuple(out)


def faces_answered_of(payload: object) -> tuple[FaceAnswer, ...]:
    """The faces a receipt says it answered for, off the declared key alone."""
    if not payload:
        return ()
    try:
        held = json.loads(str(payload))
    except ValueError:
        return ()
    listed = held.get(RECEIPT_FACES) if isinstance(held, dict) else None
    out: list[FaceAnswer] = []
    for one in listed if isinstance(listed, list) else []:
        if not isinstance(one, dict):
            continue
        words = [one.get(name) for name in ("person_id", "asset_id", "how")]
        if not all(isinstance(word, str) and word for word in words):
            continue
        person_id, asset_id, how = (str(word) for word in words)
        if how not in (FACE_SAID_YES, FACE_SAID_NO):
            continue
        out.append((person_id, asset_id, how))
    return tuple(out)


def _receipt_link(payload: object) -> Link | None:
    """The way somewhere a receipt wrote into its own payload, or None; unreadable is nothing."""
    if not payload:
        return None
    try:
        held = json.loads(str(payload))
    except ValueError:
        return None
    if not isinstance(held, dict):
        return None
    link = held.get(_RECEIPT_LINK)
    if not isinstance(link, dict):
        return None
    kind, link_id = link.get("kind"), link.get("id")
    words, href = link.get("words"), link.get("href")
    if not all(isinstance(one, str) and one for one in (kind, link_id, words, href)):
        return None
    if kind not in LINK_KINDS:
        return None
    return Link(kind=str(kind), id=str(link_id), name=str(words), href=str(href))


#: Receipts about these keep their stored words: the pane already draws a line for them, and the
#: receipt is the richer one.
_KEEPS_ITS_TITLE = ("person", "tag", "username", "site")


def _worded_from_ledger(row: Row, about: tuple[str, str, str | None]) -> bool:
    """Whether this receipt's line is assembled here, or is the words it was stored with."""
    verb = row["verb"]
    if verb is None or str(verb) == "decided":
        return False
    if about[0] in _KEEPS_ITS_TITLE:
        return False
    return _receipt_link(row["payload"]) is None


def _decided_line(
    row: Row, named: Mapping[str, tuple[str, str, str | None]], by: str | None = None
) -> Line:
    """What one receipt says, as pieces."""
    about = named.get(str(row["id"]))
    if about is None or not _worded_from_ledger(row, about):
        title = today_words(str(row["title"]))
        return titled(title, _receipt_link(row["payload"]))
    verb = str(row["verb"])
    kind, subject_id, name = about
    return say.event_said(
        verb,
        by=by or SIFT,
        here=VANTAGE_FILE,
        task=_text_or_none(row["actor_id"]) if row["actor_kind"] in (None, "sift") else None,
        object_kind=kind,
        object_id=subject_id,
        object_name=name,
    ).pieces


def titled(title: str, link: Link | None) -> Line:
    """A stored title as a line, with the one link its writer declared placed on its words."""
    if link is None or not link.name:
        return say.said(title)
    at = title.find(link.name)
    if at < 0:
        return say.said(title)
    return say.said(title[:at], piece_of(link), title[at + len(link.name) :])


#: What a file is called in a line, in the file page's order (`Repository.names_on_disk`), so both
#: say the same name.
_FILE_NAMES = (
    "SELECT a.id AS id, COALESCE(NULLIF(a.title, ''),"
    " (SELECT l.filename FROM asset_locations l WHERE l.asset_id = a.id AND l.status = 'present'"
    " ORDER BY l.first_seen_at, l.id LIMIT 1),"
    " a.original_filename,"
    " (SELECT l.filename FROM asset_locations l WHERE l.asset_id = a.id"
    " ORDER BY l.first_seen_at, l.id LIMIT 1)) AS name"
    " FROM assets a WHERE a.id IN (?*)"
)


async def files_called(database: Database, asset_ids: Sequence[str]) -> dict[str, str]:
    """What each of these files is called in a line about it, for those still there."""
    if not asset_ids:
        return {}
    statement, bound = in_clause(_FILE_NAMES, sorted(set(asset_ids)))
    return {
        str(row["id"]): str(row["name"])
        for row in await database.fetch_all(statement, bound)
        if row["name"]
    }


_HERE_IN_A_TITLE = re.compile(r"\bhere\b")


async def files_of_decisions(database: Database, rows: Sequence[Row]) -> dict[str, Link | str]:
    """The one file each of these decisions was about, for a page that is not that file's."""
    from sift.kernel.access.history_events import subjects_of

    if not rows:
        return {}
    subjects = await subjects_of(database, [str(row["id"]) for row in rows])
    one_file: dict[str, tuple[str, str | None]] = {}
    for decision, things in subjects.items():
        files = {thing.id: thing.name for thing in things if thing.kind == "asset"}
        if len(files) == 1:
            one_file[decision] = next(iter(files.items()))
    if not one_file:
        return {}
    statement, bound = in_clause(_FILE_NAMES, sorted({one for one, _ in one_file.values()}))
    named = {str(row["id"]): row["name"] for row in await database.fetch_all(statement, bound)}
    answer: dict[str, Link | str] = {}
    for decision, (asset_id, snapshot) in one_file.items():
        if asset_id not in named:
            answer[decision] = snapshot or A_GONE["asset"]
            continue
        answer[decision] = Link(
            kind="asset", id=asset_id, name=str(snapshot or named[asset_id] or A_THING["asset"])
        )
    return answer


def the_file_named(line: Line, file: Link | str) -> Line:
    """A saved title's "here" said as the file it meant, linked."""
    named: Piece | str = piece_of(file) if isinstance(file, Link) else file
    for at, one in enumerate(line):
        if one.kind is not None:
            continue
        found = _HERE_IN_A_TITLE.search(one.text)
        if found is None:
            continue
        return say.said(
            *line[:at],
            one.text[: found.start()],
            "in ",
            named,
            one.text[found.end() :],
            *line[at + 1 :],
        )
    return line


def _decision_events(
    rows: Sequence[Row],
    who: _Who,
    *,
    final: frozenset[str],
    named: Mapping[str, tuple[str, str, str | None]] | None = None,
    files: Mapping[str, Link | str] | None = None,
    lines: Mapping[str, Line] | None = None,
    under: Mapping[str, str] | None = None,
) -> list[Event]:
    """A bulk judgement that named this file, plus the moment it was taken back."""
    events: list[Event] = []
    for row in rows:
        actor, name = _actor_of_act(
            _text_or_none(row["actor_kind"]),
            _text_or_none(row["actor_id"]),
            _text_or_none(row["user_id"]),
            who,
        )
        reversed_at = row["reversed_at"]
        offered = who.viewer.is_admin and reversed_at is None and str(row["queue"]) not in final
        line = _decided_line(row, named or {}, by_of(actor, name))
        if lines is not None and str(row["id"]) in lines:
            line = lines[str(row["id"])]
        elif files is not None and str(row["id"]) in files:
            line = the_file_named(line, files[str(row["id"])])
        events.append(
            Event(
                at=int(row["decided_at"]),
                actor=actor,
                actor_name=name,
                kind=_RECEIPT_MARKS.get(str(row["verb"] or ""), "decided"),
                pieces=line,
                undo=Undo(kind="decision", id=str(row["id"])) if offered else None,
                reversed=reversed_at is not None,
                # Its own id always: whether it can be undone is a separate question from which act
                # it is.
                receipt=str(row["id"]),
                kept=kept_answers_of(row["payload"]),
                faces=faces_answered_of(row["payload"]),
                stored_words=lines is None or str(row["id"]) not in lines,
                more=(under or {}).get(str(row["id"]), ""),
            )
        )
        if reversed_at is None:
            continue
        events.append(
            Event(
                at=int(reversed_at),
                actor=Actor.SOMEBODY,
                actor_name=None,
                kind="undone",
                pieces=say.undone("song_name" if str(row["verb"]) == "song_named" else "decision"),
            )
        )
    return events


#: Which mark each act wears, by family; a receipt with its own mark never wears the Organize tray.
_RECEIPT_MARKS: Mapping[str, str] = {"song_named": "song_named"}
