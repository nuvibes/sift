# SPDX-License-Identifier: AGPL-3.0-or-later
"""Running an external tool: bounded output, killed on timeout, reaped on cancellation.

Real child processes are spawned (via the test interpreter), so the mechanics (the pipe reads, the
kill, the exit code) are exercised for real rather than mocked.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import threading
import time
import weakref
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from sift.kernel import subprocess as sp
from sift.testing.tools import ON_WINDOWS, REAL_PYTHON


async def test_small_output_is_returned_whole() -> None:
    result = await sp.run(
        [sys.executable, "-c", "import sys; sys.stdout.write('hi'); sys.stderr.write('lo')"],
        time_limit=10,
    )
    assert result.returncode == 0
    assert result.stdout == b"hi"
    assert result.stderr == b"lo"


async def test_stdout_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tool that writes far more than the cap does not fill memory: the capture is bounded and the
    rest is read and discarded so the tool still finishes rather than blocking on a full pipe."""
    monkeypatch.setattr(sp, "_MAX_OUTPUT_BYTES", 8)
    result = await sp.run(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1_000_000)"], time_limit=10
    )
    assert result.returncode == 0
    assert result.stdout == b"x" * 8  # held to the cap, not the megabyte written


async def test_stderr_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sp, "_MAX_OUTPUT_BYTES", 8)
    result = await sp.run(
        [sys.executable, "-c", "import sys; sys.stderr.write('e' * 1_000_000); sys.exit(3)"],
        time_limit=10,
    )
    assert result.returncode == 3
    assert result.stderr == b"e" * 8


async def test_stdout_can_be_discarded_entirely() -> None:
    result = await sp.run(
        [sys.executable, "-c", "print('noise')"], time_limit=10, capture_stdout=False
    )
    assert result.stdout == b""


async def test_a_tool_that_will_not_start_raises() -> None:
    with pytest.raises(sp.SubprocessError):
        await sp.run(["a-binary-that-does-not-exist-anywhere"], time_limit=10)


async def test_a_tool_that_overruns_is_stopped() -> None:
    with pytest.raises(sp.SubprocessError, match="too long"):
        await sp.run([sys.executable, "-c", "import time; time.sleep(30)"], time_limit=0.3)


@pytest.mark.integration
@pytest.mark.parametrize("priority", [sp.Priority.NORMAL, sp.Priority.BACKGROUND])
async def test_a_cancelled_run_kills_the_tool_rather_than_leaving_it_behind(
    tmp_path: Path, priority: sp.Priority
) -> None:
    """The reason every tool is spawned from one function instead of four.

    A caller cancelled mid-run that simply re-raised would leave the tool fetching or decoding in
    the background, an orphan nobody is waiting for and nothing will ever reap. So the child is
    killed on the way out and the cancellation continues.

    Run at both priorities on purpose. At the low one the thing actually launched is `nice`, which
    replaces itself with the tool, so the process id being killed is still the tool's. If that
    ever stopped being true, the kill would land on a wrapper that had already gone and the tool
    would survive its own cancellation, which is exactly the leak this is here to prevent.
    """
    marker = tmp_path / "pid"
    child = (
        "import os, pathlib, sys, time;"
        f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid()));"
        "sys.stdout.flush();"
        "time.sleep(60)"
    )

    task = asyncio.create_task(sp.run([REAL_PYTHON, "-c", child], time_limit=60, priority=priority))
    # Bounded rather than open-ended: a tool that never starts is a failure to report here, not a
    # test that hangs until the runner gives up on it.
    # WAITING FOR THE CONTENT, NOT FOR THE FILE. `write_text` creates the file and then writes to
    # it, so on a loaded machine `exists()` is true for a moment while it still holds nothing, and
    # the read that follows comes back empty and `int('')` raises inside the setup of a test about
    # something else entirely.
    said = ""
    for _ in range(500):
        said = marker.read_text().strip() if marker.exists() else ""
        if said:
            break
        await asyncio.sleep(0.02)
    assert said, "the tool never got as far as saying it had started"
    pid = int(said)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not _still_running(pid), "the tool was left behind"


def _still_running(pid: int) -> bool:
    """Whether that process id is still a live process. A question, on either site.

    `os.kill(pid, 0)` is the POSIX way to ask without touching. It is not a way to ask on Windows:
    every signal there but a console one becomes TerminateProcess, so the question would kill what
    it was asking about, and with zero it does not even get that far, it answers WinError 87.

    The Windows half asks the kernel for a handle and then whether that handle is signalled, which
    is true only once the process has ended. Gone means gone rather than merely killed: a
    killed-but-unreaped child is still a process id, and still a leak.
    """
    if not ON_WINDOWS:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    import ctypes

    synchronize = 0x0010_0000
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return False
    try:
        # Zero milliseconds: signalled already means it has ended, anything else means it has not.
        signalled: int = ctypes.windll.kernel32.WaitForSingleObject(handle, 0)
        return signalled != 0
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


POSIX_PRIORITY_ONLY = pytest.mark.skipif(
    ON_WINDOWS,
    reason=(
        "Reads the niceness back out of a real child with `os.nice`, which does not exist on "
        "Windows. The prefix is a Unix mechanism; Windows sets a priority class at creation "
        "instead, and the class has its own tests below. See `launch_prefix` and `creation_flags`."
    ),
)

WINDOWS_PRIORITY_ONLY = pytest.mark.skipif(
    not ON_WINDOWS, reason="a priority class is a Windows notion; the prefix is tested above"
)


def test_creation_flags_are_zero_at_normal_priority() -> None:
    """The ordinary path stays byte-for-byte what it was, on every site."""
    assert sp.creation_flags(sp.Priority.NORMAL) == 0


def test_creation_flags_name_the_below_normal_class_on_windows_only() -> None:
    flags = sp.creation_flags(sp.Priority.BACKGROUND)
    if ON_WINDOWS:
        import subprocess

        assert flags == subprocess.BELOW_NORMAL_PRIORITY_CLASS
    else:
        assert flags == 0


#: What a child reports its own class as. Asked of the running child rather than of the command
#: that started it, for the reason the nice test gives: a flag that was assembled and then refused
#: by the kernel looks identical from outside.
# The handle's type is declared on both sides. Left to ctypes' default (a C int), the
# pseudo-handle GetCurrentProcess answers with, -1 as a 64-bit HANDLE, was truncated on the way
# back and again on the way in, and GetPriorityClass answered 0 ("no such process") for every
# class, which read as the flag never having been applied.
_READS_ITS_OWN_CLASS = (
    "import ctypes, sys;"
    "k = ctypes.windll.kernel32;"
    "k.GetCurrentProcess.restype = ctypes.c_void_p;"
    "k.GetPriorityClass.argtypes = [ctypes.c_void_p];"
    "sys.stdout.write(str(k.GetPriorityClass(k.GetCurrentProcess())))"
)


