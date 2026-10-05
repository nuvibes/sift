# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every feature that touches media needs: ffmpeg, and the way from an asset to a file.

This module exists because two slices needed the same three things and slices may not import one
another. `media_jobs` builds thumbnails, previews and sprite sheets; `player` converts video as it
is watched. Both spawn ffmpeg, both have to choose an encoder, and both have to get from an asset
id to a file on disk that can actually be opened. There is one ffmpeg argv builder: a second one
belongs here or in the kernel, never in the slice that happened to need it.

So this is the shared half, and only the shared half. What stays in each slice is the part that is
genuinely its own: `media_jobs` keeps its thumbnail, preview and tile argument builders, and
`player` keeps its segment and remux ones. Those are different questions with different answers,
and merging them would produce one function with a flag for every caller.

Two invariants hold everywhere here, and both are architectural rather than stylistic:

- **ffmpeg is a subprocess, never a binding.** It is given an argument list, never a shell string,
  so an argument is only ever an argument: a filename containing a space, a quote or a semicolon
  is a filename.
- **Every argument builder is a pure function returning `list[str]`.** What ffmpeg is asked to do
  is decided somewhere that can be tested without spawning anything, which matters most for the
  mistakes that produce correct-looking output.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import os
import shutil
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from sift.kernel import budget, hardware, heif, lanes, subprocess
from sift.kernel.config import Settings
from sift.kernel.content import Asset, ContentStore, Location, LocationStatus
from sift.kernel.hardware import HardwareReport
from sift.kernel.log import get_logger

log = get_logger(__name__)


class FFmpegError(Exception):
    """ffmpeg or ffprobe failed, or could not be run at all.

    Carries the tool's own stderr where there is any. Throwing that away turns a diagnosable
    failure into "it did not work", and the operator reading the job's error column is the person
    who needs it.
    """


class Encoder(StrEnum):
    """The video encoders Sift will ask for, by ffmpeg's name for them.

    H.264 only, deliberately. It is the one codec every browser on every site decodes, which is
    exactly what is wanted from a compatibility encode: reaching for anything else would produce
    a file some clients cannot play, to solve a problem that was about playability.
    """

    CPU = "libx264"
    NVENC = "h264_nvenc"
    QSV = "h264_qsv"
    VAAPI = "h264_vaapi"


#: Which hardware encoder to prefer when a machine has several.
#: NVENC first because it is the most consistently fast of the three and the most widely deployed
#: on machines that have any GPU at all. Quick Sync and VAAPI follow; both are real and both are
#: better than the CPU for this work.
ENCODER_PREFERENCE: tuple[Encoder, ...] = (Encoder.NVENC, Encoder.QSV, Encoder.VAAPI)

#: The most memory ffmpeg may allocate for a single buffer, as a string because that is how it is
#: passed. A malformed file can otherwise ask for an enormous allocation before anything notices it
#: is malformed.
MAX_ALLOC_BYTES = str(1 << 30)

#: How far apart two running times may be and still be the same video, in milliseconds.
#: Here rather than in one of the two places that ask, because both are asking the identical
#: question of the identical evidence: are these two the same recording, given that they run for
#: nearly the same time. One compares two files in this library; the other compares a file against
#: an entry in a public stash-box. A second copy of this number would drift, and the drift would
#: show up as the two disagreeing about one pair with nothing to say which was right.
#: Ten seconds rather than exact. Submissions of one video to a public stash-box commonly differ by
#: several seconds, because different encodes trim differently. Demanding equality would throw away
#: exactly the pairs this exists to find.
#: A wrong value here shows up as MORE to look at, which somebody notices, rather than as fewer,
#: which nobody does. That asymmetry is why this is safe to have and a similarity threshold is not.
MAX_DURATION_GAP_MS = 10_000

#: Prepended to every invocation, wherever it is built.
#: `-nostdin` matters more than it looks: without it ffmpeg reads the parent's standard input, and a
#: tool spawned by a server has no business consuming that. `-y` overwrites, which is safe because
#: every destination here is a temporary file this process just named.
BASE_FLAGS: tuple[str, ...] = (
    "-hide_banner",
    "-loglevel",
    "error",
    "-nostdin",
    "-y",
    "-max_alloc",
    MAX_ALLOC_BYTES,
)


#: How many jobs the worker pool is actually running, as the Performance screen currently says.
#: `None` until the pool has been configured, which is the case at boot and in any test that calls
#: the builders below directly; the hardware default stands in until then.
#: `background_threads` divides the machine by "how many of these run at once", and
#: `hardware.worker_concurrency` is only the number chosen from the cores at start-up. Raising
#: "Jobs at once" on the Performance screen (which the tuning self-test recommends) moves the
#: real number and not that one: with 8 jobs running on a 16-thread machine and a start-up figure
#: of 4, each ffmpeg would get 4 threads, 32 on 16: the oversubscription this cap exists to
#: prevent.
#: Set from `read_pool_config`, on the timer that already reconfigures the pool, the shared thread
#: pool and the database's readers: one answer, read by everything sized from it, rather than
#: four places each deciding what "the worker count" means.
_jobs_at_once: int | None = None


def set_jobs_at_once(workers: int) -> bool:
    """Record how many jobs really run at once. True when the number actually changed."""
    global _jobs_at_once
    if workers < 1 or workers == _jobs_at_once:
        return False
    _jobs_at_once = workers
    return True


def jobs_at_once(settings: Settings) -> int:
    """How many jobs run at once: what the pool was told, or the hardware answer before it was."""
    if _jobs_at_once is not None:
        return _jobs_at_once
    return max(1, hardware.worker_concurrency(settings))


#: The share of the device background tools may use right now, as the pool's last reconfigure
#: said: how many workers it runs and what percent of the device they share. None until the pool
#: has said, which reads as the whole device shared by `jobs_at_once`.
_share: tuple[int, int] | None = None


def set_share(*, running: int, percent: int) -> bool:
    """Record the share in force: `running` workers sharing `percent` of the device.

    Also holds every background tool to its threads through the operating system
    (`subprocess.hold_background`) while the share is less than the whole device, because a
    tool's own thread flags reach one decoder each and a tool reading several inputs runs several.
    With the whole device in force nothing is held, and a tool may use what it can. True when the
    share actually changed.
    """
    global _share
    share = (max(1, running), min(budget.WHOLE_DEVICE, max(1, percent)))
    if share == _share:
        return False
    _share = share
    cores = os.cpu_count() or 1
    threads = budget.tool_threads(cores, running=share[0], percent=share[1])
    held = share[1] < budget.WHOLE_DEVICE
    subprocess.hold_background(budget.processor_rate(threads, cores) if held else None)
    return True


def background_threads(settings: Settings) -> int:
    """How many threads one background ffmpeg may use.

    **ffmpeg helps itself to the whole machine unless told not to.** Left alone it sizes its thread
    pool from the core count, per process, and the worker pool runs several at once. The pool is
    sized on the assumption that a job costs about a core, which is where "one core is left for
    everything that is not a job" comes from; that assumption is what breaks. Eight workers can
    produce over a hundred runnable threads, and everything the app itself wants to do (answer a
    request, read a row) queues behind them for a timeslice.

    What that looks like from the outside is a slow database, which is the trap: a query that runs
    in under a millisecond when asked for directly takes hundreds inside a request, because the time
    is spent waiting to be scheduled rather than working.

    So each background job gets a share of the machine rather than all of it. The share is
    deliberately generous (encoding parallelizes well and a job that takes twice as long is not
    free), but it is bounded, which is the whole point.

    Not applied to the player's live transcode. That one is capped at a single job, somebody is
    waiting on the other end of it, and it is the one place where using the machine hard is correct.

    **The share in force, never the cores over fewer workers.** While somebody is using the
    computer the pool runs a share of its workers (`kernel.attention`), and the threads here are
    that share of the logical processors divided by the workers still running, so the tasks
    together take the share and no more. Dividing all the cores by the smaller count would hand
    each remaining task more threads and give the processor straight back to the same work, which
    is the lag the step back exists to remove. See `set_share`.
    """
    cores = os.cpu_count() or 1
    if _share is None:
        return budget.tool_threads(
            cores, running=jobs_at_once(settings), percent=budget.WHOLE_DEVICE
        )
    running, percent = _share
    return budget.tool_threads(cores, running=running, percent=percent)


def background_flags(settings: Settings) -> tuple[str, ...]:
    """`BASE_FLAGS` plus a thread cap. What every job-driven ffmpeg should be built from.

    Two flags, because ffmpeg has two thread pools here and one flag does not reach both. In this
    position, before the input, `-threads` caps DECODING and `-filter_threads` caps the filter
    graph. **Capping the encoder needs the same flag again in the output position**. See
    `preview_args`, the one builder here that runs a real video encode.
    """
    share = str(background_threads(settings))
    return (*BASE_FLAGS, "-threads", share, "-filter_threads", share)


def choose_encoder(report: HardwareReport) -> Encoder:
    """The best encoder this machine can actually run.

    Reads the hardware report rather than probing, because the report already asked the only
    question worth asking: an encoder counts only if ffmpeg was built with it *and* the device it
    needs is present. A box with NVENC compiled in and no NVIDIA card in it (which is an ordinary
    desktop Linux install) would otherwise pick a path that fails, or silently runs slower than
    the CPU it was avoiding.

    CPU is not a failure case. It is the answer for most machines Sift runs on.
    """
    usable = set(report.transcode_encoders)
    for encoder in ENCODER_PREFERENCE:
        if encoder.value in usable:
            return encoder
    return Encoder.CPU


