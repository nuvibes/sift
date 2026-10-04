# SPDX-License-Identifier: AGPL-3.0-or-later
"""The v14 step: a stored font family becomes the face it meant, in each of the two roles.

A family's name meant its display face on the main font's key and its text face on the secondary
font's key. After the step each key holds a face of its own role by the face's own name, and the
page wears exactly what it wore before. Nothing else on the table moves.
"""

from __future__ import annotations

import pytest

from sift.kernel.db import Database
from sift.slices.settings_hub.schema import initialize_settings

pytestmark = [pytest.mark.integration]

#: The version this step upgrades from, written out: it describes a database in the world.
BEFORE = 13

#: Written out, never imported: the step must not move with the code.
MAIN = "appearance.theme_face_display"
SECOND = "appearance.theme_face_body"
#: A neighbour on the same table, which the step must not touch.
KEPT = "appearance.theme_base"

#: Every family in each role, and the face it meant there.
MEANT = {
    MAIN: {
        "archivo": "archivo",
        "grotesk": "space-grotesk",
        "geist": "geist-mono",
        "manrope": "manrope",
    },
    SECOND: {
        "archivo": "instrument-sans",
        "grotesk": "inter",
        "geist": "geist",
        "manrope": "public-sans",
    },
}


async def _made(database: Database, rows: tuple[tuple[str, str, str], ...]) -> None:
    async with database.write() as connection:
        await connection.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY)")
        await initialize_settings(connection, on_disk=0)
        for user in {row[0] for row in rows}:
            await connection.execute("INSERT INTO users (id) VALUES (?)", (user,))
        for user, key, value in rows:
            await connection.execute(
                "INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?)",
                (user, key, value),
            )


async def _stepped(database: Database, on_disk: int = BEFORE) -> dict[tuple[str, str], str]:
    async with database.write() as connection:
        await initialize_settings(connection, on_disk=on_disk)
    rows = await database.fetch_all("SELECT user_id, key, value FROM user_settings")
    return {(str(row["user_id"]), str(row["key"])): str(row["value"]) for row in rows}


@pytest.mark.parametrize("family", ["archivo", "grotesk", "geist", "manrope"])
async def test_a_family_becomes_the_face_it_meant_in_both_roles(
    temp_db: Database, family: str
) -> None:
    await _made(
        temp_db,
        (("u1", MAIN, f'"{family}"'), ("u1", SECOND, f'"{family}"'), ("u1", KEPT, '"chrome"')),
    )
    assert await _stepped(temp_db) == {
        ("u1", MAIN): f'"{MEANT[MAIN][family]}"',
        ("u1", SECOND): f'"{MEANT[SECOND][family]}"',
        ("u1", KEPT): '"chrome"',
    }


async def test_two_families_taken_apart_stay_apart(temp_db: Database) -> None:
    """One family's main face over another's secondary face is carried half by half, and two
    people's answers are each their own."""
    await _made(
        temp_db,
        (
            ("u1", MAIN, '"geist"'),
            ("u1", SECOND, '"grotesk"'),
            ("u2", MAIN, '"grotesk"'),
            ("u2", SECOND, '"manrope"'),
        ),
    )
    assert await _stepped(temp_db) == {
        ("u1", MAIN): '"geist-mono"',
        ("u1", SECOND): '"inter"',
        ("u2", MAIN): '"space-grotesk"',
        ("u2", SECOND): '"public-sans"',
    }


async def test_a_face_already_named_is_left_alone(temp_db: Database) -> None:
    """A library past the step holds face names, and running the carrying twice changes nothing."""
    await _made(temp_db, (("u1", MAIN, '"space-grotesk"'), ("u1", SECOND, '"dm-sans"')))
    assert await _stepped(temp_db) == {
        ("u1", MAIN): '"space-grotesk"',
        ("u1", SECOND): '"dm-sans"',
    }


async def test_a_library_already_past_the_step_is_left_alone(temp_db: Database) -> None:
    await _made(temp_db, (("u1", MAIN, '"geist"'), ("u1", SECOND, '"archivo"')))
    assert await _stepped(temp_db, BEFORE + 1) == {
        ("u1", MAIN): '"geist"',
        ("u1", SECOND): '"archivo"',
    }
