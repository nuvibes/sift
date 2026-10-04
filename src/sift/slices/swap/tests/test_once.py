# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each file read once: the whole-file check at the end, for a side whose hello says `once`, and
the shape an older Sift knows for one that does not. On loopback, with the session tests' rigs."""

from __future__ import annotations

import asyncio
import os
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from blake3 import blake3

from sift.slices.swap import guest as guest_side
from sift.slices.swap import host as host_side
from sift.slices.swap import pieces, transfer
from sift.slices.swap import session as swap
from sift.slices.swap.models import Diff, Offer, OfferedFile, OfferScreen
from sift.slices.swap.session import Chunk, Taken, hello
from sift.slices.swap.store import SessionStore
from sift.slices.swap.tests.test_cut_off import _drained
from sift.slices.swap.tests.test_sending import _join_in_shares
from sift.slices.swap.tests.test_session import (
    _HOST_SESSION,
    _MASTER,
    VIEWER,
    _Context,
    _database,
    _device,
    _Egress,
    _guest_rig,
    _host_rig,
    _Hoster,
    _keep_pinging,
    _needs_psk,
    _Proxy,
    _read_until,
    _sessions,
    _start_host,
    _until,
)
from sift.slices.swap.transfer import CHUNK_SIZE, Prepared

_DATA = os.urandom(CHUNK_SIZE * 3 + 777)
_COUNT = transfer.chunk_count(len(_DATA))


async def _as_it_is(source: Path, _workdir: Path) -> Prepared:
    """An original made ready as `transfer.prepare` makes one: its stamp, nothing read whole."""
    info = source.stat()
    return Prepared(source, info.st_size, copy=False, stamp=(info.st_size, info.st_mtime_ns))


def _pieces_of(data: bytes) -> str:
    chunks = [data[n * CHUNK_SIZE : (n + 1) * CHUNK_SIZE] for n in range(_COUNT)]
    return pieces.pieces_digest(transfer.chunk_digest(one) for one in chunks)


# --- the pieces' digest and the version ----------------------------------------------------------


@pytest.mark.unit
def test_the_whole_is_the_digest_of_every_pieces_digest_in_piece_order(tmp_path: Path) -> None:
    staged = tmp_path / "x.part"
    staged.write_bytes(_DATA)

    whole, ours = pieces.staged_digests(staged, len(_DATA))

    assert whole == blake3(_DATA).hexdigest(), "the landing's digest, from the same read"
    assert ours == _pieces_of(_DATA)
    digests = [transfer.chunk_digest(_DATA[n * CHUNK_SIZE : (n + 1) * CHUNK_SIZE]) for n in (0, 1)]
    assert pieces.pieces_digest(digests) != pieces.pieces_digest(digests[::-1])
    empty = blake3(b"").hexdigest()
    assert pieces.staged_digests(tmp_path / "never-written.part", 0) == (empty, empty)


@pytest.mark.unit
def test_a_version_names_the_file_its_size_and_its_time_and_says_neither() -> None:
    me, other = _device(), _device()
    version = pieces.version_of(me, "key-a", (10, 1_700_000_000_000_000_000))

    assert version == pieces.version_of(me, "key-a", (10, 1_700_000_000_000_000_000))
    assert len(version) == 64 and "1700000000" not in version
    for moved in (
        pieces.version_of(me, "key-a", (10, 1_700_000_000_000_000_001)),
        pieces.version_of(me, "key-a", (11, 1_700_000_000_000_000_000)),
        pieces.version_of(me, "key-b", (10, 1_700_000_000_000_000_000)),
        pieces.version_of(other, "key-a", (10, 1_700_000_000_000_000_000)),
    ):
        assert moved != version


async def test_the_senders_pieces_digest_reads_only_the_pieces_it_never_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "a.bin"
    source.write_bytes(_DATA)
    info = source.stat()
    ready = Prepared(source, info.st_size, copy=False, stamp=(info.st_size, info.st_mtime_ns))
    read: list[int] = []
    real = transfer.read_ready_chunk

    async def counted(prepared: Prepared, index: int) -> bytes:
        read.append(index)
        return await real(prepared, index)

    monkeypatch.setattr(pieces, "read_ready_chunk", counted)
    sent = {n: transfer.chunk_digest(_DATA[n * CHUNK_SIZE : (n + 1) * CHUNK_SIZE]) for n in (1, 3)}

    assert await pieces.pieces_of(ready, sent) == _pieces_of(_DATA)
    assert read == [0, 2], "only what the receiver held from before"
    source.write_bytes(_DATA + b"edited")
    with pytest.raises(transfer.Changed):
        await pieces.pieces_of(ready, sent)


