# SPDX-License-Identifier: AGPL-3.0-or-later
"""The products a Build can make, and the music feature that is one of them."""

from __future__ import annotations

import time
from collections.abc import Awaitable
from functools import partial
from typing import Protocol

from fastapi import FastAPI

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.config import Settings
from sift.kernel.content import Lack, lacks_fingerprint
from sift.kernel.hardware import HardwareReport
from sift.kernel.jobs import JobContext, JobQueue
from sift.kernel.jobs.families import Family
from sift.kernel.jobs.quiet_hours import next_opening
from sift.kernel.media import Accelerator
from sift.kernel.tunnels import EGRESS
from sift.kernel.wiring import provide
from sift.kernel.workbench import Workbench
from sift.slices import (
    dedup,
    download,
    faces,
    importing,
    media_jobs,
    music,
    performance,
    semantic,
    settings_hub,
    shoots,
    stash_boxes,
    stash_migration,
    tasks,
    watermarks,
)
from sift.slices.download.sources.net import guarded_session
from sift.wiring.built import Storage, Understanding
from sift.wiring.readiness import marks_cannot_run, meaning_cannot_run


class Wanted(Protocol):
    """Whether a press of this work would make it: the feature and the picture switched on."""

    def __call__(self, job_type: str, asset_id: str | None = None) -> Awaitable[bool]: ...


async def _music_waiting(service: music.MusicService, viewer: Viewer) -> int:
    """How many files this user can see that still want an audio fingerprint.

    The scoped count. A card that stated the library's own total would publish, in the
    difference, how much this user is not being shown. That is the rule for every card on this
    board.
    """
    return await service.waiting_for(viewer.id)


def _register_pictures(
    products: importing.ProductRegistry,
    settings: Settings,
    hardware: HardwareReport,
    store: Storage,
    hub: settings_hub.SettingsService,
    accelerator: Accelerator,
    wanted: Wanted,
) -> None:
    async def chosen_preview_shape() -> str:
        return str(await hub.get_app(performance.PREVIEW_SHAPE_KEY))

    # One product per picture, so the Generate row can say how many of each are missing and a
    # person can ask for the still alone. The three on one task still read the file once.
    for picture in media_jobs.PICTURES:
        products.register(
            importing.Product(
                key=picture.key,
                label=picture.label,
                family=Family.GENERATE,
                help=picture.help,
                switched_on=partial(
                    media_jobs.picture_switched_on, picture=picture, allowed=wanted
                ),
                lack=partial(media_jobs.picture_lack, picture=picture, allowed=wanted),
                lacking_among=partial(
                    media_jobs.picture_lacking_among,
                    store.content,
                    picture=picture,
                    allowed=wanted,
                ),
                build=partial(
                    media_jobs.build_picture,
                    picture=picture,
                    settings=settings,
                    hardware=hardware,
                    allowed=wanted,
                    chosen_shape=chosen_preview_shape,
                    accelerator=accelerator,
                ),
                frames=partial(
                    media_jobs.picture_frames,
                    kind=picture.kind,
                    content=store.content,
                    allowed=wanted,
                ),
                governed_by=picture.job_type,
                # A press on a file that has this picture makes it again: `build_picture` honours
                # the key. See `Product.again`.
                doing=f"Creating {picture.label.lower()} for",
                again=True,
            )
        )


def _register_fingerprints(
    products: importing.ProductRegistry, settings: Settings, store: Storage, wanted: Wanted
) -> None:
    async def fingerprints_switched_on() -> bool:
        return await wanted(media_jobs.FINGERPRINT_FILE)

    async def fingerprint_file(context: JobContext) -> None:
        await media_jobs.fingerprint_one(
            store.content, str(context.payload["asset_id"]), settings=settings
        )

    async def _lacks_fingerprint() -> Lack:
        return lacks_fingerprint()

    products.register(
        importing.Product(
            key="fingerprints",
            family=Family.GENERATE,
            label="Fingerprints",
            help="What finds duplicates and matches a file against the stash-boxes.",
            switched_on=fingerprints_switched_on,
            lack=_lacks_fingerprint,
            lacking_among=store.content.unfingerprinted_among,
            build=fingerprint_file,
            frames=media_jobs.fingerprint_frames,
            governed_by=media_jobs.FINGERPRINT_FILE,
            doing="Fingerprinting",
            # `fingerprint_one` measures the file every time it is asked.
            again=True,
            # What reads the fingerprints, asked for once a run's files stop landing: the same
            # three the fingerprint chain asks for after its last page.
            settles_into=(
                dedup.DEDUP_SCAN,
                stash_boxes.STASH_SWEEP,
                stash_migration.STASH_ARRIVED,
            ),
        )
    )


