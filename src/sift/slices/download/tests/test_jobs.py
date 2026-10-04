# SPDX-License-Identifier: AGPL-3.0-or-later
"""The download job, end to end, with the tool and the import pipeline faked at their seams.

The seams are faked; nothing else is. The ledger is real, the ingress gate is real, the content
store is real, and the master key is a real key. What is stubbed is the one thing a test must not
do (reach the internet) and the one slice this one does not own, the import pipeline, which is
run here by a fake that still puts every file through the real gate.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from sift.kernel.content import ContentStore, LibraryStore
from sift.kernel.db import Database
from sift.kernel.destination import FOLDER_GONE, NOTHING_CHOSEN
from sift.kernel.ids import new_id
from sift.kernel.ingress import IngressRejected, Origin, Reason
from sift.kernel.jobs import (
    JobBlocked,
    JobContext,
    JobFailedPermanently,
    JobPaused,
)
from sift.kernel.jobs.workspaces import Workspaces
from sift.slices.download import attempt, jobs
from sift.slices.download.jobs import download
from sift.slices.download.service import DownloadService
from sift.slices.download.site_options import SiteOptions
from sift.slices.download.sources import progress, url_hash
from sift.slices.download.sources.errors import (
    NO_ANSWER_MESSAGE,
    DownloadError,
    LoginRequired,
    NoAnswer,
    NothingFound,
)
from sift.slices.download.sources.progress import Report, nowhere
from sift.slices.download.sources.resolved import Fetched
from sift.slices.download.tests.conftest import (
    FakeFetcher,
    FakeOutcome,
    MakeContext,
    RealImport,
    RecordingReindexer,
    png_bytes,
)
from sift.slices.download.tests.jobs_support import (
    _URL,
    PausedMidFetch,
    PausedMidFetchReporting,
    _caps,
    _no_real_guard,  # noqa: F401 (autouse)
    _RecordingFiler,
    _RefusesWithoutCookies,
    _seed_download,
    _sites_on,
    _TwoFileFetcher,
    _username_from,
    _view,
)
from sift.slices.download.url_guard import UrlRejected


async def test_a_download_with_nowhere_to_land_fails_before_the_fetch(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """No folder named and none set for its Site: refused with the import's own sentence, and the
    tool is never asked, so nothing is fetched only to be thrown away."""
    await _seed_download(temp_db, "d1", landing=False)
    fetcher = FakeFetcher(png_bytes())

    with pytest.raises(JobFailedPermanently):
        await download(
            await make_context(
                {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
            ),
            service=download_service,
            downloader=fetcher,
            import_file=real_import,
        )

    assert fetcher.calls == []
    view = await _view(download_service, "d1")
    assert view.status == "failed"
    assert view.error == NOTHING_CHOSEN


async def test_a_download_whose_folder_has_gone_fails_before_the_fetch(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """A folder deleted since it was chosen for the Site is the same refusal, asked of the library."""
    await _seed_download(temp_db, "d1", landing=False)
    fetcher = FakeFetcher(png_bytes())

    async def a_deleted_folder(_key: str | None) -> SiteOptions:
        return SiteOptions(dest_folder_id=new_id())

    with pytest.raises(JobFailedPermanently):
        await download(
            await make_context(
                {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
            ),
            service=download_service,
            downloader=fetcher,
            import_file=real_import,
            read_site_options=a_deleted_folder,
        )

    assert fetcher.calls == []
    assert (await _view(download_service, "d1")).error == FOLDER_GONE


async def test_a_paused_download_keeps_what_arrived_and_settles_as_paused(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
    workspaces: Workspaces,
) -> None:
    """The whole of what a pause is worth: the bytes stay, and the job says so.

    The partial is asserted to still be on disk after the handler has returned: the workspace is
    the job's own, not a temporary directory the handler makes and unmakes, and the queue keeps a
    paused job's.
    """
    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"},
        capabilities=_caps(content_store, library_store, key=None, workspaces=workspaces),
    )
    # What the heartbeat does when somebody presses Pause, through the kernel's own seam rather
    # than by replacing the method: the handler reads `stopping()` and this is what sets it.
    context.told_to_stop("pause")
    fetcher = PausedMidFetch(png_bytes()[:20])

    with pytest.raises(JobPaused):
        await download(
            context,
            service=download_service,
            downloader=fetcher,
            import_file=real_import,
        )

    assert fetcher.wrote is not None
    assert fetcher.wrote.exists(), "the partial is still there for the resumed run to continue"
    assert fetcher.wrote.read_bytes() == png_bytes()[:20]
    # Nothing was imported and nothing was called a failure: the row keeps whatever the route that
    # asked for the pause wrote on it.
    assert (await _view(download_service, "d1")).status != "failed"


async def test_a_paused_download_keeps_its_figures_on_screen_without_a_rate(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
    workspaces: Workspaces,
) -> None:
    """A paused row still says what is kept ("20 of 226 bytes kept"), and nothing it says is about a
    transfer that has stopped: no speed and no time left, which summed into the strip above the queue
    would read as a download still running. Forgetting the row instead would draw it with no bar."""
    await _seed_download(temp_db, "d1")
    watching = progress.Registry()
    context = await make_context(
        {"download_id": "d1"},
        capabilities=_caps(content_store, library_store, key=None, workspaces=workspaces),
    )
    context.told_to_stop("pause")

    with pytest.raises(JobPaused):
        await download(
            context,
            service=download_service,
            downloader=PausedMidFetchReporting(png_bytes()[:20]),
            import_file=real_import,
            watching=watching,
        )

    held = watching.of("d1")
    assert held is not None, "the paused row keeps its figures"
    assert (held.done_bytes, held.total_bytes) == (20, 226)
    assert held.bytes_per_second is None
    assert held.seconds_left is None


async def test_a_site_that_wants_cookies_nobody_saved_waits_rather_than_failing(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """The row waits and offers Add cookies; it does not fail and offer Try again.

    Caught before the clause that records a permanent refusal, which is the whole of the risk:
    `CookiesNeeded` is a `LoginRequired`, deliberately, so that a handler which does not know it
    treats it as a login wall, and one that knows it but catches it second turns the wait
    silently into a failure.
    """
    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    with pytest.raises(JobBlocked):
        await download(
            context,
            service=download_service,
            downloader=_RefusesWithoutCookies(),
            import_file=real_import,
        )

    view = await _view(download_service, "d1")
    assert view.status != "failed", "it is waiting on somebody, not finished with"


async def test_a_download_lands_and_is_attributed(
    download_service: DownloadService,
    reindexer: RecordingReindexer,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
    )

    view = await _view(download_service, "d1")
    assert view.status == "done"
    assert view.asset_id is not None
    assert view.site == "TikTok"
    assert view.username == "creator"

    # The site, the username, and the link to the asset all exist.
    site = await temp_db.fetch_one("SELECT id FROM sites WHERE name = 'TikTok'")
    username = await temp_db.fetch_one("SELECT id FROM usernames WHERE name = 'creator'")
    assert site is not None and username is not None
    link = await temp_db.fetch_one(
        "SELECT 1 FROM asset_usernames WHERE asset_id = ? AND username_id = ?",
        (view.asset_id, username["id"]),
    )
    assert link is not None

    # The username is indexed text, and attribution happens AFTER the asset was imported. A catch-up
    # landing between the two would index the asset without it, and nothing would revisit it until
    # a rebuild: the same staleness as any other edit, through a narrower window.
    assert reindexer.touched_ids == [view.asset_id]


async def test_a_link_dropped_on_a_SITE_is_filed_under_it_AND_under_the_one_it_came_from(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """A drop adds. It replaces nothing.

    A file can come from one Site and belong to another (a mirror, a cross-post, a re-upload),
    so the address's site and the drop's are not rivals. And the costs are not comparable: an
    extra filing is one row on the file that one press removes, while a dropped one is a fact
    nothing else records, lost silently.

    Four assertions together, and each alone would pass against something wrong: the address's site
    recorded, the drop's Site asked for as well, the person still named, and a control with no
    aim on it proving neither half is simply always there.
    """
    await _seed_download(temp_db, "d1")
    # A Site to aim at, which is not the one the address names.
    await temp_db.execute("INSERT INTO sites (id, name) VALUES (?, ?)", ("p-aimed", "Pmvhaven"))
    await temp_db.execute(
        "UPDATE downloads SET aimed_kind = 'site', aimed_id = ?, aimed_by = ? WHERE id = ?",
        ("p-aimed", "u1", "d1"),
    )
    # The person the address names, already in the library. Seeded rather than relying on one being
    # created: the job's `may_create_people` defaults to never, and an existing person matched by
    # username is the ordinary case anyway.
    await temp_db.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, ?, ?, 0)",
        ("person-1", "creator", "creator"),
    )
    filer = _RecordingFiler()
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
        file_under=filer,
    )

    view = await _view(download_service, "d1")
    assert view.status == "done"
    assert view.asset_id is not None
    # The download's OWN row still records where the bytes came from.
    assert view.site == "TikTok"

    # The address's answer, which a drop on a Site must not suppress.
    assert await _sites_on(temp_db, view.asset_id) == {"TikTok"}, (
        "a drop on a Site replaced the address's answer instead of joining it"
    )
    # And the drop's answer, asked for as well rather than instead. Which table the filing writes is
    # `test_composition.py`'s question; that it is ASKED FOR alongside the other is this one's.
    assert [(call["kind"], call["target_id"]) for call in filer.calls] == [("site", "p-aimed")]

    # Who is untouched by any of it.
    people = await temp_db.fetch_all(
        "SELECT p.name FROM asset_people ap JOIN people p ON p.id = ap.person_id"
        " WHERE ap.asset_id = ?",
        (view.asset_id,),
    )
    assert {str(row["name"]) for row in people} == {"creator"}

    # THE CONTROL: the same fetch with no aim on it is filed under the address's site and nothing
    # else. Without it the set above would pass just as well against a job that files everywhere.
    # A different address, or the ledger recognises it and skips the fetch entirely.
    await _seed_download(temp_db, "d2", url="https://www.tiktok.com/@creator/video/2")
    plain = await make_context(
        {"download_id": "d2"}, capabilities=_caps(content_store, library_store, key=None)
    )
    await download(
        plain,
        service=download_service,
        downloader=FakeFetcher(png_bytes(b"\x00\xff\x00\x00")),
        import_file=real_import,
    )
    unaimed = await _view(download_service, "d2")
    assert unaimed.asset_id is not None
    assert await _sites_on(temp_db, unaimed.asset_id) == {"TikTok"}
    assert len(filer.calls) == 1, "a download nobody aimed was filed somewhere"


async def test_a_download_is_stopped_when_the_disk_runs_low(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fetch that would fill the disk is cut off, the ledger says why, and the tool is actually
    stopped: the cancellation reaches the fetcher rather than leaving it running in the background."""
    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    cancelled = False

    class _EndlessFetcher:
        def handles(self, _url: str) -> bool:
            return True

        async def fetch(
            self,
            _url: str,
            *,
            into: Path,
            cookies_file: Path | None = None,
            proxy: str | None = None,
            already_have: Callable[[str], Awaitable[bool]] | None = None,
            report: Report = nowhere,
        ) -> Fetched:
            nonlocal cancelled
            try:
                await asyncio.sleep(30)  # never finishes on its own within the test
            except asyncio.CancelledError:
                cancelled = True
                raise
            return Fetched(files=[])  # pragma: no cover (the sleep is cancelled first)

    # A full disk, checked almost immediately, so the guard trips before the fetch could complete.
    monkeypatch.setattr(attempt, "_DISK_CHECK_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(attempt, "_free_bytes", lambda _path: 0)

    with pytest.raises(JobFailedPermanently):
        await download(
            context,
            service=download_service,
            downloader=_EndlessFetcher(),
            import_file=real_import,
        )

    view = await _view(download_service, "d1")
    assert view.status == "failed"
    assert "disk" in (view.error or "").lower()
    assert cancelled, "the fetch must be cancelled, not left running in the background"


@pytest.mark.parametrize(("floor_gb", "stopped"), [(8, True), (2, False)])
async def test_the_room_to_leave_on_the_disk_is_the_number_on_downloads(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
    monkeypatch: pytest.MonkeyPatch,
    floor_gb: int,
    stopped: bool,
) -> None:
    """Six gigabytes free sits between the two numbers and above the starting five, so only the
    stored number decides whether the fetch is cut off."""
    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )
    cancelled = False

    class _SlowFetcher:
        def handles(self, _url: str) -> bool:
            return True

        async def fetch(
            self,
            _url: str,
            *,
            into: Path,
            cookies_file: Path | None = None,
            proxy: str | None = None,
            already_have: Callable[[str], Awaitable[bool]] | None = None,
            report: Report = nowhere,
        ) -> Fetched:
            nonlocal cancelled
            try:
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                cancelled = True
                raise
            return Fetched(files=[])

    async def stored_floor() -> int:
        return floor_gb

    monkeypatch.setattr(attempt, "_DISK_CHECK_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(attempt, "_free_bytes", lambda _path: 6 * 1024**3)

    with contextlib.suppress(JobFailedPermanently):
        await download(
            context,
            service=download_service,
            downloader=_SlowFetcher(),
            import_file=real_import,
            read_disk_floor=stored_floor,
        )

    error = (await _view(download_service, "d1")).error or ""
    assert cancelled is stopped
    assert ("disk" in error.lower()) is stopped


async def test_a_re_dropped_link_is_skipped(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    # An earlier, finished download of the same URL, and the file it produced, which is what makes
    # it a copy somebody already has. A settled row whose asset has since been deleted is no longer
    # a reason to skip: there is nothing left for the re-paste to be a duplicate of.
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES ('a-earlier', 'h', 'video', 0)"
    )
    await temp_db.execute(
        "INSERT INTO downloads (id, url, url_hash, state, asset_id, created_at)"
        " VALUES (?, ?, ?, 'done', 'a-earlier', 0)",
        ("earlier", _URL, url_hash(_URL)),
    )
    await _seed_download(temp_db, "d1")
    fetcher = FakeFetcher(png_bytes())
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=fetcher,
        import_file=real_import,
    )

    assert (await _view(download_service, "d1")).status == "skipped"
    assert fetcher.calls == []  # nothing was fetched


