# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Importing screen's own endpoints. Admin-only, and the server is what says so.

Two things live here that the settings hub cannot answer: what one folder says differently, which
is not a declared setting and has no scope the registry understands, and how far behind the library
has fallen, which is a count over the derivatives table rather than a stored value.

Turning a switch on or off still goes through the settings hub like every other setting. There is
no second way to write one here, because a write has to check the key exists, that the value fits
its type and that this user may set it: three rules that come to differ the moment they are
written twice.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from sift.kernel import wiring
from sift.kernel.access import AssetFilter, Repository, Viewer, Where
from sift.kernel.access.sentences import and_then
from sift.kernel.content import ContentStore, LibraryStore
from sift.kernel.jobs import DEFAULT_PRIORITY, WAITED_ON_PRIORITY, JobQueue
from sift.kernel.jobs.families import AGAIN, FAMILY_LABELS, Family
from sift.kernel.jobs.ledger import PACE_OVER_ITEMS, Ledger, RunRecord, priced
from sift.kernel.jobs.quiet_hours import AT_NOW, AT_QUIET
from sift.kernel.log import get_logger
from sift.kernel.settings_registry import folder_words_of, get_registered
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.importing.coming import take_over
from sift.slices.importing.jobs import RUNS
from sift.slices.importing.models import (
    BuildRequest,
    BuildRow,
    BuildSheet,
    BuildStarted,
    FolderAnswers,
    FolderList,
    RetryRequest,
    RetryResult,
    RunNowGroup,
    RunNowPass,
    RunNowPasses,
    RunNowRequest,
    RunNowStarted,
    SetFolderAnswers,
)
from sift.slices.importing.products import (
    PRODUCTS,
    Product,
    ProductRegistry,
    Reading,
    count_lacking,
    files_lacking,
)
from sift.slices.importing.service import SERVICE, ImportPolicy
from sift.slices.importing.store import ROOT_PREFS, RootPreferences

log = get_logger(__name__)

router = APIRouter(tags=["importing"], prefix="/importing")

#: The press on a file or a selection, addressed under the files it is about rather than under this
#: screen: it is made from a file's menu and a wall's selection bar, and never from Importing.
files_router = APIRouter(tags=["importing"], prefix="/assets")


def _policy(request: Request) -> ImportPolicy:
    return part_of(request, SERVICE)


def _roots(request: Request) -> RootPreferences:
    return part_of(request, ROOT_PREFS)


def _products(request: Request) -> ProductRegistry:
    return part_of(request, PRODUCTS)


#: The name the ledger files the worker count under. `sift/wiring/workers.py` puts it into every
#: run's settings under exactly this key when it reconfigures the pool; a run from before the key
#: was renamed prices without it.
JOBS_AT_ONCE = "jobs together"


def _wall_seconds_per_file(
    run: RunRecord | None, key: str, *, products: Collection[str]
) -> float | None:
    """How long one file of this product took ON THE CLOCK, from the last run that made any.

    Worker seconds presented as wall time would be out by the number of jobs running at the same
    time, and a person reading "about four days" for a one-day run decides not to press. The worker
    figure is not wrong, it is simply not an answer to "how long will I be waiting": it is kept, on
    `seconds_per_file`, for the screen that asks how hard the machine worked.

    The clock is divided between the PRODUCTS of the run in proportion to the worker time each one
    spent, so the estimates of a run's products add up to the run's own wall clock rather than to
    several copies of it.

    **`products` is not the same thing as the keys the ledger holds.** A run's ledger row carries a
    row per timed stage named `build.*`, and one of those stages is not a product at all:
    `decode_once` is the whole-file read that serves every product on that file, and it can be a
    large share of a run's worker time. Dividing by every key would under-price the products badly.
    The shared read is part of what getting a face cost, so its share of the clock goes back to the
    products that caused it, which is exactly what leaving it out of the divisor does.

    None where the run cannot price it: no run, a run that made none of this, or a run so short the
    clock cannot tell. A guess would read as a measurement.
    """
    if run is None:
        return None
    took = run.seconds
    made = run.products.get(key)
    if took is None or took <= 0 or made is None or key not in products:
        return None
    files = int(made.get("n", 0))
    if files <= 0:
        return None
    busy = sum(int(one.get("ms", 0)) for name, one in run.products.items() if name in products)
    mine = int(made.get("ms", 0))
    if busy <= 0 or mine <= 0:
        return None
    return took * (mine / busy) / files


