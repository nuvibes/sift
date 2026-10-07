# SPDX-License-Identifier: AGPL-3.0-or-later
"""The server's copy, held to the screen's words as a ratchet: per file, a count may only fall.

## What this reads and why it is a ratchet

`server_copy.py` finds every string the server writes for a person: History sentences, Organize
cards, task names, tidy titles, download failures, refusals, record labels and the settings
registry's words. `vocabulary.py` supplies the words they are judged against, from the one file
every copy gate reads. Four checks counted per file, and two held at zero:

- `wrong_words`: the one-word-per-thing map (fetch, account, handle ...). Zero on every client
  screen the hard gate reads; on the server it is a count.
- `retired_words`: the words the agreed vocabulary replaced (this machine, processor, job, pass,
  Tonight, no. ...).
- `insider_phrases`: words from inside the machinery rather than from the reader's side (we, the
  user, walked past, workbench, pile, settle ...) and the hedges that add nothing.
- `contractions`: the uncontracted forms a person would not say ("cannot", "does not", "it is"
  ...), each naming its contraction. What remains is allowed on purpose: "cannot" in a legal or
  security statement of impossibility, a warning that opens "It is". A History line is not read by
  this check (`NOT_ON_HISTORY_LINES`).
- `spelling`: British spellings, where the interface is spelled the American way. NOT a ratchet:
  it is zero on both sides, so it is held at zero with no baseline
  (`vocabulary.HELD_AT_ZERO`), over this copy, and over the desktop shell's strings, which no
  other copy reader reaches.
- `typography`: three full stops where the ellipsis character belongs. Held at zero over this
  copy, the same list the client's reader holds.

A ratchet rather than a ban because the rewrite that brings each count to zero is several pieces of
work and words come back while a rewrite is in progress: a retired name written once is copied
into hundreds of places. So today's count per file is recorded in
`data/copy_vocabulary_baseline.json`, a rise fails, and a fall ALSO fails until it is recorded, so
the recorded number is always the true one and progress cannot be given back:

    python -m tests.gates.test_copy_vocabulary --record

records every fall and refuses to record a rise. A file with no entry is held at zero.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections import Counter
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from tests.gates import server_copy, vocabulary

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
#: The dash the dash check refuses, planted into the specimens below.
DASH = " -- "
BASELINE = Path(__file__).resolve().parent / "data" / "copy_vocabulary_baseline.json"
DESKTOP = REPO / "desktop" / "src"

#: What the baseline file says about itself, rewritten on every record so it cannot go stale.
_NOTE = (
    "Server copy, counted per check per file against tests/gates/data/vocabulary.json. Each number "
    "may only FALL. Record a fall with: python -m tests.gates.test_copy_vocabulary --record"
)


#: The checks a History line is not judged by. A History line's words follow the History word
#: table (`test_history_says_the_word_table.py`), which writes a recorded act the way a record reads
#: ("Sift cannot download from ..."), so the screen's contractions rule does not reach them.
#: Captions only: an Insights statement is read by the History word table too, and is a sentence
#: somebody reads as one, so it keeps this rule (`server_copy.STATEMENT_MODULES`).
NOT_ON_HISTORY_LINES = frozenset({"contractions"})


def judged(check: str, one: server_copy.Copy) -> list[tuple[str, str]]:
    """`vocabulary.offences` for one piece of copy, less a check its module is not judged by."""
    if check in NOT_ON_HISTORY_LINES and server_copy.says_captions(one.path):
        return []
    return vocabulary.offences(check, one.text)


def counts(copy: list[server_copy.Copy]) -> dict[str, dict[str, int]]:
    """`{check: {file: occurrences}}`, files with none left out."""
    found: dict[str, Counter[str]] = {check: Counter() for check in vocabulary.CHECKS}
    for one in copy:
        for check in vocabulary.CHECKS:
            hits = len(judged(check, one))
            if hits:
                found[check][one.path] += hits
    return {check: dict(sorted(per_file.items())) for check, per_file in found.items()}


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


def held_at_zero(copy: list[server_copy.Copy]) -> list[str]:
    """One printable line per occurrence of a check held at zero: there is no allowance to read."""
    return [
        f"    {one.path}:{one.line}  {found!r} -> {instead}\n        {one.text.strip()[:100]}"
        for check in vocabulary.HELD_AT_ZERO
        for one in copy
        for found, instead in vocabulary.offences(check, one.text)
    ]


#: A template literal's substitution is code: `${normalised}` is a variable, not a word on screen.
_SUBSTITUTION = re.compile(r"\$\{[^}]*\}")


def desktop_spelling(text: str) -> list[tuple[int, str, str]]:
    """`(line, found, instead)` for every British spelling in one desktop shell module's strings.

    The shell's dialogs, tray menu and first-run words are read by neither copy reader, so they are
    read here through the dash check's extractor: every quoted string, less comments and log calls.
    """
    return [
        (line, found, instead)
        for line, said in _dash_script().desktop_strings(text)
        for found, instead in vocabulary.offences("spelling", _SUBSTITUTION.sub(" ", said))
    ]


def _recorded() -> dict[str, dict[str, int]]:
    data: dict[str, dict[str, int]] = json.loads(BASELINE.read_text(encoding="utf-8"))
    return {check: data.get(check, {}) for check in vocabulary.CHECKS}


@pytest.fixture(scope="module")
def today() -> tuple[list[server_copy.Copy], dict[str, dict[str, int]]]:
    copy = server_copy.server_copy()
    return copy, counts(copy)


def test_the_reader_finds_the_server_copy(
    today: tuple[list[server_copy.Copy], dict[str, dict[str, int]]],
) -> None:
    """A reader that found nothing would hold every count at zero and pass forever."""
    copy, _ = today
    assert len(copy) > 1500, f"only {len(copy)} server strings found, which cannot be right"
    assert len({one.path for one in copy}) > 150


@pytest.mark.parametrize("check", vocabulary.CHECKS)
def test_no_file_says_more_of_it_than_was_recorded(
    check: str, today: tuple[list[server_copy.Copy], dict[str, dict[str, int]]]
) -> None:
    copy, now = today
    rises, falls = compare(now[check], _recorded()[check])
    if rises:
        risen = {line.split(":", 1)[0] for line in rises}
        where = [
            f"    {one.path}:{one.line}  {found!r} -> {instead}\n        {one.text.strip()[:100]}"
            for one in copy
            if one.path in risen
            for found, instead in judged(check, one)
        ]
        pytest.fail(
            f"\n{check}: a file says more of it than was recorded.\n\n  "
            + "\n  ".join(rises)
            + "\n\n"
            + "\n".join(where[:40])
            + "\n\nUse the agreed word (tests/gates/data/vocabulary.json says which).\n"
        )
    assert not falls, (
        f"\n{check}: the count FELL. Record it so it cannot be given back:\n"
        "    python -m tests.gates.test_copy_vocabulary --record\n\n  " + "\n  ".join(falls)
    )


def test_no_server_copy_says_what_is_held_at_zero(
    today: tuple[list[server_copy.Copy], dict[str, dict[str, int]]],
) -> None:
    copy, _ = today
    found = held_at_zero(copy)
    assert not found, (
        f"\n{len(found)} in server copy of a check held at zero (a British spelling, or three"
        " full stops for an ellipsis):\n\n"
        + "\n".join(found[:40])
        + "\n\nWrite what tests/gates/data/vocabulary.json says instead.\n"
    )


def test_nothing_held_at_zero_is_recorded() -> None:
    """A number written beside a held check would read as an allowance."""
    recorded = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert not set(recorded) & set(vocabulary.HELD_AT_ZERO), (
        f"{BASELINE.name} records a check held at zero; delete its entry"
    )


def test_the_desktop_shell_is_spelled_the_american_way() -> None:
    found = [
        f"    {path.relative_to(REPO).as_posix()}:{line}  {word!r} -> {instead}"
        for path in sorted(DESKTOP.rglob("*.ts"))
        if ".test." not in path.name
        for line, word, instead in desktop_spelling(path.read_text(encoding="utf-8"))
    ]
    assert not found, "British spelling in the desktop shell's strings:\n" + "\n".join(found)


# --- the checks can fail ---------------------------------------------------------------------------


def test_british_spelling_is_held_at_zero_not_counted() -> None:
    """A known positive through the whole path, and spelling kept out of the ratcheted checks."""
    copy = server_copy.copy_in(
        'register_setting(key="a", label="Accent colour")\n', "src/sift/x.py"
    )
    assert len(held_at_zero(copy)) == 1
    assert "spelling" not in vocabulary.CHECKS


def test_three_full_stops_are_held_at_zero_not_counted() -> None:
    """The ellipsis list through the whole path: read out of server source, held at zero, and kept
    out of the ratcheted checks: the same list the client's reader holds."""
    copy = server_copy.copy_in('register_setting(key="a", label="Loading...")\n', "src/sift/x.py")
    assert len(held_at_zero(copy)) == 1
    written = server_copy.copy_in(
        'register_setting(key="a", label="Loading\\u2026")\n', "src/sift/x.py"
    )
    assert held_at_zero(written) == []
    assert "typography" not in vocabulary.CHECKS


