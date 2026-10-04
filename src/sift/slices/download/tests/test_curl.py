# SPDX-License-Identifier: AGPL-3.0-or-later
"""The guarded curl_cffi front door: it vets an address, then GETs it as an impersonated browser,
by default without following redirects and without a saved login. curl_cffi is faked; the vetting is
real."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from sift.slices.download.sources import curl
from sift.slices.download.sources.curl import Fetched, guarded_get, guarded_post
from sift.slices.download.sources.errors import DownloadError
from sift.slices.download.url_guard import UrlRejected


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str], text: str) -> None:
        self.status_code = status_code
        self.headers = headers
        self._body = text.encode("utf-8")

    async def aiter_content(self) -> AsyncIterator[bytes]:
        # Hand the body back in two chunks, the way a real streamed read arrives.
        half = len(self._body) // 2
        yield self._body[:half]
        yield self._body[half:]


class _FakeSession:
    def __init__(
        self,
        response: _FakeResponse,
        calls: list[dict[str, Any]],
        session_kwargs: dict[str, Any],
    ) -> None:
        self._response = response
        self._calls = calls
        self._session_kwargs = session_kwargs

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self._calls.append({"url": url, **kwargs})
        return self._response

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self._calls.append({"url": url, "method": "POST", **kwargs})
        return self._response


def _fake_session_factory(
    response: _FakeResponse, calls: list[dict[str, Any]], session_kwargs: list[dict[str, Any]]
) -> Any:
    def factory(**kwargs: Any) -> _FakeSession:
        session_kwargs.append(kwargs)
        return _FakeSession(response, calls, kwargs)

    return factory


async def test_it_vets_then_requests_as_an_impersonated_browser_without_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        curl, "check_url", lambda _url: None
    )  # the guard is tested on its own below
    calls: list[dict[str, Any]] = []
    session_kwargs: list[dict[str, Any]] = []
    response = _FakeResponse(200, {"Content-Type": "application/json"}, '{"ok":1}')
    monkeypatch.setattr(
        curl, "AsyncSession", _fake_session_factory(response, calls, session_kwargs)
    )

    fetched = await guarded_get(
        "https://api.example/x", params={"a": "b"}, headers={"Accept": "application/json"}
    )

    assert isinstance(fetched, Fetched)
    assert (fetched.status_code, fetched.text) == (200, '{"ok":1}')
    assert fetched.headers == {"Content-Type": "application/json"}
    call = calls[0]
    assert call["url"] == "https://api.example/x"
    assert call["params"] == {"a": "b"}
    assert call["impersonate"] == "chrome"
    assert call["allow_redirects"] is False
    assert call["headers"]["Accept"] == "application/json"
    assert "User-Agent" in call["headers"]  # a browser agent is always sent
    assert session_kwargs[0]["cookies"] is None  # no saved login by default


async def test_it_can_omit_the_user_agent_for_apis_that_reject_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(curl, "check_url", lambda _url: None)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        curl, "AsyncSession", _fake_session_factory(_FakeResponse(200, {}, "{}"), calls, [])
    )

    await guarded_get("https://api.example/x", user_agent=None)

    assert "User-Agent" not in calls[0]["headers"]  # only the impersonation sets one


async def test_it_can_carry_a_saved_login_and_never_follows_a_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(curl, "check_url", lambda _url: None)
    calls: list[dict[str, Any]] = []
    session_kwargs: list[dict[str, Any]] = []
    monkeypatch.setattr(
        curl,
        "AsyncSession",
        _fake_session_factory(_FakeResponse(200, {}, "{}"), calls, session_kwargs),
    )

    await guarded_get("https://www.reddit.com/r/x.json", cookies={"session": "TOKEN"})

    assert session_kwargs[0]["cookies"] == {"session": "TOKEN"}  # the login is on the session
    # A cookie is never allowed to ride a redirect off an unpinnable client: redirects are refused.
    assert calls[0]["allow_redirects"] is False


async def test_it_posts_a_form_body_after_vetting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(curl, "check_url", lambda _url: None)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        curl, "AsyncSession", _fake_session_factory(_FakeResponse(200, {}, "ok"), calls, [])
    )

    fetched = await guarded_post(
        "https://api.example/media",
        data={"url": "https://post"},
        headers={"Origin": "https://api.example"},
    )

    assert fetched.text == "ok"
    call = calls[0]
    assert call["method"] == "POST"
    assert call["data"] == {"url": "https://post"}
    assert call["impersonate"] == "chrome"
    assert call["allow_redirects"] is False  # a service API answers directly
    assert call["headers"]["Origin"] == "https://api.example"


async def test_it_refuses_a_private_address_before_building_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(**_kwargs: Any) -> _FakeSession:
        raise AssertionError("a session must not be built for a refused address")

    monkeypatch.setattr(curl, "AsyncSession", _fail)

    with pytest.raises(UrlRejected):
        await guarded_get("http://127.0.0.1/x")  # real check_url: loopback is refused


async def test_post_refuses_a_private_address_before_building_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(**_kwargs: Any) -> _FakeSession:
        raise AssertionError("a session must not be built for a refused address")

    monkeypatch.setattr(curl, "AsyncSession", _fail)

    with pytest.raises(UrlRejected):
        await guarded_post("http://169.254.169.254/latest", data={"x": "y"})  # link-local refused


async def test_an_oversized_response_body_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A misbehaving or hostile service that answers with a huge body cannot exhaust memory: the read
    is streamed and aborted once it passes the ceiling, rather than buffered whole."""
    monkeypatch.setattr(curl, "check_url", lambda _url: None)
    monkeypatch.setattr(curl, "_MAX_BODY_BYTES", 4)  # a tiny ceiling for the test
    monkeypatch.setattr(
        curl,
        "AsyncSession",
        _fake_session_factory(_FakeResponse(200, {}, "a body well past four bytes"), [], []),
    )

    with pytest.raises(DownloadError, match="unexpectedly large"):
        await guarded_get("https://api.example/x")


