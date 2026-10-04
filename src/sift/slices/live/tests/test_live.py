# SPDX-License-Identifier: AGPL-3.0-or-later
"""The live feed, driven against the real application.

A WebSocket escapes the route table's check, the same-origin policy and per-request authorization,
so each is tested by doing it. The emit side runs through real routes that change permissions,
because what matters is that a write which moved somebody's view announces it.
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import FastAPI, WebSocket, status
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect

from sift.kernel.access import Role, Viewer
from sift.kernel.audience import EVERY_ADMIN, Audience
from sift.kernel.changes import About, ChangeBus
from sift.kernel.config import get_settings
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.jobs.worker_pool import WorkerPool
from sift.main import create_app
from sift.slices.live import MAX_CONNECTIONS_PER_USER
from sift.slices.live.router import LiveState, _rest, _send, stream_changes
from sift.slices.live.tuning import BEAT_SECONDS, FULL_AGAIN_SECONDS
from sift.testing.auth import establish_session
from sift.testing.jobs import set_disabled

pytestmark = [pytest.mark.integration]

STREAM = "/api/live/stream"
STATE = "/api/live"

A_TAG = "01HX0000000000000000000T01"

#: Long enough that a beat which was going to send something has had two chances to.
QUIET_SECONDS = BEAT_SECONDS * 2.5


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))

    # No workers: each test asks what one write announced, and housekeeping would add its own.
    async def _no_workers(self: WorkerPool) -> None: ...

    monkeypatch.setattr(WorkerPool, "start", _no_workers)
    get_settings.cache_clear()
    yield create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def sign_in(client: TestClient, role: str) -> str:
    """A real user of this role, with a real session. Returns the user's id."""
    db_path = client.app.state.database.path  # type: ignore[attr-defined]
    user_id, token, csrf = establish_session(
        db_path, role=role, username=f"live-{role}", password="Live-Test-Passw0rd!"
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf
    return user_id


def bus_of(client: TestClient) -> ChangeBus:
    return client.app.state.changes  # type: ignore[attr-defined,no-any-return]


def only(user_id: str) -> Audience:
    return Audience(frozenset({user_id}))


def messages_waiting(socket: object) -> int:
    """How much this connection has been sent, without reading: a read that waits would hang on
    "nothing" instead of failing."""
    waiting: int = socket._send_rx.statistics().current_buffer_used  # type: ignore[attr-defined]
    return waiting


def a_tag_there_to_share(client: TestClient) -> str:
    """A tag that exists, written straight to the database so the test's one write is the share.
    Sharing an id nothing answers to is refused before any grant is made."""
    database = client.app.state.database  # type: ignore[attr-defined]
    portal = client.portal
    assert portal is not None, "the client is used inside its context, where its portal is open"
    portal.call(
        database.execute,
        "INSERT OR IGNORE INTO tags (id, name, created_at) VALUES (?, 'Shared', 0)",
        (A_TAG,),
    )
    return A_TAG


def share_a_tag_with(client: TestClient, guest_id: str) -> int:
    """An admin shares one thing with one guest, through the route the panel calls."""
    a_tag_there_to_share(client)
    answer = client.put(
        "/api/sharing",
        json={
            "object_type": "tag",
            "object_id": A_TAG,
            "subject_user_id": guest_id,
            "effect": "share",
        },
    )
    code: int = answer.status_code
    return code


# --- the emit side ------------------------------------------------------------------------------


def test_sharing_something_tells_the_account_it_was_shared_with(client: TestClient) -> None:
    """A share made by an admin is announced to the guest it was shared with, through the route."""
    guest_id = sign_in(client, "guest")
    sign_in(client, "admin")
    waiting = bus_of(client).subscribe(guest_id)

    assert share_a_tag_with(client, guest_id) == 200

    assert waiting.take(as_admin=True).about == (About.LIBRARY,)


def test_taking_a_share_back_tells_them_too(client: TestClient) -> None:
    """Taking a share back is announced too: their screen may hold what they can no longer see."""
    guest_id = sign_in(client, "guest")
    sign_in(client, "admin")
    assert share_a_tag_with(client, guest_id) == 200
    waiting = bus_of(client).subscribe(guest_id)

    taken_back = client.post(
        "/api/sharing/revoke",
        json={
            "object_type": "tag",
            "object_id": A_TAG,
            "subject_user_id": guest_id,
            "effect": "share",
        },
    )

    assert taken_back.status_code == 200, taken_back.text
    assert waiting.take(as_admin=True).about == (About.LIBRARY,)


def test_a_share_with_somebody_else_says_nothing_to_a_bystander(client: TestClient) -> None:
    """A share announces nothing to a bystander: even an empty message says something exists."""
    guest_id = sign_in(client, "guest")
    admin_id = sign_in(client, "admin")
    bystander = bus_of(client).subscribe(admin_id)

    assert share_a_tag_with(client, guest_id) == 200

    assert bystander.take(as_admin=True).about == ()


# --- where a user stands --------------------------------------------------------------------------


def test_the_plain_read_says_where_this_account_stands(client: TestClient) -> None:
    sign_in(client, "guest")

    answer = client.get(STATE)

    assert answer.status_code == 200
    assert answer.json()["about"] == []
    assert answer.json()["marker"]


def test_the_mark_moves_when_something_of_theirs_does(client: TestClient) -> None:
    """The mark a reconnecting browser compares against moves when something of theirs does."""
    guest_id = sign_in(client, "guest")
    before = client.get(STATE).json()["marker"]

    sign_in(client, "admin")
    assert share_a_tag_with(client, guest_id) == 200
    sign_in(client, "guest")

    assert client.get(STATE).json()["marker"] != before


def test_the_mark_moves_when_a_preference_changes(client: TestClient) -> None:
    """A settings change moves the mark; a preference never raises the `cache_stamp`."""
    sign_in(client, "guest")
    before = client.get(STATE).json()["marker"]

    saved = client.put("/api/settings", json={"values": {"appearance.theme_base": "graphite"}})

    assert saved.status_code == 204
    assert client.get(STATE).json()["marker"] != before


def test_two_runs_of_the_application_never_share_a_mark(app: FastAPI) -> None:
    """Each run of the application has its own mark, so a restart never reads as "nothing moved"."""
    first = ChangeBus()
    second = ChangeBus()

    assert first.mark != second.mark, "two runs of the application agreed about where they stood"


def test_nobody_signed_in_is_told_nothing(client: TestClient) -> None:
    assert client.get(STATE).status_code == 401


# --- the socket ---------------------------------------------------------------------------------


def test_a_guest_is_told_when_something_of_theirs_moves(client: TestClient) -> None:
    """A guest is told when something of theirs moves."""
    guest_id = sign_in(client, "guest")

    with client.websocket_connect(STREAM) as socket:
        bus_of(client).publish(only(guest_id), About.LIBRARY)
        message = socket.receive_json()

    assert message["about"] == ["library"]
    assert message["marker"]


def test_an_admin_is_told_too(client: TestClient) -> None:
    admin_id = sign_in(client, "admin")

    with client.websocket_connect(STREAM) as socket:
        bus_of(client).publish(only(admin_id), About.LIBRARY)
        assert socket.receive_json()["about"] == ["library"]


def test_a_socket_with_no_session_is_refused(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(STREAM):
        pass


def test_the_socket_closes_when_the_account_is_disabled(client: TestClient) -> None:
    """Disabling an account closes its open socket, since permission is re-read on every beat."""
    guest_id = sign_in(client, "guest")

    with pytest.raises(WebSocketDisconnect), client.websocket_connect(STREAM) as socket:
        bus_of(client).publish(only(guest_id), About.LIBRARY)
        socket.receive_json()
        set_disabled(client.app.state.database.path, guest_id)  # type: ignore[attr-defined]
        for _ in range(5):
            bus_of(client).publish(only(guest_id), About.LIBRARY)
            socket.receive_json()


def test_a_socket_from_another_site_is_refused(client: TestClient) -> None:
    """A socket from another site is refused: cross-site WebSocket hijacking."""
    sign_in(client, "guest")

    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(STREAM, headers={"origin": "https://not-sift.example"}),
    ):
        pass


def test_a_socket_from_sifts_own_page_is_allowed(client: TestClient) -> None:
    guest_id = sign_in(client, "guest")

    with client.websocket_connect(STREAM, headers={"origin": "http://testserver"}) as socket:
        bus_of(client).publish(only(guest_id), About.LIBRARY)
        assert socket.receive_json()["about"] == ["library"]


# --- coming back --------------------------------------------------------------------------------


def test_a_browser_that_missed_something_is_told_to_re_read(client: TestClient) -> None:
    """A browser that missed something is told to re-read, by comparing marks at the handshake
    rather than replaying a buffer. What the message holds is the next test's."""
    sign_in(client, "guest")

    with client.websocket_connect(f"{STREAM}?since=never-this") as socket:
        time.sleep(QUIET_SECONDS)
        assert messages_waiting(socket) >= 1, "a browser that missed something was told nothing"
        assert socket.receive_json()["about"], "it was told to re-read nothing at all"


def test_the_repair_covers_everything_a_screen_can_ask_about(client: TestClient) -> None:
    """The repair names every subject a screen can ask about. `opinions` carries rows, so its repair
    is `arrivals`; `remote` carries a phone's command, which must never replay."""
    sign_in(client, "guest")

    with client.websocket_connect(f"{STREAM}?since=never-this") as socket:
        told = set(socket.receive_json()["about"])

    assert told == {about.value for about in About} - {About.OPINIONS.value, About.REMOTE.value}


def test_a_browser_is_repaired_once_and_not_on_every_beat(client: TestClient) -> None:
    """The reconciliation happens once at the handshake, not on every beat, proved with a change
    addressed to somebody else: anything after the first message is a wrong repair."""
    sign_in(client, "guest")
    stranger = "01HX000000000000000000STRA"

    with client.websocket_connect(f"{STREAM}?since=never-this") as socket:
        socket.receive_json()
        bus_of(client).publish(only(stranger), About.LIBRARY)
        time.sleep(QUIET_SECONDS)
        sent = messages_waiting(socket)

    assert sent == 0, "the connection was repaired again on a later beat"


def test_an_idle_connection_that_missed_nothing_is_sent_nothing(client: TestClient) -> None:
    """An idle connection that missed nothing is sent nothing, waited out over two beats."""
    sign_in(client, "guest")
    marker = client.get(STATE).json()["marker"]

    with client.websocket_connect(f"{STREAM}?since={marker}") as socket:
        time.sleep(QUIET_SECONDS)
        sent = messages_waiting(socket)

    assert sent == 0


def test_a_first_connection_is_told_nothing_until_something_happens(client: TestClient) -> None:
    """A fresh connection holds no mark, so it is told nothing until something happens."""
    guest_id = sign_in(client, "guest")

    with client.websocket_connect(STREAM) as socket:
        time.sleep(QUIET_SECONDS)
        assert messages_waiting(socket) == 0, "a browser that had just loaded was told to re-read"
        bus_of(client).publish(only(guest_id), About.LIBRARY)
        assert socket.receive_json()["about"] == ["library"]


def test_a_guest_on_a_live_connection_is_not_told_what_only_an_admin_may_hear(
    client: TestClient,
) -> None:
    """A guest is never told an admins-only subject. The bus holds no roles; the connection decides
    on every beat."""
    sign_in(client, "guest")
    marker = client.get(STATE).json()["marker"]

    with client.websocket_connect(f"{STREAM}?since={marker}") as socket:
        time.sleep(QUIET_SECONDS)
        bus_of(client).publish(EVERY_ADMIN, About.JOBS)
        time.sleep(QUIET_SECONDS)
        heard = messages_waiting(socket)

    assert heard == 0


def test_an_admin_on_a_live_connection_is_told_it(client: TestClient) -> None:
    """The positive control: an admin hears it, so the check above cannot pass with the feed off."""
    sign_in(client, "admin")
    marker = client.get(STATE).json()["marker"]

    with client.websocket_connect(f"{STREAM}?since={marker}") as socket:
        time.sleep(QUIET_SECONDS)
        bus_of(client).publish(EVERY_ADMIN, About.JOBS)

        assert socket.receive_json()["about"] == ["jobs"]


# --- the bounds ---------------------------------------------------------------------------------


def test_too_many_connections_for_one_account_is_refused(client: TestClient) -> None:
    """Too many connections for one account are refused; each costs a permission read a second."""
    guest_id = sign_in(client, "guest")
    bus = bus_of(client)
    for _ in range(MAX_CONNECTIONS_PER_USER):
        bus.subscribe(guest_id)

    with pytest.raises(WebSocketDisconnect), client.websocket_connect(STREAM):
        pass


def test_one_under_the_limit_is_still_let_in(client: TestClient) -> None:
    """One under the limit is let in, catching an off-by-one."""
    guest_id = sign_in(client, "guest")
    bus = bus_of(client)
    for _ in range(MAX_CONNECTIONS_PER_USER - 1):
        bus.subscribe(guest_id)

    with client.websocket_connect(STREAM) as socket:
        bus.publish(only(guest_id), About.LIBRARY)
        assert socket.receive_json()["about"] == ["library"]


def test_the_plain_read_says_so_at_the_cap(client: TestClient) -> None:
    """At the cap the plain read refuses with a retry, so a refused handshake (1006, like a dropped
    network) does not send the client round in an endless loop."""
    guest_id = sign_in(client, "guest")
    bus = bus_of(client)
    for _ in range(MAX_CONNECTIONS_PER_USER):
        bus.subscribe(guest_id)

    refused = client.get(STATE)

    assert refused.status_code == 429
    # Retry-after is for other clients; Sift's own carries its own pace.
    assert refused.headers["retry-after"] == str(FULL_AGAIN_SECONDS)


def test_the_plain_read_is_answered_one_under_the_cap(client: TestClient) -> None:
    """The positive control one under the cap, catching an off-by-one."""
    guest_id = sign_in(client, "guest")
    bus = bus_of(client)
    for _ in range(MAX_CONNECTIONS_PER_USER - 1):
        bus.subscribe(guest_id)

    assert client.get(STATE).status_code == 200


def test_a_closed_connection_gives_its_place_back(client: TestClient) -> None:
    """A closed connection gives its place back, or reloads lock the user out."""
    guest_id = sign_in(client, "guest")

    with client.websocket_connect(STREAM):
        assert bus_of(client).open_for(guest_id) == 1

    assert bus_of(client).open_for(guest_id) == 0


class _StuckReader:
    """A connection whose send never finishes, as a frozen tab or a phone out of range does."""

    def __init__(self) -> None:
        self.closed_with: int | None = None

    async def send_json(self, payload: object) -> None:
        await asyncio.sleep(3600)

    async def close(self, code: int) -> None:
        self.closed_with = code


async def test_a_reader_that_cannot_keep_up_is_let_go() -> None:
    """A reader that cannot keep up is closed; the client treats a close as "come back and ask"."""
    stuck = _StuckReader()

    kept_going = await _send(cast("WebSocket", stuck), LiveState(about=[], marker="1"))

    assert kept_going is False
    assert stuck.closed_with == 1013


class _VanishingReader:
    """A transport torn down under a send: `WebSocketDisconnect` or the server's `RuntimeError`."""

    def __init__(self, failure: BaseException) -> None:
        self._failure = failure
        self.sends = 0
        self.closed_with: int | None = None

    async def send_json(self, payload: object) -> None:
        self.sends += 1
        raise self._failure

    async def close(self, code: int) -> None:
        self.closed_with = code


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("operation on a closed transport"),
        WebSocketDisconnect(1001),
    ],
    ids=["a torn-down transport", "a close frame"],
)
async def test_a_client_that_vanishes_mid_send_ends_quietly(failure: BaseException) -> None:
    """A tab closed mid-send ends quietly, including the `RuntimeError` shape."""
    gone = _VanishingReader(failure)

    kept_going = await _send(cast("WebSocket", gone), LiveState(about=[], marker="1"))

    assert kept_going is False
    assert gone.sends == 1, "it should have tried exactly once and then given up"
    assert gone.closed_with is None, "there was nobody left to send a close code to"


