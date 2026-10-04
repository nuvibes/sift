# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every queue on the Organize board says what its number counts.

A figure over a title is two facts to join (the Duplicates card counts GROUPS), so each queue
declares a phrase that follows its count ("4 folders to name") and one for a count of one
(`Summary.verb_one`). The types refuse a survey omitting either; this checks the phrases read as
phrases and the singular differs from the plural, as `kernel/tests/test_reach.py` does for
`OUT_OF_REACH`. Read statically from every slice's `queue.py`, so it needs no running board and
cannot fall silent when a feature is switched off.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.gates import server_copy, vocabulary

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]
_SOURCE = _REPO / "src" / "sift" / "slices"

#: A verb continues a number, so it ends with no stop.
_ENDINGS = ".!?"


def _surveys() -> list[tuple[Path, ast.Call]]:
    found: list[tuple[Path, ast.Call]] = []
    for path in sorted(_SOURCE.glob("*/queue.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Summary"
            ):
                found.append((path, node))
    return found


def _word(call: ast.Call, name: str) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            value = keyword.value.value
            return value if isinstance(value, str) else None
    return None


def _declared_word(cls: ast.ClassDef, name: str) -> str | None:
    value = _declared(cls, name)
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _title_of(path: Path, call: ast.Call) -> str | None:
    """The title a survey's card is drawn under: the queue's declared `title`, or the survey's."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ClassDef) and any(
            isinstance(one, ast.Call) and one.lineno == call.lineno for one in ast.walk(node)
        ):
            return _declared_word(node, "title") or _word(call, "title")
    return _word(call, "title")


def test_the_gate_can_see_the_queues() -> None:
    """A floor for the reader: a glob finding nothing would pass every rule."""
    assert len(_surveys()) >= 10


@pytest.mark.parametrize("path, call", _surveys(), ids=lambda one: getattr(one, "name", ""))
def test_every_queue_declares_a_verb_that_reads_as_one(path: Path, call: ast.Call) -> None:
    """The phrase after the count: present, lowercase (it continues the number), not a sentence,
    and not the title over again."""
    where = f"{path.parent.name}/{path.name}"
    verb = _word(call, "verb")
    assert verb, f"{where}: a queue must declare what its count counts"
    assert verb[0].islower(), f"{where}: {verb!r} continues a number, so it starts lowercase"
    assert verb[-1] not in _ENDINGS, f"{where}: {verb!r} is a phrase, not a sentence"

    title = _title_of(path, call)
    assert title, f"{where}: a queue declares the title its card is drawn under"
    assert verb.lower() != title.lower(), f"{where}: {verb!r} only repeats the card's title"


@pytest.mark.parametrize("path, call", _surveys(), ids=lambda one: getattr(one, "name", ""))
def test_every_queue_declares_the_phrase_for_one_of_them_too(path: Path, call: ast.Call) -> None:
    """The singular is present, held to the same spelling, and not the plural over again, or a
    card picking correctly draws "1 folders to name". None of the nouns counted is invariant."""
    where = f"{path.parent.name}/{path.name}"
    verb = _word(call, "verb")
    one = _word(call, "verb_one")
    assert one, f"{where}: a queue must say what ONE of these is called, not only what many are"
    assert one[0].islower(), f"{where}: {one!r} continues a number, so it starts lowercase"
    assert one[-1] not in _ENDINGS, f"{where}: {one!r} is a phrase, not a sentence"
    assert one != verb, (
        f"{where}: {one!r} is the plural over again, so a pile of one still reads as many"
    )

    title = _title_of(path, call)
    assert title, f"{where}: a queue declares the title its card is drawn under"
    assert one.lower() != title.lower(), f"{where}: {one!r} only repeats the card's title"


# --- what each card is for

#: The longest a card's purpose may run: two lines of body text at the narrowest board column
#: (`--board-column-min`), so every card keeps one shape.
_PURPOSE_MOST = 72


def _classes() -> list[tuple[str, ast.ClassDef]]:
    found: list[tuple[str, ast.ClassDef]] = []
    for path in sorted(_SOURCE.glob("*/queue.py")):
        where = f"{path.parent.name}/{path.name}"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef) and _declared(node, "band") is not None:
                found.append((where, node))
    return found


def _declared(cls: ast.ClassDef, name: str) -> ast.expr | None:
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return node.value
    return None


def _a_record(cls: ast.ClassDef) -> bool:
    band = _declared(cls, "band")
    return isinstance(band, ast.Attribute) and band.attr == "RECORD"


def test_the_gate_can_see_the_queue_classes() -> None:
    """A floor: a reader that found nothing passes."""
    classes = _classes()
    assert len(classes) >= 15
    assert any(_a_record(cls) for _, cls in classes)


@pytest.mark.parametrize("where, cls", _classes(), ids=lambda one: getattr(one, "name", ""))
def test_every_card_says_what_it_is_for_in_one_short_sentence(
    where: str, cls: ast.ClassDef
) -> None:
    """The sentence under a card's name says what the pile is FOR: declared on the class, no
    question, no number (that is the card's figure), one short sentence. A record declares None."""
    value = _declared(cls, "purpose")
    assert value is not None, f"{where}: {cls.name} must declare `purpose` (None on a record)"
    assert isinstance(value, ast.Constant), f"{where}: {cls.name}'s purpose is a plain string"
    said = value.value
    if _a_record(cls):
        assert said is None, f"{where}: {cls.name} is a record, which is never a card"
        return
    assert isinstance(said, str) and said, f"{where}: {cls.name} must say what its card is for"
    assert said[0].isupper(), f"{where}: {said!r} is a sentence, so it starts with a capital"
    assert said.endswith("."), f"{where}: {said!r} is a sentence, so it ends with a full stop"
    assert "?" not in said, f"{where}: {said!r} asks; a card's purpose describes"
    assert ". " not in said, f"{where}: {said!r} is more than one sentence"
    assert not re.search(r"\d", said), (
        f"{where}: {said!r} carries a number; the count is the card's"
    )
    assert len(said) <= _PURPOSE_MOST, (
        f"{where}: {said!r} runs to {len(said)} characters; a card carries {_PURPOSE_MOST}"
    )


def test_the_purpose_rule_refuses_a_question_built_from_a_count() -> None:
    """A question built from a count is refused."""
    tree = ast.parse(
        "class Q:\n    band = Band.DECISION\n"
        "    purpose = 'Do these 2,000 faces look like Wren Halloway?'\n"
    )
    cls = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
    with pytest.raises(AssertionError):
        test_every_card_says_what_it_is_for_in_one_short_sentence("planted/queue.py", cls)


# --- the words on the cards


def _titles() -> list[tuple[str, str]]:
    """`(where, title)` for every tab `title` and card `group_title` a queue declares."""
    found: list[tuple[str, str]] = []
    for path in sorted(_SOURCE.glob("*/queue.py")):
        where = f"{path.parent.name}/{path.name}"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef):
                for name in ("title", "group_title"):
                    if declared := _declared_word(node, name):
                        found.append((where, declared))
    return found


def test_the_gate_can_see_the_card_titles() -> None:
    """A floor: a reader that found nothing passes."""
    titles = _titles()
    assert len(titles) >= 12
    assert any(where == "library_roots/queue.py" for where, _ in titles)


def test_every_card_and_tab_title_is_sentence_case() -> None:
    """Every card and tab title is sentence case, capitalising only `proper_names`."""
    shouting = [
        f"{where}: {title!r} ({', '.join(words)})"
        for where, title in _titles()
        if (words := vocabulary.not_sentence_case(title))
    ]
    assert not shouting, (
        "A card or tab title on the Organize board is sentence case: one initial capital, and after\n"
        "that only names. If one of these IS a name, add it to proper_names in\n"
        "tests/gates/data/vocabulary.json.\n\n" + "\n".join(f"  {one}" for one in shouting)
    )


def test_sentence_case_is_judged_by_the_vocabulary_names() -> None:
    assert vocabulary.not_sentence_case("Near Duplicates") == ["Duplicates"]
    assert vocabulary.not_sentence_case("People Sift can recognize") == []
    assert vocabulary.not_sentence_case("Create Photo Set") == []


#: The words the Organize screens retired (`organize_screens` rows of `scoped_wrong_words`), held at
#: zero over the strings only a `queue.py` writes, which the client gates cannot see.
_ORGANIZE_WORDS: tuple[tuple[str, str], ...] = tuple(
    (wrong, right)
    for wrong, right, where in vocabulary.scoped_wrong_words()
    if where == vocabulary.scope("organize_screens")
)


def _retired_in(text: str) -> list[tuple[str, str]]:
    return [
        (wrong, right)
        for wrong, right in _ORGANIZE_WORDS
        if re.search(vocabulary.word_pattern(wrong), text, re.IGNORECASE)
    ]


def test_the_organize_words_are_read_from_the_vocabulary() -> None:
    """The list loads, or every queue would pass at zero."""
    words = {wrong for wrong, _ in _ORGANIZE_WORDS}
    assert {"settled", "put back", "taken back", "lands"} <= words


def test_no_queue_card_says_a_word_the_organize_screens_retired() -> None:
    """No queue card says a retired Organize word (Settle, land, take off, take back, put back,
    made, walked, filed under); the verb table names their replacements."""
    found = [
        f"  {one.path}:{one.line}  {wrong!r} -> {right}\n      {one.text.strip()[:100]}"
        for path in sorted(_SOURCE.glob("*/queue.py"))
        for one in server_copy.copy_in(
            path.read_text(encoding="utf-8"), path.relative_to(_REPO).as_posix()
        )
        for wrong, right in _retired_in(one.text)
    ]
    assert not found, (
        "\nA card on the Organize board uses a word the Organize screens retired:\n\n"
        + "\n".join(found)
        + "\n\nUse the verb table's word (tests/gates/data/vocabulary.json, organize_screens).\n"
    )


def test_the_retired_word_check_catches_a_card_that_says_one() -> None:
    """A planted survey saying a retired word is refused."""
    planted = server_copy.copy_in(
        'Summary(name="x", title="X", verb="piles", verb_one="pile", '
        'decision="One answer settles every face in it.", icon="x", count=0)\n',
        "src/sift/slices/x/queue.py",
    )
    assert any(_retired_in(one.text) for one in planted)
