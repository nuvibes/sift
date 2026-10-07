# SPDX-License-Identifier: AGPL-3.0-or-later
"""A share of the workers while somebody is using the computer, the full count once they have left it.

Driven with a stand-in for the time since the last input, so the minute can be crossed in both
directions without waiting for one, and with the tick counter's wrap worked through by hand. A
person's press for turbo mode, and pressing again to step back, are driven the same way.
"""

from __future__ import annotations

import asyncio
import ctypes
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from sift.kernel import attention
from sift.kernel.attention import ATTENTION_SECONDS, Attention, elapsed_seconds
from sift.kernel.changes import About
from sift.kernel.config import Settings
from sift.kernel.jobs import JobContext, JobQueue, JobState, WorkerPool, register_handler
from sift.testing.logs import uncached_log

#: The module's own reading, kept before the suite's fixture swaps it for a stand-in.
_SHARED = attention.ATTENTION


def _holding(reader: Attention) -> bool:
    """Asked through a call, so a type checker does not carry one answer past the next reading."""
    return reader.holding


class _Input:
    """The seconds since the last key press or mouse movement, as a test sets them."""

    def __init__(self, seconds: float | None) -> None:
        self.seconds = seconds

    def __call__(self) -> float | None:
        return self.seconds


def test_the_workers_step_back_to_a_quarter_while_input_is_live_and_come_back_once_it_stops() -> (
    None
):
    since = _Input(2.0)
    reader = Attention(since)
    assert reader.workers(8, step_back=True) == 2
    assert _holding(reader) is True
    # Still inside the minute: still a quarter.
    since.seconds = ATTENTION_SECONDS - 1
    assert reader.workers(8, step_back=True) == 2
    # The minute passed with nobody touching anything: the full count again.
    since.seconds = ATTENTION_SECONDS + 1
    assert reader.workers(8, step_back=True) == 8
    assert _holding(reader) is False
    # And a quarter again the moment somebody comes back.
    since.seconds = 0.5
    assert reader.workers(8, step_back=True) == 2


@pytest.mark.parametrize(
    ("full", "stepped"), [(1, 1), (2, 1), (3, 1), (4, 1), (5, 2), (7, 2), (12, 3)]
)
def test_the_step_back_is_a_quarter_rounded_up_and_never_below_one(full: int, stepped: int) -> None:
    assert Attention(_Input(0.0)).workers(full, step_back=True) == stepped


def test_the_share_is_the_setting_and_says_what_is_in_force() -> None:
    since = _Input(0.0)
    reader = Attention(since)
    assert reader.workers(12, step_back=True, share=50) == 6
    assert (reader.share, reader.share_now) == (50, 50)
    # Nobody here: the whole device is in force, whatever the setting says.
    since.seconds = ATTENTION_SECONDS + 1
    assert reader.workers(12, step_back=True, share=50) == 12
    assert (reader.share, reader.share_now) == (50, 100)


def test_a_share_that_rounds_up_to_the_whole_pool_never_puts_the_step_back_in_play() -> None:
    reader = Attention(_Input(0.0))
    assert reader.workers(4, step_back=True, share=90) == 4
    assert _holding(reader) is False
    assert reader.share_now == 100


def test_a_pool_of_one_has_nothing_to_give_back_and_never_says_it_is_holding() -> None:
    reader = Attention(_Input(0.0))
    assert reader.workers(1, step_back=True) == 1
    assert _holding(reader) is False


def test_the_setting_off_keeps_the_full_count_and_never_reads_the_input() -> None:
    def refuses() -> float | None:
        raise AssertionError("the input is not read while the setting is off")

    reader = Attention(refuses)
    assert reader.workers(8, step_back=False) == 8
    assert _holding(reader) is False


def test_an_unreadable_input_leaves_the_full_count() -> None:
    reader = Attention(_Input(None))
    assert reader.workers(8, step_back=True) == 8
    assert _holding(reader) is False


def test_the_shared_reading_is_what_stepping_back_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = Attention(_Input(1.0))
    monkeypatch.setattr(attention, "ATTENTION", reader)
    reader.workers(6, step_back=True)
    assert attention.stepping_back() is True
    reader.workers(6, step_back=False)
    assert attention.stepping_back() is False


