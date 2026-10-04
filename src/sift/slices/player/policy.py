# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which of the three ways to play a file this browser needs, and whether it can be played at all.

Every playback decision in Sift is made here, once, by a pure function. The compatibility matrix
lives in this one module because it will be tuned, and it has to be tuned in one place; there is a
golden file beside this one pinning what it answers.

## The three tiers, cheapest first

1. **Direct play.** The browser says it can decode this codec and read this container, so the file
   is served straight off disk over HTTP range requests. No ffmpeg runs at all. Full quality, full
   resolution, instant. This is the majority path and it must stay free.
2. **Remux.** The browser can decode the codec but cannot read the container: HEVC inside a
   Matroska file is the common one. The stream would be *copied* into fragmented MP4: no decode,
   no encode, no quality loss. **Currently closed**: a copy cannot be cut at an arbitrary
   point, so the segments overlap and lie about their durations. See `decide` for the measurement,
   and for what would re-open it. Files that would take this tier transcode instead.
3. **Transcode.** The browser genuinely cannot decode this. The file is re-encoded to H.264/AAC in
   short segments, on demand, as it is watched. Expensive, and the only tier that can lose quality.

Tier 3 is not the normal path for anything that is not H.264: that assumption is years out of
date: AV1 decode is effectively universal in Chrome, Edge and Firefox, and HEVC plays in Chrome 107+
wherever the machine has a hardware decoder. **So the question this module asks is not "what codec
is this file?" but "what can *this* browser do?"**, and the answer arrives from the client rather
than being guessed from a user-agent string.

## Why the client is asked rather than assumed

There is no single codec that covers every browser. HEVC is universal on Safari and absent from
most Firefox builds; AV1 is universal on Chrome and limited to recent Apple silicon. An
iPhone 15 Pro and an iPhone 14 give different answers. Any server-side guess is wrong for
somebody, and being wrong in the optimistic direction means a black screen while being wrong in
the pessimistic direction means transcoding something that would have played for free.

## The realtime gate

Deciding to transcode is not the same as being *able* to. A transcode that runs slower than the
video plays does not start late: it stalls, repeatedly, for the whole clip, forever. That is
the failure a first-segment-latency test cannot see, and the more important of the two
assertions. So a projected realtime ratio is computed before anything is offered, and a file that
cannot clear it is reported honestly rather than being allowed to spin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import NamedTuple

from sift.kernel.content import Asset
from sift.kernel.content.view_rule import counts_as_a_view as counts_as_a_view
from sift.kernel.content.view_rule import watch_needed as watch_needed
from sift.kernel.ingress import ALLOWED_MEDIA, Kind
from sift.kernel.log import get_logger
from sift.slices.player import tuning

log = get_logger(__name__)

#: Containers a browser is asked about. Sift's ingress gate allows exactly four video containers
#: (`kernel.ingress.ALLOWED_MEDIA`), and this vocabulary is that one, not MIME types, and not
#: ffprobe's "mov,mp4,m4a,3gp,3g2,mj2" list, which names every format that *could* demux a file
#: rather than the one it is.
VIDEO_CONTAINERS = frozenset({"mp4", "mov", "mkv", "webm"})

#: What each of those containers is called in a sentence a person reads. The format's own name,
#: because that is what a file's properties and every media tool call it; the extension is not a
#: name. A container outside the four is named by its extension, which is the best there is.
CONTAINER_NAMES: dict[str, str] = {
    "mp4": "MP4",
    "mov": "QuickTime",
    "mkv": "Matroska",
    "webm": "WebM",
}


class Copy(StrEnum):
    """Why a file is being served from a second copy rather than from itself.

    ONE derivative kind covers two completely different problems, so the sentence cannot be read
    off the copy. A copy is written either because the browser cannot read the container the streams
    are in, or because the file stores its audio too far from its video for seeking to work, and
    the two are told apart nowhere in the derivative row, because both write `remux`.

    So the caller says which. It knows: one is a fact about the browser asking and the other is a
    measurement on the file, and neither can be recovered from the copy after the fact.
    """

    REPACKAGED = "repackaged"
    """The container was unreadable to this browser. The copy is a different box, same streams."""

    REPAIRED = "repaired"
    """The stored file's audio sits too far from its video. The copy is interleaved properly."""


class Route(StrEnum):
    """How the bytes reach the browser."""

    DIRECT = "direct"
    """Off the disk, unmodified, over HTTP range requests. No ffmpeg."""

    REMUX = "remux"
    """Repackaged into fragmented MP4 by a stream copy. No re-encode, no quality loss."""

    TRANSCODE = "transcode"
    """Re-encoded to H.264/AAC segments on demand. The expensive path."""

    UNREAD = "unread"
    """Not yet. Sift has not read the file, so nothing about its streams is known and no path
    can be chosen; the read is queued and the player asks again when it lands."""


