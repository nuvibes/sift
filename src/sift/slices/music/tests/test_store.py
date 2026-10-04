# SPDX-License-Identifier: AGPL-3.0-or-later
"""The three tables, against a real database carrying Sift's own schema.

A real one rather than hand-built tables: what these statements mean depends on the keys and the
conflict clauses that actually ship, and the claim in particular is a single transaction whose
whole point is that it cannot half-happen.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from sift.kernel import chromaprint
from sift.kernel.access import waiting
from sift.kernel.db import Database
from sift.slices.music import schema
from sift.slices.music.store import Kept, MusicStore

pytestmark = [pytest.mark.integration]

_EPOCH = 1_700_000_000

#: A file that has been read: `probed_at` is set, as probing sets it, so the rule's first half
#: holds and whether it waits is decided by its sound track and its fingerprint row.
_ASSET = """
INSERT INTO assets
  (id, identity, media_type, size_bytes, original_filename, added_at, acodec, probed_at)
VALUES (?, ?, 'video', 10, ?, ?, ?, 1)
"""


#: A copy of a file, marked present unless the test says otherwise. A file waits only while it has
#: a copy that is there to read. See `kernel.content.presence`.
_COPY = """
INSERT INTO asset_locations
  (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)
VALUES (?, ?, 'root-1', NULL, ?, ?, 1, 1)
"""


async def _library(tmp_path: Path) -> Database:
    database = Database(tmp_path / "music.sqlite3")
    await database.connect()
    await database.initialize_schema()
    async with database.write() as connection:
        await connection.execute(
            "INSERT INTO library_roots (id, name, abs_path, created_at)"
            " VALUES ('root-1', 'music', 'C:\\music', 1)"
        )
        await connection.execute(_ASSET, ("asset-1", "identity-1", "one.mp4", _EPOCH, "aac"))
        await connection.execute(_ASSET, ("asset-2", "identity-2", "two.mp4", _EPOCH, None))
        await connection.execute(_COPY, ("copy-1", "asset-1", "one.mp4", "one.mp4"))
        await connection.execute(_COPY, ("copy-2", "asset-2", "two.mp4", "two.mp4"))
    return database


def _count(store: MusicStore) -> str:
    """The count the kernel makes of the term this store declares, written out here.

    A fixed string built from one constant: the term is a module constant of the slice and nothing
    in it comes from a request. Written this way so the count and the term cannot drift: a test
    that restated the condition would go on passing after the real one changed.
    """
    # nosemgrep: sift-no-string-built-sql
    return f"SELECT COUNT(*) AS files FROM assets a WHERE {store.lack().condition}"  # noqa: S608


def _kept(values: bytes = b"\x01\x00\x00\x00") -> Kept:
    return Kept(
        algorithm=chromaprint.ALGORITHM,
        tool="ffmpeg version 7.1.5",
        duration_ms=8000 if values else 0,
        offset_ms=0,
        fingerprint=values,
        computed_at=_EPOCH,
    )


@pytest.mark.asyncio
async def test_a_fingerprint_is_written_and_read_back(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    store = MusicStore(database)
    try:
        assert not await store.has("asset-1")
        await store.keep("asset-1", _kept())
        assert await store.has("asset-1")
        # Kept is not yet paired; what a page still lacks is `lacking_among`'s question, tested
        # on its own below.
        await store.settle("asset-1")
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_an_empty_row_is_an_answer_and_counts_as_having_one(tmp_path: Path) -> None:
    """A file with no audio is written down, not left blank, which is what stops it coming back
    at the head of the pass for the rest of the library's life."""
    database = await _library(tmp_path)
    store = MusicStore(database)
    try:
        await store.keep("asset-2", _kept(b""))
        assert await store.has("asset-2")
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_waiting_fingerprint_is_claimed_by_identity(tmp_path: Path) -> None:
    """The whole point of the pending table: the read happened while the bytes were local, and the
    file gets its answer without anything being opened again."""
    database = await _library(tmp_path)
    store = MusicStore(database)
    try:
        await store.keep_pending("identity-1", _kept(b"\x02\x00\x00\x00"))
        assert await store.claim("asset-1", "identity-1")
        assert await store.has("asset-1")
        row = await database.fetch_one(
            "SELECT fingerprint, duration_ms FROM audio_fingerprints WHERE asset_id = ?",
            ("asset-1",),
        )
        assert row is not None
        assert bytes(row["fingerprint"]) == b"\x02\x00\x00\x00"
        assert int(row["duration_ms"]) == 8000
        # And it is gone from the waiting table, so a second file cannot be given the same answer.
        assert await store.claim("asset-2", "identity-1") is False
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_claiming_nothing_says_so(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    store = MusicStore(database)
    try:
        assert await store.claim("asset-1", "identity-nothing") is False
        assert not await store.has("asset-1")
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_file_that_already_has_one_keeps_it_when_a_claim_arrives(tmp_path: Path) -> None:
    """Two answers for one file is the thing the conflict clause exists to stop, and the one that
    was already written is the one that stands: it was written about this asset, not about bytes
    that turned out to be it."""
    database = await _library(tmp_path)
    store = MusicStore(database)
    try:
        await store.keep("asset-1", _kept(b"\x09\x00\x00\x00"))
        await store.keep_pending("identity-1", _kept(b"\x02\x00\x00\x00"))
        assert await store.claim("asset-1", "identity-1") is False
        row = await database.fetch_one(
            "SELECT fingerprint FROM audio_fingerprints WHERE asset_id = ?", ("asset-1",)
        )
        assert row is not None
        assert bytes(row["fingerprint"]) == b"\x09\x00\x00\x00"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_waiting_fingerprint_under_an_old_algorithm_is_not_claimed(tmp_path: Path) -> None:
    """A reading taken at staging under an algorithm since replaced would be a stale answer copied
    onto a file that is then never read again: the claim leaves it, drops it, and the file is
    read afresh."""
    database = await _library(tmp_path)
    store = MusicStore(database)
    try:
        await store.keep_pending(
            "identity-1", replace(_kept(b"\x02\x00\x00\x00"), algorithm=chromaprint.ALGORITHM - 1)
        )
        assert await store.claim("asset-1", "identity-1") is False
        assert not await store.has("asset-1")
        assert (
            await database.fetch_one(
                "SELECT 1 FROM audio_fingerprints_pending WHERE identity = ?", ("identity-1",)
            )
            is None
        ), "a stale waiting row is dropped with the claim"
        # And the sweep drops one nothing has tried to claim, however young it is.
        await store.keep_pending(
            "identity-2", replace(_kept(), algorithm=chromaprint.ALGORITHM - 1)
        )
        assert await store.forget_pending_before(_EPOCH - 1) == 1
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_fingerprint_nothing_ever_claimed_is_swept_by_age(tmp_path: Path) -> None:
    database = await _library(tmp_path)
    store = MusicStore(database)
    try:
        await store.keep_pending("identity-old", _kept())
        assert await store.forget_pending_before(_EPOCH - 1) == 0
        assert await store.forget_pending_before(_EPOCH + 1) == 1
        assert await store.claim("asset-1", "identity-old") is False
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_fingerprint_goes_when_its_file_does(tmp_path: Path) -> None:
    """A fingerprint is a fact about the bytes rather than part of anybody's history of them."""
    database = await _library(tmp_path)
    store = MusicStore(database)
    try:
        await store.keep("asset-1", _kept())
        await database.execute("DELETE FROM assets WHERE id = ?", ("asset-1",))
        assert not await store.has("asset-1")
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_the_count_term_names_files_with_audio_and_no_fingerprint(tmp_path: Path) -> None:
    """The count and the page question have to describe one set, and this is the half that spans
    the library: `acodec IS NOT NULL` is what keeps a photograph out of it. A fingerprint kept and
    not yet paired is still owed its pairing (v3); paired, it is done."""
    database = await _library(tmp_path)
    store = MusicStore(database)
    params = store.lack().params
    try:
        (row,) = await database.fetch_all(_count(store), params)
        assert int(row["files"]) == 1
        await store.keep("asset-1", _kept())
        (row,) = await database.fetch_all(_count(store), params)
        assert int(row["files"]) == 1
        await store.settle("asset-1")
        (row,) = await database.fetch_all(_count(store), params)
        assert int(row["files"]) == 0
    finally:
        await database.close()


async def _waiting(database: Database) -> list[str]:
    rows = await database.fetch_all("SELECT asset_id FROM music_waiting ORDER BY asset_id")
    return [str(row["asset_id"]) for row in rows]


async def _agrees(database: Database) -> None:
    async with database.write() as connection:
        found = await waiting.differences(connection, schema.WAITING)
    assert found == [], f"the waiting files disagree with the rule: {found}"


@pytest.mark.asyncio
async def test_the_waiting_files_follow_every_writer(tmp_path: Path) -> None:
    """The table the card's count is kept over, moved by the database rather than by any caller:
    a file arriving read, a fingerprint kept, one claimed from staging, one taken away, a re-read
    that finds the sound track gone and one that finds it back, and the file itself going. After
    every step it says exactly what the rule says."""
    database = await _library(tmp_path)
    store = MusicStore(database)
    try:
        # Arrived read, with a sound track: waiting. The silent one is not.
        assert await _waiting(database) == ["asset-1"]
        await _agrees(database)
        await store.keep("asset-1", _kept())
        assert await _waiting(database) == []
        await database.execute("DELETE FROM audio_fingerprints WHERE asset_id = ?", ("asset-1",))
        assert await _waiting(database) == ["asset-1"]
        # A claim from staging is a fingerprint arriving by another statement.
        await store.keep_pending("identity-1", _kept())
        assert await store.claim("asset-1", "identity-1")
        assert await _waiting(database) == []
        await database.execute("DELETE FROM audio_fingerprints WHERE asset_id = ?", ("asset-1",))
        # A re-read that finds no sound track takes the file out, and one that finds it puts it back.
        await database.execute("UPDATE assets SET acodec = NULL WHERE id = ?", ("asset-1",))
        assert await _waiting(database) == []
        await database.execute("UPDATE assets SET acodec = 'aac' WHERE id = ?", ("asset-2",))
        assert await _waiting(database) == ["asset-2"]
        await _agrees(database)
        # A file nobody has read yet is not waiting, whatever its row says.
        await database.execute("UPDATE assets SET probed_at = NULL WHERE id = ?", ("asset-2",))
        assert await _waiting(database) == []
        await database.execute(
            "UPDATE assets SET probed_at = 2, acodec = 'aac' WHERE id = ?", ("asset-1",)
        )
        assert await _waiting(database) == ["asset-1"]
        # A file whose only copy is marked missing is work nothing can do: it leaves, and comes
        # back with the copy. Forgetting the copy takes it out too; a new one brings it back.
        await database.execute("UPDATE asset_locations SET status = 'missing' WHERE id = 'copy-1'")
        assert await _waiting(database) == []
        await database.execute("UPDATE asset_locations SET last_seen_at = 9 WHERE id = 'copy-1'")
        assert await _waiting(database) == [], "a re-stamp of a missing copy changes nothing"
        await database.execute("UPDATE asset_locations SET status = 'present' WHERE id = 'copy-1'")
        assert await _waiting(database) == ["asset-1"]
        await database.execute("DELETE FROM asset_locations WHERE id = 'copy-1'")
        assert await _waiting(database) == []
        await database.execute(_COPY, ("copy-3", "asset-1", "again.mp4", "again.mp4"))
        assert await _waiting(database) == ["asset-1"]
        await _agrees(database)
        await database.execute("DELETE FROM assets WHERE id = ?", ("asset-1",))
        assert await _waiting(database) == []
        await _agrees(database)
        assert not await store.any_waiting()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_lost_trigger_is_put_back_and_the_rows_repaired(tmp_path: Path) -> None:
    """A rebuild of `assets` elsewhere takes the trigger on it with it. The boot check makes the
    triggers again and moves only the rows that disagree."""
    database = await _library(tmp_path)
    try:
        async with database.write() as connection:
            await connection.execute("DROP TRIGGER music_waiting_file_changed")
        await database.execute("UPDATE assets SET acodec = 'aac' WHERE id = ?", ("asset-2",))
        assert await _waiting(database) == ["asset-1"], "nothing kept it with the trigger gone"
        async with database.write() as connection:
            await waiting.keep_true(connection, schema.WAITING)
        assert await _waiting(database) == ["asset-1", "asset-2"]
        await _agrees(database)
        # And a boot with nothing wrong changes nothing.
        async with database.write() as connection:
            await waiting.keep_true(connection, schema.WAITING)
        assert await _waiting(database) == ["asset-1", "asset-2"]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_file_with_no_fingerprint_has_none_to_read(tmp_path: Path) -> None:
    """No row is no answer, which is not the same as an empty one: the file was never read."""
    database = await _library(tmp_path)
    try:
        assert await MusicStore(database).fingerprint_of("asset-1") is None
    finally:
        await database.close()
