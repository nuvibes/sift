# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tables behind who may see which file, and the statements that fill, move and check them."""

from __future__ import annotations

from sift.kernel.access.sites import FILES_SITES_REACH


def _filled(template: str, **names: str) -> str:
    """One statement from a template and the module constants it names as `<<NAME>>`."""
    text = template
    for name, value in names.items():
        text = text.replace("<<" + name + ">>", value)
    return text


# --- the tables ----------------------------------------------------------------------------

_CREATE_FOLDER_ANCESTRY = """
CREATE TABLE IF NOT EXISTS folder_ancestry (
  folder_id   TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  ancestor_id TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  depth       INTEGER NOT NULL,
  PRIMARY KEY (folder_id, ancestor_id)
) WITHOUT ROWID
"""

# Every copy under a folder: seek by the ancestor, read the folder ids off the index.
_CREATE_ANCESTRY_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_folder_ancestry_ancestor"
    " ON folder_ancestry(ancestor_id, folder_id)"
)

_CREATE_VIEWER_ASSETS = """
CREATE TABLE IF NOT EXISTS viewer_assets (
  user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  asset_id  TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  concealed INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, asset_id)
) WITHOUT ROWID
"""

# Partial: the Hidden screen reads only the concealed rows.
_CREATE_CONCEALED_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_viewer_assets_concealed"
    " ON viewer_assets(user_id, asset_id) WHERE concealed = 1"
)

# The sizes of the same two sets of files, so the vault holds back bytes by the same rule.
_CREATE_VIEWER_STATS = """
CREATE TABLE IF NOT EXISTS viewer_stats (
  user_id   TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  permitted INTEGER NOT NULL DEFAULT 0,
  concealed INTEGER NOT NULL DEFAULT 0,
  permitted_bytes INTEGER NOT NULL DEFAULT 0,
  concealed_bytes INTEGER NOT NULL DEFAULT 0
)
"""

# Scratch: the pairs a recompute works on, so a folder move reads its copies BEFORE the ancestry
# changes.
_CREATE_PENDING = """
CREATE TABLE IF NOT EXISTS visibility_pending (
  user_id  TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  PRIMARY KEY (user_id, asset_id)
) WITHOUT ROWID
"""

# Scratch: what each place says for one user, worked out once per place and probed per copy.
_CREATE_PLACES = """
CREATE TABLE IF NOT EXISTS visibility_places (
  user_id    TEXT NOT NULL,
  root_id    TEXT NOT NULL,
  place      TEXT NOT NULL,
  restricted INTEGER NOT NULL,
  shared     INTEGER NOT NULL,
  vaulted    INTEGER NOT NULL,
  PRIMARY KEY (user_id, root_id, place)
) WITHOUT ROWID
"""

# How many files of each thing a user may see and how many the vault holds back, moved only by the
# fold in `visibility_settled`.
_CREATE_ENTITY_COUNTS = """
CREATE TABLE IF NOT EXISTS viewer_entity_counts (
  user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind      TEXT NOT NULL,
  object_id TEXT NOT NULL,
  permitted INTEGER NOT NULL DEFAULT 0,
  concealed INTEGER NOT NULL DEFAULT 0,
  permitted_bytes INTEGER NOT NULL DEFAULT 0,
  concealed_bytes INTEGER NOT NULL DEFAULT 0,
  permitted_ms INTEGER NOT NULL DEFAULT 0,
  concealed_ms INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, kind, object_id)
) WITHOUT ROWID
"""

# How many files two things share, per user, so a card reads its cells as short ranges. A site is no
# side of a pair: its reach moves without a file moving, so the pair is kept on the username.
_CREATE_PAIR_COUNTS = """
CREATE TABLE IF NOT EXISTS viewer_pair_counts (
  user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind_a    TEXT NOT NULL,
  id_a      TEXT NOT NULL,
  kind_b    TEXT NOT NULL,
  id_b      TEXT NOT NULL,
  permitted INTEGER NOT NULL DEFAULT 0,
  concealed INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, kind_a, id_a, kind_b, id_b)
) WITHOUT ROWID
"""

