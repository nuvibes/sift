# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every expensive row says which generation of work made it, and the probe's answer is kept.

The fault these are about cannot be seen in a fresh database, which is why they are here rather
than folded into the content suite. A library filled before the stamps exist has rows that look
finished and cannot be told apart afterwards: the first improvement to a fingerprint or a picture
recipe is then a decode of every file in the library, because nothing says which generation any
row belongs to. The migration is the only moment that answer is free: exactly one generation has
ever shipped, so today's value is known without reading anything.

Written out at the older shape rather than built from `schema.py`, for the reason every migration
test here gives: a fixture assembled from the module under test acquires today's shape and then
asserts the migration turned it into itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from blake3 import blake3

# A fingerprint write records an event through the ledger door, and the door writes into the
# workbench's own table, so a database built without that component has nowhere to put it.
# Imported for the registration, nothing else.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.config import Settings
from sift.kernel.content import hashing
from sift.kernel.content import schema as content_schema
from sift.kernel.content.identity import (
    PROBE_VERSION,
    RECIPE_VERSIONS,
    ContentStore,
    DerivativeKind,
    ProbeKeep,
    lacks_derivative,
    lacks_fingerprint,
)
from sift.kernel.content.perceptual import FINGERPRINT_VERSION
from sift.kernel.db import Connection, Database
from sift.kernel.ids import new_id
from sift.kernel.landing import registered_landings
from sift.testing.fixtures import LibraryRoot

pytestmark = pytest.mark.regression

_EPOCH = 1_700_000_000


async def _asset(connection: Connection, asset_id: str, **hashes: str | None) -> None:
    await connection.execute(
        "INSERT INTO assets (id, identity, media_type, added_at, probed_at,"
        " phash, videohash, oshash, video_phash)"
        " VALUES (?, ?, 'video', ?, ?, ?, ?, ?, ?)",
        (
            asset_id,
            f"digest-{asset_id}",
            _EPOCH,
            _EPOCH,
            hashes.get("phash"),
            hashes.get("videohash"),
            hashes.get("oshash"),
            hashes.get("video_phash"),
        ),
    )


@pytest.fixture
async def store(temp_db: Database, settings: Any) -> ContentStore:
    await temp_db.initialize_schema()
    return ContentStore(temp_db, settings)


async def _one_video(temp_db: Database) -> str:
    """A read file, with an id `derivative_relpath` will accept: it refuses anything that is not
    one Sift minted, which is what keeps a cache path from being made out of somebody's text."""
    asset_id = new_id()
    async with temp_db.write() as connection:
        await connection.execute(
            "INSERT INTO assets (id, identity, media_type, added_at, probed_at)"
            " VALUES (?, ?, 'video', ?, ?)",
            (asset_id, f"digest-{asset_id}", _EPOCH, _EPOCH),
        )
    return asset_id


async def _set_fingerprint_version(temp_db: Database, asset_id: str, version: int | None) -> None:
    async with temp_db.write() as connection:
        await connection.execute(
            "UPDATE assets SET fingerprint_version = ? WHERE id = ?", (version, asset_id)
        )


async def test_writing_the_four_hashes_says_which_generation_took_them(
    store: ContentStore, temp_db: Database
) -> None:
    """The stamp is bound at the statement, not chosen by the caller: this is the only write that
    can honestly say what took a fingerprint, because it is the one that has just taken it."""
    asset_id = await _one_video(temp_db)

    await store.record_fingerprints(
        asset_id, phash="aa", videohash="bb", oshash="cc", video_phash="dd", name="clip.mp4"
    )

    async with temp_db.write() as connection:
        (row,) = await connection.execute_fetchall(
            "SELECT fingerprint_version FROM assets WHERE id = ?", (asset_id,)
        )
    assert row["fingerprint_version"] == FINGERPRINT_VERSION


