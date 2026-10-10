# SPDX-License-Identifier: AGPL-3.0-or-later
"""Streaming a direct media address to a file, once a resolver has produced one.

The session is the guarded one (`net.guarded_session`), so the address is vetted and the socket
pinned before a byte moves. The far end is not trusted: the timeouts are split (connect, and
between reads) with no ceiling on the whole, and a transfer that stays alive but crawls is
abandoned past a startup grace. The Downloads settings reach this transfer as they reach the two
tools (`RunPolicy`). A failure leaves nothing half-written; a PAUSE is a cancellation, not a
failure, so its bytes stay for the resumed fetch to carry on from.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urljoin

import aiohttp

from sift.kernel.log import get_logger
from sift.kernel.ratelimit import HostRateLimiter, Sleep, parse_retry_after
from sift.slices.download.sources import failures, progress, ratelimit
from sift.slices.download.sources.errors import (
    NO_ANSWER_MESSAGE,
    FetchFailed,
    NoAnswer,
    NothingFound,
)
from sift.slices.download.sources.net import policy_timeout
from sift.slices.download.sources.tuning import POLICY, RunPolicy
from sift.slices.download.url_guard import MAX_REDIRECT_HOPS, check_url

log = get_logger(__name__)

_CHUNK = 1 << 16  # 64 KiB per read
# The redirect responses this follows by hand. aiohttp would follow them itself, but it does not run
# the guarded resolver for a target that is already a literal IP, so a public link that redirects to
# an internal address would slip past. Following the chain here, and vetting each hop, closes that.
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
#: The site agreed to the range and is sending the rest of the file.
_PARTIAL_CONTENT = 206
#: The site says there is nothing after the byte asked for, so what is on disk is the whole file.
_NOTHING_PAST_THAT_POINT = 416
_FETCH_FAILED_MESSAGE = "The download did not finish. Try again in a few minutes."
#: The answers that mean "ask again": the site is limiting requests, or had trouble of its own. Every
#: other refusal is the same answer on the next attempt, so it is not asked again here.
_TOO_MANY_REQUESTS = 429
_SERVER_TROUBLE = range(500, 600)
_MEGABYTE = 1024 * 1024
_KILOBYTE = 1024
# A live-but-crawling transfer is abandoned once it has had a grace period to get going and still
# cannot sustain even this average rate. Well below any healthy CDN, so it only catches real trouble.
_STALL_GRACE_SEC = 30.0
_MIN_THROUGHPUT_BPS = 50_000  # ~50 KB/s average, judged only after the grace
_STALL_CHECK_INTERVAL_SEC = 1.0

# Content types worth a definite extension when the CDN address carries none (Pixeldrain's
# /api/file/<id> and the like). Anything not here keeps the sniffed-or-absent suffix; the ingress
# gate decides what a file really is from its bytes regardless.
_EXT_BY_CONTENT_TYPE = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


#: The policy values this fetch does NOT apply, each with the reason, so "cannot apply" and
#: "somebody forgot" do not look alike (the same rule `argv.Concern` keeps for the tools). Read by the
#: gate in `test_fetcher`: every other value on the policy must be proved to bite here.
NOT_FOR_SIFTS_OWN_FETCH: dict[str, str] = {
    "verbose": "asks the tools to print their own diagnostics; Sift's own fetch writes what it "
    "did to the log whatever this says (download.fetch_retry, fetch_skipped, fetch_failed)",
}


class Skipped(NothingFound):
    """A file the size settings say not to keep. Final: the same file is the same size next time.

    A kind of `NothingFound` so everything that already knows a final answer treats it as one, and
    its own kind so the album it belongs to can skip it and carry on with the rest, the way the
    tools skip a file past `--max-filesize` and fetch the others.
    """


def _make_timeout(policy: RunPolicy = POLICY) -> aiohttp.ClientTimeout:
    """No overall ceiling, but a dead socket trips `sock_read` and a dead connect trips its own.

    Both are the connection timeout setting: the tools are told the same number
    (`--socket-timeout`, `--http-timeout`), and the question both answer is the one the setting
    asks: how long a connection may say nothing before it is given up on.
    """
    return policy_timeout(policy)


def _size_words(size: int) -> str:
    """A size the way the settings state one: whole megabytes, or kilobytes under one."""
    if size < _MEGABYTE:
        return f"{max(1, size // _KILOBYTE)} KB"
    return f"{round(size / _MEGABYTE)} MB"


def _too_large(size: int, largest: int, *, stated: bool = True) -> Skipped:
    """The sentence for a file over the ceiling. A file whose size the Site did not state is only
    known to be too large once it has passed the ceiling, so it is said as stopped, not measured."""
    if not stated:
        return Skipped(
            f"Sift stopped this file at {_size_words(largest)} because Skip files larger than "
            f"is set to {_size_words(largest)}."
        )
    return Skipped(
        f"Sift skipped this {_size_words(size)} file because Skip files larger than is set "
        f"to {_size_words(largest)}."
    )


def _too_small(size: int, smallest: int) -> Skipped:
    return Skipped(
        f"Sift skipped this {_size_words(size)} file because Skip files smaller than is set "
        f"to {_size_words(smallest)}."
    )


def _out_of_bounds(size: int, policy: RunPolicy) -> Skipped | None:
    """Why a file of `size` bytes is not kept, or None when the size settings allow it."""
    largest = policy.filters.at_most_bytes
    smallest = policy.filters.at_least_bytes
    if largest is not None and size > largest:
        return _too_large(size, largest)
    if smallest is not None and size < smallest:
        return _too_small(size, smallest)
    return None


def skipped_for_size(size: int, bound: int, *, larger: bool) -> Skipped:
    """The sentence for a file a TOOL left out for its size, the one Sift's own fetch says.

    The tools skip a file past their size options and exit as if nothing went wrong; the file and
    the limit are read off what they printed (`downloader._size_skip`), so a skipped file reads the
    same whichever way it was fetched.
    """
    return _too_large(size, bound) if larger else _too_small(size, bound)


class _Throttle:
    """The speed limit, as a token bucket over the read loop.

    A second's worth of bytes may go immediately, and after that the loop waits for the bytes it has
    read to be earned back. Throttling the READER is what throttles the connection: aiohttp stops
    reading the socket once its own small buffer is full, so a reader that waits is a sender that
    is told to wait.
    """

    def __init__(self, bytes_per_second: int, clock: Callable[[], float], sleep: Sleep) -> None:
        self._rate = float(bytes_per_second)
        self._clock = clock
        self._sleep = sleep
        self._tokens = self._rate
        self._last = clock()

    async def spend(self, count: int) -> None:
        now = self._clock()
        self._tokens = min(self._rate, self._tokens + (now - self._last) * self._rate)
        self._last = now
        self._tokens -= count
        if self._tokens < 0:
            await self._sleep(-self._tokens / self._rate)


def _asks_again(exc: BaseException) -> bool:
    """Whether a failed request is worth asking again immediately, the way the tools' `--retries` would.

    A refused or dropped connection costs nothing to ask again, so it is asked again up to the
    retry count. A timeout is not here: each ask of one costs a whole Stopped responding for, so
    `_worth_one_more_wait` decides it on its own terms. A refusal that is not a 429 or a 5xx is the
    same answer next time, and a bad certificate is not a network blip.
    """
    if isinstance(exc, TimeoutError | aiohttp.ClientSSLError):
        return False
    if isinstance(exc, aiohttp.ClientResponseError):
        return exc.status == _TOO_MANY_REQUESTS or exc.status in _SERVER_TROUBLE
    return isinstance(exc, aiohttp.ClientPayloadError | aiohttp.ClientConnectionError)


def _worth_one_more_wait(interrupted: _Interrupted, *, waited_again: bool) -> bool:
    """Whether a timeout is worth asking once more, when every ask costs a whole wait.

    Only a STALL is: the Site answered and began sending, then went quiet or slowed to a crawl,
    which a CDN does for a moment and then recovers from, and the next ask resumes from the bytes
    already here. A Site that never answered at all is down or unreachable, and waiting on it again
    only doubles the cost of finding that out. Once per file either way, so a dead Site costs one
    wait and a stalling one two, however high the retry count is.
    """
    return interrupted.answered and not waited_again


def _ext_from_content_type(content_type: str | None) -> str:
    """A file extension for a response content type, or "" when there is no confident one. The type
    can carry parameters (`image/jpeg; charset=...`), so only the media type before `;` is read."""
    if not content_type:
        return ""
    media_type = content_type.split(";", 1)[0].strip().lower()
    return _EXT_BY_CONTENT_TYPE.get(media_type, "")


async def fetch_to_file(
    session: aiohttp.ClientSession,
    *,
    url: str,
    dest: Path,
    referer: str = "",
    accept: str | None = None,
    cookie: str | None = None,
    clock: Callable[[], float] = time.monotonic,
    report: progress.Report = progress.nowhere,
    already_done: int = 0,
    files_done: int = 0,
    files_total: int | None = None,
    resume_from: int = 0,
    policy: RunPolicy = POLICY,
    sleep: Sleep = asyncio.sleep,
    limiter: HostRateLimiter | None = None,
    here: bool = True,
) -> tuple[Path, int]:
    """Stream `url` to `dest`, returning `(final_path, bytes_on_disk)`.

    `clock`, `sleep` and `limiter` are parameters so a test drives the stall detector, the speed
    limit and a host's hold without touching asyncio's own clock or the process-wide hold. Every
    address opened (the first and each redirect, followed by hand) is held to the public-only rule
    before a socket opens. A file outside the size bounds raises `Skipped`; any network, HTTP or
    truncation failure raises `FetchFailed` once the retries are spent, and a partial file goes.

    `resume_from` is how many bytes are already on disk from a paused run: the rest is asked for
    with a `Range` header. A 206 appends, a 200 overwrites, and a 416 means the file was already
    whole. The count returned is what is ON DISK afterwards, resumed bytes included.
    """
    await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
    asks_left = policy.pacing.retries
    waited_again = False
    on_disk = resume_from
    current = url
    try:
        while True:
            try:
                dest, on_disk = await _one_attempt(
                    session,
                    url=url,
                    dest=dest,
                    headers=_headers(referer, accept, cookie, on_disk),
                    clock=clock,
                    report=report,
                    already_done=already_done,
                    files_done=files_done,
                    files_total=files_total,
                    resume_from=on_disk,
                    policy=policy,
                    sleep=sleep,
                    here=here,
                )
                return dest, on_disk
            except _Interrupted as interrupted:
                # The next attempt resumes from the bytes this one left.
                dest, on_disk, current = interrupted.dest, interrupted.on_disk, interrupted.at
                cause = interrupted.cause
                waited_again = _ask_again_or_raise(interrupted, asks_left, waited_again)
                asks_left -= 1
                log.info("download.fetch_retry", url=url, left=asks_left, detail=str(cause))
                if (
                    isinstance(cause, aiohttp.ClientResponseError)
                    and cause.status == _TOO_MANY_REQUESTS
                ):
                    ratelimit.note_too_many_requests(
                        current, policy, retry_after=_retry_after(cause), limiter=limiter
                    )
                    await ratelimit.wait_out_backoff(current, limiter=limiter)
    except Skipped:
        await asyncio.to_thread(dest.unlink, missing_ok=True)
        log.info("download.fetch_skipped", url=url)
        raise
    except (aiohttp.ClientError, TimeoutError, OSError) as exc:
        await asyncio.to_thread(dest.unlink, missing_ok=True)
        log.warning("download.fetch_failed", url=url, detail=str(exc))
        raise _failure_of(exc, url=url, current=current, policy=policy, limiter=limiter) from exc


def _ask_again_or_raise(interrupted: _Interrupted, asks_left: int, waited_again: bool) -> bool:
    """Raise the attempt's cause unless it is worth asking again; whether a wait has been spent."""
    cause = interrupted.cause
    if isinstance(cause, TimeoutError):
        if asks_left <= 0 or not _worth_one_more_wait(interrupted, waited_again=waited_again):
            raise cause from None
        return True
    if asks_left <= 0 or not _asks_again(cause):
        raise cause from None
    return waited_again


