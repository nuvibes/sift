# SPDX-License-Identifier: AGPL-3.0-or-later
"""The direct fetcher: it streams one media address to a file and bounds the transfer.

The session is faked: these tests are about the stall floor, the extension sniff and the partial-
file cleanup, not about aiohttp's wire behaviour. The guarded session that vets and pins the address
is tested in test_net.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import fields, is_dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import aiohttp
import pytest

from sift.slices.download.sources import fetcher, policy
from sift.slices.download.sources.argv import CONCERNS, Concern
from sift.slices.download.sources.errors import FetchFailed, NoAnswer, NothingFound
from sift.slices.download.sources.ratelimit import AROUND_THE_TOOL, HostRateLimiter
from sift.slices.download.sources.tuning import POLICY, Filters, Pacing, RunPolicy
from sift.slices.download.url_guard import MAX_REDIRECT_HOPS, UrlRejected


@pytest.fixture(autouse=True)
def _stub_check_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the address guard to a no-op. The session here is faked, so there is nothing real to
    resolve; the guard itself is exercised against real resolution in test_url_guard and test_net.
    The SSRF tests below override this to make it refuse."""
    monkeypatch.setattr(fetcher, "check_url", lambda _url, **_k: None)


class _Content:
    def __init__(self, chunks: list[bytes], raise_exc: Exception | None = None) -> None:
        self._chunks = chunks
        self._raise_exc = raise_exc

    async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk
        if self._raise_exc is not None:
            raise self._raise_exc


class _Response:
    def __init__(
        self,
        *,
        chunks: list[bytes],
        headers: dict[str, str] | None = None,
        status: int = 200,
        status_exc: Exception | None = None,
        stream_exc: Exception | None = None,
    ) -> None:
        self.headers = headers or {}
        self.status = status
        self._status_exc = status_exc
        self.content = _Content(chunks, stream_exc)

    def raise_for_status(self) -> None:
        if self._status_exc is not None:
            raise self._status_exc

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _Session:
    """Captures the request and hands back a scripted response."""

    def __init__(self, response: _Response) -> None:
        self._response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(
        self, url: str, *, headers: dict[str, str], timeout: object, allow_redirects: bool = True
    ) -> _Response:
        self.calls.append((url, headers))
        return self._response


