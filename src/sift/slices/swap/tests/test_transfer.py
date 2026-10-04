# SPDX-License-Identifier: AGPL-3.0-or-later
"""What leaves this device: each picture format stripped by its own structure, the video strip's
command, a format that cannot be stripped refused, and the chunk arithmetic a resume rests on.

The pictures are built here byte by byte, so every marker a test looks for is one it planted.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sift.kernel.ingress import classify
from sift.slices.swap import transfer
from sift.slices.swap.transfer import CHUNK_SIZE, CannotStrip

_SECRET = b"PLANTEDLOCATION"


def _segment(marker: int, payload: bytes) -> bytes:
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


def _exif(orientation: int) -> bytes:
    """An EXIF block in big-endian TIFF: Orientation and a Make holding the planted text."""
    make_offset = 8 + 2 + 2 * 12 + 4
    entries = struct.pack(">HHIHH", 0x0112, 3, 1, orientation, 0)
    entries += struct.pack(">HHII", 0x010F, 2, len(_SECRET) + 1, make_offset)
    tiff = b"MM\x00\x2a" + struct.pack(">I", 8) + struct.pack(">H", 2) + entries
    tiff += struct.pack(">I", 0) + _SECRET + b"\x00"
    return b"Exif\x00\x00" + tiff


def _jpeg(orientation: int = 6) -> bytes:
    return (
        b"\xff\xd8"
        + _segment(0xE0, b"JFIF\x00\x01\x02\x00\x00\x01\x00\x01\x00\x00")
        + _segment(0xE1, _exif(orientation))
        + _segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x>" + _SECRET + b"</x>")
        + _segment(0xE2, b"ICC_PROFILE\x00\x01\x01profile")
        + _segment(0xFE, b"a comment: " + _SECRET)
        + _segment(0xDB, b"\x00" + bytes(64))
        + _segment(0xDA, b"\x01\x01\x00\x00\x3f\x00")
        + b"\x12\xff\x00\x34\xff\xd0\x56"  # entropy data: a stuffed 0xFF and a restart marker
        + b"\xff\xd9"
        + b"TRAILINGVIDEO"
        + _SECRET
    )


@pytest.mark.unit
def test_a_jpeg_loses_its_exif_its_comment_and_its_tail_and_keeps_its_orientation() -> None:
    out = transfer.strip_jpeg(_jpeg())
    assert _SECRET not in out
    assert b"TRAILINGVIDEO" not in out
    assert out.startswith(b"\xff\xd8\xff\xe0") and out.endswith(b"\xff\xd9")
    # Drawing needs these, and they are still there, whole.
    assert b"ICC_PROFILE\x00" in out
    assert b"\x12\xff\x00\x34\xff\xd0\x56" in out
    # The orientation travels, alone: a fresh EXIF block with one tag in it.
    assert transfer._orientation(out[out.index(b"Exif") :]) == 6
    assert out.count(b"Exif\x00\x00") == 1


@pytest.mark.unit
def test_a_jpeg_standing_upright_gets_no_exif_at_all() -> None:
    assert b"Exif" not in transfer.strip_jpeg(_jpeg(orientation=1))


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


@pytest.mark.unit
def test_a_png_keeps_only_what_drawing_needs() -> None:
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + _png_chunk(b"tEXt", b"Comment\x00" + _SECRET)
        + _png_chunk(b"eXIf", _exif(1)[6:])
        + _png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + _png_chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + _SECRET)
        + _png_chunk(b"IEND", b"")
        + b"AFTERTHEEND"
    )
    out = transfer.strip_png(png)
    assert _SECRET not in out and b"AFTERTHEEND" not in out
    for kind in (b"IHDR", b"IDAT", b"IEND"):
        assert kind in out


def _riff(kind: bytes, data: bytes) -> bytes:
    return kind + struct.pack("<I", len(data)) + data + (b"\x00" if len(data) & 1 else b"")


@pytest.mark.unit
def test_a_webp_loses_its_exif_and_xmp_and_the_flags_that_announced_them() -> None:
    flags = 0x08 | 0x04 | 0x20  # EXIF, XMP, and ICC, which stays
    body = (
        _riff(b"VP8X", bytes([flags, 0, 0, 0]) + b"\x00\x00\x00\x00\x00\x00")
        + _riff(b"ICCP", b"profile")
        + _riff(b"VP8 ", b"\x10\x02\x00\x9d\x01\x2a")
        + _riff(b"EXIF", _exif(1))
        + _riff(b"XMP ", b"<x>" + _SECRET + b"</x>")
    )
    webp = b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body
    out = transfer.strip_webp(webp)
    assert _SECRET not in out and b"EXIF" not in out and b"XMP " not in out
    assert out[20] == 0x20, "the EXIF and XMP flags cleared, the ICC one kept"
    assert struct.unpack("<I", out[4:8])[0] == len(out) - 8


@pytest.mark.unit
def test_a_gif_keeps_its_loop_and_frames_and_loses_its_comments() -> None:
    gif = (
        b"GIF89a"
        + struct.pack("<HHBBB", 1, 1, 0x80, 0, 0)
        + b"\x00\x00\x00\xff\xff\xff"  # a two-color global table
        + b"\x21\xfe"
        + bytes([len(_SECRET)])
        + _SECRET
        + b"\x00"
        + b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"
        + b"\x21\xff\x0bXMP DataXMP"
        + bytes([len(_SECRET)])
        + _SECRET
        + b"\x00"
        + b"\x21\xf9\x04\x00\x0a\x00\x00\x00"
        + b"\x2c"
        + struct.pack("<HHHHB", 0, 0, 1, 1, 0)
        + b"\x02\x02\x44\x01\x00"
        + b"\x3b"
    )
    out = transfer.strip_gif(gif)
    assert _SECRET not in out
    assert b"NETSCAPE2.0" in out and b"\x21\xf9" in out and out.endswith(b"\x3b")
    assert b"\x2c" + struct.pack("<HHHHB", 0, 0, 1, 1, 0) + b"\x02\x02\x44\x01\x00" in out


@pytest.mark.unit
def test_the_video_strip_is_the_stream_copy_with_data_and_attachments_left_out() -> None:
    settings: Any = SimpleNamespace(ffmpeg_path="ffmpeg")
    argv = transfer.strip_argv(settings, Path("in.mp4"), Path("out.strip"), "mp4")
    flags = " ".join(argv)
    for wanted in (
        "-map 0 -map -0:d -map -0:t",
        "-c copy",
        "-map_metadata -1",
        "-map_metadata:s -1",
        "-map_chapters -1",
        "-fflags +bitexact",
        "-f mp4",
    ):
        assert wanted in flags, wanted


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def _full(kind: bytes, version: int, payload: bytes, flags: int = 0) -> bytes:
    return _box(kind, bytes([version]) + flags.to_bytes(3, "big") + payload)


def _infe(item: int, kind: bytes, extra: bytes = b"") -> bytes:
    return _full(b"infe", 2, struct.pack(">HH4s", item, 0, kind) + b"\x00" + extra)


#: A picture's coded bytes: made up, since nothing here decodes them. What is proved is that they
#: are the same bytes at the same offsets, which is what "not re-encoded" means.
_CODED = bytes(range(256)) * 3
#: Where it was taken, as an EXIF block and an XMP packet would carry it.
_EXIF_ITEM = b"\x00\x00\x00\x06Exif\x00\x00MM\x00\x2aGPSLatitude 51.5074 N GPSLongitude 0.1278 W"
_XMP_ITEM = b"<x:xmpmeta><exif:GPSLatitude>51,30.44N</exif:GPSLatitude></x:xmpmeta>"


def _heif(brand: bytes = b"heic", *, sequence_udta: bool = False) -> tuple[bytes, int]:
    """A HEIF still with its picture (item 1), an EXIF item (2) and an XMP item (3), each linked
    to the picture, laid out as a phone writes one: `ftyp`, `meta`, `mdat`. Returns the file and
    where the coded picture starts in it."""

    def meta(base: int) -> bytes:
        iloc = struct.pack(">BBH", 0x44, 0x00, 3)
        at = base
        for item, body in ((1, _CODED), (2, _EXIF_ITEM), (3, _XMP_ITEM)):
            iloc += struct.pack(">HHHII", item, 0, 1, at, len(body))
            at += len(body)
        iinf = struct.pack(">H", 3) + _infe(1, b"hvc1") + _infe(2, b"Exif")
        iinf += _infe(3, b"mime", b"application/rdf+xml\x00")
        iref = _box(b"cdsc", struct.pack(">HHH", 2, 1, 1)) + _box(
            b"cdsc", struct.pack(">HHH", 3, 1, 1)
        )
        ipco = _box(b"ipco", _full(b"ispe", 0, struct.pack(">II", 16, 16)) + _box(b"irot", b"\x01"))
        ipma = struct.pack(">I", 2) + struct.pack(">HBBB", 1, 2, 0x81, 0x02)
        ipma += struct.pack(">HBB", 2, 1, 0x01)
        return _full(
            b"meta",
            0,
            _full(b"hdlr", 0, b"\x00" * 4 + b"pict" + b"\x00" * 12 + b"\x00")
            + _full(b"pitm", 0, struct.pack(">H", 1))
            + _full(b"iloc", 0, iloc)
            + _full(b"iinf", 0, iinf)
            + _full(b"iref", 0, iref)
            + _box(b"iprp", ipco + _full(b"ipma", 0, ipma)),
        )

    ftyp = _box(b"ftyp", brand + b"\x00\x00\x00\x00mif1" + brand)
    extra = b""
    if sequence_udta:
        extra = _box(b"moov", _box(b"trak", _box(b"udta", _box(b"\xa9xyz", b"+51.5074-000.1278/"))))
    head = len(ftyp) + len(meta(0)) + len(extra) + 8
    data = ftyp + meta(head) + extra + _box(b"mdat", _CODED + _EXIF_ITEM + _XMP_ITEM)
    return data, head


def _items(data: bytes) -> dict[int, tuple[bytes, int, int]]:
    """Every item a HEIF names: its type, and where its one extent is."""
    top = transfer._boxes(data, 0, len(data))
    at, header, end, _ = next(box for box in top if box[3] == b"meta")
    children = {
        kind: (one, head, stop)
        for one, head, stop, kind in transfer._boxes(data, at + header + 4, end)
    }
    one, head, stop = children[b"iinf"]
    types = {}
    for i_at, i_head, i_end, _ in transfer._boxes(data, one + head + 6, stop):
        body = data[i_at + i_head : i_end]
        (item,) = struct.unpack(">H", body[4:6])
        types[item] = body[8:12]
    one, head, stop = children[b"iloc"]
    table = data[one + head : stop]
    (count,) = struct.unpack(">H", table[6:8])
    found = {}
    for n in range(count):
        item, _, _, offset, length = struct.unpack(">HHHII", table[8 + n * 14 : 22 + n * 14])
        found[item] = (types[item], offset, length)
    return found


@pytest.mark.unit
@pytest.mark.parametrize("brand", [b"heic", b"avif"])
def test_a_heif_loses_its_exif_and_xmp_items_and_keeps_its_picture_byte_for_byte(
    brand: bytes,
) -> None:
    data, coded_at = _heif(brand)
    assert data[coded_at : coded_at + len(_CODED)] == _CODED
    stripped = transfer.strip_heif(data)

    # Lossless: the coded picture is the same bytes at the same offset, the item table still
    # points at it, and the file is the same length (the tables' saving is a `free` box).
    assert len(stripped) == len(data)
    assert stripped[coded_at : coded_at + len(_CODED)] == _CODED
    assert _items(stripped) == {1: (b"hvc1", coded_at, len(_CODED))}
    # Where it was taken is gone: no item, no reference, no bytes.
    assert b"GPSLatitude" not in stripped and b"Exif" not in stripped and b"cdsc" not in stripped
    # What draws the picture stays: its size and its turn.
    assert b"ispe" in stripped and b"irot" in stripped
    # Every box after the tables starts where it did.
    assert [box[0] for box in transfer._boxes(stripped, 0, len(stripped)) if box[3] == b"mdat"] == [
        box[0] for box in transfer._boxes(data, 0, len(data)) if box[3] == b"mdat"
    ]
    # Still a HEIF to the gate, as what it was.
    assert classify(stripped[:4096], stripped[-4096:]) == classify(data[:4096], data[-4096:])
    # Stripping again changes nothing: the receiver strips every file a second time.
    assert transfer.strip_heif(stripped) == stripped


@pytest.mark.unit
def test_a_heif_sequence_loses_the_location_its_track_carries() -> None:
    data, coded_at = _heif(b"avis", sequence_udta=True)
    stripped = transfer.strip_heif(data)
    assert b"+51.5074" not in stripped and b"udta" not in stripped
    assert stripped[coded_at : coded_at + len(_CODED)] == _CODED


@pytest.mark.unit
def test_the_corpus_heic_keeps_its_samples_and_loses_its_user_data() -> None:
    """The corpus's HEIC is a track in a `moov`, as a phone's burst is: its `udta` goes, and the
    `mdat` holding its samples is the same bytes at the same offset."""
    data = (
        Path(__file__).parents[3] / "kernel" / "tests" / "fixtures" / "ingress" / "accepted.heic"
    ).read_bytes()
    stripped = transfer.strip_heif(data)
    assert len(stripped) == len(data)
    boxes = transfer._boxes(data, 0, len(data))
    mdat = next(box for box in boxes if box[3] == b"mdat")
    assert stripped[mdat[0] : mdat[2]] == data[mdat[0] : mdat[2]]
    assert b"udta" in data and b"udta" not in stripped


@pytest.mark.unit
@pytest.mark.parametrize(
    ("data", "words"),
    [
        (_box(b"mdat", b"x" * 8), "not a HEIF"),
        (_box(b"ftyp", b"heic") + b"\x00\x00\x00\x40meta", "runs past"),
    ],
)
def test_a_heif_whose_boxes_cannot_be_read_is_not_sent(data: bytes, words: str) -> None:
    with pytest.raises(CannotStrip, match=words):
        transfer.strip_heif(data)


_FTYP = _box(b"ftyp", b"heic\x00\x00\x00\x00mif1heic")


def _meta(*children: bytes) -> bytes:
    return _full(b"meta", 0, b"".join(children))


def _infe_v0(item: int) -> bytes:
    """An item in the table's first version, which names no type: what a metadata item was."""
    return _full(b"infe", 0, struct.pack(">HH", item, 0) + b"\x00")


