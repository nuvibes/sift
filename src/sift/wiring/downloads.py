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
    # The stash-boxes, beside downloads for the guarded connector, the secret store and `through`.
    provide(
        app,
        stash_boxes.SERVICE,
        stash_boxes.StashBoxService(
            store.database,
            secrets,
            stash_boxes.StashBoxAdapter(guarded_session, through=egress.through),
        ),
    )

    # What a stash-box's answer may write: the cross-slice shapes are built in `composition`.
    people_service = wiring.part_of_app(app, people.SERVICE)
    tag_service = wiring.part_of_app(app, tags_ratings.SERVICE)
    naming = LibraryNaming(store.database, tag_service)
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
            people_filed=partial(queue.settle_into, [faces.FACE_BOX_QUESTIONS]),
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
    """The downloader and the ways out of this machine, with the search index's change seam."""
    reindexer = search.Reindexer(database=store.database, queue=queue)
    provide(app, wiring.REINDEXER, reindexer)
    hold_on(DeviceLost, seconds=LOST_DEVICE_HOLD_SECONDS)

    secrets = download.SecretStore(store.database)
    service = download.DownloadService(store.database, queue, secrets, reindexer, store.content)

    # Tunnel configuration is sealed under an admin's key, so nothing runs before a password login.
    tunnels = TunnelStore(
        store.database,
        secrets,
        read_key=download.AdminMasterKey(store.database, master_keys).master_key,
    )
    # Read per use from the tunnel store, so a settings change takes effect with nothing restarted.
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
    """A live reader for "which tool does this Site use", reusing the store's own fallback."""

    async def read(site_key: str | None) -> str | None:
        return (await store.resolve(site_key)).downloader

    return read


def _download_stores(
    app: FastAPI, settings: Settings, store: Storage
) -> tuple[download.progress.Registry, download.SiteOptionStore, download.ArtStore]:
    # In memory only: after a restart nothing is in flight, and what was running is requeued.
    watching = download.progress.Registry()
    site_options = download.SiteOptionStore(store.database)
    site_art = download.ArtStore(
        store.database, settings.cache_dir, wiring.part_of_app(app, wiring.COVER_PICTURES)
    )
    provide(app, download.PROGRESS, watching)
    provide(app, download.SITE_OPTIONS, site_options)
    provide(app, download.SITE_ART, site_art)
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
    """The downloader, after the import pipeline it hands each fetched file to; settings live."""
    watching, site_options, site_art = _download_stores(app, settings, store)

    download.register_handlers(
        service=downloads.service,
        downloader=download.Downloader(
            read_downloader=_downloader_for(site_options),
            # Read fresh for each fetch, so a change reaches the next download.
            read_policy=partial(download.policy.read_policy, hub.get_app, hub.app_is_stored),
        ),
        import_file=partial(
            capture.import_file,
            settings=settings,
            reindexer=wiring.part_of_app(app, wiring.REINDEXER),
        ),
        may_create_people=partial(hub.get_app, download.PEOPLE_FROM_USERNAMES_KEY),
        remember_downloads=partial(hub.get_app, download.REMEMBER_KEY),
        watching=watching,
        read_site_options=site_options.resolve,
        keep_art=download.art.keeper(site_art),
        # One router for every caller, so a playlist lookup goes out the way its downloads do.
        router=downloads.egress,
        # Read live: a disk that passed the check at the start can be full before the end.
        read_disk_floor=partial(hub.get_app, download.DISK_FLOOR_GB_KEY),
        # Bound here: the drop kinds belong to six features, and only `composition` may name two.
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