# --- two Sifts: each file read once, or twice for a side that checks first ------------------------


async def _a_swap_of_one_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, older: bool
) -> tuple[dict[str, bytes], Counter[str], list[dict[str, Any]]]:
    """A real host sending `_DATA` to a real guest. What landed, how often the host read each
    chunk of the original, and every header it sent. With `older`, each side reads the other's
    hello as an older Sift's, which never says `once`."""
    library = tmp_path / "library"
    library.mkdir()
    (library / "a.bin").write_bytes(_DATA)
    offer = Offer(
        files=[OfferedFile(key="a", size=len(_DATA), identity="id", kind="video", title="clip")]
    )

    async def make_offer(*_args: object) -> Offer:
        return offer

    async def path_of(_viewer: object, _key: str) -> Path:
        return library / "a.bin"

    async def assess(_arrived: Offer) -> tuple[OfferScreen, Callable[[Taken], Diff]]:
        return OfferScreen(layout="rows"), lambda _taken: Diff(wanted=["a"], people={})

    landed: dict[str, bytes] = {}

    async def land(_session: Any, received: Any, *, ctx: Any) -> None:
        landed[received.key] = await asyncio.to_thread(received.staged.read_bytes)

    reads: Counter[str] = Counter()
    real_read = transfer.read_chunk

    def counted(path: Path, index: int, size: int, chunk_size: int = CHUNK_SIZE) -> bytes:
        if path.parent == library:
            reads[f"chunk {index}"] += 1
        return real_read(path, index, size, chunk_size)

    monkeypatch.setattr(transfer, "read_chunk", counted)
    headers: list[dict[str, Any]] = []
    real_send = swap.Conn.send

    async def recorded(self: swap.Conn, message: Any) -> None:
        if "chunk_size" in message:
            headers.append(dict(message))
        await real_send(self, message)

    monkeypatch.setattr(swap.Conn, "send", recorded)
    if older:
        real_hello = swap.read_hello
        # Each side reads the other's hello in its own module.
        for side in (host_side, guest_side):
            monkeypatch.setattr(
                side, "read_hello", lambda *args: replace(real_hello(*args), once=False)
            )
    host_db = await _database(tmp_path / "host.sqlite3")
    guest_db = await _database(tmp_path / "guest.sqlite3")
    hoster = _Hoster()
    proxy = _Proxy(hoster)
    await proxy.start()
    host = _sessions(
        host_db,
        tmp_path / "h",
        hoster=hoster,
        make_offer=make_offer,
        path_of=path_of,
        prepare=_as_it_is,
    )
    guest = _sessions(guest_db, tmp_path / "g", egress=_Egress(proxy.url), assess=assess, land=land)
    try:
        started = await _start_host(host)
        joined = await guest.join(
            viewer_id=VIEWER,
            token_text=started.token.text,
            dest_folder_id="folder",
            master_key=_MASTER,
        )
        host_task = asyncio.create_task(host.run(_Context(started.session_id, tmp_path / "hw")))  # type: ignore[arg-type]
        guest_task = asyncio.create_task(guest.run(_Context(joined, tmp_path / "gw")))  # type: ignore[arg-type]
        await _until(lambda: (facts := host.facts(started.session_id)) is not None and facts.code)
        await host.answer_code(started.session_id, True)
        await _until(lambda: (facts := guest.facts(joined)) is not None and facts.screen)
        await guest.take(joined, Taken())
        await asyncio.wait_for(asyncio.gather(host_task, guest_task), 60)
        return landed, reads, headers
    finally:
        if proxy.server is not None:
            proxy.server.close()
        await host_db.close()
        await guest_db.close()


@pytest.mark.integration
@_needs_psk
@pytest.mark.parametrize("older", [False, True])
async def test_a_file_is_read_once_unless_the_receiver_checks_the_whole_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, older: bool
) -> None:
    landed, reads, headers = await _a_swap_of_one_file(tmp_path, monkeypatch, older=older)

    assert landed == {"a": _DATA}
    times = 2 if older else 1
    assert reads == {f"chunk {n}": times for n in range(_COUNT)}, "the digest first, then each"
    assert headers
    if older:
        assert all(one["digest"] == blake3(_DATA).hexdigest() for one in headers)
        assert not any("version" in one for one in headers)
    else:
        assert all(len(one["version"]) == 64 and "digest" not in one for one in headers)


# --- a host against a guest written by hand ----------------------------------------------------


