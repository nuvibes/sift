# SPDX-License-Identifier: AGPL-3.0-or-later
"""What moves: the sender's look and strip, the 4 MiB chunks with their digests, the receiver's
staging.

## A file with no location goes as it is; a file with one is stripped first

The rule a swap holds is the kernel's: no location leaves this device (`sift.kernel.places`). Most
files carry none, so each file is LOOKED AT first, with the location door's own reading of its
tables (`places.places_in`, a few milliseconds of header reads), and a file with no place in it is
sent from where it lies: no copy, its chunks read from the original through its storage's lane one
at a time. Only a file that carries a place, or whose tables cannot be read far enough to be sure,
is stripped into a copy (below) and sent from that.

Stripping every file of ANY of its container's metadata (a title, an encoder, a creation time) is
the rule this deliberately does not follow. It would mean a whole copy of every file: a stream copy
of a large video takes several times as long as reading it, and on a network share each copy holds
one of the share's reading places for the whole of it, so the streams would queue behind their own
copies. The camera, the time and the title are what Sift hands out everywhere else (save to device,
a drag out of the window: the location door takes the place and nothing more), and a swap agrees
with them.

An original sent as it is has its size and its modification time read before its first chunk goes
(`Prepared.stamp`). Every chunk read checks them again, so a file changed during the swap fails
rather than arriving as a mix of two versions, and the guest's whole check refuses anything that
slips past. The whole digest is read up front only for a receiver that checks it first
(`with_digest`); see `pieces` for the check at the end.

## The strip, for a file that carries a place, and why it is not only ffmpeg

A file stripped leaves this device without its container's metadata: a title, an encoder, a creation
time, a location an editor or a phone wrote in. For a video that is a stream copy: the pictures and
the sound are copied, not re-encoded, so the file the guest gets plays exactly as this one does:

    -map 0 -map -0:d -map -0:t -c copy -map_metadata -1 -map_metadata:s -1 -map_chapters -1
    -fflags +bitexact

`-fflags +bitexact` stops the muxer writing its own encoder tag in place of the one removed.
`-map -0:d -map -0:t` leaves out data and attachment streams: a phone's timed metadata track can
carry where it was filmed, frame by frame, and an attachment is a file inside the file. (Both
negative maps are accepted by ffmpeg when the file has no such stream.)

!! A STREAM COPY DOES NOT STRIP A PHOTOGRAPH. A JPEG put through exactly that command comes out with
its EXIF block and its comment untouched, because a JPEG's metadata lives inside the picture's own
bytes, which a copy copies. A phone photograph's EXIF holds where it was taken: for a photo taken
at home, the home's address, which is the one thing a swap exists never to reveal. So each picture
format is stripped by reading its own structure and keeping only what drawing it needs
(`strip_jpeg`, `strip_png`, `strip_webp`,
`strip_gif`, `strip_heif`); nothing is re-encoded, so the picture is the same picture. A JPEG keeps its
orientation (a fresh EXIF block holding that one tag and nothing else), since without it a phone
photograph arrives on its side.

HEIC and AVIF are sent too, stripped by rewriting their item tables (`strip_heif`): Sift holds
these pictures, and a swap carries what Sift holds. The writer is below, and its test builds a
picture holding an EXIF item and an XMP item and proves the coded bytes come out equal and at the
same offsets. A HEIF whose tables cannot be read is refused (`CannotStrip`) rather than sent as it
is.

## Chunks

A file goes as its size first, then 4 MiB chunks, each with its own BLAKE3. The receiver writes each verified chunk into place in a staged file and records it in the
file's manifest, so a dropped connection costs the chunk in flight and nothing else.
"""

from __future__ import annotations

import asyncio
import math
import os
import struct
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from blake3 import blake3

from sift.kernel import lanes, media, places
from sift.kernel.config import Settings
from sift.kernel.ingress import ALLOWED_MEDIA, MediaType, classify, read_ends
from sift.kernel.subprocess import Priority

#: The size of every chunk but a file's last.
CHUNK_SIZE = 4 * 1024 * 1024

#: How many times a chunk whose digest disagrees is asked for AGAIN before its file fails.
MAX_RETRIES = 3

#: The largest picture read whole into memory to be stripped. A picture past it is not sent.
_PICTURE_CAP = 512 * 1024 * 1024

