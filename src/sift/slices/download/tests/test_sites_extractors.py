# SPDX-License-Identifier: AGPL-3.0-or-later
"""The individual site extractors. The session is faked; each test is about the endpoint the
extractor calls and the addresses it reads back, not aiohttp."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import aiohttp
import pytest
from aiohttp.client import DEFAULT_TIMEOUT

from sift.slices.download.sources.sites import Answers
from sift.slices.download.sources.sites.common import ExtractContext
from sift.slices.download.sources.sites.extractors import (
    _attr,
    _bunkr_album_files,
    _bunkr_api_download,
    _bunkr_js_vars,
    _bunkr_sign,
    _gofile_content_id,
    _soup,
    extract_bunkr,
    extract_coomer,
    extract_cyberdrop,
    extract_cyberfile,
    extract_fapello,
    extract_gofile,
    extract_hqporner,
    extract_jpg5,
    extract_kemono,
    extract_pixeldrain,
    extract_pmvhaven,
    extract_turbovid,
    extract_xbunkr,
    find_pmvhaven_video,
    gofile_files,
    gofile_web_token,
    hqporner_best_source,
    hqporner_player,
    hqporner_title,
    parse_coomer_post,
    parse_cyberfile_details,
    parse_sign_response,
    turbovid_sign_origins,
)

_CTX = ExtractContext(user_agent="test-agent")

_JPG5_KEY = b"seltilovessimpcity@simpcityhatesscrapers"


def _jpg5_encrypt(url: str) -> str:
    """Make a JPG5-obfuscated blob (XOR -> hex -> base64) for a test page."""
    xored = bytes(byte ^ _JPG5_KEY[i % len(_JPG5_KEY)] for i, byte in enumerate(url.encode()))
    return base64.b64encode(xored.hex().encode()).decode()


class _Tag:
    def __init__(self, attrs: dict[str, Any]) -> None:
        self._attrs = attrs

    def get(self, name: str) -> Any:
        return self._attrs.get(name)


def test_attr_reads_a_string_a_multivalued_attr_or_nothing() -> None:
    assert _attr(_Tag({"src": "u"}), "src") == "u"
    assert _attr(_Tag({"class": ["a", "b"]}), "class") == "a"  # multi-valued -> the first
    assert _attr(_Tag({"class": []}), "class") is None  # an empty list is nothing
    assert _attr(_Tag({}), "src") is None  # a missing attribute is nothing


class _Response:
    def __init__(self, *, ok: bool = True, text: str = "") -> None:
        self.ok = ok
        self._text = text

    async def text(self) -> str:
        return self._text

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _Session:
    #: What a session opened without a download's settings carries (aiohttp's own default). The
    #: readers take their connection budget from it (`extractors._within`).
    timeout = DEFAULT_TIMEOUT

    def __init__(self, response: _Response | None = None) -> None:
        self._response = response or _Response()
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: Any) -> _Response:
        self.calls.append(url)
        return self._response

    def post(self, url: str, **_kwargs: Any) -> _Response:
        self.calls.append(url)
        return self._response


class _Router:
    """A session that returns a different response per URL, matched by substring, for the multi-call
    extractors. A route value that is an exception is raised (a connection failure); an unmatched URL
    is a not-ok response."""

    timeout = DEFAULT_TIMEOUT

    def __init__(self, routes: dict[str, _Response | Exception]) -> None:
        self._routes = routes
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: Any) -> _Response:
        return self._route(url)

    def post(self, url: str, **_kwargs: Any) -> _Response:
        return self._route(url)

    def _route(self, url: str) -> _Response:
        self.calls.append(url)
        for key, response in self._routes.items():
            if key in url:
                if isinstance(response, Exception):
                    raise response
                return response
        return _Response(ok=False)


def _as_session(fake: _Session | _Router) -> aiohttp.ClientSession:
    return cast("aiohttp.ClientSession", fake)


# ------------------------------------------------------------------- Pixeldrain


async def test_a_pixeldrain_file_is_named_from_its_info_and_keeps_its_id() -> None:
    """Not `<id>.mp4`, though the download address ends in the ID. The name it was uploaded
    under comes from the file's info, and the ID stays as the `{id}` naming word."""
    session = _Session(_Response(text='{"id": "abc123", "name": "holiday.mp4"}'))
    found = await extract_pixeldrain("https://pixeldrain.com/u/abc123", _as_session(session), _CTX)
    assert [(f.url, f.filename, f.id) for f in found] == [
        ("https://pixeldrain.com/api/file/abc123", "holiday.mp4", "abc123")
    ]
    assert session.calls == ["https://pixeldrain.com/api/file/abc123/info"]


@pytest.mark.parametrize("answer", [_Response(ok=False), _Response(text="not json")])
async def test_a_pixeldrain_file_whose_info_will_not_read_still_downloads(
    answer: _Response,
) -> None:
    session = _Session(answer)
    found = await extract_pixeldrain("https://pixeldrain.com/u/abc123", _as_session(session), _CTX)
    assert [(f.url, f.filename, f.id) for f in found] == [
        ("https://pixeldrain.com/api/file/abc123", None, "abc123")
    ]


async def test_a_pixeldrain_list_reads_each_file_from_the_list_api() -> None:
    body = '{"files": [{"id": "a", "name": "one.jpg"}, {"id": "b"}, {"noid": 1}]}'
    session = _Session(_Response(text=body))
    found = await extract_pixeldrain("https://pixeldrain.com/l/xyz", _as_session(session), _CTX)
    assert [(f.url, f.filename, f.id) for f in found] == [
        ("https://pixeldrain.com/api/file/a", "one.jpg", "a"),
        ("https://pixeldrain.com/api/file/b", None, "b"),
    ]
    assert session.calls == ["https://pixeldrain.com/api/list/xyz"]


async def test_a_pixeldrain_list_that_will_not_load_yields_nothing() -> None:
    session = _Session(_Response(ok=False))
    found = await extract_pixeldrain("https://pixeldrain.com/l/xyz", _as_session(session), _CTX)
    assert found == []


async def test_an_unrecognized_pixeldrain_path_yields_nothing() -> None:
    session = _Session()
    assert await extract_pixeldrain("https://pixeldrain.com/x", _as_session(session), _CTX) == []
    assert await extract_pixeldrain("https://pixeldrain.com/d/i", _as_session(session), _CTX) == []


# ------------------------------------------------------------------- Fapello


async def test_fapello_reads_images_and_video_sources_from_the_content_block() -> None:
    html = """
    <html><body>
      <div id="content">
        <img src="https://cdn.fapello.com/1.jpg">
        <video><source src="https://cdn.fapello.com/2.mp4"></video>
      </div>
      <div id="other"><img src="https://cdn.fapello.com/ignored.jpg"></div>
    </body></html>
    """
    session = _Session(_Response(text=html))
    found = await extract_fapello("https://fapello.su/model/1/", _as_session(session), _CTX)
    assert [f.url for f in found] == [
        "https://cdn.fapello.com/1.jpg",
        "https://cdn.fapello.com/2.mp4",
    ]


