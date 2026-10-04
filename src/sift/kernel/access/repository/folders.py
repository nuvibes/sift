# SPDX-License-Identifier: AGPL-3.0-or-later
"""Folders a viewer may see, and the counts shown beside them.

A folder is WHERE a file is, and it is read with the same place rules the stored verdict is built
from (`sift.kernel.access.visibility`): nearest grant wins on the way down, a restrict is never
undone by inheritance, and a folder the walk from its root never reached is missing rather than
open. The rules are spliced from that module rather than written again here.

`_COUNT_FILES_UNDER` is here rather than with the file query because it answers a question about a
folder. It carries the concealment rule for the reason the count exists at all: told a folder holds
twelve files while nine are shown, the person asking has learned exactly how much is being kept from
them.
"""

from __future__ import annotations

from sift.kernel.access.visibility import (
    PLACE_REACHED_JOIN,
    PLACE_RESTRICTED,
    PLACE_SHARED,
    PLACE_VAULTED,
)
from sift.kernel.sql_splice import splice

# The physical half of the resolver again, on folders rather than assets. Folders carry no item
# grant and belong to no tag, so the logical and item steps do not apply to them; everything else
# is the same rule, and the truth table proves it stays the same rule.
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

# The count is read off the stored verdict rather than worked out here. Every copy under the
# folder whose file this viewer may see, and (unless the vault is open) is not concealed for
# them, which already accounts for a second copy of the same file sitting somewhere vaulted.
#
# The bytes and the newest arrival go with the count and are read in the SAME pass, deliberately:
# two statements would be two readings of the same folder, and a folder vaulted between them would
# report a count from one world and a size from another.
#
# It sums over COPIES, like the count does, and that is what "size on disk" means: a file with a
# second copy under this folder really is occupying the bytes twice. `COALESCE` because SUM over no
# rows is NULL, and an empty folder is 0 bytes rather than an unknown number of them. `newest` is
# deliberately NOT coalesced: an empty folder has no newest file, and any date invented for one
# would sort it somewhere among the folders that do.
#
# The walk starts at the folder and goes down: every folder under it off the ancestry, every copy
# in each off the folder index, then one probe of the verdict per copy. CROSS JOIN fixes that
# order. Left to the planner, it starts from the user's verdict rows instead (every file
# the user may see, probed against the folder), so a folder of four files would cost the library.
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

# How much of a folder this viewer may see: the copies present under it, every folder above the
# copy's own included, less what their vault holds back unless it is open. Kept per user and
# folder by the triggers that keep the verdict, so a folder of a hundred thousand files costs one
# row: the same reading of a stored count the grid's total makes of `viewer_stats`.
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

# A picture for each of several folders: the newest file under it the user may see. Sorting a
# subtree is dearer than counting it, so this is asked for the folders on a screen and the count
# above for every folder in a list.
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

# Every folder under this one, itself not counted: the ancestry rows naming it, less its own.
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
