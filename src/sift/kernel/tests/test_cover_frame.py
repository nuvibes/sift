# SPDX-License-Identifier: AGPL-3.0-or-later
"""A cover's frame: the window of its picture it is drawn as.

What is checked here, and why each one:

- **The bounds, on the way in.** A frame is four fractions of the picture and nothing else; one that
  reaches outside it, or is too small to cut, is refused by the model every PUT reads.
- **The binding.** A stored frame names the picture it was chosen on, and a row whose cover has
  moved (by any writer, including the ones that know nothing of frames) no longer has one.
- **The address.** A framed cover is kept by the browser only under an address that names the frame,
  so moving the window moves the address and the old window is never drawn from the cache.
- **The cut.** The picture served IS the window: a synthetic picture, half one colour and half
  another, framed on one half, comes back as that colour and that width.
- **The act.** Moving the window over the same picture is a reframe and says so; choosing another
  picture says what it always said.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from sift.kernel import media
from sift.kernel.access import Viewer
from sift.kernel.access.viewer import Role
from sift.kernel.config import Settings
from sift.kernel.cover_frame import CoverFrame, frame_of, stored_frame
from sift.kernel.covers import (
    ChosenCover,
    CoverPictures,
    bytes_reader,
    chosen_from_row,
    cover_change,
    forget_displaced,
    frame_served,
    names_its_cover,
    serve_cover,
    upload_kept_by_put,
)
from sift.kernel.db import Database
from sift.kernel.serving import face_version

pytestmark = pytest.mark.anyio

_HALF = CoverFrame(x=0.5, y=0.0, w=0.5, h=1.0)


def _request(query: str = "") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "headers": [],
            "query_string": query.encode(),
            "app": FastAPI(),
        }
    )


def _two_halves(width: int = 64, height: int = 32) -> bytes:
    """A PNG whose left half is red and whose right half is blue, built in the open.

    Two flat colours so a cut can be judged by its pixels: the right half framed comes back blue
    and half as wide, and a cut that ignored the frame comes back with red in it.
    """

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    half = width // 2
    row = b"\x00" + b"\xff\x00\x00" * half + b"\x00\x00\xff" * (width - half)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


# --- the bounds ---------------------------------------------------------------------------------


def test_a_frame_inside_the_picture_is_taken_and_rounded() -> None:
    frame = CoverFrame.model_validate({"x": 0.123456, "y": 0.2, "w": 0.5, "h": 0.61234})
    assert (frame.x, frame.y, frame.w, frame.h) == (0.1235, 0.2, 0.5, 0.6123)


@pytest.mark.parametrize(
    "said",
    [
        {"x": -0.1, "y": 0.0, "w": 0.5, "h": 0.5},  # before the picture starts
        {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.5},  # no width at all
        {"x": 0.0, "y": 0.0, "w": 0.005, "h": 0.5},  # too small to cut
        {"x": 0.6, "y": 0.0, "w": 0.5, "h": 0.5},  # runs off the right edge
        {"x": 0.0, "y": 0.7, "w": 0.5, "h": 0.5},  # runs off the bottom
        {"x": 0.0, "y": 0.0, "w": 1.2, "h": 0.5},  # wider than the picture
    ],
)
def test_a_frame_that_is_not_inside_the_picture_is_refused(said: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        CoverFrame.model_validate(said)


def test_a_hair_past_the_edge_is_pulled_in_rather_than_refused() -> None:
    """Floating point on the client lands a hair outside as often as inside."""
    frame = CoverFrame.model_validate({"x": 0.5, "y": 0.25, "w": 0.5004, "h": 0.7503})
    assert frame.x + frame.w <= 1.0
    assert frame.y + frame.h <= 1.0


# --- the binding --------------------------------------------------------------------------------


def test_a_stored_frame_reads_back_while_the_row_names_its_picture() -> None:
    stored = stored_frame(_HALF, asset_id="a1", at_ms=1500, upload_id=None)
    assert frame_of(stored, asset_id="a1", at_ms=1500, upload_id=None) == _HALF


@pytest.mark.parametrize(
    ("asset_id", "at_ms", "upload_id"),
    [
        ("a2", 1500, None),  # another file
        ("a1", 3000, None),  # another moment of the same file
        ("a1", None, None),  # the file's own still rather than the moment
        (None, None, "u1"),  # an uploaded picture
        (None, None, None),  # no cover at all: a deleted file's `ON DELETE SET NULL`
    ],
)
def test_a_frame_is_dropped_once_the_cover_names_another_picture(
    asset_id: str | None, at_ms: int | None, upload_id: str | None
) -> None:
    """The writers that move a cover without knowing about frames leave the column alone, and
    this is why that is safe: the window says which picture it was chosen on."""
    stored = stored_frame(_HALF, asset_id="a1", at_ms=1500, upload_id=None)
    assert frame_of(stored, asset_id=asset_id, at_ms=at_ms, upload_id=upload_id) is None


def test_the_whole_picture_is_stored_as_no_frame() -> None:
    whole = CoverFrame(x=0, y=0, w=1, h=1)
    assert stored_frame(whole, asset_id="a1", at_ms=None, upload_id=None) is None


@pytest.mark.parametrize("raw", ["", "{", "[]", '{"of":"asset:a1@","x":2,"y":0,"w":1,"h":1}'])
def test_a_column_that_does_not_read_as_a_frame_is_the_whole_picture(raw: str) -> None:
    assert frame_of(raw, asset_id="a1", at_ms=None, upload_id=None) is None


def test_the_row_shape_every_service_reads() -> None:
    row = {
        "cover_asset_id": None,
        "cover_at_ms": None,
        "cover_upload_id": "u1",
        "cover_frame": stored_frame(_HALF, asset_id=None, at_ms=None, upload_id="u1"),
    }
    assert chosen_from_row(row) == ChosenCover(upload_id="u1", frame=_HALF)


# --- the address --------------------------------------------------------------------------------


def test_a_framed_cover_is_kept_only_under_an_address_naming_its_frame() -> None:
    chosen = ChosenCover(asset_id="a1", at_ms=1500, frame=_HALF)
    stamp = face_version(3)
    assert names_its_cover(_request(f"v={stamp}.a1.1500.{_HALF.token}"), chosen, stamp=3)
    # The address from before the frame: the one a browser may be holding for a week.
    assert not names_its_cover(_request(f"v={stamp}.a1.1500"), chosen, stamp=3)
    moved = CoverFrame(x=0.25, y=0.0, w=0.5, h=1.0)
    assert not names_its_cover(_request(f"v={stamp}.a1.1500.{moved.token}"), chosen, stamp=3)


def test_an_uploaded_cover_names_its_frame_too() -> None:
    chosen = ChosenCover(upload_id="u1", frame=_HALF)
    stamp = face_version(3)
    assert names_its_cover(_request(f"v={stamp}.u1.{_HALF.token}"), chosen, stamp=3)
    assert not names_its_cover(_request(f"v={stamp}.u1"), chosen, stamp=3)


def test_the_whole_picture_is_asked_for_by_name_and_names_no_frame() -> None:
    chosen = ChosenCover(asset_id="a1", frame=_HALF)
    stamp = face_version(3)
    asked = _request(f"v={stamp}.a1&whole=1")
    assert frame_served(asked, chosen) is None
    assert names_its_cover(asked, chosen, stamp=3)


def test_the_token_is_the_four_numbers_in_ten_thousandths() -> None:
    """The client composes the same string (`frameToken` in `lib/entity/cover-frame.ts`)."""
    assert CoverFrame(x=0.125, y=0.0, w=0.5, h=0.6667).token == "f1250-0-5000-6667"


# --- the cut ------------------------------------------------------------------------------------


async def _pixels(path: Path, settings: Settings) -> tuple[int, int, bytes]:
    probe = await media.run_json(
        [
            settings.ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ],
        time_limit=30,
    )
    stream = probe["streams"][0]
    raw = await media.run(
        [
            settings.ffmpeg_path,
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        time_limit=30,
        capture=True,
    )
    return int(stream["width"]), int(stream["height"]), raw


async def test_the_picture_served_is_the_window_and_only_the_window(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    database = Database(tmp_path / "covers.sqlite3")
    await database.connect()
    try:
        pictures = CoverPictures(database, settings)
        source = tmp_path / "halves.png"
        source.write_bytes(_two_halves())

        framed = await pictures.framed(source, _HALF)

        assert framed is not None
        width, height, raw = await _pixels(framed, settings)
        assert (width, height) == (32, 32)
        reds = raw[0::3]
        blues = raw[2::3]
        # A JPEG is not exact at a hard edge, so the judgement is the average: a cut that kept
        # the red half would put it near 128 or above.
        assert sum(reds) / len(reds) < 40
        assert sum(blues) / len(blues) > 200
        # Kept, and the same file the second time: the cut is made once.
        assert await pictures.framed(source, _HALF) == framed
    finally:
        await database.close()


async def test_a_moved_window_is_a_different_cut(tmp_path: Path) -> None:
    """The name is the source's identity and the frame's, so nothing needs invalidating."""
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    database = Database(tmp_path / "covers.sqlite3")
    await database.connect()
    try:
        pictures = CoverPictures(database, settings)
        source = tmp_path / "halves.png"
        source.write_bytes(_two_halves())
        left = CoverFrame(x=0.0, y=0.0, w=0.5, h=1.0)

        one = await pictures.framed(source, _HALF)
        other = await pictures.framed(source, left)

        assert one is not None and other is not None and one != other
        _, _, raw = await _pixels(other, settings)
        assert sum(raw[0::3]) / len(raw[0::3]) > 200
    finally:
        await database.close()


async def test_an_uploaded_cover_is_served_as_its_window(tmp_path: Path) -> None:
    """Through `serve_cover` itself, so what is proved is what a screen is sent: the upload is
    re-encoded on the way in, framed on the way out, and the whole picture is still there to be
    asked for by name."""
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    database = Database(tmp_path / "covers.sqlite3")
    await database.connect()
    try:
        await database.initialize_schema()
        pictures = CoverPictures(database, settings)
        upload_id = await pictures.receive(bytes_reader(_two_halves(128, 64)))
        chosen = ChosenCover(upload_id=upload_id, frame=_HALF)
        viewer = Viewer(id="01HX00000000000000000000AA", role=Role.ADMIN)

        framed = await serve_cover(
            _request(), cast(Any, None), viewer, chosen=chosen, pictures=pictures
        )
        whole = await serve_cover(
            _request("whole=1"), cast(Any, None), viewer, chosen=chosen, pictures=pictures
        )

        (tmp_path / "framed.jpg").write_bytes(bytes(framed.body))
        (tmp_path / "whole.jpg").write_bytes(bytes(whole.body))
        framed_width, _, framed_raw = await _pixels(tmp_path / "framed.jpg", settings)
        whole_width, _, _ = await _pixels(tmp_path / "whole.jpg", settings)
        assert framed_width * 2 == whole_width
        assert sum(framed_raw[0::3]) / len(framed_raw[0::3]) < 40
    finally:
        await database.close()


# --- the act ------------------------------------------------------------------------------------


def test_moving_the_window_over_the_same_picture_is_a_reframe() -> None:
    before = ChosenCover(asset_id="a1", at_ms=1500)
    change = cover_change(before, asset_id="a1", at_ms=1500, upload_id=None, frame=_HALF)
    assert change.object is None
    assert json.loads(change.payload or "{}") == {"cover": "reframed"}
    assert frame_of(change.frame, asset_id="a1", at_ms=1500, upload_id=None) == _HALF


def test_choosing_another_picture_says_what_it_always_said() -> None:
    before = ChosenCover(asset_id="a1", at_ms=1500, frame=_HALF)
    change = cover_change(before, asset_id="a2", at_ms=None, upload_id=None, frame=_HALF)
    assert change.object is not None and change.object.id == "a2"
    assert change.payload is None


def test_writing_the_same_frame_again_is_not_a_reframe() -> None:
    before = ChosenCover(asset_id="a1", frame=_HALF)
    change = cover_change(before, asset_id="a1", at_ms=None, upload_id=None, frame=_HALF)
    assert change.object is not None


def test_a_put_may_name_only_the_upload_that_is_already_the_cover() -> None:
    before = ChosenCover(upload_id="u1")
    assert upload_kept_by_put("u1", asset_id=None, frame=_HALF, before=before) == "u1"
    with pytest.raises(HTTPException) as refused:
        upload_kept_by_put("u2", asset_id=None, frame=_HALF, before=before)
    assert refused.value.status_code == 404


async def test_a_reframe_keeps_the_uploaded_picture_it_reframes(tmp_path: Path) -> None:
    """An uploaded cover owns its bytes, and a cover change drops the one it replaced. A reframe
    replaces nothing: forgetting the picture that is still the cover would turn moving a window
    into losing the picture."""
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    database = Database(tmp_path / "covers.sqlite3")
    await database.connect()
    try:
        await database.initialize_schema()
        pictures = CoverPictures(database, settings)
        upload_id = await pictures.receive(bytes_reader(_two_halves()))
        before = ChosenCover(upload_id=upload_id)

        await forget_displaced(pictures, before, after=upload_id)
        assert await pictures.path_of(upload_id) is not None

        await forget_displaced(pictures, before, after=None)
        assert await pictures.path_of(upload_id) is None
    finally:
        await database.close()


def test_a_frame_with_no_picture_is_refused() -> None:
    with pytest.raises(HTTPException) as refused:
        upload_kept_by_put(None, asset_id=None, frame=_HALF, before=ChosenCover(asset_id="a1"))
    assert refused.value.status_code == 422
