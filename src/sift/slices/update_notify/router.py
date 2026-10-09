# SPDX-License-Identifier: AGPL-3.0-or-later
"""The update endpoints: admin-only, and neither can apply anything."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import ConfigDict, Field

from sift.client import build_id
from sift.kernel.access import Viewer
from sift.kernel.version import app_version
from sift.kernel.wire import Wire
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, current_viewer, require_admin
from sift.slices.update_notify.service import SERVICE, UpdateService

router = APIRouter(prefix="/update", tags=["update"])


def _service(request: Request) -> UpdateService:
    return part_of(request, SERVICE)


class Dismissal(Wire):
    """The version a person no longer wants to be reminded about."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=100)


class VersionReport(Wire):
    """What Sift is running. Empty string when run from a source tree with nothing installed."""

    version: str
    #: Which client build this server would serve, finer than the version; empty with none built.
    build: str = ""


@router.get("/version")
async def running_version_report(
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> VersionReport:
    """The running version, for any signed-in user and never before sign-in."""
    del viewer
    return VersionReport(version=app_version(), build=build_id())


@router.get("/check", dependencies=[Depends(require_admin)])
async def check_for_update(
    service: Annotated[UpdateService, Depends(_service)],
) -> dict[str, Any]:
    """What is running, what has been published, and what changed; always the same shape."""
    report = await service.report()
    return report.as_dict()


@router.post(
    "/dismiss",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin), Depends(csrf_protect)],
)
async def dismiss_update(
    body: Dismissal,
    viewer: Annotated[Viewer, Depends(require_admin)],
    service: Annotated[UpdateService, Depends(_service)],
) -> None:
    """Stop the banner for one version. The next release brings it back."""
    await service.dismiss(viewer, body.version)
