# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every reply describes all of its fields as sent, because it sends all of them.

Nothing serialises with `exclude_unset`, `exclude_none` or `exclude_defaults`, so every declared key
is on every reply, but Pydantic leaves a defaulted field out of the schema's `required` list and a
generated client then treats it as possibly absent. So the application's answerable schemas must
mark every property required, and a class built on `BaseModel` instead of `Wire` is named by file.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from sift.main import create_app

pytestmark = pytest.mark.unit

#: The one module that may name `BaseModel`: it adds the setting.
THE_BASE = Path("src/sift/kernel/wire.py")

#: The setting that makes a serialisation schema say what is true.
THE_SETTING = "json_schema_serialization_defaults_required"

#: FastAPI's own two schemas for a 422 body, with no model here to set it on; named, so a Sift
#: schema cannot drift into the exemption.
NOT_OURS = frozenset({"HTTPValidationError", "ValidationError"})


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _is_a_test(path: Path) -> bool:
    """Tests are exempt: a model declared in a test never reaches the published schema."""
    return path.name.startswith("test_") or "tests" in path.parts


def _classes_built_on_pydantic(source: str) -> list[str]:
    """The classes in one file that inherit `BaseModel` directly, parsed so a docstring naming it is
    not one."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
            if name == "BaseModel":
                found.append(node.name)
    return found


def _references(node: Any, found: set[str]) -> None:
    """Every schema named anywhere inside one piece of the document."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            found.add(ref.rsplit("/", 1)[1])
        for value in node.values():
            _references(value, found)
    elif isinstance(node, list):
        for value in node:
            _references(value, found)


def _answerable(document: dict[str, Any]) -> set[str]:
    """Every schema a route can answer with, followed all the way down; responses only, since a
    request field with a default really is optional."""
    schemas = document["components"]["schemas"]
    seed: set[str] = set()
    for operations in document["paths"].values():
        for operation in operations.values():
            if isinstance(operation, dict):
                _references(operation.get("responses") or {}, seed)

    reached, pending = set(seed), list(seed)
    while pending:
        found: set[str] = set()
        _references(schemas.get(pending.pop(), {}), found)
        for name in found - reached:
            reached.add(name)
            pending.append(name)
    return reached


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    app: FastAPI = create_app()
    return app.openapi()


def test_every_reply_describes_all_of_its_fields_as_sent(document: dict[str, Any]) -> None:
    schemas = document["components"]["schemas"]
    answerable = _answerable(document)

    # Known positive: the walk found something.
    assert "AssetSummary" in answerable, "the walk over what a route can answer found nothing"

    loose: dict[str, list[str]] = {}
    for name in sorted(answerable):
        if name in NOT_OURS:
            continue
        schema = schemas[name]
        required = set(schema.get("required") or [])
        absent = [field for field in (schema.get("properties") or {}) if field not in required]
        if absent:
            loose[name] = absent

    assert loose == {}, (
        "These reply fields are described to every generated client as possibly absent, and the "
        "server sends them on every reply. A model that answers a route inherits "
        f"`sift.kernel.wire.Wire`, which says so: {loose}"
    )


def test_no_model_is_built_on_pydantic_directly() -> None:
    root = _repository_root()
    allowed = (root / THE_BASE).resolve()

    examined = [
        path
        for path in sorted((root / "src" / "sift").rglob("*.py"))
        if path.resolve() != allowed and not _is_a_test(path)
    ]

    # Known positive: the glob read a file that certainly holds models.
    assert (root / "src/sift/slices/people/models.py") in examined, "the walk read no source files"

    guilty = {
        path.relative_to(root).as_posix(): classes
        for path in examined
        if (classes := _classes_built_on_pydantic(path.read_text(encoding="utf-8")))
    }

    assert guilty == {}, (
        "These are built on `BaseModel` directly, so a field of theirs with a default is described "
        "to the client as one that might not arrive. Inherit `sift.kernel.wire.Wire` instead: it "
        "is a `BaseModel` with one setting on it, and it merges with a `model_config` of your "
        f"own: {guilty}"
    )


def _sets_the_setting(source: str) -> bool:
    """Whether `Wire` passes the setting, read from the assignment: the docstring names it too."""
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.ClassDef) and node.name == "Wire"):
            continue
        for statement in node.body:
            if not isinstance(statement, ast.Assign):
                continue
            if not any(getattr(t, "id", None) == "model_config" for t in statement.targets):
                continue
            call = statement.value
            if isinstance(call, ast.Call) and any(
                word.arg == THE_SETTING and getattr(word.value, "value", False) is True
                for word in call.keywords
            ):
                return True
    return False


def test_the_base_really_is_a_model_and_really_carries_the_setting() -> None:
    """`Wire` is a model and carries the setting."""
    source = (_repository_root() / THE_BASE).read_text(encoding="utf-8")

    assert _classes_built_on_pydantic(source) == ["Wire"]
    assert _sets_the_setting(source)
