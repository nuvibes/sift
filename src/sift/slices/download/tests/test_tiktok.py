# SPDX-License-Identifier: AGPL-3.0-or-later
"""The TikTok resolver. The parsing is a pure function of tikwm's JSON and is tested from captured
bodies; the network call is faked, and the limiter is neutralised so no test waits on real time."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from sift.slices.download.sources import curl as curl_mod
from sift.slices.download.sources import tiktok
from sift.slices.download.sources.curl import Fetched
from sift.slices.download.sources.errors import DownloadError, NothingFound
from sift.slices.download.sources.ratelimit import LIMITER
from sift.slices.download.sources.tiktok import handles, parse_tikwm_response, resolve_tiktok

_URL = "https://www.tiktok.com/@creator/video/1"


def _payload(**data: Any) -> dict[str, Any]:
    return {"code": 0, "data": data}


# --------------------------------------------------------------------------- usernames


def test_handles_only_tiktok_hosts() -> None:
    assert handles("https://www.tiktok.com/@a/video/1")
    assert handles("https://vm.tiktok.com/abc")
    assert not handles("https://nottiktok.com/@a")  # a suffix trap, not a match
    assert not handles("https://youtube.com/watch?v=1")


# --------------------------------------------------------------------------- parse (pure)


def test_it_prefers_the_hd_video_and_reads_the_hints() -> None:
    media = parse_tikwm_response(
        _payload(
            hdplay="https://cdn/hd.mp4",
            hd_size=1000,
            play="https://cdn/sd.mp4",
            size=500,
            duration=12,
            title="a clip",
        ),
        _URL,
    )
    assert media.site == "TikTok"
    assert media.username == "creator"
    assert media.title == "a clip"
    (item,) = media.items
    assert item.url == "https://cdn/hd.mp4"
    assert item.media_type == "video"
    assert item.ext == ".mp4"
    assert item.referer == tiktok.TIKWM_REFERER
    assert item.expected_bytes == 1000
    assert item.duration_sec == 12.0
    assert item.backend == "direct"


def test_it_falls_back_to_the_no_watermark_video() -> None:
    media = parse_tikwm_response(_payload(play="https://cdn/sd.mp4", size=0), _URL)
    (item,) = media.items
    assert item.url == "https://cdn/sd.mp4"
    assert item.expected_bytes is None  # a non-positive size hint is dropped
    assert item.duration_sec is None


def test_a_photo_slideshow_is_delivered_as_images() -> None:
    media = parse_tikwm_response(
        _payload(images=["https://cdn/1.jpg", "/2.jpg"], play="https://cdn/render.mp4"),
        "https://www.tiktok.com/@creator/photo/9",
    )
    assert [item.media_type for item in media.items] == ["image", "image"]
    assert (
        media.items[1].url == tiktok.TIKWM_ORIGIN + "/2.jpg"
    )  # a site-relative path made absolute
    assert media.items[0].refetch_url == "https://www.tiktok.com/@creator/photo/9"


def test_the_username_falls_back_to_the_author_when_the_url_lacks_one() -> None:
    media = parse_tikwm_response(
        {"code": 0, "data": {"play": "https://cdn/x.mp4", "author": {"unique_id": "bob"}}},
        "https://vm.tiktok.com/abc",
    )
    assert media.username == "bob"


def test_no_username_anywhere_leaves_it_unset() -> None:
    # No author object at all: the URL carries no username and there is nothing to fall back to.
    absent = parse_tikwm_response(
        {"code": 0, "data": {"play": "https://cdn/x.mp4"}}, "https://vm.tiktok.com/abc"
    )
    assert absent.username is None
    # An author object with no usable id: same result, a different path to it.
    empty = parse_tikwm_response(
        {"code": 0, "data": {"play": "https://cdn/x.mp4", "author": {}}},
        "https://vm.tiktok.com/abc",
    )
    assert empty.username is None


def test_an_error_code_is_nothing_found() -> None:
    with pytest.raises(NothingFound):
        parse_tikwm_response({"code": -1, "msg": "nope"}, _URL)


def test_a_missing_data_object_is_nothing_found() -> None:
    with pytest.raises(NothingFound):
        parse_tikwm_response({"code": 0}, _URL)


def test_a_response_with_no_usable_media_is_nothing_found() -> None:
    with pytest.raises(NothingFound):
        parse_tikwm_response(_payload(title="just a title"), _URL)


def test_a_slideshow_of_only_invalid_entries_is_nothing_found() -> None:
    with pytest.raises(NothingFound):
        parse_tikwm_response(_payload(images=[123, None]), _URL)


# --------------------------------------------------------------------------- resolve (network faked)


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def acquire(_host: str) -> None:
        return None

    monkeypatch.setattr(LIMITER, "acquire", acquire)
    monkeypatch.setattr(LIMITER, "note_retry_after", lambda *_a, **_k: None)


def _fetched(text: str, *, status: int = 200, headers: dict[str, str] | None = None) -> Fetched:
    return Fetched(status_code=status, headers=headers or {}, text=text)


def _stub_curl(monkeypatch: pytest.MonkeyPatch, responses: list[Fetched]) -> None:
    replies = iter(responses)

    async def fake_get(_url: str, **_kwargs: object) -> Fetched:
        return next(replies)

    monkeypatch.setattr(curl_mod, "guarded_get", fake_get)


async def test_it_resolves_to_a_direct_video(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_curl(monkeypatch, [_fetched(json.dumps(_payload(play="https://cdn/x.mp4", size=100)))])
    media = await resolve_tiktok(_URL)
    assert media.items[0].url == "https://cdn/x.mp4"
    assert media.items[0].backend == "direct"


async def test_it_retries_once_after_a_429(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_curl(
        monkeypatch,
        [
            _fetched("", status=429, headers={"Retry-After": "1"}),
            _fetched(json.dumps(_payload(play="https://cdn/x.mp4"))),
        ],
    )
    media = await resolve_tiktok(_URL)
    assert media.items[0].url == "https://cdn/x.mp4"


async def test_it_retries_after_an_in_body_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_curl(
        monkeypatch,
        [
            _fetched(json.dumps({"code": -1, "msg": "Free Api Limit: 1 request/second."})),
            _fetched(json.dumps(_payload(play="https://cdn/x.mp4"))),
        ],
    )
    media = await resolve_tiktok(_URL)
    assert media.items[0].url == "https://cdn/x.mp4"


async def test_a_persistent_limit_becomes_a_retryable_download_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_curl(monkeypatch, [_fetched("", status=429), _fetched("", status=429)])
    with pytest.raises(DownloadError):
        await resolve_tiktok(_URL)


async def test_a_non_json_body_is_nothing_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_curl(monkeypatch, [_fetched("<html>blocked</html>")])
    with pytest.raises(NothingFound):
        await resolve_tiktok(_URL)


async def test_a_non_dict_json_body_is_nothing_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_curl(monkeypatch, [_fetched("[1, 2, 3]")])
    with pytest.raises(NothingFound):
        await resolve_tiktok(_URL)


async def test_a_deleted_post_that_is_not_a_rate_limit_is_nothing_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_curl(monkeypatch, [_fetched(json.dumps({"code": -1, "msg": "video not found"}))])
    with pytest.raises(NothingFound):
        await resolve_tiktok(_URL)


# --------------------------------------------------------------------------- naming facts


def test_the_post_number_is_read_from_the_link() -> None:
    """`{id}` for a TikTok post: the number in `/video/<number>`, before any answer is read."""
    media = parse_tikwm_response(
        _payload(play="https://cdn/sd.mp4", id="1"),
        "https://www.tiktok.com/@creator/video/7401234567890123456",
    )
    assert media.post_id == "7401234567890123456"


def test_a_short_link_takes_the_post_number_from_the_answer() -> None:
    media = parse_tikwm_response(
        _payload(play="https://cdn/sd.mp4", id="7401234567890123456"), "https://vm.tiktok.com/Zabc/"
    )
    assert media.post_id == "7401234567890123456"


def test_the_caption_and_the_posting_time_are_kept_for_the_name() -> None:
    """The caption is read and stored, and `{title}` reads it. The
    posting time is tikwm's `create_time`, in seconds."""
    media = parse_tikwm_response(
        _payload(play="https://cdn/sd.mp4", title="a clip", create_time=1_789_000_000), _URL
    )
    assert media.title == "a clip"
    assert media.posted == datetime.fromtimestamp(1_789_000_000, UTC)


