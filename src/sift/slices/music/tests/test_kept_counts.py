# SPDX-License-Identifier: AGPL-3.0-or-later
"""A sound fingerprint kept, or one taken by an older scheme, moves the kept count of what the
music pass has left exactly as a walk of the library counts it."""

from __future__ import annotations

import pytest

# For its side effect: registering the tables it counts from.
import sift.slices.music.schema  # noqa: F401
from sift.kernel import chromaprint
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Lack
from sift.kernel.content.backlog import Term, Totals
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.music.matching import KEY_SCHEME
from sift.slices.music.store import LACKS_AUDIO_FINGERPRINT
from sift.testing.fixtures import LibraryRoot

pytestmark = pytest.mark.unit


class _Walks:
    async def totals(self, terms: list[Term]) -> list[Totals] | None:
        return None


async def test_each_write_of_a_fingerprint_moves_the_kept_count_as_the_walk(
    temp_db: Database, content_store: ContentStore, settings: Settings, library_root: LibraryRoot
) -> None:
    walker = ContentStore(temp_db, settings)
    walker._kept_counts = _Walks()  # type: ignore[assignment]
    lack = Lack(LACKS_AUDIO_FINGERPRINT, (KEY_SCHEME, chromaprint.ALGORITHM), product="music")
    asset_id = new_id()
    await temp_db.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, acodec, added_at,"
        " probed_at) VALUES (?, ?, 1, 'video', 'aac', 1, 1)",
        (asset_id, f"digest-{asset_id}"),
    )
    await temp_db.execute(
        "INSERT INTO asset_locations"
        " (id, asset_id, root_id, rel_path, filename, status, first_seen_at, last_seen_at)"
        " VALUES (?, ?, ?, ?, ?, 'present', 1, 1)",
        (new_id(), asset_id, library_root.id, f"{asset_id}.mp4", f"{asset_id}.mp4"),
    )

    async def lacking() -> int:
        await content_store.count_lacking([lack])
        await content_store._kept.settled()
        kept = (await content_store.count_lacking([lack])).files
        assert kept == (await walker.count_lacking([lack])).files
        return kept

    assert await lacking() == 1
    await temp_db.execute(
        "INSERT INTO audio_fingerprints (asset_id, algorithm, tool, duration_ms, fingerprint,"
        " computed_at, indexed_scheme) VALUES (?, ?, 'fpcalc', 1000, x'00', 1, ?)",
        (asset_id, chromaprint.ALGORITHM, KEY_SCHEME),
    )
    assert await lacking() == 0
    await temp_db.execute("UPDATE audio_fingerprints SET indexed_scheme = NULL")
    assert await lacking() == 1
    await temp_db.execute("DELETE FROM audio_fingerprints")
    assert await lacking() == 1
