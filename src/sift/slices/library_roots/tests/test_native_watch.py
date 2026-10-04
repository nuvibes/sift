# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Windows watch, and the letting-go that is the reason it exists.

Stopping a watch as watchdog does is undefined on Windows: a stop that never returns, or a
completion landing in freed memory. Neither shows as a failing assertion, so these start and stop
watches many times and require the process to survive and each stop to return promptly.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from watchdog.events import FileSystemEventHandler

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="ReadDirectoryChangesW is Windows' own notification mechanism"
)

if sys.platform == "win32":
    from sift.slices.library_roots import native_watch
    from sift.slices.library_roots.native_watch import (
        SafeWindowsApiEmitter,
        SafeWindowsApiObserver,
    )


class _Collect(FileSystemEventHandler):
    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    def on_any_event(self, event: object) -> None:
        self.seen.append((event.event_type, str(event.src_path)))  # type: ignore[attr-defined]


def _watching(where: Path) -> tuple[SafeWindowsApiObserver, _Collect]:
    heard = _Collect()
    observer = SafeWindowsApiObserver(timeout=0.2)
    observer.schedule(heard, str(where), recursive=True)
    observer.start()
    return observer, heard


def _stopped_within(observer: SafeWindowsApiObserver, seconds: float = 5.0) -> float:
    """Stop it on a thread of its own and give up waiting rather than hanging: `Observer.stop()`
    joins with no timeout. The daemon thread is not joined, and the thread count catches a stop
    that never returned."""
    began = time.monotonic()
    returned = threading.Event()

    def stop() -> None:
        observer.stop()
        returned.set()

    threading.Thread(target=stop, daemon=True, name="stop-the-watch").start()
    if not returned.wait(seconds):
        raise AssertionError(
            f"stop() did not return inside {seconds}s. That is the unbounded stop this file "
            "exists for (see the module docstring)."
        )
    return time.monotonic() - began


def test_a_file_that_appears_is_reported(tmp_path: Path) -> None:
    """The point of all of it: the operating system says so, rather than being asked over and over."""
    observer, heard = _watching(tmp_path)
    try:
        time.sleep(0.5)
        (tmp_path / "arrived.mp4").write_bytes(b"x" * 1024)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not heard.seen:
            time.sleep(0.05)
    finally:
        _stopped_within(observer)
        observer.join(5)

    assert any("arrived.mp4" in path for _, path in heard.seen), heard.seen


