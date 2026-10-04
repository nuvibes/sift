# SPDX-License-Identifier: AGPL-3.0-or-later
"""A folder's "Don't enrich": the door refuses every file under it, and its route sets the mark,
writes one History line each way, and answers a folder this viewer cannot see as no such subject."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

import httpx
import pytest
from fastapi import FastAPI

# The ledger the mark is recorded in.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel import wiring
from sift.kernel.access import Effect, ObjectType, Repository, Role
from sift.kernel.db import Database
from sift.kernel.records import FoundRecord
from sift.kernel.secret_store import SecretStore
from sift.slices.auth import csrf_protect, current_viewer, require_admin
from sift.slices.stash_boxes.adapter import Box
from sift.slices.stash_boxes.asking import kept_local_by
from sift.slices.stash_boxes.router_base import (
    KEPT_LOCAL_BY_A_FOLDER_ABOVE,
    _service,
    set_folder_kept_local,
)
from sift.slices.stash_boxes.router_links import router
from sift.slices.stash_boxes.service import KeptLocal, StashBoxService
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.unit

A_KEY = b"0" * 32
_EPOCH = 1_700_000_000


class _Adapter:
    """Counts what it was asked: reaching it is what sends a request to somebody else's service."""

    def __init__(self) -> None:
        self.asked = 0

    async def recognise(self, box: Box, hashes: Mapping[str, str]) -> list[FoundRecord]:
        _ = (box, hashes)
        self.asked += 1
        return []


async def _beach_days(db: Database) -> None:
    """`Beach days/Morning`, a file inside `Morning`, and a file in `Harbour` beside it."""
    await db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES ('r1', 'r1', '/r1', ?)",
        (_EPOCH,),
    )
    for folder, parent, path, name in (
        ("f-beach", None, "Beach days", "Beach days"),
        ("f-morning", "f-beach", "Beach days/Morning", "Morning"),
        ("f-harbour", None, "Harbour", "Harbour"),
    ):
        await db.execute(
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, 'r1', ?, ?, ?)",
            (folder, parent, path, name),
        )
    for asset, folder in (("a-shore", "f-morning"), ("a-quay", "f-harbour")):
        await db.execute(
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', ?)",
            (asset, f"digest-{asset}", _EPOCH),
        )
        await db.execute(
            "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename,"
            " first_seen_at, last_seen_at) VALUES (?, ?, 'r1', ?, ?, ?, ?, ?)",
            (f"l-{asset}", asset, folder, f"x/{asset}.mp4", f"{asset}.mp4", _EPOCH, _EPOCH),
        )


async def test_the_stash_box_pass_sends_nothing_about_a_file_in_a_kept_local_folder(
    temp_db: Database,
) -> None:
    await temp_db.initialize_schema()
    adapter = _Adapter()
    service = StashBoxService(temp_db, SecretStore(temp_db), adapter)  # type: ignore[arg-type]
    await service.add(
        name="StashDB", endpoint="https://stashdb.example/graphql", api_key="k", master_key=A_KEY
    )
    await _beach_days(temp_db)
    await temp_db.execute("UPDATE folders SET keep_local = 1 WHERE id = 'f-beach'")

    with pytest.raises(KeptLocal):
        await service.recognise("a-shore", {"oshash": "0" * 16}, A_KEY)
    assert adapter.asked == 0, "a file two folders down is kept at home"

    await service.recognise("a-quay", {"oshash": "1" * 16}, A_KEY)
    assert adapter.asked == 1, "the folder beside it is asked about"


@pytest.fixture
async def client(temp_db: Database, access: Repository) -> AsyncIterator[httpx.AsyncClient]:
    admin = await create_user(temp_db, Role.ADMIN)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[require_admin] = lambda: admin
    app.dependency_overrides[current_viewer] = lambda: admin
    app.dependency_overrides[csrf_protect] = lambda: None
    app.dependency_overrides[wiring.access] = lambda: access
    app.dependency_overrides[wiring.database] = lambda: temp_db
    app.dependency_overrides[_service] = lambda: None
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://sift.test") as http:
        yield http


