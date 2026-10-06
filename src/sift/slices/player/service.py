# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning a playback decision into bytes: the playlist, the segments, and the range reader.

Three separate jobs live here, and only one of them is expensive.

**Direct play** is the majority path and costs nothing but reading a file. `byte_range` works out
which slice of a file an HTTP `Range` header asked for, and `read_range` streams it. No ffmpeg
process is spawned, which the test suite asserts by counting processes. If that ever stops being
true, the cheapest path in the application has quietly started paying for the most expensive one.

**The playlist** is a few lines of text describing where the segments are. It is generated from the
asset's duration and never stored.

**Segments** are produced one at a time, on demand, and cached. The serialisation is the part worth
explaining.

## Why one transcode at a time, and why it is a job

Concurrency is actively harmful here: throughput *falls* as jobs are added (about a tenth lower at
two, nearly half at three), because ffmpeg already saturates the cores
it is given, so a second job subtracts cores from the first while adding cache pressure. Everyone
waits longer and less total work gets done. The correct response to a second request is to queue it.

That cap is enforced by the job queue (`jobs.job_limits`, handed to the worker pool by
`sift/wiring/workers.py` and applied in the claim's SQL) rather than by a semaphore here. The
reason is that a semaphore in this process caps *this* process, and the cap needs to hold across
everything that can start an ffmpeg. A second uvicorn worker, or the background thumbnail
pipeline, would walk straight past a local lock.

The cost of routing through a durable queue is latency: its idle poll is a full second, which would
eat most of the budget before ffmpeg started. So the request that is waiting polls the job row
itself every 50 ms (`tuning.JOB_POLL_SECONDS`) rather than waiting on the worker's own cadence.
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
    """A segment could not be produced. The router turns this into a 503, not a 404.

    The distinction matters: a 404 says "there is no such thing", which for a streaming endpoint is
    also what a permission refusal says. A segment that exists and could not be built is a
    different fact and must not be confused with either.
    """


# --- HTTP range requests (the direct-play path) ------------------------------------------------

#: `bytes=START-END`, either end optional. Deliberately strict: this parses a header an unknown
#: client sent, and the permissive reading of a malformed range is a read outside the file.
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
    """What slice of a `size`-byte file the client asked for. None if it asked for the whole thing.

    Raises `ValueError` for a range that cannot be satisfied, which the router answers with a 416.

    The open-ended forms both appear in the wild and mean different things: `bytes=500-` is "from
    here to the end", which is what a player sends when it starts, and `bytes=-500` is "the last
    500 bytes", which is what one sends looking for an MP4's index when the file is not faststart.
    """
    if header is None:
        return None

    match = _RANGE.match(header.strip())
    if match is None:
        # An unparseable Range is ignored rather than refused, which is what the HTTP spec asks
        # for: the client gets the whole file, which is always a correct answer to a bad range.
        return None

    raw_start, raw_end = match.group(1), match.group(2)
    if not raw_start and not raw_end:
        return None

    if not raw_start:
        # `bytes=-N`: the final N bytes. A suffix longer than the file means the whole file.
        length = int(raw_end)
        if length == 0:
            raise ValueError("an empty range cannot be satisfied")
        start = max(0, size - length)
        end = size - 1
    else:
        start = int(raw_start)
        end = int(raw_end) if raw_end else size - 1
        # Clamped, not rejected: a player routinely asks for more than is there when it does not
        # yet know the length, and the correct answer is what exists.
        end = min(end, size - 1)

    if start >= size:
        # Valid, and past the end: the one case 416 is the word for.
        raise ValueError("that range is outside the file")
    if start > end:
        # `bytes=100-50` is not a range at all. The specification says to ignore a Range header
        # holding an invalid range, and the whole file is always a correct answer to a bad one;
        # 416 is the word for a range that is valid and lies past the end, which this is not.
        return None

    return ByteRange(start=start, end=end, size=size)


def capped(span: ByteRange, header: str | None, limit: int) -> ByteRange:
    """A range with no end of its own, shortened to what one response should commit to.

    Only the open-ended form is shortened. A range that names both ends was asked for deliberately
    and is answered in full; `bytes=-500` names its own length too. See `tuning.MAX_OPEN_RANGE_BYTES`
    for why an unbounded one is not.
    """
    if header is None or not header.strip().endswith("-") or span.length <= limit:
        return span
    return ByteRange(start=span.start, end=span.start + limit - 1, size=span.size)


def _opened_at(path: Path, offset: int) -> BinaryIO:
    """Open a file for reading and seek to where the range starts. Both syscalls, both off the
    loop, one hop."""
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
    """Stream exactly the requested bytes, in bounded chunks, without blocking the event loop.

    Bounded because the alternative is reading a range into memory before sending it, and a player
    seeking near the end of a large file can ask for a very large range. The loop counts down what
    is left rather than trusting the file's length, so a file truncated underneath a live read
    stops rather than looping.

    **Every read is handed to a thread, or playback stutters.** Sift is one process with one event
    loop: the API, the live job feed and every stream share it. A `read()` on that loop stops all
    of them for as long as it takes, and on a bind mount into the host filesystem it takes
    milliseconds rather than microseconds. Yielding between chunks does not help: the yield happens AFTER the read, so what
    it hands back is a loop that has already been stopped. Reading in a thread means the loop is
    free during the read instead of after it, and the await below is the yield, so nothing needs a
    `sleep(0)`.

    `gone` is asked between chunks whether the client is still there: a browser that has taken what
    it wanted and hung up leaves a read behind, and a read nobody is waiting for still holds a
    thread the API and every other stream share.
    """
    remaining = span.length
    # Opening goes to a thread with the seek, for the reason the reads do. Closing stays here: the
    # `with` closes the handle down every path out of this generator.
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


# --- The HLS playlist ---------------------------------------------------------------------------


def plan_query(plan: policy.Plan) -> str:
    """The plan, as the query string that carries it from one request to the next.

    HLS is several requests (a playlist, then a segment at a time), and the server holds nothing
    between them. So the decision made once in `/playback` has to travel, or every later request
    re-derives it from nothing and gets the default.

    Getting this wrong is not a small bug and it fails silently: without the route a remux would
    re-encode (full CPU and a generation of quality, to solve a container problem), and without the
    height a file the policy decided to reduce would be converted at full size, which is exactly
    the case where reducing it was the thing keeping playback from stalling. It is validated on the
    way back in by `router._plan_from_query`, never trusted.
    """
    parts = [f"route={plan.route.value}"]
    if plan.scale_height is not None:
        parts.append(f"height={plan.scale_height}")
    return "?" + "&".join(parts)


def source_bitrate(asset: Asset) -> int:
    """Roughly what one second of the file itself weighs, in bits.

    Averaged from the size and the length, which is all a stored file will say without being read
    again. It is an average over the whole file rather than a peak, so a busy passage costs more
    than this and a still one less, which is exactly as true of the rungs' figures beside it, so
    the comparison a player makes between them is still a fair one.
    """
    if not asset.size_bytes or not asset.duration_ms or asset.duration_ms <= 0:
        return tuning.FALLBACK_SOURCE_BITRATE
    return max(1, round(asset.size_bytes * 8 * 1000 / asset.duration_ms))


def master_playlist(asset: Asset, *, base_url: str) -> str:
    """The list of sizes this file can be watched at, for a player to choose between.

    THIS is what makes "Auto" work, and it is worth being clear about why nothing else was
    needed. hls.js already measures how long every piece took to arrive and compares that against
    what each variant here says it weighs; given more than one variant it moves between them on its
    own, continuously, for the whole video. A connection test written by hand would measure one
    moment and then go on being believed after it stopped being true.

    Smallest first. A player that has not measured anything yet takes the first variant, so
    starting at the bottom means the first few seconds arrive quickly on a slow connection and the
    ladder climbs from there, where starting at the top means a stall before anything is known.

    The source's own size is last and always present, so "as it was made" is reachable from the
    menu even for a file with no rungs under it at all.
    """
    lines = ["#EXTM3U", "#EXT-X-VERSION:7"]
    floor = 0
    for rung in (*reversed(policy.rungs(asset.width, asset.height)), None):
        height = rung.height if rung else None
        out_height = height or asset.height
        out_width = (
            policy.scaled_width(asset.width, asset.height, height) if height else asset.width
        )
        # By the rung's SHORT side, which is what the table is keyed by and what a rung's name
        # means. Looked up by its height, a portrait rung (852, 1280, 1920 tall) would match
        # nothing and fall to the fallback, and every variant of a portrait file would be declared
        # to cost the same twenty megabits, one more than the last, telling hls.js nothing.
        weight = tuning.LADDER_BITRATES[rung.short] if rung else source_bitrate(asset)
        # A VARIANT MAY NEVER DECLARE LESS THAN A SMALLER ONE, whatever the arithmetic said.
        #
        # The rungs come from a table and are ordered by construction; the source's figure is
        # measured from the file and can be anything at all: a badly-probed row, a still image
        # encoded as a minute of video, a file whose size or length was never recorded. Any of
        # those makes the FULL-SIZE variant look like the cheapest thing on offer, and hls.js
        # believes what it is told: somebody on a slow connection gets handed the 4K stream and it
        # never starts. Bounded here rather than in `source_bitrate`, because the invariant is
        # about the list rather than about any one file.
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
    """The query string one variant of the ladder is asked for by.

    Always the transcode route: a rung is by definition a re-encode, and a file being watched at a
    size other than its own has already left every cheaper path behind.
    """
    if height is None:
        return f"?route={policy.Route.TRANSCODE.value}"
    return f"?route={policy.Route.TRANSCODE.value}&height={height}"


def playlist(asset: Asset, *, base_url: str, query: str = "") -> str:
    """The media playlist: a list of segments and how long each one lasts.

    `EXT-X-PLAYLIST-TYPE:VOD` tells the player the list is complete and will not change, which is
    what lets it seek anywhere immediately instead of waiting to discover the end. The segments do
    not exist yet (they are made when they are asked for), and the player has no way to know
    that, which is the whole trick.

    Every URL in here carries `query`, which is how the playback decision reaches the requests that
    act on it. See `plan_query`.

    `EXT-X-TARGETDURATION` is worked out from the longest segment there actually is rather than
    from the nominal length: the last one is short, and a player told a target shorter than a
    segment it then receives is entitled to treat the playlist as broken.
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


# --- Producing segments -------------------------------------------------------------------------


def segment_key(asset_id: str, index: int, plan: policy.Plan) -> str:
    """The cache key for one segment.

    The plan is part of the key, not just the asset and the index. Two browsers can be watching the
    same file with different answers (one remuxing, one transcoding, one downscaled and one not),
    and serving a segment built under one plan to a client that asked under another is a stream it
    cannot decode. The height is in there for the same reason.
    """
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
        # The ONE source of truth for what is encoding and where the decoding happens. The card can
        # be given up on part way through a session, so two fields set at boot from the hardware
        # report would be two answers to one question, with the stale one still readable and no
        # way to tell them apart at a call site.
        self._accelerator = accelerator
        self._device = device
        self._cpu_count = max(1, cpu_count)
        # What the encoder is currently thought to manage, in encoded pixels a second. None for
        # the processor, which has no separate budget: its work is already in the core count.
        self._encoder_rate = tuning.STARTING_ENCODER_PIXELS_PER_SECOND.get(
            accelerator.encoder.value
        )

    @property
    def encoder(self) -> Encoder:
        """What is encoding a segment right now.

        Asked of the accelerator every time rather than remembered, because the card can be given
        up on part way through a session. Everything that decides anything from the encoder has to
        ask here or it goes on believing a card that stopped being used.
        """
        return self._accelerator.encoder

    @property
    def decode(self) -> tuple[str, ...]:
        """Whether the decoding is on the card right now. Same reason as above."""
        return self._accelerator.decode

    @property
    def encoder_rate(self) -> float | None:
        """How fast the encoder is currently believed to be, for the smoothness projection.

        None when the processor is doing the encoding, which is what tells `policy` to use its
        single-budget arithmetic.

        **Also None once the card has been given up on**, and that matters more than it reads. This
        number is how the quality projection decides what this machine can keep up with, and the
        starting figure for a card is many times a processor's. Left in place after a fall back to
        the processor it would go on promising a quality nothing can render in time, and what
        somebody would see is a video that stalls, with the encoder that caused it no longer in
        use and nothing on any screen connecting the two.
        """
        return None if self.encoder is Encoder.CPU else self._encoder_rate

    def observe(
        self, *, asset: Asset, plan: policy.Plan, elapsed: float, video_seconds: float
    ) -> None:
        """Learn the encoder's real speed from a segment that was just built.

        **Measured from real work rather than probed at start-up, and that is the durable choice.**
        A start-up probe costs a quarter of a second and measures ffmpeg's test-pattern generator
        as much as the encoder; worse, it answers for an idle machine, which is not the machine
        anybody watches a video on. Every segment already reports how long it took, so the number
        that matters is free and arrives from the work actually being done.

        **The decode is subtracted first.** A segment is timed end to end (open, seek, decode,
        encode, write), and calling that the encoder's speed would charge the card for the
        processor's reading. What is left over is the part the encoder is answerable for.

        A reading is thrown away rather than believed when what remains is too small to be a
        measurement of anything: a segment that spent all its time decoding says nothing about how
        fast the encoder is, only that it was not the thing holding it up.
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
        """The path to one segment, producing it if it is not already cached.

        A cache hit is the common case during ordinary playback: the player reads ahead, so by the
        time a segment is displayed it has usually been fetched already.
        """
        key = segment_key(asset.id, index, plan) if index >= 0 else init_key(asset.id, plan)
        hit = self._cache.touch(key)
        if hit is not None:
            return Segment(path=hit, cached=True, ephemeral=False)

        await self._produce(asset, index=index, plan=plan)

        path = self._cache.path_for(key)
        if not await asyncio.to_thread(path.exists):
            raise SegmentUnavailable(f"segment {index} of {asset.id} could not be produced")
        # A segment the cache refused for being oversized is still on disk and still perfectly
        # good. It is served and then deleted: serving and caching are different decisions, and
        # conflating them would put the cache permanently over its cap.
        return Segment(path=path, cached=False, ephemeral=key not in self._cache)

    async def _produce(self, asset: Asset, *, index: int, plan: policy.Plan) -> None:
        """Run the transcode through the queue, so the one-at-a-time cap holds."""
        job_id = await self._queue.enqueue(
            TRANSCODE,
            # Ids, never paths (the queue enforces this at runtime, and the reason is that a path
            # in a payload is a path in every log line and diagnostics export the job appears in).
            {
                "asset_id": asset.id,
                "index": index,
                "route": plan.route.value,
                "scale_height": plan.scale_height,
            },
            # A segment nobody is waiting for any more is worth nothing, so this does not retry.
            # The player asks again if it still wants it, which is a better retry than ours.
            max_attempts=1,
            # AND IT IS THE DEFINITION OF WAITED-ON: an HTTP request is open and a person is
            # watching a spinner until this returns. At the ordinary priority it would sit behind
            # every thumbnail, face pass and fingerprint an import had queued, and `_await_job`
            # below gives up after `SEGMENT_TIMEOUT_SECONDS`, so on a busy library the wait would
            # end in a failure rather than in a picture.
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

        Called by the job handler, which is the only thing that runs it, so it inherits the
        one-at-a-time cap for free rather than needing a lock of its own.

        An init segment is not rendered separately. ffmpeg produces a self-initialising fragment,
        so segment zero is rendered and cut in two: the header half becomes the init segment and
        the rest becomes segment zero. That is one ffmpeg invocation for both, and it guarantees
        the header actually describes the stream the segments carry: a separately rendered header
        can disagree with them, and a player given a header that does not match plays nothing.
        """
        wanted = max(index, 0)
        key = segment_key(asset.id, wanted, plan)
        scratch = self._cache.path_for(f".build-{key}")
        # Resolved ONCE, here, off the loop, not in `segment_args`, which would put a filesystem
        # call inside an argv builder and on the event loop (milliseconds, and tens of them at
        # worst, on an SMB library, per segment, doubled whenever the encoder falls back). Somebody
        # is waiting on this path, so it is the last place that should stop everything else to ask
        # a share a question.
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

        # **THE FALLBACK TO THE PROCESSOR**, as the preview builder has. Without it a card that
        # refuses a segment, where somebody is actually waiting, goes straight to
        # `SegmentUnavailable`: a video that will not play, with the reason in a log. A card can
        # refuse this way, `CreateInputBuffer failed: invalid param (8)` on a 10-bit source.
        #
        # The job above is enqueued with `max_attempts=1` on purpose (a segment nobody is waiting
        # for any more is worth nothing), so the queue will never supply this. It has to be
        # here, inside the one attempt, and it has to be the same rule the preview uses, which is
        # why the rule lives in the kernel and neither slice owns a copy of it.
        elapsed = await self._accelerator.run(render_with, frame=(asset.width, asset.height))

        # Every read and write below is a whole segment (megabytes), and this runs once for
        # every segment of every stream. Done on the loop it stops the API, the job feed and every
        # other stream for the length of the write, which is the same fault `read_range` describes
        # from the serving side. So the disk work goes to a thread in one hop and the cache's own
        # bookkeeping stays here, on the loop, where it is the only thing touching it.
        header = self._cache.path_for(init_key(asset.id, plan))
        destination = self._cache.path_for(key)

        def place() -> tuple[int, int]:
            """Split the rendered fragment, write both halves, and report their sizes."""
            try:
                init, media = split_fragment(scratch.read_bytes())
            finally:
                scratch.unlink(missing_ok=True)

            # The header is identical for every segment of a stream, so it is written once and
            # left alone after that.
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
            # Rendered, and about to be served, but never kept. See `Segment.ephemeral`.
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
