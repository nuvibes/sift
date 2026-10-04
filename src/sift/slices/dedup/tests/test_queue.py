# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two duplicate cards as workbench queues: the receipts, and taking a decision back.

Every decision leaves a trace of having been taken, and most can be taken back, which matters
most for "these are different, stop asking": that is the one answer somebody gives quickly and
regrets slowly.

The test that matters most is the last one: a decision that deleted a file is refused by undo. It
would be easy to set the row back to pending and report success, and that would be a lie somebody
only discovers when they go looking for the file.
"""

from __future__ import annotations

from typing import Any

import pytest

from sift.kernel.access import Repository, Viewer
from sift.kernel.content.duplicates import DuplicateReads
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.dedup.grouping import Group
from sift.slices.dedup.queue import (
    NAME,
    PREVIEW_FILES,
    RECLAIM_NAME,
    CarriedAttributions,
    DedupQueue,
    ReclaimQueue,
    _closest,
    _first_files,
)
from sift.slices.dedup.service import RECLAIM_QUEUE, WORKBENCH_QUEUE, DedupService
from sift.slices.dedup.tests.conftest import Library, Recorder
from sift.slices.dedup.tests.test_dedup import dials, set_fingerprint
from sift.slices.workbench.store import Store
from sift.testing.fixtures import FakeClock

pytestmark = pytest.mark.integration

_PHASH = "0f0f0f0f0f0f0f0f"
_NEAR = "0f0f0f0f0f0f0f0e"


class Preferences:
    """The dials at their registered defaults, which is what an untouched install reads."""

    async def get_app(self, key: str) -> Any:
        return {
            "dedup.level": "medium",
            "dedup.max_duration_gap_seconds": 10,
            "dedup.keep": "higher_res",
        }.get(key)

    async def get_user(self, user_id: str, key: str) -> Any:
        return None


@pytest.fixture
async def recorded(temp_db: Database) -> Store:
    await temp_db.initialize_schema()
    return Store(temp_db)


@pytest.fixture
async def reviewed(
    temp_db: Database,
    reads: DuplicateReads,
    recorder: Recorder,
    clock: FakeClock,
    recorded: Store,
) -> DedupService:
    """The service wired to a real record, and to a remover that removes nothing."""
    await temp_db.initialize_schema()
    return DedupService(temp_db, reads, recorder, clock=clock.now, recorder=recorded)


@pytest.fixture
def panel(reviewed: DedupService, access: Repository) -> DedupQueue:
    return DedupQueue(reviewed, access, Preferences())


async def _stills_made(temp_db: Database, *asset_ids: str) -> None:
    """Record a built still for each file, as the picture pass does. A card sends only these."""
    for asset_id in asset_ids:
        await temp_db.execute(
            "INSERT INTO derivatives (id, asset_id, kind, rel_cache_path, params, size_bytes, "
            "created_at) VALUES (?, ?, 'thumb', ?, '{}', 10, 0)",
            (new_id(), asset_id, f"{asset_id}/thumb.jpg"),
        )


async def _one_pair(temp_db: Database, managed: Library, add_file: Any) -> tuple[str, str]:
    first = await add_file(managed, "one.jpg", "accepted.jpg")
    second = await add_file(managed, "two.png", "accepted.png")
    await set_fingerprint(temp_db, first.asset.id, _PHASH)
    await set_fingerprint(temp_db, second.asset.id, _NEAR)
    await _stills_made(temp_db, first.asset.id, second.asset.id)
    return first.asset.id, second.asset.id


async def test_the_queue_registers_under_the_name_its_receipts_carry(panel: DedupQueue) -> None:
    """One name, from one place. A registry entry that disagreed with the receipts would put the
    decisions in the record under a queue the board cannot find, so nothing could draw or undo
    them, and the failure would look like undo being broken rather than like a typo."""
    assert panel.name == NAME == WORKBENCH_QUEUE


async def test_the_card_counts_what_is_waiting(
    temp_db: Database, reviewed: DedupService, panel: DedupQueue, managed: Library, add_file: Any
) -> None:
    admin = await _an_admin(temp_db)
    await _one_pair(temp_db, managed, add_file)
    assert (await panel.survey(admin)).count == 0, "nothing is waiting until something has looked"

    await reviewed.scan()

    survey = await panel.survey(admin)
    assert survey.count == 1, "two files that look alike are ONE question, not one per pair"
    assert survey.title == "Near duplicates"
    # Both files of the group, so the card shows what the question is about.
    assert len(survey.preview) == 2


async def test_the_card_sends_no_still_that_was_never_built(
    temp_db: Database, reviewed: DedupService, panel: DedupQueue, managed: Library, add_file: Any
) -> None:
    """A file whose picture pass has not run, or gave up on it, has nothing at its still's address,
    and a card that sent it drew a blank square. The next file takes its place instead."""
    first = await add_file(managed, "one.jpg", "accepted.jpg")
    second = await add_file(managed, "two.png", "accepted.png")
    await set_fingerprint(temp_db, first.asset.id, _PHASH)
    await set_fingerprint(temp_db, second.asset.id, _NEAR)
    await _stills_made(temp_db, second.asset.id)
    await reviewed.scan()

    survey = await panel.survey(await _an_admin(temp_db))

    assert survey.count == 1, "the question is still asked; only its picture is left out"
    assert [one.id for one in survey.preview] == [second.asset.id]


async def test_a_still_on_the_card_leads_to_the_group_it_belongs_to(
    temp_db: Database, reviewed: DedupService, panel: DedupQueue, managed: Library, add_file: Any
) -> None:
    """Into the decision, not away from it.

    Opening the FILE a still is a picture of is the one thing pressing it cannot usefully mean: what
    the card is about is the group, and one member of it opened on its own page leads away from the
    question.

    A group has no address of its own and cannot be given one (it is computed from the pair table
    at the dials in force), so the address is the queue's own page and a fragment naming the group
    by its method and its smallest file, which is the same name the page keys its rows by.
    """
    first, second = await _one_pair(temp_db, managed, add_file)
    await reviewed.scan()
    (group,) = await reviewed.groups(dials())

    survey = await panel.survey(await _an_admin(temp_db))

    where = f"/organize/{NAME}#group-{group.method}:{group.ids[0]}"
    assert {one.href for one in survey.preview} == {where}
    assert {one.id for one in survey.preview} == {first, second}


async def test_a_still_in_the_RECORD_still_leads_to_the_file_it_is_a_picture_of(
    temp_db: Database,
    reviewed: DedupService,
    panel: DedupQueue,
    recorded: Store,
    managed: Library,
    add_file: Any,
) -> None:
    """The same read, two callers, two right answers, which is why the addresses are passed in.

    A still on the CARD is one member of a group waiting to be judged, so it opens the group. A
    still in the RECORD is a file a decision was already taken about, and the group it was in has
    been settled, so there is nothing to open but the file. The three tests below assert the
    second; this is the one that says the two are deliberately different.
    """
    admin = await _an_admin(temp_db)
    await _one_pair(temp_db, managed, add_file)
    await reviewed.scan()
    (group,) = await reviewed.groups(dials())
    await reviewed.dismiss_group(group, actor=admin)
    (written, _total) = await recorded.recent(limit=1, offset=0)

    shown = await panel.pictures_of(admin, written[0].payload)

    assert all(one.href == f"/asset/{one.id}" for one in shown)
    assert not any("#group-" in (one.href or "") for one in shown)


async def test_a_file_in_two_pairs_is_drawn_once_on_the_card(
    temp_db: Database, reviewed: DedupService, panel: DedupQueue, managed: Library, add_file: Any
) -> None:
    """Three pictures that all look alike make three pairs between them and ONE group.

    A card counting pairs would say three, which is three times the work actually waiting, and it
    would go down by three the moment somebody answered once. Listing both sides of every pair
    would also draw the same file more than once. Grouping prevents both, and this pins both.
    """
    first = await add_file(managed, "one.jpg", "accepted.jpg")
    second = await add_file(managed, "two.png", "accepted.png")
    third = await add_file(managed, "three.webp", "accepted.webp")
    for asset_id, value in (
        (first.asset.id, _PHASH),
        (second.asset.id, _NEAR),
        (third.asset.id, "0f0f0f0f0f0f0f0d"),
    ):
        await set_fingerprint(temp_db, asset_id, value)
    await _stills_made(temp_db, first.asset.id, second.asset.id, third.asset.id)
    await reviewed.scan()

    survey = await panel.survey(await _an_admin(temp_db))

    assert survey.count == 1, "three pictures that all look alike are ONE group, not three pairs"
    assert len(survey.preview) == 3, "and three files, each drawn once"
    assert len({shown.id for shown in survey.preview}) == 3


async def test_judging_a_pair_writes_a_receipt_that_says_what_happened(
    temp_db: Database,
    reviewed: DedupService,
    recorded: Store,
    managed: Library,
    add_file: Any,
) -> None:
    """The sentence is written when the decision is taken, so it describes that moment rather than
    the library as it stands whenever somebody reads it back."""
    await _one_pair(temp_db, managed, add_file)
    await reviewed.scan()
    (group,) = await reviewed.groups(dials())

    await reviewed.dismiss_group(group, actor=await _an_admin(temp_db))

    written, total = await recorded.recent(limit=10, offset=0)
    assert total == 1, "one receipt for the group, never one per pair inside it"
    assert written[0].queue == WORKBENCH_QUEUE
    # The files by NAME, never "These are different": every line says what happened.
    assert written[0].title in {
        "one.jpg and two.png are different",
        "two.png and one.jpg are different",
    }
    assert "will not be raised again" in written[0].detail


async def test_undoing_a_dismissal_puts_the_whole_group_back_in_the_queue(
    temp_db: Database,
    reviewed: DedupService,
    panel: DedupQueue,
    recorded: Store,
    managed: Library,
    add_file: Any,
) -> None:
    """ "These are different" is the answer somebody gives fastest and regrets slowest, so it can
    be changed."""
    admin = await _an_admin(temp_db)
    await _one_pair(temp_db, managed, add_file)
    await reviewed.scan()
    (group,) = await reviewed.groups(dials())
    (candidate_id,) = group.pairs
    await reviewed.dismiss_group(group, actor=admin)
    assert await reviewed.groups(dials()) == []

    (written, _total) = await recorded.recent(limit=1, offset=0)
    assert await panel.reverse(admin, written[0].id, written[0].payload) is True

    assert len(await reviewed.groups(dials())) == 1
    assert (await reviewed.get(candidate_id)).status == "pending"


async def test_a_receipt_from_before_groups_is_still_read_and_still_reversed(
    temp_db: Database,
    reviewed: DedupService,
    panel: DedupQueue,
    managed: Library,
    add_file: Any,
) -> None:
    """A record outlives the version that wrote it, and this queue's records changed shape.

    Every decision taken before the queue reviewed groups named one `candidate_id`, and those rows
    are sitting in every existing library. Undo has to keep reading them or a restored backup (and
    every install that upgrades) loses the ability to take back the decisions it already made.

    So both shapes are read, and this is the OLD one: written by hand, because the code that wrote
    it is gone and a test that produced it with the new code would be proving nothing.
    """
    import json

    admin = await _an_admin(temp_db)
    await _one_pair(temp_db, managed, add_file)
    await reviewed.scan()
    (group,) = await reviewed.groups(dials())
    (candidate_id,) = group.pairs
    await reviewed.dismiss_group(group, actor=admin)

    old = json.dumps({"candidate_id": candidate_id, "removed": None})
    assert await panel.reverse(admin, "receipt-from-before", old) is True
    assert (await reviewed.get(candidate_id)).status == "pending"

    # And the same shape carrying a deletion is refused, which is the half that must not pretend.
    gone = json.dumps({"candidate_id": candidate_id, "removed": group.ids[0]})
    assert await panel.reverse(admin, "receipt-from-before", gone) is False


async def test_undo_refuses_a_decision_that_deleted_a_file(
    temp_db: Database,
    reviewed: DedupService,
    panel: DedupQueue,
    recorded: Store,
    managed: Library,
    add_file: Any,
) -> None:
    """The one that must not pretend.

    Setting the rows back to pending would be easy and would report success, and what it would
    actually produce is a question about files that are gone. Deleting is final; undo says so by
    refusing, and the receipt's own sentence says so too.

    The remover behind this service records rather than removes, so the rows are still here, which
    is what makes the refusal a real test of the RULE rather than of the foreign key.
    """
    admin = await _an_admin(temp_db)
    first, second = await _one_pair(temp_db, managed, add_file)
    await reviewed.scan()
    (group,) = await reviewed.groups(dials())

    await reviewed.settle_group(group, keep=group.ids[0], actor=admin)

    (written, _total) = await recorded.recent(limit=1, offset=0)
    # Which file stayed and which went, by name, read before the delete took the row.
    called = {first: "one.jpg", second: "two.png"}
    kept, gone = group.ids[0], group.ids[1]
    assert written[0].title == f"Kept {called[kept]}, deleted {called[gone]}"
    assert "cannot be undone" in written[0].detail
    assert await panel.reverse(admin, written[0].id, written[0].payload) is False


class _Deletes:
    """A removal seam that really takes the file's row away, as the real deleter does.

    The recording one above leaves every row where it was, so a receipt that read the names AFTER
    the deletes would still find them and read the same, so moving that read would go unnoticed.
    With the row gone, only a name read beforehand survives to be written.
    The statement is the content store's own (`_DELETE_ASSET`); the foreign keys take the rest.
    """

    def __init__(self, database: Database) -> None:
        self._db = database
        self.removed: list[str] = []

    async def remove(self, asset_id: str, **kwargs: object) -> None:
        async with self._db.write() as connection:
            await connection.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        self.removed.append(asset_id)


async def test_the_receipt_names_the_deleted_file_although_its_row_is_gone(
    temp_db: Database,
    reads: DuplicateReads,
    clock: FakeClock,
    recorded: Store,
    managed: Library,
    add_file: Any,
) -> None:
    """ "Kept one.jpg, deleted two.png", with the deleted file's row really gone.

    The names are read before the loop that deletes, because the file about to go takes its name
    with it. Read after, the deleted one would be "a file" and the receipt would say nothing about
    what was thrown away.
    """
    deleting = _Deletes(temp_db)
    service = DedupService(temp_db, reads, deleting, clock=clock.now, recorder=recorded)
    admin = await _an_admin(temp_db)
    first, second = await _one_pair(temp_db, managed, add_file)
    await service.scan()
    (group,) = await service.groups(dials())

    await service.settle_group(group, keep=group.ids[0], actor=admin)

    kept, gone = group.ids[0], group.ids[1]
    assert deleting.removed == [gone]
    async with temp_db.read() as connection:
        cursor = await connection.execute("SELECT id FROM assets WHERE id = ?", (gone,))
        assert await cursor.fetchone() is None, "the double did not delete, so this proves nothing"
    (written, _total) = await recorded.recent(limit=1, offset=0)
    called = {first: "one.jpg", second: "two.png"}
    assert written[0].title == f"Kept {called[kept]}, deleted {called[gone]}"


async def test_a_record_this_version_cannot_read_is_not_reversed(panel: DedupQueue) -> None:
    """A payload from a restored backup or an older release. "Nothing was put back" is honest;
    reaching straight in would fail the request and read as undo being broken."""
    nobody = Viewer(id="01HX0000000000000000000001", role="admin")  # type: ignore[arg-type]

    assert await panel.reverse(nobody, "receipt", "not json at all") is False
    assert await panel.reverse(nobody, "receipt", '["a list"]') is False
    assert await panel.reverse(nobody, "receipt", "{}") is False


async def _an_admin(temp_db: Database) -> Viewer:
    from sift.kernel.access import Role
    from sift.testing.fixtures import create_user

    return await create_user(temp_db, Role.ADMIN)


# --- the branches the happy path never reaches -------------------------------------------------


async def test_the_queue_is_always_available(panel: DedupQueue) -> None:
    """Comparing fingerprints needs nothing switched on and no model downloaded, so the card is
    always on the board, and a zero on it is the honest kind, meaning nothing is left to answer
    rather than that the feature is off."""
    assert await panel.available() is True


async def test_a_receipt_shows_the_two_files_it_was_about(
    temp_db: Database,
    reviewed: DedupService,
    panel: DedupQueue,
    recorded: Store,
    managed: Library,
    add_file: Any,
) -> None:
    """A record saying only that something happened is a record nobody can check."""
    admin = await _an_admin(temp_db)
    first, second = await _one_pair(temp_db, managed, add_file)
    await reviewed.scan()
    (group,) = await reviewed.groups(dials())
    await reviewed.dismiss_group(group, actor=admin)
    (written, _total) = await recorded.recent(limit=1, offset=0)

    shown = await panel.pictures_of(admin, written[0].payload)

    assert {one.id for one in shown} == {first, second}
    assert all(one.href == f"/asset/{one.id}" for one in shown)


async def test_a_receipt_FROM_BEFORE_GROUPS_still_shows_the_pair_it_was_about(
    temp_db: Database,
    reviewed: DedupService,
    panel: DedupQueue,
    managed: Library,
    add_file: Any,
) -> None:
    """The old record shape, drawn rather than only reversed.

    Undo reads both shapes (proved above), and the PICTURES are the other half of the same
    promise: a decision somebody is being offered the chance to take back has to show what it was
    about, or the offer is "undo something". Every row written before the queue reviewed groups is
    this shape, and they are sitting in every existing library.

    Written by hand, for the reason the reversal test gives: the code that wrote them is gone, and
    producing one with the current code would be proving nothing.
    """
    import json

    admin = await _an_admin(temp_db)
    first, second = await _one_pair(temp_db, managed, add_file)
    await reviewed.scan()
    (group,) = await reviewed.groups(dials())
    (candidate_id,) = group.pairs

    shown = await panel.pictures_of(admin, json.dumps({"candidate_id": candidate_id}))

    assert {one.id for one in shown} == {first, second}
    assert all(one.href == f"/asset/{one.id}" for one in shown)


async def test_a_receipt_whose_pair_has_gone_shows_no_pictures(
    temp_db: Database, panel: DedupQueue
) -> None:
    """Ordinary rather than exceptional: deleting either file takes the row with it, and a record
    of having judged the pair outlives the pair. The decision still reads; it just has nothing left
    to draw."""
    import json

    payload = json.dumps({"candidate_id": "01HX0000000000000000000099", "removed": None})

    assert await panel.pictures_of(await _an_admin(temp_db), payload) == ()


async def test_a_record_this_version_cannot_read_shows_no_pictures(
    temp_db: Database, panel: DedupQueue
) -> None:
    """The same tolerance `reverse` has, for the same reason: a payload from a restored backup or
    an older release is unreadable, not an error."""
    admin = await _an_admin(temp_db)

    assert await panel.pictures_of(admin, "not json at all") == ()
    assert await panel.pictures_of(admin, "{}") == ()


async def test_a_card_with_nothing_waiting_asks_the_access_layer_nothing(
    temp_db: Database, panel: DedupQueue
) -> None:
    """An empty library is the state this whole screen is trying to reach, and it must not cost a
    permission query to say so."""
    survey = await panel.survey(await _an_admin(temp_db))

    assert survey.count == 0
    assert survey.preview == ()


async def test_undo_is_refused_for_a_payload_naming_no_pair(
    temp_db: Database, panel: DedupQueue
) -> None:
    """A record carrying `removed` but no candidate. Neither guard alone covers it: the first
    returns early on the missing id, so the deletion check is never reached."""
    import json

    payload = json.dumps({"removed": "01HX0000000000000000000099"})

    assert await panel.reverse(await _an_admin(temp_db), "receipt", payload) is False


# --- the other card: the same file in more than one place ---------------------------------------


@pytest.fixture
def copies(reviewed: DedupService, access: Repository) -> ReclaimQueue:
    return ReclaimQueue(reviewed, access)


async def _two_copies(temp_db: Database, managed: Library, add_file: Any) -> str:
    """One asset, two locations. Identical bytes are one asset by construction, so adding the same
    file twice is all it takes: there is no comparison involved and nothing to scan."""
    first = await add_file(managed, "one.mp4")
    await add_file(managed, "two.mp4")
    await _stills_made(temp_db, first.asset.id)
    return str(first.asset.id)


async def test_the_two_cards_do_not_claim_the_same_name(
    panel: DedupQueue, copies: ReclaimQueue
) -> None:
    """They register into one board, which refuses a name twice, so a copy-paste that left both
    on the same name would take out whichever registered second, on somebody else's install."""
    assert copies.name == RECLAIM_NAME == RECLAIM_QUEUE
    assert copies.name != panel.name


