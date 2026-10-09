# SPDX-License-Identifier: AGPL-3.0-or-later
"""The word list that names a place, and where each picture format keeps one."""

from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass

#: The words that make a name a place: a substring test, wider than it needs to be on purpose.
PLACE_WORDS = ("location", "gps", "geo", "iso6709")


def names_a_place(name: str) -> bool:
    """Whether a key, a tag name or a property name says where something was."""
    lowered = name.lower()
    return any(word in lowered for word in PLACE_WORDS)


class _Unreadable(Exception):
    """A structure ran past its own end or said something no writer writes."""


@dataclass(frozen=True, slots=True)
class _Splice:
    """Replace the bytes from `start` to `end` of the source with `data` in the copy."""

    start: int
    end: int
    data: bytes


#: The most a compressed PNG text chunk is allowed to grow to when it is opened to be read.
_TEXT_CAP = 16 * 1024 * 1024


def _u16(data: bytes, at: int, order: str = ">") -> int:
    if at < 0 or at + 2 > len(data):
        raise _Unreadable("a number runs past its table")
    return int(struct.unpack_from(order + "H", data, at)[0])


def _u32(data: bytes, at: int, order: str = ">") -> int:
    if at < 0 or at + 4 > len(data):
        raise _Unreadable("a number runs past its table")
    return int(struct.unpack_from(order + "I", data, at)[0])


def _uint(data: bytes, at: int, size: int) -> int:
    """A big-endian unsigned number `size` bytes long (0 to 8)."""
    if size == 0:
        return 0
    if size > 8 or at < 0 or at + size > len(data):
        raise _Unreadable("a number runs past its table")
    return int.from_bytes(data[at : at + size], "big")


# --- EXIF: the GPS directory ----------------------------------------------------------------------

_GPS_POINTER = 0x8825
_XMP_TAG = 0x02BC
#: Bytes per value of each TIFF field type.
_TIFF_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}


def _tiff_without_places(tiff: bytes) -> bytes | None:
    """A TIFF block (an EXIF body) with its GPS directory gone, the same length, or None when it
    has no place in it.

    The pointer's entry is taken out of the first directory by moving every entry after it up one
    place and the next-directory link up behind them, the count one less, and the twelve bytes
    that frees zeroed. The GPS directory and every value it points at are zeroed where they lie.
    Nothing else moves, so every other offset in the block still points at what it pointed at.
    """
    if tiff[:4] == b"II*\x00":
        order = "<"
    elif tiff[:4] == b"MM\x00*":
        order = ">"
    else:
        raise _Unreadable("an EXIF block is not a TIFF")
    first = _u32(tiff, 4, order)
    count = _u16(tiff, first, order)
    entries = first + 2
    if entries + count * 12 + 4 > len(tiff):
        raise _Unreadable("an EXIF directory runs past its block")
    out = bytearray(tiff)
    changed = False
    gps_index: int | None = None
    for index in range(count):
        at = entries + index * 12
        tag, kind, number = struct.unpack_from(order + "HHI", tiff, at)
        if tag == _GPS_POINTER:
            gps_index = index
        elif tag == _XMP_TAG and kind in (1, 7):
            start = _u32(tiff, at + 8, order) if number > 4 else at + 8
            if start + number > len(tiff):
                raise _Unreadable("an EXIF XMP packet runs past its block")
            blanked = _xmp_without_places(tiff[start : start + number])
            if blanked is not None:
                out[start : start + number] = blanked
                changed = True
    if gps_index is not None:
        _zero_gps_directory(tiff, out, _u32(tiff, entries + gps_index * 12 + 8, order), order)
        at = entries + gps_index * 12
        stop = entries + count * 12 + 4
        out[at : stop - 12] = tiff[at + 12 : stop]
        out[stop - 12 : stop] = bytes(12)
        struct.pack_into(order + "H", out, first, count - 1)
        changed = True
    return bytes(out) if changed else None


