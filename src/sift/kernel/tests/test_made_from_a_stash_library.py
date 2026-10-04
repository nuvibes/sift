# SPDX-License-Identifier: AGPL-3.0-or-later
"""The rows a Stash library imported say so, and never read as a stash-box's.

The shared lookups make every row under the stash-box word. A Stash library is not a stash-box, so
its rows are named with their own word: as they are made (`catalog.mark_made_via`) and, for a
library that already holds them, by the catalog step that brings it across.
"""

from __future__ import annotations

import pytest

import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import schema
from sift.kernel.access.catalog import (
    by_sift,
    by_user,
    create_person_on,
    ensure_site,
    mark_made_via,
    seed_site_username,
)
from sift.kernel.db import Database
from sift.kernel.vocabulary import VIA_STASH, VIA_STASH_LIBRARY

pytestmark = pytest.mark.anyio


async def _made(database: Database, table: str, row_id: str) -> tuple[str, str | None]:
    row = await database.fetch_one(
        # nosemgrep: sift-no-string-built-sql
        f"SELECT created_by_kind, created_by_via FROM {table} WHERE id = ?",  # noqa: S608
        (row_id,),
    )
    assert row is not None
    return str(row["created_by_kind"]), row["created_by_via"]


async def test_a_row_the_lookups_just_made_is_named_as_the_stash_library_s(
    temp_db: Database, access: object
) -> None:
    async with temp_db.write() as connection:
        brought = await create_person_on(connection, "Wren Halloway", made=by_sift(VIA_STASH))
        typed = await create_person_on(connection, "Esme Wrenfield", made=by_user(None))
    assert brought is not None and typed is not None

    assert await mark_made_via(temp_db, "person", brought, VIA_STASH_LIBRARY)
    # Only a row still reading what the lookups wrote moves: one somebody made keeps its maker.
    assert not await mark_made_via(temp_db, "person", typed, VIA_STASH_LIBRARY)
    # A word that is no pass of Sift's is refused rather than written.
    assert not await mark_made_via(temp_db, "person", brought, "nonsense")

    assert await _made(temp_db, "people", brought) == ("sift", VIA_STASH_LIBRARY)
    assert await _made(temp_db, "people", typed) == ("user", None)


async def test_the_step_names_a_stash_library_s_rows_and_leaves_a_box_s_site_alone(
    temp_db: Database, access: object
) -> None:
    """A Site a box's username brought keeps the box's word; one a Stash library made does not."""
    async with temp_db.write() as connection:
        person = await create_person_on(connection, "Wren Halloway", made=by_sift(VIA_STASH))
    assert person is not None
    brought = await ensure_site(temp_db, "Northlight Media", made=by_sift(VIA_STASH))
    by_a_box, _ = await seed_site_username(
        temp_db, site="Quillhouse", name="quillmoss", made=by_sift(VIA_STASH)
    )

    async with temp_db.write() as connection:
        # The library as it stood before this step: 72, not "the version before the newest", which
        # would stop naming this step the moment another was added after it.
        await schema.initialize_catalog(connection, 72)

    assert await _made(temp_db, "people", person) == ("sift", VIA_STASH_LIBRARY)
    assert await _made(temp_db, "sites", brought) == ("sift", VIA_STASH_LIBRARY)
    assert await _made(temp_db, "sites", by_a_box) == ("sift", VIA_STASH)