def _register_faces_and_meaning(
    products: importing.ProductRegistry, understanding: Understanding, wanted: Wanted
) -> None:
    face_service = understanding.faces
    meaning = understanding.semantic

    async def faces_switched_on() -> bool:
        return await wanted(faces.FACE_SCAN)

    async def meaning_switched_on() -> bool:
        return await wanted(semantic.SEMANTIC_DESCRIBE)

    products.register(
        importing.Product(
            key=faces.PRODUCT,
            family=Family.IDENTIFY,
            label="Faces",
            help="Who is in each file, for the people screens.",
            switched_on=faces_switched_on,
            lack=face_service.lack,
            lacking_among=face_service.needs_scanning_among,
            build=partial(faces.scan_file, service=face_service),
            frames=face_service.frame_requests,
            governed_by=faces.FACE_SCAN,
            doing="Looking for faces in",
            # A press on a finished scan looks at it again from the first moment; one on a pass
            # the time limit cut short reads on from where it stopped (see
            # `FaceService._resume_point`). Either way the press looks again.
            again=True,
            # The device AND the chosen models: a press with the models missing would start a pass
            # whose every file waited on a download nobody asked for. See `cannot_scan`.
            cannot_run=face_service.cannot_scan,
        )
    )
    products.register(
        importing.Product(
            key="meaning",
            family=Family.IDENTIFY,
            label="Meaning",
            help="What each file shows, for searching by description.",
            switched_on=meaning_switched_on,
            lack=meaning.lack,
            lacking_among=meaning.waiting_among,
            build=partial(semantic.describe_file, service=meaning),
            frames=semantic.frame_requests,
            # A deleted file cannot reach its own descriptions (they live in a table that takes
            # no foreign key), so each run of the Build drops them first.
            before_run=meaning.prune_index,
            governed_by=semantic.SEMANTIC_DESCRIBE,
            # A description is what the shoots pass reads, so a press that wrote some asks for it,
            # the way the fingerprints product asks for the duplicate sweep.
            settles_into=(shoots.SHOOTS_LOOK,),
            doing="Describing",
            # A file described since it was last read is not described again (`describe_asset`),
            # so a press offers it only where the description is missing or out of date.
            again=False,
            cannot_run=partial(meaning_cannot_run, meaning),
        )
    )


def _register_watermarks(products: importing.ProductRegistry, app: FastAPI, wanted: Wanted) -> None:
    async def marks_switched_on() -> bool:
        return await wanted(watermarks.WATERMARK_READ)

    # The third identification pass. "Identify now" runs what the stage's switches say, and the
    # watermark switch sits among them (`IDENTIFY_KEYS` on the client, `generate_keys` in
    # `build_imports`), so the button's own sentence, "for each thing switched on above", needs a
    # product behind it. `switched_on` is the import policy's answer, not a second one: a folder
    # can already say no to this read, and the Build must honour that the same way a file arriving
    # does.
    marks_service = wiring.part_of_app(app, watermarks.SERVICE)
    products.register(
        importing.Product(
            key=watermarks.PRODUCT,
            family=Family.IDENTIFY,
            label="Watermarks",
            help="The Site a file's watermark says it came from.",
            switched_on=marks_switched_on,
            lack=marks_service.lack,
            lacking_among=marks_service.unread_among,
            build=partial(watermarks.read_one, service=marks_service),
            governed_by=watermarks.WATERMARK_READ,
            doing="Reading the watermark on",
            # `read_asset` reads the file every time it is asked.
            again=True,
            cannot_run=partial(marks_cannot_run, marks_service),
            # No frames: this pass cuts two crops out of one frame with a graph of its own, which
            # is not a moment the kernel can prepare for it.
        )
    )


