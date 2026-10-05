# SPDX-License-Identifier: AGPL-3.0-or-later
"""Property tests on the query parser, run against arbitrary input.

The golden table says what the language means for inputs somebody thought of; these say what the
parser must never do for inputs nobody thought of: raise, widen, or emit a match expression it did
not intend. The last is proven by executing the expression on a real FTS5 table, since text from a
search box is data and never FTS5 syntax.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from sift.kernel.access import MIN_TEXT_TERM, AssetFilter, fts_contains, fts_match
from sift.kernel.access.constraints import _printable
from sift.slices.search.filters import (
    Presence,
    Term,
    _text,
    parse,
    parse_tokens,
    scalar,
    token_prefix,
)

NOW = 1_783_468_800

#: Unicode with the characters either grammar cares about over-represented.
INTERESTING = '":,+-.. \t*^()[]{}\\/%_\x00\n' + "NEAR OR AND NOT"

#: Lone surrogates, which `st.characters()` never generates; one reaching SQLite unescaped can
#: break out of a quoted literal.
SURROGATES = st.sampled_from([chr(point) for point in (0xD800, 0xDBFF, 0xDC00, 0xDFFF)])

#: Words in the indexed row below, so the "only matching rows" property has rows to check.
PRESENT = st.sampled_from(
    ["beach", "sunset", "Jane", "Doe", "janed", "NEAR", "quoted", "caret", "dash", "paren"]
)

TEXT = st.one_of(
    st.text(
        alphabet=st.one_of(st.sampled_from(INTERESTING), SURROGATES, st.characters()),
        max_size=120,
    ),
    # Half the examples use words in the row, so a match has something to return.
    st.lists(st.one_of(PRESENT, st.sampled_from(INTERESTING)), min_size=1, max_size=4).map(
        " ".join
    ),
)

#: The token names, so generated input reaches the value parsers.
TOKENS = st.sampled_from(
    [
        "people",
        "accounts",
        "sites",
        "tags",
        "collections",
        "type",
        "rating",
        "fav",
        "in",
        "added",
        "duration",
        "colour",
        "TAGS",
        "Rating",
    ]
)

QUERY = st.one_of(
    TEXT,
    st.builds(lambda name, value: f"{name}:{value}", TOKENS, TEXT),
    st.lists(
        st.one_of(TEXT, st.builds(lambda name, value: f"{name}:{value}", TOKENS, TEXT)),
        max_size=5,
    ).map(" ".join),
)


@given(QUERY)
@settings(max_examples=400, suppress_health_check=[HealthCheck.too_slow])
def test_parsing_never_raises(typed: str) -> None:
    """Whatever was typed, there is a query at the end of it."""
    parsed = parse_tokens(typed)
    assert isinstance(parsed.leaves(), tuple)
    # The modal front-end, on the same input.
    assert isinstance(parse({"q": typed, "tags": typed, "rating": typed}).leaves(), tuple)


@given(QUERY)
@settings(max_examples=400, suppress_health_check=[HealthCheck.too_slow])
def test_deciding_the_values_never_raises(typed: str) -> None:
    """The value parsers are reached with anything the tokenizer let through."""
    for leaf in parse_tokens(typed).leaves():
        if isinstance(leaf, Presence):
            continue
        assert scalar(leaf, now=NOW) is not None


@given(QUERY)
@settings(max_examples=400, suppress_health_check=[HealthCheck.too_slow])
def test_every_condition_a_query_produces_can_be_written_out(typed: str) -> None:
    """Whatever the tokenizer lets through becomes conditions the emitter can write."""
    for leaf in parse_tokens(typed).leaves():
        if isinstance(leaf, Presence) or not isinstance(leaf, Term):
            continue
        sql, bound = AssetFilter(where=scalar(leaf, now=NOW)).predicate()
        assert sql
        for name in bound:
            assert name.startswith("p") or name in {
                "text_match",
                "text_contains",
                "text_likes",
                "text_globs",
                "text_rank",
                "semantic_rank",
                "folder_ids_groups",
                # Bound for every statement, like the groups it goes with.
                "folder_depth_direct",
                # The resume floor, bound once per compile for `viewed:continue`.
                "resume_min_ms",
            }


@given(QUERY)
@settings(max_examples=400, suppress_health_check=[HealthCheck.too_slow])
def test_the_caret_lookup_never_raises(typed: str) -> None:
    """The caret lookup, asked on every keystroke, never raises."""
    found = token_prefix(typed)
    if found is None:
        return
    assert isinstance(found.prefix, str)
    # The offset points inside the text it was given.
    assert 0 <= found.at <= len(typed)


# --- the match expression, executed rather than inspected -------------------------------------


#: One row with every FTS5 operator word and mark, so syntax and text give different answers.
_ROW = 'NEAR OR AND NOT beach_sunset "quoted" a*b ^caret -dash (paren) column:filter'


@pytest.fixture(scope="module")
def index() -> Iterator[sqlite3.Connection]:
    """A real FTS5 table with the real tokenizer."""
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE VIRTUAL TABLE fts USING fts5(asset_id UNINDEXED, filename, path, tags, "
        "people, usernames, tokenize='trigram')"
    )
    connection.execute(
        "INSERT INTO fts VALUES ('one', ?, '/media/one.mp4', 'beach', 'Jane Doe', 'janed')",
        (_ROW,),
    )
    connection.execute(
        "INSERT INTO fts VALUES ('two', 'nothing_alike.mp4', '/media/two.mp4', '', '', '')"
    )
    # A row with no underscore, so escaped and unescaped `_` patterns return different rows.
    connection.execute(
        "INSERT INTO fts VALUES ('three', 'plainname.mp4', '/media/three.mp4', '', '', '')"
    )
    yield connection
    connection.close()


@given(TEXT)
@settings(
    max_examples=400, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
def test_the_match_expression_is_always_executable(index: sqlite3.Connection, text: str) -> None:
    """Whatever the text, FTS5 accepts the expression the parser built."""
    expression = fts_match(text)
    if expression is None:
        return
    index.execute("SELECT asset_id FROM fts WHERE fts MATCH ?", (expression,)).fetchall()


@given(TEXT)
@settings(
    max_examples=400, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
def test_a_match_only_ever_returns_rows_that_really_contain_the_text(
    index: sqlite3.Connection, text: str
) -> None:
    """Every row a match returns contains every term as characters. Terms under three characters
    are skipped: the trigram index cannot hold them."""
    expression = fts_match(text)
    if expression is None:
        return
    # Cleaned by `_printable`, the rule both sides of the search share, so the comparison is with
    # the term that actually went to the database.
    terms = [
        cleaned.casefold()
        for cleaned in (_printable(term) for term in text.split())
        if len(cleaned) >= MIN_TEXT_TERM
    ]
    if not terms:
        return

    rows = index.execute(
        "SELECT filename, path, tags, people, usernames FROM fts WHERE fts MATCH ?",
        (expression,),
    ).fetchall()
    for row in rows:
        blob = " ".join(str(column) for column in row).casefold()
        for term in terms:
            assert term in blob, f"{term!r} matched a row that does not contain it"


def _decode(expression: str) -> list[str]:
    """Read an expression back as FTS5 would: a second implementation, so it can disagree with the
    escaper."""
    terms: list[str] = []
    index = 0
    while index < len(expression):
        assert expression[index] == '"', f"a literal does not start here: {expression[index:]!r}"
        index += 1
        current: list[str] = []
        while True:
            assert index < len(expression), "the expression ended inside a literal"
            if expression[index] == '"':
                if expression[index + 1 : index + 2] == '"':
                    current.append('"')
                    index += 2
                    continue
                index += 1
                break
            current.append(expression[index])
            index += 1
        terms.append("".join(current))
        if index < len(expression):
            assert expression[index : index + 5] == " AND ", "literals are joined by AND"
            index += 5
    return terms


@given(TEXT)
@settings(max_examples=400)
def test_the_expression_decodes_back_to_exactly_what_was_typed(text: str) -> None:
    """The expression decodes back to exactly the terms that went in, and nothing else."""
    expression = fts_match(text)
    if expression is None:
        return

    expected = [
        term
        for term in (_printable(part) for part in text.split())
        if term and len(term) >= MIN_TEXT_TERM
    ]
    assert _decode(expression) == expected


def test_the_escaping_is_doing_something() -> None:
    """A control: the escaping does something, so the properties are not no-ops."""
    # `OR` is two characters, so it goes to the scan rather than the index.
    assert fts_match('NEAR OR "x"') == '"NEAR" AND """x"""'
    assert fts_contains('NEAR OR "x"') == '["OR"]'
    assert fts_match("beach sunset") == '"beach" AND "sunset"'
    assert fts_match("   ") is None
    assert fts_match(None) is None
    # NUL is removed: FTS5 stops reading the expression at it.
    assert fts_match("bea\x00ch") == '"beach"'
    assert fts_match("\x00") is None


# --- the terms the index cannot hold -----------------------------------------------------------


@given(TEXT)
@settings(max_examples=400)
def test_every_term_goes_to_exactly_one_of_the_two_paths(text: str) -> None:
    """Every term goes to exactly one of the two paths; a dropped term would widen the search."""
    indexed = [] if fts_match(text) is None else _decode(fts_match(text) or "")
    short = [] if fts_contains(text) is None else json.loads(fts_contains(text) or "[]")

    words = [term for term in (_printable(part) for part in text.split()) if term]
    assert len(indexed) + len(short) == len(words)
    assert all(len(term) >= MIN_TEXT_TERM for term in indexed)


@given(TEXT)
@settings(
    max_examples=400, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
def test_the_short_term_pattern_is_always_executable(index: sqlite3.Connection, text: str) -> None:
    """Whatever the text, SQLite accepts the fallback's LIKE pattern."""
    encoded = fts_contains(text)
    if encoded is None:
        return
    for term in json.loads(encoded):
        index.execute(
            "SELECT asset_id FROM fts WHERE filename LIKE '%' || ? || '%' ESCAPE '\\'", (term,)
        ).fetchall()