def _own_class() -> int:
    """The priority class this process runs in, read the way the child reads its own."""
    import ctypes

    k = ctypes.windll.kernel32
    k.GetCurrentProcess.restype = ctypes.c_void_p
    k.GetPriorityClass.argtypes = [ctypes.c_void_p]
    k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    return int(k.GetPriorityClass(k.GetCurrentProcess()))


@pytest.fixture
def at_normal_class() -> Iterator[int]:
    """This process at the normal class for one test, and the class it is then in.

    A test run started below normal (a service, a scheduled task, `start /belownormal`) hands that
    class to every child it starts, so a background child would read below normal whether or not
    the flag was applied. Raising this process to normal first is what lets the pair tell them
    apart; a process may always move itself back up to normal.
    """
    import ctypes
    import subprocess

    k = ctypes.windll.kernel32
    before = _own_class()
    if before != subprocess.NORMAL_PRIORITY_CLASS:
        k.SetPriorityClass(k.GetCurrentProcess(), subprocess.NORMAL_PRIORITY_CLASS)
        if _own_class() != subprocess.NORMAL_PRIORITY_CLASS:
            # A service's job object can hold its processes below normal; from there the pair
            # cannot tell the flag from inheritance, and the test is the machine's, not this one's.
            pytest.skip("this process cannot raise itself to the normal class here")
    try:
        yield _own_class()
    finally:
        if before != subprocess.NORMAL_PRIORITY_CLASS:
            k.SetPriorityClass(k.GetCurrentProcess(), before)


@WINDOWS_PRIORITY_ONLY
@pytest.mark.integration
async def test_a_background_child_is_created_below_normal_on_windows(at_normal_class: int) -> None:
    import subprocess

    assert at_normal_class == subprocess.NORMAL_PRIORITY_CLASS
    result = await sp.run(
        [sys.executable, "-c", _READS_ITS_OWN_CLASS], time_limit=30, priority=sp.Priority.BACKGROUND
    )
    assert result.returncode == 0, result.stderr
    assert int(result.stdout.decode()) == subprocess.BELOW_NORMAL_PRIORITY_CLASS


@WINDOWS_PRIORITY_ONLY
@pytest.mark.integration
async def test_a_normal_child_keeps_its_parents_class_on_windows(at_normal_class: int) -> None:
    """The other half of the pair, so the test above cannot pass on a machine that runs everything
    below normal."""
    result = await sp.run([sys.executable, "-c", _READS_ITS_OWN_CLASS], time_limit=30)
    assert result.returncode == 0, result.stderr
    assert int(result.stdout.decode()) == at_normal_class


@WINDOWS_PRIORITY_ONLY
@pytest.mark.integration
async def test_a_captured_background_child_is_created_below_normal_too(
    at_normal_class: int,
) -> None:
    """`capture` launches by a different road and has to arrive at the same class."""
    import subprocess

    assert at_normal_class == subprocess.NORMAL_PRIORITY_CLASS
    said = await sp.capture(
        [sys.executable, "-c", _READS_ITS_OWN_CLASS], time_limit=30, priority=sp.Priority.BACKGROUND
    )
    assert int(said.decode()) == subprocess.BELOW_NORMAL_PRIORITY_CLASS


async def _worst_gap_while(doing: Callable[[], Awaitable[None]]) -> float:
    """The longest a turn of the loop took while `doing` ran, in seconds.

    A spinning heartbeat, which sees the true gap between two turns rather than the 15.6 ms Windows
    timer a `sleep` would be rounded to.
    """
    worst = 0.0
    stop = asyncio.Event()

    async def heartbeat() -> None:
        nonlocal worst
        while not stop.is_set():
            before = asyncio.get_running_loop().time()
            await asyncio.sleep(0)
            worst = max(worst, asyncio.get_running_loop().time() - before)

    beating = asyncio.create_task(heartbeat())
    try:
        await doing()
    finally:
        stop.set()
        await beating
    return worst


@pytest.mark.integration
async def test_launching_a_tool_does_not_hold_the_event_loop() -> None:
    import subprocess

    if sys.platform == "win32" and _own_class() == subprocess.BELOW_NORMAL_PRIORITY_CLASS:
        # A below-normal process is pre-empted by every normal one on each turn, so the difference
        # the launches make cannot be told from the machine's other work; the measurement is the
        # normal class's, which a service's job object does not let this process reach.
        pytest.skip("this process runs below normal, where a launch's cost cannot be measured")
    """Process creation off the loop: on Windows, run on it, it holds the loop for about 16 ms per
    tool and 45 ms with two dozen in flight, and a probe is dozens of launches per file.

    MEASURED AS A DIFFERENCE, not an absolute gap: on a loaded machine the operating system can
    simply not schedule this thread for tens of milliseconds, which an absolute latency cannot tell
    apart from the fault. So the same heartbeat is run over the launches and over a quiet stretch
    of the same length.

    AND AS THE MEDIAN OF FIVE PAIRS. Each reading is a MAXIMUM over a stretch, decided by one
    unlucky descheduling, and subtracting two independent maxima adds their variance rather than
    cancelling it: one pair can read several milliseconds either way while the launch itself costs
    about 0.1 ms. Noise is one unlucky pair; the fault is every pair, because creation on the loop
    holds it on every launch. The median of the differences is robust in both directions: a
    twentieth of the threshold when the launches are off the loop, about 16 ms per launch when they
    are on it.
    """
    loop = asyncio.get_running_loop()

    async def twelve_launches() -> None:
        for _ in range(12):
            await sp.run([sys.executable, "-c", "pass"], time_limit=30)

    async def one_pair() -> float:
        began = loop.time()
        busy = await _worst_gap_while(twelve_launches)
        took = loop.time() - began
        # The control, over the same stretch of time so it has the same chance of being descheduled.
        quiet = await _worst_gap_while(lambda: asyncio.sleep(took))
        return busy - quiet

    pairs = [await one_pair() for _ in range(5)]
    added = statistics.median(pairs)
    assert added < 0.008, (
        f"the launches added {added * 1000:.1f} ms to the worst turn of the loop, as the median of "
        f"five pairs ({', '.join(f'{one * 1000:.1f}' for one in pairs)} ms)"
    )