async def test_a_re_dropped_link_is_fetched_again_when_downloads_does_not_skip_repeats(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """The test above with the setting turned off on `Settings > Downloads` and no choice on the
    paste: the setting is what decided the skip, so off, the link is fetched."""
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES ('a-earlier', 'h', 'video', 0)"
    )
    await temp_db.execute(
        "INSERT INTO downloads (id, url, url_hash, state, asset_id, created_at)"
        " VALUES (?, ?, ?, 'done', 'a-earlier', 0)",
        ("earlier", _URL, url_hash(_URL)),
    )
    await _seed_download(temp_db, "d1")
    fetcher = FakeFetcher(png_bytes())
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    async def setting_off() -> bool:
        return False

    await download(
        context,
        service=download_service,
        downloader=fetcher,
        import_file=real_import,
        remember_downloads=setting_off,
    )

    assert (await _view(download_service, "d1")).status != "skipped"
    assert fetcher.calls, "a link the setting said to fetch again was skipped"


async def test_a_paste_that_chose_to_download_repeats_is_fetched_whatever_the_setting_says(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """The page decides for THIS download. The same finished earlier row as the test above, the
    setting still on, and a paste that turned the skip off for itself is fetched, not skipped."""
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES ('a-earlier', 'h', 'video', 0)"
    )
    await temp_db.execute(
        "INSERT INTO downloads (id, url, url_hash, state, asset_id, created_at)"
        " VALUES (?, ?, ?, 'done', 'a-earlier', 0)",
        ("earlier", _URL, url_hash(_URL)),
    )
    await _seed_download(temp_db, "d1")
    await temp_db.execute("UPDATE downloads SET remember = 0 WHERE id = 'd1'")
    fetcher = FakeFetcher(png_bytes())
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    async def setting_on() -> bool:
        return True

    await download(
        context,
        service=download_service,
        downloader=fetcher,
        import_file=real_import,
        remember_downloads=setting_on,
    )

    assert (await _view(download_service, "d1")).status != "skipped"
    assert fetcher.calls, "the paste's own answer was overridden by the setting"


