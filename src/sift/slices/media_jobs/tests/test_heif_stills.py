# SPDX-License-Identifier: AGPL-3.0-or-later
"""A phone's HEIC read whole: the size recorded, the tile drawn, and the copy a browser is given.

The photograph here is laid out the way a phone lays one out, a grid of separately coded tiles
(`kernel.tests.heif_fixture`), because that is the whole of the fault: ffprobe lists the tiles as
streams, and a command that asks for the first stream gets one tile. A photograph coded as
one picture would pass every test below whether the door existed or not.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

import sift.slices.workbench.schema  # noqa: F401  (the ledger's table, see test_jobs)
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, DerivativeKind
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import JobContext
from sift.kernel.tests.heif_fixture import grid_heic
from sift.slices.media_jobs import jobs
from sift.slices.media_jobs.tests.conftest import take_in
from sift.testing.fixtures import LibraryRoot

pytestmark = [pytest.mark.integration]

Context = Callable[[str, dict[str, object]], Awaitable[JobContext]]

#: Three tiles across and two down, each 64 pixels square: a whole picture of 192 x 128.
WHOLE = (192, 128)
TILE = (64, 64)


def _size_of(path: Path) -> tuple[int, int]:
    said = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    stream = json.loads(said.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


async def _derivative(store: ContentStore, asset_id: str, kind: DerivativeKind) -> Path:
    found = [one for one in await store.derivatives(asset_id) if one.kind is kind]
    assert len(found) == 1, f"expected one {kind.value}, found {len(found)}"
    at = await store.derivative_at(found[0].rel_cache_path)
    assert at is not None
    return at


async def test_a_phones_heic_is_recorded_at_the_size_of_the_whole_picture(
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """ffprobe says a tile; the row says the photograph. And the rest of the reading is still the
    file's own: its codec is HEVC, not the JPEG of the copy it is measured from."""
    photo = grid_heic(library_root.path / "IMG_0001.heic")
    assert _size_of(photo) == TILE, (
        "the fixture must reproduce what ffprobe says of a phone's photo"
    )
    ingested = await take_in(content_store, library_root, photo, settings)

    await jobs.probe(
        await context_for("probe", {"asset_id": ingested.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    asset = await content_store.get(ingested.asset.id)
    assert asset is not None
    assert (asset.width, asset.height) == WHOLE
    assert asset.vcodec == "hevc"
    copy = await _derivative(content_store, asset.id, DerivativeKind.RENDITION)
    assert copy.suffix == ".jpg"
    assert _size_of(copy) == WHOLE


async def test_the_tile_of_a_phones_heic_is_cut_from_the_whole_picture(
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The tile keeps the photograph's shape, three wide and two high, where one tile of it is
    square; and it holds every tile's colour, left to right."""
    from PIL import Image

    photo = grid_heic(library_root.path / "IMG_0002.heic")
    ingested = await take_in(content_store, library_root, photo, settings)
    await jobs.probe(
        await context_for("probe", {"asset_id": ingested.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    await jobs.thumbnail(
        await context_for("thumbnail", {"asset_id": ingested.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    thumb = await _derivative(content_store, ingested.asset.id, DerivativeKind.THUMB)
    assert _size_of(thumb) == WHOLE
    with Image.open(thumb) as drawn:
        rgb = drawn.convert("RGB")
        left, middle, right = (rgb.getpixel((x, 32)) for x in (32, 96, 160))
    assert isinstance(left, tuple) and isinstance(middle, tuple) and isinstance(right, tuple)
    assert left[0] > 150 and left[1] < 90, "the first tile is red"
    assert middle[1] > 150 and middle[0] < 90, "the second tile is green"
    assert right[2] > 150 and right[0] < 90, "the third tile is blue"


async def test_an_ordinary_photograph_is_read_from_its_own_bytes_and_given_no_copy(
    picture: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The door is for HEIF alone: a JPEG is read from its own bytes, and nothing is made of it."""
    ingested = await take_in(content_store, library_root, picture, settings)

    await jobs.probe(
        await context_for("probe", {"asset_id": ingested.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    kinds = [one.kind for one in await content_store.derivatives(ingested.asset.id)]
    assert DerivativeKind.RENDITION not in kinds
