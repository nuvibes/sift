# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proposing duplicates, and acting on what a person decides about them.

Sift proposes, a person disposes: removal goes out through a `Protocol` the delete feature
satisfies, so this slice holds no filesystem call of its own.
"""

from __future__ import annotations

import asyncio
import hashlib
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
    Fingerprint,
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

#: What this queue is called in the workbench registry and on every receipt the service writes.
WORKBENCH_QUEUE = "duplicates"

#: The exact-copy queue's name, so a receipt says which of the two questions it answered.
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
    """The removal seam refused for a reason that is neither absence nor permission, such as a read-
    only folder.
    """


class Remover(Protocol):
    """The removal seam, as this slice needs it, refusing with this module's exceptions.

    `location_id` takes one copy and keeps the asset; without it every copy goes.
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
    #: How far apart the two run, in milliseconds, or None when nobody can say.
    duration_gap_ms: int | None = None


@dataclass(frozen=True, slots=True)
class Settled:
    """What one group's confirm did, per group, so a page press can say what went and what would not."""

    ids: tuple[str, ...]
    kept: str
    removed: tuple[str, ...]
    #: The files the disk would not let go of; the group stays in the queue with them.
    refused: tuple[str, ...]


#: Every pair already recorded, whatever was answered about it: a sweep never files one again.
_RECORDED_PAIRS = "SELECT asset_a, asset_b, method FROM dedup_candidates"

_INSERT_CANDIDATE = """
INSERT INTO dedup_candidates
       (id, asset_a, asset_b, method, distance, duration_gap_ms, status, created_at)
VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
ON CONFLICT(asset_a, asset_b, method) DO NOTHING
"""

# A pair filed as already answered, with no distance; `DO NOTHING` keeps an earlier confirmation.
_INSERT_SETTLED = """
INSERT INTO dedup_candidates
       (id, asset_a, asset_b, method, distance, duration_gap_ms, status, created_at)
VALUES (?, ?, ?, ?, NULL, NULL, 'dismissed', ?)
ON CONFLICT(asset_a, asset_b, method) DO NOTHING
"""

#: Which waiting pairs the dials show: the one rule spliced into the page, the whole read and the
#: count, with the dials bound from `_filter_params`. The threshold is per method, since each
#: measures on its own scale. An unknown duration gap shows the pair rather than hiding it.
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

#: Every pending pair these dials show, in one read, for the grouping: a group is a property of the
#: whole pair graph. Through the sweep lane, and ordered so two runs read alike.
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

#: How many pairs the settings show, and how many are waiting in all, from one statement.
_PENDING_COUNTS = splice(
    """
SELECT COUNT(*) AS held,
       COUNT(CASE WHEN {{DIALS_SHOW}} THEN 1 END) AS shown
  FROM dedup_candidates
 WHERE status = 'pending'
""",
    DIALS_SHOW=_DIALS_SHOW,
)

#: How many pairs there are in all, whatever their state.
_PAIRS_HELD = "SELECT COUNT(*) AS n FROM dedup_candidates"

_CANDIDATE = """
SELECT id, asset_a, asset_b, method, distance, duration_gap_ms, status, created_at
  FROM dedup_candidates
 WHERE id = ?
"""

_SETTLE = "UPDATE dedup_candidates SET status = ? WHERE id = ?"

#: Every pair of one group, answered together; only pending rows, so a second press does nothing.
_DISMISS_GROUP = """
UPDATE dedup_candidates
   SET status = 'dismissed'
 WHERE status = 'pending'
   AND id IN (?*)
"""

#: How many of these pairs are back in the queue.
_PENDING_AMONG = """
SELECT COUNT(*) AS back
  FROM dedup_candidates
 WHERE status = 'pending'
   AND id IN (?*)
"""

#: And the undo of that, only from `dismissed`. Names are read before anything is deleted, through
#: the kernel's `names_now`.

