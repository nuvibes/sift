# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pile of answers about files: paging it, applying answers, and refusing them."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.enrichment import Enricher, Plan, Strategy
from sift.kernel.ledger import Actor
from sift.kernel.paging import MAX_PAGE_SIZE, resume_at
from sift.kernel.records import (
    Subject,
)
from sift.kernel.seams import SettingsSeam
from sift.kernel.vocabulary import Subject as DecisionSubject
from sift.kernel.workbench import Recorder
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.stash_boxes.enrich import file_writer, linkable, record_who_invented
from sift.slices.stash_boxes.jobs import (
    STASH_ENRICH,
    ask_for_creator_pictures,
    strategies_for,
)
from sift.slices.stash_boxes.match_words import _answered, _confirmed, _did, _match_view
from sift.slices.stash_boxes.models import (
    Applied,
    ApplyMatches,
    MatchList,
    MatchRef,
)
from sift.slices.stash_boxes.router_base import (
    MATCH_PAGE,
    _kept_local,
    _service,
)
from sift.slices.stash_boxes.service import (
    KeptLocal,
    Match,
    StashBoxService,
)
from sift.slices.stash_boxes.settings import box_for

router = APIRouter(tags=["stash-boxes"])

#: The name the Tagger queue registers under, shared with the panel that draws it.
TAGGER = "tagger"


def _subjects_named(fields: Mapping[str, object]) -> set[tuple[Subject, str]]:
    """The people, the site and the tags one answer names, as (kind, name)."""
    named: set[tuple[Subject, str]] = set()
    for name in _listed(fields.get("people")):
        named.add((Subject.PERSON, name))
    for name in _listed(fields.get("tags")):
        named.add((Subject.TAG, name))
    site = fields.get("site")
    if isinstance(site, str) and site.strip():
        named.add((Subject.SITE, site.strip()))
    return named


def _listed(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(one).strip() for one in value if str(one).strip()]


@router.get("/stash-boxes/matches")
async def waiting_matches(
    request: Request,
    viewer: Annotated[Viewer, Depends(require_admin)],
    access: Annotated[Repository, Depends(wiring.access)],
    service: Annotated[StashBoxService, Depends(_service)],
    settings: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 24,
    offset: Annotated[int, Query(ge=0)] = 0,
    asset: Annotated[str | None, Query()] = None,
    state: Annotated[Literal["waiting", "answered"], Query()] = "waiting",
    start: Annotated[str | None, Query(alias="from", max_length=200)] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
) -> MatchList:
    """What the pass found and nobody has answered, surest first, or, with `state=answered`, what
    was already agreed to or refused, newest answer first.

    One route for both states, as the pile and one file's share of it are one route: the same rows,
    the same rule about who may see them, and a tab that shows the settled half is the same screen
    showing a different state, so an empty pile does not read as a feature nobody switched on.

    `asset` narrows it to ONE file, which is what the chooser on a file's own menu reads. It is a
    parameter rather than a route of its own for the reason the scan's scopes are: the pile and one
    file's share of it are the same question, with the same order and the same rule about who may
    see the row, and two routes would be two places to keep that rule.

    Every row is resolved against the user asking before it is drawn, and a file they may not be
    shown produces no row at all: a match is a statement about a file, and a screen must not draw
    one for a file it would refuse to open.

    Each row carries what applying it would do, so a bulk confirm shows its consequences.

    `from` names the match a page starts at, as `<file id>:<box id>`: the row the pile was left
    at, carried in the tab's address so the way back lands on the same page, in whichever state it
    was showing. Answering a match takes it off the waiting pile, so a `from` naming nothing is the
    ordinary case there, answered with the page it was on (`near`), or the top. Not read beside
    `asset`: one file's share of the pile is a chooser, not a page anybody comes back to.
    """
    enricher = wiring.part_of(request, wiring.ENRICHER)
    if start is not None and asset is None:
        asset_id, _, box_id = start.rpartition(":")
        at = (
            await service.match_position(asset_id, box_id, answered=state == "answered")
            if asset_id
            else None
        )
        offset = resume_at(at, near)
    strategies = await strategies_for(settings, Subject.ASSET)
    if state == "answered":
        found, total = await service.answered(limit=limit, offset=offset, asset_id=asset)
    else:
        found, total = await service.waiting(limit=limit, offset=offset, asset_id=asset)
    kept = [one for one in found if await access.get_asset(viewer, one.asset_id) is not None]
    # The stills, asked ONCE for the page rather than per row. Without the token every picture on
    # this pile would be addressed bare, which the server answers the careful way, so a screen of
    # twenty-four rows would re-ask about twenty-four thumbnails it already had, on every visit.
    art = await service.art_of([one.asset_id for one in kept], stamp=viewer.cache_stamp)
    rows = [
        await _match_view(one, enricher=enricher, strategies=strategies, art=art.get(one.asset_id))
        for one in kept
    ]
    return MatchList(
        matches=rows,
        total=total,
        answered=await service.applied_files() if asset is None else 0,
        offset=offset,
    )


