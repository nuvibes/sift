# SPDX-License-Identifier: AGPL-3.0-or-later
"""The download job, end to end: what a download is named, whose it is, and what it leaves behind.

The tool and the import pipeline are stood in for, as in `test_jobs.py`; the rows, the ledger and
the library are real.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path

import pytest
import structlog

from sift.kernel.content import ContentStore, LibraryStore
from sift.kernel.db import Database
from sift.kernel.ingress import IngressRejected, NoDestination, Origin, Reason
from sift.kernel.jobs import (
    JobContext,
    JobFailedPermanently,
)
from sift.kernel.photo_sets import MIN_PICTURES
from sift.kernel.tunnels import EgressRouter, ListenPorts, TunnelProcess, TunnelSpec
from sift.kernel.tunnels import client as tunnel_client
from sift.slices.download import attempt, jobs
from sift.slices.download.jobs import download
from sift.slices.download.service import DownloadService, PasteChoices
from sift.slices.download.site_options import SiteOptions
from sift.slices.download.sources import cookie_health, progress, url_hash
from sift.slices.download.sources.progress import Report, nowhere
from sift.slices.download.sources.resolved import Fetched, NameFacts
from sift.slices.download.tests.conftest import (
    FakeFetcher,
    FakeOutcome,
    MakeContext,
    RealImport,
    png_bytes,
)
from sift.slices.download.tests.jobs_support import (
    _A_PMVHAVEN_URL,
    _HIGHLIGHT,
    _REDDIT,
    _URL,
    _a_folder,
    _by_id,
    _caps,
    _creator_first,
    _GrowingSource,
    _health_of,
    _landed_as,
    _NamingFetcher,
    _no_real_guard,  # noqa: F401 (autouse)
    _RecordingFiler,
    _seed_download,
    _username_from,
    _view,
)


async def test_a_site_sift_does_not_know_is_still_recorded(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """Sift works out a site's name from any address it fetches, including ones it does not
    recognize, and records it without requiring a USERNAME as well, so a download from anywhere
    outside the built-in list is still filed under the site it came from.
    """
    await _seed_download(temp_db, "d1", url="https://example-host.test/video/some-title_01234567")
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
    )

    site = await temp_db.fetch_one("SELECT id, name FROM sites")
    assert site is not None and site["name"] == "Example-host"
    # Nobody is invented: there is no username to invent them from.
    assert await temp_db.fetch_all("SELECT id FROM people") == []
    # And no source is claimed for a username that does not exist.
    assert await _username_from(temp_db, "d1") is None

    # And the file is under the site, not merely alongside it: every question about a site reaches
    # its files through `asset_usernames`. So the assertion is the join the filter actually makes,
    # not the existence of the row.
    filed = await temp_db.fetch_all(
        "SELECT aa.asset_id FROM asset_usernames aa JOIN usernames ac ON ac.id = aa.username_id"
        " WHERE ac.site_id = ?",
        (site["id"],),
    )
    assert len(filed) == 1, "the download is not filed under the site it came from"
    # The username carrying it is empty, because there is none. It is bookkeeping, and with
    # People on every screen rather than usernames there is nowhere it can be seen.
    usernames = await temp_db.fetch_all("SELECT name FROM usernames")
    assert [row["name"] for row in usernames] == [""]


async def test_a_site_that_names_its_creator_only_on_the_page_still_files_the_person(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """A site whose links are a title and an id still files its downloads under whoever posted them.

    The name is on the page, in the schema.org metadata every video page publishes for search
    engines. Asserted through the real job so the wiring is what is under test: the
    reader is stubbed, but which downloads reach it, and what is done with the answer, are not.
    """

    async def yes() -> bool:
        return True

    async def reader(url: str, **_kwargs: object) -> str | None:
        assert url == "https://pmvhaven.com/video/some-title_01234567"
        return "quillmoss"

    await _seed_download(temp_db, "d1", url="https://pmvhaven.com/video/some-title_01234567")
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
        may_create_people=yes,
        read_creator=reader,
    )

    people = await temp_db.fetch_all("SELECT name FROM people")
    assert [row["name"] for row in people] == ["quillmoss"]
    filed = await temp_db.fetch_all("SELECT asset_id FROM asset_people")
    assert len(filed) == 1, "the person was created but the file was not put under them"
    # A name read off the page is the page's claim rather than the link's, and the row says so.
    assert await _username_from(temp_db, "d1") == "page"


async def test_a_creator_read_off_the_page_is_in_the_name(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """`{creator}` is filled from every fact the download learns, not only from its address.

    The page is read before the file is named, so a PMVHaven link (a title and an id) is named
    with its creator under a template that asks for the creator first.
    """

    async def reader(url: str, **_kwargs: object) -> str | None:
        return "Elina Sorrel"

    await _seed_download(temp_db, "d1", url="https://pmvhaven.com/video/some-title_01234567")
    await download(
        await make_context(
            {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
        ),
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
        read_creator=reader,
        read_site_options=_creator_first,
    )

    assert await _landed_as(temp_db, "d1") == "Elina Sorrel - clip.png"


async def test_a_download_s_id_reaches_its_name(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """The facts the fetch learned beyond the site and the creator (here the post's id) are the
    name's to use: read off the fetch's own record before the rename, like the media key."""
    await _seed_download(temp_db, "d1")
    await download(
        await make_context(
            {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
        ),
        service=download_service,
        downloader=_NamingFetcher(
            png_bytes(), facts=NameFacts(id="abc", title="Tide pools", posted=date(2026, 9, 1))
        ),
        import_file=real_import,
        read_site_options=_by_id,
    )

    assert await _landed_as(temp_db, "d1") == "abc.png"
    # And the row keeps what the file was named FROM, for the naming preview's real example.
    row = await temp_db.fetch_one(
        "SELECT post_id, title, posted, original FROM downloads WHERE id = 'd1'"
    )
    assert row is not None
    assert (row["post_id"], row["title"], row["posted"], row["original"]) == (
        "abc",
        "Tide pools",
        "2026-09-01",
        "clip",
    )


async def test_a_creator_the_resolver_named_is_in_the_name_and_the_page_is_not_read(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """The resolver's answer names the file too, and still costs no page request.

    A RedGIFs watch link carries no username; the site's own API names the uploader while the link
    is resolved. That answer reaches the name as well as the attribution.
    """
    asked: list[str] = []

    async def reader(url: str, **_kwargs: object) -> str | None:
        asked.append(url)
        return "somebody else"

    await _seed_download(temp_db, "d1", url="https://www.redgifs.com/watch/somegreenclip")
    await download(
        await make_context(
            {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
        ),
        service=download_service,
        downloader=FakeFetcher(png_bytes(), username="bryncalloway"),
        import_file=real_import,
        read_creator=reader,
        read_site_options=_creator_first,
    )

    assert await _landed_as(temp_db, "d1") == "bryncalloway - clip.png"
    assert asked == [], "the page was read for a creator the resolver had already named"
    assert await _username_from(temp_db, "d1") == "resolver"


async def test_the_page_is_not_read_when_the_address_already_named_somebody(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """One request per download that needs one, and none for the downloads that do not.

    TikTok puts the username in the path. Fetching the page anyway would add a request to every
    download from every site in the registry, for an answer already in hand.
    """
    asked: list[str] = []

    async def reader(url: str) -> str | None:
        asked.append(url)
        return "somebody else"

    await _seed_download(temp_db, "d1", url="https://www.tiktok.com/@creator/video/1")
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
        read_creator=reader,
    )

    assert asked == []
    username = await temp_db.fetch_one("SELECT name FROM usernames")
    assert username is not None and username["name"] == "creator"


async def test_every_file_of_an_album_is_attributed_and_not_only_the_last(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """One paste, many files, and the site is recorded against every one of them.

    An album is the ordinary case for several of the sites Sift downloads from. A file with no
    site, no username and nobody on it is, on every screen, indistinguishable from a file that was
    never downloaded at all.
    """
    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(
            png_bytes(), also=[png_bytes(b"\x00\x00\xff\x00"), png_bytes(b"\x00\x00\x00\xff")]
        ),
        import_file=real_import,
    )

    username = await temp_db.fetch_one("SELECT id FROM usernames WHERE name = 'creator'")
    assert username is not None
    linked = await temp_db.fetch_all(
        "SELECT asset_id FROM asset_usernames WHERE username_id = ?", (username["id"],)
    )
    assert len(linked) == 3, "the album's other files were left with no username on them"


async def test_an_animated_webp_that_could_not_be_read_says_what_to_do_about_it(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    make_context: MakeContext,
) -> None:
    """Not a corrupt file, and not a retry.

    The image hosts serve these in place of GIFs, so they arrive constantly, and the reason one
    will not open is usually that this installation has no WebP tools. Calling that
    "quarantined" tells somebody their perfectly ordinary GIF is broken; the message names
    the two things that actually work instead.
    """

    async def unreadable(
        *, path: Path, origin: Origin, dest_folder_id: str | None, ctx: JobContext
    ) -> FakeOutcome:
        raise IngressRejected(Reason.ANIMATED_WEBP_UNREADABLE)

    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"},
        capabilities=_caps(content_store, library_store, key=None),
        max_attempts=3,
    )

    # It returns rather than raising: there is nothing a second attempt would do differently.
    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=unreadable,
    )

    view = await _view(download_service, "d1")
    # Also quarantined rather than failed: the file arrived and is kept, and the message says what to
    # do with it. Retrying would fetch the identical GIF and read it no better.
    assert view.status == "quarantined"
    assert view.error is not None
    assert "animated WebP" in view.error
    assert "GIF" in view.error, "the message does not say what does work"


def test_how_much_room_is_left_is_read_off_the_disk(tmp_path: Path) -> None:
    """The one line the disk guard rests on, and the only one nothing else can stand in for.

    Every test of the guard replaces it (otherwise they would be measuring this machine's free
    space), so without this the reading itself is never run.
    """
    assert jobs._free_bytes(tmp_path) > 0


async def test_a_fetch_with_room_to_spare_is_checked_and_left_alone(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    make_context: MakeContext,
    real_import: RealImport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the disk guard: it looks, finds room, and goes round again.

    A guard that only ever fires is a guard nobody has proved lets anything through, and this one
    stands between every download and the disk, so "it cancels correctly" is half an answer.
    """
    looks = 0

    def plenty(_path: Path) -> int:
        nonlocal looks
        looks += 1
        return 1 << 40

    class _SlowFetcher(FakeFetcher):
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
            await asyncio.sleep(0.05)
            return await super().fetch(url, into=into, cookies_file=cookies_file)

    monkeypatch.setattr(attempt, "_DISK_CHECK_INTERVAL_SECONDS", 0.005)
    monkeypatch.setattr(attempt, "_free_bytes", plenty)
    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"},
        capabilities=_caps(content_store, library_store, key=None),
    )

    await download(
        context,
        service=download_service,
        downloader=_SlowFetcher(png_bytes()),
        import_file=real_import,
    )

    assert looks >= 1, "the disk was never looked at while the fetch was in flight"
    assert (await _view(download_service, "d1")).status == "done"


