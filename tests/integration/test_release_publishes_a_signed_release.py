# SPDX-License-Identifier: AGPL-3.0-or-later
"""`release.py --publish`: only a signed release the changelog describes, never replacing a file.

The GitHub CLI is stood in for by a function that answers the reads and records every call, and
the release is signed here with a key made for the test, in the form the shell verifies.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

pytestmark = pytest.mark.integration

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sift_release_publish_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load()

REPOSITORY = "example-owner/sift"
TAG = f"v{release.VERSION}"
KEY_ID = b"\x01\x02\x03\x04\x05\x06\x07\x08"


class Signer:
    """A minisign key made for the test: its public half in the shell's form, and a signer."""

    def __init__(self) -> None:
        self.private = Ed25519PrivateKey.generate()
        raw = self.private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.public = base64.b64encode(b"Ed" + KEY_ID + raw).decode("ascii")

    def signature(self, content: bytes, algorithm: bytes = b"Ed") -> str:
        signed = base64.b64encode(algorithm + KEY_ID + self.private.sign(content)).decode("ascii")
        return f"untrusted comment: test\n{signed}\ntrusted comment: test\n{'A' * 88}\n"


@pytest.fixture
def signer(monkeypatch: pytest.MonkeyPatch) -> Signer:
    made = Signer()
    declared = {
        "PUBLIC_KEY_BASE64": made.public,
        "DEFAULT_FEED_URL": f"https://api.github.com/repos/{REPOSITORY}/releases/latest",
    }
    monkeypatch.setattr(release, "_declared_in_the_shell", lambda name: declared[name])
    return made


def _built(folder: Path, signer: Signer) -> list[Path]:
    """A signed release in `folder`, as `release.py` leaves one."""
    installer = folder / f"Sift-{release.VERSION}-x64-setup.exe"
    installer.write_bytes(b"installer bytes")
    digest = hashlib.sha256(b"installer bytes").hexdigest()
    sums = folder / f"{installer.name}.sha256"
    sums.write_text(f"{digest}  {installer.name}\n", encoding="utf-8", newline="\n")
    manifest = folder / f"{installer.name}.manifest.json"
    body = {"version": release.VERSION, "installer": installer.name, "sha256": digest}
    manifest.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8", newline="\n")
    for signed in (sums, manifest):
        Path(f"{signed}.minisig").write_text(signer.signature(signed.read_bytes()), "utf-8")
    (folder / f"{installer.name}.blockmap").write_bytes(b"blockmap")
    return sorted(folder.iterdir())


def _changelog(folder: Path) -> Path:
    changelog = folder / "CHANGELOG.md"
    changelog.write_text(
        f"# Changelog\n\n## Unreleased\n\n## {release.VERSION} - 2026-01-02\n\n"
        "### What changed for you\n\n- **Theater** keeps its place\n",
        encoding="utf-8",
    )
    return changelog


class FakeGh:
    """Stands in for the GitHub CLI: answers `api` reads from `documents`, records every call."""

    def __init__(self, documents: dict[str, dict[str, Any] | None]) -> None:
        self.documents = documents
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, args: list[str], stdin: str | None) -> Any:
        self.calls.append((args, stdin))
        if args[0] == "api":
            found = self.documents.get(args[1])
            if found is None:
                return release.GhRun(1, "", "gh: Not Found (HTTP 404)")
            return release.GhRun(0, json.dumps(found), "")
        return release.GhRun(0, "", "")

    def writes(self) -> list[list[str]]:
        return [args for args, _stdin in self.calls if args[0] != "api"]


def _github(release_document: dict[str, Any] | None, *, tagged: bool = True) -> FakeGh:
    return FakeGh(
        {
            f"repos/{REPOSITORY}/git/ref/tags/{TAG}": {"ref": f"refs/tags/{TAG}"}
            if tagged
            else None,
            f"repos/{REPOSITORY}/releases/tags/{TAG}": release_document,
        }
    )


def _asset(path: Path, digest: str | None = None) -> dict[str, Any]:
    recorded = digest or "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return {"name": path.name, "digest": recorded}


def test_the_dry_run_plans_a_new_release_and_sends_nothing(
    tmp_path: Path, signer: Signer, capsys: pytest.CaptureFixture[str]
) -> None:
    files = _built(tmp_path, signer)
    gh = _github(None)

    steps = release.publish(
        pre_release=False, dry_run=True, gh=gh, folder=tmp_path, changelog=_changelog(tmp_path)
    )

    assert gh.writes() == []
    [(create, notes)] = steps
    assert create[:6] == ["release", "create", TAG, "--repo", REPOSITORY, "--verify-tag"]
    assert "--prerelease" not in create
    assert sorted(create[-6:]) == sorted(one.name for one in files)
    assert notes == "### What changed for you\n\n- **Theater** keeps its place"
    shown = capsys.readouterr().out
    assert f"$ gh release create {TAG}" in shown
    assert "Dry run: nothing was sent." in shown


def test_a_publish_creates_the_release_with_the_notes_on_standard_input(
    tmp_path: Path, signer: Signer
) -> None:
    _built(tmp_path, signer)
    gh = _github(None)

    release.publish(
        pre_release=True, dry_run=False, gh=gh, folder=tmp_path, changelog=_changelog(tmp_path)
    )

    [create] = gh.writes()
    assert "--prerelease" in create
    assert [stdin for args, stdin in gh.calls if args[0] == "release"] == [
        "### What changed for you\n\n- **Theater** keeps its place"
    ]


