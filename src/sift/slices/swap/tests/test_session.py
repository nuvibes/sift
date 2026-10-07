# SPDX-License-Identifier: AGPL-3.0-or-later
"""A swap session, on loopback: the frames, the hello and the code, one use, the watchdog, the
stream ladder, and a whole session with a dropped stream and a bad chunk.

Nothing leaves this machine. The host's "tunnel" is a fake that hands back a public-looking
address nobody dials; the guest's tunnel is a CONNECT proxy on loopback that sends every
connection to the host's listener whatever address the token names.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from blake3 import blake3
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import sift.slices.swap.schema
import sift.slices.workbench.schema  # noqa: F401 (the ledger a swap's start and end go on)
from sift.kernel.db import Database
from sift.kernel.secret_store import SecretStore
from sift.slices.swap import frames, lock, transfer
from sift.slices.swap import session as swap
from sift.slices.swap import token as token_module
from sift.slices.swap.device import Device, device_id_of, public_bytes
from sift.slices.swap.frames import encode_chunk, encode_json
from sift.slices.swap.models import Chosen, Diff, Offer, OfferedFile, OfferScreen
from sift.slices.swap.session import (
    Chunk,
    Conn,
    Ladder,
    ProtocolError,
    SwapSessions,
    Taken,
    Watchdog,
    code_of,
    hello,
    read_hello,
)
from sift.slices.swap.store import SessionRow, SessionStore
from sift.slices.swap.token import parse
from sift.slices.swap.transfer import CHUNK_SIZE, Prepared

_MASTER = b"\x07" * 32
_ADDRESS, _PORT = "8.8.4.4", 40000


def _device() -> Device:
    key = Ed25519PrivateKey.generate()
    return Device(device_id_of(public_bytes(key.public_key())), key)


# --- frames, the hello, the code ---------------------------------------------------------------


class _Pipe:
    """A reader fed with whatever frames a test hands it."""

    def __init__(self, *frames: bytes) -> None:
        self.reader = asyncio.StreamReader()
        for frame in frames:
            self.reader.feed_data(frame)
        self.reader.feed_eof()


@pytest.mark.unit
async def test_frames_come_back_as_they_went() -> None:
    big = {"offer": {"files": [{"key": f"k{n}", "note": "x" * 50} for n in range(40_000)]}}
    frames = encode_json(big)
    assert len(frames) > 1, "a message past one frame's cap travels in parts"
    data = b"\x00\x01" * 1000
    pipe = _Pipe(*encode_json({"ping": 1}), *frames, encode_chunk(3, 7, data))
    conn = Conn(pipe.reader, None)  # type: ignore[arg-type]
    assert await conn.read() == {"ping": 1}
    assert await conn.read() == big
    chunk = await conn.read()
    assert isinstance(chunk, Chunk)
    assert (chunk.file, chunk.index, chunk.data) == (3, 7, data)
    assert chunk.digest == transfer.chunk_digest(data)


@pytest.mark.unit
async def test_a_frame_of_no_kind_or_past_the_cap_is_refused() -> None:
    for body in (b"[1]", b"\x05abc", b"{not json"):
        conn = Conn(_Pipe(len(body).to_bytes(4, "big") + body).reader, None)  # type: ignore[arg-type]
        with pytest.raises(ProtocolError):
            await conn.read()
    huge = (frames.JSON_CAP + 1).to_bytes(4, "big") + b"{" + b" " * frames.JSON_CAP
    with pytest.raises(ProtocolError):
        await Conn(_Pipe(huge).reader, None).read()  # type: ignore[arg-type]


@pytest.mark.unit
def test_a_hello_proves_its_device_for_this_session_only() -> None:
    me = _device()
    secret, nonce = os.urandom(32), os.urandom(32)
    frame = hello(me, "guest", secret, nonce)
    peer = read_hello(frame, "guest", secret)
    assert (peer.device, peer.nonce) == (me.id, nonce)
    assert peer.model is None
    # The face model a guest names is read when it is a name, and nothing else is.
    assert read_hello({**frame, "model": "buffalo_l"}, "guest", secret).model == "buffalo_l"
    for odd in ("", 7, "x" * 500):
        assert read_hello({**frame, "model": odd}, "guest", secret).model is None
    # That it takes a person's confirmed count, read only from the one word that says so.
    assert read_hello({**frame, "counts": 1}, "guest", secret).counts is True
    assert peer.counts is False
    # Another session's secret, the other side's role, another device's id: all refused.
    cases: list[tuple[dict[str, Any], str, bytes]] = [
        (frame, "guest", os.urandom(32)),
        (frame, "host", secret),
        ({**frame, "device": _device().id}, "guest", secret),
        ({**frame, "v": 2}, "guest", secret),
    ]
    for sent, role, key in cases:
        with pytest.raises(ProtocolError):
            read_hello(sent, role, key)


@pytest.mark.unit
def test_both_sides_compute_one_code_and_every_input_moves_it() -> None:
    secret = os.urandom(32)
    host, guest = _device().id, _device().id
    host_nonce, guest_nonce = os.urandom(32), os.urandom(32)
    code = code_of(secret, host, guest, host_nonce, guest_nonce)
    assert len(code) == 6 and code.isalnum() and code.upper() == code
    assert code == code_of(secret, host, guest, host_nonce, guest_nonce)
    assert code != code_of(os.urandom(32), host, guest, host_nonce, guest_nonce)
    assert code != code_of(secret, guest, host, host_nonce, guest_nonce)
    assert code != code_of(secret, host, guest, os.urandom(32), guest_nonce)
    assert code != code_of(secret, host, guest, host_nonce, os.urandom(32))


@pytest.mark.unit
def test_the_watchdog_bites_at_ten_seconds() -> None:
    now = [100.0]
    dog = Watchdog(clock=lambda: now[0])
    assert dog.seconds == 10.0
    now[0] += 9.9
    assert not dog.silent()
    dog.heard()
    now[0] += 9.9
    assert not dog.silent()
    now[0] += 0.1
    assert dog.silent()


@pytest.mark.unit
def test_the_streams_start_at_eight_climb_while_the_rate_holds_and_stop_at_thirty_two() -> None:
    ladder = Ladder()
    assert ladder.streams == 8
    assert ladder.window(10.0) == 8  # the first window has nothing to compare with
    rising = [ladder.window(rate) for rate in range(20, 20 + 10 * 26, 10)]
    assert rising[:3] == [9, 10, 11] and rising[23:] == [32, 32, 32]
    assert not ladder.climbing


@pytest.mark.unit
def test_a_rise_at_thirty_two_streams_adds_none_and_the_climb_stops_again() -> None:
    """A line that gives more after the climb ended starts it again, but never past the cap."""
    ladder = Ladder(streams=32, climbing=False, best=100.0)
    assert ladder.window(200.0) == 32
    assert not ladder.climbing
    assert ladder.best == 200.0


# --- fakes for a session on loopback -------------------------------------------------------------


@dataclass(frozen=True)
class _Hosted:
    public_ipv4: str
    external_port: int


class _Hoster:
    def __init__(self) -> None:
        self.target: int | None = None
        self.stopped: list[str] = []
        self.on_moved: Any = None
        self.on_lost: Any = None

    async def host_on(
        self,
        tunnel_id: str,
        *,
        target_port: int,
        master_key: bytes,
        on_moved: Any = None,
        on_lost: Any = None,
    ) -> _Hosted:
        self.target = target_port
        self.on_moved = on_moved
        self.on_lost = on_lost
        return _Hosted(_ADDRESS, _PORT)

    async def stop_hosting(self, tunnel_id: str) -> None:
        self.stopped.append(tunnel_id)


class _Proxy:
    """The guest's tunnel, on loopback: every CONNECT goes to the host's listener."""

    def __init__(self, hoster: _Hoster) -> None:
        self.hoster = hoster
        self.targets: list[bytes] = []
        self.server: asyncio.Server | None = None
        self.url = ""

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.url = f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except asyncio.IncompleteReadError:
            # A dial dropped before it named its target (a cut, or a session ending while one of
            # its streams was still being opened): a tunnel closes it and carries nothing.
            writer.close()
            return
        self.targets.append(head.split(b"\r\n", 1)[0])
        assert self.hoster.target is not None
        up_reader, up_writer = await asyncio.open_connection("127.0.0.1", self.hoster.target)
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()

        async def pump(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
            with suppress(Exception):
                while data := await source.read(1 << 16):
                    sink.write(data)
                    await sink.drain()
            with suppress(Exception):
                sink.close()

        await asyncio.gather(pump(reader, up_writer), pump(up_reader, writer))


class _Egress:
    def __init__(self, url: str | None) -> None:
        self.url = url

    @asynccontextmanager
    async def through(self, route: str | None) -> AsyncIterator[str | None]:
        yield self.url


class _Jobs:
    def __init__(self) -> None:
        self.queued: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def enqueue(self, job_type: str, payload: Any = None, **options: Any) -> str:
        self.queued.append((job_type, dict(payload or {}), options))
        return f"job-{len(self.queued)}"


class _Context:
    """The part of a job's context a session reads."""

    def __init__(self, session_id: str, workspace: Path) -> None:
        self.payload = {"session_id": session_id}
        self._workspace = workspace
        self.notes: list[str] = []
        self.progress: list[float] = []

    def require_str(self, key: str, message: str) -> str:
        value = self.payload.get(key)
        if not value:
            raise ValueError(message)
        return value

    async def master_key(self) -> bytes:
        return _MASTER

    @property
    def workspace(self) -> Path:
        self._workspace.mkdir(parents=True, exist_ok=True)
        return self._workspace

    async def report_progress(self, fraction: float) -> None:
        self.progress.append(fraction)

    async def set_note(self, note: str) -> None:
        self.notes.append(note)


async def _copy_prepare(source: Path, workdir: Path) -> Prepared:
    """The strip stands aside here (it is proven in test_transfer.py), so the bytes are known."""

    def run() -> Prepared:
        workdir.mkdir(parents=True, exist_ok=True)
        target = workdir / (source.name + ".strip")
        shutil.copyfile(source, target)
        return Prepared(target, target.stat().st_size, transfer.digest_file(target))

    return await asyncio.to_thread(run)


async def _read_route() -> str:
    return "tunnel-guest"


#: The two people the tests press as. Real rows, because the session's start and end go on the
#: ledger under the user who pressed, and that column is a foreign key the database enforces.
VIEWER = "viewer"
ADMIN = "admin"


async def _database(path: Path) -> Database:
    database = Database(path)
    await database.connect()
    await database.initialize_schema()
    for user_id in (VIEWER, ADMIN):
        await database.execute(
            "INSERT INTO users (id, username, password_hash, role, created_at)"
            " VALUES (?, ?, 'not-a-hash', 'admin', 1)",
            (user_id, user_id),
        )
    return database


def _sessions(database: Database, root: Path, **kwargs: Any) -> SwapSessions:
    defaults: dict[str, Any] = {
        "jobs": _Jobs(),
        "hoster": _Hoster(),
        "egress": _Egress(None),
        "read_route": _read_route,
        "staging": root / "staging",
        "prepare": _copy_prepare,
        "watchdog_seconds": 2.0,
        "ping_seconds": 0.2,
        "rate_window": 0.5,
        "hello_wait": 5.0,
    }
    defaults.update(kwargs)
    return SwapSessions(SessionStore(database), SecretStore(database), **defaults)


async def _until(check: Callable[[], object], seconds: float = 10.0) -> None:
    loop = asyncio.get_running_loop()
    began = loop.time()
    while not check():
        if loop.time() - began > seconds:
            raise AssertionError("waited too long")
        await asyncio.sleep(0.02)


async def _start_host(host: SwapSessions) -> swap.Started:
    return await host.start(
        viewer=SimpleNamespace(id="viewer"),  # type: ignore[arg-type]
        chosen=[Chosen(kind="person", id="p1")],
        tunnel_id="tunnel-host",
        share_boxes=True,
        master_key=_MASTER,
    )


async def _read_until(conn: Conn, key: str) -> dict[str, Any]:
    while True:
        frame = await conn.read(5)
        assert isinstance(frame, dict)
        if key in frame:
            return frame


# --- the host against a guest written by hand --------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not lock.PSK_AVAILABLE, reason="TLS with a key needs CPython 3.13")
async def test_one_hello_one_code_and_they_dont_match_ends_it(tmp_path: Path) -> None:
    database = await _database(tmp_path / "host.sqlite3")
    hoster = _Hoster()
    host = _sessions(database, tmp_path, hoster=hoster)
    try:
        started = await _start_host(host)
        assert started.token.address == _ADDRESS and started.token.port == _PORT
        # The provider moves the port before anybody joins: the token is made again for it, with
        # the same secret and the same expiry, and the screen answers the new one.
        await hoster.on_moved(_Hosted(_ADDRESS, _PORT + 1))
        remade = host.facts(started.session_id)
        assert remade is not None and remade.token is not None
        assert remade.token != started.token.text
        again_token = parse(remade.token, host.now())
        assert again_token.port == _PORT + 1 and again_token.secret == started.token.secret
        assert again_token.expires == started.token.expires
        task = asyncio.create_task(host.run(_Context(started.session_id, tmp_path / "ws")))  # type: ignore[arg-type]
        assert hoster.target is not None
        secret = started.token.secret

        me, nonce = _device(), os.urandom(32)
        conn = Conn(*await lock.dial("127.0.0.1", hoster.target, None, secret))
        await conn.send(hello(me, "guest", secret, nonce))
        answer = await conn.read(5)
        assert isinstance(answer, dict)
        peer = read_hello(answer, "host", secret)
        assert peer.device == started.token.host_device
        mine = code_of(secret, peer.device, me.id, peer.nonce, nonce)
        await _until(
            lambda: (
                (
                    host.facts(started.session_id)
                    or swap.LiveFacts(None, None, 0, 0, None, None, 0)
                ).code
            )
        )
        facts = host.facts(started.session_id)
        assert facts is not None
        assert facts.code == mine
        assert facts.token is None, "the token is not answered once somebody has joined"
        row = await host.row(started.session_id)
        assert row is not None and (row.state, row.peer_device) == ("connected", me.id)

        # ONE USE: the same token again, from anybody, is refused.
        again = Conn(*await lock.dial("127.0.0.1", hoster.target, None, secret))
        await again.send(hello(_device(), "guest", secret, os.urandom(32)))
        assert await again.read(5) == {"refused": "used"}
        again.close()

        await host.answer_code(started.session_id, False)
        assert (await _read_until(conn, "end"))["end"] == "refused"
        await asyncio.wait_for(task, 5)
        row = await host.row(started.session_id)
        assert row is not None and (row.state, row.end_reason) == ("ended", "refused")
        assert hoster.stopped == ["tunnel-host"]
        assert host.live(started.session_id) is None
        conn.close()
    finally:
        await database.close()


