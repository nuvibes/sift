# SPDX-License-Identifier: AGPL-3.0-or-later
"""Renaming many files at once from one template, planned in full before anything is written.

    preview(ids, template)   every new name, with the ones that clash marked. Writes nothing.
    apply(ids, template)     the same plan, carried out one file at a time through `rename`,
                             and ONE receipt whose Undo puts every file back.

The words are the kernel's naming words, the same ones a download is named with, filled here from
what the library knows about each file (`library_facts`): the username it is filed under and its
Site, its title, the code its Site files it under, the day it was released, and the day it was
added, which is what `{date}` means for a file already in the library.

**Every name is planned before any file moves**, in the order the files were given, against the
names already in each folder. A name the disk already holds, or one an earlier file of the batch
has just claimed, is a clash, and the person chooses what a clash does: take the next number, or
leave that file as it is. The plan is made again at the moment of applying, from the disk as it
is then, and each rename still claims its name exclusively, so a name taken in between is a file
left alone and never a file written over.

**One receipt for the whole batch.** Each rename is recorded on its own as well, which is what
lets the Undo walk them backwards: a file renamed again since is refused by the per-file undo it
goes through, and the rest still go back.

A large batch, or one on a folder served from another machine, runs as a task with progress
rather than holding a request open while a share answers a thousand renames.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal, Protocol

from sift.kernel.access import Repository, Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Database
from sift.kernel.jobs import JobContext, JobQueue, register_handler
from sift.kernel.jobs.families import Family
from sift.kernel.log import get_logger
from sift.kernel.naming import MOST_COLLISIONS, Facts, fill, numbered, without_unsafe
from sift.kernel.vocabulary import Subject
from sift.kernel.workbench import (
    DOER,
    Preview,
    Recorded,
    Recorder,
    Reversal,
    Worded,
    payload_held,
)
from sift.slices.organize.service import (
    Organizer,
    OrganizeRefused,
    Place,
    check_filename,
)

log = get_logger(__name__)

#: What a name that clashes does: takes the next number, or leaves that file as it is.
OnClash = Literal["number", "skip"]

#: What the plan says about one file.
#:
#: `renamed` gets the name the template made; `numbered` gets it with the next number, because
#: the name clashed; `same` keeps its name (the template made the name it already has, or made
#: nothing); `taken` and `twice` keep their names because the name clashed and the person chose to
#: leave those alone, `taken` with a file already in the folder and `twice` with an earlier file
#: of this batch; `refused` cannot be renamed at all, and says why.
State = Literal["renamed", "numbered", "same", "taken", "twice", "refused"]

#: The most files one batch takes. The ceiling Enrich keeps for a selection of files, for the same
#: reason: past it a selection is a library, and a library is renamed a folder at a time.
MOST_FILES = 1000

#: The most files a batch renames while the request waits. Past it, or on a folder served from
#: another machine, it runs as a task with progress instead.
MOST_WHILE_WAITING = 200

#: How many planned rows a preview sends back. The counts cover the whole batch; the rows are what
#: a person reads down, and a list of a thousand names is read by nobody.
PREVIEW_ROWS = 100

#: The receipt's queue name: what the Undo asks the workbench for.
QUEUE = "batch-rename"

#: The task, by the name the queue knows it by.
RENAME_BATCH = "organize_rename_batch"

#: What each word means for a file already in the library, over the kernel's own keys. The kernel's
#: sentences are a download's, and `{date}` there is the day of the download; here it is the day
#: the file was added.
WORDS: dict[str, str] = {
    "name": "The name it has now, without its extension.",
    "n": "Its place in this batch, as 1, 2, 3.",
    "creator": "The username it's filed under. Empty where there's none.",
    "site": "The Site that username is on. Empty where there's none.",
    "title": "The title it was given. Empty where it has none.",
    "date": "The day it was added to the library, as 2026-08-13.",
    "time": "The time it was added, as 14-05.",
    "posted": "The day it was released, as 2026-08-13. Empty where nobody has said.",
    "id": "The code its Site files it under. Empty where nobody has said.",
}


@dataclass(frozen=True, slots=True)
class Planned:
    """One file of the plan: what it is called now, what it would be called, and why."""

    asset_id: str
    before: str
    after: str
    state: State
    reason: str | None = None
    location_id: str | None = None

    @property
    def moves(self) -> bool:
        return self.state in ("renamed", "numbered")


@dataclass(frozen=True, slots=True)
class Plan:
    """The whole batch, planned, in the order the files were given."""

    rows: tuple[Planned, ...]
    #: Whether any of the files sits on a folder served from another machine.
    remote: bool = False

    def count(self, *states: State) -> int:
        return sum(1 for row in self.rows if row.state in states)

    @property
    def moving(self) -> int:
        return self.count("renamed", "numbered")

    @property
    def as_task(self) -> bool:
        """Whether carrying this out is a task rather than an answer the request waits for."""
        return self.moving > MOST_WHILE_WAITING or (self.remote and self.moving > 0)


@dataclass(frozen=True, slots=True)
class Applied:
    """What carrying a batch out did."""

    renamed: int = 0
    skipped: int = 0
    receipt_id: str | None = None
    job_id: str | None = None
    #: The first refusal met on the way, as a sentence, where a file was left alone.
    reason: str | None = None


@dataclass(slots=True)
class _Folder:
    """The names one folder holds, folded for comparison, as the plan goes down the batch."""

    held: set[str]
    #: The names an earlier file of this batch has claimed, so a clash can say which kind it is.
    claimed: set[str] = field(default_factory=set)


def _folded(name: str) -> str:
    """A name as a folder compares it: a case-insensitive disk holds `A.mp4` and `a.mp4` as one."""
    return name.casefold()


def _listing(directory: Path) -> set[str]:
    """Every name in one folder, folded. Blocking, and called from a thread."""
    try:
        with os.scandir(directory) as entries:
            return {_folded(entry.name) for entry in entries}
    except OSError:
        return set()


def plan(
    places: Sequence[tuple[str, Place | str]],
    stems: dict[str, str],
    folders: dict[Path, set[str]],
    *,
    on_clash: OnClash,
) -> Plan:
    """Every file's new name, decided in order against what each folder holds. Pure.

    `places` is the batch in its order; `stems` the stem the template made for each placed file
    (empty where it made nothing); `folders` the names each folder holds now, folded.

    Planned the way it will be carried out: file by file, in order. A file that moves gives its old
    name up, so a later file may take it, which is what makes renumbering a run of files work; a
    name an earlier file has claimed is a clash for every file after it.
    """
    state = {directory: _Folder(held=set(names)) for directory, names in folders.items()}
    rows: list[Planned] = []
    remote = False
    for asset_id, place in places:
        if isinstance(place, str):
            rows.append(
                Planned(asset_id=asset_id, before="", after="", state="refused", reason=place)
            )
            continue
        location = place.location
        before = location.filename
        remote = remote or place.remote
        stem = stems.get(asset_id, "")
        suffix = Path(before).suffix
        wanted = f"{stem}{suffix}" if stem else before
        folder = state.setdefault(place.directory, _Folder(held=set()))
        row = _one(asset_id, location.id, before, wanted, stem, suffix, folder, on_clash)
        if row.moves:
            folder.held.discard(_folded(before))
            folder.held.add(_folded(row.after))
            folder.claimed.add(_folded(row.after))
        rows.append(row)
    return Plan(rows=tuple(rows), remote=remote)


def _one(
    asset_id: str,
    location_id: str,
    before: str,
    wanted: str,
    stem: str,
    suffix: str,
    folder: _Folder,
    on_clash: OnClash,
) -> Planned:
    """One file of the plan. See `plan`."""
    if _folded(wanted) == _folded(before):
        return Planned(asset_id, before, before, "same", location_id=location_id)
    try:
        wanted = check_filename(wanted)
    except OrganizeRefused as refused:
        return Planned(asset_id, before, before, "refused", str(refused), location_id)
    if _folded(wanted) not in folder.held:
        return Planned(asset_id, before, wanted, "renamed", location_id=location_id)
    clash: State = "twice" if _folded(wanted) in folder.claimed else "taken"
    if on_clash == "number":
        for nth in range(1, MOST_COLLISIONS + 1):
            candidate = f"{numbered(stem, nth)}{suffix}"
            if _folded(candidate) == _folded(before):
                return Planned(asset_id, before, before, "same", location_id=location_id)
            if _folded(candidate) not in folder.held:
                return Planned(asset_id, before, candidate, "numbered", location_id=location_id)
    return Planned(asset_id, before, before, clash, location_id=location_id)


# --- what the library knows about each file -------------------------------------------------------


class ReadsNamingFacts(Protocol):
    """What `library_facts` asks of the organizer, and nothing more of it."""

    async def naming_facts(self, asset_ids: Sequence[str]) -> dict[str, dict[str, object]]: ...


async def library_facts(
    organizer: ReadsNamingFacts, placed: Sequence[tuple[str, Place]]
) -> dict[str, Facts]:
    """What the template can say about each placed file, keyed by file. `{n}` is its place among
    them, from 1, in the order given. The rows come through the organizer, which reads them from
    the kernel's content store: what a file is called and who it was filed under are the kernel's
    facts, read where every other read of a file's row is scoped."""
    rows = await organizer.naming_facts([asset_id for asset_id, _ in placed])
    facts: dict[str, Facts] = {}
    for nth, (asset_id, place) in enumerate(placed, start=1):
        found = rows.get(asset_id, {})
        added = found.get("added_at")
        facts[asset_id] = Facts(
            site=_text(found.get("site")),
            username=_text(found.get("creator")),
            original=Path(place.location.filename).stem,
            when=datetime.fromtimestamp(int(added), UTC) if isinstance(added, int) else None,
            id=_text(found.get("site_code")),
            n=nth,
            title=_text(found.get("title")),
            posted=_day(found.get("release_date")),
        )
    return facts


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _day(value: object) -> date | None:
    """A release date as a calendar day, or None for anything that does not read as one."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


# --- planning and carrying out ---------------------------------------------------------------------


#: What a rename, once done, tells the search index about. Handed in: the index is another area's.
Touched = Callable[[Sequence[str]], Awaitable[None]]


class BatchRenamer:
    """The batch rename: planned here, carried out one file at a time through the organizer."""

    def __init__(
        self,
        organizer: Organizer,
        database: Database,
        recorder: Recorder,
        *,
        touched: Touched,
        queue: JobQueue | None = None,
    ) -> None:
        self._organizer = organizer
        self._db = database
        self._recorder = recorder
        self._touched = touched
        self._queue = queue

    async def plan(
        self, asset_ids: Sequence[str], template: str, *, on_clash: OnClash, actor: Viewer
    ) -> Plan:
        """Every file's new name, from the disk as it is now. Writes nothing."""
        wanted = list(dict.fromkeys(asset_ids))
        places = await self._organizer.places(wanted, actor=actor)
        ordered = [(one, places[one]) for one in wanted]
        placed = [(one, place) for one, place in ordered if isinstance(place, Place)]
        facts = await library_facts(self._organizer, placed)
        stems = {one: fill(template, facts[one]) for one, _ in placed}
        directories = {place.directory for _, place in placed}
        folders = {
            directory: await asyncio.to_thread(_listing, directory)
            for directory in sorted(directories)
        }
        return plan(ordered, stems, folders, on_clash=on_clash)

    async def apply(
        self, asset_ids: Sequence[str], template: str, *, on_clash: OnClash, actor: Viewer
    ) -> Applied:
        """Plan again and carry it out, or hand it to a task where it is large or remote."""
        planned = await self.plan(asset_ids, template, on_clash=on_clash, actor=actor)
        if planned.moving == 0:
            return Applied(skipped=len(planned.rows) - planned.count("same"))
        if planned.as_task and self._queue is not None:
            job_id = await self._queue.enqueue(
                RENAME_BATCH,
                {
                    "actor_id": actor.id,
                    "asset_ids": list(dict.fromkeys(asset_ids)),
                    # What `fill` makes of it, unchanged; carried in the form that cannot be
                    # mistaken for a path by the payload's own check.
                    "template": without_unsafe(template),
                    "on_clash": on_clash,
                },
                # Once. A second attempt would fill the template over names the first attempt
                # already gave, and `{name}` would then repeat itself in every one of them.
                max_attempts=1,
                requested_by=actor.id,
            )
            return Applied(job_id=job_id)
        return await self.carry_out(planned, template, actor=actor)

    async def carry_out(
        self,
        planned: Plan,
        template: str,
        *,
        actor: Viewer,
        progress: Callable[[int, int], Awaitable[bool]] | None = None,
    ) -> Applied:
        """Rename every file the plan moves, in order, then write the one receipt.

        `progress` is told how far along the batch is and answers whether to carry on; a task
        stopped half way keeps what it did, and its receipt names exactly that.
        """
        moving = [row for row in planned.rows if row.moves]
        moves: list[str] = []
        renamed: list[tuple[str, str]] = []
        skipped = 0
        reason: str | None = None
        for done, row in enumerate(moving, start=1):
            try:
                organized = await self._organizer.rename(
                    row.asset_id, new_name=row.after, actor=actor, location_id=row.location_id
                )
            except OrganizeRefused as refused:
                skipped += 1
                reason = reason or str(refused)
            else:
                moves.append(organized.move_id)
                renamed.append((organized.asset_id, organized.filename))
            if progress is not None and not await progress(done, len(moving)):
                skipped += len(moving) - done
                break
        receipt_id = await self._receipt(moves, renamed, template, actor) if moves else None
        if renamed:
            # The name and the path are both indexed. Once for the batch, not once a file.
            await self._touched([asset_id for asset_id, _ in renamed])
        log.info("organize.renamed_batch", renamed=len(renamed), skipped=skipped)
        return Applied(renamed=len(renamed), skipped=skipped, receipt_id=receipt_id, reason=reason)

    async def _receipt(
        self, moves: list[str], renamed: list[tuple[str, str]], template: str, actor: Viewer
    ) -> str:
        """The one line in the record that says the batch happened, and whose Undo takes it back.

        Its subjects are every renamed file, so the batch is on each file's own History; its
        payload is the moves in the order they were made, which is the order Undo walks backwards.
        """
        files = "file" if len(renamed) == 1 else "files"
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            return await self._recorder.record_on(
                connection,
                queue=QUEUE,
                user_id=actor.id,
                title=f"Renamed {len(renamed):,} {files}",
                detail="Undo puts every one of them back to the name it had.",
                payload=json.dumps({"moves": moves, "template": template}),
                subjects=[Subject(kind="asset", id=one, name=name) for one, name in renamed],
                verb="renamed",
            )