def _iinf(*items: bytes) -> bytes:
    return _full(b"iinf", 0, struct.pack(">H", len(items)) + b"".join(items))


def _iloc(*entries: tuple[int, int, int, int, int], sizes: int = 0x44) -> bytes:
    """A version 1 `iloc`, which says how each item is held: entries of (item, method, data
    reference, offset, length), one extent each."""
    offset_size, length_size = sizes >> 4, sizes & 0x0F
    body = bytes([sizes, 0]) + struct.pack(">H", len(entries))
    for item, method, reference, offset, length in entries:
        body += struct.pack(">HHHH", item, method, reference, 1)
        body += offset.to_bytes(offset_size, "big") + length.to_bytes(length_size, "big")
    return _full(b"iloc", 1, body)


#: A picture (item 1) and an EXIF item (2): the smallest table with something to take out.
_TWO_ITEMS = _iinf(_infe(1, b"hvc1"), _infe(2, b"Exif"))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("data", "words"),
    [
        pytest.param(_FTYP + b"\x00\x00\x00", "runs past", id="a box shorter than its header"),
        pytest.param(
            _FTYP + struct.pack(">I4s", 1, b"free") + bytes(4),
            "runs past",
            id="a 64-bit size cut short",
        ),
        pytest.param(
            _FTYP + _meta(_TWO_ITEMS, _iloc((2, 0, 0, 0, 4), sizes=0x94)),
            "not one Sift can read",
            id="an offset wider than eight bytes",
        ),
        pytest.param(
            _FTYP + _meta(_full(b"iinf", 0, struct.pack(">H", 1) + _box(b"free", b"")), _iloc()),
            "other than items",
            id="an item table holding something else",
        ),
        pytest.param(
            _FTYP
            + _meta(
                _TWO_ITEMS,
                # Offsets and lengths of no bytes, after a four-byte index the table never holds.
                _full(b"iloc", 1, bytes([0x00, 0x04]) + struct.pack(">HHHHH", 1, 2, 0, 0, 1)),
            ),
            "location table runs past",
            id="a location table cut short",
        ),
        pytest.param(
            _FTYP + _meta(_TWO_ITEMS, _iloc((2, 0, 0, 40, 0))),
            "runs to the end of the file",
            id="a metadata item of no stated length",
        ),
        pytest.param(
            _FTYP
            + _meta(
                _TWO_ITEMS, _iloc(), _box(b"iprp", _full(b"ipma", 0, struct.pack(">IHB", 1, 2, 5)))
            ),
            "property table runs past",
            id="a property table cut short",
        ),
        pytest.param(
            _FTYP + _meta(_full(b"pitm", 0, struct.pack(">H", 2)), _TWO_ITEMS, _iloc()),
            "main item is its metadata",
            id="the main item is the EXIF",
        ),
        pytest.param(
            _FTYP + _meta(_TWO_ITEMS, _iloc((2, 1, 0, 0, 64)), _box(b"idat", b"short")),
            "runs past its data",
            id="an item in the tables past their end",
        ),
        pytest.param(
            _FTYP + _meta(_TWO_ITEMS, _iloc((2, 0, 0, 100_000, 4))),
            "runs past the file",
            id="an item past the end of the file",
        ),
    ],
)
def test_a_heif_whose_tables_cannot_be_trusted_is_not_sent(data: bytes, words: str) -> None:
    """Every table is read by the sizes it states, and a size that points past what holds it is a
    file Sift cannot strip with certainty, so it is not sent rather than sent half stripped."""
    with pytest.raises(CannotStrip, match=words):
        transfer.strip_heif(data)


