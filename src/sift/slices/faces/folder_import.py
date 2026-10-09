# SPDX-License-Identifier: AGPL-3.0-or-later
"""Importing a folder of people as a task: one folder per person, read by Sift from its path.

The folder is read here, off any request, a person at a time: a gallery of hundreds of people is
an hour of face reading, and a request holding it open would freeze the window that sent it. What each
person's folder gave is held as it is read, so a stopped import keeps what landed.
"""

from __future__ import annotations

import asyncio
import shutil
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from sift.kernel.access.sentences import and_then
from sift.kernel.access.sentences import people as people_counted
from sift.kernel.jobs import JobContext, JobFailedPermanently, JobPaused, JobQueue
from sift.kernel.log import get_logger
from sift.slices.faces.models import Finding
from sift.slices.faces.references import PersonReport, folders_in, images_in
from sift.slices.faces.service import FaceService
from sift.slices.faces.store_left_out import LeftOutStore
from sift.slices.faces.weights import WeightError

log = get_logger(__name__)

#: Importing a folder of people (`import_folder`), queued by both of the import's doors.
FACE_FOLDER_IMPORT = "face_folder_import"

#: The most pictures one import may hold, and the most bytes: either alone is bypassable (one
#: enormous file, or a hundred thousand tiny ones that each cost a round of face detection).
MAX_FOLDER_FILES = 20_000
MAX_FOLDER_BYTES = 8 * 1024 * 1024 * 1024

#: The note while the folder is counted, before the first person is read.
READING = "Reading the folder\u2026"

#: How many folders the closing sentence names before it counts the rest.
NAMED_FOLDERS = 5

#: Each reason a photo was left out, worded to follow its count ("347 facing away").
LEFT_OUT_WORDS: dict[Finding, str] = {
    Finding.NO_FACE: "with no face in it",
    Finding.UNREADABLE: "not a readable picture",
    Finding.SEVERAL_FACES: "with more than one face",
    Finding.TOO_SMALL: "too small",
    Finding.TOO_BLURRED: "too blurred",
    Finding.TURNED_AWAY: "facing away",
    Finding.RUNS_OFF_EDGE: "cut off at the edge",
    Finding.ODD_ONE_OUT: "looking like someone else",
}


def worded(reason: Finding) -> str:
    """A reason's words in the report, its own name where it has none."""
    return LEFT_OUT_WORDS.get(reason, reason.value.replace("_", " "))


@dataclass(frozen=True, slots=True)
class FolderListing:
    """The people's folders under the chosen one, and what they hold."""

    people: list[Path]
    pictures: int
    size: int


def read_folder(root: Path) -> FolderListing:
    """List the people's folders and count their pictures. Reads the disk: call it off the loop.

    Counted up to the caps and no further, so a folder far past them is refused without every
    picture in it being looked at.
    """
    people = folders_in(root)
    pictures = 0
    size = 0
    for folder in people:
        for image in images_in(folder):
            pictures += 1
            size += image.stat().st_size
            if pictures > MAX_FOLDER_FILES or size > MAX_FOLDER_BYTES:
                return FolderListing(people=people, pictures=pictures, size=size)
    return FolderListing(people=people, pictures=pictures, size=size)


def over_the_caps(listing: FolderListing) -> str | None:
    """The sentence a folder past either cap is refused with, or None when it is within both."""
    if listing.pictures > MAX_FOLDER_FILES:
        return (
            f"That folder holds more than {MAX_FOLDER_FILES:,} pictures, more than Sift imports in "
            "one go. Import it in parts."
        )
    if listing.size > MAX_FOLDER_BYTES:
        gigabytes = MAX_FOLDER_BYTES // (1024 * 1024 * 1024)
        return (
            f"That folder holds more than {gigabytes} GB of pictures, more than Sift imports in "
            "one go. Import it in parts."
        )
    return None


async def _listing_of(root: Path) -> FolderListing:
    """The folder's listing, or the import refused where it cannot be read or is past a cap."""
    try:
        listing = await asyncio.to_thread(read_folder, root)
    except OSError as error:
        raise JobFailedPermanently(
            f"Sift couldn't read the folder {root}. Check that it is still there."
        ) from error
    refused = over_the_caps(listing)
    if refused:
        raise JobFailedPermanently(refused)
    return listing


@dataclass(slots=True)
class Tally:
    """What the import has done so far, added to a person at a time."""

    people: int = 0
    added: int = 0
    left_out: Counter[Finding] = field(default_factory=Counter)
    near_copies: int = 0
    #: Kept for their person though turned past the bar's angle, never compared with anybody.
    turned: int = 0
    to_check: list[str] = field(default_factory=list)
    #: Folders an earlier import read whole, passed over without a picture read.
    already: int = 0

    def add(self, report: PersonReport) -> None:
        if report.already:
            self.already += 1
            return
        self.people += 1
        self.added += report.added
        self.left_out.update(report.left_out)
        self.near_copies += report.near_duplicates
        self.turned += report.turned
        if not report.usable or report.left_out or report.near_duplicates:
            self.to_check.append(report.name)

    def said(self) -> str:
        """The import's report: the faces kept, from how many people, the photos left out and
        why, and the folders worth going back to."""
        faces = "1 face" if self.added == 1 else f"{self.added:,} faces"
        parts = [f"Kept {faces} from {people_counted(self.people)}."]
        left = sum(self.left_out.values())
        if left:
            reasons = [
                f"{count:,} {worded(reason)}" for reason, count in self.left_out.most_common()
            ]
            photos = "1 photo" if left == 1 else f"{left:,} photos"
            parts.append(f"{photos} left out: {', '.join(reasons)}.")
        if self.turned:
            photos = "1 photo" if self.turned == 1 else f"{self.turned:,} photos"
            parts.append(f"{photos} facing away kept for their person, never matched.")
        if self.near_copies:
            photos = "1 photo" if self.near_copies == 1 else f"{self.near_copies:,} photos"
            parts.append(f"{photos} kept though almost the same as another.")
        if self.already:
            folders = "1 folder" if self.already == 1 else f"{self.already:,} folders"
            parts.append(f"{folders} imported before, not read again.")
        if self.to_check:
            named = self.to_check[:NAMED_FOLDERS]
            more = len(self.to_check) - len(named)
            listed = and_then([*named, f"{more:,} more"] if more else named)
            parts.append(f"Check these folders: {listed}.")
        return " ".join(parts)


