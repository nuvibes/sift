# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one import path, exercised for every kind of thing it takes in.

These run against a real library on a real disk, with the real ingress gate. A refused file is
refused because its bytes are wrong, not because a test said so; a duplicate is a duplicate because
the digest matches. That is the whole point of the slice, so none of it is faked.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

# The ledger writes a decision into the workbench's own table: imported for the registration, so
# this file passes on its own and not only beside another that imports it.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, FolderRow, Root
from sift.kernel.db import Database
from sift.kernel.destination import resolve_destination
from sift.kernel.ingress import IngressRejected, NoDestination, Origin
from sift.kernel.jobs import JobContext, JobQueue
from sift.kernel.ledger import Actor
from sift.slices.capture.pipeline import (
    ImportOutcome,
    import_file,
    safe_name,
)
from sift.testing.tools import POSIX_ONLY, WINDOWS_ONLY, junction

from .conftest import VIDEO_SECONDS, RecordingReindexer, corpus_file, draw

pytestmark = [pytest.mark.integration]

Context = Callable[[str, dict[str, object]], Awaitable[JobContext]]


# --- the headline: the gate holds on every path -------------------------------------------------


@pytest.mark.parametrize("origin", [Origin.DROP, Origin.PASTE, Origin.UPLOAD])
async def test_a_disguised_file_is_refused_and_quarantined_on_every_origin(
    origin: Origin,
    context_for: Context,
    settings: Settings,
    content_store: ContentStore,
    root: Root,
    tmp_path: Path,
    reindexer: RecordingReindexer,
    default_folder: str,
) -> None:
    """An executable named `.mp4`, dropped and pasted and uploaded, is quarantined every time.

    Not hashed, not indexed, not left where a later step could pick it up: the gate runs first, and
    a file that fails it never reaches the copy, the digest or the queue. Proving it on each origin
    is the point: one convenient path passing is not the same as the gate being the only way in.
    """
    staged = corpus_file(tmp_path / "staging" / "disguised_exe.mp4", "disguised_exe.mp4")
    ctx = await context_for("import", {"staging_id": "x"})

    with pytest.raises(IngressRejected):
        await import_file(
            path=staged,
            origin=origin,
            dest_folder_id=default_folder,
            ctx=ctx,
            settings=settings,
            reindexer=reindexer,
        )

    # It was moved out of the way, because Sift wrote it.
    assert not staged.exists()
    assert list(settings.quarantine_dir.iterdir())
    # Nothing was recorded and nothing was queued: no asset, no location, no probe.
    assert await content_store.location_at(root.id, "disguised_exe.mp4") is None
    assert await ctx.queue.children(ctx.job.id) == []


# --- identical bytes are one asset in two places ------------------------------------------------


async def test_the_location_row_describes_the_copy_so_the_first_rescan_does_not_read_it_again(
    context_for: Context,
    settings: Settings,
    content_store: ContentStore,
    root: Root,
    tmp_path: Path,
    reindexer: RecordingReindexer,
    default_folder: str,
) -> None:
    """The row carries the library copy's age, not the staged source's. Otherwise the first rescan
    finds a file whose size matches and whose age does not, and gates and digests every imported
    file a second time."""
    import os
    import time

    staged = draw(tmp_path / "staging" / "clip.mp4", "testsrc2=size=64x64:rate=5", VIDEO_SECONDS)
    long_ago = time.time() - 400 * 24 * 3600
    os.utime(staged, (long_ago, long_ago))
    ctx = await context_for("import", {"staging_id": "x"})

    outcome = await import_file(
        path=staged,
        origin=Origin.DROP,
        dest_folder_id=default_folder,
        ctx=ctx,
        settings=settings,
        reindexer=reindexer,
    )

    location = await content_store.location(outcome.location_id)
    assert location is not None
    copied = Path(root.abs_path) / location.rel_path
    assert location.mtime == int(copied.stat().st_mtime)
    assert location.mtime != int(long_ago), "the row is about the copy, not the staged source"