async def test_fapello_yields_nothing_when_the_page_will_not_load() -> None:
    session = _Session(_Response(ok=False))
    assert await extract_fapello("https://fapello.su/model/1/", _as_session(session), _CTX) == []


async def test_fapello_yields_nothing_when_the_content_block_is_empty() -> None:
    session = _Session(_Response(text="<html><body><div id='content'></div></body></html>"))
    assert await extract_fapello("https://fapello.su/model/1/", _as_session(session), _CTX) == []


async def test_fapello_skips_a_matched_tag_that_has_no_src() -> None:
    html = """
    <div id="content">
      <img>
      <img src="https://cdn.fapello.com/real.jpg">
    </div>
    """
    session = _Session(_Response(text=html))
    found = await extract_fapello("https://fapello.su/model/1/", _as_session(session), _CTX)
    assert [f.url for f in found] == [
        "https://cdn.fapello.com/real.jpg"
    ]  # the src-less img skipped


# ------------------------------------------------------------------- Cyberdrop


async def test_cyberdrop_resolves_a_file_through_info_then_auth() -> None:
    session = _Router(
        {
            "/file/info/abc": _Response(text='{"name": "clip.mp4"}'),
            "/file/auth/abc": _Response(text='{"url": "https://cdn.cyberdrop/real.mp4"}'),
        }
    )
    found = await extract_cyberdrop("https://cyberdrop.cr/f/abc", _as_session(session), _CTX)
    assert [(f.url, f.filename) for f in found] == [("https://cdn.cyberdrop/real.mp4", "clip.mp4")]


async def test_cyberdrop_uses_the_e_prefix_and_survives_a_missing_name() -> None:
    session = _Router(
        {
            "/file/info/abc": _Response(ok=False),  # info miss -> no name, still resolves
            "/file/auth/abc": _Response(text='{"url": "https://cdn.cyberdrop/real.mp4"}'),
        }
    )
    found = await extract_cyberdrop("https://cyberdrop.me/e/abc", _as_session(session), _CTX)
    assert [(f.url, f.filename) for f in found] == [("https://cdn.cyberdrop/real.mp4", None)]


async def test_cyberdrop_yields_nothing_when_auth_fails_or_has_no_url() -> None:
    no_auth = _Router({"/file/auth/abc": _Response(ok=False)})
    assert await extract_cyberdrop("https://cyberdrop.cr/f/abc", _as_session(no_auth), _CTX) == []
    no_url = _Router({"/file/auth/abc": _Response(text="{}")})
    assert await extract_cyberdrop("https://cyberdrop.cr/f/abc", _as_session(no_url), _CTX) == []


async def test_cyberdrop_album_resolves_each_linked_file() -> None:
    album = '<a class="image" href="/f/one"></a><a class="image" href="/f/two"></a>'
    session = _Router(
        {
            "/a/set": _Response(text=album),
            "/file/info/one": _Response(text='{"name": "1.jpg"}'),
            "/file/auth/one": _Response(text='{"url": "https://cdn/1.jpg"}'),
            "/file/info/two": _Response(text='{"name": "2.jpg"}'),
            "/file/auth/two": _Response(text='{"url": "https://cdn/2.jpg"}'),
        }
    )
    found = await extract_cyberdrop("https://cyberdrop.cr/a/set", _as_session(session), _CTX)
    assert [f.url for f in found] == ["https://cdn/1.jpg", "https://cdn/2.jpg"]


async def test_cyberdrop_album_that_will_not_load_yields_nothing() -> None:
    session = _Router({})  # every URL is not-ok
    assert await extract_cyberdrop("https://cyberdrop.cr/a/set", _as_session(session), _CTX) == []


async def test_an_unrecognized_cyberdrop_path_yields_nothing() -> None:
    session = _Router({})
    assert await extract_cyberdrop("https://cyberdrop.cr/", _as_session(session), _CTX) == []


# ------------------------------------------------------------------- XBunkr


async def test_an_xbunkr_media_host_address_is_already_direct() -> None:
    session = _Session()
    url = "https://media.xbunkr.com/abc.jpg"
    found = await extract_xbunkr(url, _as_session(session), _CTX)
    assert [f.url for f in found] == [url]
    assert session.calls == []  # a media address needs no page fetch


async def test_an_xbunkr_album_reads_its_image_anchors() -> None:
    html = '<a class="image" href="https://media.xbunkr.com/1.jpg"></a><a class="image"></a>'
    session = _Session(_Response(text=html))
    found = await extract_xbunkr("https://xbunkr.com/album/1", _as_session(session), _CTX)
    assert [f.url for f in found] == [
        "https://media.xbunkr.com/1.jpg"
    ]  # the href-less anchor skipped


async def test_an_xbunkr_album_that_will_not_load_yields_nothing() -> None:
    session = _Session(_Response(ok=False))
    assert await extract_xbunkr("https://xbunkr.com/album/1", _as_session(session), _CTX) == []


async def test_cyberdrop_file_with_info_present_but_no_name_still_resolves() -> None:
    session = _Router(
        {
            "/file/info/abc": _Response(text="{}"),  # ok, but no usable name
            "/file/auth/abc": _Response(text='{"url": "https://cdn/x.mp4"}'),
        }
    )
    found = await extract_cyberdrop("https://cyberdrop.cr/f/abc", _as_session(session), _CTX)
    assert [(f.url, f.filename) for f in found] == [("https://cdn/x.mp4", None)]


async def test_cyberdrop_album_skips_an_anchor_with_no_href() -> None:
    album = '<a class="image"></a><a class="image" href="/f/one"></a>'
    session = _Router(
        {
            "/a/set": _Response(text=album),
            "/file/info/one": _Response(text='{"name": "1.jpg"}'),
            "/file/auth/one": _Response(text='{"url": "https://cdn/1.jpg"}'),
        }
    )
    found = await extract_cyberdrop("https://cyberdrop.cr/a/set", _as_session(session), _CTX)
    assert [f.url for f in found] == ["https://cdn/1.jpg"]  # the href-less anchor skipped


# ------------------------------------------------------------------- GoFile


def test_gofile_web_token_is_deterministic_bucketed_and_agent_bound() -> None:
    token = gofile_web_token("ua", "tok", now=0.0)
    assert token == gofile_web_token("ua", "tok", now=1000.0)  # same 4h bucket -> same token
    assert len(token) == 64 and all(c in "0123456789abcdef" for c in token)
    assert token != gofile_web_token("ua", "tok", now=14400.0)  # the next bucket differs
    assert token != gofile_web_token("other", "tok", now=0.0)  # the user agent is part of it


def test_gofile_content_id_reads_both_url_shapes() -> None:
    assert _gofile_content_id("https://gofile.io/d/abc") == "abc"
    assert _gofile_content_id("https://gofile.io/xyz") == "xyz"
    assert _gofile_content_id("https://gofile.io/") is None