def test_the_desktop_spelling_check_reads_words_but_not_code() -> None:
    shell = (
        "/* a colour in a comment */\n"
        "log.info('a colour in a log line');\n"
        "const detail = `Sift could not reach ${normalised}.`;\n"
        "dialog({ message: 'Choose a colour for the title bar.' });\n"
    )
    assert desktop_spelling(shell) == [(4, "colour", "color")]


def test_every_vocabulary_pattern_matches_its_own_example() -> None:
    """The file's own known positives. The client proves the same examples with its own regex
    engine, so a pattern the two sides read differently fails on one of them."""
    missed = [
        where
        for where, pattern, example in vocabulary.every_example()
        if not pattern.search(example)
    ]
    assert not missed, "these patterns no longer match their own example:\n  " + "\n  ".join(missed)


def test_the_reader_finds_copy_by_every_rule() -> None:
    """One known positive per rule, and the three things it must never read."""
    source = '''
"""A module docstring about the job queue."""
register_handler("thumb", make, name="Making what one file lacks")
register_setting(key="a", label="Graphics card", disclosure=f"Uses {n} jobs at the same time.")
class Tidy:
    title = "Files Sift would not take"
KEPT_LOCAL = "Kept local, nothing was sent outside this machine"
QUERY = "SELECT id FROM jobs WHERE state = 'queued'"
raise OrganizeRefused("That job is already running.")
raise ValueError("an internal message about a job")
log.info("a job started in the background")
other(name="thumbnail")
'''
    found = {(one.via, one.text) for one in server_copy.copy_in(source, "src/sift/x.py")}
    assert ("name", "Making what one file lacks") in found
    assert ("label", "Graphics card") in found
    assert ("disclosure", "Uses   jobs at the same time.") in found
    assert ("assign:title", "Files Sift would not take") in found
    assert ("assign:KEPT_LOCAL", "Kept local, nothing was sent outside this machine") in found
    assert ("refusal:OrganizeRefused", "That job is already running.") in found
    texts = {text for _via, text in found}
    assert not any("docstring" in text for text in texts)
    assert not any(text.startswith("SELECT") for text in texts)
    assert "an internal message about a job" not in texts
    assert "a job started in the background" not in texts
    assert "thumbnail" not in texts


