# SPDX-License-Identifier: AGPL-3.0-or-later
"""A swap a tunnel cut off is joined again, the guest's tunnel is the one chosen beside Join, and
Activity's row for a swap waits for the other side before it says how long.

On loopback, with the session tests' own rigs: a real host against a guest written by hand, and a
real guest against a host written by hand. A cut is made the way a tunnel makes one: the control
connection closes under a session that never pressed End.
"""

from __future__ import annotations

import asyncio
import base64
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from sift.kernel.jobs.families import HOUSEKEEPING, OwnEstimate, own_estimate
from sift.slices import swap as swap_slice
from sift.slices.swap import session as swap
from sift.slices.swap import token as token_module
from sift.slices.swap.frames import ProtocolError
from sift.slices.swap.session import Chunk, Conn, SwapRefused, hello, read_hello
from sift.slices.swap.store import SessionStore
from sift.slices.swap.tests.test_session import (
    _HOST_SESSION,
    _MASTER,
    VIEWER,
    _database,
    _device,
    _dropped,
    _Egress,
    _guest_rig,
    _host_rig,
    _Jobs,
    _keep_pinging,
    _needs_psk,
    _read_until,
    _send_whole,
    _sessions,
    _start_host,
    _until,
)
from sift.slices.swap.transfer import CHUNK_SIZE

# --- the host: the same guest again ---------------------------------------------------------------


@pytest.mark.integration
@_needs_psk
async def test_rejoin_the_same_guest_carries_the_file_on_from_the_chunk_it_had(
    tmp_path: Path,
) -> None:
    """Cut off mid-file, the session keeps its step; another device with a copy of the token is
    still turned away; the same device joining again carries on, with the code it compared, and
    the file resumes from what the guest already verified."""
    data = os.urandom(CHUNK_SIZE + 10)
    async with _host_rig(tmp_path, {"a": data}, retry_seconds=0.1) as rig:
        me = _device()
        secret = rig.started.token.secret
        control = await rig.dial()
        await control.send(hello(me, "guest", secret, os.urandom(32)))
        answer = await control.read(5)
        assert isinstance(answer, dict)
        rig.session = read_hello(answer, "host", secret).session
        rig.control = control
        rig.pinger = asyncio.create_task(_keep_pinging(control))
        await _until(rig.code)
        code = rig.code()
        await rig.transferring("a")

        stream = await rig.stream()
        header = await stream.read(5)
        assert isinstance(header, dict) and header["file"] == 0
        await stream.send({"have": []})
        first = await stream.read(5)
        assert isinstance(first, Chunk) and first.index == 0
        await stream.send({"ack": 0, "file": 0})

        # The tunnel goes: every connection closes, and nobody pressed End.
        assert rig.pinger is not None
        rig.pinger.cancel()
        control.close()
        stream.close()
        await _until(lambda: rig.live.cut_at)
        row = await rig.sessions.row(rig.id)
        assert row is not None and (row.state, row.live) == ("transferring", True)
        assert row.cut_off_at is not None
        facts = rig.sessions.facts(rig.id)
        assert facts is not None and facts.token, "the token is shown again, to be sent again"

        other = await rig.dial()
        await other.send(hello(_device(), "guest", secret, os.urandom(32)))
        assert await other.read(5) == {"refused": "used"}

        again = await rig.dial()
        await again.send(hello(me, "guest", secret, os.urandom(32)))
        answer = await again.read(5)
        assert isinstance(answer, dict)
        assert read_hello(answer, "host", secret).session == rig.id
        rig.control = again
        rig.pinger = asyncio.create_task(_keep_pinging(again))
        await _until(lambda: rig.live.cut_at is None)
        row = await rig.sessions.row(rig.id)
        assert row is not None and (row.state, row.cut_off_at) == ("transferring", None)
        assert rig.code() == code, "the code they compared stands"

        stream = await rig.stream()
        header = await stream.read(5)
        assert isinstance(header, dict) and header["file"] == 0
        await stream.send({"have": [0]})
        second = await stream.read(5)
        assert isinstance(second, Chunk) and second.index == 1, "only what the guest lacked"
        await stream.send({"ack": 1, "file": 0})
        await stream.send({"file_done": 0})
        assert await stream.read(5) == {"none": True}
        row = await rig.finish()
        assert (row.state, row.sent_files) == ("done", 1)


