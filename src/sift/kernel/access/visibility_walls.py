# SPDX-License-Identifier: AGPL-3.0-or-later
"""The walls' stored totals: how many things of each kind a user may see, the triggers on the
stored counts that keep them, and the version 17 step.

`visibility` calls in here while it loads, so its names are imported where they are used.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sift.kernel.db import Connection

#: Per user and kind: how many things have a file this user may see (`permitted`), and how many
#: have one with the vault shut (`shown`). A plain wall's total is this row, not a walk of the wall.
CREATE = """
CREATE TABLE IF NOT EXISTS viewer_wall_totals (
  user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind      TEXT NOT NULL,
  permitted INTEGER NOT NULL DEFAULT 0,
  shown     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, kind)
) WITHOUT ROWID
"""

# Moved by one count row crossing nought on either measure, as the partner totals are moved by a
# pair; made only when something is added and gone at nought, so a cascade cannot leave one behind.
_ENSURED = (
    "INSERT INTO viewer_wall_totals (user_id, kind)"
    " SELECT <<ROW>>.user_id, <<ROW>>.kind WHERE (<<DP>> > 0 OR <<DS>> > 0)"
    " AND NOT EXISTS (SELECT 1 FROM viewer_wall_totals"
    " WHERE user_id = <<ROW>>.user_id AND kind = <<ROW>>.kind)"
)
_MOVED = (
    "UPDATE viewer_wall_totals SET permitted = permitted + <<DP>>, shown = shown + <<DS>>"
    " WHERE user_id = <<ROW>>.user_id AND kind = <<ROW>>.kind"
)
_EMPTIED = (
    "DELETE FROM viewer_wall_totals"
    " WHERE user_id = <<ROW>>.user_id AND kind = <<ROW>>.kind AND permitted <= 0"
)
_ALIVE = "(<<ROW>>.permitted > 0)"
_SHOWN = "(<<ROW>>.permitted - <<ROW>>.concealed > 0)"

_ROWS = (
    "SELECT user_id, kind, SUM(permitted > 0) AS permitted,"
    " SUM(permitted - concealed > 0) AS shown"
    " FROM viewer_entity_counts WHERE permitted > 0 GROUP BY user_id, kind"
)
CLEAR = "DELETE FROM viewer_wall_totals"
FILL = "INSERT INTO viewer_wall_totals (user_id, kind, permitted, shown) " + _ROWS

#: The stored totals against what the stored counts say; the counts are held to the facts beside it.
DIFFERENCES = (
    "SELECT 'wall total missing' AS what, user_id || '/' || kind,"  # noqa: S608
    " permitted || '/' || shown, permitted FROM ("
    "SELECT user_id, kind, permitted, shown FROM (" + _ROWS + ")"
    " EXCEPT SELECT user_id, kind, permitted, shown FROM viewer_wall_totals)"
    " UNION ALL SELECT 'wall total extra', user_id || '/' || kind,"
    " permitted || '/' || shown, permitted FROM ("
    "SELECT user_id, kind, permitted, shown FROM viewer_wall_totals"
    " EXCEPT SELECT user_id, kind, permitted, shown FROM (" + _ROWS + "))"
)


def _one(event: str, dp: str, ds: str, row: str) -> tuple[str, str, str]:
    from sift.kernel.access import visibility as v

    name = "vis_wall_totals_" + event.split()[0].lower()
    body = [v._filled(one, ROW=row, DP=dp, DS=ds) for one in (_ENSURED, _MOVED, _EMPTIED)]
    when = dp + " != 0 OR " + ds + " != 0"
    return (
        name,
        "viewer_entity_counts",
        v._trigger(name, event, "viewer_entity_counts", body, when=when),
    )


def triggers() -> list[tuple[str, str, str]]:
    """The three triggers on the stored counts, as (name, table, DDL)."""
    from sift.kernel.access import visibility as v

    alive_new, shown_new = v._filled(_ALIVE, ROW="NEW"), v._filled(_SHOWN, ROW="NEW")
    alive_old, shown_old = v._filled(_ALIVE, ROW="OLD"), v._filled(_SHOWN, ROW="OLD")
    return [
        _one("INSERT", alive_new, shown_new, "NEW"),
        _one("DELETE", "-" + alive_old, "-" + shown_old, "OLD"),
        _one(
            "UPDATE OF permitted, concealed",
            "(" + alive_new + " - " + alive_old + ")",
            "(" + shown_new + " - " + shown_old + ")",
            "NEW",
        ),
    ]


async def fill(connection: Connection) -> None:
    """Every total from the stored counts, with the triggers out of the way."""
    await connection.execute(CLEAR)
    # Two constants joined at import: no value reaches the text.
    await connection.execute(FILL)  # nosemgrep: sift-no-string-built-sql


async def total_the_walls(connection: Connection) -> None:
    """The version 17 step, safe to run again: the totals made from the stored counts, the settle
    step's scratch made, and the triggers rewritten to keep and call them."""
    from sift.kernel.access import visibility as v
    from sift.kernel.access import visibility_settled

    for table in (CREATE, visibility_settled.CREATE):
        await connection.execute(table)
    await v._drop_triggers(connection)
    await fill(connection)
    await v._create_triggers(connection)
    v.log.info("visibility.walls_totalled")
