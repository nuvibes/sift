# SPDX-License-Identifier: AGPL-3.0-or-later
"""A video host is matched on the host, never on a substring of it."""

from __future__ import annotations

import pytest

from sift.slices.download.sources import curl as curl_mod
from sift.slices.download.sources import reddit
from sift.slices.download.sources.reddit import resolve_reddit
from sift.slices.download.tests.test_reddit import _NO_HTML, _NO_JSON, _responder


@pytest.mark.parametrize(
    ("url", "backends"),
    [
        ("https://v.redd.it/abcdef", ("ytdlp", None)),
        ("https://v.redd.it.example.net/abcdef", ("gallerydl", "ytdlp")),
        ("https://notredgifs.com/watch/abcdef", ("gallerydl", "ytdlp")),
    ],
)
async def test_only_the_video_hosts_themselves_go_to_the_video_tool(
    monkeypatch: pytest.MonkeyPatch, url: str, backends: tuple[str, str | None]
) -> None:
    monkeypatch.setattr(reddit, "load_cookie_jar", lambda _f, _d: {})
    monkeypatch.setattr(curl_mod, "guarded_get", _responder([], html=_NO_HTML, js=_NO_JSON))

    media = await resolve_reddit(url)

    assert [(one.backend, one.fallback_backend) for one in media.items] == [backends]
