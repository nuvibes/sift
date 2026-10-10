# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Stash migration's names, its refusal, and the state every part of it shares."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path

from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, LibraryStore, UserStateStore
from sift.kernel.db import Database
from sift.kernel.enrichment import Enricher, Naming
from sift.kernel.seams import PhotoSetSeam
from sift.kernel.whole_file import write_json_whole
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


# --- what a run has already asked the stash-boxes -------------------------------------------------
# The writes a run makes are fills, so redoing them is safe; the stash-box links are paced requests
# to somebody else's service, and a long Stash spends most of its run on them. Each answer is
# written down in batches, whole or not at all, and only for the read it was made from.

CHECKPOINT_NAME = "links-done.json"

#: Answers kept in memory before they are written down: a stop asks at most this many again.
EVERY = 50

#: Nor longer than this, in seconds, while answers are waiting.
SECONDS = 10.0


class Checkpoint:
    """The links one run of one read has made, by what was asked, with what each came to."""

    def __init__(self, folder: Path, read_at: int) -> None:
        self._path = folder / CHECKPOINT_NAME
        self._read_at = read_at
        self._done: dict[str, str] = {}
        self._waiting = 0
        self._since = time.monotonic()

    async def load(self) -> None:
        """What an earlier run of this same read finished; nothing for another read's."""
        try:
            kept = json.loads(await asyncio.to_thread(self._path.read_text, encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(kept, dict) and kept.get("read_at") == self._read_at:
            done = kept.get("done")
            if isinstance(done, dict):
                self._done = {str(asked): str(went) for asked, went in done.items()}

    def went(self, asked: str) -> str | None:
        """What this link came to on an earlier run, or None to ask it now."""
        return self._done.get(asked)

    async def note(self, asked: str, went: str) -> None:
        """One more answer, written down with the others every `EVERY` or `SECONDS`."""
        self._done[asked] = went
        self._waiting += 1
        if self._waiting >= EVERY or time.monotonic() - self._since >= SECONDS:
            await self.flush()

    async def flush(self) -> None:
        if not self._waiting:
            return
        kept = {"read_at": self._read_at, "done": dict(self._done)}
        await asyncio.to_thread(write_json_whole, self._path, kept)
        self._waiting = 0
        self._since = time.monotonic()

    async def finish(self) -> None:
        """The run is over: the next one starts from nothing."""
        await asyncio.to_thread(
            self._path.unlink, True
        )  # nosemgrep: sift-no-file-removal-outside-delete-trash (the run's own record, under Sift's folder)
