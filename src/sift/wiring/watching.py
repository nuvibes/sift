# SPDX-License-Identifier: AGPL-3.0-or-later
"""Watched folders."""

from __future__ import annotations

from fastapi import FastAPI

from sift.kernel.jobs import JobQueue
from sift.kernel.wiring import provide
from sift.slices import library_roots, settings_hub
from sift.wiring.built import Storage


async def start_watching(
    app: FastAPI, store: Storage, queue: JobQueue, hub: settings_hub.SettingsService
) -> library_roots.LibraryWatcher:
    """Watched folders.

    Not a job, and that is the point: a watcher runs for as long as Sift does, and the workers are a
    small fixed number. One that never returns would keep a worker forever, so watching four
    folders on a four-worker box would mean nothing else ever ran. It sits beside the queue and puts
    scans into it instead.
    """

    watcher = library_roots.LibraryWatcher(store.library, queue)
    provide(app, library_roots.WATCHER, watcher)
    await watcher.start()
    return watcher
