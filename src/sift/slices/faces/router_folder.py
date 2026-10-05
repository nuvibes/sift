# SPDX-License-Identifier: AGPL-3.0-or-later
"""Importing a folder of reference pictures, one folder per person."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)
from starlette.datastructures import UploadFile as StarletteUpload
from starlette.exceptions import HTTPException as StarletteHTTPException

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.content import LibraryError, LibraryStore, resolve_directory
from sift.kernel.db import Database
from sift.kernel.jobs import JobQueue
from sift.kernel.wire import Wire
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.faces import folder_import
from sift.slices.faces.folder_import import FACE_FOLDER_IMPORT, worded
from sift.slices.faces.models import Finding
from sift.slices.faces.models_http import FolderByPath, FolderImportStarted
from sift.slices.faces.router_common import (
    _off,
    _service,
    log,
)
from sift.slices.faces.service import (
    FaceService,
)
from sift.slices.faces.store_left_out import LeftOutStore

router = APIRouter(tags=["faces"])


def _stage(destination: Path, body: bytes) -> None:
    """Put one uploaded file into the scratch tree, making the folders above it."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)


#: The folder import's body, described by hand because the route reads it itself.
_FOLDER_BODY = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["files"],
                "properties": {
                    "files": {"type": "array", "items": {"type": "string", "format": "binary"}},
                },
            }
        }
    },
}


@dataclass(frozen=True, slots=True)
class FolderUpload:
    """What a folder import sent: every file with the path it had."""

    files: list[StarletteUpload]


async def _read_folder_form(request: Request) -> AsyncIterator[FolderUpload]:
    """The files of a folder import, read under this route's own caps.

    Read here rather than declared as parameters: the framework parses a declared form with the
    parser's default of a thousand files, which refuses any real gallery before the route runs, and
    it has no way to be told otherwise. One file over the cap is let through the parser so that the
    route's own check answers it, in words that say to import in parts. The form is closed once the
    answer has gone, which removes the spooled copies of every file.
    """
    try:
        form = await request.form(max_files=folder_import.MAX_FOLDER_FILES + 1, max_fields=8)
    except StarletteHTTPException as error:
        if "too many files" in str(error.detail).casefold():
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                _too_many_files(),
            ) from error
        raise
    try:
        files = [one for one in form.getlist("files") if isinstance(one, StarletteUpload)]
        if not files:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "choose a folder to import")
        yield FolderUpload(files=files)
    finally:
        await form.close()


def _safe_relative(name: str) -> PurePosixPath | None:
    """The path inside the chosen folder that this upload claims, or None if it claims nothing safe.

    A browser sends the path a file had on the machine it came from, and that string arrives from
    the client, so it is a request, not a fact. Absolute paths, `..` in any position and drive
    letters are refused outright rather than sanitized, because a name that had to be repaired is a
    name whose author was not describing a file inside the folder they chose.

    Backslashes become separators first: the same folder chosen on Windows arrives with them, and
    treating one as an ordinary character would turn a nested path into a single very odd filename.

    Worth being precise about what the path type does before any of this runs, so the checks are not
    read as doing more than they do: it absorbs a doubled separator and a leading `./` on its own,
    and both name the same file inside the folder anyway. What is left for the checks below is the
    part that would actually escape.
    """
    cleaned = PurePosixPath(name.replace("\\", "/"))
    if cleaned.is_absolute() or not cleaned.parts:
        return None
    if any(part in {"", ".", ".."} for part in cleaned.parts):
        return None
    if any(":" in part for part in cleaned.parts):
        return None
    return cleaned


def _drop_the_chosen_folder(paths: Sequence[PurePosixPath]) -> list[PurePosixPath]:
    """Strip the wrapper directory a browser puts in front of everything, when there is exactly one.

    Choosing a folder called `Gallery` sends every file as `Gallery/Ada/one.jpg`, so what arrives is
    one folder containing the people rather than a folder of people. Removing it is what makes the
    thing somebody picked mean what they thought it meant.

    Only when every path shares the same first part AND has something after it. Two top-level names
    means they picked several folders, and stripping one of them would silently merge two people.
    """
    if not paths:
        return []
    first = {path.parts[0] for path in paths}
    if len(first) != 1 or any(len(path.parts) < 3 for path in paths):
        return list(paths)
    return [PurePosixPath(*path.parts[1:]) for path in paths]


def _too_many_files() -> str:
    return f"that's more than {folder_import.MAX_FOLDER_FILES:,} files. Import it in parts"


def _unstage(folder: Path) -> None:
    """Remove a staged upload that never became a task."""
    # A folder this route made under Sift's own cache a moment ago.
    # nosemgrep: sift-no-file-removal-outside-delete-trash
    shutil.rmtree(folder, ignore_errors=True)


async def _queue_import(queue: JobQueue, viewer: Viewer, payload: dict[str, object]) -> str:
    """The task that reads the folder. One attempt: a run that stopped part way says so, rather
    than reading an hour of pictures again on its own."""
    return await queue.enqueue(FACE_FOLDER_IMPORT, payload, requested_by=viewer.id, max_attempts=1)