def test_gofile_files_reads_a_single_file_with_its_cookie() -> None:
    node = {"type": "file", "link": "https://cdn/x.mp4", "name": "x.mp4"}
    (found,) = gofile_files(node, "accountToken=tok")
    assert (found.url, found.filename, found.cookie) == (
        "https://cdn/x.mp4",
        "x.mp4",
        "accountToken=tok",
    )


def test_gofile_files_falls_back_to_the_direct_link_when_overloaded() -> None:
    node = {"type": "file", "link": "overloaded", "directLink": "https://cdn/real.mp4"}
    (found,) = gofile_files(node, "c")
    assert found.url == "https://cdn/real.mp4"
    assert found.filename is None  # no name field


def test_gofile_files_walks_a_folder_and_skips_what_has_no_link() -> None:
    node = {
        "type": "folder",
        "children": {
            "a": {"type": "file", "link": "https://cdn/1.mp4"},
            "b": {
                "type": "folder",
                "children": {"c": {"type": "file", "link": "https://cdn/2.mp4"}},
            },
            "d": {"type": "file"},  # no link -> skipped
            "e": {"type": "folder"},  # a folder with no children
            "f": "not-a-dict",  # skipped
        },
    }
    assert [f.url for f in gofile_files(node, "c")] == ["https://cdn/1.mp4", "https://cdn/2.mp4"]


def test_gofile_files_ignores_a_non_dict_node() -> None:
    assert gofile_files("nope", "c") == []


async def test_gofile_resolves_a_file_with_the_guest_cookie() -> None:
    session = _Router(
        {
            "/accounts": _Response(text='{"data": {"token": "tok123"}}'),
            "/contents/": _Response(
                text='{"status": "ok", "data": {"type": "file", "link": "https://cdn/x.mp4"}}'
            ),
        }
    )
    found = await extract_gofile("https://gofile.io/d/folder", _as_session(session), _CTX)
    assert [(f.url, f.cookie) for f in found] == [("https://cdn/x.mp4", "accountToken=tok123")]


async def test_gofile_needs_a_content_id() -> None:
    assert await extract_gofile("https://gofile.io/", _as_session(_Router({})), _CTX) == []


async def test_gofile_stops_when_the_guest_account_cannot_be_minted() -> None:
    no_acc = _Router({"/accounts": _Response(ok=False)})
    assert await extract_gofile("https://gofile.io/d/f", _as_session(no_acc), _CTX) == []


async def test_gofile_stops_when_no_token_comes_back() -> None:
    empty = _Router({"/accounts": _Response(text='{"data": {}}')})
    assert await extract_gofile("https://gofile.io/d/f", _as_session(empty), _CTX) == []
    not_object = _Router({"/accounts": _Response(text="[]")})  # the body is not an object
    assert await extract_gofile("https://gofile.io/d/f", _as_session(not_object), _CTX) == []


async def test_gofile_stops_when_contents_fail_or_are_not_ok() -> None:
    fail = _Router(
        {"/accounts": _Response(text='{"data": {"token": "t"}}'), "/contents/": _Response(ok=False)}
    )
    assert await extract_gofile("https://gofile.io/d/f", _as_session(fail), _CTX) == []
    not_ok = _Router(
        {
            "/accounts": _Response(text='{"data": {"token": "t"}}'),
            "/contents/": _Response(text='{"status": "error"}'),
        }
    )
    assert await extract_gofile("https://gofile.io/d/f", _as_session(not_ok), _CTX) == []


# ------------------------------------------------------------------- JPG5


async def test_jpg5_reads_the_obfuscated_script_blob() -> None:
    blob = _jpg5_encrypt("https://cdn.jpg5/secret.jpg")
    html = f"<script>/* obfuscated */ var d = 'data-src=\"{blob}\"';</script>"
    session = _Session(_Response(text=html))
    found = await extract_jpg5("https://jpg5.su/img/1", _as_session(session), _CTX)
    assert [f.url for f in found] == ["https://cdn.jpg5/secret.jpg"]


async def test_jpg5_falls_back_to_the_download_button_blob() -> None:
    blob = _jpg5_encrypt("https://cdn.jpg5/button.jpg")
    html = f'<a class="btn-download" href="{blob}"></a>'
    session = _Session(_Response(text=html))
    found = await extract_jpg5("https://jpg5.su/img/1", _as_session(session), _CTX)
    assert [f.url for f in found] == ["https://cdn.jpg5/button.jpg"]


async def test_jpg5_falls_back_to_a_plain_image_src() -> None:
    non_http = _jpg5_encrypt("not-a-url")  # a blob that does not decrypt to an http address
    # A download button with no href is skipped on the way to the plain src.
    html = (
        f'<a class="btn-download"></a><img data-src="{non_http}" src="https://cdn.jpg5/plain.jpg">'
    )
    session = _Session(_Response(text=html))
    found = await extract_jpg5("https://jpg5.su/img/1", _as_session(session), _CTX)
    assert [f.url for f in found] == ["https://cdn.jpg5/plain.jpg"]


async def test_jpg5_skips_a_lazy_image_with_an_empty_data_src() -> None:
    session = _Session(_Response(text='<img data-src="">'))
    assert await extract_jpg5("https://jpg5.su/img/1", _as_session(session), _CTX) == []


async def test_jpg5_ignores_a_script_that_is_not_the_obfuscated_one() -> None:
    session = _Session(_Response(text="<script>var page = 1;</script>"))
    assert await extract_jpg5("https://jpg5.su/img/1", _as_session(session), _CTX) == []


async def test_jpg5_ignores_an_obfuscated_script_that_decrypts_to_nothing() -> None:
    html = "<script>/* obfuscated */ var d = 'data-src=\"not-valid-base64!!\"';</script>"
    session = _Session(_Response(text=html))
    assert await extract_jpg5("https://jpg5.su/img/1", _as_session(session), _CTX) == []


async def test_jpg5_ignores_an_obfuscated_script_with_no_data_src() -> None:
    session = _Session(_Response(text="<script>/* obfuscated */ nothing here</script>"))
    assert await extract_jpg5("https://jpg5.su/img/1", _as_session(session), _CTX) == []


async def test_jpg5_yields_nothing_when_the_page_will_not_load() -> None:
    session = _Session(_Response(ok=False))
    assert await extract_jpg5("https://jpg5.su/img/1", _as_session(session), _CTX) == []


async def test_jpg5_yields_nothing_when_the_page_has_no_media() -> None:
    session = _Session(_Response(text="<html><body>nothing</body></html>"))
    assert await extract_jpg5("https://jpg5.su/img/1", _as_session(session), _CTX) == []


async def test_jpg5_ignores_a_lazy_image_whose_src_is_not_a_url() -> None:
    html = '<img data-src="ignored" src="/relative/not-http.jpg">'
    session = _Session(_Response(text=html))
    assert await extract_jpg5("https://jpg5.su/img/1", _as_session(session), _CTX) == []


