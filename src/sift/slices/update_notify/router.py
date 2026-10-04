# SPDX-License-Identifier: AGPL-3.0-or-later
"""The update endpoints.

Two routes, both admin-only. Reading is admin-only because it is the route that can cause the
server to make an outbound request, and nobody who is not running the installation has a reason to
be able to trigger that. Dismissing is admin-only because applying an update is an admin's job, so
deciding not to be reminded about one is too.

Neither route can apply anything. There is no third route, and there is nothing behind these two
that starts a process or replaces a file: installing is the desktop application's, on a press.
"""

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
    #: Which BUILD of the client this server would serve, as a short digest of the page shell.
    #:
    #: Two installs can carry the same version and a different client build, so this is a finer
    #: question than the one above and a different one: it is "would reloading give you something
    #: different", asked by a window that has been open across an upgrade. Empty in a checkout with
    #: no client built. See `sift.client.build_id`.
    build: str = ""


@router.get("/version")
async def running_version_report(
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> VersionReport:
    """The version of Sift that is running.

    Any signed-in user, unlike the update check beside it, which is admin-only because it is the
    one route that makes the server open a connection to the internet. This reads the installed
    package and reaches nothing, so there is nothing here for a caller to trigger, and the About
    screen shows it to a guest as readily as to an admin.

    Not public, though. A version number is the one fact that turns a general "some Sift is here"
    into "this Sift has the flaws published against that release": it is the first thing worth
    reading off an internet-exposed installation and the last thing worth handing over for free.
    Nothing needs it before sign-in (the About screen is inside the app) so requiring a session
    costs no feature and takes the fingerprint off the front door.
    """
    del viewer
    return VersionReport(version=app_version(), build=build_id())


@router.get("/check", dependencies=[Depends(require_admin)])
async def check_for_update(
    service: Annotated[UpdateService, Depends(_service)],
) -> dict[str, Any]:
    """What is running, what has been published, and what changed in it.

    Always answers. With no network, with the check turned off, or before the first check has
    happened, the reply is the same shape with nothing published in it, so a screen renders the
    same way whether or not Sift has ever reached the internet.
    """
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
