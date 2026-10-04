# SPDX-License-Identifier: AGPL-3.0-or-later
"""What is still to come, per kind of job, and whether each pass can run at all."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import partial
from typing import Any

from fastapi import FastAPI

from sift.kernel import wiring
from sift.kernel.content import ContentStore
from sift.kernel.jobs import JobQueue, WorkAhead
from sift.kernel.jobs.families import Family
from sift.kernel.wiring import provide
from sift.slices import faces, importing, media_jobs, music, semantic, stash_migration, watermarks
from sift.wiring.built import Storage, Understanding
from sift.wiring.readiness import meaning_can_run, recognition_can_run


async def _nothing_ahead() -> int:
    """A job type whose rows weigh nothing on the family's bar. See where it is registered."""
    return 0


def _asked_for(payloads: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[str] | None]:
    """The products the live runs of one family are making, and the folders they are over.

    A run over some folders carries them as `roots`; a whole run carries no key. One whole run
    among them means the whole library is ahead, so the folders are None; otherwise the union of
    every run's folders, sorted, which is what a bar over two narrowed runs has left to do.
    """
    keys: list[str] = []
    roots: set[str] = set()
    whole = False
    for payload in payloads:
        for key in payload.get("products", ()):
            if isinstance(key, str) and key not in keys:
                keys.append(key)
        named = payload.get("roots")
        if isinstance(named, list):
            roots.update(one for one in named if isinstance(one, str))
        else:
            whole = True
    return keys, None if whole or not payloads else sorted(roots)


async def _pass_ahead(
    queue: JobQueue, run_type: str, products: importing.ProductRegistry, content: ContentStore
) -> int:
    """How many files the runs of one family still have to make something for.

    Not what has been queued: the pass queues a page at a time, so that count would read "246 of
    246" from the first page to the last. This is the same count the row on Importing shows before
    the button is pressed, for the products of every run of this family the queue is holding, and it
    falls as tasks land.
    """
    keys, roots = _asked_for(await queue.live_payloads(run_type))
    if not keys:
        return 0
    return await importing.files_lacking(products, keys, content, roots=roots)


async def _pass_ahead_by_kind(
    queue: JobQueue,
    run_type: str,
    file_type: str,
    products: importing.ProductRegistry,
    content: ContentStore,
) -> dict[str, int]:
    """What `_pass_ahead` counts, by media kind, and the kind of every task already queued.

    The queued tasks are counted too because a press on chosen files queues its tasks and no run:
    those files may lack nothing, and are remade. One task is one unit of the work left. Their
    files come from the queue as a column (`live_asset_ids`): a run holds thousands of them, and
    this is read twice a second while it goes.
    """
    keys, roots = _asked_for(await queue.live_payloads(run_type))
    split = await importing.lacking_by_kind(products, keys, content, roots=roots) if keys else {}
    tasks = await queue.live_asset_ids(file_type)
    kinds = await content.kinds_of(tasks)
    for asset_id in tasks:
        if (kind := kinds.get(asset_id)) is not None:
            split[kind] = split.get(kind, 0) + 1
    return split


async def _product_left(
    products: importing.ProductRegistry, content: ContentStore, key: str
) -> int:
    """How many files still want this one product, asked of its own switch.

    Through `files_lacking` rather than a count over the derivative table, because that is the one
    place a switch is consulted: a product whose switch is off answers with no condition at all
    and is counted as nothing waiting. Over one key the union IS that key's own count.

    AND THE FILES NOT READ YET, which lack it as surely and which that count cannot see: it reads
    only files that have been read. See `Product.coming`.
    """
    lacking = await importing.files_lacking(products, [key], content)
    product = products.get(key)
    if product is None or product.coming is None or await product.lack() is None:
        return lacking
    return lacking + await product.coming(await products.within(product))


async def _product_left_by_kind(
    products: importing.ProductRegistry, content: ContentStore, key: str
) -> dict[str, int]:
    """`_product_left` by media kind, for the mix the estimate prices. The files not read yet have
    no reading to place them by and are left out of the mix, not the count."""
    return await importing.lacking_by_kind(products, [key], content)


async def _product_wanted(
    products: importing.ProductRegistry, content: ContentStore, key: str
) -> int:
    """How many files want this product at all, done or not. The denominator of its bar.

    The library, when the product is switched on; nought when it is off, because nothing wants
    work nobody asked for and a bar over it would be measuring a pass that is not going to run.
    Filtered by the same folder term the count of what is lacking carries (`products.within`), so
    a folder that refused the work leaves both ends of the bar rather than only the numerator.
    """
    product = products.get(key)
    if product is None or await product.lack() is None:
        return 0
    within = await products.within(product)
    if product.wants is not None:
        return await product.wants(within)
    return await content.asset_count(within)


