# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a recompute writes: a change that moves no membership and no size (a grant, a hide)
settles first, deciding its pairs into scratch and letting go the ones whose answer stands, so its
work falls to the files whose answer moves; and the counts of every file a write touches move once,
when the write ends, not once per row.

`visibility` calls in here while it loads, so its names are imported where they are used.
"""

from __future__ import annotations

import functools
import json
import re
import weakref
from collections.abc import Sequence
from typing import TYPE_CHECKING

from sift.kernel.audience import Audience
from sift.kernel.db import Connection, Row, before_commit
from sift.kernel.log import get_logger

if TYPE_CHECKING:
    from sift.kernel.access.visibility import Counted, _Recompute

log = get_logger("sift.kernel.access.visibility")

#: Where a trigger hands a recompute over: a view per step that no row ever sits in, with one
#: INSTEAD OF trigger. A trigger body cannot call a procedure, but it can write to a view, and the
#: view's trigger runs in the same transaction with the same conflict handling, so writing one row
#: there runs the step's statements exactly as if they were written in place. A view per step, not
#: one view for all: a write to a view enters every trigger on it.
#:
#: That keeps the schema small: every half reads only the staged pairs, so in place they were the
#: same text in every trigger, and every connection parses the whole schema; one copy of each step
#: is the same rules parsed once.
RECOMPUTE = "visibility_step_"

_CREATE_STEP = "CREATE VIEW IF NOT EXISTS visibility_step_<<STEP>> (user_id) AS SELECT NULL WHERE 0"

#: The single view every step was called through before version 18.
_DROP_ONE_VIEW = "DROP VIEW IF EXISTS visibility_recompute"

#: A call of one step. `touch` and `rows` work on the staged pairs; `user` re-decides one user.
RUN = "INSERT INTO visibility_step_<<STEP>> (user_id) VALUES (NULL)"
RUN_USER = "INSERT INTO visibility_step_user (user_id) VALUES (<<USER>>)"

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


def steps() -> list[tuple[str, list[str]]]:
    """The settle step, and the rows written from its answers rather than decided again; their
    counts are moved by the fold."""
    from sift.kernel.access import visibility as v

    decide = "INSERT INTO visibility_decided (user_id, asset_id, concealed)" + v._filled(
        v._VERDICT_ROWS, PAIRS=v._STAGED
    )
    settle = [_CLEAR, v._CLEAR_PLACES, v._FILL_STAGED_PLACES, decide, v._CLEAR_PLACES, _LET_GO]
    settled = [v._DELETE_STAGED, _INSERT_DECIDED, _CLEAR, v._CLEAR_PENDING]
    return [(SETTLE, settle), (SETTLED, settled)]


# --- the counts, moved once per write ------------------------------------------------------------
#
# A row trigger records the pairs it reaches (`touch`): each pair's answer as the counts hold it,
# and, the first time a file is reached, what the file is a member of, per counted kind. The rows
# are re-decided where the change lands, so the write reads its own answers. The counts move once,
# before the write commits (`fold_touched`), by the difference between those members and answers
# and the ones the file now has: three statements for the whole write, in place of a recompute per
# row. A large share marks its pairs owed instead, and they are folded after its press answers.

#: The answer each touched pair's counts still hold (NULL: no row), and whether it is owed past
#: this write. Every pair of one file carries the same `owed`, so a fold always takes a file whole.
CREATE_OWED = """
CREATE TABLE IF NOT EXISTS visibility_owed (
  asset_id  TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  concealed INTEGER,
  owed      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (asset_id, user_id)
) WITHOUT ROWID
"""
CREATE_OWED_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_visibility_owed_now ON visibility_owed(asset_id) WHERE owed = 0"
)

#: What a touched file is a member of, per counted kind (`kind` '' is the file itself, for the
#: totals): side 0 as the counts hold it, side 1 as it now stands, made by the fold.
CREATE_MEMBERS = """
CREATE TABLE IF NOT EXISTS visibility_members (
  asset_id  TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  side      INTEGER NOT NULL,
  kind      TEXT NOT NULL,
  object_id TEXT NOT NULL,
  n         INTEGER NOT NULL,
  bytes     INTEGER NOT NULL,
  ms        INTEGER NOT NULL,
  PRIMARY KEY (asset_id, side, kind, object_id)
) WITHOUT ROWID
"""

#: Scratch: each pair of the files being folded whose answer moved, and how far (`_ANSWER_MOVED`),
#: worked out once for every statement of the fold and emptied by it. The file's size and running
#: time are kept beside it, so no statement of the fold reads the file's row again. No foreign keys:
#: nothing outlives the fold, and without them a row is written unchecked and the table emptied whole.
CREATE_MOVED = """
CREATE TABLE IF NOT EXISTS visibility_moved (
  asset_id TEXT NOT NULL,
  user_id  TEXT NOT NULL,
  dn       INTEGER NOT NULL,
  dc       INTEGER NOT NULL,
  read     INTEGER NOT NULL,
  size     INTEGER NOT NULL,
  time     INTEGER NOT NULL,
  PRIMARY KEY (read, asset_id, user_id)
) WITHOUT ROWID
"""

#: The version 18 shape of `visibility_moved`, scratch emptied by every fold: dropped for the new.
DROP_MOVED = "DROP TABLE IF EXISTS visibility_moved"

#: A widening too large to decide while its press waits (`DEFER_FROM` pairs or more): its pairs
#: are filed after it, a page at a time from `after` on, so a guest sees less than was granted until
#: the last page and never more. A narrowing is never filed later.
CREATE_FILING = """
CREATE TABLE IF NOT EXISTS visibility_filing (
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  object_type TEXT NOT NULL,
  object_id   TEXT NOT NULL,
  after       TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (user_id, object_type, object_id)
) WITHOUT ROWID
"""

#: A grant row that can only show a user more (a share made, a restrict taken away): the one kind
#: filed after its press. A narrowing is decided in its own write, whatever its size.
WIDENING = {"INSERT": "NEW.effect = 'share'", "DELETE": "OLD.effect = 'restrict'"}


def widening(event: str, tail: str) -> str | None:
    """The widening test for a grant trigger on `event`; none for an update's or its `_was` half."""
    return None if tail else WIDENING.get(event)


