# SPDX-License-Identifier: AGPL-3.0-or-later
"""A pass waiting on files still being read says no less time than the read does.

Priced from its own items alone (a thumbnail is a fifth of a second), Generate would say "under a
minute" for most of a first import and finish minutes later, because every file it counts is
behind the read.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

from sift.kernel.jobs import register_handler
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.ledger import Estimate
from sift.kernel.jobs.queue_rows import FilesToRead
from sift.kernel.jobs.switchboard import Switchboard
from sift.slices.media_jobs.activity_families import (
    AFTER_THE_BENCHMARK,
    NOT_KNOWN_UNTIL_COUNTED,
    PACE_WINDOW_SECONDS,
    PACED_BY_SHARE,
    PAUSED_FOR_THE_BENCHMARK,
    ShareWaits,
    _families,
    not_before_the_read,
    not_known_yet,
    pictured_in_the_read,
)
from sift.slices.media_jobs.router import WAITING_FOR_THE_SCAN, FamilyOfWork, KindOfWork

pytestmark = pytest.mark.unit

ROUTER = sys.modules[not_known_yet.__module__]
ROUTES = sys.modules["sift.slices.media_jobs.router"]


def _family(label: str, *, waiting: int, quick: int | None, slow: int | None) -> FamilyOfWork:
    return FamilyOfWork(
        label=label, types=[], waiting=waiting, quick_seconds=quick, slow_seconds=slow, task=None
    )


def test_a_pass_after_the_read_says_at_least_what_the_read_says() -> None:
    answer = {
        "scan": _family("Scan", waiting=80, quick=120, slow=240),
        "generate": _family("Generate", waiting=230, quick=3, slow=49),
        "identify": _family("Identify", waiting=40, quick=10, slow=20),
    }

    said = not_before_the_read(answer)

    assert (said["generate"].quick_seconds, said["generate"].slow_seconds) == (120, 240)
    # Identify's files come from the read as well.
    assert (said["identify"].quick_seconds, said["identify"].slow_seconds) == (120, 240)


def test_where_the_read_cannot_say_neither_can_the_pass_after_it() -> None:
    answer = {
        "scan": _family("Scan", waiting=80, quick=None, slow=None),
        "generate": _family("Generate", waiting=230, quick=3, slow=3),
    }

    said = not_before_the_read(answer)

    assert said["generate"].quick_seconds is None
    assert said["generate"].slow_seconds is None


def test_once_everything_is_read_a_pass_keeps_its_own_price() -> None:
    answer = {
        "scan": _family("Scan", waiting=0, quick=None, slow=None),
        "generate": _family("Generate", waiting=30, quick=20, slow=40),
    }

    said = not_before_the_read(answer)

    assert (said["generate"].quick_seconds, said["generate"].slow_seconds) == (20, 40)


def test_the_pictures_coming_from_the_read_are_generate_running() -> None:
    """At the tail of a first import Generate would read "Not started" between one file's last
    picture and the next file's read: its work is on the way and none of it queued yet."""
    read = _family("Scan", waiting=20, quick=30, slow=60).model_copy(update={"outstanding": 12})
    answer = {"scan": read, "generate": _family("Generate", waiting=40, quick=5, slow=9)}

    assert pictured_in_the_read(answer)["generate"].outstanding == 12
    done = {"scan": read, "generate": _family("Generate", waiting=0, quick=None, slow=None)}
    assert pictured_in_the_read(done)["generate"].outstanding == 0, "nothing left is nothing on"


def test_a_pass_the_scan_holds_takes_the_reads_time_and_its_own() -> None:
    held = _family("Generate", waiting=230, quick=60, slow=100).model_copy(
        update={"reason": WAITING_FOR_THE_SCAN}
    )
    answer = {"scan": _family("Scan", waiting=80, quick=120, slow=240), "generate": held}

    said = not_before_the_read(answer)

    assert (said["generate"].quick_seconds, said["generate"].slow_seconds) == (180, 340)


async def _nothing(_context: object) -> None:
    return None


class _Book:
    """A ledger pricing every file at a second, writing down what each family was priced from."""

    def __init__(self) -> None:
        self.left: dict[str, float] = {}
        self.kinds: dict[str, dict[str, float]] = {}

    async def estimate(self, family: Family, *_args: object, **named: object) -> Estimate:
        left = float(str(named["left"]))
        self.left[family.value] = left
        kinds = named["kinds"]
        assert kinds is None or isinstance(kinds, dict)
        self.kinds[family.value] = dict(kinds or {})
        return Estimate(quick_seconds=int(left), slow_seconds=int(left), items=100, at_once=1)


