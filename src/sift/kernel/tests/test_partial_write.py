# SPDX-License-Identifier: AGPL-3.0-or-later
"""Telling "the caller said nothing" apart from "clear it".

Tiny, and the smallness is the point: everything this module has to do is be a value that is not
None and is not any other value. What is worth asserting is that the distinction survives the two
ways it is actually used: an `isinstance` check in a writer, and the truthiness a careless writer
might reach for instead.
"""

from __future__ import annotations

import pytest

from sift.kernel.partial_write import UNCHANGED, Unchanged

pytestmark = pytest.mark.unit


def test_the_sentinel_is_not_none() -> None:
    """The whole reason it exists. None means clear; this means nothing was said."""
    assert UNCHANGED is not None


def test_a_writer_can_tell_them_apart() -> None:
    """The check every caller makes, in the shape they make it."""
    assert isinstance(UNCHANGED, Unchanged)
    assert not isinstance(None, Unchanged)
    assert not isinstance("", Unchanged)


def test_there_is_one_of_them() -> None:
    """Two instances would both pass `isinstance` and fail an identity check, which is the trap a
    module-level `object()` in each slice would have set."""
    assert Unchanged() is not UNCHANGED
    assert isinstance(Unchanged(), Unchanged)


def test_it_carries_nothing() -> None:
    """`__slots__` is empty, so nothing can be hung on it by accident and every instance is the
    same size. A sentinel that could hold state would eventually hold some."""
    with pytest.raises(AttributeError):
        UNCHANGED.anything = 1  # type: ignore[attr-defined]
