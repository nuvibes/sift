# SPDX-License-Identifier: AGPL-3.0-or-later
"""The queue, the reclaim view, and the promise to touch nothing: the full pipeline over a library
with exact and near duplicates leaves every real file on disk."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sift.kernel.access import Viewer
from sift.kernel.content.duplicates import DuplicateReads
from sift.kernel.db import Database
from sift.slices.dedup import service as dedup_service
from sift.slices.dedup.grouping import Group, Rule
from sift.slices.dedup.matcher import (
    DEFAULT_ACCURACY,
    DEFAULT_MAX_DURATION_GAP_MS,
    Accuracy,
)
from sift.slices.dedup.service import (
    DEFAULT_RULE,
    DedupService,
    Dials,
    NotAllowed,
    NotFound,
    duration_gap_from,
    level_from,
)
from sift.slices.dedup.tests.conftest import Library, Recorder

pytestmark = pytest.mark.integration


def test_a_receipt_title_names_three_files_and_counts_the_rest() -> None:
    """A title over five files would be a title nobody reads; three by name and the rest counted."""
    names = {letter: f"{letter}.mp4" for letter in "abcde"}
    assert dedup_service._named(("a",), names) == "a.mp4"
    assert dedup_service._named(("a", "b"), names) == "a.mp4 and b.mp4"
    assert dedup_service._named(("a", "b", "c"), names) == "a.mp4, b.mp4 and c.mp4"
    assert dedup_service._named(tuple("abcde"), names) == "a.mp4, b.mp4, c.mp4 and 2 more"
    assert dedup_service._named(("gone",), names) == "a file"
    assert dedup_service._named((), names) == ""


_PHASH = "0f0f0f0f0f0f0f0f"
_NEAR = "0f0f0f0f0f0f0f0e"  # one bit different: the same picture, re-encoded
_FAR = "f0f0f0f0f0f0f0f0"  # every bit different: a different picture


async def set_fingerprint(
    db: Database, asset_id: str, phash: str, videohash: str | None = None
) -> None:
    """Write the fingerprint probing would have written, in the column the asset's kind uses: a
    video's whole-video fingerprint, not `phash`."""
    row = await db.fetch_one("SELECT media_type FROM assets WHERE id = ?", (asset_id,))
    if row is not None and row["media_type"] == "video":
        await db.execute(
            "UPDATE assets SET video_phash = ?, duration_ms = ? WHERE id = ?",
            (phash, 210000, asset_id),
        )
        return
    await db.execute(
        "UPDATE assets SET phash = ?, videohash = ? WHERE id = ?", (phash, videohash, asset_id)
    )


def files_under(path: Path) -> set[Path]:
    return {found.relative_to(path) for found in path.rglob("*") if found.is_file()}


def dials(
    level: Accuracy = DEFAULT_ACCURACY,
    gap: int | None = DEFAULT_MAX_DURATION_GAP_MS,
    rule: Rule = DEFAULT_RULE,
) -> Dials:
    """The three controls, at their defaults unless a test is about one of them."""
    return Dials(level=level, max_duration_gap_ms=gap, rule=rule)


async def pairs_in(service: DedupService, at: Dials | None = None) -> int:
    """How many pending pairs the grouping read; every pair lands in exactly one group."""
    return sum(len(one.pairs) for one in await service.groups(at or dials()))


# --- the distinction: exact against near ------------------------------------------------------


async def test_an_exact_duplicate_is_one_asset_in_two_places_and_never_a_question(
    temp_db: Database,
    service: DedupService,
    managed: Library,
    add_file: Any,
) -> None:
    """The same bytes twice are one asset with two locations: in reclaim, never in the queue."""
    first = await add_file(managed, "one.mp4")
    second = await add_file(managed, "copy-of-one.mp4")

    assert first.asset.id == second.asset.id, "identical bytes are one asset"
    assert not second.asset_is_new

    await set_fingerprint(temp_db, first.asset.id, _PHASH)
    await service.scan()

    assert await service.groups(dials()) == [], "an exact duplicate is not a question"

    (redundancy,) = await service.reclaim(limit=50, offset=0)
    assert redundancy.asset_id == first.asset.id
    assert len(redundancy.copies) == 2
    assert redundancy.reclaimable_bytes > 0


