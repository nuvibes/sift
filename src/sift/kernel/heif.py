# SPDX-License-Identifier: AGPL-3.0-or-later
"""HEIF stills (a phone's HEIC, and an AVIF photograph): the whole picture, read by libheif.

**ffmpeg reads one tile of a phone's photograph, not the photograph.** A phone does not code a
photograph as one picture: it codes a grid of tiles (commonly 512 pixels square) and
adds an item that names the tiles in order and says how big the whole picture is. ffprobe lists
each tile as a video stream of its own and the grid as a "stream group" beside them, and every
command Sift runs asks for the first video stream. Read that way, a 4032 x 3024 photograph is a
640 x 896 picture (the size of the tile ffprobe lists first): its size, its tile, its faces and its
description are all one tile of it. The same container carries AVIF
photographs, which can be gridded the same way.

**libheif assembles the grid, and is the one door for a HEIF still.** Through pillow-heif for HEIC
and through Pillow's own AVIF reader (libavif, which assembles a grid the same way) for AVIF. The
whole picture is decoded ONCE, turned upright by the file's own rotation, and written as a JPEG:
the readable copy. Everything after that (the probe's size, the tile, the faces, the description,
the fingerprints) reads that copy through `media.resolve_decodable`, the door every stage already
takes, and needs no branch for the format; the animated WebP's MP4 is the same shape.

**The copy is also what a browser draws when it cannot draw the original.** Chromium has no HEIC
decoder, so a HEIC opened in the viewer is served the original, fails to draw, and is shown this
copy instead (`/api/assets/{id}/rendition`). An AVIF needs none: every browser draws AVIF.

**The decode runs in a process of its own.** A decoder handed a malformed file is the last thing
that should be able to take the server down with it: ffmpeg is a subprocess for exactly that
reason (see `media`), and libheif is held to the same rule. The child is the bundled Python with a
dozen lines of script, under the background priority's memory limit and a time limit, so a file
that makes the decoder spin or balloon costs one refused copy and nothing else.

**The copy carries no metadata.** It is written from the pixels alone: no EXIF, so no place a
camera wrote, and no orientation note, because the pixels are already upright. The colour profile
is kept, so a wide-gamut photograph is drawn in its own colours.
"""

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

#: The stills this door reads: a HEIC photograph and an AVIF one. Named by MIME, which the ingress
#: gate sets from the file's own brands the moment the row exists, so it is there while the probe
#: runs (the container column is the probe's own and is still empty then).
HEIF_MIMES = frozenset({"image/heic", "image/avif"})

#: The ones a browser may not draw, and so the only ones whose copy is offered to a browser. Every
#: browser draws AVIF; only Safari draws HEIC.
BROWSER_MAY_NOT_DRAW = frozenset({"image/heic"})

#: What the copy is written as. A JPEG because every browser and every decoder here reads one, and
#: at a quality a phone's own camera writes: this is the photograph somebody looks at full screen.
COPY_EXTENSION = "jpg"
COPY_QUALITY = 90

#: A 48-megapixel photograph decodes and encodes in a few seconds. Generous, because this runs in
#: the background on a file being imported, and bounded, because a malformed file can make a
#: decoder spin.
DECODE_TIMEOUT_SECONDS = 120

#: The child. It imports the two readers and nothing of Sift's, so it starts in a fraction of a
#: second, and it reads only the formats this door exists for: `formats` stops Pillow from offering
#: a file that is not HEIF to every other decoder it carries.
#:
#: `exif_transpose` is the turn: libheif applies a HEIC's rotation while it decodes and resets the
#: orientation note, and libavif leaves an AVIF's rotation to the note, so asking the note after
#: the decode turns both upright exactly once.
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
    """The picture could not be decoded, or the decoder could not be run at all.

    Carries the decoder's own last words where there are any: the person reading a job's error
    column is the one who needs them."""


@dataclass(frozen=True, slots=True)
class Decoded:
    """What one decode wrote: the copy, and the size of the whole picture, upright."""

    path: Path
    width: int
    height: int


def is_heif_still(asset: Asset) -> bool:
    """Whether this is a HEIF photograph, read through this door rather than by ffmpeg.

    A still only. An animated AVIF or HEIC is filed as a GIF and is a sequence of whole frames,
    which ffmpeg reads correctly; its question is which stream moves (see `Probed.picture_stream`).
    """
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
    """The path every decoder should read for this photograph, making it first if it is not there.

    Kept as a derivative beside the tiles, so it is swept with them and made again from the
    original the same way: the cache is disposable by design, and a photograph whose copy is
    swept gets it back on the next thing that asks.
    """
    for existing in await store.derivatives(asset.id):
        if existing.kind is DerivativeKind.RENDITION:
            if (on_disk := await store.derivative_at(existing.rel_cache_path)) is not None:
                return on_disk
            break

    # The system's temporary directory rather than the cache: a file under the cache would be
    # offered for removal by the maintenance sweep while the decode writing it is still running.
    with tempfile.TemporaryDirectory(prefix="sift-heif-") as workspace:
        built = await decode(original, Path(workspace) / f"whole.{COPY_EXTENSION}")
        size = await asyncio.to_thread(lambda: built.path.stat().st_size)
        derivative = await store.add_derivative(
            asset.id, DerivativeKind.RENDITION, extension=COPY_EXTENSION, size_bytes=size
        )
        destination = settings.cache_dir / derivative.rel_cache_path
        # Moved into place rather than written there: a half-written file under a path a row
        # already names is a copy every later reader treats as finished.
        await asyncio.to_thread(_place, built.path, destination)

    log.info("heif.readable_copy", asset_id=asset.id, width=built.width, height=built.height)
    return destination


def _place(built: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Sift's own scratch to Sift's own cache: never a library file.
    shutil.move(  # nosemgrep: sift-no-file-removal-outside-delete-trash
        str(built), str(destination)
    )
