# SPDX-License-Identifier: AGPL-3.0-or-later
"""The dry runs of the tasks whose run decides and writes in one pass.

Each feature splits its run into a plan (what it would do, worked out and written nowhere) and the
run that carries that plan out, so a dry run and a real run cannot come to different answers. This
turns each plan into what the task's row says. Here because it names files and folders for the
person who pressed, which only the access layer can decide, and pairs each feature's plan with the
task another declaration names.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import PurePosixPath

from fastapi import FastAPI

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.slices import (
    backup,
    dedup,
    library_roots,
    music,
    settings_hub,
    shoots,
    stash_boxes,
    suggestions,
    tasks,
)
from sift.slices.library_roots.scan_plan import PlannedRead, plan_scan
from sift.slices.stash_boxes.jobs import sweep_page
from sift.slices.tasks.parts import FIRST_FILES
from sift.wiring.built import Storage

#: How many things a dry run names, before it says how many more there are.
NAMED = 10

#: What a dry run says for a feature that is not running in this process.
NOT_HERE = "This feature isn't running here."


def _counted(count: int, one: str, many: str) -> str:
    return f"1 {one}" if count == 1 else f"{count:,} {many}"


def _listed(names: Sequence[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


class _DryRuns:
    """The dry runs that decide and write in one pass, each feature read when its run runs."""

    def __init__(self, app: FastAPI, store: Storage, hub: settings_hub.SettingsService) -> None:
        self._app = app
        self._store = store
        self._hub = hub

    async def files_named(self, viewer: Viewer, asset_ids: Sequence[str]) -> tuple[str, ...]:
        """The first files of these, in order, by name. Files this viewer cannot see are passed
        over, never named."""
        names: list[str] = []
        wanted = list(dict.fromkeys(asset_ids))
        for start in range(0, len(wanted), NAMED * 5):
            batch = wanted[start : start + NAMED * 5]
            seen = await self._store.access.visible_of(viewer, batch)
            places = await self._store.content.locations_of([one for one in batch if one in seen])
            for asset_id in batch:
                if asset_id in places:
                    names.append(places[asset_id][0].filename)
                    if len(names) >= NAMED:
                        return tuple(names)
        return tuple(names)

    async def reads_named(
        self, viewer: Viewer, root_id: str, reads: Sequence[PlannedRead]
    ) -> list[str]:
        """The names of the files a walk would read that this viewer may see. A file under a
        folder they cannot see, or already recorded as a file they cannot see, is never named."""
        holders: dict[str, str] = {}
        for one in dict.fromkeys(read.folder for read in reads):
            row = await self._store.library.folder_at(root_id, one)
            if row is not None:
                holders[one] = row.id
        folders = await self._store.access.visible_folders_of(viewer, list(holders.values()))
        recorded: dict[str, str] = {}
        for read in reads:
            location = await self._store.content.location_at(root_id, read.recorded_at)
            if location is not None:
                recorded[read.recorded_at] = location.asset_id
        seen = await self._store.access.visible_of(viewer, list(recorded.values()))
        names: list[str] = []
        for read in reads:
            folder = folders.get(holders.get(read.folder, ""))
            if folder is None or folder.concealed:
                continue
            asset_id = recorded.get(read.recorded_at)
            if asset_id is not None and asset_id not in seen:
                continue
            names.append(PurePosixPath(read.rel_path).name)
        return names

    async def scan(self, only: tasks.Selection, viewer: Viewer) -> tasks.Plan:
        # A walk of each library folder asked for, planned by the steps the walk takes
        # (`plan_scan`): what it would move, read and mark missing, with nothing written.
        service = wiring.part_of_app_or_none(self._app, library_roots.SERVICE)
        if service is None:
            return tasks.Plan(files=0, refusals=(NOT_HERE,))
        roots = [
            one
            for one in await self._store.library.roots()
            if only.locations is None or one.id in only.locations
        ]
        refusals: list[str] = []
        moved = new = changed = returned = archives = missing = 0
        reading: list[str] = []
        going: list[str] = []
        for root in roots:
            planned = await plan_scan(self._store, service, root, first=NAMED * 5)
            if not planned.answered:
                refusals.append(f"{root.name} didn't answer, so nothing in it was planned.")
                continue
            moved += 0 if planned.folders is None else len(planned.folders.moves.pairs)
            new += planned.new
            changed += planned.changed
            returned += planned.returned
            archives += planned.archives
            missing += planned.missing
            if len(reading) < NAMED:
                reading += await self.reads_named(viewer, root.id, planned.reading)
            going += planned.going
        doing = [
            one
            for count, one in (
                (moved, f"move {_counted(moved, 'folder', 'folders')}"),
                (new, f"take in {_counted(new, 'new file', 'new files')}"),
                (changed, f"read {_counted(changed, 'changed file', 'changed files')} again"),
                (returned, f"find {_counted(returned, 'file', 'files')} back where they were"),
                (archives, f"look inside {_counted(archives, 'archive', 'archives')}"),
                (missing, f"mark {_counted(missing, 'file', 'files')} missing"),
            )
            if count
        ]
        lines = [
            tasks.PlanLine("Folders moved", moved),
            tasks.PlanLine("New files", new),
            tasks.PlanLine("Changed files", changed),
            tasks.PlanLine("Files marked missing", missing),
        ]
        if returned:
            lines.insert(3, tasks.PlanLine("Files back", returned))
        if archives:
            lines.insert(-1, tasks.PlanLine("Archives to look inside", archives))
        # The files it would read, or where it reads none, the files it would mark missing.
        to_read = new + changed + archives
        names = tuple(reading[:NAMED]) if to_read else await self.files_named(viewer, going)
        return tasks.Plan(
            files=moved + to_read + returned + missing,
            lines=tuple(lines),
            names=names,
            named=FIRST_FILES if to_read else "First files it would mark missing",
            nameable=to_read if to_read else missing,
            refusals=tuple(refusals),
            doing=f"would {_listed(doing)}",
            idle="has nothing to do. Every file is where it was when it was last scanned.",
        )

    async def duplicates(self, _only: tasks.Selection, viewer: Viewer) -> tasks.Plan:
        # Each feature is read when its dry run runs rather than when the tasks are built, and one
        # that is not running here answers None.
        service = wiring.part_of_app_or_none(self._app, dedup.SERVICE)
        if service is None:
            return tasks.Plan(files=0, refusals=(NOT_HERE,))
        planned = await service.plan()
        pairs = len(planned.pairs)
        involved = [one for pair in planned.pairs for one in (pair.asset_a, pair.asset_b)]
        return tasks.Plan(
            files=pairs,
            lines=(
                tasks.PlanLine("Files compared", planned.compared),
                tasks.PlanLine("New pairs to review", pairs),
            ),
            names=await self.files_named(viewer, involved) if pairs else (),
            nameable=len(set(involved)),
            doing=(
                f"would add {_counted(pairs, 'new pair', 'new pairs')} of near duplicates to "
                f"review, from {_counted(planned.compared, 'file', 'files')} compared"
            ),
            idle="has nothing new to add. Every near duplicate it finds is already there to review.",
        )

    async def folder_suggestions(self, _only: tasks.Selection, viewer: Viewer) -> tasks.Plan:
        service = wiring.part_of_app_or_none(self._app, suggestions.SERVICE)
        if service is None:
            return tasks.Plan(files=0, refusals=(NOT_HERE,))
        planned = await service.plan()
        moved = len(planned.moved)
        by_names = (
            ", and read the names of new files that have no Site yet, to file them"
            if planned.by_names
            else ""
        )
        visible = await self._store.access.visible_folders_of(
            viewer, [one.id for one in planned.moved[: NAMED * 5]]
        )
        names = tuple(
            visible[one.id].name
            for one in planned.moved[: NAMED * 5]
            if one.id in visible and not visible[one.id].concealed
        )[:NAMED]
        return tasks.Plan(
            files=moved,
            lines=(
                tasks.PlanLine("Folders that changed", moved),
                tasks.PlanLine("Folders in all", len(planned.folders)),
            ),
            names=names,
            named="First folders",
            nameable=moved,
            doing=(
                f"would read {_counted(moved, 'folder', 'folders')} that changed since it last "
                f"looked{by_names}"
            ),
            idle=(
                "has no changed folders to read. It would still read the names of new files that "
                "have no Site yet, to file them."
                if planned.by_names
                else "has nothing to do. No folder has changed since it last looked."
            ),
        )

    async def find_shoots(self, _only: tasks.Selection, viewer: Viewer) -> tasks.Plan:
        service = wiring.part_of_app_or_none(self._app, shoots.SERVICE)
        if service is None:
            return tasks.Plan(files=0, refusals=(NOT_HERE,))
        planned = await service.plan()
        if not planned.can_compare:
            return tasks.Plan(
                files=0,
                refusals=(
                    "Nothing has been described for Smart Search yet, so there is nothing to "
                    "compare.",
                ),
            )
        found = planned.shoots
        people = len(planned.found)
        pictures = [
            one for creator in planned.found for group in creator.groups for one in group.asset_ids
        ]
        made = (
            f"would make {_counted(found, 'Photo Set', 'Photo Sets')} from the shoots it finds"
            if planned.automatic
            else f"would suggest {_counted(found, 'shoot', 'shoots')}"
        )
        return tasks.Plan(
            files=found,
            lines=(
                tasks.PlanLine("People with loose pictures", planned.creators),
                tasks.PlanLine("Shoots found", found),
            ),
            names=await self.files_named(viewer, pictures) if found else (),
            nameable=len(set(pictures)),
            doing=f"{made}, of {_counted(people, 'person', 'people')}",
            idle="has nothing to do. It found no new shoots.",
        )

    async def enrichment(self, _only: tasks.Selection, viewer: Viewer) -> tasks.Plan:
        # The press's own checks, in its order (see `build_tasks`' `enrich`), then the pages its
        # sweep would walk. Asking is the work, so this says who would be asked about what and
        # asks nobody.
        if not await self._hub.get_app(stash_boxes.SCAN_KEY):
            return tasks.Plan(files=0, refusals=(stash_boxes.ENRICHING_OFF,))
        service = wiring.part_of_app_or_none(self._app, stash_boxes.SERVICE)
        if service is None:
            return tasks.Plan(files=0, refusals=(NOT_HERE,))
        box = await stash_boxes.box_for(self._hub, None)
        nobody = await service.cannot_ask(box)
        if nobody is not None:
            return tasks.Plan(files=0, refusals=(nobody,))
        asking: list[str] = []
        count = 0
        offset = 0
        while True:
            page = await sweep_page(
                self._store.access, service, viewer, offset=offset, folder=None, only=box or None
            )
            count += len(page.asking)
            if len(asking) < NAMED * 5:
                asking.extend(page.asking[: NAMED * 5 - len(asking)])
            offset += page.walked
            if page.walked == 0 or offset >= page.total:
                break
        boxes = await service.names_asked(box)
        return tasks.Plan(
            files=count,
            lines=(
                tasks.PlanLine("Files to ask about", count),
                tasks.PlanLine("Stash-boxes to ask", len(boxes)),
            ),
            names=await self.files_named(viewer, asking) if count else (),
            nameable=count,
            doing=f"would ask {_listed(boxes)} about {_counted(count, 'file', 'files')}",
            idle="has nothing to do. Every file has already been asked about.",
        )

    async def song_names(self, _only: tasks.Selection, viewer: Viewer) -> tasks.Plan:
        # The press's own check, then the walk its run takes (`LookupStarter.plan`), with nothing
        # queued: asking is the work, so this counts who would be asked about and sends nothing.
        lookups = wiring.part_of_app_or_none(self._app, music.LOOKUP_STARTER)
        if lookups is None:
            return tasks.Plan(files=0, refusals=(NOT_HERE,))
        refused = await lookups.cannot_run()
        if refused is not None:
            return tasks.Plan(files=0, refusals=(refused,))
        planned = await lookups.plan(first=NAMED * 5)
        return tasks.Plan(
            files=planned.files,
            lines=(
                tasks.PlanLine("Files to ask about", planned.files),
                # Said beside it, and never asked again: AcoustID answered these already.
                tasks.PlanLine("Files AcoustID did not know", await lookups.not_known()),
            ),
            names=await self.files_named(viewer, planned.first) if planned.files else (),
            nameable=planned.files,
            doing=(
                f"would ask AcoustID about {_counted(planned.files, 'file', 'files')}, sending "
                "each one's music fingerprint and length"
            ),
            idle="has nothing to do. Every file with a music fingerprint has a song or an answer.",
        )

    async def automatic_backup(self, _only: tasks.Selection, _viewer: Viewer) -> tasks.Plan:
        service = wiring.part_of_app_or_none(self._app, backup.SERVICE)
        if service is None:
            return tasks.Plan(files=0, refusals=(NOT_HERE,))
        try:
            planned = await service.plan()
        except backup.DestinationRefused as refused:
            return tasks.Plan(files=0, refusals=(str(refused),))
        dropped = len(planned.drop)
        deleting = (
            f" and delete {_counted(dropped, 'older backup', 'older backups')}" if dropped else ""
        )
        return tasks.Plan(
            files=1,
            lines=(
                tasks.PlanLine("New backup", 1),
                tasks.PlanLine("Older backups deleted", dropped),
            ),
            names=tuple(one.name for one in planned.drop[:NAMED]),
            named="Older backups it would delete",
            nameable=dropped,
            doing=f"would write {planned.file.name}{deleting}",
        )


def dry_run_planners(
    app: FastAPI, store: Storage, hub: settings_hub.SettingsService
) -> dict[str, tasks.Planner]:
    """The planners of Scan, Find duplicate files, Suggest People, Find shoots, the stash-box
    lookups, naming songs with AcoustID and the automatic backup, by task id."""
    runs = _DryRuns(app, store, hub)
    return {
        "scan": runs.scan,
        "duplicates": runs.duplicates,
        "suggestions": runs.folder_suggestions,
        "shoots": runs.find_shoots,
        "enrichment": runs.enrichment,
        music.LOOKUP_TASK: runs.song_names,
        "backup": runs.automatic_backup,
    }
