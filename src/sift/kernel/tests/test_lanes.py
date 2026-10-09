# SPDX-License-Identifier: AGPL-3.0-or-later
"""The storage lanes: a network share admits as many readers as the setting says, a local disk any.

The property that matters is the cap holding under real concurrency, so the tests here run real
coroutines through a real lane and count how many were inside together.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable, Iterator
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest

from sift.kernel import lanes as lanes_module
from sift.kernel.content.mounts import Storage
from sift.kernel.lanes import StorageLanes, reading, reading_if, storage_for
from sift.kernel.log import configure_logging

NAS = Storage(key="\\\\nas\\photos\\", remote=True)
DISK = Storage(key="C:\\", remote=False)


@pytest.fixture(autouse=True)
def _known_storages(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A path under `nas` is the share; anything else is the local disk. The operating system is
    not asked, so the tests say the same thing on every machine."""

    def storage_for(path: Path) -> Storage:
        return NAS if "nas" in path.parts else DISK

    monkeypatch.setattr(lanes_module, "storage_for", storage_for)
    lanes_module.install(None)
    yield
    lanes_module.install(None)


_inside_now = 0


async def _read(lanes: StorageLanes, path: Path, inside: list[int], hold: float = 0.02) -> None:
    """Read for a moment, noting how many readers were inside together, counted here rather than
    read off the lane, because an uncapped lane counts nobody."""
    global _inside_now
    async with lanes.reading(path):
        _inside_now += 1
        inside.append(_inside_now)
        await asyncio.sleep(hold)
        _inside_now -= 1


async def test_a_network_share_admits_only_as_many_readers_as_the_setting_says() -> None:
    lanes = StorageLanes(network_reads_at_once=2)
    inside: list[int] = []

    await asyncio.gather(*(_read(lanes, Path("/nas/a") / str(i), inside) for i in range(8)))

    assert max(inside) == 2
    lane = lanes.lane_for(Path("/nas/a/x"))
    assert lane.waits == 6 and lane.active == 0 and lane.waiting == 0


async def test_a_reader_that_asked_to_go_first_is_admitted_before_the_others() -> None:
    """On one share the scan's reads go ahead of the probes of the files it took in."""
    lanes = StorageLanes(network_reads_at_once=1)
    order: list[str] = []
    holder = asyncio.Event()

    async def hold() -> None:
        async with lanes.reading(Path("/nas/a/held")):
            await holder.wait()

    async def ordinary() -> None:
        async with lanes.reading(Path("/nas/a/probe")):
            order.append("ordinary")

    async def scan() -> None:
        async with lanes_module.first(), lanes.reading(Path("/nas/a/scan")):
            order.append("first")

    holding = asyncio.create_task(hold())
    await asyncio.sleep(0)
    waiting = asyncio.create_task(ordinary())
    await asyncio.sleep(0)
    first = asyncio.create_task(scan())
    await asyncio.sleep(0)
    assert lanes.lane_for(Path("/nas/a/x")).urgent_waiting == 1
    holder.set()
    await asyncio.gather(holding, waiting, first)

    assert order == ["first", "ordinary"]
    assert lanes.lane_for(Path("/nas/a/x")).urgent_waiting == 0


async def test_an_ordinary_reader_has_every_fourth_place_while_readers_that_go_first_wait() -> None:
    """A swap asks for places without a break for as long as it runs, and goes first. Strictly
    first, a picture being made on the same share would wait behind the whole swap; so of the
    places handed on while both kinds wait, every fourth goes to the oldest ordinary reader."""
    lanes = StorageLanes(network_reads_at_once=1)
    order: list[str] = []

    async def swap(name: str) -> None:
        async with lanes_module.first():
            for _ in range(4):
                async with lanes.reading(Path("/nas/a") / name):
                    order.append("swap")
                    await asyncio.sleep(0.005)

    async def picture(name: str) -> None:
        await asyncio.sleep(0.001)
        async with lanes.reading(Path("/nas/a") / name):
            order.append(name)

    await asyncio.gather(swap("s1"), swap("s2"), picture("p1"), picture("p2"))

    # The first place is taken immediately; of the ones handed on while pictures wait, every fourth
    # is a picture's, the older picture first, and each picture is in long before the swaps end.
    assert order.index("p1") == 4 and order.index("p2") == 8
    assert order.count("swap") == 8
    lane = lanes.lane_for(Path("/nas/a/x"))
    assert (lane.active, lane.waiting, lane.urgent_waiting) == (0, 0, 0)


