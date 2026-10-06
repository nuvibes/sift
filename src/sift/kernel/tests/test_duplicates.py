# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two reads duplicate-finding is allowed to make, mostly tested for what they refuse: unprobed
assets have nothing to compare, and a missing location frees no space."""

from __future__ import annotations

import asyncio
import itertools
import time
from typing import Any

import pytest

from sift.kernel.content import duplicates
from sift.kernel.content.duplicates import FINGERPRINTS_WITHIN, Copy, DuplicateReads, Redundancy
from sift.kernel.content.identity import LocationStatus
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.paging import MAX_PAGE_SIZE

pytestmark = pytest.mark.unit

_EPOCH = 1_700_000_000

#: A phash is 16 hex characters and a videohash 480; only the widths matter here.
_PHASH = "0123456789abcdef"
_VIDEOHASH = _PHASH * 30


@pytest.fixture
async def db(temp_db: Database) -> Database:
    await temp_db.initialize_schema()
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        ("root", "root", "/library/root", _EPOCH),
    )
    return temp_db


@pytest.fixture
def reads(db: Database) -> DuplicateReads:
    return DuplicateReads(db)


async def add_asset(
    db: Database,
    asset_id: str,
    *,
    identity: str | None = None,
    media_type: str = "video",
    phash: str | None = _PHASH,
    videohash: str | None = _VIDEOHASH,
) -> None:
    await db.execute(
        "INSERT INTO assets (id, identity, media_type, phash, videohash, added_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (asset_id, identity or f"digest-{asset_id}", media_type, phash, videohash, _EPOCH),
    )


async def add_location(
    db: Database,
    asset_id: str,
    rel_path: str,
    *,
    size_bytes: int | None = 1000,
    status: str = "present",
) -> str:
    location_id = new_id()
    await db.execute(
        "INSERT INTO asset_locations "
        "(id, asset_id, root_id, folder_id, rel_path, filename, size_bytes, status, "
        " first_seen_at, last_seen_at) "
        "VALUES (?, ?, 'root', NULL, ?, ?, ?, ?, ?, ?)",
        (
            location_id,
            asset_id,
            rel_path,
            rel_path.rsplit("/", 1)[-1],
            size_bytes,
            status,
            _EPOCH,
            _EPOCH,
        ),
    )
    return location_id


# --- what has not been looked at yet ----------------------------------------------------------


async def test_videos_nothing_has_fingerprinted_are_counted(
    db: Database, reads: DuplicateReads
) -> None:
    """The count of files still to fingerprint (missing the exact-file hash) tells an empty queue
    from one not yet looked at."""
    await add_asset(db, "waiting")
    await db.execute("UPDATE assets SET oshash = NULL WHERE id = 'waiting'")
    await add_asset(db, "done")
    await db.execute("UPDATE assets SET oshash = 'abc' WHERE id = 'done'")

    assert await reads.awaiting_fingerprint() == 1


async def test_a_photograph_is_never_waiting_on_a_video_fingerprint(
    db: Database, reads: DuplicateReads
) -> None:
    """A still has no whole-video fingerprint to be missing, or the count never reaches zero."""
    await add_asset(db, "a-picture", media_type="image", videohash=None)

    assert await reads.awaiting_fingerprint() == 0


async def test_an_empty_library_is_waiting_on_nothing(reads: DuplicateReads) -> None:
    """Zero arrived at by counting rather than by there being no row to read."""
    assert await reads.awaiting_fingerprint() == 0


async def test_a_file_that_cannot_be_fingerprinted_is_counted_apart_from_one_that_is_waiting(
    db: Database, reads: DuplicateReads
) -> None:
    """A file refused during the probe is counted apart, or it would sit in "not fingerprinted yet"
    for good."""
    await add_asset(db, "refused")
    await db.execute("UPDATE assets SET oshash = NULL WHERE id = 'refused'")
    await db.execute(
        "INSERT INTO file_verdicts (asset_id, product, code, reason, transient, at)"
        " VALUES ('refused', 'fingerprints', 'not_decodable', 'the frames would not decode', 0, 1)"
    )

    assert await reads.awaiting_fingerprint() == 0, "work that cannot happen is counted as pending"
    assert await reads.cannot_fingerprint() == 1


async def test_a_file_that_was_merely_away_is_still_waiting_rather_than_refused(
    db: Database, reads: DuplicateReads
) -> None:
    """A transient verdict is not counted as uncomparable: the next scan clears it."""
    await add_asset(db, "blinked")
    await db.execute("UPDATE assets SET oshash = NULL WHERE id = 'blinked'")
    await db.execute(
        "INSERT INTO file_verdicts (asset_id, product, code, reason, transient, at)"
        " VALUES ('blinked', 'fingerprints', 'unreadable', 'the share was away', 1, 1)"
    )

    assert await reads.awaiting_fingerprint() == 1
    assert await reads.cannot_fingerprint() == 0


async def test_an_empty_library_can_compare_nothing_and_says_zero(reads: DuplicateReads) -> None:
    """Zero arrived at by counting, as above: a bare aggregate answers one row either way."""
    assert await reads.cannot_fingerprint() == 0


# --- fingerprints ---------------------------------------------------------------------------


async def test_an_unprobed_asset_offers_no_fingerprint(db: Database, reads: DuplicateReads) -> None:
    """A file waiting for its probe is not a candidate."""
    await add_asset(db, "probed")
    await add_asset(db, "waiting", phash=None, videohash=None)

    found = await reads.fingerprints()

    assert [row.asset_id for row in found] == ["probed"]


async def test_the_fingerprints_are_made_off_the_loop(
    db: Database, reads: DuplicateReads, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One object per fingerprinted file is a whole library's worth of them: made on the loop, the
    read would hold every page and video while "Similar to this" or a duplicate pass asked for
    them."""
    await add_asset(db, "probed")
    make = duplicates._fingerprints_of

    def slow(rows: Any) -> Any:
        time.sleep(0.3)
        return make(rows)

    monkeypatch.setattr(duplicates, "_fingerprints_of", slow)
    ticks: list[float] = []

    async def tick() -> None:
        while True:
            ticks.append(time.perf_counter())
            await asyncio.sleep(0.005)

    ticker = asyncio.create_task(tick())
    await asyncio.sleep(0.02)
    try:
        found = await reads.fingerprints()
        await asyncio.sleep(0.02)
    finally:
        ticker.cancel()
    assert [row.asset_id for row in found] == ["probed"]
    longest = max(later - earlier for earlier, later in itertools.pairwise(ticks))
    assert longest < 0.15, f"the loop was held for {longest:.3f} s making the fingerprints"