# The B side as a range, covering, so a card read from that side never probes the table.
_CREATE_PAIR_COUNTS_B_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_viewer_pair_counts_b"
    " ON viewer_pair_counts(user_id, kind_b, id_b, kind_a, id_a, permitted, concealed)"
)

# Partial: the sweep reads only the rows a recompute has just emptied.
_CREATE_PAIR_COUNTS_EMPTY_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_viewer_pair_counts_empty"
    " ON viewer_pair_counts(user_id) WHERE permitted <= 0"
)

# How many live partners of one kind a thing has, per user, kept only by the triggers on
# `viewer_pair_counts`.
_CREATE_PARTNER_COUNTS = """
CREATE TABLE IF NOT EXISTS viewer_partner_counts (
  user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL,
  object_id    TEXT NOT NULL,
  partner_kind TEXT NOT NULL,
  permitted    INTEGER NOT NULL DEFAULT 0,
  shown        INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, kind, object_id, partner_kind)
) WITHOUT ROWID
"""

TABLES = (
    "folder_ancestry",
    "viewer_assets",
    "viewer_stats",
    "viewer_entity_counts",
    "viewer_pair_counts",
    "viewer_partner_counts",
    "visibility_pending",
    "visibility_places",
)


#: A kind's file and concealed counts over `v`, the two forms of `Counted.distinct`.
_ROWS_COUNTED = ("COUNT(*)", "COALESCE(SUM(v.concealed), 0)")
_FILES_COUNTED = (
    "COUNT(DISTINCT v.asset_id)",
    "COUNT(DISTINCT CASE WHEN v.concealed = 1 THEN v.asset_id END)",
)

#: The sizes beside those counts, over `v` and `a`.
_BYTES_SUMMED = (
    "COALESCE(SUM(a.size_bytes), 0)",
    "COALESCE(SUM(CASE WHEN v.concealed = 1 THEN a.size_bytes END), 0)",
)
_BYTES_NOT_SUMMED = ("0", "0")
_MS_SUMMED = (
    "COALESCE(SUM(a.duration_ms), 0)",
    "COALESCE(SUM(CASE WHEN v.concealed = 1 THEN a.duration_ms END), 0)",
)


_STAGED = "SELECT user_id, asset_id FROM visibility_pending"

_CLEAR_PENDING = "DELETE FROM visibility_pending"

_STAGE_PAIRS = (
    "INSERT INTO visibility_pending (user_id, asset_id)"
    " SELECT DISTINCT user_id, asset_id FROM (<<PAIRS>>)"
)

_CLEAR_PLACES = "DELETE FROM visibility_places"

_STAGED_PLACES = (
    "SELECT DISTINCT s.user_id, l.root_id, l.folder_id"
    "  FROM visibility_pending s JOIN asset_locations l ON l.asset_id = s.asset_id"
)

_DELETE_STAGED = _filled(
    "DELETE FROM viewer_assets WHERE (user_id, asset_id) IN (<<STAGED>>)", STAGED=_STAGED
)

_INSERT_ROWS = "INSERT INTO viewer_assets (user_id, asset_id, concealed)<<ROWS>>"

# CROSS JOIN, so the walk starts from the staged pairs rather than every row the user has.
_STAGED_ROWS = (
    "visibility_pending s CROSS JOIN viewer_assets v"
    " ON v.user_id = s.user_id AND v.asset_id = s.asset_id"
)

# The counts, moved as sets. `WHERE 1 = 1` lets the parser tell an upsert's ON CONFLICT from a
# join's ON.
_STAGED_ROWS_SIZED = _STAGED_ROWS + " JOIN assets a ON a.id = v.asset_id"

_STATS_TAKEN = _filled(
    "UPDATE viewer_stats SET permitted = permitted - d.n, concealed = concealed - d.c,"
    " permitted_bytes = permitted_bytes - d.b, concealed_bytes = concealed_bytes - d.cb"
    " FROM (SELECT v.user_id, COUNT(*) AS n, SUM(v.concealed) AS c,"
    "              <<BYTES>> AS b, <<CONCEALED_BYTES>> AS cb"
    "         FROM <<STAGED_ROWS>> GROUP BY v.user_id) d"
    " WHERE viewer_stats.user_id = d.user_id",
    STAGED_ROWS=_STAGED_ROWS_SIZED,
    BYTES=_BYTES_SUMMED[0],
    CONCEALED_BYTES=_BYTES_SUMMED[1],
)