async def _twice(
    origin: Origin,
    context_for: Context,
    settings: Settings,
    tmp_path: Path,
    reindexer: RecordingReindexer,
    default_folder: str,
) -> tuple[ImportOutcome, ImportOutcome, JobContext, JobContext]:
    """The same bytes handed over twice, under the same name, from one origin."""
    original = draw(
        tmp_path / "staging" / "a" / "clip.mp4", "testsrc2=size=64x64:rate=5", VIDEO_SECONDS
    )
    twin = tmp_path / "staging" / "b" / "clip.mp4"
    twin.parent.mkdir(parents=True)
    twin.write_bytes(original.read_bytes())
    first_ctx = await context_for("import", {"staging_id": "a"})
    first = await import_file(
        path=original,
        origin=origin,
        dest_folder_id=default_folder,
        ctx=first_ctx,
        settings=settings,
        reindexer=reindexer,
    )
    second_ctx = await context_for("import", {"staging_id": "b"})
    second = await import_file(
        path=twin,
        origin=origin,
        dest_folder_id=default_folder,
        ctx=second_ctx,
        settings=settings,
        reindexer=reindexer,
    )
    return first, second, first_ctx, second_ctx


@pytest.mark.parametrize("origin", [Origin.DROP, Origin.PASTE, Origin.UPLOAD])
async def test_a_file_handed_over_twice_lands_once_and_says_where_it_is(
    origin: Origin,
    context_for: Context,
    settings: Settings,
    content_store: ContentStore,
    root: Root,
    tmp_path: Path,
    reindexer: RecordingReindexer,
    default_folder: str,
) -> None:
    """A person dropping a file Sift already has meant "add this", and a second copy in their folder
    is a twin they would then have to find. The identity is asked BEFORE the copy: nothing lands,
    nothing is queued, and the answer names the folder it is already in."""
    first, second, first_ctx, second_ctx = await _twice(
        origin, context_for, settings, tmp_path, reindexer, default_folder
    )

    assert first.was_duplicate is False
    assert first.already_at is None
    assert second.was_duplicate is True
    assert second.asset_id == first.asset_id
    assert second.location_id == first.location_id
    assert second.already_at == root.name

    # One place, one file on the disk: no `clip-1.mp4` beside it.
    locations = await content_store.locations(first.asset_id)
    assert [location.rel_path for location in locations] == ["clip.mp4"]
    assert not (Path(root.abs_path) / "clip-1.mp4").exists()
    assert len(await first_ctx.queue.children(first_ctx.job.id)) == 1
    assert await second_ctx.queue.children(second_ctx.job.id) == []
    assert reindexer.told == [first.asset_id]


async def test_a_download_of_bytes_already_here_still_records_a_second_place(
    context_for: Context,
    settings: Settings,
    content_store: ContentStore,
    root: Root,
    tmp_path: Path,
    reindexer: RecordingReindexer,
    default_folder: str,
) -> None:
    """A download keeps its own rule and its own sentence about a duplicate: it records a second
    place the bytes sit, never a twin asset."""
    first, second, first_ctx, second_ctx = await _twice(
        Origin.DOWNLOAD, context_for, settings, tmp_path, reindexer, default_folder
    )

    assert second.was_duplicate is True
    assert second.already_at is None
    assert second.asset_id == first.asset_id
    locations = await content_store.locations(first.asset_id)
    # The name clash on disk is resolved by numbering, not by overwriting the first copy.
    assert {location.rel_path for location in locations} == {"clip.mp4", "clip-1.mp4"}
    assert len(await first_ctx.queue.children(first_ctx.job.id)) == 1
    assert await second_ctx.queue.children(second_ctx.job.id) == []
    assert reindexer.told == [first.asset_id]


