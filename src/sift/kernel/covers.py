# SPDX-License-Identifier: AGPL-3.0-or-later
"""The picture an entity is drawn as, served and received one way for all of them.

Reading and receiving share a module so an upload winning over a file is one rule, not two."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import time
import weakref
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from starlette.requests import Request
from starlette.responses import Response

from sift.kernel import media, svg_raster
from sift.kernel.access import Repository, Viewer
from sift.kernel.access.default_covers import standing
from sift.kernel.config import Settings
from sift.kernel.content.identity import DerivativeKind
from sift.kernel.cover_frame import CoverFrame, frame_of, picture_named, stored_frame
from sift.kernel.db import Database, Row
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, Object
from sift.kernel.log import get_logger
from sift.kernel.records import Subject
from sift.kernel.serving import ART_KEY, CAREFUL, face_version, keeps, serve_file
from sift.kernel.site_icons import token_of

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ChosenCover:
    """What an entity's row says its picture is; an upload wins over a file, a moment needs one."""

    asset_id: str | None = None
    at_ms: int | None = None
    upload_id: str | None = None
    #: The window of that picture it is drawn as, or None for the whole; read only by `frame_of`.
    frame: CoverFrame | None = None

    @property
    def picture(self) -> str | None:
        """Which picture this is, as `picture_named` spells it. None for no cover."""
        return picture_named(asset_id=self.asset_id, at_ms=self.at_ms, upload_id=self.upload_id)


def chosen_from_row(row: Mapping[str, Any] | Row) -> ChosenCover:
    """A row's four cover columns as a `ChosenCover`, so no service forgets the frame."""
    asset_id, at_ms = row["cover_asset_id"], row["cover_at_ms"]
    upload_id = row["cover_upload_id"]
    return ChosenCover(
        asset_id=asset_id,
        at_ms=at_ms,
        upload_id=upload_id,
        frame=frame_of(row["cover_frame"], asset_id=asset_id, at_ms=at_ms, upload_id=upload_id),
    )


@dataclass(frozen=True, slots=True)
class CoverChange:
    """What one cover write stores and what its History line says; a reframe is its own act."""

    frame: str | None
    object: Object | None
    payload: str | None
    cleared_at: int | None = None


def cover_change(
    before: ChosenCover,
    *,
    asset_id: str | None,
    at_ms: int | None,
    upload_id: str | None,
    frame: CoverFrame | None,
    box: Object | None = None,
) -> CoverChange:
    """The stored frame and the event for writing these pointers over `before`."""
    stored = stored_frame(frame, asset_id=asset_id, at_ms=at_ms, upload_id=upload_id)
    after = picture_named(asset_id=asset_id, at_ms=at_ms, upload_id=upload_id)
    kept = None if frame is None or frame.is_whole else frame
    if after is not None and after == before.picture and kept != before.frame:
        return CoverChange(stored, None, json.dumps({"cover": "reframed"}))
    return CoverChange(
        stored,
        None if asset_id is None else Object(kind="asset", id=asset_id),
        cover_payload(asset_id, upload_id, box),
        cleared_mark(asset_id, upload_id),
    )


#: The bytes are held in memory to be piped, so an unbounded upload is an unbounded allocation.
COVER_PICTURE_MAX_BYTES = 20 * 1024 * 1024

_CHUNK_BYTES = 1 << 20

#: Taller and finer than a thumbnail: a cover is the only copy, with no original behind it.
_COVER_HEIGHT = 720
_COVER_QUALITY = 4

#: Long enough for a large photograph on a slow machine; a hang is a failure.
_ENCODE_TIMEOUT = 30.0

#: Inside the cache directory Sift owns, never beside a library.
_COVERS_DIR = "covers"

#: Framed pictures apart from uploads: an upload is the only copy, a cut can be made again.
_FRAMED_DIR = "framed"

#: Framed cuts made together, so a wall opened for the first time is not one ffmpeg per card.
_CUTTING_AT_A_TIME = 2


class CoverPictureRefused(Exception):
    """The bytes could not be made into a cover; carries the sentence to show."""


