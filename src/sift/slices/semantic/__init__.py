# SPDX-License-Identifier: AGPL-3.0-or-later
"""Searching by what a picture looks like: off until turned on, no model shipped, may be absent."""

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
from sift.slices.semantic.settings import describes_together
from sift.slices.semantic.similar import SimilarFinder, Tier
from sift.slices.semantic.store import DIMENSION, EXTENSION, STORE, Neighbour, VectorStore
from sift.slices.semantic.whole_picture import WHOLE_PICTURE, WholePicture

# The switch the task names has to be registered before the task is.
settings.register()

#: Describing files for Smart Search, as a task; the feature switch is the consent.
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
    "describes_together",
    "frame_requests",
    "register_handlers",
    "router",
]

ENABLED_KEY = settings.ENABLED_KEY
DESCRIBE_ON_IMPORT_KEY = settings.DESCRIBE_ON_IMPORT_KEY


async def _load_vector_extension(connection: Connection) -> None:
    """Add the vector table type to one connection; imported late, so its absence is only logged."""
    import sqlite_vec

    await connection.load_extension(sqlite_vec.loadable_path())


register_connection_extension(EXTENSION, _load_vector_extension)
tidy.register()
