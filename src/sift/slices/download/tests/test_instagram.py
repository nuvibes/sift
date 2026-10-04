# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Instagram resolver. The parsing is a pure function of instasave's response body and is tested
from built fixtures; the network call is faked, and the limiter is neutralised so no test waits on
real time. The username comes only from the URL: there is no off-to-the-side backfill."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from sift.slices.download.sources import curl as curl_mod
from sift.slices.download.sources import instagram
from sift.slices.download.sources.curl import Fetched
from sift.slices.download.sources.errors import DownloadError, NothingFound, UnsupportedURL
from sift.slices.download.sources.instagram import (
    INSTASAVE_REFERER,
    _canonicalize,
    _classify,
    _stories_username,
    _username_from_url,
    handles,
    is_instagram_story,
    parse_instasave_response,
    resolve_instagram,
)
from sift.slices.download.sources.ratelimit import LIMITER


def _jwt(filename: str, url: str) -> str:
    """A minimal `header.payload.signature` JWT (base64url, no padding) whose payload is
    `{filename, url}`: the shape instasave returns."""

    def seg(obj: dict[str, str]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    header = seg({"alg": "HS256", "typ": "JWT"})
    payload = seg({"filename": filename, "url": url})
    return f"{header}.{payload}.sigsigsig"


def _anchor(filename: str, url: str) -> str:
    return f'<a href="https://cdn.instasave.website/?token={_jwt(filename, url)}">Download</a>'


def _card(filename: str, url: str, *, thumb: tuple[str, str] | None = None) -> str:
    """One of the page's real cards, copied from a captured body: a preview and the download link.
    `thumb` is a preview that differs from the download, as a video's cover does."""
    preview = _jwt(*(thumb if thumb is not None else (filename, url)))
    return (
        '<div class="download-items">'
        f'<div class="download-items__thumb">'
        f'<img src="https://cdn.instasave.website/?token={preview}" alt="Thumb">'
        f'<span data-gg="1" class="format-icon"><i class="icon-sprite icon-ivideo"></i></span>'
        "</div>"
        f'<div class="download-items__btn">'
        f'<a href="https://cdn.instasave.website/?token={_jwt(filename, url)}" '
        'class="abutton is-success" title="Download"><span>Download</span></a>'
        "</div></div>"
    )


# --------------------------------------------------------------------------- usernames / URL reads


def test_handles_only_instagram_hosts() -> None:
    assert handles("https://www.instagram.com/reel/ABC/")
    assert handles("https://instagram.com/p/XYZ/")
    assert not handles("https://notinstagram.com/reel/ABC/")  # a suffix trap
    assert not handles("https://www.tiktok.com/@a/video/1")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/quillmoss/",  # a bare profile root
        "https://www.instagram.com/quillmoss",  # ... without the trailing slash
        "https://www.instagram.com/quillmoss/reels/",  # the reels tab, not one reel
        "https://www.instagram.com/quillmoss/tagged/",  # the tagged tab
    ],
)
async def test_a_whole_profile_or_tab_link_is_refused_as_unsupported(url: str) -> None:
    """A profile root or tab has no single item, so it is refused as `UnsupportedURL` with guidance
    before any network call."""
    with pytest.raises(UnsupportedURL, match="whole Instagram profile"):
        await resolve_instagram(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.instagram.com/coolaccount/p/ABC/", "coolaccount"),
        ("https://www.instagram.com/someone/reel/XYZ/", "someone"),
        ("https://www.instagram.com/stories/quill.moss/", "quill.moss"),
        ("https://www.instagram.com/reel/ABC/", None),  # a bare shortcode carries no username
        ("https://www.instagram.com/p/XYZ/", None),
    ],
)
def test_username_from_url(url: str, expected: str | None) -> None:
    assert _username_from_url(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://www.instagram.com/quillmoss/reel/DQmExampl1A/",
            "https://www.instagram.com/reel/DQmExampl1A/",  # the /<user>/ prefix is stripped
        ),
        (
            "https://www.instagram.com/reels/ABC/?utm_source=ig_web",
            "https://www.instagram.com/reel/ABC/",  # /reels/ -> /reel/, query dropped
        ),
        (
            "https://www.instagram.com/p/DYLJ/?igsh=x",
            "https://www.instagram.com/p/DYLJ/",
        ),
        (
            "https://www.instagram.com/stories/someuser/",
            "https://www.instagram.com/stories/someuser/",  # no post id -> unchanged
        ),
    ],
)
def test_canonicalize(url: str, expected: str) -> None:
    assert _canonicalize(url) == expected


