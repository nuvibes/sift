# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compression samples whose job has been swept.

A sample is a few seconds of a file encoded the way a compression would be, written under Sift's
own cache and named after the job that made it. It is served for as long as that job's row exists
and never afterwards (the route refuses a sample whose job it cannot find) and the queue sweeps
a settled job a week after it was made. Nothing else sweeps the file it named: without this,
samples would sit under `cache/compress-samples` for as long as the cache directory existed.

Registered as a tidying so it obeys the two rules that hold for all of them: it says what it would
remove before removing anything, and it does not run on its own. What it removes is a file nothing
can serve, made in seconds from a file that is still in the library.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from sift.kernel.log import get_logger
from sift.kernel.tidy import Leftovers, Resources, existing_job_ids, register_tidying
from sift.slices.media_edit.jobs import samples_directory

log = get_logger(__name__)


class StrandedSamples:
    """Compression samples whose job has been swept, and which nothing can serve."""

    name = "stranded-samples"
    costly = False
    title = "Compression samples nothing can show you"
    noun = "sample"
    nouns = "samples"
    detail = (
        "A short sample made while you were choosing how much to compress a file. Each one is "
        "shown for as long as its job is on the Activity screen and never afterwards; these "
        "belong to jobs that have since been swept. Removing them changes nothing about your files."
    )

    def __init__(self, resources: Resources) -> None:
        self._resources = resources
        self._directory = samples_directory(resources.settings)

    def _listed(self) -> dict[str, Path]:
        """Every sample on the disk, by the job id it is named after."""
        try:
            entries = list(os.scandir(self._directory))
        except OSError:
            return {}
        found: dict[str, Path] = {}
        for entry in entries:
            if entry.is_file(follow_symlinks=False):
                found[Path(entry.name).stem] = Path(entry.path)
        return found

    async def _stranded(self) -> list[Path]:
        listed = await asyncio.to_thread(self._listed)
        if not listed:
            return []
        alive = await existing_job_ids(self._resources, list(listed))
        return [path for job_id, path in listed.items() if job_id not in alive]

    async def survey(self) -> Leftovers:
        stranded = await self._stranded()
        sizes = await asyncio.to_thread(lambda: [_size(path) for path in stranded])
        return Leftovers(
            name=self.name,
            title=self.title,
            detail=self.detail,
            noun=self.noun,
            nouns=self.nouns,
            count=len(stranded),
            frees_bytes=sum(sizes),
        )

    async def run(self) -> int:
        stranded = await self._stranded()
        removed = await asyncio.to_thread(_remove_all, stranded)
        if removed:
            log.info("media_edit.samples_tidied", removed=removed)
        return removed


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _remove_all(paths: list[Path]) -> int:
    removed = 0
    for path in paths:
        try:
            # Sift's own scratch, under Sift's own cache, named after a job that no longer exists.
            path.unlink()  # nosemgrep: sift-no-file-removal-outside-delete-trash
            removed += 1
        except OSError:
            continue
    return removed


def register() -> None:
    register_tidying(StrandedSamples.name, StrandedSamples)
