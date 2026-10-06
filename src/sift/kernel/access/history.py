# SPDX-License-Identifier: AGPL-3.0-or-later
"""What happened to one thing, in order.

A file gathers decisions. Somebody names a person in it, a stash-box recognises it and writes a
studio onto it, a folder read gives it somebody's name without asking, it is renamed, it is shared
with a guest, a copy is made from it. Each of those is written down in a different table, in a
different feature, with a different word for the moment it happened. This is the question a person
asks when they open a file and find something they do not remember putting there (*what happened
to this, and when*), asked once, over the tables that already hold the answers.

Nothing new is recorded to serve it: a history assembled from a second log beside the real writes
is a log that can be missing an event that happened, or hold one that did not, and a record that is
only usually right is worse than none.

## Why it is in the kernel and not in a feature

Because the tables are. `asset_people`, `asset_tags`, `asset_usernames`, `assets` and `acl_grants`
are the kernel's own, and reading them anywhere else is refused by a lint rule, so a feature could
not write this if it wanted to. The other half of the answer lives in tables features own, and those
are read here the same way the `enriched:` filter next door already reads `face_tracks` and
`asset_stash_box_matches`: by name, guarded on the table being there, because a feature's component
may not be registered at all in a process that never imported it.

## The subject is an asset, and that is deliberately not for ever

`history_of_asset` is one function because there is one screen. A person and a queue have histories
of the same shape (the same `Event`, the same actors, the same ordering rule), and
`history_of_person` belongs beside this when something needs it, reading `asset_people` from the
other end. There is no whole-library feed and there should not be one here: a feed is a page over
every asset, which is a different question with a different cost.

## A workbench decision

Every bulk judgement (a folder confirmed, a face group named, a stash-box page applied) writes a
row in `workbench_decisions` that can be taken back, and that row records WHAT it did in an opaque
JSON payload whose shape belongs to the queue that wrote it. Parsing every queue's payload here
would be the coupling that opacity exists to prevent.

`workbench_decision_subjects` says what a decision touched instead, written in the same transaction
as the decision, in a vocabulary of five words that anything may read. The payload stays opaque and
is the only thing undo reads. The two records answer different questions: how to put a decision
back, and what it was about.

So the join (`_DECIDED`, in `history_sources`) reads a table and parses nothing, and adding a queue
needs no edit here: a queue says what its decision touched in the same words as every other queue,
or says nothing and its decisions appear in no history.

**Two decisions this application writes name nothing, and that is correct.** A quarantined file and
a file refused on the way in were never imported, so there is no row for them to be about.

## Where the parts live

A line's shape and a thread's order are in `history_line`, who did an act in `history_actors`, the
file's own tables in `history_sources` and its filings' lines in `history_filings`, a workbench
decision in `history_receipts`, a stash-box's lines in `history_boxes`, the face lines in
`history_faces`, the event ledger's acts in `history_ledger`, and the rule that makes one act one
line in `history_folds`. This module assembles
a file's thread from them; `history_person` and `history_entity` assemble the others.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

# The wording. EVERY sentence this module says is written next door, in one table with no database
# in it, so the whole vocabulary can be read (and tested) at once.
from sift.kernel.access import sentences as say
from sift.kernel.access.history_actors import (
    MADE_BY_BOX,
    _names_of,
    _via_of_source,
    _Who,
    actor_of_source,
    maker_of,
)
from sift.kernel.access.history_boxes import (
    BOX_OF_A_ROW,
    _by_hand,
    _filled_said,
    _wrote,
    box_filled_in,
    enriched_by_box,
    field_word,
    files_of_filing,
    kept_events,
    kept_line,
    runs_not_drawn,
    stash_box_tables_in,
)

# At module level, and safe there: `history_events` imports the viewer, the database, the splice
# and the vocabulary and nothing else, so it cannot come back round to this module.
from sift.kernel.access.history_events import verdict_of
from sift.kernel.access.history_faces import (
    SCAN_FACTS,
    _faces_found,
    face_matches_of_asset,
    face_run_events,
    face_sures,
    is_face_match,
    looked,
    recognized_lines,
)
from sift.kernel.access.history_filings import _filed_events, _named_events, _Pane, _tagged_events
from sift.kernel.access.history_folds import (
    EPISODE_GAP,
    _one_line_per_act,
    _one_line_per_download,
    _one_line_per_face_act,
    _one_processed_line,
    _said_by,
    episodes,
    one_line_per_face_answer,
    one_line_per_kept,
    receipts_on_a_file,
)
from sift.kernel.access.history_ledger import (
    _EVENT_KINDS,
    _LINKED_KINDS,
    COUNTED_ON_ITS_PAGE,
    LINK_VERBS,
    Lent,
    details_of,
    ledger_events,
    share_makers,
)
from sift.kernel.access.history_line import (
    CAUSE_ORDER,
    CAUSE_RANK,
    DEFAULT_LIMIT,
    KINDS,
    LINK_KINDS,
    MAX_LIMIT,
    VIAS,
    Actor,
    Detail,
    Event,
    FaceAnswer,
    KeptAnswer,
    Link,
    Undo,
    by_of,
    link_of_piece,
    ordered,
    piece_of,
)
from sift.kernel.access.history_reads import _TABLES, _seconds
from sift.kernel.access.history_receipts import (
    _decision_events,
    _receipt_objects,
    faces_answered_of,
    files_called,
    files_of_decisions,
    kept_answers_of,
    the_file_named,
    titled,
)
from sift.kernel.access.history_sources import (
    _ADDED,
    _ASKED,
    _CONFIRMED,
    _DECIDED,
    _ENRICHED,
    _FACE_SCAN,
    _FACES_FOUND,
    _FIRST_PLACE,
    _GRANTS,
    _MADE_INTO,
    _MOVES,
    _PRODUCED,
    _REJECTED,
    LEDGER_SAID,
    MOST_COPIES,
    Pressers,
    _folders_seen,
    _move_events,
    _unread_sources,
    arrived_in,
    by_pressed,
    folder_ids,
    landing_folders,
    press_lines,
    pressers_of,
)
from sift.kernel.access.repository import Repository
from sift.kernel.access.sentences import (
    SIFT,
    VANTAGE_FILE,
)
from sift.kernel.access.viewer import Viewer
from sift.kernel.db import Database, Row
from sift.kernel.urls import scene_page
from sift.kernel.vocabulary import (
    FACE_SAID_NO,
    FACE_SAID_YES,
)

#: What this module answers for: its own entry points, every public name of its parts, and the
#: private names callers already import from here.
__all__ = [
    "BOX_OF_A_ROW",
    "CAUSE_ORDER",
    "CAUSE_RANK",
    "COUNTED_ON_ITS_PAGE",
    "DEFAULT_LIMIT",
    "EPISODE_GAP",
    "KINDS",
    "LINK_KINDS",
    "LINK_VERBS",
    "MADE_BY_BOX",
    "MAX_LIMIT",
    "MOST_COPIES",
    "VIAS",
    "_DECIDED",
    "_EVENT_KINDS",
    "_LINKED_KINDS",
    "Actor",
    "Detail",
    "Event",
    "FaceAnswer",
    "KeptAnswer",
    "Lent",
    "Link",
    "Undo",
    "_Who",
    "_by_hand",
    "_decision_events",
    "_move_events",
    "_names_of",
    "_one_line_per_download",
    "_one_processed_line",
    "_via_of_source",
    "actor_of_source",
    "box_filled_in",
    "by_of",
    "details_of",
    "enriched_by_box",
    "episodes",
    "face_matches_of_asset",
    "face_sures",
    "faces_answered_of",
    "field_word",
    "files_called",
    "files_of_decisions",
    "files_of_filing",
    "history_of_asset",
    "is_face_match",
    "kept_answers_of",
    "kept_events",
    "kept_line",
    "ledger_events",
    "link_of_piece",
    "maker_of",
    "one_line_per_face_answer",
    "one_line_per_kept",
    "ordered",
    "piece_of",
    "recognized_lines",
    "runs_not_drawn",
    "share_makers",
    "stash_box_tables_in",
    "the_file_named",
    "titled",
]

if TYPE_CHECKING:  # pragma: no cover
    # `kernel.workbench` imports `kernel.access`, the package this module sits in, so `Workbench`
    # is a name for the annotations and is never imported at run time.
    from sift.kernel.workbench import Workbench


#: The tables this read touches that a FEATURE owns, and which may therefore not exist.
#:
#: Not a convenience: a process that never imported the faces slice has never registered its
#: schema, so `face_scans` is genuinely absent and a query naming it is a hard error rather than an
#: empty answer. The same shape the attribution migration uses for `folder_people`, and for the
#: same reason.
_FEATURE_TABLES = (
    "file_moves",
    # The nine below are tables that carry a moment for an act on the file. See the reads in
    # `history_sources`.
    "watermark_scans",
    "watermark_reads",
    "watermark_refusals",
    "downloads",
    "semantic_indexed",
    "face_removals",
    "face_ignored",
    "stash_box_kept",
    "asset_stash_box_matches",
    "stash_box_scans",
    "stash_boxes",
    "face_scans",
    "face_confirmations",
    "face_rejections",
    "face_tracks",
    "produced_files",
    "workbench_decisions",
    "workbench_decision_subjects",
    # The music feature's: a file's music fingerprint, and what AcoustID answered about it. See
    # `history_sources._passes_said`.
    "audio_fingerprints",
    "music_lookups",
)


async def _file_named(access: Repository, viewer: Viewer, asset_id: object) -> Link | None:
    """Another FILE a sentence names, or None where there is nothing this user may be told.

    The one place this read resolves something through the access layer rather than reading a table,
    and it is here for the reason `made_from` and `produced_for` in the editing feature do the same:
    a file is the one kind of thing in a history that is itself hidden from people. Everything else
    a sentence names (a person, a tag, a site) is library vocabulary that every user may see.

    None covers two cases that must read alike and do: the other file has been deleted (the column
    is `ON DELETE SET NULL`, so the id is simply gone), and the other file is one this viewer is not
    shown. The caller's wording has to be true of both, because saying "deleted" would be a lie
    exactly when the answer is the one that matters.

    The FIRST location's filename, which is the same one the editing feature's own read names. A
    file in two folders has two, and any of them is the file; picking one is what makes the name in
    the sentence and the name in the link the same run of characters.
    """
    if asset_id is None:
        return None
    found = str(asset_id)
    locations = await access.locations(viewer, found)
    if not locations:
        return None
    return Link(kind="asset", id=found, name=locations[0].filename)


async def history_of_asset(
    database: Database,
    access: Repository,
    viewer: Viewer,
    asset_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
    final_queues: Sequence[str] = (),
    bench: Workbench | None = None,
) -> list[Event]:
    """Everything that happened to one file, oldest first, the newest `limit` kept.

    Unscoped about its subject: the route has already resolved the file through the scoped read.
    Scoped about every OTHER file it names (what this was made from, what was made from it), which
    is why `access` is here: each is resolved through the access layer before it is named
    (`_file_named`). The viewer decides whether an act was this user's own and whether the sharing
    on the file is answered at all.

    `final_queues` names the queues whose decisions can never be taken back. It is passed in
    because the answer lives in the application's registry, which the kernel cannot reach; empty
    offers an Undo the workbench would refuse with an honest sentence, the safe direction.
    """
    kept = max(1, min(limit, MAX_LIMIT))
    present = {str(row["name"]) for row in await database.fetch_all(_TABLES)}
    here = {name for name in _FEATURE_TABLES if name in present}

    added = await database.fetch_one(_ADDED, (asset_id,))
    if added is None:
        return []

    pane = await _pane_of(database, access, viewer, asset_id, present=present, here=here)
    events = await _arrival_events(pane, added, final_queues=final_queues, bench=bench)
    # WHICH RECEIPT WROTE EACH LINK ON THIS FILE, read once for the whole pane by the one rule every
    # thread folds by (`history_folds.RECEIPT_OF_A_NAMING`). This is what the fold below keys on.
    pane.by_receipt = (
        await receipts_on_a_file(database, asset_id)
        if {"workbench_decisions", "workbench_decision_subjects"} <= here
        else {}
    )
    # THE LEDGER, read before the link rows, because it lends them what they do not record (see
    # `Lent`): a person named or a tag added by hand leaves a row with no source and no moment.
    told = await ledger_events(
        database,
        viewer,
        here=VANTAGE_FILE,
        kind="asset",
        subject_id=asset_id,
        grants_as="item",
        limit=kept,
        lent=pane.lent,
    )
    events.extend(await _named_events(pane))
    events.extend(await _tagged_events(pane))
    events.extend(await _filed_events(pane))
    events.extend(await _stash_box_events(pane))
    events.extend(await _face_scan_events(pane, kept))
    events.extend(await _face_answer_events(pane))
    events.extend(await _copy_events(pane))
    events.extend(await _share_events(pane))
    return await _folded(pane, events, told, added, kept)


async def _folders_named(
    database: Database, moves: Sequence[Row], first_place: Row | None
) -> tuple[dict[tuple[str, str], str], str | None]:
    """The ids of the folders the file's moves and arrival name, and the arrival folder's own."""
    arrived_at = (
        (str(moves[0]["root_id"]), str(moves[0]["from_rel_path"]))
        if moves
        else (
            str(first_place["root_id"]),
            str(first_place["archive_rel_path"] or first_place["rel_path"]),
        )
        if first_place is not None
        else None
    )
    named = await folder_ids(database, landing_folders(moves, arrived_at))
    if arrived_at is None:
        return named, None
    return named, named.get((arrived_at[0], say.moved_into(arrived_at[1])))


async def _pane_of(
    database: Database,
    access: Repository,
    viewer: Viewer,
    asset_id: str,
    *,
    present: set[str],
    here: set[str],
) -> _Pane:
    """The reads every source of the pane shares, in the order the thread has always made them."""
    # The ledger's own word. See `_DECIDED` for what the word is doing.
    from sift.kernel.vocabulary import LEDGER_QUEUE

    moves = list(await database.fetch_all(_MOVES, (asset_id,))) if "file_moves" in here else []
    first_place = await database.fetch_one(_FIRST_PLACE, (asset_id,))
    # The folders this reader may see, asked once and only where a line names a folder.
    folders_seen = await _folders_seen(access, viewer) if moves or first_place is not None else {}
    arrival_folder = arrived_in(moves, first_place, folders_seen)
    named_folders, arrival_folder_id = await _folders_named(database, moves, first_place)
    produced = (
        list(await database.fetch_all(_PRODUCED, (asset_id,))) if "produced_files" in here else []
    )
    # What was made OUT of this file: empty for nearly every file, so the copies are resolved one
    # at a time without a cap.
    made_into = (
        list(await database.fetch_all(_MADE_INTO, (asset_id, MOST_COPIES)))
        if "produced_files" in here
        else []
    )
    # Both tables: the statement names both, and a query naming a missing table is a hard error.
    decided = (
        list(
            await database.fetch_all(
                _DECIDED, {**verdict_of(viewer), "subject": asset_id, "ledger": LEDGER_QUEUE}
            )
        )
        if {"workbench_decisions", "workbench_decision_subjects"} <= here
        else []
    )
    # WHAT THE RECEIPTS THAT CARRY A REAL VERB WERE ABOUT, asked only where there is one; a receipt
    # that says `decided` needs nothing here (see `_decided_line`).
    worded = [
        str(row["id"]) for row in decided if row["verb"] is not None and row["verb"] != "decided"
    ]
    receipt_objects = await _receipt_objects(database, worded) if worded else {}
    # WHO PRESSED THE PASSES HERE, read once for the pane (`history_sources.Pressers`).
    pressers = await pressers_of(database, viewer, asset_id, present=present)
    # Before the actors are resolved, so a decision's user is in the single lookup of names.
    who = _Who(
        viewer=viewer,
        names=await _names_of(
            database,
            [str(row["moved_by"]) for row in moves if row["moved_by"] is not None]
            + [str(row["produced_by"]) for row in produced if row["produced_by"] is not None]
            + [str(row["produced_by"]) for row in made_into if row["produced_by"] is not None]
            + [str(row["user_id"]) for row in decided if row["user_id"] is not None],
        ),
    )
    return _Pane(
        database=database,
        access=access,
        viewer=viewer,
        asset_id=asset_id,
        present=present,
        here=here,
        moves=moves,
        folders_seen=folders_seen,
        arrival_folder=arrival_folder,
        folder_ids=named_folders,
        arrival_folder_id=arrival_folder_id,
        produced=produced,
        made_into=made_into,
        decided=decided,
        receipt_objects=receipt_objects,
        pressers=pressers,
        who=who,
    )


async def _arrival_events(
    pane: _Pane, added: Row, *, final_queues: Sequence[str], bench: Workbench | None
) -> list[Event]:
    """The line the file arrived with, its moves, and every decision taken over it."""
    database, viewer, asset_id, here = pane.database, pane.viewer, pane.asset_id, pane.here
    arrival_folder, moves, decided, who = pane.arrival_folder, pane.moves, pane.decided, pane.who
    # Named where there is a name: a file Sift downloaded itself arrived under no folder. Where the
    # ledger says which task brought it in, the line says that instead (a swap names the device it
    # came from); the `added` event itself is never drawn (see `_drawn_elsewhere`).
    from sift.kernel.access.history_events import arrival_of

    arrival = (
        await arrival_of(database, viewer, asset_id)
        if {"workbench_decisions", "workbench_decision_subjects"} <= here
        else None
    )
    events: list[Event] = [
        Event(
            at=int(added["added_at"]),
            actor=Actor.SIFT,
            actor_name=SIFT,
            kind="added",
            pieces=(
                say.added_by(arrival.actor_id, say.payload_of(arrival.payload))
                if arrival is not None and arrival.actor_id and say.from_pass(arrival.actor_id)
                else say.added(
                    added["original_filename"],
                    None if arrival_folder is None else arrival_folder[0],
                    None if arrival_folder is None else arrival_folder[1],
                    pane.arrival_folder_id,
                )
            ),
        )
    ]
    events.extend(_move_events(moves, who, pane.folders_seen, pane.folder_ids) if moves else ())
    # EVERY DECISION WORDED THE ONE WAY the feed and the decision record word it
    # (`worded.decided_said`). Imported here: the reader imports this module for the face-match
    # rule, the cycle `ledger_events` avoids the same way.
    from sift.kernel.access.worded import decided_said, lines_under

    said = await decided_said(
        database, bench, viewer, decided, here=("asset", asset_id, say.HERE[say.VANTAGE_FILE])
    )
    events.extend(
        _decision_events(
            decided,
            who,
            final=frozenset(final_queues),
            named=pane.receipt_objects,
            lines={key: line.pieces for key, line in said.items()},
            under=lines_under(said),
        )
    )
    return events


async def _stash_box_events(pane: _Pane) -> list[Event]:
    """What a stash-box filled in on the file, and each ask that found nothing."""
    database, asset_id, here, pressers = pane.database, pane.asset_id, pane.here, pane.pressers
    events: list[Event] = []
    if {"asset_stash_box_matches", "stash_boxes"} <= here:
        for row in await database.fetch_all(_ENRICHED, (asset_id,)):
            box = str(row["box"])
            by_hand = None if row["automatic"] is None else not bool(row["automatic"])
            grade = None if row["grade"] is None else str(row["grade"])
            page = scene_page(str(row["endpoint"] or ""), row["remote_id"])
            events.append(
                Event(
                    at=None if row["decided_at"] is None else int(row["decided_at"]),
                    actor=Actor.STASH_BOX,
                    actor_name=box,
                    kind="enriched",
                    # What the box wrote that this file's own rows cannot say. The people, tags and
                    # filing are rows the fold counts; `_ROW_FIELDS` is the line between the two.
                    pieces=say.recognized(box, by_hand, _filled_said(row["applied"]), grade=grade),
                    by_hand=by_hand,
                    grade=grade,
                    away=None if page is None else (say.open_on(box), page),
                    wrote=_wrote(row["applied"]),
                    # The same word the `enriched:stash` filter reads.
                    via="stash",
                )
            )
    if {"stash_box_scans", "stash_boxes"} <= here:
        for row in await database.fetch_all(_ASKED, (asset_id,)):
            # SECONDS ALREADY, unlike the face tables: `service.scan_one` writes `self._now()`.
            asked_at = int(row["scanned_at"])
            # Sift did the asking; where somebody pressed the last ask, the line is theirs.
            actor, actor_name, by = by_pressed(pressers.of(_ASKED.name, asked_at))
            events.append(
                Event(
                    at=asked_at,
                    actor=actor,
                    actor_name=actor_name,
                    kind="asked",
                    pieces=say.asked_and_found_nothing(str(row["box"]), int(row["asks"]), by=by),
                    routine=by is None,
                    # NO `via`: the client picks a mark by `via` first, and `stash` would put the
                    # box's own mark ("this box wrote to the file") on the line where it did not.
                )
            )
    return events


async def _face_scan_events(pane: _Pane, kept: int) -> list[Event]:
    """Each look for faces in the file, and who it saw (`face_run_events`); a file last looked at
    before each look was kept has the scan row's one line."""
    database, viewer, asset_id, here = pane.database, pane.viewer, pane.asset_id, pane.here
    if "face_scans" not in here:
        return []
    # WHO, not how many. The scan says a look HAPPENED and when; a file Sift found nothing in has
    # a scan row and no tracks, which is an answer somebody opens this pane to read.
    tracks = (
        list(await database.fetch_all(_FACES_FOUND, (asset_id,))) if "face_tracks" in here else []
    )
    # A face Sift only suggests is said as one that may be somebody (`_faces_found`).
    seen = _faces_found(tracks, asks=viewer.is_admin)
    if {"workbench_decisions", "workbench_decision_subjects"} <= here and (
        runs := await face_run_events(
            database, viewer, asset_id, seen=seen, pressers=pane.pressers, limit=kept
        )
    ):
        return runs
    events: list[Event] = []
    for row in await database.fetch_all(_FACE_SCAN, (asset_id,)):
        scanned_at = _seconds(row["scanned_at"])
        actor, actor_name, by = by_pressed(pane.pressers.of(_FACE_SCAN.name, scanned_at))
        facts = {key: row[key] for key in SCAN_FACTS}
        events.append(
            Event(
                at=scanned_at,
                actor=actor,
                actor_name=actor_name,
                kind="face_run",
                pieces=looked(facts, seen, by),
            )
        )
    return events