async def test_bytes_whose_every_copy_is_missing_land_again(
    context_for: Context,
    settings: Settings,
    content_store: ContentStore,
    root: Root,
    tmp_path: Path,
    reindexer: RecordingReindexer,
    default_folder: str,
) -> None:
    """A file whose only copy has gone is not here: dropping it again is how it is put back."""
    original = draw(
        tmp_path / "staging" / "a" / "clip.mp4", "testsrc2=size=64x64:rate=5", VIDEO_SECONDS
    )
    kept = original.read_bytes()
    first = await import_file(
        path=original,
        origin=Origin.DROP,
        dest_folder_id=default_folder,
        ctx=await context_for("import", {"staging_id": "a"}),
        settings=settings,
        reindexer=reindexer,
    )
    await content_store.mark_missing(first.location_id)
    again = tmp_path / "staging" / "b" / "clip.mp4"
    again.parent.mkdir(parents=True)
    again.write_bytes(kept)

    second = await import_file(
        path=again,
        origin=Origin.DROP,
        dest_folder_id=default_folder,
        ctx=await context_for("import", {"staging_id": "b"}),
        settings=settings,
        reindexer=reindexer,
    )

    assert second.already_at is None
    assert second.location_id != first.location_id


# --- the drop target sets the destination -------------------------------------------------------


async def test_a_named_folder_is_where_the_file_lands(
    context_for: Context,
    settings: Settings,
    content_store: ContentStore,
    root: Root,
    subfolder: FolderRow,
    root_path: Path,
    tmp_path: Path,
    reindexer: RecordingReindexer,
) -> None:
    """Dropping onto a folder puts the file in that folder, on disk and in the row."""
    staged = draw(tmp_path / "staging" / "clip.mp4", "testsrc2=size=64x64:rate=5", VIDEO_SECONDS)
    ctx = await context_for("import", {"staging_id": "x"})

    outcome = await import_file(
        path=staged,
        origin=Origin.DROP,
        dest_folder_id=subfolder.id,
        ctx=ctx,
        settings=settings,
        reindexer=reindexer,
    )

    locations = await content_store.locations(outcome.asset_id)
    assert locations[0].folder_id == subfolder.id
    assert locations[0].rel_path == "Vacations/clip.mp4"
    assert (root_path / "Vacations" / "clip.mp4").is_file()


async def test_an_imported_file_is_findable_by_name_through_the_real_index(
    context_for: Context,
    settings: Settings,
    temp_db: Database,
    job_queue: JobQueue,
    root: Root,
    tmp_path: Path,
    default_folder: str,
) -> None:
    """The import's end of the path, with the real index rather than the recorder above it.

    The recorder proves what the import SAYS, which is this slice's half. It would go on passing if
    the thing on the other end could not accept the call, and indexing opens a write of its own,
    just after the one that recorded the asset, so a call placed a level too deep would hang rather
    than fail. This runs the real one and then asks the index.

    A slice never imports another slice; a test may.
    """
    from sift.slices.search import Reindexer

    staged = draw(tmp_path / "staging" / "holiday.mp4", "testsrc2=size=64x64:rate=5", VIDEO_SECONDS)
    ctx = await context_for("import", {"staging_id": "x"})

    outcome = await import_file(
        path=staged,
        origin=Origin.DROP,
        dest_folder_id=default_folder,
        ctx=ctx,
        settings=settings,
        reindexer=Reindexer(database=temp_db, queue=job_queue),
    )

    indexed = await temp_db.fetch_all("SELECT asset_id, filename FROM assets_fts")
    assert [(str(row["asset_id"]), str(row["filename"])) for row in indexed] == [
        (outcome.asset_id, "holiday.mp4")
    ]


async def test_with_no_folder_named_the_file_lands_in_the_default_download_folder(
    context_for: Context,
    settings: Settings,
    content_store: ContentStore,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    reindexer: RecordingReindexer,
    default_folder: str,
) -> None:
    staged = draw(tmp_path / "staging" / "clip.mp4", "testsrc2=size=64x64:rate=5", VIDEO_SECONDS)
    ctx = await context_for("import", {"staging_id": "x"})

    outcome = await import_file(
        path=staged,
        origin=Origin.DROP,
        dest_folder_id=default_folder,
        ctx=ctx,
        settings=settings,
        reindexer=reindexer,
    )

    locations = await content_store.locations(outcome.asset_id)
    assert locations[0].rel_path == "clip.mp4"
    assert (root_path / "clip.mp4").is_file()


