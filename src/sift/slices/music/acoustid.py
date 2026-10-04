# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking AcoustID which song a file's fingerprint is, when an admin has turned that on.

AcoustID is the one free service that names a song from a Chromaprint fingerprint. What leaves
this device is the fingerprint of the first two minutes of the sound and the file's length, never
the file, and nothing leaves at all unless `music.lookup` is on and a key is set (`lookup.py`
asks both at run time). It is the same class of sender as the stash-box switch, and it is paced,
routed and refused the same way (`slices/stash_boxes/adapter.py`).

## What AcoustID answers, on real files

Full-length music videos whose songs are known are mostly named correctly at the file's own
length, and wrongly none of the time, at scores well above `FLOOR`. Some are named only when sent
with another length, because AcoustID's server only considers recordings within 7 seconds of the
length it is sent (`FINGERPRINT_MAX_LENGTH_DIFF = 7` in its own code). Short excerpts and clips
name nothing, as AcoustID's own FAQ says they will not: the service was built for whole songs. So
this names full-length files, and a short clip gets its name from a file it shares a song with
(`names.py`), never from here.

## The fingerprint AcoustID is sent is Chromaprint's COMPRESSED form

Sift keeps the raw values (`kernel/chromaprint.py`); AcoustID takes the compressed base64 its own
tool prints. `compress` makes one from the other in pure Python and is proved byte-for-byte against
pairs made with ffmpeg's own muxer and against AcoustID's documented example
(`tests/test_acoustid.py`).
"""

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

#: One request every 0.4 s. AcoustID allows three a second per application key; this keeps under
#: that with room for a clock that is not exact.
PACE_SECONDS = 0.4

#: How long a "slow down" keeps AcoustID off limits when it names no wait of its own.
REFUSAL_BACKOFF_SECONDS = 60.0

REQUEST_TIMEOUT_SECONDS = 20.0

#: The most of an answer that is read. A lookup's answer is a few kilobytes; this is a cap on a
#: machine Sift does not control, not a size anything is expected to reach.
MAX_ANSWER_BYTES = 1 << 20

#: How many values of the stored fingerprint are sent: the first two minutes. Checked twice over:
#: ffmpeg's chromaprint muxer given `-t 120` writes exactly 948 values for a full-length track,
#: and every recording longer than 118 s in a sample of AcoustID's own data holds exactly
#: 948. AcoustID keeps only the start of a song, so sending more buys nothing.
FIRST_VALUES = 948

#: What is asked for beside the ids: the recordings (their titles and artists) and how many
#: submissions each has, which is what `choose` weighs them by.
META = "recordings sources"

#: The lowest score a result may have and still name a song. Correct answers score above it; an
#: answer just below it (at a length far off the song's) can name the right song, and such a file
#: gets the name anyway through the file it pairs with.
FLOOR = 0.80

#: AcoustID's documented example: the fingerprint and length its own web service page gives
#: (https://acoustid.org/webservice). The Check sends this, which proves the key works without
#: sending anything of the library.
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


# --- the compressed fingerprint -------------------------------------------------------------------

#: A gap between two set bits shorter than this is written in three bits; one this long or longer
#: writes 7 there and the rest (gap - 7) in five bits in the second list. Chromaprint's constants
#: (`fingerprint_compressor.cpp`: kMaxNormalValue, kNormalBits, kExceptionBits).
_MAX_NORMAL = 7
_NORMAL_BITS = 3
_EXCEPTION_BITS = 5


def compress(values: Sequence[int], algorithm: int = 1) -> str:
    """Chromaprint's compressed fingerprint, as the URL-safe base64 AcoustID is sent.

    The reference implementation's algorithm (`FingerprintCompressor::Compress`), step by step:

    1. **Differences.** The first value is taken as it is; every later one is XORed with the one
       before it, so a run of similar values becomes a run of words with few bits set.
    2. **Bit gaps.** For each word, walk its set bits from the lowest, writing the distance from the
       previous set bit (the first counts from bit 0, so bit 0 set is a gap of 1). A gap under 7 is
       written as it is; a gap of 7 or more writes 7 and puts `gap - 7` in a second list. A 0 ends
       each word, so a word with no bits set is just the 0.
    3. **Packing.** The first list is packed three bits a value and the second five bits a value,
       each least-significant bit first into a continuous stream of bytes, the last byte padded with
       zero bits.
    4. **Header.** One byte of algorithm number and three bytes of value count, big-endian, then the
       three-bit stream, then the five-bit stream.
    5. **Base64**, URL-safe alphabet (`-` and `_`), no padding: what `fpcalc` prints and what
       AcoustID's lookup takes.

    Pure and total: any list of 32-bit values compresses, the empty list included (a header and
    nothing else). A value outside 32 bits is refused rather than silently masked.
    """
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


# --- the answer, and choosing from it -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Recording:
    """One recording AcoustID lists under a result."""

    id: str
    title: str
    artists: str
    #: How many submissions AcoustID has linking this recording to the fingerprint. None where the
    #: answer did not say, which `choose` weighs as one: a recording listed at all was submitted once.
    sources: int | None = None
    #: The same artists one by one, in AcoustID's order: what a song credits (`songs.credit`).
    #: `artists` is them joined, which a name holding a comma would split wrongly.
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
    #: The artists one by one, in AcoustID's order. See `Recording.artist_names`.
    artist_names: tuple[str, ...] = field(default=(), compare=False)

    @property
    def song(self) -> str:
        """The name a file is given: "{title} - {artists}", the shape a Site's page gives."""
        return f"{self.title} - {self.artists}"


