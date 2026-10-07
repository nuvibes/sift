# SPDX-License-Identifier: AGPL-3.0-or-later
"""The download list is narrowed and ordered on the SERVER, because it is paged.

A state tab, a Site, the search and the order that narrowed only the fifty rows a screen held
answered "which of these fifty", while every one of them is labelled with a question about the
whole queue. Each test here asks for a page SMALLER than what the narrowing keeps, so a filter that
ran over a page instead of the queue shows up as a wrong count or a missing row.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sift.kernel.db import Database
from sift.slices.download import service_listing as download_service_module
from sift.slices.download.service import (
    DownloadNarrowing,
    DownloadService,
    FileFacts,
)
from sift.slices.download.sources import url_hash


async def _row(
    db: Database,
    download_id: str,
    *,
    state: str,
    url: str,
    at: int,
    filename: str | None = None,
    asset_id: str | None = None,
) -> None:
    await db.execute(
        "INSERT INTO downloads (id, url, url_hash, state, asset_id, created_at, filename) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (download_id, url, url_hash(f"{url}#{download_id}"), state, asset_id, at, filename),
    )


async def _a_mixed_queue(db: Database) -> None:
    """Five done, two failed, one queued, across two Sites, at distinct moments."""
    await _row(
        db, "d1", state="done", url="https://www.youtube.com/watch?v=1", at=10, filename="b.mp4"
    )
    await _row(db, "d2", state="done", url="https://youtu.be/2", at=20, filename="a.mp4")
    await _row(db, "d3", state="done", url="https://example.org/3", at=30, filename="e.mp4")
    await _row(db, "d4", state="done", url="https://example.org/4", at=40, filename="c.mp4")
    await _row(
        db, "d5", state="done", url="https://www.youtube.com/watch?v=5", at=50, filename="d.mp4"
    )
    await _row(db, "f1", state="failed", url="https://youtu.be/6", at=60)
    await _row(db, "f2", state="failed", url="https://example.org/7", at=70)
    await _row(db, "q1", state="queued", url="https://example.org/8", at=80)


async def _files_for_the_done_rows(db: Database) -> None:
    """A file under every done row, so a row can point at one. `x<row id>` each."""
    for one in ("d1", "d2", "d3", "d4", "d5"):
        await db.execute(
            "INSERT INTO assets (id, identity, media_type, added_at) VALUES (?, ?, 'video', 0)",
            (f"x{one}", f"hash-{one}"),
        )
        await db.execute("UPDATE downloads SET asset_id = ? WHERE id = ?", (f"x{one}", one))


async def test_the_narrowing_is_one_rule() -> None:
    """The fold, the host and the search words open every statement, written out and identical.

    They cannot be joined in (the SQL rule refuses a joined statement in a slice), so the copies are
    held to one text here: a tab whose statement folded a blocked job differently from the chip
    counts would say "Needs you 3" over a list of two.
    """
    shown = download_service_module._SHOWN_ROWS.strip()
    narrowed = download_service_module._NARROWED.strip()
    for statement in (
        download_service_module._LIST_DOWNLOADS,
        download_service_module._MATCHING,
        download_service_module._COUNT_MATCHING,
        download_service_module._STATE_COUNTS,
        download_service_module._HOST_COUNTS,
        download_service_module._RAIL_COUNTS,
    ):
        assert shown in statement
    for statement in (
        download_service_module._LIST_DOWNLOADS,
        download_service_module._MATCHING,
        download_service_module._COUNT_MATCHING,
    ):
        assert narrowed in statement


async def test_a_state_tab_narrows_the_whole_queue_not_the_page(
    download_service: DownloadService, temp_db: Database
) -> None:
    await _a_mixed_queue(temp_db)

    page = await download_service.list_downloads(limit=2, narrowing=DownloadNarrowing(show="done"))
    assert page.matched == 5
    assert page.total == 8
    assert [one.id for one in page.downloads] == ["d5", "d4"]
    assert page.counts == {"done": 5, "failed": 2, "queued": 1}

    deeper = await download_service.list_downloads(
        limit=2, offset=4, narrowing=DownloadNarrowing(show="done")
    )
    assert [one.id for one in deeper.downloads] == ["d1"]

    needs = await download_service.list_downloads(
        limit=10, narrowing=DownloadNarrowing(show="needs")
    )
    assert {one.id for one in needs.downloads} == {"f1", "f2"}
    assert needs.matched == 2


async def test_a_site_narrows_by_every_host_it_owns(
    download_service: DownloadService, temp_db: Database
) -> None:
    """YouTube is two hosts here. The Site menu names it once, with both hosts' rows counted."""
    await _a_mixed_queue(temp_db)

    whole = await download_service.list_downloads(limit=1)
    assert {(one.name, one.count) for one in whole.sites} == {("YouTube", 4), ("Example", 4)}

    page = await download_service.list_downloads(
        limit=2, narrowing=DownloadNarrowing(sites=("youtube",))
    )
    assert page.matched == 4
    assert [one.id for one in page.downloads] == ["f1", "d5"]
    # The tabs count inside the Site; the header and the strip still count the whole queue.
    assert page.counts == {"done": 3, "failed": 1}
    assert page.by_state == {"done": 5, "failed": 2, "queued": 1}

    both = await download_service.list_downloads(
        limit=10, narrowing=DownloadNarrowing(show="failed", sites=("YouTube",))
    )
    assert [one.id for one in both.downloads] == ["f1"]
    # The menu counts inside the lit tab.
    assert {(one.name, one.count) for one in both.sites} == {("YouTube", 1), ("Example", 1)}

    nowhere = await download_service.list_downloads(
        limit=10, narrowing=DownloadNarrowing(sites=("A site with nothing here",))
    )
    assert (nowhere.downloads, nowhere.matched) == ([], 0)


