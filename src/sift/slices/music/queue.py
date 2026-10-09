# SPDX-License-Identifier: AGPL-3.0-or-later
"""The files whose music has never been read, as a pile on Organize; nothing reads them unasked."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sift.kernel.access import Viewer
from sift.kernel.jobs.schedules import when_key
from sift.kernel.log import get_logger
from sift.kernel.workbench import Band, Preview, Summary

log = get_logger(__name__)

QUEUE = "music"

PRODUCT = "music"

TASK = "music"

#: Handed in by the composition root: the count spans the library.
Waiting = Callable[[Viewer], Awaitable[int]]

#: The cheap library-wide question, asked before the scoped count.
AnyWaiting = Callable[[], Awaitable[bool]]


class MusicQueue:
    name = QUEUE
    title = "Music"
    #: Machine time and network against a library is a judgement only a person makes.
    band = Band.DECISION
    group = None
    group_title = None
    purpose = "Fingerprint the music in your files to match files that share a song."
    #: A run is stopped on Activity, like every run.
    reversible = False
    #: Surveyed on every read; the count is one row of a kept total (see `Moves`).
    moved_by = None

    def __init__(self, *, waiting: Waiting, any_waiting: AnyWaiting) -> None:
        self._waiting = waiting
        self._any_waiting = any_waiting

    async def available(self) -> bool:
        """Always: the pile stands at zero when nothing is waiting."""
        return True

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing: the answers are about the library, not about any file."""
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Nothing; `reversible` says so before anybody is offered the button."""
        return False

    async def survey(self, viewer: Viewer) -> Summary:
        files = await self._waiting(viewer) if await self._any_waiting() else 0
        return Summary(
            name=QUEUE,
            title=self.title,
            verb="files to fingerprint for music",
            verb_one="file to fingerprint for music",
            decision=(
                "Fingerprints the music in each file, so Sift can match files that share a song. "
                "It runs once per file, in the background, and changes nothing else."
            ),
            icon="music_note_2",
            count=files,
            # About the whole library, so it opens the task's row rather than a list of files.
            opens=f"/settings/tasks#{when_key(TASK)}",
        )
