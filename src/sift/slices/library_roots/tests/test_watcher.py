# SPDX-License-Identifier: AGPL-3.0-or-later
"""Watching a real folder with a real watcher and real files.

No fake observer: what the operating system reports when a file is copied, renamed or read is the
thing under test, so files are really written and the waits are real time, kept short.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

import pytest

from sift.kernel.ledger import Actor

if sys.platform == "win32":
    from sift.slices.library_roots.native_watch import SafeWindowsApiObserver
from watchdog.events import FileClosedNoWriteEvent
from watchdog.observers.polling import PollingEmitter

from sift.kernel.content import LibraryStore, Root, RootKind
from sift.kernel.jobs import JobQueue
from sift.kernel.log import configure_logging
from sift.slices.library_roots import jobs
from sift.slices.library_roots import watcher as watcher_module
from sift.slices.library_roots.tests.conftest import VIDEO_SECONDS, WINDOWS_ONLY, draw
from sift.slices.library_roots.watcher import LibraryWatcher

#: Long enough for inotify to deliver and the loop to tick.
QUIET = 0.3
TICK = 0.05

#: The cooldown is off for most tests: it is minutes long and they are about the first second.
NO_COOLDOWN = 0.0

#: The poller's interval, so a polling platform answers within the same wait as a native one.
POLL = 0.05


async def settle(seconds: float = QUIET * 3) -> None:
    """Give the real filesystem, the real observer thread and the real loop time to agree."""
    await asyncio.sleep(seconds)


async def scans_of(queue: JobQueue) -> list[dict[str, object]]:
    page = await queue.list(job_type=jobs.SCAN, limit=100)
    return [job.payload for job in page.jobs]


@pytest.fixture
async def watcher(
    library_store: LibraryStore, job_queue: JobQueue, handlers: None
) -> LibraryWatcher:
    return LibraryWatcher(
        library_store,
        job_queue,
        quiet_seconds=QUIET,
        tick_seconds=TICK,
        cooldown_seconds=NO_COOLDOWN,
        clock=asyncio.get_running_loop().time,
    )


async def test_a_file_dropped_in_gets_its_folder_scanned(
    watcher: LibraryWatcher,
    root: Root,
    root_path: Path,
    job_queue: JobQueue,
    library_store: LibraryStore,
) -> None:
    """The whole feature, end to end, through a real inotify."""
    (root_path / "inbox").mkdir()
    await watcher.start()
    await watcher.watching()
    try:
        draw(root_path / "inbox" / "new.mp4", "testsrc2=size=32x32:rate=5", VIDEO_SECONDS)
        await settle()
    finally:
        await watcher.stop()

    inbox = await library_store.upsert_folder(root.id, "inbox")
    # The file is named, so noticing it costs one `stat` rather than a walk.
    assert await scans_of(job_queue) == [
        {"root_id": root.id, "folder_id": inbox.id, "paths": ["inbox/new.mp4"]}
    ]


async def test_the_folder_is_named_by_id_and_never_by_path(
    watcher: LibraryWatcher, root: Root, root_path: Path, job_queue: JobQueue
) -> None:
    """A payload names the folder by id, never by path."""
    (root_path / "inbox").mkdir()
    await watcher.start()
    await watcher.watching()
    try:
        draw(root_path / "inbox" / "new.mp4", "testsrc2=size=32x32:rate=5", VIDEO_SECONDS)
        await settle()
    finally:
        await watcher.stop()

    payloads = await scans_of(job_queue)
    assert payloads, "the drop must have produced a scan"
    for payload in payloads:
        assert set(payload) <= {"root_id", "folder_id", "paths"}
        # Files inside are named by path, each checked by `check_rel_path` before reaching a job.
        assert "/" not in str(payload["root_id"])
        assert "/" not in str(payload["folder_id"])
        carried = payload.get("paths") or []
        assert isinstance(carried, list)
        for named in carried:
            assert not PurePosixPath(str(named)).is_absolute()
            assert ".." not in PurePosixPath(str(named)).parts


async def test_many_files_at_once_are_one_scan(
    watcher: LibraryWatcher, root: Root, root_path: Path, job_queue: JobQueue
) -> None:
    """Copying many files in queues one scan: the folder is not quiet until the copying stops."""
    (root_path / "inbox").mkdir()
    await watcher.start()
    await watcher.watching()
    try:
        for index in range(5):
            draw(
                root_path / "inbox" / f"clip{index}.mp4", f"testsrc2=size=32x{32 + index}:rate=5", 1
            )
        await settle()
    finally:
        await watcher.stop()

    assert len(await scans_of(job_queue)) == 1


async def test_a_file_still_being_written_is_not_scanned_yet(
    watcher: LibraryWatcher, root: Root, root_path: Path, job_queue: JobQueue
) -> None:
    """A file still being written is not scanned yet, or the digest would be of half a file."""
    (root_path / "inbox").mkdir()
    target = root_path / "inbox" / "slow.mp4"
    await watcher.start()
    await watcher.watching()
    try:
        with target.open("wb") as handle:
            for _ in range(6):
                handle.write(b"x" * 4096)
                handle.flush()
                await asyncio.sleep(QUIET / 2)
                assert await scans_of(job_queue) == [], (
                    "a folder somebody is still writing to has not gone quiet"
                )
        await settle()
    finally:
        await watcher.stop()

    assert len(await scans_of(job_queue)) == 1, "and once the writing stops, it is scanned"


async def test_a_file_renamed_into_the_folder_is_noticed(
    watcher: LibraryWatcher, root: Root, root_path: Path, job_queue: JobQueue
) -> None:
    """A file renamed into place arrives as the destination of a move, and is noticed."""
    (root_path / "inbox").mkdir()
    staged = root_path / "inbox" / "download.part"
    draw(staged.with_suffix(".mp4"), "testsrc2=size=32x32:rate=5", VIDEO_SECONDS)
    staged.with_suffix(".mp4").rename(staged)
    await settle()

    await watcher.start()
    await watcher.watching()
    try:
        staged.rename(root_path / "inbox" / "arrived.mp4")
        await settle()
    finally:
        await watcher.stop()

    assert len(await scans_of(job_queue)) == 1


async def test_a_folder_renamed_on_the_disk_is_noticed(
    watcher: LibraryWatcher, root: Root, root_path: Path, job_queue: JobQueue
) -> None:
    """Renaming a folder re-indexes its files where they now are: watchdog raises a moved event for
    each file inside. The folder's own row is not moved by this."""
    (root_path / "before").mkdir()
    draw(root_path / "before" / "clip.mp4", "testsrc2=size=32x32:rate=5", VIDEO_SECONDS)
    await settle()

    await watcher.start()
    await watcher.watching()
    try:
        (root_path / "before").rename(root_path / "after")
        await settle()
    finally:
        await watcher.stop()

    assert len(await scans_of(job_queue)) >= 1, "renaming a folder asked for no scan"


