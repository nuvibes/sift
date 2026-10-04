# SPDX-License-Identifier: AGPL-3.0-or-later
"""The History ratchets: three counts over the words History can say, per file, that may only fall.

Today's count per file is in `data/history_ratchets.json`: a rise fails, a file with no entry is
held at zero, and a fall fails until recorded, so the number stays true:

    python -m tests.gates.history_ratchets --record

- `history_words`: the History word table's wrong words (`history_screens` in
  `data/vocabulary.json`) in everything the source can put on a History screen. Read by
  `test_history_says_the_word_table.py`.
- `unnamed_words`: "a file", "something", "someone", "somebody", where the name may be known.
- `raw_figures`: a size said in bytes. Both read by `test_history_names_what_it_knows.py`.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

from tests.gates import server_copy, vocabulary

BASELINE = Path(__file__).resolve().parent / "data" / "history_ratchets.json"

#: What the baseline says about itself, rewritten on every record so it cannot go stale.
_NOTE = (
    "History words counted per file (tests/gates/history_ratchets.py). Each number may only FALL. "
    "Record a fall with: python -m tests.gates.history_ratchets --record"
)

#: The words that stand in for a thing instead of naming it. See the module header.
UNNAMED = re.compile(r"\b(?:a file|something|someone|somebody)\b", re.IGNORECASE)


def history_word_patterns() -> list[tuple[re.Pattern[str], str, str]]:
    """`(pattern, wrong, instead)` for every word the History word table retired."""
    return [
        (re.compile(vocabulary.word_pattern(word), re.IGNORECASE), word, instead)
        for word, instead, where in vocabulary.scoped_wrong_words()
        if where == vocabulary.scope("history_screens")
    ]


def history_words_in(text: str) -> list[tuple[str, str]]:
    """`(found, instead)` for every retired History word in one piece of copy."""
    return [
        (match.group(0), instead)
        for pattern, _word, instead in history_word_patterns()
        for match in pattern.finditer(text)
    ]


def unnamed_words_in(text: str) -> list[tuple[str, str]]:
    """`(found, instead)` for every word standing in for a thing in one piece of copy."""
    return [
        (match.group(0), "the thing's name, or the line in the passive")
        for match in UNNAMED.finditer(text)
    ]


#: A SIZE SAID AS A RAW FIGURE: "That freed 123456789 bytes", a saved detail.
BYTES = re.compile(r"\bbytes\b", re.IGNORECASE)


def raw_figures_in(text: str) -> list[tuple[str, str]]:
    """`(found, instead)` for every size a History line would say in bytes."""
    return [(match.group(0), "a size in KB, MB or GB") for match in BYTES.finditer(text)]


#: Each ratchet: its name in the baseline, and what it finds in one piece of copy.
CHECKS: dict[str, Callable[[str], list[tuple[str, str]]]] = {
    "history_words": history_words_in,
    "unnamed_words": unnamed_words_in,
    "raw_figures": raw_figures_in,
}


def counts(copy: Sequence[server_copy.Copy], check: str) -> dict[str, int]:
    """`{file: occurrences}` of one check, files with none left out."""
    found: Counter[str] = Counter()
    for one in copy:
        hits = len(CHECKS[check](one.text))
        if hits:
            found[one.path] += hits
    return dict(sorted(found.items()))


def where(copy: Sequence[server_copy.Copy], check: str, paths: set[str]) -> list[str]:
    """One printable line per occurrence of a check in the named files."""
    return [
        f"    {one.path}:{one.line}  {found!r} -> {instead}\n        {one.text.strip()[:100]}"
        for one in copy
        if one.path in paths
        for found, instead in CHECKS[check](one.text)
    ]


def compare(now: dict[str, int], recorded: dict[str, int]) -> tuple[list[str], list[str]]:
    """`(rises, falls)` between a check's counts today and its recorded ones, as printable lines."""
    rises, falls = [], []
    for path in sorted(set(now) | set(recorded)):
        today, allowed = now.get(path, 0), recorded.get(path, 0)
        if today > allowed:
            rises.append(f"{path}: {today}, recorded {allowed}")
        elif today < allowed:
            falls.append(f"{path}: {today}, recorded {allowed}")
    return rises, falls


def recorded(check: str) -> dict[str, int]:
    """One check's recorded counts. A check with no entry holds every file at zero."""
    data: dict[str, dict[str, int]] = json.loads(BASELINE.read_text(encoding="utf-8"))
    return data.get(check, {})


def held(check: str, copy: Sequence[server_copy.Copy]) -> str | None:
    """The failure for one check today, or None: a rise names each occurrence, a fall asks to be
    recorded."""
    now = counts(copy, check)
    rises, falls = compare(now, recorded(check))
    if rises:
        risen = {line.split(":", 1)[0] for line in rises}
        return (
            f"\n{check}: a file says more of it than was recorded.\n\n  "
            + "\n  ".join(rises)
            + "\n\n"
            + "\n".join(where(copy, check, risen)[:40])
            + "\n"
        )
    if falls:
        return (
            f"\n{check}: the count FELL. Record it so it cannot be given back:\n"
            "    python -m tests.gates.history_ratchets --record\n\n  " + "\n  ".join(falls)
        )
    return None


def _record() -> int:
    """Write every fall; refuse (exit 1) while anything has risen."""
    copy = server_copy.history_copy()
    now = {check: counts(copy, check) for check in CHECKS}
    if BASELINE.exists():
        rose = [
            f"{check} {line}"
            for check in CHECKS
            for line in compare(now[check], recorded(check))[0]
        ]
        if rose:
            print(  # nosemgrep: sift-no-print-or-raw-logger (the record tool's own answer)
                "Not recorded: these ROSE, and a rise is fixed, not recorded:\n  "
                + "\n  ".join(rose)
            )
            return 1
    BASELINE.write_text(
        json.dumps({"_note": _NOTE, **now}, indent="\t") + "\n", encoding="utf-8", newline="\n"
    )
    totals = ", ".join(f"{check} {sum(now[check].values())}" for check in CHECKS)
    print(f"recorded {BASELINE.name}: {totals}")  # nosemgrep: sift-no-print-or-raw-logger
    return 0


if __name__ == "__main__":
    sys.exit(_record() if "--record" in sys.argv else 2)
