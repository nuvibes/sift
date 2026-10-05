# SPDX-License-Identifier: AGPL-3.0-or-later
"""The log archive as the desktop app makes it: a child of its own, isolated, loading nothing native."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from sift.slices.logs.tests.test_logbundle import PLANTED, read, write

pytestmark = pytest.mark.integration


def test_run_as_a_child_it_makes_the_archive_with_the_accounts_folder_taken_out(
    tmp_path: Path,
) -> None:
    """How the desktop app runs it: isolated, on its own, the account's name read from its home."""
    home = tmp_path / "Wrenfield"
    home.mkdir()
    lines = ["could not open D:\\Wrenfield\\clip.mp4", *(line for line, _value in PLANTED.values())]
    write(tmp_path / "app", "backend.log", lines)
    out, app = tmp_path / "logs.zip", str(tmp_path / "app")
    env = {**os.environ, "USERPROFILE": str(home), "HOME": str(home)}

    done = subprocess.run(
        [sys.executable, "-I", "-m", "sift.logbundle", "--out", str(out), "--app-logs", app],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    held = read(out)["app/backend.log"]
    assert "D:\\[redacted]\\clip.mp4" in held
    assert not [value for _line, value in PLANTED.values() if value in held]


def test_it_imports_nothing_but_the_standard_library_and_the_redaction_rules() -> None:
    """It runs where a native library crashes the server, so it must load none."""
    probe = (
        "import sys; before = set(sys.modules); import sift.logbundle; "
        "print(*sorted(m for m in set(sys.modules) - before "
        "if m.split('.')[0] not in sys.stdlib_module_names))"
    )

    done = subprocess.run(
        [sys.executable, "-I", "-c", probe], capture_output=True, text=True, timeout=60, check=True
    )

    assert done.stdout.split() == ["sift", "sift.kernel", "sift.kernel.redaction", "sift.logbundle"]
