# SPDX-License-Identifier: AGPL-3.0-or-later
"""The repository root holds only the entries `data/root_entries.txt` declares.

The root is where a scratch file lands (a commit message saved to a file, a batch file, a
temporary note), and one `git add` of the whole tree takes it in. It is also the first thing a
reader of the repository sees. So the root is a closed list: a new top-level entry is added here
on purpose, with the change that needs it. `scripts/check_root_entries.sh` reads the same list at
every commit.

"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
ROOT_ENTRIES = Path(__file__).resolve().parent / "data" / "root_entries.txt"


def declared() -> set[str]:
    """The list, less comments and blank lines."""
    lines = ROOT_ENTRIES.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def top_level(paths: list[str]) -> set[str]:
    """The first segment of each tracked path: a file at the root, or the folder it sits in."""
    return {path.split("/", 1)[0] for path in paths if path}


def _tracked() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return listed.stdout.split("\0")


def test_the_root_holds_only_what_is_declared() -> None:
    extra = top_level(_tracked()) - declared()
    assert not extra, (
        f"tracked at the repository root and not declared: {sorted(extra)}. A scratch file does "
        "not belong in the repository; a real new entry is a line in tests/gates/data/"
        "root_entries.txt."
    )


def test_nothing_declared_is_missing() -> None:
    """A stale line reads as permission for something that is not there."""
    gone = declared() - top_level(_tracked())
    assert not gone, f"declared in root_entries.txt and not tracked: {sorted(gone)}"


def test_a_scratch_file_at_the_root_is_refused() -> None:
    """The comparison itself, against the shapes that have to be refused."""
    planted = ["msg.txt", "run.bat", "notes.tmp", "src/sift/main.py", "README.md"]
    assert top_level(planted) - declared() == {"msg.txt", "run.bat", "notes.tmp"}