#: HDR to SDR, in software, in front of everything else in a filter chain: linear light at a
#: 100-nit reference, the Hable curve (the one that keeps highlights rather than clipping them),
#: then back to BT.709 television range and 8-bit 4:2:0. `zscale` is what the vendored build
#: ships for this; the `tonemap` filter only maps, so the colour-space steps sit either side of
#: it. One chain for every encode that forces 8-bit 4:2:0 (the player's segments and the
#: compress copy), so the two cannot drift into two different pictures of one file.
HDR_TO_SDR = (
    "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
)


def decode_flags(report: HardwareReport) -> tuple[str, ...]:
    """Input flags that move DECODING onto the graphics card, where the machine has one.

    The encoder is the visible half and it is not the expensive one. Against the exact command the
    preview job builds, decoding on the card cuts the processor seconds per clip several times over
    for 1080p H.264 and HEVC, and by more than ten times for 4K AV1, and the finished clips come out
    within one percent of the same size, so this changes what a derivative costs and not what it
    is.

    Processor time is the figure that matters rather than wall clock, and the two disagree. Setting
    up a context on the card costs roughly six hundredths of a second per process, so a short job on
    a fast local file can finish slightly LATER while using a quarter of the processor. Sift runs
    eight of these at once, so cores are the contended resource and latency of one job is not.

    **`cuda`, named, rather than `auto`.** `-hwaccel auto` engages for H.264 and HEVC and silently
    does nothing at all for AV1: the one codec where the saving is largest. A flag that quietly
    declines is worse than no flag: it reads as covered.

    **A codec the card cannot decode looks after itself.** ProRes, which NVDEC will not touch,
    produces a correct clip with this flag present because ffmpeg decodes it in software without
    being asked to. So this degrades rather than failing, and the retry in the caller is a guard
    against the card being broken, not against the wrong codec.

    **Only NVIDIA, deliberately.** Quick Sync and VAAPI have the same flag and neither has been run
    or can be run in CI. Two more paths that look complete and have never been executed are worth
    less than one measured path and a gap somebody can see; adding one is a line here plus a
    machine to prove it on.

    **And only where a process decodes a RUN of video, not a single frame.** That rule is what
    keeps this from being sprinkled everywhere: the context costs the same six hundredths whether
    the process goes on to decode one frame or six hundred, so a thumbnail pays it for nothing:
    thirty scrubber tiles of an ordinary 1080p file cost the same processor time either way, with
    the wall clock doubled. The preview and the player's live segment decode runs; the
    thumbnail, the scrubber tile and the fingerprint frame decode one frame each.
    """
    return ("-hwaccel", "cuda") if report.cuda else ()


#: How many times in a row the card may let Sift down before it stops being asked.
#: Not one. A card is genuinely busy sometimes, and a single refusal is not evidence of anything:
#: latching off after one would take a machine that works off the fast path for the rest of the
#: session over a hiccup. Not twenty either: the whole point is to stop paying for a dead attempt on
#: every file in a library, and twenty files is already a lot of paying.
#: In a ROW, and any success puts it back to zero, which is what makes the number safe to be this
#: small. Several jobs run at once, so one genuinely awkward file among healthy ones has its failure
#: separated from the next by the successes either side of it and never reaches the count.
GIVE_UP_AFTER = 3

#: A frame with a side shorter than this goes to the processor without asking the card. Graphics
#: cards refuse frames below a minimum of their own (48 pixels for one maker's decoder, more for
#: some encoders), and such a refusal is about the file, not the card, so it must never count toward
#: giving up on it. A frame this small also costs the processor next to nothing.
CARD_SMALLEST_SIDE = 160


class Accelerator:
    """What this machine's graphics card is worth asking for, and whether it still is.

    **The state a start-up answer cannot hold.** Choosing an encoder and choosing a decoder are
    both questions about the machine, answered once at start-up from the hardware report, and a
    report can be wrong in the one direction nothing checks. It says an encoder is compiled in and
    its device is present, which is the most anyone can know without trying; it cannot say the
    driver is too old, or that the card is already spoken for, or that this ffmpeg and this driver
    disagree.

    Without a memory, a machine like that pays for a failed attempt on **every single file, for
    ever**, and the only trace is a line in a log nobody reads: a wasted attempt per file,
    invisible, because the retry works.

    So this counts. Three real failures in a row and it stops asking, says so once, and everything
    carries on at processor speed, which is where a machine with no card has been all along.

    **A failure only counts against the card when the processor then SUCCEEDS.** That distinction is
    the whole accuracy of the thing. A file that is malformed fails on both paths and is evidence
    about the file; a file that fails on the card and renders perfectly without it is evidence about
    the card. Counting the first sort would let a handful of broken files turn the acceleration off
    for a library that is fine.

    **One of these per process, because there is one card.** The preview builder and the player both
    read it, so a fault either of them meets is a fault the other stops paying for. It lives here
    rather than in either of them for the ordinary reason: a feature may not import another feature.

    **What it cannot tell apart, said plainly:** "this card will not do this KIND of file" and "this
    card is broken" look identical from here. Three unusual files in a row (an encoder refusing a
    pixel format it cannot ingest, say) would turn acceleration off for a session that would have
    been fine. The trade is deliberate and it is asymmetric: being wrong that way costs a slower
    session and clears on the next start, while being wrong the other way costs a wasted attempt
    per file for as long as the library exists. Distinguishing them
    properly means knowing which refusals are about content, which is a list that would go stale
    against every ffmpeg release.

    Not locked, deliberately. Every worker is a task on one event loop, so a read and the write that
    follows it cannot be interleaved unless something waits in between, and nothing here does.
    """

    def __init__(self, report: HardwareReport, *, patience: int = GIVE_UP_AFTER) -> None:
        self._encoder = choose_encoder(report)
        self._decode = decode_flags(report)
        self._patience = max(1, patience)
        self._failures = 0
        self._given_up = False

    @property
    def offers_hardware(self) -> bool:
        """Whether anything here is still being asked of the card."""
        return not self._given_up and (self._encoder is not Encoder.CPU or bool(self._decode))

    @property
    def state(self) -> str:
        """One word for what the card is doing, for a record of a run to keep.

        THREE answers rather than the two `offers_hardware` gives, and the third is the whole
        reason this exists. A run that used the processor because this machine has no card and a
        run that used the processor because the card had let Sift down three times both read as
        "no hardware" from the boolean, and they are the two cases somebody comparing one run with
        another most needs to tell apart: the first is what this machine always does, and the
        second is a machine that was doing better an hour ago. Without it, a pass that suddenly
        takes four times as long has no explanation anywhere.

        `off` covers both machines with nothing to offer and a build with no hardware encoder or
        decoder compiled in, which are the same fact as far as a run is concerned.
        """
        if self._given_up:
            return "latched_off"
        if self._encoder is Encoder.CPU and not self._decode:
            return "off"
        return "on"

    @property
    def encoder(self) -> Encoder:
        """The encoder to use now. The processor, once the card has been given up on."""
        return Encoder.CPU if self._given_up else self._encoder

    @property
    def decode(self) -> tuple[str, ...]:
        """The decode flags to use now. Nothing, once the card has been given up on."""
        return () if self._given_up else self._decode

    async def run[T](
        self,
        attempt: Callable[[Encoder, tuple[str, ...]], Awaitable[T]],
        *,
        frame: tuple[int | None, int | None] | None = None,
    ) -> T:
        """Do the work on the card, and again on the processor if the card would not.

        A method rather than something each caller assembles, because the bookkeeping is the part
        that gets forgotten: a caller that retries but never says whether the retry was needed is a
        caller with no memory, which is the shape this class exists to prevent. Handed the work as a
        function of what to use, there is exactly one place that can forget, and it is this one.

        `attempt` is called with an encoder and the decode flags, and is expected to raise
        `FFmpegError` when ffmpeg refuses. `ValueError` counts too: a hardware path built without
        the device it needs raises that before ffmpeg is ever spawned, and it is the same failure.

        `frame` is the source's width and height where they are known. A frame with a side under
        `CARD_SMALLEST_SIDE` is done on the processor straight away and says nothing about the card.
        """
        refusal: Exception | None = None
        if self.offers_hardware and not _too_small_for_the_card(frame):
            try:
                result = await attempt(self.encoder, self.decode)
            except (FFmpegError, ValueError) as exc:
                refusal = exc
            else:
                self._failures = 0
                return result

        result = await attempt(Encoder.CPU, ())
        # Only reached when the processor succeeded, so a refusal above was the card's fault and
        # not the file's. A file that neither path can render raised on the line before this one.
        if refusal is not None:
            self._blame(refusal)
        return result

    def _blame(self, refusal: Exception) -> None:
        """Record a failure the processor went on to prove was the card's."""
        self._failures += 1
        log.warning(
            "media.accelerator_fell_back",
            encoder=self._encoder.value,
            hardware_decode=bool(self._decode),
            failures=self._failures,
            reason=str(refusal),
        )
        if self._failures < self._patience:
            return

        self._given_up = True
        # Once, and at warning level, because it is the sentence that explains every slow thing
        # that happens afterwards. Said on every job it would be noise; said nowhere it is a machine
        # that quietly halved its own speed with no way to find out why.
        log.warning(
            "media.accelerator_given_up",
            encoder=self._encoder.value,
            hardware_decode=bool(self._decode),
            failures=self._failures,
            detail=(
                "The graphics card refused this work several times in a row and the processor "
                "did it instead. Sift will stop asking the card until it is restarted."
            ),
        )


