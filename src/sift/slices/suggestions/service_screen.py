# SPDX-License-Identifier: AGPL-3.0-or-later
"""The review screen's reads, every one resolved against the user it is served to."""

from __future__ import annotations

from collections.abc import Sequence

from sift.kernel.access import (
    Folder,
    PersonSuggestion,
    Viewer,
    art_of_files,
)
from sift.kernel.attribution import FolderFaces
from sift.kernel.serving import face_version
from sift.slices.suggestions.naming import (
    fold,
    near_misses,
    person_in_filename,
)
from sift.slices.suggestions.service_base import (
    MAX_OUTSTANDING,
    PAGE,
    Filed,
    Outline,
    Page,
    Proposal,
    SuggestionBase,
)
from sift.slices.suggestions.store import Claim


def _a_face_to_show(group_id: str | None, faces: FolderFaces) -> str | None:
    """The picture for a claim: its own group's face, or the folder's biggest group as it stands.

    **A claim keeps the group id it was made with, and groups do not survive being rebuilt.**
    """
    portrait = faces.portraits.get(group_id or "")
    if portrait is not None:
        return portrait
    if not faces.piles:
        return None
    leader = max(faces.piles.items(), key=lambda pair: (pair[1], pair[0]))[0]
    return faces.portraits.get(leader)


class ScreenMixin(SuggestionBase):
    """The questions, the outline and the filed list, scoped to who is looking."""

    # --- the screen ----------------------------------------------------------------------------

    async def art_of(self, asset_ids: Sequence[str], *, stamp: int) -> dict[str, str | None]:
        """The picture token for each of these files, so a card's stills may be kept."""
        return await art_of_files(self._store.database, asset_ids, stamp=stamp)

    async def pending(self, viewer: Viewer, *, limit: int = PAGE, offset: int = 0) -> Page:
        """The questions still outstanding, resolved against whoever is asking.

        **Scoped first, then paged**
        """
        scoped = await self._scoped(viewer)
        begin = max(0, offset)
        shown = scoped[begin : begin + max(1, limit)]
        names = await self._store.people_names()
        # The picture for each folder on the page: sorting a folder's files is asked for these
        # and never for the whole list.
        covers = await self._access.folder_covers(viewer, [folder.id for _c, folder, _f in shown])
        faces: dict[str, FolderFaces] = {}
        for claim, _folder, _files in shown:
            if claim.folder_id not in faces:
                faces[claim.folder_id] = await self._faces.faces_in(claim.folder_id)
        # Everything else a row asks, asked once for the page.
        dissenting = await self._access.visible_of(
            viewer, sorted({one for found in faces.values() for one in found.dissenting})
        )
        nearly = {claim.id: _the_near_miss(claim, names) for claim, _folder, _files in shown}
        wanted = sorted({one for one in nearly.values() if one is not None})
        people = await self._access.visible_people(viewer, wanted) if wanted else {}
        sites = sorted({claim.folder_id for claim, _f, _n in shown if claim.kind == "site"})
        filenames = await self._store.filenames_by_folder(sites) if sites else {}
        items = [
            _resolved(
                viewer,
                claim,
                folder,
                files,
                covers.get(folder.id, ""),
                faces=faces[claim.folder_id],
                dissenting=dissenting,
                near_miss=nearly[claim.id] if nearly[claim.id] in people else None,
                filenames=filenames.get(claim.folder_id, []),
            )
            for claim, folder, files in shown
        ]
        return Page(items=items, total=len(scoped))

    async def outline(self, viewer: Viewer, *, limit: int = PAGE) -> Outline:
        """The front of the queue as a card draws it: the count and the stills.

        **The same scoped list `pending` pages, and no row of it resolved.**
        """
        scoped = await self._scoped(viewer)
        shown = scoped[: max(1, limit)]
        if not shown:
            return Outline(total=len(scoped))
        covers = await self._access.folder_covers(viewer, [folder.id for _c, folder, _f in shown])
        return Outline(
            total=len(scoped),
            covers=[(one.id, covers.get(where.id, "")) for one, where, _f in shown],
        )

    async def _scoped(self, viewer: Viewer) -> list[tuple[Claim, Folder, int]]:
        """Every question still open that this user may be told about, in screen order."""
        claims = await self._store.pending(limit=MAX_OUTSTANDING, offset=0)
        folder_ids = [claim.folder_id for claim in claims]
        folders = await self._access.visible_folders_of(viewer, folder_ids)
        summaries = await self._access.folder_summaries(viewer, list(folders))
        scoped: list[tuple[Claim, Folder, int]] = []
        for claim in claims:
            folder = folders.get(claim.folder_id)
            files = summaries.get(claim.folder_id)
            if folder is None or files is None:
                continue
            scoped.append((claim, folder, files))
        return scoped

    async def position_of(self, viewer: Viewer, claim_id: str) -> int | None:
        """Where one question sits in the queue, counting from zero, or None."""
        for index, (claim, _folder, _files) in enumerate(await self._scoped(viewer)):
            if claim.id == claim_id:
                return index
        return None

    async def visible_of(self, viewer: Viewer, asset_ids: Sequence[str]) -> set[str]:
        """Which of these files this user may be shown, for a caller outside this service."""
        return await self._access.visible_of(viewer, asset_ids)

    async def filed(self, viewer: Viewer) -> list[Filed]:
        """What was filed without anybody being asked, as this user may be told about it."""
        scoped = await self._filed_scoped(viewer)
        # One read for every count rather than one per row, and only for the rows this user may
        # be told about. The count is the folder's own and this user's own (see `filed_counts`).
        counts = await self._access.filed_counts(
            viewer, [(folder.id, person.id) for folder, person in scoped]
        )
        out: list[Filed] = []
        for folder, person in scoped:
            folder_id, person_id = folder.id, person.id
            files = counts.get((folder_id, person_id), 0)
            out.append(
                Filed(
                    person_id=person_id,
                    person=person.name,
                    folder_id=folder_id,
                    folder=folder.name,
                    path=folder.rel_path,
                    files=files,
                    cover_asset_id=person.cover_asset_id,
                    cover_upload_id=person.cover_upload_id,
                    cover_at_ms=person.cover_at_ms,
                    cover_frame=person.cover_frame,
                    cover_track_id=person.cover_track_id,
                    # No read: a person's token says how many times what this user may see has
                    # changed and nothing about the picture, which is the whole of it (see
                    # `face_version`, and the people router, which mints it the same way).
                    art=face_version(viewer.cache_stamp),
                )
            )
        return out

    async def filed_total(self, viewer: Viewer) -> int:
        """How many rows `filed` would hand this user, without counting the files under each."""
        return len(await self._filed_scoped(viewer))

    async def _filed_scoped(self, viewer: Viewer) -> list[tuple[Folder, PersonSuggestion]]:
        """Every folder filed without asking that this user may be told about, with its person."""
        filed = await self._store.filed_without_asking()
        folders = await self._access.visible_folders_of(viewer, [one[0] for one in filed])
        people = await self._access.visible_people(viewer, [one[1] for one in filed])
        scoped: list[tuple[Folder, PersonSuggestion]] = []
        for folder_id, person_id in filed:
            folder = folders.get(folder_id)
            person = people.get(person_id)
            if folder is None or person is None:
                continue
            scoped.append((folder, person))
        return scoped