# ------------------------------------------------------------------- PMVHaven


def test_find_pmvhaven_video_reads_the_nuxt_array_first() -> None:
    html = '<script id="__NUXT_DATA__">["x", "https://cdn.pmv/v.mp4", 1]</script>'
    assert find_pmvhaven_video(html) == "https://cdn.pmv/v.mp4"


def test_find_pmvhaven_video_falls_back_to_the_longest_html_match() -> None:
    html = "a https://cdn.pmv/short.mp4 b https://cdn.pmv/a-much-longer-one.mp4 c"
    assert find_pmvhaven_video(html) == "https://cdn.pmv/a-much-longer-one.mp4"


def test_find_pmvhaven_video_uses_the_fallback_when_the_nuxt_array_has_no_video() -> None:
    html = '<script id="__NUXT_DATA__">["nope", 1]</script> https://cdn.pmv/from-html.mp4'
    assert find_pmvhaven_video(html) == "https://cdn.pmv/from-html.mp4"


def test_find_pmvhaven_video_ignores_bad_or_non_list_nuxt_and_finds_nothing() -> None:
    assert find_pmvhaven_video('<script id="__NUXT_DATA__">nonsense</script>') is None
    assert find_pmvhaven_video('<script id="__NUXT_DATA__">{"a": 1}</script>') is None
    assert find_pmvhaven_video("<html>nothing here</html>") is None


async def test_pmvhaven_needs_a_video_path() -> None:
    session = _Session(_Response(text="<html></html>"))
    assert (
        await extract_pmvhaven("https://pmvhaven.com/profile/x", _as_session(session), _CTX) == []
    )


async def test_pmvhaven_reads_the_video_from_the_page() -> None:
    html = '<script id="__NUXT_DATA__">["https://cdn.pmv/v.mp4"]</script>'
    session = _Session(_Response(text=html))
    found = await extract_pmvhaven("https://pmvhaven.com/video/abc", _as_session(session), _CTX)
    assert [f.url for f in found] == ["https://cdn.pmv/v.mp4"]


async def test_a_pmvhaven_file_is_named_after_the_video_not_its_storage_name() -> None:
    """The file's name is the page's title, not the address it is stored at.

    PMVHaven stores a video as `<uploader>_-_<title>__<upload ms>_<random>.mp4`, and a file named
    after that and given "Creator - Name" would read like `someone - someone_-_A_title__
    1781234567890_4kq2vbxa.mp4`. The page's `<title>` less the site's own name is what the site
    calls the video; a page with no title keeps the storage name.
    """
    stored = "https://cdn.pmv/videos/Someone_-_A_Title__1781234567890_4kq2vbxa.mp4"
    html = (
        "<html><head><title>A Title. Part 2 - PMVHaven</title></head>"
        f'<script id="__NUXT_DATA__">["{stored}"]</script></html>'
    )
    session = _Session(_Response(text=html))
    found = await extract_pmvhaven("https://pmvhaven.com/video/abc", _as_session(session), _CTX)
    assert [(f.url, f.filename, f.title) for f in found] == [
        (stored, "A Title. Part 2.mp4", "A Title. Part 2")
    ]

    untitled = _Session(_Response(text=f'<script id="__NUXT_DATA__">["{stored}"]</script>'))
    found = await extract_pmvhaven("https://pmvhaven.com/video/abc", _as_session(untitled), _CTX)
    assert [(f.url, f.filename) for f in found] == [(stored, None)]


async def test_pmvhaven_yields_nothing_when_the_page_fails_or_has_no_video() -> None:
    fail = _Session(_Response(ok=False))
    assert await extract_pmvhaven("https://pmvhaven.com/video/a", _as_session(fail), _CTX) == []
    none = _Session(_Response(text="<html>no media</html>"))
    assert await extract_pmvhaven("https://pmvhaven.com/video/a", _as_session(none), _CTX) == []


# ------------------------------------------------------------------- TurboVid / Saint


def test_parse_sign_response_reads_the_url_and_a_name() -> None:
    assert parse_sign_response({"url": "https://c/s.mp4", "original_filename": "n.mp4"}) == (
        "https://c/s.mp4",
        "n.mp4",
    )
    assert parse_sign_response({"url": "https://c/s.mp4", "filename": "f.mp4"}) == (
        "https://c/s.mp4",
        "f.mp4",
    )
    assert parse_sign_response({"url": "https://c/s.mp4"}) == ("https://c/s.mp4", "")
    assert parse_sign_response("not-a-dict") is None
    assert parse_sign_response({"url": ""}) is None


async def test_turbovid_signs_a_single_file() -> None:
    session = _Router(
        {
            "/api/sign?v=abc": _Response(
                text='{"url": "https://c/s.mp4", "original_filename": "clip.mp4"}'
            )
        }
    )
    found = await extract_turbovid("https://turbovid.cr/d/abc", _as_session(session), _CTX)
    assert [(f.url, f.filename) for f in found] == [("https://c/s.mp4", "clip.mp4")]


async def test_turbovid_signs_on_the_host_the_address_was_pasted_from_first() -> None:
    """One domain can answer 521 to everything while another signs the same file at once, so
    signing only on the primary would fail every download pasted from the working one."""
    session = _Router(
        {"https://turbo.cr/api/sign?v=abc": _Response(text='{"url": "https://c/s.mp4"}')}
    )
    found = await extract_turbovid("https://turbo.cr/d/abc", _as_session(session), _CTX)
    assert [f.url for f in found] == ["https://c/s.mp4"]
    assert session.calls == ["https://turbo.cr/api/sign?v=abc"]
    assert turbovid_sign_origins("https://turbo.cr/d/abc") == [
        "https://turbo.cr",
        "https://turbovid.cr",
    ]


async def test_turbovid_falls_back_to_the_primary_when_the_pasted_host_is_down() -> None:
    session = _Router(
        {
            "https://saint2.su/api/sign": aiohttp.ClientConnectionError("down"),
            "https://turbovid.cr/api/sign?v=abc": _Response(text='{"url": "https://c/s.mp4"}'),
        }
    )
    found = await extract_turbovid("https://saint2.su/d/abc", _as_session(session), _CTX)
    assert [f.url for f in found] == ["https://c/s.mp4"]


async def test_turbovid_file_needs_an_id_and_a_working_sign() -> None:
    assert await extract_turbovid("https://turbovid.cr/d", _as_session(_Router({})), _CTX) == []
    no_sign = _Router({"/api/sign": _Response(ok=False)})
    assert await extract_turbovid("https://turbovid.cr/v/abc", _as_session(no_sign), _CTX) == []


