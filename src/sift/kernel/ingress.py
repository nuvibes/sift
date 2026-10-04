# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one gate every file passes through on its way in.

Bytes arrive from five directions (a drop in the browser, a clipboard paste, a downloader's
output, a watched folder, an upload) and none of them is trustworthy. A filename and a
client-supplied content type are both chosen by whoever sent the file, so an `.mp4` can be a
Windows executable, an HTML page, or something that is validly both.

So the file's own leading bytes decide what it is, and nothing else does. Every ingress path
calls `verify_ingress` before the file is hashed, indexed, decoded or served. One function
rather than a check per path: five checks drift, and the one that drifts is the hole.

Verification is in two stages, because they cost very different amounts:

    verify_ingress    reads the first and last few bytes. Microseconds on a local disk.
    verify_decodable  asks ffprobe to actually parse it. Milliseconds, and a subprocess.

Microseconds on a local disk, and not always: a library root is very often a share on another
machine, and there `read_ends` is an open, a stat, a read, a seek to the end and a second read:
five or six network round trips, a tenth of a second or more against an SMB library during an
import, against a small fraction of a millisecond on the local disk. Cost is a property of the
STORAGE, not of the function, so no caller may run it on the event loop.

A file that passes the first can still be a malformed container that crashes a decoder, so the
second runs before anything decodes it for real. Both live here so the order is not something
each caller has to remember.

Nothing here ever executes an ingested file. Sift decodes them as media; there is no code path
that runs one, and none may be added.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import shutil
import stat
import time
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import filetype

from sift.kernel.config import Settings
from sift.kernel.log import get_logger, hashed, security_event
from sift.kernel.numbers import as_int
from sift.kernel.paths import O_NONBLOCK
from sift.kernel.subprocess import SubprocessError
from sift.kernel.subprocess import run as run_tool
from sift.kernel.threads import waits_on_storage

log = get_logger(__name__)


class Kind(StrEnum):
    """What a file is, as far as Sift is concerned."""

    VIDEO = "video"
    IMAGE = "image"
    GIF = "gif"


@dataclass(frozen=True, slots=True)
class MediaType:
    """One accepted format.

    `family` is what the extension is checked against, not `name`. MP4, MOV and M4V are the same
    container with different brands stamped in the header, and a perfectly ordinary `.mp4` off a
    phone is often detected as MOV. Comparing names would reject it; comparing families does not.
    """

    name: str
    kind: Kind
    family: str
    extensions: frozenset[str]
    mime: str


# The allowlist. The only place in Sift that says which media types exist: the scanner, the
# importer and the downloader all import this rather than keeping a list of their own. Two lists
# is one list that is wrong.
ALLOWED_MEDIA: tuple[MediaType, ...] = (
    MediaType("mp4", Kind.VIDEO, "isobmff-video", frozenset({".mp4", ".m4v"}), "video/mp4"),
    MediaType("mov", Kind.VIDEO, "isobmff-video", frozenset({".mov"}), "video/quicktime"),
    MediaType("mkv", Kind.VIDEO, "matroska", frozenset({".mkv"}), "video/x-matroska"),
    MediaType("webm", Kind.VIDEO, "matroska", frozenset({".webm"}), "video/webm"),
    MediaType("jpeg", Kind.IMAGE, "jpeg", frozenset({".jpg", ".jpeg"}), "image/jpeg"),
    MediaType("png", Kind.IMAGE, "png", frozenset({".png"}), "image/png"),
    MediaType("webp", Kind.IMAGE, "webp", frozenset({".webp"}), "image/webp"),
    # The same container holding a sequence of frames rather than one picture. Its own entry
    # because it is read by different tools: ffmpeg cannot read these at all. See
    # `sift.kernel.webp`, which is what does.
    MediaType("webp-animated", Kind.GIF, "webp", frozenset({".webp"}), "image/webp"),
    MediaType("heic", Kind.IMAGE, "heif", frozenset({".heic", ".heif"}), "image/heic"),
    MediaType("avif", Kind.IMAGE, "heif", frozenset({".avif"}), "image/avif"),
    # The same containers holding a SEQUENCE of frames rather than one picture: the AVIF and HEIF
    # equivalent of an animated WebP, and they arrive from exactly the same places: a phone's motion
    # still, and every social site that re-encodes a short clip for a feed.
    #
    # `Kind.GIF` rather than a kind of their own, because they are the same thing to everybody who
    # reads them: it moves, it loops, it has a length. Sift's word for that is a GIF (the tile
    # says so, the hover preview is built for it, the still viewer draws it) and a fourth kind
    # would mean teaching all of them a second name for one idea.
    #
    # Two entries rather than one covering both extensions, because the only thing separating them
    # is the MIME type and that is what a browser is handed to decide whether it can draw the file
    # at all. An animated HEIC served as `image/avif` is a picture Safari refuses and Chrome sniffs
    # its way past, which is the worst kind of wrong: it works on the machine it was tested on.
    MediaType("avif-sequence", Kind.GIF, "heif", frozenset({".avif"}), "image/avif"),
    MediaType("heic-sequence", Kind.GIF, "heif", frozenset({".heic", ".heif"}), "image/heic"),
    MediaType("gif", Kind.GIF, "gif", frozenset({".gif"}), "image/gif"),
)

