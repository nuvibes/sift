# SPDX-License-Identifier: AGPL-3.0-or-later
"""Downloading the graphics-card runtime as a watched job: the transfer's chunk callback records a
number and reads a flag, and a ticker turns them into a progress row and a stop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from sift.kernel.config import Settings
from sift.kernel.jobs import JobCanceled
from sift.kernel.ml import accel
from sift.slices.performance import jobs

pytestmark = pytest.mark.anyio


@dataclass
class Context:
    """Enough of a job context to see what the handler said about itself."""

    payload: dict[str, Any] = field(default_factory=dict)
    progress: list[float] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    canceled: bool = False

    async def set_progress(self, value: float) -> None:
        self.progress.append(value)

    async def set_note(self, value: str) -> None:
        self.notes.append(value)

    async def raise_if_canceled(self) -> None:
        if self.canceled:
            raise JobCanceled("stopped")


@pytest.fixture
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tick short enough that the ticker runs several times inside a test."""
    monkeypatch.setattr(jobs, "_PROGRESS_TICK", 0.01)


async def test_the_row_says_how_far_along_the_transfer_is(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, quick: None
) -> None:
    """In megabytes, because bytes are unreadable and a bare percentage says nothing about size."""

    async def install(_settings: Settings, *, progress: Any = None) -> bool:
        assert progress is not None
        progress(300_000_000, 1_200_000_000)
        await asyncio.sleep(0.05)
        return True

    monkeypatch.setattr(accel, "install", install)
    monkeypatch.setattr(accel, "works", _answers(None))
    context = Context()

    await jobs.install_accelerator(context, settings=settings)  # type: ignore[arg-type]

    assert "300 MB of 1200 MB" in context.notes
    assert context.progress[0] == pytest.approx(0.25)
    assert context.progress[-1] == 1.0


async def test_stopping_it_asks_the_reader_to_stop_rather_than_tearing_it_down(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, quick: None
) -> None:
    """The whole reason the flag exists.

    Tearing the transfer down mid-chunk leaves a partial file nothing can resume from. Setting a
    flag lets the reader stop asking for the next one, so what arrived is still there and the next
    attempt continues from it.
    """
    told_to_stop: list[bool] = []

    async def install(_settings: Settings, *, progress: Any = None) -> bool:
        assert progress is not None
        for _ in range(200):
            told_to_stop.append(progress(1, 10) is False)
            if told_to_stop[-1]:
                return False
            await asyncio.sleep(0.01)
        raise AssertionError("the reader was never asked to stop")

    monkeypatch.setattr(accel, "install", install)
    context = Context(canceled=True)

    with pytest.raises(JobCanceled):
        await jobs.install_accelerator(context, settings=settings)  # type: ignore[arg-type]

    assert told_to_stop[-1] is True


async def test_a_transfer_that_was_stopped_installs_nothing_and_says_nothing_more(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, quick: None
) -> None:
    """False from the install is somebody having pressed stop, which is not a failure to report."""

    async def install(_settings: Settings, *, progress: Any = None) -> bool:
        return False

    monkeypatch.setattr(accel, "install", install)
    monkeypatch.setattr(accel, "works", _never_asked)
    context = Context()

    await jobs.install_accelerator(context, settings=settings)  # type: ignore[arg-type]

    assert context.progress == [], "a stopped transfer reported itself as finished"


async def test_a_runtime_that_installs_but_cannot_run_a_model_says_so_on_the_job(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, quick: None
) -> None:
    """Proved here rather than left to the first real use.

    A card that cannot run a model is a fact somebody wants while they are still looking at the
    screen that downloaded it, not hours later, in a job that quietly failed.
    """

    async def install(_settings: Settings, *, progress: Any = None) -> bool:
        return True

    monkeypatch.setattr(accel, "install", install)
    monkeypatch.setattr(accel, "works", _answers("the card would not run a model"))
    context = Context()

    await jobs.install_accelerator(context, settings=settings)  # type: ignore[arg-type]

    assert context.notes[-1] == "the card would not run a model"


def _answers(problem: str | None) -> Any:
    async def works(_settings: Settings) -> str | None:
        return problem

    return works


async def _never_asked(_settings: Settings) -> str | None:
    raise AssertionError("the card was tested after a transfer nobody finished")


async def test_a_transfer_that_says_nothing_about_its_size_draws_no_row(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, quick: None
) -> None:
    """A total of zero is a server that did not say how big the file is.

    Dividing by it would raise inside the ticker, which is a background task, so the failure
    would arrive as a job that stopped reporting rather than as anything anybody could read.
    """

    async def install(_settings: Settings, *, progress: Any = None) -> bool:
        assert progress is not None
        progress(500, 0)
        await asyncio.sleep(0.05)
        return True

    monkeypatch.setattr(accel, "install", install)
    monkeypatch.setattr(accel, "works", _answers(None))
    context = Context()

    await jobs.install_accelerator(context, settings=settings)  # type: ignore[arg-type]

    # The only progress written is the one at the end, when it is known to be done.
    assert context.progress == [1.0]
    assert context.notes == []
