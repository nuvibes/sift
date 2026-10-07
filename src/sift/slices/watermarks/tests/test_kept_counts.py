# SPDX-License-Identifier: AGPL-3.0-or-later
"""A watermark read, a refusal or a scan at another revision moves the kept count of what the
catch-up pass has left exactly as a walk of the library counts it."""

from __future__ import annotations

import pytest

# For its side effect: registering the tables it counts from.
import sift.slices.watermarks.schema  # noqa: F401
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Lack
from sift.kernel.content.backlog import Term, Totals
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.watermarks import signatures
from sift.slices.watermarks.store import _LACKS_READING
from sift.testing.fixtures import LibraryRoot

pytestmark = pytest.mark.unit


class _Walks:
    async def totals(self, terms: list[Term]) -> list[Totals] | None:
        return None


async def _read_file(database: Database, root: LibraryRoot) -> str:
    asset_id = new_id()
    await database.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, added_at, probed_at)"
        " VALUES (?, ?, 1, 'video', 1, 1)",
        (asset_id, f"digest-{asset_id}"),
    )
    await database.execute(
        "INSERT INTO asset_locations"
        " (id, asset_id, root_id, rel_path, filename, status, first_seen_at, last_seen_at)"
        " VALUES (?, ?, ?, ?, ?, 'present', 1, 1)",
        (new_id(), asset_id, root.id, f"{asset_id}.mp4", f"{asset_id}.mp4"),
    )
    return asset_id


async def test_each_write_of_a_reading_moves_the_kept_count_as_the_walk(
    temp_db: Database, content_store: ContentStore, settings: Settings, library_root: LibraryRoot
) -> None:
    walker = ContentStore(temp_db, settings)
    walker._kept_counts = _Walks()  # type: ignore[assignment]
    lack = Lack(_LACKS_READING, ("now", signatures.MATCHER_VERSION), product="watermarks")
    asset_id = await _read_file(temp_db, library_root)

    async def lacking() -> int:
        await content_store.count_lacking([lack])
        await content_store._kept.settled()
        kept = (await content_store.count_lacking([lack])).files
        assert kept == (await walker.count_lacking([lack])).files
        return kept

    assert await lacking() == 1
    await temp_db.execute(
        "INSERT INTO watermark_scans (asset_id, revision, identity, found, scanned_at)"
        " VALUES (?, 'now', ?, 0, 1)",
        (asset_id, f"digest-{asset_id}"),
    )
    assert await lacking() == 0
    await temp_db.execute("UPDATE watermark_scans SET revision = 'before'")
    assert await lacking() == 1
    await temp_db.execute(
        "INSERT INTO watermark_refusals (asset_id, created_at) VALUES (?, 1)", (asset_id,)
    )
    assert await lacking() == 0
    await temp_db.execute("DELETE FROM watermark_refusals")
    assert await lacking() == 1