async def _face_answer_events(pane: _Pane) -> list[Event]:
    """What Sift recognized on its own, then each Yes and No a person gave, in the order they happen."""
    database, asset_id, here, by_receipt = pane.database, pane.asset_id, pane.here, pane.by_receipt
    events: list[Event] = []
    # `here` rather than `present`: a process that never imported the faces slice has no
    # `face_tracks`, and the statement inside is a hard error rather than an empty answer.
    events.extend(
        await face_matches_of_asset(database, asset_id, tables=sorted(here), by_receipt=by_receipt)
    )
    if "face_confirmations" in here:
        for row in await database.fetch_all(_CONFIRMED, (asset_id,)):
            events.append(
                Event(
                    at=_seconds(row["created_at"]),
                    actor=Actor.SOMEBODY,
                    actor_name=None,
                    kind="confirmed",
                    pieces=say.agreed_to_be(
                        say.thing("person", str(row["person_id"]), str(row["name"]))
                    ),
                    faces=((str(row["person_id"]), asset_id, FACE_SAID_YES),),
                )
            )
    if {"face_rejections", "face_tracks"} <= here:
        for row in await database.fetch_all(_REJECTED, (asset_id,)):
            events.append(
                Event(
                    at=_seconds(row["created_at"]),
                    actor=Actor.SOMEBODY,
                    actor_name=None,
                    kind="rejected",
                    pieces=say.refused_as(
                        say.thing("person", str(row["person_id"]), str(row["name"]))
                    ),
                    faces=((str(row["person_id"]), asset_id, FACE_SAID_NO),),
                )
            )
    return events


