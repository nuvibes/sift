# SPDX-License-Identifier: AGPL-3.0-or-later
"""A guest's words are matched once per address and kept for that User alone, until anything they
stand on moves: what the User may see, the word index, or the change mark."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from sift.kernel.access import AssetFilter, AssetPage, Repository, Role, Viewer, index_assets
from sift.kernel.access.repository.read_files import WordMatches, words_of
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.slices.browse import service as browse
from sift.slices.browse.service import BrowseService
from sift.slices.browse.tests.conftest import Library, db_path, share, sign_in, write

_COLUMNS = ("media", "tags", "people", "in", "rating")


def _asked(text: str) -> AssetFilter:
    from sift.kernel.access import AllOf, Where

    return AssetFilter(where=AllOf((Where("text_match"),)), text=text)


class _Access:
    """The reads the service makes, answering nothing and counting each match."""

    def __init__(self) -> None:
        self.matched: list[str] = []
        self.handed: list[WordMatches | None] = []
        self.size = 1
        self.written: bytes | None = b"one"

    async def words_stand_on(self, viewer: Viewer) -> tuple[int, int, bytes | None]:
        return (1, 0, self.written)

    async def word_matches(self, viewer: Viewer, asset_filter: AssetFilter) -> WordMatches | None:
        self.matched.append(viewer.id)
        await asyncio.sleep(0)
        words = words_of(asset_filter)
        assert words is not None
        ids = [new_id() for _ in range(self.size)]
        return WordMatches(viewer.id, words, json.dumps(ids), len(ids))

    async def visible_assets(self, viewer: Viewer, **asked: Any) -> AssetPage:
        self.handed.append(asked["words"])
        return AssetPage(items=[], total=0)

    async def facet_counts(self, viewer: Viewer, facet: str, **asked: Any) -> list[Any]:
        self.handed.append(asked["words"])
        return []


@pytest.fixture
def mark(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    now: list[str | None] = ["first"]
    monkeypatch.setattr(browse, "current_mark", lambda: now[0])
    return now


def _guest(stamp: int = 0) -> Viewer:
    return Viewer(id=new_id(), role=Role.GUEST, cache_stamp=stamp)


async def _address(service: BrowseService, viewer: Viewer, text: str = "zqxv") -> None:
    """The wall and the Filter panel's five columns, asked at the same time as the client asks them."""
    asked = _asked(text)
    await asyncio.gather(
        service.page(viewer, limit=50, offset=0, asset_filter=asked),
        *(
            service.facets(viewer, facet, asset_filter=asked, hidden_only=False, limit=24)
            for facet in _COLUMNS
        ),
    )


def _service(access: _Access) -> BrowseService:
    return BrowseService(cast(Any, None), cast(Any, access), cast(Any, None), cast(Any, None))


async def test_an_address_matches_a_guests_words_once(mark: list[str | None]) -> None:
    access = _Access()
    service = _service(access)
    guest = _guest()
    await _address(service, guest)
    await service.page(guest, limit=50, offset=0, asset_filter=_asked("zqxv"))
    assert access.matched == [guest.id]
    assert all(one is not None and one.viewer == guest.id for one in access.handed)


async def test_an_admins_words_are_never_listed(mark: list[str | None]) -> None:
    access = _Access()
    await _address(_service(access), Viewer(id=new_id(), role=Role.ADMIN))
    assert access.matched == [] and set(access.handed) == {None}


async def test_one_users_list_is_never_read_or_pushed_out_by_another(
    mark: list[str | None],
) -> None:
    access = _Access()
    service = _service(access)
    mine, theirs = _guest(), _guest()
    await _address(service, mine)
    await _address(service, theirs)
    assert access.matched == [mine.id, theirs.id]
    for n in range(browse.VIEWERS_KEPT - 2):
        await _address(service, _guest(), f"zqx{n}")
    await _address(service, mine)
    assert access.matched.count(mine.id) == 1
    for n in range(browse.VIEWERS_KEPT):
        await _address(service, _guest(), f"zqy{n}")
    await _address(service, mine)
    assert access.matched.count(mine.id) == 2


async def test_a_list_past_the_budget_is_used_and_not_kept(mark: list[str | None]) -> None:
    access = _Access()
    service = _service(access)
    guest = _guest()
    await service.page(guest, limit=50, offset=0, asset_filter=_asked("zqxw"))
    access.size = browse.WORD_IDS_KEPT + 1
    for _ in range(2):
        await service.page(guest, limit=50, offset=0, asset_filter=_asked("zqxv"))
    assert access.handed[-1] is not None and access.handed[-1].count == access.size
    await service.page(guest, limit=50, offset=0, asset_filter=_asked("zqxw"))
    assert len(access.matched) == 3


