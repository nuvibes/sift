# SPDX-License-Identifier: AGPL-3.0-or-later
"""How busy other programs keep this device, with Sift's own work taken out.

Read on Windows on the worker pool's reconfigure; off elsewhere, as `kernel.attention` is.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import weakref
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from sift.kernel import subprocess as tools
from sift.kernel.attention import ATTENTION_SECONDS
from sift.kernel.log import get_logger

log = get_logger(__name__)

#: Other programs' share of the whole processor that counts as busy. A placeholder until measured.
BUSY_PROCESSOR = 25.0
#: Other programs' use of any one graphics engine that counts as busy. A placeholder until measured.
BUSY_ENGINE = 30.0
#: Free memory under this share of the whole counts as busy, whoever holds it. A placeholder.
LOW_MEMORY = 10.0
#: Readings over a line in a row before it counts, so one spike never steps back.
BUSY_READINGS = 2
#: Quiet is under this fraction of each line (and memory this far clear of its floor).
QUIET_FRACTION = 0.5
#: How long quiet must hold before the work comes back: the same patience as for input.
QUIET_SECONDS = ATTENTION_SECONDS
#: A graphics read slower than this is taken only every `ENGINE_EVERY` readings.
SLOW_ENGINE_MS = 20.0
ENGINE_EVERY = 3

_ENGINE = re.compile(r"pid_(\d+)_luid_(\w+?)_phys_(\d+)_eng_(\d+)_engtype_(.*)$")


@dataclass(frozen=True, slots=True)
class Load:
    """One reading: shares of the device in percent, and what the reading itself cost."""

    processor: float
    own: float
    engines: dict[str, float] = field(default_factory=dict)
    memory_free: float = 100.0
    cost_ms: float = 0.0

    @property
    def engine(self) -> float:
        return max(self.engines.values(), default=0.0)

    def busy(self) -> list[str]:
        """Which lines this reading is over."""
        over = []
        if self.processor >= BUSY_PROCESSOR:
            over.append("processor")
        if self.engine >= BUSY_ENGINE:
            over.append("graphics")
        if self.memory_free < LOW_MEMORY:
            over.append("memory")
        return over

    def quiet(self) -> bool:
        return (
            self.processor < BUSY_PROCESSOR * QUIET_FRACTION
            and self.engine < BUSY_ENGINE * QUIET_FRACTION
            and self.memory_free >= LOW_MEMORY * (2 - QUIET_FRACTION)
        )

    def said(self) -> dict[str, object]:
        """The figures a log line carries."""
        return {
            "processor_others": round(self.processor, 1),
            "processor_sift": round(self.own, 1),
            "engines_others": {
                name: round(value, 1) for name, value in self.engines.items() if value >= 0.1
            },
            "memory_free": round(self.memory_free, 1),
            "cost_ms": round(self.cost_ms, 2),
        }


def others_engines(
    instances: Iterable[tuple[str, float]], own: frozenset[int] | set[int]
) -> dict[str, float]:
    """Each engine type's busiest engine, counting only processes that are not Sift's."""
    per_engine: dict[tuple[str, str, str, str], float] = {}
    for name, value in instances:
        found = _ENGINE.search(name)
        if found is None or int(found[1]) in own:
            continue
        key = (found[2], found[3], found[4], found[5])
        per_engine[key] = per_engine.get(key, 0.0) + value
    busiest: dict[str, float] = {}
    for (_luid, _phys, _eng, kind), value in per_engine.items():
        busiest[kind] = max(busiest.get(kind, 0.0), min(100.0, value))
    return busiest


# --- Sift's own processes besides the server and its tools ------------------------------------

#: The model processes, weakly, so one dropped without `child_ended` is never kept alive.
_CHILDREN: weakref.WeakSet[subprocess.Popen[bytes]] = weakref.WeakSet()
#: Processor time of model processes that have ended, in 100 ns units.
_children_ended = 0


def own_child(process: subprocess.Popen[bytes]) -> None:
    """Count a long-running child of Sift's (the model process) as Sift's own work."""
    _CHILDREN.add(process)


def child_ended(process: subprocess.Popen[bytes]) -> None:
    """Keep an ended child's processor time, once, so its work does not vanish from the sum."""
    global _children_ended
    if process not in _CHILDREN:
        return
    _CHILDREN.discard(process)
    api = _windows()
    if api is not None:
        _children_ended += api.process_time(_handle(process))


