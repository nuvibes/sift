# SPDX-License-Identifier: AGPL-3.0-or-later
"""A cut-off says what cut it, a guest that cannot reach the host stops and asks for a new token,
a host nobody rejoins hosts again on a fresh port, a Join turns its tunnel on, and a paused queue
refuses a swap before a token is shown.

On loopback with the session tests' own rigs, and against the parts of a session directly.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from sift.kernel.jobs.holding import Pause
from sift.kernel.tunnels import TunnelError
from sift.slices.swap import guest as guest_side
from sift.slices.swap import host as host_side
from sift.slices.swap import live as live_side
from sift.slices.swap import session as swap
from sift.slices.swap import token as token_module
from sift.slices.swap.handshake import (
    CUT_CONTROL_CLOSED,
    CUT_HOSTING_LOST,
    CUT_SEND_FAILED,
    CUT_SILENT,
    CUT_STREAMS_LOST,
    PAUSED,
    REJOIN_DIALS,
    SWAP_SESSION,
)
from sift.slices.swap.lock import LockFailed
from sift.slices.swap.session import SwapRefused
from sift.slices.swap.tests.test_cut_off import _Said
from sift.slices.swap.tests.test_session import (
    _ADDRESS,
    _MASTER,
    _PORT,
    VIEWER,
    _database,
    _device,
    _Egress,
    _guest_rig,
    _host_rig,
    _Hosted,
    _Hoster,
    _Jobs,
    _keep_pinging,
    _needs_psk,
    _sessions,
    _start_host,
    _until,
)
from sift.testing.logs import uncached_log


def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    for module in (live_side, host_side, guest_side, swap):
        uncached_log(monkeypatch, module)


def _cuts(written: list[Any]) -> list[tuple[str, str | None]]:
    return [(one["why"], one["error"]) for one in written if one["event"] == "swap.cut_off"]


def _now(live: Any, name: str) -> Any:
    """A session's field read afresh: a check before an await does not hold after it."""
    return vars(live)[name]


async def _no_wait(_seconds: float) -> None:
    await asyncio.sleep(0)


# --- what cut it -----------------------------------------------------------------------------------


