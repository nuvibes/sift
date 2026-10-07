# SPDX-License-Identifier: AGPL-3.0-or-later
"""Telling open connections what moved, and never telling them anything that did not happen.

A change is announced only once it has landed, and only to the users the write itself resolved: a
message with no payload still says that something exists.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import cast

import pytest

from sift.kernel import changes
from sift.kernel.audience import EVERY_ADMIN, NOBODY, Audience
from sift.kernel.changes import (
    MAX_COMMANDS_PER_BEAT,
    MAX_OPINIONS_PER_BEAT,
    NOTHING_PENDING,
    About,
    AssetOpinion,
    ChangeBus,
    Pending,
    RemoteAction,
    RemoteCommand,
    Subscription,
    announce,
    announce_now,
    telling,
)
from sift.kernel.db import Connection, Database, DatabaseError, Row

pytestmark = pytest.mark.unit


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    db = Database(tmp_path / "changes.sqlite3")
    await db.connect()
    async with db.write() as connection:
        await connection.execute("CREATE TABLE note (id TEXT PRIMARY KEY)")
    yield db
    await db.close()


@pytest.fixture
def bus() -> Iterator[ChangeBus]:
    """A bus this process announces to, removed afterwards: the handle is one per process."""
    made = ChangeBus()
    changes.listens(made)
    yield made
    changes.listens(None)


def _guest() -> Audience:
    return Audience(frozenset({"guest"}))


# --- who is told


def test_only_the_users_a_change_moved_are_told(bus: ChangeBus) -> None:
    """Only the users a change moved are told: telling a connection about a person it may not see
    says the person exists, so the audience arrives decided by the write."""
    told = bus.subscribe("guest")
    not_told = bus.subscribe("bystander")

    bus.publish(_guest(), About.LIBRARY)

    assert told.take(as_admin=True).about == (About.LIBRARY,)
    assert not_told.take(as_admin=True).about == ()


def test_every_connection_of_one_user_is_told(bus: ChangeBus) -> None:
    """Every connection of one user is told."""
    one = bus.subscribe("guest")
    two = bus.subscribe("guest")

    bus.publish(_guest(), About.LIBRARY)

    assert one.take(as_admin=True).about == (About.LIBRARY,)
    assert two.take(as_admin=True).about == (About.LIBRARY,)


def test_taking_what_is_waiting_clears_it(bus: ChangeBus) -> None:
    """Taking what is waiting clears it, or an idle connection hears one change for ever."""
    waiting = bus.subscribe("guest")
    bus.publish(_guest(), About.LIBRARY)

    assert waiting.take(as_admin=True).about == (About.LIBRARY,)
    assert waiting.take(as_admin=True).about == ()


def test_a_thousand_changes_in_one_beat_are_one_message(bus: ChangeBus) -> None:
    """Many changes in one beat are one message: a word, not rows, and the screen re-reads its
    page."""
    waiting = bus.subscribe("guest")
    for _ in range(1000):
        bus.publish(_guest(), About.LIBRARY)

    assert waiting.take(as_admin=True).about == (About.LIBRARY,)


def test_an_empty_audience_tells_nobody(bus: ChangeBus) -> None:
    """An empty audience tells nobody."""
    waiting = bus.subscribe("guest")
    bus.publish(NOBODY, About.LIBRARY)

    assert waiting.take(as_admin=True).about == ()


# --- where the stream of announcements stands


def test_the_mark_moves_when_anything_is_announced(bus: ChangeBus) -> None:
    """The mark moves for a publish, not a subscribe: it answers "has anything been announced"."""
    before = bus.mark
    bus.subscribe("guest")

    assert bus.mark == before, "opening a connection was counted as something happening"

    bus.publish(_guest(), About.SETTINGS)

    assert bus.mark != before


def test_the_mark_moves_for_a_change_reaching_nobody(bus: ChangeBus) -> None:
    """The mark is counted at the publish, so it moves for a change reaching nobody connected: that
    user's next browser must find out."""
    before = bus.mark

    bus.publish(NOBODY, About.LIBRARY)

    assert bus.mark != before


def test_the_mark_moves_when_an_opinion_is_carried(bus: ChangeBus) -> None:
    """The mark moves when an opinion is carried, the separate way in."""
    before = bus.mark

    bus.publish_opinion("guest", _opinion())

    assert bus.mark != before


def test_the_mark_never_says_a_number_on_its_own(bus: ChangeBus) -> None:
    """The mark names the application's run as well as the count, so a restart is never read as
    "nothing moved"."""
    fresh = ChangeBus()

    assert bus.mark != fresh.mark
    assert ":" in bus.mark, "the mark carries no sign of which run of the application counted it"


