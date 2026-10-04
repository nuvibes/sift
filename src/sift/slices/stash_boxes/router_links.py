# SPDX-License-Identifier: AGPL-3.0-or-later
"""A subject's links: reading, searching, linking, refreshing, taking fields, keeping local."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.access.catalog import refusers_of_file
from sift.kernel.db import Database
from sift.kernel.enrichment import (
    Writer,
)
from sift.kernel.ledger import Actor
from sift.kernel.records import (
    Subject,
    fields_of,
)
from sift.kernel.wire import made_by_wire
from sift.slices.auth import csrf_protect, current_viewer, master_key, require_admin
from sift.slices.stash_boxes.adapter import StashBoxUnreachable
from sift.slices.stash_boxes.asking import kept_local_by
from sift.slices.stash_boxes.models import (
    EnrichmentRunView,
    EnrichmentState,
    KeepLocal,
    LinkList,
    LinkView,
    LinkWrite,
    LookUpResult,
    RecordHeld,
    TakeFields,
    Taken,
)
from sift.slices.stash_boxes.queue import name_of
from sift.slices.stash_boxes.router_base import (
    FOLDER,
    _answer,
    _exists,
    _kept_local,
    _kept_local_kind,
    _may_see,
    _missing,
    _needs_a_key,
    _record,
    _service,
    _subject,
    folder_enrichment_state,
    folder_name,
    set_folder_kept_local,
)
from sift.slices.stash_boxes.service import (
    KEPT_LOCAL_INHERITED_WHY,
    KEPT_LOCAL_WHY,
    BoxView,
    KeptLocal,
    Linked,
    StashBoxService,
    entry_page,
)

router = APIRouter(tags=["stash-boxes"])


async def _name_for(
    access: Repository, viewer: Viewer, subject: Subject, local_id: str
) -> str | None:
    """What this thing is called, for the record. None when it has no name to give.

    `name_of` answers for the three entities and this adds the file, which it has no word for: a
    file's name is the file page's, its title where somebody typed one, then its name on disk now,
    then the name it arrived under.
    None is a real answer here and not a refusal: a file with neither has no name, and the
    caller has already established that the viewer may see it.
    """
    if subject is not Subject.ASSET:
        return await name_of(access, viewer, subject, local_id)
    asset = await access.open_asset(viewer, local_id)
    if asset is None:
        return None  # pragma: no cover (the file went between `_may_see`'s read and this one)
    on_disk = (await access.names_on_disk(viewer, [local_id])).get(local_id)
    return asset.title or on_disk or asset.original_filename


def _link(
    held: Linked, kind: Subject, boxes: Sequence[BoxView], gave: Sequence[str] = ()
) -> LinkView:
    """One kept record, with the word its box is known by and the box's own page for it.

    The boxes come in rather than being looked up here: the list is read per subject and the boxes
    are read once for the whole list, which is the same economy `links_of` already makes for names.
    A link whose box is not among them has no word and no page, and is drawn by its id alone.
    """
    box = next((one for one in boxes if one.id == held.source_id), None)
    return LinkView(
        box_id=held.source_id,
        box_name=held.source_name,
        box_slug=None if box is None else box.slug,
        remote_id=held.remote_id,
        page_url=None if box is None else entry_page(box, kind, held.remote_id),
        fetched_at=held.fetched_at,
        record=_record(held.record, held.source_name),
        gave=list(gave),
    )


def _still_given(given: Mapping[str, str], held: Mapping[str, object], link: Linked) -> list[str]:
    """The fields this link's box gave whose value is still the one it gave, in the box's order.

    The runs say which box WROTE a field last; the value decides whether that is still where it
    came from, since somebody may have typed over it since. A list is never one box's: it is merged
    from every box that answered (`enrichment.keep_both`), so only a single value is said.
    """
    theirs = link.record.fields
    return [
        key
        for key, box_id in given.items()
        if box_id == link.source_id
        and key in theirs
        and not isinstance(theirs[key], list | tuple)
        and _same_value(held.get(key), theirs[key])
    ]


def _same_value(mine: object, theirs: object) -> bool:
    """Whether a held value is the one a box gave: a number by its value, anything else by its
    words without case or the space around them, the way a record compares what it is offered."""
    if mine is None or isinstance(mine, list | tuple):
        return False
    numbers = (int, float)
    if isinstance(mine, numbers) and isinstance(theirs, numbers):
        return float(mine) == float(theirs)
    return str(mine).strip().casefold() == str(theirs).strip().casefold()


@router.get("/stash-boxes/links/{subject}/{local_id}")
async def links_of(
    request: Request,
    subject: str,
    local_id: str,
    service: Annotated[StashBoxService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> LinkList:
    """What has already been agreed about this subject, read from Sift's own table.

    The one route in this file that is not admin-only, and the one that reaches no network: a
    record is shown to everybody looking at the page, while asking a service anything spends a
    stored key and stays admin-only.
    """
    kind = _subject(subject)
    if not await _may_see(access, viewer, kind, local_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such subject")
    boxes = await service.boxes()
    made = await service.made_by(kind, local_id, viewer)
    links = await service.links_of(kind, local_id)
    # Which box each field came from, for the record's hover and the band: the runs say who wrote
    # it, and what the record holds now says whether that is still so.
    # `part_or_none`: a process that never wired the record writers still draws the links.
    enricher = wiring.part_or_none(request, wiring.ENRICHER)
    writer = enricher.writers.get(kind) if enricher is not None else None
    given = await service.fields_given(kind, local_id) if links and writer else {}
    held = await writer.current(local_id) if given and writer else {}
    return LinkList(
        links=[_link(one, kind, boxes, _still_given(given, held, one)) for one in links],
        made_by=None if made is None else made_by_wire(made),
    )


@router.get("/stash-boxes/search/{subject}")
async def search_boxes(
    subject: str,
    service: Annotated[StashBoxService, Depends(_service)],
    key: Annotated[bytes | None, Depends(master_key)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    term: Annotated[str, Query(min_length=1, max_length=200)],
    about: Annotated[str | None, Query(max_length=64)] = None,
    box: Annotated[str | None, Query(max_length=40)] = None,
) -> LookUpResult:
    """Ask every switched-on box about a name, for the chooser somebody picks an entry out of.

    Nothing is written. This is the question, and the answer is a list of candidates with a picture
    and a count beside each so that a person can tell two people of the same name apart.

    `about` is the row the chooser was opened ON, where it was opened on one. It is what makes a
    record kept local refuse before its name goes anywhere, and it is optional because the chooser
    can also be typed into: a term somebody typed is not a fact about any row in this library.
    Every way the chooser opens from a record's own page passes it.

    `box` is the flyout's answer: ask this one, or every switched-on one. Absent is every one.
    """
    try:
        answers = await service.search(
            term, key, subject=_subject(subject), about=about, only=box or None
        )
    except KeptLocal:
        # Caught here like every outbound route, so opening the chooser on something kept local
        # answers with the refusal rather than a 500.
        raise _kept_local() from None
    return LookUpResult(answers=[_answer(one) for one in answers])


@router.put(
    "/stash-boxes/links/{subject}/{local_id}/{box_id}", dependencies=[Depends(csrf_protect)]
)
async def link_subject(
    request: Request,
    subject: str,
    local_id: str,
    box_id: str,
    body: LinkWrite,
    service: Annotated[StashBoxService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    key: Annotated[bytes | None, Depends(master_key)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> LinkView:
    """Agree that this box's entry is this subject, and keep what it says about them.

    A PUT rather than a POST because it is the same statement however many times it is made: this
    subject, in this box, is that entry. Saying it twice re-fetches and replaces what was kept,
    which is what somebody pressing it again means.
    """
    kind = _subject(subject)
    if not await _exists(service, box_id) or not await _may_see(access, viewer, kind, local_id):
        raise _missing()
    try:
        held = await service.link(kind, local_id, box_id, body.remote_id, _needs_a_key(key))
    except KeptLocal:
        raise _kept_local() from None
    except StashBoxUnreachable as failure:
        # The box's own sentence ("Sift could not reach PMVStash.", "PMVStash answered 500."),
        # which the adapter writes for exactly this and which never carries a key or an address.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(failure)) from None
    if held is None:
        # Two causes, two answers, because their next moves are opposite: try again later, or pick
        # another entry. This one is the box answering: it was asked and holds no entry by that id.
        # 404, because the thing asked for is not there.
        box_name = next((one.name for one in await service.boxes() if one.id == box_id), None)
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"{box_name or 'That stash-box'} was asked and has no entry by that id. It may have "
            "been merged or removed there \u2014 search again and pick the one it holds now.",
        )
    # BY HAND. Somebody pressed this, which is the half of the fact nothing else in the database
    # records: an agreement made here and one Auto-enrich made behind their back leave identical
    # link rows.
    await service.record_enrichment(kind, local_id, box_id, automatic=False, pressed_by=viewer.id)
    # A person linked by hand is linked like one Auto-enrich linked: the face feature hears of it
    # and may take the box's pictures as starters (`EntityEnricher.person_linked`). Read as optional,
    # because an app built without face recognition simply has nobody listening.
    recognition = wiring.part_or_none(request, wiring.RECOGNITION)
    if kind is Subject.PERSON and recognition is not None:
        await recognition.linked(local_id)
    return _link(held, kind, await service.boxes())


@router.post(
    "/stash-boxes/links/{subject}/{local_id}/{box_id}/refresh",
    dependencies=[Depends(csrf_protect)],
)
async def refresh_link(
    subject: str,
    local_id: str,
    box_id: str,
    service: Annotated[StashBoxService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    key: Annotated[bytes | None, Depends(master_key)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> LinkView:
    """Ask a box again about something it is already linked to. By hand, never on a timer."""
    kind = _subject(subject)
    if not await _may_see(access, viewer, kind, local_id):
        raise _missing()
    try:
        held = await service.refresh(kind, local_id, box_id, _needs_a_key(key))
    except KeptLocal:
        raise _kept_local() from None
    except StashBoxUnreachable as failure:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(failure)) from None
    if held is None:
        raise _missing()
    await service.record_enrichment(kind, local_id, box_id, automatic=False, pressed_by=viewer.id)
    return _link(held, kind, await service.boxes())


def _writer_for(request: Request, subject: Subject) -> Writer:
    """The one writer of this kind of subject, or a 404 where this build has none."""
    writer = wiring.part_of(request, wiring.ENRICHER).writers.get(subject)
    if writer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "nothing of that kind can be linked")
    return writer


@router.get("/stash-boxes/record/{subject}/{local_id}")
async def record_held(
    subject: str,
    local_id: str,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> RecordHeld:
    """What Sift holds for one subject, for the chooser opened AWAY from its record's page.

    The queue of names nobody could choose for opens the chooser in place, without leaving the page.
    The chooser draws what Sift has beside what the box offers; here that comes from the subject's
    writer, which is where every plan reads it, rather than a second assembly of the record out of
    several routes. Admin-only, like everything the chooser does besides reading what is linked.
    """
    kind = _subject(subject)
    if not await _may_see(access, viewer, kind, local_id):
        raise _missing()
    return RecordHeld(values=dict(await _writer_for(request, kind).current(local_id)))


@router.post(
    "/stash-boxes/links/{subject}/{local_id}/{box_id}/take",
    dependencies=[Depends(csrf_protect)],
)
async def take_fields(
    subject: str,
    local_id: str,
    box_id: str,
    body: TakeFields,
    request: Request,
    service: Annotated[StashBoxService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Taken:
    """Take these of a LINKED box's fields onto the record, through the subject's one writer."""
    kind = _subject(subject)
    if not await _exists(service, box_id) or not await _may_see(access, viewer, kind, local_id):
        raise _missing()
    kept = next(
        (one for one in await service.links_of(kind, local_id) if one.source_id == box_id), None
    )
    if kept is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That stash-box isn't linked to this yet.")
    # The kept answer is a STORED one, so nothing is sent, and it is still the enrichment
    # somebody said they did not want. See `StashBoxService.nothing_applied`.
    try:
        await service.nothing_applied(kind, local_id)
    except KeptLocal:
        raise _kept_local() from None
    takeable = {one.key for one in fields_of(kind) if one.editable}
    offered = kept.record.fields
    values = {
        key: offered[key] for key in dict.fromkeys(body.keys) if key in takeable and key in offered
    }
    written: Mapping[str, int] = {}
    if values:
        written = await _writer_for(request, kind).write(
            local_id, values, creating=False, actor=Actor.user(viewer.id)
        )
    await service.record_enrichment(
        kind, local_id, box_id, automatic=False, applied=written, pressed_by=viewer.id
    )
    return Taken(fields=len(written))


@router.delete(
    "/stash-boxes/links/{subject}/{local_id}/{box_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def forget_link(
    subject: str,
    local_id: str,
    box_id: str,
    service: Annotated[StashBoxService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Forget that a box knows this subject.

    What was agreed to stays. A field somebody accepted is Sift's own from that moment, and undoing
    a month-old agreement would mean remembering which of today's values came from where.
    """
    kind = _subject(subject)
    if not await _may_see(access, viewer, kind, local_id):
        raise _missing()
    if not await service.forget_link(kind, local_id, box_id):
        raise _missing()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/stash-boxes/enrichment/{subject}/{local_id}")