@pytest.mark.unit
def test_a_heif_box_with_a_64_bit_size_or_one_running_to_the_end_is_emptied_in_place() -> None:
    """Both of the format's other ways to state a box's size are read: a 64-bit size after the
    type, and a size of 0 for a box running to the end of the file."""
    big = struct.pack(">I4sQ", 1, b"free", 22) + b"secret"
    data = _FTYP + big + struct.pack(">I4s", 0, b"skip") + b"hidden"

    stripped = transfer.strip_heif(data)

    at = len(_FTYP)
    assert len(stripped) == len(data)
    assert stripped[at : at + 16] == big[:16] and stripped[at + 16 : at + 22] == bytes(6)
    assert stripped[at + 22 :] == struct.pack(">I4s", 0, b"free") + bytes(6)


@pytest.mark.unit
def test_a_heif_whose_items_have_no_locations_is_left_as_it_is() -> None:
    """Without a location table no item's bytes can be found, so there is nothing to take out."""
    data = _FTYP + _meta(_full(b"hdlr", 0, bytes(4) + b"pict" + bytes(13)), _TWO_ITEMS)

    assert transfer.strip_heif(data) == data


@pytest.mark.unit
def test_a_heif_item_held_in_the_tables_or_in_another_file_goes_and_its_pictures_stay() -> None:
    """An EXIF item can be held three ways: in the file, in the tables' own data, or in another
    file altogether. The first two are zeroed where they lie; the third has no bytes here, so
    nothing of this file is zeroed for it. A reference between two pictures stays."""
    in_tables = b"GPS held in the tables"

    def meta(coded_at: int) -> bytes:
        return _meta(
            _iinf(
                _infe(1, b"hvc1"),
                _infe(3, b"hvc1"),
                _infe_v0(2),
                _infe(4, b"Exif"),
                _infe(5, b"Exif"),
            ),
            _iloc(
                (1, 0, 0, coded_at, len(_CODED)),
                (3, 0, 0, coded_at, 16),
                (2, 0, 0, coded_at + len(_CODED), len(_EXIF_ITEM)),
                (4, 1, 0, 0, len(in_tables)),
                # In the file the data reference names, at offsets that are the picture's here.
                (5, 0, 1, coded_at, 16),
            ),
            _full(
                b"iref",
                0,
                _box(b"thmb", struct.pack(">HHH", 3, 1, 1))
                + _box(b"cdsc", struct.pack(">HHH", 2, 1, 1)),
            ),
            _box(b"idat", in_tables),
        )

    coded_at = len(_FTYP) + len(meta(0)) + 8
    data = _FTYP + meta(coded_at) + _box(b"mdat", _CODED + _EXIF_ITEM)

    stripped = transfer.strip_heif(data)

    assert len(stripped) == len(data)
    assert stripped[coded_at : coded_at + len(_CODED)] == _CODED
    assert b"GPSLatitude" not in stripped and in_tables not in stripped
    assert b"thmb" in stripped and b"cdsc" not in stripped


