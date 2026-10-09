# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rolling segment cache: bounded, least-recently-used, and never over its cap.

A segment that would not fit in an empty cache is served but never admitted.
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

    Not task-safe on its own: every caller runs inside the single-slot transcode lock.
    """

    def __init__(self, directory: Path, *, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError("a cache cap must be a positive number of bytes")
        self._directory = directory
        self._max_bytes = max_bytes
        # Oldest first; `move_to_end` on a hit makes it an LRU.
        self._entries: OrderedDict[str, Entry] = OrderedDict()
        self._total_bytes = 0

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

    def touch(self, key: str) -> Path | None:
        """Mark a segment as just used and return its path, or None if it is not held."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        path = self.path_for(key)
        if not path.exists():
            # The file went away underneath us: keep the books honest and report a miss.
            self._forget(key)
            return None
        self._entries.move_to_end(key)
        return path

    def admit(self, key: str, size_bytes: int) -> bool:
        """Take responsibility for a segment already on disk; False when too big ever to keep."""
        if size_bytes < 0:
            raise ValueError("a segment cannot have a negative size")

        # The old accounting goes before the guard can return, or the books keep a deleted segment.
        if key in self._entries:
            self._forget(key)

        # The guard, before anything is evicted.
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
        """Take over what is already in the directory at boot, oldest first, under the cap.

        The scratch of a transcode in flight (`.build-`) is left alone. Returns how many were kept.
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
        """Throw away every piece held for one file, but a transcode's scratch. Returns how many.

        A prefix match is exact: ids are fixed-length ULIDs with no dash.
        """
        prefix = f"{asset_id}-"
        held = [key for key in self._entries if key.startswith(prefix)]
        for key in held:
            self.discard(key)
        if held:
            log.info("player.segments_discarded", asset_id=asset_id, segments=len(held))
        return len(held)

    def resize(self, max_bytes: int) -> bool:
        """Move the cap, evicting down to it immediately. True when the cap actually moved."""
        if max_bytes <= 0:
            raise ValueError("a cache cap must be a positive number of bytes")
        if max_bytes == self._max_bytes:
            return False
        self._max_bytes = max_bytes
        # Room for nothing, which asks the loop to bring the total under the cap and stop.
        self._evict_until_room_for(0)
        return True

    def _evict_until_room_for(self, size_bytes: int) -> None:
        """Delete least-recently-used segments until `size_bytes` more will fit under the cap."""
        while self._entries and self._total_bytes + size_bytes > self._max_bytes:
            oldest = next(iter(self._entries))
            _unlink(self.path_for(oldest))
            self._forget(oldest)
            log.debug("player.segment_evicted", key=oldest)

    def _forget(self, key: str) -> None:
        entry = self._entries.pop(key)
        self._total_bytes -= entry.size_bytes


def _unlink(path: Path) -> None:
    """Delete a file, tolerating one that is already gone."""
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        # A stubborn file is a leak, not a crash: the accounting has already dropped it.
        log.warning("player.segment_unlink_failed", path=str(path), error=str(error))


def size_on_disk(path: Path) -> int:
    """How many bytes a just-written segment takes up, or 0 if it is not there."""
    try:
        return os.stat(path).st_size
    except OSError:
        return 0


#: The transcoded segments; published so an end-to-end test can watch eviction.
SEGMENT_CACHE: Part[SegmentCache] = Part("segment_cache")
