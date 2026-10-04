# SPDX-License-Identifier: AGPL-3.0-or-later
"""The site registry and resolve_site: host to extractor, and the extractor's finds turned into
direct media over the guarded session (which is faked here)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest
from aiohttp.client import DEFAULT_TIMEOUT

from sift.slices.download.sources import ratelimit, sites
from sift.slices.download.sources.errors import DownloadError, NothingFound
from sift.slices.download.sources.tuning import POLICY, RunPolicy


class _NoAnswer:
    """A response that is not ok, for a session that is asked something incidental (Pixeldrain's
    name lookup), and must not fail the resolve for lack of it."""

    ok = False

    async def __aenter__(self) -> _NoAnswer:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _AnswersNothing:
    """A session that opens but answers every request with a not-ok response."""

    timeout = DEFAULT_TIMEOUT

    def get(self, *_args: object, **_kwargs: object) -> _NoAnswer:
        return _NoAnswer()


@asynccontextmanager
async def _fake_session(session: Any = None) -> AsyncIterator[Any]:
    yield session if session is not None else _AnswersNothing()


def test_the_registry_matches_hosts_and_names_sites() -> None:
    assert sites.is_site("https://pixeldrain.com/u/x")
    assert sites.is_site("https://cdn.pixeldrain.com/u/x")  # a subdomain
    assert sites.is_site("https://fapello.su/model/1")
    assert not sites.is_site("https://example.com/x")
    assert sites.site_site("https://pixeldrain.com/u/x") == "Pixeldrain"
    assert sites.site_site("https://example.com/x") is None


async def test_resolve_site_returns_direct_media(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sites, "guarded_session", lambda **_kwargs: _fake_session())

    media = await sites.resolve_site("https://pixeldrain.com/u/abc")

    assert media.site == "Pixeldrain"
    (item,) = media.items
    assert item.backend == "direct"
    assert item.url == "https://pixeldrain.com/api/file/abc"
    assert item.referer == "https://pixeldrain.com/"  # the CDN referer


async def test_resolve_site_with_no_media_is_nothing_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sites, "guarded_session", lambda **_kwargs: _fake_session())
    with pytest.raises(NothingFound):
        await sites.resolve_site("https://pixeldrain.com/x")  # an unrecognized path finds nothing


async def test_resolve_site_wraps_a_connection_failure_as_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Failing:
        timeout = DEFAULT_TIMEOUT  # what a session opened without a download's settings carries

        def get(self, *_a: object, **_k: object) -> None:
            raise aiohttp.ClientError("could not connect")

    monkeypatch.setattr(sites, "guarded_session", lambda **_kwargs: _fake_session(_Failing()))
    with pytest.raises(DownloadError):
        await sites.resolve_site("https://pixeldrain.com/l/xyz")  # a list needs a request


class _Refuses429:
    """A session that tells its listeners every request was answered 429, as aiohttp would."""

    timeout = DEFAULT_TIMEOUT

    def __init__(self, observe: tuple[aiohttp.TraceConfig, ...]) -> None:
        self._observe = observe

    def get(self, *_args: object, **_kwargs: object) -> Any:
        session = self

        class _Answer(_NoAnswer):
            async def __aenter__(self) -> _NoAnswer:
                params: Any = SimpleNamespace(response=SimpleNamespace(status=429))
                for trace in session._observe:
                    for hook in trace.on_request_end:
                        await hook(None, None, params)  # type: ignore[arg-type]
                return self

        return _Answer()


async def test_a_site_that_answered_429_is_held_for_the_next_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read told "too many requests" holds the Site, by the download's own settings, so the next
    download waits it out before asking again, whatever this one went on to find."""
    held: list[tuple[str, RunPolicy]] = []

    def note(url: str, policy: RunPolicy, **_kwargs: object) -> float:
        held.append((url, policy))
        return 0.0

    def session(**kwargs: Any) -> Any:
        return _fake_session(_Refuses429(kwargs["observe"]))

    monkeypatch.setattr(sites, "guarded_session", session)
    monkeypatch.setattr(ratelimit, "note_too_many_requests", note)

    with pytest.raises(DownloadError):
        await sites.resolve_site("https://pixeldrain.com/l/xyz", policy=POLICY)

    assert held == [("https://pixeldrain.com/l/xyz", POLICY)]


# --- An empty result says what the site answered on the way to it --------------------------------


def _answered(*statuses: int) -> sites.Answers:
    """An `Answers` that watched these responses, fed through the same listener the session calls."""
    import asyncio
    from types import SimpleNamespace

    answers = sites.Answers()
    for status in statuses:
        params: Any = SimpleNamespace(response=SimpleNamespace(status=status))
        asyncio.run(answers._ended(None, None, params))  # type: ignore[arg-type]
    return answers


def test_turbovid_521_is_the_server_being_down_and_is_retried() -> None:
    """TurboVid's signing API answering 521 is a server that is down, not "Nothing could be
    downloaded from TurboVid." A server that is down comes back, so it is retried."""
    failure = _answered(521).why_nothing("https://turbo.cr/d/abc", "TurboVid")
    assert type(failure) is DownloadError
    assert failure.code == "http-521"
    assert str(failure) == (
        "TurboVid answered 521: the Site behind the network is down; try again later."
    )


def test_gofile_401_says_the_code_and_what_it_means() -> None:
    """GoFile's contents API answers 401 to a guest."""
    failure = _answered(200, 401).why_nothing("https://gofile.io/d/abc", "GoFile")
    assert failure.code == "http-401"
    assert str(failure).startswith("GoFile answered 401 Unauthorized: ")


def test_a_final_answer_from_a_site_reader_is_not_retried() -> None:
    failure = _answered(410).why_nothing("https://turbo.cr/d/abc", "TurboVid")
    assert isinstance(failure, NothingFound)
    assert failure.code == "http-410"


def test_an_address_the_reader_never_asked_about_says_so() -> None:
    """A PMVHaven playlist and a Coomer profile each make no request (the reader reads videos and
    posts), so "Nothing could be downloaded" would blame a site that was never asked."""
    failure = _answered().why_nothing("https://pmvhaven.com/playlists/abc", "PMVHaven")
    assert isinstance(failure, NothingFound)
    assert failure.code == "address-not-read"
    assert str(failure).startswith("PMVHaven: Sift reads single posts and videos there")


def test_every_page_answering_and_holding_nothing_keeps_the_plain_sentence() -> None:
    failure = _answered(200).why_nothing("https://gofile.io/d/abc", "GoFile")
    assert isinstance(failure, NothingFound)
    assert failure.code is None
    assert str(failure).startswith("Nothing could be downloaded from GoFile")


async def test_resolve_site_hands_the_session_a_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    """The listener has to reach the session, or every reading above is of an empty record."""
    given: dict[str, Any] = {}

    def _session(**kwargs: Any) -> Any:
        given.update(kwargs)
        return _fake_session()

    monkeypatch.setattr(sites, "guarded_session", _session)
    with pytest.raises(NothingFound):
        await sites.resolve_site("https://pixeldrain.com/x")
    (trace,) = given["observe"]
    assert isinstance(trace, aiohttp.TraceConfig)


async def test_the_guarded_session_carries_a_listener_it_is_given() -> None:
    """`observe` has to reach the session; the guard's own listener stays first."""
    from sift.slices.download.sources.net import guarded_session

    watcher = aiohttp.TraceConfig()
    async with guarded_session(observe=(watcher,)) as session:
        configs = session.trace_configs
    assert configs[-1] is watcher
    assert len(configs) == 2
