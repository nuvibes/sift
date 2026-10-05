# SPDX-License-Identifier: AGPL-3.0-or-later
"""A guest's words are answered from their own files: the files the index finds, read without
work that follows the files they may not see."""

from __future__ import annotations

import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
from typing import Any, cast

import pytest

from sift.kernel.access import (
    AllOf,
    AnyOf,
    AssetFilter,
    Effect,
    Not,
    ObjectType,
    Repository,
    Viewer,
    Where,
    fts_contains,
    fts_match,
    index_assets,
)
from sift.kernel.access.repository.read_files import FileReads, WordMatches, words_of
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.tests.access_helpers import _EPOCH
from sift.testing.fixtures import Actors

#: Names a guest is given, each beside one they are not: other cases, a capital `upper()` never
#: makes, a LIKE wildcard, a short term, a song's name.
_SHARED = (
    "\u00c9toile harbour.mp4",
    "\u212aite run.mp4",
    "half 50%_off.mp4",
    "ab cd.mp4",
    "zqxv shared.mp4",
    "zqxw shared.mp4",
)
_KEPT = ("\u00e9toile kept.mp4", "kite kept.mp4", "Half 50%_OFF kept.mp4", "AB kept.mp4")
_HIDDEN_MATCHES = 40


async def _file(db: Database, root_id: str, name: str, music: str | None = None) -> str:
    asset_id = new_id()
    await db.execute(
        "INSERT INTO assets (id, identity, media_type, original_filename, music, added_at)"
        " VALUES (?, ?, 'video', ?, ?, ?)",
        (asset_id, f"digest-{asset_id}", name, music, _EPOCH),
    )
    await db.execute(
        "INSERT INTO asset_locations"
        " (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)"
        " VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
        (new_id(), asset_id, root_id, name, name, _EPOCH, _EPOCH),
    )
    return asset_id


@pytest.fixture
async def library(access: Repository, actors: Actors, temp_db: Database) -> set[str]:
    """The files shared with the guest, beside kept ones and many kept matches for `zqxv`."""
    root_id = new_id()
    await temp_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (root_id, "root", "/library", _EPOCH),
    )
    shared = {await _file(temp_db, root_id, name) for name in _SHARED}
    shared.add(await _file(temp_db, root_id, "untitled.mp4", music="xy theme"))
    for name in _KEPT:
        await _file(temp_db, root_id, name, music="xy kept")
    for n in range(_HIDDEN_MATCHES):
        await _file(temp_db, root_id, f"zqxv kept {n}.mp4")
    await index_assets(temp_db, rebuild=True)
    for asset_id in shared:
        await access.grant(ObjectType.ITEM, asset_id, actors.guest.id, Effect.SHARE)
    return shared


def _searched(words: str) -> AssetFilter:
    """The words as the search box's compiler writes them."""
    halves = (("text_match", fts_match), ("text_contains", fts_contains))
    return AssetFilter(
        where=AllOf(tuple(Where(key) for key, half in halves if half(words) is not None)),
        text=words,
    )


async def _found(
    access: Repository, viewer: Viewer, words: str, kept: WordMatches | None = None
) -> set[str]:
    page = await access.visible_assets(
        viewer, limit=200, asset_filter=_searched(words), sort="relevance", words=kept
    )
    return {item.asset.id for item in page.items}


@pytest.mark.parametrize(
    "words",
    ["\u00e9toile", "\u00c9TOILE", "kite", "KITE", "50%_o", "ab", "AB", "xy", "harbour \u00e9to"],
)
async def test_a_guest_finds_what_the_index_finds_among_their_files(
    access: Repository, actors: Actors, library: set[str], words: str
) -> None:
    every = await _found(access, actors.admin, words)
    assert every & library, "the words find a shared file"
    assert every & library != library, "the words leave a shared file out"
    assert await _found(access, actors.guest, words) == every & library


class _Counted:
    """A plain connection that counts the statement steps every read takes."""

    def __init__(self, path: Any) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.steps = 0
        self.texts: list[str] = []
        self.connection.set_progress_handler(self._step, 1)

    def _step(self) -> int:
        self.steps += 1
        return 0

    async def fetch_all(self, statement: Any, params: Any = ()) -> list[Any]:
        self.texts.append(getattr(statement, "sql", statement))
        return self.connection.execute(self.texts[-1], params).fetchall()

    async def fetch_one(self, statement: Any, params: Any = ()) -> Any:
        rows = await self.fetch_all(statement, params)
        return rows[0] if rows else None


async def _steps(db: Database, viewer: Viewer, words: str, sort: str) -> tuple[int, int]:
    counted = _Counted(db.path)
    try:
        page = await FileReads(cast(Any, counted), cast(Any, None)).visible_assets(
            viewer, limit=50, asset_filter=_searched(words), sort=sort
        )
        return counted.steps, page.total
    finally:
        counted.connection.close()