async def test_a_tool_is_started_on_a_thread_and_not_on_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rule the timing above measures, read where it is decided: the creation, and the steps
    that set the new process up, run on a thread of their own. A fast machine can launch quickly
    enough on the loop to pass the timing, so this one asks the thread itself."""
    seen: list[int] = []
    set_up = sp.step_aside

    def recorded(process: Any, priority: sp.Priority) -> None:
        seen.append(threading.get_ident())
        set_up(process, priority)

    monkeypatch.setattr(sp, "step_aside", recorded)
    await sp.run([sys.executable, "-c", "pass"], time_limit=30)
    assert seen and threading.get_ident() not in seen


# --- priority ---------------------------------------------------------------------------
#
# The point of these is that the setting REACHES the operating system. A test that only checks the
# arguments Sift assembles proves it built a command; it does not prove the machine agreed to it,
# and "asked politely and was ignored" is the outcome worth catching. So the ones below read the
# priority back out of a real child.


@pytest.fixture(autouse=True)
def _forget_which_tools_exist() -> Iterator[None]:
    """The lookup is cached for the life of the process, and these tests change its answer.

    Cleared on the way OUT as well as on the way in, which is the half that matters. Monkeypatching
    puts back the function it replaced but knows nothing about a cache holding what that function
    said while it was replaced, so a test that pretended neither utility exists would leave
    "neither utility exists" cached for every test that ran after it in the same process, and those
    would quietly stop deprioritising anything while still passing.
    """
    sp._priority_tools.cache_clear()
    yield
    sp._priority_tools.cache_clear()


def test_normal_priority_changes_nothing_about_the_command() -> None:
    """The ordinary path is byte-for-byte what it was before any of this existed."""
    assert sp.launch_prefix(sp.Priority.NORMAL) == []


def test_background_priority_asks_for_both_the_processor_and_the_disk() -> None:
    prefix = sp.launch_prefix(sp.Priority.BACKGROUND)

    if ON_WINDOWS:
        # NOTHING, deliberately, and this is the assertion rather than a skip. Those names belong
        # to Unix tools, so anything answering to them here came from another toolchain that is on
        # PATH, and the prefix replaces the process that gets launched, which is what the kill
        # and the reaping are aimed at. A foreign `nice.EXE` in front of a tool is worse than no
        # scheduling hint at all.
        assert prefix == []
        return

    assert prefix, "nothing was prefixed, so nothing was deprioritised"
    joined = " ".join(prefix)
    assert "nice" in joined
    assert "-n" in prefix and sp._BACKGROUND_NICE in prefix
    assert "ionice" in joined
    assert "-c" in prefix and sp._IDLE_IO_CLASS in prefix


def test_both_utilities_present_asks_for_both_halves(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole prefix, assembled, on a machine that has both.

    Driven through the site gate rather than skipped where the prefix is never built: this is
    what a POSIX install really gets, and the arm that adds the disk half is not reachable at all
    on a machine where neither tool exists.
    """
    monkeypatch.setattr("sift.kernel.subprocess._UNIX_PRIORITY_TOOLS", True)
    monkeypatch.setattr(
        "sift.kernel.subprocess.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )
    sp._priority_tools.cache_clear()

    assert sp.launch_prefix(sp.Priority.BACKGROUND) == [
        "/usr/bin/nice",
        "-n",
        sp._BACKGROUND_NICE,
        "/usr/bin/ionice",
        "-c",
        sp._IDLE_IO_CLASS,
    ]
    sp._priority_tools.cache_clear()


def test_a_missing_utility_costs_only_its_own_half(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scheduling hint, not a dependency. A machine without one of these still gets the other,
    and one without either runs the tool rather than refusing to.

    Driven on every site rather than skipped where the prefix is never built. The rule is about
    what happens when a tool is absent, and a machine where BOTH are always absent is the strongest
    case it has, so the site gate is turned on here and the lookup is answered by hand.
    """
    monkeypatch.setattr("sift.kernel.subprocess._UNIX_PRIORITY_TOOLS", True)
    monkeypatch.setattr(
        "sift.kernel.subprocess.shutil.which",
        lambda name: "/usr/bin/nice" if name == "nice" else None,
    )
    sp._priority_tools.cache_clear()
    assert sp.launch_prefix(sp.Priority.BACKGROUND) == ["/usr/bin/nice", "-n", sp._BACKGROUND_NICE]

    monkeypatch.setattr("sift.kernel.subprocess.shutil.which", lambda name: None)
    sp._priority_tools.cache_clear()
    assert sp.launch_prefix(sp.Priority.BACKGROUND) == []


@pytest.mark.integration
async def test_a_background_child_really_runs_below_everything_else() -> None:
    """Read back out of the running child rather than off the command that started it.

    A prefix that was assembled correctly and then refused by the kernel looks identical from the
    outside, and this is the only thing that tells the two apart. Both halves are read: the
    processor share from the scheduler, the disk class from the io scheduler.
    """
    if not all(sp._priority_tools()):
        pytest.skip("this machine has neither nice nor ionice")

    # The child reports on itself (`os.nice(0)` returns the current value without changing it)
    # rather than the test inspecting it from outside. What is being checked is the priority of the
    # process Sift actually spawned, and asking that process is the only way to be sure it is that
    # one and not a wrapper around it.
    reads_its_own_priority = (
        "import os, subprocess, sys;"
        "sys.stdout.write(str(os.nice(0)));"
        "sys.stdout.write(' ');"
        "sys.stdout.write(subprocess.run(['ionice', '-p', str(os.getpid())],"
        " capture_output=True, text=True).stdout.strip())"
    )

    result = await sp.run(
        [sys.executable, "-c", reads_its_own_priority],
        time_limit=30,
        priority=sp.Priority.BACKGROUND,
    )

    assert result.returncode == 0, result.stderr
    niceness, io_class = result.stdout.decode().split(" ", 1)
    assert niceness == sp._BACKGROUND_NICE
    assert "idle" in io_class


@POSIX_PRIORITY_ONLY
@pytest.mark.integration
async def test_a_normal_child_is_left_where_it_was() -> None:
    """The other half of the pair. Without this the test above passes on a machine that runs
    everything at nineteen, and the setting would be proving nothing."""
    result = await sp.run(
        [sys.executable, "-c", "import os, sys; sys.stdout.write(str(os.nice(0)))"],
        time_limit=30,
        priority=sp.Priority.NORMAL,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.decode() != sp._BACKGROUND_NICE


# --- handing a tool its input ---------------------------------------------------------------------


@pytest.mark.integration
async def test_a_tool_can_be_handed_bytes_rather_than_a_filename() -> None:
    """For the tools that are given data rather than something to open."""
    result = await sp.run(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        time_limit=30,
        stdin=b"given, not opened",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == b"GIVEN, NOT OPENED"


@pytest.mark.integration
async def test_a_tool_that_stops_reading_part_way_is_an_ordinary_end() -> None:
    """It has all it needs, or it has failed. Either way the broken pipe is not a fault to raise."""
    result = await sp.run(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read(4))"],
        time_limit=30,
        stdin=b"x" * (4 << 20),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == b"xxxx"


#: A tool that answers while it is still being fed: each piece of its input goes straight back out.
_ECHO = (
    "import sys\n"
    "while chunk := sys.stdin.buffer.read1(1 << 16):\n"
    "    sys.stdout.buffer.write(chunk)\n"
    "    sys.stdout.buffer.flush()\n"
)


@pytest.mark.integration
async def test_a_tool_fed_its_input_finishes_while_the_shared_pool_has_one_thread_to_give() -> None:
    """Fed, read and drained at once. Those three waits run on threads of the tool's own, so a
    shared pool with a single thread free does not leave the tool stopped on a full pipe until its
    time limit ends it."""
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
    payload = os.urandom(4 << 20)

    result = await sp.run([sys.executable, "-c", _ECHO], time_limit=20, stdin=payload)

    assert result.returncode == 0, result.stderr
    assert result.stdout == payload


@pytest.mark.integration
async def test_a_tool_given_no_input_is_not_left_waiting_for_any() -> None:
    """A tool started by a server has no business reading the server's own input, so it is closed
    rather than left open: otherwise a tool that reads it waits for ever."""
    result = await sp.run(
        [sys.executable, "-c", "import sys; sys.stdout.write(repr(sys.stdin.read()))"],
        time_limit=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == b"''"


# --- reading a tool's output as it arrives ----------------------------------------------------------


WRITES_PIECES = (
    "import sys\nfor index in range(6):\n    sys.stdout.buffer.write(bytes([index]) * 8)\n"
)


@pytest.mark.integration
async def test_output_arrives_in_pieces_of_the_size_asked_for() -> None:
    """The other runner keeps what a tool said, capped. That is right for a report and wrong for a
    tool whose output IS the work: past the cap it hands back a short answer with nothing to say it
    was short."""
    pieces = [
        piece
        async for piece in sp.stream(
            [sys.executable, "-c", WRITES_PIECES], frame_bytes=8, time_limit=30
        )
    ]

    assert len(pieces) == 6
    assert pieces[0] == bytes([0]) * 8
    assert pieces[5] == bytes([5]) * 8


@pytest.mark.integration
async def test_a_trailing_part_piece_is_dropped_rather_than_handed_over() -> None:
    """What a tool killed mid-write leaves behind. Handed over, it would be a piece with rubbish
    on the end, which nothing downstream could tell from a real one."""
    pieces = [
        piece
        async for piece in sp.stream(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 20)"],
            frame_bytes=8,
            time_limit=30,
        )
    ]

    assert pieces == [b"x" * 8, b"x" * 8]


@pytest.mark.integration
async def test_a_tool_that_ran_to_the_end_and_failed_is_reported_rather_than_read_as_empty() -> (
    None
):
    """A tool that could not do the work and one that had none to do produce the same nothing."""
    with pytest.raises(sp.SubprocessError, match="failed with status"):
        [
            piece
            async for piece in sp.stream(
                [sys.executable, "-c", "import sys; sys.exit(3)"], frame_bytes=8, time_limit=30
            )
        ]


@pytest.mark.integration
async def test_a_caller_that_stops_reading_kills_the_tool() -> None:
    """Otherwise it carries on decoding into a pipe nobody is reading. A killed tool's exit status
    says nothing about the work, so it is not reported as a failure either."""
    reader: AsyncGenerator[bytes] = sp.stream(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            "sys.stdout.buffer.write(b'a' * 8)\n"
            "sys.stdout.buffer.flush()\n"
            "time.sleep(30)\n",
        ],
        frame_bytes=8,
        time_limit=30,
    )

    first = await anext(reader)
    await reader.aclose()

    assert first == b"a" * 8


@pytest.mark.integration
async def test_a_tool_that_will_not_start_is_reported() -> None:
    with pytest.raises(sp.SubprocessError, match="could not run"):
        [piece async for piece in sp.stream(["/nonexistent/tool"], frame_bytes=8, time_limit=30)]


@pytest.mark.integration
async def test_a_tool_that_never_finishes_is_stopped() -> None:
    with pytest.raises(sp.SubprocessError, match="took too long"):
        [
            piece
            async for piece in sp.stream(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                frame_bytes=8,
                time_limit=0.5,
            )
        ]


@pytest.mark.integration
async def test_a_streaming_tool_can_be_handed_its_input_too() -> None:
    pieces = [
        piece
        async for piece in sp.stream(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
            frame_bytes=4,
            time_limit=30,
            stdin=b"abcdefgh",
        )
    ]

    assert pieces == [b"abcd", b"efgh"]


@pytest.mark.integration
async def test_a_streaming_tool_that_ignores_its_input_is_not_a_fault() -> None:
    pieces = [
        piece
        async for piece in sp.stream(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'ok!!')"],
            frame_bytes=4,
            time_limit=30,
            stdin=b"y" * (4 << 20),
        )
    ]

    assert pieces == [b"ok!!"]


# --- the capture that never touches the event loop ------------------------------------------------
#
# Its own launcher rather than a flag on `run`: starting a process from a large Python process
# costs the kernel real work, and `create_subprocess_exec` does that ON the loop. A face pass over
# one long video is sixty launches, eight files at a time, enough to stop the whole application
# for minutes inside exactly that call. Everything here happens on a thread.


async def test_a_captured_tool_hands_back_everything_it_wrote() -> None:
    written = await sp.capture(
        [sys.executable, "-c", "import sys; sys.stdout.write('the answer')"], time_limit=10
    )

    assert written == b"the answer"


async def test_a_captured_tool_that_wrote_nothing_is_empty_rather_than_none() -> None:
    written = await sp.capture([sys.executable, "-c", "pass"], time_limit=10)

    assert written == b""


async def test_a_captured_tool_that_will_not_start_is_reported() -> None:
    with pytest.raises(sp.SubprocessError, match="could not run"):
        await sp.capture(["a-binary-that-does-not-exist-anywhere"], time_limit=10)


async def test_a_captured_tool_that_overruns_is_stopped() -> None:
    """Its own timeout, enforced on its own thread rather than by the loop: there is no loop
    involved here to enforce one."""
    with pytest.raises(sp.SubprocessError, match="took too long"):
        await sp.capture([sys.executable, "-c", "import time; time.sleep(30)"], time_limit=0.3)


async def test_a_captured_tool_that_failed_raises_rather_than_reading_as_empty() -> None:
    """A decoder handed a file it cannot open writes nothing and exits non-zero. Handing back an
    empty list would turn an unreadable file into a file with nothing in it, which reads as a
    perfectly good answer."""
    with pytest.raises(sp.SubprocessError, match="exit code 4"):
        await sp.capture([sys.executable, "-c", "raise SystemExit(4)"], time_limit=10)


@pytest.mark.integration
async def test_a_captured_tool_can_be_asked_for_at_a_lower_priority() -> None:
    """The same prefix every other launcher gets. Background work must not contend with the
    interface, and a capture is background work by definition: nobody is waiting on a frame."""
    written = await sp.capture(
        [sys.executable, "-c", "print('quietly')"],
        time_limit=10,
        priority=sp.Priority.BACKGROUND,
    )

    assert written.strip() == b"quietly"


# --- watching a tool report on itself -------------------------------------------------------


async def test_each_line_of_output_is_handed_over_as_it_arrives() -> None:
    seen: list[str] = []
    result = await sp.run(
        [sys.executable, "-c", "print('one'); print('two'); print('three')"],
        time_limit=10,
        on_line=seen.append,
    )
    assert seen == ["one", "two", "three"]
    # The output is still collected and still returned. Watching is in addition to, never instead.
    # By lines rather than byte-for-byte: `print` writes the site's own line ending, and what
    # this is about is that watching a tool does not consume its output.
    assert result.stdout.splitlines() == [b"one", b"two", b"three"]


async def test_a_line_rewritten_in_place_is_still_seen_as_lines() -> None:
    """The trap this exists for, and it is not hypothetical.

    A tool showing a progress bar rewrites one line with a carriage return and emits no newline at
    all until it is finished. A reader splitting only on newlines therefore waits for the whole
    download and then receives every update at once, as a single enormous line, which is
    indistinguishable from progress reporting being broken.
    """
    seen: list[str] = []
    await sp.run(
        [sys.executable, "-c", r"import sys; sys.stdout.write('10%\r50%\r100%\r')"],
        time_limit=10,
        on_line=seen.append,
    )
    assert seen == ["10%", "50%", "100%"]


async def test_watching_reads_the_output_and_never_the_error_stream() -> None:
    """Load-bearing: a failure is classified from stderr, and a caller wanting progress from stdout
    must not be able to affect that. If these ever merged, turning on detailed logging would start
    feeding diagnostics to something expecting progress."""
    seen: list[str] = []
    result = await sp.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('progress\\n'); sys.stderr.write('ERROR: 403\\n')",
        ],
        time_limit=10,
        on_line=seen.append,
    )
    assert seen == ["progress"]
    assert b"403" in result.stderr


async def test_a_watcher_that_raises_does_not_take_the_read_down_with_it() -> None:
    """A tool whose output stops being read blocks on a full pipe, so a bad progress line would
    become a hung download rather than a logged mistake."""

    def explode(_line: str) -> None:
        raise ValueError("bad line")

    result = await sp.run(
        [sys.executable, "-c", "print('one'); print('two')"], time_limit=10, on_line=explode
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == [b"one", b"two"]


async def test_a_tool_that_says_nothing_reports_nothing() -> None:
    seen: list[str] = []
    await sp.run([sys.executable, "-c", "pass"], time_limit=10, on_line=seen.append)
    assert seen == []


async def test_a_last_line_with_no_ending_is_still_handed_over() -> None:
    """A tool killed mid-write, or one that simply does not end its last line, still said it."""
    seen: list[str] = []
    await sp.run(
        [sys.executable, "-c", "import sys; sys.stdout.write('done')"],
        time_limit=10,
        on_line=seen.append,
    )
    assert seen == ["done"]


async def test_a_blank_line_between_two_lines_is_not_handed_over() -> None:
    """A tool draws its own spacing. Handing a watcher an empty string is a progress update with
    nothing in it, and every seam that reads one has to check for it separately."""
    seen: list[str] = []
    await sp.run(
        [sys.executable, "-c", r"import sys; sys.stdout.write('one\n\n   \ntwo\n')"],
        time_limit=10,
        on_line=seen.append,
    )
    assert seen == ["one", "two"]


async def test_a_tool_that_overruns_the_cap_is_still_watched_to_the_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past the cap the rest is read and thrown away, so the tool never blocks on a full pipe while
    the memory held stays bounded. The WATCHING has to survive that: a download whose tool is
    chatty enough to pass the cap is exactly the long one somebody is watching a bar for, and it
    would go quiet at the moment it mattered.

    The cap and the read size are both moved rather than met. Meeting the real cap means sixteen
    megabytes of output to answer a question about a boundary, and leaving the read at 64 KiB
    would swallow the whole of a small output in one read, so the second loop would find the stream
    already at its end and never run at all.
    """
    monkeypatch.setattr(sp, "_MAX_OUTPUT_BYTES", 8)
    monkeypatch.setattr(sp, "_READ_CHUNK", 4)
    seen: list[str] = []
    result = await sp.run(
        [sys.executable, "-c", r"import sys; sys.stdout.write('one\ntwo\nthree\nfour\n')"],
        time_limit=10,
        on_line=seen.append,
    )
    assert seen == ["one", "two", "three", "four"]
    # And what is kept is still bounded by the cap.
    assert len(result.stdout) <= 8


# --- the whole tree, on Windows ----------------------------------------------------------------

WINDOWS_JOBS_ONLY = pytest.mark.skipif(
    not ON_WINDOWS,
    reason="a job object is the Windows answer to a launcher's child; elsewhere nothing is added",
)

#: A grandchild that proves it is alive by writing to a file every tenth of a second. Its pipes are
#: its own, so the tool's pipes close when the tool dies and nothing waits on the grandchild.
_HEARTBEAT = (
    "import sys, time\n"
    "while True:\n"
    "    with open(sys.argv[1], 'a') as beat:\n"
    "        beat.write('.')\n"
    "    time.sleep(0.1)\n"
)

#: A launcher: starts the heartbeat as a child, then either hangs or leaves at once.
_LAUNCHER = (
    "import subprocess, sys, time\n"
    "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]],"
    " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "time.sleep(0.5)\n"
    "if sys.argv[3] == 'hang':\n"
    "    time.sleep(60)\n"
)


async def _still_beating(heart: Path) -> bool:
    """Whether the grandchild is still writing, read twice with a gap longer than its beat."""
    await asyncio.sleep(0.5)
    before = heart.stat().st_size
    await asyncio.sleep(0.6)
    return heart.stat().st_size != before


@WINDOWS_JOBS_ONLY
async def test_a_killed_launcher_takes_its_child_with_it(tmp_path: Path) -> None:
    """`process.kill()` on gallery-dl's one-file launcher would leave the real program (its
    child) downloading with nobody holding it. The kill ends the tool's job, which is the tool
    and everything it started."""
    heart = tmp_path / "heart"
    with pytest.raises(sp.SubprocessError, match="too long"):
        await sp.run([REAL_PYTHON, "-c", _LAUNCHER, _HEARTBEAT, str(heart), "hang"], time_limit=2.0)
    assert heart.exists(), "the grandchild never started, so this proved nothing"
    assert not await _still_beating(heart), "the launcher's child outlived the kill"
    assert not sp._JOBS, "a job handle was left open"


@WINDOWS_JOBS_ONLY
async def test_a_child_left_behind_by_a_tool_that_exited_is_ended_with_it(tmp_path: Path) -> None:
    """The same orphan by the other road: the tool leaves normally and its child is still going.
    Closing the job after the run ends it, because the job is made to kill on close."""
    heart = tmp_path / "heart"
    result = await sp.run(
        [REAL_PYTHON, "-c", _LAUNCHER, _HEARTBEAT, str(heart), "leave"], time_limit=20
    )
    assert result.returncode == 0
    assert heart.exists(), "the grandchild never started, so this proved nothing"
    assert not await _still_beating(heart), "the tool's child outlived the run"
    assert not sp._JOBS


# --- the memory a background tool may take -------------------------------------------------------
#
# Two tools reading moments of 4K files can hold tens of gigabytes between them and starve the
# database of memory. A background tool is held to a share of the machine, and one that asks for
# more is ended and fails: it is not left refused and retrying, which is what a decoder does.

#: Asks for far more than the limit below and, refused, asks again for ever, the way a decoder
#: that is denied a picture reports the error and carries on. Only being ended stops it.
_GREEDY = (
    "import time\n"
    "while True:\n"
    "    try:\n"
    "        held = bytearray(600 * 2**20)\n"
    "    except MemoryError:\n"
    "        time.sleep(0.05)\n"
)


def test_a_background_tool_is_held_to_a_share_of_the_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sp, "_machine_memory", 64 << 30)
    assert sp.memory_limit_for(sp.Priority.BACKGROUND) == 16 << 30
    assert sp.memory_limit_for(sp.Priority.NORMAL) is None, "somebody is waiting on this one"
    # Half the machine shared by the tools running together, never more than half a tool's limit.
    assert sp.planned_memory(12) == (64 << 30) // 2 // 12
    assert sp.planned_memory(1) == (16 << 30) // 2
    monkeypatch.setattr(sp, "_machine_memory", None)
    assert sp.memory_limit_for(sp.Priority.BACKGROUND) is None
    assert sp.planned_memory(1) == sp.ASSUMED_MACHINE_MEMORY // sp.BACKGROUND_MEMORY_SHARE // 2


@WINDOWS_JOBS_ONLY
@pytest.mark.parametrize("launcher", ["run", "capture", "stream"])
async def test_a_background_tool_that_asks_past_its_share_is_ended_and_says_so(
    monkeypatch: pytest.MonkeyPatch, launcher: str
) -> None:
    monkeypatch.setattr(sp, "_machine_memory", 800 * 2**20)
    argv = [REAL_PYTHON, "-c", _GREEDY]
    started = asyncio.get_running_loop().time()
    with pytest.raises(sp.SubprocessError, match="more memory than a background tool may use"):
        if launcher == "run":
            await sp.run(argv, time_limit=8, priority=sp.Priority.BACKGROUND)
        elif launcher == "capture":
            await sp.capture(argv, time_limit=8, priority=sp.Priority.BACKGROUND)
        else:
            async for _piece in sp.stream(
                argv, frame_bytes=8, time_limit=8, priority=sp.Priority.BACKGROUND
            ):
                pass
    assert asyncio.get_running_loop().time() - started < 6, "it sat at its limit until the clock"
    assert not sp._JOBS, "a job handle was left open"


@WINDOWS_JOBS_ONLY
async def test_a_tool_somebody_is_waiting_for_is_not_held_to_the_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sp, "_machine_memory", 800 * 2**20)
    done = await sp.run(
        [REAL_PYTHON, "-c", "held = bytearray(600 * 2**20)"],
        time_limit=15,
        priority=sp.Priority.NORMAL,
    )
    assert done.returncode == 0


