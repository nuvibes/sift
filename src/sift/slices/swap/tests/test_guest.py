# SPDX-License-Identifier: AGPL-3.0-or-later
"""The guest of a swap against a host written by hand to say the wrong thing, on loopback."""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from typing import Any

import pytest
from blake3 import blake3
from structlog.testing import capture_logs

import sift.slices.swap.schema
import sift.slices.workbench.schema  # noqa: F401 (the ledger a swap's start and end go on)
from sift.kernel.db import Database
from sift.slices.swap import guest as guest_side
from sift.slices.swap import receiving as receiving_side
from sift.slices.swap import session as swap
from sift.slices.swap import transfer
from sift.slices.swap.models import Diff
from sift.slices.swap.session import (
    REDIAL_LIMIT,
    Ladder,
    Taken,
    hello,
)
from sift.slices.swap.store import SessionRow
from sift.slices.swap.tests.test_session import (
    _ADDRESS,
    _PORT,
    VIEWER,
    _bad_chunk,
    _device,
    _dropped,
    _Egress,
    _guest_rig,
    _GuestRig,
    _header,
    _needs_psk,
    _read_until,
    _refusing_proxy,
    _send_whole,
    _sessions,
    _until,
)
from sift.slices.swap.transfer import CHUNK_SIZE
from sift.testing.logs import uncached_log


async def _cut_row(rig: _GuestRig) -> SessionRow:
    """The row once the cut is written. `cut` records the cut in memory first and writes the row
    after closing the connections, so a read right after `cut_at` lands can be a step early."""
    await _until(lambda: rig.live.cut_at)
    row = await rig.sessions.row(rig.id)
    began = asyncio.get_running_loop().time()
    while row is None or row.cut_off_at is None:
        if asyncio.get_running_loop().time() - began > 10.0:
            raise AssertionError("the cut was never written")
        await asyncio.sleep(0.02)
        row = await rig.sessions.row(rig.id)
    return row


