# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every act the ledger can record has a SENTENCE, an ICON and a TOOLTIP, read from the builders.

## Why this gate, and why it reads what it reads

Every one of the ledger's verbs can be said on both sides and still wear the wrong mark: a verb
with no mark of its own falls through to the Organize tray's tooltip ("Settled at the workbench,
and it can be taken back"), and a gate reading only a word table cannot see that.

The browser builds no sentence and holds no tooltip, so the three halves are read where they
live, one each, for every verb in `kernel.ledger.VERBS`:

- **the sentence**: `sentences.FEED` has the verb, AND the builders say it: the verb rendered by
  `feed_line` (the feed) and by `event_said` (a page), with a named subject and object, is a line
  with words, no unfilled `{slot}`, and not the fallback a verb nobody wrote falls to ("You changed
  holiday.mp4"). A template row alone is not a sentence: a verb handled before the table is read
  can still say nothing;
- **the icon**: the KIND the verb is drawn as (`history._EVENT_KINDS`, and for `linked` every
  kind a link can be made to) is a kind a History screen declares (`history.KINDS`) and has a glyph
  in the client's one table of marks (`components/common/history.ts`, `MARKS`), which is text read
  as text: a kind with no row there falls through to "info";
- **the tooltip**: that kind has words of its own in `sentences.MEANS`, which is what each line's
  `means` is, and they are not the fallback ("Something happened") nor the Organize tray's.

The same three questions are then asked of every KIND a History screen can draw, which covers the
lines that are not ledger verbs at all (a face scan, a watermark, a copy).

Everything is a function of the tables it is handed, so the self-test plants a verb and a kind and
proves each half refuses it, so the gate cannot pass by reading nothing.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from pathlib import Path

import pytest

from sift.kernel.access import sentences as say
from sift.kernel.access.history import _EVENT_KINDS, _LINKED_KINDS, KINDS
from sift.kernel.ledger import VERBS

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
MARKS_FILE = REPO / "frontend" / "src" / "lib" / "components" / "common" / "history.ts"

#: The subject and object every verb is rendered with: named, so a line that drops a name is a
#: line that says less than it was handed. Names from `data/names_cast.txt`.
FILE = say.thing("asset", "a1", "holiday.mp4")
TAG = say.thing("tag", "t1", "poolside")

#: What a verb nobody wrote falls to, rendered: the template `sentences._SOMETHING` fills.
_FALLBACK = re.compile(r"^\S+ changed ")

#: A slot the template named and nothing filled.
_UNFILLED = re.compile(r"\{\w+\}")

#: A MARK ROW in the client's table: the kind, a colon, a quoted glyph name.
_MARK = re.compile(r"^\s*(\w+):\s*'[a-z0-9_]+'", re.MULTILINE)

#: The words `sentences.means` answers for a kind it has no words for.
_NO_WORDS = say.means("a kind nobody declared")


def client_marks(source: str) -> set[str]:
    """The kinds with a glyph in the client's `MARKS` table, cut between its brace and the next."""
    start = source.index("const MARKS")
    return set(_MARK.findall(source[start : source.index("\n};", start)]))


def sentence_gaps(verbs: Collection[str], feed: Mapping[str, object]) -> list[str]:
    """Every verb whose line is missing, or is the fallback, on the feed or on a page."""
    gaps: list[str] = []
    for verb in sorted(verbs):
        if verb not in feed:
            gaps.append(f"{verb}: no row in sentences.FEED")
            continue
        said = {
            "the feed": say.text_of(
                say.feed_line(
                    verb,
                    by=say.YOU,
                    subjects=[("asset", FILE)],
                    object_kind="tag",
                    object_piece=TAG,
                ).pieces
            ),
            "a page": say.text_of(
                say.event_said(
                    verb,
                    by=say.YOU,
                    here=say.VANTAGE_FILE,
                    object_kind="tag",
                    object_id="t1",
                    object_name="poolside",
                ).pieces
            ),
        }
        for where, words in said.items():
            if not words.strip() or _UNFILLED.search(words) or _FALLBACK.match(words):
                gaps.append(f"{verb}: {where} says {words!r}")
    return gaps


def kinds_of(verbs: Collection[str], event_kinds: Mapping[str, str]) -> dict[str, set[str]]:
    """Every kind each verb can be drawn as. `linked` is drawn by what the link was made TO."""
    return {
        verb: (
            {*_LINKED_KINDS.values(), "added"}
            if verb == "linked"
            else {event_kinds[verb]}
            if verb in event_kinds
            else set()
        )
        for verb in verbs
    }


def icon_gaps(
    verbs: Collection[str],
    event_kinds: Mapping[str, str],
    kinds: Collection[str],
    marks: Collection[str],
) -> list[str]:
    """Every verb drawn as no kind, as a kind no screen declares, or as a kind with no glyph."""
    gaps: list[str] = []
    for verb, drawn in sorted(kinds_of(verbs, event_kinds).items()):
        if not drawn:
            gaps.append(f"{verb}: no kind in history._EVENT_KINDS, so it wears the Organize tray")
        for kind in sorted(drawn):
            if kind not in kinds:
                gaps.append(f"{verb}: drawn as {kind!r}, which history.KINDS does not declare")
            if kind not in marks:
                gaps.append(f"{verb}: drawn as {kind!r}, which has no glyph in MARKS (history.ts)")
    return gaps


def tooltip_gaps(kinds: Collection[str], means: Mapping[str, str]) -> list[str]:
    """Every kind with no words of its own for its mark, or with words that say nothing."""
    gaps: list[str] = []
    for kind in sorted(kinds):
        words = means.get(kind)
        if not words or words == _NO_WORDS:
            gaps.append(f"{kind}: no words in sentences.MEANS, so its tooltip says {_NO_WORDS!r}")
        elif re.search(r"\b(?:workbench|settled)\b", words, re.IGNORECASE):
            gaps.append(f"{kind}: its tooltip is the Organize tray's ({words!r})")
    return gaps


def all_gaps(
    verbs: Collection[str],
    feed: Mapping[str, object],
    event_kinds: Mapping[str, str],
    kinds: Collection[str],
    marks: Collection[str],
    means: Mapping[str, str],
) -> list[str]:
    """The whole gate as one function of the tables, so the self-test can hand it a planted one."""
    drawn = {kind for found in kinds_of(verbs, event_kinds).values() for kind in found}
    return [
        *sentence_gaps(verbs, feed),
        *icon_gaps(verbs, event_kinds, kinds, marks),
        # Every kind a screen declares, and every kind a verb is drawn as: a line that is not a
        # ledger verb (a face scan, a watermark) needs its mark and its words just the same.
        *tooltip_gaps({*kinds, *drawn}, means),
        *(
            f"{kind}: declared in history.KINDS with no glyph in MARKS (history.ts)"
            for kind in sorted(set(kinds) - set(marks))
        ),
    ]


@pytest.fixture(scope="module")
def marks() -> set[str]:
    return client_marks(MARKS_FILE.read_text(encoding="utf-8"))


def test_every_act_has_a_sentence_an_icon_and_a_tooltip(marks: set[str]) -> None:
    gaps = all_gaps(VERBS, say.FEED, _EVENT_KINDS, KINDS, marks, say.MEANS)
    assert not gaps, (
        "\nAn act the ledger can record is missing a sentence, an icon or a tooltip:\n\n  "
        + "\n  ".join(gaps)
        + "\n\nA sentence is a row in sentences.FEED that the builders say; an icon is a kind in"
        "\nhistory._EVENT_KINDS and history.KINDS with a glyph in MARKS (history.ts); a tooltip is"
        "\nthat kind's words in sentences.MEANS.\n"
    )


def test_no_sentence_is_written_for_a_verb_nothing_records() -> None:
    """The other direction: a template nothing writes is a filter that always answers empty."""
    assert set(say.FEED) <= set(VERBS), sorted(set(say.FEED) - set(VERBS))


# --- the gate can fail -----------------------------------------------------------------------------


def test_a_planted_verb_is_refused_on_all_three_halves(marks: set[str]) -> None:
    """A verb added to the ledger and nowhere else fails once for each thing it lacks."""
    planted = "invented_later"
    gaps = all_gaps({*VERBS, planted}, say.FEED, _EVENT_KINDS, KINDS, marks, say.MEANS)
    assert [gap for gap in gaps if gap.startswith(f"{planted}:")] == [
        f"{planted}: no row in sentences.FEED",
        f"{planted}: no kind in history._EVENT_KINDS, so it wears the Organize tray",
    ]
    # Given a row and a kind, and nothing else, it is refused for the glyph and the words, and
    # for the sentence too, because a row handed to the gate is not a line the builders say: they
    # read their own table and fall back to "changed".
    gaps = all_gaps(
        {*VERBS, planted},
        {**say.FEED, planted: say.FEED["hidden"]},
        {**_EVENT_KINDS, planted: planted},
        (*KINDS, planted),
        marks,
        say.MEANS,
    )
    assert sorted(gap for gap in gaps if gap.startswith(f"{planted}:")) == sorted(
        [
            f"{planted}: the feed says 'You changed holiday.mp4'",
            f"{planted}: a page says 'You changed this file'",
            f"{planted}: drawn as {planted!r}, which has no glyph in MARKS (history.ts)",
            f"{planted}: no words in sentences.MEANS, so its tooltip says {_NO_WORDS!r}",
            f"{planted}: declared in history.KINDS with no glyph in MARKS (history.ts)",
        ]
    )


def test_a_verb_the_builders_leave_to_the_fallback_is_refused() -> None:
    """A row in the table is not a sentence: a verb the builders fall through on says "changed"."""
    assert sentence_gaps({"hidden"}, {"hidden": say.FEED["hidden"]}) == []
    refused = sentence_gaps({"invented_later"}, {"invented_later": say.FEED["hidden"]})
    assert [gap.split(" says ")[0] for gap in refused] == [
        "invented_later: the feed",
        "invented_later: a page",
    ]


def test_a_tooltip_that_says_nothing_or_says_the_tray_is_refused() -> None:
    assert tooltip_gaps({"paused"}, {"paused": "Settled at the workbench"}) == [
        "paused: its tooltip is the Organize tray's ('Settled at the workbench')"
    ]
    assert tooltip_gaps({"paused"}, {}) == [
        f"paused: no words in sentences.MEANS, so its tooltip says {_NO_WORDS!r}"
    ]


def test_the_client_marks_are_really_being_read(marks: set[str]) -> None:
    """The canary: a reader that found no marks would refuse everything, and one that found the
    wrong table would pass kinds with no glyph."""
    assert {"added", "deleted", "paused", "song_named"} <= marks
    assert len(marks) >= len(KINDS)
