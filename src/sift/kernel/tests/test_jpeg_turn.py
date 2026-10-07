# SPDX-License-Identifier: AGPL-3.0-or-later
"""A JPEG is drawn as a browser draws it: by its first Exif block, which ffmpeg does not obey
where a later block says otherwise; such a file is read through a copy carrying the first alone."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from sift.kernel import jpeg_turn
from sift.kernel.config import Settings
from sift.kernel.content import Asset, ContentStore, DerivativeKind
from sift.kernel.ids import new_id


def _exif(turn: int | None, *, order: bytes = b"MM") -> bytes:
    """One Exif block, its first directory holding an orientation (or a resolution unit alone)."""
    end = ">" if order == b"MM" else "<"
    tag, value = (0x0112, turn) if turn is not None else (0x0128, 2)
    entry = struct.pack(end + "HHIHH", tag, 3, 1, value, 0)
    tiff = order + struct.pack(end + "HI", 42, 8) + struct.pack(end + "H", 1) + entry
    body = b"Exif\x00\x00" + tiff + struct.pack(end + "I", 0)
    return b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body


_XMP = b"\xff\xe1" + struct.pack(">H", 2 + 29 + 4) + b"http://ns.adobe.com/xap/1.0/\x00<x/>"
_REST = b"\xff\xdb" + struct.pack(">H", 4) + b"\x00\x01" + b"\xff\xda\x00\x02picture\xff\xd9"


def _jpeg(*segments: bytes) -> bytes:
    return b"\xff\xd8" + b"".join(segments) + _REST


@pytest.mark.parametrize(
    ("segments", "browser", "ffmpeg"),
    [
        ((), 1, 1),
        ((_exif(6),), 6, 6),
        ((_exif(6, order=b"II"),), 6, 6),
        # A browser takes the first block as it is; ffmpeg the last block that carries a turn.
        ((_exif(6), _exif(1)), 6, 1),
        ((_exif(1), _exif(6)), 1, 6),
        ((_exif(None), _exif(6)), 1, 6),
        ((_exif(6), _exif(None)), 6, 6),
        ((_XMP, _exif(6)), 6, 6),
    ],
)
def test_each_reader_s_turn(segments: tuple[bytes, ...], browser: int, ffmpeg: int) -> None:
    assert jpeg_turn.turns(_jpeg(*segments)) == (browser, ffmpeg)


def test_what_is_not_a_readable_head_takes_no_turn() -> None:
    assert jpeg_turn.turns(b"GIF89a") == (1, 1)
    # A segment running past what was read, and a block too short to hold its directory.
    assert jpeg_turn.turns(b"\xff\xd8\xff\xe1\x40\x00Exif\x00\x00MM") == (1, 1)
    short = b"Exif\x00\x00MM\x00\x2a\x00\x00\x00\x08"
    assert jpeg_turn.turns(b"\xff\xd8\xff\xe1" + struct.pack(">H", len(short) + 2) + short) == (
        1,
        1,
    )
    # A directory past the block's end, a byte order that is neither, and an entry cut short.
    far = b"Exif\x00\x00MM\x00\x2a\x00\x00\x00\x40"
    odd = b"Exif\x00\x00XX\x00\x2a\x00\x00\x00\x08"
    cut = b"Exif\x00\x00MM\x00\x2a\x00\x00\x00\x08\x00\x02" + b"\x01\x12\x00\x03"
    for block in (far, odd, cut):
        head = b"\xff\xd8\xff\xe1" + struct.pack(">H", len(block) + 2) + block
        assert jpeg_turn.turns(head) == (1, 1)


def test_the_copy_keeps_the_first_block_alone(tmp_path: Path) -> None:
    data = _jpeg(_exif(6), _XMP, _exif(1))
    copy = jpeg_turn.as_the_browser_reads(data)
    assert copy == _jpeg(_exif(6), _XMP)
    assert jpeg_turn.turns(copy) == (6, 6)
    apart, together = tmp_path / "apart.jpg", tmp_path / "together.jpg"
    apart.write_bytes(data)
    together.write_bytes(copy)
    assert jpeg_turn.drawn_apart(apart)
    assert not jpeg_turn.drawn_apart(together)


async def _a_photograph(store: ContentStore, mime: str = "image/jpeg") -> Asset:
    asset_id = new_id()
    await store._db.execute(
        "INSERT INTO assets (id, identity, media_type, mime, added_at) VALUES (?, ?, 'image', ?, 0)",
        (asset_id, f"digest-{asset_id}", mime),
    )
    got = await store.get(asset_id)
    assert got is not None
    return got


async def test_only_a_jpeg_photograph_is_looked_at(content_store: ContentStore) -> None:
    assert jpeg_turn.needs_a_look(await _a_photograph(content_store))
    assert not jpeg_turn.needs_a_look(await _a_photograph(content_store, "image/png"))


async def test_the_copy_is_made_once_and_made_again_when_swept(
    content_store: ContentStore, settings: Settings, tmp_path: Path
) -> None:
    original = tmp_path / "turned.jpg"
    original.write_bytes(_jpeg(_exif(6), _exif(1)))
    asset = await _a_photograph(content_store)
    await content_store.add_derivative(asset.id, DerivativeKind.THUMB, extension="jpg")

    first = await jpeg_turn.readable_copy(content_store, asset, original, settings=settings)
    again = await jpeg_turn.readable_copy(content_store, asset, original, settings=settings)

    assert again == first
    assert first.read_bytes() == _jpeg(_exif(6))
    first.unlink()
    swept = await jpeg_turn.readable_copy(content_store, asset, original, settings=settings)
    assert swept.read_bytes() == _jpeg(_exif(6))
