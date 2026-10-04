# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether the tunnel program is where the pack put it, and is the file the pack shipped.

A virus scanner can take the tunnel program off the disk without asking, days after an install
that finished cleanly: it is a small network program, the kind that gets flagged. What is left is
an install that looks whole and a tunnel that cannot start, and the failure would otherwise surface
as a tunnel that "did not connect" or a download refused over a tunnel that "no longer exists",
neither of which points at the cause. So the program is checked at start-up and before every
start, and when it is gone or altered every tunnel row, and every download that needed a tunnel,
says so in the same words, and the log says it once.

**Checked against the pack, never the machine.** Only a Windows pack has a folder the program was
put in (`vendor_folder`); a checkout on Linux runs whatever copy the machine has, and there is
nothing of Sift's to compare it with. There, a missing program is still the launch's own refusal
(`process._NO_CLIENT`).

**By content, cheaply.** The program is one file of about ten megabytes. Its digest is taken once
and taken again only when the file's size or modification time moves, so asking before every
start, and on every read of the tunnel list, costs a `stat`.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sift.kernel.config import vendor_folder
from sift.kernel.log import get_logger

log = get_logger(__name__)

#: The program's file name in the pack's folder.
PROGRAM = "wireproxy.exe"

#: The version of the program this build ships, as `scripts/vendor_manifest.json` pins it under
#: `built`. A gate holds the two equal (`test_tunnels_client.py`), so moving the pin without moving
#: the digest below fails the build rather than every tunnel on every install.
VERSION = "1.1.3+sift.1"

#: The SHA-256 of `wireproxy.exe` as Sift builds it from its pinned source
#: (`scripts/vendor_build/wireproxy/build.py`, which installs nothing else). The manifest pins the
#: same digest, and the same gate holds the two equal.
SHA256 = "20d51a3f8532e8e1b77f2741fbc63b118111bf7689420aec563a1297befa9546"

#: What every tunnel row and every download that needed a tunnel says when the program is gone.
#: The cause first, because it is the one nobody thinks of after an install that finished cleanly,
#: then the two ways back.
CLIENT_REMOVED = (
    "Windows Defender or another antivirus removed Sift's tunnel program. Restore it from "
    "quarantine and allow it, or install Sift again."
)

#: The same, for a program that is there and is not the file Sift installed.
CLIENT_CHANGED = (
    "Sift's tunnel program isn't the one Sift installed, so Sift won't run it. Install Sift again "
    "to put it back."
)

_CHUNK = 1 << 20


@dataclass(frozen=True, slots=True)
class _Seen:
    """What the file looked like when its digest was last taken, and whether the digest matched."""

    size: int
    modified_ns: int
    sound: bool


class ClientCheck:
    """The check, holding what it last saw. One per process (`CHECK`); a test makes its own over a
    folder of its own."""

    def __init__(
        self, folder: Callable[[], Path | None] = vendor_folder, *, digest: str = SHA256
    ) -> None:
        self._folder = folder
        self._digest = digest
        self._seen: _Seen | None = None
        #: What the log was last told, so a fault is logged when it starts and when it ends, once
        #: each, however many rows and downloads ask in between.
        self._said: str | None = None
        self._lock = threading.Lock()

    def fault(self) -> str | None:
        """The sentence for what is wrong with the program, or None when nothing is (or when there
        is no pack to compare with). Blocking: it may read the whole file, so it runs off the loop
        (`client_fault`)."""
        with self._lock:
            found = self._look()
            if found != self._said:
                if found is None:
                    log.info("tunnel.client_restored")
                else:
                    log.warning(
                        "tunnel.client_lost",
                        reason="removed" if found == CLIENT_REMOVED else "changed",
                    )
                self._said = found
            return found

    def _look(self) -> str | None:
        folder = self._folder()
        if folder is None:
            return None
        program = folder / PROGRAM
        try:
            status = program.stat()
        except OSError:
            self._seen = None
            return CLIENT_REMOVED
        seen = self._seen
        if seen is None or (seen.size, seen.modified_ns) != (status.st_size, status.st_mtime_ns):
            try:
                sound = _digest_of(program) == self._digest
            except OSError:
                # There a moment ago and unreadable now: a scanner holding it, or taking it.
                self._seen = None
                return CLIENT_REMOVED
            seen = _Seen(size=status.st_size, modified_ns=status.st_mtime_ns, sound=sound)
            self._seen = seen
        return None if seen.sound else CLIENT_CHANGED


def _digest_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


#: The check every tunnel start, the tunnel list and the way out of a download ask.
CHECK = ClientCheck()


async def client_fault() -> str | None:
    """`CHECK.fault()`, off the loop."""
    return await asyncio.to_thread(CHECK.fault)


__all__ = [
    "CHECK",
    "CLIENT_CHANGED",
    "CLIENT_REMOVED",
    "PROGRAM",
    "SHA256",
    "VERSION",
    "ClientCheck",
    "client_fault",
]