async def test_a_reader_back_for_its_next_block_goes_behind_whoever_was_waiting() -> None:
    """A place let go goes to the reader that has waited longest, not to the one that let it go.

    A whole-file digest read a block at a time takes its place again for each block. Were the
    place a reader let go free for it to take back immediately, two digests would hold both places
    of a two-place share for their whole files, and every read queued behind them would wait for a
    whole file (a swap's first pieces, many seconds at a time)."""
    lanes = StorageLanes(network_reads_at_once=1)
    order: list[str] = []

    async def digest() -> None:
        for _ in range(4):
            async with lanes.reading(Path("/nas/a/whole")):
                order.append("digest")
                await asyncio.sleep(0.01)

    async def chunk(name: str, after: float) -> None:
        await asyncio.sleep(after)
        async with lanes.reading(Path("/nas/a") / name):
            order.append(name)

    await asyncio.gather(digest(), chunk("piece-1", 0.004), chunk("piece-2", 0.006))

    # Oldest first: both pieces before the digest's next block, the one that asked first first.
    assert order == ["digest", "piece-1", "piece-2", "digest", "digest", "digest"]
    lane = lanes.lane_for(Path("/nas/a/x"))
    assert (lane.active, lane.waiting) == (0, 0)


async def test_a_reader_given_up_while_waiting_hands_its_turn_on() -> None:
    """A waiter cancelled in its queue holds nothing, and the next waiter is not skipped for it."""
    lanes = StorageLanes(network_reads_at_once=1)
    order: list[str] = []
    holder = asyncio.Event()

    async def hold() -> None:
        async with lanes.reading(Path("/nas/a/held")):
            await holder.wait()

    async def read(name: str) -> None:
        async with lanes.reading(Path("/nas/a") / name):
            order.append(name)

    holding = asyncio.create_task(hold())
    await asyncio.sleep(0)
    given_up = asyncio.create_task(read("given-up"))
    after = asyncio.create_task(read("after"))
    await asyncio.sleep(0)
    given_up.cancel()
    await asyncio.sleep(0)
    holder.set()
    await asyncio.gather(holding, after)

    assert given_up.cancelled() and order == ["after"]
    lane = lanes.lane_for(Path("/nas/a/x"))
    assert (lane.active, lane.waiting, lane.urgent_waiting) == (0, 0, 0)


async def test_a_reader_given_up_before_a_place_is_handed_on_is_passed_over() -> None:
    """Given up and let go in the same moment, before the given-up reader has run: the place
    goes to the next waiter, and nothing is handed to a reader that is no longer there."""
    lanes = StorageLanes(network_reads_at_once=1)
    lane = lanes.lane_for(Path("/nas/a/x"))
    await lane.take(False)
    given_up = asyncio.create_task(lane.take(False))
    after = asyncio.create_task(lane.take(False))
    await asyncio.sleep(0)
    given_up.cancel()
    lane.give_back()
    await asyncio.wait_for(after, 1)
    assert lane.active == 1
    lane.give_back()

    await lane.take_whole()
    whole_given_up = asyncio.create_task(lane.take_whole())
    whole_after = asyncio.create_task(lane.take_whole())
    await asyncio.sleep(0)
    whole_given_up.cancel()
    lane.give_back_whole()
    await asyncio.wait_for(whole_after, 1)
    assert lane.whole == 1
    with pytest.raises(asyncio.CancelledError):
        await given_up
    with pytest.raises(asyncio.CancelledError):
        await whole_given_up


async def test_a_place_on_a_storage_nothing_caps_is_taken_immediately() -> None:
    lanes = StorageLanes(network_reads_at_once=2)
    assert await lanes.lane_for(Path("/disk/a")).take(False) is None


async def test_a_reader_given_up_as_its_place_arrives_passes_the_place_on() -> None:
    lanes = StorageLanes(network_reads_at_once=1)
    lane = lanes.lane_for(Path("/nas/a/x"))
    order: list[str] = []

    async def read(name: str) -> None:
        async with lanes.reading(Path("/nas/a") / name):
            order.append(name)

    await lane.take(False)
    given_up = asyncio.create_task(read("given-up"))
    after = asyncio.create_task(read("after"))
    await asyncio.sleep(0)
    lane.give_back()  # handed to the first waiter, which has not run yet
    given_up.cancel()
    await asyncio.gather(after)

    assert given_up.cancelled() and order == ["after"]
    assert (lane.active, lane.waiting) == (0, 0)


