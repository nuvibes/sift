#!/usr/bin/env python3
"""No dash made of two hyphens anywhere in the repository, and an em dash in text on screen.

**The whole tree.** Every line of every text file git knows about (tracked, or written and not yet
added) is refused a dash typed as space, two hyphens, space; a line ending in space and two hyphens
with its sentence carried onto the next; a line that opens, after its comment marker, with two
hyphens and a space, which is the same dash wrapped; and the dash before an escaped newline in a
string, the same wrap inside a message. A sentence that wants a dash is written with a
comma, a colon, a full stop or parentheses instead. The places two hyphens are not a dash at all
(SQL comments, a command's end of options, a banner's rule) are named in `EXEMPTIONS`, each with
the reason it holds; nothing else is let through.

**Text on screen.** An em dash is what belongs in a sentence somebody reads, so copy is held to it
in four places:

1. A quoted string in the client's script.
2. Prose written straight into the markup between two tags, where most of the prose in this app
   lives.
3. **The server's own strings.** Every setting's help text, every refusal a person reads and every
   label the client draws without editing comes from Python. They are matched by ARGUMENT NAME
   rather than by scanning every string, because Python prose is not all for a screen: see
   `DISPLAY_ARGUMENTS`.
4. **Everything else the server writes for a person, and the desktop shell.** A download row can
   say a sentence no argument name reaches, and the desktop shell's own dialogs are read nowhere
   else. The server half is `tests/gates/server_copy.py`, the one reader of server copy, taking the
   strings it finds under any rule OTHER than the ten argument names above; the shell half is its
   quoted strings outside comments and log calls.

   That half is a RATCHET, not a ban: each file's count is recorded in
   `display_dashes_baseline.json` beside this script and may only fall. A rise fails, and a fall
   fails until it is recorded with `python scripts/check_display_dashes.py --record`, so the
   recorded number is always the true one.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
import types
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path("frontend/src")
SERVER = Path("src/sift")
DESKTOP = Path("desktop/src")
BASELINE = Path(__file__).resolve().parent / "display_dashes_baseline.json"


def _server_copy_module() -> types.ModuleType:
    """`tests/gates/server_copy.py`, the one reader of server copy, loaded by path.

    By path rather than by import because this script runs from a hook with the repository root as
    its working directory and no package on the path, the same way `test_gone_is_only_a_404.py`
    borrows this file in the other direction.
    """
    path = Path(__file__).resolve().parents[1] / "tests" / "gates" / "server_copy.py"
    spec = importlib.util.spec_from_file_location("_server_copy", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load the server copy reader from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_server_copy"] = module  # a dataclass looks its module up while it is made
    spec.loader.exec_module(module)
    return module


#: Argument names whose value is read by a person.
#:
#: A list rather than "every string in the file" because Python prose is not all for a screen: a
#: log line or a message for a developer is not copy, and an em dash there would be out of place in
#: an ASCII source. Add a name when a new one starts carrying a sentence somebody reads; a name
#: missing from here is a gap in this check, not a licence.
#:
#: The set itself lives in `tests/gates/server_copy.py`, where the reader of all server copy tells
#: this surface from the rest; one definition, read by both.
_SERVER_COPY = _server_copy_module()
DISPLAY_ARGUMENTS: frozenset[str] = _SERVER_COPY.DISPLAY_ARGUMENTS


STRING = re.compile(r"""(['"])((?:\\.|(?!\1)[^\\\n])*)\1""")
#: A template literal, which may run over several lines. The shell writes its dialogs this way.
TEMPLATE = re.compile(r"`((?:\\.|[^`\\])*)`", re.S)


def _blank(match: re.Match[str]) -> str:
    """The same number of lines, none of the content, so a report still points at the right line."""
    return "\n" * match.group().count("\n")


def _without_comments(text: str) -> str:
    """Blank out comments, keeping line numbers so a report points at the right line."""
    text = re.sub(r"/\*.*?\*/", _blank, text, flags=re.S)
    text = re.sub(r"<!--.*?-->", _blank, text, flags=re.S)
    return re.sub(r"^(\s*)//.*$", r"\1", text, flags=re.M)


#: A template literal handed straight to `Error(`: a message for the console, never drawn.
_ERROR_ARGUMENT = re.compile(r"\bError\(\s*$")


def client_template_dashes(code: str) -> list[tuple[int, str]]:
    """`(line, text)` for every client TEMPLATE literal with the wrong dash, comments already out.

    The quoted-string scan above reads `'...'` and `"..."` only, so a sentence built with a
    substitution (a toast's "N more were grouped with it" followed by a dash and "check them on
    their page") would reach the screen past it. A template passed straight to `Error(` is a message for a developer,
    not copy, and is left out, the way a log call is for the shell."""
    return [
        (code[: match.start()].count("\n") + 1, match.group(1))
        for match in TEMPLATE.finditer(code)
        if " -- " in match.group(1)
        and not _ERROR_ARGUMENT.search(code[max(0, match.start() - 40) : match.start()])
    ]


def _markup_only(text: str) -> str:
    """Everything a template renders, with the script and the style blocks taken out.

    What is left is the prose written between tags. Strings inside the script are handled by the
    scan below; this is the other half.

    A `/* */` comment is blanked here as well as in `_without_comments`, and that is not tidiness.
    An inline handler in an attribute, `onclick={() => { /* why */ }}`, is markup by this
    function's reckoning, so a comment inside one would be read as prose on screen and reported as
    copy with the wrong dash. Nothing renders a block comment, and nobody writes one as a sentence for a
    reader, so blanking it takes nothing real out of the check.
    """
    text = re.sub(r"<script[\s\S]*?</script>", _blank, text)
    text = re.sub(r"<style[\s\S]*?</style>", _blank, text)
    text = re.sub(r"<!--[\s\S]*?-->", _blank, text)
    return re.sub(r"/\*[\s\S]*?\*/", _blank, text)


#: Where two hyphens between spaces are not a dash, by name, with the reason each one holds. Every
#: other line of every text file in the repository is refused one.
EXEMPTIONS: dict[str, str] = {
    "sql-comment": (
        "Inside a SQL statement two hyphens open a comment, and SQLite keeps a statement's text as"
        " written: a schema's comments and a trigger's body are stored data."
    ),
    "seam-marker": (
        "A string holding one SQL comment line, used to cut a statement at that line, has to equal"
        " the statement's own text."
    ),
    "the-dash-itself": "A dash quoted on its own is code looking for the dash, as this file is.",
    "end-of-options": (
        "Two hyphens alone end a command's options: unquoted in shell, and before an address in a"
        " command line kept as a string."
    ),
    "section-banner": "Hyphens drawn either side of a comment's title are a rule, not a dash.",
    "stored-sentence": (
        "Words older versions stored in rows, matched whole so a row can be said in today's words."
    ),
    "lockfile": "A lockfile is the package manager's output, word for word.",
}

_LOCKFILE = re.compile(r"(?:^|/)(?:[^/]+\.lock|package-lock\.json)$")
_THE_DASH_ITSELF = re.compile(r"""(['"`]) -{2} \1""")
_BEFORE_AN_ADDRESS = re.compile(r" -{2} (?=[a-z][a-z0-9+.-]*://)")
_BANNER = re.compile(r"^\s*(?:#|//|;)\s*-{2,}\s+\S.*?\s-{2,}\s*$")
_STORED_SENTENCE = re.compile(
    r"(?<=often temporary) -{2} (?=try it again)| -{2} (?=it is not a site any of its downloaders)"
)
#: A comment marker a wrapped line can open with before its text.
_WRAPPED = re.compile(r"^\s*(?:#:?|///?|/\*+|\*|;|<!--)?\s*--(?:\s|$)")
#: A string is SQL when it names a statement, or when one of its lines is a SQL comment and it
#: carries a fragment's words as well. Prose in capitals for emphasis ("NOT", "OR") has neither
#: shape on its own, so a sentence is never taken for a statement by one word.
_SQL_STATEMENT = re.compile(
    r"\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|PRAGMA|WHERE|JOIN|VALUES|ORDER BY"
    r"|GROUP BY)\b"
)
_SQL_COMMENT_LINE = re.compile(r"(?m)^[ \t]*--(?:[ \t]|$)")
_SQL_FRAGMENT = re.compile(r"\b(?:AND|OR|NOT|EXISTS|CASE|WHEN|THEN|ELSE|END|IS|NULL|IN|ON|AS)\b")
_SEAM_MARKER = re.compile(r"^\n[ \t]*--[ \t][^\n]*\n?$")
_HEREDOC = re.compile(r"<<-?\s*['\"]?([A-Za-z_]\w*)['\"]?")
_YAML_RUN = re.compile(r"^(\s*)(?:-\s+)?run:\s*(.*)$")
_FENCE = re.compile(r"^\s*(?:```|~~~)")


def _neutral(match: re.Match[str]) -> str:
    """The match with its hyphens turned into letters, so no rule below can see a dash in it."""
    return match.group().replace("-", "x")


def refused(line: str) -> bool:
    """Whether one line, its exemptions already neutralised, carries the dash in any of its shapes.

    Four shapes: between two words; at the end of a line, the sentence carried onto the next; at
    the start of one, after its comment marker; and before an escaped newline inside a string, the
    same wrap in a message built across several lines."""
    return (
        " -- " in line
        or line.rstrip().endswith(" --")
        or " --\\n" in line
        or bool(_WRAPPED.match(line))
    )


def _shell_masked(lines: list[str]) -> list[str]:
    """Shell code with every unquoted end-of-options marker neutralised.

    Quoted text and comments are left as they are, because a message or a comment is a sentence. A
    heredoc's body is left too: it is text handed to something else, not arguments."""
    out: list[str] = []
    quote = ""
    heredoc: str | None = None
    for line in lines:
        if heredoc is not None:
            out.append(line)
            if line.strip() == heredoc:
                heredoc = None
            continue
        masked, quote, heredoc = _shell_masked_line(line, quote, heredoc)
        out.append(masked)
    return out


def _shell_masked_line(line: str, quote: str, heredoc: str | None) -> tuple[str, str, str | None]:
    """One line masked, with the quote and heredoc it leaves open."""
    chars = list(line)
    i = 0
    while i < len(line):
        c = line[i]
        if quote:
            i, quote = _in_quotes(c, i, quote)
        elif c == "\\":
            i += 1
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or line[i - 1].isspace()):
            break
        elif (
            line.startswith("--", i)
            and (i == 0 or line[i - 1].isspace())
            and (i + 2 == len(line) or line[i + 2].isspace())
        ):
            chars[i] = chars[i + 1] = "x"
            i += 1
        elif line.startswith("<<", i) and not line.startswith("<<<", i):
            found = _HEREDOC.match(line, i)
            heredoc = found.group(1) if found else heredoc
        i += 1
    return "".join(chars), quote, heredoc


def _in_quotes(c: str, i: int, quote: str) -> tuple[int, str]:
    """One character inside a quote: where the scan goes on, and the quote still open."""
    if quote == "'":
        return i, "" if c == "'" else quote
    if c == "\\":
        return i + 1, quote
    if c == '"':
        return i, ""
    return i, quote


def _yaml_masked(lines: list[str]) -> list[str]:
    """A workflow's `run:` steps read as shell; everything else in the file as prose."""
    out = list(lines)
    i = 0
    while i < len(lines):
        found = _YAML_RUN.match(lines[i])
        if found is None:
            i += 1
            continue
        indent, rest = len(found.group(1)), found.group(2)
        if rest[:1] in {"|", ">"}:
            end = i + 1
            while end < len(lines) and (
                not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) > indent
            ):
                end += 1
            out[i + 1 : end] = _shell_masked(lines[i + 1 : end])
            i = end
            continue
        out[i] = lines[i][: found.start(2)] + _shell_masked([rest])[0]
        i += 1
    return out


