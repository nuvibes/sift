# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one path bytes take to become an asset, wherever they came from.

Everything a person hands Sift lands here: a file dropped on the page, a clipboard paste, an
upload, and the output of a finished download. All of it is bytes Sift wrote to disk and does not
yet trust. This sequences the steps that turn them into an indexed asset: it writes none of that
logic, it orders it:

    verify the signature (the one gate) -> copy into a chosen folder -> hash and record -> queue
    the work that draws it

One function does that for every origin, so a second, subtly different path cannot grow up beside
it. The download feature hands its output straight to `import_file`; the routes here stage their
bytes and let a job call the same function. Both are the same four steps in the same order.

`verify_ingress` is inside this function and nowhere else on the way in: reading a file's leading
bytes is what decides whether it is media at all, and a file that is not is quarantined before it is
hashed, indexed, decoded or served. A caller that skipped it would be the hole the gate exists to
close, so there is only one caller shape and it is this.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

from sift.kernel import landing, places
from sift.kernel.config import Settings
from sift.kernel.content import LocationStatus, Root
from sift.kernel.content.hashing import identity_file
from sift.kernel.destination import resolve_destination
from sift.kernel.ingress import IngressResult, NoDestination, Origin, verify_ingress
from sift.kernel.jobs import JobContext
from sift.kernel.log import get_logger
from sift.kernel.paths import PathEscape, confine
from sift.kernel.seams import ReindexSeam

log = get_logger(__name__)

#: What `import_file` queues once a file is in. Probing opens the file, works out what it is, and
#: starts the thumbnail and the rest itself, so this is the only thing enqueued here. Named as a
#: string because a feature cannot import the one that owns it and the queue takes a string; a test
#: that registers a stand-in under this name is what holds the two ends together.
PROBE = "probe"

#: Where staged bytes wait between the request that received them and the job that imports them. A
#: subdirectory of the data directory, which is Sift's own: a library root is forbidden from
#: overlapping it, so nothing staged here can be mistaken for a file already in someone's library.
STAGING_DIR_NAME = "imports"


class ImportOutcome(NamedTuple):
    """What an import turned out to be.

    `was_duplicate` is True when these exact bytes were already in the library: the import recorded
    a second place they sit rather than a second asset, so the caller can say "already in your
    library" instead of drawing a twin.

    `already_at` is set when nothing landed at all: bytes a person handed over (an upload, a drop,
    a paste) that the library already holds in a folder it can see. It names that folder, as a
    breadcrumb, for the sentence the drop answers with. See `_already_here`.
    """

    asset_id: str
    location_id: str
    was_duplicate: bool
    already_at: str | None = None


#: The origins whose bytes land ONCE. A person handing Sift a file it already has meant "add this",
#: and a second copy in their folder is a twin they then have to find and delete; the file is
#: already here, and the drop says where. A download keeps its own rule (it says its own sentence
#: about a duplicate, `download.jobs`), and a swap is another install's file arriving on purpose.
_LANDS_ONCE = frozenset({Origin.UPLOAD, Origin.DROP, Origin.PASTE})


# --- where a captured item goes -----------------------------------------------------------------


class Route(StrEnum):
    """What to do with a captured item: fetch its source, or take its bytes."""

    DOWNLOAD = "download"
    IMPORT = "import"


# Schemes a dragged or pasted string can wear that Sift cannot fetch. `blob:` and `data:` are the
# page's own in-memory bytes with no source to go back to; `file:` names a path on the machine that
# did the dragging, which is not this one; the last two are never a media source.
_UNFETCHABLE_SCHEMES = frozenset({"blob", "data", "file", "javascript", "about"})


def usable_source_url(candidate: str) -> bool:
    """Whether a string is a source Sift can go and fetch, rather than local bytes in a URL's shape.

    Dragging an image out of a browser tab offers both the image bytes and the page it came from,
    and the page is preferred: it fetches the full-resolution original with the username and site
    it came from, not the downscaled copy the drag carried. But only when the URL is one Sift can
    actually reach: a `blob:` or `data:` URL is the very bytes the drag already has, wearing a link.
    """
    text = candidate.strip()
    if not text:
        return False
    parsed = urlsplit(text)
    scheme = parsed.scheme.lower()
    if scheme in _UNFETCHABLE_SCHEMES:
        return False
    return scheme in {"http", "https"} and bool(parsed.netloc)


