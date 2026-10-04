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

#: How many repairs one write asks for: the batch `JobQueue.enqueue_many` is sized for.
_REPAIRS_AT_ONCE = 1000

#: The bound the read of files needing repair takes, which it must have (a read of the library
#: without one is refused as a sweep). Past any library's count of files that need repairing: the
#: rule covers every one of them.
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
        # Turning face recognition off gives the memory back. The models are a hundred and seventy
        # megabytes held for as long as the process lives; without this, switching the feature off
        # frees nothing until the next restart. Only on the way off: turning it ON loads them when
        # they are first needed.
        if faces.ENABLED_KEY in changed and not await hub.get_app(faces.ENABLED_KEY):
            understanding.faces.release()
        # The same for searching by meaning, and for the same reason: its models are several
        # hundred megabytes held for the life of the process. Only on the way off. Note what this
        # deliberately does NOT do: the index it built stays. Turning something off to see what it
        # does should not cost hours of re-reading every file, so there is a separate control that
        # removes it.
        if semantic.ENABLED_KEY in changed and not await hub.get_app(semantic.ENABLED_KEY):
            understanding.semantic.release()
        # Changing the face model family measures every face found so far again, from the
        # pictures Sift kept, which is what the setting's disclosure promises. Only with the feature on
        # and the new family's models present: without them the pass has nothing to measure with,
        # and the fetch that brings them asks for it itself.
        if faces.MODEL_KEY in changed and await understanding.faces.ready():
            await queue.enqueue_when_settled(faces.FACE_REMEASURE, delay=0)
        # Turning "Find usernames in photo details" on asks for the folder pass at once, so the
        # files already waiting on a username number have their pictures read now rather than
        # when the next import settles. The pass itself reads them again: a pass made with the
        # switch off remembers nothing as read (`suggestions.service.NOT_READ`).
        if suggestions.READ_METADATA_KEY in changed and await hub.get_app(
            suggestions.READ_METADATA_KEY
        ):
            with contextlib.suppress(JobSwitchedOff):
                await queue.enqueue_when_settled(suggestions.SUGGESTION_SCAN, delay=0)
        # Turning "Create people from these fingerprints as their faces are recognized" on runs the
        # pass over facial fingerprints at once, so the entries held already that faces match are
        # made People now rather than at the next scan; turning recognition on runs it too, since
        # a fingerprints file may be taken in while it is off. Each person is announced as they
        # are made, and the run is on Activity. Off needs nothing done: a person made stays.
        for key in (faces.PEOPLE_FROM_FILES_KEY, faces.ENABLED_KEY):
            if key in changed and await hub.get_app(key):
                with contextlib.suppress(JobSwitchedOff):
                    await queue.enqueue_when_settled(faces.FACE_PEOPLE_FROM_FILES, delay=0)

    async def _schedules(self, changed: set[str]) -> None:
        queue, task_clock = self.queue, self.task_clock
        # The playback repair's switch stops Sift MAKING repaired copies; it destroys none. A
        # delete on Off would take the copies the player makes for a file whose container the
        # browser cannot read as well (a different product the switch does not govern), and On
        # asks only for the interleave repairs back. A copy is cache, and the place that deletes
        # cache on purpose is `Settings > Maintenance`, which offers these copies while the
        # switch is off (`kernel.tidy.RepackagedCopies`) and says how much space it frees.
        # Turning the quarantine retention rule ON has to queue its sweep, because with the rule off
        # there is no job waiting to notice: without it, switching retention on would do nothing at
        # all until the next restart. Off needs nothing done: the running job stands itself down
        # when it next reads the rule, and stops requeueing itself.
        #
        # EVERY TIMED TASK, BY ONE RULE: a change to anything it reads (its cadence, its When, the
        # retention that decides whether it has anything to do, or quiet hours) takes the waiting
        # run back and places the next one again, so a changed schedule does not keep the old
        # moment and Off does not leave a run queued to wake up and log "off". A retired
        # key names the settings it was retired into, so an old screen's write reaches them too.
        expanded = set(changed)
        for key in changed:
            retired = get_retired(key)
            if retired is not None:
                expanded.update(retired.into)
        quiet_range = {tasks.FROM_KEY, tasks.UNTIL_KEY}
        whens = {task.when_key for task in registered_schedules().values()}
        if expanded & (quiet_range | whens):
            # The next claim asks quiet hours again rather than a few seconds from now, so a task
            # moved to "As soon as there is work" starts at once.
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
            # Turning it ON has to ask for the work, because nothing else will. The pass that
            # finds files needing repair only looks at ones nobody has MEASURED, and a file
            # measured while the switch was off was measured already, so without this, off-then-on
            # leaves exactly the files the feature exists for unrepaired and silent about it.
            # EVERY such file whose copy is missing, in batches of what one write may carry, rather
            # than the first few hundred: a copy deleted in Maintenance is missing too.
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
    """What has to happen the moment a preference is saved, rather than on somebody's timer.

    Assembled here because the things that react belong to features that do not know about each
    other. The settings route calls this after a successful save. Pool settings need no entry: the
    pool reads them on its own timer.
    """

    provide(
        app, wiring.ON_SETTINGS_CHANGED, _Reactions(store, queue, hub, understanding, task_clock)
    )
