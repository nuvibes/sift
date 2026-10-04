# SPDX-License-Identifier: AGPL-3.0-or-later
"""What part of a task can be run on its own, and what a dry run of it says it would do.

A task's parts are declared once, by the composition root (which is the only place that knows
which products a task is made of): its sub-tasks, and whether it can be run over some of the
library folders. The Tasks screen draws its menu from that declaration and the run route checks a
press against the same one, so the menu can never offer a part the server would refuse.

A dry run is a pass that reads and writes nothing, asked of the same planning step the real run
starts from: the counts it reports are the counts the run would be weighed by, and the files it
names are the first the run would hand out.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from sift.kernel.access import Viewer


class NotAPart(ValueError):
    """A press named a part or a folder the task does not have. The message is meant to be read."""


@dataclass(frozen=True, slots=True)
class TaskPart:
    """One sub-task: its address in a press, and what it is called on screen."""

    key: str
    label: str
    #: Where a library folder is on this device, for the menu to draw beside its name; empty for
    #: a sub-task.
    path: str = ""


@dataclass(frozen=True, slots=True)
class TaskParts:
    """What a task can be run in part over. A task with neither runs only whole."""

    subtasks: tuple[TaskPart, ...] = ()
    #: Whether a press may name some of the library folders.
    locations: bool = False


@dataclass(frozen=True, slots=True)
class Selection:
    """Which part of a task a press asked for. None for either means all of it."""

    parts: tuple[str, ...] | None = None
    locations: tuple[str, ...] | None = None

    @property
    def whole(self) -> bool:
        return self.parts is None and self.locations is None


#: A press on the lead half: the whole task.
EVERYTHING = Selection()


def narrowed(
    declared: TaskParts,
    parts: Sequence[str] | None,
    locations: Sequence[str] | None,
    folders: Sequence[str],
) -> Selection:
    """A press's parts, checked against the task's declaration and the folders there are now.

    A list that is given must name at least one thing, each of them the task's own. An empty list
    is refused rather than read as everything: somebody who unticked every row did not ask for
    the whole task.
    """
    chosen_parts: tuple[str, ...] | None = None
    if parts is not None:
        known = {one.key for one in declared.subtasks}
        if not known:
            raise NotAPart("This task can't be run in parts.")
        chosen_parts = tuple(dict.fromkeys(parts))
        if not chosen_parts:
            raise NotAPart("Choose at least one part to run.")
        unknown = [one for one in chosen_parts if one not in known]
        if unknown:
            raise NotAPart(f"This task has no part called {unknown[0]}.")
    chosen_folders: tuple[str, ...] | None = None
    if locations is not None:
        if not declared.locations:
            raise NotAPart("This task can't be run for some folders only.")
        chosen_folders = tuple(dict.fromkeys(locations))
        if not chosen_folders:
            raise NotAPart("Choose at least one folder to run it for.")
        gone = [one for one in chosen_folders if one not in set(folders)]
        if gone:
            raise NotAPart("One of those folders is no longer in your library.")
    return Selection(parts=chosen_parts, locations=chosen_folders)


@dataclass(frozen=True, slots=True)
class PlanLine:
    """One part of a plan: what it is called and how many it would do."""

    label: str
    count: int


#: What a plan's names are, as its report heads them, where they are the first files it would do.
FIRST_FILES = "First files"

#: What a Build with nothing to do says after the task's name.
NOTHING_TO_MAKE = "has nothing to do. Every file already has what it would make."

#: The longest a name in a report may be. A report is stored with its job, whose note is bounded, so
#: a name is shortened in the middle and keeps its start and its extension.
NAME_CHARS = 96

#: The longest a report may be as a note. Under the note's own bound, so it is never cut through.
NOTE_CHARS = 1800


@dataclass(frozen=True, slots=True)
class DryReport:
    """What a dry run said, as fields the task's row lays out and as the sentence History reads.

    Stored as the dry run job's note, in JSON (`note`), because the note is the one thing a job
    leaves behind for whoever asked, and the row reads it back from there."""

    said: str
    #: What it would do in one sentence, the report's first: "Backup would write one.zip".
    headline: str = ""
    lines: tuple[PlanLine, ...] = ()
    #: What `names` are: the first files, or the old backups it would delete.
    named: str = FIRST_FILES
    names: tuple[str, ...] = ()
    #: How many more there are than the names shown.
    more: int = 0
    #: Why a part could not run on this device now, each in its own sentence.
    refusals: tuple[str, ...] = ()

    def note(self) -> str:
        """The report as the job's note."""
        return json.dumps(
            {
                "dry": 1,
                "said": self.said,
                "head": self.headline,
                "parts": [[one.label, one.count] for one in self.lines],
                "named": self.named,
                "names": list(self.names),
                "more": self.more,
                "cannot": list(self.refusals),
            },
            ensure_ascii=False,
        )

    @staticmethod
    def of_note(note: str | None) -> DryReport | None:
        """A report read back from a note; None for a note that is a plain sentence."""
        if not note or not note.startswith("{"):
            return None
        try:
            held = json.loads(note)
        except ValueError:
            return None
        if not isinstance(held, dict) or not isinstance(held.get("said"), str):
            return None
        parts = held.get("parts")
        names = held.get("names")
        cannot = held.get("cannot")
        more = held.get("more")
        return DryReport(
            said=held["said"],
            headline=str(held.get("head") or ""),
            lines=tuple(
                PlanLine(str(one[0]), int(one[1]))
                for one in (parts if isinstance(parts, list) else [])
                if isinstance(one, list) and len(one) == 2 and isinstance(one[1], int)
            ),
            named=str(held.get("named") or FIRST_FILES),
            names=tuple(str(one) for one in (names if isinstance(names, list) else [])),
            more=more if isinstance(more, int) and more > 0 else 0,
            refusals=tuple(str(one) for one in (cannot if isinstance(cannot, list) else [])),
        )