async def test_a_whole_file_read_leaves_the_others_a_place_on_a_share() -> None:
    """Two stream copies on a two-place share: the second waits for the first, and a read that
    lets go block by block is never behind both."""
    lanes = StorageLanes(network_reads_at_once=2)
    lanes_module.install(lanes)
    lane = lanes.lane_for(Path("/nas/a/x"))
    order: list[str] = []
    copies_go = asyncio.Event()

    async def copy(name: str) -> None:
        async with lanes_module.whole_file(Path("/nas/a") / name), reading(Path("/nas/a") / name):
            order.append(name)
            await copies_go.wait()

    async def block() -> None:
        async with reading(Path("/nas/a/block")):
            order.append("block")

    first = asyncio.create_task(copy("copy-1"))
    second = asyncio.create_task(copy("copy-2"))
    await asyncio.sleep(0.01)
    await asyncio.wait_for(block(), 1)

    assert order == ["copy-1", "block"] and lane.whole == 1
    copies_go.set()
    await asyncio.gather(first, second)
    assert order == ["copy-1", "block", "copy-2"]
    assert (lane.whole, lane.active) == (0, 0)


async def test_a_whole_file_read_on_a_one_place_share_or_a_disk_still_reads() -> None:
    """Never none: a one-place share lets one whole-file read in, a disk any number, and with no
    lanes installed it costs nothing."""
    async with lanes_module.whole_file(Path("/nas/a/x")):
        pass
    lanes = StorageLanes(network_reads_at_once=1)
    lanes_module.install(lanes)
    share = lanes.lane_for(Path("/nas/a/x"))
    assert share.whole_limit == 1
    async with lanes_module.whole_file(Path("/nas/a/x")):
        assert share.whole == 1
    async with (
        lanes_module.whole_file(Path("/disk/a")),
        lanes_module.whole_file(Path("/disk/b")),
    ):
        assert lanes.lane_for(Path("/disk/a")).whole == 2


async def test_a_whole_file_read_waiting_is_let_in_by_a_wider_setting_or_gives_up_cleanly() -> None:
    lanes = StorageLanes(network_reads_at_once=2)
    lanes_module.install(lanes)
    lane = lanes.lane_for(Path("/nas/a/x"))
    await lane.take_whole()

    given_up = asyncio.create_task(lane.take_whole())
    await asyncio.sleep(0)
    given_up.cancel()
    await asyncio.sleep(0)
    assert given_up.cancelled() and not lane._whole_waiters

    handed = asyncio.create_task(lane.take_whole())
    waiting = asyncio.create_task(lane.take_whole())
    await asyncio.sleep(0)
    lane.give_back_whole()  # handed to the first, which is given up before it runs
    handed.cancel()
    await asyncio.wait_for(waiting, 1)
    assert handed.cancelled() and lane.whole == 1

    later = asyncio.create_task(lane.take_whole())
    await asyncio.sleep(0)
    assert not later.done()
    await lanes.configure(network_reads_at_once=3)
    await asyncio.wait_for(later, 1)
    assert lane.whole == 2


def test_how_many_to_read_at_a_time_follows_the_storage() -> None:
    lanes = StorageLanes(network_reads_at_once=2)
    lanes_module.install(lanes)
    try:
        assert lanes_module.reads_at_once(Path("/nas/a/x")) == 2
        assert lanes_module.reads_at_once(Path("/disk/x")) == lanes_module.LOCAL_READS_AT_A_TIME
    finally:
        lanes_module.install(None)
    assert lanes_module.reads_at_once(Path("/disk/x")) == 1


async def test_a_local_disk_has_no_cap() -> None:
    lanes = StorageLanes(network_reads_at_once=2)
    inside: list[int] = []

    await asyncio.gather(*(_read(lanes, Path("/disk") / str(i), inside) for i in range(8)))

    assert max(inside) == 8
    assert lanes.lane_for(Path("/disk/x")).waits == 0


