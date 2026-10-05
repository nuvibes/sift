# SPDX-License-Identifier: AGPL-3.0-or-later
"""Each installed model, timed through the feature's own model process on its set device, one file
of decoded frames at a time and then several. Nothing is downloaded."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import math
import os
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from sift.kernel import media, sampling
from sift.kernel.config import Settings
from sift.kernel.log import get_logger
from sift.kernel.ml.runtime import DeviceUnavailable, Loaded
from sift.kernel.ml.weights import Weight, WeightError
from sift.slices.performance.budget import MODELS, Deadline
from sift.slices.performance.selftest import (
    BUSY,
    NEAR_BEST,
    REPEATS,
    Recommendation,
    middle_by,
    near_best,
    others_busy,
    steady,
    sureness,
)

log = get_logger(__name__)

#: How many files at once each pass is timed at.
MODEL_LEVELS = (1, 2, 4)

#: Recognition's "Share of this device to use", the one model setting the benchmark advises.
RECOGNITION_SHARE_KEY = "faces.core_share"
SHARE_FLOOR = 10
SHARE_DEFAULT = 50
SHARE_CEILING = 100

_WINDOWS = sys.platform == "win32"

NOT_INSTALLED = "not installed"
NO_SOURCE = "the 1080p test clip couldn't be made"


class _Runs(Protocol):
    def load(self, weight: Weight) -> Loaded: ...
    def run(
        self, loaded: Loaded, blob: np.ndarray, *, outputs: Sequence[str] | None = None
    ) -> list[np.ndarray]: ...
    def unload(self) -> None: ...


@dataclass(frozen=True)
class Ask:
    """One model run a file makes: the model, the shape of what it is handed, and how often."""

    weight: Weight
    shape: tuple[int, ...]
    times: int = 1


@dataclass(frozen=True)
class ModelPass:
    """One pass that runs a model, as the composition root describes it."""

    name: str
    family: str
    """The family of jobs whose estimate this prices."""
    device: str
    installed: Callable[[Weight], bool]
    runner: Callable[[], _Runs]
    """The feature's own model process on `device`; ended when the pass is measured."""
    asks: tuple[Ask, ...]
    picture: tuple[int, int]
    """The width and height each frame is decoded at."""
    moments: int = sampling.MAX_FRAMES
    share_key: str | None = None


@dataclass(frozen=True)
class ModelLevel:
    """One width: how many files at once, and what came of them."""

    at_once: int
    seconds: float
    finished: int
    failed: int = 0
    low: float | None = None
    high: float | None = None
    busy: bool = False
    memory_bytes: int | None = None
    """How far the device's free memory fell while it ran; None where it cannot be read."""
    card_memory_bytes: int | None = None

    @property
    def files_per_second(self) -> float:
        return self.finished / self.seconds if self.seconds > 0 else 0.0


@dataclass(frozen=True)
class ModelCurve:
    """How one pass's models did as more files went through them at once."""

    name: str
    family: str
    device: str
    levels: tuple[ModelLevel, ...] = ()
    failed: str | None = None
    share_key: str | None = None
    memory_bytes: int | None = None
    """What loading the models and a first file took of the device's memory, and the card's."""
    card_memory_bytes: int | None = None

    @property
    def best(self) -> ModelLevel | None:
        whole = [one for one in self.levels if one.finished and not one.failed]
        return near_best(whole, key=_per_second)

    @property
    def still_gaining(self) -> bool:
        """Whether the widest width tried was the answer, so wider might do better still."""
        best = self.best
        return best is not None and len(self.levels) > 1 and best is self.levels[-1]

    @property
    def seconds_per_file(self) -> float | None:
        """Worker seconds one file costs at the chosen width: what an estimate starts from."""
        best = self.best
        return None if best is None else best.seconds * best.at_once / best.finished

    def said(self) -> list[str]:
        """What was not measured here, or measured while other programs were busy."""
        if self.failed is not None:
            return [f"{self.name} wasn't measured: {self.failed}."]
        said = [
            f"{self.name} at {one.at_once} at the same time: {one.failed} of its files failed, so "
            f"it wasn't counted."
            for one in self.levels
            if one.failed
        ]
        busy = [str(one.at_once) for one in self.levels if one.busy]
        if busy:
            said.append(f"{self.name} at {', '.join(busy)} at the same time was {BUSY}.")
        if self.share_key and self.still_gaining:
            said.append(
                f"{self.name} was still getting quicker at {self.levels[-1].at_once} at the same "
                f"time, the most this run tries, so no share is suggested for it."
            )
        return said


def _per_second(level: ModelLevel) -> float:
    return level.files_per_second


def _marked(level: ModelLevel) -> ModelLevel:
    return replace(level, busy=True)


# --- memory ---------------------------------------------------------------------------------------


def free_memory() -> int | None:
    """The device's free memory in bytes, or None where it cannot be read."""
    return _windows_free() if _WINDOWS else _posix_free()


