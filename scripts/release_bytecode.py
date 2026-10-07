# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every module of the packed runtime ships compiled, in the form checked against its source."""

from __future__ import annotations

from pathlib import Path

#: The flags word of a `.pyc` compiled in the checked-hash form (PEP 552): hash-based, checked.
_CHECKED_HASH = 0b11


def compile_command(runtime: Path) -> list[str]:
    """Bytecode for every module, checked against its source on import so a stale one is never
    trusted. Without it each start after an install compiles about 1,600 modules first."""
    return [
        str(runtime / "python.exe"),
        "-I",
        "-m",
        "compileall",
        "-q",
        # Forced: the interpreter ships its own timestamp bytecode, which compileall would keep.
        # One process: workers importing a module while another rewrites its bytecode lose the
        # rename on Windows.
        "-f",
        "-j",
        "1",
        "--invalidation-mode",
        "checked-hash",
        str(runtime / "Lib"),
    ]


def uncompiled(runtime: Path) -> str | None:
    """None when every module has its checked-hash `.pyc`, else what is missing, in one sentence."""
    missing: list[str] = []
    unchecked: list[str] = []
    for source in sorted((runtime / "Lib").rglob("*.py")):
        cache = source.parent / "__pycache__"
        compiled = sorted(cache.glob(f"{source.stem}.cpython-*.pyc")) if cache.is_dir() else []
        if not compiled:
            missing.append(source.relative_to(runtime).as_posix())
            continue
        with compiled[0].open("rb") as head:
            flags = int.from_bytes(head.read(8)[4:8], "little")
        if flags != _CHECKED_HASH:
            unchecked.append(source.relative_to(runtime).as_posix())
    if not (missing or unchecked):
        return None
    named = (missing or unchecked)[:3]
    return (
        f"the packed runtime has {len(missing)} modules without bytecode and {len(unchecked)} "
        f"compiled without a checked hash, starting with {', '.join(named)}. Every start would "
        "compile them first; build_runtime compiles them with compileall."
    )