#: Set by a caller that files what it defers (the sharing press): any other write decides in place.
CREATE_MAY_DEFER = "CREATE TABLE IF NOT EXISTS visibility_may_defer (id INTEGER PRIMARY KEY)"
MAY_DEFER = "INSERT OR IGNORE INTO visibility_may_defer (id) VALUES (1)"
NO_DEFER = "DELETE FROM visibility_may_defer"

TABLES = (
    CREATE_OWED,
    CREATE_OWED_INDEX,
    CREATE_MEMBERS,
    CREATE_MOVED,
    CREATE_FILING,
    CREATE_MAY_DEFER,
)

TOUCH, ANSWERS, ROWS, FOLD, OWE, GOING = "touch", "answers", "rows", "fold", "owe", "going"
TOUCH_CALL, ROWS_CALL = (RUN.replace("<<STEP>>", step) for step in (TOUCH, ROWS))

#: The fold of a page of owed files, after a large share: the same fold under its own name, so a
#: page (bounded by its size) and a write (bounded by what it touched) are each priced as they are.
FOLD_PAGE = "fold_page"

#: From this many settled pairs a grant's counts are owed past its press: about 1,240 steps a pair
#: at one million files, so at most about 100 ms counted while the press waits.
OWED_FROM = 1_000

ASSETS = "<<ASSETS>>"

#: The files a fold takes: every file touched in this write, or the staged ones (a file going).
_TOUCHED = "SELECT asset_id FROM visibility_owed WHERE owed = 0"
_STAGED_FILES = "SELECT asset_id FROM visibility_pending"

# The staged files whose members are read: a file already gone (a membership going after it, by
# cascade) was folded as it went.
_STAGED_THERE = (
    "SELECT DISTINCT s.asset_id FROM visibility_pending s JOIN assets a ON a.id = s.asset_id"
)

# Members left by a fold that a user going or a user rebuilt emptied of pairs.
_STALE_MEMBERS = (
    "DELETE FROM visibility_members WHERE asset_id IN (SELECT asset_id FROM visibility_pending)"
    " AND NOT EXISTS (SELECT 1 FROM visibility_owed o WHERE o.asset_id = visibility_members.asset_id)"
)

# Each pair of the named files whose answer is not the one the counts hold, and how far it moved:
# present (`dn`) and held back (`dc`), each -1, 0 or 1, and the size and time each moves.
_FILL_MOVED = (
    "INSERT INTO visibility_moved (asset_id, user_id, dn, dc, read, size, time)"
    " SELECT o.asset_id, o.user_id,"
    " (v.asset_id IS NOT NULL) - (o.concealed IS NOT NULL),"
    " COALESCE(v.concealed, 0) - COALESCE(o.concealed, 0),"
    " EXISTS (SELECT 1 FROM visibility_members r WHERE r.asset_id = o.asset_id AND r.side = 0),"
    " COALESCE(a.size_bytes, 0), COALESCE(a.duration_ms, 0)"
    " FROM visibility_owed o"
    " LEFT JOIN viewer_assets v ON v.user_id = o.user_id AND v.asset_id = o.asset_id"
    " LEFT JOIN assets a ON a.id = o.asset_id"
    " WHERE o.asset_id IN (<<ASSETS>>) AND o.concealed IS NOT v.concealed"
)
_ANSWER_MOVED = "visibility_moved"
CLEAR_MOVED = "DELETE FROM visibility_moved"
# The moved files a touch read members of (a seek on the key's first column), and letting them go
# once the sides have moved them.
_MOVED_READ = "SELECT asset_id FROM visibility_moved WHERE read = 1"
_MOVED_READ_GO = "DELETE FROM visibility_moved WHERE read = 1"

