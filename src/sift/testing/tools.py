# SPDX-License-Identifier: AGPL-3.0-or-later
"""Plant a stand-in for a command-line tool, on a platform that can run it.

Sift shells out to ffmpeg, ffprobe, webpinfo and anim_dump, and several tests need one of them to
behave in a way the real tool cannot be made to: to hang, to fail, to report a picture forty-five
thousand pixels wide, or to produce frames without an encoder being involved. The usual answer is a
tiny shell script on PATH.

**A shell script is not an executable on Windows.** `CreateProcess` answers "%1 is not a valid Win32
application", so every one of those tests would fail there for a reason that has nothing to do
with what it was testing.

A batch file can be started directly, and would carry a body of its own badly: each of
`< > | & ^ % ( )` means something to the command processor, and what these stand-ins print is
ordinary text somebody will want to edit. So the BODY is Python, always, and only the thing that
starts it differs: a `.cmd` on Windows, a `#!/bin/sh` line on POSIX, each of which does nothing but
run the body with the interpreter this suite is already using.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

#: Named rather than asked inline, so neither arm below is dead code to a type checker reading
#: this on one platform.
ON_WINDOWS = sys.platform == "win32"

#: An interpreter that is the process it says it is.
#:
#: `sys.executable` inside a virtual environment on Windows is a launcher: it starts, and then
#: starts the real interpreter as a child with a different process id. A stand-in reached through
#: two extra processes is one that outlives being killed, and a stand-in that hangs on purpose is
#: exactly what some of these are for.
#: `_base_executable` is not in the typeshed stubs and is documented behaviour rather than
#: private detail: it is what `venv` records so a virtual environment can find its base.
REAL_PYTHON = getattr(sys, "_base_executable", None) or sys.executable


def stand_in_tool(directory: Path, name: str, body: str) -> str:
    """Write a program called `name` into `directory` that runs `body`, and answer with its path.

    `body` is Python source. It is handed the tool's own arguments in `sys.argv` (`sys.argv[1]` is
    the first one, exactly as `$1` would be), and its exit status is the tool's.
    """
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


#: For a test whose SETUP needs something Windows will not do.
#:
#: Two things recur and neither is about Sift. Creating a symbolic link needs a privilege an
#: ordinary account does not hold (WinError 1314), and `chmod` on a directory is accepted and then
#: ignored, so a folder made unreadable or unwritable stays readable and writable and the refusal
#: under test never happens.
#:
#: Reach for this only when the platform cannot be made to hold the situation. Where the guarantee
#: is about escaping a root, a junction expresses it and needs no privilege, and where it is about
#: an absolute path, `a_full_path` spells one the platform recognises. A skip is the last answer,
#: not the first, because a guarantee proved on one platform is not proved on the one Sift ships on.
POSIX_ONLY = pytest.mark.skipif(
    ON_WINDOWS,
    reason=(
        "Set up with mechanisms Windows does not have: creating a symbolic link needs a privilege "
        "an ordinary account does not hold (WinError 1314), and chmod does not take a directory's "
        "read or write permission away there. Where the guarantee is about escaping a root it is "
        "proved on Windows by a junction instead, which needs no privilege."
    ),
)

#: For the other half of a pair: a case only Windows has.
WINDOWS_ONLY = pytest.mark.skipif(not ON_WINDOWS, reason="a junction is a Windows reparse point")


def junction(link: Path, target: Path) -> None:
    """Point `link` at the directory `target`, the way a Windows user actually can.

    `mklink /J` needs no privilege, and unlike a symbolic link the result reports
    `is_symlink() == False`, so a guard that notices links never fires on one. Anything that
    refuses it has to refuse it by resolving the path and finding it outside the root, which is the
    stronger property and the one worth proving on the platform Sift ships on.
    """
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert made.returncode == 0, made.stderr


def a_full_path(*parts: str) -> str:
    """A full path to somewhere, spelled the way this platform spells one.

    `/one/two` on POSIX, `C:\\one\\two` on Windows. What "absolute" LOOKS like is a property of the
    platform, and a leading slash is one of the RELATIVE forms on Windows: resolved against
    whichever drive happens to be current, which is exactly what a check for an absolute path is
    refusing a relative path for.
    """
    return str(Path(Path(sys.executable).anchor) / Path(*parts))
