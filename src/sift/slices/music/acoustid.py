# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ask AcoustID which song a fingerprint is; only two minutes of it and the length are sent."""

from __future__ import annotations

import base64
import gzip
import json
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode

import aiohttp

from sift.kernel.http import read_capped
from sift.kernel.log import get_logger
from sift.kernel.ratelimit import HostRateLimiter, parse_retry_after

log = get_logger(__name__)

ENDPOINT = "https://api.acoustid.org/v2/lookup"
HOST = "api.acoustid.org"

#: AcoustID allows three a second per key; this leaves room for an inexact clock.
PACE_SECONDS = 0.4

REFUSAL_BACKOFF_SECONDS = 60.0

REQUEST_TIMEOUT_SECONDS = 20.0

#: A cap on a machine Sift does not control.
MAX_ANSWER_BYTES = 1 << 20

#: The first two minutes: AcoustID keeps only the start of a song, so more buys nothing.
FIRST_VALUES = 948

#: The recordings and their submission counts, which `choose` weighs.
META = "recordings sources"

#: Correct answers on real files score above this.
FLOOR = 0.80

#: AcoustID's documented example, so the Check sends nothing of the library.
EXAMPLE_DURATION = 641
EXAMPLE_FINGERPRINT = (
    "AQABz0qUkZK4oOfhL-CPc4e5C_wW2H2QH9uDL4cvoT8UNQ-eHtsE8cceeFJx-LiiHT-aPzhxoc-Opj_eI5d2hOFyMJRz"
    "fDk-QSsu7fBxqZDMHcfxPfDIoPWxv9C1o3yg44d_3Df2GJaUQeeR-cb2HfaPNsdxHj2PJnpwPMN3aPcEMzd-_MeB_Ej4"
    "D_CLP8ghHjkJv_jh_UDuQ8xnILwunPg6hF2R8HgzvLhxHVYP_ziJX0eKPnIE1UePMByDJyg7wz_6yELsB8n4oDmDa0Gv"
    "40hf6D3CE3_wH6HFaxCPUD9-hNeF5MfWEP3SCGym4-SxnXiGs0mRjEXD6fgl4LmKWrSChzzC33ge9PB3otyJMk-IVC6R"
    "8MTNwD9qKQ_CC8kPv4THzEGZS8GPI3x0iGVUxC1hRSizC5VzoamYDi-uR7iKPhGSI82PkiWeB_eHijvsaIWfBCWH5Ajj"
    "CfVxZ1TQ3CvCTclGnEMfHbnZFA8pjD6KXwd__Cn-Y8e_I9cq6CR-4S9KLXqQcsxxoWh3eMxiHI6TIzyPv0M43YHz4yte"
    "-Cv-4D16Hv9F9C9SPUdyGtZRHV-OHEeeGD--BKcjVLOK_NCDXMfx44dzHEiOZ0Z44Rf6DH5R3uiPj4d_PKolJNyRJzyu"
    "4_CTD2WOvzjKH9GPb4cUP1Av9EuQd8fGCFee4JlRHi18xQh96NLxkCgfWFKOH6WGeoe4I3za4c5hTscTPEZTES1x8kE-"
    "9MQPjT8a8gh5fPgQZtqCFj9MDvp6fDx6NCd07bjx7MLR9AhtnFnQ70GjOcV0opmm4zpY3SOa7HiwdTtyHa6NC4e-HN-O"
    "fC5-OP_gLe2QDxfUCz_0w9l65HiPAz9-IaGOUA7-4MZ5CWFOlIfe4yUa6AiZGxf6w0fFxsjTOdC6Itbh4mGD63iPH9-R"
    "Fy909XAMj7mC5_BvlDyO6kGTZKJxHUd4NDwuZUffw_5RMsde5CWkJAgXnDReNEaP6DTOQ65yaD88HoeX8fge-DSeHo9Q"
    "a8cTHc80I-_RoHxx_UHeBxrJw62Q34Kd7MEfpCcu6BLeB1ePw6OO4sOF_sHhmB504WWDZiEu8sKPpkcfCT9xfej0o0lr"
    "4T5yNJeOvjmu40w-TDmqHXmYgfFhFy_M7tD1o0cO_B2ms2j-ACEEQgQgAIwzTgAGmBIKIImNQAABwgQATAlhDGCCEIGI"
    "IM4BaBgwQBogEBIOESEIA8ARI5xAhxEFmAGAMCKAURKQQpQzRAAkCCBQEAKkQYIYIQQxCixCDADCABMAE0gpJIgyxhED"
    "iCKCCIGAEIgJIQByAhFgGACCACMRQEyBAoxQiHiCBCFOECQFAIgAABR2QAgFjCDMA0AUMIoAIMChQghChASGEGeYEAIA"
    "IhgBSErnJPPEGWYAMgw05AhiiGHiBBBGGSCQcQgwRYJwhDDhgCSCSSEIQYwILoyAjAIigBFEUQK8gAYAQ5BCAAjkjCCA"
    "EEMZAUQAZQCjCCkpCgFMCCiIcVIAZZgilAQAiSHQECOcQAQIc4QClAHAjDDGkAGAMUoBgyhihgEChFCAAWEIEYwIJYwV"
    "iAAlHCBIGEIEAEIQAoBwwgwiEBAEEEOoEwBY4wRwxAhBgAcKAESIQAwwIowRFhoBhAE"
)


