# SPDX-License-Identifier: AGPL-3.0-or-later
"""The screen's words, read from `data/vocabulary.json`: the Python half of the one reader.

Every copy gate reads its words from this one file, Python through this module and the client
through `frontend/scripts/lib/vocabulary.js`; both compile patterns the same way (case-insensitive
unless an entry says `"case": true`) and prove every entry's own `example`. Not
`src/sift/kernel/vocabulary.py`, which holds the stored record's identifiers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

#: The one file, outside `frontend/src` so nothing imports it into the shipped client.
VOCABULARY = Path(__file__).resolve().parent / "data" / "vocabulary.json"


@dataclass(frozen=True)
class Rule:
    """One counted pattern: what it matches, what to write instead, and a sentence it must catch."""

    list_name: str
    pattern: re.Pattern[str]
    instead: str
    example: str

    def found_in(self, text: str) -> list[str]:
        """Every match in one piece of copy, one per occurrence, which is what a ratchet counts."""
        return [match.group(0) for match in self.pattern.finditer(text)]


@cache
def load() -> dict[str, Any]:
    """The whole file, parsed once per process."""
    loaded: dict[str, Any] = json.loads(VOCABULARY.read_text(encoding="utf-8"))
    return loaded


def compile_pattern(entry: dict[str, Any]) -> re.Pattern[str]:
    """An entry's pattern, compiled as the client compiles it: case-blind unless it says not."""
    flags = 0 if entry.get("case") else re.IGNORECASE
    return re.compile(entry["pattern"], flags)


def rules(list_name: str) -> tuple[Rule, ...]:
    """A pattern list (`retired_words`, `insider_phrases`, `spelling`, `typography`) as rules."""
    return tuple(
        Rule(
            list_name=list_name,
            pattern=compile_pattern(entry),
            instead=entry.get("instead") or entry.get("american") or "",
            example=entry["example"],
        )
        for entry in load()[list_name]
    )


#: The ratcheted word lists, by the name each check is recorded under. `phrasing` is a sentence's
#: shape rather than its words (a stand-in for the plain word, a missing subject, a slogan, over 25
#: words).
RATCHETED = ("retired_words", "insider_phrases", "contractions", "phrasing", "lower_case_names")

#: The word lists held at ZERO: one occurrence anywhere fails, and a ratchet would let `--record`
#: write a regression down as the new number. `paths` is read over the documents too
#: (`test_paths_are_breadcrumbs.py`).
HELD_AT_ZERO = ("spelling", "typography", "paths")


def word_pattern(word: str) -> str:
    """A whole word or phrase, as a pattern: a boundary on each edge that IS a word character.

    `\\b` exists only between word and non-word characters, so an entry ending in punctuation
    ("no. " before an interpolation) would never match. The client's `wordPattern` does the same.
    """
    head = r"\b" if word[:1].isalnum() or word[:1] == "_" else ""
    tail = r"\b" if word[-1:].isalnum() or word[-1:] == "_" else ""
    return f"{head}{re.escape(word)}{tail}"


def wrong_words() -> dict[str, str]:
    """The coined word, and the thing it is a second name for. Hard: zero on every screen."""
    return {entry["word"]: entry["instead"] for entry in load()["wrong_words"]}


def scope(name: str) -> tuple[str, ...]:
    """One named part of the interface, as the path fragments that reach it."""
    found: list[str] = load()["scopes"][name]
    return tuple(found)


def scoped_wrong_words() -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """`(wrong, right, where)` rows: words wrong only in one part of the interface."""
    return tuple(
        (entry["word"], entry["instead"], scope(entry["scope"]))
        for entry in load()["scoped_wrong_words"]
    )


def sign_in_screens() -> tuple[str, ...]:
    return tuple(load()["sign_in"]["screens"])


def sign_in_words() -> tuple[str, ...]:
    return tuple(load()["sign_in"]["words"])


def allowed_sentences() -> tuple[str, ...]:
    return tuple(load()["allowed_sentences"]["phrases"])


def proper_names() -> frozenset[str]:
    return frozenset(load()["proper_names"]["words"])


def proper_phrases() -> tuple[str, ...]:
    return tuple(load()["proper_names"]["phrases"])


