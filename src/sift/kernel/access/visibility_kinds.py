# SPDX-License-Identifier: AGPL-3.0-or-later
"""The kinds of thing whose files are counted per user, and the pairs of them a card reads."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sift.kernel.access import visibility_panel
from sift.kernel.access.sites import SITE_REACH
from sift.kernel.access.visibility_tables import (
    _BYTES_NOT_SUMMED,
    _BYTES_SUMMED,
    _EVERY_ROW,
    _FILES_COUNTED,
    _MS_SUMMED,
    _ROWS_COUNTED,
    _STAGED_ROWS,
    SCOPE,
    _filled,
)

# Copies, not files: a file with two copies under a folder occupies it twice. Missing copies do not
# count.
_COPIES_UNDER_FOLDERS = (
    "(SELECT l.asset_id, an.ancestor_id AS folder_id"
    "   FROM asset_locations l JOIN folder_ancestry an ON an.folder_id = l.folder_id"
    "  WHERE l.status = 'present')"
)


@dataclass(frozen=True)
class Counted:
    """One kind of thing whose files are counted per user; `keys` guard its BEFORE triggers."""

    kind: str
    source: str
    column: str
    table: str
    update: str = "UPDATE"
    keys: tuple[tuple[str, ...], ...] = ()
    #: Count files, not rows: a Site's source can reach one file twice.
    distinct: bool = False
    #: Sum the size and running time too, for a kind that counts files.
    sized: bool = False


def _members(one: Counted, rows: str, tail: str = "") -> str:
    """One kind's memberships joined to `rows` (verdict rows aliased `v`), `tail` after them."""
    if not one.sized:
        template = _JOINED
    elif not one.distinct:
        template = _JOINED_SIZED
    else:
        template = _JOINED_DISTINCT_SIZED
    return _filled(template, ROWS=rows, TABLE=one.source, COLUMN=one.column, TAIL=tail)


#: The CROSS JOIN keeps the planner from scanning every file to reach a handful of rows.
_JOINED = "<<ROWS>> JOIN <<TABLE>> m ON m.asset_id = v.asset_id<<TAIL>>"
_JOINED_SIZED = (
    "<<ROWS>> JOIN <<TABLE>> m ON m.asset_id = v.asset_id"
    " JOIN assets a ON a.id = v.asset_id<<TAIL>>"
)
_JOINED_DISTINCT_SIZED = (
    "(SELECT DISTINCT v.user_id, v.asset_id, v.concealed, m.<<COLUMN>>"
    " FROM <<ROWS>> JOIN <<TABLE>> m ON m.asset_id = v.asset_id<<TAIL>>)"
    " v CROSS JOIN assets a ON a.id = v.asset_id"
)


def _member_of(one: Counted) -> str:
    """The thing a membership row names, as the templates over `_members` read it."""
    return ("v." if one.sized and one.distinct else "m.") + one.column


#: What a Site reaches, as memberships, over the one walk every site rule reads. `noqa: S608`:
#: module constants only.
_SITE_REACHED = (
    "(SELECT aa.asset_id, reach.ancestor_id AS site_id FROM asset_usernames aa"  # noqa: S608
    " JOIN usernames ac ON ac.id = aa.username_id"
    " JOIN (" + SITE_REACH + ") reach ON reach.site_id = ac.site_id)"
)


