# SPDX-License-Identifier: AGPL-3.0-or-later
"""A slice does not carry finished work with no way to ask for it.

The route gate cannot see work that never became a route. Every public method on a `*Service` must
be mentioned outside its definition, as an attribute ending at a word boundary, in a module that
could hold that service: one naming the class, its conventional binding, or a seam declaring the
method. Another slice's method of the same name is no evidence. Tests do not count: a method kept
alive only by its own test is the shape hunted. Private methods are out of scope.
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.gate


def _source_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "src" / "sift"
        if candidate.is_dir():
            return candidate
    raise AssertionError("could not find src/sift from the test file")


#: Public service methods nothing refers to, each with why that is correct; a sentence hard to write
#: usually means a forgotten way in.
NOT_CALLED_ANYWHERE: dict[str, str] = {}


def service_classes(source: str) -> list[str]:
    """Every `*Service` class this module defines, by name."""
    return [
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ClassDef) and node.name.endswith("Service")
    ]


def public_methods(source: str, *, only: str | None = None) -> dict[str, int]:
    """Every public method on a `*Service` class with its defining line, which is never a mention.
    `only` narrows to one class."""
    found: dict[str, int] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef) or not node.name.endswith("Service"):
            continue
        if only is not None and node.name != only:
            continue
        for item in node.body:
            if isinstance(item, ast.AsyncFunctionDef | ast.FunctionDef) and item.name[0] != "_":
                found[item.name] = item.lineno
    return found


def methods_of(source: str, name: str) -> dict[str, int]:
    """Every public method one class defines, with its line."""
    found: dict[str, int] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef) and node.name == name:
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef | ast.FunctionDef) and item.name[0] != "_":
                    found[item.name] = item.lineno
    return found


def parts_of(module: Path, service: str) -> list[tuple[Path, str]]:
    """The classes a service is put together from inside its slice: each base imported from a
    sibling module, followed in turn."""
    found: list[tuple[Path, str]] = []
    queue = [(module, service)]
    while queue:
        path, name = queue.pop()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        siblings = {
            alias.asname or alias.name: path.parent / f"{node.module.rsplit('.', 1)[1]}.py"
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module
            for alias in node.names
            if node.module.rsplit(".", 1)[0] == f"sift.slices.{path.parent.name}"
        }
        for node in tree.body:
            if not (isinstance(node, ast.ClassDef) and node.name == name):
                continue
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id in siblings:
                    part = (siblings[base.id], base.id)
                    if part not in found and part[0].exists():
                        found.append(part)
                        queue.append(part)
    return found


def held_as(service: str) -> str:
    """The name a service is usually bound to: `LibraryService` -> `library_service`."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", service).lower()


def seams_declaring(name: str) -> frozenset[str]:
    """The seam interfaces declaring a method of this name: a slice reaches another only through a
    Protocol handed it at boot, naming the seam and never the service. Matched by name, without
    importing every service."""
    from sift.kernel import seams as seam_module

    found = set()
    for seam_name in seam_module.__all__:
        interface = getattr(seam_module, seam_name, None)
        if interface is not None and name in getattr(interface, "__protocol_attrs__", frozenset()):
            found.add(seam_name)
    return frozenset(found)


def could_hold(service: str, source: str, *, method: str | None = None) -> bool:
    """Whether this module could hold that service: it names the class, uses its conventional
    binding (`request.app.state.library_service`), or names a seam declaring the method. A module
    that never heard of the service in any form does not count."""
    if re.search(rf"\b{re.escape(service)}\b", source) is not None:
        return True
    if re.search(rf"\b{re.escape(held_as(service))}\b", source) is not None:
        return True
    return any(
        re.search(rf"\b{re.escape(seam)}\b", source) is not None
        for seam in (seams_declaring(method) if method else frozenset())
    )


#: The composition root, whose steps hand each other services through `wiring/built.py`, read as one
#: module.
COMPOSITION_ROOT = ("sift", "wiring")


def in_composition_root(path: Path) -> bool:
    return (path.parent.parent.name, path.parent.name) == COMPOSITION_ROOT


def _mentioned(
    name: str,
    *,
    service: str,
    defined_in: Path,
    on_line: int,
    files: dict[Path, list[str]],
    parts: frozenset[Path] = frozenset(),
) -> bool:
    """Whether anything that could reach this method refers to it besides its definition: `.name`
    to a word boundary (`.resolve_session_state` is not `.resolve_session`), in a module that could
    hold the service."""
    needle = re.compile(rf"\.{re.escape(name)}\b")
    root = "\n".join("\n".join(lines) for path, lines in files.items() if in_composition_root(path))
    root_holds = could_hold(service, root, method=name)
    for path, lines in files.items():
        if in_composition_root(path):
            holds = root_holds
        else:
            holds = could_hold(service, "\n".join(lines), method=name)
        # A part reaches the rest through `self`.
        if path not in parts and not holds:
            continue
        for number, line in enumerate(lines, start=1):
            if path == defined_in and number == on_line:
                continue
            if needle.search(line):
                return True
    return False


def service_modules(root: Path) -> list[Path]:
    """Every slice module defining a `*Service`, not only `service.py` (`insights/path.py`,
    `backup/libraries.py`, `media_edit/editor.py`)."""
    return [
        path
        for path in sorted(root.glob("slices/*/*.py"))
        if service_classes(path.read_text(encoding="utf-8"))
    ]