async def test_a_near_duplicate_is_two_assets_and_one_question(
    temp_db: Database,
    service: DedupService,
    managed: Library,
    add_file: Any,
) -> None:
    """Different bytes that look alike are two assets and one question, nothing to reclaim."""
    first = await add_file(managed, "one.jpg", "accepted.jpg")
    second = await add_file(managed, "two.png", "accepted.png")

    assert first.asset.id != second.asset.id

    await set_fingerprint(temp_db, first.asset.id, _PHASH)
    await set_fingerprint(temp_db, second.asset.id, _NEAR)

    assert await service.scan() == 1

    (group,) = await service.groups(dials())
    assert set(group.ids) == {first.asset.id, second.asset.id}
    assert group.method == "phash"
    assert group.distance == 1
    assert len(group.pairs) == 1

    assert await service.reclaim(limit=50, offset=0) == [], (
        "two different files are not copies of each other"
    )


async def test_two_different_pictures_are_never_asked_about(
    temp_db: Database, service: DedupService, managed: Library, add_file: Any
) -> None:
    first = await add_file(managed, "one.jpg", "accepted.jpg")
    second = await add_file(managed, "two.png", "accepted.png")

    await set_fingerprint(temp_db, first.asset.id, _PHASH)
    await set_fingerprint(temp_db, second.asset.id, _FAR)

    assert await service.scan() == 0
    assert await service.groups(dials()) == []


# --- the import policy ------------------------------------------------------------------------


async def test_a_near_duplicate_is_imported_and_flagged_never_dropped(
    temp_db: Database, service: DedupService, managed: Library, add_file: Any
) -> None:
    """A near duplicate is imported, kept and flagged, never dropped; there is no setting for it."""
    original = await add_file(managed, "original.jpg", "accepted.jpg")
    similar = await add_file(managed, "similar.png", "accepted.png")

    await set_fingerprint(temp_db, original.asset.id, _PHASH)
    await set_fingerprint(temp_db, similar.asset.id, _NEAR)
    await service.scan()

    # Kept: both files are on disk and both are indexed.
    assert (managed.path / "original.jpg").exists()
    assert (managed.path / "similar.png").exists()
    fingerprints = {row.asset_id for row in await DuplicateReads(temp_db).fingerprints()}
    assert fingerprints == {original.asset.id, similar.asset.id}

    # And flagged.
    assert len(await service.groups(dials())) == 1


# --- dismiss has to stick ---------------------------------------------------------------------


async def test_a_dismissed_pair_is_not_asked_about_again(
    temp_db: Database, service: DedupService, managed: Library, add_file: Any
) -> None:
    """A dismissed pair is not filed again by the next scan."""
    first = await add_file(managed, "one.jpg", "accepted.jpg")
    second = await add_file(managed, "two.png", "accepted.png")
    await set_fingerprint(temp_db, first.asset.id, _PHASH)
    await set_fingerprint(temp_db, second.asset.id, _NEAR)

    await service.scan()
    (group,) = await service.groups(dials())
    (candidate_id,) = group.pairs

    admin_viewer = await _an_admin(temp_db)
    assert await service.dismiss_group(group, actor=admin_viewer) == 1
    assert await service.groups(dials()) == []
    # A group with no pending pairs answers nought and writes nothing.
    assert (
        await service.dismiss_group(
            Group(ids=group.ids, method=group.method, distance=0), actor=admin_viewer
        )
        == 0
    )

    # The scan runs again, finds the same pair, and must leave it settled.
    assert await service.scan() == 0
    assert await service.groups(dials()) == []
    assert (await service.get(candidate_id)).status == "dismissed"