@dataclass(frozen=True, slots=True)
class ClientCapabilities:
    """What one browser reported it can handle.

    Everything is in Sift's own vocabulary (ffprobe's codec names, `h264`, `hevc`, `av1`, and the
    ingress gate's container names, `mp4`, `mkv`) rather than the MIME-and-codec-parameter
    strings the browser actually answers questions in (`video/mp4; codecs="hvc1.1.6.L93.B0"`).

    The translation happens in the client, deliberately. The browser is the only party that knows
    which probe strings mean what, that list is long and full of exceptions, and keeping it there
    means this module compares names that match the database columns it is reading. A server-side
    translation layer would be a second vocabulary to keep in step with the first.
    """

    video_codecs: frozenset[str]
    audio_codecs: frozenset[str]
    containers: frozenset[str]
    #: The codecs the browser also decodes at ten bits a sample. A codec here is in
    #: `video_codecs` too; one there and not here plays the eight-bit files and not the others.
    video_codecs_10bit: frozenset[str] = frozenset()

    def plays(self, vcodec: str, bit_depth: int | None) -> bool:
        """Whether this browser decodes a stream of this codec at this depth.

        Unknown depth is read as eight, which is what the depth column is NULL for on a library
        from before it was kept; every file read since carries a number.
        """
        if not vcodec or vcodec not in self.video_codecs:
            return False
        if bit_depth is not None and bit_depth > 8:
            return vcodec in self.video_codecs_10bit
        return True

    @classmethod
    def nothing(cls) -> ClientCapabilities:
        """A client that has reported nothing yet.

        Everything transcodes. This is the safe direction: a client that turns out to be more
        capable than this loses some quality and some CPU on one playback, where the opposite
        mistake is a black screen.
        """
        return cls(
            video_codecs=frozenset(),
            audio_codecs=frozenset(),
            containers=frozenset(),
            video_codecs_10bit=frozenset(),
        )


@dataclass(frozen=True, slots=True)
class Plan:
    """How a particular file will be played for a particular browser."""

    route: Route
    reason: str
    """Why, in words a person can read. Shown in the UI when the answer is not 'direct'."""

    scale_height: int | None = None
    """The height the transcode is scaled down to, or None to keep the source's own.

    Only ever set on the transcode path. Downscaling a file the browser could have played is a
    quality loss for nothing.
    """

    projected_realtime: float | None = None
    """How many seconds of video this machine is expected to produce per second of work.

    None when it could not be worked out: an unprobed file, or one whose frame rate was never
    recorded. None is not a refusal: what cannot be measured is not blocked, it is allowed through
    and corrected by the first segment's real timing.
    """

    streamable: bool = True
    """False when the projection says this file would stall rather than play.

    The player says so plainly and offers to try anyway. It is not an error and not a hard block:
    the projection is an estimate, and the person watching is entitled to overrule it.
    """


# --- The realtime projection ----------------------------------------------------------------

#: The work budget of one CPU core, in units of "encoded pixels per second".
#:
#: Calibrated against the worst *streamable* row measured: HEVC 10-bit 1080p at 60 fps, at
#: **2.2x realtime on 4 cores**. The model below reproduces that row at 2.21x.
#:
#: Checked against the whole corpus at 4 cores (projected vs measured):
#:
#: | File | projected | measured | |
#: |---|---|---|---|
#: | `h264_1080p` | 4.7x | 4.3x | slightly optimistic |
#: | `hevc_8bit_1080p30` | 4.4x | 4.2x |  |
#: | `av1_8bit_1080p` | 3.9x | 3.4x | slightly optimistic |
#: | `hevc_10bit_1080p` | **2.2x** | **2.2x** |  the calibration row |
#: | `av1_8bit_4k` | 0.49x | 0.7x | conservative, correctly refused |
#: | `h264_4k_120fps` | 0.29x | 0.5x | conservative, correctly refused |
#:
#: It also reproduces the 2-core stall independently: 1080p AV1 at 60 fps on 2 cores projects
#: 0.98x, against a measured 0.86x: a file that would stall.
#:
#:  **It is deliberately conservative at the 4K boundary.** Downscaled 4K AV1 projects 0.84x
#: where 1.2x was measured, so that file is reported as "would stall, try anyway" rather than
#: offered silently. That is the intended behaviour and not a mis-fit: the 4K boundary needs
#: confirmation on real modest hardware before a fallback is built on it,
#: and erring toward an honest warning with an override beats erring toward a silent stall.
PIXELS_PER_SECOND_PER_CORE = 110_000_000

#: What decoding a frame costs relative to encoding one, at the same resolution.
#:
#: Derived from the downscaling measurement rather than assumed. Scaling 4K AV1 to 1080p cuts
#: the encode to a quarter of the pixels and leaves the decode untouched, and it improved latency
#: by 1.7x (2.86 s -> 1.66 s). Only one decode/encode split reproduces that ratio, and it is this
#: one: **encoding costs about twice what decoding does.**
#:
#: This is why downscaling helps but does not rescue 4K on a weak box: the 4K *decode*
#: must still happen at full resolution, and on two cores that alone exceeds the budget. Charging
#: decode and encode separately is what makes that fall out of the arithmetic instead of needing a
#: special case.
_DECODE_COST_RELATIVE_TO_ENCODE = 0.5

#: How much more a codec costs to *decode* than H.264, which is the baseline.
#:
#: This is the one place codec identity earns a say. Measuring the same files on older Intel
#: silicon, HEVC and H.264 cost 1.3-1.5x more while **AV1 cost 2.2x**,
#: and the 10-bit HEVC row was identical, so this is not a uniform "older chip is slower" effect.
#: dav1d leans hard on wide vector instructions that older cores implement poorly.
#:
#: This is deliberately the *only* codec-based number in the module. The headline finding is that
#: the cost driver is resolution, not codec, and a matrix of per-codec fudge factors is precisely
#: the mitigation it says would have missed the real problem.
_DECODE_COST: dict[str, float] = {
    "av1": 1.6,
    "hevc": 1.2,
}

#: The frame rate assumed for a file that does not record one.
#:
#: `fps` arrived with content schema v2, so every asset probed before it is NULL, and re-probing a
#: whole library at boot to backfill it is not a trade worth making. 30 is the common case and the
#: error it introduces is bounded: a 60 fps file read as 30 fps projects twice as fast as it will
#: run, which the first segment's real timing then corrects.
_ASSUMED_FPS = 30.0


