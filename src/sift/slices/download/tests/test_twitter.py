# SPDX-License-Identifier: AGPL-3.0-or-later
"""The X/Twitter resolver: pure URL work, no network. It reads the author's username from a status
link and produces one subprocess item that tries gallery-dl then falls back to yt-dlp."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sift.slices.download.sources.twitter import (
    extract_username,
    handles,
    posted_from_post_id,
    resolve_x,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/quillmoss/status/1",
        "https://twitter.com/quillmoss/status/1",
        "https://mobile.twitter.com/quillmoss/status/1",
    ],
)
def test_handles_x_and_twitter_hosts(url: str) -> None:
    assert handles(url)


@pytest.mark.parametrize(
    "url", ["https://notx.com/quillmoss/status/1", "https://youtube.com/watch?v=1"]
)
def test_does_not_handle_other_hosts(url: str) -> None:
    assert not handles(url)


def test_it_reads_the_author_from_a_status_link_preserving_case() -> None:
    assert extract_username("https://x.com/Bob_99/status/123") == "Bob_99"


def test_it_reads_the_author_from_a_photo_permalink() -> None:
    assert extract_username("https://x.com/quillmoss/status/123/photo/1") == "quillmoss"


def test_the_anonymised_status_forms_carry_no_username() -> None:
    assert extract_username("https://x.com/i/status/123") is None  # `i` is a reserved route
    assert (
        extract_username("https://x.com/i/web/status/123") is None
    )  # second segment is not `status`


def test_a_non_status_link_has_no_username() -> None:
    assert extract_username("https://x.com/home") is None


def test_an_impossible_username_is_not_read_as_one() -> None:
    assert extract_username("https://x.com/bad-name/status/1") is None  # a hyphen is not allowed
    assert extract_username("https://x.com/waytoolongusername/status/1") is None  # over 15 chars


def test_resolve_x_makes_a_gallerydl_item_with_a_ytdlp_fallback_and_the_username() -> None:
    media = resolve_x("https://x.com/quillmoss/status/1")
    assert media.site == "X"
    assert media.source_host == "x.com"
    assert media.username == "quillmoss"
    (item,) = media.items
    assert item.backend == "gallerydl"
    assert item.fallback_backend == "ytdlp"


@pytest.mark.parametrize(
    ("url", "number"),
    [
        ("https://x.com/someone/status/1700000000000000123", "1700000000000000123"),
        ("https://x.com/i/status/1700000000000000123/photo/1", "1700000000000000123"),
        ("https://x.com/someone", None),
        ("https://x.com/someone/status/notanumber", None),
    ],
)
def test_the_post_number_is_the_posts_id(url: str, number: str | None) -> None:
    assert resolve_x(url).post_id == number


def test_when_a_post_was_made_is_read_from_its_number() -> None:
    """The `{posted}` word for a file gallery-dl fetched, which says nothing of the post's time."""
    moment = datetime(2024, 6, 13, 12, 46, 0, 961000, tzinfo=UTC)
    assert posted_from_post_id("1801234567890123456") == moment
    assert resolve_x("https://x.com/someone/status/1801234567890123456").posted == moment
    # Before snowflakes, a number carried no moment; and a number too large for a clock is none.
    assert posted_from_post_id("20") is None
    assert posted_from_post_id("9" * 400) is None
    assert posted_from_post_id(None) is None
    assert resolve_x("https://x.com/someone").posted is None
