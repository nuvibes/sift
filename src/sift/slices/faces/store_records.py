# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shapes the face store hands back, and the statements and rules its parts share."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from sift.kernel.db import Row
from sift.kernel.sampling import FACE_SAMPLING_VERSION
from sift.slices.faces import recognize
from sift.slices.faces.models import (
    AskedBy,
    Attribution,
    Box,
    Origin,
    Reference,
    ScanStatus,
    Vector,
)
from sift.slices.faces.tuning import QUALITY_VERSION

#: STARTERS ARE RETIRED: every starter picture (`Origin.SEED`) of one person still in use is marked
#: retired, in the transaction of the write that makes it true, so both kinds never describe her
#: at the same time. Retired rather than deleted: the row stops the same picture being filed again.
_RETIRE_STARTERS = (
    "UPDATE face_references SET retired_at = ? "
    "WHERE person_id = ? AND origin = 'seed' AND retired_at IS NULL"
)


#: The word the enrichment pass writes onto a pairing a stash-box decided, spelt out because the
#: pass lives in another slice and a slice may not import one.
SOURCE_STASH_BOX = "stash_box"

#: The key faces are registered under as a product of the Build (`work_runs.made_for`).
PRODUCT = "faces"


#: Which answers about a face put its person on the file: confirmed by you, or recognized by Sift.
#: A question never does. The one rule every reader here that lists a file's People from its faces
#: binds. The kernel spells it twice as literals (`kernel/access/constraints.py`,
#: `kernel/access/repository/assets.py`); `test_names_the_file.py` holds both to this.
NAMES_THE_FILE: tuple[Attribution, ...] = (Attribution.CONFIRMED, Attribution.MATCHED)


#: The same rule, as the values a statement binds.
_NAMES_THE_FILE = tuple(one.value for one in NAMES_THE_FILE)


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True, slots=True)
class StoredTrack:
    """One appearance as it comes back out of the database."""

    id: str
    asset_id: str
    started_ms: int
    ended_ms: int
    seen_in: int
    quality: float
    person_id: str | None
    confidence: float | None
    attribution: Attribution | None
    pile_id: str | None


@dataclass(frozen=True, slots=True)
class Standing:
    """One appearance as a rescan is about to replace it: who it carried, how, and its clearest
    description, so `FaceService.scan` knows a face found again IS the one it was."""

    track_id: str
    person_id: str | None
    attribution: Attribution | None
    asked_by: AskedBy | None
    vector: Vector


@dataclass(frozen=True, slots=True)
class Asked:
    """One question standing: a face Sift proposed somebody for, with its clearest description.

    `confidence` is None for a face offered because the rest of its group was named, until a
    re-match scores it. `asked_by` is who put it, never None here.
    """

    track_id: str
    asset_id: str
    person_id: str
    confidence: float | None
    vector: Vector
    asked_by: AskedBy


@dataclass(frozen=True, slots=True)
class Ruling:
    """One face's new standing, and the standing it must STILL be in for the write to land.

    A re-match reads, does its arithmetic, then writes, and somebody may answer a face in between;
    each write names what it read so the guess never lands over that answer. `asked_by` is who
    asks for a ruling that leaves the face a question, None for one an Undo puts back.
    """

    track_id: str
    was_person: str | None
    was: Attribution | None
    person_id: str | None
    attribution: Attribution | None
    confidence: float | None
    asked_by: AskedBy | None = None


def _asker(
    asked_by: AskedBy | None, person_id: str | None, attribution: Attribution | None
) -> str | None:
    """The word a write puts in `face_tracks.asked_by`, or None to leave the one standing: only a
    write that leaves the face a QUESTION says who asked (`schema._ADD_ASKED_BY`)."""
    if asked_by is None or person_id is None or attribution is not Attribution.SUGGESTED:
        return None
    return asked_by.value


@dataclass(frozen=True, slots=True)
class FiledFace:
    """One appearance nobody is on, beside the person a pass filed the FILE under, and the word
    that pass wrote (`source`) so a sentence can say where the name came from."""

    track: StoredTrack
    person_id: str
    source: str


@dataclass(frozen=True, slots=True)
class FiledOff:
    """A name a pass had put on a file, as it stood when somebody took it off: the whole row, so
    an Undo writes back the same source, moment and stash-box (`box_id`, None for a filing that
    names no box)."""

    asset_id: str
    source: str
    decided_at: int | None
    box_id: str | None = None


class Ranked(Protocol):
    """Anything that is one of an appearance's faces, as far as choosing between them goes."""

    @property
    def id(self) -> str: ...

    @property
    def quality(self) -> float: ...


