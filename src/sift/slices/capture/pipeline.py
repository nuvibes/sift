# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one path bytes take to become an asset: verify, copy into a folder, record, queue its work.
Every origin comes through `import_file`, so the ingress gate has exactly one caller."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

from sift.kernel import landing, lanes, places
from sift.kernel.config import Settings
from sift.kernel.content import Asset, LocationStatus, Root
from sift.kernel.content.hashing import identity_file
from sift.kernel.destination import resolve_destination
from sift.kernel.ingress import IngressResult, Kind, NoDestination, Origin, verify_ingress
from sift.kernel.jobs import JobContext
from sift.kernel.jobs.retrying import WaitingForSpace, is_disk_full
from sift.kernel.log import get_logger
from sift.kernel.paths import PathEscape, confine
from sift.kernel.seams import ReindexSeam

log = get_logger(__name__)

#: Queued once a file is in; probing starts the thumbnail and the rest itself.
PROBE = "probe"

#: Staged bytes wait here, inside the data directory, which no library root may overlap.
STAGING_DIR_NAME = "imports"


class ImportOutcome(NamedTuple):
    """What an import turned out to be: a new asset, a duplicate, or bytes already in a visible
    folder."""

    asset_id: str
    location_id: str
    was_duplicate: bool
    already_at: str | None = None


#: Origins whose bytes land once: a second copy of a dropped file is a twin to clean up.
_LANDS_ONCE = frozenset({Origin.UPLOAD, Origin.DROP, Origin.PASTE})


# --- where a captured item goes -----------------------------------------------------------------


class Route(StrEnum):
    """What to do with a captured item: fetch its source, or take its bytes."""

    DOWNLOAD = "download"
    IMPORT = "import"


# Schemes that Sift cannot fetch: in-page bytes, a path on another machine, or never media.
_UNFETCHABLE_SCHEMES = frozenset({"blob", "data", "file", "javascript", "about"})


def usable_source_url(candidate: str) -> bool:
    """Whether a string is a source Sift can fetch, rather than local bytes in a URL's shape."""
    text = candidate.strip()
    if not text:
        return False
    parsed = urlsplit(text)
    scheme = parsed.scheme.lower()
    if scheme in _UNFETCHABLE_SCHEMES:
        return False
    return scheme in {"http", "https"} and bool(parsed.netloc)


def route_capture(*, url: str | None, has_bytes: bool) -> Route | None:
    """Decide where a captured item goes, preferring a fetchable URL to the thumbnail bytes a drag
    carries."""
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
    """Take one untrusted file in: verify it, copy it into a folder, record it, and queue its work."""
    destination = await resolve_destination(ctx.library, dest_folder_id)

    checked = await asyncio.to_thread(verify_ingress, path, origin=origin, settings=settings)

    # Every way in passes here, so this is where the copy loses where it was made. After the gate,
    # before hashing.
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

    # Checked before the copy, so a file handed over twice lands once.
    if origin in _LANDS_ONCE and (known := await _already_here(checked, ctx)) is not None:
        log.info("capture.already_here", asset_id=known.asset_id, origin=str(origin))
        return known

    rel_path = await asyncio.to_thread(
        _copy_into_folder, path, destination.root, destination.rel_dir
    )
    # Stamped from the copy, or the first rescan would gate and digest every import again.
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
        ctx.arrived(1)
        # While the bytes are still local: the one affordable chance to read the whole file. Only
        # for new bytes.
        await landing.landed(
            path, ingested.asset.identity, settings=settings, root_id=destination.root.id
        )
        await _kept_for_the_passes(ctx, path, copied, ingested.asset, settings)
        await ctx.enqueue_child(PROBE, {"asset_id": ingested.asset.id})
        # So a new file can be found by its name before anything rebuilds the index.
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


#: A video up to this size landed on a share is kept local too, as the take-in keeps one.
KEEP_VIDEOS_UP_TO = 64 * 1024**2


