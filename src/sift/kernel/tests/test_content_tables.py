# SPDX-License-Identifier: AGPL-3.0-or-later
"""The content tables: their schema, the exact-file hash the stash-boxes key on, what a pass could not make, and the fields written on a file."""

from __future__ import annotations

import json
import struct
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

# A fingerprint write records an event through the ledger door, and the door writes into the
# workbench's own table, so a database built without that component has nowhere to put it.
# Imported for the registration, nothing else.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel import changes
from sift.kernel.changes import ChangeBus
from sift.kernel.content import hashing, identity, schema
from sift.kernel.content.hashing import (
    hash_file,
)
from sift.kernel.content.identity import (
    ContentStore,
    DerivativeKind,
    Lack,
    Lacking,
    LocationStatus,
    VerdictProduct,
    lacks_derivative,
    lacks_fingerprint,
)
from sift.kernel.content.schema import CONTENT_COMPONENT, LIBRARY_COMPONENT
from sift.kernel.db import Connection, Database, registered_components
from sift.kernel.ids import new_id
from sift.kernel.ingress import (
    ALLOWED_MEDIA,
    Kind,
    MediaType,
)
from sift.kernel.tests.content_helpers import (
    GOLDEN_IDENTITY,
    _make_legacy,
    checked,
    corpus_survives,  # noqa: F401  (the corpus check, autouse)
    place,
)
from sift.kernel.vocabulary import FINGERPRINTS_EMPTY
from sift.testing.fixtures import LibraryRoot

# --- The schema ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "column", "enum"),
    [
        ("assets", "media_type", Kind),
        ("asset_locations", "status", LocationStatus),
        ("derivatives", "kind", DerivativeKind),
    ],
)
async def test_the_check_constraints_agree_with_the_enums(
    temp_db: Database, table: str, column: str, enum: Any
) -> None:
    """SQLite takes no placeholder in a CHECK constraint, so the values are written out twice:
    once in the DDL and once in an enum. A drift between them is a write that fails in production
    months later, so it is caught here instead."""
    await temp_db.initialize_schema()
    row = await temp_db.fetch_one(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    )
    assert row is not None

    ddl = str(row["sql"])
    start = ddl.index(f"CHECK({column} IN (")
    constraint = ddl[start : ddl.index(")", start)]

    assert {member.value for member in enum} == set(
        constraint.split("(")[-1].replace("'", "").split(",")
    )


async def test_an_initializer_at_its_current_version_does_nothing(
    temp_db: Database, library_root: LibraryRoot
) -> None:
    """An initializer is told what version is on disk and is responsible for the gap. Told there
    is none, it must do nothing at all, and once these carry `ALTER TABLE`s to get from one
    version to the next, "nothing" is the difference between a quiet boot and a destructive one."""
    async with temp_db.write() as connection:
        await schema.initialize_library(connection, on_disk=schema.LIBRARY_VERSION)
        await schema.initialize_content(connection, on_disk=schema.CONTENT_VERSION)
        await schema.initialize_user_state(connection, on_disk=schema.USER_STATE_VERSION)

    survivors = await temp_db.fetch_all("SELECT id FROM library_roots")
    assert [row["id"] for row in survivors] == [library_root.id]


# The `assets` table exactly as content schema v1 shipped it. Written out rather than derived,
# because the point of the test below is that a database created by *that* build upgrades cleanly,
# and a v1 assembled from today's constants would quietly acquire today's columns and prove
# nothing.
_ASSETS_V1 = """
CREATE TABLE assets (
  id                TEXT PRIMARY KEY,
  identity            TEXT NOT NULL UNIQUE,
  media_type        TEXT NOT NULL CHECK(media_type IN ('video','image','gif')),
  mime              TEXT,
  width             INTEGER,
  height            INTEGER,
  duration_ms       INTEGER,
  size_bytes        INTEGER,
  container         TEXT,
  vcodec            TEXT,
  acodec            TEXT,
  phash             TEXT,
  videohash         TEXT,
  original_filename TEXT,
  vault             INTEGER NOT NULL DEFAULT 0,
  added_at          INTEGER NOT NULL,
  probed_at         INTEGER
)
"""

#: The assets table as it stood before the sampled identity, at the one column that changed. The
#: rename is what a library from before the sample takes; `_ASSETS_V1` above is written with the
#: column's new name so every other step's test is about its own step and not this one.
_ASSETS_BEFORE_THE_SAMPLE = """
CREATE TABLE assets (
  id         TEXT PRIMARY KEY,
  blake3     TEXT NOT NULL UNIQUE,
  media_type TEXT NOT NULL CHECK(media_type IN ('video','image','gif')),
  added_at   INTEGER NOT NULL,
  probed_at  INTEGER
)
"""

#: The derivatives table as it stood before `remux` was a kind. The tests for the rebuild have to
#: start from this rather than from `initialize_content`, which now creates the wider table: the
#: rebuild is guarded on what the table already says, so starting from the new shape makes it
#: return immediately and every assertion afterwards passes against a no-op.
_DERIVATIVES_V3 = """
CREATE TABLE derivatives (
  id             TEXT PRIMARY KEY,
  asset_id       TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  kind           TEXT NOT NULL CHECK(kind IN ('thumb','preview','sprite','rendition')),
  rel_cache_path TEXT NOT NULL,
  params         TEXT NOT NULL DEFAULT '{}',
  size_bytes     INTEGER,
  created_at     INTEGER NOT NULL,
  UNIQUE(asset_id, kind, params)
)
"""


async def _old_content_tables(connection: Connection) -> None:
    """A content schema at v3: assets current, derivatives still four-kinded."""
    await connection.execute(_ASSETS_V1)
    await connection.execute("ALTER TABLE assets ADD COLUMN fps REAL")
    await connection.execute("ALTER TABLE assets ADD COLUMN interleave_gap INTEGER")
    await connection.execute(_DERIVATIVES_V3)
    await connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_derivatives_asset ON derivatives(asset_id)"
    )


