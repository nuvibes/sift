# SPDX-License-Identifier: AGPL-3.0-or-later
"""One decode of a still for every model pass that reads it.

Faces, Smart Search and watermarks each read a photograph at the size their model wants: the
faces' working frame and its closer look, the square the describer reads, the two crops a mark is
drawn in. Read apart, a photograph is decoded whole three or four times. Here it is read once and
split inside the decoder, each branch through the very filter its pass would have asked for, so
every pass is handed the pixels it would have read itself (the decoder's own scaler, never a
resize in memory).

The passes ask as usual (`held`), and a picture the decoder refused as damaged is refused to every
pass in the decoder's words, without a second decode.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import re
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from sift.kernel import lanes, media, subprocess
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore
from sift.kernel.log import get_logger, timing_hook

log = get_logger(__name__)

#: Where a still is taken from: never sought, or it loses its only frame.
STILL = media.Moment(seek=())

#: A guard against a wedged decoder, as each pass's own read has.
TIME_LIMIT_SECONDS = 120.0

#: The most raw pixels one decode keeps for the passes; past it the largest ask reads on its own.
MOST_PREPARED_BYTES = 256 * 1024 * 1024

#: The stills each decode refused as damaged, with the decoder's words, for the task on this stack.
_REFUSED: contextvars.ContextVar[Mapping[Path, str] | None] = contextvars.ContextVar(
    "pictures_refused", default=None
)


class PlansFrames(Protocol):
    """What the reader needs of a pass: which frames it would ask for, if any."""

    @property
    def frames(
        self,
    ) -> Callable[[media.FileFacts], Awaitable[Sequence[media.FrameRequest]]] | None: ...


def held(
    source: Path,
    *,
    filters: str,
    pixel_format: str,
    moment: media.Moment = STILL,
) -> bytes | None:
    """The pixels prepared for this ask, or None to read them as usual.

    `FFmpegError` where the one decode of this still was refused as damaged: the same refusal its
    own read would meet, in the decoder's words."""
    refused = _REFUSED.get()
    if refused is not None and source in refused:
        raise media.FFmpegError(refused[source])
    ready = media.prepared_now()
    if ready is None:
        return None
    found = ready.raw(source, [moment], filters=filters, pixel_format=pixel_format)
    return None if found is None else found[0]


def damaged(detail: str) -> str:
    """The decoder's own lines that say the bytes are broken, without a path or an address."""
    lines = []
    for line in detail.splitlines():
        lowered = line.lower()
        found = [lowered.find(one) for one in media.BROKEN_DATA_PHRASES if one in lowered]
        if not found:
            continue
        # From the codec's tag, or the phrase itself: what precedes it names the tool and its path.
        bracket = line.find("[")
        start = bracket if 0 <= bracket < min(found) else min(found)
        # "[mjpeg @ 000001c4...] Decode error rate ...": the codec's name stays, its address goes.
        lines.append(re.sub(r"\s*@\s*[0-9a-fA-Fx]+\]", "]", line[start:]).strip())
    said = "; ".join(dict.fromkeys(lines)) or "the bytes are damaged"
    return f"The decoder refused the picture: {said}"


def still_requests(requests: Sequence[media.FrameRequest]) -> list[media.RawFrames]:
    """The asks one decode of a still can answer, each once, the largest left out past the cap."""
    asks: dict[tuple[str, str], media.RawFrames] = {}
    for request in requests:
        if isinstance(request, media.RawFrames) and request.moments == (STILL,):
            asks.setdefault((request.filters, request.pixel_format), request)
    kept = sorted(asks.values(), key=lambda one: one.frame_bytes)
    while kept and sum(one.frame_bytes for one in kept) > MOST_PREPARED_BYTES:
        kept.pop()
    return kept


def still_graph(requests: Sequence[media.RawFrames]) -> str:
    """The filter graph: the picture split once, each branch through its own ask's filters."""
    branches = "".join(f"[s{index}]" for index in range(len(requests)))
    chains = [f"[0:v]split={len(requests)}{branches}"]
    chains += [f"[s{index}]{one.filters}[o{index}]" for index, one in enumerate(requests)]
    return ";\n".join(chains) + "\n"


