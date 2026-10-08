# SPDX-License-Identifier: AGPL-3.0-or-later
"""Checking the faces a person is recognized by, before they are trusted to recognize anybody.

The reference gallery is the one thing the whole feature rests on. A file's faces are found by a
model and can be wrong in ways that show up as a poor match; a reference that is the wrong person
is wrong in a way that shows up as **confident matches to the wrong person, for ever**. So every
candidate is checked, and the checks are the same ones whether a folder is being imported or a
pack is being made: a pack is what these checks produce, rather than a file accompanied by a
promise that somebody ran them.

Seven things can be wrong, and one of them is worth more than the other six:

- **no face** in the picture at all
- **several faces**, so which one is the person is ambiguous
- **too small**: not enough of a face to describe
- **too blurred**
- a **near-duplicate** of another of that person's, which adds nothing and costs matching time
- **below the minimum** number of pictures, which is about the person rather than any one picture
- **the odd one out**: a picture whose numbers sit far from that person's others. Almost always
  somebody else's face in the folder, and invisible to every other check because the picture itself
  is perfectly good.

**Exactly one face per image is a rule, not a situation to be handled.** An image with none or with
several is refused and reported for whoever supplied it to fix. Showing the faces found and
letting somebody choose would quietly accept an ambiguous reference, and the guarantee that a
reference *is* the face is what everything else depends on. There is no choose-which-face step
anywhere in this path, deliberately.
"""

from __future__ import annotations

import asyncio
import csv
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from sift.kernel.config import Settings
from sift.kernel.ingress import ALLOWED_MEDIA, Kind
from sift.kernel.log import get_logger
from sift.kernel.text import clean_stored_text
from sift.slices.faces import crop as cropping
from sift.slices.faces import frames, tuning
from sift.slices.faces import quality as quality_module
from sift.slices.faces.detect import Detector
from sift.slices.faces.models import Finding, Vector
from sift.slices.faces.recognize import Recognizer

log = get_logger(__name__)

#: What a folder of candidate references may hold: the still pictures Sift accepts anywhere else.
#:
#: Taken from the one allowlist rather than written out again. A second list would drift, and the
#: way it would drift is by accepting something the gate refuses, which is how a format nobody
#: checked gets decoded. GIFs are excluded here on purpose: a reference is one picture of one
#: face, and which frame of a GIF that would be is a question with no answer.
REFERENCE_SUFFIXES = frozenset(
    extension
    for media in ALLOWED_MEDIA
    if media.kind is Kind.IMAGE
    for extension in media.extensions
)

#: The optional spreadsheet beside the folders, in the four columns a list of people already has.
#: Nobody fills in a manifest: everything else Sift can work out, it works out.
SHEET_NAMES = ("people.csv", "aliases.csv")
SHEET_COLUMNS = ("name", "aliases", "channel url", "social url")


@dataclass(frozen=True, slots=True)
class Candidate:
    """One picture offered as a reference, and what came of it."""

    path: Path
    findings: tuple[Finding, ...]
    vector: Vector | None = None
    chip: np.ndarray | None = None
    quality: float = 0.0
    pixels: int = 0
    """How big the face was before it was warped onto the stored square. Kept because the square
    is the same size whatever the original was, so this is the last chance to know it."""
    detail: str | None = None

    @property
    def usable(self) -> bool:
        """Whether this picture can be a reference.

        A near-duplicate is usable and merely redundant: it is reported so somebody can tidy up,
        not refused. Everything else on the list is a refusal. Defined as having no reason to be
        left out, so the two answers are one rule and cannot disagree about any picture.
        """
        return self.left_out_for is None

    @property
    def left_out_for(self) -> Finding | None:
        """The ONE reason this picture was left out, or None when it was used.

        One reason and never two, because this is what a picture is counted under, and a picture
        counted twice makes the totals disagree with each other: a near-duplicate is used, and
        counted with the refusals too it would make "left out" larger than "read" less "used". A
        picture can also
        carry two findings (a near-copy is still looked at by the odd-one-out check, and can be
        marked by it), and it is then left out for the refusal, the only one of the two that
        stops it being used.
        """
        return next(
            (finding for finding in self.findings if finding is not Finding.NEAR_DUPLICATE), None
        )


