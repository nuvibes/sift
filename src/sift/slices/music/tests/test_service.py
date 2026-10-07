# SPDX-License-Identifier: AGPL-3.0-or-later
"""The four ways one file's fingerprint ends, and the two that must never open anything.

Against the real database and the real store; what is stood in for is the job's context, because
what is being held here is which reads happen rather than how a worker hands a job over.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import pytest

from sift.kernel import chromaprint
from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.kernel.jobs import JobContext
from sift.slices.music.service import MusicService
from sift.slices.music.store import Candidate, Kept, MusicStore

pytestmark = [pytest.mark.integration]

_EPOCH = 1_700_000_000

_ASSET = """
INSERT INTO assets
  (id, identity, media_type, size_bytes, original_filename, added_at, acodec, probed_at)
VALUES (?, ?, ?, 10, ?, ?, ?, 1)
"""
#: A copy of a file, there to read. A file owes a fingerprint only while it has one (see
#: `kernel.content.presence`), so every file here is given one.
_COPY = (
    "INSERT INTO asset_locations"
    " (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)"
    " VALUES (?, ?, 'root-1', NULL, ?, ?, 1, 1)"
)
_ROOT = (
    "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES ('root-1', 'm', 'C:\\m', 1)"
)


@dataclass
class _Asset:
    id: str
    identity: str
    acodec: str | None


class _Content:
    """The asset row, as the service reads it."""

    def __init__(self, assets: dict[str, _Asset]) -> None:
        self._assets = assets

    async def get(self, asset_id: str) -> _Asset | None:
        return self._assets.get(asset_id)


class _Context:
    """A handler's context with the two things this service touches."""

    def __init__(self, payload: dict[str, Any], content: _Content) -> None:
        self.payload = payload
        self.content = content


def _context(asset_id: str, assets: dict[str, _Asset]) -> JobContext:
    return cast(JobContext, _Context({"asset_id": asset_id}, _Content(assets)))


async def _library(tmp_path: Path) -> Database:
    database = Database(tmp_path / "music.sqlite3")
    await database.connect()
    await database.initialize_schema()
    async with database.write() as connection:
        await connection.execute(_ROOT)
        await connection.execute(
            _ASSET, ("asset-1", "identity-1", "video", "one.mp4", _EPOCH, "aac")
        )
        await connection.execute(
            _ASSET, ("asset-2", "identity-2", "image", "two.jpg", _EPOCH, None)
        )
        await connection.execute(_COPY, ("copy-1", "asset-1", "one.mp4", "one.mp4"))
        await connection.execute(_COPY, ("copy-2", "asset-2", "two.jpg", "two.jpg"))
    return database


def _service(store: MusicStore, *, on: bool = True) -> MusicService:
    async def allowed(job_type: str, asset_id: str | None = None) -> bool:
        return on

    return MusicService(store=store, allowed=allowed, job_type="audio_fingerprint")


def _kept(values: bytes) -> Kept:
    return Kept(
        algorithm=1,
        tool="ffmpeg version 7.1.5",
        duration_ms=8000 if values else 0,
        offset_ms=0,
        fingerprint=values,
        computed_at=_EPOCH,
    )