def test_the_reader_finds_a_running_task_s_note_in_every_shape_it_is_handed() -> None:
    """A task's progress note is drawn on Activity, so it is copy: three shapes, one known
    positive each, and the helper's own docstring still left alone."""
    source = """
def _swept(*, done):
    \"\"\"A docstring about the sweep.\"\"\"
    if not done:
        return f"{n} queued so far, still going through the library..."
    return "Everything has already been scanned."

async def sweep(context):
    await context.set_note(_swept(done=False))
    await context.set_note("Nothing was ticked, so there is nothing to build.")
    said = "A newer version is out."
    await context.set_note(said)
"""
    found = {(one.via, one.text) for one in server_copy.copy_in(source, "src/sift/x.py")}
    assert ("note:_swept", "  queued so far, still going through the library...") in found
    assert ("note:_swept", "Everything has already been scanned.") in found
    assert ("note", "Nothing was ticked, so there is nothing to build.") in found
    assert ("note:said", "A newer version is out.") in found
    assert not any("docstring" in text for _via, text in found)
    # And the planted three full stops reach the check that holds them at zero.
    held = held_at_zero(server_copy.copy_in(source, "src/sift/x.py"))
    assert len(held) == 1 and "still going through the library" in held[0]


def test_a_copy_module_is_read_whole_except_its_keys_and_comparisons() -> None:
    source = """
TABLE = {"http-404": "Sift fetched nothing from {site}"}
def said(code):
    if code == "Not a sentence at all":
        return f"Sift could not fetch {code} today"
"""
    texts = {one.text for one in server_copy.copy_in(source, server_copy.COPY_MODULES[0])}
    assert "Sift fetched nothing from {site}" in texts  # a placeholder is a gap, not code
    assert "Sift could not fetch   today" in texts
    assert "http-404" not in texts
    assert "Not a sentence at all" not in texts


