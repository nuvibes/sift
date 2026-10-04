# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who is listening on a port on this machine, and ending a process Sift itself left behind.

A tunnel client can outlive the Sift that started it (a hard kill of the whole tree leaves a
grandchild standing) and hold its ports, so every boot finds the port taken and cannot tell its
own orphan from a stranger. **Sift owns what it spawns.**

**"Its own" is proved by a token, not a port.** A port says where a process listens and nothing
about who started it: two Sifts on one device can describe each other's clients by port and end each
other's as leftovers. What the caller matches is a token only its own clients carry
(`kernel/tunnels/process.TunnelProcess`); this module only answers who is listening, and ends a
process when asked.

Both answers come from the operating system's own tools rather than a library: the question is
asked about the two ports of each tunnel start, and a process-table dependency for that would be a
dependency for one question.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

from sift.kernel.log import get_logger
from sift.kernel.subprocess import SubprocessError, run

log = get_logger(__name__)

#: How long the lookup may take. A PowerShell start is a fraction of a second; a machine under
#: load may make it several, and a tunnel start can wait for that.
_LOOKUP_TIMEOUT = 15.0

#: Decided at import from the platform's name rather than narrowed on `sys.platform`, so both
#: halves below are ordinary code the checker reads on every platform.
_ON_WINDOWS = sys.platform.startswith("win")


@dataclass(frozen=True, slots=True)
class PortHolder:
    """A process listening on a port: its id, the name of its program, and how it was started."""

    pid: int
    name: str
    command_line: str


def _windows_query(port: int) -> str:
    """One PowerShell program: the listener on the port, then the process behind it.

    The port is an integer checked by the caller, which is the whole of what makes composing a
    program text acceptable here; nothing else a person typed reaches it. Tab-separated, because
    a command line can hold any other character.
    """
    return (
        f"$c = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue "
        "| Select-Object -First 1; "
        "if ($c) { "
        '$p = Get-CimInstance Win32_Process -Filter ("ProcessId = " + $c.OwningProcess); '
        'if ($p) { Write-Output ("" + $p.ProcessId + "`t" + $p.Name + "`t" + $p.CommandLine) } '
        "}"
    )


def parse_windows(text: str) -> PortHolder | None:
    """The holder out of what the PowerShell program printed, or None for nothing listening."""
    for line in text.splitlines():
        parts = line.rstrip("\r\n").split("\t", 2)
        if len(parts) < 2 or not parts[0].strip().isdigit():
            continue
        return PortHolder(
            pid=int(parts[0].strip()),
            name=parts[1].strip(),
            command_line=parts[2].strip() if len(parts) == 3 else "",
        )
    return None


_SS_PROCESS = re.compile(r'users:\(\("([^"]+)",pid=(\d+)')


def parse_ss(text: str) -> tuple[str, int] | None:
    """The program name and id out of one `ss -ltnp` line, or None for nothing listening."""
    found = _SS_PROCESS.search(text)
    if found is None:
        return None
    return found.group(1), int(found.group(2))


def _command_line_of(pid: int) -> str:
    """How a process was started, read off the process filesystem. Empty when it cannot be."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return " ".join(part.decode("utf-8", "replace") for part in raw.split(b"\0") if part)


async def holder_of(port: int) -> PortHolder | None:
    """The process listening on this port, or None when nothing is or it cannot be told.

    None is the honest answer for both, and the caller treats it the same way: a port that is
    taken by something this cannot name is reported as taken by something it could not name.
    """
    if not 0 < port < 65536:
        raise ValueError(f"{port} is not a port")
    try:
        if _ON_WINDOWS:
            result = await run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    _windows_query(port),
                ],
                time_limit=_LOOKUP_TIMEOUT,
            )
            return parse_windows(result.stdout.decode("utf-8", "replace"))
        result = await run(["ss", "-Hltnp", f"sport = :{port}"], time_limit=_LOOKUP_TIMEOUT)
        found = parse_ss(result.stdout.decode("utf-8", "replace"))
        if found is None:
            return None
        name, pid = found
        return PortHolder(
            pid=pid, name=name, command_line=await asyncio.to_thread(_command_line_of, pid)
        )
    except (SubprocessError, OSError) as error:
        log.info("ports.holder_unknown", port=port, detail=str(error))
        return None


async def end_process(pid: int) -> bool:
    """End a process by its id. True when the request was accepted; the port frees a moment later.

    Only ever called on a process `holder_of` named and the caller PROVED is Sift's own: by a
    token on its command line that no other process carries, never by the port it holds.
    """
    if pid <= 0:
        return False
    try:
        if _ON_WINDOWS:
            result = await run(["taskkill.exe", "/PID", str(pid), "/F"], time_limit=_LOOKUP_TIMEOUT)
            return result.returncode == 0
        os.kill(pid, signal.SIGTERM)
        return True
    except (SubprocessError, OSError) as error:
        log.warning("ports.end_failed", pid=pid, detail=str(error))
        return False
