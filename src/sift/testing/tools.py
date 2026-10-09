# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stand-ins for command-line tools: a Python body, started by a `.cmd` on Windows or `sh`."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

#: A name, so neither arm below is dead code to the type checker.
ON_WINDOWS = sys.platform == "win32"

#: A venv's executable on Windows is a launcher, and a hanging stand-in behind it outlives a kill.
REAL_PYTHON = getattr(sys, "_base_executable", None) or sys.executable


def stand_in_tool(directory: Path, name: str, body: str) -> str:
    """Write a program called `name` that runs the Python `body` with the tool's arguments."""
    directory.mkdir(parents=True, exist_ok=True)
    program = directory / f"{name}-stand-in.py"
    program.write_text(dedent(body).lstrip("\n"), encoding="utf-8")

    if ON_WINDOWS:
        launcher = directory / f"{name}.cmd"
        launcher.write_text(f'@echo off\r\n"{REAL_PYTHON}" "{program}" %*\r\n', encoding="utf-8")
        return str(launcher)

    launcher = directory / name
    launcher.write_text(
        f'#!/bin/sh\nexec {shlex.quote(REAL_PYTHON)} {shlex.quote(str(program))} "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    return str(launcher)


#: The last answer, not the first: prefer `junction` and `a_full_path`.
POSIX_ONLY = pytest.mark.skipif(
    ON_WINDOWS,
    reason=(
        "Set up with mechanisms Windows does not have: creating a symbolic link needs a privilege "
        "an ordinary account does not hold (WinError 1314), and chmod does not take a directory's "
        "read or write permission away there. Where the guarantee is about escaping a root it is "
        "proved on Windows by a junction instead, which needs no privilege."
    ),
)

WINDOWS_ONLY = pytest.mark.skipif(not ON_WINDOWS, reason="a junction is a Windows reparse point")


def junction(link: Path, target: Path) -> None:
    """Point `link` at `target` with no privilege; not a symlink, so only resolving catches it."""
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert made.returncode == 0, made.stderr


def a_full_path(*parts: str) -> str:
    """A full path as this system spells one: a leading slash is relative on Windows."""
    return str(Path(Path(sys.executable).anchor) / Path(*parts))
