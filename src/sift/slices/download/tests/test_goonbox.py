# SPDX-License-Identifier: AGPL-3.0-or-later
"""The goonbox.cr resolver, against a faked JSON API.

The API itself is stubbed: these tests are about reading its two endpoints and turning what they
return into direct items, not about the network. The response shapes mirror goonbox's real API: an
image endpoint returns one `image` with an `original_url` on the CDN; an album endpoint returns a
page of `images` and a `pagination` block that says when to stop.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from sift.slices.download.sources import curl as curl_mod
from sift.slices.download.sources import goonbox
from sift.slices.download.sources.curl import Fetched
from sift.slices.download.sources.goonbox import handles, resolve_goonbox
from sift.slices.download.sources.tuning import POLICY

_CDN = "https://simp6.cuckcapital.cr/images4"


def _api(
    responses: dict[str, Fetched], calls: list[str] | None = None
) -> Callable[..., Awaitable[Fetched]]:
    """A fake guarded_get that answers by the first URL fragment it matches, else a 404."""

    async def fake_get(url: str, **_kwargs: Any) -> Fetched:
        if calls is not None:
            calls.append(url)
        for fragment, response in responses.items():
            if fragment in url:
                return response
        return Fetched(404, {}, "")

    return fake_get


def test_handles_goonbox_and_its_cdn() -> None:
    assert handles("https://goonbox.cr/img/Q7mZp2")
    assert handles("https://goonbox.cr/a/kd8LwN")
    assert handles("https://simp6.cuckcapital.cr/images4/x.jpg")  # a CDN file, by suffix
    assert not handles("https://jpg.church/img/ABC")  # a sibling with a different structure
    assert not handles("https://notgoonbox.cr/img/ABC")  # a suffix trap


async def test_an_image_resolves_to_its_original_cdn_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """The image endpoint names the original file; the resolver hands it back as one direct item,
    typed by the extension the API gave, not the tool it would otherwise have fallen through to."""
    body = json.dumps(
        {
            "image": {
                "original_url": f"{_CDN}/91cda280.jpg",
                "extension": "jpg",
                "original_filename": "91cda280.jpg",
                "size_bytes": 273213,
            }
        }
    )
    calls: list[str] = []
    monkeypatch.setattr(
        curl_mod, "guarded_get", _api({"/api/images/": Fetched(200, {}, body)}, calls)
    )

    media = await resolve_goonbox("https://goonbox.cr/img/vRt3Hq9")

    assert media.site == "GoonBox"
    assert len(media.items) == 1
    item = media.items[0]
    assert item.url == f"{_CDN}/91cda280.jpg"
    assert item.ext == ".jpg"
    assert item.backend == "direct"
    assert item.expected_bytes == 273213
    # It asked the API for the id in the path, not the page HTML.
    assert calls == ["https://goonbox.cr/api/images/vRt3Hq9"]


async def test_an_album_pages_through_all_its_images(monkeypatch: pytest.MonkeyPatch) -> None:
    """An album is walked page by page until the API says the current page is the last, and every
    image across the pages comes back as its own direct item, in order."""
    page1 = json.dumps(
        {
            "images": [{"original_url": f"{_CDN}/a.jpg", "extension": "jpg"}],
            "pagination": {"current_page": 1, "last_page": 2},
        }
    )
    page2 = json.dumps(
        {
            "images": [{"original_url": f"{_CDN}/b.jpg", "extension": "jpg"}],
            "pagination": {"current_page": 2, "last_page": 2},
        }
    )
    calls: list[str] = []
    monkeypatch.setattr(
        curl_mod,
        "guarded_get",
        _api({"page=2": Fetched(200, {}, page2), "page=1": Fetched(200, {}, page1)}, calls),
    )

    media = await resolve_goonbox("https://goonbox.cr/a/kd8LwN")

    assert [item.url for item in media.items] == [f"{_CDN}/a.jpg", f"{_CDN}/b.jpg"]
    assert [item.index for item in media.items] == [0, 1]
    # It stopped after the page the API called last: it did not ask for a third.
    assert "page=3" not in " ".join(calls)


async def test_an_album_waits_its_sites_pace_before_every_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A download's pages are its requests like any other, so each waits the time the Site is
    given between one request and the next."""
    page1 = json.dumps(
        {
            "images": [{"original_url": f"{_CDN}/a.jpg", "extension": "jpg"}],
            "pagination": {"current_page": 1, "last_page": 2},
        }
    )
    page2 = json.dumps(
        {
            "images": [{"original_url": f"{_CDN}/b.jpg", "extension": "jpg"}],
            "pagination": {"current_page": 2, "last_page": 2},
        }
    )
    said: list[str] = []
    monkeypatch.setattr(
        curl_mod,
        "guarded_get",
        _api({"page=2": Fetched(200, {}, page2), "page=1": Fetched(200, {}, page1)}, said),
    )

    class _Paced:
        @classmethod
        def for_policy(cls, _policy: object) -> _Paced:
            return cls()

        async def wait(self) -> None:
            said.append("wait")

    monkeypatch.setattr(goonbox, "Pacer", _Paced)

    media = await resolve_goonbox("https://goonbox.cr/a/kd8LwN", policy=POLICY)

    assert len(media.items) == 2
    pages = [one for one in said if one != "wait"]
    assert said == ["wait", pages[0], "wait", pages[1]], "a page was asked for without its wait"


