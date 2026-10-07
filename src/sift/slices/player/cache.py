# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rolling segment cache: bounded, least-recently-used, and never over its cap.

This is the piece that makes on-the-fly transcoding safe to ship. Transcoding only the seconds
someone actually watches means the work is small, but it also means a long browsing session
produces a steady trickle of files that nothing would otherwise ever delete. Six hours of
scrolling through incompatible clips is thousands of segments. Unbounded, that fills the disk.

So every segment is written into one directory under a byte cap, and the least recently used ones
are deleted to make room. The invariant is one sentence and it is absolute:

    **The cache never holds more bytes than its cap.**

Not "usually", and not "shortly after a sweep". A cache that exceeds its cap at 3am on a machine
whose disk is also holding the person's library is not a performance problem, it is data loss, and
the cap is a promise rather than a target. There is a property test beside this file that asserts
the invariant over thousands of generated access sequences rather than a handful of chosen ones,
because "for every possible sequence" is exactly the claim that hand-picked examples cannot make.

## The oversized-segment guard

The obvious policy (*evict the oldest until the new one fits*) has a hole in it that reading
the code does not show. When a single segment is larger than the **entire cap**, the eviction loop
runs out of things to delete, exits having emptied the cache, and then inserts the oversized
segment anyway. The cache is then permanently over its limit *and* it threw away everything useful
on the way: an 81 MiB segment against a 40 MiB cap leaves the cache at 2x its bound.

That is not a hypothetical shape. A 4K/120 segment is about 81 MiB at 4 seconds and 123 MiB at 6.

The guard is one rule: **a segment that would not fit in an empty cache is never admitted.** It is
still served (the person watching gets their video); it is simply not kept afterwards. Serving
and caching are different decisions, and conflating them is what creates the bug.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from sift.kernel.log import get_logger
from sift.kernel.wiring import Part

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Entry:
    """One cached segment."""

    key: str
    size_bytes: int


