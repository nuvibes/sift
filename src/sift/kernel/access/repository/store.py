# SPDX-License-Identifier: AGPL-3.0-or-later
"""The repository itself: one object, and every scoped read in Sift goes through it.

The reads live one module per thing read (`read_files`, `read_people`, ...); what is here is the
one object they make up, so there is one object that knows who is asking, and the writes that
change what one user may see.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from sift.kernel.access.repository.assets import (
    _SET_ASSET_VAULT,
    _SET_ASSET_VAULT_MANY,
)
from sift.kernel.access.repository.folders import (
    _SET_FOLDER_VAULT,
    _VISIBLE_FOLDERS,
)
from sift.kernel.access.repository.grants import (
    _DELETE_GRANT,
    _DELETE_ITEM_GRANTS,
    _DELETE_OBJECT_GRANTS,
    _INSERT_GRANT,
)
from sift.kernel.access.repository.read_collections import CollectionReads
from sift.kernel.access.repository.read_counts import CountReads
from sift.kernel.access.repository.read_files import FileReads
from sift.kernel.access.repository.read_folders import FolderReads
from sift.kernel.access.repository.read_grants import GrantReads
from sift.kernel.access.repository.read_loops import LoopReads
from sift.kernel.access.repository.read_one_file import OneFileReads
from sift.kernel.access.repository.read_people import PeopleReads
from sift.kernel.access.repository.read_photo_sets import PhotoSetReads
from sift.kernel.access.repository.read_sites import SiteReads
from sift.kernel.access.repository.read_songs import SongReads
from sift.kernel.access.repository.read_tags import TagReads
from sift.kernel.access.repository.views import (
    Actionable,
    Grant,
    _grant_from_row,
    _is_object_id,
)
from sift.kernel.access.viewer import Effect, ObjectType, Viewer
from sift.kernel.audience import NOBODY
from sift.kernel.cache_stamp import bump_cache_stamp
from sift.kernel.changes import About, announce
from sift.kernel.content.entity_state import OpinionWrite, opinion_before
from sift.kernel.content.user_state import OpinionKind, record_opinion
from sift.kernel.db import Params, Row, in_clause
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, Object, record_event
from sift.kernel.log import get_logger
from sift.kernel.paging import MAX_PAGE_SIZE
from sift.kernel.vocabulary import Subject, SubjectKind

log = get_logger(__name__)


def _row_name(row: Row) -> str | None:
    """What a resolved row was called, off whichever column that row keeps its name in."""
    mapping = dict(row)
    for column in ("title", "name", "original_filename", "filename"):
        value = mapping.get(column)
        if value:
            return str(value)
    return None


@dataclass(frozen=True, slots=True)
class Named:
    """A thing an event is about, as the record keeps it: its kind, its id, what it was CALLED.

    The name is a snapshot, never a lookup: a revoked grant or a vaulted file resolves to nothing.
    """

    kind: str
    id: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Recording:
    """One ledger event, to be written in the same transaction as the change beside it.

    Plain strings, which `_write_for` turns into the ledger's own types; a kind spelled wrong is
    caught there at run time.
    """

    #: The signed-in user who took the act. Every recording made here is somebody's: a pass
    #: does not conceal a file or share one.
    user_id: str
    verb: str
    subjects: tuple[Named, ...]
    object: Named | None = None


#: `MAX_PAGE_SIZE` is re-exported from `kernel.paging`, where the routes' ceiling is the same fact.
__all__ = ["MAX_PAGE_SIZE", "Repository"]


class Repository(
    FileReads,
    OneFileReads,
    FolderReads,
    TagReads,
    PeopleReads,
    SiteReads,
    CollectionReads,
    PhotoSetReads,
    SongReads,
    LoopReads,
    CountReads,
    GrantReads,
):
    """The permission-scoped read path. One per database.

    Held on the application rather than reached for through a global, so a test can hand it a
    different database instead of reaching in to swap one out.
    """

    async def set_asset_vault(self, viewer: Viewer, asset_id: str, *, vault: bool) -> bool:
        """Put one file in the vault or take it back out. False if there is nothing to write to.

        False is "not there" and "not yours" alike, or it would confirm a hidden file exists. Only
        an unlocked vault resolves a concealed row here, as `open_asset`: a placeholder is a way of
        looking, and a write from behind it would be the reveal. So taking a file back out needs no
        rule of its own: whoever resolves it has proved the PIN.
        """
        row = await self._one(viewer, asset_id, reveal=1 if viewer.show_hidden else 0)
        if row is None:
            self._log_denied(viewer, asset_id)
            return False
        now = self._now()
        await self._write_for(
            viewer.id,
            _SET_ASSET_VAULT,
            {
                "asset_id": asset_id,
                "viewer": viewer.id,
                "hidden": 1 if vault else 0,
                "hidden_at": now if vault else None,
                "now": now,
            },
            # The name off the row just resolved: once concealed it can no longer be looked up.
            events=[
                Recording(
                    user_id=viewer.id,
                    verb="hidden" if vault else "revealed",
                    subjects=(Named("asset", asset_id, _row_name(row)),),
                )
            ],
        )
        return True

    async def set_asset_vault_many(
        self, viewer: Viewer, asset_ids: Sequence[str], *, vault: bool
    ) -> Actionable:
        """Put a selection in the vault or take it back out. Answers what it was allowed to touch.

        The list form of the write above, resolving the selection itself through `actionable_of`
        (the resolution IS the permission), so it allows exactly what the single write would. The
        `Actionable` says why the rest were left out. One statement, one transaction, one cache
        stamp; see `_write_for`.
        """
        actionable = await self.actionable_of(viewer, asset_ids)
        if not actionable.allowed:
            return actionable
        now = self._now()
        await self._write_for(
            viewer.id,
            _SET_ASSET_VAULT_MANY,
            {
                "asset_ids": json.dumps(list(actionable.allowed)),
                "viewer": viewer.id,
                "hidden": 1 if vault else 0,
                "hidden_at": now if vault else None,
                "now": now,
            },
            # One event per file, since a file's own page asks when THIS file went out of sight. No
            # names: reading the rows would undo the one round trip, and the files still exist.
            events=[
                Recording(
                    user_id=viewer.id,
                    verb="hidden" if vault else "revealed",
                    subjects=(Named("asset", one),),
                )
                for one in actionable.allowed
            ],
        )
        return actionable

    async def set_folder_vault(self, viewer: Viewer, folder_id: str, *, vault: bool) -> bool:
        """The same, for a folder, which carries everything under it.

        One write covers a tree, every copy of every file under it. Resolved through the folder
        query with `reveal` closed, for the reason the one above gives.
        """
        if not _is_object_id(folder_id):
            return False
        rows = await self._db.fetch_all(
            _VISIBLE_FOLDERS,
            {
                "viewer": viewer.id,
                "is_admin": 1 if viewer.is_admin else 0,
                "folder_id": folder_id,
                "folder_ids": None,
                "parent_id": None,
                "root_id": None,
                "top_level": 0,
                "reveal": 1 if viewer.show_hidden else 0,
            },
        )
        if not rows:
            self._log_denied(viewer, folder_id)
            return False
        now = self._now()
        await self._write_for(
            viewer.id,
            _SET_FOLDER_VAULT,
            {
                "folder_id": folder_id,
                "viewer": viewer.id,
                "hidden": 1 if vault else 0,
                "hidden_at": now if vault else None,
                "now": now,
            },
            # The FOLDER, not everything under it: the folder was the act.
            events=[
                Recording(
                    user_id=viewer.id,
                    verb="hidden" if vault else "revealed",
                    subjects=(Named("folder", folder_id, _row_name(rows[0])),),
                )
            ],
            # And as this user's own opinion, which outlives `hidden_at` being cleared on a reveal.
            opinion=OpinionWrite(
                user_id=viewer.id,
                subject_kind="folder",
                subject_id=folder_id,
                kind=OpinionKind.HIDE,
                after=1 if vault else 0,
            ),
        )
        return True

    async def grant(
        self,
        object_type: ObjectType,
        object_id: str | None,
        subject_user_id: str,
        effect: Effect,
        *,
        event: Recording | None = None,
    ) -> Grant:
        """Share or restrict one object for one person.

        Re-granting returns the existing row: the control says the state, so pressing it twice is
        not an error. `event` is what somebody DID, built by the sharing feature, which knows the
        user's name; optional, since arranging permissions is not an act.
        """
        self._check_object(object_type, object_id)
        rows = await self._write_for(
            subject_user_id,
            _INSERT_GRANT,
            (
                new_id(),
                object_type.value,
                object_id,
                subject_user_id,
                effect.value,
                self._now(),
            ),
            events=() if event is None else (event,),
        )
        granted = _grant_from_row(rows[0])
        log.info(
            "access.granted",
            object_type=granted.object_type.value,
            object_id=granted.object_id,
            subject_user_id=granted.subject_user_id,
            effect=granted.effect.value,
        )
        return granted

    async def revoke(
        self,
        object_type: ObjectType,
        object_id: str | None,
        subject_user_id: str,
        effect: Effect,
        *,
        event: Recording | None = None,
    ) -> None:
        """Take one grant back. `event` is the record of it (see `grant` for why it is optional).

        The row is deleted, so the record is the only trace the share was made.
        """
        self._check_object(object_type, object_id)
        # The one that most has to be atomic. Taking a share away and leaving that user's
        # pictures reachable from their own browser is the revoke not having happened.
        await self._write_for(
            subject_user_id,
            _DELETE_GRANT,
            (object_type.value, object_id, subject_user_id, effect.value),
            events=() if event is None else (event,),
        )
        log.info(
            "access.revoked",
            object_type=object_type.value,
            object_id=object_id,
            subject_user_id=subject_user_id,
            effect=effect.value,
        )

    async def forget_object(self, object_type: ObjectType, object_id: str) -> None:
        """Drop every grant naming an object that is being deleted.

        `acl_grants.object_id` can carry no foreign key, so nothing cascades: deleting an object
        without calling this leaves grants applying to whatever next has that id.
        """
        self._check_object(object_type, object_id)
        async with self._db.write() as connection:
            named = await connection.execute_fetchall(
                _DELETE_OBJECT_GRANTS, (object_type.value, object_id)
            )
            # Only the users a grant named see differently once it goes; nobody else is told.
            told = NOBODY
            for user_id in sorted({str(row["subject_user_id"]) for row in named}):
                told = told | await bump_cache_stamp(connection, user_id)
            if told:
                announce(told, About.LIBRARY)

    async def forget_items(self, asset_ids: Sequence[str]) -> None:
        """Drop every grant naming files that have ended, in one write.

        The batch form of `forget_object` for the one kind of object that ends in bulk. No picture
        counter is raised: the files are gone from every listing already, and the counter is for
        concealment (see `remove_asset_if_unplaced` for why a delete does not raise it).
        """
        wanted = [asset_id for asset_id in dict.fromkeys(asset_ids) if _is_object_id(asset_id)]
        if not wanted:
            return
        async with self._db.write() as connection:
            for start in range(0, len(wanted), MAX_PAGE_SIZE):
                sql, values = in_clause(_DELETE_ITEM_GRANTS, wanted[start : start + MAX_PAGE_SIZE])
                await connection.execute(sql, values)

    async def _write_for(
        self,
        user_id: str,
        sql: str,
        params: Params,
        *,
        events: Sequence[Recording] = (),
        opinion: OpinionWrite | None = None,
    ) -> list[Row]:
        """A write that changes what one user may see, and the note that says so.

        One transaction, so no window leaves old pictures readable after the change. `events` ride
        in it too, since these acts leave nothing else behind. `opinion` is read BEFORE the
        statement, because after the upsert nothing remembers the value it replaced.
        """
        async with self._db.write() as connection:
            before = (
                None
                if opinion is None
                else await opinion_before(
                    connection,
                    subject_kind=opinion.subject_kind,
                    subject_id=opinion.subject_id,
                    user_id=opinion.user_id,
                    kind=opinion.kind,
                )
            )
            rows = list(await connection.execute_fetchall(sql, params))
            if opinion is not None:
                await record_opinion(
                    connection,
                    user_id=opinion.user_id,
                    subject_kind=opinion.subject_kind,
                    subject_id=opinion.subject_id,
                    kind=opinion.kind,
                    before=before,
                    after=opinion.after,
                    at=self._now(),
                )
            for one in events:
                await record_event(
                    connection,
                    actor=Actor.user(one.user_id),
                    verb=one.verb,
                    subject=[
                        Subject(kind=cast(SubjectKind, each.kind), id=each.id, name=each.name)
                        for each in one.subjects
                    ],
                    object=(
                        None
                        if one.object is None
                        else Object(
                            kind=cast(SubjectKind, one.object.kind),
                            id=one.object.id,
                            name=one.object.name,
                        )
                    ),
                )
            announce(await bump_cache_stamp(connection, user_id), About.LIBRARY)
            return rows