@pytest.mark.integration
@pytest.mark.skipif(not lock.PSK_AVAILABLE, reason="TLS with a key needs CPython 3.13")
async def test_a_silent_guest_cuts_the_session_off_until_the_token_runs_out(
    tmp_path: Path,
) -> None:
    """Silence is a tunnel dropping, not anybody's End: the session is cut off, keeps its step,
    its listener and its hosting, and ends as lost only when its token runs out."""
    database = await _database(tmp_path / "host.sqlite3")
    hoster = _Hoster()
    clock = [time.time()]
    host = _sessions(
        database,
        tmp_path,
        hoster=hoster,
        watchdog_seconds=0.6,
        ping_seconds=0.1,
        retry_seconds=0.1,
        now=lambda: clock[0],
    )
    task: asyncio.Task[object] | None = None
    try:
        started = await _start_host(host)
        context = _Context(started.session_id, tmp_path / "ws")
        task = asyncio.create_task(host.run(context))  # type: ignore[arg-type]
        assert hoster.target is not None
        secret = started.token.secret
        conn = Conn(*await lock.dial("127.0.0.1", hoster.target, None, secret))
        await conn.send(hello(_device(), "guest", secret, os.urandom(32)))
        await conn.read(5)
        loop = asyncio.get_running_loop()
        began = loop.time()
        # And now nothing: no ping, no answer, the connection left open.
        await _until(lambda: (live := host.live(started.session_id)) and live.cut_at, 5)
        took = loop.time() - began
        assert 0.5 <= took < 3.0, took
        # The cut is decided in memory first and written after, and the note is said once the row
        # holds it: the row is read after the note, never between the two.
        await _until(lambda: swap.CUT_OFF_NOTE in context.notes, 5)
        row = await host.row(started.session_id)
        assert row is not None and row.live and row.state == "connected"
        assert row.cut_off_at is not None
        assert not task.done(), "a session cut off waits to be joined again"
        assert hoster.stopped == [], "the listener and the hosting stay for them to come back"
        assert context.notes[-1] == swap.CUT_OFF_NOTE

        clock[0] = started.token.expires + 1
        await asyncio.wait_for(task, 5)
        row = await host.row(started.session_id)
        assert row is not None and (row.state, row.end_reason) == ("failed", "lost")
        assert hoster.stopped == ["tunnel-host"]
        conn.close()
    finally:
        # A session still running when an assertion above fails ends by writing its row, so it is
        # ended here while the database is still open.
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await database.close()


@pytest.mark.integration
async def test_a_restart_ends_what_it_interrupted(tmp_path: Path) -> None:
    database = await _database(tmp_path / "host.sqlite3")
    host = _sessions(database, tmp_path)
    try:
        await host.store.create(
            "01HRESTARTED0000000000001", role="guest", started_at=1, started_by=VIEWER
        )
        await host.store.create(
            "01HRESTARTED0000000000002", role="host", started_at=1, started_by=VIEWER
        )
        await host.store.end("01HRESTARTED0000000000002", state="done", reason="done", now=2)
        assert await host.settle_after_restart() == ["01HRESTARTED0000000000001"]
        row = await host.row("01HRESTARTED0000000000001")
        assert row is not None and (row.state, row.end_reason) == ("failed", "lost")
        done = await host.row("01HRESTARTED0000000000002")
        assert done is not None and done.state == "done"
    finally:
        await database.close()


