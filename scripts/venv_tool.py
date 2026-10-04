#!/usr/bin/env python
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run one of the project's own tools, from either platform.

    python scripts/venv_tool.py ruff check --fix

A virtual environment keeps its executables in `Scripts/` on Windows and `bin/` elsewhere, and a
hook naming one layout cannot start on the other. The hook, a terminal and CI share one version.
A tool in `PINNED_TOOLS` keeps its own dependencies out of `uv.lock`: it runs from a locked script
under `scripts/tools/` that names its version once, and every runner of it comes through here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Both layouts, in the order that puts the running platform's first.
CANDIDATES = (
    ("Scripts", ".exe") if sys.platform == "win32" else ("bin", ""),
    ("bin", "") if sys.platform == "win32" else ("Scripts", ".exe"),
)


def resolve(name: str) -> Path:
    for folder, suffix in CANDIDATES:
        candidate = ROOT / ".venv" / folder / f"{name}{suffix}"
        if candidate.is_file():
            return candidate
    looked = ", ".join(str(ROOT / ".venv" / folder) for folder, _ in CANDIDATES)
    raise SystemExit(
        f"\n  {name} is not in this project's environment.\n"
        f"  Looked in: {looked}\n"
        f"  Create it first (uv sync), so the hook runs the same version everything else does.\n"
    )


#: A tool kept out of `uv.lock`, and the locked script that names its version.
PINNED_TOOLS = {"semgrep": ROOT / "scripts" / "tools" / "semgrep_pinned.py"}


def find_uv() -> str:
    """uv: `UV` when set, then PATH, then where its installer puts it."""
    home = Path.home() / ".local" / "bin"
    for candidate in (
        os.environ.get("UV"),
        shutil.which("uv"),
        home / "uv.exe",
        home / "uv",
    ):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise SystemExit(
        "\n  uv is not on PATH or in ~/.local/bin, and the pinned tools run through it.\n"
    )


def pinned_command(name: str, args: list[str]) -> list[str]:
    """`--locked` refuses a lock that no longer matches the version the script names."""
    script = PINNED_TOOLS[name]
    return [find_uv(), "run", "--quiet", "--locked", "--script", str(script), *args]


def main(argv: list[str]) -> int:
    if not argv:
        raise SystemExit("usage: venv_tool.py TOOL [ARGS...]")
    if argv[0] in PINNED_TOOLS:
        command = pinned_command(argv[0], argv[1:])
        return subprocess.run(command, cwd=os.getcwd(), check=False).returncode
    tool = resolve(argv[0])
    # Not `exec`: Windows has no execv that replaces the process in a way a parent can wait on
    # sensibly, and pre-commit reads the exit code.
    return subprocess.run([str(tool), *argv[1:]], cwd=os.getcwd(), check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
