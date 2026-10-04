# SPDX-License-Identifier: AGPL-3.0-or-later
"""The face lines of a History thread: who a scan found, what Sift recognized on its own, and
how sure it was, said the same way on every page.
"""

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
    """WHO a scan found, in words, with a way to each of them. ("", ()) where it found nothing.

    The named ones first, each once. A person is one name however many separate appearances of them
    the scan tracked, and listing a person three times because they walk in and out of shot would be
    a sentence that reads as three people.

    THEN THE FACES SIFT ONLY SUGGESTS, each person once: "a face that may be Ada Lumen", the person
    a way to their page and the words before the name a way to where the question is answered (the
    person's waiting faces in Organize, `faces_of_person`). A face matched closely enough to ask
    about and not to name is neither somebody nor nobody, and "found a face" said of it hid the one
    name the scan came up with. A person both named and suggested here is said once, as named: the
    name is the answer, the suggestion only the question that led to it. Read as the rows stand, so
    the line stays true once the question is answered: a yes makes the face named, a no makes it a
    face nobody has named. `asks` is whether the reader may be asked (an admin, the one reader the
    faces questions are for); to anybody else a suggested face is a face nobody has named.

    THE FACES NOBODY HAS NAMED ARE ONE PHRASE, and the link on it is the group they are waiting in,
    which is only offered where they are all waiting in the SAME group. Two faces in two groups
    have two places to go and the phrase is one run of characters, so any link on it would take
    somebody to one group and silently not to the other. That is the rule `sentences.folder_of`
    follows for the same reason, and it costs less than it looks: most files with an unnamed face
    have all of them in one group, and get the way in.
    """
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

#: Every look for faces at one file, newest first. `face_scans` keeps only the last look, so the
#: lines are read from the acts, and a look made again leaves the earlier one on the History.
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
    """A line for each look for faces at this file, each with what that look found.

    Only the newest names who: the faces on the file are its look's, and an earlier look's are
    gone with it, so that one says its count.
    """
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


