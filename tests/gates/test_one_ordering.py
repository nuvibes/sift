# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every write of a name writes its stored ordering key (`kernel.sorting.sort_key`), which folds
accents where `COLLATE NOCASE` folds only ASCII; a write that forgets is silent, the row filing in
its old place. Read from the SQL text; `test_sorting.py` holds what a key IS.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sift.kernel.access.schema import SORT_KEYS
from sift.kernel.content.schema import CONTENT_SORT_KEYS

pytestmark = [pytest.mark.gate, pytest.mark.unit]

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src" / "sift"

#: `(table, source column, key column)` from the catalog's and the content component's own lists.
KEYED = {table: (source, key) for table, source, key in (*SORT_KEYS, *CONTENT_SORT_KEYS)}

#: Adjacent Python string literals joined, so a statement split across lines reads as one.
_JOINED = re.compile(r'"\s*\n\s*"')

_INSERT = re.compile(r"INSERT (?:OR IGNORE )?INTO (\w+)\s*\(([^)]*)\)", re.S)

#: One whole ORDER BY clause, to the LIMIT that ends it or the end of its string: entity walls
#: write one arm per line. A semicolon is NOT a terminator: two clauses carry one in a `--` comment.
_ORDER_BY = re.compile(r"ORDER BY(.*?)(\bLIMIT\b[^\n)]*|\"|\Z)", re.S)
#: `UPDATE OR IGNORE <table>` is an UPDATE too, as the rename statements are written.
_UPDATE = re.compile(r"UPDATE (?:OR \w+ )?(\w+) SET ([^\"']*?)(?:WHERE|RETURNING|\"|$)", re.S)


def _every_statement() -> list[tuple[Path, str, str, str]]:
    """`(file, kind, table, the columns it names)` for every write to a keyed table."""
    found = []
    for path in sorted(SOURCE.rglob("*.py")):
        # Shared fixtures (`src/sift/testing/`) count: every suite builds its world from them.
        if "tests" in path.parts:
            continue
        text = _JOINED.sub("", path.read_text(encoding="utf-8", errors="replace"))
        # A migration's `INSERT ... SELECT` runs before the key column exists, which fills it.
        text = re.sub(r"INSERT INTO \w+ \([^)]*\)\s*SELECT", "", text)
        for match in _INSERT.finditer(text):
            if match.group(1) in KEYED:
                found.append((path, "INSERT", match.group(1), match.group(2)))
        for match in _UPDATE.finditer(text):
            if match.group(1) in KEYED:
                found.append((path, "UPDATE", match.group(1), match.group(2)))
    return found


def test_every_write_of_a_name_writes_its_ordering_key() -> None:
    """The stored key is written by every statement that writes the name."""
    guilty = []
    for path, kind, table, columns in _every_statement():
        source, key = KEYED[table]
        writes_name = re.search(rf"(?<![\w.]){source}\b", columns) is not None
        writes_key = key in columns
        if writes_name and not writes_key:
            guilty.append(f"{path.relative_to(REPO)}  {kind} {table}  ({columns.strip()[:70]})")

    assert not guilty, (
        "these statements write a name and not the key it is ordered by, so the rows they write\n"
        "sort where they used to and nothing anywhere says so:\n\n  "
        + "\n  ".join(guilty)
        + "\n\nAdd the key column to the statement and `sort_key(<the name>)` to its parameters."
    )


#: Orderings deliberately left on `COLLATE NOCASE`, each with the reason: a ratchet that may only
#: shrink, so a new wall cannot be written the old way.
STILL_NOCASE = {
    # A folder PATH, ordered as the filesystem holds it rather than as a list of names reads.
    "src/sift/kernel/content/identity_derivatives.py": 1,
    # Owners with no sort-key column yet: `tunnels` and the faces slice's `pack_entries`.
    "src/sift/kernel/tunnels/store.py": 1,
    "src/sift/slices/faces/store_references.py": 2,
    # The Loops wall, on the Loops slice's table.
    "src/sift/kernel/access/repository/wall_loops.py": 2,
    # The download queue's two lists, on `downloads`, seen through `UPDATE OR IGNORE`.
}


def test_no_new_wall_is_ordered_the_old_way() -> None:
    """No new wall is ordered the old way: only orderings count, never a `LIMIT 1` tie-break or a
    `WHERE name = ? COLLATE NOCASE` match."""
    # A set of `(file, line)`, because an arm carrying ASC or DESC is found by both passes.
    found: set[tuple[str, int]] = set()

    def note(path: Path, text: str, line: str, at: int) -> None:
        if line.lstrip().startswith(("#", "*", '"""', "--")):
            return
        found.add((path.relative_to(REPO).as_posix(), at))

    for path in sorted(SOURCE.rglob("*.py")):
        # Both quote the old form in prose.
        if "tests" in path.parts or path.name == "sorting.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for match in _ORDER_BY.finditer(text):
            body, ends = match.group(1), match.group(2)
            # A `LIMIT 1` tie-break inside a scalar subquery.
            if ends.startswith("LIMIT 1"):
                continue
            # Only inside a SQL string.
            first = text[: match.start()].count("\n")
            opener = lines[first].lstrip()
            if opener.startswith(("#", "*", '"""', "--")):
                continue
            for offset, one in enumerate(body.splitlines()):
                if "COLLATE NOCASE" not in one:
                    continue
                note(path, text, one, first + offset)

        # And arms never under an `ORDER BY` (the Browse wall splices its sort arms in): `ASC` or
        # `DESC` on the line marks an ordering, and a comparison never carries one.
        for at, one in enumerate(lines):
            if "COLLATE NOCASE" not in one:
                continue
            if not re.search(r"COLLATE NOCASE\s+(ASC|DESC)\b", one):
                continue
            note(path, text, one, at)

    counts: dict[str, int] = {}
    for where, _at in found:
        counts[where] = counts.get(where, 0) + 1

    assert counts == STILL_NOCASE, (
        f"orderings still on COLLATE NOCASE: {counts}, recorded as {STILL_NOCASE}.\n\n"
        "Up: order by COALESCE(<table>.<key>, <table>.<name>) instead, and see `kernel.sorting`.\n"
        "Down: lower the entry. A ceiling above the truth makes the next few free."
    )