@router.post(
    "/faces/references/folder/path",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(csrf_protect)],
)
async def import_reference_folder_by_path(
    body: FolderByPath,
    viewer: Annotated[Viewer, Depends(require_admin)],
    service: Annotated[FaceService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    library: Annotated[LibraryStore, Depends(wiring.library)],
) -> FolderImportStarted:
    """Import a folder of people that sits on the machine Sift runs on, read by Sift from its path.

    The folder must be inside one Sift has been given (`Settings > Library` and the folder dialog
    give them): the same confinement the folder picker keeps, so this door reads nothing the picker
    could not show. Answers at once with the task; the folder is read by the task.
    """
    if not await service.enabled():
        raise _off()
    try:
        folder = await asyncio.to_thread(resolve_directory, Path(body.path))
    except LibraryError as refusal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refusal)) from refusal
    covering = [
        grant
        for grant in await library.grants()
        if folder_import.inside(folder, Path(grant.abs_path))
    ]
    if not covering:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Sift hasn't been given that folder. Choose it again to give it to Sift.",
        )
    grant = covering[0]
    within = folder.relative_to(Path(grant.abs_path)).as_posix()
    job_id = await _queue_import(
        queue, viewer, {"grant": grant.id, "within": "" if within == "." else within}
    )
    log.info("faces.folder.queued", door="path")
    return FolderImportStarted(job_id=job_id)


@router.post(
    "/faces/references/folder",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(csrf_protect)],
    openapi_extra={"requestBody": _FOLDER_BODY},
)
async def import_reference_folder(
    # FIRST, before the form: dependencies run in the order they are declared, and a body read
    # ahead of the door answers a locked session or a guest about the body (422) instead of
    # refusing them, and spools their upload to disk before saying no.
    viewer: Annotated[Viewer, Depends(require_admin)],
    upload: Annotated[FolderUpload, Depends(_read_folder_form)],
    service: Annotated[FaceService, Depends(_service)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
) -> FolderImportStarted:
    """Take in a folder of folders sent from a browser on another device: one per person, full of
    pictures of them, the folder a file sat in being the person's name.

    The files are copied under Sift's cache and the same task as the path door reads them, so the
    request ends when the upload does. The copy goes when the task ends.
    """
    if not await service.enabled():
        raise _off()

    files = upload.files
    claimed = [(_safe_relative(one.filename or ""), one) for one in files]
    if any(path is None for path, _ in claimed):
        # Refused whole: a run that dropped files would report a person as thin for Sift's reason.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "one of those files isn't inside the folder you chose"
        )
    if len(claimed) > folder_import.MAX_FOLDER_FILES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, _too_many_files())

    sent_paths = [path for path, _ in claimed if path is not None]
    paths = _drop_the_chosen_folder(sent_paths)
    # The chosen folder's own name, where the browser put one in front of everything: what the
    # people it holds say they came from. None where several were chosen at once.
    named = sent_paths[0].parts[0] if sent_paths and paths != sent_paths else None
    root = Path(
        await asyncio.to_thread(
            tempfile.mkdtemp, prefix=folder_import.STAGED_PREFIX, dir=service.scratch_root()
        )
    )
    try:
        written = 0
        for path, sent in zip(paths, [one for _, one in claimed], strict=True):
            if len(path.parts) < 2:
                # A loose file at the top level belongs to nobody, skipped (a Mac's `.DS_Store`).
                continue
            body = await sent.read()
            written += len(body)
            if written > folder_import.MAX_FOLDER_BYTES:
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    "that folder is larger than Sift will take in one go. Import it in parts",
                )
            # Off the loop: a request that writes a gallery from the loop stops every other
            # request and every stream for the lot.
            await asyncio.to_thread(_stage, root / Path(*path.parts), body)
        job_id = await _queue_import(
            queue, viewer, {"staged": root.name, **({"named": named} if named else {})}
        )
    except BaseException:
        await asyncio.to_thread(_unstage, root)
        raise
    log.info("faces.folder.queued", door="upload")
    return FolderImportStarted(job_id=job_id)


class LeftOutFiles(Wire):
    """The pictures a folder import left out for one reason."""

    reason: str
    #: The words its report counts them in ("facing away").
    words: str
    files: list[str]


class FolderLeftOut(Wire):
    """Which pictures a folder import left out, most common reason first, and the near copies it
    kept. Each path is inside the chosen folder."""

    left_out: list[LeftOutFiles]
    near_copies: list[str]


@router.get("/faces/references/folder/{job_id}/left-out")
async def folder_left_out(
    job_id: str,
    viewer: Annotated[Viewer, Depends(require_admin)],
    database: Annotated[Database, Depends(wiring.database)],
) -> FolderLeftOut:
    """The files behind each count of a folder import's report. Only the latest import has any."""
    by_reason: dict[str, list[str]] = {}
    for file, reason in await LeftOutStore(database).of(job_id):
        by_reason.setdefault(reason, []).append(file)
    near = by_reason.pop(Finding.NEAR_DUPLICATE.value, [])
    ordered = sorted(by_reason.items(), key=lambda item: -len(item[1]))
    return FolderLeftOut(
        left_out=[
            LeftOutFiles(reason=reason, words=worded(Finding(reason)), files=files)
            for reason, files in ordered
        ],
        near_copies=near,
    )
