# SPDX-License-Identifier: AGPL-3.0-or-later
"""A comment says why the code is as it is now, not how it came to be.

Every comment and docstring in the Python tree, every full-line comment in the shell scripts
and the YAML (the workflows included, whose comments are as public as the code), and every string
the gates' own data files hold (`data/*.json`, `data/*.txt`: a `why` written there is as public as
a comment) is read against `data/narration.json`: dates, a person named where a role belongs, the
history of a rule, another product named as the authority for a design, and paths on one
development machine. Held at zero. The client and the desktop shell are read against the same file by
`frontend/scripts/check_no_narration.js`, and commit messages by `scripts/check_commit_message.sh`.

A measured number the code depends on may stay; where and when it was measured goes.
"""

from __future__ import annotations

import ast
import io
import json
import re
import subprocess
import tokenize
from collections import Counter
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
NARRATION = Path(__file__).resolve().parent / "data" / "narration.json"


@dataclass(frozen=True)
class Entry:
    """One pattern of the list, compiled the way every reader compiles it."""

    family: str
    source: str
    pattern: re.Pattern[str]
    example: str
    clean: str


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    family: str
    found: str


@cache
def load() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(NARRATION.read_text(encoding="utf-8"))
    return loaded


@cache
def entries() -> tuple[Entry, ...]:
    """Every entry, case-insensitive unless it says `"case": true`."""
    return tuple(
        Entry(
            family=family["name"],
            source=entry["pattern"],
            pattern=re.compile(entry["pattern"], 0 if entry.get("case") else re.IGNORECASE),
            example=entry["example"],
            clean=entry["clean"],
        )
        for family in load()["families"]
        for entry in family["entries"]
    )


def python_prose(source: str) -> list[tuple[int, str]]:
    """`(first line, text)` for every comment and every bare string statement in one module.

    A bare string statement is a docstring wherever it stands: a module's, a class's, a function's,
    or the one written under an assignment to describe it.
    """
    found: list[tuple[int, str]] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type != tokenize.COMMENT:
                continue
            # Comment lines that follow one another are one paragraph: a sentence wrapped at a line
            # end is read whole, so a pattern is not dodged by where the line happens to break.
            line, text = token.start[0], token.string
            if found and found[-1][0] + found[-1][1].count("\n") == line - 1:
                head, body = found[-1]
                found[-1] = (head, body + "\n" + text)
            else:
                found.append((line, text))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return found
    try:
        tree = ast.parse(source)
    except SyntaxError:  # a module that does not parse fails every other gate first
        return found
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            found.append((node.lineno, node.value.value))
    return found


def shell_prose(source: str) -> list[tuple[int, str]]:
    """`(line, text)` for every full-line comment in a shell script, less the interpreter line."""
    return [
        (number, line)
        for number, line in enumerate(source.split("\n"), 1)
        if line.lstrip().startswith("#") and not (number == 1 and line.startswith("#!"))
    ]


def offences(first_line: int, text: str, where: str) -> list[Finding]:
    """Every entry's match in one piece of prose, at the line it sits on."""
    return [
        Finding(where, first_line + text.count("\n", 0, match.start()), one.family, match.group(0))
        for one in entries()
        for match in one.pattern.finditer(text)
    ]


def _tracked(*patterns: str) -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", *patterns],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return [name for name in listed.stdout.split("\0") if name]


#: What is read: Python for its comments and docstrings, the rest for their full-line `#` comments.
SCANNED = ("*.py", "*.sh", "*.yml", "*.yaml")

#: The gates' data files, read for every string they hold. The list of words itself is not: each
#: of its entries carries the words it refuses as its example.
DATA = ("tests/gates/data/*.json", "tests/gates/data/*.txt")

#: The keys whose value is a regular expression rather than a sentence.
DATA_CODE_KEYS = frozenset({"pattern"})


def data_prose(source: str, where: str) -> list[tuple[int, str]]:
    """`(line, text)` for every string a JSON data file holds, or every `#` line of a text one.

    A JSON string is placed at the first line its written form appears on; a value under a key in
    `DATA_CODE_KEYS` is code and is not read.
    """
    if not where.endswith(".json"):
        return shell_prose(source)
    found: list[tuple[int, str]] = []

    def walk(value: Any, key: str | None) -> None:
        if isinstance(value, str) and key not in DATA_CODE_KEYS:
            at = source.find(json.dumps(value)[1:-1])
            found.append((source.count("\n", 0, at) + 1 if at >= 0 else 1, value))
        elif isinstance(value, dict):
            for name, inner in value.items():
                walk(inner, name)
        elif isinstance(value, list):
            for inner in value:
                walk(inner, key)

    walk(json.loads(source), None)
    return found


