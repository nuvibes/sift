# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one reader of Sift's version."""

from __future__ import annotations

import re
import tomllib
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from sift.kernel.version import app_version, release_of

REPO = Path(__file__).resolve().parents[4]


def test_it_reports_the_version_this_package_was_built_with() -> None:
    """A known positive. An answer of "" would satisfy a test that only checked the type, and "" is
    exactly what this returns when it cannot find the package, so the shape is asserted."""
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", app_version())


def test_it_reports_what_the_project_declares() -> None:
    """The number a person is shown comes from the field the project declares, not from anywhere
    that could have been written twice."""
    declared = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert app_version() == declared["project"]["version"]


def test_a_release_is_its_three_numbers_and_nothing_else_is_one() -> None:
    assert release_of(" 0.2.0 ") == (0, 2, 0)
    assert release_of("1.10.3") > (1, 9, 30)
    for text in ("", "0.2", "v0.2.0", "0.2.0-rc.1", "0.2.0.1"):
        assert release_of(text) is None, text


def test_an_uninstalled_package_answers_with_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Running from a source tree with nothing installed, there is no version and none is invented.

    The empty string is load-bearing rather than a placeholder: it parses as no version, so the
    update check compares against nothing and declines to offer an update instead of announcing one
    against a number it made up.
    """

    def missing(_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr("sift.kernel.version.package_version", missing)
    assert app_version() == ""