# The named files a touch read members of: the only ones whose members are read again. Driven from
# the few files read (the unary + keeps it off the key), not from every file being folded.
_READ_BEFORE = (
    "SELECT DISTINCT asset_id FROM visibility_members WHERE side = 0 AND +asset_id IN (<<ASSETS>>)"
)
_COUNTS_MOVED_ONLY = "SUM(d.dn) != 0 OR SUM(d.dc) != 0"
_BYTES_MOVED = _COUNTS_MOVED_ONLY + " OR SUM(d.dn * d.size) != 0 OR SUM(d.dc * d.size) != 0"
_SUMS_MOVED = _BYTES_MOVED + " OR SUM(d.dn * d.time) != 0 OR SUM(d.dc * d.time) != 0"
# A kind that sums no size moves by its counts alone.
_COUNTS_ONLY = {"B": "0", "CB": "0", "MS": "0", "CMS": "0"}
_SIZES = {
    "B": "SUM(d.dn * d.size)",
    "CB": "SUM(d.dc * d.size)",
    "MS": "SUM(d.dn * d.time)",
    "CMS": "SUM(d.dc * d.time)",
}
_KIND_MOVED = (
    "INSERT INTO viewer_entity_counts (user_id, kind, object_id, permitted, concealed,"
    " permitted_bytes, concealed_bytes, permitted_ms, concealed_ms)"
    " SELECT d.user_id, '<<KIND>>', d.object_id, SUM(d.dn), SUM(d.dc), <<B>>, <<CB>>, <<MS>>,"
    " <<CMS>> FROM (SELECT <<DISTINCT>>d.user_id, d.asset_id, d.dn, d.dc, d.size, d.time,"
    " m.<<COLUMN>> AS object_id FROM <<ANSWER_MOVED>> d CROSS JOIN <<SOURCE>> m"
    " ON m.asset_id = d.asset_id) d WHERE 1 = 1"
    " GROUP BY d.user_id, d.object_id HAVING <<SUMS_MOVED>>"
    " ON CONFLICT (user_id, kind, object_id) DO UPDATE"
    " SET permitted = permitted + excluded.permitted, concealed = concealed + excluded.concealed,"
    " permitted_bytes = permitted_bytes + excluded.permitted_bytes,"
    " concealed_bytes = concealed_bytes + excluded.concealed_bytes,"
    " permitted_ms = permitted_ms + excluded.permitted_ms,"
    " concealed_ms = concealed_ms + excluded.concealed_ms"
)
_PAIR_MOVED = (
    "INSERT INTO viewer_pair_counts (user_id, kind_a, id_a, kind_b, id_b, permitted, concealed)"
    " SELECT d.user_id, '<<KIND_A>>', ma.<<COLUMN_A>>, '<<KIND_B>>', mb.<<COLUMN_B>>,"
    " SUM(d.dn), SUM(d.dc)"
    " FROM <<ANSWER_MOVED>> d CROSS JOIN <<TABLE_A>> ma ON ma.asset_id = d.asset_id"
    " CROSS JOIN <<TABLE_B>> mb ON mb.asset_id = d.asset_id"
    " WHERE 1 = 1 GROUP BY d.user_id, ma.<<COLUMN_A>>, mb.<<COLUMN_B>>"
    " HAVING SUM(d.dn) != 0 OR SUM(d.dc) != 0"
    " ON CONFLICT (user_id, kind_a, id_a, kind_b, id_b) DO UPDATE"
    " SET permitted = permitted + excluded.permitted, concealed = concealed + excluded.concealed"
)
_FILE_MOVED = (
    "INSERT INTO viewer_stats (user_id, permitted, concealed, permitted_bytes, concealed_bytes)"
    " SELECT d.user_id, SUM(d.dn), SUM(d.dc), SUM(d.dn * d.size), SUM(d.dc * d.size)"
    " FROM <<ANSWER_MOVED>> d WHERE 1 = 1"
    " GROUP BY d.user_id HAVING <<SUMS_MOVED>>"
    " ON CONFLICT (user_id) DO UPDATE"
    " SET permitted = permitted + excluded.permitted, concealed = concealed + excluded.concealed,"
    " permitted_bytes = permitted_bytes + excluded.permitted_bytes,"
    " concealed_bytes = concealed_bytes + excluded.concealed_bytes"
)

# NOT EXISTS, not OR IGNORE: a trigger's conflict clause gives way to the statement that fired it.
_RECORD = (
    "INSERT INTO visibility_owed (asset_id, user_id, concealed)"
    " SELECT s.asset_id, s.user_id, v.concealed"
    " FROM visibility_pending s JOIN assets a ON a.id = s.asset_id"
    " LEFT JOIN viewer_assets v ON v.user_id = s.user_id AND v.asset_id = s.asset_id"
    " WHERE NOT EXISTS (SELECT 1 FROM visibility_owed o"
    " WHERE o.asset_id = s.asset_id AND o.user_id = s.user_id)"
)

# A file touched again is folded by this write, every pair of it.
_DUE_NOW = (
    "UPDATE visibility_owed SET owed = 0"
    " WHERE owed = 1 AND asset_id IN (SELECT asset_id FROM visibility_pending)"
)

#: The `owe` call: the settled pairs are many, so their files are owed past this write.
OWE_IF_MANY = (
    "INSERT INTO visibility_step_owe (user_id) SELECT NULL"
    " WHERE (SELECT COUNT(*) FROM (SELECT 1 FROM visibility_pending LIMIT <<N>>)) >= <<N>>"
).replace("<<N>>", str(OWED_FROM))
_OWE = (
    "UPDATE visibility_owed SET owed = 1"
    " WHERE asset_id IN (SELECT asset_id FROM visibility_pending)"
)

#: The `fold` call, from a trigger that has to fold what it touched before the write ends.
RUN_FOLD = "INSERT INTO visibility_step_fold (user_id) VALUES (NULL)"
RUN_FOLD_PAGE = "INSERT INTO visibility_step_fold_page (user_id) VALUES (NULL)"

#: Whether any file is owed past its write.
ANY_OWED = "SELECT 1 FROM visibility_owed WHERE owed = 1 LIMIT 1"

_PAID = "DELETE FROM visibility_owed WHERE asset_id IN (<<ASSETS>>)"
# Driven from the members, which a fold of answers alone has none of (the + keeps it off the key).
_MEMBERS_PAID = "DELETE FROM visibility_members WHERE +asset_id IN (<<ASSETS>>)"
CLEAR_OWED = "DELETE FROM visibility_owed"
CLEAR_MEMBERS = "DELETE FROM visibility_members"