async def test_an_image_keeps_its_null_videohash(db: Database, reads: DuplicateReads) -> None:
    """A still has a frame fingerprint and no video one, so it is never comparable to a video."""
    await add_asset(db, "picture", media_type="image", videohash=None)

    (found,) = await reads.fingerprints()

    assert found.phash == _PHASH
    assert found.videohash is None
    assert found.media_type == "image"


async def test_a_fingerprint_carries_the_digest(db: Database, reads: DuplicateReads) -> None:
    """The digest is carried, so a pair is proved near and not exact."""
    await add_asset(db, "one", identity="aaaa")

    (found,) = await reads.fingerprints()

    assert found.identity == "aaaa"


async def test_fingerprints_can_be_read_within_a_set_of_files(
    db: Database, reads: DuplicateReads
) -> None:
    """A viewer's cheap Similar reads their files, never the library's."""
    for asset_id in ("aaa", "bbb", "ccc"):
        await add_asset(db, asset_id)

    statement = FINGERPRINTS_WITHIN.replace(
        "{{FILES}}", "SELECT :one AS asset_id UNION SELECT :two"
    )
    found = await reads.fingerprints(within=(statement, {"one": "aaa", "two": "ccc"}))

    assert [row.asset_id for row in found] == ["aaa", "ccc"]


async def test_fingerprints_come_back_in_a_settled_order(
    db: Database, reads: DuplicateReads
) -> None:
    """Two scans of an unchanged library do the same work in the same order."""
    for asset_id in ("ccc", "aaa", "bbb"):
        await add_asset(db, asset_id)

    assert [row.asset_id for row in await reads.fingerprints()] == ["aaa", "bbb", "ccc"]
    assert [row.asset_id for row in await reads.fingerprints()] == ["aaa", "bbb", "ccc"]


