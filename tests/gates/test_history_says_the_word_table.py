# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every line a History can say uses the History word table, and never the words it replaced.

The retired words are `scoped_wrong_words` under `history_screens` in `data/vocabulary.json`. The
client's half is that scope over the History components; the server's half reads what the builders
SAY (`sift.kernel.tests.test_sentences.SAID`, held at zero) and what the source HOLDS
(`server_copy.history_copy`, saved titles included), the latter a per-file ratchet
(`history_ratchets.py`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sift.kernel.access import sentences as say
from sift.kernel.tests.test_sentences import SAID
from sift.slices.insights.tests.test_statements import SAID as INSIGHTS_SAID
from tests.gates import history_ratchets, server_copy, vocabulary

pytestmark = [pytest.mark.gate, pytest.mark.unit]

#: The ratchet this file holds. See `history_ratchets.py`.
CHECK = "history_words"


def _history_words() -> list[tuple[re.Pattern[str], str]]:
    """The History word table's wrong words, read from the one vocabulary file."""
    return [
        (pattern, instead) for pattern, _word, instead in history_ratchets.history_word_patterns()
    ]


@pytest.mark.parametrize(("what", "by", "line"), SAID, ids=[one for one, _, _ in SAID])
def test_every_line_says_the_history_word_table(what: str, by: str | None, line: say.Line) -> None:
    words = say.text_of(line)
    for pattern, instead in _history_words():
        assert not pattern.search(words), f"{what}: {words!r} -> {instead}"


@pytest.mark.parametrize(
    ("what", "line"), INSIGHTS_SAID, ids=[f"insights: {one}" for one, _ in INSIGHTS_SAID]
)
def test_every_insights_statement_says_the_history_word_table(
    what: str, line: say.Line | None
) -> None:
    """Every statement Insights can say, rendered: drawn by History's component, so held to its
    words."""
    words = say.text_of(line or ())
    assert words, what
    for pattern, instead in _history_words():
        assert not pattern.search(words), f"{what}: {words!r} -> {instead}"


def test_the_word_table_is_read() -> None:
    """A known positive, so an empty scope cannot pass every line by reading nothing."""
    found = _history_words()
    assert len(found) >= 10
    assert any(pattern.search("That was taken back") for pattern, _ in found)


# --- what the source holds: the History modules, every piece, every saved title --------------------


@pytest.fixture(scope="module")
def held() -> list[server_copy.Copy]:
    return server_copy.history_copy()


def test_no_history_source_says_more_retired_words_than_was_recorded(
    held: list[server_copy.Copy],
) -> None:
    failure = history_ratchets.held(CHECK, held)
    assert failure is None, failure


def test_the_history_source_is_really_being_read(held: list[server_copy.Copy]) -> None:
    """Canaries: a one-word piece, a mark's tooltip and a saved title are found."""
    vias = {one.via for one in held}
    assert len(held) > 400, f"only {len(held)} History strings found, which cannot be right"
    assert any(via.startswith("line:") for via in vias), "no piece of a line was read"
    assert "table" in vias, "no word table's values were read"
    assert vias & server_copy.SAVED_TITLE_VIAS, "no saved decision title was read"
    assert any(one.text == say.MEANS["renamed"] for one in held), "a one-word tooltip was missed"


def test_a_retired_word_is_found_in_a_piece_a_tooltip_and_a_saved_title() -> None:
    """Planted words are found in each place only one rule reads, and never in a table's key, a
    lookup or a log line."""
    sentences = server_copy.HISTORY_MODULES[0]
    module = """
MEANS = {"unlinked": "Unlinked"}
STALE = {"set aside": "discarded"}
def line(by):
    return said(by, "Cancelled", thing("tag", "t", "poolside"))
def lookup(kind):
    return TABLE["kept local"] if kind == "made" else None
"""
    writer = """
async def decide(recorder, connection, n):
    back = " It was put back."
    await recorder.record_on(connection, queue="q", user_id=None, title="Made a Photo Set",
        detail=f"{n} faces.{back}", payload="{}")
    log.info("a group was taken back")
"""
    found = [
        word
        for one in (
            *server_copy.history_copy_in(module, sentences),
            *server_copy.history_copy_in(writer, "src/sift/slices/x/service.py"),
        )
        for word, _instead in history_ratchets.history_words_in(one.text)
    ]
    assert sorted(word.lower() for word in found) == ["cancelled", "made", "put back", "unlinked"]
    # A module that neither says a line nor writes a receipt is not a History surface at all.
    assert server_copy.history_copy_in(writer.replace("record_on", "other"), "src/x.py") == []


def test_an_insights_statement_is_read_as_history_whatever_its_shape() -> None:
    """The statement module and the recaps' cards are History modules; a module outside the list is
    not."""
    module = """
NOT_ENOUGH = "Nothing was cancelled yet."
def viewed(period, whole):
    return said(f"You viewed {whole} and learnt nothing {period}.")
def closing(name):
    return Said(capitalized(said("Taken back", thing("person", "p", name))))
"""
    found = sorted(
        word.lower()
        for where in server_copy.STATEMENT_MODULES
        for one in server_copy.history_copy_in(module, where)
        for word, _instead in history_ratchets.history_words_in(one.text)
    )
    assert found == sorted(
        ["cancelled", "learnt", "taken back"] * len(server_copy.STATEMENT_MODULES)
    )
    assert server_copy.history_copy_in(module, "src/sift/slices/insights/router.py") == []


def test_the_ratchet_sees_a_rise_and_a_fall() -> None:
    rises, falls = history_ratchets.compare({"a.py": 3, "b.py": 1}, {"a.py": 2, "c.py": 4})
    assert rises == ["a.py: 3, recorded 2", "b.py: 1, recorded 0"]
    assert falls == ["c.py: 0, recorded 4"]


# --- the client's half reaches every file that draws History ---------------------------------------

CLIENT = Path(__file__).resolve().parents[2] / "frontend" / "src"

#: A client module that draws History: the History components and the feed.
_DRAWS_HISTORY = re.compile(r"histor|ledger", re.IGNORECASE)


def test_every_client_file_that_draws_history_is_held_to_the_word_table() -> None:
    """Every client module drawing History is reached by the `history_screens` scope."""
    scope = vocabulary.scope("history_screens")
    drawing = [
        path.relative_to(CLIENT).as_posix()
        for path in CLIENT.rglob("*")
        if path.suffix in (".svelte", ".ts")
        and ".test." not in path.name
        and not path.name.endswith(".d.ts")
        and _DRAWS_HISTORY.search(path.name)
    ]
    assert len(drawing) >= 8, drawing
    outside = [where for where in drawing if not any(fragment in where for fragment in scope)]
    assert not outside, (
        f"these draw History and the word table does not reach them: {outside}. Add a fragment"
        " to scopes.history_screens in tests/gates/data/vocabulary.json"
    )
