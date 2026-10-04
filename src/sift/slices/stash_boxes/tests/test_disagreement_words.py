# SPDX-License-Identifier: AGPL-3.0-or-later
"""A disagreement's two values said in words by `records.value_said`, the rule History uses."""

from __future__ import annotations

from sift.kernel.records import Subject, value_said
from sift.slices.stash_boxes.reconcile import Disagreement
from sift.slices.stash_boxes.router import _disagreement_view


def test_a_receipt_says_a_constant_as_the_record_draws_it() -> None:
    """A decision's words go through the record's one rule first: a box's constant is a word, a
    length has its unit, and only what that rule declines is said here (nothing, a list)."""
    from sift.slices.stash_boxes.reconcile import _said

    assert _said(Subject.PERSON, "breast_type", "NATURAL") == "Natural"
    assert _said(Subject.PERSON, "height_cm", 172) == "172 cm"
    assert _said(Subject.PERSON, "aliases", None) == "nothing"
    assert _said(Subject.PERSON, "aliases", ["Neve", "Arbor"]) == "Neve, Arbor"


def _row(key: str, mine: object, theirs: object) -> Disagreement:
    # Invented for this file; see `tests/gates/data/names_cast.txt`.
    return Disagreement(
        subject=Subject.PERSON,
        local_id="p-1",
        name="Neve Arbor",
        box_id="box-1",
        box_name="FansDB",
        key=key,
        mine=mine,
        theirs=theirs,
    )


def test_a_boxs_constant_is_said_as_the_history_line_says_it() -> None:
    view = _disagreement_view(_row("breast_type", "FAKE", "NATURAL"))

    assert (view.mine_said, view.theirs_said) == ("Fake", "Natural")
    # The one rule, not a copy of it: exactly what the line's own reader says.
    assert view.theirs_said == value_said(Subject.PERSON, "breast_type", '"NATURAL"')
    # The raw values still travel, for what has no words.
    assert (view.mine, view.theirs) == ("FAKE", "NATURAL")


def test_a_length_says_its_unit_and_ordinary_text_is_left_as_written() -> None:
    height = _disagreement_view(_row("height_cm", 170, 172))
    named = _disagreement_view(_row("name", "null", "123"))

    assert (height.mine_said, height.theirs_said) == ("170 cm", "172 cm")
    # A name that happens to read like JSON is still a name.
    assert (named.mine_said, named.theirs_said) == ("null", "123")


def test_a_list_is_said_item_by_item_and_nothing_is_not_said() -> None:
    view = _disagreement_view(_row("aliases", ["NATURAL", "Neve A"], None))

    # An alias is a name, so capitals stay; only a `WORD` field holds a box's constants.
    assert view.mine_said == "NATURAL, Neve A"
    assert view.theirs_said is None


def test_a_name_in_capitals_is_a_name_and_a_nationality_is_its_country() -> None:
    """The rule reads the field's kind, not the look of the value: a person's name "WRENFIELD" is
    not a box's constant, and a nationality "US" is drawn as the record draws it,
    "United States"."""
    named = _disagreement_view(_row("name", "WRENFIELD", "wrenfieldx"))
    country = _disagreement_view(_row("country", "US", "CR"))
    unknown = _disagreement_view(_row("country", "XX", "ca"))
    word = _disagreement_view(_row("ethnicity", "MIXED", "CAUCASIAN"))

    assert (named.mine_said, named.theirs_said) == ("WRENFIELD", "wrenfieldx")
    assert (country.mine_said, country.theirs_said) == ("United States", "Costa Rica")
    # A code the table does not know is said as itself, so a wrong value stays visible.
    assert (unknown.mine_said, unknown.theirs_said) == ("XX", "Canada")
    assert (word.mine_said, word.theirs_said) == ("Mixed", "Caucasian")
    # The one rule, which the History line reads too.
    assert value_said(Subject.PERSON, "country", '"US"') == "United States"
    # A reference is not a choice: a Site's code keeps its capitals.
    assert value_said(Subject.ASSET, "site_code", '"S03E03"') == "S03E03"
    assert value_said(Subject.PERSON, "hair_color", '"BLONDE"') == "Blonde"


def test_a_receipt_says_a_list_value_item_by_item() -> None:
    """The stored title and detail of a settled row, for a value held as a list."""
    from sift.slices.stash_boxes.router import _settled_words

    title, detail = _settled_words(
        _row("measurements", ["34", "24", "34"], "36-24-36"), take_theirs=False
    )

    assert "34, 24, 34" in title
    assert "stays 34, 24, 34." in detail
