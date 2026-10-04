# SPDX-License-Identifier: AGPL-3.0-or-later
"""The doors a Stash run writes through, filled in from the features that own them.

`stash_migration.ports.StashDoors`, built here because this is the one place the migration and the
features it writes into may be named together. Every service is read off the application
when it is used rather than captured, for the reason `_photo_set_maker` gives: they are provided
while the application is being assembled.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import FastAPI

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.ledger import Actor
from sift.kernel.records import Subject
from sift.slices import (
    backup,
    collections,
    loops,
    people,
    photo_sets,
    search,
    stash_boxes,
    tags_ratings,
)
from sift.slices.loops.service import Refused
from sift.slices.search.service import ASSET_WALL, TooMany
from sift.slices.stash_migration import StashRefused
from sift.slices.stash_migration.ports import Linked, LinkOn, OpinionOn, Seed

#: Which statement reads a user's heart and stars on each kind, written out rather than built.
_OPINION = {
    "person": "SELECT favorite, rating FROM person_user_state WHERE person_id = ? AND user_id = ?",
    "site": "SELECT favorite, rating FROM site_user_state WHERE site_id = ? AND user_id = ?",
    "tag": "SELECT favorite, rating FROM tag_user_state WHERE tag_id = ? AND user_id = ?",
}

_SUBJECT: dict[str, Subject] = {"person": Subject.PERSON, "site": Subject.SITE, "tag": Subject.TAG}

#: Whether a Photo Set a run made is still there. Asked before adding, because a set somebody
#: deleted is not made again by a picture that arrives for it.
_PHOTO_SET_EXISTS = "SELECT 1 FROM photo_sets WHERE id = ?"

#: The same question of a Collection a run made, for the same reason.
_COLLECTION_EXISTS = "SELECT 1 FROM collections WHERE id = ?"


class StashDoorsFromApp:
    """`StashDoors`, each door the owning feature's own writer."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    async def _viewer(self, user_id: str) -> Viewer | None:
        return await wiring.part_of_app(self._app, wiring.ACCESS).load_viewer(user_id)

    async def mark(
        self,
        asset_id: str,
        start_ms: int,
        end_ms: int,
        *,
        name: str | None,
        tag_ids: Sequence[str],
        created_by: str,
    ) -> bool:
        if await wiring.part_of_app(self._app, loops.SERVICE).marked(asset_id, start_ms, end_ms):
            return False
        asset = await wiring.part_of_app(self._app, wiring.CONTENT).get(asset_id)
        if asset is None:
            return False
        duration = asset.duration_ms
        if duration is not None:
            # A moment near the end runs to the end rather than past it.
            end_ms = min(end_ms, duration)
        service = wiring.part_of_app(self._app, loops.SERVICE)
        try:
            made = await service.create(
                asset_id=asset_id,
                start_ms=start_ms,
                end_ms=end_ms,
                name=name,
                created_by=created_by,
                duration_ms=duration,
            )
        except Refused:
            return False
        for tag_id in tag_ids:
            await service.set_tag(made.id, tag_id, on=True)
        return True

    async def opinion(
        self,
        kind: OpinionOn,
        entity_id: str,
        user_id: str,
        *,
        favorite: bool,
        rating: int | None,
    ) -> bool:
        database = wiring.part_of_app(self._app, wiring.DATABASE)
        held = await database.fetch_one(_OPINION[kind], (entity_id, user_id))
        heart = favorite and not (held is not None and held["favorite"])
        stars = rating if rating is not None and (held is None or held["rating"] is None) else None
        if not heart and stars is None:
            return False
        if kind == "tag":
            viewer = await self._viewer(user_id)
            if viewer is None:
                return False
            tags = wiring.part_of_app(self._app, tags_ratings.SERVICE)
            if heart:
                await tags.set_favorite(viewer, entity_id, favorite=True)
            if stars is not None:
                await tags.set_rating(viewer, entity_id, rating=stars)
            return True
        catalog = wiring.part_of_app(self._app, people.SERVICE)
        if kind == "person":
            if heart:
                await catalog.set_person_favorite(entity_id, user_id, True)
            if stars is not None:
                await catalog.set_person_rating(entity_id, user_id, stars)
        else:
            if heart:
                await catalog.set_site_favorite(entity_id, user_id, True)
            if stars is not None:
                await catalog.set_site_rating(entity_id, user_id, stars)
        return True

    async def keep_search(self, user_id: str, name: str, query: str) -> bool:
        viewer = await self._viewer(user_id)
        if viewer is None:
            return False
        service = wiring.part_of_app(self._app, search.SERVICE)
        kept = await service.saved_searches(viewer)
        if any(one.kind == ASSET_WALL and one.name == " ".join(name.split()) for one in kept):
            return False
        try:
            await service.save_search(viewer, name, query, ASSET_WALL)
        except (TooMany, ValueError):
            return False
        return True

    async def link(
        self,
        kind: LinkOn,
        local_id: str,
        endpoint: str,
        remote_id: str,
        master_key: bytes | None,
    ) -> Linked:
        service = wiring.part_of_app(self._app, stash_boxes.SERVICE)
        box = await _box_at(service, endpoint)
        if box is None:
            return Linked.NO_BOX
        if kind == "site" and box.sites_are != "site":
            return Linked.NOT_A_SITE
        subject = _SUBJECT[kind]
        if any(one.source_id == box.id for one in await service.links_of(subject, local_id)):
            return Linked.ALREADY
        if await service.kept_local(subject, local_id):
            return Linked.KEPT_LOCAL
        try:
            made = await service.link(subject, local_id, box.id, remote_id, master_key)
        except stash_boxes.StashBoxUnreachable:
            return Linked.NOT_ASKED
        return Linked.NOT_ASKED if made is None else Linked.LINKED

    async def link_file(
        self, asset_id: str, endpoint: str, remote_id: str, master_key: bytes | None
    ) -> Linked:
        service = wiring.part_of_app(self._app, stash_boxes.SERVICE)
        box = await _box_at(service, endpoint)
        if box is None:
            return Linked.NO_BOX
        access = wiring.part_of_app(self._app, wiring.ACCESS)
        deps = stash_boxes.ScanDeps(
            access=access,
            settings=wiring.part_of_app(self._app, wiring.SETTINGS_HUB),
            enricher=wiring.part_of_app(self._app, wiring.ENRICHER),
            naming=wiring.part_of_app(self._app, wiring.NAMING),
            viewer_for=access.load_viewer,
        )
        went = await stash_boxes.link_known_scene(
            service,
            deps,
            asset_id,
            box.id,
            remote_id,
            master_key,
            queue=wiring.part_of_app(self._app, wiring.QUEUE),
        )
        return Linked(went.value)

    async def add_to_photo_set(
        self, photo_set_id: str, asset_ids: Sequence[str], *, actor: Actor
    ) -> int | None:
        database = wiring.part_of_app(self._app, wiring.DATABASE)
        if await database.fetch_one(_PHOTO_SET_EXISTS, (photo_set_id,)) is None:
            return None
        return await wiring.part_of_app(self._app, photo_sets.SERVICE).add(
            photo_set_id, asset_ids, actor=actor
        )

    async def make_collection(self, name: str, owner_id: str, *, actor: Actor) -> str | None:
        if await self._viewer(owner_id) is None:
            return None
        made = await wiring.part_of_app(self._app, collections.SERVICE).create(
            name, owner_id=owner_id, actor=actor
        )
        return made.id

    async def add_to_collection(
        self, collection_id: str, asset_ids: Sequence[str], *, actor: Actor
    ) -> int | None:
        database = wiring.part_of_app(self._app, wiring.DATABASE)
        if await database.fetch_one(_COLLECTION_EXISTS, (collection_id,)) is None:
            return None
        return await wiring.part_of_app(self._app, collections.SERVICE).add(
            collection_id, asset_ids, actor=actor
        )

    async def picture(self, kind: OpinionOn, entity_id: str, blob: bytes, *, actor: Actor) -> bool:
        # `fill`, never `keep`: a picture somebody chose here is never replaced by Stash's. No box
        # is named, since no stash-box gave it.
        covers = wiring.part_of_app(self._app, wiring.SUBJECT_COVERS)
        return await covers.fill(_SUBJECT[kind], entity_id, blob, actor=actor, box=None)

    async def new_library(self, name: str, actor: Viewer, seed: Seed) -> str:
        libraries = wiring.part_of_app(self._app, backup.LIBRARIES)
        try:
            made = await libraries.create(name, actor, seed=seed)
        except (backup.LibraryError, backup.BackupError) as refused:
            raise StashRefused(str(refused), status=getattr(refused, "status", 409)) from refused
        return made.id


async def _box_at(
    service: stash_boxes.StashBoxService, endpoint: str
) -> stash_boxes.BoxView | None:
    """The configured box at this address, read as Stash spells it (any case, a trailing slash)."""
    wanted = endpoint.strip().rstrip("/").casefold()
    found = await service.boxes()
    return next((one for one in found if one.endpoint.rstrip("/").casefold() == wanted), None)


def stash_doors(app: FastAPI) -> StashDoorsFromApp:
    """The doors, read off the application as they are used."""
    return StashDoorsFromApp(app)
