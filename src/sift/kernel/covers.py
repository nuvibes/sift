# SPDX-License-Identifier: AGPL-3.0-or-later
"""The picture an entity is drawn as: served the one way for all six, and received the one way too.

Its own module rather than a function in `serving.py`, and the reason is the dependency direction:
`access` imports `serving`, so `serving` cannot import `access`. This sits above both: it reads a
picture through the permission-scoped repository and sends it with the caching rules `serving`
owns, and nothing in the kernel imports it back.

Its own module rather than a function in one of the slices, too. Five things carry a cover: a
person, a Site, a tag, a photo album and a collection, spread across four slices that may not
import one another. Five copies of a permission-scoped read is four chances to write one of them
slightly differently, and the one that differs is never the one anybody looks at. (A username
has no page to choose one on, so it carries none.)

**Reading and receiving are in the same module on purpose.** A cover is one of two things: a file
in the library, or a picture somebody uploaded, and which of the two wins is a precedence. A
precedence written in one place is a rule; written in the place that reads and again in the place
that writes, it is two rules that agree until they do not.
"""

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
    """What an entity's row says its picture is. All three fields, or none of them.

    A named shape rather than a tuple, so a pointer added to it is one edit rather than an edit to
    every one of the six routes that read it.

    The three are not independent. An UPLOAD wins over a FILE, a MOMENT is meaningless without a
    file, and the one statement per entity that writes any of them writes all of them, so there is
    no row on which a moment outlives the file it was a moment of, and none on which both kinds of
    cover are set at the same time.
    """

    asset_id: str | None = None
    at_ms: int | None = None
    upload_id: str | None = None
    #: The window of that picture it is drawn as, or None for the whole of it. Only ever a window of
    #: the picture the three above name. See `frame_of`, which is the one way a row's frame is
    #: read.
    frame: CoverFrame | None = None

    @property
    def picture(self) -> str | None:
        """Which picture this is, as `picture_named` spells it. None for no cover."""
        return picture_named(asset_id=self.asset_id, at_ms=self.at_ms, upload_id=self.upload_id)


def chosen_from_row(row: Mapping[str, Any] | Row) -> ChosenCover:
    """A cover's four columns as one entity's statement read them, as a `ChosenCover`.

    Five services each read their own table's four columns, and building the shape by hand in each
    is where the frame would be forgotten. The row must carry
    `cover_asset_id`, `cover_at_ms`, `cover_upload_id` and `cover_frame`.
    """
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
    """What one cover write stores, and what its History line says: worked out once for all five.

    `frame` is the column's text (see `stored_frame`). `object` and `payload` are the event's.
    `cleared_at` is the clear mark written beside the pointers (see `cleared_mark`).

    **A REFRAME IS ITS OWN ACT.** The same picture with a new window is not "Cover set to <the file
    it already was>": that line would claim a choice nobody made. So where the picture does not
    move and the window does, the event has no object and says `{"cover": "reframed"}`, which reads
    "Cover reframed" (`sentences.COVER_WITHOUT_OBJECT`). Every other write says what it said before.
    """

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
    """The stored frame and the event for writing these pointers over `before`. See `CoverChange`."""
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


#: The most an uploaded cover picture may weigh, before anything is decoded.
#:
#: A cover is a photograph of somebody, not a film, and twenty megabytes is a generous phone camera
#: original. The cap exists because the bytes are held in memory to be piped (see `CoverPictures.
#: receive`), so an unbounded upload is an unbounded allocation, which is the one way this feature
#: could take the application down without anybody having to be clever about it.
#:
#: `capture`'s own stager has no cap at all and streams to disk instead; that is a different trade
#: for a different job (a library file IS a film) and it is recorded rather than copied.
COVER_PICTURE_MAX_BYTES = 20 * 1024 * 1024

#: How much is read from the wire at a time. The same size `capture` streams at.
_CHUNK_BYTES = 1 << 20

#: The size and quality Sift's own copy is written at.
#:
#: Taller than the 480 a thumbnail gets, because a cover is drawn as a 200px-wide portrait on a
#: header and full-bleed on a card, and unlike a thumbnail it is the ONLY copy: there is no
#: original behind it to go back to. Quality 4 against a thumbnail's 5 for the same reason.
_COVER_HEIGHT = 720
_COVER_QUALITY = 4

