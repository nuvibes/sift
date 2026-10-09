# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Visual C++ runtime a release carries beside its interpreter."""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
from pathlib import Path

from release_common import ReleaseFailed

#: The Visual C++ runtime carried beside the interpreter, where Windows looks before System32.
CPP_RUNTIME = (
    "concrt140.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
)


#: Carried too where the source has them, at its version: a library may import any of them.
CPP_COMPANIONS = (
    "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll",
    "vcruntime140_threads.dll",
    "vccorlib140.dll",
)


def file_version(path: Path) -> str | None:
    """A Windows file's version from its fixed version block, or None where it has none."""
    data = path.read_bytes()
    at = data.find(b"\xbd\x04\xef\xfe")
    if at < 0 or len(data) < at + 16:
        return None
    high, low = struct.unpack_from("<II", data, at + 8)
    return f"{high >> 16}.{high & 0xFFFF}.{low >> 16}.{low & 0xFFFF}"


def _visual_studio_redist() -> Path:
    """The newest installed C++ toolchain's redistributable folder, as its own tools name it."""
    named = os.getenv("VCTOOLSREDISTDIR")
    if named:
        return Path(named)
    installer = Path(os.getenv("PROGRAMFILES(X86)", "C:\\Program Files (x86)"))
    vswhere = installer / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        raise ReleaseFailed(f"there is no {vswhere}, so no C++ runtime to carry.")
    asked = [str(vswhere), "-latest", "-products", "*", "-property", "installationPath"]
    home = Path(subprocess.run(asked, capture_output=True, text=True, check=True).stdout.strip())
    default = home / "VC" / "Auxiliary" / "Build" / "Microsoft.VCRedistVersion.default.txt"
    if not default.is_file():
        raise ReleaseFailed(f"{home} has no C++ build tools, so no C++ runtime to carry.")
    return home / "VC" / "Redist" / "MSVC" / default.read_text(encoding="utf-8").strip()


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def whole_cpp_runtime(folder: Path) -> str | None:
    """The one version all of CPP_RUNTIME, and every companion present, carries in `folder`."""
    versions = {
        file_version(folder / name) if (folder / name).is_file() else None for name in CPP_RUNTIME
    }
    present = {file_version(folder / name) for name in CPP_COMPANIONS if (folder / name).is_file()}
    whole = len(versions) == 1 and None not in versions and present <= versions
    return versions.pop() if whole else None


def cpp_runtime_source(redist: Path | None = None) -> tuple[Path, str]:
    """The newer whole x64 C++ runtime of the toolchain's redistributable and Windows' installed one."""
    candidates = [Path(os.getenv("SYSTEMROOT", "C:\\Windows")) / "System32"]
    try:
        folder = _visual_studio_redist() if redist is None else redist
        candidates += sorted(folder.glob("x64/Microsoft.VC*.CRT"))[-1:]
    except (ReleaseFailed, OSError, subprocess.CalledProcessError):
        pass
    whole = [(whole_cpp_runtime(one), at, one) for at, one in enumerate(candidates)]
    found = [(_parts(version), at, one, version) for version, at, one in whole if version]
    if not found:
        raise ReleaseFailed(
            "no whole x64 C++ runtime is on this machine. Install the C++ build tools."
        )
    _, _, folder, version = max(found)
    print(f"  C++ runtime from {folder}  {version}")
    return folder, version


def carry_the_cpp_runtime(into: Path) -> None:
    """The newest C++ runtime this machine has, beside the interpreter, over what it came with."""
    source, _ = cpp_runtime_source()
    names = [name for name in CPP_RUNTIME + CPP_COMPANIONS if (source / name).is_file()]
    for name in names:
        shutil.copyfile(source / name, into / name)
    print(f"  C++ runtime  {len(names)} files carried")


def linker_version(path: Path) -> tuple[int, int] | None:
    """The toolset a Windows binary was linked with, from its PE optional header; None if not one."""
    with path.open("rb") as handle:
        head = handle.read(0x40)
        if len(head) < 0x40 or head[:2] != b"MZ":
            return None
        handle.seek(struct.unpack_from("<I", head, 0x3C)[0])
        pe = handle.read(28)
    return (pe[26], pe[27]) if len(pe) == 28 and pe[:4] == b"PE\0\0" else None


def newest_linked(folder: Path) -> tuple[tuple[int, int], Path] | None:
    """The newest toolset any bundled binary was linked with; an older linker cannot raise it."""
    linked = [
        (linker_version(one), one)
        for one in folder.rglob("*")
        if one.suffix.lower() in (".pyd", ".dll")
    ]
    found = [(version, one) for version, one in linked if version is not None]
    return max(found) if found else None