async def test_a_fingerprint_carries_its_generation_and_its_exact_key(
    db: Database, reads: DuplicateReads
) -> None:
    """A reader holding another install's facts (a swap) compares fingerprints only within one
    generation, and confirms an exact match by oshash and size, so both ride with the row."""
    await add_asset(db, "aaa")
    await db.execute(
        "UPDATE assets SET fingerprint_version = 2, oshash = 'abcdef0123456789', size_bytes = 4321"
        " WHERE id = 'aaa'"
    )

    (row,) = await reads.fingerprints()

    assert (row.fingerprint_version, row.oshash, row.size_bytes) == (2, "abcdef0123456789", 4321)


async def test_an_empty_library_has_no_fingerprints(reads: DuplicateReads) -> None:
    assert await reads.fingerprints() == []


# --- redundancies ---------------------------------------------------------------------------


async def test_one_copy_is_not_a_redundancy(db: Database, reads: DuplicateReads) -> None:
    """A file that sits in one place is a file, not a duplicate. Nothing to reclaim."""
    await add_asset(db, "single")
    await add_location(db, "single", "single.mp4")

    assert await reads.redundancies_page(limit=50, offset=0) == []


async def test_two_copies_of_one_asset_are_reported_together(
    db: Database, reads: DuplicateReads
) -> None:
    """Exact duplicates are one asset with two locations, a fact the tables hold, never queued."""
    await add_asset(db, "twin")
    await add_location(db, "twin", "a/twin.mp4")
    await add_location(db, "twin", "b/twin.mp4")

    (found,) = await reads.redundancies_page(limit=50, offset=0)

    assert found.asset_id == "twin"
    assert [copy.rel_path for copy in found.copies] == ["a/twin.mp4", "b/twin.mp4"]


async def test_a_missing_copy_is_not_counted(db: Database, reads: DuplicateReads) -> None:
    """A missing copy is no redundancy: it would offer to free space that is already free."""
    await add_asset(db, "half")
    await add_location(db, "half", "here.mp4")
    await add_location(db, "half", "gone.mp4", status="missing")

    assert await reads.redundancies_page(limit=50, offset=0) == []


async def test_a_missing_copy_is_left_out_of_an_otherwise_redundant_asset(
    db: Database, reads: DuplicateReads
) -> None:
    """Two present copies and a missing one: the missing one is not offered for removal."""
    await add_asset(db, "twin")
    await add_location(db, "twin", "a.mp4")
    await add_location(db, "twin", "b.mp4")
    await add_location(db, "twin", "gone.mp4", status="missing")

    (found,) = await reads.redundancies_page(limit=50, offset=0)

    assert [copy.rel_path for copy in found.copies] == ["a.mp4", "b.mp4"]
    assert all(copy.status is LocationStatus.PRESENT for copy in found.copies)


async def test_several_redundant_assets_are_kept_apart(db: Database, reads: DuplicateReads) -> None:
    """Interleaved rows fold back per asset, or one file's copies would be offered as another's."""
    await add_asset(db, "aaa")
    await add_asset(db, "bbb")
    await add_location(db, "aaa", "aaa-1.mp4")
    await add_location(db, "aaa", "aaa-2.mp4")
    await add_location(db, "bbb", "bbb-1.mp4")
    await add_location(db, "bbb", "bbb-2.mp4")
    await add_location(db, "bbb", "bbb-3.mp4")

    found = {entry.asset_id: entry for entry in await reads.redundancies_page(limit=50, offset=0)}

    assert len(found["aaa"].copies) == 2
    assert len(found["bbb"].copies) == 3


async def test_an_empty_library_has_no_redundancies(reads: DuplicateReads) -> None:
    assert await reads.redundancies_page(limit=50, offset=0) == []


async def test_a_page_is_whole_assets_and_never_half_of_one(
    db: Database, reads: DuplicateReads
) -> None:
    """The limit is on assets, inside the subquery: on the join it would count locations and show
    two of a file's three copies."""
    await add_asset(db, "aaa")
    await add_asset(db, "bbb")
    for name in ("aaa-1.mp4", "aaa-2.mp4", "aaa-3.mp4"):
        await add_location(db, "aaa", name)
    await add_location(db, "bbb", "bbb-1.mp4")
    await add_location(db, "bbb", "bbb-2.mp4")

    first = await reads.redundancies_page(limit=1, offset=0)

    assert [one.asset_id for one in first] == ["aaa"]
    assert len(first[0].copies) == 3