# --- who is connected


def test_a_released_connection_is_forgotten(bus: ChangeBus) -> None:
    waiting = bus.subscribe("guest")
    assert bus.open_for("guest") == 1

    bus.release(waiting)

    assert bus.open_for("guest") == 0
    assert bus.open_connections() == 0


def test_releasing_the_last_connection_drops_the_user_entirely(bus: ChangeBus) -> None:
    """The last connection released drops the user entirely."""
    bus.release(bus.subscribe("guest"))

    assert bus._by_user == {}


def test_releasing_one_of_two_leaves_the_other(bus: ChangeBus) -> None:
    one = bus.subscribe("guest")
    two = bus.subscribe("guest")

    bus.release(one)

    assert bus.open_for("guest") == 1
    bus.publish(_guest(), About.LIBRARY)
    assert two.take(as_admin=True).about == (About.LIBRARY,)


def test_releasing_twice_is_not_an_error(bus: ChangeBus) -> None:
    """Releasing twice is no error: a socket closes in more than one way."""
    waiting = bus.subscribe("guest")
    bus.release(waiting)
    bus.release(waiting)

    assert bus.open_for("guest") == 0


def test_counting_connections_across_users(bus: ChangeBus) -> None:
    bus.subscribe("guest")
    bus.subscribe("guest")
    bus.subscribe("admin")

    assert bus.open_for("guest") == 2
    assert bus.open_for("admin") == 1
    assert bus.open_connections() == 3


# --- when it is told


async def test_a_change_is_announced_once_it_has_landed(bus: ChangeBus, database: Database) -> None:
    waiting = bus.subscribe("guest")

    async with database.write() as connection:
        await connection.execute("INSERT INTO note (id) VALUES ('one')")
        announce(_guest(), About.LIBRARY)
        # Still inside the transaction, so nobody has been told.
        assert waiting.take(as_admin=True).about == ()

    assert waiting.take(as_admin=True).about == (About.LIBRARY,)


async def test_a_change_that_rolled_back_is_never_announced(
    bus: ChangeBus, database: Database
) -> None:
    """A change is announced after the commit, so a rolled-back write is never re-read for."""
    waiting = bus.subscribe("guest")

    with pytest.raises(RuntimeError, match="deliberate"):
        async with database.write() as connection:
            await connection.execute("INSERT INTO note (id) VALUES ('one')")
            announce(_guest(), About.LIBRARY)
            raise RuntimeError("deliberate")

    assert waiting.take(as_admin=True).about == ()


async def test_announcing_outside_a_write_is_refused(bus: ChangeBus) -> None:
    """Announcing outside a write is refused loudly: the caller believes it is inside one."""
    with pytest.raises(DatabaseError, match="outside a write"):
        announce(_guest(), About.LIBRARY)


async def test_a_change_with_nobody_listening_is_harmless(database: Database) -> None:
    """With no bus, a change is harmless."""
    changes.listens(None)

    async with database.write() as connection:
        await connection.execute("INSERT INTO note (id) VALUES ('one')")
        announce(_guest(), About.LIBRARY)


def test_the_current_mark_is_the_listening_bus_s_mark(bus: ChangeBus) -> None:
    """The current mark is the listening bus's."""
    assert changes.current_mark() == bus.mark

    bus.publish(_guest(), About.LIBRARY)

    assert changes.current_mark() == bus.mark


def test_with_nobody_listening_there_is_no_mark_at_all() -> None:
    """With no bus there is no mark, so a reader keeps nothing."""
    changes.listens(None)

    assert changes.current_mark() is None
    assert changes.mark_of({About.LIBRARY}) is None


def test_a_mark_of_some_subjects_moves_with_those_alone(bus: ChangeBus) -> None:
    """A mark of some subjects moves with those alone, so a library answer survives the job queue
    moving; a carried opinion is its own subject."""
    library = changes.mark_of({About.LIBRARY, About.OPINIONS})
    assert library == bus.mark_of([About.OPINIONS, About.LIBRARY]), "the same subjects, any order"

    bus.publish(_guest(), About.JOBS)
    assert changes.mark_of({About.LIBRARY, About.OPINIONS}) == library

    bus.publish(NOBODY, About.LIBRARY)
    moved = changes.mark_of({About.LIBRARY, About.OPINIONS})
    assert moved != library

    bus.publish_opinion("guest", _opinion())
    assert changes.mark_of({About.LIBRARY, About.OPINIONS}) != moved
    assert ChangeBus().mark_of({About.LIBRARY}) != bus.mark_of({About.LIBRARY}), "a run apart"


