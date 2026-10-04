# SPDX-License-Identifier: AGPL-3.0-or-later
"""Folders as one viewer may see them: one folder, a level of the tree, its counts and covers."""

from __future__ import annotations

import json
from collections.abc import Sequence

from sift.kernel.access.repository.core import RepositoryCore
from sift.kernel.access.repository.folders import (
    _COUNT_FILES_UNDER,
    _COUNT_FOLDERS_UNDER,
    _FOLDER_COVERS,
    _FOLDER_FILE_COUNT,
    _FOLDER_FILE_COUNTS,
)
from sift.kernel.access.repository.views import (
    Folder,
    FolderContents,
    _is_object_id,
)
from sift.kernel.access.viewer import Viewer
from sift.kernel.paging import MAX_PAGE_SIZE

#: How many files carry each of several people by a pass's doing (a `source`, which filing by hand
#: leaves NULL) under each of several folders, as one user may see them. The pairs bind as JSON
#: `[[folder_id, person_id], ...]`; a pair with nothing to count is absent. The verdict is an
#: EXISTS, not a join: a join lets the planner walk an admin's whole verdict first, and an EXISTS
#: cannot be an outer loop, whatever the statistics say.
_FILED_COUNTS = """
SELECT p.folder_id AS folder_id, p.person_id AS person_id, COUNT(DISTINCT ap.asset_id) AS files
  FROM (SELECT json_extract(pair.value, '$[0]') AS folder_id,
               json_extract(pair.value, '$[1]') AS person_id
          FROM json_each(?) pair) p
  JOIN folder_ancestry fa ON fa.ancestor_id = p.folder_id
  JOIN asset_locations al ON al.folder_id = fa.folder_id
  JOIN asset_people ap ON ap.asset_id = al.asset_id
                       AND ap.person_id = p.person_id
                       AND ap.source IS NOT NULL
 WHERE EXISTS (SELECT 1 FROM viewer_assets va
                WHERE va.user_id = ?
                  AND va.asset_id = ap.asset_id
                  AND va.concealed = 0)
 GROUP BY p.folder_id, p.person_id
"""


