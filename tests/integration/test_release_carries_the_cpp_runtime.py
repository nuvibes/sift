# SPDX-License-Identifier: AGPL-3.0-or-later
"""The C++ runtime the release carries beside its interpreter, and the check that refuses less."""

from __future__ import annotations

import importlib.util
import re
import struct
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.integration

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release.py"

OLD = (14, 44, 35211, 0)
NEW = (14, 51, 36247, 0)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sift_release_cpp_runtime", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load()


def _dll(
    path: Path,
    version: tuple[int, int, int, int] | None,
    linked: tuple[int, int] | None = None,
) -> Path:
    """A file shaped like a DLL as far as its PE header and version block go."""
    body = b"MZ" + bytes(58) + struct.pack("<I", 0x40 if linked else 0)
    if linked is not None:
        body += b"PE\0\0" + bytes(20) + b"\x0b\x02" + bytes(linked)
    if version is not None:
        a, b, c, d = version
        body += b"\xbd\x04\xef\xfe" + struct.pack("<III", 0x10000, (a << 16) | b, (c << 16) | d)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def _runtime(folder: Path, version: tuple[int, int, int, int] = OLD) -> Path:
    for name in release.CPP_RUNTIME:
        _dll(folder / name, version)
    return folder


def _machine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    windows: tuple[int, int, int, int] | None,
    toolchain: tuple[int, int, int, int] | None,
) -> tuple[Path, Path]:
    """Windows' installed runtime and the toolchain's redistributable, either one left out."""
    system = tmp_path / "Windows" / "System32"
    system.mkdir(parents=True)
    if windows is not None:
        _runtime(system, windows)
    monkeypatch.setenv("SYSTEMROOT", str(tmp_path / "Windows"))
    crt = tmp_path / "redist" / "x64" / "Microsoft.VC143.CRT"
    crt.mkdir(parents=True)
    if toolchain is not None:
        _runtime(crt, toolchain)
    monkeypatch.setenv("VCTOOLSREDISTDIR", str(tmp_path / "redist"))
    return system, crt


def test_a_file_version_is_read_from_its_version_block(tmp_path: Path) -> None:
    assert release.file_version(_dll(tmp_path / "a.dll", (14, 44, 35211, 7))) == "14.44.35211.7"
    assert release.file_version(_dll(tmp_path / "b.dll", None)) is None
    (tmp_path / "c.dll").write_bytes(b"MZ\xbd\x04\xef\xfe")
    assert release.file_version(tmp_path / "c.dll") is None


def test_a_linker_version_is_read_from_the_pe_header(tmp_path: Path) -> None:
    assert release.linker_version(_dll(tmp_path / "a.pyd", None, (14, 51))) == (14, 51)
    assert release.linker_version(_dll(tmp_path / "b.dll", None)) is None
    (tmp_path / "c.dll").write_bytes(b"not a binary")
    assert release.linker_version(tmp_path / "c.dll") is None
    (tmp_path / "d.dll").write_bytes(b"MZ" + bytes(58) + struct.pack("<I", 0x400))
    assert release.linker_version(tmp_path / "d.dll") is None


def test_the_newest_toolset_is_read_from_every_bundled_binary(tmp_path: Path) -> None:
    assert release.newest_linked(tmp_path) is None
    _dll(tmp_path / "python313.dll", None, (14, 44))
    _dll(tmp_path / "Lib" / "site-packages" / "sp" / "_sp.PYD", None, (14, 51))
    _dll(tmp_path / "Lib" / "site-packages" / "openblas.dll", None, (2, 36))
    _dll(tmp_path / "Lib" / "site-packages" / "notes.txt", None, (15, 0))

    assert release.newest_linked(tmp_path) == (
        (14, 51),
        tmp_path / "Lib" / "site-packages" / "sp" / "_sp.PYD",
    )


def test_a_whole_runtime_of_one_release_passes_and_says_its_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert release.prove_the_runtime_carries_the_cpp_runtime(_runtime(tmp_path)) == "14.44.35211.0"
    assert "C++ runtime  14.44.35211.0" in capsys.readouterr().out


def test_the_runtime_folder_is_the_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "RUNTIME", _runtime(tmp_path))
    assert release.prove_the_runtime_carries_the_cpp_runtime() == "14.44.35211.0"


def test_a_runtime_missing_msvcp140_is_refused_by_name(tmp_path: Path) -> None:
    (_runtime(tmp_path) / "msvcp140.dll").unlink()
    with pytest.raises(release.ReleaseFailed, match=r"no msvcp140\.dll beside"):
        release.prove_the_runtime_carries_the_cpp_runtime(tmp_path)


def test_a_mix_of_releases_is_refused(tmp_path: Path) -> None:
    _dll(_runtime(tmp_path) / "vcruntime140.dll", (14, 24, 28127, 4))
    with pytest.raises(release.ReleaseFailed, match=re.escape("vcruntime140.dll 14.24.28127.4")):
        release.prove_the_runtime_carries_the_cpp_runtime(tmp_path)


def test_a_runtime_with_no_versions_is_refused(tmp_path: Path) -> None:
    for name in release.CPP_RUNTIME:
        _dll(tmp_path / name, None)
    with pytest.raises(release.ReleaseFailed, match="not one release"):
        release.prove_the_runtime_carries_the_cpp_runtime(tmp_path)