# --- a child that runs for as long as Sift does ------------------------------------------------
#
# A long-lived child (the tunnel client) started outside any job would survive a hard stop of
# Sift with its ports and its VPN session, and the next boot would have to guess whose it was. It
# is held in a kill-on-close job whose only handle is Sift's own, so Windows ends it with Sift.

#: A stand-in for Sift: starts the heartbeat as a long-lived child, says so, and waits to be killed.
_HOLDER = (
    "import asyncio, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from sift.kernel.subprocess import start_long_lived\n"
    "async def main():\n"
    "    child = await start_long_lived([sys.executable, '-c', sys.argv[2], sys.argv[3]])\n"
    "    print(child.pid, flush=True)\n"
    "    await asyncio.sleep(60)\n"
    "asyncio.run(main())\n"
)


@WINDOWS_JOBS_ONLY
async def test_a_long_lived_child_ends_when_the_process_holding_it_is_killed(
    tmp_path: Path,
) -> None:
    """THE PROPERTY, end to end: the process that started the child is killed outright (no
    shutdown code runs, which is a crash, a forced end, or a restart that would not wait), and the
    child goes with it. Otherwise the tunnel client would stay up after every such stop."""
    heart = tmp_path / "heart"
    source = str(Path(sp.__file__).resolve().parents[2])
    holder = await asyncio.create_subprocess_exec(
        REAL_PYTHON,
        "-c",
        _HOLDER,
        source,
        _HEARTBEAT,
        str(heart),
        stdout=asyncio.subprocess.PIPE,
    )
    try:
        assert holder.stdout is not None
        line = await asyncio.wait_for(holder.stdout.readline(), timeout=20)
        assert line.strip().isdigit(), f"the stand-in never started its child: {line!r}"
        assert await _still_beating(heart), "the child never started, so this proved nothing"
    finally:
        holder.kill()
        await holder.wait()
    assert not await _still_beating(heart), "the child outlived the process that held it"


