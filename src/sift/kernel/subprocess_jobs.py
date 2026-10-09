# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Windows job object a tool is held in: its limits, its memory watch, its rate and its time."""

from __future__ import annotations

import subprocess
import threading
import weakref
from functools import cache
from typing import Any

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


#: The information class for a job's processor rate.
_CPU_RATE_INFORMATION = 15
#: Its flags: the rate is on, and it is a ceiling rather than a weight among jobs.
_CPU_RATE_ON = 0x1
_CPU_RATE_HARD_CAP = 0x4


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