@pytest.mark.integration
@_needs_psk
async def test_rejoin_before_the_offer_was_answered_sends_the_offer_again(tmp_path: Path) -> None:
    """The offer went out and the connection went with it: joined again, it is sent again."""
    async with _host_rig(tmp_path, retry_seconds=0.1) as rig:
        me = _device()
        secret = rig.started.token.secret
        control = await rig.dial()
        await control.send(hello(me, "guest", secret, os.urandom(32)))
        await control.read(5)
        rig.control = control
        await _until(rig.code)
        await rig.sessions.answer_code(rig.id, True)
        await _read_until(control, "offer")
        control.close()
        await _until(lambda: rig.live.cut_at)

        again = await rig.dial()
        await again.send(hello(me, "guest", secret, os.urandom(32)))
        await again.read(5)
        resent = await _read_until(again, "offer")
        assert [one["key"] for one in resent["offer"]["files"]] == ["a"]


@pytest.mark.integration
@_needs_psk
async def test_they_match_while_cut_off_holds_the_offer_for_the_join_and_an_end_drops_it(
    tmp_path: Path,
) -> None:
    async with _host_rig(tmp_path, retry_seconds=0.1) as rig:
        control = await rig.join()
        assert rig.pinger is not None
        rig.pinger.cancel()
        control.close()
        await _until(lambda: rig.live.cut_at)
        live = rig.live

        await rig.sessions.answer_code(rig.id, True)
        await _until(lambda: live.offer is not None)
        await asyncio.sleep(0.1)
        assert not live.diff_in.done() and live.cut_at is not None, "made, and waiting to go"

        await rig.sessions.end(rig.id)
        row = await rig.ended()
        assert (row.end_reason, row.offered_files) == (swap.ENDED_BY_YOU, 1)


# --- the guest: dialling again ---------------------------------------------------------------------


async def _drained(queue: asyncio.Queue[Conn]) -> None:
    while not queue.empty():
        queue.get_nowait().close()


@pytest.mark.integration
@_needs_psk
async def test_rejoin_a_cut_off_guest_dials_again_says_its_answer_again_and_takes_the_file(
    tmp_path: Path,
) -> None:
    async with _guest_rig(tmp_path, retry_seconds=0.1) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        await rig.take(control)
        live = rig.live
        await _until(lambda: live.transferring)

        # The tunnel goes, and for a while nothing gets through it.
        rig.lock_with[0] = os.urandom(32)
        assert rig.pinger is not None
        rig.pinger.cancel()
        control.close()
        await _until(lambda: live.cut_at)
        row = await rig.sessions.row(rig.id)
        assert row is not None and (row.state, row.live) == ("transferring", True)
        assert rig.context.notes[-1] == swap.CUT_OFF_NOTE
        await _until(lambda: all(task.done() for task in live.streams.values()), 3)

        # A Join with the same token is the same session, and dials immediately.
        same = await rig.sessions.join(
            viewer_id=VIEWER,
            token_text=live.token.text,
            dest_folder_id="folder",
            master_key=_MASTER,
        )
        assert same == rig.id and len(rig.sessions._live) == 1

        await _drained(rig.accepted)
        rig.lock_with[0] = rig.secret
        while True:
            conn = await rig.conn()
            with suppress(Exception):
                first = await conn.read(5)
                if isinstance(first, dict) and "device" in first:
                    break
        assert live.device is not None
        assert read_hello(first, "guest", rig.secret).device == live.device.id, "the same device"
        await conn.send(hello(rig.host, "host", rig.secret, os.urandom(32), session=_HOST_SESSION))
        resent = await _read_until(conn, "diff")
        assert resent["diff"]["wanted"] == ["a"], "the answer is said again"
        await _until(lambda: live.cut_at is None)
        rig.pinger = asyncio.create_task(_keep_pinging(conn))

        stream, _ = await rig.stream()
        await _send_whole(stream, 0, b"a small file")
        await stream.send({"none": True})
        await _read_until(conn, "done")
        ended = await rig.ended()
        assert (ended.state, ended.end_reason) == ("done", swap.REASON_DONE)
        assert rig.landed == {"a": b"a small file"}


