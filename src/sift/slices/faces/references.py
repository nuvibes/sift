# SPDX-License-Identifier: AGPL-3.0-or-later
"""Checking the faces a person is recognized by, before they are trusted to recognize anybody.

A wrong reference makes confident wrong matches for ever, so every candidate is checked the same
way for a folder or a pack. Exactly one face per picture is a rule: none or several is refused.
The odd one out (another person's face) is the check that matters most.
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
from sift.slices.faces.models import Box, Detection, Finding, Vector
from sift.slices.faces.pipeline import Bar
from sift.slices.faces.recognize import Recognizer

log = get_logger(__name__)

#: What a folder of candidate references may hold: the one still-picture allowlist, GIFs excluded.
REFERENCE_SUFFIXES = frozenset(
    extension
    for media in ALLOWED_MEDIA
    if media.kind is Kind.IMAGE
    for extension in media.extensions
)

#: The optional spreadsheet beside the folders.
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
    turned: bool = False
    """Kept although turned past the bar's angle (`quality.asked_only`): held for the person and
    never compared with anybody, the rule a turned face found in a file follows."""

    @property
    def usable(self) -> bool:
        """Whether this picture can be a reference: nothing left it out (a near-copy is used)."""
        return self.left_out_for is None

    @property
    def left_out_for(self) -> Finding | None:
        """The one reason this picture was left out, or None when used: a refusal outranks a
        near-copy, so the totals add up."""
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
    #: References this folder contributed; zero when an earlier import already holds them.
    added: int = 0
    created: bool = False
    already: bool = False

    @property
    def usable(self) -> list[Candidate]:
        return [item for item in self.candidates if item.usable]

    @property
    def left_out(self) -> Counter[Finding]:
        """The pictures left out, by the one reason each was, so read = used + left out."""
        reasons: Counter[Finding] = Counter()
        for item in self.candidates:
            reason = item.left_out_for
            if reason is not None:
                reasons[reason] += 1
        return reasons

    @property
    def turned(self) -> int:
        """Pictures kept for the person although turned past the bar's angle (`Candidate.turned`)."""
        return sum(1 for item in self.candidates if item.turned)

    @property
    def near_duplicates(self) -> int:
        """Pictures used although almost the same as another of this person's; never refused."""
        return sum(
            1 for item in self.candidates if item.usable and Finding.NEAR_DUPLICATE in item.findings
        )


@dataclass(frozen=True, slots=True)
class Sheet:
    """The optional extras a person cannot be asked to derive: other names, and links."""

    aliases: dict[str, tuple[str, ...]]
    links: dict[str, tuple[str, ...]]