async def test_a_collection_url_is_re_fetched_even_when_already_done(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    # An Instagram story tray changes through the day, so a finished earlier run of the same URL must
    # NOT skip it: it is fetched again, and repeats are caught per file by the library's dedup.
    story = "https://www.instagram.com/stories/someuser/"
    await temp_db.execute(
        "INSERT INTO downloads (id, url, url_hash, state, created_at) VALUES (?, ?, ?, 'done', 0)",
        ("earlier", story, url_hash(story)),
    )
    await _seed_download(temp_db, "d1", url=story)
    fetcher = FakeFetcher(png_bytes())
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=fetcher,
        import_file=real_import,
    )

    assert (await _view(download_service, "d1")).status != "skipped"  # not skipped: re-resolved
    assert len(fetcher.calls) == 1  # it actually fetched


async def test_a_re_dropped_link_is_fetched_when_the_job_says_to_pass_the_ledger(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """The same set-up as the skip above, and the opposite answer, because of one payload flag.

    Written against a ledger that WOULD skip it: an install where nothing had been fetched before
    would fetch it either way, so the flag could be deleted and the test would still pass.

    The earlier row needs its file as well as its state: a settled row whose asset has since been
    deleted is no reason to skip, so without the file this would seed a row the ledger fetches
    anyway and the flag would stop mattering.
    """
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES ('a-earlier', 'h', 'video', 0)"
    )
    await temp_db.execute(
        "INSERT INTO downloads (id, url, url_hash, state, asset_id, created_at)"
        " VALUES (?, ?, ?, 'done', 'a-earlier', 0)",
        ("earlier", _URL, url_hash(_URL)),
    )
    await _seed_download(temp_db, "d1")
    fetcher = FakeFetcher(png_bytes())
    context = await make_context(
        {"download_id": "d1", "ignore_ledger": True},
        capabilities=_caps(content_store, library_store, key=None),
    )

    await download(
        context,
        service=download_service,
        downloader=fetcher,
        import_file=real_import,
    )

    assert (await _view(download_service, "d1")).status != "skipped"
    assert len(fetcher.calls) == 1  # it really fetched