# --- a whole session -------------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not lock.PSK_AVAILABLE, reason="TLS with a key needs CPython 3.13")
async def test_a_whole_swap_with_a_dropped_stream_and_a_bad_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = tmp_path / "library"
    sources.mkdir()
    first = os.urandom(CHUNK_SIZE * 2 + CHUNK_SIZE // 2)
    second = os.urandom(CHUNK_SIZE + 1000)
    (sources / "a.bin").write_bytes(first)
    (sources / "b.bin").write_bytes(second)
    offer = Offer(
        files=[
            OfferedFile(
                key="key-a", size=len(first), identity="id-a", kind="video", title="clip 1 of 2"
            ),
            OfferedFile(
                key="key-b", size=len(second), identity="id-b", kind="video", title="clip 2 of 2"
            ),
        ]
    )

    models_heard: list[str | None] = []

    async def make_offer(
        viewer: object, chosen: object, share_boxes: bool, peer_model: str | None = None
    ) -> Offer:
        models_heard.append(peer_model)
        return offer

    async def path_of(viewer: object, key: str) -> Path:
        return sources / {"key-a": "a.bin", "key-b": "b.bin"}[key]

    async def assess(arrived: Offer) -> tuple[OfferScreen, Callable[[Taken], Diff]]:
        assert arrived == offer
        return OfferScreen(layout="rows"), lambda taken: Diff(
            wanted=[one.key for one in arrived.files], people={}
        )

    landed: dict[str, bytes] = {}
    fingerprints_landed: list[Any] = []

    async def land(session: Any, received: Any, *, ctx: Any) -> None:
        assert received.key in session.wanted
        landed[received.key] = await asyncio.to_thread(received.staged.read_bytes)

    async def land_fingerprints(session: Any, arrived: Offer, **asked: Any) -> None:
        fingerprints_landed.append((arrived, asked))

    async def face_model() -> str:
        return "model-b"

    # THE FAULTS. The first chunk 1 of the first file is never written (the stream carrying it
    # drops), and the first chunk 0 of the second arrives with a byte changed under its digest.
    sent: Counter[tuple[int, int]] = Counter()
    original = frames.encode_chunk

    def faulty(file_index: int, chunk_index: int, data: bytes) -> bytes:
        sent[(file_index, chunk_index)] += 1
        if (file_index, chunk_index) == (0, 1) and sent[(0, 1)] == 1:
            raise ConnectionResetError("dropped")
        frame = original(file_index, chunk_index, data)
        if (file_index, chunk_index) == (1, 0) and sent[(1, 0)] == 1:
            frame = frame[:-1] + bytes([frame[-1] ^ 0xFF])
        return frame

    monkeypatch.setattr(frames, "encode_chunk", faulty)

    host_db = await _database(tmp_path / "host.sqlite3")
    guest_db = await _database(tmp_path / "guest.sqlite3")
    hoster = _Hoster()
    proxy = _Proxy(hoster)
    await proxy.start()
    host = _sessions(host_db, tmp_path / "h", hoster=hoster, make_offer=make_offer, path_of=path_of)
    guest = _sessions(
        guest_db,
        tmp_path / "g",
        egress=_Egress(proxy.url),
        assess=assess,
        land=land,
        land_fingerprints=land_fingerprints,
        face_model=face_model,
    )
    try:
        started = await _start_host(host)
        joined = await guest.join(
            viewer_id="viewer",
            token_text=started.token.text,
            dest_folder_id="folder",
            master_key=_MASTER,
        )
        host_task = asyncio.create_task(host.run(_Context(started.session_id, tmp_path / "hw")))  # type: ignore[arg-type]
        guest_context = _Context(joined, tmp_path / "gw")
        guest_task = asyncio.create_task(guest.run(guest_context))  # type: ignore[arg-type]

        def codes() -> tuple[str | None, str | None]:
            one, two = host.facts(started.session_id), guest.facts(joined)
            return (one.code if one else None, two.code if two else None)

        await _until(lambda: all(codes()))
        assert codes()[0] == codes()[1]
        await host.answer_code(started.session_id, True)
        await _until(
            lambda: (guest.facts(joined) or swap.LiveFacts(None, None, 0, 0, None, None, 0)).screen
        )
        await guest.take(joined, Taken())
        await asyncio.wait_for(asyncio.gather(host_task, guest_task), 30)

        assert landed == {"key-a": first, "key-b": second}
        # The guest's hello named its face model and the host's offer was made knowing it; the
        # people offered for their facial fingerprints alone were landed once, before the files.
        assert models_heard == ["model-b"]
        assert [(one[0], set(one[1]["skipped"])) for one in fingerprints_landed] == [(offer, set())]
        # Resumed from the manifest: chunk 0 of the first file went once, the dropped chunk twice.
        assert sent[(0, 0)] == 1 and sent[(0, 1)] == 2 and sent[(0, 2)] == 1
        # The bad chunk was asked for again, once.
        assert sent[(1, 0)] == 2 and sent[(1, 1)] == 1
        host_row, guest_row = await host.row(started.session_id), await guest.row(joined)
        assert host_row is not None and guest_row is not None
        assert (host_row.state, host_row.end_reason) == ("done", "done")
        assert (guest_row.state, guest_row.end_reason) == ("done", "done")
        assert (host_row.offered_files, host_row.wanted_files, host_row.sent_files) == (2, 2, 2)
        assert host_row.sent_bytes == guest_row.sent_bytes == len(first) + len(second)
        assert guest_row.peer_device == started.token.host_device
        # Waiting for them until the other side is there, then who it is with.
        assert guest_context.notes == [
            swap.WAITING_NOTE,
            f"Swap with device {started.token.host_device}",
        ]
        assert hoster.stopped == ["tunnel-host"]
        # Every CONNECT named the token's address; no manifest is left behind a finished file.
        assert set(proxy.targets) == {f"CONNECT {_ADDRESS}:{_PORT} HTTP/1.1".encode()}
        assert await guest.store.manifest(joined, "key-a") is None
    finally:
        if proxy.server is not None:
            proxy.server.close()
        await host_db.close()
        await guest_db.close()


@pytest.mark.unit
def test_every_reason_a_session_ends_with_has_a_state_and_words() -> None:
    """A reason the session writes is a word History has a sentence for, and a state the row can
    end in: the stranger answering a token included, which is not a refusal (no code was ever
    compared) and says so."""
    from sift.kernel.access.sentences import SWAP_ENDED
    from sift.slices.swap.session import _STATE_OF, REASONS, WRONG_DEVICE

    assert set(REASONS) == set(_STATE_OF)
    assert set(REASONS) <= set(SWAP_ENDED)
    assert WRONG_DEVICE in REASONS and _STATE_OF[WRONG_DEVICE] == "ended"
    assert "different device" in SWAP_ENDED[WRONG_DEVICE]


# --- the frames a peer may not send ------------------------------------------------------------


def _frame(body: bytes) -> bytes:
    return len(body).to_bytes(4, "big") + body


def _json_frame(value: object) -> bytes:
    import json

    return _frame(json.dumps(value).encode())


@pytest.mark.unit
def test_this_side_refuses_to_send_what_it_could_not_read_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(frames, "MESSAGE_CAP", 2 * frames.JSON_CAP)
    with pytest.raises(ValueError, match="too long"):
        encode_json({"text": "x" * (3 * frames.JSON_CAP)})
    with pytest.raises(ValueError, match="2\\*\\*56"):
        encode_chunk(1 << 56, 0, b"x")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("frames", "words"),
    [
        ((_frame(b""),), "an empty frame"),
        ((_frame(b"\x00" + bytes(3)),), "a chunk frame of the wrong size"),
        (((frames.JSON_CAP + transfer.CHUNK_SIZE + 64).to_bytes(4, "big"),), "past every cap"),
        ((_json_frame({"part": 0, "of": 1, "text": "{}"}),), "with no count"),
        (
            (
                _json_frame({"part": 0, "of": 2, "text": "{"}),
                _json_frame({"part": 2, "of": 2, "text": "}"}),
            ),
            "out of order",
        ),
        ((_json_frame({"part": 0, "of": 2, "text": 5}),), "with no text"),
        (
            (_json_frame({"part": 0, "of": 2, "text": "{"}), encode_chunk(0, 0, b"x")),
            "a chunk inside a message",
        ),
        (
            (
                _json_frame({"part": 0, "of": 2, "text": "{not"}),
                _json_frame({"part": 1, "of": 2, "text": " json"}),
            ),
            "isn't JSON",
        ),
        (
            (
                _json_frame({"part": 0, "of": 2, "text": "[1,"}),
                _json_frame({"part": 1, "of": 2, "text": "2]"}),
            ),
            "isn't an object",
        ),
    ],
)
async def test_a_frame_a_peer_may_not_send_is_refused(
    frames: tuple[bytes, ...], words: str
) -> None:
    conn = Conn(_Pipe(*frames).reader, None)  # type: ignore[arg-type]
    with pytest.raises(ProtocolError, match=words):
        await conn.read()


@pytest.mark.unit
async def test_a_message_in_parts_past_the_cap_is_refused_before_it_is_joined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(frames, "MESSAGE_CAP", 10_000)
    part = "x" * 6_000
    conn = Conn(
        _Pipe(
            _json_frame({"part": 0, "of": 3, "text": part}),
            _json_frame({"part": 1, "of": 3, "text": part}),
        ).reader,
        None,  # type: ignore[arg-type]
    )

    with pytest.raises(ProtocolError, match="past the cap"):
        await conn.read()


@pytest.mark.unit
def test_a_hello_is_made_only_as_a_side_with_a_whole_nonce() -> None:
    me = _device()
    secret = os.urandom(32)
    with pytest.raises(ValueError, match="host or the guest"):
        hello(me, "watcher", secret, os.urandom(32))
    with pytest.raises(ValueError, match="host or the guest"):
        hello(me, "guest", secret, os.urandom(31))


@pytest.mark.unit
def test_a_hello_whose_fields_are_not_hex_of_their_length_is_refused() -> None:
    me = _device()
    secret, nonce = os.urandom(32), os.urandom(32)
    frame = hello(me, "guest", secret, nonce)
    for broken, words in (
        ({**frame, "nonce": "ab"}, "wrong length"),
        ({**frame, "key": 5}, "wrong length"),
        ({**frame, "sig": "zz" * 64}, "isn't hex"),
    ):
        with pytest.raises(ProtocolError, match=words):
            read_hello(broken, "guest", secret)


# --- the presses and the task, with nobody on the other end -------------------------------------


