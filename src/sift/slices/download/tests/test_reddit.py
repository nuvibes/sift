# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Reddit resolver: pure media-collection from a post's JSON, and the branching resolve that picks
embedded RedGIFs, then direct gallery/image items, then a subprocess fallback. The network is faked;
the cookie-health killswitch is the real module, reset between tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from sift.slices.download.sources import cookie_health, reddit
from sift.slices.download.sources import curl as curl_mod
from sift.slices.download.sources.curl import Fetched
from sift.slices.download.sources.reddit import (
    RedditMedia,
    _gallery_media,
    _json_url,
    _media_item,
    _subreddit_from_url,
    collect_reddit_images,
    handles,
    parse_reddit_json,
    resolve_reddit,
)


@pytest.fixture(autouse=True)
def _clean_switches() -> None:
    cookie_health.reset()


def test_handles_reddit_hosts() -> None:
    assert handles("https://www.reddit.com/r/pics/comments/abc/t/")
    assert handles("https://old.reddit.com/r/pics/")
    assert handles("https://v.redd.it/abcdef")
    assert not handles("https://notreddit.com/r/pics/")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.reddit.com/r/pics/comments/abc/a_title/", "pics"),
        ("https://old.reddit.com/r/AskReddit/", "AskReddit"),
        ("https://www.reddit.com/r/pics", "pics"),  # no trailing slash
        ("https://www.reddit.com/user/someone", None),  # not a subreddit path
        ("https://v.redd.it/abcdef", None),
    ],
)
def test_subreddit_from_url(url: str, expected: str | None) -> None:
    assert _subreddit_from_url(url) == expected


@pytest.mark.parametrize(
    ("url", "already_json"),
    [
        ("https://www.reddit.com/r/pics/comments/abc/t/", False),
        ("https://www.reddit.com/r/pics/comments/abc/t/?utm=1", False),  # a query is dropped
        ("https://www.reddit.com/r/pics/comments/abc/t.json", True),
    ],
)
def test_json_url(url: str, already_json: bool) -> None:
    result = _json_url(url)
    assert result.endswith("raw_json=1")
    assert result.count(".json") == 1  # never doubled up


def test_gallery_media_prefers_mp4_then_gif_then_still_then_preview() -> None:
    assert _gallery_media({"s": {"mp4": "m.mp4", "gif": "g.gif", "u": "u.jpg"}}) == RedditMedia(
        "m.mp4", "video"
    )
    assert _gallery_media({"s": {"gif": "g.gif", "u": "u.jpg"}}) == RedditMedia("g.gif", "gif")
    assert _gallery_media({"s": {"u": "u.jpg"}}) == RedditMedia("u.jpg", "image")
    assert _gallery_media({"s": {}, "p": [{"u": "small.jpg"}, {"u": "big.jpg"}]}) == RedditMedia(
        "big.jpg", "image"
    )


def test_gallery_media_gives_up_on_nothing_usable() -> None:
    assert _gallery_media(None) is None  # not a dict
    assert _gallery_media({"s": {}}) is None  # no keys, no previews
    assert _gallery_media({"s": {}, "p": [{}]}) is None  # a preview with no url


def test_collect_reddit_images_reads_a_gallery_and_unescapes_urls() -> None:
    out: list[RedditMedia] = []
    collect_reddit_images(
        {
            "gallery_data": {"items": [{"media_id": "A"}, {"media_id": "Gone"}, {"media_id": "B"}]},
            "media_metadata": {
                "A": {"s": {"u": "https://i.redd.it/a.jpg?x=1&amp;y=2"}},
                "Gone": {"s": {}},  # a removed item with no usable media: skipped, not a hole
                "B": {"s": {"mp4": "https://i.redd.it/b.mp4"}},
            },
        },
        out,
    )
    assert out == [
        RedditMedia("https://i.redd.it/a.jpg?x=1&y=2", "image"),  # &amp; decoded
        RedditMedia("https://i.redd.it/b.mp4", "video"),
    ]


