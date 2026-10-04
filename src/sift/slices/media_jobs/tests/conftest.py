# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fixtures for the media jobs: real files made by ffmpeg's deterministic test patterns and indexed
through the real gate."""

from __future__ import annotations

import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested, LibraryStore
from sift.kernel.hardware import HardwareReport
from sift.kernel.ids import new_id
from sift.kernel.ingress import Origin, verify_ingress
from sift.kernel.jobs import JobContext, JobQueue, SystemCapabilities
from sift.testing.fixtures import LibraryRoot
from sift.testing.tools import stand_in_tool

#: Long enough to reach the ladder's first rung and be sampled more than once, short enough that a
#: suite that makes several of them stays quick.
VIDEO_SECONDS = 5


def draw(path: Path, source: str, seconds: int | None = None, *filters: str) -> Path:
    """Make a real media file with ffmpeg. Raises with ffmpeg's own words if it will not."""
    argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", source]
    if seconds is not None:
        argv += ["-t", str(seconds)]
    else:
        # A single still. Without this, ffmpeg reads the pattern as a numbered sequence and
        # refuses to write a second frame over the first.
        argv += ["-frames:v", "1"]
    if filters:
        argv += ["-vf", ",".join(filters)]
    if path.suffix == ".mp4":
        argv += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    result = subprocess.run([*argv, str(path)], capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"could not build the fixture: {result.stderr.decode()}")
    return path


@pytest.fixture
def hardware() -> HardwareReport:
    """A machine with no GPU: the ordinary case."""
    return HardwareReport(
        cpu_count=4,
        total_ram_bytes=8 * 1024**3,
        worker_concurrency=4,
        cuda=False,
        rocm=False,
        transcode_encoders=(),
        warnings=(),
    )


@pytest.fixture
def video(library_root: LibraryRoot) -> Path:
    """A real, decodable video sitting in a real library root."""
    return draw(library_root.path / "holiday.mp4", "testsrc2=size=320x240:rate=15", VIDEO_SECONDS)


@pytest.fixture
def picture(library_root: LibraryRoot) -> Path:
    return draw(library_root.path / "photo.png", "testsrc2=size=320x240:rate=1", None)


@pytest.fixture
def animation(library_root: LibraryRoot) -> Path:
    return draw(library_root.path / "loop.gif", "testsrc2=size=64x64:rate=10", 2)


@pytest.fixture
def animated_webp(library_root: LibraryRoot) -> Path:
    """A real animated WebP, written by ffmpeg, which cannot read it back."""
    path = library_root.path / "moving.webp"
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=5",
            "-t", "1", "-c:v", "libwebp_anim", "-loop", "0", "-q:v", "40", str(path),
        ],
        capture_output=True,
        check=False,
    )  # fmt: skip
    if result.returncode != 0:
        raise RuntimeError(f"could not build the fixture: {result.stderr.decode()}")
    return path


def webp_tools(where: Path, *, frames: int, size: str) -> dict[str, str]:
    """Stand-ins for `webpinfo` and `anim_dump` alone (see `scripts/check_animated_webp.py`)."""
    width, height = size.split("x")

    blocks = "".join("Chunk ANMF\n  Duration: 200\n" for _ in range(frames))
    report = f"  Animation: 1\n  Canvas size {width} x {height}\n{blocks}"
    info = stand_in_tool(where, "webpinfo", f"import sys\n\nsys.stdout.write({report!r})")

    dump = stand_in_tool(
        where,
        "anim_dump",
        f"""
        import pathlib, subprocess, sys

        folder = pathlib.Path(sys.argv[2])
        prefix = sys.argv[4]
        for index in range({frames}):
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size={size}:rate=1", "-frames:v", "1",
                    str(folder / f"{{prefix}}{{index:04d}}.png"),
                ],
                check=True,
            )
        """,
    )
    return {"webpinfo_path": info, "anim_dump_path": dump}


async def take_in(
    store: ContentStore, root: LibraryRoot, path: Path, settings: Settings
) -> Ingested:
    """Index a file the way a scan would: through the gate, then into the store."""
    checked = verify_ingress(path, origin=Origin.SCAN, settings=settings)
    return await store.ingest(checked, root_id=root.id, rel_path=path.name)


@pytest.fixture
async def ingested_video(
    content_store: ContentStore, library_root: LibraryRoot, video: Path, settings: Settings
) -> Ingested:
    return await take_in(content_store, library_root, video, settings)


@pytest.fixture
async def ingested_picture(
    content_store: ContentStore, library_root: LibraryRoot, picture: Path, settings: Settings
) -> Ingested:
    return await take_in(content_store, library_root, picture, settings)


@pytest.fixture
async def ingested_animation(
    content_store: ContentStore, library_root: LibraryRoot, animation: Path, settings: Settings
) -> Ingested:
    return await take_in(content_store, library_root, animation, settings)


@pytest.fixture
def capabilities(content_store: ContentStore, library_store: LibraryStore) -> SystemCapabilities:
    """What a handler may do as the system, built once."""
    return SystemCapabilities(content=content_store, library=library_store)


@pytest.fixture
def handlers(settings: Settings, hardware: HardwareReport, clean_handlers: None) -> None:
    """Claim the four job types for one test, since the queue refuses an unclaimed type; booting
    the app claims them too, so this is asked for."""
    from sift.slices.media_jobs import jobs

    jobs.register_handlers(settings=settings, hardware=hardware)


@pytest.fixture
def context_for(
    job_queue: JobQueue, capabilities: SystemCapabilities, handlers: None
) -> Callable[[str, dict[str, object]], Awaitable[JobContext]]:
    """A handler's context for a job really queued and claimed by this worker."""

    async def build(job_type: str, payload: dict[str, object]) -> JobContext:
        job_id = await job_queue.enqueue(job_type, payload)
        worker_id = new_id()
        # Claim past what is waiting: an earlier probe fans out children ahead of this job.
        while True:
            job = await job_queue.claim(worker_id)
            assert job is not None, "the queue lost a job this test enqueued"
            if job.id == job_id:
                return JobContext(
                    job=job, worker_id=worker_id, queue=job_queue, capabilities=capabilities
                )

    return build