class CoverPictures:
    """The store for cover pictures somebody uploaded: one row, one file, one re-encode."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self._db = database
        self._settings = settings

    @property
    def database(self) -> Database:
        """The library these pictures belong to, for `SubjectCovers`."""
        return self._db

    @property
    def _root(self) -> Path:
        return self._settings.cache_dir / _COVERS_DIR

    async def receive(self, read: Callable[[int], Awaitable[bytes]]) -> str:
        """Take an uploaded picture in, re-encoded from memory, and return its row's id."""
        # The bytes are never a file: piped to ffmpeg, capped while read, the file before the row.
        raw = bytearray()
        while True:
            chunk = await read(_CHUNK_BYTES)
            if not chunk:
                break
            raw += chunk
            if len(raw) > COVER_PICTURE_MAX_BYTES:
                raise CoverPictureRefused(
                    f"A cover picture has to be under "
                    f"{COVER_PICTURE_MAX_BYTES // (1024 * 1024)} MB."
                )
        if not raw:
            raise CoverPictureRefused("There was nothing in that file.")
        if svg_raster.looks_like_svg(bytes(raw)):
            # ffmpeg cannot read an SVG; drawn once in memory, its PNG takes the same re-encode.
            try:
                raw = bytearray(await asyncio.to_thread(svg_raster.rasterise, bytes(raw)))
            except svg_raster.SvgRefused as refused:
                log.info("cover.upload.refused", detail=f"svg: {refused}")
                raise CoverPictureRefused(
                    "Sift couldn't read that as a picture. Try a JPEG or a PNG."
                ) from None

        upload_id = new_id()
        destination = self._root / f"{upload_id}.jpg"
        await asyncio.to_thread(self._root.mkdir, parents=True, exist_ok=True)
        argv = media.cover_picture_args(
            destination,
            height=_COVER_HEIGHT,
            quality=_COVER_QUALITY,
            settings=self._settings,
        )
        try:
            await media.run(argv, time_limit=_ENCODE_TIMEOUT, stdin=bytes(raw))
        except media.FFmpegError as failure:
            # The tool's own words name paths and demuxers, so they are logged, never shown.
            log.info("cover.upload.refused", detail=str(failure))
            raise CoverPictureRefused(
                "Sift couldn't read that as a picture. Try a JPEG or a PNG."
            ) from None

        size = await asyncio.to_thread(_size_of, destination)
        if size is None:
            # A zero exit is not proof there is a picture, so the picture is checked.
            raise CoverPictureRefused("Sift couldn't read that as a picture. Try a JPEG or a PNG.")

        await self._db.execute(
            "INSERT INTO cover_pictures (id, rel_cache_path, size_bytes, created_at) "
            "VALUES (?, ?, ?, ?)",
            (upload_id, f"{_COVERS_DIR}/{upload_id}.jpg", size, int(time.time() * 1000)),
        )
        log.info("cover.upload.stored", bytes=size)
        return upload_id

    async def path_of(self, upload_id: str) -> Path | None:
        """The file a row points at, or None when it is missing or escapes the cache."""
        row = await self._db.fetch_one(
            "SELECT rel_cache_path FROM cover_pictures WHERE id = ?", (upload_id,)
        )
        if row is None:
            return None
        cache = self._settings.cache_dir
        candidate = cache / str(row["rel_cache_path"])
        return await asyncio.to_thread(_confined, candidate, cache)

    async def kept_as(self, upload_id: str) -> str | None:
        """Where a stored picture is, relative to the cache, as its row says; None if no row."""
        row = await self._db.fetch_one(
            "SELECT rel_cache_path FROM cover_pictures WHERE id = ?", (upload_id,)
        )
        return None if row is None else str(row["rel_cache_path"])

    async def framed(self, source: Path, frame: CoverFrame) -> Path | None:
        """The window `frame` of `source`, cut once on the way out and kept, or None if it fails."""
        # Named by the source's identity and the frame's token, so nothing needs invalidating.
        identity = await asyncio.to_thread(_identity_of, source)
        if identity is None:
            return None
        name = hashlib.blake2s(f"{identity}|{frame.token}".encode(), digest_size=16).hexdigest()
        folder = self._root / _FRAMED_DIR
        destination = folder / f"{name}.jpg"
        if await asyncio.to_thread(_size_of, destination) is not None:
            return destination
        await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        scratch = folder / f"{name}.{new_id()}.part.jpg"
        argv = media.cover_frame_args(
            source, scratch, crop=frame.crop(), quality=_COVER_QUALITY, settings=self._settings
        )
        async with _cutting():
            try:
                await media.run(argv, time_limit=_ENCODE_TIMEOUT)
            except media.FFmpegError as failure:
                log.info("cover.frame.refused", detail=str(failure))
                await asyncio.to_thread(_unlink_quietly, scratch)
                return None
        if await asyncio.to_thread(_size_of, scratch) is None:
            # A zero exit is not proof there is a picture.
            await asyncio.to_thread(_unlink_quietly, scratch)
            return None
        await asyncio.to_thread(os.replace, scratch, destination)
        return destination

    async def forget(self, upload_id: str) -> None:
        """Drop an uploaded picture no cover names any more: the row first, then the file."""
        await self._db.execute("DELETE FROM cover_pictures WHERE id = ?", (upload_id,))
        path = self._settings.cache_dir / _COVERS_DIR / f"{upload_id}.jpg"
        await asyncio.to_thread(_unlink_quietly, path)