async def test_a_long_lived_child_runs_until_it_is_stopped_and_is_reaped(tmp_path: Path) -> None:
    """Started, still running when asked, stopped, reaped, and on Windows its job let go once it
    has, so nothing is held for a child that has gone."""
    heart = tmp_path / "heart"
    child = await sp.start_long_lived([REAL_PYTHON, "-c", _HEARTBEAT, str(heart)])
    running_when_asked = child.returncode is None
    assert running_when_asked
    assert await _still_beating(heart)
    child.terminate()
    await child.wait(time_limit=10)
    assert child.returncode is not None
    assert not await _still_beating(heart)
    assert not sp._JOBS, "a job handle was left open for a child that has gone"


async def test_waiting_for_a_long_lived_child_is_bounded() -> None:
    """A wait with a limit ends at the limit and leaves the child running rather than a thread
    blocked behind it."""
    child = await sp.start_long_lived([REAL_PYTHON, "-c", "import time; time.sleep(60)"])
    try:
        with pytest.raises(TimeoutError):
            await child.wait(time_limit=0.2)
        assert child.returncode is None
    finally:
        child.kill()
        await child.wait()


async def test_a_long_lived_child_that_will_not_start_is_reported() -> None:
    with pytest.raises(sp.SubprocessError, match="could not run"):
        await sp.start_long_lived(["definitely-not-a-real-program-sift"])


