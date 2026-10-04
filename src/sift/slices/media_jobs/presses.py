# SPDX-License-Identifier: AGPL-3.0-or-later
"""The runs over some files, as Activity's Library tasks describe them.

Run task on a file, a selection or a folder's files queues the chosen passes for THOSE files, as
top rows named for the presser (`POST /assets/run`). A family's row on Activity that read the
library's figures over them would say, for forty files pressed beside nothing else, how far the
whole library had got, and price the library's owed files at the ledger's pace as their time
left: a quarter of an hour for a minute's work. What is here is the figures of those presses,
whole, and how much of each family's live work is the library's, read in one go so the two are
never weighed against each other from two different moments.

A TASK'S RUN NOW OVER SOME LIBRARY FOLDERS IS THE SAME CASE FROM ANOTHER DOOR: its row would
read the library's figures while it walked one folder. It is described by its folders' files, by
the count the task's dry run over them states, and what it has made since it began.

ALL PRESSES STILL GOING IN A FAMILY ARE ONE RUN. A second press made while the first is going
joins it, and the run ends when the last of them does; the next press starts a new one. Read from
the queue rather than remembered here, so a restart in the middle of a run reads the same run.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field

from sift.kernel.jobs import (
    JobQueue,
    JobState,
    registered_families,
    registered_product_carriers,
)
from sift.kernel.jobs.families import LONG_PASSES, PRODUCT_FAMILIES, PRODUCT_TYPES, Family
from sift.kernel.jobs.queue import LiveWork, PressedWork


@dataclass(slots=True)
class Presses:
    """What the runs over some files have in one family, beside the library's work: a press for
    some files (Run task), and a run over some library folders (a task's Run now with folders
    ticked)."""

    live: int = 0
    """Pressed rows still going: queued, running or held."""
    again_live: int = 0
    """Of those, the rows that make their work again where the file has it (`AGAIN`). No count of
    the library holds them, so beside a pass over the library they are added to it; a pressed row
    for a file that lacks the work is one of the library's owed files already."""
    parts: dict[str, list[int]] = field(default_factory=dict)
    """By the job type the work is drawn as: [done, total] of the presses still going. A failed
    row is in the total and not done; a cancelled one is in neither, as work somebody called off."""
    again_parts: dict[str, list[int]] = field(default_factory=dict)
    """The same, for the rows that make their work again."""
    folders: int = 0
    """Live rows of runs over some library folders: their pages and their files' tasks."""
    folders_done: dict[str, int] = field(default_factory=dict)
    """By the line it is drawn on: files those runs have made it for since the oldest began."""
    folders_total: dict[str, int] = field(default_factory=dict)
    """By the line it is drawn on: how many of those runs' folders' files lacked it when each was
    pressed, as the run carries it (`each`, written by `importing.start_runs` from the count the
    task's dry run over the same folders states). Fixed for the run, so its bar reads done of
    total over the folders however far its pages have walked."""

    @property
    def folders_left(self) -> int:
        """What the runs over some folders still have to make, every line together."""
        return sum(
            max(0, total - self.folders_done.get(job_type, 0))
            for job_type, total in self.folders_total.items()
        )

    library: int = 0
    """Live rows of this family that are the library's: a pass over it (a Build's pages and tasks)
    or work a count of the library answers for (a file arriving). A follow-on chore nothing counts
    (faces grouped again after a scan) is neither, and is left out: it is not a pass anybody
    reads a bar for."""
    held: int = 0
    """Of the live rows, how many are held for quiet hours: queued, and not going to run until the
    range opens. Set by the reader that knows (`_families`)."""

    @property
    def going(self) -> bool:
        """Whether any run over some files is going in this family."""
        return self.live > 0 or self.folders > 0

    @property
    def alone(self) -> bool:
        """Whether what this family is doing now is runs over some files and nothing of the
        library's: then the row is those runs' own figures. A pass over the library that is
        WAITING (every row of it held for quiet hours) is not doing anything now, and its files
        are not what a person reading the time left beside forty pressed files is asking about;
        once the range opens it is running, and the row is the library's again."""
        return self.going and self.library <= self.held


