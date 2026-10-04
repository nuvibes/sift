# SPDX-License-Identifier: AGPL-3.0-or-later
"""One person's opinions of a file and of a named thing, what they watched, and where to start again."""

from __future__ import annotations

import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
from contextlib import closing
from typing import Any

import pytest

# A fingerprint write records an event through the ledger door, and the door writes into the
# workbench's own table, so a database built without that component has nowhere to put it.
# Imported for the registration, nothing else.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.changes import AssetOpinion
from sift.kernel.content import schema, user_state
from sift.kernel.content.entity_state import EntityStateStore, PinnableKind
from sift.kernel.content.identity import (
    ContentStore,
    DerivativeKind,
)
from sift.kernel.content.user_state import (
    HEAT_BUCKETS,
    RESUMING,
    AssetUserState,
    UserStateStore,
    resume_minimum_ms,
    resume_point,
)
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ingress import (
    CLASSIFIER_VERSION,
)
from sift.kernel.tests.content_helpers import (
    checked,
    corpus_survives,  # noqa: F401  (the corpus check, autouse)
    place,
)
from sift.testing.fixtures import Actors, FakeClock, LibraryRoot, World


@pytest.fixture
async def state(temp_db: Database) -> UserStateStore:
    await temp_db.initialize_schema()
    return UserStateStore(temp_db, clock=FakeClock(1_700_000_000).now)