def scan() -> list[Finding]:
    """Every offence in every tracked Python module, shell script and YAML file."""
    found: list[Finding] = []
    for where in _tracked(*SCANNED):
        try:
            source = (REPO / where).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # a deleted file still in the index, or a binary
            continue
        prose = python_prose(source) if where.endswith(".py") else shell_prose(source)
        for first_line, text in prose:
            found.extend(offences(first_line, text, where))
    for where in _tracked(*DATA):
        if where == NARRATION.relative_to(REPO).as_posix():
            continue
        try:
            source = (REPO / where).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for first_line, text in data_prose(source, where):
            found.extend(offences(first_line, text, where))
    return found


def _area(path: str) -> str:
    """The folder a finding is counted under: two levels, three inside the Python package."""
    parts = path.split("/")
    depth = 4 if parts[:2] == ["src", "sift"] else 2
    return "/".join(parts[: min(depth, len(parts) - 1)]) or path


@pytest.mark.parametrize("one", entries(), ids=lambda one: f"{one.family}:{one.source[:40]}")
def test_every_entry_matches_its_example_and_not_its_clean_one(one: Entry) -> None:
    """The known positive and the near miss, for every pattern. A pattern that matches nothing
    reads exactly like a tree with nothing to report."""
    assert one.pattern.search(one.example), f"{one.source!r} does not match {one.example!r}"
    assert not one.pattern.search(one.clean), f"{one.source!r} matches {one.clean!r}"


def test_every_family_has_an_entry() -> None:
    families = [family["name"] for family in load()["families"]]
    assert set(families) >= {"date", "process", "history", "authority", "machine"}, families
    assert all(family["entries"] for family in load()["families"])


def test_the_list_is_written_the_way_the_shell_reader_reads_it() -> None:
    """`check_commit_message.sh` reads one entry per line with a line-oriented reader, so every
    value is ASCII, holds no double quote and uses no JSON escape but a doubled backslash."""
    raw = NARRATION.read_text(encoding="utf-8")
    assert raw.isascii()
    lines = raw.split("\n")
    for one in entries():
        written = json.dumps(one.source)
        assert sum(written in line for line in lines) == 1, f"{one.source!r} is not on one line"
        for value in (one.source, one.example, one.clean):
            assert '"' not in value, value
            assert re.sub(r"\\\\", "", json.dumps(value)[1:-1]).count("\\") == 0, value


def test_the_reader_finds_a_comment_a_docstring_and_a_shell_comment() -> None:
    """The three places prose sits, each with its real line, and none of the code around them."""
    stamp = "20" + "26-01-02"
    module = (
        f'"""Module docstring, {stamp}."""\n'
        "\n"
        f"VALUE = '{stamp}'  # a trailing comment, {stamp}\n"
        "\n"
        "def f() -> None:\n"
        f'    """First line.\n\n    Third line, {stamp}.\n    """\n'
    )
    found = [(finding.line, finding.family) for text in [module] for finding in _in(text)]
    assert found == [(1, "date"), (3, "date"), (8, "date")], found

    script = f"#!/usr/bin/env bash\n# about {stamp}\necho '{stamp}'\n  # indented, {stamp}\n"
    lines = [line for line, _ in shell_prose(script)]
    assert lines == [2, 4]


def test_the_workflows_and_actions_are_read() -> None:
    """Their comments are public too, and a pattern list nobody points at them reads nothing."""
    read = _tracked(*SCANNED)
    assert any(name.startswith(".github/actions/") for name in read), "no action.yml is read"
    stamp = "20" + "26-01-02"
    workflow = f"on: push\n# measured {stamp} on a runner\njobs: {{}}\n"
    found = [
        (line, one.family)
        for line, text in shell_prose(workflow)
        for one in entries()
        if one.pattern.search(text)
    ]
    assert found == [(2, "date")], found


def test_a_data_file_is_read_for_its_sentences_and_not_its_patterns() -> None:
    """A date in parentheses in a `why`, and a person's word, are found at their lines; a pattern
    that names the word it refuses is code, and the data files are in what is read."""
    stamp = "20" + "26-01-02"
    data = (
        "{\n"
        f'\t"words": [{{ "verb": "Clip", "why": "the cut of the last seconds ({stamp})" }}],\n'
        '\t"retired": [{ "pattern": "\\\\brefuted\\\\b" }],\n'
        '\t"_why": "per a developer\'s note"\n'
        "}\n"
    )
    found = [
        (line, one.family)
        for line, text in data_prose(data, "x.json")
        for one in entries()
        if one.pattern.search(text)
    ]
    assert found == [(2, "date"), (4, "process")], found
    assert [line for line, _ in data_prose("# a note\nnot a comment\n", "x.txt")] == [1]
    read = _tracked(*DATA)
    assert "tests/gates/data/vocabulary.json" in read and "tests/gates/data/names_cast.txt" in read