async def test_a_fresh_library_has_neither_flag(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        await connection.execute("DROP TABLE IF EXISTS folders")
        await connection.execute("DROP TABLE IF EXISTS library_roots")
        await schema.initialize_library(connection, on_disk=0)

    columns = [
        row["name"]
        for row in await temp_db.fetch_all("SELECT name FROM pragma_table_info('library_roots')")
    ]
    assert "managed" not in columns
    assert "watched" not in columns


_USER_STATE_V1 = """
CREATE TABLE asset_user_state (
  asset_id       TEXT NOT NULL,
  user_id        TEXT NOT NULL,
  favorite       INTEGER NOT NULL DEFAULT 0,
  rating         INTEGER CHECK(rating IS NULL OR rating BETWEEN 1 AND 5),
  view_count     INTEGER NOT NULL DEFAULT 0,
  watched_ms     INTEGER NOT NULL DEFAULT 0,
  last_viewed_at INTEGER,
  updated_at     INTEGER NOT NULL,
  PRIMARY KEY (asset_id, user_id)
)
"""


async def test_a_fresh_watch_history_does_not_also_run_the_resume_migration(
    temp_db: Database,
) -> None:
    """The CREATE has already written `resume_ms`; adding it again is an error, not a no-op.

    The steps are independent `if`s so a database two versions behind gets both in one boot, which
    means each one decides for itself whether it applies. This is the other side of that.
    """
    async with temp_db.write() as connection:
        await connection.execute("DROP TABLE IF EXISTS asset_user_state")
        await schema.initialize_user_state(connection, on_disk=0)

    columns = await temp_db.fetch_all("SELECT name FROM pragma_table_info('asset_user_state')")
    assert [row["name"] for row in columns].count("resume_ms") == 1


async def test_content_is_created_after_the_library_it_points_at() -> None:
    """`asset_locations` has foreign keys into the roots and folders. SQLite will create a table
    whose parent is missing and only complain at the first insert, which is a long way from the
    mistake, so the order is declared rather than left to import order."""
    components = registered_components()
    assert LIBRARY_COMPONENT in components[CONTENT_COMPONENT].depends_on


@pytest.mark.integration
async def test_deleting_an_asset_takes_its_locations_and_derivatives_with_it(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The cascade, which only works if `foreign_keys` is on, and it is per-connection, so this
    is really a test that the pragma is applied to the writer as well as the readers."""
    target = place("accepted.mp4", library_root, "clip.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )
    await content_store.add_derivative(result.asset.id, DerivativeKind.THUMB, extension="jpg")

    await temp_db.execute("DELETE FROM assets WHERE id = ?", (result.asset.id,))

    assert await content_store.locations(result.asset.id) == []
    assert await content_store.derivatives(result.asset.id) == []


@pytest.mark.integration
async def test_a_location_cannot_point_at_a_root_that_does_not_exist(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The foreign key is real, which is what makes deleting a root delete what was in it."""
    target = place("accepted.mp4", library_root, "clip.mp4")
    proof = checked(target, settings)
    digest = await hash_file(proof)
    asset, _ = await content_store.upsert_asset(
        digest=digest, media=proof.media, size_bytes=proof.size
    )

    with pytest.raises(Exception, match="FOREIGN KEY"):
        await content_store.add_location(asset_id=asset.id, root_id=new_id(), rel_path="clip.mp4")


@pytest.mark.regression
async def test_two_thumbnails_with_no_settings_are_one_row(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """`params` is NOT NULL for a reason that is invisible until it bites: SQLite treats every
    NULL as distinct from every other, so a nullable column would leave the unique index unable
    to dedup the commonest case there is: a thumbnail, which has no settings at all."""
    target = place("accepted.mp4", library_root, "clip.mp4")
    result = await content_store.ingest(
        checked(target, settings), root_id=library_root.id, rel_path="clip.mp4"
    )

    await content_store.add_derivative(result.asset.id, DerivativeKind.THUMB, extension="jpg")
    await content_store.add_derivative(result.asset.id, DerivativeKind.THUMB, extension="jpg")

    rows = await temp_db.fetch_all(
        "SELECT params FROM derivatives WHERE asset_id = ?", (result.asset.id,)
    )
    assert len(rows) == 1
    assert rows[0]["params"] == "{}"


# --- the exact-file hash the public stash-boxes key on ----------------------------------------


def test_the_exact_file_hash_is_the_length_plus_both_ends() -> None:
    """The whole algorithm, on a file small enough to check by hand.

    Sixteen bytes: two words at the front, the same two at the back, and the length. Deliberately
    arithmetic anybody can follow, because this value is compared against numbers computed by
    software Sift did not write, and a test that only compared it to itself would prove nothing.
    """
    head = struct.pack("<QQ", 1, 2)
    tail = struct.pack("<QQ", 3, 4)
    assert hashing.oshash(head, tail, 32) == f"{1 + 2 + 3 + 4 + 32:016x}"


def test_the_exact_file_hash_wraps_rather_than_growing() -> None:
    """Sixty-four bits, and a sum past that end wraps around. Every other implementation does, so
    an implementation that widened instead would disagree with all of them on large files."""
    head = struct.pack("<Q", 0xFFFFFFFFFFFFFFFF)
    tail = struct.pack("<Q", 0)
    assert hashing.oshash(head, tail, 16) == f"{15:016x}"


def test_a_file_too_small_to_hash_says_so_rather_than_guessing() -> None:
    with pytest.raises(ValueError, match="eight bytes"):
        hashing.oshash(b"", b"", 8)


def test_a_partial_word_is_refused() -> None:
    """Both ends are summed as whole 64-bit words, so a length that is not a multiple of eight
    means the caller read the wrong amount, which would silently produce a different hash."""
    with pytest.raises(ValueError, match="8-byte words"):
        hashing.oshash(b"1234567", b"12345678", 64)


async def test_a_real_file_is_hashed_from_both_of_its_ends(tmp_path: Path) -> None:
    """Long enough to have two distinct ends, so a version that read the front twice would differ.

    The length is measured from the file rather than passed in, and that is load-bearing: the
    length is part of the hash, so a size remembered from an earlier version of the file would not
    give a slightly stale answer but a confidently wrong one, matching nothing anywhere.
    """
    path = tmp_path / "clip.bin"
    path.write_bytes(bytes(range(256)) * 1024)
    size = path.stat().st_size

    expected = hashing.oshash(
        path.read_bytes()[: hashing.OSHASH_CHUNK],
        path.read_bytes()[-hashing.OSHASH_CHUNK :],
        size,
    )
    assert await hashing.oshash_file(path) == expected


async def test_a_short_file_uses_as_much_of_itself_as_divides_by_eight(tmp_path: Path) -> None:
    """Below the chunk size the two ends overlap, which is what every implementation does. The
    length is rounded down to a whole number of words, so the trailing bytes are ignored."""
    path = tmp_path / "tiny.bin"
    path.write_bytes(b"A" * 100)
    got = await hashing.oshash_file(path)
    assert got == hashing.oshash(b"A" * 96, b"A" * 96, 100)


async def test_a_file_of_eight_bytes_has_no_exact_hash(tmp_path: Path) -> None:
    """None rather than a raise: it is an ordinary thing to meet in a library, the caller has
    plenty else to record about the file, and there is nothing to retry."""
    path = tmp_path / "nothing.bin"
    path.write_bytes(b"12345678")
    assert await hashing.oshash_file(path) is None


async def test_the_stash_box_columns_are_not_added_twice_to_a_fresh_database(
    temp_db: Database,
) -> None:
    """The CREATE already wrote them, and adding a column that exists is an error rather than a
    no-op, which would make the first boot of a new install fail.

    Asserted as "no column appears twice" rather than as a count. A count written beside the
    thing it describes drifts the moment the thing changes, and it drifts silently, which is the
    one failure this file is otherwise careful about everywhere.
    """
    async with temp_db.write() as connection:
        await schema.initialize_content(connection, on_disk=0)
    columns = [
        str(row["name"])
        for row in await temp_db.fetch_all("SELECT name FROM pragma_table_info('assets')")
    ]
    assert columns, "a fresh database has no assets table at all"
    duplicated = sorted({name for name in columns if columns.count(name) > 1})
    assert not duplicated, f"a fresh database declared these columns twice: {duplicated}"


async def test_the_two_stash_box_hashes_are_written_without_touching_anything_else(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """The backfill writes two columns and must not blank the other nine.

    `record_probe` writes every column it knows about in one statement, so calling it to set two
    fields sets the rest to their defaults, wiping the width, height, duration and codecs. This is
    the separate writer that exists so that cannot happen, and this is the test that says so.
    """
    store = content_store
    ingested = await store.ingest(
        checked(place("accepted.mp4", library_root, "clip.mp4"), settings),
        root_id=library_root.id,
        rel_path="clip.mp4",
    )
    await store.record_probe(ingested.asset.id, width=1920, height=1080, duration_ms=210000)

    await store.record_fingerprints(
        ingested.asset.id,
        phash="1122334455667788",
        videohash="8877665544332211",
        oshash="5ca1ab1e0dd5eed1",
        video_phash="deadbeef0badf00d",
    )

    after = await store.get(ingested.asset.id)
    assert after is not None
    assert after.phash == "1122334455667788"
    assert after.videohash == "8877665544332211"
    assert after.oshash == "5ca1ab1e0dd5eed1"
    assert after.video_phash == "deadbeef0badf00d"
    assert (after.width, after.height, after.duration_ms) == (1920, 1080, 210000)


async def test_a_read_that_gave_nothing_to_compare_says_so_on_its_event(
    content_store: ContentStore,
    library_root: Any,
    settings: Any,
) -> None:
    """Every fingerprint came back empty: the event carries the word the line reads, so the file's
    history says nothing was there to fingerprint. A read that gave any one of them carries none."""
    store = content_store
    empty = await store.ingest(
        checked(place("accepted.mp4", library_root, "silent.mp4"), settings),
        root_id=library_root.id,
        rel_path="silent.mp4",
    )
    full = await store.ingest(
        checked(place("accepted.jpg", library_root, "shot.jpg"), settings),
        root_id=library_root.id,
        rel_path="shot.jpg",
    )

    await store.record_fingerprints(
        empty.asset.id, phash="", videohash="", oshash="", video_phash=""
    )
    await store.record_fingerprints(
        full.asset.id, phash="1122334455667788", videohash=None, oshash=None, video_phash=None
    )

    rows = await store._db.fetch_all(
        "SELECT s.subject_id AS asset_id, d.payload AS payload FROM workbench_decisions d "
        "JOIN workbench_decision_subjects s ON s.decision_id = d.id WHERE d.verb = 'scanned'"
    )
    said = {str(row["asset_id"]): row["payload"] for row in rows}
    assert json.loads(said[empty.asset.id]) == {FINGERPRINTS_EMPTY: True}
    assert FINGERPRINTS_EMPTY not in json.loads(said[full.asset.id] or "{}")


async def test_a_scan_only_probe_does_not_blank_the_fingerprints_it_did_not_compute(
    content_store: ContentStore,
    library_root: Any,
    settings: Any,
) -> None:
    """The trap on the other side of the same statement, and the reason for a second one.

    `record_probe` writes every column it names, so a scan-only pass (which deliberately computes
    no fingerprints) would pass None for four of them and BLANK the fingerprints of a file that
    already had them. A rescan reads files it has read before, so that is not a corner case: it is
    every file in the library, on the second scan.

    The same shape bites the other way round too: a write meaning to change two fields would wipe
    the width, height, duration and codecs of every file it touched.
    """
    store = content_store
    ingested = await store.ingest(
        checked(place("accepted.mp4", library_root, "clip.mp4"), settings),
        root_id=library_root.id,
        rel_path="clip.mp4",
    )
    await store.record_probe(
        ingested.asset.id, width=1920, height=1080, duration_ms=210000, phash="aaaabbbbccccdddd"
    )

    # Read again by a pass that did not fingerprint. The width changes, the fingerprint does not.
    await store.record_probe(
        ingested.asset.id, width=1280, height=720, duration_ms=210000, keep_fingerprints=True
    )

    after = await store.get(ingested.asset.id)
    assert after is not None
    assert after.phash == "aaaabbbbccccdddd", "a scan that computed no fingerprint erased one"
    assert (after.width, after.height) == (1280, 720), "the pass wrote nothing at all"


async def test_a_caller_cannot_hand_over_a_fingerprint_and_ask_for_it_to_be_dropped(
    content_store: ContentStore,
) -> None:
    """Because that caller has computed something expensive and is about to throw it away.

    It is the one way the two paths can be got wrong from outside, and it is silent: the write
    succeeds, the file keeps whatever it had before, and the work is simply gone.
    """
    store = content_store
    video = next(media for media in ALLOWED_MEDIA if media.kind is Kind.VIDEO)
    asset, _ = await store.upsert_asset(digest="keep", media=video, size_bytes=1)

    with pytest.raises(ValueError, match="asked to keep"):
        await store.record_probe(asset.id, phash="0011223344556677", keep_fingerprints=True)


async def test_everything_read_and_missing_a_fingerprint_is_offered_to_the_backfill(
    content_store: ContentStore,
    library_root: LibraryRoot,
) -> None:
    """What the catch-up pass asks for, and the four things it must not ask for.

    Not video only: a scan-only pass leaves every file it reads without fingerprints:
    fingerprinting is about seventy percent of what a probe costs, so this has to find all four
    kinds of fingerprint, on every kind of file.

    Each kind is asked only for what it can have. A photograph has one frame and gets `phash`
    alone; asking it for a video fingerprint would be a pass that can never finish, because its own
    work could never make the query return fewer rows.

    A file NOBODY HAS READ is not offered either, and that is the other half. Its fingerprints
    cannot be worked out at the right moments because nothing knows how long it is, and a pass that
    took them would be racing the scan for the same files.
    """
    store = content_store
    video = next(media for media in ALLOWED_MEDIA if media.kind is Kind.VIDEO)
    still = next(media for media in ALLOWED_MEDIA if media.kind is Kind.IMAGE)

    # Each with a copy on disk, because the Build's count below counts only files there is a copy
    # of: work on a file every copy of which is gone is work no pass can do.
    async def read(digest: str, media: MediaType) -> str:
        asset, _ = await store.upsert_asset(digest=digest, media=media, size_bytes=1)
        await store.record_probe(asset.id, width=16, height=16, duration_ms=1000)
        await store.add_location(asset_id=asset.id, root_id=library_root.id, rel_path=digest)
        return asset.id

    waiting = await read("a", video)
    picture = await read("b", still)

    done = await read("c", video)
    await store.record_fingerprints(
        done, phash="aa", videohash="bb", oshash="0123456789abcdef", video_phash="cc"
    )

    # Tried and unreadable: written down with empty hashes rather than left NULL, which is what
    # takes it out of this list for good. Left NULL it comes back on every run for ever.
    broken = await read("d", video)
    await store.record_fingerprints(broken, phash="", videohash="", oshash="", video_phash="")

    shot = await read("e", still)
    await store.record_fingerprints(shot, phash="dd", videohash=None, oshash=None, video_phash=None)

    # With a copy there to read, so what keeps it out is that nobody has read it, and only that.
    unread, _ = await store.upsert_asset(digest="f", media=video, size_bytes=1)
    await store.add_location(asset_id=unread.id, root_id=library_root.id, rel_path="f")

    # Read, refused, and written down: the decoder will not read this file's frames, so its columns
    # are still NULL and it would sit at the head of this oldest-first order for the life of the
    # library. The standing verdict is what takes it out (see `fingerprints._no_fingerprints`).
    refused = await read("g", video)
    await store.record_verdict(
        refused,
        VerdictProduct.FINGERPRINTS,
        code="not_decodable",
        reason="this file could not be decoded",
    )
    # And the file that was merely AWAY when a pass reached it is still waiting. A transient verdict
    # is about a moment, the next scan of the file clears it, and treating it as permanent would
    # take a good file out of every comparison for ever.
    blinked = await read("h", video)
    await store.record_verdict(
        blinked,
        VerdictProduct.FINGERPRINTS,
        code="unreadable",
        reason="the share was away",
        transient=True,
    )
    # GONE: read, missing its fingerprints, and its only copy marked missing by a scan. No pass
    # can read it, and at the head of this oldest-first order a library's worth of such files would
    # have the pass read a page of them, record nothing and step over it, twenty pages at a time,
    # while the files behind them were never reached.
    gone, _ = await store.upsert_asset(digest="i", media=video, size_bytes=1)
    await store.record_probe(gone.id, width=16, height=16, duration_ms=1000)
    copy = await store.add_location(asset_id=gone.id, root_id=library_root.id, rel_path="i")
    assert await store.mark_missing(copy.id)

    offered = await store.unfingerprinted(10)

    assert offered == [waiting, picture, blinked], "the wrong files are waiting for a fingerprint"
    assert done not in offered
    assert broken not in offered, "a file recorded as unreadable comes back for ever"
    assert shot not in offered, "a photograph is being asked for a video fingerprint"
    assert unread.id not in offered, "a file nobody has read is being fingerprinted"
    assert refused not in offered, "a file the pass has given up on is offered to it again"
    assert gone.id not in offered, "a file with no copy to read is offered to the pass"
    assert await store.unfingerprinted_count() == 3
    # The page form of the same question, asked about ids the caller holds, is held to it too.
    assert await store.unfingerprinted_among(
        [waiting, picture, done, broken, shot, refused, blinked, unread.id, gone.id, "nobody"]
    ) == {waiting, picture, blinked}

    # STEPPING OVER THE HEAD. The order is oldest first, so a page of files nothing can record sits
    # in front of everything behind it; `skip` is the pass reading the next page instead. Nothing
    # is stored, so the same question asked from the beginning still offers all three.
    assert await store.unfingerprinted(10, skip=1) == [picture, blinked]
    assert await store.unfingerprinted(1, skip=2) == [blinked]
    assert await store.unfingerprinted(10, skip=3) == []
    assert await store.unfingerprinted(10) == [waiting, picture, blinked]
    with pytest.raises(ValueError):
        await store.unfingerprinted(10, skip=-1)
    # The third copy of the condition, the one the Build's sheet counts by, held to the same list.
    assert await store.count_lacking([lacks_fingerprint()]) == Lacking(each=(3,), files=3)


async def test_the_set_wise_count_agrees_with_the_page_question_and_counts_a_file_once(
    content_store: ContentStore,
    library_root: LibraryRoot,
) -> None:
    """One statement over the library answers what a walk in pages would: a count per
    term and the union over the ticked ones. Each term's count is held to the page-at-a-time
    question about the same thing, the union counts a file lacking two things once, a term that
    is not ticked is counted and left out of the union, and a file nobody has read is not offered:
    the same line the page walk draws."""
    store = content_store
    video = next(media for media in ALLOWED_MEDIA if media.kind is Kind.VIDEO)

    async def read(digest: str) -> str:
        asset, _ = await store.upsert_asset(digest=digest, media=video, size_bytes=1)
        await store.record_probe(asset.id, width=16, height=16, duration_ms=1000)
        await store.add_location(asset_id=asset.id, root_id=library_root.id, rel_path=digest)
        return asset.id

    bare, thumbed, pictured = [await read(digest) for digest in ("bare", "thumbed", "pictured")]
    await store.add_derivative(thumbed, DerivativeKind.THUMB, extension="jpg")
    await store.add_derivative(pictured, DerivativeKind.THUMB, extension="jpg")
    await store.add_derivative(pictured, DerivativeKind.PREVIEW, extension="mp4")
    await store.record_fingerprints(
        pictured, phash="aa", videohash="bb", oshash="0123456789abcdef", video_phash="cc"
    )
    unread, _ = await store.upsert_asset(digest="unread", media=video, size_bytes=1)

    pictures = lacks_derivative([DerivativeKind.THUMB, DerivativeKind.PREVIEW])
    assert pictures is not None
    everything = [bare, thumbed, pictured, unread.id]
    page = await store.lacking_derivative(DerivativeKind.THUMB, everything) | (
        await store.lacking_derivative(DerivativeKind.PREVIEW, everything)
    )
    assert page == {bare, thumbed}

    counted = await store.count_lacking([pictures, lacks_fingerprint()])
    assert counted == Lacking(each=(2, 2), files=2), "bare and thumbed lack both; one file each"

    # Only the pictures ticked: fingerprints are still counted for their row and left out of the
    # union. Only fingerprints ticked, the other way round.
    assert await store.count_lacking([pictures, lacks_fingerprint()], ticked=[True, False]) == (
        Lacking(each=(2, 2), files=2)
    )
    assert await store.count_lacking([pictures, lacks_fingerprint()], ticked=[False, False]) == (
        Lacking(each=(2, 2), files=0)
    )
    only_previews = lacks_derivative([DerivativeKind.PREVIEW])
    assert only_previews is not None
    assert await store.count_lacking(
        [only_previews, lacks_fingerprint()], ticked=[True, False]
    ) == (Lacking(each=(2, 2), files=2))
    assert await store.count_lacking(
        [Lack("a.id = ?", (thumbed,)), lacks_fingerprint()], ticked=[True, False]
    ) == Lacking(each=(1, 2), files=1)

    assert lacks_derivative([]) is None, "no kind wanted is nothing lacking, not a term"
    assert await store.count_lacking([]) == Lacking(each=(), files=0)
    with pytest.raises(ValueError, match="one tick per term"):
        await store.count_lacking([pictures], ticked=[True, False])


async def test_a_folder_that_refuses_a_piece_of_work_takes_its_files_out_of_every_count_of_it(
    content_store: ContentStore, library_root: LibraryRoot, temp_db: Database
) -> None:
    """The EITHER rule, over every count a bar is drawn from. A file only in a folder that said no
    is not wanted there and is left out of both ends of the bar and out of what is lacking; a file
    that also sits in a folder that did not refuse is wanted; and a library with no refusing folder
    pays nothing, because there is no condition at all."""
    store = content_store
    video = next(media for media in ALLOWED_MEDIA if media.kind is Kind.VIDEO)
    refusing = new_id()
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, 'no', '/no', 0)",
        (refusing,),
    )

    async def taken_in(digest: str, roots: tuple[str, ...], *, read: bool = True) -> str:
        asset, _ = await store.upsert_asset(digest=digest, media=video, size_bytes=1)
        if read:
            await store.record_probe(asset.id, width=16, height=16, duration_ms=1000)
        for root in roots:
            await store.add_location(asset_id=asset.id, root_id=root, rel_path=digest)
        return asset.id

    await taken_in("refused", (refusing,))
    await taken_in("both", (refusing, library_root.id))
    await taken_in("open", (library_root.id,))
    await taken_in("unread-refused", (refusing,), read=False)
    await taken_in("unread-open", (library_root.id,), read=False)

    assert identity.wanted_outside([]) is None
    assert identity.wanted_outside([[], []]) is None
    within = identity.wanted_outside([[refusing]])
    assert within is not None
    two_keys = identity.wanted_outside([[refusing], [refusing, library_root.id]])
    assert two_keys is not None and len(two_keys.params) == 4

    assert (await store.asset_count(), await store.asset_count(within)) == (5, 3)
    thumbs = VerdictProduct.THUMBNAILS.value
    assert await store.wanting_count(DerivativeKind.THUMB, thumbs) == 5
    assert await store.wanting_count(DerivativeKind.THUMB, thumbs, within) == 3
    assert await store.coming_count(DerivativeKind.THUMB, thumbs) == 2
    assert await store.coming_count(DerivativeKind.THUMB, thumbs, within) == 1

    lacking = lacks_derivative([DerivativeKind.THUMB])
    assert lacking is not None
    narrowed = replace(lacking, product=thumbs, within=within)
    assert await store.count_lacking([lacking]) == Lacking(each=(3,), files=3)
    assert await store.count_lacking([narrowed]) == Lacking(each=(2,), files=2)


async def test_an_arriving_files_fingerprints_write_no_line_of_their_own(
    content_store: ContentStore, temp_db: Database
) -> None:
    """Taking a file in writes no line, and its fingerprints are part of taking it in. The same
    write from the pass over the library says which pass read the file."""
    store = content_store
    arriving = await _read_video(store, "arriving")
    swept = await _read_video(store, "swept")
    for asset_id, is_arriving in ((arriving, True), (swept, False)):
        await store.record_fingerprints(
            asset_id,
            phash="aa",
            videohash="bb",
            oshash="0123456789abcdef",
            video_phash="cc",
            arriving=is_arriving,
        )

    after = await store.get(arriving)
    assert after is not None and after.phash == "aa"
    lines = await temp_db.fetch_all(
        "SELECT subject_id FROM workbench_decision_subjects WHERE kind = 'asset'"
    )
    assert [str(row["subject_id"]) for row in lines] == [swept]


async def test_a_split_count_refuses_a_tick_list_of_the_wrong_length(
    content_store: ContentStore,
) -> None:
    thumbs = lacks_derivative([DerivativeKind.THUMB])
    assert thumbs is not None
    with pytest.raises(ValueError, match="one tick per term"):
        await content_store.count_lacking_by_kind([thumbs], ticked=[True, False])


async def test_the_count_by_kind_splits_the_same_count_by_media_kind(
    content_store: ContentStore, library_root: LibraryRoot
) -> None:
    """What an estimate prices apart: the videos and the photographs lacking a thumbnail, each
    counted the way the whole count is, and summing to it. A kind with nothing read is absent."""
    store = content_store
    video = next(media for media in ALLOWED_MEDIA if media.kind is Kind.VIDEO)
    image = next(media for media in ALLOWED_MEDIA if media.kind is Kind.IMAGE)

    async def read(digest: str, media: MediaType) -> str:
        asset, _ = await store.upsert_asset(digest=digest, media=media, size_bytes=1)
        await store.record_probe(asset.id, width=16, height=16, duration_ms=1000)
        await store.add_location(asset_id=asset.id, root_id=library_root.id, rel_path=digest)
        return asset.id

    clips = [await read(f"clip{n}", video) for n in range(3)]
    shots = [await read(f"shot{n}", image) for n in range(2)]
    await store.add_derivative(clips[0], DerivativeKind.THUMB, extension="jpg")
    await store.upsert_asset(digest="unread", media=video, size_bytes=1)

    thumbs = lacks_derivative([DerivativeKind.THUMB])
    assert thumbs is not None
    split = await store.count_lacking_by_kind([thumbs])
    assert split == {"video": Lacking(each=(2,), files=2), "image": Lacking(each=(2,), files=2)}
    whole = await store.count_lacking([thumbs])
    assert sum(one.files for one in split.values()) == whole.files
    assert await store.count_lacking_by_kind([]) == {}

    assert await store.kinds_of([clips[0], shots[0], "nobody"]) == {
        clips[0]: "video",
        shots[0]: "image",
    }
    assert await store.kinds_of([]) == {}


# --- what a feature could not make for a file ----------------------------------------------------
#
# One record for every feature's "I cannot make this", rather than one answer each (nothing, a
# permanent yes, a refusal of the bytes, a row that is never terminal).


async def _read_video(store: ContentStore, digest: str) -> str:
    video = next(media for media in ALLOWED_MEDIA if media.kind is Kind.VIDEO)
    asset, _ = await store.upsert_asset(digest=digest, media=video, size_bytes=1)
    await store.record_probe(asset.id, width=16, height=16, duration_ms=1000)
    return asset.id


async def test_a_verdict_is_written_once_per_file_and_product_and_can_be_taken_back(
    content_store: ContentStore,
) -> None:
    store = content_store
    one = await _read_video(store, "one")
    other = await _read_video(store, "other")

    await store.record_verdict(one, VerdictProduct.THUMBNAILS, code="no_frame", reason="No frame.")
    await store.record_verdict(one, VerdictProduct.FACES, code="no_frame", reason="No frame.")
    await store.record_verdict(
        other,
        VerdictProduct.THUMBNAILS,
        code="share_away",
        reason="The share was away.",
        transient=True,
    )

    kept = await store.verdict_of(one, VerdictProduct.THUMBNAILS)
    assert kept is not None and kept.code == "no_frame" and kept.transient is False
    assert [v.product for v in await store.verdicts_of(one)] == ["faces", "thumbnails"]
    # A second verdict for the same pair replaces the first: a retry that failed again.
    await store.record_verdict(
        one, VerdictProduct.THUMBNAILS, code="truncated", reason="Cut short."
    )
    kept = await store.verdict_of(one, VerdictProduct.THUMBNAILS)
    assert kept is not None and kept.code == "truncated"

    # Standing verdicts only: the transient one on `other` is not a verdict a pass leaves out.
    assert await store.verdicted_among(VerdictProduct.THUMBNAILS, [one, other, "nobody"]) == {one}
    assert await store.verdict_count(VerdictProduct.THUMBNAILS) == 1
    assert await store.verdict_count(VerdictProduct.FACES) == 1

    # A scan saw the file again: what was true of a moment is gone, what was true of the bytes
    # stays.
    assert await store.forget_transient_verdicts(other) == 1
    assert await store.forget_transient_verdicts(one) == 0
    assert await store.verdicts_of(other) == []

    # Asked to try again: every verdict for that product goes, and no other product's.
    assert await store.clear_verdicts(VerdictProduct.THUMBNAILS) == 1
    assert await store.verdict_of(one, VerdictProduct.THUMBNAILS) is None
    assert await store.verdict_of(one, VerdictProduct.FACES) is not None


async def test_the_standing_verdicts_on_a_page_are_read_per_file_for_the_products_asked(
    content_store: ContentStore,
) -> None:
    """What a wall of the files a product gave up on reads to say why under each one: standing
    verdicts only, only the products asked, and a file with none is absent."""
    store = content_store
    one = await _read_video(store, "one")
    other = await _read_video(store, "other")
    clean = await _read_video(store, "clean")
    await store.record_verdict(one, VerdictProduct.THUMBNAILS, code="no_frame", reason="No frame.")
    await store.record_verdict(one, VerdictProduct.FACES, code="no_frame", reason="No frame.")
    await store.record_verdict(
        other, VerdictProduct.THUMBNAILS, code="away", reason="Away.", transient=True
    )
    await store.record_verdict(other, VerdictProduct.FACES, code="no_frame", reason="No frame.")

    found = await store.standing_verdicts_among(
        [one, other, clean], [VerdictProduct.THUMBNAILS.value, VerdictProduct.FACES.value]
    )

    assert {asset: [v.product for v in held] for asset, held in found.items()} == {
        one: ["faces", "thumbnails"],
        other: ["faces"],
    }
    assert await store.standing_verdicts_among([one], [VerdictProduct.PREVIEWS.value]) == {}
    assert await store.standing_verdicts_among([], [VerdictProduct.FACES.value]) == {}
    assert await store.standing_verdicts_among([one], []) == {}


async def test_the_naming_facts_say_what_a_template_can_name_and_skip_unknown_files(
    temp_db: Database, content_store: ContentStore
) -> None:
    """The creator is the username decided first, and its Site comes with it; a file filed under
    nobody names nobody. Asked in pieces, so a long batch is one answer."""
    store = content_store
    filed = await _read_video(store, "filed")
    loose = await _read_video(store, "loose")
    await store.set_site_code(filed, "QH-0042")
    await temp_db.execute("INSERT INTO sites (id, name) VALUES ('s1', 'Quillhouse')")
    for user_id, name, decided in (("u1", "first_name", 10), ("u2", "later_name", 20)):
        await temp_db.execute(
            "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, 's1', ?, 1)",
            (user_id, name),
        )
        await temp_db.execute(
            "INSERT INTO asset_usernames (asset_id, username_id, decided_at) VALUES (?, ?, ?)",
            (filed, user_id, decided),
        )

    facts = await store.naming_facts([filed, loose, *[new_id() for _ in range(600)]])

    assert set(facts) == {filed, loose}
    assert (facts[filed]["creator"], facts[filed]["site"], facts[filed]["site_code"]) == (
        "first_name",
        "Quillhouse",
        "QH-0042",
    )
    assert (facts[loose]["creator"], facts[loose]["site"]) == (None, None)


async def test_every_file_a_product_gave_up_on_can_be_listed_at_once(
    content_store: ContentStore,
) -> None:
    """The whole set rather than a page of it, for a sweep that already holds its settled set in
    memory: standing verdicts for this product only, so a transient one and another product's
    are not in it."""
    store = content_store
    one = await _read_video(store, "one")
    other = await _read_video(store, "other")
    third = await _read_video(store, "third")
    await store.record_verdict(one, VerdictProduct.FACES, code="no_frame", reason="No frame.")
    await store.record_verdict(third, VerdictProduct.FACES, code="no_frame", reason="No frame.")
    await store.record_verdict(
        other, VerdictProduct.FACES, code="share_away", reason="Away.", transient=True
    )
    await store.record_verdict(
        other, VerdictProduct.THUMBNAILS, code="no_frame", reason="No frame."
    )

    assert await store.verdicted_ids(VerdictProduct.FACES) == {one, third}
    assert await store.verdicted_ids(VerdictProduct.MEANING) == set()


async def test_a_file_a_feature_gave_up_on_is_not_lacking_what_it_cannot_have(
    content_store: ContentStore, temp_db: Database, library_root: LibraryRoot
) -> None:
    """The exclusion, in every place a file is offered: the Build's count and the pass that reads
    unread files. A transient verdict excludes nothing. Each file has a copy on disk, since a
    count of what is lacking counts only files there is a copy of."""
    store = content_store
    bare = await _read_video(store, "bare")
    given_up = await _read_video(store, "given-up")
    later = await _read_video(store, "later")
    for one in (bare, given_up, later):
        await temp_db.execute(
            "INSERT INTO asset_locations (id, asset_id, root_id, rel_path, filename, status,"
            " first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, 'present', 1, 1)",
            (new_id(), one, library_root.id, f"{one}.mp4", f"{one}.mp4"),
        )
    await store.record_verdict(given_up, VerdictProduct.THUMBNAILS, code="no_frame", reason="x")
    await store.record_verdict(
        later, VerdictProduct.THUMBNAILS, code="share_away", reason="y", transient=True
    )

    pictures = lacks_derivative([DerivativeKind.THUMB])
    assert pictures is not None
    filed = Lack(pictures.condition, pictures.params, product=VerdictProduct.THUMBNAILS.value)
    assert (await store.count_lacking([pictures])).each == (3,), "without a product, all three"
    assert (await store.count_lacking([filed])).each == (2,), "with one, the given-up file is out"
    assert await store.count_lacking([filed], ticked=[True]) == Lacking(each=(2,), files=2)

    # The read that comes before every product: a file the probe gave up on is not unread.
    video = next(media for media in ALLOWED_MEDIA if media.kind is Kind.VIDEO)
    await store.upsert_asset(digest="unread", media=video, size_bytes=1)
    unreadable, _ = await store.upsert_asset(digest="unreadable", media=video, size_bytes=1)
    await store.record_verdict(
        unreadable.id, VerdictProduct.PROBE, code="not_decodable", reason="z"
    )
    assert await store.unread_count() == 1


async def test_what_is_lacking_can_be_counted_over_what_one_user_sees(
    content_store: ContentStore, temp_db: Any, library_root: LibraryRoot
) -> None:
    """The number a feature's settings screen calls "still to do" for the person looking. The
    stored visibility is joined in here, once, in place of the feature handing its whole settled
    set to the access layer as an array: the count is per user, concealed rows are out, and
    a file the feature gave up on is out. Every file has a copy on disk, since a count of what is
    lacking counts only files there is a copy of."""
    from sift.kernel.access import Role
    from sift.testing.fixtures import create_user

    store = content_store
    seen = await _read_video(store, "seen")
    hidden = await _read_video(store, "hidden")
    given_up = await _read_video(store, "given-up")
    unseen = await _read_video(store, "unseen")
    for one in (seen, hidden, given_up, unseen):
        await store.add_location(asset_id=one, root_id=library_root.id, rel_path=f"{one}.mp4")
    await store.record_verdict(given_up, VerdictProduct.THUMBNAILS, code="no_frame", reason="x")
    viewer = (await create_user(temp_db, Role.ADMIN)).id
    other = (await create_user(temp_db, Role.GUEST)).id
    # The stored visibility is written out here rather than derived, because what is under test is
    # the count's join, not the rule that fills the table: an admin is handed every file with a copy
    # on disk, so the answer is cleared and this viewer given three of the four, one concealed.
    await temp_db.execute("DELETE FROM viewer_assets WHERE user_id = ?", (viewer,))
    for asset_id, concealed in ((seen, 0), (hidden, 1), (given_up, 0)):
        await temp_db.execute(
            "INSERT INTO viewer_assets (user_id, asset_id, concealed) VALUES (?, ?, ?)",
            (viewer, asset_id, concealed),
        )
    pictures = lacks_derivative([DerivativeKind.THUMB])
    assert pictures is not None
    filed = Lack(pictures.condition, pictures.params, product=VerdictProduct.THUMBNAILS.value)

    assert await store.count_lacking_visible(viewer, [filed]) == Lacking(each=(1,), files=1)
    assert await store.count_lacking_visible(viewer, [pictures]) == Lacking(each=(2,), files=2)
    assert await store.count_lacking_visible(other, [filed]) == Lacking(each=(0,), files=0)
    assert await store.count_lacking_visible(viewer, []) == Lacking(each=(), files=0)


async def test_a_row_the_pass_gave_up_on_stops_costing_every_import_a_whole_read(
    content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """One row at the old identity that cannot be sampled would keep `legacy_identities_remain`
    true for ever, and every file taken in would be digested whole for as long as it stood. A
    verdict on it takes it out of the count, the page and the flag, and the flag is remembered
    between asks, so the two writes that can change it forget it."""
    target = place("accepted.mp4", library_root, "clip.mp4")
    proof = checked(target, settings)
    taken = await content_store.ingest(proof, root_id=library_root.id, rel_path="clip.mp4")
    await _make_legacy(content_store, taken.asset.id, await hash_file(proof))
    assert await content_store.legacy_identities_remain()
    assert await content_store.legacy_identity_count() == 1

    await content_store.record_verdict(
        taken.asset.id, VerdictProduct.IDENTITY, code="no_copy", reason="No readable copy."
    )

    assert not await content_store.legacy_identities_remain()
    assert await content_store.legacy_identity_count() == 0
    assert await content_store.legacy_identity_page(10) == []

    # A transient verdict (the share was away) is not the same: the row still waits.
    await content_store.record_verdict(
        taken.asset.id, VerdictProduct.IDENTITY, code="share_away", reason="Away.", transient=True
    )
    assert await content_store.legacy_identities_remain()

    await content_store.clear_verdicts(VerdictProduct.IDENTITY)
    assert await content_store.legacy_identities_remain()
    assert await content_store.adopt_identity(taken.asset.id, GOLDEN_IDENTITY["accepted.mp4"])
    assert not await content_store.legacy_identities_remain()


async def test_files_already_measured_as_needing_repair_can_be_found_again(
    content_store: ContentStore, library_root: LibraryRoot, temp_db: Database
) -> None:
    """What a switch being turned back ON has to find.

    Everything here was measured when it was read, and nothing reads a file again for it, so
    without this turning the repair off and on again leaves exactly the files it exists for
    permanently unrepaired, and nothing anywhere says so.
    """
    video = next(media for media in ALLOWED_MEDIA if media.kind is Kind.VIDEO)
    bad, _ = await content_store.upsert_asset(digest="bad", media=video, size_bytes=1)
    fine, _ = await content_store.upsert_asset(digest="fine", media=video, size_bytes=1)
    nowhere, _ = await content_store.upsert_asset(digest="nowhere", media=video, size_bytes=1)
    for asset_id, gap in ((bad.id, 900_000), (fine.id, 0), (nowhere.id, 900_000)):
        await temp_db.execute("UPDATE assets SET interleave_gap = ? WHERE id = ?", (gap, asset_id))

    # Only the first has somewhere to read the bytes from. A file with no readable copy is left
    # out: there is nothing to copy, so queueing one would be a job that can only fail.
    for asset_id in (bad.id, fine.id):
        await content_store.add_location(
            asset_id=asset_id, root_id=library_root.id, rel_path=f"{asset_id}.mp4"
        )

    assert await content_store.needing_remux(500_000, 10) == [bad.id]


async def test_dropping_a_kind_of_derivative_takes_the_files_with_the_rows(
    content_store: ContentStore, settings: Any
) -> None:
    """For a feature being switched off.

    The files go first and the rows after, and that order is the survivable one: a file removed
    whose row survives is found by the leftover sweep and reported, while a row removed whose file
    survives is a file nothing will ever mention again: it just sits in the cache taking space,
    which is exactly what somebody switching this off is trying to stop.
    """
    video = next(media for media in ALLOWED_MEDIA if media.kind is Kind.VIDEO)
    asset, _ = await content_store.upsert_asset(digest="repaired", media=video, size_bytes=1)

    kept = await content_store.add_derivative(asset.id, DerivativeKind.THUMB, extension="jpg")
    dropped = await content_store.add_derivative(
        asset.id, DerivativeKind.REMUX, extension="mp4", size_bytes=4096
    )
    swept = await content_store.add_derivative(
        asset.id, DerivativeKind.REMUX, extension="mp4", params={"quality": 1}, size_bytes=2048
    )
    for derivative in (kept, dropped):
        on_disk = settings.cache_dir / derivative.rel_cache_path
        on_disk.parent.mkdir(parents=True, exist_ok=True)
        on_disk.write_bytes(b"x")

    # The third row's file is already gone, which is the ordinary state of a cache somebody has
    # swept. Its recorded size is not counted, because nothing was freed by removing it.
    count, freed = await content_store.drop_derivatives(DerivativeKind.REMUX)

    assert count == 2
    assert freed == 4096
    assert not (settings.cache_dir / dropped.rel_cache_path).exists()
    assert (settings.cache_dir / kept.rel_cache_path).exists()
    assert [one.kind for one in await content_store.derivatives(asset.id)] == [DerivativeKind.THUMB]
    assert swept.kind is DerivativeKind.REMUX

    # And switching off something that was never built removes nothing and says nothing. A log line
    # for a kind with no rows is noise on the one screen somebody reads to see what happened.
    assert await content_store.drop_derivatives(DerivativeKind.SPRITE) == (0, 0)


# --- the record's own writers -----------------------------------------------------------------
#
# Four one-statement writers, each the ONLY writer of its column. That is what makes them worth
# their own tests rather than being exercised through whatever screen calls them: the record form
# and an import both arrive here, and a column with one writer has one place to be wrong.


@pytest.mark.integration
async def test_a_title_is_written_and_a_blank_one_clears_it(
    content_store: Any, library_root: LibraryRoot, settings: Any
) -> None:
    """Blank is not a title. Stored as itself it draws an empty box where a dash belongs, and puts
    a row of spaces into the word index."""
    held = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "clip.mp4"), settings),
        root_id=library_root.id,
        rel_path="clip.mp4",
        folder_id=None,
    )

    await content_store.set_title(held.asset.id, "  A Name  ")
    assert (await content_store.get(held.asset.id)).title == "A Name"

    await content_store.set_title(held.asset.id, "   ")
    assert (await content_store.get(held.asset.id)).title is None


