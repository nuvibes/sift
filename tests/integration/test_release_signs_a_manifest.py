# SPDX-License-Identifier: AGPL-3.0-or-later
"""A signed release says which release it is.

The desktop application installs a release only when a manifest signed with Sift's key names its
version, its installer and the installer's SHA-256, and only when that version is newer than the
copy running. A signature over the installer's hash alone proves the bytes are Sift's and nothing
about which release they are, so an old installer served as new would pass it.

Every case runs against a temp directory built here, with minisign and the build steps stood in for.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "release.py"


def _load() -> ModuleType:
    """The script, imported by path, as the other release tests do."""
    spec = importlib.util.spec_from_file_location("sift_release_manifest_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load()

DIGEST = "ab" * 32


def _built(folder: Path) -> tuple[Path, Path]:
    installer = folder / f"Sift-{release.VERSION}-x64-setup.exe"
    installer.write_bytes(b"installer")
    sums = folder / f"{installer.name}.sha256"
    sums.write_text(f"{DIGEST}  {installer.name}\n", encoding="utf-8")
    return installer, sums


def test_the_manifest_names_the_version_the_installer_and_its_hash(tmp_path: Path) -> None:
    installer, sums = _built(tmp_path)

    manifest = release.write_manifest(installer, sums)

    assert manifest.name == f"{installer.name}.manifest.json"
    assert json.loads(manifest.read_text(encoding="utf-8")) == {
        "version": release.VERSION,
        "installer": installer.name,
        "sha256": DIGEST,
    }


def test_both_files_are_signed_in_one_call_in_the_form_the_application_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`-l`: the legacy form signs the bytes. Without it minisign signs a digest of them, which
    every installed copy of Sift refuses."""
    installer, sums = _built(tmp_path)
    manifest = release.write_manifest(installer, sums)
    key = tmp_path / "sift.key"
    key.write_text("stand-in", encoding="utf-8")
    calls: list[list[str]] = []

    def minisign(command: list[str], *, where: Path, quiet: bool = False) -> str:
        del where, quiet
        calls.append(command)
        for signed in command[command.index("-m") + 1 :]:
            Path(f"{signed}.minisig").write_text("sig", encoding="utf-8")
        return ""

    monkeypatch.setattr(release, "run", minisign)
    monkeypatch.setattr(release, "tool", lambda _name: "minisign")
    monkeypatch.setattr(release, "MINISIGN_KEY", key)

    signatures = release.sign(sums, manifest)

    assert len(calls) == 1
    assert calls[0][:3] == ["minisign", "-S", "-l"]
    assert calls[0][-3:] == ["-m", str(sums), str(manifest)]
    assert [one.name for one in signatures] == [f"{sums.name}.minisig", f"{manifest.name}.minisig"]


def test_a_signature_that_did_not_appear_stops_the_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer, sums = _built(tmp_path)
    manifest = release.write_manifest(installer, sums)
    key = tmp_path / "sift.key"
    key.write_text("stand-in", encoding="utf-8")
    monkeypatch.setattr(release, "run", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(release, "tool", lambda _name: "minisign")
    monkeypatch.setattr(release, "MINISIGN_KEY", key)

    with pytest.raises(release.ReleaseFailed):
        release.sign(sums, manifest)


@pytest.mark.parametrize("unsigned", [True, False])
def test_an_unsigned_build_carries_no_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsigned: bool
) -> None:
    """`--no-sign` still builds and hashes. It writes no manifest: an unsigned one would describe
    a release nothing can verify."""
    if sys.platform != "win32":
        pytest.skip("the release script refuses to build anywhere but Windows")
    installer, _sums = _built(tmp_path)
    handed: list[list[str]] = []
    signed: list[tuple[Path, Path]] = []

    for step in ("build_client", "build_runtime", "rebuild_addon", "build_shell"):
        monkeypatch.setattr(release, step, lambda **_given: None)
    # The upgrade fixture is made by the runtime the step above did not build, and written into the
    # tree; it has a test of its own. Left in, this case would need a runtime from an earlier build.
    monkeypatch.setattr(release, "write_upgrade_fixture", lambda: None)
    monkeypatch.setattr(release, "_check_the_vendored_tools_are_in", lambda *_args: None)
    # The changelog has a gate of its own.
    monkeypatch.setattr(release, "check_the_changelog", lambda **_kwargs: "")
    # So has the refusal of a tree that is not a pushed commit, which a signed build meets first;
    # left in, this case would answer for whatever the working tree holds today.
    monkeypatch.setattr(release, "check_the_tree", lambda **_kwargs: None)
    monkeypatch.setattr(release, "check_the_notices", lambda: None)
    monkeypatch.setattr(release, "VENDOR", tmp_path / "vendor")
    monkeypatch.setattr(release, "pack", lambda: installer)
    monkeypatch.setattr(release, "prune_old_releases", lambda: 0)
    monkeypatch.setattr(
        release, "put_on_the_desktop", lambda files: handed.append([one.name for one in files])
    )

    def sign(sums: Path, manifest: Path) -> list[Path]:
        signed.append((sums, manifest))
        return [Path(f"{sums}.minisig"), Path(f"{manifest}.minisig")]

    monkeypatch.setattr(release, "sign", sign)

    argv = ["--skip-vendor", *(["--no-sign"] if unsigned else [])]
    assert release.main(argv) == 0
    assert (tmp_path / "vendor" / "THIRD-PARTY-NOTICES.txt").is_file()

    manifest = tmp_path / f"{installer.name}.manifest.json"
    if unsigned:
        assert not manifest.exists()
        assert signed == []
        assert handed == [[installer.name, f"{installer.name}.sha256"]]
    else:
        assert manifest.is_file()
        assert signed == [(tmp_path / f"{installer.name}.sha256", manifest)]
        assert handed == [
            [
                installer.name,
                f"{installer.name}.sha256",
                manifest.name,
                f"{installer.name}.sha256.minisig",
                f"{manifest.name}.minisig",
            ]
        ]


