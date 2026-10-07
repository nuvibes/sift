# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proposing duplicates, and acting on what a person decides about them.

The rule this whole feature is built around: **Sift proposes, a person disposes.** Nothing here
removes a file, nothing here decides that two files are the same, and nothing here acts on a
judgement nobody made. A scan writes rows; a person reads them and says what to do; and the doing
goes out through the one component in Sift allowed to touch a file.

That last part is not politeness. Removal is reached through a `Protocol` declared here and
satisfied by the delete feature, so this slice cannot unlink even by accident: there is no
filesystem call in this file to get wrong, and a static rule in the build refuses one if anybody
adds it later. Everything routed that way is permanent, is refused for a guest, and is refused
for a folder Sift was not given write access to. None of those three checks live here, and all
three still apply, which is why the sentence in front of the button says so.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from sift.kernel.access import Viewer
from sift.kernel.access.catalog import (
    Carried,
    attribution_of_files,
    carry_attribution_on,
    uncarry_attribution_on,
)
from sift.kernel.access.history_events import names_now
from sift.kernel.access.sentences import carried_sentence
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now, telling
from sift.kernel.content.duplicates import (
    Copy,
    DuplicateReads,
    Place,
    Redundancy,
    RedundantTotals,
)
from sift.kernel.db import Connection, Database, Row, in_clause
from sift.kernel.ids import new_id
from sift.kernel.ledger import Object as LedgerObject
from sift.kernel.log import get_logger
from sift.kernel.memo import MarkedMemo
from sift.kernel.seams import SettingsSeam
from sift.kernel.sql_splice import splice
from sift.kernel.vocabulary import Subject
from sift.kernel.wiring import Part
from sift.kernel.workbench import Recorder
from sift.slices.dedup.grouping import (
    DEFAULT_MAX_GROUP,
    Group,
    PairRow,
    Rule,
    group_pairs,
)
from sift.slices.dedup.matcher import (
    DEFAULT_ACCURACY,
    DEFAULT_MAX_DURATION_GAP_MS,
    LEVELS,
    Accuracy,
    Matcher,
    Pair,
)

log = get_logger(__name__)

#: What this queue is called wherever it is stored: in the workbench registry, and in every
#: receipt it writes. Declared here rather than in the queue adapter beside it, because the service
#: writes the receipts and the two must not be able to disagree about the name on them.
WORKBENCH_QUEUE = "duplicates"

#: And what the exact-copy queue is called. A second name because they are two cards answering two
#: different questions ("are these the same thing" and "which of these copies do you want") and
#: a receipt has to say which of the two it came from.
RECLAIM_QUEUE = "copies"

Method = Literal["phash", "videohash", "video_phash"]
Status = Literal["pending", "confirmed", "dismissed"]
Verdict = Literal["confirmed", "dismissed"]


class DedupError(ValueError):
    """Something the caller asked for cannot be done."""


class NotFound(DedupError):
    """No such candidate, or no such copy."""


class NotAllowed(DedupError):
    """The user may not do this."""


class RemovalRefused(DedupError):
    """The removal seam refused, for a reason that is neither absence nor permission.

    A read-only folder, a name already taken, a disk that will not accept the write. The request
    was reasonable and the state of the disk says no, which is a different answer from either of
    the two above and deserves its own.

    It exists here rather than being the delete feature's own exception because this slice depends
    on the *shape* of removal and not on the feature that implements it. Translating one into the
    other is the wiring's job (see where the application is assembled), which is what keeps this
    package able to be tested against a stand-in that removes nothing.
    """


