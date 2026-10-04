# SPDX-License-Identifier: AGPL-3.0-or-later
"""Staging an upload and queuing its import: the little a request may safely do itself."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import UploadFile

from sift.kernel.content import LibraryStore, Root
from sift.kernel.ingress import NoDestination, Origin
from sift.kernel.jobs import JobQueue
from sift.slices.capture.jobs import IMPORT
from sift.slices.capture.service import CaptureService

pytestmark = [pytest.mark.integration]


def _upload(data: bytes, name: str) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


@pytest.fixture
def service(
    library_store: LibraryStore, job_queue: JobQueue, tmp_path: Path, default_folder: str
) -> CaptureService:
    """Wired with a default destination, the way the running application wires it.

    Where a file with no target lands is one stored answer for the whole install, handed over as a
    folder id.
    """

    async def default() -> str | None:
        return default_folder

    return CaptureService(
        library_store, job_queue, tmp_path / "imports", default_destination=default
    )


@pytest.fixture
def service_with_no_default(
    library_store: LibraryStore, job_queue: JobQueue, tmp_path: Path
) -> CaptureService:
    """A fresh install, before anybody has chosen where downloads go."""
    return CaptureService(library_store, job_queue, tmp_path / "imports")


async def test_it_stages_the_bytes_and_queues_an_import_that_carries_only_ids(
    service: CaptureService,
    job_queue: JobQueue,
    root: Root,
    handlers: None,
    tmp_path: Path,
) -> None:
    """The bytes go to disk under a fresh id, and the job is handed that id, never the path."""
    upload = _upload(b"some pretend bytes", "clip.mp4")

    job_id = await service.stage_and_enqueue(upload, origin=Origin.UPLOAD, dest_folder_id=None)

    job = await job_queue.get(job_id)
    assert job is not None
    assert job.type == IMPORT
    assert job.payload["origin"] == "upload"
    # The default download folder was resolved to a concrete id now, not left as a question.
    default = await service.resolve_destination(None)
    assert job.payload["dest_folder_id"] == default.folder_id

    staging_id = job.payload["staging_id"]
    assert isinstance(staging_id, str)
    staged = tmp_path / "imports" / staging_id / "clip.mp4"
    assert staged.read_bytes() == b"some pretend bytes"


async def test_it_refuses_when_there_is_nowhere_to_put_the_file(
    service_with_no_default: CaptureService, tmp_path: Path
) -> None:
    """With no folder named and no default set, nothing is written and nothing is queued."""
    with pytest.raises(NoDestination):
        await service_with_no_default.stage_and_enqueue(
            _upload(b"x", "x.mp4"), origin=Origin.UPLOAD, dest_folder_id=None
        )
    assert not (tmp_path / "imports").exists()


async def test_an_upload_with_no_filename_is_still_staged(
    service: CaptureService,
    job_queue: JobQueue,
    root: Root,
    handlers: None,
    tmp_path: Path,
) -> None:
    upload = UploadFile(file=io.BytesIO(b"bytes"), filename=None)
    job_id = await service.stage_and_enqueue(upload, origin=Origin.PASTE, dest_folder_id=None)

    job = await job_queue.get(job_id)
    assert job is not None
    staged = tmp_path / "imports" / job.payload["staging_id"] / "file"
    assert staged.read_bytes() == b"bytes"


def test_the_staging_root_is_under_the_data_directory() -> None:
    assert CaptureService.staging_root(Path("/data")) == Path("/data/imports")