async def test_two_shares_are_two_lanes() -> None:
    """The cap is per storage, so a second server is not held back by the first."""
    other = Storage(key="\\\\other\\share\\", remote=True)
    lanes = StorageLanes(network_reads_at_once=1)
    lanes_module.storage_for = lambda path: other if "other" in path.parts else NAS
    inside: list[int] = []

    await asyncio.gather(
        _read(lanes, Path("/nas/a"), inside),
        _read(lanes, Path("/other/b"), inside),
    )

    assert lanes.lane_for(Path("/nas/a")).waits == 0
    assert lanes.lane_for(Path("/other/b")).waits == 0


async def test_raising_the_setting_wakes_a_waiting_reader() -> None:
    lanes = StorageLanes(network_reads_at_once=1)
    inside: list[int] = []
    readers = [
        asyncio.create_task(_read(lanes, Path("/nas") / str(i), inside, hold=0.2)) for i in range(3)
    ]
    await asyncio.sleep(0.05)
    assert lanes.lane_for(Path("/nas/x")).waiting == 2

    assert await lanes.configure(network_reads_at_once=3) is True
    await asyncio.sleep(0.05)

    assert lanes.lane_for(Path("/nas/x")).waiting == 0
    await asyncio.gather(*readers)
    assert max(inside) == 3


async def test_the_same_setting_again_changes_nothing() -> None:
    lanes = StorageLanes(network_reads_at_once=2)
    assert await lanes.configure(network_reads_at_once=2) is False


async def test_the_setting_is_held_inside_its_bounds() -> None:
    lanes = StorageLanes(network_reads_at_once=2)
    await lanes.configure(network_reads_at_once=10_000)
    assert lanes.network_reads_at_once == lanes_module.MAX_READS_AT_ONCE
    await lanes.configure(network_reads_at_once=-4)
    assert lanes.network_reads_at_once == 0
    assert lanes.lane_for(Path("/nas/x")).limit == lanes_module.NETWORK_READS_AT_A_TIME


async def test_a_reader_that_raises_gives_its_place_back() -> None:
    lanes = StorageLanes(network_reads_at_once=1)
    with pytest.raises(RuntimeError):
        async with lanes.reading(Path("/nas/a")):
            raise RuntimeError("the read failed")
    assert lanes.lane_for(Path("/nas/a")).active == 0
    inside: list[int] = []
    await _read(lanes, Path("/nas/b"), inside)
    assert inside == [1]


async def test_the_readings_say_what_a_share_has_been_waiting() -> None:
    lanes = StorageLanes(network_reads_at_once=1)
    inside: list[int] = []
    await asyncio.gather(
        *(_read(lanes, Path("/nas") / str(i), inside, hold=0.05) for i in range(3))
    )

    readings = lanes.readings()
    assert readings[NAS.key]["limit"] == 1
    assert readings[NAS.key]["waits"] == 2
    worst = readings[NAS.key]["worst_wait_seconds"]
    assert isinstance(worst, float) and worst > 0
    assert round(lanes.worst_wait_seconds, 3) == worst


async def test_the_readings_split_the_wait_between_readers_that_went_first_and_the_rest() -> None:
    lanes = StorageLanes(network_reads_at_once=1)
    held = asyncio.Event()

    async def hold() -> None:
        async with lanes.reading(Path("/nas/held")):
            await held.wait()

    async def scan() -> None:
        async with lanes_module.first(), lanes.reading(Path("/nas/scan")):
            await asyncio.sleep(0.05)

    holder = asyncio.create_task(hold())
    await asyncio.sleep(0)
    waiting = [asyncio.create_task(scan()), asyncio.create_task(_read(lanes, Path("/nas/p"), []))]
    await asyncio.sleep(0.1)
    held.set()
    await asyncio.gather(holder, *waiting)

    lane = lanes.readings()[NAS.key]
    urgent, ordinary = lane["urgent_wait_seconds"], lane["ordinary_wait_seconds"]
    assert isinstance(urgent, float) and isinstance(ordinary, float)
    assert 0.05 <= urgent < ordinary, "the ordinary read also waited out the scan's"


async def test_with_nothing_installed_every_read_goes_straight_through() -> None:
    """A console tool, a migration and most tests install nothing; a read then costs nothing."""
    async with reading(Path("/nas/a")):
        pass


async def test_the_installed_lanes_are_the_ones_every_read_goes_through() -> None:
    lanes = StorageLanes(network_reads_at_once=1)
    lanes_module.install(lanes)
    inside: list[int] = []

    async def read(i: int) -> None:
        async with reading(Path("/nas") / str(i)):
            inside.append(lanes.lane_for(Path("/nas/x")).active)
            await asyncio.sleep(0.02)

    await asyncio.gather(*(read(i) for i in range(4)))
    assert max(inside) == 1


