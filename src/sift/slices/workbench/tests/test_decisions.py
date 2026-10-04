# SPDX-License-Identifier: AGPL-3.0-or-later
"""The record of what was decided, and taking one back.

The highest-risk part of the workbench. A decision here writes hundreds of rows from one press, and
the wrong one is usually noticed a day later rather than in ten seconds, so the record has to say
what happened, and undo has to be reachable long after the toast has gone. The record is History
narrowed to Decisions (`/ledger?decisions=true`), read here through its own route.
"""

from __future__ import annotations

import json

import pytest

from sift.kernel.access import Repository, Viewer
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.ledger import Actor, record_event
from sift.kernel.serving import art_version
from sift.kernel.vocabulary import Subject
from sift.kernel.workbench import ASSET, Reversal, Workbench
from sift.slices.workbench.models import LedgerEventView
from sift.slices.workbench.router import ledger
from sift.slices.workbench.service import NotFound, WorkbenchService
from sift.slices.workbench.store import Store
from sift.slices.workbench.tests.conftest import FakeQueue
from sift.testing.fixtures import World


async def record(
    store: Store,
    admin: Viewer,
    *,
    queue: str = "folders",
    payload: str = "{}",
    title: str = "Reya Solberg — 47 files",
) -> str:
    """One receipt.

    `title` is a parameter because the feed FOLDS a run of receipts that say the same thing into
    one line. See `history_feed.presses_recent`. A test about two rows therefore has to write two different
    sentences, which is what two decisions taken about two folders really look like; leaving them
    identical would be a test asserting the ordering of a row that no longer exists.
    """
    async with store.database.write() as connection:
        return await store.record_on(
            connection,
            queue=queue,
            user_id=admin.id,
            title=title,
            detail="47 files filed under Reya Solberg.",
            payload=payload,
        )


async def decided(store: Store, bench: Workbench, admin: Viewer) -> list[LedgerEventView]:
    """History's Decisions, as its route hands it back."""
    page = await ledger(
        database=store.database,
        bench=bench,
        runs=Ledger(store.database),
        viewer=admin,
        decisions=True,
    )
    return page.items


async def stilled(
    store: Store, bench: Workbench, admin: Viewer, access: Repository
) -> list[LedgerEventView]:
    """History's Decisions with its pictures: the route handed the repository it mints tokens from."""
    page = await ledger(
        database=store.database,
        bench=bench,
        runs=Ledger(store.database),
        viewer=admin,
        decisions=True,
        access=access,
    )
    return page.items


def reversible(line: LedgerEventView) -> bool:
    """Whether the line offers its Undo: a receipt, not taken back, from a queue that can."""
    receipt = line.receipt
    return receipt is not None and receipt.reversed_at is None and not receipt.final


