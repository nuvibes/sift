# SPDX-License-Identifier: AGPL-3.0-or-later
"""The undo: taking an answer, a folder or a username's filings back, and putting them back again."""

from __future__ import annotations

import json
from collections.abc import Sequence

from sift.kernel.access import (
    Viewer,
    attribute_assets_recording_on,
    detach_person_on,
    person_is_bare_on,
    remove_alias_on,
    remove_person_on,
    unlink_username_from_person_on,
)
from sift.kernel.access.sentences import plural
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce, announce_now
from sift.kernel.ledger import Object as LedgerObject
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import Subject
from sift.slices.suggestions.service_base import (
    AUTOMATIC,
    FILED_FROM_A_NAME,
    FILED_QUEUE,
    FILENAMES_QUEUE,
    TAKEN_BACK,
    FolderTakenOff,
    UsernameTakenOff,
    Written,
)
from sift.slices.suggestions.service_folders import FolderRuleMixin
from sift.slices.suggestions.store import NameFiling

log = get_logger(__name__)


def _taken_back_sentence(name: str, place: str, files: int) -> str:
    """The line a folder taken back reads as: "Removed 240 files in the folder Shoots from Neve
    Alder", the neighbouring tab's word for the same press ("Removed 12 files from ...")."""
    if files:
        return f"Removed {plural(files, 'file', 'files')} in the folder {place} from {name}"
    return f"Removed the folder {place} from {name}"


