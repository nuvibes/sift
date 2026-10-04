# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a saved login at the moment it is saved, rather than leaving the format to the tools."""

from __future__ import annotations

import time

import pytest

from sift.slices.download.sources.cookies import (
    NETSCAPE_HEADER,
    CookieInvalid,
    header_for,
    understand,
)

_FAR_FUTURE = int(time.time()) + 30 * 24 * 60 * 60
_LONG_PAST = 1000000


def _exported(*rows: tuple[str, str, int]) -> str:
    lines = [NETSCAPE_HEADER]
    lines += [
        "\t".join([".instagram.com", "TRUE", "/", "TRUE", str(expiry), name, value])
        for name, value, expiry in rows
    ]
    return "\n".join(lines) + "\n"


async def test_an_exported_file_is_read_and_described() -> None:
    saved, summary = await understand(
        _exported(("sessionid", "abc", _FAR_FUTURE), ("csrftoken", "xyz", _FAR_FUTURE + 60))
    )
    assert saved.startswith(NETSCAPE_HEADER)
    assert summary.count == 2
    assert summary.domains == ("instagram.com",)
    assert summary.expires_at == _FAR_FUTURE  # the FIRST one to run out, not the last
    assert summary.expired is False


async def test_the_short_form_a_browser_hands_out_is_converted_rather_than_refused() -> None:
    """It is what most people will try first, and it is a few lines to turn into the real thing.
    Refusing it would be the single most likely way to fail at saving a working login."""
    saved, summary = await understand("sessionid=abc; csrftoken=xyz", domain="instagram.com")

    assert saved.startswith(NETSCAPE_HEADER)
    assert "sessionid\tabc" in saved  # the seven tab-separated fields the tools read
    assert summary.count == 2
    assert summary.domains == ("instagram.com",)
    assert summary.expires_at is not None  # the form carries no date, so one is chosen


async def test_the_short_form_is_refused_when_the_site_is_not_known() -> None:
    """A cookie file says which site each cookie is for. Inventing that would build a file the tools
    read happily and send to nobody, which is worse than refusing."""
    with pytest.raises(CookieInvalid) as refusal:
        await understand("sessionid=abc; csrftoken=xyz", domain=None)
    assert "which site" in str(refusal.value)


async def test_a_login_that_is_already_dead_is_recognized_at_the_moment_it_is_pasted() -> None:
    """The standard parser drops expired cookies as it reads, so read naively a dead login comes
    back as a smaller healthy one. It is read with that turned off and judged here instead."""
    _, summary = await understand(_exported(("sessionid", "abc", _LONG_PAST)))
    assert summary.count == 1  # not silently dropped
    assert summary.expired is True


async def test_a_login_made_of_session_cookies_gets_a_count_and_no_date() -> None:
    """A session cookie carries no expiry at all. Saying one has expired, or naming a date for it,
    would both be inventing a fact, so the description simply stops short."""
    _, summary = await understand(_exported(("sessionid", "abc", 0)))
    assert summary.count == 1
    assert summary.expires_at is None
    assert summary.expired is False


@pytest.mark.parametrize("junk", ["", "   ", "not a login at all", '{"sessionid": "abc"}'])
async def test_something_that_is_neither_form_is_refused_with_a_sentence(junk: str) -> None:
    with pytest.raises(CookieInvalid) as refusal:
        await understand(junk, domain="instagram.com")
    assert str(refusal.value)  # a sentence for a person, not an empty exception


async def test_a_file_with_no_cookies_in_it_is_refused() -> None:
    """Copying only the top of the file is an easy miss, and it parses perfectly: it is simply a
    login with nothing in it. Stored, it would look saved and log in to nothing."""
    with pytest.raises(CookieInvalid):
        await understand(f"{NETSCAPE_HEADER}\n# This is a generated file. Do not edit.\n")


async def test_a_pasted_file_missing_its_first_line_is_still_read() -> None:
    """One of the tools refuses outright without that line, so it is added rather than made a
    person's problem: selecting the whole of a file including its comments is an easy miss."""
    body = "\t".join([".instagram.com", "TRUE", "/", "TRUE", str(_FAR_FUTURE), "sid", "abc"])
    saved, summary = await understand(body)
    assert saved.startswith(NETSCAPE_HEADER)
    assert summary.count == 1


def test_the_header_for_a_host_carries_that_sites_cookies_and_no_other() -> None:
    jar = "\n".join(
        [
            NETSCAPE_HEADER,
            "\t".join([".instagram.com", "TRUE", "/", "TRUE", str(_FAR_FUTURE), "sessionid", "a"]),
            "\t".join([".example.com", "TRUE", "/", "TRUE", str(_FAR_FUTURE), "other", "b"]),
        ]
    )
    assert header_for(jar + "\n", "www.Instagram.com") == "sessionid=a"
    assert header_for(jar + "\n", "nowhere.test") == ""
