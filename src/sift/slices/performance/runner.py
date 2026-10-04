# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one self-test run there may be at a time, and the two things that ask for it.

The Performance screen asks for a run and reads it back as it goes. A Build asks for one too, as
its first step, when this machine has never been measured: every read it is about to make is
shaped by two rates only the self-test measures, and a Build that guessed them would be the
core-count guess the self-test exists to replace. Both go through here, so a run started from the
screen is the run the Build waits for and a Build's run shows on the screen.

What a run reads (the loop's worst lag, the thread pool's worst wait, the settings as they stand,
the folders the library is on) is handed in as callables from the composition root rather than
read from the application here: the watches are built later in the boot than the Build's handlers,
and they are only read while a run is going, which is after the boot is over.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Protocol

from sift.kernel.config import Settings
from sift.kernel.content.mounts import storage_of
from sift.kernel.hardware import HardwareReport
from sift.kernel.log import get_logger
from sift.kernel.media import ReadRates
from sift.kernel.wiring import Part
from sift.slices.performance import selftest
from sift.slices.performance.rates import MachineRates, RatesStore, now
from sift.slices.performance.selftest import Measurement, SelfTest, StorageToMeasure

log = get_logger(__name__)


class _HasRoots(Protocol):
    async def roots(self) -> Sequence[_Root]: ...


