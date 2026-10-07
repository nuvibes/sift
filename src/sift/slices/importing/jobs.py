# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Build: one pass over the library that reads each file once for everything it lacks.

This is what makes "off" mean *later* rather than *never*, and it is the one long stage a person
presses. Every switch on the Importing screen is read as a file arrives, so turning one
off skips the work for everything that lands while it is off, and nothing else in Sift would
ever come back to it: a rescan skips any file whose path, size and mtime are unchanged, which is
what makes a rescan quick and also means an already-indexed file is never offered again.

So the pass walks the library a page at a time, asks each product which files on the page lack it,
and hands out one TASK per file naming every product that file lacks among the ones ticked. The
task makes them in turn on the one read of the file. Four separate sweeps (pictures, then
fingerprints, then faces, then meaning) would each open every file again on a share with only a
couple of read slots; one task per file is what lets the reads be shared.

**One page per job, and the job asks for itself again.** A pass that ran until the library was done
would hold a worker for an hour, report progress nobody could read, and lose everything it had
queued if the machine went down halfway. Each page is a small complete unit somebody can watch on
the Activity screen and stop. The count the sheet stated travels in the payload, so the pass is
weighed by the files it has still to hand out and the tasks by the files they hold.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from sift.kernel.content import ContentStore
from sift.kernel.jobs import JobBlocked, JobContext, held_for, register_handler
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.ledger import BUILD_STAGE_PREFIX
from sift.kernel.log import get_logger, timing_hook
from sift.slices.importing.coming import take_over
from sift.slices.importing.products import PAGE, ProductRegistry, lacking_on_page

log = get_logger(__name__)

GENERATE = "generate"
GENERATE_FILE = "generate_file"
IDENTIFY = "identify"
IDENTIFY_FILE = "identify_file"

#: The two runs, by family: the job that walks the library a page at a time, and the task it
#: hands out per file. Two rather than one because they are two things a person waits for and
#: each has to be counted before it starts; a product says which it belongs to.
RUNS: Mapping[Family, tuple[str, str]] = {
    Family.GENERATE: (GENERATE, GENERATE_FILE),
    Family.IDENTIFY: (IDENTIFY, IDENTIFY_FILE),
}

#: What the run says while the self-test goes, on the Activity screen.
MEASURING_FIRST = (
    "Benchmarking this device first to find the fastest way to process each file. It takes "
    "up to 5 minutes and runs once."
)

#: The payload key of a first page queued again behind the benchmark it asked for.
MEASURED_FIRST = "measured_first"


