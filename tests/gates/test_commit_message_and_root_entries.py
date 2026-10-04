# SPDX-License-Identifier: AGPL-3.0-or-later
"""The commit-message check and the root-entries check, each made to refuse.

`scripts/check_commit_message.sh` (the commit-msg hook) and `scripts/check_root_entries.sh` (at each
commit), run against a throwaway repository under `tmp_path`. Every refused shape is built at run
time, so this file holds none of them and no gate has to excuse it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.gates import posix_bash

pytestmark = pytest.mark.gate

REPO = Path(__file__).resolve().parents[2]

#: The commit types the hook takes, as CONTRIBUTING.md lists them.
_TYPES = ("feat", "fix", "chore", "docs", "test", "refactor", "perf", "build", "ci")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)


def _run(repo: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [posix_bash(), *argv],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _write(repo: Path, where: str, text: str) -> None:
    path = repo / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository holding the scripts, the data they read and one ordinary source file."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "test")
    _git(root, "config", "user.email", "test@users.noreply.github.com")
    (root / "scripts").mkdir()
    for script in ("check_commit_message.sh", "check_root_entries.sh"):
        shutil.copy(REPO / "scripts" / script, root / "scripts" / script)
    (root / "tests" / "gates" / "data").mkdir(parents=True)
    for data in ("narration.json", "root_entries.txt"):
        shutil.copy(REPO / "tests" / "gates" / "data" / data, root / "tests/gates/data" / data)
    _write(root, "README.md", "# A project\n")
    _write(root, "src/thing.py", "# SPDX-License-Identifier: AGPL-3.0-or-later\nVALUE = 1\n")
    _git(root, "add", "-A")
    return root


def _message(repo: Path, text: str) -> str:
    _write(repo, "msg/COMMIT_EDITMSG", text)
    return "msg/COMMIT_EDITMSG"


# --- the commit message -------------------------------------------------------------------------


def test_a_plain_message_passes(repo: Path) -> None:
    said = _message(repo, "fix(theater): shuffle keeps the current clip\n\n- one factual line\n")
    result = _run(repo, "scripts/check_commit_message.sh", said)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("message", "complaint"),
    [
        ("fix(browse): " + "x" * 60 + "\n", "72 is the most"),
        ("fix(browse): tidy, found on 20" + "26-01-02\n", "narrates"),
        ("fix(browse): tidy, measured " + "on a slow disk\n", "narrates"),
        ("fix(browse): tidy \u2192 faster\n", "outside ASCII"),
    ],
)
def test_a_message_outside_the_house_style_is_refused(
    repo: Path, message: str, complaint: str
) -> None:
    result = _run(repo, "scripts/check_commit_message.sh", _message(repo, message))
    assert result.returncode != 0, f"not refused: {message!r}"
    assert complaint in result.stderr, result.stderr


@pytest.mark.parametrize(
    "subject",
    [
        "Theater: shuffle keeps the current clip",
        "feature(theater): shuffle keeps the current clip",
        "Fix(theater): shuffle keeps the current clip",
        "fix(Theater): shuffle keeps the current clip",
        "fix(theater):shuffle keeps the current clip",
        "fix(): shuffle keeps the current clip",
        "fix theater: shuffle keeps the current clip",
    ],
)
def test_a_subject_without_a_known_type_is_refused(repo: Path, subject: str) -> None:
    result = _run(repo, "scripts/check_commit_message.sh", _message(repo, subject + "\n"))
    assert result.returncode != 0, f"not refused: {subject!r}"
    assert "the subject is not type(area): the change" in result.stderr, result.stderr


@pytest.mark.parametrize(
    "subject",
    [
        *(f"{kind}(theater): shuffle keeps the current clip" for kind in _TYPES),
        "fix(download-manager): a paused row keeps its place",
        "build: Sift 0.1.216",
    ],
)
def test_every_type_on_the_list_passes_with_or_without_an_area(repo: Path, subject: str) -> None:
    result = _run(repo, "scripts/check_commit_message.sh", _message(repo, subject + "\n"))
    assert result.returncode == 0, result.stderr


def test_the_hook_and_contributing_name_the_same_types() -> None:
    hook = (REPO / "scripts" / "check_commit_message.sh").read_text("utf-8")
    declared = re.search(r"^TYPES='([a-z ]+)'$", hook, re.M)
    assert declared is not None, "the hook no longer declares TYPES on one line"
    assert tuple(declared.group(1).split()) == _TYPES
    guide = (REPO / "CONTRIBUTING.md").read_text("utf-8")
    listed = guide[guide.index("fixed list of types:") : guide.index("The area is")]
    assert tuple(re.findall(r"`([a-z]+)`", listed)) == _TYPES


def test_what_git_drops_from_a_message_is_not_read(repo: Path) -> None:
    said = _message(
        repo,
        "fix(browse): tidy\n# a comment on 20" + "26-01-02\n"
        "# ------------------------ >8 ------------------------\n"
        "a diff line on 20" + "26-01-02\n",
    )
    result = _run(repo, "scripts/check_commit_message.sh", said)
    assert result.returncode == 0, result.stderr


# --- the root -----------------------------------------------------------------------------------


def test_a_root_of_declared_entries_passes(repo: Path) -> None:
    result = _run(repo, "scripts/check_root_entries.sh")
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize("scratch", ["msg.txt", "run.bat", "notes.tmp"])
def test_a_scratch_file_at_the_root_is_refused(repo: Path, scratch: str) -> None:
    _write(repo, scratch, "x\n")
    _git(repo, "add", "-A")
    result = _run(repo, "scripts/check_root_entries.sh")
    assert result.returncode != 0
    assert scratch in result.stdout