async def test_where_there_is_no_job_a_long_lived_child_still_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is no job to put it in anywhere but Windows, and a machine that will not make one is the same
    case: the child runs, uncontained, rather than the launch failing over a safety net."""
    monkeypatch.setattr(sp, "_job_api", lambda: None)
    child = await sp.start_long_lived([REAL_PYTHON, "-c", "import time; time.sleep(60)"])
    try:
        assert child.returncode is None
        assert not sp._JOBS
    finally:
        child.kill()
        await child.wait()


async def test_extra_variables_are_added_to_the_environment_never_instead_of_it() -> None:
    """A tool given one variable must still have everything else: a PATH, a system root."""
    shown = await sp.run(
        [
            sys.executable,
            "-c",
            "import os, sys; sys.stdout.write(os.environ['SIFT_PROBE'] + '|'"
            " + str(len(os.environ) > 1))",
        ],
        time_limit=20,
        extra_env={"SIFT_PROBE": "here"},
    )
    assert shown.stdout == b"here|True"


# --- the machine's memory, and the job calls where Windows will not make one ---------------------


def test_the_machine_s_memory_is_what_the_hardware_report_said_and_nothing_where_it_could_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A report of none, or of zero, is "could not say", never a limit of nothing."""
    monkeypatch.setattr(sp, "_machine_memory", None)

    sp.set_machine_memory(64 << 30)
    assert sp.memory_limit_for(sp.Priority.BACKGROUND) == 16 << 30

    for said in (0, None):
        sp.set_machine_memory(said)
        assert sp.memory_limit_for(sp.Priority.BACKGROUND) is None