def route_capture(*, url: str | None, has_bytes: bool) -> Route | None:
    """Decide where a captured item goes. None when there is nothing to do.

    A usable URL wins, even when bytes came with it: the bytes a drag carries are a thumbnail, and
    the URL fetches the original. Bytes are the fallback, for when there is no URL or the URL is one
    Sift cannot fetch (a `blob:`/`data:`/`file:` one). This is the whole of the drag/paste decision,
    written once so the routes and the client cannot drift on it.
    """
    if url is not None and usable_source_url(url):
        return Route.DOWNLOAD
    if has_bytes:
        return Route.IMPORT
    return None


# --- taking a file in ---------------------------------------------------------------------------


async def import_file(
    *,
    path: Path,
    origin: Origin,
    dest_folder_id: str | None,
    ctx: JobContext,
    settings: Settings,
    reindexer: ReindexSeam,
) -> ImportOutcome:
    """Take one file in: verify it, copy it into a folder, record it, and queue its work.

    `path` is bytes Sift already wrote (a staged upload or paste, or a finished download) and it
    is never trusted. `verify_ingress` reads its leading bytes and decides what it really is; a file
    whose signature is not an accepted media type is quarantined by the gate and this raises before
    anything is copied or hashed. Only a verified file is copied into the chosen folder and handed
    to the content store, which hashes it and, for bytes already in the library, records a second
    place they sit rather than a second asset.

    The copy is deliberate. Sift usually runs on another machine, so a dropped file's real path is
    not knowable and indexing it "in place" is impossible: its bytes are copied into a root, and the
    source is left untouched. `path` is not deleted here: the caller that staged it owns it.
    """
    destination = await resolve_destination(ctx.library, dest_folder_id)

    # Off the event loop: the gate reads the file, and a large one on a network share is slow.
    checked = await asyncio.to_thread(verify_ingress, path, origin=origin, settings=settings)

    # NO PLACE LANDS: where a file was made is never kept in anything Sift writes. Every way in comes through here (a download, an upload, a paste, a
    # screenshot, a swap), so this is the one place the copy Sift makes into a library is written
    # without its location. After the gate, so a file the gate refuses is refused for its own
    # reason; before anything is hashed, so the library records the bytes it actually holds. The
    # copy is made beside the staged file, in Sift's own staging, under the same name, and taken
    # in by this same function, which finds nothing more to remove in it.
    unplaced = await asyncio.to_thread(places.remove_places, checked.path, checked.path.parent)
    if unplaced is not None:
        try:
            return await import_file(
                path=unplaced,
                origin=origin,
                dest_folder_id=dest_folder_id,
                ctx=ctx,
                settings=settings,
                reindexer=reindexer,
            )
        finally:
            await asyncio.to_thread(places.discard, unplaced)

    # The bytes' identity BEFORE the copy, so a file handed over twice lands once. Asked of the
    # verified file: the gate is still the first thing any bytes meet.
    if origin in _LANDS_ONCE and (known := await _already_here(checked, ctx)) is not None:
        log.info("capture.already_here", asset_id=known.asset_id, origin=str(origin))
        return known

    rel_path = await asyncio.to_thread(
        _copy_into_folder, path, destination.root, destination.rel_dir
    )
    # The location row is about the COPY, which is the file every later pass will find at that
    # path. Stamped with the staged source's age, the row would disagree with the copy on the
    # first rescan, so every imported and downloaded file would be gated and digested a second time.
    # The identity still comes from the verified source: the bytes are the same bytes.
    copied = Path(destination.root.abs_path) / rel_path
    landed = await asyncio.to_thread(copied.stat)

    ingested = await ctx.content.ingest(
        checked,
        root_id=destination.root.id,
        rel_path=rel_path,
        folder_id=destination.folder_id,
        mtime=int(landed.st_mtime),
    )

    if ingested.asset_is_new:
        # Counted for the scan's line in History, as a walk counts what it takes in.
        ctx.arrived(1)
        # WHILE THE BYTES ARE STILL LOCAL. Anything that has to read a file end to end gets its one
        # affordable chance here: the staged copy is on this machine's own disk, and the copy that
        # was just made of it very often is not. What runs is whatever registered (see
        # `kernel/landing.py`) and none of it can fail the import.
        #
        # Only for bytes the library did not already have. A second copy of a file already indexed
        # is a location row and nothing else, and reading it again would answer a question that has
        # an answer.
        #
        # With the folder the bytes are going into, because some of that work is a folder's to
        # switch on or off: the music fingerprint is off unless the folder says yes.
        await landing.landed(
            path, ingested.asset.identity, settings=settings, root_id=destination.root.id
        )
        # Probing, and nothing else. It opens the file, records what it is, and starts the
        # thumbnail, preview and sprite itself: each needs what only it knows. Gated on the asset
        # being new: importing bytes already in the library adds a place they sit and no work.
        await ctx.enqueue_child(PROBE, {"asset_id": ingested.asset.id})
        # A filename is indexed text, so the index is told the moment the row exists. Without this
        # a file that has just been imported cannot be found by its own name (the grid shows it,
        # typing it finds nothing) until something else rebuilds the index.
        await reindexer.touched(ingested.asset.id)

    log.info(
        "capture.imported",
        asset_id=ingested.asset.id,
        location_id=ingested.location.id,
        new_asset=ingested.asset_is_new,
        origin=str(origin),
    )
    return ImportOutcome(
        asset_id=ingested.asset.id,
        location_id=ingested.location.id,
        was_duplicate=not ingested.asset_is_new,
    )


