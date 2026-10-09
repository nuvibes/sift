# SPDX-License-Identifier: AGPL-3.0-or-later
"""Put the server under a real scan load and measure whether its loop and threads still answer."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

import aiohttp

from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.slices.auth.crypto import derive_csrf_token

# Above the running server's quarter-second warning, as this load is deliberately punishing.
MAX_HELD_SECONDS = 0.5

# Higher than the held-loop line: a queued thread delays only the work that needed one.
MAX_THREAD_WAIT_SECONDS = 2.0

# Fast enough to land inside a hold of tens of milliseconds.
POLL_INTERVAL_SECONDS = 0.05

# Not 5171 (a real install) and not 5399 (the end-to-end run).
PORT = 5398
BASE = f"http://127.0.0.1:{PORT}"


def _source_media(into: Path, count: int, *, heavy: bool = False) -> None:
    """Write the files the scan will find, built here so ffmpeg has real frames to decode."""
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:  # pragma: no cover - the caller checks this before calling
        raise SystemExit("ffmpeg is not on PATH")
    for index in range(count):
        target = into / f"clip-{index:03d}.mp4"
        subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                # A different length per file, or they would be one asset in several places.
                f"testsrc=size=1280x720:rate=30:duration={(15 if heavy else 6) + index}",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={(15 if heavy else 6) + index}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                # A real bitrate, or every size target is already met and no encode would run.
                *(["-b:v", "4M"] if heavy else []),
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(target),
            ],
            check=True,
            capture_output=True,
        )


async def _poll_health(session: aiohttp.ClientSession, stop: asyncio.Event) -> list[float]:
    """Ask /health until told to stop, and hand back how long each answer took."""
    taken: list[float] = []
    while not stop.is_set():
        started = time.monotonic()
        try:
            async with session.get(f"{BASE}/health") as response:
                await response.read()
        except aiohttp.ClientError:
            # Recorded as a gap, not as a fast answer.
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            continue
        taken.append(time.monotonic() - started)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    return taken


async def _wait_until_up(session: aiohttp.ClientSession, limit: float = 60.0) -> None:
    """Poll until the server answers or give up, so the boot is never what is measured."""
    try:
        async with asyncio.timeout(limit):
            while True:
                with contextlib.suppress(aiohttp.ClientError):
                    async with session.get(f"{BASE}/health") as response:
                        if response.status == 200:
                            return
                await asyncio.sleep(0.2)
    except TimeoutError:
        raise SystemExit("the server never came up") from None


async def _run(seconds: float, files: int, *, compress: bool = False) -> int:
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not on PATH, and this check builds its own source media")

    scratch = Path(tempfile.mkdtemp(prefix="sift-load-"))
    media = scratch / "media"
    media.mkdir(parents=True)
    print(f"building {files} source files ...", flush=True)
    _source_media(media, files, heavy=compress)

    environment = {
        **os.environ,
        "SIFT_DATA_DIR": str(scratch / "data"),
        "SIFT_CACHE_DIR": str(scratch / "cache"),
        "SIFT_PORT": str(PORT),
    }
    server = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "sift.main",
        env=environment,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip

    stop = asyncio.Event()
    # Bound first, so a poller that never returned reports "nothing was measured".
    taken: list[float] = []
    threads: dict[str, float] | None = None
    try:
        # `unsafe=True`: aiohttp drops cookies set by a bare IP address such as 127.0.0.1.
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            await _wait_until_up(session)
            print(f"server up; measuring for {seconds:.0f}s under load ...", flush=True)

            polling = asyncio.ensure_future(_poll_health(session, stop))
            try:
                # The load: the ordinary scan of the folder and its derivative jobs.
                await _drive_load(session, media, seconds, compress=compress)
                # Read while the server is still up: its own account of the run.
                threads = await _thread_reading(session)
            finally:
                # Stopped however this ended, so the poller never uses a closing session.
                stop.set()
                with contextlib.suppress(asyncio.CancelledError):
                    taken = await polling
    finally:
        server.terminate()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(server.wait(), timeout=15)
        # Only the run's own temp directory; suppressed on the line so the rule watches the rest.
        shutil.rmtree(  # nosemgrep: sift-no-file-removal-outside-delete-trash
            scratch, ignore_errors=True
        )

    return _report(taken, threads)


async def _drive_load(
    session: aiohttp.ClientSession, media: Path, seconds: float, *, compress: bool = False
) -> None:
    """Start the work and let it run; a load that fails to start is fatal, never a pass."""
    await _sign_in_and_scan(session, media)
    if compress:
        await _start_compressing(session)
    await asyncio.sleep(seconds)


async def _start_compressing(session: aiohttp.ClientSession) -> None:
    """Queue a real compression over what the scan has taken in; nothing indexed is fatal."""
    ids: list[str] = []
    for _ in range(60):
        async with session.get(f"{BASE}/api/assets?limit=100") as response:
            if response.status == 200:
                body = await response.json()
                ids = [item["id"] for item in body.get("items", [])]
        if ids:
            break
        await asyncio.sleep(1)

    if not ids:
        raise SystemExit("nothing was indexed in time, so there was nothing to compress")

    async with session.post(
        f"{BASE}/api/compress",
        json={
            "asset_ids": ids,
            "preset": "custom",
            "custom_target_mb": 1,
            "force": True,
        },
        headers=_csrf(session),
    ) as response:
        await _expect(response, (200,), "starting the compression")
        started = (await response.json())["started"]

    if started == 0:
        async with session.post(
            f"{BASE}/api/compress/preflight",
            json={"asset_ids": ids, "preset": "custom", "custom_target_mb": 1},
            headers=_csrf(session),
        ) as response:
            why = (await response.text())[:600]
        raise SystemExit(
            "the compression queued nothing, so this would have measured a scan only.\n"
            f"what the server said would happen: {why}"
        )
    print(f"compressing {started} files", flush=True)


#: Meets the install's password policy; a throwaway instance, deleted at the end of the run.
_PASSWORD = "Load-Check-1"  # noqa: S105 (not a credential; a fresh instance is created per run)


async def _expect(response: aiohttp.ClientResponse, allowed: tuple[int, ...], doing: str) -> None:
    """Refuse to carry on past a step that did not work, saying what the server said."""
    if response.status not in allowed:
        detail = (await response.text())[:200]
        raise SystemExit(f"{doing} answered {response.status}: {detail}")


def _csrf(session: aiohttp.ClientSession) -> dict[str, str]:
    """The header every state-changing route wants, derived from the session cookie."""
    for cookie in session.cookie_jar:
        if cookie.key == SESSION_COOKIE_NAME:
            return {CSRF_HEADER_NAME: derive_csrf_token(cookie.value)}
    raise SystemExit("no session cookie to derive a CSRF token from")


async def _sign_in_and_scan(session: aiohttp.ClientSession, media: Path) -> None:
    """First boot: create an admin, add the folder, start the scan."""
    async with session.post(
        f"{BASE}/api/auth/setup", json={"username": "admin", "password": _PASSWORD}
    ) as response:
        await _expect(response, (200, 201), "creating the admin")

    async with session.post(
        f"{BASE}/api/library/roots",
        # Managed, or the compression may not write its copy and the run measures only a scan.
        json={"name": "load", "abs_path": str(media), "watched": False, "managed": True},
        headers=_csrf(session),
    ) as response:
        await _expect(response, (200, 201), "adding the folder")
        root = (await response.json())["id"]

    async with session.post(
        f"{BASE}/api/library/roots/{root}/rescan", headers=_csrf(session)
    ) as response:
        await _expect(response, (200, 202), "starting the scan")
    print("scan started", flush=True)


async def _thread_reading(session: aiohttp.ClientSession) -> dict[str, float] | None:
    """How long work waited for a thread during the run, by the server's own account."""
    async with session.get(f"{BASE}/health") as response:
        if response.status != 200:
            return None
        body = await response.json()
    reading = body.get("threads")
    if not isinstance(reading, dict):
        return None
    return reading