async def test_the_setting_leaves_a_local_disk_alone() -> None:
    """Only a share is capped; a local lane that already exists is not handed the share's limit."""
    lanes = StorageLanes(network_reads_at_once=2)
    local = lanes.lane_for(Path("/disk/a"))
    share = lanes.lane_for(Path("/nas/a"))

    assert await lanes.configure(network_reads_at_once=3) is True

    assert share.limit == 3
    assert not local.capped
    assert local.limit != 3


async def test_a_wait_worth_reporting_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same quarter second the loop and the pools warn at; here every wait is worth it."""
    configure_logging("INFO")
    monkeypatch.setattr(lanes_module, "WAIT_WARN_SECONDS", 0.0)
    lanes = StorageLanes(network_reads_at_once=1)
    inside: list[int] = []

    await asyncio.gather(*(_read(lanes, Path("/nas") / str(i), inside) for i in range(2)))

    records = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    waited = [one for one in records if one["event"] == "lanes.waited"]
    assert len(waited) == 1
    assert waited[0]["storage"].endswith("photos\\"), "the share, as the record redacts it"
    assert waited[0]["seconds"] >= 0


def test_which_storage_a_file_is_on_is_asked_of_its_folder_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real question, under the fixture that replaces it for every other test here. Asked per
    read, so it is remembered per folder rather than put to the operating system per read."""
    asked: list[Path] = []

    def storage_of(directory: Path) -> Storage:
        asked.append(directory)
        return NAS

    monkeypatch.setattr(lanes_module, "storage_of", storage_of)
    lanes_module._storage_of_directory.cache_clear()
    folder = Path("/somewhere/shared")

    assert storage_for(folder / "a.jpg") is NAS
    assert storage_for(folder / "b.jpg") is NAS

    assert asked == [folder]
    lanes_module._storage_of_directory.cache_clear()


def test_what_is_installed_can_be_read_back() -> None:
    assert lanes_module.installed() is None
    lanes = StorageLanes(network_reads_at_once=1)
    lanes_module.install(lanes)
    assert lanes_module.installed() is lanes


async def test_a_reader_that_may_not_be_opening_a_file_at_all() -> None:
    """A caller with no path holds no place; one with a path holds the same place `reading` would."""
    lanes = StorageLanes(network_reads_at_once=1)
    lanes_module.install(lanes)
    lane = lanes.lane_for(Path("/nas/x"))

    async with reading_if(None):
        assert lane.active == 0
    async with reading_if(Path("/nas/x")):
        assert lane.active == 1
    assert lane.active == 0


async def test_each_share_reads_its_own_measured_number_unless_one_is_set_for_all() -> None:
    other = Storage(key="\\\\other\\share\\", remote=True)
    lanes_module.storage_for = lambda path: (
        other if "other" in path.parts else NAS if "nas" in path.parts else DISK
    )
    lanes = StorageLanes()
    nas, unmeasured = lanes.lane_for(Path("/nas/a")), lanes.lane_for(Path("/other/a"))
    assert (nas.limit, unmeasured.limit) == (2, 2), "two until a share is measured"

    assert await lanes.configure(network_reads_at_once=0, measured={NAS.key: 6, DISK.key: 16})
    assert (nas.limit, unmeasured.limit) == (6, lanes_module.NETWORK_READS_AT_A_TIME)
    assert not lanes.lane_for(Path("/disk/a")).capped, "a measured disk is still not capped"
    assert (
        await lanes.configure(network_reads_at_once=0, measured={NAS.key: 6, DISK.key: 16}) is False
    )

    assert await lanes.configure(network_reads_at_once=3)
    assert (nas.limit, unmeasured.limit) == (3, 3), "a number set overrides every share"
    assert await lanes.configure(network_reads_at_once=0)
    assert nas.limit == 6, "the measured numbers are kept when only the setting moves"


def test_a_reader_keeps_its_storages_measured_number_of_files_open() -> None:
    lanes = StorageLanes()
    lanes_module.install(lanes)
    try:
        assert lanes_module.reads_at_once(Path("/disk/x")) == lanes_module.LOCAL_READS_AT_A_TIME
        asyncio.run(lanes.configure(network_reads_at_once=0, measured={DISK.key: 16, NAS.key: 5}))
        assert lanes_module.reads_at_once(Path("/disk/x")) == 16
        assert lanes_module.reads_at_once(Path("/nas/x")) == 5
    finally:
        lanes_module.install(None)


