# SPDX-License-Identifier: AGPL-3.0-or-later
"""The AcoustID adapter: the compressed fingerprint, the choice among answers, the request, the pace.

No network, ever: every request goes to a session this file stands up, and what it records is the
request that WOULD have left.

`compress` is proved two ways. Against AcoustID's documented example (in the tree), by decoding it
with an independent reader written here and compressing the values back. And against thirteen
calibration pairs where a machine holds a copy (see `calibration`): each
`<name>.first120.b64` was written by ffmpeg's own chromaprint muxer, and the raw file beside it by
the exact launch Sift makes, so compressing the raw's first N values (N read from the b64's own
header) must give the muxer's bytes exactly. The tree ships no fingerprints, so that test SKIPS
elsewhere and says so.
"""

from __future__ import annotations

import base64
import gzip
import struct
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import aiohttp
import pytest

from sift.kernel.ratelimit import HostRateLimiter
from sift.slices.music import acoustid
from sift.slices.music.acoustid import (
    EXAMPLE_FINGERPRINT,
    FIRST_VALUES,
    AcoustIDClient,
    AcoustIDRefused,
    AcoustIDUnreachable,
    Recording,
    Result,
    answer_of,
    choose,
    compress,
)
from sift.slices.music.tests import calibration

#: Where the calibration fingerprints are, on a machine that has them. See `calibration`.
CALIBRATION = calibration.folder()