class Auditor:
    """Runs the checks over a folder of folders, one per person, against the library's bar."""

    def __init__(
        self,
        settings: Settings,
        detector: Detector,
        recognizer: Recognizer,
        *,
        bar: Bar | None = None,
        keep_turned: bool = False,
    ) -> None:
        self._settings = settings
        self._detector = detector
        self._recognizer = recognizer
        self._bar = bar or Bar(
            min_pixels=quality_module.MIN_PIXELS,
            min_sharpness=quality_module.MIN_SHARPNESS,
            min_frontality=quality_module.MIN_FRONTALITY,
        )
        self._keep_turned = keep_turned

    async def examine(self, image: Path) -> Candidate:
        """One picture, checked and described if it passes, on a thread."""
        picture = await frames.decode_image(image, self._settings)
        if picture is None:
            return _unreadable(image)
        found = await asyncio.to_thread(self._find, image, picture)
        if isinstance(found, Candidate):
            return found
        closer, detection = await self._at_own_size(image, picture, found)
        return await asyncio.to_thread(self._judge_face, image, closer, detection)

    async def _at_own_size(
        self, image: Path, picture: np.ndarray, found: Detection
    ) -> tuple[np.ndarray, Detection]:
        """The picture a face is measured in: the reduced one, or the piece at the file's own size
        where the face there is under the floor."""
        height, width = picture.shape[:2]
        reduced = max(height, width) >= frames.REFERENCE_LONG_SIDE
        if not reduced or found.box.long_side >= self._bar.min_pixels:
            return picture, found
        left, top, right, bottom = frames.reach(found.box, width, height)
        share = (left / width, top / height, right / width, bottom / height)
        piece = await frames.decode_image(image, self._settings, piece=share)
        if piece is None:
            return picture, found
        across = piece.shape[1] / (right - left)
        down = piece.shape[0] / (bottom - top)
        box = found.box
        return piece, Detection(
            box=Box(
                x=round((box.x - left) * across),
                y=round((box.y - top) * down),
                width=max(1, round(box.width * across)),
                height=max(1, round(box.height * down)),
            ),
            score=found.score,
            landmarks=tuple(((x - left) * across, (y - top) * down) for x, y in found.landmarks),
            timestamp_ms=found.timestamp_ms,
        )

    async def examine_bytes(self, blob: bytes, label: Path) -> Candidate:
        """A picture that was never a file (a stash-box's photo), checked exactly as a folder's."""
        picture = await frames.decode_picture_bytes(blob, self._settings)
        return await asyncio.to_thread(self._judge, label, picture)

    def _judge(self, image: Path, picture: np.ndarray | None) -> Candidate:
        """The checks, over a picture already decoded (None when it could not be), on a thread."""
        if picture is None:
            return _unreadable(image)
        found = self._find(image, picture)
        if isinstance(found, Candidate):
            return found
        return self._judge_face(image, picture, found)

    def _find(self, image: Path, picture: np.ndarray) -> Candidate | Detection:
        """The one face in a picture, or the refusal of a picture with none, or several."""
        found = self._detector.detect(picture)
        if not found:
            return Candidate(path=image, findings=(Finding.NO_FACE,))
        if len(found) > 1:
            return Candidate(
                path=image,
                findings=(Finding.SEVERAL_FACES,),
                detail=f"{len(found)} faces in one picture",
            )
        return found[0]

    def _judge_face(self, image: Path, picture: np.ndarray, found: Detection) -> Candidate:
        """The checks on the one face, in the picture it is measured in, against the bar."""
        detection = self._detector.refine(picture, found)
        aligned = cropping.align(picture, detection.landmarks)
        chip = aligned.chip
        measured = quality_module.assess(
            detection.box,
            detection.landmarks,
            chip,
            containment=aligned.containment,
            min_pixels=self._bar.min_pixels,
            min_sharpness=self._bar.min_sharpness,
            min_frontality=self._bar.min_frontality,
        )
        turned = self._keep_turned and quality_module.asked_only(measured)
        if not measured.accepted and not turned:
            # Named by the check that refused it (`Quality.failed`), so code and sentence agree.
            problem = measured.failed or Finding.TOO_BLURRED
            return Candidate(path=image, findings=(problem,), detail=measured.reason)

        return Candidate(
            path=image,
            findings=(),
            # The description's direction alone: chosen pictures are ranked only against each other.
            vector=self._recognizer.embed(chip).vector,
            chip=chip,
            quality=measured.score,
            pixels=measured.pixels,
            turned=turned,
        )

    async def person(self, folder: Path) -> PersonReport:
        """One person's folder: every picture checked, then the checks that need the whole set."""
        report = PersonReport(name=folder.name)
        for image in await asyncio.to_thread(images_in, folder):
            report.candidates.append(await self.examine(image))

        mark_near_duplicates(report)
        mark_odd_ones_out(report)
        if sum(not item.turned for item in report.usable) < tuning.MIN_REFERENCES:
            report.findings.append(Finding.BELOW_MINIMUM)
        return report

    async def folder(self, root: Path) -> list[PersonReport]:
        """Every person's folder under one parent, in name order."""
        folders = await asyncio.to_thread(folders_in, root)
        return [await self.person(child) for child in folders]


def _unreadable(image: Path) -> Candidate:
    return Candidate(path=image, findings=(Finding.UNREADABLE,), detail="not a readable image")


def images_in(folder: Path) -> list[Path]:
    """The pictures in one person's folder; reads the disk, so call it off the loop."""
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
    """Flag pictures all but identical to one already kept, against the kept, not pairwise."""
    kept: list[Vector] = []
    for index, candidate in enumerate(report.candidates):
        if candidate.vector is None or not candidate.usable or candidate.turned:
            continue
        if any(_similarity(candidate.vector, other) >= tuning.NEAR_DUPLICATE for other in kept):
            report.candidates[index] = _with(candidate, Finding.NEAR_DUPLICATE)
            continue
        kept.append(candidate.vector)


def mark_odd_ones_out(report: PersonReport) -> None:
    """Flag the picture unlike the rest of that person's, never compared with itself."""
    described = [
        (index, candidate.vector)
        for index, candidate in enumerate(report.candidates)
        if candidate.vector is not None and candidate.usable and not candidate.turned
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
    """The optional spreadsheet beside the folders, its columns matched by name."""
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
    """The same candidate with one more finding, every other field carried over by `replace`."""
    return replace(candidate, findings=(*candidate.findings, finding))


def _similarity(first: Vector, second: Sequence[float]) -> float:
    return float(np.dot(np.asarray(first, dtype=np.float32), np.asarray(second, dtype=np.float32)))
