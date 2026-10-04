# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where two areas are allowed to know about each other: the wiring, and nothing else.

A slice never imports another slice. That rule is what keeps a feature removable and what stops the
application becoming one object with everything hanging off it, and it leaves exactly one job
nobody can do from inside a slice: turning a name a stash-box said into a row, when people, tags and
sites are owned by two different areas.

So the shapes are declared in the kernel, the implementation is HERE, and the assembly in `main`
hands it to whoever needs it. This module is the only place in Sift that names two slices in one
file, and it does so to answer five small questions and no others.

**Nothing here decides anything.** Whether a name may become a row is `creating`, passed in from the
decision somebody took on a button; what should be written at all was settled by the enrichment
rules long before this is reached.
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
    """Turning a stash-box's names into this library's rows, or finding the row each one means.

    Every lookup is by name and case-insensitive, because that is the only handle a stash-box gives:
    its own ids name rows in its own database. A name that matches SEVERAL people answers with none
    of them: two people really can share a spelling, and picking the first is picking at random
    and calling it a match.
    """

    def __init__(self, database: Database, tags: TagService) -> None:
        self._db = database
        self._tags = tags

    async def person_named(self, name: str, *, creating: bool) -> str | None:
        """Who this word names here, making them when it names nobody and that is allowed.

        Matched by name OR by alias, through the same lookup the rest of Sift uses, so a
        stash-box's spelling that somebody already wrote down as an also-known-as finds the person
        it belongs to rather than creating a second one beside them.
        """
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
            # `stash` every time this module creates: the only thing that reaches these three
            # lookups is a stash-box's answer being applied. A row that turns out to be the box's
            # own invention is upgraded to 'box' moments later by `mark_created_by_box`, which
            # keeps this word, so a box later forgotten still leaves the row saying how it came.
            return await create_person_on(connection, cleaned, made=by_sift(VIA_STASH))

    async def site_named(
        self, name: str, *, creating: bool, address: str | None = None
    ) -> str | None:
        """The site this word names, made when it names none and that is allowed.

        The shared upsert, so a site that already exists under a different capitalisation is found
        rather than duplicated: the name is unique under a case-insensitive collation.

        `address` is any address the box gave on that site (a username's page is one), and a
        site made here is made with the site's own part of it (`catalog.site_home`), so it is
        recognised by where it lives from its first drawing. Never written onto a site that exists.
        """
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
        """The tag this word means, made when it means none and that is allowed.

        The race between looking and making is handled by catching the refusal rather than by
        locking: two imports asking for the same new tag at once is an ordinary thing, the name is
        unique, and the second one wants the row the first just made.
        """
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
        """Say this person makes the edits. The catalog's own statement, called from here.

        Here for the reason the three lookups above are: `people` is a catalog table, the two
        features that decide this flag are in different slices, and neither may import the other.
        The statement itself is `catalog.mark_pmv_creator`, the same one `StashBoxService.link`
        calls when somebody links a creator by hand, so the two roads write one row the one way.

        Its answer is dropped. It reports whether the mark MOVED, which is a fact about the last
        time this person was recognised rather than about this write; see the seam's own note.
        """
        await mark_pmv_creator(self._db, person_id)

    async def mark_created_by_box(self, kind: str, local_id: str, source_id: str) -> None:
        """Say this row was invented from that box's answer. The catalog's own statement again.

        Here for the reason every other verb on this seam is: the three tables are the catalog's,
        and the slice that knows a row was just invented may not import the slices that own them.

        Its answer is dropped, which is the seam's shape rather than a loss: it reports whether the
        row was still unclaimed, and a caller acting on that would be acting on whether somebody
        else got there first.
        """
        await mark_created_by_box(self._db, kind, local_id, source_id)


def _pass_of(source: str) -> str:
    """The pass a filing's source word names: a box's filing is `stash_box`, its pass `stash`."""
    return VIA_STASH if source == "stash_box" else source


