# SPDX-License-Identifier: AGPL-3.0-or-later
"""The filter panel's stored counts: its kinds, the triggers that keep them, the version 16 step.

`visibility` calls in here while it loads, so its names are imported where they are used.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from sift.kernel.access.visibility import Counted, _Recompute
    from sift.kernel.db import Connection

#: The counted kinds the v16 step added, sides of no pair.
PANEL_KINDS = (
    "media",
    "place",
    "has_tags",
    "has_people",
    "has_sites",
    "has_collections",
    "has_photo_sets",
    "has_songs",
)

_HAS: tuple[tuple[str, str, tuple[tuple[str, ...], ...]], ...] = (
    ("tags", "asset_tags", (("asset_id", "tag_id"),)),
    ("people", "asset_people", (("asset_id", "person_id"),)),
    ("sites", "asset_usernames", (("asset_id", "username_id"),)),
    ("collections", "collection_items", (("collection_id", "asset_id"),)),
    ("photo_sets", "photo_set_items", (("photo_set_id", "asset_id"),)),
    ("songs", "song_files", (("asset_id",),)),
)

_EVERY_USER_IN_FOLDER = (
    "SELECT DISTINCT u.id AS user_id, l.asset_id FROM users u, asset_locations l"
    " WHERE l.folder_id = {folder}"
)

#: The keys of `folders`, for the guard on its rename's BEFORE half.
_FOLDER_KEYS: tuple[tuple[str, ...], ...] = (("id",), ("root_id", "rel_path"))

_EMPTIED_KEYS = {
    "viewer_entity_counts": ("user_id", "kind", "object_id"),
    "viewer_pair_counts": ("user_id", "kind_a", "id_a", "kind_b", "id_b"),
}
_EMPTIED = "DELETE FROM <<TABLE>> WHERE <<SAME>>"

#: A BEFORE and AFTER pair as `_triggers` makes one: stem, event, table, halves, guard.
Pair = tuple[str, str, str, Sequence[str], Sequence[str], str]


class Panel(NamedTuple):
    renamed: Pair
    sized: Pair
    emptied: list[tuple[str, str, str]]
    given: tuple[str, ...]


def kinds() -> tuple[Counted, ...]:
    """The panel's kinds, in the order the kernel's list carries them."""
    from sift.kernel.access.visibility import Counted

    return (
        # A file's kind (video, image, gif), off its own row; moved by `vis_assets_size`.
        Counted(
            "media",
            "(SELECT ma.id AS asset_id, ma.media_type FROM assets ma)",
            "media_type",
            "assets",
            "UPDATE OF media_type",
            keys=(("id",), ("identity",)),
            sized=True,
        ),
        # The name of the folder a copy sits in itself, the file once, as `in:` reads a name.
        Counted(
            "place",
            "(SELECT pl.asset_id, pf.name AS folder_name FROM asset_locations pl"
            " JOIN folders pf ON pf.id = pl.folder_id)",
            "folder_name",
            "asset_locations",
            "UPDATE OF asset_id, root_id, folder_id, status",
            keys=(("id",), ("root_id", "rel_path")),
            distinct=True,
        ),
        # Whether a file carries any of a dimension at all, for the Has and No rows.
        *(
            Counted(
                "has_" + dimension,
                "(SELECT hm.asset_id, 'any' AS present FROM " + table + " hm)",  # noqa: S608
                "present",
                table,
                keys=keys,
                distinct=True,
            )
            for dimension, table, keys in _HAS
        ),
    )


def triggers(halves: _Recompute, own: Counted | None) -> Panel:
    """The panel's triggers and the shared give step without the version 13 sweeps.

    `own` is the watcher of `assets`, whose columns a file's own update moves with its size."""
    from sift.kernel.access import visibility as v

    renamed: Pair = (
        "vis_folders_name",
        "UPDATE OF name",
        "folders",
        *halves.split(_EVERY_USER_IN_FOLDER.format(folder="NEW.id")),
        v._changed(["name"])
        + " AND "
        + v._not_already_held("folders", _FOLDER_KEYS, updating=True),
    )
    columns = [
        "size_bytes",
        "duration_ms",
        *([] if own is None else v._update_columns(own.update) or []),
    ]
    sized: Pair = (
        "vis_assets_size",
        "UPDATE OF " + ", ".join(columns),
        "assets",
        *halves.split(v._EVERY_USER_ONE_FILE.format(asset="NEW.id")),
        v._changed(columns),
    )
    # A count row that reaches nought goes at once by its own key; the sweeps read every row.
    emptied = []
    for table, key in _EMPTIED_KEYS.items():
        name = "vis_" + table + "_emptied"
        same = " AND ".join(column + " = NEW." + column for column in key)
        body = [v._filled(_EMPTIED, TABLE=table, SAME=same)]
        ddl = v._trigger(name, "UPDATE OF permitted", table, body, when="NEW.permitted <= 0")
        emptied.append((name, table, ddl))
    sweeps = (v._COUNTS_EMPTIED, v._PAIRS_EMPTIED)
    given = tuple(one for one in halves.given if one not in sweeps)
    return Panel(renamed, sized, emptied, given)


async def count_the_panel(connection: Connection) -> None:
    """The version 16 step, safe to run again: the Filter panel's counts made from the stored rows,
    and the triggers rewritten to keep them."""
    from sift.kernel.access import visibility as v

    by_kind = {one.kind: one for one in v.counted()}
    panel = [by_kind[kind] for kind in PANEL_KINDS]
    rows = v._every_kind_joined(v._ENTITY_COUNT_ROWS_ONE, panel)
    await v._drop_triggers(connection)
    for one in panel:
        await connection.execute(v._filled(v._DROP_KIND_COUNTS, KIND=one.kind))
    await connection.execute(v._filled(v._filled(v._FILL_ENTITY_COUNTS, ROWS=rows), SCOPE=""))
    await v._create_triggers(connection)
    v.log.info("visibility.panel_counted")
