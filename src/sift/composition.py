# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where two areas are allowed to know about each other: the wiring, and nothing else.

The one module naming two slices in one file, to turn a stash-box's names into rows and file
what a drop fetched. Nothing here decides anything: callers pass what was decided.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sift.kernel.access.catalog import (
    attribute_assets_recording_on,
    by_sift,
    create_person_on,
    ensure_site,
    file_asset_under_username,
    link_asset_to_site,
    mark_created_by_box,
    mark_pmv_creator,
    people_named,
)
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce, telling, who_may_see_a_file
from sift.kernel.content import UserStateStore
from sift.kernel.db import Database
from sift.kernel.jobs.queue import JobQueue
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.kernel.vocabulary import VIA_STASH
from sift.slices.collections.service import CollectionService
from sift.slices.faces import packs, weights
from sift.slices.faces.jobs import ask_for_rematching
from sift.slices.faces.recognize import unpack
from sift.slices.faces.service import FaceService
from sift.slices.people.service import PeopleService
from sift.slices.photo_sets.service import PhotoSetService
from sift.slices.songs.service import SongService
from sift.slices.swap.ingest import OfferedFace
from sift.slices.swap.models import MAX_FACES_PER_PERSON, OfferedFaces
from sift.slices.swap.offer import faces_of
from sift.slices.tags_ratings.service import DuplicateTag, TagService

log = get_logger(__name__)


class LibraryNaming:
    """A stash-box's names as this library's rows, by name, case-insensitive; several is none."""

    def __init__(self, database: Database, tags: TagService) -> None:
        self._db = database
        self._tags = tags

    async def person_named(self, name: str, *, creating: bool) -> str | None:
        """Who this word names here (by name or alias), made when nobody and that is allowed."""
        cleaned = name.strip()
        if not cleaned:
            return None
        found = await people_named(self._db, cleaned)
        if len(found) == 1:
            return found[0]
        if found:
            # More than one. Ambiguous is not a match, and it is not a reason to make a third.
            log.info("enrich.person.ambiguous", candidates=len(found))
            return None
        if not creating:
            return None
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            # `stash` every time this module creates; `mark_created_by_box` upgrades it to 'box'.
            return await create_person_on(connection, cleaned, made=by_sift(VIA_STASH))

    async def site_named(
        self, name: str, *, creating: bool, address: str | None = None
    ) -> str | None:
        """The site this word names, made (with the site's part of `address`) when none is."""
        cleaned = name.strip()
        if not cleaned:
            return None
        row = await self._db.fetch_one("SELECT id FROM sites WHERE name = ?", (cleaned,))
        if row is not None:
            return str(row["id"])
        if not creating:
            return None
        return await ensure_site(self._db, cleaned, made=by_sift(VIA_STASH), address=address)

    async def tag_named(self, name: str, *, creating: bool) -> str | None:
        """The tag this word means, made when none is; a racing second maker takes its row."""
        cleaned = name.strip()
        if not cleaned:
            return None
        row = await self._db.fetch_one("SELECT id FROM tags WHERE name = ?", (cleaned,))
        if row is not None:
            return str(row["id"])
        if not creating:
            return None
        try:
            made = await self._tags.create(cleaned, made=by_sift(VIA_STASH))
        except DuplicateTag:
            again = await self._db.fetch_one("SELECT id FROM tags WHERE name = ?", (cleaned,))
            return None if again is None else str(again["id"])
        return made.id

    async def mark_pmv_creator(self, person_id: str) -> None:
        """Say this person makes the edits, by the catalog's statement; its answer is dropped."""
        await mark_pmv_creator(self._db, person_id)

    async def mark_created_by_box(self, kind: str, local_id: str, source_id: str) -> None:
        """Say this row was invented from that box's answer, by the catalog's own statement."""
        await mark_created_by_box(self._db, kind, local_id, source_id)


def _pass_of(source: str) -> str:
    """The pass a filing's source word names: a box's filing is `stash_box`, its pass `stash`."""
    return VIA_STASH if source == "stash_box" else source


