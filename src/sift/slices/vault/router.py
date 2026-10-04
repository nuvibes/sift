# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hidden's endpoints: opening it, shutting it, and putting things in.

Three things are decided here rather than anywhere else, and each is easy to get wrong in a way
that looks like a working feature.

**Opening always costs the PIN.** There is no route that sets the unlocked state without one, so
there is no way to arrive at a revealed Hidden by any path except entering it. A session that is
already open still has to send the PIN to reopen it. That looks redundant from the outside and it
is not: showing hidden things is the one act the whole feature exists to make deliberate, and an
unlock that could be inherited from something the person did ten minutes ago is not deliberate.

**Shutting it costs nothing.** Locking needs no proof and is not an error when nothing was open.
Every trigger the screen arms (the idle timer, the tab going away, the panic control) comes
through here, and a close that could fail or that had to check first is a close that would sometimes
not happen at the moment it mattered.

**Opening is every user's own.** What a viewer hid is concealed from that viewer and nobody
else, so opening reveals only what the user asking put out of their own sight: there is nothing
of anybody else's behind the door. The PIN is per user and so is the throttle in front of it, so
one user guessing spends their own budget and reaches their own things.

What is *not* decided here is who may see what. That is the access layer's, applied to every query
in the application; the two writes below hand it the viewer and let it answer, and a refusal from
it becomes the same 404 an id that was never minted would get.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.reach import BulkWriteDone
from sift.kernel.wiring import part_of
from sift.slices.auth import (
    SERVICE,
    AuthService,
    LockedOut,
    csrf_protect,
    current_viewer,
    require_vault_pin,
    session_key,
)
from sift.slices.vault.models import UnlockRequest, VaultMany, VaultState, VaultWrite

router = APIRouter(tags=["vault"])


def _auth(request: Request) -> AuthService:
    return part_of(request, SERVICE)


def _missing() -> HTTPException:
    """The answer for anything the caller may not be shown, whatever the reason.

    The same 404 an id that was never minted would get. Telling "you may not" apart from "there is
    nothing there" is exactly the bit of information concealment exists to withhold, and an id
    outlives being concealed: anybody who saw it before the flag went on still holds one.
    """
    return HTTPException(status.HTTP_404_NOT_FOUND, "not found")


# --- Opening and shutting --------------------------------------------------------------------


@router.get("/vault")
async def vault_state(
    request: Request,
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> VaultState:
    """Whether this browser has the vault open, and whether there is a PIN to open it with.

    Both are asked before anything about the vault is drawn. Without the second the screen would
    offer to hide something and only then discover there is no way to get it back.
    """
    return VaultState(
        unlocked=viewer.show_hidden,
        pin_set=await _auth(request).has_pin(viewer.id),
    )


@router.post("/vault/unlock", dependencies=[Depends(csrf_protect)])
async def unlock(
    body: UnlockRequest,
    request: Request,
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> VaultState:
    """Show this user's hidden things, for this browser, until something locks it again.

    Open to every user, guests included. What comes back is what the caller hid, and nothing
    else: hiding is per user, so there is nothing here that belongs to somebody else and no
    version of this that opens another user's Hidden.

    The PIN is checked by the auth feature, against the same throttle the lock screen uses, so
    guessing is slowed by the same counter: one secret per user, one budget of attempts, and no
    second door with a fresh allowance behind it. The budget is per user too, so one user
    guessing badly cannot lock another out.

    What this does not do is reach the master key. The PIN never wraps it and never derives it: the
    key comes from the password and lives in memory, so a restart leaves none and no PIN can
    conjure one. Opening Hidden after a restart is possible and gets you your hidden files; it does
    not get you the saved logins, and that is the design rather than a gap.
    """
    key = session_key(request)
    if key is None:  # pragma: no cover (current_viewer resolved a session, so the token is there)
        raise _missing()
    try:
        ok = await _auth(request).verify_pin(viewer.id, body.pin)
    except LockedOut as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts, try again later"
        ) from exc
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "incorrect PIN")
    _auth(request).vault_unlocks.unlock(key)
    return VaultState(unlocked=True, pin_set=True)


@router.post(
    "/vault/lock", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(csrf_protect)]
)
async def lock(
    request: Request,
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """Hide them again. Every lock trigger and the panic control land here.

    Deliberately the cheapest route in the application: no proof, no precondition, and the same
    answer whether or not anything was open. This is the direction that fails safe, so nothing is
    allowed to stand between pressing it and it happening.
    """
    key = session_key(request)
    if key is not None:  # pragma: no cover (current_viewer resolved a session)
        _auth(request).vault_unlocks.lock(key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Putting things in -----------------------------------------------------------------------


@router.put(
    "/assets/{asset_id}/vault",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def set_asset_vault(
    asset_id: str,
    body: VaultWrite,
    request: Request,
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """Hide one file, or bring it back, for this user.

    Answered with no body. By the time the answer is written the row it describes may be one the
    caller is no longer allowed to be shown, and describing it anyway would undo the request in the
    act of confirming it.

    Bringing one back is the same request pointed the other way, and it is reachable exactly when
    this user has Hidden open, because that is when the access layer resolves the file at all.
    Nothing here tests the state to decide; the resolution is the test.
    """
    if body.vault:
        await require_vault_pin(request, viewer)
    if not await wiring.access(request).set_asset_vault(viewer, asset_id, vault=body.vault):
        raise _missing()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/assets/vault", dependencies=[Depends(csrf_protect)])
async def set_assets_vault(
    body: VaultMany,
    request: Request,
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> BulkWriteDone:
    """Hide a selection, or bring it back, for this user, in ONE request.

    **One request for the selection, never one per file stopping at the first refusal.** Forty
    files hidden one at a time would be forty round trips, forty write transactions and forty bumps
    of the cache stamp, and one file the caller could not touch would end the run part-way with the
    rest still on the screen. The work is never the cost.

    A file that cannot be resolved is SKIPPED and counted, and the reply says how many and why:
    the same partial-with-a-reason every other bulk write in Sift answers with. The single route
    keeps its flat 404, because over one file there is no half of the answer to give.

    Counts and no ids, which is the same promise the single route makes by answering with no body
    at all: by the time this is written the rows it describes may be ones the caller is no longer
    allowed to be shown, and naming them would undo the request in the act of confirming it. A
    number of files and a reason names nothing.

    The PIN is required to go IN, once for the selection rather than once per file, and is not
    required to come back out, for the reason the single route gives: bringing something back is
    reachable exactly when the access layer resolves it at all, so whoever can do it has already
    proved the PIN.
    """
    if body.vault:
        await require_vault_pin(request, viewer)
    actionable = await wiring.access(request).set_asset_vault_many(
        viewer, body.asset_ids, vault=body.vault
    )
    return BulkWriteDone.after(actionable, len(actionable.allowed))


@router.put(
    "/folders/{folder_id}/vault",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def set_folder_vault(
    folder_id: str,
    body: VaultWrite,
    request: Request,
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """Hide a folder, or bring it back. Everything under it goes with it, for this user.

    The chain is walked by the access layer, not here, and it is walked for every copy of a file
    rather than the one in front of the reader: a file that also sits in a folder somewhere else
    stays concealed on the strength of this one. Otherwise hiding a folder would take things off the
    screen that a second copy quietly put straight back.
    """
    if body.vault:
        await require_vault_pin(request, viewer)
    if not await wiring.access(request).set_folder_vault(viewer, folder_id, vault=body.vault):
        raise _missing()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