async def test_a_cdn_file_link_is_taken_as_direct_media(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare link on the CDN is already the media: it becomes a direct item with no API call at all."""

    async def _boom(_url: str, **_kwargs: Any) -> Fetched:
        raise AssertionError("a CDN file link must not hit the API")

    monkeypatch.setattr(curl_mod, "guarded_get", _boom)

    media = await resolve_goonbox(f"{_CDN}/x.png")

    assert len(media.items) == 1
    assert media.items[0].url == f"{_CDN}/x.png"
    assert media.items[0].backend == "direct"


async def test_a_failed_api_read_falls_through_to_a_subprocess_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A challenge page or a moved endpoint (any non-200) resolves to nothing, so the resolver hands
    back one subprocess item for a tool to try: it never raises and hangs the resolve."""
    monkeypatch.setattr(curl_mod, "guarded_get", _api({}))  # everything 404s

    media = await resolve_goonbox("https://goonbox.cr/img/deadbeef")

    assert len(media.items) == 1
    assert media.items[0].backend == "gallerydl"


def test_the_resolver_exports_only_its_two_public_names() -> None:
    assert set(goonbox.__all__) == {"handles", "resolve_goonbox"}


# --- somebody else's API, answering badly ---------------------------------------------------------
#
# Every branch below is a shape the real endpoint has no business returning and might. This is a
# third-party API read over the network: a challenge page, a partial deploy or a changed field name
# all arrive as valid JSON of the wrong shape, and the resolver's promise is that it hands back
# whatever it could gather and falls through to a tool rather than raising into the job.


async def test_a_body_that_is_not_json_at_all_resolves_to_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 carrying an interstitial page. Parsed as JSON it raises, and the raise would come out
    of the resolve rather than out of the download it belongs to."""
    monkeypatch.setattr(
        curl_mod, "guarded_get", _api({"api/images": Fetched(200, {}, "<html>nope</html>")})
    )

    media = await resolve_goonbox("https://goonbox.cr/img/Q7mZp2")

    assert [item.backend for item in media.items] == ["gallerydl"]


@pytest.mark.parametrize(
    "image",
    [
        pytest.param("not an object", id="a string where an object was expected"),
        pytest.param({}, id="an object naming no original file"),
        pytest.param({"original_url": ""}, id="an empty original file"),
        pytest.param({"original_url": 7}, id="an original file that is not text"),
    ],
)
async def test_an_image_object_that_names_no_file_resolves_to_nothing(
    monkeypatch: pytest.MonkeyPatch, image: Any
) -> None:
    body = json.dumps({"image": image})
    monkeypatch.setattr(curl_mod, "guarded_get", _api({"api/images": Fetched(200, {}, body)}))

    media = await resolve_goonbox("https://goonbox.cr/img/Q7mZp2")

    assert [item.backend for item in media.items] == ["gallerydl"]


async def test_an_album_keeps_what_it_gathered_when_a_later_page_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first page worked and the second did not. Everything already read is worth having:
    losing it because page four timed out would turn a partial answer into no answer."""
    page1 = json.dumps(
        {
            "images": [{"original_url": f"{_CDN}/a.jpg", "extension": "jpg"}],
            "pagination": {"current_page": 1, "last_page": 3},
        }
    )
    monkeypatch.setattr(curl_mod, "guarded_get", _api({"page=1": Fetched(200, {}, page1)}))

    media = await resolve_goonbox("https://goonbox.cr/a/kd8LwN")

    assert [item.url for item in media.items] == [f"{_CDN}/a.jpg"]


async def test_an_album_page_that_is_not_json_keeps_the_pages_before_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 carrying an interstitial page rather than an answer, partway through an album.

    Parsed as JSON it raises, and a raise here is not the same as an empty page: it comes out of
    the middle of the walk and takes every image already gathered with it. Read as "no more", the
    album is one page shorter than it should have been and still worth having.
    """
    page1 = json.dumps(
        {
            "images": [{"original_url": f"{_CDN}/a.jpg", "extension": "jpg"}],
            "pagination": {"current_page": 1, "last_page": 3},
        }
    )
    monkeypatch.setattr(
        curl_mod,
        "guarded_get",
        _api(
            {
                "page=1": Fetched(200, {}, page1),
                "page=2": Fetched(200, {}, "<html>just a moment</html>"),
            }
        ),
    )

    media = await resolve_goonbox("https://goonbox.cr/a/kd8LwN")

    assert [item.url for item in media.items] == [f"{_CDN}/a.jpg"]


async def test_an_album_page_that_says_nothing_about_paging_is_the_last_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No pagination block, so there is no next page to ask for. Asking anyway is how a resolver
    walks to its own ceiling against an endpoint that only ever returns one page."""
    body = json.dumps({"images": [{"original_url": f"{_CDN}/a.jpg", "extension": "jpg"}]})
    calls: list[str] = []
    monkeypatch.setattr(
        curl_mod, "guarded_get", _api({"api/albums": Fetched(200, {}, body)}, calls)
    )

    media = await resolve_goonbox("https://goonbox.cr/a/kd8LwN")

    assert [item.url for item in media.items] == [f"{_CDN}/a.jpg"]
    assert "page=2" not in " ".join(calls)


async def test_an_album_page_whose_images_are_not_a_list_contributes_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps({"images": {"not": "a list"}})
    monkeypatch.setattr(curl_mod, "guarded_get", _api({"api/albums": Fetched(200, {}, body)}))

    media = await resolve_goonbox("https://goonbox.cr/a/kd8LwN")

    assert [item.backend for item in media.items] == ["gallerydl"]


async def test_an_album_holding_something_that_is_not_an_image_skips_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One bad entry among good ones is not a reason to drop the album."""
    body = json.dumps(
        {
            "images": [
                "not an object",
                {"original_url": f"{_CDN}/good.jpg", "extension": "jpg"},
            ]
        }
    )
    monkeypatch.setattr(curl_mod, "guarded_get", _api({"api/albums": Fetched(200, {}, body)}))

    media = await resolve_goonbox("https://goonbox.cr/a/kd8LwN")

    assert [item.url for item in media.items] == [f"{_CDN}/good.jpg"]


async def test_an_album_that_never_says_it_has_finished_stops_at_the_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An endpoint that answers every page with "there is one more" would be walked forever. The
    ceiling is what makes a resolve finish against an API nobody here controls."""
    monkeypatch.setattr(goonbox, "_MAX_ALBUM_PAGES", 3)
    body = json.dumps(
        {
            "images": [{"original_url": f"{_CDN}/a.jpg", "extension": "jpg"}],
            "pagination": {"current_page": 1, "last_page": 9_999},
        }
    )
    calls: list[str] = []
    monkeypatch.setattr(
        curl_mod, "guarded_get", _api({"api/albums": Fetched(200, {}, body)}, calls)
    )

    media = await resolve_goonbox("https://goonbox.cr/a/kd8LwN")

    assert len(calls) == 3, "the album was walked past its ceiling"
    assert len(media.items) == 3


async def test_an_address_on_the_site_that_is_neither_an_image_nor_an_album_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A profile page, a search, the front page. There is no API call that would answer it, so it
    goes straight to a tool rather than being read as an empty album."""

    async def _boom(_url: str, **_kwargs: Any) -> Fetched:
        raise AssertionError("an address with no id in it must not hit the API")

    monkeypatch.setattr(curl_mod, "guarded_get", _boom)

    media = await resolve_goonbox("https://goonbox.cr/profile/somebody")

    assert [item.backend for item in media.items] == ["gallerydl"]