_STATS_GIVEN = _filled(
    "INSERT INTO viewer_stats (user_id, permitted, concealed, permitted_bytes, concealed_bytes)"
    " SELECT v.user_id, COUNT(*), SUM(v.concealed), <<BYTES>>, <<CONCEALED_BYTES>>"
    "   FROM <<STAGED_ROWS>> WHERE 1 = 1"
    " GROUP BY v.user_id"
    " ON CONFLICT (user_id) DO UPDATE"
    " SET permitted = permitted + excluded.permitted, concealed = concealed + excluded.concealed,"
    " permitted_bytes = permitted_bytes + excluded.permitted_bytes,"
    " concealed_bytes = concealed_bytes + excluded.concealed_bytes",
    STAGED_ROWS=_STAGED_ROWS_SIZED,
    BYTES=_BYTES_SUMMED[0],
    CONCEALED_BYTES=_BYTES_SUMMED[1],
)

# `<<MEMBERS>>` and `<<OBJECT>>` are filled per kind by `_each_kind`.
_COUNTS_TAKEN_ONE = (
    "UPDATE viewer_entity_counts SET permitted = permitted - d.n, concealed = concealed - d.c,"
    " permitted_bytes = permitted_bytes - d.b, concealed_bytes = concealed_bytes - d.cb,"
    " permitted_ms = permitted_ms - d.ms, concealed_ms = concealed_ms - d.cms"
    " FROM (SELECT v.user_id, <<OBJECT>> AS object_id, <<FILES>> AS n, <<CONCEALED>> AS c,"
    "              <<BYTES>> AS b, <<CONCEALED_BYTES>> AS cb,"
    "              <<MS>> AS ms, <<CONCEALED_MS>> AS cms"
    "         FROM <<MEMBERS>>"
    "        GROUP BY v.user_id, <<OBJECT>>) d"
    " WHERE viewer_entity_counts.user_id = d.user_id AND viewer_entity_counts.kind = '<<KIND>>'"
    "   AND viewer_entity_counts.object_id = d.object_id"
)

_COUNTS_GIVEN_ONE = (
    "INSERT INTO viewer_entity_counts (user_id, kind, object_id, permitted, concealed,"
    " permitted_bytes, concealed_bytes, permitted_ms, concealed_ms)"
    " SELECT v.user_id, '<<KIND>>', <<OBJECT>>, <<FILES>>, <<CONCEALED>>,"
    "        <<BYTES>>, <<CONCEALED_BYTES>>, <<MS>>, <<CONCEALED_MS>>"
    "   FROM <<MEMBERS>> WHERE 1 = 1"
    "  GROUP BY v.user_id, <<OBJECT>>"
    " ON CONFLICT (user_id, kind, object_id) DO UPDATE"
    " SET permitted = permitted + excluded.permitted, concealed = concealed + excluded.concealed,"
    " permitted_bytes = permitted_bytes + excluded.permitted_bytes,"
    " concealed_bytes = concealed_bytes + excluded.concealed_bytes,"
    " permitted_ms = permitted_ms + excluded.permitted_ms,"
    " concealed_ms = concealed_ms + excluded.concealed_ms"
)

_COUNTS_EMPTIED = (
    "DELETE FROM viewer_entity_counts WHERE permitted <= 0"
    " AND user_id IN (SELECT DISTINCT user_id FROM visibility_pending)"
)

# Each membership is held once per file, so COUNT(*) over the join counts files.
_PAIR_JOIN = (
    "<<STAGED_ROWS>> JOIN <<TABLE_A>> ma ON ma.asset_id = v.asset_id"
    " JOIN <<TABLE_B>> mb ON mb.asset_id = v.asset_id"
)

