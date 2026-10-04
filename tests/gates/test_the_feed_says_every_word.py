# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Settings feed's filters in `ledger.ts` name every verb and kind of thing and nothing else,
and
the client builds no sentence (`say:`, `sentence(`): lines are built on the server by the History
builders (`test_every_act_has_a_sentence_an_icon_and_a_tooltip.py`). Read with a regular expression.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest

from sift.kernel.ledger import VERBS
from sift.kernel.vocabulary import SubjectKind

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
FEED = REPO / "frontend" / "src" / "lib" / "library" / "ledger.ts"

#: One key of a table: the word and a colon at the start of an indented line.
_KEY = re.compile(r"^\t(\w+): ", re.MULTILINE)


def _table(name: str) -> set[str]:
    """The keys of one `Record` in the feed's module."""
    source = FEED.read_text(encoding="utf-8")
    start = source.index(f"export const {name}")
    return set(_KEY.findall(source[start : source.index("\n};", start)]))


def test_the_feed_filters_every_act_and_nothing_else() -> None:
    assert _table("VERBS") == set(VERBS), (
        "the Action filter in frontend/src/lib/library/ledger.ts must name exactly the verbs"
        " kernel/ledger.py can record"
    )


def test_the_feed_filters_every_kind_of_thing_and_nothing_else() -> None:
    assert _table("KINDS") == set(get_args(SubjectKind)), (
        "the Type filter in frontend/src/lib/library/ledger.ts must name exactly the kernel's SubjectKind"
    )


def test_the_browser_builds_no_sentence_of_its_own() -> None:
    """The browser builds no sentence of its own."""
    source = FEED.read_text(encoding="utf-8")
    for built in ("say:", "function sentence(", "subjectFirst", "objectFirst", "actorWords"):
        assert built not in source, f"ledger.ts builds a line again ({built!r})"


def test_the_tables_are_really_being_read() -> None:
    """A reader that found nothing would pass the 'nothing else' halves."""
    assert len(_table("VERBS")) == len(VERBS)
    assert len(_table("KINDS")) == len(get_args(SubjectKind))
