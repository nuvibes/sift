# SPDX-License-Identifier: AGPL-3.0-or-later
"""The RedGIFs username resolver: a pure gif-id read plus a best-effort token-then-lookup for the
uploader. The network calls are faked."""

from __future__ import annotations

from typing import Any

import pytest

from sift.slices.download.sources import curl as curl_mod
from sift.slices.download.sources.curl import Fetched
from sift.slices.download.sources.redgifs import extract_gif_id, handles, resolve_username


def test_handles_redgifs_hosts() -> None:
    assert handles("https://www.redgifs.com/watch/abcdef")
    assert handles("https://redgifs.app/ifr/abcdef")
    assert not handles("https://notredgifs.com/watch/abcdef")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.redgifs.com/watch/SqueakyFawn", "squeakyfawn"),  # lowercased
        ("https://redgifs.com/ifr/SqueakyFawn", "squeakyfawn"),
        ("https://redgifs.com/?gif=FromQuery", "fromquery"),
        ("https://redgifs.com/SqueakyFawn", "squeakyfawn"),  # a bare id
        ("https://media.redgifs.com/SqueakyFawn-mobile.mp4", "squeakyfawn"),  # a CDN filename
        ("https://redgifs.com/users/someone", None),  # a profile, not a gif
        ("https://redgifs.com/browse", None),  # a reserved route word
        ("https://redgifs.com/ab", None),  # too short for an id
    ],
)
def test_extract_gif_id(url: str, expected: str | None) -> None:
    assert extract_gif_id(url) == expected


async def test_resolve_username_reads_a_profile_link_with_no_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(_url: str, **_kwargs: Any) -> Fetched:
        raise AssertionError("a profile link must not hit the network")

    monkeypatch.setattr(curl_mod, "guarded_get", fail)
    assert await resolve_username("https://redgifs.com/users/someone") == "someone"


def _router(responses: dict[str, Fetched]) -> Any:
    async def fake_get(url: str, **_kwargs: Any) -> Fetched:
        for key, value in responses.items():
            if key in url:
                return value
        return Fetched(status_code=404, headers={}, text="")

    return fake_get


async def test_resolve_username_reads_the_uploader_through_the_token_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        curl_mod,
        "guarded_get",
        _router(
            {
                "auth/temporary": Fetched(200, {}, '{"token": "TT"}'),
                "gifs/": Fetched(200, {}, '{"gif": {"userName": "uploader"}}'),
            }
        ),
    )
    assert await resolve_username("https://redgifs.com/watch/abcdef") == "uploader"


async def test_resolve_username_needs_a_gif_id() -> None:
    assert await resolve_username("https://redgifs.com/browse") is None


async def test_resolve_username_gives_up_at_each_dead_end(monkeypatch: pytest.MonkeyPatch) -> None:
    no_auth = _router({"auth/temporary": Fetched(500, {}, "")})
    monkeypatch.setattr(curl_mod, "guarded_get", no_auth)
    assert await resolve_username("https://redgifs.com/watch/abcdef") is None

    no_token = _router({"auth/temporary": Fetched(200, {}, "{}")})  # 200 but no token
    monkeypatch.setattr(curl_mod, "guarded_get", no_token)
    assert await resolve_username("https://redgifs.com/watch/abcdef") is None

    no_gif = _router(
        {"auth/temporary": Fetched(200, {}, '{"token": "TT"}'), "gifs/": Fetched(403, {}, "")}
    )
    monkeypatch.setattr(curl_mod, "guarded_get", no_gif)
    assert await resolve_username("https://redgifs.com/watch/abcdef") is None

    bad_shape = _router(
        {"auth/temporary": Fetched(200, {}, '{"token": "TT"}'), "gifs/": Fetched(200, {}, "{}")}
    )
    monkeypatch.setattr(curl_mod, "guarded_get", bad_shape)
    assert await resolve_username("https://redgifs.com/watch/abcdef") is None


async def test_resolve_username_swallows_a_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def raising(_url: str, **_kwargs: Any) -> Fetched:
        raise RuntimeError("network down")

    monkeypatch.setattr(curl_mod, "guarded_get", raising)
    assert await resolve_username("https://redgifs.com/watch/abcdef") is None