async def test_the_route_keeps_a_folder_local_and_writes_one_line_each_way(
    temp_db: Database, client: httpx.AsyncClient
) -> None:
    await _beach_days(temp_db)
    url = "/api/stash-boxes/enrichment/folder/f-beach"

    before = await client.get(url)
    assert before.status_code == 200, before.text
    assert (before.json()["kept_local"], before.json()["refused"]) == (False, False)

    for _ in range(2):
        put = await client.put(url + "/keep-local", json={"kept_local": True})
        assert put.status_code == 200, put.text
        assert (put.json()["kept_local"], put.json()["refused"]) == (True, True)
    inside = (await client.get("/api/stash-boxes/enrichment/folder/f-morning")).json()
    assert (inside["kept_local"], inside["refused"], inside["why"]) == (
        False,
        True,
        KEPT_LOCAL_BY_A_FOLDER_ABOVE,
    )
    assert (await client.put(url + "/keep-local", json={"kept_local": False})).status_code == 200

    rows = await temp_db.fetch_all(
        "SELECT d.verb, s.kind, s.name FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id"
        " WHERE s.subject_id = 'f-beach' ORDER BY d.id"
    )
    assert [(r["verb"], r["kind"], r["name"]) for r in rows] == [
        ("kept_local", "folder", "Beach days"),
        ("allowed", "folder", "Beach days"),
    ], "a press that changes nothing writes nothing"

    missing = await client.put(
        "/api/stash-boxes/enrichment/folder/nowhere/keep-local", json={"kept_local": True}
    )
    assert missing.status_code == 404
    assert (await client.get("/api/stash-boxes/enrichment/folder/nowhere")).status_code == 404


async def test_a_files_menu_reason_names_the_folder_that_keeps_it_local(
    temp_db: Database, access: Repository, client: httpx.AsyncClient
) -> None:
    """Not "Kept local by something it's filed under": the folder, by name, since the switch on the
    file's own row will not turn it off and the reader needs to know where to look."""
    await _beach_days(temp_db)
    service = StashBoxService(temp_db, SecretStore(temp_db), _Adapter())  # type: ignore[arg-type]
    app = client._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[_service] = lambda: service
    await temp_db.execute("UPDATE folders SET keep_local = 1 WHERE id = 'f-beach'")

    state = (await client.get("/api/stash-boxes/enrichment/asset/a-shore")).json()

    assert (state["kept_local"], state["refused"], state["why"]) == (
        False,
        True,
        "Kept local by the folder Beach days",
    )
    assert kept_local_by([("folder", "Beach days"), ("tag", "dusk")]) == (
        "Kept local by the folder Beach days and 1 more"
    )
    assert kept_local_by([]) == "Kept local by something it's filed under"


async def test_the_reason_names_only_a_dont_enrich_and_only_what_the_viewer_may_see(
    temp_db: Database, access: Repository, client: httpx.AsyncClient
) -> None:
    """The folder it sits in keeps it out of swaps, which is not why it is kept local; a guest
    shown the file alone is not told the name of the folder above it."""
    await _beach_days(temp_db)
    service = StashBoxService(temp_db, SecretStore(temp_db), _Adapter())  # type: ignore[arg-type]
    app = client._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[_service] = lambda: service
    await temp_db.execute("UPDATE folders SET keep_local = 1 WHERE id = 'f-beach'")
    await temp_db.execute("UPDATE folders SET keep_from_swaps = 1 WHERE id = 'f-morning'")
    url = "/api/stash-boxes/enrichment/asset/a-shore"

    assert (await client.get(url)).json()["why"] == "Kept local by the folder Beach days"

    guest = await create_user(temp_db, Role.GUEST)
    await access.grant(ObjectType.ITEM, "a-shore", guest.id, Effect.SHARE)
    app.dependency_overrides[current_viewer] = lambda: guest
    assert (await client.get(url)).json()["why"] == "Kept local by something it's filed under"


async def test_a_folder_gone_before_the_write_is_no_such_folder_and_no_line(
    temp_db: Database,
) -> None:
    """The route reads its name first; a rescan that removes it before the write leaves nothing
    to mark."""
    await temp_db.initialize_schema()
    admin = await create_user(temp_db, Role.ADMIN)
    lines = "SELECT COUNT(*) AS n FROM workbench_decisions"
    before = (await temp_db.fetch_all(lines))[0]["n"]
    assert not await set_folder_kept_local(temp_db, admin, "f-gone", True, name="Beach days")
    assert (await temp_db.fetch_all(lines))[0]["n"] == before
