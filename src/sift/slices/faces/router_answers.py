# SPDX-License-Identifier: AGPL-3.0-or-later
"""The answers given on Faces: naming, accepting, refusing, setting aside and moving faces, a
group or a run at a time."""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.jobs import JobQueue
from sift.kernel.paging import MAX_PAGE_SIZE, resume_at
from sift.kernel.reach import BulkWriteDone
from sift.kernel.serving import face_version
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.faces.jobs import (
    ask_for_rematching,
)
from sift.slices.faces.models_http import (
    FACES_PER_PAGE,
    FacesDecided,
    FacesRefused,
    FacesWrite,
    GroupDetail,
    GroupsWrite,
    MatchesAgreed,
    MovedFaces,
    MoveFacesWrite,
    NameFacesWrite,
    RunWrite,
)
from sift.slices.faces.router_common import (
    _group,
    _missing,
    _off,
    _only,
    _scope,
    _service,
    log,
)
from sift.slices.faces.service import (
    FaceService,
)

router = APIRouter(tags=["faces"])


@router.get("/faces/groups/{pile_id}")
async def face_group(
    pile_id: str,
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = FACES_PER_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
    start: Annotated[str | None, Query(alias="from")] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
) -> GroupDetail:
    """One pile, with every face in it rather than the handful a card previews, so part of a
    pile can be answered. A pile this user may see nothing of is a 404, not an empty page.

    `from` names a row to start the page at (a durable address, unlike a page number), resolved
    against this same list. One that resolves to nothing serves its page (`near`) or the TOP,
    whatever the reason, so a link cannot ask whether something is there.
    """
    if not await service.enabled():
        raise _off()
    if start is not None:
        at = await service.position_of_face(viewer, pile_id, start)
        offset = resume_at(at, near)
    found = await service.pile(viewer, pile_id, limit=limit, offset=offset)
    if found is None:
        raise _missing()
    view, total = found
    return GroupDetail(
        group=_group(view, face_version(viewer.cache_stamp)), total=total, offset=offset
    )


@router.post("/faces/name", dependencies=[Depends(csrf_protect)])
async def name_faces(
    body: NameFacesWrite,
    service: Annotated[FaceService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FacesDecided:
    """Say who some faces are: somebody who already exists, or somebody new.

    The name is resolved against existing people before anything is created, so pressing twice
    makes one person. A face out of reach is skipped and counted, and the reply says how many and
    why.
    """
    if not await service.enabled():
        raise _off()
    if (body.person_id is None) == (body.name is None):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "name faces as somebody who exists, or under a new name"
        )
    if not body.track_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no faces were named")
    actionable = await service.touchable_faces(viewer, body.track_ids)

    person_id = body.person_id
    if person_id is None:
        person_id = await service.find_or_create_person(str(body.name), by=viewer.id)
        if person_id is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "a new person needs a name")
    elif not await service.may_see_person(viewer, person_id):
        raise _missing()

    named = list(actionable.allowed)
    changed, offered, receipt = 0, 0, None
    if named:
        if body.whole_group:
            # With the receipt a group's Yes writes, so it is taken back the same way.
            answered = await service.name_groups(viewer, named, person_id)
            changed, offered = answered.changed, answered.offered
            receipt = answered.decision_id or None
        else:
            changed, offered = await service.confirm_many(named, person_id, viewer=viewer), 0
    if changed:
        await ask_for_rematching(queue)
    log.info("faces.named", changed=changed, offered=offered, skipped=actionable.skipped)
    # SPREAD, never a field list, so a field added to the reply cannot be left out.
    return FacesDecided(
        **BulkWriteDone.after(actionable, changed).model_dump(),
        person_id=person_id,
        person_name=await service.name_of(viewer, person_id),
        offered=offered,
        decision_id=receipt,
    )


