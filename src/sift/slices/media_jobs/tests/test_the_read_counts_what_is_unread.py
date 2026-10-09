# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Scan row counts the files its walks have counted and not read yet, each once."""

from __future__ import annotations

import pytest

from sift.kernel.jobs import register_handler
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.ledger import Estimate
from sift.kernel.jobs.queue_rows import FilesToRead
from sift.kernel.jobs.switchboard import Switchboard
from sift.slices.media_jobs.activity_families import NOT_KNOWN_UNTIL_COUNTED, _families
from sift.slices.media_jobs.job_types import PROBE
from sift.slices.media_jobs.router import KindOfWork

pytestmark = pytest.mark.unit

#: The folder's files once counted, before the walk read the first of them.
COUNTED = 769


async def _nothing(_context: object) -> None:
    return None


def _read(found: int, done: int) -> dict[str, KindOfWork]:
    """The read as the library counts it: the files taken in so far, `done` of them read."""
    register_handler("walking", _nothing, name="walking", family=Family.SCAN)
    register_handler(PROBE, _nothing, name="Probing file", family=Family.SCAN, counts="files read")
    return {
        "walking": KindOfWork(done=0, outstanding=1, failed=0),
        PROBE: KindOfWork(
            done=done, outstanding=found - done, failed=0, waiting=found - done, total=found
        ),
    }


class _Paced:
    """A ledger pricing each file at what one poll's row said a file costs."""

    def __init__(self, each: tuple[float, float] | None) -> None:
        self.each = each
        self.left = 0.0
        self.kinds: dict[str, float] = {}

    async def estimate(self, family: Family, *_args: object, **named: object) -> Estimate | None:
        if family is not Family.SCAN or self.each is None:
            return None
        self.left = float(str(named["left"]))
        self.kinds = dict(named["kinds"] or {})  # type: ignore[call-overload]
        quick, slow = self.each
        return Estimate(int(self.left * quick), int(self.left * slow), 100, 1)


#: A new library's first read of one folder of 769 files: the clock, the files the row counted, how
#: many were read, and the time it said.
_POLLS = [
    ("09:09:40", 47, 0, None),
    ("09:10:20", 213, 15, None),
    ("09:10:30", 262, 19, (230, 641)),
    ("09:11:20", 518, 41, (920, 1245)),
    ("09:11:40", 639, 53, (1048, 1498)),
]


@pytest.mark.parametrize(("clock", "found", "done", "said"), _POLLS)
async def test_the_read_counts_every_file_its_walk_counted(
    clock: str, found: int, done: int, said: tuple[int, int] | None
) -> None:
    """Before, the row was the files found so far ("19 of 262") and its time priced only those."""
    each = None if said is None else (said[0] / (found - done), said[1] / (found - done))
    ledger = _Paced(each)

    row = (
        await _families(
            _read(found, done),
            ledger,  # type: ignore[arg-type]
            Switchboard(),
            unread=FilesToRead(by_kind={"video": COUNTED - found}),
        )
    )["scan"]

    assert (row.done, row.total, row.waiting) == (done, COUNTED, COUNTED - done), clock
    assert [(part.caption, part.total) for part in row.parts] == [("files read", COUNTED)]
    if said is not None:
        assert ledger.left == COUNTED - done
        assert ledger.kinds == {"video": COUNTED - found}
        assert row.quick_seconds is not None and row.quick_seconds > said[0]


async def test_once_the_walk_is_over_the_row_is_what_the_library_counts() -> None:
    work = _read(728, 63)

    row = (await _families(work, None, Switchboard(), unread=FilesToRead()))["scan"]

    assert (row.done, row.total, row.waiting) == (63, 728, 665)


async def test_a_new_librarys_first_files_are_counted_before_any_is_taken_in() -> None:
    row = (await _families(_read(0, 0), None, Switchboard(), unread=FilesToRead({"image": 30})))[
        "scan"
    ]

    assert (row.done, row.total, row.waiting) == (0, 30, 30)


async def test_a_walk_not_counted_yet_adds_nothing_and_says_so() -> None:
    row = (await _families(_read(10, 4), None, Switchboard(), unread=FilesToRead(uncounted=1)))[
        "scan"
    ]

    assert (row.done, row.total, row.time_unknown) == (4, 10, NOT_KNOWN_UNTIL_COUNTED)


async def test_with_no_walk_the_row_is_unchanged() -> None:
    work = _read(40, 10)

    with_none = (await _families(work, None, Switchboard()))["scan"]
    nothing_unread = (await _families(work, None, Switchboard(), unread=FilesToRead()))["scan"]

    assert (with_none.total, with_none.waiting) == (nothing_unread.total, nothing_unread.waiting)
    assert (nothing_unread.total, nothing_unread.waiting, nothing_unread.time_unknown) == (
        40,
        30,
        None,
    )
