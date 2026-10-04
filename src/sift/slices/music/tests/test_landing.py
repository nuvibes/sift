# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a file's music while its bytes are still on the local disk.

The two properties: the DESTINATION folder's switch decides whether it happens at all, and what it
writes is keyed by the identity of the bytes rather than by a file that may not exist yet.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from sift.kernel import chromaprint, landing
from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.slices.music import landing as music_landing
from sift.slices.music.store import MusicStore

pytestmark = [pytest.mark.integration]


async def _library(tmp_path: Path) -> Database:
    database = Database(tmp_path / "music.sqlite3")
    await database.connect()
    await database.initialize_schema()
    return database


@pytest.fixture(autouse=True)
def _no_gate() -> object:
    """Each case sets the gate it wants and leaves the module as it found it."""
    yield
    music_landing.install_gate(None)


def _read(values: tuple[int, ...]) -> Callable[..., Awaitable[chromaprint.Fingerprint]]:
    async def read(source: Path, *, settings: Settings) -> chromaprint.Fingerprint:
        return chromaprint.Fingerprint(
            algorithm=1,
            tool="ffmpeg version 7.1.5",
            duration_ms=chromaprint.covers_ms(len(values)),
            offset_ms=0,
            values=values,
        )

    return read


def _gate(answer: bool) -> Callable[[str, str], Awaitable[bool]]:
    async def allowed(job_type: str, root_id: str) -> bool:
        return answer

    return allowed


#: The folder every case lands its file in, unless the case is about which folder that is.
_ROOT = "root-1"


@pytest.mark.asyncio
async def test_the_fingerprint_is_kept_under_the_identity_of_the_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There may be no file row yet, and there may never be one. An identity is true of the bytes
    whatever becomes of them."""
    monkeypatch.setattr(chromaprint, "read_whole_track", _read((5, 6, 7)))
    music_landing.install_gate(_gate(True))
    database = await _library(tmp_path)
    try:
        await music_landing.MusicLanding(database).landed(
            tmp_path / "staged.mp4", "identity-a", Settings(), root_id=_ROOT
        )
        row = await database.fetch_one(
            "SELECT fingerprint, tool FROM audio_fingerprints_pending WHERE identity = ?",
            ("identity-a",),
        )
        assert row is not None
        assert chromaprint.parse(bytes(row["fingerprint"])) == (5, 6, 7)
        assert str(row["tool"]).startswith("ffmpeg version")
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_nothing_is_read_while_the_switch_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(*args: object, **kwargs: object) -> chromaprint.Fingerprint:
        raise AssertionError("the switch is off")

    monkeypatch.setattr(chromaprint, "read_whole_track", refuse)
    music_landing.install_gate(_gate(False))
    database = await _library(tmp_path)
    try:
        await music_landing.MusicLanding(database).landed(
            tmp_path / "staged.mp4", "identity-a", Settings(), root_id=_ROOT
        )
        assert await MusicStore(database).claim("asset-1", "identity-a") is False
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_nothing_is_read_before_the_boot_has_said_which_switch_decides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A build that forgot the wiring reads nothing, rather than reading every file that arrives."""

    async def refuse(*args: object, **kwargs: object) -> chromaprint.Fingerprint:
        raise AssertionError("no gate is installed")

    monkeypatch.setattr(chromaprint, "read_whole_track", refuse)
    music_landing.install_gate(None)
    database = await _library(tmp_path)
    try:
        await music_landing.MusicLanding(database).landed(
            tmp_path / "staged.mp4", "identity-a", Settings(), root_id=_ROOT
        )
    finally:
        await database.close()


def test_the_slice_declared_itself_into_the_kernels_registry() -> None:
    """At import, the way a schema step and a forgetting do: the kernel must not know this slice
    exists and cannot call it by name."""
    assert music_landing.LANDING in landing.registered_landings()


_ASSET = """
INSERT INTO assets (id, identity, media_type, original_filename, added_at, probed_at, acodec)
VALUES (?, ?, ?, 'staged', 0, ?, ?)
"""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("media_type", "probed_at", "acodec", "read"),
    [
        ("image", None, None, False),  # a still never has a sound track
        ("gif", None, None, False),
        ("video", 5, None, False),  # probed, and probing found no audio stream
        ("video", None, None, True),  # just arrived: nothing kept to ask, so it is read
        ("video", 5, "aac", True),  # probed, with sound
    ],
)
async def test_a_file_known_to_be_silent_is_not_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    media_type: str,
    probed_at: int | None,
    acodec: str | None,
    read: bool,
) -> None:
    """No ffmpeg launch for bytes Sift already knows carry no sound. Without this, every still that
    arrived would be handed to ffmpeg, which would fail and log `chromaprint.no_audio` for a
    picture."""
    launched: list[Path] = []

    async def reading(source: Path, *, settings: Settings) -> chromaprint.Fingerprint:
        launched.append(source)
        return await _read((1, 2))(source, settings=settings)

    monkeypatch.setattr(chromaprint, "read_whole_track", reading)
    music_landing.install_gate(_gate(True))
    database = await _library(tmp_path)
    try:
        await database.execute(_ASSET, ("asset-1", "identity-a", media_type, probed_at, acodec))
        await music_landing.MusicLanding(database).landed(
            tmp_path / "staged.bin", "identity-a", Settings(), root_id=_ROOT
        )
        assert bool(launched) is read
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_only_a_folder_that_said_on_has_its_arrivals_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The music switch is off out of the box and on per folder, and at arrival the folder asked is
    the one the file is going INTO. A file landing in a folder that said nothing is not read at
    all (no pending row), because the read is the cost the switch exists to avoid."""
    launched: list[Path] = []
    asked: list[tuple[str, str]] = []

    async def reading(source: Path, *, settings: Settings) -> chromaprint.Fingerprint:
        launched.append(source)
        return await _read((3, 4))(source, settings=settings)

    async def only_the_music_folder(job_type: str, root_id: str) -> bool:
        asked.append((job_type, root_id))
        return root_id == "music-folder"

    monkeypatch.setattr(chromaprint, "read_whole_track", reading)
    music_landing.install_gate(only_the_music_folder)
    database = await _library(tmp_path)
    try:
        await music_landing.MusicLanding(database).landed(
            tmp_path / "on.mp4", "identity-on", Settings(), root_id="music-folder"
        )
        await music_landing.MusicLanding(database).landed(
            tmp_path / "off.mp4", "identity-off", Settings(), root_id="other-folder"
        )
        assert launched == [tmp_path / "on.mp4"]
        assert asked == [
            ("audio_fingerprint", "music-folder"),
            ("audio_fingerprint", "other-folder"),
        ]
        rows = await database.fetch_all("SELECT identity FROM audio_fingerprints_pending")
        assert [str(row["identity"]) for row in rows] == ["identity-on"]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_file_with_no_known_destination_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No folder to ask is off, even where every folder that could be asked would say yes."""

    async def refuse(*args: object, **kwargs: object) -> chromaprint.Fingerprint:
        raise AssertionError("no destination is off")

    monkeypatch.setattr(chromaprint, "read_whole_track", refuse)
    music_landing.install_gate(_gate(True))
    database = await _library(tmp_path)
    try:
        await music_landing.MusicLanding(database).landed(
            tmp_path / "staged.mp4", "identity-a", Settings(), root_id=None
        )
        assert await database.fetch_all("SELECT identity FROM audio_fingerprints_pending") == []
    finally:
        await database.close()
