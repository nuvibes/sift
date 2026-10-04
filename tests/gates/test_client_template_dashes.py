# SPDX-License-Identifier: AGPL-3.0-or-later
"""The dash gate reads a client TEMPLATE literal as well as a quoted string.

A toast built with a substitution is copy as much as a quoted string is, so
`scripts/check_display_dashes.py` reads template literals in the client as well as `'...'` and
`"..."`. What it reads is pinned here, and so is what it leaves alone: a message handed to
`Error(` is for a developer.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.gate, pytest.mark.unit]

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_display_dashes.py"
_SPEC = importlib.util.spec_from_file_location("_display_dashes", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
dashes = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dashes)

#: The dash the gate refuses, planted into the specimens below.
DASH = " -- "


def test_a_template_with_the_wrong_dash_is_found() -> None:
    code = "toasts.show(`${n} more were grouped with it" + DASH + "check them on their page.`);"
    assert [line for line, _ in dashes.client_template_dashes(code)] == [1]


def test_the_em_dash_and_a_developer_error_pass() -> None:
    assert dashes.client_template_dashes("show(`grouped with it \\u2014 check them`);") == []
    code = "throw new Error(\n\t`needs one" + DASH + "it has ${n}`\n);"
    assert dashes.client_template_dashes(code) == []