@given(TEXT)
@settings(
    max_examples=400, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
def test_a_short_term_only_matches_rows_that_really_contain_it(
    index: sqlite3.Connection, text: str
) -> None:
    """Every row the short-term pattern returns contains the typed characters."""
    encoded = fts_contains(text)
    if encoded is None:
        return
    words = [
        term
        for term in (_printable(part) for part in text.split())
        if len(term) < MIN_TEXT_TERM and term
    ]

    for term, typed in zip(json.loads(encoded), words, strict=True):
        rows = index.execute(
            "SELECT filename FROM fts WHERE filename LIKE '%' || ? || '%' ESCAPE '\\'", (term,)
        ).fetchall()
        for row in rows:
            assert typed.casefold() in str(row[0]).casefold()


@settings(max_examples=400, suppress_health_check=[HealthCheck.too_slow])
@given(TEXT)
def test_a_filter_that_exists_always_has_a_clause_for_its_words(text: str) -> None:
    """A filter that exists always has a clause for its words, so `_with_neighbours` never builds
    an empty `AllOf`, which would widen to the whole library. `AssetFilter` refuses text nothing
    could match, the condition `_text` reads; this holds the two in agreement."""
    normalised = " ".join(text.split()) or None
    try:
        made = AssetFilter(text=normalised)
    except Exception:
        # Text nothing could match is refused.
        assert fts_match(normalised) is None and fts_contains(normalised) is None
        return
    if made.text is None:
        # No words: `_by_meaning` returns before the union is built.
        return
    assert _text(made.text), made.text
