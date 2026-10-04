# SPDX-License-Identifier: AGPL-3.0-or-later
"""A tool is launched from a thread, never on the event loop.

The loop's own subprocess support creates the process synchronously on the loop, which on Windows
holds it per launch, and a probe is dozens of launches per file. `kernel.subprocess.run` launches on
a thread; the one place allowed is the streaming runner, which needs the loop's own pipes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SRC = Path(__file__).resolve().parents[2] / "src" / "sift"

#: Where an on-loop launch is allowed, with the reason. Every other file is refused one.
ALLOWED = {
    "kernel/subprocess.py": "the streaming runner a person waits on, one tool at a time",
}

ON_LOOP_LAUNCH = re.compile(r"create_subprocess_(exec|shell)\(")


def _launches() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in SRC.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        count = sum(
            1
            for line in text.splitlines()
            if ON_LOOP_LAUNCH.search(line) and not line.lstrip().startswith("#")
        )
        if count:
            found[path.relative_to(SRC).as_posix()] = count
    return found


def test_no_new_on_loop_launch() -> None:
    launches = _launches()
    strays = sorted(set(launches) - set(ALLOWED))
    assert not strays, (
        "these files launch a tool on the event loop, which on Windows holds every request for "
        "the length of the operating system's process creation. Launch through "
        "`kernel.subprocess.run` or `capture` instead:\n  " + "\n  ".join(strays)
    )


def test_the_allowed_launches_are_still_there() -> None:
    """An allowlist naming a file that no longer launches anything is a gate watching nothing."""
    launches = _launches()
    gone = sorted(one for one in ALLOWED if one not in launches)
    assert not gone, f"allowed but no longer launching, so take them off the list: {gone}"
    # The streaming runner is the one launch left in the kernel's launcher.
    assert launches["kernel/subprocess.py"] == 1