def still_args(
    source: Path,
    requests: Sequence[media.RawFrames],
    *,
    script: Path,
    workspace: Path,
    settings: Settings,
) -> list[str]:
    """One decode of `source`, every ask's pixels written under `workspace`. Pure."""
    argv = [
        settings.ffmpeg_path,
        *media.background_flags(settings),
        "-i",
        str(source),
        "-filter_complex_script",
        str(script),
    ]
    for index, request in enumerate(requests):
        argv += ["-map", f"[o{index}]", "-frames:v", "1", "-pix_fmt", request.pixel_format]
        argv += ["-f", "rawvideo", str(_output(workspace, index))]
    return argv


def _output(workspace: Path, index: int) -> Path:
    return workspace / f"{index:02d}.raw"


def _collect(
    source: Path, requests: Sequence[media.RawFrames], workspace: Path
) -> media.PreparedFrames:
    """Read what the decode wrote. Blocking, for a thread."""
    frames = media.PreparedFrames()
    for index, request in enumerate(requests):
        made = _output(workspace, index)
        raw = made.read_bytes() if made.exists() else b""
        frames.put_raw(source, request, [raw if len(raw) == request.frame_bytes else None])
    return frames


async def decode_still(
    source: Path,
    requests: Sequence[media.RawFrames],
    *,
    workspace: Path,
    settings: Settings,
    time_limit: float = TIME_LIMIT_SECONDS,
) -> media.PreparedFrames:
    """Decode `source` once for every ask; `FFmpegError` in the decoder's words if it refused."""
    script = workspace / "graph.txt"
    await asyncio.to_thread(script.write_text, still_graph(requests), encoding="ascii")
    argv = still_args(source, requests, script=script, workspace=workspace, settings=settings)
    try:
        async with lanes.reading(source):
            await subprocess.capture(
                argv, time_limit=time_limit, priority=subprocess.Priority.BACKGROUND
            )
    except subprocess.SubprocessError as error:
        raise media.FFmpegError(str(error)) from error
    return await asyncio.to_thread(_collect, source, requests, workspace)


@contextlib.contextmanager
def _refusing(source: Path, words: str) -> Iterator[None]:
    token = _REFUSED.set({**(_REFUSED.get() or {}), source: words})
    try:
        yield
    finally:
        _REFUSED.reset(token)


class OnePicture:
    """Reads one still once for every pass of a task, where more than one ask would read it."""

    def __init__(self, content: ContentStore, *, settings: Settings) -> None:
        self._content = content
        self._settings = settings

    @contextlib.asynccontextmanager
    async def prepared(self, asset_id: str, passes: Sequence[PlansFrames]) -> AsyncIterator[None]:
        """Decode the still once for these passes and hand its pixels to everything inside."""
        source, requests = await self._plan(asset_id, passes)
        if source is None or len(requests) < 2:
            yield
            return
        with tempfile.TemporaryDirectory(prefix="sift-one-picture-") as workspace:
            try:
                with timing_hook("identify.decode_once", asset_id=asset_id, asks=len(requests)):
                    frames = await decode_still(
                        source, requests, workspace=Path(workspace), settings=self._settings
                    )
            except media.FFmpegError as error:
                if not media.is_broken_data(str(error)):
                    # Each pass reads on its own, as it would have without this.
                    log.warning("ml.pictures.decode_refused", asset_id=asset_id, detail=str(error))
                    yield
                    return
                log.info("ml.pictures.damaged", asset_id=asset_id, detail=str(error))
                with _refusing(source, str(error)):
                    yield
                return
        ready = media.prepared_now()
        with media.prepared(frames if ready is None else ready.merged(frames)):
            yield

    async def _plan(
        self, asset_id: str, passes: Sequence[PlansFrames]
    ) -> tuple[Path | None, list[media.RawFrames]]:
        try:
            source = await media.resolve_decodable(self._content, asset_id, settings=self._settings)
        except (media.MissingAsset, media.NoReadableCopy):
            return None, []
        asset = source.asset
        if asset.media_type != "image" or not asset.width or not asset.height:
            return None, []
        facts = media.FileFacts(
            asset_id=asset_id,
            path=source.path,
            media_type=asset.media_type,
            duration_ms=0,
            width=asset.width,
            height=asset.height,
            fps=0.0,
            size_bytes=source.location.size_bytes or asset.size_bytes or 0,
        )
        requests: list[media.FrameRequest] = []
        for one in passes:
            if one.frames is not None:
                requests.extend(await one.frames(facts))
        return source.path, still_requests(requests)
