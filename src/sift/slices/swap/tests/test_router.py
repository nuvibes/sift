# SPDX-License-Identifier: AGPL-3.0-or-later
"""The session's routes, over HTTP in this test's own event loop: Start answers the token and its
sentence, a tunnel that cannot host and a guest with no tunnel say the words, the session reads
back, End ends it, and the device id is made once and reset.

Who may call them is the authz matrix's (`tests/gates/test_authz_matrix.py`); here `require_admin`
and the CSRF check are stood in for, so what is asserted is what an admitted press does.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from sift.kernel import wiring
from sift.kernel.tunnels import TunnelError
from sift.kernel.wiring import provide
from sift.slices.auth import csrf_protect, master_key, require_admin
from sift.slices.swap import lock
from sift.slices.swap.router import router
from sift.slices.swap.session import NO_TUNNEL, SESSIONS
from sift.slices.swap.tests.test_session import _MASTER, _database, _Hoster, _sessions
from sift.slices.swap.token import SENTENCE

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not lock.PSK_AVAILABLE, reason="TLS with a key needs CPython 3.13"),
]


class _Refusing(_Hoster):
    async def host_on(self, tunnel_id: str, **_kwargs: Any) -> Any:
        raise TunnelError("This tunnel's provider did not give it a port.")


class _Library:
    async def get_folder(self, folder_id: str) -> object | None:
        return object() if folder_id == "folder" else None


@pytest.fixture
async def world(
    tmp_path: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, Any, _Hoster, dict[str, str]]]:
    database = await _database(tmp_path / "swap.sqlite3")
    hoster = _Hoster()
    route = {"now": "direct"}

    async def read_route() -> str:
        return route["now"]

    sessions = _sessions(database, tmp_path, hoster=hoster, read_route=read_route)
    app = FastAPI()
    provide(app, SESSIONS, sessions)
    provide(app, wiring.LIBRARY, _Library())
    app.include_router(router, prefix="/api")
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(id="admin")
    app.dependency_overrides[csrf_protect] = lambda: None
    app.dependency_overrides[master_key] = lambda: _MASTER
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://sift.test") as client:
            yield client, sessions, hoster, route
    finally:
        await database.close()


async def test_start_read_end_and_the_device(world: Any) -> None:
    client, _live, hoster, route = world
    body = {"chosen": [{"kind": "person", "id": "p1"}], "tunnel_id": "tunnel-host"}
    started = await client.post("/api/swap/start", json=body)
    assert started.status_code == 200, started.text
    answer = started.json()
    assert answer["sentence"] == SENTENCE
    assert len(answer["token"].replace("-", "")) == 108

    read = (await client.get(f"/api/swap/sessions/{answer['session_id']}")).json()
    assert (read["state"], read["role"], read["token"], read["code"]) == (
        "waiting",
        "host",
        answer["token"],
        None,
    )

    device = (await client.get("/api/swap/device")).json()
    assert device["device_id"] and not device["locked"]
    refused = await client.post("/api/swap/device/reset")
    assert refused.status_code == 409, "not while a swap runs"

    ended = (await client.post(f"/api/swap/sessions/{answer['session_id']}/end")).json()
    assert (ended["state"], ended["end_reason"], ended["token"]) == ("ended", "ended by you", None)
    assert hoster.stopped == ["tunnel-host"]

    reset = (await client.post("/api/swap/device/reset")).json()
    assert reset["device_id"] and reset["device_id"] != device["device_id"]

    # A guest with no tunnel of its own is told so before anything is dialled.
    joined = await client.post(
        "/api/swap/join", json={"token": answer["token"], "dest_folder_id": "folder"}
    )
    assert joined.status_code == 409 and joined.json()["detail"] == NO_TUNNEL
    assert joined.headers["Sift-Field"] == "tunnel_id", "said under the Tunnel chooser"
    route["now"] = "tunnel-guest"
    nowhere = await client.post(
        "/api/swap/join", json={"token": answer["token"], "dest_folder_id": "not-a-folder"}
    )
    assert nowhere.status_code == 422
    assert nowhere.headers["Sift-Field"] == "dest_folder_id"
    assert (await client.get("/api/swap/sessions/no-such-swap")).status_code == 404


async def test_a_tunnel_that_cannot_host_says_the_words(world: Any, tmp_path: Path) -> None:
    client, sessions, _hoster, _route = world
    sessions.hoster = _Refusing()
    body = {"chosen": [{"kind": "person", "id": "p1"}], "tunnel_id": "tunnel-host"}
    refused = await client.post("/api/swap/start", json=body)
    assert refused.status_code == 409
    assert refused.json()["detail"] == "This tunnel's provider did not give it a port."
    assert not sessions.any_live()


async def test_with_the_keys_locked_the_device_is_not_made_and_cannot_be_reset(
    world: Any,
) -> None:
    """The device key is sealed with the admin's password, so until it is entered there is no id
    to show and none to reset, and the refusal says what to do rather than failing."""
    client, *_rest = world
    app = client._transport.app
    app.dependency_overrides[master_key] = lambda: None

    locked = (await client.get("/api/swap/device")).json()
    assert (locked["device_id"], locked["locked"]) == (None, True)
    refused = await client.post("/api/swap/device/reset")
    assert refused.status_code == 409
    assert refused.json()["detail"].startswith("Your saved keys are locked.")

    app.dependency_overrides[master_key] = lambda: _MASTER
    made = (await client.get("/api/swap/device")).json()
    assert made["device_id"] and not made["locked"]


async def test_two_saved_filters_at_once_are_refused_with_the_words(world: Any) -> None:
    client, sessions, _hoster, _route = world
    body = {
        "chosen": [{"kind": "filter", "id": "a"}, {"kind": "filter", "id": "b"}],
        "tunnel_id": "tunnel-host",
    }

    refused = await client.post("/api/swap/start", json=body)

    assert refused.status_code == 422
    assert not sessions.any_live()


async def test_a_token_from_another_device_joins_and_the_session_reads_back(world: Any) -> None:
    import os
    import time

    from sift.slices.swap import token
    from sift.slices.swap.device import device_id_of

    client, sessions, _hoster, route = world
    route["now"] = "tunnel-guest"
    elsewhere = device_id_of(os.urandom(32))
    made = token.mint("8.8.4.4", 40000, elsewhere, int(time.time()))

    joined = await client.post(
        "/api/swap/join", json={"token": made.text, "dest_folder_id": "folder"}
    )

    assert joined.status_code == 200, joined.text
    session_id = joined.json()["session_id"]
    read = (await client.get(f"/api/swap/sessions/{session_id}")).json()
    assert (read["role"], read["peer_device"]) == ("guest", elsewhere)
    await client.post(f"/api/swap/sessions/{session_id}/end")
    assert not sessions.any_live()


async def test_a_join_refusal_names_the_part_of_the_form_it_is_about(world: Any) -> None:
    """A token that does not read is the token's; a tunnel on the host's own server is the
    Tunnel chooser's, so the form says it there and not under the token."""
    import os
    import time

    from sift.slices.swap import token
    from sift.slices.swap.device import device_id_of
    from sift.slices.swap.session import SAME_SERVER

    client, sessions, _hoster, route = world
    route["now"] = "tunnel-guest"
    unread = await client.post(
        "/api/swap/join", json={"token": "nonsense", "dest_folder_id": "folder"}
    )
    assert unread.status_code == 409 and unread.headers["Sift-Field"] == "token"

    async def server_of(_route: str) -> str:
        return "9.9.9.9"

    sessions.server_of = server_of
    made = token.mint(
        "8.8.4.4", 40000, device_id_of(os.urandom(32)), int(time.time()), server="9.9.9.9"
    )
    same = await client.post(
        "/api/swap/join", json={"token": made.text, "dest_folder_id": "folder"}
    )
    assert same.status_code == 409 and same.json()["detail"] == SAME_SERVER
    assert same.headers["Sift-Field"] == "tunnel_id"
    assert not sessions.any_live()