@router.post("/stash-boxes/matches/apply", dependencies=[Depends(csrf_protect)])
async def apply_matches(
    request: Request,
    body: ApplyMatches,
    viewer: Annotated[Viewer, Depends(require_admin)],
    access: Annotated[Repository, Depends(wiring.access)],
    service: Annotated[StashBoxService, Depends(_service)],
    settings: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
) -> Applied:
    """Say yes to these matches, and write what each one says under the rules for each field.

    ONE decision covering a whole page, with ONE receipt, so a run can be undone. `create` is
    permission to invent the people, tags and Sites the answers name, one name at a time. Every
    subject a confirmed page named that has no link yet is then handed to the enrichment, in one
    queued job, so a row invented here gets its record and picture.
    """
    enricher = wiring.part_of(request, wiring.ENRICHER)
    recorder = wiring.part_of(request, wiring.RECORDER)
    strategies = await strategies_for(settings, Subject.ASSET)
    # The permission, once, as the shape every writer asks per name. Empty is a real answer and
    # means invent nothing: it is not the absence of one.
    tally = _Tally(allowed=frozenset((one.kind, one.name) for one in body.create))
    for ref in body.matches[:MATCH_PAGE]:
        await _apply_one(request, ref, body, viewer, access, service, enricher, strategies, tally)
    decision = await _record_decision(request, recorder, viewer, tally)
    # Resolved AFTER the writes, so a row invented a moment ago is found.
    unlinked = await _unlinked_subjects(request, service, tally.named, tally.known)
    if unlinked:
        queue = wiring.part_of(request, wiring.QUEUE)
        # Nobody named a box for this follow-up, so it asks what is set up.
        await queue.enqueue(
            STASH_ENRICH,
            {
                "subjects": unlinked,
                "box": await box_for(settings, ""),
            },
        )
    if tally.kept_local and not tally.files:
        # Every answer on the page was about something kept local: only the refusal to report.
        raise _kept_local()
    return Applied(
        files=tally.files,
        fields=tally.fields,
        created=len(tally.made),
        decision_id=decision,
        kept_local=tally.kept_local,
    )


@dataclass
class _Tally:
    """What one page of confirmations has done so far, for its receipt and its follow-up."""

    allowed: frozenset[tuple[str, str]]
    files: int = 0
    fields: int = 0
    # Kind AND name, so two rows of different kinds that share a word are counted as two.
    made: set[tuple[str, str]] = field(default_factory=set)
    settled: list[dict[str, str]] = field(default_factory=list)
    # Whose answers were applied, by name in the order first met, and every field they wrote.
    boxes: dict[str, str] = field(default_factory=dict)
    wrote: dict[str, int] = field(default_factory=dict)
    # Every subject the confirmed answers name, whether invented now or already here.
    named: set[tuple[Subject, str]] = field(default_factory=set)
    # The rows this page INVENTED that the inventing box gave its own id for: (kind, id) -> (box,
    # its id). A row that was already here is somebody's, so it is never linked by a credit.
    known: dict[tuple[Subject, str], tuple[str, str]] = field(default_factory=dict)
    # Answers held back because the file is kept local NOW.
    kept_local: int = 0