class _WrongKey(_Context):
    """A task whose admin's key is not the one the device key was sealed with."""

    async def master_key(self) -> bytes:
        return b"\x08" * 32


class _NoQueue(_Jobs):
    async def enqueue(self, job_type: str, payload: Any = None, **options: Any) -> str:
        raise RuntimeError("the queue is closed")


@pytest.mark.integration
async def test_a_copy_of_sift_that_cannot_lock_a_swap_refuses_both_presses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = await _database(tmp_path / "s.sqlite3")
    sessions = _sessions(database, tmp_path)
    try:
        monkeypatch.setattr(lock, "PSK_AVAILABLE", False)
        with pytest.raises(swap.SwapRefused, match="can't lock a swap"):
            await _start_host(sessions)
        with pytest.raises(swap.SwapRefused, match="can't lock a swap"):
            await sessions.join(
                viewer_id=VIEWER, token_text="x", dest_folder_id="f", master_key=_MASTER
            )
    finally:
        await database.close()


@pytest.mark.integration
async def test_with_the_keys_locked_neither_press_starts_anything(tmp_path: Path) -> None:
    database = await _database(tmp_path / "s.sqlite3")
    sessions = _sessions(database, tmp_path)
    try:
        with pytest.raises(swap.SwapRefused, match=swap.LOCKED):
            await sessions.start(
                viewer=SimpleNamespace(id=VIEWER),  # type: ignore[arg-type]
                chosen=[Chosen(kind="person", id="p1")],
                tunnel_id="tunnel-host",
                share_boxes=True,
                master_key=None,
            )
        with pytest.raises(swap.SwapRefused, match=swap.LOCKED):
            await sessions.join(
                viewer_id=VIEWER, token_text="x", dest_folder_id="f", master_key=None
            )
        assert not sessions.any_live()
    finally:
        await database.close()


@pytest.mark.integration
async def test_a_hosting_that_fails_for_any_other_reason_takes_the_listener_down(
    tmp_path: Path,
) -> None:
    class _Broken(_Hoster):
        async def host_on(self, tunnel_id: str, **_kwargs: Any) -> Any:
            raise RuntimeError("the tunnel's process died")

    database = await _database(tmp_path / "s.sqlite3")
    sessions = _sessions(database, tmp_path, hoster=_Broken())
    try:
        with pytest.raises(RuntimeError, match="process died"):
            await _start_host(sessions)
        assert not sessions.any_live()
    finally:
        await database.close()


@pytest.mark.integration
@pytest.mark.skipif(not lock.PSK_AVAILABLE, reason="TLS with a key needs CPython 3.13")
async def test_a_provider_that_gives_no_public_address_is_refused_and_put_back(
    tmp_path: Path,
) -> None:
    class _Private(_Hoster):
        async def host_on(self, tunnel_id: str, **kwargs: Any) -> _Hosted:
            await super().host_on(tunnel_id, **kwargs)
            return _Hosted("10.0.0.7", _PORT)

    hoster = _Private()
    database = await _database(tmp_path / "s.sqlite3")
    sessions = _sessions(database, tmp_path, hoster=hoster)
    try:
        with pytest.raises(swap.SwapRefused, match="didn't give it a public address"):
            await _start_host(sessions)
        assert not sessions.any_live()
        assert hoster.stopped == ["tunnel-host"], "the hosting is put back"
    finally:
        await database.close()


@pytest.mark.integration
@pytest.mark.skipif(not lock.PSK_AVAILABLE, reason="TLS with a key needs CPython 3.13")
async def test_a_queue_that_will_not_take_the_task_ends_the_session_it_was_for(
    tmp_path: Path,
) -> None:
    import time

    database = await _database(tmp_path / "s.sqlite3")
    hoster = _Hoster()
    sessions = _sessions(database, tmp_path, hoster=hoster, jobs=_NoQueue())
    try:
        with pytest.raises(RuntimeError, match="queue is closed"):
            await _start_host(sessions)
        assert hoster.stopped == ["tunnel-host"]

        elsewhere = device_id_of(os.urandom(32))
        made = token_module.mint(_ADDRESS, _PORT, elsewhere, int(time.time()))
        with pytest.raises(RuntimeError, match="queue is closed"):
            await sessions.join(
                viewer_id=VIEWER, token_text=made.text, dest_folder_id="f", master_key=_MASTER
            )
        assert not sessions.any_live()
        states = {
            (row["role"], row["state"], row["end_reason"])
            for row in await database.fetch_all(
                "SELECT role, state, end_reason FROM swap_sessions", ()
            )
        }
        assert states == {("host", "failed", "lost"), ("guest", "failed", "lost")}
    finally:
        await database.close()


@pytest.mark.integration
@pytest.mark.skipif(not lock.PSK_AVAILABLE, reason="TLS with a key needs CPython 3.13")
async def test_a_token_that_does_not_read_or_was_made_here_is_refused_with_words(
    tmp_path: Path,
) -> None:
    database = await _database(tmp_path / "s.sqlite3")
    sessions = _sessions(database, tmp_path)
    try:
        with pytest.raises(swap.SwapRefused, match="isn't a swap token"):
            await sessions.join(
                viewer_id=VIEWER, token_text="nonsense", dest_folder_id="f", master_key=_MASTER
            )
        started = await _start_host(sessions)
        with pytest.raises(swap.SwapRefused, match="made on this device"):
            await sessions.join(
                viewer_id=VIEWER,
                token_text=started.token.text,
                dest_folder_id="f",
                master_key=_MASTER,
            )
        await sessions.end(started.session_id)
    finally:
        await database.close()


@pytest.mark.integration
async def test_ending_a_swap_nothing_is_holding_writes_the_end_on_its_row(tmp_path: Path) -> None:
    database = await _database(tmp_path / "s.sqlite3")
    sessions = _sessions(database, tmp_path)
    try:
        await sessions.store.create(
            "01HENDNOTLIVE000000000001", role="guest", started_at=1, started_by=VIEWER
        )

        await sessions.end("01HENDNOTLIVE000000000001")

        row = await sessions.row("01HENDNOTLIVE000000000001")
        assert row is not None and (row.state, row.end_reason) == ("ended", swap.ENDED_BY_YOU)
        assert sessions.facts("01HENDNOTLIVE000000000001") is None
    finally:
        await database.close()


@pytest.mark.integration
async def test_a_task_for_a_session_that_is_over_or_was_lost_to_a_restart_ends_immediately(
    tmp_path: Path,
) -> None:
    database = await _database(tmp_path / "s.sqlite3")
    sessions = _sessions(database, tmp_path)
    try:
        # No such row: nothing to do.
        await sessions.run(_Context("01HNOSUCHSESSION000000001", tmp_path / "ws"))  # type: ignore[arg-type]
        # A live row with nothing in memory: the token's secret went with the restart.
        await sessions.store.create(
            "01HRESTARTEDTASK000000001", role="host", started_at=1, started_by=VIEWER
        )
        await sessions.run(_Context("01HRESTARTEDTASK000000001", tmp_path / "ws"))  # type: ignore[arg-type]

        row = await sessions.row("01HRESTARTEDTASK000000001")
        assert row is not None and (row.state, row.end_reason) == ("failed", swap.LOST)
    finally:
        await database.close()