_PAIRS_TAKEN_ONE = _filled(
    "UPDATE viewer_pair_counts SET permitted = permitted - d.n, concealed = concealed - d.c"
    " FROM (SELECT v.user_id, ma.<<COLUMN_A>> AS id_a, mb.<<COLUMN_B>> AS id_b,"
    "              COUNT(*) AS n, SUM(v.concealed) AS c"
    "         FROM <<PAIR_JOIN>>"
    "        GROUP BY v.user_id, ma.<<COLUMN_A>>, mb.<<COLUMN_B>>) d"
    " WHERE viewer_pair_counts.user_id = d.user_id"
    "   AND viewer_pair_counts.kind_a = '<<KIND_A>>' AND viewer_pair_counts.id_a = d.id_a"
    "   AND viewer_pair_counts.kind_b = '<<KIND_B>>' AND viewer_pair_counts.id_b = d.id_b",
    PAIR_JOIN=_PAIR_JOIN,
    STAGED_ROWS=_STAGED_ROWS,
)

_PAIRS_GIVEN_ONE = _filled(
    "INSERT INTO viewer_pair_counts (user_id, kind_a, id_a, kind_b, id_b, permitted, concealed)"
    " SELECT v.user_id, '<<KIND_A>>', ma.<<COLUMN_A>>, '<<KIND_B>>', mb.<<COLUMN_B>>,"
    "        COUNT(*), SUM(v.concealed)"
    "   FROM <<PAIR_JOIN>> WHERE 1 = 1"
    "  GROUP BY v.user_id, ma.<<COLUMN_A>>, mb.<<COLUMN_B>>"
    " ON CONFLICT (user_id, kind_a, id_a, kind_b, id_b) DO UPDATE"
    " SET permitted = permitted + excluded.permitted, concealed = concealed + excluded.concealed",
    PAIR_JOIN=_PAIR_JOIN,
    STAGED_ROWS=_STAGED_ROWS,
)

_PAIRS_EMPTIED = (
    "DELETE FROM viewer_pair_counts WHERE permitted <= 0"
    " AND user_id IN (SELECT DISTINCT user_id FROM visibility_pending)"
)


# The partner totals, moved by a pair row crossing nought. NOT EXISTS rather than OR IGNORE: a
# conflict clause in a trigger is replaced by the firing statement's, an upsert.
_PARTNER_ENSURED = (
    "INSERT INTO viewer_partner_counts (user_id, kind, object_id, partner_kind)"
    " SELECT <<ROW>>.user_id, <<ROW>>.kind_<<THIS>>, <<ROW>>.id_<<THIS>>, <<ROW>>.kind_<<THAT>>"
    " WHERE (<<DP>> > 0 OR <<DS>> > 0) AND NOT EXISTS (SELECT 1 FROM viewer_partner_counts"
    " WHERE user_id = <<ROW>>.user_id AND kind = <<ROW>>.kind_<<THIS>>"
    "   AND object_id = <<ROW>>.id_<<THIS>> AND partner_kind = <<ROW>>.kind_<<THAT>>)"
)

_PARTNER_MOVED = (
    "UPDATE viewer_partner_counts SET permitted = permitted + <<DP>>, shown = shown + <<DS>>"
    " WHERE user_id = <<ROW>>.user_id AND kind = <<ROW>>.kind_<<THIS>>"
    "   AND object_id = <<ROW>>.id_<<THIS>> AND partner_kind = <<ROW>>.kind_<<THAT>>"
)

_PARTNER_EMPTIED = (
    "DELETE FROM viewer_partner_counts"
    " WHERE user_id = <<ROW>>.user_id AND kind = <<ROW>>.kind_<<THIS>>"
    "   AND object_id = <<ROW>>.id_<<THIS>> AND partner_kind = <<ROW>>.kind_<<THAT>>"
    "   AND permitted <= 0"
)

_PAIR_ALIVE = "(<<ROW>>.permitted > 0)"
_PAIR_SHOWN = "(<<ROW>>.permitted - <<ROW>>.concealed > 0)"


_PARTNER_COUNT_ROWS = (
    "SELECT user_id, kind, object_id, partner_kind,"
    " SUM(permitted > 0) AS permitted, SUM(permitted - concealed > 0) AS shown"
    " FROM (SELECT user_id, kind_a AS kind, id_a AS object_id, kind_b AS partner_kind,"
    "              permitted, concealed FROM viewer_pair_counts"
    "       UNION ALL"
    "       SELECT user_id, kind_b, id_b, kind_a, permitted, concealed FROM viewer_pair_counts)"
    " WHERE permitted > 0"
    " GROUP BY user_id, kind, object_id, partner_kind"
)

