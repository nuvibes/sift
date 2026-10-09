# SPDX-License-Identifier: AGPL-3.0-or-later
"""Snapshots of the database, and making and switching libraries."""

from __future__ import annotations

from fastapi import FastAPI

from sift.kernel import wiring
from sift.kernel.config import Settings
from sift.kernel.jobs import JobQueue, WorkerPool
from sift.kernel.wiring import provide
from sift.slices import backup, settings_hub
from sift.wiring.built import Storage


async def build_backup(
    app: FastAPI,
    settings: Settings,
    store: Storage,
    hub: settings_hub.SettingsService,
    pool: WorkerPool,
    queue: JobQueue,
) -> None:
    """Snapshots of the database, given the pool so a restore can stop the workers for the swap."""
    service = backup.BackupService(
        store.database, settings, hub.get_app, hub.apply, workers=pool, library=store.library
    )
    provide(app, backup.SERVICE, service)
    # The backup folder is a use of a granted folder, so the grant stays while the folder is chosen.
    store.library.use_grants_for(
        "Backups are saved in that folder. Choose another backup folder first, then Sift can give"
        " it back.",
        service.folders_in_use,
    )
    hub.check_with(backup.FOLDER_KEY, service.folder_refusal)
    backup.register_handlers(service=service, queue=queue)
    # Its next run is placed by the one scheduler in `build_tasks`.


async def build_libraries(app: FastAPI, settings: Settings, store: Storage) -> None:
    """Making libraries and switching between them, after backup, whose unpacking an import uses."""
    service = backup.LibrariesService(
        store.database, settings, wiring.part_of_app(app, backup.SERVICE)
    )
    provide(app, backup.LIBRARIES, service)
    backup.register_library_handlers(service)
    await service.forget_stale_note()
    await service.record_origin()
