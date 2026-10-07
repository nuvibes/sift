# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dating a reading: what produced the letters, and what decided what they meant.

A reading carries the model's revision and the matcher's number, so a reading from a model nobody
runs any more, or decided by a matcher since improved, is told apart from a current one.
"""

from __future__ import annotations

import pytest

from sift.kernel.content import backlog
from sift.kernel.db import Connection, Database
from sift.slices.watermarks import schema as watermark_schema
from sift.slices.watermarks import signatures, weights
from sift.slices.watermarks.store import Read, Store

pytestmark = pytest.mark.regression

_EPOCH = 1_700_000_000_000
_ASSET = "01HX0000000000000000000800"
_UNSCANNED = "01HX0000000000000000000801"

#: The revision a library was read at before the current one. A name rather than a number, which is
#: the whole of why the column is TEXT: it is the model's own name for itself.
_THEN = "pp-ocrv3-mobile-en"


async def _parents(connection: Connection) -> None:
    """The tables the watermark tables point at. Foreign keys are on, so they exist first: a reading
    names its Site by id, and a write to it is refused while the table it points at is missing.
    And the tables a write's marks go into."""
    for statement in backlog.TABLES:
        await connection.execute(statement)
    await connection.execute(
        "CREATE TABLE assets (id TEXT PRIMARY KEY, identity TEXT NOT NULL UNIQUE, "
        "media_type TEXT NOT NULL, added_at INTEGER NOT NULL)"
    )
    await connection.execute("CREATE TABLE sites (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
    for asset, identity in ((_ASSET, "one"), (_UNSCANNED, "two")):
        await connection.execute(
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', ?)",
            (asset, identity, _EPOCH),
        )


async def test_a_reading_written_now_carries_the_model_and_the_matcher(temp_db: Database) -> None:
    """The writer's half. The revision is read back out of the scan row the same pass wrote a
    moment earlier, in the same transaction, so the two cannot say different things."""
    store = Store(temp_db)
    async with temp_db.write() as connection:
        await _parents(connection)
        await watermark_schema.initialize(connection, on_disk=0)

        await store.remember_on(
            connection, asset_id=_ASSET, revision=weights.REVISION, identity="one", found=1
        )
        await store.record_on(
            connection,
            asset_id=_ASSET,
            read=Read(
                text="onlyfans.com/wrenhalloway",
                kind=signatures.SITE,
                site="OnlyFans",
                username="wrenhalloway",
                confidence=0.9,
                frame_ms=1000,
            ),
        )

        row = await (
            await connection.execute(
                "SELECT revision, matcher_version FROM watermark_reads WHERE asset_id = ?",
                (_ASSET,),
            )
        ).fetchone()
        assert row is not None
        assert str(row["revision"]) == weights.REVISION
        assert int(row["matcher_version"]) == signatures.MATCHER_VERSION


async def test_the_marks_found_count_leaves_out_a_reading_nothing_stands_behind(
    temp_db: Database,
) -> None:
    """The read that decides a reading is current. A reading produced by a model that is no longer
    loaded, or decided by a matcher that has since been improved, is a record of something that
    happened rather than a fact about the file now, and must not be counted beside "files read",
    which is conditioned on the revision."""
    store = Store(temp_db)
    async with temp_db.write() as connection:
        await _parents(connection)
        await watermark_schema.initialize(connection, on_disk=0)
        await connection.execute(
            "INSERT INTO watermark_reads (asset_id, text, kind, site, username, confidence, "
            "frame_ms, revision, matcher_version, read_at) "
            "VALUES (?, 'onlyfans.com', 'site', 'OnlyFans', NULL, 0.9, 1000, ?, ?, ?)",
            (_ASSET, _THEN, signatures.MATCHER_VERSION, _EPOCH),
        )
        await connection.execute(
            "INSERT INTO watermark_reads (asset_id, text, kind, site, username, confidence, "
            "frame_ms, revision, matcher_version, read_at) "
            "VALUES (?, 'fansly.com', 'site', 'Fansly', NULL, 0.9, 1000, ?, ?, ?)",
            (_UNSCANNED, weights.REVISION, signatures.MATCHER_VERSION - 1, _EPOCH),
        )

    assert await store.found_count() == 0

    async with temp_db.write() as connection:
        await connection.execute(
            "UPDATE watermark_reads SET revision = ?, matcher_version = ? WHERE asset_id = ?",
            (weights.REVISION, signatures.MATCHER_VERSION, _ASSET),
        )

    assert await store.found_count() == 1


async def test_forgetting_says_how_many_went_including_the_ones_nothing_stood_behind(
    temp_db: Database,
) -> None:
    """Everything goes, so the number a person is shown has to be everything that went, which is
    a different question from the one `found_count` answers."""
    store = Store(temp_db)
    async with temp_db.write() as connection:
        await _parents(connection)
        await watermark_schema.initialize(connection, on_disk=0)
        await connection.execute(
            "INSERT INTO watermark_reads (asset_id, text, kind, site, username, confidence, "
            "frame_ms, revision, matcher_version, read_at) "
            "VALUES (?, 'onlyfans.com', 'site', 'OnlyFans', NULL, 0.9, 1000, ?, ?, ?)",
            (_ASSET, _THEN, signatures.MATCHER_VERSION, _EPOCH),
        )

    assert await store.found_count() == 0
    assert await store.forget_everything() == 1