def _in(module: str) -> list[Finding]:
    found = [
        finding
        for first_line, text in python_prose(module)
        for finding in offences(first_line, text, "x.py")
    ]
    return sorted(found, key=lambda finding: finding.line)


def test_no_comment_or_docstring_narrates() -> None:
    """Held at zero. The failure lists each line with the family it broke."""
    found = scan()
    if not found:
        return
    by_family = Counter(finding.family for finding in found)
    by_area = Counter(_area(finding.path) for finding in found)
    why = {family["name"]: family["why"] for family in load()["families"]}
    report = [
        "",
        f"{len(found)} lines of prose narrate rather than explain:",
        "",
        *(f"  {family:10} {count:5}  {why[family]}" for family, count in by_family.most_common()),
        "",
        "  by folder:",
        *(f"    {count:5}  {area}" for area, count in by_area.most_common(25)),
        "",
        *(
            f"  {finding.path}:{finding.line}  [{finding.family}] {finding.found!r}"
            for finding in found[:60]
        ),
        *([f"  ... and {len(found) - 60} more"] if len(found) > 60 else []),
        "",
        "  Say why the code is as it is now, in a short paragraph at most. Keep a number the code depends on;",
        "  drop when, where and by whom it was found. The story belongs in the commit message.",
        "",
    ]
    pytest.fail("\n".join(report), pytrace=False)


# --- the GIF kind is called a GIF -----------------------------------------------------------------
#
# The looping clip is a GIF on every screen, whatever its extension, and the vocabulary gate holds
# the copy to that. The prose around the code is read here too, because a comment that calls the
# kind "an animation" is the next person's reason to put the word back on a screen. The same word
# is also CSS's name for movement, and a WebP file's own name for its frames, and neither of those
# is the kind: so a match is let through when it sits in a code span (an identifier), when it is a
# container's term ("an animated WebP", "the animation flag"), when the word is quoted as a word,
# or when its paragraph is about movement on the screen, which the motion words below say.

#: The word, in the three forms that name the kind. "Animated" names it only before a noun ("an
#: animated picture"); followed by where or how ("animated over a third of a second", "animated
#: back") it is movement, and a hyphen or an underscore before it is a media type's name.
KIND_WORD = re.compile(
    r"(?<![-_.])\banimat(?:ions?\b|ed(?= [a-z])(?! (?:over|back|in|through|for|from|to|away|and"
    r"|or|at|on|by|with|as|into|out|off)\b))",
    re.IGNORECASE,
)

#: A container's own term: which bytes a file holds, not what the library calls it.
FORMAT_TERM = re.compile(
    r"\banimated[ -](?:gif|webp|avif|heic|heif|png|apng)s?\b|\banimation (?:flag|chunks?)\b"
    r'|\banimated ones?\b|"animations?"',  # the word itself, quoted: a mention, not a use
    re.IGNORECASE,
)

#: A paragraph about movement on the screen, where "animation" is the CSS word.
MOTION = re.compile(
    r"\bcss\b|keyframes?|\btransitions?\b|\bmotion\b(?! still)|\breduced\b|\breduction\b|--dur"
    r"|\bfad(?:e|es|ed|ing)\b|\bslid(?:e|es|ing)\b|\beas(?:e|es|ed|ing)\b|\bcurves?\b|\b\d+ ?ms\b"
    r"|\bjsdom\b|\bshimmer\b|\bsweep\b|mid-animation|\bclosing\b|\bdrawer\b|\bzoom|\bmagnif"
    r"|\bheight\b|\barrival\b|\bresting rule\b|\bregister\b|\bmenus?\b|\bveil\b|\bcue\b"
    r"|\btransform|\bturned\b|animations? off\b|main thread|\btwitch|\bscripted\b|\bkick"
    r"|\bmov(?:e|es|ed|ing|ement)\b|\bcountdown\b",
    re.IGNORECASE,
)

#: An identifier or a path written in a comment, which is code and keeps its own name.
CODE_SPAN = re.compile(r"`[^`\n]*`")

#: The client and the desktop shell, read for their comments; the Python package for its prose.
KIND_SCANNED = (
    "src/sift/*.py",
    "frontend/src/*.ts",
    "frontend/src/*.js",
    "frontend/src/*.svelte",
    "frontend/src/*.css",
    "desktop/src/*.ts",
    "desktop/src/*.js",
)

