# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Build: one pass over the library, a page per job, handing out one task per file.

One task per file makes every ticked product the file lacks on a single read of it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from sift.kernel.content import ContentStore
from sift.kernel.jobs import JobBlocked, JobContext, held_for, register_handler
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.ledger import BUILD_STAGE_PREFIX
from sift.kernel.log import get_logger, timing_hook
from sift.slices.importing.coming import take_over
from sift.slices.importing.products import PAGE, Lacking, ProductRegistry, lacking_on_page

log = get_logger(__name__)

GENERATE = "generate"
GENERATE_FILE = "generate_file"
IDENTIFY = "identify"
IDENTIFY_FILE = "identify_file"

#: The two runs, by family: the page job that walks the library and the task it hands out per file.
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
    """Hand out one task per file for one page of the library, then ask for the next page."""
    keys = [str(one) for one in context.payload.get("products") or [] if products.get(str(one))]
    roots = _roots(context.payload.get("roots"))
    # A page queued with an offset instead of a key starts again from the top, which is harmless.
    after = _page_key(context.payload.get("after"))
    first = after is None and not context.payload.get("offset")
    queued_before = int(context.payload.get("queued") or 0)
    files = int(context.payload.get("files") or 0)
    if not keys:
        await context.set_progress(1.0)
        await context.set_note("Nothing was ticked, so there's nothing to build.")
        log.info("importing.build.nothing_ticked")
        return

    # A machine never measured asks for the benchmark first and queues this page again behind it.
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
    taken = await _take_over_coming(context, products, keys, lacking, file_type)
    await _drop_finished(products, keys, lacking, content)
    queued = queued_before + len(
        [asset_id for asset_id in taken if not lacking.by_file.get(asset_id)]
    )
    # Every row of the run at the urgency the run was asked for, read from this job's own row.
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


async def _take_over_coming(
    context: JobContext,
    products: ProductRegistry,
    keys: list[str],
    lacking: Lacking,
    file_type: str,
) -> set[str]:
    # Work already coming for a file is not handed out again: waiting work becomes this run's.
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
    return taken


async def _drop_finished(
    products: ProductRegistry, keys: list[str], lacking: Lacking, content: ContentStore
) -> None:
    # A file whose own job finished between the two reads is neither lacking nor coming: read again.
    still = [asset_id for asset_id, wanted in lacking.by_file.items() if wanted]
    if still:
        now = await lacking_on_page(products, keys, still, content)
        for asset_id in still:
            lacking.by_file[asset_id] = [
                key for key in lacking.by_file[asset_id] if key in now.by_file.get(asset_id, ())
            ]


async def build_file(context: JobContext, *, products: ProductRegistry) -> None:
    """Make every product this file lacks on one read; one that must wait does not park the rest."""
    asset_id = str(context.payload["asset_id"])
    wanted = [str(one) for one in context.payload.get("products") or []]
    made = [products.get(key) for key in wanted]
    steps = [one for one in made if one is not None]
    waiting: JobBlocked | None = None
    # A product this machine cannot make just now is held rather than failed (`held_for`).
    holding: Exception | None = None
    settles: dict[str, None] = {}
    async with products.one_pass.prepared(asset_id, steps):
        for index, product in enumerate(steps):
            await context.raise_if_canceled()
            with timing_hook(BUILD_STAGE_PREFIX + product.key, asset_id=asset_id):
                try:
                    await product.build(context)
                except JobBlocked as blocked:
                    # The first one's words: the one a person would expect to see named.
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
    """The library folders a run was asked for; None for a whole run."""
    if not isinstance(value, list):
        return None
    return [str(one) for one in value]


def _handed_out(*, queued: int, files: int, done: bool) -> str:
    """What this page did, in a sentence, for whoever pressed the button."""
    if not done:
        return f"Handed out {queued:,} of {files:,} files."
    if queued == 0:
        return "Nothing was missing."
    return f"Handed out {queued:,} files to build."


def _page_handler(
    content: ContentStore, products: ProductRegistry, run_type: str, file_type: str
) -> Callable[[JobContext], Awaitable[None]]:
    """One family's page job with its two job types bound, as a closure, not a loop lambda."""

    async def page(context: JobContext) -> None:
        await build(
            context, content=content, products=products, run_type=run_type, file_type=file_type
        )

    return page


def register_handlers(*, content: ContentStore, products: ProductRegistry) -> None:
    """Claim both runs' job types, each under its own family; neither waits on readiness."""
    for family, (run_type, file_type) in RUNS.items():
        register_handler(
            run_type,
            _page_handler(content, products, run_type, file_type),
            # Named per family, so the Type list on Activity can tell the two apart.
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
            # Named per family, because the two carry different products.
            name=(
                "Generating missing thumbnails and fingerprints"
                if family is Family.GENERATE
                else "Identifying file"
            ),
            family=family,
            needs_ready=False,
            carries_products=True,
        )
