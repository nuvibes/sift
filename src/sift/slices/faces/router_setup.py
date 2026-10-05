# SPDX-License-Identifier: AGPL-3.0-or-later
"""The faces settings and the work behind them: the models, a look through the library,
regrouping, packs, and forgetting everything."""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.access.catalog import refused_over
from sift.kernel.db import Database
from sift.kernel.jobs import WAITED_ON_PRIORITY, JobQueue
from sift.kernel.serving import face_version
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.faces.jobs import (
    FACE_FETCH_WEIGHTS,
    FACE_FORGET,
    FACE_REGROUP,
    FACE_SWEEP,
    ask_for_fingerprints,
    ask_for_rematching,
)
from sift.slices.faces.models_http import (
    FaceSettingsView,
    FetchStarted,
    FingerprintOffers,
    FingerprintOfferView,
    KnownPeople,
    KnownPerson,
    MadeFromFingerprints,
    PackExportRequest,
    PackImported,
    WaitingEntry,
    WaitingFingerprints,
)
from sift.slices.faces.packs import NOT_ONE, PackError
from sift.slices.faces.router_common import (
    _off,
    _service,
    log,
)
from sift.slices.faces.service import (
    FaceService,
)
from sift.slices.faces.service_references import band_of
from sift.slices.faces.weights import CATALOG, WeightError, installed

router = APIRouter(tags=["faces"])


def _pack_filename(name: str) -> str:
    """A safe download name built from the pack's own: reduced to letters, digits, dashes and
    underscores, since it goes into a response header."""
    safe = "".join(
        character if character.isalnum() or character in "-_" else "-" for character in name
    )
    return f"{safe.strip('-') or 'faces'}-faces.zip"


# --- the feature itself ---------------------------------------------------------------------------


