# SPDX-License-Identifier: AGPL-3.0-or-later
"""What actually happens on disk, and what the library knows about it afterwards.

These run the real write seam over a real filesystem. ffmpeg is stood in for by something that
writes a known number of bytes, because what is under test here is where the file ends up and what
is recorded about it, not whether x264 works.

The most important test in this file is the one that asserts nothing was overwritten. It is worth
saying why it is phrased the way it is: rather than checking that a particular guard fired, it
checks the property the whole design exists to give: the original's bytes, and the digest that
identifies it, are the same afterwards as before.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from sift.kernel.access import Repository, Viewer
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore
from sift.kernel.db import Database
from sift.kernel.jobs import Job, JobContext, JobQueue, SystemCapabilities
from sift.kernel.library_write import LibraryWriteRefused
from sift.slices.media_edit import jobs as job_module
from sift.slices.media_edit import plan, tuning
from sift.slices.media_edit.jobs import compress, compress_sample
from sift.slices.media_edit.refusals import ProductionFailed
from sift.slices.media_edit.service import PRODUCED_TAG, CompressService
from sift.slices.media_edit.tests.conftest import FakeDuplicates, FakeReindexer, Library
from sift.slices.organize.service import WORKING_SUFFIX, Organizer

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("stub_handlers")]

CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


class FakeFfmpeg:
    """Stands in for the encoder: writes real bytes at the destination the arguments name.

    It reads the destination out of the argument list rather than being told, which means the
    argument builders are exercised for real: a builder that stopped putting the destination last
    would break these tests, which is the correct outcome.
    """

    def __init__(self, source: Path, sizes: list[int] | None = None) -> None:
        self.source = source
        self.sizes = sizes or []
        self.calls: list[list[str]] = []

    async def run(self, argv: list[str], **_: Any) -> bytes:
        self.calls.append(argv)
        destination = Path(argv[-1])
        # Real, gate-passing bytes, padded to whatever size this call is supposed to produce. The
        # padding rides in the container's tail, which the gate does not read.
        body = self.source.read_bytes()
        wanted = self.sizes[min(len(self.calls) - 1, len(self.sizes) - 1)] if self.sizes else 0
        # Always different from the source, and different on each call. A copy that came out
        # byte-identical to its original is the same asset in two places: a real case, handled
        # deliberately elsewhere, and not the one most of these tests are about.
        padding = max(wanted - len(body), 32 * len(self.calls))
        destination.write_bytes(body + b"\0" * padding)
        return b""


@pytest.fixture
def ffmpeg(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeFfmpeg]:
    def install(source: Path, sizes: list[int] | None = None) -> FakeFfmpeg:
        fake = FakeFfmpeg(source, sizes)
        monkeypatch.setattr(job_module, "run_tool", fake.run)
        return fake

    return install


@pytest.fixture
def duplicates() -> FakeDuplicates:
    return FakeDuplicates()


@pytest.fixture
def reindexer() -> FakeReindexer:
    return FakeReindexer()


async def claimed_job(
    queue: JobQueue,
    *,
    asset_id: str,
    actor_id: str,
    filename: str,
    target_bytes: int | None,
    compatibility: bool,
) -> Job:
    """A real queued job, really claimed.

    Hand-built `Job` objects are wrong here: the handler asks the queue
    whether it still owns the job (which is how cancelling one mid-encode works), and a row that
    was never written answers that it does not. So the row is real, and the cancellation path is
    exercised rather than stepped around.
    """
    job_id = await queue.enqueue(
        "compress",
        {
            "asset_id": asset_id,
            "actor_id": actor_id,
            "target_bytes": target_bytes,
            "compatibility": compatibility,
            "preset": "small",
            "filename": filename,
        },
    )
    claimed = await queue.claim("test")
    assert claimed is not None and claimed.id == job_id
    return claimed


def a_context(job: Job, *, content: ContentStore, queue: JobQueue) -> JobContext:
    return JobContext(
        job=job,
        worker_id="test",
        queue=queue,
        capabilities=SystemCapabilities(content=content, library=None),  # type: ignore[arg-type]
    )


async def run_compress(
    *,
    asset_id: str,
    filename: str,
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    target_bytes: int | None = 10 * 1024 * 1024,
    compatibility: bool = False,
) -> None:
    job = await claimed_job(
        job_queue,
        asset_id=asset_id,
        actor_id=admin.id,
        filename=filename,
        target_bytes=target_bytes,
        compatibility=compatibility,
    )
    await compress(
        a_context(job, content=content_store, queue=job_queue),
        settings=settings,
        access=access,
        writer=organizer,
        service=compressor,
        duplicates=duplicates,
        reindexer=reindexer,
        database=temp_db,
        follow_on=(),
    )


# --- the property the whole design exists to give ------------------------------------------------


async def test_the_original_is_untouched_by_a_compression(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """The required test, phrased as the property rather than as a guard that fired.

    Both halves matter. The BYTES being unchanged says nothing was written over; the DIGEST being
    unchanged says the library still recognizes the file as the same one, which is what carries
    its tags, its rating, the people on it and everything shared about it.
    """
    original = await add_file(managed, "clip.mp4")
    on_disk = managed.path / "clip.mp4"
    before = on_disk.read_bytes()
    digest_before = original.asset.identity

    ffmpeg(CORPUS / "accepted.mp4", sizes=[1000])
    await run_compress(
        asset_id=original.asset.id,
        filename="clip-10MB.mp4",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        compressor=compressor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
    )

    assert on_disk.read_bytes() == before
    unchanged = await content_store.get(original.asset.id)
    assert unchanged is not None
    assert unchanged.identity == digest_before


async def test_a_name_already_taken_is_refused_and_destroys_nothing(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """The required test. The file that was already there still has its own bytes afterwards."""
    original = await add_file(managed, "clip.mp4")
    occupied = managed.path / "clip-10MB.mp4"
    occupied.write_bytes(b"something somebody else put here")

    ffmpeg(CORPUS / "accepted.mp4", sizes=[1000])
    with pytest.raises(LibraryWriteRefused):
        await run_compress(
            asset_id=original.asset.id,
            filename="clip-10MB.mp4",
            admin=admin,
            settings=settings,
            access=access,
            organizer=organizer,
            compressor=compressor,
            duplicates=duplicates,
            reindexer=reindexer,
            temp_db=temp_db,
            content_store=content_store,
            job_queue=job_queue,
        )

    assert occupied.read_bytes() == b"something somebody else put here"


async def test_a_failed_encode_leaves_no_scratch_file_behind(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure must not leave half a video sitting in somebody's folder."""
    original = await add_file(managed, "clip.mp4")

    async def explode(argv: list[str], **_: Any) -> bytes:
        Path(argv[-1]).write_bytes(b"half a video")
        raise RuntimeError("the encoder fell over")

    monkeypatch.setattr(job_module, "run_tool", explode)
    with pytest.raises(RuntimeError):
        await run_compress(
            asset_id=original.asset.id,
            filename="clip-10MB.mp4",
            admin=admin,
            settings=settings,
            access=access,
            organizer=organizer,
            compressor=compressor,
            duplicates=duplicates,
            reindexer=reindexer,
            temp_db=temp_db,
            content_store=content_store,
            job_queue=job_queue,
        )

    leftovers = [each.name for each in managed.path.iterdir() if WORKING_SUFFIX in each.name]
    assert leftovers == []