class AcoustIDUnreachable(Exception):
    """AcoustID could not be asked, or did not answer. The message is meant to be read."""


class AcoustIDRefused(AcoustIDUnreachable):
    """AcoustID answered, and said no: too many requests, a key it does not accept, or an error."""


#: Chromaprint's constants (`fingerprint_compressor.cpp`).
_MAX_NORMAL = 7
_NORMAL_BITS = 3
_EXCEPTION_BITS = 5


def compress(values: Sequence[int], algorithm: int = 1) -> str:
    """Chromaprint's compressed fingerprint (`FingerprintCompressor::Compress`) in base64."""
    normal: list[int] = []
    exceptions: list[int] = []
    previous = 0
    for place, value in enumerate(values):
        if not 0 <= value <= 0xFFFFFFFF:
            raise ValueError(f"a fingerprint value is a 32-bit word, not {value}")
        word = value if place == 0 else value ^ previous
        previous = value
        bit, last = 1, 0
        while word:
            if word & 1:
                gap = bit - last
                if gap >= _MAX_NORMAL:
                    normal.append(_MAX_NORMAL)
                    exceptions.append(gap - _MAX_NORMAL)
                else:
                    normal.append(gap)
                last = bit
            word >>= 1
            bit += 1
        normal.append(0)
    count = len(values)
    header = bytes((algorithm & 0xFF, (count >> 16) & 0xFF, (count >> 8) & 0xFF, count & 0xFF))
    packed = header + _pack(normal, _NORMAL_BITS) + _pack(exceptions, _EXCEPTION_BITS)
    return base64.urlsafe_b64encode(packed).decode("ascii").rstrip("=")


def _pack(values: Sequence[int], width: int) -> bytes:
    """Values of `width` bits each, least-significant bit first into a continuous byte stream."""
    out = bytearray()
    held = 0
    bits = 0
    mask = (1 << width) - 1
    for value in values:
        held |= (value & mask) << bits
        bits += width
        while bits >= 8:
            out.append(held & 0xFF)
            held >>= 8
            bits -= 8
    if bits:
        out.append(held & 0xFF)
    return bytes(out)


@dataclass(frozen=True, slots=True)
class Recording:
    """One recording AcoustID lists under a result."""

    id: str
    title: str
    artists: str
    #: None counts as one: a listed recording was submitted once.
    sources: int | None = None
    #: One by one, since `artists` is joined and a name may hold a comma.
    artist_names: tuple[str, ...] = field(default=(), compare=False)


@dataclass(frozen=True, slots=True)
class Result:
    """One fingerprint AcoustID matched, how well, and the recordings it is linked to."""

    id: str
    score: float
    recordings: tuple[Recording, ...]


@dataclass(frozen=True, slots=True)
class LookupAnswer:
    """What one lookup came back with. An empty tuple is a real answer: AcoustID knows nothing."""

    results: tuple[Result, ...]