#: How many files a receipt's title names before it counts the rest.
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
        #: Where a decision is written down for the workbench; optional, and absent in a test.
        self._recorder = recorder
        #: The groups, kept until a pair moves, keyed on the dials as well. See `groups`.
        self._groups: MarkedMemo[list[Group]] = MarkedMemo()
        #: How many writes to the pairs this service has made.
        self._moved = 0

    def _now(self) -> int:
        return int(self._clock())

    async def scan(self) -> int:
        """Compare every fingerprint in the library and file the pairs worth asking about.

        Returns how many were newly filed. A pair already recorded keeps its status, since the scan
        only inserts. The comparison runs on a thread; the writes stay on the loop.
        """
        planned = await self.plan()
        filed = 0
        for pair in planned.pairs:
            filed += await self._file(pair)
        log.info("dedup.filed", assets=planned.compared, pairs=len(planned.pairs), filed=filed)
        return filed

    async def scan_unless_unchanged(self, compared: str | None) -> tuple[int, str]:
        """`scan`, unless every fingerprint is as it was when `compared` was taken: then there is
        nothing new to pair. How many pairs were filed, and the digest of what was read."""
        fingerprints = await self._reads.fingerprints()
        digest = await asyncio.to_thread(_digest_of, fingerprints)
        if digest == compared:
            log.info("dedup.unchanged", digest=digest[:12], assets=len(fingerprints))
            return 0, digest
        return await self.scan(), digest

    async def plan(self) -> DuplicatePlan:
        """Compare every fingerprint and answer the pairs not recorded yet. Writes nothing."""
        fingerprints = await self._reads.fingerprints()
        matcher = Matcher()
        # On a thread, so the comparison does not hold the loop for tens of seconds. Not a chunked
        # fold: the block search is complete only over the whole set.
        pairs = await asyncio.to_thread(matcher.find, fingerprints)
        # One read of what is recorded; `_file` still asks again for a pair filed meanwhile.
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

    async def groups(self, dials: Dials) -> list[Group]:
        """Every group these dials make of the queue, most alike first.

        Computed rather than stored, since a group depends on the dials, and kept until a pair
        moves: only `_say` and `dismiss` write pairs, and a file leaving takes its pairs by cascade.
        Who may see what is decided at the route.
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
        # Only the files that are in a pair.
        wanted = sorted({side for pair in pairs for side in (pair.asset_a, pair.asset_b)})
        facts = await self._reads.measures_of(wanted) if wanted else {}
        # Off the loop: the fold costs hundreds of milliseconds and somebody is waiting.
        # `group_pairs` is pure.
        return await asyncio.to_thread(
            group_pairs, pairs, facts=facts, rule=dials.rule, cap=DEFAULT_MAX_GROUP
        )

    async def pending_counts(
        self,
        *,
        level: Accuracy = DEFAULT_ACCURACY,
        max_duration_gap_ms: int | None = DEFAULT_MAX_DURATION_GAP_MS,
    ) -> tuple[int, int]:
        """How many pairs these settings show, and how many are waiting in all."""
        # Unpacked rather than guarded: a bare aggregate always answers one row.
        (row,) = await self._db.fetch_all(
            _PENDING_COUNTS, _filter_params(level, max_duration_gap_ms)
        )
        return int(row["shown"] or 0), int(row["held"] or 0)

    async def places_of(self, asset_ids: Sequence[str]) -> dict[str, Place]:
        """Where each of these files currently sits, through the slice's declared reads
        (`DuplicateReads`).
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
        """Write, and tell every admin's screens that list what changed, after the write."""
        await self._db.execute(sql, params)
        self._moved += 1
        announce_now(EVERY_ADMIN, About.LIBRARY)

    async def mark_unrelated(self, first_asset_id: str, second_asset_id: str) -> None:
        """Settle a pair as different before anybody is asked about it, as for a compressed copy.

        Written for all three methods and ordered by id, so a later scan's insert collides and does
        nothing. Nothing happens for one asset.
        """
        if first_asset_id == second_asset_id:
            return
        asset_a, asset_b = sorted((first_asset_id, second_asset_id))
        for method in ("phash", "videohash", "video_phash"):
            await self._say(_INSERT_SETTLED, (new_id(), asset_a, asset_b, method, self._now()))
        log.info("dedup.marked_unrelated", asset_a=asset_a, asset_b=asset_b)

    async def settle_group(self, group: Group, *, keep: str, actor: Viewer) -> Settled:
        """Keep one file of a group and delete the rest from the disk.

        It takes a `Group` from `groups`, so a request cannot name files to delete together. A
        deleted file takes its pairs with it; one the disk keeps stays a question. The route checks
        that this user may see every file.
        """
        if keep not in group.ids:
            raise NotFound("That file isn't part of this group.")
        if group.too_big:
            # A group past the cap is a chain whose ends look nothing alike: refused, not judged.
            raise NotAllowed("That group is too long a chain to settle in one press.")

        names = await self._names_of(group.ids)
        removed: list[str] = []
        refused: list[str] = []
        for one in group.ids:
            if one == keep:
                continue
            try:
                await self._remover.remove(one, mode="disk", actor=actor)
            except DedupError:
                # One file the disk keeps does not stop the rest or lose their receipt.
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
        """Say the files in a group are not the same thing; every pair is kept `dismissed` so it lasts.

        Returns how many pairs were answered.
        """
        if not group.pairs:
            return 0
        sql, params = in_clause(_DISMISS_GROUP, list(group.pairs))
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
        # After the commit, so a reader cannot pair the old groups with the new count.
        self._moved += 1
        log.info(
            "dedup.group.dismissed", files=len(group.ids), pairs=len(group.pairs), actor=actor.id
        )
        return len(group.pairs)

    async def restore_group(self, candidate_ids: Collection[str]) -> bool:
        """Put a dismissed group's pairs back in the queue, only for rows still there. What undo
        reaches.
        """
        if not candidate_ids:
            return False
        wanted = sorted(candidate_ids)
        await self._say(*in_clause(_UNDISMISS_GROUP, wanted))
        counted, params = in_clause(_PENDING_AMONG, wanted)
        row = await self._db.fetch_one(counted, params)
        return row is not None and int(row["back"] or 0) > 0

    async def _names_of(self, ids: Sequence[str]) -> dict[str, str]:
        """Each file's name, by id; a file with no name known is absent and said as "a file"."""
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

        One receipt per group, its title naming the files (read before any delete), written at the
        moment of the decision. `removed` in the payload is what undo reads. Every file is a
        subject.
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
            # By the names read before the delete, which the ledger's door can no longer read.
            subjects=[Subject(kind="asset", id=one, name=called.get(one)) for one in ids],
        )

    async def unsettle(self, candidate_id: str) -> bool:
        """Put a judged pair back in the queue, only for a pair still there. What undo reaches."""
        await self._say(
            "UPDATE dedup_candidates SET status = 'pending'"
            " WHERE id = ? AND status IN ('confirmed', 'dismissed')",
            (candidate_id,),
        )
        row = await self._db.fetch_one(
            "SELECT status FROM dedup_candidates WHERE id = ?", (candidate_id,)
        )
        return row is not None and row["status"] == "pending"

    async def reclaim(self, *, limit: int, offset: int) -> list[Redundancy]:
        """A page of the assets sitting in more than one place, with every copy of each."""
        return await self._reads.redundancies_page(limit=limit, offset=offset)

    async def reclaim_position(self, asset_id: str) -> int | None:
        """Where one file stored more than once sits on the list `reclaim` pages, or None."""
        return await self._reads.redundancy_position(asset_id)

    async def reclaim_totals(self) -> RedundantTotals:
        """How many there are and what the extras add up to, library-wide, from one statement."""
        return await self._reads.redundant_totals()

    async def release(self, asset_id: str, location_id: str, *, actor: Viewer) -> None:
        """Let go of one copy of an asset, keeping the others and keeping the asset.

        An asset with one copy is not redundant, so it is refused. The receipt is written after the
        removal on its own connection: a missing line is better than a line about a deletion that
        failed.
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
        """Write down which copy was let go of, by its path, and what it freed."""
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
                # The asset, not the copy: a location has no history.
                subjects=[Subject(kind="asset", id=entry.asset_id)],
            )

    async def carry_offers(self, dials: Dials) -> list[CarryOffer]:
        """Every group of copies where one file knows something the others do not.

        Read at the tightest setting whatever the queue's dial says, since a press writes without
        anybody looking at each file; the keeper rule passes through to reuse the screen's groups.
        Groups past the cap are left out.
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
        # The names last, only for the files an offer names.
        places = await self.places_of([one.source for one in offers])
        return [replace(one, source_name=_file_name(places.get(one.source))) for one in offers]

    def _offer_from(
        self, group: Group, attribution: Mapping[str, list[Carried]]
    ) -> CarryOffer | None:
        """One group's offer, out of what has already been read; the targets are the files with nothing
        on them.
        """
        agreed = _agreed(attribution, group.ids)
        if agreed is None:
            return None
        source, carried = agreed
        targets = tuple(one for one in group.ids if not attribution.get(one))
        return CarryOffer(
            ids=group.ids,
            source=source,
            #: Filled in by the caller, for the files an offer names.
            source_name="",
            targets=targets,
            carried=carried,
        )

    async def carry(self, offer: CarryOffer, *, actor: Viewer) -> int:
        """Write one offer onto its files, one receipt per file so each can be taken back alone.

        The writes and the receipts are one transaction. How many files gained something.
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
        """Write down what one file gained, in the sentence its history uses
        (`history.carried_sentence`).

        One receipt per file, naming the first row and counting the rest.
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
            # The file that gained the rows, and the one it came from.
            subjects=[Subject(kind="asset", id=asset_id), Subject(kind="asset", id=offer.source)],
            # The ledger's words for a carry, so the pane folds this receipt into one row.
            verb="linked",
            object=LedgerObject(
                kind="person" if first.kind == "person" else "username",
                id=first.id,
                name=first.name,
            ),
        )

    async def uncarry(self, *, asset_id: str, carried: Sequence[Carried]) -> bool:
        """Take one carry back off one file; True when a row actually went, False when it is already
        gone.
        """
        if not carried:
            return False
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            removed = await uncarry_attribution_on(connection, asset_id=asset_id, carried=carried)
        return removed > 0


