# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bringing a Stash library into this one.

Read: Stash's database is copied through SQLite's backup into Sift's data folder, opened read-only,
and summarised. Run: a task over that copy matches every scene and image to a file here (by path,
then OSHash, then video fingerprint, `Finder`), brings across the People, Sites and Tags they carry,
and fills each file through the same writers a stash-box answer uses, never overwriting what this
library says. What belongs to other features goes through their own doors (`ports.StashDoors`).

Nothing lands without its file: what a missing file would have carried waits (`waiting`) until a
scan brings the file in and `land` applies it. Running again adds nothing twice.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sift.kernel.access import Viewer
from sift.kernel.access.catalog import mark_made_via
from sift.kernel.access.tag_tree import file_under_if_unfiled
from sift.kernel.content import AssetUserState, LibraryStore
from sift.kernel.content.identity_models import Carrier
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.jobs import (
    Family,
    JobCanceled,
    JobContext,
    JobFailedPermanently,
    JobHeld,
    JobQueue,
    JobState,
    get_schedule,
    registered_families,
)
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.kernel.records import Subject
from sift.kernel.vocabulary import VIA_STASH_LIBRARY
from sift.kernel.whole_file import write_json_whole
from sift.kernel.wiring import Part
from sift.slices.stash_migration import reader
from sift.slices.stash_migration.deciding import Decided as Decided
from sift.slices.stash_migration.deciding import Finder as Finder
from sift.slices.stash_migration.deciding import Known as Known
from sift.slices.stash_migration.deciding import Pictures as Pictures
from sift.slices.stash_migration.deciding import Stashed as Stashed
from sift.slices.stash_migration.deciding import _blobs_of as _blobs_of
from sift.slices.stash_migration.deciding import _by_scene as _by_scene
from sift.slices.stash_migration.deciding import _database_in as _database_in
from sift.slices.stash_migration.deciding import _entity_from as _entity_from
from sift.slices.stash_migration.deciding import _inside as _inside
from sift.slices.stash_migration.deciding import _map_path as _map_path
from sift.slices.stash_migration.deciding import _master_key as _master_key
from sift.slices.stash_migration.deciding import _place as _place
from sift.slices.stash_migration.deciding import _read_stash as _read_stash
from sift.slices.stash_migration.deciding import _records_of as _records_of
from sift.slices.stash_migration.deciding import _via as _via
from sift.slices.stash_migration.deciding import _with_what_they_carry as _with_what_they_carry
from sift.slices.stash_migration.deciding import _worn_tags as _worn_tags
from sift.slices.stash_migration.deciding import decide as decide
from sift.slices.stash_migration.deciding import names_in as names_in
from sift.slices.stash_migration.deciding import ran_after as ran_after
from sift.slices.stash_migration.deciding import said_waiting as said_waiting
from sift.slices.stash_migration.ports import OpinionOn
from sift.slices.stash_migration.reader import Entity, Item, Marker, NotAStashDatabase, StashFile
from sift.slices.stash_migration.said import note_of_landing, note_of_run
from sift.slices.stash_migration.service_base import COPY_NAME as COPY_NAME
from sift.slices.stash_migration.service_base import FOLDER as FOLDER
from sift.slices.stash_migration.service_base import MOMENT_MS as MOMENT_MS
from sift.slices.stash_migration.service_base import MOMENT_TAG as MOMENT_TAG
from sift.slices.stash_migration.service_base import PLAN_NAME as PLAN_NAME
from sift.slices.stash_migration.service_base import REPORT_NAME as REPORT_NAME
from sift.slices.stash_migration.service_base import SOURCE as SOURCE
from sift.slices.stash_migration.service_base import STASH_ARRIVED as STASH_ARRIVED
from sift.slices.stash_migration.service_base import STASH_IMPORT as STASH_IMPORT
from sift.slices.stash_migration.service_base import StashRefused as StashRefused
from sift.slices.stash_migration.service_doors import DoorsMixin
from sift.slices.stash_migration.service_read import ReadMixin
from sift.slices.stash_migration.tally import Tally, moved_since_read
from sift.slices.stash_migration.waiting import Package, Waiting, WaitingFile

log = get_logger(__name__)


#: How many scenes or images between two reports of progress and two looks for a Cancel.
BATCH = 500


#: How long the task waits between two looks at a new library's first scan.
SCAN_WAIT_SECONDS = 30.0

#: The Tasks screen's Scan task: the whole-library walk a new library's first scan is.
SCAN_TASK = "scan"


#: The three kinds of named thing, by the word the waiting rows and the doors use for each.
_KINDS: dict[str, Subject] = {"person": Subject.PERSON, "site": Subject.SITE, "tag": Subject.TAG}

#: How deep a chain of parents is followed when a waiting Site or tag is made. Stash refuses a loop
#: and so does Sift; this only stops a damaged copy from recursing without end.
_DEEPEST = 32


