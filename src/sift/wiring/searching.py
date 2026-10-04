# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one filter engine, the search surface, and the saved walls."""

from __future__ import annotations

from fastapi import FastAPI

from sift.kernel import wiring
from sift.kernel.jobs import JobQueue
from sift.kernel.wiring import provide
from sift.slices import search, theater
from sift.wiring.built import Storage


async def build_search(app: FastAPI, store: Storage, queue: JobQueue) -> None:
    """The one filter engine, the search surface, and the saved walls.

    Everything that turns a typed query or a set of clicked filters into constraints goes through
    one object: the grid reads it and the search endpoints hold the same instance, so the two can
    never come to disagree about what a query means. That is the whole reason it is built once.

    After the semantic feature, because it is handed the shape that answers "what do these words
    mean". It depends on that shape and never on that slice; this is the one place the two meet.
    And after the queue, because a search asks it for a catch-up when it notices the index is
    behind, which is the only thing that keeps the word index current without a job on a timer.
    """
    compiler = search.FilterCompiler(
        store.access,
        semantic=wiring.part_of_app(app, wiring.SEMANTIC_SEARCH),
        # One token needs a preference to answer: `viewed:continue` filters to what this user is
        # part-way through, and whether it keeps a place at all, and in what, is theirs to set.
        # The hub is already provided by the time this runs; the compiler reads two keys through it
        # and nothing else.
        settings=wiring.part_of_app(app, wiring.SETTINGS_HUB),
    )
    provide(app, wiring.FILTER_ENGINE, compiler)
    provide(
        app,
        search.SERVICE,
        search.SearchService(store.database, store.access, compiler, queue=queue),
    )
    # Saved walls, and nothing else. Theater's cells run on the search and playback routes, so this
    # holds no resolver, no queue and no media of any kind.
    provide(app, theater.SERVICE, theater.TheaterService(store.database, compiler))
    # The search index catches up and then keeps itself current. Without this nothing ever fills
    # it, and free text finds nothing at all. A test cannot show that, because the fixtures build
    # the index themselves.
    await search.jobs.ensure_scheduled(queue=queue, database=store.database)