#: The keys the three dials are stored under, as strings: the registering package imports this one.
LEVEL_KEY = "dedup.level"
MAX_DURATION_GAP_KEY = "dedup.max_duration_gap_seconds"
KEEP_KEY = "dedup.keep"

#: Which file a rule keeps by default: resolution best survives a re-encode.
DEFAULT_RULE: Rule = "higher_res"

#: The five, in the order the screen offers them.
RULES: tuple[Rule, ...] = ("higher_res", "larger", "smaller", "newer", "older")

#: What each is called on screen, beside the values so the two cannot drift.
RULE_LABELS: tuple[str, ...] = (
    "Higher resolution",
    "Larger file",
    "Smaller file",
    "Newer",
    "Older",
)


def _digest_of(fingerprints: Sequence[Fingerprint]) -> str:
    """Every field of every fingerprint a comparison reads, its version included."""
    digest = hashlib.blake2b(digest_size=16)
    for one in fingerprints:
        digest.update(repr(one).encode())
    return digest.hexdigest()


def level_from(stored: object) -> Accuracy:
    """The stored closeness, or the default where it is missing or unregistered."""
    try:
        return Accuracy(str(stored))
    except ValueError:
        return DEFAULT_ACCURACY


def duration_gap_from(stored: object) -> int | None:
    """The stored length rule as milliseconds, where zero means do not compare lengths at all."""
    try:
        seconds = int(str(stored))
    except (TypeError, ValueError):
        return DEFAULT_MAX_DURATION_GAP_MS
    return seconds * 1000 if seconds > 0 else None