def _move_the_access_time(path: Path, to_ns: int) -> None:
    """Set a file's last-access time and nothing else. `os.utime` writes the modification time as
    well, even to the value it had, and Windows reports that as a write."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    write_attributes, share_all, open_existing, backup_semantics = 0x0100, 0x7, 3, 0x02000000
    handle = kernel32.CreateFileW(
        str(path), write_attributes, share_all, None, open_existing, backup_semantics, None
    )
    assert handle not in (None, wintypes.HANDLE(-1).value), ctypes.get_last_error()
    ticks = to_ns // 100 + 116_444_736_000_000_000
    moment = wintypes.FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)
    try:
        assert kernel32.SetFileTime(wintypes.HANDLE(handle), None, ctypes.byref(moment), None)
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def test_a_file_that_is_only_read_is_not_reported(tmp_path: Path) -> None:
    """A read moves a file's last-access time and nothing a scan decides by. With that time among
    the changes asked for, every file a pass opened would come back as changed and ask for a
    scan."""
    read = tmp_path / "only-read.mp4"
    read.write_bytes(b"x" * 1024)
    stat = read.stat()
    observer, heard = _watching(tmp_path)
    try:
        time.sleep(0.5)
        # The access time alone, set by hand: what a read does on a volume that keeps that time,
        # without depending on this volume keeping it (or on when it last wrote it down).
        _move_the_access_time(read, stat.st_atime_ns + 3_600_000_000_000)
        read.read_bytes()
        (tmp_path / "arrived.mp4").write_bytes(b"x" * 1024)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not any(
            "arrived.mp4" in path for _, path in heard.seen
        ):
            time.sleep(0.05)
        time.sleep(0.3)
    finally:
        _stopped_within(observer)
        observer.join(5)

    assert any("arrived.mp4" in path for _, path in heard.seen), heard.seen
    assert not any("only-read.mp4" in path for _, path in heard.seen), heard.seen


def test_a_stop_returns_promptly_while_a_read_is_outstanding(tmp_path: Path) -> None:
    """A stop returns promptly while a read is parked waiting, the state watchdog's stop could not
    leave."""
    observer, _ = _watching(tmp_path)
    time.sleep(0.5)

    took = _stopped_within(observer, 10)
    observer.join(10)

    assert not observer.is_alive(), "the observer never let go"
    assert took < 5, f"letting go took {took:.1f}s, which is a stop that is not bounded"


def test_starting_and_stopping_many_times_over_does_not_take_the_process_down(
    tmp_path: Path,
) -> None:
    """Fifty start-and-stop rounds with a change in flight leave the process running and no thread
    behind."""
    before = threading.active_count()
    for round_number in range(50):
        observer, _ = _watching(tmp_path)
        (tmp_path / f"{round_number}.mp4").write_bytes(b"x" * 256)
        _stopped_within(observer)
        observer.join(5)
        assert not observer.is_alive(), f"round {round_number} never let go"

    # A little slack for a pytest plugin's own thread.
    time.sleep(0.5)
    assert threading.active_count() <= before + 2


def test_a_watch_on_a_folder_that_goes_away_stops_rather_than_spinning(tmp_path: Path) -> None:
    """A share that disconnects, or a folder somebody deleted.

    The read fails, the emitter stops issuing new ones, and the letting-go still closes what it
    opened. The library is not left with a thread burning a core on a handle that will never answer,
    and the watcher, told the watch ended, attaches again and catches up (see `test_watcher`).
    """
    directory = tmp_path / "goes"
    directory.mkdir()
    observer, _ = _watching(directory)
    try:
        time.sleep(0.5)
        directory.rmdir()
        time.sleep(1.0)
    finally:
        took = _stopped_within(observer, 10)
        observer.join(10)
        assert not observer.is_alive()
        assert took < 5


# --- what the operating system says NO in ------------------------------------------------------
#
#
# Each branch is a kernel32 call refusing, which cannot be produced on demand, so the calls are
# stood in for; the handle sequence itself is driven for real above.


#: A stand-in handle: the code only tells it from `None`.
_A_HANDLE: Any = 1
_ANOTHER_HANDLE: Any = 7


class _Heard:
    """What this module logged, recorded off the logger, since structlog's output destination
    depends on what else has run."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def info(self, event: str, **kwargs: object) -> None:
        self.events.append(event)

    def warning(self, event: str, **kwargs: object) -> None:
        self.events.append(event)


def _recording(into: list[Any], what: Any) -> Any:
    """A stood-in kernel32 call that writes down that it happened and reports success."""

    def call(*_args: object) -> int:
        into.append(what)
        return 1

    return call


def _an_emitter(where: Path, **kwargs: object) -> SafeWindowsApiEmitter:
    from watchdog.observers.api import ObservedWatch

    emitter = SafeWindowsApiEmitter.__new__(SafeWindowsApiEmitter)
    emitter.__init__(  # type: ignore[misc]
        event_queue=queue.Queue(),
        watch=ObservedWatch(str(where), recursive=True),
        timeout=0.1,
        **kwargs,
    )
    return emitter


