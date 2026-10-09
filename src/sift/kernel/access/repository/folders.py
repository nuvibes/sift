# SPDX-License-Identifier: AGPL-3.0-or-later
"""Folders a viewer may see, and the counts beside them, spliced from the verdict's place rules."""

from __future__ import annotations

from sift.kernel.access.visibility import (
    PLACE_REACHED_JOIN,
    PLACE_RESTRICTED,
    PLACE_SHARED,
    PLACE_VAULTED,
)
from sift.kernel.sql_splice import splice

# The physical half of the resolver, on folders: no item grant and no tag, so those steps do not
# apply.
_VISIBLE_FOLDERS = splice(
    """
SELECT f.*,
       -- This folder's OWN mark for this viewer, as against the answer including everything above
       -- it. Both are drawn, and they are not the same thing: one is something you did and can
       -- undo where you are looking, the other is a consequence of a folder further up.
       CASE WHEN EXISTS (SELECT 1 FROM folder_user_state h
                          WHERE h.folder_id = f.id AND h.user_id = :viewer AND h.hidden = 1)
            THEN 1 ELSE 0 END AS vault,
       {{PLACE_VAULTED}} AS concealed
  FROM folders f
  -- The folder standing in for a copy, so the place rules read it as they read a file's place.
  JOIN (SELECT :viewer AS user_id) p
  JOIN (SELECT id AS folder_id, root_id FROM folders) l ON l.folder_id = f.id{{PLACE_REACHED_JOIN}}
 WHERE (:folder_id IS NULL OR f.id = :folder_id)
   -- A SET of folders, for a caller holding a list of ids. NULL means no narrowing, exactly as the
   -- single form above reads it. `json_each` over a bound array, so the statement stays one
   -- constant string and the caller's own list is what bounds it.
   AND (:folder_ids IS NULL OR f.id IN (SELECT value FROM json_each(:folder_ids)))
   AND (:parent_id IS NULL OR f.parent_id = :parent_id)
   AND (:root_id   IS NULL OR f.root_id = :root_id)
   AND (:top_level = 0 OR f.parent_id IS NULL)
   AND (
     :is_admin = 1
     OR ({{PLACE_RESTRICTED}} = 0 AND {{PLACE_SHARED}} = 1)
   )
   AND (:reveal = 1 OR {{PLACE_VAULTED}} = 0)
 ORDER BY f.rel_path
""",
    PLACE_VAULTED=PLACE_VAULTED,
    PLACE_REACHED_JOIN=PLACE_REACHED_JOIN,
    PLACE_RESTRICTED=PLACE_RESTRICTED,
    PLACE_SHARED=PLACE_SHARED,
)

# Read off the stored verdict, with the bytes and the newest arrival in the same pass so they
# describe one world. Summed over copies; `newest` stays NULL for an empty folder. CROSS JOIN starts
# the walk at the folder, not at the user's every row.
_COUNT_FILES_UNDER = """
SELECT COUNT(*) AS files, COALESCE(SUM(a.size_bytes), 0) AS bytes,
       MAX(a.added_at) AS newest
  FROM folder_ancestry an
  CROSS JOIN asset_locations l ON l.folder_id = an.folder_id
  CROSS JOIN viewer_assets v ON v.asset_id = l.asset_id AND v.user_id = :viewer
  CROSS JOIN assets a ON a.id = l.asset_id
 WHERE an.ancestor_id = :folder_id
   AND l.status = 'present'
   AND (:reveal = 1 OR v.concealed = 0)
"""

# Kept per user and folder by the verdict's triggers, so any folder costs one row.
_FOLDER_FILES = "permitted - CASE WHEN :reveal = 1 THEN 0 ELSE concealed END"

_FOLDER_FILE_COUNT = splice(
    """
SELECT {{FILES}} AS files
  FROM viewer_entity_counts
 WHERE user_id = :viewer AND kind = 'folder' AND object_id = :folder_id
""",
    FILES=_FOLDER_FILES,
)

_FOLDER_FILE_COUNTS = splice(
    """
SELECT object_id AS folder_id, {{FILES}} AS files
  FROM viewer_entity_counts
 WHERE user_id = :viewer AND kind = 'folder'
   AND object_id IN (SELECT value FROM json_each(:folder_ids))
   AND {{FILES}} > 0
""",
    FILES=_FOLDER_FILES,
)

# The newest file under each folder the user may see; asked only for the folders on a screen.
_FOLDER_COVERS = """
SELECT w.value AS folder_id,
       (SELECT l.asset_id
          FROM folder_ancestry an
          CROSS JOIN asset_locations l ON l.folder_id = an.folder_id
          CROSS JOIN viewer_assets v ON v.asset_id = l.asset_id AND v.user_id = :viewer
          CROSS JOIN assets a ON a.id = l.asset_id
         WHERE an.ancestor_id = w.value AND (:reveal = 1 OR v.concealed = 0)
         ORDER BY a.added_at DESC, a.id DESC LIMIT 1) AS cover
  FROM json_each(:folder_ids) w
"""

_COUNT_FOLDERS_UNDER = """
SELECT COUNT(*) - 1 AS folders
  FROM folder_ancestry an
 WHERE an.ancestor_id = :folder_id
"""


_SET_FOLDER_VAULT = """
INSERT INTO folder_user_state (folder_id, user_id, hidden, hidden_at, updated_at)
VALUES (:folder_id, :viewer, :hidden, :hidden_at, :now)
ON CONFLICT(folder_id, user_id) DO UPDATE SET
  hidden = excluded.hidden, hidden_at = excluded.hidden_at, updated_at = excluded.updated_at
"""
