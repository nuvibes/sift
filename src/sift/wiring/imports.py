# SPDX-License-Identifier: AGPL-3.0-or-later
"""What happens to a file as it lands, and the off switches for the whole-library passes."""

from __future__ import annotations

from functools import partial
from pathlib import Path

from fastapi import FastAPI

from sift.kernel import media, wiring
from sift.kernel.config import Settings
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import JobQueue, Switch
from sift.kernel.jobs.schedules import when_key
from sift.kernel.wiring import provide
from sift.slices import (
    capture,
    dedup,
    faces,
    importing,
    library_roots,
    media_jobs,
    music,
    performance,
    photo_sets,
    search,
    semantic,
    settings_hub,
    shoots,
    stash_boxes,
    stash_migration,
    suggestions,
    tidy,
    watermarks,
)
from sift.slices.importing.jobs import ARRIVED
from sift.wiring.built import Storage
from sift.wiring.tasks import starts_on_its_own


def _folder_grouper(
    app: FastAPI, store: Storage, hub: settings_hub.SettingsService
) -> library_roots.FolderSettled:
    """Turn a folder of pictures into a photo set, if the preference says to."""

    async def settled(folder_id: str, name: str) -> None:
        if not await hub.get_app(photo_sets.FOLDER_SETS_KEY):
            return
        await photo_sets.set_from_folder(
            folder_id,
            name=name,
            content=store.content,
            service=wiring.part_of_app(app, photo_sets.SERVICE),
        )

    return settled


def _archive_grouper(
    app: FastAPI, store: Storage, hub: settings_hub.SettingsService
) -> library_roots.ArchiveSettled:
    """Turn a ZIP of pictures into a photo set, if the preference says to."""

    async def settled(root_id: str, rel_path: str, name: str, asset_ids: list[str]) -> None:
        if not await hub.get_app(photo_sets.ARCHIVE_SETS_KEY):
            return
        await photo_sets.set_from_archive(
            asset_ids,
            root_id=root_id,
            rel_path=rel_path,
            name=name,
            content=store.content,
            service=wiring.part_of_app(app, photo_sets.SERVICE),
        )

    return settled


def _generate_keys() -> dict[str, tuple[str, ...]]:
    # What a scan starts and every switch each job needs on, read per file; unnamed types always
    # run.
    return {
        media_jobs.PREVIEW: (importing.GENERATE_KEY, performance.GENERATE_PREVIEWS_KEY),
        media_jobs.SPRITE: (importing.GENERATE_KEY, performance.GENERATE_SPRITES_KEY),
        media_jobs.FINGERPRINT_FOR_STASH_BOXES: (
            importing.GENERATE_KEY,
            performance.GENERATE_FINGERPRINTS_KEY,
        ),
        # An arriving file's own fingerprint job, under the same two.
        media_jobs.FINGERPRINT_FILE: (
            importing.GENERATE_KEY,
            performance.GENERATE_FINGERPRINTS_KEY,
        ),
        # A whole second copy of a file, so its switch is about disk rather than processor time.
        media_jobs.REMUX: (importing.GENERATE_KEY, performance.REPAIR_PLAYBACK_KEY),
        # Started by the same stage and governed the same way; each names its feature's switch
        # first.
        faces.FACE_SCAN: (
            importing.IDENTIFY_KEY,
            faces.ENABLED_KEY,
            performance.SCAN_FACES_ON_IMPORT_KEY,
        ),
        semantic.SEMANTIC_DESCRIBE: (
            importing.IDENTIFY_KEY,
            semantic.ENABLED_KEY,
            semantic.DESCRIBE_ON_IMPORT_KEY,
        ),
        # Reading a file for a mark: off out of the box, since this pass opens the file.
        watermarks.WATERMARK_READ: (
            importing.IDENTIFY_KEY,
            watermarks.ENABLED_KEY,
            watermarks.READ_ON_IMPORT_KEY,
        ),
        # Reading a file's sound, asked of the destination folder at arrival; off out of the box.
        music.AUDIO_FINGERPRINT: (importing.GENERATE_KEY, music.FINGERPRINT_KEY),
    }