def _decompress(text: str) -> tuple[int, list[int]]:
    """AcoustID's compressed form read back, written independently of `compress` (a reader, not a
    writer): the header, the three-bit stream up to one 0 per value, the five-bit stream after it."""
    data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    algorithm, count, body = data[0], int.from_bytes(data[1:4], "big"), data[4:]

    def bits(stream: bytes, width: int, start_bit: int, n: int) -> list[int]:
        out = []
        for index in range(n):
            at = start_bit + index * width
            value = 0
            for bit in range(width):
                byte, offset = divmod(at + bit, 8)
                value |= ((stream[byte] >> offset) & 1) << bit
            out.append(value)
        return out

    normal: list[int] = []
    zeros = 0
    while zeros < count:
        (value,) = bits(body, 3, len(normal) * 3, 1)
        normal.append(value)
        zeros += value == 0
    rest = body[(len(normal) * 3 + 7) // 8 :]
    exceptions = iter(bits(rest, 5, 0, sum(1 for one in normal if one == 7)))
    values: list[int] = []
    word, last, previous = 0, 0, 0
    for gap in normal:
        if gap == 0:
            value = word if not values else word ^ previous
            values.append(value)
            previous, word, last = value, 0, 0
            continue
        last += gap + (next(exceptions) if gap == 7 else 0)
        word |= 1 << (last - 1)
    return algorithm, values


def test_compress_gives_back_acoustids_documented_example() -> None:
    algorithm, values = _decompress(EXAMPLE_FINGERPRINT)
    assert (algorithm, len(values)) == (1, 463)
    assert compress(values, algorithm) == EXAMPLE_FINGERPRINT


def _calibration_pairs() -> list[Path]:
    return sorted(CALIBRATION.glob("*.first120.b64")) if CALIBRATION is not None else []


@pytest.mark.skipif(not _calibration_pairs(), reason=calibration.WHY_SKIPPED)
@pytest.mark.parametrize("b64", _calibration_pairs(), ids=lambda path: path.name.split(".")[0])
def test_compress_matches_ffmpegs_own_muxer_byte_for_byte(b64: Path) -> None:
    said = b64.read_text().strip()
    raw = b64.with_name(b64.name.replace(".first120.b64", ".raw")).read_bytes()
    values = struct.unpack(f"<{len(raw) // 4}I", raw)
    header = base64.urlsafe_b64decode(said[:8])
    count = int.from_bytes(header[1:4], "big")
    assert compress(values[:count], header[0]) == said


def test_the_first_two_minutes_are_the_count_the_muxer_wrote() -> None:
    """The ten full-length files' first120 headers all say 948; the lookup sends that many."""
    long_ones = [
        path for path in _calibration_pairs() if path.name[0] in "LP"
    ]  # the full-length files, not the clips
    for path in long_ones:
        header = base64.urlsafe_b64decode(path.read_text().strip()[:8])
        assert int.from_bytes(header[1:4], "big") == FIRST_VALUES


def test_compress_of_nothing_is_a_header_and_a_word_out_of_range_is_refused() -> None:
    assert base64.urlsafe_b64decode(compress([]) + "==") == b"\x01\x00\x00\x00"
    with pytest.raises(ValueError, match="32-bit"):
        compress([1 << 32])


# --- choose: one test per rule ---------------------------------------------------------------------


def _rec(rid: str, title: str, artists: str, sources: int | None = None) -> Recording:
    return Recording(id=rid, title=title, artists=artists, sources=sources)


def test_choose_ignores_every_result_under_the_floor() -> None:
    under = Result(id="r", score=0.79, recordings=(_rec("a", "Blue", "Marla Quist"),))
    assert choose([under]) is None
    at = Result(id="r", score=0.80, recordings=(_rec("a", "Blue", "Marla Quist"),))
    assert choose([at]) is not None


def test_choose_names_the_artists_who_appear_most() -> None:
    """As AcoustID answers a real file: one result listing four recordings by the singers, one by a
    DJ."""
    result = Result(
        id="r",
        score=0.83,
        recordings=(
            _rec("a", "Blue", "Marla Quist, Juno Marsh"),
            _rec("b", "blue", "Marla Quist, Juno Marsh"),
            _rec("c", "Blue", "Marla Quist, Juno Marsh"),
            _rec("d", "Blue", "Robin Vale"),
        ),
    )
    chosen = choose([result])
    assert chosen is not None
    assert chosen.song == "Blue - Marla Quist, Juno Marsh"
    assert chosen.score == pytest.approx(0.83)


def test_choose_settles_the_artists_before_it_looks_at_titles() -> None:
    """Two titles by the artists who appear most, against one title listed twice by another: the
    artists win first (three to two), then their commoner title. Weighed by title first, the two
    would tie at two and nothing would be named."""
    result = Result(
        id="r",
        score=0.9,
        recordings=(
            _rec("a", "Blue", "Marla Quist"),
            _rec("b", "Blue", "Marla Quist"),
            _rec("c", "Green", "Marla Quist"),
            _rec("d", "Red", "Robin Vale"),
            _rec("e", "Red", "Robin Vale"),
        ),
    )
    chosen = choose([result])
    assert chosen is not None
    assert chosen.song == "Blue - Marla Quist"


def test_choose_takes_the_title_with_the_most_sources() -> None:
    result = Result(
        id="r",
        score=0.9,
        recordings=(
            _rec("a", "Blue", "Marla Quist", sources=40),
            _rec("b", "Blue (on vacation)", "Marla Quist", sources=3),
            _rec("c", "Blue (on vacation)", "Marla Quist", sources=4),
        ),
    )
    chosen = choose([result])
    assert chosen is not None
    assert (chosen.recording_id, chosen.title) == ("a", "Blue")


def test_choose_names_nothing_on_a_tie() -> None:
    artists_tie = Result(
        id="r",
        score=0.9,
        recordings=(_rec("a", "Blue", "Marla Quist"), _rec("b", "Blue", "Robin Vale")),
    )
    assert choose([artists_tie]) is None
    titles_tie = Result(
        id="r",
        score=0.9,
        recordings=(_rec("a", "Blue", "Marla Quist"), _rec("b", "Red", "Marla Quist")),
    )
    assert choose([titles_tie]) is None


def test_an_error_answer_is_a_refusal_and_a_shape_it_does_not_know_is_read_as_less() -> None:
    with pytest.raises(AcoustIDRefused, match="invalid API key"):
        answer_of({"status": "error", "error": {"code": 4, "message": "invalid API key"}})
    read = answer_of(
        {
            "status": "ok",
            "results": [
                {"id": "r", "score": 0.9, "recordings": [{"id": "x", "title": "Blue"}]},
                "not a result",
            ],
        }
    )
    assert read.results[0].recordings[0].artists == ""
    assert choose(read.results) is None


# --- the request, the refusal, the pace -----------------------------------------------------------


class _Answer:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = body
        self.content = self

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        yield self._body


class _Session:
    """Records every POST and answers with what it was given. Nothing leaves the process."""

    def __init__(self, answers: list[_Answer]) -> None:
        self.answers = answers
        self.sent: list[dict[str, Any]] = []

    @asynccontextmanager
    async def post(self, url: str, **kwargs: Any) -> AsyncIterator[_Answer]:
        self.sent.append({"url": url, **kwargs})
        yield self.answers.pop(0)


_OK = b'{"status": "ok", "results": [{"id": "r", "score": 0.9, "recordings": [{"id": "x", "title": "Blue", "artists": [{"name": "Marla Quist"}], "sources": 3}]}]}'


@pytest.mark.asyncio
async def test_the_request_is_the_spikes_gzipped_form_with_the_key_in_the_body() -> None:
    session = _Session([_Answer(200, _OK)])
    answer = await acoustid.lookup(session, "the-key", "AQAB", 187)
    assert choose(answer.results) is not None
    (sent,) = session.sent
    assert sent["url"] == acoustid.ENDPOINT
    assert "the-key" not in sent["url"]
    assert sent["headers"]["Content-Encoding"] == "gzip"
    fields = parse_qs(gzip.decompress(sent["data"]).decode("ascii"))
    assert fields == {
        "client": ["the-key"],
        "format": ["json"],
        "meta": ["recordings sources"],
        "duration": ["187"],
        "fingerprint": ["AQAB"],
    }


@pytest.mark.asyncio
async def test_a_429_backs_the_host_off_and_refuses() -> None:
    told: list[float] = []
    session = _Session([_Answer(429, b"", {"Retry-After": "30"})])
    with pytest.raises(AcoustIDRefused, match="slow down"):
        await acoustid.lookup(session, "k", "AQAB", 187, on_refused=told.append)
    assert told == [30.0]


class _Raising:
    """A context that refuses on the way in, as a tunnel that is down or a connection that fails."""

    def __init__(self, failure: Exception) -> None:
        self._failure = failure

    async def __aenter__(self) -> Any:
        raise self._failure

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _client(session: _Session, clock: _Clock, **kwargs: Any) -> AcoustIDClient:
    @asynccontextmanager
    async def opened(*, user_agent: str | None = None, proxy: str | None = None) -> Any:
        yield session

    return AcoustIDClient(opened, limiter=HostRateLimiter(clock=clock, sleep=clock.sleep), **kwargs)


@pytest.mark.asyncio
async def test_lookups_are_paced_at_one_every_four_tenths_of_a_second() -> None:
    clock = _Clock()
    session = _Session([_Answer(200, _OK), _Answer(200, _OK)])
    client = _client(session, clock)
    await client.ask("k", "AQAB", 187, route=None)
    await client.ask("k", "AQAB", 174, route=None)
    assert clock.slept == [pytest.approx(acoustid.PACE_SECONDS)]


@pytest.mark.asyncio
async def test_a_tunnel_that_is_down_refuses_and_nothing_is_sent() -> None:
    def down(route: str | None) -> _Raising:
        return _Raising(LookupError("down"))

    session = _Session([])
    client = _client(session, _Clock(), through=down)
    with pytest.raises(AcoustIDRefused, match="tunnel that isn't running"):
        await client.ask("k", "AQAB", 187, route="tunnel-1")
    assert session.sent == []


@pytest.mark.asyncio
async def test_the_check_sends_the_documented_example_and_says_so() -> None:
    session = _Session([_Answer(200, _OK)])
    ok, said = await _client(session, _Clock()).check("k", route=None)
    assert (ok, said) == (True, "AcoustID accepted the key.")
    fields = parse_qs(gzip.decompress(session.sent[0]["data"]).decode("ascii"))
    assert fields["fingerprint"] == [EXAMPLE_FINGERPRINT]
    assert fields["duration"] == [str(acoustid.EXAMPLE_DURATION)]


@pytest.mark.asyncio
async def test_a_network_failure_is_said_in_sifts_words() -> None:
    class _Broken:
        def post(self, url: str, **kwargs: Any) -> _Raising:
            return _Raising(
                aiohttp.ClientConnectionError("https://api.acoustid.org/?client=secret")
            )

    with pytest.raises(AcoustIDUnreachable) as caught:
        await acoustid.lookup(_Broken(), "secret", "AQAB", 187)
    assert str(caught.value) == "Sift could not reach AcoustID."


def test_a_score_that_is_not_a_number_is_read_as_no_score() -> None:
    """Read as less, never as an error: a result that says nothing usable about its score is one
    that clears no floor."""
    read = answer_of({"status": "ok", "results": [{"id": "r", "score": "high", "recordings": []}]})
    assert [(one.id, one.score) for one in read.results] == [("r", 0.0)]


@pytest.mark.asyncio
async def test_a_429_with_nobody_to_tell_still_refuses() -> None:
    session = _Session([_Answer(503, b"")])
    with pytest.raises(AcoustIDRefused, match="slow down"):
        await acoustid.lookup(session, "k", "AQAB", 187)


@pytest.mark.asyncio
async def test_an_answer_that_never_arrives_is_said_in_sifts_words() -> None:
    class _Slow:
        def post(self, url: str, **kwargs: Any) -> _Raising:
            return _Raising(TimeoutError())

    with pytest.raises(AcoustIDUnreachable, match="did not answer in time"):
        await acoustid.lookup(_Slow(), "k", "AQAB", 187)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "said"),
    [
        (b" " * (acoustid.MAX_ANSWER_BYTES + 1), "sent more than an answer"),
        (b"<html>busy</html>", "answered 200 with something that was not an answer"),
        (b"\xff\xfe", "answered 200 with something that was not an answer"),
        (b'["not", "an", "object"]', "sent something that was not an answer"),
    ],
    ids=["too-long", "not-json", "not-text", "not-an-object"],
)
async def test_an_answer_that_is_not_one_is_refused_as_unreachable(body: bytes, said: str) -> None:
    """Too long to be an answer, not JSON, not text, or JSON of the wrong shape: each is AcoustID
    failing to answer, never a song."""
    with pytest.raises(AcoustIDUnreachable, match=said):
        await acoustid.lookup(_Session([_Answer(200, body)]), "k", "AQAB", 187)


@pytest.mark.asyncio
async def test_a_tunnel_named_where_there_are_none_refuses_and_nothing_is_sent() -> None:
    """A client given no way to open tunnels still refuses a route rather than going direct: the
    route exists so AcoustID does not see this device's own address."""
    session = _Session([])
    with pytest.raises(AcoustIDRefused, match="tunnel that isn't running"):
        await _client(session, _Clock()).ask("k", "AQAB", 187, route="tunnel-1")
    assert session.sent == []


@pytest.mark.asyncio
async def test_the_check_says_why_a_key_could_not_be_proved() -> None:
    """Unreachable is said in Sift's words; an answer naming nothing for its own example is a key
    that did not work."""
    unreachable = _Session([_Answer(200, b"not json")])
    ok, said = await _client(unreachable, _Clock()).check("k", route=None)
    assert ok is False
    assert said == "AcoustID answered 200 with something that was not an answer."

    empty = _Session([_Answer(200, b'{"status": "ok", "results": []}')])
    assert await _client(empty, _Clock()).check("k", route=None) == (
        False,
        "AcoustID answered but did not recognize its own example.",
    )