def _markdown_masked(lines: list[str]) -> list[str]:
    """A fenced block in Markdown is commands or code, so it reads as shell; the prose around it
    does not."""
    out = list(lines)
    start: int | None = None
    for number, line in enumerate(lines):
        if not _FENCE.match(line):
            continue
        if start is None:
            start = number + 1
            continue
        out[start:number] = _shell_masked(lines[start:number])
        start = None
    return out


def _is_sql(value: str) -> bool:
    """Whether a string's value is a SQL statement or a fragment of one."""
    if _SQL_STATEMENT.search(value):
        return True
    return bool(_SQL_COMMENT_LINE.search(value) and _SQL_FRAGMENT.search(value))


def _python_sql_masked(text: str, lines: list[str]) -> list[str]:
    """Python with the comments inside its SQL strings neutralised, and nothing else.

    Read with `ast` rather than by pattern, because a line that opens with two hyphens is a SQL
    comment inside a statement and a wrapped dash inside a docstring, and only the string it sits in
    tells the two apart. A string standing alone as a statement is prose (a docstring, or one of
    its kind) and is never SQL."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return lines
    prose = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    out = list(lines)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
        elif isinstance(node, ast.JoinedStr):
            value = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
        else:
            continue
        if id(node) in prose or node.end_lineno is None or node.end_col_offset is None:
            continue
        if "--" not in value:
            continue
        seam = bool(_SEAM_MARKER.match(value))
        if not seam and not _is_sql(value):
            continue
        for number in range(node.lineno, node.end_lineno + 1):
            raw = out[number - 1].encode("utf-8")
            begin = node.col_offset if number == node.lineno else 0
            end = node.end_col_offset if number == node.end_lineno else len(raw)
            segment = raw[begin:end]
            at = segment.find(b"--")
            if at < 0:
                continue
            cut = begin + at
            masked = raw[:cut] + b"x" * (end - cut) + raw[end:]
            out[number - 1] = masked.decode("utf-8", "replace")
    return out


def file_dashes(path: str, text: str) -> list[tuple[int, str]]:
    """`(line, text)` for every line of one file that carries the dash, after its exemptions."""
    if _LOCKFILE.search(path):
        return []
    original = text.split("\n")
    lines = list(original)
    if not any(refused(line) for line in lines):
        return []
    lines = _masked(path, text, lines)
    found: list[tuple[int, str]] = []
    for number, line in enumerate(lines, 1):
        if _BANNER.match(line):
            continue
        for rule in (_THE_DASH_ITSELF, _BEFORE_AN_ADDRESS, _STORED_SENTENCE):
            line = rule.sub(_neutral, line)
        if refused(line):
            found.append((number, original[number - 1]))
    return found


def _masked(path: str, text: str, lines: list[str]) -> list[str]:
    """The lines with what each kind of file says in code rather than in words masked out."""
    if path.endswith(".py"):
        lines = _python_sql_masked(text, lines)
    elif path.endswith(".sh"):
        lines = _shell_masked(lines)
    elif path.endswith((".yml", ".yaml")):
        lines = _yaml_masked(lines)
    elif path.endswith(".md"):
        lines = _markdown_masked(lines)
    return lines


def listed(root: Path) -> list[str]:
    """Every file git knows about under `root`: the index, and whatever is written and not yet added.

    The second half is what lets a new file be read before its first commit; a listing of the index
    alone never sees it."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return sorted({one for one in result.stdout.decode("utf-8", "replace").split("\0") if one})