async def build(
    context: JobContext,
    *,
    content: ContentStore,
    products: ProductRegistry,
    run_type: str,
    file_type: str,
) -> None:
    """Hand out one task per file for one page of the library, then ask for the next page.

    `run_type` and `file_type` are this family's own two job types (see `RUNS`), so a page of
    the Generate run hands out Generate tasks and asks for a Generate page, and the family the
    screen counts is the one that asked.

    The products are the ones ticked on the sheet, by key; a key nothing registers is ignored
    rather than refused, so a run asked for under one version of Sift finishes under the next.
    `files` is the count the sheet stated when the run was asked for, and what this job weighs
    until every task is handed out. `roots`, when the run was asked for some library folders only,
    names them; every page walks only those and hands the same list to the next.
    """
    keys = [str(one) for one in context.payload.get("products") or [] if products.get(str(one))]
    roots = _roots(context.payload.get("roots"))
    # Where the walk has reached, by key: see `ContentStore.asset_ids_page`. A page queued by an
    # older Sift carries an offset instead, and starts the walk again from the top: what is done
    # is no longer lacking and what is coming is not handed out twice, so nothing is made twice.
    after = _page_key(context.payload.get("after"))
    first = after is None and not context.payload.get("offset")
    queued_before = int(context.payload.get("queued") or 0)
    files = int(context.payload.get("files") or 0)
    if not keys:
        await context.set_progress(1.0)
        await context.set_note("Nothing was ticked, so there's nothing to build.")
        log.info("importing.build.nothing_ticked")
        return

    # This page weighs nothing of its own: the files still to hand out are counted for the family by
    # the task type's counter (`sift/wiring/work_ahead.py`); weighing them here counts them twice.
    # On a machine never measured the first page asks for the benchmark and queues itself again
    # behind it, once: waiting inside a worker would hold one the benchmark pauses everything for.
    if first and not context.payload.get(MEASURED_FIRST) and not await products.machine.measured():
        await products.machine.measure()
        await context.enqueue_child(
            run_type, {**context.payload, MEASURED_FIRST: True}, priority=context.job.priority
        )
        await context.set_progress(1.0)
        await context.set_note(MEASURING_FIRST)
        log.info("importing.build.measuring_first")
        return
    # And each ticked product's once-per-run housekeeping, on the first page only.
    if first:
        for key in keys:
            product = products.get(key)
            if product is not None and product.before_run is not None:
                await product.before_run()
    walked = await content.asset_ids_page(after=after, limit=PAGE, roots=roots)
    page = walked.ids
    lacking = await lacking_on_page(products, keys, page, content)
    # WORK ALREADY COMING FOR A FILE ON THIS PAGE IS NOT HANDED OUT AGAIN. What is still waiting
    # (a file's own pictures held for quiet hours, say) becomes this run's (`coming.take_over`),
    # and what is running is left to finish. A task handed out beside a held row would make the
    # file's pictures, and the held row would make them all again at the range's opening.
    taken: set[str] = set()
    for key in keys:
        product = products.get(key)
        file_ids = [asset_id for asset_id, wanted in lacking.by_file.items() if key in wanted]
        if product is None or not file_ids:
            continue
        types = [file_type] + ([product.governed_by] if product.governed_by is not None else [])
        coming = await take_over(
            context.queue,
            types,
            file_ids,
            key,
            priority=context.job.priority,
            requested_by=context.job.requested_by,
            at=context.job.timing,
        )
        taken |= coming.pulled
        for asset_id in coming.files:
            lacking.by_file[asset_id].remove(key)
    # WHAT FINISHED WHILE THE PAGE WAS READ IS NOT HANDED OUT EITHER. The page read what is lacking
    # before it looked at what is coming, so a file's own job that wrote its product and finished
    # between the two reads is neither lacking any more nor coming: handed out, it would be a second
    # task for a thing already made. Read again, after the look, for the files still wanted: a job that
    # was coming at the look was left to finish above, and one that finished before it has written
    # what it made, so this read sees it.
    still = [asset_id for asset_id, wanted in lacking.by_file.items() if wanted]
    if still:
        now = await lacking_on_page(products, keys, still, content)
        for asset_id in still:
            lacking.by_file[asset_id] = [
                key for key in lacking.by_file[asset_id] if key in now.by_file.get(asset_id, ())
            ]
    queued = queued_before + len(
        [asset_id for asset_id in taken if not lacking.by_file.get(asset_id)]
    )
    # EVERY ROW OF THIS RUN AT THE URGENCY THE RUN WAS ASKED FOR, read from this job's own row.
    #
    # A run is a page job that hands out tasks and then asks for the next page, so a press whose
    # urgency reached only the first page would be a press that meant nothing past the first two
    # hundred files: page two and every task it handed out would queue at the ordinary priority,
    # behind whatever a watcher had found in the meantime. The same reading `library_scan` uses for
    # its walks and a probe for its products.
    for asset_id, wanted in lacking.by_file.items():
        if not wanted:
            continue
        await context.raise_if_canceled()
        await context.enqueue_child(
            file_type,
            {"asset_id": asset_id, "products": wanted},
            priority=context.job.priority,
        )
        queued += 1

    done = len(page) < PAGE
    if not done and walked.last is not None:
        following: dict[str, object] = {
            "products": keys,
            "after": list(walked.last),
            "queued": queued,
            "files": files,
        }
        if roots is not None:
            following["roots"] = list(roots)
        await context.enqueue_child(run_type, following, priority=context.job.priority)
    await context.set_progress(1.0)
    await context.set_note(_handed_out(queued=queued, files=files, done=done))
    log.info(
        "importing.build.page",
        queued=queued,
        walked=len(page),
        done=done,
        products=keys,
    )