async def test_a_confirmed_group_names_every_file_but_the_keeper(
    temp_db: Database,
    service: DedupService,
    recorder: Recorder,
    managed: Library,
    add_file: Any,
) -> None:
    """Keeping one of a group is one press and one receipt; pairs leave with their files."""
    group = await _one_group(temp_db, service, managed, add_file)
    keep, drop = group.ids[0], group.ids[1]

    done = await service.settle_group(group, keep=keep, actor=await _an_admin(temp_db))

    assert done.kept == keep
    assert done.removed == (drop,)
    assert done.refused == ()
    assert [one.asset_id for one in recorder.asked] == [drop]


async def test_a_rescan_of_an_unchanged_library_files_nothing_new(
    temp_db: Database, service: DedupService, managed: Library, add_file: Any
) -> None:
    """A pending pair is not duplicated by running the scan twice either."""
    first = await add_file(managed, "one.jpg", "accepted.jpg")
    second = await add_file(managed, "two.png", "accepted.png")
    await set_fingerprint(temp_db, first.asset.id, _PHASH)
    await set_fingerprint(temp_db, second.asset.id, _NEAR)

    assert await service.scan() == 1
    assert await service.scan() == 0
    assert len(await service.groups(dials())) == 1


# --- removal goes out through the seam, and only when asked -----------------------------------


async def test_dismissing_removes_nothing(
    temp_db: Database,
    service: DedupService,
    recorder: Recorder,
    managed: Library,
    add_file: Any,
) -> None:
    """ "These are different" is not an instruction to delete anything, ever."""
    group = await _one_group(temp_db, service, managed, add_file)

    await service.dismiss_group(group, actor=await _an_admin(temp_db))

    assert recorder.asked == []


async def test_a_chain_too_long_to_be_one_question_cannot_be_settled_at_all(
    temp_db: Database,
    service: DedupService,
    recorder: Recorder,
    managed: Library,
    add_file: Any,
) -> None:
    """A component past the cap cannot be settled: its ends may look nothing alike."""
    group = await _one_group(temp_db, service, managed, add_file)
    chain = Group(
        ids=group.ids, method=group.method, distance=group.distance, pairs=group.pairs, too_big=True
    )

    with pytest.raises(NotAllowed):
        await service.settle_group(chain, keep=chain.ids[0], actor=await _an_admin(temp_db))

    assert recorder.asked == []


async def test_keeping_one_and_removing_the_other_goes_through_the_removal_seam(
    temp_db: Database,
    service: DedupService,
    recorder: Recorder,
    managed: Library,
    add_file: Any,
) -> None:
    """The only way this feature ever removes anything, and it is somebody else's code that does.

    `mode="disk"` is what sends it to the bin rather than merely forgetting it, and no
    `location_id` is right here: the whole asset is going, not one of its copies.
    """
    group = await _one_group(temp_db, service, managed, add_file)
    admin_viewer = await _an_admin(temp_db)

    await service.settle_group(group, keep=group.ids[0], actor=admin_viewer)

    assert len(recorder.asked) == 1
    assert recorder.asked[0].asset_id == group.ids[1]
    assert recorder.asked[0].mode == "disk"
    assert recorder.asked[0].location_id is None
    assert recorder.asked[0].actor == admin_viewer.id


async def test_a_file_outside_the_group_cannot_be_the_one_kept(
    temp_db: Database,
    service: DedupService,
    recorder: Recorder,
    managed: Library,
    add_file: Any,
) -> None:
    """Keeping a file that is not in the group would delete every file that IS in it.

    Without this, "settle this group, keeping X" is a general-purpose delete of the group with an
    unrelated id attached, and the keeper stops meaning anything.
    """
    group = await _one_group(temp_db, service, managed, add_file)
    other = await add_file(managed, "unrelated.gif", "accepted.gif")

    with pytest.raises(NotFound):
        await service.settle_group(group, keep=other.asset.id, actor=await _an_admin(temp_db))

    assert recorder.asked == []


async def test_a_dismissed_group_can_be_put_back_and_a_deleted_one_cannot(
    temp_db: Database,
    service: DedupService,
    recorder: Recorder,
    managed: Library,
    add_file: Any,
) -> None:
    """A dismissed group can be put back; a deleted one cannot, and `restore_group` says False."""
    group = await _one_group(temp_db, service, managed, add_file)
    await service.dismiss_group(group, actor=await _an_admin(temp_db))

    assert await service.restore_group(group.pairs) is True
    assert len(await service.groups(dials())) == 1, "and it is back in the queue"

    # Nothing to put back for a group nobody dismissed.
    assert await service.restore_group(()) is False
    assert await service.restore_group(("no-such-pair",)) is False


