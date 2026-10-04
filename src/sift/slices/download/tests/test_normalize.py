# SPDX-License-Identifier: AGPL-3.0-or-later
"""The URL normaliser and the ledger hash. The golden table is the control.

Two spellings of one link must reduce to one string and hash the same, or a re-drop re-downloads.
Two links to different media must not, or one silently masks the other in the ledger.
"""

from __future__ import annotations

import pytest

from sift.slices.download.sources.normalize import (
    normalize_url,
    url_hash,
    without_signature,
)

# Each pair is (input, canonical form). The right-hand side is what the input must reduce to.
_SAME = [
    ("https://example.com/watch/123", "https://example.com/watch/123"),
    # Case in the scheme and host, never in the path.
    ("HTTPS://Example.COM/watch/123", "https://example.com/watch/123"),
    # A trailing slash on a real path.
    ("https://example.com/watch/123/", "https://example.com/watch/123"),
    # A default port is not part of identity.
    ("https://example.com:443/watch/123", "https://example.com/watch/123"),
    ("http://example.com:80/watch/123", "http://example.com/watch/123"),
    # The fragment is a place in a page, not a resource.
    ("https://example.com/watch/123#t=42", "https://example.com/watch/123"),
    # Tracking parameters go; a meaningful one stays.
    ("https://example.com/watch?v=123&utm_source=feed", "https://example.com/watch?v=123"),
    ("https://example.com/p/abc?igshid=xyz&s=20", "https://example.com/p/abc"),
    # What remains is ordered, so two orderings are one string.
    ("https://example.com/w?b=2&a=1", "https://example.com/w?a=1&b=2"),
]


@pytest.mark.parametrize(("raw", "canonical"), _SAME, ids=[case[0] for case in _SAME])
def test_normalises_to_the_canonical_form(raw: str, canonical: str) -> None:
    assert normalize_url(raw) == canonical


@pytest.mark.parametrize(("raw", "canonical"), _SAME, ids=[case[0] for case in _SAME])
def test_two_spellings_of_one_link_hash_the_same(raw: str, canonical: str) -> None:
    assert url_hash(raw) == url_hash(canonical)


def test_a_non_default_port_is_kept() -> None:
    assert normalize_url("https://example.com:8443/x") == "https://example.com:8443/x"


def test_the_root_path_keeps_its_slash() -> None:
    assert normalize_url("https://example.com/") == "https://example.com"


def test_a_query_of_only_tracking_loses_its_query() -> None:
    assert normalize_url("https://example.com/x?utm_campaign=a") == "https://example.com/x"


def test_different_media_do_not_collide() -> None:
    assert url_hash("https://example.com/watch/1") != url_hash("https://example.com/watch/2")


def test_the_hash_is_hex_and_stable() -> None:
    digest = url_hash("https://example.com/watch/123")
    assert len(digest) == 32
    assert all(character in "0123456789abcdef" for character in digest)
    assert digest == url_hash("https://EXAMPLE.com/watch/123/")


# --- signed links -------------------------------------------------------------------------------
#
# Some hosts sign one file's address afresh on every page load, so expiry and signature are dropped.

_SIGNED = (
    "https://cdn.discordapp.com/attachments/1400000000000000001/1400000000000000002/clip.mp4"
    "?ex=6a800000&is=6a7e8000&hm=3fd0c8a1e75b0c3a9e17d24f6081b2c3d4e5f60718293a4b5c6d7e8f9012ab34&"
)
_REISSUED = (
    "https://cdn.discordapp.com/attachments/1400000000000000001/1400000000000000002/clip.mp4"
    "?ex=7b0011ff&is=7b00ff11&hm=00000000005b0c3a9e17d24f6081b2c3d4e5f60718293a4b5c6d7e8f9012ab34&"
)


def test_a_reissued_signature_is_the_same_download() -> None:
    assert url_hash(_SIGNED) == url_hash(_REISSUED)


def test_a_signed_link_normalises_to_the_file_it_names() -> None:
    assert normalize_url(_SIGNED) == (
        "https://cdn.discordapp.com/attachments/1400000000000000001/1400000000000000002/clip.mp4"
    )


def test_two_attachments_on_one_host_stay_apart() -> None:
    other = _SIGNED.replace("/clip.mp4", "/other.mp4")
    assert url_hash(_SIGNED) != url_hash(other)


def test_the_same_names_elsewhere_are_kept() -> None:
    # `ex` and `is` are ordinary words. Dropping them everywhere could change which media a link
    # resolves to, which is the failure the denylist is written conservatively to avoid.
    assert normalize_url("https://example.com/x?ex=1&is=2") == "https://example.com/x?ex=1&is=2"


def test_the_address_shown_loses_the_signature_and_nothing_else() -> None:
    assert without_signature(_SIGNED) == (
        "https://cdn.discordapp.com/attachments/1400000000000000001/1400000000000000002/clip.mp4"
    )


def test_an_ordinary_address_is_shown_exactly_as_it_is() -> None:
    # Returned unchanged rather than rebuilt, so nothing about an untouched link can be reordered
    # or re-encoded on the way to a screen.
    assert without_signature("https://example.com/x?a=1&b=2") == "https://example.com/x?a=1&b=2"
