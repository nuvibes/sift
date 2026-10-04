# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every string the SERVER writes for a person to read, found by where it is written.

## Why a reader of its own

About a third of what Sift says on screen is written in Python, and the client's copy gates
cannot see it. `test_one_word_per_thing.py` reads the settings registry and
`scripts/check_display_dashes.py` reads ten argument names; everything else (History sentences,
Organize cards, task names, tidy titles, download failure lines, refusals, record labels) is found
here, so a word refused on every client screen is refused in these too.

## What counts as copy, and why each rule is shaped the way it is

Parsed, never pattern-matched: a sentence can be split over lines, joined from adjacent literals or
built as an f-string, and every one of those reads normally on screen. An f-string is read with each
substitution replaced by a space, because what it substitutes is data, not words anybody chose.

1. **A keyword argument whose purpose is to be read** (`COPY_ARGUMENTS`): every string inside the
   value, one word or many: `label="Faces"` is copy. `name=` counts only on `register_handler`,
   where it is a task's name on Activity; everywhere else `name` is an identifier.
2. **An assignment to a copy name** (`COPY_NAMES`, e.g. a tidying's `title`) or to an UPPER_CASE
   constant, when the value is sentence-shaped. The constants are where shared sentences live
   (`VAULT_LOCKED`, `KEPT_LOCAL_NOTE`); a constant holding SQL or a user agent is not sentence-shaped.
3. **A refusal a person reads**: a string argument of a domain exception (a CamelCase callee ending
   in `Refused`, `Rejected`, `Error`, `NotFound` ... or starting `Not`/`No`) or `HTTPException`,
   when sentence-shaped. The builtins (`ValueError`, `RuntimeError` ...) are programming errors
   nobody is meant to read and are left out.
4. **Every sentence-shaped string in a copy module** (`COPY_MODULES`): the files whose whole job is
   sentences (History's sentence table and the download failure table), where copy is returned
   from functions and kept in dictionaries rather than passed by name. The modules that say a
   History line (`HISTORY_MODULES`, and every area's `worded.py`) are read the same way.
5. **The words of a History line, whatever their shape**: a line is built as
   pieces, and a piece is often one word ("Undone", "something"), which rule 4 refuses for having
   no space in it. So in a History module every argument of a line-builder call (`LINE_CALLS`) is
   read as words, and in the sentence module every string a table holds as a VALUE is too.
6. **A saved decision title** (`history_copy`): the `title` and `detail` a receipt writer saves,
   which a decision card and a History line fall back to for a row that recorded nothing else.
   Found by the rules above; `history_copy` is where they are gathered for the History word table.

A dict's KEYS are never read, by any rule: a key is what a table is looked up by, not what it says.

Never read: docstrings, comments, and anything handed to a log call or `print`: a log line is for
whoever reads the log, and its words are not the interface's.

## What it cannot see

A string built at runtime from two constants in different files is read as its two halves, and a
sentence assembled by a helper with no copy-shaped name is missed. The settings registry is also
read at runtime by the gates that import it, which covers its half of that gap.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "src" / "sift"

#: The argument names `scripts/check_display_dashes.py` reads on its own. Kept as its own set so
#: the dash check can tell that surface from the wider one this reader adds.
DISPLAY_ARGUMENTS = frozenset(
    {
        "consequence",
        "detail",
        "help",
        "hint",
        "label",
        "message",
        "placeholder",
        "reason",
        "summary",
        "title",
    }
)

#: Every keyword argument whose value is read by a person: the ones the tree passes
#: sentence-shaped values to. `description` is deliberately absent: it is
#: the API's own documentation, read by a developer tool rather than on a screen.
COPY_ARGUMENTS = DISPLAY_ARGUMENTS | frozenset(
    {
        "advice",
        "automatic_label",
        "choice_labels",
        "cookies_why",
        "counts",
        "decision",
        "disclosure",
        "doing",
        "done",
        "empty",
        "explain",
        "failed",
        "group_title",
        "heading",
        "lead",
        "meaning",
        "note",
        "noun",
        "problem",
        "question",
        "refusal",
        "section",
        "sentence",
        "skip_reason",
        "tail",
        "verb",
        "verb_one",
        "what",
        "why",
    }
)

#: Names an assignment gives to copy, whatever their case: a tidying's `title` and `detail`.
COPY_NAMES = frozenset({"title", "detail", "label", "help", "group_title", "question", "check"})

#: The files whose sentence-shaped strings are all copy.
COPY_MODULES = (
    "src/sift/kernel/access/sentences.py",
    "src/sift/kernel/access/sentences_downloads.py",
    "src/sift/kernel/access/sentences_edited.py",
    "src/sift/kernel/access/sentences_feed.py",
    "src/sift/kernel/access/sentences_file.py",
    "src/sift/kernel/access/sentences_folded.py",
    "src/sift/kernel/access/sentences_gone.py",
    "src/sift/kernel/access/sentences_ledger.py",
    "src/sift/kernel/access/sentences_lines.py",
    "src/sift/kernel/access/sentences_means.py",
    "src/sift/kernel/access/sentences_people.py",
    "src/sift/kernel/access/sentences_pieces.py",
    "src/sift/kernel/access/sentences_songs.py",
    "src/sift/kernel/access/sentences_sources.py",
    "src/sift/kernel/access/sentences_swaps.py",
    "src/sift/kernel/access/sentences_who.py",
    "src/sift/slices/download/sources/failures.py",
)

#: THE MODULES THAT SAY A HISTORY LINE, read whole like a copy module.
#:
#: One builder per act lives in `sentences.py`, but the readers beside it still write words of
#: their own into a line (a fold's "in ", the feed's "A user who is gone", a decision worded when
#: shown), and a rule that read only the sentence module passed a retired word through any of
#: them. Every area's own `worded.py` (a decision's line, worded from what it recorded) is one of
#: these by its name: see `is_history_module`.
HISTORY_MODULES = (
    "src/sift/kernel/access/sentences.py",
    "src/sift/kernel/access/sentences_downloads.py",
    "src/sift/kernel/access/sentences_edited.py",
    "src/sift/kernel/access/sentences_feed.py",
    "src/sift/kernel/access/sentences_file.py",
    "src/sift/kernel/access/sentences_folded.py",
    "src/sift/kernel/access/sentences_gone.py",
    "src/sift/kernel/access/sentences_ledger.py",
    "src/sift/kernel/access/sentences_lines.py",
    "src/sift/kernel/access/sentences_means.py",
    "src/sift/kernel/access/sentences_people.py",
    "src/sift/kernel/access/sentences_pieces.py",
    "src/sift/kernel/access/sentences_songs.py",
    "src/sift/kernel/access/sentences_sources.py",
    "src/sift/kernel/access/sentences_swaps.py",
    "src/sift/kernel/access/sentences_who.py",
    "src/sift/kernel/access/history.py",
    "src/sift/kernel/access/history_line.py",
    "src/sift/kernel/access/history_reads.py",
    "src/sift/kernel/access/history_actors.py",
    "src/sift/kernel/access/history_boxes.py",
    "src/sift/kernel/access/history_receipts.py",
    "src/sift/kernel/access/history_folds.py",
    "src/sift/kernel/access/history_faces.py",
    "src/sift/kernel/access/history_ledger.py",
    "src/sift/kernel/access/history_sources.py",
    "src/sift/kernel/access/history_unread.py",
    "src/sift/kernel/access/history_presses.py",
    "src/sift/kernel/access/history_person.py",
    "src/sift/kernel/access/history_entity.py",
    "src/sift/kernel/access/history_events.py",
    "src/sift/slices/workbench/router.py",
    "src/sift/kernel/access/worded.py",
    # Insights: every statement the page says, and the recaps' cards, which add three phrases of
    # their own (the top Site, the busiest hour, the closing line). Built with History's own `said`
    # and `thing` and drawn by History's component, so the History word table holds them, but they
    # are SENTENCES, not captions: see `STATEMENT_MODULES`.
    "src/sift/slices/insights/statements.py",
    "src/sift/slices/insights/recaps.py",
)

#: THE HISTORY MODULES THAT SAY A SENTENCE RATHER THAN A CAPTION.
#:
#: A History line is a caption: "Sift cannot download from ..." is how a record reads, so the
#: screen's contractions rule stands aside for it (`test_copy_vocabulary.NOT_ON_HISTORY_LINES`). An
#: Insights statement is read as a sentence, ends in a full stop and says "you" ("That's 12 hours
#: fewer than July."), so it keeps every rule, contractions included. Named here so that reading
#: these modules as History (the word table, the stand-in words) does not quietly take them OUT of
#: the one rule a statement is written to.
STATEMENT_MODULES = (
    "src/sift/slices/insights/statements.py",
    "src/sift/slices/insights/recaps.py",
)

#: The file name every area's decision wording is written in (`kernel.workbench.Words`).
WORDED = "worded.py"

#: THE CALLS A HISTORY LINE IS BUILT FROM, and which of their arguments are its words: positions,
#: then keyword names. `None` is every positional argument.
#:
#: WHATEVER THEIR SHAPE, and that is why this is a rule of its own. A line is pieces, and a piece is
#: often ONE word ("Undone", "something", ", a file of theirs"), which the sentence rule above
#: refuses for having no space in it. `thing` names a thing, so only its words (the third argument)
#: are read: its kind and id are identifiers.
LINE_CALLS: dict[str, tuple[tuple[int, ...] | None, frozenset[str]]] = {
    "said": (None, frozenset()),
    "Piece": ((0,), frozenset({"text"})),
    "Act": ((0, 1), frozenset({"line", "alone", "counted"})),
    "_fill": ((0,), frozenset()),
    "_active": ((1, 2), frozenset()),
    "Said": ((0,), frozenset()),
    "capitalized": ((0,), frozenset()),
    "counted_line": ((1,), frozenset({"counted"})),
    "thing": ((2,), frozenset({"name"})),
}

#: THE MODULES WHOSE TABLES ARE WORDS: every string a dict holds as a VALUE is copy, whatever its
#: shape: a mark's tooltip ("Renamed"), a vantage word ("this file"), a copy's verb ("trimmed"). The
#: one module whose job is words; its keys are identifiers and stay unread. Its kind-to-kind table
#: (`LINKED_KINDS`) is read too, which costs nothing: an identifier is no word any list refuses.
WORD_TABLE_MODULES = (
    "src/sift/kernel/access/sentences.py",
    "src/sift/kernel/access/sentences_downloads.py",
    "src/sift/kernel/access/sentences_edited.py",
    "src/sift/kernel/access/sentences_feed.py",
    "src/sift/kernel/access/sentences_file.py",
    "src/sift/kernel/access/sentences_folded.py",
    "src/sift/kernel/access/sentences_gone.py",
    "src/sift/kernel/access/sentences_ledger.py",
    "src/sift/kernel/access/sentences_lines.py",
    "src/sift/kernel/access/sentences_means.py",
    "src/sift/kernel/access/sentences_people.py",
    "src/sift/kernel/access/sentences_pieces.py",
    "src/sift/kernel/access/sentences_songs.py",
    "src/sift/kernel/access/sentences_sources.py",
    "src/sift/kernel/access/sentences_swaps.py",
    "src/sift/kernel/access/sentences_who.py",
)

#: THE WORDS A SAVED DECISION TITLE IS WRITTEN WITH, in a module that writes a receipt.
#:
#: A decision's title is saved when it is taken and is what a decision card and a History line
#: fall back to for every row that recorded nothing else, so the words a receipt writer passes as
#: its `title` and `detail` are History words, read by the History word table
#: (`test_history_says_the_word_table.py`), not only by the general copy checks.
SAVED_TITLE_VIAS = frozenset({"title", "detail", "assign:title", "assign:detail"})

#: The call that writes a receipt (`kernel.workbench.Recorder.record_on`).
RECEIPT_CALL = "record_on"

#: THE CALL A RUNNING TASK SAYS WHAT IT IS DOING WITH (`JobContext.set_note`), drawn on Activity.
#:
#: Read in the three shapes the tree hands it a sentence: a string written into the call; a call to
#: a helper in the same module whose RETURNS are the sentences (`_swept`,
#: `_handed_out`); and a local name assigned in the same function (`set_note(note)`). A module
#: constant (`MEASURING_FIRST`) is read already, by the upper-case assignment rule. Without it a
#: note could say "still going through the library..." with three full stops while the ellipsis
#: check held the server at zero.
NOTE_CALL = "set_note"


def is_history_module(where: str) -> bool:
    """Whether one module (by its path from the repository root) says History lines."""
    return where in HISTORY_MODULES or where.endswith(f"/{WORDED}")


def says_captions(where: str) -> bool:
    """Whether one module says History CAPTIONS: the register a record is written in, which the
    screen's contractions rule stands aside for. A statement module is read as History and is not
    one of these: see `STATEMENT_MODULES`."""
    return is_history_module(where) and where not in STATEMENT_MODULES


#: Exceptions nobody is meant to read: a programming error, not a refusal.
BUILTIN_EXCEPTIONS = frozenset(
    {
        "AssertionError",
        "AttributeError",
        "ConnectionError",
        "Exception",
        "FileExistsError",
        "FileNotFoundError",
        "IndexError",
        "KeyError",
        "IsADirectoryError",
        "LookupError",
        "NotADirectoryError",
        "NotImplementedError",
        "OSError",
        "PermissionError",
        "RuntimeError",
        "TimeoutError",
        "TypeError",
        "ValueError",
    }
)

#: Sift's own exceptions that are raised by code FOR code (a setting declared wrongly, a broken
#: invariant, a tool's exit status) and reach a log rather than a screen. Judged by reading their
#: messages ("expected true or false", "  is not a condition", "webpinfo failed:  "), not traced to
#: every caller, so a class here that does reach a screen is a gap in this reader.
INTERNAL_EXCEPTIONS = frozenset(
    {
        "AccessError",
        "AlignmentError",
        "ConstraintError",
        "FFmpegError",
        "FieldError",
        "LedgerError",
        "MigrationError",
        "ScheduleError",
        "SettingError",
        "SubprocessError",
        "WebpError",
    }
)

#: A domain exception's name, or a class built to carry a sentence.
_REFUSAL = re.compile(
    r"^(?:HTTPException|Not[A-Z]\w*|No[A-Z]\w*|\w+(?:Refused|Rejected|Error|NotFound|NotAllowed"
    r"|Invalid|Missing|Report|Unavailable|Conflict))$"
)

#: Calls whose arguments are for a log, not a screen.
_LOG_CALLS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "print", "log"}
)

#: What a sentence looks like: a letter first, a space, and no character code needs and prose does
#: not use. SQL is refused by its opening keyword; a path or a user agent by a slash between words.
_SENTENCE = re.compile(r"^\s*[A-Za-z].*\s")
_CODE = re.compile(r"[{}<>=;\\|`]|\w/\w|%[a-zA-Z]|^\s*[a-z_]+\.[a-z_]+")
_SQL = re.compile(
    r"^\s*(?:SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|WITH|PRAGMA|REPLACE|FROM|WHERE|JOIN"
    r"|LEFT|AND|OR|ORDER|GROUP|CASE|WHEN|VALUES|SET|ON|IN|NOT|EXISTS|UNION|LIMIT)\b"
)


@dataclass(frozen=True)
class Copy:
    """One piece of server copy: where it is written, the words, and which rule found it."""

    path: str
    line: int
    text: str
    via: str


#: A `str.format` placeholder (`{site}` in the download failure table), which is a gap in a
#: sentence, not code.
_PLACEHOLDER = re.compile(r"\{\w*\}")


def sentence_shaped(text: str) -> bool:
    """Whether a string reads as words for a person rather than as code, SQL or a path."""
    words = _PLACEHOLDER.sub(" ", text)
    return bool(_SENTENCE.match(words)) and not _CODE.search(words) and not _SQL.match(words)


#: A letter, which is all a piece of a line needs to be words.
_A_LETTER = re.compile(r"[A-Za-z]")


def word_shaped(text: str) -> bool:
    """Whether a string is words of ANY length (one word included) rather than code or SQL.

    The test for a piece of a History line, which is often a single word; see `LINE_CALLS`.
    """
    words = _PLACEHOLDER.sub(" ", text)
    return bool(_A_LETTER.search(words)) and not _CODE.search(words) and not _SQL.match(words)


def _line_words(call: ast.Call) -> Iterator[ast.expr]:
    """The arguments of one line-builder call that are its words. See `LINE_CALLS`."""
    positions, names = LINE_CALLS[_callee(call)]
    for at, argument in enumerate(call.args):
        if positions is None or at in positions:
            yield argument
    for keyword in call.keywords:
        if keyword.arg in names:
            yield keyword.value


def _words_in(node: ast.AST) -> Iterator[tuple[int, str]]:
    """Every whole string under `node`, less the identifiers a line is built WITH: a dict's keys (a
    slot name, a kind), what a comparison compares and what a subscript looks up, at any depth."""
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, ast.Compare | ast.Subscript):
            continue
        if isinstance(current, ast.expr):
            text = _text_of(current)
            if text is not None:
                yield current.lineno, text
                continue
        if isinstance(current, ast.Dict):
            stack.extend(reversed([value for value in current.values if value is not None]))
            continue
        if isinstance(current, ast.Call) and _callee(current) in LINE_CALLS:
            # A line built inside a line: only ITS words: a `thing`'s kind and id are not.
            stack.extend(reversed(list(_line_words(current))))
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(current))))