#: WHICH GENERATION OF `classify` A ROW'S TYPE WAS DECIDED BY, stored on the row as
#: `assets.classified_version`.
#:
#: A change to what `classify` answers for bytes it already accepted (a brand moved between two
#: lists, a flag read that was not) reaches new files and never the ones already in the library:
#: a scan skips any file whose path, size and mtime are unchanged. So the change raises this number
#: and the rows below it are read again, four kilobytes of header each, by the reclassify task
#: (`media_jobs.reclassify`). That task is the one pass for every such change, rather than a pass
#: per fault.
#:
#: !! RAISE IT ONLY WITH A SCHEMA STEP THAT SAYS WHICH ROWS THE CHANGE CAN REACH. The step stamps
#: every other row with the new number, so the pass reads the rows the change could type
#: differently and nothing else: a library is hundreds of thousands of files on a share, and a
#: re-read of all of them is not something that may start on its own. Version 1 is the classifier
#: that checks the sequence brands before the still-image ones and reads WebP's animation flag; the
#: step that introduced the column left the HEIF and WebP rows below it and stamped the rest.
#:
#: Version 2 answers exactly what version 1 does. It is the same question asked again of every
#: WebP, AVIF and HEIF row, because some of them never were: a WebP filed as a VIDEO by the gate
#: before the column existed carried version 1 from the column's default, so the pass never read it,
#: and it stayed a video that no player can open, with no GIF mark on its tile. Content step 28
#: leaves those rows below the line and stamps the rest.
CLASSIFIER_VERSION = 2

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    extension for media in ALLOWED_MEDIA for extension in media.extensions
)

_BY_NAME: dict[str, MediaType] = {media.name: media for media in ALLOWED_MEDIA}

#: Every extension a family's members are named with, for saying when a name and the bytes differ.
_FAMILY_EXTENSIONS: dict[str, frozenset[str]] = {
    family: frozenset(
        extension
        for media in ALLOWED_MEDIA
        if media.family == family
        for extension in media.extensions
    )
    for family in {media.family for media in ALLOWED_MEDIA}
}


class Origin(StrEnum):
    """Where the bytes came from, which decides whether Sift may move the file.

    The distinction is ownership, not trust: every origin is verified identically. Sift wrote
    the file for a download, an upload, a drop or a paste, so setting a rejected one aside is
    tidying up after itself. A file found during a library scan belongs to the user and Sift only
    ever reads their library; relocating something they put there themselves, because Sift did
    not like it, is not a safety measure. It is refused and left exactly where it is.

    A watched folder is the user's folder too, however much it looks like an inbox.

    A swap is the seventh direction: another install's file, received in pieces, each piece
    checked, reassembled in Sift's own workspace and stripped of its metadata there before it gets
    here (`slices/swap/ingest.py`). Nobody trusts the sender (which is exactly why it comes
    through this gate like a download does), and the file this gate sees is one Sift wrote, so a
    refused one is Sift's to set aside.
    """

    SCAN = "scan"
    WATCH = "watch"
    DOWNLOAD = "download"
    UPLOAD = "upload"
    DROP = "drop"
    PASTE = "paste"
    SWAP = "swap"

    @property
    def sift_wrote_it(self) -> bool:
        return self in {Origin.DOWNLOAD, Origin.UPLOAD, Origin.DROP, Origin.PASTE, Origin.SWAP}


class Reason(StrEnum):
    """Why a file was refused. Safe to log: it describes the decision, not the file."""

    EMPTY = "empty"
    UNREADABLE = "unreadable"
    SIGNATURE_NOT_ALLOWED = "signature_not_allowed"
    # Nothing raises this. See `verify_ingress`. It stays because older records carry the
    # string, and a reader that no longer knows the word would draw an old refusal as unexplained.
    EXTENSION_CONTRADICTS_SIGNATURE = "extension_contradicts_signature"

    # Named for what was actually observed, not for what was assumed about it. The formats that
    # define a terminator must end at one; a file that does not is either carrying an appended
    # payload or was cut short, and from the last few bytes there is no way to tell which. Calling
    # it "trailing data" would be a guess, and a truncated download is by far the commoner cause.
    DOES_NOT_END_WHERE_IT_SHOULD = "does_not_end_where_it_should"

    NOT_DECODABLE = "not_decodable"
    #: The decoder did not answer in time. A share that stalled, not a file that is broken:
    #: the same file answers the next time the share does, so this is never a standing verdict.
    TIMED_OUT = "timed_out"

    # An animated WebP that could not be read. These are accepted (`sift.kernel.webp` reads them
    # with libwebp's own tools, because ffmpeg never could), so this is not a refusal of the format
    # but of a file or an installation: a truncated GIF, or an image without those tools.
    ANIMATED_WEBP_UNREADABLE = "animated_webp_unreadable"
    NO_VIDEO_STREAM = "no_video_stream"
    PIXELS_EXCEEDED = "pixels_exceeded"