async def test_turbovid_album_signs_each_row_and_skips_the_rest() -> None:
    html = (
        '<tbody id="fileTbody">'
        '<tr data-id="1"></tr><tr data-id="2"></tr>'
        '<tr data-id=""></tr>'  # empty id -> skipped
        '<tr data-id="9"></tr>'  # id whose sign fails -> dropped
        "</tbody>"
    )
    session = _Router(
        {
            "/a/set": _Response(text=html),
            "/api/sign?v=1": _Response(text='{"url": "https://c/1.mp4"}'),
            "/api/sign?v=2": _Response(text='{"url": "https://c/2.mp4"}'),
        }
    )
    found = await extract_turbovid("https://turbovid.cr/a/set", _as_session(session), _CTX)
    assert [f.url for f in found] == ["https://c/1.mp4", "https://c/2.mp4"]


async def test_turbovid_album_that_will_not_load_and_an_unknown_path_yield_nothing() -> None:
    no_page = _Router({})  # the /a page is not-ok
    assert await extract_turbovid("https://turbovid.cr/a/set", _as_session(no_page), _CTX) == []
    assert await extract_turbovid("https://turbovid.cr/x", _as_session(_Router({})), _CTX) == []


# ------------------------------------------------------------------- Cyberfile


def test_parse_cyberfile_details_reads_the_tokenised_url_and_name() -> None:
    html = "<a onclick=\"openUrl('https://cf.cdn/f/x.mp4?download_token=abc')\">"
    (found,) = parse_cyberfile_details(html)
    assert found.url == "https://cf.cdn/f/x.mp4?download_token=abc"
    assert found.filename == "x.mp4"
    assert parse_cyberfile_details("<div>no download link</div>") == []


async def test_cyberfile_resolves_through_the_details_ajax() -> None:
    detail = json.dumps(
        {"html": "<a onclick=\"openUrl('https://cf.cdn/f/clip.mp4?download_token=t')\"></a>"}
    )
    session = _Router(
        {
            "/file/xyz": _Response(text='onclick="showFileInformation(12345)"'),
            "/file_details": _Response(text=detail),
        }
    )
    found = await extract_cyberfile("https://cyberfile.me/file/xyz", _as_session(session), _CTX)
    assert [f.url for f in found] == ["https://cf.cdn/f/clip.mp4?download_token=t"]


async def test_cyberfile_yields_nothing_at_each_dead_end() -> None:
    no_id = _Router({"/file/xyz": _Response(text="<html>no id here</html>")})
    assert await extract_cyberfile("https://cyberfile.me/file/xyz", _as_session(no_id), _CTX) == []

    no_page = _Router({})  # the page itself is not-ok
    assert (
        await extract_cyberfile("https://cyberfile.me/file/xyz", _as_session(no_page), _CTX) == []
    )

    no_ajax = _Router({"/file/xyz": _Response(text="showFileInformation(1)")})  # ajax not-ok
    assert (
        await extract_cyberfile("https://cyberfile.me/file/xyz", _as_session(no_ajax), _CTX) == []
    )

    no_html = _Router(
        {
            "/file/xyz": _Response(text="showFileInformation(1)"),
            "/file_details": _Response(text="{}"),
        }
    )
    assert (
        await extract_cyberfile("https://cyberfile.me/file/xyz", _as_session(no_html), _CTX) == []
    )


async def test_turbovid_drops_a_file_whose_sign_response_names_no_url() -> None:
    session = _Router({"/api/sign?v=abc": _Response(text='{"error": "gone"}')})  # ok, but no url
    assert await extract_turbovid("https://turbovid.cr/d/abc", _as_session(session), _CTX) == []


# ------------------------------------------------------------------- Coomer / Kemono


def test_parse_coomer_post_uses_the_server_registry_and_the_fallback() -> None:
    data = {
        "post": {
            "file": {"path": "/a/b.jpg", "name": "b.jpg"},
            "attachments": [{"path": "/c/d.mp4"}],  # no server -> fallback
        },
        "previews": [{"path": "/a/b.jpg", "server": "https://cdn1"}],
    }
    found = parse_coomer_post(data, "https://coomer.st")
    assert [f.url for f in found] == [
        "https://cdn1/data/a/b.jpg?f=b.jpg",
        "https://coomer.st/data/c/d.mp4?f=d.mp4",
    ]


def test_parse_coomer_post_honours_an_inline_server_and_derives_a_name() -> None:
    data = {"post": {"file": {"path": "/x/y.jpg", "server": "https://inline"}}}
    (found,) = parse_coomer_post(data, "https://coomer.st")
    assert found.url == "https://inline/data/x/y.jpg?f=y.jpg"  # name derived from the path
    assert found.filename == "y.jpg"


def test_parse_coomer_post_reads_the_flat_shapes() -> None:
    as_list = parse_coomer_post([{"file": {"path": "/x.jpg"}}], "https://coomer.st")
    assert [f.url for f in as_list] == ["https://coomer.st/data/x.jpg?f=x.jpg"]
    as_single = parse_coomer_post({"file": {"path": "/y.jpg"}}, "https://coomer.st")
    assert [f.url for f in as_single] == ["https://coomer.st/data/y.jpg?f=y.jpg"]


def test_parse_coomer_post_skips_junk_posts_and_files() -> None:
    assert parse_coomer_post(["not-a-dict"], "https://coomer.st") == []
    assert parse_coomer_post({"post": {"file": {"no": "path"}}}, "https://coomer.st") == []
    assert parse_coomer_post({"post": {"file": "not-a-dict"}}, "https://coomer.st") == []


async def test_coomer_passes_a_direct_cdn_link_straight_through() -> None:
    session = _Session()
    url = "https://coomer.st/data/a/b.jpg"
    found = await extract_coomer(url, _as_session(session), _CTX)
    assert [f.url for f in found] == [url]
    assert session.calls == []  # a direct link needs no API call


async def test_coomer_reads_a_post_from_the_api() -> None:
    session = _Session(_Response(text='{"post": {"file": {"path": "/a.jpg"}}}'))
    found = await extract_coomer(
        "https://coomer.st/onlyfans/user/u/post/1", _as_session(session), _CTX
    )
    assert [f.url for f in found] == ["https://coomer.st/data/a.jpg?f=a.jpg"]


async def test_coomer_sends_the_text_css_accept_and_the_saved_session_cookie(
    tmp_path: Path,
) -> None:
    cookies = tmp_path / "c.txt"
    cookies.write_text("\t".join([".coomer.st", "TRUE", "/", "TRUE", "0", "session", "aaa"]) + "\n")
    ctx = ExtractContext(user_agent="ua", cookies_file=cookies)
    seen: dict[str, str] = {}

    class _Capturing:
        timeout = DEFAULT_TIMEOUT

        def get(
            self,
            _url: str,
            *,
            headers: dict[str, str],
            timeout: object,
            allow_redirects: bool = True,
        ) -> _Response:
            seen.update(headers)
            seen["_allow_redirects"] = str(allow_redirects)
            return _Response(text='{"post": {"file": {"path": "/a.jpg"}}}')

    found = await extract_coomer(
        "https://coomer.st/onlyfans/user/u/post/1",
        cast("aiohttp.ClientSession", _Capturing()),
        ctx,
    )
    assert found  # the post resolved
    assert seen["Accept"] == "text/css"
    assert seen["Cookie"] == "session=aaa"
    # The request carries a saved session cookie, so it must not follow a redirect: that would
    # carry the cookie on to wherever the redirect pointed.
    assert seen["_allow_redirects"] == "False"


