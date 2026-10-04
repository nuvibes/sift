# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taking a file a walk found into the library: the decision, the read, an archive, the probe."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from sift.kernel.archives import ArchiveRefused, Member, extract_member, is_archive
from sift.kernel.archives import inspect as inspect_archive
from sift.kernel.config import Settings
from sift.kernel.content import (
    FileStillChanging,
    Ingested,
    LocationStatus,
)
from sift.kernel.ingress import (
    TRANSIENT_REASONS,
    IngressRejected,
    IngressResult,
    Origin,
    Reason,
    verify_ingress,
)
from sift.kernel.jobs import (
    JobContext,
)
from sift.kernel.log import get_logger
from sift.slices.library_roots.service import LibraryService
from sift.slices.library_roots.sweeping import _folder_for
from sift.slices.library_roots.walking import _STILL_EXTENSIONS, NO_MOVES, Walked

if TYPE_CHECKING:
    from sift.slices.library_roots.jobs import ArchiveSettled
    from sift.slices.library_roots.walking import FolderMoves, _Rows

log = get_logger(__name__)


#: What `probe` is enqueued as: a slice does not import another slice; the fan-out test holds it.
PROBE = "probe"


#: For turning the walk's nanosecond mtime into the whole seconds a location row stores.
NANOSECONDS_PER_SECOND = 1_000_000_000


#: How long a file has to have sat at zero bytes before it is an empty file rather than one about to
#: be written: an hour leaves any writer room to start, and a byte written later brings it back.
EMPTY_SETTLED_SECONDS = 3_600


def _empty_and_settled(rejection: IngressRejected, item: Walked) -> bool:
    """Whether an empty file has been empty longer than `EMPTY_SETTLED_SECONDS`, not a moment."""
    if rejection.reason is not Reason.EMPTY:
        return False
    return time.time_ns() - item.mtime_ns >= EMPTY_SETTLED_SECONDS * 1_000_000_000


class Verdict(StrEnum):
    """What a pass will do with one file the walk found, decided from the rows before any read."""

    ARCHIVE = "archive"
    REFUSED = "refused"
    UNCHANGED = "unchanged"
    #: A copy marked missing whose bytes are back, the size and age they were: marked present.
    RETURNED = "returned"
    READ = "read"

    @property
    def reads(self) -> bool:
        """Whether the file is opened at all. An archive is: its members are read out of it."""
        return self in (Verdict.ARCHIVE, Verdict.READ)


async def _decide(
    item: Walked,
    *,
    rel_path: str,
    root_id: str,
    service: LibraryService,
    context: _Rows,
    refused: Mapping[str, tuple[int, int]],
    moves: FolderMoves = NO_MOVES,
) -> Verdict:
    """Which of the four things a take-in will do, from the rows alone, so a scan can count what it
    is about to read. `moves` is for a plan: the file is looked up where the rows record it now."""
    if is_archive(item.path):
        return Verdict.ARCHIVE
    recorded_at = moves.before(rel_path)
    if refused.get(recorded_at) == (item.size, item.mtime_ns):
        return Verdict.REFUSED
    known = await _recorded_state(context, root_id=root_id, rel_path=recorded_at, item=item)
    return Verdict.READ if known is None else known