@dataclass(slots=True)
class PersonReport:
    """Everything found about one person's folder."""

    name: str
    person_id: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    #: References this folder actually contributed, filled in by the import rather than the audit.
    #: Zero after a second import of the same folder: the pictures are already held.
    added: int = 0
    #: Whether the import created this person. An audit never does, so it is always false there.
    created: bool = False
    #: Whether an earlier import read every picture here, so this one read none of them.
    already: bool = False

    @property
    def usable(self) -> list[Candidate]:
        return [item for item in self.candidates if item.usable]

    @property
    def left_out(self) -> Counter[Finding]:
        """The pictures left out, by the one reason each was (`Candidate.left_out_for`).

        THE count of what went wrong in a folder; nothing else tallies findings. Every picture is
        either usable or here exactly once, so `len(candidates) == len(usable) + total` always
        holds, which is what lets a screen say "read", "used" and "left out" as three numbers that
        add up.
        """
        reasons: Counter[Finding] = Counter()
        for item in self.candidates:
            reason = item.left_out_for
            if reason is not None:
                reasons[reason] += 1
        return reasons

    @property
    def near_duplicates(self) -> int:
        """Pictures used although almost the same as another of this person's.

        A note for whoever tidies the folder, never a refusal: these are among the usable, and are
        counted here only so the screen can say they were kept rather than folding them into what
        was left out.
        """
        return sum(
            1 for item in self.candidates if item.usable and Finding.NEAR_DUPLICATE in item.findings
        )


@dataclass(frozen=True, slots=True)
class Sheet:
    """The optional extras a person cannot be asked to derive: other names, and links."""

    aliases: dict[str, tuple[str, ...]]
    links: dict[str, tuple[str, ...]]


class Auditor:
    """Runs the checks over a folder of folders, one per person."""

    def __init__(self, settings: Settings, detector: Detector, recognizer: Recognizer) -> None:
        self._settings = settings
        self._detector = detector
        self._recognizer = recognizer

    async def examine(self, image: Path) -> Candidate:
        """One picture, checked and described if it passes.

        The checks run in a thread, as the scan's do: finding, aligning and describing a face is
        model work, and a folder of thousands done on the event loop stalls every other request
        and every stream for the length of the import.
        """
        picture = await frames.decode_image(image, self._settings)
        return await asyncio.to_thread(self._judge, image, picture)

    async def examine_bytes(self, blob: bytes, label: Path) -> Candidate:
        """A picture that was never a file (a stash-box's photo), checked exactly as a folder's.

        `label` stands where a file's path would, for the report and the log; nothing is opened by
        it. The same checks and the same order, so a starter passes or fails for the reasons an
        imported picture would. See `frames.decode_picture_bytes` for why it stays in memory.
        """
        picture = await frames.decode_picture_bytes(blob, self._settings)
        return await asyncio.to_thread(self._judge, label, picture)

    def _judge(self, image: Path, picture: np.ndarray | None) -> Candidate:
        """The checks, over a picture already decoded (None when it could not be).

        Synchronous on purpose, called from a thread.
        """
        if picture is None:
            return Candidate(
                path=image, findings=(Finding.UNREADABLE,), detail="not a readable image"
            )

        found = self._detector.detect(picture)
        if not found:
            return Candidate(path=image, findings=(Finding.NO_FACE,))
        if len(found) > 1:
            return Candidate(
                path=image,
                findings=(Finding.SEVERAL_FACES,),
                detail=f"{len(found)} faces in one picture",
            )

        detection = self._detector.refine(picture, found[0])
        aligned = cropping.align(picture, detection.landmarks)
        chip = aligned.chip
        measured = quality_module.assess(
            detection.box, detection.landmarks, chip, containment=aligned.containment
        )
        if not measured.accepted:
            # Named by the check that refused it (`Quality.failed`), the same one that wrote the
            # sentence, so somebody told what is wrong with their photo is told something they can
            # act on, and the code and the sentence agree.
            #
            # Not worked out here again: a second copy of the rule with fewer cases would log a face
            # turned from the camera as `too_blurred` beside a detail saying it was turned.
            problem = measured.failed or Finding.TOO_BLURRED
            return Candidate(path=image, findings=(problem,), detail=measured.reason)

        return Candidate(
            path=image,
            findings=(),
            # The description's own direction only. A reference is a picture somebody chose on
            # purpose, and these scores rank a person's chosen pictures against each other rather
            # than against anything a scan found, so the ranking terms that were added for that
            # contest are deliberately left out of this one. The audit is what speaks about a
            # reference that does not belong; see `Finding.ODD_ONE_OUT`.
            vector=self._recognizer.embed(chip).vector,
            chip=chip,
            quality=measured.score,
            pixels=measured.pixels,
        )

    async def person(self, folder: Path) -> PersonReport:
        """One person's folder: every picture checked, then the checks that need the whole set."""
        report = PersonReport(name=folder.name)
        for image in await asyncio.to_thread(images_in, folder):
            report.candidates.append(await self.examine(image))

        mark_near_duplicates(report)
        mark_odd_ones_out(report)
        if len(report.usable) < tuning.MIN_REFERENCES:
            report.findings.append(Finding.BELOW_MINIMUM)
        return report

    async def folder(self, root: Path) -> list[PersonReport]:
        """Every person's folder under one parent, in name order."""
        folders = await asyncio.to_thread(folders_in, root)
        return [await self.person(child) for child in folders]


