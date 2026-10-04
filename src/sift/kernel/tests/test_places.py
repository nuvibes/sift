# SPDX-License-Identifier: AGPL-3.0-or-later
"""The location door: every copy it makes has no place in it, the picture is the same bytes, the
source is never written, and a file with no place is handed back untouched.

Every fixture is made here, by Pillow, by ffmpeg or byte by byte, at an invented place: never a
real photograph. The door reads structure and never decodes a picture, so a file of a few dozen
bytes laid out by hand reaches every branch a camera's file would, and the broken ones no camera
writes.
"""

from __future__ import annotations

import hashlib
import io
import struct
import subprocess
import zlib
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin

from sift.kernel import places
from sift.kernel.config import vendored_tool

#: An invented place: the seconds are what the byte checks look for.
_SECONDS = 29.17
_XMP = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-'
    b'syntax-ns#"><rdf:Description xmlns:exif="http://ns.adobe.com/exif/1.0/" xmlns:xmp="http:'
    b'//ns.adobe.com/xap/1.0/" exif:GPSLatitude="48,51.4862N" xmp:CreatorTool="Maker Q">'
    b"<exif:GPSLongitude>2,17.6702E</exif:GPSLongitude></rdf:Description></rdf:RDF></x:xmpmeta>"
)


def _exif(*, with_gps: bool = True) -> Image.Exif:
    exif = Image.Exif()
    exif[0x0110] = "Model Q"
    exif[0x0112] = 6
    if with_gps:
        gps = exif.get_ifd(0x8825)
        gps[1] = "N"
        gps[2] = (48.0, 51.0, _SECONDS)
        gps[3] = "E"
        gps[4] = (2.0, 17.0, 40.0)
    return exif


def _picture() -> Image.Image:
    picture = Image.new("RGB", (32, 24))
    for x in range(32):
        for y in range(24):
            picture.putpixel((x, y), (x * 8, y * 10, 128))
    return picture


def _seconds_bytes(data: bytes) -> bool:
    """Whether the invented seconds are anywhere in `data`, as either byte order writes them."""
    return any(struct.pack(order + "II", 2917, 100) in data for order in "<>")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _chunks(data: bytes, *, little: bool = False) -> dict[bytes, bytes]:
    """A PNG's (big-endian, CRC) or a WebP's (little-endian) chunks by type, first of each."""
    found: dict[bytes, bytes] = {}
    at = 8 if not little else 12
    while at + 8 <= len(data):
        if little:
            kind, length = data[at : at + 4], struct.unpack("<I", data[at + 4 : at + 8])[0]
            body = data[at + 8 : at + 8 + length]
            at += 8 + length + (length & 1)
        else:
            length, kind = struct.unpack(">I4s", data[at : at + 8])
            body = data[at + 8 : at + 8 + length]
            at += 12 + length
        found.setdefault(kind, body)
    return found


# --- JPEG --------------------------------------------------------------------------------------


def test_a_jpeg_loses_its_gps_directory_and_keeps_everything_else(tmp_path: Path) -> None:
    source = tmp_path / "trip.jpg"
    _picture().save(source, exif=_exif(), quality=90)
    before = source.read_bytes()
    stamp = source.stat().st_mtime_ns
    assert _seconds_bytes(before)
    assert places.places_in(source) == 1

    copy = places.remove_places(source, tmp_path / "scratch")

    assert copy is not None
    after = copy.read_bytes()
    assert copy.name == source.name
    assert not _seconds_bytes(after)
    assert len(after) == len(before)
    # Only the EXIF block changed: the coded picture after it is the same bytes at the same place.
    scan = before.index(b"\xff\xda")
    assert after[scan:] == before[scan:]
    with Image.open(copy) as opened:
        exif = opened.getexif()
        assert 0x8825 not in exif
        assert exif[0x0110] == "Model Q"
        assert exif[0x0112] == 6
    # The source was read and never written.
    assert source.read_bytes() == before
    assert source.stat().st_mtime_ns == stamp
    assert places.places_in(copy) == 0


def test_a_jpeg_with_no_place_is_handed_back_and_nothing_is_written(tmp_path: Path) -> None:
    source = tmp_path / "plain.jpg"
    _picture().save(source, exif=_exif(with_gps=False))
    scratch = tmp_path / "scratch"

    assert places.remove_places(source, scratch) is None
    assert places.size_without_places(source) is None
    assert not scratch.exists()


def test_an_exif_block_that_is_not_a_tiff_is_zeroed_where_it_lies(tmp_path: Path) -> None:
    """It may hold a place and nothing can read it to say it does not."""
    buffer = io.BytesIO()
    _picture().save(buffer, "JPEG")
    plain = buffer.getvalue()
    body = b"Exif\x00\x00" + b"not a tiff: GPS 48.8577 2.2950"
    source = tmp_path / "odd.jpg"
    source.write_bytes(
        plain[:2] + b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body + plain[2:]
    )

    copy = places.remove_places(source, tmp_path / "scratch")

    assert copy is not None
    after = copy.read_bytes()
    assert b"48.8577" not in after
    assert len(after) == source.stat().st_size
    assert after.endswith(plain[2:])


def test_a_jpeg_xmp_packet_loses_its_place_properties_at_the_same_length(tmp_path: Path) -> None:
    source = tmp_path / "xmp.jpg"
    _picture().save(source, xmp=_XMP)
    before = source.read_bytes()

    copy = places.remove_places(source, tmp_path / "scratch")

    assert copy is not None
    after = copy.read_bytes()
    assert len(after) == len(before)
    assert b"GPS" not in after
    assert b"48,51.4862N" not in after
    assert b'xmp:CreatorTool="Maker Q"' in after


# --- PNG, WebP, GIF ------------------------------------------------------------------------------


