# SPDX-License-Identifier: AGPL-3.0-or-later
"""A recompute around a change that moves no membership and no size (a grant, a hide) settles
first: its pairs are decided into scratch, the ones whose answer stands are let go, and only the
rest are taken away and given back, so its work falls to the files whose answer moves.

`visibility` calls in here while it loads, so its names are imported where they are used.
"""

from __future__ import annotations

from collections.abc import Sequence

#: Scratch: the staged pairs' new answers, filled by the settle step and read by the give after it.
CREATE = """
CREATE TABLE IF NOT EXISTS visibility_decided (
  user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  asset_id  TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  concealed INTEGER NOT NULL,
  PRIMARY KEY (user_id, asset_id)
) WITHOUT ROWID
"""

_CLEAR = "DELETE FROM visibility_decided"

# A pair whose new answer is its stored one (both absent included) moves no count: let it go.
_LET_GO = (
    "DELETE FROM visibility_pending"
    " WHERE (SELECT d.concealed FROM visibility_decided d"
    "         WHERE d.user_id = visibility_pending.user_id"
    "           AND d.asset_id = visibility_pending.asset_id)"
    "    IS (SELECT v.concealed FROM viewer_assets v"
    "         WHERE v.user_id = visibility_pending.user_id"
    "           AND v.asset_id = visibility_pending.asset_id)"
)

_INSERT_DECIDED = (
    "INSERT INTO viewer_assets (user_id, asset_id, concealed)"
    " SELECT d.user_id, d.asset_id, d.concealed"
    " FROM visibility_pending s CROSS JOIN visibility_decided d"
    " ON d.user_id = s.user_id AND d.asset_id = s.asset_id"
)

#: The two steps a settled recompute calls around `take`.
SETTLE, SETTLED = "settle", "settled"


def steps(given: Sequence[str]) -> list[tuple[str, list[str]]]:
    """The settle step, and the give that reads its answers rather than deciding them again."""
    from sift.kernel.access import visibility as v

    decide = "INSERT INTO visibility_decided (user_id, asset_id, concealed)" + v._filled(
        v._VERDICT_ROWS, PAIRS=v._STAGED
    )
    settle = [_CLEAR, v._CLEAR_PLACES, v._FILL_STAGED_PLACES, decide, v._CLEAR_PLACES, _LET_GO]
    placed = (v._CLEAR_PLACES, v._FILL_STAGED_PLACES)
    settled = [_INSERT_DECIDED if one == v._INSERT_STAGED else one for one in given]
    return [(SETTLE, settle), (SETTLED, [one for one in settled if one not in placed] + [_CLEAR])]
