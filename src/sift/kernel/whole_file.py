# SPDX-License-Identifier: AGPL-3.0-or-later
"""A small file written whole or not at all.

Written beside the target and renamed over it, so a reader never sees half of one. The desktop
shell reads the library switch note the instant the backend process has gone, and a torn note
would be read as none.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_json_whole(target: Path, value: Any) -> None:
    """Write `value` as JSON to `target`, whole or not at all. Blocking."""
    partial = target.with_name(target.name + ".partial")
    partial.write_text(json.dumps(value, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(partial, target)