def _text_of(node: ast.expr) -> str | None:
    """The words of a string expression, a substitution read as a space. None if not a string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else " "
            for part in node.values
        )
    return None


def _strings_in(node: ast.AST) -> Iterator[tuple[int, str]]:
    """Every whole string expression under `node`, an f-string read once rather than per piece."""
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, ast.expr):
            text = _text_of(current)
            if text is not None:
                yield current.lineno, text
                continue
        stack.extend(reversed(list(ast.iter_child_nodes(current))))


def _callee(call: ast.Call) -> str:
    function = call.func
    if isinstance(function, ast.Attribute):
        return function.attr
    if isinstance(function, ast.Name):
        return function.id
    return ""


def _docstrings(tree: ast.Module) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def _log_subtrees(tree: ast.Module) -> set[int]:
    """Every node inside a log or print call, so nothing in one is read as copy."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _callee(node) in _LOG_CALLS:
            found.update(id(inner) for inner in ast.walk(node))
    return found


def _target_name(target: ast.expr) -> str | None:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def copy_in(source: str, where: str) -> list[Copy]:
    """Every piece of copy in one Python module. `where` is its path from the repository root."""
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - ruff would have failed first
        return []
    skip = _docstrings(tree) | _log_subtrees(tree)
    seen: set[tuple[int, str]] = set()
    found: list[Copy] = []

    def emit(line: int, text: str, via: str) -> None:
        if (line, text) in seen or not text.strip():
            return
        seen.add((line, text))
        found.append(Copy(where, line, text, via))

    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Call):
            callee = _callee(node)
            for keyword in node.keywords:
                if keyword.arg in COPY_ARGUMENTS or (
                    keyword.arg == "name" and callee == "register_handler"
                ):
                    for line, text in _strings_in(keyword.value):
                        emit(line, text, keyword.arg)
            if (
                _REFUSAL.match(callee)
                and callee not in BUILTIN_EXCEPTIONS
                and callee not in INTERNAL_EXCEPTIONS
            ):
                for argument in node.args:
                    for line, text in _strings_in(argument):
                        if sentence_shaped(text):
                            emit(line, text, f"refusal:{callee}")
        elif isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                name = _target_name(target)
                if name is None:
                    continue
                if name in COPY_NAMES or name.lstrip("_").isupper():
                    # A table's KEYS are what it is looked up by, never what it says: the stale
                    # phrases `sentences.STALE_IN_A_TITLE` replaces are its keys, and read as copy
                    # they counted the very words the table exists to take off the screen.
                    for line, text in _words_in(node.value):
                        if sentence_shaped(text):
                            emit(line, text, f"assign:{name}")

    for line, text, via in _notes_in(tree, skip):
        if sentence_shaped(text):
            emit(line, text, via)

    if is_history_module(where):
        for node in ast.walk(tree):
            if id(node) in skip or not isinstance(node, ast.Call):
                continue
            if _callee(node) in LINE_CALLS:
                for argument in _line_words(node):
                    for line, text in _words_in(argument):
                        if word_shaped(text):
                            emit(line, text, f"line:{_callee(node)}")
    if where in WORD_TABLE_MODULES:
        for node in ast.walk(tree):
            if id(node) in skip or not isinstance(node, ast.Dict):
                continue
            for value in node.values:
                if value is None:
                    continue
                for line, text in _words_in(value):
                    if word_shaped(text):
                        emit(line, text, "table")

    if where in COPY_MODULES or is_history_module(where):
        keys = {
            id(key) for node in ast.walk(tree) if isinstance(node, ast.Dict) for key in node.keys
        }
        # What a comparison compares and what a subscript looks up are keys, never words shown.
        compared = {
            id(inner)
            for node in ast.walk(tree)
            if isinstance(node, ast.Compare)
            for inner in ast.walk(node)
        } | {
            id(inner)
            for node in ast.walk(tree)
            if isinstance(node, ast.Subscript)
            for inner in ast.walk(node.slice)
        }
        # The literal pieces of an f-string are read as the f-string, never again on their own.
        pieces = {
            id(part)
            for node in ast.walk(tree)
            if isinstance(node, ast.JoinedStr)
            for part in node.values
        }
        excluded = skip | keys | compared | pieces
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant | ast.JoinedStr):
                continue
            if id(node) in excluded:
                continue
            written = _text_of(node)
            if written is not None and sentence_shaped(written):
                emit(node.lineno, written, "module")
    return sorted(found, key=lambda one: (one.line, one.text))


