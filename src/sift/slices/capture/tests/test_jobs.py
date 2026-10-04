# SPDX-License-Identifier: AGPL-3.0-or-later
"""The import job: resolving a staged file from its id and running the one pipeline over it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, LocationStatus, Root
from sift.kernel.ids import new_id
from sift.kernel.ingress import NoDestination
from sift.kernel.jobs import JobContext, JobFailedPermanently
from sift.slices.capture import jobs as jobs_module
from sift.slices.capture.jobs import IMPORT, ImportRefused, StagingGone, run_import, sweep_staging
from sift.slices.capture.pipeline import STAGING_DIR_NAME

from .conftest import VIDEO_SECONDS, RecordingReindexer, corpus_file, draw

pytestmark = [pytest.mark.integration]

Context = Callable[[str, dict[str, object]], Awaitable[JobContext]]


def _stage(settings: Settings, staging_id: str, source_name: str, drawn: Path) -> None:
    """Put a drawn file where the job will look for it, under a staging id."""
    staging_dir = settings.data_dir / STAGING_DIR_NAME / staging_id
    staging_dir.mkdir(parents=True)
    (staging_dir / source_name).write_bytes(drawn.read_bytes())


async def test_it_imports_the_staged_file_and_cleans_up_after_itself(
    context_for: Context,
    settings: Settings,
    content_store: ContentStore,
    root: Root,
    tmp_path: Path,
    reindexer: RecordingReindexer,
    default_folder: str,
) -> None:
    drawn = draw(tmp_path / "src" / "clip.mp4", "testsrc2=size=64x64:rate=5", VIDEO_SECONDS)
    staging_id = new_id()
    _stage(settings, staging_id, "clip.mp4", drawn)

    ctx = await context_for(
        IMPORT, {"staging_id": staging_id, "origin": "drop", "dest_folder_id": default_folder}
    )
    await run_import(ctx, settings=settings, reindexer=reindexer)

    assert await content_store.location_at(root.id, "clip.mp4") is not None
    assert len(await ctx.queue.children(ctx.job.id)) == 1  # probing
    # The scratch copy is gone once the file is in.
    assert not (settings.data_dir / STAGING_DIR_NAME / staging_id).exists()


async def test_a_screenshot_is_named_after_the_file_it_was_taken_of(
    context_for: Context,
    settings: Settings,
    content_store: ContentStore,
    root: Root,
    tmp_path: Path,
    reindexer: RecordingReindexer,
    default_folder: str,
) -> None:
    """`<the file's name>-ss.png`, and a second screenshot of the same file is numbered by the
    folder. A screenshot of a file that has gone keeps the name it arrived with."""
    video = draw(tmp_path / "src" / "Beach Day.mp4", "testsrc2=size=64x64:rate=5", VIDEO_SECONDS)
    staging_id = new_id()
    _stage(settings, staging_id, "Beach Day.mp4", video)
    ctx = await context_for(
        IMPORT, {"staging_id": staging_id, "origin": "drop", "dest_folder_id": default_folder}
    )
    await run_import(ctx, settings=settings, reindexer=reindexer)
    source = await content_store.location_at(root.id, "Beach Day.mp4")
    assert source is not None

    patterns = (
        "testsrc2=size=64x64:rate=1",
        "color=c=red:size=64x64:rate=1",
        "smptebars=size=64x64",
    )
    for index, (pattern, of) in enumerate(
        zip(patterns, (source.asset_id,) * 2 + (new_id(),), strict=True)
    ):
        shot = draw(tmp_path / "src" / f"shot{index}.png", pattern)
        staging_id = new_id()
        _stage(settings, staging_id, "frame-0m03s.png", shot)
        ctx = await context_for(
            IMPORT,
            {
                "staging_id": staging_id,
                "origin": "upload",
                "dest_folder_id": default_folder,
                "screenshot_of": of,
            },
        )
        await run_import(ctx, settings=settings, reindexer=reindexer)

    assert await content_store.location_at(root.id, "Beach Day-ss.png") is not None
    assert await content_store.location_at(root.id, "Beach Day-ss-1.png") is not None
    assert await content_store.location_at(root.id, "frame-0m03s.png") is not None


async def test_a_screenshot_is_named_after_what_the_file_is_called_now(
    context_for: Context,
    settings: Settings,
    content_store: ContentStore,
    root: Root,
    tmp_path: Path,
    reindexer: RecordingReindexer,
    default_folder: str,
) -> None:
    """A file renamed since it arrived: the screenshot takes the name the screen shows, never the
    long name the file was imported under."""
    video = draw(
        tmp_path / "src" / "clip [Beach Day].mp4", "testsrc2=size=64x64:rate=5", VIDEO_SECONDS
    )
    staging_id = new_id()
    _stage(settings, staging_id, "clip [Beach Day].mp4", video)
    ctx = await context_for(
        IMPORT, {"staging_id": staging_id, "origin": "drop", "dest_folder_id": default_folder}
    )
    await run_import(ctx, settings=settings, reindexer=reindexer)
    landed = [
        one
        for one in await content_store.every_location()
        if one.rel_path.endswith(".mp4") and one.root_id == root.id
    ]
    assert len(landed) == 1
    moved = await content_store.relocate(
        landed[0].id, root_id=root.id, rel_path="Beach Day.mp4", folder_id=landed[0].folder_id
    )
    assert moved is not None

    shot = draw(tmp_path / "src" / "shot.png", "testsrc2=size=64x64:rate=1")
    staging_id = new_id()
    _stage(settings, staging_id, "frame-0m03s.png", shot)
    ctx = await context_for(
        IMPORT,
        {
            "staging_id": staging_id,
            "origin": "upload",
            "dest_folder_id": default_folder,
            "screenshot_of": landed[0].asset_id,
        },
    )
    await run_import(ctx, settings=settings, reindexer=reindexer)

    assert await content_store.location_at(root.id, "Beach Day-ss.png") is not None


async def test_a_file_dropped_again_lands_nothing_and_its_job_says_where_it_already_is(
    context_for: Context,
    settings: Settings,
    content_store: ContentStore,
    root: Root,
    tmp_path: Path,
    reindexer: RecordingReindexer,
    default_folder: str,
) -> None:
    """The drop answers with this note when the import settles, so a second drop of the same
    bytes is told where the first one is rather than reading as nothing having happened."""
    drawn = draw(tmp_path / "src" / "clip.mp4", "testsrc2=size=64x64:rate=5", VIDEO_SECONDS)
    for name in ("clip.mp4", "the same clip.mp4"):
        staging_id = new_id()
        _stage(settings, staging_id, name, drawn)
        ctx = await context_for(
            IMPORT, {"staging_id": staging_id, "origin": "drop", "dest_folder_id": default_folder}
        )
        await run_import(ctx, settings=settings, reindexer=reindexer)

    assert await content_store.location_at(root.id, "the same clip.mp4") is None
    job = await ctx.queue.get(ctx.job.id)
    assert job is not None and job.note == f"Already here: {root.name}"
    assert not (settings.data_dir / STAGING_DIR_NAME / staging_id).exists()


class _Named:
    """The two content reads `_named_after` makes, answered for one file."""

    def __init__(self, asset: object, locations: list[object]) -> None:
        self._asset = asset
        self._locations = locations

    async def get(self, _asset_id: str) -> object:
        return self._asset

    async def locations(self, _asset_id: str) -> list[object]:
        return self._locations


def _named_context(asset: object, locations: list[object]) -> JobContext:
    return cast("JobContext", SimpleNamespace(content=_Named(asset, locations)))


async def test_a_screenshot_of_a_file_with_no_name_to_take_keeps_the_name_it_arrived_with(
    tmp_path: Path,
) -> None:
    """No copy present and no name recorded on import: there is nothing to sit beside."""
    staged = tmp_path / "frame-0m03s.png"
    staged.write_bytes(b"png")
    nameless = SimpleNamespace(original_filename=None)
    gone = SimpleNamespace(rel_path="Beach Day.mp4", status=LocationStatus.MISSING)

    named = await jobs_module._named_after(staged, new_id(), _named_context(nameless, [gone]))

    assert named == staged and staged.exists()


async def test_a_retried_screenshot_already_under_its_new_name_is_not_renamed_again(
    tmp_path: Path,
) -> None:
    """The rename happens inside the staging folder, so a retry finds the file already named."""
    staged = tmp_path / "Beach Day-ss.png"
    staged.write_bytes(b"png")
    asset = SimpleNamespace(original_filename="clip [Beach Day].mp4")
    here = SimpleNamespace(rel_path="videos/Beach Day.mp4", status=LocationStatus.PRESENT)

    named = await jobs_module._named_after(staged, new_id(), _named_context(asset, [here]))

    assert named == staged and staged.read_bytes() == b"png"


async def test_a_screenshot_of_something_that_is_not_an_id_is_refused(
    context_for: Context, settings: Settings, reindexer: RecordingReindexer, default_folder: str
) -> None:
    ctx = await context_for(
        IMPORT,
        {
            "staging_id": new_id(),
            "origin": "upload",
            "dest_folder_id": default_folder,
            "screenshot_of": "not an id",
        },
    )
    with pytest.raises(ValueError, match="screenshot_of"):
        await run_import(ctx, settings=settings, reindexer=reindexer)


async def test_a_disguised_staged_file_is_refused_and_the_scratch_space_is_cleared(
    context_for: Context,
    settings: Settings,
    content_store: ContentStore,
    root: Root,
    tmp_path: Path,
    reindexer: RecordingReindexer,
    default_folder: str,
) -> None:
    staging_id = new_id()
    corpus_file(tmp_path / "corpus" / "disguised_exe.mp4", "disguised_exe.mp4")
    _stage(settings, staging_id, "disguised_exe.mp4", tmp_path / "corpus" / "disguised_exe.mp4")

    ctx = await context_for(
        IMPORT, {"staging_id": staging_id, "origin": "upload", "dest_folder_id": default_folder}
    )
    with pytest.raises(ImportRefused):
        await run_import(ctx, settings=settings, reindexer=reindexer)

    assert await content_store.location_at(root.id, "disguised_exe.mp4") is None
    assert not (settings.data_dir / STAGING_DIR_NAME / staging_id).exists()
    assert issubclass(ImportRefused, JobFailedPermanently), "no retry reads the bytes differently"


async def test_a_staging_directory_that_is_gone_is_not_retried(
    context_for: Context,
    settings: Settings,
    root: Root,
    reindexer: RecordingReindexer,
) -> None:
    ctx = await context_for(
        IMPORT, {"staging_id": new_id(), "origin": "drop", "dest_folder_id": None}
    )
    with pytest.raises(StagingGone):
        await run_import(ctx, settings=settings, reindexer=reindexer)
    assert issubclass(StagingGone, JobFailedPermanently), "nothing to retry against"


async def test_a_staging_directory_with_no_file_in_it_is_gone_too(
    context_for: Context,
    settings: Settings,
    root: Root,
    reindexer: RecordingReindexer,
    default_folder: str,
) -> None:
    """A directory that holds only another directory holds no file to import."""
    staging_id = new_id()
    staging_dir = settings.data_dir / STAGING_DIR_NAME / staging_id
    (staging_dir / "not-a-file").mkdir(parents=True)
    ctx = await context_for(
        IMPORT, {"staging_id": staging_id, "origin": "drop", "dest_folder_id": default_folder}
    )
    with pytest.raises(StagingGone):
        await run_import(ctx, settings=settings, reindexer=reindexer)


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"origin": "drop"}, "staged file"),  # no staging_id
        ({"staging_id": 123, "origin": "drop"}, "staged file"),  # not an id
        ({"staging_id": "not-an-id", "origin": "drop"}, "staged file"),
        ({"staging_id": None, "origin": None}, "staged file"),
    ],
)
async def test_a_bad_staging_id_is_refused(
    context_for: Context,
    settings: Settings,
    payload: dict[str, object],
    match: str,
    reindexer: RecordingReindexer,
) -> None:
    ctx = await context_for(IMPORT, payload)
    with pytest.raises(ValueError, match=match):
        await run_import(ctx, settings=settings, reindexer=reindexer)


@pytest.mark.parametrize(
    "origin",
    [None, 42, "bogus", "scan", "watch", "download"],
)
async def test_an_origin_capture_does_not_stage_is_refused(
    context_for: Context,
    settings: Settings,
    origin: object,
    reindexer: RecordingReindexer,
) -> None:
    ctx = await context_for(IMPORT, {"staging_id": new_id(), "origin": origin})
    with pytest.raises(ValueError):
        await run_import(ctx, settings=settings, reindexer=reindexer)


async def test_a_dest_folder_id_that_is_not_a_string_is_refused(
    context_for: Context,
    settings: Settings,
    reindexer: RecordingReindexer,
) -> None:
    ctx = await context_for(IMPORT, {"staging_id": new_id(), "origin": "drop", "dest_folder_id": 5})
    with pytest.raises(ValueError, match="folder"):
        await run_import(ctx, settings=settings, reindexer=reindexer)


async def test_a_failure_that_may_pass_keeps_the_staged_file_for_the_retry(
    context_for: Context,
    settings: Settings,
    content_store: ContentStore,
    root: Root,
    tmp_path: Path,
    reindexer: RecordingReindexer,
    default_folder: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The destination's drive is away for a moment. The bytes stay where the retry will look,
    and the retry then takes them in and clears the scratch space."""
    drawn = draw(tmp_path / "src" / "clip.mp4", "testsrc2=size=64x64:rate=5", VIDEO_SECONDS)
    staging_id = new_id()
    _stage(settings, staging_id, "clip.mp4", drawn)
    staging_dir = settings.data_dir / STAGING_DIR_NAME / staging_id

    async def drive_is_away(**kwargs: object) -> None:
        raise NoDestination("the drive is away")

    monkeypatch.setattr(jobs_module, "import_file", drive_is_away)
    ctx = await context_for(
        IMPORT, {"staging_id": staging_id, "origin": "drop", "dest_folder_id": default_folder}
    )
    with pytest.raises(NoDestination):
        await run_import(ctx, settings=settings, reindexer=reindexer)
    assert (staging_dir / "clip.mp4").is_file(), "the bytes are kept for the retry"

    monkeypatch.undo()
    again = await context_for(
        IMPORT, {"staging_id": staging_id, "origin": "drop", "dest_folder_id": default_folder}
    )
    await run_import(again, settings=settings, reindexer=reindexer)
    assert await content_store.location_at(root.id, "clip.mp4") is not None
    assert not staging_dir.exists()


