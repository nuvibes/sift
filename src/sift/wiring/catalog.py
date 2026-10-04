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
    """The reads and writes over what is in the library: files, tags, People, sites, collections.

    Each is handed the resolver rather than a database handle, because every row any of them puts
    on a screen has to be resolved against the user asking for it.
    """
    # Adding and removing a root, and remembering what the scanner refused. It holds the access
    # repository because removing a root has to drop the permissions naming its folders first:
    # `acl_grants.object_id` carries no foreign key (the id beside it could belong to any of
    # several tables), so nothing cascades them, and nothing else will do it.
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
    """What renders a still of one moment, for everything that wants one.

    The seam's implementation, and the ONLY place in Sift that knows both which job type builds a
    picture of a moment and who is asking for one. A mark asks when it is saved; a cover asks when
    somebody picks a frame. Two features, one job, one cache key: see `StillSeam`.

    Deduped, so a sweep and a fresh save asking for the same moment in the same instant queue one
    job. The picture itself is filed under `(asset_id, kind, params)`, so a second one that did slip
    through would rewrite the same row rather than making a second file. This saves the work; it
    is not what makes it safe.
    """

    def __init__(self, queue: JobQueue) -> None:
        self._queue = queue

    async def wants_still(self, asset_id: str, at_ms: int) -> None:
        await self._queue.enqueue(
            media_jobs.LOOP_THUMBNAIL,
            {"asset_id": asset_id, "at_ms": at_ms},
            dedupe=True,
        )


def build_loops(app: FastAPI, store: Storage, queue: JobQueue) -> loops.LoopService:
    """Loops, which is the one catalog service that has to reach the queue.

    Its own step rather than a line in `build_catalog`, because that step runs before there IS a
    queue, and a mark asks for its own picture the moment it is saved. Which job type builds that
    picture is media_jobs' business and which mark wants one is this slice's, so the composition
    root is the only place that may know both: neither slice imports the other.
    """

    stills = _Stills(queue)
    provide(app, wiring.STILLS, stills)
    service = loops.LoopService(store.database, wants_still=stills.wants_still)
    provide(app, loops.SERVICE, service)
    loops.register_handlers(service=service)
    return service
