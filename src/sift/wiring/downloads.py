# SPDX-License-Identifier: AGPL-3.0-or-later
"""The downloader, the ways out of this machine, and the stash-boxes that share them."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial

from fastapi import FastAPI

from sift.composition import LibraryDropFiling, LibraryFiling, LibraryNaming
from sift.kernel import wiring
from sift.kernel.access.creator_studios import account_for
from sift.kernel.config import Settings
from sift.kernel.enrichment import Enricher
from sift.kernel.jobs import JobQueue, hold_on
from sift.kernel.ml.child import LOST_DEVICE_HOLD_SECONDS
from sift.kernel.ml.runtime import DeviceLost
from sift.kernel.tunnels import EGRESS, TUNNELS, EgressRouter, TunnelStore
from sift.kernel.wiring import provide
from sift.slices import (
    auth,
    capture,
    collections,
    download,
    faces,
    people,
    photo_sets,
    search,
    settings_hub,
    songs,
    stash_boxes,
    tags_ratings,
)
from sift.slices.download.sources.net import guarded_session
from sift.wiring.built import Downloads, Storage


def _build_stash_boxes_and_writers(
    app: FastAPI,
    store: Storage,
    queue: JobQueue,
    secrets: download.SecretStore,
    egress: EgressRouter,
) -> None:
    # The stash-boxes Sift can ask about a person, a site or a file.
    #
    # Built here beside the downloads because it needs two of the same things they do and may not
    # import them: the guarded connector, which vets every address before a socket opens and can
    # send a request through a tunnel, and the secret store the keys are sealed in. Handed in from
    # the composition root, which is what a composition root is for.
    #
    # And the egress router's `through`, which turns the tunnel id a box stores as its way out into
    # the address that tunnel listens on. It is the same resolver every download uses, so a tunnel id
    # means one thing to both, and a tunnel that is down refuses both rather than letting either
    # out of the machine's own address.
    provide(
        app,
        stash_boxes.SERVICE,
        stash_boxes.StashBoxService(
            store.database,
            secrets,
            stash_boxes.StashBoxAdapter(guarded_session, through=egress.through),
        ),
    )

    # What a stash-box's answer is allowed to write, and who writes it.
    #
    # The rules live in the kernel and each area registers a writer for the subject it owns, the
    # same arrangement the workbench uses for its queues. The two shapes underneath, which turn a
    # name into a row and put a row onto a file, are the one thing no slice can build: they span
    # people, tags and sites, and a slice may not import another slice. So they are built in
    # `composition`, which exists for exactly that and holds nothing else.
    people_service = wiring.part_of_app(app, people.SERVICE)
    tag_service = wiring.part_of_app(app, tags_ratings.SERVICE)
    naming = LibraryNaming(store.database, tag_service)
    # Published for the stash-box confirm, which has to find the rows a match named so the ones it
    # invented can be linked to their box afterwards: see `router_matches.apply_matches`.
    provide(app, wiring.NAMING, naming)
    filing = LibraryFiling(store.database, tag_service)
    enricher = Enricher()
    enricher.register(people.PersonWriter(people_service, naming))
    enricher.register(people.SiteWriter(people_service, naming))
    enricher.register(tags_ratings.TagWriter(tag_service))
    enricher.register(
        stash_boxes.AssetWriter(
            content=store.content,
            filing=filing,
            naming=naming,
            reindex=wiring.part_of_app(app, wiring.REINDEXER),
            # People a box filed may be asked about by the one face in the file: the pass that
            # asks runs once the batch of writes has settled (`faces.FACE_BOX_QUESTIONS`).
            people_filed=partial(queue.settle_into, [faces.FACE_BOX_QUESTIONS]),
            # A studio read as one creator's own store is filed under her username, never made a
            # Site again (`kernel.access.creator_studios`).
            studio_account=partial(account_for, store.database),
        )
    )
    provide(app, wiring.ENRICHER, enricher)


def build_downloads(
    app: FastAPI,
    store: Storage,
    queue: JobQueue,
    master_keys: auth.MasterKeyStore,
) -> Downloads:
    """The downloader and the ways out of this machine.

    The search index's "this changed" seam is built here rather than beside the other seams, because
    the download service takes it: attribution writes a username, and a username is indexed
    text. Nothing here knows there is a full-text index; they know something has to be told when
    text they own changes.
    """
    reindexer = search.Reindexer(database=store.database, queue=queue)
    provide(app, wiring.REINDEXER, reindexer)
    # A job whose device stopped answering waits out the hold instead of spending its attempts.
    hold_on(DeviceLost, seconds=LOST_DEVICE_HOLD_SECONDS)

    # The ledger and the site logins are a service on the application so the capture screen can hand
    # it a URL; the sealed cookies go through the secret store; and the pool's secrets seam is the
    # admin key, read from the key store above.
    secrets = download.SecretStore(store.database)
    service = download.DownloadService(store.database, queue, secrets, reindexer, store.content)

    # The tunnels a download can be routed through, and which sites take which. A tunnel's
    # configuration is sealed under an admin's key, so nothing can run before a password login.
    # The key reader here is the same one the download job uses for a saved site login, and None
    # from it means the same thing: wait rather than proceed without.
    tunnels = TunnelStore(
        store.database,
        secrets,
        read_key=download.AdminMasterKey(store.database, master_keys).master_key,
    )
    # Which way out each request to a site takes. Read per use from the tunnel store, so a change on
    # a settings screen takes effect on the next one with nothing restarted. The router and the
    # store are the kernel's; which Site a URL belongs to is the download slice's catalog, handed
    # in.
    egress = EgressRouter(
        tunnels.processes,
        read_default=tunnels.default_route,
        read_site=tunnels.site_route,
        ensure_started=tunnels.ensure_started,
        site_of=download.site_key_of,
        why_down=tunnels.why_down,
    )

    provide(app, download.SERVICE, service)
    provide(app, TUNNELS, tunnels)
    provide(app, EGRESS, egress)

    _build_stash_boxes_and_writers(app, store, queue, secrets, egress)
    return Downloads(secrets=secrets, service=service, tunnels=tunnels, egress=egress)


def _downloader_for(
    store: download.SiteOptionStore,
) -> Callable[[str | None], Awaitable[str | None]]:
    """A live reader for "which tool does this Site use", closed over the store that knows.

    `resolve` on the store already does the per-Site-then-default fallback, so this adds no
    rule of its own, which is the point. The alternative would be for the downloader to hold the
    store and do the lookup itself, and then two pieces of code would decide what a Site with no
    row of its own falls back to.
    """

    async def read(site_key: str | None) -> str | None:
        return (await store.resolve(site_key)).downloader

    return read


def _download_stores(
    app: FastAPI, settings: Settings, store: Storage
) -> tuple[download.progress.Registry, download.SiteOptionStore, download.ArtStore]:
    # What is in flight, held in memory and never written down: a transfer is happening or it is
    # not, and after a restart none is: anything that was running is requeued.
    watching = download.progress.Registry()
    # What each site does differently: what its files are called, and where they land.
    site_options = download.SiteOptionStore(store.database)
    # The picture each creator is shown with, fetched from their page once and kept in the cache,
    # through the cover door every picture from outside goes through (Sift's own JPEG, never the
    # site's bytes). The door is built with the storage, before this.
    site_art = download.ArtStore(
        store.database, settings.cache_dir, wiring.part_of_app(app, wiring.COVER_PICTURES)
    )
    provide(app, download.PROGRESS, watching)
    provide(app, download.SITE_OPTIONS, site_options)
    provide(app, download.SITE_ART, site_art)
    # A stash-box's picture of a creator it files as a studio, kept as that creator's picture. The
    # stash-box slice queues it; the store is this slice's, so the two meet here.
    stash_boxes.register_picture_handler(
        service=wiring.part_of_app(app, stash_boxes.SERVICE),
        pictures=download.art.CreatorPictures(site_art),
    )
    return watching, site_options, site_art


def build_download_handlers(
    app: FastAPI,
    settings: Settings,
    store: Storage,
    downloads: Downloads,
    hub: settings_hub.SettingsService,
) -> None:
    """The downloader, registered after the import pipeline it hands each fetched file to.

    It gets capture's `import_file` (settings bound in, so the gate runs inside it) and live readers
    for everything an admin can change, so a change reaches the next download rather than the next
    restart.
    """
    watching, site_options, site_art = _download_stores(app, settings, store)

    download.register_handlers(
        service=downloads.service,
        downloader=download.Downloader(
            # Which tool each Site was pointed at, read from the same store its folder and
            # its naming come from: one question, one place, rather than a setting beside two
            # rows that answer the rest of it.
            read_downloader=_downloader_for(site_options),
            # Pacing, retries, timeouts, the bandwidth cap, the size bounds and the quality
            # preference, read fresh for each fetch so a change reaches the next download.
            read_policy=partial(download.policy.read_policy, hub.get_app, hub.app_is_stored),
        ),
        import_file=partial(
            capture.import_file,
            settings=settings,
            reindexer=wiring.part_of_app(app, wiring.REINDEXER),
        ),
        # Whether a username that names nobody yet becomes a new person. Read live, per download, so
        # a change on the Downloads screen takes effect on the next one rather than at a restart.
        may_create_people=partial(hub.get_app, download.PEOPLE_FROM_USERNAMES_KEY),
        remember_downloads=partial(hub.get_app, download.REMEMBER_KEY),
        watching=watching,
        # Read per download, so a naming rule changed while a queue drains reaches the next file.
        read_site_options=site_options.resolve,
        # Once per site, ever, and only for a site that has no picture yet.
        keep_art=download.art.keeper(site_art),
        # Which way out each download takes. Read per download from the tunnel store, so a change
        # on a settings screen takes effect on the next one with nothing restarted.
        # One router, not one per caller: asking a site what a whole playlist holds is a request to
        # that site and goes out the way its downloads do, so the endpoint that asks reads this same
        # object rather than building a second one that could be wired differently.
        router=downloads.egress,
        # How much room to leave on the disk, read live for the same reason. Not a fixed floor: half
        # a gigabyte is less than one 4K file, so a disk that passed the check when a fetch began
        # could be full before it ended.
        read_disk_floor=partial(hub.get_app, download.DISK_FLOOR_GB_KEY),
        # Where a link DROPPED on something gets filed once it has landed. Bound here because the
        # seven kinds belong to six features and an opinion, and the download slice may import none
        # of them. `composition` is the one module allowed to name two slices in one file.
        file_under=LibraryDropFiling(
            store.database,
            people=wiring.part_of_app(app, people.SERVICE),
            tags=wiring.part_of_app(app, tags_ratings.SERVICE),
            collections=wiring.part_of_app(app, collections.SERVICE),
            photo_sets=wiring.part_of_app(app, photo_sets.SERVICE),
            songs=wiring.part_of_app(app, songs.SERVICE),
            user_state=store.user_state,
        ).file_under,
    )