def _too_small_for_the_card(frame: tuple[int | None, int | None] | None) -> bool:
    if frame is None:
        return False
    return any(side is not None and 0 < side < CARD_SMALLEST_SIDE for side in frame)


def render_node() -> str | None:
    """The DRI render node VAAPI and Quick Sync encode through, if there is one.

    The first one, where a machine has several. Picking between two GPUs is a real question and
    this is not the module that answers it.
    """
    try:
        nodes = sorted(
            entry for entry in Path("/dev/dri").iterdir() if entry.name.startswith("renderD")
        )
    except OSError:
        return None
    return str(nodes[0]) if nodes else None


def cover_picture_args(
    destination: Path,
    *,
    height: int,
    quality: int,
    settings: Settings,
) -> list[str]:
    """Re-encode a picture somebody uploaded into Sift's own JPEG, read from standard input.

    Here rather than in a slice for the reason this module exists: there is one ffmpeg argument
    builder, and an entity's cover is a kernel concern: `kernel.covers` serves all six of them and
    may not import a slice to receive one.

    **The bytes arrive on the pipe and are never a file.** That is the security property of the
    upload feature and it is stronger than writing the original and deleting it afterwards: there is
    no moment at which anything a stranger chose exists on the disk under a name, so there is
    nothing for another process to open, nothing for a scan to find, and nothing left behind by a
    crash between the write and the delete. `faces.crop.encode_args` already feeds ffmpeg this way,
    so the pattern is one that runs here rather than one being tried for the first time.

    **What comes out is Sift's own picture, never the uploader's file.** Re-encoding is what
    disposes of camera metadata, of a polyglot file that is a picture and an archive at once, and of
    every decoder bug in every browser that will ever open what is served, because what is served
    was written by this ffmpeg, not by whoever sent it.

    `-frames:v 1` because an animated GIF, a WebP with frames in it, or a whole video are all things
    somebody will drop on this, and every one of them has a first frame. Without it ffmpeg is asked
    to write many frames to one filename and fails, which would refuse a file that has a perfectly
    good picture at the front of it.

    NO INPUT SEEK, and that is not an omission: see `media_jobs.ffmpeg._seek`, which records what
    `-ss 0` does to a still: it lands on the only frame's timestamp, discards it as already passed,
    and exits 0 having written nothing.

    `-an` drops audio rather than letting ffmpeg fail on having nowhere to put it, and
    `-map_metadata -1` states the metadata rule in the command as well as relying on the re-encode
    for it.
    """
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        "-i",
        "pipe:0",
        "-frames:v",
        "1",
        "-map_metadata",
        "-1",
        "-an",
        "-vf",
        f"scale=-2:min({height}\\,ih)",
        "-q:v",
        str(quality),
        str(destination),
    ]


def cover_frame_args(
    source: Path,
    destination: Path,
    *,
    crop: str,
    quality: int,
    settings: Settings,
) -> list[str]:
    """Cut the window a cover is drawn as out of its picture, into Sift's own JPEG.

    The picture is always one Sift wrote itself (a still in the cache, or an uploaded cover that
    `cover_picture_args` already re-encoded), so it is read by name, not piped: there are no
    stranger's bytes here to keep off the disk. `crop` is `CoverFrame.crop()`, a filter in terms of
    the picture's own size, so this builder knows nothing of fractions.

    NO INPUT SEEK, for the reason `cover_picture_args` gives: `-ss 0` before a still discards the
    only frame and exits 0 having written nothing. `-frames:v 1` because the input is one picture
    and the output is one file.
    """
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-map_metadata",
        "-1",
        "-an",
        "-vf",
        crop,
        "-q:v",
        str(quality),
        str(destination),
    ]


#: What ffmpeg says when the DATA is broken, rather than when the tool or the machine could not do
#: its job. Matched against the stderr of a refused run, case-insensitively.
#: The distinction is the whole of why this list exists and not a shorter rule. A NAS that blinked,
#: a file still being written, a device that was busy: all of those fail and all of them succeed on
#: the next attempt, so treating every refusal as final would take a perfectly good file out of the
#: library for ever with nothing to notice. Only the other direction is safe to be final: a stream
#: the decoder cannot parse does not start parsing.
#: Every phrase here is one a real broken file produces (a truncated H.264 stream, a corrupt PNG),
#: each read three times before being given up on. Adding to it is cheap and reversible; a phrase
#: that is too loose costs a file, so a phrase belongs here only once something has failed on it.
BROKEN_DATA_PHRASES: tuple[str, ...] = (
    # A truncated or corrupt H.264 stream: the container claims a packet far larger than the packet
    # it sits in. A file broken this way fails the same way on every attempt.
    "invalid nal unit size",
    "error splitting the input into nal units",
    "missing picture in access unit",
    # A corrupt still: one malformed PNG chunk, and the decoder giving up after reading frames that
    # are not pictures. A file broken this way fails the same way on every attempt.
    "invalid sbit size",
    "decoding error: invalid data found when processing input",
    "decode error rate",
)

# !! TWO PHRASES WERE CONSIDERED FOR THE LIST ABOVE AND LEFT OUT, and the reason is the whole rule.
# `moov atom not found` looks like the safest entry there could be (the header is there and the
# media is not) and it is the message a file STILL BEING WRITTEN gives. Sift watches folders, so
# a file part-way through a copy onto a share is an ordinary thing to meet, and it decodes perfectly
# well a minute later. Permanent is the one answer that cannot be taken back by the next scan.
# The bare `invalid data found when processing input` is ffmpeg's generic text for bad input and is
# produced by the DEMUXER as readily as by a decoder, so it covers a partial read too. The prefixed
# form in the list is a decoder that read frames and found them not to be pictures, which is a
# statement about the bytes rather than about how much of them arrived.


def is_broken_data(detail: str) -> bool:
    """Whether what the tool said means the bytes are broken, rather than the moment.

    Read off ffmpeg's own words, which is the only evidence there is: exit codes do not distinguish
    a corrupt stream from a share that went away, and both arrive here as a non-zero exit with a
    message. The default is False, so anything unrecognised keeps its retries: an unknown failure
    is treated as the transient kind, because that error is recoverable and the other is not.
    """
    lowered = detail.lower()
    return any(phrase in lowered for phrase in BROKEN_DATA_PHRASES)


async def run(
    argv: list[str],
    *,
    time_limit: float,
    capture: bool = False,
    priority: subprocess.Priority = subprocess.Priority.NORMAL,
    stdin: bytes | None = None,
    reads: Path | None = None,
) -> bytes:
    """Run ffmpeg or ffprobe to completion. Raises `FFmpegError` on anything but success.

    `reads` is the library file the tool is about to open, when it opens one. A read of a library
    file takes a place in its storage's lane first (see `kernel.lanes`), so a network share is
    never asked to serve more seeking readers than it can. A tool that reads nothing of the
    library (an encode of Sift's own scratch, a stitch of tiles it just wrote) passes nothing and
    waits for nobody.

    `time_limit` is the caller's: a background sprite sheet wants a generous guard against a
    hang, a segment somebody is waiting on wants a budget.

    `stdin` hands the tool bytes on its input, for the one caller that gives ffmpeg pictures rather
    than a filename to read.

    `priority` is the caller's for the same reason and splits the same two apart: the sheet nobody
    is waiting for yields the machine to the segment somebody is. It defaults to normal so that a
    new caller is at full speed unless it says otherwise: the wrong default in the other
    direction is a person watching a spinner, which is worse than a thumbnail arriving late.
    """
    try:
        async with lanes.reading_if(reads):
            result = await subprocess.run(
                argv, time_limit=time_limit, capture_stdout=capture, priority=priority, stdin=stdin
            )
    except subprocess.SubprocessError as error:
        raise FFmpegError(str(error)) from error

    if result.returncode != 0:
        said = result.stderr.decode("utf-8", "replace").strip()
        detail = said or subprocess.unsaid(result.returncode)
        raise FFmpegError(f"{Path(argv[0]).name} failed: {detail}")
    return result.stdout


async def run_json(
    argv: list[str],
    *,
    time_limit: float,
    priority: subprocess.Priority = subprocess.Priority.NORMAL,
    reads: Path | None = None,
) -> dict[str, Any]:
    """Run a tool that answers in JSON (ffprobe) and parse what it said."""
    raw = await run(argv, time_limit=time_limit, capture=True, priority=priority, reads=reads)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise FFmpegError(f"{Path(argv[0]).name} did not return JSON") from error
    if not isinstance(payload, dict):
        raise FFmpegError(f"{Path(argv[0]).name} returned something other than an object")
    return payload