async def test_coomer_yields_nothing_for_a_non_post_path_or_a_failed_api() -> None:
    non_post = _Session()
    assert (
        await extract_coomer("https://coomer.st/onlyfans/user/u", _as_session(non_post), _CTX) == []
    )
    failed = _Session(_Response(ok=False))
    assert (
        await extract_coomer("https://coomer.st/onlyfans/user/u/post/1", _as_session(failed), _CTX)
        == []
    )


async def test_kemono_passes_a_thumbnail_link_through() -> None:
    session = _Session()
    url = "https://kemono.cr/thumbnail/data/x.jpg"
    found = await extract_kemono(url, _as_session(session), _CTX)
    assert [f.url for f in found] == [url]


def test_parse_coomer_post_server_registry_skips_bad_entries() -> None:
    data = {
        "post": {"file": {"path": "/a/b.jpg"}},
        "previews": ["not-a-dict", {"path": "/a/b.jpg"}],  # non-dict, then a dict with no server
    }
    (found,) = parse_coomer_post(data, "https://coomer.st")
    assert found.url == "https://coomer.st/data/a/b.jpg?f=b.jpg"  # no server found -> fallback base


# ------------------------------------------------------------------- Bunkr

_JSCDN = '<script>var jsCDN = "https://cdn.bunkr/get/x.mp4";</script>'
_SIGN_OK = _Response(text='{"token": "TT", "ex": 9}')


def test_bunkr_js_vars_reads_the_jscdn_variable() -> None:
    soup = _soup('<script>var jsCDN = "https:\\/\\/cdn\\/x.mp4"; var n = 1;</script>')
    assert _bunkr_js_vars(soup) == {"jsCDN": "https://cdn/x.mp4", "n": "1"}


def test_bunkr_js_vars_is_empty_without_the_jscdn_script() -> None:
    assert _bunkr_js_vars(_soup("<html><script>var other = 2;</script></html>")) == {}


def test_bunkr_album_files_parses_the_js_object_array() -> None:
    html = '<script>window.albumFiles = [ { slug: "a", name: "a.jpg" }, { slug: "b" } ];</script>'
    assert [entry.get("slug") for entry in _bunkr_album_files(_soup(html))] == ["a", "b"]


def test_bunkr_album_files_empty_cases() -> None:
    assert _bunkr_album_files(_soup("<html></html>")) == []  # no scripts at all
    assert _bunkr_album_files(_soup("<script>var unrelated = 1;</script>")) == []  # other script
    assert (
        _bunkr_album_files(_soup("<script>window.albumFiles = broken</script>")) == []
    )  # no array
    assert (
        _bunkr_album_files(_soup("<script>window.albumFiles = [ oops ];</script>")) == []
    )  # bad json


async def test_bunkr_sign_reads_a_token_and_ex() -> None:
    assert await _bunkr_sign(_as_session(_Session(_SIGN_OK)), "/p") == ("TT", 9)


async def test_bunkr_sign_returns_none_on_every_failure() -> None:
    assert await _bunkr_sign(_as_session(_Session(_Response(ok=False))), "/p") is None
    bad_type = _Session(_Response(text='{"token": "T", "ex": "not-int"}'))
    assert await _bunkr_sign(_as_session(bad_type), "/p") is None
    bad_json = _Session(_Response(text="not json"))
    assert await _bunkr_sign(_as_session(bad_json), "/p") is None
    raising = _Router({"": aiohttp.ClientError("blocked")})
    assert await _bunkr_sign(_as_session(raising), "/p") is None


async def test_bunkr_api_download_builds_the_cdn_url() -> None:
    session = _Session(
        _Response(text='{"mediafiles": "https://m", "path": "/x.mp4", "original": "o.mp4"}')
    )
    assert await _bunkr_api_download(_as_session(session), "id") == ("https://m/x.mp4", "o.mp4")


async def test_bunkr_api_download_prepends_a_missing_slash_and_drops_a_bad_original() -> None:
    session = _Session(
        _Response(text='{"mediafiles": "https://m/", "path": "x.mp4", "original": 5}')
    )
    assert await _bunkr_api_download(_as_session(session), "id") == ("https://m/x.mp4", None)


async def test_bunkr_api_download_returns_none_on_failure_or_bad_shape() -> None:
    assert await _bunkr_api_download(_as_session(_Session(_Response(ok=False))), "id") is None
    bad = _Session(_Response(text='{"mediafiles": 1, "path": "/x"}'))
    assert await _bunkr_api_download(_as_session(bad), "id") is None
    raising = _Router({"": aiohttp.ClientError("blocked")})
    assert await _bunkr_api_download(_as_session(raising), "id") is None


async def test_bunkr_file_resolves_via_the_jscdn_var_and_the_og_title() -> None:
    page = '<meta property="og:title" content="Clip">' + _JSCDN
    session = _Router(
        {"bunkr.cr/f/slug": _Response(text=page), "glb-apisign.cdn.cr/sign": _SIGN_OK}
    )
    (found,) = await extract_bunkr("https://bunkr.cr/f/slug", _as_session(session), _CTX)
    assert "token=TT" in found.url and "ex=9" in found.url
    assert found.filename == "Clip"


async def test_bunkr_file_resolves_via_the_download_api_when_only_a_button_is_present() -> None:
    page = '<a class="btn ic-download-01" href="/f/realid"></a>'
    session = _Router(
        {
            "bunkr.cr/f/slug": _Response(text=page),
            "_001_v2": _Response(
                text='{"mediafiles": "https://m", "path": "/x.mp4", "original": "o.mp4"}'
            ),
            "glb-apisign.cdn.cr/sign": _Response(text='{"token": "T", "ex": 1}'),
        }
    )
    (found,) = await extract_bunkr("https://bunkr.cr/f/slug", _as_session(session), _CTX)
    assert found.filename == "o.mp4"
    assert found.url.startswith("https://m/x.mp4?")


async def test_bunkr_retries_across_mirror_hosts_and_skips_one_that_raises() -> None:
    session = _Router(
        {
            "bunkr.cr/f/slug": aiohttp.ClientError("blocked"),  # primary raises
            "bunkr.site/f/slug": _Response(text=_JSCDN),  # the mirror works
            "glb-apisign.cdn.cr/sign": _SIGN_OK,
        }
    )
    found = await extract_bunkr("https://bunkr.cr/f/slug", _as_session(session), _CTX)
    assert len(found) == 1


