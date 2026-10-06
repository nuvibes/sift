# SPDX-License-Identifier: AGPL-3.0-or-later
"""Smart Search and the lookalike strip rank only what the asker may see.

A guest whose 300 nearest files are hidden from them is owed a full page of their own, in
nearest order, asked for the same number of times as if those files were not there.
"""

from __future__ import annotations

import importlib
import math
from dataclasses import dataclass, replace
from typing import Any

import pytest

from sift.kernel.access import SIMILARITY, Repository, Role, Viewer
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.search.filters import FilterCompiler
from sift.slices.semantic import store as store_module
from sift.slices.semantic.search import SemanticSearch
from sift.slices.semantic.similar import Similar, Tier
from sift.slices.semantic.store import DIMENSION, VectorStore
from sift.testing.fixtures import create_user

pytestmark = pytest.mark.integration

router = importlib.import_module("sift.slices.semantic.router")

REVISION = "test-model"
HIDDEN_NEARER = 300
VISIBLE = 60


def unit(*leading: float) -> list[float]:
    values = [*leading, *([0.0] * (DIMENSION - len(leading)))]
    length = math.sqrt(sum(value * value for value in values))
    return [value / length for value in values]


class Service:
    """The service's two answers over the real store; no model."""

    def __init__(self, store: VectorStore) -> None:
        self.store = store
        self.asked: list[int] = []

    async def describe_query(self, text: str) -> list[float] | None:
        return unit(1.0)

    async def nearest(self, vector: list[float], *, limit: int, asker: Viewer | None) -> Any:
        self.asked.append(limit)
        return await self.store.nearest(vector, revision=REVISION, limit=limit, asker=asker)

    async def similar_to(self, asset_id: str, *, limit: int = 200, asker: Viewer | None) -> Similar:
        vector = await self.store.describes(asset_id, revision=REVISION)
        found = await self.nearest(vector, limit=limit, asker=asker)
        return Similar(
            tier=Tier.LOOKS,
            neighbours=tuple((n.asset_id, n.distance) for n in found if n.asset_id != asset_id),
        )


@dataclass
class Library:
    db: Database
    access: Repository
    store: VectorStore
    guest: Viewer
    admin: Viewer
    keeper: Viewer
    subject: str
    visible: list[str]
    hidden: list[str]


@pytest.fixture
async def library(temp_db: Database) -> Library:
    """Visible files further from the words in order; hidden ones all nearer. The keeper is an
    admin whose own vault holds the nearer ones."""
    await temp_db.initialize_schema()
    guest = await create_user(temp_db, Role.GUEST)
    admin = await create_user(temp_db, Role.ADMIN)
    keeper = await create_user(temp_db, Role.ADMIN)
    subject = new_id()
    visible = [new_id() for _ in range(VISIBLE)]
    hidden = [new_id() for _ in range(HIDDEN_NEARER)]
    placed = [(subject, "mine"), *((one, "mine") for one in visible)]
    placed += [(one, "theirs") for one in hidden]
    async with temp_db.write() as c:
        for root in ("mine", "theirs"):
            await c.execute(
                "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, 0)",
                (root, root, f"/library/{root}"),
            )
        await c.execute(
            "INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect,"
            " created_at) VALUES ('g', 'root', 'mine', ?, 'share', 0)",
            (guest.id,),
        )
        for asset_id, root in placed:
            await c.execute(
                "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
                " VALUES (?, ?, 1, 'image', 0)",
                (asset_id, f"digest-{asset_id}"),
            )
            await c.execute(
                "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path,"
                " filename, first_seen_at, last_seen_at) VALUES (?, ?, ?, NULL, ?, ?, 0, 0)",
                (new_id(), asset_id, root, asset_id, asset_id),
            )
        await c.executemany(
            "INSERT INTO asset_user_state (asset_id, user_id, hidden, hidden_at, updated_at)"
            " VALUES (?, ?, 1, 0, 0)",
            [(one, keeper.id) for one in hidden],
        )
    store = VectorStore(temp_db)
    await store.put(subject, [(0, unit(1.0))], revision=REVISION)
    for index, one in enumerate(hidden):
        await store.put(one, [(0, unit(1.0, 0.001 * (index + 1)))], revision=REVISION)
    for index, one in enumerate(visible):
        await store.put(one, [(0, unit(1.0, 0.5 + 0.01 * index))], revision=REVISION)
    access = Repository(temp_db, None)  # type: ignore[arg-type]
    return Library(temp_db, access, store, guest, admin, keeper, subject, visible, hidden)