def _zero_gps_directory(tiff: bytes, out: bytearray, at: int, order: str) -> None:
    """Zero the GPS directory at `at` and every value stored outside it. A directory the pointer
    does not really reach has nothing of it zeroed: the pointer going is what matters."""
    if at <= 0 or at + 2 > len(tiff):
        return
    count = _u16(tiff, at, order)
    end = at + 2 + count * 12 + 4
    if end > len(tiff):
        return
    for index in range(count):
        entry = at + 2 + index * 12
        _tag, kind, number = struct.unpack_from(order + "HHI", tiff, entry)
        size = _TIFF_SIZES.get(kind, 1) * number
        if size > 4:
            value = _u32(tiff, entry + 8, order)
            if value + size <= len(tiff):
                out[value : value + size] = bytes(size)
    out[at:end] = bytes(end - at)


_EXIF_HEADER = b"Exif\x00\x00"


def _exif_splices(block: bytes, base: int) -> list[_Splice]:
    """The edit for an EXIF body that may or may not start with its `Exif` header.

    An EXIF body that cannot be read as a TIFF is zeroed where it lies, the same length: it may
    hold a place and nothing can say it does not, and a reader meeting zeros where EXIF should be
    ignores them, as it ignored the unreadable block."""
    offset = len(_EXIF_HEADER) if block.startswith(_EXIF_HEADER) else 0
    if not block[offset:].strip(b"\x00"):
        return []  # nothing, or a block this door has already zeroed
    try:
        rewritten = _tiff_without_places(block[offset:])
    except _Unreadable:
        rewritten = bytes(len(block) - offset)
    if rewritten is None:
        return []
    return [_Splice(base + offset, base + len(block), rewritten)]


# --- XMP ------------------------------------------------------------------------------------------

_WORDS = b"|".join(word.encode("ascii") for word in PLACE_WORDS)
#: A property ELEMENT whose qualified name holds a place word, with everything inside it.
_XMP_ELEMENT = re.compile(
    rb"<([A-Za-z_][\w.-]*:[\w.-]*(?:" + _WORDS + rb")[\w.-]*)(?=[\s/>])[^>]*?(?:/>|>.*?</\1\s*>)",
    re.IGNORECASE | re.DOTALL,
)


#: A property ATTRIBUTE whose qualified name holds a place word. A namespace declaration is never
#: one: blanking `xmlns:geo` would leave every element that uses the prefix undeclared.
_XMP_ATTRIBUTE = re.compile(
    rb"(?<=\s)(?!xmlns:)[A-Za-z_][\w.-]*:[\w.-]*(?:" + _WORDS + rb")[\w.-]*\s*=\s*"
    rb"(?:\"[^\"]*\"|'[^']*')",
    re.IGNORECASE,
)


def _spaces(match: re.Match[bytes]) -> bytes:
    return b" " * (match.end() - match.start())


def _xmp_without_places(packet: bytes) -> bytes | None:
    """The packet with every place property overwritten by spaces, the same length, or None."""
    blanked = _XMP_ATTRIBUTE.sub(_spaces, _XMP_ELEMENT.sub(_spaces, packet))
    return blanked if blanked != packet else None


def _xmp_splices(packet: bytes, base: int) -> list[_Splice]:
    blanked = _xmp_without_places(packet)
    return [] if blanked is None else [_Splice(base, base + len(packet), blanked)]


# --- JPEG -----------------------------------------------------------------------------------------

_XMP_JPEG = b"http://ns.adobe.com/xap/1.0/\x00"
_XMP_JPEG_EXTENDED = b"http://ns.adobe.com/xmp/extension/\x00"


def _jpeg(data: bytes, base: int = 0) -> list[_Splice]:
    """Every place in a JPEG, and in any picture stored after its end (a multi-picture file keeps
    its second picture, with its own EXIF, straight after the first)."""
    splices: list[_Splice] = []
    start = 0
    while True:
        end = _jpeg_one(data, start, base, splices)
        following = data.find(b"\xff\xd8\xff", end)
        if following < 0:
            return splices
        start = following


def _jpeg_one(data: bytes, start: int, base: int, splices: list[_Splice]) -> int:
    """Read one JPEG from `start`, adding its places to `splices`; answer where it ends."""
    if data[start : start + 2] != b"\xff\xd8":
        raise _Unreadable("not a JPEG")  # pragma: no cover (every caller matched the marker)
    size = len(data)
    at = start + 2
    while at < size:
        if data[at] != 0xFF:
            raise _Unreadable("a JPEG segment did not start where it should")
        while at < size and data[at] == 0xFF:
            at += 1
        if at >= size:
            break
        marker = data[at]
        at += 1
        if marker == 0xD9:
            return at
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            continue
        length = _u16(data, at)
        end = at + length
        if length < 2 or end > size:
            raise _Unreadable("a JPEG segment runs past the file")
        payload_at = at + 2
        payload = data[payload_at:end]
        _app1_places(marker, payload, payload_at, base, splices)
        at = end
        if marker == 0xDA:
            at = _past_scan(data, at)
    raise _Unreadable("a JPEG has no end")


