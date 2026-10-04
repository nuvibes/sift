# SPDX-License-Identifier: AGPL-3.0-or-later
"""The drag-and-paste decision as a hand-written table, one row per thing a browser hands over:
an image dragged from a tab, a file drop, a pasted link, a `blob:` URL."""

from __future__ import annotations

import pytest

from sift.slices.capture.pipeline import Route, route_capture, usable_source_url

#: (name, url, has_bytes, expected route). `expected is None` means there was nothing to do.
CASES = [
    # A usable link always wins, even when bytes came with it: the bytes a drag carries are a
    # thumbnail, the link fetches the original.
    ("url_and_bytes_prefers_url", "https://example.com/a/photo", True, Route.DOWNLOAD),
    ("url_only", "https://example.com/a/photo", False, Route.DOWNLOAD),
    ("http_is_usable", "http://example.com/a/photo", True, Route.DOWNLOAD),
    # Bytes are the fallback: no link, or a link that is really the page's own in-memory bytes.
    ("bytes_only", None, True, Route.IMPORT),
    ("blob_url_falls_back_to_bytes", "blob:https://example.com/abc", True, Route.IMPORT),
    ("data_url_falls_back_to_bytes", "data:image/png;base64,AAAA", True, Route.IMPORT),
    ("local_file_url_falls_back_to_bytes", "file:///home/someone/pic.png", True, Route.IMPORT),
    ("empty_url_falls_back_to_bytes", "", True, Route.IMPORT),
    # Nothing to do: an unusable URL with no bytes behind it, or nothing at all.
    ("blob_url_no_bytes", "blob:https://example.com/abc", False, None),
    ("nothing_at_all", None, False, None),
]


@pytest.mark.unit
@pytest.mark.parametrize(("name", "url", "has_bytes", "expected"), CASES, ids=[c[0] for c in CASES])
def test_route_capture(name: str, url: str | None, has_bytes: bool, expected: Route | None) -> None:
    assert route_capture(url=url, has_bytes=has_bytes) is expected


#: (name, candidate, usable). Which strings Sift can go and fetch, and which are local bytes wearing
#: a link.
URL_CASES = [
    ("https", "https://example.com/x", True),
    ("http", "http://example.com/x", True),
    ("https_with_port_and_path", "https://example.com:8443/a/b?c=d", True),
    ("blob", "blob:https://example.com/abc", False),
    ("data", "data:image/png;base64,AAAA", False),
    ("file", "file:///home/someone/pic.png", False),
    ("javascript", "javascript:alert(1)", False),
    ("about", "about:blank", False),
    ("ftp_is_not_fetchable", "ftp://example.com/x", False),
    ("scheme_without_host", "https://", False),
    ("empty", "", False),
    ("whitespace", "   ", False),
    ("bare_word", "notaurl", False),
]


@pytest.mark.unit
@pytest.mark.parametrize(("name", "candidate", "usable"), URL_CASES, ids=[c[0] for c in URL_CASES])
def test_usable_source_url(name: str, candidate: str, usable: bool) -> None:
    assert usable_source_url(candidate) is usable