# --- Many moments of one file, from one process ---------------------------------------------------
#
# A fingerprint reads thirty moments of a video, a scrub strip up to four hundred, a face pass up to
# sixty and a description thirty more. As a process each (open the file, seek, decode one frame,
# exit), that is dozens of launches per video and half of what a probe costs; over a network share
# each launch is an open and a seek across the wire. The seek itself is the same either way. What a
# single process saves is the launch, and the launch is most of it.
# One process takes every moment as its own seeked input: `-ss T1 -i file -ss T2 -i file ...`. That
# is the same `-ss` before the same `-i` per moment, so the frame that comes out of input N is the
# frame the per-moment command produces, byte for byte. Two ways of getting the frames back out,
# because two things want them:
# * **A raw stream on the pipe, in input order.** Each input is trimmed to its first frame, its
#   timestamps reset, and the thirty are joined by the concat filter; `-fps_mode passthrough` is
#   load-bearing, because without it the muxer sees thirty frames at the same timestamp and drops
#   all but two. A moment past the end of the file yields no frame, and the stream is then SHORT,
#   which cannot say which moment is missing. So a short stream is thrown away and that chunk is
#   read one process per moment, where a missing frame keeps its position: the per-frame path
#   kept on purpose.
# * **One image file per moment**, `-map N:v -frames:v 1 ... into/NNNN.ext`. A moment that read
#   nothing simply has no file, so its position is known without a fallback.
# The command line has a length, and on Windows it is 32,767 characters. Four hundred inputs of one
# long path is past it (`The filename or extension is too long` at about 210), so the moments
# are taken in chunks sized from the path they name.
# And every input is a decoder of its own, holding its reference pictures at the file's full size.
# A hundred inputs of a 4K file is a hundred 4K decoders in one process: tens of gigabytes. So a
# chunk is also sized by the file's picture against the memory one background tool plans to use
# (`subprocess.planned_memory`), and every input is given the same thread share as the first:
# an input option reaches only the input after it, and a decoder left to itself starts a thread,
# and a picture in flight, per processor.


@dataclass(frozen=True, slots=True)
class Moment:
    """One picture to take from a file: the input options that place it, `-ss 1.500` or nothing."""

    seek: tuple[str, ...]


#: The most characters a command line is allowed here. Windows refuses one past 32,767 outright;
#: the same budget everywhere, so a chunk on one site is a chunk on every site.
COMMAND_LINE_BUDGET = 30_000

#: What one input costs on the command line beyond its path and its seek: the `-i`, the spaces, its
#: entry in the filter graph or its output block. Generous, because the failure past the budget is
#: a refused launch and the cost of a smaller chunk is one more launch.
_PER_MOMENT_OVERHEAD = 160

#: What the fixed part of the command costs: the tool, the base flags and the output options.
_FIXED_OVERHEAD = 600

#: The per-input chain that keeps exactly the first frame and puts its clock back to zero. See the
#: note above on why the second half is there.
_FIRST_FRAME = "trim=end_frame=1,setpts=PTS-STARTPTS"


