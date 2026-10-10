# SPDX-License-Identifier: AGPL-3.0-or-later
"""Running an external tool: a list never a shell, bounded in time and machine, always reaped.

The one spawn point, so a cancelled job never leaves an orphan tool running. A non-zero exit is the
caller's to interpret, and nothing here logs: a tool's output can carry a URL or a path.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import weakref
from collections.abc import AsyncGenerator, Callable, Mapping
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from typing import IO, Any

from sift.kernel.long_lived import LongLivedChild as LongLivedChild
from sift.kernel.long_lived import start_long_lived as start_long_lived
from sift.kernel.subprocess_jobs import (
    _EXTENDED_LIMIT_INFORMATION,
    _OVER_MEMORY,
    _job_ids,
    _job_time,
    _kill_on_close_limits,
    _MemoryWatch,
    _over_memory,
    _set_rate,
)

_READ_CHUNK = 1 << 16  # 64 KiB per read

# Past the cap output is read and thrown away, so the tool never blocks on a full pipe.
_MAX_OUTPUT_BYTES = 16 * 1024 * 1024


class SubprocessError(Exception):
    """A tool could not be started, or did not finish within its time budget."""


class ToolFailed(SubprocessError):
    """A tool ended with a non-zero status; `said` is the tail of what it wrote to its errors."""

    def __init__(self, message: str, *, returncode: int, said: str) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.said = said


#: How much of a failed tool's error output its failure keeps, from the end. A decoder's cause comes
#: first and its consequences after, so this holds the whole of an ordinary refusal.
SAID_TAIL_CHARS = 2000


class TookTooLong(SubprocessError):
    """A tool ran past its time limit and was stopped: a hung read more often than broken bytes."""


def said_tail(stderr: bytes | None) -> str:
    """The last `SAID_TAIL_CHARS` of what a tool wrote to its errors, as text."""
    return (stderr or b"").decode("utf-8", "replace").strip()[-SAID_TAIL_CHARS:]


#: The statuses Windows ends a program with when it could not be started at all: a file it needs
#: in use, a library missing, not a program for this machine, a library that would not initialise.
_NEVER_STARTED = frozenset({0xC0000043, 0xC0000135, 0xC000007B, 0xC0000142})

#: What a caller says of such a tool in place of the words it never wrote.
NEVER_STARTED = "it couldn't start: a file it needs is in use or missing"


def could_not_start(returncode: int) -> bool:
    """Whether this exit status is Windows saying the program never started."""
    return returncode & 0xFFFFFFFF in _NEVER_STARTED


def unsaid(returncode: int) -> str:
    """What to say of a tool that ended badly and wrote nothing."""
    return NEVER_STARTED if could_not_start(returncode) else "no detail"


@dataclass(frozen=True, slots=True)
class SubprocessResult:
    """What a finished tool left behind: its exit status and the two streams, as raw bytes."""

    returncode: int
    stdout: bytes
    stderr: bytes


class Priority(StrEnum):
    """How much of the machine a spawned tool may take: whether anybody is waiting for it.

    On Windows a background child is below normal, not idle, which a busy foreground starves.
    """

    NORMAL = "normal"
    """Somebody is waiting for this. Playback, downloads, and anything on a request path."""

    BACKGROUND = "background"
    """Generated work nobody asked for yet. It yields the processor and the disk to everything else
    and takes as long as it takes, which for a thumbnail nobody has scrolled to is free."""


#: How far below normal a background child is put on the processor: the most Linux takes.
_BACKGROUND_NICE = "19"

#: The idle disk-scheduling class, which needs no privileges.
_IDLE_IO_CLASS = "3"


#: A constant rather than an inline check, so tests can drive either machine's path.
_UNIX_PRIORITY_TOOLS = sys.platform != "win32"


@cache
def _priority_tools() -> tuple[str | None, str | None]:
    """Where `nice` and `ionice` are, looked up once; never on Windows, where a foreign `nice`
    re-parses ffmpeg's escaped filter arguments."""
    if not _UNIX_PRIORITY_TOOLS:
        return None, None
    return shutil.which("nice"), shutil.which("ionice")


#: The class a background child is created in on Windows. See `Priority` for why this one.
_BACKGROUND_CLASS = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


def creation_flags(priority: Priority) -> int:
    """The Windows creation flags for `priority`, zero elsewhere, so it never runs at full."""
    if priority is not Priority.BACKGROUND or _UNIX_PRIORITY_TOOLS:
        return 0
    return _BACKGROUND_CLASS


