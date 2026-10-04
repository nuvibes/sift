# SPDX-License-Identifier: AGPL-3.0-or-later
"""Filing a file under a NAMED username (`catalog.file_asset_under_username`).

What is pinned here: the filing lands on a named row and never on the Site's nameless one; a name
that differs only in case finds the username already held; a name that cleans away to nothing is
refused; and the username is said to be a person only where it names nobody yet.
"""

from __future__ import annotations

import pytest

# The ledger's table: a username a pass makes records its arrival there.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access.catalog import (
    MADE_BY_A_PERSON,
    by_sift,
    create_person_on,
    file_asset_under_username,
    seed_site_username,
)
from sift.kernel.db import Database
from sift.kernel.ids import new_id

pytestmark = pytest.mark.anyio


async def _asset(db: Database) -> str:
    asset_id = new_id()
    await db.execute(
        "INSERT INTO assets (id, identity, media_type, size_bytes, original_filename, added_at) "
        "VALUES (?, ?, 'video', 1, 'x.mp4', 0)",
        (asset_id, new_id()),
    )
    return asset_id


async def _person(db: Database, name: str) -> str:
    async with db.write() as connection:
        made = await create_person_on(connection, name, made=MADE_BY_A_PERSON)
    assert made is not None
    return made


async def _filed(db: Database, asset_id: str) -> list[tuple[str, str, str | None]]:
    rows = await db.fetch_all(
        "SELECT s.name AS site, u.name AS name, u.person_id AS person FROM asset_usernames l"
        " JOIN usernames u ON u.id = l.username_id JOIN sites s ON s.id = u.site_id"
        " WHERE l.asset_id = ?",
        (asset_id,),
    )
    return [(str(row["site"]), str(row["name"]), row["person"]) for row in rows]


async def test_a_named_filing_lands_on_the_username_and_never_the_nameless_row(
    temp_db: Database,
) -> None:
    await temp_db.initialize_schema()
    asset_id = await _asset(temp_db)
    esme = await _person(temp_db, "Esme Wrenfield")

    filed = await file_asset_under_username(
        temp_db,
        asset_id=asset_id,
        site="OnlyFans",
        name="quillmoss",
        url="https://onlyfans.com/quillmoss",
        source="stash_box",
        made=by_sift("stash"),
        person_id=esme,
    )

    assert filed is True
    assert await _filed(temp_db, asset_id) == [("OnlyFans", "quillmoss", esme)]
    nameless = await temp_db.fetch_one("SELECT COUNT(*) AS n FROM usernames WHERE name = ''")
    assert nameless is not None and nameless["n"] == 0
    again = await file_asset_under_username(
        temp_db,
        asset_id=asset_id,
        site="OnlyFans",
        name="quillmoss",
        source="stash_box",
        made=by_sift("stash"),
    )
    assert again is False


async def test_a_name_in_another_case_finds_the_username_already_held(temp_db: Database) -> None:
    await temp_db.initialize_schema()
    asset_id = await _asset(temp_db)
    jane = await _person(temp_db, "Jane Roe")
    _, held = await seed_site_username(
        temp_db, site="OnlyFans", name="QuillMoss", made=MADE_BY_A_PERSON
    )
    await temp_db.execute("UPDATE usernames SET person_id = ? WHERE id = ?", (jane, held))
    esme = await _person(temp_db, "Esme Wrenfield")

    await file_asset_under_username(
        temp_db,
        asset_id=asset_id,
        site="OnlyFans",
        name="quillmoss",
        source="stash_box",
        made=by_sift("stash"),
        person_id=esme,
    )

    # One row, its spelling kept, and the person somebody already named left alone.
    assert await _filed(temp_db, asset_id) == [("OnlyFans", "QuillMoss", jane)]


async def test_a_name_that_is_nothing_is_refused(temp_db: Database) -> None:
    await temp_db.initialize_schema()
    asset_id = await _asset(temp_db)

    with pytest.raises(ValueError, match="empty"):
        await file_asset_under_username(
            temp_db,
            asset_id=asset_id,
            site="OnlyFans",
            name=" @ ",
            source="stash_box",
            made=by_sift("stash"),
        )

    assert await _filed(temp_db, asset_id) == []