@pytest.mark.unit
def test_nothing_sift_accepts_is_left_out_of_a_swap_for_want_of_a_strip() -> None:
    """Every format Sift takes has a strip, so the list derived from the allowlist is empty, and it
    stays the guard for any format added without one."""
    assert not transfer.NEVER_SENT_MIMES


@pytest.mark.unit
def test_the_chunks_and_where_a_file_resumes() -> None:
    assert transfer.chunk_count(0) == 0
    assert transfer.chunk_count(1) == 1
    assert transfer.chunk_count(CHUNK_SIZE) == 1
    assert transfer.chunk_count(CHUNK_SIZE + 1) == 2
    assert transfer.chunk_length(CHUNK_SIZE + 5, 1) == 5
    with pytest.raises(ValueError):
        transfer.chunk_length(CHUNK_SIZE, 1)
    assert transfer.missing([0, 2], 5) == [1, 3, 4]
    assert transfer.missing([], 3) == [0, 1, 2]
    assert transfer.missing([0, 1, 2], 3) == []


CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


@pytest.mark.unit
def test_a_size_or_a_chunk_that_cannot_be_is_refused() -> None:
    for size, chunk in ((-1, CHUNK_SIZE), (10, 0)):
        with pytest.raises(ValueError, match="not negative"):
            transfer.chunk_count(size, chunk)


