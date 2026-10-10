# SPDX-License-Identifier: AGPL-3.0-or-later
"""A HEIC or AVIF stripped by rewriting its item tables, and the refusal every strip raises."""

from __future__ import annotations

import struct


class CannotStrip(Exception):
    """This file cannot be sent without what it says about where it came from. It is not sent."""


# --- HEIC and AVIF ---------------------------------------------------------------------------
#
# Both are ISO base media files: a tree of boxes, each a size and a four-letter type. A still
# picture's coded bytes are ITEMS: `iinf` names each item and its type, `iloc` says where its bytes
# are (usually in `mdat`, by absolute file offset), `iref` links items to each other and `ipma`
# gives them properties. Where it was taken lives in two items beside the picture, never in it:
# an `Exif` item (the EXIF block, GPS directory and all) and a `mime` item (XMP), each linked to
# the picture by a `cdsc` reference. A sequence (an animated AVIF, a burst) keeps its frames as
# samples of a track in `moov`, where a phone's location is a `udta` or a `meta` box.
#
# So the strip rewrites the TABLES and leaves every byte of the picture where it was:
#
#   - the metadata items leave `iinf`, `iloc`, `iref` and `ipma`, and their bytes are zeroed where
#     they lay (in `mdat` or in `idat`), so nothing of the EXIF block is left in the file;
#   - the rewritten `meta` is shorter, and a `free` box the size of the difference is written
#     straight after it, so every box after it (the `mdat` holding the picture, a `moov`) starts at
#     the offset it started at, and not one `iloc` offset or `stco` chunk offset has to change;
#   - inside `moov`, every `udta` and `meta` becomes a `free` box of the same size, zeroed;
#   - a top-level `uuid` box (where some writers put XMP) is zeroed the same way, and so is the
#     inside of an existing `free` or `skip`, which is where an editor leaves what it replaced.
#
# Nothing is decoded and nothing is re-encoded: the picture's coded bytes are the same bytes at
# the same offsets, which is what the test compares. Orientation needs nothing kept: a HEIF turns
# its picture with `irot` and `imir` properties, which stay, not with an EXIF tag.

#: The item types that are metadata about a picture rather than a picture: EXIF, XMP (a `mime`
#: item) and a URI item. Everything else (the coded picture, a grid, a thumbnail, an alpha plane,
#: a depth map) is kept whole.
_HEIF_METADATA_ITEMS = frozenset({b"Exif", b"mime", b"uri "})

#: The boxes inside a `moov` that hold other boxes, walked to reach every `udta` and `meta`.
_MOOV_CONTAINERS = frozenset({b"moov", b"trak", b"edts", b"mdia", b"minf", b"dinf", b"stbl"})

#: Boxes inside a `moov` that are metadata and nothing else: emptied into `free` boxes.
_MOOV_EMPTIED = frozenset({b"udta", b"meta"})

#: Top-level boxes whose inside is zeroed and whose type becomes `free`.
_TOP_EMPTIED = frozenset({b"uuid", b"free", b"skip"})


def _boxes(data: bytes, start: int, end: int) -> list[tuple[int, int, int, bytes]]:
    """Each box between `start` and `end`: where it starts, its header length, its end, its type."""
    found: list[tuple[int, int, int, bytes]] = []
    at = start
    while at < end:
        if at + 8 > end:
            raise CannotStrip("a box runs past the one holding it")
        size, kind = struct.unpack(">I4s", data[at : at + 8])
        header = 8
        if size == 1:
            if at + 16 > end:
                raise CannotStrip("a box runs past the one holding it")
            (size,) = struct.unpack(">Q", data[at + 8 : at + 16])
            header = 16
        elif size == 0:
            size = end - at
        if size < header or at + size > end:
            raise CannotStrip("a box runs past the one holding it")
        found.append((at, header, at + size, kind))
        at += size
    return found


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def _uint(data: bytes, at: int, size: int) -> int:
    """A big-endian unsigned number `size` bytes long (up to 8), or 0 for a size of 0."""
    if size == 0:
        return 0
    if size > 8 or at + size > len(data):
        raise CannotStrip("a HEIF table is not one Sift can read")
    return int.from_bytes(data[at : at + size], "big")


