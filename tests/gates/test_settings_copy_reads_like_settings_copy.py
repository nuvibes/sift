# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every setting's label and help follow the rules a settings screen is written to.

The registry's strings are declared across the slices and each is right on its own; what drifts is
the SHAPE, which no single file shows. The rules: a label is at most six words,
sentence case, and not a sentence; help is at most two sentences and 34 words, with anything longer
in `disclosure`, which the row folds away; help never talks about the control itself. Second person
is not checked: most sentences rightly have Sift as their subject.
"""

from __future__ import annotations

import re

import pytest

import sift.main  # noqa: F401 (imported for its side effect: every slice registers its settings)
from sift.kernel.settings_registry import registered_settings
from tests.gates import vocabulary

pytestmark = [pytest.mark.gate, pytest.mark.unit]

#: The most words a label may have.
LABEL_WORDS = 6

#: Labels allowed past that, each carrying meaning a shorter one would leave to the help: the app
#: lock's "on LAN", both halves of making people from facial fingerprints, and the step back while
#: the device is in use.
LABELS_ALLOWED_LONGER: frozenset[str] = frozenset(
    {"vault.app_lock_enabled", "faces.people_from_files", "performance.step_back_while_used"}
)

#: The most a help string may be, in sentences and in words.
HELP_SENTENCES = 2
HELP_WORDS = 34

#: Words capitalised wherever they appear: Sift's own nouns, brands, formats and acronyms
#: (`proper_names` in `data/vocabulary.json`). Anything else capitalised mid-label is a label to
#: rewrite.
PROPER: frozenset[str] = vocabulary.proper_names()

#: Names of more than one word, taken out of a label before its words are judged: "Photo Set" is
#: one of Sift's objects, and neither "Photo" nor "Set" is a name alone.
PROPER_PHRASES: tuple[str, ...] = vocabulary.proper_phrases()

#: Ways of writing about the control instead of about what it does.
ABOUT_ITSELF = (
    "this setting",
    "this option",
    "this preference",
    "this checkbox",
    "this toggle",
    "use this to",
)

#: Ways of naming a clock format, which no setting's help may do: `TimeField` shows the clock the
#: READER's own device shows, so "Twenty-four hour clock" beside it would contradict the field.
CLOCK_FORMATS = (
    "twenty-four hour",
    "twentyfour hour",
    "24-hour",
    "24 hour",
    "twelve-hour",
    "12-hour",
    "12 hour",
    "am/pm",
    "a.m./p.m.",
)

#: A sentence end followed by a space or the string's end: a version number carries a `.` too.
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")

#: A word for counting. The typographic apostrophe is an escape: ruff's RUF001 refuses an ambiguous
#: character in a string literal.
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\u2019./+-]*")


def _sentences(text: str) -> int:
    return len(_SENTENCE_END.findall(text.strip()))


def _words(text: str) -> list[str]:
    return _WORD.findall(text)


def _drawn() -> list[tuple[str, str, str]]:
    """Every setting a person can read, as (key, label, help), `updates.dismissed_version` included:
    its words reach a reader through the API and a settings search."""
    return [(key, one.label, one.help) for key, one in sorted(registered_settings().items())]


def test_there_are_settings_to_check() -> None:
    """A registry that failed to import reads like a registry with nothing wrong in it."""
    drawn = _drawn()
    assert len(drawn) > 100, f"only {len(drawn)} settings were registered, which cannot be right"


def test_a_label_is_short() -> None:
    too_long = [
        f"{key}: {label!r} ({len(_words(label))} words)"
        for key, label, _ in _drawn()
        if len(_words(label)) > LABEL_WORDS and key not in LABELS_ALLOWED_LONGER
    ]
    assert not too_long, (
        f"A settings label is at most {LABEL_WORDS} words. A label needing more is answering the "
        "question its help is for.\n\n" + "\n".join(f"  {one}" for one in too_long)
    )


def _is_a_name(word: str) -> bool:
    """Whether a capitalised word may stand after the first one: a listed name, an acronym, or the
    plural of either (`Save GIFs as`). The stem must be two letters or more, so `As` and `Is` are
    not acronym plurals."""
    if word in PROPER or word.isupper():
        return True
    stem = word[:-1]
    return word.endswith("s") and len(stem) >= 2 and (stem in PROPER or stem.isupper())


def test_a_label_is_sentence_case() -> None:
    """One initial capital, and after that only words that are capitals everywhere."""
    shouting = []
    for key, label, _ in _drawn():
        # `vocabulary.not_sentence_case` is what the Organize queues' gate reads too.
        for word in vocabulary.not_sentence_case(label):
            shouting.append(f"{key}: {label!r} ({word!r})")
    assert not shouting, (
        "A settings label is sentence case: one initial capital, and after that only names.\n"
        "If one of these IS a name, add it to PROPER with the reason.\n\n"
        + "\n".join(f"  {one}" for one in shouting)
    )


def test_a_plural_acronym_is_a_name_and_a_plural_word_is_not() -> None:
    """`_is_a_name`'s plural rule, with the cases that must still FAIL beside those that pass."""
    assert _is_a_name("GIF") and _is_a_name("GIFs")
    assert _is_a_name("ZIPs") and _is_a_name("Sites")
    assert not _is_a_name("Sets")
    assert not _is_a_name("Photo")
    assert "Photo Sets" in PROPER_PHRASES and "Sets" not in PROPER
    assert not _is_a_name("GIFt")
    assert not _is_a_name("As")
    assert not _is_a_name("Is")


