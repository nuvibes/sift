# SPDX-License-Identifier: AGPL-3.0-or-later
"""A photograph's turn, read where the file is first read and honoured by every picture made of it.

A camera stores the picture the way the sensor saw it and writes a note (the EXIF orientation)
saying how to turn it. ffmpeg turns every picture it draws by that note, so the size recorded has to
be the size after the turn: the wall's tile, the orientation filter and the editor all measure the
picture a person sees.

The picture here is white over black, so where the white ends up says which way it was turned.
"""

from __future__ import annotations

import struct
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from sift.kernel.config import Settings
from sift.kernel.content import ContentStore
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import JobContext
from sift.slices.media_jobs import jobs
from sift.slices.media_jobs.tests.conftest import draw, take_in
from sift.testing.fixtures import LibraryRoot

pytestmark = [pytest.mark.integration]

Context = Callable[[str, dict[str, object]], Awaitable[JobContext]]

#: White over black, 64 wide and 32 tall as stored.
_HALVES = "color=c=white:s=64x16[top];color=c=black:s=64x16[bottom];[top][bottom]vstack"


def _noted(path: Path, orientation: int) -> Path:
    """The same JPEG with one Exif block, holding only this orientation, straight after SOI."""
    entry = struct.pack(">HHIHH", 0x0112, 3, 1, orientation, 0)
    tiff = b"MM\x00*" + struct.pack(">I", 8) + struct.pack(">H", 1) + entry + struct.pack(">I", 0)
    body = b"Exif\x00\x00" + tiff
    data = path.read_bytes()
    assert data[:2] == b"\xff\xd8", "not a JPEG"
    path.write_bytes(data[:2] + b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body + data[2:])
    return path


def _grey(path: Path, width: int, height: int) -> list[list[int]]:
    """The picture's brightness, row by row, as ffmpeg decodes it."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
        capture_output=True,
        check=True,
    )  # fmt: skip
    pixels = result.stdout
    assert len(pixels) == width * height, "the tile is not the size that was recorded"
    return [list(pixels[row * width : (row + 1) * width]) for row in range(height)]


def _white_side(rows: list[list[int]]) -> str:
    """Which edge the white half sits against."""
    height, width = len(rows), len(rows[0])

    def mean(cells: list[int]) -> float:
        return sum(cells) / len(cells)

    sides = {
        "top": mean([v for row in rows[: height // 4] for v in row]),
        "bottom": mean([v for row in rows[-(height // 4) :] for v in row]),
        "left": mean([v for row in rows for v in row[: width // 4]]),
        "right": mean([v for row in rows for v in row[-(width // 4) :]]),
    }
    return max(sides, key=lambda side: sides[side])


@pytest.mark.parametrize(
    ("orientation", "size", "white"),
    [
        (1, (64, 32), "top"),
        (3, (64, 32), "bottom"),
        (6, (32, 64), "right"),
        (8, (32, 64), "left"),
    ],
)
async def test_the_size_and_the_tile_follow_the_note(
    orientation: int,
    size: tuple[int, int],
    white: str,
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """1 is upright, 3 a half turn, 6 a quarter clockwise, 8 a quarter the other way."""
    photo = _noted(draw(library_root.path / "photo.jpg", _HALVES), orientation)
    ingested = await take_in(content_store, library_root, photo, settings)
    asset_id = ingested.asset.id

    await jobs.probe(
        await context_for("probe", {"asset_id": asset_id}), settings=settings, hardware=hardware
    )
    asset = await content_store.get(asset_id)
    assert asset is not None
    assert (asset.width, asset.height) == size

    await jobs.thumbnail(
        await context_for("thumbnail", {"asset_id": asset_id}), settings=settings, hardware=hardware
    )
    (made,) = await content_store.derivatives(asset_id)
    tile = _grey(settings.cache_dir / made.rel_cache_path, *size)
    assert _white_side(tile) == white


async def test_the_catch_up_turns_a_size_read_before_the_turn_was(
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """A photograph read before its turn was read recorded the size it is stored at. The pass that
    re-reads every file kept under an older reading is what puts it the right way round."""
    photo = _noted(draw(library_root.path / "photo.jpg", _HALVES), 6)
    ingested = await take_in(content_store, library_root, photo, settings)
    await content_store.record_probe(ingested.asset.id, width=64, height=32)

    await jobs.keep_probes(
        await context_for("keep_probes", {}), settings=settings, hardware=hardware
    )

    asset = await content_store.get(ingested.asset.id)
    assert asset is not None
    assert (asset.width, asset.height) == (32, 64)