@POSIX_ONLY
def test_a_destination_folder_that_leaves_the_root_is_refused(
    root: Root, root_path: Path, tmp_path: Path
) -> None:
    """A subfolder that is a symlink pointing outside the root is a lexically clean rel_dir (no `..`
    in it) and copying into it would write the file outside the library. The resolved destination
    is confirmed under the root, so the escape is refused and nothing is written outside."""
    from sift.slices.capture.pipeline import _copy_into_folder

    outside = tmp_path / "outside"
    outside.mkdir()
    (root_path / "escape").symlink_to(outside)  # a folder that leaves the root

    source = tmp_path / "src.bin"
    source.write_bytes(b"data")

    with pytest.raises(ValueError, match="outside its library root"):
        _copy_into_folder(source, root, "escape")


@WINDOWS_ONLY
def test_a_destination_junction_that_leaves_the_root_is_refused(
    root: Root, root_path: Path, tmp_path: Path
) -> None:
    """The same refusal by the redirection this site actually offers, and the sharper case.

    A junction is a directory and is not a link, so a folder name that leads out of the library is
    an ordinary-looking `rel_dir` with nothing in the text to catch. Copying into it would write
    somebody's file outside their library.
    """
    from sift.slices.capture.pipeline import _copy_into_folder

    outside = tmp_path / "outside"
    outside.mkdir()
    junction(root_path / "escape", outside)

    source = tmp_path / "src.bin"
    source.write_bytes(b"data")

    with pytest.raises(ValueError, match="outside its library root"):
        _copy_into_folder(source, root, "escape")
    assert list(outside.iterdir()) == [], "nothing was written outside the root"
    assert not (outside / "src.bin").exists()  # nothing was written outside the root


def test_a_destination_that_refuses_a_chmod_still_takes_the_file(
    root: Root, root_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder on a NAS is a real place people keep their library, and some refuse a chmod.

    Copying the permissions after the bytes would raise "Operation not permitted" there, fail the
    whole job after the file was written, and leave a numbered copy behind per attempt: "the link
    failed, but it downloaded anyway, and now there are three of them". Copying the metadata buys
    nothing: the destination is created by Sift a line earlier with the mode it should have.
    """
    from sift.slices.capture.pipeline import _copy_into_folder

    def refuses(*args: object, **kwargs: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    # Anything reaching for the source's permissions or timestamps is what the share refuses.
    monkeypatch.setattr("os.chmod", refuses)
    monkeypatch.setattr("os.utime", refuses)

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"the bytes")

    landed = _copy_into_folder(source, root, "")

    assert (root_path / landed).read_bytes() == b"the bytes"


def test_a_copy_that_fails_does_not_leave_the_name_it_claimed(
    root: Root, root_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: one failure must not become several files.

    The name is claimed by CREATING the file, so a copy that then fails leaves an empty one in
    somebody's folder, and the next attempt does not reuse it, it numbers around it. Three tries,
    three files, none of which anybody asked for.
    """
    from sift.slices.capture import pipeline

    def refuses(*args: object, **kwargs: object) -> None:
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr("shutil.copyfile", refuses)

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"the bytes")

    with pytest.raises(OSError, match="not permitted"):
        pipeline._copy_into_folder(source, root, "")

    assert list(root_path.iterdir()) == [], "the failed copy left its claimed name behind"


# --- the bytes are copied in, and the source is left alone --------------------------------------


