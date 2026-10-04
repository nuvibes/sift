# SPDX-License-Identifier: AGPL-3.0-or-later
"""The v15 step: a custom accent colour chosen before colours could be kept becomes the first one kept.

Without it, the first person to try a second colour after the upgrade would lose the one they had,
which is exactly what keeping colours exists to end. The colour in force does not move; the step
only adds a list where none is stored, and only for a colour somebody chose.
"""

from __future__ import annotations

import pytest

from sift.kernel.db import Database
from sift.slices.settings_hub.schema import initialize_settings

pytestmark = [pytest.mark.integration]

#: The version this step upgrades from, written out: it describes a database in the world.
BEFORE = 14

#: Written out, never imported: the step must not move with the code.
COLOUR = "appearance.theme_accent_hex"
KEPT = "appearance.theme_accent_swatches"
ACCENT = "appearance.theme_accent"

#: Colours as the validator stores them: JSON strings, lower case. The starting blue is the
#: registered default, which is also the named Blue.
CHOSEN = '"#7a4cd6"'
OTHER = '"#2a9d8f"'
STARTING_BLUE = '"#2563eb"'


async def _made(database: Database, rows: tuple[tuple[str, str, str], ...]) -> None:
    async with database.write() as connection:
        await connection.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY)")
        await initialize_settings(connection, on_disk=0)
        for user in sorted({row[0] for row in rows}):
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


async def test_a_chosen_colour_becomes_the_first_kept_and_stays_in_force(temp_db: Database) -> None:
    await _made(temp_db, (("u1", COLOUR, CHOSEN), ("u1", ACCENT, '"custom"')))
    assert await _stepped(temp_db) == {
        ("u1", COLOUR): CHOSEN,
        ("u1", ACCENT): '"custom"',
        ("u1", KEPT): f"[{CHOSEN}]",
    }


async def test_each_person_keeps_their_own(temp_db: Database) -> None:
    """Two people, two colours, each kept for its owner only; somebody who never chose one keeps
    nothing, and the colour is kept even where one of the six is the accent in force now."""
    await _made(
        temp_db,
        (
            ("u1", COLOUR, CHOSEN),
            ("u2", COLOUR, OTHER),
            ("u2", ACCENT, '"gold"'),
            ("u3", ACCENT, '"red"'),
        ),
    )
    stepped = await _stepped(temp_db)
    assert stepped[("u1", KEPT)] == f"[{CHOSEN}]"
    assert stepped[("u2", KEPT)] == f"[{OTHER}]"
    assert ("u3", KEPT) not in stepped


async def test_the_starting_blue_is_not_kept(temp_db: Database) -> None:
    """The default colour is the named Blue already; a dot for it in the kept row would be a copy of
    the first chip above it."""
    await _made(temp_db, (("u1", COLOUR, STARTING_BLUE),))
    assert ("u1", KEPT) not in await _stepped(temp_db)


async def test_a_value_of_the_wrong_shape_is_not_kept(temp_db: Database) -> None:
    """A row the validator would refuse on read becomes no list, rather than a list the validator
    then refuses whole."""
    await _made(temp_db, (("u1", COLOUR, '"burnt umber"'), ("u2", COLOUR, '"#7A4CD6"')))
    stepped = await _stepped(temp_db)
    assert ("u1", KEPT) not in stepped
    assert ("u2", KEPT) not in stepped


async def test_a_list_already_kept_is_left_alone(temp_db: Database) -> None:
    await _made(temp_db, (("u1", COLOUR, CHOSEN), ("u1", KEPT, f"[{OTHER}]")))
    assert (await _stepped(temp_db))[("u1", KEPT)] == f"[{OTHER}]"


async def test_a_library_already_past_the_step_is_left_alone(temp_db: Database) -> None:
    await _made(temp_db, (("u1", COLOUR, CHOSEN),))
    assert ("u1", KEPT) not in await _stepped(temp_db, BEFORE + 1)
