# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one door everything is written through, and the two promises it makes.

The RULES are asserted against a bare database, because what is being asked is whether the door
refuses what it says it refuses: a verb it has no word for, a list of subjects long enough to be
a pass, a count beside seven subjects. A real library would make those answers depend on two things
together.

The PROMISE is asserted against real rows: a person deleted, and the event that named them read
back afterwards with the name it wrote down. That is the half worth having, because "an event
outlives its subject" is a claim about what the database does rather than about what this module
intends, and the only thing that turns it into a fact is deleting the row and looking.
"""

from __future__ import annotations

import pytest

# Imported for its side effect: the workbench slice registers the schema component that creates
# `workbench_decisions`, which is the table this door writes to. A kernel database has it only
# once that registration has happened, and `temp_db.initialize_schema()` creates what is
# registered rather than everything the tree defines, so without this line the table's presence
# depends on whether some other test in the same worker happened to import the slice first.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository
from sift.kernel.db import Database
from sift.kernel.ledger import (
    MOST_SUBJECTS,
    NAMED_KINDS,
    SIFT_WITHOUT_A_TASK,
    Actor,
    LedgerError,
    Object,
    Reversal,
    record_event,
)
from sift.kernel.vocabulary import LEDGER_QUEUE, Subject
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.unit

_PERSON_NAME = "Ilva Brennan"


async def _write(database: Database, **named: object) -> str:
    async with database.write() as connection:
        return await record_event(connection, **named)  # type: ignore[arg-type]


async def _row(database: Database, event_id: str) -> dict[str, object]:
    row = await database.fetch_one("SELECT * FROM workbench_decisions WHERE id = ?", (event_id,))
    assert row is not None
    return dict(row)


async def _subjects(database: Database, event_id: str) -> list[tuple[str, str, str | None]]:
    rows = await database.fetch_all(
        "SELECT kind, subject_id, name FROM workbench_decision_subjects"
        " WHERE decision_id = ? ORDER BY kind, subject_id",
        (event_id,),
    )
    return [
        (
            str(row["kind"]),
            str(row["subject_id"]),
            None if row["name"] is None else str(row["name"]),
        )
        for row in rows
    ]


# --- what the door writes -------------------------------------------------------------------


async def test_an_event_carries_its_verb_object_actor_and_the_names_at_the_time(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The whole row, because every column here exists to answer a question the record is asked."""
    event = await _write(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="linked",
        subject=Subject(kind="asset", id=world.solo, name="beach-walk.mp4"),
        object=Object(kind="person", id=world.person, name=_PERSON_NAME),
    )

    row = await _row(temp_db, event)
    assert row["verb"] == "linked"
    assert row["actor_kind"] == "user" and row["actor_id"] == actors.admin.id
    assert row["object_kind"] == "person" and row["object_id"] == world.person
    assert row["object_name"] == _PERSON_NAME
    assert await _subjects(temp_db, event) == [("asset", world.solo, "beach-walk.mp4")]


async def test_a_box_that_answered_is_written_as_the_actor_by_its_id(
    temp_db: Database, access: Repository, world: World
) -> None:
    """A stash-box filling a record is the one who acted, and the row says which box."""
    event = await _write(
        temp_db,
        actor=Actor.box("box-1"),
        verb="linked",
        subject=Subject(kind="asset", id=world.solo),
    )

    row = await _row(temp_db, event)
    assert row["actor_kind"] == "box" and row["actor_id"] == "box-1"


async def test_an_event_that_is_not_a_judgement_is_filed_under_a_queue_nothing_can_claim(
    temp_db: Database, access: Repository, world: World
) -> None:
    """A grant revoked came off no card, so undo must find nothing that offers to put it back."""
    event = await _write(
        temp_db,
        actor=Actor.sift("faces"),
        verb="unshared",
        subject=Subject(kind="asset", id=world.solo),
    )

    row = await _row(temp_db, event)
    assert row["queue"] == LEDGER_QUEUE
    assert row["title"] == "" and row["detail"] == ""
    # The sentence is the reader's, from the verb and the names. See the module docstring in
    # kernel/ledger.py for why it is not written here.