async def test_the_card_counts_the_whole_library_and_draws_a_page_of_it(
    temp_db: Database, copies: ReclaimQueue, managed: Library, add_file: Any
) -> None:
    """The count is a `COUNT` and the pictures are a page.

    They come from two different reads on purpose. On a large library this is thousands of assets,
    and counting them by reading them would be slow, on a card that is also polled on a timer.
    """
    admin = await _an_admin(temp_db)
    await _two_copies(temp_db, managed, add_file)

    survey = await copies.survey(admin)

    assert survey.count == 1
    assert survey.title == "Exact duplicates"
    # One still, not two. A copy is the same picture twice, and drawing it twice would say there
    # is more waiting than there is.
    assert len(survey.preview) == 1


async def test_a_still_on_the_copies_card_leads_to_the_row_it_belongs_to(
    temp_db: Database, copies: ReclaimQueue, managed: Library, add_file: Any
) -> None:
    """The same rule as the near-duplicate card next door, because they are tabs of one screen.

    A row here has no address of its own either: it is ONE asset in several places, on a paged list.
    So the address is that page and a fragment naming the asset, and `CopiesPanel` marks its rows
    with the same words.
    """
    admin = await _an_admin(temp_db)
    await _two_copies(temp_db, managed, add_file)

    survey = await copies.survey(admin)

    (shown,) = survey.preview
    assert shown.href == f"/organize/{RECLAIM_NAME}#copy-{shown.id}"


