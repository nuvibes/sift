# SPDX-License-Identifier: AGPL-3.0-or-later
"""HEIF stills (a phone's HEIC, an AVIF): the whole picture, read by libheif in a child process.

ffmpeg reads one tile of a gridded photograph; this writes an upright JPEG copy with no metadata."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sift.kernel import subprocess
from sift.kernel.config import Settings
from sift.kernel.content import Asset, ContentStore, DerivativeKind
from sift.kernel.log import get_logger

log = get_logger(__name__)

#: By MIME, which the ingress gate sets before the probe fills the container column.
HEIF_MIMES = frozenset({"image/heic", "image/avif"})

#: Only Safari draws HEIC; every browser draws AVIF.
BROWSER_MAY_NOT_DRAW = frozenset({"image/heic"})

#: A JPEG every reader takes, at a phone camera's own quality.
COPY_EXTENSION = "jpg"
COPY_QUALITY = 90

#: Bounded, as a malformed file can make a decoder spin.
DECODE_TIMEOUT_SECONDS = 120

#: Imports no Sift code and reads only HEIF formats; `exif_transpose` turns both kinds upright
#: exactly once.
_DECODE = """
import json, sys
import pillow_heif
from PIL import Image, ImageOps
pillow_heif.register_heif_opener()
source, destination, quality = sys.argv[1], sys.argv[2], int(sys.argv[3])
with Image.open(source, formats=["HEIF", "AVIF"]) as opened:
    opened.load()
    profile = opened.info.get("icc_profile")
    upright = ImageOps.exif_transpose(opened)
    if upright.mode != "RGB":
        upright = upright.convert("RGB")
    extra = {"icc_profile": profile} if profile else {}
    upright.save(destination, "JPEG", quality=quality, **extra)
    print(json.dumps({"width": upright.width, "height": upright.height}))
"""


class HeifError(Exception):
    """The picture could not be decoded, or the decoder could not run; carries its last words."""


@dataclass(frozen=True, slots=True)
class Decoded:
    """What one decode wrote: the copy, and the size of the whole picture, upright."""

    path: Path
    width: int
    height: int


def is_heif_still(asset: Asset) -> bool:
    """Whether this is a HEIF still, read through this door; an animated one is filed as a GIF."""
    return asset.media_type == "image" and (asset.mime or "") in HEIF_MIMES


def decode_args(source: Path, destination: Path) -> list[str]:
    """The child's command. Absolute paths, so a filename beginning `-` is never an option."""
    return [
        sys.executable,
        "-c",
        _DECODE,
        str(source.resolve()),
        str(destination.resolve()),
        str(COPY_QUALITY),
    ]


async def decode(source: Path, destination: Path) -> Decoded:
    """Decode the whole picture of `source` into a JPEG at `destination`. Raises `HeifError`."""
    argv = await asyncio.to_thread(decode_args, source, destination)
    try:
        result = await subprocess.run(
            argv,
            time_limit=DECODE_TIMEOUT_SECONDS,
            priority=subprocess.Priority.BACKGROUND,
        )
    except subprocess.SubprocessError as error:
        raise HeifError(str(error)) from error
    if result.returncode != 0:
        said = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise HeifError(f"the picture couldn't be decoded: {said[-1] if said else 'no detail'}")
    try:
        size = json.loads(result.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
        return Decoded(path=destination, width=int(size["width"]), height=int(size["height"]))
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise HeifError("the decoder finished without saying what it wrote") from error


async def readable_copy(
    store: ContentStore, asset: Asset, original: Path, *, settings: Settings
) -> Path:
    """The path every decoder should read for this photograph, making the copy if it is gone."""
    for existing in await store.derivatives(asset.id):
        if existing.kind is DerivativeKind.RENDITION:
            if (on_disk := await store.derivative_at(existing.rel_cache_path)) is not None:
                return on_disk
            break

    # Not the cache, whose sweep would offer the file while the decode still writes it.
    with tempfile.TemporaryDirectory(prefix="sift-heif-") as workspace:
        built = await decode(original, Path(workspace) / f"whole.{COPY_EXTENSION}")
        size = await asyncio.to_thread(lambda: built.path.stat().st_size)
        derivative = await store.add_derivative(
            asset.id, DerivativeKind.RENDITION, extension=COPY_EXTENSION, size_bytes=size
        )
        destination = settings.cache_dir / derivative.rel_cache_path
        # Moved into place, as a half-written file under a named path reads as finished.
        await asyncio.to_thread(_place, built.path, destination)

    log.info("heif.readable_copy", asset_id=asset.id, width=built.width, height=built.height)
    return destination


def _place(built: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Sift's own scratch to Sift's own cache: never a library file.
    shutil.move(  # nosemgrep: sift-no-file-removal-outside-delete-trash
        str(built), str(destination)
    )
