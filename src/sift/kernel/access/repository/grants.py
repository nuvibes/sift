# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who a viewer is, what has been granted to them, and where a grant came from."""

from __future__ import annotations

from sift.kernel.access.sites import SITE_REACH
from sift.kernel.access.viewer import ObjectType
from sift.kernel.access.visibility import (
    ITEM_BITS,
    LOGICAL_BITS,
    PLACE_BITS_OF_COPIES,
    PLACE_REACHED_JOIN,
    PLACE_RESTRICTED,
    PLACE_SHARED,
    ladder_admits,
    ladder_restricts,
)
from sift.kernel.db import point_read
from sift.kernel.sql_splice import splice

# Rebuilt on every request, one primary-key seek: a point read.
_USER_BY_ID = point_read(
    "access.user_by_id",
    "SELECT id, role, cache_stamp FROM users WHERE id = ? AND disabled = 0",
)

# One admin, for a pass that runs as nobody: the oldest enabled, so the answer does not wander
# between runs.
_AN_ADMIN = "SELECT id FROM users WHERE role = 'admin' AND disabled = 0 ORDER BY id LIMIT 1"

# Two conflict targets: a global grant's NULL object slips the full UNIQUE and collides on the
# partial index.
_INSERT_GRANT = """
INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(object_type, object_id, subject_user_id, effect) DO UPDATE SET id = id
ON CONFLICT(object_type, subject_user_id, effect) WHERE object_id IS NULL DO UPDATE SET id = id
RETURNING *
"""

_DELETE_GRANT = """
DELETE FROM acl_grants
 WHERE object_type = ? AND object_id IS ? AND subject_user_id = ? AND effect = ?
"""

_DELETE_OBJECT_GRANTS = (
    "DELETE FROM acl_grants WHERE object_type = ? AND object_id IS ? RETURNING subject_user_id"
)

_DELETE_ITEM_GRANTS = "DELETE FROM acl_grants WHERE object_type = 'item' AND object_id IN (?*)"

_GRANTS_OF_USER = "SELECT * FROM acl_grants WHERE subject_user_id = ? ORDER BY id"

#: Every grant made on one object, whoever it was made to: who this is shared with.
_GRANTS_ON_OBJECT = """
SELECT * FROM acl_grants
 WHERE object_type = ? AND object_id IS ?
 ORDER BY id
"""

#: Which of these objects carry a grant at all, one row per object.
_GRANT_MARKS = """
SELECT object_id,
       MAX(effect = 'share')    AS shared,
       MAX(effect = 'restrict') AS restricted
  FROM acl_grants
 WHERE object_type = ? AND object_id IN (?*)
 GROUP BY object_id
"""

#: Whether anything was ever shared, the cheap answer for an install with no guests.
_ANY_GRANT_AT_ALL = point_read("access.any_grant", "SELECT 1 FROM acl_grants LIMIT 1")

#: What each file's sharing comes to, counting everything above it: the ladder run per user and then
#: collapsed, with whether the decision was made on the file itself. Hiding is not a grant; a
#: location whose root is gone is dropped.
_EFFECTIVE_ASSET_MARKS = splice(
    """
WITH
wanted(asset_id) AS (
  SELECT value FROM json_each(:asset_ids)
),
subjects(subject) AS (
  SELECT DISTINCT subject_user_id FROM acl_grants
),
-- Every user with a grant, against every file asked about. The bits are the same three the
-- stored verdict is built from (where the copies sit, what the file belongs to, the item's own
-- grant), spliced from the one place they are written.
-- MATERIALIZED, so each bit is worked out once per user and file: flattened into the ladder below,
-- every mention of a bit ran its subquery again.
standing(asset_id, user_id, ph, lo, it) AS MATERIALIZED (
  SELECT p.asset_id, p.user_id,
         COALESCE({{PHYSICAL_BITS}}, 0),
         COALESCE({{LOGICAL_BITS}}, 0),
         COALESCE({{ITEM_BITS}}, 0)
    FROM (SELECT s.subject AS user_id, w.asset_id FROM subjects s CROSS JOIN wanted w) p
)
-- The ladder itself, spliced from the one place it is written (`visibility.LADDER_ADMITS`), and
-- whether a restrict is what decided it, never a copy that goes on drawing an old rule.
SELECT w.asset_id AS object_id,
       COALESCE(MAX({{LADDER_ADMITS}}), 0) AS shared,
       COALESCE(MAX({{LADDER_RESTRICTS}}), 0) AS restricted,
       COALESCE(MAX(st.it & 1), 0) AS shared_here,
       COALESCE(MAX((st.it & 2) / 2), 0) AS restricted_here
  FROM wanted w
  LEFT JOIN standing st ON st.asset_id = w.asset_id
 GROUP BY w.asset_id
""",
    PHYSICAL_BITS=PLACE_BITS_OF_COPIES,
    LOGICAL_BITS=LOGICAL_BITS,
    ITEM_BITS=ITEM_BITS,
    LADDER_ADMITS=ladder_admits("st"),
    LADDER_RESTRICTS=ladder_restricts("st"),
)