async def test_a_folder_that_is_merely_made_asks_for_nothing(
    watcher: LibraryWatcher, root: Root, root_path: Path, job_queue: JobQueue
) -> None:
    """A new empty folder asks for nothing: a poll would otherwise scan on a timer."""
    await watcher.start()
    await watcher.watching()
    try:
        (root_path / "empty").mkdir()
        await settle()
    finally:
        await watcher.stop()

    assert await scans_of(job_queue) == []


async def test_a_file_that_is_not_media_is_ignored(
    watcher: LibraryWatcher, root: Root, root_path: Path, job_queue: JobQueue
) -> None:
    """A folder of documents must not wake the scanner every time one is saved."""
    (root_path / "inbox").mkdir()
    await watcher.start()
    await watcher.watching()
    try:
        (root_path / "inbox" / "notes.txt").write_text("hello")
        (root_path / "inbox" / "sheet.csv").write_text("a,b")
        await settle()
    finally:
        await watcher.stop()

    assert await scans_of(job_queue) == []


async def test_reading_a_file_does_not_ask_for_a_scan(
    watcher: LibraryWatcher, root: Root, root_path: Path, job_queue: JobQueue
) -> None:
    """Reading a file, as a scan does, asks for no scan; otherwise every scan triggers the next."""
    (root_path / "inbox").mkdir()
    existing = root_path / "inbox" / "already.mp4"
    draw(existing, "testsrc2=size=32x32:rate=5", VIDEO_SECONDS)
    await settle()

    await watcher.start()
    await watcher.watching()
    try:
        with existing.open("rb") as handle:
            handle.read()
        await settle()
    finally:
        await watcher.stop()

    assert await scans_of(job_queue) == [], "reading a file is not a change to it"


async def test_writing_to_a_file_still_asks_for_a_scan(
    watcher: LibraryWatcher, root: Root, root_path: Path, job_queue: JobQueue
) -> None:
    """Closing a file that was written still asks for a scan."""
    (root_path / "inbox").mkdir()
    await watcher.start()
    await watcher.watching()
    try:
        draw(root_path / "inbox" / "arrived.mp4", "testsrc2=size=32x32:rate=5", VIDEO_SECONDS)
        await settle()
    finally:
        await watcher.stop()

    assert len(await scans_of(job_queue)) == 1


async def test_a_file_in_the_root_itself_scans_the_root(
    watcher: LibraryWatcher,
    root: Root,
    root_path: Path,
    job_queue: JobQueue,
    library_store: LibraryStore,
) -> None:
    """A file dropped on the root names no subfolder, so a press and the watcher share one payload
    shape (`jobs.scan_shape`); the named paths still travel."""
    await watcher.start()
    await watcher.watching()
    try:
        draw(root_path / "loose.mp4", "testsrc2=size=32x32:rate=5", VIDEO_SECONDS)
        await settle()
    finally:
        await watcher.stop()

    top = await library_store.root_folder(root.id)
    assert top is not None, "the root still has a folder row; the payload simply does not name it"
    assert await scans_of(job_queue) == [{"root_id": root.id, "paths": ["loose.mp4"]}]


async def test_a_network_share_is_listened_to_where_the_platform_serves_notifications(
    library_store: LibraryStore, job_queue: JobQueue, tmp_path: Path, handlers: None
) -> None:
    """Whether a share reports its own changes depends on the platform: Windows serves SMB2
    `CHANGE_NOTIFY`, so a share is listened to; a kernel watching its own disk polls it."""
    from watchdog.observers.polling import PollingObserver

    directory = tmp_path / "share"
    directory.mkdir()
    nas = await library_store.create_root(name="NAS", abs_path=directory, kind=RootKind.NAS)
    watcher = LibraryWatcher(
        library_store,
        job_queue,
        quiet_seconds=QUIET,
        tick_seconds=TICK,
    )

    await watcher.start()
    await watcher.watching()
    try:
        assert nas.kind is RootKind.NAS
        polled = [isinstance(o, PollingObserver) for o in watcher._observers]
        assert polled == [sys.platform != "win32"], (
            "a share is polled only where the platform's own watch could not hear it"
        )
    finally:
        await watcher.stop()


# --- the default watch: the platform's own notifications where they are usable -----------------


async def test_a_local_root_takes_the_best_watch_this_platform_has(
    library_store: LibraryStore, job_queue: JobQueue, tmp_path: Path, handlers: None
) -> None:
    """A local root takes the platform's native watch where usable. On Windows that is Sift's own
    emitter, since watchdog's closes the handle under a pending read."""
    from watchdog.observers.polling import PollingObserver

    directory = tmp_path / "local"
    directory.mkdir()
    await library_store.create_root(name="Local", abs_path=directory, kind=RootKind.LOCAL)
    watcher = LibraryWatcher(
        library_store,
        job_queue,
        quiet_seconds=QUIET,
        tick_seconds=TICK,
    )

    await watcher.start()
    await watcher.watching()
    try:
        (observer,) = watcher._observers
        if watcher_module._NATIVE_WATCH_IS_SAFE:
            assert not isinstance(observer, PollingObserver)
            if sys.platform == "win32":
                assert isinstance(observer, SafeWindowsApiObserver), (
                    "watchdog's own Windows emitter closes a handle with I/O outstanding on it"
                )
        else:
            assert isinstance(observer, PollingObserver)
    finally:
        await watcher.stop()


