# SPDX-License-Identifier: AGPL-3.0-or-later
"""A field an import is allowed to fill in is a field an import can actually fill in.

Declaring a field `imported=True` puts it on the reconcile screen, in plans and rules, and hands it
to a writer, which may have nowhere to put it. So for every subject with a writer, the keys its
`write` READS (directly, through methods it calls, or through a module-level collection such as
`PROMOTED`) cover every imported field. Whether the value LANDS is the writers' own tests.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from sift.kernel.records import Subject, fields_of

pytestmark = [pytest.mark.gate, pytest.mark.unit]

#: Where the writers live, one module per owning slice.
_ENRICH_MODULES = (
    "sift.slices.people.enrich",
    "sift.slices.stash_boxes.enrich",
    "sift.slices.tags_ratings.enrich",
)


def _writers() -> list[tuple[Subject, ModuleType, type]]:
    """Every writer class: one in those modules with a `subject` and a `write`."""
    found: list[tuple[Subject, ModuleType, type]] = []
    for name in _ENRICH_MODULES:
        module = importlib.import_module(name)
        for _named, held in inspect.getmembers(module, inspect.isclass):
            if held.__module__ != name:
                continue
            subject = getattr(held, "subject", None)
            if isinstance(subject, Subject) and callable(getattr(held, "write", None)):
                found.append((subject, module, held))
    return found


def _methods(tree: ast.Module, class_name: str) -> dict[str, ast.AST]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                one.name: one
                for one in node.body
                if isinstance(one, ast.AsyncFunctionDef | ast.FunctionDef)
            }
    raise AssertionError(f"{class_name} is not in the module that reported it")


def _values_get(node: ast.AST) -> str | None:
    """The key in `values.get("x")`, or None for anything else."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr != "get" or not isinstance(node.func.value, ast.Name):
        return None
    if node.func.value.id != "values" or not node.args:
        return None
    first = node.args[0]
    return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None


def _in_values(node: ast.AST) -> str | None:
    """The key in `"x" in values`, or None."""
    if not isinstance(node, ast.Compare) or len(node.ops) != 1:
        return None
    if not isinstance(node.ops[0], ast.In) or not isinstance(node.left, ast.Constant):
        return None
    if not isinstance(node.left.value, str) or not isinstance(node.comparators[0], ast.Name):
        return None
    return node.left.value if node.comparators[0].id == "values" else None


def _named_collection(node: ast.AST, module: object) -> tuple[str, ...]:
    """The members of a module-level collection of strings named here, asked of the module, since
    `PROMOTED` is built rather than written out."""
    if not isinstance(node, ast.Name):
        return ()
    held = getattr(module, node.id, None)
    if not isinstance(held, set | frozenset | tuple | list) or not held:
        return ()
    return (
        tuple(one for one in held if isinstance(one, str))
        if all(isinstance(one, str) for one in held)
        else ()
    )


def keys_written(tree: ast.Module, class_name: str, module: object) -> set[str]:
    """Every field key one writer's `write` reads, following the methods it calls."""
    methods = _methods(tree, class_name)
    waiting = ["write"]
    seen: set[str] = set()
    keys: set[str] = set()
    while waiting:
        name = waiting.pop()
        if name in seen or name not in methods:
            continue
        seen.add(name)
        for inner in ast.walk(methods[name]):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and isinstance(inner.func.value, ast.Name)
                and inner.func.value.id == "self"
            ):
                waiting.append(inner.func.attr)
            for key in (_values_get(inner), _in_values(inner)):
                if key is not None:
                    keys.add(key)
            keys.update(_named_collection(inner, module))
    return keys


def _source(module: ModuleType) -> ast.Module:
    path = Path(str(module.__file__))
    return ast.parse(path.read_text(encoding="utf-8"))


def test_a_writer_is_found_for_more_than_one_subject() -> None:
    """A walk that found nothing would pass for ever, which is the way a gate dies quietly."""
    subjects = {subject for subject, _module, _held in _writers()}
    assert len(subjects) >= 3, f"only {sorted(one.value for one in subjects)} have writers"


def test_every_importable_field_has_a_writer_that_reads_it() -> None:
    complaints: list[str] = []
    for subject, module, held in _writers():
        read = keys_written(_source(module), held.__name__, module)
        for one in fields_of(subject):
            if one.imported and one.key not in read:
                complaints.append(
                    f"{subject.value}.{one.key} is declared importable and {held.__name__}.write"
                    " never reads it"
                )
    assert not complaints, (
        "\nThese fields are offered to a writer by every plan and nothing writes them. They are\n"
        "counted as written and dropped, which looks exactly like an import that worked.\n"
        "Write them, or take `imported` off the declaration with the reason.\n\n  "
        + "\n  ".join(sorted(complaints))
        + "\n"
    )


#: A writer that takes two fields and drops a third, held as text so it is read like the real ones.
_INCOMPLETE = """
class _HalfWriter:
    subject = None

    async def write(self, local_id, values, *, creating):
        title = values.get("title")
        if title is not None:
            await self._content.set_title(local_id, title)
        for name in self._names(values.get("people")):
            await self._filing.attribute(local_id, name)
"""


def test_the_reading_refuses_a_writer_that_drops_a_field() -> None:
    """The known positive. Without one, a reading that returned every key in the language would
    pass this gate for ever and catch nothing."""
    read = keys_written(ast.parse(_INCOMPLETE), "_HalfWriter", SimpleNamespace())

    assert read == {"title", "people"}
    assert "details" not in read, "the reading claims a key the writer never mentions"


def test_the_reading_follows_a_helper_and_a_named_list() -> None:
    """The two forms a real writer uses that a naive reading of `write` alone would miss."""
    holder: Any = SimpleNamespace(_SOME_COLUMNS=frozenset({"height", "country"}))
    source = """
class _Writer:
    async def write(self, local_id, values, *, creating):
        columns = {key: value for key, value in values.items() if key in _SOME_COLUMNS}
        if columns or "name" in values:
            await self._merge(local_id, columns)
        await self._lists(local_id, values)

    async def _lists(self, local_id, values):
        for url in values.get("links") or ():
            await self._add(local_id, url)
"""

    read = keys_written(ast.parse(source), "_Writer", holder)

    assert read == {"height", "country", "name", "links"}