def test_a_png_loses_its_exif_place_its_place_text_and_its_xmp_place(tmp_path: Path) -> None:
    source = tmp_path / "trip.png"
    info = PngImagePlugin.PngInfo()
    info.add_text("exif:GPSLatitude", "48.8577")
    info.add_text("Comment", "kept")
    info.add_itxt("XML:com.adobe.xmp", _XMP.decode("ascii"))
    _picture().save(source, exif=_exif(), pnginfo=info)
    before = source.read_bytes()
    assert _seconds_bytes(before)

    copy = places.remove_places(source, tmp_path / "scratch")

    assert copy is not None
    after = copy.read_bytes()
    assert not _seconds_bytes(after)
    assert b"GPS" not in after
    assert _chunks(after)[b"IDAT"] == _chunks(before)[b"IDAT"]
    # The size a drag hands over before the copy exists is the copy's size, chunks gone and all.
    assert len(after) != len(before)
    assert places.size_without_places(source) == len(after)
    with Image.open(copy) as opened:
        opened.load()
        assert isinstance(opened, PngImagePlugin.PngImageFile)
        assert opened.text.get("Comment") == "kept"
        assert 0x8825 not in opened.getexif()
    assert source.read_bytes() == before


def test_a_webp_loses_its_exif_place_and_keeps_its_picture(tmp_path: Path) -> None:
    source = tmp_path / "trip.webp"
    _picture().save(source, "WEBP", exif=_exif().tobytes(), xmp=_XMP)
    before = source.read_bytes()
    assert _seconds_bytes(before)

    copy = places.remove_places(source, tmp_path / "scratch")

    assert copy is not None
    after = copy.read_bytes()
    assert not _seconds_bytes(after)
    assert b"GPS" not in after
    assert len(after) == len(before)
    picture = next(kind for kind in (b"VP8 ", b"VP8L") if kind in _chunks(before, little=True))
    assert _chunks(after, little=True)[picture] == _chunks(before, little=True)[picture]


def test_a_gif_loses_an_xmp_extension_holding_a_place(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    _picture().convert("P").save(buffer, "GIF")
    plain = buffer.getvalue()
    # XMP in a GIF: the identifier, the packet as raw bytes, then the 258-byte magic trailer.
    trailer = b"\x01" + bytes(range(0xFF, -1, -1)) + b"\x00"
    extension = b"\x21\xff\x0bXMP DataXMP" + _XMP + trailer
    source = tmp_path / "trip.gif"
    source.write_bytes(plain[:-1] + extension + plain[-1:])

    copy = places.remove_places(source, tmp_path / "scratch")

    assert copy is not None
    assert copy.read_bytes() == plain


# --- HEIC -----------------------------------------------------------------------------------------


def test_a_heic_exif_item_loses_its_gps_directory(tmp_path: Path) -> None:
    pillow_heif = pytest.importorskip("pillow_heif")
    source = tmp_path / "trip.heic"
    pillow_heif.from_pillow(_picture()).save(source, exif=_exif().tobytes(), quality=80)
    before = source.read_bytes()
    assert _seconds_bytes(before)

    copy = places.remove_places(source, tmp_path / "scratch")

    assert copy is not None
    after = copy.read_bytes()
    assert not _seconds_bytes(after)
    assert len(after) == len(before)
    decoded = pillow_heif.open_heif(copy)
    # Turned by the orientation the copy kept, as the original is.
    assert decoded.size == (24, 32)
    exif = Image.Exif()
    exif.load(decoded.info["exif"])
    assert 0x8825 not in exif
    assert exif[0x0110] == "Model Q"


# --- video ----------------------------------------------------------------------------------------


def _ffmpeg(*arguments: str) -> None:
    subprocess.run(
        [vendored_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *arguments],
        check=True,
        timeout=60,
    )


def _clip(destination: Path, muxer: str, *extra: str) -> None:
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=64x48:rate=10",
        "-t",
        "1",
        "-c:v",
        "mpeg4",
        "-metadata",
        "location=+48.8577+002.2950/",
        "-metadata",
        "title=Kept title",
        *extra,
        "-f",
        muxer,
        str(destination),
    )


@pytest.mark.parametrize(
    ("name", "muxer", "extra"),
    [
        ("trip.mov", "mov", ()),
        ("trip.mp4", "mp4", ("-movflags", "use_metadata_tags")),
        ("trip.mkv", "matroska", ()),
    ],
)
def test_a_video_loses_its_location_tag_and_keeps_its_length_and_title(
    tmp_path: Path, name: str, muxer: str, extra: tuple[str, ...]
) -> None:
    source = tmp_path / name
    _clip(source, muxer, *extra)
    before = source.read_bytes()
    assert b"+48.8577" in before

    copy = places.remove_places(source, tmp_path / "scratch")

    assert copy is not None
    after = copy.read_bytes()
    assert b"+48.8577" not in after
    assert b"Kept title" in after
    assert len(after) == len(before)
    assert _digest(source) == hashlib.sha256(before).hexdigest()
    # It still reads, and nothing it reports says where.
    answer = subprocess.run(
        [vendored_tool("ffprobe"), "-v", "error", "-show_format", "-show_streams", str(copy)],
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout
    assert b"tag:location" not in answer.lower()
    assert b"+48.8577" not in answer
    assert b"codec_name=mpeg4" in answer


def test_a_video_with_no_place_is_handed_back(tmp_path: Path) -> None:
    source = tmp_path / "plain.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10", "-t", "1", "-c:v", "mpeg4", str(source)
    )

    assert places.remove_places(source, tmp_path / "scratch") is None


# --- the door's own rules -----------------------------------------------------------------------


def test_a_file_sift_built_itself_is_rewritten_where_it_lies(tmp_path: Path) -> None:
    built = tmp_path / ".trip.mov.01ABC.sift-part"
    _clip(built, "mov")

    assert places.remove_places_from_own(built) is True
    assert b"+48.8577" not in built.read_bytes()
    assert places.remove_places_from_own(built) is False
    assert sorted(one.name for one in tmp_path.iterdir()) == [built.name]


def test_discard_removes_only_what_the_door_made(tmp_path: Path) -> None:
    source = tmp_path / "trip.jpg"
    _picture().save(source, exif=_exif())
    copy = places.remove_places(source, tmp_path / "scratch")
    assert copy is not None

    with pytest.raises(ValueError, match="not a copy"):
        places.discard(source)
    places.discard(copy)

    assert not copy.parent.exists()
    assert source.exists()