def left_out_files(report: PersonReport, root: Path) -> list[tuple[str, str]]:
    """Each picture of a person's folder that was left out or kept as a near copy, as
    `(path inside the chosen folder, reason)`."""
    rows: list[tuple[str, str]] = []
    for item in report.candidates:
        reason = item.left_out_for
        if reason is None and Finding.NEAR_DUPLICATE in item.findings:
            reason = Finding.NEAR_DUPLICATE
        if reason is not None:
            rows.append((_inside(item.path, root), reason.value))
    return rows


def _inside(path: Path, root: Path) -> str:
    """A picture's path inside the chosen folder, or its name where a link led outside it."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


async def import_folder(
    context: JobContext,
    *,
    service: FaceService,
    ask: Callable[[JobQueue], Awaitable[None]],
    left_out: LeftOutStore | None = None,
) -> None:
    """Read a folder of people and hold each person's faces as facial fingerprints.

    The payload names the folder by ids, never by a path: `grant` and `within` for a folder inside
    one Sift has been given, checked again here, or `staged` for the copy of a browser's upload,
    removed when the task ends for good (kept through a pause, which runs it again from the start:
    a person already held adds nothing the second time). `ask` queues the pass over facial
    fingerprints, asked for whenever anything landed, a stopped import included. `left_out` keeps
    which pictures the report counts.
    """
    staged = context.payload.get("staged")
    tally = Tally()
    root: Path | None = None
    try:
        if not await service.enabled():
            raise JobFailedPermanently(
                "Face recognition is switched off, so the folder was not imported."
            )
        root = await _folder_of(context, service)
        await context.set_note(READING)
        listing = await _listing_of(root)
        if left_out is not None:
            await left_out.begin(context.job.id)
        total = len(listing.people)
        source = _source_of(context, root)
        for done, folder in enumerate(listing.people, start=1):
            await context.raise_if_canceled()
            if context.stopping() == "pause":
                raise JobPaused
            try:
                report = await service.import_person_folder(folder, source=source)
            except WeightError as error:
                raise JobFailedPermanently(str(error)) from error
            tally.add(report)
            if left_out is not None:
                rows = left_out_files(report, root)
                await left_out.keep(context.job.id, rows, most=MAX_FOLDER_FILES)
            await context.set_note(f"Importing {done:,} of {people_counted(total)}\u2026")
            await context.report_progress(done / total)
        await context.set_progress(1.0)
        await context.set_note(tally.said())
        log.info("faces.folder.imported", folders=tally.people, added=tally.added)
    finally:
        if tally.added:
            await ask(context.queue)
        if staged and root is not None and context.stopping() != "pause":
            # A copy of a browser's upload under Sift's own cache, made for this task alone.
            # nosemgrep: sift-no-file-removal-outside-delete-trash
            await asyncio.to_thread(shutil.rmtree, root, True)


#: The name a staged upload's folder is given under the scratch folder (`FaceService.scratch_root`).
STAGED_PREFIX = "folder-of-people-"


async def _folder_of(context: JobContext, service: FaceService) -> Path:
    """The folder this task reads, from the ids its payload carries.

    A granted folder is resolved and confined again, as the route did: the grant may have been
    given back, or the folder replaced by a link elsewhere, since the press.
    """
    staged = context.payload.get("staged")
    if isinstance(staged, str):
        if not staged.startswith(STAGED_PREFIX) or Path(staged).name != staged:
            raise JobFailedPermanently("That import names no folder Sift was sent.")
        return service.scratch_root() / staged
    grant_id = context.require_str("grant", "a folder import names no folder")
    within = str(context.payload.get("within") or "")
    grants = {grant.id: Path(grant.abs_path) for grant in await context.library.grants()}
    granted = grants.get(grant_id)
    if granted is None:
        raise JobFailedPermanently(
            "Sift no longer has the folder this import was reading. Choose it again."
        )
    folder = await asyncio.to_thread(lambda: (granted / within).resolve())
    if not inside(folder, granted):
        raise JobFailedPermanently("That folder isn't inside one Sift has been given.")
    return folder


#: The longest folder name an entry keeps as where it came from.
_SOURCE_LENGTH = 255


def _source_of(context: JobContext, root: Path) -> str | None:
    """The folder of people's own name, which every entry it brings says it came from: the
    granted folder's last part, or the name a browser's upload was chosen by (its copy under the
    cache is named for the task). None where an upload of several folders named none."""
    if isinstance(context.payload.get("staged"), str):
        named = context.payload.get("named")
        if not isinstance(named, str) or not named.strip():
            return None
        return named.strip()[:_SOURCE_LENGTH]
    return root.name[:_SOURCE_LENGTH] or None


def inside(folder: Path, granted: Path) -> bool:
    """Whether a resolved folder is the granted one or sits under it."""
    return folder == granted or granted in folder.parents