def test_stories_username() -> None:
    assert _stories_username("https://www.instagram.com/stories/someuser/3812/") == "someuser"
    assert _stories_username("https://www.instagram.com/reel/ABC/") is None
    # highlights are permanent, not a story tray and not a username, so they resolve as a permalink
    assert _stories_username("https://www.instagram.com/stories/highlights/17abc/") is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.instagram.com/stories/someuser/", True),  # an ephemeral tray
        ("https://www.instagram.com/stories/someuser/3812/", True),  # still ephemeral
        ("https://www.instagram.com/stories/highlights/17abc/", False),  # highlights are permanent
        ("https://www.instagram.com/reel/ABC/", False),  # a fixed permalink
        ("https://www.instagram.com/coolaccount/p/ABC/", False),
    ],
)
def test_is_instagram_story(url: str, expected: bool) -> None:
    assert is_instagram_story(url) is expected


def test_classify_defaults_to_video_then_reads_the_filename() -> None:
    assert _classify("clip.mp4", "https://cdn/x.mp4") == ("video", ".mp4")
    assert _classify("photo.jpg", "https://cdn/abcdef?stp=x") == ("image", ".jpg")  # url has no ext
    assert _classify(None, "https://cdn/opaque") == ("video", ".mp4")  # nothing readable -> video


# --------------------------------------------------------------------------- parse (pure)


def test_parse_single_video() -> None:
    body = _anchor("reel_clip.mp4", "https://scontent.cdninstagram.com/v/reel.mp4?efg=1")
    media = parse_instasave_response(body, "https://www.instagram.com/reel/ABC123/")
    assert media.site == "Instagram"
    assert media.source_host == "instagram.com"
    (item,) = media.items
    assert item.media_type == "video"
    assert item.ext == ".mp4"
    assert item.filename == "reel_clip.mp4"
    assert item.url.startswith("https://cdn.instasave.website/?token=")  # the proxy, not the CDN
    assert item.referer == INSTASAVE_REFERER
    assert item.backend == "direct"


def test_parse_carousel_preserves_order_and_types() -> None:
    # A photograph and a clip, both offered for download, both kept.
    body = _anchor("img1.jpg", "https://scontent.cdninstagram.com/img1.jpg") + _anchor(
        "clip.mp4", "https://scontent.cdninstagram.com/clip.mp4"
    )
    media = parse_instasave_response(body, "https://www.instagram.com/p/XYZ/")
    assert [i.index for i in media.items] == [0, 1]
    assert [i.media_type for i in media.items] == ["image", "video"]


# ------------------------------------------------- the preview beside every medium on the page


def test_a_video_posted_to_the_feed_arrives_without_its_cover() -> None:
    """A video's cover is its card's preview, not a second medium."""
    body = _card(
        "AQPqvcln.mp4",
        "https://scontent.cdninstagram.com/o1/v/t16/f2/m69/AQPqvcln.mp4",
        thumb=("cover.jpg", "https://scontent.cdninstagram.com/v/t51.71878-15/cover.jpg"),
    )

    media = parse_instasave_response(body, "https://www.instagram.com/p/DQmExampl2B/")

    assert [i.media_type for i in media.items] == ["video"]
    assert media.items[0].filename == "AQPqvcln.mp4"


def test_a_carousel_of_photographs_and_clips_keeps_every_one_of_them() -> None:
    """A carousel of photographs and clips keeps every one: each real medium has a download link."""
    body = (
        _card("one.jpg", "https://scontent.cdninstagram.com/one.jpg")
        + _card(
            "clip.mp4",
            "https://scontent.cdninstagram.com/clip.mp4",
            thumb=("clipcover.jpg", "https://scontent.cdninstagram.com/clipcover.jpg"),
        )
        + _card("two.jpg", "https://scontent.cdninstagram.com/two.jpg")
    )

    media = parse_instasave_response(body, "https://www.instagram.com/p/ABC/")

    assert [i.media_type for i in media.items] == ["image", "video", "image"]
    assert [i.filename for i in media.items] == ["one.jpg", "clip.mp4", "two.jpg"]
    assert [i.index for i in media.items] == [0, 1, 2]


