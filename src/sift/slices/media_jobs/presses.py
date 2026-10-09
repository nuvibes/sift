# SPDX-License-Identifier: AGPL-3.0-or-later
"""The runs over some files that Activity's Library tasks describe, beside the library's work.

All presses still going in a family are one run, read from the queue so a restart reads the same run.
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
    """One family's runs over some files (Run task) or some folders, beside the library's work."""

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
        """Whether only presses are going; a library pass held for quiet hours is not going."""
        return self.going and self.library <= self.held


def families_of(line: LiveWork | PressedWork, carriers: Collection[str]) -> dict[Family, list[str]]:
    """Which families a row is work of, each with the lines it is drawn on there."""
    if line.type in carriers:
        found: dict[Family, list[str]] = {}
        for key in line.products:
            if key in PRODUCT_FAMILIES:
                found.setdefault(PRODUCT_FAMILIES[key], []).append(PRODUCT_TYPES.get(key, key))
        return found
    return {registered_families()[line.type]: [line.type]}


def pass_types() -> list[str]:
    """Every job type of the long passes, in order: what the reads here are about."""
    return sorted(
        job_type for job_type, family in registered_families().items() if family in LONG_PASSES
    )


async def read_presses(
    queue: JobQueue, counted: Collection[str]
) -> tuple[dict[Family, Presses], list[LiveWork]]:
    """The live presses and the library's live work per long pass, read together for Activity."""
    carriers = registered_product_carriers()
    types = pass_types()
    lines = await queue.live_by_press(types)
    answer, began = _tally_live(lines, carriers, counted)
    if any(press.folders for press in answer.values()):
        await _weigh_folders(queue, types, answer)
    await _tally_pressed(queue, types, carriers, answer, began)
    return answer, lines


def _tally_live(
    lines: list[LiveWork], carriers: Collection[str], counted: Collection[str]
) -> tuple[dict[Family, Presses], dict[Family, int]]:
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
    return answer, began


async def _weigh_folders(queue: JobQueue, types: list[str], answer: dict[Family, Presses]) -> None:
    # A run that carries no count per product predates the count and is drawn as library work.
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


async def _tally_pressed(
    queue: JobQueue,
    types: list[str],
    carriers: Collection[str],
    answer: dict[Family, Presses],
    began: dict[Family, int],
) -> None:
    for family, since in sorted(began.items()):
        press = answer[family]
        # Every long-pass type: a run's file tasks may be done while its page still goes.
        for row in await queue.pressed_since(types, since):
            for part in families_of(row, carriers).get(family, []):
                if row.folders:
                    # A folder run's remainder is its weight less what it made, so only DONE counts.
                    if press.folders and row.state is JobState.DONE:
                        press.folders_done[part] = press.folders_done.get(part, 0) + row.count
                    continue
                for share in [press.parts] + ([press.again_parts] if row.again else []):
                    tally = share.setdefault(part, [0, 0])
                    tally[1] += row.count
                    if row.state is JobState.DONE:
                        tally[0] += row.count