#: Long enough for a large photograph on a slow machine, short enough that a hang is a failure
#: rather than a request nobody ever gets an answer to.
_ENCODE_TIMEOUT = 30.0

#: Inside the cache directory, which is the one directory Sift owns and the one that is safe to
#: delete. Never beside anybody's library: the same rule `derivative_path` states.
_COVERS_DIR = "covers"

#: Where a framed cover's picture is kept, beside the uploads and apart from them: an upload is the
#: ONLY copy of its picture, and a framed one is always a copy that can be cut again.
_FRAMED_DIR = "framed"

#: How many framed pictures are cut together. A frame is cut the first time its cover is asked for,
#: inside that request, and a wall of framed covers opened for the first time asks for all of them
#: together; unbounded, that is one ffmpeg per card at the same instant. Two is enough to keep a
#: page moving (each cut is one small still, a fraction of a second) and bounds the worst case.
_CUTTING_AT_A_TIME = 2


class CoverPictureRefused(Exception):
    """The bytes could not be made into a cover. Carries the sentence to show, which is the point.

    One exception for "too big" and for "not a picture" because the caller does the same thing with
    both (refuses the request and says why), and two classes would be two `except` arms doing one
    thing. What differs is the sentence, and that travels on the instance.
    """


class CoverPictures:
    """The store for cover pictures somebody uploaded: one row, one file, one re-encode.

    A class rather than four functions taking the same two arguments, and it is provided once at
    composition, so the six routes that receive an upload and the six that serve one take a single
    dependency instead of a database and a settings object each.
    """

    def __init__(self, database: Database, settings: Settings) -> None:
        self._db = database
        self._settings = settings

    @property
    def database(self) -> Database:
        """The library these pictures belong to, for `SubjectCovers`, which is handed this store
        rather than the database and asks the database where an entity's cover stands."""
        return self._db

    @property
    def _root(self) -> Path:
        return self._settings.cache_dir / _COVERS_DIR

    async def receive(self, read: Callable[[int], Awaitable[bytes]]) -> str:
        """Take an uploaded picture in and record it. Returns the id of the row that names it.

        `read` is a chunk reader (`UploadFile.read` is exactly this shape) rather than the
        upload object itself, so this can be exercised without a request and so the kernel does not
        take a view on which web framework produced the bytes.

        **THE UPLOADED BYTES ARE NEVER A FILE.** They are read to a cap, piped into ffmpeg on
        standard input, and what lands on the disk is Sift's own JPEG. See
        `media.cover_picture_args` for why that is the security property of this feature and why it
        is stronger than writing the original and deleting it: there is no instant at which a
        stranger's bytes exist on the disk under a name, so there is nothing for anything else to
        open and nothing left behind by a crash in the middle.

        The cap is enforced WHILE READING and not from the declared length. A `Content-Length` is
        something the sender writes down, so a cap read from it is a cap the sender sets.

        The file is written before the row, and that order is deliberate: a row naming a file that
        is not there yet is a cover that answers 404 for as long as the gap lasts, while a file no
        row names is a few kilobytes the cache sweep collects. Only one of those is visible to
        anybody.
        """
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
            # An SVG is a document, and ffmpeg has no decoder for one. It is drawn ONCE, in
            # memory, with nothing it names reached (see `svg_raster`), and the PNG that comes out
            # takes the same re-encode below as every other picture: the SVG itself never lands.
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
            # The tool's own words are NOT shown. ffmpeg's stderr on a refused file names the
            # demuxers it tried and the path it was writing to, which tells the person who chose the
            # file nothing and tells anybody else more than they should have. Logged, and one
            # sentence sent.
            log.info("cover.upload.refused", detail=str(failure))
            raise CoverPictureRefused(
                "Sift couldn't read that as a picture. Try a JPEG or a PNG."
            ) from None

        size = await asyncio.to_thread(_size_of, destination)
        if size is None:
            # ffmpeg exits 0 having written nothing for more than one reason. See
            # `media_jobs.ffmpeg._seek` for one that silently skips a still's only frame. A zero
            # exit is not proof there is a picture, so the picture is what gets checked.
            raise CoverPictureRefused("Sift couldn't read that as a picture. Try a JPEG or a PNG.")

        await self._db.execute(
            "INSERT INTO cover_pictures (id, rel_cache_path, size_bytes, created_at) "
            "VALUES (?, ?, ?, ?)",
            (upload_id, f"{_COVERS_DIR}/{upload_id}.jpg", size, int(time.time() * 1000)),
        )
        log.info("cover.upload.stored", bytes=size)
        return upload_id

    async def path_of(self, upload_id: str) -> Path | None:
        """The file a stored row points at, or None when there is not one on the disk.

        The path is rebuilt from the cache directory and the stored RELATIVE path, and a row naming
        anything that escapes the cache is treated as a miss rather than followed. The row was
        written by this module, but a restored backup or a hand-edited database is exactly where a
        row that predates a guard meets the code that assumes it: the same reasoning
        `derivative_at` gives, and the same answer.
        """
        row = await self._db.fetch_one(
            "SELECT rel_cache_path FROM cover_pictures WHERE id = ?", (upload_id,)
        )
        if row is None:
            return None
        cache = self._settings.cache_dir
        candidate = cache / str(row["rel_cache_path"])
        return await asyncio.to_thread(_confined, candidate, cache)

    async def kept_as(self, upload_id: str) -> str | None:
        """Where a stored picture is, relative to the cache directory, as its row says. None if no row.

        For a store outside the kernel that keeps its own pointer at a picture this door made (a
        creator's picture), so what it writes down is the door's own relative path rather than one
        worked out again from an absolute path, which can differ in spelling and not in place.
        """
        row = await self._db.fetch_one(
            "SELECT rel_cache_path FROM cover_pictures WHERE id = ?", (upload_id,)
        )
        return None if row is None else str(row["rel_cache_path"])

    async def framed(self, source: Path, frame: CoverFrame) -> Path | None:
        """The window `frame` of the picture at `source`, cut once and kept. None if it cannot be cut.

        ## Why it is cut on the way out, and kept

        A frame is stored as four numbers beside the cover (see `stored_frame`), never as a picture
        of its own, so the picture it frames can still be framed again later, including the one
        currently chosen. The cut is therefore made where the picture is SENT, and kept in the cache
        so it is made once.

        **The name is the source's identity and the frame's.** The source's path, size and
        modification time, and the frame's token, so a moment's still that is rebuilt, or a frame
        that moves, is simply a different name and the old cut is never served for the new
        question. Nothing has to be invalidated, which is the property that makes a cache safe to
        keep.

        Cut in the request that first asks, under `_CUTTING_AT_A_TIME`. Queued as a job instead, the
        cover would answer with the WHOLE picture until the job landed (the change somebody just
        made, visibly not made) and a cut of one still is a fraction of a second.

        Written to a scratch name and moved into place, so a reader never meets half a picture and
        two requests racing to cut the same frame both end with the same whole file.

        What is kept is bounded by the frames somebody has actually chosen, each a few tens of
        kilobytes, inside the directory Sift owns and may empty; an old cut no name reaches any more
        is left for the same housekeeping an orphaned upload waits for (`receive_cover`).
        """
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
            # A zero exit is not proof there is a picture (see `receive`).
            await asyncio.to_thread(_unlink_quietly, scratch)
            return None
        await asyncio.to_thread(os.replace, scratch, destination)
        return destination

    async def forget(self, upload_id: str) -> None:
        """Drop a stored picture, row and file, once nothing points at it any more.

        Called when a cover is replaced or cleared, because an uploaded cover is the one kind of
        cover that owns bytes: a file cover points at something the library holds anyway, and this
        points at something that exists only to be that cover. Leaving them behind is a cache that
        only ever grows, with nothing on any screen to say so.

        The row goes first and the file second. The other order leaves a row naming a file that is
        gone, which is a cover that answers 404 for ever; this order leaves at worst a file no row
        names, which the same sweep collects.
        """
        await self._db.execute("DELETE FROM cover_pictures WHERE id = ?", (upload_id,))
        path = self._settings.cache_dir / _COVERS_DIR / f"{upload_id}.jpg"
        await asyncio.to_thread(_unlink_quietly, path)


