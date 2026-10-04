# SPDX-License-Identifier: AGPL-3.0-or-later
"""Real bytes, a real library and a real queue for the slice that takes files in: media drawn by
ffmpeg, refusals from the ingress gate's corpus."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

import pytest

from sift.kernel.content import ContentStore, FolderRow, LibraryStore, Root
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobContext, JobQueue, SystemCapabilities, register_handler
from sift.slices.capture import jobs
from sift.slices.capture.pipeline import PROBE

#: Short enough that a suite drawing several stays quick, long enough to be a real video.
VIDEO_SECONDS = 2

#: The files the ingress gate is tested against, reused rather than re-made. A refusal here is the
#: same refusal the kernel proves it gives.
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


def corpus_file(destination: Path, name: str) -> Path:
    """Copy a file out of the ingress corpus, so the original is never moved by the gate."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(CORPUS / name, destination)
    return destination


@pytest.fixture
async def root(library_store: LibraryStore, tmp_path: Path) -> Root:
    """A watched library root; where an untargeted file lands is a setting, handed in as an answer
    (see `CaptureService.default_destination`)."""
    directory = tmp_path / "library"
    directory.mkdir()
    return await library_store.create_root(name="Downloads", abs_path=directory)


@pytest.fixture
def root_path(root: Root) -> Path:
    return Path(root.abs_path)


@pytest.fixture
async def default_folder(library_store: LibraryStore, root: Root) -> str:
    """Where a file with no target lands: the library's top folder, resolved by the caller."""
    top = await library_store.root_folder(root.id)
    assert top is not None
    return top.id


@pytest.fixture
async def subfolder(library_store: LibraryStore, root: Root) -> FolderRow:
    """A folder inside the root, for the drop-target-sets-the-destination case."""
    return await library_store.upsert_folder(root.id, "Vacations")


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

    async def renamed(self) -> None:
        self.rebuilds += 1


@pytest.fixture
def reindexer() -> RecordingReindexer:
    return RecordingReindexer()


@pytest.fixture
def handlers(settings: object, reindexer: RecordingReindexer, clean_handlers: None) -> None:
    """Claim this slice's job types, and `probe` as a no-op: the queue refuses an unclaimed type,
    and what is asserted is that the import asked for a probe."""
    jobs.register_handlers(settings=settings, reindexer=reindexer)  # type: ignore[arg-type]

    async def nothing(context: JobContext) -> None:
        return None

    register_handler(PROBE, nothing, name="Test job")


@pytest.fixture
def capabilities(content_store: ContentStore, library_store: LibraryStore) -> SystemCapabilities:
    return SystemCapabilities(content=content_store, library=library_store)


@pytest.fixture
def context_for(
    job_queue: JobQueue, capabilities: SystemCapabilities, handlers: None
) -> Callable[[str, dict[str, object]], Awaitable[JobContext]]:
    """A handler's context for a job really queued and claimed: progress, cancellation and fan-out
    are fenced on the claim."""

    async def build(job_type: str, payload: dict[str, object]) -> JobContext:
        job_id = await job_queue.enqueue(job_type, payload)
        worker_id = new_id()
        while True:
            job = await job_queue.claim(worker_id)
            assert job is not None, "the queue lost a job this test enqueued"
            if job.id == job_id:
                return JobContext(
                    job=job, worker_id=worker_id, queue=job_queue, capabilities=capabilities
                )

    return build
