# SPDX-License-Identifier: AGPL-3.0-or-later
"""The resolve stage: a pasted URL becomes the media behind it.

A host with its own resolver (TikTok) is dispatched to it; every other host still resolves to one
subprocess item, and the point of those tests is that the right tool is chosen and the site and
username are read from the URL. TikTok's own resolving is tested in test_tiktok.
"""

from __future__ import annotations

import pytest

from sift.kernel.settings_registry import get_registered
from sift.slices.download.sources import instagram, reddit, redgifs, sites, tiktok, youtube
from sift.slices.download.sources.registry import (
    CHOOSABLE_DOWNLOADERS,
    Backend,
    chosen_backend,
    is_a_downloader,
)
from sift.slices.download.sources.resolve import is_mutable, resolve
from sift.slices.download.sources.resolved import ResolvedItem, ResolvedMedia


async def test_a_tiktok_url_is_dispatched_to_the_tiktok_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct = ResolvedMedia(
        site="TikTok",
        source_url="https://www.tiktok.com/@a/video/1",
        source_host="tiktok.com",
        items=[ResolvedItem(index=0, url="https://cdn/x.mp4", media_type="video", ext=".mp4")],
    )

    async def fake_resolve_tiktok(_url: str, **_kwargs: object) -> ResolvedMedia:
        return direct

    monkeypatch.setattr(tiktok, "resolve_tiktok", fake_resolve_tiktok)

    media = await resolve("https://www.tiktok.com/@a/video/1")
    assert media is direct  # the resolver's result is returned as-is, not a subprocess item


async def test_a_file_host_url_is_dispatched_to_the_site_extractor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct = ResolvedMedia(
        site="Pixeldrain",
        source_url="https://pixeldrain.com/u/x",
        source_host="pixeldrain.com",
        items=[ResolvedItem(index=0, url="https://cdn/x", media_type="other", ext="")],
    )

    async def fake_resolve_site(_url: str, **_kwargs: object) -> ResolvedMedia:
        return direct

    monkeypatch.setattr(sites, "resolve_site", fake_resolve_site)

    media = await resolve("https://pixeldrain.com/u/x")
    assert media is direct


async def test_tiktok_uses_the_tool_when_a_site_is_pointed_at_one() -> None:
    media = await resolve("https://www.tiktok.com/@a/video/1", downloader=Backend.YTDLP)
    assert [item.backend for item in media.items] == ["ytdlp"]  # the downloader tool, not tikwm
    assert media.site == "TikTok"
    # Attribution is a fact about the ADDRESS, so choosing a tool must not cost it.
    assert media.username == "a"


def test_the_one_global_service_setting_is_gone() -> None:
    """It is one of the three answers a Site is given, and a leftover registration would be a
    control on the Downloads screen that decides nothing."""
    assert get_registered("downloads.service_method") is None


def test_every_tool_the_code_can_run_is_one_somebody_can_choose() -> None:
    """The choice list is derived from `Backend`, so this is what catches a third tool added to the
    enum and not offered: a tool the code runs and nobody can pick."""
    offered = {one.value for one in CHOOSABLE_DOWNLOADERS if one.value}
    assert offered == {backend.value for backend in Backend}
    assert CHOOSABLE_DOWNLOADERS[0].value == "", "Sift's own answer is offered first"
    assert all(is_a_downloader(value) for value in offered)


def test_a_stored_tool_name_nothing_ships_reads_as_no_opinion() -> None:
    """A database written by a newer Sift, or edited by hand. Falling back to the built-in answer is
    the behaviour that still downloads; raising here would stop every download from that Site."""
    assert chosen_backend("something-else") is None
    assert chosen_backend("") is None
    assert chosen_backend(None) is None
    assert not is_a_downloader("something-else")


