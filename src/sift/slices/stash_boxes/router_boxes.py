# SPDX-License-Identifier: AGPL-3.0-or-later
"""Configuring the boxes, and the questions asked of them by hand: a name, a file, a picture."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.covers import SubjectCovers
from sift.kernel.ledger import Actor, Object
from sift.slices.auth import csrf_protect, master_key, require_admin
from sift.slices.stash_boxes.models import (
    AddBox,
    BoxList,
    BoxResponse,
    CheckResult,
    EditBox,
    KeepPicture,
    KeyWrite,
    LookUpResult,
)
from sift.slices.stash_boxes.queue import name_of
from sift.slices.stash_boxes.router_base import (
    _answer,
    _exists,
    _kept_local,
    _missing,
    _needs_a_key,
    _service,
    _subject,
)
from sift.slices.stash_boxes.service import (
    KeptLocal,
    StashBoxService,
)

router = APIRouter(tags=["stash-boxes"])


@router.get("/stash-boxes")
async def list_boxes(
    service: Annotated[StashBoxService, Depends(_service)],
    key: Annotated[bytes | None, Depends(master_key)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> BoxList:
    """Every stash-box that has been configured, with whether each has a key rather than the key.

    `key_ready` is asked of the SERVICE rather than worked out here, and it is the same call a
    lookup makes. That is the point of it: two reads could tell two different halves of the truth
    about the same box (a green "Key saved" here, "this one has no key Sift can read" there), and
    one predicate cannot disagree with itself.
    """
    return BoxList(
        boxes=[
            BoxResponse(**asdict(one), key_ready=await service.key_ready(one.id, key))
            for one in await service.boxes()
        ]
    )


@router.post(
    "/stash-boxes", status_code=status.HTTP_201_CREATED, dependencies=[Depends(csrf_protect)]
)
async def add_box(
    body: AddBox,
    service: Annotated[StashBoxService, Depends(_service)],
    key: Annotated[bytes | None, Depends(master_key)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> BoxResponse:
    """Configure a stash-box. The key is sealed on the way in and never comes back out."""
    box_id = await service.add(
        name=body.name,
        endpoint=body.endpoint,
        api_key=body.api_key,
        master_key=_needs_a_key(key) if body.api_key else None,
        route=body.route,
        requests_per_minute=body.requests_per_minute,
    )
    for one in await service.boxes():
        if one.id == box_id:
            return BoxResponse(**asdict(one))
    raise _missing()  # pragma: no cover (written a statement ago)


@router.put("/stash-boxes/{box_id}", dependencies=[Depends(csrf_protect)])
async def edit_box(
    box_id: str,
    body: EditBox,
    service: Annotated[StashBoxService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Switch a box on or off, re-route it, or change how fast it may be asked.

    Only the fields actually sent are written. A model of optional fields read attribute by
    attribute is a full-row writer in disguise, and would switch a box off for mentioning its pace.
    """
    sent = body.model_fields_set
    if not await _exists(service, box_id):
        raise _missing()
    if "enabled" in sent and body.enabled is not None:
        await service.set_enabled(box_id, body.enabled)
    if "route" in sent:
        await service.set_route(box_id, body.route)
    if "requests_per_minute" in sent and body.requests_per_minute is not None:
        await service.set_pace(box_id, body.requests_per_minute)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/stash-boxes/{box_id}/key", dependencies=[Depends(csrf_protect)])