_CUTTING: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def _cutting() -> asyncio.Semaphore:
    """The bound on framed cuts in flight, one per loop, as a semaphore belongs to its loop."""
    loop = asyncio.get_running_loop()
    bound = _CUTTING.get(loop)
    if bound is None:
        bound = _CUTTING[loop] = asyncio.Semaphore(_CUTTING_AT_A_TIME)
    return bound


def _identity_of(path: Path) -> str | None:
    """A picture's identity for naming its cuts: lexical path, size and write time, or None."""
    try:
        measured = path.stat()
    except OSError:
        return None
    return f"{os.path.abspath(path)}|{measured.st_size}|{measured.st_mtime_ns}"


def _size_of(path: Path) -> int | None:
    try:
        size = path.stat().st_size
    except OSError:
        return None
    return size or None


def _confined(candidate: Path, root: Path) -> Path | None:
    """The path if it is really inside the cache and there; compared lexically, not resolved."""
    fixed = Path(os.path.abspath(candidate))
    if not fixed.is_relative_to(Path(os.path.abspath(root))):
        return None
    return fixed if fixed.is_file() else None


def _unlink_quietly(path: Path) -> None:
    """Remove a cover picture Sift wrote into its own cache, from a path this module composed."""
    # The cache is disposable and a file that is already gone is the outcome asked for.
    with contextlib.suppress(OSError):
        path.unlink()  # nosemgrep: sift-no-file-removal-outside-delete-trash


def bytes_reader(blob: bytes) -> Callable[[int], Awaitable[bytes]]:
    """Bytes already in hand, in the shape `CoverPictures.receive` reads from."""
    view = memoryview(blob)
    at = 0

    async def read(size: int) -> bytes:
        nonlocal at
        chunk = view[at : at + max(size, 0)].tobytes()
        at += len(chunk)
        return chunk

    return read


@dataclass(frozen=True, slots=True)
class CoverHandle:
    """How one subject's cover is read and pointed, handed in by the slice owning its table."""

    #: What the row says its picture is.
    chosen: Callable[[str], Awaitable[ChosenCover]]
    #: Point the row at an upload: `(local_id, upload_id, actor, box) -> whether a row changed`.
    point_at: Callable[[str, str, Actor, Object | None], Awaitable[bool]]


