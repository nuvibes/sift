# SPDX-License-Identifier: AGPL-3.0-or-later
"""Why a pass after the read goes slowly while a share's files are read first (`lanes.the_read`)."""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from pathlib import Path

from sift.kernel import lanes
from sift.kernel.content.library import LibraryStore
from sift.kernel.jobs.families import Family
from sift.slices.media_jobs.activity_wire import FamilyOfWork

READ_FIRST_ON_SHARE = "Files are read first on the network share that holds {folders}."

#: How long after a share's last read of a file its passes still say so: Activity's pace window.
WINDOW_SECONDS = 60.0


async def after_the_read_first(
    answer: dict[str, FamilyOfWork],
    library: LibraryStore | None,
    after: Collection[Family],
    joined: Callable[[Sequence[str]], str],
) -> dict[str, FamilyOfWork]:
    """Each pass after the read with work under way says so while files are read first on a share
    that holds a library folder."""
    installed = lanes.installed()
    keys = set() if installed is None else installed.reading_first(WINDOW_SECONDS)
    roots = await library.roots() if library is not None and keys else []
    names = sorted(
        one.name for one in roots if lanes.storage_for(Path(one.abs_path) / "walk").key in keys
    )
    if not names:
        return answer
    said = READ_FIRST_ON_SHARE.format(folders=joined(names))
    for family in after:
        row = answer.get(family.value)
        if row is not None and row.outstanding > 0 and row.reason is None and row.pace is None:
            answer[family.value] = row.model_copy(update={"pace": said})
    return answer