def _the_near_miss(claim: Claim, names: Sequence[tuple[str, str]]) -> str | None:
    """The one person a claim's name nearly matches, or None for none or several."""
    nearly = near_misses(claim.proposed, names)
    return nearly[0] if len(nearly) == 1 else None


def _resolved(
    viewer: Viewer,
    claim: Claim,
    folder: Folder,
    files: int,
    cover: str,
    *,
    faces: FolderFaces,
    dissenting: set[str],
    near_miss: str | None,
    filenames: Sequence[str],
) -> Proposal:
    """One claim as this user may be told about it, out of what the page has already read.

    The dissenting files are only the ones this user may see: a screen that offered a concealed
    file for unticking would have named it. A near miss is a NAME, and one this user may not be
    shown is not mentioned as a possible merge either.
    """
    per_file: tuple[str, ...] = ()
    if claim.kind == "site":
        # One name however its files spell it: the first spelling seen stands for the rest.
        by_fold: dict[str, str] = {}
        for name in filenames:
            found = person_in_filename(name, after_prefix=True)
            if found and fold(found) not in by_fold:
                by_fold[fold(found)] = found
        per_file = tuple(sorted(by_fold.values()))
    face_id = _a_face_to_show(claim.group_id, faces)
    return Proposal(
        id=claim.id,
        folder_id=claim.folder_id,
        kind=claim.kind,
        proposed=claim.proposed,
        evidence=claim.evidence,
        folder=folder.name,
        path=folder.rel_path,
        files=files,
        group_id=claim.group_id,
        face_id=face_id,
        face_art=None if face_id is None else face_version(viewer.cache_stamp),
        near_miss=near_miss,
        site=claim.site,
        is_username=claim.is_username,
        dissenting=tuple(sorted({one for one in faces.dissenting if one in dissenting})),
        per_file=per_file,
        cover=cover,
    )
