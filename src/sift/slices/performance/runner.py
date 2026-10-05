# SPDX-License-Identifier: AGPL-3.0-or-later
"""The one self-test run there may be at a time, and the things that ask for it.

A press and a Build on a device never measured both ask for the benchmark job, which calls `run` or
`measure_storage` here. What a run reads is handed in as callables by the composition root, since
the watches are built later in the boot and only read while a run is going.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from sift.kernel.config import Settings
from sift.kernel.content.mounts import storage_of
from sift.kernel.hardware import HardwareReport
from sift.kernel.log import get_logger
from sift.kernel.media import FFmpegError, ReadRates
from sift.kernel.wiring import Part
from sift.slices.performance import measure_encoder, measure_models, measure_together, selftest
from sift.slices.performance.measure_encoder import CardCurve, PreviewCommand
from sift.slices.performance.measure_models import ModelCurve, ModelPass
from sift.slices.performance.measure_together import Machine, Readings, Together
from sift.slices.performance.rates import MachineRates, RatesStore, StorageRate, now
from sift.slices.performance.selftest import (
    Measurement,
    Recommendation,
    SelfTest,
    StorageCurve,
    StorageToMeasure,
)

log = get_logger(__name__)

#: Said under a run that found no library folder to read.
NO_FOLDER = "No library folder yet, so no network share was measured."


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
    """The stored settings, not the resolved ones: an automatic setting reads 0 beside its advice."""
    wanted = (
        selftest.WORKER_COUNT_KEY,
        selftest.GENERATION_LIMIT_KEY,
        selftest.SHARE_READS_KEY,
        measure_models.RECOGNITION_SHARE_KEY,
    )
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
    return await asyncio.to_thread(_grouped, await library.roots())


def _grouped(roots: Sequence[_Root]) -> list[StorageToMeasure]:
    grouped: dict[str, StorageToMeasure] = {}
    for root in roots:
        where = storage_of(Path(root.abs_path))
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


@dataclass
class Found:
    """What a run measured past the CPU and storage ladders, and its sentences for the screen."""

    notes: list[str] = field(default_factory=list)
    card: CardCurve | None = None
    models: tuple[ModelCurve, ...] = ()
    together: Together | None = None

    def said(self) -> list[str]:
        card = self.card.said() if self.card is not None else []
        together = self.together.said() if self.together is not None else []
        return [*card, *(line for one in self.models for line in one.said()), *together]


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
        ask: Callable[[str | None], Awaitable[str | None]],
        preview: PreviewCommand | None = None,
        passes: Callable[[], Awaitable[Sequence[ModelPass]]] | None = None,
        together: Callable[..., Awaitable[Together]] | None = None,
        fell_behind: Callable[[], int] = lambda: 0,
        stalls: Callable[[], tuple[int, int]] = lambda: (0, 0),
    ) -> None:
        self._settings = settings
        self._hardware = hardware
        self._worst_lag = worst_lag
        self._worst_wait = worst_wait
        self._current = current
        self._storages = storages
        self._rates = rates
        self._ask = ask
        self._preview = preview
        self._passes = passes
        self._together = together
        self._fell_behind = fell_behind
        self._stalls = stalls
        self.caused = (0, 0)
        """The loop's and the threads' stalls since Sift started that a run caused on purpose."""
        self.card: CardCurve | None = None
        self.models: tuple[ModelCurve, ...] = ()
        self.together: Together | None = None
        self.state = SelfTest()
        """The result on screen: the last run that ended, or the one kept. Replaceable."""
        self.progress: Measurement | None = None
        """How far the run going has measured; None while it waits or when none is going."""
        self.notes: list[str] = []
        """What the last run could not measure or had to pause, in sentences for the screen."""
        self._task: asyncio.Task[None] | None = None
        self._recalled = False

    def start(self) -> bool:
        """Begin a run, unless one is going. Whether this call began it. The task is held here, as
        one nobody references may be collected mid-run."""
        if self.state.running:
            return False
        self.state.running = True
        self.progress = None
        self._task = asyncio.create_task(self._run())
        return True

    async def ask(self, requested_by: str | None) -> str | None:
        """Queue a whole run that suggests, unless one is coming. The job's id, or None."""
        return await self._ask(requested_by)

    async def recall(self) -> None:
        """Put this hardware's kept measurement back on the screen, once, never over a newer run."""
        if self._recalled or self.state.measurement is not None:
            return
        self._recalled = True
        kept = await self._rates.load(self._hardware.profile)
        if kept is None or kept.measurement is None:
            return
        found = Found(card=kept.card, models=kept.models, together=kept.together)
        found.notes = found.said()
        current = await self._current()
        self._show(
            kept.measurement, self.recommend(kept.measurement, current=current, run=found), found
        )

    def _show(self, measurement: Measurement, advice: list[Recommendation], found: Found) -> None:
        self.card, self.models, self.together = found.card, found.models, found.together
        self.notes = found.notes
        self.state.measurement = measurement
        self.state.recommendations = advice
        self.state.finished_at = time.monotonic()

    def recommend(
        self, measurement: Measurement, *, current: dict[str, int], run: Found | None = None
    ) -> list[Recommendation]:
        """The CPU's advice, with previews from the GPU, held to what kept up together, and
        recognition's share from its model: the shown run's, or `run`'s."""
        run = run or Found(card=self.card, models=self.models, together=self.together)
        found = selftest.recommend(measurement, current=current)
        tasks = next((one for one in found if one.key == selftest.WORKER_COUNT_KEY), None)
        if tasks is None:
            return found
        for at, one in enumerate(found):
            if one.key == selftest.GENERATION_LIMIT_KEY:
                card = measure_encoder.recommend_previews(
                    run.card, tasks=tasks.suggested, current=current, key=one.key, label=one.label
                )
                found[at] = card or one
        if run.together is not None:
            found = run.together.applied(found)
            tasks = next(one for one in found if one.key == selftest.WORKER_COUNT_KEY)
        share = measure_models.recommend_share(run.models, tasks=tasks.suggested, current=current)
        return [*found, share] if share is not None else found

    async def prices(self) -> dict[str, float]:
        """Seconds of one worker for one file, by family, from the models measured and kept."""
        kept = await self._rates.load(self._hardware.profile)
        return measure_models.prices(kept.models if kept is not None else self.models)

    async def measured(self) -> bool:
        """Whether this hardware has rates on file from an earlier run."""
        return await self._rates.load(self._hardware.profile) is not None

    async def rates(self) -> MachineRates | None:
        """This hardware's rates, or None where it has never been measured."""
        return await self._rates.load(self._hardware.profile)

    async def folders(self) -> dict[str, str]:
        """The library folders on each storage now, which outlive no measurement."""
        return {one.storage: one.label for one in await self._storages()}

    async def read_rates(self, path: Path) -> ReadRates | None:
        """This machine's rates for reading `path`, its share's own where measured; None if never."""
        rates = await self._rates.load(self._hardware.profile)
        if rates is None:
            return None
        where = await asyncio.to_thread(storage_of, path)
        return rates.for_reading(where.key if where.remote else None)

    async def measure(self) -> None:
        """Ask for a run, as a Build on a machine never measured does: queued, not awaited."""
        await self._ask(None)

    async def run(self) -> None:
        """Run the test and wait for it: the one in flight if there is one, a new one if not."""
        self.start()
        # Not shielded: a caller canceled cancels the run, and waits here while its tools end.
        if self._task is not None:  # pragma: no branch (the task is always held by here)
            await self._task

    async def _run(self) -> None:
        """The test, on its own task; shown and kept only once it ends, so a cancel keeps the last."""
        await self.recall()
        state = self.state
        current = await self._current()
        storages = await self._storages()
        found = Found(notes=[] if storages else [NO_FOLDER])
        workspace = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="sift-selftest-"))
        before = self._stalls()
        try:

            def reached(partial: Measurement) -> None:
                self.progress = partial

            measurement = await selftest.measure(
                workspace=workspace,
                settings=self._settings,
                cores=self._hardware.cpu_count,
                worst_lag=self._worst_lag,
                worst_wait=self._worst_wait,
                report=reached,
                storages=storages,
                fell_behind=self._fell_behind,
            )
            source = None if measurement.failed else await self._measure_more(workspace, found)
            advice = self.recommend(measurement, current=current, run=found)
            if measurement.failed is None:
                try:
                    if await self._measure_together(
                        measurement, advice, workspace, source, storages, current, found
                    ):
                        advice = self.recommend(measurement, current=current, run=found)
                except Exception:
                    # The combined run broke, not the run: the separate results stand.
                    await self._keep(measurement, found)
                    self._show(measurement, advice, found)
                    raise
                await self._keep(measurement, found)
            self._show(measurement, advice, found)
        finally:
            state.running = False
            self.progress = None
            self._count_caused(before)
            await asyncio.to_thread(_discard, workspace)

    def _count_caused(self, before: tuple[int, int]) -> None:
        loop, threads = self._stalls()
        self.caused = (self.caused[0] + loop - before[0], self.caused[1] + threads - before[1])

    async def lasted(self, kind: str) -> float | None:
        kept = await self._rates.load(self._hardware.profile)
        return None if kept is None else kept.lengths.get(kind)

    async def keep_length(self, kind: str, seconds: float) -> None:
        kept = await self._rates.load(self._hardware.profile)
        if kept is not None:
            await self._rates.save(replace(kept, lengths={**kept.lengths, kind: round(seconds, 1)}))

    async def _keep(self, measurement: Measurement, found: Found) -> None:
        kept = await self._rates.load(self._hardware.profile)
        rates = MachineRates.from_measurement(
            self._hardware.profile,
            measurement,
            now=now(),
            card=found.card,
            models=found.models,
            together=found.together,
            lengths=kept.lengths if kept is not None else None,
        )
        await self._rates.save(rates)
        log.info(
            "performance.selftest.rates_kept",
            profile=rates.profile,
            decode_fps=rates.decode_fps,
            seek_seconds=rates.seek_seconds,
            storages=len(rates.storages),
            together=rates.together is not None,
        )

    async def _measure_together(
        self,
        measurement: Measurement,
        advice: list[Recommendation],
        workspace: Path,
        source: Path | None,
        storages: Sequence[StorageToMeasure],
        current: dict[str, int],
        found: Found,
    ) -> bool:
        """The last step, the recommended numbers all running at the same time; whether it ran."""
        plan = measure_together.plan_for(
            advice,
            card=found.card,
            models=found.models,
            storages=measurement.storages,
            current=current,
        )
        if self._together is None or plan is None:
            return False
        machine = Machine(
            clip=workspace / selftest.CLIP_NAME,
            source=source,
            workspace=workspace,
            settings=self._settings,
            hardware=self._hardware,
            preview_command=self._preview,
            card=found.card,
        )
        found.together = await self._together(
            plan,
            machine,
            passes=await self._passes() if self._passes is not None else (),
            to_read=storages,
            readings=Readings(self._worst_lag, self._worst_wait, self._fell_behind),
        )
        found.notes.extend(found.together.said())
        return True

    async def _measure_more(self, workspace: Path, found: Found) -> Path | None:
        """The card's previews and each installed model, on one 1080p clip, which is handed back."""
        if self._preview is None and self._passes is None:
            return None
        source: Path | None = None
        try:
            source = await measure_encoder.build_source(workspace, self._settings)
        except (FFmpegError, OSError) as error:
            log.warning("performance.selftest.no_source", error=str(error))
        if self._preview is not None:
            found.card = await measure_encoder.measure_card(
                source=source,
                workspace=workspace,
                settings=self._settings,
                hardware=self._hardware,
                preview=self._preview,
                worst_lag=self._worst_lag,
                worst_wait=self._worst_wait,
                fell_behind=self._fell_behind,
            )
        if self._passes is not None:
            found.models = tuple(
                [
                    await measure_models.measure_pass(
                        one,
                        source=source,
                        seconds=measure_encoder.SOURCE_SECONDS,
                        settings=self._settings,
                    )
                    for one in await self._passes()
                ]
            )
        found.notes.extend(found.said())
        return source

    async def measure_storage(self, storage: str) -> StorageCurve | None:
        """Measure one storage and keep its number beside this hardware's rates, the rest kept.
        None where no library folder is on it any more."""
        one = next((each for each in await self._storages() if each.storage == storage), None)
        if one is None:
            return None
        state = self.state
        state.running = True
        before = self._stalls()
        try:
            curve = await selftest.measure_storage(one)
            kept = await self._rates.load(self._hardware.profile)
            earlier = kept.measurement if kept is not None else None
            if earlier is None:
                earlier = Measurement(cores=self._hardware.cpu_count)
            merged = replace(
                earlier,
                storages=(*(c for c in earlier.storages if c.storage != storage), curve),
            )
            found = Found()
            if kept is not None:
                found = Found(card=kept.card, models=kept.models, together=kept.together)
                found.notes = found.said()
            current = await self._current()
            self._show(merged, self.recommend(merged, current=current, run=found), found)
            best = curve.best
            # Never a first row: one holding only a share would make the device read as measured.
            if kept is not None and best is not None:
                await self._rates.save(
                    replace(
                        kept,
                        measured_at=now(),
                        storages={
                            **kept.storages,
                            storage: StorageRate(
                                at_once=best.at_once,
                                megabytes_per_second=best.megabytes_per_second,
                                seek_seconds=best.seconds_per_seek,
                            ),
                        },
                        measurement=merged,
                    )
                )
                log.info("performance.selftest.storage_kept", storage=storage, at_once=best.at_once)
            return curve
        finally:
            state.running = False
            self._count_caused(before)


def _discard(workspace: Path) -> None:
    """Remove the directory this run made under the system temp directory, and only that."""
    import shutil

    shutil.rmtree(  # nosemgrep: sift-no-file-removal-outside-delete-trash
        workspace, ignore_errors=True
    )


#: The runner, on the application: the screen's routes and the Build both reach it here.
SELF_TEST_RUNNER: Part[SelfTestRunner] = Part("self_test_runner")
