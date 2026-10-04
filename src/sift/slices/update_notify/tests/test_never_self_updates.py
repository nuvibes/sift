# SPDX-License-Identifier: AGPL-3.0-or-later
"""The backend says a new version is out and can never install one: installing is the desktop
application's, against its signed manifest. Nothing names a container runtime, and the update
slice starts no process."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

#: Everything in the application, minus the tests, which name these things in order to forbid
#: them, and would make this check fail on its own text.
SOURCE = Path(__file__).resolve().parents[4]


def application_sources() -> list[Path]:
    files = [
        path
        for path in (SOURCE / "sift").rglob("*.py")
        if "tests" not in path.parts and "testing" not in path.parts
    ]
    # A path that matched nothing would make every assertion below vacuously true.
    assert len(files) > 100, f"expected the application's sources, found {len(files)}"
    return files


def test_no_source_file_names_the_container_socket() -> None:
    """The socket is the whole mechanism. Nothing reads it, writes it, or knows its address."""
    for path in application_sources():
        text = path.read_text()
        assert "docker.sock" not in text, path
        assert "/var/run/docker" not in text, path
        assert "DOCKER_HOST" not in text, path


def code_of(path: Path) -> str:
    """A file's source with its comments removed, so only values are scanned."""
    import io
    import tokenize

    kept = [
        token
        for token in tokenize.generate_tokens(io.StringIO(path.read_text()).readline)
        if token.type != tokenize.COMMENT
    ]
    return tokenize.untokenize([(token.type, token.string) for token in kept])


def test_no_source_file_runs_a_container_command() -> None:
    """No container runtime at all, by any name. A later change reaching for podman or nerdctl
    would be the same grant of the host by a different name."""
    runtimes = ("docker ", "docker-compose", "podman", "nerdctl", "systemctl", "kubectl")
    for path in application_sources():
        text = code_of(path)
        for runtime in runtimes:
            assert runtime not in text, f"{path} names {runtime!r}"


def test_the_update_check_calls_nothing_that_runs_a_program() -> None:
    """Read from the syntax tree rather than by searching text, so this is about what the module
    does and not about which words appear near it."""
    module = SOURCE / "sift" / "slices" / "update_notify" / "service.py"
    tree = ast.parse(module.read_text())

    executors = {"run", "call", "Popen", "system", "exec", "spawn", "popen", "check_output"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            assert name not in executors, f"{name} is called in {module.name}"


def test_the_update_slice_imports_nothing_that_can_start_a_process() -> None:
    """Stated as an import rule so it fails at the moment somebody reaches for one, rather than
    later when they use it."""
    forbidden = {"subprocess", "os", "shutil", "pty", "multiprocessing", "sift.kernel.subprocess"}
    package = SOURCE / "sift" / "slices" / "update_notify"

    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in forbidden, f"{path}: {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                assert node.module not in forbidden, f"{path}: {node.module}"
                assert node.module.split(".")[0] not in forbidden, f"{path}: {node.module}"
