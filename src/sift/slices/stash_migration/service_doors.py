# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a Stash run brings across through other features' own doors."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sift.kernel.jobs import JobCanceled, JobContext
from sift.kernel.ledger import Actor
from sift.kernel.vocabulary import VIA_STASH_LIBRARY
from sift.slices.stash_migration import reader
from sift.slices.stash_migration.deciding import Known, Stashed, _master_key
from sift.slices.stash_migration.ports import LinkOn, OpinionOn
from sift.slices.stash_migration.reader import Entity
from sift.slices.stash_migration.saved_filters import translate
from sift.slices.stash_migration.service_base import COPY_NAME, Checkpoint, StashBase
from sift.slices.stash_migration.tally import Tally
from sift.slices.stash_migration.waiting import KeptGallery, KeptGroup


class DoorsMixin(StashBase):
    """What a run hands to other features through their own doors (`ports.StashDoors`)."""

    async def _galleries(
        self,
        stashed: Stashed,
        tally: Tally,
        matched: Mapping[tuple[str, int], str],
        source: str,
        actor: Actor,
    ) -> None:
        """Each gallery whose pictures are here, as a Photo Set through the one door that makes one,
        or added to the set an earlier run made for it."""
        for gallery in stashed.galleries:
            here = [
                asset_id
                for image_id in gallery.images
                if (asset_id := matched.get(("image", image_id))) is not None
            ]
            if here:
                await self._grow_gallery(source, gallery.stash_id, gallery.name, here, actor, tally)

    async def _grow_gallery(
        self,
        source: str,
        gallery_id: int,
        name: str,
        asset_ids: Sequence[str],
        actor: Actor,
        tally: Tally,
    ) -> None:
        """Pictures of one gallery that are here, into the Photo Set it became: made once (or found,
        where a zip or a folder here made one of them), then added to, so a second run never makes
        a second set. A set somebody deleted is not made again: the id kept for it says it is gone."""
        kept = await self._waiting.gallery(source, gallery_id) or KeptGallery(name, None)
        new = [one for one in dict.fromkeys(asset_ids) if one not in kept.pictures]
        if not new:
            return
        pictures = (*kept.pictures, *new)
        photo_set_id = kept.photo_set_id
        if photo_set_id is not None:
            if self.doors is not None:
                await self.doors.add_to_photo_set(photo_set_id, new, actor=actor)
        elif self._photo_sets is not None:
            photo_set_id = await self._photo_sets.holding(list(pictures))
            if photo_set_id is None:
                photo_set_id = await self._photo_sets.make(
                    list(pictures), name=kept.name, origin=VIA_STASH_LIBRARY
                )
                tally.photo_sets += photo_set_id is not None
        await self._waiting.keep_gallery(
            source, gallery_id, KeptGallery(kept.name, photo_set_id, pictures)
        )

    async def _groups(
        self,
        stashed: Stashed,
        tally: Tally,
        scenes: Mapping[int, str],
        source: str,
        actor: Actor,
        user_id: str | None,
    ) -> None:
        """Each group whose scenes are here, as a Collection of them in the group's own order, or
        added to the Collection an earlier run made for it."""
        for group in stashed.groups:
            here = [asset_id for scene_id in group.scenes if (asset_id := scenes.get(scene_id))]
            if here:
                await self._grow_group(
                    source, group.stash_id, group.name, here, actor, user_id, tally
                )

    async def _grow_group(
        self,
        source: str,
        group_id: int,
        name: str,
        asset_ids: Sequence[str],
        actor: Actor,
        user_id: str | None,
        tally: Tally,
    ) -> None:
        """Scenes of one group that are here, into the Collection it became.

        Made once, by the Collections feature's own writer, owned by whoever pressed Run (a
        Collection is somebody's); added to from then on, so a second run or a scene arriving
        later never makes a second one. A Collection somebody deleted is not made again."""
        if self.doors is None:
            return
        kept = await self._waiting.group(source, group_id) or KeptGroup(name, None)
        new = [one for one in dict.fromkeys(asset_ids) if one not in kept.scenes]
        if not new:
            return
        collection_id = kept.collection_id
        if collection_id is None and not kept.scenes:
            if user_id is None:
                return
            collection_id = await self.doors.make_collection(kept.name, user_id, actor=actor)
            if collection_id is not None:
                tally.collections += 1
        gone = (
            collection_id is not None
            and await self.doors.add_to_collection(collection_id, new, actor=actor) is None
        )
        if gone:
            collection_id = None
        await self._waiting.keep_group(
            source, group_id, KeptGroup(kept.name, collection_id, (*kept.scenes, *new))
        )

    async def _pictures(self, tally: Tally, known: Known, blobs: Path | None, actor: Actor) -> None:
        """The pictures Stash kept on the People, Sites and Tags that came across, as their covers
        where they have none, through the cover door (`StashDoors.picture`), which re-encodes every
        picture that is not a file of the library. Read from this run's copy, or from Stash's blobs
        folder where Stash keeps them as files."""
        if self.doors is None:
            return
        kinds: tuple[tuple[OpinionOn, dict[int, tuple[str, Entity]]], ...] = (
            ("person", known.person),
            ("site", known.site),
            ("tag", known.tag),
        )
        wanted = [
            (kind, local, one.picture)
            for kind, rows in kinds
            for local, one in rows.values()
            if one.picture
        ]
        if not wanted:
            return
        connection = await asyncio.to_thread(reader.open_copy, self.folder / COPY_NAME)
        try:
            for kind, local, checksum in wanted:
                await self._picture(connection, kind, local, str(checksum), blobs, actor, tally)
        finally:
            await asyncio.to_thread(connection.close)

    async def _picture(
        self,
        connection: Any,
        kind: OpinionOn,
        local: str,
        checksum: str,
        blobs: Path | None,
        actor: Actor,
        tally: Tally,
    ) -> None:
        if self.doors is None:
            return
        blob = await asyncio.to_thread(reader.picture_of, connection, checksum, blobs)
        if blob is None:
            tally.pictures_not_found += 1
        elif await self.doors.picture(kind, local, blob, actor=actor):
            tally.pictures += 1

    async def _opinions(self, tally: Tally, user_id: str, known: Known) -> None:
        """The heart and the stars Stash kept on a performer, a studio or a tag, for whoever asked.

        Theirs, like a file's rating: Stash kept one opinion per library and the person bringing it
        in is the one who held it. Only what they have not said here is filled (`StashDoors`)."""
        kinds: tuple[tuple[OpinionOn, dict[int, tuple[str, Entity]]], ...] = (
            ("person", known.person),
            ("site", known.site),
            ("tag", known.tag),
        )
        for kind, rows in kinds:
            for local, one in rows.values():
                await self._opinion(kind, local, one, user_id, tally)

    async def _opinion(
        self, kind: OpinionOn, local: str, one: Entity, user_id: str | None, tally: Tally
    ) -> None:
        if self.doors is None or user_id is None or (not one.favorite and one.rating is None):
            return
        if await self.doors.opinion(kind, local, user_id, favorite=one.favorite, rating=one.rating):
            tally.favorites += 1 if one.favorite else 0
            tally.entity_ratings += 1 if one.rating is not None else 0

    async def _links(
        self,
        context: JobContext,
        stashed: Stashed,
        tally: Tally,
        known: Known,
        kept: Checkpoint | None = None,
    ) -> None:
        """The stash-box ids Stash kept on its performers, studios and tags, linked here.

        Through the stash-box feature's own link, so a link made here is fetched and kept as one a
        person makes, and the enrichment never asks again who that row is. A scene's ids are the
        files' (`_file_links`)."""
        tally.scene_box_ids = len(stashed.box_ids["scene"])
        if self.doors is None:
            return
        key = await _master_key(context)
        kinds: tuple[tuple[LinkOn, str, dict[int, tuple[str, Entity]]], ...] = (
            ("person", "performer", known.person),
            ("site", "studio", known.site),
            ("tag", "tag", known.tag),
        )
        for kind, stash_kind, rows in kinds:
            for one in stashed.box_ids[stash_kind]:
                local = rows.get(one.owner)
                if local is None:
                    continue
                asked = f"{kind} {local[0]} {one.endpoint} {one.remote_id}"
                went = None if kept is None else kept.went(asked)
                if went is not None:
                    tally.links[went] = tally.links.get(went, 0) + 1
                else:
                    went = await self._link(kind, local[0], one.endpoint, one.remote_id, key, tally)
                    if kept is not None:
                        await kept.note(asked, went)
                if context.stopping() == "cancel":
                    raise JobCanceled

    async def _file_links(
        self,
        context: JobContext,
        stashed: Stashed,
        tally: Tally,
        scenes: Mapping[int, str],
        kept: Checkpoint | None = None,
    ) -> None:
        """The stash-box ids Stash kept on the scenes matched here, each its file's answer from that
        box, fetched by the id and applied as an exact match is (`StashDoors.link_file`)."""
        if self.doors is None:
            return
        key = await _master_key(context)
        for one in stashed.box_ids["scene"]:
            asset_id = scenes.get(one.owner)
            if asset_id is not None:
                asked = f"file {asset_id} {one.endpoint} {one.remote_id}"
                went = None if kept is None else kept.went(asked)
                if went is not None:
                    tally.file_links[went] = tally.file_links.get(went, 0) + 1
                else:
                    went = await self._link_file(asset_id, one.endpoint, one.remote_id, key, tally)
                    if kept is not None:
                        await kept.note(asked, went)
                if context.stopping() == "cancel":
                    raise JobCanceled

    async def _link_file(
        self, asset_id: str, endpoint: str, remote_id: str, key: bytes | None, tally: Tally
    ) -> str:
        """One file's link, counted; what it came to."""
        if self.doors is None:
            return ""
        went = (await self.doors.link_file(asset_id, endpoint, remote_id, key)).value
        tally.file_links[went] = tally.file_links.get(went, 0) + 1
        return went

    async def _link(
        self,
        kind: LinkOn,
        local: str,
        endpoint: str,
        remote_id: str,
        key: bytes | None,
        tally: Tally,
    ) -> str:
        """One row's link, counted; what it came to."""
        if self.doors is None:
            return ""
        went = (await self.doors.link(kind, local, endpoint, remote_id, key)).value
        tally.links[went] = tally.links.get(went, 0) + 1
        return went

    async def _searches(self, stashed: Stashed, tally: Tally, user_id: str) -> None:
        """Stash's saved filters over scenes and images, kept as saved searches over files."""
        for one in stashed.filters:
            said = translate(
                one,
                tag=stashed.tag_names.get,
                person=stashed.person_names.get,
                site=stashed.site_names.get,
            )
            if said.why:
                tally.filters_not_brought.append(f"{said.name} ({said.why})")
            elif self.doors is not None and await self.doors.keep_search(
                user_id, said.name, said.query
            ):
                tally.saved_searches += 1
            elif self.doors is not None:
                tally.filters_not_brought.append(f"{said.name} (a saved search by that name)")
