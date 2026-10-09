# SPDX-License-Identifier: AGPL-3.0-or-later
"""A signed or published release is built from a pushed commit and nothing else.

Read against a real repository made for the test, with a bare one standing in for the remote, so
the two questions are asked of git exactly as a release asks them: a change nobody committed, and
a HEAD that is not on `origin/main`. A build for this device only warns.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.integration

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sift_release_tree_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(where: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(where), "-c", "user.name=Test", "-c", "user.email=nobody", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def pushed(tmp_path: Path) -> Path:
    """A clone whose HEAD is exactly what its origin's main holds, and nothing changed."""
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "init", "-b", "main", str(work))
    (work / "a.txt").write_text("one\n", encoding="utf-8")
    _git(work, "add", "a.txt")
    _git(work, "commit", "-m", "one")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-u", "origin", "main")
    return work


def test_a_pushed_clean_commit_is_let_through(pushed: Path) -> None:
    _load().check_the_tree(signed=True, repo=pushed)


def test_a_change_nobody_committed_is_refused(pushed: Path) -> None:
    release = _load()
    (pushed / "a.txt").write_text("two\n", encoding="utf-8")
    with pytest.raises(release.ReleaseFailed, match="changes nobody committed"):
        release.check_the_tree(signed=True, repo=pushed)
    (pushed / "a.txt").write_text("one\n", encoding="utf-8")
    (pushed / "new.txt").write_text("new\n", encoding="utf-8")
    with pytest.raises(release.ReleaseFailed, match=r"new\.txt"):
        release.check_the_tree(signed=True, repo=pushed)


def test_a_commit_nobody_pushed_is_refused(pushed: Path) -> None:
    release = _load()
    (pushed / "a.txt").write_text("two\n", encoding="utf-8")
    _git(pushed, "commit", "-am", "two")
    with pytest.raises(release.ReleaseFailed, match="not on origin/main"):
        release.check_the_tree(signed=True, repo=pushed)


def test_a_clone_with_no_remote_is_refused(tmp_path: Path) -> None:
    release = _load()
    _git(tmp_path, "init", "-b", "main", str(tmp_path / "alone"))
    (tmp_path / "alone" / "a.txt").write_text("one\n", encoding="utf-8")
    _git(tmp_path / "alone", "add", "a.txt")
    _git(tmp_path / "alone", "commit", "-m", "one")
    with pytest.raises(release.ReleaseFailed, match="no origin/main"):
        release.check_the_tree(signed=True, repo=tmp_path / "alone")


def test_a_build_for_this_device_only_warns(
    pushed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (pushed / "a.txt").write_text("two\n", encoding="utf-8")
    _load().check_the_tree(signed=False, repo=pushed)
    assert "NOT A RELEASE ANYBODY ELSE SHOULD INSTALL" in capsys.readouterr().out


def test_the_upgrade_fixture_the_build_writes_is_not_a_change_nobody_committed(
    pushed: Path,
) -> None:
    """A build replaces the last release's fixture with this one's; a run after it, `--no-build`
    or a second try at the signature, meets that tree and must not refuse it."""
    release = _load()
    data = pushed / "tests" / "integration" / "data"
    data.mkdir(parents=True)
    (data / "library-0.0.1.sql.gz").write_bytes(b"old")
    _git(pushed, "add", ".")
    _git(pushed, "commit", "-m", "fixture")
    _git(pushed, "push", "origin", "main")
    (data / "library-0.0.1.sql.gz").unlink()
    (data / f"library-{release.VERSION}.sql.gz").write_bytes(b"new")
    release.check_the_tree(signed=True, repo=pushed)

    (data / "library-9.9.9.sql.gz").write_bytes(b"not this build's")
    with pytest.raises(release.ReleaseFailed, match=r"library-9\.9\.9"):
        release.check_the_tree(signed=True, repo=pushed)
