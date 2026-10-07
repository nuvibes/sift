# SPDX-License-Identifier: AGPL-3.0-or-later
"""A write that works out whose view it moved also tells them.

The picture-address counter and the change bus both need the users a write moved; raising the
counter and not announcing leaves an open screen showing what was taken away. Checked per function
by reading the syntax, and only the two counter writers may raise the counter at all.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

SOURCE = Path(__file__).resolve().parents[2] / "src" / "sift"

#: The calls that resolve an audience. Every one of them raises the counter and hands back who for.
RESOLVERS = frozenset({"bump_cache_stamp", "bump_every_cache_stamp", "bump_stamps_for_object"})

#: What has to happen in the same function.
ANNOUNCE = "announce"

#: Writes that resolve an audience and CANNOT announce, as `path:function`: a schema repair runs at
#: boot with nobody connected.
EXCUSED: dict[str, str] = {
    # A helper that hands the audience back; every caller announces it.
    "slices/suggestions/service_base.py:_told": "returns the audience; every caller announces it",
}

#: The two modules that may write the counter, as paths from the source root.
COUNTER_WRITERS = frozenset(
    {
        "kernel/cache_stamp.py",
        "kernel/access/stamps.py",
    }
)

#: Any statement that raises it.
_RAISES_THE_COUNTER = re.compile(r"cache_stamp\s*=\s*cache_stamp\s*\+")


def _shipping_files() -> list[Path]:
    return [path for path in sorted(SOURCE.rglob("*.py")) if "tests" not in path.parts]


def _called_names(node: ast.AST) -> set[str]:
    """Every name called anywhere inside this node, however it was reached."""
    found: set[str] = set()
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        target = inner.func
        if isinstance(target, ast.Name):
            found.add(target.id)
        elif isinstance(target, ast.Attribute):
            found.add(target.attr)
    return found


def silent_functions(source: str) -> list[str]:
    """The functions in this source that resolve an audience and never announce it."""
    offenders: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        called = _called_names(node)
        if not (called & RESOLVERS):
            continue
        if ANNOUNCE not in called:
            offenders.append(node.name)
    return offenders


def test_every_write_that_resolves_an_audience_announces_it() -> None:
    silent: list[str] = []
    for path in _shipping_files():
        relative = path.relative_to(SOURCE).as_posix()
        if relative in COUNTER_WRITERS:
            # These two ARE the resolvers. They answer the question; they do not consume it.
            continue
        for name in silent_functions(path.read_text(encoding="utf-8")):
            if f"{relative}:{name}" in EXCUSED:
                continue
            silent.append(f"{relative}:{name}")

    assert not silent, (
        "\nThese work out whose view a change moved and then tell nobody.\n\n"
        "Every connection that user has open goes on showing what it was showing, and the only\n"
        "way anybody finds out is by reloading the page, which is the state the change bus\n"
        "replaced. Call announce() with the audience the bump handed back.\n\n  "
        + "\n  ".join(silent)
        + "\n"
    )


def test_the_check_notices_a_write_that_says_nothing() -> None:
    """A write that raises the counter in its transaction and never announces is caught."""
    planted = """
async def hide_a_folder(connection, user_id):
    await connection.execute(SET_HIDDEN, (user_id,))
    await bump_cache_stamp(connection, user_id)
"""

    assert silent_functions(planted) == ["hide_a_folder"]


def test_the_check_is_satisfied_by_announcing() -> None:
    """And that it passes the shape it is asking for, so it is not simply always red."""
    written = """
async def hide_a_folder(connection, user_id):
    await connection.execute(SET_HIDDEN, (user_id,))
    announce(await bump_cache_stamp(connection, user_id), About.LIBRARY)
"""

    assert silent_functions(written) == []


def test_a_function_that_does_neither_is_not_the_gates_business() -> None:
    """The rule is about the pair, not about announcing everywhere. Most writes move nobody's view
    (a rating, a note, a saved search), and telling every connection about them would be the
    noise this feature exists to avoid."""
    written = """