# --- reclaiming one copy ----------------------------------------------------------------------


async def test_releasing_a_copy_names_the_one_copy_to_remove(
    temp_db: Database,
    service: DedupService,
    recorder: Recorder,
    managed: Library,
    add_file: Any,
) -> None:
    """The narrow removal the reclaim view exists for.

    `location_id` is the whole point: without it the removal takes every place the asset sits,
    which for a file with two copies is the file gone rather than the redundancy gone.
    """
    first = await add_file(managed, "one.mp4")
    await add_file(managed, "two.mp4")
    admin_viewer = await _an_admin(temp_db)

    (redundancy,) = await service.reclaim(limit=50, offset=0)
    doomed = redundancy.copies[1]

    await service.release(first.asset.id, doomed.location_id, actor=admin_viewer)

    assert len(recorder.asked) == 1
    assert recorder.asked[0].asset_id == first.asset.id
    assert recorder.asked[0].location_id == doomed.location_id
    assert recorder.asked[0].mode == "disk"


async def test_releasing_a_copy_that_is_not_there_is_a_miss(
    temp_db: Database,
    service: DedupService,
    recorder: Recorder,
    managed: Library,
    add_file: Any,
) -> None:
    first = await add_file(managed, "one.mp4")
    await add_file(managed, "two.mp4")

    with pytest.raises(NotFound):
        await service.release(first.asset.id, "no-such-copy", actor=await _an_admin(temp_db))

    assert recorder.asked == []


async def test_a_file_with_one_copy_cannot_be_released(
    temp_db: Database,
    service: DedupService,
    recorder: Recorder,
    managed: Library,
    add_file: Any,
) -> None:
    """An asset that sits in one place is not redundant, so there is nothing here to let go of.

    Refused rather than allowed-and-narrowed, because letting this through would make the reclaim
    view a way to delete any file at all, and the last copy going is the asset ending, which is
    the one thing this screen must never quietly do.
    """
    only = await add_file(managed, "alone.mp4")
    locations = await DuplicateReads(temp_db).redundancies_page(limit=50, offset=0)
    assert locations == []

    with pytest.raises(NotFound):
        await service.release(only.asset.id, "any", actor=await _an_admin(temp_db))

    assert recorder.asked == []


# --- what happens when the removal seam says no ------------------------------------------------