_BLOCK_COMMENT = re.compile(r"/\*[\s\S]*?\*/|<!--[\s\S]*?-->")
_LINE_COMMENT = re.compile(r"(?<![:\w/\\'\"`])//(.*)$")


def client_prose(source: str) -> list[tuple[int, str]]:
    """`(first line, text)` for every block comment, markup comment and run of `//` comments.

    Block comments are blanked out (keeping their line breaks) before the `//` comments are
    looked for, so a URL or a `//` inside one is not read twice; a `//` after a colon is a URL.
    """
    found: list[tuple[int, str]] = []
    for match in _BLOCK_COMMENT.finditer(source):
        found.append((source.count("\n", 0, match.start()) + 1, match.group(0)))
    rest = _BLOCK_COMMENT.sub(lambda match: "\n" * match.group(0).count("\n"), source)
    last = -2
    for number, line in enumerate(rest.split("\n"), 1):
        comment = _LINE_COMMENT.search(line)
        if comment is None:
            continue
        if number == last + 1 and found:
            head, body = found[-1]
            found[-1] = (head, body + "\n" + comment.group(1))
        else:
            found.append((number, comment.group(1)))
        last = number
    return found


def kind_words(first_line: int, text: str, where: str) -> list[Finding]:
    """Every use of the word for the kind in one piece of prose, paragraph by paragraph."""
    found: list[Finding] = []
    offset = 0
    for paragraph in re.split(r"(\n[ \t*#/]*\n)", CODE_SPAN.sub(lambda m: " " * len(m[0]), text)):
        if not MOTION.search(paragraph):
            spared = [(one.start(), one.end()) for one in FORMAT_TERM.finditer(paragraph)]
            for match in KIND_WORD.finditer(paragraph):
                if any(start <= match.start() < end for start, end in spared):
                    continue
                line = first_line + text.count("\n", 0, offset + match.start())
                found.append(Finding(where, line, "gif", match.group(0)))
        offset += len(paragraph)
    return found


def kind_scan() -> list[Finding]:
    """Every use of the word for the kind in the tracked prose of the three trees."""
    found: list[Finding] = []
    for where in _tracked(*KIND_SCANNED):
        try:
            source = (REPO / where).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        prose = python_prose(source) if where.endswith(".py") else client_prose(source)
        for first_line, text in prose:
            found.extend(kind_words(first_line, text, where))
    return found


def test_the_kind_reader_finds_the_word_and_passes_the_css_and_the_container_words() -> None:
    """The known positive in each place prose sits, and each of the three near misses."""
    client = (
        "const a = 1; // An animation plays on hover.\n"
        "/* A fade, so the closing animation has a transition to run. */\n"
        "<!-- An animated WebP is read by webpinfo, and its animation flag is bit two. -->\n"
        "/* See `$lib/player/animation`. */\n"
        "const url = 'https://example.org/animation';\n"
        "<!-- Every animation on a wall ends. -->\n"
    )
    found = [
        (finding.line, finding.found)
        for first_line, text in client_prose(client)
        for finding in kind_words(first_line, text, "x.svelte")
    ]
    assert sorted(found) == [(1, "animation"), (6, "animation")], found

    module = '"""Animations loop."""\n\nX = 1  # an animated WebP, which ffmpeg cannot read\n'
    found = [
        (finding.line, finding.found)
        for first_line, text in python_prose(module)
        for finding in kind_words(first_line, text, "x.py")
    ]
    assert found == [(1, "Animations")], found


def test_no_comment_calls_a_gif_an_animation() -> None:
    """Held at zero. A GIF is a GIF in the prose as on the screen, whatever its extension."""
    found = kind_scan()
    if not found:
        return
    report = [
        "",
        f"{len(found)} lines of prose call the GIF kind an animation; say GIF instead:",
        *(f"  {one.path}:{one.line}  {one.found!r}" for one in found[:80]),
        *([f"  ... and {len(found) - 80} more"] if len(found) > 80 else []),
        "",
        "  CSS movement is exempt when its paragraph says so (a transition, a fade, the motion",
        "  tokens), and so is a container's term: an animated WebP, the animation flag.",
        "",
    ]
    pytest.fail("\n".join(report), pytrace=False)


if __name__ == "__main__":  # a count by family and folder, for whoever is doing the rewrite
    everything = scan()
    print(  # nosemgrep: sift-no-print-or-raw-logger (the gate's own answer)
        f"{len(everything)} findings"
    )
    for area, count in Counter(_area(one.path) for one in everything).most_common():
        print(f"  {count:5}  {area}")  # nosemgrep: sift-no-print-or-raw-logger