def _failure_of(
    exc: BaseException,
    *,
    url: str,
    current: str,
    policy: RunPolicy,
    limiter: HostRateLimiter | None,
) -> Exception:
    """The failure a spent fetch raises, in the words the failure reader uses for a tool."""
    if isinstance(exc, aiohttp.ClientResponseError):
        if exc.status == _TOO_MANY_REQUESTS:
            # Held even with no retries left: the NEXT download from this host is what it is about.
            ratelimit.note_too_many_requests(
                current, policy, retry_after=_retry_after(exc), limiter=limiter
            )
        # The server answered with a code, and the code is the reason; a final answer is not
        # retried, since a file the host says is gone is gone on the third attempt too.
        reading = failures.explain_status(url, exc.status)
        kind = NothingFound if reading.final else FetchFailed
        return kind(
            reading.sentence,
            code=reading.code,
            tier=reading.tier,
            a_tunnel_would_help=reading.a_tunnel_would_help,
        )
    if isinstance(exc, TimeoutError):
        # Its own kind, so the job ends here rather than sitting through the same wait again.
        return NoAnswer(NO_ANSWER_MESSAGE, code=failures.CODE_NO_ANSWER)
    return FetchFailed(_FETCH_FAILED_MESSAGE)


def _retry_after(refusal: aiohttp.ClientResponseError) -> float | None:
    """The wait a 429 named in its `Retry-After`, where it named one Sift can read."""
    said = refusal.headers.get("Retry-After") if refusal.headers is not None else None
    return parse_retry_after(said)


