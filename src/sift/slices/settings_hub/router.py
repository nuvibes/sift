# SPDX-License-Identifier: AGPL-3.0-or-later
"""The settings endpoints, generated from the registry rather than written per setting."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ConfigDict, Field

from sift.kernel.access import Viewer
from sift.kernel.settings_registry import SettingError
from sift.kernel.wire import Wire
from sift.kernel.wiring import ON_SETTINGS_CHANGED, part_of, part_or_none
from sift.slices.auth import csrf_protect, current_viewer
from sift.slices.settings_hub.service import (
    SERVICE,
    ScopeForbidden,
    SettingsService,
    UnknownSetting,
)

router = APIRouter(prefix="/settings", tags=["settings"])


def _service(request: Request) -> SettingsService:
    """The whole store, which only this feature may have; others take the read-only view."""
    return part_of(request, SERVICE)


class SettingsUpdate(Wire):
    """A batch of changes, setting key to new value, checked against each declaration."""

    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any] = Field(default_factory=dict)


@router.get("")
async def read_settings(
    viewer: Annotated[Viewer, Depends(current_viewer)],
    service: Annotated[SettingsService, Depends(_service)],
) -> dict[str, Any]:
    """Every setting the caller may see, with its value, grouped into the screen's sections."""
    return await service.effective(viewer)


@router.put("", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(csrf_protect)])
async def update_settings(
    body: SettingsUpdate,
    request: Request,
    viewer: Annotated[Viewer, Depends(current_viewer)],
    service: Annotated[SettingsService, Depends(_service)],
) -> None:
    """Apply a batch of changes, all or none, then tell the change hook which keys moved."""
    try:
        await service.apply(viewer, body.values)
    except UnknownSetting as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except ScopeForbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except SettingError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    notify = part_or_none(request, ON_SETTINGS_CHANGED)
    if notify is not None:
        await notify(set(body.values))


class InterfaceState(Wire):
    """Where somebody has arranged the interface. Short text values, keyed by what they arrange."""

    model_config = ConfigDict(extra="forbid")

    state: dict[str, str] = Field(default={})


class InterfaceUpdate(Wire):
    """A batch of arrangements. A null value means "back to what Sift ships with"."""

    model_config = ConfigDict(extra="forbid")

    state: dict[str, str | None] = Field(default_factory=dict)


@router.get("/interface")
async def read_interface(
    viewer: Annotated[Viewer, Depends(current_viewer)],
    service: Annotated[SettingsService, Depends(_service)],
) -> InterfaceState:
    """How this user has arranged the interface, so it is the same on every machine."""
    return InterfaceState(state=await service.interface(viewer))


@router.put(
    "/interface", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(csrf_protect)]
)
async def update_interface(
    body: InterfaceUpdate,
    viewer: Annotated[Viewer, Depends(current_viewer)],
    service: Annotated[SettingsService, Depends(_service)],
) -> None:
    """Remember an arrangement, all of it or none of it; every user may write their own."""
    try:
        await service.arrange(viewer, body.state)
    except UnknownSetting as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except SettingError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
