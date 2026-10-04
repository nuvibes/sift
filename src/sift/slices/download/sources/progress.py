# SPDX-License-Identifier: AGPL-3.0-or-later
"""How far along a download is: one shape, reported by whichever side can see it.

**Progress is a fact the fetching side knows, and it is read from there rather than guessed at from
somewhere else.** The tempting shortcut is to watch the staging directory grow, since the disk guard
already looks at it, and it is wrong in a way that shows up on screen. A video tool downloads
picture and sound as separate part-files and then merges them, so the directory grows past the
finished size and then shrinks: a bar that runs past the end and falls back. It can also never learn
a total, so the sites people most want a bar for would never have one.

So each seam reports, because each seam is the only side that can:

* the two direct fetchers stream the bytes themselves and are told the total by the response;
* the video tool is asked for a progress line in a shape **Sift specifies** (which is a declared
  interface, not chatter being scraped), and that line carries the real total;
* the gallery tool has no such option, so it reports FILES rather than bytes. That is honest and, on
  a gallery of four hundred images, more useful than a byte count would have been.

**Throughput and time remaining are worked out here and nowhere else**, from what was reported. Four
seams each doing their own arithmetic would be four subtly different answers to "how fast is this".

The estimate is linear from the start of the current stage and re-anchored whenever the stage
changes, rather than a rolling average, which whipsaws on parallel work.

None of this is stored. A restart has no downloads in flight (whatever was running is requeued),
so a row in a table would only ever be a stale number that outlived the thing it described.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now
from sift.kernel.wiring import Part
from sift.slices.download.sources.argv import PROGRESS_MARKER

#: How often a seam that could report continuously actually does. A direct transfer moves megabytes
#: a second and the screen asks once a second, so telling the sink about every chunk would be work
#: nobody reads.
REPORT_EVERY_SECONDS = 0.25


@dataclass(frozen=True, slots=True)
class Progress:
    """How far along one download is, as everything that reads it sees it.

    Every field is optional-by-absence rather than by a zero, because "not known" and "none yet" are
    genuinely different and a screen shows them differently: a total nobody knows means no bar at
    all, and a total of zero would mean a bar stuck at the end.
    """

    #: Bytes fetched so far. Always known: something has either moved or it has not.
    done_bytes: int = 0
    #: What the whole thing is, if the site said. A fragmented download reports an estimate until
    #: its last piece, which is far better than no bar.
    total_bytes: int | None = None
    #: Whether that total is a GUESS rather than a figure the site gave.
    #:
    #: A video served in fragments has no declared size, so the tool works one out from the bitrate
    #: it has seen and revises it as it goes, which on screen is a final size that changes every
    #: few seconds, and reads as a fault rather than as an estimate. The number is honest; printing
    #: it as though it were certain is what was not.
    total_is_estimated: bool = False
    #: Files finished, for the fetches that are many small things rather than one large one.
    done_files: int = 0
    #: How many there are, where that was known before starting.
    total_files: int | None = None
    #: Bytes a second, worked out here from what was reported.
    bytes_per_second: float | None = None
    #: Seconds left at the current rate, or None when there is no total to measure against.
    seconds_left: float | None = None

    @property
    def fraction(self) -> float | None:
        """How far along, from 0 to 1, or None when nothing here can say.

        Files first when there is more than one, because the bytes then belong to whichever item is
        being fetched right now: a ten-video playlist reported by bytes runs to the end and starts
        again ten times, and the bar says nothing about how much of the paste is left. For a single
        file it is the other way round (bytes move continuously and a file count of one is either
        0 or 1), so bytes answer, and a gallery with no byte total still answers from its files.
        """
        if self.total_files and self.total_files > 1:
            return min(1.0, self.done_files / self.total_files)
        if self.total_bytes:
            return min(1.0, self.done_bytes / self.total_bytes)
        if self.total_files:
            return min(1.0, self.done_files / self.total_files)
        return None


#: What a seam calls to say how far it has got. Handed down to the seam; the seam never reaches for
#: a registry, and a test passes one that simply records.
Report = Callable[[Progress], None]


def nowhere(_progress: Progress) -> None:
    """The reporter for a fetch nobody is watching: a test, a direct call. Deliberately not None:
    a seam that had to check for absence before every report would check on every chunk."""


@dataclass
class Watcher:
    """One download's progress, and the arithmetic nobody else has to repeat.

    Holds the last reading and the anchor the estimate is measured from. Re-anchored when the shape
    of the work changes, because the rate before a change says nothing about the rate after it: a
    listing finishing and a transfer starting are not the same activity measured twice.
    """

    #: Where time comes from. A parameter so a test can drive it, and monotonic in real use because
    #: a clock that can go backwards produces negative throughput.
    clock: Callable[[], float] = time.monotonic
    latest: Progress = field(default_factory=Progress)
    _anchor_at: float = 0.0
    _anchor_bytes: int = 0
    _started: bool = False

    def update(self, reading: Progress) -> Progress:
        """Take a reading, work out the rate and the time left, and keep it as the latest.

        Returns what it stored, so a caller can hand the same object straight on.
        """
        now = self.clock()
        if not self._started or reading.done_bytes < self._anchor_bytes:
            # First reading, or the work restarted: a retry, or the next item of several. Measuring
            # from before that would divide a fresh start's bytes by the whole elapsed time and
            # report a rate the transfer never ran at.
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
    """What is in flight right now, by download. Held on the application, never written down.

    In memory on purpose. Progress describes a transfer that is happening; after a restart none is,
    because anything that was running is requeued, so a stored figure could only ever be a number
    that outlived the thing it described. It is also written several times a second per download,
    which is not a thing to do to a database for a value nobody will read afterwards.
    """

    def __init__(self) -> None:
        self._watching: dict[str, Watcher] = {}

    def reporter(self, download_id: str) -> Report:
        """A reporter for one download. Handed to the seams; they know nothing else about this."""
        watcher = self._watching.setdefault(download_id, Watcher())

        def report(reading: Progress) -> None:
            watcher.update(reading)
            # And the screen watching this download is told to ask again. Said here rather than
            # after a write, because there is no write: this figure is never stored, so there is no
            # commit to hang a message on and nothing would ever announce it. The seam reports
            # several times a second and a connection sends at most once, so what reaches a screen
            # is one re-read a second while something is moving and nothing at all when it is not.
            announce_now(EVERY_ADMIN, About.DOWNLOADS)

        return report

    def of(self, download_id: str) -> Progress | None:
        """How far along one download is, or None when it is not running."""
        watcher = self._watching.get(download_id)
        return watcher.latest if watcher is not None else None

    def all(self) -> dict[str, Progress]:
        """Everything in flight, for the strip above the list that says how the queue as a whole is
        doing. One read rather than one per row: the aggregate is the figure that updates fastest and
        it must not cost a lookup per download to produce."""
        return {download_id: watcher.latest for download_id, watcher in self._watching.items()}

    def hold(self, download_id: str) -> None:
        """Keep a paused download's figures and drop its rate.

        What is on disk is still true and the row says "8.1 of 226 MB kept" from it; the speed and
        the time left are about a fetch that has stopped, and summed into the strip above the queue
        they would read as "0 downloading, 7.3 MB/s, about 21s left"."""
        watcher = self._watching.get(download_id)
        if watcher is not None:
            watcher.latest = replace(watcher.latest, bytes_per_second=None, seconds_left=None)

    def forget(self, download_id: str) -> None:
        """Drop a finished download. Called when it settles, whatever it settled as. Otherwise
        this grows by one entry per download for as long as the process runs."""
        self._watching.pop(download_id, None)


#: The progress line the video tool is asked for, as it comes back. The marker is Sift's own, chosen
#: in the command it was asked with, so a line either begins with it or is not a progress line: no
#: shape has to be guessed at. Unknown fields arrive as `NA`, which is what the tool prints and is
#: read as "not known" rather than as a number.
_LINE = re.compile(rf"^{re.escape(PROGRESS_MARKER)}\s+(\S+)\s+(\S+)\s+(\S+)$")


def read_tool_line(line: str) -> Progress | None:
    """One progress line from the video tool, or None if this line is not one.

    Most of what a tool prints is not progress, and the honest answer for those is nothing at all.
    """
    found = _LINE.match(line.strip())
    if found is None:
        return None
    done, total, estimate = (_number(part) for part in found.groups())
    if done is None:
        return None
    # The real total when the tool knows it, and its estimate until then. A fragmented download only
    # learns its true size as it finishes, and an approximate bar for four minutes beats none, but
    # which of the two it is travels with it, so a screen can say "about" rather than implying the
    # site declared a size that keeps changing.
    return Progress(
        done_bytes=done,
        total_bytes=total if total is not None else estimate,
        total_is_estimated=total is None and estimate is not None,
    )


def _number(text: str) -> int | None:
    """A field from the progress line as a count of bytes, or None for one the tool did not know."""
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


#: What the video tool prints when a link turns out to hold more than one thing: a playlist, a
#: channel, an album. It arrives before the bytes of each item, so a row can say "3 of 10 files"
#: for the whole paste instead of restarting a bar at zero ten times with nothing to say why.
_PLAYLIST_ITEM = re.compile(r"^\[download\]\s+Downloading item (\d+) of (\d+)\b", re.IGNORECASE)


def read_playlist_line(line: str) -> tuple[int, int] | None:
    """Which item of how many, or None if this line is not that."""
    found = _PLAYLIST_ITEM.match(line.strip())
    if found is None:
        return None
    return int(found.group(1)), int(found.group(2))


#: What the gallery tool prints as it finishes each file: a path, on its own line. It has no progress
#: option to ask for, so what it can report is which file it is on, and on a four hundred image
#: gallery that is the more useful number anyway.
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


#: What is in flight right now, held in memory and never written down.
PROGRESS: Part[Registry] = Part("download_progress")