def test_a_folder_that_will_not_open_refuses_rather_than_watching_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raised rather than carried, so the watcher above falls back to polling.

    A directory that will not open is the one honest reason to walk it instead, and a watch that
    quietly attached to nothing would look exactly like a library where nobody adds files.
    """
    monkeypatch.setattr(native_watch, "_CreateFileW", lambda *args: 0)

    with pytest.raises(OSError):
        _an_emitter(tmp_path).on_thread_start()


def test_a_read_the_system_aborted_is_not_reported_as_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled read reports `ERROR_OPERATION_ABORTED`, and that is the letting-go working.

    Warning about it would put a line in the log every time somebody closed Sift, which is how a
    log stops being read.
    """
    emitter = _an_emitter(tmp_path)
    emitter._whandle = _A_HANDLE
    emitter._reading = True
    monkeypatch.setattr(native_watch, "_WaitForSingleObject", lambda *args: 0)
    monkeypatch.setattr(native_watch, "_GetOverlappedResult", lambda *args: 0)
    monkeypatch.setattr(
        native_watch.ctypes,  # type: ignore[attr-defined]
        "get_last_error",
        lambda: native_watch.ERROR_OPERATION_ABORTED,  # type: ignore[attr-defined]
    )
    heard = _Heard()
    monkeypatch.setattr(native_watch, "log", heard)

    assert emitter._read_events() == []
    assert heard.events == [], (
        "a line every time somebody closes Sift is how a log stops being read"
    )


def test_a_read_that_failed_for_another_reason_says_so_and_ends_the_watch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anything but the abort is worth a line: it is the watch going deaf, and nothing else in the
    application would ever say so. And it ENDS the watch, and says so to the watcher: re-issuing
    the read on the same handle answers the same no at once, which is a loop that spins a core."""
    ended: list[bool] = []
    emitter = _an_emitter(tmp_path, on_ended=lambda: ended.append(True))
    emitter._whandle = _A_HANDLE
    emitter._reading = True
    monkeypatch.setattr(native_watch, "_WaitForSingleObject", lambda *args: 0)
    monkeypatch.setattr(native_watch, "_GetOverlappedResult", lambda *args: 0)
    monkeypatch.setattr(native_watch, "_CancelIoEx", lambda *args: 1)
    monkeypatch.setattr(native_watch.ctypes, "get_last_error", lambda: 5)  # type: ignore[attr-defined]
    heard = _Heard()
    monkeypatch.setattr(native_watch, "log", heard)

    assert emitter._read_events() == []
    assert heard.events == ["library.native_watch_read_failed", "library.native_watch_ended"]
    assert ended == [True]
    assert not emitter.should_keep_running()


def test_a_share_s_overflow_asks_for_the_catch_up_and_keeps_watching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A share reports "too many changes, list it yourself" as an error code where a local disk
    completes with zero bytes. The same answer either way, and the watch goes on."""
    asked: list[bool] = []
    ended: list[bool] = []
    emitter = _an_emitter(
        tmp_path, on_overflow=lambda: asked.append(True), on_ended=lambda: ended.append(True)
    )
    emitter._whandle = _A_HANDLE
    emitter._reading = True
    monkeypatch.setattr(native_watch, "_WaitForSingleObject", lambda *args: 0)
    monkeypatch.setattr(native_watch, "_GetOverlappedResult", lambda *args: 0)
    monkeypatch.setattr(
        native_watch.ctypes,  # type: ignore[attr-defined]
        "get_last_error",
        lambda: native_watch.ERROR_NOTIFY_ENUM_DIR,
    )

    assert emitter._read_events() == []
    assert (asked, ended) == ([True], [])
    assert emitter.should_keep_running()


