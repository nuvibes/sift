# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Theater endpoints: one user's saved walls, and the record of sitting in front of one.

Four routes for the walls, one for the record (`sessions.py`), and one an open wall beats on. A wall is a way of watching, so everything about watching it (the
files, the streams, what any user may see) runs through the search and player routes that
already existed, under the resolver that already decides visibility. There is deliberately no route
here that returns an asset, a stream or a count.

Authenticated rather than admin-gated, and scoped rather than merely permitted: every statement
behind these filters on the asking user, so an id belonging to somebody else names no row. A
guest has walls of their own for the same reason they have saved searches of their own.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response, status

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.attention import PLAYED
from sift.kernel.db import Database
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, current_viewer
from sift.slices.theater.models import (
    ArrangementBody,
    ArrangementOut,
    Arrangements,
    CellBody,
    SessionReport,
    ShapeBody,
    SlotBody,
)
from sift.slices.theater.service import (
    SERVICE,
    Arrangement,
    Cell,
    NameTaken,
    Shape,
    Slot,
    TheaterService,
    TooMany,
    Unusable,
)
from sift.slices.theater.sessions import record_session

router = APIRouter(tags=["theater"])


def _service(request: Request) -> TheaterService:
    return part_of(request, SERVICE)


def _cells(body: ArrangementBody) -> tuple[Cell, ...]:
    return tuple(
        Cell(
            source=cell.source,
            media_kind=cell.media_kind,
            ordering=cell.ordering,
            end_behaviour=cell.end_behaviour,
            timer_seconds=cell.timer_seconds,
            volume=cell.volume,
            sort=cell.sort,
            aspect=cell.aspect,
        )
        for cell in body.cells
    )


def _shape(body: ArrangementBody) -> Shape | None:
    """The shape as the service holds it, or None for a wall stored under a layout name alone."""
    if body.shape is None:
        return None
    return Shape(
        rows=body.shape.rows,
        cols=body.shape.cols,
        slots=tuple(
            Slot(row=one.row, col=one.col, row_span=one.row_span, col_span=one.col_span)
            for one in body.shape.slots
        ),
    )


def name_taken(name: str) -> str:
    """The refusal a save or a rename onto a name already used reads as, word for word on screen."""
    return f'You already have a Saved Layout called "{name}".'


def _out(arrangement: Arrangement) -> ArrangementOut:
    return ArrangementOut(
        id=arrangement.id,
        name=arrangement.name,
        layout=arrangement.layout,
        strip=arrangement.strip,
        shape=(
            None
            if arrangement.shape is None
            else ShapeBody(
                rows=arrangement.shape.rows,
                cols=arrangement.shape.cols,
                slots=[
                    SlotBody(row=one.row, col=one.col, row_span=one.row_span, col_span=one.col_span)
                    for one in arrangement.shape.slots
                ],
            )
        ),
        cells=[
            CellBody(
                source=cell.source,
                media_kind=cell.media_kind,
                ordering=cell.ordering,
                end_behaviour=cell.end_behaviour,
                timer_seconds=cell.timer_seconds,
                volume=cell.volume,
                sort=cell.sort,
                aspect=cell.aspect,
            )
            for cell in arrangement.cells
        ],
    )


@router.get("/theater/arrangements")
async def arrangements(
    service: Annotated[TheaterService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Arrangements:
    """This user's saved walls, newest first. There is no route to anybody else's."""
    saved = await service.arrangements(viewer)
    return Arrangements(items=[_out(one) for one in saved])


@router.post("/theater/arrangements", status_code=status.HTTP_201_CREATED)
async def save_arrangement(
    body: ArrangementBody,
    service: Annotated[TheaterService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> ArrangementOut:
    """Keep a wall under a name.

    A name this user already uses is a conflict rather than a replace. Saving over a wall is
    several cells' worth of setting up thrown away, so it is done deliberately by updating the one
    that was meant, not by matching a name.
    """
    try:
        saved = await service.save(
            viewer, body.name, body.layout, _shape(body), body.strip, _cells(body)
        )
    except TooMany as full:
        # 409 rather than 422: nothing about the wall is wrong, and sending it again under another
        # name will not help. The state of the user is what refuses it.
        raise HTTPException(status.HTTP_409_CONFLICT, str(full)) from full
    except NameTaken as taken:
        raise HTTPException(status.HTTP_409_CONFLICT, name_taken(str(taken))) from taken
    except Unusable as unusable:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(unusable)) from unusable
    return _out(saved)


@router.patch("/theater/arrangements/{arrangement_id}")
async def update_arrangement(
    arrangement_id: str,
    body: ArrangementBody,
    service: Annotated[TheaterService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> ArrangementOut:
    """Change a saved wall: its name, its layout, its cells.

    Scoped to the asker: an id belonging to another user names no row, and comes back as a plain
    not-found rather than as a refusal that would confirm the row exists.
    """
    try:
        changed = await service.update(
            viewer,
            arrangement_id,
            body.name,
            body.layout,
            _shape(body),
            body.strip,
            _cells(body),
        )
    except NameTaken as taken:
        raise HTTPException(status.HTTP_409_CONFLICT, name_taken(str(taken))) from taken
    except Unusable as unusable:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(unusable)) from unusable
    if changed is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return _out(changed)


@router.delete("/theater/arrangements/{arrangement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_arrangement(
    arrangement_id: str,
    service: Annotated[TheaterService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> Response:
    """Drop one saved wall, and the cells with it.

    Scoped to the asker in the statement: an id belonging to another user names no row this
    touches, so a guessed id is a no-op rather than a way to delete somebody else's, and it is
    answered the same way, because "gone" and "there was nothing of yours" are the same outcome.
    """
    await service.delete(viewer, arrangement_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/theater/sessions/{session}", status_code=status.HTTP_204_NO_CONTENT)
async def report_session(
    body: SessionReport,
    database: Annotated[Database, Depends(wiring.database)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    session: Annotated[str, Path(min_length=1, max_length=64)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> Response:
    """Record a Theater wall opening or closing, for this user.

    The session is named by the browser, and keyed on this user as well as the name, so a name that
    happens to match somebody else's writes a row of this user's own rather than touching theirs.
    It arrives with `keepalive` from a page that may be closing, so nothing is waiting on the
    answer: it is always a 204 once it is past validation.
    """
    await record_session(
        database,
        user_id=viewer.id,
        session=session,
        elapsed_ms=body.elapsed_ms,
        ended=body.ended,
        layout=body.layout,
        cells=body.cells,
        arrangement_id=body.arrangement,
        sources=body.sources,
        files=body.files,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/theater/watching",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(current_viewer), Depends(csrf_protect)],
)
async def theater_open() -> Response:
    """A wall is open, playing or paused, on any device: eco mode holds as it does for a clip."""
    PLAYED.now()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