#: The same question asked of folders, mirroring `_VISIBLE_FOLDERS`.
_EFFECTIVE_FOLDER_MARKS = splice(
    """
WITH
wanted(folder_id) AS (
  SELECT value FROM json_each(:folder_ids)
),
subjects(subject) AS (
  SELECT DISTINCT subject_user_id FROM acl_grants
),
standing(folder_id, restricted, shared) AS (
  SELECT l.folder_id, {{PLACE_RESTRICTED}}, {{PLACE_SHARED}}
    FROM (SELECT s.subject AS user_id FROM subjects s) p
   CROSS JOIN (SELECT f.id AS folder_id, f.root_id FROM folders f
                WHERE f.id IN (SELECT folder_id FROM wanted)) l{{PLACE_REACHED_JOIN}}
)
SELECT w.folder_id AS object_id,
       COALESCE(MAX(CASE WHEN st.restricted = 0 AND st.shared = 1 THEN 1 ELSE 0 END), 0) AS shared,
       COALESCE(MAX(st.restricted), 0)  AS restricted,
       COALESCE(MAX(own.effect = 'share'), 0)    AS shared_here,
       COALESCE(MAX(own.effect = 'restrict'), 0) AS restricted_here
  FROM wanted w
  LEFT JOIN standing st ON st.folder_id = w.folder_id
  LEFT JOIN acl_grants own
         ON own.object_type = 'folder' AND own.object_id = w.folder_id
 GROUP BY w.folder_id
""",
    PLACE_RESTRICTED=PLACE_RESTRICTED,
    PLACE_SHARED=PLACE_SHARED,
    PLACE_REACHED_JOIN=PLACE_REACHED_JOIN,
)