async def test_the_copies_card_sends_no_still_that_was_never_built(
    temp_db: Database, copies: ReclaimQueue, managed: Library, add_file: Any
) -> None:
    """The same rule as the near-duplicates card: counted, and drawn only where a still exists."""
    first = await add_file(managed, "one.mp4")
    await add_file(managed, "two.mp4")
    admin = await _an_admin(temp_db)

    unbuilt = await copies.survey(admin)
    await _stills_made(temp_db, first.asset.id)
    built = await copies.survey(admin)

    assert unbuilt.count == built.count == 1
    assert unbuilt.preview == ()
    assert [one.id for one in built.preview] == [first.asset.id]


async def test_a_library_with_nothing_stored_twice_says_so_at_zero(
    temp_db: Database, copies: ReclaimQueue, managed: Library, add_file: Any
) -> None:
    """The goal state. A file in one place is a file, not a duplicate."""
    await add_file(managed, "alone.mp4")

    survey = await copies.survey(await _an_admin(temp_db))

    assert survey.count == 0
    assert survey.preview == ()
    assert await copies.available() is True, "and the card is still on the board, saying zero"


async def test_letting_a_copy_go_is_written_into_the_record(
    temp_db: Database,
    reviewed: DedupService,
    recorded: Store,
    managed: Library,
    add_file: Any,
) -> None:
    """Letting a copy go leaves a trace of having happened.

    The sentence names the path that
    went, says how many places the file is still kept in, and says plainly that it cannot be
    undone: written when the decision is taken, so it describes that moment rather than the
    library as it is now.
    """
    asset_id = await _two_copies(temp_db, managed, add_file)
    admin = await _an_admin(temp_db)
    (redundancy,) = await reviewed.reclaim(limit=50, offset=0)
    doomed = redundancy.copies[1]

    await reviewed.release(asset_id, doomed.location_id, actor=admin)

    (written,) = (await recorded.recent(limit=10, offset=0))[0]
    assert written.queue == RECLAIM_QUEUE
    assert doomed.rel_path in written.detail
    assert "cannot be undone" in written.detail
    assert "1 other place" in written.detail