class _SeqSession:
    """Hands back a scripted sequence of responses, one per request; the last one repeats."""

    def __init__(self, responses: list[_Response]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(
        self, url: str, *, headers: dict[str, str], timeout: object, allow_redirects: bool = True
    ) -> _Response:
        self.calls.append((url, headers))
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


def _as_session(fake: _Session | _SeqSession) -> aiohttp.ClientSession:
    return cast("aiohttp.ClientSession", fake)


def _fake(**kw: object) -> _Session:
    return _Session(_Response(**kw))  # type: ignore[arg-type]


async def test_a_successful_fetch_writes_the_bytes_and_reports_the_count(tmp_path: Path) -> None:
    session = _fake(chunks=[b"aaa", b"bbbb"])
    dest = tmp_path / "clip.mp4"

    final, written = await fetcher.fetch_to_file(
        _as_session(session), url="https://cdn.example/clip.mp4", dest=dest
    )

    assert final == dest
    assert written == 7
    assert dest.read_bytes() == b"aaabbbb"


async def test_a_paused_download_asks_for_the_rest_and_appends_it(tmp_path: Path) -> None:
    """What resuming a plain direct fetch IS: a `Range` header and an append.

    The header is asserted as well as the bytes because the two can disagree in the one way that
    matters: appending without having asked for a range writes the whole file onto the end of the
    part already there, and the result is a file that is longer than the original and plays as
    nothing.
    """
    session = _Session(_Response(chunks=[b"ccc"], status=206))
    dest = tmp_path / "clip.mp4"
    dest.write_bytes(b"aabb")

    final, on_disk = await fetcher.fetch_to_file(
        _as_session(session), url="https://cdn.example/clip.mp4", dest=dest, resume_from=4
    )

    assert session.calls[0][1]["Range"] == "bytes=4-"
    assert final == dest
    assert dest.read_bytes() == b"aabbccc"
    # What is ON DISK, resumed bytes included, so an album's running total stays right.
    assert on_disk == 7


async def test_a_site_that_ignores_the_range_is_written_from_the_start(tmp_path: Path) -> None:
    """A site is not obliged to agree, and the answer is what decides it, not the ask.

    Trusting the ask here is the corruption above: a 200 is the whole file, so appending it to the
    part already on disk produces four bytes of rubbish in front of a perfectly good download.
    """
    session = _Session(_Response(chunks=[b"whole"], status=200))
    dest = tmp_path / "clip.mp4"
    dest.write_bytes(b"aabb")

    _final, on_disk = await fetcher.fetch_to_file(
        _as_session(session), url="https://cdn.example/clip.mp4", dest=dest, resume_from=4
    )

    assert dest.read_bytes() == b"whole"
    assert on_disk == 5


async def test_a_site_saying_there_is_nothing_past_that_point_leaves_the_file_alone(
    tmp_path: Path,
) -> None:
    """416 means the file was already whole: the pause landed on the last byte.

    Read before `raise_for_status`, deliberately: left to it, the one answer that means "you
    already have it" becomes a failure, and the failure path deletes the file it is about.
    """
    session = _Session(_Response(chunks=[], status=416, status_exc=FetchFailed("refused")))
    dest = tmp_path / "clip.mp4"
    dest.write_bytes(b"aabb")

    final, on_disk = await fetcher.fetch_to_file(
        _as_session(session), url="https://cdn.example/clip.mp4", dest=dest, resume_from=4
    )

    assert (final, on_disk) == (dest, 4)
    assert dest.read_bytes() == b"aabb"


async def test_an_ordinary_fetch_asks_for_no_range_at_all(tmp_path: Path) -> None:
    """The known negative: nothing is resumed unless something was paused, so the header is absent
    on every other download and a site that handles ranges badly never sees one."""
    session = _fake(chunks=[b"aaa"])

    await fetcher.fetch_to_file(
        _as_session(session), url="https://cdn.example/clip.mp4", dest=tmp_path / "clip.mp4"
    )

    assert "Range" not in session.calls[0][1]


async def test_an_extensionless_dest_gets_its_extension_from_the_content_type(
    tmp_path: Path,
) -> None:
    session = _fake(chunks=[b"data"], headers={"Content-Type": "image/jpeg; charset=binary"})
    dest = tmp_path / "0"  # a Pixeldrain-style extensionless address

    final, _ = await fetcher.fetch_to_file(
        _as_session(session), url="https://pixeldrain.com/api/file/x", dest=dest
    )

    assert final == tmp_path / "0.jpg"
    assert final.read_bytes() == b"data"


async def test_an_extensionless_dest_with_no_content_type_stays_bare(tmp_path: Path) -> None:
    session = _fake(chunks=[b"data"])  # no Content-Type header at all
    dest = tmp_path / "0"

    final, _ = await fetcher.fetch_to_file(
        _as_session(session), url="https://cdn.example/x", dest=dest
    )

    assert final == dest  # nothing to sniff, so the name is unchanged


async def test_an_unknown_content_type_leaves_the_name_alone(tmp_path: Path) -> None:
    session = _fake(chunks=[b"data"], headers={"Content-Type": "application/octet-stream"})
    dest = tmp_path / "0"

    final, _ = await fetcher.fetch_to_file(
        _as_session(session), url="https://cdn.example/x", dest=dest
    )

    assert final == dest  # no confident extension, so the name is unchanged


async def test_a_crawling_transfer_is_abandoned_after_the_grace(tmp_path: Path) -> None:
    # started at 0, first read reported at 31s with only a few bytes -> below the throughput floor.
    #
    # Handed in rather than patched over `time.monotonic`: asyncio's scheduler reads that same
    # function, so a fake clock installed globally would be drained by the event loop before the
    # transfer ever asks it the time, and the test would look like a broken stall detector.
    values = iter([0.0, 31.0])
    session = _fake(chunks=[b"tiny", b"more"])
    dest = tmp_path / "slow.mp4"

    with pytest.raises(NoAnswer):
        await fetcher.fetch_to_file(
            _as_session(session),
            url="https://cdn/x",
            dest=dest,
            clock=lambda: next(values, 62.0),
            policy=_policy(retries=0),
        )
    assert not dest.exists()


class _Silent:
    """A request the Site never answers: entering it times out before any header arrives."""

    async def __aenter__(self) -> _Response:
        raise aiohttp.ConnectionTimeoutError("connect timed out")

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _BadCertificate:
    """A request whose TLS handshake fails on the certificate, the same answer however often."""

    async def __aenter__(self) -> _Response:
        key = SimpleNamespace(host="cdn.example", port=443, ssl=True)
        raise aiohttp.ClientSSLError(cast("Any", key), OSError(1, "certificate verify failed"))

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _Refused:
    """A request whose connection is refused at once, which costs nothing to ask again."""

    async def __aenter__(self) -> _Response:
        raise aiohttp.ClientConnectionError("refused")

    async def __aexit__(self, *_exc: object) -> None:
        return None


async def test_a_site_that_never_answers_costs_one_wait_whatever_the_retry_count(
    tmp_path: Path,
) -> None:
    """Every ask of a silent Site costs the whole timeout, so it is asked once and the fetch ends
    as `NoAnswer`, which the job does not retry either."""
    session = _SeqSession([_Silent()])  # type: ignore[list-item]
    dest = tmp_path / "a.mp4"
    with pytest.raises(NoAnswer) as caught:
        await fetcher.fetch_to_file(
            _as_session(session),
            url="https://cdn.example/a.mp4",
            dest=dest,
            policy=_policy(retries=3),
        )
    assert len(session.calls) == 1
    assert caught.value.code == "no-answer"
    assert not dest.exists()


async def test_a_stall_after_the_site_answered_is_asked_once_more_and_resumes(
    tmp_path: Path,
) -> None:
    stalled = _Response(chunks=[b"aaa"], stream_exc=aiohttp.SocketTimeoutError("read timed out"))
    rest = _Response(chunks=[b"bbb"], status=206)
    session = _SeqSession([stalled, rest])
    dest = tmp_path / "a.mp4"

    _final, on_disk = await fetcher.fetch_to_file(
        _as_session(session), url="https://cdn.example/a.mp4", dest=dest, policy=_policy(retries=3)
    )

    assert dest.read_bytes() == b"aaabbb"
    assert on_disk == 6
    assert session.calls[1][1]["Range"] == "bytes=3-"


async def test_a_stall_is_asked_again_only_once_however_high_the_retry_count(
    tmp_path: Path,
) -> None:
    def stalling() -> _Response:
        return _Response(chunks=[b"a"], stream_exc=aiohttp.SocketTimeoutError("read timed out"))

    session = _SeqSession([stalling(), stalling(), stalling(), _Response(chunks=[b"ok"])])
    with pytest.raises(NoAnswer):
        await fetcher.fetch_to_file(
            _as_session(session),
            url="https://cdn.example/a.mp4",
            dest=tmp_path / "a.mp4",
            policy=_policy(retries=5),
        )
    assert len(session.calls) == 2


async def test_a_refused_connection_is_asked_again_at_once_up_to_the_retry_count(
    tmp_path: Path,
) -> None:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    session = _SeqSession([_Refused(), _Refused(), _Response(chunks=[b"ok"])])  # type: ignore[list-item]
    _final, written = await fetcher.fetch_to_file(
        _as_session(session),
        url="https://cdn.example/a.mp4",
        dest=tmp_path / "a.mp4",
        policy=_policy(retries=3),
        sleep=sleep,
    )
    assert written == 2
    assert len(session.calls) == 3
    assert waits == []


async def test_a_bad_certificate_is_not_asked_again_whatever_the_retry_count(
    tmp_path: Path,
) -> None:
    """A TLS failure is a connection error to aiohttp, and the one kind of it asking again cannot
    change: the certificate is the same on the next ask."""
    session = _SeqSession([_BadCertificate(), _Response(chunks=[b"ok"])])  # type: ignore[list-item]
    dest = tmp_path / "a.mp4"
    with pytest.raises(FetchFailed):
        await fetcher.fetch_to_file(
            _as_session(session),
            url="https://cdn.example/a.mp4",
            dest=dest,
            policy=_policy(retries=3),
        )
    assert len(session.calls) == 1
    assert not dest.exists()


async def test_a_healthy_transfer_past_the_grace_is_not_abandoned(tmp_path: Path) -> None:
    # Past the grace (31s) but moving at ~64 KB/s (above the floor), so the check passes and the
    # transfer runs to the end rather than being abandoned.
    values = iter([0.0, 31.0, 32.0])
    session = _fake(chunks=[b"a" * 2_000_000, b"b" * 16])
    dest = tmp_path / "big-but-live.mp4"

    _final, written = await fetcher.fetch_to_file(
        _as_session(session), url="https://cdn/x", dest=dest, clock=lambda: next(values, 33.0)
    )
    assert written == 2_000_016


async def test_an_http_error_becomes_a_fetch_failure(tmp_path: Path) -> None:
    session = _fake(chunks=[], status_exc=aiohttp.ClientError("404 not found"))
    dest = tmp_path / "gone.mp4"

    with pytest.raises(FetchFailed, match="Try again in a few minutes"):
        await fetcher.fetch_to_file(_as_session(session), url="https://cdn/x", dest=dest)
    assert not dest.exists()


def _answered(status: int) -> aiohttp.ClientResponseError:
    """What `raise_for_status` raises for a server that answered with this code."""
    from multidict import CIMultiDict, CIMultiDictProxy
    from yarl import URL

    where = URL("https://gofile.io/x")
    asked = aiohttp.RequestInfo(where, "GET", CIMultiDictProxy(CIMultiDict()), where)
    return aiohttp.ClientResponseError(asked, (), status=status)


async def test_a_file_the_host_says_is_gone_is_final_and_says_the_code(tmp_path: Path) -> None:
    """The server answered, with a code, and the code is the reason: a 404 on
    a file is not "often temporary", and three attempts at it learn nothing."""
    session = _fake(chunks=[], status_exc=_answered(404))
    with pytest.raises(NothingFound) as caught:
        await fetcher.fetch_to_file(
            _as_session(session), url="https://gofile.io/x", dest=tmp_path / "a.mp4"
        )
    assert caught.value.code == "http-404"
    assert str(caught.value).startswith("GoFile answered 404 Not Found: ")


async def test_a_host_that_is_struggling_is_retried_and_says_the_code(tmp_path: Path) -> None:
    session = _fake(chunks=[], status_exc=_answered(503))
    with pytest.raises(FetchFailed) as caught:
        await fetcher.fetch_to_file(
            _as_session(session), url="https://gofile.io/x", dest=tmp_path / "a.mp4"
        )
    assert not isinstance(caught.value, NothingFound)
    assert caught.value.code == "http-503"
    assert "try again later" in str(caught.value)


async def test_a_dropped_connection_mid_stream_is_a_fetch_failure(tmp_path: Path) -> None:
    session = _fake(chunks=[b"partial"], stream_exc=aiohttp.ClientPayloadError("reset"))
    dest = tmp_path / "trunc.mp4"

    with pytest.raises(FetchFailed):
        await fetcher.fetch_to_file(_as_session(session), url="https://cdn/x", dest=dest)
    assert not dest.exists()  # nothing half-written survives


async def test_the_request_carries_referer_accept_and_cookie_only_when_given(
    tmp_path: Path,
) -> None:
    session = _fake(chunks=[b"x"])
    await fetcher.fetch_to_file(
        _as_session(session),
        url="https://cdn/x",
        dest=tmp_path / "a.mp4",
        referer="https://site/",
        accept="video/mp4",
        cookie="accountToken=abc",
    )
    _url, headers = session.calls[-1]
    assert headers == {
        "Referer": "https://site/",
        "Accept": "video/mp4",
        "Cookie": "accountToken=abc",
    }

    bare = _fake(chunks=[b"x"])
    await fetcher.fetch_to_file(_as_session(bare), url="https://cdn/x", dest=tmp_path / "b.mp4")
    assert bare.calls[-1][1] == {}  # no referer, no negotiation headers


async def test_a_redirect_is_followed_by_hand_to_its_target(tmp_path: Path) -> None:
    redirect = _Response(
        chunks=[], status=302, headers={"Location": "https://cdn.example/final.mp4"}
    )
    final = _Response(chunks=[b"body"])
    session = _SeqSession([redirect, final])
    dest = tmp_path / "clip.mp4"

    out, written = await fetcher.fetch_to_file(
        _as_session(session), url="https://cdn.example/start", dest=dest
    )

    assert written == 4
    assert out.read_bytes() == b"body"
    assert session.calls[0][0] == "https://cdn.example/start"
    assert session.calls[1][0] == "https://cdn.example/final.mp4"  # the redirect target


async def test_a_relative_redirect_location_is_resolved_against_the_current_url(
    tmp_path: Path,
) -> None:
    redirect = _Response(chunks=[], status=301, headers={"Location": "/moved.mp4"})
    session = _SeqSession([redirect, _Response(chunks=[b"x"])])

    await fetcher.fetch_to_file(
        _as_session(session), url="https://cdn.example/a/b", dest=tmp_path / "c.mp4"
    )

    assert session.calls[1][0] == "https://cdn.example/moved.mp4"


async def test_a_redirect_without_a_location_is_a_fetch_failure(tmp_path: Path) -> None:
    session = _SeqSession([_Response(chunks=[], status=302)])  # 3xx, but no Location header
    with pytest.raises(FetchFailed):
        await fetcher.fetch_to_file(
            _as_session(session), url="https://cdn/x", dest=tmp_path / "a.mp4"
        )


async def test_a_redirect_chain_past_the_cap_is_a_fetch_failure(tmp_path: Path) -> None:
    always = _Response(chunks=[], status=302, headers={"Location": "https://cdn/next"})
    session = _SeqSession([always])  # one response, reused: an endless redirect loop
    with pytest.raises(FetchFailed):
        await fetcher.fetch_to_file(
            _as_session(session), url="https://cdn/x", dest=tmp_path / "a.mp4"
        )
    assert len(session.calls) == MAX_REDIRECT_HOPS + 1


async def test_a_blocked_initial_address_is_refused_before_any_socket_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _fake(chunks=[b"x"])

    def _reject(_url: str, **_k: object) -> None:
        raise UrlRejected("private", reason="private_address")

    monkeypatch.setattr(fetcher, "check_url", _reject)

    with pytest.raises(UrlRejected):
        await fetcher.fetch_to_file(
            _as_session(session), url="http://169.254.169.254/", dest=tmp_path / "a.mp4"
        )
    assert session.calls == []  # never even asked the session to connect


async def test_a_redirect_target_is_revalidated_and_can_be_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    redirect = _Response(chunks=[], status=302, headers={"Location": "http://169.254.169.254/meta"})
    session = _SeqSession([redirect, _Response(chunks=[b"never"])])
    seen: list[str] = []

    def _guard(url: str, **_k: object) -> None:
        seen.append(url)
        if "169.254" in url:
            raise UrlRejected("private", reason="private_address")

    monkeypatch.setattr(fetcher, "check_url", _guard)

    with pytest.raises(UrlRejected):
        await fetcher.fetch_to_file(
            _as_session(session), url="https://cdn/start", dest=tmp_path / "a.mp4"
        )
    assert seen == ["https://cdn/start", "http://169.254.169.254/meta"]  # both hops vetted


# --- the Downloads settings on Sift's own fetch -------------------------------------------------
#
# Most Sites are fetched by Sift itself rather than by the two tools. Every test below plants a
# value and watches it bite on the transfer; the gate at the end holds every policy value to having
# one of these and a tool option, so a setting cannot reach one side only without a test failing.


class _Time:
    """A clock and a sleep that move together, so a wait is counted rather than waited out."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept = 0.0

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.slept += seconds


def _policy(
    *,
    retries: int = 0,
    timeout: float = 30.0,
    backoff: float = 60.0,
    rate: int | None = None,
    at_least: int | None = None,
    at_most: int | None = None,
) -> RunPolicy:
    return RunPolicy(
        pacing=Pacing(
            seconds_between_requests=0.0,
            retries=retries,
            timeout_seconds=timeout,
            wait_after_too_many_requests=backoff,
            bytes_per_second=rate,
        ),
        filters=Filters(at_least_bytes=at_least, at_most_bytes=at_most),
    )


class _CountedContent(_Content):
    """Content that counts how many chunks were actually read off it."""

    def __init__(self, chunks: list[bytes], raise_exc: Exception | None = None) -> None:
        super().__init__(chunks, raise_exc)
        self.read = 0

    async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.read += 1
            yield chunk
        if self._raise_exc is not None:
            raise self._raise_exc


def _counted(chunks: list[bytes], **kw: object) -> tuple[_Response, _CountedContent]:
    response = _Response(chunks=[], **kw)  # type: ignore[arg-type]
    content = _CountedContent(chunks, cast("Exception | None", kw.get("stream_exc")))
    response.content = content
    return response, content


class _TimedSession(_SeqSession):
    """Also keeps the time budget each request was given."""

    def __init__(self, responses: list[_Response]) -> None:
        super().__init__(responses)
        self.timeouts: list[object] = []

    def get(
        self, url: str, *, headers: dict[str, str], timeout: object, allow_redirects: bool = True
    ) -> _Response:
        self.timeouts.append(timeout)
        return super().get(url, headers=headers, timeout=timeout, allow_redirects=allow_redirects)


def _answered_with(
    status: int, headers: dict[str, str] | None = None
) -> aiohttp.ClientResponseError:
    from multidict import CIMultiDict, CIMultiDictProxy
    from yarl import URL

    where = URL("https://cdn.example/x")
    asked = aiohttp.RequestInfo(where, "GET", CIMultiDictProxy(CIMultiDict()), where)
    return aiohttp.ClientResponseError(
        asked, (), status=status, headers=CIMultiDictProxy(CIMultiDict(headers or {}))
    )


_KIB = 1024
_MIB = 1024 * 1024


async def test_a_speed_limit_holds_a_two_megabyte_file_to_about_ten_seconds(
    tmp_path: Path,
) -> None:
    """The limit, counted rather than waited: 2 MB at 200 KB/s.

    A second's worth goes at once (the bucket starts full) and the rest is earned at the limit, so
    (2048 - 200) / 200 = 9.24 s. A limit that reached only the tools would make this 0 s.
    """
    time_ = _Time()
    session = _SeqSession([_Response(chunks=[b"x" * (64 * _KIB)] * 32)])

    _final, written = await fetcher.fetch_to_file(
        _as_session(session),
        url="https://cdn.example/big.mp4",
        dest=tmp_path / "big.mp4",
        policy=_policy(rate=200 * _KIB),
        clock=time_.clock,
        sleep=time_.sleep,
    )

    assert written == 2 * _MIB
    assert 9.0 <= time_.now <= 10.5


async def test_with_no_speed_limit_the_read_loop_never_waits(tmp_path: Path) -> None:
    time_ = _Time()
    session = _SeqSession([_Response(chunks=[b"x" * (64 * _KIB)] * 32)])

    await fetcher.fetch_to_file(
        _as_session(session),
        url="https://cdn.example/big.mp4",
        dest=tmp_path / "big.mp4",
        policy=_policy(rate=None),
        clock=time_.clock,
        sleep=time_.sleep,
    )

    assert time_.slept == 0.0


async def test_a_speed_limit_below_the_crawl_floor_is_not_abandoned_as_a_crawl(
    tmp_path: Path,
) -> None:
    """20 KB/s is a choice, and the crawl floor (~50 KB/s after 30 s) would kill it as a dead CDN.

    Under a limit the floor is half the limit, so a transfer somebody slowed down runs to the end.
    """
    time_ = _Time()
    session = _SeqSession([_Response(chunks=[b"x" * (16 * _KIB)] * 64)])  # 1 MiB, ~50 s at 20 KB/s

    _final, written = await fetcher.fetch_to_file(
        _as_session(session),
        url="https://cdn.example/slow.mp4",
        dest=tmp_path / "slow.mp4",
        policy=_policy(rate=20 * _KIB),
        clock=time_.clock,
        sleep=time_.sleep,
    )

    assert written == _MIB
    assert time_.now > 30.0  # past the grace, so the floor was really consulted


async def test_a_declared_size_above_the_ceiling_is_refused_before_a_byte_is_written(
    tmp_path: Path,
) -> None:
    response, content = _counted([b"x"], headers={"Content-Length": str(50 * _MIB)})
    dest = tmp_path / "huge.mp4"

    with pytest.raises(fetcher.Skipped) as caught:
        await fetcher.fetch_to_file(
            _as_session(_SeqSession([response])),
            url="https://cdn.example/huge.mp4",
            dest=dest,
            policy=_policy(at_most=10 * _MIB),
        )

    assert str(caught.value) == (
        "Sift skipped this 50 MB file because Skip files larger than is set to 10 MB."
    )
    # Final, like the tools' own skip: the same file is the same size on the next attempt.
    assert isinstance(caught.value, NothingFound)
    assert content.read == 0
    assert not dest.exists()


async def test_a_stream_that_states_no_size_is_cut_off_once_it_passes_the_ceiling(
    tmp_path: Path,
) -> None:
    response, content = _counted([b"x" * (_MIB // 2 + 1)] * 10)  # no Content-Length at all
    dest = tmp_path / "unsized.mp4"

    with pytest.raises(fetcher.Skipped) as caught:
        await fetcher.fetch_to_file(
            _as_session(_SeqSession([response])),
            url="https://cdn.example/unsized.mp4",
            dest=dest,
            policy=_policy(at_most=_MIB),
        )

    assert str(caught.value) == (
        "Sift stopped this file at 1 MB because Skip files larger than is set to 1 MB."
    )
    assert content.read == 2  # two halves and a byte is past 1 MB: nothing after is read
    assert not dest.exists()


async def test_a_declared_size_below_the_floor_is_refused(tmp_path: Path) -> None:
    response, content = _counted([b"x" * 10], headers={"Content-Length": "10"})
    dest = tmp_path / "thumb.jpg"

    with pytest.raises(fetcher.Skipped) as caught:
        await fetcher.fetch_to_file(
            _as_session(_SeqSession([response])),
            url="https://cdn.example/thumb.jpg",
            dest=dest,
            policy=_policy(at_least=_MIB),
        )

    assert str(caught.value) == (
        "Sift skipped this 1 KB file because Skip files smaller than is set to 1 MB."
    )
    assert content.read == 0
    assert not dest.exists()


async def test_a_file_that_states_no_size_and_arrives_below_the_floor_is_not_kept(
    tmp_path: Path,
) -> None:
    dest = tmp_path / "thumb.jpg"
    with pytest.raises(fetcher.Skipped):
        await fetcher.fetch_to_file(
            _as_session(_SeqSession([_Response(chunks=[b"x" * 10])])),
            url="https://cdn.example/thumb.jpg",
            dest=dest,
            policy=_policy(at_least=_MIB),
        )
    assert not dest.exists()


async def test_a_file_inside_both_bounds_is_kept(tmp_path: Path) -> None:
    """The known negative: bounds that allow the file change nothing about it."""
    session = _SeqSession([_Response(chunks=[b"x" * 500], headers={"Content-Length": "500"})])
    _final, written = await fetcher.fetch_to_file(
        _as_session(session),
        url="https://cdn.example/fine.mp4",
        dest=tmp_path / "fine.mp4",
        policy=_policy(at_least=100, at_most=1000),
    )
    assert written == 500


async def test_the_connection_timeout_setting_is_the_budget_to_connect_and_between_reads(
    tmp_path: Path,
) -> None:
    session = _TimedSession([_Response(chunks=[b"x"])])

    await fetcher.fetch_to_file(
        _as_session(session),
        url="https://cdn.example/a.mp4",
        dest=tmp_path / "a.mp4",
        policy=_policy(timeout=7.0),
    )

    (budget,) = session.timeouts
    assert isinstance(budget, aiohttp.ClientTimeout)
    assert (budget.total, budget.sock_connect, budget.sock_read) == (None, 7.0, 7.0)


async def test_a_server_error_is_asked_again_up_to_the_retry_count(tmp_path: Path) -> None:
    def trouble() -> _Response:
        return _Response(chunks=[], status_exc=_answered_with(503))

    recovered = _SeqSession([trouble(), trouble(), _Response(chunks=[b"ok"])])
    _final, written = await fetcher.fetch_to_file(
        _as_session(recovered),
        url="https://cdn.example/a.mp4",
        dest=tmp_path / "a.mp4",
        policy=_policy(retries=2),
    )
    assert written == 2
    assert len(recovered.calls) == 3

    one_short = _SeqSession([trouble(), trouble(), _Response(chunks=[b"ok"])])
    with pytest.raises(FetchFailed) as caught:
        await fetcher.fetch_to_file(
            _as_session(one_short),
            url="https://cdn.example/b.mp4",
            dest=tmp_path / "b.mp4",
            policy=_policy(retries=1),
        )
    assert caught.value.code == "http-503"
    assert len(one_short.calls) == 2


async def test_zero_retries_asks_once(tmp_path: Path) -> None:
    session = _SeqSession([_Response(chunks=[], status_exc=_answered_with(503))])
    with pytest.raises(FetchFailed):
        await fetcher.fetch_to_file(
            _as_session(session),
            url="https://cdn.example/a.mp4",
            dest=tmp_path / "a.mp4",
            policy=_policy(retries=0),
        )
    assert len(session.calls) == 1


async def test_a_final_answer_is_not_asked_again_whatever_the_retry_count(tmp_path: Path) -> None:
    session = _SeqSession([_Response(chunks=[], status_exc=_answered_with(404))])
    with pytest.raises(NothingFound):
        await fetcher.fetch_to_file(
            _as_session(session),
            url="https://cdn.example/a.mp4",
            dest=tmp_path / "a.mp4",
            policy=_policy(retries=5),
        )
    assert len(session.calls) == 1


async def test_a_connection_that_breaks_mid_file_resumes_from_the_bytes_on_disk(
    tmp_path: Path,
) -> None:
    broken = _Response(chunks=[b"aaa"], stream_exc=aiohttp.ClientPayloadError("reset"))
    rest = _Response(chunks=[b"bbb"], status=206)
    session = _SeqSession([broken, rest])
    dest = tmp_path / "a.mp4"

    _final, on_disk = await fetcher.fetch_to_file(
        _as_session(session),
        url="https://cdn.example/a.mp4",
        dest=dest,
        policy=_policy(retries=1),
    )

    assert dest.read_bytes() == b"aaabbb"
    assert on_disk == 6
    assert "Range" not in session.calls[0][1]
    assert session.calls[1][1]["Range"] == "bytes=3-"


async def test_a_rate_limit_holds_the_host_for_the_setting_before_asking_again(
    tmp_path: Path,
) -> None:
    time_ = _Time()
    limiter = HostRateLimiter(clock=time_.clock, sleep=time_.sleep)
    session = _SeqSession(
        [_Response(chunks=[], status_exc=_answered_with(429)), _Response(chunks=[b"ok"])]
    )

    await fetcher.fetch_to_file(
        _as_session(session),
        url="https://cdn.example/a.mp4",
        dest=tmp_path / "a.mp4",
        policy=_policy(retries=1, backoff=45.0),
        limiter=limiter,
    )

    assert time_.slept == 45.0
    assert len(session.calls) == 2


async def test_a_longer_retry_after_from_the_site_wins_over_the_setting(tmp_path: Path) -> None:
    time_ = _Time()
    limiter = HostRateLimiter(clock=time_.clock, sleep=time_.sleep)
    limited = _answered_with(429, {"Retry-After": "120"})
    session = _SeqSession([_Response(chunks=[], status_exc=limited), _Response(chunks=[b"ok"])])

    await fetcher.fetch_to_file(
        _as_session(session),
        url="https://cdn.example/a.mp4",
        dest=tmp_path / "a.mp4",
        policy=_policy(retries=1, backoff=45.0),
        limiter=limiter,
    )

    assert time_.slept == 120.0


async def test_a_rate_limit_with_no_retries_left_still_holds_the_host_for_the_next_download(
    tmp_path: Path,
) -> None:
    time_ = _Time()
    limiter = HostRateLimiter(clock=time_.clock, sleep=time_.sleep)
    session = _SeqSession([_Response(chunks=[], status_exc=_answered_with(429))])

    with pytest.raises(FetchFailed) as caught:
        await fetcher.fetch_to_file(
            _as_session(session),
            url="https://cdn.example/a.mp4",
            dest=tmp_path / "a.mp4",
            policy=_policy(retries=0, backoff=45.0),
            limiter=limiter,
        )
    assert caught.value.code == "http-429"

    await limiter.acquire("cdn.example")  # what the next download from that host does first
    assert time_.slept == 45.0


async def test_a_downloads_session_is_paced_and_timed_by_its_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wait between requests is the SESSION's, because it spans every request of a download."""
    from sift.slices.download.sources import net, ratelimit

    made: list[float] = []
    original = ratelimit.Pacer.for_policy

    def capture(policy: RunPolicy) -> ratelimit.Pacer:
        made.append(policy.pacing.seconds_between_requests)
        return original(policy)

    monkeypatch.setattr(ratelimit.Pacer, "for_policy", staticmethod(capture))
    planted = RunPolicy(pacing=replace(_policy(timeout=9.0).pacing, seconds_between_requests=1.25))

    async with net.guarded_session(policy=planted) as session:
        assert (session.timeout.sock_connect, session.timeout.sock_read) == (9.0, 9.0)
    assert made == [1.25]

    made.clear()
    async with net.guarded_session() as session:  # not a download: unpaced
        assert session.timeout.sock_read is None
    assert made == []


#: Each policy value, and the test above that proves it bites on Sift's own fetch.
_A_4K_LADDER = (
    "<video>"
    '<source src=\\"//cdn.example/v/360.mp4\\" title=\\"360p\\">'
    '<source src=\\"//cdn.example/v/720.mp4\\" title=\\"720p HD\\">'
    '<source src=\\"//cdn.example/v/1080.mp4\\" title=\\"1080p60\\">'
    '<source src=\\"//cdn.example/v/2160.mp4\\" title=\\"2160p60\\">'
    "</video>"
)


def test_video_quality_picks_the_rung_sifts_own_readers_take() -> None:
    """ "Best compatible" is the tallest rung at or under 1080p; "Best available" the tallest.

    Proved on a player's ladder Sift reads itself, and on the rule every such reader shares: the
    setting reaches the reader through the download's policy (`sites.resolve_site`).
    """
    from sift.slices.download.sources.sites.common import pick_rung
    from sift.slices.download.sources.sites.extractors import hqporner_best_source
    from sift.slices.download.sources.tuning import QUALITY_BEST, QUALITY_COMPATIBLE

    player = "https://player.example/embed/1"
    compatible = hqporner_best_source(_A_4K_LADDER, player, QUALITY_COMPATIBLE)
    best = hqporner_best_source(_A_4K_LADDER, player, QUALITY_BEST)
    assert compatible == "https://cdn.example/v/1080.mp4"
    assert best == "https://cdn.example/v/2160.mp4"
    # Every rung taller than asked: the nearest one, not the tallest.
    assert pick_rung([(2160, "a"), (1440, "b")], QUALITY_COMPATIBLE) == "b"
    assert pick_rung([], QUALITY_BEST) is None


def test_the_default_wait_raises_no_middlemans_floor() -> None:
    """A middleman service's measured backoff is raised only by a wait somebody CHOSE. The default
    reads the same number as a chosen sixty, so the policy carries whether it was chosen, and at
    the default a five-second floor stays five."""
    from dataclasses import replace

    from sift.slices.download.sources.ratelimit import middleman_backoff
    from sift.slices.download.sources.tuning import PACING, POLICY

    assert middleman_backoff(5.0, None) == 5.0
    assert middleman_backoff(5.0, POLICY) == 5.0
    chosen = replace(
        POLICY, pacing=replace(PACING, wait_after_too_many_requests=120.0, wait_chosen=True)
    )
    assert middleman_backoff(5.0, chosen) == 120.0
    shorter = replace(
        POLICY, pacing=replace(PACING, wait_after_too_many_requests=1.0, wait_chosen=True)
    )
    assert middleman_backoff(5.0, shorter) == 5.0


async def test_the_policy_says_whether_the_wait_was_chosen() -> None:
    """`read_policy` asks the store whether the wait holds a chosen value; without a store nothing
    counts as chosen."""
    from sift.slices.download.sources.policy import BACKOFF_KEY, read_policy

    async def get_app(_key: str) -> object:
        return None

    async def stored(key: str) -> bool:
        return key == BACKOFF_KEY

    assert (await read_policy(get_app)).pacing.wait_chosen is False
    assert (await read_policy(get_app, stored)).pacing.wait_chosen is True


SIFTS_OWN_PROOF: dict[str, Callable[..., object]] = {
    "pacing.wait_chosen": test_the_default_wait_raises_no_middlemans_floor,
    "pacing.seconds_between_requests": test_a_downloads_session_is_paced_and_timed_by_its_policy,
    "pacing.retries": test_a_server_error_is_asked_again_up_to_the_retry_count,
    "pacing.timeout_seconds": (
        test_the_connection_timeout_setting_is_the_budget_to_connect_and_between_reads
    ),
    "pacing.wait_after_too_many_requests": (
        test_a_rate_limit_holds_the_host_for_the_setting_before_asking_again
    ),
    "pacing.bytes_per_second": test_a_speed_limit_holds_a_two_megabyte_file_to_about_ten_seconds,
    "filters.at_least_bytes": test_a_declared_size_below_the_floor_is_refused,
    "filters.at_most_bytes": (
        test_a_declared_size_above_the_ceiling_is_refused_before_a_byte_is_written
    ),
    "quality": test_video_quality_picks_the_rung_sifts_own_readers_take,
}


def _leaves(value: object, prefix: str = "") -> set[str]:
    """Every value on the policy, by its dotted path."""
    if not is_dataclass(value):
        return {prefix}
    found: set[str] = set()
    for field in fields(value):
        found |= _leaves(
            getattr(value, field.name),
            f"{prefix}{field.name}" if not prefix else f"{prefix}.{field.name}",
        )
    return found


def _gaps(
    *,
    keys: set[str],
    lands_at: dict[str, str],
    leaves: set[str],
    concerns: tuple[Concern, ...],
    around_the_tool: frozenset[str],
    proofs: dict[str, Callable[..., object]],
    exempt: dict[str, str],
    read_off: dict[str, str],
) -> list[str]:
    """Every way a download setting fails to reach both kinds of download, in words."""
    problems: list[str] = []
    problems += [
        f"{key}: declared in policy.py and not in LANDS_AT" for key in keys - lands_at.keys()
    ]
    problems += [
        f"{path}: on the policy and read from no setting"
        for path in leaves - set(lands_at.values()) - read_off.keys()
    ]
    # A value read off another setting's store: that setting must be one the table holds, and what
    # the value changes in Sift's own fetch must be proved like any setting's.
    for path, key in read_off.items():
        if key not in lands_at:
            problems.append(f"{path}: read off {key}, which is not in LANDS_AT")
        if path not in proofs:
            problems.append(f"{path}: nothing proves it reaches Sift's own fetch")
    by_path = {concern.value: concern for concern in concerns}
    for key, path in lands_at.items():
        concern = by_path.get(path)
        # Quality is a sort rather than a flag (`argv._SORT_BY_QUALITY`), pinned in test_argv.
        if concern is None and path != "quality":
            problems.append(f"{key}: no tool is told it (not in argv.CONCERNS)")
        tool_cannot = concern is not None and None in (concern.ytdlp, concern.gallerydl)
        if tool_cannot and path not in around_the_tool:
            problems.append(
                f"{key}: a tool has no option and Sift does not apply it around the run"
            )
        if path not in proofs and path not in exempt:
            problems.append(f"{key}: nothing proves it reaches Sift's own fetch")
        if path in proofs and not proofs[path].__name__.startswith("test_"):
            problems.append(f"{key}: its proof is not a test")
    return problems


def _keys_declared_in(namespace: dict[str, object]) -> set[str]:
    return {
        value
        for name, value in namespace.items()
        if name.endswith("_KEY") and isinstance(value, str) and value.startswith("download.")
    }


def _the_real_gaps(**planted: object) -> list[str]:
    arguments: dict[str, Any] = {
        "keys": _keys_declared_in(vars(policy)),
        "lands_at": policy.LANDS_AT,
        "leaves": _leaves(POLICY),
        "concerns": CONCERNS,
        "around_the_tool": AROUND_THE_TOOL,
        "proofs": SIFTS_OWN_PROOF,
        "exempt": fetcher.NOT_FOR_SIFTS_OWN_FETCH,
        "read_off": policy.READ_OFF_A_SETTING,
    }
    arguments.update(planted)
    return _gaps(**arguments)


def test_every_download_setting_reaches_the_tools_and_sifts_own_fetch() -> None:
    """THE GATE: one setting, one meaning, on every Site.

    Each setting `policy.py` reads must land on the policy, be told to the tools (or be applied by
    Sift around a tool with no option for it), and be proved to bite on Sift's own fetch, or be
    named in `fetcher.NOT_FOR_SIFTS_OWN_FETCH` with the reason.
    """
    assert _the_real_gaps() == []


def test_the_gate_refuses_a_setting_that_reaches_one_side_only() -> None:
    """The planted-key self-test: a gate that cannot fail proves nothing."""
    planted_key = {**policy.LANDS_AT, "download.planted": "pacing.planted"}
    gaps = _the_real_gaps(
        keys=_keys_declared_in(vars(policy)) | {"download.planted"},
        lands_at=planted_key,
        leaves=_leaves(POLICY) | {"pacing.planted"},
    )
    assert "download.planted: no tool is told it (not in argv.CONCERNS)" in gaps
    assert "download.planted: nothing proves it reaches Sift's own fetch" in gaps

    # A key declared and never mapped, and a proof dropped, are both named.
    unmapped = _the_real_gaps(keys=_keys_declared_in(vars(policy)) | {"download.forgotten"})
    assert unmapped == ["download.forgotten: declared in policy.py and not in LANDS_AT"]
    unproved = dict(SIFTS_OWN_PROOF)
    del unproved["pacing.bytes_per_second"]
    assert _the_real_gaps(proofs=unproved) == [
        f"{policy.BANDWIDTH_KEY}: nothing proves it reaches Sift's own fetch"
    ]
    # A tool with no option for a value, and nothing applying it around the run, is named too.
    assert _the_real_gaps(around_the_tool=frozenset()) == [
        f"{policy.BACKOFF_KEY}: a tool has no option and Sift does not apply it around the run"
    ]
    # A value read off a setting is held to that setting and to a proof, and one read off nothing
    # is a value on the policy that no setting feeds.
    assert _the_real_gaps(read_off={"pacing.wait_chosen": "download.gone"}) == [
        "pacing.wait_chosen: read off download.gone, which is not in LANDS_AT"
    ]
    unproved_read = dict(SIFTS_OWN_PROOF)
    del unproved_read["pacing.wait_chosen"]
    assert _the_real_gaps(proofs=unproved_read) == [
        "pacing.wait_chosen: nothing proves it reaches Sift's own fetch"
    ]
    assert _the_real_gaps(read_off={}) == [
        "pacing.wait_chosen: on the policy and read from no setting"
    ]
