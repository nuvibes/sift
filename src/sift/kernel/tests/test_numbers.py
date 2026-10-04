# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a number out of untrusted text: `str.isdigit()` admits Unicode digits that `int()` then
refuses, so a guard written that way crashes a few lines later, reachable by a caller or a remote
host."""

from __future__ import annotations

import pytest

from sift.kernel.numbers import as_float, as_int

pytestmark = pytest.mark.unit


#: Superscript two: `isdigit()` is True and `int()` raises. An escape, since the repository is
#: ASCII.
SUPERSCRIPT_TWO = "\u00b2"


def test_the_character_that_makes_the_obvious_guard_a_lie() -> None:
    """Stated as an assertion rather than only in prose, because it is the premise of everything
    below. If a future Python narrowed `isdigit` this would be the line that says so."""
    assert SUPERSCRIPT_TWO.isdigit() is True
    with pytest.raises(ValueError):
        int(SUPERSCRIPT_TWO)

    assert as_int(SUPERSCRIPT_TWO) is None
    assert as_float(SUPERSCRIPT_TWO) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", 0),
        ("7", 7),
        ("-7", -7),
        ("+7", 7),
        ("  7  ", 7),
        ("10000000000000000000000", 10_000_000_000_000_000_000_000),
        # Arabic-Indic digits, which `int()` genuinely reads. This deliberately does not put a
        # second, narrower opinion on top of the conversion.
        ("\u0663\u0664", 34),  # Arabic-Indic three and four
    ],
)
def test_what_counts_as_a_whole_number(raw: str, expected: int) -> None:
    assert as_int(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "seven", "7.5", "7,5", "0x10", "1e3", SUPERSCRIPT_TWO, "\u00bd", None],
)
def test_what_does_not_spell_a_whole_number(raw: str | None) -> None:
    """None is the answer every caller already had a reply for: a 404, a default, a header
    ignored. Never an exception: the callers are a route, a path segment and a remote header."""
    assert as_int(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", 0.0),
        ("7", 7.0),
        ("7.5", 7.5),
        ("-7.5", -7.5),
        ("  7.5  ", 7.5),
        ("1e3", 1000.0),
    ],
)
def test_what_counts_as_a_number_of_seconds(raw: str, expected: float) -> None:
    assert as_float(raw) == expected


@pytest.mark.parametrize("raw", ["", "later", "7,5", SUPERSCRIPT_TWO, None])
def test_what_does_not_spell_a_number_of_seconds(raw: str | None) -> None:
    assert as_float(raw) is None


@pytest.mark.parametrize("raw", ["inf", "-inf", "Infinity", "-Infinity", "nan", "NaN"])
def test_a_wait_that_never_ends_is_refused(raw: str) -> None:
    """These parse (`float("inf")` is not an error), and none of them is a number of seconds
    anybody meant. Letting one through turns a header from another server into a wait forever."""
    assert as_float(raw) is None