def choose(results: Sequence[Result]) -> Chosen | None:
    """The one song these results name, or None where they do not name one clearly.

    The rule, in the order it is applied:

    1. **The floor.** Only results scoring `FLOOR` or more count, and only their recordings with a
       title and at least one artist.
    2. **The artists.** A result can list recordings by different artists (four by the singers
       and one by a DJ, say): the artists who appear most across everything left win, each recording
       weighing its `sources`. A tie between two sets of artists names nothing.
    3. **The recording.** Among those artists' recordings, grouped by title (letter case aside),
       the title with the most `sources` wins. A tie names nothing. Its best-weighted recording is
       the one named, with the best score among the results that listed that title.

    "Names nothing" is the honest answer to a tie: a wrong name spread to every file sharing the
    song is worse than no name, and the person can type one.
    """
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
    """AcoustID's JSON as a `LookupAnswer`. Its `status` and `error` are read first.

    A shape this does not expect is read as less, never as an error: a result with no recordings is
    a result naming nothing, and an artist with no name is left out of the credit.
    """
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


# --- asking ---------------------------------------------------------------------------------------


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
    """One lookup: a POST of the compressed fingerprint and a length, gzip-compressed.

    The body is gzipped because a fingerprint is a few kilobytes of base64 and AcoustID asks for it.
    The key goes in the body, never the address, so no log of an address can carry it; and a failure
    is raised with a sentence of Sift's rather than the exception, which can carry the request.

    A `429` or `503` is AcoustID asking for less: `on_refused` is told how long to keep off (its
    `Retry-After`, or `REFUSAL_BACKOFF_SECONDS`) and the lookup is refused.
    """
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
    """Opens a session whose connections are vetted before they are made: the application's
    guarded connector (`slices/download/sources/net.guarded_session`), handed in at boot exactly as
    the stash-boxes are handed it. `proxy` is how a tunnel is reached; None is direct."""

    def __call__(
        self, *, user_agent: str | None = None, proxy: str | None = None
    ) -> AbstractAsyncContextManager[Any]: ...


#: Holds one stored route (None or a tunnel id) for the length of a request and yields the proxy
#: address, or None for direct; raises when the tunnel is gone or down. The downloads' own resolver,
#: handed in at boot, the same one a stash-box is handed (`stash_boxes.adapter.RouteOpener`).
RouteOpener = Callable[[str | None], AbstractAsyncContextManager[str | None]]


@asynccontextmanager
async def _no_tunnels(route: str | None) -> AsyncIterator[str | None]:
    """The route opener when none is handed in: direct only, and a tunnel id refuses."""
    if route is not None:
        raise LookupError("no tunnels are available here")
    yield None


class AcoustIDClient:
    """Asks AcoustID at Sift's pace, through the route an admin chose. One per application.

    The limiter is held here rather than per job, so every lookup this process makes queues behind
    the last one however many jobs are running: the property that keeps a whole library's worth of
    lookups at three a second rather than three a second per worker.
    """

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
        # Declared again on every ask, as a stash-box's pace is: `pace` never undoes a back-off a
        # refusal recorded, and a limiter handed in by a test starts with no pace of its own.
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
        """Whether this key works, by one lookup of AcoustID's documented example. Nothing of the
        library is sent. The sentence is what the settings screen shows beside the result."""
        try:
            answer = await self.ask(key, EXAMPLE_FINGERPRINT, EXAMPLE_DURATION, route=route)
        except AcoustIDUnreachable as failure:
            return False, str(failure)
        if not answer.results:
            return False, "AcoustID answered but did not recognize its own example."
        return True, "AcoustID accepted the key."

    @asynccontextmanager
    async def _way_out(self, route: str | None) -> AsyncIterator[str | None]:
        """Hold the route for one request and yield the proxy address, or None for direct.

        A tunnel that is gone or down refuses the lookup, in the words a stash-box gets, never a
        fall back to a direct connection: the route exists so AcoustID does not see this device's
        own address. Only the ENTRY is caught: the opener's refusal comes from the tunnels, whose
        own error type this slice may not import.
        """
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
