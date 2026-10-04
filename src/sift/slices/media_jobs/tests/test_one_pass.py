# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one-pass reader: what each product plans, that the plan matches the product's own ask, and
the shape the read rule chooses per file."""

from __future__ import annotations

import dataclasses
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from sift.kernel import media
from sift.kernel import sampling as sampler
from sift.kernel import subprocess as kernel_subprocess
from sift.kernel.config import Settings
from sift.kernel.content import (
    RECIPE_VERSIONS,
    ContentStore,
    DerivativeKind,
    Ingested,
    perceptual,
)
from sift.kernel.media import FileFacts, Source
from sift.slices.media_jobs import ffmpeg, jobs, one_pass, tuning
from sift.slices.media_jobs.tests.conftest import VIDEO_SECONDS

pytestmark = pytest.mark.integration


def facts_of(
    video: Path, ingested: Ingested, *, duration_ms: int = VIDEO_SECONDS * 1000
) -> FileFacts:
    return FileFacts(
        asset_id=ingested.asset.id,
        path=video,
        media_type="video",
        duration_ms=duration_ms,
        width=320,
        height=240,
        fps=15.0,
        size_bytes=video.stat().st_size,
    )


def _launches(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []
    real_run, real_capture = media.run, kernel_subprocess.capture

    async def counted_run(argv: list[str], **kwargs: Any) -> bytes:
        seen.append(argv)
        return await real_run(argv, **kwargs)

    async def counted_capture(argv: list[str], **kwargs: Any) -> bytes:
        seen.append(argv)
        return await real_capture(argv, **kwargs)

    monkeypatch.setattr(media, "run", counted_run)
    monkeypatch.setattr(kernel_subprocess, "capture", counted_capture)
    return seen


# --- what each product plans ---------------------------------------------------------------------


async def test_the_fingerprints_plan_the_hash_frames_and_the_stash_box_stills(
    video: Path, ingested_video: Ingested
) -> None:
    raw, files = await one_pass.fingerprint_frames(facts_of(video, ingested_video))
    assert isinstance(raw, media.RawFrames)
    assert len(raw.moments) == len(sampler.hash_frames(VIDEO_SECONDS * 1000))
    assert raw.pixel_format == ffmpeg.HASH_PIXEL_FORMAT
    assert raw.frame_bytes == perceptual.HASH_FRAME_SIZE**2
    assert isinstance(files, media.FrameFiles)
    assert len(files.moments) == perceptual.SCENE_FRAMES and files.suffix == ".bmp"


async def test_the_hash_frames_are_planned_across_the_picture_as_the_fingerprint_asks(
    video: Path, ingested_video: Ingested
) -> None:
    """The plan and the ask read the same length: the picture's, where the row says it stops
    before the file does. The stash-box grid stays on the file's length: it has to divide the
    number other people's software divides."""
    facts = dataclasses.replace(
        facts_of(video, ingested_video, duration_ms=10_000), video_duration_ms=4_000
    )
    raw, files = await one_pass.fingerprint_frames(facts)
    assert isinstance(raw, media.RawFrames) and isinstance(files, media.FrameFiles)
    assert raw.moments == tuple(ffmpeg.hash_frame_moment(at) for at in sampler.hash_frames(4_000))
    assert files.moments == tuple(
        ffmpeg.stash_box_moment(at) for at in perceptual.scene_frame_times(10.0)
    )


async def test_a_video_with_no_timeline_plans_no_stash_box_grid(
    video: Path, ingested_video: Ingested
) -> None:
    planned = await one_pass.fingerprint_frames(facts_of(video, ingested_video, duration_ms=0))
    assert [type(one) for one in planned] == [media.RawFrames]


