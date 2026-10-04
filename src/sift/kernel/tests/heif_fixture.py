# SPDX-License-Identifier: AGPL-3.0-or-later
"""A HEIC laid out the way a phone writes one: a grid of separately coded tiles.

A phone camera does not code a photograph as one picture. It codes a grid of tiles (512 pixels
square is usual) and adds one more item, the grid, which names the tiles in order
and says how big the whole picture is. That grid item is the primary item. A reader that takes the
first coded picture it finds gets one tile, and says the photograph is the size of that tile.

No tool in the test environment writes such a file: libheif through pillow-heif writes a
photograph as one coded picture, and ffmpeg writes no HEIF at all. So the tiles are coded one by
one with pillow-heif, each as a file of its own, and their coded pictures are lifted out and put
together here under a grid item, in the same boxes a phone uses. What comes out is decoded by
libheif exactly as a phone's photograph is.
"""

from __future__ import annotations

import io
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

#: The colour of each tile, row by row: far enough apart that a decoder putting a tile in the
#: wrong place, or reading only one of them, cannot be mistaken for one that did not.
TILE_COLOURS: tuple[tuple[int, int, int], ...] = (
    (220, 30, 30),
    (30, 200, 30),
    (30, 30, 220),
    (230, 230, 230),
    (230, 200, 30),
    (30, 200, 200),
)


@dataclass(frozen=True, slots=True)
class _Coded:
    """One tile's coded picture and the decoder setup it needs, lifted out of its own file."""

    config: bytes
    data: bytes


def _boxes(body: bytes) -> list[tuple[str, bytes]]:
    """The boxes laid end to end in `body`, as (type, payload) pairs."""
    found: list[tuple[str, bytes]] = []
    at = 0
    while at + 8 <= len(body):
        size, kind = struct.unpack(">I4s", body[at : at + 8])
        header = 8
        if size == 1:
            size = struct.unpack(">Q", body[at + 8 : at + 16])[0]
            header = 16
        elif size == 0:
            size = len(body) - at
        found.append((kind.decode("ascii"), body[at + header : at + size]))
        at += size
    return found


def _child(boxes: Sequence[tuple[str, bytes]], kind: str) -> bytes:
    return next(payload for name, payload in boxes if name == kind)


def _uint(raw: bytes, at: int, size: int) -> int:
    return int.from_bytes(raw[at : at + size], "big") if size else 0


def _coded_tile(heic: bytes) -> _Coded:
    """The primary picture of a one-picture HEIC: its `hvcC` and its coded bytes."""
    top = _boxes(heic)
    meta = _boxes(_child(top, "meta")[4:])
    primary = struct.unpack(">H", _child(meta, "pitm")[4:6])[0]

    iloc = _child(meta, "iloc")
    version = iloc[0]
    offset_size, length_size = iloc[4] >> 4, iloc[4] & 15
    base_size = iloc[5] >> 4
    index_size = iloc[5] & 15 if version in (1, 2) else 0
    at = 6
    count = _uint(iloc, at, 2 if version < 2 else 4)
    at += 2 if version < 2 else 4
    data = b""
    for _ in range(count):
        item = _uint(iloc, at, 2 if version < 2 else 4)
        at += 2 if version < 2 else 4
        if version in (1, 2):
            at += 2  # construction method
        at += 2  # data reference index
        base = _uint(iloc, at, base_size)
        at += base_size
        extents = _uint(iloc, at, 2)
        at += 2
        pieces = []
        for _ in range(extents):
            at += index_size
            offset = _uint(iloc, at, offset_size)
            at += offset_size
            length = _uint(iloc, at, length_size)
            at += length_size
            pieces.append(heic[base + offset : base + offset + length])
        if item == primary:
            data = b"".join(pieces)

    iprp = _boxes(_child(meta, "iprp"))
    properties = _boxes(_child(iprp, "ipco"))
    ipma = _child(iprp, "ipma")
    wide = ipma[3] & 1
    at = 4
    entries = _uint(ipma, at, 4)
    at += 4
    config = b""
    for _ in range(entries):
        item = _uint(ipma, at, 2 if ipma[0] < 1 else 4)
        at += 2 if ipma[0] < 1 else 4
        associations = ipma[at]
        at += 1
        for _ in range(associations):
            raw = _uint(ipma, at, 2 if wide else 1)
            at += 2 if wide else 1
            index = raw & (0x7FFF if wide else 0x7F)
            name, payload = properties[index - 1]
            if item == primary and name == "hvcC":
                config = payload
    if not data or not config:
        raise ValueError("the tile's own file did not have the shape expected of it")
    return _Coded(config=config, data=data)