def _app1_places(
    marker: int, payload: bytes, payload_at: int, base: int, splices: list[_Splice]
) -> None:
    """Add the places in one APP1 segment, EXIF or XMP, to `splices`."""
    if marker == 0xE1:
        if payload.startswith(_EXIF_HEADER):
            splices += _exif_splices(payload, base + payload_at)
        elif payload.startswith(_XMP_JPEG):
            splices += _xmp_splices(payload[len(_XMP_JPEG) :], base + payload_at + len(_XMP_JPEG))
        elif payload.startswith(_XMP_JPEG_EXTENDED):
            # The GUID (32) and the two lengths (8) come before this part of the packet.
            skip = len(_XMP_JPEG_EXTENDED) + 40
            splices += _xmp_splices(payload[skip:], base + payload_at + skip)


def _past_scan(data: bytes, at: int) -> int:
    """Where the next marker after a scan's coded data starts."""
    size = len(data)
    while True:
        found = data.find(b"\xff", at)
        if found < 0 or found + 1 >= size:
            raise _Unreadable("a JPEG ends inside its picture")
        following = data[found + 1]
        if following == 0x00 or 0xD0 <= following <= 0xD7 or following == 0xFF:
            at = found + (1 if following == 0xFF else 2)
            continue
        return found


# --- PNG ------------------------------------------------------------------------------------------

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_TEXT = frozenset({b"tEXt", b"zTXt", b"iTXt"})
_XMP_KEYWORD = "XML:com.adobe.xmp"
_RAW_PROFILE = "raw profile type "


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def _inflate(data: bytes) -> bytes:
    opened = zlib.decompressobj()
    try:
        out = opened.decompress(data, _TEXT_CAP)
    except zlib.error as error:
        raise _Unreadable("a PNG text chunk does not decompress") from error
    if opened.unconsumed_tail:
        raise _Unreadable("a PNG text chunk is too large to examine")
    return out


def _png_text(kind: bytes, payload: bytes) -> tuple[str, bytes, int | None]:
    """A text chunk's keyword, its text, and where the text starts in the payload when it is
    stored uncompressed (so it can be blanked in place), else None."""
    keyword, _, rest = payload.partition(b"\x00")
    name = keyword.decode("latin-1")
    if kind == b"tEXt":
        return name, rest, len(keyword) + 1
    if kind == b"zTXt":
        return name, _inflate(rest[1:]), None
    compressed = rest[:1] == b"\x01"
    tail = rest[2:]
    _language, _, tail = tail.partition(b"\x00")
    _translated, _, text = tail.partition(b"\x00")
    if compressed:
        return name, _inflate(text), None
    return name, text, len(payload) - len(text)


def _raw_profile_places(name: str, text: bytes) -> bool:
    """Whether ImageMagick's hex-encoded profile (`\\nexif\\n  1234\\n<hex>`) holds a place."""
    lines = text.strip().split(b"\n")
    try:
        body = bytes.fromhex(b"".join(line.strip() for line in lines[2:]).decode("ascii"))
    except ValueError as error:
        raise _Unreadable("a raw profile is not hex") from error
    profile = name[len(_RAW_PROFILE) :].lower()
    if profile in ("exif", "app1"):
        return bool(_exif_splices(body, 0))
    return profile == "xmp" and _xmp_without_places(body) is not None