@pytest.mark.integration
async def test_a_release_date_is_written_and_a_blank_one_clears_it(
    content_store: Any, library_root: LibraryRoot, settings: Any
) -> None:
    held = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "clip.mp4"), settings),
        root_id=library_root.id,
        rel_path="clip.mp4",
        folder_id=None,
    )

    await content_store.set_release_date(held.asset.id, " 1991-02-02 ")
    assert (await content_store.get(held.asset.id)).release_date == "1991-02-02"

    await content_store.set_release_date(held.asset.id, None)
    assert (await content_store.get(held.asset.id)).release_date is None


@pytest.mark.integration
async def test_an_address_somebody_types_is_cleaned_the_way_a_seeded_one_is(
    content_store: Any, library_root: LibraryRoot, settings: Any
) -> None:
    """The cleaning is done in the writer, not at one caller: otherwise a typed address would go in
    with its tracking parameters and a seeded one would not, the same column holding two kinds of
    value depending on which way it arrived. Both spellings work when you click them, which is why
    nothing on a screen would show it."""
    held = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "clip.mp4"), settings),
        root_id=library_root.id,
        rel_path="clip.mp4",
        folder_id=None,
    )

    await content_store.set_download_url(held.asset.id, "https://example.test/a?utm_source=x")
    typed = (await content_store.get(held.asset.id)).download_url

    await content_store.set_download_url(held.asset.id, None)
    assert (await content_store.get(held.asset.id)).download_url is None

    await content_store.seed_download_url(held.asset.id, "https://example.test/a?utm_source=x")
    assert (await content_store.get(held.asset.id)).download_url == typed


