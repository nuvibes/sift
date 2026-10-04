# SPDX-License-Identifier: AGPL-3.0-or-later
"""The files whose music has never been read, as a pile on Organize.

Everything else a Build makes is made from a few frames. This decodes a file's whole sound track:
on MP4, a few seconds for a three-minute video, the same again with the bytes already cached (so
the cost is the decode, not the share) while the read itself is a small part of the file's
bytes because the demuxer skips the video it was told to drop (MKV, WebM and AVI may read more).
A few seconds a file is still many hours over a library, and a pass of that size starting because
somebody upgraded, or because a scan found files, is the one thing this feature must never do.

Nothing reads a library for its music on its own. A file arriving through Sift into a folder that
said yes is fingerprinted at staging (`slices/music/landing.py`); a scan's follow-on only claims such
a fingerprint and never opens a file; everything else waits for the music task's Run now. So the
pile counts what is waiting, and its card on the board opens that task's row under
`Settings > Tasks`, where Run now and its When are: the question is about the whole library, and a
list of the waiting files would say the pass is about those and no others.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sift.kernel.access import Viewer
from sift.kernel.jobs.schedules import when_key
from sift.kernel.log import get_logger
from sift.kernel.workbench import Band, Preview, Summary

log = get_logger(__name__)

QUEUE = "music"

#: The product's key on the Build sheet: what the music task's Run now builds.
PRODUCT = "music"

#: The task the card opens, as it is declared in `music/__init__.py`.
TASK = "music"

#: How many files this user can see that still want a fingerprint. Handed in by the composition
#: root: the count spans the library, which is not this slice's to reach for.
Waiting = Callable[[Viewer], Awaitable[int]]

#: Whether ANYTHING is still waiting, asked without a viewer. The library's own total rather than
#: one user's, and the cheap question, asked before the scoped count.
AnyWaiting = Callable[[], Awaitable[bool]]


class MusicQueue:
    """Files whose music has never been read, counted for whoever is looking."""

    name = QUEUE
    title = "Music"
    #: A judgement: how much of this machine's time and its network a library is worth. Nothing but
    #: a person can weigh that, which is the board's first rule for what belongs in this band.
    band = Band.DECISION
    #: Stands alone. It is not another reading of anything else on the board.
    group = None
    group_title = None
    #: What the card on the board is for. See `Queue.purpose`.
    purpose = "Fingerprint the music in your files to match files that share a song."
    #: Nothing is decided here: a run is started from the task's row and stopped on Activity,
    #: where every run is stopped.
    reversible = False
    #: Surveyed on every read: a fingerprint landing is told as the work moving, which is several
    #: times a second while anything runs, and the count is one row of a kept total. See `Moves`.
    moved_by = None

    def __init__(self, *, waiting: Waiting, any_waiting: AnyWaiting) -> None:
        self._waiting = waiting
        self._any_waiting = any_waiting

    async def available(self) -> bool:
        """Always: the pile stands whatever the library's state, at zero when nothing is waiting,
        as every other pile does."""
        return True

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing. This queue's answers are about the LIBRARY rather than about any file, so there
        is no still that would say anything about one, and a strip of arbitrary files beside
        "reading the music" would say the pass was about those and no others."""
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Nothing, and `reversible` above says so before anybody is offered the button.

        Present because the board's protocol asks for it, and answering honestly: a run that has
        started is stopped on Activity, where every run is stopped.
        """
        return False

    async def survey(self, viewer: Viewer) -> Summary:
        # The cheap question first: this runs on every draw of the board, and a library with
        # nothing waiting needs no library-wide count to say so.
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
            # The way in is the music task's own row under `Settings > Tasks`, where Run now and
            # its When are: the question is about the whole library, so a list of the waiting
            # files would say the pass is about those, and no Organize page asks it.
            opens=f"/settings/tasks#{when_key(TASK)}",
        )
