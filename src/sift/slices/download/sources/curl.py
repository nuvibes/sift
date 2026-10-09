# SPDX-License-Identifier: AGPL-3.0-or-later
"""The guarded way a resolver reaches a service API over curl_cffi, as a browser at the TLS layer.

It cannot pin a connection, so the address is vetted just before and redirects are refused."""

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

_IMPERSONATE = "chrome"
_DEFAULT_TIMEOUT = 30.0

# Control bodies are kilobytes; past this a service is trying to exhaust memory.
_MAX_BODY_BYTES = 8 * 1024 * 1024


async def _read_body(response: Any) -> str:
    """A response body as text, streamed and aborted past the ceiling."""
    body = bytearray()
    async for chunk in response.aiter_content():
        body.extend(chunk)
        if len(body) > _MAX_BODY_BYTES:
            raise DownloadError("The service returned an unexpectedly large response.")
    return bytes(body).decode("utf-8", "replace")


@dataclass(frozen=True, slots=True)
class Fetched:
    """A finished request, its body read while the session was still open."""

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
    """Vet `url`, then GET it as an impersonated browser, never following a redirect."""
    check_url(url, here=proxy is None)
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
    """Vet `url`, then POST a form to it as an impersonated browser, never following a redirect."""
    check_url(url, here=proxy is None)
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
    """How long one ask may take: the service's measured ceiling, raised by a longer timeout."""
    return ceiling if policy is None else max(ceiling, policy.pacing.timeout_seconds)


async def _asked_again(
    once: Callable[[], Awaitable[Fetched]],
    policy: RunPolicy | None,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Fetched:
    """One ask, and up to the retries more only when nothing came back; a status is the caller's."""
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
