# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ledger read back, and the vault applied to it.

The thing being asserted is not that a permission check exists: it is that the check is the
STORED VERDICT rather than the assumption every other history read makes. `history_of_asset` is
unscoped and safe because its route resolved the file first; a feed over the whole install has no
route and no subject, and an event about one file can name a second one nobody resolved.

So every test here is about an event somebody must NOT be shown, and each one is a different way of
not being shown it: the file is not theirs, the file is concealed from them, the file it names on
the side is not theirs. The gate at the foot is the other half: it reads the statements and
refuses one that does not join the verdict at all, because a read added later cannot be caught by
a test that was written before it.
"""

from __future__ import annotations

import pytest

# REGISTERING THE TABLES THE RECORD OWNS, so a database built here has them. The same line, for the
# same reason, as `test_history_ledger.py` beside it: the events live in the workbench slice's two
# tables and nothing else in this file imports that slice. Without it the file passes in the suite,
# where something else has imported the slice first, and fails on a missing table run on its own.
# A suite that only passes in company reports the wrong thing to whoever runs one file.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository
from sift.kernel.access.history_events import (
    _OF_ASSET,
    _OF_ENTITY,
    _RECENT,
    Thing,
    events_of_asset,
    events_of_entity,
    events_recent,
    subjects_of,
)
from sift.kernel.access.viewer import Viewer
from sift.kernel.db import Database
from sift.kernel.ledger import Actor, Object, record_event
from sift.kernel.vocabulary import Subject
from sift.testing.fixtures import Actors, World, hide

pytestmark = pytest.mark.unit

_PERSON_NAME = "Ilva Brennan"


async def _event(database: Database, **named: object) -> str:
    async with database.write() as connection:
        return await record_event(connection, **named)  # type: ignore[arg-type]


def _unlocked(viewer: Viewer) -> Viewer:
    """The same user with the vault open, which is a fact about a session and not a permission."""
    return Viewer(id=viewer.id, role=viewer.role, show_hidden=True, concealment=viewer.concealment)


async def test_a_file_s_own_events_come_back_newest_first(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    older = await _event(
        temp_db,
        actor=Actor.sift("folder"),
        verb="added",
        subject=Subject(kind="asset", id=world.solo, name="beach-walk.mp4"),
    )
    newer = await _event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="linked",
        subject=Subject(kind="asset", id=world.solo),
        object=Object(kind="person", id=world.person, name=_PERSON_NAME),
    )

    found = await events_of_asset(temp_db, actors.admin, world.solo)

    assert [one.id for one in found] == [newer, older]
    assert found[0].object is not None and found[0].object.name == _PERSON_NAME
    assert found[1].verb == "added"


async def test_an_event_about_a_file_the_user_may_not_see_is_not_shown(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The guest has no verdict row for it, so the event does not exist as far as they are told."""
    await _event(
        temp_db,
        actor=Actor.sift("folder"),
        verb="added",
        subject=Subject(kind="asset", id=world.solo),
    )

    assert await events_of_asset(temp_db, actors.guest, world.solo) == []
    assert await events_of_asset(temp_db, actors.admin, world.solo) != []