async def test_a_decision_is_in_the_record_the_moment_it_is_made(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    workbench.register(FakeQueue(name="folders"))
    await record(store, admin)

    found = await decided(store, workbench, admin)

    assert [one.receipt.title for one in found if one.receipt] == ["Reya Solberg — 47 files"]


async def test_the_record_says_what_the_decision_did(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    """Written when the decision was taken rather than worked out when it is read. A sentence
    assembled later from the current state describes the library as it is now, which is exactly
    what somebody reading a list of past decisions is trying to look behind."""
    await record(store, admin)

    found = await decided(store, workbench, admin)

    assert found[0].receipt is not None
    assert found[0].receipt.detail == "47 files filed under Reya Solberg."


async def test_the_newest_decision_is_first(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    """The ordering is what the screen is for. A bulk decision that went wrong is looked for by
    when it happened, because that is the only thing anybody remembers about it."""
    first = await record(store, admin, title="Reya Solberg — 47 files")
    second = await record(store, admin, title="Rian Fennick — 12 files")

    found = await decided(store, workbench, admin)

    assert [one.id for one in found] == [second, first]


async def test_a_decision_is_reversible_however_long_ago_it_was_made(
    service: WorkbenchService, store: Store, workbench: Workbench, admin: Viewer
) -> None:
    """Not for ten seconds, and not for thirty days. A wrong bulk apply is often noticed the
    following week, and a receipt that has expired by then is a receipt for the wrong problem."""
    queue = FakeQueue(name="folders")
    workbench.register(queue)
    decision_id = await record(store, admin, payload='{"claim_id": "one"}')

    assert (await service.undo(admin, decision_id)).put_back == 1
    assert queue.reversed_with == [(decision_id, '{"claim_id": "one"}')]


async def test_an_undo_rings_the_library_bell_once_and_only_where_something_went_back(
    service: WorkbenchService,
    store: Store,
    workbench: Workbench,
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the bell another tab would keep a taken-back decision's line, and whatever it put
    back, until it reloaded: the mark and some reversers' writes tell nobody. One bell per press,
    a fold's included, and none for an Undo that moved nothing."""
    from sift.slices.workbench import service as service_module

    rung: list[object] = []
    monkeypatch.setattr(service_module, "announce_now", lambda _who, about: rung.append(about))
    workbench.register(FakeQueue(name="folders"))
    first = await record(store, admin)
    second = await record(store, admin)
    third = await record(store, admin)

    await service.undo(admin, first)
    assert len(rung) == 1
    assert await service.undo_each(admin, [second, third]) == 2
    assert len(rung) == 2
    assert await service.undo_each(admin, [second, third]) == 0
    assert len(rung) == 2


async def test_an_undo_that_put_back_only_some_acts_says_how_many(
    service: WorkbenchService, store: Store, workbench: Workbench, admin: Viewer
) -> None:
    """A decision of many acts can go back in part. The answer carries the counts and the reason,
    so the screen never says every one went back when some stayed."""
    partly = Reversal(put_back=2, of=3, said="Put back 2 of 3 things.")
    workbench.register(FakeQueue(name="folders", counts=partly))
    decision_id = await record(store, admin)

    assert await service.undo(admin, decision_id) == partly
    # Taken back, all the same: what went back went back, and a second press finds nothing.
    with pytest.raises(NotFound):
        await service.undo(admin, decision_id)


async def test_the_decisions_that_went_back_along_with_one_read_as_taken_back(
    service: WorkbenchService, store: Store, workbench: Workbench, admin: Viewer
) -> None:
    """A decision that later ones rested on takes them back with it, so each of their lines stops
    offering an Undo that would find nothing left to put back."""
    queue = FakeQueue(name="folders")
    workbench.register(queue)
    rested_on = await record(store, admin, title="Filed under Reya Solberg")
    resting = await record(store, admin, title="Filed under Saskia Verhoeven")
    await record(store, admin, title="Filed under Tobias Ellery")
    queue.counts = Reversal(put_back=1, of=1, along=(resting,))

    await service.undo(admin, rested_on)

    lines = {
        one.receipt.title: reversible(one)
        for one in await decided(store, workbench, admin)
        if one.receipt is not None
    }
    assert lines == {
        "Filed under Reya Solberg": False,
        "Filed under Saskia Verhoeven": False,
        "Filed under Tobias Ellery": True,
    }


async def test_an_undo_where_none_of_many_went_back_leaves_the_decision_standing(
    service: WorkbenchService, store: Store, workbench: Workbench, admin: Viewer
) -> None:
    workbench.register(FakeQueue(name="folders", counts=Reversal(put_back=0, of=3)))
    decision_id = await record(store, admin)

    assert (await service.undo(admin, decision_id)).put_back == 0
    assert (await service.undo(admin, decision_id)).of == 3


async def test_undo_hands_the_queue_the_decision_s_own_record(
    service: WorkbenchService, store: Store, workbench: Workbench, admin: Viewer
) -> None:
    """Not the current state of the library. Reversing from what the decision wrote down is what
    keeps an undo off rows the decision never touched."""
    queue = FakeQueue(name="folders")
    workbench.register(queue)
    decision_id = await record(store, admin, payload='{"attributed": [["a", "p"]]}')

    await service.undo(admin, decision_id)

    assert queue.reversed_with[0][1] == '{"attributed": [["a", "p"]]}'


async def test_a_decision_taken_back_stops_offering_to_be_taken_back(
    service: WorkbenchService, store: Store, workbench: Workbench, admin: Viewer
) -> None:
    workbench.register(FakeQueue(name="folders"))
    decision_id = await record(store, admin)
    await service.undo(admin, decision_id)

    found = await decided(store, workbench, admin)

    assert [reversible(one) for one in found] == [False]


async def test_it_is_kept_rather_than_deleted(
    service: WorkbenchService, store: Store, workbench: Workbench, admin: Viewer
) -> None:
    """The record says what happened. Removing the row on an undo would lose half of it, and the
    half it loses is the part somebody is trying to understand."""
    workbench.register(FakeQueue(name="folders"))
    decision_id = await record(store, admin)

    await service.undo(admin, decision_id)

    found = await decided(store, workbench, admin)
    assert [one.id for one in found] == [decision_id]


async def test_pressing_undo_twice_reverses_once(
    service: WorkbenchService, store: Store, workbench: Workbench, admin: Viewer
) -> None:
    """Which is what a slow request produces, every time somebody presses again rather than waits.
    The second press must not put back a second helping of rows that are already back."""
    queue = FakeQueue(name="folders")
    workbench.register(queue)
    decision_id = await record(store, admin)

    assert (await service.undo(admin, decision_id)).put_back == 1
    with pytest.raises(NotFound, match="already been taken back"):
        await service.undo(admin, decision_id)
    assert len(queue.reversed_with) == 1


async def test_a_reversal_that_put_nothing_back_leaves_it_reversible(
    service: WorkbenchService, store: Store, workbench: Workbench, admin: Viewer
) -> None:
    """Marked as taken back and not actually taken back is the worst of the three outcomes: the
    record would say it had been put right and nothing would have moved."""
    workbench.register(FakeQueue(name="folders", reverses=False))
    decision_id = await record(store, admin)

    assert (await service.undo(admin, decision_id)).put_back == 0

    found = await decided(store, workbench, admin)
    assert reversible(found[0]) is True


async def test_a_reversal_that_fails_halfway_leaves_it_reversible(
    service: WorkbenchService, store: Store, workbench: Workbench, admin: Viewer
) -> None:
    workbench.register(FakeQueue(name="folders", fails=RuntimeError("the disk went away")))
    decision_id = await record(store, admin)

    with pytest.raises(RuntimeError):
        await service.undo(admin, decision_id)

    found = await decided(store, workbench, admin)
    assert reversible(found[0]) is True


async def test_a_decision_nobody_made_is_refused(
    service: WorkbenchService, workbench: Workbench, admin: Viewer
) -> None:
    workbench.register(FakeQueue(name="folders"))

    with pytest.raises(NotFound):
        await service.undo(admin, "01HX0000000000000000000000")


async def test_a_decision_from_a_queue_this_version_lost_says_so(
    service: WorkbenchService, store: Store, admin: Viewer
) -> None:
    """Nothing has gone wrong: the area that made the decision is not in this build, and nothing
    here knows how to put it back. Saying that beats reporting a failure."""
    decision_id = await record(store, admin, queue="something-removed")

    with pytest.raises(NotFound, match="knows how to take that decision back"):
        await service.undo(admin, decision_id)


async def test_the_record_lands_in_the_decision_s_own_transaction(
    store: Store, temp_db: Database, admin: Viewer
) -> None:
    """A receipt written in a separate transaction can be present for a decision that rolled back,
    or missing for one that landed. Both are worse than no record at all, because the record is
    what somebody reaches for once they already know something has gone wrong."""
    with pytest.raises(RuntimeError):
        async with temp_db.write() as connection:
            await store.record_on(
                connection,
                queue="folders",
                user_id=admin.id,
                title="Reya Solberg — 47 files",
                detail="47 files filed under Reya Solberg.",
                payload="{}",
            )
            raise RuntimeError("the decision itself failed")

    assert await decided(store, Workbench(), admin) == []


async def test_a_decision_whose_queue_is_gone_keeps_its_record_and_loses_its_pictures(
    store: Store, workbench: Workbench, admin: Viewer, access: Repository
) -> None:
    """The area that made the decision is not in this build: switched off, or removed by an
    upgrade. What was decided still happened, so the record stays and reads exactly as it did;
    only the pictures, which nothing here knows how to find, are absent."""
    workbench.register(FakeQueue(name="folders"))
    await record(store, admin, queue="something-removed", payload='{"shows": ["asset-1"]}')

    (line,) = await stilled(store, workbench, admin, access)

    assert line.receipt is not None
    assert line.receipt.title == "Reya Solberg — 47 files"
    assert line.still is None


async def test_a_queue_that_cannot_say_what_a_decision_was_about_costs_only_its_pictures(
    store: Store, workbench: Workbench, admin: Viewer, access: Repository
) -> None:
    """A read that fails while drawing a thumbnail must not take the list of decisions with it.

    This screen is where somebody goes after a bulk decision they regret, so it has to open on the
    worst day the library is having. The pictures are the checkable half; undo is the half that
    matters, and it is still there on a record with nothing drawn on it.
    """
    workbench.register(FakeQueue(name="folders", picture_fault=RuntimeError("the store is gone")))
    await record(store, admin, payload='{"shows": ["asset-1"]}')

    (line,) = await stilled(store, workbench, admin, access)

    assert line.receipt is not None
    assert line.receipt.title == "Reya Solberg — 47 files"
    assert line.still is None
    assert reversible(line) is True


# --- a decision that can never be taken back ------------------------------------------------------


async def test_a_queue_whose_decisions_are_final_offers_no_undo(
    workbench: Workbench, store: Store, admin: Viewer
) -> None:
    """An Undo beside a decision whose own sentence says it cannot be undone would answer "there
    was nothing left to put back", which reads as undo being broken rather than as the decision
    being final.

    There is no way to work this out by asking: calling `reverse` to see whether it would succeed
    IS the reversal, so the queue declares it once, the way it declares `pending`.
    """
    workbench.register(FakeQueue(name="quarantine", final=True))
    await record(store, admin, queue="quarantine")

    (shown,) = await decided(store, workbench, admin)

    assert reversible(shown) is False


async def test_an_ordinary_queue_still_offers_it(
    workbench: Workbench, store: Store, admin: Viewer
) -> None:
    """The known positive. Without it the assertion above passes with `reversible` hard-wired to
    False for everything, which would take undo off the whole board."""
    workbench.register(FakeQueue(name="folders"))
    await record(store, admin, queue="folders")

    (shown,) = await decided(store, workbench, admin)

    assert reversible(shown) is True


async def test_a_decision_from_a_queue_this_build_does_not_have_still_offers_it(
    store: Store, workbench: Workbench, admin: Viewer
) -> None:
    """Deliberate, and the opposite of what looks tidy.

    Nothing is registered under that name, so nothing can say whether it was final. Undo stays on
    offer and the service refuses it with a sentence saying nothing in this version knows how to
    take it back, which is true. Greying it out would claim the decision itself was irreversible,
    which is a different statement and one nothing here can make.
    """
    await record(store, admin, queue="from-a-later-version")

    (shown,) = await decided(store, workbench, admin)

    assert reversible(shown) is True


async def test_an_event_that_is_not_a_receipt_is_no_part_of_the_record(
    store: Store, temp_db: Database, workbench: Workbench, admin: Viewer
) -> None:
    """THE RECORD IS THE RECEIPTS, and the ledger's other rows live in the same table.

    `workbench_decisions` holds a second kind of row because the ledger is built on it: an event
    carries a verb and an object and no queue, no title and no detail, because it is a thing that
    happened rather than a judgement anybody can take back. History's narrowing to decisions leaves those
    out.

    Without the exclusion the pager counts two where one press happened (a face scan that gives a
    person their first cover picture writes both), and the record draws a line with no receipt
    beside it.
    """
    receipt = await record(store, admin)
    async with temp_db.write() as connection:
        await record_event(
            connection,
            actor=Actor.user(admin.id),
            verb="edited",
            subject=Subject(kind="asset", id="01HX0000000000000000000B01", name="beach-walk.mp4"),
        )

    assert [one.id for one in await decided(store, workbench, admin)] == [receipt]


class _SaysWhatUndoLeft(FakeQueue):
    """A queue that words what taking one of its decisions back leaves true."""

    taken_back = "Taken back, so these can come up again."


async def test_an_undone_row_stops_saying_what_the_decision_promised(
    service: WorkbenchService, store: Store, workbench: Workbench, admin: Viewer
) -> None:
    """ "Undone" beside "these will not be raised again" says two opposite things. Once the decision
    is back, the row says what is true now (the area's own sentence) while the receipt keeps
    the sentence the decision wrote."""
    workbench.register(_SaysWhatUndoLeft(name="folders"))
    decision_id = await record(store, admin)

    (line,) = await decided(store, workbench, admin)
    assert line.receipt is not None and line.receipt.taken_back is None

    await service.undo(admin, decision_id)
    (line,) = await decided(store, workbench, admin)

    assert line.receipt is not None
    assert line.receipt.taken_back == "Taken back, so these can come up again."
    assert line.receipt.detail == "47 files filed under Reya Solberg."


async def test_an_undone_row_from_an_area_with_no_sentence_says_only_that_it_was_taken_back(
    service: WorkbenchService, store: Store, workbench: Workbench, admin: Viewer
) -> None:
    workbench.register(FakeQueue(name="folders"))
    decision_id = await record(store, admin)
    await service.undo(admin, decision_id)

    (line,) = await decided(store, workbench, admin)

    assert line.receipt is not None and line.receipt.taken_back == "Taken back."


def test_the_duplicates_queue_says_the_pairs_can_come_up_again() -> None:
    from sift.slices.dedup.queue import DedupQueue

    assert "come up as duplicates again" in DedupQueue.taken_back


# --- the picture a decision's line carries ----------------------------------------------------------


async def test_a_decision_line_carries_the_picture_its_area_draws_for_it(
    store: Store, workbench: Workbench, admin: Viewer, access: Repository, world: World
) -> None:
    """A record saying only that something happened is a record nobody can check. The shell has no
    idea what a payload names, so it asks the queue that wrote the decision, from the payload the
    decision wrote about itself, and hands the first picture this viewer may see, with its token."""
    await store.database.execute(
        "INSERT INTO derivatives (id, asset_id, kind, rel_cache_path, params, content_hash,"
        " created_at) VALUES (?, ?, 'thumb', 'a/b.jpg', '{}', 'digest-of-the-still', 0)",
        (new_id(), world.solo),
    )
    queue = FakeQueue(name="folders")
    workbench.register(queue)
    await record(store, admin, payload=json.dumps({"shows": [world.solo]}))

    (line,) = await stilled(store, workbench, admin, access)

    assert line.still is not None
    assert (line.still.kind, line.still.id) == (ASSET, world.solo)
    assert line.still.art == art_version("thumb:digest-of-the-still", admin.cache_stamp)
    assert queue.pictured_from == [json.dumps({"shows": [world.solo]})]


async def test_a_line_on_the_whole_feed_asks_for_no_picture(
    store: Store, workbench: Workbench, admin: Viewer, access: Repository, world: World
) -> None:
    """Only the Decisions narrowing draws them: the whole feed keeps one line's height and asks
    no area about anything."""
    queue = FakeQueue(name="folders")
    workbench.register(queue)
    await record(store, admin, payload=json.dumps({"shows": [world.solo]}))

    page = await ledger(
        database=store.database,
        bench=workbench,
        runs=Ledger(store.database),
        viewer=admin,
        access=access,
    )

    assert [one.still for one in page.items if one.receipt] == [None]
    assert queue.pictured_from == []
