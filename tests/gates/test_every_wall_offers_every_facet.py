# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every wall offers every facet its server counts, and offers nothing its server refuses.

## Why this is worth a gate

A facet is declared twice, once per side, for each kind of thing a wall can show: the server's
list of what it groups by (`FACETS` for files, `ENTITY_FACETS` for the five walls of things, the
Downloads queue's own), and the browser's list of the columns the filter panel draws for that
kind (`facet-labels.ts`). Each side reads complete on its own, and they drift two ways with no
symptom anybody sees at once:

  * a column the browser offers that the server does not count for THAT kind is a column that
    draws empty forever (the panel turns the refusal into an empty list);
  * a dimension the server counts that no wall offers is a filter nobody can reach from the panel:
    a dimension counted and declared and never drawn.

`test_one_name_per_filter.py` already holds every key to SOME dimension; this holds each kind's
list to its OWN, both ways, and holds the admin-only flags to the server's.

## The one kind of entry that is neither

A RETIRED entry (`linked`) is a word the server still filters by and the panel no longer draws as
a column. It is in the browser's list on purpose, so an address carrying it still narrows, and
it counts as accounted for here. A SPAN (`added`) is a range the panel draws itself and the server
never groups by, so it must not be a dimension.

Read out of the TypeScript rather than run, for the reason the neighbouring gate gives.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sift.kernel.access import ADMIN_FACETS, FACETS
from sift.kernel.access.constraints import ADMIN_ENTITY_FACETS, ENTITY_FACETS
from sift.slices.download.router import DOWNLOAD_FACETS

pytestmark = [pytest.mark.gate, pytest.mark.unit]

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "lib"
    / "components"
    / "shell"
    / "facet-labels.ts"
)

#: Each browser list, by the kind of thing its wall shows.
LISTS = {
    "asset": "FACETS",
    "person": "PERSON_FACETS",
    "site": "SITE_FACETS",
    "tag": "TAG_FACETS",
    "collection": "COLLECTION_FACETS",
    "photo_set": "PHOTO_SET_FACETS",
    "song": "SONG_FACETS",
    "download": "DOWNLOAD_FACETS",
}

#: Downloads is admin-only as a whole, so its one column is.
_DOWNLOAD_ADMIN = frozenset(DOWNLOAD_FACETS)

_ENTRY = re.compile(r"\{\s*key: '([a-z_]+)',([^}]*)\}")
_NAMED = re.compile(r"\b([A-Z_]+_FACET)\b")


def _without_comments(source: str) -> str:
    without_blocks = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", without_blocks)


def _named_entries(source: str) -> dict[str, tuple[str, str]]:
    """The entries written once and placed in several lists (`SHARING_FACET`), by name."""
    found = {}
    for name, body in re.findall(r"const ([A-Z_]+_FACET): Facet = (\{.*?\});", source, re.DOTALL):
        entry = _ENTRY.search(body)
        assert entry is not None, f"{name} could not be read"
        found[name] = (entry.group(1), entry.group(2))
    return found


def _list(source: str, name: str) -> list[tuple[str, str]]:
    """One list's entries in written order: its key, and the rest of the entry (its flags)."""
    start = re.search(rf"(?:export )?const {name}(?:: readonly Facet\[\])? = \[", source)
    assert start is not None, f"{name} is not declared"
    depth = 0
    for at in range(start.end() - 1, len(source)):
        if source[at] == "[":
            depth += 1
        elif source[at] == "]":
            depth -= 1
            if depth == 0:
                body = source[start.end() : at]
                break
    else:
        raise AssertionError(f"{name} is not closed")
    named = _named_entries(source)
    entries: list[tuple[int, tuple[str, str]]] = [
        (found.start(), (found.group(1), found.group(2))) for found in _ENTRY.finditer(body)
    ]
    entries += [(found.start(), named[found.group(1)]) for found in _NAMED.finditer(body)]
    return [entry for _, entry in sorted(entries)]


def _server(subject: str) -> tuple[set[str], frozenset[str]]:
    """What the server groups by for this kind, and which of those only an admin may ask."""
    if subject == "asset":
        return set(FACETS), ADMIN_FACETS
    if subject == "download":
        return set(DOWNLOAD_FACETS), _DOWNLOAD_ADMIN
    return set(ENTITY_FACETS[subject]), ADMIN_ENTITY_FACETS


@pytest.mark.parametrize("subject", sorted(LISTS))
def test_every_column_a_wall_offers_is_one_its_server_counts(subject: str) -> None:
    """A column the browser draws for this kind and the server refuses for it draws empty."""
    source = _without_comments(SOURCE.read_text())
    counted, _ = _server(subject)
    entries = _list(source, LISTS[subject])
    assert entries, f"{LISTS[subject]} could not be read"
    refused = [
        key
        for key, rest in entries
        if "span: true" not in rest and "retired: true" not in rest and key not in counted
    ]
    assert not refused, (
        f"the {subject} wall offers {refused}, which its server does not count for {subject}"
    )
    spans = [key for key, rest in entries if "span: true" in rest and key in counted]
    assert not spans, f"{spans} is a dimension the server groups by, so a column and not a span"


@pytest.mark.parametrize("subject", sorted(LISTS))
def test_every_dimension_a_server_counts_is_offered_by_its_wall(subject: str) -> None:
    """A dimension counted and never drawn is a filter nobody can reach from the panel."""
    source = _without_comments(SOURCE.read_text())
    counted, _ = _server(subject)
    offered = {key for key, _ in _list(source, LISTS[subject])}
    unoffered = sorted(counted - offered)
    assert not unoffered, (
        f"the server counts {unoffered} for {subject} and no column on that wall offers it"
    )


@pytest.mark.parametrize("subject", sorted(LISTS))
def test_a_column_is_admin_only_exactly_where_its_server_says(subject: str) -> None:
    """Drawn to a guest, an admin-only column is a column that fails to load; kept from an admin,
    one the server answers is a question nobody can ask."""
    source = _without_comments(SOURCE.read_text())
    counted, admin = _server(subject)
    for key, rest in _list(source, LISTS[subject]):
        if key not in counted:
            continue
        flagged = "admin: true" in rest
        assert flagged == (key in admin), (
            f"{subject}.{key}: the browser says admin-only is {flagged}, the server {key in admin}"
        )
