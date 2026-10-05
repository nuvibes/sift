#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""How long the Python is, how it branches, and how much of it is prose: ratchets that may only fall.

Each file or function over its line is recorded in `tests/gates/data/code-shape.json`. A recorded
number that grew is refused, so is a new entry over the line, and a number that fell is refused
until it is recorded (`--record`, which the pre-commit hook passes), so the record is always the
truth. The lines are where the code is headed, not where it stands.

Prose is docstring lines (the parser's) plus comment lines (the tokenizer's) over non-blank lines.
It is held as a pair, the share and the count of prose lines, and only both rising together is
growth: deleting code raises the share without adding a word. A test file is held to the prose
line as a module is; only its length has a line of its own (`TEST_FILE_LINES`), and its functions
none. The client and the desktop shell are held the same way by
`frontend/scripts/check_code_shape.js`.

Branches are a function's cyclomatic complexity, counted as the linter's C901 rule counts it:
one, and one more for each `if` and `elif`, loop, `except`, `try` with an `else`, `match` case
that can fail, and function defined inside it. A long function can be simple and a short one
cannot hide a dozen paths, so the two lines are held apart.
"""

from __future__ import annotations

import ast
import io
import json
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "tests" / "gates" / "data" / "code-shape.json"

MODULE_LINES = 1000
FUNCTION_LINES = 80
FUNCTION_BRANCHES = 10
TEST_FILE_LINES = 2000
PROSE_SHARE = 25.0
#: Below this many non-blank lines a single line moves the share by two points or more.
PROSE_FLOOR = 50
#: Far below the real count: a walk that stops finding files would otherwise pass.
AT_LEAST = 500

#: The trees read. `desktop/` holds no Python of its own, only what its packages carry.
TREES = ("src/sift", "scripts", "tests")

#: Modules whose length is one entry per thing they list, so a new entry is a longer file by
#: design. Each is still held to the function and prose lines; only the module line is lifted.
GROWS_BY_ENTRY: dict[str, str] = {
    "src/sift/slices/download/sources/sites/catalog.py": "one block per supported Site",
}
PLANTED_PREFIX = "GateFixture"

COUNTS = ("module_lines", "function_lines", "function_branches", "test_file_lines")
#: The maps keyed by function, where a move between modules is not a new entry.
BY_FUNCTION = ("function_lines", "function_branches")
_NOTE = (
    "Python modules over 1000 lines, functions over 80 lines or 10 branches, test files over 2000, "
    "and files of 50 or "
    "more non-blank lines, test files included, whose prose is over 25%. Each may only fall: scripts/check_code_shape.py, "
    "and record a fall with python scripts/check_code_shape.py --record"
)


def is_test_file(rel: str) -> bool:
    parts = rel.split("/")
    return "tests" in parts or parts[-1].startswith("test_") or parts[-1] == "conftest.py"


def python_files(root: Path = ROOT) -> list[str]:
    found = [
        path.relative_to(root).as_posix()
        for tree in TREES
        for path in (root / tree).rglob("*.py")
        if "__pycache__" not in path.parts and not path.name.startswith(PLANTED_PREFIX)
    ]
    if (root / "conftest.py").is_file():
        found.append("conftest.py")
    return sorted(found)


def lines_of(text: str) -> int:
    return len(text.splitlines())


def prose_of(text: str, tree: ast.Module) -> tuple[int, int]:
    """`(non-blank lines, prose lines)` of one module."""
    lines = text.splitlines()
    nonblank = {number for number, line in enumerate(lines, 1) if line.strip()}
    prose: set[int] = set()
    kinds = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        first = node.body[0] if isinstance(node, kinds) and node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            prose.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            prose.add(token.start[0])
    return len(nonblank), len(prose & nonblank)


FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def functions_of(tree: ast.Module) -> dict[str, FunctionNode]:
    """`{qualified name: node}` for every function, a repeated name numbered from its second."""
    found: dict[str, FunctionNode] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                if not isinstance(child, ast.ClassDef):
                    key, seen = name, 1
                    while key in found:
                        seen += 1
                        key = f"{name}#{seen}"
                    found[key] = child
                visit(child, f"{name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return found


def length_of(function: FunctionNode) -> int:
    return (function.end_lineno or function.lineno) - function.lineno + 1


_ONE_MORE_PATH = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.FunctionDef, ast.AsyncFunctionDef)


def _always_matches(case: ast.match_case) -> bool:
    pattern = case.pattern
    return case.guard is None and isinstance(pattern, ast.MatchAs) and pattern.pattern is None


def _paths_added(node: ast.stmt) -> int:
    """The paths this statement adds by itself, whatever is inside it."""
    if isinstance(node, _ONE_MORE_PATH):
        return 1
    if isinstance(node, (ast.Try, ast.TryStar)):
        return len(node.handlers) + bool(node.orelse)
    if isinstance(node, ast.Match):
        return sum(not _always_matches(case) for case in node.cases)
    return 0


def _blocks_under(node: ast.stmt) -> list[list[ast.stmt]]:
    """Every list of statements directly under this one."""
    held = [getattr(node, name, None) for name in ("body", "orelse", "finalbody")]
    parts = [*getattr(node, "handlers", []), *getattr(node, "cases", [])]
    return [block for block in held if isinstance(block, list)] + [part.body for part in parts]


def _paths_in(body: list[ast.stmt]) -> int:
    return sum(
        _paths_added(node) + sum(_paths_in(block) for block in _blocks_under(node)) for node in body
    )


def branches_of(function: FunctionNode) -> int:
    """The function's cyclomatic complexity; a function inside it counts toward it too."""
    return 1 + _paths_in(function.body)


def share(prose: int, nonblank: int) -> float:
    return round(100 * prose / nonblank, 1) if nonblank else 0.0


