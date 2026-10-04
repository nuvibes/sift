# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pages a User opened, reported by their client, and their own history cleared.

Both routes are a signed-in User's own: a visit is written for whoever reports it and nobody else,
and a clear clears the history of whoever presses it. There is no id in either that could name
another User.
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.client import client_of
from sift.kernel.db import Database
from sift.kernel.seams import SettingsSeam
from sift.kernel.use_history import clear_history_of
from sift.slices.auth import csrf_protect, current_viewer
from sift.slices.insights.capture import Visit, record_visits
from sift.slices.insights.capture_models import VisitsKept, VisitsReport

router = APIRouter(tags=["insights"])


@router.post("/insights/visits")
async def report_visits(
    body: VisitsReport,
    request: Request,
    database: Annotated[Database, Depends(wiring.database)],
    access: Annotated[Repository, Depends(wiring.access)],
    preferences: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> VisitsKept:
    """Write down a batch of the pages this person had in front of them. See `capture`."""
    kept = await record_visits(
        database,
        access,
        preferences,
        viewer,
        client_of(request),
        [
            Visit(
                id=one.id,
                place=one.place,
                ref=one.ref,
                opened_ago_ms=one.opened_ago_ms,
                last_ago_ms=one.last_ago_ms,
                front_ms=one.front_ms,
            )
            for one in body.visits
        ],
        now_ms=int(time.time() * 1000),
    )
    return VisitsKept(kept=kept)


@router.delete("/insights/history", status_code=status.HTTP_204_NO_CONTENT)
async def clear_history(
    database: Annotated[Database, Depends(wiring.database)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> Response:
    """Clear everything Sift keeps about this person's use, in one write: every part a feature
    keeps registers how it is cleared (`kernel/use_history.py`), and the figures added up from them
    go too. Their files, ratings, tags and saved searches are not history and are left alone."""
    await clear_history_of(database, viewer.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
