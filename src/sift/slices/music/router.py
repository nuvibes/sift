# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file's same-music group, and the AcoustID lookup's key, presses and settings."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import Field

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.wire import Wire
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, current_viewer, master_key, require_admin
from sift.slices.music.lookup import (
    ASK_AGAIN_AFTER_DAYS,
    LOOKUP,
    LOOKUP_STARTER,
    NOTHING_TO_ASK_AGAIN,
    LookupNotReady,
    LookupSettings,
    LookupStarter,
)
from sift.slices.music.models import SameMusic, SameMusicFile
from sift.slices.music.service import SERVICE, MusicService

router = APIRouter(tags=["music"])


def _service(request: Request) -> MusicService:
    return part_of(request, SERVICE)


@router.get("/assets/{asset_id}/same-music")
async def same_music(
    asset_id: str,
    service: Annotated[MusicService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> SameMusic:
    """Files sharing a song with this one that the viewer may see, closest first; none if hidden."""
    if not await access.can_view(viewer, asset_id):
        return SameMusic()
    ids = await service.same_music_of(viewer.id, asset_id, reveal=viewer.show_hidden)
    if not ids:
        return SameMusic()
    views = await access.assets_of(viewer, ids)
    files = [
        SameMusicFile(
            id=view.asset.id,
            media_type=view.asset.media_type,
            width=view.asset.width,
            height=view.asset.height,
            duration_ms=view.asset.duration_ms,
            art=view.art_version,
            name=view.asset.original_filename,
        )
        # In the group's order, not the read's.
        for view in (views.get(one) for one in ids)
        if view is not None
    ]
    return SameMusic(files=files, count=len(files))


# The key is sealed and never read back; library lookups are the lookup task's Run now.


#: The word an AcoustID enrichment is filed under, and the name the Enrich menu shows.
ACOUSTID_SOURCE = "acoustid"
ACOUSTID_LABEL = "AcoustID"

#: A selection, never a library (see `MAX_RUN_FILES`).
MAX_LOOKUP_FILES = 500


class LookupState(Wire):
    """What Settings and the Enrich menu read about the lookup; never the key itself."""

    on: bool
    key_set: bool
    key_ready: bool
    route: str | None
    source: str = ACOUSTID_SOURCE
    label: str = ACOUSTID_LABEL
    ready: bool = False
    #: Files a lookup press would ask about now; zero while off or keyless.
    owed: int = 0
    #: Files AcoustID did not know, never counted in `owed`.
    not_known: int = 0
    #: Of those, how many Ask again would ask now; zero while off or keyless.
    ask_again: int = 0
    ask_again_after_days: int = ASK_AGAIN_AFTER_DAYS


class LookupFiles(Wire):
    """AcoustID asked about these files; `again` asks only ones it did not know."""

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_LOOKUP_FILES)
    again: bool = False


class LookupPressed(Wire):
    #: The press's row on Activity, or None when no file wanted one.
    job_id: str | None = None
    queued: int = 0
    #: Files left out, or not the user's to act on; counted, never named.
    left_out: int = 0
    said: str


class LookupKeyBody(Wire):
    key: str = Field(min_length=1, max_length=200)


class LookupChecked(Wire):
    ok: bool
    said: str


def _lookup(request: Request) -> LookupSettings:
    return part_of(request, LOOKUP)


def _starter(request: Request) -> LookupStarter:
    return part_of(request, LOOKUP_STARTER)


async def _state(lookup: LookupSettings, starter: LookupStarter, key: bytes | None) -> LookupState:
    """The settings and the count beside the press."""
    again = await starter.plan_again()
    return LookupState.model_validate(
        {
            **await lookup.state(key),
            "owed": await starter.owed(),
            "not_known": again.not_known,
            "ask_again": again.files,
            "ready": await starter.cannot_run() is None,
        }
    )


@router.get("/music/lookup")
async def lookup_state(
    lookup: Annotated[LookupSettings, Depends(_lookup)],
    starter: Annotated[LookupStarter, Depends(_starter)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    key: Annotated[bytes | None, Depends(master_key)],
) -> LookupState:
    """Whether the lookup is on, its key and route, and how many files are still owed a lookup."""
    return await _state(lookup, starter, key)


def _files(count: int) -> str:
    return "1 file" if count == 1 else f"{count:,} files"


@router.post("/music/lookup/files", dependencies=[Depends(csrf_protect)])
async def look_up_files(
    body: LookupFiles,
    starter: Annotated[LookupStarter, Depends(_starter)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> LookupPressed:
    """Enrich files with AcoustID now; files this user cannot act on are counted, never sent."""
    actionable = await access.actionable_of(viewer, body.asset_ids)
    allowed = list(actionable.allowed)
    try:
        job_id, queued = await starter.start_for_files(
            allowed, requested_by=viewer.id, again=body.again
        )
    except LookupNotReady as refused:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refused)) from refused
    left = len(dict.fromkeys(body.asset_ids)) - queued
    if body.again:
        said = (
            f"Asking AcoustID again about {_files(queued)}."
            if queued
            else "AcoustID knew this file or was never asked about it."
        )
    elif queued:
        said = f"Looking up {_files(queued)} on AcoustID."
        if left:
            said += (
                f" Left out {_files(left)} that have a song already, were asked before, or have"
                " no music to send."
            )
    else:
        said = "No file here needs looking up on AcoustID."
    return LookupPressed(job_id=job_id, queued=queued, left_out=left, said=said)


@router.post("/music/lookup/again", dependencies=[Depends(csrf_protect)])
async def ask_again(
    starter: Annotated[LookupStarter, Depends(_starter)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> LookupPressed:
    """Ask AcoustID again about files it did not know, past `ASK_AGAIN_AFTER_DAYS`. Admin-only."""
    try:
        job_id, queued = await starter.start_again(requested_by=viewer.id)
    except LookupNotReady as refused:
        raise HTTPException(status.HTTP_409_CONFLICT, str(refused)) from refused
    said = f"Asking AcoustID again about {_files(queued)}." if queued else NOTHING_TO_ASK_AGAIN
    return LookupPressed(job_id=job_id, queued=queued, said=said)


@router.put("/music/lookup/key", dependencies=[Depends(csrf_protect)])
async def set_lookup_key(
    body: LookupKeyBody,
    lookup: Annotated[LookupSettings, Depends(_lookup)],
    starter: Annotated[LookupStarter, Depends(_starter)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    key: Annotated[bytes | None, Depends(master_key)],
) -> LookupState:
    """Seal an AcoustID application key. Refused while the saved keys are locked."""
    if key is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Your saved keys are locked. Enter your password in the box at the top of this page to "
            "unlock them, then try again.",
        )
    if not body.key.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter an AcoustID key.")
    await lookup.set_key(body.key, key)
    return await _state(lookup, starter, key)


@router.delete("/music/lookup/key", dependencies=[Depends(csrf_protect)])
async def remove_lookup_key(
    lookup: Annotated[LookupSettings, Depends(_lookup)],
    starter: Annotated[LookupStarter, Depends(_starter)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    key: Annotated[bytes | None, Depends(master_key)],
) -> LookupState:
    """Remove the key. Nothing is looked up again until one is entered."""
    await lookup.forget_key()
    return await _state(lookup, starter, key)


@router.post("/music/lookup/check", dependencies=[Depends(csrf_protect)])
async def check_lookup(
    lookup: Annotated[LookupSettings, Depends(_lookup)],
    viewer: Annotated[Viewer, Depends(require_admin)],
    key: Annotated[bytes | None, Depends(master_key)],
) -> LookupChecked:
    """Prove the key with AcoustID's own example via the set route; sends nothing of the library."""
    ok, said = await lookup.check(key)
    return LookupChecked(ok=ok, said=said)