#: Whether this write touched anything: the before-commit hook's whole cost when it did not.
ANY_TOUCHED = "SELECT 1 FROM visibility_owed WHERE owed = 0 LIMIT 1"

#: A page of owed files made due, for the fold after a large share and at boot.
DUE_PAGE = (
    "UPDATE visibility_owed SET owed = 0 WHERE asset_id IN"
    " (SELECT DISTINCT asset_id FROM visibility_owed WHERE owed = 1 LIMIT ?)"
)
DUE_USERS = "SELECT DISTINCT user_id FROM visibility_owed WHERE owed = 0"

#: What the `user` step adds: a user rebuilt from nothing owes nothing.
DROP_USER_OWED = "DELETE FROM visibility_owed WHERE user_id = <<USER>>"

# One kind's members of the named files: a file's rows per thing (1 for a kind counting files),
# and the size and running time of those rows for a kind that sums them. Read once per file and
# kind: a kind read is marked (`#kind`, n 0), so a later touch in the write reads only the kinds
# not read yet, and the fold reads now exactly the kinds read before.
_ARM = (
    "SELECT m.asset_id, '<<KIND>>' AS kind, m.<<COLUMN>> AS object_id, <<N>> AS n,"
    " <<BYTES>> AS bytes, <<MS>> AS ms FROM <<SOURCE>> m<<SIZED>>"
    " WHERE m.asset_id IN (<<ASSETS>>) AND <<READ>> GROUP BY m.asset_id, m.<<COLUMN>>"
)
_FILE_ARM = (
    "SELECT a.id AS asset_id, '' AS kind, '' AS object_id, 1 AS n,"
    " COALESCE(a.size_bytes, 0) AS bytes, COALESCE(a.duration_ms, 0) AS ms"
    " FROM assets a WHERE a.id IN (<<ASSETS>>) AND <<READ>>"
)
_MARK_ARM = (
    "SELECT a.id, k.column1, '', 0, 0, 0 FROM assets a CROSS JOIN (VALUES <<MARKS>>) k"
    " WHERE a.id IN (<<ASSETS>>) AND NOT EXISTS (SELECT 1 FROM visibility_members r"
    " WHERE r.asset_id = a.id AND r.side = 0 AND r.kind = k.column1)"
)
_MARKED = (
    "EXISTS (SELECT 1 FROM visibility_members r"
    " WHERE r.asset_id = <<ID>> AND r.side = 0 AND r.kind = '#<<KIND>>')"
)
_READ_MEMBERS = (
    "INSERT INTO visibility_members (asset_id, side, kind, object_id, n, bytes, ms)"
    " SELECT asset_id, <<SIDE>>, kind, object_id, n, bytes, ms FROM (<<ARMS>>)"
)

# The difference, per user and thing: the members as the counts hold them taken away, under the
# answer the counts hold, and the members as they now stand given, under the answer each pair now
# has. A pair with no answer either side moves nothing on that side.
# The member sides start from one row when any file's members were read, and from none for a
# fold of answers alone, which then reads nothing more.
_ANY_MEMBERS = "(SELECT 1 FROM visibility_members LIMIT 1) g"

_SIDES = (
    "SELECT o.user_id, m.kind, m.object_id, -m.n AS n, -m.n * o.concealed AS c,"
    " -m.bytes AS b, -m.bytes * o.concealed AS cb, -m.ms AS ms, -m.ms * o.concealed AS cms"
    " FROM <<ANY_MEMBERS>> CROSS JOIN visibility_owed o"
    " JOIN visibility_members m ON m.asset_id = o.asset_id AND m.side = 0"
    " WHERE o.asset_id IN (<<ASSETS>>) AND o.concealed IS NOT NULL AND m.n != 0 AND <<WHICH>>"
    " UNION ALL"
    " SELECT o.user_id, m.kind, m.object_id, m.n, m.n * v.concealed,"
    " m.bytes, m.bytes * v.concealed, m.ms, m.ms * v.concealed"
    " FROM <<ANY_MEMBERS>> CROSS JOIN visibility_owed o"
    " CROSS JOIN viewer_assets v ON v.user_id = o.user_id AND v.asset_id = o.asset_id"
    " JOIN visibility_members m ON m.asset_id = o.asset_id AND m.side = 1"
    " WHERE o.asset_id IN (<<ASSETS>>) AND m.n != 0 AND <<WHICH>>"
).replace("<<ANY_MEMBERS>>", _ANY_MEMBERS)
_MOVED = "SUM(d.n) != 0 OR SUM(d.c) != 0 OR SUM(d.b) != 0 OR SUM(d.cb) != 0"

_COUNTS_MOVED = (
    "INSERT INTO viewer_entity_counts (user_id, kind, object_id, permitted, concealed,"
    " permitted_bytes, concealed_bytes, permitted_ms, concealed_ms)"
    " SELECT d.user_id, d.kind, d.object_id, SUM(d.n), SUM(d.c), SUM(d.b), SUM(d.cb),"
    " SUM(d.ms), SUM(d.cms) FROM (<<SIDES>>) d WHERE 1 = 1"
    " GROUP BY d.user_id, d.kind, d.object_id"
    " HAVING <<MOVED>> OR SUM(d.ms) != 0 OR SUM(d.cms) != 0"
    " ON CONFLICT (user_id, kind, object_id) DO UPDATE"
    " SET permitted = permitted + excluded.permitted, concealed = concealed + excluded.concealed,"
    " permitted_bytes = permitted_bytes + excluded.permitted_bytes,"
    " concealed_bytes = concealed_bytes + excluded.concealed_bytes,"
    " permitted_ms = permitted_ms + excluded.permitted_ms,"
    " concealed_ms = concealed_ms + excluded.concealed_ms"
)