def _posix_free() -> int | None:  # pragma: no cover (the other system's branch)
    sysconf: Any = getattr(os, "sysconf", None)
    if sysconf is None or "SC_AVPHYS_PAGES" not in getattr(os, "sysconf_names", {}):
        return None
    return int(sysconf("SC_AVPHYS_PAGES")) * int(sysconf("SC_PAGE_SIZE"))


def _windows_free() -> int | None:
    class Status(ctypes.Structure):
        _fields_ = (
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        )

    status = Status()
    status.dwLength = ctypes.sizeof(Status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.ullAvailPhys)


def card_used() -> int | None:
    """The GPU's memory in use, from NVML as the hardware probe loads it; Windows only."""
    if not _WINDOWS:  # pragma: no cover (the other system's branch)
        return None
    try:
        nvml: Any = ctypes.CDLL("nvml.dll")
    except OSError:
        return None
    return _nvml_used(nvml)


def _nvml_used(nvml: Any) -> int | None:
    """The first card's memory in use, through an NVML handle; None on any refusal."""

    class Memory(ctypes.Structure):
        _fields_ = (
            ("total", ctypes.c_ulonglong),
            ("free", ctypes.c_ulonglong),
            ("used", ctypes.c_ulonglong),
        )

    if nvml.nvmlInit_v2() != 0:
        return None
    try:
        handle = ctypes.c_void_p()
        memory = Memory()
        if nvml.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)) != 0:
            return None
        if nvml.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(memory)) != 0:
            return None
        return int(memory.used)
    finally:
        nvml.nvmlShutdown()


class Watch(Protocol):
    def start(self) -> None: ...
    def stop(self) -> tuple[int | None, int | None]: ...


class MemoryWatch:
    """How far free memory fell, and the card's use rose, between `start` and `stop`."""

    EVERY_SECONDS = 0.1

    def __init__(
        self,
        free: Callable[[], int | None] = free_memory,
        card: Callable[[], int | None] = card_used,
    ) -> None:
        self._free = free
        self._card = card
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self._free_before: int | None = None
        self._card_before: int | None = None
        self._lowest: int | None = None
        self._highest: int | None = None

    def start(self) -> None:
        self._free_before = self._lowest = self._free()
        self._card_before = self._highest = self._card()
        self._thread = threading.Thread(target=self._sample, name="benchmark-memory", daemon=True)
        self._thread.start()

    def _sample(self) -> None:
        while not self._done.wait(self.EVERY_SECONDS):
            self._read()

    def _read(self) -> None:
        free, card = self._free(), self._card()
        if free is not None and self._lowest is not None:
            self._lowest = min(self._lowest, free)
        if card is not None and self._highest is not None:
            self._highest = max(self._highest, card)

    def stop(self) -> tuple[int | None, int | None]:
        """The fall in free memory and the rise in the card's, each None where unread."""
        self._done.set()
        if self._thread is not None:
            self._thread.join()
        self._read()
        memory = card = None
        if self._free_before is not None and self._lowest is not None:
            memory = max(0, self._free_before - self._lowest)
        if self._card_before is not None and self._highest is not None:
            card = max(0, self._highest - self._card_before)
        return memory, card


# --- one file -------------------------------------------------------------------------------------


def moments_of(seconds: int, count: int) -> list[media.Moment]:
    """`count` moments spread over a clip of `seconds`, the first at its start."""
    every = seconds * 1000 / max(1, count)
    ats = [round(index * every) for index in range(count)]
    return [media.Moment(seek=() if at <= 0 else ("-ss", media.seconds(at))) for at in ats]


@dataclass(frozen=True)
class _Ready:
    """A pass's models loaded and its inputs made, so a level times only the work."""

    runner: _Runs
    loaded: tuple[Loaded, ...]
    blobs: tuple[np.ndarray, ...]


def _load(one: ModelPass) -> _Ready:
    """Start the feature's model process and load each model in it. Blocking."""
    runner = one.runner()
    loaded = tuple(runner.load(ask.weight) for ask in one.asks)
    numbers = np.random.default_rng(0)
    blobs = tuple(numbers.random(ask.shape, dtype=np.float32) for ask in one.asks)
    return _Ready(runner=runner, loaded=loaded, blobs=blobs)


async def loaded(one: ModelPass, load: Callable[[ModelPass], _Ready] = _load) -> _Ready:
    """`load` on a thread; a cancel ends the model process once it's up."""
    loads = asyncio.ensure_future(asyncio.to_thread(load, one))
    try:
        return await asyncio.shield(loads)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            ready = await loads
            await asyncio.to_thread(ready.runner.unload)
        raise


def _ask(ready: _Ready, one: ModelPass) -> None:
    for ask, loaded, blob in zip(one.asks, ready.loaded, ready.blobs, strict=True):
        for _ in range(ask.times):
            ready.runner.run(loaded, blob)