def _heif_items(payload: bytes) -> tuple[list[bytes], set[int]]:
    """The `infe` boxes of an `iinf` payload (after its version), and which items are metadata."""
    version = payload[0]
    count_size = 2 if version == 0 else 4
    kept: list[bytes] = []
    dropped: set[int] = set()
    for at, header, end, kind in _boxes(payload, 4 + count_size, len(payload)):
        if kind != b"infe":
            raise CannotStrip("a HEIF item list holds something other than items")
        body = payload[at + header : end]
        infe_version = body[0] if body else 0
        if infe_version >= 2:
            id_size = 2 if infe_version == 2 else 4
            item = _uint(body, 4, id_size)
            item_type = body[4 + id_size + 2 : 4 + id_size + 6]
            metadata = item_type in _HEIF_METADATA_ITEMS
        else:
            # The first two versions have no item type; an item there is described by a content
            # type, which is what a metadata item is. Nothing that draws a HEIF picture uses them.
            item = _uint(body, 4, 2)
            metadata = True
        if metadata:
            dropped.add(item)
        else:
            kept.append(payload[at:end])
    return kept, dropped


def _heif_locations(
    payload: bytes, dropped: set[int]
) -> tuple[bytes, list[tuple[int, int]], list[tuple[int, int]]]:
    """An `iloc` payload without the dropped items, and where their bytes lay: in the file, and in
    `idat`, each as (offset, length)."""
    version = payload[0]
    offset_size, length_size = payload[4] >> 4, payload[4] & 0x0F
    base_size = payload[5] >> 4
    index_size = payload[5] & 0x0F if version in (1, 2) else 0
    id_size, count_size = (2, 2) if version < 2 else (4, 4)
    count = _uint(payload, 6, count_size)
    at = 6 + count_size
    kept = bytearray()
    kept_count = 0
    in_file: list[tuple[int, int]] = []
    in_idat: list[tuple[int, int]] = []
    for _ in range(count):
        start = at
        item = _uint(payload, at, id_size)
        at += id_size
        method = 0
        if version in (1, 2):
            method = _uint(payload, at, 2) & 0x0F
            at += 2
        reference = _uint(payload, at, 2)
        at += 2
        base = _uint(payload, at, base_size)
        at += base_size
        extents = _uint(payload, at, 2)
        at += 2
        spans: list[tuple[int, int]] = []
        for _ in range(extents):
            at += index_size
            offset = _uint(payload, at, offset_size)
            at += offset_size
            length = _uint(payload, at, length_size)
            at += length_size
            spans.append((base + offset, length))
        if at > len(payload):
            raise CannotStrip("a HEIF location table runs past its end")
        if item not in dropped:
            kept += payload[start:at]
            kept_count += 1
            continue
        if method == 2 or reference != 0:
            continue  # bytes in another item or another file: nothing of it is in this one
        for offset, length in spans:
            if length == 0:
                raise CannotStrip("a HEIF metadata item runs to the end of the file")
            (in_idat if method == 1 else in_file).append((offset, length))
    head = payload[:6] + kept_count.to_bytes(count_size, "big")
    return head + bytes(kept), in_file, in_idat


def _heif_references(payload: bytes, dropped: set[int]) -> bytes:
    """An `iref` payload with every reference from or to a dropped item taken out."""
    id_size = 2 if payload[0] == 0 else 4
    out = bytearray(payload[:4])
    for at, header, end, kind in _boxes(payload, 4, len(payload)):
        body = payload[at + header : end]
        source = _uint(body, 0, id_size)
        count = _uint(body, id_size, 2)
        targets = [_uint(body, id_size + 2 + n * id_size, id_size) for n in range(count)]
        left = [one for one in targets if one not in dropped]
        if source in dropped or not left:
            continue
        entry = source.to_bytes(id_size, "big") + len(left).to_bytes(2, "big")
        entry += b"".join(one.to_bytes(id_size, "big") for one in left)
        out += _box(kind, entry)
    return bytes(out)


def _heif_associations(payload: bytes, dropped: set[int]) -> bytes:
    """An `ipma` payload with the dropped items' property associations taken out."""
    version, flags = payload[0], _uint(payload, 1, 3)
    id_size = 2 if version < 1 else 4
    each = 2 if flags & 1 else 1
    count = _uint(payload, 4, 4)
    at = 8
    kept = bytearray()
    kept_count = 0
    for _ in range(count):
        start = at
        item = _uint(payload, at, id_size)
        at += id_size
        at += 1 + _uint(payload, at, 1) * each
        if at > len(payload):
            raise CannotStrip("a HEIF property table runs past its end")
        if item not in dropped:
            kept += payload[start:at]
            kept_count += 1
    return payload[:4] + kept_count.to_bytes(4, "big") + bytes(kept)