def test_alternating_photographs_and_clips_survive_too() -> None:
    # The exact shape a positional cover rule would destroy: as many images as videos, each
    # image directly before a video, and every one of them something somebody posted.
    body = "".join(
        _card(f"p{n}.jpg", f"https://scontent.cdninstagram.com/p{n}.jpg")
        + _card(
            f"v{n}.mp4",
            f"https://scontent.cdninstagram.com/v{n}.mp4",
            thumb=(f"c{n}.jpg", f"https://scontent.cdninstagram.com/c{n}.jpg"),
        )
        for n in (1, 2, 3)
    )

    media = parse_instasave_response(body, "https://www.instagram.com/p/ABC/")

    assert [i.media_type for i in media.items] == ["image", "video"] * 3
    assert [i.index for i in media.items] == [0, 1, 2, 3, 4, 5]


def test_a_photograph_whose_preview_is_itself_is_delivered_once() -> None:
    # The preview and the download are the same file for a photograph, so the dedup collapses them.
    body = _card("photo.jpg", "https://scontent.cdninstagram.com/photo.jpg")

    media = parse_instasave_response(body, "https://www.instagram.com/p/ABC/")

    assert [i.media_type for i in media.items] == ["image"]


def test_a_page_that_offers_no_download_links_is_read_the_old_way_rather_than_refused() -> None:
    """A page with no download anchors is read loosely rather than refused, since the markup is not
    a contract."""
    plain = (
        f'<img src="https://cdn.instasave.website/?token='
        f'{_jwt("cover.jpg", "https://scontent.cdninstagram.com/cover.jpg")}">'
        f'<img src="https://cdn.instasave.website/?token='
        f'{_jwt("clip.mp4", "https://scontent.cdninstagram.com/clip.mp4")}">'
    )

    # A reel, so the degraded path has a rule to apply, and it applies it.
    media = parse_instasave_response(plain, "https://www.instagram.com/reel/ABC/")
    assert [i.media_type for i in media.items] == ["video"]

    # And a post, where it has none: both come back.
    both = parse_instasave_response(plain, "https://www.instagram.com/p/ABC/")
    assert [i.media_type for i in both.items] == ["image", "video"]


def test_username_from_url_when_present() -> None:
    body = _anchor("clip.mp4", "https://scontent.cdninstagram.com/clip.mp4")
    with_user = parse_instasave_response(body, "https://www.instagram.com/coolaccount/p/ABC/")
    assert with_user.username == "coolaccount"
    bare = parse_instasave_response(body, "https://www.instagram.com/reel/ABC/")
    assert bare.username is None


def test_a_story_s_username_is_read_from_the_link() -> None:
    body = _anchor("story.mp4", "https://scontent.cdninstagram.com/story.mp4")
    media = parse_instasave_response(body, "https://www.instagram.com/stories/cooluser/")
    assert media.username == "cooluser"


def test_js_escaped_body_is_decoded() -> None:
    raw = _anchor("clip.mp4", "https://scontent.cdninstagram.com/clip.mp4")
    escaped = raw.replace("/", "\\/").replace(":", "\\x3a")
    media = parse_instasave_response(escaped, "https://www.instagram.com/reel/E/")
    (item,) = media.items
    assert item.url.startswith("https://cdn.instasave.website/?token=")


def test_a_reel_arrives_as_the_clip_alone() -> None:
    # The cover is the card's preview and never a medium the page offered, so nothing has to drop
    # it.
    body = _card(
        "clip.mp4",
        "https://scontent.cdninstagram.com/clip.mp4",
        thumb=("cover.jpg", "https://scontent.cdninstagram.com/cover.jpg"),
    )
    media = parse_instasave_response(body, "https://www.instagram.com/reel/ABC/")
    assert [i.media_type for i in media.items] == ["video"]
    assert media.items[0].index == 0


def test_a_reel_with_no_video_keeps_what_it_has() -> None:
    # The cover-drop only fires when a real video came through; a video-less reel response is left
    # alone rather than emptied.
    body = _anchor("cover.jpg", "https://scontent.cdninstagram.com/cover.jpg")
    media = parse_instasave_response(body, "https://www.instagram.com/reel/ABC/")
    assert [i.media_type for i in media.items] == ["image"]


