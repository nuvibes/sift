# SPDX-License-Identifier: AGPL-3.0-or-later
"""The recaps on the wire: the list with the one being announced, one recap opened, the cross.

**A User reads only their own.** Every read is asked with the caller's id beside the recap's
(`store.recap`), so somebody else's recap is not a row this route can find. It answers 404 (the
same answer as an id that was never minted, and as a recap a locked vault leaves out), so the
answer never says which of the three it was.

**Opening marks it seen, on a GET.** Reading a recap is the act that ends its announcement, and the
contract says so; nothing else about the recap moves, so a GET that is repeated, prefetched or
replayed only ever draws the same moment again (`seen_at` is written once).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import Field

from sift.kernel import wiring
from sift.kernel.access import Repository, Viewer
from sift.kernel.db import Database
from sift.kernel.seams import SettingsSeam
from sift.kernel.wire import Wire
from sift.slices.auth import csrf_protect, current_viewer
from sift.slices.insights import recaps
from sift.slices.insights.naming import covered_cards
from sift.slices.insights.recaps_models import Recap, RecapList
from sift.slices.insights.router import clock_of

router = APIRouter(tags=["insights"])


def _missing() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "not found")


@router.get("/insights/recaps")
async def list_recaps(
    database: Annotated[Database, Depends(wiring.database)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> RecapList:
    """Every recap of a day, a week, a month or a year this User has, newest first, drawn for their vault
    state now, and the one the top of Insights and Browse's header announce."""
    listed, announced = await recaps.heads(database, viewer)
    return RecapList(recaps=listed, announced=announced)


@router.get("/insights/recaps/{recap_id}")
async def open_recap(
    recap_id: str,
    database: Annotated[Database, Depends(wiring.database)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    settings: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
) -> Recap:
    """One recap, its cards drawn for the reader's vault state and clock now. The first open marks
    it seen."""
    found = await recaps.opened(database, viewer, recap_id, hours=await clock_of(settings, viewer))
    if found is None:
        raise _missing()
    cards = await covered_cards(access, database, viewer, found.cards)
    return found.model_copy(update={"cards": cards})


class RecapLeftOut(Wire):
    """The people and files a reader takes out of their recap before sharing it."""

    ids: list[Annotated[str, Field(max_length=64)]] = Field(max_length=500)


@router.put("/insights/recaps/{recap_id}/left-out", dependencies=[Depends(csrf_protect)])
async def leave_out(
    recap_id: str,
    body: RecapLeftOut,
    database: Annotated[Database, Depends(wiring.database)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    settings: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
) -> Recap:
    """Take people and files out of a recap, or put them back: the cards naming one are drawn
    again without it, for the deck and its pictures alike. Answers the recap drawn again."""
    if not await recaps.left_out(database, viewer, recap_id, body.ids):
        raise _missing()
    return await open_recap(recap_id, database, access, viewer, settings)


@router.post(
    "/insights/recaps/{recap_id}/dismiss",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def dismiss_recap(
    recap_id: str,
    database: Annotated[Database, Depends(wiring.database)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The cross on the announcement. The recap stays in the list, to be opened whenever."""
    if not await recaps.dismissed(database, viewer, recap_id):
        raise _missing()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