async def rate_a_file(connection, asset_id, stars):
    await connection.execute(SET_RATING, (asset_id, stars))
"""

    assert silent_functions(written) == []


def test_only_the_two_modules_that_answer_the_question_write_the_counter() -> None:
    """The floor under both rules above.

    A statement of one's own that raises the counter somewhere else would satisfy neither: it would
    make the pictures unreachable, tell nobody, and read as an ordinary write. It is also a second
    copy of the rule that decides who is in the audience, and two copies of that rule drift with
    nothing saying so.
    """
    elsewhere = []
    for path in _shipping_files():
        relative = path.relative_to(SOURCE).as_posix()
        if relative in COUNTER_WRITERS:
            continue
        if _RAISES_THE_COUNTER.search(path.read_text(encoding="utf-8")):
            elsewhere.append(relative)

    assert not elsewhere, (
        "\nThese raise the picture-address counter with a statement of their own:\n  "
        + "\n  ".join(elsewhere)
        + "\n\nUse the two functions that answer 'whose view did this move', so that the answer is\n"
        "worked out in one place and both the counter and the change bus read the same one.\n"
    )


def test_the_two_modules_that_may_write_it_are_still_there() -> None:
    """An exemption for a file that has moved is an exemption quietly covering a real one later."""
    for relative in COUNTER_WRITERS:
        assert (SOURCE / relative).is_file(), f"{relative} has moved; update COUNTER_WRITERS"


def test_there_are_writes_to_check() -> None:
    """A walk that found nothing would pass for ever. There are nineteen of these."""
    resolving = 0
    for path in _shipping_files():
        relative = path.relative_to(SOURCE).as_posix()
        if relative in COUNTER_WRITERS:
            continue
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and (
                _called_names(node) & RESOLVERS
            ):
                resolving += 1

    assert resolving >= 10, f"only {resolving} functions resolve an audience; the walk is broken"


def test_every_excuse_names_a_function_that_still_resolves_an_audience() -> None:
    """A stale excuse reads as a considered rule; the day the repair stops bumping, its line goes."""
    for excused in EXCUSED:
        relative, name = excused.split(":")
        path = SOURCE / relative
        assert path.exists(), f"{excused} is excused and its file is not there any more"
        assert name in silent_functions(path.read_text(encoding="utf-8")), (
            f"{excused} no longer resolves an audience without announcing. Delete its excuse"
        )


# --- The writes that change a file's pictures -----------------------------------------------------
#
# The rule above reads "resolves an audience", and a write of a file's pictures resolves none: it
# bumps no stamp, because a picture's address carries its own digest. So a whole class of writes
# (every still, hover clip and scrub strip Sift builds) would pass it while telling nobody, and a
# clip built on request would stay invisible on every open wall until the page was reloaded. This
# is the second rule: a function that writes the `derivatives` table announces, itself or through a
# function of its own module that does.
#
# The store's `add_derivative` is the one door every builder uses, and it announces, so the builders
# that call it need no line of their own here: a builder that went round the door would have to
# write the table itself, and then this rule reads it.

#: A statement that adds, replaces or removes a picture row.
_WRITES_A_PICTURE = re.compile(
    r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)\s+derivatives\b",
    re.IGNORECASE,
)

#: What counts as telling somebody. `telling` is the wrapper that announces on the commit.
ANNOUNCERS = frozenset(
    {"announce", "announce_arrival", "announce_now", "announce_picture", "telling"}
)

#: Writes of pictures that CANNOT announce, each with the reason, as `path:function`.
PICTURE_WRITERS_EXCUSED: dict[str, str] = {
    "testing/fixture_library.py:_file_rows": "a test library planned offline, which nothing is drawing",
    "testing/library.py:seed_asset": (
        "a test seed written straight into a database that no running server holds"
    ),
    "testing/library.py:seed_loop_still": (
        "a test seed written straight into a database that no running server holds"
    ),
}


def _picture_statements(tree: ast.Module) -> set[str]:
    """The module-level names whose SQL writes a picture row."""
    named: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str) or not _WRITES_A_PICTURE.search(node.value.value):
            continue
        named.update(target.id for target in node.targets if isinstance(target, ast.Name))
    return named


def _writes_a_picture(function: ast.AST, statements: set[str]) -> bool:
    for inner in ast.walk(function):
        if isinstance(inner, ast.Name) and inner.id in statements:
            return True
        if (
            isinstance(inner, ast.Constant)
            and isinstance(inner.value, str)
            and _WRITES_A_PICTURE.search(inner.value)
        ):
            return True
    return False


def silent_picture_writers(source: str) -> list[str]:
    """The functions in this source that write a picture row and tell nobody.

    Telling counts when the function calls an announcer, or calls a function of this same module
    that does (the store's `_write_pictures`, say). One step, not a chain: a helper that tells is
    a helper somebody wrote to tell, and a chain of them is a place for the telling to fall out.
    """
    tree = ast.parse(source)
    statements = _picture_statements(tree)
    functions = [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    tellers = {node.name for node in functions if _called_names(node) & ANNOUNCERS}
    return [
        node.name
        for node in functions
        if _writes_a_picture(node, statements)
        and not (_called_names(node) & (ANNOUNCERS | tellers))
    ]


def test_every_write_of_a_picture_announces_it() -> None:
    silent: list[str] = []
    for path in _shipping_files():
        relative = path.relative_to(SOURCE).as_posix()
        for name in silent_picture_writers(path.read_text(encoding="utf-8")):
            if f"{relative}:{name}" in PICTURE_WRITERS_EXCUSED:
                continue
            silent.append(f"{relative}:{name}")

    assert not silent, (
        "\nThese add, replace or remove a file's picture and tell nobody.\n\n"
        "A tile drawn before the write goes on drawing the old picture, or no clip at all, until\n"
        "the page is reloaded. Write through a function that announces the arrival (the content\n"
        "store's `add_derivative` does), or name the reason it cannot below.\n\n  "
        + "\n  ".join(silent)
        + "\n"
    )


def test_the_door_every_builder_uses_announces() -> None:
    """The floor under the rule: `add_derivative` is read as a picture writer, and it tells.

    Without this, a walk that stopped matching the statement would pass every builder silently,
    which is the failure the rule exists to end.
    """
    store = SOURCE / "kernel" / "content" / "identity_derivatives.py"
    source = store.read_text(encoding="utf-8")
    tree = ast.parse(source)
    statements = _picture_statements(tree)
    door = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "add_derivative"
    )

    assert _writes_a_picture(door, statements), "add_derivative is no longer read as a writer"
    assert "add_derivative" not in silent_picture_writers(source)


def test_the_picture_check_notices_a_write_that_says_nothing() -> None:
    planted = """
_ADD = "INSERT INTO derivatives (id) VALUES (?)"

async def add_a_still(db, asset_id):
    async with db.write() as connection:
        await connection.execute(_ADD, (asset_id,))
"""

    assert silent_picture_writers(planted) == ["add_a_still"]


def test_the_picture_check_is_satisfied_through_a_helper_that_tells() -> None:
    written = """
_ADD = "INSERT INTO derivatives (id) VALUES (?)"

async def _write_pictures(db, sql, params):
    async with db.write() as connection:
        await connection.execute(sql, params)
        await announce_arrival(connection)

async def add_a_still(db, asset_id):
    await _write_pictures(db, _ADD, (asset_id,))
"""

    assert silent_picture_writers(written) == []


def test_every_picture_excuse_names_a_function_that_still_writes_silently() -> None:
    for excused in PICTURE_WRITERS_EXCUSED:
        relative, name = excused.split(":")
        path = SOURCE / relative
        assert path.exists(), f"{excused} is excused and its file is not there any more"
        assert name in silent_picture_writers(path.read_text(encoding="utf-8")), (
            f"{excused} no longer writes a picture without announcing. Delete its excuse"
        )
