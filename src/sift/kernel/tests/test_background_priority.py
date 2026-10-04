# SPDX-License-Identifier: AGPL-3.0-or-later
"""A background child's disk and memory priority: set on Windows, tolerated when refused.

The processor half (the below-normal class) has its tests in `test_subprocess.py`. These are the
other two halves: the I/O priority and the memory priority, set on the child's handle right after
it starts. A stand-in for ntdll shows the two calls are made with the right values; a real child
on Windows reads its own priorities back, which is the only proof the kernel accepted them.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from typing import Any

import pytest

from sift.kernel import subprocess as sp
from sift.testing.tools import ON_WINDOWS


class _Recorder:
    """ntdll's set call, recording what it was asked and answering with `status`."""

    def __init__(self, status: int = 0) -> None:
        self.status = status
        self.calls: list[tuple[int, int, int, int]] = []

    def NtSetInformationProcess(
        self, handle: int, information: int, pointer: Any, size: int
    ) -> int:
        self.calls.append((handle, information, pointer._obj.value, size))
        return self.status


@pytest.fixture(autouse=True)
def _forget_the_logged_refusal() -> Any:
    sp._STEP_ASIDE_FAILED_LOGGED = False
    yield
    sp._STEP_ASIDE_FAILED_LOGGED = False


def test_the_io_priority_is_very_low_and_the_memory_priority_is_very_low() -> None:
    api = _Recorder()
    assert sp.lower_disk_and_memory(0x1234, api) is True
    assert api.calls == [
        (0x1234, 33, sp.IO_PRIORITY_VERY_LOW, 4),
        (0x1234, 39, sp.MEMORY_PRIORITY_VERY_LOW, 4),
    ]
    assert (sp.IO_PRIORITY_VERY_LOW, sp.MEMORY_PRIORITY_VERY_LOW) == (0, 1)


def test_a_refusal_is_tolerated_and_written_down_once() -> None:
    api = _Recorder(status=-1073741790)  # STATUS_ACCESS_DENIED
    assert sp.lower_disk_and_memory(7, api) is False
    assert sp._STEP_ASIDE_FAILED_LOGGED is True
    # Both halves were still asked for: one refused never stops the other being tried.
    assert [call[1] for call in api.calls] == [33, 39]
    assert sp.lower_disk_and_memory(7, api) is False


def test_nothing_is_lowered_where_there_is_no_ntdll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sp, "_process_api", lambda: None)
    assert sp.lower_disk_and_memory(7) is False
    assert sp.read_disk_and_memory(7) is None


def test_a_normal_priority_child_is_never_stepped_aside(monkeypatch: pytest.MonkeyPatch) -> None:
    lowered: list[int] = []
    monkeypatch.setattr(
        sp, "lower_disk_and_memory", lambda handle, api=None: lowered.append(handle)
    )
    monkeypatch.setattr(sp, "_UNIX_PRIORITY_TOOLS", False)

    class Held:
        _handle = 42

    sp.step_aside(Held(), sp.Priority.NORMAL)  # type: ignore[arg-type]
    assert lowered == []
    sp.step_aside(Held(), sp.Priority.BACKGROUND)  # type: ignore[arg-type]
    assert lowered == [42]


def test_a_child_with_no_handle_to_name_it_by_is_left_as_it_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows names a process by its handle; a child without one is not guessed at."""
    lowered: list[int] = []
    monkeypatch.setattr(
        sp, "lower_disk_and_memory", lambda handle, api=None: lowered.append(handle)
    )
    monkeypatch.setattr(sp, "_UNIX_PRIORITY_TOOLS", False)

    class Unnamed:
        pass

    sp.step_aside(Unnamed(), sp.Priority.BACKGROUND)  # type: ignore[arg-type]
    assert lowered == []


class _Asked:
    """ntdll's query call, answering `values` in turn, or refusing with `status`."""

    def __init__(self, values: tuple[int, int], status: int = 0) -> None:
        self.values = list(values)
        self.status = status

    def NtQueryInformationProcess(
        self, handle: int, information: int, pointer: Any, size: int, written: Any
    ) -> int:
        if self.status:
            return self.status
        pointer._obj.value = self.values.pop(0)
        return 0


