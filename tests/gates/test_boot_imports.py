# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the backend imports before it listens: a count that only falls, and the heavy runtimes that
belong to work after the start never among them.

Every module imported here is read, and on a first start after an install compiled, before the
window can be answered; the count is the one figure about it that is the same on every machine.
A new module at start is a decision: raise the record in the same change, and say why.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

RECORD = Path(__file__).parent / "data" / "boot_imports.json"

#: How far under the record a count may fall before the record has to come down with it.
SLACK = 10

_COUNT = (
    "import json, sys, sift.main;"
    "names = sorted(sys.modules);"
    "print(json.dumps({"
    "'sift': sum(1 for n in names if n == 'sift' or n.startswith('sift.')),"
    "'top': sorted({n.split('.')[0] for n in names})}))"
)


@pytest.fixture(scope="module")
def imported() -> dict[str, object]:
    """One fresh interpreter's imports, read once for both tests."""
    done = subprocess.run(
        [sys.executable, "-c", _COUNT], capture_output=True, text=True, check=True, timeout=120
    )
    answer: dict[str, object] = json.loads(done.stdout.strip().splitlines()[-1])
    return answer


def test_the_start_imports_no_more_of_sift_than_its_record(imported: dict[str, object]) -> None:
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    count = int(str(imported["sift"]))
    assert count <= record["sift_modules"], (
        f"the backend imports {count} of Sift's modules before it listens, over the record of "
        f"{record['sift_modules']}. Import the new ones where they are used, or raise the record "
        f"in {RECORD.name} and say why."
    )
    assert count >= record["sift_modules"] - SLACK, (
        f"the backend imports {count} of Sift's modules before it listens: lower the record in "
        f"{RECORD.name} to {count}, so it holds."
    )


def test_no_runtime_of_later_work_is_imported_at_the_start(imported: dict[str, object]) -> None:
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    top = set(imported["top"])  # type: ignore[call-overload]
    early = sorted(top & set(record["never_at_start"]))
    assert early == [], f"{', '.join(early)} imported before the backend listens"