def projected_realtime(
    *,
    width: int | None,
    height: int | None,
    fps: float | None,
    vcodec: str | None,
    cpu_count: int,
    scale_height: int | None = None,
    encoder_rate: float | None = None,
) -> float | None:
    """Seconds of video this machine should produce per second of work. None if unknowable.

    Above 1.0 the transcode outruns playback and the buffer fills; below it the buffer drains and
    the player stalls forever. `tuning.MIN_REALTIME_RATIO` is where the line is drawn, with margin.

    `encoder_rate` is how many pixels a second the *encoder* can produce, when the encoding is not
    being done by the processor. None means it is, which is the single-budget arithmetic, so the
    corpus this model was calibrated against still describes it exactly.

    **Two budgets rather than one, and the answer is the slower of them.** Decoding is the
    processor's work whatever else is true: a card that encodes does not read the source file. So
    with hardware encoding the two halves happen AT THE SAME TIME, on different silicon, and the
    thing that decides whether playback keeps up is whichever finishes last. Adding them, as the
    single-budget form does, would charge for the overlap twice and report a machine as too slow
    for work it does comfortably.
    """
    if not width or not height or width <= 0 or height <= 0:
        # An unprobed file. Refusing what cannot be measured would block every asset not yet
        # probed, so this reports "no answer" and the caller lets it through.
        return None

    rate = fps if fps and fps > 0 else _ASSUMED_FPS

    # Downscaling cheapens the encode but *not* the decode: the source frames still have to be
    # decoded at full resolution before anything can shrink them. Measurement showed this: on
    # two cores, downscaling 4K helped and still left both files below realtime, because "the 4K
    # decode alone exceeds the budget". Splitting the two halves is what makes that fall out.
    decoded_pixels = width * height * rate
    encoded_pixels = decoded_pixels
    if scale_height is not None and scale_height < height:
        encoded_pixels = decoded_pixels * (scale_height / height) ** 2

    decode = decoded_pixels * _DECODE_COST_RELATIVE_TO_ENCODE * _decode_multiplier(vcodec)
    processor = PIXELS_PER_SECOND_PER_CORE * max(1, cpu_count)

    if encoder_rate is None or encoder_rate <= 0:
        # The processor is doing both halves, so it pays for both. The single-budget arithmetic.
        return processor / (decode + encoded_pixels)

    # Decoding on the processor and encoding on the card, at the same time. Neither waits for the
    # other to finish a frame, so the pipeline runs at the speed of its slower half.
    return min(processor / decode, encoder_rate / encoded_pixels)


def decode_seconds(
    *,
    width: int | None,
    height: int | None,
    fps: float | None,
    vcodec: str | None,
    cpu_count: int,
    video_seconds: float,
) -> float | None:
    """How long this machine should take to DECODE that much of this file. None if unknowable.

    Exists so that a segment's measured time can be split into its two halves. A segment is timed
    end to end (open the file, seek, decode, encode, write), and using that whole number as an
    encoder's speed would charge the card for the processor's reading. Subtracting what the decode
    was expected to cost leaves the part the encoder is actually responsible for.

    The estimate it subtracts is the same one `projected_realtime` uses, so the two cannot drift.
    """
    if not width or not height or width <= 0 or height <= 0:
        return None
    rate = fps if fps and fps > 0 else _ASSUMED_FPS
    cost = width * height * rate * _DECODE_COST_RELATIVE_TO_ENCODE * _decode_multiplier(vcodec)
    budget = PIXELS_PER_SECOND_PER_CORE * max(1, cpu_count)
    return video_seconds * cost / budget


def _decode_multiplier(vcodec: str | None) -> float:
    return _DECODE_COST.get((vcodec or "").lower(), 1.0)


# --- The ladder -------------------------------------------------------------------------------


class Rung(NamedTuple):
    """One step of the ladder: what it is CALLED, and what it is ENCODED at.

    Two numbers because for a portrait video they are not the same one. The name is the short side,
    which is how everybody names a video; the encode is a height, because that is what
    `scale=-2:h` takes and what every cache key and playlist here is written in.
    """

    #: The short side this comes to. The name: 720 is "720p", landscape or portrait.
    short: int
    #: The height to encode at. Equal to `short` for a landscape file, taller for a portrait one.
    height: int


def rungs(source_width: int | None, source_height: int | None) -> tuple[Rung, ...]:
    """The steps worth offering for a file this size, largest first.

    Strictly below the source, never equal to it and never above it. A rung the same size as the
    file is a re-encode that looks worse for no reason, and one above it is an upscale: bigger,
    slower, and blurrier than what it was made from. The source itself is offered separately, as
    itself, which is a different and better thing than a rung that pretends to be it.

    CHOSEN BY THE SHORT SIDE, and for a landscape file that changes nothing at all: the short side
    IS the height, so every rung and every cache key is what picking by height gives. It matters for
    portrait files, where picking by height is wrong twice over.

    By height, a phone video stored 2160 wide and 3840 tall would be offered a rung "2160p" that is
    1215 pixels across: a name that reads as 4K, sitting directly under the file itself which really
    is 4K, so the menu would appear to offer 4K twice. And the step below it, "1440p", would be 810
    across. Every name on the ladder would describe a size the picture never had.

    By the short side the same file gets 1440p, 1080p, 720p, 480p and 360p, encoded at 2560, 1920,
    1280, 854 and 640 tall, the sizes those names have always meant.

    A file with no known size gets nothing. That is the honest answer for something never probed:
    with no measurement there is no way to tell an upscale from a downscale.
    """
    if not source_height or source_height <= 0:
        return ()
    short = min(source_width, source_height) if source_width and source_width > 0 else source_height
    steps: list[Rung] = []
    for rung in tuning.LADDER_HEIGHTS:
        if rung >= short:
            continue
        # Landscape is the identity case and is written as one, so it cannot drift by a rounding.
        height = rung if short == source_height else round(rung * source_height / short)
        # Even, for the same reason `scaled_width` is: H.264 stores colour at half resolution in
        # each direction and an encoder refuses an odd dimension.
        height -= height % 2
        if 0 < height < source_height:
            steps.append(Rung(short=rung, height=height))
    return tuple(steps)