async def test_the_pictures_plan_the_sprite_only_when_it_is_wanted_and_missing(
    video: Path, ingested_video: Ingested, content_store: ContentStore
) -> None:
    facts = facts_of(video, ingested_video)
    # Only a read file lacks anything: the count and the plan agree on that.
    await content_store.record_probe(
        ingested_video.asset.id, width=320, height=240, duration_ms=VIDEO_SECONDS * 1000
    )

    async def yes(_job: str, _asset: str | None) -> bool:
        return True

    async def no(_job: str, _asset: str | None) -> bool:
        return False

    sprite = partial(one_pass.picture_frames, facts, kind=DerivativeKind.SPRITE)
    planned = await sprite(content=content_store, allowed=yes)
    assert len(planned) == 1 and isinstance(planned[0], media.FrameFiles)
    assert planned[0].suffix == ".jpg"
    assert await sprite(content=content_store, allowed=no) == []
    # The thumbnail is one seek and the preview its own decode: neither reads prepared frames.
    for kind in (DerivativeKind.THUMB, DerivativeKind.PREVIEW):
        assert (
            await one_pass.picture_frames(facts, kind=kind, content=content_store, allowed=yes)
            == []
        )

    await content_store.add_derivative(
        ingested_video.asset.id, DerivativeKind.SPRITE, extension="jpg", params={}, size_bytes=1
    )
    assert await sprite(content=content_store, allowed=yes) == [], (
        "a sprite already there is not read for"
    )


async def test_a_pictures_term_names_its_own_kind_and_nothing_when_switched_off() -> None:
    """The Generate row counts each picture by its own term, the same kind
    `picture_lacking_among` asks about a page at a time."""

    async def only_thumbnails(job: str, _asset: str | None) -> bool:
        return job == jobs.THUMBNAIL

    thumbnails, previews, _sprites = jobs.PICTURES
    lack = await jobs.picture_lack(picture=thumbnails, allowed=only_thumbnails)
    assert lack is not None
    # The kind and the recipe it is built at: a picture made by an older recipe is still work to do.
    assert lack.params == (DerivativeKind.THUMB.value, RECIPE_VERSIONS[DerivativeKind.THUMB])
    assert lack.condition.count("NOT EXISTS") == 1
    assert await jobs.picture_lack(picture=previews, allowed=only_thumbnails) is None, (
        "a picture that is switched off is nothing lacking"
    )


# --- the plan and the ask agree -------------------------------------------------------------------