# --- the disk and the memory, on Windows ----------------------------------------------------------
# A below-normal child keeps normal disk and memory priority, set here through its handle.

#: `PROCESS_INFORMATION_CLASS`: the process's I/O priority, and its memory (page) priority.
_PROCESS_IO_PRIORITY = 33
_PROCESS_PAGE_PRIORITY = 39
#: `IoPriorityVeryLow`: the disk serves this process when nobody else is waiting. Background mode's.
IO_PRIORITY_VERY_LOW = 0
#: `MEMORY_PRIORITY_VERY_LOW`: this process's pages are the first to leave memory. Background mode's.
MEMORY_PRIORITY_VERY_LOW = 1

#: Whether a refusal to lower a child has been logged: once per run says it.
_STEP_ASIDE_FAILED_LOGGED = False


@cache
def _process_api() -> Any:
    """ntdll with the two process-information calls typed, or None where there is no such thing."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    try:
        ntdll = ctypes.WinDLL("ntdll")  # type: ignore[attr-defined, unused-ignore]
    except (OSError, AttributeError):
        return None
    ntdll.NtSetInformationProcess.restype = ctypes.c_long
    ntdll.NtSetInformationProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.ULONG,
    ]
    ntdll.NtQueryInformationProcess.restype = ctypes.c_long
    ntdll.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    ]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    return ntdll


def lower_disk_and_memory(handle: int, api: Any = None) -> bool:
    """Put the process behind `handle` at the back of the disk's and memory's lines."""
    global _STEP_ASIDE_FAILED_LOGGED
    api = _process_api() if api is None else api
    if api is None:
        return False
    import ctypes

    wanted = (
        (_PROCESS_IO_PRIORITY, IO_PRIORITY_VERY_LOW),
        (_PROCESS_PAGE_PRIORITY, MEMORY_PRIORITY_VERY_LOW),
    )
    refused: list[int] = []
    for information, value in wanted:
        setting = ctypes.c_ulong(value)
        status = api.NtSetInformationProcess(
            handle, information, ctypes.byref(setting), ctypes.sizeof(setting)
        )
        if status != 0:
            refused.append(information)
    if refused and not _STEP_ASIDE_FAILED_LOGGED:
        _STEP_ASIDE_FAILED_LOGGED = True
        from sift.kernel.log import get_logger

        get_logger(__name__).warning("subprocess.background_priority_refused", classes=refused)
    return not refused


def read_disk_and_memory(handle: int, api: Any = None) -> tuple[int, int] | None:
    """The I/O priority and memory priority of the process behind `handle`, or None if unreadable."""
    api = _process_api() if api is None else api
    if api is None:
        return None
    import ctypes
    from ctypes import wintypes

    answers: list[int] = []
    for information in (_PROCESS_IO_PRIORITY, _PROCESS_PAGE_PRIORITY):
        value = ctypes.c_ulong(0)
        written = wintypes.ULONG(0)
        status = api.NtQueryInformationProcess(
            handle, information, ctypes.byref(value), ctypes.sizeof(value), ctypes.byref(written)
        )
        if status != 0:
            return None
        answers.append(value.value)
    return answers[0], answers[1]


def step_aside(process: subprocess.Popen[bytes], priority: Priority = Priority.BACKGROUND) -> None:
    """Lower a freshly started background child's disk and memory priority, on Windows."""
    if priority is not Priority.BACKGROUND or _UNIX_PRIORITY_TOOLS:
        return
    # The process handle `subprocess` holds: the one thing Windows accepts to name the process.
    handle = getattr(process, "_handle", None)
    if handle is not None:
        lower_disk_and_memory(int(handle))


def launch_prefix(priority: Priority) -> list[str]:
    """What to put in front of a tool's arguments to launch it at `priority`, on Unix.

    Both tools replace themselves, so the kill still reaches the tool at the same process id.
    """
    if priority is not Priority.BACKGROUND:
        return []

    nice, ionice = _priority_tools()
    prefix: list[str] = []
    if nice is not None:
        prefix += [nice, "-n", _BACKGROUND_NICE]
    if ionice is not None:
        prefix += [ionice, "-c", _IDLE_IO_CLASS]
    return prefix


# --- how much memory a background tool may take --------------------------------------------------
# A decoder refused memory retries for ever, so a breach ends the tool rather than starving it.