def _report(taken: list[float], threads: dict[str, float] | None) -> int:
    if not taken:
        print("FAIL: no measurement was taken at all")
        return 1

    worst = max(taken)
    ordered = sorted(taken)
    p99 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))]
    measured: dict[str, object] = {
        "samples": len(taken),
        "median_ms": round(statistics.median(taken) * 1000, 1),
        "p99_ms": round(p99 * 1000, 1),
        "worst_ms": round(worst * 1000, 1),
        "threshold_ms": round(MAX_HELD_SECONDS * 1000, 1),
    }
    if threads is not None:
        measured["thread_wait_worst_ms"] = round(float(threads["worst_wait_seconds"]) * 1000, 1)
        measured["thread_wait_occurrences"] = threads["full_count"]
        measured["thread_wait_threshold_ms"] = round(MAX_THREAD_WAIT_SECONDS * 1000, 1)
    print(json.dumps(measured, indent=2))

    if worst > MAX_HELD_SECONDS:
        print(f"FAIL: the loop was held for {worst:.3f}s, over the {MAX_HELD_SECONDS}s threshold")
        return 1

    # A missing reading is a failure, not a pass.
    if threads is None:
        print("FAIL: the server gave no thread-pool reading, so nothing was measured for it")
        return 1
    waited = float(threads["worst_wait_seconds"])
    if waited > MAX_THREAD_WAIT_SECONDS:
        print(
            f"FAIL: work waited {waited:.3f}s for a free thread, over the "
            f"{MAX_THREAD_WAIT_SECONDS}s threshold. The loop was fine throughout, which is what "
            f"this failure looks like from a screen: pages quick, video stuttering."
        )
        return 1
    print("ok: the loop answered throughout, and work never queued long for a thread")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60.0, help="how long to measure for")
    parser.add_argument("--files", type=int, default=12, help="how many source files to scan")
    parser.add_argument(
        "--compress",
        action="store_true",
        help="also run a real compression over everything indexed, the heaviest load there is",
    )
    args = parser.parse_args()
    return asyncio.run(_run(args.seconds, args.files, compress=args.compress))


if __name__ == "__main__":
    raise SystemExit(main())
