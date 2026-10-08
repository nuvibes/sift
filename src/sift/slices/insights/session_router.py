# SPDX-License-Identifier: AGPL-3.0-or-later
"""How one session went: the pages of a recap's longest session, in the order they were opened.

Read when the card is drawn, from the visits themselves, and named through the access layer as
the vault shut: a page about something hidden is left out of the path whoever reads it, open vault
or not, so the card never names something hidden and may always be saved; a page the reader may
not be shown, and a thing since gone, go with it. The rest is each page as a link: a person's, a
Site's, a wall, Settings.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from sift.kernel import wiring
from sift.kernel.access import Concealment, Repository, Viewer
from sift.kernel.access.sentences import said
from sift.kernel.db import Database
from sift.kernel.wire import HistoryPiece, Wire, pieces_of
from sift.slices.auth import current_viewer
from sift.slices.insights import store
from sift.slices.insights.capture import THINGS
from sift.slices.insights.recaps_draw import kept_cards
from sift.slices.insights.recaps_periods import _parts
from sift.slices.insights.statements import Named, named

router = APIRouter(tags=["insights"])

#: The most pages a path draws: the first few and the last, with how many fell between.
FIRST, LAST = 5, 2

_VISITS = """
SELECT place, ref, hidden, opened_at_ms FROM page_visits
 WHERE user_id = ? AND app_session_id = ?
 ORDER BY opened_at_ms, id
"""

#: The walls, by the word a visit keeps, with the screen's own name and address. Not Hidden: a
#: visit there says the reader has something hidden.
_WALLS: dict[str, tuple[str, str]] = {
    "library": ("Browse", "/browse"),
    "search": ("Search", "/browse"),
    "favorites": ("Favorites", "/favorites"),
    "recent": ("Recent", "/recent"),
    "loops": ("Loops", "/loops"),
    "downloads": ("Downloads", "/downloads"),
    "start": ("Start", "/start"),
    "theater": ("Theater", "/theater"),
    "people": ("People", "/people"),
    "tags": ("Tags", "/tags"),
    "sites": ("Sites", "/sites"),
    "collections": ("Collections", "/collections"),
    "photo_sets": ("Photo Sets", "/photo-sets"),
    "songs": ("Songs", "/songs"),
}

_INSIGHTS: dict[str, tuple[str, str]] = {
    "": ("Insights", "/insights"),
    "recaps": ("Recaps", "/insights/recaps"),
    "recap": ("Recaps", "/insights/recaps"),
}


class SessionStep(Wire):
    """One page of the path, as a link to it, and how long into the session it was opened."""

    piece: HistoryPiece
    after_ms: int


class SessionPath(Wire):
    steps: list[SessionStep]
    #: The pages between the first few and the last, not drawn.
    more: int = 0


def _place(place: str, ref: str) -> HistoryPiece | None:
    """A page about a place rather than a thing: its name and address, or None for one unknown."""
    if place == "wall":
        found = _WALLS.get(ref)
    elif place == "insights":
        found = _INSIGHTS.get(ref)
    elif place == "settings":
        found = ("Settings", f"/settings/{ref}" if ref else "/settings")
    elif place == "organize":
        found = ("Organize", "/organize")
    else:
        found = None
    return None if found is None else HistoryPiece(text=found[0], href=found[1])


async def _piece(access: Repository, viewer: Viewer, place: str, ref: str) -> HistoryPiece | None:
    ask = THINGS.get(place)
    if ask is None:
        return _place(place, ref)
    found = await ask(access, viewer, ref)
    name = getattr(found, "name", None)
    if not isinstance(name, str) or not name:
        return None
    return pieces_of(said(named(Named(place, ref, name))))[0]


def session_of(body: str) -> str | None:
    """The session a recap's session card is about, from the figure it keeps."""
    for card in kept_cards(body):
        if card.kind == "session" and card.recipe is not None:
            for name in card.recipe.sources:
                _, metric, key = _parts(name)
                if metric == "session_ms:session":
                    return key
    return None


@router.get("/insights/recaps/{recap_id}/session")
async def recap_session(
    recap_id: str,
    database: Annotated[Database, Depends(wiring.database)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> SessionPath:
    """The pages of the recap's longest session, each as the reader may be shown it now."""
    found = await store.recap(database, viewer.id, recap_id)
    session = None if found is None else session_of(found.body)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    shut = replace(viewer, show_hidden=False, concealment=Concealment.FULLY_GONE)
    steps: list[SessionStep] = []
    began: int | None = None
    for row in await database.fetch_all(_VISITS, (viewer.id, session)):
        began = int(row["opened_at_ms"]) if began is None else began
        if row["hidden"]:
            continue
        piece = await _piece(access, shut, str(row["place"]), str(row["ref"]))
        if piece is None or (steps and steps[-1].piece == piece):
            continue
        steps.append(SessionStep(piece=piece, after_ms=int(row["opened_at_ms"]) - began))
    if len(steps) <= FIRST + LAST:
        return SessionPath(steps=steps)
    return SessionPath(steps=[*steps[:FIRST], *steps[-LAST:]], more=len(steps) - FIRST - LAST)
