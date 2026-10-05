# SPDX-License-Identifier: AGPL-3.0-or-later
"""What happens to a file as it lands, and the off switches for the whole-library passes."""

from __future__ import annotations

from functools import partial

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
    """Turn a ZIP of pictures into a photo set, if the preference says to.

    The third of three groupers and deliberately the same shape as the other two: the scan knows it
    opened an archive, the photo-set slice knows what a set is, and neither imports the other. What
    binds them is here, which is the only place allowed to know both exist.
    """

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
    # What a scan starts, and every switch that has to be on for it. Read per file, so a change on
    # the Importing screen takes effect on the next file rather than the next restart.
    #
    # The map lives here because it is the one place that knows both the job types (media_jobs',
    # faces', semantic's) and the settings that govern them; a type named by nothing is always run.
    #
    # A TUPLE PER JOB RATHER THAN ONE KEY: a job is queued only when every switch it depends on is
    # on: the feature's own switch as well as the stage's. A handler that asks and returns still
    # costs a job written, claimed, run and recorded per file, for ever.
    return {
        media_jobs.PREVIEW: (importing.GENERATE_KEY, performance.GENERATE_PREVIEWS_KEY),
        media_jobs.SPRITE: (importing.GENERATE_KEY, performance.GENERATE_SPRITES_KEY),
        media_jobs.FINGERPRINT_FOR_STASH_BOXES: (
            importing.GENERATE_KEY,
            performance.GENERATE_FINGERPRINTS_KEY,
        ),
        # An arriving file's own fingerprint job, under the same two: the read hands it out.
        media_jobs.FINGERPRINT_FILE: (
            importing.GENERATE_KEY,
            performance.GENERATE_FINGERPRINTS_KEY,
        ),
        # The one entry here that is a whole second copy of a file rather than a piece of one, and
        # therefore the one whose switch is about disk rather than about processor time.
        media_jobs.REMUX: (importing.GENERATE_KEY, performance.REPAIR_PLAYBACK_KEY),
        # Not derivatives, and not media_jobs' business either, but they are started by the same
        # stage and governed by the same kind of switch, so they are mapped in the same place. Each
        # names its feature's own switch first.
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
        # The third of the three: a file arriving is read for a mark when the feature is on. It is
        # off out of the box, which is the one way it differs from the two above: this pass opens
        # the file.
        watermarks.WATERMARK_READ: (
            importing.IDENTIFY_KEY,
            watermarks.ENABLED_KEY,
            watermarks.READ_ON_IMPORT_KEY,
        ),
        # Reading a file's sound. Under Generate rather than Identify: it is made from the file
        # rather than worked out about it, and it is what any later answer to "what song is this"
        # is built on. Off out of the box (the music task's When is "Only when I press it"), and
        # a folder can say yes for itself, which is why the key is in `overridable` below. At
        # arrival it is asked of the DESTINATION folder (`ImportPolicy.allows_for_root`, installed
        # into `slices/music/landing.py`); a press passes the key over (`refused_by(pressed=)`).
        music.AUDIO_FINGERPRINT: (importing.GENERATE_KEY, music.FINGERPRINT_KEY),
    }


