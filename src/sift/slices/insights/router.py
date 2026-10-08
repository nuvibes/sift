# SPDX-License-Identifier: AGPL-3.0-or-later
"""`GET /api/insights`: the page for one period, as the server says it."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from sift.kernel import wiring
from sift.kernel.access import (
    Repository,
    Viewer,
)
from sift.kernel.db import Database
from sift.kernel.seams import SettingsSeam
from sift.kernel.wire import HistoryPiece, pieces_of
from sift.kernel.workbench import Workbench
from sift.slices.auth import current_viewer
from sift.slices.insights import statements as st
from sift.slices.insights import store
from sift.slices.insights.metrics import ADMIN_ONLY
from sift.slices.insights.models import (
    InsightsBlock,
    InsightsPage,
)
from sift.slices.insights.router_blocks import (
    TITLES,
    VIEWING_BLOCKS,
    Book,
    Page,
    _arrived,
    _machine,
    _organizing,
    _viewing_blocks,
)

__all__ = [
    "CLOCK_KEY",
    "PAGE_METRICS",
    "TITLES",
    "VIEWING_BLOCKS",
    "Book",
    "Page",
    "clock_of",
    "insights_page",
    "router",
]

router = APIRouter(tags=["insights"])


#: The spans the tabs offer, as the address spells them.
Span = Literal["day", "week", "month", "year", "all"]


#: How many recaps the page's recaps block lists; every one is on the recaps screen.
RECAPS_LISTED = 12


#: The metrics the page reads for anybody, and the ones it reads for an admin as well.
PAGE_METRICS = frozenset(
    {
        "viewed_ms",
        "viewed_ms:kind",
        "sittings",
        "sittings:kind",
        "viewed_ms:person",
        "viewed_ms:site",
        "viewed_ms:tag",
        "viewed_ms:collection",
        "viewed_ms:photo_set",
        "viewed_ms:song",
        "sittings:file",
        "viewed_ms:hour",
        "viewed_ms:weekday",
        "theater_ms:wall",
        "pickups",
        "first_opened:person",
        "earliest_start",
        "latest_finish",
        "rated",
        "rated:file",
        "starred",
        "o",
        "o:file",
        "starred:file",
        "decided",
        "decided:queue",
        "faces_named",
        "files_filed",
        "files_added",
        "files_added:site",
        "files_removed",
        "theater_files",
    }
)


#: Which clock the reader writes a time of day on: the key the appearance settings register it
#: under, read here through the preference seam because a slice does not import the slice that
#: registers it. `test_api` holds the two spellings together.
CLOCK_KEY = "appearance.clock"


async def _first_day(database: Database, user_id: str) -> date | None:
    return await store.first_day(database, user_id)


async def _ever_sat(database: Database, user_id: str) -> bool:
    return await store.ever_sat(database, user_id)


async def _first_sentences(
    access: Repository, page: Page, blocks: Sequence[InsightsBlock], ever: bool
) -> list[list[HistoryPiece]]:
    """Up to three statements, by the fixed order of interest (viewing, then organizing, then the
    library), each only past its floor. A guest nothing is shared with, and a User who has viewed
    nothing yet, are told so instead."""
    viewer, book = page.viewer, page.book
    if not viewer.is_admin and not ever:
        permitted, _concealed = await access.visible_counts(viewer)
        if permitted == 0:
            return [pieces_of(st.plain(st.NOTHING_SHARED))]
    if not ever:
        return [pieces_of(st.plain(st.NOTHING_YET))]
    first: list[list[HistoryPiece]] = []
    by_id = {block.id: block for block in blocks}
    for block_id in ("overview", "organizing"):
        block = by_id.get(block_id)
        if block is not None and block.floor_reached and block.statements:
            first.append(block.statements[0])
    if book.total("files_added") > 0:
        first.append(pieces_of(st.arrived(page.period, book.total("files_added"))))
    return first[:3]


async def clock_of(settings: SettingsSeam, viewer: Viewer) -> st.Clock:
    """The clock this reader writes a time of day on. Anything but "24" is the default, which is
    what an unset or retired value means, the same reading the browser's own store makes."""
    return "24" if await settings.get_user(viewer.id, CLOCK_KEY) == "24" else "12"


@router.get("/insights", response_model=InsightsPage)
async def insights_page(
    database: Annotated[Database, Depends(wiring.database)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    settings: Annotated[SettingsSeam, Depends(wiring.settings_hub)],
    board: Annotated[Workbench, Depends(wiring.workbench)],
    period: Annotated[Span, Query()] = "day",
    at: Annotated[date | None, Query()] = None,
) -> InsightsPage:
    """The page for one period: `at` is any day inside it, today by default."""
    today = store.local_today()
    first = await _first_day(database, viewer.id)
    within = st.period_of(period, min(at or today, today), today, first)
    metrics = PAGE_METRICS | ADMIN_ONLY if viewer.is_admin else PAGE_METRICS
    figures = await store.rows(database, viewer.id, within.start, within.end, metrics)
    book = Book.of(figures.rows, locked=not viewer.show_hidden)
    page = Page(
        viewer=viewer,
        period=within,
        book=book,
        figures=figures,
        hours=await clock_of(settings, viewer),
    )

    blocks = await _viewing_blocks(access, database, page, first)
    blocks.append(_organizing(page, board))
    blocks.append(await _arrived(access, database, page))
    if viewer.is_admin:
        blocks.append(_machine(page))

    ever = await _ever_sat(database, viewer.id) or any(
        day.get(("sittings", ""), 0) > 0 for day in book.daily.values()
    )
    return InsightsPage(
        period=period,
        from_=within.start.isoformat(),
        to=within.end.isoformat(),
        today_is_live=today.isoformat() in figures.live_days,
        first_sentences=await _first_sentences(access, page, blocks, ever),
        blocks=blocks,
    )
