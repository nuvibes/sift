# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixtures for the download slice.

The seam this slice consumes (the shared import pipeline) belongs to another slice and is not
here to import, so a fake stands in its place. The fake is not a stub that returns a canned value:
it runs the real ingress gate and the real content store, because that is exactly the boundary the
integration tests are about. What it does not do is fetch anything from the internet; the downloader
seam is faked too, and it writes a real file into the workspace the job hands it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Imported for its side effect: registering the record's own tables, so a database built for this
# slice has them. A download that lands or gives up writes an event through `kernel/ledger.py` in
# the same transaction as the row, and the door has nowhere to write without them. The application
# always has them (every install registers every slice), and this is what makes a test database
# the same shape. The same line every other slice that records an event carries.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.config import Settings, get_settings
from sift.kernel.content import ContentStore
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ingress import IngressResult, Origin, verify_ingress
from sift.kernel.jobs import JobContext, JobQueue, SystemCapabilities, worker_pool
from sift.kernel.jobs.workspaces import Workspaces
from sift.main import create_app
from sift.slices.auth.crypto import generate_master_key
from sift.slices.download.secrets import SecretStore
from sift.slices.download.service import DownloadService
from sift.slices.download.sources import cookie_health
from sift.slices.download.sources.progress import Report, nowhere
from sift.slices.download.sources.resolved import Fetched
from sift.testing.fixtures import LibraryRoot, png_bytes


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as started:
        # Boot wires the real download handler, which reaches the network. These endpoint tests
        # exercise the API (submit, list, cancel, cookies), not the fetch itself (test_jobs covers
        # that with fakes), so it is swapped for a no-op. The autouse registry save/restore in the
        # shared fixtures puts the real handler back after the test.
        async def noop(_ctx: object) -> None:
            return None

        worker_pool._HANDLERS["download"] = noop
        yield started


class MakeContext(Protocol):
    """Builds a claimed job context for a payload, the way the fixture below does."""

    async def __call__(
        self,
        payload: dict[str, object],
        *,
        capabilities: SystemCapabilities,
        max_attempts: int = 3,
    ) -> JobContext: ...


#: Re-exported so this slice's tests go on importing it from their own conftest. It lives in the
#: shared fixtures because more than one slice needs a file that is genuinely not the last one.
__all__ = ["png_bytes"]


@dataclass(frozen=True, slots=True)
class FakeOutcome:
    """The shape the import pipeline reports back, named so the job can read it structurally."""

    asset_id: str
    was_duplicate: bool


class FakeFetcher:
    """A downloader that writes a caller-chosen file instead of reaching the internet.

    `produce` is the bytes it will drop into the workspace; `filename` its name. Setting
    `produce` to None makes it produce nothing, which is how the "a page with no media" path is
    exercised without a real tool.

    `username` is what resolving the address taught, which the real downloader hands back
    beside the files: the uploader a site's own API named for a link that does not carry one.
    """

    def __init__(
        self,
        produce: bytes | None = None,
        *,
        filename: str = "clip.png",
        also: Sequence[bytes] = (),
        username: str | None = None,
    ) -> None:
        self.produce = produce
        self.filename = filename
        #: Further files from the same address, which is what an album is. One paste, many files.
        self.also = list(also)
        self.username = username
        self.calls: list[tuple[str, Path | None]] = []

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
        self.calls.append((url, cookies_file))
        if self.produce is None:
            return Fetched(files=[], username=self.username)
        target = into / self.filename
        target.write_bytes(self.produce)
        produced = [target]
        for index, extra in enumerate(self.also):
            beside = into / f"{index}-{self.filename}"
            beside.write_bytes(extra)
            produced.append(beside)
        return Fetched(files=produced, username=self.username)


class RealImport:
    """A stand-in for the shared import pipeline that runs the real gate and the real store.

    This is what makes the integration tests real: a file the fake downloader produced goes through
    `verify_ingress` (which quarantines a disguised one and raises) and, if it passes, into the
    content store, so the asset the ledger points at genuinely exists.
    """

    def __init__(self, store: ContentStore, root: LibraryRoot, settings: Settings) -> None:
        self._store = store
        self._root = root
        self._settings = settings

    async def __call__(
        self,
        *,
        path: Path,
        origin: Origin,
        dest_folder_id: str | None,
        ctx: JobContext,
    ) -> FakeOutcome:
        checked: IngressResult = verify_ingress(path, origin=origin, settings=self._settings)
        ingested = await self._store.ingest(
            checked, root_id=self._root.id, rel_path=path.name, folder_id=dest_folder_id
        )
        return FakeOutcome(asset_id=ingested.asset.id, was_duplicate=not ingested.asset_is_new)