async def _take_in(
    context: JobContext,
    item: Walked,
    *,
    rel_path: str,
    root_id: str,
    settings: Settings,
    service: LibraryService,
    taken_in: list[str],
    archive_settled: ArchiveSettled | None = None,
    decided: Verdict,
    to_probe: list[str],
    to_check: list[str],
    folders: dict[str, str],
    refused: dict[str, tuple[int, int]],
) -> set[str]:
    """One file: the gate, the digest, the rows, and `probe`.

    `folders` and `refused` are the pass's memory across its files; `to_probe` and `to_check` where
    it collects the probes to hand out once every read is done. `rel_path` is relative to the ROOT,
    `item.rel_path` to wherever the walk started. Returns the location paths this claimed, which
    the sweep is judged against: one per file, one per picture of an archive.
    """
    verdict = decided
    if verdict is Verdict.ARCHIVE:
        async with _archive_in_hand(root_id, rel_path):
            return await _take_in_archive(
                context,
                item,
                rel_path=rel_path,
                root_id=root_id,
                settings=settings,
                service=service,
                taken_in=taken_in,
                archive_settled=archive_settled,
            )
    if verdict is not Verdict.READ:
        await _left_as_recorded(context, verdict, root_id=root_id, rel_path=rel_path)
        return {rel_path}
    checked = await _through_the_gate(
        item, rel_path=rel_path, root_id=root_id, settings=settings, service=service
    )
    if checked is None:
        return {rel_path}
    try:
        ingested = await context.content.ingest(
            checked,
            root_id=root_id,
            rel_path=rel_path,
            folder_id=await _folder_for(context, root_id, rel_path, folders),
        )
    except FileStillChanging:
        # Somebody is writing to it right now: not a refusal, so the next pass or the watcher
        # comes back to it.
        log.info("library.file_still_changing", root_id=root_id)
        return {rel_path}
    # It passed. Any refusal written down against this path describes bytes that are not there any
    # more, and a stale refusal would keep a file out on a later pass that happened to match it.
    # Only where one exists: a delete of a row that is not there is a write for nothing.
    if refused is None or refused.pop(rel_path, None) is not None:
        await service.forget_rejection(root_id=root_id, rel_path=rel_path)
    _collect_probe(ingested, taken_in=taken_in, to_probe=to_probe, to_check=to_check)
    return {rel_path}


#: The archives a take-in holds right now, each with the take-ins holding or waiting on it.
_IN_HAND: dict[tuple[str, str], tuple[asyncio.Lock, int]] = {}


@asynccontextmanager
async def _archive_in_hand(root_id: str, rel_path: str) -> AsyncIterator[None]:
    """One take-in of an archive at a time, however many scans meet it.

    A file needs no such claim: its identity is one write, and a second scan reading it at the
    same moment finds the row the first made. An archive is many writes and then a grouping, so
    two scans side by side (the watcher's and a pressed Scan) would each pull every picture out
    and each make the set. The second waits instead, then finds every picture unchanged and opens
    nothing. Keyed on the library and the path, as the set it makes is.
    """
    key = (root_id, rel_path)
    lock, holders = _IN_HAND.get(key, (asyncio.Lock(), 0))
    _IN_HAND[key] = (lock, holders + 1)
    try:
        async with lock:
            yield
    finally:
        lock, holders = _IN_HAND[key]
        if holders == 1:
            del _IN_HAND[key]
        else:
            _IN_HAND[key] = (lock, holders - 1)


async def _left_as_recorded(
    context: JobContext, verdict: Verdict, *, root_id: str, rel_path: str
) -> None:
    """A file the rows already answer for: refused before, unchanged, or back where it was."""
    if verdict is Verdict.REFUSED:
        # Refused before, and unchanged since: not said again. Still CLAIMED, so the sweep does not
        # decide the file has gone because this pass declined to look at it again.
        return
    if verdict is Verdict.RETURNED:
        # The bytes are back where a row already describes them, the size and age it recorded.
        # Marked present and not read: a drive plugged back in is not thousands of new files.
        location = await context.content.location_at(root_id, rel_path)
        # Read as missing a moment ago; only a writer racing this pass could make this false.
        if (
            location is not None
            and await context.content.mark_present(  # pragma: no branch (a race)
                location.id
            )
        ):
            log.info("library.file_returned", root_id=root_id)
    await _probe_if_it_never_was(context, root_id=root_id, rel_path=rel_path)


async def _through_the_gate(
    item: Walked, *, rel_path: str, root_id: str, settings: Settings, service: LibraryService
) -> IngressResult | None:
    """The file as the ingress gate passed it, or None where it was turned away."""
    try:
        return await asyncio.to_thread(
            verify_ingress, item.path, origin=Origin.SCAN, settings=settings
        )
    except IngressRejected as rejection:
        # A refusal is remembered against the BYTES at a path. A file that could not be opened
        # just now, or was empty a moment after it was made, is a fact about a moment, and is
        # looked at again next time; one that has stayed empty is not (`_empty_and_settled`).
        if rejection.reason in TRANSIENT_REASONS and not _empty_and_settled(rejection, item):
            log.info("library.file_not_readable_now", root_id=root_id, reason=str(rejection.reason))
            return None
        await service.remember_rejection(
            root_id=root_id,
            rel_path=rel_path,
            size_bytes=item.size,
            mtime_ns=item.mtime_ns,
            rejection=rejection,
        )
        return None