def test_collect_reddit_images_reads_a_direct_image() -> None:
    out: list[RedditMedia] = []
    collect_reddit_images({"url_overridden_by_dest": "https://i.redd.it/x.gif"}, out)
    assert out == [RedditMedia("https://i.redd.it/x.gif", "gif")]


def test_collect_reddit_images_falls_back_to_the_largest_preview() -> None:
    out: list[RedditMedia] = []
    collect_reddit_images(
        {
            "url": "https://www.reddit.com/r/pics/comments/abc/t/",  # a permalink, not media
            "preview": {"images": [{"source": {"url": "https://preview.redd.it/p.jpg?a=1"}}]},
        },
        out,
    )
    assert out == [RedditMedia("https://preview.redd.it/p.jpg?a=1", "image")]


def test_collect_reddit_images_finds_nothing_worth_keeping() -> None:
    out: list[RedditMedia] = []
    collect_reddit_images(None, out)  # not a dict
    collect_reddit_images({"url": "https://youtu.be/abc"}, out)  # a video link, not direct media
    collect_reddit_images({"preview": {"images": []}}, out)  # no previews
    collect_reddit_images({"preview": {"images": [{"source": {}}]}}, out)  # a preview with no url
    assert out == []


def test_collect_reddit_images_leaves_a_video_post_for_the_subprocess() -> None:
    # A hosted-video post carries a preview thumbnail, but delivering that still would hand back a
    # screenshot instead of the clip: it must yield nothing so the resolver routes it to yt-dlp.
    out: list[RedditMedia] = []
    collect_reddit_images(
        {
            "is_video": True,
            "url": "https://v.redd.it/abcdef",
            "preview": {"images": [{"source": {"url": "https://preview.redd.it/frame.jpg"}}]},
        },
        out,
    )
    collect_reddit_images(
        {  # an external video embed, flagged by post_hint rather than is_video
            "post_hint": "rich:video",
            "url": "https://youtu.be/abc",
            "preview": {"images": [{"source": {"url": "https://preview.redd.it/frame2.jpg"}}]},
        },
        out,
    )
    assert out == []


def test_parse_reddit_json_reads_crossposts_then_the_post_deduped() -> None:
    post = {
        "url_overridden_by_dest": "https://i.redd.it/shared.jpg",
        "crosspost_parent_list": [
            {"url_overridden_by_dest": "https://i.redd.it/original.jpg"},
            {"url_overridden_by_dest": "https://i.redd.it/shared.jpg"},  # a duplicate, dropped
        ],
    }
    listing = [{"data": {"children": [{"data": post}]}}, {"data": {"children": []}}]
    assert parse_reddit_json(json.dumps(listing)) == [
        RedditMedia("https://i.redd.it/original.jpg", "image"),
        RedditMedia("https://i.redd.it/shared.jpg", "image"),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "not json at all",
        "[]",  # empty listing, no children
        '[{"data": {"children": [{"data": "not a dict"}]}}]',
    ],
)
def test_parse_reddit_json_yields_nothing_on_a_bad_shape(text: str) -> None:
    assert parse_reddit_json(text) == []


def test_media_item_types_by_kind_not_extension() -> None:
    video = _media_item(0, RedditMedia("https://i.redd.it/x.gif", "video"))  # path lies, kind wins
    assert (video.media_type, video.ext, video.accept) == ("video", ".mp4", "video/*,*/*;q=0.8")
    gif = _media_item(1, RedditMedia("https://i.redd.it/y.gif", "gif"))
    assert (gif.media_type, gif.ext, gif.accept) == ("gif", ".gif", "image/gif,image/*,*/*;q=0.8")
    image = _media_item(2, RedditMedia("https://i.redd.it/z.png?a=1", "image"))
    assert (image.media_type, image.ext, image.accept) == ("image", ".png", None)
    bare = _media_item(3, RedditMedia("https://i.redd.it/noext", "image"))
    assert bare.ext == ".jpg"  # a URL with no extension defaults
    assert image.referer == "https://www.reddit.com/"  # every item carries the Reddit referer