_CUTTING: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def _cutting() -> asyncio.Semaphore:
    """The bound on framed cuts in flight, for the loop that is running. See `_CUTTING_AT_A_TIME`.

    One per LOOP rather than one per process: a semaphore that has once made somebody wait belongs
    to that loop, and a second loop (a test suite runs many) would be refused by it. The
    application runs one loop, so there it is one bound.
    """
    loop = asyncio.get_running_loop()
    bound = _CUTTING.get(loop)
    if bound is None:
        bound = _CUTTING[loop] = asyncio.Semaphore(_CUTTING_AT_A_TIME)
    return bound


def _identity_of(path: Path) -> str | None:
    """A picture's identity for naming its cuts: where it is, how big, and when it was written.

    Compared lexically (`abspath`, never `resolve`) for the reason `_confined` gives. None where the
    picture is not there to be cut.
    """
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
    """The path, if it really is inside the cache, and None if it is not or is not there.

    Compared lexically rather than through `resolve`: resolving follows a junction, so a link
    planted inside the cache would be judged by where it
    points rather than by where it is.
    """
    fixed = Path(os.path.abspath(candidate))
    if not fixed.is_relative_to(Path(os.path.abspath(root))):
        return None
    return fixed if fixed.is_file() else None


def _unlink_quietly(path: Path) -> None:
    """Take a cover picture off the disk. Exempt from the no-removal rule, and narrowly.

    That rule exists because Sift indexes IN PLACE: the files in a library belong to whoever put
    them there, and only the delete and organize features may touch one. This is not one of those.
    It is a file Sift wrote itself, inside the cache directory Sift owns, whose whole design is that
    it can be deleted: the same exemption the cache sweep in `content.identity` takes, for the
    same reason and with the same words on it.

    The path is composed here from the cache directory and an id this module minted, never from a
    stored string, so there is nothing a corrupt row could point this at.
    """
    # The cache is disposable and a file that is already gone is the outcome asked for.
    with contextlib.suppress(OSError):
        path.unlink()  # nosemgrep: sift-no-file-removal-outside-delete-trash