#: The identify products a file is read for as it lands, each with the job its switches are
#: asked under: the three passes `build_imports` hands out as one task.
ON_ARRIVAL: tuple[tuple[str, str], ...] = (
    (faces.PRODUCT, faces.FACE_SCAN),
    ("meaning", semantic.SEMANTIC_DESCRIBE),
    (watermarks.PRODUCT, watermarks.WATERMARK_READ),
)


async def arriving_products(policy: importing.ImportPolicy, asset_id: str) -> list[str]:
    """What the import switches want made of this file as it lands, asked the arrival way (the
    task starts on its own), so a folder's own answer counts."""
    return [key for key, job_type in ON_ARRIVAL if await policy.allows(job_type, asset_id)]


def _import_policy(
    app: FastAPI, store: Storage, hub: settings_hub.SettingsService
) -> importing.ImportPolicy:
    # The gate itself, so the screen and the catch-up ask the one copy.
    policy = importing.ImportPolicy(
        settings=hub,
        content=store.content,
        roots=wiring.part_of_app(app, importing.ROOT_PREFS),
        gates=_generate_keys(),
        # What one folder may answer differently: everything but the two consent switches.
        overridable=(
            importing.GENERATE_KEY,
            importing.IDENTIFY_KEY,
            performance.GENERATE_PREVIEWS_KEY,
            performance.GENERATE_SPRITES_KEY,
            performance.GENERATE_FINGERPRINTS_KEY,
            performance.REPAIR_PLAYBACK_KEY,
            performance.SCAN_FACES_ON_IMPORT_KEY,
            semantic.DESCRIBE_ON_IMPORT_KEY,
            watermarks.READ_ON_IMPORT_KEY,
            music.FINGERPRINT_KEY,
        ),
    )
    provide(app, importing.SERVICE, policy)
    return policy


def _register_media(
    app: FastAPI,
    settings: Settings,
    hardware: HardwareReport,
    hub: settings_hub.SettingsService,
    policy: importing.ImportPolicy,
    accelerator: media.Accelerator,
) -> None:
    async def should_generate(job_type: str, asset_id: str | None = None) -> bool:
        if job_type == importing.IDENTIFY_FILE:
            # One task for the three passes, handed out where any of them is wanted for the file.
            return bool(asset_id) and bool(await arriving_products(policy, str(asset_id)))
        return await policy.allows(job_type, asset_id)

    async def read_rates(path: Path) -> media.ReadRates | None:
        """This machine's measured rates for reading a file there, the Build's own answer
        (`OnePassReader`); asked of the self-test's runner when a file lands, not at wiring."""
        machine = wiring.part_of_app(app, performance.SELF_TEST_RUNNER)
        return await machine.read_rates(path)

    async def chosen_shape() -> str:
        """Which preview shape somebody picked. Read per file, like the switches above."""
        return str(await hub.get_app(performance.PREVIEW_SHAPE_KEY))

    media_jobs.register_handlers(
        settings=settings,
        hardware=hardware,
        should_generate=should_generate,
        chosen_shape=chosen_shape,
        # So an arriving short video's strip and fingerprints share one decode where that is
        # cheaper on this machine.
        read_rates=read_rates,
        # The same object the player holds: one judgement about whether the card is working.
        accelerator=accelerator,
        # Recognition, describing and the watermark read ride the import stage as one task per
        # file, which reads the file once for all three (`ARRIVED`: the switches are asked again
        # when it runs); the music claim beside it. The switch is asked here so no idle job is
        # queued.
        follow_on=(importing.IDENTIFY_FILE, music.AUDIO_FINGERPRINT),
        follow_on_payloads={
            importing.IDENTIFY_FILE: {ARRIVED: True},
            music.AUDIO_FINGERPRINT: {music.CLAIM_ONLY: True},
        },
        # The folder pass, once per folder rather than once per file that landed in it.
        settles_into=(suggestions.SUGGESTION_SCAN, stash_migration.STASH_ARRIVED),
        # The duplicate, stash-box and Stash sweeps, asked for once the last fingerprints are
        # written.
        fingerprints_settle_into=(
            dedup.DEDUP_SCAN,
            stash_boxes.STASH_SWEEP,
            stash_migration.STASH_ARRIVED,
        ),
    )


