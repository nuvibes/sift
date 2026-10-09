# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which of the three ways to play a file this browser needs, and whether it can be played at all.

Direct play, then remux (a whole-file copy), then transcode, cheapest first, decided by what the
browser reports it can do. A transcode that cannot keep up with realtime is reported, not offered.
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

#: Containers a browser is asked about: the ingress gate's four, in its own vocabulary.
VIDEO_CONTAINERS = frozenset({"mp4", "mov", "mkv", "webm"})

#: Each container's own format name, for a sentence a person reads.
CONTAINER_NAMES: dict[str, str] = {
    "mp4": "MP4",
    "mov": "QuickTime",
    "mkv": "Matroska",
    "webm": "WebM",
}


class Copy(StrEnum):
    """Why a file is served from a second copy: both kinds write `remux`, so the caller says."""

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
    """What one browser reported it can handle, in Sift's codec and container names.

    The browser's MIME strings are translated in the client, the only party that knows them.
    """

    video_codecs: frozenset[str]
    audio_codecs: frozenset[str]
    containers: frozenset[str]
    #: The codecs also decoded at ten bits a sample; each is in `video_codecs` too.
    video_codecs_10bit: frozenset[str] = frozenset()

    def plays(self, vcodec: str, bit_depth: int | None) -> bool:
        """Whether this browser decodes this codec at this depth; an unknown depth is eight."""
        if not vcodec or vcodec not in self.video_codecs:
            return False
        if bit_depth is not None and bit_depth > 8:
            return vcodec in self.video_codecs_10bit
        return True

    @classmethod
    def nothing(cls) -> ClientCapabilities:
        """A client that has reported nothing yet: everything transcodes, the safe direction."""
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


#: The work budget of one CPU core, in encoded pixels per second. Calibrated on HEVC 10-bit
#: 1080p60 at 2.2x realtime on 4 cores; deliberately conservative at the 4K boundary.
PIXELS_PER_SECOND_PER_CORE = 110_000_000

#: Decoding a frame costs about half of encoding one, as the downscaling measurement shows;
#: charged apart, so a 4K decode that alone exceeds the budget falls out of the arithmetic.
_DECODE_COST_RELATIVE_TO_ENCODE = 0.5

#: How much more a codec costs to decode than H.264: the only codec-based number here.
_DECODE_COST: dict[str, float] = {
    "av1": 1.6,
    "hevc": 1.2,
}