# --- the audience


def test_two_audiences_together_are_one() -> None:
    """A write changing several things tells each user once."""
    both = Audience(frozenset({"guest"})) | Audience(frozenset({"admin", "guest"}))

    assert both.users == {"guest", "admin"}


def test_an_audience_says_whether_it_is_anybody() -> None:
    assert not NOBODY
    assert Audience(frozenset({"guest"}))


# --- who may HEAR it, which is not the same as who it is for


def test_a_subject_only_an_admin_may_hear_reaches_an_admin(bus: ChangeBus) -> None:
    """An admin-only subject reaches an admin."""
    watching = bus.subscribe("admin")

    bus.publish(EVERY_ADMIN, About.JOBS)

    assert watching.take(as_admin=True).about == (About.JOBS,)


def test_the_same_subject_is_withheld_from_a_guest(bus: ChangeBus) -> None:
    """The same subject is withheld from a guest at the moment of sending: the connection re-reads
    its user every beat, so a demotion takes effect on the next one."""
    watching = bus.subscribe("guest")

    bus.publish(EVERY_ADMIN, About.JOBS)

    assert watching.take(as_admin=False).about == ()


def test_a_user_named_outright_is_told_whatever_its_role(bus: ChangeBus) -> None:
    """A user named outright is told whatever its role."""
    watching = bus.subscribe("guest")

    bus.publish(_guest(), About.LIBRARY)

    assert watching.take(as_admin=False).about == (About.LIBRARY,)


def test_being_named_outright_survives_the_same_subject_arriving_admin_only(
    bus: ChangeBus,
) -> None:
    """A named reading and an admins-only reading in one beat: the unconditional one wins."""
    watching = bus.subscribe("guest")

    bus.publish(EVERY_ADMIN, About.ARRIVALS)
    bus.publish(_guest(), About.ARRIVALS)

    assert watching.take(as_admin=False).about == (About.ARRIVALS,)


def test_an_audience_widened_to_admins_reaches_both(bus: ChangeBus) -> None:
    admin = bus.subscribe("admin")
    guest = bus.subscribe("guest")
    stranger = bus.subscribe("stranger")

    bus.publish(_guest().widened_to_admins(), About.ARRIVALS)

    assert admin.take(as_admin=True).about == (About.ARRIVALS,)
    assert guest.take(as_admin=False).about == (About.ARRIVALS,)
    assert stranger.take(as_admin=False).about == ()


# --- what this user thinks of a file


def _opinion(asset_id: str = "asset", *, views: int = 1) -> AssetOpinion:
    return AssetOpinion(
        asset_id=asset_id, favorite=True, rating=4, views=views, pinned=False, o_count=0
    )


def test_an_opinion_reaches_this_users_other_screens(bus: ChangeBus) -> None:
    """An opinion reaches this user's other screens without a fetch."""
    other_tab = bus.subscribe("guest")

    bus.publish_opinion("guest", _opinion())

    taken = other_tab.take(as_admin=False)
    assert taken.about == (About.OPINIONS,)
    assert [one.asset_id for one in taken.opinions] == ["asset"]
    assert taken.opinions[0].views == 1


def test_an_opinion_reaches_nobody_else(bus: ChangeBus) -> None:
    """An opinion reaches nobody else."""
    somebody_else = bus.subscribe("admin")

    bus.publish_opinion("guest", _opinion())

    assert somebody_else.take(as_admin=True).about == ()


def test_more_opinions_than_a_message_carries_says_so(bus: ChangeBus) -> None:
    """Past what a message carries it says so, and the screen re-reads its page."""
    watching = bus.subscribe("guest")

    for index in range(MAX_OPINIONS_PER_BEAT + 5):
        bus.publish_opinion("guest", _opinion(f"asset{index}"))

    taken = watching.take(as_admin=False)
    assert len(taken.opinions) == MAX_OPINIONS_PER_BEAT
    assert taken.more_opinions


def test_under_the_cap_nothing_claims_there_was_more(bus: ChangeBus) -> None:
    watching = bus.subscribe("guest")

    bus.publish_opinion("guest", _opinion())

    assert not watching.take(as_admin=False).more_opinions


# --- a command from this user's phone


def _command(command_id: str) -> RemoteCommand:
    return RemoteCommand(id=command_id, screen="player", action=RemoteAction.PLAY_PAUSE, value=1.0)