#: Every appearance on this file that Sift attached somebody to ON ITS OWN.
#:
#: `ix_face_tracks_asset (asset_id)` is what makes it a point read rather than a walk of a table
#: that grows with every face in the library; see `_INDEXES` in the faces slice's schema.
#:
#: Read straight off the appearance rather than from a log written beside it, which is the rule this
#: whole module is built on: the row IS the record, it carries who was attached, how sure the
#: arithmetic was and when the decision was made, and a second log could be missing an event that
#: happened or hold one that did not.
#:
#: Only `matched`. A `suggested` appearance decided nothing (it is a question waiting on somebody),
#: and a `confirmed` one is somebody's own answer, which `_CONFIRMED` in `history_sources`
#: already says in the words a person's decision deserves.
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
    """What Sift recognized here on its own, one line per PERSON it recognized.

    **The one thing that happens to a file with nobody in the room.** Every other sentence in a
    history is an act somebody took or a service somebody connected; a face matched above the attach
    line puts a person's name on a file without being asked.

    ## Its own kind, not a `named` with `via="faces"`

    `_said_by` groups every `named` event by its source word and REWRITES the sentence through
    `sentences.named_sentence`, so a naming arriving there with `via="faces"` would come out as
    "Neve Alder was named in this file", the measurement thrown away. And it must not be
    regrouped: a filing is out of `_FOLDS_BY_SOURCE` because it says its source at the back of its
    own sentence, and so does this, in front, with a number after it.

    The `via` mark is `faces`, the `enriched:` filter's own word, which is what a client draws the
    glyph from, and the KIND is the third face act beside the two answers a person gives. Nothing
    on the client had to learn it: `markOf` reads `via` first and falls through to the kind only
    where there is none.

    ## One line per person, not one per appearance

    A person walking in and out of shot is several appearances and one act of recognition, and three
    identical sentences one under the other is the repetition the folds beside this exist to end.
    Grouped HERE rather than in `_one_line_per_face_act` with the other face fold, and the reason is
    the range: a fold works on finished sentences, and "between 84% and 92% sure" cannot be
    recovered from two lines that each say one figure. This is the only place both numbers exist.

    The fold rarely fires (few files hold two matched appearances of one person), but without it
    the pane would say the same sentence twice with nothing to say why.

    THE MOMENT IS THE LATEST of the group, which is `_one_press`'s rule for a press and is right for
    the same reason: the act is one thing and the pane puts it where it ended. Where a later pass
    matched a second appearance of somebody already matched here, the line moves to the later moment
    and says so in its range; there is no run id on an appearance to group by instead, and inventing
    one out of the clock is the fold `history_folds` refuses for the filings.

    ## The Undo, and where it comes from

    The scan path writes a receipt per person per file, titled with `matched_sentence`, and
    `_one_line_per_act` folds this line into it, so the pane draws one line, Sift's own sentence,
    with the receipt's Undo on it. The receipt IS the per-file door, which is the answer the
    filename pass and the watermark pass give to the same question.

    A RE-MATCH is different, deliberately: its receipt is the RUN, over hundreds of files, and its
    title says "N more faces" rather than this file's sentence, so it contains nothing to fold and
    the two lines stand together. A file a re-match touched is taken back a whole run at a time, and
    a match made by a scan older than its receipts has none, and no backfill could invent one.

    The confidence is said as a whole percentage because that is how every other surface in this
    application says a match, and it is the half of the sentence that matters: "Sift decided this"
    invites the question "how sure", and a line that cannot answer it is a line that gets checked by
    hand. See `sentences.how_sure` for how a range is written.

    `tables` is what the caller found in `sqlite_master`: a process that never imported the faces
    slice has no `face_tracks` at all, so the statement below is a hard error rather than an empty
    answer. The same guard, and the same reason, as every feature-owned read in `history_sources`.
    """
    if "face_tracks" not in tables:
        return []
    #: Keyed on the person, holding the name, every moment and every figure, in the statement's
    #: order, which is oldest first, so a group's first row is where its line sits in the thread.
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
                # None only where every appearance in the group predates the column that records a
                # moment, which keeps the line where those rows belong: first, above everything
                # with a time.
                at=max(told) if told else None,
                actor=Actor.SIFT,
                actor_name=SIFT,
                kind="matched",
                # The person, even where the line stands for three appearances: the SENTENCE still
                # names them. `detail` would be the same name listed three times under a line that
                # already says how many (see `Detail`). The ONE face-match builder every screen
                # uses (`sentences.recognized_face`); only the vantage word changes.
                pieces=say.recognized_face(say.thing("person", person_id, name), "here", sures),
                via="faces",
                receipt=_receipt_for(by_receipt or {}, "matched", person_id),
            )
        )
    return events


async def face_sures(
    database: Database, pairs: Sequence[tuple[str, str]]
) -> dict[tuple[str, str], list[float | None]]:
    """How sure Sift was of each (file, person) match it made, off the appearances themselves.

    The SAME figures the file's own line says (`face_matches_of_asset` reads the same statement), so
    a face match reads one sentence on the file, the person's page and the feed:
    the receipt's stored title is words written once, and these are the appearances as they stand.
    One point read per distinct file, bounded by the page asking; nothing where the faces feature
    is not registered in this process.
    """
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
    """Whether a recorded act is Sift recognizing a person from a face: the one act said as
    "Sift recognized ... how sure" on every screen."""
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
    """The face-match receipts on a page that is not the file, as the one face-match sentence.

    "Sift recognized them in image.png, 75% sure" on her page, where the stored title said "here".
    Only a receipt about exactly ONE file (`files_of_decisions`) whose file still exists; any other
    keeps its title. `page` is the person whose page this is, said as `here`; a match of somebody
    else read on this page keeps its title, because this read does not have their name to hand.
    """
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