async def test_the_next_page_carries_on_where_the_last_one_stopped(
    db: Database, reads: DuplicateReads
) -> None:
    """And does not repeat what the first one already showed, which is what an unordered subquery
    would do: the same asset twice and another one never."""
    for name in ("aaa", "bbb", "ccc"):
        await add_asset(db, name)
        await add_location(db, name, f"{name}-1.mp4")
        await add_location(db, name, f"{name}-2.mp4")

    seen = [
        one.asset_id
        for offset in (0, 1, 2)
        for one in await reads.redundancies_page(limit=1, offset=offset)
    ]

    assert seen == ["aaa", "bbb", "ccc"]


async def test_one_asset_is_asked_for_by_name(db: Database, reads: DuplicateReads) -> None:
    """What a request to release a copy is checked against: one asset, rather than the whole
    library read to answer a question about one."""
    await add_asset(db, "twin")
    await add_location(db, "twin", "a.mp4")
    await add_location(db, "twin", "b.mp4")

    found = await reads.redundancy_for("twin")

    assert found is not None
    assert [copy.rel_path for copy in found.copies] == ["a.mp4", "b.mp4"]


async def test_an_asset_with_one_copy_is_not_found_by_name(
    db: Database, reads: DuplicateReads
) -> None:
    """None states the asset is not redundant: a release is refused, since the last copy ends it."""
    await add_asset(db, "single")
    await add_location(db, "single", "single.mp4")

    assert await reads.redundancy_for("single") is None


async def test_an_asset_is_placed_where_the_list_of_redundancies_shows_it(
    db: Database, reads: DuplicateReads
) -> None:
    """The position counts only the assets the list itself holds before it, so a page opened at it
    lands on the asset asked about: an asset stored once in between does not move it."""
    for name in ("aaa", "bbb", "ccc"):
        await add_asset(db, name)
        await add_location(db, name, f"{name}-1.mp4")
        if name != "bbb":
            await add_location(db, name, f"{name}-2.mp4")

    assert await reads.redundancy_position("aaa") == 0
    assert await reads.redundancy_position("ccc") == 1
    (landed,) = await reads.redundancies_page(limit=1, offset=1)
    assert landed.asset_id == "ccc"


async def test_an_asset_stored_once_or_never_has_no_place_on_that_list(
    db: Database, reads: DuplicateReads
) -> None:
    await add_asset(db, "single")
    await add_location(db, "single", "single.mp4")

    assert await reads.redundancy_position("single") is None
    assert await reads.redundancy_position("never-existed") is None


async def test_an_asset_nobody_has_heard_of_is_not_found_by_name(reads: DuplicateReads) -> None:
    assert await reads.redundancy_for("never-existed") is None


async def test_the_totals_count_assets_and_add_up_the_extra_copies(
    db: Database, reads: DuplicateReads
) -> None:
    """The card's two numbers, read without a path: `SUM - MAX` per asset is `reclaimable_bytes`."""
    await add_asset(db, "twin")
    await add_location(db, "twin", "a.mp4", size_bytes=300)
    await add_location(db, "twin", "b.mp4", size_bytes=100)
    await add_asset(db, "trip")
    for name, size in (("x.mp4", 50), ("y.mp4", 20), ("z.mp4", 10)):
        await add_location(db, "trip", name, size_bytes=size)
    await add_asset(db, "single")
    await add_location(db, "single", "one.mp4", size_bytes=999)

    totals = await reads.redundant_totals()
    page = await reads.redundancies_page(limit=50, offset=0)

    assert totals.assets == 2, "the file with one copy is not redundant"
    assert totals.reclaimable_bytes == 100 + 30
    assert totals.reclaimable_bytes == sum(one.reclaimable_bytes for one in page)


async def test_a_copy_with_no_recorded_size_counts_as_nothing_in_both(
    db: Database, reads: DuplicateReads
) -> None:
    """A NULL size arrives at the same number in SQLite and in Python."""
    await add_asset(db, "twin")
    await add_location(db, "twin", "a.mp4", size_bytes=None)
    await add_location(db, "twin", "b.mp4", size_bytes=400)

    totals = await reads.redundant_totals()
    (page,) = await reads.redundancies_page(limit=50, offset=0)

    assert totals.reclaimable_bytes == 0 == page.reclaimable_bytes


async def test_an_empty_library_totals_nothing_rather_than_failing(reads: DuplicateReads) -> None:
    """A bare aggregate answers with one row on an empty table, which is why there is no guard."""
    totals = await reads.redundant_totals()

    assert totals.assets == 0
    assert totals.reclaimable_bytes == 0