async def test_the_record_points_at_a_file_that_is_still_here(
    temp_db: Database,
    reviewed: DedupService,
    recorded: Store,
    copies: ReclaimQueue,
    managed: Library,
    add_file: Any,
) -> None:
    """Unlike every other deletion on this board, the asset SURVIVES: what went was one of the
    places its bytes sat. So there is a picture to draw, and it is the file itself."""
    asset_id = await _two_copies(temp_db, managed, add_file)
    admin = await _an_admin(temp_db)
    (redundancy,) = await reviewed.reclaim(limit=50, offset=0)
    await reviewed.release(asset_id, redundancy.copies[1].location_id, actor=admin)
    (written,) = (await recorded.recent(limit=10, offset=0))[0]

    shown = await copies.pictures_of(admin, written.payload)

    assert [one.id for one in shown] == [asset_id]


async def test_a_record_this_version_cannot_read_shows_no_picture_rather_than_failing(
    temp_db: Database, copies: ReclaimQueue
) -> None:
    """A payload from an older release, or a restored backup. Reaching straight in would fail the
    request, which reads as the record being broken rather than as unreadable."""
    admin = await _an_admin(temp_db)

    assert await copies.pictures_of(admin, "not json at all") == ()
    assert await copies.pictures_of(admin, '["a list"]') == ()
    assert await copies.pictures_of(admin, '{"location_id": "only-half-of-it"}') == ()


