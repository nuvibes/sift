# SPDX-License-Identifier: AGPL-3.0-or-later
"""Making a browser forget every picture it was given.

A picture at a keepable address is served again from the browser's own store, with no permission
check; the stamp rides in every such address, and raising it makes each unreachable. It goes up
**in the same transaction** as the change that needs it, so nothing concealed stays readable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from sift.kernel.access.stamps import bump_stamps_for_object, users_that_may_gain
from sift.kernel.cache_stamp import bump_cache_stamp, bump_every_cache_stamp
from sift.kernel.db import Database

pytestmark = pytest.mark.unit

_CREATE = """
CREATE TABLE users (
  id          TEXT PRIMARY KEY,
  cache_stamp INTEGER NOT NULL DEFAULT 0
)
"""

# A grant naming a user and their own hiding, with only the columns this module reads.
_CREATE_GRANTS = """
CREATE TABLE acl_grants (
  object_type     TEXT NOT NULL,
  object_id       TEXT,
  subject_user_id TEXT NOT NULL,
  effect          TEXT NOT NULL DEFAULT 'share' CHECK(effect IN ('share','restrict'))
)
"""

_STATE_TABLES = (
    "CREATE TABLE tag_user_state (tag_id TEXT, user_id TEXT, hidden INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE person_user_state"
    " (person_id TEXT, user_id TEXT, hidden INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE collection_user_state"
    " (collection_id TEXT, user_id TEXT, hidden INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE site_user_state (site_id TEXT, user_id TEXT, hidden INTEGER NOT NULL DEFAULT 0)",
)

_ADD = "INSERT INTO users (id) VALUES (?)"
_READ = "SELECT cache_stamp FROM users WHERE id = ?"
_GRANT = "INSERT INTO acl_grants (object_type, object_id, subject_user_id) VALUES (?, ?, ?)"
_GRANT_WITH_EFFECT = (
    "INSERT INTO acl_grants (object_type, object_id, subject_user_id, effect) VALUES (?, ?, ?, ?)"
)
_HIDE_TAG = "INSERT INTO tag_user_state (tag_id, user_id, hidden) VALUES (?, ?, 1)"
_HIDE_PERSON = "INSERT INTO person_user_state (person_id, user_id, hidden) VALUES (?, ?, 1)"


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    """Three users starting where an upgraded install starts them; the third is the control."""
    db = Database(tmp_path / "stamps.sqlite3")
    await db.connect()
    async with db.write() as connection:
        await connection.execute(_CREATE)
        await connection.execute(_CREATE_GRANTS)
        for statement in _STATE_TABLES:
            await connection.execute(statement)
        for user_id in ("admin", "guest", "bystander"):
            await connection.execute(_ADD, (user_id,))
    yield db
    await db.close()


async def _stamp(database: Database, user_id: str) -> int:
    row = await database.fetch_one(_READ, (user_id,))
    assert row is not None
    return int(row["cache_stamp"])


async def test_raising_one_user_leaves_the_other_alone(database: Database) -> None:
    """Raising one user leaves the others alone: hiding is personal, and a sweep would re-download a
    shared library on every change."""
    async with database.write() as connection:
        await bump_cache_stamp(connection, "admin")

    assert await _stamp(database, "admin") == 1
    assert await _stamp(database, "guest") == 0


async def test_it_keeps_going_up_rather_than_flipping(database: Database) -> None:
    """It goes up rather than toggling, or hide, unhide, hide would return to a held value."""
    async with database.write() as connection:
        await bump_cache_stamp(connection, "admin")
        await bump_cache_stamp(connection, "admin")
        await bump_cache_stamp(connection, "admin")

    assert await _stamp(database, "admin") == 3


async def test_everybody_together_for_a_change_nobody_owns(database: Database) -> None:
    """A file leaving the library bumps everybody: there is no asking who was shown it."""
    async with database.write() as connection:
        await bump_every_cache_stamp(connection)

    assert await _stamp(database, "admin") == 1
    assert await _stamp(database, "guest") == 1


async def test_a_change_that_fails_takes_the_stamp_back_with_it(database: Database) -> None:
    """A change that fails takes the stamp back with it: one transaction."""
    with pytest.raises(RuntimeError):
        async with database.write() as connection:
            await bump_cache_stamp(connection, "admin")
            raise RuntimeError("the change this note was for did not land")

    assert await _stamp(database, "admin") == 0


async def test_a_share_on_the_object_is_what_gets_the_bump(database: Database) -> None:
    """A share on the object gets the bump: a file leaving a shared collection ends that user's
    access with nothing of theirs written."""
    async with database.write() as connection:
        await connection.execute(_GRANT, ("collection", "col-1", "guest"))
        await bump_stamps_for_object(connection, "collection", "col-1")

    assert await _stamp(database, "guest") == 1
    assert await _stamp(database, "admin") == 0
    assert await _stamp(database, "bystander") == 0


async def test_hiding_the_object_gets_the_bump_too(database: Database) -> None:
    """Hiding the object gets the bump too: a file joining a person somebody hid is concealed from
    them."""
    async with database.write() as connection:
        await connection.execute(_HIDE_PERSON, ("person-1", "admin"))
        await bump_stamps_for_object(connection, "person", "person-1")

    assert await _stamp(database, "admin") == 1
    assert await _stamp(database, "guest") == 0
    assert await _stamp(database, "bystander") == 0


async def test_a_user_that_stopped_hiding_it_is_left_alone(database: Database) -> None:
    """A user whose hiding row is back at zero is not bumped: the flag is read, not the row."""
    async with database.write() as connection:
        await connection.execute(
            "INSERT INTO tag_user_state (tag_id, user_id, hidden) VALUES (?, ?, 0)",
            ("tag-1", "admin"),
        )
        await bump_stamps_for_object(connection, "tag", "tag-1")

    assert await _stamp(database, "admin") == 0


async def test_a_grant_on_a_different_object_of_the_same_kind_is_not_it(database: Database) -> None:
    """A grant on another object of the same kind does not match."""
    async with database.write() as connection:
        await connection.execute(_GRANT, ("collection", "col-other", "guest"))
        await connection.execute(_HIDE_TAG, ("tag-other", "admin"))
        await bump_stamps_for_object(connection, "collection", "col-1")

    assert await _stamp(database, "guest") == 0
    assert await _stamp(database, "admin") == 0


async def test_a_grant_of_the_same_id_under_a_different_kind_is_not_it(database: Database) -> None:
    """The kind is part of the match: ids are unique per kind only."""
    async with database.write() as connection:
        await connection.execute(_GRANT, ("person", "same-id", "guest"))
        await bump_stamps_for_object(connection, "collection", "same-id")

    assert await _stamp(database, "guest") == 0


async def test_one_user_reached_both_ways_still_moves_by_one(database: Database) -> None:
    """Shared with and hidden by one user moves the stamp once."""
    async with database.write() as connection:
        await connection.execute(_GRANT, ("tag", "tag-1", "admin"))
        await connection.execute(_HIDE_TAG, ("tag-1", "admin"))
        await bump_stamps_for_object(connection, "tag", "tag-1")

    assert await _stamp(database, "admin") == 1


async def test_nobody_involved_costs_nothing(database: Database) -> None:
    """Nobody involved matches no rows, the cost on a scan's per-file path."""
    async with database.write() as connection:
        await bump_stamps_for_object(connection, "tag", "tag-nobody-cares-about")

    assert await _stamp(database, "admin") == 0
    assert await _stamp(database, "guest") == 0


