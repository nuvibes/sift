# SPDX-License-Identifier: AGPL-3.0-or-later
"""The fuses the release checks for and the fuses the packer sets are the same five Electron fuses:
three close a door (script mode, a debugger, a preloaded module) and two decide the start (load only
from the archive, and check it against a compiled-in hash). The packer sets them and the release
checks the built executable against an independent list, so this keeps the pair agreeing.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "scripts" / "release.py"
PACKER = ROOT / "desktop" / "electron-builder.yml"

#: What this application has decided, written out once more: read from either file, the comparison
#: could only say they agree.
DECIDED = {
    "RunAsNode": False,
    "EnableNodeOptionsEnvironmentVariable": False,
    "EnableNodeCliInspectArguments": False,
    "EnableEmbeddedAsarIntegrityValidation": True,
    "OnlyLoadAppFromAsar": True,
}


def _declared_in_the_release() -> dict[str, bool]:
    """`INTENDED_FUSES` in scripts/release.py, by fuse name."""
    tree = ast.parse(RELEASE.read_text(encoding="utf-8"))
    for node in tree.body:
        target = getattr(node, "target", None)
        named = isinstance(target, ast.Name) and target.id == "INTENDED_FUSES"
        if isinstance(node, ast.AnnAssign) and named and node.value is not None:
            wanted = ast.literal_eval(node.value)
            return {name: state for _, (name, state) in wanted.items()}
    raise AssertionError(
        "scripts/release.py no longer declares INTENDED_FUSES, so nothing checks the fuses on the "
        "executable that ships"
    )


def _declared_in_the_packer() -> dict[str, bool]:
    """The `electronFuses` block in desktop/electron-builder.yml, by fuse name, read with a regex
    whose shape the test below pins."""
    text = PACKER.read_text(encoding="utf-8")
    block = re.search(r"^electronFuses:\n((?:[ \t]+\S+:.*\n)+)", text, re.MULTILINE)
    if block is None:
        raise AssertionError(
            "desktop/electron-builder.yml has no electronFuses block, so the shipped executable "
            "carries whatever Electron was built with"
        )
    found: dict[str, bool] = {}
    for key, value in re.findall(r"^\s+(\w+):\s*(true|false)\s*$", block.group(1), re.MULTILINE):
        found[key[0].upper() + key[1:]] = value == "true"
    return found


def test_the_release_checks_every_fuse_this_application_has_decided() -> None:
    assert _declared_in_the_release() == DECIDED


def test_the_packer_sets_every_fuse_the_release_checks() -> None:
    """The packer sets every fuse the release checks."""
    assert _declared_in_the_packer() == _declared_in_the_release()


def test_the_two_archive_fuses_are_on_together() -> None:
    """The two archive fuses are on together: `OnlyLoadAppFromAsar` without integrity checking lets
    the archive be edited; integrity without it loads an unhashed `app` folder."""
    declared = _declared_in_the_release()

    assert declared["OnlyLoadAppFromAsar"] is True
    assert declared["EnableEmbeddedAsarIntegrityValidation"] is True