async def test_the_fingerprints_read_from_one_decode_are_the_fingerprints_seeking_gives(
    video: Path,
    ingested_video: Ingested,
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prepared from the plan, `_grey_frames` and `_video_phash` launch nothing and answer what the
    seek form answers: the two asks match the plan, and the frames match byte for byte."""
    payload = await ffmpeg.run_json(ffmpeg.probe_args(video, settings=settings))
    probed = ffmpeg.parse_probe(payload)
    source = Source(
        asset=ingested_video.asset, location=ingested_video.location, path=video, original=video
    )
    timestamps = sampler.hash_frames(VIDEO_SECONDS * 1000)
    seeked_frames = await jobs._grey_frames(video, timestamps, settings=settings)
    seeked_phash = await jobs._video_phash(source, probed, settings=settings)
    assert seeked_phash is not None

    facts = facts_of(video, ingested_video)
    requests = await one_pass.fingerprint_frames(facts)
    workspace = tmp_path / "once"
    workspace.mkdir()
    prepared = await media.decode_once(
        video, requests, workspace=workspace, settings=settings, time_limit=120
    )

    launches = _launches(monkeypatch)
    with media.prepared(prepared):
        frames = await jobs._grey_frames(video, timestamps, settings=settings)
        phash = await jobs._video_phash(source, probed, settings=settings)

    assert launches == [], "every frame came from the store"
    assert frames == seeked_frames
    assert phash == seeked_phash


async def test_the_sprite_tiles_read_from_one_decode_are_the_tiles_seeking_gives(
    video: Path,
    ingested_video: Ingested,
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = facts_of(video, ingested_video)
    (request,) = one_pass.sprite_tile_frames(facts)
    timestamps = sampler.sprite_frames(facts.duration_ms)
    assert [one.seek for one in request.moments] == [
        ffmpeg.hash_frame_moment(at).seek for at in timestamps
    ]
    workspace = tmp_path / "once"
    workspace.mkdir()
    prepared = await media.decode_once(
        video, [request], workspace=workspace, settings=settings, time_limit=120
    )
    seeked = tmp_path / "seeked"
    seeked.mkdir()
    by_seeking = await media.moments_to_files(
        video,
        list(request.moments),
        into=seeked,
        suffix=".jpg",
        filters=ffmpeg.still_filter(width=tuning.SPRITE_TILE_WIDTH),
        output=ffmpeg.still_output(tuning.SPRITE_QUALITY),
        settings=settings,
        time_limit=120,
    )

    launches = _launches(monkeypatch)
    once = tmp_path / "once-tiles"
    once.mkdir()
    with media.prepared(prepared):
        from_store = await media.moments_to_files(
            video,
            list(request.moments),
            into=once,
            suffix=".jpg",
            filters=ffmpeg.still_filter(width=tuning.SPRITE_TILE_WIDTH),
            output=ffmpeg.still_output(tuning.SPRITE_QUALITY),
            settings=settings,
            time_limit=120,
        )

    assert launches == []
    assert prepared.count == 0, "every tile was handed out: the ask is the plan"
    for a, b in zip(from_store, by_seeking, strict=True):
        assert a is not None and b is not None
        assert a.read_bytes() == b.read_bytes()


# --- the reader -----------------------------------------------------------------------------------


class Planned:
    """A product that plans a request, for the reader."""

    def __init__(self, request: media.FrameRequest | None) -> None:
        self._request = request

    @property
    def frames(self) -> Any:
        if self._request is None:
            return None

        async def plan(_facts: FileFacts) -> list[media.FrameRequest]:
            assert self._request is not None
            return [self._request]

        return plan


async def test_a_file_never_read_reads_each_product_as_before(
    video: Path,
    ingested_video: Ingested,
    content_store: ContentStore,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file whose length was never read has no moments to weigh, so each product seeks."""

    async def never(_path: Path) -> media.ReadRates | None:
        return None

    reader = one_pass.OnePassReader(content_store, settings=settings, rates=never)
    launches = _launches(monkeypatch)
    facts = facts_of(video, ingested_video)
    (request,) = one_pass.sprite_tile_frames(facts)
    async with reader.prepared(ingested_video.asset.id, [Planned(request)]):
        assert media._PREPARED.get() is None
    assert launches == []


async def test_a_machine_that_decodes_quickly_reads_the_file_once_for_its_products(
    video: Path,
    ingested_video: Ingested,
    content_store: ContentStore,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thirty moments of a five second clip cost more to seek than to decode: the reader decodes
    once, and inside, a product's ask is served from the store rather than by seeking."""

    async def quick(_path: Path) -> media.ReadRates | None:
        return media.ReadRates(decode_fps=100_000.0, seek_seconds=1.0)

    reader = one_pass.OnePassReader(content_store, settings=settings, rates=quick)
    # The reader reads the file's facts off its record, which only a probe fills in.
    await content_store.record_probe(
        ingested_video.asset.id, width=320, height=240, duration_ms=VIDEO_SECONDS * 1000
    )
    facts = facts_of(video, ingested_video)
    # The thirty hash frames of a five second clip: one decode is cheaper than thirty seeks.
    (request, _grid) = await one_pass.fingerprint_frames(facts)
    timestamps = sampler.hash_frames(facts.duration_ms)
    launches = _launches(monkeypatch)
    async with reader.prepared(ingested_video.asset.id, [Planned(request)]):
        prepared = media._PREPARED.get()
        assert prepared is not None and prepared.count == len(request.moments), (
            "one decode of the file, every planned moment in the store"
        )
        launches.clear()  # the decode's own reading of the file; the ask is what is counted
        frames = await jobs._grey_frames(video, timestamps, settings=settings)
    assert launches == [], "the product's ask was served without a launch"
    assert all(one is not None for one in frames)
    assert media._PREPARED.get() is None


async def test_nothing_planned_is_nothing_read(
    video: Path,
    ingested_video: Ingested,
    content_store: ContentStore,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def quick(_path: Path) -> media.ReadRates | None:
        return media.ReadRates(decode_fps=100_000.0, seek_seconds=1.0)

    reader = one_pass.OnePassReader(content_store, settings=settings, rates=quick)
    launches = _launches(monkeypatch)
    async with reader.prepared(ingested_video.asset.id, [Planned(None)]):
        pass
    assert launches == []


async def test_a_file_that_is_gone_is_left_to_the_products(
    content_store: ContentStore, settings: Settings
) -> None:
    async def quick(_path: Path) -> media.ReadRates | None:
        return media.ReadRates(decode_fps=100_000.0, seek_seconds=1.0)

    reader = one_pass.OnePassReader(content_store, settings=settings, rates=quick)
    async with reader.prepared("not-an-asset", []):
        assert media._PREPARED.get() is None


# --- what is not a video, and a decode that fails -------------------------------------------------


async def test_a_still_plans_no_frames_for_either_product(
    video: Path, ingested_video: Ingested
) -> None:
    """Both plans are about a timeline. A photograph has none, and a video whose running time
    nothing has measured has no moments to name."""
    still = dataclasses.replace(facts_of(video, ingested_video), media_type="image")
    assert await one_pass.fingerprint_frames(still) == []
    assert one_pass.sprite_tile_frames(still) == []
    assert one_pass.sprite_tile_frames(facts_of(video, ingested_video, duration_ms=0)) == []


async def test_a_file_that_is_not_a_video_is_left_to_the_products(
    video: Path,
    ingested_video: Ingested,
    ingested_picture: Ingested,
    content_store: ContentStore,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A product may plan frames for anything it is handed; the reader decodes only a video."""

    async def quick(_path: Path) -> media.ReadRates | None:
        return media.ReadRates(decode_fps=100_000.0, seek_seconds=1.0)

    reader = one_pass.OnePassReader(content_store, settings=settings, rates=quick)
    (request,) = one_pass.sprite_tile_frames(facts_of(video, ingested_video))
    launches = _launches(monkeypatch)
    async with reader.prepared(ingested_picture.asset.id, [Planned(request)]):
        assert media._PREPARED.get() is None
    assert launches == []


async def test_a_decode_that_ffmpeg_refuses_leaves_the_products_to_seek(
    video: Path,
    ingested_video: Ingested,
    content_store: ContentStore,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing is lost but the saving: the products inside read as they always did, and the
    reason is in the log rather than in a failed task."""

    async def quick(_path: Path) -> media.ReadRates | None:
        return media.ReadRates(decode_fps=100_000.0, seek_seconds=1.0)

    async def refuses(*_args: Any, **_kwargs: Any) -> Any:
        raise media.FFmpegError("the decoder gave up")

    monkeypatch.setattr(media, "decode_once", refuses)
    reader = one_pass.OnePassReader(content_store, settings=settings, rates=quick)
    await content_store.record_probe(
        ingested_video.asset.id, width=320, height=240, duration_ms=VIDEO_SECONDS * 1000
    )
    (request, _grid) = await one_pass.fingerprint_frames(facts_of(video, ingested_video))
    with capture_logs() as logs:
        async with reader.prepared(ingested_video.asset.id, [Planned(request)]):
            assert media._PREPARED.get() is None
    assert [entry["event"] for entry in logs if entry["event"].endswith("decode_refused")] == [
        "media_jobs.one_pass.decode_refused"
    ]


def test_the_decode_time_limit_has_a_floor_where_the_machine_was_never_measured(
    video: Path, ingested_video: Ingested
) -> None:
    """A guard against a hung decoder, not a budget. With no rate to expect from, or a rate of
    nothing, the floor is the whole of it; a slow measured rate lifts it above the floor."""
    facts = facts_of(video, ingested_video)
    floor = one_pass.DECODE_TIME_FLOOR_SECONDS
    limit = one_pass.OnePassReader._time_limit

    assert limit(facts, None) == floor
    assert limit(facts, media.ReadRates(decode_fps=0.0, seek_seconds=1.0)) == floor
    assert limit(facts, media.ReadRates(decode_fps=0.001, seek_seconds=1.0)) > floor