def test_each_check_counts_a_known_positive() -> None:
    assert vocabulary.offences("wrong_words", "Sift fetched this file from the web")
    assert vocabulary.offences("retired_words", "Share of the machine to use")
    assert vocabulary.offences("insider_phrases", "Settled at the workbench")
    assert vocabulary.offences("spelling", "Accent colour")
    assert vocabulary.offences("typography", "still going through the library...")
    assert vocabulary.offences("typography", "still going through the library\u2026") == []
    assert vocabulary.offences("typography", "Tasks at the same time 12 -> 16")
    assert vocabulary.offences("typography", "Tasks at the same time 12 \u2192 16") == []
    # And the agreed words pass.
    for check in (*vocabulary.CHECKS, *vocabulary.HELD_AT_ZERO):
        assert vocabulary.offences(check, "Share of this device to use. Accent color.") == [], check
    # A sentence argued for is never an offence.
    assert vocabulary.offences("wrong_words", "the creators are filed as studios") == []


def test_the_contractions_check_reads_words_a_person_says_and_nothing_else() -> None:
    """A known positive per shape, the contraction named, and the three places it must not fire:
    SQL's IS NOT, an "it is" that ends a sentence (it's cannot stand there), and a History line."""
    assert "contractions" in vocabulary.CHECKS, "the check left the ratchet: nothing counts it"
    assert vocabulary.offences("contractions", "Sift cannot reach the folder") == [
        ("cannot", "can't")
    ]
    assert vocabulary.offences("contractions", "Could not load the log.") == [
        ("Could not", "couldn't")
    ]
    assert vocabulary.offences("contractions", "It is already in the library") == [
        ("It is", "it's")
    ]
    # "It is not" is one offence, the "is not", not two.
    assert [found for found, _ in vocabulary.offences("contractions", "It is not saved")] == [
        "is not"
    ]
    assert vocabulary.offences("contractions", "WHEN seen IS NOT NULL THEN 1") == []
    assert vocabulary.offences("contractions", "Leave it where it is.") == []
    assert vocabulary.offences("contractions", "Sift can't reach the folder") == []
    history = server_copy.Copy(server_copy.HISTORY_MODULES[0], 1, "Sift cannot download it", "line")
    screen = server_copy.Copy("src/sift/x.py", 1, "Sift cannot download it", "detail")
    assert judged("contractions", history) == []
    assert judged("contractions", screen) == [("cannot", "can't")]
    # Only this check stands aside for a History line; the others still read it.
    assert judged("retired_words", server_copy.Copy(history.path, 1, "a job", "line"))
    # And it stands aside for a CAPTION only. An Insights statement is read by the History word
    # table and is still a sentence: "That is 12 hours fewer than July." is an offence there.
    statement = server_copy.Copy(
        server_copy.STATEMENT_MODULES[0], 1, "That is 12 hours fewer than July.", "line"
    )
    assert server_copy.is_history_module(statement.path)
    assert judged("contractions", statement) == [("That is", "that's")]