class _NoAnswerThenOk:
    """A session factory whose first asks get no connection at all, then an answer."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.asks = 0
        self.timeouts: list[float] = []

    def __call__(self, **_kwargs: Any) -> Any:
        outer = self

        class _Session:
            async def __aenter__(self) -> _Session:
                return self

            async def __aexit__(self, *_exc: object) -> None:
                return None

            async def get(self, _url: str, **kwargs: Any) -> _FakeResponse:
                outer.asks += 1
                outer.timeouts.append(kwargs["timeout"])
                if outer.asks <= outer.failures:
                    from curl_cffi.requests.exceptions import ConnectionError as NoConnection

                    raise NoConnection("no route")
                return _FakeResponse(200, {}, "{}")

        return _Session()


def _policy(*, retries: int, timeout: float) -> Any:
    from dataclasses import replace

    from sift.slices.download.sources.tuning import PACING, POLICY

    return replace(
        POLICY,
        pacing=replace(
            PACING, retries=retries, timeout_seconds=timeout, seconds_between_requests=0
        ),
    )


async def test_a_downloads_lookup_is_asked_again_when_nothing_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given the download's settings, a lookup that got no answer is asked again up to the retries,
    and a longer connection timeout raises the service's own ceiling (a shorter one does not)."""
    monkeypatch.setattr(curl, "check_url", lambda _url: None)
    sessions = _NoAnswerThenOk(failures=2)
    monkeypatch.setattr(curl, "AsyncSession", sessions)

    fetched = await guarded_get("https://api.example/x", policy=_policy(retries=2, timeout=90))

    assert fetched.status_code == 200
    assert sessions.asks == 3
    assert sessions.timeouts == [90, 90, 90]
    assert curl.budget(30.0, _policy(retries=0, timeout=5)) == 30.0


async def test_a_lookup_with_no_retries_left_or_no_policy_is_asked_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from curl_cffi.requests.exceptions import ConnectionError as NoConnection

    monkeypatch.setattr(curl, "check_url", lambda _url: None)
    for policy in (None, _policy(retries=1, timeout=30)):
        sessions = _NoAnswerThenOk(failures=5)
        monkeypatch.setattr(curl, "AsyncSession", sessions)
        with pytest.raises(NoConnection):
            await guarded_get("https://api.example/x", policy=policy)
        assert sessions.asks == (1 if policy is None else 2)