def tree_dashes(root: Path) -> list[tuple[str, int, str]]:
    """`(file, line, text)` for every line in the repository at `root` that carries the dash."""
    paths = listed(root)
    if not paths:
        raise SystemExit("git listed no files here, so this check would pass any tree")
    found: list[tuple[str, int, str]] = []
    for path in paths:
        file = root / path
        if not file.is_file():
            continue
        data = file.read_bytes()
        if b"\0" in data:
            continue
        text = data.decode("utf-8", "replace")
        found += [(path, number, line) for number, line in file_dashes(path, text)]
    return found


def _tree_side() -> int:
    """Refuse the dash made of two hyphens on every line of the repository, and say where."""
    found = tree_dashes(Path.cwd())
    if not found:
        return 0
    print("A dash made of two hyphens, which this repository does not write:\n")
    for path, number, line in found:
        said = line.strip()[:100].encode("ascii", "backslashreplace").decode("ascii")
        print(f"  {path}:{number}\n      {said}")
    print(
        f"\n{len(found)} lines in {len({path for path, _, _ in found})} files. Rewrite the sentence"
        " with a comma, a colon, a full stop or parentheses."
    )
    return 1


def main() -> int:
    tree = _tree_side()
    return _display_side() or tree


def _display_side() -> int:
    found: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        if path.suffix not in {".svelte", ".ts"} or ".test." in path.name:
            continue
        text = path.read_text(encoding="utf-8")
        code = _without_comments(text)
        for match in STRING.finditer(code):
            if " -- " not in match.group(2):
                continue
            line = code[: match.start()].count("\n") + 1
            found.append(f"  {path}:{line}\n      {match.group(2).strip()[:100]}")
        for line, said in client_template_dashes(code):
            found.append(f"  {path}:{line}\n      {said.strip()[:100]}")

        # And the prose written straight into the template, which is where most of it is.
        if path.suffix == ".svelte":
            for number, line_text in enumerate(_markup_only(text).splitlines(), 1):
                if " -- " in line_text:
                    found.append(f"  {path}:{number}\n      {line_text.strip()[:100]}")
    found += _server_side()

    if found:
        print("Text on screen must use an em dash:\n")
        print("\n".join(found))
        print(f"\n{len(found)} to fix. Use an em dash in anything the app renders.")
        return 1
    return _ratchet(record="--record" in sys.argv)