@pytest.mark.integration
async def test_a_seeded_address_never_overwrites_one_that_is_already_there(
    content_store: Any, library_root: LibraryRoot, settings: Any
) -> None:
    """The same link dropped again months later re-imports nothing but it does finish, and a finish
    that overwrote this column would silently undo a correction somebody typed onto the record."""
    held = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "clip.mp4"), settings),
        root_id=library_root.id,
        rel_path="clip.mp4",
        folder_id=None,
    )
    await content_store.set_download_url(held.asset.id, "https://example.test/corrected")

    await content_store.seed_download_url(held.asset.id, "https://example.test/from-the-drop")

    assert (await content_store.get(held.asset.id)).download_url == "https://example.test/corrected"


@pytest.mark.integration
async def test_a_seeded_address_that_cleans_away_to_nothing_writes_nothing(
    content_store: Any, library_root: LibraryRoot, settings: Any
) -> None:
    held = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "clip.mp4"), settings),
        root_id=library_root.id,
        rel_path="clip.mp4",
        folder_id=None,
    )

    await content_store.seed_download_url(held.asset.id, "   ")

    assert (await content_store.get(held.asset.id)).download_url is None


# --- where a file came from, on a library that already existed --------------------------------


@pytest.mark.integration
async def test_the_rest_of_a_records_words_are_trimmed_and_a_blank_one_clears_it(
    content_store: Any, library_root: LibraryRoot, settings: Any
) -> None:
    """One test for four fields, because they are one rule.

    Blank is not a value in any of them: stored as itself it draws an empty box where a dash
    belongs, and puts a row of spaces into the word index.
    """
    held = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "clip.mp4"), settings),
        root_id=library_root.id,
        rel_path="clip.mp4",
        folder_id=None,
    )
    asset_id = held.asset.id

    await content_store.set_details(asset_id, "  What it is about.  ")
    await content_store.set_production_date(asset_id, " 1990-06-01 ")
    await content_store.set_site_code(asset_id, "  SITE-1234  ")
    await content_store.set_music(asset_id, "  Plain Jane - Someone  ")

    record = await content_store.get(asset_id)
    assert record.details == "What it is about."
    assert record.production_date == "1990-06-01"
    assert record.site_code == "SITE-1234"
    assert record.music == "Plain Jane - Someone"

    await content_store.set_details(asset_id, "   ")
    await content_store.set_production_date(asset_id, None)
    await content_store.set_site_code(asset_id, "")
    await content_store.set_music(asset_id, None)

    cleared = await content_store.get(asset_id)
    assert cleared.details is None
    assert cleared.production_date is None
    assert cleared.site_code is None
    assert cleared.music is None