def images_in(folder: Path) -> list[Path]:
    """The pictures in one person's folder. Reading a directory touches the disk, so it happens off
    the event loop: a folder on a network share can take a noticeable moment."""
    return [
        image
        for image in sorted(folder.iterdir())
        if image.suffix.lower() in REFERENCE_SUFFIXES and image.is_file()
    ]


def picture_digests(folder: Path) -> list[str]:
    """The identity of each picture `images_in` lists. Reads the disk: call it off the loop."""
    return [cropping.digest(image.read_bytes()) for image in images_in(folder)]


def folders_in(root: Path) -> list[Path]:
    return [child for child in sorted(root.iterdir()) if child.is_dir()]


def mark_near_duplicates(report: PersonReport) -> None:
    """Flag pictures that are all but identical to one already kept.

    Compared against what has been kept so far rather than pairwise, so a run of five near-identical
    pictures reports four rather than ten. The first is kept: two nearly identical references add no
    information and only lengthen every comparison.
    """
    kept: list[Vector] = []
    for index, candidate in enumerate(report.candidates):
        if candidate.vector is None or not candidate.usable:
            continue
        if any(_similarity(candidate.vector, other) >= tuning.NEAR_DUPLICATE for other in kept):
            report.candidates[index] = _with(candidate, Finding.NEAR_DUPLICATE)
            continue
        kept.append(candidate.vector)


def mark_odd_ones_out(report: PersonReport) -> None:
    """Flag the picture that does not look like the rest of that person's.

    Judged by how alike a picture is to the *others*, never including itself: a picture is
    perfectly similar to itself, and including that would drag every score up and hide exactly the
    case being looked for.

    Needs a few pictures to mean anything: with two, "unlike the others" is a statement about both
    of them and there is no way to tell which is the intruder.
    """
    described = [
        (index, candidate.vector)
        for index, candidate in enumerate(report.candidates)
        if candidate.vector is not None and candidate.usable
    ]
    if len(described) < 4:
        return

    matrix = np.asarray([vector for _, vector in described], dtype=np.float32)
    likeness = matrix @ matrix.T
    np.fill_diagonal(likeness, np.nan)
    averages = np.nanmean(likeness, axis=1)
    for position, (index, _) in enumerate(described):
        if float(averages[position]) < tuning.ODD_ONE_OUT:
            report.candidates[index] = _with(report.candidates[index], Finding.ODD_ONE_OUT)


def read_sheet(root: Path) -> Sheet:
    """The optional spreadsheet dropped beside the folders.

    Four columns, matched by name rather than by position so a spreadsheet with extra columns in it
    (a status, a note) is read rather than refused. No spreadsheet means a set of people with
    names and no other names, which is perfectly valid.
    """
    aliases: dict[str, tuple[str, ...]] = {}
    links: dict[str, tuple[str, ...]] = {}
    for name in SHEET_NAMES:
        path = root / name
        if not path.is_file():
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            headings = {
                (field or "").strip().casefold(): field for field in (reader.fieldnames or [])
            }
            if "name" not in headings:
                log.warning("faces.sheet.no_name_column", path=str(path))
                continue
            for row in reader:
                person = clean_stored_text(str(row.get(headings["name"]) or "")).strip()
                if not person:
                    continue
                aliases[person] = _split(row, headings, "aliases")
                links[person] = tuple(
                    value
                    for column in ("channel url", "social url")
                    for value in _split(row, headings, column)
                )
        break
    return Sheet(aliases=aliases, links=links)


def _split(row: dict[str, str | None], headings: dict[str, str], column: str) -> tuple[str, ...]:
    field_name = headings.get(column)
    if field_name is None:
        return ()
    raw = clean_stored_text(str(row.get(field_name) or ""))
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _with(candidate: Candidate, finding: Finding) -> Candidate:
    """The same candidate with one more finding, every other field carried over.

    `replace` rather than a constructor naming the fields: a list written out by hand drops a
    field the day one is added (`pixels`, say). A near-duplicate is still imported, so it would be
    stored with a face size of 0 while its twin, the first of the pair, kept the size measured.
    """
    return replace(candidate, findings=(*candidate.findings, finding))


def _similarity(first: Vector, second: Sequence[float]) -> float:
    return float(np.dot(np.asarray(first, dtype=np.float32), np.asarray(second, dtype=np.float32)))