def test_a_picture_whose_tables_run_past_its_end_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "broken.jpg"
    _picture().save(source, exif=_exif())
    data = bytearray(source.read_bytes())
    # The EXIF segment claims to run far past the file.
    at = data.index(b"Exif\x00\x00") - 2
    data[at : at + 2] = b"\xff\xf0"
    source.write_bytes(bytes(data))

    with pytest.raises(places.CannotRemovePlaces):
        places.remove_places(source, tmp_path / "scratch")


def test_a_file_that_is_not_media_is_left_to_the_gate(tmp_path: Path) -> None:
    source = tmp_path / "notes.txt"
    source.write_bytes(b"GPS 48.8577 2.2950")

    assert places.remove_places(source, tmp_path / "scratch") is None


def test_strip_places_drops_every_key_that_names_a_place() -> None:
    answer = {
        "format": {"tags": {"title": "t", "location": "+1+2/", "LOCATION-eng": "+1+2/"}},
        "streams": [{"tags": {"com.apple.quicktime.location.ISO6709": "+1+2/", "encoder": "e"}}],
    }

    assert places.strip_places(answer) == {
        "format": {"tags": {"title": "t"}},
        "streams": [{"tags": {"encoder": "e"}}],
    }


# --- files made byte by byte ----------------------------------------------------------------------

#: An invented place as a video writes it (ISO 6709).
_PLACE = b"+48.8577+002.2950/"


