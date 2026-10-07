# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who may see what, as the sharing screens read it: the grants on a thing, the marks on a
wall, where a file's sharing comes from and how far a grant reaches.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from sift.kernel.access.repository.core import RepositoryCore
from sift.kernel.access.repository.grants import (
    _ANY_GRANT_AT_ALL,
    _EFFECTIVE_ASSET_MARKS,
    _EFFECTIVE_FOLDER_MARKS,
    _FILES_UNDER_ENTITY_FOR_USER,
    _GRANT_MARKS,
    _GRANT_SOURCES_FOR_ASSET,
    _GRANT_SOURCES_FOR_FOLDER,
    _GRANT_SOURCES_FOR_OBJECT,
    _GRANTS_OF_USER,
    _GRANTS_ON_OBJECT,
    _REACH_OF_OBJECT,
    _REACH_THROUGH_FILES,
    _VAULT_SOURCES_FOR_ASSET,
    _VAULT_SOURCES_FOR_FOLDER,
    _VAULT_SOURCES_FOR_OBJECT,
    ENTITY_REACH_CEILING,
    ENTITY_REACH_KINDS,
)
from sift.kernel.access.repository.views import (
    Grant,
    GrantMark,
    GrantSource,
    Reaches,
    ReachReason,
    ReachThroughFiles,
    VaultSource,
    _grant_from_row,
    _is_object_id,
)
from sift.kernel.access.sites import SITE_REACH
from sift.kernel.access.viewer import ConcealerType, Effect, ObjectType, Role, Viewer
from sift.kernel.db import in_clause
from sift.kernel.ids import is_id
from sift.kernel.sql_splice import splice

#: What each of these Sites' sharing comes to, counting every network above it: a label under a
#: shared network IS shared. Per user before it is collapsed, since a restrict anywhere on the
#: logical axis beats any share for that user. `shared_here` / `restricted_here` are the Site's OWN
#: grants, the ones undone where somebody is standing. The ids bind as JSON.
_SITE_MARKS = splice(
    """
WITH
wanted(site_id) AS (
  SELECT value FROM json_each(:site_ids)
),
standing(site_id, restricted, shared) AS (
  SELECT w.site_id, MAX(g.effect = 'restrict'), MAX(g.effect = 'share')
    FROM wanted w
    JOIN ({{SITE_REACH}}) reach ON reach.site_id = w.site_id
    JOIN acl_grants g ON g.object_type = 'site' AND g.object_id = reach.ancestor_id
   GROUP BY w.site_id, g.subject_user_id
)
SELECT w.site_id AS object_id,
       COALESCE(MAX(CASE WHEN st.restricted = 0 AND st.shared = 1 THEN 1 ELSE 0 END), 0) AS shared,
       COALESCE(MAX(st.restricted), 0) AS restricted,
       EXISTS (SELECT 1 FROM acl_grants own
                WHERE own.object_type = 'site' AND own.object_id = w.site_id
                  AND own.effect = 'share') AS shared_here,
       EXISTS (SELECT 1 FROM acl_grants own
                WHERE own.object_type = 'site' AND own.object_id = w.site_id
                  AND own.effect = 'restrict') AS restricted_here
  FROM wanted w
  LEFT JOIN standing st ON st.site_id = w.site_id
 GROUP BY w.site_id
""",
    SITE_REACH=SITE_REACH,
)


