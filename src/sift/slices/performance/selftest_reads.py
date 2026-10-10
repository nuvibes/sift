# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the self-test reads of a storage: a file's places, a seek, a whole read, the walk's sort."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sift.kernel.lanes import MAX_READS_AT_ONCE
from sift.slices.performance import uncached
from sift.slices.performance.clip import REPEATS

#: How many places in a file one reader seeks to, and how much it reads at each: about what a
#: decoder pulls to rebuild one frame from the keyframe before it.
SEEKS_PER_FILE = 6
BYTES_PER_SEEK = 1 << 20

#: The smallest file worth seeking into. Below this the six reads overlap and measure the cache.
SAMPLE_FLOOR_BYTES = 16 << 20

#: The small files read whole, a picture's size: above a page, below the seek sample's floor.
SMALL_FLOOR_BYTES = 64 << 10

#: The readers-at-once levels, doubling up to the most the lanes allow one storage.
STORAGE_LEVELS = tuple(1 << step for step in range(MAX_READS_AT_ONCE.bit_length()))

#: One salt per level per repeat, the midpoint and a busy level's second take included, so no run
#: re-reads a place another left in the system's cache.
_SALTS = 2 * (len(STORAGE_LEVELS) + 1) * REPEATS

#: Why a local disk is not measured where the system cannot read past its cache: a read from
#: memory is not a read from the disk.
NO_UNCACHED = "this system offers no way to read a disk past its own cache"


@dataclass(frozen=True)
class StorageToMeasure:
    """One storage and the library folders on it, as the composition root hands them in."""

    storage: str
    label: str
    remote: bool
    roots: tuple[Path, ...]


@dataclass(frozen=True)
class Walked:
    """What a bounded walk of a storage's folders found, and how quickly it listed them."""

    large: list[Path]
    small: list[Path]
    entries: int
    seconds: float

    @property
    def listed_per_second(self) -> float | None:
        return self.entries / self.seconds if self.entries and self.seconds > 0 else None


def _sort_entry(
    entry: os.DirEntry[str], pending: list[Path], seen: list[tuple[int, Path]], small: list[Path]
) -> None:
    """One entry of the walk: a folder to walk, a large file kept with its size, or a small one."""
    if entry.is_dir(follow_symlinks=False):
        pending.append(Path(entry.path))
    elif entry.is_file(follow_symlinks=False):
        size = entry.stat(follow_symlinks=False).st_size
        if size >= SAMPLE_FLOOR_BYTES:
            seen.append((size, Path(entry.path)))
        elif size >= SMALL_FLOOR_BYTES:
            small.append(Path(entry.path))


def _places(size: int, salt: int) -> list[int]:
    """Where in a file of `size` bytes one reader seeks, moved by `salt` so runs never overlap."""
    span = max(1, size - BYTES_PER_SEEK)
    within = (salt % _SALTS + 0.5) / _SALTS
    places = (
        int(((at + within) % SEEKS_PER_FILE) / SEEKS_PER_FILE * span)
        for at in range(SEEKS_PER_FILE)
    )
    return [place - place % uncached.ALIGN for place in places]


def _seek_and_read(path: Path, *, salt: int, uncached: bool = False) -> int:
    """Read `BYTES_PER_SEEK` at each of a file's places. How many bytes came back."""
    if uncached:
        return _read_uncached(path, salt)
    read = 0
    with open(path, "rb", buffering=0) as handle:
        for place in _places(os.fstat(handle.fileno()).st_size, salt):
            handle.seek(place)
            read += len(handle.read(BYTES_PER_SEEK))
    return read


def _read_uncached(path: Path, salt: int) -> int:
    return uncached.read(path, _places(path.stat().st_size, salt), BYTES_PER_SEEK)


def _whole_uncached(path: Path) -> int:
    size = path.stat().st_size
    return min(size, uncached.read(path, [0], -(-size // uncached.ALIGN) * uncached.ALIGN))


def _read_whole(path: Path, *, salt: int = 0, uncached: bool = False) -> int:
    """Read a small file start to end, as a scan's copy reads a picture. How many bytes came back."""
    del salt
    if uncached:
        return _whole_uncached(path)
    read = 0
    with open(path, "rb", buffering=0) as handle:
        while chunk := handle.read(BYTES_PER_SEEK):
            read += len(chunk)
    return read