@pytest.mark.integration
@_needs_psk
async def test_take_these_while_cut_off_holds_the_answer_for_the_join_and_an_end_drops_it(
    tmp_path: Path,
) -> None:
    """Nothing is received, and nothing counted as wanted, for an answer the host never heard."""
    async with _guest_rig(tmp_path, retry_seconds=0.1) as rig:
        control = await rig.connected()
        await rig.offer_to(control)
        rig.lock_with[0] = os.urandom(32)
        assert rig.pinger is not None
        rig.pinger.cancel()
        control.close()
        live = rig.live
        await _until(lambda: live.cut_at)

        await rig.sessions.take(rig.id, swap.Taken())
        await _until(lambda: live.diff is not None)
        await asyncio.sleep(0.1)
        assert not live.transferring, "waiting for the join to say it"

        await rig.sessions.end(rig.id)
        row = await rig.ended()
        assert (row.end_reason, row.wanted_files) == (swap.ENDED_BY_YOU, 0)


@pytest.mark.integration
@_needs_psk
async def test_rejoin_a_host_that_let_the_session_go_ends_the_guest_as_lost(
    tmp_path: Path,
) -> None:
    async with _guest_rig(tmp_path, retry_seconds=0.1) as rig:
        control = await rig.connected()
        assert rig.pinger is not None
        rig.pinger.cancel()
        control.close()
        await _until(lambda: rig.live.cut_at)
        conn = await rig.conn()
        await conn.read(5)
        await conn.send({"refused": "used"})
        row = await rig.ended()
        assert (row.state, row.end_reason) == ("failed", swap.LOST)


async def _cut_and_redialled(rig: Any, control: Conn) -> Conn:
    """The control connection gone, and the guest's first dial back with its hello read."""
    assert rig.pinger is not None
    rig.pinger.cancel()
    await _drained(rig.accepted)
    control.close()
    await _until(lambda: rig.live.cut_at)
    conn: Conn = await rig.conn()
    first = await conn.read(5)
    assert isinstance(first, dict) and "device" in first
    return conn


@pytest.mark.integration
@_needs_psk
async def test_rejoin_answered_with_a_chunk_is_dialled_again_and_joined_before_the_offer_opens_no_stream(
    tmp_path: Path,
) -> None:
    async with _guest_rig(tmp_path, retry_seconds=0.1) as rig:
        control = await rig.connected()
        live = rig.live
        conn = await _cut_and_redialled(rig, control)

        await conn.send_chunk(0, 0, b"a chunk for a hello")
        await _dropped(conn)
        assert live.cut_at is not None and live.reason is None, "still cut off, and still on"

        again = await rig.conn()
        await again.read(5)
        await again.send(hello(rig.host, "host", rig.secret, os.urandom(32), session=_HOST_SESSION))
        rig.pinger = asyncio.create_task(_keep_pinging(again))
        await _until(lambda: live.cut_at is None)
        assert live.streams == {}, "nothing to receive before the offer is answered"
        assert rig.accepted.empty()


@pytest.mark.integration
@_needs_psk
async def test_rejoin_answered_by_another_device_ends_the_guest_as_the_wrong_device(
    tmp_path: Path,
) -> None:
    async with _guest_rig(tmp_path, retry_seconds=0.1) as rig:
        control = await rig.connected()
        conn = await _cut_and_redialled(rig, control)

        await conn.send(hello(_device(), "host", rig.secret, os.urandom(32), session=_HOST_SESSION))

        row = await rig.ended()
        assert (row.state, row.end_reason) == ("ended", swap.WRONG_DEVICE)


