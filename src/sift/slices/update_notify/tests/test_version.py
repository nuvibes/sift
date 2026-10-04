# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading and ordering version numbers.

The comparison decides whether a person is shown a notice telling them to run a command. Getting it
wrong in one direction nags somebody who is already up to date; getting it wrong in the other keeps
a security fix off their screen. Both directions are asserted here, along with what happens when
either side is not a version at all.
"""

from __future__ import annotations

import pytest

from sift.slices.update_notify.version import Version, is_newer, parse


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.4.0", Version((1, 4, 0))),
        ("v1.4.0", Version((1, 4, 0))),
        ("  v0.0.1  ", Version((0, 0, 1))),
        ("10.20.30", Version((10, 20, 30))),
        ("1.4.0-rc.1", Version((1, 4, 0), ("rc", "1"))),
        ("1.4.0-beta", Version((1, 4, 0), ("beta",))),
        # Build metadata names the same release, so it is read and then dropped.
        ("1.4.0+abc123", Version((1, 4, 0))),
        ("1.4.0-rc.1+abc123", Version((1, 4, 0), ("rc", "1"))),
    ],
)
def test_parses_a_version(text: str, expected: Version) -> None:
    assert parse(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "1",
        "1.4",
        "1.4.0.0",
        "one.four.zero",
        "1.4.x",
        "latest",
        "v",
        "1.4.0-",
        "1.4.0+",
        "1.4.0-rc..1",
        "-1.4.0",
        "1.4.0 extra",
        "release 1.4.0",
    ],
)
def test_refuses_what_is_not_a_version(text: str) -> None:
    """Nothing is guessed at. A feed that has changed shape reads as no version rather than as one
    that happens to sort high."""
    assert parse(text) is None


@pytest.mark.parametrize(
    ("candidate", "current"),
    [
        ("1.4.1", "1.4.0"),
        ("1.5.0", "1.4.9"),
        ("2.0.0", "1.99.99"),
        ("v1.4.1", "1.4.0"),
        ("1.4.0", "1.4.0-rc.1"),
        ("1.4.0-rc.2", "1.4.0-rc.1"),
        ("1.4.0-rc.10", "1.4.0-rc.9"),
        ("1.4.0-beta", "1.4.0-alpha"),
        ("1.4.0-rc.1.1", "1.4.0-rc.1"),
        # A textual identifier sorts above a numeric one, so this is an advance, not a regression.
        ("1.4.0-rc", "1.4.0-1"),
    ],
)
def test_newer_is_newer(candidate: str, current: str) -> None:
    assert is_newer(candidate, current) is True


@pytest.mark.parametrize(
    ("candidate", "current"),
    [
        ("1.4.0", "1.4.0"),
        ("v1.4.0", "1.4.0"),
        ("1.4.0+abc123", "1.4.0"),
        ("1.3.9", "1.4.0"),
        ("1.4.0", "1.5.0"),
        ("0.9.9", "1.0.0"),
        ("1.4.0-rc.1", "1.4.0"),
        ("1.4.0-rc.1", "1.4.0-rc.2"),
        ("1.4.0-rc.9", "1.4.0-rc.10"),
        ("1.4.0-rc.1", "1.4.0-rc.1.1"),
        ("1.4.0-1", "1.4.0-rc"),
    ],
)
def test_not_newer(candidate: str, current: str) -> None:
    assert is_newer(candidate, current) is False


@pytest.mark.parametrize(
    ("candidate", "current"),
    [
        ("not-a-version", "1.4.0"),
        ("1.4.0", "not-a-version"),
        ("", "1.4.0"),
        ("1.4.0", ""),
        ("", ""),
    ],
)
def test_an_unreadable_version_is_never_an_update(candidate: str, current: str) -> None:
    """The doubtful case is silence. A notice asks somebody to run a command, and a version nobody
    can read is not a reason to ask."""
    assert is_newer(candidate, current) is False
