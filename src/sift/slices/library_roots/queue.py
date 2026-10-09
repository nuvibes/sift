# SPDX-License-Identifier: AGPL-3.0-or-later
"""The files Sift would not take, as two workbench queues.
Quarantine is what Sift moved; Skipped Files is what it left in place. Neither has a picture."""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath

from sift.kernel.access import Viewer
from sift.kernel.config import Settings
from sift.kernel.seams import SettingsSeam
from sift.kernel.workbench import DOER, Band, Preview, Recorded, Summary, Worded
from sift.slices.library_roots import quarantine
from sift.slices.library_roots.service import LibraryService

QUARANTINE_QUEUE = "quarantine"
SKIPPED_QUEUE = "skipped"

#: Two halves of one refusal, shown on one page.
REFUSED_GROUP = "refused"


class QuarantineQueue:
    """Files Sift moved out of the way, waiting for somebody to say whether to keep them."""

    name = QUARANTINE_QUEUE
    title = "Quarantine"
    #: A log, not a judgement; it stays so the pile can be found (see `Band.LOG`).
    band = Band.LOG
    group = REFUSED_GROUP
    group_title = "Quarantined and skipped files"
    purpose = "Files Sift held back or couldn't import, for you to check."
    #: Every decision here is a deletion.
    reversible = False
    #: Surveyed on every read: the keeping rule empties the folder silently.
    moved_by = None

    def __init__(self, settings: Settings, preferences: SettingsSeam) -> None:
        self._settings = settings
        #: Read on every survey, so the card states the rule in force now.
        self._preferences = preferences

    async def available(self) -> bool:
        """Always. Refusing a file needs nothing switched on."""
        return True

    async def survey(self, viewer: Viewer) -> Summary:
        # It may be a network mount.
        held = await asyncio.to_thread(quarantine.listing, self._settings)
        keep_days = quarantine.keep_days_from(
            await self._preferences.get_app(quarantine.KEEP_DAYS_KEY)
        )
        return Summary(
            name=QUARANTINE_QUEUE,
            title=self.title,
            verb="quarantined files",
            verb_one="quarantined file",
            decision=_kept_for(keep_days),
            icon="shield",
            count=len(held),
        )

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing, and there never can be: a refused file has no asset."""
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Nothing to put back: the only decision is a deletion."""
        return False


class SkippedQueue:
    """Files in somebody's own folders that Sift read, refused, and left exactly where they were."""

    name = SKIPPED_QUEUE
    title = "Skipped"
    band = Band.LOG
    group = REFUSED_GROUP
    group_title = None
    purpose = "Files in your folders that Sift can't import."
    reversible = False
    #: Surveyed on every read: a scan writes the table without telling the screens.
    moved_by = None

    def __init__(self, service: LibraryService) -> None:
        self._service = service

    async def available(self) -> bool:
        """Always. Every install scans folders, so every install can refuse something in one."""
        return True

    async def survey(self, viewer: Viewer) -> Summary:
        return Summary(
            name=SKIPPED_QUEUE,
            title=self.title,
            verb="skipped files",
            verb_one="skipped file",
            decision=(
                "Files in your folders that Sift can't import. They stay where they are. Choose "
                "Try again to include a file in the next scan."
            ),
            icon="rule_folder",
            count=await self._service.rejection_count(),
        )

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing, and there never can be: a refused file has no asset."""
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Nothing to put back: letting a file through only forgets the refusal."""
        return False

    def worded(self, recorded: Recorded) -> Worded | None:
        """This decision's line, naming the file in plain words since it has no page."""
        held = recorded.held()
        path = held.get("rel_path")
        if not isinstance(path, str) or not path.strip():
            return None
        name = PurePosixPath(path.replace("\\", "/")).name or path
        return Worded(said=(DOER, f" asked Sift to include {name} in the next scan"))


def _kept_for(keep_days: int) -> str:
    """What the card says the retention rule will do, with the number in force."""
    if keep_days <= 0:
        return (
            f"{_WHAT_IS_HELD}, held here until you delete them. {_ALSO_HELD} "
            "Deleting one can't be undone."
        )
    days = "day" if keep_days == 1 else "days"
    return (
        f"{_WHAT_IS_HELD}, held here for {keep_days} {days} each. "
        f"{_ALSO_HELD} Deleting one now can't be undone."
    )


#: What a quarantined file usually is: a download whose site answered with something else.
_WHAT_IS_HELD = "Downloads whose bytes weren't the file the page promised"
_ALSO_HELD = "A file you upload, drop or paste that isn't a picture or a video appears here too."


__all__ = [
    "QUARANTINE_QUEUE",
    "SKIPPED_QUEUE",
    "QuarantineQueue",
    "SkippedQueue",
]