#: One background tool may commit this fraction of the machine's memory: a quarter.
BACKGROUND_MEMORY_SHARE = 4

#: What a background tool plans against while nothing has said how much memory the machine has.
ASSUMED_MACHINE_MEMORY = 8 << 30

#: The machine's memory in bytes, from the hardware report, or None while nothing has said.
_machine_memory: int | None = None


def set_machine_memory(total_bytes: int | None) -> None:
    """Record how much memory this machine has. None, or nothing at all, where it could not say."""
    global _machine_memory
    _machine_memory = total_bytes if total_bytes is not None and total_bytes > 0 else None


def background_memory_limit() -> int | None:
    """The most one background tool may commit, or None where the machine's memory is unknown."""
    if _machine_memory is None:
        return None
    return _machine_memory // BACKGROUND_MEMORY_SHARE


def planned_memory(at_once: int) -> int:
    """What one of `at_once` background tools should plan to use, well clear of its limit."""
    machine = _machine_memory or ASSUMED_MACHINE_MEMORY
    return max(1, min(machine // BACKGROUND_MEMORY_SHARE // 2, machine // 2 // max(1, at_once)))


def memory_limit_for(priority: Priority) -> int | None:
    """The memory a tool at `priority` is held to. Somebody waiting on a tool means no limit."""
    return background_memory_limit() if priority is Priority.BACKGROUND else None


#: Told each line a tool writes to its output, as it writes it, for a tool reporting progress.
OnLine = Callable[[str], None]


_LINE_BREAK = re.compile(r"[\r\n]")


async def run(
    argv: list[str],
    *,
    time_limit: float,
    capture_stdout: bool = True,
    priority: Priority = Priority.NORMAL,
    stdin: bytes | None = None,
    on_line: OnLine | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> SubprocessResult:
    """Run `argv` to completion on threads, killed with all it started past `time_limit` or on
    cancel; `SubprocessError` if it will not run."""
    started = time.perf_counter()
    process = await _spawn(
        argv, priority, stdin=stdin is not None, stdout=capture_stdout, extra_env=extra_env
    )
    stdout, stderr = await _collect_within(
        process, argv, stdin=stdin, on_line=on_line, time_limit=time_limit, started=started
    )
    if process in _OVER_MEMORY:
        raise SubprocessError(_over_memory(argv))
    return SubprocessResult(
        returncode=process.returncode or 0,
        stdout=stdout,
        stderr=stderr,
    )


async def _collect_within(
    process: subprocess.Popen[bytes],
    argv: list[str],
    *,
    stdin: bytes | None,
    on_line: OnLine | None,
    time_limit: float,
    started: float,
) -> tuple[bytes, bytes]:
    """The tool's output, read to the end within `time_limit`; the tool killed if it overruns."""
    try:
        return await asyncio.wait_for(_collect(process, stdin, on_line), timeout=time_limit)
    except TimeoutError:
        await _kill(process)
        raise TookTooLong(f"{argv[0]!r} took too long and was stopped") from None
    except asyncio.CancelledError:
        await _kill(process)
        raise
    finally:
        _ran(process, started)
        # Closing the job ends whatever the tool started, after a kill or an ordinary exit.
        _release(process)


async def _spawn(
    argv: list[str],
    priority: Priority,
    *,
    stdin: bool,
    stdout: bool,
    extra_env: Mapping[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """Start the tool on a thread, in its own job on Windows; `SubprocessError` if it cannot."""
    variables = {**os.environ, **extra_env} if extra_env else None
    paused = _paused_start()

    def start() -> subprocess.Popen[bytes]:
        process = subprocess.Popen(  # noqa: S603 (a list, never a shell; see the module header)
            [*launch_prefix(priority), *argv],
            stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
            stdout=subprocess.PIPE if stdout else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creation_flags(priority) | paused,
            env=variables,
        )
        _hold_then_start(process, priority, paused)
        return process

    try:
        return await asyncio.to_thread(start)
    except OSError as exc:
        raise SubprocessError(f"could not run {argv[0]!r}") from exc


# --- the whole tree, on Windows -------------------------------------------------------------------
# A kill reaches one process, and a one-file launcher's real program is its child; a kill-on-close
# job ends everything the tool started when its last handle closes.

#: Each tool's job handle, closed exactly once by a finaliser; weak, so no tool is kept alive.
_JOBS: weakref.WeakKeyDictionary[subprocess.Popen[bytes], weakref.finalize[Any, Any]] = (
    weakref.WeakKeyDictionary()
)


@cache
def _job_api() -> Any:
    """kernel32 with the job calls typed (an untyped handle is truncated), or None off Windows."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined, unused-ignore]
    except (OSError, AttributeError):
        return None
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CreateIoCompletionPort.restype = wintypes.HANDLE
    kernel32.CreateIoCompletionPort.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.c_size_t,
        wintypes.DWORD,
    ]
    kernel32.GetQueuedCompletionStatus.restype = wintypes.BOOL
    kernel32.GetQueuedCompletionStatus.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.DWORD,
    ]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    return kernel32


@cache
def _memory_watch() -> _MemoryWatch | None:
    api = _job_api()
    return None if api is None else _MemoryWatch(api)


def _close_job(api: Any, job: int, key: int | None) -> None:
    """Close a tool's job, having first stopped listening for it."""
    if key is not None:
        watch = _memory_watch()
        if watch is not None:
            watch.forget(key)
    # Under the rate's lock, so `hold_background` never sets a rate on a handle closed beside it.
    global _tools_ended
    with _RATE_LOCK:
        if job in _LIVE_JOBS:
            _LIVE_JOBS.discard(job)
            _tools_ended += _job_time(api, job)
        api.CloseHandle(job)


# --- the share of the processor a background tool may use -----------------------------------------
# Thread flags reach one decoder each, so while Sift steps back a job gets a hard processor rate.

#: The rate each background tool is held to, in hundredths of a percent, or None.
_background_rate: int | None = None
#: The background tools' processes, so a moved rate reaches the ones already running.
_BACKGROUND: weakref.WeakSet[subprocess.Popen[bytes]] = weakref.WeakSet()
#: So a rate is never set on a handle another thread has just closed.
_RATE_LOCK = threading.Lock()


def hold_background(rate: int | None) -> None:
    """Hold every background tool, running ones too, to `rate` hundredths of a percent, or None."""
    global _background_rate
    api = _job_api()
    with _RATE_LOCK:
        if rate == _background_rate:
            return
        _background_rate = rate
        if api is None:
            return
        for process in list(_BACKGROUND):
            close = _JOBS.get(process)
            held = None if close is None else close.peek()
            if held is not None:
                _set_rate(api, held[2][1], rate)


def background_rate() -> int | None:
    """The processor rate each background tool is held to now, or None while none is."""
    return _background_rate


def _contain(
    process: subprocess.Popen[bytes],
    memory_limit: int | None = None,
    *,
    background: bool = False,
) -> None:
    """Put a freshly started tool into a job of its own, on Windows; a refusal still runs it."""
    api = _job_api()
    if api is None:
        return
    import ctypes

    job = api.CreateJobObjectW(None, None)
    if not job:
        return
    limits = _kill_on_close_limits(memory_limit)
    key = None
    if memory_limit is not None:
        watch = _memory_watch()
        key = None if watch is None else watch.watch(job, process)
        if key is None:
            # A limit nobody hears would leave a tool refused memory and never ended.
            limits = _kill_on_close_limits()
    # The process handle `subprocess` holds: the one thing Windows accepts to name the process.
    handle = int(process._handle)  # type: ignore[attr-defined, unused-ignore]
    if not (
        api.SetInformationJobObject(
            job, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
        )
        and api.AssignProcessToJobObject(job, handle)
    ):
        _close_job(api, job, key)
        return
    _JOBS[process] = weakref.finalize(process, _close_job, api, job, key)
    with _RATE_LOCK:
        _LIVE_JOBS.add(job)
    if background:
        with _RATE_LOCK:
            _BACKGROUND.add(process)
            if _background_rate is not None:
                _set_rate(api, job, _background_rate)


# --- a tool held until its job holds it ---------------------------------------------------------
# A tool started running reads before it is put in its job, and the job never counts those bytes
# (a quick enough tool has finished first), so it starts paused and is let go once it is held.

#: `CREATE_SUSPENDED`.
_PAUSED = 0x00000004


def _paused_start() -> int:
    """The creation flag that holds a tool still until `_hold_then_start`, where it can be let go."""
    return _PAUSED if _job_api() is not None and _process_api() is not None else 0


def _hold_then_start(process: subprocess.Popen[bytes], priority: Priority, paused: int) -> None:
    """Lower and contain a freshly started tool, then let it run if it was started paused."""
    try:
        step_aside(process, priority)
        _contain(process, memory_limit_for(priority), background=priority is Priority.BACKGROUND)
    finally:
        if paused:
            _let_go(process)


def _let_go(process: subprocess.Popen[bytes]) -> None:
    """Start a paused tool; one that will not start is ended, never left waiting for its limit."""
    handle = int(process._handle)  # type: ignore[attr-defined, unused-ignore]
    if _process_api().NtResumeProcess(handle) != 0:
        process.kill()


#: `PROCESS_SUSPEND_RESUME`: the one right letting a paused tool go needs.
_MAY_RESUME = 0x0800


def _let_go_by_id(pid: int) -> bool:
    """Start a paused tool known only by its id, which then runs uncontained; False if it cannot."""
    api = _job_api()
    handle = api.OpenProcess(_MAY_RESUME, False, pid)
    if not handle:
        return False
    try:
        return bool(_process_api().NtResumeProcess(handle) == 0)
    finally:
        api.CloseHandle(handle)


# --- the processor time Sift's tools have used, for `kernel.device_load` ------------------------

#: The job handles still open, read under `_RATE_LOCK` so none is closed while it is asked.
_LIVE_JOBS: set[int] = set()
#: Processor time of the tools whose jobs have closed, in 100 ns units.
_tools_ended = 0


def tools_time() -> tuple[int, frozenset[int]]:
    """The processor time every tool has used, in 100 ns units, and the ids of those running."""
    api = _job_api()
    if api is None:
        return 0, frozenset()
    with _RATE_LOCK:
        jobs = list(_LIVE_JOBS)
        used = _tools_ended + sum(_job_time(api, job) for job in jobs)
        ids = frozenset(one for job in jobs for one in _job_ids(api, job))
    return used, ids


#: `JobObjectBasicAndIoAccountingInformation`: the job's processor time and its I/O counters.
_IO_ACCOUNTING = 8


@cache
def _io_accounting() -> Any:
    """`JOBOBJECT_BASIC_AND_IO_ACCOUNTING_INFORMATION`, flattened."""
    import ctypes
    from ctypes import wintypes

    class Both(ctypes.Structure):
        _fields_ = (
            *((name, ctypes.c_int64) for name in ("User", "Kernel", "PeriodUser", "PeriodKernel")),
            *((name, wintypes.DWORD) for name in ("Faults", "Total", "Active", "Terminated")),
            *(
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ),
        )

    return Both


def _read_bytes(process: subprocess.Popen[bytes] | None) -> int:
    """What the tool and all it started have read, from its job; 0 where there is no job."""
    close = None if process is None else _JOBS.get(process)
    held = None if close is None else close.peek()
    if held is None:
        return 0
    import ctypes

    api, job, _key = held[2]
    info = _io_accounting()()
    if not api.QueryInformationJobObject(
        job, _IO_ACCOUNTING, ctypes.byref(info), ctypes.sizeof(info), None
    ):
        return 0
    return int(info.ReadTransferCount)


def _ran(process: subprocess.Popen[bytes] | None, started: float) -> None:
    """File one run of a tool, and what it read, to the job it ran for. Before `_release`."""
    # Here, not at the top: this module is loaded by bare interpreters with no logging installed.
    from sift.kernel.log import job_cost

    cost = job_cost()
    if cost is not None:
        cost.launched(started, time.perf_counter(), _read_bytes(process))


def _release(process: subprocess.Popen[bytes]) -> None:
    """Close the tool's job handle, once, which ends anything still in it."""
    close = _JOBS.pop(process, None)
    if close is not None:
        close()


async def capture(
    argv: list[str],
    *,
    time_limit: float,
    priority: Priority = Priority.NORMAL,
) -> bytes:
    """Run `argv` on threads and hand back all it wrote, uncapped; killed past `time_limit` or on
    cancel. A non-zero exit raises `ToolFailed` with the end of what the tool said."""
    started = time.perf_counter()
    paused = _paused_start()

    def launch() -> subprocess.Popen[bytes]:
        try:
            process = subprocess.Popen(  # noqa: S603 (a list, never a shell; see the module header)
                [*launch_prefix(priority), *argv],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creation_flags(priority) | paused,
            )
        except OSError as exc:
            raise SubprocessError(f"could not run {argv[0]!r}") from exc
        try:
            _hold_then_start(process, priority, paused)
        except BaseException:
            process.kill()
            process.communicate()
            raise
        return process

    def talk(process: subprocess.Popen[bytes]) -> tuple[bytes, bytes]:
        try:
            return process.communicate(timeout=time_limit)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise TookTooLong(f"{argv[0]!r} took too long and was stopped") from exc

    launching = asyncio.ensure_future(asyncio.to_thread(launch))
    try:
        process = await asyncio.shield(launching)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            abandoned = await launching
            abandoned.kill()
            await asyncio.to_thread(abandoned.communicate)
            _release(abandoned)
        raise
    talking = asyncio.ensure_future(asyncio.to_thread(talk, process))
    filed = False
    try:
        output, errors = await asyncio.shield(talking)
    except asyncio.CancelledError:
        # The thread cannot be cancelled: ending the tool and all it started closes its pipes,
        # which ends the thread. Filed first: a closed job has no counters left to ask.
        with contextlib.suppress(OSError):
            process.kill()
        _ran(process, started)
        filed = True
        _release(process)
        with contextlib.suppress(Exception):
            await talking
        raise
    finally:
        if not filed:
            _ran(process, started)
        _release(process)
    return _captured(process, argv, output, errors)


def _captured(
    process: subprocess.Popen[bytes], argv: list[str], output: bytes | None, errors: bytes | None
) -> bytes:
    """What `capture` hands back, or the failure its tool's exit says."""
    if process in _OVER_MEMORY:
        raise SubprocessError(_over_memory(argv))
    if process.returncode != 0:
        said = said_tail(errors) or unsaid(process.returncode)
        raise ToolFailed(
            f"{argv[0]!r} failed with exit code {process.returncode}: {said}",
            returncode=process.returncode,
            said=said,
        )
    return output or b""


async def _collect(
    process: subprocess.Popen[bytes],
    stdin: bytes | None = None,
    on_line: OnLine | None = None,
) -> tuple[bytes, bytes]:
    """Read both streams to EOF, each bounded, while feeding the input, then reap.

    On threads of its own: a shared pool short of three would deadlock the tool."""
    own = ThreadPoolExecutor(max_workers=3, thread_name_prefix="sift-tool-pipes")
    try:
        stdout, stderr, _ = await asyncio.gather(
            _capped_read(process.stdout, own, on_line),
            _capped_read(process.stderr, own),
            _feed(process, stdin, own),
        )
        await asyncio.get_running_loop().run_in_executor(own, process.wait)
    finally:
        own.shutdown(wait=False)
    return stdout, stderr


async def _feed(process: subprocess.Popen[bytes], payload: bytes | None, threads: Executor) -> None:
    """Hand the tool its input and close it; a broken pipe is an ordinary end."""
    pipe = process.stdin
    if payload is None or pipe is None:
        return

    def write_and_close() -> None:
        with contextlib.suppress(BrokenPipeError, ConnectionResetError, OSError):
            pipe.write(payload)
            pipe.flush()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError, OSError):
            pipe.close()

    await asyncio.get_running_loop().run_in_executor(threads, write_and_close)


def _read_some(pipe: IO[bytes]) -> bytes:
    """What the tool has written so far, up to a chunk: the raw descriptor never waits for more."""
    return os.read(pipe.fileno(), _READ_CHUNK)


async def _capped_read(
    stream: IO[bytes] | None, threads: Executor, on_line: OnLine | None = None
) -> bytes:
    """Read a stream to EOF keeping at most the cap, handing each line to `on_line` on the loop."""
    if stream is None:
        return b""
    loop = asyncio.get_running_loop()
    buffer = bytearray()
    pending = ""
    while len(buffer) <= _MAX_OUTPUT_BYTES:
        chunk = await loop.run_in_executor(threads, _read_some, stream)
        if not chunk:
            if on_line is not None and pending.strip():
                _tell(on_line, pending)
            return bytes(buffer)
        buffer.extend(chunk)
        if on_line is not None:
            pending = _lines(pending + chunk.decode("utf-8", "replace"), on_line)
    while True:
        chunk = await loop.run_in_executor(threads, _read_some, stream)
        if not chunk:
            break
        if on_line is not None:
            pending = _lines(pending + chunk.decode("utf-8", "replace"), on_line)
    return bytes(buffer[:_MAX_OUTPUT_BYTES])


def _lines(text: str, on_line: OnLine) -> str:
    """Hand over every complete line in `text`, split on carriage returns too; return the tail."""
    parts = _LINE_BREAK.split(text)
    tail = parts.pop()
    for part in parts:
        if part.strip():
            _tell(on_line, part)
    return tail


def _tell(on_line: OnLine, line: str) -> None:
    """Hand one line over; a caller that raises must not stop the read and hang the tool."""
    with contextlib.suppress(Exception):
        on_line(line.strip())


async def _kill(process: subprocess.Popen[bytes] | asyncio.subprocess.Process) -> None:
    """Kill a tool and reap it, tolerating one that has already exited; `_release` ends its tree."""
    with contextlib.suppress(ProcessLookupError):
        process.kill()
    if isinstance(process, subprocess.Popen):
        await asyncio.to_thread(process.wait)
        return
    with contextlib.suppress(ProcessLookupError):
        await process.wait()


def _popen_of(process: asyncio.subprocess.Process) -> subprocess.Popen[bytes] | None:
    """The `Popen` behind a loop-launched tool, which a job is made from, or None."""
    transport = getattr(process, "_transport", None)
    found = None if transport is None else transport.get_extra_info("subprocess")
    return found if isinstance(found, subprocess.Popen) else None


async def _launch_streaming(
    argv: list[str], priority: Priority, *, stdin: bytes | None
) -> tuple[asyncio.subprocess.Process, subprocess.Popen[bytes] | None]:
    """Start a streamed tool on the loop, contained like the others, and the `Popen` behind it."""
    paused = _paused_start()
    try:
        process = await asyncio.create_subprocess_exec(
            *launch_prefix(priority),
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=creation_flags(priority) | paused,
        )
    except OSError as exc:
        raise SubprocessError(f"could not run {argv[0]!r}") from exc
    held = _popen_of(process)
    if held is not None:
        await asyncio.to_thread(_hold_then_start, held, priority, paused)
    elif paused and not _let_go_by_id(process.pid):
        await _kill(process)
        raise SubprocessError(f"could not run {argv[0]!r}")
    return process, held


async def _pump(process: asyncio.subprocess.Process, stdin: bytes | None) -> None:
    """Hand a streamed tool its input and close it; a tool that stops reading early is no fault."""
    if stdin is None or process.stdin is None:
        return
    process.stdin.write(stdin)
    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
        await process.stdin.drain()
    process.stdin.close()


async def stream(
    argv: list[str],
    *,
    frame_bytes: int,
    time_limit: float,
    priority: Priority = Priority.NORMAL,
    stdin: bytes | None = None,
) -> AsyncGenerator[bytes]:
    """Run a tool and hand back its output in fixed-size pieces; closing the generator kills it."""
    started = time.perf_counter()
    process, held = await _launch_streaming(argv, priority, stdin=stdin)
    feeding = asyncio.ensure_future(_pump(process, stdin))
    output = process.stdout
    if output is None:  # pragma: no cover - stdout is always a pipe above
        raise SubprocessError(f"{argv[0]!r} produced no output stream")
    finished = False
    try:
        async with asyncio.timeout(time_limit):
            while True:
                piece = await output.readexactly(frame_bytes)
                yield piece
    except asyncio.IncompleteReadError:
        finished = True
        await process.wait()
    except TimeoutError:
        raise TookTooLong(f"{argv[0]!r} took too long and was stopped") from None
    finally:
        feeding.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await feeding
        # Never reaped twice: the loop would report a made-up status.
        if process.returncode is None:
            await _kill(process)
        _ran(held, started)
        if held is not None:
            _release(held)

    if held is not None and held in _OVER_MEMORY:
        raise SubprocessError(_over_memory(argv))
    # Only for a stream that ran out on its own: a killed tool's status says nothing.
    if finished and process.returncode:
        raise SubprocessError(f"{argv[0]!r} failed with status {process.returncode}")


__all__ = [
    "LongLivedChild",
    "Priority",
    "SubprocessError",
    "SubprocessResult",
    "TookTooLong",
    "ToolFailed",
    "background_memory_limit",
    "background_rate",
    "creation_flags",
    "hold_background",
    "launch_prefix",
    "lower_disk_and_memory",
    "memory_limit_for",
    "planned_memory",
    "read_disk_and_memory",
    "run",
    "said_tail",
    "set_machine_memory",
    "start_long_lived",
    "step_aside",
    "stream",
]
