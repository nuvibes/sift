# SPDX-License-Identifier: AGPL-3.0-or-later
"""Get to know Sift on the wire: read the learning paths, say a hint was seen.

Every route is a signed-in User's own: the paths are worked out for whoever asks and nobody else,
so there is nothing of another User's to reach. A guest's paths hold only the goals a guest can
reach.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi import Path as PathParameter

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.wiring import Part, hold, part_of, part_or_none
from sift.slices.auth import csrf_protect, current_viewer
from sift.slices.insights.path import NotFound, PathService
from sift.slices.insights.path_models import Path

router = APIRouter(tags=["insights"])

#: The path's service, built on first use and held for the life of the application (its lock has
#: to be one lock for every request, which a per-request object would not be).
SERVICE: Part[PathService] = Part("insights_path")


def _service(request: Request) -> PathService:
    found = part_or_none(request, SERVICE)
    if found is not None:
        return found
    built = PathService(
        part_of(request, wiring.DATABASE),
        part_of(request, wiring.WORKBENCH),
        part_of(request, wiring.INTERFACE_STATE),
        library=part_of(request, wiring.LIBRARY),
    )
    hold(request, SERVICE, built)
    return built


def _missing() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "not found")


@router.get("/insights/path")
async def read_path(
    service: Annotated[PathService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Path:
    """Get to know Sift, judged now: every learning path with its goals, and the hints."""
    return await service.summary(viewer)


@router.post("/insights/path/hints/{name}/seen", status_code=status.HTTP_204_NO_CONTENT)
async def hint_seen(
    service: Annotated[PathService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    name: Annotated[str, PathParameter(min_length=1, max_length=40)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> Response:
    """Remember a hint was shown, so it is not shown again."""
    try:
        await service.seen(viewer, name)
    except NotFound as refused:
        raise _missing() from refused
    return Response(status_code=status.HTTP_204_NO_CONTENT)
