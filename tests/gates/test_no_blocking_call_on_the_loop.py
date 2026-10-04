# SPDX-License-Identifier: AGPL-3.0-or-later
"""Refuses a blocking call written directly into asynchronous code.

Sift is one process with one event loop shared by the API, the live feed and every video playing,
so a call that waits on the disk or a process stops all of them, silently: even logging is on the
loop. A call handed to a thread is not written as a call (`asyncio.to_thread(path.stat)`), so no
exception list is needed:

    a call is on the loop     when it sits inside an `async def` and is not awaited
    a call is off the loop    when it is awaited, handed to an offloader, or `async with`-ed
    a plain `def` is fine     nested or not: a synchronous body is what gets given to a thread

Blocking helpers are followed within a module only, never guessed across the tree; a function
marked `@waits_on_storage` counts wherever it is imported. A file handle is followed, so
`handle.write(chunk)` on the loop is caught and `async with db.write()` is not.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src" / "sift"

# Calls that wait on the disk, a process or filesystem metadata, by the standard library's names.
# `replace` (which collides with `str.replace`) and `which` (cached once per process) are left out:
# a check with false positives is a check somebody turns off.
BLOCKING = frozenset(
    {
        # pathlib and os
        "open", "read_bytes", "read_text", "write_bytes", "write_text",
        "stat", "lstat", "exists", "is_file", "is_dir", "is_symlink", "samefile",
        "mkdir", "rmdir", "unlink", "rename", "chmod",
        "iterdir", "glob", "rglob", "walk", "listdir", "scandir", "resolve",
        "getsize", "makedirs", "remove", "link", "symlink", "readlink",
        # shutil
        "copyfile", "copy", "copy2", "copytree", "move", "rmtree", "disk_usage",
        # subprocess, and the synchronous sleep
        "check_output", "check_call", "communicate", "Popen", "sleep",
    }
)  # fmt: skip

# Names the standard library shares with an in-memory container: they count only on their module,
# so `os.remove(path)` is a finding and `connections.remove(connection)` is not.
ON_ITS_MODULE = {"remove": frozenset({"os"})}

# What a file handle bound by `open` may not do on the loop: only blocking on a real handle, since
# `db.write()` and `packs.read(raw)` share the names.
HANDLE_BLOCKING = frozenset({"write", "read", "readline", "readlines", "writelines", "seek"})

# Callables that run a function elsewhere, followed only to spot the handle a threaded `open` binds.
OFFLOADERS = frozenset({"to_thread", "run_in_executor", "run_sync"})


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    source: str

    def __str__(self) -> str:
        return f"{self.path.relative_to(REPO)}:{self.line}: {self.source}"


def _called_name(node: ast.Call) -> str | None:
    """The bare name a call names, plainly or on something."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _opens_a_file(node: ast.expr) -> bool:
    """Whether this expression produces a file handle, opened directly or on a thread, so a later
    edit that unthreads a write stays visible."""
    if isinstance(node, ast.Await):
        return _opens_a_file(node.value)
    if not isinstance(node, ast.Call):
        return False
    name = _called_name(node)
    if name == "open":
        return True
    if name in OFFLOADERS and node.args:
        first = node.args[0]
        return isinstance(first, ast.Attribute) and first.attr == "open"
    return False


