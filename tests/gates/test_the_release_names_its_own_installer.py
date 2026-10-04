# SPDX-License-Identifier: AGPL-3.0-or-later
"""The release hashes and signs the installer it just built, and not a neighbour.

The build folder keeps every installer made there. A script that picked one out of a sorted glob
and took the last name would go wrong on the first two-digit patch: a sort of names has no idea
that 10 is bigger than 9, so `Sift-0.1.9-x64-setup.exe` sorts after `Sift-0.1.10-x64-setup.exe`,
and with 0.1.10 just built, 0.1.9 would be hashed, signed, and copied to the desktop as the release.

A valid signature over the wrong file is worse than no signature at all, because it is the thing
somebody checks and is reassured by. So the rule is not "sort better": it is that the script
already KNOWS which file it just made, and must name it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

RELEASE = Path(__file__).resolve().parents[2] / "scripts" / "release.py"


def _pack() -> ast.FunctionDef:
    tree = ast.parse(RELEASE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "pack":
            return node
    raise AssertionError("scripts/release.py has no pack(), so this gate is checking nothing")


def test_the_installer_is_named_from_the_version() -> None:
    """The one thing that makes the fault impossible: the name is built, not chosen."""
    source = ast.unparse(_pack())

    assert "Sift-{VERSION}-x64-setup.exe" in source, (
        "pack() no longer names the installer after the version it just built. "
        "Whatever it does instead has to be able to tell 0.1.10 from 0.1.9."
    )


def test_no_installer_is_chosen_out_of_a_sort() -> None:
    """The shape of the fault rather than the instance of it.

    `sorted(...)[-1]` over a folder of versioned names is the bug however it is spelled, and a
    later rewrite that reintroduces it would pass the test above while being exactly as wrong.
    """
    source = ast.unparse(_pack())

    assert "sorted" not in source, (
        "pack() sorts something. If it is picking the installer out of a sorted glob again, "
        "0.1.10 sorts before 0.1.9 and the wrong file gets signed."
    )
    assert "glob" not in source, "pack() globs for the installer again; name it instead"


def test_a_missing_installer_is_a_failure_and_not_a_neighbour() -> None:
    """Naming the file is only half of it. If the packer produced nothing, the script has to stop
    rather than reach for whatever else is in the folder."""
    source = ast.unparse(_pack())

    assert "ReleaseFailed" in source, "pack() no longer refuses when its installer is not there"