async def test_a_pick_is_read_through_the_one_quote_rule(
    download_service: DownloadService, temp_db: Database
) -> None:
    """The walls spell a refusal as a minus outside the quotes and a name as written inside them,
    so a Site whose name begins with a minus is a name, and `-youtube` is everything else."""
    await _a_mixed_queue(temp_db)

    refused = await download_service.list_downloads(
        limit=10, narrowing=DownloadNarrowing(sites=("-youtube",))
    )
    assert refused.matched == 4, "everything that is not YouTube"
    quoted = await download_service.list_downloads(
        limit=10, narrowing=DownloadNarrowing(sites=('"youtube"',))
    )
    assert quoted.matched == 4, "quotes hold the name as written"
    named_with_a_minus = await download_service.list_downloads(
        limit=10, narrowing=DownloadNarrowing(sites=('"-youtube"',))
    )
    assert named_with_a_minus.matched == 0, "a name that begins with a minus, which no Site has"


async def test_several_sites_are_either_of_them(
    download_service: DownloadService, temp_db: Database
) -> None:
    """Two Sites ticked in the filter panel is the rows of either: the "or" every wall's facet
    reads a repeated value as. A blank name narrows nothing rather than everything away."""
    await _a_mixed_queue(temp_db)

    either = await download_service.list_downloads(
        limit=20, narrowing=DownloadNarrowing(sites=("YouTube", "example"))
    )
    assert either.matched == 8

    one = await download_service.list_downloads(
        limit=20, narrowing=DownloadNarrowing(sites=("Example", "A site with nothing here"))
    )
    assert one.matched == 4

    blank = await download_service.list_downloads(
        limit=20, narrowing=DownloadNarrowing(sites=(" ",))
    )
    assert blank.matched == blank.total


async def test_a_refused_site_is_every_row_but_its_own(
    download_service: DownloadService, temp_db: Database
) -> None:
    """A Site named with a leading minus is "not this", the spelling every wall reads: its rows are
    the ones left out, across every host it owns, and a refusal applies on top of a pick."""
    await _a_mixed_queue(temp_db)

    not_youtube = await download_service.list_downloads(
        limit=2, narrowing=DownloadNarrowing(sites=("-youtube",))
    )
    assert not_youtube.matched == 4
    assert [one.id for one in not_youtube.downloads] == ["q1", "f2"]
    # The tabs count what pressing them would show; the header still counts the whole queue.
    assert not_youtube.counts == {"done": 2, "failed": 1, "queued": 1}
    assert not_youtube.by_state == {"done": 5, "failed": 2, "queued": 1}

    by_name = await download_service.list_downloads(
        limit=20, narrowing=DownloadNarrowing(sites=("-Example",), sort="name_az")
    )
    assert {one.id for one in by_name.downloads} == {"d1", "d2", "d5", "f1"}

    picked_and_refused = await download_service.list_downloads(
        limit=20, narrowing=DownloadNarrowing(sites=("YouTube", "-YouTube"))
    )
    assert picked_and_refused.matched == 0

    # A lone minus is a name, not a refusal, as it is on every wall.
    lone = await download_service.list_downloads(
        limit=20, narrowing=DownloadNarrowing(sites=("-",))
    )
    assert lone.matched == 0


