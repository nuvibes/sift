# SPDX-License-Identifier: AGPL-3.0-or-later
"""One row per sitting, and the numbers a file's own line will be drawn from.

What is asserted is that the sitting the client already sends arrives WHOLE. The counters beside it
have their own tests and are not re-checked here: the point of this table is everything the
counters cannot be un-summed back into: when it happened, how long it lasted, where it stopped,
and which parts were on screen.
"""

from __future__ import annotations

import json

import pytest

from sift.kernel.access import Repository
from sift.kernel.db import Database
from sift.slices.player.plays import Place, plays_of_asset, record_play
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.unit


async def _rows(database: Database) -> list[dict[str, object]]:
    found = await database.fetch_all("SELECT * FROM plays ORDER BY id")
    return [dict(row) for row in found]


async def test_a_sitting_is_kept_whole(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    await record_play(
        temp_db,
        user_id=actors.admin.id,
        asset_id=world.solo,
        watch_ms=42_000,
        already_reported_ms=None,
        position_ms=41_500,
        heat={3: 1200, 1: 800},
    )

    (row,) = await _rows(temp_db)
    assert row["user_id"] == actors.admin.id and row["asset_id"] == world.solo
    assert row["duration_ms"] == 42_000
    assert row["ended_at_ms"] == 41_500
    # Sparse and in order, so a glance at a long film is two entries rather than a hundred zeroes.
    assert json.loads(str(row["heat"])) == {"1": 800, "3": 1200}
    # Derived: the report landed at `made_at` and the sitting was 42 seconds of it.
    assert int(str(row["started_at"])) == int(str(row["made_at"])) - 42


async def test_a_sitting_records_the_length_of_its_file(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The file's length rides with the sitting, as its kind does, so whatever judges the sitting
    later reads the file as it was, and never has to reach the file at all."""
    await record_play(
        temp_db,
        user_id=actors.admin.id,
        asset_id=world.solo,
        watch_ms=5_000,
        already_reported_ms=None,
        position_ms=None,
        heat={},
        length_ms=754_000,
    )

    (row,) = await _rows(temp_db)
    assert row["length_ms"] == 754_000


async def test_no_replay_map_is_not_an_empty_one(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """NULL and `{}` must not be the same bytes: one is "nothing was played", the other is a map."""
    await record_play(
        temp_db,
        user_id=actors.admin.id,
        asset_id=world.solo,
        watch_ms=1_000,
        already_reported_ms=None,
        position_ms=None,
        heat={},
    )

    (row,) = await _rows(temp_db)
    assert row["heat"] is None and row["ended_at_ms"] is None


async def test_the_second_piece_of_a_sitting_counts_back_over_the_whole_of_it(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The player sends one report when a sitting earns a view and another on the way out.

    Nothing in either says which sitting it belongs to, so the two are two rows: the known limit
    named in `record_play`. What `already_reported` CAN settle without a sitting id is where the
    sitting BEGAN: each row counts back over the whole sitting rather than over its own piece, so
    two reports twenty seconds apart name the same start. Asserted against each row's own `made_at`
    rather than against each other, because both land in the same instant here and twenty seconds
    apart in life, and because a machine's wall clock can step backwards.
    """
    await record_play(
        temp_db,
        user_id=actors.admin.id,
        asset_id=world.solo,
        watch_ms=30_000,
        already_reported_ms=None,
        position_ms=30_000,
        heat={},
    )
    await record_play(
        temp_db,
        user_id=actors.admin.id,
        asset_id=world.solo,
        watch_ms=20_000,
        already_reported_ms=30_000,
        position_ms=50_000,
        heat={},
    )

    first, second = await _rows(temp_db)
    assert int(str(first["started_at"])) == int(str(first["made_at"])) - 30
    assert int(str(second["started_at"])) == int(str(second["made_at"])) - 50
    # Each row's own piece, never a running total: the times add up to the sitting.
    assert first["duration_ms"] == 30_000 and second["duration_ms"] == 20_000


async def test_what_a_file_has_been_watched_is_three_numbers(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    for milliseconds in (10_000, 5_000):
        await record_play(
            temp_db,
            user_id=actors.admin.id,
            asset_id=world.solo,
            watch_ms=milliseconds,
            already_reported_ms=None,
            position_ms=None,
            heat={},
        )
    await record_play(
        temp_db,
        user_id=actors.guest.id,
        asset_id=world.solo,
        watch_ms=99_000,
        already_reported_ms=None,
        position_ms=None,
        heat={},
    )

    watched = await plays_of_asset(temp_db, actors.admin.id, world.solo)

    # Per user, like the heart and the replay curve: what somebody else watched is not a fact
    # about the file.
    assert watched.sittings == 2
    assert watched.watched_ms == 15_000
    assert watched.last_at is not None


async def test_a_file_nobody_has_watched_answers_nothing_rather_than_no_row(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    watched = await plays_of_asset(temp_db, actors.admin.id, world.twin)

    assert watched == type(watched)(sittings=0, watched_ms=0, last_at=None)


async def test_a_sitting_outlives_the_file_it_measured(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """A play outlives its file.

    A play is a fact about the USER (the one record of something somebody did here for its own
    sake), so with a cascading file key a duplicate cleanup or a moved-out folder would rewrite
    last year's hours watched with nothing anywhere to say so. The user's key stays, because a user
    removed IS the deliberate forget. See `schema.py`, and
    `tests/gates/test_every_store_lets_go.py`, where the keyless column is declared as meant.
    """
    await record_play(
        temp_db,
        user_id=actors.admin.id,
        asset_id=world.solo,
        watch_ms=1_000,
        already_reported_ms=None,
        position_ms=None,
        heat={},
    )

    await temp_db.execute("DELETE FROM assets WHERE id = ?", (world.solo,))

    (row,) = await _rows(temp_db)
    assert row["asset_id"] == world.solo and row["duration_ms"] == 1_000


async def test_two_pieces_of_one_sitting_are_one_row(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The count of sittings, made true by the id the client mints.

    Without it the time and the replay map are right in total and the COUNT is not: a file left
    open long enough to earn a view reports twice and reads as two sittings, and a picture (which
    reports an empty piece when it is opened and the time when it is left) reads as two every single
    time, one of them zero-length.
    """
    for watch_ms, already, position, heat in (
        (30_000, None, 30_000, {1: 9_000}),
        (20_000, 30_000, 50_000, {1: 1_000, 4: 5_000}),
    ):
        await record_play(
            temp_db,
            user_id=actors.admin.id,
            asset_id=world.solo,
            watch_ms=watch_ms,
            already_reported_ms=already,
            position_ms=position,
            heat=heat,
            sitting="one-sitting",
            seeks=2,
        )

    (row,) = await _rows(temp_db)
    # The pieces add up, and the position is wherever the last piece left it.
    assert row["duration_ms"] == 50_000 and row["ended_at_ms"] == 50_000
    assert row["seeks"] == 4
    # Slice 1 was played in BOTH pieces, so its times are added rather than replaced. Keeping the
    # later map alone would throw away the first half of the sitting.
    assert json.loads(str(row["heat"])) == {"1": 10_000, "4": 5_000}
    # The sitting began where its FIRST piece said, not where the second one would have derived.
    assert int(str(row["started_at"])) == int(str(row["made_at"])) - 30


@pytest.mark.parametrize("stored", ["not json", "[1, 2]", '{"one": 5}'])
async def test_a_stored_replay_map_nobody_can_read_costs_the_map_and_not_the_sitting(
    temp_db: Database, access: Repository, world: World, actors: Actors, stored: str
) -> None:
    """Only this module writes the column, so an unreadable map means a database edited by some
    other tool. The sitting's time still adds up; the map starts again from the piece arriving."""
    for watch_ms, already, heat in ((30_000, None, {1: 9_000}), (20_000, 30_000, {4: 5_000})):
        await record_play(
            temp_db,
            user_id=actors.admin.id,
            asset_id=world.solo,
            watch_ms=watch_ms,
            already_reported_ms=already,
            position_ms=watch_ms,
            heat=heat,
            sitting="one-sitting",
        )
        if already is None:
            async with temp_db.write() as connection:
                await connection.execute("UPDATE plays SET heat = ?", (stored,))

    (row,) = await _rows(temp_db)
    assert row["duration_ms"] == 50_000
    assert json.loads(str(row["heat"])) == {"4": 5_000}


async def test_a_picture_is_one_sitting_and_not_two(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The zero-length piece a still sends on the way in stops being a row of its own."""
    await record_play(
        temp_db,
        user_id=actors.admin.id,
        asset_id=world.solo,
        watch_ms=0,
        already_reported_ms=None,
        position_ms=0,
        heat={},
        sitting="a-look",
    )
    await record_play(
        temp_db,
        user_id=actors.admin.id,
        asset_id=world.solo,
        watch_ms=4_000,
        already_reported_ms=0,
        position_ms=0,
        heat={},
        sitting="a-look",
    )

    (row,) = await _rows(temp_db)
    assert row["duration_ms"] == 4_000
    watched = await plays_of_asset(temp_db, actors.admin.id, world.solo)
    assert watched.sittings == 1 and watched.watched_ms == 4_000


async def test_two_sittings_with_one_file_stay_two_rows(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The other direction, and it is the one a merge would quietly break: watching something twice
    in an evening is two sittings, and the id is what tells them apart."""
    for name in ("first-look", "second-look"):
        await record_play(
            temp_db,
            user_id=actors.admin.id,
            asset_id=world.solo,
            watch_ms=5_000,
            already_reported_ms=None,
            position_ms=5_000,
            heat={},
            sitting=name,
        )

    assert len(await _rows(temp_db)) == 2


async def test_a_report_with_no_sitting_writes_its_own_row(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """Not every screen that sends a report mints one, and a client that has not been rebuilt is
    not a client whose watching should be dropped."""
    for _ in range(2):
        await record_play(
            temp_db,
            user_id=actors.admin.id,
            asset_id=world.solo,
            watch_ms=1_000,
            already_reported_ms=None,
            position_ms=None,
            heat={},
        )

    rows = await _rows(temp_db)
    assert len(rows) == 2 and all(row["sitting"] is None for row in rows)


async def test_where_a_sitting_happened_is_written_by_its_first_piece(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """The screen, the file's kind and what it was opened from, kept with the sitting.

    Written by the FIRST piece and never by a later one, the way `started_at` is: where a sitting
    happened is a fact about its beginning, and a second piece claiming otherwise would be
    rewriting it rather than adding to it.
    """
    first = Place(
        screen="theater",
        opened_from=None,
        kind="image",
        loop_id=None,
        kept_filter_id=None,
        theater_session="an-evening-wall",
    )
    later = Place(screen="panel", opened_from="search", kind="video", loop_id="a-loop")
    for watch_ms, already, place in ((0, None, first), (6_000, 0, later)):
        await record_play(
            temp_db,
            user_id=actors.admin.id,
            asset_id=world.solo,
            watch_ms=watch_ms,
            already_reported_ms=already,
            position_ms=0,
            heat={},
            sitting="one-look",
            place=place,
        )

    (row,) = await _rows(temp_db)
    assert (row["screen"], row["kind"], row["theater_session"]) == (
        "theater",
        "image",
        "an-evening-wall",
    )
    assert row["opened_from"] is None and row["loop_id"] is None
    assert row["duration_ms"] == 6_000


async def test_a_report_that_says_nothing_about_where_stores_nothing(
    temp_db: Database, access: Repository, world: World, actors: Actors
) -> None:
    """A client that has not been rebuilt sends no place, and NULL is the only honest answer."""
    await record_play(
        temp_db,
        user_id=actors.admin.id,
        asset_id=world.solo,
        watch_ms=1_000,
        already_reported_ms=None,
        position_ms=None,
        heat={},
    )

    (row,) = await _rows(temp_db)
    for column in ("screen", "opened_from", "kind", "loop_id", "kept_filter_id", "theater_session"):
        assert row[column] is None, column
