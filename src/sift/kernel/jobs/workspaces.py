# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a job keeps what it has half-written, for as long as the job is still going.

A handler that fetches something large writes it somewhere before it is a file in the library. A
temporary directory of the handler's own, swept the moment the handler returns, is exactly right
for a job that either finishes or fails, and wrong for the one
that stops in the middle and means to carry on. A pause that threw away a hundred and forty
megabytes would be a pause in name and a cancel in effect.

So the place belongs to the JOB rather than to the attempt: one directory per job id, made when the
handler first asks for it, and swept when the job reaches a state it is not coming back from. A
job that is paused, blocked, waiting or being retried still owns its directory, and the handler
finds the same path there when it runs again.

Nothing here is a cache. What is in a workspace is work in progress that nothing else knows about,
so it is removed on the job's own terms and never on a size or an age.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from sift.kernel.log import get_logger

log = get_logger(__name__)


class Workspaces:
    """The root the per-job directories live under, and the only thing that removes one."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def of(self, job_id: str) -> Path:
        """This job's directory, made if it is not there yet.

        The id is checked rather than trusted, although every caller passes one the queue minted.
        A name with a separator or a `..` in it would put the directory (and the sweep that
        later removes it) somewhere other than under the root, and the one thing this module
        must never do is delete outside its own root.
        """
        path = self._confine(job_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def size_of(self, job_id: str) -> int | None:
        """How many bytes this job's directory holds, or None when it has no directory at all.

        A READ, and the only one: it never makes the directory, which `of` does, because asking how
        much a job kept must not leave a directory behind for a job that never wrote one. Reads the
        disk, so a caller on the event loop calls it off the loop.

        What a paused download says it has kept after a restart. The figure the fetch reported
        lived in memory and went with the process; the bytes did not, and they are the fact the
        figure was only ever a report of. Links are counted as themselves rather than followed:
        the same lexical caution as `_confine`, for a module that also deletes, and a file that
        vanishes between the listing and the stat is simply not counted.
        """
        try:
            path = self._confine(job_id)
        except ValueError:
            return None
        if not path.is_dir():
            return None
        total = 0
        for folder, _folders, names in os.walk(path, followlinks=False):
            for name in names:
                try:
                    total += os.lstat(os.path.join(folder, name)).st_size
                except OSError:
                    continue
        return total

    def sweep(self, job_id: str) -> None:
        """Remove a job's directory and everything in it. Quiet when there is nothing there.

        Never raises. It is called on the way out of a job that has already been recorded, and an
        outcome that is written down must not be undone by a file that would not delete: a
        directory left behind costs disk space, and an exception here costs the worker.
        """
        path = self._confine(job_id)
        if not path.exists():
            return
        # Sift's own directory, under Sift's own cache, named after a job the queue minted and
        # confined to the root by `_confine`. Nothing anybody else put anywhere is reachable from
        # here, which is what the rule below is about.
        # nosemgrep: sift-no-file-removal-outside-delete-trash
        shutil.rmtree(path, ignore_errors=True)
        log.info("job.workspace.swept", job_id=job_id)

    def sweep_all_but(self, keep: Iterable[str]) -> int:
        """Remove every workspace whose job is not in `keep`. Returns how many went.

        The other half of the sweep above, and it exists because a job can end while nothing is
        running it: a paused download that somebody then cancels, or a crash between the work and
        the record of it. Run at boot, when the answer to "is this job still going" is a single
        read and there is nobody to race.
        """
        if not self._root.exists():
            return 0
        living = set(keep)
        gone = 0
        for entry in sorted(self._root.iterdir()):
            if entry.is_dir() and entry.name not in living:
                # As above: a directory this module made, under its own root.
                # nosemgrep: sift-no-file-removal-outside-delete-trash
                shutil.rmtree(entry, ignore_errors=True)
                gone += 1
        if gone:
            log.info("job.workspaces.swept", removed=gone)
        return gone

    def _confine(self, job_id: str) -> Path:
        """The path for an id, refusing anything that would not land directly under the root.

        Compared lexically rather than resolved. `Path.resolve()` follows a link, so a name that
        resolved to somewhere harmless could still BE a link out of the root by the time the sweep
        followed it, and this is a module whose job is to delete things.
        """
        path = self._root / job_id
        parent = os.path.abspath(self._root)
        here = os.path.abspath(path)
        if os.path.dirname(here) != parent or not job_id or job_id in (".", ".."):
            raise ValueError(f"{job_id!r} is not a job id a workspace can be named after")
        return path