def _headers(referer: str, accept: str | None, cookie: str | None, on_disk: int) -> dict[str, str]:
    headers = {"Referer": referer} if referer else {}
    if accept:
        headers["Accept"] = accept
    if cookie:
        headers["Cookie"] = cookie
    if on_disk > 0:
        headers["Range"] = f"bytes={on_disk}-"
    return headers


class _Interrupted(Exception):
    """One attempt that failed, with what it left behind, so the next one can resume from it.

    `answered` says whether the Site had begun its answer (the response's headers arrived) before
    the attempt failed, which is what tells a stall from a Site that never answered at all.
    """

    def __init__(
        self, cause: BaseException, *, dest: Path, on_disk: int, at: str, answered: bool = False
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.dest = dest
        self.on_disk = on_disk
        self.at = at
        self.answered = answered


async def _one_attempt(
    session: aiohttp.ClientSession,
    *,
    url: str,
    dest: Path,
    headers: dict[str, str],
    clock: Callable[[], float],
    report: progress.Report,
    already_done: int,
    files_done: int,
    files_total: int | None,
    resume_from: int,
    policy: RunPolicy,
    sleep: Sleep,
    here: bool = True,
) -> tuple[Path, int]:
    """One request for the file (and the redirects it is sent through), streamed to `dest`.

    Raises `_Interrupted` around a network failure so the caller knows what is on disk; everything
    else (a refused address, a skipped size) goes straight through.
    """
    current = url
    answered = False
    try:
        for _hop in range(MAX_REDIRECT_HOPS + 1):
            # Per hop: a redirect can send the ask to another host, which has answered nothing yet.
            answered = False
            await asyncio.to_thread(check_url, current, here=here)
            async with session.get(
                current, headers=headers, timeout=_make_timeout(policy), allow_redirects=False
            ) as resp:
                answered = True
                if resp.status in _REDIRECT_STATUSES:
                    location = resp.headers.get("Location")
                    if not location:
                        raise FetchFailed(_FETCH_FAILED_MESSAGE)
                    current = urljoin(current, location)
                    continue
                if resume_from > 0 and resp.status == _NOTHING_PAST_THAT_POINT:
                    # Nothing after the bytes already here, so the file is whole. Read before
                    # `raise_for_status`, which would turn "you already have it" into a failure.
                    return dest, resume_from
                resp.raise_for_status()
                # 206 honours the range; a 200 is the whole file again, and appending to it would
                # write the first part twice. The answer decides, never the ask.
                kept = resume_from if resp.status == _PARTIAL_CONTENT else 0
                if dest.suffix == "":
                    sniffed = _ext_from_content_type(resp.headers.get("Content-Type"))
                    if sniffed:
                        dest = dest.with_suffix(sniffed)
                return await _receive(
                    resp,
                    dest=dest,
                    kept=kept,
                    at=current,
                    clock=clock,
                    report=report,
                    already_done=already_done,
                    files_done=files_done,
                    files_total=files_total,
                    policy=policy,
                    sleep=sleep,
                )
        raise FetchFailed(_FETCH_FAILED_MESSAGE)  # a redirect chain longer than the cap
    except (aiohttp.ClientError, TimeoutError, OSError) as exc:
        # Nothing reached the file on this path (a refusal, a connect that failed, a Site that
        # never answered), so what is on disk is what the attempt started with.
        raise _Interrupted(
            exc, dest=dest, on_disk=resume_from, at=current, answered=answered
        ) from exc


@dataclass
class _Transfer:
    """Where one response's body has got to, held outside the copy loop so a break knows it."""

    started: float
    last_report: float
    last_stall_check: float
    throttle: _Throttle | None
    #: Below this rate past the grace a transfer is a crawl; under a speed limit, half the limit.
    floor: float
    largest: int | None
    received: int = 0


async def _receive(
    resp: aiohttp.ClientResponse,
    *,
    dest: Path,
    kept: int,
    at: str,
    clock: Callable[[], float],
    report: progress.Report,
    already_done: int,
    files_done: int,
    files_total: int | None,
    policy: RunPolicy,
    sleep: Sleep,
) -> tuple[Path, int]:
    """Stream one answered response to `dest`, after `kept` bytes already there.

    Refused before a byte is written where the Site declared a size out of bounds. A connection
    that breaks, goes quiet or crawls leaves what is on disk for the next attempt to resume from.
    """
    size = _declared_size(resp)
    if size is not None and (skip := _out_of_bounds(kept + size, policy)) is not None:
        raise skip
    rate = policy.pacing.bytes_per_second
    throttle = _Throttle(rate, clock, sleep) if rate is not None else None
    started = clock()
    state = _Transfer(
        started=started,
        last_report=started,
        last_stall_check=started,
        throttle=throttle,
        floor=_MIN_THROUGHPUT_BPS if rate is None else min(_MIN_THROUGHPUT_BPS, rate / 2),
        largest=policy.filters.at_most_bytes,
    )
    # What is on disk already counts as done, and a range's length is what is LEFT.
    base = already_done + kept
    report(
        progress.Progress(
            done_bytes=base,
            total_bytes=(base + size) if size is not None else None,
            done_files=files_done,
            total_files=files_total,
        )
    )
    # Opening and every write go to a thread, so a transfer never stops the loop.
    with await asyncio.to_thread(dest.open, "ab" if kept else "wb") as handle:
        try:
            await _copy(
                resp,
                handle,
                state,
                kept=kept,
                base=base,
                size=size,
                clock=clock,
                report=report,
                files_done=files_done,
                files_total=files_total,
            )
        except (
            aiohttp.ClientPayloadError,
            aiohttp.ClientConnectionError,
            TimeoutError,
        ) as broke:
            raise _Interrupted(
                broke, dest=dest, on_disk=kept + state.received, at=at, answered=True
            ) from broke
    downloaded = state.received
    # A file that stated no size is only known to be too small once it has arrived.
    if size is None and (skip := _out_of_bounds(kept + downloaded, policy)) is not None:
        raise skip
    # One last reading, so a finished transfer reads as finished.
    report(
        progress.Progress(
            done_bytes=base + downloaded,
            total_bytes=base + (size if size is not None else downloaded),
            done_files=files_done + 1,
            total_files=files_total,
        )
    )
    return dest, kept + downloaded


async def _copy(
    resp: aiohttp.ClientResponse,
    handle: BinaryIO,
    state: _Transfer,
    *,
    kept: int,
    base: int,
    size: int | None,
    clock: Callable[[], float],
    report: progress.Report,
    files_done: int,
    files_total: int | None,
) -> None:
    """The body, a chunk at a time: the ceiling, the speed limit, the progress and the stall."""
    async for chunk in resp.content.iter_chunked(_CHUNK):
        await asyncio.to_thread(handle.write, chunk)
        state.received += len(chunk)
        # A Site that stated no size, or a false one, is stopped the moment it passes the ceiling.
        if state.largest is not None and kept + state.received > state.largest:
            raise _too_large(kept + state.received, state.largest, stated=False)
        if state.throttle is not None:
            await state.throttle.spend(len(chunk))
        now = clock()
        elapsed = now - state.started
        # Reported once a second, as the screen asks, rather than per chunk.
        if now - state.last_report >= progress.REPORT_EVERY_SECONDS:
            state.last_report = now
            report(
                progress.Progress(
                    done_bytes=base + state.received,
                    total_bytes=(base + size) if size is not None else None,
                    done_files=files_done,
                    total_files=files_total,
                )
            )
        if (
            elapsed >= _STALL_GRACE_SEC
            and now - state.last_stall_check >= _STALL_CHECK_INTERVAL_SEC
        ):
            state.last_stall_check = now
            if state.received / elapsed < state.floor:
                raise TimeoutError(
                    f"stalled at {state.received / elapsed / 1000:.0f} KB/s over {elapsed:.0f}s"
                )


def _declared_size(resp: aiohttp.ClientResponse) -> int | None:
    """How big the response says it is, or None when it does not say.

    None rather than zero, and the difference matters on screen: a site that declares no length
    means there is no bar to draw, while a zero would mean a bar already full.
    """
    declared = resp.headers.get("Content-Length")
    try:
        size = int(declared) if declared is not None else None
    except ValueError:
        return None
    return size if size and size > 0 else None


__all__ = ["Skipped", "fetch_to_file", "skipped_for_size"]