def test_the_priorities_are_read_in_order_and_a_refused_read_is_no_answer() -> None:
    """A half answer would read as a priority the child does not have, so a refusal of either
    question is None rather than a pair with a zero in it."""
    assert sp.read_disk_and_memory(7, _Asked((0, 1))) == (0, 1)
    assert sp.read_disk_and_memory(7, _Asked((0, 1), status=-1073741790)) is None


def test_there_is_no_ntdll_off_windows_or_where_it_will_not_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuses(*args: object, **kwargs: object) -> object:
        raise OSError("ntdll would not load")

    sp._process_api.cache_clear()
    try:
        monkeypatch.setattr(sys, "platform", "linux")
        assert sp._process_api() is None
        sp._process_api.cache_clear()
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(ctypes, "WinDLL", refuses, raising=False)
        assert sp._process_api() is None
    finally:
        monkeypatch.undo()
        sp._process_api.cache_clear()


#: A child that waits a moment (the launcher sets its priorities just after it starts) and then
#: prints its own I/O and memory priority, asked of the running process itself.
_READS_ITS_OWN = (
    "import ctypes, sys, time;"
    "time.sleep(1.5);"
    "n = ctypes.WinDLL('ntdll');"
    "n.NtQueryInformationProcess.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,"
    " ctypes.c_ulong, ctypes.c_void_p];"
    "me = ctypes.c_void_p(-1);"
    "io = ctypes.c_ulong(9); page = ctypes.c_ulong(9);"
    "n.NtQueryInformationProcess(me, 33, ctypes.byref(io), 4, None);"
    "n.NtQueryInformationProcess(me, 39, ctypes.byref(page), 4, None);"
    "sys.stdout.write(f'{io.value} {page.value}')"
)

WINDOWS_ONLY = pytest.mark.skipif(not ON_WINDOWS, reason="I/O and memory priority are Windows'")


@WINDOWS_ONLY
@pytest.mark.integration
async def test_a_background_child_reads_very_low_disk_and_memory_priority() -> None:
    result = await sp.run(
        [sys.executable, "-c", _READS_ITS_OWN], time_limit=30, priority=sp.Priority.BACKGROUND
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode() == f"{sp.IO_PRIORITY_VERY_LOW} {sp.MEMORY_PRIORITY_VERY_LOW}"


@WINDOWS_ONLY
@pytest.mark.integration
async def test_a_normal_child_keeps_normal_disk_and_memory_priority() -> None:
    """The other half, so the test above cannot pass on a machine that lowers everything."""
    result = await sp.run([sys.executable, "-c", _READS_ITS_OWN], time_limit=30)
    assert result.returncode == 0, result.stderr
    io, page = (int(part) for part in result.stdout.decode().split())
    assert io > sp.IO_PRIORITY_VERY_LOW
    assert page > sp.MEMORY_PRIORITY_VERY_LOW


@WINDOWS_ONLY
def test_the_priorities_read_back_through_the_handle() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    try:
        sp.step_aside(child)
        assert sp.read_disk_and_memory(int(child._handle)) == (  # type: ignore[attr-defined]
            sp.IO_PRIORITY_VERY_LOW,
            sp.MEMORY_PRIORITY_VERY_LOW,
        )
    finally:
        child.kill()
        child.wait()


def test_the_stand_in_reads_the_value_it_was_handed() -> None:
    """The recorder reads through the pointer the real call is handed, so it sees what was sent."""
    value = ctypes.c_ulong(5)
    api = _Recorder()
    api.NtSetInformationProcess(1, 33, ctypes.byref(value), 4)
    assert api.calls == [(1, 33, 5, 4)]