class Remover(Protocol):
    """The removal seam, as this slice needs it.

    Refusals come back as `NotFound`, `NotAllowed` or `RemovalRefused` from this module. A protocol
    cannot say that in its signature, so it is said here: an implementation that raises its own
    exceptions is adapted at the point it is wired in, not caught by guesswork downstream.

    Declared here rather than imported, so this feature depends on the shape of removal and not on
    the feature that implements it, which also means a test can stand in something that records
    what it was asked to do and removes nothing.

    `location_id` is the parameter the reclaim view is built on. Without it, removal takes every
    place an asset sits, which for a file with three copies is the file gone. With it, one copy
    goes and the asset survives with the rest, which is precisely "delete the extras and keep
    the file", the entire operation the reclaim screen offers.
    """

    async def remove(
        self,
        asset_id: str,
        *,
        mode: Literal["sift", "disk"],
        actor: Viewer,
        location_id: str | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class DuplicatePlan:
    """What a sweep now would file: the pairs it finds that are not recorded yet."""

    #: How many files had a fingerprint to compare.
    compared: int
    pairs: tuple[Pair, ...]


@dataclass(frozen=True, slots=True)
class Candidate:
    """One pair somebody still has to judge."""

    id: str
    asset_a: str
    asset_b: str
    method: Method
    distance: int
    status: Status
    created_at: int
    #: How far apart the two run, in milliseconds, or None when nobody can say: an unfinished
    #: probe, a kind with no duration, or a pair filed before the figure was recorded at all.
    duration_gap_ms: int | None = None


@dataclass(frozen=True, slots=True)
class Settled:
    """What one group's confirm actually did, which is not always what it was asked to do.

    A page confirm is one press over two dozen groups, and a folder Sift was never given write
    access to refuses one file without saying anything about the rest. Reporting per group what
    went and what would not is what lets the screen say "twenty-two done, two could not be deleted",
    rather than either failing the whole press or claiming a success it did not have.
    """

    ids: tuple[str, ...]
    kept: str
    removed: tuple[str, ...]
    #: The files the disk would not let go of. The group stays in the queue with them in it.
    refused: tuple[str, ...]


#: Every pair already recorded, whatever was answered about it: a sweep never files one again.
_RECORDED_PAIRS = "SELECT asset_a, asset_b, method FROM dedup_candidates"

_INSERT_CANDIDATE = """
INSERT INTO dedup_candidates
       (id, asset_a, asset_b, method, distance, duration_gap_ms, status, created_at)
VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
ON CONFLICT(asset_a, asset_b, method) DO NOTHING
"""

# A pair filed as already answered. No distance, because nothing measured these two: the answer
# comes from knowing how one of them was made, not from comparing them. `DO NOTHING` matters as
# much here as above: a pair somebody has already looked at and confirmed must not be quietly
# turned back into "different" by a later production.
_INSERT_SETTLED = """
INSERT INTO dedup_candidates
       (id, asset_a, asset_b, method, distance, duration_gap_ms, status, created_at)
VALUES (?, ?, ?, ?, NULL, NULL, 'dismissed', ?)
ON CONFLICT(asset_a, asset_b, method) DO NOTHING
"""

#: WHICH WAITING PAIRS THE DIALS SHOW: the one rule the queue is read through, written once.
#:
#: Spliced into the three statements below (the page, the whole read the groups are built from,
#: and the count) rather than written out in each. Every statement in Sift is still a literal:
#: `splice` puts one module constant into another at import, checks that every marker is filled
#: and every fragment is used, and nothing that arrives at run time goes near the SQL text: the
#: dials are bound, from `_filter_params`, which is the only place they become numbers. Three
#: copies would be three chances to drift, one of them inside a `COUNT(CASE)` where only the count
#: loosens. A test holds the page and the count to agree at every level and every length rule.
#:
#: The threshold is chosen per method inside the rule rather than by the caller picking one number,
#: because the three methods measure on three different scales (bits out of 63, frames out of 30,
#: bits out of 64) and one figure applied to all of them would mean three different strictnesses
#: wearing one name.
#:
#: `duration_gap_ms IS NULL OR ...` is not defensive. NULL means nobody can say how far apart the
#: two run, and the honest treatment of a fact nobody has is to show the pair and let a person
#: look, never to hide it, which is how a length rule quietly becomes a way of losing duplicates.
_DIALS_SHOW = """distance <= CASE method
         WHEN 'phash'       THEN :phash
         WHEN 'video_phash' THEN :video_phash
         WHEN 'videohash'   THEN :videohash
         ELSE 0
       END
   AND (duration_gap_ms IS NULL OR :gap IS NULL OR duration_gap_ms <= :gap)"""

#: One page of the pairs these dials show, closest first, by keyset.
_PENDING = splice(
    """
SELECT id, asset_a, asset_b, method, distance, duration_gap_ms, status, created_at
  FROM dedup_candidates
 WHERE status = 'pending'
   AND {{DIALS_SHOW}}
   AND (:after_distance IS NULL OR (distance, id) > (:after_distance, :after_id))
 ORDER BY distance, id
 LIMIT :limit
""",
    DIALS_SHOW=_DIALS_SHOW,
)

#: Every pending pair these dials show, in one read. What the grouping is computed from.
#:
#: No LIMIT and no keyset. Groups are the unit a person decides in, and a group is a property of the
#: WHOLE pair graph (three copies of one clip are three pairs that may sit anywhere in the queue),
#: so a page of pairs cannot be clustered into anything truthful. It goes through the sweep lane
#: for the same reason every other whole-library read does: it is bounded by the library rather than
#: by a screen, and the lane is what keeps one of those from taking the connection pool down with
#: it.
#:
#: Ordered so the closest pair in the library is read first. Nothing downstream depends on it
#: (the groups are sorted by their own closest pair afterwards), but a deterministic read is what
#: makes two runs over an unchanged library produce the same list, which is the property the memo
#: above this rests on.
_PENDING_ALL = splice(
    """
SELECT id, asset_a, asset_b, method, distance
  FROM dedup_candidates
 WHERE status = 'pending'
   AND {{DIALS_SHOW}}
 ORDER BY distance, id
""",
    DIALS_SHOW=_DIALS_SHOW,
)

#: How many pairs the settings show, and how many are waiting in all.
#:
#: Both numbers from one statement, because the screen has to be able to say "showing 4 of 61": a
#: filter that silently removes rows from a review queue is the same failure as a scan that never
#: found them, and the only difference a reader can see is a number saying so.
_PENDING_COUNTS = splice(
    """
SELECT COUNT(*) AS held,
       COUNT(CASE WHEN {{DIALS_SHOW}} THEN 1 END) AS shown
  FROM dedup_candidates
 WHERE status = 'pending'
""",
    DIALS_SHOW=_DIALS_SHOW,
)

#: How many pairs there are in all, whatever their state: what says a file's leaving took some.
_PAIRS_HELD = "SELECT COUNT(*) AS n FROM dedup_candidates"

_CANDIDATE = """
SELECT id, asset_a, asset_b, method, distance, duration_gap_ms, status, created_at
  FROM dedup_candidates
 WHERE id = ?
"""

_SETTLE = "UPDATE dedup_candidates SET status = ? WHERE id = ?"

#: Every pair of one group, answered in one go. `status = 'pending'` in the WHERE so a second press
#: on a group already settled changes nothing rather than re-settling rows somebody has since taken
#: back another way.
_DISMISS_GROUP = """
UPDATE dedup_candidates
   SET status = 'dismissed'
 WHERE status = 'pending'
   AND id IN (?*)
"""

#: How many of these pairs are back in the queue. What says whether an undo did anything.
_PENDING_AMONG = """
SELECT COUNT(*) AS back
  FROM dedup_candidates
 WHERE status = 'pending'
   AND id IN (?*)
"""

#: And the undo of that, which is the same rows put back. Only from `dismissed`, so a group whose
#: files were deleted (those rows are gone with them) restores nothing rather than resurrecting
#: a question about a file that is not there.
#: What each file in a group is called is read BEFORE anything in it is deleted: a deleted file's
#: row goes with it, and the receipt has to be able to say what went. The read is the kernel's
#: (`names_now`): a slice writes no SQL against the assets table.

#: How many files a receipt's title names before it counts the rest. A title is one line.
_NAMED_IN_A_TITLE = 3

_UNDISMISS_GROUP = """
UPDATE dedup_candidates
   SET status = 'pending'
 WHERE status = 'dismissed'
   AND id IN (?*)
"""


class DedupService:
    """The near-duplicate queue and the reclaim view. One per application."""

    def __init__(
        self,
        database: Database,
        reads: DuplicateReads,
        remover: Remover,
        *,
        clock: Callable[[], float] = time.time,
        recorder: Recorder | None = None,
    ) -> None:
        self._db = database
        self._reads = reads
        self._remover = remover
        self._clock = clock
        #: Where a decision is written down, so the workbench can show it and take it back.
        #:
        #: Optional, and absent in a test that has no workbench to write to. A decision taken
        #: without one still happens and still lasts: the record is how somebody sees WHAT they
        #: decided, not what makes the deciding work.
        self._recorder = recorder
        #: The groups, kept until a pair moves. See `groups`.
        #:
        #: Keyed on the DIALS as well: both dials and the keeper rule are settings, and a
        #: different setting is a different answer about pairs that have not moved.
        self._groups: MarkedMemo[list[Group]] = MarkedMemo()
        #: How many writes to the pairs this service has made. See `groups`.
        self._moved = 0

    def _now(self) -> int:
        return int(self._clock())

    # --- finding them ---------------------------------------------------------------------

    async def scan(self) -> int:
        """Compare every fingerprint in the library and file the pairs worth asking about.

        Returns how many pairs were newly filed, which on a second run over an unchanged library
        is zero, and that is the property the queue depends on. What it files is `plan`'s answer,
        so a dry run and the sweep cannot disagree. Every pair the matcher finds is
        offered for insertion every time; the ones already there collide with the unique key and
        are left exactly as they are, **including their status**. That is what makes "these are
        different, stop asking" stick: a dismissed row is not reset by the scan that finds the same
        pair again, because the scan does not update, it only inserts.

        The comparison runs on a thread and the writing does not. Only the fold is arithmetic;
        every row below goes through the writer's guard on the loop, where one writer means one
        writer.
        """
        planned = await self.plan()
        filed = 0
        for pair in planned.pairs:
            filed += await self._file(pair)
        log.info("dedup.filed", assets=planned.compared, pairs=len(planned.pairs), filed=filed)
        return filed

    async def plan(self) -> DuplicatePlan:
        """Compare every fingerprint and answer the pairs not recorded yet. Writes nothing."""
        fingerprints = await self._reads.fingerprints()
        matcher = Matcher()
        # Off the event loop, and on a thread rather than on a mechanism of its own: this is what
        # the face grouping does with the same shape of problem. The read above is fast (about a
        # hundred thousand rows in a tenth of a second) and what costs is what Python then does
        # with those rows: the block index, and the tens of millions of comparisons that come off
        # it, which on a large library holds a loop for tens of seconds. One process, one loop: that
        # is every screen, every video and the job feed, frozen, while a background job does sums.
        #
        # A thread and not a chunked fold, and the two are not interchangeable. The block search is
        # complete only over the WHOLE set, so dividing the input means duplicates that are never
        # found and nothing that can say which: the one failure this feature exists to avoid.
        # Yielding between assets instead would keep the set whole, but it would be a second way of
        # stepping off the loop beside the one the rest of the application already uses, written
        # into the hot path of the comparison it is supposed to leave alone.
        #
        # The fold grows with the library and so does the thread, so there is nothing here to
        # re-decide as a library gets bigger. What it does not buy is a faster scan: a thread takes
        # the interpreter's lock in turn, so the loop is SLOWED rather than left alone, and the
        # fold pays for that in its own wall clock. Slowed is a stutter and held is a freeze, and
        # only one of the two is a fault. The cost to the fold itself is lost in run-to-run noise;
        # the longest a waiting task goes unwoken drops from the whole fold to a few milliseconds.
        pairs = await asyncio.to_thread(matcher.find, fingerprints)
        # One read of what is recorded rather than one per pair. `_file` still asks again, so a
        # pair filed between this read and the write is left as it is.
        recorded = {
            (str(row["asset_a"]), str(row["asset_b"]), str(row["method"]))
            for row in await self._db.fetch_all(_RECORDED_PAIRS)
        }
        fresh = tuple(
            pair for pair in pairs if (pair.asset_a, pair.asset_b, pair.method) not in recorded
        )
        log.info(
            "dedup.scanned",
            assets=len(fingerprints),
            comparisons=matcher.comparisons,
            pairs=len(pairs),
            fresh=len(fresh),
        )
        return DuplicatePlan(compared=len(fingerprints), pairs=fresh)

    async def _file(self, pair: Pair) -> int:
        """Record one pair, unless it is already recorded. Returns 1 if it was new."""
        before = await self._db.fetch_one(
            "SELECT 1 FROM dedup_candidates WHERE asset_a = ? AND asset_b = ? AND method = ?",
            (pair.asset_a, pair.asset_b, pair.method),
        )
        if before is not None:
            return 0
        await self._say(
            _INSERT_CANDIDATE,
            (
                new_id(),
                pair.asset_a,
                pair.asset_b,
                pair.method,
                pair.distance,
                pair.duration_gap_ms,
                self._now(),
            ),
        )
        return 1

    # --- reading them ---------------------------------------------------------------------

    async def groups(self, dials: Dials) -> list[Group]:
        """Every group these dials make of the queue, most alike first.

        A GROUP rather than a pair, and that is the whole of this feature's shape. Three copies of
        one clip are three pairs, so a screen of pairs would ask three questions about one thing
        and could be given three answers that contradict each other. What a person decides about is the
        set: these N files, one of them stays.

        Nothing is stored. A group is not a fact about the library, it is a fact about the library
        AT A SETTING (both dials are applied when the queue is read), so storing them would mean
        every dial change invalidating a table. Computed on the way out there is nothing to
        invalidate, and the memo is what stops the same computation happening twice.

        **Kept until a pair moves, not until anything is announced.** The pairs are written here
        and nowhere else (`_say`, `dismiss`), and each of those writes counts in `_moved`; a file
        that leaves the library takes its pairs with it by the table's cascade, which only ever
        removes rows, so the table's row count says so. A file landing, a job finishing, a tag
        added to some other file: none of them is a new group, and a library mid-import announces
        those several times a second. Everything about a group is a fact about the whole library
        and about nobody in particular, which is what makes one answer safe to hand to every admin;
        deciding what any of them may SEE happens at the route, where a session is known.
        """
        (row,) = await self._db.fetch_all(_PAIRS_HELD)
        return await self._groups.get(
            (dials.level.value, dials.max_duration_gap_ms, dials.rule),
            f"{self._moved}:{row['n']}",
            lambda: self._grouped(dials),
        )

    async def _grouped(self, dials: Dials) -> list[Group]:
        """Read the pairs, read what a rule compares, and cluster. The expensive half."""
        rows = await self._db.sweep_all(
            _PENDING_ALL,
            _filter_params(dials.level, dials.max_duration_gap_ms),
            what="near-duplicate pairs",
        )
        pairs = [
            PairRow(
                id=row["id"],
                asset_a=row["asset_a"],
                asset_b=row["asset_b"],
                method=row["method"],
                distance=row["distance"],
            )
            for row in rows
        ]
        # Only the files that are actually in a pair. The rule's numbers are columns of the asset
        # table, and reading them for the whole library would read them for every file that is in
        # no group, which on a library where duplicates are a small fraction is nearly all of it.
        wanted = sorted({side for pair in pairs for side in (pair.asset_a, pair.asset_b)})
        facts = await self._reads.measures_of(wanted) if wanted else {}
        # OFF THE LOOP, for the same reason the scan's comparison is, and here somebody is waiting
        # on it. Tens of thousands of pending pairs fold in a few hundred milliseconds WITH the
        # files' figures in hand: over the watchdog's own quarter-second threshold, every time
        # the Duplicates screen is opened at a dial nothing has answered yet, and growing with the
        # library. `group_pairs` is pure and is handed everything it reads, so there is nothing in
        # here for a thread to race: no database, no settings, no shared mutable state.
        #
        # The memo above is what stops this running twice, and it is NOT what makes the fold safe:
        # a memo makes the second reader cheap and does nothing at all for the first.
        return await asyncio.to_thread(
            group_pairs, pairs, facts=facts, rule=dials.rule, cap=DEFAULT_MAX_GROUP
        )

    async def pending_counts(
        self,
        *,
        level: Accuracy = DEFAULT_ACCURACY,
        max_duration_gap_ms: int | None = DEFAULT_MAX_DURATION_GAP_MS,
    ) -> tuple[int, int]:
        """How many pairs these settings show, and how many are waiting in all.

        Two numbers rather than one, so the screen can say what the filter is holding back. A
        review queue that quietly drops rows reads exactly like a library with no duplicates in it.
        """
        # Unpacked rather than guarded. `_PENDING_COUNTS` is a bare aggregate over one table, so
        # it answers with exactly one row on an empty table as readily as on a full one, and a
        # "there was no row" branch beside it could never run, which is a line no test can reach
        # and a reader has to work out is dead.
        (row,) = await self._db.fetch_all(
            _PENDING_COUNTS, _filter_params(level, max_duration_gap_ms)
        )
        return int(row["shown"] or 0), int(row["held"] or 0)

    async def places_of(self, asset_ids: Sequence[str]) -> dict[str, Place]:
        """Where each of these files currently sits, for a screen naming several together.

        A pass-through, and it exists because this slice reaches the content tables through one
        declared set of reads and nothing else: a query written outside that set is a query with
        no permission rule in it, which is a lint rather than a convention. See `DuplicateReads`.
        """
        return await self._reads.places_of(asset_ids)

    async def awaiting_fingerprint(self) -> int:
        """How many videos nothing has fingerprinted yet, so cannot be in the queue at all."""
        return await self._reads.awaiting_fingerprint()

    async def cannot_fingerprint(self) -> int:
        """How many files can never be in the queue, because the decoder refused their frames."""
        return await self._reads.cannot_fingerprint()

    async def get(self, candidate_id: str) -> Candidate:
        row = await self._db.fetch_one(_CANDIDATE, (candidate_id,))
        if row is None:
            raise NotFound("There's no such pair.")
        return _candidate_from_row(row)

    async def _say(self, sql: str, params: Sequence[object]) -> None:
        """Write, and tell the screens that list what changed.

        Every write here that moves something a screen draws goes through this rather than straight
        to the database: a new one is added by writing a statement, which is the moment when nothing
        reminds anybody that a screen somewhere is showing the old answer.

        Every admin, because these are admin-only decisions about somebody's library and the screens
        that draw them refuse a guest. Announced after the write rather than on its commit, since
        these are single statements outside any transaction of their own.
        """
        await self._db.execute(sql, params)
        self._moved += 1
        announce_now(EVERY_ADMIN, About.LIBRARY)

    async def mark_unrelated(self, first_asset_id: str, second_asset_id: str) -> None:
        """Settle a pair as different before anybody is asked about it.

        For a feature that deliberately produces a file resembling one already in the library:
        a compressed copy is the same picture at a smaller size, so every method here matches it
        against its original, and every one of them would be a question with an obviously wrong
        answer available.

        Written for all three methods, because which of them will match is a property of the files
        rather than of the intent, and a pair dismissed under one method would still be asked under
        another. Ordered by id, the way the matcher orders one, so the row a later scan tries to
        insert is exactly the row already sitting here, and the insert does nothing rather than
        resetting it, which is what makes the answer last.

        Nothing happens if the two are the same asset: identical bytes are one asset in two places
        and are not a question this table asks.
        """
        if first_asset_id == second_asset_id:
            return
        asset_a, asset_b = sorted((first_asset_id, second_asset_id))
        for method in ("phash", "videohash", "video_phash"):
            await self._say(_INSERT_SETTLED, (new_id(), asset_a, asset_b, method, self._now()))
        log.info("dedup.marked_unrelated", asset_a=asset_a, asset_b=asset_b)

    # --- acting on them -------------------------------------------------------------------

    async def settle_group(self, group: Group, *, keep: str, actor: Viewer) -> Settled:
        """Keep one file of a group and delete the rest from the disk.

        **It takes a `Group` and not a list of ids, and that is the safety.** A group can only come
        from `groups` above, which is the server's own clustering of its own table, so there is no
        way for a request to name a set of files and have them deleted together. The one thing a
        caller can still get wrong is which of them to keep, and that is checked here.

        `keep` stays, everything else in the group goes, and every removal is the same permanent
        one the delete feature performs: refused for a guest, refused for a folder Sift was not
        given write access to. **Nothing settles the pairs afterwards and nothing needs to:** a
        deleted file takes its rows with it through the foreign key. A file the disk would not let
        go of keeps its rows too, which is right: it is still there and still a duplicate, and
        marking that pair answered would hide a real one that nobody ever decided.

        Whoever calls this has to have established that this user may see every file in the
        group. That check needs a session and this class has none; see the route.
        """
        if keep not in group.ids:
            raise NotFound("That file isn't part of this group.")
        if group.too_big:
            # Not a judgement about the files: a refusal to act on a claim nobody can check. A
            # group past the cap is a CHAIN of pairs whose ends look nothing alike, so "keep this
            # one" would be deleting hundreds of files on the strength of a transitive hop.
            raise NotAllowed("That group is too long a chain to settle in one press.")

        # The names first: the files about to go take their rows with them.
        names = await self._names_of(group.ids)
        removed: list[str] = []
        refused: list[str] = []
        for one in group.ids:
            if one == keep:
                continue
            try:
                await self._remover.remove(one, mode="disk", actor=actor)
            except DedupError:
                # One file the disk would not give up does not stop the rest, and it must not lose
                # the receipt for the ones that went. What it does is stay in the queue.
                refused.append(one)
                continue
            removed.append(one)

        settled = Settled(ids=group.ids, kept=keep, removed=tuple(removed), refused=tuple(refused))
        if removed:
            async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
                await self._record_group(
                    connection,
                    ids=settled.ids,
                    kept=settled.kept,
                    removed=settled.removed,
                    refused=settled.refused,
                    pairs=(),
                    actor=actor,
                    names=names,
                )
        log.info(
            "dedup.group.settled",
            files=len(group.ids),
            kept=keep,
            removed=len(removed),
            refused=len(refused),
            actor=actor.id,
        )
        return settled

    async def dismiss_group(self, group: Group, *, actor: Viewer) -> int:
        """Say the files in a group are not the same thing. Removes nothing, and it has to last.

        Every pair inside it is written `dismissed` and stays in the table forever, which is the
        only thing that makes the answer survive the next scan: that scan computes exactly the
        same distances and offers exactly the same pairs, and an insert that collides does nothing
        rather than resetting a row. A queue that re-asks a question somebody has answered is one
        people stop opening.

        Returns how many pairs were answered, which is not the size of the group: a group of three
        is two pairs, and one of eight can be seven or twenty-eight.
        """
        if not group.pairs:
            return 0
        sql, params = in_clause(_DISMISS_GROUP, list(group.pairs))
        # The names before the transaction: the read is the kernel's, on its own connection.
        names = await self._names_of(group.ids)
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            await connection.execute(sql, params)
            await self._record_group(
                connection,
                ids=group.ids,
                kept=None,
                removed=(),
                refused=(),
                pairs=group.pairs,
                actor=actor,
                names=names,
            )
        # After the commit: counted inside it, a reader between the two would keep the groups of
        # before the write under the count of after it.
        self._moved += 1
        log.info(
            "dedup.group.dismissed", files=len(group.ids), pairs=len(group.pairs), actor=actor.id
        )
        return len(group.pairs)

    async def restore_group(self, candidate_ids: Collection[str]) -> bool:
        """Put a dismissed group's pairs back in the queue. What undo reaches.

        Only from `dismissed`, and only for rows still there. A group whose files were deleted has
        no rows left to restore (they went with the files) and "nothing was put back" is the
        honest answer to that rather than an error about pairs nobody asked about.
        """
        if not candidate_ids:
            return False
        wanted = sorted(candidate_ids)
        await self._say(*in_clause(_UNDISMISS_GROUP, wanted))
        counted, params = in_clause(_PENDING_AMONG, wanted)
        row = await self._db.fetch_one(counted, params)
        return row is not None and int(row["back"] or 0) > 0

    async def _names_of(self, ids: Sequence[str]) -> dict[str, str]:
        """Each file's name, by id. A file with no name known is absent and said as "a file".

        Asked only about a group's files, and a group is two files at the least.
        """
        named = await names_now(self._db, {"asset": list(ids)})
        return {asset_id: name for (_kind, asset_id), name in named.items()}

    async def _record_group(
        self,
        connection: Connection,
        *,
        ids: tuple[str, ...],
        kept: str | None,
        removed: tuple[str, ...],
        refused: tuple[str, ...],
        pairs: tuple[str, ...],
        actor: Viewer,
        names: Mapping[str, str] | None = None,
    ) -> None:
        """Write down what was decided about a group, in words somebody can read a week later.

        THE TITLE NAMES THE FILES, never a bare "These are different" or "Kept one of a group": the
        History pane's rule is that every line says what happened. `names` is read before anything
        is deleted (see
        `settle_group`), so the files that went are named as well as the one that stayed.

        ONE receipt for the group, never one per pair. A group of four copies settled as four
        separate lines would be four rows in the record for one press, three of which nobody made,
        and undo would offer to take back a quarter of a decision.

        The sentence is written HERE, at the moment of the decision, rather than assembled when the
        record is read: what a decision DID is a fact about the moment it happened, and a sentence
        built later describes the library as it is now, which is exactly what somebody reading a
        list of past decisions is trying to look behind.

        `removed` in the payload is what undo reads. A group that deleted nothing can be put back
        by restoring its pairs; one that deleted a file cannot be put back at all, and the record
        says so in words as well.

        Every file in the group is a SUBJECT, including the ones that were deleted. Free (the
        list is the argument), and right: this decision is exactly as much a part of a kept file's
        history as of a deleted one's, and it is the kept file somebody is looking at when they
        wonder where the others went.
        """
        if self._recorder is None:
            return
        files = f"{len(ids)} files"
        called = names or {}
        if not removed:
            title = f"{_named(ids, called)} are different"
            detail = f"All {files} were kept, and these will not be raised again."
        else:
            gone = len(removed)
            left = f" {len(refused)} could not be deleted and are still here." if refused else ""
            title = (
                f"Kept {_named((kept,) if kept else (), called) or 'one file'},"
                f" deleted {_named(removed, called)}"
            )
            detail = (
                f"Of {files} that look alike, one was kept and {gone} "
                f"{'was' if gone == 1 else 'were'} deleted from your disk. "
                f"That cannot be undone.{left}"
            )
        await self._recorder.record_on(
            connection,
            queue=WORKBENCH_QUEUE,
            user_id=actor.id,
            title=title,
            detail=detail,
            payload=json.dumps(
                {
                    "group": list(ids),
                    "kept": kept,
                    "removed": list(removed),
                    "candidates": list(pairs),
                }
            ),
            # By the names read BEFORE the delete: a file that went has no row left for the ledger's
            # door to name it from, and the door refuses a file with no name. One that stayed is
            # named by the door itself where `names` was not read (a dismissal deletes nothing).
            subjects=[Subject(kind="asset", id=one, name=called.get(one)) for one in ids],
        )

    async def unsettle(self, candidate_id: str) -> bool:
        """Put a judged pair back in the queue. What undo reaches.

        Only from settled back to pending, and only for a pair still there: a decision whose two
        files have since been deleted has no row left to restore, and saying "nothing was put back"
        is the honest answer rather than an error about a pair nobody asked about.
        """
        await self._say(
            "UPDATE dedup_candidates SET status = 'pending'"
            " WHERE id = ? AND status IN ('confirmed', 'dismissed')",
            (candidate_id,),
        )
        row = await self._db.fetch_one(
            "SELECT status FROM dedup_candidates WHERE id = ?", (candidate_id,)
        )
        return row is not None and row["status"] == "pending"

    # --- reclaiming space -----------------------------------------------------------------

    async def reclaim(self, *, limit: int, offset: int) -> list[Redundancy]:
        """A page of the assets sitting in more than one place, with every copy of each.

        No comparison and no judgement: these are identical bytes, already resolved into one asset
        with several locations when they were imported. The only open question is which copies to
        keep, and that is a question only a person can answer: a second copy on a second disk may
        be the whole point of it.

        Paged, because on a large library this is thousands of assets, and reading all of them to
        draw twenty would be the whole cost of the screen.
        """
        return await self._reads.redundancies_page(limit=limit, offset=offset)

    async def reclaim_position(self, asset_id: str) -> int | None:
        """Where one file stored more than once sits on the list `reclaim` pages, or None when it
        is not on it any more. What a page asked for by its first row starts at."""
        return await self._reads.redundancy_position(asset_id)

    async def reclaim_totals(self) -> RedundantTotals:
        """How many there are and what the extras add up to, without reading a path.

        Whole-library while the page above is a page, so the sentence over the list can say what
        the list is a part of. Both numbers come from one statement, so they cannot disagree.
        """
        return await self._reads.redundant_totals()

    async def release(self, asset_id: str, location_id: str, *, actor: Viewer) -> None:
        """Let go of one copy of an asset, keeping the others and keeping the asset.

        The narrowest thing this feature ever does. `location_id` is what makes it narrow: removal
        takes one place the bytes sit and the asset keeps everything recorded about it, because it
        still sits somewhere else. Losing the last copy is what ends an asset, and this refuses to
        be the thing that does that: an asset with one copy is not redundant, so `redundancy_for`
        answers None for it, and a request naming a copy that is not there is a 404.

        The receipt is written AFTER the removal and on a connection of its own, which is the one
        place in this feature where a record is not atomic with what it records. It cannot be: what
        happens here is a file being unlinked from a disk, and no database transaction has ever been
        able to include one. Writing it first would claim a deletion that may then fail; writing it
        after can lose the record of one that happened. The second is the better failure: the file
        is gone either way, and a missing line in a list of past decisions is recoverable knowledge
        where a line about a deletion that never happened is not.
        """
        entry = await self._reads.redundancy_for(asset_id)
        if entry is None:
            raise NotFound("There's no such copy.")
        copy = next((one for one in entry.copies if one.location_id == location_id), None)
        if copy is None:
            raise NotFound("There's no such copy.")

        await self._remover.remove(asset_id, mode="disk", actor=actor, location_id=location_id)
        log.info("dedup.released", asset_id=asset_id, location_id=location_id, actor=actor.id)
        await self._record_release(entry, copy, actor=actor)

    async def _record_release(self, entry: Redundancy, copy: Copy, *, actor: Viewer) -> None:
        """Write down which copy was let go of, and what it freed.

        Named in the sentence rather than counted. "A copy was removed" is true of every row in the
        list and tells somebody scanning it nothing; the path is the only thing that says WHICH of
        an asset's copies is the one that is no longer there.
        """
        if self._recorder is None:
            return
        freed = f" That freed {copy.size_bytes} bytes." if copy.size_bytes else ""
        remaining = len(entry.copies) - 1
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            await self._recorder.record_on(
                connection,
                queue=RECLAIM_QUEUE,
                user_id=actor.id,
                title="Removed an extra copy",
                detail=(
                    f"{copy.rel_path} was deleted from your disk.{freed} "
                    f"The file is still kept in {remaining} other "
                    f"{'place' if remaining == 1 else 'places'}, and everything recorded about it "
                    "was kept. Deleting cannot be undone."
                ),
                payload=json.dumps({"asset_id": entry.asset_id, "location_id": copy.location_id}),
                # The asset and not the copy. A location is not a thing with a history: the file
                # is, and it is still here: what happened to it is that one of the places it sat is
                # no longer one of them.
                subjects=[Subject(kind="asset", id=entry.asset_id)],
            )

    # --- carrying an attribution onto the copies ------------------------------------------

    async def carry_offers(self, dials: Dials) -> list[CarryOffer]:
        """Every group of copies where one file knows something the others do not.

        **Read at the tightest setting there is, whatever the queue's own dial says.** The dial
        governs what a person is asked to LOOK at; this writes on files without anybody looking at
        them one by one, so it reads the one rung where the two files' fingerprints do not differ at
        all. Passing the reader's dial through would mean an admin who had widened it to see more
        pairs had also widened what a single press writes on, which is not what they moved it for.

        The keeper rule is handed through untouched. It decides which file a rule would KEEP, which
        is nothing to do with carrying and is part of the key the groups are kept under, so
        passing the one in force reuses the answer the screen already computed rather than
        computing a second set of groups beside it.

        A group too big to be one question is left out, for the reason the queue leaves it out of
        everything else: past the cap a component is a chain of files that resemble their
        neighbours and not each other, and nothing in it may be pre-marked.
        """
        groups = [
            one
            for one in await self.groups(replace(dials, level=Accuracy.EXACT))
            if not one.too_big
        ]
        wanted = sorted({one for group in groups for one in group.ids})
        if not wanted:
            return []
        attribution = await attribution_of_files(self._db, wanted)
        offers: list[CarryOffer] = []
        for group in groups:
            offer = self._offer_from(group, attribution)
            if offer is not None:
                offers.append(offer)
        if not offers:
            return []
        # The names LAST and only for the files an offer actually names, which is a handful rather
        # than every file that is in a group at all.
        places = await self.places_of([one.source for one in offers])
        return [replace(one, source_name=_file_name(places.get(one.source))) for one in offers]

    def _offer_from(
        self, group: Group, attribution: Mapping[str, list[Carried]]
    ) -> CarryOffer | None:
        """One group's offer, out of what has already been read. No database, so it can be tested.

        The targets are every file with NOTHING on it, rather than every file missing part of what
        the source carries. A file that names one of the two people in the group's answer has an
        attribution of its own, and adding the second to it is a different claim: that this copy
        shows somebody nobody has said it shows, which is not what "the copies know less than
        that one" means.
        """
        agreed = _agreed(attribution, group.ids)
        if agreed is None:
            return None
        source, carried = agreed
        targets = tuple(one for one in group.ids if not attribution.get(one))
        return CarryOffer(
            ids=group.ids,
            source=source,
            #: Filled in by the caller, which reads the names for the handful of files an offer
            #: actually names rather than for every file that is in a group at all.
            source_name="",
            targets=targets,
            carried=carried,
        )

    async def carry(self, offer: CarryOffer, *, actor: Viewer) -> int:
        """Write one offer onto its files. How many files gained something.

        **One receipt per FILE, which is the opposite of how a group settlement is recorded**, and
        the difference is what the undo means. Settling a group is one act on one question (these
        are the same thing, keep that one), so taking it back means taking all of it back. A carry
        is a separate claim about each copy: this file is by her too. Somebody looking at one of
        them a week later and disagreeing is entitled to take that one back and leave the rest,
        which a single receipt over four files cannot do.

        The writes and the receipts are ONE transaction, so there is no state where a file carries a
        row nothing can take back, or a receipt offers to undo a row that was never written.
        """
        if not offer.targets or not offer.carried:
            return 0
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            landed = await carry_attribution_on(
                connection, carried=offer.carried, to_asset_ids=offer.targets
            )
            for asset_id, wrote in landed.items():
                await self._record_carry(connection, asset_id, wrote, offer, actor=actor)
        log.info(
            "dedup.carried",
            files=len(landed),
            rows=sum(len(wrote) for wrote in landed.values()),
            source=offer.source,
            actor=actor.id,
        )
        return len(landed)

    async def _record_carry(
        self,
        connection: Connection,
        asset_id: str,
        wrote: Sequence[Carried],
        offer: CarryOffer,
        *,
        actor: Viewer,
    ) -> None:
        """Write down what one file gained, in the sentence its own history already uses.

        The title is the pane's own words with the file it came from added, so the history folds the
        two into one line carrying this receipt's Undo. See `history.carried_sentence`, which is
        where that containment is explained and where it is held.

        A file that gained two rows gets ONE receipt naming the first and a detail that counts the
        rest. Two receipts for one press on one file would offer two undos for a single act, and the
        second of them would look like a decision nobody made.
        """
        if self._recorder is None:
            return
        first = wrote[0]
        named = carried_name(first, offer.source_name or "a copy of it")
        more = len(wrote) - 1
        detail = (
            f"Carried from {offer.source_name or 'a copy of this file'}, which is identical to it "
            "on the fingerprint. Undo puts this file back as it was and leaves the other copies "
            "alone."
        )
        if more:
            detail = f"{detail} {more} more {'was' if more == 1 else 'were'} carried with it."
        await self._recorder.record_on(
            connection,
            queue=CARRY_QUEUE,
            user_id=actor.id,
            title=named,
            detail=detail,
            payload=json.dumps(
                {
                    "asset_id": asset_id,
                    "from": offer.source,
                    "carried": [
                        {"kind": one.kind, "id": one.id, "name": one.name} for one in wrote
                    ],
                }
            ),
            # The file that GAINED the rows, and the one it came from. Both, because this is as
            # much a part of the source file's history as of this one's: it is the file somebody
            # is looking at when they wonder why a copy of it is suddenly filed the same way.
            subjects=[Subject(kind="asset", id=asset_id), Subject(kind="asset", id=offer.source)],
            # The ledger's words for a carry: a person or a username was LINKED to this file, and
            # the object is the first of them: the same one the title names. It is how the file's
            # pane folds this receipt and the attribution's own line into one row, by these words
            # rather than by matching the title's text. A carry of two rows folds the first; the
            # second stands, which is the honest shape and is what the detail beside it already
            # counts.
            verb="linked",
            object=LedgerObject(
                kind="person" if first.kind == "person" else "username",
                id=first.id,
                name=first.name,
            ),
        )

    async def uncarry(self, *, asset_id: str, carried: Sequence[Carried]) -> bool:
        """Take one carry back off one file. True when a row actually went.

        False where the rows are already gone (somebody detached the person by hand, the file was
        deleted, a later version of Sift wrote the record differently), which is the honest answer
        rather than an error: nothing is left to put back, and saying so reads better on a record
        than a refusal does.
        """
        if not carried:
            return False
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            removed = await uncarry_attribution_on(connection, asset_id=asset_id, carried=carried)
        return removed > 0


#: The keys the three dials are stored under, declared where the settings themselves are.
#:
#: Named as strings rather than imported from the package that registers them, because that package
#: imports this one, and the reader that turns a stored value into a threshold belongs beside the
#: query it feeds, not beside the declaration.
LEVEL_KEY = "dedup.level"
MAX_DURATION_GAP_KEY = "dedup.max_duration_gap_seconds"
KEEP_KEY = "dedup.keep"

#: Which file a rule keeps when nothing says otherwise.
#:
#: Resolution, because it is the measure that best survives a re-encode: a re-compressed copy is
#: smaller and newer and no worse to look at, so "larger" and "newer" each prefer the wrong file
#: about as often as the right one. See `grouping._CHAIN` for what happens when it ties.
DEFAULT_RULE: Rule = "higher_res"

#: The five, in the order the screen offers them. One list, read by the settings declaration and by
#: the response that tells a screen what it may choose, so a sixth rule is one line here.
RULES: tuple[Rule, ...] = ("higher_res", "larger", "smaller", "newer", "older")

#: What each is called on screen. Beside the values rather than kept by the screen: a stored value
#: is a word like `higher_res` and the reader is shown "Higher resolution", and a screen holding
#: the mapping as a second list is a list that drifts the first time a rule is added.
RULE_LABELS: tuple[str, ...] = (
    "Higher resolution",
    "Larger file",
    "Smaller file",
    "Newer",
    "Older",
)


def level_from(stored: object) -> Accuracy:
    """The stored closeness, or the default where it is missing or is a word nobody registered.

    A value the registry would refuse cannot reach the database through the settings route, so this
    only ever fires on a row written by an older version. Falling back is right for a read: a queue
    that refuses to draw because one preference is unfamiliar is worse than one drawn at the
    default, and the settings screen shows the default too, so the two agree about what is in force.
    """
    try:
        return Accuracy(str(stored))
    except ValueError:
        return DEFAULT_ACCURACY


def duration_gap_from(stored: object) -> int | None:
    """The stored length rule as milliseconds, where zero means do not compare lengths at all.

    Zero rather than a separate switch, because "the two must run for exactly the same number of
    milliseconds" is not a rule anybody wants (two encodes of one video differ by a frame), so
    the value has no useful meaning of its own and can carry the off position instead.
    """
    try:
        seconds = int(str(stored))
    except (TypeError, ValueError):
        return DEFAULT_MAX_DURATION_GAP_MS
    return seconds * 1000 if seconds > 0 else None


def rule_from(stored: object) -> Rule:
    """The stored keeper rule, or the default where it is missing or is a word nobody registered.

    Falling back rather than refusing, for the same reason `level_from` does: a queue that will not
    draw because one preference is unfamiliar is worse than one drawn at the default, and the
    settings screen shows the default too, so the two agree about what is in force.
    """
    return stored if stored in RULES else DEFAULT_RULE


@dataclass(frozen=True, slots=True)
class Dials:
    """Everything the queue is read at: what counts as alike, and which file a rule would keep.

    One record rather than three arguments threaded through four call sites. It is also what the
    kept answer is keyed on (see `DedupService.groups`), so the three of them travelling together
    is what makes "a different setting is a different answer" something the type says rather than
    something each caller remembers.
    """

    level: Accuracy
    max_duration_gap_ms: int | None
    rule: Rule


async def read_dials(settings: SettingsSeam) -> Dials:
    """All three controls, read fresh, in the ONE place that turns stored words into numbers.

    One place because there are two readers (the queue's own screen and the card that counts it
    on the workbench board) and two copies of this would be two answers to "what is in force",
    shown side by side on the same page. There is no cache, so moving a dial takes effect on the
    next read rather than the next restart.
    """
    return Dials(
        level=level_from(await settings.get_app(LEVEL_KEY)),
        max_duration_gap_ms=duration_gap_from(await settings.get_app(MAX_DURATION_GAP_KEY)),
        rule=rule_from(await settings.get_app(KEEP_KEY)),
    )


async def bound_rule(settings: SettingsSeam) -> dict[str, object]:
    """The dials in force as the bound values the queue statements read: the ONE rule of what
    "near" means, handed to a reader outside this slice (a swap deciding what it already holds)
    so no second copy of the numbers exists anywhere."""
    dials = await read_dials(settings)
    return _filter_params(dials.level, dials.max_duration_gap_ms)


def _filter_params(level: Accuracy, max_duration_gap_ms: int | None) -> dict[str, object]:
    """The two dials, as the bound values the queue statements read.

    One place builds them, so the listing and the count cannot end up filtering differently, which
    would show a page of four pairs under a line saying there were seven.
    """
    return {
        "phash": LEVELS["phash"][level],
        "video_phash": LEVELS["video_phash"][level],
        "videohash": LEVELS["videohash"][level],
        "gap": max_duration_gap_ms,
    }


def _candidate_from_row(row: Row) -> Candidate:
    return Candidate(
        id=row["id"],
        asset_a=row["asset_a"],
        asset_b=row["asset_b"],
        method=row["method"],
        distance=row["distance"],
        duration_gap_ms=row["duration_gap_ms"],
        status=row["status"],
        created_at=row["created_at"],
    )


#: Duplicate-finding.
SERVICE: Part[DedupService] = Part("dedup")


# --- carrying an attribution onto the copies ---------------------------------------------------
#
# A file that was filed under a site or named with a person, sitting next to a copy of itself that
# carries neither. The copy is a different asset (different bytes, a re-encode or a re-save), so
# nothing it knows about itself reaches it from the original, and the pass that read the original
# has no reason ever to look at it again.
#
# **Only at the tightest setting there is, and never automatically.** Even among groups whose files
# are bit-for-bit identical on the fingerprint and ALL attributed, a library can hold some that
# name people who do not overlap at all: a fingerprint match at distance nought is not proof of the
# same content, so a carry is an offer somebody presses on a group in front of them, not a write
# hung off every pass that files a file.
#
# The group's files must AGREE before anything is offered. See `carry_offer`.


#: What a carry's receipts are called, and the third name this slice writes on one.
#:
#: Its own name rather than either of the other two, because a receipt has to say which question it
#: answered and this is a third question: not "are these the same thing" and not "which copy do you
#: want", but "these copies know less about themselves than that one does". It is also what makes
#: the undo separable: a reverser reads only its own name's receipts, and a payload that had to be
#: told apart from a group settlement's by its shape would be two decisions sharing one call site.
CARRY_QUEUE = "carry"


@dataclass(frozen=True, slots=True)
class CarryOffer:
    """What carrying would write across one group of copies, worked out before anything is written.

    The count is shown before the press, which is the whole reason this is a record rather than a
    method that just does it: an admin pressing "carry to the copies" is entitled to know how many
    files it lands on first, and a number worked out by the same code that does the writing cannot
    disagree with what happens next.
    """

    #: Every file in the group, as the group named them.
    ids: tuple[str, ...]
    #: The file the attributions are read from, and the one the sentence names.
    source: str
    #: What that file is called, for the sentence. Empty where no copy of it is anywhere Sift can
    #: currently see, which is a file with no name to print rather than a reason to refuse.
    source_name: str
    #: The files that would gain something. Never includes `source`.
    targets: tuple[str, ...]
    #: What would be written onto each of them.
    carried: tuple[Carried, ...]

    @property
    def files(self) -> int:
        """How many files this would write on. The number the offer says out loud."""
        return len(self.targets)


def _agreed(
    attribution: Mapping[str, list[Carried]], ids: Sequence[str]
) -> tuple[str, tuple[Carried, ...]] | None:
    """The one thing every attributed file in this group says, or None where they do not agree.

    **Nothing is offered unless every file that carries an attribution carries the SAME one.** Two
    copies naming different people is a disagreement, and a carry that resolved it would pick one of
    them by nothing better than which id sorted first: writing somebody's name onto a file that
    another decision says is not theirs. Withholding costs little and it is the only rule here that
    cannot be wrong.

    None also when nothing in the group is attributed, and when everything in it is: the first has
    nothing to carry and the second has nowhere to carry it.
    """
    attributed = [one for one in ids if attribution.get(one)]
    if not attributed or len(attributed) == len(ids):
        return None
    answers = {tuple((one.kind, one.id) for one in attribution[one]) for one in attributed}
    if len(answers) != 1:
        return None
    source = attributed[0]
    return source, tuple(attribution[source])


def _file_name(place: Place | None) -> str:
    """What a file is called, out of where it currently sits. Empty where nothing can see it.

    The path's last segment, which is the same name the history's own read of a file names a copy
    by. A file whose every location has gone has no name to print, and the sentence says "a copy of
    it" instead of inventing one.
    """
    return "" if place is None else place.rel_path.rsplit("/", 1)[-1]


def carried_name(carried: Carried, from_name: str) -> str:
    """One carried row as the sentence a receipt is titled with. See `history.carried_sentence`."""
    if carried.kind == "person":
        return carried_sentence(from_name=from_name, person=carried.name)
    return carried_sentence(from_name=from_name, username=carried.name, site=carried.site)


def _named(ids: Sequence[str], names: Mapping[str, str]) -> str:
    """Files as a title says them: "a.mp4", "a.mp4 and b.mp4", "a.mp4, b.mp4, c.mp4 and 2 more"."""
    said = [names.get(one, "a file") for one in ids]
    if len(said) > _NAMED_IN_A_TITLE:
        rest = len(said) - _NAMED_IN_A_TITLE
        said = [*said[:_NAMED_IN_A_TITLE], f"{rest} more"]
    if len(said) < 2:
        return "".join(said)
    return f"{', '.join(said[:-1])} and {said[-1]}"
