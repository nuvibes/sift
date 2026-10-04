# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one filter engine: two front-ends, one language, one compiler.

**An unknown field is not a token.**

**Anything that cannot mean an asset means NO asset.**
"""

from __future__ import annotations

from sift.slices.search.filter_compiler import (
    FilterCompiler,
    _text,
)
from sift.slices.search.filter_facets import (
    CANDIDATE_CEILING,
    CANDIDATES,
    LEFT_OUT_WAYS,
    OFFERED_VALUES,
    SUGGESTED_FIELDS,
    _between,
    left_out_products,
    problems_in,
    scalar,
)
from sift.slices.search.filter_fields import (
    ALIASES,
    ENTITY_FIELDS,
    EVERYTHING,
    IMPOSSIBLE,
    MAX_DEPTH,
    MAX_NUMBER,
    MAX_TERMS,
    MAX_VALUE,
    Field,
    Group,
    Negated,
    Node,
    Op,
    Presence,
    Query,
    Term,
    group,
)
from sift.slices.search.filter_help import (
    FILTERS,
    Caret,
    FilterHelp,
    Word,
    filters_matching,
    phrase_prefix,
    token_prefix,
    word_prefix,
)
from sift.slices.search.filter_parse import (
    clauses,
    over_budget,
    parse,
    parse_modal,
    parse_tokens,
    write,
)
from sift.slices.search.filter_when import (
    Range,
    presence_word,
)

__all__ = [
    "ALIASES",
    "CANDIDATES",
    "CANDIDATE_CEILING",
    "ENTITY_FIELDS",
    "EVERYTHING",
    "FILTERS",
    "IMPOSSIBLE",
    "LEFT_OUT_WAYS",
    "MAX_DEPTH",
    "MAX_NUMBER",
    "MAX_TERMS",
    "MAX_VALUE",
    "OFFERED_VALUES",
    "SUGGESTED_FIELDS",
    "Caret",
    "Field",
    "FilterCompiler",
    "FilterHelp",
    "Group",
    "Negated",
    "Node",
    "Op",
    "Presence",
    "Query",
    "Range",
    "Term",
    "Word",
    "_between",
    "_text",
    "clauses",
    "filters_matching",
    "group",
    "left_out_products",
    "over_budget",
    "parse",
    "parse_modal",
    "parse_tokens",
    "phrase_prefix",
    "presence_word",
    "problems_in",
    "scalar",
    "token_prefix",
    "word_prefix",
    "write",
]