def test_a_buffer_the_system_overflowed_asks_for_the_catch_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Nothing happened" and "everything happened at once" are the same zero.

    Read as quiet, an overflow is changes lost for good. Reported, it is the same question the
    catch-up pass already answers, so it asks for that rather than for anything new.
    """
    asked: list[bool] = []
    emitter = _an_emitter(tmp_path, on_overflow=lambda: asked.append(True))
    emitter._whandle = _A_HANDLE
    emitter._reading = True
    monkeypatch.setattr(native_watch, "_WaitForSingleObject", lambda *args: 0)
    monkeypatch.setattr(native_watch, "_GetOverlappedResult", lambda *args: 1)

    assert emitter._read_events() == []
    assert asked == [True], "an overflow that tells nobody is a library that quietly stops updating"


def test_a_watch_asked_to_read_after_letting_go_reads_nothing(tmp_path: Path) -> None:
    """The handle is on its way out. Beginning another read here is the exact race the letting-go
    was written to close."""
    emitter = _an_emitter(tmp_path)
    emitter._whandle = _A_HANDLE
    emitter._letting_go = True

    assert emitter._read_events() == []

    emitter._whandle = None
    emitter._letting_go = False
    assert emitter._read_events() == []


def test_a_directory_that_goes_while_it_is_watched_ends_the_read_rather_than_spinning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not fatal and not this class's to decide about: the emitter STOPS its own loop and tells the
    watcher, once, which attaches again when the folder answers and catches up what it missed.
    Asked again on the same handle, the read answers the same no at once: a spin."""
    ended: list[bool] = []
    emitter = _an_emitter(tmp_path, on_ended=lambda: ended.append(True))
    emitter._whandle = _A_HANDLE
    monkeypatch.setattr(native_watch, "_ResetEvent", lambda *args: 1)
    monkeypatch.setattr(native_watch, "_ReadDirectoryChangesW", lambda *args: 0)
    monkeypatch.setattr(native_watch, "_CancelIoEx", lambda *args: 1)

    heard = _Heard()
    monkeypatch.setattr(native_watch, "log", heard)

    assert emitter._begin() is False
    assert emitter._reading is False
    assert heard.events == ["library.native_watch_ended"]
    assert ended == [True]
    assert not emitter.should_keep_running(), "the loop would ask again, and again"
    # And the loop's next turn reads nothing and says nothing more.
    assert emitter._read_events() == []
    assert ended == [True]


def test_a_watch_being_let_go_that_fails_on_its_way_out_is_not_an_ending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stop somebody asked for is the letting-go, not the folder going. Told to the watcher as an
    ending, every refresh would start a re-attach of a watch it had just let go of on purpose."""
    ended: list[bool] = []
    emitter = _an_emitter(tmp_path, on_ended=lambda: ended.append(True))
    emitter._whandle = _A_HANDLE
    emitter._letting_go = True
    monkeypatch.setattr(native_watch, "_ResetEvent", lambda *args: 1)
    monkeypatch.setattr(native_watch, "_ReadDirectoryChangesW", lambda *args: 0)

    assert emitter._begin() is False
    assert ended == []


def test_letting_go_of_a_watch_that_never_opened_anything_does_nothing(tmp_path: Path) -> None:
    """Stopping before the thread started, and stopping twice. Both are ordinary (a refresh that
    changes nothing stops every watch it holds), and neither may touch a handle that is not there."""
    emitter = _an_emitter(tmp_path)

    emitter.on_thread_stop()
    emitter._let_go()

    assert emitter._whandle is None


def test_letting_go_waits_for_an_outstanding_read_before_the_handle_is_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Letting go collects the outstanding read before the handle is closed; the order is what is
    asserted."""
    done: list[str] = []
    monkeypatch.setattr(native_watch, "_GetOverlappedResult", _recording(done, "collected"))
    monkeypatch.setattr(native_watch, "_CloseHandle", _recording(done, "closed"))

    emitter = _an_emitter(tmp_path)
    emitter._whandle = _A_HANDLE
    emitter._ready = _A_HANDLE
    emitter._reading = True

    emitter._let_go()

    # `_CancelIoEx` has its own test below.
    assert done == ["collected", "closed", "closed"], (
        "the outstanding read has to be collected before the handle it is writing into is closed"
    )
    assert emitter._reading is False


