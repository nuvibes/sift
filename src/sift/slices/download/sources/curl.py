# SPDX-License-Identifier: AGPL-3.0-or-later
"""The guarded way a resolver reaches a service API over curl_cffi.

Some of the services a resolver depends on turn away an ordinary programmatic client: a plain
request gets a 401 where one that looks like a real browser at the TLS layer gets JSON. curl_cffi is
that browser-looking client, and this is its guarded front door, the counterpart to
`net.guarded_session` for the impersonating one.

The two clients guard the network the same way but by different means. aiohttp pins the connection to
a vetted address, so a name that turns private after the check cannot be reached. curl_cffi's request
API exposes no such pinning, so instead the address is vetted immediately before the request and
redirects are refused rather than followed: a service API answers directly, and a redirect to a
fresh, unvetted address is a thing to stop at, not chase. That leaves the same small residual the
subprocess tools already carry (a rebind in the instant between the check and the connect), covered
the same way: egress on a network with nothing private reachable. The address rule itself is the one
shared rule, in `url_guard`.

`proxy` is how a site routed through a tunnel is fetched here. It matters that it reaches this
client and not only the two downloader tools: the resolvers that read a service API are exactly the
ones the tunnelled sites need, so a proxy the tools honoured and this did not would leave that
traffic going out of the machine's own address, silently, on the sites the setting was chosen for.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import ConnectionError as NoConnection
from curl_cffi.requests.exceptions import Timeout

from sift.slices.download.sources.errors import DownloadError
from sift.slices.download.sources.net import DEFAULT_USER_AGENT
from sift.slices.download.sources.tuning import RunPolicy
from sift.slices.download.url_guard import check_url

#: The browser curl_cffi presents at the TLS layer. A current Chrome is what the services expect.
_IMPERSONATE = "chrome"
_DEFAULT_TIMEOUT = 30.0

# The most of a service response this will read into memory. These endpoints answer with a control
# body (JSON, or a small HTML blob) that is a few kilobytes in practice; the media itself is
# streamed to disk by the fetcher, never read here. A body past this ceiling is a misbehaving or
# hostile service trying to exhaust memory, so the read is streamed and aborted once it is exceeded
# rather than buffered whole.
_MAX_BODY_BYTES = 8 * 1024 * 1024


async def _read_body(response: Any) -> str:
    """Read a response body into text, aborting past the ceiling. Streamed rather than taken whole,
    so an oversized body is stopped before it is all in memory. Decoded as UTF-8 (these APIs answer
    in it); undecodable bytes are replaced rather than raised on."""
    body = bytearray()
    async for chunk in response.aiter_content():
        body.extend(chunk)
        if len(body) > _MAX_BODY_BYTES:
            raise DownloadError("The service returned an unexpectedly large response.")
    return bytes(body).decode("utf-8", "replace")


@dataclass(frozen=True, slots=True)
class Fetched:
    """A finished request: its status, its headers, and its body as text. The body is read while the
    session is still open, so the caller can parse it (usually as JSON) after this returns."""

    status_code: int
    headers: dict[str, str]
    text: str


async def guarded_get(
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    user_agent: str | None = DEFAULT_USER_AGENT,
    cookies: dict[str, str] | None = None,
    proxy: str | None = None,
    time_limit: float = _DEFAULT_TIMEOUT,
    policy: RunPolicy | None = None,
) -> Fetched:
    """Vet `url`'s address, then GET it as an impersonated browser.

    `user_agent` is sent unless None: some APIs (YouTube's oEmbed, RedGIFs) reject a supplied agent
    and want only the one `impersonate` sets, so those callers pass None. `cookies` is a saved login
    sent with the request (Reddit's `.json` needs one). Redirects are never followed: the client
    cannot pin, so a redirect to a fresh, unvetted address (carrying whatever cookie was attached)
    is refused rather than chased. A caller whose input needs canonicalising (Reddit's apex/old host)
    does it before the request, so the address that is vetted is the one that is fetched.

    Raises `UrlRejected` (from `check_url`) before any request if the address is not one the server may
    be pointed at. The response body is read before the session closes.
    """
    check_url(url)
    request_headers = ({"User-Agent": user_agent} if user_agent else {}) | (headers or {})

    async def once() -> Fetched:
        async with AsyncSession(cookies=cookies, proxy=proxy) as session:
            resp = await session.get(
                url,
                params=params,
                headers=request_headers,
                impersonate=_IMPERSONATE,
                timeout=budget(time_limit, policy),
                allow_redirects=False,
                stream=True,
            )
            text = await _read_body(resp)
            return Fetched(status_code=resp.status_code, headers=dict(resp.headers), text=text)

    return await _asked_again(once, policy)


async def guarded_post(
    url: str,
    *,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    user_agent: str | None = DEFAULT_USER_AGENT,
    proxy: str | None = None,
    time_limit: float = _DEFAULT_TIMEOUT,
    policy: RunPolicy | None = None,
) -> Fetched:
    """Vet `url`'s address, then POST a form body to it as an impersonated browser.

    The counterpart to `guarded_get` for a service whose resolve endpoint is a form POST rather than
    a GET (instasave takes `{url}` as posted form data). Same guard: the address is vetted immediately
    before the request and redirects are never followed: a service API answers directly. `Origin`
    and `Referer` some of these APIs check are supplied by the caller through `headers`.

    Raises `UrlRejected` (from `check_url`) before any request if the address is not one the server may
    be pointed at. The response body is read before the session closes.
    """
    check_url(url)
    request_headers = ({"User-Agent": user_agent} if user_agent else {}) | (headers or {})

    async def once() -> Fetched:
        async with AsyncSession(proxy=proxy) as session:
            resp = await session.post(
                url,
                data=data,
                headers=request_headers,
                impersonate=_IMPERSONATE,
                timeout=budget(time_limit, policy),
                allow_redirects=False,
                stream=True,
            )
            text = await _read_body(resp)
            return Fetched(status_code=resp.status_code, headers=dict(resp.headers), text=text)

    return await _asked_again(once, policy)


def budget(ceiling: float, policy: RunPolicy | None) -> float:
    """How long one ask may take: the service's own ceiling, raised by a longer connection timeout.

    The ceiling is what the service was measured to need (instasave takes longer than tikwm), so a
    shorter setting never cuts it; a longer one is somebody on a slow connection, and is honoured.
    The same rule the Site readers keep (`sites.extractors._within`).
    """
    return ceiling if policy is None else max(ceiling, policy.pacing.timeout_seconds)


async def _asked_again(
    once: Callable[[], Awaitable[Fetched]],
    policy: RunPolicy | None,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Fetched:
    """One ask, and up to the download's retries more when NOTHING came back.

    Only an ask that got no answer at all (no connection, or none in time) is asked again, after
    the wait between requests. An answer, whatever its status, is the caller's to read: the middleman
    services already read a 429 or a 5xx and wait out their own backoff, and asking again here as
    well would multiply the two. Without a policy (a lookup that is not part of a download) there
    is one ask.
    """
    asks_left = policy.pacing.retries if policy is not None else 0
    while True:
        try:
            return await once()
        except (NoConnection, Timeout):
            if policy is None or asks_left <= 0:
                raise
            asks_left -= 1
            await sleep(policy.pacing.seconds_between_requests)


__all__ = ["Fetched", "budget", "guarded_get", "guarded_post"]