async def test_bunkr_file_dead_ends_yield_nothing() -> None:
    maint = _Router({"bunkr.cr/f/slug": _Response(text="Server under maintenance")})
    assert await extract_bunkr("https://bunkr.cr/f/slug", _as_session(maint), _CTX) == []

    all_fail = _Router({})  # every host not-ok -> the page never loads
    assert await extract_bunkr("https://bunkr.cr/f/slug", _as_session(all_fail), _CTX) == []

    no_media = _Router({"bunkr.cr/f/slug": _Response(text="<html>nothing</html>")})
    assert await extract_bunkr("https://bunkr.cr/f/slug", _as_session(no_media), _CTX) == []

    button_no_api = _Router(
        {"bunkr.cr/f/slug": _Response(text='<a class="btn ic-download-01" href="/f/id"></a>')}
    )
    assert await extract_bunkr("https://bunkr.cr/f/slug", _as_session(button_no_api), _CTX) == []

    jscdn_no_sign = _Router({"bunkr.cr/f/slug": _Response(text=_JSCDN)})  # sign not routed
    assert await extract_bunkr("https://bunkr.cr/f/slug", _as_session(jscdn_no_sign), _CTX) == []


async def test_bunkr_album_resolves_each_listed_file_and_skips_a_slugless_entry() -> None:
    album = (
        '<script>window.albumFiles = [ { slug: "one" }, { slug: "two" }, { name: "x" } ];</script>'
    )
    session = _Router(
        {
            "bunkr.cr/a/set": _Response(text=album),
            "bunkr.cr/f/one": _Response(text=_JSCDN),
            "bunkr.cr/f/two": _Response(text=_JSCDN),
            "glb-apisign.cdn.cr/sign": _SIGN_OK,
        }
    )
    found = await extract_bunkr("https://bunkr.cr/a/set", _as_session(session), _CTX)
    assert len(found) == 2  # the entry with no slug is skipped


async def test_bunkr_album_that_will_not_load_yields_nothing() -> None:
    assert await extract_bunkr("https://bunkr.cr/a/set", _as_session(_Router({})), _CTX) == []


async def test_bunkr_internal_download_link_goes_straight_to_the_api() -> None:
    session = _Router(
        {
            "_001_v2": _Response(text='{"mediafiles": "https://m", "path": "/x.mp4"}'),
            "glb-apisign.cdn.cr/sign": _SIGN_OK,
        }
    )
    found = await extract_bunkr("https://get.bunkrr.su/file/theid", _as_session(session), _CTX)
    assert len(found) == 1


async def test_bunkr_stream_aliases_and_a_bare_slug_are_treated_as_files() -> None:
    session = _Router(
        {
            "bunkr.cr/f/slug": _Response(text=_JSCDN),
            "bunkr.cr/f/bare": _Response(text=_JSCDN),
            "glb-apisign.cdn.cr/sign": _SIGN_OK,
        }
    )
    alias = await extract_bunkr("https://bunkr.cr/v/slug", _as_session(session), _CTX)
    assert len(alias) == 1
    bare = await extract_bunkr("https://bunkr.cr/bare", _as_session(session), _CTX)
    assert len(bare) == 1


async def test_bunkr_empty_and_unknown_paths_yield_nothing() -> None:
    assert await extract_bunkr("https://bunkr.cr/", _as_session(_Router({})), _CTX) == []
    assert await extract_bunkr("https://bunkr.cr/x/y", _as_session(_Router({})), _CTX) == []


# ------------------------------------------------------------------- HQporner
#
# The two pages below are the site's own markup, cut to the parts the reader touches, with the
# title, the addresses and the viewer address in the player's script invented.

_HQP_URL = "https://hqporner.com/hdporn/4321-lighthouse_at_dusk.html"

_HQP_PAGE = """<!DOCTYPE html><html><head>
<title>Lighthouse at dusk - HQporner.com</title>
</head><body>
<script type="text/javascript">
function altPlayer() {
$.ajax({
url: '/blocks/altplayer.php?i=//player.example/video/0a1b2c3d4e5f/',
cache: false
});
}
</script>
<div class="videoWrapper" id="playerWrapper" style="background:#000;">
<h3 style="padding: 20px 30px;">Loading may take some time ...</h3>
<iframe width="560" height="350" src="//player.example/video/0a1b2c3d4e5f/" frameborder="0"
 allowfullscreen></iframe>
</div>
<header><h1 class="main-h1" style="line-height: 1em;">
lighthouse at dusk</h1></header>
</body></html>"""

# The player writes one of two `<video>` tags from a script: the adblock branch names only the
# 360, the other the whole ladder, here with the 4K rung a video marked 4K carries.
_HQP_PLAYER = r"""<html><body><script>function do_pl(){ if(hasAdblock){$("#jw").html("<video
 id=\"flvv\" controls src=\"\"><source src=\"//s1.cdn.example/pubs/a1.b2/360.mp4\"
 title=\"360p\" type=\"video/mp4\" /></video>"); }else{ $("#jw").html("<video id=\"flvv\"
 controls src=\"\"><source src=\"//s1.cdn.example/pubs/a1.b2/360.mp4\" title=\"360p\"
 type=\"video/mp4\" /><source src=\"//s1.cdn.example/pubs/a1.b2/720.mp4\" title=\"720p60\"
 type=\"video/mp4\" /><source src=\"//s1.cdn.example/pubs/a1.b2/2160.mp4\"
 title=\"2160p60\" type=\"video/mp4\" /><source src=\"//s1.cdn.example/pubs/a1.b2/1080.mp4\"
 title=\"1080p60\" type=\"video/mp4\" /></video>");} }; do_pl();
$.post("https://stat.player.example/e.php", { r:"203.0.113.7", f:"hqporner.com" });</script>
</body></html><!-- 198.51.100.9 || hqporner.com -->"""


class _Heard(_Router):
    """A router that also keeps the headers each request carried."""

    def __init__(self, routes: dict[str, _Response | Exception]) -> None:
        super().__init__(routes)
        self.headers: list[dict[str, str]] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.headers.append(dict(kwargs.get("headers") or {}))
        return super().get(url, **kwargs)


async def test_a_hqporner_video_takes_the_rung_video_quality_asks_for_named_after_it() -> None:
    """ "Best compatible" (the default) takes 1080p off a ladder with a 4K rung; "Best available"
    takes the 4K one."""
    from dataclasses import replace

    from sift.slices.download.sources.tuning import QUALITY_BEST

    tallest = _Heard(
        {
            "hqporner.com/hdporn/": _Response(text=_HQP_PAGE),
            "player.example": _Response(text=_HQP_PLAYER),
        }
    )
    best = await extract_hqporner(
        _HQP_URL, _as_session(tallest), replace(_CTX, quality=QUALITY_BEST)
    )
    assert [f.url for f in best] == ["https://s1.cdn.example/pubs/a1.b2/2160.mp4"]

    session = _Heard(
        {
            "hqporner.com/hdporn/": _Response(text=_HQP_PAGE),
            "player.example": _Response(text=_HQP_PLAYER),
        }
    )
    found = await extract_hqporner(_HQP_URL, _as_session(session), _CTX)
    assert [(f.url, f.filename) for f in found] == [
        ("https://s1.cdn.example/pubs/a1.b2/1080.mp4", "Lighthouse at dusk.mp4")
    ]
    assert [f.title for f in found] == ["Lighthouse at dusk"]  # the `{title}` word
    assert session.calls == [_HQP_URL, "https://player.example/video/0a1b2c3d4e5f/"]