class FolderReads(RepositoryCore):
    """The scoped reads of folders."""

    async def get_folder(self, viewer: Viewer, folder_id: str) -> Folder | None:
        if not _is_object_id(folder_id):
            return None
        folders = await self._folders(viewer, folder_id=folder_id)
        if not folders:
            self._log_denied(viewer, folder_id)
            return None
        return folders[0]

    async def filed_counts(
        self, viewer: Viewer, pairs: Sequence[tuple[str, str]]
    ) -> dict[tuple[str, str], int]:
        """How many of a folder's files carry a person by a pass's doing, for each (folder,
        person) asked about, through the subtree and this viewer's verdict. Absent means none."""
        wanted = [
            (folder_id, person_id)
            for folder_id, person_id in dict.fromkeys(pairs)
            if _is_object_id(folder_id) and _is_object_id(person_id)
        ]
        if not wanted:
            return {}
        found: dict[tuple[str, str], int] = {}
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            chunk = wanted[start : start + MAX_PAGE_SIZE]
            rows = await self._db.fetch_all(_FILED_COUNTS, (json.dumps(chunk), viewer.id))
            for row in rows:
                found[(str(row["folder_id"]), str(row["person_id"]))] = int(row["files"])
        return found

    async def visible_folders_of(
        self, viewer: Viewer, folder_ids: Sequence[str]
    ) -> dict[str, Folder]:
        """Which of these folders this viewer may see, keyed by id: the batch `get_folder`, one
        read. Absent means not allowed or not there, alike."""
        wanted = [folder_id for folder_id in dict.fromkeys(folder_ids) if _is_object_id(folder_id)]
        if not wanted:
            return {}
        found: dict[str, Folder] = {}
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            for folder in await self._folders(
                viewer, folder_ids=wanted[start : start + MAX_PAGE_SIZE]
            ):
                found[folder.id] = folder
        return found

    async def folder_summaries(self, viewer: Viewer, folder_ids: Sequence[str]) -> dict[str, int]:
        """How many files this viewer may see under each of these folders, off the stored verdict;
        a folder holding nothing they may see is absent. Whether the FOLDER may be seen is
        `visible_folders_of`'s question."""
        wanted = [folder_id for folder_id in dict.fromkeys(folder_ids) if _is_object_id(folder_id)]
        found: dict[str, int] = {}
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            rows = await self._db.fetch_all(
                _FOLDER_FILE_COUNTS,
                {
                    "viewer": viewer.id,
                    "reveal": self._reveal_existence(viewer),
                    "folder_ids": json.dumps(wanted[start : start + MAX_PAGE_SIZE]),
                },
            )
            found.update((str(row["folder_id"]), int(row["files"])) for row in rows)
        return found

    async def folder_covers(self, viewer: Viewer, folder_ids: Sequence[str]) -> dict[str, str]:
        """The newest file this viewer may see under each of these folders, for the folders that
        have one. A picture for a folder on a screen; read for a screen's worth, never a list's."""
        wanted = [folder_id for folder_id in dict.fromkeys(folder_ids) if _is_object_id(folder_id)]
        found: dict[str, str] = {}
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            rows = await self._db.fetch_all(
                _FOLDER_COVERS,
                {
                    "viewer": viewer.id,
                    "reveal": self._reveal_existence(viewer),
                    "folder_ids": json.dumps(wanted[start : start + MAX_PAGE_SIZE]),
                },
            )
            found.update((str(row["folder_id"]), str(row["cover"])) for row in rows if row["cover"])
        return found

    async def can_view_folder(self, viewer: Viewer, folder_id: str) -> bool:
        return await self.get_folder(viewer, folder_id) is not None

    async def visible_folders(
        self,
        viewer: Viewer,
        *,
        root_id: str | None = None,
        parent_id: str | None = None,
        top_level: bool = False,
    ) -> list[Folder]:
        """Every folder this viewer may see, in path order, optionally filtered.

        The whole tree, one library's, one folder's children or each library's first level are one
        statement with different binds. A tree is read whole, so a leaf and an unopened folder never
        need a second rule. A folder whose parent is absent is correct (nearest wins), and a caller
        must place it, not drop it. Roots are never offered: a root's path is a fact about the
        machine. A malformed id is an empty answer; None means "do not filter by this", which is
        safe because the scoping is in the statement.
        """
        for given in (root_id, parent_id):
            if given is not None and not _is_object_id(given):
                return []
        return await self._folders(
            viewer, root_id=root_id, parent_id=parent_id, top_level=top_level
        )

    async def folder_file_count(self, viewer: Viewer, folder: Folder) -> int | None:
        """How many files sit in a folder and everything under it, present on disk: the number shown
        before a move. A `Folder`, so only one already given to the viewer. None for a guest: the
        physical count would leak what is restricted from them."""
        if not viewer.is_admin:
            return None

        row = await self._db.fetch_one(
            _FOLDER_FILE_COUNT,
            {
                "folder_id": folder.id,
                "reveal": self._reveal_existence(viewer),
                # Leaves out what THIS admin has hidden, so it matches their own grid.
                "viewer": viewer.id,
            },
        )
        return 0 if row is None else int(row["files"])

    async def folder_contents(self, viewer: Viewer, folder: Folder) -> FolderContents | None:
        """What is under a folder: how many files, how many bytes, how many folders. None for a guest.
        The same query `folder_file_count` reads, so a move and the properties panel agree; guests
        are refused for the reason given there."""
        if not viewer.is_admin:
            return None

        under = await self._db.fetch_one(
            _COUNT_FILES_UNDER,
            {
                "folder_id": folder.id,
                "reveal": self._reveal_existence(viewer),
                "viewer": viewer.id,
            },
        )
        folders = await self._db.fetch_one(_COUNT_FOLDERS_UNDER, {"folder_id": folder.id})
        newest = None if under is None or under["newest"] is None else int(under["newest"])
        return FolderContents(
            files=0 if under is None else int(under["files"]),
            bytes=0 if under is None else int(under["bytes"]),
            folders=0 if folders is None else int(folders["folders"]),
            newest_at=newest,
        )