async def _already_here(checked: IngressResult, ctx: JobContext) -> ImportOutcome | None:
    """The file these bytes already are, where the library can see it, or None to land them.

    By identity (`identity_file`, the same key `ingest` files them under), so a renamed copy is the
    same file. Only a place that is PRESENT counts: bytes whose every copy has gone missing are not
    here, and landing them again is how a person puts back a file they lost. The place named is the
    first present one, as the folder a person would open to find it: the library's name and the
    folders under it, as a breadcrumb.
    """
    known = await ctx.content.resolve_by_identity(await identity_file(checked))
    if known is None:
        return None
    present = [
        one for one in await ctx.content.locations(known.id) if one.status is LocationStatus.PRESENT
    ]
    if not present:
        return None
    first = present[0]
    root = await ctx.library.get_root(first.root_id)
    held = first.archive_rel_path if first.inside_an_archive else first.rel_path
    folders = [part for part in (held or "").split("/")[:-1] if part]
    where = " > ".join([root.name if root is not None else "Your library", *folders])
    return ImportOutcome(
        asset_id=known.id, location_id=first.id, was_duplicate=True, already_at=where
    )


# Everything that is not a plain, safe filename character. The name arrives from the same untrusted
# place the bytes did and becomes a real file in someone's library, so it is rebuilt from what is
# safe rather than trusted: no separators (which would walk out of the folder), no control bytes.
_UNSAFE_IN_NAME = re.compile(r"[^A-Za-z0-9._ ()\-]")
_MAX_NAME = 128
#: The longest thing the cut treats as an extension: `.jpeg`, `.webm`, `.mkv`, never a sentence
#: after a dot in the middle of a title.
_LONGEST_EXTENSION = 10


def safe_name(name: str) -> str:
    """A filename that is only ever a filename: no path in it, nothing that walks out of a folder.

    THE CUT KEEPS THE EXTENSION. Cutting a long name at the end would cut the extension (`.jp`, a
    bare `.`, or nothing), and a file with no extension is a file the player cannot name a type
    for. The stem is what gives; a short extension (up to ten characters, so `.tar.gz` is not one)
    is kept whole.
    """
    cleaned = _UNSAFE_IN_NAME.sub("_", name).strip().lstrip(".")
    if len(cleaned) > _MAX_NAME:
        stem, dot, extension = cleaned.rpartition(".")
        if dot and stem and 0 < len(extension) <= _LONGEST_EXTENSION:
            cleaned = f"{stem[: _MAX_NAME - len(extension) - 1]}.{extension}"
        else:
            cleaned = cleaned[:_MAX_NAME]
    return cleaned or "file"


#: What a screenshot saved into the library carries after the name of the file it was taken of.
SCREENSHOT_MARK = "-ss"


def screenshot_name(of: str, taken: str) -> str:
    """What a screenshot saved into the library is called: `<stem>-ss.<extension>`.

    The stem is the file it was taken of, so the screenshot sits beside its source in a sorted
    folder and says what it shows. The extension is the PICTURE's (`taken`, the name it arrived
    under), never the source's: a screenshot of a video is a PNG, and a PNG named `.mp4` is a file
    the gate refuses for wearing the wrong extension. A second screenshot of the same file is
    numbered by the folder, the way any name that is taken is (`_claim_unique_path`).
    """
    stem = of.rpartition(".")[0] or of
    extension = taken.rpartition(".")[2] if "." in taken else "png"
    return safe_name(f"{stem}{SCREENSHOT_MARK}.{extension}")