def _tree_without_tests(root: Path) -> dict[Path, list[str]]:
    return {
        path: path.read_text(encoding="utf-8").splitlines()
        for path in root.rglob("*.py")
        if "tests" not in path.parts
    }


@pytest.mark.regression
def test_every_public_service_method_can_be_reached() -> None:
    root = _source_root()
    files = _tree_without_tests(root)

    unreachable: list[str] = []
    for module in service_modules(root):
        source = module.read_text(encoding="utf-8")
        for service in service_classes(source):
            split = parts_of(module, service)
            owned = [(module, service, public_methods(source, only=service))]
            owned += [
                (path, part, methods_of(path.read_text(encoding="utf-8"), part))
                for path, part in split
            ]
            parts = frozenset(path for path, _part in split)
            for where, owner, methods in owned:
                for name, line in methods.items():
                    if name in NOT_CALLED_ANYWHERE:
                        continue
                    if not _mentioned(
                        name,
                        service=service,
                        defined_in=where,
                        on_line=line,
                        files=files,
                        parts=parts,
                    ):
                        unreachable.append(f"{where.relative_to(root)}::{owner}.{name}")

    assert not unreachable, (
        "\nThese are finished, tested and cannot be reached from anywhere in the application.\n\n"
        "Not a route, not a job, not another service: nothing refers to them at all, so no\n"
        "sequence of actions in the interface can run one. That looks exactly like a working\n"
        "feature from every angle except using it. Either wire it up, delete it, or add it to\n"
        "NOT_CALLED_ANYWHERE with the reason.\n\n  " + "\n  ".join(sorted(unreachable)) + "\n"
    )


def test_the_excuses_are_all_still_public_service_methods() -> None:
    """No excuse outlives what it excused."""
    root = _source_root()
    live = {
        name
        for module in service_modules(root)
        for name in public_methods(module.read_text(encoding="utf-8"))
    }
    stale = sorted(set(NOT_CALLED_ANYWHERE) - live)
    assert not stale, f"these are not public service methods any more: {stale}"


def test_the_walk_reads_a_service_whatever_its_module_is_called() -> None:
    """A service outside `service.py` is walked; a module with none is not."""
    root = _source_root()
    walked = {path.relative_to(root).as_posix() for path in service_modules(root)}
    assert "slices/insights/path.py" in walked
    assert "slices/auth/service.py" in walked
    assert "slices/insights/router.py" not in walked


def test_the_check_reads_a_service_and_skips_what_is_not_one() -> None:
    """A service's methods are read and other classes skipped."""
    source = textwrap.dedent("""
        class FaceService:
            async def import_folder(self, root): ...
            async def _settle(self, asset_id): ...

        class Helper:
            async def tidy(self): ...
    """)
    assert set(public_methods(source)) == {"import_folder"}


def test_the_composition_root_is_read_as_one_module() -> None:
    """A step reaching a service another step named counts; a slice doing the same does not."""
    module = Path("service.py")
    definition = ["class AService:", "    async def release(self): ..."]
    built = Path("src/sift/wiring/built.py")
    call = ["    understanding.faces.release()"]

    reached = {module: definition, built: ["    faces: AService"]}
    reached[Path("src/sift/wiring/reactions.py")] = call
    assert _mentioned("release", service="AService", defined_in=module, on_line=2, files=reached)

    elsewhere = {module: definition, built: ["    faces: AService"]}
    elsewhere[Path("src/sift/slices/tidy/service.py")] = call
    assert not _mentioned(
        "release", service="AService", defined_in=module, on_line=2, files=elsewhere
    )


def test_the_defining_line_is_never_counted_as_a_mention() -> None:
    """The defining line is never counted, or everything passes."""
    module = Path("service.py")
    files = {module: ["class AService:", "    async def import_folder(self): ..."]}

    assert not _mentioned(
        "import_folder", service="AService", defined_in=module, on_line=2, files=files
    )

    with_a_caller = {
        module: ["class AService:", "    async def import_folder(self): ..."],
        Path("router.py"): ["service: AService", "    await service.import_folder(root)"],
    }
    assert _mentioned(
        "import_folder", service="AService", defined_in=module, on_line=2, files=with_a_caller
    )


def test_a_longer_name_starting_with_this_one_is_not_a_mention() -> None:
    """A longer name starting with this one is not a mention."""
    module = Path("service.py")
    files = {
        module: ["class AService:", "    async def resolve_session(self, token): ..."],
        Path("router.py"): ["auth: AService", "    await auth.resolve_session_state(token)"],
    }
    assert not _mentioned(
        "resolve_session", service="AService", defined_in=module, on_line=2, files=files
    )


def test_a_method_of_the_same_name_in_another_slice_is_not_a_mention() -> None:
    """A method of the same name in another slice is not a mention: a mention counts only in a
    module naming the service."""
    module = Path("people/service.py")
    files = {
        module: ["class PeopleService:", "    async def list_usernames(self): ..."],
        # Another slice calling its own service: it has never heard of PeopleService.
        Path("auth/router.py"): ["auth: AuthService", "    await auth.list_usernames()"],
    }

    assert not _mentioned(
        "list_usernames", service="PeopleService", defined_in=module, on_line=2, files=files
    )
