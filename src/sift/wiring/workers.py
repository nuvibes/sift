# SPDX-License-Identifier: AGPL-3.0-or-later
"""The worker pool, sized from the settings and kept there, and the ledger of what each run cost."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from fastapi import FastAPI

from sift.kernel import attention, budget, device_load, lanes, media, wiring
from sift.kernel.config import get_settings
from sift.kernel.db import readers_for
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import (
    JobQueue,
    SystemCapabilities,
    WorkerPool,
    Workspaces,
    registered_families,
)
from sift.kernel.jobs.ledger import Ledger
from sift.kernel.log import get_logger, set_stage_sink
from sift.kernel.threads import size_shared_pool
from sift.kernel.version import app_version
from sift.kernel.wiring import provide
from sift.slices import (
    auth,
    download,
    faces,
    importing,
    library_roots,
    media_edit,
    media_jobs,
    performance,
    player,
    semantic,
    settings_hub,
)
from sift.wiring.built import Storage

log = get_logger(__name__)


def _makers_of(products: importing.ProductRegistry) -> dict[str, tuple[str, ...]]:
    """Which products each job type makes, from each product's own `governed_by` type."""
    makers: dict[str, tuple[str, ...]] = {}
    for one in products:
        if one.governed_by is not None:
            makers[one.governed_by] = (*makers.get(one.governed_by, ()), one.key)
    return makers


def pace_settings(full: int, previews: object, share_reads: int) -> dict[str, object]:
    """The settings a pace belongs to, at the full worker count: stepping back is not a change."""
    return {
        importing.JOBS_AT_ONCE: full,
        "previews together": performance.resolve_generation_limit(previews, full),
        "network share reads": share_reads,
    }


class _PoolConfig:
    """Workers and per-type caps as the performance settings say, 0 resolved; polled by the pool."""

    def __init__(
        self,
        store: Storage,
        queue: JobQueue,
        hardware: HardwareReport,
        hub: settings_hub.SettingsService,
        accelerator: media.Accelerator,
        book: Ledger,
        measuring: Callable[[], bool] = lambda: False,
        measured_reads: Callable[[], Awaitable[Mapping[str, int]]] | None = None,
    ) -> None:
        self._store = store
        self._queue = queue
        self._hardware = hardware
        self._get_app = hub.get_app
        self._accelerator = accelerator
        self._book = book
        self._measuring = measuring
        self._measured_reads = measured_reads

    async def __call__(self) -> tuple[int, dict[str, int]]:
        get_app = self._get_app
        full = performance.resolve_worker_count(
            await get_app(performance.WORKER_COUNT_KEY), self._hardware
        )
        # Read first: every per-kind share below is a share of the count that actually runs.
        step_back = bool(await get_app(performance.STEP_BACK_KEY))
        share = performance.resolve_step_back_share(await get_app(performance.STEP_BACK_SHARE_KEY))
        busy_on = bool(await get_app(performance.BUSY_STEP_BACK_KEY))
        measuring = self._measuring()
        # Read whatever the setting says, so its log shows what it would have done.
        others = await device_load.READER.tick_off_loop(acting=busy_on and not measuring)
        concurrency = attention.ATTENTION.workers(
            full,
            step_back=step_back,
            share=share,
            others_busy=busy_on and others,
            measuring=measuring,
        )
        share_reads = await self._resize_shared(full, concurrency)
        limits = {
            # The accelerator's cap: three on a card, one on the processor, where two encodes fight.
            **player.job_limits(self._accelerator.encoder),
        }
        await self._own_caps(limits, concurrency)
        unfinished = await self._queue.due_by_type()
        limits.update(await self._divided(concurrency))
        # The ledger closes runs whose family has nothing due and records the three pace settings.
        previews = await get_app(performance.GENERATION_LIMIT_KEY)
        await self._book.settle(
            unfinished,
            settings=pace_settings(full, previews, share_reads),
            stepped_back=attention.ATTENTION.holding,
        )
        return concurrency, limits

    async def _resize_shared(self, full: int, concurrency: int) -> int:
        """Size what the workers share from the counts; the network share reads, for the ledger."""
        # And each tool's threads, from the same share (`media.set_share`).
        if media.set_share(running=concurrency, percent=attention.ATTENTION.share_now):
            log.info(
                "media.device_share.resized",
                workers=concurrency,
                percent=attention.ATTENTION.share_now,
            )
        # The shared thread pool follows the workers, or it becomes the unseen ceiling.
        if size_shared_pool(full):
            log.info("threads.shared.resized", workers=full)
        # And one ffmpeg's share of the machine, from the number set here (`media.jobs_at_once`).
        if media.set_jobs_at_once(full):
            log.info("media.thread_share.resized", workers=full)
        # And the database's read connections, so the browser is left one to borrow (`readers_for`).
        database = self._store.database
        if await database.resize_readers(readers_for(full)):
            log.info("db.readers.resized", readers=database.readers, workers=full)
        # And the reads at once per network share (`kernel.lanes`), through the installed lanes.
        storage_lanes = lanes.installed()
        share_reads = performance.resolve_share_reads(
            await self._get_app(performance.SHARE_READS_KEY)
        )
        measured = await self._measured_reads() if self._measured_reads is not None else None
        if storage_lanes is not None and await storage_lanes.configure(
            network_reads_at_once=share_reads, measured=measured
        ):
            log.info("lanes.configured", network_reads_at_once=share_reads, measured=measured)
        return share_reads

    async def _own_caps(self, limits: dict[str, int], concurrency: int) -> None:
        """The caps outside the division below."""
        get_app = self._get_app
        # Preview and sprite follow the effective worker count: a ceiling, not an entitlement below.
        generation = performance.resolve_generation_limit(
            await get_app(performance.GENERATION_LIMIT_KEY), concurrency
        )
        limits[media_jobs.PREVIEW] = generation
        limits[media_jobs.SPRITE] = generation
        # Downloads sit outside the shared budget: they wait on another server, not this machine.
        downloads_at_once = download.at_once_limit(
            await get_app(download.AT_ONCE_KEY), await get_app(download.PAUSED_KEY)
        )
        if downloads_at_once is not None:
            limits[download.DOWNLOAD] = downloads_at_once

    async def _face_scans(self, concurrency: int) -> int:
        get_app = self._get_app
        return faces.scans_at_once(
            budget=str(await get_app(faces.BUDGET_KEY)),
            core_share=int(await get_app(faces.CORE_SHARE_KEY) or 0),
            threads=int(await get_app(faces.THREAD_COUNT_KEY) or 0),
            workers=concurrency,
        )

    async def _entitlements(self, concurrency: int, faces_on: bool) -> dict[str, int]:
        return {
            faces.FACE_SCAN: await self._face_scans(concurrency) if faces_on else 0,
            # The pressed run's share is the same control as a scan's recognition.
            importing.IDENTIFY_FILE: await self._face_scans(concurrency)
            if faces_on
            else performance.resolve_describe_share(concurrency),
            semantic.SEMANTIC_DESCRIBE: performance.resolve_describe_share(concurrency),
            # Probing, inside the division so it cannot hold every worker (`PROBE_SHARE`).
            media_jobs.PROBE: performance.resolve_probe_share(concurrency),
            # The thumbnail, counted so the passes make room and never capped (`THUMBNAIL_SHARE`).
            media_jobs.THUMBNAIL: performance.resolve_thumbnail_share(concurrency),
            # An arriving file's fingerprints (`FINGERPRINT_SHARE`).
            media_jobs.FINGERPRINT_FILE: performance.resolve_fingerprint_share(concurrency),
            library_roots.SCAN: performance.resolve_scan_share(
                await self._get_app(performance.SCAN_LIMIT_KEY), concurrency
            ),
            # Compressing files, in the division: a share nobody uses goes to whoever is using
            # theirs.
            media_edit.COMPRESS: performance.resolve_compress_share(concurrency),
        }

    async def _divided(self, concurrency: int) -> dict[str, int]:
        """The three long passes and the machine they share, divided by `kernel.budget`."""
        get_app = self._get_app
        faces_on = bool(await get_app(faces.ENABLED_KEY))
        entitlements = await self._entitlements(concurrency, faces_on)
        # Divided on what can run: held work asks for nothing, and a typed limit is exact.
        fixed: set[str] = set()
        if performance.scan_limit_is_fixed(await get_app(performance.SCAN_LIMIT_KEY)):
            fixed.add(library_roots.SCAN)
        if (
            faces_on
            and str(await get_app(faces.BUDGET_KEY)) == "threads"
            and int(await get_app(faces.THREAD_COUNT_KEY) or 0) > 0
        ):
            fixed |= {faces.FACE_SCAN, importing.IDENTIFY_FILE}
        return budget.divide(
            workers=concurrency,
            entitlements=entitlements,
            busy=budget.unfinished_by_type(await self._queue.demand_by_type(), entitlements),
            fixed=fixed,
            waited_on={media_jobs.THUMBNAIL},
        )


