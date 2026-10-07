# SPDX-License-Identifier: AGPL-3.0-or-later
"""One person's faces: the Disagreements tab, Identified for a person, starter pictures, and
how well Sift knows somebody."""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)

from sift.kernel import wiring
from sift.kernel.access import AssetFilter, Repository, Viewer, Where
from sift.kernel.access import sentences as say
from sift.kernel.access.history_line import link_of_piece
from sift.kernel.jobs import JobQueue
from sift.kernel.paging import MAX_PAGE_SIZE, resume_at
from sift.kernel.serving import face_version
from sift.kernel.wire import link_of
from sift.slices.auth import csrf_protect, current_viewer, require_admin
from sift.slices.faces.jobs import (
    ask_for_rematching,
)
from sift.slices.faces.models import (
    Attribution,
)
from sift.slices.faces.models_http import (
    PILES_PER_PAGE,
    AppearancePage,
    DisagreeingPeople,
    DisagreeingPersonView,
    DisagreementsWrite,
    FacesDecided,
    RecognitionStrength,
    ReferenceStrengths,
    RunScope,
    StarterPerson,
    StartersOffer,
    StartersQueued,
    ToCheckCard,
    ToCheckPage,
    WorkLeft,
)
from sift.slices.faces.router_common import (
    _card,
    _missing,
    _off,
    _service,
    log,
)
from sift.slices.faces.service import (
    FACE_STARTERS,
    FaceService,
)

router = APIRouter(tags=["faces"])