async def test_a_read_only_folder_is_refused_before_anything_is_encoded(
    read_only: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """The required test, at the job rather than at the panel: the job's answer is the one that
    decides, and it is asked of the same function."""
    original = await add_file(read_only, "clip.mp4")
    fake = ffmpeg(CORPUS / "accepted.mp4", sizes=[1000])

    with pytest.raises(LibraryWriteRefused):
        await run_compress(
            asset_id=original.asset.id,
            filename="clip-10MB.mp4",
            admin=admin,
            settings=settings,
            access=access,
            organizer=organizer,
            compressor=compressor,
            duplicates=duplicates,
            reindexer=reindexer,
            temp_db=temp_db,
            content_store=content_store,
            job_queue=job_queue,
        )
    assert fake.calls == []


# --- the copy arrives, and arrives as a copy -----------------------------------------------------


async def test_the_copy_lands_beside_the_original_and_is_indexed(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    original = await add_file(managed, "clip.mp4")
    ffmpeg(CORPUS / "accepted.mp4", sizes=[1000])
    await run_compress(
        asset_id=original.asset.id,
        filename="clip-10MB.mp4",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        compressor=compressor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
    )

    assert (managed.path / "clip-10MB.mp4").is_file()
    located = await content_store.location_at(managed.root.id, "clip-10MB.mp4")
    assert located is not None


async def test_a_file_in_the_vault_can_be_compressed_at_all(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """A job has no session, so it rebuilds the user with the vault shut unless it says not to.

    Every other test here uses a file nobody has hidden, so all of them pass while the feature is
    unusable on the files somebody most wants a smaller copy of: the job resolves the asset through
    the user it is acting for, that user comes back with the vault locked, and a concealed
    file is not there. What comes out is not a refusal anybody can act on: it is "There is no such
    file" against a file they were looking at when they pressed the button.
    """
    original = await add_file(managed, "clip.mp4")
    await temp_db.execute(
        "INSERT INTO asset_user_state (asset_id, user_id, hidden, hidden_at, updated_at)"
        " VALUES (?, ?, 1, ?, ?)",
        (original.asset.id, admin.id, 1_700_000_000, 1_700_000_000),
    )
    assert await access.is_concealed(admin.id, original.asset.id), "it is not actually concealed"

    ffmpeg(CORPUS / "accepted.mp4", sizes=[1000])
    await run_compress(
        asset_id=original.asset.id,
        filename="clip-10MB.mp4",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        compressor=compressor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
    )

    assert (managed.path / "clip-10MB.mp4").is_file()
    located = await content_store.location_at(managed.root.id, "clip-10MB.mp4")
    assert located is not None
    # And it arrived concealed, which is the other half and the half nobody would notice.
    assert await access.is_concealed(admin.id, located.asset_id)


async def test_the_copy_is_tagged_and_says_what_it_came_from(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    original = await add_file(managed, "clip.mp4")
    ffmpeg(CORPUS / "accepted.mp4", sizes=[1000])
    await run_compress(
        asset_id=original.asset.id,
        filename="clip-10MB.mp4",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        compressor=compressor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
    )

    located = await content_store.location_at(managed.root.id, "clip-10MB.mp4")
    assert located is not None
    produced = await compressor.produced_for(located.asset_id, viewer=admin)
    assert produced is not None
    assert produced.source_asset_id == original.asset.id

    rows = await temp_db.fetch_all(
        "SELECT t.name, t.created_by_act FROM asset_tags at JOIN tags t ON t.id = at.tag_id"
        " WHERE at.asset_id = ?",
        (located.asset_id,),
    )
    # The tag says which act made it, so its Created by line wears the Compress glyph.
    assert [(str(row["name"]), row["created_by_act"]) for row in rows] == [
        (PRODUCED_TAG, "compress")
    ]


async def test_the_copy_and_its_original_are_settled_as_not_a_mistake(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """The required test. Mutate the recording away and forty copies become forty questions."""
    original = await add_file(managed, "clip.mp4")
    ffmpeg(CORPUS / "accepted.mp4", sizes=[1000])
    await run_compress(
        asset_id=original.asset.id,
        filename="clip-10MB.mp4",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        compressor=compressor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
    )
    located = await content_store.location_at(managed.root.id, "clip-10MB.mp4")
    assert located is not None
    assert duplicates.pairs == [(original.asset.id, located.asset_id)]
    assert reindexer.touched_ids == [located.asset_id]


# --- the scratch file the scan must not pick up ---------------------------------------------------


async def test_the_scratch_name_is_one_the_folder_scan_walks_past() -> None:
    """A half-written video carrying a real extension is one the scan tries to take in.

    Asserted against the walk's own filter rather than against a hand-written list, so this fails
    if either side changes and they stop agreeing.
    """
    from sift.kernel.ingress import ALLOWED_EXTENSIONS
    from sift.slices.organize.service import working_name

    scratch = Path(working_name("clip-10MB.mp4", "01HX0000000000000000000101"))
    assert scratch.suffix.lower() not in ALLOWED_EXTENSIONS


# --- the ladder, driven for real --------------------------------------------------------------


async def test_the_ladder_stops_at_the_best_fit_and_keeps_those_bytes(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The winner's bytes are what ends up on disk, even when it was not the last rung tried.

    The scratch file is one file and each attempt writes over the last, so a winner found two
    attempts ago has to be produced again. This is the test that catches the version that keeps
    whatever happened to be sitting there.
    """
    original = await add_file(managed, "clip.mp4")
    await _pretend_it_is_a_long_video(temp_db, original.asset.id)

    produced: list[int] = []
    body = (CORPUS / "accepted.mp4").read_bytes()

    async def encode(argv: list[str], **_: Any) -> bytes:
        crf = int(argv[argv.index("-crf") + 1])
        # Better quality is a bigger file. The first attempt overshoots so the ladder steps down,
        # and then steps back up when it finds room.
        size = {20: 900, 24: 600, 28: 300, 30: 120, 34: 60, 36: 40}[crf]
        produced.append(size)
        Path(argv[-1]).write_bytes(body + b"\0" * max(0, size * 1024 - len(body)))
        return b""

    monkeypatch.setattr(job_module, "run_tool", encode)
    await run_compress(
        asset_id=original.asset.id,
        filename="clip-10MB.mp4",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        compressor=compressor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
        target_bytes=400 * 1024,
    )

    landed = (managed.path / "clip-10MB.mp4").stat().st_size
    assert landed <= 400 * 1024
    # And it is the BEST one that fits, not merely one that does: 300 KB rather than 120 or 60.
    assert landed == 300 * 1024


async def test_a_container_change_alone_never_reaches_the_encoder(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """The required test. A rewrap has no -crf in it at all, which is what makes it lossless."""
    original = await add_file(managed, "clip.mkv", source="accepted.mkv")
    await temp_db.execute(
        "UPDATE assets SET vcodec = 'h264', acodec = 'aac', container = 'matroska',"
        " size_bytes = 1000, width = 1280, height = 720, duration_ms = 5000, fps = 25 WHERE id = ?",
        (original.asset.id,),
    )
    fake = ffmpeg(CORPUS / "accepted.mp4", sizes=[1000])

    await run_compress(
        asset_id=original.asset.id,
        filename="clip-compatible.mp4",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        compressor=compressor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
        target_bytes=None,
        compatibility=True,
    )

    assert len(fake.calls) == 1
    assert "-crf" not in fake.calls[0]
    assert fake.calls[0][fake.calls[0].index("-c:v") + 1] == "copy"


async def test_the_sound_is_copied_and_never_re_encoded_on_a_size_target(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """The required test, asserted on what ffmpeg is asked for.

    The whole file's sound arriving bit-identical follows from `-c:a copy` and from nothing else,
    and asserting the argument catches the change that would break it (a bitrate quietly added to
    buy room), where comparing two decoded streams would only catch it slowly.
    """
    original = await add_file(managed, "clip.mp4")
    await temp_db.execute(
        "UPDATE assets SET vcodec = 'hevc', acodec = 'vorbis', container = 'mp4' WHERE id = ?",
        (original.asset.id,),
    )
    fake = ffmpeg(CORPUS / "accepted.mp4", sizes=[1000])

    await run_compress(
        asset_id=original.asset.id,
        filename="clip-10MB.mp4",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        compressor=compressor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
        compatibility=False,
    )

    argv = fake.calls[0]
    assert argv[argv.index("-c:a") + 1] == "copy"


async def test_sound_the_container_cannot_carry_is_converted_only_for_compatibility(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """The one exception, and it only applies when compatibility was asked for."""
    original = await add_file(managed, "clip.mp4")
    await temp_db.execute(
        "UPDATE assets SET vcodec = 'hevc', acodec = 'vorbis', container = 'mp4' WHERE id = ?",
        (original.asset.id,),
    )
    fake = ffmpeg(CORPUS / "accepted.mp4", sizes=[1000])

    await run_compress(
        asset_id=original.asset.id,
        filename="clip-compatible.mp4",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        compressor=compressor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
        target_bytes=None,
        compatibility=True,
    )

    argv = fake.calls[0]
    assert argv[argv.index("-c:a") + 1] == tuning.FALLBACK_ACODEC


# --- the guards -----------------------------------------------------------------------------


async def test_no_room_on_the_disk_stops_it_before_it_starts(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filling somebody's media disk is worse than not compressing."""
    import shutil

    original = await add_file(managed, "clip.mp4")
    fake = ffmpeg(CORPUS / "accepted.mp4", sizes=[1000])

    class NoRoom:
        free = 1

    monkeypatch.setattr(shutil, "disk_usage", lambda _path: NoRoom)
    with pytest.raises(ProductionFailed, match="free space"):
        await run_compress(
            asset_id=original.asset.id,
            filename="clip-10MB.mp4",
            admin=admin,
            settings=settings,
            access=access,
            organizer=organizer,
            compressor=compressor,
            duplicates=duplicates,
            reindexer=reindexer,
            temp_db=temp_db,
            content_store=content_store,
            job_queue=job_queue,
        )
    assert fake.calls == []


async def test_an_encoder_that_writes_nothing_is_reported_as_such(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ffmpeg can exit 0 having produced no file."""
    original = await add_file(managed, "clip.mp4")

    async def writes_nothing(argv: list[str], **_: Any) -> bytes:
        return b""

    monkeypatch.setattr(job_module, "run_tool", writes_nothing)
    with pytest.raises(ProductionFailed, match="produced nothing"):
        await run_compress(
            asset_id=original.asset.id,
            filename="clip-10MB.mp4",
            admin=admin,
            settings=settings,
            access=access,
            organizer=organizer,
            compressor=compressor,
            duplicates=duplicates,
            reindexer=reindexer,
            temp_db=temp_db,
            content_store=content_store,
            job_queue=job_queue,
        )


async def test_an_account_that_has_gone_stops_the_job(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
) -> None:
    """A job carries who asked for it, and the write is made as them. If they are gone, it stops."""
    original = await add_file(managed, "clip.mp4")
    await temp_db.execute("DELETE FROM users WHERE id = ?", (admin.id,))
    with pytest.raises(ProductionFailed, match="no longer exists"):
        await run_compress(
            asset_id=original.asset.id,
            filename="clip-10MB.mp4",
            admin=admin,
            settings=settings,
            access=access,
            organizer=organizer,
            compressor=compressor,
            duplicates=duplicates,
            reindexer=reindexer,
            temp_db=temp_db,
            content_store=content_store,
            job_queue=job_queue,
        )


# --- helpers ------------------------------------------------------------------------------------


async def _pretend_it_is_a_long_video(database: Database, asset_id: str) -> None:
    await database.execute(
        "UPDATE assets SET width = 1920, height = 1080, duration_ms = 600000, fps = 30,"
        " size_bytes = 900000000, vcodec = 'hevc', acodec = 'aac', container = 'mp4'"
        " WHERE id = ?",
        (asset_id,),
    )


# --- the paths that only happen when something is unusual ----------------------------------------


async def test_an_overridden_impossible_target_keeps_the_smallest_and_says_so(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Somebody was told it would not fit and went ahead. They get the closest Sift can come.

    Not nothing, and not silence: the job carries a note saying what it actually managed, because
    a file that is four times the size asked for is not a failure and is not a success either.
    """
    original = await add_file(managed, "clip.mp4")
    await _pretend_it_is_a_long_video(temp_db, original.asset.id)
    body = (CORPUS / "accepted.mp4").read_bytes()

    async def always_too_big(argv: list[str], **_: Any) -> bytes:
        Path(argv[-1]).write_bytes(body + b"\0" * (5 * 1024 * 1024))
        return b""

    monkeypatch.setattr(job_module, "run_tool", always_too_big)
    await run_compress(
        asset_id=original.asset.id,
        filename="clip-1MB.mp4",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        compressor=compressor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
        target_bytes=1024 * 1024,
    )

    assert (managed.path / "clip-1MB.mp4").is_file()
    row = await temp_db.fetch_one(
        "SELECT note FROM jobs WHERE type = 'compress' ORDER BY id DESC LIMIT 1", ()
    )
    assert row is not None
    assert "Couldn't reach the target" in str(row["note"])


async def test_a_copy_identical_to_something_already_indexed_records_nothing(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identical bytes are one asset in two places, not a new file.

    Writing provenance and inheritance onto it would be this feature editing something it did not
    make: an asset that already has its own tags, its own rating and its own history.
    """
    original = await add_file(managed, "clip.mp4")
    body = (CORPUS / "accepted.mp4").read_bytes()

    async def produces_the_same_bytes(argv: list[str], **_: Any) -> bytes:
        Path(argv[-1]).write_bytes(body)
        return b""

    monkeypatch.setattr(job_module, "run_tool", produces_the_same_bytes)
    await run_compress(
        asset_id=original.asset.id,
        filename="clip-10MB.mp4",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        compressor=compressor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
    )

    assert duplicates.pairs == []
    assert reindexer.touched_ids == []
    assert await compressor.produced_for(original.asset.id, viewer=admin) is None


async def test_a_produced_file_that_fails_the_gate_is_reported(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file Sift wrote seconds ago still goes through the gate. This is what happens when it fails.

    The alternative (trusting it because Sift made it) is a second way into the library, and
    the second one is always the one that is subtly wrong.
    """
    original = await add_file(managed, "clip.mp4")

    async def produces_rubbish(argv: list[str], **_: Any) -> bytes:
        Path(argv[-1]).write_bytes(b"not media at all")
        return b""

    monkeypatch.setattr(job_module, "run_tool", produces_rubbish)
    with pytest.raises(ProductionFailed, match="own check"):
        await run_compress(
            asset_id=original.asset.id,
            filename="clip-10MB.mp4",
            admin=admin,
            settings=settings,
            access=access,
            organizer=organizer,
            compressor=compressor,
            duplicates=duplicates,
            reindexer=reindexer,
            temp_db=temp_db,
            content_store=content_store,
            job_queue=job_queue,
        )


async def test_the_follow_on_work_is_started_on_the_copy(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """Probing, which is what gives the copy a thumbnail and everything drawn from it.

    Named from outside rather than known in here: it is another feature's job type.
    """
    original = await add_file(managed, "clip.mp4")
    ffmpeg(CORPUS / "accepted.mp4", sizes=[1000])
    job = await claimed_job(
        job_queue,
        asset_id=original.asset.id,
        actor_id=admin.id,
        filename="clip-10MB.mp4",
        target_bytes=10 * 1024 * 1024,
        compatibility=False,
    )
    await compress(
        a_context(job, content=content_store, queue=job_queue),
        settings=settings,
        access=access,
        writer=organizer,
        service=compressor,
        duplicates=duplicates,
        reindexer=reindexer,
        database=temp_db,
        follow_on=("probe",),
    )

    children = await job_queue.children(job.id)
    assert [child.type for child in children] == ["probe"]


async def test_an_ffmpeg_failure_is_reported_as_a_sentence(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    compressor: CompressService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ffmpeg's own stderr is kept. Throwing it away turns a diagnosable failure into "it broke"."""
    from sift.kernel.media import FFmpegError

    original = await add_file(managed, "clip.mp4")

    async def refuses(argv: list[str], **_: Any) -> bytes:
        raise FFmpegError("ffmpeg failed: no such filter")

    monkeypatch.setattr(job_module, "run_tool", refuses)
    with pytest.raises(ProductionFailed, match="no such filter"):
        await run_compress(
            asset_id=original.asset.id,
            filename="clip-10MB.mp4",
            admin=admin,
            settings=settings,
            access=access,
            organizer=organizer,
            compressor=compressor,
            duplicates=duplicates,
            reindexer=reindexer,
            temp_db=temp_db,
            content_store=content_store,
            job_queue=job_queue,
        )


# --- the sample -------------------------------------------------------------------------------


async def _run_sample(
    *,
    asset_id: str,
    actor_id: str,
    settings: Settings,
    access: Repository,
    content_store: ContentStore,
    job_queue: JobQueue,
    target_bytes: int | None = None,
) -> str:
    job_id = await job_queue.enqueue(
        "compress_sample",
        {"asset_id": asset_id, "actor_id": actor_id, "target_bytes": target_bytes},
    )
    claimed = await job_queue.claim("test")
    assert claimed is not None
    await compress_sample(
        a_context(claimed, content=content_store, queue=job_queue),
        settings=settings,
        access=access,
    )
    return job_id


async def test_a_sample_lands_in_sifts_own_cache_and_never_in_the_library(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """It is thrown away, so it has no business sitting in somebody's collection even briefly."""
    original = await add_file(managed, "clip.mp4")
    ffmpeg(CORPUS / "accepted.mp4", sizes=[1000])

    job_id = await _run_sample(
        asset_id=original.asset.id,
        actor_id=admin.id,
        settings=settings,
        access=access,
        content_store=content_store,
        job_queue=job_queue,
    )

    assert (job_module.samples_directory(settings) / f"{job_id}.mp4").is_file()
    assert sorted(each.name for each in managed.path.iterdir()) == ["clip.mp4"]


async def test_a_sample_is_encoded_at_the_rung_the_target_asks_for(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    content_store: ContentStore,
    job_queue: JobQueue,
    temp_db: Database,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """A sample produced at some other quality answers a question nobody asked."""
    original = await add_file(managed, "clip.mp4")
    await _pretend_it_is_a_long_video(temp_db, original.asset.id)
    fake = ffmpeg(CORPUS / "accepted.mp4", sizes=[1000])

    await _run_sample(
        asset_id=original.asset.id,
        actor_id=admin.id,
        settings=settings,
        access=access,
        content_store=content_store,
        job_queue=job_queue,
        target_bytes=20 * 1024 * 1024,
    )

    argv = fake.calls[0]
    facts = plan.SourceFacts(
        width=1920,
        height=1080,
        duration_ms=600000,
        fps=30,
        size_bytes=900000000,
        vcodec="hevc",
        acodec="aac",
        container="mp4",
    )
    index = plan.best_rung_under(20 * 1024 * 1024, facts)
    expected = plan.allowed_rungs(facts)[index if index is not None else -1]
    assert argv[argv.index("-crf") + 1] == str(expected.crf)
    # And it has no sound in it at all: a sample shows the picture, and the real output's sound is
    # a copy of what is already there.
    assert "-an" in argv


async def test_a_sample_that_produces_nothing_is_reported(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    content_store: ContentStore,
    job_queue: JobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = await add_file(managed, "clip.mp4")

    async def writes_nothing(argv: list[str], **_: Any) -> bytes:
        return b""

    monkeypatch.setattr(job_module, "run_tool", writes_nothing)
    with pytest.raises(ProductionFailed, match="no sample"):
        await _run_sample(
            asset_id=original.asset.id,
            actor_id=admin.id,
            settings=settings,
            access=access,
            content_store=content_store,
            job_queue=job_queue,
        )


async def test_a_sample_asked_for_by_an_account_that_has_gone_stops(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    content_store: ContentStore,
    job_queue: JobQueue,
    temp_db: Database,
) -> None:
    original = await add_file(managed, "clip.mp4")
    await temp_db.execute("DELETE FROM users WHERE id = ?", (admin.id,))
    with pytest.raises(ProductionFailed, match="no longer exists"):
        await _run_sample(
            asset_id=original.asset.id,
            actor_id=admin.id,
            settings=settings,
            access=access,
            content_store=content_store,
            job_queue=job_queue,
        )