async def test_a_file_fingerprinted_by_an_older_generation_is_offered_again(
    store: ContentStore, temp_db: Database, library_root: LibraryRoot
) -> None:
    """The whole point of the column. Every one of these four reads decides that a file has been
    fingerprinted, and all four have to agree: the page the pass takes, the test it makes of a
    page of ids, the number the bar draws and the term the Build sheet counts. One of them left
    behind is a pass that works through files the sheet says are finished.

    The file has a copy on disk: a file with no copy there is no work for any pass, so without
    one the empty answers below would hold whatever the version said.
    """
    asset_id = await _one_video(temp_db)
    await store.add_location(asset_id=asset_id, root_id=library_root.id, rel_path="clip.mp4")
    await store.record_fingerprints(
        asset_id, phash="aa", videohash="bb", oshash="cc", video_phash="dd", name="clip.mp4"
    )

    assert await store.unfingerprinted(10) == []
    assert await store.unfingerprinted_among([asset_id]) == set()
    assert await store.unfingerprinted_count() == 0

    # The arithmetic moved on: what this file holds was taken by the generation before this one.
    await _set_fingerprint_version(temp_db, asset_id, FINGERPRINT_VERSION - 1)

    assert await store.unfingerprinted(10) == [asset_id]
    assert await store.unfingerprinted_among([asset_id]) == {asset_id}
    assert await store.unfingerprinted_count() == 1


async def test_a_picture_built_to_an_older_recipe_is_not_a_picture_this_build_has(
    store: ContentStore, temp_db: Database
) -> None:
    """A stale thumbnail is present and is not the thing that would be built now. Matching on kind
    alone counts it as done, so every Build count says a library is finished a generation behind."""
    asset_id = await _one_video(temp_db)
    await store.add_derivative(asset_id, DerivativeKind.THUMB, extension="jpg")

    assert await store.lacking_derivative(DerivativeKind.THUMB, [asset_id]) == set()

    async with temp_db.write() as connection:
        await connection.execute("UPDATE derivatives SET recipe_version = recipe_version - 1")

    assert await store.lacking_derivative(DerivativeKind.THUMB, [asset_id]) == {asset_id}


async def test_a_picture_is_stamped_with_the_recipe_that_built_it(
    store: ContentStore, temp_db: Database
) -> None:
    """Stamped at the one door every builder goes through, so no builder can leave it out."""
    asset_id = await _one_video(temp_db)

    await store.add_derivative(asset_id, DerivativeKind.SPRITE, extension="jpg")

    async with temp_db.write() as connection:
        (row,) = await connection.execute_fetchall(
            "SELECT recipe_version FROM derivatives WHERE kind = 'sprite'"
        )
    assert row["recipe_version"] == RECIPE_VERSIONS[DerivativeKind.SPRITE]


async def test_a_rebuilt_picture_is_stamped_with_the_recipe_that_rebuilt_it(
    store: ContentStore, temp_db: Database
) -> None:
    """A thumbnail has no settings in its name, so rebuilding one replaces the row rather than
    adding a second. Left at the old number, the catch-up pass finds it again and builds it
    again, for ever."""
    asset_id = await _one_video(temp_db)
    await store.add_derivative(asset_id, DerivativeKind.THUMB, extension="jpg")
    async with temp_db.write() as connection:
        await connection.execute("UPDATE derivatives SET recipe_version = 0")

    await store.add_derivative(asset_id, DerivativeKind.THUMB, extension="jpg")

    async with temp_db.write() as connection:
        rows = list(await connection.execute_fetchall("SELECT recipe_version FROM derivatives"))
    assert [row["recipe_version"] for row in rows] == [RECIPE_VERSIONS[DerivativeKind.THUMB]]


async def test_what_the_tool_said_is_kept_beside_the_fields_read_out_of_it(
    store: ContentStore, temp_db: Database
) -> None:
    """One transaction, because the columns and the answer they were read from are one fact."""
    asset_id = await _one_video(temp_db)

    await store.record_probe(
        asset_id,
        width=1920,
        height=1080,
        audio_channels=2,
        audio_sample_rate=48_000,
        keep=ProbeKeep(body=b"compressed", tool="ffprobe version 7.1.5"),
    )

    async with temp_db.write() as connection:
        (row,) = await connection.execute_fetchall(
            "SELECT probe_version, tool, body FROM asset_probes WHERE asset_id = ?", (asset_id,)
        )
        (asset,) = await connection.execute_fetchall(
            "SELECT audio_channels, audio_sample_rate FROM assets WHERE id = ?", (asset_id,)
        )
    assert row["tool"] == "ffprobe version 7.1.5"
    assert row["body"] == b"compressed"
    assert row["probe_version"] == PROBE_VERSION
    assert asset["audio_channels"] == 2
    assert asset["audio_sample_rate"] == 48_000


