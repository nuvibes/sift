# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who is listening on a port here, and ending a process Sift itself left behind.

Ownership is proved by a token on the command line by the caller, never by the port."""

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

#: A loaded machine can take several seconds to start PowerShell.
_LOOKUP_TIMEOUT = 15.0

#: Decided at import, so the checker reads both platforms' halves.
_ON_WINDOWS = sys.platform.startswith("win")


@dataclass(frozen=True, slots=True)
class PortHolder:
    """A process listening on a port: its id, program name, and command line."""

    pid: int
    name: str
    command_line: str


def _windows_query(port: int) -> str:
    """The PowerShell program for one port, composed only from a checked integer; tab-separated."""
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
    """How a process was started, from the process filesystem; empty when it cannot be read."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return " ".join(part.decode("utf-8", "replace") for part in raw.split(b"\0") if part)


async def holder_of(port: int) -> PortHolder | None:
    """The process listening on this port, or None when nothing is or it cannot be told."""
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
    """End a process the caller proved is Sift's own by its token; True when accepted."""
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
