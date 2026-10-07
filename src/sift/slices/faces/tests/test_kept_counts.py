# SPDX-License-Identifier: AGPL-3.0-or-later
"""A look for faces, first or resumed, or any write of a scan row, moves the kept count of what the faces pass has left
exactly as a walk of the library counts it."""

from __future__ import annotations

from dataclasses import replace

import pytest

from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested
from sift.kernel.content.backlog import Term, Totals
from sift.kernel.db import Database
from sift.slices.faces import schema
from sift.slices.faces.models import ScanStatus
from sift.slices.faces.store import PassRecord, Store

pytestmark = pytest.mark.integration


class _Walks:
    async def totals(self, terms: list[Term]) -> list[Totals] | None:
        return None


_LOOK = PassRecord(
    status=ScanStatus.NONE_IDENTIFIED,
    depth="fast",
    coverage=1.0,
    frames_sampled=2,
    detector="test-detector",
    recognizer="test-recognizer",
    settings_digest="abcd1234",
)


async def test_a_look_for_faces_moves_the_kept_count_as_the_walk(
    store: Store,
    clip: Ingested,
    other_clip: Ingested,
    temp_db: Database,
    content_store: ContentStore,
    settings: Settings,
) -> None:
    await temp_db.execute("UPDATE assets SET probed_at = 1")
    walker = ContentStore(temp_db, settings)
    walker._kept_counts = _Walks()  # type: ignore[assignment]
    never = store.never_scanned()
    settled = replace(store.lack("abcd1234"), product=never.product)

    async def lacking() -> tuple[int, ...]:
        await content_store.count_lacking([never, settled])
        await content_store._kept.settled()
        kept = (await content_store.count_lacking([never, settled])).each
        assert kept == (await walker.count_lacking([never, settled])).each
        return kept

    assert await lacking() == (2, 2)
    await store.replace_pass(clip.asset.id, [], [], _LOOK)
    assert await lacking() == (1, 1)
    await store.extend_pass(other_clip.asset.id, [], [], [], replace(_LOOK, coverage=0.5))
    assert await lacking() == (0, 1)
    await store.extend_pass(other_clip.asset.id, [], [], [], _LOOK)
    assert await lacking() == (0, 0)
    # Any write marks it, raw or batched as forgetting every face is.
    await temp_db.execute(
        "UPDATE face_scans SET quality_version = 0 WHERE asset_id = ?", (clip.asset.id,)
    )
    assert await lacking() == (0, 1)
    await temp_db.execute("DELETE FROM face_scans WHERE rowid IN (SELECT rowid FROM face_scans)")
    assert await lacking() == (2, 2)


async def test_a_library_at_faces_version_41_gets_the_marks(temp_db: Database) -> None:
    await temp_db.initialize_schema()
    async with temp_db.write() as connection:
        await connection.execute("DROP TRIGGER backlog_face_scans_moved")
        await schema.initialize(connection, on_disk=41)
    names = await temp_db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'face_scans'"
        " AND name LIKE 'backlog_%'"
    )
    assert len(names) == 3
