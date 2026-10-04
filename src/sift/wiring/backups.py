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
    """Snapshots of the database, which is the only thing here that cannot be rebuilt.

    It is handed the pool because a restore replaces the file the workers are writing to, so they
    are stopped for the moment the swap takes and started again after. It takes the pool as an
    argument rather than reading it back, which is what makes "after the workers" a signature: the
    queue refuses a job type nothing can execute, and a schedule with no handler behind it is a job
    that fails every night.
    """
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
    # Both doors refuse the same folder: the general settings route asks the same question the
    # schedule route does.
    hub.check_with(backup.FOLDER_KEY, service.folder_refusal)
    backup.register_handlers(service=service, queue=queue)
    # Its next run, and the two clean-ups' and the update check's, is placed by the one scheduler in
    # `build_tasks`, once every timed task's handler is registered.


async def build_libraries(app: FastAPI, settings: Settings, store: Storage) -> None:
    """Making libraries and switching between them. After backup, whose unpacking an import uses.

    A switch note left by the last run is removed here, once: it names a library to start on, and
    one still here means nothing acted on it. Left, the next ordinary restart would carry it off.
    A library just imported writes where it came from as its first History line, here, before
    the workers start.
    """
    service = backup.LibrariesService(
        store.database, settings, wiring.part_of_app(app, backup.SERVICE)
    )
    provide(app, backup.LIBRARIES, service)
    backup.register_library_handlers(service)
    await service.forget_stale_note()
    await service.record_origin()
