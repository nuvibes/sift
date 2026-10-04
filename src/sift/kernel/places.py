# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a file was made: the one definition of a place, and the one door that takes it out.

The rule this module holds: a location is never kept in anything Sift stores, and every
copy Sift makes or sends carries none. The originals in somebody's own folders
are theirs and are never rewritten; everything Sift WRITES from them is.

Two halves, one word list:

- `strip_places` takes a tool's answer (ffprobe's JSON) and drops every key that names a place,
  before the answer is stored.
- `remove_places` takes a FILE and makes a copy of it with every location field removed, or
  answers None when the file carries none, so the caller hands out the file as it is. It never
  opens its source for anything but reading: the copy is a new file in a new directory under the
  scratch folder the caller names, which is always Sift's own.

## What counts as a place in a file, per format

- **EXIF** (a JPEG's APP1, a PNG's `eXIf`, a WebP's `EXIF`, a HEIC or AVIF `Exif` item, a picture
  inside a video's cover art or a Matroska attachment): the GPS directory. Its entries and values
  are zeroed and its pointer taken out of the first directory, with every other entry moved up one
  place so every offset in the block stays where it was. The camera, the time and the orientation
  are left as they were: the rule is that the coordinates go, not the file's history.
- **XMP** (in all of the above, and in a video's `XMP_` or XMP `uuid` box): every property whose
  name holds a place word, overwritten with spaces of the same length, which XML reads as nothing.
- **PNG text**: a text chunk whose keyword names a place, or whose raw profile (ImageMagick's
  hex-encoded EXIF or XMP) holds one, is dropped whole. The picture's own chunks are untouched.
- **GIF**: an XMP application extension holding a place is dropped whole.
- **MP4 and MOV**: the QuickTime `\xa9xyz` atom and the 3GPP `loci` box become `free` boxes of the
  same size, zeroed; an Apple `keys`/`ilst` entry whose key names a place does too; a timed track
  whose samples are positions (GoPro's `gpmd`, the camera-motion `camm`, an Apple `mebx` track
  describing a location) keeps its table and has its samples zeroed where they lie.
- **Matroska and WebM**: a `SimpleTag` whose name holds a place word becomes a `Void` element of
  the same length, and a CRC-32 over the element holding it is written again.

Every edit but the PNG and GIF drops keeps the file's length and every byte's offset, so the
picture and the sound are the same bytes at the same places and nothing that points into the file
has to change. The copy is read again once written; a copy the second reading still finds a place
in is refused rather than handed out.

## Why a byte-level rewrite and not a remux

A stream copy through ffmpeg rewrites the whole container: a new `moov`, new chunk offsets, the
muxer's own idea of what to keep. It loses things nobody asked it to lose and writes a different
file for the same picture. The edits above change the metadata and nothing else, and a file with
no location costs one read of its tables and no write at all.
"""

from __future__ import annotations

import re
import shutil
import struct
import tempfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, BinaryIO

from sift.kernel.ingress import MediaType, classify, read_ends
from sift.kernel.log import get_logger

log = get_logger(__name__)

__all__ = [
    "PLACE_WORDS",
    "CannotRemovePlaces",
    "discard",
    "names_a_place",
    "places_in",
    "remove_places",
    "remove_places_from_own",
    "strip_places",
]

#: The words that make a name a PLACE, wherever it appears.
#:
#: A camera writes where it was standing into the file, and the commonest of them is the QuickTime
#: location atom, which a muxer rewrites as a plain `location` and a `location-<language>` beside
#: it, so matching the atom's full name alone would miss both keys a real file carries. So it is a
#: substring test, case-insensitive, deliberately wider than it needs to be: a key that happens to
#: say `gps` is dropped, and the cost of dropping one is a field nobody was reading.
PLACE_WORDS = ("location", "gps", "geo", "iso6709")


def names_a_place(name: str) -> bool:
    """Whether a key, a tag name or a property name says where something was."""
    lowered = name.lower()
    return any(word in lowered for word in PLACE_WORDS)


def strip_places(payload: Any) -> Any:
    """The same answer with every key that names a place taken out, at every depth.

    Case-insensitively, because a tag's spelling is the writer's choice: `Location`,
    `LOCATION-eng` and `com.apple.quicktime.location.ISO6709` are all one thing.
    """
    if isinstance(payload, dict):
        return {
            key: strip_places(value)
            for key, value in payload.items()
            if not names_a_place(str(key))
        }
    if isinstance(payload, list):
        return [strip_places(one) for one in payload]
    return payload


class CannotRemovePlaces(Exception):
    """This file's tables cannot be read far enough to be sure no place is left in a copy of it.

    The copy is not made. A caller that was going to hand one out refuses instead: a file whose
    location cannot be taken out does not leave with it."""


class _Unreadable(Exception):
    """A structure ran past its own end or said something no writer writes."""


# --- the edits -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Splice:
    """Replace the bytes from `start` to `end` of the source with `data` in the copy."""

    start: int
    end: int
    data: bytes


#: The largest thing read whole into memory to be examined: a picture, a `moov`, a Tags element.
#: A picture past it cannot be examined, so it cannot be copied without its place: it is refused.
_READ_CAP = 512 * 1024 * 1024

#: The most a compressed PNG text chunk is allowed to grow to when it is opened to be read.
_TEXT_CAP = 16 * 1024 * 1024


class _Source:
    """Reads at an offset from an open file, refusing anything past its end."""

    def __init__(self, handle: BinaryIO, size: int) -> None:
        self.handle = handle
        self.size = size

    def read(self, at: int, length: int) -> bytes:
        if at < 0 or length < 0 or at + length > self.size:
            raise _Unreadable("a structure runs past the end of the file")
        if length > _READ_CAP:
            raise _Unreadable("a structure is too large to examine")
        self.handle.seek(at)
        data = self.handle.read(length)
        if len(data) != length:
            raise _Unreadable("the file is shorter than it said")
        return data


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


# --- EXIF: the GPS directory -------------------------------------------------------------------

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


# --- XMP ----------------------------------------------------------------------------------------

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


# --- JPEG ---------------------------------------------------------------------------------------

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
        if marker == 0xE1:
            if payload.startswith(_EXIF_HEADER):
                splices += _exif_splices(payload, base + payload_at)
            elif payload.startswith(_XMP_JPEG):
                splices += _xmp_splices(
                    payload[len(_XMP_JPEG) :], base + payload_at + len(_XMP_JPEG)
                )
            elif payload.startswith(_XMP_JPEG_EXTENDED):
                # The GUID (32) and the two lengths (8) come before this part of the packet.
                skip = len(_XMP_JPEG_EXTENDED) + 40
                splices += _xmp_splices(payload[skip:], base + payload_at + skip)
        at = end
        if marker == 0xDA:
            at = _past_scan(data, at)
    raise _Unreadable("a JPEG has no end")


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


# --- PNG ----------------------------------------------------------------------------------------

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


# --- WebP ---------------------------------------------------------------------------------------


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


# --- GIF ----------------------------------------------------------------------------------------


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


# --- ISO base media: MP4, MOV, HEIC, AVIF --------------------------------------------------------

_XMP_UUID = bytes.fromhex("be7acfcb97a942e89c71999491e3afac")
#: Boxes that hold other boxes and nothing else, walked to reach every place below them.
_CONTAINERS = frozenset({b"moov", b"trak", b"mdia", b"minf", b"udta", b"stbl", b"edts"})
#: Boxes that are a place and nothing else: emptied into `free` boxes of the same size.
_PLACE_BOXES = frozenset({b"\xa9xyz", b"loci"})
#: A timed track whose samples are positions.
_POSITION_TRACKS = frozenset({b"gpmd", b"camm"})


def _boxes(data: bytes, start: int, end: int) -> list[tuple[int, int, int, bytes]]:
    """Each box between `start` and `end`: where it starts, its header length, its end, its type."""
    found: list[tuple[int, int, int, bytes]] = []
    at = start
    while at + 8 <= end:
        size = _u32(data, at)
        kind = data[at + 4 : at + 8]
        header = 8
        if size == 1:
            size = _uint(data, at + 8, 8)
            header = 16
        elif size == 0:
            size = end - at
        if size < header or at + size > end:
            raise _Unreadable("a box runs past the one holding it")
        found.append((at, header, at + size, kind))
        at += size
    return found


def _emptied(at: int, header: int, end: int, base: int, data: bytes) -> _Splice:
    """The box at `at` as a `free` box of the same size, its inside zeroed."""
    head = data[at : at + 4] + b"free" + data[at + 8 : at + header]
    return _Splice(base + at, base + end, head + bytes(end - at - header))


def _meta_children(data: bytes, at: int, header: int, end: int) -> int:
    """Where a `meta` box's children start: ISO's `meta` is a full box (four bytes of version and
    flags first), QuickTime's is not, and a file carries either."""
    first = at + header
    if first + 8 <= end and _u32(data, first) >= 8 and data[first + 4 : first + 8].isalpha():
        return first
    return first + 4


def _moov(data: bytes, base: int, start: int, end: int, reader: _Source) -> list[_Splice]:
    """Every place inside a `moov` (or any box below it) read whole into `data`."""
    splices: list[_Splice] = []
    for at, header, stop, kind in _boxes(data, start, end):
        body = at + header
        if kind in _PLACE_BOXES:
            splices.append(_emptied(at, header, stop, base, data))
        elif kind == b"XMP_":
            splices += _xmp_splices(data[body:stop], base + body)
        elif kind == b"uuid" and data[body : body + 16] == _XMP_UUID:
            splices += _xmp_splices(data[body + 16 : stop], base + body + 16)
        elif kind == b"meta":
            splices += _quicktime_meta(data, base, at, header, stop)
        elif kind in _CONTAINERS:
            if kind == b"trak" and _is_position_track(data, body, stop):
                splices += _zeroed_samples(data, body, stop, reader)
            splices += _moov(data, base, body, stop, reader)
    return splices


def _quicktime_meta(data: bytes, base: int, at: int, header: int, end: int) -> list[_Splice]:
    """A `meta` box: an Apple `keys` table and the `ilst` it indexes, or iTunes items by name."""
    children = _boxes(data, _meta_children(data, at, header, end), end)
    placed: set[int] = set()
    for one, head, stop, kind in children:
        if kind == b"keys":
            placed |= _place_keys(data[one + head : stop])
    splices: list[_Splice] = []
    for one, head, stop, kind in children:
        if kind != b"ilst":
            continue
        for item, item_head, item_stop, item_kind in _boxes(data, one + head, stop):
            index = int.from_bytes(item_kind, "big")
            if item_kind in _PLACE_BOXES or index in placed:
                splices.append(_emptied(item, item_head, item_stop, base, data))
            elif item_kind == b"covr":
                for inner, inner_head, inner_stop, inner_kind in _boxes(
                    data, item + item_head, item_stop
                ):
                    if inner_kind == b"data":
                        # Eight bytes of type and locale before the picture.
                        picture = inner + inner_head + 8
                        splices += _picture(data[picture:inner_stop], base + picture)
    return splices


def _place_keys(keys: bytes) -> set[int]:
    """The 1-based indexes of the keys in a `keys` table (after its version) that name a place."""
    count = _u32(keys, 4)
    at = 8
    placed: set[int] = set()
    for index in range(1, count + 1):
        size = _u32(keys, at)
        if size < 8 or at + size > len(keys):
            raise _Unreadable("a metadata key runs past its table")
        if names_a_place(keys[at + 8 : at + size].decode("utf-8", "replace")):
            placed.add(index)
        at += size
    return placed


def _find(data: bytes, start: int, end: int, path: tuple[bytes, ...]) -> tuple[int, int] | None:
    """The body of the box reached by `path` from the boxes between `start` and `end`."""
    for at, header, stop, kind in _boxes(data, start, end):
        if kind == path[0]:
            if len(path) == 1:
                return at + header, stop
            return _find(data, at + header, stop, path[1:])
    return None


def _is_position_track(data: bytes, start: int, end: int) -> bool:
    described = _find(data, start, end, (b"mdia", b"minf", b"stbl", b"stsd"))
    if described is None or described[1] - described[0] < 16:
        return False
    body, stop = described
    entry = data[body + 12 : body + 16]
    if entry in _POSITION_TRACKS:
        return True
    return entry == b"mebx" and names_a_place(data[body:stop].decode("latin-1"))


def _zeroed_samples(data: bytes, start: int, end: int, reader: _Source) -> list[_Splice]:
    """Every sample of a track, zeroed where it lies in the file: the table stays, the positions go."""
    table = _find(data, start, end, (b"mdia", b"minf", b"stbl"))
    if table is None:  # pragma: no cover (the track's description was found in this table)
        raise _Unreadable("a track has no sample table")
    boxes = {kind: (at + header, stop) for at, header, stop, kind in _boxes(data, *table)}
    if b"stsz" not in boxes or b"stsc" not in boxes:
        raise _Unreadable("a track's sample table is not one Sift can read")
    body, _stop = boxes[b"stsz"]
    fixed, count = _u32(data, body + 4), _u32(data, body + 8)
    sizes = [fixed] * count if fixed else [_u32(data, body + 12 + 4 * n) for n in range(count)]
    body, _stop = boxes[b"stsc"]
    runs = [
        (_u32(data, body + 8 + 12 * n), _u32(data, body + 12 + 12 * n))
        for n in range(_u32(data, body + 4))
    ]
    if b"stco" in boxes:
        body, _stop = boxes[b"stco"]
        offsets = [_u32(data, body + 8 + 4 * n) for n in range(_u32(data, body + 4))]
    elif b"co64" in boxes:
        body, _stop = boxes[b"co64"]
        offsets = [_uint(data, body + 8 + 8 * n, 8) for n in range(_u32(data, body + 4))]
    else:
        raise _Unreadable("a track has no chunk offsets")
    splices: list[_Splice] = []
    sample = 0
    for chunk, offset in enumerate(offsets, start=1):
        per_chunk = next((n for first, n in reversed(runs) if first <= chunk), 0)
        length = sum(sizes[sample : sample + per_chunk])
        sample += per_chunk
        if length:
            if offset + length > reader.size:
                raise _Unreadable("a sample runs past the file")
            # Samples already zeroed are left alone, so a copy read again finds nothing to edit.
            if reader.read(offset, length).strip(b"\x00"):
                splices.append(_Splice(offset, offset + length, bytes(length)))
    return splices


def _heif_items(payload: bytes) -> dict[int, bytes]:
    """Each item's id and type from an `iinf` payload (after its header)."""
    version = payload[0]
    count_size = 2 if version == 0 else 4
    items: dict[int, bytes] = {}
    for at, header, end, kind in _boxes(payload, 4 + count_size, len(payload)):
        if kind != b"infe":
            continue
        body = payload[at + header : end]
        infe_version = body[0] if body else 0
        if infe_version >= 2:
            id_size = 2 if infe_version == 2 else 4
            items[_uint(body, 4, id_size)] = body[4 + id_size + 2 : 4 + id_size + 6]
    return items


def _heif_extents(payload: bytes) -> dict[int, tuple[int, list[tuple[int, int]]]]:
    """Each item's construction method and its extents (offset, length) from an `iloc` payload."""
    version = payload[0]
    offset_size, length_size = payload[4] >> 4, payload[4] & 0x0F
    base_size = payload[5] >> 4
    index_size = payload[5] & 0x0F if version in (1, 2) else 0
    id_size, count_size = (2, 2) if version < 2 else (4, 4)
    count = _uint(payload, 6, count_size)
    at = 6 + count_size
    found: dict[int, tuple[int, list[tuple[int, int]]]] = {}
    for _ in range(count):
        item = _uint(payload, at, id_size)
        at += id_size
        method = 0
        if version in (1, 2):
            method = _uint(payload, at, 2) & 0x0F
            at += 2
        at += 2  # the data reference index
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
        found[item] = (method, spans)
    return found


def _heif_meta(reader: _Source, at: int, header: int, end: int) -> list[_Splice]:
    """A HEIF's `Exif` items (the GPS directory) and XMP items (the place properties)."""
    data = reader.read(at, end - at)
    children = {
        kind: (one, head, stop) for one, head, stop, kind in _boxes(data, header + 4, len(data))
    }
    if b"iinf" not in children or b"iloc" not in children:
        return []
    one, head, stop = children[b"iinf"]
    items = _heif_items(data[one + head : stop])
    one, head, stop = children[b"iloc"]
    extents = _heif_extents(data[one + head : stop])
    idat = children.get(b"idat")
    splices: list[_Splice] = []
    for item, kind in items.items():
        if kind not in (b"Exif", b"mime") or item not in extents:
            continue
        method, spans = extents[item]
        if method == 1:
            if idat is None:
                raise _Unreadable("a HEIF item is in an idat that is not there")
            spans = [(at + idat[0] + idat[1] + offset, length) for offset, length in spans]
        elif method != 0:
            continue
        if any(length == 0 for _offset, length in spans):
            raise _Unreadable("a HEIF metadata item runs to the end of the file")
        whole = b"".join(reader.read(offset, length) for offset, length in spans)
        if kind == b"Exif":
            skip = 4 + _u32(whole, 0)
            edits = _exif_splices(whole[skip:], skip)
        else:
            edits = _xmp_splices(whole, 0)
        splices += _spread(edits, whole, spans)
    return splices


def _spread(edits: list[_Splice], whole: bytes, spans: list[tuple[int, int]]) -> list[_Splice]:
    """Edits made to an item's bytes read as one run, put back into the extents they came from."""
    if not edits:
        return []
    rewritten = bytearray(whole)
    for one in edits:
        rewritten[one.start : one.end] = one.data
    splices: list[_Splice] = []
    at = 0
    for offset, length in spans:
        splices.append(_Splice(offset, offset + length, bytes(rewritten[at : at + length])))
        at += length
    return splices


def _isobmff(reader: _Source) -> list[_Splice]:
    splices: list[_Splice] = []
    at = 0
    while at + 8 <= reader.size:
        head = reader.read(at, 8)
        size, kind = _u32(head, 0), head[4:8]
        header = 8
        if size == 1:
            size = _uint(reader.read(at + 8, 8), 0, 8)
            header = 16
        elif size == 0:
            size = reader.size - at
        if size < header or at + size > reader.size:
            raise _Unreadable("a box runs past the end of the file")
        if kind in (b"moov", b"udta"):
            splices += _moov(reader.read(at, size), at, 0, size, reader)
        elif kind == b"meta":
            splices += _heif_meta(reader, at, header, at + size)
        elif kind == b"uuid" and reader.read(at + header, 16) == _XMP_UUID:
            body = at + header + 16
            splices += _xmp_splices(reader.read(body, at + size - body), body)
        at += size
    return splices


# --- Matroska and WebM ---------------------------------------------------------------------------

_SEGMENT = 0x18538067
_CLUSTER = 0x1F43B675
_TAGS = 0x1254C367
_TAG = 0x7373
_SIMPLE_TAG = 0x67C8
_TAG_NAME = 0x45A3
_ATTACHMENTS = 0x1941A469
_ATTACHED_FILE = 0x61A7
_FILE_DATA = 0x465C
_CRC32 = 0xBF
_VOID = 0xEC
#: The elements that sit directly in a Segment: what ends a Cluster whose size is unknown.
_TOP_LEVEL = frozenset(
    {0x114D9B74, 0x1549A966, 0x1654AE6B, _CLUSTER, 0x1C53BB6B, _ATTACHMENTS, 0x1043A770, _TAGS}
)
_UNKNOWN = -1


def _vint(data: bytes, at: int, *, keep_marker: bool) -> tuple[int, int]:
    """An EBML variable-length number at `at`: its value and its length in bytes."""
    if at >= len(data):
        raise _Unreadable("an element runs past its parent")
    first = data[at]
    length = 1
    while length <= 8 and not first & (0x80 >> (length - 1)):
        length += 1
    if length > 8 or at + length > len(data):
        raise _Unreadable("an element's size is not one Sift can read")
    value = int.from_bytes(data[at : at + length], "big")
    if keep_marker:
        return value, length
    value &= (1 << (7 * length)) - 1
    if value == (1 << (7 * length)) - 1:
        return _UNKNOWN, length
    return value, length


def _element(data: bytes, at: int) -> tuple[int, int, int]:
    """An element's id, where its data starts and its size (or `_UNKNOWN`)."""
    ident, id_length = _vint(data, at, keep_marker=True)
    size, size_length = _vint(data, at + id_length, keep_marker=False)
    return ident, at + id_length + size_length, size


def _void(length: int) -> bytes:
    """A Void element exactly `length` bytes long, zeroed inside."""
    if length >= 9:
        return b"\xec\x01" + (length - 9).to_bytes(7, "big") + bytes(length - 9)
    # A SimpleTag naming a place is nine bytes at the least: no shorter Void is ever asked for.
    return bytes((_VOID, 0x80 | (length - 2))) + bytes(length - 2)  # pragma: no cover


def _children(data: bytes, start: int, end: int) -> list[tuple[int, int, int, int]]:
    """Each element between `start` and `end`: id, start, data start, end."""
    found: list[tuple[int, int, int, int]] = []
    at = start
    while at < end:
        ident, body, size = _element(data, at)
        if size == _UNKNOWN or body + size > end:
            raise _Unreadable("an element runs past its parent")
        found.append((ident, at, body, body + size))
        at = body + size
    return found


def _voided(data: bytearray, start: int, end: int) -> bool:
    """Void every SimpleTag between `start` and `end` whose name is a place, at any depth, and
    write each CRC-32 the change invalidates again. Answers whether anything changed."""
    changed = False
    elements = _children(bytes(data), start, end)
    for ident, at, body, stop in elements:
        if ident == _SIMPLE_TAG and _tag_is_place(bytes(data[body:stop])):
            data[at:stop] = _void(stop - at)
            changed = True
        elif ident in (_TAG, _SIMPLE_TAG) and _voided(data, body, stop):
            changed = True
    if changed and elements and elements[0][0] == _CRC32:
        _ident, _at, body, stop = elements[0]
        crc = zlib.crc32(bytes(data[stop:end])) & 0xFFFFFFFF
        data[body:stop] = crc.to_bytes(4, "little")
    return changed


def _tag_is_place(simple_tag: bytes) -> bool:
    for ident, _at, body, stop in _children(simple_tag, 0, len(simple_tag)):
        if ident == _TAG_NAME:
            return names_a_place(simple_tag[body:stop].decode("utf-8", "replace"))
    return False


def _attachments(data: bytes, base: int) -> list[_Splice]:
    splices: list[_Splice] = []
    for ident, _at, body, stop in _children(data, 0, len(data)):
        if ident != _ATTACHED_FILE:
            continue
        for inner, _inner_at, inner_body, inner_stop in _children(data, body, stop):
            if inner == _FILE_DATA:
                splices += _picture(data[inner_body:inner_stop], base + inner_body)
    return splices


def _matroska(reader: _Source) -> list[_Splice]:
    splices: list[_Splice] = []
    at = 0
    while at < reader.size:
        ident, body, size = _element(reader.read(at, min(12, reader.size - at)), 0)
        body += at
        end = reader.size if size == _UNKNOWN else body + size
        if end > reader.size:
            raise _Unreadable("an element runs past the end of the file")
        if ident == _SEGMENT:
            splices += _segment(reader, body, end)
        at = end
    return splices


def _segment(reader: _Source, start: int, end: int) -> list[_Splice]:
    splices: list[_Splice] = []
    at = start
    while at < end:
        ident, body, size = _element(reader.read(at, min(12, end - at)), 0)
        body += at
        if size == _UNKNOWN:
            if ident != _CLUSTER:
                raise _Unreadable("an element of unknown size is not a cluster")
            at = _past_open_cluster(reader, body, end)
            continue
        stop = body + size
        if stop > end:
            raise _Unreadable("an element runs past its segment")
        if ident == _TAGS:
            tags = bytearray(reader.read(at, stop - at))
            if _voided(tags, body - at, len(tags)):
                splices.append(_Splice(at, stop, bytes(tags)))
        elif ident == _ATTACHMENTS:
            splices += _attachments(reader.read(body, size), body)
        at = stop
    return splices


def _past_open_cluster(reader: _Source, start: int, end: int) -> int:
    """Where a cluster written without a size ends: at the next element that is not inside one."""
    at = start
    while at < end:
        ident, body, size = _element(reader.read(at, min(12, end - at)), 0)
        if ident in _TOP_LEVEL:
            return at
        if size == _UNKNOWN:
            raise _Unreadable("a block of unknown size inside a cluster")
        at += body + size
    return end


# --- the door --------------------------------------------------------------------------------------

_WHOLE: dict[str, Callable[[bytes], list[_Splice]]] = {
    "jpeg": _jpeg,
    "png": _png,
    "webp": _webp,
    "gif": _gif,
}
_WALKED: dict[str, Callable[[_Source], list[_Splice]]] = {
    "heif": _isobmff,
    "isobmff-video": _isobmff,
    "matroska": _matroska,
}


def _media(path: Path) -> MediaType | None:
    head, tail, _size = read_ends(path)
    found = classify(head, tail)
    return found if isinstance(found, MediaType) else None


def _plan(path: Path) -> tuple[str, list[_Splice]]:
    """The file's family and every edit that takes its places out. Blocking; reads only."""
    media = _media(path)
    if media is None:
        return "", []
    size = path.stat().st_size
    try:
        with path.open("rb") as handle:
            if media.family in _WHOLE:
                if size > _READ_CAP:
                    raise _Unreadable("a picture is too large to examine")
                splices = _WHOLE[media.family](handle.read())
            else:
                splices = _WALKED[media.family](_Source(handle, size))
    except (_Unreadable, IndexError, struct.error) as error:
        raise CannotRemovePlaces(str(error)) from error
    ordered = sorted(splices, key=lambda one: one.start)
    for before, after in pairwise(ordered):
        if after.start < before.end:
            raise CannotRemovePlaces("two edits overlap")
    return media.family, ordered


def places_in(path: Path) -> int:
    """How many edits a copy of this file needs to carry no place. 0 is a file with none."""
    return len(_plan(path)[1])


def size_without_places(path: Path) -> int | None:
    """How many bytes the copy `remove_places` would make is, or None when the file carries no
    place and leaves as it is. Blocking; reads only, and writes nothing.

    For a caller that has to say a copy's size before the copy exists: a drag out of the window
    hands the receiving program the size before the bytes. Raises `CannotRemovePlaces` exactly
    where `remove_places` would.
    """
    _family, splices = _plan(path)
    if not splices:
        return None
    return path.stat().st_size + sum(len(one.data) - (one.end - one.start) for one in splices)


def _write(source: Path, destination: Path, splices: list[_Splice]) -> None:
    with source.open("rb") as reading, destination.open("xb") as writing:
        at = 0
        for one in splices:
            _copy_range(reading, writing, at, one.start)
            writing.write(one.data)
            at = one.end
        reading.seek(at)
        shutil.copyfileobj(reading, writing, 4 * 1024 * 1024)


def _copy_range(reading: BinaryIO, writing: BinaryIO, start: int, end: int) -> None:
    reading.seek(start)
    left = end - start
    while left > 0:
        block = reading.read(min(left, 4 * 1024 * 1024))
        if not block:
            raise CannotRemovePlaces("the file is shorter than it was")
        writing.write(block)
        left -= len(block)


#: The folder of Sift's own cache a copy waits in while it is handed to somebody: every route that
#: hands a file out through this door writes its copy under it, never beside the file in a library.
OUTGOING_FOLDER = "outgoing"

#: What the directory a copy is written into is called, so `discard` removes nothing else.
_COPY_PREFIX = "unplaced-"


def remove_places(source: Path, scratch: Path) -> Path | None:
    """A copy of `source` with no place in it, or None when `source` carries none. Blocking.

    None means the file can be handed out as it is: nothing is written. Otherwise the copy is a new
    file under a new directory in `scratch`, named as the source is (so whatever takes it in names
    it the same), and the caller hands it to `discard` once it is done with it. The source is opened
    for reading and nothing else, whoever calls this and wherever it lies.

    Raises `CannotRemovePlaces` when the file's tables cannot be read far enough to be sure, and
    when the copy, read again, still holds a place.
    """
    family, splices = _plan(source)
    if not splices:
        return None
    scratch.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=_COPY_PREFIX, dir=scratch))
    copy = folder / source.name
    try:
        _write(source, copy, splices)
        if _plan(copy)[1]:
            raise CannotRemovePlaces("a place is still there after it was taken out")
    except BaseException:
        discard(copy)
        raise
    log.info("places.removed", family=family, edits=len(splices))
    return copy