def test_a_label_is_not_a_sentence() -> None:
    punctuated = [
        f"{key}: {label!r}"
        for key, label, _ in _drawn()
        if label.rstrip().endswith((".", "?", "!"))
    ]
    assert not punctuated, (
        "A settings label is a name, not a sentence, so it carries no closing punctuation.\n\n"
        + "\n".join(f"  {one}" for one in punctuated)
    )


def test_help_is_at_most_two_sentences() -> None:
    long_winded = [
        f"{key}: {_sentences(text)} sentences: {text[:80]}..."
        for key, _, text in _drawn()
        if _sentences(text) > HELP_SENTENCES
    ]
    assert not long_winded, (
        f"A setting's help is at most {HELP_SENTENCES} sentences. Anything past that is reference "
        "material and belongs in `disclosure`, which the row folds away.\n\n"
        + "\n".join(f"  {one}" for one in long_winded)
    )


def test_help_is_short() -> None:
    long_winded = [
        f"{key}: {len(_words(text))} words: {text[:80]}..."
        for key, _, text in _drawn()
        if len(_words(text)) > HELP_WORDS
    ]
    assert not long_winded, (
        f"A setting's help is at most {HELP_WORDS} words. Move the rest to `disclosure`.\n\n"
        + "\n".join(f"  {one}" for one in long_winded)
    )


def test_help_says_what_it_does_not_what_it_is() -> None:
    """ "This setting lets you..." describes the control somebody is already looking at."""
    navel_gazing = [
        f"{key}: {phrase!r} in {text[:70]}..."
        for key, _, text in _drawn()
        for phrase in ABOUT_ITSELF
        if phrase in text.lower()
    ]
    assert not navel_gazing, (
        "Help says what happens, not what the control is. The reader can see the control.\n\n"
        + "\n".join(f"  {one}" for one in navel_gazing)
    )


def test_help_does_not_name_a_clock_format() -> None:
    """A time of day is shown in the reader's own clock, so the help cannot promise one."""
    prescribing = [
        f"{key}: {phrase!r} in {text[:70]}..."
        for key, _, text in _drawn()
        for phrase in CLOCK_FORMATS
        if phrase in text.lower()
    ]
    assert not prescribing, (
        "A time of day is drawn by `TimeField`, which shows the reader's own clock, so help "
        "naming a clock format contradicts the field beside it.\n\n"
        + "\n".join(f"  {one}" for one in prescribing)
    )


