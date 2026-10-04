# SPDX-License-Identifier: AGPL-3.0-or-later
"""The sharing endpoints. Every one of them is an admin route.

Not "admin by default" or "admin for the writes": all of them, reads included. Who else an item
is shared with is a fact about other people's access, and a guest asking it would be asking who
else is in the house. The engine underneath would happily answer; the door is here.
"""

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
    """Who this thing is shared with, and who it is restricted from.

    An empty list is the ordinary answer and means private, which is the default for everything
    in the library, not an error and not a missing object.
    """
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
    """Every grant that reaches this thing, and where each one was made.

    The route behind "why is this shared when I never shared it". The one above answers what was
    written on this exact row, which is what the controls change; a file inside three folders,
    carrying four tags and sitting in two collections has eight places a share could have come from,
    and without this the only way to find which is to open all of them.

    Admin-only, like everything else in this slice. A guest is told nothing about grants, including
    the ones about them.
    """
    del admin
    sources = await access.grant_sources(object_type, object_id)

    # Which of them actually decided the answer, asked of the one query that decides it.
    #
    # Several grants can reach one file and disagree, and the panel has to be able to grey out the
    # ones that lost: a share that a restrict is beating is worth seeing and must not read as
    # though it is in force. Working that out here would be a second copy of the resolve, so this
    # asks the resolver instead, once per user named: whatever it says a guest can see IS the
    # answer, and every grant pointing the other way lost.
    standing: dict[str, Effect] = {}
    for subject_user_id in {source.subject_user_id for source in sources}:
        # Read from the row rather than built here. A hand-made viewer sets its own role, which is
        # the one thing a caller must never decide, and it would answer for a user that has
        # since been blocked or deleted as though nothing had happened.
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
    """Who, other than you, can see this thing, and through what.

    The route behind "can anybody else see this". `/sharing` says what was decided here and
    `/sharing/sources` says where the decisions came from; neither answers the question somebody
    actually asks, which is whether the thing is reachable at all and by whom: a file inside a
    shared folder, carrying a shared tag, released by a label under a shared network has three
    answers and nothing written on it.

    **The yes comes from the stored verdict and the explanation comes from the grants.** One read
    decides (`reach_of`, which probes `viewer_assets`) and the other only says why (`grant_sources`,
    which lists the rows that reach it). Written the other way round (resolve the grants here and
    report the result), it would be a second copy of the access rules living in a slice, and the
    day it drifted the report would confidently describe a library that does not exist.

    `decides` is settled by comparing each grant against that verdict rather than by re-running any
    ladder: whatever the verdict says IS the answer, so a grant pointing the other way lost. That is
    the same reasoning `/sharing/sources` uses and a stronger form of it: it asks the table both
    are downstream of, so it is right for a tag and a Photo Set as well as for a file.

    Admin-only, like almost everything in this slice, and here the reason is at its plainest: the
    report is a list of what other users can see.

    THE CALLER IS LEFT OUT. They are reading the report, so "you can see it" is the one row that
    tells them nothing, and on a library with one user it would be the whole of it, which reads
    as an answer when it is a mirror.
    """
    try:
        reaches = await access.reach_of(object_type, object_id)
        sources = await access.grant_sources(object_type, object_id)
    except AccessError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    # Only while their own Hidden is open, exactly as `/sharing/hidden-by` answers: what conceals a
    # thing is named by naming it, so a shut vault is told the same nothing every other read tells.
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


#: The kinds a stash-box or a swap is ever told about, in the refusal's own word for each.
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
    """Where the thing stands with the world outside this device. See `OutsideReach`.

    Read after the reach itself has answered, which is what established the thing exists and this
    admin may see it; a kind no refusal covers answers None."""
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
    """WHY one user can see one entity, when nothing was ever said about the entity.

    The second half of the report above, and the answer to the one thing it could not say. A
    person, a tag, a Site, a collection or a Photo Set is on somebody else's wall because ONE
    file under it can be reached (that is the rule, and it is what `reach_of` asks), so the yes
    is true and routinely has no grant naming the entity to put beside it. This names what does.

    ITS OWN ROUTE AND ITS OWN CEILING, which is why it is not folded into the report. This reads a
    page of an entity's files per user, where the report above is one statement for every
    user at once; asked for everybody on arrival it would turn a panel somebody opens to check
    one thing into a read per user whether or not any of them needed it. The client asks it for
    the users whose yes has nothing behind it, which is the only case it answers differently.

    ONE USER AT A TIME, named as a parameter. The caller is an admin reading a report about
    somebody else, exactly as with the rest of this slice: the user is the subject of the
    question rather than whoever is asking it.

    Admin-only, and here the reason is at its plainest twice over: it is a list of what another
    user can see, and it names the folders and collections that let them.
    """
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
    """One grant as a line of the report, and whether it is the line that decided the answer.

    `here` is the same comparison `/sharing/sources` makes and it is what separates the two words a
    share can wear: said about this very thing, or reaching it from something above. A restrict is
    one word either way (where it was made changes which name the sentence carries and not what it
    did), so the third word is not split.

    `decides` is read off the verdict and not off the ladder. Whatever the user can actually see
    is the answer, so a share on a user who cannot see the thing lost to something, and a
    restrict on a user who can see it did not reach what they see it through: a restrict is
    absolute where it reaches, so that is the only way one is not in force.
    """
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
    """What YOU have hidden that is keeping this off your screen, and where each one is set.

    Hidden's half of the question the route above answers about sharing. A file can be concealed by
    itself, by a folder above it, by the library it is in, by somebody it is attributed to, or by a
    tag, collection or site it belongs to, and the thing that did it is, by definition, not on the
    screen. Without this, working it out means unhiding one at a time until the file comes back.

    Every user may ask, and every user gets their own answer. Hiding is personal, so the same
    file can be gone for one user and perfectly ordinary for another; this names only what the
    caller hid, which is the only list that explains what they are looking at.

    **It answers only while Hidden is open.** These names are the thing being concealed: told
    "hidden by the person Wren Hale", somebody who has not entered the PIN has learned that Wren
    Hale is in the library, that she is hidden, and that this file is hers. Shut, the honest
    answer is the same one the rest of the app gives: there is nothing here.
    """
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
    """Whether this user can actually see the thing, asked of the resolver.

    Files and folders have their own scoped reads and are asked directly. Everything else (a tag,
    a person, a collection, a site) has nothing above it, so the only grants that reach one are
    its own and the global one, and there the rule is the whole of it: a restrict beats a share.
    """
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
    """Share this thing with somebody, or restrict it from them.

    A PUT rather than a POST because it is idempotent by design: the body says what the state
    should be, and saying it twice is one decision made twice. What comes back is the whole panel,
    not the row just written: a restrict added inside a share has to be read next to the share it
    beats.
    """
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
    """Take one grant back. It stops applying on the subject's very next request.

    Not a DELETE, because what identifies the row is four fields and a delete carrying a body is a
    request half the things between here and a browser will quietly drop.
    """
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
