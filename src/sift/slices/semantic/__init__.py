# SPDX-License-Identifier: AGPL-3.0-or-later
"""Searching by what a picture looks like rather than by what it is called.

A model reads a picture and produces a few hundred numbers describing it; the same model reads a
typed sentence and produces numbers in the same space. Searching is then finding the pictures whose
numbers sit nearest the sentence's, so "a dog on a beach" can find a clip nobody ever labelled,
and "more like this one" is the same question asked with a picture instead of words.

Four things about it are decisions rather than details:

**It is off until somebody turns it on, and it ships with no model.** The weights are obtained when
the feature is enabled, from the publisher, under whatever terms that publisher sets: Sift
distributes none of them. An install that never switches this on pays nothing: no download, no
index, no memory, and the search box behaves exactly as it did.

**The expense is the index, not the search.** Describing a library is a background job measured in
minutes to hours. Answering a query is one pass over the typed words and one comparison, measured
in tens of milliseconds. That asymmetry is why the index is throttled and resumable, and why
searching needs no special treatment at all.

**It never blends into the ordinary search quietly.** A visible control turns it on, because a
result set that changed shape because a model had an opinion is unexplainable to the person looking
at it.

**It can be absent.** The index is kept in a table type that arrives with a database add-on, and
some machines cannot load one. There, this feature reports itself unavailable and says why, and
every other part of Sift carries on. An install missing an add-on must lose one feature, never the
application.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_connection_extension
from sift.kernel.jobs.quiet_hours import WHEN_WORK
from sift.kernel.jobs.schedules import PER_FILE, ScheduledTask, register_schedule
from sift.slices.semantic import schema as schema  # registers the schema component
from sift.slices.semantic import settings, tidy
from sift.slices.semantic.embed import Embedder
from sift.slices.semantic.frames import frame_requests
from sift.slices.semantic.jobs import (
    SEMANTIC_DESCRIBE,
    SEMANTIC_WHOLE_PICTURE,
    register_handlers,
)
from sift.slices.semantic.jobs import (
    describe as describe_file,
)
from sift.slices.semantic.records import Records
from sift.slices.semantic.router import router
from sift.slices.semantic.search import SemanticSearch
from sift.slices.semantic.service import SERVICE, Readiness, SemanticService
from sift.slices.semantic.settings import describes_at_once
from sift.slices.semantic.similar import SimilarFinder, Tier
from sift.slices.semantic.store import DIMENSION, EXTENSION, STORE, Neighbour, VectorStore
from sift.slices.semantic.whole_picture import WHOLE_PICTURE, WholePicture

# The switch the task names has to be registered before the task is.
settings.register()

#: Describing files for Smart Search, as a task. Run now is an Identify run over the library for
#: this product alone. As files arrive by default, for the reason recognition's task gives: the
#: feature switch is the consent, and this answer counts only once it is on.
register_schedule(
    ScheduledTask(
        id="smart-search",
        title="Describe files for Smart Search",
        explain="Describes new files, so Smart Search can find them by what they show.",
        job_type=SEMANTIC_DESCRIBE,
        needs_starter=True,
        when_default=WHEN_WORK,
        set_in="semantic",
        unit=PER_FILE,
        switch=settings.ENABLED_KEY,
    )
)

__all__ = [
    "DESCRIBE_ON_IMPORT_KEY",
    "DIMENSION",
    "ENABLED_KEY",
    "EXTENSION",
    "SEMANTIC_DESCRIBE",
    "SEMANTIC_WHOLE_PICTURE",
    "SERVICE",
    "STORE",
    "WHOLE_PICTURE",
    "Embedder",
    "Neighbour",
    "Readiness",
    "Records",
    "SemanticSearch",
    "SemanticService",
    "SimilarFinder",
    "Tier",
    "VectorStore",
    "WholePicture",
    "describe_file",
    "describes_at_once",
    "frame_requests",
    "register_handlers",
    "router",
]

ENABLED_KEY = settings.ENABLED_KEY
DESCRIBE_ON_IMPORT_KEY = settings.DESCRIBE_ON_IMPORT_KEY


async def _load_vector_extension(connection: Connection) -> None:
    """Add the vector table type to one connection.

    Imported here rather than at the top of the file so that a machine where the package is
    somehow missing loses this feature with a line in the log, rather than failing to import the
    module and taking the application down with it.
    """
    import sqlite_vec

    await connection.load_extension(sqlite_vec.loadable_path())


register_connection_extension(EXTENSION, _load_vector_extension)
tidy.register()
