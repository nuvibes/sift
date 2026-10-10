# SPDX-License-Identifier: AGPL-3.0-or-later
"""What receiving a file costs: the writes that record its chunks, and the reads for its digest."""

from __future__ import annotations

import asyncio
import os
from collections import deque
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from blake3 import blake3

import sift.slices.swap.schema  # noqa: F401 (the manifests table)
from sift.kernel.db import Database
from sift.slices.swap import pieces, transfer
from sift.slices.swap.frames import Chunk
from sift.slices.swap.ingest import LandingSession
from sift.slices.swap.live import Watchdog, _Live
from sift.slices.swap.models import Diff, Offer, OfferedFile
from sift.slices.swap.receiving import _Receiving
from sift.slices.swap.session import SwapSessions
from sift.slices.swap.tests.test_session import VIEWER, _database, _sessions
from sift.slices.swap.transfer import CHUNK_SIZE

_SESSION = "01HRECEIVINGCOSTS00000001"

pytestmark = pytest.mark.integration


class _Conn:
    """The sender's side, scripted: its frames in order, then a dropped connection."""

    def __init__(self, frames: list[Any]) -> None:
        self.frames = deque(frames)
        self.said: list[dict[str, Any]] = []

    async def read(self, within: float | None = None) -> Any:
        if not self.frames:
            raise asyncio.IncompleteReadError(b"", None)
        return self.frames.popleft()

    async def send(self, message: dict[str, Any]) -> None:
        self.said.append(message)


class _Side(_Receiving):
    """The receiving half alone, wanting the one file offered."""

    def __init__(self, sessions: SwapSessions, size: int) -> None:
        self._start_figures()
        self.live = SimpleNamespace(  # type: ignore[assignment]
            owner=sessions,
            id=_SESSION,
            short_id=_SESSION[-8:],
            peer="device",
            watchdog=Watchdog(),
            ended=asyncio.Event(),
            settle=_Live.settle,
            context=None,
        )
        self._start_receiving("folder")
        self.offer = Offer(
            files=[OfferedFile(key="a", size=size, identity="id-a", kind="video", title="clip a")]
        )
        self.wanted, self.wanted_set = [0], {0}


def _frames(count: int, size: int, digest: str, data: bytes) -> list[Any]:
    piece = transfer.chunk_digest(data)
    header = {"file": 0, "size": size, "digest": digest, "chunk_size": CHUNK_SIZE}
    return [header, *(Chunk(0, index, piece, data) for index in range(count))]


def _counting_records(database: Database) -> list[str]:
    """Every statement that writes a manifest's chunks, as it runs."""
    seen: list[str] = []
    real = database.execute

    async def execute(sql: str, *args: Any, **kwargs: Any) -> Any:
        if sql.startswith("UPDATE swap_manifests SET done"):
            seen.append(sql)
        return await real(sql, *args, **kwargs)

    database.execute = execute  # type: ignore[method-assign]
    return seen


def _empty_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every chunk written as nothing, so a gigabyte's worth costs only its opens and syncs."""
    real = transfer.write_chunk

    def write(path: Path, index: int, data: bytes, *rest: Any) -> None:
        real(path, index, b"")

    monkeypatch.setattr(transfer, "write_chunk", write)


