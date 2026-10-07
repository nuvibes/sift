# SPDX-License-Identifier: AGPL-3.0-or-later
"""Put the server under real load and measure whether it still answers.

A freeze of this kind appears only when the machine is busy, and shows itself as a person noticing
the application has stopped rather than as anything failing. The static check beside this refuses a
blocking call that is written down; this one catches the ones that are not: work inside a C
extension, a library that reads a file on the way past, a thread pool with nobody left in it.

`/health` is what gets asked, because an unauthenticated one returns a constant with no database
behind it. There is nothing for it to be slow *at*: any delay answering it is the event loop not
running, and nothing else.

**Two failures are measured here, not one, and the second is invisible to a response time.** Work
is kept off the loop by handing it to a thread, and there is a finite number of threads. When they
are all busy the next piece of work queues, and because answering `/health` does no thread work at
all, a completely full pool answers it instantly. The tell is the opposite of a held loop: the
interface stays responsive while video stutters. So the server's own reading of how long work is
waiting for a thread is collected at the end of the run and judged on its own threshold. A green
response time proves nothing about it.

The load is the real thing: a scan importing files, thumbnails and previews being built, all of
it through the ordinary job queue and the ordinary ffmpeg. A synthetic busy loop would prove the
measurement works and nothing about the application.

    python scripts/check_loop_under_load.py [--seconds 60] [--files 12]

Exits non-zero when the loop was held longer than the threshold, and prints what it measured
either way. Run by hand: the load has to be big enough to matter, which makes it
too slow to sit in the ordinary gate.
"""

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

# The most the loop may be held at any single moment before this is a failure, in seconds.
#
# Above the quarter second the running server warns at, because this is a deliberately punishing
# load on a machine that may be running something else too, and a check
# that fails on a busy machine is one that gets ignored. It is still far below anything a person
# would call working: half a second of no video, repeatedly, is a stutter somebody reports.
MAX_HELD_SECONDS = 0.5

# The most work may wait for a free thread before this is a failure, in seconds.
#
# Higher than the held-loop threshold above, deliberately. A held loop stops the whole application
# including the interface; a queued thread delays only the work that needed a thread, and under a
# scan that is deliberately trying to use the whole machine some queueing is the system working
# rather than failing. The number is set where video would visibly suffer rather than where the
# pool is merely busy, and it is checked against what a real run actually produces: a threshold
# nothing can pass is a check that gets deleted.
MAX_THREAD_WAIT_SECONDS = 2.0

# How often /health is asked. Fast enough to land inside a hold rather than around it: a hold is
# tens of milliseconds at the low end, and a poll every second would step straight over it.
POLL_INTERVAL_SECONDS = 0.05

# Not 5171 (a real install) and not 5399 (the end-to-end run). Nothing this touches may be a port
# somebody has a library on.
PORT = 5398
BASE = f"http://127.0.0.1:{PORT}"


def _source_media(into: Path, count: int, *, heavy: bool = False) -> None:
    """Write the files the scan will find.

    Built here rather than checked in: what has to be exercised is decoding and thumbnailing real
    frames, and a file small enough to commit is one ffmpeg finishes with before the loop could
    notice.
    """
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
                # A different length per file, so the files are different FILES. Built with the
                # same arguments they would come out byte-identical, which Sift reads as one
                # asset sitting in several places: fine for a scan, and fatal for anything that
                # acts on a file, which refuses to guess which copy was meant.
                f"testsrc=size=1280x720:rate=30:duration={(15 if heavy else 6) + index}",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={(15 if heavy else 6) + index}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                # A real bitrate when a compression is going to be run over these. The default is
                # a few hundred kilobytes, which every size target already meets, so the
                # compression would decline every file and the run would measure a scan while
                # reporting that it had measured an encode.
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
            # A refused or dropped connection is not a measurement of the loop. Recorded as a gap
            # rather than as a fast answer, which is what counting it as zero would do.
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            continue
        taken.append(time.monotonic() - started)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    return taken