def test_there_are_no_job_calls_off_windows_or_where_kernel32_will_not_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes

    def refuses(*args: object, **kwargs: object) -> object:
        raise OSError("kernel32 would not load")

    sp._job_api.cache_clear()
    try:
        monkeypatch.setattr(sys, "platform", "linux")
        assert sp._job_api() is None
        sp._job_api.cache_clear()
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(ctypes, "WinDLL", refuses, raising=False)
        assert sp._job_api() is None
    finally:
        monkeypatch.undo()
        sp._job_api.cache_clear()


class _Api:
    """The job calls, answered by a script rather than by Windows."""

    def __init__(self, *, job: int = 7, port: int = 5, sets: bool = True) -> None:
        self.job = job
        self.port = port
        self.sets = sets
        self.closed: list[int] = []
        self.ended: list[int] = []
        self.completions: list[tuple[bool, int, int, int | None]] = []

    def CreateJobObjectW(self, *args: object) -> int:
        return self.job

    def CreateIoCompletionPort(self, *args: object) -> int:
        return self.port

    def SetInformationJobObject(self, *args: object) -> bool:
        return self.sets

    def AssignProcessToJobObject(self, *args: object) -> bool:
        return True

    def CloseHandle(self, handle: int) -> None:
        self.closed.append(handle)

    def TerminateJobObject(self, job: int, status: int) -> None:
        self.ended.append(job)

    def GetQueuedCompletionStatus(
        self, port: object, message: object, key: object, detail: object, wait: int
    ) -> bool:
        ok, said, number, where = (
            self.completions.pop(0) if self.completions else (False, 0, 0, None)
        )
        message._obj.value = said  # type: ignore[attr-defined]
        key._obj.value = number  # type: ignore[attr-defined]
        detail._obj.value = where  # type: ignore[attr-defined]
        return ok


class _Tool:
    """Something a job can be made of: a handle, and a weak reference to it."""

    _handle = 11


@pytest.mark.skipif(not ON_WINDOWS, reason="the job calls are Windows' own")
def test_a_memory_watch_that_cannot_be_set_up_says_so_rather_than_watching_nothing() -> None:
    """No port, or a job that will not report to it: None, so the caller runs the tool without a
    limit nothing is listening for."""
    assert sp._MemoryWatch(_Api(port=0)).watch(7, _Tool()) is None  # type: ignore[arg-type]

    refusing = sp._MemoryWatch(_Api(sets=False))
    assert refusing.watch(7, _Tool()) is None  # type: ignore[arg-type]
    assert refusing.watch(8, _Tool()) is None, "the port it already made is used again"  # type: ignore[arg-type]


@pytest.mark.skipif(not ON_WINDOWS, reason="the job calls are Windows' own")
def test_only_a_breach_by_a_job_still_held_ends_it_and_a_gone_port_ends_the_listening() -> None:
    """A wait that took nothing off is waited again; another kind of message, and one about a job
    already forgotten, end nothing; a breach ends its job and marks a tool that is still there; a
    port that is gone stops the thread rather than spinning on it."""
    api = _Api()
    watch = sp._MemoryWatch(api)
    watch._port = 5
    alive = _Tool()
    gone = _Tool()
    held: Any = {1: (101, weakref.ref(alive)), 2: (102, weakref.ref(gone))}
    watch._jobs = held
    del gone
    breach = sp._MESSAGE_JOB_MEMORY_LIMIT
    api.completions = [
        (False, 0, 0, 1),
        (True, breach + 1, 1, 1),
        (True, breach, 9, 1),
        (True, breach, 2, 1),
        (True, breach, 1, 1),
        (False, 0, 0, None),
    ]

    watch._listen()

    assert api.ended == [102, 101]
    assert alive in sp._OVER_MEMORY  # type: ignore[comparison-overlap]
    assert api.completions == []