def test_post_keeps_images_and_videos() -> None:
    body = _anchor("a.jpg", "https://scontent.cdninstagram.com/a.jpg") + _anchor(
        "b.mp4", "https://scontent.cdninstagram.com/b.mp4"
    )
    media = parse_instasave_response(body, "https://www.instagram.com/p/XYZ/")
    assert [i.media_type for i in media.items] == ["image", "video"]


def test_no_media_raises_nothing_found() -> None:
    with pytest.raises(NothingFound):
        parse_instasave_response("<html><body>no downloads here</body></html>", "https://x")


# --------------------------------------------------------------------------- resolve (network faked)


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def acquire(_host: str) -> None:
        return None

    monkeypatch.setattr(LIMITER, "acquire", acquire)
    monkeypatch.setattr(LIMITER, "note_retry_after", lambda *_a, **_k: None)


def _fetched(text: str, *, status: int = 200, headers: dict[str, str] | None = None) -> Fetched:
    return Fetched(status_code=status, headers=headers or {}, text=text)


def _stub_post(monkeypatch: pytest.MonkeyPatch, responses: list[Fetched]) -> list[dict[str, Any]]:
    replies = iter(responses)
    calls: list[dict[str, Any]] = []

    async def fake_post(url: str, **kwargs: Any) -> Fetched:
        calls.append({"url": url, **kwargs})
        return next(replies)

    async def fake_get(_url: str, **_kwargs: Any) -> Fetched:
        return _fetched("")  # a warm GET; body unused

    monkeypatch.setattr(curl_mod, "guarded_post", fake_post)
    monkeypatch.setattr(curl_mod, "guarded_get", fake_get)
    return calls


async def test_resolve_a_reel_posts_the_canonical_url_to_the_media_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _anchor("clip.mp4", "https://scontent.cdninstagram.com/clip.mp4")
    calls = _stub_post(monkeypatch, [_fetched(body)])

    media = await resolve_instagram("https://www.instagram.com/someone/reel/ABC/")

    assert media.items[0].url.startswith("https://cdn.instasave.website/?token=")
    assert media.username == "someone"
    assert calls[0]["url"] == instagram.INSTASAVE_MEDIA_API
    assert calls[0]["data"] == {"url": "https://www.instagram.com/reel/ABC/"}  # canonicalised
    assert calls[0]["headers"]["Origin"] == instagram.INSTASAVE_ORIGIN


async def test_resolve_a_story_posts_the_username_to_the_story_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _anchor("story.mp4", "https://scontent.cdninstagram.com/story.mp4")
    calls = _stub_post(monkeypatch, [_fetched(body)])

    media = await resolve_instagram("https://www.instagram.com/stories/cooluser/")

    assert media.username == "cooluser"
    assert calls[0]["url"] == instagram.INSTASAVE_STORY_API
    assert calls[0]["data"] == {"url": "cooluser"}  # the bare username, not a URL


async def test_resolve_a_highlights_link_goes_to_the_media_endpoint_not_the_story_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # highlights are permanent saved reels, so they resolve as a permalink through /media, not as
    # the story tray of a user literally named "highlights".
    body = _anchor("clip.mp4", "https://scontent.cdninstagram.com/clip.mp4")
    calls = _stub_post(monkeypatch, [_fetched(body)])

    media = await resolve_instagram("https://www.instagram.com/stories/highlights/17abc/")

    assert calls[0]["url"] == instagram.INSTASAVE_MEDIA_API
    assert media.username is None  # a highlights link names no uploader in the URL


