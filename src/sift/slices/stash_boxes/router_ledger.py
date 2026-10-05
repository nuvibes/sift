# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ledger of links, the undecided names, the studio questions, and a box's pictures."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.access.sentences import said, text_of, thing, username_opens
from sift.kernel.paging import MAX_PAGE_SIZE, resume_at
from sift.kernel.records import (
    Subject,
)
from sift.kernel.wire import pieces_of
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, current_viewer, master_key, require_admin
from sift.slices.stash_boxes.models import (
    LinkedEntity,
    LinkedLedger,
    StudioAnswered,
    StudioQuestions,
    StudioQuestionView,
    UndecidedEntity,
    UndecidedList,
)
from sift.slices.stash_boxes.queue import name_of, names_of
from sift.slices.stash_boxes.router_base import (
    LEDGER_PAGE,
    _exists,
    _missing,
    _service,
    _subject,
)
from sift.slices.stash_boxes.service import (
    StashBoxService,
)
from sift.slices.stash_boxes.studios import CreatorStudios

router = APIRouter(tags=["stash-boxes"])


@router.get("/stash-boxes/linked")
async def linked_ledger(
    service: Annotated[StashBoxService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    subject: Annotated[str, Query(max_length=32)] = "",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = LEDGER_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
    start: Annotated[str | None, Query(alias="from", max_length=200)] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
) -> LinkedLedger:
    """A page of the ledger: every person, site and tag a stash-box has been agreed to know,
    newest first, with which box and when.

    Paged over the rows this user may be shown, so a page holds its full size, the total counts
    what the pages hold, and a position never says where a hidden row sits. Read from Sift's own
    tables and never from the network.

    `from` names the link a page starts at, as `<subject id>:<box id>`: the row the tab was left
    at, carried in its address so the way back lands on the same page. A link taken off since is
    answered with the page it was on (`near`), or the top. See `resume_at`.
    """
    kind = _subject(subject) if subject else None
    names = {box.id: box.name for box in await service.boxes()}
    keys = await service.ledger_keys(subject=kind)
    # Every row's subject in one scoped read per kind. See `names_of`.
    seen = await names_of(access, viewer, ((one.subject, one.local_id) for one in keys))
    shown = [one for one in keys if (one.subject, one.local_id) in seen]
    if start is not None:
        local_id, _, box_id = start.rpartition(":")
        at = next(
            (
                place
                for place, one in enumerate(shown)
                if (one.local_id, one.box_id) == (local_id, box_id)
            ),
            None,
        )
        offset = resume_at(at, near)
    rows: list[LinkedEntity] = []
    for one in await service.ledger_rows(shown[offset : offset + limit]):
        rows.append(
            LinkedEntity(
                subject=one.subject.value,
                id=one.local_id,
                name=one.name,
                box_id=one.box_id,
                box=names.get(one.box_id, one.box_id),
                known_as=one.record.name,
                fetched_at=one.fetched_at,
                # The line the entity's own History says for this link, built once, so the tab
                # and the record cannot come to two different accounts of one link.
                said=pieces_of(
                    await service.what_it_filled(one, names.get(one.box_id, one.box_id), viewer)
                ),
            )
        )
    files = len(await access.visible_of(viewer, await service.applied_file_ids()))
    return LinkedLedger(items=rows, files=files, total=len(shown), offset=offset)


@router.get("/stash-boxes/undecided")
async def undecided(
    service: Annotated[StashBoxService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = LEDGER_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
    start: Annotated[str | None, Query(alias="from", max_length=200)] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
) -> UndecidedList:
    """A page of the names the unattended enrichment could not choose for. See `UndecidedQueue`.

    `from` names the subject a page starts at: the row the tab was left at, carried in its address
    so the way back lands on the same page. Deciding a name takes it off this list, so a `from`
    naming nothing is the ordinary case and is answered with the page it was on (`near`), or the
    top. See `resume_at`. Paged over the names this user may be shown, as the ledger is.
    """
    keys = await service.undecided_keys()
    # One scoped read per kind rather than one per row, since each Site resolved alone costs the
    # whole Sites wall's counts. See `names_of`.
    seen = await names_of(access, viewer, keys)
    shown = [one for one in keys if one in seen]
    if start is not None:
        at = next((place for place, one in enumerate(shown) if one[1] == start), None)
        offset = resume_at(at, near)
    rows: list[UndecidedEntity] = []
    for one in await service.undecided_rows(shown[offset : offset + limit]):
        rows.append(
            UndecidedEntity(
                subject=one.subject.value,
                id=one.local_id,
                name=one.name,
                candidates=one.candidates,
                seen_at=one.seen_at,
            )
        )
    return UndecidedList(items=rows, total=len(shown), offset=offset)


def _studios(request: Request) -> CreatorStudios:
    return CreatorStudios(part_of(request, wiring.DATABASE))


def _not_a_question() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "There's no question about that Site.")


@router.get("/stash-boxes/creator-sites")
async def studio_questions(
    studios: Annotated[CreatorStudios, Depends(_studios)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = LEDGER_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> StudioQuestions:
    """A page of the Sites a stash-box made that may be one person's own store. See `StudioQueue`.

    Admin: either answer moves a Site's files for everybody. Paged over the Sites this admin may
    be shown, as every list on the stash-box page is.
    """
    asked = await studios.questions()
    seen = await names_of(access, viewer, ((Subject.SITE, one.site_id) for one in asked))
    shown = [one for one in asked if (Subject.SITE, one.site_id) in seen]
    return StudioQuestions(
        items=[
            StudioQuestionView(
                id=one.site_id,
                name=one.name,
                files=one.files,
                scenes=one.scenes,
                credited=one.credited,
                others=one.others,
                site=one.site,
                handle=one.handle,
                store=one.store,
            )
            for one in shown[offset : offset + limit]
        ],
        total=len(shown),
        offset=offset,
    )


@router.post("/stash-boxes/creator-sites/{site_id}/username", dependencies=[Depends(csrf_protect)])
async def studio_is_a_username(
    site_id: str,
    studios: Annotated[CreatorStudios, Depends(_studios)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> StudioAnswered:
    """Yes: this Site is one person's username. Its box filings move there, with an Undo."""
    if await name_of(access, viewer, Subject.SITE, site_id) is None:
        raise _not_a_question()
    turned = await studios.turn(site_id, viewer.id)
    if turned is None:
        raise _not_a_question()
    href = username_opens(turned.username_id, None)
    line = said(thing("username", turned.username_id, turned.site_name, href=href))
    line = said(line, f" is a username on {turned.host}, not a Site")
    return StudioAnswered(receipt_id=turned.receipt_id, said=text_of(line), pieces=pieces_of(line))


@router.post("/stash-boxes/creator-sites/{site_id}/site", dependencies=[Depends(csrf_protect)])
async def studio_is_a_site(
    site_id: str,
    studios: Annotated[CreatorStudios, Depends(_studios)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> StudioAnswered:
    """No: this Site is a Site. Remembered, so it is not asked about again; with an Undo."""
    name = await name_of(access, viewer, Subject.SITE, site_id)
    if name is None:
        raise _not_a_question()
    receipt_id = await studios.keep(site_id, viewer.id)
    if receipt_id is None:
        raise _not_a_question()
    line = said(thing("site", site_id, name), " stays a Site")
    return StudioAnswered(receipt_id=receipt_id, said=text_of(line), pieces=pieces_of(line))


@router.get("/stash-boxes/{box_id}/picture")
async def picture(
    box_id: str,
    service: Annotated[StashBoxService, Depends(_service)],
    key: Annotated[bytes | None, Depends(master_key)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    url: Annotated[str, Query(min_length=1, max_length=2000)],
) -> Response:
    """A picture from a stash-box, served from Sift's own address.

    Sift's pages run under `img-src 'self'`, which allows no remote host: that is what makes
    injected CSS harmless, because there is nowhere for it to send anything. Widening it for
    somebody else's domain would give that away, so the bytes come through here instead.

    The adapter refuses a URL that is not on the box's own host, checks the type and caps the size.
    A picture that will not come is a 404: a missing thumbnail is a missing thumbnail, not an error
    on a screen somebody is reading.
    """
    if not await _exists(service, box_id):
        raise _missing()
    got = await service.picture(box_id, url, key)
    if got is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such picture")
    body, kind = got
    # A week. The address carries the picture's own identifier, so a different picture is a
    # different address and nothing here can go stale into the wrong answer.
    return Response(
        content=body, media_type=kind, headers={"Cache-Control": "private, max-age=604800"}
    )