def test_a_runtime_older_than_a_bundled_toolset_is_refused_and_a_new_enough_one_passes(
    tmp_path: Path,
) -> None:
    library = tmp_path / "Lib" / "site-packages" / "sentencepiece" / "_sentencepiece.pyd"
    _dll(library, None, (14, 51))
    _runtime(tmp_path, OLD)
    refusal = "carries C++ runtime 14.44.35211.0, older than Lib"
    with pytest.raises(release.ReleaseFailed, match=re.escape(refusal)) as refused:
        release.prove_the_runtime_carries_the_cpp_runtime(tmp_path)
    assert "_sentencepiece.pyd was linked with (14.51)" in str(refused.value)

    _runtime(tmp_path, NEW)
    assert release.prove_the_runtime_carries_the_cpp_runtime(tmp_path) == "14.51.36247.0"


def test_the_newer_of_windows_and_the_toolchain_is_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    system, _ = _machine(tmp_path, monkeypatch, windows=NEW, toolchain=OLD)

    assert release.cpp_runtime_source() == (system, "14.51.36247.0")
    assert f"C++ runtime from {system}  14.51.36247.0" in capsys.readouterr().out


def test_the_toolchain_is_the_source_when_it_is_newer_or_the_same(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system, crt = _machine(tmp_path, monkeypatch, windows=OLD, toolchain=NEW)
    assert release.cpp_runtime_source() == (crt, "14.51.36247.0")
    _runtime(system, NEW)
    assert release.cpp_runtime_source() == (crt, "14.51.36247.0")


def test_a_runtime_that_is_not_whole_is_never_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system, crt = _machine(tmp_path, monkeypatch, windows=NEW, toolchain=OLD)
    (system / "concrt140.dll").unlink()
    assert release.cpp_runtime_source() == (crt, "14.44.35211.0")

    _dll(crt / "msvcp140_2.dll", (14, 40, 33810, 0))
    with pytest.raises(release.ReleaseFailed, match="no whole x64 C\\+\\+ runtime"):
        release.cpp_runtime_source()


def test_windows_own_runtime_serves_where_there_is_no_toolchain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system, _ = _machine(tmp_path, monkeypatch, windows=NEW, toolchain=None)
    monkeypatch.delenv("VCTOOLSREDISTDIR")
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "nowhere"))

    assert release.cpp_runtime_source() == (system, "14.51.36247.0")
    assert release.cpp_runtime_source(tmp_path / "redist") == (system, "14.51.36247.0")


def test_the_toolchain_is_found_by_its_own_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VCTOOLSREDISTDIR", raising=False)
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path))
    with pytest.raises(release.ReleaseFailed, match=re.escape("vswhere.exe, so no C++ runtime")):
        release._visual_studio_redist()

    vswhere = tmp_path / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    vswhere.parent.mkdir(parents=True)
    vswhere.write_bytes(b"")
    home = tmp_path / "BuildTools"
    build = home / "VC" / "Auxiliary" / "Build"
    build.mkdir(parents=True)
    asked: list[list[str]] = []

    def answer(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        asked.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=f"{home}\r\n")

    monkeypatch.setattr(release.subprocess, "run", answer)
    with pytest.raises(release.ReleaseFailed, match=re.escape("has no C++ build tools")):
        release._visual_studio_redist()
    (build / "Microsoft.VCRedistVersion.default.txt").write_text("14.44.35112\n", encoding="utf-8")

    assert release._visual_studio_redist() == home / "VC" / "Redist" / "MSVC" / "14.44.35112"
    assert asked[0][0] == str(vswhere)
    assert "-latest" in asked[0]


def test_the_six_and_the_companions_present_are_carried_over_the_interpreters_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, crt = _machine(tmp_path, monkeypatch, windows=OLD, toolchain=NEW)
    _dll(crt / "msvcp140_atomic_wait.dll", NEW)
    _dll(crt / "vccorlib140.dll", NEW)
    runtime = tmp_path / "runtime"
    _dll(runtime / "vcruntime140.dll", (14, 40, 33810, 0))

    release.carry_the_cpp_runtime(runtime)

    carried = [*release.CPP_RUNTIME, "msvcp140_atomic_wait.dll", "vccorlib140.dll"]
    assert sorted(one.name for one in runtime.iterdir()) == sorted(carried)
    assert "C++ runtime  8 files carried" in capsys.readouterr().out
    assert release.prove_the_runtime_carries_the_cpp_runtime(runtime) == "14.51.36247.0"


def test_absent_companions_do_not_make_a_runtime_less_than_whole(tmp_path: Path) -> None:
    assert release.whole_cpp_runtime(_runtime(tmp_path, NEW)) == "14.51.36247.0"


def test_a_companion_of_another_release_makes_its_source_not_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system, crt = _machine(tmp_path, monkeypatch, windows=NEW, toolchain=OLD)
    _dll(system / "vcruntime140_threads.dll", (14, 29, 30139, 0))

    assert release.whole_cpp_runtime(system) is None
    assert release.cpp_runtime_source() == (crt, "14.44.35211.0")


def test_a_carried_companion_of_another_release_is_refused(tmp_path: Path) -> None:
    _dll(_runtime(tmp_path, NEW) / "msvcp140_codecvt_ids.dll", OLD)
    with pytest.raises(
        release.ReleaseFailed, match=re.escape("msvcp140_codecvt_ids.dll 14.44.35211.0")
    ):
        release.prove_the_runtime_carries_the_cpp_runtime(tmp_path)