def _collect_probe(
    ingested: Ingested, *, taken_in: list[str], to_probe: list[str], to_check: list[str]
) -> None:
    """`probe` and nothing else: it starts the thumbnail, the preview and the sprite itself.

    The second half is the recovery for an asset whose `probe` was lost (a file renamed while its
    own probe was in flight): asked of the queue (`_probe_unless_already_coming`), so it is safe on
    every pass.
    """
    if ingested.asset_is_new:
        to_probe.append(ingested.asset.id)
        # Collected rather than indexed here: telling the search index per file is a write and an
        # orphan sweep per file. The caller says it once for the whole walk.
        taken_in.append(ingested.asset.id)
    elif ingested.asset.probed_at is None:
        to_check.append(ingested.asset.id)


async def _take_in_archive(
    context: JobContext,
    item: Walked,
    *,
    rel_path: str,
    root_id: str,
    settings: Settings,
    service: LibraryService,
    taken_in: list[str],
    archive_settled: ArchiveSettled | None,
) -> set[str]:
    """A ZIP of pictures: every picture inside it, indexed where it lies.

    **The archive never becomes an asset**: what appears on the Browse wall is the pictures, and a
    photo set holding them. **Nothing is unpacked**: each picture is written to a scratch file, put
    through the ordinary gate, hashed, recorded, and DELETED again; the full-size picture is pulled
    out into the cache the first time somebody looks at it (`ContentStore._materialised`).

    The scratch file is removed in a `finally`: otherwise a refused picture or a cancelled scan
    leaves partial bytes in the cache, which is served. Every picture carries the ARCHIVE's mtime, so
    an unchanged archive is answered from its index without anything being decompressed.
    """
    members = await _archive_members(item, rel_path=rel_path, root_id=root_id, service=service)
    if not members:
        return set()

    # The folder the ARCHIVE sits in, resolved once: a member's parent would make a folder row
    # named after the `.zip`, a directory that is not on disk.
    folder_id = await _folder_for(context, root_id, rel_path)
    # A scratch directory of this take-in's own, unique by construction, removed once the members
    # are through (each member's own file goes as it is taken in).
    incoming = settings.cache_dir / "incoming"
    await asyncio.to_thread(incoming.mkdir, parents=True, exist_ok=True)
    scratch = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="archive-", dir=incoming))

    claimed: set[str] = set()
    inside: list[str] = []
    # Removed from Sift by somebody, and the archive is not Sift's to change, so this is the only
    # place that removal can hold: without it the next scan brings the picture back under a new id.
    removed = await service.removed_inside(root_id=root_id, archive_rel_path=rel_path)
    for member in members:
        await context.raise_if_canceled()
        member_rel = _member_rel_path(rel_path, member.path)
        claimed.add(member_rel)
        if removed.get(member_rel) == member.size_bytes:
            continue
        asset_id = await _take_in_member(
            context,
            archive=item.path,
            member=member.path,
            member_rel=member_rel,
            archive_rel_path=rel_path,
            root_id=root_id,
            folder_id=folder_id,
            size_bytes=member.size_bytes,
            mtime_ns=item.mtime_ns,
            settings=settings,
            scratch=scratch / Path(member.path).name,
            taken_in=taken_in,
        )
        if asset_id is not None:
            inside.append(asset_id)

    await asyncio.to_thread(shutil.rmtree, scratch, True)
    log.info(
        "library.archive_indexed", root_id=root_id, pictures=len(inside), considered=len(members)
    )
    if archive_settled is not None and inside:
        # After every picture, never per picture: a set is the archive. A listener that refuses must
        # not take the scan down with it (see `_settle_folders`).
        try:
            await archive_settled(root_id, rel_path, Path(rel_path).stem, inside)
        except Exception as refused:  # a grouping is a convenience; a scan is not
            log.warning(
                "library.archive_settle_failed",
                root_id=root_id,
                error_kind=type(refused).__name__,
                error=str(refused),
            )
    return claimed


