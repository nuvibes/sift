# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real application, real media in several codecs, and real ffmpeg.

The media is generated rather than checked in, matching the rule `media_jobs` set: a repository is
not a place to keep video files, and a fixture built by ffmpeg on the way in describes the codec it
claims to. Three files, because this slice's whole job is telling them apart:

- **`h264.mp4`**: what a browser can play. The direct-play path, and the one that must stay free.
- **`hevc.mp4`**: what an older browser cannot. The transcode path.
- **`hevc.mkv`**: the same stream in a container most browsers cannot demux. What would be the
  remux path; that tier is currently held closed, so this exercises the fall-through to transcode.

Generating these costs a few seconds per session, so they are session-scoped and shared. Nothing
here mutates them.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sift.kernel.config import get_settings
from sift.kernel.content import Asset
from sift.kernel.db import Database
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.ids import new_id
from sift.kernel.jobs import WorkerPool
from sift.main import create_app
from sift.testing.auth import establish_session, hide_for_caller

_EPOCH = 1_700_000_000

PASSWORD = "A-Player-Test-Passw0rd!"

#: How long the generated clips are. Long enough to be several segments at 2 seconds each, short
#: enough that generating and transcoding them does not dominate the suite.
CLIP_SECONDS = 10

_INSERT_ASSET = """
INSERT INTO assets
    (id, identity, media_type, mime, width, height, duration_ms, fps, size_bytes,
     container, vcodec, acodec, original_filename, added_at, probed_at, bit_depth, color_transfer)
VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_LOCATION = """
INSERT INTO asset_locations
    (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_SHARE_ITEM = """
INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)
VALUES (?, 'item', ?, ?, 'share', 0)
ON CONFLICT DO NOTHING
"""


def _ffmpeg(*args: str) -> None:
    """Run ffmpeg, raising with its own stderr so a fixture failure explains itself."""
    binary = shutil.which("ffmpeg")
    if binary is None:  # pragma: no cover - every supported install has it
        pytest.skip("ffmpeg is not on PATH")
    result = subprocess.run(
        [binary, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed building a fixture: {result.stderr.decode()}")


@dataclass(frozen=True, slots=True)
class Clip:
    """One generated file and what it actually is."""

    path: Path
    container: str
    vcodec: str
    acodec: str
    width: int
    height: int
    fps: float
    duration_ms: int
    #: Eight and nothing stated for the ordinary clips; the HDR one says what it is.
    bit_depth: int = 8
    color_transfer: str = ""


@pytest.fixture(scope="session")
def clips(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Clip]:
    """Three real files: H.264 in MP4, HEVC in MP4, and the same HEVC in Matroska."""
    directory = tmp_path_factory.mktemp("player-media")
    source = ["-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=15:duration={CLIP_SECONDS}"]
    # A real audio track, because a file with no audio takes a different branch in the policy and
    # testing only the silent case would leave the ordinary one unexercised.
    audio = ["-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_SECONDS}"]

    # A four-second keyframe interval: two segments' worth, and a realistic shape for real media.
    #
    # This is not incidental. Left on its defaults, the encoder at this frame rate puts a keyframe
    # only at the start: the whole clip is one group of pictures, so every possible segment begins
    # at frame zero and any keyframe-alignment mistake looks correct. Fixtures that make the hard
    # case impossible are worse than no fixtures, because they read as coverage.
    gop = ["-g", "60", "-keyint_min", "60", "-sc_threshold", "0"]

    h264 = directory / "h264.mp4"
    _ffmpeg(
        *source, *audio,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", *gop,
        "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(h264),
    )  # fmt: skip

    hevc = directory / "hevc.mp4"
    _ffmpeg(
        *source, *audio,
        "-c:v", "libx265", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-tag:v", "hvc1",
        "-x265-params", "keyint=60:min-keyint=60:scenecut=0",
        "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(hevc),
    )  # fmt: skip

    # The same encoded stream, repackaged. `-c copy` so it really is the identical video in a
    # different box, which is precisely what the remux tier exists for.
    hevc_mkv = directory / "hevc.mkv"
    _ffmpeg("-i", str(hevc), "-c", "copy", str(hevc_mkv))
    # A ten-bit HDR file: PQ transfer, BT.2020 primaries, tagged the way a real one is. The
    # compat encode has to map it, and this is the file that proves the map runs on the ffmpeg
    # that ships rather than only appearing in the command line.
    hdr = directory / "hdr.mp4"
    _ffmpeg(
        *source, *audio,
        "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-preset", "ultrafast", "-tag:v", "hvc1",
        "-x265-params",
        "keyint=60:min-keyint=60:scenecut=0:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc",
        "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc",
        "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(hdr),
    )  # fmt: skip

    shape: dict[str, Any] = {
        "width": 320,
        "height": 240,
        "fps": 15.0,
        "duration_ms": CLIP_SECONDS * 1000,
    }
    return {
        "h264": Clip(path=h264, container="mp4", vcodec="h264", acodec="aac", **shape),
        "hevc": Clip(path=hevc, container="mp4", vcodec="hevc", acodec="aac", **shape),
        "hevc_mkv": Clip(path=hevc_mkv, container="mkv", vcodec="hevc", acodec="aac", **shape),
        "hdr": Clip(
            path=hdr,
            container="mp4",
            vcodec="hevc",
            acodec="aac",
            bit_depth=10,
            color_transfer="smpte2084",
            **shape,
        ),
    }


def write(db_path: Path, statements: list[tuple[str, tuple[object, ...]]]) -> None:
    """Run writes on a connection of this helper's own, on its own loop.

    The test client drives the application on its own event loop, and a write issued from the
    test's loop meets a lock held on the app's, which fails as "bound to a different event loop"
    and has nothing to do with what is being tested.
    """

    async def run() -> None:
        database = Database(db_path, readers=1)
        await database.connect()
        try:
            async with database.write() as connection:
                for sql, params in statements:
                    await connection.execute(sql, params)
        finally:
            await database.close()

    asyncio.run(run())


def read(db_path: Path, sql: str, params: tuple[object, ...] = ()) -> list[dict[str, Any]]:
    """Read through the kernel's database handle, on a connection and a loop of this helper's own.

    Through the kernel handle rather than `sqlite3` directly, and that is a rule with a lint behind
    it: a connection opened by hand misses the pragmas (foreign keys off, so cascades silently
    stop working) and the single-writer lock. A test that reads its own way is a test that can
    disagree with the application about what the database says.
    """

    async def run() -> list[dict[str, Any]]:
        database = Database(db_path, readers=1)
        await database.connect()
        try:
            rows = await database.fetch_all(sql, params)
            return [dict(row) for row in rows]
        finally:
            await database.close()

    return asyncio.run(run())


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(request: pytest.FixtureRequest, app: FastAPI) -> Iterator[TestClient]:
    # A test that asks for `idle_workers` needs it in force BEFORE the application starts, and
    # the pool starts as the client enters, so it is asked for here, by name, rather than left
    # to the order the test happened to list its fixtures in.
    if "idle_workers" in request.fixturenames:
        request.getfixturevalue("idle_workers")
    with TestClient(app) as running:
        yield running


@pytest.fixture
def idle_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker pool that never claims anything, for a test about what was ASKED of the queue.

    The application under test runs a real pool, and a probe of a row with no file behind it, or
    a stream copy of a one-second fixture, is claimed and finished within milliseconds of being
    queued, sooner on a process other tests have warmed than on a cold one. A test counting
    what is waiting in the queue would therefore read 1 when it ran alone and 0 when it ran after
    the rest of the file, which is not something the test can be arranged around: the worker's
    timing is the worker's. With no worker, what was asked for stays exactly as it was asked."""

    async def no_workers(self: WorkerPool) -> None: ...

    monkeypatch.setattr(WorkerPool, "start", no_workers)


def db_path(client: TestClient) -> Path:
    return client.app.state.database.path  # type: ignore[attr-defined,no-any-return]


def sign_in(client: TestClient, role: str = "admin", *, who: str = "one") -> str:
    """Become somebody. Returns their user id."""
    user_id, token, csrf = establish_session(
        db_path(client), role=role, username=f"player-{role}-{who}", password=PASSWORD
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


@dataclass(frozen=True, slots=True)
class Library:
    """A root, a folder, and the assets the tests name."""

    root: str
    folder: str
    ids: dict[str, str]
    media: Path

    def id_of(self, name: str) -> str:
        return self.ids[name]


@pytest.fixture
def library(client: TestClient, tmp_path: Path, clips: dict[str, Clip]) -> Library:
    """Every generated clip, copied into a real library root with a real row behind it."""
    media = tmp_path / "media" / "clips"
    media.mkdir(parents=True, exist_ok=True)

    root, folder = new_id(), new_id()
    statements: list[tuple[str, tuple[object, ...]]] = [
        (
            "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
            (root, "library", str(tmp_path / "media"), _EPOCH),
        ),
        (
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
            (folder, root, None, "clips", "clips"),
        ),
    ]

    ids: dict[str, str] = {}
    for name, clip in clips.items():
        asset_id = new_id()
        ids[name] = asset_id
        filename = clip.path.name
        # A distinct name per asset, so two clips that share a filename do not collide on the
        # unique (root, rel_path) index.
        stored = f"{name}-{filename}"
        shutil.copy(clip.path, media / stored)
        mime = "video/x-matroska" if clip.container == "mkv" else "video/mp4"
        statements.append(
            (
                _INSERT_ASSET,
                (
                    asset_id,
                    f"digest-{name}",
                    mime,
                    clip.width,
                    clip.height,
                    clip.duration_ms,
                    clip.fps,
                    (media / stored).stat().st_size,
                    clip.container,
                    clip.vcodec,
                    clip.acodec,
                    stored,
                    _EPOCH,
                    _EPOCH,
                    clip.bit_depth,
                    clip.color_transfer,
                ),
            )
        )
        statements.append(
            (
                _INSERT_LOCATION,
                (new_id(), asset_id, root, folder, f"clips/{stored}", stored, _EPOCH, _EPOCH),
            )
        )

    write(db_path(client), statements)
    return Library(root=root, folder=folder, ids=ids, media=tmp_path / "media")


def share(client: TestClient, asset_id: str, user_id: str) -> None:
    """Share one asset with one person, at the item level."""
    write(db_path(client), [(_SHARE_ITEM, (new_id(), asset_id, user_id))])


def conceal(client: TestClient, asset_id: str) -> None:
    """Hide an asset for whoever is signed in, which is what 'concealed' means to them."""
    hide_for_caller(client, "asset", asset_id)


#: What a modern desktop browser reports. The direct-play case for everything here.
MODERN = {
    "video_codecs": ["h264", "hevc", "av1", "vp9"],
    "audio_codecs": ["aac", "opus", "mp3"],
    "containers": ["mp4", "mov", "webm"],
}

#: What an old browser reports: H.264 in MP4 and nothing else. The transcode case.
ANCIENT = {"video_codecs": ["h264"], "audio_codecs": ["aac"], "containers": ["mp4"]}

#: Decodes HEVC, cannot read Matroska. The remux case, and the reason tier 2 exists.
NO_MATROSKA = {
    "video_codecs": ["h264", "hevc"],
    "audio_codecs": ["aac"],
    "containers": ["mp4", "mov"],
}


# --- a row, without a file behind it ------------------------------------------------------


def asset(
    *,
    container: str = "mp4",
    vcodec: str | None = "h264",
    acodec: str | None = "aac",
    width: int | None = 1920,
    height: int | None = 1080,
    fps: float | None = 30.0,
    duration_ms: int | None = 10_000,
    media_type: str = "video",
) -> Asset:
    return Asset(
        id="01HX0000000000000000000001",
        identity="digest",
        media_type=media_type,
        mime="video/mp4",
        width=width,
        height=height,
        duration_ms=duration_ms,
        fps=fps,
        size_bytes=1024,
        container=container,
        vcodec=vcodec,
        acodec=acodec,
        bit_depth=8,
        phash=None,
        videohash=None,
        original_filename="clip.mp4",
        added_at=0,
        probed_at=0,
    )