def test_the_sweep_at_start_clears_only_what_no_job_can_ask_for(settings: Settings) -> None:
    """A staged file older than the queue keeps a job for is gone; a younger one may still be
    retried and stays."""
    staging_root = settings.data_dir / STAGING_DIR_NAME
    old, young = new_id(), new_id()
    for staging_id in (old, young):
        (staging_root / staging_id).mkdir(parents=True)
        (staging_root / staging_id / "clip.mp4").write_bytes(b"x")
    (staging_root / "not-a-staging-id").mkdir()
    now = 1_800_000_000.0
    import os

    os.utime(staging_root / old, (now - 8 * 86_400, now - 8 * 86_400))
    os.utime(staging_root / young, (now - 3_600, now - 3_600))

    assert sweep_staging(settings.data_dir, now=now) == 1
    assert not (staging_root / old).exists()
    assert (staging_root / young / "clip.mp4").is_file()
    assert (staging_root / "not-a-staging-id").is_dir(), "only staging ids are touched"


def test_a_staged_folder_that_vanishes_under_the_sweep_is_left_to_whoever_took_it(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sweep lists the staging area and then asks each entry its age, and an import can finish
    and clear its own folder between the two. That folder is nobody's to remove any more, and its
    disappearance must not stop the sweep reaching the rest."""
    import os

    staging_root = settings.data_dir / STAGING_DIR_NAME
    taken, old = new_id(), new_id()
    for staging_id in (taken, old):
        (staging_root / staging_id).mkdir(parents=True)
    now = 1_800_000_000.0
    os.utime(staging_root / old, (now - 8 * 86_400, now - 8 * 86_400))
    real_stat, real_is_dir = Path.stat, Path.is_dir

    def stat(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == taken:
            raise FileNotFoundError(self)
        return real_stat(self, *args, **kwargs)

    def is_dir(self: Path, *args: Any, **kwargs: Any) -> bool:
        return True if self.name == taken else real_is_dir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(Path, "is_dir", is_dir)

    assert sweep_staging(settings.data_dir, now=now) == 1
    assert not os.path.isdir(staging_root / old)
    assert os.path.isdir(staging_root / taken), "not this sweep's to remove"