async def _apply_one(
    request: Request,
    ref: MatchRef,
    body: ApplyMatches,
    viewer: Viewer,
    access: Repository,
    service: StashBoxService,
    enricher: Enricher,
    strategies: dict[str, Strategy],
    tally: _Tally,
) -> None:
    """Apply one waiting answer, settle it, and count what it wrote into `tally`."""
    if await access.get_asset(viewer, ref.asset_id) is None:
        return
    held = await service.match(ref.asset_id, ref.box_id)
    if held is None or held.state != "waiting":
        return
    # Kept local since the answer came back: applying a stored answer is the enrichment refused.
    # Left waiting, not refused, so allowing enrichment again brings it back as a question.
    try:
        await service.nothing_applied(Subject.ASSET, ref.asset_id)
    except KeptLocal:
        tally.kept_local += 1
        return
    plan = await enricher.plan_for(
        subject=Subject.ASSET,
        local_id=ref.asset_id,
        source_id=ref.box_id,
        offered=held.record.fields,
        strategies=strategies,
    )
    if plan is None:
        return
    written = await _write_plan(request, enricher, plan, held, ref.box_id, tally)
    # Then the disagreements somebody answered by hand: a person overriding the plan, so THE USER
    # and not the box is the actor (`Enricher.write_one`).
    tally.fields += await _answered(
        enricher,
        plan,
        {
            one.key: one.take
            for one in body.settle
            if one.asset_id == ref.asset_id and one.box_id == ref.box_id
        },
        Actor.user(viewer.id),
    )
    await service.settle(ref.asset_id, ref.box_id, applied=True)
    # BY HAND, and `written` only: the fields a person chose between two answers for are their own
    # acts, not what the BOX filled in.
    await service.record_enrichment(
        Subject.ASSET,
        ref.asset_id,
        ref.box_id,
        automatic=False,
        applied=written,
        pressed_by=viewer.id,
    )
    # A creator's username that landed brings the box's picture of them, queued.
    await ask_for_creator_pictures(
        wiring.part_of(request, wiring.QUEUE),
        held.record,
        box_id=ref.box_id,
        asset_id=ref.asset_id,
        written=written,
    )
    tally.settled.append({"asset_id": ref.asset_id, "box_id": ref.box_id})
    tally.boxes.setdefault(ref.box_id, held.box_name)
    for field_key, many in written.items():
        tally.wrote[field_key] = tally.wrote.get(field_key, 0) + max(int(many), 1)
    tally.files += 1
    tally.fields += len(written)


async def _write_plan(
    request: Request,
    enricher: Enricher,
    plan: Plan,
    held: Match,
    box_id: str,
    tally: _Tally,
) -> Mapping[str, int]:
    """Write one answer's plan, inventing only the ticked names it needs, and note what it named."""
    # Counted from what this plan would ACTUALLY have to invent, narrowed to what was ticked.
    invented: set[tuple[str, str]] = set()
    if tally.allowed:
        invented = {
            (one.kind, one.name)
            for one in await enricher.missing_for(plan)
            if (one.kind, one.name) in tally.allowed
        }
        tally.made |= invented
    written = await enricher.apply(plan, creating=tally.allowed)
    # Which rows THIS box invented, and the box's own id for each, so the follow-up LINKS them by
    # it. First box wins where two on one page invented the same row.
    made_here = await record_who_invented(
        wiring.part_of(request, wiring.NAMING), invented=invented, box_id=box_id
    )
    for one in linkable(made_here, held.record, box_id):
        tally.known.setdefault((one.subject, one.local_id), (one.box_id, one.remote_id))
    tally.named |= _subjects_named(held.record.fields)
    return written


