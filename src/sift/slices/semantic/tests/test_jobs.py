# SPDX-License-Identifier: AGPL-3.0-or-later
"""The background work, and the three ways it declines to do anything.

**Every job checks the switch first.** One queued before somebody switched the feature off finds it
off and stops, rather than doing the work its payload describes, which is the difference between
"I turned that off" and a machine that goes on reading every file for another hour.

**The sweep stops when it cannot run rather than queueing work that will fail.** Switched on with
no models is an ordinary state, and a sweep that queued ten thousand pieces of work into it would
put ten thousand identical failures in the job log and bury the one line that says why.

**The sweep steps by what a page WALKED, not by what it asked for.** The access layer caps a page,
so a sweep advancing by the limit steps over everything between the cap and the request, silently,
with nothing to see but a sweep that keeps saying it has finished.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from sift.kernel.jobs.queue import JobCanceled
from sift.kernel.ledger import Actor
from sift.slices.semantic import jobs as semantic_jobs
from sift.slices.semantic.service import Readiness

pytestmark = pytest.mark.unit


@dataclass
class Job:
    id: str = "the-run"


@dataclass
class Queue:
    settled: list[tuple[str, ...]] = field(default_factory=list)

    async def settle_into(self, job_types: Sequence[str], *, priority: int = 0) -> None:
        self.settled.append(tuple(job_types))


@dataclass
class Context:
    job: Job = field(default_factory=Job)
    queue: Queue = field(default_factory=Queue)
    payload: dict[str, Any] = field(default_factory=dict)
    progress: float | None = None
    note: str | None = None
    canceled: bool = False
    children: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def set_progress(self, value: float) -> None:
        self.progress = value

    async def set_note(self, value: str) -> None:
        self.note = value

    async def enqueue_child(self, job_type: str, payload: dict[str, Any] | None = None) -> str:
        self.children.append((job_type, payload or {}))
        return f"child-{len(self.children)}"

    async def raise_if_canceled(self) -> None:
        if self.canceled:
            raise JobCanceled("cancelled")


def ready(**overrides: Any) -> Readiness:
    values: dict[str, Any] = {
        "supported": True,
        "enabled": True,
        "ready": True,
        "family": "compact",
        "device": "cpu",
        "problem": None,
    }
    values.update(overrides)
    return Readiness(**values)


class Recording:
    """A service that records what was asked of it and answers what the test set up."""

    def __init__(
        self, *, switched_on: bool = True, readiness: Readiness | None = None, frames: int = 3
    ) -> None:
        self.frames = frames
        self.switched_on = switched_on
        self._readiness = readiness or ready()
        self.described: list[str] = []
        self.installed: list[str] = []
        self.forced: list[bool] = []

    async def enabled(self) -> bool:
        return self.switched_on

    async def readiness(self) -> Readiness:
        return self._readiness

    async def describe_asset(self, asset_id: str) -> int:
        self.described.append(asset_id)
        return self.frames

    async def install_models(
        self, *, progress: Any = None, force: bool = False, on_file: Any = None
    ) -> list[str]:
        self.forced.append(force)
        if progress is not None:
            progress(10, 100)
        return self.installed


# --- describing one file ------------------------------------------------------------------


async def test_describing_one_file_runs_it_and_reports_it_finished() -> None:
    service = Recording()
    context = Context(payload={"asset_id": "asset-1"})

    await semantic_jobs.describe(context, service=service)  # type: ignore[arg-type]

    assert service.described == ["asset-1"]
    assert context.progress == 1.0


async def test_a_description_written_asks_for_the_passes_that_read_descriptions() -> None:
    """The shoots pass reads descriptions; a file described after the scan's own settle has run
    it asks for it again, and a file that wrote nothing asks for nothing."""
    context = Context(payload={"asset_id": "asset-1"})
    await semantic_jobs.describe(context, service=Recording(), settles_into=("shoots_look",))  # type: ignore[arg-type]
    assert context.queue.settled == [("shoots_look",)]

    quiet = Context(payload={"asset_id": "asset-2"})
    await semantic_jobs.describe(quiet, service=Recording(frames=0), settles_into=("shoots_look",))  # type: ignore[arg-type]
    assert quiet.queue.settled == []


async def test_a_file_queued_before_the_switch_went_off_is_not_described() -> None:
    service = Recording(switched_on=False)
    context = Context(payload={"asset_id": "asset-1"})

    await semantic_jobs.describe(context, service=service)  # type: ignore[arg-type]

    assert service.described == []


async def test_fetching_the_models_reports_it_finished() -> None:
    service = Recording()
    service.installed = ["compact.pictures"]
    context = Context()

    await semantic_jobs.fetch_models(context, service=service)  # type: ignore[arg-type]

    assert context.progress == 1.0


async def test_the_models_are_not_fetched_for_a_feature_nobody_switched_on() -> None:
    """The one network call the switch exists to prevent."""
    service = Recording(switched_on=False)
    context = Context()

    await semantic_jobs.fetch_models(context, service=service)  # type: ignore[arg-type]

    assert context.progress is None


# --- the rest of the wiring ------------------------------------------------------------------


async def test_a_download_publishes_its_progress_while_it_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seam between an ordinary callback that cannot wait and a progress row that has to be
    written. A ticker beside the transfer turns one into the other."""
    import asyncio

    monkeypatch.setattr(semantic_jobs, "_PROGRESS_TICK", 0.01)

    class Slow(Recording):
        async def install_models(
            self, *, progress: Any = None, force: bool = False, on_file: Any = None
        ) -> list[str]:
            if progress is not None:
                progress(50, 100)
            await asyncio.sleep(0.05)
            return ["compact.pictures"]

    context = Context()

    await semantic_jobs.fetch_models(context, service=Slow())  # type: ignore[arg-type]

    assert context.progress == 1.0