class StashMigration(ReadMixin, DoorsMixin):
    """Reading a Stash database and bringing it into this library. One per application."""

    # --- read ---------------------------------------------------------------------------------

    # --- run ----------------------------------------------------------------------------------

    async def run(self, context: JobContext) -> None:
        """The task. See the module's own description for the order and the rules."""
        user_id = context.job.requested_by
        if not user_id:
            raise JobFailedPermanently("Sift doesn't know who asked for this, so it has stopped.")
        read = await self.last_read()
        if read is None:
            raise JobFailedPermanently("The Stash copy has gone. Read the database again.")
        await self._wait_for_first_scan(context, user_id)
        plan = json.loads(
            await asyncio.to_thread((self.folder / PLAN_NAME).read_text, encoding="utf-8")
        )
        mapping: dict[str, str] = dict(read["mapping"])
        source = str(plan.get("source") or "")
        read_at = int(plan.get("read_at") or 0)
        actor = Actor.user(user_id)
        tally = Tally()
        moved_since_read(tally, dict(plan.get("mapping") or {}), mapping)
        if self.doors is None:
            tally.not_done = [
                "favorites",
                "stash-box ids",
                "markers",
                "saved filters",
                "groups",
                "pictures",
            ]
        connection = await asyncio.to_thread(reader.open_copy, self.folder / COPY_NAME)
        try:
            stashed = await asyncio.to_thread(_read_stash, connection)
        finally:
            await asyncio.to_thread(connection.close)
        matched = await self._match(stashed, tally, mapping)
        await context.report_progress(0.05)
        decided = decide(stashed, set(matched))
        known = await self._catalog(context, stashed, tally, actor, decided)
        await self._opinions(tally, user_id, known)
        await self._links(context, stashed, tally, known)
        if plan.get("pictures"):
            await self._pictures(tally, known, _blobs_of(plan), actor)
        scenes = await self._items(context, stashed, tally, actor, user_id, matched)
        await self._file_links(context, stashed, tally, scenes)
        await self._galleries(stashed, tally, matched, source, actor)
        await self._groups(stashed, tally, scenes, source, actor, user_id)
        await self._marks(stashed, tally, user_id, scenes, known)
        await self._searches(stashed, tally, user_id)
        await self._keep_waiting(stashed, tally, matched, mapping, known, read_at, user_id, source)
        await asyncio.to_thread(write_json_whole, self.folder / REPORT_NAME, asdict(tally))
        await context.set_progress(1.0)
        await context.set_note(note_of_run(tally))
        log.info(
            "stash.imported",
            scenes_matched=tally.scenes_matched,
            images_matched=tally.images_matched,
            waiting=tally.waiting_scenes + tally.waiting_images,
            folders_matched_since_read=tally.folders_matched_since_read,
            folders_rematched_since_read=tally.folders_rematched_since_read,
            folders_unmatched_since_read=tally.folders_unmatched_since_read,
        )

    async def _match(
        self, stashed: Stashed, tally: Tally, mapping: Mapping[str, str]
    ) -> dict[tuple[str, int], str]:
        """Which file here each scene and image is, before anything is written: what comes across
        depends on it. Counts how each was found."""
        finder = await self._finder()
        matched: dict[tuple[str, int], str] = {}
        for one in [*stashed.scenes, *stashed.images]:
            if one.kind == "scene":
                tally.scenes += 1
            else:
                tally.images += 1
            found = _found_in(finder, one, mapping, tally)
            if found is None:
                if one.files and all(stash_file.in_zip for stash_file in one.files):
                    tally.images_in_zips += 1
                continue
            asset_id, how = found
            matched[(one.kind, one.stash_id)] = asset_id
            if one.kind == "scene":
                tally.scenes_matched += 1
            else:
                tally.images_matched += 1
            if how == "place":
                tally.matched_by_place += 1
            elif how == "oshash":
                tally.matched_by_oshash += 1
            else:
                tally.matched_by_fingerprint += 1
        return matched

    async def _catalog(
        self,
        context: JobContext,
        stashed: Stashed,
        tally: Tally,
        actor: Actor,
        decided: Decided,
    ) -> Known:
        """Tags with their parents, Sites with theirs, and People: only those that come across now
        (`decide`). Answers which row here each one of Stash's became, for the parts of the run
        that follow."""
        known = Known()
        ids: dict[str, str] = {}
        for one in stashed.tags:
            if ("tag", one.name) not in decided.come:
                continue
            local = await self._made(Subject.TAG, one.name, via=_via(("tag", one.name), decided))
            if local is None:
                continue
            ids[one.name] = local
            known.tag[one.stash_id] = (local, one)
            await self._merge(Subject.TAG, local, one, actor)
            tally.tags += 1
            tally.tags_without_files += 1 if ("tag", one.name) in decided.without_files else 0
        for one in stashed.tags:
            if one.parent and one.name in ids and one.parent in ids:
                async with self._db.write() as writing:
                    if await file_under_if_unfiled(
                        writing, ids[one.name], ids[one.parent], actor=actor
                    ):
                        tally.tags_filed_under_a_parent += 1
        tally.tags_with_several_parents = list(stashed.several_parents)
        await context.report_progress(0.08)
        for one in stashed.studios:
            if ("site", one.name) not in decided.come:
                continue
            local = await self._made(Subject.SITE, one.name, via=_via(("site", one.name), decided))
            if local is None:
                continue
            known.site[one.stash_id] = (local, one)
            await self._merge(Subject.SITE, local, one, actor)
            tally.sites += 1
            tally.sites_without_files += 1 if ("site", one.name) in decided.without_files else 0
        await context.report_progress(0.1)
        await self._catalog_people(context, stashed, tally, actor, decided, known)
        await context.report_progress(0.15)
        return known

    async def _catalog_people(
        self,
        context: JobContext,
        stashed: Stashed,
        tally: Tally,
        actor: Actor,
        decided: Decided,
        known: Known,
    ) -> None:
        """The People of `_catalog` that come across now, each kept in `known`."""
        for one in stashed.performers:
            if ("person", one.name) not in decided.come:
                continue
            local = await self._made(
                Subject.PERSON, one.name, via=_via(("person", one.name), decided)
            )
            if local is None:
                continue
            known.person[one.stash_id] = (local, one)
            await self._merge(Subject.PERSON, local, one, actor)
            tally.people += 1
            if ("person", one.name) in decided.without_files:
                tally.people_without_files += 1
            if context.stopping() == "cancel":
                raise JobCanceled

    async def _merge(self, subject: Subject, local: str, one: Entity | Item, actor: Actor) -> None:
        """Offer one row's fields to the writer that owns it, every field on Merge.

        The rows the write invents (a scene's People, its Site, its Tags) are named as made from
        this Stash library, as the rows `_made` makes are.
        """
        decided = await self._enricher.plan_for(
            subject=subject, local_id=local, source_id=SOURCE, offered=one.fields, strategies={}
        )
        if decided is None or not decided.writes:
            return
        invented = await self._enricher.missing_for(decided)
        await self._enricher.apply(decided, creating=True, actor=actor)
        for missing in invented:
            made = await self._named(missing.kind, missing.name, creating=False)
            if made is not None:
                await mark_made_via(self._db, missing.kind, made, VIA_STASH_LIBRARY)

    async def _made(
        self, subject: Subject, name: str, *, via: str = VIA_STASH_LIBRARY
    ) -> str | None:
        """The row this name means here, made when it means none, and then said to be made from
        this Stash library rather than from a stash-box's answer (the lookups' own word).

        `via` is `VIA_STASH_UNATTACHED` for a row Stash attached to nothing: the mark the People,
        Sites and Tags walls filter by (`created=stash_unattached`), so the review of those is one
        list. Only a row made here wears it; one this library already had is left as it was."""
        found = await self._named(subject.value, name, creating=False)
        if found is not None:
            return found
        made = await self._named(subject.value, name, creating=True)
        if made is not None:
            await mark_made_via(self._db, subject.value, made, via)
        return made

    async def _named(self, kind: str, name: str, *, creating: bool) -> str | None:
        """One of the three lookups, by the subject's own word."""
        if kind == Subject.PERSON.value:
            return await self._naming.person_named(name, creating=creating)
        if kind == Subject.SITE.value:
            return await self._naming.site_named(name, creating=creating)
        if kind == Subject.TAG.value:
            return await self._naming.tag_named(name, creating=creating)
        return None

    async def _finder(self) -> Finder:
        """Every place and fingerprint this library holds, read once, through the kernel."""
        roots = [(root.id, Path(root.abs_path)) for root in await self._library.roots()]
        places = {
            (one.root_id, one.rel_path.casefold()): one.asset_id
            for one in await self._content.every_location()
        }
        by_oshash: dict[str, str] = {}
        by_phash: dict[str, str] = {}
        for asset_id, oshash, phash in await self._content.every_fingerprint():
            if oshash:
                by_oshash.setdefault(str(oshash).lower(), asset_id)
            if phash:
                by_phash.setdefault(str(phash).lower(), asset_id)
        return Finder(roots, places, by_oshash, by_phash)

    async def _items(
        self,
        context: JobContext,
        stashed: Stashed,
        tally: Tally,
        actor: Actor,
        user_id: str,
        matched: Mapping[tuple[str, int], str],
    ) -> dict[int, str]:
        """Every matched scene, then every matched image: written, and rated for whoever asked.
        Answers which file here each matched scene is, for the markers."""
        everything = [*stashed.scenes, *stashed.images]
        total = max(1, len(everything))
        scenes: dict[int, str] = {}
        for done, one in enumerate(everything, start=1):
            asset_id = matched.get((one.kind, one.stash_id))
            if asset_id is not None:
                await self._write_item(one, asset_id, actor, user_id, tally)
                if one.kind == "scene":
                    scenes[one.stash_id] = asset_id
            if done % BATCH == 0:
                if context.stopping() == "cancel":
                    raise JobCanceled
                await context.report_progress(0.15 + 0.7 * done / total)
        return scenes

    async def _write_item(
        self, one: Item, asset_id: str, actor: Actor, user_id: str | None, tally: Tally
    ) -> None:
        """One scene's or image's record onto its file, and the person's own opinion of it, where
        they have not given one here: the run and the pass that lands what waited both write
        through this, so a file that arrives late is given exactly what a file found immediately is."""
        await self._merge(Subject.ASSET, asset_id, one, actor)
        if user_id is None or (
            one.rating is None
            and not one.o_count
            and not one.views
            and one.resume_ms is None
            and not one.played_ms
        ):
            return
        held = await self._user_state.state_of(asset_id, user_id)
        if one.rating is not None and held.rating is None:
            await self._user_state.set_rating(asset_id, user_id, one.rating)
            tally.ratings += 1
        if one.o_count > held.o_count or one.views > held.view_count:
            await self._user_state.carry_counts(
                asset_id, user_id, o_count=one.o_count, views=one.views
            )
            tally.o_counts += 1 if one.o_count > held.o_count else 0
            tally.viewed += 1 if one.views > held.view_count else 0
        await self._carry_watching(one, asset_id, user_id, held, tally)

    async def _carry_watching(
        self, one: Item, asset_id: str, user_id: str, held: AssetUserState, tally: Tally
    ) -> None:
        """Where Stash would pick a scene up and how long it was watched, for this person, where
        they have said nothing here: a resume point fills an empty one, the time watched is raised
        to Stash's and never lowered, so a second run adds nothing. No date comes with either.

        One write through the store's own sitting-without-a-view (`record_watch_time`), which
        replaces the resume point: it is handed the one already here when there is one, so a place
        this person reached in Sift is never moved by Stash's."""
        more_ms = max(0, one.played_ms - held.watched_ms)
        resume = held.resume_ms if held.resume_ms is not None else one.resume_ms
        if more_ms == 0 and resume == held.resume_ms:
            return
        await self._user_state.record_watch_time(
            asset_id, user_id, watch_ms=more_ms, resume_ms=resume
        )
        tally.resume_points += 1 if resume != held.resume_ms else 0
        tally.time_watched += 1 if more_ms else 0

    # --- what belongs to other features --------------------------------------------------------

    async def _marks(
        self,
        stashed: Stashed,
        tally: Tally,
        user_id: str,
        scenes: Mapping[int, str],
        known: Known,
    ) -> None:
        """Each scene marker as a Loop on the video it was made on, where that video is here. A
        marker on a video that is not here waits with it (`_keep_waiting`)."""
        tag_ids = {one.name: local for local, one in known.tag.values()}
        for one in stashed.markers:
            asset_id = scenes.get(one.scene_id)
            if asset_id is None:
                tally.marks_not_here += 1
            elif self.doors is not None:
                await self._mark(asset_id, one, tag_ids, user_id, tally)

    async def _mark(
        self,
        asset_id: str,
        one: Marker,
        tag_ids: Mapping[str, str],
        user_id: str | None,
        tally: Tally,
    ) -> None:
        """One marker as a Loop, through the Loops feature's own writer. Refused where the stretch is
        already marked, which is what makes a second run add none."""
        if self.doors is None:
            return
        start_ms = max(0, round(one.start_seconds * 1000))
        words = [tag_ids[name] for name in one.tags if name in tag_ids]
        if one.end_seconds is None:
            end_ms = start_ms + MOMENT_MS
            moment_tag = await self._made(Subject.TAG, MOMENT_TAG)
            if moment_tag is not None:
                words.append(moment_tag)
        else:
            end_ms = round(one.end_seconds * 1000)
        made = await self.doors.mark(
            asset_id,
            start_ms,
            end_ms,
            name=one.title or (one.tags[0] if one.tags else None),
            tag_ids=words,
            created_by=user_id or "",
        )
        if not made:
            tally.marks_refused += 1
            return
        tally.marks += 1
        tally.marks_from_moments += 1 if one.end_seconds is None else 0

    # --- what waits for its file ---------------------------------------------------------------

    async def _keep_waiting(
        self,
        stashed: Stashed,
        tally: Tally,
        matched: Mapping[tuple[str, int], str],
        mapping: Mapping[str, str],
        known: Known,
        read_at: int,
        user_id: str,
        source: str,
    ) -> None:
        """Every scene and image whose file is not here, kept in the run's own shape with what
        Stash knew of its file, and the records of the People, Sites and Tags only they carry.

        Replaces what an earlier run kept, whole: a scene that has arrived since is matched now
        and no longer waits, and one still missing is kept once, never twice."""
        markers, box_ids = _by_scene(stashed)
        in_gallery, in_group = _memberships(stashed)
        came = (
            {("tag", one.name) for _local, one in known.tag.values()}
            | {("site", one.name) for _local, one in known.site.values()}
            | {("person", one.name) for _local, one in known.person.values()}
        )
        rows: list[Waiting] = []
        wanted: set[tuple[str, str]] = set()
        for one in [*stashed.scenes, *stashed.images]:
            if (one.kind, one.stash_id) in matched or not one.files:
                continue
            row = _waiting_of(
                one,
                markers=markers,
                box_ids=box_ids,
                in_gallery=in_gallery,
                in_group=in_group,
                mapping=mapping,
                read_at=read_at,
                user_id=user_id,
                source=source,
            )
            wanted |= names_in(one.fields, row.package["markers"]) - came
            rows.append(row)
        # A waiting person's tags and a waiting Site's or tag's parent wait with it, as their own
        # records, unless they came across already.
        wanted = _with_what_they_carry(wanted, stashed, _worn_tags(stashed)) - came
        records = _records_of(stashed, wanted)
        await self._waiting.replace(rows, records)
        tally.marks_waiting = sum(len(one.package["markers"]) for one in rows)
        tally.waiting_scenes = sum(1 for one in rows if one.kind == "scene")
        tally.waiting_images = len(rows) - tally.waiting_scenes
        tally.people_waiting = sum(1 for kind, _name in records if kind == "person")
        tally.sites_waiting = sum(1 for kind, _name in records if kind == "site")
        tally.tags_waiting = sum(1 for kind, _name in records if kind == "tag")
        tally.waiting = [said_waiting(one) for one in rows]

    async def land(self, context: JobContext) -> None:
        """The pass: apply what waits to the files that have arrived for it, then forget it.

        Asked for after a batch of new files is fingerprinted, beside the duplicate sweep and the
        stash-box sweep, and after a scan settles, because a picture is found by where it is and a
        scan is what brings a new place. Every write goes through the run's own writers
        (`_write_item`, `_mark`, `_grow_gallery`, `_ensure`), as Sift's own act from this Stash
        library. With nothing waiting it reads one row and ends."""
        if not await self._waiting.anything():
            return
        arrived = await self._arrived()
        await context.set_units(len(arrived))
        if not arrived:
            return
        actor = Actor.sift(VIA_STASH_LIBRARY)
        key = await _master_key(context) if self.doors is not None else None
        pictures = await self._pictures_chosen()
        tally = Tally()
        for done, (row, asset_id) in enumerate(arrived, start=1):
            await self._land_one(row, asset_id, actor, key, tally, pictures=pictures)
            await self._waiting.landed(row.id)
            if context.stopping() == "cancel":
                raise JobCanceled
            await context.report_progress(done / len(arrived))
        await context.set_note(note_of_landing(len(arrived), tally))
        log.info("stash.landed", files=len(arrived), marks=tally.marks)

    async def arrived_count(self) -> int:
        """How many waiting scenes and pictures a file here now carries, and so how much the pass
        has ahead of it. The pass's own question, counted."""
        if not await self._waiting.anything():
            return 0
        return len(await self._arrived_ids())

    async def _arrived_ids(self) -> dict[str, str]:
        """Which waiting rows a file here now carries, by row: the one question the pass asks,
        answered by one read of the files that carry the waiting rows' keys, through the matcher
        the run uses."""
        keys = await self._waiting.keys()
        if not keys:
            return {}
        roots = [(root.id, Path(root.abs_path)) for root in await self._library.roots()]
        heres: dict[str, tuple[str, str] | None] = {}
        for _waiting_id, one in keys:
            if one.here is not None and one.here not in heres:
                heres[one.here] = _place(roots, Path(one.here))
        carriers = await self._content.carriers_of(
            oshashes=sorted({one.oshash for _id, one in keys if one.oshash}),
            phashes=sorted({one.phash for _id, one in keys if one.phash}),
            places=sorted({place for place in heres.values() if place is not None}),
        )
        finder = _finder_of(roots, carriers)
        found: dict[str, str] = {}
        for waiting_id, one in keys:
            if waiting_id in found:
                continue
            hit = finder.find_here(
                StashFile(one.path, oshash=one.oshash, phash=one.phash),
                None if one.here is None else Path(one.here),
            )
            if hit is not None:
                found[waiting_id] = hit[0]
        return found

    async def _arrived(self) -> list[tuple[Waiting, str]]:
        found = await self._arrived_ids()
        return [(row, found[row.id]) for row in await self._waiting.rows(list(found))]

    async def _pictures_chosen(self) -> Pictures | None:
        """Whether the run that left rows waiting was asked to bring pictures, and from where: the
        choice is the read's (`choose`), kept in its plan; None when it was not asked for."""
        try:
            plan = json.loads(
                await asyncio.to_thread((self.folder / PLAN_NAME).read_text, encoding="utf-8")
            )
        except (OSError, ValueError):
            return None
        return Pictures(_blobs_of(plan)) if plan.get("pictures") else None

    async def _land_one(
        self,
        row: Waiting,
        asset_id: str,
        actor: Actor,
        key: bytes | None,
        tally: Tally,
        *,
        pictures: Pictures | None = None,
    ) -> None:
        """One waiting scene or picture onto the file that arrived for it."""
        package = row.package
        user_id = row.user_id
        if user_id is not None and not await self._still_a_user(user_id):
            user_id = None
        markers = package.get("markers") or []
        for kind, name in sorted(names_in(package.get("fields") or {}, markers)):
            await self._ensure(kind, name, actor, user_id, key, tally, pictures=pictures)
        item = Item(
            stash_id=row.stash_id,
            kind=row.kind,
            files=(),
            fields=dict(package.get("fields") or {}),
            rating=package.get("rating"),
            o_count=int(package.get("o_count") or 0),
            views=int(package.get("views") or 0),
            resume_ms=package.get("resume_ms"),
            played_ms=int(package.get("played_ms") or 0),
        )
        await self._write_item(item, asset_id, actor, user_id, tally)
        for endpoint, remote_id in package.get("box_ids") or []:
            await self._link_file(asset_id, str(endpoint), str(remote_id), key, tally)
        for marker in markers:
            one = Marker(
                scene_id=row.stash_id,
                title=str(marker.get("title") or ""),
                start_seconds=float(marker.get("start") or 0),
                end_seconds=None if marker.get("end") is None else float(marker["end"]),
                tags=tuple(str(name) for name in marker.get("tags") or ()),
            )
            tag_ids = {
                name: local
                for name in one.tags
                if (local := await self._named("tag", name, creating=False)) is not None
            }
            await self._mark(asset_id, one, tag_ids, user_id, tally)
        for gallery_id, name in package.get("galleries") or []:
            await self._grow_gallery(
                str(package.get("source") or ""),
                int(gallery_id),
                str(name),
                [asset_id],
                actor,
                tally,
            )
        for group_id, name in package.get("groups") or []:
            await self._grow_group(
                str(package.get("source") or ""),
                int(group_id),
                str(name),
                [asset_id],
                actor,
                user_id,
                tally,
            )

    async def _ensure(
        self,
        kind: str,
        name: str,
        actor: Actor,
        user_id: str | None,
        key: bytes | None,
        tally: Tally,
        depth: int = 0,
        *,
        pictures: Pictures | None = None,
    ) -> str | None:
        """A person, Site or tag a landing file carries, made from its kept record where it waited
        and is still absent, with the heart, the stars and the stash-box ids Stash kept on it.

        One that did not wait (it came across with the run, or was never Stash's) is left to the
        file's own write, which finds it or makes it. A kept record is applied once and then
        forgotten, so the next file carrying the same person finds her made."""
        record = await self._waiting.entity(kind, name)
        subject = _KINDS[kind]
        if record is None:
            return await self._named(kind, name, creating=False)
        entity = _entity_from(name, record)
        if entity.parent and depth < _DEEPEST and kind in ("site", "tag"):
            parent = await self._ensure(
                kind, entity.parent, actor, user_id, key, tally, depth + 1, pictures=pictures
            )
        else:
            parent = None
        # The tags a person or a Site wears come first, from their own kept records, so the merge
        # below finds them made rather than inventing each from its name alone.
        worn = entity.fields.get("tags")
        if kind != "tag" and isinstance(worn, list) and depth < _DEEPEST:
            for tag in worn:
                await self._ensure("tag", str(tag), actor, user_id, key, tally, depth + 1)
        local = await self._made(subject, name)
        if local is None:
            return None
        await self._merge(subject, local, entity, actor)
        if kind == "tag" and parent is not None:
            async with self._db.write() as writing:
                if await file_under_if_unfiled(writing, local, parent, actor=actor):
                    tally.tags_filed_under_a_parent += 1
        opinion_on: OpinionOn = (
            "person" if kind == "person" else "site" if kind == "site" else "tag"
        )
        await self._opinion(opinion_on, local, entity, user_id, tally)
        for endpoint, remote_id in record.get("box_ids") or []:
            await self._link(opinion_on, local, str(endpoint), str(remote_id), key, tally)
        if pictures is not None and entity.picture:
            await self._picture_from_copy(opinion_on, local, entity.picture, pictures, actor, tally)
        await self._waiting.entity_landed(kind, name)
        tally.people += 1 if kind == "person" else 0
        tally.sites += 1 if kind == "site" else 0
        tally.tags += 1 if kind == "tag" else 0
        return local

    async def _picture_from_copy(
        self,
        kind: OpinionOn,
        local: str,
        checksum: str,
        pictures: Pictures,
        actor: Actor,
        tally: Tally,
    ) -> None:
        """One picture for a person, Site or tag that landed after the run, read from the copy the
        read left (a checksum names the same picture in any copy, so a newer read is as good)."""
        copy = self.folder / COPY_NAME
        if not await asyncio.to_thread(copy.is_file):
            tally.pictures_not_found += 1
            return
        try:
            connection = await asyncio.to_thread(reader.open_copy, copy)
        except NotAStashDatabase:
            tally.pictures_not_found += 1
            return
        try:
            await self._picture(connection, kind, local, checksum, pictures.blobs, actor, tally)
        finally:
            await asyncio.to_thread(connection.close)

    async def _still_a_user(self, user_id: str) -> bool:
        row = await self._db.fetch_one(_USER_EXISTS, (user_id,))
        return row is not None

    # --- a new library ---------------------------------------------------------------------------

    async def _wait_for_first_scan(self, context: JobContext, user_id: str) -> None:
        """In a library made for this run, hold the run until its first scan has finished.

        The task is queued in the new library before it is ever opened, so it can be claimed before
        that library holds a single file. Held rather than failed: the wait passes on its own, and a
        hold hands back its attempt. The scan is asked for once, here, since nothing else knows the
        folders are new; it is the Tasks screen's own Scan task."""
        plan_path = self.folder / PLAN_NAME
        plan = json.loads(await asyncio.to_thread(plan_path.read_text, encoding="utf-8"))
        if not plan.get("after_first_scan"):
            return
        scanning = [kind for kind, family in registered_families().items() if family is Family.SCAN]
        for kind in scanning:
            for state in (JobState.QUEUED, JobState.RUNNING):
                if (await context.queue.list(job_type=kind, state=state, limit=1)).total:
                    raise JobHeld(WAITING_FOR_SCAN, retry_in=SCAN_WAIT_SECONDS)
        if not plan.get("scan_asked"):
            task = get_schedule(SCAN_TASK)
            if task is not None and task.job_type is not None:
                await context.queue.enqueue(
                    task.job_type, dict(task.payload), dedupe=True, requested_by=user_id
                )
            plan["scan_asked"] = True
            await asyncio.to_thread(write_json_whole, plan_path, plan)
            raise JobHeld(WAITING_FOR_SCAN, retry_in=SCAN_WAIT_SECONDS)
        plan["after_first_scan"] = False
        await asyncio.to_thread(write_json_whole, plan_path, plan)

    async def bring_into_new(
        self, name: str, actor: Viewer, *, pictures: bool = False, blobs: str | None = None
    ) -> str:
        """Make a library beside this one for the last read, and switch to it. Answers its id.

        Refused before anything is made: nothing read, no folder matched (a new library would have
        nothing to scan), or nothing here that makes libraries. The choice about pictures is kept
        with the read before the read goes with the new library (`choose`)."""
        read = await self.last_read()
        if read is None:
            raise StashRefused("Read a Stash database first.")
        mapping: dict[str, str] = dict(read["mapping"])
        if not mapping:
            raise StashRefused(
                "None of Stash's folders is a folder of this library, so a new library would "
                "have no files. Add the folders first, or bring it into this library."
            )
        if self.doors is None:
            raise StashRefused("Sift can't make a library from here.")
        await self.choose(pictures=pictures, blobs=blobs)
        roots = {Path(root.abs_path): root.name for root in await self._library.roots()}
        grants = [Path(grant.abs_path) for grant in await self._library.grants()]
        wanted = [(Path(path), roots.get(Path(path), Path(path).name)) for path in mapping.values()]

        async def seed(database: Path, data_dir: Path) -> None:
            await self._seed(database, data_dir, wanted, grants, actor.id)

        return await self.doors.new_library(name, actor, seed)

    async def _seed(
        self,
        database: Path,
        data_dir: Path,
        roots: Sequence[tuple[Path, str]],
        grants: Sequence[Path],
        user_id: str,
    ) -> None:
        """Give a library just made the matched folders, this read, and the queued run.

        The grants that hold those folders go with them, so its folder picker sees what this one's
        did. The report of any earlier run stays behind: it is about this library."""
        target = data_dir / FOLDER
        plan = json.loads(
            await asyncio.to_thread((self.folder / PLAN_NAME).read_text, encoding="utf-8")
        )
        plan["after_first_scan"] = True
        plan.pop("scan_asked", None)
        await asyncio.to_thread(reader.copy_in, self.folder / COPY_NAME, target / COPY_NAME)
        await asyncio.to_thread(write_json_whole, target / PLAN_NAME, plan)
        fresh = Database(database, readers=1)
        await fresh.connect()
        try:
            store = LibraryStore(fresh, self._settings)
            for grant in grants:
                if any(_inside(root, grant) for root, _name in roots):
                    await store.grant(grant)
            for path, root_name in roots:
                await store.create_root(name=root_name, abs_path=path)
            await JobQueue(fresh).enqueue(
                STASH_IMPORT, {}, requested_by=user_id, require_handler=False
            )
        finally:
            await fresh.close()
        log.info("stash.new_library_seeded", folders=len(roots))