async def _search(
    library: Library, viewer: Viewer, query: str, *, by_meaning: bool = False
) -> tuple[Any, ...]:
    service = Service(library.store)
    compiler = FilterCompiler(library.access, semantic=SemanticSearch(service))  # type: ignore[arg-type]
    narrowed = await compiler.narrow(viewer, {"q": query}, by_meaning=by_meaning, need=50)
    page = await library.access.visible_assets(
        viewer, limit=50, offset=0, asset_filter=narrowed.asset_filter, sort=SIMILARITY
    )
    return [view.asset.id for view in page.items], narrowed.complete, service.asked


async def test_a_guest_gets_a_full_page_of_their_own_files_in_nearest_order(
    library: Library,
) -> None:
    ids, complete, asked = await _search(library, library.guest, "words", by_meaning=True)

    assert ids == [library.subject, *library.visible[:49]]
    assert complete is True
    assert asked == [200]


async def test_the_asks_are_the_same_whatever_is_hidden(library: Library) -> None:
    """How many times the index is asked can tell nobody how many hidden files lie near."""
    _, _, near = await _search(library, library.guest, "words", by_meaning=True)
    await library.store.prune(library.hidden)
    _, _, none = await _search(library, library.guest, "words", by_meaning=True)

    assert near == none


async def test_like_ranks_only_what_the_guest_may_see(library: Library) -> None:
    ids, complete, _ = await _search(library, library.guest, f"like:{library.subject}")

    assert ids == library.visible[:50]
    assert complete is True


async def test_the_lookalike_strip_shows_what_the_guest_may_see(library: Library) -> None:
    page = await router.find_similar(
        library.subject,
        service=Service(library.store),
        access=library.access,
        viewer=library.guest,
    )

    assert [item.id for item in page.items] == library.visible[:24]


async def test_an_admins_answer_and_statement_are_unchanged(
    library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []
    reads = library.db.fetch_all

    async def reading(sql: Any, *args: Any) -> Any:
        asked.append(sql)
        return await reads(sql, *args)

    monkeypatch.setattr(library.db, "fetch_all", reading)
    found = await library.store.nearest(
        unit(1.0), revision=REVISION, limit=400, asker=library.admin
    )
    unscoped = await library.store.nearest(unit(1.0), revision=REVISION, limit=400)

    assert found == unscoped
    assert asked == [store_module._NEAREST, store_module._NEAREST]


async def test_a_shut_vault_is_not_ranked_and_an_open_one_is(library: Library) -> None:
    shut, complete, asked = await _search(library, library.keeper, "words", by_meaning=True)
    opened, _, _ = await _search(
        library, replace(library.keeper, show_hidden=True), "words", by_meaning=True
    )

    assert shut == [library.subject, *library.visible[:49]]
    assert (complete, asked) == (True, [200])
    assert opened[:2] == [library.subject, library.hidden[0]]


async def test_files_are_ranked_among_what_the_guest_may_see(library: Library) -> None:
    found = await library.store.nearest_files(
        unit(1.0), revision=REVISION, limit=10, asker=library.guest
    )

    assert [one for one, _ in found] == [library.subject, *library.visible[:9]]


async def test_a_shut_vault_is_not_ranked_by_file_and_an_open_one_is(library: Library) -> None:
    shut = await library.store.nearest_files(
        unit(1.0), revision=REVISION, limit=10, asker=library.keeper
    )
    opened = await library.store.nearest_files(
        unit(1.0), revision=REVISION, limit=10, asker=replace(library.keeper, show_hidden=True)
    )

    assert [one for one, _ in shut] == [library.subject, *library.visible[:9]]
    assert [one for one, _ in opened] == [library.subject, *library.hidden[:9]]


async def test_an_admin_ranks_files_by_the_plain_statement(
    library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []
    reads = library.db.fetch_all

    async def reading(sql: Any, *args: Any) -> Any:
        asked.append(sql)
        return await reads(sql, *args)

    monkeypatch.setattr(library.db, "fetch_all", reading)
    found = await library.store.nearest_files(
        unit(1.0), revision=REVISION, limit=400, asker=library.admin
    )

    assert len(found) == 1 + VISIBLE + HIDDEN_NEARER
    assert asked == [store_module._NEAREST_FILES]
