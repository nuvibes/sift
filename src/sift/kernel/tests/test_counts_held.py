# SPDX-License-Identifier: AGPL-3.0-or-later
"""A count of the library is held until something that could move it is announced, and no longer
than `HELD_AT_MOST_SECONDS`: a screen opened on a library at rest does not read every file again."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from sift.kernel import changes
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, ChangeBus
from sift.kernel.content import ContentStore, identity_counts
from sift.kernel.db import Database
from sift.kernel.ids import new_id

pytestmark = pytest.mark.unit


@pytest.fixture
def bus() -> Iterator[ChangeBus]:
    listening = ChangeBus()
    changes.listens(listening)
    yield listening
    changes.listens(None)


async def _unread(database: Database) -> None:
    await database.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
        " VALUES (?, ?, 1, 'image', 1)",
        (asset_id := new_id(), f"digest-{asset_id}"),
    )


@pytest.mark.usefixtures("bus")
async def test_a_count_is_held_until_the_library_moves(
    temp_db: Database, content_store: ContentStore
) -> None:
    await _unread(temp_db)
    assert await content_store.unread_count() == 1
    await _unread(temp_db)
    assert await content_store.unread_count() == 1, "nothing was announced"

    changes.announce_now(EVERY_ADMIN, About.ARRIVALS)
    assert await content_store.unread_count() == 2


@pytest.mark.usefixtures("bus")
async def test_any_job_moving_lets_the_count_be_taken_again(
    temp_db: Database, content_store: ContentStore
) -> None:
    await content_store.unread_count()
    await _unread(temp_db)
    changes.announce_now(EVERY_ADMIN, About.JOBS)
    assert await content_store.unread_count() == 1


@pytest.mark.usefixtures("bus")
async def test_a_count_is_never_held_past_its_limit(
    temp_db: Database, content_store: ContentStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(identity_counts, "HELD_AT_MOST_SECONDS", 0.0)
    await content_store.unread_count()
    await _unread(temp_db)
    assert await content_store.unread_count() == 1


async def test_nothing_is_held_where_nothing_announces(
    temp_db: Database, content_store: ContentStore
) -> None:
    await content_store.unread_count()
    await _unread(temp_db)
    assert await content_store.unread_count() == 1


async def test_a_library_at_content_version_29_gets_the_indexes_a_start_reads(
    temp_db: Database,
) -> None:
    from sift.kernel.content import schema

    listed = "SELECT name FROM pragma_index_list('assets')"
    wanted = {"ix_assets_unread", "ix_assets_classified", "ix_assets_video_unhashed"}
    async with temp_db.write() as connection:
        await schema.initialize_library(connection, on_disk=0)
        await schema.initialize_content(connection, on_disk=0)
        for name in sorted(wanted):
            await connection.execute(f"DROP INDEX {name}")  # nosemgrep: sift-no-string-built-sql
        await schema.initialize_content(connection, on_disk=29)
        names = {str(row[0]) for row in await connection.execute_fetchall(listed)}
    assert wanted <= names


async def test_a_starts_questions_and_the_awaiting_count_seek_rather_than_walk(
    temp_db: Database, content_store: ContentStore
) -> None:
    from sift.kernel.content import duplicates, identity_probes

    for statement, params in (
        (identity_counts._ANY_UNREAD, ("probe",)),
        (identity_counts._COUNT_UNREAD, ("probe",)),
        (identity_probes._ANY_UNCLASSIFIED, (2,)),
        (duplicates._AWAITING_FINGERPRINT, (1, "fingerprints")),
    ):
        plan = await temp_db.fetch_all(
            "EXPLAIN QUERY PLAN " + statement,  # nosemgrep: sift-no-string-built-sql
            params,
        )
        walks = [row["detail"] for row in plan if str(row["detail"]) in ("SCAN a", "SCAN assets")]
        assert not walks, statement