def _first_import() -> dict[str, KindOfWork]:
    """The admin's screen at minute 48: 23,167 files read in and lacking their work."""
    for job_type, family in (
        ("walking", Family.SCAN),
        ("thumbnail", Family.GENERATE),
        ("preview", Family.GENERATE),
        ("fingerprint_file", Family.FINGERPRINT),
        ("face_scan", Family.IDENTIFY),
        ("semantic_describe", Family.SEMANTIC),
    ):
        register_handler(job_type, _nothing, name=job_type, family=family)
    known = KindOfWork(done=0, outstanding=0, failed=0, waiting=23167, total=23167)
    return {
        "walking": KindOfWork(done=0, outstanding=1, failed=0, left_units=77129.0),
        "thumbnail": known,
        "preview": known,
        "fingerprint_file": known,
        "face_scan": known,
        "semantic_describe": known,
    }


#: 100,296 files listed, 23,167 of them read: 77,129 still to read, a tenth of them videos.
_LISTED_UNREAD = {"video": 7712.9, "image": 69416.1}


async def test_while_a_folder_is_uncounted_nothing_from_the_scan_on_shows_a_time() -> None:
    families = await _families(
        _first_import(),
        _Book(),  # type: ignore[arg-type]
        Switchboard(),
        unread=FilesToRead(by_kind=dict(_LISTED_UNREAD), uncounted=9),
    )

    for family in ("scan", "generate", "fingerprint", "identify", "semantic"):
        row = families[family]
        assert (row.quick_seconds, row.slow_seconds) == (None, None), f"{family} shows a floor"
        assert row.time_unknown == NOT_KNOWN_UNTIL_COUNTED


async def test_once_every_folder_is_counted_the_files_left_include_the_unread_ones() -> None:
    book = _Book()

    families = await _families(
        _first_import(),
        book,  # type: ignore[arg-type]
        Switchboard(),
        unread=FilesToRead(by_kind=dict(_LISTED_UNREAD), uncounted=0),
    )

    # Before: 23,167 a product. After: the 77,129 still to read as well, and a preview only of
    # the videos among them.
    assert book.left["generate"] == 23167 + 77129 + 23167 + 7713
    assert book.left["identify"] == book.left["semantic"] == 23167 + 77129
    assert book.kinds["generate"]["video"] == pytest.approx(2 * 7712.9)
    generate = families["generate"]
    assert generate.time_unknown is None
    assert generate.quick_seconds is not None and generate.quick_seconds >= 77129
    assert (generate.done, generate.total) == (0, 23167 + 77129 + 23167 + 7713)


async def test_a_product_switched_off_is_given_no_unread_files() -> None:
    work = _first_import()
    work["preview"] = KindOfWork(done=0, outstanding=0, failed=0, waiting=0, total=0)
    book = _Book()

    await _families(
        work,
        book,  # type: ignore[arg-type]
        Switchboard(),
        unread=FilesToRead(by_kind={"video": 10.0}, uncounted=0),
    )

    assert book.left["generate"] == 23167 + 10


async def test_a_pass_whose_work_the_scan_holds_says_so() -> None:
    work = _first_import()
    work["face_scan"] = KindOfWork(done=0, outstanding=40, failed=0, waiting=23167, total=23167)

    families = await _families(
        work, None, Switchboard(), unread=FilesToRead(), scan_held={"face_scan": 40}
    )

    assert families["identify"].reason == WAITING_FOR_THE_SCAN
    free = await _families(work, None, Switchboard(), unread=FilesToRead(), scan_held={})
    assert free["identify"].reason is None


def test_the_running_read_names_what_sets_its_pace() -> None:
    read = _family("Scan", waiting=20, quick=30, slow=60).model_copy(update={"outstanding": 1})
    said = "Reading is limited by the network share that holds Films."

    assert not_known_yet({"scan": read}, uncounted=0, pace=said)["scan"].pace == said
    idle = read.model_copy(update={"outstanding": 0})
    assert not_known_yet({"scan": idle}, uncounted=0, pace=said)["scan"].pace is None


def test_a_pass_with_no_row_is_left_out_while_a_folder_is_uncounted() -> None:
    read = _family("Scan", waiting=20, quick=30, slow=60)
    answer = not_known_yet({"scan": read}, uncounted=2)
    assert list(answer) == ["scan"] and answer["scan"].time_unknown == NOT_KNOWN_UNTIL_COUNTED