async def test_letting_a_copy_go_cannot_be_taken_back(
    temp_db: Database, copies: ReclaimQueue
) -> None:
    """The bytes at that path are gone. Putting the location row back would point the library at a
    file that is not there, and reporting success would be a lie somebody discovers when they go
    looking for it."""
    payload = (
        '{"asset_id": "01HX000000000000000000000A", "location_id": "01HX000000000000000000000B"}'
    )

    assert await copies.reverse(await _an_admin(temp_db), "receipt", payload) is False


def test_the_cards_pictures_never_draw_one_file_twice_and_stop_at_the_ceiling() -> None:
    """`_first_files` on its own, because the two arms it has cannot be arranged through the
    service cheaply: one needs a file matched by two different fingerprint methods, and the other
    needs more than two dozen files.

    A file can be in more than one group, once per method, so the same still would otherwise appear
    across the card twice, which reads as more waiting than there is. And the card draws a fixed
    number: stopping at the ceiling is what keeps a survey of a library with thousands of groups in
    it from building a list of thousands to hand back two dozen.

    It also says where each still LEADS, and the rule falls out of the same one: a file is kept the
    FIRST time it is seen, so the group it was kept for is the group it belongs to on this card. A
    still pointing at two groups is not a thing a link can be.
    """
    twice = Group(ids=("a", "b"), method="phash", distance=1)
    again = Group(ids=("b", "c"), method="videohash", distance=2)

    kept, leads = _first_files([twice, again])
    assert kept == ("a", "b", "c"), "a file in two groups was drawn twice"
    # `b` is in both and belongs to the first, which is the closer of the two.
    assert leads["a"] == leads["b"] == "/organize/duplicates#group-phash:a"
    assert leads["c"] == "/organize/duplicates#group-videohash:b"

    # Past the ceiling, and it stops gathering rather than gathering everything and slicing.
    many = [
        Group(ids=(f"f{n:03d}", f"g{n:03d}"), method="phash", distance=1)
        for n in range(PREVIEW_FILES)
    ]
    gathered, addresses = _first_files(many)
    assert len(gathered) == PREVIEW_FILES
    assert len(set(gathered)) == PREVIEW_FILES
    # And nothing left over: an address for a file the ceiling cut is an address nothing draws.
    assert set(addresses) == set(gathered)
    assert _first_files([]) == ((), {})

    # A file with no still is passed over and the next one fills its place, up to the ceiling.
    drawable = {one for group in many for one in group.ids if one.startswith("g")}
    only, _where = _first_files(many, drawn=drawable)
    assert set(only) <= drawable
    assert len(only) == PREVIEW_FILES