@pytest.mark.asyncio
async def test_a_waiting_fingerprint_is_claimed_and_nothing_is_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of reading at staging: the answer is already here, so the file on the share
    is never touched. A launch from this path would be the days of reading this exists to avoid."""
    database = await _library(tmp_path)
    store = MusicStore(database)
    service = _service(store)

    async def refuse(*args: object, **kwargs: object) -> chromaprint.Fingerprint:
        raise AssertionError("a claimed fingerprint must not open the file")

    monkeypatch.setattr(chromaprint, "read_whole_track", refuse)
    try:
        await store.keep_pending("identity-1", _kept(b"\x07\x00\x00\x00"))
        assets = {"asset-1": _Asset("asset-1", "identity-1", "aac")}
        await service.fingerprint(_context("asset-1", assets), settings=Settings())
        row = await database.fetch_one(
            "SELECT fingerprint FROM audio_fingerprints WHERE asset_id = ?", ("asset-1",)
        )
        assert row is not None
        assert bytes(row["fingerprint"]) == b"\x07\x00\x00\x00"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_file_with_no_audio_is_written_down_without_a_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`acodec` is NULL for a photograph, a GIF and a silent video, and ffmpeg refuses the audio
    map on all three. The empty row is what stops the file being offered again for ever."""
    database = await _library(tmp_path)
    store = MusicStore(database)
    service = _service(store)

    async def refuse(*args: object, **kwargs: object) -> chromaprint.Fingerprint:
        raise AssertionError("a file with no audio must not be opened")

    monkeypatch.setattr(chromaprint, "read_whole_track", refuse)
    try:
        assets = {"asset-2": _Asset("asset-2", "identity-2", None)}
        await service.fingerprint(_context("asset-2", assets), settings=Settings())
        row = await database.fetch_one(
            "SELECT fingerprint, duration_ms FROM audio_fingerprints WHERE asset_id = ?",
            ("asset-2",),
        )
        assert row is not None
        assert bytes(row["fingerprint"]) == b""
        assert int(row["duration_ms"]) == 0
        assert await service.lacking_among(["asset-2"]) == set()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_file_that_already_has_one_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = await _library(tmp_path)
    store = MusicStore(database)
    service = _service(store)

    async def refuse(*args: object, **kwargs: object) -> chromaprint.Fingerprint:
        raise AssertionError("a file that has been answered for must not be read again")

    monkeypatch.setattr(chromaprint, "read_whole_track", refuse)
    try:
        await store.keep("asset-1", _kept(b"\x01\x00\x00\x00"))
        assets = {"asset-1": _Asset("asset-1", "identity-1", "aac")}
        await service.fingerprint(_context("asset-1", assets), settings=Settings())
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_file_that_is_gone_is_not_written_down(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    store = MusicStore(database)
    service = _service(store)
    try:
        await service.fingerprint(_context("asset-9", {}), settings=Settings())
        assert not await store.has("asset-9")
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_job_with_no_file_is_a_fault_rather_than_a_quiet_return(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    service = _service(MusicStore(database))
    try:
        with pytest.raises(ValueError, match="needs the id"):
            await service.fingerprint(_context("", {}), settings=Settings())
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_the_switch_being_off_is_nothing_lacking_and_nothing_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off, a Build must not count these files and must not read them: the same answer in both
    places, which is what keeps the sheet's number and the pass describing one set."""
    database = await _library(tmp_path)
    store = MusicStore(database)
    service = _service(store, on=False)

    async def refuse(*args: object, **kwargs: object) -> chromaprint.Fingerprint:
        raise AssertionError("the switch is off")

    monkeypatch.setattr(chromaprint, "read_whole_track", refuse)
    try:
        assert await service.lack() is None
        assert await service.lacking_among(["asset-1"]) == set()
        await service.fingerprint(_context("asset-1", {}), settings=Settings())
        assert not await store.has("asset-1")
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_folder_that_answered_no_is_honoured_file_by_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A root of material with no music in it can say no for itself, and the answer is asked of the
    FILE. Asked of the library instead, a folder's answer would be accepted by the screen and
    silently ignored by the work, which is the shape the import policy exists to make impossible.
    """
    database = await _library(tmp_path)
    store = MusicStore(database)

    async def refuse(*args: object, **kwargs: object) -> chromaprint.Fingerprint:
        raise AssertionError("this file's folder said no")

    async def allowed(job_type: str, asset_id: str | None = None) -> bool:
        # On for the library, off for this one file, which is what a folder answering no is.
        return asset_id != "asset-1"

    monkeypatch.setattr(chromaprint, "read_whole_track", refuse)
    service = MusicService(store=store, allowed=allowed, job_type="audio_fingerprint")
    try:
        assets = {"asset-1": _Asset("asset-1", "identity-1", "aac")}
        await service.fingerprint(_context("asset-1", assets), settings=Settings())
        assert not await store.has("asset-1")
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_what_a_page_lacks_is_what_has_no_row_yet(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    store = MusicStore(database)
    service = _service(store)
    try:
        assert await service.lacking_among([]) == set()
        # A silent file owes nothing, as the Build's count says: a page that handed it a task
        # would read more files handed out than it counted.
        assert await service.lacking_among(["asset-1", "asset-2"]) == {"asset-1"}
        await store.keep("asset-1", _kept(b"\x01\x00\x00\x00"))
        # Kept and not yet paired is still owed its pairing (v3); paired, it is not lacking.
        assert await service.lacking_among(["asset-1", "asset-2"]) == {"asset-1"}
        await store.settle("asset-1")
        assert await service.lacking_among(["asset-1", "asset-2"]) == set()
        # And a file whose only copy is missing owes nothing any pass could do.
        await database.execute("DELETE FROM audio_fingerprints WHERE asset_id = 'asset-1'")
        assert await service.lacking_among(["asset-1"]) == {"asset-1"}
        await database.execute("UPDATE asset_locations SET status = 'missing' WHERE id = 'copy-1'")
        assert await service.lacking_among(["asset-1"]) == set()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_fingerprint_read_by_an_older_algorithm_is_owed_a_new_read(tmp_path: Path) -> None:
    """The count, the page and the task agree about a row an older algorithm wrote.

    The count's term says such a file still lacks its fingerprint; the page asked of a Run now has
    to bind both of the term's values to say the same (bound one short, every Run now of the
    music task would fail); and the task must not take the old row as an answer, or the file would
    be handed out on every run and returned immediately without being read again.
    """
    database = await _library(tmp_path)
    store = MusicStore(database)
    service = _service(store)
    try:
        await store.keep("asset-1", _kept(b"\x01\x00\x00\x00"))
        await store.settle("asset-1")
        assert await store.has("asset-1")
        assert await service.lacking_among(["asset-1", "asset-2"]) == set()
        older = chromaprint.ALGORITHM - 1
        await database.execute(
            "UPDATE audio_fingerprints SET algorithm = ? WHERE asset_id = 'asset-1'", (older,)
        )
        assert await service.lacking_among(["asset-1", "asset-2"]) == {"asset-1"}
        assert not await store.has("asset-1")
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_run_sweeps_the_fingerprints_nothing_ever_claimed(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    store = MusicStore(database)
    service = _service(store)
    try:
        # One stamped long ago, and one stamped now. A run drops the first and keeps the second.
        await store.keep_pending("identity-old", _kept(b"\x01\x00\x00\x00"))
        fresh = _kept(b"\x02\x00\x00\x00")
        await store.keep_pending("identity-fresh", replace(fresh, computed_at=int(time.time())))
        await service.before_run()
        assert await store.claim("asset-2", "identity-old") is False
        assert await store.claim("asset-1", "identity-fresh") is True
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_file_with_audio_is_read_whole_and_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordinary path: nothing waiting at staging, the file has sound, so its readable copy is
    read from end to end and what came back is the row."""
    from sift.slices.music import service as service_module

    database = await _library(tmp_path)
    store = MusicStore(database)
    service = _service(store)
    opened: list[Path] = []

    async def decodable(content: object, asset_id: str, *, settings: Settings) -> object:
        return type("Source", (), {"path": tmp_path / f"{asset_id}.mp4"})()

    async def read(path: Path, *, settings: Settings) -> chromaprint.Fingerprint:
        opened.append(path)
        return chromaprint.Fingerprint(
            algorithm=1, tool="ffmpeg version 7.1.5", duration_ms=8000, offset_ms=0, values=(7,)
        )

    monkeypatch.setattr(service_module, "resolve_decodable", decodable)
    monkeypatch.setattr(chromaprint, "read_whole_track", read)
    try:
        assets = {"asset-1": _Asset("asset-1", "identity-1", "aac")}
        await service.fingerprint(_context("asset-1", assets), settings=Settings())
        assert opened == [tmp_path / "asset-1.mp4"]
        row = await database.fetch_one(
            "SELECT fingerprint, duration_ms FROM audio_fingerprints WHERE asset_id = ?",
            ("asset-1",),
        )
        assert row is not None
        assert bytes(row["fingerprint"]) == b"\x07\x00\x00\x00"
        assert int(row["duration_ms"]) == 8000
    finally:
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("why", ["missing", "unreadable"])
async def test_a_file_that_cannot_be_reached_right_now_is_not_written_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, why: str
) -> None:
    """An unreachable file is a moment, not an answer: an empty row would stop it ever being asked
    again, so nothing is written and the next pass tries again."""
    from sift.kernel.media import MissingAsset, NoReadableCopy
    from sift.slices.music import service as service_module

    database = await _library(tmp_path)
    store = MusicStore(database)
    service = _service(store)

    async def unreachable(content: object, asset_id: str, *, settings: Settings) -> object:
        raise MissingAsset(asset_id) if why == "missing" else NoReadableCopy(asset_id)

    monkeypatch.setattr(service_module, "resolve_decodable", unreachable)
    try:
        assets = {"asset-1": _Asset("asset-1", "identity-1", "aac")}
        await service.fingerprint(_context("asset-1", assets), settings=Settings())
        assert not await store.has("asset-1")
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_switched_on_the_lack_is_the_stores_own_term(tmp_path: Path) -> None:
    """On, the Build's count takes this feature's term from the store, as the pass reads it."""
    database = await _library(tmp_path)
    store = MusicStore(database)
    try:
        assert await _service(store).lack() == store.lack()
    finally:
        await database.close()


