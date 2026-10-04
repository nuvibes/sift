# SPDX-License-Identifier: AGPL-3.0-or-later
"""The server and the client read a stash-box's constant by the same pattern.

A `word` field's value spelled as a box's constant (`BLONDE`) is said as the word it stands for.
The server says it so in a History line and a disagreement's cell (`records.value_said`, through
`records.CONSTANT`); the client says it so in the record's own cells (`$lib/entity/constant-word`). If
they drift, the record draws `BLONDE` beside a line saying "Blonde". Two patterns in two languages
drift silently, so this reads the client's and compares.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sift.kernel.records import CONSTANT, SPELLED

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
CLIENT = REPO / "frontend" / "src" / "lib" / "entity" / "constant-word.ts"

_DECLARED = re.compile(r"^export const CONSTANT = /\^(?P<pattern>.+)\$/;$", re.M)


def test_the_client_reads_a_constant_by_the_servers_pattern() -> None:
    found = _DECLARED.search(CLIENT.read_text(encoding="utf-8"))
    assert found is not None, f"{CLIENT} no longer declares CONSTANT the way this gate reads it"
    assert found["pattern"] == CONSTANT.pattern


_SPELLED = re.compile(r"^export const SPELLED: [^=]+= \{(?P<body>[^}]*)\};$", re.M)
_ENTRY = re.compile(r"(?P<key>[A-Z][A-Z0-9_]*): '(?P<words>[^']*)'")


def test_the_client_spells_the_same_constants_the_server_does() -> None:
    """A constant that is no word ("NA") is said the same on the record and in a History line."""
    found = _SPELLED.search(CLIENT.read_text(encoding="utf-8"))
    assert found is not None, f"{CLIENT} no longer declares SPELLED the way this gate reads it"
    client = {one["key"]: one["words"] for one in _ENTRY.finditer(found["body"])}
    assert client == dict(SPELLED)
