# SPDX-License-Identifier: AGPL-3.0-or-later
"""Running an external tool: spawned, bounded, and always reaped.

ffmpeg, ffprobe, yt-dlp and gallery-dl are all run this way: as a separate process, with its
arguments passed as a list rather than a string a shell would read, given a time budget, and killed
if it exceeds it or if the work it belongs to is cancelled. That last part is why this is one
function and not four. A tool left running after its job is cancelled (because the caller
re-raised the cancellation without killing the child) keeps fetching or decoding in the
background, an orphan nobody is waiting for. Killing on both the timeout and the cancellation, in
the one place every tool is spawned, is what stops that.

It is also the one place that decides how much of the machine a tool may take. That belongs here
for the same reason the kill does: it is a property of spawning a child rather than of any one
tool, and a second spawn point would be a second answer to it. See `Priority`.

This returns the raw outcome (the exit code and the captured streams) and raises only when the
tool could not be started or did not finish in time. What a non-zero exit *means* is the caller's to
decide: a downloader treats it as a failed fetch, the ingress gate as an undecodable file, the
hardware probe as "assume no encoders". The interpretation stays with them; only the mechanics live
here.

Nothing here logs. A tool's output is where a URL or a path can leak, so what to do with it is the
caller's decision, made after the output has been through that layer's own redaction.
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
import weakref
from collections.abc import AsyncGenerator, Callable, Mapping
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from typing import IO, Any

_READ_CHUNK = 1 << 16  # 64 KiB per read

# The most of a tool's stdout or stderr this keeps. Past the cap it is read and thrown away, so the
# tool never blocks on a full pipe and an endless stream cannot exhaust memory.
_MAX_OUTPUT_BYTES = 16 * 1024 * 1024


class SubprocessError(Exception):
    """A tool could not be started, or did not finish within its time budget.

    Distinct from a tool that ran and reported a failure: that comes back as a non-zero exit code
    for the caller to interpret. This is the process itself never producing one.
    """


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
    """How much of the machine a spawned tool may take: whether anybody is waiting for it, not
    how heavy it is.

    About the CHILD, not Sift's own threads: a thread cannot be put below its process, and the
    heavy work all happens in children. The per-type job caps decide how much runs at once.

    On Windows a background child is created below normal, not idle: a busy foreground starves
    the idle class outright, and a library would never finish its pictures while a game ran.
    """

    NORMAL = "normal"
    """Somebody is waiting for this. Playback, downloads, and anything on a request path."""

    BACKGROUND = "background"
    """Generated work nobody asked for yet. It yields the processor and the disk to everything else
    and takes as long as it takes, which for a thumbnail nobody has scrolled to is free."""


#: How far below normal a background child is put on the processor: the most Linux takes.
_BACKGROUND_NICE = "19"

#: The idle disk-scheduling class, the half that matters most where the work is reading whole
#: video files. It needs no privileges to ask for.
_IDLE_IO_CLASS = "3"


#: Whether this platform has the tools the prefix is made of at all.
#: A named constant rather than the check written inline, so the tolerance rule below (one tool
#: missing costs only its own half) can still be driven on a machine that has neither.
_UNIX_PRIORITY_TOOLS = sys.platform != "win32"


@cache
def _priority_tools() -> tuple[str | None, str | None]:
    """Where `nice` and `ionice` are on this machine, if they are anywhere. Looked up once.

    Either missing costs only its own half. Never looked for on Windows: Git for Windows ships a
    `nice.EXE`, and a foreign `nice` re-parses ffmpeg's escaped filter arguments. Windows sets a
    priority class at creation instead (`creation_flags`).
    """
    if not _UNIX_PRIORITY_TOOLS:
        return None, None
    return shutil.which("nice"), shutil.which("ionice")


#: The class a background child is created in on Windows. See `Priority` for why this one.
_BACKGROUND_CLASS = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


def creation_flags(priority: Priority) -> int:
    """What a child is created with on Windows to run at `priority`, zero everywhere else. Given
    at creation, so the tool never runs a moment at full priority."""
    if priority is not Priority.BACKGROUND or _UNIX_PRIORITY_TOOLS:
        return 0
    return _BACKGROUND_CLASS


# --- the disk and the memory, on Windows ----------------------------------------------------------
# The priority class above is the processor's half only: on Windows a process also carries an I/O
# priority (whose reads the disk serves first) and a memory priority (whose pages leave first when
# memory runs short), and a below-normal child keeps NORMAL for both. Background work is mostly
# reading whole video files, so the lag a busy library causes is the disk and the memory, far more
# than the processor.
# Windows' background mode can only be entered by a process for itself, so a launcher cannot hand
# it to a child; the two halves it sets can be set on another process through its handle, which is
# what this does right after the child starts. On Unix the `ionice` in `launch_prefix` is the disk half.

#: `PROCESS_INFORMATION_CLASS`: the process's I/O priority, and its memory (page) priority.
_PROCESS_IO_PRIORITY = 33
_PROCESS_PAGE_PRIORITY = 39
#: `IoPriorityVeryLow`: the disk serves this process when nobody else is waiting. Background mode's.
IO_PRIORITY_VERY_LOW = 0
#: `MEMORY_PRIORITY_VERY_LOW`: this process's pages are the first to leave memory. Background mode's.
MEMORY_PRIORITY_VERY_LOW = 1

#: Whether a failure to lower a child has been written down yet. Once per run: a machine that
#: refuses it refuses it for every child, and one line says so as well as ten thousand.
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
    return ntdll


def lower_disk_and_memory(handle: int, api: Any = None) -> bool:
    """Put the process behind `handle` at the back of the disk's and the memory's lines.

    True when both were set. Tolerant by design, like the job and the priority tools: a machine
    that refuses still runs the child exactly as it did before, and the first refusal is logged
    once. `api` is the ntdll to call, looked up when not given, which is how a test hands it a
    stand-in that records the calls.
    """
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
    """Lower a freshly started child's disk and memory priority when it works in the background.

    Nothing at normal priority, where somebody is waiting on the tool, and nothing off Windows.
    """
    if priority is not Priority.BACKGROUND or _UNIX_PRIORITY_TOOLS:
        return
    # The process handle `subprocess` holds: the one thing Windows accepts to name the process.
    handle = getattr(process, "_handle", None)
    if handle is not None:
        lower_disk_and_memory(int(handle))


def launch_prefix(priority: Priority) -> list[str]:
    """What to put in front of a tool's own arguments to launch it at `priority`.

    Both tools replace themselves with what they are given rather than starting it alongside, so
    the process that ends up running is the tool itself, at the same process id, which is what
    keeps the kill and the reaping below aimed at the right thing.

    Nothing is prefixed at normal priority, so the ordinary path is byte-for-byte what it was.

    This is the Unix half. Windows has no prefix: it sets a priority class on the process at
    creation, which is `creation_flags` beside this, and the class chosen there is BELOW_NORMAL,
    not the idle class, for the reason `Priority` gives. Inference runs in a child of its own (`kernel.ml.child`), started through both halves like any other tool.
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
#
# A tool that works for nobody who is waiting gets a share of the machine's memory, and on Windows
# its job object holds it to that share. Refusing it more memory is not enough on its own: a decoder
# refused an allocation reports the error and tries again, and can sit at the limit for as long as
# it is left there. So the job also reports the breach, and the tool is ended at once and
# fails the way a tool that ran out of time does: a failed job rather than a starved machine.

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
    """What one background tool should plan to use while `at_once` of them run together.

    Half the machine shared among them, and never more than half of one tool's limit, so work that
    goes as planned stays well clear of the line where a tool is stopped.
    """
    machine = _machine_memory or ASSUMED_MACHINE_MEMORY
    return max(1, min(machine // BACKGROUND_MEMORY_SHARE // 2, machine // 2 // max(1, at_once)))


def memory_limit_for(priority: Priority) -> int | None:
    """The memory a tool at `priority` is held to. Somebody waiting on a tool means no limit."""
    return background_memory_limit() if priority is Priority.BACKGROUND else None


#: Told each line a tool writes to its output, as it writes it. For a tool that reports its own
#: progress: the alternative is inferring progress from a side effect, which is wrong in ways that
#: are visible on screen.
OnLine = Callable[[str], None]


#: What ends a line, for a caller watching a tool report on itself. Carriage return as well as
#: newline: see `_lines`.
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
    """Run `argv` to completion and return its result. Raise `SubprocessError` if it will not run.

    A list, never a shell. Past `time_limit`, or when the caller is cancelled, the tool is killed,
    on Windows with everything it started (`_contain`). `capture_stdout` False discards stdout;
    stderr is always kept for a failure's detail. `stdin` is handed over and the input closed.
    At background priority on Unix the launch is `nice`, so a missing tool is a non-zero exit, not
    `SubprocessError`. `on_line` is told each line (split on carriage returns too) as it arrives.
    `extra_env` is added to Sift's own environment, never instead of it. The launch and the reads
    run on threads (`_collect`), because process creation on the loop would hold it.
    """
    process = await _spawn(
        argv, priority, stdin=stdin is not None, stdout=capture_stdout, extra_env=extra_env
    )
    stdout, stderr = await _collect_within(
        process, argv, stdin=stdin, on_line=on_line, time_limit=time_limit
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
) -> tuple[bytes, bytes]:
    """The tool's output, read to the end within `time_limit`; the tool killed if it overruns."""
    try:
        return await asyncio.wait_for(_collect(process, stdin, on_line), timeout=time_limit)
    except TimeoutError:
        await _kill(process)
        raise SubprocessError(f"{argv[0]!r} took too long and was stopped") from None
    except asyncio.CancelledError:
        # The work this belongs to was cancelled while the tool ran. Kill it (otherwise it keeps
        # going in the background after the person asked it to stop) and let the cancellation go.
        await _kill(process)
        raise
    finally:
        # However it ended, the tool's job is closed, and closing it ends everything still in it:
        # after a kill, the processes the tool started (a one-file launcher's real program); after
        # an ordinary exit, anything it left running behind it: the same orphan by another road.
        _release(process)


async def _spawn(
    argv: list[str],
    priority: Priority,
    *,
    stdin: bool,
    stdout: bool,
    extra_env: Mapping[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """Start the tool on a thread and hand back the handle. `SubprocessError` if it cannot start.

    On Windows the tool is put into a job object of its own before this returns (see `_contain`),
    so every process it starts is one the kill can reach.
    """
    variables = {**os.environ, **extra_env} if extra_env else None

    def start() -> subprocess.Popen[bytes]:
        process = subprocess.Popen(  # noqa: S603 (a list, never a shell; see the module header)
            [*launch_prefix(priority), *argv],
            stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
            stdout=subprocess.PIPE if stdout else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creation_flags(priority),
            env=variables,
        )
        step_aside(process, priority)
        _contain(process, memory_limit_for(priority), background=priority is Priority.BACKGROUND)
        return process

    try:
        return await asyncio.to_thread(start)
    except OSError as exc:
        raise SubprocessError(f"could not run {argv[0]!r}") from exc


# --- the whole tree, on Windows -------------------------------------------------------------------
# Killing a process on Windows kills THAT process; its children are not told (no process group a
# signal reaches). yt-dlp and gallery-dl are the publishers' one-file builds: launchers that unpack a
# Python runtime and run the real program as a child, so `process.kill()` on the launcher leaves the
# child downloading with its pipes open and nothing in Sift holding it: cancelled, but not stopped.
# A job made with `KILL_ON_JOB_CLOSE` holds every process the tool starts and ends them all when its
# last handle closes: `_release` after every run, or Sift itself exiting, crashed or not. The tool is
# assigned just after it starts (`subprocess` offers no way in between), a window far shorter than
# a launcher's unpacking.

#: `JOBOBJECT_EXTENDED_LIMIT_INFORMATION.BasicLimitInformation.LimitFlags`: end every process in
#: the job when the last handle to it closes.
_KILL_ON_JOB_CLOSE = 0x2000
#: The same flags: hold everything in the job, together, to `JobMemoryLimit` bytes of commit.
_JOB_MEMORY = 0x200
#: The information class `SetInformationJobObject` is told it is being handed.
_EXTENDED_LIMIT_INFORMATION = 9
#: The information class that ties a job to a completion port, which is how a breach is heard.
_ASSOCIATE_COMPLETION_PORT = 7
#: The message a job posts when something in it asked for more than the job's memory limit.
_MESSAGE_JOB_MEMORY_LIMIT = 10
#: The exit status a tool stopped for memory is given: Windows' own "not enough memory".
_NO_MEMORY_STATUS = 0xC0000017


#: How each running tool's job handle gets closed: a finaliser, so it is closed exactly once
#: whichever comes first: `_release` after the run, or the process object being collected by a
#: path that never reached `_release`. Keyed weakly, so an entry never keeps a finished tool alive.
_JOBS: weakref.WeakKeyDictionary[subprocess.Popen[bytes], weakref.finalize[Any, Any]] = (
    weakref.WeakKeyDictionary()
)


@cache
def _job_api() -> Any:
    """kernel32 with the job calls typed, or None where there is no such thing.

    Looked up once. Typed because the default ctypes conversion passes a handle as a C int, which
    truncates a 64-bit handle: the calls would then act on some other handle or on none.
    """
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


def _kill_on_close_limits(memory_limit: int | None = None) -> Any:
    """`JOBOBJECT_EXTENDED_LIMIT_INFORMATION` with `KILL_ON_JOB_CLOSE` set, and the memory limit
    with it when there is one."""
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = (
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        )

    class Counters(ctypes.Structure):
        _fields_ = tuple(
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        )

    class Extended(ctypes.Structure):
        _fields_ = (
            ("BasicLimitInformation", Basic),
            ("IoInfo", Counters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        )

    limits = Extended()
    limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
    if memory_limit is not None:
        limits.BasicLimitInformation.LimitFlags |= _JOB_MEMORY
        limits.JobMemoryLimit = memory_limit
    return limits


#: The tools a job ended for asking past their memory limit. Read once the tool has been reaped.
_OVER_MEMORY: weakref.WeakSet[subprocess.Popen[bytes]] = weakref.WeakSet()


def _over_memory(argv: list[str]) -> str:
    return f"{argv[0]!r} needed more memory than a background tool may use and was stopped"


class _MemoryWatch:
    """One completion port every memory-limited job reports to, and one thread reading it.

    A job is known by a number for as long as its handle is open, and forgotten under the same
    lock before the handle is closed, so a late message about a job that has gone finds nothing
    to end, and never a handle Windows has since given to something else.
    """

    def __init__(self, api: Any) -> None:
        self._api = api
        self._lock = threading.Lock()
        self._jobs: dict[int, tuple[int, weakref.ref[subprocess.Popen[bytes]]]] = {}
        self._next = 0
        self._port: int | None = None

    def watch(self, job: int, process: subprocess.Popen[bytes]) -> int | None:
        """Have the job report a breach here. The number to forget it by, or None if it cannot."""
        import ctypes

        class Associate(ctypes.Structure):
            _fields_ = (("CompletionKey", ctypes.c_void_p), ("CompletionPort", ctypes.c_void_p))

        with self._lock:
            if self._port is None:
                invalid = ctypes.c_void_p(-1).value
                port = self._api.CreateIoCompletionPort(invalid, None, 0, 1)
                if not port:
                    return None
                self._port = port
                threading.Thread(target=self._listen, name="sift-tool-memory", daemon=True).start()
            self._next += 1
            key = self._next
            tie = Associate(key, self._port)
            if not self._api.SetInformationJobObject(
                job, _ASSOCIATE_COMPLETION_PORT, ctypes.byref(tie), ctypes.sizeof(tie)
            ):
                return None
            self._jobs[key] = (job, weakref.ref(process))
            return key

    def forget(self, key: int) -> None:
        with self._lock:
            self._jobs.pop(key, None)

    def _listen(self) -> None:
        import ctypes
        from ctypes import wintypes

        message = wintypes.DWORD()
        key = ctypes.c_size_t()
        detail = ctypes.c_void_p()
        while True:
            if not self._api.GetQueuedCompletionStatus(
                self._port,
                ctypes.byref(message),
                ctypes.byref(key),
                ctypes.byref(detail),
                0xFFFFFFFF,
            ):
                if detail.value is None:
                    # Nothing was taken off the port: it is gone, and waiting again would spin.
                    return
                continue
            if message.value != _MESSAGE_JOB_MEMORY_LIMIT:
                continue
            with self._lock:
                held = self._jobs.get(key.value)
                if held is None:
                    continue
                job, tool = held
                # Marked before it is ended, so whoever reaps it already knows why it stopped.
                process = tool()
                if process is not None:
                    _OVER_MEMORY.add(process)
                self._api.TerminateJobObject(job, _NO_MEMORY_STATUS)


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
#
# A tool's thread flags reach one decoder each. A tool reading several inputs at once runs a decoder
# per input, so two threads asked for can be six used, and a pool held to a share of its workers can
# still keep every core busy. While the step back is in force (`kernel.attention`) each background
# tool's job is therefore given a hard processor rate, its threads' share of the machine, which the
# operating system holds whatever the tool does inside. Lifted when the whole device is in force, so
# a machine nobody is using runs its tools exactly as before.

#: The information class for a job's processor rate.
_CPU_RATE_INFORMATION = 15
#: Its flags: the rate is on, and it is a ceiling rather than a weight among jobs.
_CPU_RATE_ON = 0x1
_CPU_RATE_HARD_CAP = 0x4

#: The rate each background tool is held to, in hundredths of a percent of the machine, or None
#: while background tools may use what they can. Set by `hold_background`, read by `_contain`.
_background_rate: int | None = None
#: The background tools' processes, so a moved rate reaches the tools already running. Weak, like
#: `_JOBS`, so a finished tool is never kept.
_BACKGROUND: weakref.WeakSet[subprocess.Popen[bytes]] = weakref.WeakSet()
#: Taken around the rate and around every job handle closed, so a rate is never set on a handle
#: that another thread has just closed.
_RATE_LOCK = threading.Lock()


def _set_rate(api: Any, job: int, rate: int | None) -> bool:
    """Hold one job to `rate` hundredths of a percent of the machine, or lift its rate (None)."""
    import ctypes

    class Rate(ctypes.Structure):
        _fields_ = (("ControlFlags", ctypes.c_uint32), ("CpuRate", ctypes.c_uint32))

    info = Rate(0, 0) if rate is None else Rate(_CPU_RATE_ON | _CPU_RATE_HARD_CAP, rate)
    return bool(
        api.SetInformationJobObject(
            job, _CPU_RATE_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
        )
    )


def hold_background(rate: int | None) -> None:
    """Hold every background tool to `rate` hundredths of a percent of the machine, or let each use
    what it can (None). The tools already running are moved too, so a person arriving in the middle
    of a long tool is given the processor back at once rather than when it ends.

    Tolerant like `_contain`: a job that refuses a rate keeps running as it was.
    """
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
    """Put a freshly started tool into a job object of its own. Nothing at all off Windows.

    With `memory_limit`, everything in the job is held to that many bytes together, and a tool that
    asks for more is ended. See the note above `BACKGROUND_MEMORY_SHARE`.

    Tolerant by design, like the priority tools: a machine that will not make the job still runs
    the tool, and the kill falls back to the one process it always reached. Refusing to download
    because a containment call failed would turn a safety net into a dependency.
    """
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
            # A limit nothing is listening for would leave a tool refused memory and never ended,
            # which is the hang this exists to prevent. The tool runs as it did before.
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
        # Registered and given the rate in force under one lock, so a rate moved at this moment
        # reaches this tool either way.
        with _RATE_LOCK:
            _BACKGROUND.add(process)
            if _background_rate is not None:
                _set_rate(api, job, _background_rate)


# --- the processor time Sift's tools have used, for `kernel.device_load` ------------------------

#: The job handles still open, read under `_RATE_LOCK` so none is closed while it is asked.
_LIVE_JOBS: set[int] = set()
#: Processor time of the tools whose jobs have closed, in 100 ns units.
_tools_ended = 0
#: `JOBOBJECT_BASIC_ACCOUNTING_INFORMATION` and `JOBOBJECT_BASIC_PROCESS_ID_LIST`.
_ACCOUNTING = 1
_PROCESS_IDS = 3
#: How many process ids one tool's job is asked for: a tool and a helper or two.
_IDS_ASKED = 32


@cache
def _job_records() -> tuple[Any, Any]:
    """The two job records read back: basic accounting, and the process id list."""
    import ctypes
    from ctypes import wintypes

    class Accounting(ctypes.Structure):
        _fields_ = (
            ("TotalUserTime", ctypes.c_int64),
            ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodTotalUserTime", ctypes.c_int64),
            ("ThisPeriodTotalKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        )

    class Ids(ctypes.Structure):
        _fields_ = (
            ("NumberOfAssignedProcesses", wintypes.DWORD),
            ("NumberOfProcessIdsInList", wintypes.DWORD),
            ("ProcessIdList", ctypes.c_size_t * _IDS_ASKED),
        )

    return Accounting, Ids


def _job_time(api: Any, job: int) -> int:
    """Every process's processor time in the job, ended ones included; 0 when unreadable."""
    import ctypes

    info = _job_records()[0]()
    if not api.QueryInformationJobObject(
        job, _ACCOUNTING, ctypes.byref(info), ctypes.sizeof(info), None
    ):
        return 0
    return int(info.TotalUserTime + info.TotalKernelTime)


def _job_ids(api: Any, job: int) -> list[int]:
    """The ids of the processes in the job now, up to `_IDS_ASKED`."""
    import ctypes

    held = _job_records()[1]()
    # A job with more processes than asked for fails the call but still fills the list it was given.
    api.QueryInformationJobObject(job, _PROCESS_IDS, ctypes.byref(held), ctypes.sizeof(held), None)
    listed = min(_IDS_ASKED, int(held.NumberOfProcessIdsInList))
    return [int(one) for one in held.ProcessIdList[:listed]]


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


def _release(process: subprocess.Popen[bytes]) -> None:
    """Close the tool's job handle, once. With `KILL_ON_JOB_CLOSE` that ends anything still in it:
    the tool's children after a kill, or whatever it left running after an ordinary exit."""
    close = _JOBS.pop(process, None)
    if close is not None:
        close()


# --- a child that runs for as long as Sift does ---------------------------------------------------
#
# A child nothing reads to the end (a tunnel client) is held in a kill-on-close job whose only
# handle is this process's, so Windows ends it however Sift stops. Off Windows it is a plain child:
# a parent-death signal fires when the starting THREAD exits, which here is a worker thread.


class LongLivedChild:
    """A child that runs until it is told to stop, held in a kill-on-close job on Windows.

    The handle is the child's lifeline there: while this object is held the job stays open, and
    dropping it (or this process ending by any road) closes the job and ends the child. So a
    caller keeps it for as long as the child should run, and `wait` after a stop releases it.
    """

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        """The exit status, or None while it runs. Asked of the process each time, never cached."""
        return self._process.poll()

    def terminate(self) -> None:
        """Ask it to stop. On Windows there is no asking: this ends it, the same as `kill`."""
        self._process.terminate()

    def kill(self) -> None:
        self._process.kill()

    async def wait(self, *, time_limit: float | None = None) -> int:
        """Wait for it to exit, on a thread, and release its job. `TimeoutError` past `time_limit`.

        The limit is handed to the operating system's own wait rather than put round the await as
        a cancellation: cancelling the await would leave the thread blocked behind it until the
        child happened to exit, and a limit that leaves a thread waiting has not limited anything.
        """
        try:
            code = await asyncio.to_thread(self._process.wait, time_limit)
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"process {self.pid} did not exit in {time_limit} s") from None
        _release(self._process)
        return code


async def start_long_lived(
    argv: list[str],
    *,
    stdout: IO[bytes] | int = subprocess.DEVNULL,
    stderr: IO[bytes] | int = subprocess.DEVNULL,
) -> LongLivedChild:
    """Start a child that runs until it is told to stop, contained so it cannot outlive Sift.

    Its input is closed: a child of a server has no business reading the server's own, and the
    desktop shell uses this process's input to ask it to stop. Started on a thread, like `run`'s
    tools, because process creation on Windows is a wait nothing else on the loop should share.

    A launch that is cancelled while the thread is still creating the child does not leave the child
    behind: the launch is let finish and what it made is ended, since nothing will ever hold it.
    """

    def launch() -> subprocess.Popen[bytes]:
        child = subprocess.Popen(  # noqa: S603 (a list, never a shell; see the module header)
            argv,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
        )
        _contain(child)
        return child

    launching = asyncio.ensure_future(asyncio.to_thread(launch))
    try:
        started = await asyncio.shield(launching)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            abandoned = await launching
            abandoned.kill()
            await asyncio.to_thread(abandoned.wait)
            _release(abandoned)
        raise
    except OSError as exc:
        raise SubprocessError(f"could not run {argv[0]!r}") from exc
    return LongLivedChild(started)


async def capture(
    argv: list[str],
    *,
    time_limit: float,
    priority: Priority = Priority.NORMAL,
) -> bytes:
    """Run `argv` to completion in a thread and hand back everything it wrote, uncapped.

    For a tool whose output IS the work (every frame of a GIF), where `run`'s cap would silently
    hand back a short answer. Holds a thread for the whole run. A non-zero exit raises, so an
    unreadable file is not read as an empty one; stderr is discarded.
    """

    def go() -> bytes:
        # Blocking, on a thread, on purpose: no event loop and no child watcher. Started in its own
        # job like every other tool, so the memory limit and the kill reach whatever it starts.
        try:
            process = subprocess.Popen(  # noqa: S603 (a list, never a shell; see the module header)
                [*launch_prefix(priority), *argv],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags(priority),
            )
        except OSError as exc:
            raise SubprocessError(f"could not run {argv[0]!r}") from exc
        try:
            step_aside(process, priority)
            _contain(
                process, memory_limit_for(priority), background=priority is Priority.BACKGROUND
            )
            try:
                output, _ = process.communicate(timeout=time_limit)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.communicate()
                raise SubprocessError(f"{argv[0]!r} took too long and was stopped") from exc
        finally:
            _release(process)
        if process in _OVER_MEMORY:
            raise SubprocessError(_over_memory(argv))
        if process.returncode != 0:
            raise SubprocessError(f"{argv[0]!r} failed with exit code {process.returncode}")
        return output or b""

    return await asyncio.to_thread(go)


async def _collect(
    process: subprocess.Popen[bytes],
    stdin: bytes | None = None,
    on_line: OnLine | None = None,
) -> tuple[bytes, bytes]:
    """Read both streams to EOF, each bounded, then reap. The reads run together (reading one to
    the end while the other's pipe fills would deadlock the tool), the way `communicate` does, but
    holding at most the cap from each. Writing the input runs alongside them for the same reason:
    a tool that answers while it is still being fed would otherwise fill its output pipe and stop,
    while this side was still waiting to finish writing.

    The three waits run on threads of the tool's own, never the shared pool. They are needed all at
    once, and a pool with fewer free threads than that hands out only some of them: the feed waits
    on a tool that waits on an output nobody is reading, and the tool sits stopped until its time
    limit kills it."""
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
    """Hand the tool its input and close it. A tool that stops reading part-way (because it has
    all it needs, or has failed) breaks the pipe, which is an ordinary end rather than a fault."""
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
    """Whatever the tool has written so far, up to a chunk. Empty at the end of the stream.

    The raw descriptor rather than the buffered file: a buffered read of N bytes waits until N
    have arrived, which on a tool reporting one line a second would hold each line back until
    sixty-four kilobytes of them had piled up. The descriptor hands over what is there.
    """
    return os.read(pipe.fileno(), _READ_CHUNK)


async def _capped_read(
    stream: IO[bytes] | None, threads: Executor, on_line: OnLine | None = None
) -> bytes:
    """Read a stream to EOF but keep at most the cap. Past it the rest is read and discarded, so the
    tool never blocks on a full pipe while the memory held stays bounded.

    With `on_line`, each complete line is also handed over as it arrives. The reading is unchanged:
    same chunks, same cap, same discard past it, and the lines are cut out of the chunks on the
    way past, so watching a tool report on itself costs a split and nothing else. A caller that
    raises is not allowed to take the read down with it: the tool would then be left writing into a
    pipe nobody is reading, which is a hang rather than an error.

    Each piece is read on one of `threads` and handed back here, so the lines are still handed over
    on the loop: a watcher is ordinary asynchronous code and may touch anything asynchronous code
    may.
    """
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
    """Hand over every complete line in `text` and return the incomplete tail.

    Split on carriage returns as well as newlines, because a tool drawing a progress bar rewrites
    one line in place and emits no newline at all until it is finished.
    """
    parts = _LINE_BREAK.split(text)
    tail = parts.pop()
    for part in parts:
        if part.strip():
            _tell(on_line, part)
    return tail


def _tell(on_line: OnLine, line: str) -> None:
    """Hand one line over, swallowing whatever the caller does with it.

    A caller that raises must not stop the stream being read: the tool is still writing, and a
    reader that stopped would leave it blocked on a full pipe, turning a bad progress line into a
    hung download.
    """
    with contextlib.suppress(Exception):
        on_line(line.strip())


async def _kill(process: subprocess.Popen[bytes] | asyncio.subprocess.Process) -> None:
    """Kill a tool and reap it, tolerating one that has already exited.

    Both handle shapes, because the streaming runner below still holds the loop's own kind. A
    killed tool closes its pipes, which is what lets any read still waiting on them return.

    This kills the tool's own process. What IT started is ended by `_release`, which `run` calls
    straight after: closing a job made to kill on close ends everything still in it. Not
    `TerminateJobObject` as well: a kill landing on a process part-way through terminating reads
    its exit code early, so `wait` answers while the tool is still running.
    """
    with contextlib.suppress(ProcessLookupError):
        process.kill()
    if isinstance(process, subprocess.Popen):
        await asyncio.to_thread(process.wait)
        return
    with contextlib.suppress(ProcessLookupError):
        await process.wait()


def _popen_of(process: asyncio.subprocess.Process) -> subprocess.Popen[bytes] | None:
    """The `Popen` behind a loop-launched tool, which is what a job is made from. None where the
    loop does not say."""
    transport = getattr(process, "_transport", None)
    found = None if transport is None else transport.get_extra_info("subprocess")
    return found if isinstance(found, subprocess.Popen) else None


async def _launch_streaming(
    argv: list[str], priority: Priority, *, stdin: bytes | None
) -> tuple[asyncio.subprocess.Process, subprocess.Popen[bytes] | None]:
    """Start a streamed tool on the loop, and the `Popen` behind it where the loop says.

    Contained like `run`'s and `capture`'s tools: in its own job, held to the background share at
    that priority, and ended with whatever it started when the job closes.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *launch_prefix(priority),
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=creation_flags(priority),
        )
    except OSError as exc:
        raise SubprocessError(f"could not run {argv[0]!r}") from exc
    held = _popen_of(process)
    if held is not None:
        step_aside(held, priority)
        await asyncio.to_thread(
            _contain, held, memory_limit_for(priority), background=priority is Priority.BACKGROUND
        )
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
    """Run a tool and hand back its output in fixed-size pieces as it arrives.

    Memory stays at one piece of `frame_bytes`; a trailing part-piece is dropped. The time limit
    covers the whole run. Closing the generator kills the tool. It launches on the loop, once per
    tool, because what streams is one tool a person is waiting on.
    """
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
        # The tool finished. Whatever is left is shorter than a whole piece and is not one.
        finished = True
        await process.wait()
    except TimeoutError:
        raise SubprocessError(f"{argv[0]!r} took too long and was stopped") from None
    finally:
        feeding.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await feeding
        # Waited for only when it has not already been reaped. Asking a second time is not
        # harmless: the loop reports a made-up status for a process whose real one it has already
        # collected, which would turn a perfectly good run into a failure at random.
        if process.returncode is None:
            await _kill(process)
        if held is not None:
            _release(held)

    if held is not None and held in _OVER_MEMORY:
        raise SubprocessError(_over_memory(argv))
    # A tool that ran to the end and reported a failure produced no output because it could not,
    # not because there was none, and those two are indistinguishable from the pieces alone. Only
    # checked when the stream ran out on its own: a caller that stopped reading early killed the
    # tool, and a killed tool's exit status says nothing about the work.
    if finished and process.returncode:
        raise SubprocessError(f"{argv[0]!r} failed with status {process.returncode}")


__all__ = [
    "LongLivedChild",
    "Priority",
    "SubprocessError",
    "SubprocessResult",
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
    "set_machine_memory",
    "start_long_lived",
    "step_aside",
    "stream",
]
