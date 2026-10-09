# SPDX-License-Identifier: AGPL-3.0-or-later
"""What is still to come, per kind of job, and whether each pass can run at all."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from functools import partial
from typing import Any

from fastapi import FastAPI

from sift.kernel import wiring
from sift.kernel.content import ContentStore
from sift.kernel.jobs import JobQueue, WorkAhead, by_itself_job_types, registered_families
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.queue_rows import FilesToRead
from sift.kernel.jobs.work_ahead import Split
from sift.kernel.wiring import provide
from sift.slices import faces, importing, media_jobs, music, semantic, stash_migration, watermarks
from sift.wiring.built import Storage, Understanding
from sift.wiring.readiness import meaning_can_run, recognition_can_run


async def _nothing_ahead() -> int:
    """A job type whose rows weigh nothing on the family's bar. See where it is registered."""
    return 0


def _asked_for(payloads: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[str] | None]:
    """The products the live runs of one family are making, and the folders they are over."""
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
    """How many files the runs of one family still have to make something for."""
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
    """What `_pass_ahead` counts, by media kind, and the kind of every task already queued."""
    keys, roots = _asked_for(await queue.live_payloads(run_type))
    split = await importing.lacking_by_kind(products, keys, content, roots=roots) if keys else {}
    tasks = await queue.live_asset_ids(file_type)
    kinds = await content.kinds_of(tasks)
    for asset_id in tasks:
        if (kind := kinds.get(asset_id)) is not None:
            split[kind] = split.get(kind, 0) + 1
    return split


async def _product_left(
    products: importing.ProductRegistry, content: ContentStore, key: str, live: Sequence[str]
) -> Split:
    """How many files still want this one product, per its switch, unread files included."""
    lacking = await importing.files_lacking(products, [key], content)
    product = products.get(key)
    lack = None if product is None else await product.lack()
    if product is None or lack is None:
        return Split(waiting=lacking)
    within = await products.within(product)
    term = replace(lack, product=key, within=within)
    under_way = await content.count_lacking_by_kind([term], among=live) if live else {}
    arriving = {kind: one.files for kind, one in under_way.items()}
    coming = await content.coming_count(key, within)
    return Split(lacking + coming, max(0, lacking - sum(arriving.values())), arriving)


async def _product_left_by_kind(
    products: importing.ProductRegistry, content: ContentStore, key: str
) -> dict[str, int]:
    """`_product_left` by media kind, for the mix the estimate prices."""
    return await importing.lacking_by_kind(products, [key], content)


async def _product_wanted(
    products: importing.ProductRegistry, content: ContentStore, key: str
) -> int:
    """How many files want this product at all, done or not: the denominator of its bar."""
    product = products.get(key)
    if product is None or await product.lack() is None:
        return 0
    return await content.wanting_count(key, await products.within(product))


def _register_products(
    ahead: WorkAhead, products: importing.ProductRegistry, store: Storage
) -> None:
    # The same counts asked of each product's own switch, with the denominator beside each.
    for picture in media_jobs.PICTURES:
        ahead.register_split(
            picture.job_type, partial(_product_left, products, store.content, picture.key)
        )
        ahead.register_total(
            picture.job_type, partial(_product_wanted, products, store.content, picture.key)
        )
        ahead.register_kinds(
            picture.job_type, partial(_product_left_by_kind, products, store.content, picture.key)
        )
    # And the four that are not pictures, so the Identify row counts every pass it runs.
    for job_type, product_key in (
        (media_jobs.FINGERPRINT_FILE, "fingerprints"),
        (faces.FACE_SCAN, "faces"),
        (semantic.SEMANTIC_DESCRIBE, "meaning"),
        (watermarks.WATERMARK_READ, "watermarks"),
        (music.AUDIO_FINGERPRINT, music.PRODUCT),
    ):
        ahead.register_split(job_type, partial(_product_left, products, store.content, product_key))
        ahead.register_total(
            job_type, partial(_product_wanted, products, store.content, product_key)
        )


def _declare_readiness(
    queue: JobQueue, products: importing.ProductRegistry, understanding: Understanding
) -> None:
    # Whether each pass can run at all, declared beside its counters.
    queue.switchboard.declare_ready(
        Family.IDENTIFY, partial(recognition_can_run, understanding.faces)
    )
    queue.switchboard.declare_ready(
        Family.SEMANTIC, partial(meaning_can_run, understanding.semantic)
    )
    queue.switchboard.declare_window(Family.IDENTIFY, products.night_start)


async def _walks_to_read(queue: JobQueue) -> FilesToRead:
    """Not the probes: tens of thousands wait during an import, and none is a walk."""
    quiet = by_itself_job_types()
    walks = [kind for kind, family in registered_families().items() if family is Family.SCAN]
    return await queue.files_to_read([kind for kind in walks if kind not in quiet])


def build_work_ahead(
    app: FastAPI,
    store: Storage,
    queue: JobQueue,
    products: importing.ProductRegistry,
    understanding: Understanding,
) -> None:
    """What is still to come per kind of job, and whether each pass can run at all."""
    # Each counter belongs to whoever owns the record it reads.
    ahead = WorkAhead(live_files=queue.live_files, to_read=partial(_walks_to_read, queue))
    ahead.register(media_jobs.PROBE, store.content.unread_count)

    # One counter per pass; a run's page job weighs nothing, or its files would count twice.
    for run_type, file_type in importing.RUNS.values():
        ahead.register(file_type, partial(_pass_ahead, queue, run_type, products, store.content))
        ahead.register(run_type, _nothing_ahead)
    # Generate's work split by media kind: a video's pictures cost a hundred times a photograph's.
    generate_run, generate_file = importing.RUNS[Family.GENERATE]
    ahead.register_kinds(
        generate_file,
        partial(_pass_ahead_by_kind, queue, generate_run, generate_file, products, store.content),
    )
    _register_products(ahead, products, store)
    ahead.register(
        stash_migration.STASH_ARRIVED,
        wiring.part_of_app(app, stash_migration.SERVICE).arrived_count,
    )
    # The walk has no product behind it, so only its denominator can be counted.
    ahead.register_total(media_jobs.PROBE, store.content.asset_count)
    _declare_readiness(queue, products, understanding)

    provide(app, wiring.WORK_AHEAD, ahead)
