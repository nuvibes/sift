# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every gate that reads the browser client's source tree shares.

`test_web_gates_reject.py` plants fixtures into the real `frontend/src` to prove the browser gates
fail, while other gates list and read the same tree on parallel workers. Planted files carry
`PLANTED_PREFIX` and are filtered by NAME, never opened, so a listing cannot race a removal.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

#: What every fixture planted into the real source tree is called.
PLANTED_PREFIX = "GateFixture"


def client_source(root: Path, *suffixes: str) -> list[Path]:
    """Every browser-client source file under `root`, in a stable order, without generated
    declarations, test files or planted fixtures."""
    found = [path for suffix in suffixes for path in root.rglob(f"*{suffix}")]
    return sorted(
        path
        for path in found
        if not path.name.startswith(PLANTED_PREFIX)
        and not path.name.endswith((".test.ts", ".d.ts"))
    )


def posix_bash() -> str:
    """The shell the repository's `.sh` scripts run with, as an absolute path: on Windows `bash` on
    PATH is usually the WSL launcher in System32, which cannot reach this checkout. SIFT_BASH
    overrides."""
    override = os.environ.get("SIFT_BASH")
    if override:
        return override
    if sys.platform != "win32":
        return "bash"
    for candidate in (
        Path(r"C:\Program Files\Git\bin\bash.exe"),
        Path(r"C:\Program Files (x86)\Git\bin\bash.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("bash")
    # Anything under the system directory is that launcher.
    if found and "system32" not in found.lower():
        return found
    raise RuntimeError("no POSIX bash was found. Install Git for Windows, or set SIFT_BASH to one.")


#: The lock's file, outside the tree, which the gates themselves read.
_TREE_LOCK = Path(tempfile.gettempdir()) / "sift-client-tree.lock"


@contextmanager
def the_client_tree() -> Iterator[None]:
    """Sole use of `frontend/src` for the block: a planted fixture can break more than its own
    gate. An OS lock, which a worker killed mid-block releases."""
    with _TREE_LOCK.open("a+b") as handle:
        _take(handle)
        try:
            yield
        finally:
            _release(handle)


def _take(handle: IO[bytes]) -> None:
    if sys.platform != "win32":
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return
    # Windows' `LK_LOCK` gives up after ten tries a second apart, so the waiting is done here.
    deadline = time.monotonic() + 900
    while True:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def _release(handle: IO[bytes]) -> None:
    if sys.platform != "win32":
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
