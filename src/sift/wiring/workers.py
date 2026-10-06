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
    """Which products each job type is the maker of, from each product's own job type.

    `Product.governed_by` is that type (the one an arriving file's work for the product is queued
    as, which is why the import policy gates it), so a `face_scan` run is a run for `faces`.
    """
    makers: dict[str, tuple[str, ...]] = {}
    for one in products:
        if one.governed_by is not None:
            makers[one.governed_by] = (*makers.get(one.governed_by, ()), one.key)
    return makers


def _less(counts: dict[str, int], held: dict[str, int]) -> dict[str, int]:
    """Counts of work less what a family's hold keeps waiting: held is not due."""
    left = {kind: count - held.get(kind, 0) for kind, count in counts.items()}
    return {kind: count for kind, count in left.items() if count > 0}


class _PoolConfig:
    """How many workers, and the per-type caps, as the performance settings currently say.

    This is the one place the tuning numbers are turned into the shape the pool wants, because
    it is the one place that may know both halves: the settings (via the hub) and which job
    types a setting governs (the constants below). The pool polls this and converges on it, so a
    change on the Performance screen takes hold within a few seconds and no restart.

    `0` on any of these means "let Sift decide", resolved here against the hardware the pool was
    sized from. The base caps are the two slices' own (`transcode: 1`, not a knob; preview and
    sprite at half the workers), and an explicit generation or scan setting steps over the
    relevant one. A scan setting that is left automatic adds no entry at all, so scans stay
    bounded only by the worker count.
    """

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
        # Read first: every per-kind share below is a share OF the count that actually runs.
        step_back = bool(await get_app(performance.STEP_BACK_KEY))
        share = performance.resolve_step_back_share(await get_app(performance.STEP_BACK_SHARE_KEY))
        busy_on = bool(await get_app(performance.BUSY_STEP_BACK_KEY))
        measuring = self._measuring()
        # Read whatever the setting says, so its log shows what it would have done.
        others = device_load.READER.tick(acting=busy_on and not measuring)
        concurrency = attention.ATTENTION.workers(
            full,
            step_back=step_back,
            share=share,
            others_busy=busy_on and others,
            measuring=measuring,
        )
        share_reads = await self._resize_shared(full, concurrency)
        limits = {
            # The accelerator's answer, not the report's: the cap for a card is three segments at
            # once and the cap for the processor is ONE, because two processor encodes do not split
            # the machine, they fight over it. This runs on a timer, so the cap follows a fall back
            # within a tick.
            **player.job_limits(self._accelerator.encoder),
        }
        generation = await self._own_caps(limits, concurrency)
        unfinished = _less(
            await self._queue.due_by_type(), await self._queue.held_for_family_by_type()
        )
        limits.update(await self._divided(concurrency))
        # The ledger closes the runs whose family has nothing left that is due (`due_by_type`: a
        # row put off to a later moment begins the next run), and writes the open ones through. The
        # settings it records beside a run are the three that decide its pace.
        await self._book.settle(
            unfinished,
            settings={
                importing.JOBS_AT_ONCE: full,
                "previews at once": generation,
                "network share reads": share_reads,
            },
            stepped_back=attention.ATTENTION.holding,
        )
        return concurrency, limits

    async def _resize_shared(self, full: int, concurrency: int) -> int:
        """Size what the workers share from the counts; the network share reads, for the ledger."""
        # And each background tool's threads, from the same share, held by the operating system
        # while the share is less than the whole device. See `media.set_share`.
        if media.set_share(running=concurrency, percent=attention.ATTENTION.share_now):
            log.info(
                "media.device_share.resized",
                workers=concurrency,
                percent=attention.ATTENTION.share_now,
            )
        # The shared thread pool is sized from the workers here, on the same timer, because raising
        # the worker count without raising the pool leaves the pool as the real ceiling and no
        # screen anywhere saying so. A no-op unless the number actually moved.
        if size_shared_pool(full):
            log.info("threads.shared.resized", workers=full)
        # And the share of the machine one background ffmpeg may take, on the same timer and for
        # the same reason as the two below: it divides the cores by how many jobs run at once, so
        # it must read the number set here, not the one chosen at boot. See `media.jobs_at_once`.
        if media.set_jobs_at_once(full):
            log.info("media.thread_share.resized", workers=full)
        # And the database's read connections, so the browser is left one to borrow (`readers_for`).
        database = self._store.database
        if await database.resize_readers(readers_for(full)):
            log.info("db.readers.resized", readers=database.readers, workers=full)
        # And how many files may be read at once from each network share, on the same timer. The
        # cap is the storage's, not the job count's (see `kernel.lanes`), and it is the setting
        # a NAS library needs where a local one needs none. Read through the installed lanes rather
        # than a handle of this function's own, because the reads go through the installed ones.
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

    async def _own_caps(self, limits: dict[str, int], concurrency: int) -> int:
        """The caps outside the division below; the previews' cap, for the ledger."""
        get_app = self._get_app
        # Preview/sprite are always (re)set from the effective worker count (an explicit setting,
        # or half the workers when automatic), so the encoding cap tracks the worker count above it
        # rather than the hardware default the merge started from.
        #
        # A CEILING, NOT AN ENTITLEMENT IN THE DIVISION BELOW: a throughput knee, the point past
        # which another simultaneous encode finishes no more work, written by the self-test from
        # the encode ladder it ran; idle shares handed to it would only put more encodes on a full
        # processor. An install the self-test has never run on stores nothing and gets half.
        generation = performance.resolve_generation_limit(
            await get_app(performance.GENERATION_LIMIT_KEY), concurrency
        )
        limits[media_jobs.PREVIEW] = generation
        limits[media_jobs.SPRITE] = generation
        # How many downloads at once. Outside the shared budget below: a download waits on somebody
        # else's server rather than on this machine, so a share-of-processor cap would slow the one
        # thing here that is not processor-bound and free nothing worth having. Left at 0 there is
        # no entry at all and downloads share the worker count.
        downloads_at_once = download.at_once_limit(
            await get_app(download.AT_ONCE_KEY), await get_app(download.PAUSED_KEY)
        )
        if downloads_at_once is not None:
            limits[download.DOWNLOAD] = downloads_at_once
        return generation

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
            # The pressed run's share: the same control as a scan's recognition. WHEN either may run
            # is not a cap (quiet hours hold unpressed work back at the claim, and a press is
            # never held), so the two read the same numbers.
            importing.IDENTIFY_FILE: await self._face_scans(concurrency)
            if faces_on
            else performance.resolve_describe_share(concurrency),
            semantic.SEMANTIC_DESCRIBE: performance.resolve_describe_share(concurrency),
            # Probing, which is the bulk of what an import costs. Inside the division so it cannot
            # hold every worker while recognition and describing wait behind it; on a machine that
            # is only importing, probe is the only claimant and still gets all of it. See
            # `PROBE_SHARE`.
            media_jobs.PROBE: performance.resolve_probe_share(concurrency),
            # The thumbnail, which a person is waiting on as a grid fills in. Counted so that the
            # passes make room for it while it has work, and never capped: see `waited_on` below
            # and `THUMBNAIL_SHARE`.
            media_jobs.THUMBNAIL: performance.resolve_thumbnail_share(concurrency),
            # An arriving file's fingerprints: the decode the read carries elsewhere, so the share
            # it is given there. See `FINGERPRINT_SHARE`.
            media_jobs.FINGERPRINT_FILE: performance.resolve_fingerprint_share(concurrency),
            library_roots.SCAN: performance.resolve_scan_share(
                await self._get_app(performance.SCAN_LIMIT_KEY), concurrency
            ),
            # Compressing files. Processor-bound and hour-long over a big selection, so it belongs
            # in the division rather than outside it the way downloads are: a download waits on
            # somebody else's server and competes for nothing here. No second cap on top of this:
            # the whole point of the division is that a share nobody is using goes to whoever is
            # using theirs, and a fixed ceiling would take that back.
            media_edit.COMPRESS: performance.resolve_compress_share(concurrency),
        }

    async def _divided(self, concurrency: int) -> dict[str, int]:
        """The three long passes, and the machine they share.

        Scanning folders, recognising faces and describing pictures each read a whole library and
        take hours doing it. They are the only work that genuinely competes for the machine, and
        this is the only place their shares can be worked out together: a slice never imports
        another slice, so nothing inside recognition can know describing exists.

        What each is ENTITLED to comes from the settings, so the controls keep meaning what they
        say. What each is ALLOWED right now comes from dividing those entitlements against whoever
        actually has work: see `kernel.budget`. A share nobody is using goes to whoever is using
        theirs, and comes back within a few seconds when that pass has work again.
        """
        get_app = self._get_app
        faces_on = bool(await get_app(faces.ENABLED_KEY))
        entitlements = await self._entitlements(concurrency, faces_on)
        # DIVIDED ON WHAT CAN RUN. Work quiet hours are holding back asks for none of the machine
        # until the range opens, and counting it would keep workers aside for recognition all day
        # while the work that can run is given less: see `JobQueue.demand_by_type`.
        # A number whose label calls it a limit IS one: a fixed kind gets exactly its own count,
        # never raised by an idle share and never cut by competition.
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
    """The worker pool, sized from the settings and kept there. BUILT here, started by the caller.

    NOT STARTED HERE. A worker claims anything the moment it exists, and the backup's handler is
    registered AFTER this (the backup needs the pool to stop the workers for a restore), so a
    backup that fell due while Sift was closed would be claimed before its handler existed and fail
    for good as "no longer exists in this version". The lifespan starts the pool once every handler
    is registered, and a gate (`tests/gates/test_workers_start_after_every_handler.py`) holds that
    order.
    """

    # What each run costs, written down. Built before the pool so the pool can hand it every job
    # that starts and ends, and before the settings reader so the reader can settle its runs on
    # the same read of the queue the machine budget already makes.
    book = Ledger(
        store.database,
        machine=hardware.profile_label,
        profile=hardware.profile,
        version=app_version(),
        families_of=registered_families(),
        accelerator=lambda: accelerator.state,
        # Which product each maker's job type is for, so a run records the products it was for:
        # the one declaration of a product's own job type (`Product.governed_by`), read here too.
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

    # Read once at boot so the pool starts at the settings' numbers rather than the automatic ones
    # and then correcting itself a few seconds later.
    initial_concurrency, initial_limits = await read_pool_config()
    settings = get_settings()
    pool = WorkerPool(
        queue,
        concurrency=initial_concurrency,
        capabilities=SystemCapabilities(
            content=store.content,
            library=store.library,
            secrets=download.AdminMasterKey(store.database, master_keys),
            # Under the cache directory, because what is in it is work in progress and a machine
            # that lost it would lose a part-fetched download and nothing else. One directory per
            # job, kept while the job is (see `kernel/jobs/workspaces.py`), which is what lets a
            # paused download keep the bytes it had already written.
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
