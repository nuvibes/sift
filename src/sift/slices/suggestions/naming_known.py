# SPDX-License-Identifier: AGPL-3.0-or-later
"""The names a library already holds, and the near misses of them."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sift.slices.suggestions.naming_words import fold

#: How far apart two spellings may be and still be worth mentioning as possibly the same person.
NEAR_MISS_EDITS = 2


#: And both names have to be at least this long.
MIN_NEAR_MISS = 4


def _edits(one: str, other: str) -> int:
    """How many single-character changes turn one string into the other, counted up to a point."""
    if abs(len(one) - len(other)) > NEAR_MISS_EDITS:
        return NEAR_MISS_EDITS + 1
    previous = list(range(len(other) + 1))
    for row, left in enumerate(one, start=1):
        current = [row]
        for column, right in enumerate(other, start=1):
            current.append(
                min(
                    previous[column] + 1,
                    current[column - 1] + 1,
                    previous[column - 1] + (left != right),
                )
            )
        previous = current
    return previous[-1]


#: The shortest a known name may be before it is looked for INSIDE a longer text. A two-letter
#: person would match half the library, and a single short word is the shape that over-matches:
MIN_KNOWN_NAME = 4


@dataclass(frozen=True, slots=True)
class KnownName:
    """One name the library already holds, with its folded form worked out once.

    **The folding is why this type exists.**
    """

    person_id: str
    name: str
    folded: str

    #: Whether this name may be looked for INSIDE a longer text.
    @property
    def searchable(self) -> bool:
        return len(self.folded.replace(" ", "")) >= MIN_KNOWN_NAME


def fold_known(known: Iterable[tuple[str, str]]) -> tuple[KnownName, ...]:
    """Prepare `(person_id, name)` pairs for the questions below. Do this ONCE per pass."""
    return tuple(
        KnownName(person_id=person_id, name=name, folded=fold(name)) for person_id, name in known
    )


def known_people_in(text: str, known: Sequence[KnownName]) -> list[tuple[str, str]]:
    """Which of the people this library ALREADY has are named inside this text, and under which name.

    **Whole words, longest first.**
    """
    haystack = f" {fold(text)} "
    if not haystack.strip():
        return []
    hits = [one for one in known if one.searchable and f" {one.folded} " in haystack]
    hits.sort(key=lambda one: (-len(one.folded), one.folded))
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    covered: list[str] = []
    for one in hits:
        # A shorter name sitting inside one already matched is the same claim said again.
        if any(f" {one.folded} " in f" {longer} " for longer in covered):
            continue
        covered.append(one.folded)
        if one.person_id not in seen:
            seen.add(one.person_id)
            found.append((one.person_id, one.name))
    return found


def known_names_in(text: str, known: Sequence[KnownName]) -> list[str]:
    """Just the ids from `known_people_in`, for callers that only need to know who."""
    return [person_id for person_id, _ in known_people_in(text, known)]


def near_misses(name: str, existing: Iterable[tuple[str, str]]) -> list[str]:
    """People already in the library whose name is nearly, but not exactly, this one."""
    wanted = fold(name)
    if len(wanted) < MIN_NEAR_MISS:
        return []
    found = []
    for person_id, other in existing:
        folded = fold(other)
        if len(folded) < MIN_NEAR_MISS or folded == wanted:
            continue
        if _edits(wanted, folded) <= NEAR_MISS_EDITS:
            found.append(person_id)
    return found