#: What people call a video of a given size, by its SHORT side.
#:
#: The short side, not the height, and that is the whole point. A phone clip stored 1080 wide and
#: 1920 tall is a 1080p video to everybody who has ever seen one; naming it by its height makes it
#: "1920p", which is not a thing, and puts it above a rung called "1080p" that is the same 1080
#: across. Landscape and portrait of the same recording get the same name, which is correct: they
#: are the same size, turned.
#:
#: Largest first, and a size is called by the first name it is at least as big as, so 3840x1600,
#: a wide cinema crop with a 1600 short side, is called 4K rather than falling through to 1440p.
_SIZE_NAMES: tuple[tuple[int, str], ...] = (
    (2160, "4K"),
    (1440, "1440p"),
    (1080, "1080p"),
    (720, "720p"),
    (480, "480p"),
    (360, "360p"),
)


def size_name(width: int | None, height: int | None) -> str | None:
    """What to call a video of this size, or None when it was never measured.

    None rather than a guess: a file with no known shape has no honest name, and inventing one is
    how a screen comes to state something it does not know.
    """
    if not width or not height or width <= 0 or height <= 0:
        return None
    short = min(width, height)
    for floor, name in _SIZE_NAMES:
        if short >= floor:
            return name
    # Smaller than every name on the list. Its own numbers are the honest answer.
    return f"{width} x {height}"


def scaled_width(source_width: int | None, source_height: int | None, height: int) -> int | None:
    """How wide this file becomes at a given height, keeping its shape. None if unknowable.

    Rounded to an even number, which is not cosmetic: H.264 stores colour at half resolution in
    each direction, so an odd dimension has no way to divide in two and the encoder refuses it.
    This is the same arithmetic `scale=-2:h` does inside ffmpeg, done here because the master
    playlist has to *declare* the size before anything has been encoded.
    """
    if not source_width or not source_height or source_height <= 0:
        return None
    width = round(source_width * height / source_height)
    return max(2, width - (width % 2))


# --- What the stream may claim to be ----------------------------------------------------------

#: The H.264 levels, with what each one is allowed to carry.
#:
#: A level is a promise about the hardest work a decoder will be asked to do (how many macroblocks
#: a second, and how many in one picture), and a device advertises the highest level it can manage.
#: Every phone, television and browser reads it and some of them ENFORCE it, which is why picking
#: one is not a formality.
#:
#: `(name, hex, max macroblocks per second, max macroblocks per frame)` from the H.264 specification's
#: Table A-1. The hex is the same number the name is: level 4.1 is 41, which is 0x29, and that is
#: what goes in the `avc1.` string a master playlist declares.
_H264_LEVELS: tuple[tuple[str, str, int, int], ...] = (
    ("3.0", "1e", 40_500, 1_620),
    ("3.1", "1f", 108_000, 3_600),
    ("3.2", "20", 216_000, 5_120),
    # 4.0 is deliberately absent, and it is the only gap in this table.
    #
    # It carries the SAME macroblock limits as 4.1 and a much lower bitrate ceiling: 20 Mbit/s
    # against 50. Picking the lowest level that fits would therefore choose 4.0 for every 1080p
    # file, and Sift's compatibility encode is constant-QUALITY: a busy passage at CRF 23 can pass
    # 20 Mbit/s comfortably, which would make the stream break the level it declares. There is
    # nothing to gain either (no decoder implements 4.0 without 4.1), so the twin that cannot be
    # over-run is the one worth naming.
    ("4.1", "29", 245_760, 8_192),
    ("4.2", "2a", 522_240, 8_704),
    ("5.0", "32", 589_824, 22_080),
    ("5.1", "33", 983_040, 36_864),
    ("5.2", "34", 2_073_600, 36_864),
    ("6.0", "3c", 4_177_920, 139_264),
    ("6.1", "3d", 8_355_840, 139_264),
    ("6.2", "3e", 16_711_680, 139_264),
)

#: What the profile byte is for High profile, which is what Sift's compatibility encode produces.
#: `6400` is High (100 = 0x64) with no constraint flags set.
_H264_HIGH_PROFILE = "6400"

#: The audio half of the codec string: AAC Low Complexity, which is what the encode produces.
_AAC_LC = "mp4a.40.2"


