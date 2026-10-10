# SPDX-License-Identifier: AGPL-3.0-or-later
"""The hover clip, run against real files with real ffmpeg."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

# A fingerprint write records an event through the ledger door, and the door writes into the
# workbench's own table, so a database built without that component has nowhere to put it.
# Imported for the registration, nothing else. Without it this module passes only when some
# OTHER module collected in the same run happens to have imported it, and fails on its own;
# the content suite carries the same line for the same reason.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel import sampling as sampler
from sift.kernel.config import Settings
from sift.kernel.content import (
    ContentStore,
    DerivativeKind,
    Ingested,
    perceptual,
)
from sift.kernel.db import Database
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import (
    JobQueue,
    registry,
    worker_pool,
)
from sift.kernel.media import Accelerator
from sift.slices import performance
from sift.slices.media_jobs import (
    ffmpeg,
    jobs,
    previews,
    tuning,
)
from sift.slices.media_jobs.tests.conftest import draw, take_in
from sift.slices.media_jobs.tests.support import Context
from sift.testing.fixtures import LibraryRoot
from sift.wiring import imports as wiring_imports

pytestmark = [pytest.mark.integration]


# --- derivatives ------------------------------------------------------------------------------


async def test_a_preview_is_small_enough_to_have_arrived_and_starts_at_the_first_frame(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    video: Path,
) -> None:
    """The two properties that make a hover preview feel instant, checked on the file itself.

    "Instant" is not a property of the code that plays it: it is a property of the file: small
    enough to have been fetched before the cursor arrived, and opening on the same frame the tile
    was already showing, so nothing jumps and nothing seeks.
    """
    await jobs.preview(
        await context_for("preview", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    derivatives = await content_store.derivatives(ingested_video.asset.id)
    assert [d.kind for d in derivatives] == [DerivativeKind.PREVIEW]
    clip = settings.cache_dir / derivatives[0].rel_cache_path
    assert clip.is_file()

    # Per second of clip, so the band goes on meaning the same thing whichever shape was asked for.
    # A flat number is only a check while the length never moves.
    seconds_of_clip = sampler.preview_shape(sampler.DEFAULT_PREVIEW_SHAPE).total_seconds
    size = clip.stat().st_size
    floor = tuning.PREVIEW_MIN_BYTES_PER_SECOND * seconds_of_clip
    ceiling = tuning.PREVIEW_MAX_BYTES_PER_SECOND * seconds_of_clip + tuning.PREVIEW_OVERHEAD_BYTES
    assert floor <= size <= ceiling, (
        f"a {size} byte preview is outside the band that makes it feel instant"
    )

    # Frame zero of the clip is frame zero of the source. Compared by fingerprint rather than by
    # bytes, because the clip has been re-encoded at a fraction of the size: the bytes are all
    # different and the picture is the same, which is exactly what the fingerprint is for.
    from sift.kernel.tests.test_perceptual import grey_square

    apart = perceptual.distance(phash_of(grey_square(video)), phash_of(grey_square(clip)))
    assert apart is not None
    assert apart <= 12, f"the preview opens on a different frame than the tile does ({apart} bits)"


def phash_of(frame: bytes) -> str:
    return perceptual.phash(frame)


async def test_a_still_gets_no_preview_file(
    ingested_picture: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """A still does not move. An empty derivative row would promise a file that is not there."""
    await jobs.preview(
        await context_for("preview", {"asset_id": ingested_picture.asset.id}),
        settings=settings,
        hardware=hardware,
    )
    assert await content_store.derivatives(ingested_picture.asset.id) == []


# --- concurrency ------------------------------------------------------------------------------


async def test_a_gif_previews_as_a_clip_a_browser_can_play(
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    animation: Path,
) -> None:
    """A GIF gets a generated clip, the same as a video, and that is a deliberate reading of the
    rule rather than an oversight.

    The rule says a GIF previews "as itself", which is about not spending a re-encode to gain
    nothing, because a GIF is already a short silent loop. But a GIF is stored uncompressed
    between frames, so a long one is enormous for what it shows, and the whole point of a hover
    preview is that it has arrived before the cursor did. A few seconds of H.264 is a fraction of
    the bytes and every tile then plays the same way, whatever the source was.
    """
    from sift.slices.media_jobs.tests.conftest import take_in

    taken = await take_in(content_store, library_root, animation, settings)
    await jobs.probe(
        await context_for("probe", {"asset_id": taken.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    await jobs.preview(
        await context_for("preview", {"asset_id": taken.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    derivatives = await content_store.derivatives(taken.asset.id)
    previews = [d for d in derivatives if d.kind is DerivativeKind.PREVIEW]
    assert len(previews) == 1
    clip = settings.cache_dir / previews[0].rel_cache_path
    assert clip.is_file()
    assert clip.stat().st_size <= tuning.PREVIEW_MAX_BYTES_PER_SECOND * 10
    assert clip.suffix == ".mp4"


async def test_an_animated_avif_previews_its_frames_not_its_still_cover(
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """An animated AVIF as ffmpeg writes one: its still cover is the first video stream and its
    frames the second. Cut from the cover, the clip would be one frame held for a second, and the
    tile under the pointer would not move. Real encoder, real file: the clip has to run as long as
    the animation does."""
    animation = draw(library_root.path / "moving.avif", "testsrc2=size=64x64:rate=10", 2)
    taken = await take_in(content_store, library_root, animation, settings)
    assert taken.asset.media_type == "gif"
    await jobs.probe(
        await context_for("probe", {"asset_id": taken.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    await jobs.preview(
        await context_for("preview", {"asset_id": taken.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    previews = [
        d
        for d in await content_store.derivatives(taken.asset.id)
        if d.kind is DerivativeKind.PREVIEW
    ]
    assert len(previews) == 1
    clip = settings.cache_dir / previews[0].rel_cache_path
    made = ffmpeg.parse_probe(await ffmpeg.run_json(ffmpeg.probe_args(clip, settings=settings)))
    assert made.duration_ms is not None and made.duration_ms >= 1_800, (
        f"a {made.duration_ms} ms clip of a two-second animation was cut from its still cover"
    )


# --- the three passes over a library that predates a feature --------------------------------------
#
# Each walks a batch, and each is asked for again while there is more. What is pinned here is the
# case where there is nothing to do: an install where the feature was always on runs all three on
# every start, so "nothing to do" is the ordinary path rather than an edge, and it must not ask for
# itself again: a pass that re-queues on an empty library never stops.


def _accelerator(*encoders: str, cuda: bool = False) -> Accelerator:
    """An accelerator for a machine whose report claims these encoders."""
    return Accelerator(
        HardwareReport(
            cpu_count=4,
            total_ram_bytes=8 << 30,
            worker_concurrency=2,
            cuda=cuda,
            rocm=False,
            transcode_encoders=encoders,
            warnings=(),
        )
    )


async def test_a_hardware_encoder_that_fails_falls_back_to_the_processor(
    video: Path,
    tmp_path: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never no preview. A machine without a working hardware encoder is the ordinary case.

    The encoder is listed by ffmpeg and still fails at runtime more often than not: a container
    may hold the device without the library that drives it, and a GPU can already be busy. So the
    answer is a preview built by the processor, slightly later, rather than none at all.
    """
    # Counted rather than read off the arguments: what the hardware path's argument list looks
    # like is ffmpeg's business and it changes, and a classifier that guesses wrong reads as the
    # fallback not happening.
    # A machine that really has the device, so the hardware argument list is built and the attempt
    # is a genuine one. Without this the hardware path fails while assembling its arguments and the
    # processor's encode becomes the FIRST call, which is a different test wearing this name.
    monkeypatch.setattr(ffmpeg, "render_node", lambda: "/dev/dri/renderD128")
    calls: list[list[str]] = []

    async def render(argv: list[str], _destination: Path, **_: object) -> None:
        calls.append(argv)
        if len(calls) == 1:
            raise ffmpeg.FFmpegError("no such device")

    monkeypatch.setattr(previews, "_render", render)

    await previews._encode_preview(
        video,
        tmp_path / "preview.mp4",
        pieces=(sampler.Piece(0, 2000),),
        accelerator=_accelerator("h264_vaapi"),
        settings=settings,
    )

    assert len(calls) == 2, "it did not try again after the hardware encoder refused"
    assert "libx264" in calls[1], "the second attempt was not the processor's"