@pytest.mark.unit
def test_a_stripped_copy_that_shrank_since_it_was_measured_is_not_read_short(
    tmp_path: Path,
) -> None:
    """The size was measured once, and a chunk is read against it: a copy shorter than that is an
    error, never a short chunk sent as if it were whole."""
    copy = tmp_path / "x.strip"
    copy.write_bytes(b"12345")

    with pytest.raises(OSError, match="shorter than it was"):
        transfer.read_chunk(copy, 0, 10, chunk_size=10)


@pytest.mark.unit
def test_two_streams_writing_the_first_chunks_of_one_file_keep_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two streams carry shares of one file and the second looks for the staged file before the
    first has created it: its chunk lands beside the first one, never over an emptied file."""
    staged = tmp_path / "session" / "file.part"
    transfer.write_chunk(staged, 1, b"b" * 4, chunk_size=4)
    # What the second stream saw when it looked, a moment before the first stream's create.
    monkeypatch.setattr(Path, "exists", lambda self: False)
    transfer.write_chunk(staged, 0, b"a" * 4, chunk_size=4)
    monkeypatch.undo()

    assert staged.read_bytes() == b"aaaabbbb"


def _exif_entries(order: bytes, *entries: tuple[int, int, int, int]) -> bytes:
    """An EXIF block in `order` ("II" or "MM") holding these (tag, kind, count, value) entries."""
    end = "<" if order == b"II" else ">"
    tiff = order + (b"\x2a\x00" if order == b"II" else b"\x00\x2a") + struct.pack(end + "I", 8)
    tiff += struct.pack(end + "H", len(entries))
    for tag, kind, count, value in entries:
        tiff += struct.pack(end + "HHIHH", tag, kind, count, value, 0)
    return b"Exif\x00\x00" + tiff + struct.pack(end + "I", 0)


@pytest.mark.unit
def test_the_orientation_is_read_in_either_byte_order_past_other_tags() -> None:
    little = _exif_entries(b"II", (0x010F, 2, 4, 0), (0x0112, 3, 1, 8))
    assert transfer._orientation(little) == 8


@pytest.mark.unit
@pytest.mark.parametrize(
    "block",
    [
        b"XMP\x00\x00\x00" + bytes(20),  # not an EXIF block at all
        b"Exif\x00\x00XX\x00\x2a" + bytes(8),  # a byte order no TIFF has
        _exif_entries(b"MM", (0x010F, 2, 4, 0)),  # no orientation among its tags
        _exif_entries(b"MM", (0x0112, 3, 1, 9)),  # an orientation no picture has
        b"Exif\x00\x00MM\x00\x2a\x00\x00\x00\xff",  # a directory past the block's end
    ],
)
def test_an_exif_block_that_does_not_say_an_orientation_says_none(block: bytes) -> None:
    assert transfer._orientation(block) is None


@pytest.mark.unit
def test_an_adobe_segment_is_kept_and_any_other_app0_is_not() -> None:
    """The three APPn segments drawing needs are kept whole; an APP0 that is not JFIF is somebody
    else's and goes."""
    out = transfer.strip_jpeg(
        b"\xff\xd8"
        + _segment(0xEE, b"Adobe\x00\x64\x00\x00\x00\x00\x01")
        + _segment(0xE0, b"NOTJFIF" + _SECRET)
        + _segment(0xDB, b"\x00" + bytes(64))
        + b"\xff\xd9"
    )
    assert b"Adobe" in out and _SECRET not in out