def test_a_locked_session_is_refused(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A locked session is refused. A socket takes none of the routes' dependencies, so the lock is
    checked here; it is set at the resolver."""
    sign_in(client, "admin")

    async def locked(_socket: object) -> object:
        # A real viewer beside the lock, so only the lock can be what refuses.
        return SimpleNamespace(locked=True, viewer=Viewer(id="somebody", role=Role.ADMIN))

    monkeypatch.setattr(sys.modules["sift.slices.live.router"], "viewer_on", locked)

    with (
        pytest.raises(WebSocketDisconnect) as refused,
        client.websocket_connect("/api/live/stream"),
    ):
        pass

    assert refused.value.code == status.WS_1008_POLICY_VIOLATION


def test_a_session_locked_while_the_connection_is_open_is_let_go_on_the_next_beat(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lock put on while the socket is open ends it on the next beat; the handshake check has
    already passed by then."""
    sign_in(client, "admin")
    router = sys.modules["sift.slices.live.router"]
    answer_truthfully = router.viewer_on
    asked = 0

    async def locked_after_the_handshake(socket: object) -> object:
        nonlocal asked
        found = await answer_truthfully(socket)
        asked += 1
        if asked == 1:
            return found
        return SimpleNamespace(locked=True, viewer=found.viewer)

    monkeypatch.setattr(router, "viewer_on", locked_after_the_handshake)

    # Something to hear on every beat, so a connection not let go answers instead of hanging.
    with pytest.raises(WebSocketDisconnect) as ended, client.websocket_connect(STREAM) as socket:
        for _ in range(5):
            bus_of(client).publish(EVERY_ADMIN, About.LIBRARY)
            socket.receive_json()

    assert ended.value.code == status.WS_1008_POLICY_VIOLATION
    assert asked > 1, "the lock was never re-asked, so this proved only the check at the door"


class _OneBeatSocket:
    """A connection that exists only long enough to be spoken to once."""

    def __init__(self, bus: ChangeBus, *, dies_on_close: BaseException | None = None) -> None:
        self.app = SimpleNamespace(
            state=SimpleNamespace(settings=SimpleNamespace(cors_origins=()), changes=bus)
        )
        self.headers = {"origin": "http://testserver", "host": "testserver"}
        # A mark that cannot be current, so the first beat has something to send. Publishing first
        # would reach no subscription: the route subscribes inside.
        self.query_params: dict[str, str] = {"since": "not where this account stands"}
        self._dies_on_close = dies_on_close
        self.accepted = False
        self.sends = 0
        self.closed_with: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, _payload: object) -> None:
        self.sends += 1
        raise WebSocketDisconnect(1001)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code
        if self._dies_on_close is not None:
            raise self._dies_on_close


