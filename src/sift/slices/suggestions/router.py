# SPDX-License-Identifier: AGPL-3.0-or-later
"""The review surface. Admin-only, and the server is what says so.

`require_admin` sits on all four routes. That is necessary and it is not sufficient, which is why
the service resolves every row against the user asking as well: an admin can conceal things from
themselves, and a list built for "an admin" rather than for *this* admin would show them back what
they hid.

Nothing here applies anything on its own. There is no auto-apply setting in this feature and there
is no route that would need one: a folder name is far weaker evidence than the fingerprint match
that earned one elsewhere, and a wrong attribution in a library nobody is auditing gives no sign
which entries to distrust.
"""

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
    """The folders Sift thinks it can name, and what it thinks each one is.

    The total is the total of what survived scoping rather than the number of rows there are. A
    count larger than the list would publish, in the difference, how many folders this user is
    not being told about, which is the one thing concealment exists to prevent.

    `from` names a row to start the page at, instead of an offset. The wall pages by whole rows,
    so how many cards fit depends on the size of the screen, which means a page NUMBER is not a
    durable thing to put in an address and the row somebody was looking at is. Resolved against this
    same scoped, narrowed list, because a position only means anything in the list it came from.

    A `from` that resolves to nothing serves the page it was on (`near`), or the TOP,
    rather than refusing. Deleted, renamed out of
    the current narrowing, answered by somebody else, or simply not this user's to see all give
    the same answer, which is what stops a link being a way to ask whether something is there. On
    a queue it is also the ordinary case: answering a question is what takes it off the list.
    """
    if start is not None:
        at = await service.position_of(viewer, start)
        offset = resume_at(at, near)
    page = await service.pending(viewer, limit=limit, offset=offset)
    # The stills this page draws, asked ONCE for the page rather than per picture. Without the
    # token every thumbnail here would be addressed bare, which the server answers the careful way,
    # so a card of twenty-four dissenting files would be twenty-four conditional requests a visit.
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
    """What was filed under somebody without anybody being asked.

    Its own route rather than a field on the list, because the list is outstanding work and this is
    work already done, and an empty list of questions with a record of what was answered silently
    beside it is the whole point.
    """
    # `asdict`, not `vars`. Every dataclass in this slice declares `slots=True`, and an object with
    # slots has no `__dict__` for `vars` to read, so `vars` would raise as soon as there was
    # anything at all to report, while answering perfectly on an install where no pass had run yet.
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
    """What a file's own name said about where it came from, grouped by the username it named.

    Declared BEFORE `/suggestions/{claim_id}/...`, and that is load-bearing rather than tidy: those
    are POSTs and this is a GET, so nothing collides today, and the ordering is what keeps that true
    the day one of them grows a read.

    A report and never a question: nothing on this page is waiting on anybody, and what it offers is
    the undo of one file at a time, or of one whole username (`take_back_username`). See
    `FiledFromFilenamesQueue` for why the pass applies itself.

    `from` names the username a page starts at: the group the page was left at, carried in the
    tab's address so the way back lands on the same page. Taking back every filing under a username
    takes it off this list, so a `from` naming nothing is answered with the page it was on
    (`near`), or the top. See `resume_at`.
    """
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
    """No on one row of the filenames page: every file a name filed under this username comes off.

    One decision, recorded by whoever pressed, so the toast's Undo and the record's put every
    filing back as it was. Only the files this viewer may be shown, the files the row counted.
    Nothing left to take back answers no files and no record rather than an error: a second
    press finds the first one's work done.
    """
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
    """Take back, on a row of Added without asking: the person comes off the files the folder pass
    put them on under this folder, the folder is no longer theirs, and Sift never adds it to them
    again without asking. One decision, recorded by whoever pressed, whose Undo puts it all back.

    Nothing to take back (a second press, a folder or person this viewer cannot see) answers no
    files and no record rather than an error, so none of those can be told apart.
    """
    taken = await service.take_back_folder(viewer, folder_id=folder_id, person_id=person_id)
    return FolderTakenBack(files=taken.files, decision_id=taken.decision_id or "")


@router.post("/suggestions/{claim_id}/confirm", dependencies=[Depends(csrf_protect)])
async def confirm_suggestion(
    claim_id: str,
    body: ConfirmRequest,
    service: Annotated[SuggestionService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> ConfirmedView:
    """Yes, and everything that follows from yes, in one press.

    The files are attributed, the face group is named, the folder's spelling becomes an
    also-known-as name, a username folder gets its username linked to the person, and the question
    is never asked again. Making any of that a second press would be the point of this screen missed.
    """
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
    """Not a person. Remembered permanently, and by name rather than by folder.

    Renaming a folder on disk makes it a different folder as far as Sift is concerned, so a no tied
    to the folder would come straight back under the new name. Tied to the name it holds wherever
    that name turns up, which is what "it never comes back" has to mean.
    """
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
    """Name a folder the reader got wrong, or never asked about, as a person or as a site.

    The other four routes all answer a question Sift asked. This is the only one that starts with a
    person, and it is the only way a MISS is ever written down: a folder the reader misread or
    walked past leaves no row at all, so without this the record holds every "you proposed X, wrong"
    and not one "you missed Y".

    It answers exactly as a confirmation does, because it IS one: the claim is written and then put
    through the same path, so the attribution, the alias and the username are decided in one place.
    """
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