class _Root(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def abs_path(self) -> str: ...


class _ReadsSettings(Protocol):
    async def get_app(self, key: str) -> object: ...


async def current_settings(hub: _ReadsSettings) -> dict[str, int]:
    """What the settings say now, so a recommendation can show what it is changing from.

    Read through the hub rather than from a resolver: what belongs beside "should be 6" is the
    number on the screen, which for an automatic setting is 0, and a resolver would helpfully
    turn that into the derived answer and hide the fact that nothing was ever chosen.
    """
    wanted = (selftest.WORKER_COUNT_KEY, selftest.GENERATION_LIMIT_KEY, selftest.SHARE_READS_KEY)
    current: dict[str, int] = {}
    for key in wanted:
        with contextlib.suppress(TypeError, ValueError):
            current[key] = int(await hub.get_app(key) or 0)  # type: ignore[call-overload]
    return current


async def storages_to_measure(library: _HasRoots) -> list[StorageToMeasure]:
    """Every storage the library sits on, with the folders on it, for the storage half of the test.

    Grouped by the storage the operating system names rather than by folder: nine folders on one
    share are one share, and it is the share that has a number of readers it can serve.
    """
    grouped: dict[str, StorageToMeasure] = {}
    for root in await library.roots():
        where = await asyncio.to_thread(storage_of, Path(root.abs_path))
        known = grouped.get(where.key)
        if known is None:
            grouped[where.key] = StorageToMeasure(
                storage=where.key,
                label=root.name,
                remote=where.remote,
                roots=(Path(root.abs_path),),
            )
        else:
            grouped[where.key] = StorageToMeasure(
                storage=known.storage,
                label=f"{known.label}, {root.name}",
                remote=known.remote,
                roots=(*known.roots, Path(root.abs_path)),
            )
    return list(grouped.values())


class SelfTestRunner:
    """Starts a run, keeps the one in flight, and files its rates when it finishes."""

    def __init__(
        self,
        *,
        settings: Settings,
        hardware: HardwareReport,
        worst_lag: Callable[[], float],
        worst_wait: Callable[[], float],
        current: Callable[[], Awaitable[dict[str, int]]],
        storages: Callable[[], Awaitable[Sequence[StorageToMeasure]]],
        rates: RatesStore,
    ) -> None:
        self._settings = settings
        self._hardware = hardware
        self._worst_lag = worst_lag
        self._worst_wait = worst_wait
        self._current = current
        self._storages = storages
        self._rates = rates
        self.state = SelfTest()
        """What the screen reads. Replaceable, so a test can plant a finished run."""
        self._task: asyncio.Task[None] | None = None
        self._recalled = False

    def start(self) -> bool:
        """Begin a run, unless one is going. Whether this call began it.

        A second request while one is running is not an error and does not start a second test:
        two of these at once would measure each other. The task is held here so nothing else has
        to: a task nobody keeps a reference to may be collected while it is still running, which
        would be a measurement that silently stops partway.
        """
        if self.state.running:
            return False
        self.state.running = True
        self.state.finished_at = None
        self.state.measurement = None
        self.state.recommendations = []
        self._task = asyncio.create_task(self._run())
        return True

    async def recall(self) -> None:
        """Put the last kept measurement of THIS hardware back on the screen, once per process.

        The whole reading is kept per hardware profile (`rates` v2), so a changed machine recalls
        nothing. Recommendations are worked out again against the settings as they stand, as a
        finished run does. Asked by the screen's read rather than at boot, so the boot never waits
        on it. Never over a run in flight or one that finished in this process: that one is newer.
        """
        if self._recalled or self.state.running or self.state.measurement is not None:
            return
        self._recalled = True
        kept = await self._rates.load(self._hardware.profile)
        if kept is None or kept.measurement is None:
            return
        self.state.measurement = kept.measurement
        self.state.recommendations = selftest.recommend(
            kept.measurement, current=await self._current()
        )
        self.state.finished_at = time.monotonic()

    async def measured(self) -> bool:
        """Whether this hardware has rates on file from an earlier run."""
        return await self._rates.load(self._hardware.profile) is not None

    async def rates(self) -> MachineRates | None:
        """This hardware's rates, or None where it has never been measured."""
        return await self._rates.load(self._hardware.profile)

    async def read_rates(self, path: Path) -> ReadRates | None:
        """This machine's rates for reading the file at `path`, with its storage's own numbers
        where the file is on a share that was measured. None where never measured."""
        rates = await self._rates.load(self._hardware.profile)
        if rates is None:
            return None
        where = await asyncio.to_thread(storage_of, path)
        return rates.for_reading(where.key if where.remote else None)

    async def measure(self) -> None:
        """Run the test and wait for it: the one in flight if there is one, a new one if not."""
        self.start()
        # `start` holds the task whenever it flips `running`, on this same loop turn, so the two
        # cannot disagree by the time this line runs.
        if self._task is not None:  # pragma: no branch (the task is always held by here)
            await asyncio.shield(self._task)

    async def _run(self) -> None:
        """The test itself, on its own task, cleaning up after itself however it ends."""
        state = self.state
        current = await self._current()
        workspace = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="sift-selftest-"))
        try:

            def reached(partial: Measurement) -> None:
                """Publish what has been measured SO FAR, so the screen can show a run advancing.

                The same field the finished run lands in, one rung short, not a second progress
                shape beside it. It reads as running rather than finished because `finished_at`
                is what says a run is over, and that is set once below.
                """
                state.measurement = partial

            measurement = await selftest.measure(
                workspace=workspace,
                settings=self._settings,
                cores=self._hardware.cpu_count,
                worst_lag=self._worst_lag,
                worst_wait=self._worst_wait,
                report=reached,
                storages=await self._storages(),
            )
            state.measurement = measurement
            state.recommendations = selftest.recommend(measurement, current=current)
            state.finished_at = time.monotonic()
            if measurement.failed is None:
                rates = MachineRates.from_measurement(
                    self._hardware.profile, measurement, now=now()
                )
                await self._rates.save(rates)
                log.info(
                    "performance.selftest.rates_kept",
                    profile=rates.profile,
                    decode_fps=rates.decode_fps,
                    seek_seconds=rates.seek_seconds,
                    storages=len(rates.storages),
                )
        finally:
            state.running = False
            await asyncio.to_thread(_discard, workspace)


def _discard(workspace: Path) -> None:
    """Remove the directory this run made for itself, and only that.

    Under the system temp directory, holding the clips the test built and nothing else. Never a
    library, and never a file anybody else put there. Suppressed on the line rather than for the
    file, so the rule keeps watching everything else here.

    The suppression sits on the opening line on purpose: the formatter wraps this call, and a
    comment left on the closing bracket is attached to nothing.
    """
    import shutil

    shutil.rmtree(  # nosemgrep: sift-no-file-removal-outside-delete-trash
        workspace, ignore_errors=True
    )


#: The runner, on the application: the screen's routes and the Build both reach it here.
SELF_TEST_RUNNER: Part[SelfTestRunner] = Part("self_test_runner")
