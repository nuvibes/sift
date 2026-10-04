# SPDX-License-Identifier: AGPL-3.0-or-later
"""A pass waiting on files still being read says no less time than the read does.

Priced from its own items alone (a thumbnail is a fifth of a second), Generate would say "under a
minute" for most of a first import and finish minutes later, because every file it counts is
behind the read.
"""

from __future__ import annotations

import pytest

from sift.slices.media_jobs.router import (
    FamilyOfWork,
    not_before_the_read,
    pictured_in_the_read,
)

pytestmark = pytest.mark.unit


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
    # A pass whose count takes in no unread file is left to its own price.
    assert (said["identify"].quick_seconds, said["identify"].slow_seconds) == (10, 20)


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