@pytest.mark.integration
@_needs_psk
async def test_rejoin_that_drops_while_saying_its_answer_again_leaves_the_next_cut_to_dial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Joined again, the session is no longer cut off, so the dialling stops even though the
    answer did not go: the connection's own reader cuts it off again if it has gone, and that
    cut dials anew."""
    async with _guest_rig(tmp_path, retry_seconds=0.1) as rig:
        control = await rig.connected()
        live = rig.live

        async def dropped(_conn: Conn) -> None:
            raise ConnectionResetError("the tunnel went again")

        monkeypatch.setattr(live, "say_again", dropped)
        conn = await _cut_and_redialled(rig, control)
        await conn.send(hello(rig.host, "host", rig.secret, os.urandom(32), session=_HOST_SESSION))
        rig.pinger = asyncio.create_task(_keep_pinging(conn))
        await _until(lambda: live.cut_at is None)

        await asyncio.sleep(0.4)
        assert rig.accepted.empty(), "no dial while the session is joined"
        assert not any(
            task.get_name().startswith("swap.redial.") and not task.done() for task in live.tasks
        )


# --- the guest's tunnel is the one chosen beside Join ---------------------------------------------


async def test_join_goes_through_the_tunnel_chosen_beside_it_or_the_one_chosen_last(
    tmp_path: Path,
) -> None:
    remembered: dict[str, str | None] = {"now": None}

    async def read_route() -> str | None:
        return remembered["now"]

    database = await _database(tmp_path / "guest.sqlite3")
    sessions = _sessions(database, tmp_path, egress=_Egress(None), read_route=read_route)
    try:

        def token() -> str:
            return token_module.mint(
                "8.8.4.4", 40000, _device().id, sessions.now(), secret=os.urandom(32)
            ).text

        async def join(**chosen: Any) -> str:
            return await sessions.join(
                viewer_id=VIEWER,
                token_text=token(),
                dest_folder_id="folder",
                master_key=_MASTER,
                **chosen,
            )

        # Nothing chosen and nothing chosen before: refused, and the words name the chooser.
        with pytest.raises(SwapRefused) as refused:
            await join()
        assert str(refused.value) == swap.NO_TUNNEL
        assert "under Tunnel" in swap.NO_TUNNEL and "Settings" not in swap.NO_TUNNEL

        chosen = sessions.live(await join(tunnel_id="tunnel-chosen"))
        assert isinstance(chosen, swap.GuestSession) and chosen.route == "tunnel-chosen"

        remembered["now"] = "tunnel-last-time"
        last = sessions.live(await join())
        assert isinstance(last, swap.GuestSession) and last.route == "tunnel-last-time"
        beside = sessions.live(await join(tunnel_id="tunnel-chosen"))
        assert isinstance(beside, swap.GuestSession) and beside.route == "tunnel-chosen", (
            "the tunnel chosen beside Join wins over the one chosen last time"
        )
    finally:
        for session_id in list(sessions._live):
            await sessions.end(session_id)
        await database.close()


async def test_join_with_nothing_chosen_beside_it_dials_through_the_tunnel_stored_for_joining(
    tmp_path: Path,
) -> None:
    """The application's own reader of the stored choice, handed to the sessions as the
    composition root hands it: the tunnel saved on the swap screens is the guest's route, and
    clearing it leaves the guest with no tunnel rather than the downloads' one."""
    from sift.kernel.access import Role
    from sift.slices.settings_hub.service import SettingsService
    from sift.testing.fixtures import create_user
    from sift.wiring.swapping import _guest_tunnel

    database = await _database(tmp_path / "guest-stored.sqlite3")
    hub = SettingsService(database)
    admin = await create_user(database, Role.ADMIN)
    sessions = _sessions(database, tmp_path, egress=_Egress(None), read_route=_guest_tunnel(hub))
    try:

        async def join() -> str:
            text = token_module.mint(
                "8.8.4.4", 40000, _device().id, sessions.now(), secret=os.urandom(32)
            ).text
            return await sessions.join(
                viewer_id=VIEWER, token_text=text, dest_folder_id="folder", master_key=_MASTER
            )

        await hub.apply(admin, {swap_slice.GUEST_TUNNEL_KEY: "01KZTUNNEL0000000000000002"})
        joined = sessions.live(await join())
        assert isinstance(joined, swap.GuestSession)
        assert joined.route == "01KZTUNNEL0000000000000002"

        await hub.apply(admin, {swap_slice.GUEST_TUNNEL_KEY: ""})
        with pytest.raises(SwapRefused) as refused:
            await join()
        assert str(refused.value) == swap.NO_TUNNEL
    finally:
        for session_id in list(sessions._live):
            await sessions.end(session_id)
        await database.close()