def _build_song_names(
    app: FastAPI,
    store: Storage,
    hub: settings_hub.SettingsService,
    queue: JobQueue,
    board: Workbench,
    music_store: music.MusicStore,
) -> music.SongNames:
    reindexer = wiring.part_of_app(app, wiring.REINDEXER)
    # WHAT A FILE'S SONG IS CALLED, once its pairs settle: the name spreads through the group that
    # shares the song (one undoable receipt per file), and a file still without one is looked up on
    # AcoustID by the lookup's own task, where an admin allowed it. The lookup goes out through the guarded connector and
    # the downloads' own tunnel resolver, as a stash-box does, so a tunnel id means one thing to
    # both. See `slices/music/names.py` and `slices/music/lookup.py`.
    song_names_store = music.NameStore(store.database)
    acoustid = music.AcoustIDClient(
        guarded_session, through=wiring.part_of_app(app, EGRESS).through
    )
    # One starter for both ways a file comes to be asked about: its pairing settling, where the
    # lookup task's own When starts it, and a press of that task (`build_tasks`).
    lookup_starter = music.LookupStarter(song_names_store, hub, enqueue=queue.enqueue)
    song_names = music.SongNames(
        song_names_store,
        group_of=music_store.music_group,
        touched=reindexer.touched_many,
        after_spread=lookup_starter.consider,
    )
    music.register_lookup_handlers(
        music.LookupTask(
            song_names_store,
            hub,
            download.SecretStore(store.database),
            acoustid,
            fingerprint_of=music_store.fingerprint_of,
            spread=song_names.spread,
            touched=reindexer.touched_many,
        ),
        lookup_starter,
    )
    provide(app, music.LOOKUP_STARTER, lookup_starter)
    provide(
        app,
        music.LOOKUP,
        music.LookupSettings(song_names_store, download.SecretStore(store.database), acoustid, hub),
    )
    # The shared names' receipts, taken back from a file's History. A reverser with no card.
    board.register_reverser(
        music.SharedNameReceipts(song_names_store, touched=reindexer.touched_many)
    )
    return song_names


def _register_music(
    products: importing.ProductRegistry,
    app: FastAPI,
    settings: Settings,
    store: Storage,
    hub: settings_hub.SettingsService,
    queue: JobQueue,
    board: Workbench,
    wanted: Wanted,
) -> None:
    policy = wiring.part_of_app(app, importing.SERVICE)
    # WHAT SONG IS IN A FILE. Built here because this is the only place that has all four of the
    # things it needs: the switches the import policy answers, the content store the count spans,
    # the board the card sits on, and the ledger the card's price is read from.
    #
    # THE SERVICE IS HANDED THE PRESS'S ANSWER (`wanted`), because every one of its questions is
    # asked for a press: the Build's count and page, a file's Run task, the card's count and the
    # job those start. The music task is "Only when I press it" out of the box, and asked the
    # arrival way every one of those would answer off: the Build would count nothing, the card
    # would never appear and a pressed job would return without reading. The scan's follow-on reaches the same
    # job and is kept from reading by `CLAIM_ONLY`, not by the switch.
    music_store = music.MusicStore(store.database)
    song_names = _build_song_names(app, store, hub, queue, board, music_store)
    music_service = music.MusicService(
        store=music_store,
        allowed=wanted,
        job_type=music.AUDIO_FINGERPRINT,
        on_pairs_settled=song_names.on_pairs_settled,
    )
    provide(app, music.SERVICE, music_service)
    music.register_handlers(service=music_service, settings=settings)
    # And the arrival's own question for the read taken at staging, asked of the folder the file is
    # going INTO: see `slices/music/landing.py`. Installed rather than passed for the reason
    # written there.
    music.install_gate(policy.allows_for_root)

    async def music_on_arrival() -> bool:
        """Whether the library starts this on its own: what the Build sheet ticks the row from.
        The one question here that is the arrival's rather than a press's: see above."""
        return await policy.allows(music.AUDIO_FINGERPRINT, None)

    products.register(
        importing.Product(
            key=music.PRODUCT,
            # GENERATE, while the JOB is in the Fingerprint family, and the two are different
            # questions: a family is what somebody watches on the Activity screen and a product is
            # which of the two runs makes it. The stash-box fingerprints are split the same way.
            family=Family.GENERATE,
            label="Music fingerprints",
            help="What lets Sift tell you which song a file's sound is.",
            switched_on=music_on_arrival,
            lack=music_service.lack,
            lacking_among=music_service.lacking_among,
            build=partial(music.fingerprint_one, service=music_service, settings=settings),
            # No frames: this reads the sound and never a picture, so there is no moment for the
            # kernel's one-decode reader to prepare.
            before_run=music_service.before_run,
            governed_by=music.AUDIO_FINGERPRINT,
            doing="Fingerprinting the sound of",
            # A file that has its sound fingerprint is not read again (`MusicService.fingerprint`).
            again=False,
        )
    )
    board.register(
        music.MusicQueue(
            waiting=partial(_music_waiting, music_service),
            any_waiting=music_service.any_waiting,
        )
    )


