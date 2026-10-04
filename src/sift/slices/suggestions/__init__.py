# SPDX-License-Identifier: AGPL-3.0-or-later
"""Folder-led attribution: reading the tree somebody already organised, and asking one question.

Files fetched through Sift arrive knowing where they came from: the site, the username, and on a
site whose usernames name people, the person. Nothing knows that about a file that arrived any other
way, and in many libraries that is most of them: dragged in, copied off an old drive, pulled by a
downloader years ago, unzipped out of a pack.

What those files do carry is a folder tree somebody organised by hand. `Nadia Vance/`,
`Instagram/harlowquin/`, `Northlight/Northlight - Jane Doe - Show 34.mp4`. The information is
sitting in the path.

**The folder is the claim and the faces are the proof.** A folder whose files mostly carry one and
the same unnamed face group, named something that reads like a name, is offered as a person, and
saying yes names the face group and attributes every file in the folder in one action.

Two things are worth knowing before reading further.

**Nothing applies itself on a name.** There is no auto-apply setting here and there is no route
that would need one. Two rungs of the ladder do write without asking, and both rest on a judgement
somebody has already made: a face group they named, a person they created. Everything that would
invent something new is confirmed by a person first.

**The stop-list matters more than the reading does.** Without it the app confidently proposes a
person called `Videos`. It is one list in one place, in `naming.py`, and it is built in: a settings
screen for it would be a second place the answer lives, and the answer it gives is wrong in the
same way for everybody.
"""

from __future__ import annotations

from sift.kernel.jobs.schedules import ScheduledTask, register_schedule
from sift.slices.suggestions import schema, settings
from sift.slices.suggestions.jobs import SUGGESTION_SCAN, register_handlers
from sift.slices.suggestions.ladder import (
    DOMINANT_FLOOR,
    DOMINANT_LEAD,
    DOMINANT_SHARE,
    DOMINANT_SHARE_WITH_A_LEAD,
    Action,
    Evidence,
    Verdict,
    decide,
    dominant,
)
from sift.slices.suggestions.metadata import Fields as PictureMetadata
from sift.slices.suggestions.metadata import read_fields as read_picture_fields
from sift.slices.suggestions.queue import NAME as FOLDER_QUEUE
from sift.slices.suggestions.queue import FiledFromFilenamesQueue, FiledQueue, FolderQueue
from sift.slices.suggestions.router import router
from sift.slices.suggestions.service import (
    PAGE,
    SERVICE,
    Applied,
    Arrivals,
    MadeSet,
    NotFound,
    Page,
    PictureFields,
    PostSets,
    Proposal,
    SuggestionError,
    SuggestionService,
    SwapFolders,
)
from sift.slices.suggestions.store import Store

#: Suggesting People for folders, as a task: the pass a scan asks for once it settles, and Run now.
register_schedule(
    ScheduledTask(
        id="suggestions",
        title="Suggest People from your folders",
        explain="Suggests a person for a folder that holds one person's files, for you to confirm.",
        job_type=SUGGESTION_SCAN,
        set_in="importing",
    )
)

__all__ = [
    "DOMINANT_FLOOR",
    "DOMINANT_LEAD",
    "DOMINANT_SHARE",
    "DOMINANT_SHARE_WITH_A_LEAD",
    "FILE_FROM_FILENAMES_KEY",
    "FOLDER_QUEUE",
    "PAGE",
    "READ_METADATA_KEY",
    "SCAN_KEY",
    "SERVICE",
    "SUGGESTION_SCAN",
    "Action",
    "Applied",
    "Arrivals",
    "Evidence",
    "FiledFromFilenamesQueue",
    "FiledQueue",
    "FolderQueue",
    "MadeSet",
    "NotFound",
    "Page",
    "PictureFields",
    "PictureMetadata",
    "PostSets",
    "Proposal",
    "Store",
    "SuggestionError",
    "SuggestionService",
    "SwapFolders",
    "Verdict",
    "decide",
    "dominant",
    "read_picture_fields",
    "register_handlers",
    "router",
    "schema",
]

#: Re-exported so the composition root and the gates name it the way they name every other switch.
FILE_FROM_FILENAMES_KEY = settings.FILE_FROM_FILENAMES_KEY
READ_METADATA_KEY = settings.READ_METADATA_KEY
SCAN_KEY = settings.SCAN_KEY

settings.register()