def test_the_checks_can_actually_fail() -> None:
    """Each rule in front of a string whose answer is known: `1.3 GB` is one sentence, not three."""
    assert len(_words("Stop when free space drops below")) == 6
    assert len(_words("Stop when free space drops below this much")) == 8

    assert _sentences("One sentence.") == 1
    assert _sentences("One. Two.") == 2
    assert _sentences("It is about 1.3 GB and is fetched once.") == 1
    assert _sentences("No full stop at all") == 0

    assert "this setting" in "This setting lets you...".lower()
    assert "this setting" not in "Sift skips a link it has already fetched.".lower()

    assert any(
        one in "Twenty-four hour clock, in this machine's own time.".lower()
        for one in CLOCK_FORMATS
    )
    assert not any(one in "In this machine's own time.".lower() for one in CLOCK_FORMATS)


# --- THE FOLDED-AWAY DETAIL (`disclosure`)
#
# Reference material, so the help's two-sentence ceiling does not apply; the voice rules do, it is
# written in sentences, and a ceiling keeps it from becoming a manual.

#: A ceiling at the longest disclosure, the duplicate-finding explanation. Past it, the detail
#: belongs on a page of its own.
DISCLOSURE_SENTENCES = 4
DISCLOSURE_WORDS = 120


def _disclosed() -> list[tuple[str, str]]:
    """Every registered setting's folded-away paragraph, as (key, text)."""
    return [
        (key, str(one.disclosure))
        for key, one in sorted(registered_settings().items())
        if getattr(one, "disclosure", None)
    ]


def disclosure_faults(text: str) -> list[str]:
    """Why one disclosure breaks the shape, or nothing; the proof below runs the same code."""
    faults = [
        f"says {phrase!r}, which is about the control"
        for phrase in ABOUT_ITSELF
        if phrase in text.lower()
    ]
    faults += [
        f"names a clock format ({phrase!r})" for phrase in CLOCK_FORMATS if phrase in text.lower()
    ]
    if not text.rstrip().endswith((".", "?", "!", ")", '"')):
        faults.append("does not end as a sentence")
    if _sentences(text) > DISCLOSURE_SENTENCES or len(_words(text)) > DISCLOSURE_WORDS:
        faults.append(
            f"{_sentences(text)} sentences and {len(_words(text))} words, past the ceiling of "
            f"{DISCLOSURE_SENTENCES} and {DISCLOSURE_WORDS}"
        )
    return faults


def test_there_are_disclosures_to_check() -> None:
    """A reading that found none would pass the rule below."""
    assert len(_disclosed()) > 40, f"only {len(_disclosed())} disclosures, which cannot be right"


def test_every_disclosure_has_the_shape() -> None:
    """It says what happens, never what the control is; no clock format; sentences; not a manual."""
    faulty = [f"{key}: {fault}" for key, text in _disclosed() for fault in disclosure_faults(text)]
    assert not faulty, "A disclosure breaks the shape of settings copy.\n\n" + "\n".join(
        f"  {one}" for one in faulty
    )


def test_the_disclosure_checks_can_actually_fail() -> None:
    """Each rule in front of a paragraph whose answer is known, and a paragraph that passes."""
    assert disclosure_faults("This option keeps the files.")
    assert disclosure_faults("Times are shown on a 24-hour clock.")
    assert disclosure_faults("Sift keeps the copy")
    assert disclosure_faults(" ".join(["Sift keeps a copy."] * (DISCLOSURE_SENTENCES + 1)))
    assert disclosure_faults("word " * (DISCLOSURE_WORDS + 1) + "end.")
    assert disclosure_faults("Sift keeps a corrected copy and plays that instead.") == []