def _import_policy(
    app: FastAPI, store: Storage, hub: settings_hub.SettingsService
) -> importing.ImportPolicy:
    # The gate itself, as a part of the application: the screen reads the same map to draw the same
    # groups, and the catch-up pass asks it the same question rather than a second copy of it.
    policy = importing.ImportPolicy(
        settings=hub,
        content=store.content,
        roots=wiring.part_of_app(app, importing.ROOT_PREFS),
        gates=_generate_keys(),
        # What ONE FOLDER may answer differently. Every key above except the two consent switches:
        # `faces.enabled` and `semantic.enabled` decide whether a feature exists at all (whether
        # models are downloaded, whether the screens are there), so a folder answering one would be
        # a folder turning a feature on for the whole install, or off for itself while every screen
        # went on offering it.
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
    settings: Settings,
    hardware: HardwareReport,
    hub: settings_hub.SettingsService,
    policy: importing.ImportPolicy,
    accelerator: media.Accelerator,
) -> None:
    async def should_generate(job_type: str, asset_id: str | None = None) -> bool:
        return await policy.allows(job_type, asset_id)

    async def chosen_shape() -> str:
        """Which preview shape somebody picked. Read per file, like the switches above."""
        return str(await hub.get_app(performance.PREVIEW_SHAPE_KEY))

    media_jobs.register_handlers(
        settings=settings,
        hardware=hardware,
        should_generate=should_generate,
        chosen_shape=chosen_shape,
        # The SAME object the player holds. One card, one judgement about whether it is working.
        accelerator=accelerator,
        # Recognition runs on a file as it lands, so a library stays current without being swept.
        # media_jobs is handed the job type rather than knowing it: a feature never imports another.
        # Describing a file for search-by-meaning rides the same stage, for the same reason.
        #
        # The describe job checks its own switch, but a job that does nothing is still a row
        # written, claimed, run and recorded. Consulting the switch HERE is what stops it being
        # queued at all. See `generate_keys`.
        # The fourth, the music slice's, and it ONLY CLAIMS. A file that arrived through
        # Sift into a folder that said yes has its fingerprint waiting under its identity, taken
        # at staging, and this files it against the asset. Every other file (one a scan found,
        # one whose folder said nothing) is left waiting WITHOUT BEING OPENED: a scan finding a
        # file is nobody asking for its music, and reading every one on a NAS folder of a hundred
        # thousand files would be days nobody asked for.
        follow_on=(
            faces.FACE_SCAN,
            semantic.SEMANTIC_DESCRIBE,
            watermarks.WATERMARK_READ,
            music.AUDIO_FINGERPRINT,
        ),
        follow_on_payloads={music.AUDIO_FINGERPRINT: {music.CLAIM_ONLY: True}},
        # The folder pass. A claim is about a FOLDER, so re-reading one after each of the two
        # hundred files that landed in it would be two hundred passes producing one answer, and
        # this timing is also what puts a name on a file dropped into a folder somebody has
        # already answered. Not the duplicate sweep: the read writes no fingerprint, so
        # the sweep is asked for by what does (`fingerprints_settle_into` below).
        #
        # And what a Stash library kept for files it did not find here: a picture waits for its
        # place, which the import of a new file is what writes (`stash_migration.STASH_ARRIVED`,
        # reading one row and stopping where nothing waits).
        settles_into=(suggestions.SUGGESTION_SCAN, stash_migration.STASH_ARRIVED),
        # The duplicate sweep, asked for by whatever writes fingerprints: an arriving file's own
        # fingerprint job, and the fingerprint chain once it has written its last page, so it
        # runs a settle after the last file's fingerprints rather than after the last read.
        # And the stash-box sweep beside it, at the same moment and for the same reason: what it
        # sends is the fingerprints, so the last page of that chain is the first moment a batch of
        # new files can be asked about. It runs as Sift's own act (`stash_boxes.jobs.sweep`),
        # and only where the enrichment switch is on, which the handler checks for itself.
        # And the Stash pass beside them, for a scene that waits for its OSHash or its video
        # fingerprint, which only a fingerprint read can match.
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
    # The seam the search index listens on. Read off the application rather than passed down: it is
    # built with the downloader, one step earlier, because the download service takes it too.
    reindexer = wiring.part_of_app(app, wiring.REINDEXER)
    library_roots.register_handlers(
        settings=settings,
        service=library_service,
        reindexer=reindexer,
        # The prune job needs the queue to put its own next run in, and the preferences to read
        # the retention rule fresh each time rather than holding the value it booted with.
        queue=queue,
        preferences=hub,
        # A folder of pictures, as a set. Bound here for the reason the download grouper is: the
        # scan knows which folders it went through and the photo-set slice knows what a set is, and
        # neither may import the other. The preference is read inside, per folder, so turning it off
        # takes effect on the next scan.
        folder_settled=_folder_grouper(app, store, hub),
        # And a ZIP of pictures, the same way. Its own switch, read per archive.
        archive_settled=_archive_grouper(app, store, hub),
        # THE WHOLE-LIBRARY PASSES A WALK MAKES WORTH RUNNING, named here as well as on the probe
        # below because a scan of an already-indexed library reads no file and hands out no probe,
        # so without this neither pass would run on a press of Scan.
        #
        # These two and not the duplicate sweep: both read FOLDER AND FILE NAMES, which a walk is
        # exactly what refreshes, while the sweep compares fingerprints and there are none to
        # compare until a probe has written them. The probe keeps its own list for that reason.
        settles_into=(suggestions.SUGGESTION_SCAN, shoots.SHOOTS_LOOK),
    )


def _declare_switches(queue: JobQueue, hub: settings_hub.SettingsService) -> None:
    # THE OFF SWITCHES FOR WHOLE-LIBRARY PASSES, on the one board the queue consults before it
    # writes a job down and again before a worker starts one.
    #
    # The gates above are the same idea reached
    # through `ImportPolicy`, which answers for work done TO A FILE as it arrives and can be
    # overridden per folder; these answer for whole-library passes, which belong to no folder and
    # are asked for by a button as well as by an import.
    #
    # NAMED BY JOB TYPE RATHER THAN BY FAMILY, and that is the point of the grain. "Stop scanning"
    # cannot mean "stop probing a file already in the library": the probe and the import are in the
    # SCAN family too, and a file taken in with no probe has no dimensions and cannot be laid out
    # on a wall. So the walk, the whole-library pass and the catch-up are named, and nothing else.
    queue.switchboard.declare(
        Switch(
            key=importing.SCAN_KEY,
            refusal=(
                "Scanning is switched off, so nothing was read. Turn it back on under Importing."
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
                "under Importing."
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
    # And the shoots pass, which a settling scan asks for beside the suggestions: its task's When is
    # the answer, read directly because no older key stands for it.
    queue.switchboard.declare(
        Switch(
            key=when_key("shoots"),
            refusal="Finding shoots runs only when you run it, so nothing was read.",
            on=partial(starts_on_its_own, hub, "shoots"),
        ),
        shoots.SHOOTS_LOOK,
    )
    # And the stash-box lookups, which the fingerprints ask for as they settle: the key it answers
    # for is the lookups' When (`tasks.enrichment.when`, read through the key retired into it), so
    # "Only when I press it" is refused at the enqueue and at the claim, and no run nobody pressed
    # waits on Activity. The handler's own check of the same key stays, for a switch moved while a
    # run was already under way.
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
    """What happens to a file as it lands, and in what order.

    Which handlers exist is a property of the code, so they are claimed here, once, before a worker
    can take anything. The per-type caps come from the same report the pool is sized from (see
    `job_limits`): the jobs are not interchangeable, and the expensive ones are capped so they cannot
    fill every worker and leave the cheap ones queued behind them.
    """
    policy = _import_policy(app, store, hub)
    _register_media(settings, hardware, hub, policy, accelerator)
    _register_library(app, settings, store, library_service, hub, queue)
    _declare_switches(queue, hub)
    # The import pipeline that takes a staged upload or paste in. The download feature is handed
    # `capture.import_file` bound with these same settings so its fetched files go through the one
    # gate too: capture owns the pipeline, and there is no second copy of it.
    capture.register_handlers(
        settings=settings, reindexer=wiring.part_of_app(app, wiring.REINDEXER)
    )
    # The search index, kept in step with the library. A cache, so this handler is always safe to
    # run again from scratch.
    search.register_handlers(database=store.database, preferences=hub, queue=queue)
    # Counting what the costly tidyings would remove, which reads whole directories off the disk.
    tidy.register_handlers(database=store.database, settings=settings)
    # Fetching the graphics card's runtime. Queued because it is over a gigabyte, and a request held
    # open that long times out somewhere between the browser and here.
    performance.register_handlers(settings=settings)
    # The Build's products are registered in `build_products`, where the three features that
    # answer for them meet; nothing to do here.