def _counting_syncs(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    synced: list[int] = []
    real = os.fsync

    def fsync(fd: int) -> None:
        synced.append(fd)
        real(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    return synced


async def test_a_file_of_256_chunks_is_written_down_in_four_records_not_one_per_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    count, digest = 256, "d" * 64
    size = count * CHUNK_SIZE
    database = await _database(tmp_path / "r.sqlite3")
    try:
        sessions = _sessions(database, tmp_path, clock=lambda: 0.0)
        await sessions.store.create(_SESSION, role="guest", started_at=1, started_by=VIEWER)
        records = _counting_records(database)
        _empty_writes(monkeypatch)
        synced = _counting_syncs(monkeypatch)
        monkeypatch.setattr(transfer, "digest_file", lambda _path: digest)
        side = _Side(sessions, size)
        conn = _Conn(_frames(count, size, digest, bytes(CHUNK_SIZE)))

        with pytest.raises(asyncio.IncompleteReadError):
            await side.receive(conn)  # type: ignore[arg-type]

        assert conn.said[-1] == {"file_done": 0}
        assert (len(records), len(synced)) == (4, 4), (len(records), len(synced))
    finally:
        await database.close()


async def test_a_file_cut_part_way_resumes_from_its_last_record_and_no_further_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stopped with no chance to say anything (as a process is), a file has lost at most the
    chunks since its last record: the next session asks only for those again."""
    count, digest = 256, "d" * 64
    size = count * CHUNK_SIZE
    database = await _database(tmp_path / "r.sqlite3")
    try:
        sessions = _sessions(database, tmp_path, clock=lambda: 0.0)
        await sessions.store.create(_SESSION, role="guest", started_at=1, started_by=VIEWER)
        _empty_writes(monkeypatch)
        frames = _frames(100, size, digest, bytes(CHUNK_SIZE))

        with pytest.raises(asyncio.IncompleteReadError):
            await _Side(sessions, size).receive(_Conn(frames))  # type: ignore[arg-type]

        kept = await sessions.store.manifest(_SESSION, "a")
        assert kept is not None and kept.done == tuple(range(transfer.RECORD_EVERY))
        again = _Conn(frames[:1])
        with pytest.raises(asyncio.IncompleteReadError):
            await _Side(sessions, size).receive(again)  # type: ignore[arg-type]
        assert again.said == [{"have": list(range(transfer.RECORD_EVERY))}]
    finally:
        await database.close()


async def test_a_slow_file_is_written_down_every_few_seconds_and_at_its_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    count, digest = 10, "d" * 64
    size = count * CHUNK_SIZE
    clock = [0.0]
    database = await _database(tmp_path / "r.sqlite3")
    try:
        sessions = _sessions(database, tmp_path, clock=lambda: clock[0])
        await sessions.store.create(_SESSION, role="guest", started_at=1, started_by=VIEWER)
        real = transfer.write_chunk

        def slow(path: Path, index: int, data: bytes, *rest: Any) -> None:
            clock[0] += 2.0
            real(path, index, b"")

        monkeypatch.setattr(transfer, "write_chunk", slow)
        monkeypatch.setattr(transfer, "digest_file", lambda _path: digest)
        recorded: list[int] = []
        real_record = sessions.store.record_done

        async def record(session_id: str, key: str, done: Any, now: int) -> None:
            recorded.append(len(list(done)))
            await real_record(session_id, key, done, now)

        monkeypatch.setattr(sessions.store, "record_done", record)
        conn = _Conn(_frames(count, size, digest, bytes(CHUNK_SIZE)))

        with pytest.raises(asyncio.IncompleteReadError):
            await _Side(sessions, size).receive(conn)  # type: ignore[arg-type]

        assert recorded == [3, 6, 9, 10]
    finally:
        await database.close()


@pytest.mark.parametrize("once", [False, True])
async def test_a_received_file_is_read_once_for_its_digest_and_landed_without_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, once: bool
) -> None:
    """The stream that finds the file whole reads it for its digest; the landing is told the
    digest was read from these bytes and does not read them again."""
    data = os.urandom(CHUNK_SIZE + 5)
    database = await _database(tmp_path / "r.sqlite3")
    try:
        sessions = _sessions(database, tmp_path)
        await sessions.store.create(_SESSION, role="guest", started_at=1, started_by=VIEWER)
        read: list[int] = []

        def counted(real: Callable[..., Any]) -> Callable[..., Any]:
            def reading(path: Path, *rest: Any) -> Any:
                read.append(path.stat().st_size)
                return real(path, *rest)

            return reading

        monkeypatch.setattr(transfer, "digest_file", counted(transfer.digest_file))
        monkeypatch.setattr(pieces, "staged_digests", counted(pieces.staged_digests))
        landed: list[Any] = []

        async def land(_session: Any, received: Any, *, ctx: Any) -> None:
            landed.append(received)

        sessions.land = land
        side = _Side(sessions, len(data))
        side.diff = Diff(wanted=["a"], people={})
        side.landing_session = LandingSession(
            id=_SESSION, peer_device="device", dest_folder_id="folder", wanted=frozenset({"a"})
        )
        header: dict[str, Any] = {"file": 0, "size": len(data), "chunk_size": CHUNK_SIZE}
        if once:
            header["version"] = "v" * 64
        else:
            header["digest"] = blake3(data).hexdigest()
        chunks = [data[:CHUNK_SIZE], data[CHUNK_SIZE:]]
        frames: list[Any] = [header]
        frames += [Chunk(0, n, transfer.chunk_digest(one), one) for n, one in enumerate(chunks)]
        if once:
            ours = pieces.pieces_digest(transfer.chunk_digest(one) for one in chunks)
            frames.append({"file": 0, "pieces": ours})
        landing = asyncio.create_task(side.land_each())
        try:
            with pytest.raises(asyncio.IncompleteReadError):
                await side.receive(_Conn(frames))  # type: ignore[arg-type]
            await asyncio.wait_for(side.landing.join(), 5)
        finally:
            landing.cancel()

        assert read == [len(data)], "one read of the staged file for its digest"
        assert [one.checked for one in landed] == [True]
        assert landed[0].digest == blake3(data).hexdigest()
    finally:
        await database.close()