async def test_join_a_token_naming_this_sides_own_exit_is_refused_before_dialling(
    tmp_path: Path,
) -> None:
    """Both tunnels on one VPN server: a dial from there is answered by the server itself and
    never reaches the host, so the Join says so and nothing is queued or dialled. An exit that
    differs, or one that could not be read, joins as before."""
    exits = {"tunnel-same": "8.8.4.4", "tunnel-other": "8.8.8.8", "tunnel-unread": None}
    asked: list[str] = []

    async def exit_of(route: str) -> str | None:
        asked.append(route)
        return exits[route]

    database = await _database(tmp_path / "guest.sqlite3")
    jobs = _Jobs()
    sessions = _sessions(database, tmp_path, jobs=jobs, egress=_Egress(None), exit_of=exit_of)
    try:

        async def join(tunnel_id: str) -> str:
            token = token_module.mint(
                "8.8.4.4", 40000, _device().id, sessions.now(), secret=os.urandom(32)
            )
            return await sessions.join(
                viewer_id=VIEWER,
                token_text=token.text,
                dest_folder_id="folder",
                master_key=_MASTER,
                tunnel_id=tunnel_id,
            )

        with pytest.raises(SwapRefused) as refused:
            await join("tunnel-same")
        assert str(refused.value) == swap.SAME_SERVER
        assert sessions._live == {} and jobs.queued == [], "nothing queued or dialled"

        assert sessions.live(await join("tunnel-other")) is not None
        assert sessions.live(await join("tunnel-unread")) is not None
        assert asked == ["tunnel-same", "tunnel-other", "tunnel-unread"]
    finally:
        for session_id in list(sessions._live):
            await sessions.end(session_id)
        await database.close()


@_needs_psk
async def test_join_a_token_naming_this_sides_own_endpoint_is_refused_before_the_exit_is_read(
    tmp_path: Path,
) -> None:
    """Two configurations for one VPN server leave from different exits, and a dial between them is
    answered by the server all the same. So a token naming the server this side's tunnel connects
    to is refused whatever the exits say, before the exit is read. Another server joins; so does a
    version 1 token, which carries no server, when the exits differ."""
    servers = {"tunnel-same": "9.9.9.9", "tunnel-other": "8.8.8.8", "tunnel-unread": None}
    asked: list[str] = []

    async def server_of(route: str) -> str | None:
        asked.append(f"server {route}")
        return servers[route]

    async def exit_of(route: str) -> str | None:
        asked.append(f"exit {route}")
        return "8.8.8.4"

    database = await _database(tmp_path / "guest.sqlite3")
    jobs = _Jobs()
    sessions = _sessions(
        database,
        tmp_path,
        jobs=jobs,
        egress=_Egress(None),
        exit_of=exit_of,
        server_of=server_of,
    )
    try:

        async def join(tunnel_id: str, *, server: str | None = "9.9.9.9", v1: bool = False) -> str:
            token = token_module.mint(
                "8.8.4.4",
                40000,
                _device().id,
                sessions.now(),
                secret=os.urandom(32),
                server=server,
            )
            text = token.text
            if v1:
                raw = base64.b32decode(text.replace("-", "") + "====")
                text = base64.b32encode(bytes([1]) + raw[1:63]).decode().rstrip("=")
            return await sessions.join(
                viewer_id=VIEWER,
                token_text=text,
                dest_folder_id="folder",
                master_key=_MASTER,
                tunnel_id=tunnel_id,
            )

        with pytest.raises(SwapRefused) as refused:
            await join("tunnel-same")
        assert str(refused.value) == swap.SAME_SERVER
        assert sessions._live == {} and jobs.queued == [], "nothing queued or dialled"
        assert asked == ["server tunnel-same"], "refused on the server, the exit never asked"

        asked.clear()
        assert sessions.live(await join("tunnel-other")) is not None
        assert sessions.live(await join("tunnel-unread")) is not None
        assert asked == [
            "server tunnel-other",
            "exit tunnel-other",
            "server tunnel-unread",
            "exit tunnel-unread",
        ]

        asked.clear()
        assert sessions.live(await join("tunnel-same", v1=True)) is not None
        assert asked == ["exit tunnel-same"], "a version 1 token has no server to compare"
    finally:
        for session_id in list(sessions._live):
            await sessions.end(session_id)
        await database.close()


