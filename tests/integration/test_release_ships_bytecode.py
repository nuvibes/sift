# SPDX-License-Identifier: AGPL-3.0-or-later
"""The runtime ships every module compiled, in the form that is checked against its source."""

from __future__ import annotations

import importlib.util
import py_compile
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.integration

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sift_release_bytecode", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load()


def _runtime(tmp_path: Path, mode: py_compile.PycInvalidationMode | None) -> Path:
    for name in ("Lib/os.py", "Lib/site-packages/sift/main.py"):
        source = tmp_path / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("VALUE = 1\n", encoding="utf-8")
        if mode is not None:
            py_compile.compile(str(source), doraise=True, invalidation_mode=mode)
    return tmp_path


def test_a_runtime_compiled_with_checked_hashes_passes(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, py_compile.PycInvalidationMode.CHECKED_HASH)
    release.prove_the_runtime_ships_bytecode(runtime)


def test_a_runtime_without_bytecode_is_refused(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, None)
    with pytest.raises(release.ReleaseFailed, match="2 modules without bytecode"):
        release.prove_the_runtime_ships_bytecode(runtime)


def test_bytecode_trusted_by_its_timestamp_is_refused(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, py_compile.PycInvalidationMode.TIMESTAMP)
    with pytest.raises(release.ReleaseFailed, match="2 compiled without a checked hash"):
        release.prove_the_runtime_ships_bytecode(runtime)


def test_the_build_compiles_before_it_proves() -> None:
    body = _SCRIPT.read_text(encoding="utf-8")
    start = body.index("def build_runtime")
    built = body[start : body.index("\ndef ", start + 1)]
    compiled = built.index("release_bytecode.compile_command(RUNTIME)")
    assert compiled < built.index("prove_the_runtime_ships_bytecode()")


def test_the_compile_writes_checked_hashes_for_the_whole_library(tmp_path: Path) -> None:
    command = release.release_bytecode.compile_command(tmp_path)
    assert command[0] == str(tmp_path / "python.exe")
    assert command[-3:] == ["--invalidation-mode", "checked-hash", str(tmp_path / "Lib")]