class _Folders:
    def __init__(self, *where: str) -> None:
        self.where = where

    async def roots(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=path.rsplit("/", 1)[-1], abs_path=path) for path in self.where]


async def test_the_share_setting_the_pace_is_named_by_its_folders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ROUTER.lanes, "installed", lambda: SimpleNamespace(readings=dict))
    monkeypatch.setattr(ROUTER._SHARE_WAITS, "busiest", lambda *_: "nas")

    def storage_for(path: Any) -> SimpleNamespace:
        return SimpleNamespace(key="nas" if path.parts[1] == "nas" else "disk")

    monkeypatch.setattr(ROUTER.lanes, "storage_for", storage_for)
    films, shows, trips = "/nas/Films", "/nas/Shows", "/nas/Trips"
    named = {
        (films,): "Films",
        (shows, "/disk/Local", films): "Films and Shows",
        (trips, films, shows): "Films, Shows and Trips",
    }
    for where, folders in named.items():
        said = await ROUTER._paced_by(_Folders(*where))
        assert said == PACED_BY_SHARE.format(folders=folders)
    assert await ROUTER._paced_by(_Folders("/disk/Local")) is None


def test_a_pass_whose_task_is_not_registered_keeps_its_own_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def task(job_type: str | None) -> Any:
        return lambda task_id: SimpleNamespace(job_type=job_type and f"{job_type}-{task_id}")

    monkeypatch.setattr(ROUTES, "get_schedule", task("run"))
    tasked = ROUTES._run_types()
    monkeypatch.setattr(ROUTES, "get_schedule", lambda _task: None)
    unregistered = ROUTES._run_types()
    monkeypatch.setattr(ROUTES, "get_schedule", task(None))
    assert ROUTES._run_types() == unregistered
    for family, task_id in ROUTER.FAMILY_TASKS.items():
        assert tasked[family] == [*unregistered[family], f"run-{task_id}"]


def _readings(**waited: float) -> dict[str, dict[str, object]]:
    return {
        key: {"remote": True, "urgent_wait_seconds": seconds, "ordinary_wait_seconds": 0.0}
        for key, seconds in waited.items()
    }


def test_a_share_is_named_only_when_its_readers_waited_most_of_the_last_minute() -> None:
    waits = ShareWaits()
    assert waits.busiest(_readings(nas=0.0, other=0.0), 0.0) is None
    assert waits.busiest(_readings(nas=10.0, other=5.0), 30.0) is None, "under a minute watched"
    assert waits.busiest(_readings(nas=40.0, other=20.0), PACE_WINDOW_SECONDS) == "nas"

    quiet = ShareWaits()
    quiet.busiest(_readings(nas=0.0), 0.0)
    assert quiet.busiest(_readings(nas=20.0), PACE_WINDOW_SECONDS) is None, "a third is not most"

    stale = ShareWaits()
    stale.busiest(_readings(nas=0.0), 0.0)
    assert stale.busiest(_readings(nas=500.0), 10 * PACE_WINDOW_SECONDS) is None
    assert stale.busiest(_readings(nas=500.0), 11 * PACE_WINDOW_SECONDS) is None, "begun again"


async def test_a_pass_says_how_many_of_its_tasks_run_now() -> None:
    states = {"thumbnail": {"running": 2}, "preview": {"running": 1, "queued": 9}}

    families = await _families(_first_import(), _Book(), Switchboard(), states=states)  # type: ignore[arg-type]

    assert (families["generate"].running, families["identify"].running) == (3, 0)


async def test_a_pass_the_benchmark_holds_says_so_in_place_of_a_time() -> None:
    states = {"face_scan": {"running": 1}, "thumbnail": {"paused": 12}}
    work = _first_import()
    work["thumbnail"] = work["face_scan"] = work["thumbnail"].model_copy(update={"outstanding": 12})

    families = await _families(
        work,
        _Book(),  # type: ignore[arg-type]
        Switchboard(),
        states=states,
        pace="Reading is limited by the network share that holds Shows.",
        benchmark=True,
    )

    held = families["generate"]
    assert (held.reason, held.time_unknown) == (PAUSED_FOR_THE_BENCHMARK, AFTER_THE_BENCHMARK)
    assert (held.for_task, held.pace) == (None, None)
    assert families["identify"].time_unknown is None, "a task still running is not held"
