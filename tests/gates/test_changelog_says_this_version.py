# SPDX-License-Identifier: AGPL-3.0-or-later
"""The changelog says what this version carries, in a shape the release script can publish.

A version's section of CHANGELOG.md is its release notes, on the release page and on the Updates
screen, so the version `pyproject.toml` declares needs a section of its own, or changes waiting
under Unreleased for the release that will carry them. The reading is `scripts/release.py`'s own,
so this gate and the script that publishes cannot disagree about what the file says.
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "sift_release_changelog_gate", REPO / "scripts" / "release.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load()


def _declared_version() -> str:
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return str(pyproject["project"]["version"])


def _sections() -> list[Any]:
    return list(release.read_changelog((REPO / "CHANGELOG.md").read_text(encoding="utf-8")))


def test_the_changelog_is_in_shape() -> None:
    """Unreleased first, then versions newest first, each once, each heading readable."""
    sections = _sections()
    assert any(one.title != release.UNRELEASED for one in sections), "CHANGELOG.md has no version"


def test_this_version_is_described() -> None:
    version = _declared_version()
    assert release.changelog_covers(version, _sections()), (
        f"pyproject.toml declares {version} and CHANGELOG.md says nothing about it: write a "
        f"`## {version}` section, or list the changes under `## {release.UNRELEASED}`"
    )


def test_every_section_reads_the_same_on_the_updates_screen() -> None:
    """Each section is publishable as it stands: nothing the Updates screen would draw otherwise."""
    for section in _sections():
        release.release_notes(section.body)


# --- the rules above, each shown able to fail ----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("## 0.1.2\n\n- a\n\n## 0.1.10\n\n- b\n", "below"),
        ("## 0.1.2\n\n- a\n\n## 0.1.2\n\n- b\n", "below"),
        ("## 0.1.2\n\n- a\n\n## Unreleased\n\n- b\n", "goes first"),
        ("## Next\n\n- a\n", "headed 'Next'"),
        ("## 0.1.2 - 2026-13-40\n\n- a\n", "not a date"),
        ("## 0.1.2" + " -- " + "2026-01-02\n\n- a\n", "headed"),
    ],
)
def test_a_changelog_out_of_shape_is_refused(text: str, said: str) -> None:
    with pytest.raises(release.ReleaseFailed, match=said):
        release.read_changelog(text)


def test_a_heading_inside_code_is_not_a_section() -> None:
    sections = release.read_changelog("## 0.1.2\n\n```\n## not a section\n```\n")
    assert [one.title for one in sections] == ["0.1.2"]


@pytest.mark.parametrize(
    "line",
    [
        "- the <b>new</b> screen",
        "| Site | Username |",
        "> a quote",
    ],
)
def test_notes_the_updates_screen_would_draw_differently_are_refused(line: str) -> None:
    with pytest.raises(release.ReleaseFailed, match="Updates screen"):
        release.release_notes(f"### What changed for you\n\n{line}\n")


def test_notes_keep_what_the_screen_draws_and_drop_what_no_page_shows() -> None:
    body = (
        "\n### Fixed\n\n- **Theater** keeps `<root>` as it is\n<!-- a note to editors -->\n\n\n\n"
        "```\n<kept in code>\n```\n[0.1.2]: https://example.com/compare\n"
    )
    assert release.release_notes(body) == (
        "### Fixed\n\n- **Theater** keeps `<root>` as it is\n\n```\n<kept in code>\n```"
    )


def test_this_version_is_not_covered_by_an_empty_section_or_an_empty_unreleased() -> None:
    sections = release.read_changelog("## Unreleased\n\n## 0.1.3\n\n## 0.1.2 - 2026-01-02\n\n- a\n")
    assert not release.changelog_covers("0.1.3", sections)
    assert release.changelog_covers("0.1.2", sections)
    waiting = release.read_changelog("## Unreleased\n\n- a\n\n## 0.1.2 - 2026-01-02\n\n- b\n")
    assert release.changelog_covers("0.1.3", waiting)


def test_a_signed_release_needs_a_dated_section_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unreleased describes an unsigned build for this device; a release that can be published
    needs its own section, dated the day it goes out."""
    monkeypatch.setattr(release, "VERSION", "0.1.3")
    changelog = tmp_path / "CHANGELOG.md"

    changelog.write_text("## Unreleased\n\n- a\n\n## 0.1.2 - 2026-01-02\n\n- b\n", encoding="utf-8")
    assert release.check_the_changelog(signed=False, path=changelog) == ""
    with pytest.raises(release.ReleaseFailed, match=r"no section for 0\.1\.3"):
        release.check_the_changelog(signed=True, path=changelog)

    changelog.write_text("## 0.1.3\n\n- a\n", encoding="utf-8")
    with pytest.raises(release.ReleaseFailed, match="has no date"):
        release.check_the_changelog(signed=True, path=changelog)

    changelog.write_text("## 0.1.3 - 2026-01-03\n\n- a\n", encoding="utf-8")
    assert release.check_the_changelog(signed=True, path=changelog) == "- a"

    changelog.write_text("## Unreleased\n\n## 0.1.2 - 2026-01-02\n\n- b\n", encoding="utf-8")
    with pytest.raises(release.ReleaseFailed, match=r"says nothing about 0\.1\.3"):
        release.check_the_changelog(signed=False, path=changelog)


def test_the_release_script_reads_the_changelog_before_it_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if sys.platform != "win32":
        pytest.skip("the release script refuses to build anywhere but Windows")
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## Unreleased\n\n## 0.0.1 - 2026-01-02\n\n- a\n", encoding="utf-8")
    built: list[bool] = []
    monkeypatch.setattr(release, "CHANGELOG", changelog)
    monkeypatch.setattr(release, "build", lambda **_kwargs: built.append(True))

    assert release.main(["--no-sign"]) == 1
    assert built == []