def _made(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _tiff(
    order: str = "<",
    *,
    gps: bool = True,
    xmp: bytes | None = None,
    gps_pointer: int | None = None,
    gps_count: int = 2,
    seconds_at: int | None = None,
) -> bytes:
    """An EXIF body: a first directory holding a camera model, an XMP packet when one is given and
    the GPS pointer, then the GPS directory with the invented seconds among its values. The
    keywords write a pointer or a count that does not match the block, as a broken writer does."""
    fields = [(0x0110, 2, b"Model Q\x00")]
    if xmp is not None:
        fields.append((0x02BC, 7, xmp))
    count = len(fields) + gps
    values_at = 8 + 2 + 12 * count + 4
    entries = b""
    values = b""
    for tag, kind, value in fields:
        if len(value) <= 4:
            field = value.ljust(4, b"\x00")
        else:
            field = struct.pack(order + "I", values_at + len(values))
            values += value + bytes(len(value) & 1)
        entries += struct.pack(order + "HHI", tag, kind, len(value)) + field
    directory = b""
    if gps:
        gps_at = values_at + len(values)
        pointer = gps_at if gps_pointer is None else gps_pointer
        entries += struct.pack(order + "HHII", 0x8825, 4, 1, pointer)
        rationals = gps_at + 2 + 2 * 12 + 4 if seconds_at is None else seconds_at
        directory = (
            struct.pack(order + "H", gps_count)
            + struct.pack(order + "HHI", 1, 2, 2)
            + b"N\x00\x00\x00"
            + struct.pack(order + "HHII", 2, 5, 3, rationals)
            + bytes(4)
            + struct.pack(order + "6I", 48, 1, 51, 1, 2917, 100)
        )
    head = (b"II*\x00" if order == "<" else b"MM\x00*") + struct.pack(order + "I", 8)
    return head + struct.pack(order + "H", count) + entries + bytes(4) + values + directory


def _first_directory(tiff: bytes) -> Image.Exif:
    exif = Image.Exif()
    exif.load(b"Exif\x00\x00" + tiff)
    return exif


#: A scan with one stuffed byte in it, and the end of the picture.
_SCAN = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\x12\x34\xff\x00\x56\xff\xd9"


def _segment(marker: int, payload: bytes) -> bytes:
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


def _jpeg(*segments: bytes) -> bytes:
    return b"\xff\xd8" + b"".join(segments) + _SCAN


def _exif_segment(tiff: bytes) -> bytes:
    return _segment(0xE1, b"Exif\x00\x00" + tiff)


#: Where the EXIF body starts in `_jpeg(_exif_segment(...))`: the start marker, the segment's
#: marker and length, then the `Exif` header.
_TIFF_IN_JPEG = 2 + 4 + 6


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def _png(*chunks: bytes) -> bytes:
    header = _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
    picture = _png_chunk(b"IDAT", zlib.compress(b"\x00\x00"))
    return b"\x89PNG\r\n\x1a\n" + header + b"".join(chunks) + picture + _png_chunk(b"IEND", b"")


def _text(keyword: str, text: bytes) -> bytes:
    return _png_chunk(b"tEXt", keyword.encode("latin-1") + b"\x00" + text)


def _ztext(keyword: str, compressed: bytes) -> bytes:
    return _png_chunk(b"zTXt", keyword.encode("latin-1") + b"\x00\x00" + compressed)


def _itext(keyword: str, text: bytes) -> bytes:
    """An international text chunk, compressed, with no language and no translated keyword."""
    body = b"\x01\x00" + b"\x00\x00" + zlib.compress(text)
    return _png_chunk(b"iTXt", keyword.encode("latin-1") + b"\x00" + body)


def _raw_profile(kind: str, body: bytes) -> bytes:
    """ImageMagick's text form of a profile: its name, its length, then its bytes in hex."""
    return f"\n{kind}\n{len(body):8d}\n".encode("ascii") + body.hex().encode("ascii") + b"\n"


def _webp(*chunks: bytes) -> bytes:
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


#: A GIF's screen descriptor with no colour table, and one frame of one pixel.
_GIF_HEAD = b"GIF89a" + struct.pack("<HH", 1, 1) + b"\x00\x00\x00"
_GIF_FRAME = b"\x2c" + struct.pack("<HHHH", 0, 0, 1, 1) + b"\x00" + b"\x02\x02\x44\x01\x00"
#: What follows an XMP packet in a GIF, so its bytes read as blocks that end.
_GIF_XMP_TRAILER = b"\x01" + bytes(range(0xFF, -1, -1)) + b"\x00"


def _box(kind: bytes, *children: bytes) -> bytes:
    body = b"".join(children)
    return struct.pack(">I", 8 + len(body)) + kind + body


def _full_box(kind: bytes, *children: bytes, version: int = 0) -> bytes:
    return _box(kind, bytes((version, 0, 0, 0)), *children)


_FTYP = _box(b"ftyp", b"isom", bytes(4), b"isom")
_HEIF_FTYP = _box(b"ftyp", b"heic", bytes(4), b"mif1heic")
_XMP_UUID = bytes.fromhex("be7acfcb97a942e89c71999491e3afac")
_XYZ = _box(b"\xa9xyz", struct.pack(">HH", len(_PLACE), 0x15C7), _PLACE)
#: Where the samples of a file laid out as `_FTYP + _box(b"mdat", samples) + moov` start.
_SAMPLES_AT = len(_FTYP) + 8
#: Two positions, one sample each.
_SAMPLES = _PLACE + b"+48.8579+002.2952/"


def _track(entry: bytes, *tables: bytes) -> bytes:
    """A track whose one sample description is `entry`, with the sample tables given."""
    description = _full_box(b"stsd", struct.pack(">I", 1), entry)
    return _box(b"trak", _box(b"mdia", _box(b"minf", _box(b"stbl", description, *tables))))


def _stsz(fixed: int, count: int, *sizes: int) -> bytes:
    return _full_box(
        b"stsz", struct.pack(">II", fixed, count), *(struct.pack(">I", n) for n in sizes)
    )


def _stsc(*runs: tuple[int, int]) -> bytes:
    rows = (struct.pack(">III", first, count, 1) for first, count in runs)
    return _full_box(b"stsc", struct.pack(">I", len(runs)), *rows)


def _stco(*offsets: int) -> bytes:
    return _full_box(
        b"stco", struct.pack(">I", len(offsets)), *(struct.pack(">I", n) for n in offsets)
    )


def _co64(*offsets: int) -> bytes:
    return _full_box(
        b"co64", struct.pack(">I", len(offsets)), *(struct.pack(">Q", n) for n in offsets)
    )


def _positions(*tracks: bytes, samples: bytes = _SAMPLES) -> bytes:
    return _FTYP + _box(b"mdat", samples) + _box(b"moov", *tracks)


def _mebx(key: bytes) -> bytes:
    return _box(b"mebx", bytes(8), _box(b"keys", _box(b"keyd", b"mdta", key)))


def _key(name: bytes) -> bytes:
    return struct.pack(">I", 8 + len(name)) + b"mdta" + name


def _infe(item: int, kind: bytes) -> bytes:
    return _full_box(b"infe", struct.pack(">HH", item, 0), kind, b"\x00", version=2)


def _iloc(*items: tuple[int, int, list[tuple[int, int]]], sizes: int = 0x44) -> bytes:
    """A version 1 location table: four-byte offsets and lengths, no base offset, no index."""
    body = bytes((sizes, 0)) + struct.pack(">H", len(items))
    for item, method, spans in items:
        body += struct.pack(">HHHH", item, method, 0, len(spans))
        body += b"".join(struct.pack(">II", offset, length) for offset, length in spans)
    return _full_box(b"iloc", body, version=1)


#: An Exif item: the offset to its TIFF header, then the header and the body.
_EXIF_ITEM = struct.pack(">I", 6) + b"Exif\x00\x00" + _tiff()
#: Where the data of a file laid out as `_HEIF_FTYP + _box(b"mdat", data) + meta` starts.
_ITEMS_AT = len(_HEIF_FTYP) + 8


def _heif(*children: bytes, data: bytes = _EXIF_ITEM) -> bytes:
    meta = _full_box(b"meta", _box(b"hdlr", bytes(20)), *children)
    return _HEIF_FTYP + _box(b"mdat", data) + meta


def _exif_items(*items: tuple[int, int, list[tuple[int, int]]]) -> bytes:
    """A HEIF whose location table is `items`, each one an Exif item."""
    kinds = _full_box(
        b"iinf", struct.pack(">H", len(items)), *(_infe(n, b"Exif") for n, _m, _s in items)
    )
    return _heif(kinds, _iloc(*items))


def _ebml(ident: int, *children: bytes, size: bytes | None = None) -> bytes:
    body = b"".join(children)
    if size is None:
        size = (
            bytes((0x80 | len(body),))
            if len(body) < 0x7F
            else b"\x01" + len(body).to_bytes(7, "big")
        )
    return ident.to_bytes((ident.bit_length() + 7) // 8, "big") + size + body


#: The size an element is written with when its writer did not know it yet.
_OPEN = b"\x01" + b"\xff" * 7
_MKV_HEAD = _ebml(0x1A45DFA3, _ebml(0x4282, b"matroska"))
_CLUSTER = 0x1F43B675
_TAGS = 0x1254C367


def _mkv(*children: bytes, size: bytes | None = None) -> bytes:
    return _MKV_HEAD + _ebml(0x18538067, *children, size=size)


def _simple_tag(*children: bytes) -> bytes:
    return _ebml(0x67C8, *children)


def _tag_name(name: bytes) -> bytes:
    return _ebml(0x45A3, name)


def _tag_string(value: bytes) -> bytes:
    return _ebml(0x4487, value)


def _copied(tmp_path: Path, name: str, data: bytes) -> tuple[bytes, bytes]:
    """The source's bytes and its copy's, after checking the copy is the same length, carries no
    place a second reading finds, and left the source as it was."""
    source = _made(tmp_path, name, data)
    copy = places.remove_places(source, tmp_path / "scratch")
    assert copy is not None
    after = copy.read_bytes()
    assert len(after) == len(data)
    assert places.places_in(copy) == 0
    assert source.read_bytes() == data
    return data, after


# --- EXIF, byte by byte ---------------------------------------------------------------------------


def test_a_big_endian_exif_loses_its_gps_and_the_place_in_its_xmp(tmp_path: Path) -> None:
    before, after = _copied(tmp_path, "big.jpg", _jpeg(_exif_segment(_tiff(">", xmp=_XMP))))

    assert _seconds_bytes(before)
    assert not _seconds_bytes(after)
    assert b"48,51.4862N" not in after
    assert b'xmp:CreatorTool="Maker Q"' in after
    exif = _first_directory(after[_TIFF_IN_JPEG:])
    assert 0x8825 not in exif
    assert exif[0x0110] == "Model Q"


def test_an_xmp_packet_short_enough_to_sit_in_its_exif_entry_is_read_there(tmp_path: Path) -> None:
    _before, after = _copied(tmp_path, "short.jpg", _jpeg(_exif_segment(_tiff(xmp=b"<a/>"))))

    assert not _seconds_bytes(after)
    assert b"<a/>" in after


def _xmp_past_block(tiff: bytearray) -> None:
    # The XMP entry (the second) claims 999 bytes.
    struct.pack_into("<I", tiff, 10 + 12 + 4, 999)


def _directory_past_block(tiff: bytearray) -> None:
    struct.pack_into("<H", tiff, 8, 200)


@pytest.mark.parametrize(
    "breaking",
    [
        pytest.param(_xmp_past_block, id="xmp-past-its-block"),
        pytest.param(_directory_past_block, id="directory-past-its-block"),
    ],
)
def test_an_exif_body_that_runs_past_itself_is_zeroed_where_it_lies(
    tmp_path: Path, breaking: Callable[[bytearray], None]
) -> None:
    tiff = bytearray(_tiff(xmp=_XMP))
    breaking(tiff)

    _before, after = _copied(tmp_path, "broken.jpg", _jpeg(_exif_segment(bytes(tiff))))

    assert after[_TIFF_IN_JPEG : _TIFF_IN_JPEG + len(tiff)] == bytes(len(tiff))
    assert after.endswith(_SCAN)


def test_an_exif_body_cut_short_after_its_byte_order_is_zeroed(tmp_path: Path) -> None:
    _before, after = _copied(tmp_path, "cut.jpg", _jpeg(_exif_segment(b"II*\x00\x08\x00")))

    assert after[_TIFF_IN_JPEG : _TIFF_IN_JPEG + 6] == bytes(6)


@pytest.mark.parametrize(
    ("pointer", "count"),
    [
        pytest.param(0, 2, id="pointer-at-zero"),
        pytest.param(4000, 2, id="pointer-past-the-block"),
        pytest.param(None, 50, id="directory-past-the-block"),
    ],
)
def test_a_gps_pointer_that_reaches_no_directory_is_still_taken_out(
    tmp_path: Path, pointer: int | None, count: int
) -> None:
    """The pointer going is what matters: nothing reads a directory nothing points at."""
    tiff = _tiff(gps_pointer=pointer, gps_count=count)

    _before, after = _copied(tmp_path, "nowhere.jpg", _jpeg(_exif_segment(tiff)))

    exif = _first_directory(after[_TIFF_IN_JPEG:])
    assert 0x8825 not in exif
    assert exif[0x0110] == "Model Q"


def test_a_gps_value_stored_past_the_block_leaves_the_rest_of_the_directory_zeroed(
    tmp_path: Path,
) -> None:
    tiff = _tiff(seconds_at=4000)
    directory = len(tiff) - (2 + 2 * 12 + 4 + 24)

    _before, after = _copied(tmp_path, "far.jpg", _jpeg(_exif_segment(tiff)))

    start = _TIFF_IN_JPEG + directory
    assert after[start : start + 2 + 2 * 12 + 4] == bytes(2 + 2 * 12 + 4)
    assert 0x8825 not in _first_directory(after[_TIFF_IN_JPEG:])


# --- JPEG, byte by byte ---------------------------------------------------------------------------


def test_every_picture_of_a_multi_picture_jpeg_loses_its_gps(tmp_path: Path) -> None:
    one = _jpeg(_exif_segment(_tiff()))
    source = _made(tmp_path, "two.jpg", one + one)
    assert places.places_in(source) == 2

    _before, after = _copied(tmp_path, "two.jpg", one + one)

    assert not _seconds_bytes(after)
    assert after[len(one) + _TIFF_IN_JPEG :].startswith(b"II*\x00")


def test_markers_that_stand_alone_between_segments_are_stepped_over(tmp_path: Path) -> None:
    data = b"\xff\xd8" + b"\xff\xd0" + b"\xff\x01" + _exif_segment(_tiff()) + _SCAN

    _before, after = _copied(tmp_path, "restart.jpg", data)

    assert not _seconds_bytes(after)


def test_an_extended_xmp_segment_loses_its_place_properties(tmp_path: Path) -> None:
    header = b"http://ns.adobe.com/xmp/extension/\x00" + b"0" * 32 + struct.pack(">II", 1, 0)

    _before, after = _copied(tmp_path, "extended.jpg", _jpeg(_segment(0xE1, header + _XMP)))

    assert b"GPS" not in after
    assert b'xmp:CreatorTool="Maker Q"' in after


def test_an_app1_segment_of_another_kind_is_left_alone(tmp_path: Path) -> None:
    other = _segment(0xE1, b"http://ns.example.invalid/other/\x00GPS 48.8577")
    source = _made(tmp_path, "other.jpg", _jpeg(other, _exif_segment(_tiff(gps=False))))

    assert places.remove_places(source, tmp_path / "scratch") is None


# --- PNG, WebP and GIF, byte by byte ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("chunk", "dropped"),
    [
        pytest.param(
            _text("Raw profile type exif", _raw_profile("exif", b"Exif\x00\x00" + _tiff())),
            True,
            id="raw-exif-profile-with-gps",
        ),
        pytest.param(
            _text("Raw profile type exif", _raw_profile("exif", _tiff(gps=False))),
            False,
            id="raw-exif-profile-without-gps",
        ),
        pytest.param(
            _ztext("Raw profile type xmp", zlib.compress(_raw_profile("xmp", _XMP))),
            True,
            id="compressed-raw-xmp-profile-with-a-place",
        ),
        pytest.param(
            _text("Raw profile type iptc", _raw_profile("iptc", b"\x1c\x02\x00")),
            False,
            id="raw-iptc-profile",
        ),
        pytest.param(
            _text("Raw profile type exif", b"\nexif\n       2\nzz\n"),
            True,
            id="raw-profile-that-is-not-hex",
        ),
        pytest.param(_ztext("Comment", b"not deflate"), True, id="text-that-does-not-inflate"),
        pytest.param(_itext("XML:com.adobe.xmp", _XMP), True, id="compressed-xmp-with-a-place"),
        pytest.param(
            _itext("XML:com.adobe.xmp", b"<x:xmpmeta/>"), False, id="compressed-xmp-without"
        ),
    ],
)
def test_a_png_text_chunk_goes_when_it_holds_a_place_or_cannot_be_read(
    tmp_path: Path, chunk: bytes, dropped: bool
) -> None:
    source = _made(tmp_path, "text.png", _png(chunk))

    copy = places.remove_places(source, tmp_path / "scratch")

    if dropped:
        assert copy is not None
        assert copy.read_bytes() == _png()
    else:
        assert copy is None


def test_a_png_text_chunk_too_large_to_open_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(places, "_TEXT_CAP", 16)
    source = _made(tmp_path, "large.png", _png(_ztext("Comment", zlib.compress(b"x" * 100))))

    copy = places.remove_places(source, tmp_path / "scratch")

    assert copy is not None
    assert copy.read_bytes() == _png()


def test_a_gif_keeps_a_comment_and_an_xmp_extension_with_no_place(tmp_path: Path) -> None:
    comment = b"\x21\xfe\x04kept\x00"
    xmp = b"\x21\xff\x0bXMP DataXMP" + b"<x:xmpmeta/>" + _GIF_XMP_TRAILER
    source = _made(tmp_path, "kept.gif", _GIF_HEAD + comment + xmp + _GIF_FRAME + b"\x3b")

    assert places.remove_places(source, tmp_path / "scratch") is None


# --- MP4 and MOV, byte by byte --------------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(
            _FTYP
            + struct.pack(">I", 1)
            + b"moov"
            + struct.pack(">Q", 16 + 16 + len(_XYZ))
            + struct.pack(">I", 1)
            + b"udta"
            + struct.pack(">Q", 16 + len(_XYZ))
            + _XYZ,
            id="sizes-written-in-64-bits",
        ),
        pytest.param(
            _FTYP + bytes(4) + b"moov" + bytes(4) + b"udta" + _XYZ,
            id="sizes-that-run-to-the-end",
        ),
    ],
)
def test_boxes_sized_in_either_other_way_are_walked_to_their_place(
    tmp_path: Path, data: bytes
) -> None:
    _before, after = _copied(tmp_path, "sized.mp4", data)

    assert _PLACE not in after
    assert b"free" in after


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(_FTYP + _box(b"moov", _box(b"udta", _box(b"XMP_", _XMP))), id="xmp-box"),
        pytest.param(_FTYP + _box(b"moov", _box(b"uuid", _XMP_UUID, _XMP)), id="uuid-in-moov"),
        pytest.param(_FTYP + _box(b"uuid", _XMP_UUID, _XMP), id="uuid-at-the-top"),
    ],
)
def test_a_video_xmp_packet_loses_its_place_wherever_its_box_sits(
    tmp_path: Path, data: bytes
) -> None:
    _before, after = _copied(tmp_path, "xmp.mp4", data)

    assert b"GPS" not in after
    assert b'xmp:CreatorTool="Maker Q"' in after


