# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the download job tests share: stand-ins for the tool, the keys and the folders."""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from sift.kernel.content import ContentStore, LibraryStore
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import (
    SystemCapabilities,
)
from sift.kernel.jobs.workspaces import Workspaces
from sift.slices.download import jobs
from sift.slices.download.service import DownloadService, DownloadView
from sift.slices.download.site_options import SiteOptions
from sift.slices.download.sources import progress, url_hash
from sift.slices.download.sources.errors import (
    CookiesNeeded,
)
from sift.slices.download.sources.progress import Report, nowhere
from sift.slices.download.sources.resolved import Fetched, NameFacts
from sift.slices.download.tests.conftest import (
    FakeFetcher,
    png_bytes,
)

_URL = "https://www.tiktok.com/@creator/video/1"


async def _view(service: DownloadService, download_id: str) -> DownloadView:
    """The ledger row, asserted present: the tests here always seeded one."""
    view = await service.get(download_id)
    assert view is not None
    return view


class _Secrets:
    def __init__(self, key: bytes | None) -> None:
        self._key = key

    async def master_key(self) -> bytes | None:
        return self._key


@pytest.fixture(autouse=True)
def _no_real_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise the SSRF guard by default, so the happy paths do not touch DNS or the network.

    The guard has its own exhaustive tests. Here it is replaced with one that lets everything
    through, and re-replaced per test where a refusal is the thing under test.
    """

    async def allow(_url: str, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(jobs, "guard_url", allow)


def _caps(
    content: ContentStore,
    library: LibraryStore,
    *,
    key: bytes | None,
    workspaces: Workspaces | None = None,
) -> SystemCapabilities:
    # A real `Workspaces` whichever way it arrives: the handler asks the job for its directory, so
    # a capability set without one refuses every download here. Where a test cares what is in that
    # directory afterwards it passes the fixture below and keeps the path; where it does not, one of
    # its own is made and the operating system clears it up.
    return SystemCapabilities(
        content=content,
        library=library,
        secrets=_Secrets(key),
        workspaces=workspaces or Workspaces(Path(tempfile.mkdtemp(prefix="sift-workspaces-"))),
    )


async def _sites_on(db: Database, asset_id: str) -> set[str]:
    """Every site recorded against one file, by name. A file reaches a site through a username."""
    rows = await db.fetch_all(
        "SELECT p.name FROM asset_usernames aa"
        " JOIN usernames a ON a.id = aa.username_id"
        " JOIN sites p ON p.id = a.site_id"
        " WHERE aa.asset_id = ?",
        (asset_id,),
    )
    return {str(row["name"]) for row in rows}


async def _a_folder(db: Database) -> str:
    """A real folder to land in: the first library's own folder, made with the library if needed.

    Real rows, because the job asks the library where a download lands before it fetches, the way
    the import does after, and a made-up id is refused there exactly as it would be in use.
    """
    root = await db.fetch_one("SELECT id FROM library_roots ORDER BY id LIMIT 1")
    if root is None:
        root_id = new_id()
        home = Path(tempfile.mkdtemp(prefix="sift-landing-"))
        await db.execute(
            "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, 0)",
            (root_id, "landing", str(home)),
        )
    else:
        root_id = str(root["id"])
    folder = await db.fetch_one(
        "SELECT id FROM folders WHERE root_id = ? AND rel_path = ''", (root_id,)
    )
    if folder is not None:
        return str(folder["id"])
    folder_id = new_id()
    await db.execute(
        "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, NULL, '', ?)",
        (folder_id, root_id, "landing"),
    )
    return folder_id


async def _seed_download(
    db: Database, download_id: str, *, url: str = _URL, landing: bool = True
) -> None:
    """A queued row. `landing` gives it a real folder to land in, as the paste routes do; without
    one the row has nowhere to go, which is its own refusal."""
    folder = await _a_folder(db) if landing else None
    await db.execute(
        "INSERT INTO downloads (id, url, url_hash, dest_folder_id, state, created_at)"
        " VALUES (?, ?, ?, ?, 'queued', 0)",
        (download_id, url, url_hash(url), folder),
    )


class PausedMidFetch:
    """A downloader that writes half a file, waits to be told to stop, and never finishes.

    It stands in for a real tool part-way through a transfer: the bytes are on disk, the fetch is
    still in flight, and the only thing that ends it is the pause. The sleep is what gives the
    handler's watch loop a turn: without it this coroutine would run to its end before anything
    else on the loop could look at the job.
    """

    def __init__(self, half: bytes, *, filename: str = "clip.png") -> None:
        self.half = half
        self.filename = filename
        self.wrote: Path | None = None

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
        self.wrote = into / self.filename
        self.wrote.write_bytes(self.half)
        # Long enough that the pause is what ends it, never the clock.
        await asyncio.sleep(600)
        raise AssertionError("the pause should have stopped this fetch")


class PausedMidFetchReporting(PausedMidFetch):
    """The same stalled transfer, having told the screen how far it got and how fast."""

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
        # Two readings a moment apart, so the registry has a real rate to drop: a single reading
        # has none, and the assertion below would pass whether or not the pause held anything.
        report(progress.Progress(done_bytes=len(self.half) // 2, total_bytes=226))
        await asyncio.sleep(0.05)
        report(progress.Progress(done_bytes=len(self.half), total_bytes=226))
        return await super().fetch(
            url,
            into=into,
            cookies_file=cookies_file,
            proxy=proxy,
            already_have=already_have,
            report=report,
        )


class _RefusesWithoutCookies:
    """A downloader that answers the way a Site does when it only serves to a signed-in browser."""

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
        raise CookiesNeeded("YouTube will not serve this without cookies.")


class _TwoFileFetcher:
    """A downloader that drops two distinct files, so the per-file duplicate tally is exercised across
    more than one import."""

    def __init__(self, first: bytes, second: bytes) -> None:
        self._blobs = (first, second)

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
        paths = []
        for index, blob in enumerate(self._blobs):
            target = into / f"{index}.png"
            target.write_bytes(blob)
            paths.append(target)
        return Fetched(files=paths)


async def _landed_as(database: Database, download_id: str) -> str | None:
    row = await database.fetch_one("SELECT filename FROM downloads WHERE id = ?", (download_id,))
    assert row is not None
    return None if row["filename"] is None else str(row["filename"])


async def _creator_first(_key: str | None) -> SiteOptions:
    return SiteOptions(naming="{creator} - {name}")


class _NamingFetcher(FakeFetcher):
    """A fetcher that hands back what a file can be named from, keyed by the path it produced:
    the shape the real downloader answers with."""

    def __init__(self, produce: bytes, *, facts: NameFacts) -> None:
        super().__init__(produce)
        self.facts = facts

    async def fetch(self, url: str, **named: Any) -> Fetched:
        fetched = await super().fetch(url, **named)
        return Fetched(
            files=fetched.files,
            username=fetched.username,
            item_keys=fetched.item_keys,
            names={path: self.facts for path in fetched.files},
        )


async def _by_id(_key: str | None) -> SiteOptions:
    return SiteOptions(naming="{id}")


async def _username_from(database: Database, download_id: str) -> str | None:
    row = await database.fetch_one(
        "SELECT username_from FROM downloads WHERE id = ?", (download_id,)
    )
    assert row is not None
    return None if row["username_from"] is None else str(row["username_from"])


# --- Links whose contents change -----------------------------------------------------------------

_HIGHLIGHT = "https://www.instagram.com/stories/highlights/17900000000000000/"


class _GrowingSource:
    """A link that holds a set of items and gains more over time.

    It produces one file per item the caller has not already recorded, which is exactly what a real
    resolver plus fetcher does for a story tray: the address is looked at again in full every time,
    and only what is new is downloaded.
    """

    def __init__(self, *keys: str) -> None:
        self.keys = list(keys)
        #: What it was actually asked to fetch, per call. The whole assertion of these tests.
        self.fetched: list[list[str]] = []

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
        produced: list[Path] = []
        keys: dict[Path, str] = {}
        for index, key in enumerate(self.keys):
            if already_have is not None and await already_have(key):
                continue
            target = into / f"{key}.png"
            target.write_bytes(png_bytes(bytes([0, index + 1, 0, 0])))
            produced.append(target)
            keys[target] = key
        self.fetched.append(list(keys.values()))
        return Fetched(files=produced, item_keys=keys)


# --- The health of a saved login ------------------------------------------------------------------

_REDDIT = "https://www.reddit.com/r/pics/comments/abc/title/"


async def _health_of(service: DownloadService) -> str | None:
    (connection,) = await service.list_connections()
    return connection.status


_A_PMVHAVEN_URL = "https://pmvhaven.com/video/night-drive_0c0ffee000000000000beef0"


# --- the last hop: a finished download filed under what the link was dropped ON ------------------
#
# The routes' tests prove that the aim is written, that it is refused when wrong, and that the row
# is queued. These prove the hop from a finished download to a filed file, which is the one that
# decides whether dropping a link on a card does anything.


class _RecordingFiler:
    """The composition root's `FilingSeam`, as a recorder.

    A double here rather than the real dispatch, and deliberately: what this file is about is the
    JOB: whether the aim is read off the ledger, whether the files handed over are the ones that
    arrived, and whether a refusal can take a download down with it. Which table each kind writes is
    `test_composition.py`'s question and is answered there against the real services.
    """

    def __init__(self, *, raising: Exception | None = None, filed: int = 1) -> None:
        self.calls: list[dict[str, object]] = []
        self.raising = raising
        self.filed = filed

    async def __call__(
        self, *, kind: str, target_id: str, asset_ids: Sequence[str], for_user: str
    ) -> int:
        self.calls.append(
            {
                "kind": kind,
                "target_id": target_id,
                "asset_ids": list(asset_ids),
                "for_user": for_user,
            }
        )
        if self.raising is not None:
            raise self.raising
        return self.filed