@pytest.mark.parametrize("sort", ["relevance", "newest"])
async def test_a_guests_search_does_no_work_for_files_they_may_not_see(
    actors: Actors, library: set[str], temp_db: Database, sort: str
) -> None:
    """Two words of one shape, one also in many kept files, cost a guest the same steps."""
    many, none = (
        await _steps(temp_db, actors.guest, "zqxv", sort),
        await _steps(temp_db, actors.guest, "zqxw", sort),
    )
    assert many[1] == none[1] == 1
    assert many[0] == none[0]
    # The count sees the index's work: the admin's read of the same two words differs.
    assert (await _steps(temp_db, actors.admin, "zqxv", sort))[0] != (
        await _steps(temp_db, actors.admin, "zqxw", sort)
    )[0]


async def _address(
    db: Database,
    viewer: Viewer,
    words: str,
    *,
    kept: WordMatches | None = None,
    listing: bool = True,
) -> tuple[int, int, WordMatches | None]:
    """The steps of one address: its word list unless one is kept, the page, its total, two columns."""
    counted = _Counted(db.path)
    reads = FileReads(cast(Any, counted), cast(Any, None))
    try:
        searched = _searched(words)
        listed = kept or (await reads.word_matches(viewer, searched) if listing else None)
        page = await reads.visible_assets(
            viewer, limit=50, asset_filter=searched, sort="relevance", words=listed
        )
        for facet in ("tags", "media"):
            await reads.facet_counts(viewer, facet, asset_filter=searched, words=listed)
        return counted.steps, page.total, listed
    finally:
        counted.connection.close()


@pytest.mark.parametrize("listing", [True, False])
async def test_a_guests_counts_and_kept_list_do_no_work_for_files_they_may_not_see(
    actors: Actors, library: set[str], temp_db: Database, listing: bool
) -> None:
    """The counts and the total too, and a second read from the kept list, cheaper and equal."""
    many = await _address(temp_db, actors.guest, "zqxv", listing=listing)
    none = await _address(temp_db, actors.guest, "zqxw", listing=listing)
    assert many[1] == none[1] == 1
    assert many[0] == none[0]
    if listing:
        assert many[2] is not None and none[2] is not None
        again = await _address(temp_db, actors.guest, "zqxv", kept=many[2])
        twin = await _address(temp_db, actors.guest, "zqxw", kept=none[2])
        assert again[1] == twin[1] == 1
        assert again[0] == twin[0] < many[0]
    assert (await _address(temp_db, actors.admin, "zqxv"))[0] != (
        await _address(temp_db, actors.admin, "zqxw")
    )[0]


async def test_a_kept_list_answers_only_its_own_viewer_and_words(
    access: Repository, actors: Actors, library: set[str]
) -> None:
    """A list is taken as given only by its own viewer for its own words, never by an admin."""
    twin = await access.word_matches(actors.guest, _searched("zqxw"))
    assert twin is not None and twin.count == 1
    asked = words_of(_searched("zqxv"))
    assert asked is not None
    found = await _found(access, actors.guest, "zqxv")
    assert (
        await _found(access, actors.guest, "zqxv", WordMatches(actors.guest.id, asked, twin.ids, 1))
        != found
    )
    assert await _found(access, actors.guest, "zqxv", twin) == found
    assert (
        await _found(access, actors.guest, "zqxv", WordMatches(new_id(), asked, twin.ids, 1))
        == found
    )
    every = await _found(access, actors.admin, "zqxv")
    assert len(every) == _HIDDEN_MATCHES + 1
    assert (
        await _found(access, actors.admin, "zqxv", WordMatches(actors.admin.id, asked, twin.ids, 1))
        == every
    )
    assert await access.word_matches(actors.admin, _searched("zqxv")) is None


@pytest.mark.parametrize(
    "where",
    [Not(Where("text_contains")), AnyOf((Where("text_contains"), Where("media_type", ("image",))))],
)
async def test_words_under_an_or_or_a_not_are_matched_in_place(
    access: Repository, actors: Actors, library: set[str], where: Any
) -> None:
    asked = AssetFilter(where=AllOf((Where("text_match"), where)), text="zqxv ab")
    assert words_of(asked) is None
    assert await access.word_matches(actors.guest, asked) is None


async def test_a_page_read_from_a_list_reads_its_rows_without_it(
    actors: Actors, library: set[str], temp_db: Database
) -> None:
    """The second read of a sorted page takes the ids the first kept, every rule still applied."""
    counted = _Counted(temp_db.path)
    reads = FileReads(cast(Any, counted), cast(Any, None))
    try:
        kept = await reads.word_matches(actors.guest, _searched("zqxv shared"))
        page = await reads.visible_assets(
            actors.guest, asset_filter=_searched("zqxv shared"), sort="relevance", words=kept
        )
    finally:
        counted.connection.close()
    assert page.total == 1
    walk = counted.texts[-2]
    assert walk.count("json_each(:word_ids)") == 1 and "paged_id" in walk
    assert "assets_fts_rows" not in walk
