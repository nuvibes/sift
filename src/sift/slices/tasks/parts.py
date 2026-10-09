# SPDX-License-Identifier: AGPL-3.0-or-later
"""What part of a task can be run on its own, and what a dry run of it says it would do."""

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
    path: str = ""


@dataclass(frozen=True, slots=True)
class TaskParts:
    """What a task can be run in part over. A task with neither runs only whole."""

    subtasks: tuple[TaskPart, ...] = ()
    locations: bool = False


@dataclass(frozen=True, slots=True)
class Selection:
    """Which part of a task a press asked for. None for either means all of it."""

    parts: tuple[str, ...] | None = None
    locations: tuple[str, ...] | None = None

    @property
    def whole(self) -> bool:
        return self.parts is None and self.locations is None


EVERYTHING = Selection()


def narrowed(
    declared: TaskParts,
    parts: Sequence[str] | None,
    locations: Sequence[str] | None,
    folders: Sequence[str],
) -> Selection:
    """A press's parts, checked against the declaration; an empty list is refused, never all."""
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


FIRST_FILES = "First files"

NOTHING_TO_MAKE = "has nothing to do. Every file already has what it would make."

#: Names are shortened in the middle, keeping start and extension, to fit the job's note.
NAME_CHARS = 96

#: Under the note's own bound, so it is never cut through.
NOTE_CHARS = 1800


@dataclass(frozen=True, slots=True)
class DryReport:
    """What a dry run said, as fields for the task's row and as a sentence for History."""

    said: str
    headline: str = ""
    lines: tuple[PlanLine, ...] = ()
    named: str = FIRST_FILES
    names: tuple[str, ...] = ()
    more: int = 0
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

    #: Each counted once however many parts want it.
    files: int
    lines: tuple[PlanLine, ...] = ()
    #: Files the person could not see are counted and never named.
    names: tuple[str, ...] = ()
    refusals: tuple[str, ...] = ()
    doing: str | None = None
    named: str = FIRST_FILES
    nameable: int | None = None
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


#: The same step the run starts from, so a dry run and a real one cannot disagree.
Planner = Callable[[Selection, Viewer], Awaitable[Plan]]