def _box(kind: str, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind.encode("ascii")) + payload


def _full(kind: str, payload: bytes, *, version: int = 0, flags: int = 0) -> bytes:
    return _box(kind, struct.pack(">I", (version << 24) | flags) + payload)


def _ispe(width: int, height: int) -> bytes:
    return _full("ispe", struct.pack(">II", width, height))


def _assemble(tiles: Sequence[_Coded], *, rows: int, columns: int, tile: tuple[int, int]) -> bytes:
    width, height = tile[0] * columns, tile[1] * rows
    grid = struct.pack(">BBBBHH", 0, 0, rows - 1, columns - 1, width, height)
    items = len(tiles) + 1

    def meta(offsets: Sequence[int]) -> bytes:
        hdlr = _full("hdlr", struct.pack(">I4s12s", 0, b"pict", b"\0" * 12) + b"\0")
        pitm = _full("pitm", struct.pack(">H", 1))
        entries = [_full("infe", struct.pack(">HH4s", 1, 0, b"grid") + b"\0", version=2)]
        entries += [
            _full("infe", struct.pack(">HH4s", 2 + n, 0, b"hvc1") + b"\0", version=2, flags=1)
            for n in range(len(tiles))
        ]
        iinf = _full("iinf", struct.pack(">H", len(entries)) + b"".join(entries))
        dimg = _box(
            "dimg",
            struct.pack(">HH", 1, len(tiles))
            + b"".join(struct.pack(">H", 2 + n) for n in range(len(tiles))),
        )
        iref = _full("iref", dimg)
        configs = [_box("hvcC", one.config) for one in tiles]
        ipco = _box("ipco", b"".join(configs) + _ispe(*tile) + _ispe(width, height))
        tile_size, whole_size = len(tiles) + 1, len(tiles) + 2
        associations = [struct.pack(">HBB", 1, 1, whole_size)]
        associations += [
            struct.pack(">HBBB", 2 + n, 2, 0x80 | (n + 1), tile_size) for n in range(len(tiles))
        ]
        ipma = _full("ipma", struct.pack(">I", items) + b"".join(associations))
        iprp = _box("iprp", ipco + ipma)
        located = b"".join(
            struct.pack(">HHHII", item, 0, 1, offset, length)
            for item, (offset, length) in enumerate(
                zip(offsets, [len(grid), *(len(one.data) for one in tiles)], strict=True), start=1
            )
        )
        iloc = _full("iloc", struct.pack(">BBH", 0x44, 0x00, items) + located)
        return _full("meta", hdlr + pitm + iinf + iref + iprp + iloc)

    ftyp = _box("ftyp", b"heic" + struct.pack(">I", 0) + b"mif1heic")
    placeholder = meta([0] * items)
    start = len(ftyp) + len(placeholder) + 8
    offsets = [start]
    for piece in [grid, *(one.data for one in tiles)][:-1]:
        offsets.append(offsets[-1] + len(piece))
    body = grid + b"".join(one.data for one in tiles)
    return ftyp + meta(offsets) + _box("mdat", body)


def grid_heic(
    destination: Path,
    *,
    tile: tuple[int, int] = (64, 64),
    rows: int = 2,
    columns: int = 3,
    colours: Sequence[tuple[int, int, int]] = TILE_COLOURS,
) -> Path:
    """Write a HEIC of `rows` x `columns` tiles, each one flat colour, and return where it went."""
    import pillow_heif
    from PIL import Image

    coded: list[_Coded] = []
    for index in range(rows * columns):
        picture = Image.new("RGB", tile, colours[index % len(colours)])
        one = io.BytesIO()
        pillow_heif.from_pillow(picture).save(one, format="HEIF", quality=90)
        coded.append(_coded_tile(one.getvalue()))
    destination.write_bytes(_assemble(coded, rows=rows, columns=columns, tile=tile))
    return destination