# --- taking a batch back ----------------------------------------------------------------------------


#: Why a file of a batch did not go back. True of every refusal the organizer's undo makes: each
#: one is something that changed after the batch ran.
KEPT_SINCE = "because something changed after the rename"


def undone_in_part(put_back: int, of: int) -> str | None:
    """The line an undo of a batch says when not every file went back, or None when all did."""
    if put_back == of:
        return None
    if put_back == 0:
        return f"None of the {of:,} names went back, {KEPT_SINCE}."
    return (
        f"Put back {put_back:,} of {of:,} names. The others keep the names they have now, "
        f"{KEPT_SINCE}."
    )


class BatchRenameReceipts:
    """How a batch rename is taken back. A reverser with no card: the act is on each file's
    History and in the record, not in a pile anybody works through."""

    name = QUEUE
    #: Every batch can be taken back, file by file, as far as each file still allows.
    reversible = True

    def __init__(self, organizer: Organizer, *, touched: Touched) -> None:
        self._organizer = organizer
        self._touched = touched

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing: the line names how many files, and each file's own History names the batch."""
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> Reversal:
        """Put every file of the batch back, newest rename first, and say how many went back.

        Each goes back through the organizer's own undo, which refuses a file that has been renamed
        or moved since: that file keeps where it is now, and the rest still go back.
        """
        moves = _moves(payload)
        put_back: list[str] = []
        refused = 0
        for move_id in reversed(moves):
            try:
                undone = await self._organizer.undo(move_id, actor=viewer)
            except OrganizeRefused:
                refused += 1
                continue
            put_back.append(undone.asset_id)
        if put_back:
            await self._touched(put_back)
        log.info("organize.batch_undone", put_back=len(put_back), refused=refused)
        return Reversal(
            put_back=len(put_back),
            of=len(moves),
            said=undone_in_part(len(put_back), len(moves)),
        )

    def worded(self, recorded: Recorded) -> Worded | None:
        """The batch's line: who renamed how many files. None for a receipt with no moves."""
        count = len(_moves(recorded.payload))
        if count == 0:
            return None
        # "At once" only says something about a batch; one file was simply renamed.
        if count == 1:
            return Worded(said=(DOER, " renamed 1 file"))
        return Worded(said=(DOER, f" renamed {count:,} files at once"))


