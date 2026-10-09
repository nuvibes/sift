# SPDX-License-Identifier: AGPL-3.0-or-later
"""Identifiers: ULIDs, sortable by creation and unguessable from the OS random source."""

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
    """A new ULID, kept in order within a millisecond and when the clock steps back."""
    global _last_ms, _last_random

    with _lock:
        # A floor, never a comparison: clocks do go backwards.
        now_ms = max(int(time.time() * 1000), _last_ms)

        if now_ms == _last_ms:
            _last_random += 1
            # Unreachable in practice; step the clock rather than emit a duplicate.
            if _last_random >= 1 << 80:
                now_ms += 1
                _last_ms = now_ms
                _last_random = int.from_bytes(os.urandom(10), "big")
        else:
            _last_ms = now_ms
            _last_random = int.from_bytes(os.urandom(10), "big")

        return _encode(now_ms, _TIME_CHARS) + _encode(_last_random, _RANDOM_CHARS)


def floor_at(ms: int) -> str:
    """The lowest id that can be minted at or after this millisecond."""
    return _encode(max(0, ms), _TIME_CHARS) + _encode(0, _RANDOM_CHARS)


def is_id(value: str) -> bool:
    """Whether a string is shaped like an ID Sift minted; it says nothing of access."""
    return len(value) == _ID_LENGTH and all(c in _ALPHABET for c in value)


def timestamp_ms(value: str) -> int:
    """The creation time encoded in an ID, in milliseconds."""
    if not is_id(value):
        raise ValueError("not a valid identifier")

    result = 0
    for char in value[:_TIME_CHARS]:
        result = (result << 5) | _ALPHABET.index(char)
    return result