#: ffmpeg's container name for each video format Sift accepts.
_MUXERS = {"mp4": "mp4", "mov": "mov", "mkv": "matroska", "webm": "webm"}

#: The strip's command after the input, as the module docstring explains.
STRIP_FLAGS: tuple[str, ...] = (
    "-map",
    "0",
    "-map",
    "-0:d",
    "-map",
    "-0:t",
    "-c",
    "copy",
    "-map_metadata",
    "-1",
    "-map_metadata:s",
    "-1",
    "-map_chapters",
    "-1",
    "-fflags",
    "+bitexact",
)


class CannotStrip(Exception):
    """This file cannot be sent without what it says about where it came from. It is not sent."""


class Changed(OSError):
    """An original sent as it is changed after it was measured for sending. It is not sent."""


# --- chunks ----------------------------------------------------------------------------------


def chunk_count(size: int, chunk_size: int = CHUNK_SIZE) -> int:
    """How many chunks a file of `size` bytes is sent in."""
    if size < 0 or chunk_size <= 0:
        raise ValueError("a size is not negative and a chunk is not empty")
    return math.ceil(size / chunk_size)


def chunk_length(size: int, index: int, chunk_size: int = CHUNK_SIZE) -> int:
    """How many bytes chunk `index` of a file of `size` bytes holds."""
    if not 0 <= index < chunk_count(size, chunk_size):
        raise ValueError("no such chunk")
    return min(chunk_size, size - index * chunk_size)


def missing(done: Iterable[int], count: int) -> list[int]:
    """The chunks still to come, first missing first: where a dropped file resumes."""
    have = set(done)
    return [index for index in range(count) if index not in have]


def chunk_digest(data: bytes) -> bytes:
    return blake3(data).digest()


def digest_file(path: Path) -> str:
    """The whole file's BLAKE3, hex. Blocking: call it off the loop."""
    hasher = blake3()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK_SIZE):
            hasher.update(block)
    return hasher.hexdigest()


def read_chunk(path: Path, index: int, size: int, chunk_size: int = CHUNK_SIZE) -> bytes:
    """One chunk of a stripped file. Blocking."""
    length = chunk_length(size, index, chunk_size)
    with path.open("rb") as handle:
        handle.seek(index * chunk_size)
        data = handle.read(length)
    if len(data) != length:
        raise OSError("the stripped file is shorter than it was")
    return data


def write_chunk(path: Path, index: int, data: bytes, chunk_size: int = CHUNK_SIZE) -> None:
    """Write one verified chunk into its place in a staged file. Blocking.

    The file is opened without truncation whether or not it exists yet: streams carrying shares of
    one file write their first chunks at the same moment, and a look followed by a truncating
    create lets the second create empty the chunk the first has just written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o666)
    with os.fdopen(fd, "r+b") as handle:
        handle.seek(index * chunk_size)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def staged_path(root: Path, session_id: str, file_key: str) -> Path:
    """Where a received file's chunks are written: under the session, named by a digest of the key
    rather than the key or anything the peer said, so nothing a peer sends becomes a path."""
    return root / session_id / (blake3(file_key.encode("utf-8")).hexdigest()[:32] + ".part")


# --- the strip -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Prepared:
    """A file ready to send: where its bytes are read from, their size, and their whole digest once
    `with_digest` has read it.

    `copy` says whose file `path` is. A stripped copy is this slice's own, removed once the file is
    settled; an original sent as it is (`copy` False) is somebody's library file, read and never
    removed, and its `stamp` (size, modification time in nanoseconds) is checked at every chunk. A
    copy carries its original's stamp, which names the version sent.
    """

    path: Path
    size: int
    digest: str | None = None
    copy: bool = True
    stamp: tuple[int, int] | None = None


def _segment_ok(marker: int, payload: bytes) -> bool:
    """Whether a JPEG APPn segment is needed to draw the picture. Only these three are."""
    if marker == 0xE0:
        return payload.startswith((b"JFIF\x00", b"JFXX\x00"))
    if marker == 0xE2:
        return payload.startswith(b"ICC_PROFILE\x00")
    if marker == 0xEE:
        return payload.startswith(b"Adobe")
    return False


def _orientation(exif: bytes) -> int | None:
    """The Orientation tag (0x0112) of an EXIF block's first directory, or None."""
    if not exif.startswith(b"Exif\x00\x00") or len(exif) < 14:
        return None
    tiff = exif[6:]
    order = tiff[:2]
    if order == b"II":
        end = "<"
    elif order == b"MM":
        end = ">"
    else:
        return None
    try:
        (offset,) = struct.unpack(end + "I", tiff[4:8])
        (entries,) = struct.unpack(end + "H", tiff[offset : offset + 2])
        for n in range(entries):
            at = offset + 2 + n * 12
            tag, kind, count = struct.unpack(end + "HHI", tiff[at : at + 8])
            if tag == 0x0112 and kind == 3 and count == 1:
                (value,) = struct.unpack(end + "H", tiff[at + 8 : at + 10])
                return value if 1 <= value <= 8 else None
    except struct.error:
        return None
    return None