async def test_the_bytes_are_copied_into_the_root_and_the_source_is_untouched(
    context_for: Context,
    settings: Settings,
    root: Root,
    root_path: Path,
    tmp_path: Path,
    reindexer: RecordingReindexer,
    default_folder: str,
) -> None:
    """A dropped file cannot be indexed in place (its real path is not knowable) so it is copied
    into the root, byte-for-byte, and the file it came from is not moved or changed."""
    staged = draw(tmp_path / "staging" / "clip.mp4", "testsrc2=size=64x64:rate=5", VIDEO_SECONDS)
    before = staged.read_bytes()
    ctx = await context_for("import", {"staging_id": "x"})

    await import_file(
        path=staged,
        origin=Origin.DROP,
        dest_folder_id=default_folder,
        ctx=ctx,
        settings=settings,
        reindexer=reindexer,
    )

    assert staged.exists()
    assert staged.read_bytes() == before
    assert (root_path / "clip.mp4").read_bytes() == before


@pytest.mark.parametrize("origin", [Origin.DOWNLOAD, Origin.UPLOAD, Origin.SWAP])
async def test_a_file_lands_without_its_location_and_the_staged_file_is_untouched(
    origin: Origin,
    context_for: Context,
    settings: Settings,
    root_path: Path,
    tmp_path: Path,
    reindexer: RecordingReindexer,
    default_folder: str,
) -> None:
    """Every way in lands a copy with no GPS directory: the library never holds a place Sift
    wrote. The staged bytes are read, not changed, and the door's copy is gone afterwards."""
    staging = tmp_path / "staging"
    staging.mkdir()
    staged = staging / "trip.jpg"
    exif = Image.Exif()
    exif[0x0110] = "Model Q"
    exif.get_ifd(0x8825)[2] = (48.0, 51.0, 29.17)
    Image.new("RGB", (32, 24), (40, 90, 200)).save(staged, exif=exif)
    before = staged.read_bytes()
    ctx = await context_for("import", {"staging_id": "x"})

    await import_file(
        path=staged,
        origin=origin,
        dest_folder_id=default_folder,
        ctx=ctx,
        settings=settings,
        reindexer=reindexer,
    )

    landed = root_path / "trip.jpg"
    with Image.open(landed) as opened:
        assert 0x8825 not in opened.getexif()
        assert opened.getexif()[0x0110] == "Model Q"
    assert len(landed.read_bytes()) == len(before)
    assert staged.read_bytes() == before
    assert sorted(one.name for one in staging.iterdir()) == ["trip.jpg"]


# --- resolving the destination ------------------------------------------------------------------


async def test_resolve_destination_uses_a_named_folder(
    library_store: object, root: Root, subfolder: FolderRow
) -> None:
    destination = await resolve_destination(library_store, subfolder.id)  # type: ignore[arg-type]
    assert destination.folder_id == subfolder.id
    assert destination.rel_dir == "Vacations"
    assert destination.root.id == root.id


async def test_resolve_destination_refuses_a_folder_that_is_gone(library_store: object) -> None:
    with pytest.raises(NoDestination):
        await resolve_destination(library_store, "01HX0000000000000000000000")  # type: ignore[arg-type]


async def test_resolve_destination_refuses_a_folder_whose_root_is_gone(
    library_store: object, root: Root, subfolder: FolderRow
) -> None:
    await library_store.delete_root(root.id, actor=Actor.sift("folder"))  # type: ignore[attr-defined]
    with pytest.raises(NoDestination):
        await resolve_destination(library_store, subfolder.id)  # type: ignore[arg-type]


