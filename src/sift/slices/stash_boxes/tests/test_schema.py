# SPDX-License-Identifier: AGPL-3.0-or-later
"""The component's schema: what a new library gets, and the steps a library at the baseline takes.

Version 17 regrades every kept certain answer by what its record can prove (see the tests at the
end). Version 16 gives `stash_boxes.sites_are` the CHECK a new library's CREATE carries, on a library
whose column was added without one. It is written into the stored definition rather than by
rebuilding the table, because `stash_boxes` is a parent of six tables that cascade.
"""

from __future__ import annotations

import pytest

from sift.kernel.db import Connection, Database
from sift.kernel.records import FoundRecord, Subject
from sift.slices.stash_boxes.adapter import EXACT, as_json
from sift.slices.stash_boxes.schema import (
    _CREATE_MATCHES,
    _CREATE_SITE_LINKS,
    STASH_BOX_VERSION,
    initialize_stash_boxes,
)

pytestmark = pytest.mark.anyio

_TABLES = "SELECT name FROM sqlite_master WHERE type = 'table'"
_BOXES = "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'stash_boxes'"


async def _what_it_depends_on(connection: Connection) -> None:
    """The one table from another component this one's tables point at, as narrow as it can be:
    `stash_box_scans` and `asset_stash_box_matches` key into `assets`."""
    await connection.execute("CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY)")


async def _what_later_steps_read(connection: Connection) -> None:
    """The tables the steps after v16 read, as narrow as they can be: a file's length for v17 and
    a Site's parent and maker for v18."""
    await connection.execute(
        "CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, duration_ms INTEGER)"
    )
    await connection.execute(
        "CREATE TABLE IF NOT EXISTS sites (id TEXT PRIMARY KEY, name TEXT, parent_id TEXT,"
        " created_by_box_id TEXT, created_by_kind TEXT, created_by_via TEXT,"
        " created_by_user_id TEXT, created_at INTEGER)"
    )
    await connection.execute(_CREATE_MATCHES)
    await connection.execute(_CREATE_SITE_LINKS)


async def _brought_forward_from_15(connection: Connection) -> None:
    """A library at version 15 brought forward through every later step."""
    await _what_later_steps_read(connection)
    await initialize_stash_boxes(connection, 15)


async def _tables(database: Database) -> set[str]:
    return {str(row["name"]) for row in await database.fetch_all(_TABLES)}


