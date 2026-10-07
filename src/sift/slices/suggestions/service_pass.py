# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pass: reading the whole library for nobody, one rule after another."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now
from sift.kernel.content import FolderNode
from sift.kernel.log import get_logger
from sift.slices.suggestions.ladder import Evidence
from sift.slices.suggestions.naming import (
    PARSER_VERSION,
    KnownName,
    Reading,
    fold_known,
    read_chain,
)
from sift.slices.suggestions.service_base import Arrivals, FolderPass
from sift.slices.suggestions.service_filenames import FilenameRuleMixin
from sift.slices.suggestions.service_folders import FolderRuleMixin, PassReads
from sift.slices.suggestions.settings import FILE_FROM_FILENAMES_KEY

log = get_logger(__name__)


def _signature(folder: FolderNode, stamp: object) -> str:
    """A folder's shape as one opaque string. Compared for equality and never read.

    **The reader's own generation is part of the shape**
    """
    faces = stamp.as_text() if stamp is not None and hasattr(stamp, "as_text") else "-"
    return f"v{PARSER_VERSION}:{folder.files}:{folder.newest}:{faces}"


def _read_all(
    folders: Sequence[FolderNode], sites: Sequence[str], known: Sequence[KnownName]
) -> list[Reading]:
    """Every folder's chain read in one go, on a thread. Pure work over strings."""
    return [read_chain(folder.chain, sites=sites, known=known) for folder in folders]