async def test_a_youtube_url_is_a_ytdlp_item_with_a_looked_up_channel_username(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake(_url: str, **_kwargs: object) -> str | None:
        return "Creator"

    monkeypatch.setattr(youtube, "resolve_username", fake)
    media = await resolve("https://youtu.be/Xy7Qm2Lp9Ka")
    assert [item.backend for item in media.items] == ["ytdlp"]
    assert media.username == "Creator"


async def test_a_youtube_url_with_no_username_is_left_unlabelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake(_url: str, **_kwargs: object) -> str | None:
        return None

    monkeypatch.setattr(youtube, "resolve_username", fake)
    media = await resolve("https://youtu.be/Xy7Qm2Lp9Ka")
    assert media.username is None


async def test_a_redgifs_url_is_a_ytdlp_item_with_the_looked_up_uploader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake(_url: str, **_kwargs: object) -> str | None:
        return "uploader"

    monkeypatch.setattr(redgifs, "resolve_username", fake)
    media = await resolve("https://redgifs.com/watch/abcdef")
    assert [item.backend for item in media.items] == ["ytdlp"]
    assert media.username == "uploader"


async def test_an_instagram_url_is_dispatched_to_the_instagram_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct = ResolvedMedia(
        site="Instagram",
        source_url="https://www.instagram.com/reel/ABC/",
        source_host="instagram.com",
        items=[ResolvedItem(index=0, url="https://cdn/x", media_type="video", ext=".mp4")],
    )

    async def fake_resolve_instagram(_url: str, **_kwargs: object) -> ResolvedMedia:
        return direct

    monkeypatch.setattr(instagram, "resolve_instagram", fake_resolve_instagram)

    media = await resolve("https://www.instagram.com/reel/ABC/")
    assert media is direct


async def test_instagram_uses_the_tool_when_a_site_is_pointed_at_one() -> None:
    """Only when somebody has chosen it. The service stays the default: this path is reached by
    pointing the Site at a tool, never by the service having a bad day.

    The fallback assertion below is the one that matters. An Instagram post can be photographs,
    which yt-dlp does not read, so a chosen tool that also DROPPED the second one would come back
    empty from half of Instagram, and an empty result is indistinguishable from a post that held
    nothing. Choosing a tool decides which goes first; it does not spend the Site's coverage."""
    media = await resolve("https://www.instagram.com/reel/ABC/", downloader=Backend.YTDLP)
    assert [item.backend for item in media.items] == ["ytdlp"]  # the downloader tool, not instasave
    # And on this path only, the item names a second tool: an Instagram post can be photographs,
    # which yt-dlp does not read, so one tool alone comes back empty rather than wrong.
    assert [item.fallback_backend for item in media.items] == ["gallerydl"]
    assert media.site == "Instagram"


async def test_a_reddit_url_is_dispatched_to_the_reddit_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct = ResolvedMedia(
        site="Reddit",
        source_url="https://www.reddit.com/r/pics/comments/abc/t/",
        source_host="reddit.com",
        items=[
            ResolvedItem(index=0, url="https://i.redd.it/x.jpg", media_type="image", ext=".jpg")
        ],
    )

    async def fake_resolve_reddit(_url: str, **_kwargs: object) -> ResolvedMedia:
        return direct

    monkeypatch.setattr(reddit, "resolve_reddit", fake_resolve_reddit)

    media = await resolve("https://www.reddit.com/r/pics/comments/abc/t/")
    assert media is direct


async def test_an_x_url_resolves_to_a_gallerydl_item_with_a_ytdlp_fallback() -> None:
    # Bunkr and the other file-hosts have direct extractors, so a plain gallery-dl item comes
    # from X, where gallery-dl is tried first and yt-dlp is the fallback.
    media = await resolve("https://x.com/creator/status/1")
    assert media.site == "X"
    (item,) = media.items
    assert item.backend == "gallerydl"
    assert item.fallback_backend == "ytdlp"


async def test_an_unknown_host_resolves_through_the_ytdlp_catch_all_with_a_domain_site() -> None:
    media = await resolve("https://vimeo.com/12345")
    assert media.site == "Vimeo"  # best-effort label from the domain
    assert media.username is None
    assert [item.backend for item in media.items] == ["ytdlp"]


async def test_a_host_with_no_readable_label_falls_back_to_the_host_itself() -> None:
    media = await resolve("http://intranet/thing")  # a single-label host names no site
    assert media.site == "intranet"
    assert [item.backend for item in media.items] == ["ytdlp"]


def test_is_mutable_only_for_a_link_whose_contents_change() -> None:
    tray = "https://www.instagram.com/stories/someuser/"
    highlight = "https://www.instagram.com/stories/highlights/17900000000000000/"
    assert is_mutable(tray) is True  # a day's stories, different by the evening
    # Permanent, and added to for years under one address, which is why it belongs here. Left out
    # because it is permanent, every item added after the first paste would be lost silently.
    assert is_mutable(highlight) is True
    assert is_mutable("https://www.instagram.com/reel/ABC/") is False  # a fixed permalink
    assert is_mutable("https://www.instagram.com/p/ABC/") is False
    assert is_mutable("https://www.tiktok.com/@a/video/1") is False
    assert is_mutable("https://x.com/creator/status/1") is False
    assert is_mutable("https://example-host.test/whatever/") is False  # an unknown site


async def test_a_goonbox_url_is_dispatched_to_its_own_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It has an API that names the original files, so it is read directly rather than handed to a
    tool. What is checked here is the dispatch: the resolver's own behaviour is tested next door."""
    from sift.slices.download.sources import goonbox

    direct = ResolvedMedia(
        site="Goonbox",
        source_url="https://goonbox.cr/a/kd8LwN",
        source_host="goonbox.cr",
        items=[
            ResolvedItem(
                index=0,
                url="https://simp6.cuckcapital.cr/images4/a.jpg",
                media_type="image",
                ext=".jpg",
            )
        ],
    )

    async def fake_resolve_goonbox(_url: str, **_kwargs: object) -> ResolvedMedia:
        return direct

    monkeypatch.setattr(goonbox, "resolve_goonbox", fake_resolve_goonbox)

    assert await resolve("https://goonbox.cr/a/kd8LwN") is direct
