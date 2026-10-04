# SPDX-License-Identifier: AGPL-3.0-or-later
"""A folder's "Don't swap": the route sets the mark with one History line each way, a file under
the folder is out of every swap and says which folder keeps it out, and the picks' weight counts
it among what is left out."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

import sift.slices.workbench.schema  # noqa: F401 (the ledger the mark is recorded in)
from sift.kernel import wiring
from sift.kernel.access import Repository, Role
from sift.kernel.db import Database
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.swap import refusal
from sift.slices.swap.router import router
from sift.slices.swap.tests.test_offer import Library, _NoFilters, library  # noqa: F401
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.unit

_EPOCH = 1_700_000_000


async def _beach_days(db: Database) -> None:
    await db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES ('r1', 'r1', '/r1', ?)",
        (_EPOCH,),
    )
    for folder, parent, path, name in (
        ("f-beach", None, "Beach days", "Beach days"),
        ("f-morning", "f-beach", "Beach days/Morning", "Morning"),
    ):
        await db.execute(
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, 'r1', ?, ?, ?)",
            (folder, parent, path, name),
        )
    await db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES ('a-shore', 'd', 'video', ?)",
        (_EPOCH,),
    )
    await db.execute(
        "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename,"
        " first_seen_at, last_seen_at) VALUES ('l1', 'a-shore', 'r1', 'f-morning',"
        " 'Beach days/Morning/shore.mp4', 'shore.mp4', ?, ?)",
        (_EPOCH, _EPOCH),
    )


@pytest.fixture
async def client(temp_db: Database, access: Repository) -> AsyncIterator[httpx.AsyncClient]:
    admin = await create_user(temp_db, Role.ADMIN)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[require_admin] = lambda: admin
    app.dependency_overrides[csrf_protect] = lambda: None
    app.dependency_overrides[wiring.access] = lambda: access
    app.dependency_overrides[wiring.database] = lambda: temp_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sift.test") as http:
        yield http


async def test_a_folder_is_kept_out_of_swaps_and_its_files_say_which_folder(
    temp_db: Database, client: httpx.AsyncClient
) -> None:
    await _beach_days(temp_db)
    url = "/api/swap/keep-out/folder/f-beach"

    put = await client.put(url, json={"kept_out": True})
    assert put.status_code == 200, put.text
    assert (put.json()["kept_out_here"], put.json()["kept_out"]) == (True, True)

    inside = (await client.get("/api/swap/keep-out/folder/f-morning")).json()
    assert (inside["kept_out_here"], inside["kept_out"], inside["why"]) == (
        False,
        True,
        refusal.BY_A_FOLDER_ABOVE,
    )
    shore = (await client.get("/api/swap/keep-out/asset/a-shore")).json()
    assert shore["kept_out"]
    assert shore["by"] == [
        {"kind": "folder", "id": "f-beach", "name": "Beach days", "mark": "swap"}
    ]

    assert (await client.put(url, json={"kept_out": False})).status_code == 200
    rows = await temp_db.fetch_all(
        "SELECT d.verb, s.kind, s.name FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE s.subject_id = 'f-beach' ORDER BY d.id"
    )
    assert [(r["verb"], r["kind"], r["name"]) for r in rows] == [
        ("kept_from_swaps", "folder", "Beach days"),
        ("allowed_in_swaps", "folder", "Beach days"),
    ]
    assert (await client.get("/api/swap/keep-out/folder/nowhere")).status_code == 404


async def test_the_swap_chooser_leaves_a_marked_folders_files_out_and_counts_them(
    library: Library,  # noqa: F811
) -> None:
    """A person picked whole: the file of theirs inside a folder kept out of swaps is not offered,
    and the total under the picks counts it among what a mark elsewhere keeps back."""
    from sift.slices.swap import offer
    from sift.slices.swap.models import Chosen
    from sift.slices.swap.weight import left_out

    db, ids = library.db, library.ids
    picked = [Chosen(kind="person", id=ids["person"])]
    before = await offer.weigh(library.access, _NoFilters(), library.admin, picked)
    _named, other_before = await left_out(library.access, db, _NoFilters(), library.admin, picked)

    await db.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name)"
        " VALUES ('f-beach', 'r1', 'f1', 'shoot/Beach days', 'Beach days')"
    )
    await db.execute(
        "UPDATE asset_locations SET folder_id = 'f-beach' WHERE asset_id = ?", (ids["p1"],)
    )
    await db.execute("UPDATE folders SET keep_from_swaps = 1 WHERE id = 'f-beach'")

    after = await offer.weigh(library.access, _NoFilters(), library.admin, picked)
    _named, other_after = await left_out(library.access, db, _NoFilters(), library.admin, picked)
    assert after[0] == before[0] - 1, "the file inside the folder is not offered"
    assert other_after == other_before + 1, "and the total says it is left out"