class LibraryFiling:
    """What is on a file, by name, and how to put something on it; the caller resolved the file."""

    def __init__(self, database: Database, tags: TagService) -> None:
        self._db = database
        self._tags = tags

    async def people_on(self, asset_id: str) -> tuple[str, ...]:
        rows = await self._db.fetch_all(
            "SELECT p.name AS name FROM asset_people ap JOIN people p ON p.id = ap.person_id"
            " WHERE ap.asset_id = ? ORDER BY COALESCE(p.name_sort, p.name), p.id",
            (asset_id,),
        )
        return tuple(str(row["name"]) for row in rows)

    async def tags_on(self, asset_id: str) -> tuple[str, ...]:
        rows = await self._db.fetch_all(
            "SELECT t.name AS name FROM asset_tags at JOIN tags t ON t.id = at.tag_id"
            " WHERE at.asset_id = ? ORDER BY COALESCE(t.name_sort, t.name), t.id",
            (asset_id,),
        )
        return tuple(str(row["name"]) for row in rows)

    async def site_of(self, asset_id: str) -> str | None:
        """The site a file came from, through the username that posted it."""
        row = await self._db.fetch_one(
            "SELECT p.name AS name FROM asset_usernames aa"
            " JOIN usernames a ON a.id = aa.username_id"
            " JOIN sites p ON p.id = a.site_id"
            " WHERE aa.asset_id = ? ORDER BY p.name COLLATE NOCASE LIMIT 1",
            (asset_id,),
        )
        return None if row is None else str(row["name"])

    async def attribute(
        self, asset_id: str, person_id: str, *, source: str, box_id: str | None = None
    ) -> None:
        """Put somebody on a file, marked with how; the first answer on the file is kept."""
        async with self._db.write() as connection:
            written = await attribute_assets_recording_on(
                connection,
                asset_ids=(asset_id,),
                person_id=person_id,
                source=source,
                box_id=box_id,
            )
            if written:
                announce(await who_may_see_a_file(connection), About.LIBRARY)

    async def attach_tag(
        self, asset_id: str, tag_id: str, *, source: str, box_id: str | None = None
    ) -> None:
        """Put a tag on a file, marked with how it got there."""
        # Sift put it there, said by the pass's own word (the filing's `stash_box` is `stash`).
        await self._tags.assign(
            (asset_id,),
            (tag_id,),
            add=True,
            source=source,
            box_id=box_id,
            actor=Actor.sift(_pass_of(source)),
        )

    async def file_under_site(
        self, asset_id: str, site: str, *, source: str, box_id: str | None = None
    ) -> None:
        """File a file under a site by name, marked with how; the first answer is kept."""
        await link_asset_to_site(
            self._db,
            asset_id=asset_id,
            site=site,
            source=source,
            # The site it may invent is that same act, said in the pass's own word.
            made=by_sift(_pass_of(source)),
            box_id=box_id,
        )

    async def accounts_on(self, asset_id: str) -> tuple[Mapping[str, str], ...]:
        """The named usernames a file is filed under, each with its Site and its page."""
        rows = await self._db.fetch_all(
            "SELECT p.name AS site, a.name AS handle, a.url AS url FROM asset_usernames aa"
            " JOIN usernames a ON a.id = aa.username_id"
            " JOIN sites p ON p.id = a.site_id"
            " WHERE aa.asset_id = ? AND a.name <> ''"
            " ORDER BY COALESCE(p.name_sort, p.name), COALESCE(a.name_sort, a.name)",
            (asset_id,),
        )
        return tuple(
            {"site": str(row["site"]), "handle": str(row["handle"]), "url": str(row["url"] or "")}
            for row in rows
        )

    async def file_under_username(
        self,
        asset_id: str,
        *,
        site: str,
        handle: str,
        url: str | None,
        source: str,
        person_id: str | None = None,
        box_id: str | None = None,
    ) -> bool:
        """File a file under a named username on a site: the catalog's one body for it."""
        return await file_asset_under_username(
            self._db,
            asset_id=asset_id,
            site=site,
            name=handle,
            url=url,
            source=source,
            made=by_sift(_pass_of(source)),
            person_id=person_id,
            box_id=box_id,
        )


