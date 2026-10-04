# SPDX-License-Identifier: AGPL-3.0-or-later
"""The still the grid draws and the still of one loop, run against real files with real ffmpeg."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

# A fingerprint write records an event through the ledger door, and the door writes into the
# workbench's own table, so a database built without that component has nowhere to put it.
# Imported for the registration, nothing else. Without it this module passes only when some
# OTHER module collected in the same run happens to have imported it, and fails on its own;
# the content suite carries the same line for the same reason.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel import media
from sift.kernel import subprocess as kernel_subprocess
from sift.kernel.config import Settings
from sift.kernel.content import (
    ContentStore,
    DerivativeKind,
    Ingested,
)
from sift.kernel.db import Database
from sift.kernel.hardware import HardwareReport
from sift.kernel.ingress import Origin, verify_ingress
from sift.kernel.jobs import (
    JobQueue,
)
from sift.slices.media_jobs import (
    ffmpeg,
    jobs,
    shared,
    thumbnails,
)
from sift.slices.media_jobs.tests.conftest import VIDEO_SECONDS, draw, take_in, webp_tools
from sift.slices.media_jobs.tests.support import Context
from sift.testing.fixtures import LibraryRoot

pytestmark = [pytest.mark.integration]


# --- probe ------------------------------------------------------------------------------------


async def test_a_thumbnail_is_drawn_for_an_animated_webp(
    animated_webp: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    tmp_path: Path,
) -> None:
    """The stage the whole feature exists for: without it these sit on the importing shimmer.

    Run after probing, as it really is, so the readable copy is already there: what this shows
    is that the thumbnail stage finds it rather than reaching for the original and drawing nothing.
    """
    settings = settings.model_copy(update=webp_tools(tmp_path / "tools", frames=5, size="64x64"))
    checked = verify_ingress(animated_webp, origin=Origin.SCAN, settings=settings)
    ingested = await content_store.ingest(
        checked, root_id=library_root.id, rel_path=animated_webp.name
    )
    await jobs.probe(
        await context_for("probe", {"asset_id": ingested.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    await jobs.thumbnail(
        await context_for("thumbnail", {"asset_id": ingested.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    thumbs = [
        one
        for one in await content_store.derivatives(ingested.asset.id)
        if one.kind.value == "thumb"
    ]
    assert len(thumbs) == 1
    drawn = settings.cache_dir / thumbs[0].rel_cache_path
    assert drawn.is_file()
    assert drawn.stat().st_size > 0


async def test_a_thumbnail_is_drawn_for_an_animated_webp_filed_as_a_video(
    animated_webp: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    temp_db: Database,
    tmp_path: Path,
) -> None:
    """A row can file an animated WebP as a video (an older classifier did). The bytes are WebP
    all the same, which the decoder cannot read, so the stage still reaches for the copy."""
    settings = settings.model_copy(update=webp_tools(tmp_path / "tools", frames=5, size="64x64"))
    checked = verify_ingress(animated_webp, origin=Origin.SCAN, settings=settings)
    ingested = await content_store.ingest(
        checked, root_id=library_root.id, rel_path=animated_webp.name
    )
    await jobs.probe(
        await context_for("probe", {"asset_id": ingested.asset.id}),
        settings=settings,
        hardware=hardware,
    )
    await temp_db.execute(
        "UPDATE assets SET media_type = 'video' WHERE id = ?", (ingested.asset.id,)
    )

    await jobs.thumbnail(
        await context_for("thumbnail", {"asset_id": ingested.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    kinds = [one.kind.value for one in await content_store.derivatives(ingested.asset.id)]
    assert "thumb" in kinds


# --- derivatives ------------------------------------------------------------------------------


async def test_a_thumbnail_lands_in_the_cache_and_the_library_gains_nothing(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    library_root: LibraryRoot,
    video: Path,
) -> None:
    """The promise that lets someone point Sift at a folder they care about.

    Asserted rather than assumed, and asserted by counting what is in the library rather than by
    checking where the thumbnail went: those are different claims, and only one of them is the
    promise.
    """
    before = sorted(library_root.path.rglob("*"))

    await jobs.thumbnail(
        await context_for("thumbnail", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    assert sorted(library_root.path.rglob("*")) == before == [video]

    derivatives = await content_store.derivatives(ingested_video.asset.id)
    assert len(derivatives) == 1
    thumbnail = settings.cache_dir / derivatives[0].rel_cache_path
    assert thumbnail.is_file()
    assert thumbnail.stat().st_size == derivatives[0].size_bytes
    assert settings.cache_dir in thumbnail.parents


async def test_a_marked_moment_gets_its_own_picture_cut_at_that_moment(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The moment is half of the cache key, which is the whole of what makes two marks of one video
    two tiles rather than the same picture twice.

    Both stills are asked for and both must survive: a key that ignored the moment would leave one
    row, and the second render would overwrite the first in place.
    """
    asset_id = ingested_video.asset.id

    for at_ms in (0, 500):
        await jobs.loop_thumbnail(
            await context_for("loop_thumbnail", {"asset_id": asset_id, "at_ms": at_ms}),
            settings=settings,
            hardware=hardware,
        )

    derivatives = await content_store.derivatives(asset_id)
    assert len(derivatives) == 2
    assert {json.loads(one.params)["at_ms"] for one in derivatives} == {0, 500}
    for one in derivatives:
        picture = settings.cache_dir / one.rel_cache_path
        assert picture.is_file()
        assert picture.stat().st_size == one.size_bytes