#: A word, for judging its capital: a letter or digit, then letters, digits and inner marks.
_A_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\u2019./+-]*")


def is_a_name(word: str) -> bool:
    """Whether a capitalised word may stand after the first in a sentence-case label: a listed name,
    an acronym, or the plural of either (`GIFs`); the stem is two letters or more, so not `As`."""
    names = proper_names()
    if word in names or word.isupper():
        return True
    # A name's possessive is the name: "the Site's name".
    if word.endswith(("'s", "\u2019s")) and len(word) > 2:
        return is_a_name(word[:-2])
    stem = word[:-1]
    return word.endswith("s") and len(stem) >= 2 and (stem in names or stem.isupper())


def not_sentence_case(label: str) -> list[str]:
    """Every word after the first capitalised without being a name; multi-word names (`Photo Set`)
    are taken out first."""
    judged = label
    for phrase in proper_phrases():
        judged = judged.replace(phrase, "")
    return [
        word for word in _A_WORD.findall(judged)[1:] if word[0].isupper() and not is_a_name(word)
    ]


def banned_everywhere() -> tuple[dict[str, Any], ...]:
    """The words no tracked file may hold, comments included. Python patterns: see the file."""
    return tuple(load()["banned_everywhere"]["entries"])


def verbs(where: str = "") -> tuple[str, ...]:
    """Every word or phrase a control's label in `where` may start with; a scoped verb ("End" is
    the swap's) only in its own files."""
    table = load()["verbs"]
    return tuple(
        one["verb"]
        for one in (*table["allowed"], *table["also_allowed"])
        if "scope" not in one or any(fragment in where for fragment in scope(one["scope"]))
    )


def verb_replacements() -> tuple[Rule, ...]:
    return tuple(
        Rule("verbs", compile_pattern(entry), entry["instead"], entry["example"])
        for entry in load()["verbs"]["replaces"]
    )


def starts_with_a_verb(label: str, where: str = "") -> bool:
    """Whether a control's label starts with an agreed verb, case-blind, stepping over leading
    punctuation and an interpolation's gap."""
    words = re.sub(r"^[^A-Za-z]+", "", label).lower()
    for verb in verbs(where):
        lowered = verb.lower()
        if words == lowered or words.startswith(lowered + " ") or words.startswith(lowered + ","):
            return True
    return False


def retired_heading_pairs() -> tuple[tuple[str, str], ...]:
    return tuple((one["old"], one["now"]) for one in load()["retired_headings"])


def every_example() -> list[tuple[str, re.Pattern[str], str]]:
    """`(where, compiled pattern, example)` for every pattern entry, `banned_everywhere`
    included."""
    data = load()
    found: list[tuple[str, re.Pattern[str], str]] = []
    for list_name in (*RATCHETED, *HELD_AT_ZERO):
        for entry in data[list_name]:
            found.append(
                (f"{list_name}: {entry['pattern']}", compile_pattern(entry), entry["example"])
            )
    for entry in data["verbs"]["replaces"]:
        found.append(
            (f"verbs.replaces: {entry['pattern']}", compile_pattern(entry), entry["example"])
        )
    for entry in data["banned_everywhere"]["entries"]:
        found.append(
            (
                f"banned_everywhere: {entry['name']}",
                re.compile(entry["pattern"], re.IGNORECASE),
                entry["example"],
            )
        )
    return found


# --- JUDGING ONE STRING

#: Every ratcheted check; `wrong_words` starts as a ratchet over surfaces the hard gate misses.
CHECKS = ("wrong_words", *RATCHETED)


@cache
def _wrong_word_rules() -> tuple[Rule, ...]:
    return tuple(
        Rule(
            "wrong_words",
            re.compile(word_pattern(word), re.IGNORECASE),
            instead,
            word,
        )
        for word, instead in wrong_words().items()
    )


@cache
def _rules_for(check: str) -> tuple[Rule, ...]:
    return _wrong_word_rules() if check == "wrong_words" else rules(check)


def offences(check: str, text: str) -> list[tuple[str, str]]:
    """`(what matched, what to write instead)` for each occurrence, unless an allowed sentence."""
    if any(allowed in text for allowed in allowed_sentences()):
        return []
    return [(found, rule.instead) for rule in _rules_for(check) for found in rule.found_in(text)]