def _handle(process: subprocess.Popen[bytes]) -> int:
    """The process handle `subprocess` holds, or 0 for something that is not a real child."""
    return int(getattr(process, "_handle", 0))


# --- the Windows calls ------------------------------------------------------------------------


class _Windows:
    """The four readings, typed. `pdh` is None where the graphics counters cannot be opened."""

    def __init__(self, kernel32: Any, pdh: Any) -> None:
        import ctypes

        self._k = kernel32
        self._pdh = pdh
        self._query = ctypes.c_void_p()
        self._counter = ctypes.c_void_p()
        if pdh is not None and not self._open_counters():
            self._pdh = None

    def _open_counters(self) -> bool:
        import ctypes

        if self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) != 0:
            return False
        path = "\\GPU Engine(*)\\Utilization Percentage"
        if self._pdh.PdhAddEnglishCounterW(self._query, path, 0, ctypes.byref(self._counter)):
            self._pdh.PdhCloseQuery(self._query)
            return False
        # The first collect is the baseline the next one's rate is measured from.
        self._pdh.PdhCollectQueryData(self._query)
        return True

    def system_times(self) -> tuple[int, int]:
        """The whole device's busy and total processor time, in 100 ns units, every core added."""
        import ctypes

        idle, kernel, user = ctypes.c_uint64(), ctypes.c_uint64(), ctypes.c_uint64()
        self._k.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        total = kernel.value + user.value
        return total - idle.value, total

    def process_time(self, handle: int) -> int:
        """A process's processor time so far, in 100 ns units; 0 when it cannot be read."""
        import ctypes

        times = [ctypes.c_uint64() for _ in range(4)]
        if not self._k.GetProcessTimes(handle, *(ctypes.byref(one) for one in times)):
            return 0
        return int(times[2].value + times[3].value)

    def own_time(self) -> int:
        return self.process_time(self._k.GetCurrentProcess())

    def memory_free(self) -> float:
        """Available physical memory, in percent of the whole."""
        import ctypes

        status = _structures()[0]()
        status.dwLength = ctypes.sizeof(status)
        if not self._k.GlobalMemoryStatusEx(ctypes.byref(status)) or not status.ullTotalPhys:
            return 100.0
        return float(100.0 * status.ullAvailPhys / status.ullTotalPhys)

    def engines(self) -> list[tuple[str, float]] | None:
        """Every graphics engine instance's use since the last call, or None where unreadable."""
        if self._pdh is None:
            return None
        import ctypes
        from ctypes import wintypes

        if self._pdh.PdhCollectQueryData(self._query) != 0:
            return None
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        flags = _PDH_FMT_DOUBLE | _PDH_FMT_NOCAP100
        self._pdh.PdhGetFormattedCounterArrayW(
            self._counter, flags, ctypes.byref(size), ctypes.byref(count), None
        )
        if not size.value:
            return []
        buffer = ctypes.create_string_buffer(size.value)
        if self._pdh.PdhGetFormattedCounterArrayW(
            self._counter, flags, ctypes.byref(size), ctypes.byref(count), buffer
        ):
            return None
        items: Any = ctypes.cast(buffer, ctypes.POINTER(_structures()[1]))
        return [
            (str(items[i].szName), float(items[i].FmtValue.doubleValue))
            for i in range(count.value)
            if items[i].FmtValue.CStatus in (0, 1)
        ]


_PDH_FMT_DOUBLE = 0x200
_PDH_FMT_NOCAP100 = 0x8000


@cache
def _structures() -> tuple[Any, Any]:
    """The memory status and the counter item, made on first use: Windows' own types."""
    import ctypes
    from ctypes import wintypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = (
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        )

    class Value(ctypes.Structure):
        _fields_ = (("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double))

    class Item(ctypes.Structure):
        _fields_ = (("szName", wintypes.LPWSTR), ("FmtValue", Value))

    return MemoryStatus, Item


@cache
def _windows() -> _Windows | None:
    """The calls, typed, or None off Windows."""
    if sys.platform != "win32":
        return None  # pragma: no cover (the other operating system's branch)
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined, unused-ignore]
    filetime = ctypes.POINTER(ctypes.c_uint64)
    kernel32.GetSystemTimes.argtypes = [filetime, filetime, filetime]
    kernel32.GetSystemTimes.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, filetime, filetime, filetime, filetime]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
    return _Windows(kernel32, _typed_pdh())