_CLEAR_PARTNER_COUNTS = "DELETE FROM viewer_partner_counts"

_FILL_PARTNER_COUNTS = _filled(
    "INSERT INTO viewer_partner_counts (user_id, kind, object_id, partner_kind, permitted, shown)"
    " <<ROWS>>",
    ROWS=_PARTNER_COUNT_ROWS,
)

_PARTNER_COUNT_DIFFERENCES = _filled(
    "SELECT 'partners missing' AS what,"
    " user_id || '/' || kind || '/' || object_id || '/' || partner_kind,"
    " permitted || '/' || shown, permitted FROM ("
    "SELECT user_id, kind, object_id, partner_kind, permitted, shown FROM (<<ROWS>>)"
    " EXCEPT SELECT user_id, kind, object_id, partner_kind, permitted, shown"
    " FROM viewer_partner_counts)"
    " UNION ALL "
    "SELECT 'partners extra',"
    " user_id || '/' || kind || '/' || object_id || '/' || partner_kind,"
    " permitted || '/' || shown, permitted FROM ("
    "SELECT user_id, kind, object_id, partner_kind, permitted, shown FROM viewer_partner_counts"
    " EXCEPT SELECT user_id, kind, object_id, partner_kind, permitted, shown FROM (<<ROWS>>))",
    ROWS=_PARTNER_COUNT_ROWS,
)


USER = "<<USER>>"

_ONE_USER_EVERY_FILE = "SELECT <<USER>> AS user_id, a.id AS asset_id FROM assets a"

# From the copies, so a copy whose root and folder disagree is decided on its own row.
_EVERY_PLACE = "SELECT DISTINCT root_id, folder_id FROM asset_locations"

_ONE_USER_EVERY_PLACE = _filled(
    "SELECT <<USER>> AS user_id, x.root_id, x.folder_id FROM (<<EVERY_PLACE>>) x",
    EVERY_PLACE=_EVERY_PLACE,
)

_DROP_USER_STATS = "DELETE FROM viewer_stats WHERE user_id = <<USER>>"
_DROP_USER_COUNTS = "DELETE FROM viewer_entity_counts WHERE user_id = <<USER>>"
_DROP_USER_PAIRS = "DELETE FROM viewer_pair_counts WHERE user_id = <<USER>>"
_DROP_USER_ROWS = "DELETE FROM viewer_assets WHERE user_id = <<USER>>"

SCOPE = "<<SCOPE>>"

# Every count from the rows; the scope marker narrows a user's rebuild.
_ENTITY_COUNT_ROWS_ONE = (
    "SELECT v.user_id AS user_id, '<<KIND>>' AS kind, <<OBJECT>> AS object_id,"
    " <<FILES>> AS permitted, <<CONCEALED>> AS concealed,"
    " <<BYTES>> AS permitted_bytes, <<CONCEALED_BYTES>> AS concealed_bytes,"
    " <<MS>> AS permitted_ms, <<CONCEALED_MS>> AS concealed_ms"
    " FROM <<MEMBERS>>"
    " GROUP BY v.user_id, <<OBJECT>>"
)

_EVERY_ROW = "viewer_assets v"


_PAIR_COUNT_ROWS_ONE = (
    "SELECT v.user_id AS user_id, '<<KIND_A>>' AS kind_a, ma.<<COLUMN_A>> AS id_a,"
    " '<<KIND_B>>' AS kind_b, mb.<<COLUMN_B>> AS id_b,"
    " COUNT(*) AS permitted, COALESCE(SUM(v.concealed), 0) AS concealed"
    " FROM viewer_assets v JOIN <<TABLE_A>> ma ON ma.asset_id = v.asset_id"
    " JOIN <<TABLE_B>> mb ON mb.asset_id = v.asset_id<<SCOPE>>"
    " GROUP BY v.user_id, ma.<<COLUMN_A>>, mb.<<COLUMN_B>>"
)

_FILL_PAIR_COUNTS = (
    "INSERT INTO viewer_pair_counts (user_id, kind_a, id_a, kind_b, id_b, permitted, concealed)"
    " <<ROWS>>"
)