async def test_a_kept_answer_goes_when_the_file_does(
    store: ContentStore, temp_db: Database
) -> None:
    """It describes one file and nothing else, so it has a real key and the database clears it."""
    asset_id = await _one_video(temp_db)
    await store.record_probe(asset_id, keep=ProbeKeep(body=b"x", tool="ffprobe"))

    async with temp_db.write() as connection:
        await connection.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        rows = list(await connection.execute_fetchall("SELECT asset_id FROM asset_probes"))
    assert rows == []


async def test_the_catch_up_offers_only_files_whose_reading_was_never_kept(
    store: ContentStore, temp_db: Database
) -> None:
    """What the pass takes. A file that has been read and whose answer is kept is finished with;
    one that was never read at all is the import queue's business, not this pass's."""
    kept = await _one_video(temp_db)
    await store.record_probe(kept, keep=ProbeKeep(body=b"x", tool="ffprobe"))
    waiting = await _one_video(temp_db)
    async with temp_db.write() as connection:
        await connection.execute(
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES"
            " (?, 'digest-unread', 'video', ?)",
            (new_id(), _EPOCH),
        )

    assert await store.assets_lacking_probe_rows(10) == [waiting]
    assert await store.assets_lacking_probe_rows_count() == 1


async def test_the_catch_up_writes_the_answer_and_the_sound_together(
    store: ContentStore, temp_db: Database
) -> None:
    """It deliberately does not rewrite the ten fields (several of them come from somewhere other
    than this tool), so the sound's shape rides on a statement that names only itself."""
    asset_id = await _one_video(temp_db)
    async with temp_db.write() as connection:
        await connection.execute("UPDATE assets SET width = 1920 WHERE id = ?", (asset_id,))

    await store.keep_probe(
        asset_id,
        ProbeKeep(body=b"y", tool="ffprobe"),
        audio_channels=6,
        audio_sample_rate=44_100,
    )

    async with temp_db.write() as connection:
        (row,) = await connection.execute_fetchall(
            "SELECT width, audio_channels, audio_sample_rate FROM assets WHERE id = ?", (asset_id,)
        )
        (kept,) = await connection.execute_fetchall("SELECT body FROM asset_probes")
    assert row["width"] == 1920, "the catch-up blanked a field it was never given"
    assert row["audio_channels"] == 6
    assert row["audio_sample_rate"] == 44_100
    assert kept["body"] == b"y"


async def test_the_set_wise_count_asks_the_same_question_as_the_pass(
    store: ContentStore, temp_db: Database, library_root: LibraryRoot
) -> None:
    """The Build sheet counts through terms rather than through the statements above, and the two
    have to agree: a sheet that says a library is finished while the pass still has work is a
    disagreement nothing else would show. The values ride in the term's own order, so a
    mis-ordered one answers confidently and wrongly.

    The file has a copy on disk, because the count takes in only files there is a copy of: work on
    a file every copy of which is gone is work no pass can do.
    """
    asset_id = await _one_video(temp_db)
    await store.add_location(asset_id=asset_id, root_id=library_root.id, rel_path="clip.mp4")
    await store.record_fingerprints(
        asset_id, phash="aa", videohash="bb", oshash="cc", video_phash="dd", name="clip.mp4"
    )
    await store.add_derivative(asset_id, DerivativeKind.THUMB, extension="jpg")

    thumbnails = lacks_derivative([DerivativeKind.THUMB])
    assert thumbnails is not None, "a kind that was asked for is a term"
    terms = [lacks_fingerprint(), thumbnails]
    settled = await store.count_lacking(terms)
    assert settled.each == (0, 0)
    assert settled.files == 0

    # Both generations move on: the arithmetic and the recipe.
    await _set_fingerprint_version(temp_db, asset_id, FINGERPRINT_VERSION - 1)
    async with temp_db.write() as connection:
        await connection.execute("UPDATE derivatives SET recipe_version = recipe_version - 1")

    behind = await store.count_lacking(terms)
    assert behind.each == (1, 1)
    assert behind.files == 1, "one file lacking two things is one file"