async def test_passing_the_ledger_does_not_pass_the_address_check(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The flag is a second opinion about one record, and nothing else gives way to it.

    Worth its own test because "download it anyway" is exactly the shape of a control that grows
    into a way past every other guard, and the address guard is the one that stops Sift being
    pointed at a private network.
    """

    async def refuse(_url: str, **_kwargs: object) -> None:
        raise UrlRejected("That link points to a private address.", reason="private_address")

    monkeypatch.setattr(jobs, "guard_url", refuse)
    await _seed_download(temp_db, "d1")
    fetcher = FakeFetcher(png_bytes())
    context = await make_context(
        {"download_id": "d1", "ignore_ledger": True},
        capabilities=_caps(content_store, library_store, key=None),
    )

    with pytest.raises(JobFailedPermanently):
        await download(
            context,
            service=download_service,
            downloader=fetcher,
            import_file=real_import,
        )

    view = await _view(download_service, "d1")
    assert view.status == "failed"
    assert view.error is not None and "private" in view.error
    assert fetcher.calls == []


async def test_a_download_that_adds_nothing_new_is_marked_a_duplicate(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    # First download lands the media; a second of a different URL fetches the very same bytes, which
    # the library already holds, so it settles as 'duplicate', not a fresh 'done'.
    await _seed_download(temp_db, "first", url="https://public.example/a")
    await _seed_download(temp_db, "second", url="https://public.example/b")
    caps = _caps(content_store, library_store, key=None)

    for download_id in ("first", "second"):
        context = await make_context({"download_id": download_id}, capabilities=caps)
        await download(
            context,
            service=download_service,
            downloader=FakeFetcher(png_bytes()),  # identical bytes both times
            import_file=real_import,
        )

    assert (await _view(download_service, "first")).status == "done"  # the media was new
    assert (await _view(download_service, "second")).status == "duplicate"  # already in the library


async def test_a_download_with_one_new_file_among_duplicates_is_not_a_duplicate(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    # A carousel where one slide is already in the library but another is new added something, so it
    # is a fresh 'done', not a 'duplicate': the whole-download flag must not be the last file's.
    red, green = png_bytes(b"\x00\xff\x00\x00"), png_bytes(b"\x00\x00\xff\x00")
    await _seed_download(temp_db, "first", url="https://public.example/a")
    await _seed_download(temp_db, "second", url="https://public.example/b")
    caps = _caps(content_store, library_store, key=None)

    context = await make_context({"download_id": "first"}, capabilities=caps)
    await download(  # lands the red slide, so it is in the library
        context,
        service=download_service,
        downloader=FakeFetcher(red),
        import_file=real_import,
    )

    context = await make_context({"download_id": "second"}, capabilities=caps)
    await download(  # a new green slide then the already-held red one
        context,
        service=download_service,
        downloader=_TwoFileFetcher(green, red),
        import_file=real_import,
    )

    assert (await _view(download_service, "second")).status == "done"  # the green slide was new


async def test_a_refused_address_fails_without_spawning_a_tool(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refuse(_url: str, **_kwargs: object) -> None:
        raise UrlRejected("That link points to a private address.", reason="private_address")

    monkeypatch.setattr(jobs, "guard_url", refuse)
    await _seed_download(temp_db, "d1")
    fetcher = FakeFetcher(png_bytes())
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    with pytest.raises(JobFailedPermanently):
        await download(
            context,
            service=download_service,
            downloader=fetcher,
            import_file=real_import,
        )

    view = await _view(download_service, "d1")
    assert view.status == "failed"
    assert view.error is not None and "private" in view.error
    assert fetcher.calls == []  # the guard ran before any tool


async def test_a_disguised_file_is_quarantined_and_no_asset_is_made(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    await _seed_download(temp_db, "d1")
    # A downloader that returns a file whose bytes are not the media its name claims.
    fetcher = FakeFetcher(b"this is not a video at all", filename="clip.mp4")
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=fetcher,
        import_file=real_import,
    )

    view = await _view(download_service, "d1")
    # Quarantined, not failed: the download itself worked and the gate refused what was inside. A
    # failure invites trying again, which fetches the same bytes and sets them aside again.
    assert view.status == "quarantined"
    assert view.error is not None and "quarantined" in view.error
    assert view.asset_id is None
    assets = await temp_db.fetch_one("SELECT COUNT(*) AS n FROM assets")
    assert assets is not None and assets["n"] == 0


async def test_a_truncated_download_retries_and_only_fails_when_exhausted(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    make_context: MakeContext,
) -> None:
    async def truncated(
        *, path: Path, origin: Origin, dest_folder_id: str | None, ctx: JobContext
    ) -> FakeOutcome:
        raise IngressRejected(Reason.NOT_DECODABLE)

    # First attempt of three: the handler re-raises for a retry and leaves the ledger untouched.
    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"},
        capabilities=_caps(content_store, library_store, key=None),
        max_attempts=3,
    )
    with pytest.raises(IngressRejected):
        await download(
            context,
            service=download_service,
            downloader=FakeFetcher(png_bytes()),
            import_file=truncated,
        )
    assert (await _view(download_service, "d1")).status == "running"

    # A single-attempt job: the last attempt records the plain failure before it re-raises.
    await _seed_download(temp_db, "d2", url="https://public.example/two")
    last = await make_context(
        {"download_id": "d2"},
        capabilities=_caps(content_store, library_store, key=None),
        max_attempts=1,
    )
    with pytest.raises(IngressRejected):
        await download(
            context=last,
            service=download_service,
            downloader=FakeFetcher(png_bytes()),
            import_file=truncated,
        )
    view = await _view(download_service, "d2")
    assert view.status == "failed"
    assert view.error is not None and "incomplete" in view.error


async def test_a_login_needed_with_nobody_logged_in_blocks(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
    master_key: bytes,
    admin_id: str,
) -> None:
    # A saved TikTok login exists, but the key that opens it is absent (nobody logged in).
    await download_service.save_connection(
        site="TikTok", cookie="c=1", master_key=master_key, by=admin_id
    )
    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    with pytest.raises(JobBlocked, match="password to unlock the saved cookies for TikTok"):
        await download(
            context,
            service=download_service,
            downloader=FakeFetcher(png_bytes()),
            import_file=real_import,
        )
    # A wait, not a failure: the ledger is not marked failed.
    assert (await _view(download_service, "d1")).status != "failed"


async def test_a_login_needed_with_the_admin_logged_in_uses_the_cookie(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
    master_key: bytes,
    admin_id: str,
) -> None:
    await download_service.save_connection(
        site="TikTok", cookie="c=1", master_key=master_key, by=admin_id
    )
    await _seed_download(temp_db, "d1")
    fetcher = FakeFetcher(png_bytes())
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=master_key)
    )

    await download(
        context,
        service=download_service,
        downloader=fetcher,
        import_file=real_import,
    )

    # The cookie was decrypted and handed to the tool.
    assert fetcher.calls[0][1] is not None
    assert (await _view(download_service, "d1")).status == "done"


async def test_a_corrupt_saved_login_is_a_failure_not_a_wait(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
    master_key: bytes,
    admin_id: str,
) -> None:
    await download_service.save_connection(
        site="TikTok", cookie="c=1", master_key=master_key, by=admin_id
    )
    await _seed_download(temp_db, "d1")
    # The key present is not the one the cookie was sealed with.
    from sift.slices.auth.crypto import generate_master_key

    context = await make_context(
        {"download_id": "d1"},
        capabilities=_caps(content_store, library_store, key=generate_master_key()),
    )

    with pytest.raises(JobFailedPermanently):
        await download(
            context,
            service=download_service,
            downloader=FakeFetcher(png_bytes()),
            import_file=real_import,
        )
    view = await _view(download_service, "d1")
    assert view.status == "failed"
    assert view.error is not None and "could not be read" in view.error


@pytest.mark.parametrize(
    ("error", "fragment"),
    [
        (LoginRequired("Instagram needs you to be logged in."), "logged in"),
        (NothingFound("nothing here"), "nothing"),
        # A resolver or redirect handed back a private/internal address the fetcher refused: as
        # permanent as an SSRF refusal on the pasted link itself.
        (
            UrlRejected("That link points to a private address.", reason="private_address"),
            "private",
        ),
    ],
)
async def test_a_fetch_that_fails_for_good_marks_the_ledger(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
    error: Exception,
    fragment: str,
) -> None:
    class Failing:
        def handles(self, url: str) -> bool:
            return True

        async def fetch(
            self,
            url: str,
            *,
            into: Path,
            cookies_file: Path | None = None,
            proxy: str | None = None,
            already_have: Callable[[str], Awaitable[bool]] | None = None,
            report: Report = nowhere,
        ) -> Fetched:
            raise error

    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    with pytest.raises(JobFailedPermanently):
        await download(
            context,
            service=download_service,
            downloader=Failing(),
            import_file=real_import,
        )
    assert (await _view(download_service, "d1")).status == "failed"


async def test_a_transient_fetch_error_retries(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    class Flaky:
        def handles(self, url: str) -> bool:
            return True

        async def fetch(
            self,
            url: str,
            *,
            into: Path,
            cookies_file: Path | None = None,
            proxy: str | None = None,
            already_have: Callable[[str], Awaitable[bool]] | None = None,
            report: Report = nowhere,
        ) -> Fetched:
            raise DownloadError("did not finish")

    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"},
        capabilities=_caps(content_store, library_store, key=None),
        max_attempts=3,
    )
    with pytest.raises(DownloadError):
        await download(
            context,
            service=download_service,
            downloader=Flaky(),
            import_file=real_import,
        )
    assert (
        await _view(download_service, "d1")
    ).status == "running"  # not failed; a retry is coming


async def test_a_site_that_stopped_responding_ends_the_job_on_its_first_attempt(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """A Site that went quiet for the whole timeout is not tried again by the queue: every attempt
    would sit through the same wait, so a dead Site would cost one wait per attempt."""

    class Silent:
        def handles(self, url: str) -> bool:
            return True

        async def fetch(
            self,
            url: str,
            *,
            into: Path,
            cookies_file: Path | None = None,
            proxy: str | None = None,
            already_have: Callable[[str], Awaitable[bool]] | None = None,
            report: Report = nowhere,
        ) -> Fetched:
            raise NoAnswer(NO_ANSWER_MESSAGE, code="no-answer")

    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"},
        capabilities=_caps(content_store, library_store, key=None),
        max_attempts=3,
    )
    with pytest.raises(JobFailedPermanently):
        await download(
            context,
            service=download_service,
            downloader=Silent(),
            import_file=real_import,
        )
    view = await _view(download_service, "d1")
    assert view.status == "failed"
    assert view.error == NO_ANSWER_MESSAGE


async def test_a_run_that_never_finished_is_recorded_with_what_the_site_said(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """On the last attempt a retryable failure is written with what was learned about it (a 521
    from a server that was down keeps its code), not as one sentence every such failure shares."""

    class Down:
        def handles(self, url: str) -> bool:
            return True

        async def fetch(
            self,
            url: str,
            *,
            into: Path,
            cookies_file: Path | None = None,
            proxy: str | None = None,
            already_have: Callable[[str], Awaitable[bool]] | None = None,
            report: Report = nowhere,
        ) -> Fetched:
            raise DownloadError(
                "Sunsetter answered 521: the Site behind the network is down; try again later.",
                code="http-521",
                tier=2,
            )

    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"},
        capabilities=_caps(content_store, library_store, key=None),
        max_attempts=1,
    )
    with pytest.raises(DownloadError):
        await download(
            context, service=download_service, downloader=Down(), import_file=real_import
        )
    view = await _view(download_service, "d1")
    assert view.status == "failed"
    assert (view.error_code, view.error_tier) == ("http-521", 2)
    assert view.error is not None and view.error.startswith("Sunsetter answered 521")


async def test_a_vanished_download_row_is_a_no_op(
    download_service: DownloadService,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    context = await make_context(
        {"download_id": "gone"}, capabilities=_caps(content_store, library_store, key=None)
    )
    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
    )  # returns quietly


async def test_a_download_with_no_username_still_lands(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    # A public host with no username in its path: a site is guessed, there is no username, and the
    # attribution step is skipped, but the media still lands.
    url = "https://public.example/clip"
    await _seed_download(temp_db, "d1", url=url)
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )
    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
    )
    view = await _view(download_service, "d1")
    assert view.status == "done"
    assert view.username is None


async def test_a_url_with_no_recognizable_site_still_lands(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    # A single-label host: no site is derived, so there is nothing to look a login up by.
    url = "http://internalhost/clip"
    await _seed_download(temp_db, "d1", url=url)
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )
    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
    )
    view = await _view(download_service, "d1")
    assert view.status == "done"
    assert view.site is None


async def test_a_connection_with_no_saved_secret_fetches_anonymously(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    from sift.kernel.access import MADE_BY_A_PERSON, ensure_site

    site_id = await ensure_site(temp_db, "TikTok", made=MADE_BY_A_PERSON)
    await temp_db.execute(
        "INSERT INTO site_connections (id, site_id, secret_id, status, updated_at) "
        "VALUES ('sc1', ?, NULL, 'saved', 0)",
        (site_id,),
    )
    await _seed_download(temp_db, "d1")
    fetcher = FakeFetcher(png_bytes())
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )
    await download(
        context,
        service=download_service,
        downloader=fetcher,
        import_file=real_import,
    )
    assert (await _view(download_service, "d1")).status == "done"
    assert fetcher.calls[0][1] is None  # no cookie was used


async def test_register_handlers_claims_the_download_type(
    download_service: DownloadService, real_import: RealImport
) -> None:
    from sift.kernel.jobs import registered_handlers
    from sift.slices.download.jobs import register_handlers

    async def never() -> bool:
        return False

    register_handlers(
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
        may_create_people=never,
    )
    assert "download" in registered_handlers()


async def test_a_payload_without_a_download_id_is_a_bug(
    download_service: DownloadService,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    context = await make_context({}, capabilities=_caps(content_store, library_store, key=None))
    with pytest.raises(ValueError, match="download_id"):
        await download(
            context,
            service=download_service,
            downloader=FakeFetcher(png_bytes()),
            import_file=real_import,
        )


# --- the wire from a download to a Person -----------------------------------------------------
#
# These test the call, not the service behind it: a call that left every argument at its default
# would make the whole feature do nothing while the service and the handler each read right.


async def test_a_download_from_a_creator_site_files_itself_under_the_person(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """The point of the whole thing: nobody types a name and the file is filed under whoever
    posted it."""

    async def yes() -> bool:
        return True

    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
        may_create_people=yes,
    )

    view = await _view(download_service, "d1")
    person = await temp_db.fetch_one("SELECT id, name FROM people")
    assert person is not None, "the username named nobody and nobody was made"
    assert person["name"] == "creator"
    on_it = await temp_db.fetch_one(
        "SELECT 1 FROM asset_people WHERE asset_id = ? AND person_id = ?",
        (view.asset_id, person["id"]),
    )
    assert on_it is not None


async def test_the_uploader_the_resolver_named_is_used_when_the_address_names_nobody(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """Resolving a link often answers "who posted this" for an address that does not say.

    YouTube's oEmbed names the channel behind a short link; RedGIFs' API names its uploader; an
    Instagram story link carries the username after the route word. All of it is computed and put
    on the resolved media; a seam that handed back only a list of paths would drop it, and the job
    would attribute from the ADDRESS alone and file those downloads under their site and nobody.
    """

    async def yes() -> bool:
        return True

    # A post link with no username in it, which is what Instagram's own Copy Link produces.
    await _seed_download(temp_db, "d1", url="https://www.instagram.com/p/Abc123/")
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(png_bytes(), username="whoever"),
        import_file=real_import,
        may_create_people=yes,
    )

    view = await _view(download_service, "d1")
    assert view.username == "whoever"
    # And HOW it was learned, which the row keeps because nothing can work it out afterwards.
    assert await _username_from(temp_db, "d1") == "resolver"
    person = await temp_db.fetch_one("SELECT id, name FROM people")
    assert person is not None, "the resolver named somebody and nobody was made"
    assert person["name"] == "whoever"
    assert (
        await temp_db.fetch_one(
            "SELECT 1 FROM asset_people WHERE asset_id = ? AND person_id = ?",
            (view.asset_id, person["id"]),
        )
        is not None
    )


async def test_the_address_wins_over_what_the_resolver_guessed(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """A username IN the address is believed over one the resolver worked out. Somebody pasting a
    link chose that link, and a best-effort guess does not get to overrule it."""
    await _seed_download(temp_db, "d1")  # .../@creator/video/1
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(png_bytes(), username="someone-else"),
        import_file=real_import,
    )

    assert (await _view(download_service, "d1")).username == "creator"
    assert await _username_from(temp_db, "d1") == "address"


async def test_a_download_creates_nobody_when_the_preference_says_not_to(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """Off, the site and the username are still recorded and nobody is invented."""

    async def no() -> bool:
        return False

    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
        may_create_people=no,
    )

    assert await temp_db.fetch_all("SELECT id FROM people") == []
    assert await temp_db.fetch_one("SELECT id FROM usernames WHERE name = 'creator'") is not None


async def test_a_failure_nothing_foresaw_goes_through_untouched_and_writes_nothing() -> None:
    """Written down as the download's failure it would read as something the tool reported. It
    goes through as it is, and the queue's own retry and error record take it; nothing here so
    much as reads the row (the stand-ins below would fail if anything did)."""
    unforeseen = RuntimeError("an error no branch names")

    with pytest.raises(RuntimeError) as raised:
        await attempt._fetch_failed(
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            new_id(),
            unforeseen,
            direct=True,
        )

    assert raised.value is unforeseen