@_needs_psk
async def test_join_a_tunnel_whose_server_or_exit_cannot_be_read_is_compared_on_what_can(
    tmp_path: Path,
) -> None:
    """A server that cannot be read leaves the exit to decide, so the same exit is still refused.
    An exit that cannot be read either is unknown, and unknown is not the same: the join goes on."""
    from sift.kernel.tunnels import TunnelError

    exits: dict[str, str | OSError] = {"tunnel-same": "8.8.4.4", "tunnel-unread": OSError("down")}

    async def server_of(route: str) -> str | None:
        raise TunnelError("the tunnel is not up")

    async def exit_of(route: str) -> str | None:
        found = exits[route]
        if isinstance(found, OSError):
            raise found
        return found

    database = await _database(tmp_path / "guest.sqlite3")
    jobs = _Jobs()
    sessions = _sessions(
        database, tmp_path, jobs=jobs, egress=_Egress(None), exit_of=exit_of, server_of=server_of
    )
    try:

        async def join(tunnel_id: str) -> str:
            token = token_module.mint(
                "8.8.4.4",
                40000,
                _device().id,
                sessions.now(),
                secret=os.urandom(32),
                server="9.9.9.9",
            )
            return await sessions.join(
                viewer_id=VIEWER,
                token_text=token.text,
                dest_folder_id="folder",
                master_key=_MASTER,
                tunnel_id=tunnel_id,
            )

        with pytest.raises(SwapRefused) as refused:
            await join("tunnel-same")
        assert str(refused.value) == swap.SAME_SERVER
        assert sessions.live(await join("tunnel-unread")) is not None
    finally:
        for session_id in list(sessions._live):
            await sessions.end(session_id)
        await database.close()


@_needs_psk
async def test_start_a_hosts_token_carries_its_tunnels_endpoint(tmp_path: Path) -> None:
    """The host reads its own tunnel's server into the token; unread, the token says unknown."""
    read: list[str] = []

    async def server_of(route: str) -> str | None:
        read.append(route)
        return "9.9.9.9"

    for reader, carried in ((server_of, "9.9.9.9"), (None, None)):
        database = await _database(tmp_path / f"host-{carried}.sqlite3")
        sessions = _sessions(database, tmp_path, server_of=reader)
        try:
            started = await _start_host(sessions)
            assert started.token.server == carried
            assert token_module.parse(started.token.text, sessions.now()).server == carried
        finally:
            for session_id in list(sessions._live):
                await sessions.end(session_id)
            await database.close()
    assert read == ["tunnel-host"]


@_needs_psk
async def test_start_a_hosts_token_says_unknown_for_a_server_that_cannot_be_read(
    tmp_path: Path,
) -> None:
    """A tunnel whose server cannot be read still hosts: the token says unknown, and the guest
    compares exits alone."""
    from sift.kernel.tunnels import TunnelError

    async def server_of(route: str) -> str | None:
        raise TunnelError("the tunnel is not up")

    database = await _database(tmp_path / "host.sqlite3")
    sessions = _sessions(database, tmp_path, server_of=server_of)
    try:
        started = await _start_host(sessions)
        assert started.token.server is None
        assert token_module.parse(started.token.text, sessions.now()).server is None
    finally:
        for session_id in list(sessions._live):
            await sessions.end(session_id)
        await database.close()


async def test_join_the_setting_for_the_guests_tunnel_round_trips(tmp_path: Path) -> None:
    from sift.kernel.access import Role
    from sift.kernel.settings_registry import get_registered
    from sift.slices.settings_hub.service import SettingsService
    from sift.testing.fixtures import create_user

    database = await _database(tmp_path / "settings.sqlite3")
    try:
        hub = SettingsService(database)
        assert await hub.get_app(swap_slice.GUEST_TUNNEL_KEY) == ""
        admin = await create_user(database, Role.ADMIN)
        await hub.apply(admin, {swap_slice.GUEST_TUNNEL_KEY: "01KZTUNNEL0000000000000001"})
        assert await hub.get_app(swap_slice.GUEST_TUNNEL_KEY) == "01KZTUNNEL0000000000000001"
        setting = get_registered(swap_slice.GUEST_TUNNEL_KEY)
        assert setting is not None
        with pytest.raises(ValueError, match="choose one of your tunnels"):
            setting.validate("../not a tunnel")
    finally:
        await database.close()


# --- Activity: waiting for them, then the rate's own estimate -------------------------------------


@pytest.mark.integration
@_needs_psk
async def test_cut_off_or_waiting_activity_says_so_and_then_the_rate_says_how_long(
    tmp_path: Path,
) -> None:
    (chore,) = [one for one in HOUSEKEEPING if one.job_type == swap.SWAP_SESSION]
    assert not chore.priced and chore.waiting == "Waiting for them"
    async with _host_rig(tmp_path, {"a": b"x" * 1000}) as rig:
        swap.register_handlers(rig.sessions)
        assert own_estimate(swap.SWAP_SESSION) == OwnEstimate(waiting=True)
        await rig.join()
        assert rig.sessions.time_left() == OwnEstimate(waiting=True), "connected is not moving"
        await rig.transferring("a")
        assert rig.sessions.time_left() == OwnEstimate(seconds=None), "measuring, not waiting"
        live = rig.live
        live.pace_bps = 800
        assert rig.sessions.time_left() == OwnEstimate(seconds=10)
        live.cut_at = 1
        assert rig.sessions.time_left() == OwnEstimate(waiting=True)
        live.cut_at = None