def _runs_that_made(runs: Sequence[RunRecord], key: str) -> list[RunRecord]:
    """The runs of this sample that made this product, newest first, until they hold
    `PACE_OVER_ITEMS` of its files: the pace as it is now, not the history."""
    made: list[RunRecord] = []
    files = 0
    for run in runs:
        n = int(run.products.get(key, {}).get("n", 0))
        if n <= 0:
            continue
        made.append(run)
        files += n
        if files >= PACE_OVER_ITEMS:
            break
    return made


def _window(
    runs: Sequence[RunRecord], key: str, *, products: Collection[str]
) -> tuple[float, float] | None:
    """Wall seconds per file of this product, at its cheapest and dearest stretch over these runs.

    A run over photographs and a run over long videos price the same product a hundred times
    apart, and the files the next run reads may be either, so the row is a window between them
    rather than the newest run's one figure. None under `FEWEST_ITEMS` files, as for every pace.
    """
    sample = [
        (wall, int(run.products[key]["n"]))
        for run in runs
        if (wall := _wall_seconds_per_file(run, key, products=products)) is not None
    ]
    found = priced(sample)
    return None if found is None else (found.quick, found.slow)


def _jobs_at_once(run: RunRecord | None) -> int | None:
    """How many jobs were running together during the run an estimate is priced from.

    It travels with the estimate because the estimate ASSUMES IT: the same library on the same
    machine with that number halved takes about twice as long. The sentence on screen says it, so
    somebody reading "about 21 hours" knows what it is 21 hours of.
    """
    if run is None:
        return None
    value = run.settings.get(JOBS_AT_ONCE)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    return value


