# SPDX-License-Identifier: AGPL-3.0-or-later
"""A walk and a count asked for some library folders stay inside them; a whole one is unchanged.

A Build over some folders counts its files with `count_lacking` and hands them out a page at a
time with `asset_ids_page`, both given the same `roots`. Every page has to stay inside the folders
asked for, and the count has to be the number of files those pages hand out, or the run's bar and
the files it touches describe two different sets.
"""

from __future__ import annotations

import pytest

from sift.kernel.content import ContentStore
from sift.kernel.content.identity import Lack
from sift.kernel.db import Database
from sift.kernel.ids import new_id

pytestmark = pytest.mark.unit

_EPOCH = 1_700_000_000


async def _root(database: Database, name: str) -> str:
    root_id = new_id()
    await database.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (root_id, name, f"/library/{name}", _EPOCH),
    )
    return root_id


async def _file(database: Database, at: int, *copies: tuple[str, str], kind: str = "video") -> str:
    """A read file with a copy in each named folder, each `(root id, status)`."""
    asset_id = new_id()
    await database.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, added_at, probed_at)"
        " VALUES (?, ?, 1, ?, ?, ?)",
        (asset_id, f"digest-{asset_id}", kind, at, _EPOCH),
    )
    for root_id, status in copies:
        await database.execute(
            "INSERT INTO asset_locations"
            " (id, asset_id, root_id, rel_path, filename, status, first_seen_at, last_seen_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id(), asset_id, root_id, f"{asset_id}.mp4", f"{asset_id}.mp4", status, at, at),
        )
    return asset_id


async def _walk(content: ContentStore, roots: list[str] | None) -> list[list[str]]:
    """Every page of a walk two files at a time, as the Build pages through it."""
    pages: list[list[str]] = []
    after = None
    while True:
        page = await content.asset_ids_page(after=after, limit=2, roots=roots)
        if page.ids:
            pages.append(page.ids)
        if len(page.ids) < 2 or page.last is None:
            return pages
        after = page.last


async def test_every_page_of_a_walk_over_some_folders_stays_inside_them(
    temp_db: Database, content_store: ContentStore
) -> None:
    holidays = await _root(temp_db, "holidays")
    garden = await _root(temp_db, "garden")
    # Interleaved by when they arrived, so a page that ignored the folders would mix them.
    inside = [await _file(temp_db, _EPOCH + n, (holidays, "present")) for n in range(0, 10, 2)]
    outside = [await _file(temp_db, _EPOCH + n, (garden, "present")) for n in range(1, 10, 2)]
    # In both folders: inside, because one of the folders asked for holds it.
    both = await _file(temp_db, _EPOCH + 20, (garden, "present"), (holidays, "present"))
    # Its copy in the folder asked for is gone: nothing there to read, so not inside.
    gone_here = await _file(temp_db, _EPOCH + 21, (holidays, "missing"), (garden, "present"))

    pages = await _walk(content_store, [holidays])

    walked = [one for page in pages for one in page]
    assert walked == [*inside, both]
    assert len(pages) == 3, "the walk still goes a page at a time"
    assert not set(walked) & {*outside, gone_here}

    everything = Lack("1")
    counted = await content_store.count_lacking([everything], roots=[holidays])
    assert counted.files == len(walked) == 6
    by_kind = await content_store.count_lacking_by_kind([everything], roots=[holidays])
    assert by_kind["video"].files == 6


async def test_a_whole_walk_and_count_are_the_library_and_no_folder_is_nothing(
    temp_db: Database, content_store: ContentStore
) -> None:
    holidays = await _root(temp_db, "holidays")
    garden = await _root(temp_db, "garden")
    files = [
        await _file(temp_db, _EPOCH + n, (holidays if n % 2 else garden, "present"))
        for n in range(5)
    ]

    whole = [one for page in await _walk(content_store, None) for one in page]
    assert whole == files
    everything = Lack("1")
    assert (await content_store.count_lacking([everything])).files == 5
    assert (await content_store.count_lacking([everything], roots=[holidays, garden])).files == 5
    # Asked for folders and given none, nothing: never the whole library by accident.
    assert await _walk(content_store, []) == []
    assert (await content_store.count_lacking([everything], roots=[])).files == 0
