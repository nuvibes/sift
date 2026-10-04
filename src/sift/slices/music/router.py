# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file's same-music group, and the online lookup's settings.

Admin-only, like every other press that decides what the whole library does with the machine's
time. Starting the read has no route here at all: both of the card's answers post the music
task's own run, which is the rule a board choice follows: a card may not invent a second way to
do what a page already does. See `slices/music/queue.py`.
"""

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


# --- which files share a song -------------------------------------------------------------------


@router.get("/assets/{asset_id}/same-music")
async def same_music(
    asset_id: str,
    service: Annotated[MusicService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> SameMusic:
    """The files sharing a song with this one, closest first: the file page's Same music strip.

    **Anybody signed in**, like the lookalikes (`/assets/{asset_id}/similar`, whose rules this
    copies): it is a way of browsing, not a control over the install.

    A file this viewer may not see answers exactly as a file that does not exist (an EMPTY group,
    not a refusal) and the answer is given before the group is read at all, so nothing about a
    concealed file's partners is worked out on its behalf. That is the lookalikes' rule, and it is
    written there with the reason: a reply that differed would be a way to ask whether a file
    exists, and a group read for a file the viewer cannot have would describe it out of files they
    can.

    The group itself is scoped by the stored verdict (`MusicStore.same_music_of`: every file on
    the path, the one reached and the one it was reached through), with a concealed file only
    while this viewer's Hidden is open: the rule the lookalikes apply. And the ids it names are
    then handed to the ordinary read (`assets_of`), so what is drawn is what that read says this
    viewer may see, in the one statement every screen uses.
    """
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
        # In the group's order, closest first, rather than the read's.
        for view in (views.get(one) for one in ids)
        if view is not None
    ]
    return SameMusic(files=files, count=len(files))


# --- SONG-NAMES: the online lookup's key and its check --------------------------------------------
#
# Admin, CSRF on the writes, like a stash-box's key: the key is sealed under the master key of the
# admin who enters it, never read back, and never shown. The switch and the route are ordinary
# settings through the hub (`slices/music/settings.py`); these are the two things the hub cannot be:
# a value that must never be read back, and a request that goes out to prove it.
#
# Asking AcoustID about the library has no route here: it is the lookup task's Run now, posted to
# the tasks' own run route like every task's (`lookup.LOOKUP_TASK`). One press, one door.


#: AcoustID AS AN ENRICHMENT SOURCE: the word a file's enrichment is filed under (the `enriched:`
#: filter's `acoustid`, the Enriched by column's row, `song_files.source`) and the name the Enrich
#: menu says it by, beside the stash-boxes. Published on `LookupState` so the menu reads one answer
#: for whether to offer it and what to call it.
ACOUSTID_SOURCE = "acoustid"
ACOUSTID_LABEL = "AcoustID"

#: How many files one Enrich press may name: a selection, never a library. See `MAX_RUN_FILES`.
MAX_LOOKUP_FILES = 500


class LookupState(Wire):
    """What Settings draws for the lookup. Never the key: whether one is set, and whether it can be
    opened now (a key set before a restart is locked until somebody signs in again).

    And what the Enrich menu reads to offer AcoustID beside the stash-boxes: `source` and `label`
    are its word and its name, and `ready` whether a press would send anything (the switch on and a
    key saved), the same answer a press is refused by."""

    on: bool
    key_set: bool
    key_ready: bool
    route: str | None
    source: str = ACOUSTID_SOURCE
    label: str = ACOUSTID_LABEL
    ready: bool = False
    #: How many files have a fingerprint, no song and no answer yet, and could be looked up by a
    #: press of the lookup task on Settings > Tasks (`LookupStarter.owed`). Nought while the lookup
    #: is off or has no key.
    owed: int = 0
    #: How many files AcoustID was asked about and did not know (`LookupStarter.not_known`): asked
    #: and answered, so never asked again by the lookup's own press and never counted in `owed`.
    not_known: int = 0
    #: How many of those a press of Ask again would ask about now (`LookupStarter.asks_again`):
    #: last asked more than `ask_again_after_days` ago and still with no song. Nought while the
    #: lookup is off or has no key.
    ask_again: int = 0
    #: How old an answer of "not known" must be before Ask again asks about its file again.
    ask_again_after_days: int = ASK_AGAIN_AFTER_DAYS


class LookupFiles(Wire):
    """A press of Enrich on a file, a selection or a folder's files: AcoustID asked about these.
    `again` is a file's own Ask again: only the ones AcoustID did not know, whatever the age of
    that answer."""

    asset_ids: list[str] = Field(min_length=1, max_length=MAX_LOOKUP_FILES)
    again: bool = False


class LookupPressed(Wire):
    """What an Enrich press did, counted, and the sentence the screen says about it."""

    #: The press's own row on Activity (one walk, a lookup under it per file), or None where no
    #: file wanted one.
    job_id: str | None = None
    #: The files AcoustID will be asked about.
    queued: int = 0
    #: The files left out: a file that carries a song already, is shorter than a song, is kept
    #: local, has no music fingerprint yet, or was asked and answered; and a file this user may not
    #: act on. Counted, never named.
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
    """What every one of these routes answers with: the settings, and the count beside the press."""
    return LookupState.model_validate(
        {
            **await lookup.state(key),
            "owed": await starter.owed(),
            "not_known": await starter.not_known(),
            "ask_again": await starter.asks_again(),
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
    """Whether the lookup is on, whether a key is set and ready, the route it goes through, and
    how many files are still owed a lookup."""
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
    """Enrich some files with AcoustID: the lookup task pressed for THESE files.

    AcoustID as a choice of Enrich on a file's menu, a selection's bar and a folder (whose files
    the screen sends), beside the stash-boxes. One press of the lookup task, queued as its own walk with a
    lookup per file under it (`LookupStarter.start_for_files`), so it runs now whatever the task's
    When says, and Activity draws it as the press it is.

    Admin-only, as every press that sends something about the library outside the machine is.
    Every file is resolved through what this user may act on first: a file in Hidden while it is
    shut, or one they may not see, is left out and counted, never sent. Refused in words while the
    lookup is off or has no key (409), as the task's own press is. A file AcoustID was asked about
    and did not know is not asked again by this press: asking again is a choice nothing here makes.
    """
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
    """Ask AcoustID again about the files it did not know: one press, queued as a walk of the
    lookup task with a lookup per file under it (`LookupStarter.start_again`), counted before it
    starts. Only the files last asked more than `ASK_AGAIN_AFTER_DAYS` days ago (the reason is
    over the constant). Each file keeps its earlier answer until the new one lands.

    Admin-only and refused in words while the lookup is off or has no key (409), as every press
    that sends something outside the machine is. A file in Hidden is asked about as every walk of
    the lookup task asks about it: nothing of a file is shown or counted to anybody by this press.
    """
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
    """Prove the key with one lookup of AcoustID's own documented example (nothing of the library
    is sent) through the route that is set."""
    ok, said = await lookup.check(key)
    return LookupChecked(ok=ok, said=said)
