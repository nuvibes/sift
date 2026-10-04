# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two background jobs, with the feature stood in for: each checks the switch first, and the
model download's chunk callback and ticker meet."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from sift.kernel.jobs import family_of, registered_handlers
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.queue import JobCanceled
from sift.slices.watermarks import jobs

pytestmark = [pytest.mark.anyio]


@dataclass
class Feature:
    """Stands in for the service, recording what it was asked to do."""

    on: bool = True
    read: list[str] = field(default_factory=list)
    forced: list[bool] = field(default_factory=list)
    #: The running byte count the stand-in transfer reports, chunk by chunk. Ten of them, so that
    #: "it stopped part way" is a claim a test can measure.
    chunks: tuple[int, ...] = (10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
    expected: int = 100
    stopped_at: int | None = None
    #: How many turns pass before the first byte: a connection being opened.
    warmup: int = 0

    async def enabled(self) -> bool:
        return self.on

    async def read_asset(self, asset_id: str) -> None:
        self.read.append(asset_id)

    async def install_models(self, *, progress: Any = None, force: bool = False) -> list[str]:
        self.forced.append(force)
        for _ in range(self.warmup):
            await asyncio.sleep(0)
        for written in self.chunks:
            if progress is not None and not progress(written, self.expected):
                self.stopped_at = written
                return []
            await asyncio.sleep(0)
        return ["finder", "reader"]


@dataclass
class Context:
    payload: dict[str, Any] = field(default_factory=dict)
    reported: list[float] = field(default_factory=list)
    #: Set to make `raise_if_canceled` raise, which is what a cancelled job looks like from inside.
    canceled: bool = False

    async def set_progress(self, value: float) -> None:
        self.reported.append(value)

    async def raise_if_canceled(self) -> None:
        if self.canceled:
            raise JobCanceled("cancelled")


async def test_reading_one_file_reads_it_and_reports_it_finished() -> None:
    feature = Feature()
    context = Context(payload={"asset_id": "asset-1"})

    await jobs.read_one(context, service=feature)  # type: ignore[arg-type]

    assert feature.read == ["asset-1"]
    assert context.reported == [1.0]


async def test_a_read_claimed_after_the_feature_was_switched_off_does_nothing() -> None:
    feature = Feature(on=False)
    context = Context(payload={"asset_id": "asset-1"})

    await jobs.read_one(context, service=feature)  # type: ignore[arg-type]

    assert feature.read == []
    assert context.reported == []


async def test_a_download_claimed_after_the_feature_was_switched_off_fetches_nothing() -> None:
    """The switch exists to prevent exactly this network call."""
    feature = Feature(on=False)
    context = Context()

    await jobs.fetch_models(context, service=feature)  # type: ignore[arg-type]

    assert feature.forced == []
    assert context.reported == []


async def test_the_download_reports_where_it_got_to_and_finishes_at_the_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bar is fed from the ticker, so a download that published nothing until it finished
    would be a silent bar for as long as the transfer took."""
    monkeypatch.setattr(jobs, "_PROGRESS_TICK", 0)
    feature = Feature()
    context = Context()

    await jobs.fetch_models(context, service=feature)  # type: ignore[arg-type]

    assert feature.forced == [False]
    assert context.reported[-1] == 1.0
    assert any(0 < value < 1 for value in context.reported)


async def test_nothing_is_published_before_the_first_byte_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no total known yet, a fraction would be a division by zero or a bar at a made-up
    place. The tick passes, and the first real number is the first thing shown."""
    monkeypatch.setattr(jobs, "_PROGRESS_TICK", 0)
    context = Context()

    await jobs.fetch_models(context, service=Feature(warmup=3))  # type: ignore[arg-type]

    assert context.reported[0] > 0


async def test_fetching_again_asks_for_files_already_on_disk() -> None:
    """A model is called installed if it EXISTS; a damaged one is replaced by fetching again, which
    nobody should have to do by deleting a file at a shell."""
    feature = Feature()

    await jobs.fetch_models(Context(payload={"again": True}), service=feature)  # type: ignore[arg-type]

    assert feature.forced == [True]


async def test_cancelling_stops_the_transfer_rather_than_tearing_it_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopped by a flag the transfer reads, so the partial file it leaves is one the next attempt
    can resume from: torn down mid-write, its length could not be trusted."""
    monkeypatch.setattr(jobs, "_PROGRESS_TICK", 0)
    feature = Feature()

    with pytest.raises(JobCanceled):
        await jobs.fetch_models(Context(canceled=True), service=feature)  # type: ignore[arg-type]

    assert feature.stopped_at is not None, "the transfer ran to the end after being cancelled"
    assert feature.stopped_at < feature.expected


def test_both_jobs_are_registered_and_reading_is_part_of_identify(clean_handlers: None) -> None:
    """Reading a watermark answers the same question the face pass does (who is this file from),
    so it is one row on the screen with it rather than a second."""
    jobs.register_handlers(service=Feature())  # type: ignore[arg-type]

    registered = registered_handlers()
    assert {jobs.WATERMARK_READ, jobs.WATERMARK_FETCH_MODELS} <= set(registered)
    assert family_of(jobs.WATERMARK_READ) is Family.IDENTIFY
    assert family_of(jobs.WATERMARK_FETCH_MODELS) is Family.OTHER


async def test_a_registered_handler_runs_the_job_it_was_registered_for(
    clean_handlers: None,
) -> None:
    feature = Feature()
    jobs.register_handlers(service=feature)  # type: ignore[arg-type]

    handlers = registered_handlers()
    await handlers[jobs.WATERMARK_READ](Context(payload={"asset_id": "a"}))  # type: ignore[arg-type]
    await handlers[jobs.WATERMARK_FETCH_MODELS](Context())  # type: ignore[arg-type]

    assert feature.read == ["a"]
    assert feature.forced == [False]
