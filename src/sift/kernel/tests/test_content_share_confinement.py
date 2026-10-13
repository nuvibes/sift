# SPDX-License-Identifier: AGPL-3.0-or-later
"""A location on a share is confined without resolving it: the root once, then one look at the
file; a local root is resolved as before."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sift.kernel.content import identity_paths
from sift.kernel.content.identity_paths import _confine_to_root
from sift.kernel.content.mounts import Storage
from sift.testing.tools import POSIX_ONLY, WINDOWS_ONLY, junction


@pytest.fixture(autouse=True)
def _roots_forgotten() -> None:
    identity_paths._resolved_root.cache_clear()


def _on_a_share(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(
        identity_paths, "storage_of", lambda _path: Storage(key="\\\\nas\\x\\", remote=True)
    )


def test_a_file_on_a_share_is_not_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    (root / "a" / "clip.mp4").write_bytes(b"x")
    _on_a_share(monkeypatch, root)
    resolves: list[Path] = []
    real = Path.resolve

    def counted(self: Path, strict: bool = False) -> Path:
        resolves.append(self)
        return real(self, strict)

    monkeypatch.setattr(Path, "resolve", counted)
    first = _confine_to_root(root / "a" / "clip.mp4", root, "loc")
    second = _confine_to_root(root / "a" / "clip.mp4", root, "loc")
    assert first == second == root.resolve() / "a" / "clip.mp4"
    # The root once, for both; never the file.
    assert [one for one in resolves if one != root] == [] and len(resolves) <= 1 + 1


def test_a_file_not_there_on_a_share_is_the_path_it_would_have(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    _on_a_share(monkeypatch, root)
    assert _confine_to_root(root / "gone.mp4", root, "loc") == root.resolve() / "gone.mp4"


@WINDOWS_ONLY
def test_a_junction_on_a_share_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    junction(root / "jlink", outside)
    _on_a_share(monkeypatch, root)
    with pytest.raises(ValueError, match="outside its library root"):
        _confine_to_root(root / "jlink", root, "loc")


@POSIX_ONLY
def test_a_symlink_on_a_share_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret").write_text("s")
    os.symlink(tmp_path / "secret", root / "link")
    _on_a_share(monkeypatch, root)
    with pytest.raises(ValueError, match="outside its library root"):
        _confine_to_root(root / "link", root, "loc")


@WINDOWS_ONLY
def test_a_local_root_is_still_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A junction a folder deep is caught by resolving, which a local disk answers at once."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s")
    junction(root / "jlink", outside)
    monkeypatch.setattr(
        identity_paths, "storage_of", lambda _path: Storage(key="C:\\", remote=False)
    )
    with pytest.raises(ValueError, match="outside its library root"):
        _confine_to_root(root / "jlink" / "secret.txt", root, "loc")