_STATS_MOVED = (
    "INSERT INTO viewer_stats (user_id, permitted, concealed, permitted_bytes, concealed_bytes)"
    " SELECT d.user_id, SUM(d.n), SUM(d.c), SUM(d.b), SUM(d.cb) FROM (<<SIDES>>) d WHERE 1 = 1"
    " GROUP BY d.user_id HAVING <<MOVED>>"
    " ON CONFLICT (user_id) DO UPDATE"
    " SET permitted = permitted + excluded.permitted, concealed = concealed + excluded.concealed,"
    " permitted_bytes = permitted_bytes + excluded.permitted_bytes,"
    " concealed_bytes = concealed_bytes + excluded.concealed_bytes"
)

# The pairs: two members of one file on one side, of a pair of kinds the counts keep.
_PAIR_SIDES = (
    "SELECT o.user_id, a.kind AS kind_a, a.object_id AS id_a, b.kind AS kind_b,"
    " b.object_id AS id_b, -a.n * b.n AS n, -a.n * b.n * o.concealed AS c"
    " FROM <<ANY_MEMBERS>> CROSS JOIN visibility_owed o"
    " JOIN visibility_members a ON a.asset_id = o.asset_id AND a.side = 0"
    " JOIN visibility_members b ON b.asset_id = o.asset_id AND b.side = 0"
    " WHERE o.asset_id IN (<<ASSETS>>) AND o.concealed IS NOT NULL"
    " AND (a.kind, b.kind) IN (<<PAIRED>>)"
    " UNION ALL"
    " SELECT o.user_id, a.kind, a.object_id, b.kind, b.object_id, a.n * b.n,"
    " a.n * b.n * v.concealed"
    " FROM <<ANY_MEMBERS>> CROSS JOIN visibility_owed o"
    " CROSS JOIN viewer_assets v ON v.user_id = o.user_id AND v.asset_id = o.asset_id"
    " JOIN visibility_members a ON a.asset_id = o.asset_id AND a.side = 1"
    " JOIN visibility_members b ON b.asset_id = o.asset_id AND b.side = 1"
    " WHERE o.asset_id IN (<<ASSETS>>) AND (a.kind, b.kind) IN (<<PAIRED>>)"
).replace("<<ANY_MEMBERS>>", _ANY_MEMBERS)
_PAIRS_MOVED = (
    "INSERT INTO viewer_pair_counts (user_id, kind_a, id_a, kind_b, id_b, permitted, concealed)"
    " SELECT d.user_id, d.kind_a, d.id_a, d.kind_b, d.id_b, SUM(d.n), SUM(d.c)"
    " FROM (<<PAIR_SIDES>>) d WHERE 1 = 1"
    " GROUP BY d.user_id, d.kind_a, d.id_a, d.kind_b, d.id_b"
    " HAVING SUM(d.n) != 0 OR SUM(d.c) != 0"
    " ON CONFLICT (user_id, kind_a, id_a, kind_b, id_b) DO UPDATE"
    " SET permitted = permitted + excluded.permitted, concealed = concealed + excluded.concealed"
)


def _read_members(
    kinds: Sequence[Counted], side: int, assets: str, own: frozenset[str], *, file: bool = True
) -> str:
    """One statement reading the named files' members of these kinds (and the file itself) into
    `side`: on side 0 the kinds not read yet, marked; on side 1 the kinds side 0 read. A kind
    a table's own touch reads is marked by itself; every other kind goes with the file (`#`)."""
    from sift.kernel.access import visibility as v

    def read(ident: str, kind: str) -> str:
        marked = v._filled(_MARKED, ID=ident, KIND=kind if kind in own else "")
        return "NOT " + marked if side == 0 else marked

    arms = [v._filled(_FILE_ARM, READ=read("a.id", ""))] if file else []
    for one in kinds:
        n = "1" if one.distinct else "COUNT(*)"
        sized = " CROSS JOIN assets a ON a.id = m.asset_id" if one.sized else ""
        arms.append(
            v._filled(
                _ARM,
                KIND=one.kind,
                COLUMN=one.column,
                SOURCE=one.source,
                N=n,
                BYTES=f"COALESCE(MAX(a.size_bytes), 0) * {n}" if one.sized else "0",
                MS=f"COALESCE(MAX(a.duration_ms), 0) * {n}" if one.sized else "0",
                SIZED=sized,
                READ=read("m.asset_id", one.kind),
            )
        )
    if side == 0:
        names = ([""] if file else []) + [one.kind for one in kinds if one.kind in own]
        marks = ", ".join(f"('#{name}')" for name in names)
        arms.append(v._filled(_MARK_ARM, MARKS=marks))
    return v._filled(_READ_MEMBERS, SIDE=str(side), ARMS=" UNION ALL ".join(arms)).replace(
        ASSETS, assets
    )


