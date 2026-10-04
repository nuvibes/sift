# SPDX-License-Identifier: AGPL-3.0-or-later
"""Do not swap: the mark, the whole rule it is read by, the record it leaves, and its two routes.

The mark is the kernel's refusal with its swap purpose, so these hold what is particular to it:
"Do not enrich" refuses a swap as well, a file is out under anything it is filed under, a press is
written down only when it changes something, and the routes answer a thing this viewer cannot see
exactly as one that was never made.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

import sift.slices.workbench.schema  # noqa: F401 (the ledger the mark is recorded in)
from sift.kernel import wiring
from sift.kernel.access import Repository, Role
from sift.kernel.access.catalog import files_kept_from_swaps, refused_here, refused_over
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.swap import refusal
from sift.slices.swap.router import router
from sift.testing.fixtures import create_user, hide

pytestmark = pytest.mark.unit

_EPOCH = 1_700_000_000


async def _file_under_a_person(db: Database) -> tuple[str, str]:
    person, asset = new_id(), new_id()
    await db.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, 'Orla Tennant', 'o', ?)",
        (person, _EPOCH),
    )
    await db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', ?)",
        (asset, f"digest-{asset}", _EPOCH),
    )
    await db.execute(
        "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (asset, person)
    )
    return person, asset


async def test_the_whole_rule_takes_in_the_filing_and_do_not_enrich(
    temp_db: Database, access: Repository
) -> None:
    person, asset = await _file_under_a_person(temp_db)
    assert not await refused_over(temp_db, "swap", "asset", asset)

    await temp_db.execute("UPDATE people SET keep_from_swaps = 1 WHERE id = ?", (person,))
    assert await refused_over(temp_db, "swap", "asset", asset)
    assert not await refused_here(temp_db, "swap", "asset", asset), "the file's own switch is off"
    # The swap purpose leaves the stash-box lookups alone.
    assert not await refused_over(temp_db, "enrich", "asset", asset)
    assert await refusal.state_of(temp_db, "asset", asset) == (False, True, refusal.BY_FILING)

    await temp_db.execute(
        "UPDATE people SET keep_from_swaps = 0, keep_local = 1 WHERE id = ?", (person,)
    )
    # "Do not enrich" keeps everything on this device, swaps included.
    assert await refused_over(temp_db, "swap", "person", person)
    assert await refusal.state_of(temp_db, "person", person) == (
        False,
        True,
        refusal.BY_ENRICHMENT,
    )


async def test_every_file_a_swap_must_not_offer_is_read_through_each_thing_it_is_filed_under(
    temp_db: Database, access: Repository
) -> None:
    """Its own marks, a person, a tag, and a Site ABOVE the one its username is on: each keeps it
    out. A thing with no id, or of a kind that carries no mark, is never refused."""
    person, by_person = await _file_under_a_person(temp_db)
    own, by_tag, by_site, free = (new_id() for _ in range(4))
    for asset in (own, by_tag, by_site, free):
        await temp_db.execute(
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', ?)",
            (asset, f"digest-{asset}", _EPOCH),
        )
    await temp_db.execute("UPDATE assets SET keep_local = 1 WHERE id = ?", (own,))
    await temp_db.execute("UPDATE people SET keep_from_swaps = 1 WHERE id = ?", (person,))
    await temp_db.execute(
        "INSERT INTO tags (id, name, name_sort, created_at, keep_from_swaps)"
        " VALUES ('t-1', 'beach', 'beach', ?, 1)",
        (_EPOCH,),
    )
    await temp_db.execute("INSERT INTO asset_tags (asset_id, tag_id) VALUES (?, 't-1')", (by_tag,))
    await temp_db.execute(
        "INSERT INTO sites (id, name, name_sort, created_at, keep_from_swaps)"
        " VALUES ('above', 'Cedar Vale', 'cedar vale', ?, 1)",
        (_EPOCH,),
    )
    await temp_db.execute(
        "INSERT INTO sites (id, name, name_sort, created_at, parent_id)"
        " VALUES ('below', 'Quillhouse', 'quillhouse', ?, 'above')",
        (_EPOCH,),
    )
    await temp_db.execute(
        "INSERT INTO usernames (id, site_id, name, name_sort, created_at)"
        " VALUES ('u-1', 'below', 'quillmoss', 'quillmoss', ?)",
        (_EPOCH,),
    )
    await temp_db.execute(
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, 'u-1')", (by_site,)
    )

    assert await files_kept_from_swaps(temp_db) == {own, by_person, by_tag, by_site}
    assert not await refused_over(temp_db, "swap", "person", "")
    assert not await refused_over(temp_db, "swap", "collection", person)


async def test_a_press_is_recorded_once_and_only_when_it_changes_something(
    temp_db: Database, access: Repository
) -> None:
    admin = await create_user(temp_db, Role.ADMIN)
    person, _asset = await _file_under_a_person(temp_db)

    for _ in range(2):
        assert await refusal.set_kept_from_swaps(
            temp_db, admin, "person", person, True, name="Orla Tennant"
        )
    assert await refusal.state_of(temp_db, "person", person) == (True, True, "")
    assert await refusal.set_kept_from_swaps(temp_db, admin, "person", person, False, name=None)
    assert not await refusal.set_kept_from_swaps(
        temp_db, admin, "person", "nobody", True, name=None
    )

    rows = await temp_db.fetch_all(
        "SELECT d.verb FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE s.subject_id = ? ORDER BY d.id",
        (person,),
    )
    assert [row["verb"] for row in rows] == ["kept_from_swaps", "allowed_in_swaps"]


async def test_a_file_is_named_in_the_file_pages_order_for_its_history_line(
    temp_db: Database, access: Repository
) -> None:
    """The line a press writes names the file as its page does: a title, then its name on disk
    now, then the name it arrived under. The imported name alone would keep a file renamed on disk
    under a name nobody sees any more."""
    admin = await create_user(temp_db, Role.ADMIN)
    asset, root = new_id(), new_id()
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at, original_filename)"
        " VALUES (?, ?, 'video', ?, 'arrived.mp4')",
        (asset, f"digest-{asset}", _EPOCH),
    )
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, 'files', '/f', ?)",
        (root, _EPOCH),
    )
    await temp_db.execute(
        "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename,"
        " first_seen_at, last_seen_at) VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
        (new_id(), asset, root, "renamed.mp4", "renamed.mp4", _EPOCH, _EPOCH),
    )

    assert await refusal.name_for(access, admin, "asset", asset) == "renamed.mp4"
    await temp_db.execute("UPDATE assets SET title = 'A title' WHERE id = ?", (asset,))
    assert await refusal.name_for(access, admin, "asset", asset) == "A title"
    await temp_db.execute("UPDATE assets SET title = NULL WHERE id = ?", (asset,))
    await temp_db.execute(
        "UPDATE asset_locations SET status = 'missing' WHERE asset_id = ?", (asset,)
    )
    assert await refusal.name_for(access, admin, "asset", asset) == "arrived.mp4"


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


async def test_the_route_sets_the_mark_and_answers_where_it_stands(
    temp_db: Database, client: httpx.AsyncClient
) -> None:
    person, _asset = await _file_under_a_person(temp_db)
    url = f"/api/swap/keep-out/person/{person}"

    before = await client.get(url)
    assert before.status_code == 200, before.text
    assert (before.json()["kept_out_here"], before.json()["kept_out"]) == (False, False)

    put = await client.put(url, json={"kept_out": True})
    assert put.status_code == 200, put.text
    assert (put.json()["kept_out_here"], put.json()["kept_out"]) == (True, True)

    # The read gives back what the write left, and changes nothing by asking.
    after = await client.get(url)
    assert after.json() == put.json()
    assert (await client.get(f"/api/swap/keep-out/collection/{person}")).status_code == 404
    assert (await client.get("/api/swap/keep-out/person/nobody")).status_code == 404

    # A kind nothing is ever refused for, and a thing that is not there, are the same 404.
    assert (
        await client.put(f"/api/swap/keep-out/collection/{person}", json={"kept_out": True})
    ).status_code == 404
    assert (
        await client.put("/api/swap/keep-out/person/nobody", json={"kept_out": True})
    ).status_code == 404


async def test_a_tag_is_marked_by_its_name_and_one_in_hidden_is_no_such_subject(
    temp_db: Database, access: Repository, client: httpx.AsyncClient
) -> None:
    tag = new_id()
    await temp_db.execute(
        "INSERT INTO tags (id, name, name_sort, created_at) VALUES (?, 'harbour', 'harbour', ?)",
        (tag, _EPOCH),
    )

    put = await client.put(f"/api/swap/keep-out/tag/{tag}", json={"kept_out": True})

    assert put.status_code == 200, put.text
    assert (put.json()["kept_out_here"], put.json()["kept_out"]) == (True, True)
    rows = await temp_db.fetch_all(
        "SELECT name FROM workbench_decision_subjects WHERE subject_id = ?", (tag,)
    )
    assert [row["name"] for row in rows] == ["harbour"], "the line names the tag"
    admin = await temp_db.fetch_one("SELECT id FROM users LIMIT 1")
    assert admin is not None
    await hide(temp_db, "tag", tag, str(admin["id"]))
    assert (await client.get(f"/api/swap/keep-out/tag/{tag}")).status_code == 404


async def test_a_thing_removed_between_its_name_and_the_mark_is_no_such_subject(
    temp_db: Database, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A delete can land between the read that names the thing and the write that marks it: the
    press answers as it would for a thing that was never there, and records nothing."""
    person, _asset = await _file_under_a_person(temp_db)
    named = refusal.name_for

    async def then_removed(*args: object) -> str | None:
        name = await named(*args)  # type: ignore[arg-type]
        await temp_db.execute("DELETE FROM asset_people WHERE person_id = ?", (person,))
        await temp_db.execute("DELETE FROM people WHERE id = ?", (person,))
        return name

    monkeypatch.setattr(refusal, "name_for", then_removed)

    put = await client.put(f"/api/swap/keep-out/person/{person}", json={"kept_out": True})

    assert put.status_code == 404
    assert await temp_db.fetch_all("SELECT id FROM workbench_decisions") == []