def _never_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both seams a read goes through, each refusing: a claim-only job may reach neither."""
    from sift.slices.music import service as service_module

    async def refuse(*args: object, **kwargs: object) -> Any:
        raise AssertionError("a claim-only job opened the file")

    monkeypatch.setattr(service_module, "resolve_decodable", refuse)
    monkeypatch.setattr(chromaprint, "read_whole_track", refuse)


def _claim_only(asset_id: str, assets: dict[str, _Asset]) -> JobContext:
    from sift.slices.music.service import CLAIM_ONLY

    return cast(JobContext, _Context({"asset_id": asset_id, CLAIM_ONLY: True}, _Content(assets)))


@pytest.mark.asyncio
async def test_a_scans_follow_on_claims_what_staging_took(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The follow-on after a probe files a fingerprint taken at staging against its file."""
    database = await _library(tmp_path)
    store = MusicStore(database)
    service = _service(store)
    _never_opened(monkeypatch)
    try:
        await store.keep_pending("identity-1", _kept(b"\x09\x00\x00\x00"))
        assets = {"asset-1": _Asset("asset-1", "identity-1", "aac")}
        await service.fingerprint(_claim_only("asset-1", assets), settings=Settings())
        assert await store.has("asset-1")
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_scans_follow_on_with_nothing_to_claim_never_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scan finding a file is nobody asking for its music. With no fingerprint waiting the
    file is left as it was (not read, not written down) for a press or the card."""
    database = await _library(tmp_path)
    store = MusicStore(database)
    service = _service(store)
    _never_opened(monkeypatch)
    try:
        assets = {"asset-1": _Asset("asset-1", "identity-1", "aac")}
        await service.fingerprint(_claim_only("asset-1", assets), settings=Settings())
        assert not await store.has("asset-1")
    finally:
        await database.close()


class _Waiting:
    """The two waiting reads, answering fixed numbers and writing down that they were asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def waiting_for(self, user_id: str) -> int:
        self.asked.append(user_id)
        return 5

    async def any_waiting(self) -> bool:
        self.asked.append("any")
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("on", [True, False])
async def test_nothing_is_waiting_while_the_switch_is_off(on: bool) -> None:
    """The card's number and whether it is drawn at all: the store's answer while the switch is on,
    nought and no while it is off (nothing is going to be read, so nothing waits) and then the
    store is not asked."""
    store = _Waiting()
    service = _service(cast(MusicStore, store), on=on)

    assert await service.waiting_for("user-1") == (5 if on else 0)
    assert await service.any_waiting() is on
    assert store.asked == (["user-1", "any"] if on else [])


