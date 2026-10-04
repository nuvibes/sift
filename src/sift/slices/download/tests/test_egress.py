# SPDX-License-Identifier: AGPL-3.0-or-later
"""The four places a download's way out has to be honoured, and the Site lookup the router is given.

The route is only worth having if every one of them carries it. Two of the four are downloader
tools Sift runs, and their route is a flag on a command line; the other two are clients inside this
process. A route that reached the tools and not the clients would leak the traffic of exactly the
sites somebody put on a tunnel, because those are the sites the in-process resolvers exist for.

How the route is CHOSEN (a Site's own over the default, and the refusal when its tunnel is gone
or down) is the kernel router's, tested in `kernel/tests/test_tunnels_egress.py`. What is this
slice's is which Site a URL belongs to, and that the router is handed this slice's catalog to say.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import aiohttp
import pytest

from sift.kernel.public_net import TOOL_PROXY
from sift.kernel.tunnels import EgressRouter
from sift.slices.download.sources import curl, net
from sift.slices.download.sources.argv import build_gallerydl_argv, build_ytdlp_argv
from sift.slices.download.sources.sites.catalog import site_key_of

_PROXY = "http://127.0.0.1:45000"
_URL = "https://www.reddit.com/r/x/comments/1/t/"


# --- the four ways out ---------------------------------------------------------------------------


@pytest.mark.parametrize("builder", [build_ytdlp_argv, build_gallerydl_argv])
def test_a_downloader_tool_is_given_the_route_on_its_command_line(builder: Any) -> None:
    """The tool is handed the tool proxy, chained to the route's tunnel."""
    argv = builder(_URL, Path("/work"), proxy=_PROXY)
    assert TOOL_PROXY.chained_to(argv[argv.index("--proxy") + 1]) == _PROXY
    # And the URL is still the last argument, after the end-of-options marker.
    assert argv[-1] == _URL and argv[-2] == "--"


@pytest.mark.parametrize("builder", [build_ytdlp_argv, build_gallerydl_argv])
def test_a_downloader_tool_with_no_route_goes_through_the_tool_proxy_directly(builder: Any) -> None:
    """A site set to Direct still carries a real proxy value (the tool proxy, chained nowhere),
    never an empty one, which some tools read as a proxy of the empty string."""
    argv = builder(_URL, Path("/work"))
    handed = argv[argv.index("--proxy") + 1]
    assert handed and TOOL_PROXY.chained_to(handed) is None


async def test_the_streaming_fetcher_session_carries_the_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class _Session:
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)

        async def close(self) -> None:
            return None

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    async with net.guarded_session(proxy=_PROXY):
        pass
    assert seen["proxy"] == _PROXY


async def test_the_impersonating_client_carries_the_route(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class _Session:
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)

        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def get(self, *_args: object, **_kwargs: object) -> Any:
            return _Response()

        async def post(self, *_args: object, **_kwargs: object) -> Any:
            return _Response()

    class _Response:
        status_code = 200
        headers: ClassVar[dict[str, str]] = {}

        async def aiter_content(self) -> Any:
            for chunk in (b"{}",):
                yield chunk

    monkeypatch.setattr(curl, "AsyncSession", _Session)
    await curl.guarded_get("https://example.com/api", proxy=_PROXY)
    assert seen["proxy"] == _PROXY

    seen.clear()
    await curl.guarded_post("https://example.com/api", proxy=_PROXY)
    assert seen["proxy"] == _PROXY


# --- which Site a URL is, as the router is told it ------------------------------------------------


def test_a_url_is_its_sites_key_and_an_unknown_host_is_none() -> None:
    assert site_key_of(_URL) == "reddit"
    assert site_key_of("https://example-host.test/x") is None


async def test_the_catalog_is_what_lets_a_sites_own_route_win() -> None:
    """The router with this slice's catalog, as the application wires it: a Site with a route of its
    own takes it, and every other URL follows the default."""

    async def default() -> str:
        return "t1"

    async def site(key: str) -> str | None:
        return "t2" if key == "reddit" else None

    router = EgressRouter({}, read_default=default, read_site=site, site_of=site_key_of)
    assert await router.route_of("https://youtu.be/abc") == "t1"
    assert await router.route_of(_URL) == "t2"
