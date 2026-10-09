# SPDX-License-Identifier: AGPL-3.0-or-later
"""The sharing endpoints, every one an admin route, reads included."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from sift.kernel import wiring
from sift.kernel.access import AccessError, Effect, GrantSource, ObjectType, Repository, Viewer
from sift.kernel.access.catalog import enrichment_of, refused_here, refused_over
from sift.kernel.db import Database
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, current_viewer, require_admin
from sift.slices.sharing.models import (
    GrantResponse,
    GrantSourceResponse,
    GrantWrite,
    OutsideReach,
    ReachReasonResponse,
    ReachReport,
    ReachThrough,
    ReachThroughReport,
    ReachUser,
    ShareableUserResponse,
    VaultSourceResponse,
)
from sift.slices.sharing.service import (
    SERVICE,
    GrantView,
    InertGrant,
    NoSuchObject,
    NoSuchSubject,
    SharingService,
    SubjectNotAGuest,
)

router = APIRouter(tags=["sharing"])


def _service(request: Request) -> SharingService:
    return part_of(request, SERVICE)


def _grants(views: list[GrantView]) -> list[GrantResponse]:
    return [
        GrantResponse(
            subject_user_id=view.subject_user_id,
            username=view.username,
            effect=view.effect,
            created_at=view.created_at,
        )
        for view in views
    ]


@router.get("/sharing/users")
async def shareable_users(
    admin: Annotated[Viewer, Depends(require_admin)],
    service: Annotated[SharingService, Depends(_service)],
) -> list[ShareableUserResponse]:
    """Everybody a grant could name, for the picker beside the share control."""
    return [
        ShareableUserResponse(id=user.id, username=user.username, role=user.role)
        for user in await service.users()
    ]


@router.get("/sharing")
async def grants_on_object(
    admin: Annotated[Viewer, Depends(require_admin)],
    service: Annotated[SharingService, Depends(_service)],
    object_type: Annotated[ObjectType, Query()],
    object_id: Annotated[str | None, Query(max_length=64)] = None,
) -> list[GrantResponse]:
    """Who this thing is shared with, and who it is restricted from; empty means private."""
    try:
        return _grants(await service.grants_on(object_type, object_id))
    except InertGrant as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/sharing/sources")
async def grant_sources(
    admin: Annotated[Viewer, Depends(require_admin)],
    access: Annotated[Repository, Depends(wiring.access)],
    object_type: Annotated[ObjectType, Query()],
    object_id: Annotated[str | None, Query(max_length=64)] = None,
) -> list[GrantSourceResponse]:
    """Every grant that reaches this thing, and where each one was made."""
    del admin
    sources = await access.grant_sources(object_type, object_id)

    # Which of them decided the answer, asked of the resolver rather than a second copy of it.
    standing: dict[str, Effect] = {}
    for subject_user_id in {source.subject_user_id for source in sources}:
        # Read from the row: a hand-made viewer would set its own role.
        subject = await access.load_viewer(subject_user_id)
        reaches = subject is not None and await _reaches(access, subject, object_type, object_id)
        standing[subject_user_id] = Effect.SHARE if reaches else Effect.RESTRICT

    return [
        GrantSourceResponse(
            subject_user_id=source.subject_user_id,
            username=source.username,
            effect=source.effect,
            source_type=source.source_type,
            source_id=source.source_id,
            source_name=source.source_name,
            here=source.source_type is object_type and source.source_id == object_id,
            decides=source.effect is standing[source.subject_user_id],
        )
        for source in sources
    ]


@router.get("/sharing/reach")
async def reach_report(
    viewer: Annotated[Viewer, Depends(require_admin)],
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    object_type: Annotated[ObjectType, Query()],
    object_id: Annotated[str | None, Query(max_length=64)] = None,
) -> ReachReport:
    """Who else can see this thing and through what: the verdict decides, the grants explain."""
    try:
        reaches = await access.reach_of(object_type, object_id)
        sources = await access.grant_sources(object_type, object_id)
    except AccessError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    # Only while their own Hidden is open: what conceals a thing is named by naming it.
    concealing = (
        await access.vault_sources(viewer, object_type, object_id) if viewer.show_hidden else []
    )

    by_user: dict[str, list[GrantSource]] = {}
    for source in sources:
        by_user.setdefault(source.subject_user_id, []).append(source)

    return ReachReport(
        subject_type=object_type,
        subject_id=object_id,
        hidden=any(source.here for source in concealing),
        concealed=bool(concealing),
        outside=await _outside(database, object_type, object_id),
        users=[
            ReachUser(
                id=reach.user_id,
                name=reach.username,
                role=reach.role.value,
                disabled=reach.disabled,
                sees=reach.sees,
                through=[
                    _through(source, object_type, object_id, sees=reach.sees)
                    for source in by_user.get(reach.user_id, [])
                ],
            )
            for reach in reaches
            if reach.user_id != viewer.id
        ],
    )


_OUTSIDE_KIND = {
    ObjectType.ITEM: "asset",
    ObjectType.PERSON: "person",
    ObjectType.SITE: "site",
    ObjectType.TAG: "tag",
    ObjectType.FOLDER: "folder",
}


async def _outside(
    database: Database, object_type: ObjectType, object_id: str | None
) -> OutsideReach | None:
    """Where the thing stands with the world outside this device; None for a kind never told."""
    kind = _OUTSIDE_KIND.get(object_type)
    if kind is None or not object_id:
        return None
    runs = await enrichment_of(database, kind, object_id)
    last = runs[0] if runs else None
    return OutsideReach(
        enrich_refused_here=await refused_here(database, "enrich", kind, object_id),
        enrich_refused=await refused_over(database, "enrich", kind, object_id),
        enriched_at=last.at if last else None,
        enriched_by=(last.box_name or None) if last else None,
        swap_refused_here=await refused_here(database, "swap", kind, object_id),
        swap_refused=await refused_over(database, "swap", kind, object_id),
    )


@router.get("/sharing/reach/through")
async def reach_through_files(
    viewer: Annotated[Viewer, Depends(require_admin)],
    access: Annotated[Repository, Depends(wiring.access)],
    object_type: Annotated[ObjectType, Query()],
    user: Annotated[str, Query(min_length=1, max_length=64)],
    object_id: Annotated[str | None, Query(max_length=64)] = None,
) -> ReachThroughReport:
    """Why one user can see one entity when nothing was said about it; one user per request."""
    del viewer
    try:
        explained = await access.reach_through_files(object_type, object_id, user)
    except AccessError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    return ReachThroughReport(
        reasons=[
            ReachReasonResponse(
                kind=reason.source_type,
                id=reason.source_id,
                name=reason.source_name,
                files=reason.files,
            )
            for reason in explained.reasons
        ],
        files=explained.files,
        complete=explained.complete,
    )


def _through(
    source: GrantSource, object_type: ObjectType, object_id: str | None, *, sees: bool
) -> ReachThrough:
    """One grant as a line of the report, and whether the verdict says it decided the answer."""
    here = source.source_type is object_type and source.source_id == object_id
    if source.effect is Effect.RESTRICT:
        return ReachThrough(
            kind=source.source_type,
            id=source.source_id,
            name=source.source_name,
            how="restricted",
            decides=not sees,
        )
    return ReachThrough(
        kind=source.source_type,
        id=source.source_id,
        name=source.source_name,
        how="shared" if here else "inherited",
        decides=sees,
    )


@router.get("/sharing/hidden-by")
async def vault_sources(
    viewer: Annotated[Viewer, Depends(current_viewer)],
    access: Annotated[Repository, Depends(wiring.access)],
    object_type: Annotated[ObjectType, Query()],
    object_id: Annotated[str | None, Query(max_length=64)] = None,
) -> list[VaultSourceResponse]:
    """What you have hidden that keeps this off your screen; only while Hidden is open."""
    if not viewer.show_hidden:
        return []
    return [
        VaultSourceResponse(
            source_type=source.source_type,
            source_id=source.source_id,
            source_name=source.source_name,
            here=source.here,
        )
        for source in await access.vault_sources(viewer, object_type, object_id)
    ]


async def _reaches(
    access: Repository, subject: Viewer, object_type: ObjectType, object_id: str | None
) -> bool:
    """Whether this user can actually see the thing, asked of the resolver."""
    if object_type is ObjectType.ITEM and object_id is not None:
        return await access.can_view(subject, object_id)
    if object_type is ObjectType.FOLDER and object_id is not None:
        return await access.can_view_folder(subject, object_id)
    grants = await access.grants_of(subject.id)
    reaching = [
        grant
        for grant in grants
        if (grant.object_type is object_type and grant.object_id == object_id)
        or grant.object_type is ObjectType.GLOBAL
    ]
    return bool(reaching) and all(grant.effect is not Effect.RESTRICT for grant in reaching)


@router.put("/sharing", dependencies=[Depends(csrf_protect)])
async def share(
    body: GrantWrite,
    admin: Annotated[Viewer, Depends(require_admin)],
    service: Annotated[SharingService, Depends(_service)],
) -> list[GrantResponse]:
    """Share this thing with somebody, or restrict it from them; answers the whole panel."""
    try:
        return _grants(
            await service.share(
                admin, body.object_type, body.object_id, body.subject_user_id, body.effect
            )
        )
    except (NoSuchSubject, NoSuchObject) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except SubjectNotAGuest as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except InertGrant as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.post("/sharing/revoke", dependencies=[Depends(csrf_protect)])
async def revoke(
    body: GrantWrite,
    admin: Annotated[Viewer, Depends(require_admin)],
    service: Annotated[SharingService, Depends(_service)],
) -> list[GrantResponse]:
    """Take one grant back, effective on the subject's very next request."""
    try:
        return _grants(
            await service.revoke(
                admin, body.object_type, body.object_id, body.subject_user_id, body.effect
            )
        )
    except NoSuchObject as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InertGrant as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