@pytest.mark.unit
def test_markers_that_stand_alone_between_segments_are_copied_as_they_are() -> None:
    out = transfer.strip_jpeg(
        b"\xff\xd8" + b"\xff\x01" + b"\xff\xd3" + _segment(0xDB, b"\x00" + bytes(64)) + b"\xff\xd9"
    )
    assert b"\xff\x01\xff\xd3" in out and out.endswith(b"\xff\xd9")


@pytest.mark.unit
def test_fill_bytes_inside_a_scan_are_part_of_the_picture() -> None:
    scan = b"\x12\xff\xff\x00\x34"
    out = transfer.strip_jpeg(
        b"\xff\xd8" + _segment(0xDA, b"\x01\x01\x00\x00\x3f\x00") + scan + b"\xff\xd9"
    )
    assert scan in out


@pytest.mark.unit
@pytest.mark.parametrize(
    ("data", "words"),
    [
        (b"GIF89a", "not a JPEG"),
        (b"\xff\xd8\x00\x00", "did not start where it should"),
        (b"\xff\xd8\xff\xff", "has no end"),
        (b"\xff\xd8\xff\xdb", "has no end"),
        (b"\xff\xd8\xff\xdb\x00\x40\x00", "runs past the file"),
        (b"\xff\xd8\xff\xda\x00\x02\x12\x34", "ends inside its picture"),
        (b"\xff\xd8" + _segment(0xDB, b"\x00" + bytes(64)), "has no end"),
    ],
)
def test_a_jpeg_that_cannot_be_read_to_its_end_is_not_sent(data: bytes, words: str) -> None:
    with pytest.raises(CannotStrip, match=words):
        transfer.strip_jpeg(data)