async def build_file(context: JobContext, *, products: ProductRegistry) -> None:
    """Make every product this file lacks, in the order the sheet lists them, on one read.

    Each product is timed under its own name, and the ledger files those times against the run:
    that record is what the sheet prices the next Build from, per product, on this machine.

    **ONE PRODUCT THAT HAS TO WAIT DOES NOT PARK THE OTHERS.** A product raises `JobBlocked` when
    it needs something no retry can supply (face recognition with its model files not fetched,
    for one), and raising immediately would leave every product after it in the list unattempted
    while the queue said "waiting" about all of them. Every product is attempted; the wait is
    raised at the end, so the row still parks and still comes back the moment the thing it waits
    for arrives, and the products that could be made have been made by then.
    """
    asset_id = str(context.payload["asset_id"])
    wanted = [str(one) for one in context.payload.get("products") or []]
    made = [products.get(key) for key in wanted]
    steps = [one for one in made if one is not None]
    waiting: JobBlocked | None = None
    # A product this machine cannot make just now (a graphics card that stopped answering) is
    # held rather than failed, and like a wait it does not stop the others. See `held_for`.
    holding: Exception | None = None
    settles: dict[str, None] = {}
    # One read of the file for every product, where this machine reads that way quicker; inside,
    # each product reads as it always did and finds its frames already there. See `OnePass`.
    async with products.one_pass.prepared(asset_id, steps):
        for index, product in enumerate(steps):
            await context.raise_if_canceled()
            with timing_hook(BUILD_STAGE_PREFIX + product.key, asset_id=asset_id):
                try:
                    await product.build(context)
                except JobBlocked as blocked:
                    # The first one's words, not the last one's: they all say the same thing when
                    # two products of one family are waiting, and the first is the one a person
                    # reading the row would expect to see named.
                    waiting = waiting or blocked
                    log.info(
                        "importing.build.product_waiting", product=product.key, why=str(blocked)
                    )
                except Exception as error:
                    if held_for(error) is None:
                        raise
                    holding = holding or error
                    log.info("importing.build.product_held", product=product.key, why=str(error))
                else:
                    settles.update(dict.fromkeys(product.settles_into))
            await context.set_progress((index + 1) / len(steps))
    # The passes that read what was just made, asked for once the run's files stop landing.
    if settles:
        await context.queue.settle_into(list(settles))
    await context.set_progress(1.0)
    if holding is not None:
        raise holding
    if waiting is not None:
        raise waiting


def _page_key(value: object) -> tuple[int, str] | None:
    """The walk's key as a payload carries it (`[added_at, id]`), or None to start at the top."""
    if not isinstance(value, list | tuple) or len(value) != 2:
        return None
    added_at, asset_id = value
    if not isinstance(added_at, int) or not isinstance(asset_id, str):
        return None
    return (added_at, asset_id)


def _roots(value: object) -> list[str] | None:
    """The library folders a run was asked for, as its payload carries them; None for a whole run.

    Anything but a list is a whole run, whose payload carries no key."""
    if not isinstance(value, list):
        return None
    return [str(one) for one in value]


def _handed_out(*, queued: int, files: int, done: bool) -> str:
    """What this page did, in a sentence, for whoever pressed the button."""
    if not done:
        return f"{queued:,} of {files:,} files handed out so far, still going through the library\u2026"
    if queued == 0:
        return "Nothing was missing."
    return f"Handed out {queued:,} files to build."


def _page_handler(
    content: ContentStore, products: ProductRegistry, run_type: str, file_type: str
) -> Callable[[JobContext], Awaitable[None]]:
    """One family's page job, with its two job types bound: a closure per family rather than a
    lambda in a loop, which binds the loop variable by name and would hand both families the
    last pair."""

    async def page(context: JobContext) -> None:
        await build(
            context, content=content, products=products, run_type=run_type, file_type=file_type
        )

    return page


def register_handlers(*, content: ContentStore, products: ProductRegistry) -> None:
    """Claim both runs' job types, each under its own family.

    `needs_ready=False` on both, and it is the one place in Sift that says it. Readiness is
    declared per family because a capability belongs to a feature, and the queue holds back a
    family's work while the machine cannot do it, which is right for `face_scan` and wrong for
    these two: they are in the IDENTIFY family because that is the bar a person watches them
    under, and what they actually do is walk the library and hand each file to whichever of its
    products were ticked. An Identify run asked for the MEANING of a library, on a machine whose
    face weights have not arrived, is work that can be done. Each product reads its own switch and
    its own readiness inside, and one that cannot run says so per file.
    """
    for family, (run_type, file_type) in RUNS.items():
        register_handler(
            run_type,
            _page_handler(content, products, run_type, file_type),
            # Named per family, like the file's type below: the Type list on Activity offers each
            # kind by its name, and two choices reading the same words cannot be told apart.
            name=(
                "Checking what to generate"
                if family is Family.GENERATE
                else "Checking what to identify"
            ),
            family=family,
            needs_ready=False,
            carries_products=True,
        )
        register_handler(
            file_type,
            lambda context: build_file(context, products=products),
            # Named per family, because the two carry different products: Generate makes the derived
            # files (thumbnails, previews, fingerprints) and Identify reads faces, meaning and
            # watermarks: one name for both would be untrue of one of them.
            name=(
                "Generating missing thumbnails and fingerprints"
                if family is Family.GENERATE
                else "Identifying file"
            ),
            family=family,
            needs_ready=False,
            carries_products=True,
        )
