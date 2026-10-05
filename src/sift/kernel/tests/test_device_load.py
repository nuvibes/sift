# SPDX-License-Identifier: AGPL-3.0-or-later
"""Other programs' load, read with Sift's own work taken out, and the rule that judges it."""

from __future__ import annotations

import ctypes
import subprocess
import sys
from typing import Any

import pytest
from structlog.testing import capture_logs

from sift.kernel import device_load
from sift.kernel import subprocess as tools
from sift.kernel.device_load import (
    BUSY_ENGINE,
    BUSY_PROCESSOR,
    LOW_MEMORY,
    QUIET_SECONDS,
    DeviceLoad,
    Load,
    others_engines,
)
from sift.testing.logs import uncached_log
from sift.testing.tools import ON_WINDOWS, REAL_PYTHON

WINDOWS_ONLY = pytest.mark.skipif(not ON_WINDOWS, reason="the readings are Windows' own")


def _engine(pid: int, eng: int, kind: str, luid: str = "0x0_0x1") -> str:
    return f"pid_{pid}_luid_{luid}_phys_0_eng_{eng}_engtype_{kind}"


def test_each_engine_adds_up_other_programs_and_leaves_sifts_out() -> None:
    found = others_engines(
        [
            (_engine(10, 0, "3D"), 20.0),
            (_engine(11, 0, "3D"), 15.0),
            (_engine(99, 0, "3D"), 60.0),
            (_engine(10, 1, "3D"), 5.0),
            (_engine(12, 3, "VideoEncode"), 70.0),
            (_engine(13, 3, "VideoEncode"), 50.0),
            ("not an engine", 90.0),
        ],
        {99},
    )
    assert found == {"3D": 35.0, "VideoEncode": 100.0}


def test_a_reading_is_busy_over_any_line_and_quiet_only_under_half_of_every_one() -> None:
    assert Load(processor=BUSY_PROCESSOR, own=0).busy() == ["processor"]
    assert Load(processor=0, own=90, engines={"3D": BUSY_ENGINE}).busy() == ["graphics"]
    assert Load(processor=0, own=0, memory_free=LOW_MEMORY - 1).busy() == ["memory"]
    assert Load(processor=BUSY_PROCESSOR / 2 - 1, own=80).quiet() is True
    assert Load(processor=BUSY_PROCESSOR / 2, own=0).quiet() is False
    assert Load(processor=0, own=0, memory_free=LOW_MEMORY * 1.2).quiet() is False


class _Api:
    """Cumulative processor times and the other readings, as a test moves them."""

    def __init__(self) -> None:
        self.busy, self.total, self.own, self.memory = 0, 0, 0, 50.0
        self.instances: list[tuple[str, float]] | None = []
        self.engine_reads = 0
        self.slow = 0.0

    def own_time(self) -> int:
        return self.own

    def process_time(self, handle: int) -> int:
        return 0

    def system_times(self) -> tuple[int, int]:
        return self.busy, self.total

    def memory_free(self) -> float:
        return self.memory

    def engines(self) -> list[tuple[str, float]] | None:
        self.engine_reads += 1
        if self.slow:
            import time

            time.sleep(self.slow)
        return self.instances

    def advance(self, *, busy: int, own: int, total: int = 1000) -> None:
        self.busy += busy
        self.own += own
        self.total += total


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def no_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "tools_time", lambda: (0, frozenset()))


def test_sifts_own_processor_time_is_taken_out_of_the_whole(no_tools: None) -> None:
    api = _Api()
    reader = DeviceLoad(lambda: api)
    assert reader.read() is None, "the first reading has nothing to measure from"
    api.advance(busy=700, own=400)
    load = reader.read()
    assert load is not None
    assert (load.processor, load.own) == (30.0, 40.0)
    # Sift's count running ahead of the device's never makes others negative.
    api.advance(busy=100, own=300)
    load = reader.read()
    assert load is not None and load.processor == 0.0
    # A clock that did not move measures nothing.
    api.advance(busy=0, own=0, total=0)
    assert reader.read() is None