async def _run_until_it_stops(socket: _OneBeatSocket, *, allowed: bool) -> None:
    async def found(_socket: object) -> object:
        return SimpleNamespace(
            locked=not allowed,
            viewer=Viewer(id="somebody", role=Role.ADMIN),
        )

    # Through `sys.modules`: the package binds `router` to the APIRouter, not the module.
    module = sys.modules["sift.slices.live.router"]
    original = getattr(module, "viewer_on")  # noqa: B009
    setattr(module, "viewer_on", found)  # noqa: B010
    try:
        await stream_changes(cast("WebSocket", socket))
    finally:
        setattr(module, "viewer_on", original)  # noqa: B010


async def test_a_connection_that_cannot_be_sent_to_is_let_go() -> None:
    """A connection that cannot be sent to is let go and its subscription released."""
    bus = ChangeBus()
    socket = _OneBeatSocket(bus)

    await _run_until_it_stops(socket, allowed=True)

    assert socket.accepted
    assert socket.sends == 1, "it should have tried exactly once and then given up"
    assert bus.open_connections() == 0, "the connection was never given back to the bus"


@pytest.mark.parametrize(
    "how",
    [WebSocketDisconnect(1001), RuntimeError("operation on a closed transport")],
    ids=["a close frame", "a torn-down transport"],
)
async def test_a_client_already_gone_when_it_is_closed_on_is_not_an_error(
    how: BaseException,
) -> None:
    """A client gone before the route closes on it is not an error."""
    socket = _OneBeatSocket(ChangeBus(), dies_on_close=how)

    await _run_until_it_stops(socket, allowed=False)

    assert socket.closed_with == status.WS_1008_POLICY_VIOLATION


