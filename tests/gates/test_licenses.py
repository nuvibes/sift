# SPDX-License-Identifier: AGPL-3.0-or-later
"""The license scan rejects an incompatible dependency, and is clean on the real tree.

Sift is AGPL-3.0 and the scan is what keeps a dependency under an incompatible license from
shipping unnoticed. A scan that cannot fail would pass silently the day one did, so the failing
cases are asserted here alongside the clean one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.gate

REPO = Path(__file__).resolve().parents[2]
CHECKER = REPO / "scripts" / "check_licenses.py"


def _run(sbom: dict[str, object], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "sbom.cdx.json"
    path.write_text(json.dumps(sbom))
    return subprocess.run(
        [sys.executable, str(CHECKER), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_an_incompatible_license_fails_the_scan(tmp_path: Path) -> None:
    """GPL-2.0-only has no "or later" clause and cannot be combined with GPLv3-family code, so it
    cannot ship inside an AGPL-3.0 work."""
    result = _run(
        {
            "components": [
                {
                    "name": "trap",
                    "version": "1.0",
                    "licenses": [{"license": {"id": "GPL-2.0-only"}}],
                }
            ]
        },
        tmp_path,
    )
    assert result.returncode == 1, result.stdout
    assert "incompatible" in result.stdout
    assert "trap" in result.stdout


def test_an_undeclared_license_fails_the_scan(tmp_path: Path) -> None:
    """Fail closed: a package that declares no license and is not a reviewed exception is a failure,
    because unrecognized and incompatible are indistinguishable from a distance."""
    result = _run(
        {"components": [{"name": "silent", "version": "1.0", "licenses": []}]},
        tmp_path,
    )
    assert result.returncode == 1, result.stdout
    assert "unknown" in result.stdout
    assert "silent" in result.stdout


def test_a_nonfree_license_is_named_as_incompatible(tmp_path: Path) -> None:
    result = _run(
        {
            "components": [
                {
                    "name": "sourceavail",
                    "version": "1.0",
                    "licenses": [{"license": {"name": "Server Side Public License"}}],
                }
            ]
        },
        tmp_path,
    )
    assert result.returncode == 1, result.stdout
    assert "incompatible" in result.stdout


def test_permissive_and_choice_licenses_pass(tmp_path: Path) -> None:
    """The other direction: a permissive id, a free-text classifier, and an OR expression are all
    compatible, so a bill made only of them passes."""
    result = _run(
        {
            "components": [
                {"name": "a", "version": "1.0", "licenses": [{"license": {"id": "MIT"}}]},
                {
                    "name": "b",
                    "version": "1.0",
                    "licenses": [{"license": {"name": "Apache Software License"}}],
                },
                {"name": "c", "version": "1.0", "licenses": [{"expression": "MIT OR Apache-2.0"}]},
                {"name": "d", "version": "1.0", "licenses": [{"license": {"id": "MPL-2.0"}}]},
            ]
        },
        tmp_path,
    )
    assert result.returncode == 0, result.stdout


def _cyclonedx_available() -> bool:
    try:
        import cyclonedx_py  # type: ignore[import-untyped]  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.integration
def test_the_real_dependency_tree_is_license_clean(tmp_path: Path) -> None:
    """Generate the bill of materials from the actual environment and scan it. This is what proves
    the tree really is clean, and that the reviewed exceptions still cover the packages that declare
    no license of their own."""
    if not _cyclonedx_available():
        if os.environ.get("CI"):
            pytest.fail("cyclonedx-bom is not installed in CI; the license scan would not run")
        pytest.skip("cyclonedx-bom not installed")

    sbom_path = tmp_path / "real.cdx.json"
    generate = subprocess.run(
        [
            sys.executable,
            "-m",
            "cyclonedx_py",
            "environment",
            sys.executable,
            "--output-format",
            "json",
            "--output-file",
            str(sbom_path),
            "--output-reproducible",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert generate.returncode == 0, generate.stderr

    result = subprocess.run(
        [sys.executable, str(CHECKER), str(sbom_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout
