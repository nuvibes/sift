# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real library on a real disk, for the slice that reads one: media drawn by ffmpeg, and
refusals from the ingress gate's own corpus."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

import pytest

# Removing a root writes through the ledger door into the workbench's table.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, LibraryStore, Root
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobContext, JobQueue, SystemCapabilities
from sift.kernel.jobs.tuning import DEFAULT_PRIORITY
from sift.slices.library_roots import jobs, quarantine, taking_in
from sift.slices.library_roots.service import LibraryService
from sift.testing.tools import ON_WINDOWS, POSIX_ONLY, WINDOWS_ONLY, junction

#: Short enough that a suite drawing several stays quick, long enough to be a real video.
VIDEO_SECONDS = 2

#: The files the ingress gate is tested against, reused.
CORPUS = Path(__file__).resolve().parents[3] / "kernel" / "tests" / "fixtures" / "ingress"


def draw(path: Path, source: str, seconds: int | None = None) -> Path:
    """Make a real media file with ffmpeg. Raises with ffmpeg's own words if it will not."""
    path.parent.mkdir(parents=True, exist_ok=True)
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", source]
    if seconds is not None:
        argv += ["-t", str(seconds)]
    else:
        argv += ["-frames:v", "1"]
    if path.suffix == ".mp4":
        argv += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    result = subprocess.run([*argv, str(path)], capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"could not build the fixture: {result.stderr.decode()}")
    return path


def refused_file(destination: Path, name: str = "disguised_exe.mp4") -> Path:
    """A file from the corpus that the gate refuses. Named `.mp4` and not one."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(CORPUS / name, destination)
    return destination


@pytest.fixture
def service(temp_db: Database, library_store: LibraryStore, access: Repository) -> LibraryService:
    return LibraryService(temp_db, library_store, access)


@pytest.fixture
async def root(library_store: LibraryStore, tmp_path: Path) -> Root:
    """A library root made through the store, as a person would have one."""
    directory = tmp_path / "library"
    directory.mkdir()
    return await library_store.create_root(name="Videos", abs_path=directory)


@pytest.fixture
def root_path(root: Root) -> Path:
    return Path(root.abs_path)


class RecordingReindexer:
    """Stands in for the search index and remembers what it was told; the index is not this
    slice's."""

    def __init__(self) -> None:
        self.told: list[str] = []
        self.rebuilds = 0

    async def touched(self, asset_id: str) -> None:
        self.told.append(asset_id)

    async def touched_many(self, asset_ids: Sequence[str]) -> None:
        self.told.extend(asset_ids)

    async def queue_many(self, asset_ids: Sequence[str]) -> None:  # pragma: no cover (no rename)
        await self.touched_many(asset_ids)

    async def renamed(self) -> None:
        self.rebuilds += 1


@pytest.fixture
def reindexer() -> RecordingReindexer:
    return RecordingReindexer()


@pytest.fixture
def handlers(
    settings: Settings,
    service: LibraryService,
    reindexer: RecordingReindexer,
    job_queue: JobQueue,
    content_store: ContentStore,
    clean_handlers: None,
) -> None:
    """Claim this slice's job types, and `probe` as a no-op: the queue refuses an unclaimed type,
    and what is asserted is that the scan asked for a probe."""
    from sift.kernel.jobs import register_handler

    jobs.register_handlers(
        settings=settings,
        service=service,
        reindexer=reindexer,
        queue=job_queue,
        preferences=_Preferences(),
        content=content_store,
    )

    async def nothing(context: JobContext) -> None:
        return None

    register_handler(taking_in.PROBE, nothing, name="Test job")


class _Preferences:
    """The retention rule at its registered default. These tests are about the scanner, not the
    quarantine sweep, which has its own."""

    async def get_app(self, key: str) -> object:
        return quarantine.DEFAULT_KEEP_DAYS

    async def get_user(self, user_id: str, key: str) -> object:
        return None


@pytest.fixture
def capabilities(content_store: ContentStore, library_store: LibraryStore) -> SystemCapabilities:
    return SystemCapabilities(content=content_store, library=library_store)


@pytest.fixture
def context_for(
    job_queue: JobQueue, capabilities: SystemCapabilities, handlers: None
) -> Callable[..., Awaitable[JobContext]]:
    """A handler's context for a job really queued and claimed, since progress, cancellation and
    fan-out are fenced on the claim. `priority` is the row's own."""

    async def build(
        job_type: str, payload: dict[str, object], *, priority: int = DEFAULT_PRIORITY
    ) -> JobContext:
        job_id = await job_queue.enqueue(job_type, payload, priority=priority)
        worker_id = new_id()
        while True:
            job = await job_queue.claim(worker_id)
            assert job is not None, "the queue lost a job this test enqueued"
            if job.id == job_id:
                return JobContext(
                    job=job, worker_id=worker_id, queue=job_queue, capabilities=capabilities
                )

    return build


# --- the two things Windows will not let a test do -----------------------------------------------
#
# Declared in `sift.testing.tools`, shared with the backup slice.

WINDOWS = ON_WINDOWS

__all__ = ["POSIX_ONLY", "WINDOWS", "WINDOWS_ONLY", "junction"]