async def enrichment_state(
    subject: str,
    local_id: str,
    service: Annotated[StashBoxService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> EnrichmentState:
    """Whether this may be sent outside, and when it last was. Reaches no network.

    Not admin-only, for the same reason the links beside it are not: both are read from Sift's own
    tables and both are part of what a thing IS. What a guest cannot do is CHANGE either.

    One answer rather than two calls. A menu opening on a file asks both questions at the same
    instant (may I offer Enrich, and what does the line under it say), and two routes would be
    two waits for one row's worth of facts.
    """
    if subject == FOLDER:
        if await folder_name(access, viewer, local_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such subject")
        return await folder_enrichment_state(database, local_id)
    kind = _kept_local_kind(subject)
    if not await _may_see(access, viewer, kind, local_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such subject")
    return await _enrichment_state(
        service,
        kind,
        local_id,
        inherited=await _kept_local_why(access, database, viewer, kind, local_id),
    )


async def _kept_local_why(
    access: Repository, database: Database, viewer: Viewer, kind: Subject, local_id: str
) -> str:
    """Why a FILE is kept local when its own switch is not the reason: what it is filed under that
    keeps it, by name, as far as this viewer may see it (`asking.kept_local_by`)."""
    if kind is not Subject.ASSET:
        return KEPT_LOCAL_INHERITED_WHY
    named: list[tuple[str, str]] = []
    for one in await refusers_of_file(database, local_id):
        if not one.kept_local:
            continue
        name = (
            await folder_name(access, viewer, one.id)
            if one.kind == FOLDER
            else await name_of(access, viewer, Subject(one.kind), one.id)
        )
        if name:
            named.append((one.kind, name))
    return kept_local_by(named)


async def _enrichment_state(
    service: StashBoxService,
    kind: Subject,
    local_id: str,
    kept: bool | None = None,
    inherited: str = KEPT_LOCAL_INHERITED_WHY,
) -> EnrichmentState:
    """Where one thing stands with enrichment, in the shape both routes answer with.

    `kept` is what a write has just SET it to, where a write has just set it. Passed rather than
    re-read so the reply describes the statement that was made rather than the row as it stands a
    moment later, which is the only way the two can disagree and the only way that matters.
    `inherited` is the reason where something it is filed under keeps it (`_kept_local_why`).
    """
    own = await service.kept_local_here(kind, local_id) if kept is None else kept
    # THE WHOLE RULE, which for a file takes in everything it is filed under. Asked even where a
    # write has just set the row's own switch: turning a file's own switch OFF does not let it out
    # if the Site it is filed under is the thing keeping it in, and a reply that said otherwise
    # would be read straight into a menu row that offers to send it.
    refused = own or await service.kept_local(kind, local_id)
    return EnrichmentState(
        subject=kind.value,
        id=local_id,
        kept_local=own,
        refused=refused,
        why=("" if not refused else KEPT_LOCAL_WHY if own else inherited),
        runs=[
            EnrichmentRunView(
                box_id=one.box_id,
                box_name=one.box_name,
                box_slug=one.box_slug,
                at=one.at,
                automatic=one.automatic,
            )
            for one in await service.enrichment_of(kind, local_id)
        ],
    )


@router.put(
    "/stash-boxes/enrichment/{subject}/{local_id}/keep-local",
    dependencies=[Depends(csrf_protect)],
)
async def keep_local(
    subject: str,
    local_id: str,
    body: KeepLocal,
    request: Request,
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> EnrichmentState:
    """Keep this file or record local, or let it be enriched again.

    An admin's, like every other write here that changes what leaves the machine. It is not a
    privacy setting for one user: the decision is recorded on the row, and the door reads it
    on behalf of every pass and every press there is.

    A PUT because it is the same statement however many times it is made. It says nothing about what
    has ALREADY been sent, and the menu says so too: a fingerprint that went to a public service
    last month cannot be recalled, and this is a decision about everything from now on.
    """
    if subject == FOLDER:
        named = await folder_name(access, viewer, local_id)
        if named is None or not await set_folder_kept_local(
            database, viewer, local_id, body.kept_local, name=named
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such subject")
        return await folder_enrichment_state(database, local_id, body.kept_local)
    kind = _kept_local_kind(subject)
    if not await _may_see(access, viewer, kind, local_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such subject")
    service = _service(request)
    # Read while the scoped read that just allowed this is still the truth of the row, and handed
    # down rather than looked up again inside the service: the record keeps what a thing was
    # CALLED when the decision was taken, and a name read afterwards is the name it has now.
    if not await service.set_kept_local(
        viewer,
        kind,
        local_id,
        body.kept_local,
        name=await _name_for(access, viewer, kind, local_id),
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such subject")
    return await _enrichment_state(
        service,
        kind,
        local_id,
        body.kept_local,
        await _kept_local_why(access, database, viewer, kind, local_id),
    )