def test_an_existing_release_gains_only_what_it_lacks(tmp_path: Path, signer: Signer) -> None:
    files = {one.name: one for one in _built(tmp_path, signer)}
    installer = files[f"Sift-{release.VERSION}-x64-setup.exe"]
    gh = _github({"assets": [_asset(installer)], "draft": False})

    release.publish(
        pre_release=False, dry_run=False, gh=gh, folder=tmp_path, changelog=_changelog(tmp_path)
    )

    upload, edit = gh.writes()
    assert upload[:5] == ["release", "upload", TAG, "--repo", REPOSITORY]
    assert installer.name not in upload
    assert len(upload) == 5 + 5
    assert edit[:2] == ["release", "edit"]
    assert "--prerelease=false" in edit


def test_a_file_already_published_with_other_bytes_stops_everything(
    tmp_path: Path, signer: Signer
) -> None:
    files = {one.name: one for one in _built(tmp_path, signer)}
    installer = files[f"Sift-{release.VERSION}-x64-setup.exe"]
    gh = _github({"assets": [_asset(installer, "sha256:" + "0" * 64)]})

    with pytest.raises(release.ReleaseFailed, match="different bytes"):
        release.publish(
            pre_release=False, dry_run=False, gh=gh, folder=tmp_path, changelog=_changelog(tmp_path)
        )
    assert gh.writes() == []


def test_a_tag_not_on_github_stops_the_publish(tmp_path: Path, signer: Signer) -> None:
    _built(tmp_path, signer)
    gh = _github(None, tagged=False)

    with pytest.raises(release.ReleaseFailed, match="no tag"):
        release.publish(
            pre_release=False, dry_run=True, gh=gh, folder=tmp_path, changelog=_changelog(tmp_path)
        )


def test_an_unsigned_release_is_never_published(tmp_path: Path, signer: Signer) -> None:
    _built(tmp_path, signer)
    (tmp_path / f"Sift-{release.VERSION}-x64-setup.exe.manifest.json.minisig").rename(
        tmp_path / "set-aside"
    )
    gh = _github(None)

    with pytest.raises(release.ReleaseFailed, match="signed or not at all"):
        release.publish(
            pre_release=False, dry_run=True, gh=gh, folder=tmp_path, changelog=_changelog(tmp_path)
        )
    assert gh.calls == []


def test_a_signature_by_another_key_is_refused_before_anything_is_sent(
    tmp_path: Path, signer: Signer
) -> None:
    _built(tmp_path, signer)
    stranger = Signer()
    manifest = tmp_path / f"Sift-{release.VERSION}-x64-setup.exe.manifest.json"
    Path(f"{manifest}.minisig").write_text(stranger.signature(manifest.read_bytes()), "utf-8")
    gh = _github(None)

    with pytest.raises(release.ReleaseFailed, match="not a signature by the key"):
        release.publish(
            pre_release=False, dry_run=True, gh=gh, folder=tmp_path, changelog=_changelog(tmp_path)
        )
    assert gh.calls == []


def test_the_signature_test_is_the_shells(signer: Signer) -> None:
    """Plain form, this key, these bytes: a prehashed signature is refused as the shell refuses it."""
    content = b"the manifest"
    assert release.signature_is_good(content, signer.signature(content), signer.public)
    assert not release.signature_is_good(b"other bytes", signer.signature(content), signer.public)
    assert not release.signature_is_good(
        content, signer.signature(content, algorithm=b"ED"), signer.public
    )
    assert not release.signature_is_good(content, "untrusted comment: only\n", signer.public)


@pytest.mark.parametrize(
    "argv",
    [
        ["--publish", "--no-sign"],
        ["--dry-run"],
        ["--pre-release"],
        ["--no-build"],
    ],
)
def test_the_flags_that_do_not_go_together_are_refused(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as refused:
        release.main(argv)
    assert refused.value.code == 2


def test_the_command_line_dry_run_builds_nothing(
    tmp_path: Path, signer: Signer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _built(tmp_path, signer)
    gh = _github(None)
    monkeypatch.setattr(release, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(release, "CHANGELOG", _changelog(tmp_path))
    monkeypatch.setattr(release, "run_gh", lambda args, stdin, **_kwargs: gh(args, stdin))
    monkeypatch.setattr(release, "build", lambda **_kwargs: pytest.fail("a dry run built"))
    # The clone this runs in is whatever state somebody left it in; whether a tree may be
    # released is `test_release_builds_only_a_pushed_commit.py`'s question, asked of a clone it makes.
    monkeypatch.setattr(release, "check_the_tree", lambda **_kwargs: None)

    assert release.main(["--publish", "--dry-run"]) == 0
    assert gh.writes() == []


def test_the_github_cli_is_the_one_release_gh_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELEASE_GH", sys.executable)
    assert Path(release.gh_program()) == Path(sys.executable)
    monkeypatch.setenv("RELEASE_GH", "a-program-nobody-has")
    with pytest.raises(release.ReleaseFailed, match="RELEASE_GH"):
        release.gh_program()