class LibraryDropFiling:
    """Filing what a dropped link fetched under the thing it was dropped on: a dispatch table.

    Each branch calls the same code the matching menu verb calls. The drop was allowed when it was
    taken; a target gone since means nothing to file.
    """

    def __init__(
        self,
        database: Database,
        *,
        people: PeopleService,
        tags: TagService,
        collections: CollectionService,
        photo_sets: PhotoSetService,
        songs: SongService,
        user_state: UserStateStore,
    ) -> None:
        self._db = database
        self._people = people
        self._tags = tags
        self._songs = songs
        self._collections = collections
        self._photo_sets = photo_sets
        self._user_state = user_state

    async def file_under(
        self, *, kind: str, target_id: str, asset_ids: Sequence[str], for_user: str
    ) -> int:
        """Put these files where the drop said; how many were filed, zero for an unknown kind."""
        if not asset_ids:
            return 0
        if kind == "person":
            return await self._on_person(target_id, asset_ids)
        if kind == "site":
            return await self._on_site(target_id, asset_ids, for_user)
        if kind == "collection":
            return await self._collections.add(target_id, asset_ids, actor=Actor.user(for_user))
        if kind == "photo_set":
            return await self._photo_sets.add(target_id, asset_ids, actor=Actor.user(for_user))
        if kind == "song":
            # The same call the song's page makes: a file carries one song.
            return len(await self._songs.add(target_id, asset_ids, actor=Actor.user(for_user)))
        if kind == "tag":
            return await self._tags.assign(
                asset_ids,
                (target_id,),
                add=True,
                source="by_hand",
                actor=Actor.user(for_user),
            )
        if kind == "favorite":
            return await self._favorite(asset_ids, for_user)
        log.warning("filing.unknown_kind", kind=kind)
        return 0

    async def _on_person(self, target_id: str, asset_ids: Sequence[str]) -> int:
        async with self._db.write() as connection:
            # Counted from what it wrote: a file already on that person is not filed twice.
            written = await attribute_assets_recording_on(
                connection, asset_ids=asset_ids, person_id=target_id, source="by_hand"
            )
            if written:
                announce(await who_may_see_a_file(connection), About.LIBRARY)
        return len(written)

    async def _on_site(self, target_id: str, asset_ids: Sequence[str], for_user: str) -> int:
        # By name, the same call the `Add to > Site` verb makes.
        name = await self._site_name(target_id)
        if name is None:
            return 0
        # The record names the user whose drop this was, not the job.
        return await self._people.file_under_sites(
            list(asset_ids), [name], actor=Actor.user(for_user)
        )

    async def _favorite(self, asset_ids: Sequence[str], for_user: str) -> int:
        # An opinion, so the user is carried this far; `target_id` names nothing here.
        for asset_id in asset_ids:
            await self._user_state.set_favorite(asset_id, for_user, True)
        return len(asset_ids)

    async def _site_name(self, site_id: str) -> str | None:
        row = await self._db.fetch_one("SELECT name FROM sites WHERE id = ?", (site_id,))
        return None if row is None else str(row["name"])


class SwapFaceDescriptions:
    """The face feature's descriptions, as a swap's offer asks for them."""

    def __init__(self, faces: FaceService) -> None:
        self._faces = faces

    async def descriptions(
        self, person_ids: Sequence[str], *, peer_model: str | None = None
    ) -> Mapping[str, OfferedFaces] | None:
        found = await self._faces.descriptions_for_swap(person_ids)
        if found is None:
            return None
        recognizer, dimension, per_person = found
        # Pictures only when the guest named the other model: a picture is a photograph of somebody.
        pictures = (
            await self._faces.pictures_for_swap(list(per_person), best=MAX_FACES_PER_PERSON)
            if peer_model is not None and peer_model != recognizer
            else None
        )
        return {
            person_id: faces_of(recognizer, dimension, references, pictures=pictures)
            for person_id, references in per_person.items()
        }


class SwapFaceHolding:
    """The face feature's pack import for a swap's landing, idempotent on the pack's name."""

    def __init__(self, faces: FaceService, queue: JobQueue) -> None:
        self._faces = faces
        self._queue = queue
        self._held: set[str] = set()

    async def recognizer(self) -> str | None:
        if not await self._faces.enabled():
            return None
        configured = await self._faces.configuration()
        return weights.pairing(configured.family)[1].revision

    async def hold(
        self,
        *,
        pack: str,
        person: str,
        recognizer: str,
        dimension: int,
        faces: Sequence[OfferedFace],
        suggest_only: bool = False,
        confirmed: int | None = None,
    ) -> int:
        if pack in self._held:
            return 0
        # Another model's faces come with their pictures, described again with this model.
        other_model = recognizer != await self.recognizer()
        packed = packs.PackedPerson(
            name=person,
            aliases=(),
            links=(),
            faces=tuple(
                packs.PackedFace(
                    digest=one.digest,
                    quality=one.quality,
                    vector=unpack(one.vector),
                    picture=one.picture if other_model else None,
                )
                for one in faces
            ),
            confirmed=confirmed,
        )
        raw = packs.build(
            name=pack,
            version="1",
            recognizer=recognizer,
            dimension=dimension,
            people=[packed],
            include_pictures=other_model,
        )
        outcome = await self._faces.take_from_swap(
            raw, suggest_only=suggest_only, other_model=other_model
        )
        self._held.add(pack)
        if outcome.added:
            await ask_for_rematching(self._queue)
        return outcome.added
