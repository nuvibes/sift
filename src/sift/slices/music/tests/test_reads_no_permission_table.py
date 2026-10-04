# SPDX-License-Identifier: AGPL-3.0-or-later
"""No statement in the music feature names a table carrying who may see a file: files are asked of
the kernel and access layer. The rule is read from the lint (`semgrep/access.yml`,
`sift-no-asset-sql-outside-kernel`) and checked with the feature's own tests.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

FEATURE = Path(__file__).resolve().parents[1]
RULES = Path(__file__).resolve().parents[5] / "semgrep" / "access.yml"


def _the_rule() -> re.Pattern[str]:
    """The lint's own pattern for a statement against a permission-carrying table."""
    text = RULES.read_text(encoding="utf-8")
    found = re.search(r"- pattern-regex: >-\n\s+(\S.*)\n", text)
    assert found is not None, "semgrep/access.yml no longer declares its pattern the way this reads"
    return re.compile(found.group(1).strip())


def test_no_statement_in_the_feature_names_a_permission_carrying_table() -> None:
    rule = _the_rule()
    modules = sorted(FEATURE.glob("*.py"))
    # A walk that found nothing would pass for ever.
    assert len(modules) >= 10, [one.name for one in modules]
    named = [
        f"{module.name}:{number}: {line.strip()}"
        for module in modules
        for number, line in enumerate(module.read_text(encoding="utf-8").splitlines(), start=1)
        if rule.search(line)
    ]
    assert not named, (
        "\nThese lines name a table that carries who may see a file. Ask the kernel (the file's\n"
        "song, length and name) or the access layer (what a person may be shown) instead.\n\n  "
        + "\n  ".join(named)
        + "\n"
    )


def test_the_rule_sees_the_statements_it_is_about() -> None:
    """A known positive for each table the feature is most tempted to read, and a known negative."""
    rule = _the_rule()
    assert rule.search("SELECT id, music FROM assets WHERE id = ?")
    assert rule.search(" (SELECT l.filename FROM asset_locations l WHERE l.asset_id = a.id")
    assert rule.search("f JOIN assets a ON a.id = f.asset_id")
    assert rule.search("SELECT 1 FROM library_roots r")
    assert rule.search("JOIN folders f ON f.id = l.folder_id")
    assert not rule.search("SELECT asset_id FROM audio_fingerprints WHERE asset_id = ?")
    assert not rule.search("DELETE FROM music_pairs WHERE a_id = ?")