def rule_from(stored: object) -> Rule:
    """The stored keeper rule, or the default where it is missing or unregistered."""
    return stored if stored in RULES else DEFAULT_RULE


@dataclass(frozen=True, slots=True)
class Dials:
    """Everything the queue is read at, and the key the kept groups are kept under."""

    level: Accuracy
    max_duration_gap_ms: int | None
    rule: Rule


async def read_dials(settings: SettingsSeam) -> Dials:
    """All three controls, read fresh, in the one place that turns stored words into numbers."""
    return Dials(
        level=level_from(await settings.get_app(LEVEL_KEY)),
        max_duration_gap_ms=duration_gap_from(await settings.get_app(MAX_DURATION_GAP_KEY)),
        rule=rule_from(await settings.get_app(KEEP_KEY)),
    )


async def bound_rule(settings: SettingsSeam) -> dict[str, object]:
    """The dials in force as bound values, for a reader outside this slice, so no second copy exists."""
    dials = await read_dials(settings)
    return _filter_params(dials.level, dials.max_duration_gap_ms)


def _filter_params(level: Accuracy, max_duration_gap_ms: int | None) -> dict[str, object]:
    """The two dials, as the bound values the queue statements read, built in one place."""
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


SERVICE: Part[DedupService] = Part("dedup")


# --- carrying an attribution onto the copies ---------------------------------------------------
#
# Only at the tightest setting, and only on a press: a match at distance nought is not proof. The
# group's files must agree first; see `carry_offer`.


#: What a carry's receipts are called: a third question, with its own reverser.
CARRY_QUEUE = "carry"


@dataclass(frozen=True, slots=True)
class CarryOffer:
    """What carrying would write across one group of copies, worked out before anything is written."""

    #: Every file in the group, as the group named them.
    ids: tuple[str, ...]
    #: The file the attributions are read from, and the one the sentence names.
    source: str
    #: What that file is called, for the sentence; empty where no copy of it can be seen.
    source_name: str
    #: The files that would gain something; never `source`.
    targets: tuple[str, ...]
    #: What would be written onto each of them.
    carried: tuple[Carried, ...]

    @property
    def files(self) -> int:
        """How many files this would write on."""
        return len(self.targets)


def _agreed(
    attribution: Mapping[str, list[Carried]], ids: Sequence[str]
) -> tuple[str, tuple[Carried, ...]] | None:
    """The one thing every attributed file in this group says, or None where they do not agree.

    None too when nothing or everything in the group is attributed.
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
    """What a file is called, by its path's last segment; empty where nothing can see it."""
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