def bytes_reader(blob: bytes) -> Callable[[int], Awaitable[bytes]]:
    """Bytes already in hand, in the shape `CoverPictures.receive` reads from.

    `receive` takes a chunk reader because an upload arrives as one; a picture a stash-box handed
    over arrives whole. This is the adapter, so the store keeps its one way in (the cap, the
    re-encode, the row after the file) rather than growing a second entry for bytes that
    happen to be in memory already.
    """
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
    """How one kind of subject's cover is read and pointed, handed in by whoever owns its table.

    A person's cover is a column the people slice writes, a tag's a column the tags slice writes,
    and the kernel may not import either. So each slice hands in its two verbs at composition,
    keyed by subject, and `SubjectCovers` below asks them without knowing which table answers.
    """

    #: What the row says its picture is. The same `chosen_cover` every cover route serves from.
    chosen: Callable[[str], Awaitable[ChosenCover]]
    #: Point the row at an uploaded picture: `(local_id, upload_id, actor, box) -> whether a row
    #: changed`. `actor` is who did it and `box` which stash-box the picture came from, both for the
    #: event the write records, so the line reads "Cover set to FansDB's picture", by whoever did.
    point_at: Callable[[str, str, Actor, Object | None], Awaitable[bool]]


class SubjectCovers:
    """Giving a person, a site or a tag a picture Sift fetched, as their cover.

    ## Why a cover, and not the picture cache

    Every wall and every page draws a person through their COVER, which is a column on their row
    pointing at a file in the library or at an uploaded picture, not through any art cache. A
    fetched photograph kept anywhere else would leave a linked person wearing their monogram.

    A fetched picture IS an uploaded cover: bytes that are not a library file, re-encoded through
    the one path every uploaded cover takes, pointed at by the one column every screen reads. What
    differs is only who supplied the bytes.

    ## The two verbs

    `fill` puts a picture where there is none and stops where there is one: the unattended
    half, reached by a run nobody is watching, which must never replace a picture somebody chose.
    `keep` replaces, and is reached only by a press that says so. Both go through `receive`, so a
    login wall saved as `.jpg` is refused here exactly as it is refused from a file chooser.
    """

    def __init__(self, pictures: CoverPictures) -> None:
        self._pictures = pictures
        self._handles: dict[Subject, CoverHandle] = {}

    def register(self, subject: Subject, handle: CoverHandle) -> None:
        self._handles[subject] = handle

    async def has_one(self, subject: Subject, local_id: str) -> bool:
        """Whether this subject already has a cover a gap-filler must leave alone. False for a kind
        nobody registered, which the callers treat as nothing to do rather than as an error.

        Two answers are not read off the pointers (see `default_covers.Standing`): a cover a person
        CLEARED is one they want empty, so it counts as had, and nothing unattended puts a picture
        back; a cover that is only the rule's default (the first file's still) counts as none, so
        a stash-box's portrait of somebody takes the place of a frame of one of their files.
        """
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
        """Make these bytes the cover only where there is none. True when a picture landed.

        `actor` and `box` are for the event the cover write records: who did it, and which box the
        picture came from. Handed in rather than assumed, because the two verbs are reached by
        different doers: a run nobody is watching, and a user pressing Keep picture.
        """
        handle = self._handles.get(subject)
        if handle is None or await self.has_one(subject, local_id):
            return False
        return await self._put(handle, local_id, blob, ChosenCover(), actor, box)

    async def keep(
        self, subject: Subject, local_id: str, blob: bytes, *, actor: Actor, box: Object | None
    ) -> bool:
        """Make these bytes the cover, replacing whatever was there. True when a picture landed.

        Reached by a press, so `actor` is the USER who pressed it: recorded as Sift's own act, the
        line would say Sift had replaced a cover somebody chose to replace.
        """
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
            # Nothing points at what was just stored (the row has gone), so it is swept here,
            # for the reason `receive_cover` gives.
            await self._pictures.forget(upload_id)
            return False
        # After the write, never before it: see `forget_displaced`.
        await forget_displaced(self._pictures, before)
        return True


