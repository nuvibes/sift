# SPDX-License-Identifier: AGPL-3.0-or-later
"""The maintenance endpoints. Every one of them is admin-only, and the server is what says so.

`require_admin` sits on every one of them, so a guest is refused before any looks at anything. That
is different from how deleting a single asset works: there the refusal has to come *after*
visibility is settled, so that being told "admins only" cannot confirm a file exists. Here there is
no such leak to avoid: these routes name no asset the caller chose, they describe the library as a
whole, and the honest answer to a guest asking about the library as a whole is no.

Hiding the Maintenance section in the client is a courtesy to the person using it, never the
control. These checks are the control.
"""

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

#: How many pairs one page of the queue carries. A review queue is worked through a pair at a
#: time, so this is a generous page rather than a tuning knob.
_PAGE = 100

#: How many redundant assets one page of the reclaim list carries, and the most it will hand back.
#:
#: A page rather than the whole population: a large library holds thousands of them, each with
#: every path under it. Twenty-four because each row draws a still and a list of paths, so a page is
#: what a screen can show rather than what a query can return.
RECLAIM_PAGE = 24
#: The most one request will hand back. The ceiling every paged route shares, rather than a
#: number of this route's own: the panel measures its own page size from the screen, so a
#: ceiling below what a large one asks for refuses the request as malformed, and the panel
#: reports that as there being nothing to reclaim.
MAX_RECLAIM = MAX_PAGE_SIZE


def _service(request: Request) -> DedupService:
    return part_of(request, SERVICE)


def _refusal(error: DedupError) -> HTTPException:
    """Each refusal with its own status, and its own sentence kept.

    The three are genuinely different answers and a client acts on each differently: 404 means
    there is nothing there for you, 403 means you may not, and 409 means the request was fine but
    the state of the disk says no: a folder Sift was never given write access to, a name already
    taken. Collapsing them would tell somebody to sign in again over a read-only drive.
    """
    if isinstance(error, NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    if isinstance(error, NotAllowed):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(error))
    return HTTPException(status.HTTP_409_CONFLICT, str(error))


#: How many groups one page carries. Each draws up to eight stills side by side, so this is what a
#: screen can show rather than what a query can return, and it is the unit the confirm acts on,
#: which is the other reason it is a page rather than a scroll.
GROUP_PAGE = 24


