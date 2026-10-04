# SPDX-License-Identifier: AGPL-3.0-or-later
"""The command lines the tools are run with, pinned. A flag that moves is a flag a test catches."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from sift.kernel.public_net import TOOL_PROXY, Traffic
from sift.slices.download.sources.argv import (
    PROGRESS_TEMPLATE,
    TOOL_FACTS_FILE,
    TOOL_FACTS_TEMPLATE,
    build_enumerate_argv,
    build_gallerydl_argv,
    build_ytdlp_argv,
    proxy_traffic,
)
from sift.slices.download.sources.tuning import (
    DEFAULT_USER_AGENT,
    GALLERYDL_BINARY,
    JS_RUNTIME,
    PACING,
    YTDLP_BINARY,
)

_DEST = Path("/work/downloads")
_COOKIES = Path("/work/c.txt")

#: The paths as the argument lists carry them. How an absolute path is spelled is a property of the
#: site, and written out by hand these are commands nobody would run on Windows.
_DEST_ARG = str(_DEST)
#: Both fields are capped in BYTES, and the id's cap is the one that matters on Windows.
#:
#: A plain file URL has no extractor, so yt-dlp's "id" is the filename stem. A long stem in both
#: fields can make a 272-character path against a 260-character limit, reported as "No such file
#: or directory", which reads as a missing folder.
_TEMPLATE_ARG = str(_DEST / "%(title).80B [%(id).40B].%(ext)s")
_COOKIES_ARG = str(_COOKIES)
_URL = "https://example.com/watch/123"
_TUNNEL = "http://127.0.0.1:45000"


def _proxy_of(command: list[str]) -> str:
    return command[command.index("--proxy") + 1]


def test_ytdlp_argv_is_exactly_this() -> None:
    command = build_ytdlp_argv(_URL, _DEST)
    assert command == [
        YTDLP_BINARY,
        "--output",
        _TEMPLATE_ARG,
        "--no-playlist",
        "--no-warnings",
        # Progress, on stdout, in a shape Sift chose, and one line per update rather than one
        # line rewritten in place, which a reader splitting on newlines never sees the end of.
        "--newline",
        "--progress-template",
        PROGRESS_TEMPLATE,
        # What the tool knew about each file it finished (the naming words' facts), written
        # into the tool's own directory, where the listing of its output passes it by.
        "--print-to-file",
        TOOL_FACTS_TEMPLATE,
        str(_DEST / TOOL_FACTS_FILE),
        "--js-runtimes",
        JS_RUNTIME,
        "-S",
        "vcodec:h264,ext:mp4",
        "--restrict-filenames",
        # Continue a part-file this job already has, which is what pausing a download leaves in the
        # workspace. It is the tool's default and it is passed anyway: the pause feature depends on
        # it, and a default nothing states is one a later release of somebody else's tool can
        # change with nothing here noticing.
        "--continue",
        "--user-agent",
        DEFAULT_USER_AGENT,
        # How hard to lean on the site. Sift decides these and the tool applies them, because a
        # request made inside a running tool is not one Sift can slow down from outside.
        "--sleep-requests",
        str(PACING.seconds_between_requests),
        # And before each file: the option above spaces only the tool's reads of the page.
        "--sleep-interval",
        str(PACING.seconds_between_requests),
        "--retries",
        str(PACING.retries),
        "--socket-timeout",
        str(PACING.timeout_seconds),
        # Every connection the tool opens goes through the tool proxy, which refuses this device
        # and its private network. Its address is pinned by the tests below.
        "--proxy",
        _proxy_of(command),
        "--",
        _URL,
    ]


def test_gallerydl_argv_is_exactly_this() -> None:
    command = build_gallerydl_argv(_URL, _DEST)
    assert command == [
        GALLERYDL_BINARY,
        "--directory",
        _DEST_ARG,
        # Not quiet. This tool has no progress template to ask for, so the one thing it can report
        # is which file it is on, which it prints, and which quiet would suppress.
        "--user-agent",
        DEFAULT_USER_AGENT,
        "--sleep-request",
        str(PACING.seconds_between_requests),
        "--sleep",
        str(PACING.seconds_between_requests),
        "--retries",
        str(PACING.retries),
        "--http-timeout",
        str(PACING.timeout_seconds),
        "--sleep-429",
        str(PACING.wait_after_too_many_requests),
        "--proxy",
        _proxy_of(command),
        "--",
        _URL,
    ]


def test_nothing_built_here_stops_a_paused_download_being_continued() -> None:
    """The other half of resuming, which is an ABSENCE and so needs saying out loud.

    yt-dlp continues a part-file and gallery-dl skips a file already there, both by default, and
    three options would turn that off. None of them can appear: they are in neither the Sift-owned
    list nor any decided concern, and the allow-list refuses every option that is in neither. This
    is what would catch one being added for some other reason.
    """
    for builder in (build_ytdlp_argv, build_gallerydl_argv):
        argv = builder(_URL, _DEST, cookies_file=_COOKIES)
        for refused in ("--no-continue", "--no-part", "--force-overwrites", "--no-skip"):
            assert refused not in argv


def test_the_url_is_last_after_an_end_of_options_marker() -> None:
    hostile = "-oProxyCommand=evil; rm -rf / `whoami` $(id)"  # a URL that opens with a dash
    for builder in (build_ytdlp_argv, build_gallerydl_argv):
        argv = builder(hostile, _DEST)
        assert argv[-1] == hostile
        assert argv[-2] == "--"  # ends options, so the dash-leading value is never read as a flag
        assert argv.count(hostile) == 1


def test_a_cookies_file_is_added_only_when_supplied() -> None:
    plain = build_ytdlp_argv(_URL, _DEST)
    with_cookies = build_ytdlp_argv(_URL, _DEST, cookies_file=_COOKIES)
    assert "--cookies" not in plain
    assert with_cookies[with_cookies.index("--cookies") + 1] == _COOKIES_ARG

    gallery = build_gallerydl_argv(_URL, _DEST, cookies_file=_COOKIES)
    assert gallery[gallery.index("--cookies") + 1] == _COOKIES_ARG


# --- A Site that must be connected to as a browser -----------------------------------------------


def test_impersonate_is_on_a_command_whose_site_refuses_the_tools_own_client() -> None:
    """Pornhub answers yt-dlp's own client 410 Gone from every address, a
    tunnel included, and the same request made with `--impersonate chrome` gets the video. The
    download and the listing alike, because the listing asks the same site the same way."""
    for command in (
        build_ytdlp_argv(_URL, _DEST, as_a_browser=True),
        build_enumerate_argv(_URL, as_a_browser=True),
    ):
        at = command.index("--impersonate")
        assert command[at + 1] == "chrome"
        assert at < command.index("--")


def test_impersonate_is_off_unless_a_record_asks_for_it() -> None:
    """Pretending to be a browser is not free and not the ordinary case: only a measured record
    asks for it, and the default command is the one pinned above."""
    assert "--impersonate" not in build_ytdlp_argv(_URL, _DEST)
    assert "--impersonate" not in build_enumerate_argv(_URL)


# --- The tool proxy -------------------------------------------------------------------------------

_BUILDERS: tuple[Callable[[str | None], list[str]], ...] = (
    lambda proxy: build_ytdlp_argv(_URL, _DEST, proxy=proxy),
    lambda proxy: build_gallerydl_argv(_URL, _DEST, proxy=proxy),
    lambda proxy: build_enumerate_argv(_URL, proxy=proxy),
)


@pytest.mark.parametrize("build", _BUILDERS)
def test_every_run_goes_through_the_tool_proxy_on_loopback(
    build: Callable[[str | None], list[str]],
) -> None:
    """Direct as much as tunnelled: the tool proxy is what keeps a tool off the private network."""
    command = build(None)
    proxy = urlsplit(_proxy_of(command))
    assert (proxy.scheme, proxy.hostname) == ("http", "127.0.0.1")
    assert TOOL_PROXY.chained_to(_proxy_of(command)) is None
    assert command.index("--proxy") < command.index("--")


@pytest.mark.parametrize("build", _BUILDERS)
def test_a_tunnelled_run_is_handed_the_tool_proxy_chained_to_the_tunnel(
    build: Callable[[str | None], list[str]],
) -> None:
    """The tool never sees the tunnel's address: it goes through the refusal first."""
    command = build(_TUNNEL)
    assert _TUNNEL not in command
    assert TOOL_PROXY.chained_to(_proxy_of(command)) == _TUNNEL


def test_each_run_is_its_own_so_what_it_was_refused_is_its_own() -> None:
    first, second = build_ytdlp_argv(_URL, _DEST), build_ytdlp_argv(_URL, _DEST)
    assert _proxy_of(first) != _proxy_of(second)
    assert proxy_traffic(first) == Traffic()


def test_a_command_with_no_proxy_has_no_traffic_to_ask_about() -> None:
    assert proxy_traffic(["yt-dlp", "--", _URL]) is None
