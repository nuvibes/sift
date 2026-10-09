# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning a playback decision into bytes: the playlist, the segments, and the range reader.

Direct play spawns no ffmpeg. Segments are built one at a time through the job queue, whose cap
holds across every process; the waiting request polls the job row itself to keep latency low.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from sift.kernel.config import Settings
from sift.kernel.content import Asset
from sift.kernel.jobs import WAITED_ON_PRIORITY, JobQueue, JobState
from sift.kernel.log import get_logger
from sift.kernel.media import Accelerator, Encoder
from sift.kernel.threads import on_serving_thread
from sift.kernel.wiring import Part
from sift.slices.player import policy, tuning
from sift.slices.player.cache import SegmentCache, size_on_disk
from sift.slices.player.transcode import (
    SegmentSpec,
    realtime_ratio,
    render,
    segment_args,
    split_fragment,
)

log = get_logger(__name__)

TRANSCODE = "transcode"
"""The job type. Registered by `sift/wiring/playback.py`, capped by `jobs.job_limits`."""


class SegmentUnavailable(Exception):
    """A segment could not be produced: a 503, never the 404 a refusal also reads as."""


#: `bytes=START-END`, either end optional; strict, as a loose reading could read outside the file.
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


@dataclass(frozen=True, slots=True)
class ByteRange:
    """A resolved, clamped, inclusive byte range within a file of known size."""

    start: int
    end: int
    size: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    @property
    def content_range(self) -> str:
        return f"bytes {self.start}-{self.end}/{self.size}"


def byte_range(header: str | None, size: int) -> ByteRange | None:
    """What slice of a `size`-byte file the client asked for, or None for the whole thing.

    Raises `ValueError` for an unsatisfiable range, answered with a 416.
    """
    if header is None:
        return None

    match = _RANGE.match(header.strip())
    if match is None:
        # Unparseable is ignored, as HTTP asks: the whole file is always a correct answer.
        return None

    raw_start, raw_end = match.group(1), match.group(2)
    if not raw_start and not raw_end:
        return None

    if not raw_start:
        # `bytes=-N`: the final N bytes, or the whole file if longer.
        length = int(raw_end)
        if length == 0:
            raise ValueError("an empty range cannot be satisfied")
        start = max(0, size - length)
        end = size - 1
    else:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1
        # Clamped: a player asks for more than is there before it knows the length.
        end = min(end, size - 1)

    if start >= size:
        raise ValueError("that range is outside the file")
    if start > end:
        # Not a range at all: ignored, since 416 is only for a valid range past the end.
        return None

    return ByteRange(start=start, end=end, size=size)


def capped(span: ByteRange, header: str | None, limit: int) -> ByteRange:
    """An open-ended range shortened to what one response should commit to; a bounded one is not."""
    if header is None or not header.strip().endswith("-") or span.length <= limit:
        return span
    return ByteRange(start=span.start, end=span.start + limit - 1, size=span.size)


def _opened_at(path: Path, offset: int) -> BinaryIO:
    """Open a file and seek to the range's start, off the loop in one hop."""
    handle = path.open("rb")
    handle.seek(offset)
    return handle


async def read_range(
    path: Path,
    span: ByteRange,
    *,
    gone: Callable[[], Awaitable[bool]] | None = None,
    played: Callable[[], None] | None = None,
) -> AsyncIterator[bytes]:
    """Stream exactly the requested bytes in bounded chunks, each read on a thread.

    A read on the event loop stalls every other stream; `gone` stops a read nobody waits for.
    """
    remaining = span.length
    # Opened on a thread; the `with` closes the handle on every way out.
    with await on_serving_thread(_opened_at, path, span.start) as handle:
        while remaining > 0:
            if gone is not None and await gone():
                return
            wanted = min(tuning.STREAM_CHUNK_BYTES, remaining)
            chunk = await on_serving_thread(handle.read, wanted)
            if not chunk:
                return
            remaining -= len(chunk)
            if played is not None:
                played()
            yield chunk


