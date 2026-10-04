# SPDX-License-Identifier: AGPL-3.0-or-later
"""The facets of number and time: ranges, stars, lengths, moments and the flags beside them."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from sift.kernel.access import (
    AllOf,
    Not,
    Where,
    clamp_rating,
)
from sift.kernel.access import (
    Node as Constraint,
)
from sift.kernel.when import day_bounds
from sift.slices.search.filter_fields import (
    _AGO,
    _AGO_UNITS,
    _DATE,
    _FALSE,
    _LENGTH,
    _LENGTH_UNITS,
    _NUMBER,
    _TRUE,
    MAX_NUMBER,
    Field,
    Unreadable,
)
from sift.slices.search.filter_parse import _presence

# --- the value parsers ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Range:
    """A closed interval, either end open. The one range grammar, used by three fields."""

    low: int | None
    high: int | None


def _range(
    value: str,
    point: Callable[[str], tuple[int, int]],
    whole: Callable[[str], tuple[int, int]] | None = None,
) -> Range:
    """Parse the grammar above, given how to read one value."""
    text = value.strip()
    if not text:
        raise Unreadable("a filter needs a value")
    standalone = whole or point

    if ".." in text:
        start, _, end = text.partition("..")
        if end.startswith("<"):
            # The top end is the first value NOT wanted, so the last one wanted is the one below
            # it, in the field's own unit, which is what `point` already answers in. One below a
            # day's FIRST second is the last second of the day before, one below 60,000 ms is
            # 59,999, one below four stars is three. The predicates stay `<=`, so nothing
            # downstream has to learn a second shape of range.
            end = end[1:].strip()
            if not start or not end:
                raise Unreadable("a range needs both ends")
            return Range(point(start)[0], point(end)[0] - 1)
        if not start or not end:
            raise Unreadable("a range needs both ends")
        return Range(point(start)[0], point(end)[1])
    if text.endswith("+"):
        return Range(point(text[:-1])[0], None)
    if text.endswith("-"):
        return Range(None, point(text[:-1])[1])

    low, high = standalone(text)
    return Range(low, high)


def _number(text: str) -> int:
    """A whole number small enough to be stored. Anything else cannot describe an asset."""
    if not _NUMBER.match(text) or len(text) > 10 or int(text) > MAX_NUMBER:
        raise Unreadable("that is not a number of a size anything could have")
    return int(text)


def _stars(value: str) -> tuple[int, int]:
    if not _NUMBER.match(value.strip()):
        raise Unreadable("a rating is a number of stars")
    # Clamped, not refused: `rating:9+` means "the top ones", and answering that with nothing
    # would be pedantry rather than accuracy.
    star = clamp_rating(int(value.strip()))
    return star, star


def _times(value: str) -> tuple[int, int]:
    """A count of presses, as an exact point on the O-counter scale."""
    count = _number(value.strip())
    return count, count


def _length(value: str) -> tuple[int, int]:
    """A duration, in milliseconds. `30s`, `5m`, `1h`, or a bare number of seconds."""
    text = value.strip().lower()
    match = _LENGTH.match(text)
    if match:
        milliseconds = _number(match.group(1)) * _LENGTH_UNITS[match.group(2)]
    elif _NUMBER.match(text):
        milliseconds = _number(text) * _LENGTH_UNITS["s"]
    else:
        raise Unreadable("a duration is a number of seconds, minutes or hours")
    return milliseconds, milliseconds


def _instant(value: str, now: int) -> tuple[int, int]:
    """A point in time, as the first and last second it covers."""
    text = value.strip().lower()

    ago = _AGO.match(text)
    if ago:
        moment = now - _number(ago.group(1)) * _AGO_UNITS[ago.group(2)]
        return moment, moment

    if not _DATE.match(text):
        raise Unreadable("a date is YYYY-MM-DD, or a span like 7d")
    try:
        day = date.fromisoformat(text)
    except ValueError as bad:
        raise Unreadable("that is not a date on the calendar") from bad

    start, end = day_bounds(day)
    return start, end - 1


def _since(value: str, now: int) -> tuple[int, int]:
    """What a value means standing alone in `added:`."""
    text = value.strip().lower()
    if _AGO.match(text):
        return _instant(text, now)[0], now
    return _instant(text, now)


#: What `viewed:` takes beyond a plain yes or no, and what each word narrows to.
_VIEWED_STATES = {
    "finished": "finished",
    "started": "started",
    "continue": "resuming",
    "continue_watching": "resuming",
}


#: The one state that is two leaves rather than one, and it is written as those two rather than as a
#: third predicate: opened, and not one to go back to. A second copy of the resume rule in SQL is
#: exactly how a facet comes to count a set that clicking it does not return, which is the fault
#: this word exists to avoid.
_VIEWED_DONE = frozenset({"done"})


def _viewed(value: str) -> Constraint:
    """What `viewed:` filters to: a state by name, or a plain yes or no."""
    # Spaces read as underscores, which is the spelling rule the language already applies to every
    # label it accepts as a token, so "continue watching" and `continue_watching` are one word
    # here. The three states with no space in them are unaffected.
    word = value.strip().lower().replace(" ", "_")
    if word in _VIEWED_DONE:
        return AllOf((Where("viewed"), Not(Where("resuming"))))
    named = _VIEWED_STATES.get(word)
    if named is not None:
        return Where(named)
    # Touched at all, or not touched at all: the wide pair, and `viewed:any` says the same thing.
    opened = Where("opened")
    return opened if _flag(value) else Not(opened)


def _flag(value: str) -> bool:
    text = value.strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise Unreadable("that is not a yes or a no")


def presence_word(found: Field, value: str) -> bool | None:
    """Whether a value is one of the words asking if a dimension is there at all (`none`, `any`),
    and which way: the same reading `_leaf` gives it, for code that has to tell such a word from a
    value naming something."""
    return _presence(found, value)