# --- the whole-file digest, taken while the bytes are still on the local disk ---------------------
#
# `whole_digest` is NULL on every row of a library built since the identity became a SAMPLE of a
# file, and filling it afterwards means reading every byte of every file over whatever the library
# sits on: days on a share. The landing is the one moment that read is local, so it is taken
# there and only there; a scanned file is deliberately left alone until something needs it.


async def _landed_library(connection: Connection) -> None:
    """A new library holding one file a landing can name: `a-fresh`, identity `digest-a-fresh`."""
    await content_schema.initialize_library(connection, on_disk=0)
    await content_schema.initialize_content(connection, on_disk=0)
    await _asset(connection, "a-fresh")


async def test_a_file_that_lands_has_every_byte_of_it_digested(
    temp_db: Database, tmp_path: Path, settings: Settings
) -> None:
    async with temp_db.write() as connection:
        await _landed_library(connection)

    staged = tmp_path / "arrived.mp4"
    staged.write_bytes(b"the bytes that arrived" * 500)
    await hashing.WholeDigestLanding(temp_db).landed(staged, "digest-a-fresh", settings)

    row = await temp_db.fetch_one("SELECT whole_digest FROM assets WHERE id = 'a-fresh'")
    assert row is not None
    assert row["whole_digest"] == blake3(staged.read_bytes()).hexdigest()


async def test_a_row_that_already_carries_a_digest_is_never_argued_with(
    temp_db: Database, tmp_path: Path, settings: Settings
) -> None:
    """The same bytes can land twice (a second copy of a file the library already holds), and
    the second landing must not rewrite what the first wrote."""
    async with temp_db.write() as connection:
        await _landed_library(connection)
        await connection.execute("UPDATE assets SET whole_digest = 'already' WHERE id = 'a-fresh'")

    staged = tmp_path / "arrived.mp4"
    staged.write_bytes(b"different bytes entirely")
    await hashing.WholeDigestLanding(temp_db).landed(staged, "digest-a-fresh", settings)

    row = await temp_db.fetch_one("SELECT whole_digest FROM assets WHERE id = 'a-fresh'")
    assert row is not None
    assert row["whole_digest"] == "already"


async def test_bytes_no_asset_row_claims_write_nothing_and_do_not_fail(
    temp_db: Database, tmp_path: Path, settings: Settings
) -> None:
    """A landing is keyed by identity, and the bytes may turn out to belong to no row at all:
    a copy that failed, or a file quarantined on the way in. There is nothing to write and nothing
    to report."""
    async with temp_db.write() as connection:
        await _landed_library(connection)

    staged = tmp_path / "arrived.mp4"
    staged.write_bytes(b"nobody's bytes")
    await hashing.WholeDigestLanding(temp_db).landed(staged, "digest-nothing-here", settings)

    rows = await temp_db.fetch_all("SELECT whole_digest FROM assets WHERE whole_digest IS NOT NULL")
    assert list(rows) == []


def test_the_landing_is_registered_so_a_file_arriving_reaches_it() -> None:
    """The registration is the whole of the wiring: nothing else calls this, and a hook that is
    not in the registry is a hook nothing ever runs."""
    assert hashing.WholeDigestLanding.name in registered_landings()


async def test_the_files_with_sound_are_counted_apart_from_the_library(
    store: ContentStore, temp_db: Database
) -> None:
    """The music fingerprint's denominator is the files with a sound track, not every file."""
    async with temp_db.write() as connection:
        await _asset(connection, "01HX00000000000000000000A1")
        await _asset(connection, "01HX00000000000000000000A2")
        await connection.execute(
            "UPDATE assets SET acodec = 'aac' WHERE id = ?", ("01HX00000000000000000000A1",)
        )
    assert await store.with_audio_count() == 1
    assert await store.asset_count() == 2
