# SPDX-License-Identifier: AGPL-3.0-or-later
"""The backup endpoints. All three are admin-only, and the server is what says so.

`require_admin` sits on each of them, so a guest is refused before anything is read. There is no
existence to leak by refusing early here: none of these routes names something the caller chose,
they act on the install as a whole, and the honest answer to a guest asking about the install as a
whole is no. Hiding the Backup section in the client is a courtesy; these three checks are the
control.
"""

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
    moment_in_name,
)

router = APIRouter(prefix="/backup", tags=["backup"])

#: How much of an uploaded file is read at a time on its way to disk. A backup is a database and
#: can be tens of megabytes; reading it into memory in one piece is how an admin-only route still
#: becomes a way to exhaust the machine.
_CHUNK = 1024 * 1024


def _service(request: Request) -> BackupService:
    return part_of(request, SERVICE)


def _refusal(error: BackupError) -> HTTPException:
    """Each refusal with its own status and its own sentence.

    422 for a file that is not a backup or is from a newer Sift: the request was well-formed and
    what it carried is not something this build can act on. 409 for a destination folder, which is
    a fact about the disk rather than about the request. Neither is a 403: the caller got through
    the door, and telling them otherwise would send them off to check their sign-in.
    """
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
    """Take a backup now, into the backup folder, and say where it went.

    The same folder the automatic backups go to, NAMED AS SAVED BY HAND (`SAVED_MARK`), so no
    rule ever deletes it; the pane lists it with the other backups no rule takes. The answer is
    where it is, rather than the file: the desktop app on the computer running Sift opens that
    folder, and a browser on another device asks for a copy by its name (`copy_of`) when the
    person wants one there too.

    A press that only handed the file to the browser would keep nothing on the computer running
    Sift, and the backup folder is where a person looks for the backup they made.
    """
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
    """A copy of one backup the pane lists, handed to the browser to save.

    For a browser on another device, which cannot open a folder on the computer running Sift.
    Only a name the list shows is handed out (`BackupService.left_alone_file`); anything else is a
    404, so this route cannot hand out some other file in the folder.
    """
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
    """What the next backup would hold beside the database, with sizes, so the one sentence on
    the screen that says what the file holds is read from the disk rather than written once."""
    listed = await _service(request).contents()
    return ContentsView(parts=[ContentPart(**one) for one in listed])


@router.post("/restore", response_model=RestoreResult, dependencies=[Depends(csrf_protect)])
async def restore(
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    file: Annotated[UploadFile, File()],
) -> RestoreResult:
    """Put a backup back, having proved first that it is one and that this Sift can read it.

    Everything that can refuse refuses before the live database is touched, so a rejected file
    leaves the install exactly as it was. A backup from a newer Sift is refused rather than forced:
    carrying a schema backwards would destroy the copy it was being restored from.

    Afterwards the library's records are the backup's, and the media is whatever is on the disks,
    which is the whole design. Re-scan the folders and the thumbnails, previews and search index
    are built again from the files.
    """
    service = _service(request)
    staged = await service.staged()
    try:
        # A backup is gigabytes and this is a request. Opening it and every write of it go to a
        # thread, so uploading one does not stop the rest of the application for its length.
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
    """The automatic-backup settings as they stand.

    The folder is reported as it was saved rather than as it resolves, because that is what the
    screen has to put back in the field. Whether it is Sift's own directory is reported separately,
    so the screen can say that a backup living beside the database is not much of a backup.
    """
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
    )


@router.put("/schedule", response_model=ScheduleView, dependencies=[Depends(csrf_protect)])
async def update_schedule(
    body: ScheduleUpdate,
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
) -> ScheduleView:
    """Save the schedule, and queue the next backup if there is one to queue.

    The folder is checked here rather than only when the job runs, so a folder that is read-only or
    gone is a sentence on the screen now instead of a failure in the job log tonight. It is checked
    again at run time regardless: a mount can go away between the two.
    """
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
    # Read back rather than echoed: a field left out kept what was stored, and the answer says it.
    return await _schedule_view(service)


@router.get("/unmarked", response_model=UnmarkedBackupsView)
async def unmarked(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> UnmarkedBackupsView:
    """The backups in the backup folder whose names carry no library's mark.

    No rule deletes them, so they are why a folder holds more than the number kept; listed with
    their sizes so a person can delete them by hand.
    """
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
    """Delete one unmarked backup, by the name the list showed, and answer the list as it stands.

    Only a name the list holds is deleted (`BackupService.delete_unmarked`); anything else is a 404,
    so this route cannot delete some other file in the folder.
    """
    service = _service(request)
    try:
        await service.delete_unmarked(name, admin)
    except BackupError as error:
        raise _refusal(error) from error
    return await _unmarked_view(service)