@dataclass
class Shape:
    """Every entry over its line, in the record's own shape."""

    module_lines: dict[str, int] = field(default_factory=dict)
    function_lines: dict[str, int] = field(default_factory=dict)
    function_branches: dict[str, int] = field(default_factory=dict)
    test_file_lines: dict[str, int] = field(default_factory=dict)
    prose_share: dict[str, float] = field(default_factory=dict)
    prose_lines: dict[str, int] = field(default_factory=dict)

    def as_record(self) -> dict[str, dict[str, float]]:
        return {name: dict(sorted(getattr(self, name).items())) for name in _MAPS}


_MAPS = (*COUNTS, "prose_share", "prose_lines")


def add_file(shape: Shape, rel: str, text: str) -> None:
    """Measure one file into `shape`; a file that does not parse is left to the linters."""
    try:
        tree = ast.parse(text)
        nonblank, prose = prose_of(text, tree)
    except (SyntaxError, tokenize.TokenError):
        return
    length = lines_of(text)
    if is_test_file(rel):
        if length > TEST_FILE_LINES:
            shape.test_file_lines[rel] = length
    else:
        if length > MODULE_LINES and rel not in GROWS_BY_ENTRY:
            shape.module_lines[rel] = length
        for name, function in functions_of(tree).items():
            if length_of(function) > FUNCTION_LINES:
                shape.function_lines[f"{rel}::{name}"] = length_of(function)
            if branches_of(function) > FUNCTION_BRANCHES:
                shape.function_branches[f"{rel}::{name}"] = branches_of(function)
    if nonblank >= PROSE_FLOOR and share(prose, nonblank) > PROSE_SHARE:
        shape.prose_share[rel] = share(prose, nonblank)
        shape.prose_lines[rel] = prose


def measure(root: Path = ROOT) -> Shape:
    shape = Shape()
    for rel in python_files(root):
        add_file(shape, rel, (root / rel).read_text(encoding="utf-8"))
    return shape


@dataclass
class Verdict:
    rose: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    fell: list[str] = field(default_factory=list)

    @property
    def refused(self) -> bool:
        return bool(self.rose or self.added)


def _base_name(key: str) -> str:
    return key.split("::", 1)[1].split("#", 1)[0].rsplit(".", 1)[-1]


def _compare_prose(
    recorded: dict[str, dict[str, float]], today: dict[str, dict[str, float]], verdict: Verdict
) -> None:
    """The prose pair: only the share and the count rising together is growth."""
    shares, counts = recorded.get("prose_share", {}), recorded.get("prose_lines", {})
    for key, value in today["prose_share"].items():
        lines = today["prose_lines"][key]
        if key not in shares:
            verdict.added.append(f"prose {key}: {value}% over the line and not recorded")
        elif value > shares[key] and lines > counts.get(key, 0):
            verdict.rose.append(
                f"prose {key}: {value}% in {lines} lines, recorded {shares[key]}% in "
                f"{counts.get(key, 0)}"
            )
        elif value != shares[key] or lines != counts.get(key, 0):
            verdict.fell.append(f"prose {key}: {value}% in {lines} lines, recorded {shares[key]}%")
    verdict.fell += [
        f"prose {key}: under the line, recorded {shares[key]}%"
        for key in shares
        if key not in today["prose_share"]
    ]


def compare(recorded: dict[str, dict[str, float]], now: Shape) -> Verdict:
    """What moved between the record and today.

    A function that leaves one module and arrives in another no longer and no more branched than
    it left keeps its number: splitting a module moves functions, and a move is not a new entry.
    """
    verdict = Verdict()
    today = now.as_record()
    for name in COUNTS:
        was, is_ = recorded.get(name, {}), today[name]
        gone = [key for key in was if key not in is_]
        for key, value in is_.items():
            if key not in was:
                moved = next(
                    (
                        old
                        for old in gone
                        if name in BY_FUNCTION
                        and _base_name(old) == _base_name(key)
                        and was[old] >= value
                    ),
                    None,
                )
                if moved is None:
                    verdict.added.append(f"{name} {key}: {value}, over the line and not recorded")
                else:
                    gone.remove(moved)
                    verdict.fell.append(f"{name} {key}: moved from {moved}")
            elif value > was[key]:
                verdict.rose.append(f"{name} {key}: {value}, recorded {was[key]}")
            elif value < was[key]:
                verdict.fell.append(f"{name} {key}: {value}, recorded {was[key]}")
        verdict.fell += [f"{name} {key}: under the line, recorded {was[key]}" for key in gone]
    _compare_prose(recorded, today, verdict)
    return verdict


def write(now: Shape, path: Path = RECORD) -> None:
    body = {"_note": _NOTE, **now.as_record()}
    path.write_text(json.dumps(body, indent="\t") + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str]) -> int:
    record = "--record" in argv
    if len(python_files()) < AT_LEAST:
        print(f"code shape: under {AT_LEAST} Python files read; the walk has stopped finding them")
        return 1
    now = measure()
    if not RECORD.exists():
        if not record:
            print(f"{RECORD.name} is missing: python scripts/check_code_shape.py --record")
            return 1
        write(now)
        return 0
    verdict = compare(json.loads(RECORD.read_text(encoding="utf-8")), now)
    if verdict.refused:
        print("Over its line and past what is recorded, or new and over the line. Shorten it:\n")
        print("  " + "\n  ".join(verdict.rose + verdict.added))
        return 1
    if verdict.fell and not record:
        print("RATCHET FELL: record it so it cannot be given back:")
        print("    python scripts/check_code_shape.py --record\n  " + "\n  ".join(verdict.fell))
        return 1
    if verdict.fell:
        write(now)
        print(f"code shape: recorded {len(verdict.fell)} change(s) in {RECORD.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
