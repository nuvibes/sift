# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where the music calibration fingerprints are, on a machine that holds a copy.

The tree ships none. The folder is named by one environment variable, and the tests that need it
skip with `WHY_SKIPPED` where it is unset or does not exist.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The environment variable naming the folder.
VARIABLE = "MUSIC_CALIBRATION_FINGERPRINTS"

#: What a skipped test says.
WHY_SKIPPED = f"no calibration fingerprints on this device (set {VARIABLE} to a folder of them)"


def folder() -> Path | None:
    """The calibration folder, or None where none is named or it is not there."""
    named = os.getenv(VARIABLE, "").strip()
    return Path(named) if named and Path(named).is_dir() else None