def _moves(payload: str) -> list[str]:
    moves = payload_held(payload).get("moves")
    if not isinstance(moves, list):
        return []
    return [one for one in moves if isinstance(one, str) and one]


# --- the task -----------------------------------------------------------------------------------


def register_handlers(
    *,
    organizer: Organizer,
    database: Database,
    recorder: Recorder,
    access: Repository,
    touched: Touched,
) -> None:
    """Claim the batch rename's task. Called once, at boot, by whoever built the organizer."""
    renamer = BatchRenamer(organizer, database, recorder, touched=touched)

    async def handle(context: JobContext) -> None:
        await rename_batch(context, renamer=renamer, access=access)

    register_handler(RENAME_BATCH, handle, name="Renaming files", family=Family.OTHER)


async def rename_batch(context: JobContext, *, renamer: BatchRenamer, access: Repository) -> None:
    """Carry out a batch too large, or too far away, to wait for. Planned again from the disk now.

    Stopping it half way (a pause or a cancel) keeps what it did and writes the receipt for exactly
    that: it is never run a second time, because the template would be filled again over names it
    already gave.
    """
    actor_id = context.require_str("actor_id", "this task needs the user that asked for it")
    template = context.require_str("template", "this task needs the template to fill")
    raw_ids = context.payload.get("asset_ids")
    asset_ids = (
        [one for one in raw_ids if isinstance(one, str)] if isinstance(raw_ids, list) else []
    )
    on_clash: OnClash = "skip" if context.payload.get("on_clash") == "skip" else "number"
    # The vault open, as the files were chosen with it: see `media_edit.jobs._actor`.
    actor = await access.load_viewer(actor_id, show_hidden=True)
    if actor is None:
        await context.set_note("Whoever asked for this no longer exists.")
        return
    planned = await renamer.plan(asset_ids, template, on_clash=on_clash, actor=actor)
    await context.set_units(planned.moving)

    async def progress(done: int, of: int) -> bool:
        await context.report_progress(done / of)
        return context.stopping() is None

    applied = await renamer.carry_out(planned, template, actor=actor, progress=progress)
    await context.set_note(_note(applied))


def _note(applied: Applied) -> str:
    files = "file" if applied.renamed == 1 else "files"
    said = f"Renamed {applied.renamed:,} {files}."
    if applied.skipped:
        said += f" {applied.skipped:,} kept their names."
    return said


__all__ = [
    "MOST_FILES",
    "MOST_WHILE_WAITING",
    "PREVIEW_ROWS",
    "QUEUE",
    "RENAME_BATCH",
    "WORDS",
    "Applied",
    "BatchRenameReceipts",
    "BatchRenamer",
    "OnClash",
    "Plan",
    "Planned",
    "State",
    "library_facts",
    "plan",
    "register_handlers",
]
