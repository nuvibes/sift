# SPDX-License-Identifier: AGPL-3.0-or-later
"""The vocabulary of the face pass: what a face is, what happened to it, how sure anything is."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - a type name only, never imported at runtime
    import numpy as np

#: Its length depends on the recognizer, so it is carried with the numbers, never assumed.
Vector = tuple[float, ...]


class Depth(StrEnum):
    """How hard a pass looks: how much of a file is examined, never what is accepted from it."""

    FAST = "fast"
    DEEP = "deep"


class ScanStatus(StrEnum):
    """Where a file stands, counted by track, never by detection; NOT_SCANNED is no row at all."""

    NOT_SCANNED = "not_scanned"
    NO_FACES = "no_faces"
    NONE_IDENTIFIED = "none_identified"
    SOME_IDENTIFIED = "some_identified"
    ALL_IDENTIFIED = "all_identified"


class Attribution(StrEnum):
    """How a face came to be attached to a person; only CONFIRMED may become a reference."""

    MATCHED = "matched"
    SUGGESTED = "suggested"
    CONFIRMED = "confirmed"


class AskedBy(StrEnum):
    """Who put a question, read only beside `Attribution.SUGGESTED` (`schema._ADD_ASKED_BY`).

    MATCH is arithmetic; GROUP a named group's offer; UNDONE a name taken back, never named again on
    a number; BOX a stash-box's filing, asked only.
    """

    MATCH = "match"
    GROUP = "group"
    UNDONE = "undone"
    BOX = "box"


class Origin(StrEnum):
    """Where a reference face came from: a folder (stored as `added`), a pack, a confirmation, a
    stash-box starter (SEED, never trusted to name) or Sift's own recognition."""

    ADDED = "added"
    PACK = "pack"
    CONFIRMED = "confirmed"
    SEED = "seed"
    RECOGNIZED = "recognized"


class PileStatus(StrEnum):
    """A group of unidentified faces, waiting to be named or set aside (still listed)."""

    OPEN = "open"
    IGNORED = "ignored"


class ToCheckKind(StrEnum):
    """What one item of the review list is a question about; named, never inferred from fields."""

    PERSON = "person"
    #: "These groups may be her": one card per person; its `id` is the person, as PERSON's.
    MAY_BE = "may_be"
    GROUP = "group"
    #: A face that is not the person whose name is already on the file.
    MISMATCH = "mismatch"


class ToCheckShow(StrEnum):
    """Which part of the review list is asked for: the list, the small groups, or set aside."""

    WAITING = "waiting"
    SMALL = "small"
    IGNORED = "ignored"


class StartersShow(StrEnum):
    """Which People the recognize wall shows: those known by starters alone, or everybody else."""

    ONLY = "only"
    WITHOUT = "without"


class Finding(StrEnum):
    """What the reference audit can say about a candidate reference image."""

    NO_FACE = "no_face"
    UNREADABLE = "unreadable"
    SEVERAL_FACES = "several_faces"
    TOO_SMALL = "too_small"
    TOO_BLURRED = "too_blurred"
    TURNED_AWAY = "turned_away"
    RUNS_OFF_EDGE = "runs_off_edge"
    NEAR_DUPLICATE = "near_duplicate"
    BELOW_MINIMUM = "below_minimum"
    ODD_ONE_OUT = "odd_one_out"


@dataclass(frozen=True, slots=True)
class Box:
    """A face's place in a frame, in that frame's pixels."""

    x: int
    y: int
    width: int
    height: int

    @property
    def short_side(self) -> int:
        """The smaller of the two sides."""
        return min(self.width, self.height)

    @property
    def long_side(self) -> int:
        """The larger of the two sides: a face's box is taller than wide, and the recognizer
        reads the whole box, so the long side is how many pixels of face there are."""
        return max(self.width, self.height)

    @property
    def area(self) -> int:
        return self.width * self.height

    def overlap(self, other: Box) -> float:
        """How much two boxes share, as a fraction of what they cover between them."""
        left = max(self.x, other.x)
        top = max(self.y, other.y)
        right = min(self.x + self.width, other.x + other.width)
        bottom = min(self.y + self.height, other.y + other.height)
        if right <= left or bottom <= top:
            return 0.0
        intersection = (right - left) * (bottom - top)
        union = self.area + other.area - intersection
        return intersection / union if union > 0 else 0.0

    def centre(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2


@dataclass(frozen=True, slots=True)
class Quality:
    """Whether a face is worth the expensive step, with the reason a screen can show."""

    pixels: int
    sharpness: float
    frontality: float
    score: float
    accepted: bool
    reason: str | None = None
    containment: float = 1.0
    """How much of the aligned square came from inside the frame rather than from its edge pixel
    repeated. Last and defaulted because a face measured without being warped has nothing to
    report, not because it matters least."""
    strength: float = 0.0
    """How long the recognizer's answer was before it was scaled to unit length.

    The one measurement here that is taken AFTER the expensive step rather than to decide whether
    to take it, which is why it cannot be a floor: by the time it exists the description has
    already been paid for. Kept raw, and 0.0 means nobody measured it (a face described without
    this measurement), not a face that scored nothing.

    Raw rather than as the 0-to-1 term the score uses: a number nothing records has no
    distribution to set a threshold from. Storing it is what makes the next choice evidence-led instead of another guess."""
    agreement: float = 1.0
    """How much this face looks like the OTHER frames of the same appearance.

    1.0 where there are no others to disagree with, which is most faces: three quarters of
    appearances are a single frame, and a still can never be more."""
    failed: Finding | None = None
    """Which measurement refused the face, decided by the same check that wrote `reason`, so the
    code a refusal is logged and counted under and the sentence beside it cannot disagree."""


@dataclass(frozen=True, slots=True)
class Detection:
    """One face, in one frame, before anything expensive has been done to it."""

    box: Box
    score: float
    landmarks: tuple[tuple[float, float], ...]
    timestamp_ms: int


@dataclass(frozen=True, slots=True)
class Description:
    """What a recognizer answers with: the face's direction, and how firmly it said so."""

    vector: Vector
    strength: float


@dataclass(frozen=True, slots=True)
class Described:
    """A face that cleared the quality bar, its numbers and the square they came from, kept."""

    detection: Detection
    quality: Quality
    vector: Vector
    chip: np.ndarray


@dataclass(frozen=True, slots=True)
class Appearance:
    """One face continuing across the sampled frames; a still has a range of zero."""

    started_ms: int
    ended_ms: int
    seen_in: int
    quality: float
    faces: tuple[Described, ...]


@dataclass(frozen=True, slots=True)
class Match:
    """A person the numbers point at, and how strongly."""

    person_id: str
    confidence: float


@dataclass(frozen=True, slots=True)
class Reference:
    """One face a person is recognized by."""

    id: str
    person_id: str
    vector: Vector
    quality: float
    crop_digest: str
    #: Where it came from: a person described by starters alone is only asked about.
    origin: Origin = Origin.ADDED