def cover_payload(asset_id: str | None, upload_id: str | None, box: Object | None) -> str | None:
    """What a cover change's event says about itself, where the picture is not a library file.

    None where a FILE is the cover: the file is the event's object and says it. Otherwise
    `{"cover": "picture"}` for a picture sent in and `{"cover": "none"}` for the cover taken away,
    and a picture a stash-box supplied names the box as well, `"box"` (its name then) and
    `"box_id"`, so the line reads "Cover set to FansDB's picture". See
    `sentences.COVER_WITHOUT_OBJECT`. One shape for every entity's cover writer, so a person's
    and a tag's cannot come to say one act two ways.
    """
    if asset_id is not None:
        return None
    said: dict[str, str] = {"cover": "none" if upload_id is None else "picture"}
    if upload_id is not None and box is not None:
        said["box_id"] = box.id
        if box.name:
            said["box"] = box.name
    return json.dumps(said)


def cleared_mark(asset_id: str | None, upload_id: str | None) -> int | None:
    """What a cover write stores as `cover_cleared_at` beside the pointers it writes.

    The time, where the write names no picture at all: that is a person taking the cover away, and
    Sift's own rule (`default_covers`) leaves an entity so marked empty until somebody chooses a
    picture again. NULL where it names one, which is that choice, so the mark goes with it. Worked
    out here rather than in each entity's statement, so the five writers cannot come to disagree
    about what a clear is.
    """
    return int(time.time()) if asset_id is None and upload_id is None else None