def _found_in(
    finder: Finder, one: Item, mapping: Mapping[str, str], tally: Tally
) -> tuple[str, str] | None:
    """The file here one scene or image is, by the first of its files that is found."""
    found: tuple[str, str] | None = None
    # A file inside a zip is looked for like any other: Sift reads a zip where it is, and
    # a picture in one is kept at the zip's path with its own name under it, as Stash's is.
    for stash_file in one.files:
        found = finder.find(stash_file, mapping)
        if found is not None:
            if stash_file.in_zip:
                tally.zip_pictures_matched += 1
            break
    return found


def _memberships(stashed: Stashed) -> tuple[dict[int, list[list[Any]]], dict[int, list[list[Any]]]]:
    """Each image's galleries and each scene's groups, as `[stash id, name]` pairs."""
    in_gallery: dict[int, list[list[Any]]] = {}
    for gallery in stashed.galleries:
        for image_id in gallery.images:
            in_gallery.setdefault(image_id, []).append([gallery.stash_id, gallery.name])
    in_group: dict[int, list[list[Any]]] = {}
    for group in stashed.groups:
        for scene_id in group.scenes:
            in_group.setdefault(scene_id, []).append([group.stash_id, group.name])
    return in_gallery, in_group


def _finder_of(roots: list[tuple[str, Path]], carriers: Sequence[Carrier]) -> Finder:
    """A `Finder` over only the files that carry a waiting row's keys."""
    finder = Finder(roots, {}, {}, {})
    for carrier in carriers:
        if carrier.kind == "place" and carrier.root_id and carrier.rel_path is not None:
            finder.places.setdefault(
                (carrier.root_id, carrier.rel_path.casefold()), carrier.asset_id
            )
        elif carrier.kind == "oshash" and carrier.key:
            finder.by_oshash.setdefault(carrier.key, carrier.asset_id)
        elif carrier.kind == "phash" and carrier.key:
            finder.by_phash.setdefault(carrier.key, carrier.asset_id)
    return finder