#: Everything a grant could be made on that reaches a file, per file, over a JSON array of files.
#: DISTINCT on the root and site arms, so one reason is not listed twice.
TOUCHING_ASSETS = splice(
    """
wanted(asset_id) AS (
  SELECT value FROM json_each(:asset_ids)
),
-- Every folder each file sits in, and every folder above those.
chain(asset_id, folder_id) AS (
  SELECT l.asset_id, l.folder_id FROM asset_locations l
    JOIN wanted w ON w.asset_id = l.asset_id
   WHERE l.folder_id IS NOT NULL
  UNION
  SELECT c.asset_id, f.parent_id FROM folders f JOIN chain c ON f.id = c.folder_id
   WHERE f.parent_id IS NOT NULL
),
touching(asset_id, object_type, object_id, name) AS (
  SELECT w.asset_id, 'item', w.asset_id, NULL FROM wanted w
  UNION ALL
  SELECT c.asset_id, 'folder', f.id, f.name FROM folders f JOIN chain c ON c.folder_id = f.id
  UNION ALL
  SELECT DISTINCT l.asset_id, 'root', r.id, r.name FROM asset_locations l
    JOIN wanted w ON w.asset_id = l.asset_id
    JOIN library_roots r ON r.id = l.root_id
  UNION ALL
  SELECT at2.asset_id, 'tag', t.id, t.name FROM tags t
    JOIN asset_tags at2 ON at2.tag_id = t.id
    JOIN wanted w ON w.asset_id = at2.asset_id
  UNION ALL
  SELECT ap.asset_id, 'person', p.id, p.name FROM people p
    JOIN asset_people ap ON ap.person_id = p.id
    JOIN wanted w ON w.asset_id = ap.asset_id
  UNION ALL
  SELECT ci.asset_id, 'collection', c.id, c.name FROM collections c
    JOIN collection_items ci ON ci.collection_id = c.id
    JOIN wanted w ON w.asset_id = ci.asset_id
  UNION ALL
  SELECT DISTINCT aa.asset_id, 'site', pl.id, pl.name
    FROM asset_usernames aa
    JOIN wanted w ON w.asset_id = aa.asset_id
    JOIN usernames ac ON ac.id = aa.username_id
    JOIN ({{SITE_REACH}}) reach ON reach.site_id = ac.site_id
    JOIN sites pl ON pl.id = reach.ancestor_id
  UNION ALL
  SELECT psi.asset_id, 'photo_set', ps.id, ps.name FROM photo_sets ps
    JOIN photo_set_items psi ON psi.photo_set_id = ps.id
    JOIN wanted w ON w.asset_id = psi.asset_id
  UNION ALL
  SELECT sf.asset_id, 'song', sg.id, sg.name FROM songs sg
    JOIN song_files sf ON sf.song_id = sg.id
    JOIN wanted w ON w.asset_id = sf.asset_id
  UNION ALL
  SELECT w.asset_id, 'global', NULL, NULL FROM wanted w
)
""",
    SITE_REACH=SITE_REACH,
)

#: Every grant that reaches one file and what it was made on: every arm `LOGICAL_BITS` reads.
_GRANT_SOURCES_FOR_ASSET = splice(
    """
WITH RECURSIVE
{{TOUCHING}}
SELECT g.subject_user_id, u.username, g.effect,
       t.object_type AS source_type, t.object_id AS source_id, t.name AS source_name
  FROM acl_grants g
  JOIN touching t ON t.object_type = g.object_type AND t.object_id IS g.object_id
  JOIN users u ON u.id = g.subject_user_id
 ORDER BY u.username, g.effect, t.object_type, t.name
""",
    TOUCHING=TOUCHING_ASSETS,
)

#: The six kinds of thing whose reach is explained through the files under them.
ENTITY_REACH_KINDS = frozenset(
    {
        ObjectType.TAG,
        ObjectType.PERSON,
        ObjectType.COLLECTION,
        ObjectType.PHOTO_SET,
        ObjectType.SITE,
        ObjectType.SONG,
    }
)

#: How many of an entity's files are looked at to work out why it is reachable; the answer says
#: whether that was all.
ENTITY_REACH_CEILING = 200

#: The files under one entity one user can reach, a page of them, driven from the membership.
#: Ordered by id.
_FILES_UNDER_ENTITY_FOR_USER = splice(
    """
SELECT m.asset_id FROM asset_tags m
  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = :subject
 WHERE :object_type = 'tag' AND m.tag_id = :object_id
UNION
SELECT m.asset_id FROM asset_people m
  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = :subject
 WHERE :object_type = 'person' AND m.person_id = :object_id
UNION
SELECT m.asset_id FROM collection_items m
  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = :subject
 WHERE :object_type = 'collection' AND m.collection_id = :object_id
UNION
SELECT m.asset_id FROM photo_set_items m
  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = :subject
 WHERE :object_type = 'photo_set' AND m.photo_set_id = :object_id
UNION
SELECT m.asset_id FROM song_files m
  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = :subject
 WHERE :object_type = 'song' AND m.song_id = :object_id
UNION
SELECT m.asset_id FROM asset_usernames m
  JOIN usernames ac ON ac.id = m.username_id
  JOIN ({{SITE_REACH}}) reach ON reach.site_id = ac.site_id
  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = :subject
 WHERE :object_type = 'site' AND reach.ancestor_id = :object_id
 ORDER BY 1
 LIMIT :ceiling
""",
    SITE_REACH=SITE_REACH,
)