# --- what a decision says it was about -----------------------------------------------------------


async def _subjects_of(temp_db: Database, decision_id: str) -> set[tuple[str, str]]:
    rows = await temp_db.fetch_all(
        "SELECT kind, subject_id FROM workbench_decision_subjects WHERE decision_id = ?",
        (decision_id,),
    )
    return {(str(row["kind"]), str(row["subject_id"])) for row in rows}


async def test_settling_a_group_says_it_was_about_every_file_in_it(
    temp_db: Database,
    reviewed: DedupService,
    recorded: Store,
    managed: Library,
    add_file: Any,
) -> None:
    """Both files, including one that was deleted.

    The decision is exactly as much a part of the kept file's history as of the deleted one's, and
    the kept file is the one somebody is looking at when they wonder where the other went.
    """
    admin = await _an_admin(temp_db)
    first, second = await _one_pair(temp_db, managed, add_file)
    await reviewed.scan()
    (group,) = await reviewed.groups(dials())

    await reviewed.settle_group(group, keep=group.ids[0], actor=admin)

    (written, _total) = await recorded.recent(limit=1, offset=0)
    assert await _subjects_of(temp_db, written[0].id) == {
        ("asset", first),
        ("asset", second),
    }


async def test_releasing_a_copy_says_it_was_about_the_file_and_not_the_copy(
    temp_db: Database,
    reviewed: DedupService,
    recorded: Store,
    managed: Library,
    add_file: Any,
) -> None:
    """A location is not a thing with a history. The file is, and it is still here: what happened
    to it is that one of the places it sat is no longer one of them."""
    admin = await _an_admin(temp_db)
    first = await add_file(managed, "one.jpg", "accepted.jpg")
    second = await add_file(managed, "copy.jpg", "accepted.jpg")
    assert first.asset.id == second.asset.id, "identical bytes are one asset in two places"
    (entry,) = await reviewed.reclaim(limit=10, offset=0)
    spare = next(one for one in entry.copies if one.rel_path == "copy.jpg")

    await reviewed.release(entry.asset_id, spare.location_id, actor=admin)

    (written, _total) = await recorded.recent(limit=1, offset=0)
    assert await _subjects_of(temp_db, written[0].id) == {("asset", first.asset.id)}


