# SPDX-License-Identifier: AGPL-3.0-or-later
"""The passes that correct what the library holds about a file: its kind and its identity."""

from __future__ import annotations

import asyncio

from sift.kernel import lanes
from sift.kernel.config import Settings
from sift.kernel.content import (
    ContentStore,
    VerdictProduct,
    hashing,
)
from sift.kernel.hardware import HardwareReport
from sift.kernel.ingress import (
    TRANSIENT_REASONS,
    IngressRejected,
    Origin,
    detect,
    read_ends,
    verify_ingress,
)
from sift.kernel.jobs import (
    JobContext,
)
from sift.kernel.log import get_logger
from sift.kernel.media import (
    MissingAsset,
    NoReadableCopy,
    resolve,
)
from sift.slices.media_jobs import tuning
from sift.slices.media_jobs.job_types import PROBE, RECLASSIFY, REIDENTIFY

log = get_logger(__name__)


async def reclassify(
    context: JobContext,
    *,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """Read again what a file IS, for every row an older generation of the classifier typed.

    A scan skips a file whose path, size and mtime are unchanged, so a change to what
    `ingress.classify` answers would never reach the files already in the library. The row says
    which generation typed it (`assets.classified_version`); the rows below
    `ingress.CLASSIFIER_VERSION` are read, a page at a time, four kilobytes of header and
    thirty-two of tail each. A file whose kind changed is probed again for what its new kind is
    owed; a file the classifier now refuses is stamped and left as it is, never quarantined.
    """
    del hardware  # every handler in the table takes it; this one has no use for it
    store = context.content
    ids = await store.unclassified(tuning.STAMP_BATCH)
    if not ids:
        return

    log.info("classify.start", count=len(ids))
    read = 0
    changed_kind = 0
    for index, asset_id in enumerate(ids):
        await context.raise_if_canceled()
        await context.set_progress(index / len(ids))
        moved = await _reclassify_one(context, asset_id, settings=settings)
        if moved is None:
            continue
        read += 1
        changed_kind += moved

    await context.set_progress(1.0)
    log.info("classify.done", looked_at=len(ids), read=read, changed_kind=changed_kind)

    # More than one batch, AND this batch got somewhere: a batch that read nothing has learnt only
    # that a drive is away, and asking for the same page again would loop for ever.
    if len(ids) == tuning.STAMP_BATCH and read > 0:
        await context.queue.enqueue_when_settled(RECLASSIFY, delay=tuning.STAMP_SETTLE_SECONDS)


async def _reclassify_one(context: JobContext, asset_id: str, *, settings: Settings) -> bool | None:
    """Type one file from its own header. None where it could not be read (left below the line for
    the next start); otherwise whether its kind changed."""
    store = context.content
    try:
        # The file's own bytes, never a readable copy: an animated WebP's copy for the decoder is
        # an MP4 that would be typed as one.
        source = await resolve(store, asset_id)
    except (MissingAsset, NoReadableCopy):
        return None

    try:
        async with lanes.reading(source.path):
            head, tail, _ = await asyncio.to_thread(read_ends, source.path)
    except OSError as exc:
        log.warning("classify.unreadable", asset_id=asset_id, reason=str(exc))
        return None

    media = detect(head, tail)
    if media is None:
        log.warning("classify.refused_now", asset_id=asset_id)
    await store.reclassify(asset_id, media)
    if media is None or str(media.kind) == source.asset.media_type:
        return False
    # One line per file moved, naming both kinds: it changes which player opens the file.
    log.info(
        "classify.moved",
        asset_id=asset_id,
        was=source.asset.media_type,
        now=str(media.kind),
        container=media.name,
    )
    # A new kind is a file read again, through the switches every arriving file passes.
    # `dedupe`, because a scan can reach the same file at once.
    await context.queue.enqueue(PROBE, {"asset_id": asset_id}, dedupe=True)
    return True


async def reidentify(context: JobContext, *, settings: Settings, hardware: HardwareReport) -> None:
    """Bring files identified by the whole-file digest forward to the sampled identity, a page at
    a time.

    A row an upgraded library holds says `identity_version = 0`; this reads each such file's
    sample and records the new identity beside the old digest. Until it finishes, an arriving file
    with the size of a waiting row is digested both ways. Asked for at boot while any row remains,
    and it asks for itself after each page. A row that cannot be brought forward gets a verdict
    rather than a silent merge or a guess.
    """
    del hardware  # every handler in the table takes it; this one has no use for it
    store = context.content
    ids = await store.legacy_identity_page(tuning.REIDENTIFY_BATCH)
    if not ids:
        return
    log.info("identity.reidentify_start", count=len(ids))
    adopted = 0
    given_up = 0
    for index, asset_id in enumerate(ids):
        await context.raise_if_canceled()
        await context.report_progress(index / len(ids))
        outcome = await _reidentify_one(store, asset_id, settings=settings)
        if outcome is None:
            given_up += 1
        else:
            adopted += outcome
    await context.set_progress(1.0)
    log.info("identity.reidentify_done", read=len(ids), adopted=adopted, given_up=given_up)
    # The next page at once, not after the settling delay, which is for whole-library work asked
    # for per arriving file. A page that only recorded verdicts has moved the pass on too.
    if len(ids) == tuning.REIDENTIFY_BATCH and (adopted > 0 or given_up > 0):
        await context.queue.enqueue(REIDENTIFY, {}, dedupe=True)


async def _reidentify_one(store: ContentStore, asset_id: str, *, settings: Settings) -> int | None:
    """Bring one row forward: how many rows adopted the identity, or None where a verdict was
    written instead.

    One row left behind would keep `legacy_identities_remain` true for ever, and every arriving
    file read whole, so it is written down: transient where the copy may come back (a drive
    unplugged, a file held open), standing where the bytes themselves refused.
    """
    try:
        source = await resolve(store, asset_id)
    except MissingAsset:
        return 0
    except NoReadableCopy:
        # Named: this row keeps an arriving file of its size read twice.
        log.info("identity.reidentify_no_copy", asset_id=asset_id)
        await store.record_verdict(
            asset_id,
            VerdictProduct.IDENTITY,
            code="no_copy",
            reason="No copy of this file could be read.",
            transient=True,
        )
        return None
    try:
        async with lanes.reading(source.original):
            checked = await asyncio.to_thread(
                verify_ingress, source.original, origin=Origin.SCAN, settings=settings
            )
        digest = await hashing.identity_file(checked)
    except IngressRejected as exc:
        log.info("identity.reidentify_skipped", asset_id=asset_id, reason=str(exc))
        await store.record_verdict(
            asset_id,
            VerdictProduct.IDENTITY,
            code=exc.reason.value,
            reason=f"This file could not be read ({exc.reason}).",
            transient=exc.reason in TRANSIENT_REASONS,
        )
        return None
    except (hashing.FileStillChanging, OSError) as exc:
        log.info("identity.reidentify_skipped", asset_id=asset_id, reason=str(exc))
        await store.record_verdict(
            asset_id,
            VerdictProduct.IDENTITY,
            code="unreadable",
            reason="This file could not be read just now.",
            transient=True,
        )
        return None
    return await store.adopt_identity(asset_id, digest)