@dataclass(frozen=True, slots=True)
class Plan:
    """What a run would do, worked out and not done."""

    #: How many things the run would work on, each counted once however many parts want it. Zero
    #: is a run with nothing to do.
    files: int
    #: The same, part by part, in the order the task declares them.
    lines: tuple[PlanLine, ...] = ()
    #: The first things it would do, by name. Files the person could not see are counted and never
    #: named.
    names: tuple[str, ...] = ()
    #: Why a part could not run on this device now, in its own sentence; empty when all can.
    refusals: tuple[str, ...] = ()
    #: What it would do, after the task's name, where "would work on 12 files" is not it.
    doing: str | None = None
    #: What `names` are, as the report heads them.
    named: str = FIRST_FILES
    #: How many there are to name in all; `files` when that is what the names are drawn from.
    nameable: int | None = None
    #: What a plan with nothing to do says after the task's name.
    idle: str = NOTHING_TO_MAKE

    def report(self, title: str) -> str:
        """The dry run's answer in plain sentences."""
        return self.reported(title).said

    def reported(self, title: str) -> DryReport:
        """The dry run's answer as fields, with its sentence, bounded to fit a job's note."""
        names = tuple(_shortened(one) for one in self.names)
        while True:
            report = self._report(title, names)
            if len(report.note()) <= NOTE_CHARS or not names:
                return report
            names = names[:-1]

    def _report(self, title: str, names: tuple[str, ...]) -> DryReport:
        total = self.files if self.nameable is None else self.nameable
        more = max(0, total - len(names))
        if self.files == 0 and self.refusals:
            said = f"{title} would do nothing on this device now."
        elif self.files == 0:
            said = f"{title} {self.idle}"
        else:
            said = f"{title} {self.doing or f'would work on {_files(self.files)}'}."
        headline = said
        if self.files:
            parts = [f"{one.label} {one.count:,}" for one in self.lines if one.count]
            # A plan in its own words says its numbers there; the parts of a Build are said here.
            if self.doing is None and len(self.lines) > 1 and parts:
                said += f" By part: {', '.join(parts)}."
            if names:
                tail = f", and {more:,} more" if more > 0 else ""
                said += f" {self.named}: {', '.join(names)}{tail}."
        for one in self.refusals:
            said += f" {one}"
        return DryReport(
            said=said + " Nothing was changed.",
            headline=headline,
            lines=self.lines if self.files else (),
            named=self.named,
            names=names if self.files else (),
            more=more if self.files and names else 0,
            refusals=self.refusals,
        )


def _files(count: int) -> str:
    return "1 file" if count == 1 else f"{count:,} files"


def _shortened(name: str) -> str:
    """A name no longer than `NAME_CHARS`, cut in the middle so its start and extension stay."""
    if len(name) <= NAME_CHARS:
        return name
    keep = NAME_CHARS - 3
    return name[: keep - keep // 3] + "..." + name[-(keep // 3) :]


#: Works out what a task's run would do for a selection, as the viewer who pressed. The same step
#: the run itself starts from, so a dry run and a real one cannot come to different answers.
Planner = Callable[[Selection, Viewer], Awaitable[Plan]]
