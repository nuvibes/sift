# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tunnel speed probe on loopback: a hosted sink counts what a send pushes, and takes only a
connection that opens with its secret."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest

from sift.slices.swap.probe import SECRET_BYTES, Probes


class _Hosting:
    def __init__(self, port: int) -> None:
        self.public_ipv4, self.external_port = "127.0.0.1", port


class _Hoster:
    """Hosts on loopback: the sink's own port is the public one."""

    def __init__(self) -> None:
        self.stopped: list[str] = []

    async def host_on(
        self, tunnel_id: str, *, target_port: int, master_key: bytes, **_: Any
    ) -> Any:
        return _Hosting(target_port)

    async def stop_hosting(self, tunnel_id: str) -> None:
        self.stopped.append(tunnel_id)


class _Egress:
    @contextlib.asynccontextmanager
    async def through(self, route: str | None) -> AsyncIterator[str | None]:
        yield None


@pytest.mark.asyncio
async def test_a_send_through_the_probe_is_counted_by_the_sink() -> None:
    hoster = _Hoster()
    probes = Probes(hoster, _Egress())
    sink = await probes.host("t1", b"k" * 32, seconds=2)
    try:
        sent = await probes.send("t1", sink.address, sink.external_port, sink.secret, 1, 2)
        assert sent.streams == 2 and sent.failed == 0 and sent.sent > 0
        await asyncio.sleep(0.1)
        reading = probes.reading("t1")
        assert reading is not None and reading.connections == 2 and reading.refused == 0
        assert reading.received == sent.sent
        assert reading.rate_bps is not None and reading.rate_bps > 0
    finally:
        await probes.stop("t1")
    assert hoster.stopped == ["t1"] and probes.reading("t1") is None


@pytest.mark.asyncio
async def test_a_connection_without_the_secret_is_refused_and_counts_nothing() -> None:
    probes = Probes(_Hoster(), _Egress())
    sink = await probes.host("t2", b"k" * 32, seconds=2)
    try:
        sent = await probes.send("t2", sink.address, sink.external_port, bytes(SECRET_BYTES), 1, 1)
        await asyncio.sleep(0.1)
        reading = probes.reading("t2")
        assert reading is not None and reading.refused == 1 and reading.received == 0
        assert sent.streams == 1
    finally:
        await probes.stop("t2")


@pytest.mark.asyncio
async def test_a_sink_closes_itself_when_its_seconds_are_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sift.slices.swap import probe as module

    monkeypatch.setattr(module, "_GRACE_SECONDS", 0.0)
    hoster = _Hoster()
    probes = Probes(hoster, _Egress())
    sink = await probes.host("t3", b"k" * 32, seconds=1)
    await asyncio.sleep(1.3)
    assert sink.done and hoster.stopped == ["t3"]
    await probes.stop("t3")