def clearest[F: Ranked](faces: Iterable[F]) -> F:
    """The clearest of one appearance's faces: the highest quality, the lower id on a tie.

    THE ONE RULE for which face stands for a track: the picture shown, the portrait's frame, the
    description compared and the moment a pressed face plays from must be the same face.
    """
    return min(faces, key=lambda face: (-face.quality, face.id))


@dataclass(frozen=True, slots=True)
class _Moment:
    """One face's place in time, and what choosing between faces reads. No picture, no numbers."""

    id: str
    quality: float
    timestamp_ms: int
    frontality: float | None = None


@dataclass(frozen=True, slots=True)
class StoredFace:
    """One stored face: its picture, its numbers, its quality (to choose the clearest) and its box
    (to cut a bigger cover from the same place)."""

    id: str
    track_id: str
    timestamp_ms: int
    crop_path: str
    vector: Vector
    quality: float
    box: Box
    pixels: int | None = None
    """How big the face was in the picture its square was cut from: the file's own pixels, where
    `box` is in the reduced frame's. Null on a face stored before it was measured; for those the
    square was cut from the reduced frame, so `box` says the same thing."""
    frontality: float | None = None
    """How square-on the face was, as `quality.frontality` measured it. Null on a face stored
    before it was measured. Under the quality bar's angle it is kept and matched, and never filed as
    a reference (`quality.asked_only`)."""

    def turned(self, line: float) -> bool:
        """Whether this face is turned past `line`, the angle the quality bar in force names
        faces at. A face measured before angles were stored is not: it cleared the bar then."""
        return self.frontality is not None and self.frontality < line


@dataclass(frozen=True, slots=True)
class Remeasured:
    """One stored face as the new model describes it, beside the description it replaces: the
    old one is the KEY to the decisions remembered about the face."""

    id: str
    previous: bytes
    embedding: bytes
    strength: float


#: What described the faces a pile holds, read from their scan rows and written onto the pile,
#: after its members are assigned. Read from the table so it cannot drift from it; a pile over
#: two models (`COUNT(DISTINCT ...) > 1`) keeps a null, which grouping rebuilds.
_STAMP_PILE = """
UPDATE face_piles
   SET recognizer = (
       SELECT CASE WHEN COUNT(DISTINCT s.recognizer) = 1 THEN MIN(s.recognizer) END
         FROM face_tracks AS t
         JOIN face_scans AS s ON s.asset_id = t.asset_id
        WHERE t.pile_id = face_piles.id)
 WHERE id = ?
"""


@dataclass(frozen=True, slots=True)
class Scan:
    """How far a pass over one file got, and what produced it."""

    asset_id: str
    status: ScanStatus
    depth: str
    coverage: float
    frames_sampled: int
    track_count: int
    identified_count: int
    detector: str
    recognizer: str
    settings_digest: str
    scanned_at: int
    reached_ms: int | None = None
    """The last moment looked at, when the pass did not finish. None once it has."""
    refused_small: int | None = None
    """Faces found and refused for size, counted per moment. None on a scan made before the count
    was kept, which is "not known" and never "none refused"."""
    refused_closer: int | None = None
    """Faces refused on the closer look, counted the same way, with the same None."""
    cut_short: bool | None = None
    """Whether the time limit stopped the pass: the one stop that leaves work in the file. None
    on a scan made before this was kept, which is "not known"."""
    refused_largest: int | None = None
    """The long side of the biggest face refused for size, in the file's pixels; 0 when none was."""
    refused_blurred: int | None = None
    """`refused_closer` split by reason: too blurred, turned away, running off the edge."""
    refused_turned: int | None = None
    refused_edge: int | None = None


@dataclass(frozen=True, slots=True)
class PassRecord:
    """What a pass records about itself, apart from the faces it found, shared by both passes."""

    status: ScanStatus
    depth: str
    coverage: float
    frames_sampled: int
    detector: str
    recognizer: str
    settings_digest: str
    #: The tuning without the density, so a later pass can tell 'looked at under settings that
    #: would accept something else' from 'looked at over more or fewer moments'.
    settings_shape: str = ""
    #: How many moments this pass was asked for, as a multiplier. The one part of the tuning with
    #: an order to it, so it is compared rather than matched.
    settings_density: float = 0.0
    reached_ms: int | None = None
    #: What the pass found and refused, at each gate: `Outcome.refused_small` and
    #: `Outcome.refused_closer`, per moment. None only where a caller has no count to give, and
    #: then the row says "not known" rather than claiming the pass refused nothing.
    refused_small: int | None = None
    refused_closer: int | None = None
    #: Whether the time limit stopped the pass, as opposed to its moments running out. The one
    #: stop a press carries on from. See `FaceService._resume_point`.
    cut_short: bool | None = None
    #: The refusals told apart: the biggest face too small, in the file's pixels, and the closer
    #: look's refusals by reason. None where a caller has none to give, the same "not known".
    refused_largest: int | None = None
    refused_blurred: int | None = None
    refused_turned: int | None = None
    refused_edge: int | None = None