def _registry(
    app: FastAPI, settings: Settings, store: Storage, hub: settings_hub.SettingsService
) -> importing.ProductRegistry:
    policy = wiring.part_of_app(app, importing.SERVICE)

    async def night_start() -> str:
        return str(await hub.get_app(tasks.FROM_KEY))

    async def quiet_opens() -> int:
        start = str(await hub.get_app(tasks.FROM_KEY))
        end = str(await hub.get_app(tasks.UNTIL_KEY))
        return next_opening(start, end, int(time.time()))

    # The self-test's runner answers two questions for the Build: has this machine been measured,
    # and what are its rates for reading a file at a path. The one-pass reader turns the second
    # into one decode per file where that is the cheaper shape.
    machine = wiring.part_of_app(app, performance.SELF_TEST_RUNNER)
    return importing.ProductRegistry(
        night_start=night_start,
        quiet_opens=quiet_opens,
        machine=machine,
        # So a folder that refuses a product's work takes its files out of the counts: see
        # `Product.governed_by`, named on each registration below.
        policy=policy,
        one_pass=media_jobs.OnePassReader(
            store.content, settings=settings, rates=machine.read_rates
        ),
    )


def build_products(
    app: FastAPI,
    settings: Settings,
    hardware: HardwareReport,
    store: Storage,
    hub: settings_hub.SettingsService,
    queue: JobQueue,
    understanding: Understanding,
) -> importing.ProductRegistry:
    """The products a Build can make, each answered for by the feature that owns the work.

    After the understanding, because three of the products are its passes, and the music feature is
    built here because its only way in is as a product and a card on the same board.
    """
    # THE PRODUCTS A BUILD CAN MAKE, declared where the three features that answer for them meet.
    # Each is four questions (is it wanted, what lacking it looks like, which of these lack it,
    # make it for this file) answered by the feature that owns the work, through the same
    # handlers an arriving file gets and the same switches, so a Build cannot make something the
    # switches refuse or miss something they want.
    policy = wiring.part_of_app(app, importing.SERVICE)
    accelerator = wiring.part_of_app(app, wiring.ACCELERATOR)

    # WHAT A PRODUCT READS IS THE PRESS'S ANSWER. A product is made by a Build, a Run now or a
    # file's Run task, every one of them somebody pressing, so it is asked whether the WHAT is on
    # (the feature, the picture) and never whether the task starts on its own. Asked the arrival
    # way, "Generate thumbnails automatically" off would refuse a press of Generate and draw every
    # picture on the Build sheet as off with nothing to make.
    async def wanted(job_type: str, asset_id: str | None = None) -> bool:
        return await policy.allows(job_type, asset_id, pressed=True)

    products = _registry(app, settings, store, hub)
    _register_pictures(products, settings, hardware, store, hub, accelerator, wanted)
    _register_fingerprints(products, settings, store, wanted)
    _register_faces_and_meaning(products, understanding, wanted)
    _register_watermarks(products, app, wanted)
    _register_music(products, app, settings, store, hub, queue, understanding.workbench, wanted)
    # WHAT "Scan now" MEANS FOR ONE FILE: reading it again from disk (how big, how long, what
    # kind), which is the arriving file's own first job. Offered beside the products on a file's
    # "Run task", and nowhere on the Build: nothing lacks it. See `importing.Reading`.
    products.register_reading(
        importing.Reading(
            key="details",
            label="File details",
            help="Its size, length and kind, read again from the file on disk.",
            doing="Reading",
            job_type=media_jobs.PROBE,
        )
    )
    provide(app, importing.PRODUCTS, products)
    # The pass's run and task, registered once the products they read are there.
    importing.register_handlers(content=store.content, products=products)
    return products