#: The frame rate assumed for a file probed before `fps` was kept; the first segment corrects it.
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

    With hardware encoding, decode and encode run together, so the slower half decides.
    """
    if not width or not height or width <= 0 or height <= 0:
        # An unprobed file: no answer, and the caller lets it through.
        return None

    rate = fps if fps and fps > 0 else _ASSUMED_FPS

    # Downscaling cheapens the encode, not the decode, which stays at full resolution.
    decoded_pixels = width * height * rate
    encoded_pixels = decoded_pixels
    if scale_height is not None and scale_height < height:
        encoded_pixels = decoded_pixels * (scale_height / height) ** 2

    decode = decoded_pixels * _DECODE_COST_RELATIVE_TO_ENCODE * _decode_multiplier(vcodec)
    processor = PIXELS_PER_SECOND_PER_CORE * max(1, cpu_count)

    if encoder_rate is None or encoder_rate <= 0:
        return processor / (decode + encoded_pixels)

    # Decode on the processor and encode on the card together: the slower half sets the pace.
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
    """How long this machine should take to decode that much of this file. None if unknowable.

    Subtracted from a segment's time to leave the encoder's, with `projected_realtime`'s estimate.
    """
    if not width or not height or width <= 0 or height <= 0:
        return None
    rate = fps if fps and fps > 0 else _ASSUMED_FPS
    cost = width * height * rate * _DECODE_COST_RELATIVE_TO_ENCODE * _decode_multiplier(vcodec)
    budget = PIXELS_PER_SECOND_PER_CORE * max(1, cpu_count)
    return video_seconds * cost / budget


def _decode_multiplier(vcodec: str | None) -> float:
    return _DECODE_COST.get((vcodec or "").lower(), 1.0)


class Rung(NamedTuple):
    """One step of the ladder: its name (the short side) and the height it is encoded at."""

    #: The short side, and the name: 720 is "720p", landscape or portrait.
    short: int
    height: int


def rungs(source_width: int | None, source_height: int | None) -> tuple[Rung, ...]:
    """The steps worth offering for a file this size, largest first, chosen by the short side.

    Strictly below the source, so none is an upscale; a file with no known size gets none.
    """
    if not source_height or source_height <= 0:
        return ()
    short = min(source_width, source_height) if source_width and source_width > 0 else source_height
    steps: list[Rung] = []
    for rung in tuning.LADDER_HEIGHTS:
        if rung >= short:
            continue
        height = rung if short == source_height else round(rung * source_height / short)
        # Even: an encoder refuses an odd dimension.
        height -= height % 2
        if 0 < height < source_height:
            steps.append(Rung(short=rung, height=height))
    return tuple(steps)


#: What people call a video of a given size, by its short side, largest first.
_SIZE_NAMES: tuple[tuple[int, str], ...] = (
    (2160, "4K"),
    (1440, "1440p"),
    (1080, "1080p"),
    (720, "720p"),
    (480, "480p"),
    (360, "360p"),
)


def size_name(width: int | None, height: int | None) -> str | None:
    """What to call a video of this size, or None when it was never measured."""
    if not width or not height or width <= 0 or height <= 0:
        return None
    short = min(width, height)
    for floor, name in _SIZE_NAMES:
        if short >= floor:
            return name
    return f"{width} x {height}"


def scaled_width(source_width: int | None, source_height: int | None, height: int) -> int | None:
    """How wide this file becomes at a given height, kept even (`scale=-2:h`); None if unknown."""
    if not source_width or not source_height or source_height <= 0:
        return None
    width = round(source_width * height / source_height)
    return max(2, width - (width % 2))


#: The H.264 levels from the specification's Table A-1: (name, hex, macroblocks a second, a frame).
_H264_LEVELS: tuple[tuple[str, str, int, int], ...] = (
    ("3.0", "1e", 40_500, 1_620),
    ("3.1", "1f", 108_000, 3_600),
    ("3.2", "20", 216_000, 5_120),
    # 4.0 is left out: 4.1's limits with a bitrate cap a constant-quality encode can break.
    ("4.1", "29", 245_760, 8_192),
    ("4.2", "2a", 522_240, 8_704),
    ("5.0", "32", 589_824, 22_080),
    ("5.1", "33", 983_040, 36_864),
    ("5.2", "34", 2_073_600, 36_864),
    ("6.0", "3c", 4_177_920, 139_264),
    ("6.1", "3d", 8_355_840, 139_264),
    ("6.2", "3e", 16_711_680, 139_264),
)

#: The profile bytes for High profile with no constraint flags.
_H264_HIGH_PROFILE = "6400"

_AAC_LC = "mp4a.40.2"


def h264_level(width: int | None, height: int | None, fps: float | None) -> tuple[str, str]:
    """The lowest H.264 level that can legally carry this picture: `(name, hex)`.

    Unknown dimensions get the highest, so nothing is under-promised.
    """
    if not width or not height or width <= 0 or height <= 0:
        return _H264_LEVELS[-1][0], _H264_LEVELS[-1][1]

    # Partial 16x16 macroblocks are padded to whole ones.
    blocks = -(-width // 16) * -(-height // 16)
    rate = fps if fps and fps > 0 else _ASSUMED_FPS
    per_second = blocks * rate

    for name, code, max_rate, max_frame in _H264_LEVELS:
        if per_second <= max_rate and blocks <= max_frame:
            return name, code
    return _H264_LEVELS[-1][0], _H264_LEVELS[-1][1]


def codec_string(width: int | None, height: int | None, fps: float | None) -> str:
    """What a master playlist declares this variant is, as HLS spells it: H.264 High and AAC-LC."""
    _, code = h264_level(width, height, fps)
    return f"avc1.{_H264_HIGH_PROFILE}{code},{_AAC_LC}"


#: A repaired copy is always MP4.
REPAIRED_CONTAINER = "mp4"
REPAIRED_MIME = "video/mp4"


#: What to say about each kind of copy; the repaired one says the outcome, not the cause.
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
    """The sentence for a file played from a second copy, naming the container it is stored in."""
    if copy is not Copy.REPACKAGED or not stored_container:
        return _COPY_REASON[copy]
    container = stored_container.lower()
    named = CONTAINER_NAMES.get(container, container.upper())
    return (
        f"Your browser cannot read this file's container ({named}), so it is playing from a "
        "repackaged copy. The video and audio are the originals, untouched."
    )


def as_served(asset: Asset, *, repaired: bool) -> Asset:
    """The file the browser will be sent: a repaired copy changes only the container (to MP4)."""
    if not repaired:
        return asset
    return replace(asset, container=REPAIRED_CONTAINER, mime=REPAIRED_MIME)


def needs_repackaging(asset: Asset, client: ClientCapabilities) -> bool:
    """Whether a repackaged copy would let this browser play the file for free, asked as stored."""
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

    `max_height` caps transcoded video only. `requested_height` is a size picked from the quality
    menu and overrules everything, the free path included.
    """
    if requested_height is not None:
        return _requested_plan(
            asset, requested_height, cpu_count=cpu_count, encoder_rate=encoder_rate
        )

    container = (asset.container or "").lower()
    vcodec = (asset.vcodec or "").lower()
    acodec = (asset.acodec or "").lower()

    # An image or a GIF is served as itself.
    if asset.media_type != "video":
        return Plan(route=Route.DIRECT, reason="This is not a video.")

    # A video nobody has read has nothing to compare; it is not a conversion.
    if asset.probed_at is None:
        return _unread_plan(asset, client)

    video_ok = client.plays(vcodec, asset.bit_depth)
    # A silent clip's NULL `acodec` is no audio problem.
    audio_ok = not acodec or acodec in client.audio_codecs
    container_ok = bool(container) and container in client.containers

    if video_ok and audio_ok and container_ok:
        if copy is not None:
            # Said as a copy, naming which: the two kinds have nothing to do with each other.
            return Plan(route=Route.REMUX, reason=_copy_reason(copy, stored_container))
        return Plan(route=Route.DIRECT, reason="Your browser can play this file as it is.")

    # The remux tier is a whole-file repackage that is then direct-played: copied segments cannot be
    # cut cleanly in time. The first viewing converts while the copy is built (`needs_repackaging`).
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
            # Plays this codec but not at this depth: the sentence must say which.
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
    """What the browser refused, as the start of the player's sentence, and what follows it.

    The picture, then the sound, then the container: the first that failed is named.
    """
    label = vcodec.upper() or "this video"
    if not video_ok:
        if ten_bit:
            return f"Your browser cannot play the 10-bit {label} this file is", ""
        return f"Your browser cannot play {label}", ""
    if not audio_ok:
        return f"Your browser cannot play this file's sound ({acodec.upper()})", ""
    # Only the box is wrong, which a copy cures; said so the reader knows the next viewing is free.
    named = CONTAINER_NAMES.get(container, container.upper() or "this one")
    return (
        f"Your browser can't play files in this container ({named})",
        " Sift is making a repackaged copy, and plays it from that once it is ready.",
    )