def test_off_windows_there_is_nothing_to_read() -> None:
    reader = DeviceLoad(lambda: None)
    assert reader.read() is None
    assert reader.tick(acting=True) is False


def test_a_slow_graphics_read_is_taken_only_every_few_readings(no_tools: None) -> None:
    api = _Api()
    api.instances = [(_engine(5, 0, "3D"), 40.0)]
    api.slow = (device_load.SLOW_ENGINE_MS + 5) / 1000
    reader = DeviceLoad(lambda: api)
    for _ in range(device_load.ENGINE_EVERY + 1):
        api.advance(busy=0, own=0)
        reader.read()
    assert api.engine_reads == 2
    assert reader.latest is not None and reader.latest.engines == {"3D": 40.0}
    api.instances = None
    api.slow = 0
    for _ in range(device_load.ENGINE_EVERY):
        api.advance(busy=0, own=0)
        reader.read()
    assert reader.latest is not None and reader.latest.engines == {}


def test_two_busy_readings_step_back_and_a_quiet_minute_comes_back(
    no_tools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    uncached_log(monkeypatch, device_load)
    api = _Api()
    clock = _Clock()
    reader = DeviceLoad(lambda: api, clock)
    reader.tick(acting=False)
    api.advance(busy=500, own=100)
    assert reader.tick(acting=False) is False, "one spike is not busy"
    api.advance(busy=100, own=0)
    assert reader.tick(acting=False) is False, "a quiet reading starts the count again"
    with capture_logs() as logs:
        for _ in range(2):
            api.advance(busy=500, own=100)
            busy = reader.tick(acting=False)
    assert busy is True
    assert [(one["event"], one["processor_others"], one["acting"]) for one in logs] == [
        ("device_load.others_busy", 40.0, False)
    ]
    # Quiet, then busy again before the minute: still stepped back, and the minute starts over.
    api.advance(busy=50, own=0)
    reader.tick(acting=False)
    clock.now += QUIET_SECONDS - 1
    api.advance(busy=500, own=0)
    assert reader.tick(acting=False) is True
    api.advance(busy=50, own=0)
    reader.tick(acting=False)
    clock.now += QUIET_SECONDS - 1
    api.advance(busy=50, own=0)
    assert reader.tick(acting=False) is True
    clock.now += 1
    api.advance(busy=50, own=0)
    with capture_logs() as logs:
        assert reader.tick(acting=True) is False
    assert [(one["event"], one["acting"]) for one in logs] == [("device_load.others_quiet", True)]


def test_a_busy_hour_writes_a_line_a_minute_and_quiet_says_how_long_it_lasted(
    no_tools: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    uncached_log(monkeypatch, device_load)
    api = _Api()
    clock = _Clock()
    reader = DeviceLoad(lambda: api, clock)
    reader.tick(acting=False)
    for _ in range(2):
        api.advance(busy=500, own=100)
        reader.tick(acting=False)
    assert reader.over == ["processor"]
    with capture_logs() as logs:
        for step in range(1200):
            clock.now += 3
            # Over the line, then under it but not quiet: the spell holds and keeps what it was over.
            api.advance(busy=500 if step % 2 else 300, own=100)
            reader.tick(acting=False)
    assert reader.over == ["processor"]
    lines = [one for one in logs if one["event"] == "device_load.others_still_busy"]
    assert len(lines) == 3600 / device_load.SHADOW_EVERY
    first = lines[0]
    assert (first["busy_seconds"], first["readings"], first["acting"]) == (60, 20, False)
    assert first["processor_others"] == 30.0
    assert lines[-1]["busy_seconds"] == 3600
    with capture_logs() as logs:
        for _ in range(21):
            clock.now += 3
            api.advance(busy=50, own=0)
            reader.tick(acting=False)
    assert [one["busy_seconds"] for one in logs if one["event"].endswith("quiet")] == [3663]
    assert reader.over == []


def test_several_readings_sum_to_the_mean_the_peaks_and_the_least_memory() -> None:
    one = Load(processor=10, own=2, engines={"3D": 50.0}, memory_free=40, cost_ms=1)
    two = Load(processor=30, own=4, engines={"3D": 20.0, "VideoEncode": 70.0}, memory_free=30)
    both = device_load.summed([one, two])
    assert (both.processor, both.own, both.memory_free, both.cost_ms) == (20.0, 3.0, 30, 1)
    assert both.engines == {"3D": 50.0, "VideoEncode": 70.0}


def test_low_memory_is_busy_whoever_holds_it(no_tools: None) -> None:
    api = _Api()
    api.memory = LOW_MEMORY - 1
    reader = DeviceLoad(lambda: api, _Clock())
    for _ in range(3):
        api.advance(busy=0, own=0)
        reader.tick(acting=True)
    assert reader.busy is True


# --- the real readings ---------------------------------------------------------------------------


@WINDOWS_ONLY
def test_the_real_device_reads_in_range_and_cheaply() -> None:
    reader = DeviceLoad()
    reader.read()
    import time

    time.sleep(0.3)
    load = reader.read()
    assert load is not None
    assert 0 <= load.processor <= 100 and 0 <= load.own <= 100
    assert 0 < load.memory_free <= 100
    assert all(0 <= value <= 100 for value in load.engines.values())


@WINDOWS_ONLY
def test_an_ended_model_process_keeps_its_time_once() -> None:
    child = subprocess.Popen([REAL_PYTHON, "-c", "sum(range(3_000_000))"])
    device_load.own_child(child)
    child.wait(timeout=30)
    before = device_load._children_ended
    device_load.child_ended(child)
    after = device_load._children_ended
    assert after > before
    device_load.child_ended(child)
    assert device_load._children_ended == after, "counted twice"


# --- the Windows calls refusing ------------------------------------------------------------------


class _Pdh:
    def __init__(self, *, opens: int = 0, adds: int = 0, collects: int = 0, size: int = 0) -> None:
        self.opens, self.adds, self.collects, self.size = opens, adds, collects, size
        self.closed = False
        self.calls = 0

    def PdhOpenQueryW(self, *args: Any) -> int:
        return self.opens

    def PdhAddEnglishCounterW(self, *args: Any) -> int:
        return self.adds

    def PdhCloseQuery(self, *args: Any) -> int:
        self.closed = True
        return 0

    def PdhCollectQueryData(self, *args: Any) -> int:
        self.calls += 1
        return 0 if self.calls == 1 else self.collects

    def PdhGetFormattedCounterArrayW(self, counter: Any, flags: int, size: Any, *rest: Any) -> int:
        size._obj.value = self.size
        return 1


class _Kernel32:
    def GetProcessTimes(self, *args: Any) -> int:
        return 0

    def GlobalMemoryStatusEx(self, status: Any) -> int:
        return 0


@pytest.mark.skipif(sys.platform != "win32", reason="the structures are read with Windows' types")
def test_each_windows_call_that_refuses_reads_as_nothing_rather_than_failing() -> None:
    assert device_load._Windows(_Kernel32(), _Pdh(opens=1)).engines() is None
    refused = _Pdh(adds=1)
    assert device_load._Windows(_Kernel32(), refused).engines() is None
    assert refused.closed is True
    assert device_load._Windows(_Kernel32(), _Pdh(collects=1)).engines() is None
    assert device_load._Windows(_Kernel32(), _Pdh()).engines() == []
    assert device_load._Windows(_Kernel32(), _Pdh(size=64)).engines() is None
    api = device_load._Windows(_Kernel32(), None)
    assert api.process_time(1) == 0
    assert api.memory_free() == 100.0
    assert ctypes.sizeof(device_load._structures()[1]) == (24 if sys.maxsize > 2**32 else 16)


def test_a_child_never_counted_is_not_ended_twice() -> None:
    child: Any = type("Gone", (), {})()
    before = device_load._children_ended
    device_load.child_ended(child)
    assert device_load._children_ended == before


def test_off_windows_an_ended_child_adds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(device_load, "_windows", lambda: None)
    child: Any = type("Child", (), {})()
    device_load.own_child(child)
    before = device_load._children_ended
    device_load.child_ended(child)
    assert device_load._children_ended == before
    assert child not in device_load._CHILDREN
