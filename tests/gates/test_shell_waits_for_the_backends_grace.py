# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shell's stop timeout is at least the backend's own grace for a running job.

The shell asks the backend to stop and takes it after a timeout. The backend, asked, waits up to
its own grace for a running job to finish before it lets go. A shell timeout shorter than that
grace takes the process in the middle of the very wait that lets a job finish, so every quit
during a long job is a hard stop dressed as a polite one. The two numbers live in two languages;
this reads the shell's and holds it above the backend's.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sift.kernel.jobs.tuning import SHUTDOWN_GRACE_SECONDS

pytestmark = [pytest.mark.gate, pytest.mark.unit]

BACKEND_TS = Path(__file__).resolve().parents[2] / "desktop" / "src" / "backend.ts"

#: Room for the database to close after the pool has let go.
CLOSE_MARGIN_MS = 5_000

_TIMEOUT = re.compile(r"^const STOP_TIMEOUT_MS = ([0-9_]+);", re.M)


def stop_timeout_ms(source: str) -> int:
    found = _TIMEOUT.search(source)
    assert found is not None, "backend.ts no longer declares STOP_TIMEOUT_MS where this reads it"
    return int(found.group(1).replace("_", ""))


def test_the_shell_waits_at_least_the_backends_grace() -> None:
    timeout = stop_timeout_ms(BACKEND_TS.read_text(encoding="utf-8"))
    floor = int(SHUTDOWN_GRACE_SECONDS * 1000) + CLOSE_MARGIN_MS
    assert timeout >= floor, f"the shell takes the backend after {timeout} ms; it needs {floor}"


def test_the_reading_sees_a_planted_number() -> None:
    assert stop_timeout_ms("x\nconst STOP_TIMEOUT_MS = 5_000;\ny") == 5000