@pytest.mark.unit
def test_a_png_that_is_not_one_or_has_no_end_is_not_sent() -> None:
    with pytest.raises(CannotStrip, match="not a PNG"):
        transfer.strip_png(b"GIF89a")
    cut = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", bytes(13))[:-6]
    with pytest.raises(CannotStrip, match="has no end"):
        transfer.strip_png(cut)
    unended = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", bytes(13))
    with pytest.raises(CannotStrip, match="has no end"):
        transfer.strip_png(unended)


@pytest.mark.unit
def test_a_webp_that_is_not_one_or_runs_past_its_end_is_not_sent() -> None:
    with pytest.raises(CannotStrip, match="not a WebP"):
        transfer.strip_webp(b"RIFF\x00\x00\x00\x00WAVE")
    past = b"RIFF\x00\x00\x00\x00WEBP" + b"VP8 " + struct.pack("<I", 100) + b"\x00" * 10
    with pytest.raises(CannotStrip, match="runs past the file"):
        transfer.strip_webp(past)


@pytest.mark.unit
def test_a_webp_whose_last_odd_chunk_lost_its_padding_is_padded_again() -> None:
    """The pad byte after an odd-length chunk is the RIFF rule; a file that ends without it is
    written with it, so the stripped copy is a well-formed WebP."""
    body = b"VP8 " + struct.pack("<I", 3) + b"\x10\x02\x00"
    out = transfer.strip_webp(b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body)
    assert out.endswith(b"\x10\x02\x00\x00")
    assert struct.unpack("<I", out[4:8])[0] == len(out) - 8


@pytest.mark.unit
@pytest.mark.parametrize(
    ("data", "words"),
    [
        (b"PNG89a" + bytes(10), "not a GIF"),
        (b"GIF89a" + struct.pack("<HHBBB", 1, 1, 0, 0, 0) + b"\x21\xfe\x05ab", "inside a block"),
        (b"GIF89a" + struct.pack("<HHBBB", 1, 1, 0, 0, 0) + b"\x2c\x00\x00", "has no end"),
        (b"GIF89a" + struct.pack("<HHBBB", 1, 1, 0, 0, 0) + b"\x99", "not one Sift can read"),
        (b"GIF89a" + struct.pack("<HHBBB", 1, 1, 0, 0, 0), "has no end"),
    ],
)
def test_a_gif_that_cannot_be_read_to_its_end_is_not_sent(data: bytes, words: str) -> None:
    with pytest.raises(CannotStrip, match=words):
        transfer.strip_gif(data)


@pytest.mark.integration
async def test_a_file_the_gate_does_not_recognise_is_not_sent(tmp_path: Path) -> None:
    source = tmp_path / "noise.bin"
    source.write_bytes(bytes(range(256)) * 16)
    settings: Any = SimpleNamespace(ffmpeg_path="ffmpeg")

    with pytest.raises(CannotStrip, match="not one Sift sends"):
        await transfer.strip(source, tmp_path / "out", settings=settings)


@pytest.mark.integration
async def test_a_picture_past_the_cap_is_not_stripped_and_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(transfer, "_PICTURE_CAP", 10)
    settings: Any = SimpleNamespace(ffmpeg_path="ffmpeg")

    with pytest.raises(CannotStrip, match="too large"):
        await transfer.strip(CORPUS / "accepted.jpg", tmp_path / "out", settings=settings)

    assert list((tmp_path / "out").iterdir()) == []