#: Why those files are reachable, one row per reason. Shares only: every file here is one the user
#: sees.
_REACH_THROUGH_FILES = splice(
    """
WITH RECURSIVE
{{TOUCHING}}
SELECT t.object_type AS source_type, t.object_id AS source_id, t.name AS source_name,
       COUNT(DISTINCT t.asset_id) AS files
  FROM acl_grants g
  JOIN touching t ON t.object_type = g.object_type AND t.object_id IS g.object_id
 WHERE g.subject_user_id = :subject AND g.effect = 'share'
 GROUP BY t.object_type, t.object_id, t.name
 ORDER BY files DESC, t.object_type, t.name
""",
    TOUCHING=TOUCHING_ASSETS,
)

#: The grants that reach something that is not a file: its own, the global one, and for a site the
#: networks above it. The type is compared, never interpolated.
_GRANT_SOURCES_FOR_OBJECT = splice(
    """
SELECT g.subject_user_id, u.username, g.effect,
       g.object_type AS source_type, g.object_id AS source_id, NULL AS source_name
  FROM acl_grants g
  JOIN users u ON u.id = g.subject_user_id
 WHERE :object_type <> 'site'
   AND ((g.object_type = :object_type AND g.object_id IS :object_id)
     OR (g.object_type = 'global' AND g.object_id IS NULL))
UNION ALL
SELECT g.subject_user_id, u.username, g.effect,
       g.object_type, g.object_id, pl.name
  FROM ({{SITE_REACH}}) reach
  JOIN sites pl ON pl.id = reach.ancestor_id
  JOIN acl_grants g ON g.object_type = 'site' AND g.object_id = pl.id
  JOIN users u ON u.id = g.subject_user_id
 WHERE :object_type = 'site' AND reach.site_id = :object_id
UNION ALL
SELECT g.subject_user_id, u.username, g.effect, g.object_type, g.object_id, NULL
  FROM acl_grants g
  JOIN users u ON u.id = g.subject_user_id
 WHERE :object_type = 'site'
   AND g.object_type = 'global' AND g.object_id IS NULL
 ORDER BY username, effect, source_type, source_name
""",
    SITE_REACH=SITE_REACH,
)

_GRANT_SOURCES_FOR_FOLDER = """
WITH RECURSIVE
chain(folder_id) AS (
  SELECT :folder_id
  UNION
  SELECT f.parent_id FROM folders f JOIN chain c ON f.id = c.folder_id
   WHERE f.parent_id IS NOT NULL
),
touching(object_type, object_id, name) AS (
  SELECT 'folder', f.id, f.name FROM folders f JOIN chain c ON c.folder_id = f.id
  UNION ALL
  SELECT 'root', r.id, r.name FROM library_roots r
   WHERE r.id IN (SELECT root_id FROM folders WHERE id = :folder_id)
  UNION ALL
  SELECT 'global', NULL, NULL
)
SELECT g.subject_user_id, u.username, g.effect,
       t.object_type AS source_type, t.object_id AS source_id, t.name AS source_name
  FROM acl_grants g
  JOIN touching t ON t.object_type = g.object_type AND t.object_id IS g.object_id
  JOIN users u ON u.id = g.subject_user_id
 ORDER BY u.username, g.effect, t.object_type, t.name
"""