async def replace_key(
    box_id: str,
    body: KeyWrite,
    service: Annotated[StashBoxService, Depends(_service)],
    key: Annotated[bytes | None, Depends(master_key)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Replace a box's key. What it had already said is thrown away with it: a new key can mean a
    different account, and an answer given to the old one is not evidence about the new."""
    if not await _exists(service, box_id):
        raise _missing()
    await service.set_key(box_id, body.api_key, _needs_a_key(key))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/stash-boxes/{box_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def forget_box(
    box_id: str,
    service: Annotated[StashBoxService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Remove a box. What it had said goes with it: a cached answer outliving its source is a fact
    with no provenance."""
    if not await _exists(service, box_id):
        raise _missing()
    await service.forget(box_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/stash-boxes/{box_id}/answers",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def forget_answers(
    box_id: str,
    service: Annotated[StashBoxService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Throw away what a box has said, so the next question is asked for real. The refresh control."""
    if not await _exists(service, box_id):
        raise _missing()
    await service.forget_answers(box_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/stash-boxes/{box_id}/check", dependencies=[Depends(csrf_protect)])
async def check_box(
    box_id: str,
    service: Annotated[StashBoxService, Depends(_service)],
    key: Annotated[bytes | None, Depends(master_key)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> CheckResult:
    """Ask a box the smallest real question there is, and say whether it answered.

    Answers 200 either way. Whether a third-party service is reachable is a fact to show, not a
    failure of this request, and a 502 here would read as Sift being broken.
    """
    if not await _exists(service, box_id):
        raise _missing()
    problem = await service.check(box_id, key)
    return CheckResult(ok=problem is None, problem=problem)


@router.get("/stash-boxes/look-up")
async def look_up(
    service: Annotated[StashBoxService, Depends(_service)],
    key: Annotated[bytes | None, Depends(master_key)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    term: Annotated[str, Query(min_length=1, max_length=200)],
) -> LookUpResult:
    """What every switched-on stash-box knows about a name. Reads only, and writes nothing anywhere.

    This is the surface that proves a key works. Nothing here is applied to anything in the
    library: it shows what came back, and the person reading it decides what that is worth.
    """
    return LookUpResult(answers=[_answer(one) for one in await service.search(term, key)])


@router.get("/stash-boxes/recognise/{asset_id}")
async def recognise(
    asset_id: str,
    service: Annotated[StashBoxService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    key: Annotated[bytes | None, Depends(master_key)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> LookUpResult:
    """What the stash-boxes make of one FILE, from hashes Sift has already computed.

    The scoped read comes first, so a file this user may not be shown answers "no such file"
    rather than having its fingerprints sent to three third-party services.

    Nothing is applied. A perceptual-only answer is capped and fuzzy by nature (see the adapter),
    so what comes back here is a suggestion for a person to agree with, never a decision.
    """
    view = await access.get_asset(viewer, asset_id)
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such file")
    hashes = {
        name: value
        for name, value in (
            ("oshash", view.asset.oshash),
            ("phash", view.asset.video_phash),
        )
        if value
    }
    if not hashes:
        # Not an error: a photograph has no video fingerprint and a clip that has not been through
        # the catch-up pass has none yet. Nothing to ask means nothing was asked.
        return LookUpResult(answers=[])
    try:
        answers = await service.recognise(asset_id, hashes, key)
    except KeptLocal:
        # The door refused, and it says so in its own words with the status every other refusal
        # of this kind answers, rather than reaching the global handler as a 500.
        raise _kept_local() from None
    return LookUpResult(answers=[_answer(one) for one in answers])


@router.post("/stash-boxes/{box_id}/picture/keep", dependencies=[Depends(csrf_protect)])
async def keep_picture(
    box_id: str,
    body: KeepPicture,
    service: Annotated[StashBoxService, Depends(_service)],
    covers: Annotated[SubjectCovers, Depends(wiring.subject_covers)],
    access: Annotated[Repository, Depends(wiring.access)],
    key: Annotated[bytes | None, Depends(master_key)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Keep a stash-box's picture as the one this subject is shown with: their cover.

    ## Where the address comes from

    From the link, which was written a moment ago by the step before this one, not from the
    request. What the browser holds is Sift's own proxy address for that picture, which is on the
    wrong host for the adapter.

    The fetch itself goes out through the box's own route, with its key where it needs one, so a
    box behind a tunnel stays behind it.

    ## Why it is kept as a cover

    A cover is the one column every wall reads, and an uploaded cover is bytes that are not a
    library file, which is exactly what this is. This replaces, because the button says so; the
    unattended run goes through the filling verb instead. See `SubjectCovers`.
    """
    if not await _exists(service, box_id):
        raise _missing()
    kind = _subject(body.subject)
    # ONE scoped read. `name_of` resolves through the same read `_may_see` uses, so a subject this
    # viewer may not see has no name either, and no name is the same answer as not there.
    if await name_of(access, viewer, kind, body.local_id) is None:
        raise _missing()
    # A box's picture made the subject's cover is a stored answer APPLIED, and the fetch it needs
    # names nothing local, so the door lets it through by design (`picture` is exempt). Refused
    # here instead, like every other writer of what a box said. See `nothing_applied`.
    try:
        await service.nothing_applied(kind, body.local_id)
    except KeptLocal:
        raise _kept_local() from None

    # The address out of Sift's own copy of what this box said, never out of the request. See
    # `KeepPicture` for why: what the browser holds is Sift's proxy address, and handing that back
    # is an address on the wrong host, which the adapter refuses.
    linked = [one for one in await service.links_of(kind, body.local_id) if one.source_id == box_id]
    if not linked:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this box isn't linked to that")
    where = linked[0].record.image_url
    if not where:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "that entry has no picture")

    # A COVER fetch: the bytes go to the cover door, which draws a vector logo safely, so an SVG
    # may come here and never through the picture route below.
    got = await service.picture(box_id, where, key, vector=True)
    if got is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such picture")
    blob, _content_type = got
    # The user who pressed Keep picture, and the box the picture came from: the line reads
    # "Cover set to FansDB's picture" by whoever pressed it.
    if not await covers.keep(
        kind,
        body.local_id,
        blob,
        actor=Actor.user(viewer.id),
        box=Object(kind="box", id=box_id, name=linked[0].source_name),
    ):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "that isn't a picture Sift can keep")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