@pytest.mark.integration
async def test_a_video_whose_stream_copy_fails_is_not_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sift.kernel import media

    async def refuses(*_args: object, **_kwargs: object) -> str:
        raise media.FFmpegError("Invalid data found when processing input")

    monkeypatch.setattr(media, "run", refuses)
    settings: Any = SimpleNamespace(ffmpeg_path="ffmpeg")

    with pytest.raises(CannotStrip, match="stream copy failed"):
        await transfer.strip(CORPUS / "accepted.mp4", tmp_path / "out", settings=settings)

    assert list((tmp_path / "out").iterdir()) == []


def _placed_jpeg(path: Path) -> Path:
    """A picture carrying a GPS directory: one the look sends to the strip."""
    from PIL import Image

    exif = Image.Exif()
    exif.get_ifd(0x8825)[2] = (48.0, 51.0, 29.17)
    Image.new("RGB", (16, 12)).save(path, exif=exif)
    return path


@pytest.mark.integration
async def test_a_prepared_file_is_its_stripped_copy_measured(tmp_path: Path) -> None:
    settings: Any = SimpleNamespace(ffmpeg_path="ffmpeg")
    source = _placed_jpeg(tmp_path / "trip.jpg")

    prepared = await transfer.prepare(source, tmp_path / "out", settings=settings)

    assert prepared.path.parent == tmp_path / "out"
    assert prepared.size == prepared.path.stat().st_size
    assert prepared.stamp == (source.stat().st_size, source.stat().st_mtime_ns), "its original's"
    assert (await transfer.with_digest(prepared)).digest == transfer.digest_file(prepared.path)


@pytest.mark.integration
async def test_a_copy_that_cannot_be_measured_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreadable(_path: Path) -> int:
        raise OSError("the disk went away")

    monkeypatch.setattr(transfer, "_measure", unreadable)
    settings: Any = SimpleNamespace(ffmpeg_path="ffmpeg")

    with pytest.raises(OSError, match="went away"):
        await transfer.prepare(
            _placed_jpeg(tmp_path / "trip.jpg"), tmp_path / "out", settings=settings
        )

    assert list((tmp_path / "out").iterdir()) == []


@pytest.mark.integration
async def test_a_strip_that_left_a_place_is_refused_by_the_door_and_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The kernel's location door reads every stripped copy before it is sent: a strip that ever
    came to keep a GPS directory would be refused here rather than send it."""
    from PIL import Image

    source = tmp_path / "trip.jpg"
    exif = Image.Exif()
    exif.get_ifd(0x8825)[2] = (48.0, 51.0, 29.17)
    Image.new("RGB", (16, 12)).save(source, exif=exif)
    monkeypatch.setitem(transfer._PICTURE_STRIPS, "jpeg", lambda data: data)
    settings: Any = SimpleNamespace(ffmpeg_path="ffmpeg")

    with pytest.raises(CannotStrip, match="location is still in this file"):
        await transfer.strip(source, tmp_path / "out", settings=settings)

    assert list((tmp_path / "out").iterdir()) == []


@pytest.mark.integration
async def test_a_format_with_no_strip_of_its_own_is_refused_and_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A format the allowlist takes before a strip is written for it is never sent as it is."""
    monkeypatch.delitem(transfer._PICTURE_STRIPS, "jpeg")
    settings: Any = SimpleNamespace(ffmpeg_path="ffmpeg")

    with pytest.raises(CannotStrip, match="cannot remove this file's details"):
        await transfer.strip(
            _placed_jpeg(tmp_path / "trip.jpg"), tmp_path / "out", settings=settings
        )

    assert list((tmp_path / "out").iterdir()) == []


@pytest.mark.integration
async def test_a_copy_the_location_door_cannot_read_is_refused_as_holding_a_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy the door cannot read well enough to say is treated as one that holds a place."""
    from sift.kernel import places

    def unreadable(_path: Path) -> int:
        raise places.CannotRemovePlaces("a box runs past its end")

    monkeypatch.setattr(places, "places_in", unreadable)
    settings: Any = SimpleNamespace(ffmpeg_path="ffmpeg")

    with pytest.raises(CannotStrip, match="location is still in this file"):
        await transfer.strip(
            _placed_jpeg(tmp_path / "trip.jpg"), tmp_path / "out", settings=settings
        )

    assert list((tmp_path / "out").iterdir()) == []
