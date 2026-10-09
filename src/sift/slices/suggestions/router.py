# SPDX-License-Identifier: AGPL-3.0-or-later
"""The folder review routes; admin-only, and every row is still resolved against this admin."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from sift.kernel.access import Viewer
from sift.kernel.paging import MAX_PAGE_SIZE, resume_at
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.suggestions.models import (
    ConfirmedView,
    ConfirmRequest,
    FiledFromNameView,
    FiledList,
    FiledView,
    FilenameFilingList,
    FilenameGroupView,
    FolderSetAside,
    FolderTakenBack,
    NameAFolder,
    ProposalList,
    ProposalView,
    UsernameTakenBack,
)
from sift.slices.suggestions.service import (
    PAGE,
    SERVICE,
    NotFound,
    SuggestionError,
    SuggestionService,
)

router = APIRouter(tags=["suggestions"])


def _service(request: Request) -> SuggestionService:
    return part_of(request, SERVICE)


@router.get("/suggestions")
async def list_suggestions(
    service: Annotated[SuggestionService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
    start: Annotated[str | None, Query(alias="from")] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
) -> ProposalList:
    """The folders Sift thinks it can name, scoped to this viewer; `from` resumes at a row."""
    if start is not None:
        at = await service.position_of(viewer, start)
        offset = resume_at(at, near)
    page = await service.pending(viewer, limit=limit, offset=offset)
    # Cache tokens for the page's stills, asked once for the page.
    art = await service.art_of(
        [
            asset_id
            for item in page.items
            for asset_id in (item.cover, *item.dissenting)
            if asset_id
        ],
        stamp=viewer.cache_stamp,
    )
    return ProposalList(
        proposals=[
            ProposalView(
                id=item.id,
                kind=item.kind,
                proposed=item.proposed,
                evidence=item.evidence,
                folder=item.folder,
                folder_id=item.folder_id,
                path=item.path,
                files=item.files,
                group_id=item.group_id,
                face_id=item.face_id,
                face_art=item.face_art,
                near_miss=item.near_miss,
                site=item.site,
                is_username=item.is_username,
                dissenting=list(item.dissenting),
                per_file=list(item.per_file),
                cover=item.cover,
                art={
                    asset_id: token
                    for asset_id in (item.cover, *item.dissenting)
                    if asset_id and (token := art.get(asset_id)) is not None
                },
            )
            for item in page.items
        ],
        total=page.total,
        offset=offset,
    )


@router.get("/suggestions/filed")
async def filed(
    service: Annotated[SuggestionService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FiledList:
    """What was filed under somebody without anybody being asked."""
    # `asdict`, not `vars`: these dataclasses have slots and no `__dict__`.
    return FiledList(filed=[FiledView(**asdict(one)) for one in await service.filed(viewer)])


@router.get("/suggestions/filenames")
async def filed_from_filenames(
    service: Annotated[SuggestionService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
    start: Annotated[str | None, Query(alias="from", max_length=200)] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
) -> FilenameFilingList:
    """What files' own names said, grouped by username; declared before the `{claim_id}` routes."""
    if start is not None:
        offset = resume_at(await service.filing_position(viewer, start), near)
    groups, total = await service.filings_by_username(viewer, limit=limit, offset=offset)
    return FilenameFilingList(
        groups=[
            FilenameGroupView(
                username_id=group.username_id,
                username=group.name,
                site=group.site,
                person_id=group.person_id,
                files=group.files,
                shown=[
                    FiledFromNameView(
                        asset_id=one.asset_id,
                        filename=one.filename,
                        decision_id=one.decision_id,
                        art=one.art,
                    )
                    for one in group.shown
                ],
            )
            for group in groups
        ],
        total=total,
        offset=offset,
    )


@router.post("/suggestions/filenames/{username_id}/undo", dependencies=[Depends(csrf_protect)])
async def take_back_username(
    username_id: str,
    service: Annotated[SuggestionService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> UsernameTakenBack:
    """No on one filenames row: every name filing under this username comes off, as one decision."""
    taken = await service.take_back_username(viewer, username_id=username_id)
    return UsernameTakenBack(files=taken.files, decision_id=taken.decision_id or "")


@router.post(
    "/suggestions/filed/{folder_id}/people/{person_id}/undo", dependencies=[Depends(csrf_protect)]
)
async def take_back_folder(
    folder_id: str,
    person_id: str,
    service: Annotated[SuggestionService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FolderTakenBack:
    """Take back one silent folder filing and refuse it from now on, as one undoable decision."""
    taken = await service.take_back_folder(viewer, folder_id=folder_id, person_id=person_id)
    return FolderTakenBack(files=taken.files, decision_id=taken.decision_id or "")


@router.post("/suggestions/{claim_id}/confirm", dependencies=[Depends(csrf_protect)])
async def confirm_suggestion(
    claim_id: str,
    body: ConfirmRequest,
    service: Annotated[SuggestionService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> ConfirmedView:
    """Yes, and everything that follows from it, in one press."""
    try:
        applied = await service.confirm(viewer, claim_id, skip=body.skip)
    except NotFound as refused:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(refused)) from refused
    except SuggestionError as refused:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from refused
    return ConfirmedView(
        person_id=applied.person_id,
        created=applied.created,
        files=applied.files,
        faces=applied.faces,
        alias=applied.alias,
        username_linked=applied.username_linked,
        people=applied.people,
        site=applied.site,
        decision_id=applied.decision_id,
    )


@router.post("/suggestions/{claim_id}/reject", dependencies=[Depends(csrf_protect)])
async def reject_suggestion(
    claim_id: str,
    service: Annotated[SuggestionService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FolderSetAside:
    """Not a person: remembered for good, by name rather than by folder."""
    try:
        settled = await service.reject(viewer, claim_id)
    except NotFound as refused:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(refused)) from refused
    return FolderSetAside(settled=settled.settled, decision_id=settled.decision_id)


@router.post("/suggestions/folder/{folder_id}", dependencies=[Depends(csrf_protect)])
async def say_who_a_folder_is(
    folder_id: str,
    body: NameAFolder,
    service: Annotated[SuggestionService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> ConfirmedView:
    """Name a folder the reader got wrong or missed, as a person or a site; answers as a confirm."""
    try:
        applied = await service.say_who_a_folder_is(viewer, folder_id, body.name, kind=body.kind)
    except NotFound as refused:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(refused)) from refused
    except SuggestionError as refused:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refused)) from refused
    return ConfirmedView(
        person_id=applied.person_id,
        created=applied.created,
        files=applied.files,
        faces=applied.faces,
        alias=applied.alias,
        username_linked=applied.username_linked,
        people=applied.people,
        site=applied.site,
        decision_id=applied.decision_id,
    )
