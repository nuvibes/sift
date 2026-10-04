# SPDX-License-Identifier: AGPL-3.0-or-later
"""Long bytes in a failed comparison are told by their length and a short digest.

A test that compares received files with the ones sent prints both sides in full when it fails,
and a file of a few megabytes becomes millions of lines of escaped bytes in a run's log: nothing a
person can read, and text the run-log privacy scan has to read as if it could hold names. What the
reader needs is whether the two sides are the same bytes, which a digest says in sixteen letters.
"""

from __future__ import annotations

from pprint import pformat
from typing import Any

from blake3 import blake3

#: Bytes up to this long are shown as they are; anything longer is told by length and digest.
SHOWN_WHOLE = 64


class _Told:
    """Stands in for long bytes when a comparison is printed."""

    def __init__(self, data: bytes | bytearray) -> None:
        self.text = f"<{len(data)} bytes, blake3 {blake3(bytes(data)).hexdigest()[:16]}>"

    def __repr__(self) -> str:
        return self.text


class _Absent:
    def __repr__(self) -> str:
        return "(no such key)"


_ABSENT = _Absent()


def _long(value: Any) -> bool:
    if isinstance(value, bytes | bytearray):
        return len(value) > SHOWN_WHOLE
    if isinstance(value, dict):
        return any(_long(one) for one in value.values())
    if isinstance(value, list | tuple | set | frozenset):
        return any(_long(one) for one in value)
    return False


def told(value: Any) -> Any:
    """`value` with every long run of bytes in it, at any depth, told by its length and digest."""
    if isinstance(value, bytes | bytearray):
        return _Told(value) if len(value) > SHOWN_WHOLE else value
    if isinstance(value, dict):
        return {key: told(one) for key, one in value.items()}
    if type(value) in (list, tuple):
        return type(value)(told(one) for one in value)
    if isinstance(value, set | frozenset) and _long(value):
        return sorted((told(one) for one in value), key=repr)
    return value


def pytest_assertrepr_compare(op: str, left: Any, right: Any) -> list[str] | None:
    """The explanation of a failed `==` that holds long bytes; pytest's own for everything else."""
    if op != "==" or not (_long(left) or _long(right)):
        return None
    shown_left, shown_right = told(left), told(right)
    lines = [f"{shown_left!r} == {shown_right!r}"]
    if isinstance(shown_left, dict) and isinstance(shown_right, dict):
        for key in sorted({*shown_left, *shown_right}, key=repr):
            mine = shown_left.get(key, _ABSENT)
            theirs = shown_right.get(key, _ABSENT)
            if repr(mine) != repr(theirs):
                lines.append(f"{key!r}: {mine!r} != {theirs!r}")
        return lines
    left_lines = pformat(shown_left).splitlines()
    right_lines = pformat(shown_right).splitlines()
    return [*lines, "Left:", *left_lines, "Right:", *right_lines]
