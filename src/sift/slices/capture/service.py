# SPDX-License-Identifier: AGPL-3.0-or-later
"""Staging uploaded bytes and queuing the import that takes them in.

A route cannot import a file itself: reading and writing the content tables happens with no viewer
and no permission check, which is a job's business, not a request's. So the route does the little
that is safe to do in a request: write the bytes to a scratch directory Sift owns and queue the
work, and the import job does the rest.

The bytes are staged under a fresh id, and the job's payload carries that id, never the path. A
path in a payload is a path in every log line and backup the job appears in, and a job that took a
path could be pointed at any file on the machine by whoever could queue one. An id resolves to a
path in one place, here, and nowhere else.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import UploadFile

from sift.kernel.content import CHUNK_BYTES, LibraryStore
from sift.kernel.destination import Destination, resolve_destination
from sift.kernel.ids import new_id
from sift.kernel.ingress import Origin
from sift.kernel.jobs import JobQueue
from sift.kernel.log import get_logger
from sift.kernel.wiring import Part
from sift.slices.capture.jobs import IMPORT
from sift.slices.capture.pipeline import STAGING_DIR_NAME, safe_name

log = get_logger(__name__)


#: Where a file goes when nobody said. Supplied at boot by the feature that stores that answer.
#:
#: A seam rather than a read, because this slice may not import the one that owns the setting, and
#: because there must be exactly one stored answer: a flag on a library folder here beside a folder
#: id there would be two controls, both labelled where downloads go, able to disagree.
#:
#: Absent in a test, and absent means there is no default, which is what a fresh install is until
#: somebody chooses one, and is answered with a sentence rather than a guess.
DefaultDestination = Callable[[], Awaitable[str | None]]


async def _no_default() -> str | None:
    return None


class CaptureService:
    """Stages bytes and queues their import. One per application."""

    def __init__(
        self,
        library: LibraryStore,
        queue: JobQueue,
        staging_root: Path,
        *,
        default_destination: DefaultDestination = _no_default,
    ) -> None:
        self._library = library
        self._queue = queue
        self._default_destination = default_destination
        #: The directory staged uploads wait in. A subdirectory of the data directory, so a library
        #: root cannot overlap it and nothing here can be mistaken for a file already in a library.
        self._staging_root = staging_root

    async def resolve_destination(self, dest_folder_id: str | None) -> Destination:
        """Where a file would go, checked before anything is staged so a bad target fails fast.

        The default is read HERE, once, and what goes on the job is the folder it resolved to. A job
        carrying the question rather than the answer would be a job whose destination changed if
        somebody edited the setting while it was queued.
        """
        chosen = dest_folder_id or await self._default_destination()
        return await resolve_destination(self._library, chosen)

    async def stage_and_enqueue(
        self,
        upload: UploadFile,
        *,
        origin: Origin,
        dest_folder_id: str | None,
        screenshot_of: str | None = None,
    ) -> str:
        """Write an upload to the scratch directory and queue its import. Returns the job id.

        The destination is resolved first, so a file is not written to disk only to fail because
        there was nowhere to put it. The folder id the job is handed is the resolved one, so the
        default download folder is decided now rather than again when the job runs: a job carries
        an answer, not a question.

        `screenshot_of` is the file a screenshot was taken of, whose name the import gives it.
        """
        destination = await self.resolve_destination(dest_folder_id)

        staging_id = new_id()
        await self._stage(upload, staging_id)

        payload: dict[str, object] = {
            "staging_id": staging_id,
            "origin": origin.value,
            "dest_folder_id": destination.folder_id,
        }
        if screenshot_of is not None:
            payload["screenshot_of"] = screenshot_of
        job_id = await self._queue.enqueue(IMPORT, payload)
        log.info("capture.staged", job_id=job_id, origin=str(origin))
        return job_id

    async def _stage(self, upload: UploadFile, staging_id: str) -> Path:
        """Stream an upload to disk, off the event loop, under its own id.

        Streamed rather than read whole: an upload is a video as often as a photo, and reading one
        into memory to write it back out is a design that works on the machine it was written on.
        The name is kept, sanitised, so the import can record what the file was called.
        """
        staging_dir = self._staging_root / staging_id
        await asyncio.to_thread(staging_dir.mkdir, parents=True, exist_ok=True)
        target = staging_dir / safe_name(upload.filename or "file")

        handle = await asyncio.to_thread(target.open, "wb")
        try:
            while True:
                chunk = await upload.read(CHUNK_BYTES)
                if not chunk:
                    break
                await asyncio.to_thread(handle.write, chunk)
        finally:
            await asyncio.to_thread(handle.close)
        return target

    @staticmethod
    def staging_root(data_dir: Path) -> Path:
        return data_dir / STAGING_DIR_NAME


#: Staging and queuing an import.
SERVICE: Part[CaptureService] = Part("capture")
