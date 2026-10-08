# SPDX-License-Identifier: AGPL-3.0-or-later
"""A recap's deck as a video, for the one person reading it.

The body is one file of the frames the client drew (`video.frames_of`), sent as a form so it is
spooled to disk on the way in rather than held, and the answer is the MP4, which the
client hands to the one door every screenshot takes (`deliver`): the reader's library where they
save screenshots there, else a download. A recap that is not the caller's is 404, as everywhere
in recaps. A week's or a day's deck is pictures only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.kernel.log import get_logger
from sift.kernel.media import FFmpegError, render_node
from sift.slices.auth import csrf_protect, current_viewer
from sift.slices.insights import store, video

log = get_logger(__name__)

router = APIRouter(tags=["insights"])

#: The decks offered as a video.
FILMED = frozenset({"year", "month"})


def film_name(period: str) -> str:
    """ "recap-year-2026.mp4": the recap's, as its pictures are named."""
    return "recap-" + "".join(c if c.isalnum() or c == "-" else "-" for c in period) + ".mp4"


#: How much of the upload is read at a time.
_CHUNK = 1 << 20


async def _chunks(upload: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await upload.read(_CHUNK):
        yield chunk


@router.post(
    "/insights/recaps/{recap_id}/video",
    dependencies=[Depends(csrf_protect)],
    response_class=Response,
    responses={200: {"content": {"video/mp4": {}}, "description": "The deck as an MP4."}},
)
async def recap_video(
    recap_id: str,
    request: Request,
    frames: Annotated[UploadFile, File()],
    database: Annotated[Database, Depends(wiring.database)],
    settings: Annotated[Settings, Depends(wiring.settings)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """The frames in the body, encoded as an MP4 of the deck. Only the caller's own year or month
    recap; nothing of the library is read."""
    found = await store.recap(database, viewer.id, recap_id)
    if found is None or found.period.partition(":")[0] not in FILMED:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    try:
        drawn = await video.frames_of(_chunks(frames))
    except video.NotAFilm as refusal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refusal)) from refusal
    try:
        film = await video.encode(
            drawn,
            ffmpeg=settings.ffmpeg_path,
            accelerator=wiring.part_or_none(request, wiring.ACCELERATOR),
            device=render_node(),
        )
    except FFmpegError as failure:
        log.warning("insights.recap_video_failed", detail=str(failure)[:400])
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Sift couldn't encode the video."
        ) from failure
    name = film_name(found.period)
    return Response(
        content=film,
        media_type="video/mp4",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
