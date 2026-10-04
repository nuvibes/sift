# SPDX-License-Identifier: AGPL-3.0-or-later
"""The passes that correct a file's kind and its identity, against real files."""

from __future__ import annotations

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
from sift.kernel.config import Settings
from sift.kernel.content import (
    ContentStore,
    Ingested,
    VerdictProduct,
)
from sift.kernel.db import Database
from sift.kernel.hardware import HardwareReport
from sift.kernel.ids import new_id
from sift.kernel.ingress import CLASSIFIER_VERSION, Origin, verify_ingress
from sift.kernel.jobs import (
    JobQueue,
)
from sift.slices.media_jobs import (
    corrections,
    jobs,
    tuning,
)
from sift.slices.media_jobs.tests.conftest import webp_tools
from sift.slices.media_jobs.tests.support import Context, watch_enqueues
from sift.testing.fixtures import LibraryRoot
from sift.testing.logs import uncached_log

pytestmark = [pytest.mark.integration]


# --- probe ------------------------------------------------------------------------------------


async def test_an_animated_webp_is_reclassified_from_its_own_bytes(
    animated_webp: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    temp_db: Database,
    tmp_path: Path,
) -> None:
    """Probed first, so the readable copy exists: the pass reads the file, never the copy, whose
    MP4 header would retype an animated WebP as an MP4 video."""
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
        "UPDATE assets SET classified_version = 0 WHERE id = ?", (ingested.asset.id,)
    )

    await jobs.reclassify(
        await context_for(jobs.RECLASSIFY, {}), settings=settings, hardware=hardware
    )

    asset = await content_store.get(ingested.asset.id)
    assert asset is not None
    assert (asset.media_type, asset.mime) == ("gif", "image/webp")