async def _record_decision(
    request: Request, recorder: Recorder, viewer: Viewer, tally: _Tally
) -> str:
    """The page's one receipt, filed against the files it wrote to; "" when nothing settled."""
    if not tally.settled:
        return ""
    async with wiring.part_of(request, wiring.DATABASE).write() as connection:
        return await recorder.record_on(
            connection,
            queue=TAGGER,
            user_id=viewer.id,
            title=_confirmed(len(tally.settled), list(tally.boxes.values()), tally.wrote),
            detail=_did(files=tally.files, fields=tally.fields, created=len(tally.made)),
            payload=json.dumps({"matches": tally.settled}),
            # The files this page wrote to, never the people it named inside them.
            subjects=[
                DecisionSubject(kind="asset", id=str(one["asset_id"])) for one in tally.settled
            ],
        )


async def _unlinked_subjects(
    request: Request,
    service: StashBoxService,
    named: set[tuple[Subject, str]],
    known: Mapping[tuple[Subject, str], tuple[str, str]] | None = None,
) -> list[dict[str, str]]:
    """The rows behind these names that no box is linked to, as the enrichment job takes them.

    A row in `known` carries the box and that box's id for it, and the job links it by that id
    instead of searching the name. See `jobs.enrich_entities`.
    """
    naming = wiring.part_of(request, wiring.NAMING)
    wanted: list[dict[str, str]] = []
    for subject, name in sorted(named, key=lambda one: (one[0].value, one[1])):
        if subject is Subject.PERSON:
            local_id = await naming.person_named(name, creating=False)
        elif subject is Subject.SITE:
            local_id = await naming.site_named(name, creating=False)
        else:
            local_id = await naming.tag_named(name, creating=False)
        if local_id is None or await service.links_of(subject, local_id):
            continue
        entry = {"subject": subject.value, "id": local_id, "name": name}
        by_id = (known or {}).get((subject, local_id))
        if by_id is not None:
            entry["box"], entry["remote_id"] = by_id
        wanted.append(entry)
    return wanted


@router.post("/stash-boxes/matches/refuse", dependencies=[Depends(csrf_protect)])
async def refuse_matches(
    request: Request,
    body: ApplyMatches,
    viewer: Annotated[Viewer, Depends(require_admin)],
    access: Annotated[Repository, Depends(wiring.access)],
    service: Annotated[StashBoxService, Depends(_service)],
) -> Applied:
    """No, to these. A waiting answer is never written and stops being asked about; an answer
    already applied is taken back whole: everything it wrote comes off the file, with one History
    line and an Undo (`StashBoxService.take_back`).

    A refusal is remembered rather than deleted, for the reason the folder suggestions give: a row
    that is gone comes straight back the next time the pass runs, and the same wrong answer is then
    offered for ever.
    """
    writer = file_writer(wiring.part_or_none(request, wiring.ENRICHER))
    refused = 0
    receipts: list[str] = []
    for ref in body.matches[:MATCH_PAGE]:
        if await access.get_asset(viewer, ref.asset_id) is None:
            continue
        if await service.settle(ref.asset_id, ref.box_id, applied=False):
            refused += 1
            continue
        taken = await service.take_back(
            ref.asset_id, ref.box_id, actor=Actor.user(viewer.id), reopen=False, writer=writer
        )
        if taken is not None:
            refused += 1
            # A take-back of one answer always writes its line, so it always has a receipt.
            if taken.receipt_id:  # pragma: no branch
                receipts.append(taken.receipt_id)
    # The take-back's own receipt where the press took exactly one back, so the screen can offer
    # its Undo where it was pressed. Several each keep theirs on the file's History.
    return Applied(files=refused, decision_id=receipts[0] if len(receipts) == 1 else "")
