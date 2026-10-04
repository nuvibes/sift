# SPDX-License-Identifier: AGPL-3.0-or-later
"""The check on the tunnel client Sift builds says when a move is due, and only then.

`scripts/check_tunnel_client.py` asks two questions that need the network, so nothing here runs it
against the world: the scan of the built file and the release list are stood in for by text and
objects this file makes. What is held is the judgement between an answer and the manifest: a new
advisory is said, a known one is not, a closed one asks to leave the list, a newer release is said,
and a question that could not be asked is never read as a clean answer.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "scripts" / "vendor_manifest.json"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_check_tunnel_client_under_test", REPO / "scripts" / "check_tunnel_client.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    return _load()


def _manifest(**changes: Any) -> dict[str, Any]:
    entry = {
        "name": "wireproxy",
        "file": "bin/wireproxy.exe",
        "recipe": "scripts/vendor_build/wireproxy/build.py",
        "source": {"repository": "https://github.com/example/wireproxy", "tag": "v1.1.3"},
        "known_findings": ["GO-0000-0001"],
        **changes,
    }
    return {"built": [entry]}


def _scan_output(*ids: str) -> str:
    """govulncheck's shape: JSON objects one after another, findings among other messages."""
    parts: list[dict[str, Any]] = [{"config": {"scanner_name": "govulncheck"}}]
    for one in ids:
        parts.append({"osv": {"id": one}})
        parts.append({"finding": {"osv": one, "trace": [{"module": "example.org/x"}]}})
    return "\n".join(json.dumps(part, indent=2) for part in parts)


@pytest.fixture
def vendor(tmp_path: Path) -> Path:
    program = tmp_path / "bin" / "wireproxy.exe"
    program.parent.mkdir()
    program.write_bytes(b"MZ")
    return tmp_path


def _release(tag: str) -> Any:
    return lambda _address: {"tag_name": tag}


def test_nothing_is_due_when_the_scan_and_the_release_match_the_manifest(
    checker: ModuleType, vendor: Path
) -> None:
    due = checker.check(
        _manifest(),
        scan=lambda _program: _scan_output("GO-0000-0001"),
        fetch=_release("v1.1.3"),
        vendor=vendor,
    )
    assert due == []


def test_a_new_advisory_is_said_by_its_id(checker: ModuleType, vendor: Path) -> None:
    due = checker.check(
        _manifest(),
        scan=lambda _program: _scan_output("GO-0000-0001", "GO-0000-0002"),
        fetch=None,
        vendor=vendor,
    )
    assert due == ["a new advisory against the built tunnel client: GO-0000-0002"]


def test_a_known_advisory_no_longer_reported_asks_to_leave_the_list(
    checker: ModuleType, vendor: Path
) -> None:
    due = checker.check(
        _manifest(), scan=lambda _program: _scan_output(), fetch=None, vendor=vendor
    )
    assert len(due) == 1
    assert "GO-0000-0001" in due[0]
    assert "no longer reported" in due[0]


def test_one_advisory_found_in_three_places_is_said_once(checker: ModuleType) -> None:
    text = _scan_output("GO-0000-0002", "GO-0000-0002", "GO-0000-0002")
    assert checker.findings_in(text) == {"GO-0000-0002"}


def test_a_newer_release_of_the_source_is_said_with_both_tags(
    checker: ModuleType, vendor: Path
) -> None:
    asked: list[str] = []

    def fetch(address: str) -> dict[str, Any]:
        asked.append(address)
        return {"tag_name": "v1.2.0"}

    due = checker.check(_manifest(), scan=None, fetch=fetch, vendor=vendor)
    assert asked == ["https://api.github.com/repos/example/wireproxy/releases/latest"]
    assert due == ["the source has a release the pin is not at: v1.2.0 (pinned v1.1.3)"]


def test_the_scan_is_given_the_built_file(checker: ModuleType, vendor: Path) -> None:
    seen: list[Path] = []

    def scan(program: Path) -> str:
        seen.append(program)
        return _scan_output("GO-0000-0001")

    checker.check(_manifest(), scan=scan, fetch=None, vendor=vendor)
    assert seen == [vendor / "bin" / "wireproxy.exe"]


def test_a_missing_built_file_is_unanswered_not_clean(checker: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(checker.Unanswered, match="build it with"):
        checker.check(_manifest(), scan=lambda _program: "", fetch=None, vendor=tmp_path)


def test_a_release_list_without_a_tag_is_unanswered(checker: ModuleType, vendor: Path) -> None:
    with pytest.raises(checker.Unanswered, match="without a tag"):
        checker.check(_manifest(), scan=None, fetch=lambda _address: {}, vendor=vendor)


def test_a_source_that_is_not_on_github_is_unanswered(checker: ModuleType, vendor: Path) -> None:
    manifest = _manifest(source={"repository": "https://example.org/wireproxy", "tag": "v1.1.3"})
    with pytest.raises(checker.Unanswered, match="not a GitHub repository"):
        checker.check(manifest, scan=None, fetch=_release("v1.1.3"), vendor=vendor)


def test_the_run_exits_two_when_the_scanner_is_not_installed(
    checker: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SIFT_GOVULNCHECK", raising=False)
    monkeypatch.setattr(checker.shutil, "which", lambda _name: None)
    assert checker.main([]) == 2
    assert "unanswered" in capsys.readouterr().out


def test_the_run_exits_one_when_a_move_is_due_and_nought_when_none_is(
    checker: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pinned = json.loads(MANIFEST.read_text("utf-8"))["built"][0]["source"]["tag"]
    monkeypatch.setattr(checker, "_fetch", lambda _address: {"tag_name": pinned})
    assert checker.main(["--skip-advisories"]) == 0
    monkeypatch.setattr(checker, "_fetch", lambda _address: {"tag_name": pinned + "-next"})
    assert checker.main(["--skip-advisories"]) == 1
    assert "(pinned " + pinned + ")" in capsys.readouterr().out


def test_the_manifest_lists_the_findings_already_judged() -> None:
    """The list the check compares against exists, is ids, and every id is explained in the note."""
    (entry,) = [
        one
        for one in json.loads(MANIFEST.read_text("utf-8"))["built"]
        if one["name"] == "wireproxy"
    ]
    known = entry["known_findings"]
    assert isinstance(known, list)
    assert all(isinstance(one, str) and one.startswith("GO-") for one in known)
    assert all(one in entry["note"] for one in known)