class _Pairing:
    """The pairing's reads and writes, over fingerprints held in a dict."""

    def __init__(
        self, held: dict[str, chromaprint.Fingerprint], candidates: list[Candidate]
    ) -> None:
        self.held = held
        self._candidates = candidates
        self.read: list[str] = []
        self.written: list[tuple[str, list[object]]] = []
        self.settled: list[str] = []

    async def fingerprint_of(self, asset_id: str) -> chromaprint.Fingerprint | None:
        self.read.append(asset_id)
        return self.held.get(asset_id)

    async def index_keys(self, asset_id: str, keys: object) -> None:
        return None

    async def candidates(self, asset_id: str, keys: object) -> list[Candidate]:
        return self._candidates

    async def write_pairs(self, asset_id: str, pairs: list[object]) -> None:
        self.written.append((asset_id, pairs))

    async def settle(self, asset_id: str) -> None:
        self.settled.append(asset_id)


def _print(values: int) -> chromaprint.Fingerprint:
    return chromaprint.Fingerprint(
        algorithm=1,
        tool="ffmpeg",
        duration_ms=values * 124,
        offset_ms=0,
        values=tuple(range(1, values + 1)),
    )


@pytest.mark.asyncio
async def test_a_file_gone_before_its_pairing_is_neither_paired_nor_settled() -> None:
    store = _Pairing({}, [])

    await _service(cast(MusicStore, store))._pair("gone")

    assert store.written == []
    assert store.settled == []


@pytest.mark.asyncio
async def test_only_candidates_over_the_floor_with_a_fingerprint_to_compare_are_verified() -> None:
    """A file sharing too few keys for its length is never read; one whose row went, or whose
    fingerprint is empty, has nothing to compare. None of them is a pair, and the file is settled
    with none."""
    silent = chromaprint.Fingerprint(
        algorithm=1, tool="ffmpeg", duration_ms=0, offset_ms=0, values=()
    )
    store = _Pairing(
        {"one": _print(400), "silent": silent},
        [
            Candidate(asset_id="few", shared=1, duration_ms=49_600),
            Candidate(asset_id="gone", shared=400, duration_ms=49_600),
            Candidate(asset_id="silent", shared=400, duration_ms=49_600),
        ],
    )

    await _service(cast(MusicStore, store))._pair("one")

    assert "few" not in store.read
    assert store.written == [("one", [])]
    assert store.settled == ["one"]