#: The kinds the kernel itself counts, a Site last.
_KERNEL_COUNTED: tuple[Counted, ...] = (
    Counted(
        "tag", "asset_tags", "tag_id", "asset_tags", keys=(("asset_id", "tag_id"),), sized=True
    ),
    Counted(
        "person",
        "asset_people",
        "person_id",
        "asset_people",
        keys=(("asset_id", "person_id"),),
        sized=True,
    ),
    Counted(
        "collection",
        "collection_items",
        "collection_id",
        "collection_items",
        keys=(("collection_id", "asset_id"),),
        sized=True,
    ),
    Counted(
        "photo_set",
        "photo_set_items",
        "photo_set_id",
        "photo_set_items",
        keys=(("photo_set_id", "asset_id"),),
        sized=True,
    ),
    # A file carries at most one song, so the file is the table's key.
    Counted(
        "song",
        "song_files",
        "song_id",
        "song_files",
        keys=(("asset_id",),),
        sized=True,
    ),
    Counted(
        "username",
        "asset_usernames",
        "username_id",
        "asset_usernames",
        keys=(("asset_id", "username_id"),),
        sized=True,
    ),
    # A loop is a membership of exactly one file, so its pairs give a card its mark counts.
    Counted("loop", "loops", "id", "loops", "UPDATE OF id, asset_id", keys=(("id",),)),
    Counted(
        "folder",
        _COPIES_UNDER_FOLDERS,
        "folder_id",
        "asset_locations",
        "UPDATE OF asset_id, root_id, folder_id, status",
        keys=(("id",), ("root_id", "rel_path")),
        sized=True,
    ),
    *visibility_panel.kinds(),
    Counted(
        "site",
        _SITE_REACHED,
        "site_id",
        "asset_usernames",
        keys=(("asset_id", "username_id"),),
        distinct=True,
        sized=True,
    ),
)

#: The pairs `viewer_pair_counts` keeps: exactly the tabs an entity card draws (`tabsFor` in the
#: client).
PAIRED: tuple[tuple[str, str], ...] = (
    ("person", "photo_set"),
    ("person", "tag"),
    ("person", "collection"),
    ("person", "username"),
    ("tag", "photo_set"),
    ("tag", "collection"),
    ("tag", "username"),
    ("collection", "username"),
    ("photo_set", "username"),
    ("person", "song"),
    ("tag", "song"),
    ("song", "username"),
    ("collection", "song"),
    ("song", "loop"),
    ("person", "loop"),
    ("tag", "loop"),
    ("collection", "loop"),
    ("username", "loop"),
)

#: The kinds the v6 and v8 steps added.
_LOOP_KIND = "loop"

_SITE_KIND = "site"

#: Counted by the faces slice; named here because the wall listing the groups is the kernel's.
WAITING_FACES_KIND = "pile"

_counted: list[Counted] = list(_KERNEL_COUNTED)


def counted() -> tuple[Counted, ...]:
    """Every counted kind: the kernel's own, and what the slices have registered."""
    return tuple(_counted)


def _each_pair(template: str, pairs: Sequence[tuple[Counted, Counted]]) -> list[str]:
    return [
        _filled(
            template,
            KIND_A=a.kind,
            COLUMN_A=a.column,
            TABLE_A=a.source,
            KIND_B=b.kind,
            COLUMN_B=b.column,
            TABLE_B=b.source,
        )
        for a, b in pairs
    ]


def _each_kind(
    template: str, kinds: Sequence[Counted], rows: str = _STAGED_ROWS, tail: str = ""
) -> list[str]:
    """The template once per kind, its memberships taken over `rows` with `tail` after them."""
    return [
        _filled(
            template,
            MEMBERS=_members(one, rows, tail),
            OBJECT=_member_of(one),
            KIND=one.kind,
            COLUMN=one.column,
            TABLE=one.source,
            FILES=(_FILES_COUNTED if one.distinct else _ROWS_COUNTED)[0],
            CONCEALED=(_FILES_COUNTED if one.distinct else _ROWS_COUNTED)[1],
            BYTES=(_BYTES_SUMMED if one.sized else _BYTES_NOT_SUMMED)[0],
            CONCEALED_BYTES=(_BYTES_SUMMED if one.sized else _BYTES_NOT_SUMMED)[1],
            MS=(_MS_SUMMED if one.sized else _BYTES_NOT_SUMMED)[0],
            CONCEALED_MS=(_MS_SUMMED if one.sized else _BYTES_NOT_SUMMED)[1],
        )
        for one in kinds
    ]


def _every_kind_joined(template: str, kinds: Sequence[Counted]) -> str:
    """The template for each counted kind over every verdict row, as one compound SELECT."""
    return " UNION ALL ".join(_each_kind(template, kinds, _EVERY_ROW, SCOPE))