class SubjectCovers:
    """Giving a person, site or tag a fetched picture as their cover, through `receive`.

    `fill` only where there is none, for unattended runs; `keep` replaces, on a press."""

    def __init__(self, pictures: CoverPictures) -> None:
        self._pictures = pictures
        self._handles: dict[Subject, CoverHandle] = {}

    def register(self, subject: Subject, handle: CoverHandle) -> None:
        self._handles[subject] = handle

    async def has_one(self, subject: Subject, local_id: str) -> bool:
        """Whether a gap-filler must leave this cover alone: a cleared one counts, a default not."""
        handle = self._handles.get(subject)
        if handle is None:
            return False
        stands = await standing(self._pictures.database, subject.value, local_id)
        if stands.cleared:
            return True
        if stands.by_default:
            return False
        before = await handle.chosen(local_id)
        return before.upload_id is not None or before.asset_id is not None

    async def fill(
        self, subject: Subject, local_id: str, blob: bytes, *, actor: Actor, box: Object | None
    ) -> bool:
        """Make these bytes the cover only where there is none. True when a picture landed."""
        handle = self._handles.get(subject)
        if handle is None or await self.has_one(subject, local_id):
            return False
        return await self._put(handle, local_id, blob, ChosenCover(), actor, box)

    async def keep(
        self, subject: Subject, local_id: str, blob: bytes, *, actor: Actor, box: Object | None
    ) -> bool:
        """Make these bytes the cover, replacing what was there; `actor` is who pressed."""
        handle = self._handles.get(subject)
        if handle is None:
            return False
        return await self._put(handle, local_id, blob, await handle.chosen(local_id), actor, box)

    async def _put(
        self,
        handle: CoverHandle,
        local_id: str,
        blob: bytes,
        before: ChosenCover,
        actor: Actor,
        box: Object | None,
    ) -> bool:
        try:
            upload_id = await self._pictures.receive(bytes_reader(blob))
        except CoverPictureRefused as refusal:
            log.info("covers.subject.refused", subject_id=local_id, why=str(refusal))
            return False
        if not await handle.point_at(local_id, upload_id, actor, box):
            # Nothing points at what was just stored, so it is swept now.
            await self._pictures.forget(upload_id)
            return False
        # After the write, never before it: see `forget_displaced`.
        await forget_displaced(self._pictures, before)
        return True


def cover_payload(asset_id: str | None, upload_id: str | None, box: Object | None) -> str | None:
    """A cover event's payload where the picture is not a library file, naming any stash-box."""
    if asset_id is not None:
        return None
    said: dict[str, str] = {"cover": "none" if upload_id is None else "picture"}
    if upload_id is not None and box is not None:
        said["box_id"] = box.id
        if box.name:
            said["box"] = box.name
    return json.dumps(said)


def cleared_mark(asset_id: str | None, upload_id: str | None) -> int | None:
    """The `cover_cleared_at` a write stores: the time where it names no picture, else NULL."""
    return int(time.time()) if asset_id is None and upload_id is None else None


def upload_kept_by_put(
    upload_id: str | None,
    *,
    asset_id: str | None,
    frame: CoverFrame | None,
    before: ChosenCover,
) -> str | None:
    """The upload a cover PUT may name: only the current cover's, so uploads stay unshared."""
    if upload_id is not None and (asset_id is not None or upload_id != before.upload_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if frame is not None and asset_id is None and upload_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "A frame needs a picture to be a window of."
        )
    return upload_id


async def forget_displaced(
    pictures: CoverPictures, before: ChosenCover, *, after: str | None = None
) -> None:
    """Drop the upload a cover change replaced, after the write and never before it."""
    if before.upload_id is not None and before.upload_id != after:
        await pictures.forget(before.upload_id)