@pytest.mark.integration
async def test_a_song_typed_again_as_it_already_reads_changes_nothing_and_tells_no_screen(
    content_store: Any, library_root: LibraryRoot, settings: Any
) -> None:
    """Saving the record form unchanged is a press, not a change: no screen is told to re-read."""
    held = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "clip.mp4"), settings),
        root_id=library_root.id,
        rel_path="clip.mp4",
        folder_id=None,
    )
    first = await content_store.set_music(held.asset.id, "Plain Jane - Someone")
    bus = ChangeBus()
    changes.listens(bus)
    try:
        watching = bus.subscribe("admin")
        again = await content_store.set_music(held.asset.id, "  Plain Jane - Someone ")
        told = watching.take(as_admin=True)
    finally:
        changes.listens(None)

    assert first.changed and not again.changed
    assert (again.before, again.after) == (first.after, first.after)
    assert not told


@pytest.mark.integration
async def test_a_files_links_are_a_whole_set_and_replacing_them_takes_the_rest_away(
    content_store: Any, library_root: LibraryRoot, settings: Any
) -> None:
    """A list, unlike the four above, and there is no "add one": the record form edits the set
    and saves it in one press, so a partial write would be a list that can only grow."""
    held = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "clip.mp4"), settings),
        root_id=library_root.id,
        rel_path="clip.mp4",
        folder_id=None,
    )
    asset_id = held.asset.id

    await content_store.set_links(
        asset_id, ["https://example.test/one", "https://example.test/two"]
    )
    assert await content_store.links_of(asset_id) == [
        "https://example.test/one",
        "https://example.test/two",
    ]

    await content_store.set_links(asset_id, ["https://example.test/two"])
    assert await content_store.links_of(asset_id) == ["https://example.test/two"]

    await content_store.set_links(asset_id, [])
    assert await content_store.links_of(asset_id) == []


