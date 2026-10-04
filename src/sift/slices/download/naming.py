# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a downloaded file is called, decided by Sift rather than by the tool that fetched it.

The tools only write into a staging directory Sift owns, and every file goes through the one import
path that sanitises the name and confirms it lands inside the library; a template handed to a tool
would decide where it writes. Most sites never go through a tool anyway, so one token set is applied
here to whatever any fetch seam produced, renaming in staging just before the import takes its name
from the file. A template that fills to nothing keeps the name the file had.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

# The naming words are the kernel's, because a batch rename in the library fills the same ones.
from sift.kernel.naming import TOKEN, TOKENS, Facts, fill, free_name

#: The kernel's words under the names this slice's tests and the Site defaults read them by.
_TOKEN = TOKEN
_free_name = free_name

#: The template used unless a site says otherwise. Empty keeps the fetcher's name, since the
#: resolvers read a real, meaningful name out of most responses.
DEFAULT_TEMPLATE = ""


#: The tool's ` [<id>]`, which never reaches the library. yt-dlp writes `<title> [<id>]` into
#: staging (`sources/argv.py`) because it skips a destination already there (two clips of one post
#: with one title) and resumes only a part-file its template still names; Sift then decides the kept
#: name. Nothing reads the id back out of a name: a repeat is known by its address. Only the
#: template's own shape matches: `--restrict-filenames` turns every other space into an underscore,
#: so a staged name's one space is the template's, and a name with spaces elsewhere or nested
#: brackets (`Holiday photo [2019].jpg` from another fetcher) is somebody's and is left alone. A
#: repeated id (`image0 [image0]`, a plain file address) is still an id. The literal is copied from
#: the template rather than imported, which would pull the fetch package in; the two are held
#: together by `test_a_long_discord_attachment_is_named_once_through_the_real_naming_path`.
_TOOL_ID = re.compile(r"(?P<name>[^ \[\]]+) \[(?P<id>[^ \[\]]+)\]")


def without_tool_id(stem: str) -> str:
    """A staged stem with the tool's ` [<id>]` taken off; any other stem unchanged.

    `A_video_title [Xy7Qm2Lp9Ka]` is `A_video_title`, and `image0 [image0]` is `image0`. A stem
    not in the tool's shape (a space in the title, an empty bracket, more name after it) is
    returned as it came.
    """
    found = _TOOL_ID.fullmatch(stem)
    return stem if found is None else found.group("name")


def tool_id(stem: str) -> str | None:
    """The ID the tool wrote into a staged stem, KEPT as a fact rather than thrown away.

    `{id}` is how somebody who wants it in the name asks for it back; for some hosts it is the only
    unique part. None for a stem not in the tool's shape, or an id that only repeats the name.
    """
    found = _TOOL_ID.fullmatch(stem)
    if found is None or found.group("id") == found.group("name"):
        return None
    return found.group("id")


async def rename(path: Path, template: str, facts: Facts) -> Path:
    """Rename a staged file to what the template says, and return where it now is.

    The file stays where it is on any doubt (an empty result, a refusing filesystem, endless
    collisions): naming must never lose a downloaded file. A taken name is not doubt: a template of
    `{site}` and `{creator}` fills alike for every file of a post, so it gets the next number. Off
    the event loop, because the filesystem may be a network share.
    """
    # An empty template keeps the name the file already had, which is the tool's name with its
    # id taken off, not the staged one. See `without_tool_id`.
    stem = fill(template, facts) or without_tool_id(path.stem)
    if not stem or stem == path.stem:
        return path
    target = await asyncio.to_thread(_free_name, path, stem)
    if target is None:
        return path
    try:
        # A file Sift wrote seconds ago in its own staging directory, never in the library; the
        # no-removal rule is about files somebody else put somewhere.
        return await asyncio.to_thread(
            path.rename, target
        )  # nosemgrep: sift-no-file-removal-outside-delete-trash
    except OSError:
        # A name the filesystem will not take: too long once encoded, a reserved word on some
        # site. The download is fine; only its name is not what was asked for.
        return path


__all__ = [
    "DEFAULT_TEMPLATE",
    "TOKENS",
    "Facts",
    "fill",
    "rename",
    "tool_id",
    "without_tool_id",
]
