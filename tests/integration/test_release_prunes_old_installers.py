# SPDX-License-Identifier: AGPL-3.0-or-later
"""The release folder keeps the newest few installers and lets the rest go.

What is tested is which files are chosen: versions ordered numerically (0.1.10 after 0.1.9), and
each installer taken with its hash and signature. Every case runs in a temp directory.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.integration

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release.py"


def _load() -> ModuleType:
    """The script, imported by path and registered in `sys.modules` first."""
    spec = importlib.util.spec_from_file_location("sift_release_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load()


def _make(
    folder: Path,
    version: str,
    *,
    sidecars: tuple[str, ...] = (".sha256", ".sha256.minisig", ".blockmap"),
) -> list[Path]:
    """One release's files, with a byte in each so a size can be added up."""
    installer = folder / f"Sift-{version}-x64-setup.exe"
    written = [installer]
    installer.write_bytes(b"x" * 10)
    for suffix in sidecars:
        one = folder / f"{installer.name}{suffix}"
        one.write_bytes(b"x")
        written.append(one)
    return written


def _names(folder: Path) -> set[str]:
    return {one.name for one in folder.iterdir()}


# --- which releases go -----------------------------------------------------------------------


def test_the_newest_three_stay_and_the_rest_go(tmp_path: Path) -> None:
    for version in ("0.1.1", "0.1.2", "0.1.3", "0.1.4", "0.1.5"):
        _make(tmp_path, version)

    release.prune_old_releases(tmp_path, keep=3, never="0.1.5")

    kept = _names(tmp_path)
    assert {name for name in kept if name.startswith("Sift-0.1.3")}
    assert {name for name in kept if name.startswith("Sift-0.1.4")}
    assert {name for name in kept if name.startswith("Sift-0.1.5")}
    assert not [name for name in kept if name.startswith(("Sift-0.1.1-", "Sift-0.1.2-"))]


def test_a_two_digit_patch_is_newer_than_a_one_digit_one(tmp_path: Path) -> None:
    """0.1.10 is newer than 0.1.9: a prune by name would delete the newest."""
    for version in ("0.1.8", "0.1.9", "0.1.10", "0.1.11", "0.1.147"):
        _make(tmp_path, version)

    release.prune_old_releases(tmp_path, keep=3, never="0.1.147")

    kept = _names(tmp_path)
    assert "Sift-0.1.147-x64-setup.exe" in kept
    assert "Sift-0.1.11-x64-setup.exe" in kept
    assert "Sift-0.1.10-x64-setup.exe" in kept
    assert "Sift-0.1.9-x64-setup.exe" not in kept
    assert "Sift-0.1.8-x64-setup.exe" not in kept


def test_a_release_goes_whole_or_not_at_all(tmp_path: Path) -> None:
    """A hash with no installer is worse than neither: it is the thing somebody checks."""
    for version in ("0.1.1", "0.1.2", "0.1.3", "0.1.4"):
        _make(tmp_path, version)

    release.prune_old_releases(tmp_path, keep=3, never="0.1.4")

    assert not [name for name in _names(tmp_path) if name.startswith("Sift-0.1.1-")]


def test_the_version_just_built_is_never_pruned(tmp_path: Path) -> None:
    """Rebuilding an older tag must not delete what the run just made."""
    for version in ("0.1.2", "0.1.3", "0.1.4", "0.1.5"):
        _make(tmp_path, version)

    release.prune_old_releases(tmp_path, keep=3, never="0.1.2")

    assert "Sift-0.1.2-x64-setup.exe" in _names(tmp_path)


# --- what is not ours ------------------------------------------------------------------------


def test_the_unpacked_tree_and_anything_else_is_left_alone(tmp_path: Path) -> None:
    unpacked = tmp_path / "win-unpacked"
    (unpacked / "resources").mkdir(parents=True)
    (unpacked / "Sift.exe").write_bytes(b"x")
    (tmp_path / "builder-debug.yml").write_text("notes", encoding="utf-8")
    (tmp_path / "Sift-notes.txt").write_text("somebody's own file", encoding="utf-8")
    for version in ("0.1.1", "0.1.2", "0.1.3", "0.1.4"):
        _make(tmp_path, version)

    release.prune_old_releases(tmp_path, keep=3, never="0.1.4")

    assert (unpacked / "Sift.exe").is_file()
    assert (tmp_path / "builder-debug.yml").is_file()
    assert (tmp_path / "Sift-notes.txt").is_file()


def test_a_folder_that_is_not_there_is_not_a_failure(tmp_path: Path) -> None:
    assert release.prune_old_releases(tmp_path / "nothing-here", keep=3, never="0.1.1") == 0


def test_fewer_than_the_limit_removes_nothing(tmp_path: Path) -> None:
    for version in ("0.1.1", "0.1.2"):
        _make(tmp_path, version)

    assert release.prune_old_releases(tmp_path, keep=3, never="0.1.2") == 0
    assert len(_names(tmp_path)) == 8


# --- what it reports -------------------------------------------------------------------------


def test_it_answers_with_the_bytes_it_freed(tmp_path: Path) -> None:
    for version in ("0.1.1", "0.1.2", "0.1.3", "0.1.4"):
        _make(tmp_path, version)

    # One release: a 10-byte installer and three 1-byte sidecars.
    assert release.prune_old_releases(tmp_path, keep=3, never="0.1.4") == 13


# --- the ordering itself ---------------------------------------------------------------------


def test_a_prerelease_is_older_than_the_version_it_qualifies() -> None:
    assert release._version_key("0.1.10-rc1") < release._version_key("0.1.10")
    assert release._version_key("0.1.9") < release._version_key("0.1.10")
    assert release._version_key("0.2.0") > release._version_key("0.1.147")


# --- the packer's leftovers ------------------------------------------------------------------


def test_the_packers_intermediate_goes_even_when_no_release_does(tmp_path: Path) -> None:
    """The packer's leftover `.nsis.7z` goes even when no release does."""
    for version in ("0.1.1", "0.1.2"):
        _make(tmp_path, version)
    leftover = tmp_path / "sift-desktop-0.1.105-x64.nsis.7z"
    leftover.write_bytes(b"x" * 20)

    freed = release.prune_old_releases(tmp_path, keep=3, never="0.1.2")

    assert not leftover.exists(), "the packer's intermediate survived a prune written for it"
    assert freed == 20, "the bytes it freed have to include the leftover"
    assert len(_names(tmp_path)) == 8, "a release went with it"


def test_the_intermediate_of_the_version_just_built_stays(tmp_path: Path) -> None:
    """The intermediate of the version just built stays."""
    mine = tmp_path / "sift-desktop-0.1.9-x64.nsis.7z"
    mine.write_bytes(b"x" * 20)
    older = tmp_path / "sift-desktop-0.1.8-x64.nsis.7z"
    older.write_bytes(b"x" * 20)

    release.prune_old_releases(tmp_path, keep=3, never="0.1.9")

    assert mine.is_file()
    assert not older.exists()


def test_a_seven_zip_that_is_not_the_packers_is_left_alone(tmp_path: Path) -> None:
    """The pattern is anchored and version-shaped for the same reason `OURS` is: this runs over a
    folder that has somebody's own files in it."""
    theirs = tmp_path / "sift-desktop-notes.nsis.7z"
    theirs.write_bytes(b"x")
    also = tmp_path / "backup.7z"
    also.write_bytes(b"x")

    release.prune_old_releases(tmp_path, keep=3, never="0.1.9")

    assert theirs.is_file() and also.is_file()