def test_a_quicktime_meta_box_with_no_version_loses_its_place_key(tmp_path: Path) -> None:
    keys = _full_box(
        b"keys",
        struct.pack(">I", 2),
        _key(b"com.apple.quicktime.location.ISO6709"),
        _key(b"com.apple.quicktime.make"),
    )
    items = _box(
        b"ilst",
        _box(struct.pack(">I", 1), _box(b"data", bytes(8), _PLACE)),
        _box(struct.pack(">I", 2), _box(b"data", bytes(8), b"Maker Q")),
    )
    meta = _box(b"meta", _box(b"hdlr", bytes(8), b"mdta", bytes(12)), keys, items)

    _before, after = _copied(tmp_path, "keys.mov", _FTYP + _box(b"moov", meta))

    assert _PLACE not in after
    assert b"Maker Q" in after


def test_every_picture_in_a_video_cover_loses_its_gps(tmp_path: Path) -> None:
    cover = _box(
        b"covr",
        _box(b"data", bytes(8), _jpeg(_exif_segment(_tiff()))),
        _box(b"data", bytes(8), _png(_png_chunk(b"eXIf", _tiff()))),
        _box(b"data", bytes(8), b"BM" + bytes(12)),
        _box(b"name", b"cover"),
    )
    meta = _full_box(b"meta", _box(b"hdlr", bytes(8), b"mdir", bytes(12)), _box(b"ilst", cover))

    before, after = _copied(tmp_path, "cover.mp4", _FTYP + _box(b"moov", _box(b"udta", meta)))

    assert before.count(struct.pack("<II", 2917, 100)) == 2
    assert not _seconds_bytes(after)
    assert b"BM" + bytes(12) in after