@dataclass(frozen=True, slots=True)
class Chosen:
    """The recording a lookup names, and the score of the best result that listed it."""

    recording_id: str
    title: str
    artists: str
    score: float
    artist_names: tuple[str, ...] = field(default=(), compare=False)

    @property
    def song(self) -> str:
        """The name a file is given: "{title} - {artists}", the shape a Site's page gives."""
        return f"{self.title} - {self.artists}"


def choose(results: Sequence[Result]) -> Chosen | None:
    """The one song these results name, or None on a tie: a wrong name spreads to every pair."""
    listed: list[tuple[Result, Recording]] = [
        (result, recording)
        for result in results
        if result.score >= FLOOR
        for recording in result.recordings
        if recording.title.strip() and recording.artists.strip()
    ]
    if not listed:
        return None

    by_artists: Counter[str] = Counter()
    for _result, recording in listed:
        by_artists[_folded(recording.artists)] += _weight(recording)
    artists = _sole_most(by_artists)
    if artists is None:
        return None

    theirs = [(r, one) for r, one in listed if _folded(one.artists) == artists]
    by_title: Counter[str] = Counter()
    for _result, recording in theirs:
        by_title[_folded(recording.title)] += _weight(recording)
    title = _sole_most(by_title)
    if title is None:
        return None

    named = [(r, one) for r, one in theirs if _folded(one.title) == title]
    best = max(named, key=lambda pair: _weight(pair[1]))[1]
    return Chosen(
        recording_id=best.id,
        title=best.title.strip(),
        artists=best.artists.strip(),
        score=max(result.score for result, _one in named),
        artist_names=best.artist_names,
    )


def _weight(recording: Recording) -> int:
    return recording.sources if recording.sources is not None and recording.sources > 0 else 1


def _folded(text: str) -> str:
    return " ".join(text.split()).casefold()


def _sole_most(counts: Counter[str]) -> str | None:
    """The key with the highest count, or None when two share it."""
    ranked = counts.most_common(2)
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None
    return ranked[0][0]


def _recording(rec: Mapping[str, Any]) -> Recording:
    """One recording of an answer, its artists both joined and one by one."""
    names = tuple(
        str(artist.get("name"))
        for artist in rec.get("artists") or ()
        if isinstance(artist, Mapping) and artist.get("name")
    )
    return Recording(
        id=str(rec.get("id") or ""),
        title=str(rec.get("title") or ""),
        artists=", ".join(names),
        sources=rec.get("sources") if isinstance(rec.get("sources"), int) else None,
        artist_names=names,
    )


