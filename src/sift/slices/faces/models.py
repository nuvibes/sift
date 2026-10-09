# SPDX-License-Identifier: AGPL-3.0-or-later
"""The vocabulary of the face pass: what a face is, what happened to it, and how sure anything is.

Kept apart from the modules that produce these so that the pipeline, the matcher and the store all
name the same things. Nothing here reaches a database or a model; they are values.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - a type name only, never imported at runtime
    import numpy as np

#: How many numbers a face is described by depends on the recognizer, so it is carried alongside
#: the numbers rather than assumed anywhere.
Vector = tuple[float, ...]


class Depth(StrEnum):
    """How hard a pass looks.

    FAST runs over everything with a small budget per file. DEEP samples several times as densely:
    worth it on a file somebody cares about, expensive as a default across a library. Both read
    their plan to the end unless the time limit stops them.

    Both judge a face against the same bar. Depth decides how much of a file is examined, never
    what is accepted from it: a floor that moved with how hard somebody was looking would mean the
    same face was worth keeping or not depending on the setting it was found under, and two passes
    of one library could not then be compared at all.
    """

    FAST = "fast"
    DEEP = "deep"


class ScanStatus(StrEnum):
    """Where a file stands.

    NOT_SCANNED is the absence of a scan row rather than a value written into one, so it cannot
    disagree with reality.

    The counting is by track, never by detection. One person on screen for thirty sampled frames
    is one track and one appearance: counted the other way, that file would be one person
    matched out of thirty faces seen, and would read as partly identified for ever.
    """

    NOT_SCANNED = "not_scanned"
    NO_FACES = "no_faces"
    NONE_IDENTIFIED = "none_identified"
    SOME_IDENTIFIED = "some_identified"
    ALL_IDENTIFIED = "all_identified"


class Attribution(StrEnum):
    """How a face came to be attached to a person.

    MATCHED was decided by arithmetic, above the confidence at which Sift applies a match without
    asking. SUGGESTED is the same arithmetic below that line, waiting for somebody to agree.
    CONFIRMED is somebody having agreed, and is the only one of the three that makes the face
    eligible to become a reference.
    """

    MATCHED = "matched"
    SUGGESTED = "suggested"
    CONFIRMED = "confirmed"


class AskedBy(StrEnum):
    """Who put a question: read only beside `Attribution.SUGGESTED`. See `schema._ADD_ASKED_BY`.

    MATCH is the arithmetic, and a re-match that finds the face now clears its person's line
    recognizes it. GROUP is a group being named, which offered the rest of its faces: a re-match
    names it where it clears her line only once enough faces of her are confirmed
    (`tuning.GROUP_NAMING_REFERENCES`); a few pictures make it a guess for a person to answer.

    UNDONE is somebody taking back a name Sift added on its own (`FaceService.unmatch`). Its Undo
    says "do not decide this for me", so the face is asked instead and a re-match scores it,
    withdraws it under the line for asking, and never recognizes it on a number; otherwise the next
    re-match would put the same name back at the same score.

    BOX is a stash-box having put the person on the file whose one face this is
    (`FaceService.ask_for_the_boxes`): a decision Sift took, as a folder filed is, so the face is
    OFFERED as theirs and never named on it. Asked only about somebody Sift has no picture of; a
    re-match treats it as an UNDONE question until there are pictures of them to judge it by.
    """

    MATCH = "match"
    GROUP = "group"
    UNDONE = "undone"
    BOX = "box"


class Origin(StrEnum):
    """Where a reference face came from. Stored on every reference, with its date, since it began.

    ADDED is a FOLDER of somebody's own pictures, imported, whether the person existed at the time
    or was added later and claimed what the folder had been holding for them. The stored word
    stays `added`: renaming a value in a CHECK is a table rewrite across every library for a word
    nobody reads on screen.

    PACK is an installed face pack, and a face a pack was holding that a person later claimed.

    CONFIRMED is a face somebody named on screen (typed the name, or agreed with a suggestion),
    which is the only way a face found IN the library becomes a reference.

    SEED is a STARTER: one of a stash-box's pictures of somebody linked to it, checked the way a
    folder is. The only origin that is not evidence from this library or from somebody's choosing,
    and so the only one that is never trusted to attach a name. See `FaceService.file_starters`.
    Sift still ships no starter set of its own; these come from the boxes somebody configured.

    RECOGNIZED is a face Sift named on its own, sure enough and for somebody known well enough from
    faces people confirmed that it describes her as well as one of those does
    (`FaceService.learn_from_recognitions`). It rests on the name: taking the name back takes the
    reference with it, and it never counts toward the confirmed references that let Sift learn.
    """

    ADDED = "added"
    PACK = "pack"
    CONFIRMED = "confirmed"
    SEED = "seed"
    RECOGNIZED = "recognized"


class PileStatus(StrEnum):
    """A group of unidentified faces is either waiting to be named or deliberately set aside.

    Set aside is a status and not a deletion: it stays listed and it can be brought back.
    """

    OPEN = "open"
    IGNORED = "ignored"


class ToCheckKind(StrEnum):
    """What one item of the review list is a question about.

    The list is one list on purpose (what is left to check, in the order of how much one press
    settles), and it holds three different questions. PERSON is "do these faces look like somebody
    Sift already knows", which one press answers for every proposal standing for them. GROUP is
    "who is this", asked about a pile of faces that resemble each other and nobody yet. MISMATCH is
    the one that runs the other way: "is the name already on this file the person in it".

    Named rather than worked out from which fields are filled in. A reader that decided from the
    presence of a name would call a group whose faces carry no name a person the day a group gains
    one, and the two cards are drawn differently.
    """

    PERSON = "person"
    #: "These groups may be her": one card per person listing the unnamed GROUPS that may be them,
    #: closest first, by how close the group as a whole comes to her pictures, or because most of
    #: a folder filed as her is that group. Every other question here compares one face at a time,
    #: so a group whose faces each fall just short of the ask line would stay under Unnamed faces
    #: for ever even when the group as a whole plainly looks like her. Its `id` is the PERSON, like
    #: PERSON's; the two are told apart by this word, never by the id.
    MAY_BE = "may_be"
    GROUP = "group"
    #: And "this face is not the person whose name is already on the file", which is neither of the
    #: other two: nobody is being asked to name anybody. A pass filed somebody here (from a folder
    #: name, a filename, a stash-box), the file holds exactly one face, and that face is named as
    #: somebody else (recognized by Sift or confirmed by a person). The two questions above only
    #: run forward, from a face towards a name; this reads a name back against the face beside it.
    MISMATCH = "mismatch"


class ToCheckShow(StrEnum):
    """Which part of the review list is being asked for.

    WAITING is the list itself: the proposals, then the groups big enough to be worth a question.
    SMALL is what the floor holds back, opened from the line at the foot of that list: the same
    cards, so nothing is hidden and nothing is a different screen. IGNORED is what somebody set
    aside, which is a filter on this list rather than a tab of its own: it is the same question
    answered no, and it belongs beside the question.
    """

    WAITING = "waiting"
    SMALL = "small"
    IGNORED = "ignored"


class StartersShow(StrEnum):
    """Which People the People Sift can recognize wall shows, by what Sift knows them from.

    A person known only from STARTER pictures (a stash-box's photos, `Origin.SEED`) is one
    Sift may only ask about and never names on its own (`Gallery.starters_only`). A library
    linked to a stash-box holds hundreds of them beside the few with pictures of their own, so the
    wall sets them apart: ONLY is them alone, WITHOUT is everybody else. No value is everybody.
    """

    ONLY = "only"
    WITHOUT = "without"


class Finding(StrEnum):
    """What the reference audit can say about a candidate reference image.

    ODD_ONE_OUT is the one worth the most: an image whose numbers sit far from the rest of that
    person's is almost always a different person in the folder, and it is invisible to every other
    check here because the picture itself is perfectly good.
    """

    NO_FACE = "no_face"
    #: The picture could not be decoded at all, not a picture with nobody in it (`no_face`).
    UNREADABLE = "unreadable"
    SEVERAL_FACES = "several_faces"
    TOO_SMALL = "too_small"
    TOO_BLURRED = "too_blurred"
    #: Turned too far from the camera, not blur, which is a different measurement.
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
        """The larger of the two sides: what "how many pixels of face" means.

        **The larger one.** A face is taller than it is
        wide, so a detector's box around one is too: a good, sharp, front-on face in a web-sized
        picture measures something like 78 across and 112 down. Judged on the narrow side it is 78
        and refused; what the recognizer actually reads is the whole box warped onto a 112 square,
        so along the axis that carries the detail (brow to chin) it has every pixel it wants,
        and the other axis is stretched by a fifth.

        Read off the short side, the floor would reject faces nobody would call small: a video
        of faces a hundred pixels tall recorded as having no faces in it at all.
        """
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
    """Whether a face is worth the expensive step, and why.

    The reasons are kept alongside the verdict because they are what a screen shows somebody whose
    reference image was refused, and "rejected" on its own is not something anyone can act on.
    """

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
    """What a recognizer answers with: which way the face points, and how firmly it said so.

    The length is thrown away by every comparison (two faces are compared by direction alone),
    but it is not nothing: it separates a face from a picture that is not really a face at all far
    better than any of the four measurements taken before the description, and it is free.
    """

    vector: Vector
    strength: float


@dataclass(frozen=True, slots=True)
class Described:
    """A face that cleared the quality bar and has been turned into numbers.

    Carries the aligned square it was described from, because that picture is what gets stored:
    keeping it is what makes changing the recognition model cost minutes of re-describing rather
    than a re-read of every file in the library. It is turned into a stored image later, in one
    batch per file, rather than one at a time here.
    """

    detection: Detection
    quality: Quality
    vector: Vector
    chip: np.ndarray


@dataclass(frozen=True, slots=True)
class Appearance:
    """One face continuing across the frames a file was sampled at.

    The time range is what a screen shows and what a player seeks to. A still has a range of zero
    length, which is the honest answer rather than a special case.
    """

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
    #: Where it came from. Read by `matching.build_gallery` to know who is described by starters
    #: alone: the people Sift may only ask about. Defaulted, so a reference built without one (a
    #: test, a measurement) is an ordinary one.
    origin: Origin = Origin.ADDED
