# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a wall of things is cut at checked seams: its order, its page, its position, its counts."""

from __future__ import annotations

import re
from dataclasses import dataclass

#: What a wall of things may be ordered by, bound as a value, in the file grid's own words; the id
#: is a ULID minted in time order; `largest` counts files, `largest_total` bytes, `longest_total` time.
ENTITY_SORT_SEEN = "seen"
ENTITY_SORT_KEYS: frozenset[str] = frozenset(
    {
        ENTITY_SORT_SEEN,
        "favorite",
        "rating",
        "name_az",
        "name_za",
        "newest",
        "oldest",
        "edited",
        "largest",
        "smallest",
        "largest_total",
        "smallest_total",
        "longest_total",
        "shortest_total",
    }
)

#: The Music wall's own order: by the first artist each song credits, A to Z, nobody last.
SONG_SORT_ARTIST = "artist"
SONG_SORT_KEYS: frozenset[str] = ENTITY_SORT_KEYS | {SONG_SORT_ARTIST}


#: Whether a row with nothing under it belongs on the wall: on an admin's plain wall, so it can be
#: edited; on a filtered wall, or anybody else's, never. The caller binds it.
LIST_EMPTY_ROWS = "list_empty"


def _entity_sort(sort: str) -> str:
    """The chosen order, or the ordinary one for an order this does not know."""
    return sort if sort in ENTITY_SORT_KEYS else ENTITY_SORT_SEEN


# The page, a row's position and the facet counts are one statement CUT, so a facet row returns the
# number it shows. Each marker occurs once; the alias is read out of the statement.
_SELECT_AT = "\nSELECT "
_FROM_AT = "\n  FROM "
_ORDER_AT = "\n -- The wall's chosen order"
_PAGE_AT = "\n LIMIT :limit OFFSET :offset\n"


@dataclass(frozen=True, slots=True)
class _Wall:
    """One entity statement in the pieces its readers put back together."""

    ctes: str
    columns: str
    source: str
    body: str
    order: str
    tail: str

    @property
    def from_at(self) -> str:
        """The FROM line, ready to have a facet's own joins written after it."""
        return _FROM_AT + self.source

    @property
    def alias(self) -> str:
        """What the final SELECT calls the thing the wall is a wall of."""
        return self.source.split()[1]


def _wall(statement: str, name: str) -> _Wall:
    """One statement cut at its four seams, each occurring once, proved by putting it back."""
    for marker in (_SELECT_AT, _FROM_AT, _ORDER_AT, _PAGE_AT):
        found = statement.count(marker)
        if found != 1:  # pragma: no cover (reaching this means a statement was edited)
            raise RuntimeError(f"{name} has {found} of {marker!r}, expected exactly one")
    ctes, rest = statement.split(_SELECT_AT, 1)
    columns, rest = rest.split(_FROM_AT, 1)
    source, rest = rest.split("\n", 1)
    body, rest = ("\n" + rest).split(_ORDER_AT, 1)
    order, tail = rest.split(_PAGE_AT, 1)
    cut = _Wall(ctes=ctes, columns=columns, source=source, body=body, order=order, tail=tail)
    if (
        cut.ctes
        + _SELECT_AT
        + cut.columns
        + cut.from_at
        + cut.body
        + _ORDER_AT
        + cut.order
        + _PAGE_AT
        + cut.tail
    ) != statement:  # pragma: no cover (reaching this means the statement moved under a seam)
        raise RuntimeError(f"{name} no longer puts itself back together from its seams")
    return cut


def _position(wall: _Wall) -> str:
    """One wall reprojected to each row's place in the page's order; a concealed row has none."""
    return (
        wall.ctes
        + "\nSELECT position FROM ("
        + f"\nSELECT {wall.alias}.id AS ranked_id,"
        + "\n       ROW_NUMBER() OVER ("
        + _ORDER_AT
        + wall.order
        + "\n       ) AS position"
        + wall.from_at
        + wall.body
        + "\n)\n WHERE ranked_id = :position_of\n"
    )


#: A row only a file in the vault puts on a wall is a LOCKED TILE: every file under it this viewer
#: may see is held back (`viewer_entity_counts`), the vault is shut and placeholders are on. It
#: stays, since every card counts it, with its name and identifying columns withheld. `:by_id`
#: keeps the name on a read by id. Probed only with the vault shut: an AND stops at a false term.
_LOCKED_TILE = (
    "(:by_id = 0 AND :reveal = 1 AND :reveal_named = 0"
    " AND EXISTS (SELECT 1 FROM viewer_entity_counts lk"
    " WHERE lk.user_id = :viewer AND lk.kind = '{kind}' AND lk.object_id = {row}.id"
    " AND lk.permitted > 0 AND lk.concealed = lk.permitted))"
)


def locked_tile(kind: str, row: str) -> str:
    """The locked-tile rule for one wall: `kind` as the stored counts name it, `row` its alias."""
    return _LOCKED_TILE.format(kind=kind, row=row)