def upload_kept_by_put(
    upload_id: str | None,
    *,
    asset_id: str | None,
    frame: CoverFrame | None,
    before: ChosenCover,
) -> str | None:
    """The uploaded picture a cover PUT may name: ONLY the one that is already the cover.

    A PUT names a FILE, or nothing. Reframing an uploaded cover is the one reason for it to name an
    upload (the picture stays, its window moves), so it may name exactly the upload the row holds
    and nothing else. Any other id is refused as missing, and that is the security half: uploads are
    not addressable across entities, and a PUT pointing this entity at another's picture would let
    the next change here delete it from under the other (`forget_displaced`).

    A frame needs a picture to be a window of, so one sent with neither a file nor an upload is
    refused rather than silently dropped: the screen that sent it believes it framed something.
    Answers the upload to write: the kept one, or None where the PUT names a file or nothing.
    """
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
    """Drop the uploaded picture a cover change has just replaced, if it replaced one.

    An uploaded cover is the one kind of cover that OWNS bytes. A file cover points at something the
    library holds anyway and outlives being un-chosen; this points at a picture that exists only to
    be this entity's cover, so leaving it behind is a cache that only grows with nothing on any
    screen to say so.

    Called after the write and never before it. The other order deletes the picture that is still on
    screen and then fails to write the new pointer, which turns a refused change into a lost cover.

    `after` is the upload the row names NOW, where it still names one: a reframe keeps its picture,
    and forgetting the picture that is still the cover would turn moving a window into losing it.
    """
    if before.upload_id is not None and before.upload_id != after:
        await pictures.forget(before.upload_id)