@pytest.mark.integration
async def test_a_link_is_cleaned_the_way_the_download_address_is_and_not_kept_twice(
    content_store: Any, library_root: LibraryRoot, settings: Any
) -> None:
    """One column holding two kinds of value depending on how it got there is the thing this
    avoids: an address typed into the record and one that arrived from a stash-box are stored the
    same way. Blanks and duplicates go, which is what stops a saved form growing a row of nothing.
    """
    held = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "clip.mp4"), settings),
        root_id=library_root.id,
        rel_path="clip.mp4",
        folder_id=None,
    )

    await content_store.set_links(
        held.asset.id,
        [
            "https://example.test/a?utm_source=x",
            "https://example.test/a?utm_source=y",
            "   ",
        ],
    )

    assert await content_store.links_of(held.asset.id) == ["https://example.test/a"]


@pytest.mark.integration
async def test_a_file_nothing_has_been_said_about_has_no_links(
    content_store: Any, library_root: LibraryRoot, settings: Any
) -> None:
    held = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "clip.mp4"), settings),
        root_id=library_root.id,
        rel_path="clip.mp4",
        folder_id=None,
    )

    assert await content_store.links_of(held.asset.id) == []


@pytest.mark.integration
async def test_a_track_from_a_download_is_seeded_once_and_never_over_a_correction(
    content_store: Any, library_root: LibraryRoot, settings: Any
) -> None:
    """The same link dropped again months later re-imports nothing, but it does FINISH, and a
    finish that overwrote this column would silently undo a correction somebody had typed."""
    held = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "clip.mp4"), settings),
        root_id=library_root.id,
        rel_path="clip.mp4",
        folder_id=None,
    )
    asset_id = held.asset.id

    await content_store.seed_music(asset_id, "  Plain Jane - Someone  ")
    assert (await content_store.get(asset_id)).music == "Plain Jane - Someone"

    await content_store.seed_music(asset_id, "Something Else")
    assert (await content_store.get(asset_id)).music == "Plain Jane - Someone"


