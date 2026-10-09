# SPDX-License-Identifier: AGPL-3.0-or-later
"""Staging uploaded bytes and queuing the import that takes them in.
The job's payload carries a staging id, never a path, so a job cannot be pointed at any file."""

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


#: Where a file goes when nobody said; supplied at boot by the slice that stores that answer.
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
        #: Inside the data directory, so a library root cannot overlap it.
        self._staging_root = staging_root

    async def resolve_destination(self, dest_folder_id: str | None) -> Destination:
        """Where a file would go, resolved now so a bad target fails fast and the job carries the
        answer."""
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
        """Write an upload to the scratch directory and queue its import. Returns the job id."""
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
        """Stream an upload to disk under its own id, off the event loop; videos are too large to
        read whole."""
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