def _register_products(
    ahead: WorkAhead, products: importing.ProductRegistry, store: Storage
) -> None:
    # THE SAME COUNTS AGAIN, ASKED OF THE PRODUCT'S OWN SWITCH, and the denominator beside each.
    #
    # A count read off the library without asking whether anybody wants the work would count
    # previews and strips that are switched off, and disagree with the Build sheet reading the same
    # library. `Product.lack` is the one switch-aware answer: a product whose switch is off has no
    # condition at all and counts nothing. Registering the same job type again REPLACES a counter:
    # see `WorkAhead.register`.
    #
    # `register_total` is the denominator. What is left falls to nought on a finished library, and
    # with nothing to divide it by the bar would draw empty over work that is entirely done.
    for picture in media_jobs.PICTURES:
        ahead.register(
            picture.job_type, partial(_product_left, products, store.content, picture.key)
        )
        ahead.register_total(
            picture.job_type, partial(_product_wanted, products, store.content, picture.key)
        )
        ahead.register_kinds(
            picture.job_type, partial(_product_left_by_kind, products, store.content, picture.key)
        )
    # And the four that are not pictures, so the Identify row counts every pass it runs, marks
    # included.
    for job_type, product_key in (
        # Under an arriving file's own fingerprint job, which is Generate's: the row that makes them
        # counts them. The catch-up chain is its own row's work and counts from the queue.
        (media_jobs.FINGERPRINT_FILE, "fingerprints"),
        (faces.FACE_SCAN, "faces"),
        (semantic.SEMANTIC_DESCRIBE, "meaning"),
        (watermarks.WATERMARK_READ, "watermarks"),
        (music.AUDIO_FINGERPRINT, music.PRODUCT),
    ):
        ahead.register(job_type, partial(_product_left, products, store.content, product_key))
        ahead.register_total(
            job_type, partial(_product_wanted, products, store.content, product_key)
        )


def _declare_readiness(
    queue: JobQueue, products: importing.ProductRegistry, understanding: Understanding
) -> None:
    # WHETHER EACH PASS CAN RUN AT ALL, declared by the server beside the counters that say how
    # much work each one has. Two answers, wired here because this is the only place both the
    # families and the features that gate them are in scope. The queue holds work and says how
    # fast it is going; whether the machine has a runtime and a set of model files is a fact about
    # a feature.
    #
    # The server declares it rather than the screen working it out: which feature gates which
    # family is a fact about the server's registrations, and a family added here would otherwise
    # read "no estimate" on a screen that had no idea it existed.
    queue.switchboard.declare_ready(
        Family.IDENTIFY, partial(recognition_can_run, understanding.faces)
    )
    queue.switchboard.declare_ready(
        Family.SEMANTIC, partial(meaning_can_run, understanding.semantic)
    )
    queue.switchboard.declare_window(Family.IDENTIFY, products.night_start)


def build_work_ahead(
    app: FastAPI,
    store: Storage,
    queue: JobQueue,
    products: importing.ProductRegistry,
    understanding: Understanding,
) -> None:
    """What is still to come per kind of job, and whether each pass can run at all.

    After the products, because every counter but the walk's is a question asked of one.
    """
    # WHAT IS STILL TO COME, per kind of job, and this is the one place it can be assembled.
    #
    # A progress bar needs a denominator, and the work already in the queue GROWS while a pass
    # runs, so it cannot be the total.
    #
    # None of it needs the disk. Sift already records what has been done to each file, so "what is
    # left" is the library minus that record: indexed counts. The counters are registered rather than written
    # in the queue because each belongs to whoever owns the record it reads: only recognition
    # knows that a file scanned under a shallower setting counts as unscanned, and only Smart Search
    # knows that a file described by a superseded model counts as undescribed.
    #
    # The folder WALK is the one kind with nothing to register, and it needs nothing: a scan builds
    # its whole list before it hands out a single job, so its count exists the moment the walk ends.
    ahead = WorkAhead()
    ahead.register(media_jobs.PROBE, store.content.unread_count)

    # One counter per pass, Generate and Identify, each answering for the runs of its own
    # family the queue is holding. See `_pass_ahead`. The page job of a run weighs nothing: the
    # task counter carries the files, and a queued page counted beside it would be the same files
    # twice.
    for run_type, file_type in importing.RUNS.values():
        ahead.register(file_type, partial(_pass_ahead, queue, run_type, products, store.content))
        ahead.register(run_type, _nothing_ahead)
    # Generate's work split by media kind, so its time left is priced kind by kind: a video's
    # pictures cost a hundred times a photograph's. See `Ledger.estimate`.
    generate_run, generate_file = importing.RUNS[Family.GENERATE]
    ahead.register_kinds(
        generate_file,
        partial(_pass_ahead_by_kind, queue, generate_run, generate_file, products, store.content),
    )
    _register_products(ahead, products, store)
    # What a Stash library kept, weighed by the waiting rows a file here now carries: the pass's
    # own question, counted, and nought without a question to ask when nothing waits.
    ahead.register(
        stash_migration.STASH_ARRIVED,
        wiring.part_of_app(app, stash_migration.SERVICE).arrived_count,
    )
    # The walk is the one kind with no product behind it: there is no record of a file nobody has
    # seen, so what is LEFT cannot be counted and only the denominator can.
    ahead.register_total(media_jobs.PROBE, store.content.asset_count)
    _declare_readiness(queue, products, understanding)

    provide(app, wiring.WORK_AHEAD, ahead)
