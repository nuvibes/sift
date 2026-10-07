# SPDX-License-Identifier: AGPL-3.0-or-later
"""What an edit leaves on disk, and what the library knows about it afterwards.

The same real write seam over a real filesystem the compression job is tested against, and the same
stand-in for ffmpeg, for the same reason: what is under test is where the file ends up and what is
recorded about it.

Two of these are worth reading first: the one about a file in the vault, and the one about what a
copy inherits. Neither would fail in a suite whose fixtures were all ordinary unhidden files, and
both are faults a suite like that would miss.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from sift.kernel.access import Repository, Viewer
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore
from sift.kernel.db import Database
from sift.kernel.jobs import Job, JobContext, JobQueue, SystemCapabilities
from sift.slices.media_edit import jobs as job_module
from sift.slices.media_edit.editor import EDITED_TAG, EditService
from sift.slices.media_edit.jobs import edit
from sift.slices.media_edit.refusals import ProductionFailed
from sift.slices.media_edit.tests.conftest import FakeDuplicates, FakeReindexer, Library
from sift.slices.organize.service import Organizer

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("stub_handlers")]

CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


class FakeFfmpeg:
    """Writes real, gate-passing bytes wherever the argument list says to.

    It reads the destination out of the arguments rather than being told, so what builds them is
    exercised for real: one that stopped putting the destination last would break these, which is
    the correct outcome.
    """

    def __init__(self, source: Path) -> None:
        self.source = source
        self.calls: list[list[str]] = []

    async def run(self, argv: list[str], **_: Any) -> bytes:
        self.calls.append(argv)
        destination = Path(argv[-1])
        # Padded so the copy is never byte-identical to its original, which would make it the same
        # asset in two places: a real case, handled deliberately, and not the one under test here.
        destination.write_bytes(
            self.source.read_bytes() + _padding(self.source, 32 * len(self.calls))
        )
        return b""


def _padding(source: Path, length: int) -> bytes:
    """`length` bytes of filler the container itself allows after its last element, so a reader
    of the built file (the place remover reads every Matroska element) meets a well-formed file:
    a Void element in Matroska, a `free` box in the MP4 family, plain zeros elsewhere."""
    if source.suffix.lower() in (".mkv", ".webm"):
        # The id, then the size as an eight-byte number: the one shape every length can take.
        return b"\xec\x01" + (length - 9).to_bytes(7, "big") + bytes(length - 9)
    if source.suffix.lower() in (".mp4", ".mov", ".m4v"):
        return length.to_bytes(4, "big") + b"free" + bytes(length - 8)
    return bytes(length)


@pytest.fixture
def ffmpeg(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeFfmpeg]:
    def install(source: Path) -> FakeFfmpeg:
        fake = FakeFfmpeg(source)
        monkeypatch.setattr(job_module, "run_tool", fake.run)
        return fake

    return install


@pytest.fixture
def duplicates() -> FakeDuplicates:
    return FakeDuplicates()


@pytest.fixture
def reindexer() -> FakeReindexer:
    return FakeReindexer()


def step(**numbers: Any) -> dict[str, Any]:
    """One operation, as it sits in a job's payload.

    A Save carries a list of these, because several operations together produce one file rather than
    one file each. Most of what is asserted here is one of them, which is still the ordinary case.
    """
    return numbers


async def claimed_edit(queue: JobQueue, **payload: Any) -> Job:
    """A real queued job, really claimed, so the handler's cancellation check has a row to read."""
    job_id = await queue.enqueue("edit", payload)
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


async def run_edit(
    *,
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    editor: EditService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    follow_on: tuple[str, ...] = (),
    also_if_asked: tuple[str, ...] = (),
    **payload: Any,
) -> None:
    job = await claimed_edit(job_queue, actor_id=admin.id, **payload)
    await edit(
        a_context(job, content=content_store, queue=job_queue),
        settings=settings,
        access=access,
        writer=organizer,
        editor=editor,
        duplicates=duplicates,
        reindexer=reindexer,
        database=temp_db,
        follow_on=follow_on,
        also_if_asked=also_if_asked,
    )