@pytest.mark.integration
@_needs_psk
@pytest.mark.parametrize(("once", "changed"), [(True, False), (True, True), (False, False)])
async def test_a_host_names_the_version_only_to_a_guest_that_says_once(
    tmp_path: Path, once: bool, changed: bool
) -> None:
    async with _host_rig(tmp_path, {"a": _DATA}, prepare=_as_it_is) as rig:
        await _join_in_shares(rig, **({"once": 1} if once else {}))
        await rig.transferring("a")
        stream = await rig.stream()
        header = await stream.read(5)
        assert isinstance(header, dict) and header["file"] == 0
        if not once:
            assert header["digest"] == blake3(_DATA).hexdigest() and "version" not in header
            return
        assert "digest" not in header and len(header["version"]) == 64
        await stream.send({"have": []})
        for _ in header["chunks"]:
            chunk = await stream.read(5)
            assert isinstance(chunk, Chunk)
            await stream.send({"ack": chunk.index, "file": 0})
        # The rest of the file was held from before: the host reads it now for the digest.
        if changed:
            (tmp_path / "library" / "a").write_bytes(_DATA + b"edited")
            await stream.send({"check": 0})
            assert await stream.read(5) == {"file": 0, "cannot": True}
            return
        await stream.send({"check": 0})
        assert await stream.read(5) == {"file": 0, "pieces": _pieces_of(_DATA)}
        await stream.send({"file_done": 0})
        assert await stream.read(5) == {"none": True}
        row = await rig.finish()
        assert (row.state, row.sent_files) == ("done", 1)


@pytest.mark.integration
@_needs_psk
@pytest.mark.parametrize("copy", [False, True])
async def test_a_file_that_cannot_be_read_whole_for_an_older_guest_is_made_ready_afresh(
    tmp_path: Path, copy: bool
) -> None:
    made: list[Path] = []

    async def stale_first(source: Path, workdir: Path) -> Prepared:
        info = source.stat()
        made.append(source)
        if len(made) > 1:
            return await _as_it_is(source, workdir)
        if copy:
            return Prepared(workdir / "never-written.strip", info.st_size, stamp=None)
        return Prepared(source, info.st_size, copy=False, stamp=(info.st_size, 1))

    async with _host_rig(tmp_path, {"a": _DATA}, prepare=stale_first) as rig:
        await _join_in_shares(rig)
        await rig.transferring("a")
        first = await rig.stream()
        with pytest.raises((asyncio.IncompleteReadError, ConnectionError)):
            await first.read(5)

        header = await (await rig.stream()).read(5)

        assert isinstance(header, dict) and header["digest"] == blake3(_DATA).hexdigest()
        assert len(made) == 2


# --- a guest against a host written by hand ----------------------------------------------------


def _versioned(index: int, version: str, chunks: list[int]) -> dict[str, Any]:
    """A header as a host sends it to a guest that says `once`."""
    return {
        "file": index,
        "size": len(_DATA),
        "version": version,
        "chunk_size": CHUNK_SIZE,
        "chunks": chunks,
    }


async def _carry(stream: swap.Conn, chunks: list[int]) -> None:
    for number in chunks:
        await stream.send_chunk(0, number, _DATA[number * CHUNK_SIZE : (number + 1) * CHUNK_SIZE])
        assert await stream.read(5) == {"ack": number, "file": 0}


@pytest.mark.integration
@_needs_psk
@pytest.mark.parametrize("answer", ["right", "wrong", "cannot", "another"])
async def test_a_guest_lands_a_file_only_when_the_pieces_digest_matches(
    tmp_path: Path, answer: str
) -> None:
    async with _guest_rig(tmp_path, {"a": _DATA}) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        await rig.take(control)
        stream, _ = await rig.stream()
        await stream.send(_versioned(0, "v" * 64, list(range(_COUNT))))
        assert await stream.read(5) == {"have": []}
        await _carry(stream, list(range(_COUNT)))

        assert await stream.read(5) == {"check": 0}
        answers: dict[str, dict[str, Any]] = {
            "right": {"pieces": _pieces_of(_DATA)},
            "wrong": {"pieces": _pieces_of(_DATA[::-1])},
            "cannot": {"cannot": True},
            "another": {"file": 1, "pieces": _pieces_of(_DATA)},
        }
        await stream.send({"file": 0, **answers[answer]})

        if answer == "another":
            with pytest.raises((asyncio.IncompleteReadError, ConnectionError)):
                await stream.read(5)
            assert rig.landed == {}
            return
        if answer == "right":
            assert await stream.read(5) == {"file_done": 0}
            await stream.send({"none": True})
            await _read_until(control, "done")
            assert (await rig.ended()).state == "done" and rig.landed == {"a": _DATA}
        else:
            assert await stream.read(5) == {"skip": 0}
            assert rig.landed == {} and not rig.staged("a").exists()