async def _archive_members(
    item: Walked, *, rel_path: str, root_id: str, service: LibraryService
) -> list[Member]:
    """The pictures an archive lists, or none where it was refused before or is refused now."""
    if await service.was_already_refused(
        root_id=root_id, rel_path=rel_path, size_bytes=item.size, mtime_ns=item.mtime_ns
    ):
        return []

    try:
        members = await asyncio.to_thread(inspect_archive, item.path, wanted=_STILL_EXTENSIONS)
    except ArchiveRefused as refused:
        # Remembered the same way a rejected file is, against the ARCHIVE's own path, so a bomb or a
        # password-protected file is opened once rather than on every pass for ever.
        await service.remember_rejection(
            root_id=root_id,
            rel_path=rel_path,
            size_bytes=item.size,
            mtime_ns=item.mtime_ns,
            rejection=IngressRejected(Reason.UNREADABLE, detected=str(refused)),
        )
        log.info("library.archive_refused", root_id=root_id, reason=str(refused))
        return []

    await service.forget_rejection(root_id=root_id, rel_path=rel_path)
    return members


def _member_rel_path(archive_rel_path: str, member: str) -> str:
    """Where a picture inside an archive is recorded: the archive's path, then its name inside."""
    return f"{archive_rel_path}/{member}"


async def _take_in_member(
    context: JobContext,
    *,
    archive: Path,
    member: str,
    member_rel: str,
    archive_rel_path: str,
    root_id: str,
    folder_id: str | None,
    size_bytes: int,
    mtime_ns: int,
    settings: Settings,
    scratch: Path,
    taken_in: list[str],
) -> str | None:
    """One picture out of an archive. Its asset id, unchanged ones too, or None if refused.

    The gate is the ordinary one, which is why the member is written to a real file first.
    """
    unchanged = await _unchanged_since_last_scan(
        context,
        root_id=root_id,
        rel_path=member_rel,
        item=Walked(rel_path=member_rel, path=archive, size=size_bytes, mtime_ns=mtime_ns),
    )
    if unchanged:
        await _probe_if_it_never_was(context, root_id=root_id, rel_path=member_rel)
        # Still offered to the grouping, so a set switched on since the last pass is made now.
        location = await context.content.location_at(root_id, member_rel)
        return None if location is None else location.asset_id

    try:
        try:
            await asyncio.to_thread(
                extract_member, archive, member, scratch, cache_dir=settings.cache_dir
            )
        except ArchiveRefused as refused:
            log.info("library.archive_member_refused", root_id=root_id, reason=str(refused))
            return None

        try:
            checked = await asyncio.to_thread(
                verify_ingress, scratch, origin=Origin.SCAN, settings=settings
            )
        except IngressRejected:
            # Not remembered: a member borrows the archive's size and mtime, so one bad picture
            # would file a refusal every other picture in that archive matches.
            log.info("library.archive_member_rejected", root_id=root_id)
            return None

        try:
            ingested = await context.content.ingest(
                checked,
                root_id=root_id,
                rel_path=member_rel,
                folder_id=folder_id,
                archive_rel_path=archive_rel_path,
                member_path=member,
                # The ARCHIVE's: a scratch copy's own timestamp would answer "changed" against the
                # archive on every later pass.
                mtime=mtime_ns // NANOSECONDS_PER_SECOND,
            )
        except FileStillChanging:  # pragma: no cover (the scratch file is written and closed here)
            return None
    finally:
        # The whole point of indexing in place: the bytes do not stay. Removed whichever way this
        # went, including the cancelled path, or the cache fills with a second copy of the library.
        await asyncio.to_thread(scratch.unlink, True)

    if ingested.asset_is_new:
        await context.enqueue_child(PROBE, _probe_payload(context, ingested.asset.id))
        taken_in.append(ingested.asset.id)
    elif ingested.asset.probed_at is None:
        await _probe_unless_already_coming(context, ingested.asset.id)
    return ingested.asset.id