def _png(data: bytes, base: int = 0) -> list[_Splice]:
    if not data.startswith(_PNG_SIGNATURE):
        raise _Unreadable("not a PNG")  # pragma: no cover (every caller matched the signature)
    splices: list[_Splice] = []
    at = len(_PNG_SIGNATURE)
    while at + 12 <= len(data):
        length = _u32(data, at)
        kind = data[at + 4 : at + 8]
        end = at + 12 + length
        if end > len(data):
            raise _Unreadable("a PNG chunk runs past the file")
        payload = data[at + 8 : end - 4]
        if kind == b"eXIf":
            edits = _exif_splices(payload, 0)
            if edits:
                rewritten = bytearray(payload)
                for one in edits:
                    rewritten[one.start : one.end] = one.data
                splices.append(_Splice(base + at, base + end, _png_chunk(kind, bytes(rewritten))))
        elif kind in _PNG_TEXT:
            splices += _png_text_splices(kind, payload, base + at, base + end)
        at = end
        if kind == b"IEND":
            return splices
    raise _Unreadable("a PNG has no end")


def _png_text_splices(kind: bytes, payload: bytes, start: int, end: int) -> list[_Splice]:
    """A text chunk that names a place, holds one, or cannot be read to say, dropped whole."""
    drop = [_Splice(start, end, b"")]
    try:
        name, text, text_at = _png_text(kind, payload)
        if name.lower().startswith(_RAW_PROFILE) and not names_a_place(name):
            return drop if _raw_profile_places(name, text) else []
    except _Unreadable:
        return drop
    if names_a_place(name):
        return drop
    if name == _XMP_KEYWORD:
        blanked = _xmp_without_places(text)
        if blanked is None:
            return []
        if text_at is None:
            return drop
        rewritten = payload[:text_at] + blanked
        return [_Splice(start, end, _png_chunk(kind, rewritten))]
    return []


# --- WebP -----------------------------------------------------------------------------------------


def _webp(data: bytes, base: int = 0) -> list[_Splice]:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise _Unreadable("not a WebP")  # pragma: no cover (classify matched these bytes)
    splices: list[_Splice] = []
    at = 12
    while at + 8 <= len(data):
        kind = data[at : at + 4]
        length = _u32(data, at + 4, "<")
        if at + 8 + length > len(data):
            raise _Unreadable("a WebP chunk runs past the file")
        payload = data[at + 8 : at + 8 + length]
        if kind == b"EXIF":
            splices += _exif_splices(payload, base + at + 8)
        elif kind == b"XMP ":
            splices += _xmp_splices(payload, base + at + 8)
        at += 8 + length + (length & 1)
    return splices


# --- GIF ------------------------------------------------------------------------------------------


def _gif_blocks(data: bytes, at: int) -> int:
    """The index just past a run of GIF data sub-blocks starting at `at`."""
    while True:
        if at >= len(data):
            raise _Unreadable("a GIF ends inside a block")
        length = data[at]
        at += 1 + length
        if length == 0:
            return at


def _gif(data: bytes, base: int = 0) -> list[_Splice]:
    """An XMP application extension holding a place, dropped whole. XMP in a GIF is written as
    raw bytes that double as their own block lengths, so it cannot be blanked in place."""
    if data[:6] not in (b"GIF87a", b"GIF89a") or len(data) < 13:
        raise _Unreadable("not a GIF")
    flags = data[10]
    at = 13 + (3 * (2 ** ((flags & 0x07) + 1)) if flags & 0x80 else 0)
    splices: list[_Splice] = []
    while at < len(data):
        introducer = data[at]
        if introducer == 0x3B:
            return splices
        if introducer == 0x2C:
            if at + 10 > len(data):
                raise _Unreadable("a GIF frame runs past the file")
            local = data[at + 9]
            at += 10 + (3 * (2 ** ((local & 0x07) + 1)) if local & 0x80 else 0)
            at = _gif_blocks(data, at + 1)
            continue
        if introducer == 0x21 and at + 1 < len(data):
            start = at
            end = _gif_blocks(data, at + 2)
            if (
                data[at + 1] == 0xFF
                and data[at + 3 : at + 14] == b"XMP DataXMP"
                and _xmp_without_places(data[at + 14 : end]) is not None
            ):
                splices.append(_Splice(base + start, base + end, b""))
            at = end
            continue
        raise _Unreadable("a GIF block is not one Sift can read")
    raise _Unreadable("a GIF has no end")


def _picture(data: bytes, base: int) -> list[_Splice]:
    """A picture carried inside another file (a video's cover art, an attachment)."""
    if data.startswith(b"\xff\xd8\xff"):
        return _jpeg(data, base)
    if data.startswith(_PNG_SIGNATURE):
        return _png(data, base)
    return []