def fold(
    kinds: Sequence[Counted],
    pairs: Sequence[tuple[Counted, Counted]],
    assets: str,
    *,
    staying: bool = True,
) -> list[str]:
    """Move the counts of the named files by what they hold now against what the counts hold,
    and let go of them."""
    from sift.kernel.access import visibility as v

    paired = ", ".join(f"('{a.kind}', '{b.kind}')" for a, b in pairs)
    things, files = "m.kind != ''", "m.kind = ''"
    own = _own(kinds, pairs)
    made = [
        _FILL_MOVED,
        # A file some of whose kinds a touch read, and whose answer moved: the rest read now, as
        # the counts hold them (unchanged in this write, or a touch would have read them), so the
        # sides below move it whole and the statements after them read no marks.
        _read_members(kinds, 0, _MOVED_READ, own),
        # The kinds a touch read before its change: by what each file held against what it holds
        # (nothing, for a file going, whose rows are already gone).
        *([_read_members(kinds, 1, _READ_BEFORE, own)] if staying else []),
        v._filled(_COUNTS_MOVED, SIDES=v._filled(_SIDES, WHICH=things), MOVED=_MOVED),
        v._filled(_PAIRS_MOVED, PAIR_SIDES=v._filled(_PAIR_SIDES, PAIRED=paired)),
        v._filled(_STATS_MOVED, SIDES=v._filled(_SIDES, WHICH=files), MOVED=_MOVED),
        _MOVED_READ_GO,
        # The rest, where only an answer moved: their members are what the counts hold, since a
        # change to a member is read before it lands, so each moves by the answer's difference.
        *_answers_moved(kinds, pairs),
        CLEAR_MOVED,
        _MEMBERS_PAID,
        _PAID,
    ]
    return [one.replace(ASSETS, assets) for one in made]


def _answers_moved(kinds: Sequence[Counted], pairs: Sequence[tuple[Counted, Counted]]) -> list[str]:
    """Per kind, pair and the totals: the counts moved by each pair's answer, for the files whose
    members no touch read."""
    from sift.kernel.access import visibility as v

    made = []
    for one in kinds:
        made.append(
            v._filled(
                _KIND_MOVED,
                ANSWER_MOVED=_ANSWER_MOVED,
                SUMS_MOVED=_SUMS_MOVED if one.sized else _COUNTS_MOVED_ONLY,
                DISTINCT="DISTINCT " if one.distinct else "",
                KIND=one.kind,
                COLUMN=one.column,
                SOURCE=one.source,
                **(_SIZES if one.sized else _COUNTS_ONLY),
            )
        )
    for a, b in pairs:
        made.append(
            v._filled(
                _PAIR_MOVED,
                ANSWER_MOVED=_ANSWER_MOVED,
                KIND_A=a.kind,
                COLUMN_A=a.column,
                TABLE_A=a.source,
                KIND_B=b.kind,
                COLUMN_B=b.column,
                TABLE_B=b.source,
            )
        )
    made.append(v._filled(_FILE_MOVED, ANSWER_MOVED=_ANSWER_MOVED, SUMS_MOVED=_BYTES_MOVED))
    return made


def kinds_over(
    kinds: Sequence[Counted], pairs: Sequence[tuple[Counted, Counted]]
) -> dict[str, list[Counted]]:
    """Each counted table the verdict does not read, and the kinds its change can move: the
    kinds whose members read it and the other side of every pair they are in. Not a file's own
    row: every kind that sums a size reads it."""

    alone = sorted({one.table for one in kinds} - decided_by() - {"assets"})
    made: dict[str, list[Counted]] = {}
    for table in alone:
        over = {one.kind for one in kinds if re.search(rf"\b{table}\b", one.source)}
        # Every kind paired with one read, and so on: a pair is read whole or not at all.
        while (
            grown := {
                side
                for a, b in pairs
                if a.kind in over or b.kind in over
                for side in (a.kind, b.kind)
            }
            - over
        ):
            over |= grown
        made[table] = [one for one in kinds if one.kind in over]
    return made


def _own(kinds: Sequence[Counted], pairs: Sequence[tuple[Counted, Counted]]) -> frozenset[str]:
    """The kinds a table's own touch reads, each marked by itself when read."""
    return frozenset(one.kind for over in kinds_over(kinds, pairs).values() for one in over)


def moving_steps(
    kinds: Sequence[Counted], pairs: Sequence[tuple[Counted, Counted]]
) -> list[tuple[str, list[str]]]:
    """`touch`, `rows`, `owe` and `fold` (of every file this write touched), as trigger steps."""
    from sift.kernel.access import visibility as v

    own = _own(kinds, pairs)
    touch = [_STALE_MEMBERS, _read_members(kinds, 0, _STAGED_THERE, own), _DUE_NOW, _RECORD]
    rows = [
        v._CLEAR_PLACES,
        v._FILL_STAGED_PLACES,
        v._DELETE_STAGED,
        v._INSERT_STAGED,
        v._CLEAR_PLACES,
        v._CLEAR_PENDING,
    ]
    # A file about to go: its rows dropped and its counts folded while its members are there, so
    # what its memberships' own triggers then reach finds nothing recorded to take.
    going = [
        v._DELETE_STAGED,
        *fold(kinds, pairs, _STAGED_FILES, staying=False),
        v._CLEAR_PENDING,
    ]
    # A table the verdict does not read moves no answer, so only the kinds over it are read.
    alone = [
        (
            TOUCH + "_" + table,
            [
                _STALE_MEMBERS,
                _read_members(over, 0, _STAGED_THERE, own, file=False),
                _DUE_NOW,
                _RECORD,
            ],
        )
        for table, over in sorted(kinds_over(kinds, pairs).items())
    ]
    # A grant or a hide moves answers and never a member: the answers alone are recorded, and
    # the members read when the counts are folded.
    answers = [_STALE_MEMBERS, _DUE_NOW, _RECORD]
    return [
        (TOUCH, touch),
        (ANSWERS, answers),
        *alone,
        (ROWS, rows),
        (OWE, [_OWE]),
        (FOLD, fold(kinds, pairs, _TOUCHED)),
        (FOLD_PAGE, [RUN_FOLD]),
        (GOING, going),
    ]