@router.get("/folders", response_model=FolderList)
async def folder_answers(
    library: Annotated[LibraryStore, Depends(wiring.library)],
    policy: Annotated[ImportPolicy, Depends(_policy)],
    prefs: Annotated[RootPreferences, Depends(_roots)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FolderList:
    """Every folder in the library, and the switches each one answers differently.

    Each switch comes with what it is called, answered HERE rather than looked up by the screen in
    the settings it has loaded. Several of these keys are retired into the task Whens and have no
    row of their own, so a screen reading labels off the registered rows would show them as raw keys;
    the registry knows what each became, and the list is where the question is asked. A key
    retired into a task's When is called by its FOLDER words where it declared some
    (`settings_registry.folder_words_of`): a folder's answer decides only what happens as a file
    arrives, and the task's title would promise a run over the folder that never happens.
    """
    roots = await library.roots()
    stored = await prefs.for_roots(root.id for root in roots)
    keys = list(policy.overridable())
    words = {key: folder_words_of(key) for key in keys}
    return FolderList(
        folders=[
            FolderAnswers(
                root_id=root.id,
                name=root.name,
                answers={key: bool(value) for key, value in (stored.get(root.id) or {}).items()},
            )
            for root in roots
        ],
        keys=keys,
        labels={key: label or key for key, (label, _help) in words.items()},
        helps={key: help_ for key, (_label, help_) in words.items() if help_},
    )


@router.put(
    "/folders/{root_id}", response_model=FolderAnswers, dependencies=[Depends(csrf_protect)]
)
async def set_folder_answers(
    root_id: str,
    body: SetFolderAnswers,
    library: Annotated[LibraryStore, Depends(wiring.library)],
    policy: Annotated[ImportPolicy, Depends(_policy)],
    prefs: Annotated[RootPreferences, Depends(_roots)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> FolderAnswers:
    """Answer for one folder. A key set to null puts it back to following the library.

    A key nothing gates is refused rather than stored. An override that no job ever reads is a row
    that looks like a decision and is not one, and it would sit in the table describing a switch
    that may since have been removed.
    """
    root = await library.get_root(root_id)
    if root is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no such folder.")

    known = set(policy.overridable())
    unknown = sorted(key for key in body.answers if key not in known)
    if unknown:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"A folder cannot answer {unknown[0]}.",
        )

    await prefs.set(root_id, dict(body.answers))
    stored = await prefs.for_root(root_id)
    log.info("importing.folder_answered", root_id=root_id, how_many=len(stored))
    return FolderAnswers(
        root_id=root.id,
        name=root.name,
        answers={key: bool(value) for key, value in stored.items()},
    )


@router.get("/build", response_model=BuildSheet)
async def build_sheet(
    content: Annotated[ContentStore, Depends(wiring.content)],
    access: Annotated[Repository, Depends(wiring.access)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    ledger: Annotated[Ledger, Depends(wiring.ledger)],
    products: Annotated[ProductRegistry, Depends(_products)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> BuildSheet:
    """What a Build would do. Queues nothing, and must not.

    Every count here is exact and taken now, before anything starts: a button whose size is only
    visible after it has started is one nobody can press carefully. Every row's count and the
    union come from one statement over the library, so they describe one moment. The time is a
    window between the cheapest and the dearest stretch of this machine's recent runs that made
    the product (`_priced_rows`); too few files in them gives no window rather than a guess.

    The window is WALL TIME and the per-file figure beside it is WORKER TIME. See
    `_wall_seconds_per_file` for why they are two numbers and not one.
    """
    # The product's OWN family's runs (Generate's for a picture, Identify's for faces), since
    # the two are timed apart.
    sample: dict[Family, list[RunRecord]] = {}
    for family in RUNS:
        sample[family] = await ledger.recent_runs(family)
    rows: list[BuildRow] = []
    # Which of a ledger row's timed stages are products. The rest (`decode_once`, the whole-file
    # read shared by every product on a file) are timed under the same prefix and are not things
    # the sheet offers; see `_wall_seconds_per_file`.
    priceable = set(products.keys())
    ticked = [product.key for product in products if await product.switched_on()]
    counted = await count_lacking(products, content, products.keys(), ticked)
    for product in products:
        on = product.key in ticked
        files = counted.each[product.key]
        runs = _runs_that_made(sample.get(product.family, []), product.key)
        run = runs[0] if runs else None
        measured = (run.products if run is not None else {}).get(product.key)
        seconds = None
        if measured and int(measured.get("n", 0)) > 0:
            seconds = int(measured.get("ms", 0)) / int(measured["n"]) / 1000
        window = _window(runs, product.key, products=priceable)
        rows.append(
            BuildRow(
                key=product.key,
                label=product.label,
                help=product.help,
                switched_on=on,
                files=files,
                seconds_per_file=None if seconds is None else round(seconds, 2),
                quick_seconds=None if window is None else int(files * window[0]),
                slow_seconds=None if window is None else int(files * window[1]),
                jobs_at_once=_jobs_at_once(run),
                cannot=await _left_out_count(access, content, viewer, product.key),
            )
        )
    unfinished = await queue.unfinished_by_type()
    return BuildSheet(
        rows=rows,
        files=counted.files,
        identifying=await content.legacy_identity_count(),
        unread=await content.unread_count(),
        running=any(unfinished.get(job_type, 0) > 0 for pair in RUNS.values() for job_type in pair),
        night_start=str(await products.night_start()),
        measure_first=not await products.machine.measured(),
    )


async def _left_out_count(
    access: Repository, content: ContentStore, viewer: Viewer, product: str
) -> int:
    """How many files this product gave up on, as the wall its line opens counts them.

    The table's own count first, because it is one index read and nearly always nought: a product
    that has given up on nothing needs no scoped count to say so. Otherwise the Files wall's total
    for `left_out:<product>`, for this viewer (`Repository.count_visible`), which is the number the
    wall the link opens will say.
    """
    if await content.verdict_count(product) == 0:
        return 0
    return await access.count_visible(viewer, AssetFilter(where=Where("left_out", (product,))))


@router.post("/build", response_model=BuildStarted, dependencies=[Depends(csrf_protect)])
async def start_build(
    body: BuildRequest,
    content: Annotated[ContentStore, Depends(wiring.content)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    products: Annotated[ProductRegistry, Depends(_products)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> BuildStarted:
    """Start a Build for the ticked products, now or when the quiet hours begin.

    The count travels with the request so the run is weighed by it from the first moment; the
    pass does not discover its size as it goes. `dedupe` keeps two presses from walking the same
    library twice for the same gaps.
    """
    keys = [key for key in dict.fromkeys(body.products) if products.get(key) is not None]
    unknown = sorted(set(body.products) - set(keys))
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"There is nothing called {unknown[0]}.")
    if not keys:
        return BuildStarted(queued=False, files=0)
    at = AT_QUIET if body.at == AT_QUIET or body.tonight else AT_NOW
    files, job_ids = await start_runs(
        keys, content=content, products=products, queue=queue, requested_by=viewer.id, at=at
    )
    if not job_ids:
        return BuildStarted(queued=False, files=files)
    starts_at = await products.quiet_opens() if at == AT_QUIET else None
    log.info("importing.build_requested", files=files, products=keys, at=at)
    return BuildStarted(
        queued=True, files=files, job_id=job_ids[0], job_ids=job_ids, starts_at=starts_at
    )


async def start_runs(
    keys: list[str],
    *,
    content: ContentStore,
    products: ProductRegistry,
    queue: JobQueue,
    requested_by: str | None,
    at: str = AT_NOW,
    roots: Sequence[str] | None = None,
) -> tuple[int, list[str]]:
    """Start one run per family these products belong to. Answers the files lacking and the runs.

    THE ONE BODY of every press that goes over the library for products: the Build sheet and a
    task's Run now | Run during quiet hours. A family with
    nothing lacking starts no run: a Generate run over a library that needs no pictures walks it
    to find nothing, and shows on the Activity screen as work somebody is waiting on.

    A PRESS RUNS WHATEVER THE TASK'S WHEN SAYS. `at` is marked on the run and handed to every page
    and task it gives out (`JobContext.enqueue_child`): `now` runs immediately at the urgency of the
    person waiting; `quiet` waits for quiet hours and pauses when they close, at the ordinary
    priority, because nobody is sitting in front of it and it should not step in front of whatever
    is. Both name the person who pressed, so the pass says who started it.

    `roots` runs it over some library folders only: counted over them, and carried in the run's
    payload so every page walks only them. None is the whole library, and its payload has no
    `roots` key at all, so a whole run is the same row whoever asks for it.
    """
    within = None if roots is None else sorted(set(roots))
    files = await files_lacking(products, keys, content, roots=within)
    if files == 0:
        return 0, []
    # One run per family the ticked products belong to. The pane asks for Generate and Identify
    # separately, so this is usually one run; a request naming both gets both, each weighed by
    # its own files.
    job_ids: list[str] = []
    for family, (run_type, _file_type) in RUNS.items():
        mine = [
            key
            for key in keys
            if (found := products.get(key)) is not None and found.family is family
        ]
        if not mine:
            continue
        counted = await count_lacking(products, content, mine, mine, roots=within)
        if counted.files == 0:
            continue
        payload: dict[str, object] = {"products": mine, "files": counted.files}
        if within is not None:
            payload["roots"] = within
            # WHAT THE RUN IS OVER, PER PRODUCT: the count the task's dry run over the same folders
            # states, taken by the same question at the press. Activity draws the run's lines
            # from it (done of this), so the bar and the dry run cannot disagree, and a run over
            # one folder is not drawn with the library's figures. A whole run carries none: the
            # library's own counts are its figures.
            payload["each"] = {key: counted.each.get(key, 0) for key in mine}
        job_ids.append(
            await queue.enqueue(
                run_type,
                payload,
                dedupe=True,
                priority=WAITED_ON_PRIORITY if at == AT_NOW else DEFAULT_PRIORITY,
                requested_by=requested_by,
                at=at,
            )
        )
    return files, job_ids


@router.post("/build/retry", response_model=RetryResult, dependencies=[Depends(csrf_protect)])
async def retry_build(
    body: RetryRequest,
    content: Annotated[ContentStore, Depends(wiring.content)],
    products: Annotated[ProductRegistry, Depends(_products)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> RetryResult:
    """Forget what these products have given up on, so the next Build offers those files again.

    Forgetting is the whole of it: nothing is queued. The sheet re-reads and shows the files back
    among what is lacking, and the person decides whether to run. A verdict that was right comes
    back on the first attempt, once, which is what a retry costs and what it is for.
    """
    keys = [key for key in dict.fromkeys(body.products) if products.get(key) is not None]
    unknown = sorted(set(body.products) - set(keys))
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"There is nothing called {unknown[0]}.")
    forgotten = {key: await content.clear_verdicts(key) for key in keys}
    log.info("importing.verdicts_forgotten", forgotten=forgotten)
    return RetryResult(forgotten=forgotten)


# --- "Run task" on a file or a selection --------------------------------------------------------


#: WHAT A STAGE'S EVERY-PASS PRESS IS DOING, as the head of its sentence: "Identifying 12 files:
#: faces, meaning, watermarks." The stage's own verb, beside the label Settings gives its press
#: ("Identify now"): a stage missing here says its label, which is plainer and still true.
_STAGE_DOING: dict[Family, str] = {
    Family.SCAN: "Scanning",
    Family.GENERATE: "Generating",
    Family.IDENTIFY: "Identifying",
}

#: How a stage's every-pass press is named in a request: the stage, and this. A pass's own key is a
#: product's or a reading's and never holds a colon, so the two cannot be mistaken for each other.
_EVERY = ":all"


def _run_now_groups(products: ProductRegistry) -> list[tuple[Family, list[Reading | Product]]]:
    """The passes a press can start, grouped by the Importing stage that owns them, in its order.

    READ FROM THE DECLARATIONS THE PANE ALREADY DRAWS: the products are the rows the Generate and
    Identify stages count, under the same labels, and the readings are what Scan means for one
    file. Nothing here lists a pass, so a product registered tomorrow is offered by the same line
    that puts it on the sheet, and every one of them is per file by construction, because a
    product's maker is handed exactly one file's task (`jobs.build_file`). The same list is what a
    stage's every-pass press expands into, so "Identify all" cannot come to mean a different set
    from the rows drawn under it.
    """
    readings: list[Reading | Product] = list(products.readings())
    groups: list[tuple[Family, list[Reading | Product]]] = [(Family.SCAN, readings)]
    for family in RUNS:
        mine: list[Reading | Product] = [one for one in products if one.family is family]
        groups.append((family, mine))
    return [(family, passes) for family, passes in groups if passes]


def _pass_named(products: ProductRegistry, key: str) -> Reading | Product | None:
    for _family, passes in _run_now_groups(products):
        for one in passes:
            if one.key == key:
                return one
    return None


def _stage_named(
    products: ProductRegistry, key: str
) -> tuple[Family, list[Reading | Product]] | None:
    """The stage a request's `<stage>:all` names, and every pass under it, or None."""
    for family, passes in _run_now_groups(products):
        if key == f"{family.value}{_EVERY}":
            return family, passes
    return None


@router.get("/run-now", response_model=RunNowPasses)
async def run_now_passes(
    products: Annotated[ProductRegistry, Depends(_products)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> RunNowPasses:
    """What "Run task" offers on a file's menu and a selection's bar. Queues nothing.

    Under this screen's address rather than beside the press, because `GET /assets/{asset_id}` is a
    file's own record and a sibling named `run` would be read as a file of that id by whichever
    router was mounted first. Admin-only for the reason every stage of Importing is: each of these
    is real work on the machine, and what it finds lands on screens everybody sees.
    """
    return RunNowPasses(
        groups=[
            RunNowGroup(
                family=family.value,
                label=f"{FAMILY_LABELS[family]} now",
                every=RunNowPass(
                    key=f"{family.value}{_EVERY}",
                    label=f"{FAMILY_LABELS[family]} all",
                    help="Every pass below, for these files.",
                ),
                passes=[RunNowPass(key=one.key, label=one.label, help=one.help) for one in passes],
            )
            for family, passes in _run_now_groups(products)
        ]
    )


def _files(count: int) -> str:
    return "1 file" if count == 1 else f"{count:,} files"


def _switch_label(key: str) -> str:
    """A switch as Settings names it, for the sentence that says it is what refused a press."""
    declared = get_registered(key)
    return declared.label if declared is not None else key


def _left_out(*, total: int, waiting: int, had: int, refused: int, switch: str) -> list[str]:
    """What a press left out and why, one clause each, in the words the toast says."""
    one = total == 1
    parts: list[str] = []
    if waiting:
        parts.append(
            "this file is already waiting for it" if one else f"{_files(waiting)} already waiting"
        )
    if had:
        parts.append("this file already has it" if one else f"{_files(had)} already had it")
    if refused:
        named = f'"{_switch_label(switch)}"'
        parts.append(
            f"{named} is switched off for this file"
            if one
            else f"{_files(refused)} with {named} switched off"
        )
    return parts


@dataclass(frozen=True, slots=True)
class _Pressed:
    """What one pass did for the files of one press: which it queued, and which it left out why.

    Sets of files rather than counts, so a stage's every-pass press can say how many FILES it
    touched (a file handed three passes is one file), by a union rather than a sum.
    """

    chosen: Reading | Product
    queued: list[str]
    waiting: frozenset[str] = frozenset()
    had: frozenset[str] = frozenset()
    refused: frozenset[str] = frozenset()
    switch: str = ""
    problem: str | None = None
    """Why the pass cannot run on this machine at all just now (`Product.cannot_run`). Set, nothing
    else is: no file was looked at and nothing was queued."""

    def left_out(self, total: int) -> list[str]:
        return _left_out(
            total=total,
            waiting=len(self.waiting),
            had=len(self.had),
            refused=len(self.refused),
            switch=self.switch,
        )


async def _press_one(
    chosen: Reading | Product,
    wanted: list[str],
    *,
    queue: JobQueue,
    policy: ImportPolicy,
    presser: str,
) -> _Pressed:
    """Queue one pass for these files (already known to be visible), the way Importing would.

    THE ONE BODY of a press, whether it named one pass or a stage's every pass: the every-pass
    press is this, once per pass, so a switch refuses, a waiting file is skipped and a gap-filler
    skips what it has by the same lines for both.
    """
    if isinstance(chosen, Product) and chosen.cannot_run is not None:
        problem = await chosen.cannot_run()
        if problem is not None:
            return _Pressed(chosen=chosen, queued=[], problem=problem)

    # WHAT IS ALREADY COMING, from every queue type that would answer this for a file.
    if isinstance(chosen, Reading):
        live_types = [chosen.job_type]
    else:
        live_types = [RUNS[chosen.family][1]]
        if chosen.governed_by is not None:
            live_types.append(chosen.governed_by)

    # WHAT IS ALREADY COMING, and the part of it still waiting pulled forward to this press. See
    # `coming.take_over`: a press is now, even for work already in the queue.
    coming = await take_over(
        queue,
        live_types,
        wanted,
        chosen.key,
        priority=WAITED_ON_PRIORITY,
        requested_by=presser,
    )
    pulled = [asset_id for asset_id in wanted if asset_id in coming.pulled]
    waiting = frozenset(
        asset_id
        for asset_id in wanted
        if asset_id in coming.files and asset_id not in coming.pulled
    )
    left = [asset_id for asset_id in wanted if asset_id not in coming.files]
    refused: list[str] = []
    switch = ""
    had: frozenset[str] = frozenset()
    if isinstance(chosen, Product):
        allowed: list[str] = []
        for asset_id in left:
            key = (
                await policy.refused_by(chosen.governed_by, asset_id, pressed=True)
                if chosen.governed_by is not None
                else None
            )
            if key is None:
                allowed.append(asset_id)
            else:
                refused.append(asset_id)
                switch = switch or key
        left = allowed
        if not chosen.again and left:
            lacking = await chosen.lacking_among(left)
            had = frozenset(asset_id for asset_id in left if asset_id not in lacking)
            left = [asset_id for asset_id in left if asset_id in lacking]

    # ONE WRITE FOR THE WHOLE PRESS (`enqueue_many`), each file still collapsing onto an identical
    # task already waiting, rather than one trip through the single writer per file.
    tasks: list[dict[str, object]] = []
    for asset_id in left:
        task: dict[str, object] = {"asset_id": asset_id}
        if not isinstance(chosen, Reading):
            task["products"] = [chosen.key]
            if chosen.again:
                task[AGAIN] = True
        tasks.append(task)
    await queue.enqueue_many(
        chosen.job_type if isinstance(chosen, Reading) else RUNS[chosen.family][1],
        tasks,
        priority=WAITED_ON_PRIORITY,
        dedupe=True,
        requested_by=presser,
        title=f"{chosen.doing or chosen.label} {_files(len(tasks))}",
    )
    return _Pressed(
        chosen=chosen,
        queued=[*pulled, *left],
        waiting=waiting,
        had=had,
        refused=frozenset(refused),
        switch=switch,
    )


def _stage_said(family: Family, total: int, pressed: list[_Pressed]) -> tuple[list[str], str]:
    """The every-pass press's sentence: the stage's verb over the files, the passes it queued,
    then one sentence per pass that left files out or could not run, naming the pass and why,
    as a single pass's press names it. Returns the files queued and the sentence."""
    queued = sorted({asset_id for one in pressed for asset_id in one.queued})
    ran = [one.chosen.label.lower() for one in pressed if one.queued]
    notes: list[str] = []
    for one in pressed:
        if one.problem is not None:
            notes.append(f"{one.chosen.label}: {one.problem.rstrip('.')}.")
            continue
        parts = one.left_out(total)
        if parts:
            notes.append(f"{one.chosen.label}: " + and_then(parts) + ".")
    if not queued:
        return queued, " ".join(["Nothing was queued.", *notes])
    doing = _STAGE_DOING.get(family, FAMILY_LABELS[family])
    head = f"{doing} {_files(len(queued))}: {', '.join(ran)}."
    return queued, " ".join([head, *notes])


@files_router.post("/run", response_model=RunNowStarted, dependencies=[Depends(csrf_protect)])
async def run_now(
    body: RunNowRequest,
    access: Annotated[Repository, Depends(wiring.access)],
    queue: Annotated[JobQueue, Depends(wiring.queue)],
    policy: Annotated[ImportPolicy, Depends(_policy)],
    products: Annotated[ProductRegistry, Depends(_products)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> RunNowStarted:
    """Run one pass now, or a stage's every pass, for these files: what Importing's own press
    does, narrowed to them.

    THE ONE DOOR for doing a file's work again by hand, for every pass: without it a bad thumbnail,
    a watermark read under an older model or a file that was replaced on disk could only be done
    again by walking the whole library. This is that walk's task, handed the files a person
    picked:

    * **Every pass is refused the way Importing refuses it.** A switch that says no for a file
      (the library's, or the folder's answer over it) leaves that file out, and a press where it
      said no for every file is refused naming the switch. A pass that cannot run on this machine
      at all (`Product.cannot_run`) is refused before anything is queued, with its own sentence.
    * **A file already waiting for this is not queued twice**, whoever queued it: an arriving
      file's own job, a pass over the library, an earlier press. Read once per press, never looped.
      Work of it still WAITING is pulled forward: the press collapses onto that row and runs it
      now, whatever the task's When held it for; only work already running is left as it is.
    * **Again means again** where the maker can: the task carries `AGAIN`. A pass whose maker only
      fills what is missing is offered only for the files that lack it. See `Product.again`.
    * **Ahead of the library-wide work** (`WAITED_ON_PRIORITY`), because somebody pressed it and is
      looking at the files, and **named for the presser** (`requested_by`), which is also what tells
      a face scan to ring the screens when it lands. Each task writes its own History line.
    * **`<stage>:all` ("Identify all") is every pass of that stage**, expanded HERE rather than
      by the screen sending one request per pass: the server is the one place that knows which
      passes a stage has, so a pass registered tomorrow joins "Identify all" by the declaration
      that draws its row, and the press answers with one sentence about the lot. Each pass is the
      single press above; one that a switch refuses or that cannot run here is left out and named,
      and the press is refused only when no pass queued anything.

    An id this admin may not open is the 404 a made-up one gets, and one such id refuses the press:
    a selection is what the wall showed, so a stranger in it is a request nobody's screen made.
    """
    target = _stage_named(products, body.run) or _pass_named(products, body.run)
    if target is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"There is nothing called {body.run} that can run for one file.",
        )
    wanted = list(dict.fromkeys(body.asset_ids))
    visible = await access.visible_of(viewer, wanted)
    if any(asset_id not in visible for asset_id in wanted):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such file")

    pressed: list[_Pressed] = []
    if isinstance(target, tuple):
        family, passes = target
        for one in passes:
            pressed.append(
                await _press_one(one, wanted, queue=queue, policy=policy, presser=viewer.id)
            )
        queued, said = _stage_said(family, len(wanted), pressed)
        if not queued:
            raise HTTPException(status.HTTP_409_CONFLICT, said)
    else:
        chosen = target
        only = await _press_one(chosen, wanted, queue=queue, policy=policy, presser=viewer.id)
        if only.problem is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, only.problem)
        parts = only.left_out(len(wanted))
        if not only.queued:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Nothing was queued: " + and_then(parts) + "."
            )
        pressed.append(only)
        queued = only.queued
        said = f"{chosen.doing or chosen.label} {_files(len(queued))}."
        if parts:
            said += " Left out: " + and_then(parts) + "."

    waiting = {asset_id for one in pressed for asset_id in one.waiting}
    had = {asset_id for one in pressed for asset_id in one.had}
    refused = {asset_id for one in pressed for asset_id in one.refused}
    ran = [one.chosen.key for one in pressed if one.queued]
    log.info(
        "importing.run_now",
        run=body.run,
        passes=ran,
        queued=len(queued),
        waiting=len(waiting),
        had=len(had),
        refused=len(refused),
    )
    return RunNowStarted(
        queued=len(queued),
        waiting=len(waiting),
        had=len(had),
        refused=len(refused),
        said=said,
        passes=ran,
    )