def _copy_into_folder(source: Path, root: Root, rel_dir: str) -> str:
    """Copy verified bytes into a folder of a root without overwriting anything, and return the
    path they landed at, relative to the root.

    This is one of the few times Sift writes into a library, and it is the deliberate one: the
    person asked for the file to be added here. It never writes over a file already in the folder
    (a name that is taken is given a number) because the folder holds the person's own files and an
    import must not silently replace one of them.
    """
    root_path = Path(root.abs_path)

    # The library itself, before anything is created inside it. A root can stop being there between
    # being chosen and being written to (a disk unplugged, a share whose server went, a container
    # started without the mount that carries the folder) and `mkdir(parents=True)` reacts to that
    # by trying to CREATE the missing library, one component at a time, on whatever filesystem is
    # underneath. What comes back is an errno about that filesystem, not about the library
    # ("Read-only file system" on some part of a path nobody had chosen), saying nothing about the
    # folder they had, once per attempt, and the person is left to guess.
    #
    # Asked here rather than when the destination was resolved, for two reasons: this already runs
    # in a thread, so a stat on a dead share cannot hold the event loop; and it is the line that
    # follows, so nothing can drift in between.
    if not root_path.is_dir():
        raise NoDestination(
            f'Sift cannot see the folder "{root.name}" any more, so there is nowhere to put this. '
            "Check the drive it is on is still attached, then try again."
        )

    base = root_path / rel_dir if rel_dir else root_path
    base.mkdir(parents=True, exist_ok=True)

    # The write side is confined the way the read side is. A subfolder that is a symlink pointing
    # outside the root is a lexically clean rel_dir (no `..` in it) and copying into it would land
    # the person's file outside their own library. The trailing relative_to below is lexical and
    # cannot see through a symlink, so the resolved destination is confirmed to sit under the resolved
    # root first. (walk_media never descends a symlinked directory, so this only bites a link planted
    # after the folder was recorded, but the guard does not lean on that.)
    try:
        confine(root_path, base)
    except PathEscape as exc:
        raise ValueError("the destination folder resolves outside its library root") from exc

    target = _claim_unique_path(base, safe_name(source.name))
    try:
        # The BYTES, and nothing else.
        #
        # `copy2` would be the obvious call and it is the wrong one: it copies the permissions and
        # timestamps afterwards, and a network share does not always allow that. A folder mounted
        # from a NAS can refuse the chmod with "Operation not permitted", which would fail every
        # download into one, once per attempt, AFTER the file had already been written. Each attempt
        # would leave its own numbered copy: one dropped link, several identical files and a
        # duplicate warning.
        #
        # Nothing is lost by not copying them. The destination was created a line ago, by Sift, with
        # the mode it should have; the source is a file in a scratch directory that was itself
        # created moments earlier, so its timestamps describe the download rather than the media.
        shutil.copyfile(source, target)
    except OSError:
        # The name was claimed by creating the file, so a failure here leaves an empty or half-
        # written one sitting in somebody's folder, and the next attempt numbers around it rather
        # than reusing it. Three tries, three files. Whatever went wrong, this path is not a file
        # anybody asked for.
        #
        # This one line removes a file inside a library, which is the thing the rule below exists to
        # stop, so it is worth being exact about why it is allowed: the path was created by the line
        # above, by Sift, with O_CREAT | O_EXCL, so it did not exist a moment ago and holds nothing
        # but the bytes this call was in the middle of writing. It is never a file somebody else put
        # there, and that is guaranteed by the exclusive create rather than assumed. Suppressed on
        # the line rather than for the file: everything else here writes INTO a library, which is
        # exactly what the rule should keep watching.
        target.unlink(missing_ok=True)  # nosemgrep: sift-no-file-removal-outside-delete-trash
        raise
    return target.relative_to(root_path).as_posix()


def _claim_unique_path(directory: Path, name: str) -> Path:
    """Reserve a not-yet-taken path in `directory` for `name`, numbering to avoid a clash.

    The reservation is atomic: `O_CREAT | O_EXCL` creates the file only when it does not already
    exist, so two imports landing the same name at the same moment cannot both claim it: the one
    that loses the create tries the next number instead of writing over the file the winner just
    took. A plain exists()-then-copy would let both pick the same free name, and the second copy
    would overwrite the first's bytes while the ledger still pointed a location at that path.
    """
    candidate = directory / name
    stem, suffix = candidate.stem, candidate.suffix
    attempt = 0
    while True:
        try:
            handle = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            attempt += 1
            candidate = directory / f"{stem}-{attempt}{suffix}"
            continue
        os.close(handle)
        return candidate
