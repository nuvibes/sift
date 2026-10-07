# SPDX-License-Identifier: AGPL-3.0-or-later
"""The recommended numbers run together for half a minute, stepped down until Sift keeps up: each
ladder measured one thing alone, but they all share one device."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Protocol

from sift.kernel import media
from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.kernel.log import get_logger
from sift.kernel.media import Encoder
from sift.kernel.ml.runtime import DeviceUnavailable
from sift.kernel.ml.weights import WeightError
from sift.slices.performance import measure_encoder, measure_models, selftest
from sift.slices.performance.measure_encoder import CardCurve, PreviewCommand
from sift.slices.performance.measure_models import (
    RECOGNITION_SHARE_KEY,
    SHARE_DEFAULT,
    ModelCurve,
    ModelPass,
)
from sift.slices.performance.selftest import (
    BUSY,
    GENERATION_LIMIT_KEY,
    THREADS_PER_ENCODE,
    TOO_BUSY_SECONDS,
    WORKER_COUNT_KEY,
    Recommendation,
    StorageCurve,
    StorageToMeasure,
    others_busy,
    storage_name,
)

log = get_logger(__name__)

#: How long everything runs together in all, and how often it is judged.
TOGETHER_SECONDS = 30.0
WINDOW_SECONDS = 15.0


@dataclass(frozen=True)
class Reads:
    """One storage read at its measured number, on top of the tasks as the lanes are."""

    storage: str
    label: str
    at_once: int


@dataclass(frozen=True)
class Plan:
    """What runs at the same time: the tasks, the previews among them, each model's files."""

    tasks: int
    previews: int
    on_card: bool
    widths: tuple[tuple[str, int], ...] = ()
    """Each model and the most files it takes at the same time, fitted into the tasks in order."""
    reads: tuple[Reads, ...] = ()

    @property
    def models(self) -> tuple[tuple[str, int], ...]:
        left = self.tasks - self.previews
        fitted = []
        for name, wanted in self.widths:
            took = min(wanted, left)
            left -= took
            if took:
                fitted.append((name, took))
        return tuple(fitted)

    @property
    def encodes(self) -> int:
        """The tasks left over, each an encode as the CPU ladder ran them."""
        return self.tasks - self.previews - sum(n for _, n in self.models)

    def stepped(self) -> Plan | None:
        """A quarter fewer tasks and previews; None where one task is all there is."""
        if self.tasks <= 1:
            return None
        tasks = self.tasks - max(1, self.tasks // 4)
        previews = self.previews - max(1, self.previews // 4) if self.previews > 1 else 1
        return replace(self, tasks=tasks, previews=min(previews, tasks))

    def described(self) -> str:
        what = [f"{self.previews} previews{' on the GPU' if self.on_card else ''}"]
        what += [f"{name} {n} {'file' if n == 1 else 'files'}" for name, n in self.models]
        if self.encodes:
            what.append(f"{self.encodes} encodes")
        said = f"{self.tasks} tasks at the same time ({_listed(what)})"
        reads = [f"{storage_name(one.storage)} at {one.at_once}" for one in self.reads]
        return f"{said}, with reads from {_listed(reads)}" if reads else said


def _listed(parts: Sequence[str]) -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def _times(count: int) -> str:
    return "once" if count == 1 else f"{count} times"


@dataclass(frozen=True)
class Window:
    """One stretch of the plan, and how Sift and the work fared in it."""

    plan: Plan
    seconds: float
    worst_lag_seconds: float = 0.0
    worst_wait_seconds: float = 0.0
    fell_behind: int = 0
    """How many times the loop was held, or work waited for a thread, past the quarter second."""
    done: int = 0
    failed: int = 0
    megabytes: float = 0.0
    busy: bool = False

    @property
    def kept_up(self) -> bool:
        worst = max(self.worst_lag_seconds, self.worst_wait_seconds)
        return not self.failed and not self.fell_behind and worst < TOO_BUSY_SECONDS

    def why(self) -> str:
        if self.failed:
            return f"{self.failed} of the tasks failed"
        if self.worst_lag_seconds >= TOO_BUSY_SECONDS:
            return f"Sift stopped responding for {self.worst_lag_seconds:.2f} s"
        if self.worst_wait_seconds >= TOO_BUSY_SECONDS:
            return f"work waited {self.worst_wait_seconds:.2f} s for a thread"
        return f"Sift fell behind {_times(self.fell_behind)}"


@dataclass(frozen=True)
class Together:
    """Every window run, the plan stepped down after each one that fell behind."""

    windows: tuple[Window, ...] = ()
    failed: str | None = None
    seconds: float = 0.0
    """From the first slot started to the last unit finished."""

    @property
    def settled(self) -> Plan | None:
        """The plan the run ended on: the last window's, stepped down where it fell behind."""
        if not self.windows:
            return None
        last = self.windows[-1]
        return last.plan if last.kept_up else last.plan.stepped() or last.plan

    def said(self) -> list[str]:
        if self.failed is not None:
            return [f"The recommended numbers weren't run together: {self.failed}."]
        settled = self.settled
        if settled is None:
            return []
        first = self.windows[0].plan
        said = [
            f"Everything recommended ran together for {round(self.seconds)} s: {first.described()}."
        ]
        behind = next((one for one in self.windows if not one.kept_up), None)
        if behind is None:
            said.append("Sift kept up, so nothing was stepped down.")
        elif settled.tasks == behind.plan.tasks:
            said.append(f"Even at {settled.tasks} task, {behind.why()}.")
        else:
            again = "where it kept up" if self.windows[-1].kept_up else "which wasn't run again"
            said.append(f"At {behind.plan.tasks} tasks, {behind.why()}.")
            said.append(
                f"So tasks were stepped down to {settled.tasks} and previews to "
                f"{settled.previews}, {again}."
            )
        if any(one.busy for one in self.windows):
            said.append(f"Part of it was {BUSY}.")
        return said

    def applied(self, found: Sequence[Recommendation]) -> list[Recommendation]:
        """The advice held to what kept up together, saying so where it moved."""
        settled = self.settled
        if settled is None or all(one.kept_up for one in self.windows):
            return list(found)
        caps = {WORKER_COUNT_KEY: settled.tasks, GENERATION_LIMIT_KEY: settled.previews}
        moved = []
        for one in found:
            cap = caps.get(one.key)
            if cap is not None and cap < one.suggested:
                why = (
                    f" Run together with everything else recommended, Sift fell behind at "
                    f"{one.suggested}, so this is {cap}."
                )
                one = replace(one, suggested=cap, reason=one.reason + why)
            moved.append(one)
        return moved


def plan_for(
    found: Sequence[Recommendation],
    *,
    card: CardCurve | None,
    models: Sequence[ModelCurve],
    storages: Sequence[StorageCurve],
    current: Mapping[str, int],
) -> Plan | None:
    """The advice as it would run: None where there is no task count to run."""
    advice = {one.key: one.suggested for one in found}
    tasks = advice.get(WORKER_COUNT_KEY)
    if tasks is None:
        return None
    share = advice.get(RECOGNITION_SHARE_KEY) or current.get(RECOGNITION_SHARE_KEY) or SHARE_DEFAULT
    widths = []
    for one in models:
        top = one.best
        if top is not None:
            cap = max(1, tasks * share // 100) if one.share_key else top.at_once
            widths.append((one.name, min(top.at_once, cap)))
    return Plan(
        tasks=tasks,
        previews=min(tasks, advice.get(GENERATION_LIMIT_KEY, 1)),
        on_card=card is not None and card.best is not None,
        widths=tuple(widths),
        reads=tuple(
            Reads(storage=one.storage, label=one.label, at_once=best.at_once)
            for one in storages
            if (best := one.best) is not None
        ),
    )


# --- running it -----------------------------------------------------------------------------------


class Work(Protocol):
    """One unit of each kind of work; True or the bytes read when it finished."""

    async def preview(self, index: int) -> bool: ...
    async def encode(self, index: int) -> bool: ...
    async def model(self, name: str, index: int) -> bool: ...
    async def read(self, storage: str) -> int: ...


@dataclass(frozen=True)
class Readings:
    """The server's readings the ladders are judged by, and its count of falling behind."""

    worst_lag: Callable[[], float]
    worst_wait: Callable[[], float]
    fell_behind: Callable[[], int] = lambda: 0


@dataclass
class _Run:
    """What the slots share while the plan runs."""

    current: Plan
    done: int = 0
    failed: int = 0
    read: int = 0
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    turned: asyncio.Event = field(default_factory=asyncio.Event)

    def counts(self) -> tuple[int, int, int]:
        return self.done, self.failed, self.read

    def turn(self) -> None:
        self.turned.set()
        self.turned = asyncio.Event()


async def _job(
    run: _Run, holds: Callable[[Plan], bool], unit: Callable[[], Awaitable[bool]]
) -> None:
    while not run.stop.is_set() and holds(run.current):
        if await unit():
            run.done += 1
            continue
        run.failed += 1
        # Tried again in the next window, if the plan still holds it.
        await run.turned.wait()


async def _reader(run: _Run, work: Work, storage: str) -> None:
    while not run.stop.is_set():
        got = await work.read(storage)
        if got <= 0:
            return
        run.read += got


def _slots(plan: Plan, work: Work, run: _Run) -> list[Awaitable[None]]:
    return [
        *(
            _job(run, partial(_holds_previews, i), partial(work.preview, i))
            for i in range(plan.previews)
        ),
        *(
            _job(run, partial(_holds_encodes, i), partial(work.encode, i))
            for i in range(plan.encodes)
        ),
        *(
            _job(run, partial(_holds_model, name, i), partial(work.model, name, i))
            for name, width in plan.models
            for i in range(width)
        ),
        *(_reader(run, work, one.storage) for one in plan.reads for _ in range(one.at_once)),
    ]


async def _window(
    run: _Run,
    readings: Readings,
    *,
    window: float,
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
) -> Window:
    at, before = clock(), run.counts()
    lag, wait, behind = readings.worst_lag(), readings.worst_wait(), readings.fell_behind()
    await sleep(window)
    done, failed, read = (now - then for now, then in zip(run.counts(), before, strict=True))
    one = Window(
        plan=run.current,
        seconds=clock() - at,
        worst_lag_seconds=max(0.0, readings.worst_lag() - lag),
        worst_wait_seconds=max(0.0, readings.worst_wait() - wait),
        fell_behind=readings.fell_behind() - behind,
        done=done,
        failed=failed,
        megabytes=read / 1_000_000,
    )
    _logged(one)
    run.turn()
    return one


async def measure(
    plan: Plan,
    work: Work,
    *,
    readings: Readings,
    busy: Callable[[], bool] = others_busy,
    seconds: float = TOGETHER_SECONDS,
    window: float = WINDOW_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Together:
    """The plan's work kept going for `seconds`, judged every `window`; a window behind steps it
    down, after one more where other programs were busy. A cancel ends every slot immediately."""
    run = _Run(current=plan)
    running = asyncio.gather(*_slots(plan, work, run))
    windows: list[Window] = []
    started = clock()
    given = False
    try:
        while clock() - started < seconds:
            one = await _window(run, readings, window=window, clock=clock, sleep=sleep)
            if not one.kept_up and not given and busy():
                given = True
                continue
            windows.append(replace(one, busy=given and busy()))
            given = False
            if not one.kept_up:
                lower = run.current.stepped()
                if lower is None:
                    break
                run.current = lower
    except BaseException:
        running.cancel()
        await asyncio.wait({running})
        raise
    run.stop.set()
    run.turned.set()
    await running
    return Together(windows=tuple(windows), seconds=clock() - started)


def _holds_previews(index: int, plan: Plan) -> bool:
    return index < plan.previews


def _holds_encodes(index: int, plan: Plan) -> bool:
    return index < plan.encodes


def _holds_model(name: str, index: int, plan: Plan) -> bool:
    return index < dict(plan.models).get(name, 0)


def _logged(one: Window) -> None:
    log.info(
        "performance.together.window",
        tasks=one.plan.tasks,
        previews=one.plan.previews,
        seconds=round(one.seconds, 1),
        done=one.done,
        failed=one.failed,
        megabytes=round(one.megabytes, 1),
        worst_lag=round(one.worst_lag_seconds, 3),
        worst_wait=round(one.worst_wait_seconds, 3),
        fell_behind=one.fell_behind,
    )


@dataclass
class Machine:
    """The work as Sift does it: the preview's own command, the CPU encode, each model's own
    process, and reads of each storage's largest files."""

    clip: Path
    source: Path | None
    workspace: Path
    settings: Settings
    hardware: HardwareReport
    preview_command: PreviewCommand | None
    card: CardCurve | None
    ready: dict[str, tuple[measure_models._Ready, ModelPass]] = field(default_factory=dict)
    samples: dict[str, tuple[list[Path], bool, itertools.count[int]]] = field(default_factory=dict)

    async def preview(self, index: int) -> bool:
        card = self.card
        if card is None or self.preview_command is None or self.source is None:
            return await self.encode(10_000 + index)
        encoder = Encoder(card.encoder)
        decode = media.decode_flags(self.hardware) if card.decodes_on_card else ()
        return await measure_encoder._encode(
            self.preview_command,
            self.source,
            self.workspace,
            index,
            encoder=encoder,
            decode=decode,
            settings=self.settings,
        )

    async def encode(self, index: int) -> bool:
        return await selftest._encode_once(
            self.clip, self.workspace, index, self.settings, THREADS_PER_ENCODE
        )

    async def model(self, name: str, index: int) -> bool:
        ready, one = self.ready[name]
        if self.source is None:
            return False
        return await measure_models.one_file(
            ready,
            one,
            source=self.source,
            seconds=measure_encoder.SOURCE_SECONDS,
            settings=self.settings,
        )

    async def read(self, storage: str) -> int:
        files, remote, turns = self.samples[storage]
        turn = next(turns)
        path = files[turn % len(files)]
        try:
            return await asyncio.to_thread(
                selftest._seek_and_read, path, salt=turn // len(files), uncached=not remote
            )
        except OSError as error:
            log.info("performance.together.read_failed", detail=str(error))
            return 0

    async def open(
        self, plan: Plan, passes: Sequence[ModelPass], to_read: Sequence[StorageToMeasure]
    ) -> None:
        """Load each model the plan runs and find each storage's files, before any window."""
        named = {one.name: one for one in passes}
        for name, _ in plan.models if self.source is not None else ():
            if name in named:
                self.ready[name] = (await measure_models.loaded(named[name]), named[name])
        for one in to_read:
            files = await asyncio.to_thread(selftest.sample_files, list(one.roots))
            if files:
                self.samples[one.storage] = (files, one.remote, itertools.count())

    async def close(self) -> None:
        for ready, _ in self.ready.values():
            with contextlib.suppress(RuntimeError, OSError):
                await asyncio.to_thread(ready.runner.unload)
        self.ready.clear()


async def run(
    plan: Plan,
    machine: Machine,
    *,
    passes: Sequence[ModelPass],
    to_read: Sequence[StorageToMeasure],
    readings: Readings,
) -> Together:
    """Load what the plan runs, run it together, and end every model process after."""
    read = {one.storage for one in plan.reads}
    try:
        await machine.open(plan, passes, [one for one in to_read if one.storage in read])
    except (DeviceUnavailable, WeightError, RuntimeError, OSError) as error:
        await machine.close()
        return Together(failed=str(error).rstrip("."))
    except BaseException:
        await machine.close()
        raise
    plan = replace(
        plan,
        widths=tuple(one for one in plan.widths if one[0] in machine.ready),
        reads=tuple(one for one in plan.reads if one.storage in machine.samples),
    )
    try:
        return await measure(plan, machine, readings=readings)
    finally:
        await machine.close()
