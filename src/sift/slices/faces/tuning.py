# SPDX-License-Identifier: AGPL-3.0-or-later
"""The face numbers in one place; every threshold is measured, so move none without measuring."""

from __future__ import annotations

# Similarity is the dot product of unit embeddings; a group's middle reads on a scale of its own.
AUTO_APPLY_CONFIDENCE = 0.60

STRONG_APPLY_CONFIDENCE = 0.55

SUGGEST_CONFIDENCE = 0.45

#: The top of the setting's scale, which promises always being asked.
ALWAYS_ASK = 1.0


def bar_for(references: int, *, bar: float = AUTO_APPLY_CONFIDENCE) -> float:
    """The bar a match must clear to be named without asking, lower for a well-described person."""
    if bar >= ALWAYS_ASK or references < STRONG_REFERENCES:
        return bar
    return max(SUGGEST_CONFIDENCE, bar - (AUTO_APPLY_CONFIDENCE - STRONG_APPLY_CONFIDENCE))


MATCH_GROUPS = 1

MAX_MATCH_GROUPS = 4


#: Set to over-split: two people in one pile silently files somebody under the wrong person.
PILE_JOIN = 0.50

MIN_PILE_SIZE = 1

TRACKLET_MERGE = 0.50


TRACK_OVERLAP = 0.3

FRAMES_PER_TRACK = 2

#: One: a long appearance's frames would describe that video rather than the person.
REFERENCES_PER_APPEARANCE = 1


DETECTOR_INPUT = 640

DETECTOR_CONFIDENCE = 0.5

DETECTOR_OVERLAP = 0.4

#: The recognizer's own input; below it a face is stretched into detail nothing measured.
MIN_PIXELS = 112

MIN_PIXELS_ACCEPTED = 96

#: Bumped when how a face is judged changes, so every file is offered again.
QUALITY_VERSION = 7

LOOK_AGAIN_REACH = 0.8

LOOK_AGAIN_EACH_SIDE = 3

RUN_MEMORY_MS = 7 * 24 * 60 * 60 * 1000

#: Outside the frame the edge pixel streaks, and streaked squares pull strangers together.
MIN_CONTAINMENT = 0.95

RETRY_BORDER = 0.25

FRAME_LONG_SIDE = 1280

SOURCE_REACH = 1.5


MIN_REFERENCES = 5

STRONG_REFERENCES = 10

GOOD_REFERENCES = 20

FEWEST_REFERENCES = 3

#: Strength's bands on the share named outright: chosen, not yet measured.
RATE_FAIR = 0.50
RATE_GOOD = 0.75
RATE_STRONG = 0.90

GROUP_NAMING_REFERENCES = GOOD_REFERENCES

#: Below it a wrong name would teach the very description that made it.
LEARNING_REFERENCES = GOOD_REFERENCES

LEARNING_CONFIDENCE = AUTO_APPLY_CONFIDENCE

REFERENCE_QUALITY = 0.22

NEAR_DUPLICATE = 0.92

ODD_ONE_OUT = 0.32

ALREADY_DECIDED = 0.92

PERSON_FACES_AT_MOST = 5000

SUGGESTIONS_AT_MOST = 5000

FILED_FACES_AT_MOST = 5000

#: Small groups are most of a library; under this they are only counted.
STRANGER_FLOOR = 5

# A group is only ever asked about, never attached; its lines sit low, as a miss costs more.

GROUP_ASK = 0.35

GROUP_TICK = 0.40

GROUP_AT_LEAST = 3

GROUP_REFUSED_SHARE = 0.5

GROUPS_PER_CARD = 12

FACES_PER_GROUP = 6


COVER_SIZE = 512

COVER_MARGIN = 0.45

COVER_LONG_SIDE = 1920

CROP_QUALITY = 95
