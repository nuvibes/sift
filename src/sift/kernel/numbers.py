# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a number out of text that came from somewhere untrusted.

The obvious guard is wrong: `str.isdigit()` is true for characters `int()` and `float()` refuse
(the superscript digits), so the refusal arrives as an unhandled exception after a check that
looked like validation. Request values and a remote `Retry-After` reach these conversions, so the
crash would be reachable by a caller or a remote host. So this simply attempts the conversion,
the only authority on its own answer; `None` means "not a number", which every caller answers with
a 404, a default or an ignored header.
"""

from __future__ import annotations


def as_int(raw: str | None) -> int | None:
    """The integer this text spells, or None if it does not spell one.

    Never raises. Whitespace, a sign, or a Unicode digit `int()` accepts is converted: `int()` is
    the authority, with no narrower opinion on top.
    """
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def as_float(raw: str | None) -> float | None:
    """The number this text spells, or None if it does not spell one.

    The rule of `as_int`, for a duration. Infinities and NaN parse but are refused: as a wait in
    seconds they would never end.
    """
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    # `x != x` is true only for NaN, and the comparison against infinity catches both signs.
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


__all__ = ["as_float", "as_int"]
