# SPDX-License-Identifier: AGPL-3.0-or-later
"""SECURITY.md prints the key a release is signed with, and the shell verifies with the one in its
source: two copies of one fact, held equal here so a rotated key is rotated in both."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]


def test_security_md_names_the_key_the_shell_verifies_with() -> None:
    shell = (REPO / "desktop" / "src" / "update.ts").read_text(encoding="utf-8")
    declared = re.search(r"PUBLIC_KEY_BASE64 = '([A-Za-z0-9+/=]+)'", shell)
    assert declared, "the shell no longer declares PUBLIC_KEY_BASE64 where this looks"
    security = (REPO / "SECURITY.md").read_text(encoding="utf-8")
    assert declared.group(1) in security, (
        "SECURITY.md does not print the key the shell verifies releases with; a reader checking a"
        " download against the document would check it against the wrong key"
    )
