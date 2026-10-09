# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Stash migration's names, its refusal, and the state every part of it shares."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, LibraryStore, UserStateStore
from sift.kernel.db import Database
from sift.kernel.enrichment import Enricher, Naming
from sift.kernel.seams import PhotoSetSeam
from sift.slices.stash_migration.ports import StashDoors
from sift.slices.stash_migration.waiting import WaitingStore

#: The task that brings a Stash library in.
STASH_IMPORT = "stash_import"

#: The pass that applies what waits, once files have arrived and been fingerprinted.
STASH_ARRIVED = "stash_arrived"

#: Where the copy and what was read about it live, in this library's data folder.
FOLDER = "stash"
COPY_NAME = "stash.sqlite"
PLAN_NAME = "plan.json"
REPORT_NAME = "report.json"

#: How long a Loop made from a Stash marker with no end runs. A moment has no length of its own,
#: and a Loop must have one; twenty seconds is long enough to see what the moment was.
MOMENT_MS = 20_000

#: The tag every Loop made from a moment carries, so the made-up length can be found and trimmed.
MOMENT_TAG = "From a Stash marker"

#: What a plan's source is called when a writer asks which box answered: Stash is not a box, and
#: the actor on every write is the person who pressed Run, so this names the source for the plan's
#: own bookkeeping and nothing reads it as a box.
SOURCE = "stash"


class StashRefused(Exception):
    """Refused, with a sentence a person can act on, and the status a route answers it with."""

    def __init__(self, message: str, *, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


class StashBase:
    """The migration's state, which every part of `StashMigration` stands on."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        library: LibraryStore,
        enricher: Enricher,
        naming: Naming,
        user_state: UserStateStore,
        photo_sets: PhotoSetSeam | None = None,
        *,
        content: ContentStore,
        doors: StashDoors | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._content = content
        self.doors = doors
        self._db = database
        self._settings = settings
        self._library = library
        self._enricher = enricher
        self._naming = naming
        self._user_state = user_state
        self._photo_sets = photo_sets
        self._clock = clock
        self._waiting = WaitingStore(database)

    @property
    def folder(self) -> Path:
        return self._settings.data_dir / FOLDER
