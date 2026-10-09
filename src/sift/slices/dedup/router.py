# SPDX-License-Identifier: AGPL-3.0-or-later
"""The maintenance endpoints, all admin-only: they describe the whole library, so a guest is
simply refused."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.log import get_logger
from sift.kernel.paging import MAX_PAGE_SIZE, resume_at
from sift.kernel.seams import SettingsSeam
from sift.kernel.settings_registry import get_registered
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.dedup.grouping import Group
from sift.slices.dedup.models import (
    CarryResult,
    CarryTotals,
    ChoiceView,
    CopyView,
    FileView,
    GroupList,
    GroupSettleRequest,
    GroupSettleResult,
    GroupView,
    ReclaimView,
    RedundancyView,
    Released,
    ReleaseMany,
    ReleaseRequest,
)
from sift.slices.dedup.service import (
    LEVEL_KEY,
    MAX_DURATION_GAP_KEY,
    RULE_LABELS,
    RULES,
    SERVICE,
    CarryOffer,
    DedupError,
    DedupService,
    Dials,
    NotAllowed,
    NotFound,
    read_dials,
)

log = get_logger(__name__)

router = APIRouter(tags=["dedup"])

_PAGE = 100

#: Each row draws a still and its paths, so a page is what a screen shows.
RECLAIM_PAGE = 24
#: The shared ceiling: the panel sizes its page from the screen.
MAX_RECLAIM = MAX_PAGE_SIZE


def _service(request: Request) -> DedupService:
    return part_of(request, SERVICE)


def _refusal(error: DedupError) -> HTTPException:
    """Each refusal with its own status (404, 403 or 409) and its own sentence."""
    if isinstance(error, NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    if isinstance(error, NotAllowed):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(error))
    return HTTPException(status.HTTP_409_CONFLICT, str(error))


#: Up to eight stills each; also the unit a confirm acts on.
GROUP_PAGE = 24


async def _facts_of(
    request: Request, admin: Viewer, groups: Sequence[Group]
) -> dict[str, FileView]:
    """What the screen prints about these files, in three reads; hidden files are left out."""
    access = wiring.access(request)
    wanted = sorted({one for group in groups for one in group.ids})
    if not wanted:
        return {}
    views = await access.assets_of(admin, wanted)
    places = await _service(request).places_of(list(views))
    said = await wiring.whereabouts(request, admin)
    found: dict[str, FileView] = {}
    for asset_id, view in views.items():
        asset = view.asset
        place = places.get(asset_id)
        where = said.of(place.root_id, place.rel_path) if place is not None else None
        found[asset_id] = FileView(
            id=asset_id,
            media_type=asset.media_type,
            concealed=view.concealed,
            # Nothing about a concealed file is printed: not its name and not where it sits.
            original_filename=None if view.concealed else asset.original_filename,
            where=None if view.concealed else where,
            size_bytes=None if view.concealed else asset.size_bytes,
            width=None if view.concealed else asset.width,
            height=None if view.concealed else asset.height,
            duration_ms=None if view.concealed else asset.duration_ms,
            container=None if view.concealed else asset.container,
            added_at=None if view.concealed else asset.added_at,
            art=None if view.concealed else view.art_version,
        )
    return found


def _group_view(group: Group, facts: dict[str, FileView]) -> GroupView:
    """One group as the screen reads it, with no keeper where a file is hidden from this session."""
    hidden = any(facts[one].concealed for one in group.ids if one in facts)
    return GroupView(
        files=[facts[one] for one in group.ids if one in facts],
        method=group.method,
        distance=group.distance,
        keeper=None if hidden else group.keeper,
        too_big=group.too_big,
    )


@router.get("/dedup/groups", response_model=GroupList)
async def list_groups(
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    settings: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = GROUP_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
    needs_you: Annotated[bool, Query()] = False,
    start: Annotated[str | None, Query(alias="from", max_length=200)] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
) -> GroupList:
    """The review queue as groups, paged before the vault is applied; `from` resumes at a group."""
    service = _service(request)
    dials = await read_dials(settings)
    every = await service.groups(dials)
    wanted = [one for one in every if one.needs_a_person] if needs_you else every
    if start is not None:
        at = next((place for place, one in enumerate(wanted) if group_key(one) == start), None)
        offset = resume_at(at, near)

    window = wanted[offset : offset + limit]
    facts = await _facts_of(request, admin, window)
    # A group with a file this user may not have is left off this page, and counted.
    shown = [one for one in window if all(id_ in facts for id_ in one.ids)]

    matching, waiting = await service.pending_counts(
        level=dials.level, max_duration_gap_ms=dials.max_duration_gap_ms
    )
    return GroupList(
        groups=[_group_view(one, facts) for one in shown],
        total=len(wanted),
        offset=offset,
        needs_you=sum(1 for one in every if one.needs_a_person),
        matching=matching,
        pending_total=waiting,
        concealed=len(window) - len(shown),
        awaiting_fingerprint=await service.awaiting_fingerprint(),
        cannot_fingerprint=await service.cannot_fingerprint(),
        level=dials.level.value,
        max_duration_gap_ms=dials.max_duration_gap_ms,
        rule=dials.rule,
        rules=[
            ChoiceView(key=key, label=label) for key, label in zip(RULES, RULE_LABELS, strict=True)
        ],
        levels=_declared_levels(),
        max_duration_gap_limit=_declared_gap_limit(),
        max_duration_gap_word=_declared_gap_word(),
    )


def group_key(group: Group) -> str:
    """The one name a group has: its method and its smallest file (the client's `keyOf`)."""
    return f"{group.method}:{group.ids[0]}"


@router.get("/dedup/groups/{method}/{first}", response_model=GroupView)
async def one_group(
    method: str,
    first: str,
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    settings: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
) -> GroupView:
    """One group by method and smallest file; 404 when the dials moved or it was settled."""
    service = _service(request)
    dials = await read_dials(settings)
    for group in await service.groups(dials):
        if group.method != method or group.ids[0] != first:
            continue
        facts = await _facts_of(request, admin, [group])
        if not all(id_ in facts for id_ in group.ids):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "A file in that group isn't yours to see."
            )
        return _group_view(group, facts)
    raise HTTPException(status.HTTP_404_NOT_FOUND, "There's no such group at the current settings.")


