# SPDX-License-Identifier: AGPL-3.0-or-later
"""The read of a file, run against real files with real ffmpeg: what it writes, and the work it hands out."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from structlog.testing import capture_logs

# A fingerprint write records an event through the ledger door, and the door writes into the
# workbench's own table, so a database built without that component has nowhere to put it.
# Imported for the registration, nothing else. Without it this module passes only when some
# OTHER module collected in the same run happens to have imported it, and fails on its own;
# the content suite carries the same line for the same reason.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel import mp4
from sift.kernel.config import Settings
from sift.kernel.content import (
    ContentStore,
    Ingested,
    VerdictProduct,
)
from sift.kernel.content.identity import PROBE_VERSION
from sift.kernel.db import Database
from sift.kernel.hardware import HardwareReport
from sift.kernel.ids import new_id
from sift.kernel.ingress import CLASSIFIER_VERSION, Kind, MediaType, Origin, verify_ingress
from sift.kernel.jobs import (
    JobContext,
    JobFailedPermanently,
    JobQueue,
    SystemCapabilities,
)
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.switchboard import JobSwitchedOff
from sift.kernel.jobs.tuning import BACKGROUND_PRIORITY, DEFAULT_PRIORITY, WAITED_ON_PRIORITY
from sift.kernel.jobs.worker_pool import registered_families, registered_handlers
from sift.kernel.media import Source
from sift.slices.importing.service import ImportPolicy
from sift.slices.importing.store import RootPreferences
from sift.slices.media_jobs import (
    ffmpeg,
    jobs,
    probing,
    tuning,
)
from sift.slices.media_jobs.tests.conftest import VIDEO_SECONDS, webp_tools
from sift.slices.media_jobs.tests.support import Context, probe_and_fingerprint, watch_enqueues
from sift.testing.fixtures import LibraryRoot
from sift.testing.logs import uncached_log

pytestmark = [pytest.mark.integration]


# --- probe ------------------------------------------------------------------------------------


async def test_probe_records_what_the_file_actually_is(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    asset_id = ingested_video.asset.id
    assert ingested_video.asset.probed_at is None

    await jobs.probe(
        await context_for("probe", {"asset_id": asset_id}), settings=settings, hardware=hardware
    )

    asset = await content_store.get(asset_id)
    assert asset is not None
    assert (asset.width, asset.height) == (320, 240)
    assert asset.duration_ms == pytest.approx(VIDEO_SECONDS * 1000, abs=200)
    assert asset.vcodec == "h264"
    assert asset.probed_at is not None
    # Frame rate, which the player needs and nothing else does. It is half of "can this machine
    # convert this file faster than it plays" (a 4K/120 source costs twice a 4K/60 one for the
    # same pixels), so probing that quietly stopped recording it would leave the player projecting
    # from resolution alone and promising smooth playback it cannot deliver.
    assert asset.fps == pytest.approx(15.0, abs=0.1)
    # The container comes from the gate, which read the file's structure. ffprobe would have said
    # "mov,mp4,m4a,3gp,3g2,mj2" and left someone to pick one.
    assert asset.container == "mp4"


async def test_an_animated_webp_is_probed_through_a_readable_copy(
    animated_webp: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    tmp_path: Path,
) -> None:
    """The format ffmpeg cannot read, going all the way through the stage that reads everything.

    Two things are being asserted and the second is the one that bites. The obvious one: probing
    produces real dimensions, a duration and a fingerprint rather than failing. The other: it works
    *during probing*, which is before anything has written down what the container is, so
    deciding whether a file needs converting cannot depend on a column probing itself fills in.
    """
    settings = settings.model_copy(update=webp_tools(tmp_path / "tools", frames=5, size="64x64"))
    checked = verify_ingress(animated_webp, origin=Origin.SCAN, settings=settings)
    assert checked.media.name == "webp-animated"
    ingested = await content_store.ingest(
        checked, root_id=library_root.id, rel_path=animated_webp.name
    )
    assert ingested.asset.container is None

    await jobs.probe(
        await context_for("probe", {"asset_id": ingested.asset.id}),
        settings=settings,
        hardware=hardware,
    )
    # The fingerprints are the file's own job, handed out by the read; run it as the pool
    # would, so the readable copy the read made is what it hashes.
    await jobs.fingerprint_arrival(
        await context_for(jobs.FINGERPRINT_FILE, {"asset_id": ingested.asset.id}),
        settings=settings,
    )

    asset = await content_store.get(ingested.asset.id)
    assert asset is not None
    assert (asset.width, asset.height) == (64, 64)
    # A GIF, so it keeps a duration and is fingerprinted on both axes like any other.
    assert asset.duration_ms
    assert asset.phash is not None
    assert asset.videohash is not None
    # And the copy everything else reads is recorded, so this happens once rather than per stage.
    kinds = [one.kind.value for one in await content_store.derivatives(ingested.asset.id)]
    assert kinds == ["rendition"]


async def test_a_still_is_never_given_a_duration_however_ffprobe_read_it(
    ingested_picture: Ingested,
    ingested_video: Ingested,
    picture: Path,
    video: Path,
) -> None:
    """A photograph has no length, whatever the decoder that opened it says.

    ffprobe does not report a picture as duration-less. A normally-sized jpeg is demuxed as
    `image2` and comes back as a one-frame video at 25 fps (0.04 seconds), which is a true
    statement about a decoder and a false one about a photograph, which the grid would draw as a
    0:00 timestamp in the corner of the tile.

    The probing test above passes for the wrong reason and cannot be relied on for this: its
    fixture is a small PNG, and a small still is demuxed as `png_pipe`, which reports nothing. So
    the number ffprobe really returns for a photograph is handed in directly, rather than hoping the
    fixture reproduces it.
    """
    read_as_a_one_frame_video = ffmpeg.Probed(
        width=320, height=240, duration_ms=40, fps=25.0, vcodec="mjpeg", acodec=None
    )
    still = Source(
        asset=ingested_picture.asset,
        location=ingested_picture.location,
        path=picture,
        original=picture,
    )
    moving = Source(
        asset=ingested_video.asset,
        location=ingested_video.location,
        path=video,
        original=video,
    )

    assert probing._duration_of(still, read_as_a_one_frame_video) is None
    # And the same forty milliseconds is kept for anything that really runs, so this discards a
    # media type rather than a suspiciously small number.
    assert probing._duration_of(moving, read_as_a_one_frame_video) == 40


async def test_probe_refuses_a_file_that_does_not_decode(
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    video: Path,
) -> None:
    """The signature check proves the container is what it claims. Only this proves it is intact.

    A truncated file has a perfect header, so it passes the gate on its way in and hits the
    decoder as malformed input, which is the one moment a file someone else made influences a
    program that was not expecting it. Nothing is built from it, and nothing is written down.
    """
    from sift.slices.media_jobs.tests.conftest import take_in

    taken = await take_in(content_store, library_root, video, settings)
    video.write_bytes(video.read_bytes()[: len(video.read_bytes()) // 3])

    with pytest.raises(jobs.Unusable, match="could not be read"):
        await jobs.probe(
            await context_for("probe", {"asset_id": taken.asset.id}),
            settings=settings,
            hardware=hardware,
        )

    asset = await content_store.get(taken.asset.id)
    assert asset is not None
    assert asset.probed_at is None, "a file that will not decode was written down as probed"
    assert await content_store.derivatives(taken.asset.id) == []


async def test_a_file_that_will_not_decode_is_failed_once_and_written_on_the_file(
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    video: Path,
) -> None:
    """The queue is told, and so is the file. `Unusable` is not an ordinary error attempted three
    times: the verdict is what every Build and every catch-up pass leaves out. The read is
    probing's own product.

    Driven through the handler as registered, not through `probe` wrapped by hand, so a
    registration that wrapped nothing would fail here.
    """
    from sift.slices.media_jobs.tests.conftest import take_in

    assert issubclass(jobs.Unusable, JobFailedPermanently)
    taken = await take_in(content_store, library_root, video, settings)
    video.write_bytes(video.read_bytes()[: len(video.read_bytes()) // 3])
    probe = registered_handlers()[jobs.PROBE]

    with pytest.raises(jobs.Unusable):
        await probe(await context_for("probe", {"asset_id": taken.asset.id}))

    verdict = await content_store.verdict_of(taken.asset.id, VerdictProduct.PROBE)
    assert verdict is not None
    assert verdict.code == "not_decodable" and verdict.transient is False
    assert "could not be read" in verdict.reason
    assert await content_store.unread_count() == 0, "read and refused is not unread"


async def test_probe_never_moves_a_file_it_refuses(
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    video: Path,
) -> None:
    """A file in a library root is the user's. Refused or not, it stays where they put it.

    Quarantine means moving a file, and moving someone's video because a decoder disliked it is
    not a thing to do to them. It is theirs; Sift indexes it where it lies.
    """
    from sift.slices.media_jobs.tests.conftest import take_in

    taken = await take_in(content_store, library_root, video, settings)
    video.write_bytes(b"\x00" * 4096)

    with pytest.raises(jobs.Unusable):
        await jobs.probe(
            await context_for("probe", {"asset_id": taken.asset.id}),
            settings=settings,
            hardware=hardware,
        )

    assert video.exists(), "a scanned file was moved out of the library"
    assert list(library_root.path.iterdir()) == [video]


async def test_probe_starts_the_jobs_that_draw_the_file(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """One file is one thing on the dashboard, with its work hanging off it."""
    context = await context_for("probe", {"asset_id": ingested_video.asset.id})

    await jobs.probe(context, settings=settings, hardware=hardware)

    children = await job_queue.children(context.job.id)
    assert {child.type for child in children} == {
        "thumbnail",
        "preview",
        "sprite",
        "fingerprint_file",
    }
    assert all(child.payload == {"asset_id": ingested_video.asset.id} for child in children)


async def test_a_probe_writes_the_reading_and_its_files_work_and_nothing_else(
    ingested_video: Ingested,
    context_for: Context,
    temp_db: Database,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every write waits its turn at the single writer behind every other, so a probe makes two:
    the reading on the file's row, and the work it hands out in one. No progress of its own (it
    is one file, done or not) and no heartbeat to hear a stop the pool's heartbeat already hears."""
    context = await context_for("probe", {"asset_id": ingested_video.asset.id})
    blocks = 0
    write = temp_db.write

    @asynccontextmanager
    async def counted() -> AsyncIterator[object]:
        nonlocal blocks
        blocks += 1
        async with write() as connection:
            yield connection

    monkeypatch.setattr(temp_db, "write", counted)
    await jobs.probe(context, settings=settings, hardware=hardware)

    assert blocks == 2