@router.post("/faces/accept", dependencies=[Depends(csrf_protect)])
async def accept_faces(
    body: FacesWrite,
    service: Annotated[FaceService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FacesDecided:
    """Agree with what Sift proposed for these faces, each as the person already proposed for it.

    A face out of reach is skipped and counted. Each agreement adds a reference, so a re-match
    is asked for afterwards, which settles the suggestions like it.
    """
    if not await service.enabled():
        raise _off()
    if not body.track_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no faces were agreed to")
    actionable = await service.touchable_faces(viewer, body.track_ids)

    changed = (
        await service.accept_suggestions(list(actionable.allowed)) if actionable.allowed else 0
    )
    if changed:
        await ask_for_rematching(queue)
    log.info("faces.accepted", changed=changed, skipped=actionable.skipped)
    # `FacesDecided` rather than its base: narrowing it would change the published schema.
    return FacesDecided(**BulkWriteDone.after(actionable, changed).model_dump())


@router.post("/faces/look-alikes/{person_id}/confirm", dependencies=[Depends(csrf_protect)])
async def confirm_look_alikes(
    person_id: str,
    service: Annotated[FaceService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    body: RunWrite | None = None,
) -> FacesDecided:
    """Agree with the proposals standing for one person: every one, or the ones a press names.

    **Which faces, by `scope`** (`RunWrite`): none or `all` is every one standing on the tab, read
    by the server; `page` and `picked` are narrowed against that set.

    Both halves of naming by hand: each face is agreed to, and the rest of any group it waited in
    is offered as her. A re-match is asked for afterwards, since each agreement adds a reference.
    A person this user may not be told about answers "nothing changed", never 404.
    """
    if not await service.enabled():
        raise _off()
    done = await service.confirm_look_alikes(viewer, person_id, only=_only(body))
    if done.changed:
        await ask_for_rematching(queue)
    log.info(
        "faces.look_alikes_confirmed",
        person_id=person_id,
        named=done.changed,
        offered=done.offered,
        scope=_scope(body),
    )
    return FacesDecided(
        changed=done.changed,
        person_id=person_id,
        offered=done.offered,
        decision_id=done.decision_id or None,
    )


@router.post("/faces/look-alikes/{person_id}/reject", dependencies=[Depends(csrf_protect)])
async def reject_look_alikes(
    person_id: str,
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    body: RunWrite | None = None,
) -> FacesRefused:
    """Refuse the proposals standing for one person, in one press.

    **Which faces, by `scope`** (`RunWrite`): none or `all` is every one standing on the tab, read
    by the server; `page` and `picked` are narrowed against that set.

    No re-match afterwards: a refusal makes the gallery smaller, not different.
    A person this user may not be told about answers "nothing changed", never 404.
    """
    if not await service.enabled():
        raise _off()
    done = await service.reject_look_alikes(viewer, person_id, only=_only(body))
    log.info(
        "faces.look_alikes_rejected",
        person_id=person_id,
        refused=done.changed,
        scope=_scope(body),
    )
    # `changed` alone: nothing was SELECTED, so there is nothing for a skip to be counted against.
    return FacesRefused(changed=done.changed, decision_id=done.decision_id or None)


@router.post("/faces/may-be/{person_id}/confirm", dependencies=[Depends(csrf_protect)])
async def confirm_groups(
    person_id: str,
    body: GroupsWrite,
    service: Annotated[FaceService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FacesDecided:
    """Yes on a "these groups may be her" card: the faces shown are confirmed, the rest offered.

    `pile_ids` are the groups left ticked and `track_ids` the faces the card showed; both are
    narrowed to what the card offers NOW (`FaceService.confirm_groups`). A Yes naming no faces is
    refused. A re-match is asked for afterwards.
    A person this user may not be told about answers "nothing changed", never 404.
    """
    if not await service.enabled():
        raise _off()
    if not body.track_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "a yes names the faces it was shown")
    done = await service.confirm_groups(viewer, person_id, body.pile_ids, body.track_ids)
    if done.changed:
        await ask_for_rematching(queue)
    log.info(
        "faces.may_be_confirmed",
        person_id=person_id,
        groups=len(body.pile_ids),
        named=done.changed,
        offered=done.offered,
    )
    return FacesDecided(
        changed=done.changed,
        person_id=person_id,
        offered=done.offered,
        decision_id=done.decision_id or None,
    )


@router.post("/faces/may-be/{person_id}/reject", dependencies=[Depends(csrf_protect)])
async def refuse_groups(
    person_id: str,
    body: GroupsWrite,
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FacesRefused:
    """No on a "these groups may be her" card: every face of those groups is refused as her, per
    face, so a regrouping cannot bring the question back. No re-match is asked for.
    A person this user may not be told about answers "nothing changed", never 404.
    """
    if not await service.enabled():
        raise _off()
    done = await service.refuse_groups(viewer, person_id, body.pile_ids)
    log.info(
        "faces.may_be_refused",
        person_id=person_id,
        groups=len(body.pile_ids),
        refused=done.changed,
    )
    return FacesRefused(changed=done.changed, decision_id=done.decision_id or None)


@router.post("/faces/people/{person_id}/confirm-matches", dependencies=[Depends(csrf_protect)])
async def confirm_matches(
    person_id: str,
    service: Annotated[FaceService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    body: RunWrite | None = None,
) -> MatchesAgreed:
    """Agree with the matches Sift made for one person, whatever it was sure of.

    Under People: these are her own matches, what her page shows, unlike the look-alike card.
    **Which faces, by `scope`** (`RunWrite`): none or `all` is every one standing on the tab, read
    by the server; `page` and `picked` are narrowed against that set.

    Every agreement adds a reference (and can lower her bar, `tuning.bar_for`), so a re-match is
    asked for afterwards.
    A person this user may not be told about answers "nothing changed", never 404.
    """
    if not await service.enabled():
        raise _off()
    confirmed, references = await service.confirm_matches(viewer, person_id, only=_only(body))
    if confirmed:
        await ask_for_rematching(queue)
    log.info(
        "faces.matches_confirmed",
        person_id=person_id,
        confirmed=confirmed,
        references=references,
        scope=_scope(body),
    )
    return MatchesAgreed(confirmed=confirmed, references=references)


@router.post("/faces/people/{person_id}/reject-matches", dependencies=[Depends(csrf_protect)])
async def reject_matches(
    person_id: str,
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    body: RunWrite | None = None,
) -> FacesRefused:
    """Say that the matches Sift made for one person are not them, in one press: the other half
    of `confirm-matches`, at the same address.

    **Which faces, by `scope`** (`RunWrite`): none or `all` is every one standing on the tab, read
    by the server; `page` and `picked` are narrowed against that set.

    No re-match afterwards: a refusal teaches Sift nothing new to match against.
    A person this user may not be told about answers "nothing changed", never 404.
    """
    if not await service.enabled():
        raise _off()
    done = await service.reject_matches(viewer, person_id, only=_only(body))
    log.info(
        "faces.matches_rejected",
        person_id=person_id,
        refused=done.changed,
        scope=_scope(body),
    )
    # `changed` alone, as the two bulk doors above answer.
    return FacesRefused(changed=done.changed, decision_id=done.decision_id or None)


@router.post("/faces/set-aside", dependencies=[Depends(csrf_protect)])
async def set_aside_faces(
    body: FacesWrite,
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FacesDecided:
    """Set some faces aside as a pile of their own under Discarded: listed, reversible, out of
    every regrouping. Nothing is deleted and no file is touched."""
    if not await service.enabled():
        raise _off()
    if not body.track_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no faces were named")
    actionable = await service.touchable_faces(viewer, body.track_ids)
    if not actionable.allowed:
        # Nothing to do is not an error: a selection wholly inside a locked vault keeps the
        # sentence that explains it.
        return FacesDecided(**BulkWriteDone.after(actionable, 0).model_dump())
    moved = await service.set_aside(list(actionable.allowed))
    if moved is None:
        raise _missing()
    log.info("faces.set_aside", faces=len(actionable.allowed), skipped=actionable.skipped)
    return FacesDecided(**BulkWriteDone.after(actionable, len(actionable.allowed)).model_dump())


@router.post("/faces/move", dependencies=[Depends(csrf_protect)])
async def move_faces(
    body: MoveFacesWrite,
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> MovedFaces:
    """Merge some faces into another group, or split them into a group of their own.

    A pile id merges them into it; none makes a pile of exactly them. The result is marked as
    built by hand, which keeps it through regrouping and rescans. Admin-only.
    """
    if not await service.enabled():
        raise _off()
    if not body.track_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no faces were named")
    actionable = await service.touchable_faces(viewer, body.track_ids)
    if not actionable.allowed:
        # Answered rather than refused, as `set_aside` above; `pile_id` stays empty.
        return MovedFaces(**BulkWriteDone.after(actionable, 0).model_dump())
    moved = await service.move_faces(list(actionable.allowed), body.pile_id)
    if moved is None:
        raise _missing()
    log.info(
        "faces.move",
        faces=len(actionable.allowed),
        into_new=body.pile_id is None,
        skipped=actionable.skipped,
    )
    return MovedFaces(
        pile_id=moved, **BulkWriteDone.after(actionable, len(actionable.allowed)).model_dump()
    )


@router.post("/faces/remove", dependencies=[Depends(csrf_protect)])
async def remove_faces(
    body: FacesWrite,
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FacesDecided:
    """Take some faces away for good, and stop them coming back.

    For a detection that was never a face or a worthless crop. The description and its quality
    measurements are kept, so the next scan drops the same thing silently. No undo, so it is an
    admin's. No file is touched.
    """
    if not await service.enabled():
        raise _off()
    if not body.track_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no faces were named")
    actionable = await service.touchable_faces(viewer, body.track_ids)
    if not actionable.allowed:
        return FacesDecided(**BulkWriteDone.after(actionable, 0).model_dump())
    removed = await service.remove_faces(list(actionable.allowed))
    log.info("faces.removed", faces=removed, skipped=actionable.skipped)
    return FacesDecided(**BulkWriteDone.after(actionable, removed).model_dump())