async def test_resolve_destination_refuses_when_the_library_goes_between_the_two_reads(
    library_store: object, subfolder: FolderRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The folder is found and its library is not, which is a race rather than a state.

    It cannot be arranged by deleting the library: a folder row does not outlive one (the
    reference cascades) so removing it takes the folder too and the check above answers first.
    What this guards is the gap between the two reads, which are deliberately not one transaction.
    Somebody removing a library at that moment leaves a folder in hand and nowhere to put the file,
    and the file must be refused rather than written against a root that is gone.
    """

    async def gone(_root_id: str) -> None:
        return None

    monkeypatch.setattr(library_store, "get_root", gone)

    with pytest.raises(NoDestination, match="isn't there any more"):
        await resolve_destination(library_store, subfolder.id)  # type: ignore[arg-type]


async def test_resolve_destination_takes_the_folder_it_is_handed(
    library_store: object, root: Root, default_folder: str
) -> None:
    """The library's own top folder is a destination like any other, named rather than guessed."""
    destination = await resolve_destination(library_store, default_folder)  # type: ignore[arg-type]
    assert destination.root.id == root.id
    assert destination.rel_dir == ""


async def test_resolve_destination_refuses_when_nothing_was_named(
    library_store: object, tmp_path: Path
) -> None:
    """Handed nothing, this refuses rather than guessing.

    The default destination has one stored answer, so this does not hunt for one of its own.
    Whoever calls this resolves the default and hands over a folder.
    """
    await library_store.create_root(  # type: ignore[attr-defined]
        name="A library", abs_path=_a_dir(tmp_path)
    )
    with pytest.raises(NoDestination):
        await resolve_destination(library_store, None)  # type: ignore[arg-type]


def _a_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "other-library"
    directory.mkdir()
    return directory


# --- the filename is only ever a filename -------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("holiday video.mp4", "holiday video.mp4"),
        ("clip (1).mov", "clip (1).mov"),
        ("../../etc/passwd", "_.._etc_passwd"),
        ("a\\b\\c.png", "a_b_c.png"),
        ("\x00danger.gif", "_danger.gif"),
        ("...", "file"),
        ("", "file"),
    ],
)
def test_safe_name_keeps_only_a_plain_filename(raw: str, expected: str) -> None:
    assert safe_name(raw) == expected


def test_a_long_name_is_cut_at_the_stem_and_keeps_its_extension() -> None:
    """A cut at the end would leave `.jp`, `.` or nothing. The stem gives; the extension stays
    whole; a dot mid-title is not an extension."""
    long = "a" * 200 + ".jpeg"
    cut = safe_name(long)
    assert cut.endswith(".jpeg") and len(cut) == 128
    assert safe_name("b" * 130 + ". and then a whole sentence") == "b" * 128
    assert safe_name("short.mp4") == "short.mp4"


def test_a_library_that_is_not_there_is_refused_by_name(
    root: Root, root_path: Path, tmp_path: Path
) -> None:
    """A disk unplugged, a share whose server went, a container started without the mount.

    Left to `mkdir(parents=True)`, a missing library is not a refusal at all: it is an attempt to
    CREATE the library, component by component, on whatever filesystem sits underneath. What comes
    back is an errno such as "Read-only file system", naming a path nobody had chosen and saying
    nothing about the folder they had, once per attempt.

    So the refusal has to name the folder the person picked and say what to check, and nothing may
    be created on the way to finding out.
    """
    from sift.slices.capture.pipeline import _copy_into_folder

    source = tmp_path / "src.bin"
    source.write_bytes(b"data")
    carried_off = tmp_path / "carried-off"
    root_path.rename(carried_off)

    with pytest.raises(NoDestination) as refusal:
        _copy_into_folder(source, root, "")

    assert root.name in str(refusal.value)
    assert "attached" in str(refusal.value)
    assert not root_path.exists(), "the missing library was recreated instead of being reported"