async def receive_cover(
    pictures: CoverPictures,
    read: Callable[[int], Awaitable[bytes]],
    *,
    before: ChosenCover,
    point_at: Callable[[str], Awaitable[bool]],
) -> None:
    """Take an uploaded cover in and point one entity at it. Six routes, one body.

    What differs between the six is which entity has to be resolved against the viewer first and
    which view is sent back, and both of those stay in the route. What is the same is this, and it
    is worth sharing for one reason above the others: **the cleanup on a failed write.** A picture
    is stored before anything points at it, so a pointer write that does not land leaves a file and
    a row nothing references, and six copies of that undo is five chances to omit it in the arm
    nobody exercises.

    Raises rather than returning a verdict, and raises the HTTP answer itself, exactly as
    `serve_cover` does for a miss. The two refusals are genuinely different sentences: 400 says the
    bytes were no good and can be acted on by whoever chose the file, 404 says the thing being
    given a cover is not there.
    """
    try:
        upload_id = await pictures.receive(read)
    except CoverPictureRefused as refusal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refusal)) from None

    if not await point_at(upload_id):
        # Nothing points at the picture that was just stored, so it is swept here rather than left
        # for a housekeeping pass that does not exist yet.
        await pictures.forget(upload_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    await forget_displaced(pictures, before)


def names_its_cover(request: Request, chosen: ChosenCover, *, stamp: int) -> bool:
    """Whether the address this cover was asked for by names WHICH cover it is.

    A keepable reply promises that the address will not come to mean something else, so it may be
    made only to an address that moves when the cover moves. The client composes the cover's token
    (`coverToken` in `lib/entity/art.ts`) as the entity's own token, a dot, and then the upload id, or
    the file id and, for a chosen moment, a dot and the moment. This is the server's half of the
    same rule: the promise is made when the address ends with what the row says the cover IS, and
    the careful answer is given otherwise, so a screen that has not been told the moment or the
    upload gets a re-checked picture rather than a stale one kept for a week.

    Not a blanket refusal (a moment or an upload never keepable) nor a blanket promise (a whole file
    kept under ANY token): each of those is a statement about today's callers, and this is a
    statement about the address.

    **An upload also has to carry the user's stamp**, which a file does not. A file cover is
    served through the permission-scoped read, which reports a concealed picture and refuses it the
    promise; an uploaded picture has no such flag (it is not a library file), so the stamp is
    the only thing that takes a kept copy away when what this user may see changes. The token
    is compared as a cache key and never as a capability: a mismatch is answered with the picture,
    the careful way, exactly as a bare address is.

    **A framed cover names its frame as well**: the upload, or the file and
    moment, then a dot and `CoverFrame.token`. Moving the window changes the picture behind the
    address, so an address that did not move with it would keep the old window for a week. The
    whole picture the frame editor draws (`?whole=1`) is a different picture and its address names
    no frame (see `frame_served`).
    """
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


#: The query key that asks for a cover's WHOLE picture, whatever its frame: `?whole=1`.
WHOLE_KEY = "whole"


def frame_served(request: Request, chosen: ChosenCover) -> CoverFrame | None:
    """The window this request is answered with: the cover's frame, or none where `?whole=1` asks.

    The whole picture is what the frame editor draws its window over: to reframe the one
    currently chosen, a window can only be moved over the picture it is a window of.
    Asking for it reaches nothing new: it is the same picture, behind the same permission check,
    that the cover was before it was framed.
    """
    if request.query_params.get(WHOLE_KEY) == "1":
        return None
    return chosen.frame


def names_the_shipped(request: Request, instead: Path, *, stamp: int) -> bool:
    """Whether the address names the SHIPPED picture an empty row is answered with.

    The server's half of the rule `coverToken` in `lib/entity/art.ts` composes: the user's own token, a
    dot, and the picture's token (`site_icons.token_of`: the release and a digest of the bytes).
    Exactly that and nothing looser, for the two reasons a looser rule would be wrong:

    - **The picture's token, because the user's stamp alone does not name the logo.** A new pack
      in a release, or a site whose name or address now points at another entry, changes the
      picture without moving the stamp, and a logo kept under the stamp would stay the old one
      for a week.
    - **The stamp as well, for the reason an upload carries it.** A shipped picture is not a
      library file, so nothing permission-scoped reports it concealed; the stamp is the one thing
      that moves when what this user may see does, and it takes a kept copy away with it.

    Compared as a cache key and never as a capability, as `names_its_cover` is: a mismatch is
    answered with the same picture, the careful way.
    """
    asked = request.query_params.get(ART_KEY)
    return bool(asked) and asked == f"{face_version(stamp)}.{token_of(instead)}"


#: What a cover answer sends: the file, its type and its cache headers.
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
    """The picture an entity is drawn as: an uploaded one, a file's own still, or one moment of it.

    Five things in Sift carry a cover (a person, a Site, a tag, a photo album and a collection)
    and every one of them answers this question the same way. Written once here rather than five
    times, because five copies of a permission-scoped read is four chances to write one of them
    slightly differently, and the one that differs is never the one anybody looks at.

    **AN UPLOAD WINS, and this is the only place that says so.** The two pointers are written by one
    statement per entity so they cannot both be set, which makes the precedence unreachable in
    practice, and it is still written down here, because "cannot happen" is a property of today's
    writers and this is a property of the cover.

    **The moment comes from the ROW, never from the caller.** A still is filed under
    `(asset_id, kind, params)`, so a moment named in a query string would let anybody ask for an
    unbounded number of distinct pictures of one file and fill the cache with them. Asked this way,
    the set of stills a library can be made to hold is bounded by the number of covers somebody has
    actually chosen. The marks' own picture is built on exactly this rule and says so where it is
    served.

    A 404 covers "no cover", "not allowed" and "not built yet" alike, which is what every other
    generated picture in Sift answers with. The last of those is ordinary rather than exceptional:
    the still at a chosen moment is queued when the moment is chosen, and until it lands there is
    nothing to send. **The client falls back to the file's own picture**. See `Avatar`'s second
    source, which is what makes that sentence true rather than merely intended.

    Nothing here decides who may see anything. `serve_derivative` is scoped to the viewer, so a
    cover pointing at a file this user may not open is a miss rather than a leak, which is the
    read-side half of the rule the write side states: the route that SETS a cover checks the asset
    against the viewer as well, and the two are locks on the same door rather than one lock twice.

    An UPLOADED cover carries no such check, and it needs none: it is not a file in the library, it
    was put there by an admin as the picture for this entity, and the entity itself has already been
    resolved against the viewer by the route before this is called. Anybody who may see the thing
    may see the picture chosen for it.

    **`instead` is what to send when the row names no cover at all**, and it is a picture that came
    with Sift rather than out of the library: today, a site's own logo out of the icon pack, for a
    Site nobody has chosen a picture for. Handed in by the route because only the route knows which
    entity has a shipped picture and which does not; sent from here because a fallback written at
    the route would be a second precedence beside the one this module exists to keep in one place.

    It fires when the row is EMPTY (no upload and no file) **and when the row names a WHOLE FILE
    whose still cannot be served.** A cover at a chosen MOMENT keeps its 404, because the client's
    second address is the FILE's own still (`Avatar.instead`): a different picture, very likely
    already built, and a logo for a few seconds would look like the cover had been replaced. A cover
    naming a whole file with no moment IS that same still: the two addresses resolve to one
    derivative, so when it is missing both are, and the card would fall through to a coloured
    letter.

    So the miss falls through to the shipped picture, which leaks nothing: it came with Sift, and
    the entity was resolved against the viewer before this was called. A moment cover keeps its 404.

    The promise a long cache makes is about the ADDRESS, and this address means the shipped logo
    until the moment somebody chooses a picture, after which it means theirs. So a site's view
    carries the shipped picture's token (`SiteView.icon`, `site_icons.token_of`), and an empty row's
    logo is kept when the address carries exactly that token (`names_the_shipped`). Choosing a
    picture moves the address by itself: the screen then names the chosen cover instead, which is
    what `names_its_cover` compares. The whole-file miss is served the careful way, because its
    address names the FILE, whose still may be built at any moment.
    """
    if chosen.upload_id is None and chosen.asset_id is None and instead is not None:
        sending = _shipped(request, viewer, instead)
    elif chosen.upload_id is not None:
        sending = await _upload(request, viewer, chosen, pictures, chosen.upload_id)
    else:
        sending = await _file_cover(request, access, viewer, chosen, pictures, instead)
    path, media_type, headers = sending
    return await serve_file(request, path, media_type=media_type, headers=headers)


def _shipped(request: Request, viewer: Viewer, instead: Path) -> _Sending:
    # Kept only when the address names this shipped picture (see `names_the_shipped`).
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
        # Kept only when the address names this upload. See `names_its_cover`. An upload is
        # kept because the address carries its id, not because the bytes are safe (they are: an
        # upload id is minted per picture, never reused, and never rewritten). A keepable reply
        # promises that `/api/<wall>/<id>/cover?v=<token>` will not come to mean something
        # else, so a token that does not name the upload (one replaced by another at the same
        # address) gets the careful answer, one conditional request that a 304 answers with no
        # body.
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
        # A row naming a path that is not inside the cache any more: a restored backup, or one
        # written before the check that now refuses it. The same answer as any other miss.
        served = None
    if served is None:
        # The shipped picture rather than a 404, where there is one and the cover is a whole file.
        # See the docstring: for a whole-file cover the client's second address is this same
        # derivative, so a 404 here ends at a coloured letter and not at the file's own still.
        if instead is not None and chosen.at_ms is None:
            return instead, "image/png", CAREFUL
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No cover")
    path, cut = await _in_its_frame(request, chosen, pictures, served.path)
    if not cut:
        return path, "image/jpeg", CAREFUL
    return (
        path,
        "image/jpeg",
        # Kept only when the address names this file and, for a chosen moment, this moment. See
        # `names_its_cover`. An entity's cover address is `/api/<wall>/<id>/cover` plus a token the
        # CLIENT composes out of what it has been told, so a screen that has not been told the
        # moment gets the careful answer: a cover moved from one moment of a clip to another would
        # otherwise keep its address, and a week-long `immutable` on it would be a promise the
        # address cannot keep. The same trade `keeps` makes for a concealed picture: one conditional
        # request per such cover.
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
    """The picture to send for this cover: `source` cut to its frame, where it has one.

    The second value is False only where a frame was asked for and could NOT be cut, and then the
    whole picture is sent the careful way: its address names the frame, and a kept copy of the
    wrong window under it is exactly the stale picture `names_its_cover` exists to prevent. The next
    request tries the cut again. True everywhere else, including where there is no frame to cut.
    """
    frame = frame_served(request, chosen)
    if frame is None:
        return source, True
    framed = await pictures.framed(source, frame)
    if framed is None:
        return source, False
    return framed, True