#: Anybody but an admin is matched only on what they may be shown, so hidden names cost nothing:
#: the walls test it inside a CASE, so no planner tries the name first.
_SHOWN = (
    "(:is_admin = 1 OR {row}.id IN (SELECT nm.object_id FROM viewer_entity_counts nm"
    " WHERE nm.user_id = :viewer AND nm.kind = '{kind}' AND nm.permitted > 0))"
)


def shown(kind: str, row: str) -> str:
    """Where a typed name is tried: `kind` as the stored counts name it, `row` its alias."""
    return _SHOWN.format(kind=kind, row=row)


# The filter seam narrows a wall to a set of files after every rule: it cannot widen it.
_FILTER_AT = "\n     -- The wall's own narrowing is spliced in here. See `_filtered`.\n"


def _cut(statement: str, name: str) -> tuple[str, str]:
    """A statement split at its one filter seam; a second seam fails the import."""
    found = statement.count(_FILTER_AT)
    if found != 1:  # pragma: no cover (reaching this means a statement was edited across the cut)
        raise RuntimeError(f"{name} has {found} filter seams, expected exactly one")
    head, rest = statement.split(_FILTER_AT, 1)
    return head, rest


#: What `AssetFilter.predicate()` writes for a wall that filters nothing, compared whitespace-folded.
_NOTHING = "1"

#: An unfiltered wall's counts, off `viewer_entity_counts`. A filtered wall counts live, since no
#: stored number knows a filter on files; unfiltered the two agree.
_STORED_COUNTS = """counted({key}, {col}, {size}, {length}) AS (
  SELECT c.object_id, c.permitted - CASE WHEN :reveal = 1 THEN 0 ELSE c.concealed END,
         c.permitted_bytes - CASE WHEN :reveal_named = 1 THEN 0 ELSE c.concealed_bytes END,
         c.permitted_ms - CASE WHEN :reveal_named = 1 THEN 0 ELSE c.concealed_ms END
    FROM viewer_entity_counts c
   WHERE c.user_id = :viewer AND c.kind = '{kind}'
),
whole({key}, {col}, {size}, {length}) AS (
  SELECT {key}, {col}, {size}, {length} FROM counted
)
"""

_COUNT_BLOCK = re.compile(
    r"^counted\((\w+), (\w+), (\w+), (\w+)\) AS \(\n.*?^\),\n(?:^--[^\n]*\n)*^whole\(\1, \2, \3, \4\) AS \(\n.*?^\)\n",
    re.M | re.S,
)

#: Which stored kind each wall counts; a Site's walks `lineage` as its statement does.
_COUNTED_KIND = {
    "_VISIBLE_TAGS": "tag",
    "_VISIBLE_PEOPLE": "person",
    "_VISIBLE_COLLECTIONS": "collection",
    "_VISIBLE_PHOTO_SETS": "photo_set",
    "_VISIBLE_SONGS": "song",
    "_VISIBLE_USERNAMES": "username",
    "_VISIBLE_SITES": "site",
}


def _with_stored_counts(statement: str, name: str) -> str:
    """The same wall with its two count CTEs read off the stored counts."""
    kind = _COUNTED_KIND[name]
    found = _COUNT_BLOCK.search(statement)
    if found is None:  # pragma: no cover (an edit across the seam)
        raise RuntimeError(f"{name} no longer carries its two count CTEs where the seam expects")
    key, col, size, length = found.group(1), found.group(2), found.group(3), found.group(4)
    stored = _STORED_COUNTS.format(key=key, col=col, size=size, length=length, kind=kind)
    return statement.replace(found.group(0), stored, 1)


def _filtered(head: str, rest: str, where: str) -> str:
    """The two halves with a filter between them in a SECOND pair of parentheses, on a
    `WHERE 1 = 1`: the line between what was asked for and what may be shown is held twice."""
    return head + "\n     AND (" + where + ")\n" + rest


#: The one line a ROW filter replaces: it filters the THINGS, where the filter seam filters their
#: files. One conjunct of fixed text, every value bound; a comment after `AND 1 = 1`.
_ROW_AT = "\n   -- The wall's own ROW narrowing is spliced in here. See `_row_narrowed`.\n"


def _row_narrowed(statement: str, rows: str) -> str:
    """The statement filtered to the rows asked for, or untouched when nothing filters."""
    if not _narrowed(rows):
        return statement
    found = statement.count(_ROW_AT)
    if found != 1:  # pragma: no cover (reaching this means a statement was edited across the cut)
        raise RuntimeError(f"a wall has {found} row seams, expected exactly one")
    return statement.replace(_ROW_AT, "\n   AND (" + rows + ")\n", 1)


def _one_seam(statement: str, name: str, seam: str, replacement: str, *, expected: int = 1) -> str:
    """`statement` with each copy of `seam` replaced, after checking there are `expected` of them."""
    found = statement.count(seam)
    if found != expected:  # pragma: no cover (reaching this means a statement was edited across it)
        raise RuntimeError(f"{name} has {found} copies of a by-id seam, expected {expected}")
    return statement.replace(seam, replacement)


def _narrowed(where: str) -> bool:
    """Whether a filter filters anything. The empty filter is the one word `1`."""
    return " ".join(where.split()) != _NOTHING
