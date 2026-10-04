# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compression samples whose job has been swept: counted before they are removed, and only those."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from sift.kernel.config import Settings, ensure_directories
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import JobQueue
from sift.kernel.tidy import Resources
from sift.slices.media_edit.jobs import samples_directory
from sift.slices.media_edit.tidy import StrandedSamples, _remove_all, _size

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def resources(tmp_path: Path) -> AsyncIterator[Resources]:
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    ensure_directories(settings)
    database = Database(tmp_path / "data" / "sift.sqlite3")
    await database.connect()
    await database.initialize_schema()
    try:
        yield Resources(database=database, settings=settings)
    finally:
        await database.close()


async def test_a_sample_whose_job_is_gone_is_counted_and_removed_and_a_live_ones_is_kept(
    resources: Resources,
) -> None:
    """A sample is served for as long as its job's row exists and never afterwards; the queue
    sweeps the row a week on and nothing else sweeps the file. Only the file nothing can serve
    goes."""
    directory = samples_directory(resources.settings)
    directory.mkdir(parents=True)
    live = await JobQueue(resources.database).enqueue("compress_sample", require_handler=False)
    (directory / f"{live}.mp4").write_bytes(b"x" * 10)
    (directory / f"{new_id()}.mp4").write_bytes(b"x" * 30)
    tidying = StrandedSamples(resources)

    survey = await tidying.survey()

    assert survey.count == 1
    assert survey.frees_bytes == 30

    assert await tidying.run() == 1
    assert sorted(path.name for path in directory.iterdir()) == [f"{live}.mp4"]
    assert (await tidying.survey()).count == 0


async def test_a_folder_among_the_samples_is_neither_counted_nor_removed(
    resources: Resources,
) -> None:
    """Only files are samples. Anything else in the directory is not Sift's to count or sweep."""
    directory = samples_directory(resources.settings)
    (directory / "not-a-sample").mkdir(parents=True)
    (directory / f"{new_id()}.mp4").write_bytes(b"x" * 5)
    tidying = StrandedSamples(resources)

    assert (await tidying.survey()).count == 1
    assert await tidying.run() == 1
    assert (directory / "not-a-sample").is_dir()


def test_a_sample_that_went_between_the_survey_and_the_sweep_is_nothing(tmp_path: Path) -> None:
    """The listing is one moment and the sizing or the removal is another; a file that the
    queue's own sweep, or a person, took in between is not an error, it is zero bytes freed and
    one fewer removed."""
    gone = tmp_path / "gone.mp4"
    here = tmp_path / "here.mp4"
    here.write_bytes(b"x" * 7)

    assert _size(gone) == 0
    assert _size(here) == 7
    assert _remove_all([gone, here]) == 1
    assert not here.exists()


async def test_no_samples_directory_is_nothing_to_tidy(resources: Resources) -> None:
    tidying = StrandedSamples(resources)
    assert (await tidying.survey()).count == 0
    assert await tidying.run() == 0