# --- the row: a column beside the step --------------------------------------------------------------


async def test_cut_off_a_swap_table_from_before_gains_its_column_and_keeps_its_rows(
    tmp_path: Path,
) -> None:
    """Version one's table, as somebody who upgrades has it: the column is added, nothing rebuilt,
    so no row goes and no chunk a guest verified is cascaded away with it."""
    from sift.slices.swap import schema

    database = await _database(tmp_path / "old.sqlite3")
    try:
        async with database.write() as connection:
            await connection.execute("DROP TABLE swap_manifests")
            await connection.execute("DROP TABLE swap_sessions")
            await connection.execute("DROP TABLE swap_device")
            await schema.initialize(connection, on_disk=0)
            await connection.execute("ALTER TABLE swap_sessions DROP COLUMN cut_off_at")
            await connection.execute(
                "INSERT INTO swap_sessions (id, role, state, started_at) VALUES"
                " ('01KZOLDSESSION00000000001', 'guest', 'transferring', 1)"
            )
            await connection.execute(
                "INSERT INTO swap_manifests (session_id, file_key, size, chunk_size, updated_at)"
                " VALUES ('01KZOLDSESSION00000000001', 'k', 1, 1, 1)"
            )
            await schema.initialize(connection, on_disk=1)
        store = SessionStore(database)
        row = await store.get("01KZOLDSESSION00000000001")
        assert row is not None and row.cut_off_at is None and row.state == "transferring"
        assert await store.manifest("01KZOLDSESSION00000000001", "k") is not None
        assert await store.cut_off(row.id, 5) and not await store.cut_off(row.id, 6)
        cut = await store.get(row.id)
        assert cut is not None and (cut.state, cut.cut_off_at) == ("transferring", 5)
        assert await store.rejoined(row.id) and not await store.rejoined(row.id)
        assert await store.end(row.id, state="failed", reason=swap.LOST, now=7)
        assert not await store.cut_off(row.id, 8), "an ended session is never cut off after"
    finally:
        await database.close()


# --- a session's own parts, cut off and joined again ------------------------------------------------


class _Said:
    """A control connection that keeps what is sent on it, or fails each send the way a connection
    the tunnel took does. `then` runs before a failing send raises."""

    def __init__(self, fail: BaseException | None = None, then: Any = None) -> None:
        self.sent: list[Any] = []
        self.fail = fail
        self.then = then
        self.closed = False

    async def send(self, message: Any) -> None:
        if self.fail is not None:
            if self.then is not None:
                self.then()
            raise self.fail
        self.sent.append(message)

    def close(self) -> None:
        self.closed = True


async def test_cut_off_is_written_once_waits_out_its_token_once_and_never_after_the_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _guest_rig(tmp_path, run=False) as rig:
        live = rig.live
        written: list[int] = []
        real = rig.sessions.store.cut_off

        async def counted(session_id: str, when: int) -> bool:
            written.append(when)
            return await real(session_id, when)

        monkeypatch.setattr(rig.sessions.store, "cut_off", counted)

        def waiting_out() -> int:
            return sum(1 for task in live.tasks if task.get_name().startswith("swap.expiry."))

        await live.cut()
        await live.cut()
        assert len(written) == 1, "a session already cut off is not cut off again"
        await live.resume(_Said())  # type: ignore[arg-type]
        await live.cut()
        assert len(written) == 2
        assert waiting_out() == 1, "the token is waited out once, however often it is cut"

        await rig.sessions.end(rig.id)
        await live.cut()
        assert len(written) == 2, "an ended session is never cut off after"
        row = await rig.sessions.row(rig.id)
        assert row is not None and (row.state, row.live) == ("ended", False)