async def test_a_receipt_keeps_the_sentence_the_queue_wrote(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    event = await _write(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="decided",
        subject=Subject(kind="asset", id=world.solo),
        payload='{"kind": "folder"}',
        receipt=Reversal(queue="folders", title="47 files", detail="47 files were filed."),
    )

    row = await _row(temp_db, event)
    assert row["queue"] == "folders"
    assert row["title"] == "47 files" and row["payload"] == '{"kind": "folder"}'


# --- what the door refuses ------------------------------------------------------------------


async def test_a_verb_the_ledger_has_no_word_for_is_refused(
    temp_db: Database, access: Repository, world: World
) -> None:
    """The closed list is the whole reason a reader can group and filter this record."""
    with pytest.raises(LedgerError, match="not a verb"):
        await _write(
            temp_db,
            actor=Actor.sift("folder"),
            verb="unlinkified",
            subject=Subject(kind="asset", id=world.solo),
        )


async def test_an_actor_of_no_known_kind_is_refused(
    temp_db: Database, access: Repository, world: World
) -> None:
    with pytest.raises(LedgerError, match="sift, user or box"):
        await _write(
            temp_db,
            actor=Actor("robot", "x"),
            verb="added",
            subject=Subject(kind="asset", id=world.solo),
        )


async def test_a_pass_this_build_has_no_word_for_is_refused(
    temp_db: Database, access: Repository, world: World
) -> None:
    """The pass names are the SAME vocabulary a row's provenance uses, or the two drift."""
    with pytest.raises(LedgerError, match="MADE_VIAS"):
        await _write(
            temp_db,
            actor=Actor.sift("guesswork"),
            verb="added",
            subject=Subject(kind="asset", id=world.solo),
        )


async def test_a_user_or_a_box_has_to_say_which_one(
    temp_db: Database, access: Repository, world: World
) -> None:
    with pytest.raises(LedgerError, match="which one"):
        await _write(
            temp_db,
            actor=Actor("user"),
            verb="added",
            subject=Subject(kind="asset", id=world.solo),
        )


async def test_an_event_about_nothing_is_refused(temp_db: Database, access: Repository) -> None:
    with pytest.raises(LedgerError, match="about nothing"):
        await _write(temp_db, actor=Actor.sift("fingerprint"), verb="scanned", subject=[])


async def test_a_receipt_about_nothing_is_allowed(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """A quarantined file was never imported, so there is no row anywhere for it to name."""
    event = await _write(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="decided",
        subject=[],
        receipt=Reversal(queue="quarantine", title="Skipped", detail="It was skipped."),
    )

    assert await _subjects(temp_db, event) == []


async def test_a_list_long_enough_to_be_a_pass_is_refused(
    temp_db: Database, access: Repository, world: World
) -> None:
    """The one-per-file rule. A pass that wrote a row per file would be the largest table here."""
    many = [Subject(kind="asset", id=f"01HX000000000000000000{n:04d}") for n in range(20)]
    assert len(many) > MOST_SUBJECTS

    with pytest.raises(LedgerError, match="one event per file"):
        await _write(temp_db, actor=Actor.sift("folder"), verb="named", subject=many)


async def test_a_receipt_may_name_as_many_subjects_as_its_undo_needs(
    temp_db: Database, access: Repository, actors: Actors
) -> None:
    """The exception, and the reason it is not a loophole.

    A receipt's subjects are the rows its undo has to put back. Capping THAT would make undo wrong,
    which is a different thing from a provenance row somebody forgot to fold into a count.
    """
    # Named, because none of these ids is a row the door could name them from: a receipt is held to
    # the naming rule like any other event.
    many = [
        Subject(kind="asset", id=f"01HX000000000000000000{n:04d}", name=f"clip-{n}.mp4")
        for n in range(20)
    ]

    event = await _write(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="decided",
        subject=many,
        receipt=Reversal(queue="folders", title="20 files", detail="20 files were filed."),
    )

    assert len(await _subjects(temp_db, event)) == 20


async def test_a_pass_hands_a_count_and_one_subject(
    temp_db: Database, access: Repository, world: World
) -> None:
    """One event with a number on it, and the folder it ran over. Never thousands of rows."""
    event = await _write(
        temp_db,
        actor=Actor.sift("folder"),
        verb="named",
        subject=Subject(kind="folder", id=world.leaf, name="leaf"),
        object=Object(kind="person", id=world.person, name=_PERSON_NAME),
        count=7726,
    )

    assert (await _row(temp_db, event))["touched"] == 7726


async def test_a_count_beside_several_subjects_is_refused(
    temp_db: Database, access: Repository, world: World
) -> None:
    """A pass that can afford to name each file does not need the count, so the two are exclusive."""
    with pytest.raises(LedgerError, match="at most one subject"):
        await _write(
            temp_db,
            actor=Actor.sift("folder"),
            verb="named",
            subject=[
                Subject(kind="asset", id=world.solo),
                Subject(kind="asset", id=world.twin),
            ],
            count=2,
        )


async def test_a_count_of_nothing_is_refused(
    temp_db: Database, access: Repository, world: World
) -> None:
    with pytest.raises(LedgerError, match="not a pass that ran"):
        await _write(
            temp_db,
            actor=Actor.sift("folder"),
            verb="named",
            subject=Subject(kind="folder", id=world.leaf),
            count=0,
        )


# --- the promise ------------------------------------------------------------------------------


async def test_an_event_outlives_its_subject(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """Delete the person; the event is still there and still says who it was about.

    THE reason the ledger exists. Everything about a file cascading away with it would erase exactly
    the question (what did I remove this year) somebody opens the record to ask.
    """
    event = await _write(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="removed",
        subject=Subject(kind="person", id=world.person, name=_PERSON_NAME),
        object=Object(kind="asset", id=world.solo, name="beach-walk.mp4"),
    )

    await temp_db.execute("DELETE FROM people WHERE id = ?", (world.person,))

    assert await _subjects(temp_db, event) == [("person", world.person, _PERSON_NAME)]
    assert (await _row(temp_db, event))["verb"] == "removed"


async def test_an_event_outlives_the_user_that_took_it(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """`user_id` is a key and goes NULL; `actor_id` is a snapshot and does not.

    Two columns for one question, and this is the whole of why: the key is what the record screen
    and the pace measurement already read, and the snapshot is what survives.
    """
    event = await _write(
        temp_db,
        actor=Actor.user(actors.guest.id),
        verb="shared",
        subject=Subject(kind="asset", id=world.solo),
    )

    await temp_db.execute("DELETE FROM users WHERE id = ?", (actors.guest.id,))

    row = await _row(temp_db, event)
    assert row["user_id"] is None
    assert row["actor_id"] == actors.guest.id and row["actor_kind"] == "user"


# --- the recording rules: every named thing named, every act of Sift's said by which task --------


async def test_a_name_the_writer_did_not_hand_is_read_from_the_row_as_it_stands(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The durable half of "never 'a file'": the name is written down at the moment of the act.

    A reader that looks the name up later can only ever say what it is called NOW, and nothing at
    all once it is gone. So the door fills the name in the act's own transaction.
    """
    row = await temp_db.fetch_one("SELECT name FROM people WHERE id = ?", (world.person,))
    assert row is not None
    event = await _write(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="linked",
        subject=Subject(kind="asset", id=world.solo, name="beach-walk.mp4"),
        object=Object(kind="person", id=world.person),
    )

    await temp_db.execute("DELETE FROM people WHERE id = ?", (world.person,))
    assert (await _row(temp_db, event))["object_name"] == str(row["name"])


async def test_a_file_is_named_in_the_file_pages_order(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """Its title, then the name on disk now, then the name it arrived under: the order the file
    page reads. The imported name first would have a file renamed on disk kept being called by a
    name the person no longer sees anywhere."""
    await temp_db.execute(
        "UPDATE assets SET title = NULL, original_filename = 'imported-name.mp4' WHERE id = ?",
        (world.solo,),
    )
    await temp_db.execute(
        "UPDATE asset_locations SET filename = 'on-disk-now.mp4', status = 'present'"
        " WHERE asset_id = ?",
        (world.solo,),
    )

    async def named() -> str | None:
        event = await _write(
            temp_db,
            actor=Actor.user(actors.admin.id),
            verb="saved",
            subject=Subject(kind="asset", id=world.solo),
        )
        return (await _subjects(temp_db, event))[0][2]

    assert await named() == "on-disk-now.mp4"
    await temp_db.execute("UPDATE assets SET title = 'Harbor walk' WHERE id = ?", (world.solo,))
    assert await named() == "Harbor walk"
    await temp_db.execute("UPDATE assets SET title = '' WHERE id = ?", (world.solo,))
    await temp_db.execute(
        "UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?", (world.solo,)
    )
    assert await named() == "imported-name.mp4"
    # Nothing names it at all, which the schema allows: the row is there, so the act is recorded
    # with the name it has (none) rather than refused as if the file had gone.
    await temp_db.execute("UPDATE assets SET original_filename = NULL WHERE id = ?", (world.solo,))
    assert not await named()


async def test_a_name_the_writer_handed_is_kept_rather_than_looked_up(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """A writer may be recording what something WAS called; a lookup would overwrite it."""
    event = await _write(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="renamed",
        subject=Subject(kind="person", id=world.person, name="Its old name"),
    )

    assert await _subjects(temp_db, event) == [("person", world.person, "Its old name")]


@pytest.mark.parametrize("kind", sorted(NAMED_KINDS))
async def test_a_named_thing_that_is_gone_and_was_not_named_is_refused(
    temp_db: Database, access: Repository, actors: Actors, kind: str
) -> None:
    """The writer recorded after the row went and kept no name: nothing can ever name it now."""
    with pytest.raises(LedgerError, match="has no name to record"):
        await _write(
            temp_db,
            actor=Actor.user(actors.admin.id),
            verb="deleted",
            subject=Subject(kind=kind, id="01HX0000000000000000000GONE"),  # type: ignore[arg-type]
        )


async def test_a_gone_thing_as_the_object_is_refused_as_well(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    with pytest.raises(LedgerError, match="has no name to record"):
        await _write(
            temp_db,
            actor=Actor.user(actors.admin.id),
            verb="linked",
            subject=Subject(kind="asset", id=world.solo, name="beach-walk.mp4"),
            object=Object(kind="tag", id="01HX0000000000000000000GONE"),
        )


async def test_sift_that_does_not_say_which_task_is_refused(
    temp_db: Database, access: Repository, world: World
) -> None:
    """ "Sift" and nothing else would be nearly every act a library records. The task is always
    known where the act happens, so the door asks for it."""
    with pytest.raises(LedgerError, match="did not say which task"):
        await _write(
            temp_db,
            actor=Actor.sift(),
            verb="linked",
            subject=Subject(kind="asset", id=world.solo, name="beach-walk.mp4"),
        )


async def test_a_task_that_makes_no_row_has_a_word_the_door_takes(
    temp_db: Database, access: Repository, world: World
) -> None:
    event = await _write(
        temp_db,
        actor=Actor.sift("fingerprint"),
        verb="scanned",
        subject=Subject(kind="asset", id=world.solo, name="beach-walk.mp4"),
    )

    assert (await _row(temp_db, event))["actor_id"] == "fingerprint"


async def test_the_acts_allowed_no_task_each_say_why(temp_db: Database, access: Repository) -> None:
    """The allow-list is short and every entry is a reason, not a name: see `SIFT_WITHOUT_A_TASK`."""
    assert set(SIFT_WITHOUT_A_TASK) == {"ran"}
    assert all(len(reason.split()) >= 6 for reason in SIFT_WITHOUT_A_TASK.values())
    event = await _write(
        temp_db,
        actor=Actor.sift(),
        verb="ran",
        subject=Subject(kind="run", id="01HX0000000000000000000RUN", name="Scan"),
    )
    assert (await _row(temp_db, event))["actor_id"] is None