@dataclass(frozen=True, slots=True)
class IngressResult:
    """Proof that a file was verified, and what it turned out to be.

    Only `verify_ingress` constructs one. Anything downstream that touches an ingested file
    (hashing, indexing, decoding) takes this rather than a bare path, so "did anyone check this
    file?" is answered by the type of the argument instead of by remembering to call something.
    """

    path: Path
    media: MediaType
    size: int
    origin: Origin


class NoDestination(LookupError):
    """There is nowhere to put the file. The message is written to be shown to a person.

    Not a judgement on the file: the bytes may be perfectly good and already fetched. It is the
    *place* that is missing: a folder deleted since it was chosen, no default download folder set,
    or a library whose disk is not there at all.

    It lives in the kernel rather than with the pipeline that raises it because two slices need to
    name it and neither may import the other: capture raises it on the way in, and the downloader
    has to turn it into the sentence a person reads in the ledger. Left in the pipeline, the
    downloader could only catch it as a bare exception, and an unplugged disk would be recorded as
    an `OSError` about a read-only file system.
    """


#: The refusals that are about a moment rather than about the bytes: a file another program holds
#: open, a share that has gone away, a download not yet finished. A scan does not remember one
#: against the file while it can still be a moment (an empty file stops being one once it has stayed
#: empty; see the scan's `EMPTY_SETTLED_SECONDS`), and a verdict carrying one is cleared by the next
#: scan that sees it.
TRANSIENT_REASONS: frozenset[Reason] = frozenset(
    {Reason.UNREADABLE, Reason.EMPTY, Reason.TIMED_OUT}
)


class IngressRejected(Exception):
    """A file was refused, and is not to be hashed, indexed, decoded or served."""

    def __init__(
        self,
        reason: Reason,
        *,
        detected: str | None = None,
        quarantined_to: Path | None = None,
    ) -> None:
        super().__init__(f"rejected: {reason}")
        self.reason = reason
        self.detected = detected
        self.quarantined_to = quarantined_to


# --- Signatures ---------------------------------------------------------------------------
#
# Written out here rather than delegated to a signature library, because the two things this has
# to get exactly right are the two things a general-purpose library gets approximately right:
#
#   - Too strict is a bug that eats your library. A real MP4 whose header brand is `iso5` (what
#     fragmented and HLS-derived files carry) is not recognized by the obvious brand allowlists,
#     and quarantining someone's actual videos is a far worse failure than any file this gate is
#     meant to stop.
#   - Too lenient is a hole. A GIF header with a payload glued on the end is still "a GIF" to a
#     header-only matcher.
#
# So: the container is identified structurally, and a header alone is never enough.

_HEAD_BYTES = 4096
_TAIL_BYTES = 32

# ffprobe is given a bounded amount of time. A malformed file is one of the few things that can
# make a decoder spin, and an ingest path that waits forever is a denial of service against every
# other file in the queue.
_PROBE_TIMEOUT_SECONDS = 30

# The most pixels a single frame may declare before it is refused. A decompression bomb is a few
# kilobytes on disk that name tens of thousands of pixels on each side; it passes the signature
# check and decodes, then makes the decoder allocate gigabytes to render one frame: an out-of-
# memory kill on the modest hardware Sift targets, from a single dropped or downloaded file. The
# ceiling is read from the same probe that already runs, and sits far above real media: 8K UHD is
# 33 megapixels, so 200 leaves room for high-resolution stills while a bomb declares billions.
_MAX_DECODE_PIXELS = 200_000_000

_EBML = b"\x1a\x45\xdf\xa3"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_END = b"IEND\xaeB`\x82"
_GIF_END = b"\x3b"

#: The chunk a WebP file carries when it is anything more than a single still picture, and the bit
#: inside it that says the extra thing is a sequence of frames.
#:
#: A WebP is a RIFF container: "RIFF", four bytes of length, "WEBP", then chunks. An ordinary still
#: has "VP8 " or "VP8L" first. A GIF in WebP has "VP8X" (the extended header) whose first payload
#: byte is a set of flags, and 0x02 is the one that means the file holds a sequence of frames rather
#: than a picture.
#:
#: Read from the header rather than asked of a decoder, because the decoder is exactly what cannot
#: read these: handed one, ffmpeg reports "image data not found" and returns a stream of width zero.
_WEBP_EXTENDED = b"VP8X"
_WEBP_ANIMATION_FLAG = 0x02
_WEBP_FLAGS_AT = 20


