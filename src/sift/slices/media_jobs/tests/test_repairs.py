# SPDX-License-Identifier: AGPL-3.0-or-later
"""The repaired copy of a file whose audio sits too far from its video."""

from __future__ import annotations

from pathlib import Path

import pytest

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
    DerivativeKind,
    Ingested,
)
from sift.kernel.db import Database
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import (
    JobQueue,
)
from sift.slices.media_jobs import jobs, tuning
from sift.slices.media_jobs.tests.support import Context
from sift.testing.fixtures import LibraryRoot

pytestmark = [pytest.mark.integration]


# --- remux ------------------------------------------------------------------------------------


async def _measured(temp_db: Database, asset_id: str, gap: int) -> None:
    """A file whose interleave was measured at `gap`, as the read writes it."""
    await temp_db.execute("UPDATE assets SET interleave_gap = ? WHERE id = ?", (gap, asset_id))


async def test_a_repaired_copy_lands_in_the_cache_and_the_library_gains_nothing(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    library_root: LibraryRoot,
    video: Path,
    temp_db: Database,
) -> None:
    """The repair is a copy in the cache. The user's file is not rewritten, moved or touched."""
    before = video.read_bytes()
    listing = sorted(library_root.path.rglob("*"))
    await _measured(temp_db, ingested_video.asset.id, tuning.MAX_INTERLEAVE_GAP_BYTES + 1)

    await jobs.remux(
        await context_for("remux", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    assert sorted(library_root.path.rglob("*")) == listing == [video]
    assert video.read_bytes() == before

    derivatives = await content_store.derivatives(ingested_video.asset.id)
    assert [d.kind for d in derivatives] == [DerivativeKind.REMUX]
    repaired = settings.cache_dir / derivatives[0].rel_cache_path
    assert repaired.is_file()
    assert repaired.stat().st_size == derivatives[0].size_bytes
    assert settings.cache_dir in repaired.parents


async def test_a_file_that_no_longer_needs_repair_is_not_copied(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
    temp_db: Database,
) -> None:
    """The measurement can change between probing and this job reaching the front of the queue:
    somebody may have replaced the file. Building a whole extra copy nothing will read is the waste
    this avoids."""
    await _measured(temp_db, ingested_video.asset.id, tuning.MAX_INTERLEAVE_GAP_BYTES - 1)

    await jobs.remux(
        await context_for("remux", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )

    assert await content_store.derivatives(ingested_video.asset.id) == []


async def test_an_unmeasured_file_is_not_copied(
    ingested_video: Ingested,
    content_store: ContentStore,
    context_for: Context,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """Unmeasured is not the same as bad. A file nobody has looked at is left alone."""
    await jobs.remux(
        await context_for("remux", {"asset_id": ingested_video.asset.id}),
        settings=settings,
        hardware=hardware,
    )
    assert await content_store.derivatives(ingested_video.asset.id) == []


async def test_probe_starts_a_repair_for_a_badly_interleaved_file(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    content_store: ContentStore,
    settings: Settings,
    hardware: HardwareReport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The link between measuring a file and repairing it.

    Nothing else covers this: if probing stopped enqueuing the repair, the measurement would still
    be written and the repair job would still work when called by hand, and every other test would
    stay green.

    The measurement is forced rather than fabricated on disk: ffmpeg re-interleaves cleanly
    whatever it is fed, at every setting, so a genuinely bad file cannot be generated here.
    """
    monkeypatch.setattr(mp4, "worst_gap", lambda _path: tuning.MAX_INTERLEAVE_GAP_BYTES + 1)
    context = await context_for("probe", {"asset_id": ingested_video.asset.id})

    await jobs.probe(context, settings=settings, hardware=hardware)

    children = await job_queue.children(context.job.id)
    assert jobs.REMUX in {child.type for child in children}
    assert await content_store.get(ingested_video.asset.id) is not None
    stored = await content_store.get(ingested_video.asset.id)
    assert stored is not None
    assert stored.interleave_gap == tuning.MAX_INTERLEAVE_GAP_BYTES + 1


async def test_probe_starts_no_repair_for_a_well_made_file(
    ingested_video: Ingested,
    context_for: Context,
    job_queue: JobQueue,
    settings: Settings,
    hardware: HardwareReport,
) -> None:
    """Almost every file. A repair is a whole extra copy on disk and is not built speculatively."""
    context = await context_for("probe", {"asset_id": ingested_video.asset.id})

    await jobs.probe(context, settings=settings, hardware=hardware)

    children = await job_queue.children(context.job.id)
    assert jobs.REMUX not in {child.type for child in children}