def _waiting_of(
    one: Item,
    *,
    markers: Mapping[int, list[dict[str, Any]]],
    box_ids: Mapping[int, list[list[str]]],
    in_gallery: Mapping[int, list[list[Any]]],
    in_group: Mapping[int, list[list[Any]]],
    mapping: Mapping[str, str],
    read_at: int,
    user_id: str,
    source: str,
) -> Waiting:
    """One scene or image whose file is not here, as the row that waits for it."""
    package: Package = {
        "fields": dict(one.fields),
        "rating": one.rating,
        "o_count": one.o_count,
        "views": one.views,
        "markers": markers.get(one.stash_id, []) if one.kind == "scene" else [],
        "galleries": in_gallery.get(one.stash_id, []) if one.kind == "image" else [],
        "groups": in_group.get(one.stash_id, []) if one.kind == "scene" else [],
        "box_ids": box_ids.get(one.stash_id, []) if one.kind == "scene" else [],
        "resume_ms": one.resume_ms,
        "played_ms": one.played_ms,
        "source": source,
    }
    files = tuple(
        WaitingFile(
            path=stash_file.path,
            here=None if (here := _map_path(stash_file.path, mapping)) is None else str(here),
            oshash=stash_file.oshash.lower() if stash_file.oshash else None,
            phash=stash_file.phash,
            size_bytes=stash_file.size_bytes,
            duration_ms=stash_file.duration_ms,
        )
        for stash_file in one.files
    )
    title = one.fields.get("title")
    return Waiting(
        id=new_id(),
        kind=one.kind,
        stash_id=one.stash_id,
        read_at=read_at,
        user_id=user_id,
        label=str(title) if title else reader.stash_path(one.files[0].path).name,
        package=package,
        files=files,
    )


#: What the task says while a new library's first scan runs.
WAITING_FOR_SCAN = "Waiting for this library's first scan to finish."

#: Whether a waiting row's User is still a User. A User's table is not a permission table.
_USER_EXISTS = "SELECT 1 FROM users WHERE id = ?"


def register_stash_handlers(migration: StashMigration) -> None:
    """Claim the task and the pass. Called once, at boot, beside the other handlers."""
    from sift.kernel.jobs import register_handler

    async def handle(context: JobContext) -> None:
        await migration.run(context)

    async def land(context: JobContext) -> None:
        await migration.land(context)

    register_handler(STASH_IMPORT, handle, name="Importing a Stash library", alone=True)
    # One at a time: the settle that asks for it collapses a batch's requests onto one waiting
    # row. Beside a run it is harmless rather than prevented: every write either makes is a fill
    # or a find, so a row both apply is applied once in effect. A row the run keeps again after
    # the pass has landed it is landed, to no effect, by the next pass.
    register_handler(
        STASH_ARRIVED, land, name="Importing what Stash kept for newly imported files", alone=True
    )


#: The Stash migration.
SERVICE: Part[StashMigration] = Part("stash_migration")
