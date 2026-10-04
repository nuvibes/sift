# SPDX-License-Identifier: AGPL-3.0-or-later
"""The value objects a resolver hands the fetcher: one downloadable item, and a whole link's worth."""

from __future__ import annotations

from sift.slices.download.sources.resolved import ResolvedItem, ResolvedMedia


def test_a_direct_item_defaults_to_streaming_and_carries_no_negotiation() -> None:
    item = ResolvedItem(index=0, url="https://cdn.example/clip.mp4", media_type="video", ext=".mp4")
    assert item.backend == "direct"
    assert item.fallback_backend is None
    assert item.referer == ""
    assert item.accept is None and item.cookie is None and item.refetch_url is None


def test_a_subprocess_item_is_a_placeholder_pointing_the_tool_at_the_source() -> None:
    item = ResolvedItem.subprocess(
        source_url="https://x.com/a/status/1", backend="gallerydl", fallback_backend="ytdlp"
    )
    assert item.url == "https://x.com/a/status/1"
    assert item.backend == "gallerydl"
    assert item.fallback_backend == "ytdlp"
    # The per-file facts are unknown until the tool runs, so they are placeholders.
    assert item.media_type == "other"
    assert item.ext == ""
    assert item.index == 0


def test_resolved_media_holds_its_items_and_optional_attribution() -> None:
    items = [ResolvedItem(index=0, url="https://cdn/x.jpg", media_type="image", ext=".jpg")]
    media = ResolvedMedia(
        site="TikTok",
        source_url="https://tiktok.com/@a/video/1",
        source_host="tiktok.com",
        items=items,
        username="a",
        title="a clip",
    )
    assert media.items == items
    assert media.username == "a"
    assert media.title == "a clip"


def test_resolved_media_defaults_to_no_items_and_no_attribution() -> None:
    media = ResolvedMedia(site="Bunkr", source_url="https://bunkr.site/a", source_host="bunkr.site")
    assert media.items == []
    assert media.username is None


def test_an_address_with_no_path_names_itself() -> None:
    """The fallback for a resolver that supplied no stable name. Rare, and it must still return
    something: a key of nothing would make every item behind such a link look like every other."""
    from sift.slices.download.sources.resolved import ResolvedItem, stable_key

    item = ResolvedItem(index=0, url="https://cdn.example", media_type="video", ext=".mp4")
    assert stable_key(item) == "https://cdn.example"
