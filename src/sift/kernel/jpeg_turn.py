# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which way up a JPEG is drawn, when the browser and ffmpeg read it differently.

A JPEG says which way up it goes in its Exif block, and a file can carry more than one (an editor
that adds its own in front of the camera's). A browser obeys the FIRST Exif block, and takes no
turn when that block has none; ffmpeg obeys the LAST block that has one. Most files carry one block
and the two agree. Where they do not, the file a person opens is drawn one way and every picture
Sift makes of it (the tile, the faces, the size the grid lays it out at) the other.

Such a file is read through a copy of itself with every Exif block but the first taken out (see
`kernel.media.resolve_decodable`): the same bytes otherwise, so ffmpeg draws what the browser draws.
"""

from __future__ import annotations

import asyncio
import shutil
import struct
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from sift.kernel.config import Settings
from sift.kernel.content import Asset, ContentStore, DerivativeKind
from sift.kernel.log import get_logger

if TYPE_CHECKING:
    from sift.kernel.media import Source

log = get_logger(__name__)

COPY_EXTENSION = "jpg"

#: How much of a file's head is walked for its Exif blocks: past it, a file is drawn as ffmpeg
#: draws it. Two blocks of the largest size the format allows, and the rest of the head.
HEAD_BYTES = 256 * 1024

_EXIF = b"Exif\x00\x00"
_ORIENTATION = 0x0112
_SHORT = 3


def needs_a_look(asset: Asset) -> bool:
    """Whether this file's head is worth reading: a JPEG photograph."""
    return looked_at(asset.mime, asset.media_type)


def looked_at(mime: str | None, media_type: str) -> bool:
    """`needs_a_look` in the terms the take-in holds before there is a row."""
    return mime == "image/jpeg" and media_type == "image"


def _segments(head: bytes) -> list[tuple[int, int, int]]:
    """Each marker segment before the picture: (marker, start, end), a segment's whole bytes."""
    found: list[tuple[int, int, int]] = []
    if head[:2] != b"\xff\xd8":
        return found
    at = 2
    while at + 4 <= len(head) and head[at] == 0xFF:
        marker = head[at + 1]
        if marker in (0xD9, 0xDA) or 0xD0 <= marker <= 0xD7 or marker in (0x01, 0xFF):
            break
        end = at + 2 + struct.unpack(">H", head[at + 2 : at + 4])[0]
        if end > len(head):
            break
        found.append((marker, at, end))
        at = end
    return found


def _turn_of(block: bytes) -> int | None:
    """The orientation tag of one Exif block's first directory, or None where it has none."""
    tiff = block[len(_EXIF) :]
    order = {b"II": "<", b"MM": ">"}.get(tiff[:2])
    if order is None or len(tiff) < 8:
        return None
    first = struct.unpack(order + "I", tiff[4:8])[0]
    if first + 2 > len(tiff):
        return None
    count = struct.unpack(order + "H", tiff[first : first + 2])[0]
    for index in range(count):
        entry = tiff[first + 2 + 12 * index : first + 14 + 12 * index]
        if len(entry) < 12:
            return None
        tag, kind, many = struct.unpack(order + "HHI", entry[:8])
        if tag == _ORIENTATION and kind == _SHORT and many == 1:
            return int(struct.unpack(order + "H", entry[8:10])[0])
    return None


def _exif_blocks(head: bytes) -> list[tuple[int, int]]:
    """Where each Exif block sits, in order: (start, end) of its whole segment."""
    return [
        (start, end)
        for marker, start, end in _segments(head)
        if marker == 0xE1 and head[start + 4 : start + 4 + len(_EXIF)] == _EXIF
    ]


def turns(head: bytes) -> tuple[int, int]:
    """The turn a browser draws this file at, and the turn ffmpeg draws it at (1 is none)."""
    blocks = [_turn_of(head[start + 4 : end]) for start, end in _exif_blocks(head)]
    browser = (blocks[0] if blocks else None) or 1
    ffmpeg = next((turn for turn in reversed(blocks) if turn is not None), None) or 1
    return browser, ffmpeg


def drawn_apart(path: Path) -> bool:
    """Whether a browser and ffmpeg draw this file at different turns. Blocking: reads its head."""
    with path.open("rb") as source:
        return drawn_apart_in(source.read(HEAD_BYTES))


def drawn_apart_in(head: bytes) -> bool:
    """`drawn_apart` of a head already read: its first `HEAD_BYTES`."""
    browser, ffmpeg = turns(head)
    return browser != ffmpeg


def as_the_browser_reads(data: bytes) -> bytes:
    """The file with every Exif block but the first taken out."""
    later = _exif_blocks(data[:HEAD_BYTES])[1:]
    kept = bytearray()
    at = 0
    for start, end in later:
        kept += data[at:start]
        at = end
    return bytes(kept + data[at:])


async def as_the_browser_draws(
    store: ContentStore, source: Source, *, settings: Settings
) -> Path | None:
    """The copy a decoder should read for a JPEG photograph a browser and ffmpeg turn apart, or
    None for every other file (see `kernel.media.resolve_decodable`)."""
    if not needs_a_look(source.asset):
        return None
    # The take-in keeps the answer on the row; only a file taken in before that is read here.
    apart = source.asset.turn_apart
    if apart is None:
        apart = await asyncio.to_thread(drawn_apart, source.path)
    if not apart:
        return None
    return await readable_copy(store, source.asset, source.path, settings=settings)


async def readable_copy(
    store: ContentStore, asset: Asset, original: Path, *, settings: Settings
) -> Path:
    """The path every decoder should read for this photograph, making it first if it is not there.

    Kept as a derivative beside the tiles, swept with them and made again the same way.
    """
    for existing in await store.derivatives(asset.id):
        if existing.kind is DerivativeKind.RENDITION:
            if (on_disk := await store.derivative_at(existing.rel_cache_path)) is not None:
                return on_disk
            break

    # The system's temporary directory rather than the cache, which the sweep would offer for
    # removal while it is being written.
    with tempfile.TemporaryDirectory(prefix="sift-turn-") as workspace:
        built = Path(workspace) / f"turned.{COPY_EXTENSION}"
        size = await asyncio.to_thread(_write, original, built)
        derivative = await store.add_derivative(
            asset.id, DerivativeKind.RENDITION, extension=COPY_EXTENSION, size_bytes=size
        )
        destination = settings.cache_dir / derivative.rel_cache_path
        await asyncio.to_thread(_place, built, destination)

    log.info("jpeg_turn.readable_copy", asset_id=asset.id)
    return destination


def _write(original: Path, built: Path) -> int:
    copy = as_the_browser_reads(original.read_bytes())
    built.write_bytes(copy)
    return len(copy)


def _place(built: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Sift's own scratch to Sift's own cache: never a library file.
    shutil.move(  # nosemgrep: sift-no-file-removal-outside-delete-trash
        str(built), str(destination)
    )
