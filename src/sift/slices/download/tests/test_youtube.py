# SPDX-License-Identifier: AGPL-3.0-or-later
"""The YouTube username resolver: a pure video-id read plus a best-effort oEmbed lookup for the
channel's username. The network call is faked."""

from __future__ import annotations

import json
from typing import Any

import pytest

from sift.slices.download.sources import curl as curl_mod
from sift.slices.download.sources.curl import Fetched
from sift.slices.download.sources.youtube import (
    channel_from_oembed,
    extract_video_id,
    handles,
    resolve_username,
)


def test_handles_youtube_hosts() -> None:
    assert handles("https://www.youtube.com/watch?v=abc")
    assert handles("https://youtu.be/abc")
    assert not handles("https://notyoutube.com/watch?v=abc")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=Xy7Qm2Lp9Ka", "Xy7Qm2Lp9Ka"),
        ("https://youtu.be/Xy7Qm2Lp9Ka", "Xy7Qm2Lp9Ka"),
        ("https://www.youtube.com/shorts/Xy7Qm2Lp9Ka", "Xy7Qm2Lp9Ka"),
        ("https://www.youtube.com/embed/Xy7Qm2Lp9Ka", "Xy7Qm2Lp9Ka"),
        ("https://www.youtube.com/@channel", None),  # a channel URL has no video id
        ("https://youtu.be/tooshort", None),  # not 11 chars
        ("https://www.youtube.com/shorts/tooshort", None),
        ("https://www.youtube.com/feed/subscriptions", None),  # no id anywhere
    ],
)
def test_extract_video_id(url: str, expected: str | None) -> None:
    assert extract_video_id(url) == expected


def test_channel_from_oembed_prefers_the_handle_then_the_name() -> None:
    assert channel_from_oembed({"author_url": "https://www.youtube.com/@Creator"}) == "Creator"
    assert channel_from_oembed(
        {"author_url": "https://x/channel/UC1", "author_name": " Legacy "}
    ) == ("Legacy")
    assert channel_from_oembed({"author_name": ""}) is None  # nothing usable
    assert channel_from_oembed("not-a-dict") is None


def _fetched(text: str, *, status: int = 200) -> Fetched:
    return Fetched(status_code=status, headers={}, text=text)


async def test_resolve_username_reads_the_channel_username(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(_url: str, **_kwargs: Any) -> Fetched:
        return _fetched(json.dumps({"author_url": "https://www.youtube.com/@Creator"}))

    monkeypatch.setattr(curl_mod, "guarded_get", fake_get)
    assert await resolve_username("https://youtu.be/Xy7Qm2Lp9Ka") == "Creator"


async def test_resolve_username_needs_a_video_id() -> None:
    assert await resolve_username("https://www.youtube.com/@channel") is None


async def test_resolve_username_swallows_a_bad_response(monkeypatch: pytest.MonkeyPatch) -> None:
    async def not_ok(_url: str, **_kwargs: Any) -> Fetched:
        return _fetched("", status=500)

    monkeypatch.setattr(curl_mod, "guarded_get", not_ok)
    assert await resolve_username("https://youtu.be/Xy7Qm2Lp9Ka") is None

    async def raising(_url: str, **_kwargs: Any) -> Fetched:
        raise RuntimeError("network down")

    monkeypatch.setattr(curl_mod, "guarded_get", raising)
    assert await resolve_username("https://youtu.be/Xy7Qm2Lp9Ka") is None