# --- the property the whole design exists to give -------------------------------------------------


async def test_the_original_is_untouched_by_an_edit(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    editor: EditService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """Phrased as the property rather than as a guard that fired.

    The BYTES being unchanged says nothing was written over. The DIGEST being unchanged says the
    library still recognizes it as the same file, which is what carries its tags, its rating, the
    people on it and everything shared about it.
    """
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    on_disk = managed.path / "photo.jpg"
    before = on_disk.read_bytes()
    digest_before = original.asset.identity

    ffmpeg(CORPUS / "accepted.jpg")
    await run_edit(
        asset_id=original.asset.id,
        steps=[step(operation="rotate", turn="right")],
        filename="photo-rotated-right.jpg",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        editor=editor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
    )

    assert on_disk.read_bytes() == before
    unchanged = await content_store.get(original.asset.id)
    assert unchanged is not None and unchanged.identity == digest_before
    assert (managed.path / "photo-rotated-right.jpg").is_file()


async def test_a_name_already_taken_is_refused_and_the_file_there_survives(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    editor: EditService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    from sift.kernel.library_write import LibraryWriteRefused

    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    occupied = managed.path / "photo-rotated-right.jpg"
    occupied.write_bytes(b"something somebody else put here")

    ffmpeg(CORPUS / "accepted.jpg")
    with pytest.raises(LibraryWriteRefused):
        await run_edit(
            asset_id=original.asset.id,
            steps=[step(operation="rotate", turn="right")],
            filename="photo-rotated-right.jpg",
            admin=admin,
            settings=settings,
            access=access,
            organizer=organizer,
            editor=editor,
            duplicates=duplicates,
            reindexer=reindexer,
            temp_db=temp_db,
            content_store=content_store,
            job_queue=job_queue,
        )

    assert occupied.read_bytes() == b"something somebody else put here"


# --- a file in the vault --------------------------------------------------------------------------


async def test_a_file_in_the_vault_can_be_edited_at_all(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    editor: EditService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """A job has no session, so it rebuilds the user with the vault shut unless it says not to.

    Without that, the job resolves the file through the user it is acting for, that
    user comes back locked, and a concealed file is not there. What comes out is "There is no
    such file" against a file somebody was looking at when they pressed the button.

    And the copy has to arrive concealed too, which is the other half and the half nobody notices.
    """
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await temp_db.execute(
        "INSERT INTO asset_user_state (asset_id, user_id, hidden, hidden_at, updated_at)"
        " VALUES (?, ?, 1, ?, ?)",
        (original.asset.id, admin.id, 1_700_000_000, 1_700_000_000),
    )
    assert await access.is_concealed(admin.id, original.asset.id), "it is not actually concealed"

    ffmpeg(CORPUS / "accepted.jpg")
    await run_edit(
        asset_id=original.asset.id,
        steps=[step(operation="crop", left=0, top=0, width=8, height=8)],
        filename="photo-cropped-8x8.jpg",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        editor=editor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
    )

    assert (managed.path / "photo-cropped-8x8.jpg").is_file()
    located = await content_store.location_at(managed.root.id, "photo-cropped-8x8.jpg")
    assert located is not None
    assert await access.is_concealed(admin.id, located.asset_id)


# --- the four things every copy owes ----------------------------------------------------------------


async def test_the_copy_is_tagged_and_says_what_it_was_made_from(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    editor: EditService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """Its own tag rather than the compression one: "made smaller" and "changed" are two questions.

    The recorded operation is the verb somebody pressed, which is why a trim and a clip are apart in
    the record even though the server does one thing for both.
    """
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")

    ffmpeg(CORPUS / "accepted.jpg")
    await run_edit(
        asset_id=original.asset.id,
        steps=[step(operation="resize", width=640)],
        filename="photo-640px.jpg",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        editor=editor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
    )

    located = await content_store.location_at(managed.root.id, "photo-640px.jpg")
    assert located is not None
    tags = await temp_db.fetch_all(
        "SELECT t.name, t.created_by_act FROM tags t JOIN asset_tags at ON at.tag_id = t.id"
        " WHERE at.asset_id = ?",
        (located.asset_id,),
    )
    # A resize is the editor's act like every other operation it has.
    assert [(str(row["name"]), row["created_by_act"]) for row in tags] == [(EDITED_TAG, "edit")]

    row = await temp_db.fetch_one(
        "SELECT source_asset_id, operation, preset, target_bytes FROM produced_files"
        " WHERE asset_id = ?",
        (located.asset_id,),
    )
    assert row is not None
    assert str(row["source_asset_id"]) == original.asset.id
    assert str(row["operation"]) == "resize"
    # An edit has no size target and no preset. Both columns exist for the other half of the slice.
    assert row["preset"] is None and row["target_bytes"] is None

    # And the ledger's `produced` event, which outlives the row above: the copy as the subject, the
    # original as the object, both NAMED, and the person who asked as the actor.
    produced = await temp_db.fetch_one(
        "SELECT d.actor_kind, d.actor_id, d.object_id, d.object_name, s.subject_id, s.name"
        " FROM workbench_decisions d JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE d.verb = 'produced'",
    )
    assert produced is not None
    assert (produced["actor_kind"], produced["actor_id"]) == ("user", admin.id)
    assert (produced["subject_id"], produced["object_id"]) == (located.asset_id, original.asset.id)
    assert produced["name"] and produced["object_name"]


async def test_the_copy_and_its_original_are_not_offered_as_a_duplicate_pair(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    editor: EditService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """A crop of a photograph matches it by every measure duplicate detection has."""
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")

    ffmpeg(CORPUS / "accepted.jpg")
    await run_edit(
        asset_id=original.asset.id,
        steps=[step(operation="rotate", turn="half")],
        filename="photo-rotated-180.jpg",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        editor=editor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
    )

    located = await content_store.location_at(managed.root.id, "photo-rotated-180.jpg")
    assert located is not None
    assert duplicates.pairs == [(original.asset.id, located.asset_id)]
    assert reindexer.touched_ids == [located.asset_id]


async def test_whatever_a_new_file_is_worth_starting_is_started(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    editor: EditService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """Named from outside, because this feature must not learn that thumbnails exist."""
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")

    ffmpeg(CORPUS / "accepted.jpg")
    await run_edit(
        asset_id=original.asset.id,
        steps=[step(operation="rotate", turn="left")],
        filename="photo-rotated-left.jpg",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        editor=editor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
        follow_on=("probe",),
    )

    queued = await temp_db.fetch_all("SELECT type FROM jobs WHERE type = 'probe'")
    assert len(queued) == 1


async def test_a_copy_whose_bytes_are_already_in_the_library_records_nothing(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    editor: EditService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An edit that produced bytes already in the library is a second place, not a new file.

    Writing over what that asset already carries would be this feature editing a file it did not
    make, so nothing is inherited, nothing is tagged and nothing is recorded.
    """
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")

    class Identical(FakeFfmpeg):
        async def run(self, argv: list[str], **_: Any) -> bytes:
            Path(argv[-1]).write_bytes(self.source.read_bytes())
            return b""

    monkeypatch.setattr(job_module, "run_tool", Identical(CORPUS / "accepted.jpg").run)
    await run_edit(
        asset_id=original.asset.id,
        steps=[step(operation="rotate", turn="right")],
        filename="photo-rotated-right.jpg",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        editor=editor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
    )

    assert duplicates.pairs == []
    assert await temp_db.fetch_all("SELECT id FROM produced_files") == []


# --- what goes wrong -----------------------------------------------------------------------------------


async def test_an_encoder_that_writes_nothing_says_so_rather_than_failing_later(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    editor: EditService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ffmpeg can exit 0 and write nothing."""
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")

    async def writes_nothing(argv: list[str], **_: Any) -> bytes:
        return b""

    monkeypatch.setattr(job_module, "run_tool", writes_nothing)
    with pytest.raises(ProductionFailed, match="produced nothing"):
        await run_edit(
            asset_id=original.asset.id,
            steps=[step(operation="rotate", turn="right")],
            filename="photo-rotated-right.jpg",
            admin=admin,
            settings=settings,
            access=access,
            organizer=organizer,
            editor=editor,
            duplicates=duplicates,
            reindexer=reindexer,
            temp_db=temp_db,
            content_store=content_store,
            job_queue=job_queue,
        )
    assert not list(managed.path.glob("photo-rotated-right*"))


async def test_an_account_deleted_while_its_job_waited_is_said_plainly(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    editor: EditService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
) -> None:
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    await temp_db.execute("DELETE FROM users WHERE id = ?", (admin.id,))
    with pytest.raises(ProductionFailed, match="no longer exists"):
        await run_edit(
            asset_id=original.asset.id,
            steps=[step(operation="rotate", turn="right")],
            filename="photo-rotated-right.jpg",
            admin=admin,
            settings=settings,
            access=access,
            organizer=organizer,
            editor=editor,
            duplicates=duplicates,
            reindexer=reindexer,
            temp_db=temp_db,
            content_store=content_store,
            job_queue=job_queue,
        )


async def test_a_disk_with_no_room_is_refused_before_anything_is_written(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    editor: EditService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filling somebody's media disk is worse than not editing: everything else writing to it fails."""
    import shutil

    original = await add_file(managed, "photo.jpg", source="accepted.jpg")
    monkeypatch.setattr(
        shutil, "disk_usage", lambda _: type("Usage", (), {"free": 0, "total": 0, "used": 0})()
    )
    with pytest.raises(ProductionFailed, match="free space"):
        await run_edit(
            asset_id=original.asset.id,
            steps=[step(operation="rotate", turn="right")],
            filename="photo-rotated-right.jpg",
            admin=admin,
            settings=settings,
            access=access,
            organizer=organizer,
            editor=editor,
            duplicates=duplicates,
            reindexer=reindexer,
            temp_db=temp_db,
            content_store=content_store,
            job_queue=job_queue,
        )


# --- the right command for the right file ---------------------------------------------------------------


async def test_a_cut_is_built_as_a_copy_and_lands_in_the_source_container(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    editor: EditService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """The job picks the command, and this is the assertion that it picked the right one.

    A TRIM, deliberately. The two cuts are not the same command: a trim copies the packets, and a
    clip re-encodes so the piece begins where it was marked. What this asserts is that a trim is
    free.
    """
    original = await add_file(managed, "video.mkv", source="accepted.mkv")

    fake = ffmpeg(CORPUS / "accepted.mkv")
    await run_edit(
        asset_id=original.asset.id,
        steps=[step(operation="trim", start_ms=1_000, duration_ms=2_000)],
        filename="video-trimmed.mkv",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        editor=editor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
    )

    argv = fake.calls[0]
    assert argv[argv.index("-c:v") + 1] == "copy"
    assert argv[argv.index("-c:a") + 1] == "copy"
    assert "libx264" not in argv
    assert argv[argv.index("-f") + 1] == "matroska"


async def test_a_gif_is_built_by_its_own_command_rather_than_as_a_cut(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    editor: EditService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """A GIF is NOT a cut with a different container on it, and the job is what has to know.

    A cut writes the streams it was given: copied or re-encoded, but the same picture and the same
    sound in the same shape. This rebuilds the picture at another rate and another size, with a
    palette of its own, and throws the sound away because none of these formats can hold any. So it
    is its own builder, chosen before the branch that decides how a cut is written.

    The size caps the SHORT edge, which is why the shape is read from what probing recorded rather
    than assumed: capping the width instead would give a landscape clip a third of the pixels of a
    portrait one at the same setting.
    """
    original = await add_file(managed, "video.mp4", source="accepted.mp4")

    # WebP rather than the default GIF, and it is the FAKE that decides that rather than the
    # feature: this one pads what it writes so a copy is never byte-identical to its original, and
    # trailing bytes are exactly what a GIF's terminator rule refuses. The command under test is the
    # same one for all three formats: only the muxer and the encoder differ.
    fake = ffmpeg(CORPUS / "accepted_animated.webp")
    await run_edit(
        asset_id=original.asset.id,
        steps=[step(operation="gif", start_ms=1_000, duration_ms=2_000)],
        gif_format="webp",
        filename="video-gif-from-1s.webp",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        editor=editor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
    )

    argv = fake.calls[0]
    assert argv[argv.index("-f") + 1] == "webp"
    assert "-an" in argv, "a GIF was built carrying a sound track it cannot hold"
    assert argv[argv.index("-loop") + 1] == "0"
    assert "copy" not in argv, "a GIF was built as a stream copy"


async def test_a_payload_naming_an_edit_sift_does_not_know_is_refused(
    job_queue: JobQueue, content_store: ContentStore
) -> None:
    """The job reads its own payload rather than trusting whatever wrote the row.

    A row naming something this version has never heard of is one written by a different version.
    It is answered as a sentence rather than as a validator's traceback, because what reads it is
    somebody looking at a failed job.
    """
    job = await claimed_edit(
        job_queue,
        asset_id="01HX0000000000000000000009",
        actor_id="01HX000000000000000000000A",
        steps=[step(operation="colourize")],
        filename="x.jpg",
    )
    with pytest.raises(ProductionFailed, match="not in a shape Sift understands"):
        job_module._read_edit(a_context(job, content=content_store, queue=job_queue))


@pytest.mark.parametrize(
    ("carried", "read"),
    [
        ({"gif_format": "webp"}, "webp"),
        # The field's older name, as a job queued under it carries it.
        ({"animation_format": "avif"}, "avif"),
        ({}, "gif"),
    ],
)
async def test_the_gif_format_is_read_under_either_name(
    job_queue: JobQueue, content_store: ContentStore, carried: dict[str, str], read: str
) -> None:
    job = await claimed_edit(
        job_queue,
        asset_id="01HX0000000000000000000009",
        actor_id="01HX000000000000000000000A",
        steps=[step(operation="gif", start_ms=0, duration_ms=1_000)],
        filename="x.gif",
        **carried,
    )
    edit = job_module._read_edit(a_context(job, content=content_store, queue=job_queue))
    assert edit.gif_format == read


async def test_a_payload_with_no_edits_in_it_at_all_is_refused(
    job_queue: JobQueue, content_store: ContentStore
) -> None:
    """The list is the whole of what the job does, so an empty one is nothing to do."""
    job = await claimed_edit(
        job_queue,
        asset_id="01HX0000000000000000000009",
        actor_id="01HX000000000000000000000A",
        steps=[],
        filename="x.jpg",
    )
    with pytest.raises(ProductionFailed, match="which edits to make"):
        job_module._read_edit(a_context(job, content=content_store, queue=job_queue))


async def test_several_operations_become_one_chain_and_one_file(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    editor: EditService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """The point of a Save carrying a list: one run of the tool, one picture at the end of it.

    Asked one operation at a time, a crop and then a turn would leave two files on the disk and the
    first of them is something nobody wanted. The filters are asserted in order, because the order
    is the difference between cropping the picture and cropping what the turn made of it.
    """
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")

    fake = ffmpeg(CORPUS / "accepted.jpg")
    await run_edit(
        asset_id=original.asset.id,
        steps=[
            step(operation="crop", left=0, top=0, width=8, height=8),
            step(operation="rotate", turn="mirror"),
        ],
        filename="photo-edited.jpg",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        editor=editor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
    )

    assert len(fake.calls) == 1, "several operations must not be several runs of the tool"
    argv = fake.calls[0]
    assert argv[argv.index("-vf") + 1] == "crop=8:8:0:0,hflip"

    located = await content_store.location_at(managed.root.id, "photo-edited.jpg")
    assert located is not None
    row = await temp_db.fetch_one(
        "SELECT operation FROM produced_files WHERE asset_id = ?", (located.asset_id,)
    )
    assert row is not None
    # One row, and it says "edited" rather than naming one of the two things that were done.
    assert str(row["operation"]) == "edit"


async def test_a_photograph_a_camera_turned_is_put_upright_before_anything_else(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    editor: EditService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """The camera's note goes at the FRONT of the chain, and that is the whole of the fix.

    A rectangle is dragged over the picture somebody can see. Put anywhere but first, the crop cuts
    the stored picture instead, which is a real photograph, of the wrong part, with nothing on
    screen saying so.
    """
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")

    fake = ffmpeg(CORPUS / "accepted.jpg")
    await run_edit(
        asset_id=original.asset.id,
        steps=[step(operation="crop", left=0, top=0, width=8, height=8)],
        quarter_turns=1,
        mirrored=True,
        filename="photo-cropped-8x8.jpg",
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        editor=editor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
    )

    argv = fake.calls[0]
    assert argv[argv.index("-vf") + 1] == "hflip,transpose=1,crop=8:8:0:0"


async def test_the_extra_job_runs_ONLY_where_the_request_asked_for_it(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    editor: EditService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """The guard on "Save as Loop", and the one a test of the default alone would not catch.

    `follow_on` is what EVERY produced file is worth starting; this is what only the ones that asked
    for it get. Without the distinction, every crop, resize and trim anybody ever saved would put a
    row on the Loops screen: a screen full of things nobody marked, and nothing anywhere saying
    why.
    """
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")

    ffmpeg(CORPUS / "accepted.jpg")
    await run_edit(
        asset_id=original.asset.id,
        steps=[step(operation="rotate", turn="left")],
        filename="photo-rotated-left.jpg",
        as_loop=False,
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        editor=editor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
        also_if_asked=("loop_whole",),
    )

    queued = await temp_db.fetch_all("SELECT type FROM jobs WHERE type = 'loop_whole'")
    assert queued == [], "an ordinary edit put a row on the Loops screen"


async def test_the_extra_job_DOES_run_where_it_was_asked_for(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    editor: EditService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """The other half, and the two are worth having separately: a guard that never fires and a
    guard that always fires look identical from one test."""
    original = await add_file(managed, "photo.jpg", source="accepted.jpg")

    ffmpeg(CORPUS / "accepted.jpg")
    await run_edit(
        asset_id=original.asset.id,
        steps=[step(operation="rotate", turn="left")],
        filename="photo-rotated-left.jpg",
        as_loop=True,
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        editor=editor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
        also_if_asked=("loop_whole",),
    )

    queued = await temp_db.fetch_all("SELECT type FROM jobs WHERE type = 'loop_whole'")
    assert len(queued) == 1


async def test_the_extra_job_is_told_WHICH_PIECE_OF_WHAT_this_file_is(
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    settings: Settings,
    access: Repository,
    organizer: Organizer,
    editor: EditService,
    duplicates: FakeDuplicates,
    reindexer: FakeReindexer,
    temp_db: Database,
    content_store: ContentStore,
    job_queue: JobQueue,
    ffmpeg: Callable[..., FakeFfmpeg],
) -> None:
    """Three facts about the cut, and none of them can be recovered later.

    How LONG, because the probing that measures a produced file is a sibling of this follow-on with
    no ordering between them: ask the table and the answer is NULL about half the time. And WHICH
    FILE and FROM WHERE, because a produced file carries no memory of the stretch it was: by
    the time anything downstream runs it is simply a file with a duration.

    They are plain facts about a cut and not loop vocabulary, which is the line this slice stays on:
    it enqueues a job type it was handed and never learns what will be done with them.
    """
    original = await add_file(managed, "video.mkv", source="accepted.mkv")

    ffmpeg(CORPUS / "accepted.mkv")
    await run_edit(
        asset_id=original.asset.id,
        steps=[step(operation="clip", start_ms=8_000, duration_ms=4_000)],
        filename="video-from-8s.mkv",
        as_loop=True,
        admin=admin,
        settings=settings,
        access=access,
        organizer=organizer,
        editor=editor,
        duplicates=duplicates,
        reindexer=reindexer,
        temp_db=temp_db,
        content_store=content_store,
        job_queue=job_queue,
        also_if_asked=("loop_whole",),
    )

    (row,) = await temp_db.fetch_all("SELECT payload FROM jobs WHERE type = 'loop_whole'")
    payload = json.loads(str(row["payload"]))
    assert payload["duration_ms"] == 4_000
    assert payload["cut_from_asset_id"] == original.asset.id
    assert payload["cut_from_start_ms"] == 8_000