_FILL_ENTITY_COUNTS = (
    "INSERT INTO viewer_entity_counts (user_id, kind, object_id, permitted, concealed,"
    " permitted_bytes, concealed_bytes, permitted_ms, concealed_ms) <<ROWS>>"
)

_USER_SCOPE = " WHERE v.user_id = <<USER>>"

_USER_STATS = _filled(
    "INSERT INTO viewer_stats (user_id, permitted, concealed, permitted_bytes, concealed_bytes)"
    " SELECT <<USER>>, COUNT(*), COALESCE(SUM(v.concealed), 0), <<BYTES>>, <<CONCEALED_BYTES>>"
    "   FROM viewer_assets v JOIN assets a ON a.id = v.asset_id WHERE v.user_id = <<USER>>",
    BYTES=_BYTES_SUMMED[0],
    CONCEALED_BYTES=_BYTES_SUMMED[1],
)


# --- the pairs a change can reach ------------------------------------------------------------

_EVERY_USER_ONE_FILE = "SELECT u.id AS user_id, {asset} AS asset_id FROM users u"

_EVERY_USER_FILES_OF_USERNAME = (
    "SELECT DISTINCT u.id AS user_id, aa.asset_id"
    "  FROM users u, asset_usernames aa WHERE aa.username_id = {username}"
)

_EVERY_USER_UNDER_FOLDER = (
    "SELECT DISTINCT u.id AS user_id, l.asset_id"
    "  FROM users u, folder_ancestry an"
    "  JOIN asset_locations l ON l.folder_id = an.folder_id"
    " WHERE an.ancestor_id = {folder}"
)

# Bounded to the subtree of the site whose parent changed. `noqa: S608`: module constants and `NEW`
# only.
_EVERY_USER_UNDER_SITE = (
    "SELECT DISTINCT u.id AS user_id, reached.asset_id"  # noqa: S608
    "  FROM users u, (" + FILES_SITES_REACH.format(ancestors="= {site}") + ") reached"
)

_ONE_USER_ONE_FILE = "SELECT {user} AS user_id, {asset} AS asset_id"

_ONE_USER_IN_ROOT = (
    "SELECT DISTINCT {user} AS user_id, l.asset_id FROM asset_locations l WHERE l.root_id = {root}"
)

_ONE_USER_UNDER_FOLDER = (
    "SELECT DISTINCT {user} AS user_id, l.asset_id"
    "  FROM folder_ancestry an JOIN asset_locations l ON l.folder_id = an.folder_id"
    " WHERE an.ancestor_id = {folder}"
)

# A site reaches the files of every label under it, the same reach the search and the walls read.
_MEMBERS_OF = {
    "tag": "SELECT DISTINCT {user} AS user_id, asset_id FROM asset_tags WHERE tag_id = {object}",
    "person": (
        "SELECT DISTINCT {user} AS user_id, asset_id FROM asset_people WHERE person_id = {object}"
    ),
    "collection": (
        "SELECT DISTINCT {user} AS user_id, asset_id FROM collection_items"
        " WHERE collection_id = {object}"
    ),
    # `noqa: S608`: module constants only, filled with a trigger's own row references.
    "site": (
        "SELECT DISTINCT {user} AS user_id, reached.asset_id"  # noqa: S608
        "  FROM (" + FILES_SITES_REACH.format(ancestors="= {object}") + ") reached"
    ),
    "photo_set": (
        "SELECT DISTINCT {user} AS user_id, asset_id FROM photo_set_items"
        " WHERE photo_set_id = {object}"
    ),
    "song": "SELECT DISTINCT {user} AS user_id, asset_id FROM song_files WHERE song_id = {object}",
}

_HIDING_TABLES = {
    "tag": ("tag_user_state", "tag_id"),
    "person": ("person_user_state", "person_id"),
    "collection": ("collection_user_state", "collection_id"),
    "site": ("site_user_state", "site_id"),
    "photo_set": ("photo_set_user_state", "photo_set_id"),
    "song": ("song_user_state", "song_id"),
}

#: The keys of the two tables whose own columns move a Site's reach, for their BEFORE guards.
_USERNAME_KEYS: tuple[tuple[str, ...], ...] = (
    ("id",),
    ("site_id", "name"),
    ("site_id", "number"),
)
_SITE_KEYS: tuple[tuple[str, ...], ...] = (("id",), ("name",))