async def test_the_retry_drops_the_hardware_DECODER_as_well_as_the_encoder(
    video: Path,
    tmp_path: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both halves ask the same card for the same thing, so the machine that fails one fails both.

    Retrying with the decoder still attached runs straight back into the failure that caused the
    retry, and turns one wasted attempt into two. Nothing about the finished preview would show it.
    """
    calls: list[list[str]] = []

    async def render(argv: list[str], _destination: Path, **_: object) -> None:
        calls.append(argv)
        if len(calls) == 1:
            raise ffmpeg.FFmpegError("the driver is too old")

    monkeypatch.setattr(previews, "_render", render)

    await previews._encode_preview(
        video,
        tmp_path / "preview.mp4",
        pieces=(sampler.Piece(0, 2000),),
        accelerator=_accelerator("h264_nvenc", cuda=True),
        settings=settings,
    )

    assert len(calls) == 2, "it did not try again after the hardware path refused"
    assert "-hwaccel" in calls[0], "the first attempt was not the accelerated one"
    assert "-hwaccel" not in calls[1], "the retry asked the same broken card to decode again"
    assert "libx264" in calls[1]


async def test_a_machine_that_can_only_DECODE_on_its_card_still_gets_a_retry(
    video: Path,
    tmp_path: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unusual and real: ffmpeg carrying the CUDA decoder on a box with no working NVENC.

    Keyed on the encoder alone, such a machine would pass decode flags with NO retry standing
    behind them, so a broken driver would mean no preview at all rather than a slower one. The
    accelerated attempt happens when EITHER half has something to offer.
    """
    calls: list[list[str]] = []

    async def render(argv: list[str], _destination: Path, **_: object) -> None:
        calls.append(argv)
        if len(calls) == 1:
            raise ffmpeg.FFmpegError("cuda device creation failed")

    monkeypatch.setattr(previews, "_render", render)

    await previews._encode_preview(
        video,
        tmp_path / "preview.mp4",
        pieces=(sampler.Piece(0, 2000),),
        accelerator=_accelerator(cuda=True),
        settings=settings,
    )

    assert len(calls) == 2, "the decode flags were passed with no retry behind them"
    assert "-hwaccel" not in calls[1]


async def test_a_machine_with_no_card_at_all_encodes_once_and_asks_for_nothing(
    video: Path,
    tmp_path: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary machine, and the known positive for the two cases above: with neither half to
    offer there is nothing to attempt and nothing to retry, so it must encode exactly once."""
    calls: list[list[str]] = []

    async def render(argv: list[str], _destination: Path, **_: object) -> None:
        calls.append(argv)

    monkeypatch.setattr(previews, "_render", render)

    await previews._encode_preview(
        video,
        tmp_path / "preview.mp4",
        pieces=(sampler.Piece(0, 2000),),
        accelerator=_accelerator(),
        settings=settings,
    )

    assert len(calls) == 1
    assert "-hwaccel" not in calls[0]


async def test_a_preview_that_comes_out_bigger_than_it_should_is_recorded_anyway(
    ingested_video: Ingested,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    content_store: ContentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Noticed, not refused.

    An oversized preview is a sign the ladder was wrong for this file, which is worth a line in the
    log. It is still a working preview, and throwing it away would leave the file with none at all,
    which is worse for whoever is looking at the grid.
    """
    monkeypatch.setattr(tuning, "PREVIEW_MAX_BYTES_PER_SECOND", 1)

    await jobs.preview(
        await context_for("preview", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    previews = [
        one
        for one in await content_store.derivatives(ingested_video.asset.id)
        if one.kind is DerivativeKind.PREVIEW
    ]
    assert len(previews) == 1


async def test_a_hardware_encode_that_works_does_not_also_encode_on_the_processor(
    video: Path,
    tmp_path: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other side of the fallback, and it is worth its own case.

    A fallback that ran every time would encode twice on a machine where the hardware path works
    perfectly, and nothing about the file it produced would look wrong, so only the count says so.
    """
    monkeypatch.setattr(ffmpeg, "render_node", lambda: "/dev/dri/renderD128")
    calls: list[list[str]] = []

    async def render(argv: list[str], _destination: Path, **_: object) -> None:
        calls.append(argv)

    monkeypatch.setattr(previews, "_render", render)

    await previews._encode_preview(
        video,
        tmp_path / "preview.mp4",
        pieces=(sampler.Piece(0, 2000),),
        accelerator=_accelerator("h264_vaapi"),
        settings=settings,
    )

    assert len(calls) == 1
    assert "libx264" not in calls[0], "it fell back even though the hardware encode worked"


async def test_a_preview_records_the_shape_it_was_built_to(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """Without this, which previews are out of date is something somebody has to remember.

    `derivatives` is unique on the settings a thing was built with, so recording them turns "is
    this clip the shape that is set" into a question the database answers.
    """
    await jobs.preview(
        await context_for("preview", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
        chosen_shape=_shape("brief"),
    )

    # Written out rather than compared against `preview_recipe`, which is what builds the thing
    # being checked. A test whose two sides come from one function agrees with itself whatever that
    # function says, so a changed version number, which is the whole mechanism for making an
    # encoder change findable, would pass unnoticed. Updating this pin is meant to be part of
    # bumping it.
    (clip,) = await content_store.derivatives(ingested_video.asset.id)
    assert json.loads(clip.params) == {"clip_ms": 6000, "cut_ms": 1500, "v": 1}


async def test_the_shape_stored_on_performance_is_the_shape_each_queued_preview_is_cut_to(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole way from the stored setting to the clip, through the handler the queue runs.

    The composition root's own reader and the handler it registers are what a worker calls, so
    the shape is read from the settings as each file is built: changing it between two files
    changes the second clip, with no restart in between.
    """

    class Stored:
        def __init__(self) -> None:
            self.values: dict[str, Any] = {}

        async def get_app(self, key: str) -> Any:
            return self.values[key]

    stored = Stored()
    # The handlers this module's fixtures claimed are set aside, so the application's own can be
    # claimed in their place; the registry is put back after the test.
    monkeypatch.setattr(registry, "_HANDLERS", {})
    wiring_imports._register_media(
        stored,  # type: ignore[arg-type]
        settings,
        hardware,
        stored,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        Accelerator(hardware),
    )
    queued = worker_pool.registered_handlers()[jobs.PREVIEW]

    for shape in ("brief", "full"):
        stored.values[performance.PREVIEW_SHAPE_KEY] = shape
        await queued(await context_for("preview", {"asset_id": ingested_video.asset.id}))
        (clip,) = await content_store.derivatives(ingested_video.asset.id)
        assert json.loads(clip.params) == jobs.preview_recipe(sampler.preview_shape(shape))


async def test_changing_the_shape_replaces_the_clip_rather_than_leaving_two(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The one that keeps a library from carrying a copy of every clip it has ever had.

    The settings are folded into the filename, so a clip built one way and a clip built another are
    two files. Left alone, a library that has been through three shapes holds three hover clips per
    video, and nothing would ever say so, because the newest is the one served.
    """
    for shape in ("full", "brief"):
        await jobs.preview(
            await context_for("preview", {"asset_id": ingested_video.asset.id}),
            settings=settings,
            hardware=hardware,
            chosen_shape=_shape(shape),
        )

    derivatives = await content_store.derivatives(ingested_video.asset.id)
    assert len(derivatives) == 1, "the clip built to the old shape was left behind"
    assert json.loads(derivatives[0].params) == jobs.preview_recipe(sampler.preview_shape("brief"))

    # And the file went with the row. A row that outlives its file is a broken picture; a file that
    # outlives its row is disk nobody can reach.
    clips = list((settings.cache_dir / Path(derivatives[0].rel_cache_path).parent).glob("preview*"))
    assert len(clips) == 1


async def test_a_rebuild_asks_for_every_clip_built_to_another_shape(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """Nothing else can reach these. A rescan skips a file whose path, size and mtime are unchanged,
    and the rebuild-everything button beside this one queues thumbnails."""
    await jobs.preview(
        await context_for("preview", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
        chosen_shape=_shape("full"),
    )

    await jobs.rebuild_previews(
        await context_for(jobs.REBUILD_PREVIEWS, {}),
        settings=settings,
        hardware=hardware,
        chosen_shape=_shape("brief"),
    )

    queued = await job_queue.list(job_type=jobs.PREVIEW)
    assert queued.total == 2, "the sweep did not ask for a fresh clip"
    assert {job.payload.get("asset_id") for job in queued.jobs} == {ingested_video.asset.id}


async def test_a_rebuild_asks_for_nothing_when_the_library_is_already_the_shape_that_is_set(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """A sweep that queued the whole library on every restart would be a rebuild nobody asked for."""
    await jobs.preview(
        await context_for("preview", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
        chosen_shape=_shape("full"),
    )

    await jobs.rebuild_previews(
        await context_for(jobs.REBUILD_PREVIEWS, {}),
        settings=settings,
        hardware=hardware,
        chosen_shape=_shape("full"),
    )

    # Counted rather than asked about by payload: the preview built above is still claimed by this
    # test's own worker, so "is one live" is true for a reason that has nothing to do with a
    # rebuild. What is being asked is whether the sweep added another.
    assert (await job_queue.list(job_type=jobs.PREVIEW)).total == 1


async def test_the_short_clip_warning_fires_only_when_the_clip_really_is_short(
    ingested_video: Ingested,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    video: Path,
    tmp_path: Path,
) -> None:
    """The silent one: a file whose container declares more running time than it holds.

    The moments are spread across the declared length, so the last of them land past the end of the
    real file, contribute nothing, and ffmpeg **exits 0**: thirty seconds declaring sixty produces
    six seconds instead of ten and reports success. Nothing else in the pipeline can see it: the
    command succeeded and a file exists.

    Both halves in one case on purpose. A warning that fires on every file is a warning nobody
    reads, and a test that only checks the quiet half passes just as well when the capture is
    pointed somewhere the warning never reaches. Asking for both makes each the other's known
    positive.

    Captured through structlog rather than off the terminal: building an application reconfigures
    where log lines go, so a test reading stdout passes alone and fails beside a test that starts a
    server, which is a fact about the harness wearing the shape of a fact about the code.
    """
    whole = await _a_real_clip(ingested_video, context_for, settings, hardware)
    truncated = tmp_path / "truncated.mp4"
    await previews._encode_preview(
        video,
        truncated,
        pieces=(sampler.Piece(0, 1000),),
        accelerator=_accelerator(),
        settings=settings,
    )

    # The same request in both halves (far more than this file holds), so the only thing that
    # differs is whether the source could have supplied it.
    asked = (sampler.Piece(0, 12_000),)
    with capture_logs() as written:
        await previews._check_preview(
            ingested_video.asset.id,
            truncated,
            source=video,
            pieces=asked,
            size=100,
            settings=settings,
        )
        await previews._check_preview(
            ingested_video.asset.id, whole, source=video, pieces=asked, size=100, settings=settings
        )

    said = [line["event"] for line in written]
    assert said.count("media.preview_short") == 1, (
        "a clip that stopped early while the picture ran on is a fault; one that stopped where the "
        "picture stopped is the file, and warning about it is a warning nobody can act on"
    )


async def test_a_source_nothing_can_measure_leaves_the_check_asking_for_what_it_asked_for(
    tmp_path: Path, settings: Settings
) -> None:
    """The second ffprobe, allowed to fail for the same reason the first one is: a question
    ABOUT a preview must not be able to fail the preview.

    What a None means one layer up is that the shortfall is measured against what was asked for:
    the answer the check gave before the source was ever consulted."""
    assert (
        await previews._picture_length_ms(tmp_path / "not-a-video.mp4", settings=settings) is None
    )


async def test_a_clips_length_is_read_from_its_header_as_ffprobe_reads_it(
    ingested_video: Ingested,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The check reads the clip Sift just wrote from its own index, with no tool launched."""
    clip = await _a_real_clip(ingested_video, context_for, settings, hardware)
    said = ffmpeg.parse_probe(await ffmpeg.run_json(ffmpeg.probe_args(clip, settings=settings)))

    assert said.duration_ms is not None
    assert abs((previews._clip_length_ms(clip) or 0) - said.duration_ms) <= 1


async def test_a_clip_nothing_can_measure_is_left_alone_rather_than_failing_the_job(
    tmp_path: Path, settings: Settings
) -> None:
    """A preview that was built and cannot be measured is still a preview. Failing here would throw
    away a good file over a failed question about it."""
    assert previews._clip_length_ms(tmp_path / "not-a-video.mp4") is None


async def test_a_source_whose_streams_cannot_be_read_is_clipped_from_its_first_stream(
    tmp_path: Path, settings: Settings
) -> None:
    """Which stream moves is a question about the source; unanswered, the clip is cut from the
    first video stream, the one a command names when nothing asks the question."""
    assert await previews._moving_stream(tmp_path / "not-a-video.mp4", settings=settings) == 0


def _shape(key: str) -> jobs.ChosenShape:
    """A stand-in for the settings screen, answering with one chosen shape."""

    async def chosen() -> str:
        return key

    return chosen


async def _a_real_clip(
    ingested_video: Ingested,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> Path:
    """Build a preview the ordinary way and hand back where it landed."""
    await jobs.preview(
        await context_for("preview", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
        chosen_shape=_shape("brief"),
    )
    return next(settings.cache_dir.rglob("preview*.mp4"))


async def test_a_superseded_clip_whose_file_has_already_gone_is_still_forgotten(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """A cache directory can be emptied by hand, or sit on a drive that is not plugged in.

    The row still has to go: it is what makes the file findable, and a row pointing at nothing is a
    broken picture rather than a missing one.
    """
    await jobs.preview(
        await context_for("preview", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
        chosen_shape=_shape("full"),
    )
    for clip in settings.cache_dir.rglob("preview*.mp4"):
        clip.unlink()

    await jobs.preview(
        await context_for("preview", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
        chosen_shape=_shape("brief"),
    )

    (left,) = await content_store.derivatives(ingested_video.asset.id)
    assert json.loads(left.params)["clip_ms"] == 6000


async def test_a_clip_of_half_a_second_is_not_called_too_big_for_being_short(
    ingested_video: Ingested,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    video: Path,
) -> None:
    """A library holds videos of half a second, and their preview is the whole file.

    Almost all of it is the first frame, which has to be a whole picture rather than a difference
    from one, so measured per second alone every one of them is "too big", for a reason that says
    nothing about the file, and a warning that fires on a whole class of file is one nobody reads.

    Both halves again, so the quiet one cannot pass by the check being pointed nowhere.
    """
    clip = await _a_real_clip(ingested_video, context_for, settings, hardware)
    half = (sampler.Piece(0, 500),)

    with capture_logs() as written:
        await previews._check_preview(
            ingested_video.asset.id,
            clip,
            source=video,
            pieces=half,
            size=120_000,
            settings=settings,
        )
        await previews._check_preview(
            ingested_video.asset.id,
            clip,
            source=video,
            pieces=half,
            size=5_000_000,
            settings=settings,
        )

    said = [line["event"] for line in written]
    assert said.count("media.preview_oversized") == 1, (
        "a short clip is not too big for being short, and a huge one is still too big"
    )


# --- sampling across the picture, not across the file ---------------------------------------------
#
# A file's length is the container's answer, and the container answers for the sound as well. On a
# file whose sound outlasts its picture, moments spread across the whole of it fall past the last
# frame: the hover clip comes out short and the fingerprint holds its last frame for the rest.


class _Stop(Exception):
    """Raised by a stood-in step once it has seen what it was asked for."""


async def test_a_hover_clip_is_cut_inside_the_picture_of_a_file_whose_sound_runs_on(
    ingested_video: Ingested,
    settings: Settings,
    hardware: HardwareReport,
    temp_db: Database,
    context_for: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The montage is spread across the picture the row says the file holds. Stated on the row
    rather than drawn, because what is under test is which of the two lengths is read."""
    await temp_db.execute(
        "UPDATE assets SET duration_ms = 60000, video_duration_ms = 20000 WHERE id = ?",
        (ingested_video.asset.id,),
    )
    cut: list[sampler.Piece] = []

    async def encode(source: Path, destination: Path, **kwargs: Any) -> None:
        cut.extend(kwargs["pieces"])
        raise _Stop

    monkeypatch.setattr(previews, "_encode_preview", encode)
    with pytest.raises(_Stop):
        await jobs.preview(
            await context_for(jobs.PREVIEW, {"asset_id": ingested_video.asset.id}),
            settings=settings,
            hardware=hardware,
        )

    assert cut, "nothing was asked for"
    assert all(piece.start_ms + piece.length_ms <= 20_000 for piece in cut)


# --- the tile's still, chosen by what the frame shows ------------------------------------------


def test_the_hover_clip_starts_where_the_still_was_cut() -> None:
    piece = sampler.Piece
    # One piece is moved to the still, never past the end of the picture.
    assert jobs.from_the_still((piece(0, 3_000),), 2_000, 4_000) == (piece(1_000, 3_000),)
    # A montage keeps its other pieces, less the one the moved piece overlaps.
    montage = (piece(0, 1_000), piece(4_000, 1_000), piece(8_000, 1_000))
    assert jobs.from_the_still(montage, 4_500, 12_000) == (piece(4_500, 1_000), piece(8_000, 1_000))
    # A still at the first frame changes nothing.
    assert jobs.from_the_still(montage, 0, 12_000) == montage