@pytest.mark.parametrize(
    "track",
    [
        pytest.param(
            _track(_box(b"gpmd", bytes(8)), _stsz(0, 2, 18, 18), _stsc((1, 1)), _stco(28, 46)),
            id="gopro-sizes-each-own-chunk",
        ),
        pytest.param(
            _track(_box(b"camm", bytes(8)), _stsz(18, 2), _stsc((2, 2)), _co64(0, 28)),
            id="camera-motion-one-size-64-bit-offsets",
        ),
        pytest.param(
            _track(
                _mebx(b"com.apple.quicktime.location.ISO6709"),
                _stsz(18, 2),
                _stsc((1, 2)),
                _stco(28),
            ),
            id="apple-metadata-describing-a-location",
        ),
    ],
)
def test_a_track_of_positions_has_its_samples_zeroed_where_they_lie(
    tmp_path: Path, track: bytes
) -> None:
    before, after = _copied(tmp_path, "positions.mp4", _positions(track))

    assert after[_SAMPLES_AT : _SAMPLES_AT + len(_SAMPLES)] == bytes(len(_SAMPLES))
    assert after[len(_FTYP) + 8 + len(_SAMPLES) :] == before[len(_FTYP) + 8 + len(_SAMPLES) :]


@pytest.mark.parametrize(
    "track",
    [
        pytest.param(_box(b"trak", _box(b"tkhd", bytes(84))), id="no-media-box"),
        pytest.param(
            _box(b"trak", _box(b"mdia", _box(b"minf", _box(b"stbl", _box(b"stsd", bytes(4)))))),
            id="description-too-short",
        ),
        pytest.param(
            _track(_mebx(b"com.apple.quicktime.make"), _stsz(18, 2), _stsc((1, 2)), _stco(28)),
            id="apple-metadata-describing-something-else",
        ),
    ],
)
def test_a_track_that_is_not_positions_keeps_its_samples(tmp_path: Path, track: bytes) -> None:
    source = _made(tmp_path, "other.mp4", _positions(track))

    assert places.remove_places(source, tmp_path / "scratch") is None


