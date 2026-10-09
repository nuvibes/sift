#!/usr/bin/env python
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prove every way Sift reaches the internet honours the route it is given; needs a real tunnel."""

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

#: Both loopback; both overridable where something else holds the port.
TUNNEL_PROXY = os.environ.get("SIFT_TUNNEL_PROXY", "http://127.0.0.1:9080")
RECORDER_PORT = int(os.environ.get("SIFT_RECORDER_PORT", "9099"))
RECORDER = f"http://127.0.0.1:{RECORDER_PORT}"

#: A service that answers with the address the request came from. The whole check turns on it.
ECHO = "https://ipinfo.io/json"

#: An address the tool has no extractor for is refused first, which reads like a proxy ignored.
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
    # Every call below is expected to fail: contacting the proxy at all is the claim.
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
        # Started on the loop, not waited on: the recorder is a server in this same process.
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