async def test_a_files_read_goes_after_readers_that_go_first_and_before_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lanes_module, "READS_FIRST", True)
    lanes = StorageLanes(network_reads_at_once=1)
    order: list[str] = []
    holder = asyncio.Event()

    async def hold() -> None:
        async with lanes.reading(Path("/nas/a/held")):
            await holder.wait()

    async def read(name: str, kind: Callable[[], AbstractAsyncContextManager[None]]) -> None:
        async with kind(), lanes.reading(Path("/nas/a") / name):
            order.append(name)

    holding = asyncio.create_task(hold())
    await asyncio.sleep(0)
    waiting = [
        asyncio.create_task(read(name, kind))
        for name, kind in (
            ("ordinary", contextlib.nullcontext),
            ("read", lanes_module.the_read),
            ("first", lanes_module.first),
        )
    ]
    await asyncio.sleep(0)
    assert lanes.lane_for(Path("/nas/a/x")).urgent_waiting == 2
    holder.set()
    await asyncio.gather(holding, *waiting)

    assert order == ["first", "read", "ordinary"]


async def test_every_fourth_place_goes_to_the_kinds_below_by_the_same_rule() -> None:
    lanes = StorageLanes(network_reads_at_once=1)
    lane = lanes.lane_for(Path("/nas/a/x"))
    await lane.take(lanes_module.ORDINARY)
    order: list[str] = []

    async def wait(kind: int, name: str) -> None:
        await lane.take(kind)
        order.append(name)

    names = {lanes_module.FIRST: "F", lanes_module.READ: "R", lanes_module.ORDINARY: "O"}
    waiting = [
        asyncio.create_task(wait(kind, name)) for kind, name in names.items() for _ in range(13)
    ]
    await asyncio.sleep(0)
    for _ in range(16):
        lane.give_back()
        await asyncio.sleep(0)

    assert "".join(order) == "FFFRFFFRFFFRFFFO"
    for task in waiting:
        task.cancel()
    await asyncio.gather(*waiting, return_exceptions=True)


async def test_with_reads_first_off_a_files_read_waits_in_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lanes_module, "READS_FIRST", False)
    async with lanes_module.the_read():
        assert lanes_module._RANK.get() == lanes_module.ORDINARY


async def test_a_files_read_inside_a_reader_that_goes_first_still_goes_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lanes_module, "READS_FIRST", True)
    async with lanes_module.first(), lanes_module.the_read():
        assert lanes_module._RANK.get() == lanes_module.FIRST
    async with lanes_module.the_read():
        assert lanes_module._RANK.get() == lanes_module.READ
    assert lanes_module._RANK.get() == lanes_module.ORDINARY


async def test_the_shares_where_files_were_read_first_lately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lanes_module, "READS_FIRST", True)
    lanes = StorageLanes(network_reads_at_once=2)
    async with lanes.reading(Path("/nas/ordinary")), lanes.reading(Path("/disk/a")):
        pass
    assert lanes.reading_first(60) == set()

    async with lanes_module.the_read(), lanes.reading(Path("/nas/read")):
        pass
    assert lanes.reading_first(60) == {NAS.key}

    lane = lanes.lane_for(Path("/nas/x"))
    assert lane.read_at is not None
    lane.read_at -= 61
    assert lanes.reading_first(60) == set()


async def test_a_wait_for_a_place_is_filed_to_the_job_that_waited() -> None:
    """On a share the wait for a place is most of a read's cost, and no stage can see it."""
    from sift.kernel.log import JobCost, costing

    lanes = StorageLanes(network_reads_at_once=1)
    lanes_module.install(lanes)
    first_in = asyncio.Event()

    async def hold() -> None:
        async with lanes.reading(Path("/nas/a/1")):
            first_in.set()
            await asyncio.sleep(0.2)

    cost = JobCost()
    holder = asyncio.create_task(hold())
    await first_in.wait()
    with costing(cost):
        async with lanes.reading(Path("/nas/a/2")):
            pass
        async with lanes_module.whole_file(Path("/nas/a/3")):
            pass
    await holder

    assert cost.summary()["storage_wait_ms"] >= 150