class PassMixin(FolderRuleMixin, FilenameRuleMixin):
    """Runs every rule over the library and takes back the questions no reading asks any more."""

    # --- the pass ------------------------------------------------------------------------------

    async def rebuild(self) -> int:
        """Read every folder whose contents or faces have moved. Returns how many claims changed.

        **ONE BELL FOR THE WHOLE PASS, at its end, where it wrote anything.**
        """
        changed, wrote = await self._pass()
        if wrote:
            announce_now(EVERY_ADMIN, About.LIBRARY)
        return changed

    async def _pass(self) -> tuple[int, bool]:
        """`rebuild`'s body: how many claims changed, and whether the pass wrote anything at all
        (a number learned, a file filed from its name or put in a post counts, though no claim
        moved).

        **Incremental by signature.**
        """
        planned = await self.plan()
        folders = list(planned.folders)
        if not folders:
            return 0, False
        # The username numbers and the filings first, and OUTSIDE the early return below.
        numbered, filed, grouped = await self._by_names(planned.by_names)
        moved = list(planned.moved)
        if not moved:
            return 0, bool(numbered or filed or grouped)
        changed, retired, cards = await self._consider_moved(folders, moved, planned.stamps)
        log.info(
            "suggestions.pass",
            folders=len(moved),
            changed=changed,
            retired=retired,
            numbered=numbered,
        )
        return changed + retired, bool(changed or retired or numbered or filed or grouped or cards)

    async def _by_names(self, by_names: bool) -> tuple[int, int, int]:
        """The rules about FILES under no Site rather than folders that moved: the username numbers,
        the filings a filename's own shape settles, and the posts of what was filed before.
        Returns how many numbers, files and sets."""
        numbered = await self._learn_username_numbers()
        if numbered:
            log.info("suggestions.username_numbers", filled=numbered)
        # And the filings a filename's own SHAPE settles, for the same reason and in the same place:
        # they are about FILES that are under no site, not about folders that changed, so a library
        # nobody has touched since its last pass moves no folder and would be filed by nothing.
        filed = grouped = 0
        if by_names:
            filed = await self.file_from_filenames()
            if filed:
                log.info("suggestions.filed_from_filenames", files=filed)
            # And then the rows this pass filed BEFORE it could read a post, which on an existing
            # library is nearly all of them.
            grouped = await self.group_filings_into_posts()
            if grouped:
                log.info("suggestions.post_sets", sets=grouped)
        return numbered, filed, grouped

    async def _consider_moved(
        self, folders: list[FolderNode], moved: list[FolderNode], stamps: Mapping[str, object]
    ) -> tuple[int, int, set[str]]:
        """Read every folder that moved and take back what no reading asks any more. Returns how
        many claims changed, how many were taken back, and the folders whose may-be card moved."""
        sites = await self._store.site_names()
        rejected = await self._store.rejected_names()
        answered = await self._store.folder_people()
        folders_by_place = await self._store.folder_ids()
        site_folders = await self._store.confirmed_site_folders()
        looking = await self._faces.looking()
        # Everybody the library already holds, read once for the whole pass. It is what lets the
        # reader tell a performer's folder subdivided by scene from a category subdivided by person,
        # and what lets a folder naming two people be read as two rather than as an invented third.
        known = fold_known(await self._store.people_names())
        # The folders swaps made, read once for the pass (see `Arrivals`), and where every folder
        # sits in order, so the folders under one are a range of this list rather than a walk.
        arrivals = Arrivals() if self._swap_folders is None else await self._swap_folders.made()
        places = sorted(folders_by_place)
        refused = await self._store.folder_refusals()

        # The reading is pure work over strings, and there can be thousands of folders in one pass.
        readings = await asyncio.to_thread(_read_all, moved, sites, known)
        # One read for every folder in this pass, rather than one per folder inside the loop below.
        names_by_folder = await self._store.filenames_by_folder([folder.id for folder in moved])

        reads = PassReads(
            rejected=rejected,
            answered=answered,
            folders_by_place=folders_by_place,
            site_folders=site_folders,
            looking=looking,
            known=known,
            sites=sites,
            arrivals=arrivals,
            places=places,
            refused=refused,
        )
        changed = 0
        # What each folder's reading asked this pass, by the folder the question is about: the
        # questions still pending there that no reading asked any more are taken back below.
        made: dict[str, set[str]] = {}
        # The folders whose may-be card on Faces > Groups this pass put up or took back. A card is
        # a write a screen draws, so a pass that wrote nothing else still rings its one bell.
        cards: set[str] = set()
        for folder, reading in zip(moved, readings, strict=True):
            changed += await self._consider(
                folder,
                reading,
                reads,
                filenames=names_by_folder.get(folder.id, []),
                made=made,
                cards=cards,
            )
        retired = await self._store.close_pass(
            retired=await self._unasked(folders, moved, folders_by_place, made, looking=looking),
            signatures=[(folder.id, _signature(folder, stamps.get(folder.id))) for folder in moved],
        )
        return changed, retired, cards

    async def plan(self) -> FolderPass:
        """The folders a pass now would read: those whose signature moved. Writes nothing."""
        by_names = bool(await self._preferences.get_app(FILE_FROM_FILENAMES_KEY))
        folders = await self._store.folders_with_files()
        if not folders:
            return FolderPass(folders=(), stamps={}, moved=(), by_names=by_names)
        stamps = await self._faces.stamps()
        seen = await self._store.signatures()
        moved = tuple(
            folder
            for folder in folders
            if seen.get(folder.id) != _signature(folder, stamps.get(folder.id))
        )
        return FolderPass(folders=tuple(folders), stamps=stamps, moved=moved, by_names=by_names)

    async def _unasked(
        self,
        folders: Sequence[FolderNode],
        moved: Sequence[FolderNode],
        folders_by_place: dict[tuple[str, str], str],
        made: dict[str, set[str]],
        *,
        looking: bool,
    ) -> list[str]:
        """The pending questions this pass's readings no longer ask, by claim id."""
        read = {folder.id for folder in moved}
        unread: set[tuple[str, str]] = set()
        for folder in folders:
            if folder.id in read:
                continue
            for depth in range(len(folder.chain)):
                unread.add((folder.root_id, "/".join(folder.chain[1 : depth + 1])))
        place_of = {folder_id: place for place, folder_id in folders_by_place.items()}
        for folder in moved:
            place_of.setdefault(folder.id, (folder.root_id, folder.rel_path))
        kept = {Evidence.BY_HAND.value} | (set() if looking else {Evidence.FACE_GROUP.value})
        return [
            claim.id
            for claim in await self._store.every_pending()
            if claim.folder_id in made
            and place_of.get(claim.folder_id) not in unread
            and claim.name_key not in made[claim.folder_id]
            and claim.evidence not in kept
        ]