async def test_the_re_identifying_pass_brings_old_rows_forward(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """A row identified by the whole-file digest gets its sampled identity and keeps the digest
    it had."""
    asset_id = ingested_video.asset.id
    await content_store._db.execute(
        "UPDATE assets SET identity = 'the-old-whole-file-digest', identity_version = 0 WHERE id = ?",
        (asset_id,),
    )
    assert await content_store.legacy_identity_count() == 1

    context = await context_for(jobs.REIDENTIFY, {})
    await jobs.reidentify(context, settings=settings, hardware=hardware)

    asset = await content_store.get(asset_id)
    assert asset is not None
    assert asset.identity_version == 1
    assert asset.identity == ingested_video.asset.identity
    assert asset.whole_digest == "the-old-whole-file-digest"
    assert await content_store.legacy_identity_count() == 0


async def test_a_full_page_asks_for_the_next_one_at_once(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not after the settling delay: a pass asking for its own next page has nothing to wait for."""
    monkeypatch.setattr(tuning, "REIDENTIFY_BATCH", 1)
    await content_store._db.execute(
        "UPDATE assets SET identity = 'old', identity_version = 0 WHERE id = ?",
        (ingested_video.asset.id,),
    )
    context = await context_for(jobs.REIDENTIFY, {})
    await jobs.reidentify(context, settings=settings, hardware=hardware)

    waiting = [job for job in (await job_queue.list(limit=50)).jobs if job.type == jobs.REIDENTIFY]
    queued = [job for job in waiting if job.id != context.job.id]
    assert len(queued) == 1 and queued[0].run_after is None, "the next page waits for nothing"


async def test_the_re_identifying_pass_leaves_a_file_it_cannot_read_for_next_time(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    asset_id = ingested_video.asset.id
    await content_store._db.execute(
        "UPDATE assets SET identity = 'old', identity_version = 0 WHERE id = ?", (asset_id,)
    )
    await content_store.mark_missing(ingested_video.location.id)

    context = await context_for(jobs.REIDENTIFY, {})
    with capture_logs() as written:
        await jobs.reidentify(context, settings=settings, hardware=hardware)

    # Named in the log: a row left behind is the one to go and find, and the start line's count
    # of one says nothing about which it is.
    assert [
        line["asset_id"] for line in written if line["event"] == "identity.reidentify_no_copy"
    ] == [asset_id]
    asset = await content_store.get(asset_id)
    assert asset is not None and asset.identity_version == 0
    # Still counted: the copy may come back. But written down as such, and transient, so the
    # next scan that sees the file clears it, and a row that stays this way is not the row that
    # keeps every import reading whole files, because only a standing verdict is left out.
    assert await content_store.legacy_identity_count() == 1
    verdict = await content_store.verdict_of(asset_id, VerdictProduct.IDENTITY)
    assert verdict is not None and verdict.code == "no_copy" and verdict.transient is True


async def test_a_file_the_bytes_refuse_is_a_standing_verdict_and_the_pass_moves_on(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
    video: Path,
) -> None:
    """One row the pass could never sample would keep `legacy_identities_remain` true for ever,
    so every file taken in would be digested whole. A refusal by the bytes is a standing verdict:
    the flag goes false, and a page that recorded only verdicts still asks for the next page."""
    monkeypatch.setattr(tuning, "REIDENTIFY_BATCH", 1)
    asset_id = ingested_video.asset.id
    await content_store._db.execute(
        "UPDATE assets SET identity = 'old', identity_version = 0 WHERE id = ?", (asset_id,)
    )
    content_store._legacy_remaining = None
    assert await content_store.legacy_identities_remain()
    video.write_bytes(b"\x00" * 4096)

    context = await context_for(jobs.REIDENTIFY, {})
    await jobs.reidentify(context, settings=settings, hardware=hardware)

    verdict = await content_store.verdict_of(asset_id, VerdictProduct.IDENTITY)
    assert verdict is not None and verdict.transient is False
    assert not await content_store.legacy_identities_remain()
    assert await content_store.legacy_identity_count() == 0
    waiting = [job for job in (await job_queue.list(limit=50)).jobs if job.type == jobs.REIDENTIFY]
    assert [job for job in waiting if job.id != context.job.id], "the next page is still asked for"


# --- reading again what a file is, after the classifier changed -----------------------------------
#
# A change to what the ingress classifier answers reaches new files and never the ones already in
# the library: a scan skips any file whose path, size and mtime are unchanged. The row says which
# generation typed it, and the reclassify pass reads the rows below the one in use. The case here
# is the HEIF container: an AVIF matches the HEIF brand list, and a classifier that recorded it as
# `image/heic` would make an ANIMATED one a photograph: no length, no GIF chip and no hover
# preview, on a file that decodes and displays perfectly.


@pytest.fixture
def animated_avif(library_root: LibraryRoot) -> Path:
    """A real one, written by ffmpeg, with the two video streams a real one carries.

    Stream 0 is the still cover and stream 1 is the GIF itself, and neither is marked, which is the
    shape `_the_moving_picture` exists for and the reason this fixture is a real file rather than a
    header. A synthetic one would prove the brand arithmetic and nothing about probing.
    """
    from sift.slices.media_jobs.tests.conftest import draw

    return draw(library_root.path / "motion.avif", "testsrc2=size=64x64:rate=10", 2)


async def _filed_as_a_still(database: Database, asset_id: str) -> None:
    """Put the row back the way the older gate left it: a HEIC photograph with no length, typed by
    a generation of the classifier below the one in use."""
    await database.execute(
        "UPDATE assets SET media_type = 'image', mime = 'image/heic', container = 'heic',"
        " duration_ms = NULL, fps = NULL, classified_version = 0 WHERE id = ?",
        (asset_id,),
    )


async def test_an_animated_avif_taken_for_a_photograph_is_reclassified_once_and_read_again(
    animated_avif: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    hardware: HardwareReport,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole of the pass, on an animated HEIF filed as a photograph.

    The type, the MIME and the container are the classifier's and are written here, with the
    generation that decided them. What a moving file is owed beyond that (its length and frame
    rate, the hover preview and the scrub strip a still never had) is probing's, and probing is
    what is asked for, because the kind changed. And the row is at the generation in use
    afterwards, so it is never read again: the pass reclassifies it ONCE.
    """
    checked = verify_ingress(animated_avif, origin=Origin.SCAN, settings=settings)
    ingested = await content_store.ingest(
        checked, root_id=library_root.id, rel_path=animated_avif.name
    )
    await _filed_as_a_still(temp_db, ingested.asset.id)
    assert await content_store.unclassified(10) == [ingested.asset.id]

    context = await context_for(jobs.RECLASSIFY, {})
    started = watch_enqueues(job_queue, monkeypatch)
    await jobs.reclassify(context, settings=settings, hardware=hardware)

    asset = await content_store.get(ingested.asset.id)
    assert asset is not None
    assert (asset.media_type, asset.mime) == ("gif", "image/avif")
    row = await temp_db.fetch_one("SELECT container FROM assets WHERE id = ?", (ingested.asset.id,))
    assert row is not None
    assert row["container"] == "avif-sequence", "the container kept the HEIC it was filed under"
    assert asset.classified_version == CLASSIFIER_VERSION
    assert started == [jobs.PROBE], "a new kind is a file read again, and nothing else"

    # And it does not come back, which is what stops the pass looping for ever.
    assert await content_store.unclassified(10) == []


async def test_a_still_avif_is_retyped_without_being_read_again(
    picture: Path,
    library_root: LibraryRoot,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A photograph really stored as AVIF was mis-typed the same way and is corrected the same way,
    and it is a picture afterwards, so nothing more is asked for. Its kind did not change, and a
    probe of a still to learn nothing new is a read of a file for nothing."""
    from sift.slices.media_jobs.tests.conftest import draw

    still = draw(library_root.path / "one.avif", "testsrc2=size=64x64:rate=1", None)
    checked = verify_ingress(still, origin=Origin.SCAN, settings=settings)
    ingested = await content_store.ingest(checked, root_id=library_root.id, rel_path=still.name)
    await _filed_as_a_still(temp_db, ingested.asset.id)

    context = await context_for(jobs.RECLASSIFY, {})
    started = watch_enqueues(job_queue, monkeypatch)
    await jobs.reclassify(context, settings=settings, hardware=hardware)

    asset = await content_store.get(ingested.asset.id)
    assert asset is not None
    assert (asset.media_type, asset.mime) == ("image", "image/avif")
    assert started == [], "a still whose kind did not change was read again"


async def test_a_webp_filed_as_a_video_is_read_again_and_follows_its_header(
    animated_webp: Path,
    video: Path,
    library_root: LibraryRoot,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A WebP an older gate filed as a VIDEO opens in no player and has no GIF mark on its tile.

    Read again, the kind follows the header: the animated one is a GIF, the still one a picture,
    and each move is a line in the log naming both kinds. A real video read beside them stays a
    video and is not moved.
    """
    import shutil

    still = library_root.path / "still.webp"
    shutil.copyfile(
        Path(__file__).parents[3] / "kernel" / "tests" / "fixtures" / "ingress" / "accepted.webp",
        still,
    )
    rows: dict[str, str] = {}
    for path in (animated_webp, still, video):
        checked = verify_ingress(path, origin=Origin.SCAN, settings=settings)
        taken = await content_store.ingest(checked, root_id=library_root.id, rel_path=path.name)
        rows[path.name] = taken.asset.id
    # Every row as the older gate left it: a video, typed a generation below the one in use.
    for name, asset_id in rows.items():
        mime = "video/mp4" if name.endswith(".mp4") else "image/webp"
        await temp_db.execute(
            "UPDATE assets SET media_type = 'video', mime = ?, classified_version = ? WHERE id = ?",
            (mime, CLASSIFIER_VERSION - 1, asset_id),
        )

    context = await context_for(jobs.RECLASSIFY, {})
    started = watch_enqueues(job_queue, monkeypatch)
    uncached_log(monkeypatch, corrections)
    with capture_logs() as written:
        await jobs.reclassify(context, settings=settings, hardware=hardware)

    kinds = {}
    for name, asset_id in rows.items():
        asset = await content_store.get(asset_id)
        assert asset is not None
        assert asset.classified_version == CLASSIFIER_VERSION
        kinds[name] = asset.media_type
    assert kinds == {"moving.webp": "gif", "still.webp": "image", "holiday.mp4": "video"}
    moved = {
        (line["asset_id"], line["was"], line["now"])
        for line in written
        if line["event"] == "classify.moved"
    }
    assert moved == {
        (rows["moving.webp"], "video", "gif"),
        (rows["still.webp"], "video", "image"),
    }
    assert started == [jobs.PROBE, jobs.PROBE], "each moved file is read again, nothing else"


async def test_a_row_at_the_generation_in_use_is_not_read(
    animated_avif: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    hardware: HardwareReport,
    job_queue: JobQueue,
    context_for: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary case on every install, and on every one after the first pass. A file taken in
    now is typed by the classifier in use and stamped so by the insert, and a pass that opened
    it to decide there was nothing to do would cost something on every boot for ever."""
    checked = verify_ingress(animated_avif, origin=Origin.SCAN, settings=settings)
    ingested = await content_store.ingest(
        checked, root_id=library_root.id, rel_path=animated_avif.name
    )
    assert ingested.asset.classified_version == CLASSIFIER_VERSION

    opened: list[Path] = []

    def reading(path: Path) -> tuple[bytes, bytes, int]:
        opened.append(path)
        raise AssertionError("a row at the generation in use was read")

    monkeypatch.setattr(corrections, "read_ends", reading)
    context = await context_for(jobs.RECLASSIFY, {})
    started = watch_enqueues(job_queue, monkeypatch)
    await jobs.reclassify(context, settings=settings, hardware=hardware)

    assert opened == []
    assert started == []


async def test_a_file_on_a_drive_that_is_not_plugged_in_is_left_for_next_time(
    animated_avif: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    hardware: HardwareReport,
    temp_db: Database,
    context_for: Context,
) -> None:
    """Left below the line rather than written down, so it is read once the drive is back.
    Recording an answer nobody measured would file a real GIF as whatever the row already
    said, for ever, and this pass is the only thing that would ever have looked again."""
    checked = verify_ingress(animated_avif, origin=Origin.SCAN, settings=settings)
    ingested = await content_store.ingest(
        checked, root_id=library_root.id, rel_path=animated_avif.name
    )
    await _filed_as_a_still(temp_db, ingested.asset.id)
    animated_avif.unlink()

    await jobs.reclassify(
        await context_for(jobs.RECLASSIFY, {}), settings=settings, hardware=hardware
    )

    asset = await content_store.get(ingested.asset.id)
    assert asset is not None
    assert asset.mime == "image/heic", "an answer was recorded for a file nobody could read"
    assert await content_store.unclassified(10) == [ingested.asset.id]


async def test_a_file_the_gate_would_refuse_now_is_stamped_and_left_as_it_is(
    animated_avif: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    hardware: HardwareReport,
    temp_db: Database,
    context_for: Context,
) -> None:
    """This pass corrects a CLASSIFICATION. Quarantining a file somebody already has in their
    library is a different decision, and one nothing here is entitled to make, but it has been
    read, so it is stamped, or it would be read on every start for ever."""
    checked = verify_ingress(animated_avif, origin=Origin.SCAN, settings=settings)
    ingested = await content_store.ingest(
        checked, root_id=library_root.id, rel_path=animated_avif.name
    )
    await _filed_as_a_still(temp_db, ingested.asset.id)
    animated_avif.write_bytes(b"not media at all, and it never was" * 40)

    await jobs.reclassify(
        await context_for(jobs.RECLASSIFY, {}), settings=settings, hardware=hardware
    )

    asset = await content_store.get(ingested.asset.id)
    assert asset is not None
    assert asset.mime == "image/heic"
    assert await content_store.unclassified(10) == []


async def test_a_library_bigger_than_one_batch_asks_for_the_next_one(
    animated_avif: Path,
    content_store: ContentStore,
    library_root: LibraryRoot,
    settings: Settings,
    hardware: HardwareReport,
    temp_db: Database,
    job_queue: JobQueue,
    context_for: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full batch that got somewhere asks for the next. `read > 0` is what makes it terminate: a
    batch that read nothing has learnt only that a drive is away, and asking for the same page
    again would loop for ever."""
    monkeypatch.setattr(tuning, "STAMP_BATCH", 1)
    checked = verify_ingress(animated_avif, origin=Origin.SCAN, settings=settings)
    ingested = await content_store.ingest(
        checked, root_id=library_root.id, rel_path=animated_avif.name
    )
    await _filed_as_a_still(temp_db, ingested.asset.id)

    asked: list[str] = []
    original = job_queue.enqueue_when_settled

    async def noting(job_type: str, payload: object = None, **kwargs: object) -> str:
        asked.append(job_type)
        return await original(job_type, payload, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(job_queue, "enqueue_when_settled", noting)

    await jobs.reclassify(
        await context_for(jobs.RECLASSIFY, {}), settings=settings, hardware=hardware
    )

    assert asked == [jobs.RECLASSIFY]


async def test_a_file_that_will_not_open_is_stepped_over_and_the_rest_are_still_read(
    animated_avif: Path,
    library_root: LibraryRoot,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
    temp_db: Database,
    context_for: Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A share that drops mid-pass, or a file the operating system will not hand over.

    The read is made to fail rather than a file made unreadable, and the reason is the site:
    Windows ignores `chmod` on a file, and a directory in the file's place is refused one step
    earlier by `resolve` (it asks `is_file`). What is asserted is that the pass CARRIES ON: the file
    that could not be read keeps the row it had and stays below the line, and the one beside it is
    still corrected.
    """
    from sift.slices.media_jobs.tests.conftest import draw

    # A different SIZE, and it is not decoration: two files drawn from the same pattern are the
    # same bytes, so the library dedupes them into one asset with two locations, and the pass
    # would then see one file rather than two, with nothing to carry on to.
    second = draw(library_root.path / "other.avif", "testsrc2=size=48x48:rate=10", 2)
    ids = []
    for path in (animated_avif, second):
        checked = verify_ingress(path, origin=Origin.SCAN, settings=settings)
        held = await content_store.ingest(checked, root_id=library_root.id, rel_path=path.name)
        await _filed_as_a_still(temp_db, held.asset.id)
        ids.append(held.asset.id)

    from sift.kernel.ingress import read_ends

    def refusing(path: Path) -> tuple[bytes, bytes, int]:
        if path.name == animated_avif.name:
            raise OSError(5, "the share went away")
        return read_ends(path)

    monkeypatch.setattr(corrections, "read_ends", refusing)

    await jobs.reclassify(
        await context_for(jobs.RECLASSIFY, {}), settings=settings, hardware=hardware
    )

    refused = await content_store.get(ids[0])
    corrected = await content_store.get(ids[1])
    assert refused is not None and refused.mime == "image/heic"
    assert corrected is not None and corrected.mime == "image/avif"
    assert await content_store.unclassified(10) == [ids[0]]


# --- the pieces the Build calls, one file at a time -----------------------------------------------


async def test_a_library_with_no_old_rows_does_no_re_identifying(
    ingested_video: Ingested,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """A row already carrying the sampled identity is not a row to bring forward."""
    assert ingested_video.asset.identity_version == 1

    with capture_logs() as logs:
        await jobs.reidentify(
            await context_for(jobs.REIDENTIFY, {}), settings=settings, hardware=hardware
        )

    assert not any(entry["event"] == "identity.reidentify_start" for entry in logs)


async def test_a_row_that_left_the_library_mid_page_is_stepped_over(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The page is read once and the rows one at a time after it, so a file deleted in between is
    on the page and not in the library. Nothing is written about it (there is no row to write
    on), and the rest of the page is still brought forward."""
    asset_id = ingested_video.asset.id
    await content_store._db.execute(
        "UPDATE assets SET identity = 'old', identity_version = 0 WHERE id = ?", (asset_id,)
    )
    gone = new_id()
    real_page = content_store.legacy_identity_page

    async def with_a_ghost(limit: int) -> list[str]:
        return [gone, *await real_page(limit)]

    monkeypatch.setattr(content_store, "legacy_identity_page", with_a_ghost)

    await jobs.reidentify(
        await context_for(jobs.REIDENTIFY, {}), settings=settings, hardware=hardware
    )

    asset = await content_store.get(asset_id)
    assert asset is not None and asset.identity_version == 1
    assert await content_store.verdict_of(gone, VerdictProduct.IDENTITY) is None


async def test_a_file_that_grows_under_the_read_is_left_for_next_time(
    ingested_video: Ingested,
    context_for: Context,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file still being written is read again on a later pass, not identified by half of
    itself. The writer lands between the gate's look and the read, which is the moment the
    sampled read refuses; the verdict says so and says it is transient."""
    asset_id = ingested_video.asset.id
    await content_store._db.execute(
        "UPDATE assets SET identity = 'old', identity_version = 0 WHERE id = ?", (asset_id,)
    )

    def then_grows(path: Path, **kwargs: Any) -> Any:
        checked = verify_ingress(path, **kwargs)
        with path.open("ab") as handle:
            handle.write(b"\x00")
        return checked

    monkeypatch.setattr(corrections, "verify_ingress", then_grows)

    await jobs.reidentify(
        await context_for(jobs.REIDENTIFY, {}), settings=settings, hardware=hardware
    )

    asset = await content_store.get(asset_id)
    assert asset is not None and asset.identity_version == 0
    verdict = await content_store.verdict_of(asset_id, VerdictProduct.IDENTITY)
    assert verdict is not None
    assert verdict.code == "unreadable" and verdict.transient is True