async def _kept_for_the_passes(
    ctx: JobContext, source: Path, landed_at: Path, asset: Asset, settings: Settings
) -> None:
    """Bytes landed on a share stay readable here: the probe and the passes read the cache's copy,
    never the share. Best effort: without it they read the share, as they always could."""
    if not lanes.storage_for(landed_at).remote:
        return
    if asset.media_type != Kind.IMAGE and (asset.size_bytes or 0) > KEEP_VIDEOS_UP_TO:
        return
    incoming = settings.cache_dir / "incoming"
    try:
        await asyncio.to_thread(incoming.mkdir, parents=True, exist_ok=True)
        scratch = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="landed-", dir=incoming))
    except OSError:
        return
    try:
        copy = scratch / landed_at.name
        await asyncio.to_thread(shutil.copyfile, source, copy)
        await ctx.content.keep_local_copy(asset.id, copy)
    except OSError as error:
        log.warning("capture.local_copy_not_kept", asset_id=asset.id, reason=str(error))
    finally:
        await asyncio.to_thread(shutil.rmtree, scratch, True)


async def _already_here(checked: IngressResult, ctx: JobContext) -> ImportOutcome | None:
    """The present place these bytes already sit in the library, by identity, or None to land them."""
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


# Untrusted names are rebuilt from safe characters: no separators, no control bytes.
_UNSAFE_IN_NAME = re.compile(r"[^A-Za-z0-9._ ()\-]")
_MAX_NAME = 128
_LONGEST_EXTENSION = 10


def safe_name(name: str) -> str:
    """A filename with no path in it; a long name is cut in its stem so the extension survives."""
    cleaned = _UNSAFE_IN_NAME.sub("_", name).strip().lstrip(".")
    if len(cleaned) > _MAX_NAME:
        stem, dot, extension = cleaned.rpartition(".")
        if dot and stem and 0 < len(extension) <= _LONGEST_EXTENSION:
            cleaned = f"{stem[: _MAX_NAME - len(extension) - 1]}.{extension}"
        else:
            cleaned = cleaned[:_MAX_NAME]
    return cleaned or "file"


SCREENSHOT_MARK = "-ss"


def screenshot_name(of: str, taken: str) -> str:
    """A screenshot's name: `<stem>-ss.<extension>`, the extension being the picture's own."""
    stem = of.rpartition(".")[0] or of
    extension = taken.rpartition(".")[2] if "." in taken else "png"
    return safe_name(f"{stem}{SCREENSHOT_MARK}.{extension}")


def _copy_into_folder(source: Path, root: Root, rel_dir: str) -> str:
    """Copy verified bytes into a root's folder without overwriting anything; return the relative
    path."""
    root_path = Path(root.abs_path)

    # Checked first: mkdir(parents=True) on a vanished root would try to recreate the library.
    if not root_path.is_dir():
        raise NoDestination(
            f'Sift cannot see the folder "{root.name}" any more, so there is nowhere to put this. '
            "Check the drive it is on is still attached, then try again."
        )

    if shutil.disk_usage(root_path).free < source.stat().st_size + ROOM_TO_SPARE:
        raise WaitingForSpace(_no_room(root.name))

    base = root_path / rel_dir if rel_dir else root_path
    base.mkdir(parents=True, exist_ok=True)

    # A symlinked subfolder could point outside the root; the lexical check below cannot see that.
    try:
        confine(root_path, base)
    except PathEscape as exc:
        raise ValueError("the destination folder resolves outside its library root") from exc

    target = _claim_unique_path(base, safe_name(source.name))
    try:
        # copyfile, not copy2: a network share can refuse the chmod after the bytes are written.
        shutil.copyfile(source, target)
    except OSError as error:
        # This path was created by O_EXCL above, so it holds only our half-written bytes.
        target.unlink(missing_ok=True)  # nosemgrep: sift-no-file-removal-outside-delete-trash
        if is_disk_full(error):
            raise WaitingForSpace(_no_room(root.name)) from error
        raise
    return target.relative_to(root_path).as_posix()


#: Room left on a library's disk after a file lands, so the disk is never filled to the last byte.
ROOM_TO_SPARE = 256 * 1024**2


def _no_room(folder: str) -> str:
    return (
        f'Waiting for space: the disk the folder "{folder}" is on has no room for this file. '
        "Free some space and it carries on; the file is kept until then."
    )


def _claim_unique_path(directory: Path, name: str) -> Path:
    """Reserve a free path for `name` atomically (O_EXCL), numbering to avoid a clash."""
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