#: From this many pairs a widening is filed after its press: about 29 microseconds a pair held the
#: writer in its press (a 100,000-file fixture), so the 250 ms budget is crossed near 8,600 pairs,
#: and a widening decided in place holds it at most about half that.
DEFER_FROM = 4_000

#: Pairs filed per page: about 60 ms of the writer, and over `OWED_FROM`, so their counts are owed.
FILING_PAGE = 2_000

# Whether the grant row `ROW` defers its pairs: a widening, by a caller that files, of many pairs.
_DEFERRED = (
    "(<<WIDENING>> AND EXISTS (SELECT 1 FROM visibility_may_defer)"
    " AND (SELECT COUNT(*) FROM (SELECT 1 FROM (<<PAIRS>>) LIMIT <<N>>)) >= <<N>>)"
)
# The filing kept, started again from the first pair where one was under way.
_FILED_LATER = (
    "INSERT INTO visibility_filing (user_id, object_type, object_id)"
    " SELECT <<ROW>>.subject_user_id, <<ROW>>.object_type, <<ROW>>.object_id WHERE <<DEFERRED>>"
    " ON CONFLICT DO UPDATE SET after = ''"
)

_NOT_FILED_LATER = (
    " WHERE NOT EXISTS (SELECT 1 FROM visibility_filing f WHERE f.user_id = <<ROW>>.subject_user_id"
    " AND f.object_type = <<ROW>>.object_type AND f.object_id = <<ROW>>.object_id AND f.after = '')"
)

ANY_FILING = "SELECT 1 FROM visibility_filing LIMIT 1"
NEXT_FILING = "SELECT user_id, object_type, object_id, after FROM visibility_filing LIMIT 1"
CLEAR_FILING = "DELETE FROM visibility_filing"
_STAGE_FILED = (
    "INSERT INTO visibility_pending (user_id, asset_id)"
    " SELECT ?, a.id FROM json_each(?) j CROSS JOIN assets a ON a.id = j.value"
)
# Moved on past the page, or done; unless a widening since started it again.
_FILED_TO = (
    "UPDATE visibility_filing SET after = ?"
    " WHERE user_id = ? AND object_type = ? AND object_id = ? AND after = ?"
)
_FILED = (
    "DELETE FROM visibility_filing"
    " WHERE user_id = ? AND object_type = ? AND object_id = ? AND after = ?"
)


def filing_page(filing: Row, limit: int) -> tuple[str, dict[str, object]]:
    """The next page of a filing's files, as a read and its values: in order of id, from `after`."""
    from sift.kernel.access import visibility as v

    kind = str(filing["object_type"])
    pairs = {
        "root": v._ONE_USER_IN_ROOT.replace("{root}", "{object}"),
        "folder": v._ONE_USER_UNDER_FOLDER.replace("{folder}", "{object}"),
    }.get(kind) or v._MEMBERS_OF[kind]
    sql = (
        "SELECT DISTINCT asset_id FROM ("  # noqa: S608 (module constants and named values only)
        + pairs.format(user=":user", object=":object")
        + ") WHERE asset_id > :after ORDER BY asset_id LIMIT :limit"
    )
    values = {
        "user": filing["user_id"],
        "object": filing["object_id"],
        "after": filing["after"],
        "limit": limit,
    }
    return sql, values


#: A filed page's steps, in order: settle, answer, owe the counts where many, settled.
_PAGE_CALLS = (
    *(RUN.replace("<<STEP>>", step) for step in (SETTLE, ANSWERS)),
    OWE_IF_MANY,
    RUN.replace("<<STEP>>", SETTLED),
)


async def file_page(connection: Connection, filing: Row, files: Sequence[str], limit: int) -> None:
    """One page of a filing decided, as its press would have, and the filing moved on past it."""
    from sift.kernel.access import visibility as v

    await connection.execute(v._CLEAR_PENDING)
    await connection.execute(_STAGE_FILED, (filing["user_id"], json.dumps(list(files))))
    for statement in _PAGE_CALLS:
        await connection.execute(statement)
    key = (filing["user_id"], filing["object_type"], filing["object_id"], filing["after"])
    if len(files) < limit:
        await connection.execute(_FILED, key)
    else:
        await connection.execute(_FILED_TO, (files[-1], *key))


async def file_what_is_deferred(connection: Connection) -> None:
    """At boot: a widening a stop left part filed, filed to the end, and nobody left deferring."""
    if not await connection.execute_fetchall(_HOLDS_FILING):
        return
    await connection.execute(NO_DEFER)
    while filing := next(iter(await connection.execute_fetchall(NEXT_FILING)), None):
        sql, values = filing_page(filing, FILING_PAGE)
        files = [str(row[0]) for row in await connection.execute_fetchall(sql, values)]
        await file_page(connection, filing, files, FILING_PAGE)
    log.info("visibility.deferred_filed")


_HOLDS_FILING = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'visibility_filing'"


#: How many owed files one fold takes after a large share: under a second of the writer on a
#: library of 100,000 files (a page of 2,000 held it up to 1.3 s there).
OWED_FOLD_PAGE = 1_000