@pytest.mark.integration
@_needs_psk
@pytest.mark.parametrize("way_out", ["no tunnel", "a tunnel that carries nothing"])
async def test_a_guest_with_no_way_out_to_the_host_ends_as_lost(
    tmp_path: Path, way_out: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The log line says why, in the words the dial failed with: a session that ends as lost
    ten seconds after it joined is otherwise a blank."""
    uncached_log(monkeypatch, swap)
    refusing = await _refusing_proxy()
    port = refusing.sockets[0].getsockname()[1]
    egress = _Egress(None if way_out == "no tunnel" else f"http://127.0.0.1:{port}")
    try:
        with capture_logs() as written:
            async with _guest_rig(tmp_path, egress=egress) as rig:
                row = await rig.ended()

                assert (row.state, row.end_reason) == ("failed", swap.LOST)
                assert rig.accepted.empty(), "nothing reached the host"
    finally:
        refusing.close()
    if way_out == "no tunnel":
        assert [line["event"] for line in written if line["event"] == "swap.no_tunnel"]
    else:
        reasons = [line["why"] for line in written if line["event"] == "swap.could_not_reach"]
        assert len(reasons) == 1 and reasons[0], "the failed dial is logged once, with its reason"
    joined = [line for line in written if line["event"] == "swap.joined"]
    assert joined and joined[0]["swap_row"] == rig.id, "the session id is beside the short one"


@pytest.mark.integration
@_needs_psk
@pytest.mark.parametrize("answer", ["a chunk", "another session's hello", "a close"])
async def test_a_host_that_does_not_answer_the_hello_as_this_sessions_host_ends_it_as_lost(
    tmp_path: Path, answer: str
) -> None:
    async with _guest_rig(tmp_path) as rig:
        conn = await rig.conn()
        await conn.read(5)

        if answer == "a chunk":
            await conn.send_chunk(0, 0, b"a chunk for a hello")
        elif answer == "another session's hello":
            await conn.send(hello(rig.host, "host", os.urandom(32), os.urandom(32)))
        else:
            conn.close()

        row = await rig.ended()
        assert (row.state, row.end_reason) == ("failed", swap.LOST)


@pytest.mark.integration
@_needs_psk
async def test_a_token_the_host_has_taken_already_ends_the_guest_as_used(tmp_path: Path) -> None:
    async with _guest_rig(tmp_path) as rig:
        conn = await rig.conn()
        await conn.read(5)

        await conn.send({"refused": "used"})

        row = await rig.ended()
        # Its own reason and words, never "lost": the host is there and said no.
        assert (row.state, row.end_reason) == ("ended", swap.USED)


@pytest.mark.integration
@_needs_psk
async def test_a_host_answering_with_another_devices_key_ends_it_as_the_wrong_device(
    tmp_path: Path,
) -> None:
    async with _guest_rig(tmp_path) as rig:
        conn = await rig.conn()

        await rig.answer_hello(conn, device=_device())

        row = await rig.ended()
        assert (row.state, row.end_reason) == ("ended", swap.WRONG_DEVICE)
        assert row.offered_files == 0


@pytest.mark.integration
@_needs_psk
@pytest.mark.parametrize(
    ("what", "state", "reason"),
    [("an end", "ended", swap.REFUSED), ("a close", "connected", swap.LOST)],
)
async def test_a_host_that_refuses_at_the_code_ends_the_guest_and_one_gone_cuts_it_off(
    tmp_path: Path, what: str, state: str, reason: str
) -> None:
    async with _guest_rig(tmp_path) as rig:
        control = await rig.connected()

        if what == "an end":
            await control.send({"end": "refused"})
            row = await rig.ended()
            assert (row.state, row.end_reason) == (state, reason)
            return
        assert rig.pinger is not None
        rig.pinger.cancel()
        control.close()

        # Gone away is a tunnel dropping: cut off at the code, and dialling again.
        cut = await _cut_row(rig)
        assert (cut.state, cut.live) == (state, True)


@pytest.mark.integration
@_needs_psk
async def test_they_match_on_the_guest_releases_nothing_and_an_end_there_tells_the_host(
    tmp_path: Path,
) -> None:
    async with _guest_rig(tmp_path) as rig:
        control = await rig.connected()

        await rig.sessions.answer_code(rig.id, True)
        row = await rig.sessions.row(rig.id)
        assert row is not None and row.state == "connected", "only the host's press releases"
        await rig.sessions.end(rig.id)

        assert (await _read_until(control, "end"))["end"] == "ended"
        row = await rig.ended()
        assert (row.state, row.end_reason) == ("ended", swap.ENDED_BY_YOU)


@pytest.mark.integration
@_needs_psk
async def test_an_end_pressed_at_the_offer_screen_ends_the_guest_and_tells_the_host(
    tmp_path: Path,
) -> None:
    async with _guest_rig(tmp_path) as rig:
        control = await rig.connected()
        await rig.offer_to(control)

        await rig.sessions.end(rig.id)

        assert (await _read_until(control, "end"))["end"] == "ended"
        row = await rig.ended()
        assert (row.state, row.end_reason, row.offered_files) == ("ended", swap.ENDED_BY_YOU, 1)


@pytest.mark.integration
@_needs_psk
async def test_an_offer_that_does_not_read_as_one_ends_the_guest_as_lost(tmp_path: Path) -> None:
    async with _guest_rig(tmp_path) as rig:
        control = await rig.connected()

        await control.send({"offer": {"files": "all of them"}})

        row = await rig.ended()
        assert (row.state, row.end_reason, row.offered_files) == ("failed", swap.LOST, 0)


@pytest.mark.integration
@_needs_psk
async def test_the_hosts_port_moving_sends_the_next_dial_there_and_a_bad_move_is_passed_over(
    tmp_path: Path,
) -> None:
    async with _guest_rig(tmp_path) as rig:
        control = await rig.connected()
        live = rig.live
        # Where no token could point, and a port that is not one: neither moves the next dial. A
        # message the guest has no use for is passed over the same way.
        await control.send({"what": "a message from a later Sift"})
        await control.send({"moved": {"address": "10.0.0.7", "port": _PORT + 3}})
        await control.send({"moved": {"address": _ADDRESS, "port": "the next one"}})
        await rig.offer_to(control)
        assert (live.address, live.port) == (_ADDRESS, _PORT)

        await control.send({"moved": {"address": _ADDRESS, "port": _PORT + 7}})
        await _until(lambda: live.port == _PORT + 7)
        await rig.take(control)
        stream, _ = await rig.stream()
        await stream.send({"file": 0, "cannot": True})

        row = await rig.ended()
        assert row.state == "done"
        moved = f"CONNECT {_ADDRESS}:{_PORT + 7} HTTP/1.1".encode()
        assert rig.proxy.targets[0] == f"CONNECT {_ADDRESS}:{_PORT} HTTP/1.1".encode()
        assert rig.proxy.targets[1:] and set(rig.proxy.targets[1:]) == {moved}


@pytest.mark.integration
@_needs_psk
async def test_an_answer_naming_a_file_the_offer_never_had_ends_the_guest_as_lost(
    tmp_path: Path,
) -> None:
    def ghost(taken: Taken) -> Diff:
        return Diff(wanted=["never-offered"], people={})

    async with _guest_rig(tmp_path, answer=ghost) as rig:
        control = await rig.connected()
        await rig.offer_to(control)

        await rig.sessions.take(rig.id, Taken())

        row = await rig.ended()
        assert (row.state, row.end_reason, row.wanted_files) == ("failed", swap.LOST, 0)


@pytest.mark.integration
@_needs_psk
async def test_the_answer_sent_names_none_of_this_library_s_people(tmp_path: Path) -> None:
    """The guest keeps its matches for the landing and the host reads none of them: the diff frame
    on the wire is the answer as `Diff.for_the_other_side` makes it."""

    def matched(taken: Taken) -> Diff:
        return Diff(wanted=["a"], people={0: "p-marked", 1: "p-plain"})

    async with _guest_rig(tmp_path, answer=matched) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        sent = await rig.take(control)

        assert sent["wanted"] == ["a"]
        assert not sent.get("people")
        assert "p-marked" not in str(sent) and "p-plain" not in str(sent)


@pytest.mark.integration
@_needs_psk
async def test_a_file_nobody_asked_for_is_turned_down_and_one_the_host_cannot_send_is_failed(
    tmp_path: Path,
) -> None:
    def only_a(taken: Taken) -> Diff:
        return Diff(wanted=["a"], people={})

    async with _guest_rig(tmp_path, {"a": b"wanted", "b": b"not asked for"}, answer=only_a) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        assert (await rig.take(control))["wanted"] == ["a"]
        live = rig.live
        stream, _ = await rig.stream()

        await stream.send(_header(1, b"not asked for"))
        assert await stream.read(5) == {"skip": 1}
        await stream.send({"file": 0, "cannot": True})

        row = await rig.ended()
        assert (row.state, row.end_reason) == ("done", swap.REASON_DONE)
        assert (live.unwanted, live.failed_files, rig.landed) == (1, {0}, {})


@pytest.mark.integration
@_needs_psk
async def test_a_chunk_sent_twice_is_written_once_and_a_verified_file_offered_again_is_confirmed(
    tmp_path: Path,
) -> None:
    first = os.urandom(CHUNK_SIZE + 5)
    async with _guest_rig(tmp_path, {"a": first, "b": b"the host lost this one"}) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        await rig.take(control)
        one, _ = await rig.stream()
        two, _ = await rig.stream()

        await one.send(_header(0, first))
        assert await one.read(5) == {"have": []}
        for _ in range(2):
            await one.send_chunk(0, 0, first[:CHUNK_SIZE])
            assert await one.read(5) == {"ack": 0, "file": 0}
        await one.send_chunk(0, 1, first[CHUNK_SIZE:])
        assert await one.read(5) == {"ack": 1, "file": 0}
        assert await one.read(5) == {"file_done": 0}
        # Offered again on another stream, as after a lost word: confirmed, not received again.
        await two.send(_header(0, first))
        assert await two.read(5) == {"file_done": 0}
        await two.send({"file": 1, "cannot": True})

        row = await rig.ended()
        assert row.state == "done"
        assert rig.landed == {"a": first}
        assert row.sent_bytes == len(first), "the chunk sent twice was counted once"


@pytest.mark.integration
@_needs_psk
@pytest.mark.parametrize(
    "lie",
    [
        "a chunk before its file",
        "a file with no index",
        "a file larger than offered",
        "a chunk of another file",
    ],
)
async def test_a_stream_that_says_what_a_host_does_not_is_dropped_and_dialled_again(
    tmp_path: Path, lie: str
) -> None:
    data = b"a small file"
    async with _guest_rig(tmp_path, {"a": data}) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        await rig.take(control)
        stream, number = await rig.stream()

        if lie == "a chunk before its file":
            await stream.send_chunk(0, 0, data)
        elif lie == "a file with no index":
            await stream.send({"file": "the first"})
        elif lie == "a file larger than offered":
            await stream.send({**_header(0, data), "size": 10**12})
        else:
            await stream.send(_header(0, data))
            assert await stream.read(5) == {"have": []}
            await stream.send_chunk(1, 0, data)

        await _dropped(stream)
        again, _ = await rig.stream(number)
        await _send_whole(again, 0, data)
        row = await rig.ended()
        assert row.state == "done" and rig.landed == {"a": data}


@pytest.mark.integration
@_needs_psk
@pytest.mark.parametrize("fault", ["a chunk past its retries", "a whole file that does not match"])
async def test_a_file_that_fails_its_check_is_skipped_and_forgotten(
    tmp_path: Path, fault: str
) -> None:
    data = b"a small file"
    async with _guest_rig(tmp_path, {"a": data}) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        await rig.take(control)
        stream, _ = await rig.stream()

        if fault == "a chunk past its retries":
            await stream.send(_header(0, data))
            assert await stream.read(5) == {"have": []}
            for _ in range(transfer.MAX_RETRIES):
                stream.writer.write(_bad_chunk(0, 0, data))
                assert await stream.read(5) == {"again": 0, "file": 0}
            stream.writer.write(_bad_chunk(0, 0, data))
        else:
            await stream.send(_header(0, data, digest="0" * 64))
            assert await stream.read(5) == {"have": []}
            await stream.send_chunk(0, 0, data)
            assert await stream.read(5) == {"ack": 0, "file": 0}
        assert await stream.read(5) == {"skip": 0}

        row = await rig.ended()
        assert row.state == "done" and rig.landed == {}
        assert await rig.sessions.store.manifest(rig.id, "a") is None
        assert not rig.staged("a").exists()


@pytest.mark.integration
@_needs_psk
async def test_a_file_an_earlier_swap_with_the_same_host_left_half_done_resumes_where_it_stopped(
    tmp_path: Path,
) -> None:
    whole = os.urandom(CHUNK_SIZE + 5)
    async with _guest_rig(tmp_path, {"a": whole}, run=False) as rig:
        store, earlier = rig.sessions.store, "01HEARLIERSWAP00000000001"
        await store.create(
            earlier, role="guest", started_at=1, started_by=VIEWER, peer_device=rig.host.id
        )
        await store.end(earlier, state="failed", reason=swap.LOST, now=2)
        staged = transfer.staged_path(rig.sessions.staging, earlier, "a")
        await asyncio.to_thread(transfer.write_chunk, staged, 0, whole[:CHUNK_SIZE])
        now = rig.sessions.now()
        await store.put_manifest(
            earlier,
            "a",
            size=len(whole),
            chunk_size=CHUNK_SIZE,
            digest=blake3(whole).hexdigest(),
            staged_path=str(staged),
            now=now,
        )
        await store.mark_done(earlier, "a", 0, now)
        rig.task = asyncio.create_task(rig.sessions.run(rig.context))  # type: ignore[arg-type]
        control = await rig.connected()
        await rig.offer_to(control)
        await rig.take(control)
        stream, _ = await rig.stream()

        await stream.send(_header(0, whole))

        assert await stream.read(5) == {"have": [0]}
        await stream.send_chunk(0, 1, whole[CHUNK_SIZE:])
        assert await stream.read(5) == {"ack": 1, "file": 0}
        assert await stream.read(5) == {"file_done": 0}
        row = await rig.ended()
        assert rig.landed == {"a": whole}
        assert row.sent_bytes == 5, "only what this swap moved"


@pytest.mark.integration
@_needs_psk
async def test_a_file_that_will_not_land_is_counted_failed_and_the_swap_finishes(
    tmp_path: Path,
) -> None:
    async def refuse(session: Any, received: Any, *, ctx: Any) -> None:
        raise RuntimeError("the library's disk is full")

    data = b"a small file"
    async with _guest_rig(tmp_path, {"a": data}, land=refuse) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        await rig.take(control)
        live = rig.live
        stream, _ = await rig.stream()

        await _send_whole(stream, 0, data)

        row = await rig.ended()
        assert (row.state, row.end_reason) == ("done", swap.REASON_DONE)
        assert (live.done_files, live.failed_files) == (set(), {0})
        assert not rig.staged("a").exists()


@pytest.mark.integration
@_needs_psk
async def test_an_end_while_a_file_lands_stops_the_landing_and_removes_what_was_staged(
    tmp_path: Path,
) -> None:
    landing = asyncio.Event()

    async def forever(session: Any, received: Any, *, ctx: Any) -> None:
        landing.set()
        await asyncio.Event().wait()

    data = b"a small file"
    async with _guest_rig(tmp_path, {"a": data}, land=forever) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        await rig.take(control)
        live = rig.live
        stream, number = await rig.stream()
        await _send_whole(stream, 0, data)
        await asyncio.wait_for(landing.wait(), 5)
        # Every file is in: a stream the host drops now is not dialled again.
        stream.close()
        await _until(live.streams[number].done)

        await rig.sessions.end(rig.id)

        row = await rig.ended()
        assert (row.state, row.end_reason) == ("ended", swap.ENDED_BY_YOU)
        assert not rig.staged("a").exists()
        assert await rig.sessions.store.manifest(rig.id, "a") is None


@pytest.mark.integration
@_needs_psk
@pytest.mark.parametrize("how", ["no stream locks", "every stream drops"])
async def test_streams_that_cannot_be_kept_up_cut_the_swap_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    monkeypatch.setattr(guest_side, "REDIAL_LIMIT", 2)
    async with _guest_rig(tmp_path) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        if how == "no stream locks":
            rig.lock_with[0] = os.urandom(32)

        async def drop_every_stream() -> None:
            while True:
                conn, _ = await rig.stream()
                conn.close()

        dropper = asyncio.create_task(drop_every_stream())
        try:
            await rig.take(control)
            row = await _cut_row(rig)
        finally:
            dropper.cancel()
        assert (row.state, row.live) == ("transferring", True)


@pytest.mark.integration
@_needs_psk
async def test_a_rising_rate_opens_another_stream_while_files_are_still_coming(
    tmp_path: Path,
) -> None:
    data = os.urandom(CHUNK_SIZE + 5)
    async with _guest_rig(tmp_path, {"a": data}, rate_window=0.1) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        # The ladder's own climb is proven above; here, any window reads as a rise.
        rig.live.ladder = Ladder(best=-1.0)
        await rig.take(control)

        fifth, number = await rig.stream(swap.STREAMS_START)

        assert number == swap.STREAMS_START
        await _send_whole(fifth, 0, data)
        row = await rig.ended()
        assert row.state == "done" and rig.landed == {"a": data}


@pytest.mark.integration
@_needs_psk
async def test_a_rising_rate_opens_no_stream_once_every_file_is_in(tmp_path: Path) -> None:
    release = asyncio.Event()

    async def held(session: Any, received: Any, *, ctx: Any) -> None:
        await release.wait()

    data = b"a small file"
    async with _guest_rig(tmp_path, {"a": data}, land=held, rate_window=0.2) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        await rig.take(control)
        live = rig.live
        stream, _ = await rig.stream()
        await _send_whole(stream, 0, data)

        live.ladder = Ladder(best=-1.0)
        await _until(lambda: live.ladder.best != -1.0)

        assert live.ladder.streams == swap.STREAMS_START + 1
        assert len(live.streams) == swap.STREAMS_START, "nothing is left to open one for"
        release.set()
        row = await rig.ended()
        assert row.state == "done"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("part", "words"),
    [
        ("make_offer", "the offer"),
        ("assess", "the diff"),
        ("land", "the landing"),
        ("path_of", "the host's files"),
        ("weigh", "the weighing"),
    ],
)
async def test_a_part_the_sessions_were_not_given_fails_loudly_when_it_is_called(
    tmp_path: Path, part: str, words: str
) -> None:
    sessions = _sessions(Database(tmp_path / "never-opened.sqlite3"), tmp_path)

    with pytest.raises(RuntimeError, match=f"^{words} is not wired$"):
        await getattr(sessions, part)("anything")


@pytest.mark.integration
@_needs_psk
async def test_a_stream_that_finds_the_swap_ended_between_two_files_stops_without_a_word(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host ends a stream by saying "none". A swap ended from this side (lost, or every file
    resolved) ends it too: the stream stops before asking for another header, and the dial loop
    stops with it rather than dialling again."""
    async with _guest_rig(tmp_path, {"a": b"a" * 200}) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        await rig.take(control)
        live = rig.live
        real_receive = live.receive

        async def ending(conn: swap.Conn, **how: Any) -> bool:
            live.ended.set()
            return await real_receive(conn, **how)

        monkeypatch.setattr(live, "receive", ending)
        _stream, number = await rig.stream()
        await asyncio.wait_for(live.streams[number], timeout=5)
        assert live.conns == set()


async def test_a_stream_that_drops_after_each_file_it_received_keeps_going_past_the_redial_limit(
    tmp_path: Path,
) -> None:
    """The failures counted are dials that failed IN A ROW. A stream that delivers a file and then
    drops has not failed in a row: it worked, then the line went. Counted without the reset, the
    fifth such drop over a long swap would end the whole swap as lost with every file received."""
    files = {name: name.encode() * 200 for name in "abcdefg"}
    async with _guest_rig(tmp_path, files) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        await rig.take(control)
        stream, number = await rig.stream()
        for index in range(REDIAL_LIMIT + 1):
            await _send_whole(stream, index, files[chr(ord("a") + index)])
            # The line goes from the host's side, after a file landed: the guest dials again.
            stream.close()
            stream, _ = await rig.stream(number)
        await _send_whole(stream, REDIAL_LIMIT + 1, files["g"])
        row = await rig.ended()
        assert row.state == "done", row.state
        assert rig.landed == files


# --- the guest's own files, in a swap both ways ----------------------------------------------------


async def test_each_window_opens_streams_only_for_a_direction_whose_files_still_move(
    tmp_path: Path,
) -> None:
    """Sending back while nothing has come in yet: the window opens streams up this side's upload
    and none for files nobody is sending it, and none either way once its files are settled."""
    async with _guest_rig(tmp_path, run=False) as rig:
        live = rig.live
        opened: list[tuple[int, bool]] = []
        live._open_stream = lambda number, *, back=False: opened.append((number, back))  # type: ignore[method-assign]
        back = guest_side._BackSending(live)
        live.other_way = back
        back.wanted = [0]
        back.transferring = True

        await live.on_window()

        assert opened == [(number, True) for number in range(swap.STREAMS_START)]
        opened.clear()
        back.done_files = {0}
        await live.on_window()
        assert opened == [], "every file of this side's is settled"


async def test_this_sides_offer_opens_streams_only_when_the_host_wanted_something(
    tmp_path: Path,
) -> None:
    async with _guest_rig(tmp_path, run=False) as rig:
        live = rig.live
        opened: list[tuple[int, bool]] = []
        live._open_stream = lambda number, *, back=False: opened.append((number, back))  # type: ignore[method-assign]
        back = guest_side._BackSending(live)

        back._sending_started()
        assert opened == [], "the host wanted none of this side's files"
        back.wanted = [0]
        back._sending_started()
        assert opened == [(number, True) for number in range(swap.STREAMS_START)]


async def test_this_sides_they_match_says_what_it_offers_once_and_an_end_first_offers_nothing(
    tmp_path: Path,
) -> None:
    from sift.kernel.access import Role, Viewer
    from sift.slices.swap.models import Chosen

    async with _guest_rig(tmp_path, run=False) as rig:
        live = rig.live
        back = guest_side._BackSending(live)
        admin = Viewer(id="admin", role=Role.ADMIN)
        first = [Chosen(kind="person", id="p1")]

        back.choose(admin, first, False)
        back.choose(admin, [Chosen(kind="tag", id="t1")], True)

        assert (back.chosen, back.share_boxes) == (first, False), "the first They match stands"

        unanswered = guest_side._BackSending(live)
        await rig.sessions.end(rig.id)
        assert await unanswered.match_then_send() is False
        assert unanswered.offer is None


# --- receiving: what lands beside the files, and a file carried on several streams -----------------


@pytest.mark.integration
@_needs_psk
async def test_people_whose_fingerprints_will_not_land_cost_those_fingerprints_and_never_the_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("the face feature is down")

    uncached_log(monkeypatch, receiving_side)
    data = b"a small file"
    with capture_logs() as written:
        async with _guest_rig(tmp_path, {"a": data}, land_fingerprints=failing) as rig:
            control = await rig.connected()
            await rig.offer_to(control)
            await rig.take(control)
            stream, _ = await rig.stream()
            await _send_whole(stream, 0, data)
            row = await rig.ended()
    assert (row.state, rig.landed) == ("done", {"a": data})
    said = [line for line in written if line["event"] == "swap.fingerprints_not_landed"]
    assert [line["error"] for line in said] == ["RuntimeError"], "the type alone"


@pytest.mark.integration
@_needs_psk
async def test_cancel_while_fingerprints_land_ends_the_swap_before_any_file_is_asked_for(
    tmp_path: Path,
) -> None:
    landing = asyncio.Event()

    async def held(*_args: Any, **_kwargs: Any) -> None:
        landing.set()
        await asyncio.Event().wait()

    async with _guest_rig(tmp_path, land_fingerprints=held) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        await rig.take(control)
        live = rig.live
        await asyncio.wait_for(landing.wait(), 5)
        assert rig.task is not None

        rig.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await rig.task

        assert not live.transferring and live.streams == {}
        row = await rig.sessions.row(rig.id)
        assert row is not None and row.end_reason == swap.ENDED_BY_YOU


@pytest.mark.integration
@_needs_psk
async def test_a_files_map_a_stopped_stream_left_in_this_session_is_taken_up_by_the_next(
    tmp_path: Path,
) -> None:
    """A cut stops a stream wherever it is, between writing the file's map and taking the file up
    included: the next stream to carry the file resumes from the map, not from nothing."""
    whole = os.urandom(CHUNK_SIZE + 5)
    async with _guest_rig(tmp_path, {"a": whole}) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        await rig.take(control)
        stream, _ = await rig.stream()
        store, staged, now = rig.sessions.store, rig.staged("a"), rig.sessions.now()
        await asyncio.to_thread(transfer.write_chunk, staged, 0, whole[:CHUNK_SIZE])
        await store.put_manifest(
            rig.id,
            "a",
            size=len(whole),
            chunk_size=CHUNK_SIZE,
            digest=blake3(whole).hexdigest(),
            staged_path=str(staged),
            now=now,
        )
        await store.mark_done(rig.id, "a", 0, now)

        await stream.send(_header(0, whole))

        assert await stream.read(5) == {"have": [0]}
        await stream.send_chunk(0, 1, whole[CHUNK_SIZE:])
        assert await stream.read(5) == {"ack": 1, "file": 0}
        assert await stream.read(5) == {"file_done": 0}
        await rig.ended()
        assert rig.landed == {"a": whole}


#: A second file offered, so a swap whose first file fails is not over immediately.
_ANOTHER = b"another file"


async def _two_shares(rig: Any, data: bytes) -> tuple[swap.Conn, swap.Conn]:
    """Two streams, each opened on its half of a two-chunk file."""
    control = await rig.connected()
    await rig.offer_to(control)
    await rig.take(control)
    one, _ = await rig.stream()
    two, _ = await rig.stream()
    for stream, share in ((one, [0]), (two, [1])):
        await stream.send({**_header(0, data), "chunks": share})
        assert await stream.read(5) == {"have": []}
    return one, two


@pytest.mark.integration
@_needs_psk
async def test_a_second_header_for_a_file_that_disagrees_with_the_first_drops_that_stream(
    tmp_path: Path,
) -> None:
    data = os.urandom(CHUNK_SIZE + 5)
    async with _guest_rig(tmp_path, {"a": data}) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        await rig.take(control)
        one, _ = await rig.stream()
        two, _ = await rig.stream()
        await one.send({**_header(0, data), "chunks": [0]})
        assert await one.read(5) == {"have": []}

        await two.send({**_header(0, data, digest="0" * 64), "chunks": [1]})

        await _dropped(two)
        await one.send_chunk(0, 0, data[:CHUNK_SIZE])
        assert await one.read(5) == {"ack": 0, "file": 0}, "the file's first header stands"


@pytest.mark.integration
@_needs_psk
async def test_a_file_failed_on_another_stream_is_turned_down_on_this_one_at_its_next_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(receiving_side, "MAX_RETRIES", 0)
    data = os.urandom(CHUNK_SIZE + 5)
    async with _guest_rig(tmp_path, {"a": data, "b": _ANOTHER}) as rig:
        one, two = await _two_shares(rig, data)

        two.writer.write(_bad_chunk(0, 1, data[CHUNK_SIZE:]))
        assert await two.read(5) == {"skip": 0}
        await one.send_chunk(0, 0, data[:CHUNK_SIZE])

        assert await one.read(5) == {"skip": 0}
        assert rig.live.failed_files == {0} and not rig.staged("a").exists()


@pytest.mark.integration
@_needs_psk
async def test_a_file_failed_on_another_stream_while_this_one_wrote_keeps_nothing_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(receiving_side, "MAX_RETRIES", 0)
    entered, release = threading.Event(), threading.Event()
    real = transfer.write_chunk

    def held(path: Path, index: int, chunk: bytes) -> None:
        if index == 0:
            entered.set()
            release.wait(5)
        real(path, index, chunk)

    monkeypatch.setattr(transfer, "write_chunk", held)
    data = os.urandom(CHUNK_SIZE + 5)
    async with _guest_rig(tmp_path, {"a": data, "b": _ANOTHER}) as rig:
        one, two = await _two_shares(rig, data)
        await one.send_chunk(0, 0, data[:CHUNK_SIZE])
        assert await asyncio.to_thread(entered.wait, 5)

        two.writer.write(_bad_chunk(0, 1, data[CHUNK_SIZE:]))
        assert await two.read(5) == {"skip": 0}
        release.set()

        assert await one.read(5) == {"skip": 0}
        assert not rig.staged("a").exists(), "the chunk written after the failure went with it"


@pytest.mark.integration
@_needs_psk
async def test_a_file_failed_on_another_stream_while_this_share_was_recorded_is_turned_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The share's last chunk is in, and the file failed elsewhere meanwhile: the share ends with
    the file turned down, not with a word that it is done."""
    monkeypatch.setattr(receiving_side, "MAX_RETRIES", 0)
    data = os.urandom(CHUNK_SIZE + 5)
    async with _guest_rig(tmp_path, {"a": data, "b": _ANOTHER}) as rig:
        store = rig.sessions.store
        recording, release = asyncio.Event(), asyncio.Event()
        real = store.mark_done

        async def held(session_id: str, key: str, index: int, now: int) -> None:
            if index == 0:
                recording.set()
                await release.wait()
            await real(session_id, key, index, now)

        monkeypatch.setattr(store, "mark_done", held)
        one, two = await _two_shares(rig, data)
        await one.send_chunk(0, 0, data[:CHUNK_SIZE])
        await asyncio.wait_for(recording.wait(), 5)

        two.writer.write(_bad_chunk(0, 1, data[CHUNK_SIZE:]))
        assert await two.read(5) == {"skip": 0}
        release.set()

        assert await one.read(5) == {"ack": 0, "file": 0}
        assert await one.read(5) == {"skip": 0}


@pytest.mark.integration
@_needs_psk
async def test_a_chunk_two_streams_wrote_at_the_same_time_is_counted_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sender that sends whole files can send one chunk on two streams; both are written, and the
    bytes are counted once."""
    entered, release = threading.Semaphore(0), threading.Event()
    real = transfer.write_chunk

    def held(path: Path, index: int, chunk: bytes) -> None:
        entered.release()
        release.wait(5)
        real(path, index, chunk)

    monkeypatch.setattr(transfer, "write_chunk", held)
    data = b"one chunk, sent twice"
    async with _guest_rig(tmp_path, {"a": data}) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        await rig.take(control)
        live = rig.live
        one, _ = await rig.stream()
        two, _ = await rig.stream()
        for stream in (one, two):
            await stream.send(_header(0, data))
            assert await stream.read(5) == {"have": []}
        for stream in (one, two):
            await stream.send_chunk(0, 0, data)
        for _ in range(2):
            assert await asyncio.to_thread(entered.acquire, True, 5)
        release.set()

        ends = set()
        for stream in (one, two):
            assert await stream.read(5) == {"ack": 0, "file": 0}
            said = await stream.read(5)
            assert isinstance(said, dict)
            ends.add(next(iter(said)))
        assert ends == {"file_done", "share_done"}
        assert live.moved_bytes == len(data)