#: A call to the shell's logger or the console, argument list and one level of nesting. A log line
#: is read in a log file, not on screen, and may say " -- " like any comment.
_LOG_CALL = re.compile(
    r"\b(?:log|logger|console)\.\w+\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)", re.S
)


def desktop_strings(text: str) -> list[tuple[int, str]]:
    """`(line, string)` for every quoted string in one shell module, outside comments and log calls.

    The shell's one reader of words: the dash check below and the spelling check in
    `tests/gates/test_copy_vocabulary.py` both judge what this returns."""
    code = _LOG_CALL.sub(_blank, _without_comments(text))
    return [
        (code[: match.start()].count("\n") + 1, match.group(match.lastindex or 0))
        for match in (*STRING.finditer(code), *TEMPLATE.finditer(code))
    ]


def desktop_dashes(text: str) -> list[tuple[int, str]]:
    """`(line, string)` for every quoted string in one shell module with the wrong dash, outside
    comments and log calls."""
    return [(line, said) for line, said in desktop_strings(text) if " -- " in said]


def widened_server(copy: list[Any]) -> list[tuple[str, int, str]]:
    """`(file, line, text)` for every piece of server copy with the wrong dash that the ten argument
    names above do not already hold at zero."""
    return [
        (one.path, one.line, one.text)
        for one in copy
        if one.via not in DISPLAY_ARGUMENTS and " -- " in one.text
    ]


