# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one definition of what counts as text, tested from both directions: what the write side
stores, every query must be able to ask for, or a name is stored and never found."""

from __future__ import annotations

import unicodedata

import pytest

from sift.kernel.access.constraints import fts_match
from sift.kernel.text import (
    clean_name,
    clean_stored_text,
    clean_token_text,
    non_empty_str,
    printable,
    stripped_or_none,
)


class TestPrintable:
    def test_a_nul_is_removed(self) -> None:
        assert printable("be\x00ach") == "beach"

    def test_the_whole_c0_range_and_del_go(self) -> None:
        noisy = "a" + "".join(chr(code) for code in range(0x20)) + "\x7fb"
        assert printable(noisy) == "ab"

    def test_a_lone_surrogate_goes(self) -> None:
        assert printable("a\ud800b") == "ab"

    def test_ordinary_text_is_untouched(self) -> None:
        # The point of the rule is that it takes out what is not text, not that it narrows what
        # a name may be. Apostrophes, accents, CJK and emoji are all real in real names.
        for name in ["O'Brien", "Bjork", "Zoe", "\u5317\u4eac", "beach \U0001f3d6", "a-b_c.d"]:
            assert printable(name) == name

    def test_a_tab_and_a_newline_are_control_characters_too(self) -> None:
        assert printable("a\tb\nc\rd") == "abcd"


class TestCleanName:
    def test_it_strips_and_removes_together(self) -> None:
        assert clean_name("  be\x00ach  ", what="a tag's name") == "beach"

    def test_a_double_quote_is_refused_rather_than_removed(self) -> None:
        # Refused, not stripped: the character is visible and was typed on purpose, so handing back
        # a quietly different name would leave somebody to notice the difference themselves.
        with pytest.raises(ValueError, match="double quote"):
            clean_name('say "hi"', what="a tag's name")

    def test_an_apostrophe_is_allowed(self) -> None:
        # Deliberate and load-bearing: refusing this would refuse people their own names.
        assert clean_name("O'Brien", what="a person's name") == "O'Brien"

    def test_blank_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot be blank"):
            clean_name("   ", what="a tag's name")

    def test_a_name_that_was_only_control_characters_is_blank_not_a_character_complaint(
        self,
    ) -> None:
        # The order of the two checks is what produces this message. A complaint about characters
        # that are no longer in the string would be a message nobody can act on.
        with pytest.raises(ValueError, match="cannot be blank"):
            clean_name("\x00\x01\x02", what="a tag's name")

    def test_the_subject_is_named_in_the_message(self) -> None:
        with pytest.raises(ValueError, match="a person's name cannot be blank"):
            clean_name("", what="a person's name")


class TestCleanStoredText:
    def test_it_removes_without_raising(self) -> None:
        assert clean_stored_text("cl\x00ip.mp4") == "clip.mp4"

    def test_it_tolerates_emptiness(self) -> None:
        # Unlike a typed name: there is nobody to show a refusal to, and raising here would fail an
        # import over a character in a filename.
        assert clean_stored_text("\x00\x00") == ""

    def test_a_quote_survives(self) -> None:
        # A file really can be called this, it is already on the disk, and refusing to import it
        # would be refusing somebody their own file to protect a query they may never type.
        assert clean_stored_text('say "hi".mp4') == 'say "hi".mp4'


class TestTheTwoSidesAgree:
    """The actual bug. Each of these stores a name and then asks for it back."""

    @pytest.mark.parametrize(
        "typed",
        ["be\x00ach", "be\x01ach", "be\x7fach", "\tbeach", "be\ud800ach"],
        ids=["nul", "c0", "del", "tab", "surrogate"],
    )
    def test_a_stored_name_is_askable_for(self, typed: str) -> None:
        """What the write side keeps, the read side can still ask for."""
        stored = clean_name(typed, what="a tag's name")
        asked = fts_match(stored)
        assert asked is not None
        assert stored in asked

    def test_a_name_of_only_control_characters_can_never_be_stored(self) -> None:
        # The other half of the guarantee. The read side answers None for this text (there is no
        # term in it), so if the write side had allowed it, it would be a row no query can reach.
        assert fts_match("\x00\x01") is None
        with pytest.raises(ValueError):
            clean_name("\x00\x01", what="a tag's name")


class TestInvisibleCharacters:
    """The characters that take up no space and say nothing, a NUL or a pasted zero-width space."""

    @pytest.mark.parametrize(
        "invisible",
        ["\u200b", "\u2060", "\ufeff", "\u00ad"],
        ids=["zero-width-space", "word-joiner", "byte-order-mark", "soft-hyphen"],
    )
    def test_an_invisible_character_is_removed(self, invisible: str) -> None:
        assert clean_name(f"be{invisible}ach", what="a tag's name") == "beach"

    def test_a_joiner_that_carries_meaning_is_kept(self) -> None:
        """U+200C and U+200D are kept: they decide which letters join in Persian and Indic scripts,
        so removing one changes somebody's name."""
        assert "\u200c" in printable("be\u200cach")
        assert "\u200d" in printable("be\u200dach")

    def test_an_emoji_sequence_survives_intact(self) -> None:
        """The family emoji is several people joined by U+200D. Strip it and one picture becomes
        three, which is why the joiner cannot simply go with the rest."""
        family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
        assert printable(family) == family


class TestOneCanonicalSpelling:
    """Unicode lets the same visible name be spelled with different code points."""

    def test_a_decomposed_name_is_stored_composed(self) -> None:
        decomposed = unicodedata.normalize("NFD", "caf\u00e9 latte")
        assert clean_name(decomposed, what="a tag's name") == unicodedata.normalize(
            "NFC", "caf\u00e9 latte"
        )

    def test_either_spelling_asks_for_the_same_thing(self) -> None:
        """The actual guarantee: what is stored is what a query resolves to, whichever way the
        person typing happened to spell it. Without normalisation these are different strings that
        look identical, and the name is storable and unfindable."""
        stored = clean_name(unicodedata.normalize("NFD", "caf\u00e9"), what="a tag's name")
        asked_nfc = fts_match(unicodedata.normalize("NFC", "caf\u00e9"))
        asked_nfd = fts_match(unicodedata.normalize("NFD", "caf\u00e9"))

        assert asked_nfc == asked_nfd
        assert asked_nfc is not None and stored in asked_nfc


class TestTokenNamedText:
    """Remote text that is also written as a filter token."""

    def test_a_quote_is_removed_from_a_handle_or_a_site(self) -> None:
        # `sites:"example"` is how one of these is named, and the grammar has no escape for a
        # quote inside it, so a stored quote makes the name untypeable as a token.
        assert clean_token_text('Ho"st') == "Host"

    def test_a_filename_keeps_its_quote(self) -> None:
        # Not a token, and the file is already on the disk under that name.
        assert clean_stored_text('say "hi".mp4') == 'say "hi".mp4'


class TestAValueAsText:
    """A value read out of a record or off the wire, as text or as nothing."""

    def test_any_value_is_text_without_its_outer_spaces(self) -> None:
        assert stripped_or_none("  Jane Roe ") == "Jane Roe"
        assert stripped_or_none(1984) == "1984"
        assert stripped_or_none("   ") is None
        assert stripped_or_none("") is None
        assert stripped_or_none(None) is None

    def test_only_a_non_empty_string_is_text_as_it_stands(self) -> None:
        assert non_empty_str(" Jane Roe ") == " Jane Roe "
        assert non_empty_str("") is None
        assert non_empty_str(1984) is None
        assert non_empty_str(["Jane Roe"]) is None
        assert non_empty_str(None) is None
