# SPDX-License-Identifier: AGPL-3.0-or-later
"""What Sift generates carries none of the camera's notes. Originals keep theirs untouched; a
thumbnail is re-encoded from the picture alone, asserted on a real file through the real encoder.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from sift.kernel.config import Settings
from sift.slices.media_jobs import ffmpeg

pytestmark = [pytest.mark.integration]

FIXTURES = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"

# A position near nobody, searched for in the output.
LATITUDE = (51, 30, 0)
LONGITUDE = (0, 7, 0)

CAMERA_SERIAL = b"SERIAL-DO-NOT-LEAK-12345"


def _rational(numerator: int, denominator: int = 1) -> bytes:
    return struct.pack(">II", numerator, denominator)


def _exif_with_gps() -> bytes:
    """A minimal real APP1 segment carrying a GPS IFD and a camera serial, built by hand."""
    # GPS IFD: version, latitude ref, latitude, longitude ref, longitude.
    gps_entries = [
        (0x0001, 2, 2, b"N\x00"),  # GPSLatitudeRef
        (0x0002, 5, 3, None),  # GPSLatitude -> offset
        (0x0003, 2, 2, b"E\x00"),  # GPSLongitudeRef
        (0x0004, 5, 3, None),  # GPSLongitude -> offset
    ]

    lat = b"".join(_rational(value) for value in LATITUDE)
    lon = b"".join(_rational(value) for value in LONGITUDE)

    # TIFF header is at offset 0 of our little block; values live after the IFD.
    ifd_size = 2 + len(gps_entries) * 12 + 4
    values_at = 8 + ifd_size
    lat_at, lon_at = values_at, values_at + len(lat)

    ifd = struct.pack(">H", len(gps_entries))
    for tag, kind, count, inline in gps_entries:
        if inline is not None:
            payload = inline + b"\x00" * (4 - len(inline))
        else:
            payload = struct.pack(">I", lat_at if tag == 0x0002 else lon_at)
        ifd += struct.pack(">HHI", tag, kind, count) + payload
    ifd += struct.pack(">I", 0)

    # Root IFD points at the GPS IFD and carries the serial as a maker-ish string.
    gps_block_at = 8 + (2 + 2 * 12 + 4)
    root = struct.pack(">H", 2)
    root += struct.pack(">HHI", 0x8825, 4, 1) + struct.pack(">I", gps_block_at)  # GPSInfo
    serial_at = gps_block_at + ifd_size + len(lat) + len(lon)
    root += struct.pack(">HHI", 0xA431, 2, len(CAMERA_SERIAL) + 1)
    root += struct.pack(">I", serial_at)  # BodySerialNumber
    root += struct.pack(">I", 0)

    tiff = b"MM\x00\x2a" + struct.pack(">I", 8) + root + ifd + lat + lon + CAMERA_SERIAL + b"\x00"
    body = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body


def _tagged_jpeg(destination: Path) -> Path:
    """A real JPEG with a real EXIF block spliced in after the SOI marker."""
    original = (FIXTURES / "accepted.jpg").read_bytes()
    assert original[:2] == b"\xff\xd8", "the fixture is not a JPEG any more"

    # Skip the existing APP0 so the file has one metadata segment and it is ours.
    app0_length = struct.unpack(">H", original[4:6])[0]
    rest = original[4 + app0_length :]
    destination.write_bytes(b"\xff\xd8" + _exif_with_gps() + rest)
    return destination


def test_the_fixture_really_carries_what_we_are_looking_for(tmp_path: Path) -> None:
    """The fixture really carries the metadata, so the test above is not a false pass."""
    tagged = _tagged_jpeg(tmp_path / "tagged.jpg")
    raw = tagged.read_bytes()

    assert b"Exif" in raw
    assert CAMERA_SERIAL in raw

    probed = subprocess.run(
        [
            # The vendored tool Sift uses, not a fixed path.
            Settings(data_dir=tmp_path / "d", cache_dir=tmp_path / "c").ffprobe_path,
            "-hide_banner",
            "-show_entries",
            "format_tags",
            str(tagged),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probed.returncode == 0, probed.stderr


@pytest.mark.regression
async def test_a_generated_thumbnail_carries_no_location_or_serial(
    tmp_path: Path, settings: object
) -> None:
    """A generated thumbnail carries no GPS or serial, searched for in the bytes themselves."""
    if shutil.which("ffmpeg") is None:  # pragma: no cover - ffmpeg is installed in CI
        pytest.skip("ffmpeg is not installed")

    tagged = _tagged_jpeg(tmp_path / "tagged.jpg")
    thumb = tmp_path / "thumb.jpg"

    await ffmpeg.run(
        ffmpeg.thumbnail_args(tagged, thumb, timestamp_ms=0, settings=settings)  # type: ignore[arg-type]
    )

    assert thumb.exists(), "the thumbnail was not produced"
    produced = thumb.read_bytes()

    assert CAMERA_SERIAL not in produced, "the camera serial survived into the thumbnail"
    assert b"Exif" not in produced, "an EXIF block survived into the thumbnail"
    assert b"GPS" not in produced, "GPS metadata survived into the thumbnail"