async def test_cancelling_a_download_stops_it_asking_for_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelled by setting a flag rather than by tearing the transfer down: the reader stops
    asking for the next piece and leaves a partial file the next attempt resumes from. Tearing it
    down mid-write would leave a file whose length nobody can trust."""
    import asyncio

    monkeypatch.setattr(semantic_jobs, "_PROGRESS_TICK", 0.01)
    told_to_stop: list[bool] = []

    class Watching(Recording):
        async def install_models(
            self, *, progress: Any = None, force: bool = False, on_file: Any = None
        ) -> list[str]:
            for _ in range(20):
                await asyncio.sleep(0.01)
                if progress is not None:
                    told_to_stop.append(progress(10, 100))
                    if not told_to_stop[-1]:
                        return []
            return []

    context = Context(canceled=True)

    with pytest.raises(JobCanceled):
        await semantic_jobs.fetch_models(context, service=Watching())  # type: ignore[arg-type]

    assert False in told_to_stop


async def test_a_download_that_has_not_started_publishes_no_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bar drawn from nothing would sit at zero per cent of zero, which reads as stuck rather
    than as not yet begun."""
    import asyncio

    monkeypatch.setattr(semantic_jobs, "_PROGRESS_TICK", 0.01)
    published: list[float] = []

    class Silent(Recording):
        async def install_models(
            self, *, progress: Any = None, force: bool = False, on_file: Any = None
        ) -> list[str]:
            await asyncio.sleep(0.05)
            return []

    class Watching(Context):
        async def set_progress(self, value: float) -> None:
            published.append(value)
            self.progress = value

    await semantic_jobs.fetch_models(Watching(), service=Silent())  # type: ignore[arg-type]

    # Only the one written when it finished.
    assert published == [1.0]


async def test_the_bar_says_which_file_of_the_set_is_arriving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A working set is three separate downloads of very different sizes, so a single fraction runs
    to one and back to zero three times. Without a note beside it that reads as a download that
    keeps failing and starting over.

    The fraction is across the WHOLE set rather than within one file, so the bar only ever goes
    forward, each file counting for an equal share, which is wrong about the bytes and right
    about what a bar is for. The names beside it are what make the even share visible.
    """
    import asyncio

    monkeypatch.setattr(semantic_jobs, "_PROGRESS_TICK", 0.01)
    seen: list[tuple[float, str | None]] = []

    class ThreeFiles(Recording):
        async def install_models(
            self, *, progress: Any = None, force: bool = False, on_file: Any = None
        ) -> list[str]:
            for index, role in enumerate(("picture reader", "word reader", "vocabulary")):
                if on_file is not None:
                    on_file(index, 3, role)
                for _ in range(3):
                    await asyncio.sleep(0.01)
                    if progress is not None:
                        progress(50, 100)
            return ["a", "b", "c"]

    class Watching(Context):
        async def set_note(self, value: str) -> None:
            self.note = value
            seen.append((self.progress or 0.0, value))

    await semantic_jobs.fetch_models(Watching(), service=ThreeFiles())  # type: ignore[arg-type]

    notes = [note for _fraction, note in seen]
    assert "1 of 3 - picture reader" in notes
    assert "2 of 3 - word reader" in notes
    assert "3 of 3 - vocabulary" in notes
    # Never backwards: the second file at nothing must still read as further on than the first at
    # half, which is the whole reason the share is taken across the set.
    fractions = [fraction for fraction, _note in seen]
    assert fractions == sorted(fractions)
    assert max(fractions) <= 1.0


async def test_a_repair_asks_for_the_files_to_be_fetched_again() -> None:
    """A model file that exists but is not the one Sift expects refuses to load and says so.
    Without this there is no way to act on that from the app: every file is present, every one
    is skipped, and the job finishes instantly reporting success."""
    service = Recording()
    context = Context(payload={"again": True})

    await semantic_jobs.fetch_models(context, service=service)  # type: ignore[arg-type]

    assert service.forced == [True]


async def test_an_ordinary_fetch_does_not_ask_for_them_again() -> None:
    service = Recording()

    await semantic_jobs.fetch_models(Context(), service=service)  # type: ignore[arg-type]

    assert service.forced == [False]


async def test_deleting_the_index_is_done_for_whoever_pressed_it() -> None:
    """The History line says who deleted it; a run nobody pressed says Sift did."""
    cleared: list[object] = []

    class Clearing:
        async def clear_index(self, actor: object) -> None:
            cleared.append(actor)

    pressed = Context()
    pressed.pressed_by = "user-1"  # type: ignore[attr-defined]
    unpressed = Context()
    unpressed.pressed_by = None  # type: ignore[attr-defined]

    await semantic_jobs.forget(pressed, service=Clearing())  # type: ignore[arg-type]
    await semantic_jobs.forget(unpressed, service=Clearing())  # type: ignore[arg-type]

    assert cleared == [Actor.user("user-1"), None]
    assert pressed.progress == 1.0 and unpressed.progress == 1.0