async def test_the_bound_rule_is_the_dials_as_the_statements_bind_them() -> None:
    """One rule of what "near" means, handed outside the slice: the same numbers the queue's own
    statements bind, read from the same dials, never a second copy."""
    from sift.slices.dedup.service import _filter_params, bound_rule, read_dials

    class _Settings:
        async def get_app(self, key: str) -> object:
            return None

    settings = _Settings()
    dials = await read_dials(settings)  # type: ignore[arg-type]

    assert await bound_rule(settings) == _filter_params(  # type: ignore[arg-type]
        dials.level, dials.max_duration_gap_ms
    )
    assert set(await bound_rule(settings)) == {"phash", "video_phash", "videohash", "gap"}  # type: ignore[arg-type]


# --- the card's window, and the carry's record read back -------------------------------------------


def test_the_card_looks_only_at_as_many_close_groups_as_its_stills_need() -> None:
    """The closest groups, stopping at the one that fills the card, so a library of thousands of
    groups costs one visibility read over a page of files, not over all of them."""
    groups = [
        Group(ids=(f"{index:02}a", f"{index:02}b"), method="phash", distance=0)
        for index in range(PREVIEW_FILES)
    ]

    assert _closest(groups) == groups[: PREVIEW_FILES // 2]
    assert _closest(groups[:3]) == groups[:3], "fewer files than the card holds: every group"


class _Sees:
    """The access layer's one question, answered from a fixed set and written down."""

    def __init__(self, *visible: str) -> None:
        self.visible = set(visible)
        self.asked: list[list[str]] = []

    async def visible_of(self, viewer: object, asset_ids: list[str]) -> set[str]:
        self.asked.append(asset_ids)
        return self.visible & set(asset_ids)


class _Uncarries:
    """The service's undo of a carry, writing down what it was handed."""

    def __init__(self) -> None:
        self.handed: list[tuple[str, list[tuple[str, str, str]]]] = []

    async def uncarry(self, *, asset_id: str, carried: list[Any]) -> bool:
        self.handed.append((asset_id, [(one.kind, one.id, one.name) for one in carried]))
        return True


def _carry(asset_id: str, *rows: object) -> str:
    import json

    return json.dumps({"asset_id": asset_id, "from": "01SOURCE", "carried": list(rows)})


async def test_a_carrys_record_shows_the_file_that_gained_it_only_to_somebody_who_may_see_it() -> (
    None
):
    """Scoped rather than trusted from the record: a file restricted since is not drawn."""
    sees = _Sees("01SEEN")
    undo = CarriedAttributions(_Uncarries(), sees)  # type: ignore[arg-type]
    row = {"kind": "person", "id": "01P", "name": "Ada Lumen"}

    shown = await undo.pictures_of(None, _carry("01SEEN", row))  # type: ignore[arg-type]
    assert [(one.id, one.href) for one in shown] == [("01SEEN", "/asset/01SEEN")]
    assert await undo.pictures_of(None, _carry("01HIDDEN", row)) == ()  # type: ignore[arg-type]


async def test_a_carrys_record_naming_no_file_shows_nothing_and_asks_nothing() -> None:
    sees = _Sees("01SEEN")
    undo = CarriedAttributions(_Uncarries(), sees)  # type: ignore[arg-type]

    assert await undo.pictures_of(None, "not json at all") == ()  # type: ignore[arg-type]
    assert await undo.pictures_of(None, '{"carried": []}') == ()  # type: ignore[arg-type]
    assert sees.asked == []


async def test_undoing_a_carry_takes_back_only_the_rows_its_record_names() -> None:
    """A row that is not an object, names no one, or is of a kind a carry never writes is stepped
    over; an older record's `account` is read as a username."""
    service = _Uncarries()
    undo = CarriedAttributions(service, _Sees())  # type: ignore[arg-type]
    payload = _carry(
        "01FILE",
        {"kind": "person", "id": "01P", "name": "Ada Lumen"},
        "not a row",
        {"kind": "person", "id": "", "name": "nobody"},
        {"kind": "site", "id": "01S", "name": "Hollowgrain"},
        {"kind": "account", "id": "01U", "name": "adalumen"},
    )

    assert await undo.reverse(None, "receipt", payload) is True  # type: ignore[arg-type]
    assert service.handed == [
        ("01FILE", [("person", "01P", "Ada Lumen"), ("username", "01U", "adalumen")])
    ]


async def test_a_carrys_record_missing_its_file_or_its_rows_is_not_reversed() -> None:
    """False (nothing was put back), rather than a failed request, and the service is not asked."""
    import json

    service = _Uncarries()
    undo = CarriedAttributions(service, _Sees())  # type: ignore[arg-type]
    row = {"kind": "person", "id": "01P", "name": "Ada Lumen"}

    assert await undo.reverse(None, "r", json.dumps({"carried": [row]})) is False  # type: ignore[arg-type]
    assert await undo.reverse(None, "r", json.dumps({"asset_id": "01F", "carried": "x"})) is False  # type: ignore[arg-type]
    assert await undo.reverse(None, "r", _carry("01F", {"kind": "site", "id": "01S"})) is False  # type: ignore[arg-type]
    assert service.handed == []