def _orientation_only(value: int) -> bytes:
    """A whole APP1 segment holding one EXIF tag, Orientation, and nothing else."""
    tiff = b"MM\x00\x2a" + struct.pack(">I", 8) + struct.pack(">H", 1)
    tiff += struct.pack(">HHIHH", 0x0112, 3, 1, value, 0) + struct.pack(">I", 0)
    payload = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def strip_jpeg(data: bytes) -> bytes:
    """A JPEG with every APPn segment but JFIF, ICC and Adobe removed, and every comment, and
    everything after its end marker (a motion photo's appended video) left behind."""
    if data[:2] != b"\xff\xd8":
        raise CannotStrip("not a JPEG")
    out = bytearray(b"\xff\xd8")
    size = len(data)
    at = 2
    orientation: int | None = None
    placed = False
    while at < size:
        if data[at] != 0xFF:
            raise CannotStrip("a JPEG segment did not start where it should")
        while at < size and data[at] == 0xFF:
            at += 1
        if at >= size:
            break
        marker = data[at]
        at += 1
        if marker == 0xD9:
            out += b"\xff\xd9"
            return bytes(out)
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            out += bytes((0xFF, marker))
            continue
        if at + 2 > size:
            break
        (length,) = struct.unpack(">H", data[at : at + 2])
        end = at + length
        if length < 2 or end > size:
            raise CannotStrip("a JPEG segment runs past the file")
        payload = data[at + 2 : end]
        if 0xE0 <= marker <= 0xEF:
            if marker == 0xE1 and orientation is None:
                orientation = _orientation(payload)
            if _segment_ok(marker, payload):
                out += bytes((0xFF, marker)) + data[at:end]
        elif marker != 0xFE:
            # The first segment that is not an APPn or a comment is where the picture's own
            # tables begin: the orientation goes in just before it, where an EXIF block sits.
            if not placed and orientation is not None and orientation != 1:
                out += _orientation_only(orientation)
            placed = True
            out += bytes((0xFF, marker)) + data[at:end]
        at = end
        if marker == 0xDA:
            scan = at
            while True:
                found = data.find(b"\xff", scan)
                if found < 0 or found + 1 >= size:
                    raise CannotStrip("a JPEG ends inside its picture")
                following = data[found + 1]
                if following == 0x00 or 0xD0 <= following <= 0xD7:
                    scan = found + 2
                    continue
                if following == 0xFF:
                    scan = found + 1
                    continue
                break
            out += data[at:found]
            at = found
    raise CannotStrip("a JPEG has no end")


#: The PNG chunks drawing needs: the picture, its palette and transparency, its color, and the
#: animation chunks of an APNG. Every text chunk, EXIF chunk and time stamp is left behind.
_PNG_KEEP = frozenset(
    {
        b"IHDR",
        b"PLTE",
        b"IDAT",
        b"IEND",
        b"tRNS",
        b"cHRM",
        b"gAMA",
        b"iCCP",
        b"sBIT",
        b"sRGB",
        b"cICP",
        b"mDCV",
        b"cLLI",
        b"bKGD",
        b"pHYs",
        b"acTL",
        b"fcTL",
        b"fdAT",
    }
)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def strip_png(data: bytes) -> bytes:
    """A PNG with only the chunks drawing needs, each copied whole with its own checksum."""
    if not data.startswith(_PNG_SIGNATURE):
        raise CannotStrip("not a PNG")
    out = bytearray(_PNG_SIGNATURE)
    at = len(_PNG_SIGNATURE)
    while at + 12 <= len(data):
        (length,) = struct.unpack(">I", data[at : at + 4])
        kind = data[at + 4 : at + 8]
        end = at + 12 + length
        if end > len(data):
            break
        if kind in _PNG_KEEP:
            out += data[at:end]
        at = end
        if kind == b"IEND":
            return bytes(out)
    raise CannotStrip("a PNG has no end")