def test_a_command_reaches_only_its_users_screens_and_leaves_the_mark_alone(
    bus: ChangeBus,
) -> None:
    """A command does not move the mark, or a reconnect would replay it."""
    tab, other_tab = bus.subscribe("guest"), bus.subscribe("guest")
    somebody_else = bus.subscribe("admin")
    before = bus.mark

    told = bus.publish_command("guest", _command("c1"))

    assert told == 2
    assert bus.mark == before
    for screen in (tab, other_tab):
        taken = screen.take(as_admin=False)
        assert taken.about == (About.REMOTE,)
        assert [one.id for one in taken.commands] == ["c1"]
    assert not somebody_else.take(as_admin=True)
    assert bus.publish_command("nobody", _command("c2")) == 0


def test_a_command_that_waited_past_its_time_is_dropped_and_says_nothing(bus: ChangeBus) -> None:
    tab = bus.subscribe("guest")
    tab.note_command(_command("late"), expires_at=10.0)

    assert not tab.take(as_admin=False, now=11.0), "a late command is not a subject to send"


def test_a_late_command_does_not_hide_the_other_news_on_the_beat(bus: ChangeBus) -> None:
    tab = bus.subscribe("guest")
    tab.note(About.LIBRARY, admins_only=False)
    tab.note_command(_command("late"), expires_at=10.0)

    taken = tab.take(as_admin=False, now=11.0)

    assert taken.about == (About.LIBRARY,)
    assert taken.commands == ()


def test_past_the_cap_the_oldest_commands_go(bus: ChangeBus) -> None:
    tab = bus.subscribe("guest")
    for index in range(MAX_COMMANDS_PER_BEAT + 3):
        tab.note_command(_command(f"c{index}"), expires_at=10.0)

    taken = tab.take(as_admin=False, now=0.0)

    assert len(taken.commands) == MAX_COMMANDS_PER_BEAT
    assert taken.commands[0].id == "c3"
    assert tab.wake.is_set() is False, "taking clears the wake"


# --- a write that says what it did


async def test_a_write_that_moved_a_row_is_announced(bus: ChangeBus, database: Database) -> None:
    watching = bus.subscribe("guest")

    async with telling(database, _guest(), About.LIBRARY) as connection:
        await connection.execute("INSERT INTO note (id) VALUES ('one')")

    assert watching.take(as_admin=False).about == (About.LIBRARY,)


async def test_a_write_that_moved_nothing_says_nothing(bus: ChangeBus, database: Database) -> None:
    """A conditional write that moved nothing says nothing, so an idle library stays idle."""
    watching = bus.subscribe("guest")

    async with telling(database, _guest(), About.LIBRARY) as connection:
        await connection.execute("UPDATE note SET id = 'two' WHERE id = 'nothing here'")

    assert watching.take(as_admin=False).about == ()


async def test_an_audience_read_after_the_write_is_read_only_when_a_row_moved(
    bus: ChangeBus, database: Database
) -> None:
    watching = bus.subscribe("guest")
    asked: list[Connection] = []

    async def whoever(connection: Connection) -> Audience:
        asked.append(connection)
        return _guest()

    async with telling(database, whoever, About.LIBRARY) as connection:
        await connection.execute("UPDATE note SET id = 'two' WHERE id = 'nothing here'")
    assert asked == []
    assert watching.take(as_admin=False).about == ()

    async with telling(database, whoever, About.LIBRARY) as connection:
        await connection.execute("INSERT INTO note (id) VALUES ('one')")
    assert asked == [connection]
    assert watching.take(as_admin=False).about == (About.LIBRARY,)


async def test_a_write_that_failed_says_nothing(bus: ChangeBus, database: Database) -> None:
    watching = bus.subscribe("guest")

    with pytest.raises(RuntimeError, match="deliberate"):
        async with telling(database, _guest(), About.LIBRARY) as connection:
            await connection.execute("INSERT INTO note (id) VALUES ('one')")
            raise RuntimeError("deliberate")

    assert watching.take(as_admin=False).about == ()


def test_something_never_written_down_can_still_be_announced(bus: ChangeBus) -> None:
    """An in-memory figure such as download progress can be announced with no write to hang on."""
    watching = bus.subscribe("admin")

    announce_now(EVERY_ADMIN, About.DOWNLOADS)

    assert watching.take(as_admin=True).about == (About.DOWNLOADS,)


# --- files arriving


