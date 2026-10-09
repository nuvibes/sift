# SPDX-License-Identifier: AGPL-3.0-or-later
"""What is in the library: files, tags, People, sites, collections, and Loops."""

from __future__ import annotations

from fastapi import FastAPI

from sift.kernel import wiring
from sift.kernel.jobs import JobQueue
from sift.kernel.wiring import provide
from sift.slices import (
    browse,
    collections,
    library_roots,
    loops,
    media_jobs,
    people,
    photo_sets,
    sharing,
    songs,
    tags_ratings,
)
from sift.wiring.built import Storage


def build_catalog(app: FastAPI, store: Storage) -> library_roots.LibraryService:
    """The reads and writes over what is in the library, each handed the resolver."""
    # Removing a root drops the grants naming its folders first: nothing cascades them.
    library_service = library_roots.LibraryService(store.database, store.library, store.access)
    provide(app, library_roots.SERVICE, library_service)

    provide(
        app,
        browse.SERVICE,
        browse.BrowseService(store.database, store.access, store.user_state, store.content),
    )
    provide(app, tags_ratings.SERVICE, tags_ratings.TagService(store.database, store.access))
    provide(app, people.SERVICE, people.PeopleService(store.database, store.access))
    provide(app, collections.SERVICE, collections.CollectionService(store.database, store.access))
    provide(app, photo_sets.SERVICE, photo_sets.PhotoSetService(store.database, store.access))
    provide(app, songs.SERVICE, songs.SongService(store.database))
    provide(app, sharing.SERVICE, sharing.SharingService(store.database, store.access))
    return library_service


class _Stills:
    """What renders a still of one moment, deduped, for everything that wants one."""

    def __init__(self, queue: JobQueue) -> None:
        self._queue = queue

    async def wants_still(self, asset_id: str, at_ms: int) -> None:
        await self._queue.enqueue(
            media_jobs.LOOP_THUMBNAIL,
            {"asset_id": asset_id, "at_ms": at_ms},
            dedupe=True,
        )


def build_loops(app: FastAPI, store: Storage, queue: JobQueue) -> loops.LoopService:
    """Loops, the one catalog service that has to reach the queue, so its own step."""

    stills = _Stills(queue)
    provide(app, wiring.STILLS, stills)
    service = loops.LoopService(store.database, wants_still=stills.wants_still)
    provide(app, loops.SERVICE, service)
    loops.register_handlers(service=service)
    return service