async def _songs_named(content_store: Any) -> list[Any]:
    """Every `song_named` event, with the one subject each carries."""
    return list(
        await content_store._db.fetch_all(
            "SELECT d.actor_kind, d.actor_id, d.object_kind, d.object_id, d.object_name,"
            " d.payload, s.kind, s.subject_id, s.name"
            " FROM workbench_decisions d JOIN workbench_decision_subjects s ON s.decision_id = d.id"
            " WHERE d.verb = 'song_named' ORDER BY d.id"
        )
    )


@pytest.mark.integration
async def test_a_song_named_from_a_page_is_an_act_the_record_says_once(
    content_store: Any, library_root: LibraryRoot, settings: Any
) -> None:
    """History says "Sift named the song X from <Site>'s page" off this event, and nothing else
    can say it: a field that simply has a value reads the same whether somebody typed it or a page
    supplied it. One event, in the write's own transaction, naming the file, the Site and the song,
    and none for the re-drop that found the name already there, which named nothing."""
    from sift.kernel.ledger import Object

    held = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "clip.mp4"), settings),
        root_id=library_root.id,
        rel_path="clip.mp4",
        folder_id=None,
    )
    asset_id = held.asset.id
    site = Object(kind="site", id="s-page", name="Another Studio")

    assert await content_store.seed_music(asset_id, "  Plain Jane - Someone  ", page=site)
    assert not await content_store.seed_music(asset_id, "Something Else", page=site)

    (event,) = await _songs_named(content_store)
    assert (event["actor_kind"], event["actor_id"]) == ("sift", "download")
    assert (event["object_kind"], event["object_id"], event["object_name"]) == (
        "site",
        "s-page",
        "Another Studio",
    )
    said = json.loads(event["payload"])
    # The song it was put on, by id (`kernel/content/songs.py`): the act names its song.
    song_id = said.pop("song_id")
    assert said == {"song": "Plain Jane - Someone"}
    carried = await content_store._db.fetch_one(
        "SELECT song_id, source FROM song_files WHERE asset_id = ?", (asset_id,)
    )
    assert carried is not None and (carried["song_id"], carried["source"]) == (song_id, "site")
    # The one subject is the file, by name: History says which file the song was put on.
    assert (event["kind"], event["subject_id"], event["name"]) == ("asset", asset_id, "clip.mp4")
    assert (event["kind"], event["subject_id"]) == ("asset", asset_id)
    assert event["name"] == "clip.mp4", "the file's name at the moment, not an unnamed file"


