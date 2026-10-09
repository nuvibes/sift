# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hidden's endpoints: opening always costs the PIN, shutting costs nothing, both per user."""

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
    """The same 404 as a never-minted id: "you may not" is what concealment withholds."""
    return HTTPException(status.HTTP_404_NOT_FOUND, "not found")


# --- Opening and shutting --------------------------------------------------------------------


@router.get("/vault")
async def vault_state(
    request: Request,
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> VaultState:
    """Whether this browser has the vault open, and whether a PIN exists to open it."""
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
    """Show this user's hidden things in this browser; the PIN shares the lock screen's throttle."""
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
    """Hide them again: no proof and no precondition, since this direction fails safe."""
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
    """Hide one file, or bring it back; no body, since the row may no longer be visible."""
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
    """Hide a selection, or bring it back, in one request; refused files are counted, not named."""
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
    """Hide a folder, or bring it back; the access layer hides every copy under it."""
    if body.vault:
        await require_vault_pin(request, viewer)
    if not await wiring.access(request).set_folder_vault(viewer, folder_id, vault=body.vault):
        raise _missing()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