async def test_a_message_sent_while_cut_off_waits_for_the_join_or_is_dropped_by_the_end(
    tmp_path: Path,
) -> None:
    async with _guest_rig(tmp_path, run=False) as rig:
        live = rig.live
        waiting = asyncio.create_task(live.send_control({"diff": 1}))
        await asyncio.sleep(0.05)
        assert not waiting.done(), "nothing to send it on until the session is joined again"

        joined = _Said()
        await live.resume(joined)  # type: ignore[arg-type]

        assert await asyncio.wait_for(waiting, 5) is True
        assert {"diff": 1} in joined.sent

        live.control = None
        live.resumed.clear()
        dropped = asyncio.create_task(live.send_control({"diff": 2}))
        await asyncio.sleep(0.05)
        await rig.sessions.end(rig.id)
        assert await asyncio.wait_for(dropped, 5) is False
        assert {"diff": 2} not in joined.sent
        live.control = joined  # type: ignore[assignment]
        assert await live.send_control({"diff": 3}) is False, "nothing is sent once it has ended"
        assert {"diff": 3} not in joined.sent


async def test_a_send_the_tunnel_failed_cuts_the_session_off_unless_it_was_joined_again_meanwhile(
    tmp_path: Path,
) -> None:
    """A send that fails on the connection the session is on is a cut, and the message waits for
    the join. One that fails on a connection a join has already replaced is tried on the new one."""
    async with _guest_rig(tmp_path, run=False) as rig:
        live = rig.live
        live.control = _Said(ConnectionResetError("the tunnel went"))  # type: ignore[assignment]
        waiting = asyncio.create_task(live.send_control({"diff": 1}))
        await _until(lambda: live.cut_at)
        assert not waiting.done()
        joined = _Said()
        await live.resume(joined)  # type: ignore[arg-type]
        assert await asyncio.wait_for(waiting, 5) is True
        assert {"diff": 1} in joined.sent

        newer = _Said()

        def joined_again() -> None:
            live.control = newer  # type: ignore[assignment]

        live.control = _Said(ConnectionResetError("an old one"), then=joined_again)  # type: ignore[assignment]
        assert await live.send_control({"diff": 2}) is True
        assert live.cut_at is None, "the old connection going is not a cut"
        assert newer.sent == [{"diff": 2}]


async def test_joined_again_after_its_done_says_it_again(tmp_path: Path) -> None:
    """The host takes the first done it hears, and the last one may have gone with the tunnel."""
    async with _guest_rig(tmp_path, run=False) as rig:
        live = rig.live
        live.done_files = {0, 1}
        live.said_done = True
        conn = _Said()

        await live.say_again(conn)  # type: ignore[arg-type]

        assert conn.sent == [{"done": {"received": 2}}]


async def test_both_ways_a_direction_that_fails_fails_the_session(tmp_path: Path) -> None:
    async with _guest_rig(tmp_path, run=False) as rig:

        async def failing() -> bool:
            raise ProtocolError("a diff asking for a file that wasn't offered")

        async def carrying() -> bool:
            await asyncio.sleep(10)
            return True

        with pytest.raises(ProtocolError, match="wasn't offered"):
            await rig.live.both_ways(failing(), carrying())


async def test_a_window_before_any_file_moves_writes_no_rate(tmp_path: Path) -> None:
    """A direction whose files do not move yet is not measured: no rate on the row, and no pace
    read from windows from before it started."""
    async with _guest_rig(tmp_path, run=False, rate_window=0.02) as rig:
        live = rig.live
        live.window_bytes = 4_000
        measuring = asyncio.create_task(live.measure())
        await asyncio.sleep(0.15)
        measuring.cancel()
        with suppress(asyncio.CancelledError):
            await measuring
        assert (live.rate_bps, live.pace_bps) == (None, None)
        row = await rig.sessions.row(rig.id)
        assert row is not None and row.rate_bps is None


async def test_a_word_for_a_direction_this_side_does_not_have_is_passed_over(
    tmp_path: Path,
) -> None:
    """A guest of a swap one way has nothing to answer a diff about, and a host of one has no offer
    to take: each is passed over as an unknown word is, and the session goes on."""
    for side in ("g", "h"):
        (tmp_path / side).mkdir()
    async with _guest_rig(tmp_path / "g", run=False) as guest_rig:
        guest = guest_rig.live
        await guest.on_message({"diff": {"wanted": ["a"]}})
        assert guest.sending_half() is None and guest.reason is None
    async with _host_rig(tmp_path / "h", run=False) as host_rig:
        host = host_rig.live
        await host.on_message({"offer": {"files": []}})
        assert host.receiving_half() is None and host.reason is None
        assert not host.diff_in.done()