# --- the disk floor a fetch is guarded by -------------------------------------------------------


def test_the_stored_floor_is_read_in_gigabytes() -> None:
    """The setting is a number of gigabytes, and the check underneath counts bytes."""
    assert jobs.floor_bytes(3) == 3 * jobs.GIGABYTE
    assert jobs.floor_bytes("3") == 3 * jobs.GIGABYTE


def test_a_floor_that_is_not_a_number_falls_back_rather_than_becoming_no_guard() -> None:
    """A value from a restored row that slipped the decoder would otherwise resolve to zero, and a
    floor of zero is not a smaller guard: it is no guard at all, on the one check standing between
    a runaway fetch and a filesystem with no room left for the database."""
    for unusable in (None, "", "lots", object()):
        assert jobs.floor_bytes(unusable) == jobs._MIN_FREE_DISK_BYTES


def test_a_floor_of_zero_or_less_falls_back_too() -> None:
    """Zero parses perfectly well and is the dangerous answer, so it is refused separately from
    the values that cannot be read at all."""
    assert jobs.floor_bytes(0) == jobs._MIN_FREE_DISK_BYTES
    assert jobs.floor_bytes(-4) == jobs._MIN_FREE_DISK_BYTES


async def test_a_site_routed_to_a_tunnel_that_is_down_fails_and_never_goes_direct(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """The whole point of routing a site through a tunnel, asserted at the level it matters.

    Going out of the machine's own address on the one site somebody said not to is invisible after
    the fact, happens exactly when the tunnel is broken, and is the reason the setting exists. So
    the download stops before anything is fetched, and the message names the tunnel to turn on.
    """
    fetched = False

    class _WouldLeak:
        def handles(self, _url: str) -> bool:
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
            nonlocal fetched
            fetched = True
            return Fetched(files=[])

    async def routed_to_a_tunnel() -> str:
        return "t1"

    tunnel = TunnelProcess(TunnelSpec(id="t1", name="Sweden"))  # never started
    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    with pytest.raises(JobFailedPermanently):
        await download(
            context,
            service=download_service,
            downloader=_WouldLeak(),
            import_file=real_import,
            router=EgressRouter({"t1": tunnel}, read_default=routed_to_a_tunnel),
        )

    assert not fetched, "the download went out anyway, on a site set to use a tunnel that is down"
    row = await download_service.get("d1")
    assert row is not None and row.status == "failed"
    assert row.error is not None and "Sweden" in row.error


async def test_a_download_whose_tunnel_program_was_removed_fails_saying_so_on_its_row(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An antivirus can take the tunnel program out of the pack, and the tunnel cannot start. The
    row does not say the tunnel "no longer exists"; it says what the tunnel rows on Settings say."""
    shipped = b"the tunnel program as the pack shipped it"
    pack = tmp_path / "vendor" / "bin"
    pack.mkdir(parents=True)
    monkeypatch.setattr(
        tunnel_client,
        "CHECK",
        tunnel_client.ClientCheck(lambda: pack, digest=hashlib.sha256(shipped).hexdigest()),
    )

    async def routed_to_a_tunnel() -> str:
        return "t1"

    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    with pytest.raises(JobFailedPermanently):
        await download(
            context,
            service=download_service,
            downloader=FakeFetcher(png_bytes()),
            import_file=real_import,
            router=EgressRouter({}, read_default=routed_to_a_tunnel),
        )

    row = await download_service.get("d1")
    assert row is not None and row.status == "failed"
    assert row.error == tunnel_client.CLIENT_REMOVED


async def test_a_tunnelled_download_records_the_tunnel_and_its_server_and_logs_the_route(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Which way a download went is a FACT on its row and in its log, not a guess made later.

    A failure logged with no route on it would leave whether it came back through the tunnel or
    off the machine's own address to be guessed. Asserted here from inside the fetch: the
    downloader is handed the tunnel's proxy, the row already names the tunnel and the server it is
    on, and anything the tool logs while it runs carries the route.
    """
    seen: dict[str, object] = {}

    class _Tool:
        def handles(self, _url: str) -> bool:
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
            seen["proxy"] = proxy
            seen["logged_with"] = dict(structlog.contextvars.get_contextvars())
            row = await download_service.get("d1")
            seen["row"] = (row.via, row.via_address) if row is not None else None
            return Fetched(files=[])

    async def routed_to_a_tunnel() -> str:
        return "t1"

    async def metrics() -> tuple[int | None, str | None]:
        return 1, "192.0.2.44"

    tunnel = TunnelProcess(TunnelSpec(id="t1", name="Sweden"))
    monkeypatch.setattr(tunnel, "running", lambda: True)
    monkeypatch.setattr(tunnel, "_ports", ListenPorts(proxy=45000, status=45001))
    monkeypatch.setattr(tunnel, "_read_metrics", metrics)
    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    # The tool here fetches nothing, and a download that fetched nothing ends failed: the
    # row says so, and so does its job. What this test is about is recorded before that.
    with pytest.raises(JobFailedPermanently):
        await download(
            context,
            service=download_service,
            downloader=_Tool(),
            import_file=real_import,
            router=EgressRouter({"t1": tunnel}, read_default=routed_to_a_tunnel),
        )

    assert seen["proxy"] == "http://127.0.0.1:45000"
    assert seen["row"] == ("Sweden", "192.0.2.44")
    assert seen["logged_with"] == {"download_id": "d1", "route": "t1", "via": "Sweden"}
    # And only while the route is held: a line logged after it names nothing it no longer holds.
    assert "route" not in structlog.contextvars.get_contextvars()
    row = await download_service.get("d1")
    assert row is not None and (row.via, row.via_address) == ("Sweden", "192.0.2.44")


async def test_the_folder_a_job_resolved_is_on_its_row_before_the_fetch_starts(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """A landed row's "Saved to" must not follow whatever the setting names now, or changing the
    setting would move every finished download's folder on screen. The job records the folder
    it resolved (here the Site's own, the rule's second answer), and it is already there while
    the fetch runs, so a running row names the folder it is writing into."""
    from sift.slices.download.site_options import SiteOptions

    seen: dict[str, object] = {}

    the_sites_folder = await _a_folder(temp_db)

    async def site_folder(_key: str | None) -> SiteOptions:
        return SiteOptions(dest_folder_id=the_sites_folder)

    class _Tool:
        def handles(self, _url: str) -> bool:
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
            row = await download_service.get("d1")
            seen["folder"] = row.folder_id if row is not None else None
            return Fetched(files=[])

    await _seed_download(temp_db, "d1", landing=False)
    # The tool here fetches nothing, and a download that fetched nothing ends failed: the
    # row says so, and so does its job. What this test is about is recorded before that.
    with pytest.raises(JobFailedPermanently):
        await download(
            await make_context(
                {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
            ),
            service=download_service,
            downloader=_Tool(),
            import_file=real_import,
            read_site_options=site_folder,
        )

    assert seen["folder"] == the_sites_folder
    row = await download_service.get("d1")
    assert row is not None and row.folder_id == the_sites_folder


async def test_a_highlight_pasted_again_fetches_only_what_was_added(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """The whole point of the per-item ledger. A highlight keeps one address for years while its
    username adds to it, so answering "have I fetched this address" loses what came after."""
    source = _GrowingSource("story-a", "story-b")

    await _seed_download(temp_db, "d1", url=_HIGHLIGHT)
    await download(
        await make_context(
            {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
        ),
        service=download_service,
        downloader=source,
        import_file=real_import,
    )
    assert source.fetched == [["story-a", "story-b"]]
    assert (await _view(download_service, "d1")).status == "done"

    # The username posts one more into it. The same link is pasted again.
    source.keys.append("story-c")
    await _seed_download(temp_db, "d2", url=_HIGHLIGHT)
    await download(
        await make_context(
            {"download_id": "d2"}, capabilities=_caps(content_store, library_store, key=None)
        ),
        service=download_service,
        downloader=source,
        import_file=real_import,
    )

    assert source.fetched[1] == ["story-c"], "the whole highlight was fetched again"
    second = await _view(download_service, "d2")
    # Never skipped, and never "already in your library": both are sentences about the ADDRESS, and
    # the address is exactly the thing that has not changed.
    assert second.status == "done"


async def test_a_changing_link_with_nothing_new_finishes_rather_than_reading_as_a_duplicate(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    source = _GrowingSource("story-a")
    for download_id in ("d1", "d2"):
        await _seed_download(temp_db, download_id, url=_HIGHLIGHT)
        await download(
            await make_context(
                {"download_id": download_id},
                capabilities=_caps(content_store, library_store, key=None),
            ),
            service=download_service,
            downloader=source,
            import_file=real_import,
        )

    assert source.fetched == [["story-a"], []]
    second = await _view(download_service, "d2")
    assert second.status == "done"
    assert second.status not in {"duplicate", "skipped", "failed"}


async def test_an_item_is_recorded_only_once_its_file_has_landed(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    make_context: MakeContext,
) -> None:
    """A download the gate refuses records nothing, so pasting the link again fetches it again.

    Recording at resolve time would mark the item as fetched, and the one file that never arrived
    would be the one item skipped forever after.
    """

    async def refuse(**_kwargs: object) -> FakeOutcome:
        raise IngressRejected(Reason.SIGNATURE_NOT_ALLOWED)

    source = _GrowingSource("story-a")
    await _seed_download(temp_db, "d1", url=_HIGHLIGHT)
    await download(
        await make_context(
            {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
        ),
        service=download_service,
        downloader=source,
        import_file=refuse,
    )

    assert await download_service.item_already_done(url_hash(_HIGHLIGHT), "story-a") is False


async def test_an_item_whose_file_was_deleted_is_fetched_again(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """The same rule the ledger already keeps, one level down: a record that no longer points at a
    file is not a record of holding anything, so pasting the link gets it back."""
    source = _GrowingSource("story-a")
    await _seed_download(temp_db, "d1", url=_HIGHLIGHT)
    await download(
        await make_context(
            {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
        ),
        service=download_service,
        downloader=source,
        import_file=real_import,
    )
    assert await download_service.item_already_done(url_hash(_HIGHLIGHT), "story-a") is True

    await temp_db.execute("DELETE FROM assets")  # the file is removed from the library
    assert await download_service.item_already_done(url_hash(_HIGHLIGHT), "story-a") is False

    await _seed_download(temp_db, "d2", url=_HIGHLIGHT)
    await download(
        await make_context(
            {"download_id": "d2"}, capabilities=_caps(content_store, library_store, key=None)
        ),
        service=download_service,
        downloader=source,
        import_file=real_import,
    )
    assert source.fetched[1] == ["story-a"]


async def test_a_login_that_has_gone_dead_is_written_down_where_a_screen_can_see_it(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
    master_key: bytes,
    admin_id: str,
) -> None:
    """Without this an expired or blocked login reads as perfectly normal, and the only symptom is
    downloads quietly coming back with less than they should."""
    await download_service.save_connection(
        site="Reddit", cookie="# Netscape HTTP Cookie File\n", master_key=master_key, by=admin_id
    )
    assert await _health_of(download_service) == "saved"

    for _ in range(3):  # the run of failures that means the cookie is dead, not one bad post
        cookie_health.record_auth_failure("www.reddit.com")

    await _seed_download(temp_db, "d1", url=_REDDIT)
    await download(
        await make_context(
            {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=master_key)
        ),
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
    )

    assert await _health_of(download_service) == "needs_cookies"


async def test_a_login_that_works_again_after_a_restart_stops_being_flagged(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
    master_key: bytes,
    admin_id: str,
) -> None:
    """The flag is a record of what was last known, not a verdict that sticks.

    A rate limit counts toward the switch exactly as a rejection does, because from here the two
    look the same, so a busy hour can flag a login that was never broken. A restart gives it one
    more go, and a working cookie clears the flag on its own rather than waiting to be replaced.
    """
    await download_service.save_connection(
        site="Reddit", cookie="# Netscape HTTP Cookie File\n", master_key=master_key, by=admin_id
    )
    for _ in range(3):
        cookie_health.record_auth_failure("www.reddit.com")
    await _seed_download(temp_db, "d1", url=_REDDIT)
    await download(
        await make_context(
            {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=master_key)
        ),
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
    )
    assert await _health_of(download_service) == "needs_cookies"

    cookie_health.reset()  # what a restart does: the switch is process memory, the record is not
    # A different post, because the same one would be recognized as already fetched and skipped
    # before the login was ever reached, and a skip teaches nothing about a login.
    await _seed_download(temp_db, "d2", url="https://www.reddit.com/r/pics/comments/def/other/")
    await download(
        await make_context(
            {"download_id": "d2"}, capabilities=_caps(content_store, library_store, key=master_key)
        ),
        service=download_service,
        downloader=FakeFetcher(png_bytes(b"\x00\x00\xff\x00")),
        import_file=real_import,
    )

    assert await _health_of(download_service) == "saved"


async def test_saving_a_fresh_login_lets_the_site_be_tried_again(
    download_service: DownloadService,
    master_key: bytes,
    admin_id: str,
) -> None:
    """Otherwise a replaced login would never be sent, and the screen would go on asking for the
    very thing that had just been given to it."""
    for _ in range(3):
        cookie_health.record_auth_failure("www.reddit.com")
    assert cookie_health.should_use_cookies("www.reddit.com") is False

    await download_service.save_connection(
        site="Reddit", cookie="# Netscape HTTP Cookie File\n", master_key=master_key, by=admin_id
    )

    assert cookie_health.should_use_cookies("www.reddit.com") is True
    assert await _health_of(download_service) == "saved"


async def test_nothing_is_written_about_a_login_sift_has_no_verdict_on(
    download_service: DownloadService,
) -> None:
    """A site with no record, or a download that named no site at all. Writing "fine" in either
    case would make the connections screen claim to have checked something it never looked at."""
    await jobs._record_login_health(download_service, _URL, None)
    await jobs._record_login_health(download_service, "https://example-host.test/x", "Example")
    assert await download_service.list_connections() == []


async def test_a_note_about_a_login_never_displaces_the_failure_it_was_written_beside(
    download_service: DownloadService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This runs on the way out of a fetch, including a failed one. A write that cannot happen must
    not become the error the download reports: the real one was already on its way."""

    async def refuses(_site: str, _status: str) -> None:
        raise RuntimeError("the database is closed")

    monkeypatch.setattr(download_service, "record_login_health", refuses)

    await jobs._record_login_health(download_service, "https://www.reddit.com/r/pics/", "Reddit")


async def test_a_finished_download_stops_being_counted_as_running(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """The line above the queue reads what is in flight. Without this it grows by one entry per
    download for as long as the process lives, and finished work is counted as still going."""
    await _seed_download(temp_db, "d1")
    watching = progress.Registry()
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
        watching=watching,
    )

    assert (await _view(download_service, "d1")).status == "done"
    assert watching.all() == {}


async def test_a_sites_picture_is_asked_for_once_the_download_has_worked(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """After the work, never before it: a picture is a nicety and the file is the work, so nothing
    about fetching one is allowed to delay or fail a download."""
    await _seed_download(temp_db, "d1")
    asked: list[str] = []

    async def keep_art(url: str, _proxy: str | None, _username: str | None = None) -> None:
        asked.append(url)

    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
        keep_art=keep_art,
    )

    assert (await _view(download_service, "d1")).status == "done"
    assert asked == [_URL]


async def test_a_downloaded_gallery_lands_as_files_in_its_folder_and_no_set_is_made(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """A Photo Set is somebody's own grouping, or a folder's or an archive's; a link that brought
    back a run of pictures is files in the folder it landed in, however many there are."""
    await _seed_download(temp_db, "d1")
    pictures = [png_bytes(bytes([0, index, 0, 0])) for index in range(MIN_PICTURES + 1)]
    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(pictures[0], also=pictures[1:]),
        import_file=real_import,
    )

    assert (await _view(download_service, "d1")).status == "done"
    landed = await temp_db.fetch_one("SELECT COUNT(*) AS n FROM assets WHERE media_type = 'image'")
    assert landed is not None and int(landed["n"]) == len(pictures)
    assert await temp_db.fetch_all("SELECT id FROM photo_sets") == []
    job = await download_service.job_input("d1")
    assert job is not None
    assert job.choices == PasteChoices()


async def test_a_naming_template_does_not_stop_the_per_item_ledger_being_written(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """Two features that look unrelated, and one silently undoing the other.

    The fetcher says which piece of media each file is, keyed by the path it produced. Renaming the
    file before reading that key looks right and quietly returns nothing, so nothing is recorded,
    and the per-item ledger that stops a re-pasted highlight fetching its whole contents again is
    simply not written. It has no visible symptom except downloads that keep coming back, and it
    only appears once somebody sets a naming template, which no other test does.
    """
    from sift.slices.download.site_options import SiteOptions

    async def named(_key: str | None) -> SiteOptions:
        return SiteOptions(naming="{site} - {name}")

    source = _GrowingSource("story-a", "story-b")

    await _seed_download(temp_db, "d1", url=_HIGHLIGHT)
    await download(
        await make_context(
            {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
        ),
        service=download_service,
        downloader=source,
        import_file=real_import,
        read_site_options=named,
    )
    assert source.fetched == [["story-a", "story-b"]]

    source.keys.append("story-c")
    await _seed_download(temp_db, "d2", url=_HIGHLIGHT)
    await download(
        await make_context(
            {"download_id": "d2"}, capabilities=_caps(content_store, library_store, key=None)
        ),
        service=download_service,
        downloader=source,
        import_file=real_import,
        read_site_options=named,
    )

    assert source.fetched[1] == ["story-c"], "the whole highlight was fetched again"


async def test_a_library_that_is_not_there_is_reported_in_words_and_not_retried(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    make_context: MakeContext,
) -> None:
    """The bytes are fine; the place they were going has gone.

    Unhandled, that comes out of `mkdir(parents=True)` deep inside the copy as a raw errno about
    the filesystem underneath the library ("OSError: [Errno 30] Read-only file system") and
    escapes the handler, leaving the ledger row on "downloading" with no reason attached.

    So: the pipeline's own sentence, recorded on the row, and no second attempt: a missing disk
    stays missing until somebody attends to it, and three tries would write the same sentence three
    times.
    """

    async def nowhere(
        *, path: Path, origin: Origin, dest_folder_id: str | None, ctx: JobContext
    ) -> FakeOutcome:
        raise NoDestination(
            'Sift cannot see the folder "Sift Downloads" any more, so there is nowhere to put '
            "this. Check the drive it is on is still attached, then try again."
        )

    await _seed_download(temp_db, "d1")
    context = await make_context(
        {"download_id": "d1"},
        capabilities=_caps(content_store, library_store, key=None),
        max_attempts=3,
    )

    # Returns rather than raising: a second attempt would find the same folder still missing.
    with pytest.raises(JobFailedPermanently):
        await download(
            context,
            service=download_service,
            downloader=FakeFetcher(png_bytes()),
            import_file=nowhere,
        )

    view = await _view(download_service, "d1")
    assert view.status == "failed", "the row was left unsettled"
    assert view.error is not None
    assert "Sift Downloads" in view.error, "the message does not name the folder that was chosen"
    assert "attached" in view.error, "the message does not say what to check"
    assert "Errno" not in view.error and "OSError" not in view.error


async def test_the_track_a_page_names_is_seeded_onto_what_landed(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """One page read for the whole job rather than one per file: the track is a fact about the
    PAGE, and a gallery from one page is one video's worth of it.

    Seeded rather than set, so a value somebody has typed onto the record is never argued with by a
    re-drop of the same link months later.

    And recorded as an act, naming the Site whose page said it: History's "Sift named the song X
    from PMVHaven's page" is read off this event, and its link is the Site's row.
    """
    await _seed_download(temp_db, "d1", url=_A_PMVHAVEN_URL)
    # Spelled as a library's own row may be, which is not how the address's Site is spelled: the
    # match is case-blind, and the record keeps what the Site was called HERE.
    await temp_db.execute("INSERT INTO sites (id, name) VALUES (?, ?)", ("s-pmv", "Pmvhaven"))
    asked: list[str] = []

    async def read_music(url: str, *, proxy: str | None = None) -> str | None:
        asked.append(url)
        return "Marlo Venn - Night drive"

    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
        read_music=read_music,
    )

    view = await _view(download_service, "d1")
    assert view.status == "done"
    assert asked == [_A_PMVHAVEN_URL]
    assert view.asset_id is not None
    landed = await content_store.get(view.asset_id)
    assert landed is not None
    assert landed.music == "Marlo Venn - Night drive"
    named = await temp_db.fetch_all(
        "SELECT actor_kind, actor_id, object_kind, object_id, object_name, payload"
        " FROM workbench_decisions WHERE verb = 'song_named'"
    )
    assert [tuple(row)[:5] for row in named] == [("sift", "download", "site", "s-pmv", "Pmvhaven")]
    # The receipt names the song the file joined as well as its words, so taking it back takes
    # the file off that song and no other.
    joined = await temp_db.fetch_one(
        "SELECT song_id FROM song_files WHERE asset_id = ?", (view.asset_id,)
    )
    assert joined is not None
    assert json.loads(str(named[0]["payload"])) == {
        "song": "Marlo Venn - Night drive",
        "song_id": str(joined["song_id"]),
    }


async def test_a_page_that_names_no_track_writes_nothing(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    await _seed_download(temp_db, "d1", url=_A_PMVHAVEN_URL)

    async def read_music(url: str, *, proxy: str | None = None) -> str | None:
        return None

    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
        read_music=read_music,
    )

    view = await _view(download_service, "d1")
    assert view.asset_id is not None
    landed = await content_store.get(view.asset_id)
    assert landed is not None
    assert landed.music is None
    assert (
        await temp_db.fetch_all("SELECT id FROM workbench_decisions WHERE verb = 'song_named'")
        == []
    ), "a page that named nothing was recorded as naming a song"


async def test_a_site_that_does_not_record_a_track_is_never_asked(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """Gated on the site SAYING it records music rather than attempted hopefully. The reader has to
    know a site's own page shape, so asking anywhere else can only cost a request and return
    None."""
    await _seed_download(temp_db, "d1")
    asked: list[str] = []

    async def read_music(url: str, *, proxy: str | None = None) -> str | None:
        asked.append(url)
        return "Something"

    context = await make_context(
        {"download_id": "d1"}, capabilities=_caps(content_store, library_store, key=None)
    )

    await download(
        context,
        service=download_service,
        downloader=FakeFetcher(png_bytes()),
        import_file=real_import,
        read_music=read_music,
    )

    assert asked == []


async def test_what_arrived_is_filed_under_what_the_link_was_dropped_on(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """The aim is read off the LEDGER rather than carried in the payload, and that is the design: a
    browser holding it would lose it to a reload, and a tab closed mid-download is the ordinary case
    rather than the unlucky one. So the seam is handed all three parts of the row: the kind, the
    thing, and the user who dropped it."""
    await _seed_download(temp_db, "d1")
    await temp_db.execute(
        "UPDATE downloads SET aimed_kind = 'collection', aimed_id = ?, aimed_by = ? WHERE id = ?",
        ("shelf-1", "u1", "d1"),
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
    assert filer.calls == [
        {
            "kind": "collection",
            "target_id": "shelf-1",
            "asset_ids": [view.asset_id],
            "for_user": "u1",
        }
    ]


async def test_a_download_nobody_aimed_is_filed_nowhere(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """Most downloads are typed into the box rather than dropped on anything, and calling the seam
    with nothing to aim at would be a write nobody asked for."""
    await _seed_download(temp_db, "d1")
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

    assert filer.calls == []


async def test_a_target_deleted_while_the_download_ran_does_not_fail_the_download(
    download_service: DownloadService,
    temp_db: Database,
    content_store: ContentStore,
    library_store: LibraryStore,
    real_import: RealImport,
    make_context: MakeContext,
) -> None:
    """The thing it was aimed at can have been deleted in the minutes since. A file that arrived and
    could not be filed is a file that ARRIVED: the alternative is losing a fetch over a note, and a
    red row on the dashboard for a download that worked."""
    await _seed_download(temp_db, "d1")
    await temp_db.execute(
        "UPDATE downloads SET aimed_kind = 'tag', aimed_id = ?, aimed_by = ? WHERE id = ?",
        ("gone", "u1", "d1"),
    )
    filer = _RecordingFiler(raising=RuntimeError("that tag went"))
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
    assert view.status == "done", "a note that could not be written failed the whole fetch"
    assert view.asset_id is not None
    assert len(filer.calls) == 1


async def test_half_an_aim_is_read_as_no_aim_at_all(
    download_service: DownloadService, temp_db: Database
) -> None:
    """All three or nothing. A row carrying a kind and no id, or an id and no user, is a row
    nothing can act on, and "not aimed" is the reading that cannot go wrong, because the other one
    files a file under whatever an empty string resolves to."""
    await _seed_download(temp_db, "d1")
    assert await download_service.aim_of("d1") is None

    for kind, target, user_id in (
        ("collection", None, "u1"),
        ("collection", "shelf-1", None),
        (None, "shelf-1", "u1"),
    ):
        await temp_db.execute(
            "UPDATE downloads SET aimed_kind = ?, aimed_id = ?, aimed_by = ? WHERE id = 'd1'",
            (kind, target, user_id),
        )
        assert await download_service.aim_of("d1") is None, (kind, target, user_id)

    await temp_db.execute(
        "UPDATE downloads SET aimed_kind = 'collection', aimed_id = 'shelf-1', aimed_by = 'u1'"
        " WHERE id = 'd1'"
    )
    assert await download_service.aim_of("d1") == ("collection", "shelf-1", "u1")


def test_every_failure_the_handler_writes_down_is_one_that_ends_its_job() -> None:
    """The row and the job say the same thing about a download that failed for good.

    A handler that wrote the failure on the row and then RETURNED would finish its job as done, so
    the Downloads screen would say failed while Activity counted a success. The writes are held to the three
    places that end the job as well: `_give_up` raises the kernel's permanent failure, the classified
    refusal's caller raises it with the sentence `_record_failure` wrote, and `_retry_or_give_up`
    writes only on the last attempt, whose caller re-raises.
    """
    import ast
    import inspect

    from sift.slices.download import attempt, endings, landing, settling

    # The job is split across these modules; every one of them is read.
    tree = ast.parse(
        "\n".join(inspect.getsource(one) for one in (jobs, attempt, landing, settling, endings))
    )
    writers = {
        function.name
        for function in ast.walk(tree)
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "mark_failed"
    }
    assert writers == {"_give_up", "_record_failure", "_retry_or_give_up"}


async def test_a_retried_album_lets_in_only_what_its_earlier_attempt_did_not(
    tmp_path: Path,
) -> None:
    """The tool hands back every file in its folder on the retry, the ones already in the library
    too: those are not copied into the library a second time."""
    from types import SimpleNamespace

    from sift.slices.download import landing

    staging = tmp_path / "workspace" / "media"
    staging.mkdir(parents=True)
    let_in: list[str] = []
    broken = {"two.png"}

    async def import_file(*, path: Path, **_kwargs: object) -> SimpleNamespace:
        if path.name in broken:
            raise IngressRejected(Reason.NOT_DECODABLE)
        let_in.append(path.name)
        return SimpleNamespace(asset_id=f"asset-{path.name}", was_duplicate=False)

    class Service:
        async def record_item(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def mark_failed(self, *_args: object, **_kwargs: object) -> None:
            return None

    async def attempt_once(number: int) -> landing.Landed | None:
        files = sorted(path for path in staging.iterdir() if path.is_file())
        context = SimpleNamespace(attempt=number, job=SimpleNamespace(max_attempts=3))
        return await landing.land(
            context,  # type: ignore[arg-type]
            Service(),  # type: ignore[arg-type]
            import_file,
            Fetched(files=files),
            download_id="d1",
            staging=staging,
            options=SiteOptions(naming=""),
            site=None,
            username=None,
            dest_folder_id=None,
            hash_value="h",
        )

    (staging / "one.png").write_bytes(b"1")
    (staging / "two.png").write_bytes(b"2")
    with pytest.raises(IngressRejected):
        await attempt_once(1)
    broken.clear()
    landed = await attempt_once(2)

    assert let_in == ["one.png", "two.png"], "the first file was let in twice"
    assert landed is not None and landed.arrived == ["asset-one.png", "asset-two.png"]