async def test_a_row_whose_kind_its_bytes_refute_goes_back_to_the_classifier_unread(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """A video stored as a still: the read writes no mime of the other kind on it and makes no
    pictures for what it is not. The row goes below the classifier line, and the reclassify pass
    it asks for types it from the same answer, mime and kind together."""
    asset_id = ingested_video.asset.id
    still = MediaType("png", Kind.IMAGE, "png", frozenset({".png"}), "image/png")
    await content_store.reclassify(asset_id, still)
    context = await context_for("probe", {"asset_id": asset_id})

    await jobs.probe(context, settings=settings, hardware=hardware)

    asset = await content_store.get(asset_id)
    assert asset is not None
    assert (asset.media_type, asset.mime) == ("image", "image/png"), "nothing written over it"
    assert asset.probed_at is None
    assert asset.classified_version < CLASSIFIER_VERSION
    assert await job_queue.children(context.job.id) == []
    assert await job_queue.outstanding(jobs.RECLASSIFY) == 1

    await jobs.reclassify(
        await context_for(jobs.RECLASSIFY, {}), settings=settings, hardware=hardware
    )

    asset = await content_store.get(asset_id)
    assert asset is not None
    assert (asset.media_type, asset.mime) == ("video", "video/mp4")
    assert asset.classified_version == CLASSIFIER_VERSION


async def test_a_scan_only_probe_hands_out_the_thumbnail_and_the_fingerprints_it_skipped(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scan asked for on its own reads the file, makes its picture, and books the rest.

    The thumbnail is the one thing it hands out as a child, so no tile is ever pictureless. Every
    other stage the pipeline would have started is left alone, which is the point of the flag:
    probing is where every other stage begins, so a scan of a library would otherwise be the
    whole pipeline whatever the button that began it said.

    The fingerprint sweep is still asked for: asking for nothing would not make it wait, it would
    make it never happen, since nothing else queues `fingerprint_stash_box` for new files. What
    keeps it from competing with the scan on the same share is the priority: the request goes in
    at `BACKGROUND_PRIORITY`, so no worker takes it while there is a file read
    somebody is waiting on, and `register_handler(alone=True)` keeps a second copy off the machine.
    The pass is booked and it waits, which is what "later, when the share is free" actually needs.

    The reading itself must still happen, which is the half that makes this a scan rather than a
    no-op: a guard put in one line too high would leave the file with no dimensions at all, and
    every assertion about children below would still pass.
    """
    asked: list[str] = []

    async def noting(job_type: str, *args: object, **kwargs: object) -> str:
        asked.append(job_type)
        return "never"

    monkeypatch.setattr(job_queue, "enqueue_when_settled", noting)
    asset_id = ingested_video.asset.id
    context = await context_for("probe", {"asset_id": asset_id, "scan_only": True})
    await jobs.probe(context, settings=settings, hardware=hardware)
    assert [child.type for child in await job_queue.children(context.job.id)] == [jobs.THUMBNAIL]
    assert asked == [jobs.FINGERPRINT_FOR_STASH_BOXES], (
        "a scan-only probe asked for work beyond the fingerprints it deliberately skipped"
    )
    asset = await content_store.get(asset_id)
    assert asset is not None
    assert asset.probed_at is not None
    assert (asset.width, asset.height) == (320, 240)


@pytest.mark.parametrize("scan_only", [True, False])
async def test_every_file_gets_its_thumbnail_whatever_the_switches_say(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
    scan_only: bool,
) -> None:
    """A file with no thumbnail cannot be drawn on a wall, so it is made as every file arrives and
    no Generate switch or When refuses it; everything else is still asked."""

    async def nothing(_job_type: str, _asset_id: str | None) -> bool:
        return False

    payload: dict[str, object] = {"asset_id": ingested_video.asset.id}
    if scan_only:
        payload["scan_only"] = True
    context = await context_for("probe", payload)
    await jobs.probe(context, settings=settings, hardware=hardware, should_generate=nothing)
    children = await job_queue.children(context.job.id)
    assert [child.type for child in children] == ["thumbnail"]


async def test_the_thumbnail_is_handed_out_before_the_fingerprints_are_made(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fingerprints are most of what reading a file costs, and every picture would wait
    behind them. The read hashes nothing: it hands out the thumbnail first and the fingerprints as
    a job of their own, after every picture."""
    context = await context_for("probe", {"asset_id": ingested_video.asset.id})
    written: list[str] = []

    async def watched(asset_id: str, **_kwargs: object) -> None:
        written.append(asset_id)

    monkeypatch.setattr(content_store, "record_fingerprints", watched)
    await jobs.probe(context, settings=settings, hardware=hardware)

    handed = [child.type for child in await job_queue.children(context.job.id)]
    assert written == [], "the read wrote no fingerprint"
    # Fingerprint's work, on Fingerprint's row, where the product and a pressed Generate's
    # fingerprints already count (`PRODUCT_FAMILIES`): one product, one row.
    assert registered_families()[jobs.FINGERPRINT_FILE] is Family.FINGERPRINT
    assert handed[0] == jobs.THUMBNAIL
    assert handed.count(jobs.FINGERPRINT_FILE) == 1
    assert handed.index(jobs.FINGERPRINT_FILE) > max(
        handed.index(jobs.PREVIEW), handed.index(jobs.SPRITE)
    ), "the fingerprints come after every picture"
    asset = await content_store.get(ingested_video.asset.id)
    assert asset is not None and asset.width and asset.phash is None


async def test_a_probe_does_not_fingerprint_where_fingerprints_are_switched_off(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The fingerprint job is made only where the switch the Generate task asks says so, and once."""
    asked: list[str] = []

    async def no_fingerprints(job_type: str, _asset_id: str | None = None) -> bool:
        asked.append(job_type)
        return job_type != jobs.FINGERPRINT_FILE

    asset_id = ingested_video.asset.id
    off = await context_for("probe", {"asset_id": asset_id})
    await jobs.probe(off, settings=settings, hardware=hardware, should_generate=no_fingerprints)

    asset = await content_store.get(asset_id)
    assert asset is not None and asset.width, "the file was still read"
    assert asset.phash is None and asset.oshash is None
    assert jobs.FINGERPRINT_FILE in asked
    handed = [child.type for child in await off.queue.children(off.job.id)]
    assert jobs.FINGERPRINT_FILE not in handed, "switched off, no fingerprint job is made"

    on = await context_for("probe", {"asset_id": asset_id})
    await jobs.probe(on, settings=settings, hardware=hardware)
    handed = [child.type for child in await on.queue.children(on.job.id)]
    assert handed.count(jobs.FINGERPRINT_FILE) == 1, "switched on, exactly one"


async def test_a_scan_only_probe_does_no_fingerprinting(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """A scan-only pass does not spend the fingerprints.

    Most of probing is perceptual hashing, 55 seeks into every video. It is real
    work and worth doing (it is what finds near-duplicates), but it is not what the
    Scan button offers, and not what somebody watching the Scan queue is waiting for. So a
    scan-only pass leaves all four behind and the Fingerprint queue fills them in.

    Asserted on the COLUMNS rather than by counting ffmpeg calls: what matters is what the file is
    left holding, and a count would pass just as well if the work were done and thrown away.
    """
    asset_id = ingested_video.asset.id
    context = await context_for("probe", {"asset_id": asset_id, "scan_only": True})

    await jobs.probe(context, settings=settings, hardware=hardware)

    asset = await content_store.get(asset_id)
    assert asset is not None
    assert asset.phash is None, "a scan asked for on its own still built a frame fingerprint"
    assert asset.videohash is None
    assert asset.oshash is None
    assert asset.video_phash is None
    # And the file IS waiting for one, which is the half that keeps this from being data loss.
    assert await content_store.unfingerprinted(10) == [asset_id]


async def test_an_ordinary_probe_still_fingerprints(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The known positive. Without it the test above is satisfied by a probe that fingerprints
    nothing at all, which would be near-duplicate detection silently switched off for every file
    that ever arrives in a watched folder."""
    asset_id = ingested_video.asset.id
    context = await context_for("probe", {"asset_id": asset_id})

    await probe_and_fingerprint(context, settings=settings, hardware=hardware)

    asset = await content_store.get(asset_id)
    assert asset is not None
    assert asset.phash, "an ordinary probe stopped fingerprinting"
    assert asset.oshash is not None
    assert await content_store.unfingerprinted(10) == []


async def test_a_second_scan_only_pass_does_not_erase_the_fingerprints(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The trap a scan-only pass sets, and the one that would be silent.

    `record_probe` writes every column it names, so a scan-only pass with nothing to say about a
    fingerprint would BLANK one that was already there. A rescan reads files it has read before, so
    that is not a corner case: it is every file in the library, on the second scan, and the only
    symptom would be a duplicates screen that had quietly stopped finding anything.
    """
    asset_id = ingested_video.asset.id
    await probe_and_fingerprint(
        await context_for("probe", {"asset_id": asset_id}), settings=settings, hardware=hardware
    )
    before = await content_store.get(asset_id)
    assert before is not None and before.phash

    await probe_and_fingerprint(
        await context_for("probe", {"asset_id": asset_id, "scan_only": True}),
        settings=settings,
        hardware=hardware,
    )

    after = await content_store.get(asset_id)
    assert after is not None
    assert after.phash == before.phash, "a scan-only rescan erased the fingerprints it did not take"
    assert after.oshash == before.oshash
    assert after.probed_at is not None


async def test_a_derivative_turned_off_in_settings_is_not_started(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The per-kind switches. With previews turned off probing starts the thumbnail and the sprite
    but not the preview, and the answer is read per file, so a rescan with it back on fills it in.
    The identity work is not on this list: a scan always probes, whatever the switches say."""
    disabled = {"preview"}

    async def should_generate(job_type: str, _asset_id: str | None = None) -> bool:
        return job_type not in disabled

    context = await context_for("probe", {"asset_id": ingested_video.asset.id})

    await jobs.probe(context, settings=settings, hardware=hardware, should_generate=should_generate)

    children = await job_queue.children(context.job.id)
    assert {child.type for child in children} == {
        "thumbnail",
        "sprite",
        "fingerprint_file",
    }  # no preview


async def test_a_still_gets_no_sprite_job(
    ingested_picture: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """There is nothing to scrub through. A job whose only outcome is to discover it has nothing
    to do is a job that should not have been queued."""
    context = await context_for("probe", {"asset_id": ingested_picture.asset.id})

    await jobs.probe(context, settings=settings, hardware=hardware)

    children = await job_queue.children(context.job.id)
    # Nor a hover clip: a still does not move, and a clip nothing makes would be counted missing.
    assert {child.type for child in children} == {"thumbnail", "fingerprint_file"}


async def test_a_job_for_an_asset_that_is_gone_says_so(
    context_for: Context, settings: Settings, hardware: HardwareReport
) -> None:
    with pytest.raises(jobs.MissingAsset):
        await jobs.probe(
            await context_for("probe", {"asset_id": new_id()}), settings=settings, hardware=hardware
        )


async def test_a_job_with_no_asset_id_is_a_bug_and_says_so(
    context_for: Context, settings: Settings, hardware: HardwareReport
) -> None:
    payloads: list[dict[str, object]] = [{}, {"asset_id": ""}, {"asset_id": 12}]
    for payload in payloads:
        with pytest.raises(ValueError, match="asset_id"):
            await jobs.probe(
                await context_for("probe", payload), settings=settings, hardware=hardware
            )


async def test_an_unreachable_copy_is_worth_retrying_and_a_missing_asset_is_not(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    video: Path,
) -> None:
    """The distinction the two exceptions exist for.

    A NAS that is offline is the commonest cause of a file not opening, and the file is fine:
    retrying is exactly right. An asset that has been deleted is not coming back, and every
    attempt finds the same nothing.
    """
    video.unlink()

    with pytest.raises(jobs.NoReadableCopy, match="not connected"):
        await jobs.probe(
            await context_for("probe", {"asset_id": ingested_video.asset.id}),
            settings=settings,
            hardware=hardware,
        )


async def test_a_location_already_known_to_be_gone_is_not_opened(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """It is known to be gone. The thing sitting at that path now is not the file that was there."""
    await content_store.mark_missing(ingested_video.location.id)

    with pytest.raises(jobs.NoReadableCopy):
        await jobs.probe(
            await context_for("probe", {"asset_id": ingested_video.asset.id}),
            settings=settings,
            hardware=hardware,
        )


# --- concurrency ------------------------------------------------------------------------------


async def test_probe_reads_a_gif(
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    animation: Path,
) -> None:
    """A GIF is its own media type, and it has a timeline like a video does."""
    from sift.slices.media_jobs.tests.conftest import take_in

    taken = await take_in(content_store, library_root, animation, settings)
    assert taken.asset.media_type == "gif"

    await probe_and_fingerprint(
        await context_for("probe", {"asset_id": taken.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    asset = await content_store.get(taken.asset.id)
    assert asset is not None
    assert (asset.width, asset.height) == (64, 64)
    assert asset.duration_ms is not None and asset.duration_ms > 0
    assert asset.container == "gif"
    assert asset.vcodec == "gif"
    # Both fingerprints: a GIF moves, so it is compared like a video and not like a photograph.
    assert asset.phash is not None
    assert asset.videohash is not None and len(asset.videohash) == 16 * 30


# --- measuring a library that predates the measurement -------------------------------------------


async def test_a_settling_pass_switched_off_at_the_board_is_skipped_and_said(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The passes over the whole library have their switches at the queue's board, not at the
    import gate above them. One switched off there refuses the ask; the probe notes it and
    finishes, with nothing raised at the person."""
    context = await context_for("probe", {"asset_id": ingested_video.asset.id})

    async def refusing(job_type: str, payload: object = None, **how: object) -> str:
        raise JobSwitchedOff(f"{job_type} is switched off")

    context.queue.enqueue_when_settled = refusing  # type: ignore[method-assign]
    uncached_log(monkeypatch, probing)
    with capture_logs() as written:
        await jobs.probe(
            context,
            settings=settings,
            hardware=hardware,
            settles_into=(jobs.FINGERPRINT_FOR_STASH_BOXES,),
        )
    assert [line["job_type"] for line in written if line["event"] == "media.settling_skipped"] == [
        jobs.FINGERPRINT_FOR_STASH_BOXES
    ]
    assert await job_queue.outstanding(jobs.FINGERPRINT_FOR_STASH_BOXES) == 0


# --- the three passes over a library that predates a feature --------------------------------------
#
# Each walks a batch, and each is asked for again while there is more. What is pinned here is the
# case where there is nothing to do: an install where the feature was always on runs all three on
# every start, so "nothing to do" is the ordinary path rather than an edge, and it must not ask for
# itself again: a pass that re-queues on an empty library never stops.


async def test_whole_library_work_a_probe_would_ask_for_is_skipped_when_it_is_switched_off(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The switch is read for the whole-library follow-on too, not only for the per-file children.

    Two separate loops ask it, and a test of one of them alone would let turning a feature off
    still leave the pass that serves it queued on every import.
    """

    async def never(_job_type: str, _asset_id: str | None = None) -> bool:
        return False

    # Nothing asks for it first. `enqueue_when_settled` DEDUPES, so a probe that had already
    # queued it would make a second one indistinguishable from none, and this test would pass
    # against code that ignored the switch entirely.
    refused = await context_for("probe", {"asset_id": ingested_video.asset.id})

    await jobs.probe(
        refused,
        settings=settings,
        hardware=hardware,
        should_generate=never,
        settles_into=(jobs.FINGERPRINT_FOR_STASH_BOXES,),
    )

    assert await job_queue.outstanding(jobs.FINGERPRINT_FOR_STASH_BOXES) == 0
    # Only the thumbnail, which no switch refuses.
    assert [child.type for child in await job_queue.children(refused.job.id)] == ["thumbnail"]


async def test_a_file_removed_while_it_was_being_probed_says_so(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window between reading a file and writing down what was read.

    Deleting a file mid-read is ordinary (a scan and somebody tidying up race all the time),
    and the write simply finds no row. It has to be a refusal rather than a silent success, or the
    job reports having measured something that is not there.
    """
    context = await context_for("probe", {"asset_id": ingested_video.asset.id})

    # The row has to be there when the file is RESOLVED and gone by the time the reading is
    # written down: deleting it up front is refused earlier, by a different guard, and proves
    # nothing about this one.
    async def vanished(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(content_store, "record_probe", vanished)

    with pytest.raises(jobs.MissingAsset, match="removed while it was being probed"):
        await jobs.probe(context, settings=settings, hardware=hardware)


async def test_whole_library_work_a_probe_asks_for_is_queued_when_it_is_switched_on(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The companion to the one above, and the pair is the point.

    On its own, "nothing was queued" is also what an empty follow-on list produces, so neither half
    can tell the switch from its absence without the other.
    """
    await jobs.probe(
        await context_for("probe", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
        settles_into=(jobs.FINGERPRINT_FOR_STASH_BOXES,),
    )

    assert await job_queue.outstanding(jobs.FINGERPRINT_FOR_STASH_BOXES) == 1


# --- one process per file per pass ---------------------------------------------------------------
#
# One launch per moment would make a video's probe 55 launches (one per hash frame, one per
# stash-box still) and its scrub strip one per tile, up to four hundred. Over a share every launch
# is an open and a seek across the wire. These pin the count, and pin that the batched form is
# the SAME BYTES as the one-moment form, which is the whole of what makes it safe for a
# fingerprint.


async def test_every_read_a_probe_makes_of_a_file_takes_its_storage_lane(
    video: Path,
    ingested_video: Ingested,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lane is what keeps a network share from collapsing under a dozen seeking readers, and
    a read that goes round it is a reader the cap cannot see. Every read probing makes of the
    file (the gate, ffprobe, the hash frames, the stash-box stills, the interleave, the ends)
    has to go through it, so the lane is stood in for and the paths it was asked about counted."""
    from contextlib import asynccontextmanager

    from sift.kernel import lanes

    asked: list[Path] = []

    @asynccontextmanager
    async def counting(path: Path) -> AsyncIterator[None]:
        asked.append(path)
        yield

    monkeypatch.setattr(lanes, "reading", counting)

    await probe_and_fingerprint(
        await context_for("probe", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    of_the_file = [one for one in asked if one == video]
    # The gate, the metadata, the hash frames, the stash-box stills, the interleave and the ends.
    assert len(of_the_file) >= 6, asked
    assert all(one == video for one in asked), "a read of something that is not the file"


# --- files taken in and never read ---------------------------------------------------------------


async def test_a_file_taken_in_and_never_read_gets_its_probe_at_start(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """A scan cut off before it handed out its probes leaves files with no dimensions and no
    picture, which the next scan skips as unchanged. The pass at start reads them: one scan-only
    probe each, and none for a file whose probe is already on its way."""
    asset_id = ingested_video.asset.id
    assert await content_store.unread_count() == 1

    context = await context_for(jobs.READ_UNREAD, {})
    await jobs.read_unread(context, settings=settings, hardware=hardware)
    children = await job_queue.children(context.job.id)
    assert [(child.type, dict(child.payload)) for child in children] == [
        (jobs.PROBE, {"asset_id": asset_id, "scan_only": True})
    ]

    # Asked again while that probe is still waiting: nothing is added, in either shape.
    again = await context_for(jobs.READ_UNREAD, {})
    await jobs.read_unread(again, settings=settings, hardware=hardware)
    assert await job_queue.children(again.job.id) == []


async def test_a_file_an_ordinary_probe_is_already_reading_is_left_to_it(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The two shapes a probe is queued in are one question: is anything reading this file."""
    await job_queue.enqueue(jobs.PROBE, {"asset_id": ingested_video.asset.id})
    context = await context_for(jobs.READ_UNREAD, {})
    await jobs.read_unread(context, settings=settings, hardware=hardware)
    assert await job_queue.children(context.job.id) == [], "a read is already on its way"


async def test_the_pass_walks_every_page_of_never_read_files(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """By offset, so a page of files whose probes are now waiting does not come back for ever."""
    monkeypatch.setattr(tuning, "UNREAD_BATCH", 1)
    context = await context_for(jobs.READ_UNREAD, {})
    await jobs.read_unread(context, settings=settings, hardware=hardware)
    assert len(await job_queue.children(context.job.id)) == 1


# --- a file that can never decode is refused once, not three times -------------------------------


async def test_a_scan_only_probe_asks_for_the_fingerprints_it_skipped(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The Fingerprint queue is fed by every scan-only probing.

    A scan-only pass deliberately leaves all four fingerprints behind (they are most of what
    probing costs and a button that says Scan should not spend it), so it must ask
    the Fingerprint queue to fill them in. The chain otherwise only asks for itself between
    batches, and files arriving after it finished would wait for ever.
    """
    await jobs.probe(
        await context_for("probe", {"asset_id": ingested_video.asset.id, "scan_only": True}),
        settings=settings,
        hardware=hardware,
    )

    assert await job_queue.outstanding(jobs.FINGERPRINT_FOR_STASH_BOXES) == 1


async def test_a_scan_only_probe_of_a_file_with_its_fingerprints_asks_for_no_pass(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The pass is asked for the fingerprints a read left behind, and a file that has them left
    none. A scan reads a file again whenever its size or time moves, and asked anyway each such
    read would put a run of the pass on Activity for a library with nothing waiting."""
    asset_id = ingested_video.asset.id
    await probe_and_fingerprint(
        await context_for("probe", {"asset_id": asset_id}), settings=settings, hardware=hardware
    )

    await jobs.probe(
        await context_for("probe", {"asset_id": asset_id, "scan_only": True}),
        settings=settings,
        hardware=hardware,
    )

    assert await job_queue.outstanding(jobs.FINGERPRINT_FOR_STASH_BOXES) == 0


async def test_a_scan_only_probe_does_not_ask_when_fingerprints_are_switched_off(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The known negative for the one above. Without it, "one was queued" is also what a pass that
    never reads the switch produces, and the switch is the folder's answer as well as the app's."""

    async def never(_job_type: str, _asset_id: str | None = None) -> bool:
        return False

    await jobs.probe(
        await context_for("probe", {"asset_id": ingested_video.asset.id, "scan_only": True}),
        settings=settings,
        hardware=hardware,
        should_generate=never,
    )

    assert await job_queue.outstanding(jobs.FINGERPRINT_FOR_STASH_BOXES) == 0


async def test_the_fingerprint_pass_a_scan_only_probe_asks_for_runs_last(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """It is background catch-up, so it must not be taken before a file somebody just added.

    With every job at one priority the order is arrival alone, and whole-library follow-ons can
    hold half the pool for an hour while file reads sit unclaimed.
    """
    await jobs.probe(
        await context_for("probe", {"asset_id": ingested_video.asset.id, "scan_only": True}),
        settings=settings,
        hardware=hardware,
    )

    page = await job_queue.list(job_type=jobs.FINGERPRINT_FOR_STASH_BOXES)
    assert [row.priority for row in page.jobs] == [BACKGROUND_PRIORITY]
    assert BACKGROUND_PRIORITY > DEFAULT_PRIORITY, "background work would run FIRST"


async def test_a_file_in_two_folders_is_really_generated_for_when_either_allows(
    temp_db: Database,
    content_store: ContentStore,
    library_root: LibraryRoot,
    ingested_video: Ingested,
    video: Path,
    settings: Settings,
    tmp_path: Path,
    context_for: Context,
    job_queue: JobQueue,
    hardware: HardwareReport,
) -> None:
    """The runtime half of the either-allows rule: the work is not merely permitted, it is queued.

    `ImportPolicy._on` counts every folder a file sits in, and a folder following the library counts
    as an answer, so a file in two folders, one overridden to no and the other following a library
    yes, is generated for. That rule is proved in the importing slice against `allows`, which
    answers a question; this proves the answer reaches the queue, where a fault would look exactly
    like nothing happening.

    So: a real policy, over a real two-root arrangement, wired in as `should_generate` exactly as
    `sift/wiring/imports.py` wires it, and the assertion is on the children probing queued. The mutation that
    matters (reading only the folders that stored an answer) makes the single no the whole
    answer and takes the hover preview away. (Not the thumbnail: no switch refuses that.)
    """

    class Hub:
        """The settings hub, as much of it as a gate reads."""

        async def get_app(self, key: str) -> object:
            return True

        async def get_user(self, user_id: str, key: str) -> object:
            return True

    second = tmp_path / "second-library"
    second.mkdir(exist_ok=True)
    root_id = new_id()
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (root_id, "second", str(second), 1_700_000_000),
    )
    # The same bytes in the second folder: one asset, two locations, which is the case.
    landed = second / video.name
    landed.write_bytes(video.read_bytes())
    checked = verify_ingress(landed, origin=Origin.SCAN, settings=settings)
    again = await content_store.ingest(checked, root_id=root_id, rel_path=video.name)
    assert again.asset.id == ingested_video.asset.id, "the same bytes became two assets"

    roots = RootPreferences(temp_db)
    await roots.set(library_root.id, {"performance.generate_previews": False})
    policy = ImportPolicy(
        settings=Hub(),
        content=content_store,
        roots=roots,
        gates={"preview": ("performance.generate_previews",)},
        overridable=("performance.generate_previews",),
    )

    context = await context_for("probe", {"asset_id": ingested_video.asset.id})
    await jobs.probe(
        context,
        settings=settings,
        hardware=hardware,
        should_generate=policy.allows,
    )

    children = {child.type for child in await job_queue.children(context.job.id)}
    assert "preview" in children, (
        "one folder said no, the other followed a library that said yes, and the preview was "
        "never queued, which is the refusal the rule says in writing it will not make"
    )


# --- keeping what a file says about itself ------------------------------------------------------


async def test_a_library_from_before_the_answer_was_kept_is_caught_up(
    video: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    hardware: HardwareReport,
    context_for: Context,
) -> None:
    """The catch-up, on a file read before there was anywhere to put the tool's whole answer.

    A rescan will not do it: a scan skips any file whose path, size and mtime are unchanged, which
    is every file that was already there. What it keeps is what a later question has to work from,
    so a library that misses this has nothing to answer that question with but another pass.
    """
    checked = verify_ingress(video, origin=Origin.SCAN, settings=settings)
    ingested = await content_store.ingest(checked, root_id=library_root.id, rel_path=video.name)
    # Read, from before the answer was kept, which is exactly the state an upgrade leaves.
    await content_store.record_probe(ingested.asset.id, width=320, height=240)
    assert await content_store.assets_lacking_probe_rows(10) == [ingested.asset.id]

    await jobs.keep_probes(
        await context_for("keep_probes", {}), settings=settings, hardware=hardware
    )

    # It does not come back on the next run, which is what stops the pass looping for ever.
    assert await content_store.assets_lacking_probe_rows(10) == []
    asset = await content_store.get(ingested.asset.id)
    assert asset is not None
    assert asset.width == 320, "the catch-up blanked a field it was never asked to write"


async def test_the_catch_up_keeps_what_the_tool_said_and_strips_where_it_was_taken(
    video: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    hardware: HardwareReport,
    context_for: Context,
    temp_db: Database,
) -> None:
    """End to end, against a real file and the real tool: the answer is readable back, it names
    the streams it found, and there is nothing in it that says where anybody was standing."""
    checked = verify_ingress(video, origin=Origin.SCAN, settings=settings)
    ingested = await content_store.ingest(checked, root_id=library_root.id, rel_path=video.name)
    await content_store.record_probe(ingested.asset.id, width=320, height=240)

    await jobs.keep_probes(
        await context_for("keep_probes", {}), settings=settings, hardware=hardware
    )

    async with temp_db.write() as connection:
        (row,) = await connection.execute_fetchall(
            "SELECT tool, body, probe_version FROM asset_probes WHERE asset_id = ?",
            (ingested.asset.id,),
        )
    kept = ffmpeg.read_kept_probe(bytes(row["body"]))
    assert kept["streams"], "the kept answer describes no streams"
    assert row["probe_version"] == PROBE_VERSION
    assert "ffprobe" in str(row["tool"]).lower()
    assert "location" not in ffmpeg.read_kept_probe(bytes(row["body"])).__str__().lower()


# --- the order a file's products are handed out in, and the urgency they carry --------------------


async def test_a_probes_children_carry_the_probes_own_urgency(
    ingested_video: Ingested,
    job_queue: JobQueue,
    capabilities: SystemCapabilities,
    handlers: None,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """A pressed Scan stays waited-on all the way down to the picture somebody is waiting to see.

    The urgency must survive the first fan-out: a press walking at the waited-on priority that
    handed every file it found to work at the ordinary one would put the press at the front of the
    queue and everything it asked for at the back.
    """
    job_id = await job_queue.enqueue(
        "probe", {"asset_id": ingested_video.asset.id}, priority=WAITED_ON_PRIORITY
    )
    worker_id = new_id()
    while (job := await job_queue.claim(worker_id)) is not None and job.id != job_id:
        pass
    assert job is not None
    context = JobContext(job=job, worker_id=worker_id, queue=job_queue, capabilities=capabilities)

    await jobs.probe(context, settings=settings, hardware=hardware)

    children = await job_queue.children(job_id)
    assert children
    assert {child.priority for child in children} == {WAITED_ON_PRIORITY}


async def test_a_follow_on_carries_what_the_composition_root_adds_to_its_payload(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """The music fingerprint follows every probing job and must only claim there, which it learns
    from its payload. The file's own id is probing's to give and cannot be overridden by it."""
    from sift.kernel.jobs import register_handler

    async def nothing(_context: JobContext) -> None:
        return None

    register_handler("claims_only", nothing, name="Claiming")
    context = await context_for("probe", {"asset_id": ingested_video.asset.id})

    await jobs.probe(
        context,
        settings=settings,
        hardware=hardware,
        follow_on=("claims_only",),
        follow_on_payloads={"claims_only": {"claim_only": True, "asset_id": "not-this-one"}},
    )

    children = {child.type: child for child in await job_queue.children(context.job.id)}
    assert children["claims_only"].payload == {
        "claim_only": True,
        "asset_id": ingested_video.asset.id,
    }
    assert children["thumbnail"].payload == {"asset_id": ingested_video.asset.id}


async def test_the_repaired_copy_is_handed_out_after_the_pictures_not_before_them(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The declared order, on the file where source order would be wrong.

    A remux is a whole second copy of the file and nothing on screen waits for it, yet it is
    enqueued a few lines above the derivative loop, so by source order it would claim before the
    thumbnail the grid is waiting on. Declared, it comes after every picture.
    """
    monkeypatch.setattr(mp4, "worst_gap", lambda _path: tuning.MAX_INTERLEAVE_GAP_BYTES + 1)
    context = await context_for("probe", {"asset_id": ingested_video.asset.id})

    await jobs.probe(context, settings=settings, hardware=hardware)

    handed = [child.type for child in await job_queue.children(context.job.id)]
    assert "remux" in handed, "the repaired copy was not asked for at all"
    assert handed.index("thumbnail") < handed.index("remux")
    assert handed.index("preview") < handed.index("remux")
    assert handed.index("sprite") < handed.index("remux")
    # And after everything, not only the pictures: it trails, so another feature's pass (which
    # declares nothing and sorts after every declared type) is still handed out first.
    assert handed[-1] == "remux"
    from sift.kernel.jobs import claim_rank
    from sift.kernel.jobs.worker_pool import TRAILING_RANK

    assert claim_rank(jobs.REMUX) == TRAILING_RANK


# --- sampling across the picture, not across the file ---------------------------------------------
#
# A file's length is the container's answer, and the container answers for the sound as well. On a
# file whose sound outlasts its picture, moments spread across the whole of it fall past the last
# frame: the hover clip comes out short and the fingerprint holds its last frame for the rest.


async def test_a_probe_writes_how_long_the_picture_runs(
    ingested_video: Ingested,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
    context_for: Context,
) -> None:
    """Every file read from now on has it, so the catch-up is only ever for the ones read before."""
    await jobs.probe(
        await context_for(jobs.PROBE, {"asset_id": ingested_video.asset.id, "scan_only": True}),
        settings=settings,
        hardware=hardware,
    )

    asset = await content_store.get(ingested_video.asset.id)
    assert asset is not None
    assert asset.video_duration_ms is not None and asset.video_duration_ms > 0


# --- the catch-up passes at their edges ---------------------------------------------------------


def _switch_off(job_queue: JobQueue, job_type: str) -> None:
    """Turn one kind of whole-library work off at the queue's own board."""
    from sift.kernel.jobs.switchboard import Switch

    async def off() -> bool:
        return False

    job_queue.switchboard.declare(Switch(key="test.off", refusal="switched off", on=off), job_type)


async def test_a_probe_whose_follow_on_passes_are_switched_off_asks_for_none_and_finishes(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
    content_store: ContentStore,
) -> None:
    """Switched off at the queue's board, for the whole library: the fingerprints a scan-only read
    skipped and the passes it settles into are both skipped, and the read itself still lands."""
    _switch_off(job_queue, jobs.FINGERPRINT_FOR_STASH_BOXES)

    await jobs.probe(
        await context_for("probe", {"asset_id": ingested_video.asset.id, "scan_only": True}),
        settings=settings,
        hardware=hardware,
        settles_into=(jobs.FINGERPRINT_FOR_STASH_BOXES,),
    )

    assert await job_queue.outstanding(jobs.FINGERPRINT_FOR_STASH_BOXES) == 0
    stored = await content_store.get(ingested_video.asset.id)
    assert stored is not None and stored.probed_at is not None


async def test_a_library_with_nothing_to_keep_asks_for_nothing(
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    job_queue: JobQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await context_for("keep_probes", {})
    started = watch_enqueues(job_queue, monkeypatch)

    await jobs.keep_probes(context, settings=settings, hardware=hardware)

    assert started == []


async def test_a_file_on_a_drive_that_is_away_is_not_written_down_and_asks_for_no_next_page(
    video: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    hardware: HardwareReport,
    context_for: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Left alone so it is read once the drive is back, and a full page that kept nothing has
    learnt only that a drive is away, so asking for the same page again would loop for ever."""
    from sift.slices.media_jobs.tests.conftest import take_in

    ingested = await take_in(content_store, library_root, video, settings)
    await content_store.record_probe(ingested.asset.id, width=320, height=240)
    video.unlink()
    monkeypatch.setattr(tuning, "STAMP_BATCH", 1)
    context = await context_for("keep_probes", {})
    asked: list[str] = []

    async def noting(job_type: str, payload: object = None, **how: object) -> str:
        asked.append(job_type)
        return "asked"

    context.queue.enqueue_when_settled = noting  # type: ignore[method-assign]

    await jobs.keep_probes(context, settings=settings, hardware=hardware)

    assert await content_store.assets_lacking_probe_rows(10) == [ingested.asset.id]
    assert asked == []


async def test_a_file_the_tool_refuses_is_kept_as_an_empty_answer_and_the_next_page_is_asked_for(
    video: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    hardware: HardwareReport,
    context_for: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Looked, and there was nothing to keep": left out, the file would come back at the head of
    this order on every run. A full page that kept something asks for the page after it."""
    from sift.slices.media_jobs.tests.conftest import take_in

    ingested = await take_in(content_store, library_root, video, settings)
    await content_store.record_probe(ingested.asset.id, width=320, height=240)

    async def refuses(*args: object, **kwargs: object) -> dict[str, object]:
        raise ffmpeg.FFmpegError("ffprobe failed: moov atom not found")

    monkeypatch.setattr(ffmpeg, "run_json", refuses)
    monkeypatch.setattr(tuning, "STAMP_BATCH", 1)
    context = await context_for("keep_probes", {})
    asked: list[str] = []

    async def noting(job_type: str, payload: object = None, **how: object) -> str:
        asked.append(job_type)
        return "asked"

    context.queue.enqueue_when_settled = noting  # type: ignore[method-assign]

    await jobs.keep_probes(context, settings=settings, hardware=hardware)

    assert await content_store.assets_lacking_probe_rows(10) == []
    assert asked == [jobs.KEEP_PROBES]
