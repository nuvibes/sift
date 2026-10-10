# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file carried in shares over several streams, on loopback: a real host and a real guest moving
one large file over eight streams, a stream lost part-way, a guest keeping one map of a file
across its streams, and a host cutting its shares afresh after a cut.

The session tests' own rigs: nothing leaves this machine.
"""

from __future__ import annotations

import asyncio
import os
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from sift.slices.swap import session as swap
from sift.slices.swap.handshake import WATCHDOG_SECONDS
from sift.slices.swap.models import Diff, Offer, OfferedFile, OfferScreen
from sift.slices.swap.session import Chunk, Conn, Taken, hello, read_hello
from sift.slices.swap.tests.test_session import (
    _Context,
    _database,
    _device,
    _Egress,
    _guest_rig,
    _header,
    _host_rig,
    _Hoster,
    _keep_pinging,
    _needs_psk,
    _Proxy,
    _sessions,
    _start_host,
    _until,
)
from sift.slices.swap.transfer import CHUNK_SIZE


async def _a_real_swap(
    tmp_path: Path, data: bytes, monkeypatch: pytest.MonkeyPatch, *, drop: int | None = None
) -> tuple[dict[str, bytes], dict[int, set[int]], Counter[int], Any, Any]:
    """A real host sending `data` as one file to a real guest. What landed, the connections that
    carried each chunk, how often each chunk went, and both rows. With `drop`, the stream carrying
    that chunk the first time is lost as it sends it."""
    library = tmp_path / "library"
    library.mkdir()
    (library / "big.bin").write_bytes(data)
    offer = Offer(
        files=[OfferedFile(key="big", size=len(data), identity="id", kind="video", title="clip")]
    )

    async def make_offer(*_args: object) -> Offer:
        return offer

    async def path_of(viewer: object, key: str) -> Path:
        return library / "big.bin"

    async def assess(arrived: Offer) -> tuple[OfferScreen, Callable[[Taken], Diff]]:
        return OfferScreen(layout="rows"), lambda taken: Diff(wanted=["big"], people={})

    landed: dict[str, bytes] = {}

    async def land(session: Any, received: Any, *, ctx: Any) -> None:
        landed[received.key] = await asyncio.to_thread(received.staged.read_bytes)

    carriers: dict[int, set[int]] = {}
    sent: Counter[int] = Counter()
    original = swap.Conn.send_chunk

    async def recording(self: Conn, file_index: int, chunk_index: int, chunk: bytes) -> None:
        sent[chunk_index] += 1
        if chunk_index == drop and sent[chunk_index] == 1:
            raise ConnectionResetError("dropped")
        carriers.setdefault(chunk_index, set()).add(id(self))
        await original(self, file_index, chunk_index, chunk)

    monkeypatch.setattr(swap.Conn, "send_chunk", recording)
    host_db = await _database(tmp_path / "host.sqlite3")
    guest_db = await _database(tmp_path / "guest.sqlite3")
    hoster = _Hoster()
    proxy = _Proxy(hoster)
    await proxy.start()
    # The product's own silence limit, not the session tests' short one: a stream a busy machine
    # keeps waiting is dropped under the short one and its chunks go again, which is the swap
    # working and not what these count.
    patient = {"watchdog_seconds": WATCHDOG_SECONDS}
    host = _sessions(
        host_db, tmp_path / "h", hoster=hoster, make_offer=make_offer, path_of=path_of, **patient
    )
    guest = _sessions(
        guest_db, tmp_path / "g", egress=_Egress(proxy.url), assess=assess, land=land, **patient
    )
    try:
        started = await _start_host(host)
        joined = await guest.join(
            viewer_id="viewer",
            token_text=started.token.text,
            dest_folder_id="folder",
            master_key=b"\x07" * 32,
        )
        host_task = asyncio.create_task(host.run(_Context(started.session_id, tmp_path / "hw")))  # type: ignore[arg-type]
        guest_task = asyncio.create_task(guest.run(_Context(joined, tmp_path / "gw")))  # type: ignore[arg-type]
        await _until(lambda: (facts := host.facts(started.session_id)) is not None and facts.code)
        await host.answer_code(started.session_id, True)
        await _until(lambda: (facts := guest.facts(joined)) is not None and facts.screen)
        await guest.take(joined, Taken())
        await asyncio.wait_for(asyncio.gather(host_task, guest_task), 60)
        return landed, carriers, sent, await host.row(started.session_id), await guest.row(joined)
    finally:
        if proxy.server is not None:
            proxy.server.close()
        await host_db.close()
        await guest_db.close()


@pytest.mark.integration
@_needs_psk
async def test_a_forty_chunk_file_over_eight_streams_lands_whole_and_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = os.urandom(CHUNK_SIZE * 39 + 1234)
    landed, carriers, sent, host_row, guest_row = await _a_real_swap(tmp_path, data, monkeypatch)

    assert landed == {"big": data}
    assert sorted(carriers) == list(range(40)) and set(sent.values()) == {1}, "each chunk once"
    streams = set().union(*carriers.values())
    assert len(streams) >= swap.STREAMS_START, "every stream carried a share of the one file"
    assert (host_row.state, host_row.sent_files, host_row.sent_bytes) == ("done", 1, len(data))
    assert (guest_row.state, guest_row.sent_bytes) == ("done", len(data))


@pytest.mark.integration
@_needs_psk
async def test_a_stream_lost_mid_file_leaves_the_others_carrying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = os.urandom(CHUNK_SIZE * 15 + 99)
    landed, carriers, sent, host_row, guest_row = await _a_real_swap(
        tmp_path, data, monkeypatch, drop=6
    )

    assert landed == {"big": data}
    assert sent[6] == 2, "the lost chunk went again, on another stream"
    # Which streams carry which chunks is the scheduler's; the lost one went on to another.
    assert len(set().union(*carriers.values())) >= 2
    assert (host_row.state, host_row.sent_files, host_row.sent_bytes) == ("done", 1, len(data))
    assert host_row.cut_off_at is None and guest_row.state == "done", "never cut off"


# --- the guest: one map of a file across its streams --------------------------------------------


@pytest.mark.integration
@_needs_psk
async def test_a_guest_keeps_one_map_of_a_file_across_its_streams(tmp_path: Path) -> None:
    data = os.urandom(CHUNK_SIZE + 5)
    async with _guest_rig(tmp_path, {"a": data}) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        await rig.take(control)
        one, _ = await rig.stream()
        two, _ = await rig.stream()

        await one.send({**_header(0, data), "chunks": [0]})
        assert await one.read(5) == {"have": []}
        await two.send({**_header(0, data), "chunks": [1]})
        assert await two.read(5) == {"have": []}
        await one.send_chunk(0, 0, data[:CHUNK_SIZE])
        assert await one.read(5) == {"ack": 0, "file": 0}
        assert await one.read(5) == {"share_done": 0}, "another stream holds the rest"
        await two.send_chunk(0, 1, data[CHUNK_SIZE:])
        assert await two.read(5) == {"ack": 1, "file": 0}
        assert await two.read(5) == {"file_done": 0}, "the last chunk's stream checks the file"
        # A share of a file already verified is confirmed, not received again.
        await one.send({**_header(0, data), "chunks": []})
        assert await one.read(5) == {"file_done": 0}

        row = await rig.ended()
        assert row.state == "done" and rig.landed == {"a": data}
        assert row.sent_bytes == len(data)


# --- the host: shares cut afresh after a cut --------------------------------------------------


async def _striped_hello(rig: Any, me: Any) -> Conn:
    secret = rig.started.token.secret
    conn: Conn = await rig.dial()
    await conn.send(hello(me, "guest", secret, os.urandom(32), striped=1))
    answer = await conn.read(5)
    assert isinstance(answer, dict)
    rig.session = read_hello(answer, "host", secret).session
    rig.control = conn
    rig.pinger = asyncio.create_task(_keep_pinging(conn))
    return conn


@pytest.mark.integration
@_needs_psk
async def test_a_cut_off_and_rejoin_resumes_the_shares_from_what_the_guest_has(
    tmp_path: Path,
) -> None:
    data = os.urandom(CHUNK_SIZE * 2 + 10)
    async with _host_rig(tmp_path, {"a": data}, retry_seconds=0.1) as rig:
        me = _device()
        control = await _striped_hello(rig, me)
        await _until(rig.code)
        await rig.transferring("a")

        one = await rig.stream()
        first = await one.read(5)
        assert isinstance(first, dict) and first["chunks"] == [0], "a share, not the file"
        two = await rig.stream()
        second = await two.read(5)
        assert isinstance(second, dict) and second["chunks"] == [1]
        await one.send({"have": []})
        assert isinstance(await one.read(5), Chunk)
        await one.send({"ack": 0, "file": 0})
        await one.send({"share_done": 0})
        third = await one.read(5)
        assert isinstance(third, dict) and third["chunks"] == [2], "the next share on that stream"
        await two.send({"have": []})
        assert isinstance(await two.read(5), Chunk)  # written, and its ack lost with the cut

        assert rig.pinger is not None
        rig.pinger.cancel()
        for conn in (control, one, two):
            conn.close()
        await _until(lambda: rig.live.cut_at)
        await _striped_hello(rig, me)
        await _until(lambda: rig.live.cut_at is None)

        three = await rig.stream()
        again = await three.read(5)
        assert isinstance(again, dict) and again["chunks"] == [1], "cut again from what is left"
        await three.send({"have": [0, 1]})
        await three.send({"share_done": 0})
        last = await three.read(5)
        assert isinstance(last, dict) and last["chunks"] == [2]
        await three.send({"have": [0, 1]})
        chunk = await three.read(5)
        assert isinstance(chunk, Chunk) and chunk.index == 2, "only what the guest lacked"
        await three.send({"ack": 2, "file": 0})
        await three.send({"file_done": 0})
        assert await three.read(5) == {"none": True}

        row = await rig.finish()
        assert (row.state, row.sent_files, row.sent_bytes) == ("done", 1, len(data))
        assert rig.stripped() == []


@pytest.mark.integration
@_needs_psk
async def test_a_file_turned_down_on_two_streams_together_is_failed_once(tmp_path: Path) -> None:
    data = os.urandom(CHUNK_SIZE + 10)
    async with _host_rig(tmp_path, {"a": data}) as rig:
        await _striped_hello(rig, _device())
        await _until(rig.code)
        await rig.transferring("a")
        one, two = await rig.stream(), await rig.stream()
        for stream, share in ((one, [0]), (two, [1])):
            header = await stream.read(5)
            assert isinstance(header, dict) and header["chunks"] == share

        await one.send({"skip": 0})
        assert await one.read(5) == {"none": True}
        await two.send({"skip": 0})
        assert await two.read(5) == {"none": True}

        live = rig.live
        assert (live.done_files, live.failed_files) == (set(), {0})
        row = await rig.finish()
        assert (row.sent_files, row.state) == (0, "done")
        assert rig.stripped() == []


@pytest.mark.integration
@_needs_psk
async def test_a_file_another_stream_is_still_checking_is_asked_about_again_after_a_pause(
    tmp_path: Path,
) -> None:
    """Every chunk landed and the file not yet said done: the stream asks about it with a share
    of nothing, and asked again while it is still being checked, it waits before the next ask
    rather than asking in a loop."""
    data = os.urandom(100)
    async with _host_rig(tmp_path, {"a": data}) as rig:
        await _striped_hello(rig, _device())
        await _until(rig.code)
        await rig.transferring("a")
        stream = await rig.stream()
        header = await stream.read(5)
        assert isinstance(header, dict) and header["chunks"] == [0]
        await stream.send({"have": []})
        assert isinstance(await stream.read(5), Chunk)
        await stream.send({"ack": 0, "file": 0})
        await stream.send({"share_done": 0})

        asked = await stream.read(5)
        assert isinstance(asked, dict) and asked["chunks"] == []
        await stream.send({"have": [0]})
        await stream.send({"share_done": 0})
        loop = asyncio.get_running_loop()
        said = loop.time()
        again = await stream.read(5)
        assert isinstance(again, dict) and again["chunks"] == []
        assert loop.time() - said >= 0.4, "a pause before asking again"
        await stream.send({"have": [0]})
        await stream.send({"file_done": 0})
        assert await stream.read(5) == {"none": True}

        row = await rig.finish()
        assert (row.state, row.sent_files) == ("done", 1)