async def _wait_until_up(session: aiohttp.ClientSession, limit: float = 60.0) -> None:
    """Poll until the server answers, or give up. A boot that builds a database and runs its
    migrations takes a moment, and measuring before it is up would measure the boot."""
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
    # Bound before the run, so a poller that never returned reports "nothing was measured" rather
    # than failing with a name error while trying to say so.
    taken: list[float] = []
    threads: dict[str, float] | None = None
    try:
        # `unsafe=True` so the session cookie is kept at all: aiohttp discards cookies set by a
        # host that is a bare IP address, and this talks to 127.0.0.1. Without it the sign-in
        # appears to work and every request after it is anonymous.
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            await _wait_until_up(session)
            print(f"server up; measuring for {seconds:.0f}s under load ...", flush=True)

            polling = asyncio.ensure_future(_poll_health(session, stop))
            try:
                # The load: point the install at the folder and let the ordinary scan and its
                # derivative jobs run. Everything heavy in Sift hangs off this one gesture.
                await _drive_load(session, media, seconds, compress=compress)
                # Read before the poller is stopped and before the server is: this is the server's
                # own account of the run, and it is only readable while it is still up.
                threads = await _thread_reading(session)
            finally:
                # Stopped on the way out however this ended, so a failure to start the load does
                # not leave the poller reaching for a session that is being closed underneath it.
                stop.set()
                with contextlib.suppress(asyncio.CancelledError):
                    taken = await polling
    finally:
        server.terminate()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(server.wait(), timeout=15)
        # The directory this run made for itself under the system temp directory, holding the
        # source files it built and the throwaway install it pointed at them. Never a library, and
        # never a file anybody else put there. Suppressed on the line rather than for the file, so
        # the rule keeps watching everything else here.
        shutil.rmtree(  # nosemgrep: sift-no-file-removal-outside-delete-trash
            scratch, ignore_errors=True
        )

    return _report(taken, threads)


async def _drive_load(
    session: aiohttp.ClientSession, media: Path, seconds: float, *, compress: bool = False
) -> None:
    """Start the work and let it run for the measurement window.

    Signing in and adding the folder are done through the API rather than by writing rows, so what
    runs is what an install runs.

    A failure to start the load is fatal rather than a warning: a run that measured a server with
    nothing to do would report a worst case of a few milliseconds and say ok. A check that can pass
    without exercising anything is worse than no check, because it is the one nobody re-reads.

    `compress` adds the heaviest thing the application can be asked to do: a full re-encode of
    every file it has just taken in, several at the same time, for as long as they take. A scan and
    its derivatives are seconds of ffmpeg per file; this is minutes, on the same worker pool,
    holding one of the shared threads apiece for the whole run of each tool. It is a separate switch
    rather than always on because it makes the check far slower, and because a run without it is
    still the right measurement of a scan.
    """
    await _sign_in_and_scan(session, media)
    if compress:
        await _start_compressing(session)
    await asyncio.sleep(seconds)


async def _start_compressing(session: aiohttp.ClientSession) -> None:
    """Queue a real compression over everything the scan has taken in so far.

    It waits for the scan to produce something first, because a compression of nothing measures
    nothing. If nothing has been indexed by the time it gives up, that is fatal: a green result from
    a run that compressed nothing is exactly the confident nonsense this file exists to stop
    producing.

    The target is deliberately small enough to force a real encode rather than a stream copy, and
    the override is set, because the point is to make the machine work rather than to respect the
    arithmetic that would otherwise decline.
    """
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


#: Meets the install's own password policy: upper, lower, digit and symbol. This is a throwaway
#: instance on a temporary directory that is deleted at the end of the run.
_PASSWORD = "Load-Check-1"  # noqa: S105 (not a credential; a fresh instance is created per run)


async def _expect(response: aiohttp.ClientResponse, allowed: tuple[int, ...], doing: str) -> None:
    """Refuse to carry on past a step that did not work, saying what the server said.

    The body is read into the message rather than left out: a validation refusal names exactly what
    was wrong, and a bare status code would mean guessing at each one.
    """
    if response.status not in allowed:
        detail = (await response.text())[:200]
        raise SystemExit(f"{doing} answered {response.status}: {detail}")


def _csrf(session: aiohttp.ClientSession) -> dict[str, str]:
    """The header every state-changing route wants, derived from the session cookie the way the
    browser client derives it."""
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
        # Managed, because a compression writes its copy into this folder and Sift refuses to
        # write into one that was not handed over, so without it the compression declines every
        # file and the run measures a scan. Harmless for the scan-only run: managed permits a
        # write, it does not cause one.
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
    """The server's own account of how long work waited for a thread during the run.

    Taken from `/health` at the end rather than sampled: the figures are the worst and the count
    since the process started, and this process started for this run, so one read at the end is the
    whole answer. It rides along only for an admin, which is what the sign-in above is already for.
    """
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

    # A missing reading is a failure, not a pass: a check which quietly measures nothing
    # reports a confident green.
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
