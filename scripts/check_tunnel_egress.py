#!/usr/bin/env python
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prove every way Sift reaches the internet honours the route it is given.

Sift fetches four ways: two downloader tools it runs as processes, an impersonating client the
resolvers use for service APIs, and a streaming session for direct media addresses. A route that
reaches some of them and not the others is worse than none: the traffic that escapes is the traffic
of the sites somebody deliberately routed away from their own address, and nothing about the
download looks wrong afterwards.

The unit tests assert each seam is HANDED the route. This asserts it USES it, which is a different
claim and the one that matters. Two checks per seam:

  * the two clients inside the process are pointed at a real tunnel and asked what address they came
    out on: it must be the tunnel's, not the machine's;
  * all four are pointed at a local proxy that writes down what it is asked for and then refuses. A
    tool that ignores its proxy flag never contacts it at all, which is the only way to test the two
    that run as separate processes.

Run it from a checkout with the shipped tools fetched (scripts/fetch_vendor.py): the downloaders
and the tunnel client resolve to vendor/bin there as they do in an installed copy. Start the tunnel
client first, on a configuration that points at the provider's and listens where SIFT_TUNNEL_PROXY
says (127.0.0.1:9080 unless it is set):

    WGConfig = path/to/provider.conf

    [http]
    BindAddress = 127.0.0.1:9080

    vendor/bin/wireproxy.exe -c wp.conf -s
    python scripts/check_tunnel_egress.py

It needs a WireGuard configuration from a VPN provider and it makes real requests, so it is not part
of CI. It is what to run after touching anything on a fetch path.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from sift.slices.download.sources import argv, curl, fetcher, net

#: The tunnel's own proxy, and the recorder's. Both loopback; both overridable for a machine where
#: something else already holds a port.
TUNNEL_PROXY = os.environ.get("SIFT_TUNNEL_PROXY", "http://127.0.0.1:9080")
RECORDER_PORT = int(os.environ.get("SIFT_RECORDER_PORT", "9099"))
RECORDER = f"http://127.0.0.1:{RECORDER_PORT}"

#: A service that answers with the address the request came from. The whole check turns on it.
ECHO = "https://ipinfo.io/json"

#: Each tool needs an address it has an extractor for. One it does not recognise is refused before
#: any request is made, which reads exactly like a proxy being ignored.
TOOLS = (
    ("yt-dlp", argv.build_ytdlp_argv, "https://www.youtube.com/watch?v=aqz-KE-bpKQ", "youtube.com"),
    ("gallery-dl", argv.build_gallerydl_argv, "https://www.reddit.com/r/pics/", "reddit.com"),
)

asked: list[str] = []
failures: list[str] = []


def _pass(name: str, detail: str) -> None:
    print(f"  ok    {name}: {detail}")


def _fail(name: str, detail: str) -> None:
    failures.append(f"{name}: {detail}")
    print(f"  FAIL  {name}: {detail}")


async def _recorder() -> asyncio.Server:
    """A proxy that notes the first line of every request and then refuses it."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        asked.append((await reader.readline()).decode("latin-1").strip())
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", RECORDER_PORT)


async def _address(proxy: str | None) -> str:
    answer = await curl.guarded_get(ECHO, proxy=proxy, time_limit=25)
    return str(json.loads(answer.text)["ip"])


def _saw(name: str, host: str) -> None:
    if any(re.search(host, line) for line in asked):
        _pass(name, f"asked the proxy for {host}")
    else:
        _fail(name, f"never contacted the proxy: it saw {asked}")


async def main() -> int:
    server = await _recorder()

    direct = await _address(None)
    tunnelled = await _address(TUNNEL_PROXY)
    if direct == tunnelled:
        _fail("the tunnel", "it is not carrying anything: the exit address is unchanged")
        return 1
    print("\nthe tunnel is up and its exit differs from this machine's\n")

    print("the two clients inside the process, against that real exit:")
    if await _address(TUNNEL_PROXY) == tunnelled:
        _pass("impersonating client", "came out on the tunnel")
    else:
        _fail("impersonating client", "came out somewhere else")

    with tempfile.TemporaryDirectory() as workspace:
        async with net.guarded_session(proxy=TUNNEL_PROXY) as session:
            landed, _ = await fetcher.fetch_to_file(
                session, url=ECHO, dest=Path(workspace) / "echo.json"
            )
        came_out_on = json.loads(landed.read_text())["ip"]
    if came_out_on == tunnelled:
        _pass("streaming fetcher", "came out on the tunnel")
    else:
        _fail("streaming fetcher", "came out somewhere else")

    print("\nall four, against a proxy that records and refuses:")
    # Every call below is expected to fail. Contacting the proxy at all is the whole of the claim.
    asked.clear()
    with contextlib.suppress(Exception):
        await curl.guarded_get(ECHO, proxy=RECORDER, time_limit=15)
    _saw("impersonating client", "ipinfo.io")

    asked.clear()
    with tempfile.TemporaryDirectory() as workspace, contextlib.suppress(Exception):
        async with net.guarded_session(proxy=RECORDER) as session:
            await fetcher.fetch_to_file(session, url=ECHO, dest=Path(workspace) / "echo.json")
    _saw("streaming fetcher", "ipinfo.io")

    for name, build, url, host in TOOLS:
        asked.clear()
        # Started on the loop rather than waited on: the recorder is a server in this same process,
        # so blocking here would starve the very thing the tool is trying to talk to.
        with tempfile.TemporaryDirectory() as workspace:
            child = await asyncio.create_subprocess_exec(
                *build(url, Path(workspace), proxy=RECORDER),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(child.wait(), timeout=60)
            except TimeoutError:
                child.kill()
                await child.wait()
        _saw(name, host)

    server.close()
    await server.wait_closed()

    print("\n" + ("every way out honours the route" if not failures else f"FAILED: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