#: The connections already seen holding this component's tables: asked once each, since a
#: database being made writes before they exist, and another kind of database never has them.
_HOLDING: weakref.WeakSet[Connection] = weakref.WeakSet()

_HOLDS = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'visibility_owed'"


async def move_counts_once(connection: Connection) -> None:
    """Before a write commits: the counts of every file it touched moved once (`visibility_settled`).
    One read when it touched none."""
    if connection not in _HOLDING:
        if not await connection.execute_fetchall(_HOLDS):
            return
        _HOLDING.add(connection)
    if await connection.execute_fetchall(ANY_TOUCHED):
        await connection.execute(RUN_FOLD)


async def owe_large_shares(connection: Connection) -> None:
    """The version 18 step, safe to run again: the tables a write records its touched files in,
    and the triggers rewritten to move the counts once per write. Nothing stored moves."""
    from sift.kernel.access import visibility as v

    for table in TABLES:
        await connection.execute(table)
    await v._drop_triggers(connection)
    await connection.execute(_DROP_ONE_VIEW)
    await v._create_triggers(connection)
    log.info("visibility.counts_moved_per_write")


async def fold_owed(connection: Connection, limit: int) -> Audience:
    """Fold up to `limit` owed files' counts in this write, and the users whose counts moved."""
    await connection.execute(DUE_PAGE, (limit,))
    moved = Audience.of(await connection.execute_fetchall(DUE_USERS))
    if moved:
        await connection.execute(RUN_FOLD_PAGE)
    return moved


async def later_steps(connection: Connection, on_disk: int) -> None:
    """Versions 17 (the walls' totals, from the stored counts), 18 (the counts moved once per
    write, a large share's owed past its press) and 19 (a moved pair's size kept beside it)."""
    from sift.kernel.access import visibility_walls

    if on_disk < 17:
        await visibility_walls.total_the_walls(connection)
    await connection.execute(DROP_MOVED)
    await owe_large_shares(connection)


async def fold_what_is_owed(connection: Connection) -> None:
    """At boot: a widening a stop left part filed, then a share's counts left owed, folded
    before anything reads them."""
    await file_what_is_deferred(connection)
    if not await connection.execute_fetchall(ANY_OWED):
        return
    while await fold_owed(connection, OWED_FOLD_PAGE):
        log.info("visibility.owed_folded")


async def start_empty(connection: Connection) -> None:
    """For a rebuild from the facts: the scratch tables there, and nothing owed, since every count
    is made from the rows."""
    for table in (CREATE, *TABLES):
        await connection.execute(table)
    for statement in (CLEAR_OWED, CLEAR_MEMBERS, CLEAR_MOVED, CLEAR_FILING):
        await connection.execute(statement)


async def create_step_views(
    connection: Connection, triggers: Sequence[tuple[str, str, str]]
) -> None:
    """The view each step trigger sits on."""
    from sift.kernel.access import visibility as v

    for _name, table, _ddl in triggers:
        if table.startswith(RECOMPUTE):
            await connection.execute(v._filled(_CREATE_STEP, STEP=table.removeprefix(RECOMPUTE)))


@functools.cache
def decided_by() -> frozenset[str]:
    """The tables the verdict reads: a change to any other moves counts and never a row."""
    from sift.kernel.access import visibility as v

    return frozenset(re.findall(r"\b(?:FROM|JOIN)\s+([a-z_]+)\b", v._VERDICT_ROWS + v._PLACE_ROWS))


def owing(
    halves: _Recompute, pairs: str, *, row: str = "", widening: str | None = None
) -> list[str]:
    """`whole` for a grant: from `OWED_FROM` settled pairs its counts are owed past the write,
    so the press answers once its rows are written. A `widening` of `DEFER_FROM` pairs or more
    stages none: the grant row `row` is filed after it (`file_page`)."""
    made = halves.whole(pairs)
    if halves.version_13:
        return made
    owed = [*made[:4], OWE_IF_MANY, *made[4:]]
    if widening is None:
        return owed
    deferred = (
        _DEFERRED.replace("<<WIDENING>>", widening)
        .replace("<<PAIRS>>", pairs)
        .replace("<<N>>", str(DEFER_FROM))
    )
    # Counted once, by the filing: the pairs are staged unless it was just kept to start over.
    owed[1] = owed[1] + _NOT_FILED_LATER.replace("<<ROW>>", row)
    later = _FILED_LATER.replace("<<DEFERRED>>", deferred).replace("<<ROW>>", row)
    return [later, *owed]


def going(halves: _Recompute, pairs: str) -> list[str]:
    """A file about to go: recorded, then its rows dropped and its counts folded."""
    before = halves.split(pairs)[0]
    if halves.version_13:
        return before
    return [*before, RUN.replace("<<STEP>>", GOING)]


def user_step(halves: _Recompute) -> list[str]:
    """A user rebuilt from nothing: what this write touched is folded first, so no file is left
    recorded against members it no longer has, and the user then owes nothing."""
    return [
        RUN_FOLD,
        *halves.rebuilt("NEW.user_id"),
        DROP_USER_OWED.replace("<<USER>>", "NEW.user_id"),
    ]


def moving(
    kinds: Sequence[Counted], pairs: Sequence[tuple[Counted, Counted]]
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """The steps that move the counts once per write, as the recompute keeps them."""
    return tuple((step, tuple(body)) for step, body in moving_steps(kinds, pairs))


before_commit(move_counts_once)
