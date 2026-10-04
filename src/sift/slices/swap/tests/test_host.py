# SPDX-License-Identifier: AGPL-3.0-or-later
"""The host of a swap against a guest written by hand to say the wrong thing, on loopback."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

import sift.slices.swap.schema
import sift.slices.workbench.schema  # noqa: F401 (the ledger a swap's start and end go on)
from sift.kernel.tunnels import TunnelError
from sift.slices.swap import lock, transfer
from sift.slices.swap import session as swap
from sift.slices.swap.session import (
    Chunk,
    hello,
    read_hello,
)
from sift.slices.swap.tests.test_session import (
    _ADDRESS,
    _PORT,
    VIEWER,
    _database,
    _device,
    _dropped,
    _ended_row,
    _host_rig,
    _Hosted,
    _Hoster,
    _needs_psk,
    _read_until,
    _sessions,
    _until,
)
from sift.slices.swap.transfer import CHUNK_SIZE


@pytest.mark.integration
@_needs_psk
async def test_a_connection_that_cannot_lock_or_opens_with_no_hello_is_dropped_and_the_token_holds(
    tmp_path: Path,
) -> None:
    async with _host_rig(tmp_path) as rig:
        with pytest.raises(lock.LockFailed):
            await rig.dial(os.urandom(32))
        chunk_first = await rig.dial()
        await chunk_first.send_chunk(0, 0, b"bytes before any hello")
        await _dropped(chunk_first)
        neither = await rig.dial()
        await neither.send({"hi": 1})
        await _dropped(neither)
        row = await rig.sessions.row(rig.id)
        assert row is not None and row.state == "waiting", "none of them used the token"

        await rig.join()

        row = await rig.sessions.row(rig.id)
        assert row is not None and row.state == "connected"


@pytest.mark.integration
@_needs_psk
async def test_a_hello_the_task_never_picks_up_ends_the_session_as_lost(tmp_path: Path) -> None:
    async with _host_rig(tmp_path, run=False, hello_wait=0.3) as rig:
        conn = await rig.dial()
        await conn.send(hello(_device(), "guest", rig.started.token.secret, os.urandom(32)))

        await _dropped(conn)

        row = await _ended_row(rig.sessions, rig.id)
        assert (row.state, row.end_reason) == ("failed", swap.LOST)
        assert rig.hoster.stopped == ["tunnel-host"]


@pytest.mark.integration
@_needs_psk
async def test_a_token_nobody_used_before_it_ran_out_ends_the_session_as_expired(
    tmp_path: Path,
) -> None:
    clock = [1_800_000_000.0]
    async with _host_rig(tmp_path, run=False, now=lambda: clock[0]) as rig:
        clock[0] = rig.started.token.expires + 1.0

        await asyncio.wait_for(rig.sessions.run(rig.context), 5)  # type: ignore[arg-type]

        row = await rig.sessions.row(rig.id)
        # Its own reason: nothing was ever connected, so no connection was lost.
        assert row is not None and (row.state, row.end_reason) == ("ended", swap.EXPIRED)
        assert rig.hoster.stopped == ["tunnel-host"]


@pytest.mark.integration
@_needs_psk
async def test_an_end_pressed_while_the_host_waits_for_its_guest_ends_the_task(
    tmp_path: Path,
) -> None:
    async with _host_rig(tmp_path) as rig:
        await _until(rig.live.attached.is_set)
        await asyncio.sleep(0.1)

        await rig.sessions.end(rig.id)

        row = await rig.ended()
        assert (row.state, row.end_reason) == ("ended", swap.ENDED_BY_YOU)


@pytest.mark.integration
@_needs_psk
async def test_an_end_pressed_at_the_offer_tells_the_guest_and_a_second_match_sends_nothing(
    tmp_path: Path,
) -> None:
    async with _host_rig(tmp_path) as rig:
        control = await rig.join()
        await rig.offer()
        await rig.sessions.answer_code(rig.id, True)
        row = await rig.sessions.row(rig.id)
        assert row is not None and row.state == "offered"

        await rig.sessions.end(rig.id)

        heard: list[dict[str, Any]] = []
        while True:
            frame = await control.read(5)
            assert isinstance(frame, dict)
            if "end" in frame:
                break
            heard.append(frame)
        assert frame == {"end": "ended"}
        assert not [one for one in heard if "offer" in one], "the second press offered nothing"
        row = await rig.ended()
        assert (row.state, row.end_reason) == ("ended", swap.ENDED_BY_YOU)


@pytest.mark.integration
@_needs_psk
@pytest.mark.parametrize(
    ("word", "reason"),
    [("ended", swap.ENDED_BY_THEM), ("refused", swap.REFUSED), ("gone", swap.ENDED_BY_THEM)],
)
async def test_an_end_the_guest_sends_is_written_as_the_guest_said_it(
    tmp_path: Path, word: str, reason: str
) -> None:
    async with _host_rig(tmp_path) as rig:
        control = await rig.join()

        await control.send({"end": word})

        row = await rig.ended()
        assert (row.state, row.end_reason) == ("ended", reason)


@pytest.mark.integration
@_needs_psk
@pytest.mark.parametrize("how", ["a chunk on it", "it closed"])
async def test_a_control_connection_that_lies_is_lost_and_one_gone_away_is_cut_off(
    tmp_path: Path, how: str
) -> None:
    async with _host_rig(tmp_path) as rig:
        control = await rig.join()

        if how == "a chunk on it":
            await control.send_chunk(0, 0, b"a chunk where only messages go")
            row = await rig.ended()
            assert (row.state, row.end_reason) == ("failed", swap.LOST)
            return
        assert rig.pinger is not None
        rig.pinger.cancel()
        control.close()

        # Gone away is a tunnel dropping, not a lie: cut off, and waiting to be joined again.
        await _until(lambda: rig.live.cut_at)
        cut = await rig.sessions.row(rig.id)
        assert cut is not None and cut.live and cut.cut_off_at is not None


@pytest.mark.integration
@_needs_psk
@pytest.mark.parametrize(
    "diff",
    [{"wanted": ["never-offered"], "people": {}}, {"wanted": 5}],
    ids=["a file never offered", "a diff that does not read"],
)
async def test_a_diff_that_asks_for_what_was_not_offered_ends_the_session_as_lost(
    tmp_path: Path, diff: dict[str, Any]
) -> None:
    async with _host_rig(tmp_path) as rig:
        control = await rig.join()
        await rig.offer()
        # A message the host has no use for, and a diff that is not an object, are passed over.
        await control.send({"hello again": 1})
        await control.send({"diff": "everything"})
        await asyncio.sleep(0.1)
        row = await rig.sessions.row(rig.id)
        assert row is not None and row.state == "offered"

        await control.send({"diff": diff})

        row = await rig.ended()
        assert (row.state, row.end_reason) == ("failed", swap.LOST)
        assert row.wanted_files == 0


@pytest.mark.integration
@_needs_psk
async def test_a_stream_before_the_transfer_or_for_another_session_is_turned_away(
    tmp_path: Path,
) -> None:
    async with _host_rig(tmp_path) as rig:
        await rig.join()
        early = await rig.stream()
        await _dropped(early)
        await rig.transferring("a")
        stranger = await rig.stream(session="01HNOTTHISSESSION00000001")
        await _dropped(stranger)

        stream = await rig.stream()

        header = await stream.read(5)
        assert isinstance(header, dict) and header["file"] == 0


@pytest.mark.integration
@_needs_psk
async def test_a_file_gone_from_the_host_is_said_so_and_one_the_guest_has_moves_no_bytes(
    tmp_path: Path,
) -> None:
    async with _host_rig(tmp_path, {"a": b"gone", "b": b"here"}, gone=("a",)) as rig:
        await rig.join()
        await rig.transferring("a", "b")
        stream = await rig.stream()

        assert await stream.read(5) == {"file": 0, "cannot": True}
        header = await stream.read(5)
        assert isinstance(header, dict) and header["file"] == 1
        # The guest verified this one in a session before, and the word was lost: it says so now.
        await stream.send({"file_done": 1})
        assert await stream.read(5) == {"none": True}

        row = await rig.finish()
        assert (row.state, row.sent_files, row.sent_bytes) == ("done", 1, 0)
        assert rig.stripped() == []


@pytest.mark.integration
@_needs_psk
async def test_a_file_the_guest_turns_down_is_failed_and_its_stripped_copy_removed(
    tmp_path: Path,
) -> None:
    async with _host_rig(tmp_path) as rig:
        await rig.join()
        await rig.transferring("a", "a")  # named twice, wanted once
        stream = await rig.stream()
        header = await stream.read(5)
        assert isinstance(header, dict) and header["file"] == 0
        assert rig.stripped(), "the copy the header describes is on disk"

        await stream.send({"skip": 0})

        assert await stream.read(5) == {"none": True}
        row = await rig.finish()
        assert (row.state, row.wanted_files, row.sent_files, row.sent_bytes) == ("done", 1, 0, 0)
        assert rig.stripped() == []


@pytest.mark.integration
@_needs_psk
async def test_a_resumed_file_counts_only_what_this_session_sent_and_each_window_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = os.urandom(CHUNK_SIZE + 10)
    async with _host_rig(tmp_path, {"a": data}, rate_window=0.1) as rig:
        rates: list[int] = []
        set_rate = rig.sessions.store.set_rate

        async def recording(session_id: str, rate: int) -> None:
            rates.append(rate)
            await set_rate(session_id, rate)

        monkeypatch.setattr(rig.sessions.store, "set_rate", recording)
        await rig.join()
        await rig.transferring("a")
        stream = await rig.stream()
        await stream.read(5)

        # Chunk 0 arrived in an earlier session: only chunk 1 is sent, and only it is counted.
        await stream.send({"have": [0]})
        chunk = await stream.read(5)
        assert isinstance(chunk, Chunk) and (chunk.index, len(chunk.data)) == (1, 10)
        await stream.send({"ack": 1, "file": 0})
        await _until(lambda: any(rates) and rig.context.progress)
        mid = await rig.sessions.row(rig.id)
        assert mid is not None and mid.sent_bytes == 10, "written at the window, not at the end"
        # A second word about a chunk already acknowledged changes nothing.
        await stream.send({"ack": 1, "file": 0})
        await stream.send({"file_done": 0})
        assert await stream.read(5) == {"none": True}

        row = await rig.finish()
        assert (row.state, row.sent_files, row.sent_bytes) == ("done", 1, 10)
        assert max(rates) == 800  # 10 bytes in a tenth of a second
        assert 0 < max(rig.context.progress) < 0.01


@pytest.mark.integration
@_needs_psk
async def test_a_transfer_of_nothing_measures_its_windows_and_reports_no_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _host_rig(tmp_path, rate_window=0.1) as rig:
        rates: list[int] = []
        set_rate = rig.sessions.store.set_rate

        async def recording(session_id: str, rate: int) -> None:
            rates.append(rate)
            await set_rate(session_id, rate)

        monkeypatch.setattr(rig.sessions.store, "set_rate", recording)
        await rig.join()

        await rig.transferring()
        await _until(lambda: len(rates) >= 2)

        stream = await rig.stream()
        assert await stream.read(5) == {"none": True}
        row = await rig.finish()
        assert (row.state, row.wanted_files, row.sent_bytes) == ("done", 0, 0)
        assert set(rates) == {0}
        assert rig.context.progress == [], "no fraction of nothing"


@pytest.mark.integration
@_needs_psk
async def test_a_chunk_asked_for_again_past_the_limit_fails_the_file(tmp_path: Path) -> None:
    async with _host_rig(tmp_path) as rig:
        await rig.join()
        await rig.transferring("a")
        stream = await rig.stream()
        await stream.read(5)
        await stream.send({"have": []})

        sent = 0
        for _ in range(transfer.MAX_RETRIES + 1):
            chunk = await stream.read(5)
            assert isinstance(chunk, Chunk) and chunk.index == 0
            sent += 1
            await stream.send({"again": 0, "file": 0})

        assert await stream.read(5) == {"none": True}
        assert sent == transfer.MAX_RETRIES + 1
        row = await rig.finish()
        assert (row.state, row.sent_files) == ("done", 0)


@pytest.mark.integration
@_needs_psk
@pytest.mark.parametrize("when", ["in flight", "after every ack"])
async def test_a_skip_from_the_guest_fails_the_file_at_any_point(tmp_path: Path, when: str) -> None:
    async with _host_rig(tmp_path) as rig:
        await rig.join()
        await rig.transferring("a")
        stream = await rig.stream()
        await stream.read(5)
        await stream.send({"have": []})
        assert isinstance(await stream.read(5), Chunk)
        if when == "after every ack":
            await stream.send({"ack": 0, "file": 0})

        await stream.send({"skip": 0})

        assert await stream.read(5) == {"none": True}
        row = await rig.finish()
        assert (row.state, row.sent_files) == ("done", 0)
        assert rig.stripped() == []


@pytest.mark.integration
@_needs_psk
@pytest.mark.parametrize(
    "answer",
    [
        "a chunk for a header",
        "a have that is not a list",
        "a have past the file's chunks",
        "a chunk for a chunk",
        "an ack for a chunk not sent",
        "a chunk after the acks",
        "another file after the acks",
    ],
)
async def test_a_stream_answer_not_about_the_file_in_hand_drops_it_and_the_file_goes_back(
    tmp_path: Path, answer: str
) -> None:
    async with _host_rig(tmp_path) as rig:
        await rig.join()
        await rig.transferring("a")
        stream = await rig.stream()
        await stream.read(5)
        if answer == "a chunk for a header":
            await stream.send_chunk(0, 0, b"a guest sends no chunks")
        elif answer == "a have that is not a list":
            await stream.send({"have": "all of it"})
        elif answer == "a have past the file's chunks":
            await stream.send({"have": [9]})
        else:
            await stream.send({"have": []})
            assert isinstance(await stream.read(5), Chunk)
            if answer == "a chunk for a chunk":
                await stream.send_chunk(0, 0, b"a guest sends no chunks")
            elif answer == "an ack for a chunk not sent":
                await stream.send({"ack": 5, "file": 0})
            else:
                await stream.send({"ack": 0, "file": 0})
                if answer == "a chunk after the acks":
                    await stream.send_chunk(0, 0, b"a guest sends no chunks")
                else:
                    await stream.send({"file_done": 7})

        await _dropped(stream)

        again = await rig.stream()
        header = await again.read(5)
        assert isinstance(header, dict) and header["file"] == 0, "the file went back to the queue"


@pytest.mark.integration
@_needs_psk
async def test_a_file_confirmed_just_after_the_guests_done_is_still_counted(
    tmp_path: Path,
) -> None:
    data = b"the last file"
    async with _host_rig(tmp_path, {"a": data}) as rig:
        control = await rig.join()
        await rig.transferring("a")
        stream = await rig.stream()
        await stream.read(5)
        await stream.send({"have": []})
        assert isinstance(await stream.read(5), Chunk)

        await control.send({"done": {"received": 1}})
        await asyncio.sleep(0.2)
        await stream.send({"ack": 0, "file": 0})
        await stream.send({"file_done": 0})

        assert await stream.read(5) == {"none": True}
        row = await rig.ended()
        assert (row.state, row.sent_files, row.sent_bytes) == ("done", 1, len(data))


@pytest.mark.integration
@_needs_psk
async def test_an_end_mid_transfer_closes_the_streams_and_removes_the_stripped_copy(
    tmp_path: Path,
) -> None:
    async with _host_rig(tmp_path) as rig:
        control = await rig.join()
        await rig.transferring("a")
        stream = await rig.stream()
        await stream.read(5)
        assert rig.stripped()

        await rig.sessions.end(rig.id)

        await _dropped(stream)
        assert (await _read_until(control, "end"))["end"] == "ended"
        row = await rig.ended()
        assert (row.state, row.end_reason) == ("ended", swap.ENDED_BY_YOU)
        assert rig.stripped() == []


@pytest.mark.integration
@_needs_psk
async def test_a_port_moved_after_the_guest_joined_is_told_to_the_guest(tmp_path: Path) -> None:
    async with _host_rig(tmp_path) as rig:
        control = await rig.join()

        await rig.hoster.on_moved(_Hosted(_ADDRESS, _PORT + 1))

        moved = await _read_until(control, "moved")
        assert moved["moved"] == {"address": _ADDRESS, "port": _PORT + 1}


@pytest.mark.integration
@_needs_psk
async def test_a_port_moved_to_an_address_no_token_can_hold_ends_the_session(
    tmp_path: Path,
) -> None:
    async with _host_rig(tmp_path) as rig:
        live = rig.live

        await rig.hoster.on_moved(_Hosted("10.0.0.7", _PORT))

        row = await rig.ended()
        assert (row.state, row.end_reason) == ("failed", swap.LOST)
        # A renewal reported after the end remakes nothing.
        await rig.hoster.on_moved(_Hosted(_ADDRESS, _PORT + 2))
        assert live.token is not None and live.token.port == _PORT


@pytest.mark.integration
@_needs_psk
async def test_a_hosting_the_tunnel_lost_cuts_the_session_off_and_hosts_again(
    tmp_path: Path,
) -> None:
    async with _host_rig(tmp_path, retry_seconds=0.1) as rig:
        await rig.join()
        listening = rig.hoster.target
        rig.hoster.target = None

        await rig.hoster.on_lost(TunnelError("the renewal was refused"))

        cut = await rig.sessions.row(rig.id)
        assert cut is not None and cut.live and cut.cut_off_at is not None
        assert rig.hoster.stopped == [], (
            "the hoster ended the hosting itself; it is not asked again"
        )
        # Hosted again on the same tunnel, onto the listener that never closed.
        await _until(lambda: rig.hoster.target is not None)
        assert rig.hoster.target == listening and rig.live.hosting
        assert rig.live.cut_at is not None, "hosting again is not the guest joining again"


@pytest.mark.integration
@_needs_psk
async def test_a_tunnel_that_will_not_stop_hosting_does_not_hold_the_end_up(
    tmp_path: Path,
) -> None:
    class _WillNotStop(_Hoster):
        async def stop_hosting(self, tunnel_id: str) -> None:
            raise TunnelError("the client did not answer")

    async with _host_rig(tmp_path, run=False, hoster=_WillNotStop()) as rig:
        await rig.sessions.end(rig.id)

        row = await rig.sessions.row(rig.id)
        assert row is not None and (row.state, row.end_reason) == ("ended", swap.ENDED_BY_YOU)
        assert not rig.sessions.any_live()


class _Writer:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.mark.integration
@_needs_psk
async def test_a_connection_taken_between_the_end_and_the_listener_closing_is_closed_unread(
    tmp_path: Path,
) -> None:
    """The end is set before the listener is closed, so a connection can arrive between them."""
    async with _host_rig(tmp_path, run=False) as rig:
        live = rig.live
        await rig.sessions.end(rig.id)
        reader = asyncio.StreamReader()
        reader.feed_data(b"a hello")
        reader.feed_eof()
        writer = _Writer()

        await live.on_connection(reader, writer)  # type: ignore[arg-type]

        assert writer.closed
        assert await reader.read() == b"a hello"


@pytest.mark.integration
@_needs_psk
async def test_a_session_ends_once_for_the_first_reason_and_never_for_an_unknown_one(
    tmp_path: Path,
) -> None:
    async with _host_rig(tmp_path, run=False) as rig:
        live = rig.live
        with pytest.raises(ValueError, match="not a reason"):
            await live.end("bored")

        await live.end(swap.ENDED_BY_YOU)
        await live.end(swap.LOST)

        row = await rig.sessions.row(rig.id)
        assert row is not None and (row.state, row.end_reason) == ("ended", swap.ENDED_BY_YOU)


@pytest.mark.integration
async def test_a_row_ended_by_something_else_during_the_restart_sweep_is_not_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = await _database(tmp_path / "s.sqlite3")
    sessions = _sessions(database, tmp_path)
    try:
        await sessions.store.create(
            "01HRACEDTOTHEEND000000001", role="guest", started_at=1, started_by=VIEWER
        )

        async def already_ended(session_id: str, **_kwargs: Any) -> bool:
            return False

        monkeypatch.setattr(sessions.store, "end", already_ended)

        assert await sessions.settle_after_restart() == []
    finally:
        await database.close()


@pytest.mark.integration
@_needs_psk
async def test_the_same_guest_joining_again_on_a_new_connection_closes_the_old_and_an_end_there_ends(
    tmp_path: Path,
) -> None:
    """The guest dialled again before this side noticed the first connection go: the new one is
    the session's, the old one is closed, and the guest's end said on the new one ends it."""
    async with _host_rig(tmp_path) as rig:
        me, secret = _device(), rig.started.token.secret
        first = await rig.dial()
        await first.send(hello(me, "guest", secret, os.urandom(32)))
        assert isinstance(await first.read(5), dict)
        await _until(rig.code)

        again = await rig.dial()
        await again.send(hello(me, "guest", secret, os.urandom(32)))
        answer = await again.read(5)
        assert isinstance(answer, dict) and read_hello(answer, "host", secret).session == rig.id
        with pytest.raises((asyncio.IncompleteReadError, OSError)):
            while True:
                await first.read(5)

        await again.send({"end": "ended"})
        row = await rig.ended()
        assert row.end_reason == swap.ENDED_BY_THEM


@pytest.mark.integration
@_needs_psk
async def test_a_stream_offering_files_back_in_a_swap_one_way_is_turned_away(
    tmp_path: Path,
) -> None:
    async with _host_rig(tmp_path) as rig:
        await rig.join()
        stream = await rig.dial()

        await stream.send({"stream": 0, "session": rig.session, "send": 1})

        await _dropped(stream)
        assert rig.live.streams_in == set() and rig.live.reason is None


class _Renewing(_Hoster):
    """Refuses to host on the calls `refusals` counts, from one, and hosts on the rest."""

    def __init__(self, refusals: set[int]) -> None:
        super().__init__()
        self.calls = 0
        self.refusals = refusals

    async def host_on(self, tunnel_id: str, **kwargs: Any) -> _Hosted:
        self.calls += 1
        if self.calls in self.refusals:
            raise TunnelError("the provider refused the port")
        return await super().host_on(tunnel_id, **kwargs)


@pytest.mark.integration
@_needs_psk
async def test_a_hosting_lost_again_while_cut_off_is_tried_again_and_hosted_once(
    tmp_path: Path,
) -> None:
    """A refusal is tried again after the wait. A second loss while cut off starts another try,
    and once either hosts, the other stops rather than host a second time."""
    hoster = _Renewing(refusals={2})
    async with _host_rig(tmp_path, retry_seconds=0.1, hoster=hoster) as rig:
        await rig.join()
        live = rig.live

        await hoster.on_lost(TunnelError("the renewal was refused"))
        await hoster.on_lost(TunnelError("and again"))

        await _until(lambda: live.hosting)
        await asyncio.sleep(0.4)
        assert hoster.calls == 3, "the start, the refusal, and the one that hosted"
        assert live.cut_at is not None


@pytest.mark.integration
@_needs_psk
async def test_a_hosting_lost_before_the_task_has_the_key_waits_for_the_key_to_host_again(
    tmp_path: Path,
) -> None:
    """The saved keys are what a hosting is asked for with; until the task has them, nothing is."""
    async with _host_rig(tmp_path, run=False, retry_seconds=0.05) as rig:
        live = rig.live
        rig.hoster.target = None

        await live.hosting_lost(TunnelError("the renewal was refused"))
        await asyncio.sleep(0.3)

        assert rig.hoster.target is None and not live.hosting
        live.context = rig.context  # type: ignore[assignment]
        await _until(lambda: live.hosting)
        assert rig.hoster.target == live.port