def _declared_levels() -> list[ChoiceView]:
    """The closeness dial's choices from their registry, or none if the setting is missing."""
    declared = get_registered(LEVEL_KEY)
    if declared is None or declared.choices is None or declared.choice_labels is None:
        return []
    return [
        ChoiceView(key=str(key), label=label)
        for key, label in zip(declared.choices, declared.choice_labels, strict=True)
    ]


def _declared_gap_limit() -> int:
    declared = get_registered(MAX_DURATION_GAP_KEY)
    return declared.maximum if declared is not None and declared.maximum is not None else 3600


def _declared_gap_word() -> str | None:
    declared = get_registered(MAX_DURATION_GAP_KEY)
    return declared.automatic_label if declared is not None else None


async def _acting_on(
    request: Request,
    admin: Viewer,
    dials: Dials,
    asked: GroupSettleRequest,
) -> tuple[list[tuple[Group, str | None]], int]:
    """Accept only groups the server computed at these dials with no hidden file; count the rest."""
    every = await _service(request).groups(dials)
    by_ids = {group.ids: group for group in every}
    matched: list[tuple[Group, str | None]] = []
    for choice in asked.groups:
        group = by_ids.get(tuple(sorted(set(choice.ids))))
        if group is None or group.too_big:
            continue
        # The keeper as asked for, never inferred: a stray id is refused rather than deleting files.
        matched.append((group, choice.keep))
    if not matched:
        return [], len(asked.groups)

    access = wiring.access(request)
    wanted = sorted({one for group, _ in matched for one in group.ids})
    views = await access.assets_of(admin, wanted)
    allowed = {one for one, view in views.items() if not view.concealed}
    kept = [pair for pair in matched if all(one in allowed for one in pair[0].ids)]
    return kept, len(asked.groups) - len(kept)


@router.post(
    "/dedup/groups/confirm",
    response_model=GroupSettleResult,
    dependencies=[Depends(csrf_protect)],
)
async def confirm_groups(
    body: GroupSettleRequest,
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    settings: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
    _csrf: Annotated[None, Depends(csrf_protect)],
) -> GroupSettleResult:
    """Keep one file of each group and delete the rest, permanently; one receipt per group."""
    service = _service(request)
    dials = await read_dials(settings)
    acting, unknown = await _acting_on(request, admin, dials, body)

    settled = removed = refused = 0
    for group, keep in acting:
        if keep is None or keep not in group.ids:
            # No keeper in the group: acting would delete every file.
            unknown += 1
            continue
        try:
            done = await service.settle_group(group, keep=keep, actor=admin)
        except DedupError as error:
            raise _refusal(error) from error
        settled += 1
        removed += len(done.removed)
        refused += len(done.refused)
    return GroupSettleResult(settled=settled, removed=removed, refused=refused, unknown=unknown)


@router.post(
    "/dedup/groups/dismiss",
    response_model=GroupSettleResult,
    dependencies=[Depends(csrf_protect)],
)
async def dismiss_groups(
    body: GroupSettleRequest,
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    settings: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
    _csrf: Annotated[None, Depends(csrf_protect)],
) -> GroupSettleResult:
    """Mark these groups' files as not the same; every pair is kept answered for good."""
    service = _service(request)
    dials = await read_dials(settings)
    acting, unknown = await _acting_on(request, admin, dials, body)
    for group, _keep in acting:
        await service.dismiss_group(group, actor=admin)
    return GroupSettleResult(settled=len(acting), removed=0, refused=0, unknown=unknown)


