# SPDX-License-Identifier: AGPL-3.0-or-later
"""The dependencies every download route module shares."""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from sift.kernel.tunnels import (
    EGRESS,
    EgressRouter,
    TunnelError,
)
from sift.kernel.wiring import SETTINGS_HUB, part_of
from sift.slices.download.service import (
    SERVICE,
    DownloadService,
)
from sift.slices.download.sources.policy import read_policy
from sift.slices.download.sources.tuning import RunPolicy


def _service(request: Request) -> DownloadService:
    return part_of(request, SERVICE)


def _egress(request: Request) -> EgressRouter:
    """The router the download job uses, so a listing request goes out the way downloads do."""
    return part_of(request, EGRESS)


async def _policy(request: Request) -> RunPolicy:
    """What the tools are told, read live: a listing request is paced as a download would be."""
    return await read_policy(part_of(request, SETTINGS_HUB).get_app)


def _refused(exc: TunnelError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