async def test_a_user_keeps_a_bounded_number_of_lists(mark: list[str | None]) -> None:
    access = _Access()
    service = _service(access)
    guest = _guest()
    for n in range(browse.WORD_LISTS_KEPT + 1):
        await service.page(guest, limit=50, offset=0, asset_filter=_asked(f"zqx{n}"))
    await service.page(guest, limit=50, offset=0, asset_filter=_asked("zqx0"))
    assert len(access.matched) == browse.WORD_LISTS_KEPT + 2


@pytest.mark.parametrize("moved", ["mark", "index", "stamp", "no mark", "no index"])
async def test_a_list_is_dropped_when_what_it_stands_on_moves(
    mark: list[str | None], moved: str
) -> None:
    access = _Access()
    service = _service(access)
    guest = _guest()
    await service.page(guest, limit=50, offset=0, asset_filter=_asked("zqxv"))
    if moved == "mark":
        mark[0] = "second"
    elif moved == "index":
        access.written = b"two"
    elif moved == "stamp":
        guest = Viewer(id=guest.id, role=Role.GUEST, cache_stamp=1)
    elif moved == "no mark":
        mark[0] = None
    else:
        access.written = None
    for _ in range(2):
        await service.page(guest, limit=50, offset=0, asset_filter=_asked("zqxv"))
    assert len(access.matched) == (2 if moved in ("mark", "index", "stamp") else 3)


# --- over HTTP, against the real reads and the real writes -----------------------------------


def _index(path: Path, *asset_ids: str) -> None:
    """Write the word index on a connection of its own, with nothing announced."""

    async def run() -> None:
        database = Database(path, readers=1)
        await database.connect()
        try:
            if asset_ids:
                await index_assets(database, asset_ids=list(asset_ids))
            else:
                await index_assets(database, rebuild=True)
        finally:
            await database.close()

    asyncio.run(run())


def _found(client: TestClient, words: str) -> set[str]:
    answer = client.get("/api/assets", params={"q": words, "limit": "50"})
    assert answer.status_code == 200, answer.text
    found = {item["id"] for item in answer.json()["items"]}
    counted = client.get("/api/assets/facets", params={"q": words, "facet": "media"})
    assert sum(row["count"] for row in counted.json()["values"]) == len(found)
    return found


@pytest.fixture
def matched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every match a guest's words cost, through the real read."""
    seen: list[str] = []
    real = Repository.word_matches

    async def counted(self: Repository, viewer: Viewer, asset_filter: AssetFilter) -> Any:
        seen.append(viewer.id)
        return await real(self, viewer, asset_filter)

    monkeypatch.setattr(Repository, "word_matches", counted)
    return seen


def _titled(client: TestClient, asset_id: str, title: str) -> tuple[str, tuple[object, ...]]:
    return ("UPDATE assets SET title = ? WHERE id = ?", (title, asset_id))


@pytest.mark.integration
@pytest.mark.parametrize(
    "change", ["grant taken away", "file hidden", "words indexed", "grant given"]
)
def test_every_write_a_kept_list_stands_on_drops_it(
    client: TestClient, library: Library, matched: list[str], change: str
) -> None:
    """Each write is made straight into the database, so no announcement drops the list for it."""
    path = db_path(client)
    write(
        path,
        [_titled(client, library.shared, "zqxv one"), _titled(client, library.private, "zqxv two")],
    )
    _index(path)
    guest = sign_in(client, "guest")
    share(client, library.shared, guest)
    assert _found(client, "zqxv") == {library.shared}
    assert _found(client, "zqxv") == {library.shared}
    assert matched == [guest]
    if change == "grant taken away":
        write(path, [("DELETE FROM acl_grants WHERE subject_user_id = ?", (guest,))])
        expected: set[str] = set()
    elif change == "file hidden":
        write(
            path,
            [
                (
                    "INSERT INTO asset_user_state (asset_id, user_id, hidden, updated_at)"
                    " VALUES (?, ?, 1, 0)",
                    (library.shared, guest),
                )
            ],
        )
        expected = set()
    elif change == "words indexed":
        write(path, [_titled(client, library.shared, "harbour")])
        _index(path, library.shared)
        expected = set()
    else:
        share(client, library.private, guest)
        expected = {library.shared, library.private}
    assert _found(client, "zqxv") == expected
    assert matched == [guest, guest]