def _notes_in(tree: ast.Module, skip: set[int]) -> Iterator[tuple[int, str, str]]:
    """Every string a task's progress note can be, in the shapes `NOTE_CALL` names."""
    helpers = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for call in ast.walk(function):
            if id(call) in skip or not isinstance(call, ast.Call) or _callee(call) != NOTE_CALL:
                continue
            said = [*call.args[:1], *(k.value for k in call.keywords if k.arg == "note")]
            for argument in said:
                for line, text in _strings_in(argument):
                    yield line, text, "note"
                if (
                    isinstance(argument, ast.Call)
                    and isinstance(argument.func, ast.Name)
                    and argument.func.id in helpers
                ):
                    helper = helpers[argument.func.id]
                    for returned in ast.walk(helper):
                        if isinstance(returned, ast.Return) and returned.value is not None:
                            for line, text in _strings_in(returned.value):
                                yield line, text, f"note:{helper.name}"
                if isinstance(argument, ast.Name):
                    for assigned in ast.walk(function):
                        if isinstance(assigned, ast.Assign) and any(
                            isinstance(target, ast.Name) and target.id == argument.id
                            for target in assigned.targets
                        ):
                            for line, text in _strings_in(assigned.value):
                                yield line, text, f"note:{argument.id}"


def server_files() -> list[Path]:
    """Every server module that can put words on a screen: not a test, a fixture or a schema."""
    return sorted(
        path
        for path in SERVER.rglob("*.py")
        if "tests" not in path.parts
        and "testing" not in path.parts
        and not path.name.startswith("test_")
        # A schema module is DDL and migration steps from end to end: a column list, never a line.
        and path.name != "schema.py"
    )