@pytest.mark.integration
@pytest.mark.skipif(not lock.PSK_AVAILABLE, reason="TLS with a key needs CPython 3.13")
async def test_a_task_whose_device_key_will_not_open_ends_its_session(tmp_path: Path) -> None:
    database = await _database(tmp_path / "s.sqlite3")
    hoster = _Hoster()
    sessions = _sessions(database, tmp_path, hoster=hoster)
    try:
        started = await _start_host(sessions)

        await sessions.run(_WrongKey(started.session_id, tmp_path / "ws"))  # type: ignore[arg-type]

        row = await sessions.row(started.session_id)
        assert row is not None and (row.state, row.end_reason) == ("failed", swap.LOST)
        assert hoster.stopped == ["tunnel-host"] and not sessions.any_live()
    finally:
        await database.close()


@pytest.mark.integration
@pytest.mark.skipif(not lock.PSK_AVAILABLE, reason="TLS with a key needs CPython 3.13")
@pytest.mark.parametrize("how", ["fails", "returns"])
async def test_a_session_whose_drive_fails_or_stops_without_an_end_is_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    """The task never leaves a live row behind it: a drive that raised, or one that returned
    without ending the session, is written down as lost."""
    database = await _database(tmp_path / "s.sqlite3")
    sessions = _sessions(database, tmp_path)
    try:
        started = await _start_host(sessions)
        live = sessions.live(started.session_id)
        assert live is not None

        async def drive() -> None:
            if how == "fails":
                raise OSError("the listener's socket went")

        monkeypatch.setattr(live, "drive", drive)

        await sessions.run(_Context(started.session_id, tmp_path / "ws"))  # type: ignore[arg-type]

        row = await sessions.row(started.session_id)
        assert row is not None and (row.state, row.end_reason) == ("failed", swap.LOST)
    finally:
        await database.close()


@pytest.mark.integration
@pytest.mark.skipif(not lock.PSK_AVAILABLE, reason="TLS with a key needs CPython 3.13")
async def test_a_task_cancelled_on_activity_ends_the_session_as_this_sides_doing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = await _database(tmp_path / "s.sqlite3")
    sessions = _sessions(database, tmp_path)
    try:
        started = await _start_host(sessions)
        live = sessions.live(started.session_id)
        assert live is not None
        driving = asyncio.Event()

        async def drive() -> None:
            driving.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(live, "drive", drive)
        task = asyncio.create_task(sessions.run(_Context(started.session_id, tmp_path / "ws")))  # type: ignore[arg-type]
        await asyncio.wait_for(driving.wait(), 5)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        row = await sessions.row(started.session_id)
        assert row is not None and (row.state, row.end_reason) == ("ended", swap.ENDED_BY_YOU)
    finally:
        await database.close()


@pytest.mark.integration
@pytest.mark.skipif(not lock.PSK_AVAILABLE, reason="TLS with a key needs CPython 3.13")
async def test_a_task_that_settled_without_running_takes_its_hosting_down(tmp_path: Path) -> None:
    """Cancelled while it waited for a worker: the listener and the hosting tunnel are still up,
    and the task settling is what ends them."""
    database = await _database(tmp_path / "s.sqlite3")
    hoster = _Hoster()
    jobs = _Jobs()
    sessions = _sessions(database, tmp_path, hoster=hoster, jobs=jobs)
    try:
        started = await _start_host(sessions)
        await sessions.on_settled("job-that-was-never-this-one")
        assert sessions.any_live()

        await sessions.on_settled("job-1")

        row = await sessions.row(started.session_id)
        assert row is not None and (row.state, row.end_reason) == ("ended", swap.ENDED_BY_YOU)
        assert hoster.stopped == ["tunnel-host"]
    finally:
        await database.close()


@pytest.mark.integration
@pytest.mark.skipif(not lock.PSK_AVAILABLE, reason="TLS with a key needs CPython 3.13")
async def test_a_restart_leaves_alone_a_session_this_process_is_holding(tmp_path: Path) -> None:
    database = await _database(tmp_path / "s.sqlite3")
    sessions = _sessions(database, tmp_path)
    try:
        started = await _start_host(sessions)

        assert await sessions.settle_after_restart() == []

        row = await sessions.row(started.session_id)
        assert row is not None and row.state == "waiting"
        await sessions.end(started.session_id)
    finally:
        await database.close()


@pytest.mark.integration
@pytest.mark.skipif(not lock.PSK_AVAILABLE, reason="TLS with a key needs CPython 3.13")
async def test_a_week_old_unfinished_file_is_forgotten_and_only_staging_is_removed(
    tmp_path: Path,
) -> None:
    """Its staged chunks go when they are under this device's staging folder, and never a path
    anywhere else a row might name; a file of a session still running is not touched."""
    database = await _database(tmp_path / "s.sqlite3")
    sessions = _sessions(database, tmp_path)
    try:
        started = await _start_host(sessions)
        await sessions.store.create(
            "01HSWEPTSESSION0000000001", role="guest", started_at=1, started_by=VIEWER
        )
        ours = transfer.staged_path(sessions.staging, "01HSWEPTSESSION0000000001", "k-ours")
        ours.parent.mkdir(parents=True, exist_ok=True)
        ours.write_bytes(b"half a file")
        elsewhere = tmp_path / "someone-elses.bin"
        elsewhere.write_bytes(b"not ours")
        for session_id, key, path in (
            ("01HSWEPTSESSION0000000001", "k-ours", ours),
            ("01HSWEPTSESSION0000000001", "k-else", elsewhere),
            (started.session_id, "k-live", ours),
        ):
            await sessions.store.put_manifest(
                session_id,
                key,
                size=10,
                chunk_size=4,
                digest="d" * 64,
                staged_path=str(path),
                now=1,
            )

        assert await sessions.sweep_manifests() == 2

        assert not ours.exists() and elsewhere.exists()
        assert await sessions.store.manifest("01HSWEPTSESSION0000000001", "k-else") is None
        assert await sessions.store.manifest(started.session_id, "k-live") is not None
        await sessions.end(started.session_id)
    finally:
        await database.close()


@pytest.mark.integration
@pytest.mark.skipif(not lock.PSK_AVAILABLE, reason="TLS with a key needs CPython 3.13")
async def test_a_hosts_stripped_copies_go_where_it_was_told_or_under_staging(
    tmp_path: Path,
) -> None:
    database = await _database(tmp_path / "s.sqlite3")
    told = _sessions(database, tmp_path, workdir=lambda session_id: tmp_path / "wd" / session_id)
    plain = _sessions(database, tmp_path)
    try:
        for sessions, under in (
            (told, tmp_path / "wd"),
            (plain, tmp_path / "staging" / "outgoing"),
        ):
            started = await _start_host(sessions)
            live = sessions.live(started.session_id)
            assert live is not None
            assert sessions.workdir_of(live) == under / started.session_id
            await sessions.end(started.session_id)

        assert not plain.staged_is_ours(None)
        assert not plain.staged_is_ours(str(tmp_path / "elsewhere.bin"))
        assert plain.staged_is_ours(str(tmp_path / "staging" / "s" / "x.part"))
    finally:
        await database.close()


