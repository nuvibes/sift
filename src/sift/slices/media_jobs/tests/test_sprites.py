# SPDX-License-Identifier: AGPL-3.0-or-later
"""The scrub strip, run against real files with real ffmpeg."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# A fingerprint write records an event through the ledger door, and the door writes into the
# workbench's own table, so a database built without that component has nowhere to put it.
# Imported for the registration, nothing else. Without it this module passes only when some
# OTHER module collected in the same run happens to have imported it, and fails on its own;
# the content suite carries the same line for the same reason.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel import media
from sift.kernel import sampling as sampler
from sift.kernel.config import Settings
from sift.kernel.content import (
    ContentStore,
    DerivativeKind,
    Ingested,
)
from sift.kernel.hardware import HardwareReport
from sift.slices.media_jobs import (
    ffmpeg,
    fingerprints,
    jobs,
    sprites,
    tuning,
)
from sift.slices.media_jobs.tests.conftest import VIDEO_SECONDS
from sift.slices.media_jobs.tests.support import Context, watch_launches
from sift.testing.fixtures import LibraryRoot

pytestmark = [pytest.mark.integration]


# --- derivatives ------------------------------------------------------------------------------


async def test_a_sprite_holds_every_sampled_frame_in_one_sheet(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    await jobs.probe(
        await context_for("probe", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )
    await jobs.sprite(
        await context_for("sprite", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    sprites = [
        d
        for d in await content_store.derivatives(ingested_video.asset.id)
        if d.kind is DerivativeKind.SPRITE
    ]
    assert len(sprites) == 1
    sheet = settings.cache_dir / sprites[0].rel_cache_path
    assert sheet.is_file()
    # Five seconds at the sprite rate (twice a second for a short clip) is ten tiles, which is
    # two rows of six. The strip is built from `sampler.sprite_frames` rather than the thirty-frame
    # fingerprint ladder, because a scrubber needs a frame every few seconds and the ladder gives a
    # long video one every four minutes.
    assert '"columns": 6' in sprites[0].params or '"columns":6' in sprites[0].params
    assert '"rows": 2' in sprites[0].params or '"rows":2' in sprites[0].params


async def test_one_unreadable_moment_does_not_lose_the_whole_strip(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A moment that yields no frame is a missing tile, not a lost scrub strip.

    The renumbering in `_render_sprite` exists for exactly this, and it must hold on a non-zero
    exit as well as on an exit 0 that wrote nothing, or one bad moment throws away every good tile.

    A file that declares its audio's length (20.13s) while its 1 fps video stream stops at 19.0s
    puts the last moment past the last frame. ffmpeg decodes nothing, opens the JPEG encoder with
    no frame to take a color range from, and refuses with `Non full-range YUV is non-standard`, a
    message about color standards for a fault that is "there is no frame there".
    """
    await jobs.probe(
        await context_for("probe", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    real_run = media.run
    third = sampler.sprite_frames(VIDEO_SECONDS * 1000)[2]

    async def refuses_the_third(argv: list[str], **kwargs: Any) -> bytes:
        # The tiles come from ONE process; a refusal there is read again a tile at a time,
        # and the third tile's own moment refuses. Both shapes of the fault, in one fake.
        if argv.count("-i") > 1 or media.seconds(third) in argv:
            raise ffmpeg.FFmpegError(
                "ffmpeg failed: [mjpeg @ 0x0] Non full-range YUV is non-standard, "
                "set strict_std_compliance to at most unofficial to use it."
            )
        return await real_run(argv, **kwargs)

    monkeypatch.setattr(media, "run", refuses_the_third)

    await jobs.sprite(
        await context_for("sprite", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    sprites = [
        d
        for d in await content_store.derivatives(ingested_video.asset.id)
        if d.kind is DerivativeKind.SPRITE
    ]
    assert len(sprites) == 1, "one bad moment took the whole strip with it"
    assert (settings.cache_dir / sprites[0].rel_cache_path).is_file()


# --- concurrency ------------------------------------------------------------------------------


async def test_a_gif_gets_a_thumbnail_and_a_sprite(
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    animation: Path,
) -> None:
    """It has a timeline, so it gets the same three derivatives a video does, and its sprite is
    built through the single-decode path (a GIF cannot seek cheaply), which must still produce a real
    sheet, not just a derivative row. Real ffmpeg, the one on the machine running the tests; the
    image may run a newer one, so the container's conformance run confirms the decode there."""
    from sift.slices.media_jobs.tests.conftest import take_in

    taken = await take_in(content_store, library_root, animation, settings)
    context = await context_for("probe", {"asset_id": taken.asset.id})
    await jobs.probe(context, settings=settings, hardware=hardware)

    children = {child.type for child in await context.queue.children(context.job.id)}
    assert children == {"thumbnail", "preview", "sprite", "fingerprint_file"}

    await jobs.thumbnail(
        await context_for("thumbnail", {"asset_id": taken.asset.id}),
        settings=settings,
        hardware=hardware,
    )
    await jobs.sprite(
        await context_for("sprite", {"asset_id": taken.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    derivatives = await content_store.derivatives(taken.asset.id)
    kinds = {d.kind for d in derivatives}
    assert kinds == {DerivativeKind.THUMB, DerivativeKind.SPRITE}
    # The single-decode sprite really wrote a sheet, not just a row pointing at nothing.
    sprite_row = next(d for d in derivatives if d.kind is DerivativeKind.SPRITE)
    sheet = settings.cache_dir / sprite_row.rel_cache_path
    assert sheet.is_file()
    assert sheet.stat().st_size > 0


# --- the three passes over a library that predates a feature --------------------------------------
#
# Each walks a batch, and each is asked for again while there is more. What is pinned here is the
# case where there is nothing to do: an install where the feature was always on runs all three on
# every start, so "nothing to do" is the ordinary path rather than an edge, and it must not ask for
# itself again: a pass that re-queues on an empty library never stops.


async def test_a_file_no_frame_can_be_read_from_refuses_to_build_a_scrub_strip(
    ingested_video: Ingested,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A strip built from nothing would be a scrubber that scrubs through an empty sheet.

    ffmpeg can be asked for frames, exit cleanly and write none: a seek past the true end does
    exactly that. What comes back is what actually landed on disk, and none of it is a refusal
    rather than an empty sheet nobody can use.
    """
    # Probed first: a file whose duration is not known yet has no timeline, and the strip job
    # returns before it reads anything, which passes this test for entirely the wrong reason.
    await jobs.probe(
        await context_for("probe", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )
    monkeypatch.setattr(sprites, "_renumber", lambda _staging: 0)

    with pytest.raises(ffmpeg.FFmpegError, match="no frames could be read"):
        await jobs.sprite(
            await context_for("sprite", {"asset_id": ingested_video.asset.id}),
            settings=settings,
            hardware=hardware,
        )


async def test_a_still_is_not_given_a_scrub_strip(
    ingested_picture: Ingested,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    content_store: ContentStore,
) -> None:
    """There is nothing to scrub through, so the job ends rather than building an empty sheet.

    A photograph reaches this job at all only because the queue does not know one kind of file from
    another; the guard is what makes that harmless.
    """
    await jobs.probe(
        await context_for("probe", {"asset_id": ingested_picture.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    await jobs.sprite(
        await context_for("sprite", {"asset_id": ingested_picture.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    kinds = {one.kind for one in await content_store.derivatives(ingested_picture.asset.id)}
    assert DerivativeKind.SPRITE not in kinds


async def test_a_GIF_that_decodes_to_nothing_leaves_the_strip_to_refuse(
    animation: Path,
    tmp_path: Path,
    context_for: Context,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GIF's tiles come from one decode, and a decode that writes no frame is possible.

    Nothing is moved into the staging directory, so the caller's own check (the one that refuses
    an empty sheet) is what reports it. Handled here rather than raising, so there is one place
    that says "no frames could be read" instead of two saying it differently.
    """
    staging = tmp_path / "staging"
    staging.mkdir()

    async def decodes_nothing(*_args: object, **_kwargs: object) -> bytes:
        return b""

    monkeypatch.setattr(ffmpeg, "run", decodes_nothing)
    context = await context_for("sprite", {})

    await sprites._decode_sprite_tiles(
        animation, staging, count=4, settings=settings, context=context
    )

    assert list(staging.glob("*.jpg")) == []


# --- one process per file per pass ---------------------------------------------------------------
#
# One launch per moment would make a video's probe 55 launches (one per hash frame, one per
# stash-box still) and its scrub strip one per tile, up to four hundred. Over a share every launch
# is an open and a seek across the wire. These pin the count, and pin that the batched form is
# the SAME BYTES as the one-moment form, which is the whole of what makes it safe for a
# fingerprint.


async def test_a_scrub_strip_is_cut_from_one_process(
    ingested_video: Ingested,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Probed first: a strip is only cut for a file with a timeline, and probing is what records it.
    await jobs.probe(
        await context_for("probe", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )
    launches = watch_launches(monkeypatch)

    await jobs.sprite(
        await context_for("sprite", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    tiles = [argv for argv in launches if "-q:v" in argv and "tile=" not in " ".join(argv)]
    assert len(tiles) == 1
    assert tiles[0].count("-i") == len(sampler.sprite_frames(VIDEO_SECONDS * 1000))


# --- the scrub strip's grid ----------------------------------------------------------------------


def test_a_gap_in_the_tiles_is_filled_rather_than_closed_up(tmp_path: Path) -> None:
    """Closing the gap would move every later tile onto somebody else's moment.

    A scrubber maps a position in the video to a tile index, so tile N has to stay moment N. The
    filler is the nearest tile that did come out, which is the honest thing to show for a moment
    that cannot be read: the frame beside it.
    """
    for position, body in ((0, b"first"), (3, b"fourth")):
        (tmp_path / f"{position:04d}.jpg").write_bytes(body)

    sprites._fill_gaps(tmp_path, 5)

    present = sorted(one.name for one in tmp_path.glob("*.jpg"))
    assert present == ["0000.jpg", "0001.jpg", "0002.jpg", "0003.jpg", "0004.jpg"]
    assert (tmp_path / "0001.jpg").read_bytes() == b"first"
    assert (tmp_path / "0002.jpg").read_bytes() == b"fourth"
    assert (tmp_path / "0004.jpg").read_bytes() == b"fourth"


def test_no_tiles_at_all_are_left_alone(tmp_path: Path) -> None:
    """A sheet of copies of nothing is not an answer; the caller raises "no frames could be read",
    which is."""
    sprites._fill_gaps(tmp_path, 5)

    assert list(tmp_path.glob("*.jpg")) == []


async def test_the_sheet_is_tiled_on_the_grid_the_row_records(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mismatch, end to end: a strip tiled to the frames that CAME OUT while its path and its
    stored `params` were both chosen from the frames that were ASKED FOR.

    A scrubber reads the stored columns and rows, so any file that lost a tile would be sliced on
    the wrong grid for the life of the library. Here all but the first moment refuse, which
    without the filling would leave a one-tile sheet under a row claiming six columns.
    """
    await jobs.probe(
        await context_for("probe", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )
    real_run = media.run

    async def only_the_first(argv: list[str], **kwargs: Any) -> bytes:
        # Moment zero carries no `-ss` at all, which is what makes it the one that survives: the
        # chunk refuses, every moment is then read on its own, and only the first produces a tile.
        if argv.count("-i") > 1 or "-ss" in argv:
            raise ffmpeg.FFmpegError("ffmpeg failed: there is no frame at that moment")
        return await real_run(argv, **kwargs)

    monkeypatch.setattr(media, "run", only_the_first)

    await jobs.sprite(
        await context_for("sprite", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    sprites = [
        one
        for one in await content_store.derivatives(ingested_video.asset.id)
        if one.kind is DerivativeKind.SPRITE
    ]
    assert len(sprites) == 1
    columns, rows = ffmpeg.sprite_grid(len(sampler.sprite_frames(VIDEO_SECONDS * 1000)))
    sheet = ffmpeg.parse_probe(
        await ffmpeg.run_json(
            ffmpeg.probe_args(settings.cache_dir / sprites[0].rel_cache_path, settings=settings)
        )
    )
    assert sheet.width == columns * tuning.SPRITE_TILE_WIDTH, "the sheet is not the stored grid"
    assert rows > 1, "the fixture stopped being able to show this"


# --- one decode for the strip and the fingerprints ------------------------------------------------


async def test_a_short_video_gives_its_strip_and_fingerprints_from_one_decode(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a machine whose rates were measured, a short clip is decoded once for its strip and its
    fingerprints: one decode and the sheet's stitch, the fingerprints the ones the file's own job
    reads, and that job then starts no tool."""
    from sift.kernel import subprocess as kernel_subprocess

    asset_id = ingested_video.asset.id
    await jobs.probe(
        await context_for("probe", {"asset_id": asset_id}), settings=settings, hardware=hardware
    )
    source = await media.resolve_decodable(content_store, asset_id, settings=settings)
    probed = await fingerprints.probed_of(content_store, source, settings=settings)
    frame, whole = await fingerprints._fingerprint(source, probed, settings=settings)
    scene = await fingerprints._video_phash(source, probed, settings=settings)

    async def measured(_path: Path) -> media.ReadRates:
        return media.ReadRates(decode_fps=600.0, seek_seconds=0.04)

    started: list[list[str]] = []
    real_run, real_capture = kernel_subprocess.run, kernel_subprocess.capture

    async def counted_run(argv: list[str], **kwargs: Any) -> Any:
        started.append(argv)
        return await real_run(argv, **kwargs)

    async def counted_capture(argv: list[str], **kwargs: Any) -> bytes:
        started.append(argv)
        return await real_capture(argv, **kwargs)

    monkeypatch.setattr(kernel_subprocess, "run", counted_run)
    monkeypatch.setattr(kernel_subprocess, "capture", counted_capture)
    await jobs.sprite(
        await context_for("sprite", {"asset_id": asset_id}),
        settings=settings,
        hardware=hardware,
        read_rates=measured,
    )

    assert len(started) == 2, started
    assert await content_store.lacking_derivative(DerivativeKind.SPRITE, [asset_id]) == set()
    asset = await content_store.get(asset_id)
    assert asset is not None
    assert (asset.phash, asset.videohash, asset.video_phash) == (frame, whole, scene)
    assert asset.oshash
    started.clear()
    await jobs.fingerprint_arrival(
        await context_for(jobs.FINGERPRINT_FILE, {"asset_id": asset_id}), settings=settings
    )
    assert started == []


async def test_an_unmeasured_machine_seeks_the_strip_and_leaves_the_fingerprints(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """Without measured rates the frame count would price the strip's seeks too high and decode a
    long file whole: the strip seeks as before and the fingerprints are their own job's."""
    asset_id = ingested_video.asset.id
    await jobs.probe(
        await context_for("probe", {"asset_id": asset_id}), settings=settings, hardware=hardware
    )

    async def never(_path: Path) -> None:
        return None

    await jobs.sprite(
        await context_for("sprite", {"asset_id": asset_id}),
        settings=settings,
        hardware=hardware,
        read_rates=never,
    )
    asset = await content_store.get(asset_id)
    assert asset is not None and asset.phash is None


@pytest.mark.parametrize(
    "way", ["seeking_is_quicker", "unplanned", "decode_refused", "fingerprints_refused"]
)
async def test_a_single_decode_that_cannot_serve_the_fingerprints_still_gives_the_strip(
    way: str,
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seeking priced quicker, moments that cannot be planned, a decode the tool refuses, or
    fingerprints that fail to record: the strip is built either way and the fingerprints are left
    to the file's own job."""
    asset_id = ingested_video.asset.id
    await jobs.probe(
        await context_for("probe", {"asset_id": asset_id}), settings=settings, hardware=hardware
    )
    decode_fps = 0.001 if way == "seeking_is_quicker" else 600.0

    async def measured(_path: Path) -> media.ReadRates:
        return media.ReadRates(decode_fps=decode_fps, seek_seconds=0.04)

    def unplanned(_probed: Any) -> list[media.FrameRequest]:
        raise ValueError("no moment to read")

    async def refused(*_args: Any, **_kwargs: Any) -> Any:
        raise ffmpeg.FFmpegError("the tool refused the file")

    async def unrecorded(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("the fingerprint could not be written")

    if way == "unplanned":
        monkeypatch.setattr(fingerprints, "fingerprint_requests", unplanned)
    elif way == "decode_refused":
        monkeypatch.setattr(media, "decode_once", refused)
    elif way == "fingerprints_refused":
        monkeypatch.setattr(fingerprints, "fingerprint_one", unrecorded)
    await jobs.sprite(
        await context_for("sprite", {"asset_id": asset_id}),
        settings=settings,
        hardware=hardware,
        read_rates=measured,
    )

    assert await content_store.lacking_derivative(DerivativeKind.SPRITE, [asset_id]) == set()
    asset = await content_store.get(asset_id)
    assert asset is not None and asset.phash is None
