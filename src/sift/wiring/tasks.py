# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every task's When, quiet hours, the one scheduler, and Run now."""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial

from fastapi import FastAPI

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.jobs import DEFAULT_PRIORITY, WAITED_ON_PRIORITY, JobQueue
from sift.kernel.jobs.clock import TaskClock
from sift.kernel.jobs.clock import install as install_task_clock
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.quiet_hours import AT_NOW, WHEN_PRESS
from sift.kernel.jobs.schedules import registered_schedules, retire_into_whens, when_key
from sift.kernel.jobs.switchboard import Switch
from sift.kernel.wiring import provide
from sift.slices import (
    dedup,
    faces,
    importing,
    library_roots,
    media_jobs,
    music,
    performance,
    semantic,
    settings_hub,
    stash_boxes,
    suggestions,
    tasks,
    update_notify,
    watermarks,
)
from sift.slices.importing.products import PAGE, count_lacking, lacking_on_page
from sift.wiring.built import Storage
from sift.wiring.dry_runs import dry_run_planners

#: How many files a dry run names, before it says how many more there are.
NAMED = 10

#: What History calls a dry run's subject.
DRY_RUN_TITLE = "Dry run"


#: What a folder's answer to a retired "as files arrive" key leaves out: a task run by hand.
_BY_HAND = "Running the task by hand still covers every folder."


def retire_the_switches_into_whens() -> None:
    """Every "does this start on its own" switch, answered by its task's When from now on.

    HERE, because each pairs a key one feature declared with a task another feature declared, and
    this is the only place that may name both. Read through the When as "anything but Only when I
    press it"; written into it, off as press-only and on as "as soon as there is work" where it was
    press-only. Every caller and every folder's own answer stored under an old key goes on reading
    the one stored value: see `settings_registry.retire_setting`. The settings migration carried
    each stored value across (settings component v9).
    """
    retire_into_whens(importing.SCAN_KEY, ("scan",), why="Scan's When")
    # Each key a folder may answer for itself says, on the folder's page, what that answer does:
    # whether a file ARRIVING in the folder is worked on. A task run by hand reads every folder.
    retire_into_whens(
        importing.GENERATE_KEY,
        ("generate",),
        why="Generate's When",
        folder_label="Generate as files are imported",
        folder_help=f"Generate works on each file as it's imported from this folder. {_BY_HAND}",
    )
    retire_into_whens(
        importing.IDENTIFY_KEY,
        ("faces", "smart-search", "watermarks"),
        why="the Identify group's master, over the three tasks it held",
        folder_label="Identify as files are imported",
        folder_help=(
            "Faces, Smart Search and watermarks are read for each file as it's imported from "
            f"this folder. {_BY_HAND}"
        ),
    )
    retire_into_whens(
        performance.SCAN_FACES_ON_IMPORT_KEY,
        ("faces",),
        why="recognition's When",
        folder_label="Identify faces as files are imported",
        folder_help=_BY_HAND,
    )
    retire_into_whens(
        semantic.DESCRIBE_ON_IMPORT_KEY,
        ("smart-search",),
        why="Smart Search's When",
        folder_label="Describe files for Smart Search as they're imported",
        folder_help=_BY_HAND,
    )
    retire_into_whens(
        watermarks.READ_ON_IMPORT_KEY,
        ("watermarks",),
        why="the watermarks' When",
        folder_label="Read watermarks as files are imported",
        folder_help=_BY_HAND,
    )
    # Generate's arrival answer gates the music fingerprint too (`wiring.imports`, the gate map), so
    # the folder's music answer is only true where Generate runs for that folder as well.
    retire_into_whens(
        music.FINGERPRINT_KEY,
        ("music",),
        why="the music fingerprints' When",
        folder_label="Fingerprint music as files are imported",
        folder_help=(
            "Reads the file's sound once as it's imported, on this device, when Generate runs for "
            "this folder."
        ),
    )
    retire_into_whens(dedup.SCAN_KEY, ("duplicates",), why="the duplicate sweep's When")
    retire_into_whens(suggestions.SCAN_KEY, ("suggestions",), why="the folder suggestions' When")
    retire_into_whens(stash_boxes.ASK_NEW_FILES_KEY, ("enrichment",), why="the lookups' When")
    retire_into_whens(update_notify.ENABLED_KEY, ("update-check",), why="the update check's When")


