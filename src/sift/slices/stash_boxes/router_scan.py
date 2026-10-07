# SPDX-License-Identifier: AGPL-3.0-or-later
"""Starting the bulk passes: the fingerprint scan over files, and the enrichment of names."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.jobs import JobQueue
from sift.kernel.jobs.quiet_hours import AT_NOW
from sift.kernel.reach import (
    KEPT_LOCAL_LEFT_OUT,
    KEPT_LOCAL_LEFT_OUT_MANY,
    OUT_OF_REACH,
    OUT_OF_REACH_MANY,
    VAULT_LOCKED,
    VAULT_LOCKED_MANY,
    conceals,
    refuse_one,
)
from sift.kernel.records import (
    Subject,
)
from sift.kernel.seams import SettingsSeam
from sift.slices.auth import csrf_protect, master_key, require_admin
from sift.slices.stash_boxes.jobs import (
    STASH_ENRICH,
    STASH_SCAN,
    STASH_SWEEP,
)
from sift.slices.stash_boxes.models import (
    EnrichEntities,
    EnrichStarted,
    ScanStarted,
    ScanWhat,
)
from sift.slices.stash_boxes.queue import name_of
from sift.slices.stash_boxes.router_base import (
    _kept_local,
    _no_such_file,
    _service,
    _subject,
)
from sift.slices.stash_boxes.service import (
    StashBoxService,
)
from sift.slices.stash_boxes.settings import ENRICHING_OFF, SCAN_KEY, box_for

router = APIRouter(tags=["stash-boxes"])

# --- the bulk pass --------------------------------------------------------------------------

#: The most files one Enrich press may name. The grid's "Select all" picks at most this many
#: (`MOST_SELECTED` in the client's grid), so a selection made in one gesture is always taken whole.
#: Each named file is one open, one kept-local read and one queued task before the reply, which is
#: why there is a ceiling at all.
MOST_NAMED_FILES = 1000

#
# Admin-only, every one of them, for the reason the top of this file gives: each makes Sift send
# requests to somebody else's service with a stored key, or writes what came back.


@router.post("/stash-boxes/scan", dependencies=[Depends(csrf_protect)])
async def start_scan(
    request: Request,
    viewer: Annotated[Viewer, Depends(require_admin)],
    access: Annotated[Repository, Depends(wiring.access)],
    service: Annotated[StashBoxService, Depends(_service)],
    settings: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
    body: ScanWhat | None = None,
) -> ScanStarted:
    """Ask the stash-boxes what the library is, or what one folder of it is.

    The body is optional and so is the folder in it, so a caller that wants the whole library
    posts nothing at all. Queues the work and returns immediately; it shows up in the job list. Refused
    rather than ignored when the feature is off: a button whose job declines reads as broken.
    """
    if not bool(await settings.get_app(SCAN_KEY)):
        raise HTTPException(status.HTTP_409_CONFLICT, ENRICHING_OFF)
    # Which box, decided once for the whole press (see `settings.box_for`), and refused, not
    # started, when it would ask nobody: the rule every press shares (`cannot_ask`).
    chosen = await box_for(settings, body.box if body else None)
    nobody = await service.cannot_ask(chosen)
    if nobody is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, nobody)
    queue = wiring.part_of(request, wiring.QUEUE)
    if body and body.assets:
        return await _scan_named(body, body.assets, viewer, access, service, queue, chosen)
    # The sweep is told which box ONCE, here, rather than each of its children reading the setting
    # again (`jobs.sweep`); "" in the payload is every box. A press, so the press decides whether a
    # certain match is accepted, and `at=AT_NOW` keeps the lookups' When from refusing it.
    job_id = await queue.enqueue(
        STASH_SWEEP,
        {
            "viewer": viewer.id,
            "offset": 0,
            "queued": 0,
            "folder": body.folder if body else None,
            "box": chosen,
            "apply": bool(body and body.auto),
        },
        requested_by=viewer.id,
        at=AT_NOW,
    )
    return ScanStarted(job_id=job_id)


async def _scan_named(
    body: ScanWhat,
    wanted: list[str],
    viewer: Viewer,
    access: Repository,
    service: StashBoxService,
    queue: JobQueue,
    chosen: str,
) -> ScanStarted:
    """Named files go straight to the per-file job, one each, and skip the sweep.

    The whole selection or a refusal, never a quiet cut. The files that cannot be asked about are
    named in the reply: a file in a locked vault or kept local must not look like one that was
    sent and matched nothing.
    """
    if len(wanted) > MOST_NAMED_FILES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Enrich takes up to {MOST_NAMED_FILES:,} files at a time. Select fewer and try again.",
        )
    # `open_asset`, the same read the job makes, so the two cannot disagree about which files go.
    reachable = [
        asset.id
        for asset in [await access.open_asset(viewer, one) for one in wanted]
        if asset is not None
    ]
    kept = [one for one in reachable if await service.kept_local(Subject.ASSET, one)]
    held_back = set(kept)
    reachable = [one for one in reachable if one not in held_back]
    if kept and not reachable:
        raise _kept_local()
    if not reachable:
        # 423 with the vault's own sentence where that is the reason, as every other write on a
        # concealed file answers.
        raise await refuse_one(access, viewer, wanted[0], _no_such_file)
    # One write for the whole selection, and a press that says whose (`requested_by`), so the
    # enrichment task's When does not hold it as work nobody asked for.
    queued = await queue.enqueue_many(
        STASH_SCAN,
        [
            {
                "asset_id": one,
                "viewer": viewer.id,
                "box": chosen,
                "apply": body.auto,
                "again": body.again,
            }
            for one in reachable
        ],
        requested_by=viewer.id,
    )
    asked_about = set(reachable)
    left_out = [one for one in wanted if one not in asked_about]
    return await _named_started(access, viewer, queued[0], reachable, left_out, kept)


async def _named_started(
    access: Repository,
    viewer: Viewer,
    job_id: str,
    reachable: list[str],
    left_out: list[str],
    kept: list[str],
) -> ScanStarted:
    """The reply to a press on named files: the first job to watch, and one reason for what was
    left out. The vault wins, since it needs a PIN rather than a menu row; then kept local; and
    only then "could not find"."""
    concealed = bool(left_out) and await conceals(access, viewer, left_out[0])
    refused_here = bool(kept) and not concealed
    if concealed:
        reason, reason_many = VAULT_LOCKED, VAULT_LOCKED_MANY
    elif refused_here:
        reason, reason_many = KEPT_LOCAL_LEFT_OUT, KEPT_LOCAL_LEFT_OUT_MANY
    else:
        reason, reason_many = OUT_OF_REACH, OUT_OF_REACH_MANY
    return ScanStarted(
        job_id=job_id,
        asked=len(reachable),
        skipped=len(left_out),
        reason=reason if left_out else None,
        reason_many=reason_many if left_out else None,
        vault_locked=concealed,
        kept_local=refused_here,
    )


@router.post("/stash-boxes/enrich", dependencies=[Depends(csrf_protect)])
async def enrich_entities(
    request: Request,
    body: EnrichEntities,
    access: Annotated[Repository, Depends(wiring.access)],
    service: Annotated[StashBoxService, Depends(_service)],
    settings: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
    key: Annotated[bytes | None, Depends(master_key)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> EnrichStarted:
    """Ask the stash-boxes about a batch of people, sites or tags.

    Queues and returns immediately, for the reason the sweep does: one request per subject to
    somebody else's service, paced, so forty of them is most of a minute and a screen that waited
    for it would look broken.

    Every id is resolved through the SCOPED read first, and one that resolves to nothing is dropped
    rather than refusing the batch. Two things follow. A subject this user may not be shown does
    not have its name sent to three public services, which is the same rule the file scan follows,
    for the same reason. And a list that has drifted since the screen drew it (somebody deleted a
    tag a minute ago) still enriches the rest.

    The key rides on the job because the boxes are asked with it and a job runs long after the
    request that made it. It is the same key the request already carried; nothing new is unsealed
    and nothing is written down anywhere it was not already.
    """
    # Refused, not started, when the press would ask nobody: the rule every press shares.
    chosen = await box_for(settings, body.box)
    nobody = await service.cannot_ask(chosen)
    if nobody is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, nobody)
    kind = _subject(body.subject)
    wanted = []
    kept = 0
    for local_id in body.ids:
        # One read, for the reason the picture route gives: no name is the same answer as may not
        # be seen, because it is the same scoped read that decides both.
        name = await name_of(access, viewer, kind, local_id)
        if name is None:
            continue
        if await service.kept_local(kind, local_id):
            # Left out here as well as refused at the door, and for the reason the file form gives:
            # a batch of forty where three are kept local is not a batch to throw away, it is a
            # batch of thirty-seven and a count. A batch of NOTHING but kept-local rows is a
            # refusal, because there is nothing left to watch.
            kept += 1
            continue
        wanted.append({"subject": kind.value, "id": local_id, "name": name})
    if not wanted:
        raise (
            _kept_local()
            if kept
            else HTTPException(status.HTTP_404_NOT_FOUND, "none of those can be enriched")
        )

    queue = wiring.part_of(request, wiring.QUEUE)
    job_id = await queue.enqueue(
        STASH_ENRICH,
        # "Whatever is set up" when the press named no box, as the model says. See
        # `settings.box_for`.
        {
            "subjects": wanted,
            "key": key.hex() if key else None,
            "box": chosen,
        },
    )
    return EnrichStarted(job_id=job_id, asked=len(wanted))