async def test_a_code_is_answered_only_where_there_is_one(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No such swap is the miss; a swap with no code yet is refused with the words; an answered
    code reads the session back."""
    client, sessions, _hoster, _route = world
    body = {"chosen": [{"kind": "person", "id": "p1"}], "tunnel_id": "tunnel-host"}
    session_id = (await client.post("/api/swap/start", json=body)).json()["session_id"]

    missing = await client.post("/api/swap/sessions/no-such-swap/code", json={"match": True})
    assert missing.status_code == 404
    early = await client.post(f"/api/swap/sessions/{session_id}/code", json={"match": True})
    assert early.status_code == 409
    assert early.json()["detail"] == "There's no code to compare yet."

    answered: list[tuple[str, bool]] = []

    async def answer(one: str, match: bool, **_said: object) -> None:
        answered.append((one, match))

    monkeypatch.setattr(sessions, "answer_code", answer)
    read = await client.post(f"/api/swap/sessions/{session_id}/code", json={"match": True})
    assert read.status_code == 200 and read.json()["id"] == session_id
    assert answered == [(session_id, True)]

    assert (await client.post("/api/swap/sessions/no-such-swap/end")).status_code == 404
    await client.post(f"/api/swap/sessions/{session_id}/end")


async def test_a_join_goes_through_the_tunnel_chosen_beside_it_and_each_tunnel_names_its_server(
    world: Any,
) -> None:
    import os
    import time

    from sift.kernel.tunnels import TUNNELS, TunnelView
    from sift.slices.swap import token
    from sift.slices.swap.device import device_id_of

    client, sessions, _hoster, _route = world

    def view(tunnel_id: str, *, up: bool) -> TunnelView:
        return TunnelView(
            id=tunnel_id,
            name=tunnel_id,
            enabled=True,
            running=up,
            up=up,
            draining=False,
            last_handshake_at=1,
            endpoint="198.51.100.7",
        )

    class _Tunnels:
        async def list(self) -> list[TunnelView]:
            return [view("tunnel-up", up=True), view("tunnel-down", up=False)]

    provide(client._transport.app, TUNNELS, _Tunnels())
    listed = (await client.get("/api/swap/tunnels")).json()
    assert [(one["id"], one["endpoint"]) for one in listed] == [
        ("tunnel-up", "198.51.100.7"),
        ("tunnel-down", None),
    ], "the server a tunnel is on, only while it is up"

    def made() -> str:
        return token.mint("8.8.4.4", 40000, device_id_of(os.urandom(32)), int(time.time())).text

    gone = await client.post(
        "/api/swap/join",
        json={"token": made(), "dest_folder_id": "folder", "tunnel_id": "tunnel-removed"},
    )
    assert gone.status_code == 409 and gone.json()["detail"] == NO_TUNNEL
    joined = await client.post(
        "/api/swap/join",
        json={"token": made(), "dest_folder_id": "folder", "tunnel_id": "tunnel-up"},
    )
    assert joined.status_code == 200, joined.text
    live = sessions.live(joined.json()["session_id"])
    assert live is not None and live.route == "tunnel-up", "the chosen tunnel, not the default"
    await client.post(f"/api/swap/sessions/{live.id}/end")


_TWO_FILTERS = [{"kind": "filter", "id": "a"}, {"kind": "filter", "id": "b"}]


async def test_send_and_receive_is_refused_under_the_folder_until_a_folder_of_this_library_is_named(
    world: Any,
) -> None:
    client, sessions, _hoster, _route = world
    body = {"chosen": [{"kind": "person", "id": "p1"}], "tunnel_id": "tunnel-host", "two_way": True}

    for folder in ({}, {"dest_folder_id": "not-a-folder"}):
        refused = await client.post("/api/swap/start", json={**body, **folder})
        assert refused.status_code == 422, folder
        assert refused.headers["Sift-Field"] == "dest_folder_id"
    assert not sessions.any_live(), "nothing is hosted for a swap with nowhere to put what comes"

    started = await client.post("/api/swap/start", json={**body, "dest_folder_id": "folder"})
    assert started.status_code == 200, started.text
    await client.post(f"/api/swap/sessions/{started.json()['session_id']}/end")


async def test_two_saved_filters_are_refused_at_they_match_and_before_start_with_the_words(
    world: Any,
) -> None:
    """The guest's They match in a swap both ways, and the weight before Start, take the same
    picks Start does, and refuse two saved filters in the same words."""
    client, sessions, _hoster, _route = world
    body = {"chosen": [{"kind": "person", "id": "p1"}], "tunnel_id": "tunnel-host"}
    session_id = (await client.post("/api/swap/start", json=body)).json()["session_id"]

    code = await client.post(
        f"/api/swap/sessions/{session_id}/code", json={"match": True, "chosen": _TWO_FILTERS}
    )
    weighed = await client.post("/api/swap/weigh", json={"chosen": _TWO_FILTERS})

    for refused in (code, weighed):
        assert refused.status_code == 422
        assert refused.json()["detail"] == "A swap can offer one saved filter."
    assert sessions.facts(session_id).code is None, "nothing was answered"
    await client.post(f"/api/swap/sessions/{session_id}/end")


async def test_the_weight_of_an_answer_is_asked_of_its_own_session_and_refused_before_an_offer(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _hoster, _route = world
    body = {"chosen": [{"kind": "person", "id": "p1"}], "tunnel_id": "tunnel-host"}
    session_id = (await client.post("/api/swap/start", json=body)).json()["session_id"]
    answer = {"skipped": [1], "unticked": ["key-a"]}

    missing = await client.post("/api/swap/sessions/no-such-swap/weigh", json=answer)
    assert missing.status_code == 404
    early = await client.post(f"/api/swap/sessions/{session_id}/weigh", json=answer)
    assert early.status_code == 409
    assert early.json()["detail"] == "There's no offer to answer yet."

    asked: list[tuple[str, Any]] = []

    def weigh(one: str, taken: Any) -> tuple[int, int]:
        asked.append((one, taken))
        return 2, 3_000

    monkeypatch.setattr(sessions, "weigh_answer", weigh)
    weighed = await client.post(f"/api/swap/sessions/{session_id}/weigh", json=answer)
    assert weighed.status_code == 200, weighed.text
    assert (weighed.json()["files"], weighed.json()["bytes"]) == (2, 3_000)
    ((one, taken),) = asked
    assert (one, taken.skipped, taken.unticked) == (session_id, {1}, {"key-a"})
    await client.post(f"/api/swap/sessions/{session_id}/end")