#: THE ORDER THE TASKS SCREEN READS IN: the order the work happens to a file (found, pictured,
#: recognized, then the passes over the whole library), and then the timed upkeep. Here because
#: this is the one place that sees every task; `TasksService` refuses an order that leaves a
#: declared task out or names one nobody declared.
TASK_ORDER: tuple[str, ...] = (
    "scan",
    "generate",
    "identify",
    "faces",
    "smart-search",
    "watermarks",
    "music",
    "music-lookup",
    "duplicates",
    "suggestions",
    "shoots",
    "enrichment",
    "backup",
    "quarantine-prune",
    "search-records-prune",
    "update-check",
)


#: The products of the three recognition tasks, which the Identify stage runs together: its press,
#: its parts, its dry run, and what its row on Tasks answers for.
IDENTIFY_PRODUCTS: tuple[str, ...] = (faces.PRODUCT, "meaning", "watermarks")


#: The rows that walk the library rather than being one file of it: a task counting files never
#: counts one (`ScheduledTask.unit`).
PASSES: tuple[str, ...] = (
    *(run for run, _ in importing.RUNS.values()),
    stash_boxes.STASH_SWEEP,
    music.MUSIC_LOOKUP_CATCH_UP,
)


def rows_products(task_products: Mapping[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    """Which products each task's row on Tasks answers for: its last run and its live work.

    Every task whose work is products answers for its own (`task_products`), and the Identify
    stage for the three recognition products together: a stopped Identify run is its last run, as
    each of theirs is, and so is its live work while it goes. Read by family instead, the stage
    would read the pace's record, which leaves a stopped run out: after a run stopped a minute
    ago it would still say when the run before it ended. Beside `task_products` and not in it:
    History names a run after the task whose products it made, and the stage is no product's task.
    """
    return {**task_products, "identify": IDENTIFY_PRODUCTS}


async def starts_on_its_own(hub: settings_hub.SettingsService, task_id: str) -> bool:
    """Whether a task starts its work on its own: its When is anything but Only when I press it."""
    return str(await hub.get_app(when_key(task_id))) != WHEN_PRESS


async def check_for_an_update_at_start(clock: TaskClock) -> None:
    """Begin a start with one update check, so the banner does not wait out the interval from the
    last check before it can say a release is out.

    The ONE waiting run is moved rather than a second queued beside it: the schedule's own run is
    already due together on a new library, or after a check older than the interval, and two rows
    due together are two checks. Placed as a check that never ran would be (`since=0`): immediately,
    or at the opening of quiet hours when that is its When.
    """
    await clock.ensure("update-check", since=0, move=True)


def _governs(queue: JobQueue, hub: settings_hub.SettingsService) -> dict[str, tuple[str, ...]]:
    # WHOSE WORK FOLLOWS EACH WHEN, beyond the task's own job type. Work nobody pressed of these
    # types is refused when the task only runs when pressed (through the import gates and the
    # switches `build_imports` declares) and held to quiet hours when that is its When (the claim).
    governs: dict[str, tuple[str, ...]] = {
        "scan": (library_roots.SCAN, library_roots.RECONCILE),
        "generate": (
            media_jobs.PREVIEW,
            media_jobs.SPRITE,
            media_jobs.FINGERPRINT_FOR_STASH_BOXES,
            media_jobs.FINGERPRINT_FILE,
            media_jobs.REMUX,
        ),
        "enrichment": (stash_boxes.STASH_SCAN,),
        # A file's lookup, queued where its pairing settles: held to quiet hours by the claim when
        # that is the lookup task's When, like a stash-box's question about a new file.
        music.LOOKUP_TASK: (music.MUSIC_LOOKUP,),
    }
    # AND REFUSED under Only when I press it, at the enqueue and again at the claim, as the shoots
    # pass is: the starter queues nothing then (`LookupStarter.consider`), and a lookup nobody
    # pressed that was already waiting is cancelled rather than sent. A press, and every lookup a
    # press of the task queues under itself, is never refused (`Switchboard.refusal`).
    queue.switchboard.declare(
        Switch(
            key=when_key(music.LOOKUP_TASK),
            refusal="Naming songs with AcoustID runs only when you run it, so nothing was sent.",
            on=partial(starts_on_its_own, hub, music.LOOKUP_TASK),
        ),
        music.MUSIC_LOOKUP,
    )
    return governs


class _Builds:
    """The press and the dry run of a task whose work is a Build over the library."""

    def __init__(
        self, products: importing.ProductRegistry, store: Storage, queue: JobQueue
    ) -> None:
        self._products = products
        self._store = store
        self._queue = queue

    @staticmethod
    def chosen(keys: tuple[str, ...], only: tasks.Selection) -> list[str]:
        """The products a press asked for: the ticked ones, or all of the task's."""
        return [key for key in keys if only.parts is None or key in only.parts]

    async def ready(self, keys: list[str]) -> tuple[list[str], list[str]]:
        """Which of these products can be made on this device now, and why each other one cannot.

        THE PLAN'S FIRST STEP, shared by the press and the dry run, so the dry run cannot count a
        product the press would have left out."""
        runnable: list[str] = []
        refusals: list[str] = []
        for key in keys:
            product = self._products.get(key)
            if product is None:
                continue
            refused = await product.cannot_run() if product.cannot_run is not None else None
            if refused is None:
                runnable.append(key)
            else:
                refusals.append(refused)
        return runnable, refusals

    def by_products(self, *keys: str) -> tasks.Starter:
        """Run now for a task whose work is a Build over the library for these products."""

        async def start(
            at: str, viewer: Viewer, only: tasks.Selection = tasks.EVERYTHING
        ) -> list[str]:
            runnable, refusals = await self.ready(self.chosen(keys, only))
            if not runnable and refusals:
                raise tasks.TaskRefused(refusals[0])
            _files, ids = await importing.start_runs(
                runnable,
                content=self._store.content,
                products=self._products,
                queue=self._queue,
                requested_by=viewer.id,
                at=at,
                roots=only.locations,
            )
            return ids

        return start

    async def first_lacking(
        self, keys: list[str], files: int, viewer: Viewer, roots: tuple[str, ...] | None
    ) -> tuple[str, ...]:
        """The first files a run of these products would hand out, by name, in the order its pages
        walk the library (or the folders `roots` names): the pass's own page read
        (`lacking_on_page`), so these are the files it would start with. Files this viewer cannot
        see are passed over, never named. Stops once enough are named or every lacking file has
        been met."""
        store = self._store
        names: list[str] = []
        met = 0
        after = None
        while len(names) < NAMED and met < files:
            walked = await store.content.asset_ids_page(after=after, limit=PAGE, roots=roots)
            lacking = await lacking_on_page(self._products, keys, walked.ids, store.content)
            wanted = [one for one in walked.ids if lacking.by_file.get(one)]
            met += len(wanted)
            seen = await store.access.visible_of(viewer, wanted)
            places = await store.content.locations_of([one for one in wanted if one in seen])
            for asset_id in wanted:
                if asset_id in places and len(names) < NAMED:
                    names.append(places[asset_id][0].filename)
            if len(walked.ids) < PAGE or walked.last is None:
                break
            after = walked.last
        return tuple(names)

    def plan_of(self, *keys: str) -> tasks.Planner:
        """The dry run of a task whose work is a Build: what its press would count and hand out."""
        products = self._products

        async def plan(only: tasks.Selection, viewer: Viewer) -> tasks.Plan:
            runnable, refusals = await self.ready(self.chosen(keys, only))
            counted = await count_lacking(
                products, self._store.content, runnable, runnable, roots=only.locations
            )
            lines = tuple(
                tasks.PlanLine(label=found.label, count=counted.each.get(key, 0))
                for key in runnable
                if (found := products.get(key)) is not None
            )
            names = (
                await self.first_lacking(runnable, counted.files, viewer, only.locations)
                if counted.files
                else ()
            )
            return tasks.Plan(
                files=counted.files, lines=lines, names=names, refusals=tuple(refusals)
            )

        return plan


def _library_starters(
    app: FastAPI, queue: JobQueue, hub: settings_hub.SettingsService
) -> dict[str, tasks.Starter]:
    async def scan_folders(
        at: str, viewer: Viewer, only: tasks.Selection = tasks.EVERYTHING
    ) -> list[str]:
        """Scan for some library folders: one walk of each, the walk a folder's own rescan queues.

        The shape is `scan_shape`'s for a whole folder (its id first, then what the caller adds),
        so a folder already being walked is the same row and the press collapses onto it."""
        return [
            await queue.enqueue(
                library_roots.SCAN,
                {"root_id": root_id, "scan_only": True},
                dedupe=True,
                priority=WAITED_ON_PRIORITY if at == AT_NOW else DEFAULT_PRIORITY,
                requested_by=viewer.id,
                at=at,
            )
            for root_id in only.locations or ()
        ]

    async def enrich(
        at: str, viewer: Viewer, _only: tasks.Selection = tasks.EVERYTHING
    ) -> list[str]:
        if not await hub.get_app(stash_boxes.SCAN_KEY):
            raise tasks.TaskRefused(stash_boxes.ENRICHING_OFF)
        # Refused, not started, when the pass would ask nobody: Run now with every box turned off
        # would otherwise answer "Started" and walk the library for nothing.
        box = await stash_boxes.box_for(hub, None)
        nobody = await wiring.part_of_app(app, stash_boxes.SERVICE).cannot_ask(box)
        if nobody is not None:
            raise tasks.TaskRefused(nobody)
        return [
            await queue.enqueue(
                stash_boxes.STASH_SWEEP,
                # A press: the box it resolves to and applying certain matches, exactly what the
                # Auto-enrich press sends. Without them a Run now would run as an unattended sweep.
                {
                    "viewer": viewer.id,
                    "offset": 0,
                    "queued": 0,
                    "box": box,
                    "apply": True,
                },
                priority=WAITED_ON_PRIORITY if at == AT_NOW else DEFAULT_PRIORITY,
                requested_by=viewer.id,
                at=at,
            )
        ]

    async def look_up_songs(
        at: str, viewer: Viewer, _only: tasks.Selection = tasks.EVERYTHING
    ) -> list[str]:
        """The lookup task's press: one walk over the files still owed a lookup, as this press.
        Refused in words while the switch is off or there is no key; nothing queued, and said as
        nothing to run, when no file is owed one."""
        try:
            walk = await wiring.part_of_app(app, music.LOOKUP_STARTER).start_catch_up(
                at=at,
                requested_by=viewer.id,
                priority=WAITED_ON_PRIORITY if at == AT_NOW else DEFAULT_PRIORITY,
            )
        except music.LookupNotReady as refused:
            raise tasks.TaskRefused(str(refused)) from refused
        return [] if walk is None else [walk]

    return {
        "enrichment": enrich,
        music.LOOKUP_TASK: look_up_songs,
        # Scan's whole run is its one job; this starts it for some folders.
        "scan": scan_folders,
    }


def _task_products(products: importing.ProductRegistry) -> dict[str, tuple[str, ...]]:
    # WHICH PRODUCTS EACH TASK IS: what its Run now builds, and whose runs are its "last ran" (see
    # `Ledger.last_run_for`). One declaration for both, so the press and the row cannot come to
    # name two different things. Generate is every Generate-family product but the music, which is
    # its own task.
    return {
        "generate": tuple(
            one.key
            for one in products
            if one.family is Family.GENERATE and one.key != music.PRODUCT
        ),
        "faces": (faces.PRODUCT,),
        "smart-search": ("meaning",),
        "watermarks": ("watermarks",),
        "music": (music.PRODUCT,),
    }


def _parts(
    products: importing.ProductRegistry, task_products: dict[str, tuple[str, ...]]
) -> dict[str, tasks.TaskParts]:
    def subtasks(keys: tuple[str, ...]) -> tuple[tasks.TaskPart, ...]:
        return tuple(
            tasks.TaskPart(key=key, label=found.label)
            for key in keys
            if (found := products.get(key)) is not None
        )

    # WHAT PART OF EACH TASK CAN RUN ON ITS OWN: the menu beside its press and the check on that
    # press both read this. A stage made of several products offers each; Scan, Generate and
    # Identify offer the folders (a Build carries them to every page it walks, see `start_runs`).
    # A task of one product, or one job over the whole library, runs whole.
    return {
        "scan": tasks.TaskParts(locations=True),
        "generate": tasks.TaskParts(subtasks=subtasks(task_products["generate"]), locations=True),
        "identify": tasks.TaskParts(subtasks=subtasks(IDENTIFY_PRODUCTS), locations=True),
    }


def _tasks_service(
    app: FastAPI,
    store: Storage,
    queue: JobQueue,
    hub: settings_hub.SettingsService,
    governs: dict[str, tuple[str, ...]],
) -> tasks.TasksService:
    products = wiring.part_of_app(app, importing.PRODUCTS)
    builds = _Builds(products, store, queue)
    task_products = _task_products(products)
    identify_products = IDENTIFY_PRODUCTS
    starters: dict[str, tasks.Starter] = {
        **{task: builds.by_products(*keys) for task, keys in task_products.items()},
        # The Identify stage is the three recognition tasks' work: its press is theirs, and it is
        # no product of its own, so it is not among `task_products`.
        "identify": builds.by_products(*identify_products),
        **_library_starters(app, queue, hub),
    }
    # AND WHICH TASKS HAVE A DRY RUN: every task whose work is a Build, since its plan (what each
    # file lacks) is a step of its own that the press starts from (see `plan_of`), and every task
    # whose run is split the same way in its own feature (see `dry_runs`).
    planners: dict[str, tasks.Planner] = {
        **{task: builds.plan_of(*keys) for task, keys in task_products.items()},
        "identify": builds.plan_of(*identify_products),
        **dry_run_planners(app, store, hub),
    }

    async def folders() -> list[tasks.TaskPart]:
        return [
            tasks.TaskPart(key=root.id, label=root.name, path=root.abs_path)
            for root in await store.library.roots()
        ]

    async def viewer_for(user_id: str) -> Viewer | None:
        # A dry run names files, so hidden files stay hidden to it: a job has no open vault.
        return await store.access.load_viewer(user_id)

    # And the third reader: a run's line in History is named after the task it was for.
    book = wiring.part_of_app_or_none(app, wiring.LEDGER)
    if book is not None:
        book.learn_tasks(task_products)
    return tasks.TasksService(
        queue=queue,
        read=hub.get_app,
        database=store.database,
        ledger=wiring.part_of_app_or_none(app, wiring.LEDGER),
        governs=governs,
        starters=starters,
        products=rows_products(task_products),
        # A run and the tasks it hands out name their products: a row is the work of those tasks.
        carriers=tuple(job_type for pair in importing.RUNS.values() for job_type in pair),
        passes=PASSES,
        parts=_parts(products, task_products),
        planners=planners,
        folders=folders,
        viewer_for=viewer_for,
        keep_awake=tasks.KeepAwake(),
        order=TASK_ORDER,
    )


async def build_tasks(
    app: FastAPI,
    store: Storage,
    queue: JobQueue,
    hub: settings_hub.SettingsService,
) -> tuple[tasks.TasksService, TaskClock]:
    """Every task's When, quiet hours, the one scheduler, and Run now. Before the workers start.

    Last of the building steps that register a handler (the update check's), and the one that
    places every timed task's next run, so it has to come after every feature has declared its
    tasks and before a worker can claim anything. See `build_workers`.

    WHAT ONLY THIS PLACE KNOWS, handed in: which further job types each task's When governs (the
    pictures Generate makes are the media feature's, its run the Importing feature's), and how to
    start the tasks whose Run now is a pass over the library rather than a single job.
    """
    update_notify.register_handlers(service=wiring.part_of_app(app, update_notify.SERVICE))

    async def quiet_range() -> tuple[str, str]:
        return str(await hub.get_app(tasks.FROM_KEY)), str(await hub.get_app(tasks.UNTIL_KEY))

    clock = TaskClock(queue, read=hub.get_app, quiet_range=quiet_range)
    install_task_clock(clock)
    service = _tasks_service(app, store, queue, hub, _governs(queue, hub))
    provide(app, tasks.SERVICE, service)
    tasks.register_handlers(service.rehearse)
    queue.switchboard.declare_quiet_hours(service.quiet_hold)
    # A task whose run is one job writes each run's line in the history as it settles, which is
    # where its row reads "last ran" from. The line is kept; the job row goes after a week.
    for task in registered_schedules().values():
        if task.records_runs and task.job_type is not None:
            queue.record_runs_of(task.job_type, task_id=task.id, title=task.title)
    # And every dry run, so what it said is kept in History after its job row is pruned. One
    # subject for all of them: the report names the task, and a dry run is never a task's last run.
    queue.record_runs_of(tasks.TASK_DRY_RUN, task_id=tasks.TASK_DRY_RUN, title=DRY_RUN_TITLE)
    await clock.ensure_all()
    # THE UPDATE CHECK'S ANSWER IS HELD IN MEMORY, so a start begins with a check; not at all when
    # the check only runs when pressed.
    if await starts_on_its_own(hub, "update-check"):
        await check_for_an_update_at_start(clock)
    return service, clock