# --- what a removal would actually free -----------------------------------------------------


def copy(size: int | None) -> Copy:
    return Copy(
        location_id=new_id(),
        root_id="root",
        rel_path="x.mp4",
        filename="x.mp4",
        size_bytes=size,
        status=LocationStatus.PRESENT,
    )


def redundancy(*sizes: int | None) -> Redundancy:
    return Redundancy(
        asset_id="a",
        identity="d",
        media_type="video",
        copies=tuple(copy(size) for size in sizes),
    )


def test_reclaiming_keeps_one_copy() -> None:
    """Three copies of a 100-byte file free 200 bytes, not 300. One of them is the file."""
    assert redundancy(100, 100, 100).reclaimable_bytes == 200


def test_reclaiming_keeps_the_largest_copy() -> None:
    """Where recorded sizes disagree, the biggest is kept, so it never claims to free too much."""
    assert redundancy(100, 250, 100).reclaimable_bytes == 200


def test_a_copy_with_no_recorded_size_counts_as_nothing() -> None:
    """Not guessed from a sibling. This number is the entire point of the screen it appears on."""
    assert redundancy(100, None).reclaimable_bytes == 0
    assert redundancy(None, None).reclaimable_bytes == 0
    assert redundancy(100, 100, None).reclaimable_bytes == 100


# --- what a keeper rule may compare, and where a file sits
#
# Chunked by the caller's list, never capped: a cap reads as a file gone.


async def test_where_a_file_sits_is_the_first_copy_that_is_actually_there(
    db: Database, reads: DuplicateReads
) -> None:
    """A present copy is reported over one on an unplugged drive, the first present one kept."""
    await add_asset(db, "a1")
    await add_location(db, "a1", "gone/clip.mp4", status="missing")
    await add_location(db, "a1", "here/clip.mp4")
    await add_location(db, "a1", "also-here/clip.mp4")

    places = await reads.places_of(["a1"])

    assert places["a1"].asset_id == "a1"
    assert places["a1"].root_id == "root"
    assert places["a1"].rel_path == "here/clip.mp4", "a later copy overwrote the first present one"


async def test_a_file_with_nowhere_readable_is_absent_rather_than_empty(
    db: Database, reads: DuplicateReads
) -> None:
    """Absent, not a Place with a blank path. A row saying a file is at "" would be drawn."""
    await add_asset(db, "a1")
    await add_location(db, "a1", "gone/clip.mp4", status="missing")

    assert await reads.places_of(["a1"]) == {}
    # And an empty list asks nothing at all rather than being a query about nothing.
    assert await reads.places_of([]) == {}


async def test_the_numbers_a_keeper_rule_compares_come_back_per_file(
    db: Database, reads: DuplicateReads
) -> None:
    """Five columns and not the asset, so a keeper rule is decided by facts about the FILE."""
    await db.execute(
        "INSERT INTO assets (id, identity, media_type, size_bytes, width, height, added_at) "
        "VALUES ('a1', 'digest-a1', 'video', 4096, 1920, 1080, ?)",
        (_EPOCH,),
    )

    measures = await reads.measures_of(["a1", "a1"])

    assert measures["a1"].asset_id == "a1"
    assert (measures["a1"].size_bytes, measures["a1"].width, measures["a1"].height) == (
        4096,
        1920,
        1080,
    )
    assert measures["a1"].added_at == _EPOCH


async def test_a_file_that_has_gone_between_two_reads_has_no_measure(
    db: Database, reads: DuplicateReads
) -> None:
    """An id that has just gone has no measure, so no rule decides about it."""
    await add_asset(db, "a1")

    measures = await reads.measures_of(["a1", "never-existed"])

    assert "never-existed" not in measures
    assert await reads.measures_of([]) == {}


async def test_both_reads_are_bounded_by_the_callers_list_and_not_by_a_page(
    db: Database, reads: DuplicateReads
) -> None:
    """Asked about more files than one page holds, both reads answer for every one."""
    many = [f"a{n:04d}" for n in range(MAX_PAGE_SIZE + 7)]
    for asset_id in many:
        await add_asset(db, asset_id)
        await add_location(db, asset_id, f"{asset_id}/clip.mp4")

    places = await reads.places_of(many)
    measures = await reads.measures_of(many)

    assert len(places) == len(many)
    assert len(measures) == len(many)
