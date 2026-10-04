# SPDX-License-Identifier: AGPL-3.0-or-later
"""The library endpoints. Every one is admin-only, and every one that changes anything is CSRF-guarded.

They act on the install as a whole (which database the server runs on), so a guest is refused
before anything is read, exactly as the backup routes refuse one. None of them takes a path: a
library is named by the id the list handed out, and a new one by a name the server checks is one
folder name. Hiding the section in the client is a courtesy; these checks are the control.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)

from sift.kernel.access import Viewer
from sift.kernel.machine_acts import record_act
from sift.kernel.wiring import DATABASE, QUEUE, part_of
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.backup.libraries import (
    LIBRARIES,
    MAX_NAME,
    LibrariesService,
    LibraryError,
)
from sift.slices.backup.models import (
    DeleteLibrary,
    DuplicateLibrary,
    DuplicatePlan,
    DuplicateStarted,
    ForgetLibrary,
    LibrariesView,
    LibraryEntry,
    NewLibrary,
    OpenLibrary,
    OpensAtStart,
    SwitchView,
)
from sift.slices.backup.service import SERVICE as BACKUP
from sift.slices.backup.service import BackupError, Busy

router = APIRouter(prefix="/libraries", tags=["libraries"])

#: How much of an upload is read at a time, as the backup restore reads one.
_CHUNK = 1024 * 1024


def _service(request: Request) -> LibrariesService:
    return part_of(request, LIBRARIES)


def _refusal(error: LibraryError | BackupError) -> HTTPException:
    """Each refusal with its own status and its own sentence. A backup refusal is a 422, as there,
    except work already running on the library, which is a 409: a fact about now, not the file."""
    if isinstance(error, LibraryError):
        code = error.status
    elif isinstance(error, Busy):
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_422_UNPROCESSABLE_ENTITY
    return HTTPException(code, str(error))


@router.get("", response_model=LibrariesView)
async def list_libraries(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> LibrariesView:
    """Every library the server knows of: the running one, the ones in its libraries folder, and
    the ones kept elsewhere that it has switched away from."""
    service = _service(request)
    return LibrariesView(
        folder=str(service.folder),
        can_switch=service.can_switch(),
        libraries=[LibraryEntry(**one) for one in await service.listed()],
    )


async def _the_list(service: LibrariesService) -> LibrariesView:
    """The list as the page reads it, answered by every write that changes it."""
    return LibrariesView(
        folder=str(service.folder),
        can_switch=service.can_switch(),
        libraries=[LibraryEntry(**one) for one in await service.listed()],
    )


@router.put("/opens-at-start", response_model=LibrariesView, dependencies=[Depends(csrf_protect)])
async def choose_opening(
    body: OpensAtStart,
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> LibrariesView:
    """Say which library opens when Sift starts, or null for whichever was open last."""
    service = _service(request)
    try:
        await service.choose_opening(body.library)
    except (LibraryError, BackupError) as error:
        raise _refusal(error) from error
    return await _the_list(service)


@router.post("/delete", response_model=LibrariesView, dependencies=[Depends(csrf_protect)])
async def delete_library(
    body: DeleteLibrary,
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> LibrariesView:
    """Move a library in the libraries folder to the Recycle Bin, its name typed to confirm it.

    Never the open library, never one kept in a folder of its own, and never the media files a
    library points at: those are not in its folder.
    """
    service = _service(request)
    try:
        await service.delete(body.library, body.name)
    except (LibraryError, BackupError) as error:
        raise _refusal(error) from error
    return await _the_list(service)


@router.post("/forget", response_model=LibrariesView, dependencies=[Depends(csrf_protect)])
async def forget_library(
    body: ForgetLibrary,
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> LibrariesView:
    """Take a library kept elsewhere off the list. Its folder is left exactly as it is."""
    service = _service(request)
    try:
        await service.forget(body.library)
    except (LibraryError, BackupError) as error:
        raise _refusal(error) from error
    return await _the_list(service)


@router.post(
    "",
    response_model=SwitchView,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(csrf_protect)],
)
async def create_library(
    body: NewLibrary,
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
) -> SwitchView:
    """Make an empty library in the libraries folder and start Sift on it.

    202: the switch is ARRANGED, not done: the server finishes what it is serving, this reply
    included, and then stops to be started on the new library. The screen waits for a different
    run of the server to answer, as the restart on the Performance screen does.
    """
    try:
        made = await _service(request).create(body.name, admin)
    except (LibraryError, BackupError) as error:
        raise _refusal(error) from error
    return SwitchView(switching=True, library=made.id)


@router.post(
    "/open",
    response_model=SwitchView,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(csrf_protect)],
)
async def open_library(
    body: OpenLibrary,
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
    device: Annotated[str | None, Query(max_length=256)] = None,
) -> SwitchView:
    """Start Sift on another library from the list, having checked first that it can be opened.

    Opening another library changes what the computer running Sift serves, so a switch it takes
    writes the same History line as one asked of the app there (`machine_acts.record_act`): who
    asked, the library by its name on the list, and the device the window runs on (`device`, sent
    by the Sift app; a browser sends none). Written into the library being left, while this request
    still holds it: the switch happens once the reply is out. Opening the running one writes
    nothing, because nothing changed.
    """
    service = _service(request)
    try:
        called = await service.name_of(body.library)
        switching = await service.open(body.library, upgrade=body.upgrade)
    except (LibraryError, BackupError) as error:
        raise _refusal(error) from error
    if switching:
        named = {"library": called} if called else {}
        await record_act(part_of(request, DATABASE), admin, "library_opened", device, **named)
    return SwitchView(switching=switching, library=body.library)


@router.post(
    "/import",
    response_model=SwitchView,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(csrf_protect)],
)
async def import_library(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
    file: Annotated[UploadFile, File()],
    name: Annotated[str, Form(min_length=1, max_length=MAX_NAME)],
) -> SwitchView:
    """Make a NEW library from an uploaded backup or Sift database, and start Sift on it.

    The browser's form of "choose a database file": a page cannot hand the server a path, so it
    hands it the file. Staged where Restore stages one and read in chunks the same way; the live
    library is never touched, because the file becomes a library of its own.
    """
    service = _service(request)
    backup = part_of(request, BACKUP)
    staged = await backup.staged()
    try:
        with await asyncio.to_thread(staged.open, "wb") as sink:
            while chunk := await file.read(_CHUNK):
                await asyncio.to_thread(sink.write, chunk)
        made = await service.import_file(staged, name, chosen=file.filename)
    except (LibraryError, BackupError) as error:
        raise _refusal(error) from error
    finally:
        await backup.discard(staged)
    return SwitchView(switching=True, library=made.id)


@router.get("/duplicate", response_model=DuplicatePlan)
async def duplicate_plan(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> DuplicatePlan:
    """Where a duplicate of this library would go, how big it is with and without the pictures
    Sift made, the room there is, and what would refuse one right now. Asked when the form opens,
    not with the list: it walks the cache."""
    return DuplicatePlan(**await _service(request).duplicate_plan())


@router.post(
    "/duplicate",
    response_model=DuplicateStarted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(csrf_protect)],
)
async def duplicate_library(
    body: DuplicateLibrary,
    request: Request,
    admin: Annotated[Viewer, Depends(require_admin)],
) -> DuplicateStarted:
    """Make a second library from this one, as a task, and stay on this one.

    202: the copy is QUEUED, and it is minutes of copying: the screen follows the task. Everything
    that can refuse now (the name, room, other work on the library) is refused here with its own
    sentence; the task checks again when it runs.
    """
    try:
        job_id = await _service(request).ask_to_duplicate(
            body.name, pictures=body.pictures, actor=admin, queue=part_of(request, QUEUE)
        )
    except (LibraryError, BackupError) as error:
        raise _refusal(error) from error
    return DuplicateStarted(job_id=job_id)
