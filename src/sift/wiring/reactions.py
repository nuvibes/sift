# SPDX-License-Identifier: AGPL-3.0-or-later
"""What has to happen the moment a preference is saved."""

from __future__ import annotations

import contextlib

from fastapi import FastAPI

from sift.kernel import mp4, wiring
from sift.kernel.jobs import JobQueue, JobSwitchedOff
from sift.kernel.jobs.clock import TaskClock
from sift.kernel.jobs.schedules import registered_schedules
from sift.kernel.settings_registry import get_retired
from sift.kernel.wiring import provide
from sift.slices import (
    faces,
    library_roots,
    media_jobs,
    performance,
    semantic,
    settings_hub,
    suggestions,
    tasks,
)
from sift.wiring.built import Storage, Understanding

_REPAIRS_AT_ONCE = 1000

#: A read of the library without a bound is refused as a sweep; this is past any real count.
_EVERY_FILE = 1_000_000


class _Reactions:
    """What has to happen the moment a preference is saved, in the order it is done."""

    def __init__(
        self,
        store: Storage,
        queue: JobQueue,
        hub: settings_hub.SettingsService,
        understanding: Understanding,
        task_clock: TaskClock,
    ) -> None:
        self.store = store
        self.queue = queue
        self.hub = hub
        self.understanding = understanding
        self.task_clock = task_clock

    async def __call__(self, changed: set[str]) -> None:
        await self._features(changed)
        await self._schedules(changed)
        await self._repair(changed)

    async def _features(self, changed: set[str]) -> None:
        hub, queue, understanding = self.hub, self.queue, self.understanding
        # Turning face recognition off gives the models' memory back now, not at the next restart.
        if faces.ENABLED_KEY in changed and not await hub.get_app(faces.ENABLED_KEY):
            understanding.faces.release()
        # The same for searching by meaning; the index it built stays, removed only on purpose.
        if semantic.ENABLED_KEY in changed and not await hub.get_app(semantic.ENABLED_KEY):
            understanding.semantic.release()
        # A new face model family measures every face again, once its models are present.
        if faces.MODEL_KEY in changed and await understanding.faces.ready():
            await queue.enqueue_when_settled(faces.FACE_REMEASURE, delay=0)
        # Turning username reading on asks for the folder pass immediately.
        if suggestions.READ_METADATA_KEY in changed and await hub.get_app(
            suggestions.READ_METADATA_KEY
        ):
            with contextlib.suppress(JobSwitchedOff):
                await queue.enqueue_when_settled(suggestions.SUGGESTION_SCAN, delay=0)
        # Turning fingerprint people on, or recognition on, runs the fingerprint pass immediately.
        for key in (faces.PEOPLE_FROM_FILES_KEY, faces.ENABLED_KEY):
            if key in changed and await hub.get_app(key):
                with contextlib.suppress(JobSwitchedOff):
                    await queue.enqueue_when_settled(faces.FACE_PEOPLE_FROM_FILES, delay=0)

    async def _schedules(self, changed: set[str]) -> None:
        queue, task_clock = self.queue, self.task_clock
        # Off stops making repaired copies and deletes none; every timed task is placed again on a
        # change.
        expanded = set(changed)
        for key in changed:
            retired = get_retired(key)
            if retired is not None:
                expanded.update(retired.into)
        quiet_range = {tasks.FROM_KEY, tasks.UNTIL_KEY}
        whens = {task.when_key for task in registered_schedules().values()}
        if expanded & (quiet_range | whens):
            # Asked again of quiet hours, so a task moved to "As soon as there is work" starts
            # immediately.
            queue.forget_quiet_hours()
        if expanded & quiet_range:
            await task_clock.reschedule_all()
        else:
            await task_clock.reschedule_reading(expanded)

    async def _repair(self, changed: set[str]) -> None:
        hub, queue, store = self.hub, self.queue, self.store
        if performance.REPAIR_PLAYBACK_KEY in changed and await hub.get_app(
            performance.REPAIR_PLAYBACK_KEY
        ):
            # Turning it on asks for every file whose copy is missing: nothing else revisits a
            # measured file.
            waiting = await store.content.needing_remux(mp4.NEEDS_REPAIR_BYTES, _EVERY_FILE)
            with contextlib.suppress(JobSwitchedOff):
                for start in range(0, len(waiting), _REPAIRS_AT_ONCE):
                    await queue.enqueue_many(
                        media_jobs.REMUX,
                        [{"asset_id": one} for one in waiting[start : start + _REPAIRS_AT_ONCE]],
                        dedupe=True,
                    )


def build_settings_reactions(
    app: FastAPI,
    store: Storage,
    queue: JobQueue,
    hub: settings_hub.SettingsService,
    watcher: library_roots.LibraryWatcher,
    understanding: Understanding,
    task_clock: TaskClock,
) -> None:
    """What has to happen the moment a preference is saved, assembled across features."""

    provide(
        app, wiring.ON_SETTINGS_CHANGED, _Reactions(store, queue, hub, understanding, task_clock)
    )