async def _facts_of(
    request: Request, admin: Viewer, groups: Sequence[Group]
) -> dict[str, FileView]:
    """Everything the screen prints about the files in these groups, in three reads for the page.

    Three rather than one per file. Deciding who may see a file walks the sharing rules
    recursively, and a page of groups is up to two hundred files.

    A file this user may not be shown is simply ABSENT here, which is what the caller reads as
    "drop this group". A file it may be shown as a locked placeholder comes back with `concealed`
    set: that is a real state (the tile is drawn, nothing about it is printed) and it is why the
    two cannot be collapsed into one.

    ## Why the queue is scoped at all, when the scan is not

    Duplicate-finding reads the whole library on purpose (it must, or it would miss duplicates in
    the least visible half of it), so its two content reads carry no permission or vault scope.
    That is right for the scan and wrong for the SCREEN: a vaulted asset is concealed from an admin
    who has not opened the vault this session, and a maintenance list that named it, or printed the
    path of one of its copies, would be the one disclosure vaulting it was meant to prevent. So the
    queue and the reclaim list are held to the vault here, through the same rules the grid uses,
    rather than a second copy of them. An unlocked vault reveals them again, exactly as elsewhere.
    """
    access = wiring.access(request)
    wanted = sorted({one for group in groups for one in group.ids})
    if not wanted:
        return {}
    views = await access.assets_of(admin, wanted)
    places = await _service(request).places_of(list(views))
    # Where each file sits, said the one way every screen says it. One read for the page.
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
            # The tile is a placeholder and the sentence beside it says to unlock the vault.
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
    """One group as the screen reads it, with the keeper withheld where it cannot be offered.

    **A group holding a file this session may not be shown carries NO keeper**, whatever the rule
    said. The rule works from measurements taken across the whole library, which is what makes one
    answer safe to keep and hand to everybody; whether a particular user may see a particular
    file is a fact about a session, and this is the boundary where the two meet. Marking a keeper
    here would be proposing to delete files on the strength of a comparison against something the
    person cannot look at.
    """
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
    """The review queue, as the groups somebody actually decides about.

    A group is N files chained by pairs that are all under the closeness dial, never mixing two
    fingerprint methods, and never longer than the cap. See the grouping module, where the whole
    argument lives. The rule marks a keeper in most of them; the ones it could not separate are the
    only ones that cost a real decision, and `needs_you` is what narrows the page to those.

    ## Why the page is cut before the vault rather than after

    A page here is a position in a list somebody is paging through, and the pager can always go to
    the next one, so a page that comes back short because a file in it is vaulted is honest and
    is not a queue that cannot empty. `concealed` says how many, which is the difference between a
    short page and the end of the list.

    ## `from`, the group a page starts at

    The same `from` every paged list in Sift takes: the group the page was left at, by the one name
    a group has (its method and its smallest file (`group_key`)), carried in the queue's address
    so the way back lands on the same page. Deciding a group is what takes it off this list, so a
    `from` naming nothing is the ordinary case and is answered with the page it was on (`near`),
    or the top. See `resume_at`.
    """
    service = _service(request)
    dials = await read_dials(settings)
    every = await service.groups(dials)
    wanted = [one for one in every if one.needs_a_person] if needs_you else every
    if start is not None:
        at = next((place for place, one in enumerate(wanted) if group_key(one) == start), None)
        offset = resume_at(at, near)

    window = wanted[offset : offset + limit]
    facts = await _facts_of(request, admin, window)
    # A group with a file this user may not have at all is not on this page, and is counted.
    # You cannot judge which of several files to keep when one of them is not there to look at.
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
    """The one name a group of look-alikes has: its method and its smallest file.

    A group has no row of its own (it is computed from the pair table at the dials in force), so
    this is the name the queue's page marks each group with, the name a still on the board points at
    (`queue._group_anchor`) and the name a page is asked for by (`from`). The client writes the same
    two parts the same way (`keyOf`).
    """
    return f"{group.method}:{group.ids[0]}"


@router.get("/dedup/groups/{method}/{first}", response_model=GroupView)
async def one_group(
    method: str,
    first: str,
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    settings: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
) -> GroupView:
    """One group, by the only name a group has: its method and its smallest file.

    For a chain opened on a screen of its own. A group is computed from the pair table at the
    dials in force and has no row of its own, so it is found the way the list finds it (every
    group at these dials, then the one that answers to the name), and a name that answers to
    nothing is a 404: the dial moved, or somebody settled it in another window.

    Every file in it has to be this user's to look at, the same rule the page applies. A
    chain with a vaulted file in it is not a set anybody can judge, and is not shown.
    """
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
    """The closeness dial's choices, from the registry that declares them.

    Read here rather than listed here, because the words are the settings screen's own and a second
    copy of them is one that drifts. The dial itself lives on the QUEUE: it decides what the list
    under it holds, and a control read together with what it governs does not belong on a
    different screen.

    A registry that has somehow lost the setting answers with nothing rather than failing the read:
    the queue is still perfectly readable without its dial, and a screen that will not draw at all
    because a preference is missing is the worse of the two.
    """
    declared = get_registered(LEVEL_KEY)
    if declared is None or declared.choices is None or declared.choice_labels is None:
        return []
    return [
        ChoiceView(key=str(key), label=label)
        for key, label in zip(declared.choices, declared.choice_labels, strict=True)
    ]


def _declared_gap_limit() -> int:
    """The largest length rule the dial accepts, as the setting declares it."""
    declared = get_registered(MAX_DURATION_GAP_KEY)
    return declared.maximum if declared is not None and declared.maximum is not None else 3600