_WEBP_KEEP = frozenset({b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ANIM", b"ANMF", b"ICCP"})
#: The VP8X flags that say an EXIF or an XMP chunk follows. Cleared along with the chunks.
_VP8X_EXIF = 0x08
_VP8X_XMP = 0x04


def strip_webp(data: bytes) -> bytes:
    """A WebP with its EXIF and XMP chunks removed and the flags that announced them cleared."""
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise CannotStrip("not a WebP")
    body = bytearray()
    at = 12
    while at + 8 <= len(data):
        kind = data[at : at + 4]
        (length,) = struct.unpack("<I", data[at + 4 : at + 8])
        end = at + 8 + length + (length & 1)
        if at + 8 + length > len(data):
            raise CannotStrip("a WebP chunk runs past the file")
        chunk = bytearray(data[at : min(end, len(data))])
        if len(chunk) < end - at:
            chunk += b"\x00" * (end - at - len(chunk))
        if kind == b"VP8X" and length >= 1:
            chunk[8] &= ~(_VP8X_EXIF | _VP8X_XMP) & 0xFF
        if kind in _WEBP_KEEP:
            body += chunk
        at = end
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + bytes(body)


#: The application extensions a GIF needs: the loop count, under either of its names.
_GIF_LOOPS = (b"NETSCAPE2.0", b"ANIMEXTS1.0")


def _gif_blocks(data: bytes, at: int) -> int:
    """The index just past a run of GIF data sub-blocks starting at `at`."""
    while True:
        if at >= len(data):
            raise CannotStrip("a GIF ends inside a block")
        length = data[at]
        at += 1 + length
        if length == 0:
            return at


def strip_gif(data: bytes) -> bytes:
    """A GIF with its comments, plain-text blocks and every application extension but the loop
    count removed."""
    if data[:6] not in (b"GIF87a", b"GIF89a") or len(data) < 13:
        raise CannotStrip("not a GIF")
    flags = data[10]
    at = 13 + (3 * (2 ** ((flags & 0x07) + 1)) if flags & 0x80 else 0)
    out = bytearray(data[:at])
    while at < len(data):
        introducer = data[at]
        if introducer == 0x3B:
            out += b"\x3b"
            return bytes(out)
        if introducer == 0x2C:
            if at + 10 > len(data):
                break
            local = data[at + 9]
            start = at
            at += 10 + (3 * (2 ** ((local & 0x07) + 1)) if local & 0x80 else 0)
            at = _gif_blocks(data, at + 1)
            out += data[start:at]
            continue
        if introducer == 0x21 and at + 1 < len(data):
            label = data[at + 1]
            start = at
            end = _gif_blocks(data, at + 2)
            keep = label == 0xF9
            if label == 0xFF and at + 3 < len(data) and data[at + 2] == 11:
                keep = data[at + 3 : at + 14] in _GIF_LOOPS
            if keep:
                out += data[start:end]
            at = end
            continue
        raise CannotStrip("a GIF block is not one Sift can read")
    raise CannotStrip("a GIF has no end")


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
        body = data[one + head : stop]
        if kind == b"iinf":
            count_size = 2 if body[0] == 0 else 4
            body = body[:4] + len(items).to_bytes(count_size, "big") + b"".join(items)
        elif kind == b"iloc":
            body = locations
        elif kind == b"iref":
            body = _heif_references(body, dropped)
        elif kind == b"idat":
            held = bytearray(body)
            for offset, length in in_idat:
                if offset + length > len(held):
                    raise CannotStrip("a HEIF metadata item runs past its data")
                held[offset : offset + length] = bytes(length)
            body = bytes(held)
        elif kind == b"iprp":
            props = bytearray()
            for inner, inner_head, inner_stop, inner_kind in _boxes(body, 0, len(body)):
                inner_body = body[inner + inner_head : inner_stop]
                if inner_kind == b"ipma":
                    inner_body = _heif_associations(inner_body, dropped)
                props += _box(inner_kind, inner_body)
            body = bytes(props)
        rebuilt += _box(kind, body)
    return _box(b"meta", data[at + header : start] + bytes(rebuilt)), in_file


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


_PICTURE_STRIPS = {
    "jpeg": strip_jpeg,
    "png": strip_png,
    "webp": strip_webp,
    "webp-animated": strip_webp,
    "gif": strip_gif,
    "heic": strip_heif,
    "avif": strip_heif,
    "heic-sequence": strip_heif,
    "avif-sequence": strip_heif,
}


def _kind_of(source: Path) -> MediaType:
    head, tail, _size = read_ends(source)
    found = classify(head, tail)
    if not isinstance(found, MediaType):
        raise CannotStrip("this file is not one Sift sends")
    return found


def _strip_picture(source: Path, destination: Path, name: str) -> None:
    if source.stat().st_size > _PICTURE_CAP:
        raise CannotStrip("this picture is too large to strip")
    stripped = _PICTURE_STRIPS[name](source.read_bytes())
    destination.write_bytes(stripped)


def strip_argv(settings: Settings, source: Path, destination: Path, muxer: str) -> list[str]:
    """The stream copy, as a command. Separate so the flags can be read by a test."""
    return [
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        *STRIP_FLAGS,
        "-f",
        muxer,
        str(destination),
    ]


#: The names this slice gives the files it writes: a stripped copy and a staged file. `remove`
#: takes nothing else, so an original sent as it is can never be handed to it by mistake.
OWN_SUFFIXES = (".strip", ".part")


def remove(path: Path) -> None:
    """Remove a file this slice wrote: a stripped copy or a staged file, never anybody's. Blocking.

    Refuses any other name: an original a swap sends as it is goes through the same session code
    as a stripped copy, and a library file must not be one slip away from deletion."""
    if path.suffix not in OWN_SUFFIXES:
        raise ValueError("not a file a swap wrote")
    path.unlink(missing_ok=True)  # nosemgrep: sift-no-file-removal-outside-delete-trash


def _begin(source: Path, workdir: Path) -> tuple[MediaType, Path, int]:
    """What the file is, a new empty temporary file for its stripped copy, and its size. Blocking."""
    workdir.mkdir(parents=True, exist_ok=True)
    kind = _kind_of(source)
    handle, name = tempfile.mkstemp(dir=workdir, suffix=".strip")
    os.close(handle)
    return kind, Path(name), source.stat().st_size


def _measure(path: Path) -> int:
    """A stripped copy's size. Blocking."""
    return path.stat().st_size


#: Every format a swap can strip, and so send.
STRIPPED = frozenset({*_PICTURE_STRIPS, *_MUXERS})

#: The MIME types of the formats a swap never sends, because nothing here strips them. Empty, as
#: every accepted format has a strip; kept, derived from the allowlist, so a format added to the
#: allowlist without a strip is left out of every offer on the day it is added rather than sent
#: carrying whatever it carries.
NEVER_SENT_MIMES = frozenset(
    media.mime for media in ALLOWED_MEDIA if media.name not in STRIPPED
) - frozenset(media.mime for media in ALLOWED_MEDIA if media.name in STRIPPED)


async def strip(source: Path, workdir: Path, *, settings: Settings) -> Path:
    """Strip `source` into a new temporary file under `workdir`. Raises `CannotStrip`.

    One temporary file per file sent; the caller removes it once the file is done or failed.
    """
    kind, destination, size = await asyncio.to_thread(_begin, source, workdir)
    try:
        # Either strip holds its storage's place for the whole file, so on a share it leaves the
        # streams a place of their own (`lanes.whole_file`): two strips at once on a two-place
        # share would have every stream wait out both copies.
        if kind.name in _PICTURE_STRIPS:
            async with lanes.whole_file(source), lanes.reading(source):
                await asyncio.to_thread(_strip_picture, source, destination, kind.name)
        elif kind.name in _MUXERS:
            try:
                async with lanes.whole_file(source):
                    await media.run(
                        strip_argv(settings, source, destination, _MUXERS[kind.name]),
                        time_limit=120.0 + size / (10 * 1024 * 1024),
                        priority=Priority.BACKGROUND,
                        reads=source,
                    )
            except media.FFmpegError as error:
                raise CannotStrip("the stream copy failed") from error
        else:
            raise CannotStrip("Sift cannot remove this file's details without changing it")
        # The kernel's word on what a place is, asked of the copy about to leave: the strip above
        # keeps only what drawing needs, and this is the door every copy Sift sends goes through,
        # so a place the strip ever came to keep is refused here rather than sent.
        if await asyncio.to_thread(_holds_a_place, destination):
            raise CannotStrip("a location is still in this file after its details were removed")
    except BaseException:
        await asyncio.to_thread(remove, destination)
        raise
    return destination


def _holds_a_place(path: Path) -> bool:
    """Whether the location door finds a place in `path`, or cannot read it well enough to say."""
    try:
        return places.places_in(path) > 0
    except places.CannotRemovePlaces:
        return True


def _look(source: Path) -> tuple[bool, tuple[int, int]]:
    """Whether `source` can be sent as it is (a file Sift sends, with no place in it), and its
    stamp. Blocking; reads the file's ends and its tables, nothing else."""
    _kind_of(source)
    try:
        placed = places.places_in(source) > 0
    except places.CannotRemovePlaces:
        # Not readable far enough to be sure: the strip decides, and refuses what it cannot clear.
        placed = True
    return not placed, _stamp(source)


def _stamp(path: Path) -> tuple[int, int]:
    info = path.stat()
    return info.st_size, info.st_mtime_ns


async def _digest_original(source: Path, stamp: tuple[int, int]) -> str:
    """An original's whole digest, read a block at a time through its storage's lane: a place is
    held for one block and let go, so a send waiting on the same share is never behind a whole
    file. Raises `Changed` when the file changed under the read."""
    hasher = blake3()
    size = stamp[0]
    for index in range(chunk_count(size)):
        async with lanes.reading(source):
            block = await asyncio.to_thread(read_original_chunk, source, index, stamp)
        hasher.update(block)
    return hasher.hexdigest()


def read_original_chunk(path: Path, index: int, stamp: tuple[int, int]) -> bytes:
    """One chunk of an original sent as it is, refused when the file is not the one measured.
    Blocking."""
    if _stamp(path) != stamp:
        raise Changed("the file changed since it was measured for sending")
    return read_chunk(path, index, stamp[0])


async def read_ready_chunk(ready: Prepared, index: int) -> bytes:
    """One chunk of a file ready to send, through its storage's lane, the place let go before the
    chunk goes anywhere. An original is checked against its stamp; a copy is this slice's own."""
    async with lanes.reading(ready.path):
        if ready.copy or ready.stamp is None:
            return await asyncio.to_thread(read_chunk, ready.path, index, ready.size)
        return await asyncio.to_thread(read_original_chunk, ready.path, index, ready.stamp)


async def prepare(source: Path, workdir: Path, *, settings: Settings) -> Prepared:
    """Make a file ready to send: LOOK at it first, and send it as it is when it carries no place
    (no copy; its size and stamp read from the original), else strip it into a copy and measure
    that. Nothing here reads a file whole. See the module docstring."""
    async with lanes.reading(source):
        as_it_is, stamp = await asyncio.to_thread(_look, source)
    if as_it_is:
        return Prepared(path=source, size=stamp[0], copy=False, stamp=stamp)
    path = await strip(source, workdir, settings=settings)
    try:
        size = await asyncio.to_thread(_measure, path)
    except BaseException:
        await asyncio.to_thread(remove, path)
        raise
    return Prepared(path=path, size=size, stamp=stamp)


async def with_digest(ready: Prepared) -> Prepared:
    """`ready` with its whole digest, for a receiver that checks the whole before the first piece:
    an original read a block at a time through its lane, a copy (this slice's own) read whole."""
    if ready.copy or ready.stamp is None:
        digest = await asyncio.to_thread(digest_file, ready.path)
    else:
        digest = await _digest_original(ready.path, ready.stamp)
    return replace(ready, digest=digest)