@router.get("/faces/settings")
async def face_settings_state(
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FaceSettingsView:
    """What the feature is set to and whether it can actually run. Admin-only: it describes the
    server. `enabled` and `ready` are two fields, so a fresh install reads as waiting for a
    download rather than broken."""
    configured = await service.configuration()
    never_scanned, scanned_before = await service.backlog()
    return FaceSettingsView(
        enabled=await service.enabled(),
        ready=await service.ready(),
        family=configured.family,
        device=configured.device,
        depth=configured.depth.value,
        device_problem=await service.device_problem(),
        last_run_at=await service.last_run_at(),
        last_run_canceled=await service.last_run_canceled(),
        measured_by_another_model=await service.measured_by_another_model(),
        references_without_pictures=await service.references_without_pictures(),
        never_scanned=never_scanned,
        scanned_under_older_rules=scanned_before,
        installed=sorted(
            weight_id
            for weight_id, weight in CATALOG.items()
            if installed(service.settings, weight)
        ),
    )


@router.post("/faces/weights/fetch", dependencies=[Depends(csrf_protect)])
async def fetch_weights(
    service: Annotated[FaceService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    again: bool = False,
) -> FetchStarted:
    """Fetch the models this install is set to use. Hands back the job doing it.

    **Sift ships no models**: what this downloads is licensed by others, so an admin asks for it.
    Queued (minutes long), and a second press joins the one waiting or under way; the job id lets
    a screen follow and cancel it. Answers 409 with the feature off. `again=true` fetches files
    already here too, for a damaged model.
    """
    if not await service.enabled():
        raise _off()
    newest = [job.id for job in await queue.newest_of(FACE_FETCH_WEIGHTS, limit=1)]
    live = await queue.unfinished_among(newest)
    # Ahead of the library-wide work: somebody is watching the bar (`WAITED_ON_PRIORITY`).
    job_id = (
        live.pop()
        if live
        else await queue.enqueue(
            FACE_FETCH_WEIGHTS, {"again": again}, priority=WAITED_ON_PRIORITY, dedupe=True
        )
    )
    log.info("faces.weights.requested", job_id=job_id, again=again)
    return FetchStarted(job_id=job_id)


@router.post("/faces/scan", dependencies=[Depends(csrf_protect)])
async def scan_library(
    service: Annotated[FaceService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    force: Annotated[bool, Query()] = False,
) -> FetchStarted:
    """Look at everything that wants looking at. Hands back the job doing it.

    Covers what was in the library when the feature was turned on, and every file scanned under
    settings that have since changed. `force` offers everything regardless (a model swapped, bad
    crops, given-up files): the whole library, from the top. Queued in batches; the id lets a
    screen follow and stop it.

    Answers 409 with the feature off, or when the device it runs on is not there: a scan that
    cannot happen is refused here, so no caller can start one.
    """
    if not await service.enabled():
        raise _off()
    # The device AND the chosen models (`FaceService.cannot_scan`), as every door that scans asks.
    problem = await service.cannot_scan()
    if problem is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, problem)
    # ONE WALK OF THE LIBRARY AT A TIME: a second walk would queue every file twice, and a second
    # window does not know the button is hidden. Asked of the queue (a sweep is running or has its
    # next page waiting); `dedupe` closes the gap of two presses in the same instant.
    if await queue.outstanding(FACE_SWEEP):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The library is already being looked through for faces. Stop that run first, or let"
            " it finish.",
        )
    job_id = await queue.enqueue(
        FACE_SWEEP,
        {"viewer": viewer.id, "offset": 0, "force": force, "queued": 0},
        dedupe=True,
        # The Identify pass this starts names the person who pressed it. See `jobs.requested_by`.
        requested_by=viewer.id,
    )
    log.info("faces.sweep.requested", job_id=job_id, force=force)
    return FetchStarted(job_id=job_id)


@router.post("/faces/regroup", dependencies=[Depends(csrf_protect)])
async def regroup_faces(
    service: Annotated[FaceService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FetchStarted:
    """Pile the unclaimed faces up again, now, which otherwise happens only when scanning settles.

    Cheap and safe to repeat; a pile somebody set aside is left alone.

    Answers 409 with the feature off, or when the device it runs on is not there: a scan that
    cannot happen is refused here, so no caller can start one.
    """
    if not await service.enabled():
        raise _off()
    problem = await service.device_problem()
    if problem is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, problem)
    job_id = await queue.enqueue(FACE_REGROUP, {"full": True}, requested_by=viewer.id)
    log.info("faces.regroup.requested", job_id=job_id)
    return FetchStarted(job_id=job_id)


@router.get("/faces/known")
async def known_people(
    service: Annotated[FaceService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    q: Annotated[str, Query(max_length=120)] = "",
) -> KnownPeople:
    """Who Sift can already recognize, searchable by name: People with reference faces.

    Admin-only, since the gallery is curation; held to the People wall, so somebody a shut vault
    holds back is not named.
    """
    if not await service.enabled():
        raise _off()
    listed = await service.roster(q)
    shown = await access.visible_people(viewer, [one[0] for one in listed])
    found = [one for one in listed if one[0] in shown]
    created_from = await service.created_from([one[0] for one in found])
    art = face_version(viewer.cache_stamp)
    return KnownPeople(
        items=[
            KnownPerson(
                id=person_id,
                name=name,
                faces=faces,
                verdict=band_of(faces),
                starters=starters,
                # Her picture, as the People wall reads it for this viewer: withheld with the file.
                cover_asset_id=shown[person_id].cover_asset_id,
                cover_upload_id=shown[person_id].cover_upload_id,
                cover_at_ms=shown[person_id].cover_at_ms,
                cover_frame=shown[person_id].cover_frame,
                cover_track_id=shown[person_id].cover_track_id,
                art=art,
                keep_local=shown[person_id].keep_local,
                keep_from_swaps=shown[person_id].keep_from_swaps,
                from_fingerprints=created_from.get(person_id),
            )
            for person_id, name, faces, starters in found
        ],
        total=len(found),
    )


@router.post("/faces/packs/import", dependencies=[Depends(csrf_protect)])
async def import_pack(
    service: Annotated[FaceService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    file: Annotated[UploadFile, File()],
) -> PackImported:
    """Take in a facial fingerprints file: the people it names are held with their faces, and the
    pass after it places each by face (`FaceService.recognize_from_fingerprints`). Nothing is
    asked here. **The same file twice holds nothing new**, and a later edition replaces the
    earlier. Read into memory whole, bounded by the request cap. **Taken in with recognition
    switched off**: holding fingerprints measures nobody's face, and the answer says so
    (`recognizing`).
    """
    recognizing = await service.enabled()
    try:
        outcome = await service.import_pack(await file.read(), while_off=True)
    except WeightError as error:
        # The model has to be there to read a pack against; said in words.
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except PackError as error:
        # The pack's own refusal, written to be shown, naming nothing from inside the file but
        # its model.
        log.info("faces.pack.rejected", reason="pack")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    except (ValueError, KeyError, OSError) as error:
        # Somebody else's file, or a truncated download. Refused whole: nothing is half-imported,
        # and the message does not quote the file back, which is somebody else's data.
        log.info("faces.pack.rejected", reason=type(error).__name__)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, NOT_ONE) from error
    # Once, through the queue: the pass places what the file holds and matches the library again.
    if outcome.added and recognizing:
        await ask_for_fingerprints(queue, delay=0)
    return PackImported(added=outcome.added, people=len(outcome.held), recognizing=recognizing)


@router.get("/faces/fingerprints/offers")
async def fingerprint_offers(
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FingerprintOffers:
    """The groups that look like somebody a facial fingerprints file holds, while making people
    from fingerprints is off: one group per person, closest first. Keyed by the group, so a screen
    puts the question only on a group it already draws for this viewer; nothing here counts or
    names a file."""
    if not await service.enabled():
        raise _off()
    return FingerprintOffers(
        items=[
            FingerprintOfferView(
                entry_id=one.entry_id,
                name=one.name,
                pile_id=one.pile_id,
                faces=one.faces,
                confirmed=one.confirmed,
                source=one.source,
            )
            for one in await service.fingerprint_offers()
        ]
    )


@router.get("/faces/fingerprints/waiting")
async def waiting_fingerprints(
    service: Annotated[FaceService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> WaitingFingerprints:
    """Everybody a facial fingerprints file or a folder brought whom no face here matches yet,
    newest first, with how many faces, where each came from and whether an export carries them.
    Admin-only, as the pane is; read with recognition off too, since what is held stays held."""
    carried: set[str] = set()
    alone = 0
    if await service.enabled():
        _, entries, alone = await _carried(service, access, database, viewer, [], [])
        carried = set(entries)
    return WaitingFingerprints(
        items=[
            WaitingEntry.model_validate({**one, "exportable": one["entry_id"] in carried})
            for one in await service.waiting()
        ],
        exportable=alone,
    )


@router.delete(
    "/faces/fingerprints/waiting/{entry_id}",
    dependencies=[Depends(csrf_protect)],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_waiting_fingerprints(
    entry_id: str,
    service: Annotated[FaceService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Forget one waiting entry and the faces it brought, with its line in History."""
    if not await service.remove_waiting(entry_id, by=viewer.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "that person is no longer waiting")


@router.post("/faces/fingerprints/{entry_id}/person", dependencies=[Depends(csrf_protect)])
async def make_person_from_fingerprints(
    entry_id: str,
    service: Annotated[FaceService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> MadeFromFingerprints:
    """Yes to a group's question: make the person, with the file's faces as her references, and
    match the library again so the faces that look like her are named. Undone from History."""
    if not await service.enabled():
        raise _off()
    person_id = await service.make_person_from_entry(entry_id, by=viewer.id)
    if person_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "that person is no longer waiting")
    await ask_for_rematching(queue, delay=0)
    return MadeFromFingerprints(person_id=person_id)


async def _carried(
    service: FaceService,
    access: Repository,
    database: Database,
    viewer: Viewer,
    person_ids: list[str],
    entry_ids: list[str],
) -> tuple[list[str], list[str], int]:
    """The People and waiting entries a facial fingerprints file carries for this viewer (none
    named is everybody), and how many entries go as people of their own.

    Held to the People wall and to what a swap refuses (kept local, Do not swap); an entry named as
    somebody refused stays too, since it would carry their name out.
    """
    everyone = not person_ids and not entry_ids
    asked = await service.shareable_people() if everyone else list(dict.fromkeys(person_ids))
    held = await service.held_for_export()
    if not everyone:
        wanted = set(entry_ids)
        held = [one for one in held if one.entry_id in wanted]
    named = await service.people_named([one.name for one in held])
    namesakes = [person for ids in named.values() for person in ids]
    shown = await access.visible_people(viewer, [*asked, *namesakes])
    refused = {one for one in shown if await refused_over(database, "swap", "person", one)}
    people = [one for one in asked if one in shown and one not in refused]
    going = {shown[one].name.casefold() for one in people}
    entries: list[str] = []
    alone = 0
    for one in held:
        same = named.get(one.name.casefold(), [])
        if any(person not in shown or person in refused for person in same):
            continue
        entries.append(one.entry_id)
        alone += one.name.casefold() not in going
    return people, entries, alone


@router.post("/faces/packs/export", dependencies=[Depends(csrf_protect)])
async def export_pack(
    body: PackExportRequest,
    service: Annotated[FaceService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Build a facial fingerprints file from People here and the people waiting for a matching
    face, and hand it back. Loads no model: the numbers are stored. Held to the People wall and to
    what a swap refuses (`_carried`); nobody to carry is refused in words.
    """
    if not await service.enabled():
        raise _off()
    people, entries, _ = await _carried(
        service, access, database, viewer, body.person_ids, body.entry_ids
    )
    if not people and not entries:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "There's nobody here whose facial fingerprints to share."
        )
    raw = await service.export_pack(
        name=body.name,
        version=body.version,
        person_ids=people,
        entry_ids=entries,
        include_pictures=body.include_pictures,
    )
    log.info("faces.pack.exported", people=len(people), waiting=len(entries), bytes=len(raw))
    return Response(
        content=raw,
        media_type="application/zip",
        headers={"content-disposition": f'attachment; filename="{_pack_filename(body.name)}"'},
    )


@router.post("/faces/forget", dependencies=[Depends(csrf_protect)])
async def forget_faces(
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FetchStarted:
    """Delete every face, every reference and every picture behind them. Hands back the job.

    Separate from the switch, which keeps what was collected; not gated on it, since the commonest
    moment to press this is straight after switching it off. A job, because on a large library it
    is minutes of batched writes (`forget_all`) that nothing else may wait on.
    """
    job_id = await queue.enqueue(
        FACE_FORGET, {}, dedupe=True, priority=WAITED_ON_PRIORITY, requested_by=viewer.id
    )
    return FetchStarted(job_id=job_id)
