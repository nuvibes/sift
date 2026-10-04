# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a face pass knows about a folder, said in terms the rest of the app can use.

Shared by the feature that reads folders for names and the one that finds faces, which may not
import each other, and kept out of the seams package, which holds interfaces only. Counts and ids:
as much as a reader of folder names may know about recognition.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class FolderFaces:
    """What was found in the faces of one folder's files.

    `looked_at` counts files a pass finished and `with_faces` those holding a face. Both zero means
    not looked at; all looked at with no faces means nobody's face is here. Only the second is a
    suggestion, or a new library would produce thousands of guesses in its first hour.
    """

    #: How many of the folder's files a face pass has finished with.
    looked_at: int = 0

    #: How many of those turned out to contain at least one face.
    with_faces: int = 0

    #: Open, unnamed face groups here, against how many of the folder's files each is in; set-aside
    #: groups are absent, since they corroborate nothing.
    piles: dict[str, int] = field(default_factory=dict)

    #: People already named in these files by recognition, against how many files each is in.
    named: dict[str, int] = field(default_factory=dict)

    #: One face to draw per group in `piles`, so "is this her" needs no second question.
    portraits: dict[str, str] = field(default_factory=dict)

    #: Files whose faces disagree with the dominant group, shown so they can be unticked.
    dissenting: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FolderStamp:
    """A cheap number per folder that changes when its faces change.

    Lets a library pass tell which folders are worth reading properly: any number that moves when
    the answer would, never the answer itself, which is the full re-derive this avoids.
    """

    tracks: int = 0
    looked_at: int = 0
    latest: int = 0

    def as_text(self) -> str:
        return f"{self.tracks}:{self.looked_at}:{self.latest}"