async def test_a_cut_says_which_path_cut_it_and_what_that_path_met(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each path: a control send failing, the control connection closing, the silence, the
    streams past their redial limit, the hosting lost. And the ping goes with the cut."""
    _quiet(monkeypatch)
    with capture_logs() as written:
        async with _guest_rig(tmp_path / "send", run=False) as rig:
            live = rig.live
            live.control = _Said(ConnectionResetError("gone"))  # type: ignore[assignment]
            live._ping = asyncio.create_task(asyncio.Event().wait())
            pinging = live._ping
            waiting = asyncio.create_task(live.send_control({"diff": 1}))
            await _until(lambda: live.cut_at)
            await asyncio.sleep(0)
            assert pinging.cancelled() and _now(live, "_ping") is None, "no ping on a closed one"
            assert live.retry_at == _now(live, "cut_at"), "a guest cut off dials from the cut"
            await rig.sessions.end(rig.id)
            await asyncio.wait_for(waiting, 5)

        async with _guest_rig(tmp_path / "closed", run=False) as rig:
            live = rig.live

            class _Closing:
                async def read(self, *_args: Any) -> Any:
                    raise ConnectionResetError("closed under it")

                def close(self) -> None:
                    pass

            closing = _Closing()
            live.control = closing  # type: ignore[assignment]
            await live.listen(closing)  # type: ignore[arg-type]
            assert _now(live, "cut_at") is not None

        async with _guest_rig(tmp_path / "silent", run=False) as rig:
            live = rig.live
            live.armed = True
            live.watchdog._last = -1e9
            watching = asyncio.create_task(live.watch())
            await _until(lambda: live.cut_at)
            watching.cancel()

        async with _guest_rig(tmp_path / "streams", run=False, sleep=_no_wait) as rig:
            live = rig.live
            live.wanted = [0]

            async def refused() -> Any:
                raise LockFailed("the host could not be reached")

            live._dial = refused  # type: ignore[method-assign]
            await live.stream(0)
            assert _now(live, "cut_at") is not None

        (tmp_path / "hosting").mkdir()
        async with _host_rig(tmp_path / "hosting", run=False) as hosted:
            host = hosted.live
            await host.hosting_lost(TunnelError("the renewal was refused"))
            assert _now(host, "cut_at") is not None
            host.hosting = True  # put back, so the end does not wait on a hosting again

    assert _cuts(written) == [
        (CUT_SEND_FAILED, "ConnectionResetError"),
        (CUT_CONTROL_CLOSED, "ConnectionResetError"),
        (CUT_SILENT, None),
        (CUT_STREAMS_LOST, "LockFailed"),
        (CUT_HOSTING_LOST, "TunnelError"),
    ]
    lost = [one for one in written if one["event"] == "swap.streams_lost"]
    assert [one["error"] for one in lost] == ["LockFailed"], "the redial limit logs its error"


async def test_a_cut_joined_again_keeps_its_span_and_each_direction_its_first_and_last_bytes(
    tmp_path: Path,
) -> None:
    async with _guest_rig(tmp_path, run=False) as rig:
        live = rig.live
        clock = [1000]
        live.owner._now = lambda: clock[0]
        live.moved(0)
        assert live.first_moved_at is None, "nothing moved is not a first byte"
        live.moved(10)
        clock[0] = 1005
        live.moved(10)
        assert (_now(live, "first_moved_at"), live.last_moved_at) == (1000, 1005)

        await live.cut(CUT_SILENT)
        clock[0] = 1030
        await live.resume(_Said())  # type: ignore[arg-type]
        assert live.cut_spans == [(1005, 1030)] and live.cut_offs == 1


# --- the guest that cannot reach the host ------------------------------------------------------------


@pytest.mark.integration
@_needs_psk
async def test_a_guest_that_cannot_dial_back_stops_asks_for_a_new_token_and_joins_on_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every dial back refused: it stops after `REJOIN_DIALS`, says so by having no next try, and
    dials no more until a Join; the new token joins the same session again."""
    _quiet(monkeypatch)
    with capture_logs() as written:
        async with _guest_rig(tmp_path, retry_seconds=0.05) as rig:
            control = await rig.connected()
            live = rig.live
            rig.lock_with[0] = os.urandom(32)
            assert rig.pinger is not None
            rig.pinger.cancel()
            control.close()
            await _until(lambda: live.cut_at)

            await _until(lambda: live.cut_at and live.retry_at is None)
            extras = rig.sessions.extras(rig.id)
            assert extras is not None and extras.retry_at is None
            await asyncio.sleep(0.3)
            failed = [one for one in written if one["event"] == "swap.rejoin_failed"]
            assert len(failed) == REJOIN_DIALS, "no dial after it stopped"
            assert all(one["why"] == "the connection could not be locked" for one in failed)

            # The host's new token: the same secret, for the port it hosts on now.
            remade = token_module.mint(
                _ADDRESS, _PORT + 1, rig.host.id, rig.sessions.now(), secret=rig.secret
            )
            rig.lock_with[0] = rig.secret
            same = await rig.sessions.join(
                viewer_id=VIEWER,
                token_text=remade.text,
                dest_folder_id="folder",
                master_key=_MASTER,
            )
            assert same == rig.id and live.port == _PORT + 1
            conn = await rig.conn()
            await rig.answer_hello(conn)
            await _until(lambda: live.cut_at is None)
            rig.pinger = asyncio.create_task(_keep_pinging(conn))
    given_up = [one for one in written if one["event"] == "swap.rejoin_given_up"]
    assert [one["dials"] for one in given_up] == [REJOIN_DIALS]
    assert [one["event"] for one in written].count("swap.rejoined") == 1


# --- the host nobody rejoins ---------------------------------------------------------------------------


class _Moving(_Hoster):
    """Hosts on a new port each time it is asked."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def host_on(self, tunnel_id: str, **kwargs: Any) -> _Hosted:
        await super().host_on(tunnel_id, **kwargs)
        self.calls += 1
        return _Hosted(_ADDRESS, _PORT + self.calls - 1)


@pytest.mark.integration
@_needs_psk
async def test_a_host_still_cut_off_after_the_guests_dials_hosts_again_and_shows_a_new_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet(monkeypatch)
    hoster = _Moving()
    with capture_logs() as written:
        async with _host_rig(tmp_path, retry_seconds=0.05, hoster=hoster) as rig:
            control = await rig.join()
            live = rig.live
            old = rig.started.token.text
            assert rig.pinger is not None
            rig.pinger.cancel()
            control.close()
            await _until(lambda: live.cut_at)

            await _until(lambda: hoster.calls == 2)
            await _until(lambda: live.token is not None and live.token.port == _PORT + 1)
            facts = rig.sessions.facts(rig.id)
            assert facts is not None and facts.token not in (None, old), "the new one is shown"
            assert hoster.stopped == ["tunnel-host"], "the old hosting let go first"
            assert live.token is not None and live.token.secret == rig.started.token.secret
            await asyncio.sleep(0.5)
            assert hoster.calls == 2, "once for the cut"
    events = [one["event"] for one in written]
    assert events.count("swap.hosting_anew") == 1
    assert [one["joined"] for one in written if one["event"] == "swap.token_remade"] == [False]


@pytest.mark.integration
@_needs_psk
async def test_a_port_moved_while_joined_remakes_the_token_and_tells_the_guest(
    tmp_path: Path,
) -> None:
    async with _host_rig(tmp_path) as rig:
        control = await rig.join()
        live = rig.live
        await rig.hoster.on_moved(_Hosted(_ADDRESS, _PORT + 7))
        assert live.token is not None and live.token.port == _PORT + 7
        while True:
            frame = await control.read(5)
            if isinstance(frame, dict) and "moved" in frame:
                break
        assert frame["moved"] == {"address": _ADDRESS, "port": _PORT + 7}
        await rig.hoster.on_moved(_Hosted(_ADDRESS, _PORT + 7))
        assert live.token.port == _PORT + 7, "the same place is no move"


# --- a Join whose tunnel is off ------------------------------------------------------------------------


@dataclass
class _View:
    id: str
    name: str
    enabled: bool
    running: bool


class _Switched(_Hoster):
    """A tunnel manager whose tunnels can be off, turned on and off again."""

    def __init__(self, *, starts: bool = True, enabled: bool = False) -> None:
        super().__init__()
        self.view = _View("tunnel-guest", "NY-711", enabled, running=False)
        self.starts = starts
        self.switched: list[str] = []

    async def list(self) -> list[_View]:
        return [self.view, _View("other", "IL-322", True, True)]

    async def start(self, tunnel_id: str, master_key: bytes) -> None:
        if not self.starts:
            raise TunnelError("The tunnel's client didn't start.")
        self.switched.append(f"on {tunnel_id}")
        self.view.running = True

    async def stop(self, tunnel_id: str, *, drain: bool = True) -> None:
        self.switched.append(f"off {tunnel_id}")
        self.view.running = False


async def _join(sessions: swap.SwapSessions) -> str:
    token = token_module.mint(_ADDRESS, _PORT, _device().id, sessions.now(), secret=os.urandom(32))
    return await sessions.join(
        viewer_id=VIEWER, token_text=token.text, dest_folder_id="folder", master_key=_MASTER
    )


async def test_a_join_turns_its_tunnel_on_and_the_end_turns_it_off_as_it_was(
    tmp_path: Path,
) -> None:
    database = await _database(tmp_path / "guest.sqlite3")
    try:
        off = _Switched()
        sessions = _sessions(database, tmp_path, hoster=off)
        joined = await _join(sessions)
        assert off.switched == ["on tunnel-guest"]
        await sessions.end(joined)
        assert off.switched == ["on tunnel-guest", "off tunnel-guest"]

        meant_on = _Switched(enabled=True)
        sessions = _sessions(database, tmp_path, hoster=meant_on)
        joined = await _join(sessions)
        await sessions.end(joined)
        assert meant_on.switched == ["on tunnel-guest"], "a tunnel meant to be on stays on"
    finally:
        await database.close()


async def test_a_join_whose_tunnel_will_not_start_is_refused_by_its_name_before_any_session(
    tmp_path: Path,
) -> None:
    database = await _database(tmp_path / "guest.sqlite3")
    try:
        sessions = _sessions(database, tmp_path, hoster=_Switched(starts=False))
        with pytest.raises(SwapRefused) as refused:
            await _join(sessions)
        assert str(refused.value).startswith("The tunnel NY-711 is off and didn't start.")
        assert refused.value.field == swap.TUNNEL_FIELD
        assert await sessions.store.unfinished() == [] and not sessions.any_live()
    finally:
        await database.close()


@pytest.mark.integration
@_needs_psk
async def test_a_tunnel_that_fails_under_the_session_is_logged_in_its_own_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Off(_Egress):
        def through(self, route: str | None) -> Any:
            raise TunnelError("The tunnel NY-711 is turned off.")

    _quiet(monkeypatch)
    with capture_logs() as written:
        async with _guest_rig(tmp_path, egress=_Off(None)) as rig:
            row = await rig.ended()
    assert (row.state, row.end_reason) == ("failed", swap.LOST)
    failed = [one for one in written if one["event"] == "swap.session_failed"]
    assert [(one["error"], one["detail"]) for one in failed] == [
        ("TunnelError", "The tunnel NY-711 is turned off.")
    ]


# --- a paused queue ------------------------------------------------------------------------------------


class _Paused(_Jobs):
    def __init__(self, pause: Pause) -> None:
        super().__init__()
        self.pause = pause

    async def holds(self) -> Pause:
        return self.pause


@pytest.mark.parametrize(
    "pause", [Pause(everything=True), Pause(types=frozenset({SWAP_SESSION}))], ids=["all", "swaps"]
)
async def test_start_and_join_under_a_paused_queue_are_refused_before_a_token_or_a_row(
    tmp_path: Path, pause: Pause
) -> None:
    database = await _database(tmp_path / "s.sqlite3")
    try:
        hoster = _Hoster()
        sessions = _sessions(database, tmp_path, hoster=hoster, jobs=_Paused(pause))
        with pytest.raises(SwapRefused) as refused:
            await _start_host(sessions)
        assert str(refused.value) == PAUSED
        assert hoster.target is None, "nothing hosted, so no token"
        with pytest.raises(SwapRefused):
            await _join(sessions)
        assert await sessions.store.unfinished() == [] and not sessions.any_live()

        other = _Paused(Pause(types=frozenset({"scan"})))
        sessions = _sessions(database, tmp_path, hoster=hoster, jobs=other)
        started = await _start_host(sessions)
        assert started.token is not None, "another type paused holds no swap"
        await sessions.end(started.session_id)
    finally:
        await database.close()


async def test_a_queue_that_cannot_say_what_is_paused_refuses_nothing(
    tmp_path: Path,
) -> None:
    """A queue that cannot say what is paused (an older wiring) refuses nothing."""
    database = await _database(tmp_path / "s.sqlite3")
    try:
        sessions = _sessions(database, tmp_path)
        started = await _start_host(sessions)
        assert started.token is not None
        await sessions.end(started.session_id)
    finally:
        await database.close()


# --- the edges -----------------------------------------------------------------------------------------


class _WillNotLetGo(_Hoster):
    async def stop_hosting(self, tunnel_id: str) -> None:
        raise RuntimeError("the tunnel would not stop")


async def test_a_host_hosting_again_passes_over_what_it_cannot_do_and_what_no_longer_applies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quiet(monkeypatch)
    hoster = _WillNotLetGo()
    with capture_logs() as written:
        async with _host_rig(tmp_path, run=False, retry_seconds=0.01, hoster=hoster) as rig:
            live = rig.live
            live.ended.set()
            await live.on_cut()
            assert not live.tasks, "an ended session hosts nothing again"
            live.ended.clear()

            live.cut_at = None
            await live.unless_joined_again(5)
            assert hoster.target == live.port and live.hosting, "joined again: left as it was"

            live.cut_at = 7
            live.context = rig.context  # type: ignore[assignment]
            await live.unless_joined_again(7)
            assert live.hosting, "hosted again although the old hosting would not stop"

            live.control = _Said()  # type: ignore[assignment]
            token = live.token
            await live.hosting_moved(_Hosted("not an address", 1))
            assert live.token is token and live.reason is None, "a joined session carries on"
            live.control = None
            live.cut_at = None
    failed = [one for one in written if one["event"] == "swap.stop_hosting_failed"]
    assert failed and failed[0]["error"] == "RuntimeError"


class _WillNotStop(_Switched):
    async def stop(self, tunnel_id: str, *, drain: bool = True) -> None:
        raise TunnelError("The tunnel is busy.")


async def test_a_join_leaves_a_running_tunnel_alone_and_puts_one_back_when_its_row_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = await _database(tmp_path / "guest.sqlite3")
    try:
        running = _Switched()
        running.view.running = True
        sessions = _sessions(database, tmp_path, hoster=running)
        await sessions.end(await _join(sessions))
        assert running.switched == [], "already running: neither started nor stopped"

        off = _Switched()
        sessions = _sessions(database, tmp_path, hoster=off)

        async def broken(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("the disk went")

        monkeypatch.setattr(sessions.store, "create", broken)
        with pytest.raises(RuntimeError):
            await _join(sessions)
        assert off.switched == ["on tunnel-guest", "off tunnel-guest"]

        stuck = _WillNotStop()
        sessions = _sessions(database, tmp_path, hoster=stuck)
        await sessions.end(await _join(sessions))
        assert stuck.switched == ["on tunnel-guest"], "a stop refused is said, not raised"
    finally:
        await database.close()
