# SPDX-License-Identifier: AGPL-3.0-or-later
"""The key a named row is ordered by, and the accented case by hand."""

from __future__ import annotations

from sift.kernel.sorting import sort_key


class TestTheCaseThatSentEveryAccentedNamePastZ:
    """`ORDER BY name COLLATE NOCASE` folds ASCII only. This is what it did and what fixes it."""

    def test_a_ring_over_an_a_files_with_the_as(self) -> None:
        """`Alesund` files with the A's; where it lands is asserted, since it shares a key with its
        unaccented twin and the query breaks that tie on the id."""
        names = ["Zebra", "\u00c5lesund", "apple", "Apple", "iPhone", "Alesund"]

        ordered = sorted(names, key=sort_key)

        assert ordered[:2] == sorted(["Alesund", "\u00c5lesund"], key=ordered.index)
        assert ordered[2:] == ["apple", "Apple", "iPhone", "Zebra"]
        assert ordered.index("\u00c5lesund") < ordered.index("Zebra"), "still past Z"

    def test_the_plain_ascii_order_is_unchanged(self) -> None:
        """`iPhone` does not sort above `Apple`, which NOCASE already got right."""
        names = ["Zebra", "apple", "Apple", "iPhone"]

        assert sorted(names, key=sort_key) == ["apple", "Apple", "iPhone", "Zebra"]


class TestWhatItFolds:
    def test_case(self) -> None:
        assert sort_key("Apple") == sort_key("apple") == "apple"

    def test_every_kind_of_accent(self) -> None:
        assert sort_key("\u00e9clair") == "eclair"
        assert sort_key("Ren\u00e9e") == "renee"
        assert sort_key("\u00fcber") == "uber"

    def test_a_letter_written_as_two_codepoints_and_as_one(self) -> None:
        """A combining acute and the single character are one key: decomposed first."""
        assert sort_key("e\u0301clair") == sort_key("\u00e9clair")

    def test_the_wide_latin_letters(self) -> None:
        """Full-width characters are the same name (the K in NFKD)."""
        assert sort_key("\uff21pple") == "apple"

    def test_a_non_breaking_space_is_a_space(self) -> None:
        assert sort_key("Jane\u00a0Doe") == "jane doe"

    def test_runs_of_whitespace_collapse(self) -> None:
        assert sort_key("  Jane   Doe  ") == "jane doe"

    def test_an_empty_name_gives_an_empty_key(self) -> None:
        """An empty name gives an empty key rather than failing a write."""
        assert sort_key("") == ""


class TestWhatItIsNot:
    def test_it_is_not_a_fold_for_MATCHING(self) -> None:
        """Shared keys order alike; no UNIQUE constraint depends on them."""
        assert sort_key("Ren\u00e9e") == sort_key("Renee")

    def test_a_script_that_does_not_decompose_to_latin_keeps_its_own_order(self) -> None:
        """Other scripts sort after the Latin names, among themselves, unguessed."""
        names = ["\u4eac", "apple", "\u3042"]

        assert sorted(names, key=sort_key)[0] == "apple"