@pytest.mark.parametrize("kind", ["global", "root", "folder", "item", "", "users"])
async def test_anything_without_membership_is_refused(database: Database, kind: str) -> None:
    """A physical type is refused loudly: a folder or a file is the wrong function's business, and
    a silent no-op would look like nothing shared."""
    with pytest.raises(ValueError, match="not a logical object"):
        async with database.write() as connection:
            await bump_stamps_for_object(connection, kind, "whatever")


async def test_an_id_naming_nobody_changes_nothing(database: Database) -> None:
    """An id naming no user changes nothing rather than failing a hide."""
    async with database.write() as connection:
        await bump_cache_stamp(connection, "nobody-at-all")

    assert await _stamp(database, "admin") == 0
    assert await _stamp(database, "guest") == 0


def test_every_logical_object_has_a_statement_that_bumps_for_it() -> None:
    """Every `LOGICAL_TYPES` kind has a bump statement, the agreement kept here because the map
    cannot import the access layer: a missing one raises a 500 mid-write."""
    from sift.kernel.access.stamps import _BUMP_FOR_OBJECT
    from sift.kernel.access.viewer import LOGICAL_TYPES

    assert set(_BUMP_FOR_OBJECT) == {str(kind) for kind in LOGICAL_TYPES}


# --- who a file arriving could become visible to
#
# The grants table read the other way round. Admins are settled when a message is sent, since who
# is an admin moves.


async def test_a_user_holding_a_share_could_gain_a_file(database: Database) -> None:
    async with database.write() as connection:
        await connection.execute(_GRANT_WITH_EFFECT, ("tag", "t1", "guest", "share"))

        assert (await users_that_may_gain(connection)).users == frozenset({"guest"})


async def test_a_user_that_is_only_restricted_could_not(database: Database) -> None:
    """A restrict is never why a new file becomes visible."""
    async with database.write() as connection:
        await connection.execute(_GRANT_WITH_EFFECT, ("tag", "t1", "guest", "restrict"))

        assert (await users_that_may_gain(connection)).users == frozenset()


async def test_a_user_reached_twice_is_named_once(database: Database) -> None:
    """A user reached twice is named once: the audience is a set of users."""
    async with database.write() as connection:
        await connection.execute(_GRANT_WITH_EFFECT, ("tag", "t1", "guest", "share"))
        await connection.execute(_GRANT_WITH_EFFECT, ("person", "p1", "guest", "share"))

        assert (await users_that_may_gain(connection)).users == frozenset({"guest"})


async def test_an_install_with_nothing_shared_names_nobody(database: Database) -> None:
    """Nothing shared names nobody, cheaply, beside hashing every byte of the file."""
    async with database.write() as connection:
        gained = await users_that_may_gain(connection)

        assert gained.users == frozenset()
        assert not gained.every_admin, "admins are a reach settled when a message is sent"