@pytest.mark.skipif(not ON_WINDOWS, reason="the job calls are Windows' own")
def test_a_tool_whose_job_cannot_be_made_or_set_still_runs_and_holds_no_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tolerant by design: a job that will not be made, or will not take its limits, leaves the
    tool running uncontained, and a limit nothing could listen for is not set at all."""
    tool = _Tool()
    monkeypatch.setattr(sp, "_job_api", lambda: _Api(job=0))
    sp._contain(tool, memory_limit=1 << 20)  # type: ignore[arg-type]
    assert tool not in sp._JOBS  # type: ignore[comparison-overlap]

    refusing = _Api(sets=False)
    monkeypatch.setattr(sp, "_job_api", lambda: refusing)
    monkeypatch.setattr(sp, "_memory_watch", lambda: None)
    sp._contain(tool, memory_limit=1 << 20)  # type: ignore[arg-type]
    assert refusing.closed == [7], "the job that would not take its limits was left open"
    assert tool not in sp._JOBS  # type: ignore[comparison-overlap]

    sp._close_job(refusing, 8, 3)
    assert refusing.closed == [7, 8], "a job is closed even with no watch to forget it in"


class _RateApi(_Api):
    """The job calls, recording each processor rate set and on which job."""

    def __init__(self) -> None:
        super().__init__()
        self.rates: list[tuple[int, int, int]] = []
        self._next = 40

    def CreateJobObjectW(self, *args: object) -> int:
        self._next += 1
        return self._next

    def SetInformationJobObject(self, *args: Any) -> bool:
        job, kind, info, _ = args
        if kind == sp._CPU_RATE_INFORMATION:
            self.rates.append((job, info._obj.ControlFlags, info._obj.CpuRate))
        return True


class _Held:
    """A tool to contain: a handle, and a weak reference to it."""

    _handle = 12


def test_a_background_tool_is_held_to_the_rate_in_force_and_let_go_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The step back's processor rate reaches every background tool: one already running when it
    moves, one started while it holds, never a tool somebody is waiting on; and it is lifted from
    the running ones when the whole device is back."""
    api = _RateApi()
    monkeypatch.setattr(sp, "_job_api", lambda: api)
    monkeypatch.setattr(sp, "_memory_watch", lambda: None)
    monkeypatch.setattr(sp, "_JOBS", weakref.WeakKeyDictionary())
    monkeypatch.setattr(sp, "_BACKGROUND", weakref.WeakSet())
    monkeypatch.setattr(sp, "_background_rate", None)
    on = sp._CPU_RATE_ON | sp._CPU_RATE_HARD_CAP

    running, waited_on, arriving = _Held(), _Held(), _Held()
    sp._contain(running, background=True)  # type: ignore[arg-type]
    sp._contain(waited_on)  # type: ignore[arg-type]
    assert api.rates == [], "nothing is held while the whole device is in force"

    sp.hold_background(833)
    assert api.rates == [(41, on, 833)]
    sp._contain(arriving, background=True)  # type: ignore[arg-type]
    assert api.rates[-1] == (43, on, 833)
    assert sp.background_rate() == 833

    sp._release(arriving)  # type: ignore[arg-type]
    sp.hold_background(833)
    assert len(api.rates) == 2, "the same rate again sets nothing"
    sp.hold_background(None)
    assert api.rates[2:] == [(41, 0, 0)], "lifted from the one still running, and only that one"
    assert sp.background_rate() is None


def test_a_rate_is_kept_where_there_are_no_jobs_to_hold_a_tool_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the job calls nothing can be held, but the rate in force is still what the step
    back asked for, so the next tool started where jobs work is held to it."""
    monkeypatch.setattr(sp, "_job_api", lambda: None)
    monkeypatch.setattr(sp, "_background_rate", None)

    sp.hold_background(833)

    assert sp.background_rate() == 833


_SPINS = """
import subprocess, sys
spin = "import time\\nend = time.monotonic() + 1.5\\nwhile time.monotonic() < end: pass\\n"
spin += "print(time.process_time())"
children = [subprocess.Popen([sys.executable, "-c", spin], stdout=subprocess.PIPE) for _ in range(4)]
print(sum(float(child.communicate()[0]) for child in children))
"""


@pytest.mark.skipif(not ON_WINDOWS, reason="the processor rate is a Windows job's")
@pytest.mark.skipif((os.cpu_count() or 1) < 4, reason="four spinning processes need four cores")
async def test_a_tool_that_spins_four_processors_is_held_to_one_while_the_step_back_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real operating system: a background tool whose four children each spin for a second and
    a half spends about one processor's worth while held to one processor, not four."""
    from sift.kernel.budget import processor_rate

    monkeypatch.setattr(sp, "_background_rate", None)
    sp.hold_background(processor_rate(1, os.cpu_count() or 1))
    try:
        result = await sp.run(
            [REAL_PYTHON, "-c", _SPINS], time_limit=60, priority=sp.Priority.BACKGROUND
        )
    finally:
        sp.hold_background(None)

    spent = float(result.stdout)
    assert spent < 3.0, f"four spinning children spent {spent:.1f} s of processor in 1.5 s"


async def test_a_streamed_tool_the_loop_will_not_name_still_runs_uncontained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `Popen` behind it means no job to make; the tool runs and is read as before."""
    monkeypatch.setattr(sp, "_popen_of", lambda process: None)

    pieces = [
        piece
        async for piece in sp.stream(
            [sys.executable, "-c", WRITES_PIECES], frame_bytes=8, time_limit=30
        )
    ]

    assert len(pieces) == 6


async def test_a_long_lived_launch_cancelled_while_it_starts_ends_what_it_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing will ever hold a child whose launch was cancelled, so it is let finish starting and
    then ended rather than left running."""
    import threading

    made: list[object] = []
    starting = threading.Event()
    real_contain = sp._contain

    def slow_contain(process: object, memory_limit: int | None = None) -> None:
        made.append(process)
        starting.set()
        time.sleep(0.3)
        real_contain(process, memory_limit)  # type: ignore[arg-type]

    monkeypatch.setattr(sp, "_contain", slow_contain)
    launch = asyncio.ensure_future(
        sp.start_long_lived([REAL_PYTHON, "-c", "import time; time.sleep(60)"])
    )
    await asyncio.to_thread(starting.wait, 10)
    launch.cancel()

    with pytest.raises(asyncio.CancelledError):
        await launch

    (child,) = made
    assert child.poll() is not None, "the child of a cancelled launch was left running"  # type: ignore[attr-defined]