class _Refuses:
    """A removal seam that refuses, the way the real one does on a read-only folder."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.asked = 0

    async def remove(self, asset_id: str, **kwargs: object) -> None:
        self.asked += 1
        raise self._error


async def test_a_refused_removal_leaves_the_group_in_the_queue(
    temp_db: Database, reads: DuplicateReads, managed: Library, add_file: Any
) -> None:
    """A refused removal leaves its group in the queue, reported without failing the whole press."""
    from sift.slices.dedup.service import RemovalRefused

    refuser = _Refuses(RemovalRefused("That folder is read-only."))
    service = DedupService(temp_db, reads, refuser)

    first = await add_file(managed, "one.jpg", "accepted.jpg")
    second = await add_file(managed, "two.png", "accepted.png")
    await set_fingerprint(temp_db, first.asset.id, _PHASH)
    await set_fingerprint(temp_db, second.asset.id, _NEAR)
    await service.scan()
    (group,) = await service.groups(dials())
    (candidate_id,) = group.pairs

    done = await service.settle_group(group, keep=group.ids[0], actor=await _an_admin(temp_db))

    assert refuser.asked == 1
    assert done.removed == (), "nothing went"
    assert done.refused == (group.ids[1],), "and the press says which file the disk kept"
    assert (await service.get(candidate_id)).status == "pending", "the pair is still a question"
    assert await service.groups(dials()) != [], "and it is still in the queue"


# --- the promise ------------------------------------------------------------------------------


async def test_the_whole_pipeline_removes_no_file_from_disk(
    temp_db: Database,
    real_service: DedupService,
    managed: Library,
    add_file: Any,
) -> None:
    """The whole pipeline, with the real remover behind the seam, removes no file from disk."""
    original = await add_file(managed, "original.jpg", "accepted.jpg")
    await add_file(managed, "exact-copy.jpg", "accepted.jpg")
    near = await add_file(managed, "similar.png", "accepted.png")
    await add_file(managed, "different.gif", "accepted.gif")

    await set_fingerprint(temp_db, original.asset.id, _PHASH)
    await set_fingerprint(temp_db, near.asset.id, _NEAR)

    before = files_under(managed.path)
    assert len(before) == 4

    # Everything the feature does on its own: find them, list them, list what could be reclaimed.
    await real_service.scan()
    pending = await real_service.groups(dials())
    reclaimable = await real_service.reclaim(limit=50, offset=0)

    assert pending, "it should have found the near duplicate"
    assert reclaimable, "it should have found the exact duplicate"
    assert files_under(managed.path) == before, "the pipeline removed a file from disk"


async def test_confirming_a_group_really_takes_the_other_file_off_the_disk(
    temp_db: Database,
    real_service: DedupService,
    managed: Library,
    add_file: Any,
) -> None:
    """Confirming a group really takes the other file off the disk, with `mode="disk"`."""
    keeper = await add_file(managed, "keep.jpg", "accepted.jpg")
    extra = await add_file(managed, "drop.png", "accepted.png")
    await set_fingerprint(temp_db, keeper.asset.id, _PHASH)
    await set_fingerprint(temp_db, extra.asset.id, _NEAR)
    await real_service.scan()
    (group,) = await real_service.groups(dials())

    done = await real_service.settle_group(
        group, keep=keeper.asset.id, actor=await _an_admin(temp_db)
    )

    assert done.removed == (extra.asset.id,)
    assert done.refused == ()
    assert files_under(managed.path) == {Path("keep.jpg")}, (
        "one file went, and it was the right one"
    )
    # And the group is gone from the queue because its row went with the file: nothing settled it.
    assert await real_service.groups(dials()) == []


async def test_a_scan_writes_nothing_to_the_assets_it_reads(
    temp_db: Database, service: DedupService, managed: Library, add_file: Any
) -> None:
    """No re-hashing, no re-probing, no touching the rows it is reading.

    Fingerprints come from probing and this slice consumes them. A scan that wrote back would be
    a second, quieter place where a fingerprint could be computed differently, and a fingerprint
    computed differently matches nothing that came before it.
    """
    first = await add_file(managed, "one.jpg", "accepted.jpg")
    second = await add_file(managed, "two.png", "accepted.png")
    await set_fingerprint(temp_db, first.asset.id, _PHASH)
    await set_fingerprint(temp_db, second.asset.id, _NEAR)

    before = await temp_db.fetch_all("SELECT id, identity, phash, videohash, probed_at FROM assets")
    await service.scan()
    after = await temp_db.fetch_all("SELECT id, identity, phash, videohash, probed_at FROM assets")

    assert [tuple(row) for row in before] == [tuple(row) for row in after]


# --- helpers ----------------------------------------------------------------------------------


async def _an_admin(db: Database) -> Viewer:
    from sift.kernel.access import Role
    from sift.testing.fixtures import create_user

    return await create_user(db, Role.ADMIN)


async def _one_group(db: Database, service: DedupService, managed: Library, add_file: Any) -> Group:
    """A library holding exactly one near-duplicate pair, scanned, as the group it makes."""
    first = await add_file(managed, "one.jpg", "accepted.jpg")
    second = await add_file(managed, "two.png", "accepted.png")
    await set_fingerprint(db, first.asset.id, _PHASH)
    await set_fingerprint(db, second.asset.id, _NEAR)
    await service.scan()
    (group,) = await service.groups(dials())
    return group


async def test_a_pair_settled_in_advance_is_never_asked_about(
    service: DedupService,
    temp_db: Database,
    managed: Library,
    add_file: Any,
) -> None:
    """A feature that deliberately produces a similar file says so, and nobody is asked.

    A compressed copy is the same picture at a smaller size, so every method here matches it
    against its original. Left alone that is a question with an obviously wrong answer available,
    and forty of them after a run over forty files.
    """
    first = await add_file(managed, "one.mp4")
    second = await add_file(managed, "two.mkv", source="accepted.mkv")

    await service.mark_unrelated(first.asset.id, second.asset.id)

    rows = await temp_db.fetch_all(
        "SELECT status, method FROM dedup_candidates WHERE asset_a = ? OR asset_b = ?",
        (min(first.asset.id, second.asset.id), min(first.asset.id, second.asset.id)),
    )
    # Every method, because which one WOULD match is a property of the files rather than of the
    # intent, and a pair dismissed under one would still be asked under another.
    assert len(rows) == 3
    assert {str(row["status"]) for row in rows} == {"dismissed"}
    assert {str(row["method"]) for row in rows} == {"phash", "videohash", "video_phash"}
    assert await service.groups(dials()) == []


async def test_settling_a_pair_twice_writes_it_once(
    service: DedupService, temp_db: Database, managed: Library, add_file: Any
) -> None:
    """A retried job must not double the rows, and must not reset a verdict somebody gave."""
    first = await add_file(managed, "one.mp4")
    second = await add_file(managed, "two.mkv", source="accepted.mkv")

    await service.mark_unrelated(first.asset.id, second.asset.id)
    await service.mark_unrelated(second.asset.id, first.asset.id)

    rows = await temp_db.fetch_all("SELECT id FROM dedup_candidates", ())
    assert len(rows) == 3


async def test_a_file_is_never_settled_against_itself(
    service: DedupService, temp_db: Database, managed: Library, add_file: Any
) -> None:
    """Identical bytes are one asset in two places, which is not a question this table asks."""
    only = await add_file(managed, "one.mp4")

    await service.mark_unrelated(only.asset.id, only.asset.id)

    assert await temp_db.fetch_all("SELECT id FROM dedup_candidates", ()) == []


# --- the two dials work on the rows, never on the library --------------------------------------
#
#
# A scan files everything at the most generous level once; both dials are questions asked of the
# rows, so moving them costs no rescan.


async def _two_near_pictures(temp_db: Database, managed: Library, add_file: Any) -> None:
    """Two stills four bits apart: inside every level except `exact` and `high`."""
    first = await add_file(managed, "one.jpg", "accepted.jpg")
    second = await add_file(managed, "two.png", "accepted.png")
    await set_fingerprint(temp_db, first.asset.id, "0f0f0f0f0f0f0f0f")
    await set_fingerprint(temp_db, second.asset.id, "0f0f0f0f0f0f0f00")  # four bits


async def _two_near_videos_of_different_lengths(
    temp_db: Database, managed: Library, add_file: Any
) -> None:
    """Two videos one bit apart that run 390 seconds apart: inside every level, and outside any
    length rule tighter than that."""
    first = await add_file(managed, "three.mp4")
    second = await add_file(managed, "four.mkv", "accepted.mkv")
    await set_fingerprint(temp_db, first.asset.id, "0f0f0f0f0f0f0f0f")
    await set_fingerprint(temp_db, second.asset.id, "0f0f0f0f0f0f0f0e")
    await temp_db.execute(
        "UPDATE assets SET duration_ms = ? WHERE id = ?", (600_000, second.asset.id)
    )


async def test_the_level_filters_rows_that_are_already_filed(
    temp_db: Database, service: DedupService, managed: Library, add_file: Any
) -> None:
    """One scan, four answers. Nothing is compared again between them."""
    await _two_near_pictures(temp_db, managed, add_file)
    assert await service.scan() == 1

    assert len(await service.groups(dials(level=Accuracy.LOW))) == 1
    assert len(await service.groups(dials(level=Accuracy.MEDIUM))) == 1
    assert await service.groups(dials(level=Accuracy.HIGH)) == []
    assert await service.groups(dials(level=Accuracy.EXACT)) == []

    # And back again, from the same rows, with no rescan in between.
    assert len(await service.groups(dials(level=Accuracy.MEDIUM))) == 1


async def test_tightening_the_level_hides_a_pair_rather_than_forgetting_it(
    temp_db: Database, service: DedupService, managed: Library, add_file: Any
) -> None:
    """The distinction the whole design turns on.

    A pair the level is hiding is still filed, still counted, and comes back the moment the level
    is loosened. If tightening deleted rows, loosening would have to compare the library again,
    and worse, a dismissal made while the level was loose would be lost with them.
    """
    await _two_near_pictures(temp_db, managed, add_file)
    await service.scan()

    shown, waiting = await service.pending_counts(level=Accuracy.HIGH)
    assert (shown, waiting) == (0, 1), "the pair is hidden, not gone"

    shown, waiting = await service.pending_counts(level=Accuracy.LOW)
    assert (shown, waiting) == (1, 1)


async def test_the_counts_and_the_page_are_filtered_the_same_way(
    temp_db: Database, service: DedupService, managed: Library, add_file: Any
) -> None:
    """The count and the page apply `_DIALS_SHOW` the same way across every dial setting."""
    # Two pairs differing in both dials: stills with no running time, and videos 390 s apart.
    await _two_near_pictures(temp_db, managed, add_file)
    await _two_near_videos_of_different_lengths(temp_db, managed, add_file)
    assert await service.scan() == 2

    seen: set[tuple[int, int]] = set()
    for level in Accuracy:
        for gap in (None, 0, 1, 10_000, 10_000_000):
            read = await pairs_in(service, dials(level=level, gap=gap))
            shown, _ = await service.pending_counts(level=level, max_duration_gap_ms=gap)
            assert read == shown, f"the listing and the count disagree at {level}/{gap}"
            seen.add((read, shown))

    # And the sweep really did move. Two statements that always answered the same number would
    # agree perfectly with the filter deleted from both.
    assert len(seen) > 1, "every setting gave the same answer, so nothing here was under test"


async def test_a_pair_of_unknown_length_survives_the_tightest_length_rule(
    temp_db: Database, service: DedupService, managed: Library, add_file: Any
) -> None:
    """Nobody can say how far apart these two run, so nothing may be concluded from it.

    A still has no duration at all, which is the permanent version of the same case, and the
    temporary one, a video not yet fully probed, has to behave identically. Hiding either would mean
    a length rule quietly becoming a way to lose duplicates.
    """
    await _two_near_pictures(temp_db, managed, add_file)
    await service.scan()

    assert len(await service.groups(dials(gap=1))) == 1
    assert len(await service.groups(dials(gap=None))) == 1


async def test_the_length_rule_hides_a_pair_that_is_filed_with_a_wide_gap(
    temp_db: Database, service: DedupService, managed: Library, add_file: Any
) -> None:
    """Two videos that look alike and run for wildly different lengths.

    Filed by the scan, because the scan applies no length rule at all, and then hidden or shown
    depending on what the reader asks for. Switching the rule off shows it again from the same row.
    """
    first = await add_file(managed, "one.mp4")
    second = await add_file(managed, "two.mkv", "accepted.mkv")
    await set_fingerprint(temp_db, first.asset.id, "0f0f0f0f0f0f0f0f")
    await set_fingerprint(temp_db, second.asset.id, "0f0f0f0f0f0f0f0e")
    await temp_db.execute(
        "UPDATE assets SET duration_ms = ? WHERE id = ?", (600_000, second.asset.id)
    )

    assert await service.scan() == 1
    (group,) = await service.groups(dials(gap=None))
    (candidate_id,) = group.pairs
    assert (await service.get(candidate_id)).duration_gap_ms == 390_000

    assert await service.groups(dials(gap=10_000)) == []
    assert len(await service.groups(dials(gap=400_000))) == 1


async def test_nothing_fingerprinted_yet_is_counted_so_an_empty_queue_can_be_read(
    temp_db: Database, service: DedupService, managed: Library, add_file: Any
) -> None:
    """Videos not yet fingerprinted are counted, telling "no duplicates" from "not looked yet"."""
    video = await add_file(managed, "one.mp4")
    assert await service.awaiting_fingerprint() == 1

    await temp_db.execute("UPDATE assets SET oshash = ? WHERE id = ?", ("abc", video.asset.id))
    assert await service.awaiting_fingerprint() == 0


# --- the dials, read back from what was stored -------------------------------------------------


def test_a_closeness_this_version_does_not_know_reads_as_the_default() -> None:
    """A stored word the settings registry would refuse today, which is how an older release's
    preference arrives here.

    Falling back is right for a read: a queue that refuses to draw because one preference is
    unfamiliar is worse than one drawn at the default, and the settings screen falls back the same
    way, so the number on the card and the position of the dial agree about what is in force.
    """
    assert level_from("medium") is Accuracy.MEDIUM
    assert level_from("a word nobody registered") is DEFAULT_ACCURACY
    assert level_from(None) is DEFAULT_ACCURACY


def test_a_length_rule_that_is_not_a_number_reads_as_the_default() -> None:
    """The same tolerance from the other side, and it has two ways to fail rather than one: a
    missing row hands back None, and a row written as prose hands back something `int()` refuses.
    Both mean "nothing usable is stored", and both take the shipped rule.
    """
    assert duration_gap_from(10) == 10_000
    assert duration_gap_from(None) == DEFAULT_MAX_DURATION_GAP_MS
    assert duration_gap_from("not a number") == DEFAULT_MAX_DURATION_GAP_MS


def test_a_length_rule_of_zero_switches_length_off_rather_than_demanding_equality() -> None:
    """Zero carries the off position, because "the two must run for exactly the same number of
    milliseconds" is not a rule anybody wants: two encodes of one video differ by a frame."""
    assert duration_gap_from(0) is None
    assert duration_gap_from(-5) is None


