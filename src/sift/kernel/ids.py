# SPDX-License-Identifier: AGPL-3.0-or-later
"""Identifiers.

Every primary key in Sift comes from here. One generator, so IDs are consistently sortable and
consistently unguessable rather than depending on which slice happened to mint them.

ULIDs: a 48-bit millisecond timestamp followed by 80 bits of randomness, in Crockford base32.

Sortable, because the timestamp leads: IDs order by creation time, so paginating a table by
primary key is a chronological scan and needs no secondary index.

Unguessable, because the random half comes from the OS CSPRNG. Sift's URLs contain asset IDs
and it is shared with people who are not trusted with everything in the library, so an ID that
could be incremented or predicted would let someone walk the collection by guessing. That is
defence in depth only (the access checks are the actual control) but it costs nothing here.
"""

from __future__ import annotations

import os
import threading
import time

# Crockford base32: no I, L, O or U, so an ID cannot be misread or turned into a word.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_TIME_CHARS = 10  # 48 bits
_RANDOM_CHARS = 16  # 80 bits
_ID_LENGTH = _TIME_CHARS + _RANDOM_CHARS

_lock = threading.Lock()
_last_ms = -1
_last_random = 0


def _encode(value: int, length: int) -> str:
    chars = [""] * length
    for i in range(length - 1, -1, -1):
        chars[i] = _ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


def new_id() -> str:
    """A new ULID.

    Two IDs minted in the same millisecond still sort in creation order: the second increments
    the first's random component rather than drawing fresh randomness. Without that, ordering
    within a millisecond is arbitrary, and a batch insert of a hundred rows comes back in an
    order that has nothing to do with the order it was written, which quietly breaks
    keyset pagination and any "most recent first" listing.

    **The clock is not allowed to go backwards here, and it does go backwards.** The timestamp
    leads the ID, so an ID minted after a clock correction sorts BELOW the one before it, and
    every "newest first" listing puts the newest row last. A few milliseconds is enough; the
    machine only has to adjust its clock while Sift is running, which is what a machine does. The
    last millisecond used is therefore a floor rather than a comparison, and a step backwards
    keeps minting IDs at the floor with the random component still incrementing, so they stay
    ordered, stay unique, and catch up on their own once real time passes the floor again.
    """
    global _last_ms, _last_random

    with _lock:
        # Never below the last one issued. See the note above.
        now_ms = max(int(time.time() * 1000), _last_ms)

        if now_ms == _last_ms:
            _last_random += 1
            # Overflowing 80 bits needs 2**80 IDs inside one millisecond, so this is
            # unreachable in practice. Step the clock rather than emit a duplicate anyway:
            # a wrong ID is worse than an ID a millisecond in the future.
            if _last_random >= 1 << 80:
                now_ms += 1
                _last_ms = now_ms
                _last_random = int.from_bytes(os.urandom(10), "big")
        else:
            _last_ms = now_ms
            _last_random = int.from_bytes(os.urandom(10), "big")

        return _encode(now_ms, _TIME_CHARS) + _encode(_last_random, _RANDOM_CHARS)


def floor_at(ms: int) -> str:
    """The lowest id that can be minted at or after this millisecond.

    For a range read by id that means "everything since then": an id leads with its millisecond,
    so every id minted from `ms` on sorts at or above this one, whatever the clock did after, since
    `new_id` never mints below the last millisecond it used.
    """
    return _encode(max(0, ms), _TIME_CHARS) + _encode(0, _RANDOM_CHARS)


def is_id(value: str) -> bool:
    """Whether a string is shaped like an ID Sift minted.

    A cheap shape check for request parameters, so an obviously invalid ID is rejected before it
    reaches a query. It says nothing about whether the row exists or whether the caller may see
    it. Only the access layer answers that.
    """
    return len(value) == _ID_LENGTH and all(c in _ALPHABET for c in value)


def timestamp_ms(value: str) -> int:
    """The creation time encoded in an ID, in milliseconds."""
    if not is_id(value):
        raise ValueError("not a valid identifier")

    result = 0
    for char in value[:_TIME_CHARS]:
        result = (result << 5) | _ALPHABET.index(char)
    return result