def families_of(line: LiveWork | PressedWork, carriers: Collection[str]) -> dict[Family, list[str]]:
    """Which families a row is work of, each with the lines it is drawn on there.

    A coordinator's task is the work of each product it names, on that product's own line
    (`PRODUCT_TYPES`): a task making previews and strips is on both of Generate's lines. Any other
    row is its own type's, in the family it is registered under.
    """
    if line.type in carriers:
        found: dict[Family, list[str]] = {}
        for key in line.products:
            if key in PRODUCT_FAMILIES:
                found.setdefault(PRODUCT_FAMILIES[key], []).append(PRODUCT_TYPES.get(key, key))
        return found
    # Every type read is one the registry put in a family: see `read_presses`.
    return {registered_families()[line.type]: [line.type]}


def pass_types() -> list[str]:
    """Every job type of the long passes, in order: what the reads here are about."""
    return sorted(
        job_type for job_type, family in registered_families().items() if family in LONG_PASSES
    )


async def read_presses(
    queue: JobQueue, counted: Collection[str]
) -> tuple[dict[Family, Presses], list[LiveWork]]:
    """The runs over some files still going and the library's live work, per long pass, and the
    live rows themselves, grouped: the one read of them Activity makes.

    Two reads: the live rows, split by whether they are a press's, a run over some folders' or the
    library's (`JobQueue.live_by_press`), and for each family with such a run going, every file's
    row of its types since the oldest of them began (`JobQueue.pressed_since`), which is what makes
    the bar done of total. `counted` is the job types a count of the library answers for
    (`WorkAhead`).
    """
    carriers = registered_product_carriers()
    types = pass_types()
    lines = await queue.live_by_press(types)
    answer: dict[Family, Presses] = {}
    began: dict[Family, int] = {}
    for line in lines:
        for family in families_of(line, carriers):
            press = answer.setdefault(family, Presses())
            if line.pressed:
                press.live += line.count
                press.again_live += line.count if line.again else 0
            elif line.folders:
                press.folders += line.count
            elif line.type in carriers or line.type in counted:
                press.library += line.count
                continue
            else:
                continue
            began[family] = min(began.get(family, line.since), line.since)
    # WHAT EACH RUN OVER SOME FOLDERS WAS WEIGHED BY, from its first page, which carries it for as
    # long as any of the run goes. A run that carries no count per product (one asked for before it
    # was written) is drawn as the library's work, which is what it was drawn as then.
    if any(press.folders for press in answer.values()):
        for payload in await queue.live_tops(types):
            each = payload.get("each")
            if not isinstance(payload.get("roots"), list) or not isinstance(each, dict):
                continue
            for key, count in each.items():
                whose = PRODUCT_FAMILIES.get(str(key))
                if whose is None or whose not in answer or not isinstance(count, int):
                    continue
                part = PRODUCT_TYPES.get(str(key), str(key))
                totals = answer[whose].folders_total
                totals[part] = totals.get(part, 0) + count
        for press in answer.values():
            if press.folders and not press.folders_total:
                press.library += press.folders
                press.folders = 0
    for family, since in sorted(began.items()):
        press = answer[family]
        # Every type of the long passes, not only the ones with a row still live: a run's file
        # tasks may all be finished while its page or a last step still goes.
        for row in await queue.pressed_since(types, since):
            for part in families_of(row, carriers).get(family, []):
                if row.folders:
                    # What is left of a run over folders is what it was weighed by less what it
                    # has made, so only what it has made is read here.
                    if press.folders and row.state is JobState.DONE:
                        press.folders_done[part] = press.folders_done.get(part, 0) + row.count
                    continue
                for share in [press.parts] + ([press.again_parts] if row.again else []):
                    tally = share.setdefault(part, [0, 0])
                    tally[1] += row.count
                    if row.state is JobState.DONE:
                        tally[0] += row.count
    return answer, lines