def test_other_programs_busy_step_back_too_and_say_so_while_input_comes_first() -> None:
    since = _Input(ATTENTION_SECONDS + 1)
    reader = Attention(since)
    assert reader.workers(8, step_back=True, others_busy=True) == 2
    assert (reader.cause, reader.share_now) == ("others", 25)
    since.seconds = 1.0
    assert reader.workers(8, step_back=True, others_busy=True) == 2
    assert reader.cause == "input"
    since.seconds = ATTENTION_SECONDS + 1
    assert reader.workers(8, step_back=True, others_busy=False) == 8
    assert reader.cause is None


def test_turbo_mode_overrules_the_other_programs_cause_too() -> None:
    reader = Attention(_Input(None))
    reader.press(full=True)
    assert reader.workers(8, step_back=True, others_busy=True) == 8
    # Still said, so the bolt can say what it overrules.
    assert (reader.turbo_mode, reader.holding, reader.cause) == (True, False, "others")
    reader.press(full=False)
    assert reader.workers(8, step_back=True, others_busy=True) == 2


def test_a_clip_playing_steps_back_from_its_first_read_until_a_minute_after_its_last() -> None:
    now = [100.0]
    played = attention.Played(clock=lambda: now[0])
    reader = Attention(_Input(None), since_played=played.seconds_since)
    assert reader.workers(8, step_back=True) == 8
    played.now()
    assert reader.workers(8, step_back=True) == 2
    assert reader.cause == "playing"
    now[0] += ATTENTION_SECONDS - 1
    assert reader.workers(8, step_back=True) == 2
    now[0] += 2
    assert reader.workers(8, step_back=True) == 8
    assert reader.cause is None


def test_input_comes_before_a_clip_and_a_clip_before_other_programs() -> None:
    since = _Input(1.0)
    reader = Attention(since, since_played=lambda: 1.0)
    reader.workers(8, step_back=True, others_busy=True)
    assert reader.cause == "input"
    since.seconds = None
    reader.workers(8, step_back=True, others_busy=True)
    assert (reader.cause, reader.holding) == ("playing", True)


def test_the_setting_off_or_a_benchmark_never_reads_the_clip() -> None:
    def refuses() -> float | None:
        raise AssertionError("the clip clock is not read with the setting off or while measuring")

    reader = Attention(_Input(None), since_played=refuses)
    assert reader.workers(8, step_back=False) == 8
    assert reader.workers(8, step_back=True, measuring=True) == 8


def test_the_shared_reading_hears_the_clock_every_player_and_wall_marks() -> None:
    assert _SHARED._since_played == attention.PLAYED.seconds_since


def test_a_benchmark_is_never_stepped_back_for_either_cause() -> None:
    def refuses() -> float | None:
        raise AssertionError("the input is not read while the device is being measured")

    reader = Attention(refuses)
    assert reader.workers(8, step_back=True, others_busy=True, measuring=True) == 8
    assert _holding(reader) is False
    assert reader.share_now == 100


def test_the_tick_counter_is_read_across_its_wrap() -> None:
    assert elapsed_seconds(15_000, 5_000) == 10.0
    # The counter wrapped between the input and now: 3 s before the wrap, 2 s after it.
    assert elapsed_seconds(2_000, (1 << 32) - 3_000) == 5.0


def test_the_reading_is_windows_only() -> None:
    since = attention.seconds_since_input()
    if sys.platform == "win32":
        assert since is not None
        assert since >= 0
    else:
        assert since is None