def plan_query(plan: policy.Plan) -> str:
    """The plan as the query string that carries it between HLS requests; validated on return."""
    parts = [f"route={plan.route.value}"]
    if plan.scale_height is not None:
        parts.append(f"height={plan.scale_height}")
    return "?" + "&".join(parts)


def source_bitrate(asset: Asset) -> int:
    """Roughly what one second of the file itself weighs, in bits, averaged over the file."""
    if not asset.size_bytes or not asset.duration_ms or asset.duration_ms <= 0:
        return tuning.FALLBACK_SOURCE_BITRATE
    return max(1, round(asset.size_bytes * 8 * 1000 / asset.duration_ms))


def master_playlist(asset: Asset, *, base_url: str) -> str:
    """The ladder of sizes for a player's Auto to move between, smallest first, the source last."""
    lines = ["#EXTM3U", "#EXT-X-VERSION:7"]
    floor = 0
    for rung in (*reversed(policy.rungs(asset.width, asset.height)), None):
        height = rung.height if rung else None
        out_height = height or asset.height
        out_width = (
            policy.scaled_width(asset.width, asset.height, height) if height else asset.width
        )
        # By the rung's short side, the table's key, so portrait rungs are weighed too.
        weight = tuning.LADDER_BITRATES[rung.short] if rung else source_bitrate(asset)
        # A variant never declares less than a smaller one: a badly measured source looks cheapest.
        bandwidth = max(weight, floor + 1)
        floor = bandwidth
        attributes = [
            f"BANDWIDTH={bandwidth}",
            f'CODECS="{policy.codec_string(out_width, out_height, asset.fps)}"',
        ]
        if out_width and out_height:
            attributes.insert(1, f"RESOLUTION={out_width}x{out_height}")
        lines.append(f"#EXT-X-STREAM-INF:{','.join(attributes)}")
        lines.append(f"{base_url}/index.m3u8{rung_query(height)}")
    return "\n".join(lines) + "\n"


def rung_query(height: int | None) -> str:
    """The query string one rung is asked for by: always a transcode."""
    if height is None:
        return f"?route={policy.Route.TRANSCODE.value}"
    return f"?route={policy.Route.TRANSCODE.value}&height={height}"


def playlist(asset: Asset, *, base_url: str, query: str = "") -> str:
    """The media playlist: every segment, declared complete (VOD) so the player can seek anywhere.

    The target duration comes from the longest real segment, since the last one is short.
    """
    timeline = policy.uniform_timeline(asset.duration_ms)
    longest = max(
        (timeline.bounds(index)[1] for index in range(timeline.count)),
        default=tuning.SEGMENT_SECONDS,
    )
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:7",
        f"#EXT-X-TARGETDURATION:{math.ceil(longest)}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        f'#EXT-X-MAP:URI="{base_url}/{tuning.INIT_SEGMENT_NAME}{query}"',
    ]
    for index in range(timeline.count):
        _, duration = timeline.bounds(index)
        lines.append(f"#EXTINF:{duration:.3f},")
        lines.append(f"{base_url}/{index}{tuning.SEGMENT_SUFFIX}{query}")
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def segment_key(asset_id: str, index: int, plan: policy.Plan) -> str:
    """The cache key for one segment, including the plan, since plans cut different streams."""
    shape = f"h{plan.scale_height or 0}"
    return f"{asset_id}-{shape}-{index}{tuning.SEGMENT_SUFFIX}"


def init_key(asset_id: str, plan: policy.Plan) -> str:
    shape = f"h{plan.scale_height or 0}"
    return f"{asset_id}-{shape}-{tuning.INIT_SEGMENT_NAME}"


@dataclass(frozen=True, slots=True)
class Segment:
    """One segment, ready to serve."""

    path: Path
    cached: bool
    """True when it was already there: the ordinary case during smooth playback."""

    ephemeral: bool
    """True when the cache refused to keep it because it is larger than the whole cap.

    The router serves it and then deletes it. It is not an error: the person watching gets their
    video, the cache simply does not carry something it can never make room for.
    """


