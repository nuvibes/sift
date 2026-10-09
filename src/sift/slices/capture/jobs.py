# SPDX-License-Identifier: AGPL-3.0-or-later
"""The import job: the background half of taking an upload or a paste in."""

from __future__ import annotations

import asyncio
import shutil
import time
from functools import partial
from pathlib import Path, PurePosixPath

from sift.kernel.config import Settings
from sift.kernel.content import LocationStatus
from sift.kernel.ids import is_id
from sift.kernel.ingress import IngressRejected, Origin
from sift.kernel.jobs import JobContext, JobFailedPermanently, register_handler
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.tuning import SETTLED_RETENTION_SECONDS
from sift.kernel.log import get_logger
from sift.kernel.seams import ReindexSeam
from sift.slices.capture.pipeline import STAGING_DIR_NAME, import_file, screenshot_name

log = get_logger(__name__)

IMPORT = "import"

#: Origins that Sift staged; scans and downloads never come through here.
_STAGED_ORIGINS = frozenset({Origin.DROP, Origin.PASTE, Origin.UPLOAD})


class StagingGone(JobFailedPermanently):
    """The staged file an import was queued for is not there. Nothing to retry: it will not return."""


class ImportRefused(JobFailedPermanently):
    """The gate refused the staged bytes; they are in quarantine and no retry changes them."""


#: Kept as long as a settled job, so a failed import can be retried from Activity.
STAGING_KEEP_SECONDS = SETTLED_RETENTION_SECONDS


async def run_import(context: JobContext, *, settings: Settings, reindexer: ReindexSeam) -> None:
    """Import a staged file: resolve it from its id, then run the one pipeline over it."""
    staging_id = context.payload.get("staging_id")
    dest_folder_id = context.payload.get("dest_folder_id")
    if not isinstance(staging_id, str) or not is_id(staging_id):
        raise ValueError("an import needs the id of its staged file")
    origin = _staged_origin(context.payload.get("origin"))
    if dest_folder_id is not None and not isinstance(dest_folder_id, str):
        raise ValueError("dest_folder_id must be the id of a folder")
    shot_of = context.payload.get("screenshot_of")
    if shot_of is not None and (not isinstance(shot_of, str) or not is_id(shot_of)):
        raise ValueError("screenshot_of must be the id of a file")

    staging_dir = settings.data_dir / STAGING_DIR_NAME / staging_id
    try:
        source = await asyncio.to_thread(_staged_file, staging_dir)
        if shot_of is not None:
            source = await _named_after(source, shot_of, context)
        outcome = await import_file(
            path=source,
            origin=origin,
            dest_folder_id=dest_folder_id,
            ctx=context,
            settings=settings,
            reindexer=reindexer,
        )
        if outcome.already_at is not None:
            # Nothing landed: the note is the sentence the drop answers with.
            await context.set_note(f"Already here: {outcome.already_at}")
    except IngressRejected as refused:
        await _remove(staging_dir)
        raise ImportRefused(str(refused)) from refused
    except JobFailedPermanently:
        await _remove(staging_dir)
        raise
    # Any other failure keeps the staged file so the queue's retry still has it.
    await _remove(staging_dir)


async def _named_after(source: Path, asset_id: str, context: JobContext) -> Path:
    """A staged screenshot, renamed after the present name of the file it was taken of."""
    asset = await context.content.get(asset_id)
    if asset is None:
        return source
    present = [
        PurePosixPath(one.rel_path).name
        for one in await context.content.locations(asset_id)
        if one.status == LocationStatus.PRESENT
    ]
    shown = present[0] if present else asset.original_filename
    if not shown:
        return source
    target = source.with_name(screenshot_name(shown, source.name))
    if target != source:
        await asyncio.to_thread(source.rename, target)
    return target


async def _remove(staging_dir: Path) -> None:
    await asyncio.to_thread(shutil.rmtree, staging_dir, ignore_errors=True)


def sweep_staging(
    data_dir: Path, *, keep_seconds: float = STAGING_KEEP_SECONDS, now: float | None = None
) -> int:
    """Remove staged files older than the queue keeps a job for. Blocking; how many went."""
    staging_root = data_dir / STAGING_DIR_NAME
    if not staging_root.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - keep_seconds
    removed = 0
    for entry in staging_root.iterdir():
        if not entry.is_dir() or not is_id(entry.name):
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed += 1
    return removed


def _staged_origin(value: object) -> Origin:
    """The origin from the payload, refused unless it is one this job actually stages."""
    if not isinstance(value, str):
        raise ValueError("an import needs to know where its bytes came from")
    try:
        origin = Origin(value)
    except ValueError:
        raise ValueError(f"{value!r} is not an ingress origin") from None
    if origin not in _STAGED_ORIGINS:
        raise ValueError(f"{origin} is not an origin capture stages")
    return origin


def _staged_file(staging_dir: Path) -> Path:
    """The one file in a staging directory. Blocking."""
    if not staging_dir.is_dir():
        raise StagingGone(f"the staged file {staging_dir.name} is gone")
    for entry in sorted(staging_dir.iterdir()):
        if entry.is_file():
            return entry
    raise StagingGone(f"the staged file {staging_dir.name} is gone")


def register_handlers(*, settings: Settings, reindexer: ReindexSeam) -> None:
    """Claim the import job type. Called once, at boot, before anything queues one."""
    register_handler(
        IMPORT,
        partial(run_import, settings=settings, reindexer=reindexer),
        name="Importing file",
        family=Family.SCAN,
    )
