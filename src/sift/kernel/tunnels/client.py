# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether the tunnel program is where the pack put it, and is the file the pack shipped.

An antivirus can remove it; checked by digest against the pack, retaken only when the file moves."""

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

PROGRAM = "wireproxy.exe"

#: The shipped version as the vendor manifest pins it; a gate holds the two equal.
VERSION = "1.1.3+sift.1"

#: The SHA-256 of the program as Sift builds it from pinned source; the same gate holds it.
SHA256 = "20d51a3f8532e8e1b77f2741fbc63b118111bf7689420aec563a1297befa9546"

#: What every tunnel row and download says when the program is gone, the cause first.
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
    """What the file looked like when its digest was last taken, and whether it matched."""

    size: int
    modified_ns: int
    sound: bool


class ClientCheck:
    """The check, holding what it last saw; one per process (`CHECK`)."""

    def __init__(
        self, folder: Callable[[], Path | None] = vendor_folder, *, digest: str = SHA256
    ) -> None:
        self._folder = folder
        self._digest = digest
        self._seen: _Seen | None = None
        #: What the log was last told, logging a fault once as it starts and once as it ends.
        self._said: str | None = None
        self._lock = threading.Lock()

    def fault(self) -> str | None:
        """The sentence for what is wrong with the program, or None; blocking."""
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


#: The check every tunnel start, the tunnel list and a download's way out ask.
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
