# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two tables a screen watches by the second are not written to in silence.

The work queue and the download ledger are drawn live and told of changes rather than polled, so a
write that says nothing leaves the screen showing the answer from before it indefinitely, with no
symptom. Each has exactly one way to write, and this refuses a second.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

SOURCE = Path(__file__).resolve().parents[2] / "src" / "sift"

#: The file, the way in, and how many writes may go around it. The job queue's one is its heartbeat,
#: which changes nothing on screen; both `execute` and `write()` are counted. The ledger's two are
#: the wrapper itself and the row of fetched gallery pieces, which no screen draws.
WATCHED = (
    ("kernel/jobs/queue*.py", re.compile(r"self\._db\.(?:write\(\)|execute\()"), 1),
    ("slices/download/service*.py", re.compile(r"self\._db\.execute\("), 2),
)


def read_all(path: str) -> str:
    """Every file the entry names: one path, or a pattern for a module split across files."""
    return "".join(found.read_text(encoding="utf-8") for found in sorted(SOURCE.glob(path)))


def ways_around(path: str, pattern: re.Pattern[str]) -> int:
    return len(pattern.findall(read_all(path)))


@pytest.mark.parametrize(("path", "pattern", "allowed"), WATCHED)
def test_only_the_declared_writes_go_around_the_one_that_says_so(
    path: str, pattern: re.Pattern[str], allowed: int
) -> None:
    found = ways_around(path, pattern)

    assert found == allowed, (
        f"\n{path} has {found} writes that go straight to the database, and {allowed} is what is\n"
        "declared. A screen that is drawn live shows the answer from before an unannounced write\n"
        "for as long as it stays open, and nothing about it looks wrong. Use the wrapper this file\n"
        "already has, or (if this write really changes nothing anybody is looking at) say so\n"
        "here and in a comment beside it.\n"
    )


@pytest.mark.parametrize(("path", "pattern", "allowed"), WATCHED)
def test_the_way_that_says_so_is_the_one_being_used(
    path: str, pattern: re.Pattern[str], allowed: int
) -> None:
    """Writes do go through the wrapper, or a file that stopped writing would satisfy the count."""
    source = read_all(path)
    telling = (
        source.count("telling(") + source.count("self._say(") + source.count("self._writing()")
    )

    assert telling > allowed, f"{path} barely uses the wrapper; the rule above is guarding nothing"