@pytest.mark.parametrize(
    "name",
    [
        "Sift-0.2.0-x64-setup.exe.manifest.json",
        "Sift-0.2.0-x64-setup.exe.manifest.json.minisig",
        "Sift-0.2.0-x64-setup.exe.sha256.minisig",
    ],
)
def test_the_manifest_is_one_of_the_release_files(name: str) -> None:
    """Pruned with its release and replaced on the desktop, like the hash beside it."""
    assert release.OURS.match(name)
    assert release.ARTEFACT.match(name)


@pytest.mark.parametrize(
    "name", ["Sift-0.2.0-x64-setup.exe.minisig", "Sift-0.2.0-x64-setup.exe.manifest.json.bak"]
)
def test_nothing_else_is_taken_for_one(name: str) -> None:
    assert not release.OURS.match(name)
    assert not release.ARTEFACT.match(name)


def test_the_packer_is_told_never_to_publish() -> None:
    """The packer publishes on a tag by default. Sift's releases are built here and handed over by
    whoever built them, so every pack says `--publish never`."""
    package = json.loads((_ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))
    pack = package["scripts"]["pack"]

    assert "--publish never" in pack


def test_a_wrong_password_is_asked_again_and_the_third_stops_the_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer, sums = _built(tmp_path)
    manifest = release.write_manifest(installer, sums)
    key = tmp_path / "sift.key"
    key.write_text("stand-in", encoding="utf-8")
    asked: list[int] = []

    def minisign(command: list[str], *, where: Path, quiet: bool = False) -> str:
        del where, quiet
        asked.append(1)
        if len(asked) < 3:
            raise release.ReleaseFailed("Wrong password for that key")
        for signed in command[command.index("-m") + 1 :]:
            Path(f"{signed}.minisig").write_text("sig", encoding="utf-8")
        return ""

    monkeypatch.setattr(release, "run", minisign)
    monkeypatch.setattr(release, "tool", lambda _name: "minisign")
    monkeypatch.setattr(release, "MINISIGN_KEY", key)

    assert len(release.sign(sums, manifest)) == 2
    assert len(asked) == 3

    asked.clear()

    def refused(command: list[str], *, where: Path, quiet: bool = False) -> str:
        del command, where, quiet
        asked.append(1)
        raise release.ReleaseFailed("Wrong password for that key")

    monkeypatch.setattr(release, "run", refused)
    with pytest.raises(release.ReleaseFailed):
        release.sign(sums, manifest)
    assert len(asked) == 3


def test_no_build_signs_a_release_an_earlier_run_built_and_left_unsigned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installer, sums = _built(tmp_path)
    handed: list[list[str]] = []
    signed: list[Path] = []

    def sign(given: Path, manifest: Path) -> list[Path]:
        signed.append(manifest)
        for one in (given, manifest):
            Path(f"{one}.minisig").write_text("sig", encoding="utf-8")
        return [Path(f"{given}.minisig"), Path(f"{manifest}.minisig")]

    monkeypatch.setattr(release, "sign", sign)
    monkeypatch.setattr(
        release, "put_on_the_desktop", lambda files: handed.append([one.name for one in files])
    )

    release.sign_what_is_built(tmp_path, signing=False)
    assert signed == [], "a dry run signs nothing"
    release.sign_what_is_built(tmp_path)
    release.sign_what_is_built(tmp_path)

    assert signed == [tmp_path / f"{installer.name}.manifest.json"], "signed once, never twice"
    assert handed[0][:3] == [installer.name, sums.name, f"{installer.name}.manifest.json"]
