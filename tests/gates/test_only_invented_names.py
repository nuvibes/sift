# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sample data uses the cast: a person's name in this repository is one from `names_cast.txt`.

A fixture, an example or a comment names people, studios and usernames, and each of them comes from
one declared cast, so sample data reads the same everywhere and a new name is a deliberate choice.
A name-shaped string on neither list fails the build: adding one is a line in a text file.

## What is read

- Every docstring and every comment in the Python tree, and the string literals of every test and
  fixture module.
- Every comment in the client and the desktop shell, and the string literals of their tests.

In a comment, two capitalised words are as often the start of a sentence ("The Settings") or one of
Sift's own names ("Photo Sets") as a person, so a candidate that opens with a word on
`COMMON_OPENERS`, or is made only of the names in `vocabulary.json` (`proper_names`), is not one.

## The two lists

`names_cast.txt` is the cast: the people, studios and usernames this project invented, plus the
historical figures it uses on purpose. Adding to it is a decision.

`names_phrases.txt` is a nuisance list: title-case strings that are not anybody: interface copy,
technical terms, product names. Keeping them apart keeps the first list short enough to vouch for.
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import tokenize
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.gates import vocabulary

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
DATA = Path(__file__).parent / "data"

#: Two or three capitalised words in a row: what a person's name looks like written down. Three
#: letters at least, because two-letter capitals are initials and abbreviations rather than names.
NAME_SHAPED = re.compile(r"\b[A-Z][a-z]{2,}(?: [A-Z][a-z]{2,}){1,2}\b")

#: A quoted string in TypeScript or Svelte. Not a parser (there is none here), and it does not
#: need to be: a missed literal is a name this gate does not see, and every one it does see is
#: really in the file. The length cap keeps a whole minified line out of the candidate list.
TS_LITERAL = re.compile(r"""(['"`])((?:\\.|(?!\1)[^\\]){0,160})\1""")

#: Generated from the server's own description. Sweeping the docstring sweeps these.
GENERATED = ("frontend/openapi.json", "frontend/src/lib/api/schema.d.ts")

#: A comment in the client, read the way `frontend/scripts/lib/tree.js` (`commentsIn`) reads one:
#: a component's script and style blocks are code, with block and line comments; the rest of it is
#: markup, with markup comments only, so `accept="image/*"` in an attribute opens nothing. A line
#: comment is not preceded by a colon, a quote or a backslash, which keeps a URL out.
_BLOCKS = re.compile(r"<(script|style)\b[^>]*>[\s\S]*?</\1>")
_BLOCK_COMMENT = re.compile(r"/\*[\s\S]*?\*/")
_LINE_COMMENT = re.compile(r"(^|[^:'\"`\\])(//[^\n]*)", re.MULTILINE)
_MARKUP_COMMENT = re.compile(r"<!--[\s\S]*?-->")

#: Words that open a sentence or a clause far more often than they start a name. A candidate from a
#: comment that begins with one is prose.
COMMON_OPENERS = frozenset(
    [
        "The",
        "And",
        "But",
        "For",
        "From",
        "With",
        "Without",
        "Into",
        "Onto",
        "Until",
        "When",
        "Where",
        "Which",
        "What",
        "Whether",
        "Why",
        "How",
        "Who",
        "Whose",
        "Each",
        "Every",
        "Only",
        "Not",
        "Its",
        "Their",
        "Our",
        "Your",
        "This",
        "That",
        "These",
        "Those",
        "Some",
        "Any",
        "All",
        "Both",
        "Before",
        "After",
        "Beside",
        "Under",
        "Above",
        "Below",
        "Between",
        "Across",
        "Behind",
        "Beyond",
        "Through",
        "Than",
        "Then",
        "Once",
        "Also",
        "Even",
        "Just",
        "Still",
        "Here",
        "There",
        "Called",
        "Named",
        "Labelled",
        "Labeled",
        "Using",
        "Pressing",
        "Opening",
        "Turning",
        "Handing",
        "Catching",
        "Granting",
        "Starting",
        "Moving",
        "Naming",
        "Says",
        "Said",
        "Decides",
        "Read",
        "Press",
        "Write",
        "Use",
        "Make",
        "Remove",
        "Keep",
        "Stop",
        "Add",
        "Ask",
        "Shall",
        "Telling",
        "Suggesting",
        "Left",
        "Always",
        "Never",
        "Off",
        "Like",
        "Several",
        "One",
        "Two",
        "Three",
        "Four",
        "Five",
        "Six",
        "Forty",
        "Somebody",
        "Everything",
        "Nothing",
        "Choose",
        "Scan",
        "Sets",
        "Real",
        "New",
        "Low",
        "Ends",
        "Is",
        "Are",
        "Was",
        "Were",
        "Has",
        "Have",
        "Had",
        "Does",
        "Did",
        "Can",
        "Could",
        "Would",
        "Should",
        "Will",
        "May",
        "Might",
        "Must",
        "Per",
        "Via",
        "Over",
        "Around",
        "Tags",
        "Folders",
        "Sites",
        "Names",
        "Characters",
        "Adjacent",
        "Adapters",
        "Variables",
        "Apart",
        "Beneath",
        "Inside",
        "Outside",
        "Upon",
        "Within",
        "Given",
        "Seen",
        "Shown",
        "Showing",
        "Clicking",
        "Dragging",
        "Typing",
        "Sending",
        "Asking",
        "Setting",
        "Getting",
        "Taking",
        "Making",
        "Leaving",
        "Choosing",
        "Picking",
        "Selecting",
        "Holding",
        "Closing",
        "Reading",
        "Writing",
        "Checking",
        "Running",
        "Saving",
        "Loading",
        "Drawing",
    ]
)


