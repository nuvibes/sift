# SPDX-License-Identifier: AGPL-3.0-or-later
"""The import job: the background half of taking an upload or a paste in.

A route stages the bytes and queues one of these; the handler resolves the staged file from the id
in its payload and runs the shared import pipeline against it. The download feature does not queue
this (it calls the same pipeline directly, in its own job) so there is one import path, reached
two ways, and no second copy of it to drift.

The staging directory is cleaned up whatever happens: a file that imported cleanly no longer needs
its scratch copy, and one that was refused was already moved to quarantine by the gate, so all that
is left to remove is the empty directory around it.
"""

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

#: The origins whose bytes Sift staged and this job imports. A scan or a watch indexes a file where
#: it already lies and never comes through here; a download hands its file straight to the pipeline.
_STAGED_ORIGINS = frozenset({Origin.DROP, Origin.PASTE, Origin.UPLOAD})


class StagingGone(JobFailedPermanently):
    """The staged file an import was queued for is not there. Nothing to retry: it will not return."""


class ImportRefused(JobFailedPermanently):
    """The gate refused the staged bytes. They are in quarantine with the reason, and no retry
    changes what they are."""


#: How long a staged file outlives its job before it is swept at start. The same span the queue
#: keeps a settled job: a failed import can be retried from the Activity screen for as long as its
#: row is there, and its bytes are kept exactly as long.
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
            # Nothing landed: the file is already in the library. The job's note is the sentence
            # the drop answers with (the client reads it when the import settles).
            await context.set_note(f"Already here: {outcome.already_at}")
    except IngressRejected as refused:
        # Refused for what the bytes are. Quarantined by the gate with the reason, so the scratch
        # copy has nothing left to say, and no retry would read them differently.
        await _remove(staging_dir)
        raise ImportRefused(str(refused)) from refused
    except JobFailedPermanently:
        await _remove(staging_dir)
        raise
    # A failure of any other kind (the destination's drive away, a copy that broke) leaves the
    # staged file where it is. The queue tries again, and a retry with nothing to import is not a
    # retry: clearing the scratch space on every way out would fail the second attempt on "the
    # staged file is gone" and lose the bytes somebody dropped in.
    await _remove(staging_dir)


async def _named_after(source: Path, asset_id: str, context: JobContext) -> Path:
    """A staged screenshot, renamed after the file it was taken of (`screenshot_name`).

    Named here rather than by the route, because the route reads no content tables: the name of
    the file on screen is a content fact, and reading one belongs to a job. A file that has gone
    since, or has no name, leaves the screenshot under the name it arrived with. Renamed inside
    its own staging folder, so a retry finds it under the new name and renames nothing.

    The name is what the file is called NOW: its first present copy's name in its folder, the
    name the screen shows (`names_on_disk` reads the same rule). The imported name is written once
    and can be a download's long original, so a screenshot named after it would sit nowhere near
    its source in a sorted folder. Only with no present copy does the imported name stand in.
    """
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
    """Remove staged files older than the queue keeps a job for. Blocking; how many went.

    A staged file outlives its import when every attempt failed on something that passed (the
    drive away for an afternoon) and nobody pressed Retry. At start, anything older than a
    settled job is kept is cleared; anything younger may still be asked for.
    """
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
    """The one file in a staging directory. Blocking.

    A staging directory holds exactly the file the route wrote into it. If it is gone the import
    was already run, or the scratch space was cleared: either way there is nothing to take in.
    """
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