async def _copy_events(pane: _Pane) -> list[Event]:
    """What the file was made from, and what was made from it, each named only where it may be."""
    access, viewer, who = pane.access, pane.viewer, pane.who
    events: list[Event] = []
    for row in pane.produced:
        actor, name = who.of(None if row["produced_by"] is None else str(row["produced_by"]))
        named = await _file_named(access, viewer, row["source_asset_id"])
        how = str(row["operation"])
        events.append(
            Event(
                at=int(row["produced_at"]),
                actor=actor,
                actor_name=name,
                kind="copied_from",
                # NAMED WHERE IT CAN BE: an unreachable original and a deleted one both fall back
                # to the sentence that names nothing, so the wording is true of both. The verb is a
                # word on the row (`_COPY_VERBS`).
                pieces=say.copied_from(
                    by_of(actor, name), how, None if named is None else piece_of(named)
                ),
                how=how,
            )
        )
    for row in pane.made_into:
        actor, name = who.of(None if row["produced_by"] is None else str(row["produced_by"]))
        made = await _file_named(access, viewer, row["asset_id"])
        # NOTHING AT ALL where the copy cannot be named: "made from another file" explains what this
        # file is, while "something was made from this" with nothing to go to explains nothing.
        if made is None:
            continue
        how = str(row["operation"])
        events.append(
            Event(
                at=int(row["produced_at"]),
                actor=actor,
                actor_name=name,
                kind="copied_into",
                pieces=say.copied_into(by_of(actor, name), how, piece_of(made)),
                how=how,
            )
        )
    return events