@router.get("/reclaim", response_model=ReclaimView)
async def reclaim_space(
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=MAX_RECLAIM)] = RECLAIM_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
    start: Annotated[str | None, Query(alias="from", max_length=200)] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
) -> ReclaimView:
    """Assets stored in more than one place, a page at a time; totals are whole-library."""
    service = _service(request)
    access = wiring.access(request)
    if start is not None:
        offset = resume_at(await service.reclaim_position(start), near)
    entries = await service.reclaim(limit=limit, offset=offset)
    # Rows and vault in one walk of the sharing rules.
    rows = await access.assets_of(admin, [entry.asset_id for entry in entries])
    # A locked tile is dropped too: every row prints its copies' paths.
    shown = [
        entry for entry in entries if entry.asset_id in rows and not rows[entry.asset_id].concealed
    ]
    # The whole path: two copies usually differ only by library folder.
    said = await wiring.whereabouts(request, admin)
    totals = await service.reclaim_totals()
    return ReclaimView(
        assets=[
            RedundancyView(
                asset_id=entry.asset_id,
                media_type=entry.media_type,
                width=rows[entry.asset_id].asset.width,
                height=rows[entry.asset_id].asset.height,
                duration_ms=rows[entry.asset_id].asset.duration_ms,
                art=rows[entry.asset_id].art_version,
                copies=[
                    CopyView(
                        location_id=copy.location_id,
                        root_id=copy.root_id,
                        rel_path=copy.rel_path,
                        filename=copy.filename,
                        size_bytes=copy.size_bytes,
                        path=said.of(copy.root_id, copy.rel_path),
                    )
                    for copy in entry.copies
                ],
                reclaimable_bytes=entry.reclaimable_bytes,
            )
            for entry in shown
        ],
        total=totals.assets,
        total_reclaimable_bytes=totals.reclaimable_bytes,
        concealed=len(entries) - len(shown),
        offset=offset,
    )


@router.post("/reclaim/release-many", response_model=Released)
async def release_many(
    body: ReleaseMany,
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    _csrf: Annotated[None, Depends(csrf_protect)],
) -> Released:
    """Let go of a page of copies; a refusal counts rather than stopping the page."""
    service = _service(request)
    released = 0
    refused = 0
    for one in body.releases[:RECLAIM_PAGE]:
        try:
            await service.release(one.asset_id, one.location_id, actor=admin)
            released += 1
        except DedupError:
            refused += 1
    return Released(released=released, refused=refused)


@router.post("/reclaim/{asset_id}/release", status_code=status.HTTP_204_NO_CONTENT)
async def release_copy(
    asset_id: str,
    body: ReleaseRequest,
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    _csrf: Annotated[None, Depends(csrf_protect)],
) -> None:
    """Let go of one copy, deleting it from disk; the asset stays where its other copies are."""
    try:
        await _service(request).release(asset_id, body.location_id, actor=admin)
    except DedupError as error:
        raise _refusal(error) from error


# Carrying an attribution onto copies: read at the tightest setting, never the reader's dial.


async def _offers_for(request: Request, admin: Viewer, settings: SettingsSeam) -> list[CarryOffer]:
    """Every offer in the library, minus groups with a file this admin may not be shown."""
    service = _service(request)
    offers = await service.carry_offers(await read_dials(settings))
    if not offers:
        return []
    access = wiring.access(request)
    wanted = sorted({one for offer in offers for one in offer.ids})
    views = await access.assets_of(admin, wanted)
    allowed = {one for one, view in views.items() if not view.concealed}
    return [offer for offer in offers if all(one in allowed for one in offer.ids)]


@router.get("/dedup/carry", response_model=CarryTotals)
async def carry_totals(
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    settings: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
) -> CarryTotals:
    """How many copies know less about themselves than a twin: exactly what the press changes."""
    offers = await _offers_for(request, admin, settings)
    return CarryTotals(groups=len(offers), files=sum(offer.files for offer in offers))


@router.post("/dedup/carry/all", response_model=CarryResult, dependencies=[Depends(csrf_protect)])
async def carry_everywhere(
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    settings: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
    _csrf: Annotated[None, Depends(csrf_protect)],
) -> CarryResult:
    """Carry every offer in the library, one transaction and one receipt per group."""
    service = _service(request)
    carried = files = 0
    for offer in await _offers_for(request, admin, settings):
        written = await service.carry(offer, actor=admin)
        if written:
            carried += 1
            files += written
    return CarryResult(carried=carried, files=files)
