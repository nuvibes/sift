# SPDX-License-Identifier: AGPL-3.0-or-later
"""The swap: two installs trading files through their own tunnels, on a token."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from sift.composition import SwapFaceDescriptions, SwapFaceHolding
from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.config import Settings
from sift.kernel.content import DuplicateReads
from sift.kernel.jobs import JobQueue
from sift.kernel.wiring import provide
from sift.slices import capture, search, settings_hub, swap
from sift.slices.dedup import service as dedup_service
from sift.slices.swap import diff as swap_diff
from sift.slices.swap import ingest as swap_ingest
from sift.slices.swap import models as swap_models
from sift.slices.swap import offer as swap_offer
from sift.slices.swap import transfer as swap_transfer
from sift.slices.swap import weight as swap_weight
from sift.wiring.built import Downloads, Storage, Understanding


def _offer_maker(
    store: Storage, search_service: search.SearchService, face_descriptions: SwapFaceDescriptions
) -> Any:
    async def make_offer(  # type: ignore[no-untyped-def]
        viewer: Viewer,
        chosen: Sequence[swap_models.Chosen],
        share_boxes: bool,
        peer_model: str | None,
    ):
        return await swap_offer.offer_for(
            store.access,
            store.database,
            search_service,
            viewer,
            chosen,
            share_boxes=share_boxes,
            faces=face_descriptions,
            peer_model=peer_model,
        )

    return make_offer


def _assessor(store: Storage, hub: settings_hub.SettingsService) -> Any:
    async def assess(offer):  # type: ignore[no-untyped-def]
        # The guest's reading, as an admin with the vault shut: what the library holds is a
        # fact about the library, and a match against a hidden file is still a match.
        admin_id = await store.access.an_admin()
        if admin_id is None:
            raise swap.SwapRefused("There's no admin to receive a swap on this device.")
        viewer = await store.access.load_viewer(admin_id)
        if viewer is None:
            raise swap.SwapRefused("There's no admin to receive a swap on this device.")
        held = swap_diff.held_from(await DuplicateReads(store.database).fingerprints())
        rule = swap_diff.NearRule.from_bound(await dedup_service.bound_rule(hub))
        return await swap_diff.assess(
            store.database, store.access, viewer, offer, held=held, rule=rule
        )

    return assess


def _guest_tunnel(hub: settings_hub.SettingsService) -> Callable[[], Awaitable[str | None]]:
    async def guest_tunnel() -> str | None:
        # The tunnel chosen for joining a swap, on the swap screens; never the downloads' route,
        # which would send a guest's dial through a tunnel nobody had chosen for it.
        chosen = await hub.get_app(swap.GUEST_TUNNEL_KEY)
        return str(chosen) if chosen else None

    return guest_tunnel


def _path_of(store: Storage) -> Callable[[Viewer, str], Awaitable[Path | None]]:
    async def path_of(viewer: Viewer, key: str) -> Path | None:
        shut = await store.access.load_viewer(viewer.id)
        if shut is None:
            return None
        return await store.access.locate(shut, key)

    return path_of


async def build_swap(
    app: FastAPI,
    settings: Settings,
    store: Storage,
    queue: JobQueue,
    hub: settings_hub.SettingsService,
    downloads: Downloads,
    understanding: Understanding,
) -> None:
    """The swap: two installs trading files through their own VPN tunnels, on a token.

    Everything the session needs from the rest of Sift is handed in here, and each line is a seam
    another area owns: the offer reads the host's files through the access layer and a saved
    filter through the search feature's own compiler; the diff reads what this library already
    holds through the kernel's duplicate reader and the dedup feature's ONE rule of "near"; the
    landing goes through capture's `import_file` (the one gate) and holds faces through the face
    feature's pack import; the host's files are located through the scoped `locate` over a fresh,
    vault-shut viewer, so nothing hidden is ever offered or served.
    """
    reindexer = wiring.part_of_app(app, wiring.REINDEXER)
    search_service = wiring.part_of_app(app, search.SERVICE)
    face_descriptions = SwapFaceDescriptions(understanding.faces)
    face_holding = SwapFaceHolding(understanding.faces, queue)

    make_offer = _offer_maker(store, search_service, face_descriptions)
    assess, guest_tunnel, path_of = _assessor(store, hub), _guest_tunnel(hub), _path_of(store)

    sessions = swap.SwapSessions(
        swap.SessionStore(store.database),
        downloads.secrets,
        jobs=queue,
        hoster=downloads.tunnels,
        egress=downloads.egress,
        read_route=guest_tunnel,
        staging=settings.data_dir / "swap" / "staging",
        prepare=partial(swap_transfer.prepare, settings=settings),
        make_offer=make_offer,
        assess=assess,
        land=partial(
            swap_ingest.land,
            import_file=partial(capture.import_file, settings=settings, reindexer=reindexer),
            database=store.database,
            reindexer=reindexer,
            faces=face_holding,
            settings=settings,
        ),
        land_fingerprints=partial(
            swap_ingest.land_fingerprints, database=store.database, faces=face_holding
        ),
        face_model=face_holding.recognizer,
        exit_of=downloads.tunnels.exit_address,
        server_of=downloads.tunnels.server_address,
        path_of=path_of,
        weigh=partial(swap_weight.weigh, store.access, store.database, search_service),
    )
    await sessions.settle_after_restart()
    swap.register_handlers(sessions)
    queue.listen_for_settled(swap.SWAP_SESSION, sessions.on_settled)
    provide(app, swap.SESSIONS, sessions)