def test_a_wrong_word_ending_in_punctuation_is_found_where_it_is_written() -> None:
    """ "no. " is a wrong word for a username's ID on its Site. What follows it on screen is the
    number (an interpolation, which the readers replace with a space or drop),
    so a boundary AFTER the entry never exists and the entry would hold nothing. The edge that is
    punctuation is its own boundary (`vocabulary.word_pattern`)."""
    assert vocabulary.offences("wrong_words", "3 files - no.  ")
    assert vocabulary.offences("wrong_words", "3 files - no. 31415926")
    assert vocabulary.offences("wrong_words", "The Site's own number: ")
    # A word inside another word is still not the word.
    assert vocabulary.offences("wrong_words", "Casino. Then") == []
    assert vocabulary.offences("wrong_words", "3 files - ID 31415926") == []


def test_the_ratchet_sees_a_rise_and_a_fall() -> None:
    rises, falls = compare({"a.py": 3, "b.py": 1}, {"a.py": 2, "c.py": 4})
    assert rises == ["a.py: 3, recorded 2", "b.py: 1, recorded 0"]
    assert falls == ["c.py: 0, recorded 4"]
    assert compare({"a.py": 2}, {"a.py": 2}) == ([], [])


# --- the dash check over server copy ------------------------------------------------------------


@cache
def _dash_script() -> Any:
    """`scripts/check_display_dashes.py`, loaded by path: it is a hook script, not a package."""
    path = REPO / "scripts" / "check_display_dashes.py"
    spec = importlib.util.spec_from_file_location("_display_dashes_widened", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_dash_check_reads_server_copy_the_ten_names_never_reached() -> None:
    """A download failure's sentence reaching the screen through a constant is read for the dash."""
    dashes = _dash_script()
    source = (
        f'_FAILED = "The download could not be completed. This is often temporary{DASH}try it again."\n'
        f'refuse(detail="Already held at zero{DASH}by the hard check.")\n'
    )
    found = dashes.widened_server(server_copy.copy_in(source, "src/sift/x.py"))
    assert [text for _path, _line, text in found] == [
        f"The download could not be completed. This is often temporary{DASH}try it again."
    ]


def test_the_dash_check_reads_the_desktop_shell_but_not_its_logs_or_comments() -> None:
    dashes = _dash_script()
    shell = (
        f"/* a comment{DASH}about the code */\n"
        f"log.info('a log line{DASH}for the log file');\n"
        f"const detail = `You can set Sift up again{DASH}which asks where the library is kept.`;\n"
        "dialog({ message: 'Nothing was lost" + DASH + "it is still using the folder it was.' });\n"
    )
    assert [line for line, _text in dashes.desktop_dashes(shell)] == [4, 3]


# --- recording -------------------------------------------------------------------------------------


def _record() -> int:
    """Write every fall; refuse (exit 1) while anything has risen."""
    now = counts(server_copy.server_copy())
    recorded = _recorded() if BASELINE.exists() else {check: {} for check in vocabulary.CHECKS}
    rose = [
        f"{check} {line}"
        for check in vocabulary.CHECKS
        for line in compare(now[check], recorded[check])[0]
    ]
    if rose and BASELINE.exists():
        print(  # nosemgrep: sift-no-print-or-raw-logger (the record tool's own answer)
            "Not recorded: these ROSE, and a rise is fixed, not recorded:\n  " + "\n  ".join(rose)
        )
        return 1
    BASELINE.write_text(
        json.dumps({"_note": _NOTE, **now}, indent="\t") + "\n", encoding="utf-8", newline="\n"
    )
    totals = ", ".join(f"{check} {sum(now[check].values())}" for check in vocabulary.CHECKS)
    print(f"recorded {BASELINE.name}: {totals}")  # nosemgrep: sift-no-print-or-raw-logger
    return 0


if __name__ == "__main__":
    sys.exit(_record() if "--record" in sys.argv else 2)