class LibraryFiling:
    """What is on a file, and how to put something on it.

    The reads are unscoped and answer with NAMES rather than rows, which is the whole of what an
    enrichment plan compares against. Whoever calls this has already resolved the file through the
    access layer; this is a read of the join tables and cannot tell a visible file from a concealed
    one, which is why it does not pretend to.
    """

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
        """The site a file came from, through the username that posted it.

        Through the username rather than a column, because that is where the fact lives: a download
        records which username it fetched from, and the username records which site it is on.
        """
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
        """Put somebody on a file, marked with how they got there.

        The mark is what makes the write findable again. Every other attribution on the file is
        left exactly as it is: the statement underneath keeps the FIRST answer, so a person
        somebody put there by hand does not become an automatic one because a stash-box agreed.

        A person with no picture of either kind is given this file as one by the default-covers
        trigger on the filing itself (`kernel/access/default_covers.py`): the stash-box's answer
        is a decision Sift made, the same as a folder filed, and a person it invents has no page
        anybody has opened yet. The trigger fills a gap and never replaces a picture somebody
        chose, so nothing here writes a cover.
        """
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
        # Sift put it there, `source` is the pass's own word, so the record says Sift rather
        # than naming whoever happened to be signed in when the job ran. And WHICH pass: a filing's
        # source word is the pass word except for the stash-box's, which the filing spells
        # `stash_box` and the pass list `stash`. An unknown word reaches the ledger's door as it is,
        # and the door refuses it with the sentence that says where the words live.
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
        """File a file under a site by name, marked with how that was decided.

        The same call a drop onto a Site card makes, through the one body that knows what
        "filed under a site" writes: a username row with no name, and the join to it. Written
        here rather than in the writer for the reason this module exists: the join lives in the
        permission layer's catalog and the slice that asks for it may not name it.

        The insert keeps the FIRST answer, so a file somebody filed here by hand keeps its own
        unmarked row rather than becoming a stash-box's doing because one later agreed.
        """
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
    """Filing what a dropped link fetched under the thing it was dropped ON.

    `kernel.seams.FilingSeam`, implemented here for the reason this module exists: the seven answers
    belong to six different features plus one per-user opinion, and the download feature may
    import none of them.

    **It is a dispatch table and nothing else.** Every branch calls the same code the matching verb
    in a menu calls, so a file dropped on a person and a file added to a person through `Add to`
    are one write with one set of rules, which is the property that would be lost by writing the
    membership rows here.

    Nothing here decides whether the drop was allowed. The route that took the drop resolved the
    target through the access layer before the download was queued; this runs minutes later in a job
    with no request behind it, and its only judgement is that a target which has since gone means
    nothing to file rather than an error.
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
        """Put these files where the drop said. Returns how many were filed.

        Zero for a kind nobody recognises, rather than a refusal. The kind is written on a ledger
        row that can be months old by the time a retry reads it, and a download that fails because
        an old row names a kind this version dropped is a download lost to a rename.
        """
        if not asset_ids:
            return 0
        if kind == "person":
            async with self._db.write() as connection:
                # It answers with the assets it wrote, so the count is taken here rather than
                # trusted to be the number handed in: a file already on that person is not a
                # second filing of it.
                written = await attribute_assets_recording_on(
                    connection, asset_ids=asset_ids, person_id=target_id, source="by_hand"
                )
                if written:
                    announce(await who_may_see_a_file(connection), About.LIBRARY)
            return len(written)
        if kind == "site":
            # By NAME, because that is what filing under a site takes: the row carrying the
            # attribution is a username on it, found or made from the name. The same call the
            # `Add to > Site` verb makes.
            name = await self._site_name(target_id)
            if name is None:
                return 0
            # `for_user` is the user whose drop this was, so the record names the person
            # who did it rather than the job that carried it out minutes later.
            return await self._people.file_under_sites(
                list(asset_ids), [name], actor=Actor.user(for_user)
            )
        if kind == "collection":
            return await self._collections.add(target_id, asset_ids, actor=Actor.user(for_user))
        if kind == "photo_set":
            return await self._photo_sets.add(target_id, asset_ids, actor=Actor.user(for_user))
        if kind == "song":
            # The same call the song's page makes to put files on it by hand: a file carries one
            # song, so one already on another moves, and a song the page later reads off the Site
            # fills only an empty field and so never replaces this one.
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
            # The one that is an opinion rather than a fact about the library, which is why the
            # user is carried this far. `target_id` names nothing here, the target IS the
            # heart, and the route refuses a drop on Favorites that carries one.
            for asset_id in asset_ids:
                await self._user_state.set_favorite(asset_id, for_user, True)
            return len(asset_ids)
        log.warning("filing.unknown_kind", kind=kind)
        return 0

    async def _site_name(self, site_id: str) -> str | None:
        row = await self._db.fetch_one("SELECT name FROM sites WHERE id = ?", (site_id,))
        return None if row is None else str(row["name"])


class SwapFaceDescriptions:
    """The face feature's descriptions, as a swap's offer asks for them (`swap.offer.FaceDescriptions`).

    The faces feature owns the references and the model's name; the swap owns how many travel and
    in what form. This is the one line between them.
    """

    def __init__(self, faces: FaceService) -> None:
        self._faces = faces

    async def descriptions(
        self, person_ids: Sequence[str], *, peer_model: str | None = None
    ) -> Mapping[str, OfferedFaces] | None:
        found = await self._faces.descriptions_for_swap(person_ids)
        if found is None:
            return None
        recognizer, dimension, per_person = found
        # The pictures only when the guest named the OTHER model: its numbers and ours cannot be
        # compared, and the pictures are what it can describe itself. Never otherwise: a picture is
        # a photograph of somebody, and the numbers alone do the job when the models agree.
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
    """The face feature's pack import, as a swap's landing needs it (`swap.ingest.FaceHolding`).

    A person's descriptions arrive as a pack built in memory and go in through the one door those
    rows have: the import, which refuses a pack made by another model and holds a person nobody
    here has. Idempotent on the pack's name within this process: `packs.build` stamps the time into
    the zip, so a second build of the same pack would read as a new edition and re-add the faces
    on every file of one person in one session.
    """

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
        # Another model's faces come with their pictures (`swap.ingest._hold_faces` hands on no
        # other), and the import describes those again with this install's model.
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
