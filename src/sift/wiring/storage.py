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
    """The database and the ways into it.

    Sized from the worker pool, which is why the hardware probe has to come first. It is taken as
    an argument rather than read back, so that ordering is a signature. A read pool smaller than the
    number of workers means every request from the browser waits behind background work for a
    connection; see `readers_for`.
    """
    database = Database.for_data_dir(
        settings.data_dir, readers=readers_for(hardware.worker_concurrency)
    )
    # Handed over before it opens, so a schema that fails to apply still closes the connections
    # the open made: they run on threads that would keep a process whose start failed alive.
    teardown.push_async_callback(database.close)
    await database.connect()
    await database.initialize_schema()
    content = ContentStore(database, settings)
    # Every route that reads an asset reads it through this, and through nothing else. It holds
    # the content store so that resolving an asset to a file happens behind the permission check;
    # a slice is never handed the store directly.
    access = Repository(database, content)
    # The roots and the folder tree. Unlike the content store this is handed to the feature that
    # manages a library, because managing one is not reading assets: adding a root, walking it and
    # moving a folder are admin operations on the shape of the library rather than on what is in
    # it, and what a particular person may see of that shape still comes from the access layer.
    library = LibraryStore(database, settings)
    # One person's hearts, stars and watch history. Kernel rather than a feature, because the
    # grid, the player and the rating control all touch it and none of them should have to depend
    # on either of the others to do so.
    user_state = UserStateStore(database)
    # And the same idea one level up: what somebody thinks of a NAMED thing rather than of a
    # file. Five slices write a pin and they may not import one another, so it lives here.
    entity_state = EntityStateStore(database)

    provide(app, wiring.DATABASE, database)
    provide(app, wiring.CONTENT, content)
    provide(app, wiring.ACCESS, access)
    # Uploaded cover pictures. Kernel rather than a slice, for the reason `kernel.covers` gives:
    # six things across four slices carry a cover, and the slices may not import one another.
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
    """The job queue, with whatever was running when Sift last stopped put back into it.

    Before a worker exists to claim it, or a worker could take a job in the same moment this is
    putting it back.
    """
    queue = JobQueue(store.database)
    await recover(queue)
    return queue