async def test_a_concealed_file_s_events_are_gone_until_the_vault_is_open(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The same rule every wall applies, read off the same column. Absent, not greyed out."""
    await _event(
        temp_db,
        actor=Actor.sift("folder"),
        verb="added",
        subject=Subject(kind="asset", id=world.solo),
    )
    await hide(temp_db, "asset", world.solo, actors.admin.id)

    assert await events_of_asset(temp_db, actors.admin, world.solo) == []
    assert await events_of_asset(temp_db, _unlocked(actors.admin), world.solo) != []


async def test_an_event_naming_a_second_file_is_withheld_whole(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """THE reason this read needed a rule of its own.

    A merge, a copy or a pass names more than one file, and the second one is the thing a history
    can reveal that somebody is not entitled to know exists. The event is withheld rather than
    trimmed: a sentence with one of its two files removed says something that did not happen.
    """
    await _event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="produced",
        subject=[
            Subject(kind="asset", id=world.solo),
            Subject(kind="asset", id=world.twin),
        ],
    )
    await hide(temp_db, "asset", world.twin, actors.admin.id)

    assert await events_of_asset(temp_db, actors.admin, world.solo) == []
    assert await events_of_asset(temp_db, _unlocked(actors.admin), world.solo) != []


async def test_an_event_whose_object_is_a_concealed_file_is_withheld_too(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The object is a file the event names as surely as a subject is."""
    await _event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="produced",
        subject=Subject(kind="asset", id=world.solo),
        object=Object(kind="asset", id=world.twin, name="twin.mp4"),
    )
    await hide(temp_db, "asset", world.twin, actors.admin.id)

    assert await events_of_asset(temp_db, actors.admin, world.solo) == []


async def test_an_event_naming_no_file_at_all_is_shown(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """A person renamed names no file, so there is nothing for the verdict to refuse."""
    await _event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="renamed",
        subject=Subject(kind="person", id=world.person, name=_PERSON_NAME),
    )

    found = await events_of_entity(temp_db, actors.guest, "person", world.person)

    assert [one.verb for one in found] == ["renamed"]