async def one_file(
    ready: _Ready, one: ModelPass, *, source: Path, seconds: int, settings: Settings
) -> bool:
    """Decode a file's frames and make its model runs. True when both finished."""
    width, height = one.picture
    try:
        frames = await media.raw_moments(
            source,
            moments_of(seconds, one.moments),
            filters=f"scale={width}:{height}",
            pixel_format="rgb24",
            frame_bytes=width * height * 3,
            settings=settings,
            time_limit=120.0,
        )
        if any(frame is None for frame in frames):
            return False
        await asyncio.to_thread(_ask, ready, one)
    except (media.FFmpegError, DeviceUnavailable, WeightError, RuntimeError, OSError) as error:
        log.info("performance.models.file_failed", model=one.name, error=str(error))
        return False
    return True


# --- the ladder -----------------------------------------------------------------------------------


def _level(
    at_once: int, took: float, done: Sequence[bool], held: int | None, card: int | None
) -> ModelLevel:
    finished = sum(1 for ok in done if ok)
    return ModelLevel(
        at_once=at_once,
        seconds=took,
        finished=finished,
        failed=at_once - finished,
        memory_bytes=held,
        card_memory_bytes=card,
    )


async def measure_pass(
    one: ModelPass,
    *,
    source: Path | None,
    seconds: int,
    settings: Settings,
    levels: Sequence[int] = MODEL_LEVELS,
    repeats: int = REPEATS,
    busy: Callable[[], bool] = others_busy,
    watch: Callable[[], Watch] = MemoryWatch,
    load: Callable[[ModelPass], _Ready] = _load,
    file: Callable[..., Awaitable[bool]] = one_file,
    deadline: Deadline | None = None,
) -> ModelCurve:
    """Time one pass at each width. Never raises: what could not be measured is the answer."""
    curve = ModelCurve(name=one.name, family=one.family, device=one.device, share_key=one.share_key)
    if not all(one.installed(ask.weight) for ask in one.asks):
        return replace(curve, failed=NOT_INSTALLED)
    if source is None:
        return replace(curve, failed=NO_SOURCE)
    loading = watch()
    loading.start()
    try:
        ready = await loaded(one, load)
    except (DeviceUnavailable, WeightError, RuntimeError, OSError) as error:
        loading.stop()
        return replace(curve, failed=str(error).rstrip("."))
    except BaseException:
        loading.stop()
        raise
    try:
        # Untimed: the first run opens the device.
        await file(ready, one, source=source, seconds=seconds, settings=settings)
        held, card = loading.stop()
        curve = replace(curve, memory_bytes=held, card_memory_bytes=card)

        async def run(at_once: int) -> ModelLevel:
            memory = watch()
            memory.start()
            started = time.monotonic()
            try:
                done = await asyncio.gather(
                    *(
                        file(ready, one, source=source, seconds=seconds, settings=settings)
                        for _ in range(at_once)
                    )
                )
            finally:
                held, card = memory.stop()
            return _level(at_once, time.monotonic() - started, done, held, card)

        async def take(at_once: int) -> ModelLevel:
            runs = [await run(at_once) for _ in range(repeats)]
            figures = [_per_second(each) for each in runs]
            kept = replace(middle_by(runs, key=_per_second), low=min(figures), high=max(figures))
            log.info(
                "performance.models.level",
                model=one.name,
                at_once=at_once,
                files_per_second=round(kept.files_per_second, 3),
            )
            return kept

        done: list[ModelLevel] = []
        for at_once in levels:
            level = await (deadline or Deadline(MODELS)).within(
                steady(partial(take, at_once), _marked, busy)
            )
            if level is None:
                break
            done.append(level)
        return replace(curve, levels=tuple(done))
    finally:
        await asyncio.to_thread(ready.runner.unload)


# --- what it advises ------------------------------------------------------------------------------


def recommend_share(
    curves: Sequence[ModelCurve], *, tasks: int, current: dict[str, int]
) -> Recommendation | None:
    """The share of `tasks` giving the files at once recognition did best at; None where unadvised."""
    found = next(
        (one for one in curves if one.share_key and one.best and not one.still_gaining), None
    )
    if found is None or found.share_key is None or found.best is None or tasks < 1:
        return None
    best = found.best
    share = min(SHARE_CEILING, max(SHARE_FLOOR, math.ceil(best.at_once * 100 / tasks)))
    scans = max(1, tasks * share // 100)
    why = f"checking more at the same time added under {round((1 - NEAR_BEST) * 100)}%"
    sure = sureness(best.low, best.high, best.files_per_second, "files a second")
    return Recommendation(
        key=found.share_key,
        label="Share of this device recognition uses (%)",
        current=current.get(found.share_key, share),
        suggested=share,
        reason=(
            f"Measured: {found.name} checked {best.files_per_second:.2g} files a second at "
            f"{best.at_once} at the same time on its device, and {why}. With {tasks} tasks at the same time, "
            f"{share}% of them is {scans}.{sure}"
        ),
    )


def prices(curves: Sequence[ModelCurve]) -> dict[str, float]:
    """Seconds of one worker for one file, summed by family: a pass's price before any history."""
    summed: dict[str, float] = {}
    for one in curves:
        each = one.seconds_per_file
        if each is not None:
            summed[one.family] = summed.get(one.family, 0.0) + each
    return summed
