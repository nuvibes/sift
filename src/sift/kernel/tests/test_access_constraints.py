# SPDX-License-Identifier: AGPL-3.0-or-later
"""A History line's group as an address spells it, and the condition it filters a wall to.

An address is typed by whoever pastes it, so every spelling that cannot be a filing has to read as
nothing rather than as something wider: a filing that could not be read must never filter a wall
to the whole Site.
"""

from __future__ import annotations

import pytest

from sift.kernel.access.constraints import BOX_SOURCE, ConstraintError, Filing, Where

pytestmark = pytest.mark.unit

THING = "01HX0000000000000000000601"
#: A day in late 2023, as the day number a filing is stored by.
DAY = 19675


@pytest.mark.parametrize(
    ("parameter", "subject", "source", "box", "refused"),
    [
        ("sorted", THING, None, None, "is not a filing"),
        ("filed", "two words", None, None, "names one thing by its id"),
        ("filed", THING, "Filename", None, "is not a source"),
        ("filed", THING, "filename", "a box", "only a stash-box filing names a box"),
        ("filed", THING, BOX_SOURCE, "  ", "only a stash-box filing names a box"),
    ],
)
def test_a_filing_that_could_never_be_a_line_is_refused(
    parameter: str, subject: str, source: str | None, box: str | None, refused: str
) -> None:
    with pytest.raises(ConstraintError, match=refused):
        Filing(parameter, subject, source, DAY, box)


def test_every_filing_reads_back_from_its_own_spelling() -> None:
    for filing in (
        Filing("filed", THING, "filename", DAY),
        Filing("tagged", THING, None, None),
        Filing("named", THING, BOX_SOURCE, DAY, "01HX0000000000000000000602"),
        Filing("named", THING, BOX_SOURCE, DAY),
    ):
        assert Filing.read(filing.parameter, filing.value) == filing
    assert Filing("filed", THING, "filename", DAY).value == f"{THING}~filename~2023-11-14"


@pytest.mark.parametrize(
    "value",
    [
        f"{THING}~filename",
        f"{THING}~filename~20231114",
        f"{THING}~filename~2023-13-40",
        f"{THING}~Filename~2023-11-14",
        f"{THING}~filename~2023-11-14~a box",
    ],
)
def test_a_spelling_that_is_not_a_filing_reads_as_nothing(value: str) -> None:
    assert Filing.read("filed", value) is None


def test_a_filing_narrows_to_exactly_the_rows_its_line_counted() -> None:
    by_hand = Filing("filed", THING, None, None)
    assert by_hand.where == Where("filed_under", (THING, None, None))
    boxed = Filing("filed", THING, BOX_SOURCE, DAY, "01HX0000000000000000000602")
    assert boxed.where == Where("filed_under_box", (THING, DAY, "01HX0000000000000000000602"))
