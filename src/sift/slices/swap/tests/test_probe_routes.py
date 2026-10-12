# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tunnel speed probe's routes over HTTP: a sink hosted, read, sent to through the tunnel's
proxy, stopped; the refusals a locked key, no sink and a host nobody reaches give."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from sift.kernel.tunnels import TunnelError
from sift.slices.auth import master_key
from sift.slices.swap.tests.test_router import world  # noqa: F401 (the routes' world)

pytestmark = [pytest.mark.integration]


class _Loopback:
    """Hosts on loopback: the sink's own port is the public one."""

    def __init__(self) -> None:
        self.stopped: list[str] = []

    async def host_on(
        self, tunnel_id: str, *, target_port: int, master_key: bytes, **_: Any
    ) -> Any:
        return SimpleNamespace(public_ipv4="127.0.0.1", external_port=target_port)

    async def stop_hosting(self, tunnel_id: str) -> None:
        self.stopped.append(tunnel_id)


class _Direct:
    @contextlib.asynccontextmanager
    async def through(self, route: str | None) -> AsyncIterator[str | None]:
        yield None


async def test_a_probe_is_hosted_sent_to_read_and_stopped(world: tuple[Any, ...]) -> None:  # noqa: F811
    client, sessions, _hoster, _route = world
    hoster = _Loopback()
    sessions.probes.hoster, sessions.probes.egress = hoster, _Direct()
    hosted = await client.post("/api/swap/tunnels/t1/probe", json={"seconds": 5})
    assert hosted.status_code == 200, hosted.text
    where = hosted.json()
    assert where["seconds"] == 5 and len(where["secret"]) == 64
    sent = await client.post(
        "/api/swap/tunnels/t1/probe/send",
        json={
            "address": where["address"],
            "port": where["port"],
            "secret": where["secret"],
            "seconds": 1,
            "streams": 2,
        },
    )
    assert sent.status_code == 200, sent.text
    figures = sent.json()
    assert figures["streams"] == 2 and figures["failed"] == 0 and figures["sent"] > 0
    assert figures["rate_bps"] > 0
    # The sink reads what the sockets still hold a moment after the send returns.
    for _ in range(50):
        read = (await client.get("/api/swap/tunnels/t1/probe")).json()
        if read["received"] >= figures["sent"]:
            break
        await asyncio.sleep(0.1)
    assert read["connections"] == 2 and read["refused"] == 0 and read["received"] == figures["sent"]
    assert (await client.delete("/api/swap/tunnels/t1/probe")).status_code == 204
    assert (await client.get("/api/swap/tunnels/t1/probe")).status_code == 404
    assert hoster.stopped == ["t1"]


async def test_the_probes_refusals(world: tuple[Any, ...]) -> None:  # noqa: F811
    client, sessions, _hoster, _route = world
    # The world's hosting names an address nobody reaches: every stream fails, and the send says so.
    hosted = (await client.post("/api/swap/tunnels/t1/probe", json={"seconds": 5})).json()
    unreached = await client.post(
        "/api/swap/tunnels/t1/probe/send",
        json={
            "address": hosted["address"],
            "port": hosted["port"],
            "secret": hosted["secret"],
            "seconds": 1,
            "streams": 1,
        },
    )
    assert unreached.status_code == 409 and "reached" in unreached.json()["detail"]
    await client.delete("/api/swap/tunnels/t1/probe")
    bad_hex = await client.post(
        "/api/swap/tunnels/t1/probe/send",
        json={"address": "127.0.0.1", "port": 1, "secret": "zz" * 32, "seconds": 1, "streams": 1},
    )
    assert bad_hex.status_code == 422
    assert sessions.probes.reading("t1") is None
    app = client._transport.app
    app.dependency_overrides[master_key] = lambda: None
    locked = await client.post("/api/swap/tunnels/t1/probe", json={"seconds": 5})
    assert locked.status_code == 409 and "locked" in locked.json()["detail"]


class _Down:
    """A tunnel that is not up: hosting on it and sending through it are both refused."""

    async def host_on(self, tunnel_id: str, **_: Any) -> Any:
        raise TunnelError("The tunnel isn't connected.")

    async def stop_hosting(self, tunnel_id: str) -> None:
        return None

    @contextlib.asynccontextmanager
    async def through(self, route: str | None) -> AsyncIterator[str | None]:
        if route is not None:
            raise TunnelError("The tunnel isn't connected.")
        yield None


async def test_a_reading_before_any_byte_and_a_tunnel_that_is_down(
    world: tuple[Any, ...],  # noqa: F811
) -> None:
    client, sessions, _hoster, _route = world
    sessions.probes.hoster, sessions.probes.egress = _Loopback(), _Direct()
    assert (await client.post("/api/swap/tunnels/t2/probe", json={"seconds": 5})).status_code == 200
    quiet = (await client.get("/api/swap/tunnels/t2/probe")).json()
    assert quiet["received"] == 0 and quiet["seconds"] is None
    await client.delete("/api/swap/tunnels/t2/probe")

    down = _Down()
    sessions.probes.hoster, sessions.probes.egress = down, down
    hosted = await client.post("/api/swap/tunnels/t2/probe", json={"seconds": 5})
    assert hosted.status_code == 409 and "connected" in hosted.json()["detail"]
    sent = await client.post(
        "/api/swap/tunnels/t2/probe/send",
        json={"address": "127.0.0.1", "port": 1, "secret": "00" * 32, "seconds": 1, "streams": 1},
    )
    assert sent.status_code == 409 and "connected" in sent.json()["detail"]