class GrantReads(RepositoryCore):
    """The reads of the grants themselves."""

    async def grants_of(self, subject_user_id: str) -> list[Grant]:
        rows = await self._db.fetch_all(_GRANTS_OF_USER, (subject_user_id,))
        return [_grant_from_row(row) for row in rows]

    async def grants_on(self, object_type: ObjectType, object_id: str | None) -> list[Grant]:
        """Who this one object is shared with or restricted from, in the order it was decided: the
        sharing panel, since a share nobody is shown cannot be revoked. The rows as stored; every
        route that reaches this is an admin's."""
        self._check_object(object_type, object_id)
        rows = await self._db.fetch_all(_GRANTS_ON_OBJECT, (object_type.value, object_id))
        return [_grant_from_row(row) for row in rows]

    async def _grant_marks(
        self, object_type: ObjectType, object_ids: Sequence[str]
    ) -> dict[str, GrantMark]:
        """Which of these objects have been shared or restricted, as one read for a screenful of
        tiles; one with no grant is absent. Private, as `visible_marks` says."""
        wanted = [object_id for object_id in object_ids if is_id(object_id)]
        if not wanted:
            return {}
        # The type comes first in the query text, so it is prepended to the expanded list.
        sql, values = in_clause(_GRANT_MARKS, wanted)
        rows = await self._db.fetch_all(sql, [object_type.value, *values])
        return {
            str(row["object_id"]): GrantMark(
                shared=bool(row["shared"]),
                restricted=bool(row["restricted"]),
                shared_here=bool(row["shared"]),
                restricted_here=bool(row["restricted"]),
            )
            for row in rows
        }

    async def visible_marks(
        self, viewer: Viewer, object_type: ObjectType, object_ids: Sequence[str]
    ) -> dict[str, GrantMark]:
        """Marks for whoever is allowed to be told about them, which is an admin and nobody else.

        **The only way to ask**, so the rule is written once: a guest's screen already IS the
        answer, and a badge would describe decisions not theirs to read. The reads behind it take
        no viewer and are private so nothing can reach one by mistake. Files, folders and Sites
        inherit from what is above them, so each has an effective read.
        """
        if not viewer.is_admin or not object_ids:
            return {}
        if object_type is ObjectType.ITEM:
            return await self._asset_marks(object_ids)
        if object_type is ObjectType.FOLDER:
            return await self._folder_marks(object_ids)
        if object_type is ObjectType.SITE:
            return await self._site_marks(object_ids)
        return await self._grant_marks(object_type, object_ids)

    async def grant_sources(
        self, object_type: ObjectType, object_id: str | None
    ) -> list[GrantSource]:
        """Every grant that reaches this object, and what each one was made on, ordered by user:
        "why is this shared when I never shared it". `grants_on` is only this exact row."""
        if object_type is ObjectType.ITEM:
            if not _is_object_id(object_id):
                return []
            # One id in the array the statement binds. See `TOUCHING_ASSETS`.
            rows = await self._db.fetch_all(
                _GRANT_SOURCES_FOR_ASSET, {"asset_ids": json.dumps([object_id])}
            )
        elif object_type is ObjectType.FOLDER:
            if not _is_object_id(object_id):
                return []
            rows = await self._db.fetch_all(_GRANT_SOURCES_FOR_FOLDER, {"folder_id": object_id})
        else:
            rows = await self._db.fetch_all(
                _GRANT_SOURCES_FOR_OBJECT,
                {"object_type": object_type.value, "object_id": object_id},
            )
        return [
            GrantSource(
                subject_user_id=str(row["subject_user_id"]),
                username=str(row["username"]),
                effect=Effect(row["effect"]),
                source_type=ObjectType(str(row["source_type"])),
                source_id=None if row["source_id"] is None else str(row["source_id"]),
                source_name=None if row["source_name"] is None else str(row["source_name"]),
            )
            for row in rows
        ]

    async def reach_of(self, object_type: ObjectType, object_id: str | None) -> list[Reaches]:
        """Every user on the instance, and whether they can see this thing at all: who ends up able
        to see it, where `grant_sources` says who it was shared with.

        **It asks the stored verdict and computes nothing**, so it cannot drift from what the user
        will be told; a second working of the ladder would be a second copy of the rule. An admin
        sees everything by role, which `Reaches.role` says. Admin-only at the route, and one row
        per user, never paged, so it cannot withhold a fourth.
        """
        self._check_object(object_type, object_id)
        rows = await self._db.fetch_all(
            _REACH_OF_OBJECT,
            {"object_type": object_type.value, "object_id": object_id},
        )
        return [
            Reaches(
                user_id=str(row["user_id"]),
                username=str(row["username"]),
                role=Role(row["role"]),
                disabled=bool(row["disabled"]),
                sees=bool(row["sees"]),
            )
            for row in rows
        ]

    async def reach_through_files(
        self,
        object_type: ObjectType,
        object_id: str | None,
        subject_user_id: str,
        *,
        ceiling: int = ENTITY_REACH_CEILING,
    ) -> ReachThroughFiles:
        """WHY this user can see this entity, when nothing was ever said about the entity.

        An entity is seen through ONE file under it the user can reach, usually let through by its
        folder. Two reads: a PAGE of those files, then the sharing panel's ladder over the page, one
        row per reason. **It says how much it looked at**: `files` and `complete`, a reason's count
        out of the page. Only the five entity kinds; anything else is empty. Admin-only at the route.
        """
        self._check_object(object_type, object_id)
        # A COST GUARD, not the rule: the statement already matches nothing for any other kind.
        if object_type not in ENTITY_REACH_KINDS or not is_id(subject_user_id):
            return ReachThroughFiles(reasons=(), files=0, complete=True)
        # One more than the ceiling, so the answer knows whether it saw everything.
        found = await self._db.fetch_all(
            _FILES_UNDER_ENTITY_FOR_USER,
            {
                "object_type": object_type.value,
                "object_id": object_id,
                "subject": subject_user_id,
                "ceiling": ceiling + 1,
            },
        )
        complete = len(found) <= ceiling
        asset_ids = [str(row["asset_id"]) for row in found[:ceiling]]
        if not asset_ids:
            return ReachThroughFiles(reasons=(), files=0, complete=True)
        rows = await self._db.fetch_all(
            _REACH_THROUGH_FILES,
            {"asset_ids": json.dumps(asset_ids), "subject": subject_user_id},
        )
        return ReachThroughFiles(
            reasons=tuple(
                ReachReason(
                    source_type=ObjectType(str(row["source_type"])),
                    source_id=None if row["source_id"] is None else str(row["source_id"]),
                    source_name=None if row["source_name"] is None else str(row["source_name"]),
                    files=int(row["files"]),
                )
                for row in rows
            ),
            files=len(asset_ids),
            complete=complete,
        )

    async def vault_sources(
        self, viewer: Viewer, object_type: ObjectType, object_id: str | None
    ) -> list[VaultSource]:
        """Everything this viewer has hidden that is concealing this, named: "why can I not see
        this". Scoped to the viewer, since hiding is personal. An explanation, never a check; the
        caller must have opened Hidden, as these names are what concealment keeps back."""
        if object_type is ObjectType.ITEM:
            if not _is_object_id(object_id):
                return []
            rows = await self._db.fetch_all(
                _VAULT_SOURCES_FOR_ASSET, {"asset_id": object_id, "viewer": viewer.id}
            )
        elif object_type is ObjectType.FOLDER:
            if not _is_object_id(object_id):
                return []
            rows = await self._db.fetch_all(
                _VAULT_SOURCES_FOR_FOLDER, {"folder_id": object_id, "viewer": viewer.id}
            )
        elif object_id is None:
            # Nothing can be concealed on everything in one go, so there is nothing to look for.
            return []
        else:
            rows = await self._db.fetch_all(
                _VAULT_SOURCES_FOR_OBJECT,
                {
                    "object_type": object_type.value,
                    "object_id": object_id,
                    "viewer": viewer.id,
                },
            )
        return [
            VaultSource(
                source_type=ConcealerType(str(row["source_type"])),
                source_id=None if row["source_id"] is None else str(row["source_id"]),
                source_name=None if row["source_name"] is None else str(row["source_name"]),
                here=bool(row["here"]),
            )
            for row in rows
        ]

    async def _folder_marks(self, folder_ids: Sequence[str]) -> dict[str, GrantMark]:
        """What each of these folders' sharing comes to, counting the folders above them: hollow
        where the decision was made further up. Private, as `visible_marks` says."""
        wanted = [folder_id for folder_id in folder_ids if is_id(folder_id)]
        if not wanted:
            return {}
        if await self._db.fetch_one(_ANY_GRANT_AT_ALL) is None:
            return {}
        rows = await self._db.fetch_all(_EFFECTIVE_FOLDER_MARKS, {"folder_ids": json.dumps(wanted)})
        marks = {
            str(row["object_id"]): GrantMark(
                shared=bool(row["shared"]),
                restricted=bool(row["restricted"]),
                shared_here=bool(row["shared_here"]),
                restricted_here=bool(row["restricted_here"]),
            )
            for row in rows
        }
        return {
            folder_id: mark for folder_id, mark in marks.items() if mark.shared or mark.restricted
        }

    async def _site_marks(self, site_ids: Sequence[str]) -> dict[str, GrantMark]:
        """What each of these Sites' sharing comes to, counting the networks above them: hollow
        where the network decided. Private, as `visible_marks` says."""
        wanted = [site_id for site_id in site_ids if is_id(site_id)]
        if not wanted:
            return {}
        if await self._db.fetch_one(_ANY_GRANT_AT_ALL) is None:
            return {}
        rows = await self._db.fetch_all(_SITE_MARKS, {"site_ids": json.dumps(wanted)})
        marks = {
            str(row["object_id"]): GrantMark(
                shared=bool(row["shared"]),
                restricted=bool(row["restricted"]),
                shared_here=bool(row["shared_here"]),
                restricted_here=bool(row["restricted_here"]),
            )
            for row in rows
        }
        # Nothing said about it is not a mark: the same shape `_grant_marks` returns.
        return {site_id: mark for site_id, mark in marks.items() if mark.shared or mark.restricted}

    async def _asset_marks(self, asset_ids: Sequence[str]) -> dict[str, GrantMark]:
        """What each of these files' sharing comes to, counting every folder and tag above it: a
        tile's badge. A file nothing reaches is absent. Private, as `visible_marks` says."""
        wanted = [asset_id for asset_id in asset_ids if is_id(asset_id)]
        if not wanted:
            return {}
        # With no grant at all, the walk below would produce no rows.
        if await self._db.fetch_one(_ANY_GRANT_AT_ALL) is None:
            return {}
        rows = await self._db.fetch_all(_EFFECTIVE_ASSET_MARKS, {"asset_ids": json.dumps(wanted)})
        marks = {
            str(row["object_id"]): GrantMark(
                shared=bool(row["shared"]),
                restricted=bool(row["restricted"]),
                shared_here=bool(row["shared_here"]),
                restricted_here=bool(row["restricted_here"]),
            )
            for row in rows
        }
        # Nothing said about it is not a mark, the shape `_grant_marks` returns.
        return {
            asset_id: mark for asset_id, mark in marks.items() if mark.shared or mark.restricted
        }