# --- HEIF, byte by byte ---------------------------------------------------------------------------


def test_a_heif_exif_item_and_an_xmp_item_in_its_idat_lose_their_places(tmp_path: Path) -> None:
    kinds = _full_box(
        b"iinf",
        struct.pack(">H", 6),
        _infe(1, b"hvc1"),
        _infe(2, b"Exif"),
        _infe(3, b"mime"),
        _infe(4, b"Exif"),
        _infe(5, b"mime"),
        _full_box(b"infe", bytes(8), version=1),
        _box(b"free", bytes(4)),
    )
    table = _iloc(
        (2, 0, [(_ITEMS_AT, len(_EXIF_ITEM))]),
        (3, 1, [(0, len(_XMP))]),
        (4, 2, [(0, 4)]),
    )

    _before, after = _copied(tmp_path, "items.heic", _heif(kinds, table, _box(b"idat", _XMP)))

    assert not _seconds_bytes(after)
    assert b"GPS" not in after
    assert b"Model Q" in after
    assert b'xmp:CreatorTool="Maker Q"' in after


def test_a_heif_meta_box_with_no_location_table_has_nothing_to_edit(tmp_path: Path) -> None:
    kinds = _full_box(b"iinf", struct.pack(">H", 1), _infe(1, b"Exif"))
    source = _made(tmp_path, "tableless.heic", _heif(kinds))

    assert places.remove_places(source, tmp_path / "scratch") is None


# --- Matroska, byte by byte -----------------------------------------------------------------------


def test_clusters_written_without_a_size_are_walked_past_to_the_tags(tmp_path: Path) -> None:
    block = _ebml(0xA3, b"\x81\x00\x00\x80frame")
    tags = _ebml(
        _TAGS,
        _ebml(
            0x7373,
            _ebml(0x63C0),
            _simple_tag(_tag_name(b"LOCATION"), _tag_string(_PLACE)),
            _simple_tag(_tag_string(b"a tag with no name")),
            _simple_tag(_tag_string(b"Kept title"), _tag_name(b"TITLE")),
        ),
    )
    data = _mkv(
        _ebml(_CLUSTER, block, size=_OPEN), tags, _ebml(_CLUSTER, block, size=_OPEN), size=_OPEN
    )

    _before, after = _copied(tmp_path, "open.mkv", data)

    assert _PLACE not in after
    assert b"Kept title" in after
    assert after.count(b"frame") == 2


def test_a_picture_attached_to_a_matroska_file_loses_its_gps(tmp_path: Path) -> None:
    attached = _ebml(
        0x61A7, _ebml(0x466E, b"cover.jpg"), _ebml(0x465C, _jpeg(_exif_segment(_tiff())))
    )
    data = _mkv(_ebml(0x1941A469, _ebml(0xEC, bytes(2)), attached))

    _before, after = _copied(tmp_path, "attached.mkv", data)

    assert not _seconds_bytes(after)
    assert b"cover.jpg" in after