@pytest.mark.unit
def test_a_staged_path_that_cannot_be_resolved_is_not_ours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A share that went away under the staging folder: the path cannot be followed, so nothing
    under it is removed on the strength of a row."""
    sessions: Any = SimpleNamespace(staging=tmp_path / "staging")

    def unreachable(self: Path, strict: bool = False) -> Path:
        raise OSError("the network name cannot be found")

    monkeypatch.setattr(Path, "resolve", unreachable)

    assert not SwapSessions.staged_is_ours(sessions, str(tmp_path / "staging" / "x.part"))


@pytest.mark.unit
def test_the_session_task_is_registered_under_its_name() -> None:
    from sift.kernel.jobs import registered_job_names
    from sift.kernel.jobs.families import OwnEstimate, own_estimate
    from sift.kernel.jobs.worker_pool import registered_handlers

    sessions: Any = SimpleNamespace(
        run=lambda _context: None, time_left=lambda: OwnEstimate(seconds=7)
    )
    swap.register_handlers(sessions)

    assert registered_handlers()[swap.SWAP_SESSION] is sessions.run
    assert registered_job_names()[swap.SWAP_SESSION] == "Swapping with another Sift"
    assert own_estimate(swap.SWAP_SESSION) == OwnEstimate(seconds=7), "Activity asks the swaps"


# --- the host against a guest written to say the wrong thing -------------------------------------

_needs_psk = pytest.mark.skipif(not lock.PSK_AVAILABLE, reason="TLS with a key needs CPython 3.13")


def _offer_of(files: dict[str, bytes]) -> Offer:
    return Offer(
        files=[
            OfferedFile(
                key=key, size=len(data), identity=f"id-{key}", kind="video", title=f"clip {key}"
            )
            for key, data in files.items()
        ]
    )


def _header(index: int, data: bytes, digest: str | None = None) -> dict[str, Any]:
    """A file's header as a host sends it."""
    return {
        "file": index,
        "size": len(data),
        "digest": digest or blake3(data).hexdigest(),
        "chunk_size": CHUNK_SIZE,
    }


async def _keep_pinging(conn: Conn) -> None:
    """The hand-written side's pings, so the other side's watchdog hears it while a test runs."""
    with suppress(Exception):
        while True:
            await asyncio.sleep(0.2)
            await conn.send({"ping": 1})


async def _dropped(conn: Conn) -> None:
    """The other side closed this connection: the next read finds its end."""
    with pytest.raises((asyncio.IncompleteReadError, OSError)):
        await conn.read(5)


async def _ended_row(sessions: SwapSessions, session_id: str) -> SessionRow:
    """The row once the session is over and forgotten."""
    loop = asyncio.get_running_loop()
    began = loop.time()
    while True:
        row = await sessions.row(session_id)
        assert row is not None
        if not row.live and sessions.live(session_id) is None:
            return row
        if loop.time() - began > 10:
            raise AssertionError("the session did not end")
        await asyncio.sleep(0.02)


@dataclass
class _HostRig:
    """A real host session, and the hand-written guest's side of it."""

    sessions: SwapSessions
    started: swap.Started
    hoster: _Hoster
    context: _Context
    workspace: Path
    task: asyncio.Task[None] | None = None
    control: Conn | None = None
    session: str | None = None
    pinger: asyncio.Task[None] | None = None
    opened: list[Conn] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.started.session_id

    @property
    def live(self) -> swap.HostSession:
        live = self.sessions.live(self.id)
        assert isinstance(live, swap.HostSession)
        return live

    def code(self) -> str | None:
        facts = self.sessions.facts(self.id)
        return None if facts is None else facts.code

    def stripped(self) -> list[Path]:
        return sorted(self.workspace.glob("*.strip")) if self.workspace.exists() else []

    async def dial(self, secret: bytes | None = None) -> Conn:
        assert self.hoster.target is not None
        locked = await lock.dial(
            "127.0.0.1", self.hoster.target, None, secret or self.started.token.secret
        )
        conn = Conn(*locked)
        self.opened.append(conn)
        return conn

    async def join(self) -> Conn:
        """A guest's hello, the host's answer, and the code on the host's screen."""
        conn = await self.dial()
        secret = self.started.token.secret
        await conn.send(hello(_device(), "guest", secret, os.urandom(32)))
        answer = await conn.read(5)
        assert isinstance(answer, dict)
        self.session = read_hello(answer, "host", secret).session
        self.control = conn
        self.pinger = asyncio.create_task(_keep_pinging(conn))
        await _until(self.code)
        return conn

    async def offer(self) -> dict[str, Any]:
        """They match, and the offer it releases."""
        assert self.control is not None
        await self.sessions.answer_code(self.id, True)
        offer: dict[str, Any] = (await _read_until(self.control, "offer"))["offer"]
        return offer

    async def transferring(self, *keys: str) -> None:
        await self.offer()
        assert self.control is not None
        await self.control.send(
            {"diff": Diff(wanted=list(keys), people={}).model_dump(mode="json")}
        )
        live = self.live
        await _until(lambda: live.transferring)

    async def stream(self, session: str | None = None) -> Conn:
        conn = await self.dial()
        await conn.send({"stream": 0, "session": self.session if session is None else session})
        return conn

    async def finish(self) -> SessionRow:
        """The guest's done, and the row the host writes for it."""
        assert self.control is not None and self.task is not None
        await self.control.send({"done": {"received": 0}})
        await asyncio.wait_for(self.task, 10)
        return await _ended_row(self.sessions, self.id)

    async def ended(self) -> SessionRow:
        assert self.task is not None
        await asyncio.wait_for(self.task, 10)
        return await _ended_row(self.sessions, self.id)


@asynccontextmanager
async def _host_rig(
    tmp_path: Path,
    files: dict[str, bytes] | None = None,
    *,
    run: bool = True,
    gone: tuple[str, ...] = (),
    hoster: _Hoster | None = None,
    **kwargs: Any,
) -> AsyncIterator[_HostRig]:
    """A host session offering `files`, its task running unless `run` is False. A key in `gone` is
    offered, and is no longer on the host's disk when a stream asks for it."""
    files = {"a": b"a small file"} if files is None else files
    library = tmp_path / "library"
    library.mkdir()
    for key, data in files.items():
        (library / key).write_bytes(data)
    offer = _offer_of(files)

    async def make_offer(
        viewer: object, chosen: object, share_boxes: bool, peer_model: str | None = None
    ) -> Offer:
        return offer

    async def path_of(viewer: object, key: str) -> Path | None:
        return None if key in gone else library / key

    database = await _database(tmp_path / "host.sqlite3")
    hoster = hoster or _Hoster()
    sessions = _sessions(
        database, tmp_path, hoster=hoster, make_offer=make_offer, path_of=path_of, **kwargs
    )
    rig: _HostRig | None = None
    try:
        started = await _start_host(sessions)
        workspace = tmp_path / "ws"
        rig = _HostRig(
            sessions, started, hoster, _Context(started.session_id, workspace), workspace
        )
        if run:
            rig.task = asyncio.create_task(sessions.run(rig.context))  # type: ignore[arg-type]
        yield rig
    finally:
        if rig is not None:
            if sessions.live(rig.id) is not None:
                await sessions.end(rig.id)
            if rig.task is not None:
                with suppress(Exception, asyncio.CancelledError):
                    await asyncio.wait_for(rig.task, 5)
            if rig.pinger is not None:
                rig.pinger.cancel()
            for conn in rig.opened:
                conn.close()
        await database.close()


