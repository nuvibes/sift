# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a job keeps what it has half-written, kept until the job ends so a pause loses nothing."""

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
        """This job's directory, made if needed; the id is checked so nothing lands outside root."""
        path = self._confine(job_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def size_of(self, job_id: str) -> int | None:
        """Bytes in this job's directory, or None without one; a read that never makes it."""
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
        """Remove a job's directory; never raises, as the job's outcome is already recorded."""
        path = self._confine(job_id)
        if not path.exists():
            return
        # Sift's own directory, confined to the root by `_confine`.
        # nosemgrep: sift-no-file-removal-outside-delete-trash
        shutil.rmtree(path, ignore_errors=True)
        log.info("job.workspace.swept", job_id=job_id)

    def sweep_all_but(self, keep: Iterable[str]) -> int:
        """Remove every workspace whose job is not in `keep`, at boot; returns how many went."""
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
        """The path for an id directly under the root, compared lexically so no link is followed."""
        path = self._root / job_id
        parent = os.path.abspath(self._root)
        here = os.path.abspath(path)
        if os.path.dirname(here) != parent or not job_id or job_id in (".", ".."):
            raise ValueError(f"{job_id!r} is not a job id a workspace can be named after")
        return path