def answer_of(body: Mapping[str, Any]) -> LookupAnswer:
    """AcoustID's JSON as a `LookupAnswer`; an unexpected shape reads as less, never an error."""
    if body.get("status") != "ok":
        problem = body.get("error")
        said = problem.get("message") if isinstance(problem, Mapping) else None
        raise AcoustIDRefused(f"AcoustID refused the lookup: {said or 'no reason given'}.")
    found: list[Result] = []
    for one in body.get("results") or ():
        if not isinstance(one, Mapping):
            continue
        recordings = tuple(
            _recording(rec)
            for rec in one.get("recordings") or ()
            if isinstance(rec, Mapping) and rec.get("id")
        )
        try:
            score = float(one.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        found.append(Result(id=str(one.get("id") or ""), score=score, recordings=recordings))
    return LookupAnswer(results=tuple(found))


class Session(Protocol):
    """The one call a lookup makes of an aiohttp session. A test hands in its own."""

    def post(self, url: str, **kwargs: Any) -> AbstractAsyncContextManager[Any]: ...


async def lookup(
    session: Session,
    key: str,
    fingerprint_b64: str,
    duration: int,
    *,
    proxy: str | None = None,
    on_refused: Callable[[float], None] | None = None,
) -> LookupAnswer:
    """One gzipped lookup POST; the key goes in the body, and a 429 or 503 calls `on_refused`."""
    body = urlencode(
        {
            "client": key,
            "format": "json",
            "meta": META,
            "duration": str(int(duration)),
            "fingerprint": fingerprint_b64,
        }
    ).encode("ascii")
    try:
        async with session.post(
            ENDPOINT,
            data=gzip.compress(body),
            headers={
                "Content-Encoding": "gzip",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            proxy=proxy,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
        ) as answer:
            if answer.status in (429, 503):
                wait = parse_retry_after(answer.headers.get("Retry-After"))
                if on_refused is not None:
                    on_refused(wait or REFUSAL_BACKOFF_SECONDS)
                log.warning("music.acoustid_refused", backoff_s=wait or REFUSAL_BACKOFF_SECONDS)
                raise AcoustIDRefused(
                    "AcoustID asked Sift to slow down, so Sift waits before asking it again."
                )
            raw = await read_capped(answer.content, MAX_ANSWER_BYTES + 1)
            status = answer.status
    except aiohttp.ClientError as failure:
        raise AcoustIDUnreachable("Sift could not reach AcoustID.") from failure
    except TimeoutError as failure:
        raise AcoustIDUnreachable("AcoustID did not answer in time.") from failure
    if len(raw) > MAX_ANSWER_BYTES:
        raise AcoustIDUnreachable("AcoustID sent more than an answer.")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as failure:
        raise AcoustIDUnreachable(
            f"AcoustID answered {status} with something that was not an answer."
        ) from failure
    if not isinstance(parsed, Mapping):
        raise AcoustIDUnreachable("AcoustID sent something that was not an answer.")
    return answer_of(parsed)


class SessionFactory(Protocol):
    """Opens a session through the guarded connector handed in at boot; `proxy` reaches a tunnel."""

    def __call__(
        self, *, user_agent: str | None = None, proxy: str | None = None
    ) -> AbstractAsyncContextManager[Any]: ...


#: Holds a route for one request and yields its proxy, or None; raises when the tunnel is down.
RouteOpener = Callable[[str | None], AbstractAsyncContextManager[str | None]]


@asynccontextmanager
async def _no_tunnels(route: str | None) -> AsyncIterator[str | None]:
    """The route opener when none is handed in: direct only, and a tunnel id refuses."""
    if route is not None:
        raise LookupError("no tunnels are available here")
    yield None


class AcoustIDClient:
    """Asks AcoustID at Sift's pace, through the chosen route; one limiter for the whole process."""

    def __init__(
        self,
        open_session: SessionFactory,
        *,
        through: RouteOpener = _no_tunnels,
        clock: Callable[[], float] = time.monotonic,
        limiter: HostRateLimiter | None = None,
    ) -> None:
        self._open_session = open_session
        self._through = through
        self._limiter = limiter or HostRateLimiter(clock=clock)
        self._limiter.pace(HOST, PACE_SECONDS)

    async def ask(
        self, key: str, fingerprint_b64: str, duration: int, *, route: str | None
    ) -> LookupAnswer:
        """One paced lookup through `route`. Raises `AcoustIDUnreachable` (or `AcoustIDRefused`)."""
        # Declared on every ask: `pace` never undoes a recorded back-off.
        self._limiter.pace(HOST, PACE_SECONDS)
        await self._limiter.acquire(HOST)
        async with (
            self._way_out(route) as proxy,
            self._open_session(proxy=proxy) as session,
        ):
            return await lookup(
                session,
                key,
                fingerprint_b64,
                duration,
                proxy=proxy,
                on_refused=lambda wait: self._limiter.note_retry_after(HOST, wait),
            )

    async def check(self, key: str, *, route: str | None) -> tuple[bool, str]:
        """Whether this key works, by one lookup of AcoustID's example, and the sentence to show."""
        try:
            answer = await self.ask(key, EXAMPLE_FINGERPRINT, EXAMPLE_DURATION, route=route)
        except AcoustIDUnreachable as failure:
            return False, str(failure)
        if not answer.results:
            return False, "AcoustID answered but did not recognize its own example."
        return True, "AcoustID accepted the key."

    @asynccontextmanager
    async def _way_out(self, route: str | None) -> AsyncIterator[str | None]:
        """Hold the route for one request; a down tunnel refuses rather than going direct."""
        async with AsyncExitStack() as stack:
            try:
                proxy = await stack.enter_async_context(self._through(route))
            except Exception as exc:
                log.info("music.acoustid_route_unavailable", reason=type(exc).__name__)
                raise AcoustIDRefused(
                    "Music lookup is set to use a tunnel that isn't running or no"
                    " longer exists, so Sift didn't ask AcoustID. Turn the tunnel on in Settings >"
                    " Sites, or choose another tunnel for it in Settings > Music."
                ) from exc
            yield proxy