async def _unchanged_since_last_scan(
    context: JobContext, *, root_id: str, rel_path: str, item: Walked
) -> bool:
    """Whether this exact file is already indexed, unchanged, and needs nothing doing to it.

    The saving is the whole cost of a rescan; the caller has already recorded this path as seen, so
    the sweep will not mark it missing.

    **Size and mtime, not a digest**, the same test `was_already_refused` makes: a digest would mean
    reading the file. A file rewritten to the same length inside the same second reads as
    unchanged, and that is not a security boundary: the derivative jobs put the file through the
    gate again themselves before they touch it.
    """
    return (
        await _recorded_state(context, root_id=root_id, rel_path=rel_path, item=item)
        is Verdict.UNCHANGED
    )


async def _recorded_state(
    context: _Rows, *, root_id: str, rel_path: str, item: Walked
) -> Verdict | None:
    """What the rows already say about the file at this path, or None for one they do not know.

    UNCHANGED for a present copy of the same size and age, which the pass leaves alone. RETURNED
    for a copy marked missing whose bytes are back with the same size and age (a drive plugged
    in again, a share that came back), which the pass marks present and leaves alone in the same
    way. A file that came back CHANGED is None like any new file: it is read, and the digest says
    whether it is the content the row named.
    """
    location = await context.content.location_at(root_id, rel_path)
    if location is None:
        return None
    if location.size_bytes != item.size or location.mtime is None:
        return None
    if location.mtime != item.mtime_ns // NANOSECONDS_PER_SECOND:
        return None
    if location.status is LocationStatus.PRESENT:
        return Verdict.UNCHANGED
    return Verdict.RETURNED


async def _probe_if_it_never_was(context: JobContext, *, root_id: str, rel_path: str) -> None:
    """The recovery for a file the shortcut above would otherwise leave stranded forever.

    Skipping an unchanged file decides on the LOCATION, not on whether the asset was ever read: an
    asset whose probe was lost would be skipped by every later pass. One indexed row by id.
    """
    location = await context.content.location_at(root_id, rel_path)
    if location is None:
        return
    # Seen present and unchanged: whatever a moment kept a feature from doing is not true now.
    await context.content.forget_transient_verdicts(location.asset_id)
    asset = await context.content.get(location.asset_id)
    if asset is None or asset.probed_at is not None:
        return
    await _probe_unless_already_coming(context, asset.id)


def _probe_payload(context: JobContext, asset_id: str) -> dict[str, object]:
    """What to ask `probe` for, carrying whether this scan wants anything BUILT from the file.

    Everything after the reading (the pictures, the faces, the descriptions) is handed out by
    `probe`, which is right for a file ARRIVING and wrong for somebody who pressed Scan: they asked
    for one stage. `scan_only`, set by that button and nothing else, says so. One function, because
    the flag has to reach every probe a scan hands out.
    """
    if context.payload.get("scan_only"):
        return {"asset_id": asset_id, "scan_only": True}
    return {"asset_id": asset_id}


async def _probe_unless_already_coming(context: JobContext, asset_id: str) -> None:
    """Queue a probe for an asset that has none recorded, unless one is already on its way.

    The check is against the queue, because "is it being read right now" is answered only by the
    work: a running probe is about to write down exactly what is missing. `dedupe` as well: the
    check covers the running case the collapse will not, and the collapse covers two scans racing
    between the check and the enqueue.
    """
    # BOTH SHAPES, because `is_live` matches the payload exactly and a scan-only probe carries a
    # field an ordinary one does not: one question (is anything already reading this file?), so a
    # third shape would have to be added here.
    for shape in ({"asset_id": asset_id}, {"asset_id": asset_id, "scan_only": True}):
        if await context.queue.is_live(PROBE, shape):
            return
    await context.enqueue_child(PROBE, _probe_payload(context, asset_id), dedupe=True)
