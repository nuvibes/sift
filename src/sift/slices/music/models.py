# SPDX-License-Identifier: AGPL-3.0-or-later
"""What this feature's routes answer with."""

from __future__ import annotations

from pydantic import Field

from sift.kernel.wire import Wire


class SameMusicFile(Wire):
    """One file sharing a song with the one asked about: what a strip of tiles needs to draw it.

    The same fields a lookalike carries (`slices/semantic/models.py`), for the same reason: this is
    a strip of stills on a file page, and a slice does not reach into another for its tile.
    """

    id: str
    media_type: str = ""
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    #: The token that lets a browser keep this file's pictures for a week without asking. See
    #: `SimilarItem.art` for why a strip of stills carries it.
    art: str | None = None
    #: What the file is called, so the strip can name what it draws. A name, never a path.
    name: str | None = None


class SameMusic(Wire):
    """The files sharing a song with this one that the viewer may see, closest first.

    `count` is how many are in `files`, the strip's heading says it ("11 files"), and never how
    many there are in the library: a file this viewer may not see is not counted either, because a
    number that included it would be a way of learning it exists.
    """

    files: list[SameMusicFile] = Field(default=[])
    count: int = 0