def _webp_is_animated(head: bytes) -> bool:
    """Whether a WebP holds a sequence of frames rather than one picture.

    Discord and the image hosts serve these where they used to serve GIFs, so they arrive often,
    and they arrive with a `.webp` extension and a still-image signature, which is why nothing
    upstream of here can tell them apart.
    """
    if head[12:16] != _WEBP_EXTENDED or len(head) <= _WEBP_FLAGS_AT:
        return False
    return bool(head[_WEBP_FLAGS_AT] & _WEBP_ANIMATION_FLAG)


# Brands that mean the ISO base-media container holds a still image rather than video. Everything
# else in that container is treated as video and left to ffprobe to confirm. See above: an
# allowlist of video brands is the thing that rejects real files.
_HEIF_BRANDS = frozenset({b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1"})

# The AV1 flavour of the same container. `avif` is one picture; `avis` is a sequence of them.
_AVIF_BRANDS = frozenset({b"avif", b"avis"})

# Brands that mean the still-image container holds a SEQUENCE rather than one picture.
#
# `msf1` is MIAF's image-sequence brand and `avis` is AVIF's. Matched as a HEIF brand, an animated
# AVIF would be filed as a HEIC photograph (no GIF chip, no hover preview and no length), while
# decoding and displaying perfectly, so it would never look like a bug.
#
# Checked BEFORE `_HEIF_BRANDS`, and the order is the point. A real animated AVIF carries both:
# e.g. `avis avif msf1 iso8 mif1 miaf MA1B`, so whichever set is asked first wins, and only one of
# the two answers is right.
_SEQUENCE_BRANDS = frozenset({b"msf1", b"avis"})

# Audio-only. The container is identical to MP4's, so nothing structural distinguishes them; the
# brand is all there is. Without this an `.m4a` would be indexed as a video with no picture.
_AUDIO_BRANDS = frozenset({b"M4A ", b"M4B ", b"M4P ", b"F4A ", b"F4B "})


def _iso_brands(head: bytes) -> list[bytes] | None:
    """The brands in an ISO base-media header, or None if this is not one.

    Layout: a 4-byte box length, the literal `ftyp`, a 4-byte major brand, a 4-byte minor version,
    then the compatible brands to the end of the box.
    """
    if len(head) < 12 or head[4:8] != b"ftyp":
        return None

    box_size = int.from_bytes(head[0:4], "big")
    # A box smaller than its own header is malformed; one larger than what was read is truncated
    # to it. Either way, read no further than the bytes actually in hand.
    end = min(box_size, len(head)) if box_size >= 16 else len(head)

    brands = [head[8:12]]
    brands.extend(head[offset : offset + 4] for offset in range(16, end - 3, 4))
    return brands


def _matroska_doctype(head: bytes) -> bytes | None:
    """The DocType string of an EBML file (`matroska` or `webm`), or None.

    Found by walking to the DocType element id rather than by searching for the words anywhere in
    the header, so a file that merely happens to contain the text "webm" is not mistaken for one.
    """
    if not head.startswith(_EBML):
        return None

    marker = head.find(b"\x42\x82", 0, 1024)
    if marker == -1 or marker + 3 > len(head):
        return None

    # A one-byte EBML length: the high bit marks the width, the low seven carry the value.
    length_byte = head[marker + 2]
    if not length_byte & 0x80:
        return None
    length = length_byte & 0x7F

    value = head[marker + 3 : marker + 3 + length]
    return value.rstrip(b"\x00") or None


def classify(head: bytes, tail: bytes) -> MediaType | Reason:
    """What the bytes are, or the reason they are refused. Never both, never neither.

    Returning the reason from here, rather than inferring it afterwards, is deliberate: the code
    that knows *why* it said no is the only code in a position to say so. Asking the signature
    library what a refused file looks like and guessing the reason from that would hand a security
    field to a third party, and get it wrong: a PNG signature that is right in its first four bytes
    and wrong in its next four would be reported as a payload appended to a valid image, when it is
    nothing of the sort.

    `tail` is checked as well as `head` for the formats that define exactly where they end. A file
    with a payload welded on after its terminator decodes perfectly and carries something else
    through: the header says GIF because it genuinely is a GIF.

    JPEG is deliberately exempt from that check. Trailing data after its end marker is normal:
    phones append a whole video to the still to make a motion photo, and rejecting those would
    quarantine an ordinary camera roll to defend against a file Sift never executes anyway.
    """
    if head[:3] == b"\xff\xd8\xff":
        return _BY_NAME["jpeg"]

    if head.startswith(_PNG_SIGNATURE):
        return _BY_NAME["png"] if tail.endswith(_PNG_END) else Reason.DOES_NOT_END_WHERE_IT_SHOULD

    if head[:6] in (b"GIF87a", b"GIF89a"):
        return _BY_NAME["gif"] if tail.endswith(_GIF_END) else Reason.DOES_NOT_END_WHERE_IT_SHOULD

    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return _BY_NAME["webp-animated" if _webp_is_animated(head) else "webp"]

    doctype = _matroska_doctype(head)
    if doctype == b"matroska":
        return _BY_NAME["mkv"]
    if doctype == b"webm":
        return _BY_NAME["webm"]

    brands = _iso_brands(head)
    if brands is not None:
        if any(brand in _AUDIO_BRANDS for brand in brands):
            return Reason.NO_VIDEO_STREAM
        av1 = any(brand in _AVIF_BRANDS for brand in brands)
        # A sequence FIRST, because a file that is one carries the still-image brands as well and
        # would otherwise be filed as a photograph. See `_SEQUENCE_BRANDS`.
        if any(brand in _SEQUENCE_BRANDS for brand in brands):
            return _BY_NAME["avif-sequence" if av1 else "heic-sequence"]
        if av1:
            return _BY_NAME["avif"]
        if any(brand in _HEIF_BRANDS for brand in brands):
            return _BY_NAME["heic"]
        if brands[0] == b"qt  ":
            return _BY_NAME["mov"]
        # Any other ISO base-media file. Not an allowlist of brands, on purpose: real MP4s carry
        # brands nobody has heard of, and ffprobe is a better judge of whether this decodes than
        # a list is of whether the brand is famous.
        return _BY_NAME["mp4"]

    return Reason.SIGNATURE_NOT_ALLOWED


def detect(head: bytes, tail: bytes) -> MediaType | None:
    """`classify`, for callers that only want to know whether it is media."""
    outcome = classify(head, tail)
    return outcome if isinstance(outcome, MediaType) else None


def describe(head: bytes) -> str:
    """What a refused file appears to be, for the operator's benefit.

    "It was a Windows executable" is worth knowing and worth acting on; "not allowed" is not.

    This reaches a log line and nothing else. It decides nothing: not whether the file is
    accepted, and not why it was refused. `classify` answers both, and does not consult this.
    """
    if _looks_like_a_web_page(head):
        return _WEB_PAGE
    guess = filetype.guess(bytearray(head))
    return str(guess.mime) if guess is not None else "unrecognized"


#: What `describe` calls a web page. The signature library has no matcher for markup, so without
#: this a site's error page saved under a video's name would be "unrecognized", and the screen
#: listing refused files could only say the bytes were not media when it could say what they were.
_WEB_PAGE = "text/html"

#: How a web page starts, lowercased, once any byte order mark and leading whitespace are gone.
_WEB_PAGE_OPENINGS = (b"<!doctype html", b"<html", b"<head", b"<body")


def _looks_like_a_web_page(head: bytes) -> bool:
    """Whether the leading bytes are markup a browser would draw as a page. A label, never a verdict."""
    opening = head[:256].removeprefix(b"\xef\xbb\xbf").lstrip().lower()
    return opening.startswith(_WEB_PAGE_OPENINGS)


# --- The gate -----------------------------------------------------------------------------


@waits_on_storage
def read_ends(path: Path) -> tuple[bytes, bytes, int]:
    """The leading and trailing bytes, and the size. Never the whole file.

    Public because `classify` is: anything asking what a file IS has to read it the same way the
    gate does, and the guard below is the reason: a second reader written somewhere else would be
    a second place for the named-pipe hazard to be forgotten.

    **Blocking, so call it off the loop, and the cost depends on where the file is.** A small
    fraction of a millisecond on a local disk, a tenth of a second or more on an SMB share. The
    seek to the end is the expensive half: it is a second round trip that no read-ahead can serve.

    Opened non-blocking, and its regular-file-ness is checked on the open descriptor rather than
    on the path. A library is a directory someone else assembled, and a named pipe left in it
    (by accident in a copied tree, or on purpose by whoever can write to the share) would, opened
    the ordinary blocking way, hang this read until somebody wrote to the pipe. Nobody will, so the
    worker never returns. O_NONBLOCK makes the open of a pipe return instead of wait; the S_ISREG
    check then rejects it before a byte is read. Checking the descriptor, not the path, means a
    swap between the check and the open cannot slip a special file through. The hashing path guards
    the same way, for the same reason: this is the earlier copy of that guard, at the gate.
    """
    fd = os.open(path, os.O_RDONLY | O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError(f"{path.name} is not a regular file")
    with os.fdopen(fd, "rb") as handle:
        head = handle.read(_HEAD_BYTES)
        size = handle.seek(0, 2)
        handle.seek(max(0, size - _TAIL_BYTES))
        tail = handle.read(_TAIL_BYTES)
    return head, tail, size


_UNSAFE_IN_NAME = re.compile(r"[^A-Za-z0-9._-]")


#: What the note beside a quarantined file is called. The file's own name plus this, so the two
#: sort together in a directory listing and neither can be mistaken for media.
NOTE_SUFFIX = ".why.json"


def _write_note(destination: Path, note: dict[str, object], handle: str) -> None:
    """Say why this file is here, beside the file itself.

    ## Why a note on disk rather than a row in a table

    Without it the reason would live only in the security log, so a screen listing the quarantine
    directory could say what and when and how big, and not *why*: the one thing somebody opening
    it wants to know.

    A table was the obvious answer and is the wrong one here, for two reasons. This runs on a
    thread with no database in reach and no way to get one without a queue and a drain, which is a
    lot of machinery for an occasional admin screen. And more importantly a table would be a second
    record of the same directory: a file removed by hand leaves a row behind, a restored backup
    brings back rows for files that are gone, and the screen then describes a quarantine nobody
    has. The note travels with the file, is written in the one place that puts it there, and is as
    correct as the directory is.

    A failure to write it is swallowed for the same reason a failure to move is: the file is
    refused either way, and the bookkeeping must never be able to change that answer.
    """
    try:
        destination.with_name(destination.name + NOTE_SUFFIX).write_text(
            json.dumps(note, indent=1), encoding="utf-8"
        )
    except OSError as exc:
        log.error("ingress.quarantine_note_failed", handle=handle, error=str(exc))


def _quarantine(
    path: Path,
    settings: Settings,
    handle: str,
    *,
    note: dict[str, object],
) -> Path | None:
    """Move a refused file out of the way. Returns where it went, or None if it could not.

    The name is rebuilt rather than reused: a filename arrives from the same untrusted place the
    bytes did, and it lands in a directory an operator will later poke around in.

    A failure to move is logged and swallowed. The file is refused either way (the caller
    already has the exception) and a quarantine directory that cannot be written to must not be
    able to turn a rejection into an acceptance.

    The note is required rather than optional: every caller has one, and a file that arrives here
    without a reason beside it is the thing the quarantine screen exists to stop.
    """
    directory = settings.quarantine_dir
    safe_name = _UNSAFE_IN_NAME.sub("_", path.name)[:64].lstrip(".") or "file"
    destination = directory / f"{handle}-{safe_name}"

    try:
        directory.mkdir(parents=True, exist_ok=True)

        # The handle is derived from the path, so two different files downloaded to the same
        # temporary name collide here. Overwriting would throw away the earlier sample, which is
        # evidence, sitting in the one directory an admin looks in to find out what happened.
        attempt = 1
        while destination.exists():
            destination = directory / f"{handle}-{attempt}-{safe_name}"
            attempt += 1

        shutil.move(str(path), str(destination))
    except OSError as exc:
        log.error("ingress.quarantine_failed", handle=handle, error=str(exc))
        return None
    _write_note(destination, note, handle)
    return destination


@waits_on_storage
def verify_ingress(path: Path, *, origin: Origin, settings: Settings) -> IngressResult:
    """Decide whether a file may enter Sift. Raises `IngressRejected` if not.

    Runs the instant the bytes are in hand and before anything else touches them: after a
    download finishes, after a drop or a paste, after a watched file has stopped growing, after
    an upload. Nothing hashes, indexes, decodes or serves a file that has not been through here.

    A file is refused when its signature is not an accepted media type, or when it carries data
    past the point its format ends. **The extension is never consulted: it is a claim, not
    evidence, and nothing here lets a claim overrule what the bytes say.**

    An extension check could not do what it looks like it does. By the time it would run, `classify`
    has already proved the file is one of the media containers Sift accepts, so it would never
    fire on anything hostile, only on a real image or video wearing the wrong media extension. A
    `.webp` that is really a JPEG is what sites serve every day, and quarantining one costs somebody
    a file they wanted in exchange for no safety at all. What stops a disguised executable is
    `SIGNATURE_NOT_ALLOWED`, one step earlier; what stops a payload welded onto a real image is
    `DOES_NOT_END_WHERE_IT_SHOULD`; and neither is affected.

    If Sift wrote the file, a refused one is moved to the quarantine directory. If it was found
    in the user's own library it is left alone; Sift reads their files, it does not move them.
    """
    handle = hashed(str(path))

    try:
        head, tail, size = read_ends(path)
    except OSError:
        raise _reject(Reason.UNREADABLE, path, origin, settings, handle, None) from None

    if size == 0:
        raise _reject(Reason.EMPTY, path, origin, settings, handle, None)

    outcome = classify(head, tail)

    if isinstance(outcome, Reason):
        # `describe` only labels what it was. The reason came from the code that refused it.
        raise _reject(outcome, path, origin, settings, handle, describe(head))

    media = outcome
    # The name is NOT checked against the signature, and that is deliberate. See the note on the
    # extension in this function's docstring. It is SAID, because a picture named for another kind
    # (PNG or WebP bytes under `.jpg`, what image hosts serve every day) is taken by its bytes, and
    # somebody reading the log about that file should learn why its type is not the one its name
    # gives. A file with no extension claims nothing, so it disagrees with nothing, and the family
    # is what is compared (see `MediaType`): a phone's `.mp4` read as MOV is not news.
    suffix = path.suffix.lower()
    if suffix and suffix not in _FAMILY_EXTENSIONS[media.family]:
        log.info(
            "ingress.name_disagrees",
            handle=handle,
            file_type=media.name,
            named=suffix,
        )
    log.debug("ingress.accepted", handle=handle, file_type=media.name, file_size=size)
    return IngressResult(path=path, media=media, size=size, origin=origin)


def _reject(
    reason: Reason,
    path: Path,
    origin: Origin,
    settings: Settings,
    handle: str,
    detected: str | None,
) -> IngressRejected:
    """Quarantine if the file is Sift's to move, record it, and build the exception.

    The record says what was refused, why, and which file, because "cute_puppy.mp4 was really a
    Windows executable" is the whole of what an admin needs, and a correlation id on its own tells
    them nothing. There is no separate scrubbing here: the logger already removes the account name
    from a path and keeps the rest, which is the policy everywhere else in Sift and would only
    drift if this module kept its own version of it.

    The handle stays as well. It is stable for a given path, so repeated attempts on the same file
    can be counted without reading filenames.
    """
    # The size is read before the move, because after it the path names nothing. Missing rather
    # than zero where it cannot be read: zero is a real size an empty file has, and this screen's
    # whole job is telling somebody what a file was.
    try:
        size = path.stat().st_size
    except OSError:
        size = None

    destination = (
        _quarantine(
            path,
            settings,
            handle,
            note={
                "reason": str(reason),
                "detected": detected,
                "origin": str(origin),
                "original_name": path.name,
                "size_bytes": size,
                "quarantined_at": int(time.time()),
            },
        )
        if origin.sift_wrote_it
        else None
    )

    security_event(
        "ingress_rejected",
        reason=str(reason),
        detected=detected or "unrecognized",
        origin=str(origin),
        path=str(path),
        handle=handle,
        quarantined=destination is not None,
    )
    return IngressRejected(reason, detected=detected, quarantined_to=destination)


# --- The decoder check --------------------------------------------------------------------


async def verify_decodable(result: IngressResult, *, settings: Settings) -> None:
    """Confirm a file really decodes, before anything tries to decode it for real.

    The signature check proves the container is what it claims to be. It cannot prove the
    container is intact: a truncated or hand-corrupted file has a perfectly good header and
    still hits a decoder as malformed input, which is the one place an ingested file gets to
    influence a program that was not expecting it.

    Takes an `IngressResult` rather than a path, so this cannot run on a file that skipped the
    first stage.

    Every accepted type has a picture in it. A container with no video stream is either audio
    wearing a video extension or an empty shell, and neither is something to index.

    One format is checked by a different tool for the plainest of reasons: ffprobe cannot read an
    animated WebP, so asking it would refuse every one of them. See `sift.kernel.webp`.
    """
    if result.media.name == "webp-animated":
        await _verify_animated_webp(result, settings=settings)
        return

    # Absolute, always. ffprobe has no `--` to end its options, so a file called `-i` or
    # `-f` is read as a flag rather than as a filename, and the filename came from a remote
    # site or an upload form. An absolute path begins with a separator and cannot be mistaken
    # for one. (There is no shell here either: the arguments are passed as a list.)
    target = await asyncio.to_thread(result.path.resolve)

    argv = [
        settings.ffprobe_path,
        "-v",
        "error",
        "-select_streams",
        "v",
        "-show_entries",
        "stream=codec_type,width,height",
        "-of",
        "csv=p=0",
        str(target),
    ]

    handle = hashed(str(result.path))

    try:
        probe = await run_tool(argv, time_limit=_PROBE_TIMEOUT_SECONDS)
    except SubprocessError:
        # Not decodable and not answered are different facts. A probe that ran out of time on a
        # share that stalled must not be written down as a broken file (a standing verdict that
        # no later pass would revisit) when the file is fine the moment the share is.
        raise await _reject_decoded(Reason.TIMED_OUT, result, settings, handle, "timeout") from None

    if probe.returncode != 0:
        detail = probe.stderr.decode("utf-8", "replace").strip()
        raise await _reject_decoded(Reason.NOT_DECODABLE, result, settings, handle, detail)

    if b"video" not in probe.stdout:
        raise await _reject_decoded(Reason.NO_VIDEO_STREAM, result, settings, handle, None)

    if not _within_pixel_cap(probe.stdout):
        raise await _reject_decoded(Reason.PIXELS_EXCEEDED, result, settings, handle, None)

    log.debug("ingress.decodable", handle=handle, file_type=result.media.name)


def _within_pixel_cap(stdout: bytes) -> bool:
    """Whether every video stream sits within the pixel ceiling.

    Reads the `width,height` the probe already returned (one `codec_type,width,height` line per
    video stream) and multiplies them, so the check does not depend on which field comes first. A
    stream over the ceiling is a decode bomb and the file is refused before anything renders it. A
    stream that reports no dimensions (an unusual codec) is left to the ffmpeg allocation cap.
    """
    for line in stdout.decode("utf-8", "replace").splitlines():
        # Parsed rather than character-tested. This is the gate that refuses a decode bomb, so it is
        # the last place that should be able to raise on its own input: `isdigit()` keeps characters
        # `int()` then rejects, and the exception would abandon the check part-way through a file it
        # had not yet cleared.
        dims = [
            value for value in (as_int(field) for field in line.split(",")) if value is not None
        ]
        if len(dims) >= 2 and dims[0] * dims[1] > _MAX_DECODE_PIXELS:
            return False
    return True


async def _verify_animated_webp(result: IngressResult, *, settings: Settings) -> None:
    """The same two questions, asked of the tool that can answer them.

    Imported here rather than at the top: the WebP module reads the content store to keep its
    readable copies, and the content store is built on this gate.
    """
    from sift.kernel import webp

    handle = hashed(str(result.path))
    try:
        animation = await webp.inspect(result.path, settings=settings)
    except webp.WebpError as error:
        raise await _reject_decoded(
            Reason.ANIMATED_WEBP_UNREADABLE, result, settings, handle, str(error)
        ) from None

    if animation.width * animation.height > _MAX_DECODE_PIXELS:
        raise await _reject_decoded(Reason.PIXELS_EXCEEDED, result, settings, handle, None)

    log.debug("ingress.decodable", handle=handle, file_type=result.media.name)


async def _reject_decoded(
    reason: Reason,
    result: IngressResult,
    settings: Settings,
    handle: str,
    detail: str | None,
) -> IngressRejected:
    """Build the refusal, on a thread. Refusing a file Sift wrote moves it into quarantine, so
    this is a file move rather than the bookkeeping it reads as, and every caller is on the
    import path, where the loop is also serving whatever anybody is watching."""
    return await asyncio.to_thread(
        _reject, reason, result.path, result.origin, settings, handle, detail
    )


# --- Keeping the gate the only way in -----------------------------------------------------


def unguarded_ingress(source_root: Path) -> Iterator[str]:
    """Yield a complaint for every place a file could get in without being verified.

    A gate is only a gate while it is the only way through. Features are written by different
    people at different times, and the natural mistake is not to remove this check: it is to
    add a sixth ingress path that never knew it existed. So the rule is enforced against the
    source rather than trusted to reviewers:

      - a feature that hashes or indexes a file must import `verify_ingress`
      - only this module may construct an `IngressResult`, or the proof means nothing
      - nobody keeps their own list of media types; there is one allowlist and it is here

    Run as a test, not a lint, so it fails where it will be read.
    """
    ingest_calls = {
        "ingest",
        "hash_file",
        "identity_file",
        "content_hash",
        "index_asset",
        # Recording an asset or a place one sits is indexing it, whoever computed the digest.
        # Without these, a feature could hash a file its own way and write the row itself,
        # which is the same hole with two more steps in it.
        "upsert_asset",
        "add_location",
    }

    for module in sorted(source_root.rglob("*.py")):
        if "tests" in module.parts:
            continue

        text = module.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(module))
        where = module.relative_to(source_root.parent)

        imports_gate = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "sift.kernel.ingress"
            and any(alias.name == "verify_ingress" for alias in node.names)
            for node in ast.walk(tree)
        )

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _called_name(node)

            if name == "IngressResult":
                yield f"{where}: constructs IngressResult; only the ingress gate may mint one"
            elif name in ingest_calls and not imports_gate:
                yield f"{where}: calls {name}() without importing verify_ingress"

        for extensions in _extension_literals(tree):
            if len(extensions & ALLOWED_EXTENSIONS) >= 3:
                yield f"{where}: has its own media extension list; import ALLOWED_MEDIA instead"


def _called_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _extension_literals(tree: ast.AST) -> Iterator[set[str]]:
    """Every collection of string constants in a module.

    A dict counts, and its keys are what get looked at: `{".mp4": h264, ".mkv": h264}` is a second
    copy of the media allowlist just as surely as a set of the same strings is, and checking only
    sets and lists would leave an open door with a mat in front of it.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Set | ast.List | ast.Tuple):
            elements: list[ast.expr] = list(node.elts)
        elif isinstance(node, ast.Dict):
            elements = [key for key in node.keys if key is not None]
        else:
            continue

        values = {
            element.value.lower()
            for element in elements
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        }
        if values:
            yield values