async def test_resolve_retries_after_a_429(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _anchor("clip.mp4", "https://scontent.cdninstagram.com/clip.mp4")
    _stub_post(
        monkeypatch,
        [_fetched("", status=429, headers={"Retry-After": "1"}), _fetched(body)],
    )
    media = await resolve_instagram("https://www.instagram.com/reel/ABC/")
    assert media.items[0].media_type == "video"


async def test_resolve_retries_after_a_transient_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _anchor("clip.mp4", "https://scontent.cdninstagram.com/clip.mp4")
    _stub_post(monkeypatch, [_fetched("", status=502), _fetched(body)])
    media = await resolve_instagram("https://www.instagram.com/reel/ABC/")
    assert media.items[0].media_type == "video"


async def test_resolve_gives_up_after_persistent_transient_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_post(monkeypatch, [_fetched("", status=503)] * 3)
    with pytest.raises(DownloadError):
        await resolve_instagram("https://www.instagram.com/reel/ABC/")


async def test_resolve_a_deleted_post_is_nothing_found_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A clean 404 is not transient: its body holds no anchors, so it fails fast as "nothing found"
    # after a single attempt rather than hanging on retries.
    calls = _stub_post(monkeypatch, [_fetched("<html>not found</html>", status=404)])
    with pytest.raises(NothingFound):
        await resolve_instagram("https://www.instagram.com/reel/GONE/")
    assert len(calls) == 1  # no retry storm for a deletion


def test_a_media_address_with_no_path_falls_back_to_the_token() -> None:
    """The key has to name SOMETHING. An address with nothing but a host cannot identify one story
    out of a tray, so the token stands in: less stable, and better than every item sharing a key.
    """
    assert instagram._media_key("https://cdn.example", "tok") == "tok"
    assert instagram._media_key(None, "tok") == "tok"
    assert (
        instagram._media_key("https://cdn.example/v/123_n.mp4?oe=1", "tok")
        == "cdn.example/v/123_n.mp4"
    )


def test_the_middlemans_brand_does_not_ride_into_the_library() -> None:
    """The middleman's brand prefix does not reach the stored file name."""
    assert instagram._own_brand_off("instasave.website_512048337_27461093_n.jpg") == (
        "512048337_27461093_n.jpg"
    )
    assert instagram._own_brand_off("instasave.website/AQPx4Kd9mTzQ2rLwVbNc8sEe.mp4") == (
        "AQPx4Kd9mTzQ2rLwVbNc8sEe.mp4"
    )


def test_a_name_the_site_chose_is_left_exactly_as_it_is() -> None:
    """Only the leading brand, and only this service's. The point is to stop advertising, not to
    start editing the names a site picked."""
    for name in ("512048337_27461093_n.jpg", "my.instasave.website.jpg", None, ""):
        assert instagram._own_brand_off(name) == name


def test_a_name_that_is_only_the_brand_keeps_it() -> None:
    """Losing the name entirely is worse than keeping a bad one: the download still has to be
    called something, and the collision handling would number it against every other."""
    assert instagram._own_brand_off("instasave.website") == "instasave.website"


# ----------------------------------------------------------- a story video's auto-generated cover


def _tray(*names: str) -> str:
    """A tray of slides as instasave draws them; a `.mp4` slide's card previews a cover."""
    cards = []
    for name in names:
        url = f"https://scontent.cdninstagram.com/{name}"
        thumb = (f"cover-of-{name}.jpg", f"https://scontent.cdninstagram.com/cover-of-{name}.jpg")
        cards.append(_card(name, url, thumb=thumb) if name.endswith(".mp4") else _card(name, url))
    return "".join(cards)


def _flat(*names: str) -> str:
    """The same tray with no download links, as the degraded path is handed."""
    return "".join(
        f'<img src="https://cdn.instasave.website/?token='
        f'{_jwt(name, f"https://scontent.cdninstagram.com/{name}")}">'
        for name in names
    )


STORY = "https://www.instagram.com/stories/quillmoss/3900000000000000456/"


def test_a_story_video_arrives_without_its_cover() -> None:
    """A story video arrives without its cover: covers are previews, not download links."""
    media = parse_instasave_response(_tray("clip.mp4"), STORY)

    assert [item.media_type for item in media.items] == ["video"]
    assert media.items[0].filename == "clip.mp4"


def test_a_genuine_story_photograph_is_kept() -> None:
    """A genuine story photograph is kept: a tray is independent slides."""
    media = parse_instasave_response(_tray("slide.jpg", "another.jpg", "clip.mp4"), STORY)

    assert [item.filename for item in media.items] == ["slide.jpg", "another.jpg", "clip.mp4"]


def test_a_tray_of_photographs_alone_is_untouched() -> None:
    # Nothing to be a cover OF. A story tray with no video in it must come back whole.
    media = parse_instasave_response(_tray("one.jpg", "two.jpg", "three.jpg"), STORY)

    assert [item.filename for item in media.items] == ["one.jpg", "two.jpg", "three.jpg"]


def test_the_survivors_are_renumbered_from_zero() -> None:
    """The index is what the staging step numbers a file by, so a gap in it is a gap on disk."""
    # On the degraded path, where something is removed and the gap closed.
    media = parse_instasave_response(
        _flat("slide.jpg", "cover.jpg", "clip.mp4", "cover2.jpg", "clip2.mp4"), STORY
    )

    assert [item.index for item in media.items] == [0, 1, 2]
    assert [item.filename for item in media.items] == ["slide.jpg", "clip.mp4", "clip2.mp4"]


def test_a_story_photograph_posted_right_before_a_clip_now_survives() -> None:
    """A photograph right before a clip survives: it has a download link and a cover does not."""
    media = parse_instasave_response(_tray("real.jpg", "clip.mp4"), STORY)

    assert [item.filename for item in media.items] == ["real.jpg", "clip.mp4"]


# --- the token reader, put to what a middleman that changed its markup would hand it -------------
#
# None of these may raise: the fallback exists for a middleman whose markup changed.


def test_a_token_that_is_not_a_jwt_at_all_is_no_payload_rather_than_an_error() -> None:
    """`a.b.c` is the shape. A bare string has no middle segment to read."""
    assert instagram._decode_jwt_payload("not-a-jwt") is None


def test_a_token_whose_payload_is_not_base64_or_not_json_is_no_payload() -> None:
    """Two different failures, one answer. `binascii.Error` and `json.JSONDecodeError` both
    subclass ValueError, which is what the one `except` is written against."""
    assert instagram._decode_jwt_payload("a.!!!not-base64!!!.c") is None
    assert (
        instagram._decode_jwt_payload(
            "a." + base64.urlsafe_b64encode(b"not json at all").decode().rstrip("=") + ".c"
        )
        is None
    )
    # And a payload that decodes to JSON which is not an object is not a payload either.
    assert (
        instagram._decode_jwt_payload(
            "a." + base64.urlsafe_b64encode(b'["a list"]').decode().rstrip("=") + ".c"
        )
        is None
    )


def test_a_page_whose_anchors_are_not_download_links_offers_none() -> None:
    """The parser answers about the ELEMENT, so an anchor to anywhere else is not a download,
    and neither is a proxy address that is not on an anchor at all."""
    assert instagram._download_links("<a href='https://example.test/elsewhere'>go</a>") == []
    assert instagram._download_links("<p>no anchors here</p>") == []
    assert instagram._download_links("<a>no href at all</a>") == []


def test_a_link_whose_token_says_nothing_is_still_offered_and_read_by_its_address() -> None:
    """A link whose token says nothing is still offered, fetched by its proxy address."""
    body = '<a href="https://cdn.instasave.website/?token=not-a-jwt-at-all">Download</a>'

    media = parse_instasave_response(body, "https://www.instagram.com/p/XYZ/")

    assert len(media.items) == 1
    assert media.items[0].url == "https://cdn.instasave.website/?token=not-a-jwt-at-all"


def test_one_medium_listed_under_two_tokens_is_fetched_once() -> None:
    """The same file, signed twice. Instagram's own address is signed and expires, so the query
    string differs every time the same media is asked for while the PATH carries its id, and
    without dropping the query these arrive as two items and are fetched twice.
    """
    same = "https://scontent.cdninstagram.com/v/t51/one.jpg"
    body = _anchor("one.jpg", same + "?sig=first") + _anchor("one.jpg", same + "?sig=second")

    media = parse_instasave_response(body, "https://www.instagram.com/p/XYZ/")

    assert len(media.items) == 1
    # The known positive: a genuinely different medium beside it is still two.
    two = _anchor("one.jpg", same) + _anchor(
        "two.jpg", "https://scontent.cdninstagram.com/v/t51/two.jpg"
    )
    assert len(parse_instasave_response(two, "https://www.instagram.com/p/XYZ/").items) == 2


# ------------------------------------------------- naming facts


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("https://www.instagram.com/p/DQmExampl3C/", "DQmExampl3C"),
        ("https://www.instagram.com/someone/reel/DQmExampl3C/?igsh=x", "DQmExampl3C"),
        ("https://www.instagram.com/stories/someone/3456789012345678901/", "3456789012345678901"),
        ("https://www.instagram.com/stories/someone/", None),
    ],
)
def test_the_posts_own_code_is_its_id(url: str, code: str | None) -> None:
    body = _anchor("clip.mp4", "https://scontent.cdninstagram.com/clip.mp4")
    assert parse_instasave_response(body, url).post_id == code
