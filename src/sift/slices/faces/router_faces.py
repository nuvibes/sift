# SPDX-License-Identifier: AGPL-3.0-or-later
"""One face: its picture and cover, a Yes or a No on it, and one group's faces set aside or
brought back."""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Request,
    Response,
    status,
)

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.jobs import JobQueue
from sift.kernel.serving import face_version, keeps, serve_file
from sift.slices.auth import csrf_protect, current_viewer, require_admin
from sift.slices.faces.jobs import (
    ask_for_rematching,
)
from sift.slices.faces.models_http import (
    ConfirmWrite,
    GroupSetAside,
    PileTracks,
    RejectWrite,
)
from sift.slices.faces.router_common import (
    _missing,
    _off,
    _service,
)
from sift.slices.faces.service import (
    FacesDisabled,
    FaceService,
)

router = APIRouter(tags=["faces"])


@router.get("/faces/{track_id}/crop")
async def face_crop(
    track_id: str,
    request: Request,
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The cropped face itself.

    **The route the whole visibility rule exists for**: a crop is a piece of its file, served only
    to somebody who may open that file, asked of the resolver; otherwise the 404 an unknown track
    gets. Not gated on the feature, so a crop already on screen still draws.
    """
    seen = await service.see_face(viewer, track_id)
    if seen is None:
        raise _missing()
    path = await service.crop_file(track_id)
    if path is None:
        raise _missing()
    # A crop is never rewritten, so its address can be kept without asking; concealed, it is
    # refused regardless, so shutting the vault takes effect on the next use.
    return await serve_file(
        request,
        path,
        media_type="image/jpeg",
        headers=keeps(request, version=face_version(viewer.cache_stamp), concealed=seen.concealed),
    )


@router.get("/faces/{track_id}/cover")
async def face_cover(
    track_id: str,
    request: Request,
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The same face, cut for a screen rather than for the recognizer, whose 112-pixel square on
    the eyes, nose and mouth is no picture blown up.

    Cut when the cover is chosen (or on first request for an older one). With nothing to cut from
    it falls back to the square, served the careful way: one address serves two pictures, so only
    a real cover is given an address a browser may keep. The crop's visibility rule, asked the
    same way.
    """
    seen = await service.see_face(viewer, track_id)
    if seen is None:
        raise _missing()
    cut = await service.cover_file(track_id) if await service.enabled() else None
    path = cut if cut is not None else await service.crop_file(track_id)
    if path is None:
        raise _missing()
    return await serve_file(
        request,
        path,
        media_type="image/jpeg",
        headers=keeps(
            request,
            version=face_version(viewer.cache_stamp) if cut is not None else None,
            concealed=seen.concealed,
        ),
    )


# --- deciding ------------------------------------------------------------------------------------


@router.post("/faces/{track_id}/confirm", dependencies=[Depends(csrf_protect)])
async def confirm_face(
    track_id: str,
    body: ConfirmWrite,
    service: Annotated[FaceService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Agree that an appearance is somebody, and let it improve future matching.

    Admin-only, and the person is resolved even so: an admin with Hidden shut must not learn a
    concealed person exists. The face becomes a reference, so a re-match is asked for.
    """
    if await access.visible_person(viewer, body.person_id) is None:
        raise _missing()
    if not await service.may_see_crop(viewer, track_id):
        raise _missing()
    try:
        await service.confirm(track_id, body.person_id, viewer=viewer)
    except FacesDisabled:
        raise _off() from None
    await ask_for_rematching(queue)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/faces/{track_id}/reject", dependencies=[Depends(csrf_protect)])
async def reject_face(
    track_id: str,
    body: RejectWrite,
    service: Annotated[FaceService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Say an appearance is not somebody. Remembered, so the same suggestion is not offered again.

    Rejects that FACE. Everybody else in the same file is untouched and stays attributed, which is
    the property the whole review loop rests on.
    """
    if await access.visible_person(viewer, body.person_id) is None:
        raise _missing()
    if not await service.may_see_crop(viewer, track_id):
        raise _missing()
    try:
        await service.reject(track_id, body.person_id)
    except FacesDisabled:
        raise _off() from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ADMIN, like every way of reading the review queue (a comment, since a route's docstring is its
# published OpenAPI description): a pile's membership is the grouping itself, a fact about the
# review queue. The viewer scope in the service is the second half for an admin with Hidden shut.
@router.get("/faces/groups/{pile_id}/tracks")
async def pile_tracks(
    pile_id: str,
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> PileTracks:
    """Every face in this pile this user may see, by id. See `PileTracks`."""
    try:
        ids = await service.pile_track_ids(viewer, pile_id)
    except FacesDisabled:
        raise _off() from None
    if ids is None:
        raise _missing()
    return PileTracks(track_ids=ids, total=len(ids))


@router.post("/faces/groups/{pile_id}/ignore", dependencies=[Depends(csrf_protect)])
async def ignore_group(
    pile_id: str,
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> GroupSetAside:
    """Discard a pile. It stays listed under Discarded and can be brought back, never deleted.

    The address keeps the stored word, `ignore` (`queue.SET_ASIDE`).
    """
    try:
        recorded = await service.ignore(pile_id, viewer)
    except FacesDisabled:
        raise _off() from None
    if recorded is None:
        raise _missing()
    return GroupSetAside(settled=True, decision_id=recorded)


@router.post("/faces/groups/{pile_id}/restore", dependencies=[Depends(csrf_protect)])
async def restore_group(
    pile_id: str,
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Bring a discarded pile back, with its faces."""
    try:
        if not await service.restore(pile_id):
            raise _missing()
    except FacesDisabled:
        raise _off() from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