async def test_a_fresh_database_gets_every_table(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        await _what_it_depends_on(connection)
        await initialize_stash_boxes(connection, 0)

    held = await _tables(temp_db)
    assert {
        "stash_boxes",
        "stash_box_answers",
        "person_stash_box_links",
        "site_stash_box_links",
        "tag_stash_box_links",
        "asset_stash_box_matches",
        "stash_box_scans",
        "stash_box_undecided",
        "stash_box_catch_ups",
        "stash_box_kept",
    } <= held


async def test_a_library_at_this_version_is_left_alone(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        await initialize_stash_boxes(connection, STASH_BOX_VERSION)

    assert "stash_boxes" not in await _tables(temp_db)


#: `stash_boxes` as a library at the baseline can hold it: `sites_are` added by ALTER, so no CHECK.
_BOXES_WITHOUT_THE_CHECK = (
    "CREATE TABLE stash_boxes (\n"
    "  id                  TEXT PRIMARY KEY,\n"
    "  name                TEXT NOT NULL,\n"
    "  endpoint            TEXT NOT NULL UNIQUE,\n"
    "  secret_id           TEXT,\n"
    "  enabled             INTEGER NOT NULL DEFAULT 1,\n"
    "  route               TEXT,\n"
    "  requests_per_minute INTEGER NOT NULL DEFAULT 240,\n"
    "  created_at          INTEGER NOT NULL\n"
    ", sites_are TEXT NOT NULL DEFAULT 'site', slug TEXT)"
)

_A_BOX = (
    "INSERT INTO stash_boxes (id, name, endpoint, sites_are, created_at)"
    " VALUES (?, ?, ?, ?, 1700000000)"
)


async def test_a_library_whose_column_has_no_check_is_given_it_and_keeps_its_boxes(
    temp_db: Database,
) -> None:
    async with temp_db.write() as connection:
        await connection.execute(_BOXES_WITHOUT_THE_CHECK)
        await connection.execute(_A_BOX, ("b1", "One", "https://one.invalid/graphql", "site"))
        await connection.execute(_A_BOX, ("b2", "Two", "https://two.invalid/graphql", "person"))

        await _brought_forward_from_15(connection)

        (stored,) = [str(row[0]) for row in await connection.execute_fetchall(_BOXES)]
        kept = await connection.execute_fetchall(
            "SELECT id, sites_are FROM stash_boxes ORDER BY id"
        )
    assert "CHECK(sites_are IN ('site','person'))" in stored
    assert [tuple(row) for row in kept] == [("b1", "site"), ("b2", "person")]

    # A fresh connection reads the new definition, and refuses what it does not name.
    with pytest.raises(Exception, match="CHECK"):
        await temp_db.execute("UPDATE stash_boxes SET sites_are = 'studio' WHERE id = 'b1'")


async def test_a_stored_value_the_check_would_refuse_leaves_the_column_as_it_is(
    temp_db: Database,
) -> None:
    """SQLite does not check the rows already there when a definition is edited, so a library
    holding a value outside the list keeps its column unchecked rather than gain a false promise."""
    async with temp_db.write() as connection:
        await connection.execute(_BOXES_WITHOUT_THE_CHECK)
        await connection.execute(_A_BOX, ("b1", "One", "https://one.invalid/graphql", "studio"))

        await _brought_forward_from_15(connection)

        (stored,) = [str(row[0]) for row in await connection.execute_fetchall(_BOXES)]
    assert "CHECK" not in stored


async def test_the_step_run_again_after_the_check_is_in_place_changes_nothing(
    temp_db: Database,
) -> None:
    """A step interrupted between its work and the version being recorded runs again, and the
    second run must find the CHECK already there and write nothing."""
    async with temp_db.write() as connection:
        await connection.execute(_BOXES_WITHOUT_THE_CHECK)
        await connection.execute(_A_BOX, ("b1", "One", "https://one.invalid/graphql", "site"))
        await _brought_forward_from_15(connection)
        (once,) = [str(row[0]) for row in await connection.execute_fetchall(_BOXES)]

        await _brought_forward_from_15(connection)

        (twice,) = [str(row[0]) for row in await connection.execute_fetchall(_BOXES)]
        kept = await connection.execute_fetchall("SELECT id, sites_are FROM stash_boxes")
    assert once.count("CHECK(sites_are") == 1
    assert twice == once
    assert [tuple(row) for row in kept] == [("b1", "site")]


#: The column as nothing of Sift's ever wrote it: no NOT NULL. The step edits the stored definition
#: by replacing the column's text, which is only safe where that text is exactly the one the
#: earlier step wrote.
_BOXES_SPELLED_ANOTHER_WAY = (
    "CREATE TABLE stash_boxes (\n"
    "  id                  TEXT PRIMARY KEY,\n"
    "  name                TEXT NOT NULL,\n"
    "  endpoint            TEXT NOT NULL UNIQUE,\n"
    "  secret_id           TEXT,\n"
    "  enabled             INTEGER NOT NULL DEFAULT 1,\n"
    "  route               TEXT,\n"
    "  requests_per_minute INTEGER NOT NULL DEFAULT 240,\n"
    "  created_at          INTEGER NOT NULL\n"
    ", sites_are TEXT DEFAULT 'site', slug TEXT)"
)


async def test_a_column_spelled_another_way_is_left_as_it_is(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        await connection.execute(_BOXES_SPELLED_ANOTHER_WAY)
        await connection.execute(_A_BOX, ("b1", "One", "https://one.invalid/graphql", "site"))

        await _brought_forward_from_15(connection)

        (stored,) = [str(row[0]) for row in await connection.execute_fetchall(_BOXES)]
    assert "CHECK" not in stored


# --- version 17: a kept certain answer is regraded by what its record can prove ---------------

_A_MATCH = (
    "INSERT INTO asset_stash_box_matches (asset_id, box_id, remote_id, payload, grade, state,"
    " found_at) VALUES (?, 'b1', 'r1', ?, 'certain', ?, 1)"
)


def _kept(scene_ms: int) -> str:
    """An answer kept by the old reading: exact, because an exact hash was sent."""
    return as_json(
        [
            FoundRecord(
                source_id="b1",
                remote_id="r1",
                subject=Subject.ASSET,
                name="A Scene",
                fields={"duration_ms": scene_ms},
                confidence=EXACT,
            )
        ]
    )


async def _grades(connection: Connection) -> dict[str, str]:
    rows = await connection.execute_fetchall(
        "SELECT asset_id, grade FROM asset_stash_box_matches ORDER BY asset_id"
    )
    return {str(row[0]): str(row[1]) for row in rows}


async def test_a_kept_certain_answer_is_regraded_as_the_picture_match_it_may_have_been(
    temp_db: Database,
) -> None:
    """A clip of a few seconds given a four-minute scene loses its certainty, applied or waiting;
    a long file whose length agrees to the second keeps it; a refusal is nobody's question."""
    async with temp_db.write() as connection:
        await _what_later_steps_read(connection)
        await connection.execute(_BOXES_WITHOUT_THE_CHECK)
        await connection.execute(_A_BOX, ("b1", "One", "https://one.invalid/graphql", "site"))
        for asset_id, length in (
            ("clip-applied", 8_067),
            ("clip-waiting", 8_067),
            ("clip-refused", 8_067),
            ("film", 232_400),
        ):
            await connection.execute(
                "INSERT INTO assets (id, duration_ms) VALUES (?, ?)", (asset_id, length)
            )
        await connection.execute(_A_MATCH, ("clip-applied", _kept(232_000), "applied"))
        await connection.execute(_A_MATCH, ("clip-waiting", _kept(232_000), "waiting"))
        await connection.execute(_A_MATCH, ("clip-refused", _kept(232_000), "refused"))
        await connection.execute(_A_MATCH, ("film", _kept(232_000), "applied"))
        # A kept answer with no record in it has nothing to prove, and is left as it is.
        await connection.execute("INSERT INTO assets (id, duration_ms) VALUES ('empty', 8067)")
        await connection.execute(_A_MATCH, ("empty", "[]", "applied"))

        await initialize_stash_boxes(connection, 16)

        assert await _grades(connection) == {
            "empty": "certain",
            "clip-applied": "unsure",
            "clip-refused": "certain",
            "clip-waiting": "unsure",
            "film": "certain",
        }


async def test_the_regrade_reads_the_length_window_somebody_set(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        await _what_later_steps_read(connection)
        await connection.execute(_BOXES_WITHOUT_THE_CHECK)
        await connection.execute(_A_BOX, ("b1", "One", "https://one.invalid/graphql", "site"))
        await connection.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
        await connection.execute(
            "INSERT INTO app_settings (key, value) VALUES ('stash_boxes.duration_tolerance_s', '1')"
        )
        await connection.execute(
            "INSERT INTO assets (id, duration_ms) VALUES (?, ?)", ("film", 235_000)
        )
        await connection.execute(_A_MATCH, ("film", _kept(232_000), "applied"))

        await initialize_stash_boxes(connection, 16)

        assert await _grades(connection) == {"film": "unsure"}


@pytest.mark.parametrize(
    ("stored", "window_ms"),
    [(None, 10_000), ("12", 12_000), ("not json", 0), ('"twelve"', 0), ("-3", 0)],
    ids=["no-settings", "set", "unreadable", "not-a-number", "below-nought"],
)
async def test_the_regrade_window_is_what_was_stored_or_nothing_when_it_cannot_be_read(
    temp_db: Database, stored: str | None, window_ms: int
) -> None:
    """A library with no settings table yet reads the default; a value that is not a number of
    seconds reads as no window at all, so nothing is called certain by a length it never had."""
    from sift.slices.stash_boxes.schema import _tolerance_ms
    from sift.slices.stash_boxes.settings import DURATION_DEFAULT_S

    async with temp_db.write() as connection:
        if stored is not None:
            await connection.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
            await connection.execute(
                "INSERT INTO app_settings (key, value) VALUES"
                " ('stash_boxes.duration_tolerance_s', ?)",
                (stored,),
            )
        found = await _tolerance_ms(connection)
    assert found == (DURATION_DEFAULT_S * 1000 if stored is None else window_ms)


async def test_a_settings_table_with_no_window_stored_reads_the_default(temp_db: Database) -> None:
    from sift.slices.stash_boxes.schema import _tolerance_ms
    from sift.slices.stash_boxes.settings import DURATION_DEFAULT_S

    async with temp_db.write() as connection:
        await connection.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
        assert await _tolerance_ms(connection) == DURATION_DEFAULT_S * 1000


# --- version 18: a parent a box named is the box's -----------------------------------------------

_A_SITE = (
    "INSERT INTO sites (id, name, parent_id, created_by_kind, created_by_user_id,"
    " created_by_box_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
)
_A_SITE_LINK = (
    "INSERT INTO site_stash_box_links (site_id, box_id, remote_id, payload, fetched_at)"
    " VALUES (?, 'b1', ?, ?, 0)"
)


def _studio_under(parent: str) -> str:
    """A studio's kept record, as the link keeps it, naming its parent."""
    return as_json(
        [
            FoundRecord(
                source_id="b1",
                remote_id="r-child",
                subject=Subject.SITE,
                name="child",
                fields={"name": "child", "parent": parent},
            )
        ]
    )


async def test_a_parent_a_linked_studio_names_is_recorded_as_that_boxs(temp_db: Database) -> None:
    """The networks a studio's record named were made as if somebody typed them. Where a linked
    studio's kept record names the Site as its parent and points at it, the Site is the box's;
    a Site somebody made and named, or one no link names, is left as it was."""
    async with temp_db.write() as connection:
        await _what_later_steps_read(connection)
        await connection.execute(_BOXES_WITHOUT_THE_CHECK)
        await connection.execute(_A_BOX, ("b1", "One", "https://one.invalid/graphql", "site"))
        # Invented for this file; see `tests/gates/data/names_cast.txt`.
        for row in (
            ("net", "Harbour Network", None, "user", None, None, 5),
            ("dated-net", "Quill House", None, "user", "u1", None, None),
            ("mine", "Northlight Group", None, "user", "u1", None, 5),
            ("alone", "Wrenfield", None, "user", None, None, 5),
            ("kid", "Northlight Media", "net", "box", None, "b1", 5),
            ("kid2", "Marrowvale Studios", "dated-net", "box", None, "b1", 5),
            ("kid3", "Northlight Studio Collection", "mine", "box", None, "b1", 5),
        ):
            await connection.execute(_A_SITE, row)
        await connection.execute(_A_SITE_LINK, ("kid", "r1", _studio_under("harbour network")))
        await connection.execute(_A_SITE_LINK, ("kid2", "r2", _studio_under("Quill House")))
        await connection.execute(_A_SITE_LINK, ("kid3", "r3", _studio_under("Northlight Group")))

        await initialize_stash_boxes(connection, 17)

        made = {
            str(row["id"]): (row["created_by_kind"], row["created_by_box_id"])
            for row in await connection.execute_fetchall(
                "SELECT id, created_by_kind, created_by_box_id FROM sites"
            )
        }
    assert made["net"] == ("box", "b1")
    assert made["dated-net"] == ("box", "b1")
    assert made["mine"] == ("user", None), "a Site somebody made and dated was taken from them"
    assert made["alone"] == ("user", None), "a Site no box named was given to one"


async def test_a_parent_the_box_spelt_with_its_bracket_is_called_by_its_own_name(
    temp_db: Database,
) -> None:
    """The box's "(Network)" comes off a Site that is a parent, and stays as an alias. Not where
    the bare name is another Site's (a network beside its flagship studio), and not off a Site
    that is nobody's parent."""
    async with temp_db.write() as connection:
        await _what_later_steps_read(connection)
        await connection.execute("ALTER TABLE sites ADD COLUMN name_sort TEXT")
        await connection.execute(
            "CREATE TABLE site_aliases (id TEXT PRIMARY KEY, site_id TEXT, alias TEXT,"
            " alias_sort TEXT, added_at INTEGER, UNIQUE(site_id, alias COLLATE NOCASE))"
        )
        await connection.execute(_BOXES_WITHOUT_THE_CHECK)
        # Invented for this file; see `tests/gates/data/names_cast.txt`.
        for row in (
            ("net", "Quillhouse (Network)", None, "box", None, "b1", 5),
            ("kid", "Northlight Media", "net", "box", None, "b1", 5),
            ("held", "Marrowvale Studios (Network)", None, "box", None, "b1", 5),
            ("flagship", "Marrowvale Studios", "held", "box", None, "b1", 5),
            ("lone", "Larkspur Studios (Network)", None, "box", None, "b1", 5),
            ("bare", "(Network)", None, "box", None, "b1", 5),
            ("under-bare", "Cedar Vale", "bare", "box", None, "b1", 5),
        ):
            await connection.execute(_A_SITE, row)

        await initialize_stash_boxes(connection, 17)

        names = {
            str(row["id"]): str(row["name"])
            for row in await connection.execute_fetchall("SELECT id, name FROM sites")
        }
        aliases = [
            (str(row["site_id"]), str(row["alias"]))
            for row in await connection.execute_fetchall("SELECT site_id, alias FROM site_aliases")
        ]
    assert names["net"] == "Quillhouse"
    assert aliases == [("net", "Quillhouse (Network)")]
    assert names["held"] == "Marrowvale Studios (Network)", "a Site became its flagship's name"
    assert names["lone"] == "Larkspur Studios (Network)", "a Site that is no parent was renamed"
    assert names["bare"] == "(Network)", "a name that is only the bracket lost its whole name"