def _responder(
    calls: list[dict[str, Any]],
    *,
    html: Fetched,
    js: Fetched,
) -> Any:
    """A fake guarded_get: the `.json` read is told apart from the HTML scrape by the URL."""

    async def fake_get(url: str, **kwargs: Any) -> Fetched:
        calls.append({"url": url, **kwargs})
        return js if ".json" in url else html

    return fake_get


_NO_HTML = Fetched(200, {}, "<html>nothing embedded</html>")
_NO_JSON = Fetched(200, {}, "[]")


async def test_resolve_prefers_embedded_redgifs(monkeypatch: pytest.MonkeyPatch) -> None:
    html = Fetched(
        200,
        {},
        "a https://www.redgifs.com/watch/AbcDef b https://redgifs.com/ifr/AbcDef "
        "c https://www.redgifs.com/watch/OtherId",
    )
    monkeypatch.setattr(reddit, "load_cookie_jar", lambda _f, _d: {})
    monkeypatch.setattr(curl_mod, "guarded_get", _responder([], html=html, js=_NO_JSON))

    media = await resolve_reddit("https://www.reddit.com/r/gifs/comments/abc/t/")

    assert [item.url for item in media.items] == [
        "https://www.redgifs.com/watch/AbcDef",  # deduped by id
        "https://www.redgifs.com/watch/OtherId",
    ]
    assert all(item.backend == "ytdlp" for item in media.items)
    assert media.username == "gifs"
    assert media.site == "Reddit"


async def test_a_post_embedding_clips_is_named_by_its_own_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = Fetched(200, {}, "https://www.redgifs.com/watch/AbcDef")
    post = {"title": "A post title", "id": "zzz999", "created_utc": 1.7e9}
    js = Fetched(200, {}, json.dumps([{"data": {"children": [{"data": post}]}}]))
    monkeypatch.setattr(reddit, "load_cookie_jar", lambda _f, _d: {})
    monkeypatch.setattr(curl_mod, "guarded_get", _responder([], html=html, js=js))

    media = await resolve_reddit("https://www.reddit.com/r/gifs/comments/abc/t/")

    assert [item.url for item in media.items] == ["https://www.redgifs.com/watch/AbcDef"]
    assert (media.title, media.posted) == ("A post title", datetime.fromtimestamp(1.7e9, UTC))


async def test_resolve_reads_direct_images_from_the_json(monkeypatch: pytest.MonkeyPatch) -> None:
    post = {"url_overridden_by_dest": "https://i.redd.it/x.jpg"}
    js = Fetched(200, {}, json.dumps([{"data": {"children": [{"data": post}]}}]))
    monkeypatch.setattr(reddit, "load_cookie_jar", lambda _f, _d: {"session": "T"})
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(curl_mod, "guarded_get", _responder(calls, html=_NO_HTML, js=js))

    media = await resolve_reddit("https://www.reddit.com/r/pics/comments/abc/t/")

    assert len(media.items) == 1
    assert media.items[0].url == "https://i.redd.it/x.jpg"
    assert media.items[0].backend == "direct"
    assert all(call["cookies"] == {"session": "T"} for call in calls)  # the login rides along
    # the reads are pointed at www (the canonical host), and never with a redirect to follow
    assert all(call["url"].startswith("https://www.reddit.com/") for call in calls)
    assert all("allow_redirects" not in call for call in calls)