_MEMBERSHIP_TABLES = (
    "asset_tags",
    "asset_people",
    "collection_items",
    "asset_usernames",
    "photo_set_items",
    "song_files",
)


# --- the backfill --------------------------------------------------------------------------------

_CLEAR_ANCESTRY = "DELETE FROM folder_ancestry"

# Recursive here only: the triggers keep the table afterwards. UNION, so a loop ends the walk.
_FILL_ANCESTRY = """
WITH RECURSIVE reach(folder_id, root_id) AS (
  SELECT f.id, f.root_id FROM folders f WHERE f.parent_id IS NULL
  UNION
  SELECT f.id, f.root_id
    FROM folders f JOIN reach r ON f.parent_id = r.folder_id AND f.root_id = r.root_id
),
chain(folder_id, ancestor_id, depth) AS (
  SELECT folder_id, folder_id, 0 FROM reach
  UNION ALL
  SELECT c.folder_id, f.parent_id, c.depth + 1
    FROM chain c JOIN folders f ON f.id = c.ancestor_id
   WHERE f.parent_id IS NOT NULL
)
INSERT INTO folder_ancestry (folder_id, ancestor_id, depth)
SELECT folder_id, ancestor_id, depth FROM chain
"""

_CLEAR_ROWS = "DELETE FROM viewer_assets"

_EVERY_USER_EVERY_FILE = "SELECT u.id AS user_id, a.id AS asset_id FROM users u, assets a"

_EVERY_USER_EVERY_PLACE = _filled(
    "SELECT u.id AS user_id, x.root_id, x.folder_id FROM users u, (<<EVERY_PLACE>>) x",
    EVERY_PLACE=_EVERY_PLACE,
)

_CLEAR_ENTITY_COUNTS = "DELETE FROM viewer_entity_counts"

_CLEAR_PAIR_COUNTS = "DELETE FROM viewer_pair_counts"

_DROP_KIND_COUNTS = "DELETE FROM viewer_entity_counts WHERE kind = '<<KIND>>'"
_DROP_KIND_PAIRS = "DELETE FROM viewer_pair_counts WHERE kind_a = '<<KIND>>' OR kind_b = '<<KIND>>'"


_CLEAR_STATS = "DELETE FROM viewer_stats"

_FILL_STATS = """
INSERT INTO viewer_stats (user_id, permitted, concealed, permitted_bytes, concealed_bytes)
SELECT u.id, COUNT(v.asset_id), COALESCE(SUM(v.concealed), 0),
       COALESCE(SUM(a.size_bytes), 0),
       COALESCE(SUM(CASE WHEN v.concealed = 1 THEN a.size_bytes END), 0)
  FROM users u LEFT JOIN viewer_assets v ON v.user_id = u.id
  LEFT JOIN assets a ON a.id = v.asset_id
 GROUP BY u.id
"""

_TRIGGERS_PRESENT = (
    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'vis_%'"
)

_DROP_EXPECTED_ROWS = "DROP TABLE IF EXISTS visibility_expected"

_DIFFERENCES = """
SELECT 'missing' AS what, user_id, asset_id, concealed FROM (
  SELECT user_id, asset_id, concealed FROM visibility_expected
  EXCEPT
  SELECT user_id, asset_id, concealed FROM viewer_assets
)
UNION ALL
SELECT 'extra' AS what, user_id, asset_id, concealed FROM (
  SELECT user_id, asset_id, concealed FROM viewer_assets
  EXCEPT
  SELECT user_id, asset_id, concealed FROM visibility_expected
)
"""

# Subqueries on both sides of each EXCEPT: compound operators bind left to right.
_ENTITY_COUNT_DIFFERENCES = _filled(
    "SELECT 'count missing' AS what, user_id || '/' || kind || '/' || object_id,"
    " <<SHOWN>>, permitted FROM ("
    "SELECT <<COLUMNS>> FROM (<<ROWS>>)"
    " EXCEPT SELECT <<COLUMNS>> FROM viewer_entity_counts)"
    " UNION ALL "
    "SELECT 'count extra', user_id || '/' || kind || '/' || object_id,"
    " <<SHOWN>>, permitted FROM ("
    "SELECT <<COLUMNS>> FROM viewer_entity_counts EXCEPT SELECT <<COLUMNS>> FROM (<<ROWS>>))",
    COLUMNS="user_id, kind, object_id, permitted, concealed, permitted_bytes, concealed_bytes,"
    " permitted_ms, concealed_ms",
    SHOWN="permitted || '/' || concealed || '/' || permitted_bytes || '/' || concealed_bytes"
    " || '/' || permitted_ms || '/' || concealed_ms",
)