async def test_the_site_column_counts_inside_the_tab(
    download_service: DownloadService, temp_db: Database
) -> None:
    """The filter panel's Site column: the same fold the list's own `sites` is, from one statement."""
    await _a_mixed_queue(temp_db)

    assert {(one.name, one.count) for one in await download_service.site_counts()} == {
        ("YouTube", 4),
        ("Example", 4),
    }
    assert {(one.name, one.count) for one in await download_service.site_counts("failed")} == {
        ("YouTube", 1),
        ("Example", 1),
    }
    # A tab nobody declared is the whole queue, never an error from a count.
    assert len(await download_service.site_counts("everything")) == 2


async def test_the_search_reads_the_whole_queue(
    download_service: DownloadService, temp_db: Database
) -> None:
    await _a_mixed_queue(temp_db)

    page = await download_service.list_downloads(
        limit=1, narrowing=DownloadNarrowing(search="  A.MP4 ")
    )
    assert [one.id for one in page.downloads] == ["d2"]
    assert page.matched == 1
    # The tabs are not narrowed by what is typed.
    assert page.counts["done"] == 5


async def test_every_order_reads_the_whole_queue(
    download_service: DownloadService, temp_db: Database
) -> None:
    await _a_mixed_queue(temp_db)
    await _files_for_the_done_rows(temp_db)

    sizes = {"xd1": 300, "xd2": 100, "xd3": 500, "xd4": 200, "xd5": 400}

    async def files_of(ids: Sequence[str]) -> Mapping[str, FileFacts]:
        return {one: FileFacts(name=None, size=sizes[one]) for one in ids if one in sizes}

    async def order(sort: str) -> list[str]:
        page = await download_service.list_downloads(
            limit=3, narrowing=DownloadNarrowing(show="done", sort=sort), files_of=files_of
        )
        assert page.matched == 5
        return [one.id for one in page.downloads]

    assert await order("newest") == ["d5", "d4", "d3"]
    assert await order("oldest") == ["d1", "d2", "d3"]
    # The keys are every other wall's (`name_az`, `largest`, ...), and each has its other way round.
    assert await order("name_az") == ["d2", "d1", "d4"]
    assert await order("name_za") == ["d3", "d5", "d4"]
    assert await order("largest") == ["d3", "d5", "d1"]
    assert await order("smallest") == ["d2", "d4", "d1"]
    # Example before YouTube, newest first inside each.
    assert await order("site") == ["d4", "d3", "d5"]

    # And the second page of an order worked out in Python is the next three, not a re-read.
    later = await download_service.list_downloads(
        limit=3,
        offset=3,
        narrowing=DownloadNarrowing(show="done", sort="largest"),
        files_of=files_of,
    )
    assert [one.id for one in later.downloads] == ["d4", "d2"]


async def test_a_page_past_the_end_of_an_order_by_file_is_empty_and_still_counts_the_whole(
    download_service: DownloadService, temp_db: Database
) -> None:
    """A pager that asks for a page beyond the last gets no rows, and the total it divides by."""
    await _a_mixed_queue(temp_db)
    await _files_for_the_done_rows(temp_db)

    async def files_of(ids: Sequence[str]) -> Mapping[str, FileFacts]:
        return {one: FileFacts(name=None, size=1) for one in ids}

    page = await download_service.list_downloads(
        limit=3,
        offset=6,
        narrowing=DownloadNarrowing(show="done", sort="largest"),
        files_of=files_of,
    )

    assert page.downloads == []
    assert page.matched == 5


async def test_a_file_the_viewer_may_not_see_sorts_as_no_file(
    download_service: DownloadService, temp_db: Database
) -> None:
    """Size comes from the access layer. A file it withholds is last, never ordered by its size."""
    await _a_mixed_queue(temp_db)
    await _files_for_the_done_rows(temp_db)

    async def files_of(ids: Sequence[str]) -> Mapping[str, FileFacts]:
        return {"xd2": FileFacts(name=None, size=1)}

    page = await download_service.list_downloads(
        limit=10, narrowing=DownloadNarrowing(show="done", sort="largest"), files_of=files_of
    )
    assert page.downloads[0].id == "d2"
    # Smallest first as well: a withheld file is "no file", not a very small one, so d2 still leads.
    smallest = await download_service.list_downloads(
        limit=10, narrowing=DownloadNarrowing(show="done", sort="smallest"), files_of=files_of
    )
    assert smallest.downloads[0].id == "d2"