class SegmentCache:
    """A least-recently-used cache of transcoded segments, bounded by total size on disk.

    Ordinary use is `path_for` to find where a segment belongs, then `admit` once it has been
    written. `touch` marks a hit so that something being watched is not evicted out from under the
    player.

    Not thread-safe and not task-safe on its own: every caller runs inside the single-slot
    transcode lock, which is what serialises it. Guarding it again here would be a second lock
    protecting the same thing, and two locks around one invariant is how deadlocks are made.
    """

    def __init__(self, directory: Path, *, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError("a cache cap must be a positive number of bytes")
        self._directory = directory
        self._max_bytes = max_bytes
        # Insertion-ordered, oldest first. `move_to_end` on a hit is what makes it an LRU rather
        # than a first-in-first-out queue: the distinction matters when a person rewatches the
        # segment they are sitting on.
        self._entries: OrderedDict[str, Entry] = OrderedDict()
        self._total_bytes = 0

    # --- what the cache knows -----------------------------------------------------------------

    @property
    def total_bytes(self) -> int:
        """Bytes currently held. Never greater than `max_bytes`: that is the whole invariant."""
        return self._total_bytes

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def __contains__(self, key: str) -> bool:
        return key in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def keys(self) -> tuple[str, ...]:
        """Every key held, least recently used first. The eviction order, made visible."""
        return tuple(self._entries)

    def path_for(self, key: str) -> Path:
        """Where a segment with this key lives, whether or not it is there yet."""
        return self._directory / key

    # --- using it -----------------------------------------------------------------------------

    def touch(self, key: str) -> Path | None:
        """Mark a segment as just used and return its path, or None if it is not held.

        A miss is ordinary (it means the segment was evicted or never made), and the caller
        answers it by transcoding again. A revisit after eviction costs about what the cold first
        transcode did: no penalty beyond redoing the work.
        """
        entry = self._entries.get(key)
        if entry is None:
            return None
        path = self.path_for(key)
        if not path.exists():
            # The file went away underneath us: a sweep, an operator with a shell, a full disk
            # mid-write. The bookkeeping is what has to stay honest, so drop the row and report a
            # miss rather than handing back a path to nothing.
            self._forget(key)
            return None
        self._entries.move_to_end(key)
        return path

    def admit(self, key: str, size_bytes: int) -> bool:
        """Take responsibility for a segment already written to disk. True if it was kept.

        False means the segment is too big to ever be cached and has been left where it is for the
        caller to serve and then delete. It is not an error and not a failure to transcode.
        """
        if size_bytes < 0:
            raise ValueError("a segment cannot have a negative size")

        # Whatever was at this key is gone: there is one path per key, and the caller has
        # already written the new segment over it. So the old accounting is dropped here,
        # unconditionally, *before* the guard below can return early.
        #
        # Doing this after the guard is a real bug and a subtle one: an oversized re-write would
        # be refused, the caller would delete the file it had just written, and the cache would go
        # on believing it still held the old segment at the old size. Its books would say bytes it
        # does not have, and every later admission would evict against a total that was too high.
        # The property test beside this file is what holds it.
        if key in self._entries:
            self._forget(key)

        # The guard. Checked *before* anything is evicted, which is the entire point: the naive
        # policy discovers the problem halfway through emptying the cache, having already thrown
        # away segments to make room it was never going to be able to make.
        if size_bytes > self._max_bytes:
            log.info(
                "player.segment_too_large_to_cache",
                key=key,
                size_bytes=size_bytes,
                max_bytes=self._max_bytes,
            )
            return False

        self._evict_until_room_for(size_bytes)
        self._entries[key] = Entry(key=key, size_bytes=size_bytes)
        self._total_bytes += size_bytes
        return True

    def reload(self) -> int:
        """Take responsibility for what is already in the directory, oldest first, under the cap.

        Books that started empty at every boot would leave everything written before it invisible
        to the cap for ever, and the tidy-up deliberately leaves this directory alone because
        sweeping it would take segments out from under somebody watching. Read once at boot, off
        the loop, and admitted in the order they were made so that the newest survive when the cap
        is already full. The scratch
        of a transcode in flight (`.build-`) is left where it is. Returns how many were kept.
        """
        try:
            entries = list(os.scandir(self._directory))
        except OSError:
            return 0
        found: list[tuple[float, str, int]] = []
        for entry in entries:
            try:
                if not entry.is_file(follow_symlinks=False) or entry.name.startswith(".build-"):
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            found.append((stat.st_mtime, entry.name, stat.st_size))
        for _, key, size in sorted(found):
            if not self.admit(key, size):
                _unlink(self.path_for(key))
        kept = len(self._entries)
        log.info(
            "player.segment_cache_reloaded",
            found=len(found),
            kept=kept,
            bytes=self._total_bytes,
            max_bytes=self._max_bytes,
        )
        return kept

    def discard(self, key: str) -> None:
        """Forget a segment and delete it. Silent if it was not held."""
        if key in self._entries:
            self._forget(key)
        _unlink(self.path_for(key))

    def discard_asset(self, asset_id: str) -> int:
        """Throw away every piece held for one file. Returns how many went.

        For a file that has ended. Nothing in the database points at a segment, so these are never
        asked for again and would sit here until the cap happened to evict them, which depends on
        how much somebody watches afterwards, not on anything about the file that was deleted.

        Matching on the key's leading id is exact rather than nearly exact, and it is worth saying
        why a prefix match is safe here of all places. Every id is a ULID: twenty-six characters
        from an alphabet that has no dash in it, and always twenty-six. So no id can be another id
        followed by a dash, and the separator cannot appear inside one.

        The half-written scratch file (`.build-`) is deliberately left alone. It belongs to a
        transcode that is running right now, and taking it out from under one turns a delete into a
        failure on a video somebody else is watching.
        """
        prefix = f"{asset_id}-"
        held = [key for key in self._entries if key.startswith(prefix)]
        for key in held:
            self.discard(key)
        if held:
            log.info("player.segments_discarded", asset_id=asset_id, segments=len(held))
        return len(held)

    def resize(self, max_bytes: int) -> bool:
        """Move the cap, evicting down to it immediately. True when the cap actually moved.

        EVICTING HERE RATHER THAN WAITING is the whole of it. Left to the next admission, a cap
        lowered from forty gigabytes to four would take effect only when something new was played,
        so somebody who had just asked Sift to use less disk would watch it go on using the old
        amount for as long as they did not watch anything, which is exactly when they are looking
        at the folder.

        Called from the same single-slot transcode lock every other method here is called under.
        """
        if max_bytes <= 0:
            raise ValueError("a cache cap must be a positive number of bytes")
        if max_bytes == self._max_bytes:
            return False
        self._max_bytes = max_bytes
        # Room for nothing, which asks the loop to bring the total under the cap and stop.
        self._evict_until_room_for(0)
        return True

    # --- eviction -----------------------------------------------------------------------------

    def _evict_until_room_for(self, size_bytes: int) -> None:
        """Delete least-recently-used segments until `size_bytes` more will fit under the cap.

        Terminates because every pass removes one entry and reduces the total by that entry's
        size, and the caller has already established that the cap alone is enough room, so the
        empty cache is always a sufficient outcome and the loop cannot run out of victims first.
        """
        while self._entries and self._total_bytes + size_bytes > self._max_bytes:
            oldest = next(iter(self._entries))
            _unlink(self.path_for(oldest))
            self._forget(oldest)
            log.debug("player.segment_evicted", key=oldest)

    def _forget(self, key: str) -> None:
        entry = self._entries.pop(key)
        self._total_bytes -= entry.size_bytes


def _unlink(path: Path) -> None:
    """Delete a file, tolerating one that is already gone.

    `missing_ok` rather than a prior `exists()` check: between the check and the unlink the file
    can go, and a cache that raises because something it was about to delete was already deleted
    is a cache that falls over during exactly the cleanup it exists to do.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        # A segment that cannot be removed is a leak, not a crash. Losing the whole playback
        # because one temporary file is stubborn is the worse of the two outcomes, so this is
        # recorded and stepped over: the size accounting has already dropped it either way.
        log.warning("player.segment_unlink_failed", path=str(path), error=str(error))


def size_on_disk(path: Path) -> int:
    """How many bytes a just-written segment takes up, or 0 if it is not there."""
    try:
        return os.stat(path).st_size
    except OSError:
        return 0


#: The transcoded segments, bounded by a byte cap. Published as well as held inside the service
#: because the eviction it does is the kind of thing only an end-to-end test can watch.
SEGMENT_CACHE: Part[SegmentCache] = Part("segment_cache")
