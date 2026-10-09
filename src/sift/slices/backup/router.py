# SPDX-License-Identifier: AGPL-3.0-or-later
"""The backup endpoints, every one admin-only, enforced here on the server."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from sift.kernel.access import Viewer
from sift.kernel.jobs.clock import reschedule
from sift.kernel.settings_registry import SettingError
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.backup.models import (
    ContentPart,
    ContentsView,
    RestoreResult,
    SavedBackupView,
    ScheduleUpdate,
    ScheduleView,
    UnmarkedBackupsView,
    UnmarkedBackupView,
)
from sift.slices.backup.naming import moment_in_name
from sift.slices.backup.service import (
    AT_KEY,
    EVERY_DAYS_KEY,
    FOLDER_KEY,
    KEEP_DAYS_KEY,
    KEEP_KEY,
    SERVICE,
    BackupError,
    BackupService,
    Busy,
    DestinationRefused,
    NotThere,
    keep_days_from,
)

router = APIRouter(prefix="/backup", tags=["backup"])

#: Read in pieces, so an upload can never exhaust the machine's memory.
_CHUNK = 1024 * 1024


def _service(request: Request) -> BackupService:
    return part_of(request, SERVICE)


def _refusal(error: BackupError) -> HTTPException:
    """Each refusal with its own status and sentence: 422 for the file, 409 for the folder."""
    if isinstance(error, (DestinationRefused, Busy)):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    if isinstance(error, NotThere):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error))


@router.post("/export", response_model=SavedBackupView, dependencies=[Depends(csrf_protect)])
async def export_now(
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
) -> SavedBackupView:
    """Take a backup now into the backup folder, kept as saved by hand, and say where it went."""
    service = _service(request)
    try:
        saved = await service.save_now(admin)
    except BackupError as error:
        raise _refusal(error) from error
    return SavedBackupView(
        name=saved.name,
        folder=str(saved.parent),
        path=str(saved),
        taken_at=int(moment_in_name(saved.name) or service.now()),
        size_bytes=(await asyncio.to_thread(saved.stat)).st_size,
    )


@router.get("/saved/{name}", response_class=FileResponse)
async def copy_of(
    name: str,
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> FileResponse:
    """A copy of one listed backup for a browser on another device; any other name is a 404."""
    try:
        found = await _service(request).left_alone_file(name)
    except BackupError as error:
        raise _refusal(error) from error
    kind = "application/zip" if found.suffix == ".zip" else "application/octet-stream"
    return FileResponse(found, media_type=kind, filename=found.name)


@router.get("/contents", response_model=ContentsView)
async def contents(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> ContentsView:
    """What the next backup would hold beside the database, with sizes read from the disk."""
    listed = await _service(request).contents()
    return ContentsView(parts=[ContentPart(**one) for one in listed])


@router.post("/restore", response_model=RestoreResult, dependencies=[Depends(csrf_protect)])
async def restore(
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    file: Annotated[UploadFile, File()],
) -> RestoreResult:
    """Put a backup back, proving first that it is one and that this Sift can read it."""
    service = _service(request)
    staged = await service.staged()
    try:
        # Opened and written on a thread, so an upload of gigabytes stalls nothing else.
        with await asyncio.to_thread(staged.open, "wb") as sink:
            while chunk := await file.read(_CHUNK):
                await asyncio.to_thread(sink.write, chunk)
        manifest = await service.restore(staged, admin)
    except BackupError as error:
        raise _refusal(error) from error
    finally:
        await service.discard(staged)

    return RestoreResult(
        app_version=manifest["app_version"],
        created_at=manifest["created_at"],
        carried=list(manifest["carried"]),
    )


@router.get("/schedule", response_model=ScheduleView)
async def read_schedule(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> ScheduleView:
    """The automatic-backup settings as saved, and whether the folder is Sift's own."""
    return await _schedule_view(_service(request))


async def _schedule_view(service: BackupService) -> ScheduleView:
    folder = str(await service.setting(FOLDER_KEY) or "")
    return ScheduleView(
        every_days=int(await service.setting(EVERY_DAYS_KEY)),
        at=str(await service.setting(AT_KEY)),
        keep=int(await service.setting(KEEP_KEY)),
        keep_days=keep_days_from(await service.setting(KEEP_DAYS_KEY)),
        folder=folder,
        beside_sift_data=not folder.strip(),
        working=service.working,
    )


@router.put("/schedule", response_model=ScheduleView, dependencies=[Depends(csrf_protect)])
async def update_schedule(
    body: ScheduleUpdate,
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
) -> ScheduleView:
    """Save the schedule, checking the folder now as well as at run time."""
    service = _service(request)
    try:
        await service.set_schedule(
            admin,
            keep=body.keep,
            keep_days=body.keep_days,
            folder=body.folder,
            every_days=body.every_days,
            at=body.at,
        )
    except BackupError as error:
        raise _refusal(error) from error
    except SettingError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error

    await reschedule("backup")
    # Read back rather than echoed: a field left out kept what was stored.
    return await _schedule_view(service)


@router.get("/unmarked", response_model=UnmarkedBackupsView)
async def unmarked(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> UnmarkedBackupsView:
    """The backups whose names carry no library's mark, which no rule deletes, with sizes."""
    return await _unmarked_view(_service(request))


async def _unmarked_view(service: BackupService) -> UnmarkedBackupsView:
    return UnmarkedBackupsView(
        backups=[
            UnmarkedBackupView(
                name=one.name, taken_at=one.taken_at, size_bytes=one.size_bytes, saved=one.saved
            )
            for one in await service.unmarked()
        ],
        recycle_bin=await service.recycles(),
    )


@router.delete(
    "/unmarked/{name}",
    response_model=UnmarkedBackupsView,
    dependencies=[Depends(csrf_protect)],
)
async def delete_unmarked(
    name: str,
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
) -> UnmarkedBackupsView:
    """Delete one unmarked backup by the name the list showed, and answer the list as it stands."""
    service = _service(request)
    try:
        await service.delete_unmarked(name, admin)
    except BackupError as error:
        raise _refusal(error) from error
    return await _unmarked_view(service)