@pytest.mark.integration
@_needs_psk
async def test_a_guest_resumes_by_version_across_a_cut(
    tmp_path: Path,
) -> None:
    """The chunks a guest holds are kept by the sender's version of the file: a stream that comes
    back with the same version is told what the guest has, and the whole is still checked."""
    async with _guest_rig(tmp_path, {"a": _DATA}, retry_seconds=0.1) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        await rig.take(control)
        stream, _ = await rig.stream()
        await stream.send(_versioned(0, "v" * 64, [0, 1]))
        assert await stream.read(5) == {"have": []}
        await _carry(stream, [0, 1])
        assert await stream.read(5) == {"share_done": 0}
        manifest = await rig.sessions.store.manifest(rig.id, "a")
        assert manifest is not None and (manifest.version, manifest.digest) == ("v" * 64, None)
        assert manifest.done == (0, 1)

        # The tunnel goes; the guest dials again, and the host answers its hello.
        live = rig.live
        rig.lock_with[0] = os.urandom(32)
        assert rig.pinger is not None
        rig.pinger.cancel()
        control.close()
        await _until(lambda: live.cut_at)
        await _until(lambda: all(task.done() for task in live.streams.values()), 3)
        await _drained(rig.accepted)
        rig.lock_with[0] = rig.secret
        while True:
            conn = await rig.conn()
            with suppress(Exception):
                first = await conn.read(5)
                if isinstance(first, dict) and "device" in first:
                    break
        await conn.send(hello(rig.host, "host", rig.secret, os.urandom(32), session=_HOST_SESSION))
        await _until(lambda: live.cut_at is None)
        rig.pinger = asyncio.create_task(_keep_pinging(conn))

        again, _ = await rig.stream()
        await again.send(_versioned(0, "v" * 64, [2, 3]))
        assert await again.read(5) == {"have": [0, 1]}, "what it held, by the version"
        await _carry(again, [2, 3])
        assert await again.read(5) == {"check": 0}
        await again.send({"file": 0, "pieces": _pieces_of(_DATA)})
        assert await again.read(5) == {"file_done": 0}


@pytest.mark.integration
async def test_an_earlier_swaps_chunks_are_adopted_by_the_same_version_alone(
    tmp_path: Path,
) -> None:
    database = await _database(tmp_path / "s.sqlite3")
    try:
        store = SessionStore(database)
        peer = _device().id
        for session_id in ("01HONCESESSION00000000001", "01HONCESESSION00000000002"):
            await store.create(session_id, role="guest", started_at=1, started_by=VIEWER)
            await store.move(session_id, "connected", peer_device=peer)
        await store.put_manifest(
            "01HONCESESSION00000000001",
            "a",
            size=10,
            chunk_size=4,
            digest=None,
            staged_path=str(tmp_path / "staging" / "x.part"),
            now=1,
            version="v" * 64,
        )
        await store.mark_done("01HONCESESSION00000000001", "a", 0, 2)

        def adoptable(**named: str | None) -> Any:
            return store.adoptable(
                "01HONCESESSION00000000002",
                peer_device=peer,
                file_key="a",
                size=10,
                chunk_size=4,
                **{"digest": None, **named},
            )

        found = await adoptable(version="v" * 64)
        assert found is not None and found.done == (0,)
        assert await adoptable(version="w" * 64) is None, "the file changed since"
        assert await adoptable(digest="v" * 64) is None, "a version is never a digest"
        assert await adoptable() is None
    finally:
        await database.close()


@pytest.mark.integration
async def test_a_swap_table_from_before_gains_the_version_and_keeps_its_manifests(
    tmp_path: Path,
) -> None:
    from sift.slices.swap import schema

    database = await _database(tmp_path / "old.sqlite3")
    try:
        async with database.write() as connection:
            await connection.execute("ALTER TABLE swap_manifests DROP COLUMN version")
            await connection.execute(
                "INSERT INTO swap_sessions (id, role, state, started_at) VALUES"
                " ('01KZOLDSESSION00000000005', 'guest', 'transferring', 1)"
            )
            await connection.execute(
                "INSERT INTO swap_manifests (session_id, file_key, size, chunk_size, digest,"
                " updated_at) VALUES ('01KZOLDSESSION00000000005', 'k', 1, 1, 'd', 1)"
            )
            await schema.initialize(connection, on_disk=4)
            await schema.initialize(connection, on_disk=4)
        kept = await SessionStore(database).manifest("01KZOLDSESSION00000000005", "k")
        assert kept is not None and (kept.digest, kept.version) == ("d", None)
    finally:
        await database.close()
