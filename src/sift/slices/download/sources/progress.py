# SPDX-License-Identifier: AGPL-3.0-or-later
"""How far along a download is, reported by the seam that can see it.

Rate and time left are worked out here only; nothing is stored."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now
from sift.kernel.wiring import Part
from sift.slices.download.sources.argv import PROGRESS_MARKER

#: The screen asks once a second; reporting every chunk is work nobody reads.
REPORT_EVERY_SECONDS = 0.25


@dataclass(frozen=True, slots=True)
class Progress:
    """How far along one download is; absent, never zero, when not known."""

    done_bytes: int = 0
    total_bytes: int | None = None
    #: A fragmented video's size is revised as it goes, so the screen says "about".
    total_is_estimated: bool = False
    done_files: int = 0
    total_files: int | None = None
    bytes_per_second: float | None = None
    seconds_left: float | None = None

    @property
    def fraction(self) -> float | None:
        """How far along, 0 to 1: by files when there are several, else by bytes."""
        if self.total_files and self.total_files > 1:
            return min(1.0, self.done_files / self.total_files)
        if self.total_bytes:
            return min(1.0, self.done_bytes / self.total_bytes)
        if self.total_files:
            return min(1.0, self.done_files / self.total_files)
        return None


Report = Callable[[Progress], None]


def nowhere(_progress: Progress) -> None:
    """The reporter for a fetch nobody is watching; not None, so a seam never checks per chunk."""


@dataclass
class Watcher:
    """One download's progress, re-anchored when the work restarts so the rate stays honest."""

    #: Monotonic: a clock that goes backwards gives negative throughput.
    clock: Callable[[], float] = time.monotonic
    latest: Progress = field(default_factory=Progress)
    _anchor_at: float = 0.0
    _anchor_bytes: int = 0
    _started: bool = False

    def update(self, reading: Progress) -> Progress:
        """Take a reading, work out the rate and the time left, and keep it as the latest."""
        now = self.clock()
        if not self._started or reading.done_bytes < self._anchor_bytes:
            # The work restarted: measuring from before would report a rate never run at.
            self._anchor_at = now
            self._anchor_bytes = reading.done_bytes
            self._started = True

        elapsed = now - self._anchor_at
        moved = reading.done_bytes - self._anchor_bytes
        rate = moved / elapsed if elapsed > 0 and moved > 0 else None

        left: float | None = None
        if rate and reading.total_bytes:
            remaining = reading.total_bytes - reading.done_bytes
            left = remaining / rate if remaining > 0 else 0.0

        self.latest = Progress(
            done_bytes=reading.done_bytes,
            total_bytes=reading.total_bytes,
            done_files=reading.done_files,
            total_files=reading.total_files,
            bytes_per_second=rate,
            seconds_left=left,
        )
        return self.latest


class Registry:
    """What is in flight right now, by download, in memory only."""

    def __init__(self) -> None:
        self._watching: dict[str, Watcher] = {}

    def reporter(self, download_id: str) -> Report:
        """A reporter for one download. Handed to the seams; they know nothing else about this."""
        watcher = self._watching.setdefault(download_id, Watcher())

        def report(reading: Progress) -> None:
            watcher.update(reading)
            # Announced here: the figure is never stored, so no commit would announce it.
            announce_now(EVERY_ADMIN, About.DOWNLOADS)

        return report

    def of(self, download_id: str) -> Progress | None:
        """How far along one download is, or None when it is not running."""
        watcher = self._watching.get(download_id)
        return watcher.latest if watcher is not None else None

    def all(self) -> dict[str, Progress]:
        """Everything in flight, in one read for the strip above the list."""
        return {download_id: watcher.latest for download_id, watcher in self._watching.items()}

    def hold(self, download_id: str) -> None:
        """Keep a paused download's figures and drop its rate, which would read as still moving."""
        watcher = self._watching.get(download_id)
        if watcher is not None:
            watcher.latest = replace(watcher.latest, bytes_per_second=None, seconds_left=None)

    def forget(self, download_id: str) -> None:
        """Drop a settled download, or this grows for as long as the process runs."""
        self._watching.pop(download_id, None)


#: The marker is Sift's own, so no shape is guessed; `NA` reads as not known.
_LINE = re.compile(rf"^{re.escape(PROGRESS_MARKER)}\s+(\S+)\s+(\S+)\s+(\S+)$")


def read_tool_line(line: str) -> Progress | None:
    """One progress line from the video tool, or None if this line is not one."""
    found = _LINE.match(line.strip())
    if found is None:
        return None
    done, total, estimate = (_number(part) for part in found.groups())
    if done is None:
        return None
    return Progress(
        done_bytes=done,
        total_bytes=total if total is not None else estimate,
        total_is_estimated=total is None and estimate is not None,
    )


def _number(text: str) -> int | None:
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


_PLAYLIST_ITEM = re.compile(r"^\[download\]\s+Downloading item (\d+) of (\d+)\b", re.IGNORECASE)


def read_playlist_line(line: str) -> tuple[int, int] | None:
    """Which item of how many, or None if this line is not that."""
    found = _PLAYLIST_ITEM.match(line.strip())
    if found is None:
        return None
    return int(found.group(1)), int(found.group(2))


_GALLERY_FILE = re.compile(r"^(?:# )?/\S+")


def looks_like_a_finished_file(line: str) -> bool:
    """Whether a line from the gallery tool means one more file has landed."""
    return bool(_GALLERY_FILE.match(line.strip()))


__all__ = [
    "REPORT_EVERY_SECONDS",
    "Progress",
    "Registry",
    "Report",
    "Watcher",
    "looks_like_a_finished_file",
    "nowhere",
    "read_playlist_line",
    "read_tool_line",
]


PROGRESS: Part[Registry] = Part("download_progress")