async def build_workers(
    app: FastAPI,
    store: Storage,
    queue: JobQueue,
    hardware: HardwareReport,
    hub: settings_hub.SettingsService,
    master_keys: auth.MasterKeyStore,
    accelerator: media.Accelerator,
) -> WorkerPool:
    """The worker pool, built here and started by the caller once every handler exists."""

    # What each run costs, built before the pool and the settings reader that feed it.
    book = Ledger(
        store.database,
        machine=hardware.profile_label,
        profile=hardware.profile,
        version=app_version(),
        families_of=registered_families(),
        accelerator=lambda: accelerator.state,
        # Which product each maker's job type is for, from `Product.governed_by`.
        products_of=_makers_of(wiring.part_of_app(app, importing.PRODUCTS)),
    )
    await book.start()
    set_stage_sink(book.stage)
    provide(app, wiring.LEDGER, book)
    book.first_prices = wiring.part_of_app(app, performance.SELF_TEST_RUNNER).prices

    def measuring() -> bool:
        runner = wiring.part_of_app_or_none(app, performance.SELF_TEST_RUNNER)
        return runner is not None and runner.state.running

    async def measured_reads() -> dict[str, int]:
        runner = wiring.part_of_app_or_none(app, performance.SELF_TEST_RUNNER)
        kept = await runner.rates() if runner is not None else None
        return kept.reads_at_once() if kept is not None else {}

    read_pool_config = _PoolConfig(
        store, queue, hardware, hub, accelerator, book, measuring, measured_reads
    )

    # Read once at boot so the pool starts at the settings' numbers.
    initial_concurrency, initial_limits = await read_pool_config()
    settings = get_settings()
    pool = WorkerPool(
        queue,
        concurrency=initial_concurrency,
        capabilities=SystemCapabilities(
            content=store.content,
            library=store.library,
            secrets=download.AdminMasterKey(store.database, master_keys),
            # Under the cache directory: one per job, so a paused download keeps its bytes.
            workspaces=Workspaces(settings.cache_dir / "jobs"),
        ),
        limits=initial_limits,
        read_config=read_pool_config,
        ledger=book,
        # A press for turbo mode or eco mode takes effect now, not at the next reconfigure.
        woken_by=(attention.ATTENTION.listen,),
    )
    provide(app, wiring.QUEUE, queue)
    provide(app, wiring.POOL, pool)
    return pool
