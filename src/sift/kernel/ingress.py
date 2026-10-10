# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one gate every file passes through on its way in: the leading bytes decide what it is.

`verify_ingress` reads the ends of the file (blocking, slow on a share: never on the loop), and
`verify_decodable` asks ffprobe to parse it. Nothing here ever executes an ingested file.
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
from typing import Any

import filetype

from sift.kernel.config import Settings
from sift.kernel.log import get_logger, hashed, security_event
from sift.kernel.numbers import as_int
from sift.kernel.paths import O_NONBLOCK
from sift.kernel.subprocess import Priority, SubprocessError
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
    """One accepted format; `family` is what an extension is matched to: an `.mp4` is often MOV."""

    name: str
    kind: Kind
    family: str
    extensions: frozenset[str]
    mime: str


# The one allowlist of media types in Sift.
ALLOWED_MEDIA: tuple[MediaType, ...] = (
    MediaType("mp4", Kind.VIDEO, "isobmff-video", frozenset({".mp4", ".m4v"}), "video/mp4"),
    MediaType("mov", Kind.VIDEO, "isobmff-video", frozenset({".mov"}), "video/quicktime"),
    MediaType("mkv", Kind.VIDEO, "matroska", frozenset({".mkv"}), "video/x-matroska"),
    MediaType("webm", Kind.VIDEO, "matroska", frozenset({".webm"}), "video/webm"),
    MediaType("jpeg", Kind.IMAGE, "jpeg", frozenset({".jpg", ".jpeg"}), "image/jpeg"),
    MediaType("png", Kind.IMAGE, "png", frozenset({".png"}), "image/png"),
    MediaType("webp", Kind.IMAGE, "webp", frozenset({".webp"}), "image/webp"),
    # Its own entry: ffmpeg cannot read these, `sift.kernel.webp` does.
    MediaType("webp-animated", Kind.GIF, "webp", frozenset({".webp"}), "image/webp"),
    MediaType("heic", Kind.IMAGE, "heif", frozenset({".heic", ".heif"}), "image/heic"),
    MediaType("avif", Kind.IMAGE, "heif", frozenset({".avif"}), "image/avif"),
    # Sequences are a GIF to Sift; two entries, because a browser needs the right MIME type.
    MediaType("avif-sequence", Kind.GIF, "heif", frozenset({".avif"}), "image/avif"),
    MediaType("heic-sequence", Kind.GIF, "heif", frozenset({".heic", ".heif"}), "image/heic"),
    MediaType("gif", Kind.GIF, "gif", frozenset({".gif"}), "image/gif"),
)

#: Which generation of `classify` a row's type was decided by (`assets.classified_version`); rows
#: below it are read again by the reclassify task. Raise it only with a schema step that stamps
#: every row the change cannot reach.
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

    Every origin is verified the same; Sift moves only a refused file it wrote itself, never one of
    the user's own.
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
    # Raised by nothing now; older records carry it.
    EXTENSION_CONTRADICTS_SIGNATURE = "extension_contradicts_signature"

    # An appended payload or a cut-short file: the last bytes cannot say which.
    DOES_NOT_END_WHERE_IT_SHOULD = "does_not_end_where_it_should"

    NOT_DECODABLE = "not_decodable"
    #: The decoder did not answer in time: a stalled share, never a standing verdict.
    TIMED_OUT = "timed_out"

    # A file or an installation that could not read an accepted animated WebP.
    ANIMATED_WEBP_UNREADABLE = "animated_webp_unreadable"
    NO_VIDEO_STREAM = "no_video_stream"
    PIXELS_EXCEEDED = "pixels_exceeded"


@dataclass(frozen=True, slots=True)
class IngressResult:
    """Proof that a file was verified, and what it is; only `verify_ingress` constructs one."""

    path: Path
    media: MediaType
    size: int
    origin: Origin


class NoDestination(LookupError):
    """There is nowhere to put the file: the place is missing, not the bytes. Said to a person."""


#: The refusals about a moment rather than the bytes, cleared by the next scan that sees the file.
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
# Identified structurally, not by a library: too strict eats real files, too lenient is a hole.

_HEAD_BYTES = 4096
_TAIL_BYTES = 32

# A malformed file can make a decoder spin.
_PROBE_TIMEOUT_SECONDS = 30

# The most pixels one frame may declare: far above 8K, far below a decompression bomb.
_MAX_DECODE_PIXELS = 200_000_000

_EBML = b"\x1a\x45\xdf\xa3"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_END = b"IEND\xaeB`\x82"
_GIF_END = b"\x3b"

#: A WebP's extended header and its animation flag, read from the header: ffmpeg cannot read these.
_WEBP_EXTENDED = b"VP8X"
_WEBP_ANIMATION_FLAG = 0x02
_WEBP_FLAGS_AT = 20