#: Everything whose vault flag is concealing one file, named, over every location's chain: an
#: explanation, not the rule.
_VAULT_SOURCES_FOR_ASSET = splice(
    """
WITH RECURSIVE
chain(folder_id) AS (
  SELECT l.folder_id FROM asset_locations l
   WHERE l.asset_id = :asset_id AND l.folder_id IS NOT NULL
  UNION
  SELECT f.parent_id FROM folders f JOIN chain c ON f.id = c.folder_id
   WHERE f.parent_id IS NOT NULL
)
SELECT 'item' AS source_type, a.id AS source_id, NULL AS source_name, 1 AS here
  FROM assets a
  JOIN asset_user_state h ON h.asset_id = a.id
 WHERE a.id = :asset_id AND h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'folder', f.id, f.name, 0
  FROM folders f
  JOIN chain c ON c.folder_id = f.id
  JOIN folder_user_state h ON h.folder_id = f.id
 WHERE h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'root', r.id, r.name, 0
  FROM library_roots r
  JOIN root_user_state h ON h.root_id = r.id
 WHERE h.user_id = :viewer AND h.hidden = 1
   AND r.id IN (SELECT root_id FROM asset_locations WHERE asset_id = :asset_id)
UNION ALL
SELECT 'person', p.id, p.name, 0
  FROM people p
  JOIN asset_people ap ON ap.person_id = p.id AND ap.asset_id = :asset_id
  JOIN person_user_state h ON h.person_id = p.id
 WHERE h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'collection', c.id, c.name, 0
  FROM collections c
  JOIN collection_items ci ON ci.collection_id = c.id AND ci.asset_id = :asset_id
  JOIN collection_user_state h ON h.collection_id = c.id
 WHERE h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'tag', t.id, t.name, 0
  FROM tags t
  JOIN asset_tags at2 ON at2.tag_id = t.id AND at2.asset_id = :asset_id
  JOIN tag_user_state h ON h.tag_id = t.id
 WHERE h.user_id = :viewer AND h.hidden = 1
UNION ALL
-- THE SITE, or any site ABOVE it: a network hides what its labels released, so the site whose
-- flag is set is often not the one the file is filed under. Asking only about the file's own site
-- would bring a file concealed by a hidden network back with no reason beside it, on the panel
-- that exists to say "why can I not see this". The walk is the same fragment the rule itself
-- reads.
--
-- DISTINCT on this arm alone: a file filed under two labels of one hidden network reaches that
-- network twice (as two usernames on one site would), and a panel listing the same reason twice
-- reads as two separate reasons.
SELECT DISTINCT 'site', pl.id, pl.name, 0
  FROM asset_usernames aa
  JOIN usernames ac ON ac.id = aa.username_id
  JOIN ({{SITE_REACH}}) reach ON reach.site_id = ac.site_id
  JOIN sites pl ON pl.id = reach.ancestor_id
  JOIN site_user_state h ON h.site_id = pl.id
 WHERE aa.asset_id = :asset_id AND h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'photo_set', ps.id, ps.name, 0
  FROM photo_sets ps
  JOIN photo_set_items psi ON psi.photo_set_id = ps.id AND psi.asset_id = :asset_id
  JOIN photo_set_user_state h ON h.photo_set_id = ps.id
 WHERE h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'song', sg.id, sg.name, 0
  FROM songs sg
  JOIN song_files sf ON sf.song_id = sg.id AND sf.asset_id = :asset_id
  JOIN song_user_state h ON h.song_id = sg.id
 WHERE h.user_id = :viewer AND h.hidden = 1
 ORDER BY here DESC, source_type, source_name
""",
    SITE_REACH=SITE_REACH,
)

_VAULT_SOURCES_FOR_FOLDER = """
WITH RECURSIVE
chain(folder_id) AS (
  SELECT :folder_id
  UNION
  SELECT f.parent_id FROM folders f JOIN chain c ON f.id = c.folder_id
   WHERE f.parent_id IS NOT NULL
)
SELECT 'folder' AS source_type, f.id AS source_id, f.name AS source_name,
       CASE WHEN f.id = :folder_id THEN 1 ELSE 0 END AS here
  FROM folders f
  JOIN chain c ON c.folder_id = f.id
  JOIN folder_user_state h ON h.folder_id = f.id
 WHERE h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'root', r.id, r.name, 0
  FROM library_roots r
  JOIN root_user_state h ON h.root_id = r.id
 WHERE h.user_id = :viewer AND h.hidden = 1
   AND r.id IN (SELECT root_id FROM folders WHERE id = :folder_id)
 ORDER BY here DESC, source_type, source_name
"""