class _Visitor(ast.NodeVisitor):
    """Walks one module, reporting the calls that block the loop."""

    def __init__(self, path: Path, blocking: frozenset[str]) -> None:
        self.path = path
        self.findings: list[Finding] = []
        self._blocking = blocking
        self._in_async = False
        self._handles: set[str] = set()
        self._exempt: set[int] = set()

    # --- where we are ---------------------------------------------------------------------------

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        was, handles = self._in_async, self._handles
        self._in_async, self._handles = True, set()
        self.generic_visit(node)
        self._in_async, self._handles = was, handles

    def _synchronous_body(self, node: ast.AST) -> None:
        """A plain `def` or a lambda: its body is what gets handed to a thread, so it is off the
        loop, even inside an async function."""
        was, handles = self._in_async, self._handles
        self._in_async, self._handles = False, set()
        self.generic_visit(node)
        self._in_async, self._handles = was, handles

    visit_FunctionDef = _synchronous_body
    visit_Lambda = _synchronous_body

    # --- what has already been taken off the loop -----------------------------------------------

    def visit_Await(self, node: ast.Await) -> None:
        self._exempt.add(id(node.value))
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        # An async context manager, however it is named.
        for item in node.items:
            self._exempt.add(id(item.context_expr))
        self._note_handles(node)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        self._note_handles(node)
        self.generic_visit(node)

    def _note_handles(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            target = item.optional_vars
            if isinstance(target, ast.Name) and _opens_a_file(item.context_expr):
                self._handles.add(target.id)

    def visit_Assign(self, node: ast.Assign) -> None:
        if _opens_a_file(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._handles.add(target.id)
        self.generic_visit(node)

    # --- the check ------------------------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        if self._in_async and id(node) not in self._exempt and self._blocks(node):
            self.findings.append(Finding(self.path, node.lineno, ast.unparse(node)[:100]))
        self.generic_visit(node)

    def _blocks(self, node: ast.Call) -> bool:
        name = _called_name(node)
        if name is None or _off_its_module(node, name):
            return False
        if name in self._blocking:
            return True
        if name in HANDLE_BLOCKING and isinstance(node.func, ast.Attribute):
            receiver = node.func.value
            return isinstance(receiver, ast.Name) and receiver.id in self._handles
        return False


def _off_its_module(node: ast.Call, name: str) -> bool:
    """Whether a name in `ON_ITS_MODULE` is called on something other than its module:
    `queue.remove(item)` rather than `os.remove(path)`."""
    if name not in ON_ITS_MODULE or not isinstance(node.func, ast.Attribute):
        return False
    receiver = node.func.value
    return not (isinstance(receiver, ast.Name) and receiver.id in ON_ITS_MODULE[name])


def _direct_calls(node: ast.AST) -> set[str]:
    """Every name called inside a function, except in the coroutines it merely defines.

    Defining a nested `async def` runs none of its body, which the visitor reads on its own when
    awaited; counting it would make a builder blocking for a coroutine awaiting a store's `resolve`.
    Plain `def`s and lambdas are followed, since they do run.
    """
    found: set[str] = set()

    def walk(current: ast.AST) -> None:
        for child in ast.iter_child_nodes(current):
            if isinstance(child, ast.AsyncFunctionDef):
                continue
            if isinstance(child, ast.Call):
                name = _called_name(child)
                if name is not None and not _off_its_module(child, name):
                    found.add(name)
            walk(child)

    walk(node)
    return found


def _blocking_helpers(tree: ast.Module) -> frozenset[str]:
    """This module's own synchronous functions that themselves block: called from async code, a
    helper meant for a thread is the fault under a local name. Followed within the module only."""
    bodies: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            bodies[node.name] = _direct_calls(node)

    blocking = {name for name, calls in bodies.items() if calls & BLOCKING}
    while True:
        grown = {name for name, calls in bodies.items() if calls & blocking}
        if grown <= blocking:
            return frozenset(blocking)
        blocking |= grown


#: The decorator a function wears to say it waits on storage somebody else owns.
MARKER = "waits_on_storage"


def _marked_in(tree: ast.Module) -> frozenset[str]:
    """The functions this module declares as waiting on storage."""
    return frozenset(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            (isinstance(d, ast.Name) and d.id == MARKER)
            or (isinstance(d, ast.Attribute) and d.attr == MARKER)
            for d in node.decorator_list
        )
    )


def _imported_names(tree: ast.Module) -> frozenset[str]:
    """Every name this module pulled in with `from x import y`, under the name it uses."""
    return frozenset(
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    )


def _marked_everywhere() -> frozenset[str]:
    """Every `@waits_on_storage` function in the tree, by name, read once per run."""
    names: set[str] = set()
    for path in _modules():
        names |= _marked_in(ast.parse(path.read_text(encoding="utf-8")))
    return frozenset(names)


def _findings_in(
    source: str, path: Path = Path("<probe>"), marked: frozenset[str] = frozenset()
) -> list[Finding]:
    tree = ast.parse(source)
    # A marked name counts only where imported or defined: an `ImportFrom` names exactly one.
    reachable = marked & (_imported_names(tree) | _marked_in(tree))
    visitor = _Visitor(path, BLOCKING | _blocking_helpers(tree) | reachable)
    visitor.visit(tree)
    return visitor.findings


def _modules() -> list[Path]:
    return sorted(
        path
        for path in SOURCE.rglob("*.py")
        if "tests" not in path.parts and not path.name.startswith("test_")
    )


#: Calls that block, are on the loop, and are meant to be. The bar is high: a `stat` on a local
#: filesystem answers in microseconds, and a thread would cost more than it saves. Keyed by file and
#: the call's text, so moving a line needs no edit and changing the call does.
ALLOWED_ON_THE_LOOP: dict[str, set[str]] = {
    "slices/library_roots/router.py": {"presence(Path(root.abs_path))"},
    # One `is_file()` in Sift's own data directory, which is always local, before a long subprocess.
    "kernel/ml/accel.py": {"installed(settings)"},
}


def _excused(finding: Finding) -> bool:
    """Whether this exact call, in this file, is meant to be here."""
    for where, calls in ALLOWED_ON_THE_LOOP.items():
        if finding.path.as_posix().endswith(where) and finding.source in calls:
            return True
    return False


def test_nothing_blocks_the_event_loop() -> None:
    """No shipped module makes a blocking call from asynchronous code."""
    marked = _marked_everywhere()
    findings: list[Finding] = []
    for path in _modules():
        findings.extend(_findings_in(path.read_text(encoding="utf-8"), path, marked))

    left = [one for one in findings if not _excused(one)]
    assert not left, "blocking calls on the event loop:\n" + "\n".join(str(one) for one in left)


def test_every_excuse_is_still_a_real_call() -> None:
    """Every excuse still matches a real call: matched on text, a stale one would pass a future
    line."""
    marked = _marked_everywhere()
    found = {
        (one.path.as_posix(), one.source)
        for path in _modules()
        for one in _findings_in(path.read_text(encoding="utf-8"), path, marked)
    }
    stale = [
        f"{where}: {call}"
        for where, calls in ALLOWED_ON_THE_LOOP.items()
        for call in calls
        if not any(seen.endswith(where) and source == call for seen, source in found)
    ]
    assert not stale, "these excuses no longer name a call that exists:\n  " + "\n  ".join(stale)


def test_the_check_still_sees_a_blocking_call() -> None:
    """The check still sees a blocking call, so the green above is for the right reason."""
    caught = _findings_in(
        "import asyncio\n"
        "from pathlib import Path\n"
        "async def reads(path: Path) -> bytes:\n"
        "    return path.read_bytes()\n"
    )
    assert [finding.line for finding in caught] == [4]


def test_the_check_sees_a_call_to_a_helper_that_blocks() -> None:
    """A call to a module helper that blocks is caught: moving work into a helper is how it gets
    threaded, so only the call decides."""
    source = (
        "import asyncio\n"
        "from pathlib import Path\n"
        "def _write(paths, body):\n"
        "    for path in paths:\n"
        "        path.write_bytes(body)\n"
        "async def threaded(paths, body) -> None:\n"
        "    await asyncio.to_thread(_write, paths, body)\n"
        "async def on_the_loop(paths, body) -> None:\n"
        "    _write(paths, body)\n"
    )
    assert [finding.line for finding in _findings_in(source)] == [9]


def test_a_coroutine_a_helper_only_defines_is_not_work_the_helper_does() -> None:
    """A builder that only defines a coroutine is not blocking, and a nested call that really waits
    is still reported on its own line: both halves, since dropping either looks the same."""
    defines_one = (
        "def build(app):\n"
        "    async def destination():\n"
        "        return (await app.options.resolve(None)).folder_id\n"
        "    app.capture = destination\n"
        "async def start(app) -> None:\n"
        "    build(app)\n"
    )
    assert _findings_in(defines_one) == []

    really_blocks = (
        "from pathlib import Path\n"
        "def build(app):\n"
        "    async def destination():\n"
        "        return Path(app.dir).stat()\n"
        "    app.capture = destination\n"
    )
    assert [finding.line for finding in _findings_in(really_blocks)] == [4]


def test_a_helper_that_only_reaches_a_blocker_through_another_is_followed() -> None:
    """A wrapper around a wrapper is followed."""
    source = (
        "from pathlib import Path\n"
        "def _inner(path: Path) -> bytes:\n"
        "    return path.read_bytes()\n"
        "def _outer(path: Path) -> bytes:\n"
        "    return _inner(path)\n"
        "async def calls(path: Path) -> bytes:\n"
        "    return _outer(path)\n"
    )
    assert [finding.line for finding in _findings_in(source)] == [7]


def test_the_check_sees_a_write_to_an_opened_handle() -> None:
    """A whole file written a piece at a time from the loop is caught."""
    caught = _findings_in(
        "from pathlib import Path\n"
        "async def writes(path: Path, body: bytes) -> None:\n"
        "    with path.open('wb') as handle:\n"
        "        handle.write(body)\n"
    )
    assert [finding.line for finding in caught] == [3, 4]


def test_the_check_follows_a_handle_opened_on_a_thread() -> None:
    """A handle opened on a thread and written on the loop is caught: a half-reverted fix."""
    caught = _findings_in(
        "import asyncio\n"
        "from pathlib import Path\n"
        "async def writes(path: Path, body: bytes) -> None:\n"
        "    with await asyncio.to_thread(path.open, 'wb') as handle:\n"
        "        handle.write(body)\n"
    )
    assert [finding.line for finding in caught] == [5]


def test_a_name_the_standard_library_shares_with_a_list_counts_only_on_its_module() -> None:
    """`remove` counts on `os` and not on a list, so the receiver decides."""
    caught = _findings_in(
        "import os\n"
        "async def deletes(path) -> None:\n"
        "    os.remove(path)\n"
        "async def forgets(pool, item) -> None:\n"
        "    pool.remove(item)\n"
    )
    assert [finding.line for finding in caught] == [3]


def test_a_helper_that_takes_an_item_out_of_a_list_does_not_block() -> None:
    """The receiver rule followed into a helper: popping a queue does not block, deleting a file
    does."""
    caught = _findings_in(
        "import os\n"
        "def _next(queue, item):\n"
        "    queue.remove(item)\n"
        "def _drop(path):\n"
        "    os.remove(path)\n"
        "async def streams(queue, item, path) -> None:\n"
        "    _next(queue, item)\n"
        "    _drop(path)\n"
    )
    assert [finding.line for finding in caught] == [8]


def test_the_check_passes_work_that_is_off_the_loop() -> None:
    """The three ways of being correct pass."""
    assert not _findings_in(
        "import asyncio\n"
        "from pathlib import Path\n"
        "async def threaded(path: Path) -> bytes:\n"
        "    return await asyncio.to_thread(path.read_bytes)\n"
        "async def transacted(db) -> None:\n"
        "    async with db.write() as connection:\n"
        "        await connection.execute('SELECT 1')\n"
        "def synchronous(path: Path) -> bytes:\n"
        "    return path.read_bytes()\n"
        "async def hands_a_body_over(path: Path) -> bytes:\n"
        "    def body() -> bytes:\n"
        "        return path.read_bytes()\n"
        "    return await asyncio.to_thread(body)\n"
    )


# --- the cross-module mark: it fires, spares threaded work, ignores a guessed name, and is worn

_MARKED_MODULE = (
    "from sift.kernel.threads import waits_on_storage\n"
    "@waits_on_storage\n"
    "def reads_a_share(path):\n"
    "    return path.read_bytes()\n"
)


def test_a_marked_function_called_from_async_is_caught() -> None:
    """A marked function called from async code is caught."""
    caught = _findings_in(
        "from sift.kernel.ingress import verify_ingress\n"
        "async def probe(path):\n"
        "    return verify_ingress(path)\n",
        marked=frozenset({"verify_ingress"}),
    )

    assert [one.source for one in caught] == ["verify_ingress(path)"]


def test_a_marked_function_handed_to_a_thread_is_not_caught() -> None:
    """A marked function handed to a thread is not."""
    caught = _findings_in(
        "import asyncio\n"
        "from sift.kernel.ingress import verify_ingress\n"
        "async def probe(path):\n"
        "    return await asyncio.to_thread(verify_ingress, path)\n",
        marked=frozenset({"verify_ingress"}),
    )

    assert not caught


def test_a_marked_name_is_ignored_where_it_was_never_imported() -> None:
    """A method that merely shares a marked function's name is not caught where it was never
    imported."""
    caught = _findings_in(
        "async def probe(store, path):\n    return store.verify_ingress(path)\n",
        marked=frozenset({"verify_ingress"}),
    )

    assert not caught


def test_something_in_the_tree_actually_wears_the_mark() -> None:
    """Something in the tree wears the mark, or the rule could never fire."""
    marked = _marked_everywhere()

    assert marked, "nothing is marked @waits_on_storage, so the cross-module rule is dead"
    assert "verify_ingress" in marked and "read_ends" in marked, sorted(marked)