class UndoMixin(FolderRuleMixin):
    """Takes back what an answer or a pass wrote, and puts a folder or a username back."""

    async def take_back(self, written: Written, *, claim_id: str) -> bool:
        """Put back what one confirmation did, and only what it did."""
        async with self._store.write() as connection:
            for asset_id, person_id in written.attributed:
                await detach_person_on(connection, asset_ids=[asset_id], person_id=person_id)
            await self._faces.unname_faces(connection, written.faces)
            if written.alias is not None:
                person_id, alias = written.alias
                await remove_alias_on(connection, person_id=person_id, alias=alias)
            if written.username_linked is not None:
                username_id, person_id = written.username_linked
                await unlink_username_from_person_on(
                    connection, username_id=username_id, person_id=person_id
                )
            # Grouped by the username, not one statement per file: a folder of three thousand
            # files is one decision about one username, and it is one DELETE's worth of work.
            by_username: dict[str, list[str]] = {}
            for asset_id, username_id in written.filed:
                by_username.setdefault(username_id, []).append(asset_id)
            for username_id, under in by_username.items():
                await self._store.unfile_on(
                    connection, username_id=username_id, source=AUTOMATIC, asset_ids=under
                )
            for folder_id, person_id in written.remembered:
                await self._store.forget_folder_person_on(
                    connection, folder_id=folder_id, person_id=person_id
                )
            # Told BEFORE the people it invented go, so a guest shared one of them is told too.
            announce(
                await self._told(
                    connection,
                    people=[person_id for _, person_id in written.attributed]
                    + list(written.created_people),
                    usernames=list(by_username)
                    + ([written.username_linked[0]] if written.username_linked else []),
                ),
                About.LIBRARY,
            )
            for person_id in written.created_people:
                if await person_is_bare_on(connection, person_id):
                    await remove_person_on(connection, person_id)
            reopened = await self._store.reopen_on(connection, claim_id)
            # The other folders of that name the same Yes answered are asked again too.
            for namesake_id, _folder in written.namesakes:
                await self._store.reopen_on(connection, namesake_id)

        # And what `confirm` taught from the faces it named, after the transaction for the same
        # reason it was taught after one. The person the faces were named as is the one the folder
        # was remembered as: a person claim remembers exactly one, and only a person claim names a
        # group. Anything else (no faces, or a shape that remembered several) taught nothing.
        remembered = {person_id for _folder, person_id in written.remembered}
        if written.faces and len(remembered) == 1:
            await self._faces.unteach(written.faces, next(iter(remembered)))
        # A may-be card the pass put up on a namesake folder had that answer as its reason, as the
        # folder's own take back withdraws one. Its writer says nothing, so this rings.
        withdrawn = 0
        for _claim, folder_id in written.namesakes:
            for person_id in remembered:
                withdrawn += await self._faces.withdraw_proposals(folder_id, person_id)
        if withdrawn:
            announce_now(EVERY_ADMIN, About.LIBRARY)

        log.info("suggestions.taken_back", files=len(written.attributed), faces=len(written.faces))
        return reopened

    async def take_back_silent(self, viewer: Viewer, *, folder_id: str, person_id: str) -> bool:
        """The Undo on a silent write's own History line: the same act as the press on its row of
        Added without asking (`take_back_folder`), with no second line, since the Undo marks the
        line it was pressed on. True if anything went back."""
        taken = await self._take_folder_back(viewer, folder_id=folder_id, person_id=person_id)
        return taken.files > 0 or taken.forgot

    async def take_back_folder(
        self, viewer: Viewer, *, folder_id: str, person_id: str
    ) -> FolderTakenOff:
        """The press on a row of Organize > Folders > Added without asking: take a folder back from
        the person it was added to, as ONE decision with its own Undo (`put_folder_back`)."""
        return await self._take_folder_back(
            viewer, folder_id=folder_id, person_id=person_id, record=True
        )

    async def _take_folder_back(
        self, viewer: Viewer, *, folder_id: str, person_id: str, record: bool = False
    ) -> FolderTakenOff:
        nothing = FolderTakenOff(files=0, forgot=False, decision_id=None)
        if await self._access.get_folder(viewer, folder_id) is None:
            return nothing
        if person_id not in await self._access.visible_people(viewer, [person_id]):
            return nothing
        under = await self._store.assets_under(folder_id)
        filed = await self._store.filed_by_a_folder(person_id, under, source=AUTOMATIC)
        allowed = await self._access.visible_of(viewer, filed)
        taking = [one for one in filed if one in allowed]
        linked = await self._store.folder_person_stands(folder_id, person_id)
        if not taking and not linked:
            return nothing
        decision_id: str | None = None
        async with self._store.write() as connection:
            removed = await detach_person_on(connection, asset_ids=taking, person_id=person_id)
            forgot = await self._store.forget_folder_person_on(
                connection, folder_id=folder_id, person_id=person_id
            )
            refused = await self._store.refuse_folder_person_on(
                connection, folder_id=folder_id, person_id=person_id
            )
            if record and self._recorder is not None:
                name = await self._store.person_name_on(connection, person_id)
                place = await self._store.folder_said_on(connection, folder_id)
                title = _taken_back_sentence(name, place, len(taking))
                decision_id = await self._recorder.record_on(
                    connection,
                    queue=FILED_QUEUE,
                    user_id=viewer.id,
                    title=title,
                    detail=f"{title}.",
                    payload=json.dumps(
                        {
                            "kind": TAKEN_BACK,
                            "folder_id": folder_id,
                            "person_id": person_id,
                            "assets": taking,
                            "linked": forgot,
                            "refused": refused,
                        }
                    ),
                    subjects=[
                        Subject(kind="folder", id=folder_id),
                        *(Subject(kind="asset", id=one) for one in taking),
                        Subject(kind="person", id=person_id),
                    ],
                    verb="removed",
                    object=LedgerObject(kind="person", id=person_id, name=name or None),
                )
            announce(await self._told(connection, people=[person_id]), About.LIBRARY)
        # The may-be card goes after the transaction, through the faces side's own door, as the
        # pass takes one back when its reason goes. Its writer says nothing, so this rings.
        if await self._faces.withdraw_proposals(folder_id, person_id):
            announce_now(EVERY_ADMIN, About.LIBRARY)
        log.info("suggestions.folder_taken_back", folder_id=folder_id, files=removed)
        return FolderTakenOff(files=removed, forgot=forgot, decision_id=decision_id)

    async def put_folder_back(
        self,
        viewer: Viewer,
        *,
        folder_id: str,
        person_id: str,
        asset_ids: Sequence[str],
        linked: bool,
        refused: bool,
    ) -> bool:
        """The Undo of `take_back_folder`: the person back on the files it took them off, as the
        folder pass's own rows, the folder's answer back where it stood, the no forgotten where that
        press said it, and the may-be card put up again by the pass's own rule. True if anything
        came back."""
        if await self._access.get_folder(viewer, folder_id) is None:
            return False
        if person_id not in await self._access.visible_people(viewer, [person_id]):
            return False
        allowed = await self._access.visible_of(viewer, list(asset_ids))
        async with self._store.write() as connection:
            written = await attribute_assets_recording_on(
                connection,
                asset_ids=[one for one in asset_ids if one in allowed],
                person_id=person_id,
                source=AUTOMATIC,
            )
            relinked = linked and await self._store.remember_folder_person_on(
                connection, folder_id=folder_id, person_id=person_id
            )
            if refused:
                await self._store.forget_folder_refusal_on(
                    connection, folder_id=folder_id, person_id=person_id
                )
            announce(await self._told(connection, people=[person_id]), About.LIBRARY)
        if relinked and await self._faces.looking():
            faces = await self._faces.faces_in(folder_id)
            if await self._propose_main_group(folder_id, person_id, faces):
                announce_now(EVERY_ADMIN, About.LIBRARY)
        return bool(written or relinked)

    async def take_back_filings(self, *, username_id: str, asset_ids: Sequence[str]) -> bool:
        """Put back one filename filing. True if anything was actually put back.

        **And the file is remembered as refused, in the same transaction.**
        """
        if not asset_ids:
            return False
        async with self._store.write() as connection:
            removed = await self._store.unfile_from_a_name_on(
                connection, username_id=username_id, sources=FILED_FROM_A_NAME, asset_ids=asset_ids
            )
            if removed:
                await self._store.refuse_filename_on(connection, asset_ids)
                # The Username's wall and its Site's grid on other tabs let the files go.
                announce(await self._told(connection, usernames=[username_id]), About.LIBRARY)
        return removed > 0

    async def take_back_username(self, viewer: Viewer, *, username_id: str) -> UsernameTakenOff:
        """Take back every filing a file's own name made under one username, as ONE decision."""
        nothing = UsernameTakenOff(files=0, decision_id=None)
        username = await self._access.visible_username(viewer, username_id)
        if username is None:
            return nothing
        filed = await self._store.filed_from_a_name_under(username_id, FILED_FROM_A_NAME)
        allowed = await self._access.visible_of(viewer, [one.asset_id for one in filed])
        taking = [one for one in filed if one.asset_id in allowed]
        if not taking:
            return nothing
        already = await self._store.refused_filenames()
        refusing = [one.asset_id for one in taking if one.asset_id not in already]
        where = f" on {username.site_name}" if username.site_name else ""
        files = "1 file" if len(taking) == 1 else f"{len(taking):,} files"
        title = f"Removed {files} from {username.name}{where}"
        decision_id: str | None = None
        async with self._store.write() as connection:
            await self._store.unfile_from_a_name_on(
                connection,
                username_id=username_id,
                sources=FILED_FROM_A_NAME,
                asset_ids=[one.asset_id for one in taking],
            )
            await self._store.refuse_filename_on(connection, refusing)
            if self._recorder is not None:
                decision_id = await self._recorder.record_on(
                    connection,
                    queue=FILENAMES_QUEUE,
                    user_id=viewer.id,
                    title=title,
                    detail=f"{title}.",
                    payload=json.dumps(
                        {
                            "kind": "declined",
                            "username_id": username_id,
                            "name": username.name,
                            "site": username.site_name,
                            "files": [
                                [one.asset_id, one.source, one.decided_at, one.post_id]
                                for one in taking
                            ],
                            "refused": refusing,
                        }
                    ),
                    subjects=[
                        Subject(kind="username", id=username_id),
                        *(Subject(kind="asset", id=one.asset_id) for one in taking),
                    ],
                    verb="removed",
                    object=LedgerObject(kind="username", id=username_id, name=username.name),
                )
            announce(await self._told(connection, usernames=[username_id]), About.LIBRARY)
        log.info("suggestions.username_taken_back", files=len(taking))
        return UsernameTakenOff(files=len(taking), decision_id=decision_id)

    async def put_username_back(
        self,
        viewer: Viewer,
        *,
        username_id: str,
        filings: Sequence[NameFiling],
        refused: Sequence[str],
    ) -> bool:
        """Undo `take_back_username`: the filings written back as they stood, and the refusals it
        added forgotten. True if any filing came back."""
        if not filings:
            return False
        if await self._access.visible_username(viewer, username_id) is None:
            return False
        allowed = await self._access.visible_of(viewer, [one.asset_id for one in filings])
        coming_back = [one for one in filings if one.asset_id in allowed]
        if not coming_back:
            return False
        async with self._store.write() as connection:
            written = await self._store.refile_from_a_name_on(connection, username_id, coming_back)
            if written:
                await self._store.unrefuse_filename_on(
                    connection, [one for one in refused if one in allowed]
                )
                announce(await self._told(connection, usernames=[username_id]), About.LIBRARY)
        return written > 0