async def test_an_entity_s_events_are_scoped_by_the_files_they_name(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await _event(
        temp_db,
        actor=Actor.sift("faces"),
        verb="linked",
        subject=[
            Subject(kind="person", id=world.person, name=_PERSON_NAME),
            Subject(kind="asset", id=world.solo),
        ],
    )

    assert await events_of_entity(temp_db, actors.admin, "person", world.person) != []
    assert await events_of_entity(temp_db, actors.guest, "person", world.person) == []


async def test_a_merge_is_on_the_page_of_the_person_who_was_KEPT(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The object side. A merge names the person who WENT as its subject and the one who was kept
    as its object, so read from the subject side alone it would appear on the page of somebody who
    no
    longer exists and on no page at all for the person it actually happened to."""
    await _event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="merged",
        subject=Subject(kind="person", id="went-away", name="Nuvella Brink"),
        object=Object(kind="person", id=world.person, name=_PERSON_NAME),
    )

    kept = await events_of_entity(temp_db, actors.admin, "person", world.person)

    assert [(one.verb, None if one.object is None else one.object.name) for one in kept] == [
        ("merged", _PERSON_NAME)
    ]


async def test_a_file_chosen_as_a_cover_says_so_on_its_own_pane(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The same widening for a file: the person is the subject of choosing a cover and the still is
    the object, so the file's own pane could not say it had been chosen as one."""
    await _event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="edited",
        subject=Subject(kind="person", id=world.person, name=_PERSON_NAME),
        object=Object(kind="asset", id=world.solo, name="beach-walk.mp4"),
    )

    assert [one.verb for one in await events_of_asset(temp_db, actors.admin, world.solo)] == [
        "edited"
    ]


async def test_an_event_naming_one_thing_twice_is_one_row(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """Both arms of the union match it. `UNION` folds the duplicate; `UNION ALL` would draw the
    same act twice on one page, which reads as it having happened twice."""
    await _event(
        temp_db,
        actor=Actor.sift("faces"),
        verb="linked",
        subject=Subject(kind="asset", id=world.solo, name="beach-walk.mp4"),
        object=Object(kind="asset", id=world.solo, name="beach-walk.mp4"),
    )

    assert len(await events_of_asset(temp_db, actors.admin, world.solo)) == 1


async def test_the_whole_install_feed_filters_the_same_way_and_says_what_each_was_about(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The feed resolves nothing before it reads, which is what makes the filter load-bearing."""
    await _event(
        temp_db,
        actor=Actor.sift("folder"),
        verb="added",
        subject=Subject(kind="asset", id=world.solo, name="beach-walk.mp4"),
    )

    for_admin = await events_recent(temp_db, actors.admin)

    assert [one.verb for one in for_admin] == ["added"]
    assert for_admin[0].subjects == (Thing(kind="asset", id=world.solo, name="beach-walk.mp4"),)
    assert await events_recent(temp_db, actors.guest) == []


async def test_a_deleted_file_s_events_stay_for_an_admin_and_for_nobody_else(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The one place this is looser than a wall, and the reason it has to be.

    A deleted file has no verdict row for anybody, so a strict reading would hide exactly the
    events the ledger was built to keep: what was removed, and when. An admin already sees the
    whole library and learns nothing new; a guest is told nothing about a file that is gone.
    """
    await _event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="deleted",
        subject=Subject(kind="asset", id=world.solo, name="beach-walk.mp4"),
    )
    await temp_db.execute("DELETE FROM assets WHERE id = ?", (world.solo,))

    assert [one.verb for one in await events_recent(temp_db, actors.admin)] == ["deleted"]
    assert await events_recent(temp_db, actors.guest) == []


async def test_a_subject_with_no_snapshot_is_named_now_and_a_gone_one_stays_nameless(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """A row written before names were snapshotted names its subject as it is called NOW, for
    every reader of `subjects_of`, so the Collection's own page does not say "You added a file to
    it" about a file the feed beside it names. A subject that has gone
    stays nameless: there is nothing to read, and the reader says it has gone."""
    event = await _event(
        temp_db,
        actor=Actor.user(actors.admin.id),
        verb="linked",
        subject=[
            Subject(kind="asset", id=world.solo, name="kept-name.mp4"),
            Subject(kind="asset", id=world.twin, name="old-name.mp4"),
            Subject(kind="asset", id="01GONEGONEGONEGONEGONEGONE", name="went.mp4"),
        ],
        object=Object(kind="person", id=world.person, name=_PERSON_NAME),
    )
    async with temp_db.write() as connection:
        await connection.execute(
            "UPDATE workbench_decision_subjects SET name = NULL"
            " WHERE decision_id = ? AND subject_id <> ?",
            (event, world.solo),
        )
        await connection.execute(
            "UPDATE assets SET title = 'new-name.mp4' WHERE id = ?", (world.twin,)
        )

    named = {one.id: one.name for one in (await subjects_of(temp_db, [event]))[event]}

    assert named == {
        world.solo: "kept-name.mp4",
        world.twin: "new-name.mp4",
        "01GONEGONEGONEGONEGONEGONE": None,
    }


async def test_a_page_past_the_end_is_empty_rather_than_an_error(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await _event(
        temp_db,
        actor=Actor.sift("folder"),
        verb="added",
        subject=Subject(kind="asset", id=world.solo),
    )

    assert await events_recent(temp_db, actors.admin, limit=10, offset=50) == []
    # And a limit nobody should be allowed to ask for is capped rather than refused.
    assert len(await events_recent(temp_db, actors.admin, limit=100_000)) == 1


def test_every_read_of_the_ledger_joins_the_stored_verdict() -> None:
    """The gate. A read added later cannot be caught by a test written before it.

    It reads the statements rather than running them, for the reason the schema-shape pin is read
    off a real database rather than parsed: the failure it guards is a NEW read, and a new read has
    no test of its own by definition. Every statement in the module has to name `viewer_assets` and
    bind the user asking, or it is a read that hands the whole record to whoever asks.
    """
    for name, statement in (
        ("of_asset", _OF_ASSET),
        ("of_entity", _OF_ENTITY),
        ("recent", _RECENT),
    ):
        assert "viewer_assets" in statement, f"{name} does not join the stored verdict"
        assert ":viewer" in statement, f"{name} does not bind the user asking"
        assert "concealed" in statement, f"{name} does not read the concealment flag"
