# SPDX-License-Identifier: AGPL-3.0-or-later
"""The files Sift would not take, as the workbench sees them.

The workbench knows nothing about the ingress gate. What it knows is that something registered a
queue with a name, a count and a way to take a decision back, and this is that, twice, for the
two piles a refusal falls into.

## Two cards, because they are two questions with two different answers

**Quarantine** is what Sift itself moved. A download, an upload, a drop or a paste failed the gate,
and Sift put the file in a directory of its own, so the file is in Sift's keeping and the question
is whether to delete it now or let the retention rule take it.

**Skipped Files** is what Sift left alone. A file already in somebody's own folder failed the same
gate during a scan, and Sift does not move things around in a library it is only reading. The file
is exactly where it was; what exists is a row saying it was walked past. The question is whether
that refusal was right, and letting one through is answered by forgetting it so the next scan looks
again.

They have opposite answers to "where is my file", which is why they are two cards here rather than
one.

## Neither of them can show you a picture

Every other queue on the board draws stills of what it is about. These cannot, and it is not an
omission: a refused file was never imported, so there is no asset, no thumbnail and nothing to
address. `pictures_of` answering with nothing is the protocol's own word for that.

Everything here is a translation. The decisions themselves stay in the store and the service beside
it.
"""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath

from sift.kernel.access import Viewer
from sift.kernel.config import Settings
from sift.kernel.seams import SettingsSeam
from sift.kernel.workbench import DOER, Band, Preview, Recorded, Summary, Worded
from sift.slices.library_roots import quarantine
from sift.slices.library_roots.service import LibraryService

#: What the two queues are called wherever they are stored: in the registry, and in every receipt
#: written against them.
QUARANTINE_QUEUE = "quarantine"
SKIPPED_QUEUE = "skipped"

#: The page the two share. They are two halves of ONE refusal, told apart only by where the file
#: ended up, so reading one without the other answers half the question "what did Sift not take".
REFUSED_GROUP = "refused"


class QuarantineQueue:
    """Files Sift moved out of the way, waiting for somebody to say whether to keep them."""

    name = QUARANTINE_QUEUE
    title = "Quarantine"
    #: A log, not a judgement. Nothing is being asked: these are files Sift could not read,
    #: reported so somebody can find them. It stays on the board because a pile nobody can
    #: find is worse than a quiet card (see `Band.LOG`).
    band = Band.LOG
    #: See `REFUSED_GROUP`.
    group = REFUSED_GROUP
    #: And what the whole page is called, because the board draws a group as ONE card.
    group_title = "Quarantined and skipped files"
    #: What the card on the board is for: this group's page, which this queue leads. See
    #: `Queue.purpose`.
    purpose = "Files Sift held back or couldn't import, for you to check."
    #: Every decision here is a deletion, and the file is off the disk. See `reverse`.
    reversible = False
    #: Surveyed on every read: the pile is a folder on disk that the keeping rule empties without
    #: a write anybody announces, and reading it costs nothing. See `Moves`.
    moved_by = None

    def __init__(self, settings: Settings, preferences: SettingsSeam) -> None:
        self._settings = settings
        #: Read fresh on every survey rather than at boot, so the sentence on the card says the
        #: rule that is in force rather than the one that was in force when the process started.
        self._preferences = preferences

    async def available(self) -> bool:
        """Always. Refusing a file needs nothing switched on.

        A zero here is the honest kind and is the state the pile is meant to be in: nothing has
        been refused, or everything refused has been dealt with.
        """
        return True

    async def survey(self, viewer: Viewer) -> Summary:
        # Reading a directory is a handful of stats, and it may be a network mount: off the loop,
        # like every other directory walk in Sift.
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
        """Nothing, and there never can be. See the note at the top of this file."""
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Nothing to put back.

        The only decision this pile records is a deletion, and a deleted file is gone from the disk:
        there is no row to restore and no bytes to restore it from. Saying so by refusing is the
        same answer the near-duplicate queue gives to a decision that deleted a file, and for the
        same reason: undo does not pretend.
        """
        return False


class SkippedQueue:
    """Files in somebody's own folders that Sift read, refused, and left exactly where they were."""

    name = SKIPPED_QUEUE
    title = "Skipped"
    #: The other half of the same log. See `QuarantineQueue.band`.
    band = Band.LOG
    #: See `REFUSED_GROUP`.
    group = REFUSED_GROUP
    #: Not the first of its group, so it names no card. See `Queue.group_title`.
    group_title = None
    #: What this tab is for, said by its group's card only where this queue leads it. See
    #: `Queue.purpose`.
    purpose = "Files in your folders that Sift can't import."
    #: Nothing here destroys anything, so there is nothing to put back. See `reverse`.
    reversible = False
    #: Surveyed on every read: one count of one table that a scan writes without telling the
    #: screens. See `Moves`.
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
        """Nothing, and there never can be. See the note at the top of this file."""
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Nothing to put back, and nothing was taken.

        Letting a file through destroys nothing: it forgets that the file was refused, and the file
        itself was never touched. So there is no state to restore, and the refusal returns on its
        own if the file really is what Sift thought, because the next scan reads it again and comes
        to the same conclusion. Re-refusing it here would only be racing that scan to write the
        same row.
        """
        return False

    def worded(self, recorded: Recorded) -> Worded | None:
        """This decision's line, worded when shown (see `kernel.workbench.Recorded`).

        The stored title says "Asked Sift to look at a skipped file again": nobody doing it, and
        the file unnamed though the payload records its path. The file was never imported, so it is
        said by its name as plain words: there is no page for it.
        """
        held = recorded.held()
        path = held.get("rel_path")
        if not isinstance(path, str) or not path.strip():
            return None
        name = PurePosixPath(path.replace("\\", "/")).name or path
        return Worded(said=(DOER, f" asked Sift to include {name} in the next scan"))


def _kept_for(keep_days: int) -> str:
    """What the card says the retention rule will do, in the rule that is actually in force.

    The number is in the sentence rather than left to a settings screen somebody would have to go
    and find, because "these get deleted eventually" and "these get deleted in thirty days" are
    different facts to somebody deciding whether to go and look at the pile now.
    """
    if keep_days <= 0:
        return (
            f"{_WHAT_IS_HELD}, held here until you delete them. {_ALSO_HELD} "
            "Deleting one can't be undone."
        )
    days = "day" if keep_days == 1 else "days"
    return (
        f"{_WHAT_IS_HELD}, held here for {keep_days} {days} after each one arrived. "
        f"{_ALSO_HELD} Deleting one now can't be undone."
    )


#: What a quarantined file IS, said first. Nearly every one is a download whose site answered with
#: something other than the file (a play-button picture, an error page), and "files Sift couldn't
#: import" would leave the reader to guess whether Sift or the file was at fault.
_WHAT_IS_HELD = "Downloads whose bytes weren't the file the page promised"
_ALSO_HELD = "A file you upload, drop or paste that isn't a picture or a video appears here too."


__all__ = [
    "QUARANTINE_QUEUE",
    "SKIPPED_QUEUE",
    "QuarantineQueue",
    "SkippedQueue",
]