async def test_the_groups_are_kept_until_a_pair_moves(
    temp_db: Database,
    service: DedupService,
    managed: Library,
    add_file: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Folding every pending pair is the cost of the duplicates page and its card. A library
    mid-import announces several times a second and none of that is a new group; a pair filed,
    a pair answered and a file leaving (its pairs go by the cascade) each are."""
    from sift.kernel import changes
    from sift.kernel.audience import EVERY_ADMIN
    from sift.kernel.changes import About, ChangeBus

    first = await add_file(managed, "one.jpg", "accepted.jpg")
    second = await add_file(managed, "two.png", "accepted.png")
    third = await add_file(managed, "three.webp", "accepted.webp")
    await set_fingerprint(temp_db, first.asset.id, _PHASH)
    await set_fingerprint(temp_db, second.asset.id, _NEAR)
    await service.scan()
    folded = 0
    fold = service._grouped

    async def counted(at: Dials) -> list[Group]:
        nonlocal folded
        folded += 1
        return await fold(at)

    monkeypatch.setattr(service, "_grouped", counted)
    bus = ChangeBus()
    changes.listens(bus)
    try:
        assert await pairs_in(service) == 1
        for about in (About.LIBRARY, About.ARRIVALS, About.JOBS):
            bus.publish(EVERY_ADMIN, about)
        assert await pairs_in(service) == 1
        assert folded == 1, "an announcement is not a new group"

        await set_fingerprint(temp_db, third.asset.id, _NEAR)
        await service.scan()
        assert await pairs_in(service) == 3, "a pair filed is"
        assert folded == 2

        async with temp_db.write() as connection:
            await connection.execute("DELETE FROM assets WHERE id = ?", (third.asset.id,))
        assert await pairs_in(service) == 1, "a file leaving takes its pairs with it"
        assert folded == 3

        (group,) = await service.groups(dials())
        await service.dismiss_group(group, actor=await _an_admin(temp_db))
        assert await service.groups(dials()) == [], "a pair answered leaves the queue"
        assert folded == 4

        assert await service.restore_group(group.pairs)
        assert await pairs_in(service) == 1, "and one put back returns to it"
        assert folded == 5
    finally:
        changes.listens(None)