@pytest.mark.integration
async def test_a_typed_song_is_neither_overwritten_nor_claimed_by_a_page(
    content_store: Any, library_root: LibraryRoot, settings: Any
) -> None:
    """A name somebody typed stays, and the record does not say Sift named it: a line claiming
    a page supplied a value the page never wrote would be untrue about the one thing it records."""
    held = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "clip.mp4"), settings),
        root_id=library_root.id,
        rel_path="clip.mp4",
        folder_id=None,
    )
    await content_store.set_music(held.asset.id, "Someone Else")

    assert not await content_store.seed_music(held.asset.id, "Something Else")
    assert (await content_store.get(held.asset.id)).music == "Someone Else"
    assert await _songs_named(content_store) == []


@pytest.mark.integration
async def test_a_download_that_says_nothing_about_a_track_writes_nothing(
    content_store: Any, library_root: LibraryRoot, settings: Any
) -> None:
    # A blank is not a track. Written as one it would fill the column with nothing and then refuse
    # the real answer when it arrived, because the column would no longer be null.
    held = await content_store.ingest(
        checked(place("accepted.mp4", library_root, "clip.mp4"), settings),
        root_id=library_root.id,
        rel_path="clip.mp4",
        folder_id=None,
    )

    await content_store.seed_music(held.asset.id, "   ")

    assert (await content_store.get(held.asset.id)).music is None
