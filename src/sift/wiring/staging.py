# SPDX-License-Identifier: AGPL-3.0-or-later
"""Staging and queuing an import."""

from __future__ import annotations

from fastapi import FastAPI

from sift.kernel import wiring
from sift.kernel.config import Settings
from sift.kernel.jobs import JobQueue
from sift.kernel.wiring import provide
from sift.slices import capture, download
from sift.wiring.built import Storage


def build_capture(app: FastAPI, settings: Settings, store: Storage, queue: JobQueue) -> None:
    """Staging and queuing an import.

    Held on the application so a route can stage bytes and enqueue without holding a database handle
    or the content store of its own: the import itself runs in the job, which is the only place
    the content tables are written.
    """

    # Where a file with no target lands, read at the moment somebody drops one.
    #
    # A closure over the application rather than the store itself, because this is built before the
    # download feature is. Read per drop rather than held, for the reason every live read here is:
    # a folder chosen while something is queued should reach the next drop, not the next restart.
    async def download_folder() -> str | None:
        options = wiring.part_of_app_or_none(app, download.SITE_OPTIONS)
        if options is None:
            return None
        return (await options.resolve(None)).dest_folder_id

    provide(
        app,
        capture.SERVICE,
        capture.CaptureService(
            store.library,
            queue,
            capture.CaptureService.staging_root(settings.data_dir),
            default_destination=download_folder,
        ),
    )