def _register_library(
    app: FastAPI,
    settings: Settings,
    store: Storage,
    library_service: library_roots.LibraryService,
    hub: settings_hub.SettingsService,
    queue: JobQueue,
) -> None:
    # Read off the application: it is built with the downloader, one step earlier.
    reindexer = wiring.part_of_app(app, wiring.REINDEXER)
    library_roots.register_handlers(
        settings=settings,
        service=library_service,
        reindexer=reindexer,
        queue=queue,
        preferences=hub,
        # A folder of pictures as a set, the preference read per folder.
        folder_settled=_folder_grouper(app, store, hub),
        # And a ZIP of pictures, the same way. Its own switch, read per archive.
        archive_settled=_archive_grouper(app, store, hub),
        # The passes a walk makes worth running, named here since a scan of an indexed library
        # probes nothing.
        settles_into=(suggestions.SUGGESTION_SCAN, shoots.SHOOTS_LOOK),
        content=store.content,
    )


def _declare_switches(queue: JobQueue, hub: settings_hub.SettingsService) -> None:
    # Off switches for whole-library passes, by job type so stopping a scan never stops a probe.
    queue.switchboard.declare(
        Switch(
            key=importing.SCAN_KEY,
            refusal=(
                "Scanning is switched off, so nothing was read. Turn it back on under Import tasks."
            ),
            on=partial(hub.get_app, importing.SCAN_KEY),
        ),
        library_roots.SCAN,
        library_roots.LIBRARY_SCAN,
        library_roots.RECONCILE,
        library_roots.SCAN_COUNT,
    )
    queue.switchboard.declare(
        Switch(
            key=dedup.SCAN_KEY,
            refusal=(
                "Looking for duplicates is switched off, so nothing was compared. Turn it back on "
                "under Import tasks."
            ),
            on=partial(hub.get_app, dedup.SCAN_KEY),
        ),
        dedup.DEDUP_SCAN,
    )
    queue.switchboard.declare(
        Switch(
            key=suggestions.SCAN_KEY,
            refusal=(
                "Suggesting People is switched off, so no folder was read. Turn it back on under "
                "Importing."
            ),
            on=partial(hub.get_app, suggestions.SCAN_KEY),
        ),
        suggestions.SUGGESTION_SCAN,
    )
    # And the shoots pass, gated directly by its task's When.
    queue.switchboard.declare(
        Switch(
            key=when_key("shoots"),
            refusal="Finding shoots runs only when you run it, so nothing was read.",
            on=partial(starts_on_its_own, hub, "shoots"),
        ),
        shoots.SHOOTS_LOOK,
    )
    # And the stash-box lookups, gated at enqueue and claim by `tasks.enrichment.when`.
    queue.switchboard.declare(
        Switch(
            key=stash_boxes.ASK_NEW_FILES_KEY,
            refusal=(
                "Looking up new files on stash-boxes runs only when you run it, so nothing was "
                "asked."
            ),
            on=partial(hub.get_app, stash_boxes.ASK_NEW_FILES_KEY),
        ),
        stash_boxes.STASH_SWEEP,
    )


def build_imports(
    app: FastAPI,
    settings: Settings,
    hardware: HardwareReport,
    store: Storage,
    library_service: library_roots.LibraryService,
    hub: settings_hub.SettingsService,
    queue: JobQueue,
    accelerator: media.Accelerator,
) -> None:
    """What happens to a file as it lands, with the expensive jobs capped (see `job_limits`)."""
    policy = _import_policy(app, store, hub)
    _register_media(app, settings, hardware, hub, policy, accelerator)
    _register_library(app, settings, store, library_service, hub, queue)
    _declare_switches(queue, hub)
    # Off on Activity by the gate's answer, never refused: a claim-only follow-on runs while off.
    for kind in (media_jobs.FINGERPRINT_FILE, music.AUDIO_FINGERPRINT):
        queue.switchboard.declare_shown(partial(policy.allows, kind, None), kind)
    # The one import pipeline, which downloads are handed too.
    capture.register_handlers(
        settings=settings, reindexer=wiring.part_of_app(app, wiring.REINDEXER)
    )
    # The search index, a cache, so this handler is always safe to run again.
    search.register_handlers(database=store.database, preferences=hub, queue=queue)
    tidy.register_handlers(database=store.database, settings=settings)
    # Queued: the graphics runtime is over a gigabyte, too long for one request.
    performance.register_handlers(settings=settings)
