# SPDX-License-Identifier: AGPL-3.0-or-later
"""The receiver's own strip: a received photo is stripped here whatever the sender did."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from sift.kernel.config import Settings
from sift.slices.swap import ingest

pytestmark = [pytest.mark.integration]

CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"
SECRET = b"SIFTSECRET-a-note-the-sender-left"


def _segment(marker: int, payload: bytes) -> bytes:
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


async def test_a_received_photo_arrives_with_no_comment_and_no_exif(
    tmp_path: Path, settings: Settings
) -> None:
    """A comment is not a place, so the import after this would let it through."""
    raw = (CORPUS / "accepted.jpg").read_bytes()
    sent = raw[:2] + _segment(0xE1, b"Exif\x00\x00" + SECRET) + _segment(0xFE, SECRET) + raw[2:]
    source = tmp_path / "k1.part"
    source.write_bytes(sent)
    into = tmp_path / "scratch"
    into.mkdir()

    landed = await ingest.strip(source, into, stem="IMG_2041", suffix=".JPEG", settings=settings)

    assert landed == into / "IMG_2041.JPEG"
    kept = landed.read_bytes()
    assert kept.startswith(b"\xff\xd8") and SECRET not in kept