async def test_resolve_points_a_non_www_reddit_host_at_www(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An old./apex reddit URL is rewritten to www so the read needs no redirect (the client follows
    # none). A redd.it short link is left alone: it falls through to the subprocess route.
    monkeypatch.setattr(reddit, "load_cookie_jar", lambda _f, _d: {})
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(curl_mod, "guarded_get", _responder(calls, html=_NO_HTML, js=_NO_JSON))

    await resolve_reddit("https://old.reddit.com/r/pics/comments/abc/t/")

    assert calls  # the reads happened
    assert all(call["url"].startswith("https://www.reddit.com/") for call in calls)


async def test_resolve_falls_back_to_ytdlp_for_a_video_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reddit, "load_cookie_jar", lambda _f, _d: {})
    monkeypatch.setattr(curl_mod, "guarded_get", _responder([], html=_NO_HTML, js=_NO_JSON))

    media = await resolve_reddit("https://v.redd.it/abcdef")

    assert len(media.items) == 1
    assert (media.items[0].backend, media.items[0].fallback_backend) == ("ytdlp", None)


async def test_resolve_a_video_permalink_goes_to_the_subprocess_not_its_thumbnail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A comments permalink for a hosted video: its JSON carries a preview still, but the resolver
    # must route it to the subprocess (gallery-dl then yt-dlp) rather than deliver the thumbnail.
    post = {
        "is_video": True,
        "url_overridden_by_dest": "https://v.redd.it/abcdef",
        "preview": {"images": [{"source": {"url": "https://preview.redd.it/frame.jpg"}}]},
    }
    js = Fetched(200, {}, json.dumps([{"data": {"children": [{"data": post}]}}]))
    monkeypatch.setattr(reddit, "load_cookie_jar", lambda _f, _d: {"session": "T"})
    monkeypatch.setattr(curl_mod, "guarded_get", _responder([], html=_NO_HTML, js=js))

    media = await resolve_reddit("https://www.reddit.com/r/videos/comments/abc/t/")

    (item,) = media.items
    assert item.backend == "gallerydl"  # not a direct image item
    assert item.fallback_backend == "ytdlp"


async def test_resolve_falls_back_to_gallerydl_for_a_plain_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reddit, "load_cookie_jar", lambda _f, _d: {})
    monkeypatch.setattr(curl_mod, "guarded_get", _responder([], html=_NO_HTML, js=_NO_JSON))

    media = await resolve_reddit("https://www.reddit.com/r/pics/comments/abc/t/")

    assert (media.items[0].backend, media.items[0].fallback_backend) == ("gallerydl", "ytdlp")


async def test_resolve_ignores_a_failed_html_scrape(monkeypatch: pytest.MonkeyPatch) -> None:
    # a non-200 on the HTML scrape yields no redgifs, so the resolve moves on to the JSON read
    monkeypatch.setattr(reddit, "load_cookie_jar", lambda _f, _d: {})
    monkeypatch.setattr(
        curl_mod, "guarded_get", _responder([], html=Fetched(500, {}, ""), js=_NO_JSON)
    )

    media = await resolve_reddit("https://www.reddit.com/r/pics/comments/abc/t/")

    assert media.items[0].backend == "gallerydl"  # scrape found nothing -> subprocess fallback


async def test_resolve_swallows_network_errors_and_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def raising(_url: str, **_kwargs: Any) -> Fetched:
        raise RuntimeError("network down")

    monkeypatch.setattr(reddit, "load_cookie_jar", lambda _f, _d: {})
    monkeypatch.setattr(curl_mod, "guarded_get", raising)

    media = await resolve_reddit("https://www.reddit.com/r/pics/comments/abc/t/")

    assert media.items[0].backend == "gallerydl"  # both best-effort reads failed -> subprocess


async def test_a_run_of_json_auth_failures_trips_the_killswitch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forbidden = Fetched(403, {}, "")
    loaded: list[str] = []

    def _jar(_file: Any, domain: str) -> dict[str, str]:
        loaded.append(domain)
        return {"session": "T"}

    monkeypatch.setattr(reddit, "load_cookie_jar", _jar)
    monkeypatch.setattr(curl_mod, "guarded_get", _responder([], html=_NO_HTML, js=forbidden))

    for _ in range(3):
        await resolve_reddit("https://www.reddit.com/r/pics/comments/abc/t/")

    assert cookie_health.should_use_cookies("reddit.com") is False  # tripped after three 403s
    # once tripped, the cookie jar is not even loaded again
    before = len(loaded)
    await resolve_reddit("https://www.reddit.com/r/pics/comments/abc/t/")
    assert len(loaded) == before


async def test_a_cookieless_403_does_not_trip_the_killswitch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With no saved login, the .json read goes out cookieless and Reddit's anti-bot wall 403s it.
    # That is not a dead login (there was none), so it must not trip the switch or warn.
    monkeypatch.setattr(reddit, "load_cookie_jar", lambda _f, _d: {})  # no cookies configured
    monkeypatch.setattr(
        curl_mod, "guarded_get", _responder([], html=_NO_HTML, js=Fetched(403, {}, ""))
    )

    for _ in range(5):
        await resolve_reddit("https://www.reddit.com/r/pics/comments/abc/t/")

    assert cookie_health.should_use_cookies("reddit.com") is True  # nothing to trip