@pytest.mark.integration
async def test_an_untouched_asset_reads_as_defaults(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """Most of a library has never been rated. That has to be the cheap case, and a caller should
    not have to tell "nobody rated this" apart from "there is no row"."""
    blank = await state.state_of(world.solo, actors.admin.id)
    assert blank == AssetUserState(asset_id=world.solo, user_id=actors.admin.id)
    assert blank.favorite is False
    assert blank.rating is None
    assert blank.view_count == 0


@pytest.mark.integration
async def test_counts_carried_from_another_library_only_ever_raise_what_is_here(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """An import brings the counts across and never the dates, and a count already higher here
    is not lowered by a library that saw the file less."""
    await state.bump_o_count(world.solo, actors.admin.id)
    await state.bump_o_count(world.solo, actors.admin.id)

    carried = await state.carry_counts(world.solo, actors.admin.id, o_count=1, views=7)
    assert (carried.o_count, carried.view_count) == (2, 7)

    fresh = await state.carry_counts(world.twin, actors.admin.id, o_count=-3, views=-1)
    assert (fresh.o_count, fresh.view_count) == (0, 0), "never below nothing"

    with pytest.raises(ValueError):
        await state.carry_counts("not an id", actors.admin.id, o_count=1, views=1)


@pytest.mark.integration
async def test_a_heart_goes_on_and_comes_off(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    on = await state.set_favorite(world.solo, actors.admin.id, True)
    assert on.favorite is True
    assert (await state.state_of(world.solo, actors.admin.id)).favorite is True

    off = await state.set_favorite(world.solo, actors.admin.id, False)
    assert off.favorite is False
    assert (await state.state_of(world.solo, actors.admin.id)).favorite is False


@pytest.mark.integration
async def test_two_people_rate_the_same_asset_independently(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """The whole reason this is a table and not two columns on `assets`."""
    await state.set_rating(world.solo, actors.admin.id, 5)
    await state.set_rating(world.solo, actors.guest.id, 1)
    await state.set_favorite(world.solo, actors.admin.id, True)

    mine = await state.state_of(world.solo, actors.admin.id)
    theirs = await state.state_of(world.solo, actors.guest.id)

    assert (mine.rating, theirs.rating) == (5, 1)
    assert mine.favorite is True
    assert theirs.favorite is False, "one person's heart appeared on another person's row"


@pytest.mark.integration
async def test_a_rating_can_be_cleared_back_to_nothing(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """Cleared is NULL, not zero. A query for "rated at all" must not find this row."""
    await state.set_rating(world.solo, actors.admin.id, 3)
    cleared = await state.set_rating(world.solo, actors.admin.id, None)
    assert cleared.rating is None


@pytest.mark.parametrize("rating", [0, 11, -1, 100])
@pytest.mark.integration
async def test_a_rating_outside_the_scale_is_refused(
    state: UserStateStore, world: World, actors: Actors, rating: int
) -> None:
    """Zero especially: it is the value a caller reaches for to mean "unrated", and it would sort
    and filter as a real rating for ever after.

    Eleven rather than six. What is stored is out of TEN whichever scale is drawn, so six is an
    ordinary rating now: three stars on a five-star screen."""
    with pytest.raises(ValueError, match="rating is"):
        await state.set_rating(world.solo, actors.admin.id, rating)


@pytest.mark.integration
async def test_a_heart_and_a_rating_do_not_overwrite_each_other(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """Both write the same row through the same conflict clause, so each one naming only its own
    column is what keeps them apart."""
    await state.set_rating(world.solo, actors.admin.id, 4)
    await state.set_favorite(world.solo, actors.admin.id, True)

    both = await state.state_of(world.solo, actors.admin.id)
    assert (both.rating, both.favorite) == (4, True)


# --- The same three opinions, said once over a whole selection ----------------------------------


@pytest.fixture
def opinions(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, AssetOpinion]]:
    """Every opinion the store announced while a test ran, in the order it announced them.

    The announcement is the half of these writes that no row can prove: a selection written with
    nobody told leaves every other screen this user has open drawing what it last read, and the
    table looks perfectly correct afterwards.
    """
    heard: list[tuple[str, AssetOpinion]] = []

    def hear(user_id: str, opinion: AssetOpinion) -> None:
        heard.append((user_id, opinion))

    monkeypatch.setattr(user_state, "announce_opinion", hear)
    return heard


@pytest.mark.integration
async def test_a_selection_is_hearted_once_per_file_however_often_it_was_named(
    state: UserStateStore, world: World, actors: Actors, opinions: list[tuple[str, AssetOpinion]]
) -> None:
    """Two mentions of one file in a selection are one row and one message. `json_each` would
    otherwise hand the statement the same row twice, and the screen holding that tile would be
    told about it twice for one choice."""
    moved = await state.set_favorite_many(
        [world.solo, world.twin, world.solo], actors.admin.id, True
    )

    assert moved == 2, "the repeated id was written twice"
    assert (await state.state_of(world.solo, actors.admin.id)).favorite is True
    assert (await state.state_of(world.twin, actors.admin.id)).favorite is True
    assert {opinion.asset_id for _, opinion in opinions} == {world.solo, world.twin}
    assert [user for user, _ in opinions] == [actors.admin.id, actors.admin.id]
    assert all(opinion.favorite for _, opinion in opinions)

    off = await state.set_favorite_many([world.solo, world.twin], actors.admin.id, False)
    assert off == 2
    assert (await state.state_of(world.twin, actors.admin.id)).favorite is False


@pytest.mark.integration
async def test_a_selection_is_pinned_and_unpinned_without_touching_the_hearts(
    state: UserStateStore, world: World, actors: Actors, opinions: list[tuple[str, AssetOpinion]]
) -> None:
    """The bulk pin is independent of the bulk heart for the same reason the singulars are: both
    write the same row through the same conflict clause, and each naming only its own column is
    the whole of what keeps them apart."""
    await state.set_favorite_many([world.solo], actors.admin.id, True)
    opinions.clear()

    assert await state.set_pinned_many([world.solo, world.twin], actors.admin.id, True) == 2

    both = await state.state_of(world.solo, actors.admin.id)
    assert (both.pinned, both.favorite) == (True, True)
    assert all(opinion.pinned for _, opinion in opinions)

    assert await state.set_pinned_many([world.solo], actors.admin.id, False) == 1
    assert (await state.state_of(world.solo, actors.admin.id)).pinned is False


@pytest.mark.integration
async def test_one_rating_goes_across_a_selection_and_can_be_cleared_across_it(
    state: UserStateStore, world: World, actors: Actors, opinions: list[tuple[str, AssetOpinion]]
) -> None:
    """Three stars over a selection is one choice with one answer, not a nudge each, and
    clearing it is the same choice said with None, which has to reach every row the stars did."""
    assert await state.set_rating_many([world.solo, world.twin], actors.admin.id, 6) == 2
    assert (await state.state_of(world.solo, actors.admin.id)).rating == 6
    assert {opinion.rating for _, opinion in opinions} == {6}

    assert await state.set_rating_many([world.solo, world.twin], actors.admin.id, None) == 2
    assert (await state.state_of(world.twin, actors.admin.id)).rating is None


@pytest.mark.parametrize("rating", [0, 11, -1])
@pytest.mark.integration
async def test_a_selection_cannot_be_rated_off_the_scale_either(
    state: UserStateStore, world: World, actors: Actors, rating: int
) -> None:
    """The same refusal as the singular, checked before a statement is built: zero especially,
    because it is the value a caller reaches for to mean "unrated" and it would sort and filter as
    a real rating for ever after, across a whole selection at once."""
    with pytest.raises(ValueError, match="rating is"):
        await state.set_rating_many([world.solo], actors.admin.id, rating)


@pytest.mark.integration
async def test_a_selection_of_nothing_writes_nothing_and_is_not_news(
    state: UserStateStore,
    actors: Actors,
    temp_db: Database,
    opinions: list[tuple[str, AssetOpinion]],
) -> None:
    """A transaction and a turn of the write lock bought to do nothing is the shape the bulk form
    exists to stop, so the empty selection has to stop before the statement rather than rely on
    `json_each` over an empty array being harmless."""
    assert await state.set_favorite_many([], actors.admin.id, True) == 0
    assert await state.set_pinned_many([], actors.admin.id, True) == 0
    assert await state.set_rating_many([], actors.admin.id, 5) == 0

    assert opinions == [], "an empty selection was announced to somebody"
    assert not await temp_db.fetch_all("SELECT 1 AS there FROM asset_user_state")


@pytest.mark.integration
async def test_a_selection_holding_something_that_is_not_an_id_is_refused_whole(
    state: UserStateStore, world: World, actors: Actors, temp_db: Database
) -> None:
    """Checked over the whole selection before any of it is written. A malformed id is a caller
    that skipped the access layer, and discovering it half way through a statement would leave the
    files before it written and the files after it not."""
    with pytest.raises(ValueError, match="not an asset id"):
        await state.set_favorite_many([world.solo, "not-an-id"], actors.admin.id, True)

    assert not await temp_db.fetch_all("SELECT 1 AS there FROM asset_user_state")


@pytest.mark.integration
async def test_views_and_time_watched_accumulate(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    await state.record_view(world.solo, actors.admin.id, watch_ms=1_000)
    await state.record_view(world.solo, actors.admin.id, watch_ms=2_500)

    seen = await state.state_of(world.solo, actors.admin.id)
    assert seen.view_count == 2
    assert seen.watched_ms == 3_500
    assert seen.last_viewed_at == 1_700_000_000


@pytest.mark.integration
async def test_a_view_of_no_length_still_counts(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """Opening something and closing it again is a thing you did, and a history that omits it is
    less useful than one that does not."""
    counted = await state.record_view(world.solo, actors.admin.id)
    assert counted.view_count == 1
    assert counted.watched_ms == 0
    assert counted.last_viewed_at is not None


@pytest.mark.integration
async def test_negative_time_watched_is_refused(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    with pytest.raises(ValueError, match="negative"):
        await state.record_view(world.solo, actors.admin.id, watch_ms=-1)


@pytest.mark.integration
async def test_a_negative_resume_position_is_refused(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """There is no such place in a file. The route rejects it first, but the store is what other
    callers reach, and a stored negative would come back as a seek nothing sensible does with."""
    with pytest.raises(ValueError, match="negative"):
        await state.record_view(world.solo, actors.admin.id, resume_ms=-1)


@pytest.mark.integration
async def test_where_it_stopped_is_replaced_rather_than_kept(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """Every report states the position, and no position is one of the things it can state.

    Without this a video stopped halfway and later watched through would reopen at the halfway
    point forever: a finished sitting has no position of its own to write over the old one.
    """
    await state.record_view(world.solo, actors.admin.id, resume_ms=90_000)
    assert (await state.state_of(world.solo, actors.admin.id)).resume_ms == 90_000

    await state.record_view(world.solo, actors.admin.id, resume_ms=None)

    assert (await state.state_of(world.solo, actors.admin.id)).resume_ms is None
    # And the counters beside it still accumulate, which is the difference being drawn.
    assert (await state.state_of(world.solo, actors.admin.id)).view_count == 2


@pytest.mark.parametrize("bogus", ["", "../../etc", "not-an-id", "' OR 1=1 --"])
@pytest.mark.integration
async def test_a_malformed_id_reads_as_nothing_rather_than_matching(
    state: UserStateStore, world: World, bogus: str
) -> None:
    """It never reaches a statement. Nothing here interpolates, so this is not the injection
    control: it is that a caller holding a wrong id should get an empty answer, not an error."""
    assert (await state.state_of(world.solo, bogus)).rating is None
    assert (await state.state_of(bogus, world.solo)).favorite is False


@pytest.mark.integration
async def test_writing_against_a_malformed_id_is_refused(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """A read of a wrong id is empty; a write of one is a bug in the caller and says so."""
    with pytest.raises(ValueError, match="not an asset id"):
        await state.set_favorite("not-an-id", actors.admin.id, True)
    with pytest.raises(ValueError, match="not a user id"):
        await state.set_rating(world.solo, "not-an-id", 3)
    with pytest.raises(ValueError, match="not a user id"):
        await state.record_view(world.solo, "not-an-id")


@pytest.mark.integration
async def test_deleting_a_person_takes_their_opinions_with_them(
    state: UserStateStore, temp_db: Database, world: World, actors: Actors
) -> None:
    """Their ratings are theirs. Nothing of them stays behind attached to somebody else's library."""
    await state.set_rating(world.solo, actors.guest.id, 5)
    await temp_db.execute("DELETE FROM users WHERE id = ?", (actors.guest.id,))

    left = await temp_db.fetch_all(
        "SELECT asset_id FROM asset_user_state WHERE user_id = ?", (actors.guest.id,)
    )
    assert left == []


@pytest.mark.integration
async def test_deleting_an_asset_takes_the_opinions_of_it_with_it(
    state: UserStateStore, temp_db: Database, world: World, actors: Actors
) -> None:
    await state.set_rating(world.solo, actors.admin.id, 5)
    await temp_db.execute("DELETE FROM assets WHERE id = ?", (world.solo,))

    left = await temp_db.fetch_all(
        "SELECT user_id FROM asset_user_state WHERE asset_id = ?", (world.solo,)
    )
    assert left == []


@pytest.mark.integration
async def test_the_rating_scale_is_enforced_by_the_database_too(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """The store checks the range, and so does the table. The check in Python is the good error
    message; this is the one that holds if a future writer forgets to go through the store."""
    await temp_db.initialize_schema()
    with pytest.raises(Exception, match="CHECK constraint failed"):
        await temp_db.execute(
            "INSERT INTO asset_user_state (asset_id, user_id, rating, updated_at) "
            "VALUES (?, ?, ?, ?)",
            # Eleven: ten is the top of the stored scale, and nine is an ordinary rating.
            (world.solo, actors.admin.id, 11, 1_700_000_000),
        )


# --- what somebody thought, as it changed -------------------------------------------------------


async def _opinions(database: Database) -> list[tuple[str, str, object, object]]:
    """Every opinion row, oldest first. The id is a ULID, so `ORDER BY id` is the order they
    happened, which `at` is not, on a machine whose wall clock steps backwards."""
    found = await database.fetch_all(
        "SELECT kind, subject_id, before, after FROM opinions ORDER BY id"
    )
    return [
        (str(row["kind"]), str(row["subject_id"]), row["before"], row["after"]) for row in found
    ]


@pytest.mark.integration
async def test_every_opinion_writes_what_it_replaced(
    state: UserStateStore, temp_db: Database, world: World, actors: Actors
) -> None:
    """The whole point of the table: the value BEFORE, which the upsert destroys.

    It cannot be read back from the write afterwards, which is the trap this is here to hold shut.
    SQLite's RETURNING answers with the row after the statement, so a helper that read `before` from
    it would record the new value twice and nothing about the rows would look wrong.
    """
    await state.set_favorite(world.solo, actors.admin.id, True)
    await state.set_favorite(world.solo, actors.admin.id, False)
    await state.set_rating(world.solo, actors.admin.id, 7)
    await state.set_rating(world.solo, actors.admin.id, 9)
    await state.set_pinned(world.solo, actors.admin.id, True)
    await state.bump_o_count(world.solo, actors.admin.id)
    await state.bump_o_count(world.solo, actors.admin.id)
    await state.lower_o_count(world.solo, actors.admin.id)
    await state.reset_o_count(world.solo, actors.admin.id)

    assert await _opinions(temp_db) == [
        # NULL before the first one, because there was no row: "it was off" and "there was nothing"
        # are different answers and only the second is true.
        ("favorite", world.solo, None, 1),
        ("favorite", world.solo, 1, 0),
        ("rating", world.solo, None, 7),
        ("rating", world.solo, 7, 9),
        # ZERO rather than None: by now the row exists, so the pin really was off. The NULL above
        # is the stronger claim and belongs only to the write that found no row at all.
        ("pin", world.solo, 0, 1),
        ("o", world.solo, 0, 1),
        ("o", world.solo, 1, 2),
        ("o", world.solo, 2, 1),
        ("o", world.solo, 1, 0),
    ]


@pytest.mark.integration
async def test_an_opinion_belongs_to_the_user_that_held_it(
    state: UserStateStore, temp_db: Database, world: World, actors: Actors
) -> None:
    """Per user, like the row it is about. Two people hearting one file is two histories."""
    await state.set_favorite(world.solo, actors.admin.id, True)
    await state.set_favorite(world.solo, actors.guest.id, True)

    rows = await temp_db.fetch_all("SELECT user_id, subject_kind FROM opinions ORDER BY id")
    assert [str(row["user_id"]) for row in rows] == [actors.admin.id, actors.guest.id]
    assert {str(row["subject_kind"]) for row in rows} == {"asset"}


@pytest.mark.integration
async def test_a_selection_writes_one_opinion_per_file(
    state: UserStateStore, temp_db: Database, world: World, actors: Actors
) -> None:
    """A hundred files hearted at once is a hundred opinions, not one saying "some files".

    The count is what the CALLER reports. A history holding the count could answer nothing about
    any of the files in it, which is the question the table exists for.
    """
    await state.set_rating_many([world.solo, world.twin], actors.admin.id, 4)
    await state.set_rating_many([world.solo, world.twin], actors.admin.id, 6)

    assert await _opinions(temp_db) == [
        ("rating", world.solo, None, 4),
        ("rating", world.twin, None, 4),
        ("rating", world.solo, 4, 6),
        ("rating", world.twin, 4, 6),
    ]


@pytest.mark.integration
async def test_a_view_and_a_stretch_of_watching_write_no_opinion(
    state: UserStateStore, temp_db: Database, world: World, actors: Actors
) -> None:
    """The line the table is drawn on. Watching is a measurement of what happened; it decides
    nothing about the file, it happens far more often than everything else put together, and it has
    a table of its own. Folding it in here would bury the opinions under it."""
    await state.record_view(world.solo, actors.admin.id, watch_ms=1_000)
    await state.record_watch_time(world.solo, actors.admin.id, watch_ms=1_000)

    assert await _opinions(temp_db) == []


@pytest.mark.integration
async def test_an_opinion_outlives_the_file_it_was_about(
    state: UserStateStore, temp_db: Database, world: World, actors: Actors
) -> None:
    """No key on the subject, for the reason a play has none: it is the user's own history and a
    file being deleted is part of that history rather than the end of it."""
    await state.set_favorite(world.solo, actors.admin.id, True)

    await temp_db.execute("DELETE FROM assets WHERE id = ?", (world.solo,))

    assert await _opinions(temp_db) == [("favorite", world.solo, None, 1)]


@pytest.mark.integration
async def test_removing_the_user_takes_what_it_thought(
    state: UserStateStore, temp_db: Database, world: World, actors: Actors
) -> None:
    """The other key does cascade, and that is the deliberate forget."""
    await state.set_favorite(world.solo, actors.guest.id, True)

    await temp_db.execute("DELETE FROM users WHERE id = ?", (actors.guest.id,))

    assert await _opinions(temp_db) == []


# --- a file watched straight through ------------------------------------------------------------


@pytest.mark.integration
async def test_a_sitting_that_reaches_the_end_without_earning_a_view_is_finished(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """A video watched straight through is marked finished, in the order the reports really arrive.

    A video of two minutes or more earns its view on the report that CROSSES thirty seconds, and
    reports that it finished on a LATER one, which does not earn a second view and so goes to
    `record_watch_time`, which must still be able to write the date.
    """
    await state.record_view(world.solo, actors.admin.id, watch_ms=30_000, resume_ms=30_000)
    partway = await state.state_of(world.solo, actors.admin.id)
    assert partway.view_count == 1 and partway.completed_at is None

    await state.record_watch_time(
        world.solo, actors.admin.id, watch_ms=120_000, resume_ms=None, completed=True
    )

    finished = await state.state_of(world.solo, actors.admin.id)
    assert finished.completed_at is not None, "watched to the end and not marked finished"
    assert finished.finished is True
    # The view is still ONE. Reaching the end is not a second viewing of the same sitting.
    assert finished.view_count == 1
    assert finished.watched_ms == 150_000


@pytest.mark.integration
async def test_finishing_it_once_is_the_date_that_is_kept(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """Written once and then left alone, exactly as the view's own statement leaves it. Watching
    the first minute of something again does not unwatch the rest of it."""
    await state.record_watch_time(world.solo, actors.admin.id, watch_ms=10, completed=True)
    first = (await state.state_of(world.solo, actors.admin.id)).completed_at

    await state.record_watch_time(world.solo, actors.admin.id, watch_ms=10, completed=False)

    assert (await state.state_of(world.solo, actors.admin.id)).completed_at == first


@pytest.mark.integration
async def test_a_sitting_with_nothing_in_it_but_the_end_still_writes(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """The guard that skips an empty sitting has to let this one through: a clip that ends with no
    time left to report is the last piece of something watched straight through, and it carries the
    one fact that piece exists to deliver."""
    await state.record_watch_time(world.solo, actors.admin.id, watch_ms=0, completed=True)

    assert (await state.state_of(world.solo, actors.admin.id)).finished is True


@pytest.mark.integration
async def test_a_page_of_assets_reads_its_state_in_one_go(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """The grid asks once for the whole page. Fifty tiles asking one at a time is the shape that
    makes a fast page feel slow, and it does not show up until the library is big."""
    await state.set_rating(world.solo, actors.admin.id, 4)
    await state.set_favorite(world.twin, actors.admin.id, True)

    found = await state.states_of([world.solo, world.twin, world.loose], actors.admin.id)

    assert set(found) == {world.solo, world.twin}, "an untouched asset invented a row"
    assert found[world.solo].rating == 4
    assert found[world.twin].favorite is True


@pytest.mark.integration
async def test_a_bulk_read_is_still_one_person_only(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """The one query in here that takes a list. A predicate that lost the user would show one
    user another user's ratings, and the page would look perfectly normal."""
    await state.set_rating(world.solo, actors.guest.id, 5)

    assert await state.states_of([world.solo], actors.admin.id) == {}


@pytest.mark.integration
async def test_a_bulk_read_of_nothing_asks_nothing(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """An empty page is a real case (a library with nothing in it yet), and an `IN ()` is a
    syntax error, so it must not reach a statement at all."""
    assert await state.states_of([], actors.admin.id) == {}
    assert await state.states_of(["not-an-id"], actors.admin.id) == {}
    assert await state.states_of([world.solo], "not-an-id") == {}


# --- One person's opinion of a NAMED THING, which is the pin and nothing else -------------------
#
# Its own store and its own tables. See `kernel.content.entity_state` for why the five statements
# are written out rather than built from a table name, and why Usernames and Loops are not here.


@pytest.fixture
async def entity_state(temp_db: Database) -> EntityStateStore:
    await temp_db.initialize_schema()
    return EntityStateStore(temp_db)


#: What each kind's pin is READ back with. Written out one per kind rather than built from a table
#: name, for the reason the store's own five statements are: building SQL out of a table's name is
#: how a column name comes to be treated as data, and five statements side by side is the form in
#: which a difference between them is visible.
_PIN_OF = {
    PinnableKind.PERSON: (
        "SELECT pinned FROM person_user_state WHERE person_id = ? AND user_id = ?"
    ),
    PinnableKind.SITE: ("SELECT pinned FROM site_user_state WHERE site_id = ? AND user_id = ?"),
    PinnableKind.COLLECTION: (
        "SELECT pinned FROM collection_user_state WHERE collection_id = ? AND user_id = ?"
    ),
    PinnableKind.TAG: "SELECT pinned FROM tag_user_state WHERE tag_id = ? AND user_id = ?",
    PinnableKind.PHOTO_SET: (
        "SELECT pinned FROM photo_set_user_state WHERE photo_set_id = ? AND user_id = ?"
    ),
    PinnableKind.SONG: "SELECT pinned FROM song_user_state WHERE song_id = ? AND user_id = ?",
}


async def _a_song(database: Database) -> str:
    """A song for a pin to be about: the shared library has none of its own."""
    song_id = new_id()
    await database.execute(
        "INSERT INTO songs (id, name, name_sort, created_at) VALUES (?, 'tune', 'tune', 0)",
        (song_id,),
    )
    return song_id


@pytest.mark.parametrize("kind", list(PinnableKind))
@pytest.mark.integration
async def test_a_pin_goes_on_and_comes_off_for_every_kind_that_has_one(
    entity_state: EntityStateStore,
    world: World,
    actors: Actors,
    temp_db: Database,
    kind: PinnableKind,
) -> None:
    """All five, and it is a parametrize rather than one case because the five statements are five
    separate strings: a table named wrongly in one of them is a pin that silently writes nothing,
    and only the kind it belongs to would say so.

    Over `PinnableKind` itself rather than a list written here, so a sixth kind added to the store
    fails this until it is read back too."""
    entity_id = {
        PinnableKind.PERSON: world.person,
        PinnableKind.SITE: world.site,
        PinnableKind.COLLECTION: world.collection,
        PinnableKind.TAG: world.tag,
        PinnableKind.PHOTO_SET: world.photo_set,
        PinnableKind.SONG: await _a_song(temp_db),
    }[kind]

    reading = _PIN_OF[kind]

    assert await entity_state.set_pinned(kind, entity_id, actors.admin.id, pinned=True) is True
    row = await temp_db.fetch_one(reading, (entity_id, actors.admin.id))
    assert row is not None and row["pinned"] == 1

    assert await entity_state.set_pinned(kind, entity_id, actors.admin.id, pinned=False) is False
    row = await temp_db.fetch_one(reading, (entity_id, actors.admin.id))
    assert row is not None and row["pinned"] == 0


@pytest.mark.integration
async def test_a_pin_is_one_users_opinion_and_reaches_no_other(
    entity_state: EntityStateStore, world: World, actors: Actors, temp_db: Database
) -> None:
    """The whole reason these are tables keyed by user rather than a column on the thing itself.
    Two people sharing an install pin their own walls and neither can see the other's."""
    await entity_state.set_pinned(PinnableKind.PERSON, world.person, actors.admin.id, pinned=True)

    theirs = await temp_db.fetch_all(
        "SELECT user_id FROM person_user_state WHERE person_id = ? AND pinned = 1", (world.person,)
    )
    assert [row["user_id"] for row in theirs] == [actors.admin.id]


@pytest.mark.parametrize("kind", list(PinnableKind))
@pytest.mark.integration
async def test_a_pin_writes_what_it_replaced_for_every_kind(
    entity_state: EntityStateStore,
    world: World,
    actors: Actors,
    temp_db: Database,
    kind: PinnableKind,
) -> None:
    """The pin is an opinion and the upsert destroys the one before it, so each press appends a row
    saying what it changed, in the same transaction, or it could be missing for a press that
    happened.

    Over `PinnableKind` again rather than one case, and for the same reason the read-back above is:
    the subject kind is written from the enum's own value, and a sixth kind added to the store has
    to arrive in the record spelled the way the rest of Sift spells it.
    """
    entity_id = {
        PinnableKind.PERSON: world.person,
        PinnableKind.SITE: world.site,
        PinnableKind.COLLECTION: world.collection,
        PinnableKind.TAG: world.tag,
        PinnableKind.PHOTO_SET: world.photo_set,
        PinnableKind.SONG: await _a_song(temp_db),
    }[kind]

    await entity_state.set_pinned(kind, entity_id, actors.admin.id, pinned=True)
    await entity_state.set_pinned(kind, entity_id, actors.admin.id, pinned=False)

    rows = await temp_db.fetch_all(
        "SELECT subject_kind, kind, before, after FROM opinions WHERE subject_id = ? ORDER BY id",
        (entity_id,),
    )
    assert [
        (str(row["subject_kind"]), str(row["kind"]), row["before"], row["after"]) for row in rows
    ] == [
        # NULL and not zero on the first: there was no row at all, and "the pin was off" is a
        # stronger claim than the table could make.
        (kind.value, "pin", None, 1),
        (kind.value, "pin", 1, 0),
    ]


# --- A file's own pin, and the parts of it somebody keeps coming back to ------------------------


@pytest.mark.integration
async def test_a_files_pin_goes_on_and_comes_off(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """The sixth opinion, and the file half of the pin the five named kinds carry. Independent of
    the heart beside it: a pinned file need not be a favourite, and hearting one does not move it."""
    on = await state.set_pinned(world.solo, actors.admin.id, True)
    assert (on.pinned, on.favorite) == (True, False)
    assert (await state.state_of(world.solo, actors.admin.id)).pinned is True

    off = await state.set_pinned(world.solo, actors.admin.id, False)
    assert off.pinned is False


@pytest.mark.integration
async def test_whether_a_file_was_started_or_finished_is_read_off_the_row(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """Two questions a screen asks and neither is a column: "opened but never seen through" and
    "seen all of it at least once". Both are read from `completed_at`, which is exactly why they
    are properties and exactly why getting one wrong would be invisible."""
    blank = await state.state_of(world.solo, actors.admin.id)
    assert (blank.started, blank.finished) == (False, False)

    started = await state.record_view(world.solo, actors.admin.id, watch_ms=1_000, completed=False)
    assert (started.started, started.finished) == (True, False)

    done = await state.record_view(world.solo, actors.admin.id, watch_ms=1_000, completed=True)
    assert (done.started, done.finished) == (False, True)


@pytest.mark.integration
async def test_a_sitting_with_nothing_in_it_writes_no_row_at_all(
    state: UserStateStore, world: World, actors: Actors, temp_db: Database
) -> None:
    """A row here means somebody has DONE something with the file. Creating one to record that they
    did not would put every tile the pointer crossed into a table whose whole design is that it
    holds only the few that were touched."""
    blank = await state.record_watch_time(world.solo, actors.admin.id, watch_ms=0, resume_ms=None)

    assert blank == AssetUserState(asset_id=world.solo, user_id=actors.admin.id)
    rows = await temp_db.fetch_all(
        "SELECT 1 AS there FROM asset_user_state WHERE asset_id = ?", (world.solo,)
    )
    assert not rows


@pytest.mark.integration
async def test_where_somebody_got_to_is_kept_even_when_no_time_was_watched(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """The other half of the rule above, and the reason it is `and` rather than `or`: opening a film,
    skipping two minutes in and leaving is exactly the sitting somebody wants picked up again."""
    kept = await state.record_watch_time(world.solo, actors.admin.id, watch_ms=0, resume_ms=120_000)
    assert kept.resume_ms == 120_000


@pytest.mark.parametrize(("watch_ms", "resume_ms"), [(-1, None), (0, -1)])
@pytest.mark.integration
async def test_a_sitting_that_ran_backwards_is_refused(
    state: UserStateStore, world: World, actors: Actors, watch_ms: int, resume_ms: int | None
) -> None:
    """Neither number can be negative and both arrive from arithmetic done in a browser. Refused
    rather than clamped: a negative would be SUBTRACTED from a running total that nothing else can
    put back."""
    with pytest.raises(ValueError):
        await state.record_watch_time(
            world.solo, actors.admin.id, watch_ms=watch_ms, resume_ms=resume_ms
        )


@pytest.mark.integration
async def test_the_replay_curve_adds_up_across_sittings_and_is_per_user(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """Added in SQL rather than read-add-written, because two players can be on one clip at once and
    a round trip through Python would keep one of the two."""
    await state.add_replay_heat(world.solo, actors.admin.id, {2: 500, 5: 250})
    await state.add_replay_heat(world.solo, actors.admin.id, {2: 100})

    mine = await state.replay_heat(world.solo, actors.admin.id)
    assert (mine[2], mine[5]) == (600, 250)
    assert sum(mine) == 850, "time landed in a slice nobody played"
    assert not any(await state.replay_heat(world.solo, actors.guest.id))


@pytest.mark.integration
async def test_a_slice_nobody_can_have_played_is_dropped_rather_than_raised_on(
    state: UserStateStore, world: World, actors: Actors
) -> None:
    """This is called with arithmetic done in a browser against a duration the browser measured, and
    a rounding that lands one past the last slice is not a reason to lose the whole sitting."""
    await state.add_replay_heat(
        world.solo, actors.admin.id, {-1: 900, HEAT_BUCKETS: 900, 0: 0, 1: 400}
    )

    curve = await state.replay_heat(world.solo, actors.admin.id)
    assert curve[1] == 400
    assert sum(curve) == 400


@pytest.mark.integration
async def test_a_sitting_with_nothing_playable_in_it_opens_no_write(
    state: UserStateStore, world: World, actors: Actors, temp_db: Database
) -> None:
    """The write lock is the scarcest thing in the application and a report with nothing worth
    keeping is the common case at the end of a file that was scrubbed through."""
    await state.add_replay_heat(world.solo, actors.admin.id, {HEAT_BUCKETS + 3: 900})

    rows = await temp_db.fetch_all(
        "SELECT 1 AS there FROM asset_replay_heat WHERE asset_id = ?", (world.solo,)
    )
    assert not rows


@pytest.mark.integration
async def test_a_curve_asked_for_with_an_id_that_is_not_one_is_a_flat_line(
    state: UserStateStore, actors: Actors
) -> None:
    """A flat line and not an error, because the caller is drawing a scrubber: "no replays yet" is a
    real answer, and an id that is not an id cannot have any."""
    assert await state.replay_heat("not-an-id", actors.admin.id) == [0] * HEAT_BUCKETS


# --- how long the picture runs, read from what was kept ---------------------------------------


@pytest.mark.integration
async def test_a_stored_slice_beyond_the_end_of_the_curve_is_left_out_of_it(
    state: UserStateStore, world: World, actors: Actors, temp_db: Database
) -> None:
    """Written straight into the table, because nothing in Sift can put one there, and that is
    exactly why the guard is worth holding.

    `add_replay_heat` drops an out-of-range index on the way in, so the only way a row like this
    exists is a library written by a build whose `HEAT_BUCKETS` was LARGER than this one's. The
    curve is a fixed-width list indexed by that number: without the check, reading such a library
    would raise `IndexError` on opening a file, which is a player that will not start on somebody
    else's database rather than a curve with a bump in the wrong place.
    """
    await state.add_replay_heat(world.solo, actors.admin.id, {1: 400})
    await temp_db.execute(
        "INSERT INTO asset_replay_heat (asset_id, user_id, bucket, watched_ms, updated_at)"
        " VALUES (?, ?, ?, ?, 0)",
        (world.solo, actors.admin.id, HEAT_BUCKETS + 2, 900),
    )

    curve = await state.replay_heat(world.solo, actors.admin.id)
    assert len(curve) == HEAT_BUCKETS
    assert sum(curve) == 400


# --- Where to start from, and what counts as part-way through -----------------------------------

_ONE_MINUTE_MS = 60_000

_ONE_HOUR_MS = 60 * 60 * 1000


def test_a_video_stopped_in_the_middle_is_resumed_there() -> None:
    assert resume_point(_ONE_HOUR_MS, 20 * 60 * 1000, minimum_ms=_ONE_MINUTE_MS) == 20 * 60 * 1000


def test_a_video_shorter_than_the_minimum_always_starts_at_the_beginning() -> None:
    """The setting, and the only reason it exists: a clip is something you watch again, not
    something you go back into."""
    assert resume_point(30_000, 15_000, minimum_ms=_ONE_MINUTE_MS) is None
    # The same clip, with the minimum lowered to nothing, is remembered.
    assert resume_point(30_000, 15_000, minimum_ms=0) == 15_000


def test_the_minimum_is_a_floor_not_a_range() -> None:
    """Exactly at the minimum counts as long enough. An off-by-one here is a video that resumes
    for one person and not for another whose setting differs by a second."""
    assert resume_point(_ONE_MINUTE_MS, 30_000, minimum_ms=_ONE_MINUTE_MS) == 30_000
    assert resume_point(_ONE_MINUTE_MS - 1, 30_000, minimum_ms=_ONE_MINUTE_MS) is None


def test_barely_started_is_the_beginning() -> None:
    """Two seconds into a video is where it starts. Resuming there is not resuming."""
    assert resume_point(_ONE_HOUR_MS, 2_000, minimum_ms=_ONE_MINUTE_MS) is None


def test_watched_to_the_end_starts_over() -> None:
    """The credits are not a place to be dropped back into. This is what makes a finished video
    play from the beginning the next time it is opened."""
    assert resume_point(_ONE_HOUR_MS, _ONE_HOUR_MS - 5_000, minimum_ms=_ONE_MINUTE_MS) is None
    assert resume_point(_ONE_HOUR_MS, _ONE_HOUR_MS, minimum_ms=_ONE_MINUTE_MS) is None


def test_the_edges_scale_with_the_length() -> None:
    """Five seconds into an hour is the beginning; five seconds into a ninety-second video is not.

    Fixed edges would make a short-but-eligible video unresumable at both ends at once: the first
    five seconds and the last fifteen of a twenty-second clip leave nothing in between.
    """
    ninety = 90_000
    # 5% of ninety seconds is 4.5, so four seconds in is still the beginning and five is not.
    assert resume_point(ninety, 4_000, minimum_ms=0) is None
    assert resume_point(ninety, 5_000, minimum_ms=0) == 5_000
    # 10% off the end is nine seconds, not fifteen.
    assert resume_point(ninety, 80_000, minimum_ms=0) == 80_000
    assert resume_point(ninety, 82_000, minimum_ms=0) is None


def test_something_with_no_timeline_has_no_place_in_it() -> None:
    """A photograph. The player never asks, but the route that stores this is reachable for any
    asset somebody can see, and a stored position on a still is a number nothing could ever use."""
    assert resume_point(None, 5_000, minimum_ms=0) is None
    assert resume_point(0, 5_000, minimum_ms=0) is None


def test_no_position_reported_is_no_position_stored() -> None:
    assert resume_point(_ONE_HOUR_MS, None, minimum_ms=_ONE_MINUTE_MS) is None
    assert resume_point(_ONE_HOUR_MS, 0, minimum_ms=_ONE_MINUTE_MS) is None


def test_resuming_switched_off_has_no_place_in_anything() -> None:
    """The two settings are not one setting. A minimum nothing reaches still KEEPS a position for a
    long enough video; off keeps none, ever, for anything, and `None` is how off travels, all the
    way down to the NULL the statement binds."""
    assert resume_point(_ONE_HOUR_MS, 20 * 60 * 1000, minimum_ms=None) is None


async def test_the_user_decides_whether_there_is_a_minimum_at_all() -> None:
    """Two preferences read as one number, which is the shape everything downstream wants: a
    minimum in milliseconds, or None for "this user keeps no positions"."""
    kept = {"playback.resume_enabled": True, "playback.resume_minimum_seconds": 180}

    async def reading(_user_id: str, key: str) -> Any:
        return kept[key]

    assert await resume_minimum_ms(reading, "someone") == 180_000

    kept["playback.resume_enabled"] = False
    assert await resume_minimum_ms(reading, "someone") is None

    # Zero is a real answer ("every video, however short"), and is not the same as off.
    kept["playback.resume_enabled"] = True
    kept["playback.resume_minimum_seconds"] = 0
    assert await resume_minimum_ms(reading, "someone") == 0


#: The edges of the rule, as (length, position, minimum) with what each one should come to. Every
#: boundary above appears here, because this table is what the SQL form is held to.
_RESUME_EDGES = [
    (_ONE_HOUR_MS, 20 * 60 * 1000, _ONE_MINUTE_MS),
    (30_000, 15_000, _ONE_MINUTE_MS),
    (30_000, 15_000, 0),
    (_ONE_MINUTE_MS, 30_000, _ONE_MINUTE_MS),
    (_ONE_MINUTE_MS - 1, 30_000, _ONE_MINUTE_MS),
    (_ONE_HOUR_MS, 2_000, _ONE_MINUTE_MS),
    (_ONE_HOUR_MS, _ONE_HOUR_MS - 5_000, _ONE_MINUTE_MS),
    (_ONE_HOUR_MS, _ONE_HOUR_MS, _ONE_MINUTE_MS),
    (90_000, 4_000, 0),
    (90_000, 5_000, 0),
    (90_000, 80_000, 0),
    (90_000, 82_000, 0),
    (100_000, 5_000, 0),
    (100_000, 5_001, 0),
    (100_000, 89_999, 0),
    (100_000, 90_000, 0),
    (None, 5_000, 0),
    (0, 5_000, 0),
    (_ONE_HOUR_MS, None, _ONE_MINUTE_MS),
    (_ONE_HOUR_MS, 0, _ONE_MINUTE_MS),
    (_ONE_HOUR_MS, 20 * 60 * 1000, None),
]


@pytest.mark.parametrize(("duration_ms", "position_ms", "minimum_ms"), _RESUME_EDGES)
def test_the_sql_form_of_the_rule_answers_exactly_what_the_function_does(
    duration_ms: int | None, position_ms: int | None, minimum_ms: int | None
) -> None:
    """One rule, two engines, and this is what holds them together.

    `resume_point` answers for a tile, which is built in Python. `RESUMING` answers for a filter
    and for a facet count, which are one statement inside the permission rules: there is no way
    to run a page of rows past a Python predicate without moving the filtering out of the statement
    that decides visibility. So the shape is written twice and the NUMBERS are written once, and
    this runs both over every edge of the rule.

    It also proves the predicate is never NULL. `viewed:continue` can be negated, and `NOT NULL` is
    not true: a predicate that can go NULL is a filter that silently stops applying under a NOT,
    which is the trap the media filter beside it records.
    """
    # `noqa: S608`: the rule forbids SQL built from a variable and is right to. What is spliced
    # is a constant from the module under test, and the three values still bind.
    statement = (
        "SELECT "  # noqa: S608
        + RESUMING.format(duration="d", position="p", minimum="m")
        + " FROM t"
    )
    connect = sqlite3.connect  # nosemgrep: sift-no-database-driver-outside-kernel
    with closing(connect(":memory:")) as db:
        db.execute("CREATE TABLE t (d INTEGER, p INTEGER, m INTEGER)")
        db.execute(
            "INSERT INTO t (d, p, m) VALUES (?, ?, ?)", (duration_ms, position_ms, minimum_ms)
        )
        # The statement is a module constant with three column NAMES formatted in; every value
        # binds.
        answered = db.execute(statement).fetchone()[0]  # nosemgrep: sift-no-string-built-sql

    assert answered is not None, "the predicate went NULL, so a negated filter would stop applying"
    wanted = resume_point(duration_ms, position_ms, minimum_ms=minimum_ms) is not None
    assert bool(answered) is wanted


async def test_a_library_at_content_version_24_gains_the_still_moment_with_every_still_unmeasured(
    temp_db: Database,
) -> None:
    """Version 25 adds where a tile's still was cut. A library from before it has only first-frame
    stills, so every row arrives unmeasured (NULL), which the hover clip reads as the first frame.
    Version 26 takes the partial index over those rows away again: nothing reads it."""
    async with temp_db.write() as connection:
        await schema.initialize_library(connection, on_disk=0)
        # Version 24's assets table: today's, less the one column version 25 adds.
        today = schema._CREATE_ASSETS
        marker = today.index("The moment the tile's still was cut at")
        cut = today.rfind(",\n", 0, marker)
        # The statement is this module's own constant cut short, not a value from anywhere.
        await connection.execute(today[:cut] + "\n)")  # nosemgrep: sift-no-string-built-sql
        # And the content tables beside it, which every later step may read or write.
        for table in (
            schema._CREATE_ASSET_LOCATIONS,
            schema._CREATE_DERIVATIVES,
            schema._CREATE_FILE_VERDICTS,
            schema._CREATE_ASSET_PROBES,
            schema._CREATE_OPINIONS,
        ):
            await connection.execute(table)
        await connection.execute(
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 1)",
            ("01HX0000000000000000000009", "identity-9"),
        )
        await schema.initialize_content(connection, on_disk=24)

    (row,) = await temp_db.fetch_all("SELECT still_at_ms FROM assets")
    assert row["still_at_ms"] is None
    indexes = {
        str(one["name"])
        for one in await temp_db.fetch_all("SELECT name FROM pragma_index_list('assets')")
    }
    assert "ix_assets_still_unmeasured" not in indexes


@pytest.mark.integration
async def test_a_library_at_content_version_26_loses_the_hover_clips_cut_from_an_avif_cover(
    temp_db: Database, content_store: ContentStore, library_root: LibraryRoot, settings: Any
) -> None:
    """An animated AVIF's hover clip was cut from its still cover, so the clip goes and the
    Generate pass cuts it again; its thumbnail, and every other file's clip, stay."""
    # Two different files: the same bytes under two names are one asset (identity is a sample).
    avif = place("accepted.gif", library_root, "clips/moving.avif")
    plain = place("accepted.mp4", library_root, "clips/plain.mp4")
    moving = await content_store.ingest(
        checked(avif, settings), root_id=library_root.id, rel_path="clips/moving.avif"
    )
    other = await content_store.ingest(
        checked(plain, settings), root_id=library_root.id, rel_path="clips/plain.mp4"
    )
    async with temp_db.write() as connection:
        await connection.execute(
            "UPDATE assets SET media_type = 'gif', mime = 'image/avif' WHERE id = ?",
            (moving.asset.id,),
        )
    for one in (moving, other):
        await content_store.add_derivative(one.asset.id, DerivativeKind.THUMB, extension="jpg")
        await content_store.add_derivative(one.asset.id, DerivativeKind.PREVIEW, extension="mp4")
    async with temp_db.write() as connection:
        await schema.initialize_content(connection, on_disk=26)
        rows = await connection.execute_fetchall(
            "SELECT asset_id, kind FROM derivatives ORDER BY asset_id, kind"
        )
    kept = {(str(row[0]), str(row[1])) for row in rows}
    assert (moving.asset.id, "preview") not in kept
    assert (moving.asset.id, "thumb") in kept
    assert (other.asset.id, "preview") in kept
    assert (other.asset.id, "thumb") in kept


async def test_a_library_at_content_version_27_leaves_its_webp_and_heif_rows_to_be_read_again(
    temp_db: Database,
) -> None:
    """Every WebP, AVIF and HEIF row stays below the classifier's line, whatever it was filed as
    and whether the MIME or only the name says so; every other row is stamped and never read."""
    async with temp_db.write() as connection:
        await schema.initialize_library(connection, on_disk=0)
        await schema.initialize_content(connection, on_disk=0)
        for index, (name, kind, mime) in enumerate(
            (
                ("twirl.webp", "video", "image/webp"),
                ("renamed.bin", "video", "image/webp"),
                ("motion.avif", "image", None),
                ("phone.HEIC", "image", "image/heic"),
                ("holiday.mp4", "video", "video/mp4"),
                ("photo.jpg", "image", "image/jpeg"),
                (None, "image", None),
            )
        ):
            await connection.execute(
                "INSERT INTO assets (id, identity, media_type, mime, original_filename, added_at,"
                " classified_version) VALUES (?, ?, ?, ?, ?, 1, 1)",
                (f"01HX00000000000000000000{index:02d}", f"identity-{index}", kind, mime, name),
            )
        await schema.initialize_content(connection, on_disk=27)
        rows = await connection.execute_fetchall(
            "SELECT original_filename, classified_version FROM assets ORDER BY id"
        )
    left = {str(row[0]) for row in rows if row[1] < CLASSIFIER_VERSION}
    assert left == {"twirl.webp", "renamed.bin", "motion.avif", "phone.HEIC"}
    assert {row[1] for row in rows if str(row[0]) not in left} == {CLASSIFIER_VERSION}


async def test_a_library_at_content_version_28_reads_its_heif_stills_again(
    temp_db: Database,
) -> None:
    """Every HEIF still (HEIC, an AVIF still) loses its probe time and its picture fingerprint, so
    the probe and the fingerprints run on the whole picture the door reads now; a JPEG, a video
    and an animated AVIF (a moving picture, read from its frames already) keep theirs."""
    async with temp_db.write() as connection:
        await schema.initialize_library(connection, on_disk=0)
        await schema.initialize_content(connection, on_disk=0)
        for index, (name, kind, mime) in enumerate(
            (
                ("phone.HEIC", "image", "image/heic"),
                ("still.avif", "image", "image/avif"),
                ("motion.avif", "gif", "image/avif"),
                ("holiday.mp4", "video", "video/mp4"),
                ("photo.jpg", "image", "image/jpeg"),
            )
        ):
            await connection.execute(
                "INSERT INTO assets (id, identity, media_type, mime, original_filename, added_at,"
                " classified_version, probed_at, phash) VALUES (?, ?, ?, ?, ?, 1, 2, 5, 'abcd')",
                (f"01HX00000000000000000000{index:02d}", f"identity-{index}", kind, mime, name),
            )
        await schema.initialize_content(connection, on_disk=28)
        rows = await connection.execute_fetchall(
            "SELECT original_filename, probed_at, phash FROM assets ORDER BY id"
        )
    again = {str(row[0]) for row in rows if row[1] is None and row[2] is None}
    assert again == {"phone.HEIC", "still.avif"}
    assert all(row[1] == 5 and row[2] == "abcd" for row in rows if str(row[0]) not in again)


async def test_a_library_at_content_version_25_loses_the_index_over_the_unmeasured_stills(
    temp_db: Database,
) -> None:
    """The partial index a version 25 library was given for a one-time pass over its stills goes,
    and a new library never has it: every row written paid for an index nothing reads."""
    listed = "SELECT name FROM pragma_index_list('assets')"
    async with temp_db.write() as connection:
        await schema.initialize_library(connection, on_disk=0)
        await schema.initialize_content(connection, on_disk=0)
        assert "ix_assets_still_unmeasured" not in {
            str(row[0]) for row in await connection.execute_fetchall(listed)
        }
        await connection.execute(
            "CREATE INDEX ix_assets_still_unmeasured ON assets(id) WHERE still_at_ms IS NULL"
        )
        await schema.initialize_content(connection, on_disk=25)
        assert "ix_assets_still_unmeasured" not in {
            str(row[0]) for row in await connection.execute_fetchall(listed)
        }