#: A person, a collection or a library carries its own flag; a site also its networks'.
_VAULT_SOURCES_FOR_OBJECT = splice(
    """
SELECT 'person' AS source_type, p.id AS source_id, p.name AS source_name, 1 AS here
  FROM people p JOIN person_user_state h ON h.person_id = p.id
 WHERE :object_type = 'person' AND p.id = :object_id
   AND h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'collection', c.id, c.name, 1
  FROM collections c JOIN collection_user_state h ON h.collection_id = c.id
 WHERE :object_type = 'collection' AND c.id = :object_id
   AND h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'root', r.id, r.name, 1
  FROM library_roots r JOIN root_user_state h ON h.root_id = r.id
 WHERE :object_type = 'root' AND r.id = :object_id
   AND h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'tag', t.id, t.name, 1
  FROM tags t JOIN tag_user_state h ON h.tag_id = t.id
 WHERE :object_type = 'tag' AND t.id = :object_id
   AND h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'site', pl.id, pl.name, CASE WHEN pl.id = :object_id THEN 1 ELSE 0 END
  FROM ({{SITE_REACH}}) reach
  JOIN sites pl ON pl.id = reach.ancestor_id
  JOIN site_user_state h ON h.site_id = pl.id
 WHERE :object_type = 'site' AND reach.site_id = :object_id
   AND h.user_id = :viewer AND h.hidden = 1
UNION ALL
SELECT 'song', sg.id, sg.name, 1
  FROM songs sg JOIN song_user_state h ON h.song_id = sg.id
 WHERE :object_type = 'song' AND sg.id = :object_id
   AND h.user_id = :viewer AND h.hidden = 1
""",
    SITE_REACH=SITE_REACH,
)

#: Who can see this, one row per user, read off the stored verdict so it agrees with each user's
#: next request. EXISTS per arm; a disabled user's flag rides along. Ordered by id.
_REACH_OF_OBJECT = splice(
    """
SELECT u.id AS user_id, u.username, u.role, u.disabled,
       CASE WHEN u.disabled = 1 THEN 0 WHEN
         EXISTS (SELECT 1 FROM viewer_assets v
                  WHERE :object_type = 'item'
                    AND v.user_id = u.id AND v.asset_id = :object_id)
      OR EXISTS (SELECT 1 FROM asset_tags m
                  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = u.id
                 WHERE :object_type = 'tag' AND m.tag_id = :object_id)
      OR EXISTS (SELECT 1 FROM asset_people m
                  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = u.id
                 WHERE :object_type = 'person' AND m.person_id = :object_id)
      OR EXISTS (SELECT 1 FROM collection_items m
                  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = u.id
                 WHERE :object_type = 'collection' AND m.collection_id = :object_id)
      OR EXISTS (SELECT 1 FROM photo_set_items m
                  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = u.id
                 WHERE :object_type = 'photo_set' AND m.photo_set_id = :object_id)
      OR EXISTS (SELECT 1 FROM song_files m
                  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = u.id
                 WHERE :object_type = 'song' AND m.song_id = :object_id)
      OR EXISTS (SELECT 1 FROM asset_usernames m
                  JOIN usernames ac ON ac.id = m.username_id
                  JOIN ({{SITE_REACH}}) reach ON reach.site_id = ac.site_id
                  JOIN viewer_assets v ON v.asset_id = m.asset_id AND v.user_id = u.id
                 WHERE :object_type = 'site' AND reach.ancestor_id = :object_id)
      OR EXISTS (SELECT 1 FROM asset_locations l
                  JOIN folder_ancestry fa ON fa.folder_id = l.folder_id
                                         AND fa.ancestor_id = :object_id
                  JOIN viewer_assets v ON v.asset_id = l.asset_id AND v.user_id = u.id
                 WHERE :object_type = 'folder')
      OR EXISTS (SELECT 1 FROM asset_locations l
                  JOIN viewer_assets v ON v.asset_id = l.asset_id AND v.user_id = u.id
                 WHERE :object_type = 'root' AND l.root_id = :object_id)
      OR EXISTS (SELECT 1 FROM viewer_assets v
                  WHERE :object_type = 'global' AND v.user_id = u.id)
       THEN 1 ELSE 0 END AS sees
  FROM users u
 ORDER BY u.id
""",
    SITE_REACH=SITE_REACH,
)
