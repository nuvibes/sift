# SPDX-License-Identifier: AGPL-3.0-or-later
"""The phrasing check over the server's copy: the shapes of a sentence a person would not write.

`phrasing` in `data/vocabulary.json` is ratcheted per file by `test_copy_vocabulary.py`, the same
way as the word lists. These prove each pattern on a planted server string, read the way that gate
reads a module, and prove the rewrite of the shipped sentence it was drawn from passes.
"""

from __future__ import annotations

import pytest

from tests.gates import server_copy, vocabulary
from tests.gates.test_copy_vocabulary import compare, counts

pytestmark = [pytest.mark.gate, pytest.mark.unit]

#: (the sentence as it shipped, the same sentence rewritten)
PAIRS = (
    (
        "Made inside the folder you are in, on your disk and in your library together.",
        "Sift creates it inside the folder you're in, both on your disk and in your library.",
    ),
    ("Press one again to take it out.", "Press one again to remove it."),
    ("How it goes out", "Start the swap"),
    (
        "Restricted means never. Nothing shared later undoes it.",
        "A guest set to Restricted never sees this, even if you later share a folder or tag that "
        "includes it.",
    ),
    (
        "Other names they are known by. Any of them finds them.",
        "Other names this person goes by. Searching for any of them finds this person.",
    ),
    (
        "Ticked on its own for anyone a stash-box that files its creators as people knows.",
        "Sift ticks this for you when a stash-box that lists creators as people knows this person.",
    ),
)


def test_phrasing_is_ratcheted_with_the_word_lists() -> None:
    assert "phrasing" in vocabulary.CHECKS


@pytest.mark.parametrize(("shipped", "rewritten"), PAIRS)
def test_a_planted_server_string_is_found_and_its_rewrite_passes(
    shipped: str, rewritten: str
) -> None:
    planted = server_copy.copy_in(f"refuse(detail={shipped!r})\n", "src/sift/planted.py")
    assert planted, "the reader did not read the planted string"
    assert counts(planted)["phrasing"].get("src/sift/planted.py", 0) >= 1
    assert vocabulary.offences("phrasing", rewritten) == []


def test_a_planted_string_is_a_rise_the_ratchet_refuses() -> None:
    planted = server_copy.copy_in(
        'refuse(detail="Press one again to take it out.")\n', "src/sift/planted.py"
    )
    rises, _falls = compare(counts(planted)["phrasing"], {})
    assert rises == ["src/sift/planted.py: 1, recorded 0"]


def test_twenty_five_words_pass_and_the_twenty_sixth_is_found() -> None:
    def words(n: int) -> str:
        return " ".join(["word"] * n)

    assert vocabulary.offences("phrasing", words(25) + ".") == []
    assert len(vocabulary.offences("phrasing", words(26) + ".")) == 1
    assert vocabulary.offences("phrasing", f"{words(20)}. {words(20)}.") == []