class _ClosedTabSocket(_OneBeatSocket):
    """A quiet connection whose browser has gone; only reading the disconnect ends it."""

    def __init__(self, bus: ChangeBus) -> None:
        super().__init__(bus)
        self.query_params = {}
        self.receives = 0

    async def receive(self) -> dict[str, object]:
        self.receives += 1
        # Yields first, as a real one does, so a looping beat cannot spin inside its deadline.
        await asyncio.sleep(0)
        return {"type": "websocket.disconnect", "code": 1001}


async def test_a_tab_closed_on_a_quiet_connection_gives_its_place_back() -> None:
    """A tab closed on a quiet connection gives its place back. The test client cancels the task on
    exit; a real server only learns from reading the disconnect."""
    bus = ChangeBus()
    socket = _ClosedTabSocket(bus)

    try:
        async with asyncio.timeout(BEAT_SECONDS * 5):
            await _run_until_it_stops(socket, allowed=True)
    except TimeoutError:
        pytest.fail("the beat went round again on a connection whose browser had gone")

    assert socket.sends == 0, "there was nothing to send, which is the whole point of this one"
    assert socket.receives == 1
    assert bus.open_connections() == 0, "the connection was never given back to the bus"


class _SilentReader:
    """A browser that is there and says nothing, which is every browser Sift has."""

    def __init__(self) -> None:
        self.receives = 0

    async def receive(self) -> dict[str, object]:
        self.receives += 1
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")


class _TalkingReader:
    """A browser that sends a frame, which no browser Sift has."""

    def __init__(self) -> None:
        self.receives = 0

    async def receive(self) -> dict[str, object]:
        self.receives += 1
        await asyncio.sleep(0)
        return {"type": "websocket.receive", "text": "hello"}


async def test_a_beat_with_nobody_saying_anything_waits_and_keeps_the_connection() -> None:
    """The ordinary case: one beat's wait, and the loop goes round again."""
    quiet = _SilentReader()

    still_there = await _rest(cast("WebSocket", quiet), asyncio.Event())

    assert still_there is True
    assert quiet.receives == 1


async def test_a_client_that_sends_anything_at_all_is_finished() -> None:
    """Any frame ends the connection: Sift's client sends none, and reading and dropping frames
    would spin inside the timeout."""
    chatty = _TalkingReader()

    still_there = await _rest(cast("WebSocket", chatty), asyncio.Event())

    assert still_there is False
    assert chatty.receives == 1, "it should have stopped on the first frame"
