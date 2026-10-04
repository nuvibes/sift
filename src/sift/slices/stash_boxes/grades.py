# SPDX-License-Identifier: AGPL-3.0-or-later
"""How sure Sift is that a box's answer is this file, and the kept shape of that answer."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from sift.kernel.records import FoundRecord
from sift.slices.stash_boxes.adapter import (
    EXACT as EXACT,
)
from sift.slices.stash_boxes.adapter import (
    LONE,
)


class Grade(StrEnum):
    """How sure Sift is that a match is this file, which is never how sure the stash-box is."""

    CERTAIN = "certain"
    LIKELY = "likely"
    UNSURE = "unsure"


#: Below this length a perceptual hash is not evidence enough to act on alone.
#:
#: A video's perceptual hash is a grid of twenty-five stills spread across it. On a clip of a few
#: seconds those stills are nearly one picture, so unrelated short clips land a few bits apart,
#: and a length check cannot tell them apart either: a few seconds is most of the clip.
SHORT_MS = 60_000

#: How closely two lengths must agree for an answer kept without its proof to stay certain: the
#: figure Stash's own tagger calls a duration match. Held under the tolerance somebody set.
TIGHT_MS = 5_000


def grade_of(record: FoundRecord, *, length_ms: int | None, tolerance_ms: int) -> Grade:
    """What one answer is worth, given how long the file actually is."""
    if record.confidence >= EXACT:
        return Grade.CERTAIN
    theirs = record.fields.get("duration_ms")
    if length_ms is None or not isinstance(theirs, (int, float)) or theirs <= 0:
        return Grade.UNSURE
    apart = abs(int(theirs) - length_ms)
    if apart > tolerance_ms or length_ms < SHORT_MS:
        return Grade.UNSURE
    return Grade.LIKELY


def grade_unproven(record: FoundRecord, *, length_ms: int | None, tolerance_ms: int) -> Grade:
    """What a match kept before the scene's fingerprints were read is worth.

    Such a record says `EXACT` whenever an exact hash was sent, so only the length can show it: on
    a file long enough (`SHORT_MS`) a length within `TIGHT_MS` keeps it certain.
    """
    theirs = record.fields.get("duration_ms")
    if (
        record.confidence >= EXACT
        and length_ms is not None
        and length_ms >= SHORT_MS
        and isinstance(theirs, (int, float))
        and theirs > 0
        and abs(int(theirs) - length_ms) <= min(tolerance_ms, TIGHT_MS)
    ):
        return Grade.CERTAIN
    return grade_of(
        replace(record, confidence=min(record.confidence, LONE)),
        length_ms=length_ms,
        tolerance_ms=tolerance_ms,
    )


@dataclass(frozen=True, slots=True)
class Match:
    """One stash-box's answer about one file, as Sift kept it."""

    asset_id: str
    box_id: str
    box_name: str
    remote_id: str
    record: FoundRecord
    grade: Grade
    state: str
    found_at: int
    #: When it was agreed to or refused; None while it waits.
    decided_at: int | None = None