async def test_a_file_arriving_reaches_every_admin_and_the_users_a_share_names(
    bus: ChangeBus, database: Database
) -> None:
    """An arrival is announced to every admin and to every user holding any share: the resolver is
    per viewer and has no place on the path taking a file in."""
    admin = bus.subscribe("admin")
    holder = bus.subscribe("holder")
    nobody = bus.subscribe("has-nothing")

    async def resolve(_connection: object) -> Audience:
        return Audience(frozenset({"holder"}))

    changes.resolves_arrivals(resolve)
    try:
        async with database.write() as connection:
            await connection.execute("INSERT INTO note (id) VALUES ('one')")
            await changes.announce_arrival(connection)
    finally:
        changes.resolves_arrivals(None)

    assert admin.take(as_admin=True).about == (About.ARRIVALS,)
    assert holder.take(as_admin=False).about == (About.ARRIVALS,)
    assert nobody.take(as_admin=False).about == ()


async def test_a_file_arriving_still_reaches_admins_when_nobody_said_how(
    bus: ChangeBus, database: Database
) -> None:
    """Without an arrivals resolver, admins alone are told."""
    changes.resolves_arrivals(None)
    admin = bus.subscribe("admin")

    async with database.write() as connection:
        await connection.execute("INSERT INTO note (id) VALUES ('one')")
        await changes.announce_arrival(connection)

    assert admin.take(as_admin=True).about == (About.ARRIVALS,)


def test_an_audience_widened_to_admins_keeps_the_users_it_named() -> None:
    widened = Audience(frozenset({"guest"})).widened_to_admins()

    assert widened.users == {"guest"}
    assert widened.every_admin


def test_the_user_that_wrote_something_is_an_audience_of_one() -> None:
    """A user's own opinion is announced to that user alone."""
    assert Audience.of_user("guest").users == {"guest"}
    assert not Audience.of_user("guest").every_admin


def test_a_reach_is_somebody_even_though_it_names_nobody() -> None:
    """`EVERY_ADMIN` holds no ids and is still somebody."""
    assert EVERY_ADMIN
    assert not NOBODY


def test_the_users_a_statement_reported_are_read_off_its_rows() -> None:
    """A resolved audience is read off the first column of the write's rows, by position."""
    rows = cast("list[Row]", [("guest",), ("admin",)])

    assert Audience.of(rows).users == {"guest", "admin"}


def test_pending_says_whether_there_is_anything_to_send() -> None:
    """An idle beat sends nothing."""
    assert not NOTHING_PENDING
    assert Pending(about=(About.LIBRARY,), opinions=(), more_opinions=False)


def test_releasing_a_connection_that_was_never_registered_is_harmless() -> None:
    """Releasing an unregistered connection is harmless."""
    bus = ChangeBus()

    bus.release(Subscription("nobody"))

    assert bus.open_connections() == 0


async def test_an_opinion_written_with_nobody_listening_is_harmless(database: Database) -> None:
    """An opinion written with no bus is harmless."""
    changes.listens(None)

    async with database.write() as connection:
        await connection.execute("INSERT INTO note (id) VALUES ('one')")
        changes.announce_opinion("guest", _opinion())


def test_the_unconditional_reading_wins_whichever_order_they_arrive_in(bus: ChangeBus) -> None:
    """The unconditional reading wins whichever order the two arrive in."""
    watching = bus.subscribe("guest")

    bus.publish(_guest(), About.ARRIVALS)
    bus.publish(EVERY_ADMIN, About.ARRIVALS)

    assert watching.take(as_admin=False).about == (About.ARRIVALS,)


def test_releasing_a_connection_the_user_does_not_hold_leaves_the_others(
    bus: ChangeBus,
) -> None:
    """Releasing an unregistered connection leaves a user's own list intact."""
    kept = bus.subscribe("guest")

    bus.release(Subscription("guest"))

    assert bus.open_for("guest") == 1
    bus.publish(_guest(), About.LIBRARY)
    assert kept.take(as_admin=False).about == (About.LIBRARY,)


async def test_an_opinion_is_carried_once_the_write_that_made_it_has_landed(
    bus: ChangeBus, database: Database
) -> None:
    """An opinion is announced after the commit too."""
    watching = bus.subscribe("guest")

    async with database.write() as connection:
        await connection.execute("INSERT INTO note (id) VALUES ('one')")
        changes.announce_opinion("guest", _opinion())
        assert watching.take(as_admin=False).about == ()

    taken = watching.take(as_admin=False)
    assert taken.about == (About.OPINIONS,)
    assert [one.asset_id for one in taken.opinions] == ["asset"]