def _tracked(*patterns: str) -> list[str]:
    """What git has, rather than what is on disk: build output is not this gate's business."""
    found = subprocess.run(
        ["git", "ls-files", *patterns],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return found.stdout.split()


def _is_fixture(path: str) -> bool:
    """Whether this Python module is test or fixture code, where library data gets typed in."""
    return "/tests/" in path or "/testing/" in path or path.endswith("conftest.py")


def is_prose(candidate: str) -> bool:
    """Whether a name-shaped string from a comment is a sentence opening or Sift's own words."""
    words = candidate.split()
    if words[0] in COMMON_OPENERS:
        return True
    left = candidate
    for phrase in vocabulary.proper_phrases():
        left = left.replace(phrase, "")
    names = vocabulary.proper_names()
    return all(word in names or word.rstrip("s") in names for word in left.split())


def _comment_candidates(first_line: int, text: str) -> Iterator[tuple[int, str]]:
    for match in NAME_SHAPED.finditer(text):
        if not is_prose(match.group(0)):
            yield first_line + text.count("\n", 0, match.start()), match.group(0)


def _python_comments(source: str) -> Iterator[tuple[int, str]]:
    """Every name-shaped string in one Python file's comments."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return
    for token in tokens:
        if token.type == tokenize.COMMENT:
            yield from _comment_candidates(token.start[0], token.string)


def _client_comment_spans(source: str) -> list[tuple[int, str]]:
    """`(offset, comment)` for every comment in one client or shell file."""
    found: list[tuple[int, str]] = []

    def code(part: str, base: int) -> None:
        blanked = part
        for match in _BLOCK_COMMENT.finditer(part):
            found.append((base + match.start(), match.group(0)))
            blank = re.sub(r"[^\n]", " ", match.group(0))
            blanked = blanked[: match.start()] + blank + blanked[match.end() :]
        for match in _LINE_COMMENT.finditer(blanked):
            found.append((base + match.start(2), match.group(2)))

    def markup(part: str, base: int) -> None:
        found.extend((base + one.start(), one.group(0)) for one in _MARKUP_COMMENT.finditer(part))

    blocks = list(_BLOCKS.finditer(source))
    if not blocks:
        code(source, 0)
    else:
        at = 0
        for block in blocks:
            markup(source[at : block.start()], at)
            code(block.group(0), block.start())
            at = block.end()
        markup(source[at:], at)
    return sorted(found)


def _client_comments(source: str) -> Iterator[tuple[int, str]]:
    """Every name-shaped string in one client or shell file's comments."""
    for offset, comment in _client_comment_spans(source):
        yield from _comment_candidates(source.count("\n", 0, offset) + 1, comment)


def _python_candidates(path: str, source: str) -> Iterator[tuple[int, str]]:
    """Every name-shaped string in one Python file's docstrings and comments, and its fixture
    literals."""
    yield from _python_comments(source)
    try:
        tree = ast.parse(source)
    except SyntaxError:  # a file this gate cannot read is one another gate fails on
        return
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            written = ast.get_docstring(node, clean=False)
            if written is None:
                continue
            if node.body and isinstance(node.body[0], ast.Expr):
                docstrings.add(id(node.body[0].value))
            for found in NAME_SHAPED.findall(written):
                yield node.body[0].lineno, found
    if not _is_fixture(path):
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings or len(node.value) > 80:
            continue
        for found in NAME_SHAPED.findall(node.value):
            yield node.lineno, found


def _client_candidates(source: str) -> Iterator[tuple[int, str]]:
    """Every name-shaped string inside a quoted literal in one client test file."""
    for match in TS_LITERAL.finditer(source):
        line = source.count("\n", 0, match.start()) + 1
        for found in NAME_SHAPED.findall(match.group(2)):
            yield line, found


#: A filename in a literal: the media extensions a fixture names a file with.
_FILENAME = re.compile(
    r"[^\s/\\'\"`]+\.(?:jpe?g|png|gif|webp|mp4|m4v|mov|mkv|webm)\b", re.IGNORECASE
)


def _handles(text: str) -> list[str]:
    """The usernames Sift itself reads off every filename in `text` (`username_in_filename`).

    A handle is lowercase and joined by dots and underscores, so `NAME_SHAPED` never sees one; the
    filename fixtures that teach the naming reader are full of them. Read with the production
    reader rather than a pattern of this gate's own, so a handle counts here exactly when Sift
    would file a file under it.
    """
    from sift.slices.suggestions.naming import username_in_filename

    found: list[str] = []
    for match in _FILENAME.finditer(text):
        name = username_in_filename(match.group(0))
        if name:
            found.append(name)
    return found


def _handle_candidates() -> list[tuple[str, int, str]]:
    """Every handle on the fixture surface: the literals of Python tests and fixtures, and of the
    client's tests."""
    found: list[tuple[str, int, str]] = []
    for path in _tracked("*.py"):
        if not _is_fixture(path) or not (REPO / path).exists():
            continue
        source = (REPO / path).read_text(encoding="utf-8")
        for number, line in enumerate(source.splitlines(), 1):
            found.extend((path, number, name) for name in _handles(line))
    for path in _tracked("frontend", "desktop"):
        if ".test." not in path and "/e2e/" not in path:
            continue
        # A file git still lists and the disk no longer has is a deletion not yet staged.
        if (
            not path.endswith((".ts", ".svelte", ".js", ".mjs", ".mts"))
            or not (REPO / path).exists()
        ):
            continue
        source = (REPO / path).read_text(encoding="utf-8")
        for number, line in enumerate(source.splitlines(), 1):
            found.extend((path, number, name) for name in _handles(line))
    return found


def _declared(name: str) -> set[str]:
    """One of the two lists, as a set. `#` starts a comment and blank lines are spacing."""
    lines = (DATA / name).read_text(encoding="utf-8").splitlines()
    return {one.strip() for one in lines if one.strip() and not one.startswith("#")}


def _every_candidate() -> list[tuple[str, int, str]]:
    """Every name-shaped string on the scanned surface, with where it was found."""
    found: list[tuple[str, int, str]] = []
    for path in _tracked("*.py"):
        if not (REPO / path).exists():
            continue
        source = (REPO / path).read_text(encoding="utf-8")
        found.extend((path, line, name) for line, name in _python_candidates(path, source))
    for path in _tracked("frontend", "desktop"):
        if path in GENERATED or "/generated/" in path:
            continue
        if (
            not path.endswith((".ts", ".svelte", ".js", ".mjs", ".mts"))
            or not (REPO / path).exists()
        ):
            continue
        source = (REPO / path).read_text(encoding="utf-8")
        found.extend((path, line, name) for line, name in _client_comments(source))
        if ".test." in path or "/e2e/" in path:
            found.extend((path, line, name) for line, name in _client_candidates(source))
    return found


def test_every_name_shaped_string_is_one_this_project_invented() -> None:
    """A name in a fixture or an example is a name somebody here made up, and nobody else.

    The failure names the file and the line, because "add it to the list" is only the right answer
    once somebody has looked at where it came from.
    """
    allowed = _declared("names_cast.txt") | _declared("names_phrases.txt")
    unknown: dict[str, list[str]] = {}
    for path, line, name in _every_candidate():
        if name not in allowed:
            unknown.setdefault(name, []).append(f"{path}:{line}")

    assert not unknown, "\n".join(
        [
            "",
            "A name-shaped string is here that no list declares.",
            "",
            *(
                f"  {name}\n      " + "\n      ".join(sorted(set(where))[:4])
                for name, where in sorted(unknown.items())
            ),
            "",
            "  Sample data takes its names from the cast: use a name from",
            "  tests/gates/data/names_cast.txt of the same shape, so what the test is about is",
            "  unchanged.",
            "",
            "  If it is a new invented name, add it to names_cast.txt. If it is not a name at all,",
            "  add it to names_phrases.txt.",
            "",
        ]
    )


def test_the_lists_do_not_overlap() -> None:
    """One string, one list. A name in both is a name nobody has decided about."""
    both = _declared("names_cast.txt") & _declared("names_phrases.txt")
    assert not both, f"declared in both lists: {sorted(both)}"


def test_a_name_nothing_declares_is_refused() -> None:
    """The gate can fail. A rule proved only by a green run is a rule that may have stopped working.

    Against the real matcher rather than a copy of it: what is checked is that a name-shaped string
    is picked out of a fixture literal and out of a docstring, which are the two halves of the
    surface.
    """
    fixture = 'def make():\n    return {"name": "Somereal Person"}\n'
    docs = '"""An example naming Anotherreal Person, in prose."""\n'
    assert [name for _line, name in _python_candidates("a/tests/test_x.py", fixture)] == [
        "Somereal Person"
    ]
    assert [name for _line, name in _python_candidates("src/x.py", docs)] == ["Anotherreal Person"]
    # And a literal in a module that is not a fixture is not read at all.
    assert list(_python_candidates("src/x.py", fixture)) == []


def test_a_client_literal_is_read() -> None:
    source = "// Nadia Vance in a comment\nconst who = 'Somereal Person';\n"
    assert [name for _line, name in _client_candidates(source)] == ["Somereal Person"]


def test_a_name_in_a_comment_is_read_and_a_sentence_opening_is_not() -> None:
    """Both comment readers, with a name where it lands and the prose the filter lets through."""
    python = "X = 1  # Somereal Person asked\n# The Settings screen, and Photo Sets\n"
    assert list(_python_candidates("src/x.py", python)) == [(1, "Somereal Person")]
    client = (
        "<script>\n\t// The Settings pane\n\t/* a block\n\t   Thirdreal Person */\n</script>\n"
        "<!-- Anotherreal Person -->\n<a href='http://Notacomment Here'>x</a>\n"
    )
    assert list(_client_comments(client)) == [(4, "Thirdreal Person"), (6, "Anotherreal Person")]


def test_every_handle_a_fixture_names_is_one_this_project_invented() -> None:
    """A username in a filename fixture is invented, like a name written out in full.

    The names above are read as capitalised words, and a handle is not written that way
    (`harlowquin`, `odalie_frisk`), so the filename fixtures that teach the naming reader are read
    here as handles. Each is read the way Sift reads it and must be on `names_cast.txt`, in that
    reading.
    """
    allowed = _declared("names_cast.txt") | _declared("names_phrases.txt")
    unknown: dict[str, list[str]] = {}
    for path, line, name in _handle_candidates():
        if name not in allowed:
            unknown.setdefault(name, []).append(f"{path}:{line}")

    assert not unknown, "\n".join(
        [
            "",
            "A handle is in a filename fixture that no list declares.",
            "",
            *(
                f"  {name}\n      " + "\n      ".join(sorted(set(where))[:4])
                for name, where in sorted(unknown.items())
            ),
            "",
            "  Use a handle from the cast, or add a new invented one to names_cast.txt as Sift",
            "  reads it (spaces for . and _).",
            "",
        ]
    )


def test_a_handle_in_a_filename_is_read_as_sift_reads_it() -> None:
    """The handle reader can fail: a planted handle is found, and a filename with none is not."""
    line = (
        'files = ["plantedhandle_1029384756_3141592653589793238_27182818284.jpg", "IMG_0001.jpg"]'
    )
    assert _handles(line) == ["plantedhandle"]