#: How many piles one batched read asks about at a time: under the 999 bound parameters the
#: oldest SQLite Sift may run on allows a statement.
_PILES_PER_READ = 900


@dataclass(frozen=True, slots=True)
class PileProposal:
    """One standing "this group may be this person", as the pass left it. See `pile_proposals`."""

    pile_id: str
    person_id: str
    reason: str
    folder_id: str
    files: int
    of_files: int


def _scan_row(asset_id: str, record: PassRecord, tracks: int, stamp: int) -> tuple[object, ...]:
    """The values `_RECORD_SCAN` expects, in its order, in one place for both callers.

    The quality and sampling versions are the CODE's, read from their modules as the row is
    written; nothing can pin them, unlike the tuning a `PassRecord` carries.
    """
    return (
        asset_id,
        record.status.value,
        record.depth,
        record.coverage,
        record.frames_sampled,
        tracks,
        0,
        record.detector,
        record.recognizer,
        record.settings_digest,
        record.settings_shape,
        record.settings_density,
        QUALITY_VERSION,
        FACE_SAMPLING_VERSION,
        stamp,
        record.reached_ms,
        record.refused_small,
        record.refused_closer,
        None if record.cut_short is None else int(record.cut_short),
        record.refused_largest,
        record.refused_blurred,
        record.refused_turned,
        record.refused_edge,
    )


def _scan(row: Row) -> Scan:
    return Scan(
        asset_id=str(row["asset_id"]),
        status=ScanStatus(str(row["status"])),
        depth=str(row["depth"]),
        coverage=float(row["coverage"]),
        frames_sampled=int(row["frames_sampled"]),
        track_count=int(row["track_count"]),
        identified_count=int(row["identified_count"]),
        detector=str(row["detector"]),
        recognizer=str(row["recognizer"]),
        settings_digest=str(row["settings_digest"]),
        scanned_at=int(row["scanned_at"]),
        reached_ms=None if row["reached_ms"] is None else int(row["reached_ms"]),
        refused_small=None if row["refused_small"] is None else int(row["refused_small"]),
        refused_closer=None if row["refused_closer"] is None else int(row["refused_closer"]),
        cut_short=None if row["cut_short"] is None else bool(row["cut_short"]),
        refused_largest=_count(row["refused_largest"]),
        refused_blurred=_count(row["refused_blurred"]),
        refused_turned=_count(row["refused_turned"]),
        refused_edge=_count(row["refused_edge"]),
    )


def _count(value: object) -> int | None:
    """A stored count, or None where the scan predates it: "not known", never zero."""
    return None if value is None else int(str(value))


def _track(row: Row) -> StoredTrack:
    attribution = row["attribution"]
    return StoredTrack(
        id=str(row["id"]),
        asset_id=str(row["asset_id"]),
        started_ms=int(row["started_ms"]),
        ended_ms=int(row["ended_ms"]),
        seen_in=int(row["seen_in"]),
        quality=float(row["quality"]),
        person_id=str(row["person_id"]) if row["person_id"] else None,
        confidence=float(row["confidence"]) if row["confidence"] is not None else None,
        attribution=Attribution(str(attribution)) if attribution else None,
        pile_id=str(row["pile_id"]) if row["pile_id"] else None,
    )


def _face(row: Row) -> StoredFace:
    return StoredFace(
        id=str(row["id"]),
        track_id=str(row["track_id"]),
        timestamp_ms=int(row["timestamp_ms"]),
        crop_path=str(row["crop_path"]),
        vector=recognize.unpack(bytes(row["embedding"])),
        quality=float(row["quality"]),
        box=Box(
            x=int(row["box_x"]),
            y=int(row["box_y"]),
            width=int(row["box_w"]),
            height=int(row["box_h"]),
        ),
        pixels=None if row["pixels"] is None else int(row["pixels"]),
        frontality=None if row["frontality"] is None else float(row["frontality"]),
    )


def _reference(row: Row) -> Reference:
    """One reference row, read whole: both statements that build one select every column."""
    return Reference(
        id=str(row["id"]),
        person_id=str(row["person_id"]),
        vector=recognize.unpack(bytes(row["embedding"])),
        quality=float(row["quality"]),
        crop_digest=str(row["crop_digest"]),
        origin=Origin(str(row["origin"])),
    )