@pytest.fixture(autouse=True)
def _clean_login_switches() -> None:
    """A tripped killswitch is module-global and would otherwise leak from one test into the next,
    where it reads as a site that mysteriously stopped sending its login."""
    cookie_health.reset()


@pytest.fixture(autouse=True)
def _managed_dirs(settings: Settings) -> None:
    """Create the directories Sift owns, so the job's workspace has somewhere to live."""
    for directory in settings.managed_dirs:
        directory.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def master_key() -> bytes:
    """A real 256-bit master key, the kind a login produces."""
    return generate_master_key()


@pytest.fixture
async def admin_id(temp_db: Database) -> str:
    """A real user row, and its id, for a write that records who took the act.

    A real row rather than a made-up id: the record keeps the user as a foreign key beside the
    snapshot that outlives it (`workbench_decisions.user_id`), so an event written against an id
    nothing points at is refused by the database rather than by anything here.
    """
    await temp_db.initialize_schema()
    user_id = new_id()
    await temp_db.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at)"
        " VALUES (?, 'an-admin', 'x', 'admin', 0)",
        (user_id,),
    )
    return user_id


@pytest.fixture
async def secret_store(temp_db: Database) -> SecretStore:
    await temp_db.initialize_schema()
    return SecretStore(temp_db)


class RecordingReindexer:
    """Stands in for the search index's "this changed" seam.

    A recorder rather than a no-op: attribution is supposed to tell the index it wrote a username,
    and a stub that silently accepted the call would let that stop happening without a test
    noticing, leaving the index stale.
    """

    def __init__(self) -> None:
        self.touched_ids: list[str] = []
        self.rebuilds = 0

    async def touched(self, asset_id: str) -> None:
        self.touched_ids.append(asset_id)

    async def touched_many(self, asset_ids: Sequence[str]) -> None:
        self.touched_ids.extend(asset_ids)

    async def queue_many(self, asset_ids: Sequence[str]) -> None:  # pragma: no cover (no rename)
        await self.touched_many(asset_ids)

    async def renamed(self) -> None:
        self.rebuilds += 1


@pytest.fixture
def reindexer() -> RecordingReindexer:
    return RecordingReindexer()


@pytest.fixture
async def download_service(
    temp_db: Database,
    job_queue: JobQueue,
    secret_store: SecretStore,
    reindexer: RecordingReindexer,
    content_store: ContentStore,
) -> DownloadService:
    return DownloadService(temp_db, job_queue, secret_store, reindexer, content_store)


@pytest.fixture
def real_import(
    content_store: ContentStore, library_root: LibraryRoot, settings: Settings
) -> RealImport:
    return RealImport(content_store, library_root, settings)


@pytest.fixture
def make_context(job_queue: JobQueue) -> MakeContext:
    """Build a real, claimed job context for a payload, so the handler runs as a worker runs it."""

    async def build(
        payload: dict[str, object],
        *,
        capabilities: SystemCapabilities,
        max_attempts: int = 3,
    ) -> JobContext:
        job_id = await job_queue.enqueue(
            "download", payload, max_attempts=max_attempts, require_handler=False
        )
        worker_id = new_id()
        while True:
            job = await job_queue.claim(worker_id)
            assert job is not None
            if job.id == job_id:
                return JobContext(
                    job=job, worker_id=worker_id, queue=job_queue, capabilities=capabilities
                )

    return build


@pytest.fixture
def workspaces(tmp_path: Path) -> Workspaces:
    """Where a job's work in progress lives. Real rather than faked: the whole point of the
    handler taking the job's own directory instead of a temporary one is that what it writes
    survives a pause, and a fake that hands back a fresh path each time would hide exactly that."""
    return Workspaces(tmp_path / "workspaces")