# --- the guest against a host written to say the wrong thing -------------------------------------

#: The session id the hand-written host names in its hello, and each stream names back.
_HOST_SESSION = "01HHANDWRITTENHOST0000001"


async def _send_whole(stream: Conn, index: int, data: bytes) -> None:
    """A file as a host sends it: the header, every chunk acknowledged, then the guest's word."""
    await stream.send(_header(index, data))
    assert await stream.read(5) == {"have": []}
    for number in range(transfer.chunk_count(len(data))):
        await stream.send_chunk(
            index, number, data[number * CHUNK_SIZE : (number + 1) * CHUNK_SIZE]
        )
        assert await stream.read(5) == {"ack": number, "file": index}
    assert await stream.read(5) == {"file_done": index}


def _bad_chunk(file_index: int, chunk_index: int, data: bytes) -> bytes:
    """A chunk frame whose digest is not its bytes'."""
    body = frames.CHUNK_PREFIX.pack(file_index, chunk_index, bytes(32)) + data
    return len(body).to_bytes(4, "big") + body


@dataclass
class _GuestRig:
    """A real guest session, and the hand-written host's side of it."""

    sessions: SwapSessions
    id: str
    secret: bytes
    host: Device
    offer: Offer
    proxy: _Proxy
    accepted: asyncio.Queue[Conn]
    context: _Context
    #: The secret the host's listener locks with. Changed, it refuses every dial after.
    lock_with: list[bytes]
    landed: dict[str, bytes] = field(default_factory=dict)
    task: asyncio.Task[None] | None = None
    pinger: asyncio.Task[None] | None = None
    held: list[Conn] = field(default_factory=list)

    @property
    def live(self) -> swap.GuestSession:
        live = self.sessions.live(self.id)
        assert isinstance(live, swap.GuestSession)
        return live

    def staged(self, key: str) -> Path:
        return transfer.staged_path(self.sessions.staging, self.id, key)

    async def conn(self) -> Conn:
        return await asyncio.wait_for(self.accepted.get(), 5)

    async def answer_hello(
        self, conn: Conn, *, device: Device | None = None, secret: bytes | None = None
    ) -> None:
        first = await conn.read(5)
        assert isinstance(first, dict)
        read_hello(first, "guest", self.secret)
        await conn.send(
            hello(
                device or self.host,
                "host",
                secret or self.secret,
                os.urandom(32),
                session=_HOST_SESSION,
            )
        )

    async def connected(self) -> Conn:
        """The guest's hello answered, and the code on the guest's screen."""
        conn = await self.conn()
        await self.answer_hello(conn)
        self.pinger = asyncio.create_task(_keep_pinging(conn))
        await _until(lambda: (facts := self.sessions.facts(self.id)) is not None and facts.code)
        return conn

    async def offer_to(self, control: Conn) -> None:
        await control.send({"offer": self.offer.model_dump(mode="json")})
        await _until(lambda: (facts := self.sessions.facts(self.id)) is not None and facts.screen)

    async def take(self, control: Conn, taken: Taken | None = None) -> dict[str, Any]:
        """The guest's answer, and the diff it sends."""
        await self.sessions.take(self.id, taken or Taken())
        diff: dict[str, Any] = (await _read_until(control, "diff"))["diff"]
        return diff

    async def stream(self, number: int | None = None) -> tuple[Conn, int]:
        """The next stream the guest opens (the one numbered `number`, when given), past its
        first frame. The others are held open, unanswered."""
        while True:
            conn = await self.conn()
            intro = await conn.read(5)
            assert isinstance(intro, dict) and intro["session"] == _HOST_SESSION
            if number is None or intro["stream"] == number:
                return conn, int(intro["stream"])
            self.held.append(conn)

    async def ended(self) -> SessionRow:
        assert self.task is not None
        await asyncio.wait_for(self.task, 15)
        return await _ended_row(self.sessions, self.id)


async def _refusing_proxy() -> asyncio.Server:
    """A tunnel whose proxy carries nothing: every CONNECT is answered 502."""

    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        await writer.drain()
        writer.close()

    return await asyncio.start_server(answer, "127.0.0.1", 0)


@asynccontextmanager
async def _guest_rig(
    tmp_path: Path,
    files: dict[str, bytes] | None = None,
    *,
    run: bool = True,
    answer: Callable[[Taken], Diff] | None = None,
    land: Callable[..., Any] | None = None,
    egress: _Egress | None = None,
    **kwargs: Any,
) -> AsyncIterator[_GuestRig]:
    """A guest session joined to a hand-written host offering `files`, its task running unless
    `run` is False. The guest wants every file unless `answer` says otherwise."""
    files = {"a": b"a small file"} if files is None else files
    offer = _offer_of(files)
    host, secret = _device(), os.urandom(32)
    accepted: asyncio.Queue[Conn] = asyncio.Queue()
    every: list[Conn] = []
    closing = asyncio.Event()
    lock_with = [secret]

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            locked = await lock.accept(reader, writer, lock_with[0])
        except lock.LockFailed:
            return
        conn = Conn(*locked)
        every.append(conn)
        await accepted.put(conn)
        await closing.wait()

    listener = await asyncio.start_server(handle, "127.0.0.1", 0)
    forward = _Hoster()
    forward.target = int(listener.sockets[0].getsockname()[1])
    proxy = _Proxy(forward)
    await proxy.start()
    rig: _GuestRig | None = None

    async def assess(arrived: Offer) -> tuple[OfferScreen, Callable[[Taken], Diff]]:
        def everything(taken: Taken) -> Diff:
            return Diff(wanted=[one.key for one in arrived.files], people={})

        return OfferScreen(layout="rows"), answer or everything

    async def keep(session: Any, received: Any, *, ctx: Any) -> None:
        assert rig is not None
        rig.landed[received.key] = await asyncio.to_thread(received.staged.read_bytes)

    database = await _database(tmp_path / "guest.sqlite3")
    sessions = _sessions(
        database,
        tmp_path / "g",
        egress=egress or _Egress(proxy.url),
        assess=assess,
        land=land or keep,
        **kwargs,
    )
    try:
        token = token_module.mint(_ADDRESS, _PORT, host.id, sessions.now(), secret=secret)
        joined = await sessions.join(
            viewer_id=VIEWER, token_text=token.text, dest_folder_id="folder", master_key=_MASTER
        )
        rig = _GuestRig(
            sessions,
            joined,
            secret,
            host,
            offer,
            proxy,
            accepted,
            _Context(joined, tmp_path / "gw"),
            lock_with,
        )
        if run:
            rig.task = asyncio.create_task(sessions.run(rig.context))  # type: ignore[arg-type]
        yield rig
    finally:
        if rig is not None:
            if sessions.live(rig.id) is not None:
                await sessions.end(rig.id)
            if rig.task is not None:
                with suppress(Exception, asyncio.CancelledError):
                    await asyncio.wait_for(rig.task, 5)
            if rig.pinger is not None:
                rig.pinger.cancel()
        closing.set()
        for conn in every:
            conn.close()
        listener.close()
        if proxy.server is not None:
            proxy.server.close()
        await database.close()