async def _share_events(pane: _Pane) -> list[Event]:
    """Each share of the file with a guest, for an admin only."""
    database, viewer, asset_id = pane.database, pane.viewer, pane.asset_id
    events: list[Event] = []
    if viewer.is_admin:
        granted = list(await database.fetch_all(_GRANTS, (asset_id,)))
        makers = await share_makers(database, viewer, "asset", asset_id, granted)
        for row, (maker, maker_name) in zip(granted, makers, strict=True):
            shared = str(row["effect"]) == "share"
            events.append(
                Event(
                    at=int(row["created_at"]),
                    # WHO SHARED IT, never who it was shared with. See `share_makers`.
                    actor=maker,
                    actor_name=maker_name,
                    kind="shared",
                    pieces=say.shared_with(
                        by_of(maker, maker_name), str(row["username"]), share=shared
                    ),
                )
            )
    return events


async def _folded(
    pane: _Pane, events: list[Event], told: list[Event], added: Row, kept: int
) -> list[Event]:
    """The sources the ledger does not hold, then every fold, then the ledger's lines, in order."""
    database, viewer, asset_id = pane.database, pane.viewer, pane.asset_id
    here, pressers = pane.here, pane.pressers
    # Before the unread sources, because one asks it: `hidden_at` says only that a file is hidden
    # NOW, so where the ledger holds this user's own hides the column's line is not drawn.
    events.extend(
        await _unread_sources(
            database,
            viewer,
            asset_id,
            here=here,
            hides_recorded=any(one.kind in ("hidden", "revealed") for one in told),
            filed_by_watermark=any(
                one.kind == "filed" and one.via == "watermark" for one in events
            ),
            arrived=(int(added["added_at"]), added["original_filename"]),
            pressers=pressers,
        )
    )
    # A stash-box answer kept and the receipt of that press are one line (`one_line_per_kept`).
    events = one_line_per_kept(events)
    # And a Yes or a No on a face here and its receipt are one line (`one_line_per_face_answer`).
    events = one_line_per_face_answer(events)
    # Folded BEFORE the sort and the cap, so a pair made one line frees a place for an older line.
    # THE ORDER IS LOAD-BEARING: the face fold reads the plain lines' `via` and person link;
    # `_one_line_per_act` matches the plain sentences a receipt was written against; `_said_by`
    # rewrites what is left, after which neither could match.
    events = _said_by(_one_line_per_download(_one_line_per_act(_one_line_per_face_act(events))))
    # THE HOUSEKEEPING IS ONE LINE A SITTING (`_one_processed_line`).
    events = _one_processed_line(events)
    # THE LEDGER'S LINES GO IN AFTER THE FOLDS: a fold says once the rows one press wrote, every
    # one a link still there, while a ledger line is drawn because its link is GONE.
    events.extend(_pressed_fingerprints(one, pressers) for one in told)
    # EVERY PRESS NO LINE SAID, last, once every pass line above has claimed its own (`press_lines`).
    events.extend(press_lines(pressers))
    events = ordered(events)
    return events[-kept:]


#: The ledger act that says the fingerprints were made, as the passes' declaration names it.
_FINGERPRINTED = f"{LEDGER_SAID}scanned"


def _pressed_fingerprints(event: Event, pressers: Pressers) -> Event:
    """The ledger's fingerprints line as the presser's, where somebody pressed the pass that wrote it.

    The one pass whose line is an act the ledger keeps rather than a row of its own: the writer
    records Sift as its actor (`identity.record_fingerprints`), which is true of who did the work
    and silent about who asked for it. So the line is said again here, as every other pass line on
    this pane is, from the record of presses beside it; its empty answer keeps its own words.
    """
    if event.kind != "scanned" or event.actor is not Actor.SIFT:
        return event
    actor, actor_name, by = by_pressed(pressers.of(_FINGERPRINTED, event.at))
    if by is None:
        return event
    empty = say.text_of(event.pieces) == say.text_of(
        say.nothing_to_fingerprint(SIFT, say.HERE[VANTAGE_FILE])
    )
    return replace(
        event,
        actor=actor,
        actor_name=actor_name,
        pieces=say.fingerprinted_by(by, empty=empty),
    )
