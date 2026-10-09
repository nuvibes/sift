# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a downloaded file is called, decided by Sift in staging rather than by the fetching tool."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from sift.kernel.naming import TOKEN, TOKENS, Facts, fill, free_name

_TOKEN = TOKEN
_free_name = free_name

#: Empty keeps the fetcher's name: most responses carry a meaningful one.
DEFAULT_TEMPLATE = ""


#: yt-dlp's staged ` [<id>]` (`sources/argv.py`); a name with other spaces is somebody's and kept.
_TOOL_ID = re.compile(r"(?P<name>[^ \[\]]+) \[(?P<id>[^ \[\]]+)\]")


def without_tool_id(stem: str) -> str:
    """A staged stem with the tool's ` [<id>]` taken off; any other stem unchanged."""
    found = _TOOL_ID.fullmatch(stem)
    return stem if found is None else found.group("name")


def tool_id(stem: str) -> str | None:
    """The id the tool wrote into a staged stem, kept for `{id}`; None if it repeats the name."""
    found = _TOOL_ID.fullmatch(stem)
    if found is None or found.group("id") == found.group("name"):
        return None
    return found.group("id")


async def rename(path: Path, template: str, facts: Facts) -> Path:
    """Rename a staged file by the template; on any doubt it stays put, never lost."""
    stem = fill(template, facts) or without_tool_id(path.stem)
    if not stem or stem == path.stem:
        return path
    target = await asyncio.to_thread(_free_name, path, stem)
    if target is None:
        return path
    try:
        # Sift's own staging file, never one in the library.
        return await asyncio.to_thread(
            path.rename, target
        )  # nosemgrep: sift-no-file-removal-outside-delete-trash
    except OSError:
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