async def receive_cover(
    pictures: CoverPictures,
    read: Callable[[int], Awaitable[bytes]],
    *,
    before: ChosenCover,
    point_at: Callable[[str], Awaitable[bool]],
) -> None:
    """Take an uploaded cover in and point one entity at it, sweeping the picture if that fails."""
    try:
        upload_id = await pictures.receive(read)
    except CoverPictureRefused as refusal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refusal)) from None

    if not await point_at(upload_id):
        # Nothing points at the picture just stored, so it is swept here.
        await pictures.forget(upload_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    await forget_displaced(pictures, before)


def names_its_cover(request: Request, chosen: ChosenCover, *, stamp: int) -> bool:
    """Whether the asked address names which cover this is, so a keepable reply stays true.

    An upload must carry the user's stamp too, and a framed cover its frame token."""
    asked = request.query_params.get(ART_KEY)
    if not asked:
        return False
    frame = frame_served(request, chosen)
    framed = "" if frame is None else f".{frame.token}"
    if chosen.upload_id is not None:
        return asked == f"{face_version(stamp)}.{chosen.upload_id}{framed}"
    if chosen.asset_id is None:
        return False
    moment = "" if chosen.at_ms is None else f".{chosen.at_ms}"
    return asked.endswith(f".{chosen.asset_id}{moment}{framed}")


#: The query key that asks for a cover's whole picture, whatever its frame: `?whole=1`.
WHOLE_KEY = "whole"


def frame_served(request: Request, chosen: ChosenCover) -> CoverFrame | None:
    """The window this request is answered with; `?whole=1` gives the frame editor the whole."""
    if request.query_params.get(WHOLE_KEY) == "1":
        return None
    return chosen.frame


def names_the_shipped(request: Request, instead: Path, *, stamp: int) -> bool:
    """Whether the address names the shipped picture with the user's stamp and its token."""
    asked = request.query_params.get(ART_KEY)
    return bool(asked) and asked == f"{face_version(stamp)}.{token_of(instead)}"


_Sending = tuple[Path, str, Mapping[str, str]]


async def serve_cover(
    request: Request,
    access: Repository,
    viewer: Viewer,
    *,
    chosen: ChosenCover,
    pictures: CoverPictures,
    instead: Path | None = None,
) -> Response:
    """The picture an entity is drawn as: an upload, a file's still, a moment, or a shipped one.

    An upload wins; a moment comes from the row, never the caller; a miss is a 404 the client
    falls back from, except a whole-file cover, which falls to the shipped picture."""
    if chosen.upload_id is None and chosen.asset_id is None and instead is not None:
        sending = _shipped(request, viewer, instead)
    elif chosen.upload_id is not None:
        sending = await _upload(request, viewer, chosen, pictures, chosen.upload_id)
    else:
        sending = await _file_cover(request, access, viewer, chosen, pictures, instead)
    path, media_type, headers = sending
    return await serve_file(request, path, media_type=media_type, headers=headers)


def _shipped(request: Request, viewer: Viewer, instead: Path) -> _Sending:
    # Kept only when the address names this shipped picture.
    return (
        instead,
        "image/png",
        keeps(
            request,
            version=(
                token_of(instead)
                if names_the_shipped(request, instead, stamp=viewer.cache_stamp)
                else None
            ),
            concealed=False,
        ),
    )


async def _upload(
    request: Request, viewer: Viewer, chosen: ChosenCover, pictures: CoverPictures, upload_id: str
) -> _Sending:
    path = await pictures.path_of(upload_id)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No cover")
    path, cut = await _in_its_frame(request, chosen, pictures, path)
    if not cut:
        return path, "image/jpeg", CAREFUL
    return (
        path,
        "image/jpeg",
        # Kept only when the address names this upload (`names_its_cover`).
        keeps(
            request,
            version=(
                chosen.upload_id
                if names_its_cover(request, chosen, stamp=viewer.cache_stamp)
                else None
            ),
            concealed=False,
        ),
    )


async def _file_cover(
    request: Request,
    access: Repository,
    viewer: Viewer,
    chosen: ChosenCover,
    pictures: CoverPictures,
    instead: Path | None,
) -> _Sending:
    if chosen.asset_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No cover")
    params = None if chosen.at_ms is None else {"at_ms": chosen.at_ms}
    try:
        served = await access.serve_derivative(
            viewer, chosen.asset_id, DerivativeKind.THUMB, params=params
        )
    except ValueError:
        # A row naming a path outside the cache is any other miss.
        served = None
    if served is None:
        # A whole-file cover falls to the shipped picture: its second address is this same still.
        if instead is not None and chosen.at_ms is None:
            return instead, "image/png", CAREFUL
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No cover")
    path, cut = await _in_its_frame(request, chosen, pictures, served.path)
    if not cut:
        return path, "image/jpeg", CAREFUL
    return (
        path,
        "image/jpeg",
        # Kept only when the address names this file and moment (`names_its_cover`).
        keeps(
            request,
            version=(
                served.version
                if names_its_cover(request, chosen, stamp=viewer.cache_stamp)
                else None
            ),
            concealed=served.concealed,
        ),
    )


async def _in_its_frame(
    request: Request, chosen: ChosenCover, pictures: CoverPictures, source: Path
) -> tuple[Path, bool]:
    """`source` cut to its frame; False where the cut failed and the whole is sent carefully."""
    frame = frame_served(request, chosen)
    if frame is None:
        return source, True
    framed = await pictures.framed(source, frame)
    if framed is None:
        return source, False
    return framed, True