class PlayerService:
    """Produces segments, one at a time, and remembers them until the cache is full."""

    def __init__(
        self,
        cache: SegmentCache,
        queue: JobQueue,
        *,
        settings: Settings,
        accelerator: Accelerator,
        device: str | None = None,
        cpu_count: int = 1,
    ) -> None:
        self._cache = cache
        self._queue = queue
        self._settings = settings
        # The one source of truth for encoding and decoding: the card can be given up mid-session.
        self._accelerator = accelerator
        self._device = device
        self._cpu_count = max(1, cpu_count)
        # Believed encoder speed in pixels a second; None for the processor.
        self._encoder_rate = tuning.STARTING_ENCODER_PIXELS_PER_SECOND.get(
            accelerator.encoder.value
        )

    @property
    def encoder(self) -> Encoder:
        """What is encoding a segment right now, asked of the accelerator every time."""
        return self._accelerator.encoder

    @property
    def decode(self) -> tuple[str, ...]:
        """Whether the decoding is on the card right now."""
        return self._accelerator.decode

    @property
    def encoder_rate(self) -> float | None:
        """How fast the encoder is believed to be; None on the processor, also after a fallback."""
        return None if self.encoder is Encoder.CPU else self._encoder_rate

    def observe(
        self, *, asset: Asset, plan: policy.Plan, elapsed: float, video_seconds: float
    ) -> None:
        """Learn the encoder's real speed from a segment just built, less its decode time.

        A reading too small to measure anything is thrown away.
        """
        if self._encoder_rate is None:
            return
        out_height = plan.scale_height or asset.height
        out_width = (
            policy.scaled_width(asset.width, asset.height, plan.scale_height)
            if plan.scale_height
            else asset.width
        )
        if not out_width or not out_height or video_seconds <= 0:
            return

        decode = policy.decode_seconds(
            width=asset.width,
            height=asset.height,
            fps=asset.fps,
            vcodec=(asset.vcodec or "").lower(),
            cpu_count=self._cpu_count,
            video_seconds=video_seconds,
        )
        encoding = elapsed - (decode or 0.0)
        if encoding < tuning.SHORTEST_USABLE_ENCODE_SECONDS:
            return

        rate = asset.fps or 30.0
        pixels = out_width * out_height * rate * video_seconds
        observed = pixels / encoding
        weight = tuning.OBSERVATION_WEIGHT
        self._encoder_rate = (1 - weight) * self._encoder_rate + weight * observed
        log.info(
            "player.encoder_rate",
            encoder=self.encoder.value,
            observed=round(observed / 1_000_000, 1),
            estimate=round(self._encoder_rate / 1_000_000, 1),
        )

    def spec_for(
        self,
        source: Path,
        destination: Path,
        *,
        index: int,
        asset: Asset,
        plan: policy.Plan,
        encoder: Encoder | None = None,
        decode: tuple[str, ...] | None = None,
    ) -> SegmentSpec:
        start, duration = policy.segment_bounds(index, asset.duration_ms)
        out_height = plan.scale_height or asset.height
        out_width = (
            policy.scaled_width(asset.width, asset.height, plan.scale_height)
            if plan.scale_height
            else asset.width
        )
        level, _ = policy.h264_level(out_width, out_height, asset.fps)
        return SegmentSpec(
            source=source,
            destination=destination,
            start_seconds=start,
            duration_seconds=duration,
            scale_height=plan.scale_height,
            encoder=self.encoder if encoder is None else encoder,
            device=self._device,
            decode=self.decode if decode is None else decode,
            level=level,
            hdr=asset.is_hdr,
        )

    async def segment(self, asset: Asset, *, index: int, plan: policy.Plan) -> Segment:
        """The path to one segment, producing it if it is not already cached."""
        key = segment_key(asset.id, index, plan) if index >= 0 else init_key(asset.id, plan)
        hit = self._cache.touch(key)
        if hit is not None:
            return Segment(path=hit, cached=True, ephemeral=False)

        await self._produce(asset, index=index, plan=plan)

        path = self._cache.path_for(key)
        if not await asyncio.to_thread(path.exists):
            raise SegmentUnavailable(f"segment {index} of {asset.id} could not be produced")
        # Refused by the cache for its size: served, then deleted.
        return Segment(path=path, cached=False, ephemeral=key not in self._cache)

    async def _produce(self, asset: Asset, *, index: int, plan: policy.Plan) -> None:
        """Run the transcode through the queue, so the one-at-a-time cap holds."""
        job_id = await self._queue.enqueue(
            TRANSCODE,
            # Ids, never paths: a path in a payload is a path in every log line.
            {
                "asset_id": asset.id,
                "index": index,
                "route": plan.route.value,
                "scale_height": plan.scale_height,
            },
            # Not retried: the player asks again if it still wants it.
            max_attempts=1,
            # Somebody is watching a spinner; at the ordinary priority this times out when busy.
            priority=WAITED_ON_PRIORITY,
        )
        await self._await_job(job_id)

    async def _await_job(self, job_id: str) -> None:
        """Wait for a queued transcode to finish, polling faster than the worker's own cadence."""
        deadline = asyncio.get_running_loop().time() + tuning.SEGMENT_TIMEOUT_SECONDS
        while True:
            job = await self._queue.get(job_id)
            if job is None:
                raise SegmentUnavailable("the transcode job disappeared")
            if job.state is JobState.DONE:
                return
            if job.state in (JobState.FAILED, JobState.CANCELED):
                raise SegmentUnavailable(job.error or "the transcode failed")
            if asyncio.get_running_loop().time() > deadline:
                raise SegmentUnavailable("the transcode did not finish in time")
            await asyncio.sleep(tuning.JOB_POLL_SECONDS)

    async def build(self, asset: Asset, source: Path, *, index: int, plan: policy.Plan) -> float:
        """Render one segment into the cache directory and offer it to the cache. Seconds taken.

        Segment zero is rendered whole and cut in two, so the init header matches the stream.
        """
        wanted = max(index, 0)
        key = segment_key(asset.id, wanted, plan)
        scratch = self._cache.path_for(f".build-{key}")
        # Resolved once, here, off the loop: an argv builder does no I/O.
        resolved = await asyncio.to_thread(source.resolve)

        async def render_with(encoder: Encoder, decode: tuple[str, ...]) -> float:
            spec = self.spec_for(
                resolved,
                scratch,
                index=wanted,
                asset=asset,
                plan=plan,
                encoder=encoder,
                decode=decode,
            )
            return await render(
                segment_args(spec, settings=self._settings), scratch, stage="segment"
            )

        # The fallback to the processor, inside the one attempt: the job is never retried.
        elapsed = await self._accelerator.run(render_with, frame=(asset.width, asset.height))

        # Segment-sized disk work goes to a thread in one hop; the cache's bookkeeping stays here.
        header = self._cache.path_for(init_key(asset.id, plan))
        destination = self._cache.path_for(key)

        def place() -> tuple[int, int]:
            """Split the rendered fragment, write both halves, and report their sizes."""
            try:
                init, media = split_fragment(scratch.read_bytes())
            finally:
                scratch.unlink(missing_ok=True)

            # Written once: the header is the same for every segment of a stream.
            kept_header = 0
            if init and not header.exists():
                header.parent.mkdir(parents=True, exist_ok=True)
                header.write_bytes(init)
                kept_header = len(init)

            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(media or init)
            return kept_header, size_on_disk(destination)

        kept_header, size = await asyncio.to_thread(place)
        if kept_header:
            self._cache.admit(init_key(asset.id, plan), kept_header)

        if not self._cache.admit(key, size):
            log.info("player.segment_served_without_caching", key=key, size_bytes=size)

        _, video_seconds = policy.segment_bounds(wanted, asset.duration_ms)
        self.observe(asset=asset, plan=plan, elapsed=elapsed, video_seconds=video_seconds)
        log.info(
            "player.segment_timing",
            asset_id=asset.id,
            index=wanted,
            seconds=round(elapsed, 3),
            realtime=realtime_ratio(elapsed_seconds=elapsed, video_seconds=video_seconds),
        )
        return elapsed


#: Playback.
SERVICE: Part[PlayerService] = Part("player")