def test_the_read_is_cancelled_AGAIN_here_or_the_collect_can_wait_for_ever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read is cancelled again before the collect: a cancel from another thread can land before
    the read is issued and cancel nothing."""
    done: list[str] = []
    monkeypatch.setattr(native_watch, "_CancelIoEx", _recording(done, "cancelled"))
    monkeypatch.setattr(native_watch, "_GetOverlappedResult", _recording(done, "collected"))
    monkeypatch.setattr(native_watch, "_CloseHandle", _recording(done, "closed"))

    emitter = _an_emitter(tmp_path)
    emitter._whandle = _A_HANDLE
    emitter._ready = _A_HANDLE
    # The state the race leaves: asked to stop, and a read issued after the asking.
    emitter._letting_go = True
    emitter._reading = True

    emitter._let_go()

    assert done[0] == "cancelled", (
        "the read was collected without being cancelled first, which is a wait for a change that "
        "may never come"
    )
    assert done[:2] == ["cancelled", "collected"]


def test_nothing_outstanding_is_not_cancelled_on_the_way_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half. A cancel issued whatever the state would be a call on a handle with no
    operation on it every time a watch is let go: harmless, and a claim about the state that is
    not true. The close still happens."""
    done: list[str] = []
    monkeypatch.setattr(native_watch, "_CancelIoEx", _recording(done, "cancelled"))
    monkeypatch.setattr(native_watch, "_CloseHandle", _recording(done, "closed"))

    emitter = _an_emitter(tmp_path)
    emitter._whandle = _A_HANDLE
    emitter._ready = None
    emitter._reading = False

    emitter._let_go()

    assert done == ["closed"]


def test_letting_go_with_nothing_outstanding_simply_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No read in flight, and no event to close either: the state a watch that refused is in."""
    closed: list[int] = []
    monkeypatch.setattr(native_watch, "_CloseHandle", _recording(closed, _ANOTHER_HANDLE))

    emitter = _an_emitter(tmp_path)
    emitter._whandle = _ANOTHER_HANDLE
    emitter._ready = None
    emitter._reading = False

    emitter._let_go()

    assert closed == [7]


def test_an_overflow_with_nobody_listening_is_still_not_read_as_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The handler is optional (a watch stood up outside the application has none), and the line
    in the log is what is left. It has to be there, because zero bytes moved is otherwise
    indistinguishable from a quarter of a second in which nothing happened."""
    emitter = _an_emitter(tmp_path)
    emitter._whandle = _A_HANDLE
    emitter._reading = True
    monkeypatch.setattr(native_watch, "_WaitForSingleObject", lambda *args: 0)
    monkeypatch.setattr(native_watch, "_GetOverlappedResult", lambda *args: 1)

    heard = _Heard()
    monkeypatch.setattr(native_watch, "log", heard)

    assert emitter._read_events() == []
    assert heard.events == ["library.native_watch_overflowed"]


def test_a_share_s_overflow_with_nobody_listening_is_said_in_the_log_and_the_watch_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The share's form of the overflow, with no handler: the log line is what is left, and the
    watch is not ended over it."""
    emitter = _an_emitter(tmp_path)
    emitter._whandle = _A_HANDLE
    emitter._reading = True
    monkeypatch.setattr(native_watch, "_WaitForSingleObject", lambda *args: 0)
    monkeypatch.setattr(native_watch, "_GetOverlappedResult", lambda *args: 0)
    monkeypatch.setattr(
        native_watch.ctypes,  # type: ignore[attr-defined]
        "get_last_error",
        lambda: native_watch.ERROR_NOTIFY_ENUM_DIR,
    )
    heard = _Heard()
    monkeypatch.setattr(native_watch, "log", heard)

    assert emitter._read_events() == []
    assert heard.events == ["library.native_watch_overflowed"]
    assert emitter.should_keep_running()


def test_a_read_that_cannot_be_begun_waits_for_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no read outstanding there is no completion to wait for: waiting on the event would
    park the thread for the whole timeout on a read that was never issued."""
    ended: list[bool] = []
    emitter = _an_emitter(tmp_path, on_ended=lambda: ended.append(True))
    emitter._whandle = _A_HANDLE
    monkeypatch.setattr(native_watch, "_ResetEvent", lambda *args: 1)
    monkeypatch.setattr(native_watch, "_ReadDirectoryChangesW", lambda *args: 0)
    monkeypatch.setattr(native_watch, "_CancelIoEx", lambda *args: 1)

    def waited(*args: object) -> int:
        raise AssertionError("waited on a read that was never begun")

    monkeypatch.setattr(native_watch, "_WaitForSingleObject", waited)

    assert emitter._read_events() == []
    assert ended == [True]