def _typed_pdh() -> Any:
    """The performance counter library with the five calls typed, or None where it will not load."""
    import ctypes
    from ctypes import wintypes

    try:
        pdh: Any = ctypes.WinDLL("pdh")  # type: ignore[attr-defined, unused-ignore]
    except OSError:  # pragma: no cover (every Windows since Vista has it)
        return None
    handle = ctypes.c_void_p
    pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(handle)]
    pdh.PdhAddEnglishCounterW.argtypes = [
        handle,
        wintypes.LPCWSTR,
        ctypes.c_size_t,
        ctypes.POINTER(handle),
    ]
    pdh.PdhCollectQueryData.argtypes = [handle]
    pdh.PdhCloseQuery.argtypes = [handle]
    pdh.PdhGetFormattedCounterArrayW.argtypes = [
        handle,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    for name in (
        "PdhOpenQueryW",
        "PdhAddEnglishCounterW",
        "PdhCollectQueryData",
        "PdhCloseQuery",
        "PdhGetFormattedCounterArrayW",
    ):
        getattr(pdh, name).restype = wintypes.LONG
    return pdh


# --- the reader and its rule ------------------------------------------------------------------


class DeviceLoad:
    """Reads the device each tick and says whether other programs are busy.

    Logs each moment it would step back and come back, whether or not the setting lets it act.
    """

    def __init__(
        self,
        api: Callable[[], Any] = _windows,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api = api
        self._clock = clock
        self._last: tuple[int, int, int] | None = None
        self._engines: dict[str, float] = {}
        self._engine_wait = 0
        self._over = 0
        self._quiet_since: float | None = None
        self.busy = False
        self.latest: Load | None = None

    def read(self) -> Load | None:
        """The load since the last reading, or None on the first one and off Windows."""
        api = self._api()
        if api is None:
            return None
        began = time.perf_counter()
        own_tools, tool_ids = tools.tools_time()
        children = list(_CHILDREN)
        own = (
            api.own_time()
            + own_tools
            + _children_ended
            + sum(api.process_time(_handle(one)) for one in children)
        )
        busy, total = api.system_times()
        engines = self._read_engines(
            api, {os.getpid(), *tool_ids, *(getattr(c, "pid", 0) for c in children)}
        )
        memory = api.memory_free()
        last, self._last = self._last, (busy, total, own)
        if last is None or total <= last[1]:
            return None
        span = total - last[1]
        sift = max(0, own - last[2])
        others = max(0, busy - last[0] - sift)
        cost = (time.perf_counter() - began) * 1000
        self.latest = Load(
            processor=min(100.0, 100.0 * others / span),
            own=min(100.0, 100.0 * sift / span),
            engines=engines,
            memory_free=memory,
            cost_ms=cost,
        )
        return self.latest

    def _read_engines(self, api: Any, own: set[int]) -> dict[str, float]:
        if self._engine_wait > 0:
            self._engine_wait -= 1
            return self._engines
        began = time.perf_counter()
        instances = api.engines()
        took = (time.perf_counter() - began) * 1000
        self._engines = {} if instances is None else others_engines(instances, frozenset(own))
        if took > SLOW_ENGINE_MS:
            self._engine_wait = ENGINE_EVERY - 1
        return self._engines

    def tick(self, *, acting: bool) -> bool:
        """Read once and judge: True while other programs keep the device busy."""
        load = self.read()
        if load is not None:
            self.judge(load, acting=acting)
        return self.busy

    def judge(self, load: Load, *, acting: bool) -> None:
        over = load.busy()
        self._over = self._over + 1 if over else 0
        now = self._clock()
        if not self.busy:
            if self._over >= BUSY_READINGS:
                self.busy = True
                self._quiet_since = None
                log.info("device_load.others_busy", over=over, acting=acting, **load.said())
            return
        if not load.quiet():
            self._quiet_since = None
            return
        if self._quiet_since is None:
            self._quiet_since = now
        if now - self._quiet_since >= QUIET_SECONDS:
            self.busy = False
            self._over = 0
            log.info("device_load.others_quiet", acting=acting, **load.said())


#: The one reader the worker pool ticks.
READER = DeviceLoad()


__all__ = [
    "BUSY_ENGINE",
    "BUSY_PROCESSOR",
    "BUSY_READINGS",
    "LOW_MEMORY",
    "QUIET_SECONDS",
    "READER",
    "DeviceLoad",
    "Load",
    "child_ended",
    "others_engines",
    "own_child",
]