def _declared_gap_word() -> str | None:
    """The word the setting declares for its zero ("lengths are not compared")."""
    declared = get_registered(MAX_DURATION_GAP_KEY)
    return declared.automatic_label if declared is not None else None


async def _acting_on(
    request: Request,
    admin: Viewer,
    dials: Dials,
    asked: GroupSettleRequest,
) -> tuple[list[tuple[Group, str | None]], int]:
    """Match what was asked for against the server's OWN groups, and drop anything that is not one.

    This is the whole of the safety in the two routes below. A request names a SET OF FILES, and
    nothing about a set of files makes it safe to delete all but one of them, so the set has to be
    a group the server itself computed at the dials in force, or the request is not acted on.

    Two ways it can fail to match, and both are ordinary rather than suspicious: a dial moved while
    the screen was open, or somebody settled the same group in another window. Either way the
    honest answer is to leave it alone and say how many were left, which is what the count is for.

    Vaulted files are refused here too, for the same reason the read withholds a keeper: a request
    to delete every file but one, where one of them cannot be looked at, is not a decision anybody
    made.
    """
    every = await _service(request).groups(dials)
    by_ids = {group.ids: group for group in every}
    matched: list[tuple[Group, str | None]] = []
    for choice in asked.groups:
        group = by_ids.get(tuple(sorted(set(choice.ids))))
        if group is None or group.too_big:
            continue
        # The keeper as ASKED FOR, never inferred from the group. Reading it off the named ids
        # instead would make "keep this one" mean "keep whichever of these the server happens to
        # look at first", and a request naming a file that is not in the group at all would then
        # delete files rather than being refused.
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
    """Keep one file of each of these groups and delete the rest. Permanent, and it says so.

    A page at a time rather than the whole queue: a press that was all-or-nothing across everything
    waiting could only be used by trusting a rule over thousands of files nobody had looked at. A
    page is what somebody can actually have skimmed before pressing.

    One receipt per GROUP, so History's Decisions lists each of them and can say what each one did.
    """
    service = _service(request)
    dials = await read_dials(settings)
    acting, unknown = await _acting_on(request, admin, dials, body)

    settled = removed = refused = 0
    for group, keep in acting:
        if keep is None or keep not in group.ids:
            # Named nothing, or named a file that is not in the group. Either way there is no
            # decision here to act on, and acting would delete every file in it and keep none.
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
    """Say these files are not the same thing. Nothing is deleted, and the answer has to last.

    Every pair inside each group is written down as answered and stays in the table for ever, which
    is the only thing that makes it survive the next scan: that scan finds exactly the same pairs
    and offers them again, and an insert that collides does nothing rather than resetting a row.
    """
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
    """Assets sitting in more than one place, and what dropping the extras would free.

    No judgement involved: these are identical bytes that the content model already resolved into
    one asset with several locations. The only question is which copies to keep, and that is the
    admin's to answer: a second copy on a second disk may be deliberate.

    ## A page, and two numbers about the whole

    On a large library there are thousands of assets and twice as many paths, and the mark that
    watches for new ones asks again on a timer. So this is a page, and `total` says what the page
    is a part of.

    `total` and `total_reclaimable_bytes` are whole-library and are NOT held to the vault, the same
    way the review queue's own totals are not. What the vault conceals is a file's name and the
    paths of its copies, and no page prints one it may not: `concealed` counts what this page
    dropped for that reason. A count and a number of bytes describe a population rather than a
    file, and scoping them would mean resolving permission for every asset in the library to draw
    one sentence.

    `from` names the file a page starts at: the row the tab was left at, carried in its address
    so the way back lands on the same page. Letting a file's last extra copy go takes it off this
    list, so a `from` naming nothing is answered with the page it was on (`near`), or the top.
    """
    service = _service(request)
    access = wiring.access(request)
    if start is not None:
        offset = resume_at(await service.reclaim_position(start), near)
    entries = await service.reclaim(limit=limit, offset=offset)
    # The rows AND the vault in one question. `visible_of` is this call with the rows thrown away,
    # and the tile prints a shape and a length, so asking twice would be two walks of the sharing
    # rules over the same page to answer one of them.
    rows = await access.assets_of(admin, [entry.asset_id for entry in entries])
    # A file shown as a locked tile is dropped as well: every row here prints its copies' paths.
    shown = [
        entry for entry in entries if entry.asset_id in rows and not rows[entry.asset_id].concealed
    ]
    # Where each copy sits, the whole path: two copies of one file usually differ only in which
    # library folder they are in, so the path inside it is the half that is the same on both.
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
    """Let go of a page of copies in one press.

    The exact-copies queue settles a page at a time, like the near-duplicate queue beside it: the
    two are tabs of one job. Each copy goes through the same
    `release` the single press uses, with the same refusals (the last copy of a file is never
    let go), and a refusal counts rather than stopping the page: the copies after it are still
    somebody's decision.
    """
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
    """Let go of one copy, keeping the asset and everything recorded about it.

    The asset survives because it still sits somewhere else: that is what makes this safe to
    offer, and it is why a copy that is not one of several is not on this screen to begin with.
    The copy itself is deleted from the disk, and cannot be put back.
    """
    try:
        await _service(request).release(asset_id, body.location_id, actor=admin)
    except DedupError as error:
        raise _refusal(error) from error