def test_a_library_disk_without_room_holds_the_file_and_writes_nothing(
    root: Root, root_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file waits for room with its attempt handed back; nothing half-written is left."""
    import shutil
    from collections import namedtuple

    from sift.kernel.jobs import held_for
    from sift.kernel.jobs.retrying import ROOM_WAIT, WaitingForSpace
    from sift.slices.capture import pipeline

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"the bytes")
    usage = namedtuple("usage", "total used free")
    room = [pipeline.ROOM_TO_SPARE + 8]
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage(0, 0, room[0]))

    with pytest.raises(WaitingForSpace, match=f'"{root.name}"') as raised:
        pipeline._copy_into_folder(source, root, "")
    assert held_for(raised.value) == ROOM_WAIT
    assert list(root_path.iterdir()) == []

    room[0] = 10**12

    def full(*_args: object, **_kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil, "copyfile", full)
    with pytest.raises(WaitingForSpace):
        pipeline._copy_into_folder(source, root, "")
    assert list(root_path.iterdir()) == []


@pytest.mark.parametrize("remote", [True, False])
async def test_a_file_landed_on_a_share_keeps_its_local_bytes_for_the_passes(
    remote: bool,
    context_for: Context,
    settings: Settings,
    content_store: ContentStore,
    tmp_path: Path,
    reindexer: RecordingReindexer,
    default_folder: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe and every pass after it read the cache's copy, not the share it landed on."""
    from types import SimpleNamespace

    from sift.kernel import lanes
    from sift.kernel.media_sources import resolve

    monkeypatch.setattr(lanes, "storage_for", lambda _path: SimpleNamespace(remote=remote))
    source = draw(tmp_path / "staging" / "a" / "clip.mp4", "testsrc2=size=64x64:rate=5", 1)
    landed = await import_file(
        path=source,
        origin=Origin.DOWNLOAD,
        dest_folder_id=default_folder,
        ctx=await context_for("import", {"staging_id": "a"}),
        settings=settings,
        reindexer=reindexer,
    )

    read = await resolve(content_store, landed.asset_id)
    assert (settings.cache_dir in read.path.parents) is remote
    assert read.path.read_bytes() == source.read_bytes()
    assert source.is_file(), "the caller's own file is left where it was"


class _Keeper:
    """The content store's one door the share's local copy uses, recording what it was handed."""

    def __init__(self) -> None:
        self.kept: list[bytes] = []

    async def keep_local_copy(self, _asset_id: str, copy: Path) -> None:
        self.kept.append(copy.read_bytes())


def _on_a_share(monkeypatch: pytest.MonkeyPatch) -> _Keeper:
    from types import SimpleNamespace

    from sift.kernel import lanes

    monkeypatch.setattr(lanes, "storage_for", lambda _path: SimpleNamespace(remote=True))
    return _Keeper()


def _landed(kind: str, size: int) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(id="a1", media_type=kind, size_bytes=size)


@pytest.mark.parametrize(("kind", "kept"), [("video", False), ("image", True)])
async def test_a_large_video_on_a_share_is_read_there_and_a_large_picture_is_kept(
    kind: str, kept: bool, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from sift.slices.capture import pipeline

    keeper = _on_a_share(monkeypatch)
    source = tmp_path / "big.bin"
    source.write_bytes(b"bytes")
    landed = _landed(kind, pipeline.KEEP_VIDEOS_UP_TO + 1)
    ctx: Any = SimpleNamespace(content=keeper)
    await pipeline._kept_for_the_passes(
        ctx, source, tmp_path / "share" / "big.bin", landed, settings
    )
    assert keeper.kept == ([b"bytes"] if kept else [])


async def test_a_cache_with_no_room_for_its_folder_leaves_the_share_read(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from sift.slices.capture import pipeline

    keeper = _on_a_share(monkeypatch)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    (settings.cache_dir / "incoming").write_bytes(b"")
    source = tmp_path / "a.jpg"
    source.write_bytes(b"bytes")
    ctx: Any = SimpleNamespace(content=keeper)
    await pipeline._kept_for_the_passes(
        ctx, source, tmp_path / "a.jpg", _landed("image", 5), settings
    )
    assert keeper.kept == []


async def test_a_copy_that_fails_keeps_nothing_and_leaves_no_scratch(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from sift.slices.capture import pipeline

    keeper = _on_a_share(monkeypatch)
    ctx: Any = SimpleNamespace(content=keeper)
    gone = tmp_path / "gone.jpg"
    await pipeline._kept_for_the_passes(ctx, gone, gone, _landed("image", 5), settings)
    assert keeper.kept == []
    assert list((settings.cache_dir / "incoming").iterdir()) == []
