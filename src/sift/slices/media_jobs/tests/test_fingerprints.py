# SPDX-License-Identifier: AGPL-3.0-or-later
"""The near-duplicate fingerprints, run against real files with real ffmpeg."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

# A fingerprint write records an event through the ledger door, and the door writes into the
# workbench's own table, so a database built without that component has nowhere to put it.
# Imported for the registration, nothing else. Without it this module passes only when some
# OTHER module collected in the same run happens to have imported it, and fails on its own;
# the content suite carries the same line for the same reason.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel import media
from sift.kernel import sampling as sampler
from sift.kernel import subprocess as kernel_subprocess
from sift.kernel.config import Settings
from sift.kernel.content import (
    ContentStore,
    Ingested,
    VerdictProduct,
    hashing,
    perceptual,
)
from sift.kernel.db import Database
from sift.kernel.hardware import HardwareReport
from sift.kernel.ids import new_id
from sift.kernel.ingress import Origin, verify_ingress
from sift.kernel.jobs import (
    JobContext,
    JobQueue,
)
from sift.kernel.jobs.tuning import BACKGROUND_PRIORITY
from sift.kernel.media import Source
from sift.slices.media_jobs import (
    ffmpeg,
    fingerprints,
    jobs,
    tuning,
)
from sift.slices.media_jobs.tests.conftest import VIDEO_SECONDS, draw, take_in
from sift.slices.media_jobs.tests.support import Context, probe_and_fingerprint, watch_launches
from sift.testing.fixtures import LibraryRoot

pytestmark = [pytest.mark.integration]


# --- probe ------------------------------------------------------------------------------------


async def test_probe_fingerprints_a_video_on_both_axes(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """A frame's fingerprint and the whole video's. The second is what finds a re-encoded copy."""
    await probe_and_fingerprint(
        await context_for("probe", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    asset = await content_store.get(ingested_video.asset.id)
    assert asset is not None
    assert asset.phash is not None
    assert asset.videohash is not None
    assert len(asset.phash) == 16
    assert len(asset.videohash) == 16 * 30


async def test_probe_fingerprints_a_gif_in_one_decode_on_both_axes(
    ingested_animation: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """A GIF is fingerprinted like a video (a frame hash and a whole-loop hash, the same two
    lengths), but its thirty frames come from a single decode rather than thirty seeks (a GIF cannot
    seek cheaply). The observable result is identical to the per-seek path: a full, right-length
    fingerprint on both axes. This runs the ffmpeg of the machine running the tests; the shipped
    image may run a newer one, so the container's own conformance run confirms the decode there."""
    await probe_and_fingerprint(
        await context_for("probe", {"asset_id": ingested_animation.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    asset = await content_store.get(ingested_animation.asset.id)
    assert asset is not None
    assert asset.phash is not None
    assert asset.videohash is not None
    assert len(asset.phash) == 16
    assert len(asset.videohash) == 16 * 30  # thirty frames, whatever the GIF's own frame count


async def test_a_gif_is_sampled_to_thirty_frames_from_one_decode(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """The single decode returns every frame back to back; the fingerprint takes exactly thirty of
    them, evenly by position, however many the GIF has (more than thirty are thinned, fewer are
    repeated), so it is always its fixed length, and two copies pick the same frames and agree."""
    stride = perceptual.HASH_FRAME_SIZE * perceptual.HASH_FRAME_SIZE
    # Fifty distinct frames, each filled with its own byte value, so which were picked is observable.
    raw = b"".join(bytes([index % 256]) * stride for index in range(50))

    async def fake_run(argv: list[str], *, capture: bool = False, reads: object = None) -> bytes:
        return raw

    monkeypatch.setattr(ffmpeg, "run", fake_run)

    hashes = await fingerprints._gif_frame_hashes(Path("loop.gif"), settings=settings)

    assert len(hashes) == 30
    assert all(value is not None for value in hashes)
    assert hashes == await fingerprints._gif_frame_hashes(Path("loop.gif"), settings=settings)


async def test_an_animated_avifs_fingerprint_is_read_from_the_stream_that_moves(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """An animated AVIF is fingerprinted as a GIF, and its first video stream is a still cover:
    read from that, the thirty frames are the cover thirty times and match every other copy of
    the cover. The decode names the stream `media.moving_stream_of` answers."""
    asked: list[list[str]] = []

    async def moving(path: Path, **_: object) -> int:
        return 1

    async def fake_run(argv: list[str], *, capture: bool = False, reads: object = None) -> bytes:
        asked.append(argv)
        return b""

    monkeypatch.setattr(media, "moving_stream_of", moving)
    monkeypatch.setattr(ffmpeg, "run", fake_run)

    await fingerprints._gif_frame_hashes(Path("motion.avif"), settings=settings)

    (argv,) = asked
    assert argv[argv.index("-map") + 1] == "0:v:1"
    assert argv.index("-map") > argv.index("-i")
    assert "-map" not in ffmpeg.all_frames_args(Path("loop.gif"), size=8, settings=settings)


async def test_a_gif_that_decodes_to_no_frames_gets_no_fingerprint(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """An undecodable GIF returns nothing from the decode, so its thirty slots stay empty: the same
    "no fingerprint" answer the per-seek path gives a file it cannot read a frame from."""

    async def fake_run(argv: list[str], *, capture: bool = False, reads: object = None) -> bytes:
        return b""

    monkeypatch.setattr(ffmpeg, "run", fake_run)

    hashes = await fingerprints._gif_frame_hashes(Path("broken.gif"), settings=settings)

    assert hashes == [None] * 30


async def test_probe_fingerprints_a_picture_on_one_axis_only(
    ingested_picture: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """A still has no timeline, so it has no video fingerprint, and the two must never be
    comparable, or a photograph would come back as a duplicate of a video's first frame."""
    await probe_and_fingerprint(
        await context_for("probe", {"asset_id": ingested_picture.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    asset = await content_store.get(ingested_picture.asset.id)
    assert asset is not None
    assert asset.phash is not None
    assert asset.videohash is None
    assert asset.duration_ms is None


async def test_a_re_encoded_copy_fingerprints_close_to_the_original(
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    video: Path,
) -> None:
    """The claim the whole fingerprint exists to make, end to end through the real jobs.

    The same footage, encoded again at a much worse quality, is a different file with a different
    digest, so the exact-match half of duplicate detection has nothing to say about it. This is
    the half that does.
    """
    from sift.slices.media_jobs.tests.conftest import take_in

    copy = library_root.path / "holiday-again.mp4"
    import subprocess

    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video),
            "-c:v",
            "libx264",
            "-crf",
            "40",
            "-pix_fmt",
            "yuv420p",
            str(copy),
        ],
        check=True,
        capture_output=True,
    )

    original = await take_in(content_store, library_root, video, settings)
    duplicate = await take_in(content_store, library_root, copy, settings)
    assert original.asset.identity != duplicate.asset.identity, (
        "different bytes, or this proves nothing"
    )

    for asset_id in (original.asset.id, duplicate.asset.id):
        await probe_and_fingerprint(
            await context_for("probe", {"asset_id": asset_id}), settings=settings, hardware=hardware
        )

    first = await content_store.get(original.asset.id)
    second = await content_store.get(duplicate.asset.id)
    assert first is not None and second is not None
    apart = perceptual.distance(first.videohash or "", second.videohash or "")
    assert apart is not None
    # Thirty frames, 63 bits each. A handful of bits move under a heavy re-encode; a different
    # video moves hundreds.
    assert apart < 30 * 12


@pytest.mark.parametrize("fingerprinted", [True, False])
async def test_what_reads_the_fingerprints_is_asked_for_by_what_wrote_them(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
    fingerprinted: bool,
) -> None:
    """What reads the fingerprints is asked for by what WRITES them (the file's own fingerprint
    job, once it has) and never by the read, so it runs a settle after the last file's
    fingerprints rather than after the last read. A job that found them already there asks
    nothing."""
    reader = jobs.FINGERPRINT_FOR_STASH_BOXES  # any settling type will do; this one is registered
    probing = await context_for("probe", {"asset_id": ingested_video.asset.id})
    await jobs.probe(probing, settings=settings, hardware=hardware)
    assert await job_queue.outstanding(reader) == 0, "the read asked for nothing that reads them"

    if not fingerprinted:
        await content_store.record_fingerprints(
            ingested_video.asset.id, phash="aa", videohash="bb", oshash="cc", video_phash="dd"
        )
    (handed,) = [
        child
        for child in await job_queue.children(probing.job.id)
        if child.type == jobs.FINGERPRINT_FILE
    ]
    await jobs.fingerprint_arrival(
        await context_for(jobs.FINGERPRINT_FILE, handed.payload),
        settings=settings,
        settles_into=(reader,),
    )

    assert await job_queue.outstanding(reader) == (1 if fingerprinted else 0)


# --- concurrency ------------------------------------------------------------------------------


async def test_a_fingerprint_is_the_same_length_whatever_the_file_is(
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The invariant the whole fingerprint rests on, checked against files that break it.

    A fingerprint of a different length is not comparable to anything, so a file that produced a
    short one would silently never match a duplicate, and nothing anywhere would look wrong. That
    is what makes this worth a test of its own rather than trusting the sampler's frame count: the
    sampler asks for thirty moments, and whether thirty frames come *back* depends on the file.

    Every case here is one where a seek lands past the last real frame: a file whose declared
    duration overshoots what it holds, and a file with almost nothing in it (a GIF of twenty
    frames over two seconds, say, which would otherwise give a hash of twenty-nine).
    """
    files = [
        draw(library_root.path / "short.gif", "testsrc2=size=64x64:rate=10", 2),
        draw(library_root.path / "sparse.gif", "testsrc2=size=32x32:rate=2", 1),
        draw(library_root.path / "tiny.mp4", "testsrc2=size=64x64:rate=1", 1),
        draw(library_root.path / "brief.mp4", "testsrc2=size=64x64:rate=30", 1),
    ]

    for path in files:
        taken = await take_in(content_store, library_root, path, settings)
        await probe_and_fingerprint(
            await context_for("probe", {"asset_id": taken.asset.id}),
            settings=settings,
            hardware=hardware,
        )
        asset = await content_store.get(taken.asset.id)
        assert asset is not None
        assert asset.videohash is not None, path.name
        assert len(asset.videohash) == 16 * 30, (
            f"{path.name} produced a {len(asset.videohash) // 16}-frame fingerprint, and one of "
            "the wrong length is comparable to nothing at all"
        )


async def test_a_file_that_ends_early_fingerprints_the_same_way_every_time(
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """Holding the last frame has to be deterministic, or it would defeat the thing it protects.

    Two copies of one file cannot be used to show this: identical bytes are one asset, by
    design, so it would compare a value with itself and pass whatever the code did. Probing the
    same file twice is the honest version: the fill has to land on the same frames both times, or
    two libraries holding the same video would disagree about it.
    """
    path = draw(library_root.path / "one.gif", "testsrc2=size=64x64:rate=10", 2)
    taken = await take_in(content_store, library_root, path, settings)

    fingerprints = []
    for _ in range(2):
        await probe_and_fingerprint(
            await context_for("probe", {"asset_id": taken.asset.id}),
            settings=settings,
            hardware=hardware,
        )
        asset = await content_store.get(taken.asset.id)
        assert asset is not None
        fingerprints.append(asset.videohash)

    assert fingerprints[0] == fingerprints[1]
    assert fingerprints[0] is not None
    assert len(fingerprints[0]) == 16 * 30


# --- measuring a library that predates the measurement -------------------------------------------


async def test_a_video_gains_both_stash_box_fingerprints(
    video: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    hardware: HardwareReport,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """Probing writes the exact-file hash and the whole-video one, both sixteen characters.

    Sixteen is the width every implementation stores and the width a stash-box will accept. A value
    of any other width is not a fingerprint that is slightly wrong: it is one that can never match
    anything, which looks exactly like having no duplicates and no matches.
    """
    checked = verify_ingress(video, origin=Origin.SCAN, settings=settings)
    ingested = await content_store.ingest(checked, root_id=library_root.id, rel_path=video.name)
    # READ, but not fingerprinted, which is what a scan-only pass leaves behind and what this
    # pass exists to pick up. A file nobody has read is deliberately not offered: nothing knows how
    # long it is, so the moments to sample cannot be worked out.
    await content_store.record_probe(ingested.asset.id, width=64, height=48, duration_ms=1000)

    await probe_and_fingerprint(
        await context_for("probe", {"asset_id": ingested.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    asset = await content_store.get(ingested.asset.id)
    assert asset is not None
    assert asset.oshash is not None and len(asset.oshash) == 16
    assert asset.video_phash is not None and len(asset.video_phash) == 16
    assert int(asset.video_phash, 16) >= 0


async def test_a_short_videos_fingerprints_come_from_one_decode_and_match_the_seeked_ones(
    ingested_video: Ingested,
    content_store: ContentStore,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fifty-five seeks into a five second clip decode most of it fifty-five times. The rule
    sends it through one decode instead, every fingerprint byte for byte what seeking wrote, and
    no seeked input is opened."""
    asset_id = ingested_video.asset.id
    real_rule = media.choose_read_shape

    def always_seek(**_kwargs: Any) -> media.ReadShape:
        return media.ReadShape.SEEK

    monkeypatch.setattr(media, "choose_read_shape", always_seek)
    assert await fingerprints.fingerprint_one(content_store, asset_id, settings=settings)
    seeked = await content_store.get(asset_id)
    monkeypatch.setattr(media, "choose_read_shape", real_rule)

    launches: list[list[str]] = []
    real_run, real_capture = kernel_subprocess.run, kernel_subprocess.capture

    async def run(argv: list[str], **kwargs: Any) -> Any:
        launches.append(argv)
        return await real_run(argv, **kwargs)

    async def capture(argv: list[str], **kwargs: Any) -> bytes:
        launches.append(argv)
        return bytes(await real_capture(argv, **kwargs))

    monkeypatch.setattr(kernel_subprocess, "run", run)
    monkeypatch.setattr(kernel_subprocess, "capture", capture)
    assert await fingerprints.fingerprint_one(content_store, asset_id, settings=settings)
    once = await content_store.get(asset_id)

    assert seeked is not None and once is not None
    assert (once.phash, once.videohash, once.video_phash) == (
        seeked.phash,
        seeked.videohash,
        seeked.video_phash,
    )
    decodes = [argv for argv in launches if "-filter_complex_script" in argv]
    assert len(decodes) == 1, "one decode for every moment of both fingerprints"
    assert not any("-ss" in argv for argv in launches), "no moment was seeked"


async def test_a_decode_the_tool_refuses_leaves_the_fingerprints_to_seek_and_they_match(
    ingested_video: Ingested,
    content_store: ContentStore,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One decode is a saving, never the only way: refused, every moment is seeked as it would be
    without the rule, and the fingerprints are the same bytes."""
    asset_id = ingested_video.asset.id

    def always_seek(**_kwargs: Any) -> media.ReadShape:
        return media.ReadShape.SEEK

    real_rule = media.choose_read_shape
    monkeypatch.setattr(media, "choose_read_shape", always_seek)
    assert await fingerprints.fingerprint_one(content_store, asset_id, settings=settings)
    seeked = await content_store.get(asset_id)
    monkeypatch.setattr(media, "choose_read_shape", real_rule)

    refused: list[int] = []

    async def refuse(*_args: Any, **_kwargs: Any) -> media.PreparedFrames:
        refused.append(1)
        raise media.FFmpegError("the decode was refused")

    monkeypatch.setattr(media, "decode_once", refuse)
    assert await fingerprints.fingerprint_one(content_store, asset_id, settings=settings)
    after = await content_store.get(asset_id)

    assert refused == [1], "the short clip was not offered one decode first"
    assert seeked is not None and after is not None
    assert (after.phash, after.videohash, after.video_phash) == (
        seeked.phash,
        seeked.videohash,
        seeked.video_phash,
    )


def test_a_video_with_no_running_time_plans_its_hash_frames_and_no_stash_box_stills() -> None:
    """The stash-box stills are spread across the running time, so with none there is nothing
    to spread them across; the hash frames are still planned."""
    probed = ffmpeg.Probed(
        width=64, height=64, duration_ms=None, fps=None, vcodec="h264", acodec=None
    )

    planned = fingerprints.fingerprint_requests(probed)

    assert [type(one) for one in planned] == [media.RawFrames]


async def test_a_photograph_and_a_gif_get_no_stash_box_fingerprint(
    picture: Path,
    animation: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    hardware: HardwareReport,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """Nobody stash-boxes a photograph or a GIF, so a value on one would be a number with nothing on
    earth to compare it against. Both keep the fingerprints they are actually judged by."""
    for path in (picture, animation):
        checked = verify_ingress(path, origin=Origin.SCAN, settings=settings)
        ingested = await content_store.ingest(checked, root_id=library_root.id, rel_path=path.name)
        await probe_and_fingerprint(
            await context_for("probe", {"asset_id": ingested.asset.id}),
            settings=settings,
            hardware=hardware,
        )
        asset = await content_store.get(ingested.asset.id)
        assert asset is not None, path.name
        assert asset.oshash is None, path.name
        assert asset.video_phash is None, path.name
        assert asset.phash is not None, path.name


async def test_a_video_with_no_running_time_gets_no_whole_video_fingerprint(
    ingested_video: Ingested,
    video: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file with no timeline is not fingerprinted at all, and nothing is decoded for it.

    Every one of the twenty-five sample moments is worked out from the running time, so with no
    running time they are all zero, and the grid would be the first frame twenty-five times over.
    That is a perfectly valid fingerprint of the wrong thing: it would be sent to a stash-box as
    though it meant something, and it would match every other duration-less file whose first frame
    happens to be dark.
    """
    no_timeline = ffmpeg.Probed(
        width=320,
        height=240,
        duration_ms=None,
        fps=25.0,
        vcodec="h264",
        acodec=None,
        duration_seconds=None,
    )
    source = Source(
        asset=ingested_video.asset,
        location=ingested_video.location,
        path=video,
        original=video,
    )
    decoded = 0

    async def counting(*args: object, **kwargs: object) -> bytes:
        nonlocal decoded
        decoded += 1
        return b""

    monkeypatch.setattr(ffmpeg, "run", counting)

    assert await fingerprints._video_phash(source, no_timeline, settings=settings) is None
    assert decoded == 0, "a file with no timeline should not be decoded at all"


async def test_a_library_from_before_the_fingerprints_is_caught_up(
    video: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    hardware: HardwareReport,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """The catch-up pass, on a video that was indexed before Sift computed these.

    Without it the upgrade is silent and looks like nothing changed: video near-duplicate detection
    reads a column that is empty for every file already in the library, so the duplicates screen
    would confidently report there are none. A rescan cannot fix it either: a scan skips any file
    whose path, size and mtime are unchanged.
    """
    checked = verify_ingress(video, origin=Origin.SCAN, settings=settings)
    ingested = await content_store.ingest(checked, root_id=library_root.id, rel_path=video.name)
    # READ, but not fingerprinted, which is what a scan-only pass leaves behind and what this
    # pass exists to pick up. A file nobody has read is deliberately not offered: nothing knows how
    # long it is, so the moments to sample cannot be worked out.
    await content_store.record_probe(ingested.asset.id, width=64, height=48, duration_ms=1000)
    assert await content_store.unfingerprinted(10) == [ingested.asset.id]

    await jobs.fingerprint_stash_box(
        await context_for("fingerprint_stash_box", {}),
        settings=settings,
        hardware=hardware,
    )

    asset = await content_store.get(ingested.asset.id)
    assert asset is not None
    # All four out of one decode. The frame hashes are this pass's work too, so a run that filled
    # only the stash-box pair would leave near-duplicate detection blind, with nothing saying so.
    assert asset.phash, "the frame fingerprint was not filled in"
    assert asset.videohash, "the whole-video fingerprint was not filled in"
    assert asset.oshash is not None and asset.video_phash is not None
    # And it does not come back on the next run, which is what stops the pass looping forever.
    assert await content_store.unfingerprinted(10) == []


async def test_the_fingerprint_chains_last_page_asks_for_the_duplicate_sweep(
    video: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    hardware: HardwareReport,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """What the chain writes is what the duplicate sweep compares, so its last page asks for it.

    Probing's own settle asks for the sweep a minute after the last file lands, and this chain
    starts after that: on a library whose probing was scan-only the sweep would compare
    fingerprints that did not exist yet. A page that recorded nothing has given the sweep nothing
    new, so it asks for nothing.
    """
    checked = verify_ingress(video, origin=Origin.SCAN, settings=settings)
    ingested = await content_store.ingest(checked, root_id=library_root.id, rel_path=video.name)
    await content_store.record_probe(ingested.asset.id, width=64, height=48, duration_ms=1000)
    context = await context_for("fingerprint_stash_box", {})
    asked: list[tuple[str, int | None]] = []

    async def noting(job_type: str, payload: object = None, **how: object) -> str:
        asked.append((job_type, how.get("priority")))  # type: ignore[arg-type]
        return "asked"

    context.queue.enqueue_when_settled = noting  # type: ignore[method-assign]

    await jobs.fingerprint_stash_box(
        context, settings=settings, hardware=hardware, settles_into=("dedup_scan",)
    )
    assert asked == [("dedup_scan", BACKGROUND_PRIORITY)], "the last page did not ask for the sweep"

    asked.clear()
    await jobs.fingerprint_stash_box(
        context, settings=settings, hardware=hardware, settles_into=("dedup_scan",)
    )
    assert asked == [], "a page that recorded nothing asked for the sweep anyway"


async def test_one_unreadable_file_does_not_block_the_catch_up_pass(
    video: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    hardware: HardwareReport,
    context_for: Callable[..., Awaitable[JobContext]],
) -> None:
    """A file that is there and will not be read is written down as done, not left for next time.

    Left alone, the same file comes back on every run for the rest of the library's life and the
    pass never finishes, so every video behind it stays unfingerprinted, and the duplicates screen
    quietly keeps reporting that there are none. That is the shape of failure this whole pass exists
    to prevent, so it must not be the shape of its own failure.

    A file that is *missing* is a different case and is deliberately left alone: the drive may come
    back, and recording an answer nobody measured would be worse than asking again.
    """
    checked = verify_ingress(video, origin=Origin.SCAN, settings=settings)
    ingested = await content_store.ingest(checked, root_id=library_root.id, rel_path=video.name)
    # READ, but not fingerprinted, which is what a scan-only pass leaves behind and what this
    # pass exists to pick up. A file nobody has read is deliberately not offered: nothing knows how
    # long it is, so the moments to sample cannot be worked out.
    await content_store.record_probe(ingested.asset.id, width=64, height=48, duration_ms=1000)
    video.write_bytes(b"not a video, but certainly a file")

    await jobs.fingerprint_stash_box(
        await context_for("fingerprint_stash_box", {}),
        settings=settings,
        hardware=hardware,
    )

    asset = await content_store.get(ingested.asset.id)
    assert asset is not None
    # EVERY column this kind of file is asked for, or the ones left NULL bring it straight back,
    # and the pass asks for four.
    assert (asset.phash, asset.videohash, asset.oshash, asset.video_phash) == ("", "", "", "")
    assert await content_store.unfingerprinted(10) == []


async def test_a_batch_of_files_nobody_could_read_does_not_ask_for_itself_again(
    video: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    hardware: HardwareReport,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full batch that recorded nothing has learnt only that the drive is away. It stops.

    A full batch does not mean there is more: a file that cannot be reached is deliberately left
    unrecorded so it is tried again when the drive is back, so it is still in the work list, and
    the next page would be the same page: once a minute for ever, nothing failing and nothing
    moving.

    So the condition is what the batch recorded, not how big it was. The files stay in the work
    list and are picked up by the next import or the next start.

    The pass does ask again, for the next page: this list is ordered oldest first, so a page of
    files the decoder will never read would otherwise sit at its head and hold up every file behind
    it. It steps over the page a bounded number of times (see `tuning.FINGERPRINT_SKIP_PAGES`).
    What this test holds is never the same page twice.
    """
    monkeypatch.setattr(tuning, "FINGERPRINT_BATCH", 1)
    checked = verify_ingress(video, origin=Origin.SCAN, settings=settings)
    ingested = await content_store.ingest(checked, root_id=library_root.id, rel_path=video.name)
    # READ, but not fingerprinted, which is what a scan-only pass leaves behind and what this
    # pass exists to pick up. A file nobody has read is deliberately not offered: nothing knows how
    # long it is, so the moments to sample cannot be worked out.
    await content_store.record_probe(ingested.asset.id, width=64, height=48, duration_ms=1000)
    # The drive is not plugged in. Not corrupt: gone, which is the case that must not be recorded.
    video.unlink()

    asked: list[tuple[str, object]] = []
    original = job_queue.enqueue_when_settled

    async def noting(job_type: str, payload: object = None, **kwargs: object) -> str:
        asked.append((job_type, payload))
        return await original(job_type, payload, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(job_queue, "enqueue_when_settled", noting)

    await jobs.fingerprint_stash_box(
        await context_for("fingerprint_stash_box", {}),
        settings=settings,
        hardware=hardware,
    )

    # Still waiting, which is right: the drive may come back.
    assert await content_store.unfingerprinted(10) == [ingested.asset.id]
    # And what it asked for is the page AFTER this one, never this one again.
    assert asked == [(jobs.FINGERPRINT_FOR_STASH_BOXES, {"skip": 1})]


async def test_the_pass_stops_walking_once_it_has_stepped_over_its_allowance(
    video: Path,
    picture: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    hardware: HardwareReport,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ceiling, and the case it exists for.

    Stepping over a page that recorded nothing is right for a head of files the decoder refuses,
    and it is the wrong thing to do for ever when a whole drive is away: without a ceiling the pass
    would walk a library from end to end, a page at a time, to record nothing at all. So it walks
    a fixed distance and then stops and waits to be asked again.
    """
    monkeypatch.setattr(tuning, "FINGERPRINT_BATCH", 1)
    monkeypatch.setattr(tuning, "FINGERPRINT_SKIP_PAGES", 1)
    waiting: list[str] = []
    for where in (video, picture):
        checked = verify_ingress(where, origin=Origin.SCAN, settings=settings)
        taken = await content_store.ingest(checked, root_id=library_root.id, rel_path=where.name)
        await content_store.record_probe(taken.asset.id, width=64, height=48, duration_ms=1000)
        waiting.append(taken.asset.id)
        # The drive is away, so nothing on any page can be recorded.
        where.unlink()
    assert set(await content_store.unfingerprinted(10)) == set(waiting)

    asked: list[object] = []
    original = job_queue.enqueue_when_settled

    async def noting(job_type: str, payload: object = None, **kwargs: object) -> str:
        asked.append(payload)
        return await original(job_type, payload, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(job_queue, "enqueue_when_settled", noting)

    # One page in, which is the whole allowance here: the page after this one is past it.
    await jobs.fingerprint_stash_box(
        await context_for("fingerprint_stash_box", {"skip": 1}),
        settings=settings,
        hardware=hardware,
    )

    assert asked == []
    # And nothing was written down about either file, so a request that starts at the beginning
    # (the next import, the next start) still finds them both waiting.
    assert set(await content_store.unfingerprinted(10)) == set(waiting)


async def test_a_full_batch_that_got_somewhere_does_ask_for_the_next_one(
    video: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    hardware: HardwareReport,
    job_queue: JobQueue,
    context_for: Callable[..., Awaitable[JobContext]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half, and the half a stricter condition could silently break: a library bigger
    than one batch has to keep going, or every install past fifty videos stops half done."""
    monkeypatch.setattr(tuning, "FINGERPRINT_BATCH", 1)
    checked = verify_ingress(video, origin=Origin.SCAN, settings=settings)
    ingested = await content_store.ingest(checked, root_id=library_root.id, rel_path=video.name)
    # READ, but not fingerprinted. `unfingerprinted` offers what a scan-only pass left behind, and
    # a file nobody has read is deliberately not on that list, so without this the pass finds
    # nothing, asks for no second batch, and the test reads as the paging being broken.
    await content_store.record_probe(ingested.asset.id, width=64, height=48, duration_ms=1000)

    asked: list[str] = []
    original = job_queue.enqueue_when_settled

    async def noting(job_type: str, payload: object = None, **kwargs: object) -> str:
        asked.append(job_type)
        return await original(job_type, payload, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(job_queue, "enqueue_when_settled", noting)

    await jobs.fingerprint_stash_box(
        await context_for("fingerprint_stash_box", {}),
        settings=settings,
        hardware=hardware,
    )

    assert asked == [jobs.FINGERPRINT_FOR_STASH_BOXES]


# --- the colour-depth catch-up ------------------------------------------------------------------


def test_a_file_that_read_back_nothing_at_all_has_no_fingerprint() -> None:
    """Not a fingerprint of zeros, and not a short one. Nothing.

    An invented fingerprint would match every other file that also failed to read, which is the one
    outcome worse than having none: it makes unrelated files look like duplicates of each other.
    """
    assert fingerprints._hold_the_last_frame([None, None, None]) is None


def test_a_moment_past_the_end_holds_the_frame_before_it() -> None:
    """The real case this exists for: a file whose declared duration runs past its last frame.

    The gaps are at the END here, which is where they actually land, and the result has to stay
    the same length as the input: a fingerprint of a different length is comparable to nothing.
    """
    filled = fingerprints._hold_the_last_frame(["aa", "bb", None, None])

    assert filled == ["aa", "bb", "bb", "bb"]


def test_a_gap_before_anything_read_takes_the_first_frame_that_did() -> None:
    """The other end of the same rule, and it needs its own case: holding 'the previous frame'
    cannot work when there is no previous frame."""
    filled = fingerprints._hold_the_last_frame([None, None, "cc"])

    assert filled == ["cc", "cc", "cc"]


# --- the three passes over a library that predates a feature --------------------------------------
#
# Each walks a batch, and each is asked for again while there is more. What is pinned here is the
# case where there is nothing to do: an install where the feature was always on runs all three on
# every start, so "nothing to do" is the ordinary path rather than an edge, and it must not ask for
# itself again: a pass that re-queues on an empty library never stops.


async def test_a_library_with_nothing_left_to_fingerprint_stops(
    context_for: Callable[..., Awaitable[JobContext]],
    settings: Settings,
    hardware: HardwareReport,
    job_queue: JobQueue,
) -> None:
    context = await context_for("fingerprint_stash_box", {})
    before = await job_queue.outstanding(jobs.FINGERPRINT_FOR_STASH_BOXES)

    await jobs.fingerprint_stash_box(context, settings=settings, hardware=hardware)

    # Its own row counts while it runs, so what is asked is whether it added ANOTHER.
    assert await job_queue.outstanding(jobs.FINGERPRINT_FOR_STASH_BOXES) == before


async def test_a_file_with_no_readable_frame_in_it_has_no_fingerprint_rather_than_an_invented_one(
    ingested_video: Ingested,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    content_store: ContentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing decoded at all, which is a different answer from a file with gaps in it.

    An invented fingerprint would match every other file that also failed to read, so two unrelated
    unreadable files would look like copies of each other. None is the only safe answer.
    """

    async def nothing(
        _path: object, timestamps: Sequence[int], **_kwargs: object
    ) -> list[bytes | None]:
        return [None for _ in timestamps]

    monkeypatch.setattr(fingerprints, "_grey_frames", nothing)

    await jobs.probe(
        await context_for("probe", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    stored = await content_store.get(ingested_video.asset.id)
    assert stored is not None
    assert stored.phash is None
    assert stored.videohash is None


async def test_a_file_too_small_to_have_an_exact_hash_is_recorded_as_having_none(
    ingested_video: Ingested,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    content_store: ContentStore,
    temp_db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Written down as an empty string rather than left unset, and the difference is the pass
    finishing.

    Left NULL, the same file comes back on every run for the rest of the library's life. The empty
    string is what says "asked, and there is no answer", which is permanent.
    """
    await temp_db.execute(
        "UPDATE assets SET oshash = NULL, video_phash = NULL WHERE id = ?",
        (ingested_video.asset.id,),
    )
    # And READ, for the reason the batch test above gives: this goes through the catch-up pass, and
    # the pass only offers files something has already looked at.
    await content_store.record_probe(ingested_video.asset.id, width=64, height=48, duration_ms=1000)

    async def too_small(*_args: object, **_kwargs: object) -> str | None:
        return None

    monkeypatch.setattr(hashing, "oshash_file", too_small)

    await jobs.fingerprint_stash_box(
        await context_for("fingerprint_stash_box", {}), settings=settings, hardware=hardware
    )

    stored = await content_store.get(ingested_video.asset.id)
    assert stored is not None
    assert stored.oshash == ""


async def test_a_file_the_stash_box_stills_cannot_be_read_from_gets_no_stash_box_fingerprint(
    ingested_video: Ingested,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    content_store: ContentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fingerprint the public stash-boxes share, and the one place it is allowed to be absent.

    A file ffmpeg refuses partway through has no honest value to record, and a wrong one is worse
    than none: this number's whole worth is matching what everybody else computed for the same
    video. The rest of probing still finishes.
    """

    # Only the stash-box path, not every use of ffmpeg in probing: refusing all of them fails
    # probing outright, which says nothing about this fingerprint being allowed to be absent.
    def refuses(*_args: object, **_kwargs: object) -> media.Moment:
        raise ValueError("this file cannot be read")

    monkeypatch.setattr(ffmpeg, "stash_box_moment", refuses)

    await probe_and_fingerprint(
        await context_for("probe", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    stored = await content_store.get(ingested_video.asset.id)
    assert stored is not None
    assert stored.video_phash == "", "a grid that could not be made is recorded as none"
    assert stored.phash, "the rest of the fingerprints did not finish"


# --- one process per file per pass ---------------------------------------------------------------
#
# One launch per moment would make a video's probe 55 launches (one per hash frame, one per
# stash-box still) and its scrub strip one per tile, up to four hundred. Over a share every launch
# is an open and a seek across the wire. These pin the count, and pin that the batched form is
# the SAME BYTES as the one-moment form, which is the whole of what makes it safe for a
# fingerprint.


async def test_the_thirty_hash_frames_of_a_video_come_from_one_process(
    video: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    launches = watch_launches(monkeypatch)
    timestamps = sampler.hash_frames(VIDEO_SECONDS * 1000)

    frames = await fingerprints._grey_frames(video, timestamps, settings=settings)

    assert len(frames) == len(timestamps) and all(frame is not None for frame in frames)
    # One decode for all thirty. Beside it at most one read of the picture's size, which is what
    # says how many moments fit one process's memory (`media.moments_per_process`); it is kept per
    # file, so every other batch of the same file's moments asks it again for nothing.
    decodes = [argv for argv in launches if argv[0] == settings.ffmpeg_path]
    assert len(decodes) == 1, launches
    assert decodes[0].count("-i") == len(timestamps)
    sizes = [argv for argv in launches if argv not in decodes]
    assert len(sizes) <= 1, launches
    assert all(argv[0] == settings.ffprobe_path for argv in sizes), launches


async def test_the_batched_hash_frames_are_the_bytes_the_one_frame_command_gives(
    video: Path, settings: Settings
) -> None:
    timestamps = sampler.hash_frames(VIDEO_SECONDS * 1000)
    batched = await fingerprints._grey_frames(video, timestamps, settings=settings)
    singly = [
        await media.run(
            ffmpeg.frame_args(video, at, size=perceptual.HASH_FRAME_SIZE, settings=settings),
            time_limit=60,
            capture=True,
        )
        for at in timestamps
    ]
    assert batched == singly


async def test_the_stash_box_stills_come_from_one_process_and_are_the_same_bytes(
    video: Path, ingested_video: Ingested, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The files one process writes are the bitmaps the one-still command puts on its pipe."""
    payload = await ffmpeg.run_json(ffmpeg.probe_args(video, settings=settings))
    probed = ffmpeg.parse_probe(payload)
    assert probed.duration_seconds
    source = Source(
        asset=ingested_video.asset, location=ingested_video.location, path=video, original=video
    )
    launches = watch_launches(monkeypatch)

    fingerprint = await fingerprints._video_phash(source, probed, settings=settings)

    stills = [argv for argv in launches if "bmp" in argv]
    assert len(stills) == 1 and stills[0].count("-i") == perceptual.SCENE_FRAMES
    shots = [
        await media.run(
            ffmpeg.stash_box_still_args(
                video, at, width=perceptual.SCENE_SHOT_WIDTH, settings=settings
            ),
            time_limit=60,
            capture=True,
        )
        for at in perceptual.scene_frame_times(probed.duration_seconds)
    ]
    assert fingerprint == perceptual.video_phash(shots)


# --- the pieces the Build calls, one file at a time -----------------------------------------------


async def test_a_still_that_came_out_empty_is_a_hole_in_the_grid(
    ingested_video: Ingested,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    content_store: ContentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A still ffmpeg wrote nothing into is the same hole as one it did not write at all: a grid
    with a hole is no fingerprint, never a fingerprint of the wrong picture."""

    async def empties(
        _path: Path, moments: Sequence[Any], *, into: Path, **_kwargs: Any
    ) -> list[Path | None]:
        written: list[Path | None] = []
        for index, _moment in enumerate(moments):
            one = into / f"{index}.bmp"
            one.write_bytes(b"")
            written.append(one)
        return written

    monkeypatch.setattr(fingerprints, "moments_to_files", empties)

    await probe_and_fingerprint(
        await context_for("probe", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    stored = await content_store.get(ingested_video.asset.id)
    assert stored is not None
    assert stored.video_phash == "", "a grid that could not be made is recorded as none"
    assert stored.phash, "the rest of the fingerprints did not finish"


# --- a file that can never decode is refused once, not three times -------------------------------


#: What ffmpeg says about a video whose container claims a packet far larger than the packet it
#: sits in. The gate passes it and ffprobe reads it (the header is perfect), and only a decode
#: of its frames finds out.
NAL_CORRUPT = "ffmpeg.exe failed: [h264 @ 0000] Invalid NAL unit size (1088342112 > 21767)."


async def test_a_file_whose_frames_will_not_decode_is_still_probed_and_told_about(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fingerprint failure does not fail probing, and it is not silent.

    Failing probing would leave no dimensions, no codecs, no thumbnail and no preview over a number
    whose only use is finding near-copies; swallowing the refusal would bring the file back at the
    head of the fingerprint pass's oldest-first order on every run with nothing saying why.
    """

    async def refuse(*_args: object, **_kwargs: object) -> list[bytes | None]:
        raise media.FFmpegError(NAL_CORRUPT)

    monkeypatch.setattr(fingerprints, "_grey_frames", refuse)

    await probe_and_fingerprint(
        await context_for("probe", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    asset = await content_store.get(ingested_video.asset.id)
    assert asset is not None
    assert asset.probed_at is not None, "a readable file was refused over its fingerprints"
    assert asset.width and asset.duration_ms, "the probe threw away what it had measured"

    verdict = await content_store.verdict_of(ingested_video.asset.id, VerdictProduct.FINGERPRINTS)
    assert verdict is not None, "nothing says why this file has no fingerprints"
    assert verdict.code == "not_decodable" and verdict.transient is False
    assert "Invalid NAL unit size" in verdict.reason, "the reason is not the decoder's own words"


async def test_the_stash_box_grid_refusing_the_bytes_lands_the_same_way(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the same block. `_video_phash` lets a broken-bytes refusal out, or a file
    whose bytes are broken would be indistinguishable from one with no running time."""

    async def refuse(*_args: object, **_kwargs: object) -> list[Path | None]:
        raise media.FFmpegError(NAL_CORRUPT)

    monkeypatch.setattr(fingerprints, "moments_to_files", refuse)

    await probe_and_fingerprint(
        await context_for("probe", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    asset = await content_store.get(ingested_video.asset.id)
    assert asset is not None and asset.probed_at is not None
    assert (
        await content_store.verdict_of(ingested_video.asset.id, VerdictProduct.FINGERPRINTS)
        is not None
    )


async def test_a_moment_that_read_back_nothing_is_still_not_a_verdict(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The known negative, and it is the one that keeps this honest.

    A seek past the last frame reads back nothing, which is ordinary (a file's declared duration
    and its real one disagree more often than not), and a grid with a hole in it is refused for a
    different reason entirely. Writing a permanent verdict on that would take a perfectly good file
    out of every comparison for the life of the library.
    """

    async def empties(*_args: object, **_kwargs: object) -> list[Path | None]:
        return [None]

    monkeypatch.setattr(fingerprints, "moments_to_files", empties)

    await probe_and_fingerprint(
        await context_for("probe", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    assert (
        await content_store.verdict_of(ingested_video.asset.id, VerdictProduct.FINGERPRINTS) is None
    ), "a hole in the grid was written down as a file that cannot be decoded"


async def test_a_file_that_cannot_be_fingerprinted_is_not_offered_to_the_pass_again(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of writing it down. `unfingerprinted` is ordered oldest first and the chain
    stops when a batch records nothing, so a handful of files the decoder will not read sit at the
    head of that order and the pass spends every run re-reading them.
    """

    async def refuse(*_args: object, **_kwargs: object) -> list[bytes | None]:
        raise media.FFmpegError(NAL_CORRUPT)

    monkeypatch.setattr(fingerprints, "_grey_frames", refuse)

    await probe_and_fingerprint(
        await context_for("probe", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    assert ingested_video.asset.id not in await content_store.unfingerprinted(10)
    assert await content_store.unfingerprinted_count() == 0
    assert await content_store.unfingerprinted_among([ingested_video.asset.id]) == set()

    # THE KNOWN POSITIVE, and without it this passes for the wrong reason: the file was read and
    # its four columns are still NULL, so it is squarely in the work list on every other ground.
    # Forgetting the verdict (which is what the Generate sheet offers) puts it straight back.
    await content_store.clear_verdicts(VerdictProduct.FINGERPRINTS)
    assert ingested_video.asset.id in await content_store.unfingerprinted(10)


async def test_the_catch_up_pass_writes_the_same_verdict_when_it_meets_the_refusal(
    ingested_video: Ingested,
    content_store: ContentStore,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file taken in by a scan-only pass meets the decoder here rather than in probing, so the
    sentence on its page has to come from here too. Empty columns already stopped it coming back;
    they could not say why there is nothing to compare it by."""

    async def refuse(*_args: object, **_kwargs: object) -> list[bytes | None]:
        raise media.FFmpegError(NAL_CORRUPT)

    monkeypatch.setattr(fingerprints, "_grey_frames", refuse)

    assert (
        await jobs.fingerprint_one(content_store, ingested_video.asset.id, settings=settings)
        is False
    )

    verdict = await content_store.verdict_of(ingested_video.asset.id, VerdictProduct.FINGERPRINTS)
    assert verdict is not None and "Invalid NAL unit size" in verdict.reason


async def test_a_share_that_blinked_during_a_fingerprint_leaves_no_verdict(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The half that has to stay true, and it is the same rule probing's own verdict follows:
    anything the tool says that is not recognised as being about the BYTES keeps its retries. A
    permanent verdict on a good file is the one mistake that cannot be taken back."""

    async def refuse(*_args: object, **_kwargs: object) -> list[bytes | None]:
        raise media.FFmpegError("ffmpeg.exe failed: Input/output error")

    monkeypatch.setattr(fingerprints, "_grey_frames", refuse)

    with pytest.raises(media.FFmpegError):
        await probe_and_fingerprint(
            await context_for("probe", {"asset_id": ingested_video.asset.id}),
            settings=settings,
            hardware=hardware,
        )

    assert (
        await content_store.verdict_of(ingested_video.asset.id, VerdictProduct.FINGERPRINTS) is None
    )


async def test_a_refused_fingerprint_keeps_the_numbers_the_file_already_had(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file read again meets this as often as a new one does, and the fingerprint write sets all
    four, so writing empties would blank fingerprints the file was given while it still decoded.
    The read hands the fingerprints out as their own job, so the job is where the numbers are
    kept."""
    await content_store.record_fingerprints(
        ingested_video.asset.id,
        phash="aaaabbbbccccdddd",
        videohash="bb",
        oshash="0123456789abcdef",
        video_phash="cc",
    )
    # Age them, so the read hands the fingerprints out again and the job meets the refusal.
    await content_store._db.execute(
        "UPDATE assets SET fingerprint_version = 0 WHERE id = ?", (ingested_video.asset.id,)
    )

    async def refuse(*_args: object, **_kwargs: object) -> list[bytes | None]:
        raise media.FFmpegError(NAL_CORRUPT)

    monkeypatch.setattr(fingerprints, "_grey_frames", refuse)

    probing = await context_for("probe", {"asset_id": ingested_video.asset.id})
    await jobs.probe(probing, settings=settings, hardware=hardware)
    (handed,) = [
        child
        for child in await probing.queue.children(probing.job.id)
        if child.type == jobs.FINGERPRINT_FILE
    ]
    await jobs.fingerprint_arrival(
        await context_for(jobs.FINGERPRINT_FILE, handed.payload), settings=settings
    )

    asset = await content_store.get(ingested_video.asset.id)
    assert asset is not None
    assert asset.phash == "aaaabbbbccccdddd", "a rescan blanked a fingerprint it could not redo"


# --- the order a file's products are handed out in, and the urgency they carry --------------------


async def test_a_second_probe_does_not_hash_a_file_that_already_has_its_fingerprints(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fifty-five seeks into a file for four numbers already stored beside it.

    The player asks for probing whenever somebody presses play on a file it cannot describe, the
    queue collapses onto a waiting probe and deliberately not onto a running one, and a retry after
    a later failure starts again from the top, so the second ask is ordinary. What it must not do
    is pay for the hashing again, nor blank what is there with a write meant to fill in two fields.
    """
    first = await context_for("probe", {"asset_id": ingested_video.asset.id})
    await probe_and_fingerprint(first, settings=settings, hardware=hardware)
    before = await content_store.get(ingested_video.asset.id)
    assert before is not None and before.phash is not None

    async def refuses(*_args: Any, **_kwargs: Any) -> tuple[str | None, str | None]:
        raise AssertionError("the probe hashed a file that already had its fingerprints")

    monkeypatch.setattr(fingerprints, "_fingerprint", refuses)
    monkeypatch.setattr(hashing, "oshash_file", refuses)

    second = await context_for("probe", {"asset_id": ingested_video.asset.id})
    await probe_and_fingerprint(second, settings=settings, hardware=hardware)

    handed = [child.type for child in await second.queue.children(second.job.id)]
    assert jobs.FINGERPRINT_FILE not in handed, "a second read handed the fingerprints out again"
    after = await content_store.get(ingested_video.asset.id)
    assert after is not None
    assert (after.phash, after.videohash, after.oshash, after.video_phash) == (
        before.phash,
        before.videohash,
        before.oshash,
        before.video_phash,
    )


async def test_a_file_missing_one_fingerprint_is_still_hashed(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """All four, never some. A file with three of them is a file the fingerprint pass still wants,
    and answering "already done" here would leave it half-hashed for ever."""
    context = await context_for("probe", {"asset_id": ingested_video.asset.id})

    await probe_and_fingerprint(context, settings=settings, hardware=hardware)

    asset = await content_store.get(ingested_video.asset.id)
    assert asset is not None
    assert fingerprints._has_its_fingerprints(asset)
    assert not fingerprints._has_its_fingerprints(replace(asset, video_phash=None))
    assert not fingerprints._has_its_fingerprints(replace(asset, oshash=None))


# --- sampling across the picture, not across the file ---------------------------------------------
#
# A file's length is the container's answer, and the container answers for the sound as well. On a
# file whose sound outlasts its picture, moments spread across the whole of it fall past the last
# frame: the hover clip comes out short and the fingerprint holds its last frame for the rest.


async def test_a_fingerprint_is_sampled_inside_the_picture_of_a_file_whose_sound_runs_on(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Thirty moments across the 4 s of picture, not across the 10 s of file."""
    asked: list[int] = []

    async def frames(path: Path, timestamps: Sequence[int], **kwargs: Any) -> list[bytes | None]:
        asked.extend(timestamps)
        return [None] * len(timestamps)

    monkeypatch.setattr(fingerprints, "_grey_frames", frames)
    from types import SimpleNamespace

    source: Any = SimpleNamespace(asset=SimpleNamespace(media_type="video"), path=Path("x"))
    probed = ffmpeg.Probed(
        width=64,
        height=64,
        duration_ms=10_000,
        fps=10.0,
        vcodec="h264",
        acodec="aac",
        video_duration_ms=4_000,
        duration_seconds=10.0,
    )

    await fingerprints._fingerprint(source, probed, settings=settings)

    assert asked == list(sampler.hash_frames(4_000))
    assert max(asked) < 4_000


async def test_a_file_fingerprinted_below_the_generation_in_use_is_not_taken_as_fingerprinted(
    ingested_picture: Ingested,
) -> None:
    """A probe does not fingerprint a file that already has its fingerprints, unless they are an
    older generation's, or were sampled across more than the file's picture holds, which is what a
    zero says. Then the read of the file is the moment to take them again."""
    held = replace(ingested_picture.asset, phash="aa", fingerprint_version=1)
    owed = replace(held, fingerprint_version=0)

    assert fingerprints._has_its_fingerprints(held)
    assert not fingerprints._has_its_fingerprints(owed)


# --- the catch-up passes at their edges ---------------------------------------------------------


async def test_a_fingerprint_job_for_a_file_that_has_gone_fails_as_missing(
    context_for: Context, settings: Settings
) -> None:
    context = await context_for(jobs.FINGERPRINT_FILE, {"asset_id": new_id()})

    with pytest.raises(jobs.MissingAsset):
        await jobs.fingerprint_arrival(context, settings=settings)


async def test_a_file_out_of_reach_is_left_without_fingerprints_for_the_catch_up_to_find(
    video: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    context_for: Context,
) -> None:
    """Nobody measured it, so nothing is written, and nothing that reads fingerprints is asked
    for on its account."""
    from sift.slices.media_jobs.tests.conftest import take_in

    taken = await take_in(content_store, library_root, video, settings)
    video.unlink()
    context = await context_for(jobs.FINGERPRINT_FILE, {"asset_id": taken.asset.id})
    settled: list[object] = []

    async def noting(job_types: Sequence[str], *, priority: int = 0) -> None:
        settled.append(job_types)

    context.queue.settle_into = noting  # type: ignore[method-assign]

    await jobs.fingerprint_arrival(context, settings=settings, settles_into=("dedup_scan",))

    assert settled == []
    after = await content_store.get(taken.asset.id)
    assert after is not None and after.phash is None


async def test_a_kept_probe_that_cannot_be_read_is_not_trusted(tmp_path: Path) -> None:
    """A kept answer that does not decompress is no answer: a caller that may not ask the tool
    gets a reading with no picture rather than a raise."""
    from types import SimpleNamespace

    class _Kept:
        async def kept_probe(self, _asset_id: str) -> bytes:
            return b"not a compressed reading"

    clip = tmp_path / "clip.mp4"
    source: Any = SimpleNamespace(
        asset=SimpleNamespace(id="a1", media_type="video"), path=clip, original=clip
    )
    store: Any = _Kept()
    probed = await fingerprints.probed_of(store, source, settings=cast(Any, None), ask=False)
    assert probed.vcodec is None
