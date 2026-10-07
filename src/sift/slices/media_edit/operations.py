# SPDX-License-Identifier: AGPL-3.0-or-later
"""What an edit IS: the five things somebody can ask for, what each one is called on disk, and the
exact ffmpeg command behind it.

Pure functions and tables, the same as the compression side: an argument list rather than a shell
string, built by something a test can call, so what Sift asks ffmpeg for is read rather than
inferred from a log.

Two facts shape the whole module:

**The scratch file has no extension, on purpose.** The write seam builds every produced file at a
path the folder scan walks straight past, which means ffmpeg cannot infer the format from the name
and would otherwise guess. Compress gets away with a single `-f mp4` because it only ever makes one
kind of file. An edit does not: a photograph keeps its own format and a cut keeps its source
container. So **every builder here passes `-f`, and the still builders pass the codec too.** The
formats are mapped from the ingress allowlist rather than listed again, and the map is checked
against it at import: a format added to Sift and forgotten here is an error on the way up, not a
file nobody can save.

**A seek of zero is left out entirely rather than passed as `-ss 0`.** Before the input it is a
no-op on some ffmpeg builds and, on others, discards the only frame of a single-frame source and
exits successfully having written nothing. An empty file and a zero exit code is the hardest kind
of failure to attribute.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sift.kernel.config import Settings
from sift.kernel.ingress import ALLOWED_MEDIA, Kind
from sift.kernel.media import background_flags, background_threads, seconds
from sift.slices.media_edit.tuning import (
    AVIF_CRF,
    AVIF_SPEED_PRESET,
    GIF_FPS,
    GIF_SHORT_EDGE,
    WEBP_QUALITY,
)

__all__ = [
    "GIF_FORMATS",
    "MOVING_FORMATS",
    "QUARTER_TURNS",
    "SEVERAL",
    "STILL_FORMATS",
    "Operation",
    "Turn",
    "crop_filter",
    "cut_args",
    "gif_args",
    "moving_format_for",
    "resize_filter",
    "scale_filter",
    "still_args",
    "still_format_for",
    "turn_filter",
]


class Operation(StrEnum):
    """The six verbs, and what the provenance row records.

    `trim` and `clip` are one command underneath (a start and a length), framed two ways on
    screen: dropping the ends against taking a piece out of the middle. They stay two names here
    because the person chose between two ideas and the record should say which, but there is only
    one cut and it is built once.

    `gif` takes the same two numbers and is NOT that command. Everything else here keeps the file's
    own format (a cut lands in the container it came from and a photograph keeps the format it was)
    and this one changes it, which is why it has a table of its own below rather than an entry in
    `MOVING_FORMATS`. That table is keyed by the SOURCE's format and answers "what does a piece of
    this land in"; a GIF is a destination somebody asked for.
    """

    CROP = "crop"
    RESIZE = "resize"
    ROTATE = "rotate"
    TRIM = "trim"
    CLIP = "clip"
    GIF = "gif"


#: The three a photograph is offered, and the three a video is.
ON_STILLS = frozenset({Operation.CROP, Operation.RESIZE, Operation.ROTATE})
ON_MOVING = frozenset({Operation.TRIM, Operation.CLIP, Operation.GIF})


class Turn(StrEnum):
    """Which way round the picture goes. Quarter turns, and the two mirrors.

    Turns are quarters rather than an angle in degrees, because these are the only ones that cost
    nothing in quality on a decoded frame and the only ones anybody asks for. An arbitrary angle has
    to invent pixels in the corners, which is a different feature and a worse one.

    A mirror is not a turn and is kept here anyway. It is the same shape of request (one named way
    of putting the picture round, no numbers, no refusals of its own), and separating them would
    mean a second operation, a second verb in the record and a second branch everywhere, to say a
    thing the person experiences as the fourth button in the same row.
    """

    RIGHT = "right"
    LEFT = "left"
    HALF = "half"
    #: Left becomes right. What a mirror does.
    MIRROR = "mirror"
    #: Top becomes bottom. The same mirror, across the other line.
    FLIP = "flip"


_TURN_FILTER = {
    # ffmpeg counts the other way round from the way people say it: `transpose=1` is clockwise.
    Turn.RIGHT: "transpose=1",
    Turn.LEFT: "transpose=2",
    # Twice counter-clockwise, rather than an `hflip,vflip` pair that reads as a mirror.
    Turn.HALF: "transpose=2,transpose=2",
    Turn.MIRROR: "hflip",
    Turn.FLIP: "vflip",
}

#: The turns that swap the picture's width and height. Everything downstream that reports a size
#: (the numbers under the picture, the name the copy gets, the check that a later step still fits)
#: has to follow, so which ones do it is stated once here rather than tested for in four places.
QUARTER_TURNS = frozenset({Turn.RIGHT, Turn.LEFT})


@dataclass(frozen=True, slots=True)
class StillFormat:
    """How a photograph is written back out: what to call it, and what to hand ffmpeg."""

    #: The extension the produced file gets, without the dot.
    extension: str
    #: Everything after the filter chain: the codec, its quality, and the muxer.
    args: tuple[str, ...]
    #: Whether saving in this format costs a generation of quality. Said on screen before it runs.
    lossy: bool


# `-update 1` is what makes the image muxer write ONE file to the exact path it was given. Without
# it the muxer expects a numbered pattern, warns that it did not get one, and its behaviour on a
# path that is not a pattern is not something to depend on.
_IMAGE_FILE = ("-f", "image2", "-update", "1")

_JPEG = StillFormat(extension="jpg", args=("-c:v", "mjpeg", "-q:v", "2", *_IMAGE_FILE), lossy=True)
_PNG = StillFormat(extension="png", args=("-c:v", "png", *_IMAGE_FILE), lossy=False)
_WEBP = StillFormat(
    extension="webp", args=("-c:v", "libwebp", "-quality", "90", "-f", "webp"), lossy=True
)
# `-still-picture 1` tells the encoder this is one image rather than the first frame of something,
# which is what lets it spend its whole budget on the frame instead of holding some back for motion.
#
# `-cpu-used 6` is a SPEED: libaom is slow enough by reputation to be worth checking before it is
# put in front of somebody pressing Save. At 4000x3000 (twelve megapixels, larger than anything a
# phone produces), the shipped ffmpeg writes this in about twice JPEG's time, a fraction of a
# second, for a file a sixth of the size: not a cost worth trading a format away for. 8 is no
# faster, so 6 is taken for the better compression at no price.
_AVIF = StillFormat(
    extension="avif",
    args=(
        "-c:v",
        "libaom-av1",
        "-still-picture",
        "1",
        "-cpu-used",
        "6",
        "-crf",
        "30",
        "-f",
        "avif",
    ),
    lossy=True,
)

#: What each still format Sift accepts is written back out as, keyed by the allowlist's own name.
#:
#: Every one keeps its own format except HEIC, and that is not a preference: the ffmpeg Sift ships
#: has no HEIF encoder in it at all: it reads one perfectly well and cannot write one. A photo off
#: a phone is usually HEIC, so refusing to edit them would take the editor away from the commonest
#: picture in a library. It is saved as a JPEG instead, and the panel says so before anything runs.
#:
#: AVIF is NOT that case, though it shares the container: the shipped ffmpeg has both an `avif`
#: muxer and `libaom-av1`, verified by writing one, so an AVIF keeps its own format the way a PNG
#: does. It is worth checking rather than assuming from the HEIC line above: the two look like the
#: same gap and only one of them is one.
STILL_FORMATS: dict[str, StillFormat] = {
    "jpeg": _JPEG,
    "png": _PNG,
    "webp": _WEBP,
    "heic": _JPEG,
    "avif": _AVIF,
}


@dataclass(frozen=True, slots=True)
class MovingFormat:
    """How a cut is written back out, in the source's own container.

    `exact_video` is what makes a piece able to begin exactly where somebody asked. A copied cut
    can only start on a frame that does not depend on an earlier one, which on an ordinary video is
    every few seconds; re-encoding decodes from that frame and throws away what comes before the
    moment, so the piece is the piece. Each container carries its own encoder because a container
    will not hold just any codec (H.264 in a `.webm` is a file ffmpeg refuses to write), and the
    promise "a clip is exactly what you marked" must not quietly depend on which one a person's
    video happens to be in.
    """

    extension: str
    #: What `-f` is given. Not always the extension: a `.mkv` is written by the matroska muxer.
    muxer: str
    #: Whether to move the index to the front so the file can start playing before it has finished
    #: downloading. Only means anything in the one family of containers that has an index to move.
    faststart: bool
    #: How to re-encode the picture for an EXACT cut, in a codec this container can hold.
    exact_video: tuple[str, ...]
    #: How to re-encode the SOUND for an exact cut. Named per container for the same reason the
    #: picture is: a `.webm` will not hold AAC.
    exact_audio: tuple[str, ...]


#: Near-transparent quality for a piece that is seconds long. A cut is not a compression: nobody
#: asked for a smaller file, they asked for the right seconds, so the number is chosen to be
#: indistinguishable from the source rather than to save space. The ladder's own numbers start at
#: 20 and go up, which is the opposite job.
_EXACT_H264 = ("-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p")

#: The sound, rebuilt rather than copied, and ONLY on the exact path.
#:
#: A copied sound track keeps whole audio frames (about 23 ms each), so a copy cannot end where
#: the mark ends, and the file comes out tens of milliseconds long. On a 2.309 s mark: copied
#: 2.333, rebuilt 2.309002. That is the difference between "the same" and "nearly the same",
#: and the same was what was asked for.
#:
#: It costs a generation of audio quality, which is affordable HERE and nowhere else: the picture on
#: this path is being re-encoded anyway, so nothing about an exact cut was ever lossless, and the
#: piece is at most a minute. A trim copies both streams and is untouched by this.
_EXACT_AAC = ("-c:a", "aac", "-b:a", "160k")
_EXACT_OPUS = ("-c:a", "libopus", "-b:a", "160k")

#: The same intent in the one family H.264 cannot go in. VP9 is slower per frame, which is
#: affordable here and only here: an exact cut is offered on a piece of at most a minute.
_EXACT_VP9 = (
    "-c:v",
    "libvpx-vp9",
    "-b:v",
    "0",
    "-crf",
    "24",
    "-cpu-used",
    "4",
    "-row-mt",
    "1",
    "-pix_fmt",
    "yuv420p",
)

MOVING_FORMATS: dict[str, MovingFormat] = {
    "mp4": MovingFormat(
        extension="mp4",
        muxer="mp4",
        faststart=True,
        exact_video=_EXACT_H264,
        exact_audio=_EXACT_AAC,
    ),
    "mov": MovingFormat(
        extension="mov",
        muxer="mov",
        faststart=True,
        exact_video=_EXACT_H264,
        exact_audio=_EXACT_AAC,
    ),
    "mkv": MovingFormat(
        extension="mkv",
        muxer="matroska",
        faststart=False,
        exact_video=_EXACT_H264,
        exact_audio=_EXACT_AAC,
    ),
    "webm": MovingFormat(
        extension="webm",
        muxer="webm",
        faststart=False,
        exact_video=_EXACT_VP9,
        exact_audio=_EXACT_OPUS,
    ),
}


def _by_mime(kind: Kind) -> dict[str, str]:
    """The allowlist's own name for each media type of one kind, looked up by its mime.

    Mime rather than the container string from probing, and that is not interchangeable: `mime` is
    set by the ingress gate from the file's actual signature and is there from the moment the row
    exists, while the container column is whatever ffprobe called the format and is empty until
    probing has run.
    """
    return {media.mime: media.name for media in ALLOWED_MEDIA if media.kind is kind}


_STILL_NAME_BY_MIME = _by_mime(Kind.IMAGE)
_MOVING_NAME_BY_MIME = _by_mime(Kind.VIDEO)


def _check_every_format_has_somewhere_to_go() -> None:
    """Refuse to start rather than be offered on a file that cannot be saved.

    Every format the allowlist accepts must appear in one of the two tables above. Checked rather
    than trusted, and checked as the module loads: adding a format to Sift and forgetting this is
    then an error on the way up, not a refusal somebody meets months later with a photograph in
    front of them and nothing to do about it.

    A raise rather than an assert, because assertions are removed when Python is run optimized and
    a check that disappears in the configuration Sift might be run in is not a check.
    """
    missing = (set(_STILL_NAME_BY_MIME.values()) - STILL_FORMATS.keys()) | (
        set(_MOVING_NAME_BY_MIME.values()) - MOVING_FORMATS.keys()
    )
    if missing:
        raise RuntimeError(f"the editor has nowhere to save: {', '.join(sorted(missing))}")


_check_every_format_has_somewhere_to_go()


def still_format_for(mime: str | None) -> StillFormat | None:
    """How to write this photograph back out, or None when Sift cannot."""
    name = _STILL_NAME_BY_MIME.get(mime or "")
    return STILL_FORMATS.get(name) if name else None


def moving_format_for(mime: str | None) -> MovingFormat | None:
    """Which container a cut of this file lands in, or None when Sift will not cut it.

    A GIF and an animated WebP answer None deliberately, and the reason is the promise a cut makes.
    Trimming is meant to be a stream copy (the same packets in a shorter file, instant and
    lossless) and neither of those can be one. An animated WebP is not readable by ffmpeg at all,
    so what would actually be cut is the video Sift built to display it, which is a different file
    in a different format from the one somebody is looking at. A GIF's frames are a chain where
    each depends on the one before, so a copy starting in the middle is not a shorter GIF, it
    is a broken one. Both would have to be re-encoded, and a trim that quietly re-encodes is the
    one thing this is not allowed to be.
    """
    name = _MOVING_NAME_BY_MIME.get(mime or "")
    return MOVING_FORMATS.get(name) if name else None


# --- what the copy is called --------------------------------------------------------------------


def stamp(milliseconds: int) -> str:
    """A moment in a file, written the way somebody would say it: `1m30s`, `2h5m`, `45s`.

    Units that are zero are left out, except when all of them are: the beginning of a file is
    `0s` rather than nothing at all, because the name it goes into has to say something.
    """
    total = max(0, milliseconds) // 1000
    hours, rest = divmod(total, 3600)
    minutes, second = divmod(rest, 60)
    parts = [
        f"{value}{unit}" for value, unit in ((hours, "h"), (minutes, "m"), (second, "s")) if value
    ]
    return "".join(parts) or "0s"


def suffix(
    operation: Operation,
    *,
    turn: Turn | None = None,
    width: int | None = None,
    height: int | None = None,
    start_ms: int | None = None,
    gif_format: str | None = None,
) -> str:
    """What is added to the original's name, so an edit is recognizable in a file manager.

    Each one says which edit it was, and enough about it that two DIFFERENT edits of one file do
    not land on the same name. That matters more than it sounds: the write seam refuses a name
    something already holds, so two edits colliding is a second edit that will not save until
    somebody renames the first by hand. A crop carries its size, a resize its width, a rotation its
    direction, and a clip the moment it starts, which is the thing that says WHICH piece.

    Two edits that really are the same edit still collide, and that is right. Cropping a photograph
    to exactly the same rectangle twice has produced the file already.

    `gif_format` is which format a GIF is being written in, and is required for that one
    operation. See the branch.
    """
    if operation is Operation.CROP:
        return f"-cropped-{width}x{height}"
    if operation is Operation.RESIZE:
        return f"-{width}px"
    if operation is Operation.ROTATE:
        return _TURN_SUFFIX[turn] if turn else "-rotated"
    if operation is Operation.TRIM:
        return "-trimmed"
    if operation is Operation.GIF:
        # The moment as well as the word, for the reason a clip carries one: two GIFs made
        # from one video would otherwise land on the same name, and the write seam refuses a name
        # something already holds.
        #
        # THE WORD IS THE FORMAT, NOT THE VERB. `gif` written here whichever format was about to
        # be produced would land an AVIF as `name-gif-from-30s.avif`: a stem saying one format over
        # an extension saying another, which is exactly what the caller resolves the format early
        # to prevent. The suffix is decided once, when the copy is made, and nothing here renames
        # anything on disk.
        #
        # Refused rather than defaulted when nobody says which format. A default would be `gif`,
        # which is that fault with a friendlier face, and there is no caller that can reach
        # this without one, because the format is settled before the name is built.
        if gif_format is None:
            raise ValueError("a GIF is named after the format it is written in")
        return f"-{gif_format}-from-{stamp(start_ms or 0)}"
    return f"-from-{stamp(start_ms or 0)}"


#: What each way round is called in a filename. A mirror is not a rotation and must not be named as
#: one: two copies of a photograph called `-rotated-left` and `-rotated-mirror` would sort together
#: and read as two turns, and the second is not a turn at all.
_TURN_SUFFIX = {
    Turn.RIGHT: "-rotated-right",
    Turn.LEFT: "-rotated-left",
    Turn.HALF: "-rotated-180",
    Turn.MIRROR: "-mirrored",
    Turn.FLIP: "-flipped",
}

#: What a copy made by more than one operation at the same time is called.
#:
#: One operation keeps the name that says which it was. Several cannot: the names are built to be
#: recognizable at a glance in a file manager, and four of them end to end is neither recognizable
#: nor short enough to survive the length cap on a name. The person is typing over this whenever
#: they want to; what it has to be is honest and short.
SEVERAL = "-edited"


def output_filename(source_name: str | None, *, suffix_text: str, extension: str) -> str:
    """The original's name, what was done to it, and the extension of what came out.

    The extension is the produced file's own rather than the source's, because they are not always
    the same one: a HEIC comes back as a JPEG, since the ffmpeg Sift ships can read that format and
    cannot write it.
    """
    stem = (source_name or "file").rsplit(".", 1)[0] or "file"
    return f"{stem}{suffix_text}.{extension}"


# --- the still commands -------------------------------------------------------------------------


def still_args(
    source: Path,
    destination: Path,
    *,
    filters: Sequence[str],
    fmt: StillFormat,
    settings: Settings,
) -> list[str]:
    """One frame, one filter chain, one file, however many things that chain does.

    The chain is what makes a Save carrying several operations produce one picture rather than one
    picture per operation. Every still operation is a filter, filters compose in the order they are
    written, and ffmpeg decodes the frame once and writes it once whether the chain is one link or
    four. So a crop followed by a turn is not two runs and two files; it is one run, and the second
    file never exists to have to be cleaned up.

    `-map 0:v:0` takes the first picture and nothing else, which matters more than it looks: a HEIC
    off a phone routinely carries a depth map, a thumbnail or the other frames of a burst alongside
    the photograph, and "the image in this file" is not a well-defined thing without saying which.

    `-noautorotate` because the chain already begins with the photograph's own turn (see
    `Orientation.filters`). ffmpeg would otherwise turn the frame by the same note before the chain
    runs, and the copy would come out turned twice.
    """
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        "-noautorotate",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        # `null` is a real filter that passes the frame through untouched. An empty `-vf` is a
        # parse error, and the chain is empty in one ordinary case: an upright picture asked only
        # for a turn it has already been given by its own flag.
        ",".join(filters) or "null",
        "-frames:v",
        "1",
        *fmt.args,
        str(destination),
    ]


def snap_to_even(left: int, top: int, width: int, height: int) -> tuple[int, int, int, int]:
    """The nearest rectangle ffmpeg can actually cut, which is not always the one that was drawn.

    Colour in almost every photograph is stored at half resolution (one colour sample for each
    two-by-two block of brightness), so a rectangle has to land on those blocks. Handed an odd
    number, the crop filter quietly rounds it, and the file that appears is a pixel narrower or
    shorter than the panel said it would be: a screen promising 784 by 605 would land 784 by 604 on
    disk.

    Rounded DOWN, never up, so a rectangle drawn inside the picture stays inside it. And rounded in
    ONE place: what comes out of here is what names the file, what the screen reports, and what the
    encoder is given, because the alternative is three answers that agree until they do not.
    """
    return left - left % 2, top - top % 2, width - width % 2, height - height % 2


def crop_filter(*, left: int, top: int, width: int, height: int) -> str:
    """Keep the rectangle, throw the rest away.

    No rounding to even numbers, unlike video. That rule exists because several video encoders
    refuse a chroma-subsampled frame with an odd dimension; a still is one frame in an image format
    and an odd number of pixels across is an ordinary picture. Rounding here would silently hand
    back a crop one pixel off the one somebody drew.
    """
    return f"crop={width}:{height}:{left}:{top}"


def resize_filter(*, width: int) -> str:
    """Make it this many pixels across, and let the height follow.

    `-1` on the height keeps the shape. One number rather than two, because two numbers is an
    invitation to squash a picture, and the person who genuinely wants a different shape wants a
    crop.
    """
    return f"scale={width}:-1"


def turn_filter(turn: Turn) -> str:
    """A quarter turn either way, a half turn, or a mirror across either line."""
    return _TURN_FILTER[turn]


# --- the cut ----------------------------------------------------------------------------------


def cut_args(
    source: Path,
    destination: Path,
    *,
    start_ms: int,
    duration_ms: int,
    fmt: MovingFormat,
    settings: Settings,
    exact: bool = False,
) -> list[str]:
    """A piece of the file. One command for both trim and clip; `exact` is the only difference.

    **Copied (`exact=False`), which is what a trim is.** Nothing is decoded and nothing is
    compressed again: the packets that were already there are written into a new container. That is
    what makes it near free on a two-hour video, and it is also why the start is approximate: a
    copied stream can only begin at a frame that does not depend on an earlier one, so the cut lands
    on the last such frame before the moment asked for. Up to a few seconds early, never late.

    **Re-encoded (`exact=True`), which is what a clip is.** The picture is decoded from that same
    frame and the part before the moment is thrown away, so the piece begins exactly where somebody
    put the mark. It costs a real encode, and that is affordable precisely because of what a clip
    is: the editor offers at most sixty seconds of one and a loop is usually a few. A trim runs over
    a whole video and must never take this path.

    **The sound is copied on a trim and rebuilt on a clip.** A copied track keeps whole audio frames
    (about 23 ms each), so it cannot end where the mark ends: on a 2.309 s mark, copied comes out
    2.333 and rebuilt 2.309002. `-shortest` is the other half of it, cutting the output at whichever
    stream runs out first so the piece is never longer than what was marked.

    **`-avoid_negative_ts make_zero` is on the COPY only, never on both.** It stops a copied cut
    opening on a stall: starting part-way in leaves the packets timed from where they were in the
    original, and a player handed a file whose clock begins somewhere else either waits out the
    difference or refuses. On a RE-ENCODE it does the opposite of nothing: the encoder restamps
    the picture from zero while the sound still carries its original timestamps, and the shift is
    computed from the sound, which pushes the picture out and stretches the file. On that same 2.309
    s mark: **5.025 s with it, 2.309 s without.**

    The seek goes before the input so ffmpeg jumps to it; the length goes after, where it measures
    the output rather than how much of the input to read. That placement is right for both: on a
    copy it is the only accurate thing to do, and on a re-encode ffmpeg seeks to the nearest whole
    frame and then decodes forward to the moment, which is what makes the start exact.
    """
    streams: tuple[str, ...] = (
        (*fmt.exact_video, *fmt.exact_audio, "-shortest")
        if exact
        # `make_zero` belongs to this branch and only to this branch. See the note above: on a
        # re-encode it stretches the result to more than twice what was asked for.
        else ("-c:v", "copy", "-c:a", "copy", "-avoid_negative_ts", "make_zero")
    )
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        *(["-ss", seconds(start_ms)] if start_ms > 0 else []),
        "-i",
        str(source),
        "-t",
        seconds(duration_ms),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        *streams,
        *(["-threads", str(background_threads(settings))] if exact else []),
        *(["-movflags", "+faststart"] if fmt.faststart else []),
        "-f",
        fmt.muxer,
        str(destination),
    ]


#: The three formats a GIF can be written as, and everything that differs between them.
#:
#: All three ANIMATE, and that is checked rather than assumed: Sift's own ingress gate reads a
#: `.webp` and a `.avif` by signature and files an animated one as `Kind.GIF` (`webp-animated`
#: and `avif-sequence`), so a GIF in any of the three is a GIF in the library.
#: Through `verify_ingress`, on a 3-second clip at these numbers:
#:
#:     gif    512,348 bytes   -> media_type 'gif'            kind 'gif'
#:     webp   186,622 bytes   -> media_type 'webp-animated'  kind 'gif'
#:     avif    14,513 bytes   -> media_type 'avif-sequence'  kind 'gif'
#:
#: !! **The same measurement on a 0.4-SECOND fixture says all three are STILLS**, because a
#: sequence that short is written as one frame and the signature says so honestly. A GIF
#: format verified on a fixture is not verified: the same trap the `-ss 0` note above records.
#:
#: The sizes are why the choice is offered rather than fixed: AVIF is **35x smaller than GIF** for
#: the same seconds and the same picture. GIF is the default anyway, because the verb is "make a
#: GIF", because it is the one of the three that opens anywhere, and because somebody who saves one
#: to send elsewhere means a `.gif`. The other two are a setting away and the panel always says
#: which is about to be written.


@dataclass(frozen=True, slots=True)
class GifFormat:
    """How one GIF format is written.

    `graph` is a whole `-filter_complex` for GIF and None for the other two, and that asymmetry is
    the format rather than a preference: a GIF holds 256 colours and needs a palette built from the
    frames that will actually be written, which takes a forked graph. WebP and AVIF carry their own
    colour and take an ordinary `-vf`.
    """

    extension: str
    #: What `-f` is given.
    muxer: str
    #: The codec arguments, after the scale. Empty for GIF, whose codec is the muxer.
    codec: tuple[str, ...]


#: A GIF holds at most 256 colours and there is no default worth having.
#:
#: Handed a video with no palette to work from, ffmpeg falls back to a fixed web palette and the
#: result is banded and dithered and looks nothing like the source. The fix is the two-stage palette
#: (look at the frames, build a palette for THEM, then map the frames through it) and `split`
#: makes it one command rather than two with a palette file between them.
#:
#: !! **`[0:v]` is not decoration, and without it this fails outright.** A
#: `-filter_complex` builds its own graph and is not fed by `-map`: with the first pad unlabelled
#: ffmpeg answers *"Cannot find a matching stream for unlabeled input pad fps:default"* and exits
#: without writing anything. The graph's output is labelled for the same reason: it is what
#: `-map` names, and `-map 0:v:0` would map the source past the graph entirely.
#:
#: `scale` before `palettegen` rather than after, which is not interchangeable: the palette has to
#: describe the pixels that will actually be written. `stats_mode=full` weighs every frame rather
#: than only the first, which matters for a clip whose colours shift partway through, and
#: `sierra2_4a` is the dither that keeps a gradient without the crosshatch a bayer one leaves.
_GIF_GRAPH = (
    "[0:v]fps={fps},{scale}:flags=lanczos,split[a][b];"
    "[a]palettegen=stats_mode=full[p];[b][p]paletteuse=dither=sierra2_4a[out]"
)

GIF_FORMATS: dict[str, GifFormat] = {
    "gif": GifFormat(extension="gif", muxer="gif", codec=()),
    "webp": GifFormat(
        extension="webp",
        muxer="webp",
        # `picture` tunes libwebp for photographic content, which is what a library holds. This
        # build exposes no `-compression_level`, so the preset is the available lever.
        codec=(
            "-c:v",
            "libwebp",
            "-lossless",
            "0",
            "-q:v",
            str(WEBP_QUALITY),
            "-preset",
            "picture",
        ),
    ),
    "avif": GifFormat(
        extension="avif",
        muxer="avif",
        # `libsvtav1` rather than `libaom-av1`: both encode AV1 and libaom is an order of magnitude
        # slower for no gain anybody would see here. `yuv420p` pins 8-bit, which is what every
        # decoder takes; 10-bit is often smaller and is not worth an unverified compatibility risk.
        codec=(
            "-c:v",
            "libsvtav1",
            "-crf",
            str(AVIF_CRF),
            "-preset",
            str(AVIF_SPEED_PRESET),
            "-pix_fmt",
            "yuv420p",
        ),
    ),
}


def scale_filter(short_edge: int, *, landscape: bool) -> str:
    """Cap the SHORT edge, letting the long edge follow the picture's shape.

    A size for a GIF means the short edge, the way "720p" does. Applying it to the width
    unconditionally only does that for a portrait clip: a 16:9 landscape one comes out capped at 480
    WIDE, which is 480x270: barely a third of the pixels of the portrait version at the same
    number, from the same setting, for no reason anybody could see.

    `-2` rather than `-1` keeps the derived edge even, which every encoder here expects and which
    `-1` does not guarantee.
    """
    return f"scale=-2:{short_edge}" if landscape else f"scale={short_edge}:-2"


def gif_args(
    source: Path,
    destination: Path,
    *,
    start_ms: int,
    duration_ms: int,
    fmt: GifFormat,
    landscape: bool,
    settings: Settings,
) -> list[str]:
    """A stretch of a video, as a GIF.

    Not `cut_args` with a different format. A cut writes the streams it was given (copied or
    re-encoded, but the same picture and the same sound in the same shape) and this rebuilds the
    picture at a different rate and size, and throws the sound away because none of these formats
    can hold any.

    The seek is before the input and the length after it, exactly as a cut's are and for the same
    reason: ffmpeg jumps to the moment and then measures the OUTPUT rather than how much input to
    read. `-ss 0` is left out entirely rather than passed, because on some builds it discards the
    only frame of a single-frame source and exits successfully having written nothing.

    `-loop 0` makes it loop for ever, which is what everybody means by a GIF. Without it it
    plays once and stops on its last frame, which reads as a broken picture.
    """
    scale = scale_filter(GIF_SHORT_EDGE, landscape=landscape)
    picture: list[str] = (
        ["-filter_complex", _GIF_GRAPH.format(fps=GIF_FPS, scale=scale), "-map", "[out]"]
        if not fmt.codec
        else ["-vf", f"fps={GIF_FPS},{scale}:flags=lanczos", *fmt.codec]
    )
    return [
        settings.ffmpeg_path,
        *background_flags(settings),
        *(["-ss", seconds(start_ms)] if start_ms > 0 else []),
        "-i",
        str(source),
        "-t",
        seconds(duration_ms),
        # No sound, and it is not an omission: none of these formats can carry any. Named rather
        # than left to stream selection, so a source with audio builds the same command as one
        # without.
        "-an",
        *picture,
        "-loop",
        "0",
        "-threads",
        str(background_threads(settings)),
        "-f",
        fmt.muxer,
        str(destination),
    ]