def _requested_plan(
    asset: Asset, height: int, *, cpu_count: int, encoder_rate: float | None
) -> Plan:
    """Somebody picked a size: give them it, clamped to the source, and say whether it keeps up."""
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


#: The container the ingress gate gives a video mime, so an unread file in a readable box plays.
_CONTAINER_BY_MIME: dict[str, str] = {
    media.mime: media.name for media in ALLOWED_MEDIA if media.kind is Kind.VIDEO
}


def _unread_plan(asset: Asset, client: ClientCapabilities) -> Plan:
    """What to say about a video Sift has not read: as it is in a readable box, else a state."""
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
    """The expensive path: native resolution where the machine keeps up, downscaled only if not."""
    height = asset.height
    native = projected_realtime(
        width=asset.width,
        height=height,
        fps=asset.fps,
        vcodec=vcodec,
        cpu_count=cpu_count,
        encoder_rate=encoder_rate,
    )
    # The sentence names what was refused (`_refusal`); the codec is the default.
    cannot, then = refused or (f"Your browser cannot play {vcodec.upper() or 'this video'}", "")
    # The HDR line belongs to the one path that re-encodes.
    after = then + (
        " Its colors are HDR and are being mapped to what an ordinary screen shows."
        if asset.is_hdr
        else ""
    )

    if native is None or native >= tuning.MIN_REALTIME_RATIO:
        return Plan(
            route=Route.TRANSCODE,
            reason=f"{cannot}, so it is being converted as you watch.{after}",
            projected_realtime=native,
        )

    # Too slow at native resolution: downscale, only as far as the ceiling, a short side.
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
    """The height that brings the short side down to the ceiling, or None when already within it."""
    if not height or height <= 0:
        return None
    short = min(width, height) if width and width > 0 else height
    if short <= ceiling:
        return None
    if short == height:
        return ceiling
    scaled = round(ceiling * height / short)
    return scaled - scaled % 2


@dataclass(frozen=True, slots=True)
class Timeline:
    """Where every segment of one stream starts, and how long the whole thing is.

    A converted stream is cut on a clock; a copied one only where the file has keyframes.
    """

    starts: tuple[float, ...]
    total_seconds: float

    @property
    def count(self) -> int:
        return len(self.starts)

    def bounds(self, index: int) -> tuple[float, float]:
        """The start offset and length in seconds of one segment; the last is short."""
        if index < 0 or index >= len(self.starts):
            return 0.0, 0.0
        start = self.starts[index]
        end = self.starts[index + 1] if index + 1 < len(self.starts) else self.total_seconds
        return start, max(0.0, end - start)


def uniform_timeline(duration_ms: int | None) -> Timeline:
    """A file cut on a fixed clock, as every converted stream is; no duration is one segment."""
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


# Where to start from lives in the kernel (`user_state.resume_point`), since the grid and search
# read it too; this slice still owns the two settings.


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
