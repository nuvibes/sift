# SPDX-License-Identifier: AGPL-3.0-or-later
"""The server and the client name every country the same way.

## Why this exists

A nationality is held as its two-letter code. The client draws it on a record through
`COUNTRIES` in `frontend/src/lib/people/countries.ts`; the server words it in a sentence (a History line,
a disagreement's cell) through `COUNTRIES` in `src/sift/kernel/countries.py`. Two tables that
must agree, written in two languages, fail silently: a person's record reads "Canada" while the line
under it says the box's answer was something else, and nothing errors.

The comparison is of the whole mapping, codes and names both, so a code added on one side only is
refused as surely as a name spelled two ways.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sift.kernel.countries import COUNTRIES

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
CLIENT = REPO / "frontend" / "src" / "lib" / "people" / "countries.ts"

#: The client's declaration, as text, matched rather than parsed, as the related-tabs gate does,
#: because the alternative is a TypeScript parser in a Python gate.
_BLOCK = re.compile(
    r"COUNTRIES: Readonly<Record<string, string>> = Object\.freeze\(\{\n(.*?)\n\}\);", re.S
)
_ENTRY = re.compile(r"^\t([A-Z]{2}): (['\"])(.*?)\2,?$", re.M)


def _client_table() -> dict[str, str]:
    found = _BLOCK.search(CLIENT.read_text(encoding="utf-8"))
    assert found is not None, f"{CLIENT} no longer declares COUNTRIES the way this gate reads it"
    block = found.group(1)
    entries = {code: name for code, _quote, name in _ENTRY.findall(block)}
    # Every line of the block is an entry this gate understood: a line it could not read would be
    # a country the comparison silently leaves out.
    assert len(entries) == len([line for line in block.splitlines() if line.strip()])
    return entries


def test_the_server_names_every_country_the_client_does() -> None:
    client = _client_table()
    assert len(client) > 200
    assert dict(COUNTRIES) == client