async def test_the_hqporner_player_is_asked_with_the_video_page_as_referer() -> None:
    """Asked with no Referer the player host answers 200 with "This domain has been blocked" and
    no sources, which no status could explain."""
    session = _Heard(
        {
            "hqporner.com/hdporn/": _Response(text=_HQP_PAGE),
            "player.example": _Response(text=_HQP_PLAYER),
        }
    )
    await extract_hqporner(_HQP_URL, _as_session(session), _CTX)
    assert session.headers[1] == {"Referer": _HQP_URL}


async def test_a_hqporner_page_with_no_player_yields_nothing_and_asks_no_further() -> None:
    session = _Router(
        {"hqporner.com": _Response(text="<html><title>x</title><body></body></html>")}
    )
    assert await extract_hqporner(_HQP_URL, _as_session(session), _CTX) == []
    assert session.calls == [_HQP_URL]


async def test_a_hqporner_player_that_refuses_the_referer_yields_nothing() -> None:
    blocked = "<html><body><td>This domain has been blocked</td></body></html>"
    session = _Router(
        {
            "hqporner.com/hdporn/": _Response(text=_HQP_PAGE),
            "player.example": _Response(text=blocked),
        }
    )
    assert await extract_hqporner(_HQP_URL, _as_session(session), _CTX) == []


async def test_a_hqporner_region_refusal_yields_nothing_and_says_a_tunnel_would_help() -> None:
    """The page itself refused (a 451 is the site withholding it where the request came from):
    nothing more is asked, and the empty result says the code and that a tunnel is the way out."""
    session = _Router({"hqporner.com": _Response(ok=False)})
    assert await extract_hqporner(_HQP_URL, _as_session(session), _CTX) == []
    assert session.calls == [_HQP_URL]
    answers = Answers()
    params: Any = SimpleNamespace(response=SimpleNamespace(status=451))
    await answers._ended(cast("aiohttp.ClientSession", None), SimpleNamespace(), params)
    failure = answers.why_nothing(_HQP_URL, "HQporner")
    assert failure.code == "http-451"
    assert failure.a_tunnel_would_help is True
    assert str(failure).startswith("HQporner answered 451")


async def test_only_a_hqporner_video_page_is_read() -> None:
    session = _Router({})
    for url in (
        "https://hqporner.com/",
        "https://hqporner.com/hdporn/2",
        "https://hqporner.com/actress/some-name",
    ):
        assert await extract_hqporner(url, _as_session(session), _CTX) == []
    assert session.calls == []


def test_hqporner_reads_the_player_from_the_alternative_player_script_without_an_iframe() -> None:
    page = _HQP_PAGE.replace("<iframe", "<!-- <iframe").replace("</iframe>", "</iframe> -->")
    assert hqporner_player(page, _HQP_URL) == "https://player.example/video/0a1b2c3d4e5f/"
    assert hqporner_player("<html></html>", _HQP_URL) is None


def test_hqporner_ranks_a_source_by_its_file_name_when_the_label_says_no_height() -> None:
    player = (
        '<source src="//c.example/p/360.mp4" title="low" />'
        '<source src="//c.example/p/720.mp4" title="HD" />'
    )
    assert (
        hqporner_best_source(player, "https://player.example/v/") == "https://c.example/p/720.mp4"
    )
    assert hqporner_best_source("<html></html>", "https://player.example/v/") is None


def test_a_hqporner_source_that_is_not_a_web_address_is_never_chosen() -> None:
    """Only an http(s) address is fetched, however tall the rung that names something else says it
    is."""
    player = (
        '<source src="data:video/mp4;base64,AAAA" title="2160p" />'
        '<source src="//c.example/p/720.mp4" title="720p" />'
    )
    assert (
        hqporner_best_source(player, "https://player.example/v/") == "https://c.example/p/720.mp4"
    )


def test_a_hqporner_title_that_is_only_the_sites_name_falls_back_to_the_heading() -> None:
    heading = '<h1 class="main-h1">\nlighthouse at dusk</h1>'
    assert (
        hqporner_title(f"<html><head><title> - HQporner.com</title></head><body>{heading}</body>")
        == "lighthouse at dusk"
    )
    # Neither says anything: no name rather than an empty one.
    assert hqporner_title("<html><body></body></html>") is None


async def test_a_hqporner_player_page_that_is_refused_yields_nothing() -> None:
    session = _Router(
        {
            "hqporner.com/hdporn/": _Response(text=_HQP_PAGE),
            "player.example": _Response(ok=False),
        }
    )
    assert await extract_hqporner(_HQP_URL, _as_session(session), _CTX) == []
    assert session.calls == [_HQP_URL, "https://player.example/video/0a1b2c3d4e5f/"]


def test_hqporner_addresses_are_read_by_sifts_own_extractor() -> None:
    """No tool reads the Site ("Unsupported URL"), so the record carrying this
    reader is the whole of how a HQporner address downloads at all."""
    from sift.slices.download.sources import sites

    record = sites.match_site(_HQP_URL)
    assert record is not None
    assert record.extract is extract_hqporner


async def test_a_downloads_connection_timeout_reaches_every_page_read() -> None:
    """The readers' reads are a download's requests like the file's own,
    so the connection timeout setting is their budget to connect and between reads, inside their
    own ceiling, which a longer setting raises rather than cuts short."""
    from dataclasses import replace

    from sift.slices.download.sources.net import policy_timeout
    from sift.slices.download.sources.sites.extractors import _within
    from sift.slices.download.sources.tuning import POLICY, RunPolicy

    def session_for(seconds: float) -> aiohttp.ClientSession:
        pacing = replace(POLICY.pacing, timeout_seconds=seconds)
        return cast(
            "aiohttp.ClientSession",
            SimpleNamespace(timeout=policy_timeout(RunPolicy(pacing=pacing))),
        )

    short = _within(session_for(7.0), 15.0)
    assert (short.sock_connect, short.sock_read, short.total) == (7.0, 7.0, 25.0)
    long = _within(session_for(90.0), 15.0)
    assert (long.sock_read, long.total) == (90.0, 100.0)
    # A session with no download's settings keeps the ceiling alone.
    bare = _within(cast("aiohttp.ClientSession", _Session()), 15.0)
    assert (bare.total, bare.sock_connect, bare.sock_read) == (15.0, None, None)