def h264_level(width: int | None, height: int | None, fps: float | None) -> tuple[str, str]:
    """The lowest H.264 level that can legally carry this picture: `(name, hex)`.

    THE LOWEST rather than the highest, because a level is a *ceiling a decoder promises to meet*:
    naming a higher one than the stream needs excludes devices that could have played it perfectly
    well. Naming a lower one is the worse mistake in the other direction: a decoder that enforces
    the number it was given meets a stream that breaks it, and shows nothing.

    A fixed `-level 4.1` is right up to 1080p and a lie at 1440p and 4K, and a master playlist has
    to declare the level, so the lie becomes something a browser reads and believes.

    Unknown dimensions fall back to the highest level rather than the lowest. What cannot be
    measured must not be under-promised.
    """
    if not width or not height or width <= 0 or height <= 0:
        return _H264_LEVELS[-1][0], _H264_LEVELS[-1][1]

    # A macroblock is 16x16 pixels, and a picture that does not divide evenly still pays for the
    # partial ones: the encoder pads out to a whole block.
    blocks = -(-width // 16) * -(-height // 16)
    rate = fps if fps and fps > 0 else _ASSUMED_FPS
    per_second = blocks * rate

    for name, code, max_rate, max_frame in _H264_LEVELS:
        if per_second <= max_rate and blocks <= max_frame:
            return name, code
    return _H264_LEVELS[-1][0], _H264_LEVELS[-1][1]


def codec_string(width: int | None, height: int | None, fps: float | None) -> str:
    """What a master playlist declares this variant is, as HLS spells it.

    Both tracks, because `CODECS` describes the whole stream and a player deciding whether it can
    handle a variant has to know about the audio as well. Sift's compatibility encode is always
    H.264 High and AAC-LC, so the only thing that varies between rungs is the level.
    """
    _, code = h264_level(width, height, fps)
    return f"avc1.{_H264_HIGH_PROFILE}{code},{_AAC_LC}"


# --- What is actually being served ------------------------------------------------------------

#: What a repaired copy is, whatever the original was. The repair writes MP4 and only MP4.
REPAIRED_CONTAINER = "mp4"
REPAIRED_MIME = "video/mp4"


#: What to say about each kind of copy. Beside the enum rather than inline, so the two sentences
#: are read together and neither can drift into describing the other's problem.
#:
#: THE REPAIRED ONE SAYS THE OUTCOME, NOT THE CAUSE. "This file stores its sound far from its
#: picture" is a fact about interleave (how a container orders its packets) that somebody
#: watching a video cannot check and cannot act on. What is said is the outcome and the
#: reassurance. But it still names the act: it is a remux (`ffmpeg.remux_args`, a stream copy
#: into MP4), so the sentence says so in plain words, with the word the other copy already uses;
#: "a smoother copy" alone leaves the reader asking whether it is converting.
_COPY_REASON: dict[Copy, str] = {
    Copy.REPACKAGED: (
        "Your browser cannot read this file's container, so it is playing from a repackaged copy. "
        "The video and audio are the originals, untouched."
    ),
    Copy.REPAIRED: (
        "Playing a repackaged copy \u2014 the same picture and sound, rewrapped so skipping through "
        "it doesn't stall. The original file is untouched."
    ),
}


def _copy_reason(copy: Copy, stored_container: str | None) -> str:
    """The sentence for a file played from a second copy, naming the container it is stored in.

    Only the repackaged copy is about a container, and the one the browser refused is the stored
    file's, which the copy no longer carries. Named as the conversion's sentence names it before
    the copy exists, so one file is not "Matroska" on its first viewing and "this file's container"
    on every one after it. Unknown, the sentence says the container without a name.
    """
    if copy is not Copy.REPACKAGED or not stored_container:
        return _COPY_REASON[copy]
    container = stored_container.lower()
    named = CONTAINER_NAMES.get(container, container.upper())
    return (
        f"Your browser cannot read this file's container ({named}), so it is playing from a "
        "repackaged copy. The video and audio are the originals, untouched."
    )


def as_served(asset: Asset, *, repaired: bool) -> Asset:
    """The file the browser will actually be sent, described the way the decision below reads it.

    `decide` is a question about bytes, not about a row, and the two can differ in exactly one way:
    a file stored with its audio too far from its video is served from a repaired copy, which is
    always MP4. Deciding from the row means a Matroska file whose repaired copy is an MP4 the
    browser could play as it is gets re-encoded anyway, for a container problem that is not in the
    file being sent.

    Only the container and the type change. The repair is a stream copy, so the codecs, the
    dimensions, the frame rate and the length are the original's: anything that changed those
    would be a defect in the repair rather than something to model here.

    Passed in as a flag rather than looked up, because this module reads no database and holds no
    state. The caller knows whether a repair exists; it is the same lookup it needs anyway to know
    which file to open.
    """
    if not repaired:
        return asset
    return replace(asset, container=REPAIRED_CONTAINER, mime=REPAIRED_MIME)


# --- The decision ---------------------------------------------------------------------------


def needs_repackaging(asset: Asset, client: ClientCapabilities) -> bool:
    """Whether a repackaged copy of this file would let this browser play it for free.

    True for exactly one shape of problem: the browser can decode both streams and cannot read the
    container they are in, HEVC inside Matroska being the common one. Everything else is either
    already playable or genuinely needs converting, and a copy would help neither.

    Asked of the file as it was STORED, not as it is served, because a file that already has a copy
    is not a file that needs one. The caller checks for the copy; this answers the question that
    comes before it.
    """
    if asset.media_type != "video":
        return False
    vcodec = (asset.vcodec or "").lower()
    acodec = (asset.acodec or "").lower()
    container = (asset.container or "").lower()
    video_ok = client.plays(vcodec, asset.bit_depth)
    audio_ok = not acodec or acodec in client.audio_codecs
    container_ok = bool(container) and container in client.containers
    return video_ok and audio_ok and not container_ok


def decide(
    asset: Asset,
    client: ClientCapabilities,
    *,
    max_height: int,
    cpu_count: int,
    requested_height: int | None = None,
    encoder_rate: float | None = None,
    copy: Copy | None = None,
    stored_container: str | None = None,
) -> Plan:
    """Pick the cheapest path that will actually play, for this file and this browser.

    `max_height` is the user's configured ceiling for transcoded video. It applies to nothing else:
    a file that direct-plays or remuxes reaches the browser at its own resolution, whatever that
    ceiling says. Downscaling only ever happens on the one path that is re-encoding anyway.

    `requested_height` is somebody having chosen a size from the quality menu, and it OVERRULES
    everything below, including the free path. That is the point of it. Almost every file plays
    untouched, so a menu that could only pick between sizes Sift had already decided to make would
    be missing on exactly the 4K file somebody on a slow connection needs to turn down. Asking for
    720p has to mean 720p arrives, even where the browser could have taken the whole thing.

    It is never the default and never chosen automatically: the free path stays what happens when
    nobody says otherwise.

    `stored_container` is the container the file is kept in, where `asset` is the copy being
    served (`as_served`). A repackaged copy's sentence names it, as the conversion's sentence
    before the copy existed did; the copy's own container is not the one the browser refused.
    """
    if requested_height is not None:
        return _requested_plan(
            asset, requested_height, cpu_count=cpu_count, encoder_rate=encoder_rate
        )

    container = (asset.container or "").lower()
    vcodec = (asset.vcodec or "").lower()
    acodec = (asset.acodec or "").lower()

    # An image or a GIF has no codec to be incompatible with and nothing to segment. It is served
    # as itself, which is what the browser wanted anyway.
    if asset.media_type != "video":
        return Plan(route=Route.DIRECT, reason="This is not a video.")

    # A video nobody has read has no codecs to compare and no length to cut. Falling through to a
    # conversion of one segment under a sentence blaming the browser would be wrong; the honest
    # answer is that Sift has not looked yet.
    if asset.probed_at is None:
        return _unread_plan(asset, client)

    video_ok = client.plays(vcodec, asset.bit_depth)
    # A file with no audio track has no audio problem. `acodec` is NULL for a silent clip, and
    # treating that as an unsupported codec would transcode a perfectly playable file.
    audio_ok = not acodec or acodec in client.audio_codecs
    container_ok = bool(container) and container in client.containers

    if video_ok and audio_ok and container_ok:
        if copy is not None:
            # Same streams, a second copy. Worth saying rather than calling it ordinary direct
            # play: somebody looking at the stats panel should see that a copy is what they are
            # watching, and that nothing was lost making it.
            #
            # WHICH copy, because the two have nothing to do with each other. An
            # interleave-repaired file is an MP4 whose copy is an MP4 (the interleave measurement
            # only ever reads MP4 at all), so the repackaging sentence would tell it its browser
            # could not read a container the browser reads perfectly well.
            return Plan(route=Route.REMUX, reason=_copy_reason(copy, stored_container))
        return Plan(route=Route.DIRECT, reason="Your browser can play this file as it is.")

    # THE REMUX TIER: a whole-file repackage, not per-segment copies.
    #
    # Keyframe-aware segmentation (reading the source's real keyframes and cutting the playlist
    # on those) IS NOT ENOUGH. Cutting on keyframes fixes where a segment starts and does nothing
    # about where it ends, because ffmpeg's `-t` bounds a copy by DECODE timestamps while a
    # boundary is a PRESENTATION one. In the test fixture the keyframe presented at 4.023 s decodes
    # at 3.690 s, so a segment asked for four seconds came back holding 4.44: the same overlap,
    # smaller. Worse and unfixable by any choice of boundary: with B-pyramids some frames decode
    # AFTER a keyframe and present BEFORE it, so no cut in decode order is a clean cut in time.
    #
    # So the tier is delivered the way the rest of Sift already delivers this: the file is
    # REPACKAGED WHOLE into MP4, once, as a derivative (a stream copy, no decode, no encode,
    # nothing lost) and then DIRECT-PLAYED. `as_served` models exactly that, the repair job builds
    # it, and direct play is the cheapest path in the system. The segmentation problem stops
    # existing rather than being mitigated.
    #
    # The copy is built in the background, so the FIRST viewing of such a file still converts.
    # Every viewing after it is free. See `needs_repackaging`.
    return _transcode_plan(
        asset,
        vcodec=vcodec,
        max_height=max_height,
        cpu_count=cpu_count,
        encoder_rate=encoder_rate,
        refused=_refusal(
            vcodec=vcodec,
            acodec=acodec,
            container=container,
            video_ok=video_ok,
            audio_ok=audio_ok,
            # The browser plays this codec and not at this depth: the sentence has to say which,
            # or it names a codec the person knows the browser plays.
            ten_bit=bool(vcodec) and vcodec in client.video_codecs and not video_ok,
        ),
    )


def _refusal(
    *,
    vcodec: str,
    acodec: str,
    container: str,
    video_ok: bool,
    audio_ok: bool,
    ten_bit: bool,
) -> tuple[str, str]:
    """What the browser refused, as the start of the sentence the player shows, and what follows
    the conversion's own words.

    THE THING REFUSED, NOT THE VIDEO CODEC BY DEFAULT. A Matroska file of VP9 and Opus, sent to a
    browser that lists both, is refused for its box alone; blaming the picture's codec there tells
    the reader "Your browser cannot play VP9", the one thing they know to be untrue. The
    capabilities say which of the three failed,
    and the first that did is named: the picture, then the sound, then the container. A picture
    the browser cannot decode needs converting whatever box it is in, so it is the reason when
    more than one fails; the container is the reason only when both streams would play.
    """
    label = vcodec.upper() or "this video"
    if not video_ok:
        # "Cannot play HEVC" to somebody whose browser plays HEVC every day is a sentence that
        # reads as wrong; the ten-bit kind is the thing.
        if ten_bit:
            return f"Your browser cannot play the 10-bit {label} this file is", ""
        return f"Your browser cannot play {label}", ""
    if not audio_ok:
        return f"Your browser cannot play this file's sound ({acodec.upper()})", ""
    # Only the box is wrong, which is the one refusal a copy cures: the caller asks for a
    # repackaged copy of exactly this shape of file (`needs_repackaging`), and this viewing
    # converts only because that copy is a whole file and is not there yet. Said, so the reader
    # knows the next viewing is the ordinary one.
    named = CONTAINER_NAMES.get(container, container.upper() or "this one")
    return (
        f"Your browser can't play files in this container ({named})",
        " Sift is making a repackaged copy, and plays it from that once it is ready.",
    )


def _requested_plan(
    asset: Asset, height: int, *, cpu_count: int, encoder_rate: float | None
) -> Plan:
    """Somebody picked a size. Give them that size, and say whether it will keep up.

    Clamped to the source rather than refused, because a request for something taller than the file
    is not an error worth an error message: it is the file at its own size, which is what the
    menu called it.

    `streamable` is still answered honestly. The person has overruled the automatic decision, not
    the arithmetic, and a rung this machine cannot sustain is worth saying so about before they
    watch it stutter.
    """
    wanted = min(height, asset.height) if asset.height else height
    scale = wanted if asset.height and wanted < asset.height else None
    ratio = projected_realtime(
        width=asset.width,
        height=asset.height,
        fps=asset.fps,
        vcodec=(asset.vcodec or "").lower(),
        cpu_count=cpu_count,
        scale_height=scale,
        encoder_rate=encoder_rate,
    )
    return Plan(
        route=Route.TRANSCODE,
        reason=f"Showing this at {wanted}p because you chose that size.",
        scale_height=scale,
        projected_realtime=ratio,
        streamable=ratio is None or ratio >= tuning.MIN_REALTIME_RATIO,
    )


#: The container name the ingress gate gives a video mime (the same vocabulary a browser's
#: capabilities are reported in), so a file nobody has read can still be sent as it is when the
#: box it came in is one the browser opens. One source: the allowlist.
_CONTAINER_BY_MIME: dict[str, str] = {
    media.mime: media.name for media in ALLOWED_MEDIA if media.kind is Kind.VIDEO
}


def _unread_plan(asset: Asset, client: ClientCapabilities) -> Plan:
    """What to say about a video Sift has not read.

    Where the gate's mime names a container this browser reads, the file goes as it is: the
    browser decides on the streams inside, which is what it does for a file off a web page. Where
    it does not, there is nothing to convert from (a plan needs the codecs and the length), so
    the answer is a state of its own that the player draws and re-asks about.
    """
    container = _CONTAINER_BY_MIME.get((asset.mime or "").lower())
    if container and container in client.containers:
        return Plan(
            route=Route.DIRECT,
            reason="Sift has not read this file yet, so it is played as it is.",
        )
    return Plan(
        route=Route.UNREAD,
        reason="Sift has not read this file yet. It will play once it has been read.",
        streamable=True,
    )


def _transcode_plan(
    asset: Asset,
    *,
    vcodec: str,
    max_height: int,
    cpu_count: int,
    encoder_rate: float | None = None,
    refused: tuple[str, str] | None = None,
) -> Plan:
    """The expensive path, and how much of the picture survives it.

    Somebody who wants to watch 4K gets 4K, and a downscaled copy only where smooth playback
    genuinely requires one. So native resolution is tried first and kept whenever the machine can
    sustain it, rather than a blanket cap being applied because 4K is sometimes too much. Stash
    applies its ceiling unconditionally; this does not, and that is a deliberate difference.
    """
    height = asset.height
    native = projected_realtime(
        width=asset.width,
        height=height,
        fps=asset.fps,
        vcodec=vcodec,
        cpu_count=cpu_count,
        encoder_rate=encoder_rate,
    )
    # The sentence names what the browser refused (see `_refusal`); a caller that says nothing
    # is one that has not asked, and the picture's codec is the plain default.
    cannot, then = refused or (f"Your browser cannot play {vcodec.upper() or 'this video'}", "")
    # What follows the conversion's words: the refusal's own sequel, then the colours. The HDR
    # line is said here, on the one path that re-encodes: a direct play or a copy keeps the HDR
    # picture as it is, and only the conversion maps it.
    after = then + (
        " Its colors are HDR and are being mapped to what an ordinary screen shows."
        if asset.is_hdr
        else ""
    )

    # Unmeasurable, or comfortably fast at full resolution: send the picture as it is.
    if native is None or native >= tuning.MIN_REALTIME_RATIO:
        return Plan(
            route=Route.TRANSCODE,
            reason=f"{cannot}, so it is being converted as you watch.{after}",
            projected_realtime=native,
        )

    # Too slow at native resolution. Downscaling is the only lever that moves the ratio,
    # so it is tried, but only now, and only as far as the user's ceiling allows.
    #
    # The ceiling is a SHORT SIDE, like every name on the ladder. Applied as a height it would
    # leave a phone's 2160x3840 alone under a 2160p ceiling (3840 tall is "more than 2160", and the
    # comparison reads the wrong side), while a 1080-wide portrait file under a 1080p ceiling would
    # be reduced to 1080 tall, which is 607 across: a picture nobody would call 1080p.
    reduced = _height_for_short(max_height, asset.width, height)
    if reduced is not None:
        scaled = projected_realtime(
            width=asset.width,
            height=height,
            fps=asset.fps,
            vcodec=vcodec,
            cpu_count=cpu_count,
            scale_height=reduced,
            encoder_rate=encoder_rate,
        )
        if scaled is not None and scaled >= tuning.MIN_REALTIME_RATIO:
            return Plan(
                route=Route.TRANSCODE,
                reason=(
                    f"{cannot}. This machine cannot convert it at full "
                    f"size fast enough to play smoothly, so it is being reduced to {max_height}p. "
                    "Raise the limit in Playback settings if your machine can manage more."
                    f"{after}"
                ),
                scale_height=reduced,
                projected_realtime=scaled,
                streamable=True,
            )
        native = scaled if scaled is not None else native

    # Even downscaled, this machine cannot keep up. Say so rather than spinning.
    return Plan(
        route=Route.TRANSCODE,
        reason=(
            f"{cannot}, and this machine cannot convert it fast enough to "
            f"play smoothly \u2014 it would keep pausing to catch up. You can try anyway.{after}"
        ),
        scale_height=reduced,
        projected_realtime=native,
        streamable=False,
    )


def _height_for_short(ceiling: int, width: int | None, height: int | None) -> int | None:
    """The height that brings a picture's SHORT side down to the ceiling, or None when it is
    already within it. Landscape is the identity case: the short side is the height."""
    if not height or height <= 0:
        return None
    short = min(width, height) if width and width > 0 else height
    if short <= ceiling:
        return None
    if short == height:
        return ceiling
    scaled = round(ceiling * height / short)
    return scaled - scaled % 2


# --- Segment arithmetic ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Timeline:
    """Where every segment of one stream starts, and how long the whole thing is.

    Two kinds of stream need two different answers and the rest of the slice should not have to
    know which it is holding.

    A **converted** stream is cut on a clock. The encoder is told to put a keyframe exactly on
    every boundary, so any boundary is a legal place to start, and even segments make the
    arithmetic trivial.

    A **copied** stream cannot be cut anywhere at all. Nothing is being encoded, so nothing can be
    asked to put a keyframe where it is wanted: the cuts have to land where the file already has
    them, which is wherever whoever made it decided, and they are not evenly spaced.
    """

    starts: tuple[float, ...]
    total_seconds: float

    @property
    def count(self) -> int:
        return len(self.starts)

    def bounds(self, index: int) -> tuple[float, float]:
        """The start offset and length in seconds of one segment.

        The last segment is short: a 5.4-second clip in 2-second segments is 2 s, 2 s, 1.4 s, not
        three full ones.
        """
        if index < 0 or index >= len(self.starts):
            return 0.0, 0.0
        start = self.starts[index]
        end = self.starts[index + 1] if index + 1 < len(self.starts) else self.total_seconds
        return start, max(0.0, end - start)


def uniform_timeline(duration_ms: int | None) -> Timeline:
    """A file cut on a fixed clock, which is every converted stream.

    A file with no known duration gets one segment. That is not a guess at its length: it is the
    honest answer for something with no timeline, and the player finds the end when it reaches it.
    """
    if not duration_ms or duration_ms <= 0:
        return Timeline(starts=(0.0,), total_seconds=tuning.SEGMENT_SECONDS)
    total = duration_ms / 1000
    count = max(1, math.ceil(total / tuning.SEGMENT_SECONDS))
    return Timeline(
        starts=tuple(index * tuning.SEGMENT_SECONDS for index in range(count)),
        total_seconds=total,
    )


def segment_count(duration_ms: int | None) -> int:
    """How many segments a converted file of this length is cut into."""
    return uniform_timeline(duration_ms).count


def segment_bounds(index: int, duration_ms: int | None) -> tuple[float, float]:
    """The start offset and length in seconds of one segment of a converted file."""
    return uniform_timeline(duration_ms).bounds(index)


# --- Where to start from ------------------------------------------------------------------------
#
# IN THE KERNEL, and the reason is worth reading before moving it here.
#
# The player is not the only thing that cares where somebody has got to: a tile draws a bar along
# its bottom edge for a file you are part-way through, and `viewed:continue` narrows to exactly
# those files. Neither the grid nor the search may import a slice, so the rule here would need a
# second copy of it in each, and three copies of an arithmetic rule is three rules the first time
# one of them is edited.
#
# It is `sift.kernel.content.user_state.resume_point`, beside the `resume_ms` column it reads,
# with `resume_minimum_ms` for the two settings that parameterise it and `RESUMING` for the SQL
# form the statement needs. This slice still OWNS those two settings: their labels, their help
# and their choices are registered in `player/__init__.py`.


# --- What counts as a view ------------------------------------------------------------------

# IN THE KERNEL, for the reason `resume_point` above is: a second reader. Insights counts a day's
# views, and a slice may not import this one, so the rule here would need a copy of it there. It
# is `sift.kernel.content.view_rule`, re-exported here under the same names (see the imports) so
# every caller in this slice reads `policy.watch_needed` and `policy.counts_as_a_view`.

# How close to the end is the end.
#
# Ninety-five per cent, which is the credits of a film and the last beat of a clip. Deliberately not
# a duration from the end: fifteen seconds short of a two-hour film is the credits and fifteen
# seconds short of a twenty-second clip is most of it, which is the same trap the resume tail above
# is shaped to avoid.
_FINISHED_PERCENT = 95


def watched_to_the_end(duration_ms: int | None, position_ms: int | None) -> bool:
    """Whether the playhead got close enough to the end to call it finished.

    False for anything with no timeline. A photograph is not finished, it is looked at, and marking
    every still in a library complete the first time it is opened would empty the one filter that
    separates "seen it all" from "started it" of everything worth having in it.
    """
    if not duration_ms or duration_ms <= 0:
        return False
    if position_ms is None or position_ms <= 0:
        return False
    return position_ms * 100 >= duration_ms * _FINISHED_PERCENT
