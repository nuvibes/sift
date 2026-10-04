# SPDX-License-Identifier: AGPL-3.0-or-later
"""The allow-list is the security boundary of the downloader, so it is held to being one.

Both tools can be told to run an arbitrary command, and both will read a configuration file chosen
by whoever passed the option, so "which options may appear on a command line" is not a matter of
taste here. These tests fail if an option outside the declared list reaches a built command, if one
of the named refusals ever appears, or if a value that came from outside could become an option
rather than an argument.

The last of those is the one worth stating plainly: every element of a built command is a constant
written into this module, a number Sift validated, a path Sift created, or the URL, and the URL is last,
after the `--` that ends the options.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.slices.download.sources import argv
from sift.slices.download.sources.tuning import (
    POLICY,
    QUALITY_BEST,
    QUALITY_COMPATIBLE,
    Filters,
    Pacing,
    RunPolicy,
)

_EVERYTHING = RunPolicy(
    pacing=Pacing(
        seconds_between_requests=1.5,
        retries=7,
        timeout_seconds=45.0,
        wait_after_too_many_requests=90.0,
        bytes_per_second=2_000_000,
    ),
    filters=Filters(at_least_bytes=1024, at_most_bytes=8_000_000_000),
    quality=QUALITY_BEST,
    verbose=True,
)


def _commands(policy: RunPolicy) -> list[list[str]]:
    """Every command shape this slice can produce, with every optional part present.

    Both the with-cookies and without shapes, because a login is the one part of a command that
    appears only sometimes and it carries a path.
    """
    into = Path("/var/lib/sift/staging")
    url = "https://example.com/watch?v=1"
    return [
        argv.build_ytdlp_argv(url, into, policy=policy),
        argv.build_ytdlp_argv(
            url, into, cookies_file=into / "cookies", proxy="http://127.0.0.1:1", policy=policy
        ),
        argv.build_gallerydl_argv(url, into, policy=policy),
        argv.build_gallerydl_argv(
            url, into, cookies_file=into / "cookies", proxy="http://127.0.0.1:1", policy=policy
        ),
        argv.build_enumerate_argv(url, policy=policy),
        argv.build_enumerate_argv(url, proxy="http://127.0.0.1:1", policy=policy),
        # A Site whose record says to connect as a browser, so that option is held to the list too.
        argv.build_ytdlp_argv(url, into, policy=policy, as_a_browser=True),
        argv.build_enumerate_argv(url, policy=policy, as_a_browser=True),
    ]


def _options(command: list[str]) -> list[str]:
    """The elements of a command that are options rather than values.

    Everything after the `--` terminator is a value whatever it looks like (that is what the
    terminator is for), so the scan stops there. A URL beginning with a dash is the case this
    exists for, and it is an argument, not an option.
    """
    end = command.index("--") if "--" in command else len(command)
    return [part for part in command[:end] if part.startswith("-")]


@pytest.mark.parametrize("policy", [POLICY, _EVERYTHING])
def test_no_command_carries_an_option_outside_the_allow_list(policy: RunPolicy) -> None:
    for command in _commands(policy):
        for option in _options(command):
            assert option in argv.ALLOWED_OPTIONS, f"{option} is not on the allow-list"


def test_the_allow_list_holds_nothing_that_is_never_used() -> None:
    """A list that may hold entries nothing emits is a list that rots quietly.

    Entries accumulate as options are tried and dropped, and every stale one widens the boundary for
    nothing. So the declared list has to be exactly what these commands can produce, which also means
    an option removed from a command cannot be left behind here.
    """
    used = {option for command in _commands(_EVERYTHING) for option in _options(command)}
    assert used == argv.ALLOWED_OPTIONS


@pytest.mark.parametrize("refused", sorted(argv.REFUSED))
def test_a_refused_option_never_appears(refused: str) -> None:
    for command in _commands(_EVERYTHING):
        assert refused not in command


def test_every_refusal_says_why() -> None:
    """A bare list of refusals is a list somebody re-adds to. Each carries its reason."""
    for option, reason in argv.REFUSED.items():
        assert len(reason) > 20, f"{option} is refused without saying why"


def test_a_refused_option_is_not_quietly_on_the_allow_list() -> None:
    assert not (argv.ALLOWED_OPTIONS & set(argv.REFUSED))


def test_the_url_is_the_last_element_and_comes_after_the_terminator() -> None:
    """The only value from outside, in the one position where it cannot be read as an option."""
    url = "--not-an-option-really"
    for command in (
        argv.build_ytdlp_argv(url, Path("/var/lib/sift/s")),
        argv.build_gallerydl_argv(url, Path("/var/lib/sift/s")),
        argv.build_enumerate_argv(url),
    ):
        assert command[-1] == url
        assert command[-2] == "--"


def test_a_size_filter_is_passed_as_plain_digits() -> None:
    """Both tools read a bare number as bytes. A suffix would be a second spelling of one number."""
    command = argv.build_ytdlp_argv("https://e.com/a", Path("/var/lib/sift/s"), policy=_EVERYTHING)
    assert command[command.index("--min-filesize") + 1] == "1024"
    assert command[command.index("--max-filesize") + 1] == "8000000000"


def test_no_bandwidth_cap_means_the_option_is_absent_rather_than_zero() -> None:
    """Zero is a number a tool would try to honour. No cap is the absence of the option."""
    command = argv.build_ytdlp_argv("https://e.com/a", Path("/var/lib/sift/s"))
    assert "--limit-rate" not in command


def test_verbose_off_means_the_switch_is_absent() -> None:
    assert "--verbose" not in argv.build_ytdlp_argv("https://e.com/a", Path("/var/lib/sift/s"))
    assert "--verbose" in argv.build_ytdlp_argv(
        "https://e.com/a", Path("/var/lib/sift/s"), policy=_EVERYTHING
    )


def test_the_quality_preference_reorders_and_never_filters() -> None:
    """`-f` would fail a download outright when one format is refused; `-S` falls through."""
    for quality in (QUALITY_COMPATIBLE, QUALITY_BEST):
        command = argv.build_ytdlp_argv(
            "https://e.com/a", Path("/var/lib/sift/s"), policy=RunPolicy(quality=quality)
        )
        assert "-f" not in command
        assert command[command.index("-S") + 1]

    compatible = argv.build_ytdlp_argv("https://e.com/a", Path("/var/lib/sift/s"))
    best = argv.build_ytdlp_argv(
        "https://e.com/a", Path("/var/lib/sift/s"), policy=RunPolicy(quality=QUALITY_BEST)
    )
    assert compatible[compatible.index("-S") + 1] != best[best.index("-S") + 1]


def test_an_unknown_quality_word_falls_back_to_the_compatible_order() -> None:
    """A settings row can outlive the code that wrote it. The safe answer is today's behaviour."""
    stored_nonsense = argv.build_ytdlp_argv(
        "https://e.com/a",
        Path("/var/lib/sift/s"),
        policy=RunPolicy(quality="from-an-older-version"),
    )
    compatible = argv.build_ytdlp_argv("https://e.com/a", Path("/var/lib/sift/s"))
    assert (
        stored_nonsense[stored_nonsense.index("-S") + 1] == compatible[compatible.index("-S") + 1]
    )