def server_copy() -> list[Copy]:
    """Every piece of server copy in the tree."""
    found: list[Copy] = []
    for path in server_files():
        where = path.relative_to(REPO).as_posix()
        found.extend(copy_in(path.read_text(encoding="utf-8"), where))
    return found


def writes_a_receipt(source: str) -> bool:
    """Whether a module CALLS `record_on`: writes a decision's receipt, and so its saved title."""
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - ruff would have failed first
        return False
    return any(
        isinstance(node, ast.Call) and _callee(node) == RECEIPT_CALL for node in ast.walk(tree)
    )


def history_copy() -> list[Copy]:
    """Every word a History screen can say that the SOURCE holds: the History modules whole, and
    the saved title and detail of every decision a receipt writer takes (`SAVED_TITLE_VIAS`).

    What it cannot see is a title built by a helper with no copy-shaped name in another module, and
    a title already saved in somebody's library: the first is the general limit of this reader, the
    second is data, which `sentences.today_words` rewrites when it is drawn.
    """
    found: list[Copy] = []
    for path in server_files():
        where = path.relative_to(REPO).as_posix()
        found.extend(history_copy_in(path.read_text(encoding="utf-8"), where))
    return found


def history_copy_in(source: str, where: str) -> list[Copy]:
    """One module's History words: all its copy if it says History lines, its saved titles if it
    writes a receipt, and nothing otherwise. See `history_copy`."""
    if is_history_module(where):
        return copy_in(source, where)
    if writes_a_receipt(source):
        saved = [one for one in copy_in(source, where) if one.via in SAVED_TITLE_VIAS]
        known = {(one.line, one.text) for one in saved}
        return saved + [
            one
            for one in _substituted_into_titles(source, where)
            if (one.line, one.text) not in known
        ]
    return []


def _substituted_into_titles(source: str, where: str) -> list[Copy]:
    """The words of a local name that a receipt's `title` or `detail` substitutes, in the same
    function: `freed = f" That freed {n} bytes."` read into `detail=f"... {freed} ..."`.

    An f-string reads its substitutions as a space (they are data), and a phrase a writer builds
    into a local first is data by that rule, except that here it is the writer's own words,
    saved into the title for good. Read as `saved:<name>`.
    """
    tree = ast.parse(source)
    found: list[Copy] = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        used: set[str] = set()
        for call in ast.walk(function):
            if isinstance(call, ast.Call) and _callee(call) == RECEIPT_CALL:
                for keyword in call.keywords:
                    if keyword.arg in ("title", "detail"):
                        used.update(
                            name.id
                            for name in ast.walk(keyword.value)
                            if isinstance(name, ast.Name)
                        )
        for node in ast.walk(function):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in used:
                    found.extend(
                        Copy(where, line, text, f"saved:{target.id}")
                        for line, text in _words_in(node.value)
                        if sentence_shaped(text)
                    )
    return found
