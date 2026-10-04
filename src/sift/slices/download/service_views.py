# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the download ledger hands its screens and its job: the rows, the pages and the choices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: The job type this slice registers and enqueues.
DOWNLOAD = "download"

#: Whether new downloads may start at all; here so the rail reads it without an import cycle.
PAUSED_KEY = "download.paused"

#: Whether a username that names nobody yet becomes a new person.
PEOPLE_FROM_USERNAMES_KEY = "download.people_from_usernames"

#: A removed setting ("Group downloaded galleries into Photo Sets"), declared removed so History
#: lines about it still name it (`settings_registry.Removed`).
PHOTO_SETS_REMOVED_KEY = "download.photo_sets"

#: Whether a link that has been fetched before is skipped the next time it is pasted.
REMEMBER_KEY = "download.remember"

# The ledger states the job writes; `blocked` and an exhausted `failed` are read off the job.
_TERMINAL_LEDGER_STATES = frozenset(
    {"done", "duplicate", "failed", "skipped", "canceled", "quarantined"}
)

#: What a pause may be taken on: a download fetching, or one waiting its turn.
_PAUSABLE_LEDGER_STATES = frozenset({"queued", "running"})

#: What a row settled by `settle_orphans` says.
_INTERRUPTED = (
    "Sift stopped while this was downloading, so it never finished. Try it again when you like."
)


@dataclass(frozen=True, slots=True)
class SiteCount:
    """One Site the queue holds downloads from, and how many."""

    name: str
    count: int


@dataclass(frozen=True, slots=True)
class PasteChoices:
    """The answer the Downloads page gives FOR ONE PASTE, beside the folder it chose."""

    #: Whether a link already downloaded is skipped. `download.remember`'s question.
    remember: bool | None = None


#: Nothing chosen for this download: it follows the setting.
FOLLOW_THE_SETTINGS = PasteChoices()


@dataclass(frozen=True, slots=True)
class JobInput:
    """What the download job needs from the ledger: the URL, where the drop said to put it, and
    what the paste chose for itself."""

    url: str
    dest_folder_id: str | None
    choices: PasteChoices = FOLLOW_THE_SETTINGS


#: HOW a download's username was learned, the one fact about it nothing can recover later: the
#: link, the site's answer while resolving it, or the page. The column's CHECK spells the same list.
UsernameFrom = Literal["address", "resolver", "page"]