@pytest.mark.parametrize("create_time", [None, 0, -5, "1789000000", True])
def test_no_usable_posting_time_leaves_it_unknown(create_time: object) -> None:
    media = parse_tikwm_response(
        _payload(play="https://cdn/sd.mp4", create_time=create_time), "https://vm.tiktok.com/Zabc/"
    )
    assert media.posted is None
    assert media.post_id is None


def test_a_creation_time_past_what_the_clock_holds_is_no_posting_time() -> None:
    assert tiktok._posted({"create_time": 10**20}) is None
    assert tiktok._posted({"create_time": 1_700_000_000}) == datetime.fromtimestamp(
        1_700_000_000, UTC
    )


def test_a_post_number_not_in_the_link_is_taken_from_tikwms_own_id() -> None:
    """A short link carries no number; tikwm's `id` is its copy of the same one, as a number or as
    digits. Anything else (a flag, a word) is no post number."""
    short = "https://vm.tiktok.com/ZMabc/"
    assert tiktok._post_id(short, {"id": 7300000000000000001}) == "7300000000000000001"
    assert tiktok._post_id(short, {"id": "7300000000000000001"}) == "7300000000000000001"
    assert tiktok._post_id(short, {"id": True}) is None
    assert tiktok._post_id(short, {"id": "not-a-number"}) is None


async def test_a_longer_wait_after_a_rate_limit_raises_tikwms_backoff_and_a_shorter_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The measured backoff is the floor; a CHOSEN setting raises it and never lowers it, and the
    default (sixty seconds nobody chose) leaves the measured five alone. The ask itself is handed
    the download's settings, so it is timed and retried by them."""
    from dataclasses import replace

    from sift.slices.download.sources.tuning import PACING, POLICY

    held: list[float] = []
    monkeypatch.setattr(LIMITER, "note_retry_after", lambda _host, wait: held.append(wait))
    asked: list[object] = []

    def policy_of(wait: float | None) -> Any:
        if wait is None:
            return POLICY
        return replace(
            POLICY, pacing=replace(PACING, wait_after_too_many_requests=wait, wait_chosen=True)
        )

    for wait in (120.0, 1.0, None):
        replies = iter([_fetched("", status=429), _fetched(json.dumps(_payload(play="x.mp4")))])

        async def fake_get(_url: str, **kwargs: object) -> Fetched:
            asked.append(kwargs.get("policy"))
            return next(replies)  # noqa: B023

        monkeypatch.setattr(curl_mod, "guarded_get", fake_get)
        await resolve_tiktok(_URL, policy=policy_of(wait))

    assert held == [120.0, 5.0, 5.0]
    assert all(policy is not None for policy in asked)