@pytest.fixture
def no_stand_in(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """The settings with neither stand-in, and the input calls looked up afresh, then forgotten."""
    monkeypatch.delenv("SIFT_ATTENTION_FAKE_INPUT_FILE", raising=False)
    _stand_in_from_the_environment(monkeypatch, tmp_path, None)
    looked_up = attention._input_api
    looked_up.cache_clear()
    yield
    looked_up.cache_clear()


def test_a_windows_whose_input_calls_will_not_load_reads_no_input(
    monkeypatch: pytest.MonkeyPatch, no_stand_in: None
) -> None:
    """A Windows with the system libraries refused (a locked-down session) cannot say whether
    anybody is there, so the step-back stays off and the pool runs its full count."""
    _ = no_stand_in

    def refused(_name: str) -> object:
        raise OSError("the library could not be loaded")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", refused, raising=False)

    assert attention.seconds_since_input() is None
    assert Attention().workers(6, step_back=True) == 6


class _User32:
    def __init__(self, answers: bool) -> None:
        self.answers = answers

    def GetLastInputInfo(self, info: Any) -> int:
        info._obj.dwTime = 1_000
        return int(self.answers)


class _Kernel32:
    def GetTickCount(self) -> int:
        return 4_000


class _LastInputInfo(ctypes.Structure):
    _fields_ = (("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong))


@pytest.mark.parametrize(("answers", "since"), [(True, 3.0), (False, None)])
def test_the_last_input_is_read_off_the_tick_counter_unless_windows_declines(
    monkeypatch: pytest.MonkeyPatch, no_stand_in: None, answers: bool, since: float | None
) -> None:
    _ = no_stand_in
    monkeypatch.setattr(
        attention, "_input_api", lambda: (_User32(answers), _Kernel32(), _LastInputInfo)
    )
    assert attention.seconds_since_input() == since


def test_with_no_stand_in_file_set_there_is_no_file_to_read(no_stand_in: None) -> None:
    _ = no_stand_in
    assert attention.stand_in_file_seconds() is None


def _stand_in_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, milliseconds: str | None
) -> None:
    """The settings as a boot reads them, with the stand-in named in the environment or not."""
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    if milliseconds is None:
        monkeypatch.delenv("SIFT_ATTENTION_FAKE_INPUT_MS", raising=False)
    else:
        monkeypatch.setenv("SIFT_ATTENTION_FAKE_INPUT_MS", milliseconds)
    settings = Settings()
    monkeypatch.setattr(attention, "get_settings", lambda: settings)


def test_the_stand_in_holds_the_pool_at_its_share_with_nobody_at_the_keyboard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """0 ms reads as somebody at the keyboard on every operating system, through the real reader."""
    _stand_in_from_the_environment(monkeypatch, tmp_path, "0")
    assert attention.seconds_since_input() == 0.0
    reader = Attention()
    assert reader.workers(6, step_back=True) == 2
    assert _holding(reader) is True


def test_the_stand_in_past_the_minute_keeps_the_full_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stand_in_from_the_environment(monkeypatch, tmp_path, "600000")
    assert attention.seconds_since_input() == 600.0
    assert Attention().workers(6, step_back=True) == 6


def test_no_stand_in_leaves_the_real_reading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stand_in_from_the_environment(monkeypatch, tmp_path, None)
    assert attention.stand_in_seconds() is None


def test_a_negative_stand_in_fails_the_boot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ValidationError):
        _stand_in_from_the_environment(monkeypatch, tmp_path, "-1")


def _turbo_mode(reader: Attention) -> bool:
    return reader.turbo_mode


def test_a_press_runs_the_full_count_while_somebody_is_here_and_a_second_press_steps_back() -> None:
    since = _Input(2.0)
    reader = Attention(since)
    assert reader.workers(8, step_back=True) == 2
    assert (_holding(reader), _turbo_mode(reader)) == (True, False)
    # The leaf pressed: every worker although the input is recent, said immediately.
    reader.press(full=True)
    assert (_holding(reader), _turbo_mode(reader)) == (False, True)
    assert reader.workers(8, step_back=True) == 8
    # Still somebody here a minute later: the press holds.
    since.seconds = 1.0
    assert reader.workers(8, step_back=True) == 8
    # The bolt pressed: a quarter again.
    reader.press(full=False)
    assert (_holding(reader), _turbo_mode(reader)) == (True, False)
    assert reader.workers(8, step_back=True) == 2


def test_the_log_says_when_a_press_takes_effect_not_only_when_the_cause_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uncached_log(monkeypatch, attention)
    reader = Attention(_Input(2.0))
    reader.workers(8, step_back=True)
    with capture_logs() as logs:
        reader.press(full=True)
        reader.workers(8, step_back=True)
        reader.workers(8, step_back=True)
        reader.press(full=False)
        reader.workers(8, step_back=True)
    assert [(one["event"], one.get("workers")) for one in logs] == [
        ("attention.turbo_mode_pressed", None),
        ("attention.turbo_mode", 8),
        ("attention.step_back_pressed", None),
        ("attention.stepping_back", 2),
    ]


def test_nobody_here_is_the_full_count_by_itself_with_nothing_to_press() -> None:
    """The negative case: left alone, the pool runs every worker, and neither state is said."""
    since = _Input(ATTENTION_SECONDS + 1)
    reader = Attention(since)
    assert reader.workers(8, step_back=True) == 8
    assert (_holding(reader), _turbo_mode(reader)) == (False, False)
    # A press while nobody is here is kept, and says nothing until somebody comes back.
    reader.press(full=True)
    assert reader.pressed is True
    assert _turbo_mode(reader) is False
    assert reader.workers(8, step_back=True) == 8
    since.seconds = 0.5
    assert reader.workers(8, step_back=True) == 8
    assert _turbo_mode(reader) is True
    # Gone again: still the full count, and nothing to say.
    since.seconds = ATTENTION_SECONDS * 2
    assert reader.workers(8, step_back=True) == 8
    assert (_holding(reader), _turbo_mode(reader)) == (False, False)


def test_the_setting_off_puts_nothing_in_play_pressed_or_not() -> None:
    reader = Attention(_Input(0.0))
    reader.press(full=True)
    assert reader.workers(8, step_back=False) == 8
    assert (_holding(reader), _turbo_mode(reader)) == (False, False)
    reader.press(full=False)
    assert reader.workers(8, step_back=False) == 8
    assert (_holding(reader), _turbo_mode(reader)) == (False, False)


def test_a_pool_of_one_puts_nothing_in_play_pressed_or_not() -> None:
    reader = Attention(_Input(0.0))
    reader.press(full=True)
    assert reader.workers(1, step_back=True) == 1
    assert (_holding(reader), _turbo_mode(reader)) == (False, False)


def test_every_window_is_told_when_the_step_back_comes_and_goes_and_on_each_press(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The leaf comes and goes with these, and nothing in the queue moves to make a window read."""
    told: list[About] = []
    monkeypatch.setattr(attention, "announce_now", lambda audience, about: told.append(about))
    since = _Input(0.0)
    reader = Attention(since)
    reader.workers(8, step_back=True)
    assert told == [About.JOBS]
    reader.workers(8, step_back=True)  # nothing moved: nothing said
    assert told == [About.JOBS]
    reader.press(full=True)
    reader.press(full=True)  # pressing what is on says nothing
    assert told == [About.JOBS] * 2
    since.seconds = ATTENTION_SECONDS + 1
    reader.workers(8, step_back=True)
    assert told == [About.JOBS] * 3
    reader.press(full=False)
    assert told == [About.JOBS] * 4


def test_the_stand_in_file_is_read_afresh_so_a_running_server_can_be_moved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stand_in = tmp_path / "since-input-ms"
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("SIFT_ATTENTION_FAKE_INPUT_MS", raising=False)
    monkeypatch.setenv("SIFT_ATTENTION_FAKE_INPUT_FILE", str(stand_in))
    settings = Settings()
    monkeypatch.setattr(attention, "get_settings", lambda: settings)

    reader = Attention()
    # No file yet: nothing to read, which is the full count.
    assert attention.seconds_since_input() is None
    assert reader.workers(6, step_back=True) == 6
    stand_in.write_text("0", encoding="ascii")
    assert attention.seconds_since_input() == 0.0
    assert reader.workers(6, step_back=True) == 2
    stand_in.write_text("600000\n", encoding="ascii")
    assert attention.seconds_since_input() == 600.0
    assert reader.workers(6, step_back=True) == 6
    stand_in.write_text("soon", encoding="ascii")
    assert attention.seconds_since_input() is None


def test_the_fixed_stand_in_wins_over_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stand_in = tmp_path / "since-input-ms"
    stand_in.write_text("600000", encoding="ascii")
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SIFT_ATTENTION_FAKE_INPUT_MS", "0")
    monkeypatch.setenv("SIFT_ATTENTION_FAKE_INPUT_FILE", str(stand_in))
    settings = Settings()
    monkeypatch.setattr(attention, "get_settings", lambda: settings)
    assert attention.seconds_since_input() == 0.0


def test_the_shared_reading_is_what_turbo_mode_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = Attention(_Input(1.0))
    monkeypatch.setattr(attention, "ATTENTION", reader)
    reader.workers(6, step_back=True)
    assert attention.turbo_mode() is False
    reader.press(full=True)
    assert attention.turbo_mode() is True


class _Held:
    """A task that runs until the test lets go, counting how many are in a worker's hand."""

    def __init__(self) -> None:
        self.active = 0
        self.release = asyncio.Event()

    async def handler(self, context: JobContext) -> None:
        self.active += 1
        try:
            await self.release.wait()
        finally:
            self.active -= 1


async def _until(predicate: Callable[[], bool], *, give_up_after: float = 5.0) -> None:
    deadline = time.monotonic() + give_up_after
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the condition never became true")


@pytest.mark.integration
async def test_the_pool_follows_the_reading_and_the_press_and_finishes_every_task_in_hand(
    job_queue: JobQueue,
) -> None:
    """Wired the way it ships: the pool asks the reading on its timer and converges on it.

    Nobody here: four workers, four tasks in hand. Somebody arrives: the pool is cut to its share
    (half, here) and the two workers told to go still hold their tasks (nothing cancelled). Turbo
    mode pressed: four again although somebody is here. Pressed again: two. Every task ends
    done.
    """
    held = _Held()
    register_handler("probe", held.handler, name="Test job")
    jobs = [await job_queue.enqueue("probe", {"asset_id": f"a{index}"}) for index in range(6)]
    since = _Input(ATTENTION_SECONDS + 1)
    reader = Attention(since)

    async def read_config() -> tuple[int, dict[str, int]]:
        return reader.workers(4, step_back=True, share=50), {}

    pool = WorkerPool(
        job_queue,
        concurrency=4,
        poll_interval=0.01,
        read_config=read_config,
        reconcile_interval=0.02,
    )
    await pool.start()
    try:
        await _until(lambda: held.active == 4)
        since.seconds = 0.0
        await _until(lambda: pool.concurrency == 2)
        # The two workers told to go are still holding their tasks.
        await asyncio.sleep(0.1)
        assert held.active == 4
        reader.press(full=True)
        await _until(lambda: pool.concurrency == 4)
        reader.press(full=False)
        await _until(lambda: pool.concurrency == 2)
        held.release.set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            counts = await job_queue.counts()
            if not counts.get("queued") and not counts.get("running"):
                break
            await asyncio.sleep(0.01)
    finally:
        held.release.set()
        await pool.stop()

    for job_id in jobs:
        job = await job_queue.get(job_id)
        assert job is not None
        assert job.state is JobState.DONE


def test_a_press_tells_its_listeners_once_and_a_failing_one_is_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uncached_log(monkeypatch, attention)
    reader = Attention(_Input(2.0))
    heard: list[str] = []

    def broken() -> None:
        raise RuntimeError("boom")

    unlisten = reader.listen(lambda: heard.append("pressed"))
    reader.listen(broken)
    with capture_logs() as logs:
        reader.press(full=True)
        reader.press(full=True)  # no change, nobody told
    assert heard == ["pressed"]
    assert "attention.listener_failed" in [one["event"] for one in logs]
    unlisten()
    unlisten()
    reader.press(full=False)
    assert heard == ["pressed"]


@pytest.mark.integration
async def test_a_press_takes_effect_immediately_not_at_the_next_reconfigure(
    job_queue: JobQueue,
) -> None:
    """The supervisor's interval is a minute here: only the press's wake can move the pool."""
    held = _Held()
    register_handler("probe", held.handler, name="Test job")
    for index in range(4):
        await job_queue.enqueue("probe", {"asset_id": f"a{index}"})
    reader = Attention(_Input(0.0))
    reader.workers(4, step_back=True, share=50)

    async def read_config() -> tuple[int, dict[str, int]]:
        return reader.workers(4, step_back=True, share=50), {}

    pool = WorkerPool(
        job_queue,
        concurrency=2,
        poll_interval=0.01,
        read_config=read_config,
        reconcile_interval=60.0,
        woken_by=(reader.listen,),
    )
    await pool.start()
    try:
        await _until(lambda: held.active == 2)
        started = time.monotonic()
        reader.press(full=True)
        await _until(lambda: pool.concurrency == 4, give_up_after=1.0)
        assert time.monotonic() - started < 0.1
        reader.press(full=False)
        await _until(lambda: pool.concurrency == 2, give_up_after=1.0)
    finally:
        held.release.set()
        await pool.stop()
    # Stopped: a press no longer reaches the pool.
    reader.press(full=True)
    assert pool.concurrency == 2


@pytest.mark.integration
async def test_work_arriving_is_claimed_now_not_at_the_next_poll(job_queue: JobQueue) -> None:
    held = _Held()
    register_handler("probe", held.handler, name="Test job")
    pool = WorkerPool(job_queue, concurrency=2, poll_interval=60.0, watchdog=False)
    await pool.start()
    try:
        await asyncio.sleep(0.05)  # both workers find nothing and go idle for a minute
        await job_queue.enqueue("probe", {"asset_id": "a0"})
        started = time.monotonic()
        pool.work_arrived()
        await _until(lambda: held.active == 1, give_up_after=1.0)
        # Well under the minute's poll; a loaded runner takes longer than a tenth to get there.
        assert time.monotonic() - started < 1.0
    finally:
        held.release.set()
        await pool.stop()