_PAIR_COUNT_DIFFERENCES = (
    "SELECT 'pair missing' AS what,"
    " user_id || '/' || kind_a || '/' || id_a || '/' || kind_b || '/' || id_b,"
    " permitted || '/' || concealed, permitted FROM ("
    "SELECT user_id, kind_a, id_a, kind_b, id_b, permitted, concealed FROM (<<ROWS>>)"
    " EXCEPT SELECT user_id, kind_a, id_a, kind_b, id_b, permitted, concealed"
    " FROM viewer_pair_counts)"
    " UNION ALL "
    "SELECT 'pair extra',"
    " user_id || '/' || kind_a || '/' || id_a || '/' || kind_b || '/' || id_b,"
    " permitted || '/' || concealed, permitted FROM ("
    "SELECT user_id, kind_a, id_a, kind_b, id_b, permitted, concealed FROM viewer_pair_counts"
    " EXCEPT SELECT user_id, kind_a, id_a, kind_b, id_b, permitted, concealed FROM (<<ROWS>>))"
)

_STATS_DIFFERENCES = """
SELECT 'stats' AS what, u.id AS user_id, NULL AS asset_id, NULL AS concealed
  FROM users u
  LEFT JOIN viewer_stats s ON s.user_id = u.id
 WHERE COALESCE(s.permitted, 0) != (SELECT COUNT(*) FROM viewer_assets v WHERE v.user_id = u.id)
    OR COALESCE(s.concealed, 0) != (SELECT COALESCE(SUM(concealed), 0) FROM viewer_assets v
                                     WHERE v.user_id = u.id)
    OR COALESCE(s.permitted_bytes, 0) != (SELECT COALESCE(SUM(a.size_bytes), 0)
                                            FROM viewer_assets v JOIN assets a ON a.id = v.asset_id
                                           WHERE v.user_id = u.id)
    OR COALESCE(s.concealed_bytes, 0) != (SELECT COALESCE(SUM(a.size_bytes), 0)
                                            FROM viewer_assets v JOIN assets a ON a.id = v.asset_id
                                           WHERE v.user_id = u.id AND v.concealed = 1)
"""

_ANCESTRY_DIFFERENCES = """
WITH RECURSIVE reach(folder_id, root_id) AS (
  SELECT f.id, f.root_id FROM folders f WHERE f.parent_id IS NULL
  UNION
  SELECT f.id, f.root_id
    FROM folders f JOIN reach r ON f.parent_id = r.folder_id AND f.root_id = r.root_id
),
chain(folder_id, ancestor_id, depth) AS (
  SELECT folder_id, folder_id, 0 FROM reach
  UNION ALL
  SELECT c.folder_id, f.parent_id, c.depth + 1
    FROM chain c JOIN folders f ON f.id = c.ancestor_id
   WHERE f.parent_id IS NOT NULL
),
expected(folder_id, ancestor_id, depth) AS (SELECT folder_id, ancestor_id, depth FROM chain),
stored(folder_id, ancestor_id, depth) AS (SELECT folder_id, ancestor_id, depth FROM folder_ancestry)
SELECT 'ancestry missing' AS what, folder_id AS user_id, ancestor_id AS asset_id, depth AS concealed
  FROM (SELECT * FROM expected EXCEPT SELECT * FROM stored)
UNION ALL
SELECT 'ancestry extra', folder_id, ancestor_id, depth
  FROM (SELECT * FROM stored EXCEPT SELECT * FROM expected)
"""


_ADD_COLUMN = "ALTER TABLE {table} ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"

_ANCESTRY_CHECK = _filled("SELECT 1 FROM (<<DIFF>>) LIMIT 1", DIFF=_ANCESTRY_DIFFERENCES)
