# SPDX-License-Identifier: AGPL-3.0-or-later
# /// script
# requires-python = ">=3.13"
# dependencies = ["semgrep==1.178.0"]
# ///
"""Semgrep at the one version this repository scans with, in an environment of its own.

    python scripts/venv_tool.py semgrep scan --config semgrep/ --error

The version above is the only place it is named. Semgrep is a development tool, so its own
dependencies stay out of `uv.lock`: that lockfile is what Sift ships and what the audit reads, and
a scanner's pins (an old PyJWT among them) are not part of either. `uv run --locked --script` installs
this file's environment from `semgrep_pinned.py.lock` beside it, hash-checked like the project's
own, and reuses it from uv's cache afterwards, so a commit hook works without the network.

Below 1.179: that release ships no Windows core binary, and the commit hook runs on Windows.
After changing the version, run `uv lock --script scripts/tools/semgrep_pinned.py`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    # The console script beside this environment's interpreter: `Scripts/` with an `.exe` on
    # Windows, `bin/` elsewhere, which is where `sys.executable` already sits.
    command = Path(sys.executable).with_name(
        "semgrep.exe" if sys.platform == "win32" else "semgrep"
    )
    return subprocess.run([str(command), *argv], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