async def test_a_non_auth_json_error_does_not_count_toward_the_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reddit, "load_cookie_jar", lambda _f, _d: {"session": "T"})
    monkeypatch.setattr(
        curl_mod, "guarded_get", _responder([], html=_NO_HTML, js=Fetched(500, {}, ""))
    )

    for _ in range(5):
        await resolve_reddit("https://www.reddit.com/r/pics/comments/abc/t/")

    assert cookie_health.should_use_cookies("reddit.com") is True  # a 500 is not an auth failure


async def test_a_json_success_resets_the_failure_streak(monkeypatch: pytest.MonkeyPatch) -> None:
    ok = Fetched(200, {}, json.dumps([{"data": {"children": [{"data": {}}]}}]))
    cookie_health.record_auth_failure("reddit.com")
    cookie_health.record_auth_failure("reddit.com")
    monkeypatch.setattr(reddit, "load_cookie_jar", lambda _f, _d: {"session": "T"})
    monkeypatch.setattr(curl_mod, "guarded_get", _responder([], html=_NO_HTML, js=ok))

    await resolve_reddit("https://www.reddit.com/r/pics/comments/abc/t/")  # a 200 clears the streak

    cookie_health.record_auth_failure("reddit.com")
    cookie_health.record_auth_failure("reddit.com")
    assert cookie_health.should_use_cookies("reddit.com") is True  # never three in a row


# --------------------------------------------------------------------------- naming facts


def test_the_posts_title_id_and_time_are_read_from_its_json() -> None:
    body = json.dumps(
        [
            {
                "data": {
                    "children": [
                        {"data": {"title": " A post title ", "id": "1abc23", "created_utc": 1.7e9}}
                    ]
                }
            }
        ]
    )
    post = reddit.parse_reddit_post(body)
    assert post.title == "A post title"
    assert post.post_id == "1abc23"
    assert post.posted == datetime.fromtimestamp(1.7e9, UTC)


@pytest.mark.parametrize("text", ["", "[]", "{}", "not json", '[{"data": {"children": [1]}}]'])
def test_a_body_of_the_wrong_shape_says_nothing_about_the_post(text: str) -> None:
    assert reddit.parse_reddit_post(text) == reddit.RedditPost()


async def test_resolve_names_the_post_from_the_link_and_its_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post: dict[str, object] = {
        "url_overridden_by_dest": "https://i.redd.it/x.jpg",
        "title": "A post title",
        "id": "zzz999",
        "created_utc": 1.7e9,
    }
    js = Fetched(200, {}, json.dumps([{"data": {"children": [{"data": post}]}}]))
    monkeypatch.setattr(reddit, "load_cookie_jar", lambda _f, _d: {})
    monkeypatch.setattr(curl_mod, "guarded_get", _responder([], html=_NO_HTML, js=js))

    media = await resolve_reddit("https://www.reddit.com/r/pics/comments/1abc23/a_post_title/")

    assert media.title == "A post title"
    assert media.post_id == "1abc23"  # the link's own, before the body's
    assert media.posted == datetime.fromtimestamp(1.7e9, UTC)


def test_a_posting_time_past_what_the_clock_holds_keeps_the_rest_of_the_post() -> None:
    body = json.dumps(
        [
            {
                "data": {
                    "children": [{"data": {"title": " A title ", "id": "abc", "created_utc": 1e20}}]
                }
            }
        ]
    )

    post = reddit.parse_reddit_post(body)

    assert (post.title, post.post_id, post.posted) == ("A title", "abc", None)


def test_a_listing_whose_post_is_not_an_object_says_nothing() -> None:
    body = json.dumps([{"data": {"children": [{"data": "removed"}]}}])

    assert reddit.parse_reddit_post(body) == reddit.RedditPost()
