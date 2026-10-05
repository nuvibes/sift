# SPDX-License-Identifier: AGPL-3.0-or-later
"""Get to know Sift on the wire: read the learning paths.

Every route is a signed-in User's own: the paths are worked out for whoever asks and nobody else,
so there is nothing of another User's to reach. A guest's paths hold only the goals a guest can
reach.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.wiring import Part, hold, part_of, part_or_none
from sift.slices.auth import current_viewer
from sift.slices.insights.path import PathService
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
        library=part_of(request, wiring.LIBRARY),
    )
    hold(request, SERVICE, built)
    return built


@router.get("/insights/path")
async def read_path(
    service: Annotated[PathService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Path:
    """Get to know Sift, judged now: every learning path with its goals."""
    return await service.summary(viewer)