async def test_asking_for_the_same_moment_twice_leaves_one_picture(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """Two marks beginning at the same millisecond of one video are one picture, and the sweep that
    catches up with old marks runs at every boot."""
    asset_id = ingested_video.asset.id

    for _ in range(2):
        await jobs.loop_thumbnail(
            await context_for("loop_thumbnail", {"asset_id": asset_id, "at_ms": 500}),
            settings=settings,
            hardware=hardware,
        )

    assert len(await content_store.derivatives(asset_id)) == 1


@pytest.mark.parametrize("at_ms", ["500", True, -1, None, 1.5])
async def test_a_moment_that_is_not_a_whole_number_of_milliseconds_is_refused(
    at_ms: object,
    ingested_video: Ingested,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The refusal is about the CACHE KEY as much as about the value.

    `{"at_ms": "500"}` and `{"at_ms": 500}` are two different keys for one picture and both would be
    built. `True` is the sharp one: `isinstance(True, int)` is true in Python, so an unguarded
    reader keys a still as `1`.
    """
    payload: dict[str, object] = {"asset_id": ingested_video.asset.id, "at_ms": at_ms}

    with pytest.raises(ValueError, match="non-negative at_ms"):
        await jobs.loop_thumbnail(
            await context_for("loop_thumbnail", payload), settings=settings, hardware=hardware
        )


async def test_a_derivative_is_never_left_half_written(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """A worker is killed halfway as a matter of course: the queue is built around it.

    ffmpeg killed mid-write leaves a partial file, and a half-written thumbnail is
    indistinguishable from a finished one to everything that reads it. So it is written beside the
    target and moved into place, and a move is atomic.
    """
    await jobs.thumbnail(
        await context_for("thumbnail", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )
    leftovers = [p.name for p in settings.cache_dir.rglob(".*.partial.*")]
    assert leftovers == []


async def test_rebuilding_the_same_derivative_replaces_it(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """Same asset, same kind, same settings is the same derivative. Running twice is not two."""
    for _ in range(2):
        await jobs.thumbnail(
            await context_for("thumbnail", {"asset_id": ingested_video.asset.id}),
            settings=settings,
            hardware=hardware,
        )
    assert len(await content_store.derivatives(ingested_video.asset.id)) == 1


async def test_the_cache_can_be_deleted_and_rebuilt(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The claim that lets a backup be the database and nothing else.

    Everything in the cache is disposable. Delete the lot, run the jobs again, and the library is
    exactly as it was, so the only thing worth backing up is the file that holds what the
    library knows, and this is the test that says so.
    """
    import shutil

    asset_id = ingested_video.asset.id
    await jobs.probe(
        await context_for("probe", {"asset_id": asset_id}), settings=settings, hardware=hardware
    )
    await jobs.thumbnail(
        await context_for("thumbnail", {"asset_id": asset_id}), settings=settings, hardware=hardware
    )
    before = await content_store.get(asset_id)

    shutil.rmtree(settings.cache_dir)

    await jobs.thumbnail(
        await context_for("thumbnail", {"asset_id": asset_id}), settings=settings, hardware=hardware
    )

    derivatives = await content_store.derivatives(asset_id)
    assert len(derivatives) == 1
    assert (settings.cache_dir / derivatives[0].rel_cache_path).is_file()
    after = await content_store.get(asset_id)
    assert after == before, "the library lost something a rebuildable cache should not have cost it"


# --- concurrency ------------------------------------------------------------------------------


async def test_a_file_ffmpeg_reads_but_draws_nothing_from_says_so_plainly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ffmpeg can exit cleanly having written no file at all.

    A duration that reads as zero, or a truncated file whose only frames will not decode, sends the
    seek past the end and there is no frame to save. ffmpeg is content; the move that follows is
    not, and unguarded it would fail with a bare FileNotFoundError naming a temporary path nobody
    has heard of: a traceback standing in for "there was no picture in this". Those are the tiles
    that come back as a broken image, so the reason has to survive as words.
    """

    async def wrote_nothing(argv: list[str], **_: object) -> None:
        return None

    monkeypatch.setattr(ffmpeg, "run", wrote_nothing)

    destination = tmp_path / "thumb.jpg"
    with pytest.raises(jobs.Unusable, match="produced no image"):
        await shared._render(["ffmpeg", "-i", "in.mp4", str(destination)], destination)

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == [], "the staging file was not left behind"


# --- the catch-up passes at their edges ---------------------------------------------------------


async def test_rebuilding_the_tiles_queues_one_per_file_and_a_second_press_queues_no_more(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    temp_db: Database,
) -> None:
    """One sweep rather than a write per file under a held request; deduped, so a second press
    queues nothing more."""
    await content_store.record_probe(ingested_video.asset.id, width=64, height=48)
    context = await context_for(jobs.REBUILD_THUMBNAILS, {})
    for _ in range(2):
        await jobs.rebuild_thumbnails(context, settings=settings, hardware=hardware)

    rows = await temp_db.fetch_all("SELECT payload FROM jobs WHERE type = ?", (jobs.THUMBNAIL,))
    assert [json.loads(row["payload"])["asset_id"] for row in rows] == [ingested_video.asset.id]


async def test_rebuilding_the_tiles_of_an_empty_library_says_nothing_was_queued(
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    context = await context_for(jobs.REBUILD_THUMBNAILS, {})
    await jobs.rebuild_thumbnails(context, settings=settings, hardware=hardware)
    after = await job_queue.get(context.job.id)
    assert after is not None and after.note is None, "an empty library was told it had work"


# --- the tile's still, chosen by what the frame shows ------------------------------------------


def _grey(values: Sequence[int]) -> bytes:
    """A candidate's grey pixels, as the still maker reads them."""
    return bytes(values)


def test_a_black_frame_a_fade_and_a_flat_frame_are_refused_and_a_picture_is_kept() -> None:
    side = jobs.STILL_LEVEL_SIZE * jobs.STILL_LEVEL_SIZE
    black = _grey([1] * side)
    flat = _grey([128] * side)
    fading = _grey([20 + (i % 8) for i in range(side)])
    picture = _grey([(i * 7) % 256 for i in range(side)])
    night = _grey([(i % 40) if i % 3 else 5 for i in range(side)])

    assert not jobs.still_levels(black).worth_showing
    assert not jobs.still_levels(flat).worth_showing
    assert not jobs.still_levels(fading).worth_showing
    assert jobs.still_levels(picture).worth_showing
    # A dark scene with something in it is a picture, not a fade: dim, but it varies.
    assert jobs.still_levels(night).worth_showing


def test_the_candidates_are_spread_across_the_picture_after_the_first_frame() -> None:
    moments = jobs.still_candidates(10_000)
    assert moments[0] == 500
    assert moments[-1] == 8_000
    assert list(moments) == sorted(moments)
    assert jobs.still_candidates(0) == ()


def test_a_frame_that_read_back_nothing_is_measured_as_black_and_never_shown() -> None:
    assert jobs.still_levels(b"") == jobs.StillLevels(mean=0.0, spread=0.0)
    assert not jobs.still_levels(b"").worth_showing


def test_a_running_time_too_short_to_spread_across_tries_each_moment_once() -> None:
    """A clip of a few milliseconds rounds several fractions to the same moment; cutting one
    moment twice would cost a read and show the same frame."""
    assert jobs.still_candidates(3) == (1, 2)


def test_where_no_candidate_is_worth_showing_the_one_that_shows_the_most_is_kept() -> None:
    """A file dark from end to end still gets its least dark frame rather than its first."""
    side = jobs.STILL_LEVEL_SIZE * jobs.STILL_LEVEL_SIZE
    dark = media.CutStill(still=Path("dark.jpg"), levels=_grey([1] * side))
    darker_but_varied = media.CutStill(
        still=Path("varied.jpg"), levels=_grey([(i % 2) * 6 for i in range(side)])
    )

    assert thumbnails._choose([]) is None
    assert thumbnails._choose([(0, dark), (500, darker_but_varied)]) == (500, darker_but_varied)


async def test_a_video_no_moment_of_which_could_be_cut_says_so_plainly(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def nothing_cut(*_args: object, **_kwargs: object) -> list[object]:
        return []

    monkeypatch.setattr(thumbnails, "_cut_stills", nothing_cut)

    with pytest.raises(shared.Unusable, match="produced no image"):
        await thumbnails._cut_by_content(
            tmp_path / "clip.mp4", tmp_path / "thumb.jpg", 4_000, settings=settings
        )
    assert not (tmp_path / "thumb.jpg").exists()


async def test_a_still_cut_somewhere_new_builds_the_hover_clip_again_from_there(
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The hover clip starts at the still, so a clip built from the old moment would make the
    tile jump under a pointer: it is queued again from the new one."""
    clip = draw(
        library_root.path / "opens-dark.mp4",
        "testsrc2=size=160x120:rate=10",
        4,
        "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable=lt(t\\,1.5)",
    )
    ingested = await take_in(content_store, library_root, clip, settings)
    asset_id = ingested.asset.id
    await content_store.record_probe(asset_id, width=160, height=120, duration_ms=4_000)
    await content_store.add_derivative(
        asset_id, DerivativeKind.PREVIEW, extension="mp4", size_bytes=1
    )
    context = await context_for(jobs.THUMBNAIL, {"asset_id": asset_id})

    await jobs.thumbnail(context, settings=settings, hardware=hardware)

    queued = await context.queue.list(job_type=jobs.PREVIEW)
    assert [job.payload for job in queued.jobs] == [{"asset_id": asset_id}]


async def _still_levels_of(path: Path, settings: Settings) -> jobs.StillLevels:
    """The still maker's own measure, read back from a picture file by the same filter."""
    frame = await kernel_subprocess.capture(
        [
            settings.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            jobs.still_level_filter(),
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        time_limit=60,
    )
    assert len(frame) == jobs.STILL_LEVEL_SIZE * jobs.STILL_LEVEL_SIZE, (
        "the still was not read back"
    )
    return jobs.still_levels(frame)


async def test_a_video_that_opens_black_gets_its_still_from_where_the_picture_begins(
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The black tile: a video whose first second and a half is black (a fade in, a slate) would
    have that black frame for its still. The frame is measured, and the still is cut where the
    picture is, from moments spread across the file."""
    clip = draw(
        library_root.path / "opens-dark.mp4",
        "testsrc2=size=160x120:rate=10",
        4,
        "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable=lt(t\\,1.5)",
    )
    ingested = await take_in(content_store, library_root, clip, settings)
    await content_store.record_probe(ingested.asset.id, width=160, height=120, duration_ms=4_000)

    await jobs.thumbnail(
        await context_for(jobs.THUMBNAIL, {"asset_id": ingested.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    asset = await content_store.get(ingested.asset.id)
    assert asset is not None and asset.still_at_ms is not None
    assert asset.still_at_ms >= 1_500, "the still was cut inside the black opening"
    thumb = content_store.derivative_path(ingested.asset.id, DerivativeKind.THUMB, extension="jpg")
    assert (await _still_levels_of(thumb, settings)).worth_showing


async def test_a_video_whose_first_frame_is_a_picture_keeps_it(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    await content_store.record_probe(
        ingested_video.asset.id, width=320, height=240, duration_ms=VIDEO_SECONDS * 1000
    )
    await jobs.thumbnail(
        await context_for(jobs.THUMBNAIL, {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )
    asset = await content_store.get(ingested_video.asset.id)
    assert asset is not None and asset.still_at_ms == 0
