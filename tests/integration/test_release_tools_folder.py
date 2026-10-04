# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a release looks for a tool that is not on PATH: the folder RELEASE_TOOLS_DIR names, and
a plain refusal when the tool is in neither place."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.integration

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release.py"


def _load() -> ModuleType:
    """The script, imported by path and fresh, so the folder is read as it is set now."""
    spec = importlib.util.spec_from_file_location("sift_release_tools_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_a_tool_is_found_in_the_folder_the_environment_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RELEASE_TOOLS_DIR", str(tmp_path))
    release = _load()
    monkeypatch.setattr(release.shutil, "which", lambda _name: None)
    (tmp_path / "minisign.exe").write_bytes(b"")

    assert tmp_path == release.toolchain()
    assert release.tool("minisign") == str(tmp_path / "minisign.exe")
    with pytest.raises(release.ReleaseFailed, match="needed to build a release"):
        release.tool("a-tool-nobody-has")


def test_a_signed_build_moves_the_gallery_aside_and_always_puts_it_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The client of a signed release is built without the optional gallery; an unsigned build
    keeps it; a build that fails, or one that stopped half-way before, leaves it in place."""
    release = _load()
    gallery, aside, client = tmp_path / "routes" / "design", tmp_path / "aside", tmp_path / "web"
    for name, value in (("GALLERY", gallery), ("GALLERY_ASIDE", aside), ("CLIENT", client)):
        monkeypatch.setattr(release, name, value)
    gallery.mkdir(parents=True)
    (gallery / "+page.svelte").write_text("gallery", encoding="utf-8")
    client.mkdir()
    (client / "index.html").write_text("built", encoding="utf-8")
    seen: list[bool] = []
    monkeypatch.setattr(release, "run", lambda *_a, **_k: seen.append(gallery.exists()))

    release.build_client(with_gallery=False)
    release.build_client(with_gallery=True)
    assert seen == [False, True]
    assert (gallery / "+page.svelte").is_file() and not aside.exists()

    def fails(*_a: object, **_k: object) -> None:
        raise release.ReleaseFailed("the build stopped")

    monkeypatch.setattr(release, "run", fails)
    with pytest.raises(release.ReleaseFailed, match="the build stopped"):
        release.build_client(with_gallery=False)
    assert (gallery / "+page.svelte").is_file() and not aside.exists()

    gallery.rename(aside)
    monkeypatch.setattr(release, "run", lambda *_a, **_k: seen.append(gallery.exists()))
    release.build_client(with_gallery=True)
    assert seen[-1] is True and not aside.exists()


def test_a_gallery_another_program_holds_is_refused_in_a_sentence_and_nothing_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder somebody has open cannot be renamed on Windows. Said as what to do about it, with
    the client not built: a release with the gallery in it is not the release that was asked for."""
    release = _load()
    gallery = tmp_path / "routes" / "design"
    for name, value in (("GALLERY", gallery), ("GALLERY_ASIDE", tmp_path / "aside")):
        monkeypatch.setattr(release, name, value)
    gallery.mkdir(parents=True)
    built: list[object] = []
    monkeypatch.setattr(release, "run", lambda *_a, **_k: built.append(1))

    def held(self: Path, target: Path) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "rename", held)
    with pytest.raises(release.ReleaseFailed, match="is open in another program"):
        release.build_client(with_gallery=False)
    assert built == []


def test_no_folder_is_searched_that_nobody_named(monkeypatch: pytest.MonkeyPatch) -> None:
    """No machine's layout is a default: with nothing named, PATH is the only place looked."""
    monkeypatch.delenv("RELEASE_TOOLS_DIR", raising=False)
    release = _load()
    monkeypatch.setattr(release.shutil, "which", lambda _name: None)

    assert release.toolchain() is None
    with pytest.raises(release.ReleaseFailed, match="no tools folder is named"):
        release.tool("minisign")
