# SPDX-License-Identifier: AGPL-3.0-or-later
"""How background tools share the device, and the frames prepared for the task on this stack."""

from __future__ import annotations

import contextlib
import contextvars
import os
from collections.abc import Iterator

from sift.kernel import budget, hardware, subprocess
from sift.kernel.config import Settings
from sift.kernel.media_frames import PreparedFrames

#: The most memory ffmpeg may allocate for one buffer, against a malformed file's huge ask.
MAX_ALLOC_BYTES = str(1 << 30)

#: Prepended to every invocation; without `-nostdin` ffmpeg reads the server's own input.
BASE_FLAGS: tuple[str, ...] = (
    "-hide_banner",
    "-loglevel",
    "error",
    "-nostdin",
    "-y",
    "-max_alloc",
    MAX_ALLOC_BYTES,
)


#: How many jobs the pool really runs, as the settings say; None until the pool is configured.
_jobs_at_once: int | None = None


def set_jobs_at_once(workers: int) -> bool:
    """Record how many jobs really run together. True when the number actually changed."""
    global _jobs_at_once
    if workers < 1 or workers == _jobs_at_once:
        return False
    _jobs_at_once = workers
    return True


def jobs_at_once(settings: Settings) -> int:
    """How many jobs run together: what the pool was told, or the hardware answer before it was."""
    if _jobs_at_once is not None:
        return _jobs_at_once
    return max(1, hardware.worker_concurrency(settings))


#: The workers running and the percent of the device they share, or None for the whole device.
_share: tuple[int, int] | None = None


def set_share(*, running: int, percent: int) -> bool:
    """Record the share in force: `running` workers sharing `percent` of the device.

    Below the whole device, each background tool is also held to its threads by the system.
    True when the share changed.
    """
    global _share
    share = (max(1, running), min(budget.WHOLE_DEVICE, max(1, percent)))
    if share == _share:
        return False
    _share = share
    cores = os.cpu_count() or 1
    threads = budget.tool_threads(cores, running=share[0], percent=share[1])
    held = share[1] < budget.WHOLE_DEVICE
    subprocess.hold_background(budget.processor_rate(threads, cores) if held else None)
    return True


#: Background tools running now through this module, and whether this task is one of them.
_tools_running = 0
_COUNTED: contextvars.ContextVar[bool] = contextvars.ContextVar("tool_counted", default=False)


@contextlib.contextmanager
def _running_tool() -> Iterator[None]:
    """Count a tool, or a run of them, while it runs, so the next one sizes itself by it."""
    global _tools_running
    if _COUNTED.get():
        yield
        return
    token = _COUNTED.set(True)
    _tools_running += 1
    try:
        yield
    finally:
        _tools_running -= 1
        _COUNTED.reset(token)


def tools_sharing(settings: Settings) -> int:
    """How many tools share the device with the one about to start, itself included.

    On the whole device, the tools actually running: a file read alone gets the machine rather
    than one worker's share of it. Stepped back, the share in force, which the processor rate
    each tool is held to was set from.
    """
    if _share is None:
        running, percent = jobs_at_once(settings), budget.WHOLE_DEVICE
    else:
        running, percent = _share
    if percent < budget.WHOLE_DEVICE:
        return running
    others = _tools_running - (1 if _COUNTED.get() else 0)
    return max(1, min(running, others + 1))


def background_threads(settings: Settings) -> int:
    """How many threads one background ffmpeg may use: its share of the share in force.

    Left alone ffmpeg sizes its pool from the cores, per process, and the app queues behind them.
    """
    cores = os.cpu_count() or 1
    percent = budget.WHOLE_DEVICE if _share is None else _share[1]
    return budget.tool_threads(cores, running=tools_sharing(settings), percent=percent)


def background_flags(settings: Settings) -> tuple[str, ...]:
    """`BASE_FLAGS` plus a thread cap on decoding and the filter graph; an encoder needs its own."""
    share = str(background_threads(settings))
    return (*BASE_FLAGS, "-threads", share, "-filter_threads", share)


#: The frames prepared for the task on this stack, unknown to the consumers three features deep.
_PREPARED: contextvars.ContextVar[PreparedFrames | None] = contextvars.ContextVar(
    "prepared_frames", default=None
)


def prepared_now() -> PreparedFrames | None:
    """The frames prepared for the task on this stack, if any."""
    return _PREPARED.get()


@contextlib.contextmanager
def prepared(frames: PreparedFrames) -> Iterator[None]:
    """Hand these frames to every `raw_moments` and `moments_to_files` call inside."""
    token = _PREPARED.set(frames)
    try:
        yield
    finally:
        _PREPARED.reset(token)