def _heif_meta(data: bytes, at: int, header: int, end: int) -> tuple[bytes, list[tuple[int, int]]]:
    """A top-level `meta` box without its metadata items, and the file ranges their bytes held."""
    start = at + header + 4
    children = _boxes(data, start, end)
    found = {kind: (one, head, stop) for one, head, stop, kind in children}
    if b"iinf" not in found or b"iloc" not in found:
        return data[at:end], []
    one, head, stop = found[b"iinf"]
    items, dropped = _heif_items(data[one + head : stop])
    if not dropped:
        return data[at:end], []
    if b"pitm" in found:
        one, head, stop = found[b"pitm"]
        body = data[one + head : stop]
        if _uint(body, 4, 2 if body[0] == 0 else 4) in dropped:
            raise CannotStrip("a HEIF's main item is its metadata")
    one, head, stop = found[b"iloc"]
    locations, in_file, in_idat = _heif_locations(data[one + head : stop], dropped)
    rebuilt = bytearray()
    for one, head, stop, kind in children:
        body = _heif_child(kind, data[one + head : stop], items, locations, in_idat, dropped)
        rebuilt += _box(kind, body)
    return _box(b"meta", data[at + header : start] + bytes(rebuilt)), in_file


def _heif_child(
    kind: bytes,
    body: bytes,
    items: list[bytes],
    locations: bytes,
    in_idat: list[tuple[int, int]],
    dropped: set[int],
) -> bytes:
    """One child of `meta` with the metadata items taken out of it."""
    if kind == b"iinf":
        count_size = 2 if body[0] == 0 else 4
        body = body[:4] + len(items).to_bytes(count_size, "big") + b"".join(items)
    elif kind == b"iloc":
        body = locations
    elif kind == b"iref":
        body = _heif_references(body, dropped)
    elif kind == b"idat":
        body = _heif_zeroed(body, in_idat)
    elif kind == b"iprp":
        body = _heif_properties(body, dropped)
    return body


def _heif_zeroed(body: bytes, in_idat: list[tuple[int, int]]) -> bytes:
    held = bytearray(body)
    for offset, length in in_idat:
        if offset + length > len(held):
            raise CannotStrip("a HEIF metadata item runs past its data")
        held[offset : offset + length] = bytes(length)
    return bytes(held)


def _heif_properties(body: bytes, dropped: set[int]) -> bytes:
    props = bytearray()
    for inner, inner_head, inner_stop, inner_kind in _boxes(body, 0, len(body)):
        inner_body = body[inner + inner_head : inner_stop]
        if inner_kind == b"ipma":
            inner_body = _heif_associations(inner_body, dropped)
        props += _box(inner_kind, inner_body)
    return bytes(props)


def _emptied(out: bytearray, at: int, header: int, end: int) -> None:
    """Turn the box at `at` into a `free` box of the same size, its inside zeroed."""
    out[at + 4 : at + 8] = b"free"
    out[at + header : end] = bytes(end - at - header)


def _empty_moov(data: bytes, out: bytearray, start: int, end: int) -> None:
    for at, header, stop, kind in _boxes(data, start, end):
        if kind in _MOOV_EMPTIED:
            _emptied(out, at, header, stop)
        elif kind in _MOOV_CONTAINERS:
            _empty_moov(data, out, at + header, stop)


def strip_heif(data: bytes) -> bytes:
    """A HEIC or AVIF, one picture or a sequence, with its EXIF and XMP items and its `moov`
    metadata taken out and every byte of the picture left at the offset it was at."""
    top = _boxes(data, 0, len(data))
    if not top or top[0][3] != b"ftyp":
        raise CannotStrip("not a HEIF")
    # Every change below keeps the file's length and every box's offset, so the copy is edited in
    # place at the offsets read from the original.
    out = bytearray(data)
    zeroed: list[tuple[int, int]] = []
    for at, header, end, kind in top:
        if kind == b"meta":
            meta, ranges = _heif_meta(data, at, header, end)
            zeroed += ranges
            gap = (end - at) - len(meta)
            if 0 < gap < 8:  # pragma: no cover (each dropped item takes a box of 8 bytes or more)
                raise CannotStrip("a HEIF's tables shrank by less than a box")
            padding = struct.pack(">I4s", gap, b"free") + bytes(gap - 8) if gap else b""
            out[at:end] = meta + padding
        elif kind == b"moov":
            _empty_moov(data, out, at + header, end)
        elif kind in _TOP_EMPTIED:
            _emptied(out, at, header, end)
    for offset, length in zeroed:
        if offset + length > len(out):
            raise CannotStrip("a HEIF metadata item runs past the file")
        out[offset : offset + length] = bytes(length)
    return bytes(out)
