# SPDX-License-Identifier: AGPL-3.0-or-later
"""The database, the ways into it, and the job queue built on it."""

from __future__ import annotations

from contextlib import AsyncExitStack

from fastapi import FastAPI

from sift.kernel import wiring
from sift.kernel.access import Repository
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, EntityStateStore, LibraryStore, UserStateStore
from sift.kernel.covers import CoverPictures
from sift.kernel.db import Database, readers_for
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import JobQueue, recover
from sift.kernel.wiring import provide
from sift.wiring.built import Storage


async def build_storage(
    app: FastAPI, settings: Settings, hardware: HardwareReport, teardown: AsyncExitStack
) -> Storage:
    """The database and the ways into it, sized from the worker pool."""
    database = Database.for_data_dir(
        settings.data_dir, readers=readers_for(hardware.worker_concurrency)
    )
    # Handed over before it opens, so a failed schema still closes the connections' threads.
    teardown.push_async_callback(database.close)
    await database.connect()
    await database.initialize_schema()
    content = ContentStore(database, settings)
    # Every route that reads an asset reads it through this, behind the permission check.
    access = Repository(database, content)
    # The roots and the folder tree, handed to the feature that manages a library.
    library = LibraryStore(database, settings)
    # One person's hearts, stars and watch history, shared by several features.
    user_state = UserStateStore(database)
    entity_state = EntityStateStore(database)

    provide(app, wiring.DATABASE, database)
    provide(app, wiring.CONTENT, content)
    provide(app, wiring.ACCESS, access)
    provide(app, wiring.COVER_PICTURES, CoverPictures(database, settings))
    provide(app, wiring.LIBRARY, library)
    provide(app, wiring.USER_STATE, user_state)
    provide(app, wiring.ENTITY_STATE, entity_state)
    return Storage(
        database=database,
        content=content,
        access=access,
        library=library,
        user_state=user_state,
    )


async def open_the_queue(store: Storage) -> JobQueue:
    """The job queue, with what was running when Sift stopped put back before any worker exists."""
    queue = JobQueue(store.database)
    await recover(queue)
    return queue