def chunk_size(source: Path, moments: Sequence[Moment]) -> int:
    """How many moments one command may name for this file, from what each costs on the line."""
    longest = max((sum(len(one) + 1 for one in moment.seek) for moment in moments), default=0)
    per_moment = len(str(source)) + longest + _PER_MOMENT_OVERHEAD
    return max(1, (COMMAND_LINE_BUDGET - _FIXED_OVERHEAD) // per_moment)


#: What one seeked input holds, in pictures of the file's own size: its reference pictures and the
#: ones in flight, and a few more for each decoding thread: about fifteen, and two and a half a
#: thread, for 4K and 1080p H.264 and 4K VP9 at one to four threads, rounded up.
PICTURES_PER_INPUT = 16
PICTURES_PER_THREAD = 3


@dataclass(frozen=True, slots=True)
class Picture:
    """The size of a file's decoded picture: what each of its decoders holds several of."""

    width: int
    height: int
    bytes_per_pixel: float


def input_bytes(picture: Picture, *, threads: int) -> int:
    """What one seeked input of a file with this picture holds while it decodes."""
    pictures = PICTURES_PER_INPUT + PICTURES_PER_THREAD * max(1, threads)
    return int(picture.width * picture.height * picture.bytes_per_pixel * pictures)


def moments_in_memory(picture: Picture, *, threads: int, budget: int) -> int:
    """How many seeked inputs of this picture one process may hold inside `budget` bytes."""
    return max(1, budget // max(1, input_bytes(picture, threads=threads)))


def picture_from(stream: dict[str, Any]) -> Picture | None:
    """The picture ffprobe describes for a video stream, or None where it does not say."""
    width, height = stream.get("width"), stream.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return None
    layout = str(stream.get("pix_fmt") or "")
    deep = any(mark in layout for mark in ("p9", "p10", "p12", "p14", "p16", "le", "be"))
    if "444" in layout or layout.startswith(("rgb", "bgr", "gbr", "argb", "abgr")):
        samples = 3.0
    elif "422" in layout:
        samples = 2.0
    else:
        samples = 1.5
    return Picture(width=width, height=height, bytes_per_pixel=samples * (2 if deep else 1))


def the_moving_picture(streams: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """The video stream that IS the file, out of however many it carries.

    Nearly every file has exactly one and this returns it untouched. Two cases have more, and taking
    the first of them picks the wrong one in both:

    - **A container with cover art.** An album cover or a poster frame is a video stream of one
      frame. ffprobe marks it `attached_pic`, and reading a file's shape off its poster gives the
      poster's dimensions and a frame rate of nothing.
    - **An animated AVIF or HEIF.** The still cover comes first and the moving frames second, and
      neither is marked: stream 0 can be one frame at 1 fps and stream 1 two hundred frames at
      30 fps. Read from stream 0, the file becomes a photograph: no length, no GIF chip, and no
      hover preview; and its faces and its fingerprints are taken from the one cover frame.

    The rule is "the one that runs the longest", by frames where the container states them and by
    duration otherwise. A file with one video stream never reaches the comparison at all, so nothing
    that works today can be changed by it, which matters, because most of a library is Matroska
    and MP4 where `nb_frames` is frequently absent and any rule resting on it would be a coin toss.

    Here rather than beside the probe that records it, because the frame readers below ask the
    same question of the same answer, and the kernel may not import a slice.
    """
    pictures = [one for one in streams if one.get("codec_type") == "video"]
    if len(pictures) <= 1:
        return pictures[0] if pictures else None

    # Cover art says so. Dropped rather than ranked, because a poster frame that happened to carry a
    # longer stated duration than the film would otherwise win on the comparison below.
    moving = [one for one in pictures if not _is_attached_picture(one)] or pictures
    if len(moving) == 1:
        return moving[0]

    def runs_for(stream: dict[str, Any]) -> tuple[int, float]:
        return (_whole(stream.get("nb_frames")) or 0, _positive(stream.get("duration")) or 0.0)

    # `max` keeps the FIRST of equals, so a file whose streams cannot be told apart comes back with
    # the same one this returned before: a tie must not silently change what a library records.
    return max(moving, key=runs_for)


def position_among_pictures(streams: Sequence[dict[str, Any]], video: dict[str, Any] | None) -> int:
    """Where the chosen picture sits among the file's video streams: what `v:K` names it by.

    By identity rather than by ffprobe's `index`, because `index` counts every stream (sound and
    subtitles too) and the specifier counts video streams only."""
    pictures = [one for one in streams if one.get("codec_type") == "video"]
    for position, one in enumerate(pictures):
        if one is video:
            return position
    return 0


def _is_attached_picture(stream: dict[str, Any]) -> bool:
    """Whether ffprobe marked this stream as cover art rather than as content."""
    disposition = stream.get("disposition")
    return isinstance(disposition, dict) and bool(disposition.get("attached_pic"))


def _whole(raw: object) -> int | None:
    if not isinstance(raw, str | int | float) or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _positive(raw: object) -> float | None:
    if not isinstance(raw, str | int | float) or isinstance(raw, bool):
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    # ffprobe reports N/A as the string, and a live stream as a negative. Neither is a duration.
    return seconds if seconds > 0 else None


@dataclass(frozen=True, slots=True)
class FrameClock:
    """How a file's frames are timed: what says exactly which frame a seek stops at.

    A seek to `T` keeps the first frame whose time, in the stream's own ticks, is at or past `T`
    plus the file's start, each converted to ticks the way the tool converts them (to the
    nearest tick, a half away from zero). The decode-once form counts time from the file's start,
    so the frame a seek stops at is the first whose ticks reach `at(T)`. Selecting by seconds
    instead lands a frame early or late wherever a moment falls inside a tick.
    """

    numerator: int
    denominator: int
    start_us: int
    """Where the file's own timeline starts, in microseconds: the container's start time."""

    def ticks(self, microseconds: int) -> int:
        """Microseconds in this stream's ticks, rounded as the tool rounds them."""
        unit = self.numerator * 1_000_000
        whole = (abs(microseconds) * self.denominator + unit // 2) // unit
        return -whole if microseconds < 0 else whole

    def at(self, microseconds: int) -> int:
        """The tick a seek to this moment stops at, counted from the file's start."""
        return self.ticks(microseconds + self.start_us) - self.ticks(self.start_us)


def clock_from(stream: dict[str, Any] | None, start_time: object) -> FrameClock | None:
    """The clock ffprobe describes for a stream and its file, or None where it does not say."""
    if stream is None:
        return None
    numerator, _, denominator = str(stream.get("time_base") or "").partition("/")
    if not numerator.isdigit() or not denominator.isdigit():
        return None
    if int(numerator) <= 0 or int(denominator) <= 0:
        return None
    start = microseconds_of(str(start_time)) if start_time is not None else None
    return FrameClock(int(numerator), int(denominator), start or 0)


@dataclass(frozen=True, slots=True)
class _Reading:
    """What the frame readers ask of a file before they read it: how big its picture is, which
    of its video streams is the one that moves (the `K` of `v:K`), and how its frames are timed."""

    picture: Picture | None
    stream: int
    clock: FrameClock | None = None


#: Readings already asked of a file, by where it is, its size and when it was written.
_PICTURES: dict[tuple[str, int, int], _Reading] = {}
_PICTURES_KEPT = 256

#: A file that cannot be read, or cannot be asked: no size, and the first stream, which is what
#: ffmpeg reads when no stream is named.
_UNREAD = _Reading(picture=None, stream=0)


async def _reading_of(
    source: Path, *, settings: Settings, priority: subprocess.Priority
) -> _Reading:
    """This file's picture and moving stream, asked of ffprobe once per version of the file."""
    try:
        found = await asyncio.to_thread(source.stat)
    except OSError:
        return _UNREAD
    key = (str(source), found.st_size, found.st_mtime_ns)
    if key in _PICTURES:
        return _PICTURES[key]
    argv = [
        settings.ffprobe_path,
        "-v",
        "error",
        "-select_streams",
        "v",
        "-show_entries",
        "stream=codec_type,width,height,pix_fmt,nb_frames,duration,time_base"
        ":stream_disposition=attached_pic:format=start_time",
        "-of",
        "json",
        str(source),
    ]
    try:
        said = await run_json(argv, time_limit=30, priority=priority, reads=source)
    except FFmpegError:
        reading = _UNREAD
    else:
        listed = said.get("streams")
        # Every stream asked for is a video stream (`-select_streams v`), whether or not the
        # answer repeats it.
        streams = [
            {"codec_type": "video", **one}
            for one in (listed if isinstance(listed, list) else [])
            if isinstance(one, dict)
        ]
        video = the_moving_picture(streams)
        container = said.get("format")
        reading = _Reading(
            picture=picture_from(video) if video is not None else None,
            stream=position_among_pictures(streams, video),
            clock=clock_from(
                video, container.get("start_time") if isinstance(container, dict) else None
            ),
        )
    if len(_PICTURES) >= _PICTURES_KEPT:
        _PICTURES.pop(next(iter(_PICTURES)))
    _PICTURES[key] = reading
    return reading


async def picture_of(
    source: Path, *, settings: Settings, priority: subprocess.Priority
) -> Picture | None:
    """The size of this file's decoded picture, asked of ffprobe once per version of the file.

    The picture of the stream that moves, which is the one every moment is read from. None where
    it cannot be read. A read of such a file is then sized by its command line alone, and the
    memory limit every background tool runs under is what stands behind it.
    """
    return (await _reading_of(source, settings=settings, priority=priority)).picture


async def moving_stream_of(
    source: Path, *, settings: Settings, priority: subprocess.Priority
) -> int:
    """Which of this file's video streams its moments are read from: the one that moves.

    The question the hover clip's recipe already asks (`Probed.picture_stream`), asked by the
    readers the faces, the fingerprints, the description and the scrub strip go through. Zero
    where the file cannot be asked, which is what ffmpeg reads when no stream is named.
    """
    return (await _reading_of(source, settings=settings, priority=priority)).stream


def _picked(stream: int) -> str:
    """How a command names the stream that moves. The first is named `v` (ffmpeg's first video
    stream), so an ordinary file's command names no stream number."""
    if stream < 0:
        raise ValueError("a video stream is counted from zero")
    return f"v:{stream}" if stream else "v"


async def moments_per_process(
    source: Path,
    moments: Sequence[Moment],
    *,
    settings: Settings,
    priority: subprocess.Priority,
) -> int:
    """How many of these moments one process may take: what fits the line and fits the memory."""
    fits_line = chunk_size(source, moments)
    if len(moments) <= 1:
        return fits_line
    picture = await picture_of(source, settings=settings, priority=priority)
    if picture is None:
        return fits_line
    fits_memory = moments_in_memory(
        picture,
        threads=background_threads(settings),
        budget=subprocess.planned_memory(jobs_at_once(settings)),
    )
    return min(fits_line, fits_memory)


def _inputs(source: Path, moments: Sequence[Moment], settings: Settings) -> list[str]:
    """One seeked input per moment, each held to the same thread share as the first."""
    share = str(background_threads(settings))
    argv: list[str] = []
    for moment in moments:
        argv += [*moment.seek, "-threads", share, "-i", str(source)]
    return argv


def raw_stream_args(
    source: Path,
    moments: Sequence[Moment],
    *,
    filters: str,
    pixel_format: str,
    settings: Settings,
    stream: int = 0,
) -> list[str]:
    """Every moment as raw pixels on stdout, in order, from one process. Pure; see the note above.

    `stream` is which video stream moves (`moving_stream_of`). A filter graph does not choose a
    stream the way a plain output does: `[0:v]` is the FIRST video stream, whatever it holds, and
    for an animated AVIF that is its still cover."""
    picked = _picked(stream)
    chains = ";".join(f"[{i}:{picked}]{_FIRST_FRAME},{filters}[v{i}]" for i in range(len(moments)))
    joined = "".join(f"[v{i}]" for i in range(len(moments)))
    graph = f"{chains};{joined}concat=n={len(moments)}:v=1:a=0[out]"
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        *_inputs(source, moments, settings),
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        "-fps_mode",
        "passthrough",
        "-pix_fmt",
        pixel_format,
        "-f",
        "rawvideo",
        "pipe:1",
    ]


def raw_frame_args(
    source: Path,
    moment: Moment,
    *,
    filters: str,
    pixel_format: str,
    settings: Settings,
    stream: int = 0,
) -> list[str]:
    """One moment as raw pixels on stdout. The per-moment shape the stream falls back to.

    The stream that moves is named only where it is not the first: an ordinary file's command
    leaves the choice to ffmpeg."""
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        *moment.seek,
        "-i",
        str(source),
        *(("-map", f"0:{_picked(stream)}") if stream else ()),
        "-frames:v",
        "1",
        "-vf",
        filters,
        "-pix_fmt",
        pixel_format,
        "-f",
        "rawvideo",
        "pipe:1",
    ]


def moment_files_args(
    source: Path,
    moments: Sequence[Moment],
    *,
    filters: str,
    output: Sequence[str],
    destinations: Sequence[Path],
    settings: Settings,
    stream: int = 0,
) -> list[str]:
    """Every moment as its own file, from one process. `output` is the codec and quality options
    every file gets; `destinations` is where each moment lands, in the same order. `stream` is
    which video stream moves (`moving_stream_of`)."""
    picked = _picked(stream)
    argv = [settings.ffmpeg_path, *background_flags(settings), *_inputs(source, moments, settings)]
    for index, destination in enumerate(destinations):
        argv += ["-map", f"{index}:{picked}", "-frames:v", "1", "-vf", filters, *output]
        argv.append(str(destination))
    return argv


async def raw_moments(
    source: Path,
    moments: Sequence[Moment],
    *,
    filters: str,
    pixel_format: str,
    frame_bytes: int,
    settings: Settings,
    time_limit: float,
    priority: subprocess.Priority = subprocess.Priority.BACKGROUND,
) -> list[bytes | None]:
    """Every moment of `source` as raw pixels, in order; None where a moment read back nothing.

    `frame_bytes` is what one picture weighs once `filters` and `pixel_format` have shaped it, and
    it is what the stream is cut by and what says whether it came back whole. A short stream is
    read again one moment at a time (see the note above the class).

    The output is uncapped and read on a thread, because a chunk of face-sized frames is tens of
    megabytes and a capped read would hand back fewer perfectly good pictures with nothing saying
    the rest were dropped.
    """
    if not moments:
        return []
    ready = _PREPARED.get()
    if ready is not None:
        prepared = ready.raw(source, moments, filters=filters, pixel_format=pixel_format)
        if prepared is not None:
            return prepared
    answer: list[bytes | None] = []
    size = await moments_per_process(source, moments, settings=settings, priority=priority)
    stream = await moving_stream_of(source, settings=settings, priority=priority)
    for start in range(0, len(moments), size):
        chunk = moments[start : start + size]
        answer.extend(
            await _raw_chunk(
                source,
                chunk,
                filters=filters,
                pixel_format=pixel_format,
                frame_bytes=frame_bytes,
                settings=settings,
                time_limit=time_limit,
                priority=priority,
                stream=stream,
            )
        )
    return answer


async def _raw_chunk(
    source: Path,
    chunk: Sequence[Moment],
    *,
    filters: str,
    pixel_format: str,
    frame_bytes: int,
    settings: Settings,
    time_limit: float,
    priority: subprocess.Priority,
    stream: int = 0,
) -> list[bytes | None]:
    if len(chunk) > 1:
        argv = raw_stream_args(
            source,
            chunk,
            filters=filters,
            pixel_format=pixel_format,
            settings=settings,
            stream=stream,
        )
        try:
            async with lanes.reading(source):
                raw = await subprocess.capture(argv, time_limit=time_limit, priority=priority)
        except subprocess.SubprocessError as error:
            log.info("media.moments.stream_refused", source=str(source), detail=str(error))
        else:
            if len(raw) == len(chunk) * frame_bytes:
                return [raw[i * frame_bytes : (i + 1) * frame_bytes] for i in range(len(chunk))]
            log.info(
                "media.moments.stream_short",
                source=str(source),
                wanted=len(chunk),
                got=len(raw) // frame_bytes,
            )
    # One process per moment, so a missing moment keeps its place. A moment the tool refuses
    # outright is a missing moment too, in its place: a truncated tail is common, everything
    # before it is perfectly good, and a caller reading moments one at a time carries on past one
    # bad moment.
    pictures: list[bytes | None] = []
    for moment in chunk:
        argv = raw_frame_args(
            source,
            moment,
            filters=filters,
            pixel_format=pixel_format,
            settings=settings,
            stream=stream,
        )
        try:
            async with lanes.reading(source):
                raw = await subprocess.capture(argv, time_limit=time_limit, priority=priority)
        except subprocess.SubprocessError as error:
            log.info(
                "media.moments.moment_refused",
                source=str(source),
                seek=" ".join(moment.seek),
                detail=str(error),
            )
            pictures.append(None)
            continue
        pictures.append(raw if len(raw) == frame_bytes else None)
    return pictures


async def moments_to_files(
    source: Path,
    moments: Sequence[Moment],
    *,
    into: Path,
    suffix: str,
    filters: str,
    output: Sequence[str],
    settings: Settings,
    time_limit: float,
    priority: subprocess.Priority = subprocess.Priority.BACKGROUND,
) -> list[Path | None]:
    """Every moment of `source` written as `into/NNNN{suffix}`, in order; None where nothing came out.

    A chunk whose process fails outright is read one moment at a time, so
    one moment the tool cannot place does not cost the strip every other tile. A moment past the
    end of the file produces no file and no failure, and is reported as None in its place.
    """
    if not moments:
        return []
    destinations = [into / f"{index:04d}{suffix}" for index in range(len(moments))]
    ready = _PREPARED.get()
    if ready is not None:
        prepared = ready.files(source, moments, filters=filters, suffix=suffix, output=output)
        if prepared is not None:
            return await asyncio.to_thread(_moved, prepared, destinations)
    size = await moments_per_process(source, moments, settings=settings, priority=priority)
    stream = await moving_stream_of(source, settings=settings, priority=priority)
    for start in range(0, len(moments), size):
        chunk = moments[start : start + size]
        wanted = destinations[start : start + size]
        argv = moment_files_args(
            source,
            chunk,
            filters=filters,
            output=output,
            destinations=wanted,
            settings=settings,
            stream=stream,
        )
        try:
            await run(argv, time_limit=time_limit, priority=priority, reads=source)
            continue
        except FFmpegError as error:
            log.info("media.moments.files_refused", source=str(source), detail=str(error))
        for moment, destination in zip(chunk, wanted, strict=True):
            argv = moment_files_args(
                source,
                [moment],
                filters=filters,
                output=output,
                destinations=[destination],
                settings=settings,
                stream=stream,
            )
            try:
                await run(argv, time_limit=time_limit, priority=priority, reads=source)
            except FFmpegError as error:
                log.info(
                    "media.moments.moment_refused",
                    source=str(source),
                    seek=" ".join(moment.seek),
                    detail=str(error),
                )
    present = await asyncio.to_thread(lambda: [one.exists() for one in destinations])
    return [one if there else None for one, there in zip(destinations, present, strict=True)]


# --- A still and its brightness, from one read -----------------------------------------------------
#
# A tile's still is chosen by what the frame SHOWS: a black frame or a fade is refused and the next
# moment tried. Choosing needs each candidate's brightness and keeping one needs the still itself,
# and reading the moment twice (once to look, once to cut) is two seeks of the same frame. So each
# seeked input is mapped to two outputs in one process: the still, at the tile's size, and a few
# grey pixels the choice is read off. A moment past the end writes neither, and is None in its place.


@dataclass(frozen=True, slots=True)
class CutStill:
    """One moment, cut: the still on disk and the grey pixels its brightness is read from."""

    still: Path
    levels: bytes


def moment_stills_args(
    source: Path,
    moments: Sequence[Moment],
    *,
    still_filters: str,
    still_output: Sequence[str],
    level_filters: str,
    stills: Sequence[Path],
    levels: Sequence[Path],
    settings: Settings,
    stream: int = 0,
) -> list[str]:
    """Every moment as a still AND as grey pixels, from one process. Pure; see the note above.
    `stream` is which video stream moves (`moving_stream_of`)."""
    picked = _picked(stream)
    argv = [settings.ffmpeg_path, *background_flags(settings), *_inputs(source, moments, settings)]
    for index, (still, level) in enumerate(zip(stills, levels, strict=True)):
        argv += ["-map", f"{index}:{picked}", "-frames:v", "1", "-vf", still_filters, *still_output]
        argv.append(str(still))
        argv += ["-map", f"{index}:{picked}", "-frames:v", "1", "-vf", level_filters]
        argv += ["-pix_fmt", "gray", "-f", "rawvideo", str(level)]
    return argv


async def moments_to_stills(
    source: Path,
    moments: Sequence[Moment],
    *,
    into: Path,
    still_filters: str,
    still_output: Sequence[str],
    level_filters: str,
    level_bytes: int,
    settings: Settings,
    time_limit: float,
    priority: subprocess.Priority = subprocess.Priority.BACKGROUND,
) -> list[CutStill | None]:
    """Every moment of `source` cut as a still and read as grey pixels, in order; None where
    nothing came out. `into` is the caller's scratch folder, and the stills stay there for it.

    A chunk the tool refuses is read a moment at a time, as `moments_to_files` does, so one moment
    it cannot place costs that moment and not the rest.
    """
    if not moments:
        return []
    stills = [into / f"{index:04d}.jpg" for index in range(len(moments))]
    levels = [into / f"{index:04d}.gray" for index in range(len(moments))]
    stream = await moving_stream_of(source, settings=settings, priority=priority)

    def argv_for(start: int, end: int) -> list[str]:
        return moment_stills_args(
            source,
            moments[start:end],
            still_filters=still_filters,
            still_output=still_output,
            level_filters=level_filters,
            stills=stills[start:end],
            levels=levels[start:end],
            settings=settings,
            stream=stream,
        )

    size = await moments_per_process(source, moments, settings=settings, priority=priority)
    for start in range(0, len(moments), size):
        end = min(start + size, len(moments))
        try:
            await run(argv_for(start, end), time_limit=time_limit, priority=priority, reads=source)
            continue
        except FFmpegError as error:
            log.info("media.stills.refused", source=str(source), detail=str(error))
        for one in range(start, end):
            try:
                await run(
                    argv_for(one, one + 1), time_limit=time_limit, priority=priority, reads=source
                )
            except FFmpegError as error:
                log.info("media.stills.moment_refused", source=str(source), detail=str(error))

    def collect() -> list[CutStill | None]:
        answer: list[CutStill | None] = []
        for still, level in zip(stills, levels, strict=True):
            raw = level.read_bytes() if level.exists() else b""
            fine = still.exists() and still.stat().st_size > 0 and len(raw) == level_bytes
            answer.append(CutStill(still=still, levels=raw) if fine else None)
        return answer

    return await asyncio.to_thread(collect)


def _moved(prepared: Sequence[Path | None], destinations: Sequence[Path]) -> list[Path | None]:
    """Move the prepared files to where the caller wanted them. Blocking, for a thread."""
    answer: list[Path | None] = []
    for made, destination in zip(prepared, destinations, strict=True):
        if made is None:
            answer.append(None)
            continue
        # Sift's own scratch both sides: a frame this decode wrote, moved to where the consumer's
        # scratch wants it. Never a library file.
        shutil.move(  # nosemgrep: sift-no-file-removal-outside-delete-trash
            str(made), str(destination)
        )
        answer.append(destination)
    return answer


# --- One decode for every moment of every consumer ------------------------------------------------
#
# Everything above seeks: each moment is its own `-ss` input, and for a long file that is the only
# affordable shape, because decoding two hours to keep sixty frames is two hours of decoding. For a
# short file it is the expensive shape. A Build wants two hundred moments of a five-minute video:
# a scrub strip, two fingerprints, faces, meaning, and two hundred seeks of a five-minute file are
# more work than decoding it once, and over a share they are two hundred round trips where one read
# is one. Which shape is cheaper is a sum of this machine's measured rates against the file, and it
# is worked out per file by `choose_read_shape`.
# The decode-once form is one `-i`, one decode, and one chain per moment per consumer: the stream
# is split, each branch `select`s exactly the first frame at or after its moment and applies that
# consumer's own filter, and each is written as its own output with `-frames:v 1`. That is the same
# frame the seek form produces (`-ss` stops at the first frame at or after the time asked for)
# byte for byte for BMP, JPEG and raw pixels, including a file whose timeline starts at five
# seconds rather than zero. Two facts to know about it:
# * **A moment past the end makes the tool exit non-zero**, after writing every other output: an
#   output stream that never received a frame is "Could not open encoder before EOF". So the exit
#   code does not say whether the decode worked; the files do. A moment whose file is absent is
#   None in its place, exactly as the seek form reports it, and a run that produced nothing at all
#   is a failure the consumers recover from by seeking.
# * **The graph goes in a file.** Two hundred chains are past the command line's budget on Windows,
#   and `-filter_complex_script` reads them from a file with no budget at all; the outputs still go
#   on the line, and `decode_once_fits` says whether they do.
#
# The consumers do not know which shape read their frames. Each asks `raw_moments` or
# `moments_to_files` as it would without them; those look first in the frames prepared for this task
# (`prepared`, a context variable the Build sets around a file's products) and seek only for what
# was not prepared. A consumer whose ask does not match what was planned for it is therefore not
# wrong, only unhelped, and the tests beside each product check that the plan and the ask agree.


@dataclass(frozen=True, slots=True)
class RawFrames:
    """One consumer's ask for raw pixels: what `raw_moments` takes, less the source."""

    moments: tuple[Moment, ...]
    filters: str
    pixel_format: str
    frame_bytes: int


@dataclass(frozen=True, slots=True)
class FrameFiles:
    """One consumer's ask for image files: what `moments_to_files` takes, less the destination."""

    moments: tuple[Moment, ...]
    filters: str
    suffix: str
    output: tuple[str, ...]


FrameRequest = RawFrames | FrameFiles


def microseconds_of(text: str) -> int | None:
    """A seek's seconds as the tool reads them: whole microseconds, any further digit dropped
    rather than rounded. None for a form it is not written in here."""
    negative = text.startswith("-")
    whole, _, fraction = (text[1:] if negative else text).partition(".")
    if not whole.isdigit() or (fraction and not fraction.isdigit()):
        return None
    value = int(whole) * 1_000_000 + int((fraction + "000000")[:6])
    return -value if negative else value


def select_expression(moment: Moment, clock: FrameClock) -> str | None:
    """The `select` that keeps exactly the frame this moment's seek stops at, or None for a seek
    this cannot read: a moment placed any other way is one the decode-once form cannot select,
    so it seeks.

    No seek at all is the first frame decoded. A seek is the first frame at or past its tick
    (`FrameClock.at`); the `isnan` term is the first frame of the file, which has none before it.
    """
    if moment.seek == ():
        return "eq(n\\,0)"
    if len(moment.seek) != 2 or moment.seek[0] != "-ss":
        return None
    microseconds = microseconds_of(moment.seek[1])
    if microseconds is None:
        return None
    tick = clock.at(microseconds)
    return f"gte(pts\\,{tick})*(isnan(prev_pts)+lt(prev_pts\\,{tick}))"


def decode_once_graph(
    requests: Sequence[FrameRequest], *, clock: FrameClock, stream: int = 0
) -> str:
    """The filter graph: one split, and one select-and-filter chain per moment of every request.

    Written to a file rather than the command line. See the note above. A moment whose seek
    cannot be read gets no chain; it is None in the result and the consumer seeks it. `stream`
    is which video stream moves (`moving_stream_of`): the split is fed that one, and `clock` is
    how its frames are timed.
    """
    chains: list[str] = []
    picks = [
        (r, m, select_expression(moment, clock))
        for r, request in enumerate(requests)
        for m, moment in enumerate(request.moments)
    ]
    kept = [(r, m, one) for r, m, one in picks if one is not None]
    if not kept:
        return ""
    split = "".join(f"[s{i}]" for i in range(len(kept)))
    chains.append(f"[0:{_picked(stream)}]split={len(kept)}{split}")
    for index, (r, m, one) in enumerate(kept):
        chains.append(f"[s{index}]select='{one}',{requests[r].filters}[o{r}_{m}]")
    return ";\n".join(chains) + "\n"


def _selectable(moment: Moment) -> bool:
    """Whether the decode-once form takes this moment: a seek written the way it can read."""
    if moment.seek == ():
        return True
    return (
        len(moment.seek) == 2
        and moment.seek[0] == "-ss"
        and microseconds_of(moment.seek[1]) is not None
    )


def _output_of(workspace: Path, r: int, m: int, request: FrameRequest) -> Path:
    suffix = request.suffix if isinstance(request, FrameFiles) else ".raw"
    return workspace / f"{r:02d}-{m:04d}{suffix}"


def decode_once_args(
    source: Path,
    requests: Sequence[FrameRequest],
    *,
    script: Path,
    workspace: Path,
    settings: Settings,
) -> list[str]:
    """One decode of `source`, every moment of every request written under `workspace`. Pure."""
    argv = [
        settings.ffmpeg_path,
        *background_flags(settings),
        "-i",
        str(source),
        "-filter_complex_script",
        str(script),
    ]
    for r, request in enumerate(requests):
        for m, moment in enumerate(request.moments):
            if not _selectable(moment):
                continue
            argv += ["-map", f"[o{r}_{m}]", "-frames:v", "1"]
            if isinstance(request, RawFrames):
                argv += ["-pix_fmt", request.pixel_format, "-f", "rawvideo"]
            else:
                argv += list(request.output)
            argv.append(str(_output_of(workspace, r, m, request)))
    return argv


def decode_once_fits(argv: Sequence[str]) -> bool:
    """Whether the command line is within the budget every site is held to."""
    return sum(len(one) + 1 for one in argv) <= COMMAND_LINE_BUDGET


_RawKey = tuple[Path, str, str]
_FilesKey = tuple[Path, str, str, tuple[str, ...]]


@dataclass
class PreparedFrames:
    """What one decode produced, for the consumers that asked, looked up by what each asks for.

    Keyed the way the consumers ask (the source, the filter and the pixel format or the suffix
    and output) and then by moment, so a consumer that reads its moments a few at a time (the
    face pass) finds each run of them. A file is handed out once: it is moved to where the
    consumer wanted it, and a second ask for the same moment seeks.
    """

    _raw: dict[_RawKey, dict[tuple[str, ...], bytes | None]] = field(default_factory=dict)
    _files: dict[_FilesKey, dict[tuple[str, ...], Path | None]] = field(default_factory=dict)

    def put_raw(self, source: Path, request: RawFrames, frames: Sequence[bytes | None]) -> None:
        held = self._raw.setdefault((source, request.filters, request.pixel_format), {})
        for moment, frame in zip(request.moments, frames, strict=True):
            held[moment.seek] = frame

    def put_files(self, source: Path, request: FrameFiles, files: Sequence[Path | None]) -> None:
        held = self._files.setdefault(
            (source, request.filters, request.suffix, tuple(request.output)), {}
        )
        for moment, made in zip(request.moments, files, strict=True):
            held[moment.seek] = made

    def raw(
        self, source: Path, moments: Sequence[Moment], *, filters: str, pixel_format: str
    ) -> list[bytes | None] | None:
        """The frames for these moments, or None where any of them was not prepared."""
        held = self._raw.get((source, filters, pixel_format))
        if held is None or any(moment.seek not in held for moment in moments):
            return None
        return [held[moment.seek] for moment in moments]

    def files(
        self,
        source: Path,
        moments: Sequence[Moment],
        *,
        filters: str,
        suffix: str,
        output: Sequence[str],
    ) -> list[Path | None] | None:
        """The files for these moments, taken out of the store, or None where any was not
        prepared. Taken out, because the caller moves them."""
        held = self._files.get((source, filters, suffix, tuple(output)))
        if held is None or any(moment.seek not in held for moment in moments):
            return None
        return [held.pop(moment.seek) for moment in moments]

    @property
    def count(self) -> int:
        return sum(len(one) for one in self._raw.values()) + sum(
            len(one) for one in self._files.values()
        )

    def holds(self, source: Path, request: FrameRequest) -> bool:
        """Whether every moment of this ask is here, so the ask would launch nothing."""
        if isinstance(request, RawFrames):
            held: dict[tuple[str, ...], Any] | None = self._raw.get(
                (source, request.filters, request.pixel_format)
            )
        else:
            held = self._files.get((source, request.filters, request.suffix, tuple(request.output)))
        return held is not None and all(moment.seek in held for moment in request.moments)

    def merged(self, other: PreparedFrames) -> PreparedFrames:
        """A store holding both: what was prepared for the task, and what a product read for
        itself on top of it. `other` wins where both hold a moment."""
        both = PreparedFrames()
        for mine in (self, other):
            for raw_key, frames in mine._raw.items():
                both._raw.setdefault(raw_key, {}).update(frames)
            for files_key, files in mine._files.items():
                both._files.setdefault(files_key, {}).update(files)
        return both


#: The frames prepared for the task on this stack, if any. A context variable rather than an
#: argument because the consumers are three features deep and none of them should know that a
#: Build read their frames ahead of them.
_PREPARED: contextvars.ContextVar[PreparedFrames | None] = contextvars.ContextVar(
    "prepared_frames", default=None
)


def prepared_now() -> PreparedFrames | None:
    """The frames prepared for the task on this stack, if any."""
    return _PREPARED.get()


@contextlib.contextmanager
def prepared(frames: PreparedFrames) -> Iterator[None]:
    """Hand these frames to every `raw_moments` and `moments_to_files` call inside."""
    token = _PREPARED.set(frames)
    try:
        yield
    finally:
        _PREPARED.reset(token)


def _collect(source: Path, requests: Sequence[FrameRequest], workspace: Path) -> PreparedFrames:
    """Read what the decode produced. Blocking, for a thread."""
    frames = PreparedFrames()
    for r, request in enumerate(requests):
        outputs = [
            _output_of(workspace, r, m, request) if _selectable(moment) else None
            for m, moment in enumerate(request.moments)
        ]
        if isinstance(request, RawFrames):
            raws: list[bytes | None] = []
            for made in outputs:
                if made is None or not made.exists():
                    raws.append(None)
                    continue
                raw = made.read_bytes()
                raws.append(raw if len(raw) == request.frame_bytes else None)
            frames.put_raw(source, request, raws)
        else:
            frames.put_files(
                source,
                request,
                [made if made is not None and made.exists() else None for made in outputs],
            )
    return frames


async def decode_once(
    source: Path,
    requests: Sequence[FrameRequest],
    *,
    workspace: Path,
    settings: Settings,
    time_limit: float,
    priority: subprocess.Priority = subprocess.Priority.BACKGROUND,
) -> PreparedFrames:
    """Decode `source` once and take every moment of every request from the one stream.

    Raises `FFmpegError` only when nothing at all came out (a tool that could not start, or a
    decode that produced no frame) because a non-zero exit is what a moment past the end looks
    like (see the note above) and the files say what happened. The caller holds `workspace`;
    the frames live there until the consumers move them.
    """
    script = workspace / "graph.txt"
    reading = await _reading_of(source, settings=settings, priority=priority)
    if reading.clock is None:
        # Without the stream's ticks the frame a seek stops at cannot be named exactly, and a
        # fingerprint taken from a neighbouring frame matches nothing. The consumers seek.
        log.info("media.decode_once.no_clock", source=str(source))
        return PreparedFrames()
    graph = decode_once_graph(requests, clock=reading.clock, stream=reading.stream)
    if not graph:
        return PreparedFrames()
    await asyncio.to_thread(script.write_text, graph, encoding="ascii")
    argv = decode_once_args(source, requests, script=script, workspace=workspace, settings=settings)
    if not decode_once_fits(argv):
        raise FFmpegError("too many moments for one command line")
    try:
        async with lanes.reading(source):
            result = await subprocess.run(
                argv, time_limit=time_limit, capture_stdout=False, priority=priority
            )
    except subprocess.SubprocessError as error:
        raise FFmpegError(str(error)) from error
    frames = await asyncio.to_thread(_collect, source, requests, workspace)
    produced = sum(
        1
        for held in list(frames._raw.values()) + list(frames._files.values())
        for one in held.values()
        if one is not None
    )
    if produced == 0:
        detail = result.stderr.decode("utf-8", "replace").strip()[-400:] or "no detail"
        raise FFmpegError(f"{Path(argv[0]).name} produced no frame: {detail}")
    log.info(
        "media.decode_once",
        source=str(source),
        moments=frames.count,
        produced=produced,
        returncode=result.returncode,
    )
    return frames


# --- Which shape to read a file in ------------------------------------------------------------------


class ReadShape(StrEnum):
    """How a Build reads one file's moments."""

    SEEK = "seek"
    """Each moment as its own seeked input: today's form, and the only one for a long file."""
    DECODE_ONCE = "decode_once"
    """The whole file decoded once, every moment kept as it passes."""


@dataclass(frozen=True, slots=True)
class StorageRead:
    """What the storage a file sits on measured, when it is on another machine."""

    megabytes_per_second: float
    seek_seconds: float


@dataclass(frozen=True, slots=True)
class ReadRates:
    """This machine's measured rates, the way the rule reads them. All at the self-test's 720p."""

    decode_fps: float
    """Frames of 720p one task decodes a second."""
    seek_seconds: float
    """What one moment costs by seeking, within a process that seeks many: the seek and the
    decode from the keyframe before it, on a local disk."""
    storage: StorageRead | None = None
    """The share's own numbers, when the file is on one. None for a local disk."""


#: The pixels the self-test's clip has; a file's cost scales by its pixels against these.
REFERENCE_PIXELS = 1280 * 720


#: WHAT ONE SEEKED MOMENT COSTS, in frames of the same file decoded, by the codec that made it.
#:
#: A seek decodes from the keyframe before its moment, and the decoder each seeked input opens
#: costs its own start, so a moment costs about half a keyframe interval of frames and a little
#: more. Keyframes sit further apart in the newer codecs, and each of their frames is dearer to
#: start from, so the count rises with them; a codec whose every frame is a keyframe (ProRes)
#: pays only the decoder's start. The middle of what the fingerprints' read cost on phone and
#: camera clips. A codec not named here is priced as H.264, the commonest.
FRAMES_PER_SEEK: dict[str, int] = {
    "h264": 50,
    "hevc": 115,
    "vp9": 200,
    "av1": 160,
    "vp8": 35,
    "prores": 27,
}
FRAMES_PER_SEEK_UNKNOWN = FRAMES_PER_SEEK["h264"]


def frames_per_seek(codec: str | None) -> int:
    """What one seeked moment of a file in this codec costs, in its own frames decoded."""
    return FRAMES_PER_SEEK.get(codec or "", FRAMES_PER_SEEK_UNKNOWN)


def choose_read_shape(
    *,
    moments: int,
    duration_seconds: float,
    fps: float,
    width: int,
    height: int,
    size_bytes: int,
    codec: str | None = None,
    rates: ReadRates | None,
) -> ReadShape:
    """Seek or decode once, from what the file is and, on a share, what the share measured.

    Decoding once costs every frame of the file. Seeking costs each moment `frames_per_seek` of
    the file's own frames, so on a local disk the rule is frames against moments times that, and
    neither the file's pixels nor this machine's speed moves it: both sides decode the same
    pictures. A share adds its seek to every moment and makes the decode at least as long as
    moving the file's bytes, and those are seconds, so with the share's numbers both sides are
    priced in seconds at this machine's measured decode rate. A file with no length or no
    moments seeks.
    """
    if moments <= 0 or duration_seconds <= 0:
        return ReadShape.SEEK
    frames = duration_seconds * (fps if fps > 0 else 30.0)
    decoding = frames
    seeking = float(moments * frames_per_seek(codec))
    if rates is not None and rates.storage is not None and rates.decode_fps > 0:
        scale = max(1.0, (width * height) / REFERENCE_PIXELS) if width and height else 1.0
        per_frame = scale / rates.decode_fps
        decoding *= per_frame
        seeking = seeking * per_frame + moments * rates.storage.seek_seconds
        if rates.storage.megabytes_per_second > 0:
            decoding = max(decoding, size_bytes / 1_000_000 / rates.storage.megabytes_per_second)
    return ReadShape.DECODE_ONCE if decoding < seeking else ReadShape.SEEK


@dataclass(frozen=True, slots=True)
class FileFacts:
    """What a product needs to know about a file to say which moments it would ask for."""

    asset_id: str
    path: Path
    media_type: str
    duration_ms: int
    width: int
    height: int
    fps: float
    size_bytes: int
    #: How long the PICTURE runs, as the row stores it. What a fingerprint's moments are spread
    #: across is `sampling.picture_span` of this and `duration_ms`, asked the same way here as by
    #: the product that reads them.
    video_duration_ms: int | None = None
    #: The picture's codec, which prices a seek into the file (`frames_per_seek`).
    vcodec: str | None = None


# --- Getting from an asset to a file ------------------------------------------------------------


class MissingAsset(Exception):
    """The asset is not there any more.

    Permanent by nature. Every retry finds the same nothing, and an asset does not come back.
    """


class NoReadableCopy(Exception):
    """Every copy of this asset's bytes is somewhere Sift cannot read right now.

    Not permanent, and that is the whole point of the distinction: the commonest cause is a NAS
    that is offline, and the file is fine. Retrying is exactly right.
    """


@dataclass(frozen=True, slots=True)
class Source:
    """An asset, and a file of it that can actually be opened.

    `path` is what to hand a decoder; `original` is the user's own file. For everything Sift
    handles they are the same file, and the two names exist for the one format they are not:
    ffmpeg cannot read an animated WebP, so those are converted once into a readable copy and
    `path` is that copy. Anything asking what is really on disk (putting the file back through
    the gate, serving its bytes, naming it in a message) wants `original`.
    """

    asset: Asset
    location: Location
    path: Path
    original: Path


async def resolve(store: ContentStore, asset_id: str) -> Source:
    """Find an asset and a readable copy of it.

    An asset can sit in several places, and a caller needs any one of them: two copies of the same
    bytes make the same thumbnail and the same segment, so the first one that opens is the right
    one. `path_of` is the only way from a row to a file, and it re-checks the stored path on the
    way out, so a row written by an older version, or restored from a backup, is validated here
    rather than trusted.

    A location marked missing is skipped without being tried. It is already known to be gone, and
    the file that replaced a NAS mount when it was unmounted is exactly the thing not to open.
    """
    asset = await store.get(asset_id)
    if asset is None:
        raise MissingAsset(f"asset {asset_id} no longer exists")

    locations = await store.locations(asset_id)
    for location in locations:
        if location.status is not LocationStatus.PRESENT:
            continue
        try:
            path = await store.path_of(location)
        except (LookupError, ValueError) as exc:
            # The root is gone, or the stored path does not survive re-validation. Either way this
            # copy is not usable; another one might be.
            log.warning("media.location_unusable", location_id=location.id, reason=str(exc))
            continue
        if await asyncio.to_thread(path.is_file):
            return Source(asset=asset, location=location, path=path, original=path)

    raise NoReadableCopy(
        f"none of the {len(locations)} known copies of this file could be opened. "
        "A drive or network share it lives on is probably not connected."
    )


async def resolve_decodable(store: ContentStore, asset_id: str, *, settings: Settings) -> Source:
    """`resolve`, and then whatever it takes for a decoder to be able to read it.

    Every stage that hands a file to ffmpeg goes through this instead: the probe, the thumbnail,
    the preview, the sprite strip and the face pass. What it does for all but two formats is
    nothing at all, which is the point: the alternative is each of those five learning which
    formats need converting first, and the sixth one forgetting. The two are the animated WebP,
    which ffmpeg cannot read, and the HEIF photograph, of which ffmpeg reads one tile.

    Playback deliberately does not use it. A browser plays an animated WebP natively, so serving
    the original bytes is both correct and free, and converting one to stream it would be work
    done to make something worse.
    """
    source = await resolve(store, asset_id)
    # Imported here rather than at the top: the WebP module builds its copies through ffmpeg, so
    # it imports this one.
    from sift.kernel import webp

    # A HEIF photograph is read whole by libheif, never by ffmpeg, which reads one tile of a
    # phone's photograph (see `heif`). Its copy is the whole picture, upright.
    if heif.is_heif_still(source.asset):
        readable = await heif.readable_copy(store, source.asset, source.path, settings=settings)
    elif webp.needs_a_readable_copy(source.asset):
        readable = await webp.readable_copy(store, source.asset, source.path, settings=settings)
    else:
        return source
    return Source(
        asset=source.asset, location=source.location, path=readable, original=source.original
    )


def seconds(milliseconds: int) -> str:
    """Milliseconds as the decimal seconds ffmpeg's time options take."""
    return f"{milliseconds / 1000:.3f}"