# --- carrying an attribution onto the copies ---------------------------------------------------
#
# Read at the tightest setting there is and never at the reader's dial. See `carry_offers`, which
# says why. So these three routes take no dial of their own: what they act on is not a matter of
# anybody's preference.


async def _offers_for(request: Request, admin: Viewer, settings: SettingsSeam) -> list[CarryOffer]:
    """Every offer the library holds, with the ones this session may not act on left out.

    The permission check is on the FILES and is made here rather than in the service, for the reason
    every other route in this file makes it here: the service answers about a library and a session
    is a fact about a request. A group holding a file this admin may not be shown is dropped whole,
    exactly as settling one is: writing on the copies of a file somebody cannot look at is not a
    decision they made.

    Only the files an offer actually names are resolved, which is a handful rather than every file
    that is in a group at all.
    """
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
    """How many copies in the whole library know less about themselves than a twin of theirs does.

    The count comes before the press, and it is the same computation the press then makes, so the
    number an admin is shown is the number of files that will change, not an estimate of one.
    """
    offers = await _offers_for(request, admin, settings)
    return CarryTotals(groups=len(offers), files=sum(offer.files for offer in offers))


@router.post("/dedup/carry/all", response_model=CarryResult, dependencies=[Depends(csrf_protect)])
async def carry_everywhere(
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    settings: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
    _csrf: Annotated[None, Depends(csrf_protect)],
) -> CarryResult:
    """Carry every offer the library holds, once. The count above is what it does.

    **The whole library and not one group, and that is what the screen actually offers.** A group
    on the queue is clustered at the READER's dial and an offer is read bit-for-bit, so a group in
    front of somebody is regularly a superset of the one a carry would act on: a per-group press
    would be pointed at a set of files that is not the set being written on. The count says how many
    files change before the press, and a receipt per file is what makes a press this wide safe:
    an admin who regrets it takes back as many of them as they disagree with, one at a time.

    ## One transaction per group, never one for all

    A carry over a hundred groups in one transaction is a write lock held for the length of a
    hundred groups, and a failure halfway through would take back the ninety that had already
    worked. They are independent decisions (separate groups, separate receipts), so they are
    written one at a time, and a group that wrote nothing is simply a group that wrote nothing.
    """
    service = _service(request)
    carried = files = 0
    for offer in await _offers_for(request, admin, settings):
        written = await service.carry(offer, actor=admin)
        if written:
            carried += 1
            files += written
    return CarryResult(carried=carried, files=files)