@router.get("/faces/disagreements")
async def disagreeing_people(
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> DisagreeingPeople:
    """Everybody the Disagreements tab is about, the most files first, with how many of each.

    The tab's rows gathered by person, which is the shape they arrive in: a pass files a whole
    folder in one go, so its mistakes come by the hundred under one name. The counts are of the same
    rows the tab counts, so they add up to its number. Unpaged: one row per PERSON, never per file,
    and the rows it gathers are bounded by the read beneath (`tuning.FILED_FACES_AT_MOST`).

    Admin, like every list this feature offers. See `to_check`.
    """
    if not await service.enabled():
        return DisagreeingPeople()
    people = await service.disagreeing_people(viewer)
    return DisagreeingPeople(
        people=[
            DisagreeingPersonView(
                person_id=one.person_id,
                person_name=one.name,
                count=one.count,
                source=one.source,
                filed=say.text_of(one.filed),
                filed_links=[link_of(link_of_piece(piece)) for piece in say.things_in(one.filed)],
            )
            for one in people
        ],
        total=sum(one.count for one in people),
    )


@router.get("/faces/disagreements/{person_id}")
async def disagreements_of_person(
    person_id: str,
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = PILES_PER_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ToCheckPage:
    """One page of one person's disagreements, the faces that look most like her first.

    The rows are the tab's own (`ToCheckCard`, kind `mismatch`), so a file drawn here and a file
    drawn on the tab are one thing. A person this viewer may not be told about has no rows, which
    is the same answer as somebody with none, so an address cannot be used to ask who exists.
    """
    if not await service.enabled():
        return ToCheckPage()
    items, total = await service.disagreements_of(viewer, person_id, limit=limit, offset=offset)
    art = face_version(viewer.cache_stamp)
    return ToCheckPage(
        items=[
            ToCheckCard(
                kind=item.kind,
                id=item.id,
                size=item.size,
                person_name=item.person_name,
                person_id=item.person_id,
                source=item.source,
                faces=[_card(sighting, art) for sighting in item.faces],
            )
            for item in items
        ],
        total=total,
        offset=max(0, offset),
    )


@router.post("/faces/disagreements/{person_id}", dependencies=[Depends(csrf_protect)])
async def answer_disagreements(
    person_id: str,
    body: DisagreementsWrite,
    service: Annotated[FaceService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FacesDecided:
    """Yes or No over one person's disagreements: a page of them, a pick, or all of hers.

    Yes names each face as her, which is what every other faces screen's Yes writes, and teaches
    Sift what she looks like, so a re-match is asked for. No takes her off the files and remembers
    it, so the folder that filed her does not put her back; its receipt (`decision_id`) puts every
    file back as it was, which is what the toast's Undo presses.
    """
    if not await service.enabled():
        raise _off()
    run = await service.answer_disagreements(
        viewer,
        person_id,
        yes=body.yes,
        asset_ids=None if body.scope is RunScope.ALL else body.asset_ids,
    )
    if body.yes and run.changed:
        await ask_for_rematching(queue)
    log.info("faces.disagreements.answered", yes=body.yes, scope=body.scope, changed=run.changed)
    return FacesDecided(
        changed=run.changed,
        person_id=person_id,
        person_name=await service.name_of(viewer, person_id),
        decision_id=run.decision_id or None,
    )


@router.get("/faces/identified/people/{person_id}")
async def identified_for_person(
    person_id: str,
    service: Annotated[FaceService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 60,
    offset: Annotated[int, Query(ge=0)] = 0,
    attribution: Annotated[Attribution | None, Query()] = None,
    start: Annotated[str | None, Query(alias="from")] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
) -> AppearancePage:
    """One person's decided faces: the card from the wall above, opened up, scoped afresh.

    `attribution` narrows to one way a face came to carry the name (proposed, agreed, matched),
    before the page is taken, each its own paged wall. `from` anchors the page as
    `face_groups` explains. The three counts come back whatever was narrowed to, so the tab row
    arrives whole (`identified_for`).
    """
    if not await service.enabled():
        return AppearancePage()
    if start is not None:
        at = await service.position_of_appearance(viewer, person_id, start, attribution=attribution)
        offset = resume_at(at, near)
    found = await service.identified_for(
        viewer, person_id, limit=limit, offset=offset, attribution=attribution
    )
    art = face_version(viewer.cache_stamp)
    return AppearancePage(
        items=[_card(s, art) for s in found.items],
        total=found.total,
        offset=offset,
        person_name=await service.name_of(viewer, person_id),
        waiting=found.waiting,
        matched=found.matched,
        confirmed=found.confirmed,
        # The Files wall's own total for the filter the line opens, so the two cannot disagree.
        unnamed_from_folder=await access.count_visible(
            viewer, AssetFilter(where=Where("unnamed_face", (person_id,)))
        ),
    )


@router.get("/people/{person_id}/recognition")
async def recognition_strength(
    person_id: str,
    service: Annotated[FaceService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> RecognitionStrength:
    """How reliably Sift can recognize this person, and what that rests on.

    Resolved as the People screen resolves her, so a hidden person is a 404. Zero rather than 409
    with the feature off: "cannot recognize them" is the true answer either way.
    """
    if await access.visible_person(viewer, person_id) is None:
        raise _missing()
    if not await service.enabled():
        return RecognitionStrength()
    strength = await service.recognition_of(person_id, viewer)
    return RecognitionStrength(
        references=strength.references,
        target=strength.target,
        floor=strength.floor,
        strong=strength.strong,
        fraction=strength.fraction,
        verdict=strength.verdict,
        starters=strength.starters,
        starters_retired=strength.starters_retired,
        starters_from=list(strength.starters_from),
    )


@router.get("/faces/starters")
async def starters_offer(
    request: Request,
    service: Annotated[FaceService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> StartersOffer:
    """How many People "Use stash-box pictures as starters" would act on, and who. The count comes
    FIRST.

    Linked to a stash-box and holding no reference of any kind. Read from this library's own
    tables; no box is asked anything to answer it. The door is built at every start-up, so its
    absence is a fault and `part_of` names it. The names are the People wall's for this viewer, in
    name order, so the pane can list them under the count.
    """
    if not await service.enabled():
        raise _off()
    door = wiring.part_of(request, wiring.BOX_PICTURES)
    wanted = await service.starters_count(door)
    shown = await access.visible_people(viewer, wanted)
    who = sorted(
        (StarterPerson(id=person_id, name=shown[person_id].name) for person_id in shown),
        key=lambda one: (one.name.casefold(), one.id),
    )
    return StartersOffer(people=len(wanted), who=who)


@router.post("/faces/starters", dependencies=[Depends(csrf_protect)])
async def use_starters(
    request: Request,
    service: Annotated[FaceService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> StartersQueued:
    """Queue the starter pictures for everybody the count names. Hands back the task doing it.

    The explicit press for the People already linked to a stash-box with no pictures; somebody
    linked from now on is given them at link time. The same work either way
    (`jobs.starters`): each person's box pictures fetched through the stash-box door, checked like
    an imported folder, and filed as starters that only ever make Sift ASK. 409 with the feature
    off, and when there is nobody to act on: a press that would do nothing is refused in words.
    """
    if not await service.enabled():
        raise _off()
    people = await service.starters_count(wiring.part_of(request, wiring.BOX_PICTURES))
    if not people:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Nobody linked to a stash-box is waiting for starter pictures.",
        )
    job_id = await queue.enqueue(FACE_STARTERS, {"people": people}, requested_by=viewer.id)
    log.info("faces.starter.requested", job_id=job_id, people=len(people))
    return StartersQueued(job_id=job_id, people=len(people))


@router.get("/faces/references/strength")
async def reference_strengths(
    service: Annotated[FaceService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> ReferenceStrengths:
    """Everybody's reference count in one answer, for a screen choosing between people.

    Attaching a face to somebody is the moment their reference count matters and the moment it was
    invisible: a name in a list looks the same whether Sift can recognize that person from fifty
    pictures or from four. Matching compares against every reference somebody has, so the count is
    what decides whether the next file finds them by itself.

    Admin-only and count-only. It names nobody and says nothing about which files anybody appears
    in, and it keys each count by a person's id, so it is held to the People wall like the list
    beside it (`known_people`): a person the vault is holding back is not in it while it is shut.
    Unfiltered, the id and the count of somebody hidden would sit in every answer.
    """
    if not await service.enabled():
        raise _off()
    found = await service.reference_strengths()
    shown = await access.visible_people(viewer, list(found.people))
    people = {person_id: one for person_id, one in found.people.items() if person_id in shown}
    return ReferenceStrengths(
        people={person_id: one.references for person_id, one in people.items()},
        verdicts={person_id: one.verdict for person_id, one in people.items()},
        target=found.target,
        floor=found.floor,
        strong=found.strong,
    )


@router.get("/faces/work-left")
async def work_left(
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> WorkLeft:
    """How much of a scan is still to come, in files and in the work they amount to.

    Two numbers because they answer two different questions and the second cannot be got from the
    first. Files left is what a bar counts down. What it COSTS is the moments those files add up
    to, and files are wildly unequal, so a rate in files per minute measured over the last
    stretch of a library says very little about the next one, and an estimate built on it lurches.

    Admin-only, like the scan it reports on. It counts and says nothing about which files.
    """
    if not await service.enabled():
        return WorkLeft(files=0, moments=0)
    files, moments = await service.work_left(viewer)
    return WorkLeft(files=files, moments=moments)