def remove_places_from_own(built: Path) -> bool:
    """Take every place out of a file Sift itself has just built and nobody else holds yet (a
    produced file in its scratch name, before it is given its real one). Blocking.

    The one function here that changes a file where it lies, so it is for exactly that case: the
    copy is written beside it and moved over it in one rename. `tests/gates/
    test_nothing_sift_writes_carries_a_location.py` holds the list of places that may call it, and
    none of them is ever handed a file from somebody's library. Answers whether anything changed.
    """
    family, splices = _plan(built)
    if not splices:
        return False
    copy = built.with_name(built.name + ".unplaced")
    try:
        _write(built, copy, splices)
        if _plan(copy)[1]:
            raise CannotRemovePlaces("a place is still there after it was taken out")
        copy.replace(built)
    except BaseException:
        copy.unlink(missing_ok=True)  # nosemgrep: sift-no-file-removal-outside-delete-trash
        raise
    log.info("places.removed", family=family, edits=len(splices))
    return True


def discard(copy: Path) -> None:
    """Remove a copy `remove_places` made, and the directory it made for it. Blocking.

    Refuses anything else: a path whose directory is not one of the door's own is left alone."""
    if not copy.parent.name.startswith(_COPY_PREFIX):
        raise ValueError("not a copy this door made")
    # The door's own directory under Sift's own scratch folder, checked by name on the line above.
    shutil.rmtree(  # nosemgrep: sift-no-file-removal-outside-delete-trash
        copy.parent, ignore_errors=True
    )
