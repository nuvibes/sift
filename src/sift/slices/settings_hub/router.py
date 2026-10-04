# SPDX-License-Identifier: AGPL-3.0-or-later
"""The settings endpoints.

The two main routes are generated from the registry rather than written per setting: reading
returns every setting the caller may see with its current value, and writing validates each change
against its registered declaration. Adding a preference elsewhere in the app is one
`register_setting` call and it appears here with no edit to this file: that is the point of a
registry, and the reason there is no bespoke endpoint per setting.

A write is state-changing, so it carries `csrf_protect` like every other mutating route. Whether a
particular change is allowed is decided per key inside the service: a guest may change their own
per-user preferences, and a global setting is refused with a 403 on the server, not merely hidden.
"""

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
    """The whole store, which only this feature may have.

    The kernel publishes the same object under the same name as a read-only interface, and that is
    what every other feature takes. Writing a preference has to check the key exists, that the value
    fits its type, and that this user may set it, so the writes stay behind this door.
    """
    return part_of(request, SERVICE)


class SettingsUpdate(Wire):
    """A batch of changes: setting key to new value. Values are whatever the setting holds (a
    boolean, a number, a string) and are validated against the registered declaration, not here."""

    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any] = Field(default_factory=dict)


@router.get("")
async def read_settings(
    viewer: Annotated[Viewer, Depends(current_viewer)],
    service: Annotated[SettingsService, Depends(_service)],
) -> dict[str, Any]:
    """Every setting the caller may see, with its value, grouped into the screen's sections.

    A guest sees their own per-user preferences; an admin also sees the instance-wide ones. The
    sections are all present even when empty, so the screen renders the same shell for everyone.
    """
    return await service.effective(viewer)


@router.put("", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(csrf_protect)])
async def update_settings(
    body: SettingsUpdate,
    request: Request,
    viewer: Annotated[Viewer, Depends(current_viewer)],
    service: Annotated[SettingsService, Depends(_service)],
) -> None:
    """Apply a batch of changes, all or none.

    An unknown key is a 400: it is a bug or a probe, never a preference. A guest writing a global
    setting is a 403, enforced here on the server. A value its validator rejects is a 422. Nothing
    is stored unless every change in the batch is accepted.

    Once the batch has landed, anything that has to act on a change the instant it is made (the
    watcher rebuilding its observers when the polling setting flips) is told through an optional
    application hook. The route does not know what listens or why; it names the keys that changed and
    leaves the wiring to whoever installed the hook. Absent in a test that builds routes without
    booting the app, so it is reached for rather than assumed.
    """
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


# There is no first-run endpoint: a first run is a user and an installation method and nothing else.
# Every other question is asked in Settings, or not at all. See the note in the settings registry.


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
    """How this user has arranged the interface, so it is the same on every machine.

    Separate from the settings above and not a section of them. A setting is a preference chosen on
    a screen and every one of them is drawn there; where somebody dragged a rail row to is not a
    preference and would be a nonsense row in Settings.

    Empty is the ordinary answer for a user that has never rearranged anything.
    """
    return InterfaceState(state=await service.interface(viewer))


@router.put(
    "/interface", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(csrf_protect)]
)
async def update_interface(
    body: InterfaceUpdate,
    viewer: Annotated[Viewer, Depends(current_viewer)],
    service: Annotated[SettingsService, Depends(_service)],
) -> None:
    """Remember an arrangement, all of it or none of it.

    Every signed-in user may write their own, guest included: this is where they put their own rail
    and it reaches nobody else. An unknown key is a 400, and a value the store cannot hold is a 422.
    """
    try:
        await service.arrange(viewer, body.state)
    except UnknownSetting as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except SettingError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