def _webp_is_animated(head: bytes) -> bool:
    """Whether a WebP holds a sequence of frames rather than one picture."""
    if head[12:16] != _WEBP_EXTENDED or len(head) <= _WEBP_FLAGS_AT:
        return False
    return bool(head[_WEBP_FLAGS_AT] & _WEBP_ANIMATION_FLAG)


# Brands of a still image; every other ISO base-media file is video, for ffprobe to confirm.
_HEIF_BRANDS = frozenset({b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1"})

# The AV1 flavour of the same container. `avif` is one picture; `avis` is a sequence of them.
_AVIF_BRANDS = frozenset({b"avif", b"avis"})

# Brands of a sequence, checked before `_HEIF_BRANDS`: an animated AVIF carries both.
_SEQUENCE_BRANDS = frozenset({b"msf1", b"avis"})

# Audio-only: the container is MP4's, so the brand is all there is.
_AUDIO_BRANDS = frozenset({b"M4A ", b"M4B ", b"M4P ", b"F4A ", b"F4B "})


def _iso_brands(head: bytes) -> list[bytes] | None:
    """The brands in an ISO base-media header (`ftyp`), or None if this is not one."""
    if len(head) < 12 or head[4:8] != b"ftyp":
        return None

    box_size = int.from_bytes(head[0:4], "big")
    end = min(box_size, len(head)) if box_size >= 16 else len(head)

    brands = [head[8:12]]
    brands.extend(head[offset : offset + 4] for offset in range(16, end - 3, 4))
    return brands


def _matroska_doctype(head: bytes) -> bytes | None:
    """The DocType of an EBML file (`matroska` or `webm`), found by its element id, or None."""
    if not head.startswith(_EBML):
        return None

    marker = head.find(b"\x42\x82", 0, 1024)
    if marker == -1 or marker + 3 > len(head):
        return None

    length_byte = head[marker + 2]
    if not length_byte & 0x80:
        return None
    length = length_byte & 0x7F

    value = head[marker + 3 : marker + 3 + length]
    return value.rstrip(b"\x00") or None


def classify(head: bytes, tail: bytes) -> MediaType | Reason:
    """What the bytes are, or the reason they are refused. Never both, never neither.

    Formats that define their end are checked at the tail too; JPEG is exempt, as a motion photo
    appends a video to the still.
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
        return _iso_media(brands)

    return Reason.SIGNATURE_NOT_ALLOWED


def _iso_media(brands: list[bytes]) -> MediaType | Reason:
    """What an ISO base-media file is, by its brands."""
    if any(brand in _AUDIO_BRANDS for brand in brands):
        return Reason.NO_VIDEO_STREAM
    av1 = any(brand in _AVIF_BRANDS for brand in brands)
    # A sequence first: it carries the still-image brands as well.
    if any(brand in _SEQUENCE_BRANDS for brand in brands):
        return _BY_NAME["avif-sequence" if av1 else "heic-sequence"]
    if av1:
        return _BY_NAME["avif"]
    if any(brand in _HEIF_BRANDS for brand in brands):
        return _BY_NAME["heic"]
    if brands[0] == b"qt  ":
        return _BY_NAME["mov"]
    # Not an allowlist: real MP4s carry brands nobody has heard of; ffprobe judges.
    return _BY_NAME["mp4"]


def detect(head: bytes, tail: bytes) -> MediaType | None:
    """`classify`, for callers that only want to know whether it is media."""
    outcome = classify(head, tail)
    return outcome if isinstance(outcome, MediaType) else None


def describe(head: bytes) -> str:
    """What a refused file appears to be, for a log line; it decides nothing."""
    if _looks_like_a_web_page(head):
        return _WEB_PAGE
    guess = filetype.guess(bytearray(head))
    return str(guess.mime) if guess is not None else "unrecognized"


#: What `describe` calls a web page, which the signature library has no matcher for.
_WEB_PAGE = "text/html"

#: How a web page starts, lowercased, once any byte order mark and leading whitespace are gone.
_WEB_PAGE_OPENINGS = (b"<!doctype html", b"<html", b"<head", b"<body")


def _looks_like_a_web_page(head: bytes) -> bool:
    """Whether the leading bytes are markup a browser draws as a page: a label, never a verdict."""
    opening = head[:256].removeprefix(b"\xef\xbb\xbf").lstrip().lower()
    return opening.startswith(_WEB_PAGE_OPENINGS)


# --- The gate -----------------------------------------------------------------------------


@waits_on_storage
def read_ends(path: Path) -> tuple[bytes, bytes, int]:
    """The leading and trailing bytes, and the size; blocking, and slow on a share.

    Opened non-blocking and checked on the descriptor, so a named pipe in a library cannot hang it.
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


#: The note beside a quarantined file: its name plus this, so the two sort together.
NOTE_SUFFIX = ".why.json"


def _write_note(destination: Path, note: dict[str, object], handle: str) -> None:
    """Say why this file is here beside the file itself: the note travels with it, unlike a row."""
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
    """Move a refused file away under a rebuilt name; where it went, or None if it could not."""
    directory = settings.quarantine_dir
    safe_name = _UNSAFE_IN_NAME.sub("_", path.name)[:64].lstrip(".") or "file"
    destination = directory / f"{handle}-{safe_name}"

    try:
        directory.mkdir(parents=True, exist_ok=True)

        # Two files under one temporary name collide; the earlier one is evidence.
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
    """Decide whether a file may enter Sift; `IngressRejected` if not.

    The extension is never consulted: it is a claim, not evidence. A refused file Sift wrote is
    moved to quarantine; one in the user's own library is left alone.
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
        raise _reject(outcome, path, origin, settings, handle, describe(head))

    media = outcome
    # Not checked, only said, so the log explains a type its name does not give.
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
    """Quarantine if the file is Sift's to move, record it, and build the exception."""
    # Read before the move; None, not zero, where it cannot be read.
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
    """Confirm a file really decodes and has a picture, before anything decodes it for real."""
    if result.media.name == "webp-animated":
        await _verify_animated_webp(result, settings=settings)
        return

    # Absolute: ffprobe has no `--`, so a file named `-i` would read as a flag.
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
        # Not answered is not broken: a stalled share is no standing verdict.
        raise await _reject_decoded(Reason.TIMED_OUT, result, settings, handle, "timeout") from None

    if probe.returncode != 0:
        detail = probe.stderr.decode("utf-8", "replace").strip()
        raise await _reject_decoded(Reason.NOT_DECODABLE, result, settings, handle, detail)

    if b"video" not in probe.stdout:
        raise await _reject_decoded(Reason.NO_VIDEO_STREAM, result, settings, handle, None)

    if not _within_pixel_cap(probe.stdout):
        raise await _reject_decoded(Reason.PIXELS_EXCEEDED, result, settings, handle, None)

    log.debug("ingress.decodable", handle=handle, file_type=result.media.name)


async def verify_probed(
    result: IngressResult, argv: list[str], *, settings: Settings
) -> dict[str, Any]:
    """`verify_decodable` asked of the reader's own ffprobe `argv`, whose JSON answer it returns."""
    handle = hashed(str(result.path))
    try:
        probe = await run_tool(
            argv, time_limit=_PROBE_TIMEOUT_SECONDS, priority=Priority.BACKGROUND
        )
    except SubprocessError:
        raise await _reject_decoded(Reason.TIMED_OUT, result, settings, handle, "timeout") from None

    if probe.returncode != 0:
        detail = probe.stderr.decode("utf-8", "replace").strip()
        raise await _reject_decoded(Reason.NOT_DECODABLE, result, settings, handle, detail)

    try:
        answer = json.loads(probe.stdout)
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        raise await _reject_decoded(Reason.NOT_DECODABLE, result, settings, handle, "no answer")

    listed = answer.get("streams")
    pictures = [
        one
        for one in (listed if isinstance(listed, list) else [])
        if isinstance(one, dict) and one.get("codec_type") == "video"
    ]
    if not pictures:
        raise await _reject_decoded(Reason.NO_VIDEO_STREAM, result, settings, handle, None)

    lines = "\n".join(f"video,{one.get('width', '')},{one.get('height', '')}" for one in pictures)
    if not _within_pixel_cap(lines.encode("utf-8")):
        raise await _reject_decoded(Reason.PIXELS_EXCEEDED, result, settings, handle, None)

    log.debug("ingress.decodable", handle=handle, file_type=result.media.name)
    return answer


def _within_pixel_cap(stdout: bytes) -> bool:
    """Whether every video stream sits within the pixel ceiling, against a decode bomb."""
    for line in stdout.decode("utf-8", "replace").splitlines():
        # Parsed, never raising: an exception here would abandon the bomb check.
        dims = [
            value for value in (as_int(field) for field in line.split(",")) if value is not None
        ]
        if len(dims) >= 2 and dims[0] * dims[1] > _MAX_DECODE_PIXELS:
            return False
    return True


async def _verify_animated_webp(result: IngressResult, *, settings: Settings) -> None:
    """The same two questions, asked of the tool that can read an animated WebP."""
    # Here, not at the top: the content store the WebP module reads is built on this gate.
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
    """Build the refusal on a thread: refusing a file Sift wrote moves it."""
    return await asyncio.to_thread(
        _reject, reason, result.path, result.origin, settings, handle, detail
    )


# --- Keeping the gate the only way in -----------------------------------------------------


def unguarded_ingress(source_root: Path) -> Iterator[str]:
    """Yield a complaint for every place a file could get in without being verified."""
    ingest_calls = {
        "ingest",
        "hash_file",
        "identity_file",
        "content_hash",
        "index_asset",
        # Recording an asset or its place is indexing it, whoever computed the digest.
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
    """Every collection of string constants in a module, a dict's keys included."""
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