# --- what the door refuses ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "data"),
    [
        pytest.param("a.jpg", b"\xff\xd8\xff\xe1\x00", id="jpeg-cut-inside-a-length"),
        pytest.param("a.jpg", b"\xff\xd8\xff\xe0\x00\x04ab\x00" + _SCAN, id="jpeg-no-marker"),
        pytest.param("a.jpg", b"\xff\xd8\xff\xff\xff", id="jpeg-ends-in-fill-bytes"),
        pytest.param("a.jpg", b"\xff\xd8\xff\xe0\x00\x02", id="jpeg-ends-after-a-segment"),
        pytest.param("a.jpg", b"\xff\xd8" + _SCAN[:12], id="jpeg-ends-inside-its-scan"),
        pytest.param(
            "a.png", _png(struct.pack(">I", 9999) + b"tEXt"), id="png-chunk-past-the-file"
        ),
        pytest.param(
            "a.png", _png()[: -len(_png_chunk(b"IEND", b""))] + b"IEND\xaeB`\x82", id="png-no-end"
        ),
        pytest.param(
            "a.webp", _webp(b"VP8 " + struct.pack("<I", 1000) + b"abc"), id="webp-chunk-past"
        ),
        pytest.param("a.gif", _GIF_HEAD + b"\x21\xfe\x03abc\x3b", id="gif-ends-inside-a-block"),
        pytest.param("a.gif", b"GIF89a\x3b", id="gif-shorter-than-its-header"),
        pytest.param("a.gif", _GIF_HEAD + b"\x2c\x00\x3b", id="gif-frame-past-the-file"),
        pytest.param("a.gif", _GIF_HEAD + b"\x00\x3b", id="gif-block-of-no-kind"),
        pytest.param(
            "a.gif",
            b"GIF89a" + struct.pack("<HH", 1, 1) + b"\x80\x00\x00" + bytes(5) + b"\x3b",
            id="gif-no-trailer",
        ),
        pytest.param("a.mp4", _FTYP + struct.pack(">I", 4) + b"free", id="mp4-box-too-small"),
        pytest.param("a.mp4", _FTYP + struct.pack(">I", 1) + b"mdat", id="mp4-64-bit-size-cut"),
        pytest.param(
            "a.mp4",
            _FTYP + _box(b"moov", struct.pack(">I", 4) + b"udta"),
            id="mp4-child-box-too-small",
        ),
        pytest.param(
            "a.mov",
            _FTYP
            + _box(
                b"moov",
                _full_box(b"meta", _full_box(b"keys", struct.pack(">II", 1, 4), b"mdta")),
            ),
            id="mp4-key-shorter-than-its-header",
        ),
        pytest.param(
            "a.mp4",
            _positions(_track(_box(b"gpmd", bytes(8)), _stsc((1, 1)), _stco(28))),
            id="positions-without-sizes",
        ),
        pytest.param(
            "a.mp4",
            _positions(_track(_box(b"gpmd", bytes(8)), _stsz(18, 1), _stsc((1, 1)))),
            id="positions-without-offsets",
        ),
        pytest.param(
            "a.mp4",
            _positions(_track(_box(b"gpmd", bytes(8)), _stsz(18, 1), _stsc((1, 1)), _stco(9000))),
            id="positions-past-the-file",
        ),
        pytest.param(
            "a.heic",
            _heif(
                _full_box(b"iinf", struct.pack(">H", 1), _infe(1, b"Exif")),
                _iloc((1, 1, [(0, 8)])),
            ),
            id="heif-item-in-a-missing-idat",
        ),
        pytest.param(
            "a.heic", _exif_items((1, 0, [(_ITEMS_AT, 0)])), id="heif-item-to-the-end-of-the-file"
        ),
        pytest.param(
            "a.heic",
            _heif(
                _full_box(b"iinf", struct.pack(">H", 1), _infe(1, b"Exif")),
                _iloc((1, 0, [(_ITEMS_AT, 8)]), sizes=0x94),
            ),
            id="heif-nine-byte-offsets",
        ),
        pytest.param(
            "a.heic",
            _exif_items(
                (1, 0, [(_ITEMS_AT, len(_EXIF_ITEM))]), (2, 0, [(_ITEMS_AT, len(_EXIF_ITEM))])
            ),
            id="heif-two-items-in-one-place",
        ),
        pytest.param("a.mkv", _mkv() + b"\x80", id="mkv-id-with-no-size"),
        pytest.param("a.mkv", _mkv() + b"\x00\x00", id="mkv-id-of-no-length"),
        pytest.param("a.mkv", _mkv(_ebml(_TAGS, size=_OPEN)), id="mkv-open-element-not-a-cluster"),
        pytest.param(
            "a.mkv",
            _mkv(_ebml(_CLUSTER, size=_OPEN), _ebml(0xA3, size=_OPEN)),
            id="mkv-open-block-inside-a-cluster",
        ),
        pytest.param(
            "a.mkv", _mkv(_ebml(_TAGS, _ebml(0x7373, size=b"\x90"))), id="mkv-tag-past-its-tags"
        ),
        pytest.param(
            "a.mkv", _MKV_HEAD + _ebml(0x18538067, size=b"\x90"), id="mkv-segment-past-the-file"
        ),
        pytest.param("a.mkv", _mkv(_ebml(0xEC, size=b"\x90")), id="mkv-element-past-its-segment"),
    ],
)
def test_a_file_whose_tables_cannot_be_read_is_refused_and_nothing_is_written(
    tmp_path: Path, name: str, data: bytes
) -> None:
    source = _made(tmp_path, name, data)
    scratch = tmp_path / "scratch"

    with pytest.raises(places.CannotRemovePlaces):
        places.remove_places(source, scratch)
    assert not scratch.exists()
    assert source.read_bytes() == data


@pytest.mark.parametrize(
    ("name", "data"),
    [
        pytest.param("big.jpg", _jpeg(_exif_segment(_tiff())), id="picture-read-whole"),
        pytest.param("big.mp4", _FTYP + _box(b"moov", _box(b"udta", _XYZ)), id="moov-read-whole"),
    ],
)
def test_a_structure_too_large_to_examine_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, data: bytes
) -> None:
    monkeypatch.setattr(places, "_READ_CAP", 32)
    source = _made(tmp_path, name, data)

    with pytest.raises(places.CannotRemovePlaces, match="too large"):
        places.remove_places(source, tmp_path / "scratch")


def test_a_file_shorter_than_its_size_said_cannot_be_read() -> None:
    reader = places._Source(io.BytesIO(b"short"), 64)

    with pytest.raises(places._Unreadable, match="shorter"):
        reader.read(0, 32)


def test_a_file_cut_short_while_it_is_copied_is_refused_and_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _made(tmp_path, "trip.jpg", _jpeg(_exif_segment(_tiff())))
    planned = places._plan

    def plan_then_cut(path: Path) -> tuple[str, list[places._Splice]]:
        answer = planned(path)
        # Something else cuts the file short between the reading and the copying.
        source.write_bytes(source.read_bytes()[:4])
        return answer

    monkeypatch.setattr(places, "_plan", plan_then_cut)
    scratch = tmp_path / "scratch"

    with pytest.raises(places.CannotRemovePlaces, match="shorter"):
        places.remove_places(source, scratch)
    assert list(scratch.iterdir()) == []


def _write_unchanged(source: Path, destination: Path, _splices: object) -> None:
    """An edit that did not take: the copy is the source as it was."""
    destination.write_bytes(source.read_bytes())


def test_a_copy_still_holding_a_place_is_refused_and_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(places, "_write", _write_unchanged)
    source = _made(tmp_path, "trip.jpg", _jpeg(_exif_segment(_tiff())))
    scratch = tmp_path / "scratch"

    with pytest.raises(places.CannotRemovePlaces, match="still there"):
        places.remove_places(source, scratch)
    assert list(scratch.iterdir()) == []


def test_a_built_file_still_holding_a_place_after_its_edit_is_left_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(places, "_write", _write_unchanged)
    data = _jpeg(_exif_segment(_tiff()))
    built = _made(tmp_path, ".trip.jpg.01ABC.sift-part", data)

    with pytest.raises(places.CannotRemovePlaces, match="still there"):
        places.remove_places_from_own(built)
    assert built.read_bytes() == data
    assert [one.name for one in tmp_path.iterdir()] == [built.name]
