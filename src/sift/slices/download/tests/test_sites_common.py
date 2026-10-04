# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shared helpers behind the site extractors: host matching, the CDN referer, and the turning of
an extractor's finds into ResolvedMedia."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from sift.slices.download.sources.errors import NothingFound
from sift.slices.download.sources.sites.common import (
    ExtractedFile,
    build_resolved_media,
    decrypt_jpg5_xor,
    guess_type_and_ext,
    host_matches,
    load_cookie_jar,
    load_session_cookie,
    source_host,
    source_origin,
)

_JPG5_KEY = b"seltilovessimpcity@simpcityhatesscrapers"


def _jpg5_encrypt(url: str) -> str:
    """The inverse of decrypt_jpg5_xor: XOR with the key, hex, then base64, to make test input."""
    xored = bytes(byte ^ _JPG5_KEY[i % len(_JPG5_KEY)] for i, byte in enumerate(url.encode()))
    return base64.b64encode(xored.hex().encode()).decode()


def test_host_matches_is_suffix_exact_not_substring() -> None:
    assert host_matches("bunkr.cr", ("bunkr.cr",))
    assert host_matches("cdn.bunkr.cr", ("bunkr.cr",))  # a subdomain
    assert not host_matches("evil-bunkr.cr", ("bunkr.cr",))  # not a label boundary
    assert not host_matches("bunkr.cr.attacker.com", ("bunkr.cr",))


def test_source_host_lowercases() -> None:
    assert source_host("https://Cdn.Bunkr.CR/a/b") == "cdn.bunkr.cr"


def test_source_origin_is_scheme_and_host() -> None:
    assert source_origin("https://bunkr.cr/a/b?c=d") == "https://bunkr.cr/"


@pytest.mark.parametrize(
    ("url", "filename", "expected"),
    [
        ("https://cdn/x.mp4", None, ("video", ".mp4")),
        ("https://cdn/x.JPG", None, ("image", ".jpg")),
        ("https://cdn/api/file/abc", "clip.webm", ("video", ".webm")),  # name wins over path
        ("https://cdn/api/file/abc", None, ("other", "")),  # nothing to go on
        ("https://cdn/x.bin", None, ("other", "")),  # an unknown extension
    ],
)
def test_guess_type_and_ext(url: str, filename: str | None, expected: tuple[str, str]) -> None:
    assert guess_type_and_ext(url, filename) == expected


def test_build_resolved_media_carries_the_name_cookie_and_referer() -> None:
    media = build_resolved_media(
        "https://bunkr.cr/a/xyz",
        "Bunkr",
        [ExtractedFile("https://cdn/x.mp4", filename="clip.mp4", cookie="token=1")],
        referer="https://bunkr.cr/",
    )
    assert media.site == "Bunkr"
    assert media.source_host == "bunkr.cr"
    (item,) = media.items
    assert item.backend == "direct"
    assert item.url == "https://cdn/x.mp4"
    assert item.filename == "clip.mp4"
    assert item.cookie == "token=1"
    assert item.referer == "https://bunkr.cr/"
    assert item.index == 0


def test_the_title_a_page_shows_is_the_posts_title() -> None:
    titled = [ExtractedFile("https://cdn/x.mp4"), ExtractedFile("https://cdn/y.mp4", title="Dusk")]
    media = build_resolved_media(
        "https://hqporner.com/hdporn/1-a.html", "HQporner", titled, referer=""
    )
    assert media.title == "Dusk"
    untitled = [ExtractedFile("https://cdn/x.mp4")]
    assert build_resolved_media("https://a.example/", "A", untitled, referer="").title is None


def test_build_resolved_media_skips_empty_urls_and_numbers_what_remains() -> None:
    media = build_resolved_media(
        "https://s/a",
        "Site",
        [ExtractedFile(""), ExtractedFile("https://cdn/1.jpg"), ExtractedFile("https://cdn/2.jpg")],
        referer="https://s/",
    )
    assert [item.index for item in media.items] == [0, 1]
    assert [item.url for item in media.items] == ["https://cdn/1.jpg", "https://cdn/2.jpg"]


def test_build_resolved_media_with_nothing_is_nothing_found() -> None:
    with pytest.raises(NothingFound):
        build_resolved_media("https://s/a", "Site", [ExtractedFile("")], referer="https://s/")


def test_decrypt_jpg5_xor_round_trips_a_url_and_fails_softly() -> None:
    url = "https://simp.jpg5.su/images/real.jpg"
    assert decrypt_jpg5_xor(_jpg5_encrypt(url)) == url
    assert decrypt_jpg5_xor("eno=") == ""  # decodes from base64 but is not valid hex
    assert decrypt_jpg5_xor("//4=") == ""  # decodes to bytes that are not UTF-8


def _netscape_line(domain: str, name: str, value: str) -> str:
    # domain, include-subdomains flag, path, secure, expiry, name, value: tab separated.
    return "\t".join([domain, "TRUE", "/", "TRUE", "0", name, value])


def test_load_cookie_jar_reads_a_netscape_file_for_the_domain(tmp_path: Path) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text(
        "\n".join(
            [
                "# Netscape HTTP Cookie File",
                "#HttpOnly_" + _netscape_line(".coomer.st", "session", "aaa"),
                _netscape_line(".coomer.st", "theme", "dark"),
                _netscape_line("other.com", "session", "zzz"),  # a different domain
                "malformed line without enough tabs",
                "",
            ]
        )
    )
    assert load_cookie_jar(cookies, "coomer.st") == {"session": "aaa", "theme": "dark"}
    assert load_session_cookie(cookies, "coomer.st") == "aaa"


def test_load_cookie_jar_is_empty_without_a_readable_file(tmp_path: Path) -> None:
    assert load_cookie_jar(None, "coomer.st") == {}
    assert load_session_cookie(None, "coomer.st") is None
    assert load_cookie_jar(tmp_path / "missing.txt", "coomer.st") == {}  # OSError on read