# --- when the folder, or the machine, does not cooperate --------------------------------------
# A watcher runs for months against disks that go away; none of it may take the watcher down.


async def test_a_folder_that_cannot_be_watched_does_not_stop_the_others(
    watcher: LibraryWatcher,
    library_store: LibraryStore,
    root: Root,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A folder that cannot be watched is a warning, and the others stay watched."""
    # The base class: `Observer` is a per-platform alias that cannot be patched.
    from watchdog.observers.api import BaseObserver

    real_schedule = BaseObserver.schedule
    first = root.abs_path

    def refuse_the_first(self: BaseObserver, handler: Any, path: str, **kwargs: Any) -> Any:
        if path == first:
            raise OSError(28, "No space left on device")
        return real_schedule(self, handler, path, **kwargs)

    monkeypatch.setattr(BaseObserver, "schedule", refuse_the_first)

    second = tmp_path / "second"
    second.mkdir()
    await library_store.create_root(name="Second", abs_path=second)

    await watcher.refresh()

    # The one that refused is simply not watched; the other one is.
    assert len(watcher._observers) == 1
    await watcher.stop()


async def test_a_root_ALREADY_POLLING_that_still_cannot_be_watched_gives_up_quietly(
    watcher: LibraryWatcher,
    library_store: LibraryStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A root already polling that still cannot be watched is given up on with a warning; there is
    nothing left to fall back to, and the other roots keep their watch."""
    from watchdog.observers.api import BaseObserver

    share = tmp_path / "share"
    share.mkdir()
    await library_store.create_root(name="Share", abs_path=share, kind=RootKind.NAS)
    # Pinned so a NAS root starts out polling on every platform.
    monkeypatch.setattr(watcher_module, "_NATIVE_WATCH_IS_SAFE", False)
    monkeypatch.setattr(
        BaseObserver,
        "schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(28, "No space left on device")),
    )

    await watcher.refresh()

    assert watcher._observers == []
    await watcher.stop()


async def test_a_file_outside_any_watched_root_is_ignored(
    watcher: LibraryWatcher, root: Root, tmp_path: Path, job_queue: JobQueue
) -> None:
    """An event outside every watched root scans nothing."""
    await watcher.refresh()

    watcher._notice(root.id, tmp_path / "elsewhere" / "stray.mp4")
    await settle()

    assert await scans_of(job_queue) == []
    await watcher.stop()


async def test_an_event_for_a_root_that_is_not_being_watched_is_ignored(
    watcher: LibraryWatcher, root: Root, root_path: Path, job_queue: JobQueue
) -> None:
    """A root removed while an event for it was still in flight."""
    await watcher.refresh()

    watcher._notice("01HX0000000000000000000009", root_path / "clip.mp4")
    await settle()

    assert await scans_of(job_queue) == []
    await watcher.stop()


async def test_a_root_removed_before_its_folder_goes_quiet_queues_nothing(
    watcher: LibraryWatcher, library_store: LibraryStore, root: Root, job_queue: JobQueue
) -> None:
    """A root removed before its folder goes quiet queues nothing."""
    await watcher.refresh()
    await library_store.delete_root(root.id, actor=Actor.sift("folder"))

    await watcher._scan(root.id, "")

    assert await scans_of(job_queue) == []
    await watcher.stop()


async def test_a_failure_in_one_sweep_does_not_kill_the_loop(
    watcher: LibraryWatcher, root: Root, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loop is the whole feature. Whatever one pass throws, the next one still happens."""
    passes = 0

    async def fail_once() -> None:
        nonlocal passes
        passes += 1
        if passes == 1:
            raise RuntimeError("the disk went away")

    monkeypatch.setattr(watcher, "_sweep_pending", fail_once)
    await watcher.start()
    await watcher.watching()
    await settle(TICK * 6)

    assert passes > 1, "the loop stopped at the first thing that went wrong"
    await watcher.stop()


async def test_stopping_a_watcher_that_never_started_is_fine(
    watcher: LibraryWatcher,
) -> None:
    """Shutdown runs whether or not boot got as far as starting this."""
    await watcher.stop()


async def test_a_folder_row_that_cannot_be_made_queues_nothing(
    watcher: LibraryWatcher, root: Root, job_queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder whose row cannot be made queues nothing, so no scan marks a library missing."""
    await watcher.refresh()

    async def gone(*args: object, **kwargs: object) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(watcher._library, "upsert_folder", gone)

    await watcher._scan(root.id, "clips")

    assert await scans_of(job_queue) == []
    await watcher.stop()


async def test_a_root_whose_own_folder_row_is_missing_queues_nothing(
    watcher: LibraryWatcher, root: Root, job_queue: JobQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same, for the row standing for the root itself."""
    await watcher.refresh()

    async def nothing(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(watcher._library, "root_folder", nothing)

    await watcher._scan(root.id, "")

    assert await scans_of(job_queue) == []
    await watcher.stop()


async def test_a_folder_that_never_stops_changing_is_scanned_once_per_cooldown(
    library_store: LibraryStore,
    job_queue: JobQueue,
    handlers: None,
    root: Root,
    root_path: Path,
) -> None:
    """A folder that never stops changing is walked once per cooldown, so overlapping walks of a
    slow filesystem cannot take every worker. A subfolder goes away each time, since a vanished
    folder is what needs a walk."""
    watcher = LibraryWatcher(
        library_store,
        job_queue,
        quiet_seconds=QUIET,
        tick_seconds=TICK,
        # Longer than the test, so only the cooldown can refuse the second burst.
        cooldown_seconds=60.0,
        clock=asyncio.get_running_loop().time,
    )
    (root_path / "inbox" / "first").mkdir(parents=True)
    (root_path / "inbox" / "second").mkdir()
    draw(root_path / "inbox" / "first" / "one.mp4", "testsrc2=size=32x32:rate=5", VIDEO_SECONDS)
    draw(root_path / "inbox" / "second" / "two.mp4", "testsrc2=size=32x32:rate=5", VIDEO_SECONDS)
    await watcher.start()
    await watcher.watching()
    try:
        (root_path / "inbox" / "first" / "one.mp4").unlink()
        (root_path / "inbox" / "first").rmdir()
        await settle()
        assert len(await scans_of(job_queue)) == 1, "the first change is scanned straight away"

        # A second change after the first scan was handed over.
        (root_path / "inbox" / "second" / "two.mp4").unlink()
        (root_path / "inbox" / "second").rmdir()
        await settle()

        assert len(await scans_of(job_queue)) == 1, "the second walk waits for the cooldown"
    finally:
        await watcher.stop()


async def test_a_file_that_arrives_is_not_held_back_by_a_walk_s_cooldown(
    library_store: LibraryStore,
    job_queue: JobQueue,
    handlers: None,
    root: Root,
    root_path: Path,
) -> None:
    """A pasted file (written, then renamed into place) is named and not held behind a walk's
    cooldown: a named scan is a `stat` per file."""
    watcher = LibraryWatcher(
        library_store,
        job_queue,
        quiet_seconds=QUIET,
        tick_seconds=TICK,
        cooldown_seconds=60.0,
        clock=asyncio.get_running_loop().time,
    )
    (root_path / "inbox" / "old").mkdir(parents=True)
    draw(root_path / "inbox" / "old" / "gone.mp4", "testsrc2=size=32x32:rate=5", VIDEO_SECONDS)
    await watcher.start()
    await watcher.watching()
    try:
        # A subfolder goes away, so the first scan of `inbox` is a walk and starts the cooldown.
        (root_path / "inbox" / "old" / "gone.mp4").unlink()
        (root_path / "inbox" / "old").rmdir()
        await settle()
        assert len(await scans_of(job_queue)) == 1

        # A paste: written under a non-media name and renamed, so the rename is the only event.
        made = root_path.parent / "source.mp4"
        draw(made, "testsrc2=size=32x32:rate=5", VIDEO_SECONDS)
        (root_path / "inbox" / "pasting.tmp").write_bytes(made.read_bytes())
        (root_path / "inbox" / "pasting.tmp").rename(root_path / "inbox" / "pasted.mp4")
        await settle()

        payloads = await scans_of(job_queue)
        assert len(payloads) == 2, "an arrival does not wait out a walk's rate limit"
        # By content: the queue's listing is not oldest-first.
        named = [payload for payload in payloads if payload.get("paths")]
        assert [str(payload["paths"]) for payload in named] == ["['inbox/pasted.mp4']"], (
            "a rename INTO a folder is the file arriving, named, rather than a walk of the folder"
        )
    finally:
        await watcher.stop()


async def test_waiting_on_a_watcher_that_was_never_started_returns_at_once(
    watcher: LibraryWatcher,
) -> None:
    """`watching()` before `start` returns at once: nothing is attaching."""
    await asyncio.wait_for(watcher.watching(), timeout=1)


# --- the poller pacing itself against what a walk actually costs -------------------------------
# A configured interval cannot hold over a slow walk, so the poller paces itself by what each walk
# cost. The emitter is driven directly with a walk whose cost the test decides.


def _paced(
    walk_seconds: float, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, list[float], list[float]]:
    """The real emitter, with watchdog's walk replaced by one that costs exactly `walk_seconds`
    on a clock that moves only when the walk moves it: on the real one, a busy machine's late
    wake from a sleep is read as part of the walk, and a cheap walk as an expensive one."""
    emitter = watcher_module._PacedPollingEmitter.__new__(watcher_module._PacedPollingEmitter)
    emitter._rest_seconds = 0.0
    emitter._reported = False
    # The attribute behind watchdog's read-only  property, which the log line reads.
    emitter._watch = SimpleNamespace(path="/library")  # type: ignore[assignment]

    waits: list[float] = []
    costs: list[float] = []
    now = [1_000.0]

    def walked(self: Any, timeout: float) -> None:
        # watchdog waits the interval and then walks inside this one call, so the code subtracts
        # the wait to find the walk's cost.
        waits.append(timeout)
        now[0] += timeout + walk_seconds
        costs.append(walk_seconds)

    monkeypatch.setattr(PollingEmitter, "queue_events", walked)
    monkeypatch.setattr(watcher_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return emitter, waits, costs


@pytest.mark.unit
def test_a_cheap_walk_polls_exactly_as_often_as_it_was_told_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cheap walk polls at exactly the configured interval."""
    emitter, waits, _ = _paced(0.002, monkeypatch)

    for _ in range(3):
        emitter.queue_events(0.05)

    assert waits == [0.05, 0.05, 0.05]


@pytest.mark.unit
def test_an_expensive_walk_backs_off_in_proportion_to_what_it_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expensive walk backs off so walking takes a quarter of the time; the first pass honours
    the setting."""
    emitter, waits, costs = _paced(0.2, monkeypatch)

    for _ in range(3):
        emitter.queue_events(0.01)

    assert waits[0] == 0.01, "the first pass has nothing to go on and obeys the setting"

    # Every bound scales with the measured cost: the share is a ratio.
    share = 1.0 / watcher_module.POLL_WALK_SHARE - 1.0
    for at, waited in enumerate(waits[1:]):
        wanted = costs[at] * share
        assert wanted * 0.95 <= waited <= wanted * 1.05, (
            f"a walk that cost {costs[at]:.3f}s should rest about {wanted:.3f}s, and it rested"
            f" {waited:.3f}s"
        )
        assert waited > 0.01, "and it is the rest that binds here, not the setting"


@pytest.mark.unit
def test_the_backing_off_has_a_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """The backing off has a ceiling, or a slow tree looks like a stopped watcher. The ceiling is
    moved for the test."""
    monkeypatch.setattr(watcher_module, "MAX_POLL_REST_SECONDS", 0.05)
    emitter, waits, _ = _paced(0.001, monkeypatch)
    emitter._rest_seconds = 9_999.0

    emitter.queue_events(0.01)

    assert waits == [0.05]


@pytest.mark.unit
def test_the_share_leaves_most_of_the_machine_to_the_person_using_it() -> None:
    """The share is held as a property, so changing the constant is deliberate."""
    assert 0 < watcher_module.POLL_WALK_SHARE <= 0.5


# --- letting go of a watch ----------------------------------------------------------------------


async def test_a_detached_handler_delivers_nothing(
    library_store: LibraryStore, job_queue: JobQueue, root: Root, root_path: Path, handlers: None
) -> None:
    """A detached handler delivers nothing, which makes abandoning an observer safe."""
    watcher = LibraryWatcher(
        library_store,
        job_queue,
        quiet_seconds=QUIET,
        tick_seconds=TICK,
        cooldown_seconds=NO_COOLDOWN,
        clock=asyncio.get_running_loop().time,
    )
    await watcher.start()
    await watcher.watching()
    try:
        (handler,) = watcher._handlers
        handler.detach()

        draw(root_path / "after.mp4", "testsrc2=size=32x32:rate=5", VIDEO_SECONDS)
        await settle()

        assert await scans_of(job_queue) == [], "a detached handler still delivered an event"
    finally:
        await watcher.stop()


async def test_an_observer_that_will_not_let_go_is_abandoned_and_says_so(
    library_store: LibraryStore,
    job_queue: JobQueue,
    handlers: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An observer whose `stop()` never returns is abandoned within a bound, and the log says so."""
    # Installed here: a handler keeps the stream current when it was configured.
    configure_logging("INFO", redact_personal=False)
    monkeypatch.setattr(watcher_module, "_STOP_SECONDS", 0.05)

    class WillNotStop:
        def stop(self) -> None:
            time.sleep(5)

        def join(self, timeout: float | None = None) -> None:
            time.sleep(5)

    watcher = LibraryWatcher(library_store, job_queue, quiet_seconds=QUIET, tick_seconds=TICK)
    handler = watcher_module._Events("a-root", asyncio.get_running_loop(), lambda *_a: None)
    watcher._observers.append(WillNotStop())  # type: ignore[arg-type]
    watcher._handlers.append(handler)

    began = time.monotonic()
    await asyncio.to_thread(watcher._stop_observers)
    took = time.monotonic() - began

    assert took < 4, "the stop was not bounded"
    # structlog writes to stdout, which caplog never sees.
    assert "library.watch_stop_abandoned" in capsys.readouterr().out
    assert watcher._observers == [], "the watcher still believes it is watching"


async def test_a_read_does_not_schedule_a_scan() -> None:
    """A close after reading schedules no scan; its write-side twin does. Handed straight to the
    handler, since no filesystem produces one on demand."""
    asked: list[str] = []
    handler = watcher_module._Events(
        "a-root", asyncio.get_running_loop(), lambda *args: asked.append(str(args))
    )

    handler.on_any_event(FileClosedNoWriteEvent(str(Path("clip.mp4"))))
    await asyncio.sleep(0)

    assert asked == [], "reading a file asked for the scan that would read it again"


# --- what may be named, and what must be walked ---------------------------------------------------
# Naming a file makes noticing it cost one `stat`; these are the cases where naming would be wrong.


async def test_a_folder_renamed_underneath_is_walked_and_not_named(
    watcher: LibraryWatcher, root: Root, root_path: Path, job_queue: JobQueue
) -> None:
    """A renamed folder is walked, not named, so its row moves and keeps its id and shares."""
    (root_path / "before").mkdir()
    draw(root_path / "before" / "one.mp4", "testsrc2=size=32x32:rate=5", VIDEO_SECONDS)
    await watcher.start()
    await watcher.watching()
    try:
        (root_path / "before").rename(root_path / "after")
        await settle()
    finally:
        await watcher.stop()

    payloads = await scans_of(job_queue)
    assert payloads, "the rename must have produced a scan"
    assert all("paths" not in payload for payload in payloads), (
        "a move must be answered by a walk, so the folder can be RECOGNISED rather than replaced"
    )


async def test_the_folder_a_move_left_gets_no_row(
    watcher: LibraryWatcher,
    root: Root,
    root_path: Path,
    job_queue: JobQueue,
    library_store: LibraryStore,
) -> None:
    """The source end of a folder renamed on the disk is a directory that has gone: its parent is
    walked, and no folder row is recorded for a path nothing is at."""
    (root_path / "before").mkdir()
    draw(root_path / "before" / "one.mp4", "testsrc2=size=32x32:rate=5", VIDEO_SECONDS)
    await watcher.start()
    await watcher.watching()
    try:
        (root_path / "before").rename(root_path / "after")
        await settle()
    finally:
        await watcher.stop()

    assert await library_store.folder_at(root.id, "before") is None, (
        "a walk of the folder the move left would record a row for a path that is not there"
    )
    assert await scans_of(job_queue), "the rename must still be walked"


async def test_a_deleted_file_is_named_and_not_walked(
    watcher: LibraryWatcher, root: Root, root_path: Path, job_queue: JobQueue
) -> None:
    """A deleted file is named, not walked: `look_at` leaves out a path not on the disk and the
    narrowed sweep marks it missing."""
    draw(root_path / "gone.mp4", "testsrc2=size=32x32:rate=5", VIDEO_SECONDS)
    await watcher.start()
    await watcher.watching()
    try:
        (root_path / "gone.mp4").unlink()
        await settle()
    finally:
        await watcher.stop()

    payloads = await scans_of(job_queue)
    assert payloads, "the delete must have produced a scan"
    assert [payload.get("paths") for payload in payloads] == [["gone.mp4"]], (
        "one file removed from a folder that is still there is one `stat`, not a walk of everything"
    )


async def test_a_folder_that_went_away_gives_its_parent_a_walk(
    watcher: LibraryWatcher,
    root: Root,
    root_path: Path,
    job_queue: JobQueue,
    library_store: LibraryStore,
) -> None:
    """A folder that went away gives its parent a walk: only the parent's listing tells a removal
    from a rename, as `_folders_that_moved` does at start-up."""
    (root_path / "shelf").mkdir()
    draw(root_path / "shelf" / "one.mp4", "testsrc2=size=32x32:rate=5", VIDEO_SECONDS)
    await watcher.start()
    await watcher.watching()
    try:
        (root_path / "shelf" / "one.mp4").unlink()
        (root_path / "shelf").rmdir()
        await settle()
    finally:
        await watcher.stop()

    payloads = await scans_of(job_queue)
    assert payloads, "the folder going away must have produced a scan"
    top = await library_store.root_folder(root.id)
    assert top is not None, "the root still has a folder row; the payload simply does not name it"
    assert all("paths" not in payload for payload in payloads), (
        "a folder that has gone cannot be answered by naming the files that were in it"
    )
    # The parent here is the root, whose walk names no folder; what is ruled out is a walk of
    # `shelf` itself.
    assert payloads == [{"root_id": root.id}], (
        "the walk is of the folder ABOVE, which is the only listing that can tell "
        "a folder that was removed from one that was renamed"
    )


async def test_a_root_that_went_away_asks_for_nothing(
    watcher: LibraryWatcher, root: Root, root_path: Path, job_queue: JobQueue
) -> None:
    """A root that went away asks for nothing: walking an unmounted root would mark every file
    missing. Driven through the sweep, since a watched root cannot be removed on Windows."""
    watcher._bases[root.id] = root_path
    watcher._notice(root.id, root_path / "one.mp4")
    root_path.rmdir()
    await asyncio.sleep(QUIET * 2)

    # Twice: without the guard the first pass looks blameless and the second walks the root.
    await watcher._sweep_pending()
    assert watcher._pending == {}, "an unreachable root must not leave itself armed to try again"
    await watcher._sweep_pending()

    assert await scans_of(job_queue) == [], "an unreachable root is not evidence about its files"


async def test_a_burst_for_a_root_taken_out_of_the_library_asks_nothing(
    watcher: LibraryWatcher, root_path: Path, job_queue: JobQueue
) -> None:
    """A root removed while a burst of its settles: a refresh drops its base, and the burst then
    asks the disk nothing and queues nothing."""
    gone = "a-root-no-longer-in-the-library"
    watcher._bases[gone] = root_path
    watcher._notice(gone, root_path / "one.mp4")
    del watcher._bases[gone]
    await asyncio.sleep(QUIET * 2)

    await watcher._sweep_pending()

    assert watcher._pending == {}, "the burst stayed armed for a root nobody watches"
    assert await scans_of(job_queue) == []


async def test_more_arrivals_than_the_cap_give_the_folder_back(
    watcher: LibraryWatcher, root: Root, root_path: Path, job_queue: JobQueue
) -> None:
    """Past the cap the paths are dropped and the folder is walked, for the rest of the burst."""
    (root_path / "many").mkdir()
    await watcher.start()
    await watcher.watching()
    try:
        # Touched rather than drawn: this counts paths.
        for index in range(jobs.MOST_NAMED_PATHS + 5):
            (root_path / "many" / f"{index}.mp4").write_bytes(b"not really a video")
        await settle()
    finally:
        await watcher.stop()

    payloads = await scans_of(job_queue)
    assert payloads, "the copy must have produced a scan"
    assert all("paths" not in payload for payload in payloads), (
        f"more than {jobs.MOST_NAMED_PATHS} at once is a walk, not a payload of paths"
    )


def test_a_folder_that_cannot_be_read_is_not_a_folder_that_has_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable folder is not a gone one, so trouble on the disk does not buy its biggest read.
    The errors are supplied, since `chmod` is ignored on Windows."""
    (tmp_path / "shelf").mkdir()
    real = os.stat

    def refuses(path: object, *args: object, **kwargs: object) -> object:
        if str(path).endswith("shelf"):
            raise PermissionError("the folder is there and will not say so")
        return real(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "stat", refuses)
    assert watcher_module._measure(tmp_path, "shelf", []).folder_is_there, (
        "a folder that will not answer is not a folder that has gone"
    )

    monkeypatch.undo()
    assert not watcher_module._measure(tmp_path, "missing", []).folder_is_there, (
        "and a folder that is really not there must still read as gone"
    )

    def is_a_file(path: object, *args: object, **kwargs: object) -> object:
        if str(path).endswith("shelf"):
            raise NotADirectoryError("a file now stands where the folder was")
        return real(path, *args, **kwargs)  # type: ignore[arg-type]

    # Windows answers this with "path not found", so the other platforms' error is supplied.
    monkeypatch.setattr(os, "stat", is_a_file)
    assert not watcher_module._measure(tmp_path, "shelf", []).folder_is_there, (
        "a folder that has become a file is a folder that has gone"
    )


def test_what_may_be_named_and_what_must_be_walked() -> None:
    """A rename over SMB can arrive with an empty source; it is still named, not read as a move
    across directories that walks the folder."""
    from watchdog.events import FileMovedEvent

    paired = FileMovedEvent("/library/inbox/working.tmp", "/library/inbox/arrived.mp4")
    assert watcher_module._can_be_named(paired), "a rename within one folder is a file arriving"

    unpaired = FileMovedEvent("", "/library/inbox/arrived.mp4")
    assert watcher_module._can_be_named(unpaired), (
        "a name that appeared with nothing saying where it came from is an arrival"
    )

    across = FileMovedEvent("/library/before/one.mp4", "/library/after/one.mp4")
    assert not watcher_module._can_be_named(across), (
        "a move BETWEEN folders may be a folder being renamed underneath, and needs the listing"
    )

    from watchdog.events import FileDeletedEvent

    assert watcher_module._can_be_named(FileDeletedEvent("/library/inbox/gone.mp4")), (
        "a file removed from a folder that is still there is one `stat`, not a walk"
    )


def test_a_move_with_nowhere_to_go_is_walked_rather_than_named() -> None:
    """A move with no destination is the file leaving, so the folder it left is walked."""
    from watchdog.events import FileMovedEvent

    leaving = FileMovedEvent("/library/inbox/one.mp4", "")

    assert not watcher_module._can_be_named(leaving)


def test_a_root_that_is_not_being_watched_has_no_path_inside_it() -> None:
    """A root not being watched has no base, so asking for a path inside it is a `LookupError`."""
    watching = LibraryWatcher.__new__(LibraryWatcher)
    watching._bases = {}

    with pytest.raises(LookupError):
        watching._path_of("01HX0000000000000000000000", Path("/library/one.mp4"))


async def test_a_root_that_is_already_polling_and_still_refuses_is_left_unwatched(
    library_store: LibraryStore,
    job_queue: JobQueue,
    tmp_path: Path,
    handlers: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A poller that refuses has no fallback: the root is left unwatched with a warning."""
    from watchdog.observers.api import BaseObserver

    directory = tmp_path / "local"
    directory.mkdir()
    await library_store.create_root(name="Share", abs_path=directory, kind=RootKind.NAS)

    def refuse(self: BaseObserver, handler: Any, path: str, **kwargs: Any) -> Any:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(BaseObserver, "schedule", refuse)
    watching = LibraryWatcher(
        library_store,
        job_queue,
        quiet_seconds=QUIET,
        tick_seconds=TICK,
    )

    await watching.start()
    await watching.watching()
    try:
        assert watching._observers == [], "a poller that refuses has nothing left to try"
    finally:
        await watching.stop()


async def test_a_native_watch_that_refuses_falls_back_to_a_poller_that_works(
    watcher: LibraryWatcher, root: Root, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A native watch that refuses falls back to a poller that works."""
    if sys.platform != "win32":
        pytest.skip("the native watch this falls back FROM only exists on Windows")

    refused: list[str] = []
    real_start = SafeWindowsApiObserver.start

    def refuse_the_native_one(self: Any) -> None:
        refused.append("native")
        raise OSError(1, "the platform said no")

    monkeypatch.setattr(SafeWindowsApiObserver, "start", refuse_the_native_one)
    await watcher.start()
    await watcher.watching()

    assert refused == ["native"], "the native watch has to have been tried first"
    assert len(watcher._observers) == 1, "and the root is watched by the poller instead"
    await watcher.stop()
    monkeypatch.setattr(SafeWindowsApiObserver, "start", real_start)


def test_a_machine_that_is_not_windows_takes_watchdog_s_own_observer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Away from Windows, the watchdog's own observer is used."""
    from watchdog.observers.api import BaseObserver

    monkeypatch.setattr(watcher_module, "_WINDOWS", False)
    watching = LibraryWatcher.__new__(LibraryWatcher)
    a_root = Root(
        id="01HX0000000000000000000000",
        name="Videos",
        abs_path="/library",
        kind=RootKind.LOCAL,
        created_at=0,
    )

    chosen = watching._observer_for(a_root, asyncio.new_event_loop(), polling=False)

    assert isinstance(chosen, BaseObserver)
    assert not isinstance(chosen, SafeWindowsApiObserver)


async def test_a_watch_that_loses_events_asks_for_the_catch_up_pass(
    watcher: LibraryWatcher, root: Root, job_queue: JobQueue
) -> None:
    """An overflow asks for the catch-up pass that already exists."""
    await watcher.start()
    await watcher.watching()

    watcher._overflowed(root.id, asyncio.get_running_loop())
    await settle()

    page = await job_queue.list(job_type=jobs.RECONCILE, limit=10)
    assert page.jobs, "an overflow has to reach the catch-up, or what it lost is lost for good"
    await watcher.stop()


def test_a_folder_that_goes_while_its_parent_is_still_settling_turns_that_into_a_walk() -> None:
    """A folder gone while its parent settles turns the parent's burst into a walk, keeping the
    deadline."""
    watching = LibraryWatcher.__new__(LibraryWatcher)
    root_id = "01HX0000000000000000000000"
    parent = watcher_module._Pending(due=500.0)
    parent.note("clips/one.mp4")
    watching._pending = {
        (root_id, "clips"): parent,
        (root_id, "clips/gone"): watcher_module._Pending(due=100.0),
    }

    watching._folder_went((root_id, "clips/gone"), now=100.0)

    assert (root_id, "clips/gone") not in watching._pending
    still = watching._pending[(root_id, "clips")]
    assert still.due == 500.0, "a settle-wait that is already running must not be cut short"
    assert still.paths is None, "and what it will do is a walk now, not a list of names"


def test_an_event_that_is_neither_a_move_nor_one_of_the_nameable_kinds_is_walked() -> None:
    """An event of any other kind is walked rather than named."""
    from watchdog.events import FileOpenedEvent

    assert not watcher_module._can_be_named(FileOpenedEvent("/library/clips/one.mp4"))


@WINDOWS_ONLY
def test_a_share_that_stopped_answering_is_not_a_folder_that_has_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sleeping share's error code is told from a missing folder's, so it is no walk."""
    (tmp_path / "shelf").mkdir()
    real = os.stat

    def share_is_off(path: Any, *args: Any, **kwargs: Any) -> Any:
        if str(path).endswith("shelf"):
            raise FileNotFoundError(2, "The network path was not found", str(path), 53)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", share_is_off)
    assert watcher_module._measure(tmp_path, "shelf", []).folder_is_there, (
        "a share that stopped answering is not a folder that has gone"
    )


# --- a watch that ended, attached again ----------------------------------------------------------


class _Held:
    """An observer that is already stopped: what is left of a watch whose share went away."""

    def stop(self) -> None:
        return None

    def join(self, timeout: float | None = None) -> None:
        return None


async def test_a_watch_that_ended_is_attached_again_on_the_third_try_and_catches_up(
    library_store: LibraryStore,
    job_queue: JobQueue,
    handlers: None,
    root: Root,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A watch that ended is let go, attached again on the third try, and the catch-up is asked
    for, since nothing reports what arrived meanwhile."""
    watching = LibraryWatcher(
        library_store,
        job_queue,
        quiet_seconds=QUIET,
        tick_seconds=TICK,
        cooldown_seconds=NO_COOLDOWN,
        reattach_seconds=0.01,
    )
    loop = asyncio.get_running_loop()
    dead = watcher_module._Events(root.id, loop, watching._notice)
    watching._keep(root, (_Held(), dead))  # type: ignore[arg-type]
    fresh: Any = (_Held(), watcher_module._Events(root.id, loop, watching._notice))
    attempts: list[str] = []

    def observe(asked: Root, _loop: object) -> object:
        attempts.append(asked.id)
        return fresh if len(attempts) == 3 else None

    monkeypatch.setattr(watching, "_observe", observe)
    try:
        await asyncio.to_thread(watching._ended, root.id, dead, loop)
        await settle(0.05)
        await asyncio.wait_for(watching._reattaching[root.id], 5)

        assert attempts == [root.id] * 3
        assert watching._watches[root.id] is fresh
        assert not dead.live, "the watch that ended was not let go of"
        page = await job_queue.list(job_type=jobs.RECONCILE, limit=10)
        assert [job.payload for job in page.jobs] == [{"root_id": root.id}]
    finally:
        await watching.stop()


async def test_a_watch_let_go_on_purpose_is_not_attached_again(
    library_store: LibraryStore, job_queue: JobQueue, handlers: None, root: Root
) -> None:
    """An ending from a watch already let go of on purpose is not attached again."""
    watching = LibraryWatcher(library_store, job_queue, reattach_seconds=0.01)
    loop = asyncio.get_running_loop()
    handler = watcher_module._Events(root.id, loop, watching._notice)
    watching._keep(root, (_Held(), handler))  # type: ignore[arg-type]
    handler.detach()

    watching._watch_ended(root.id, handler)

    assert watching._reattaching == {}


async def test_a_second_ending_while_a_re_attach_runs_starts_no_second_one(
    library_store: LibraryStore, job_queue: JobQueue, handlers: None, root: Root
) -> None:
    """One re-attach per root at a time: a second ending heard meanwhile is the same outage."""
    watching = LibraryWatcher(library_store, job_queue, reattach_seconds=0.01)
    loop = asyncio.get_running_loop()
    handler = watcher_module._Events(root.id, loop, watching._notice)
    watching._keep(root, (_Held(), handler))  # type: ignore[arg-type]
    running = asyncio.ensure_future(asyncio.sleep(5))
    watching._reattaching[root.id] = running
    try:
        watching._watch_ended(root.id, handler)

        assert watching._reattaching == {root.id: running}
    finally:
        running.cancel()
        await watching.stop()


async def test_a_root_removed_while_its_watch_was_away_ends_the_re_attach_quietly(
    library_store: LibraryStore,
    job_queue: JobQueue,
    handlers: None,
    root: Root,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing to come back to: the root is not asked for again and no catch-up is queued."""
    watching = LibraryWatcher(library_store, job_queue, reattach_seconds=0.01)

    async def removed(root_id: str) -> None:
        return None

    def observe(asked: Root, _loop: object) -> object:
        raise AssertionError("a removed root was attached again")

    monkeypatch.setattr(library_store, "get_root", removed)
    monkeypatch.setattr(watching, "_observe", observe)

    await asyncio.wait_for(watching._reattach(root.id), 5)

    assert root.id not in watching._watches
    assert (await job_queue.list(job_type=jobs.RECONCILE, limit=10)).jobs == []


@pytest.mark.parametrize("attached", [True, False], ids=["attached", "still-away"])
async def test_a_watch_that_attaches_as_the_re_attach_is_cancelled_is_let_go_of(
    library_store: LibraryStore,
    job_queue: JobQueue,
    handlers: None,
    root: Root,
    monkeypatch: pytest.MonkeyPatch,
    attached: bool,
) -> None:
    """A watch that attaches as the re-attach is cancelled is let go of."""
    import threading

    watching = LibraryWatcher(library_store, job_queue, reattach_seconds=0.01)
    loop = asyncio.get_running_loop()
    late = watcher_module._Events(root.id, loop, watching._notice)
    asked = threading.Event()
    answer = threading.Event()

    def observe(_root: Root, _loop: object) -> object:
        asked.set()
        answer.wait(5)
        return (_Held(), late) if attached else None

    monkeypatch.setattr(watching, "_observe", observe)
    task = asyncio.create_task(watching._reattach(root.id))
    await asyncio.to_thread(asked.wait, 5)

    task.cancel()
    await asyncio.sleep(0.05)
    answer.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)

    assert late.live is not attached, "the watch that attached late was left running"
    assert root.id not in watching._watches


def _switched_off(queue: JobQueue, job_type: str) -> None:
    from sift.kernel.jobs.switchboard import Switch

    async def off() -> bool:
        return False

    queue.switchboard.declare(Switch(key="test.off", refusal="switched off", on=off), job_type)


async def test_a_catch_up_that_is_switched_off_is_not_asked_for_and_is_not_a_failure(
    watcher: LibraryWatcher, root: Root, job_queue: JobQueue
) -> None:
    """The machine asked, not somebody: the refusal is the answer, never an unretrieved raise."""
    _switched_off(job_queue, jobs.RECONCILE)

    await watcher._catch_up(root.id)

    assert (await job_queue.list(job_type=jobs.RECONCILE, limit=10)).jobs == []


async def test_a_scan_that_is_switched_off_is_not_asked_for_by_the_watch(
    watcher: LibraryWatcher, root: Root, job_queue: JobQueue
) -> None:
    """Caught where it is asked, so the sweep goes on to the other folders that were ready."""
    _switched_off(job_queue, jobs.SCAN)

    await watcher._scan(root.id, "")

    assert await scans_of(job_queue) == []