def _widened() -> list[tuple[str, int, str]]:
    """Every wrong dash the ratchet counts: the rest of the server's copy, and the desktop shell."""
    found = widened_server(_SERVER_COPY.server_copy())
    for path in sorted(DESKTOP.rglob("*.ts")):
        if ".test." in path.name:
            continue
        for line, said in desktop_dashes(path.read_text(encoding="utf-8")):
            found.append((path.as_posix(), line, said))
    return found


def _ratchet(*, record: bool) -> int:
    """Hold the widened surface's count per file at or under what was recorded."""
    found = _widened()
    now = Counter(path for path, _line, _text in found)
    if not BASELINE.exists():
        if record:
            _write(now)
            return 0
        print(f"{BASELINE.name} is missing: python scripts/check_display_dashes.py --record")
        return 1
    recorded: dict[str, int] = {
        key: value
        for key, value in json.loads(BASELINE.read_text(encoding="utf-8")).items()
        if not key.startswith("_")
    }
    rose = [path for path in sorted(now) if now[path] > recorded.get(path, 0)]
    fell = [path for path in sorted(recorded) if now.get(path, 0) < recorded[path]]
    if rose:
        print("Text on screen must use an em dash. These files say ' -- ' more than recorded:\n")
        for path, line, text in found:
            if path in rose:
                print(f"  {path}:{line}\n      {text.strip()[:100]}")
        return 1
    if fell and not record:
        print(
            "RATCHET FELL: fewer ' -- ' on screen than recorded. Record it so it cannot come back:"
        )
        print("    python scripts/check_display_dashes.py --record\n  " + "\n  ".join(fell))
        return 1
    if fell:
        _write(now)
    return 0


def _write(now: Counter[str]) -> None:
    note = (
        "Server copy outside the ten argument names, and the desktop shell: ' -- ' on screen, "
        "per file. May only fall; see scripts/check_display_dashes.py."
    )
    BASELINE.write_text(
        json.dumps({"_note": note, **dict(sorted(now.items()))}, indent="\t") + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"display dashes: recorded {sum(now.values())} in {len(now)} files")


def _server_side() -> list[str]:
    """The strings the server sends to be read, found by the name of the argument they are passed to.

    Parsed rather than pattern-matched: a keyword argument's value can be split over several lines,
    joined from adjacent literals, or wrapped in parentheses, and every one of those forms reads
    perfectly normally on screen. A regular expression sees three of them as something else.
    """
    found: list[str] = []
    for path in sorted(SERVER.rglob("*.py")):
        if "/tests/" in path.as_posix() or path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - ruff would have failed first
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg not in DISPLAY_ARGUMENTS:
                    continue
                for text in _literal_text(keyword.value):
                    if " -- " in text:
                        found.append(f"  {path}:{keyword.value.lineno}\n      {text.strip()[:100]}")
    return found


def _literal_text(node: ast.expr) -> list[str]:
    """Every string literal inside an expression, however it was assembled.

    Adjacent literals across lines are already one constant by the time this runs; an explicit `+`
    or a parenthesised group is not, so the tree is walked rather than the node read. An f-string's
    literal parts count: the words around a number are still words somebody reads.
    """
    out: list[str] = []
    for inner in ast.walk(node):
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
            out.append(inner.value)
    return out


if __name__ == "__main__":
    sys.exit(main())
