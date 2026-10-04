# SPDX-License-Identifier: AGPL-3.0-or-later
"""Links between this library's people, Sites and tags and the entries a box files them under."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from sift.kernel.access.catalog import (
    EnrichmentRun,
    EntityMaker,
    mark_pmv_creator,
)
from sift.kernel.access.catalog import (
    enrichment_of as enrichment_of_subject,
)
from sift.kernel.access.catalog import (
    made_by as maker_of_entity,
)
from sift.kernel.access.catalog import (
    record_enrichment_on as write_enrichment_run_on,
)
from sift.kernel.access.viewer import Viewer
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import point_read
from sift.kernel.ledger import Actor, Object, record_event
from sift.kernel.log import get_logger
from sift.kernel.records import FoundRecord, Subject
from sift.kernel.vocabulary import Subject as DecisionSubject
from sift.slices.stash_boxes.adapter import (
    EXACT as EXACT,
)
from sift.slices.stash_boxes.adapter import (
    StashBoxUnreachable,
    as_json,
    from_json,
)
from sift.slices.stash_boxes.asking import AskingBoxes
from sift.slices.stash_boxes.configured import _ONE, _TABLES, Linked

log = get_logger(__name__)


#: What each kind the `enriched` event is written for is called, read inside the run's write.
#: A FILE is not here: its run writes no event. See `StashBoxService.record_enrichment`.
_NAMED_SUBJECT: Mapping[Subject, str] = {
    Subject.PERSON: "SELECT name FROM people WHERE id = ?",
    Subject.SITE: "SELECT name FROM sites WHERE id = ?",
    Subject.TAG: "SELECT name FROM tags WHERE id = ?",
}

#: The box's name at the time, for the event's object. See `Object.name`.
_BOX_NAME = "SELECT name FROM stash_boxes WHERE id = ?"


def _as_applied(applied: Mapping[str, int] | Sequence[str]) -> dict[str, int] | list[str]:
    """What the event's payload carries: the run column's own shape. See `catalog._applied`."""
    return dict(applied) if isinstance(applied, Mapping) else list(applied)


#: WHICH BOX GAVE EACH FIELD, read off the enrichment runs (the write-time record) rather than a
#: second table that could disagree with it: newest first, one row per field a run filled. The
#: array branch reads the older shape of `applied`, a list of keys.
_FIELDS_GIVEN = point_read(
    "stash_boxes.fields_given",
    "SELECT CASE json_type(r.applied) WHEN 'array' THEN j.value ELSE j.key END AS key,"
    " r.box_id AS box_id FROM enrichment_runs r, json_each(r.applied) j"
    " WHERE r.subject = ? AND r.local_id = ? AND r.applied IS NOT NULL AND json_valid(r.applied)"
    " ORDER BY r.at DESC, r.id DESC",
)

_CLEAR_UNDECIDED = "DELETE FROM stash_box_undecided WHERE subject = ? AND local_id = ?"
#: Every subject a box has been agreed to know, across the three link tables: three statements,
#: one per table, because each holds a different foreign key. The reconcile survey asks one
#: question ("where does anything disagree"), and the answer spans all three.
_LINKED_PEOPLE = (
    "SELECT person_id AS local_id, box_id, payload, fetched_at FROM person_stash_box_links"
)

_LINKED_SITES = "SELECT site_id AS local_id, box_id, payload, fetched_at FROM site_stash_box_links"

_LINKED_TAGS = "SELECT tag_id AS local_id, box_id, payload, fetched_at FROM tag_stash_box_links"

#: Which statement reads which kind, so the two questions asked of these tables (everything a box
#: was agreed to know, and merely WHICH subjects have a link) are asked of one list rather than of
#: two hand-written copies that can come to disagree about a table.
_LINKED_OF: Mapping[Subject, str] = {
    Subject.PERSON: _LINKED_PEOPLE,
    Subject.SITE: _LINKED_SITES,
    Subject.TAG: _LINKED_TAGS,
}

# Whether any subject has ever been linked: what decides if the ledger is a tab at all.
_ANY_LINK = (
    "SELECT 1 FROM person_stash_box_links"
    " UNION ALL SELECT 1 FROM site_stash_box_links"
    " UNION ALL SELECT 1 FROM tag_stash_box_links LIMIT 1"
)


class LinkingBoxes(AskingBoxes):
    """Make, read, refresh and forget the links, and record what an enrichment run filled in."""

    # --- Linking ---------------------------------------------------------------------------

    async def link(
        self,
        subject: Subject,
        local_id: str,
        box_id: str,
        remote_id: str,
        master_key: bytes | None,
    ) -> Linked | None:
        """Remember that this box files this subject under that id, and keep what it said."""
        table = _TABLES.get(subject)
        if table is None:
            raise ValueError(f"a stash-box cannot be linked to a {subject.value}")
        box = await self._unsealed(box_id, master_key)
        if box is None:
            log.info("stashbox.link.failed", box_id=box_id, subject=subject.value, why="sealed")
            row = await self._db.fetch_one(_ONE, (box_id,))
            named = str(row["name"]) if row is not None else "That stash-box"
            raise StashBoxUnreachable(
                f"{named} could not be asked. {await self._sealed_sentence(box_id)}"
            )
        try:
            found = await self._fetched(box, subject, remote_id, about=(subject, local_id))
        except StashBoxUnreachable as failure:
            log.info("stashbox.link.failed", box=box.name, subject=subject.value, why=str(failure))
            raise
        if found is None:
            log.info("stashbox.link.failed", box=box.name, subject=subject.value, why="not known")
            return None
        now = self._now()
        await self._say(
            table.write,
            (local_id, box_id, found.remote_id, as_json([found]), now),
            About.LIBRARY,
        )
        # A box that keeps its creators where another keeps its studios has just said it knows this
        # person: the evidence the PMV-creator mark stands on, as `AssetWriter._mark_creator` reads
        # it from a scene. Set and never cleared; it rides the announcement below.
        if subject is Subject.PERSON and box.studios_are_people:
            await mark_pmv_creator(self._db, local_id)
        # Linked is decided, whoever decided it.
        await self._say(_CLEAR_UNDECIDED, (subject.value, local_id), About.LIBRARY)
        log.info("stashbox.linked", box=box.name, subject=subject.value)
        return Linked(box_id, box.name, found.remote_id, found, now)

    async def made_by(self, subject: Subject, local_id: str, viewer: Viewer) -> EntityMaker | None:
        """Who created this row: the word, the pass, and the box where one made it."""
        return await maker_of_entity(self._db, viewer, subject.value, local_id)

    async def enrichment_of(self, subject: Subject, local_id: str) -> list[EnrichmentRun]:
        """Every box that has enriched this thing, newest first. The "Last:" line reads this.

        The catalog's read rather than one of this slice's own, because the fact is about a catalog
        row and four kinds carry it, a file as well as the three a box can be looked up by name.
        This service is where it is asked from, so a screen reaching for the boxes reaches one
        place, the same arrangement as `made_by`.
        """
        return await enrichment_of_subject(self._db, subject.value, local_id)

    async def record_enrichment(
        self,
        subject: Subject,
        local_id: str,
        box_id: str,
        *,
        automatic: bool,
        applied: Mapping[str, int] | Sequence[str] | None = None,
        pressed_by: str | None = None,
    ) -> None:
        """Write down that this box has just enriched this thing, and tell the screens."""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            if not await write_enrichment_run_on(
                connection,
                subject.value,
                local_id,
                box_id,
                automatic=automatic,
                at=self._now(),
                applied=applied,
            ):
                return
            named = _NAMED_SUBJECT.get(subject)
            if named is None:
                return
            rows = list(await connection.execute_fetchall(named, (local_id,)))
            boxes = list(await connection.execute_fetchall(_BOX_NAME, (box_id,)))
            await record_event(
                connection,
                # THE USER where somebody pressed it, and the box where nobody did: the same
                # split `Enricher.apply` and `write_one` make for the values themselves.
                actor=(
                    Actor.user(pressed_by)
                    if pressed_by is not None and not automatic
                    else Actor.box(box_id)
                ),
                verb="enriched",
                subject=DecisionSubject(
                    kind=subject.value,
                    id=local_id,
                    name=str(rows[0]["name"]) if rows else None,
                ),
                object=Object(kind="box", id=box_id, name=str(boxes[0]["name"]) if boxes else None),
                # The column's own shape, so the event and the run cannot say different things:
                # nothing for a bare link, `{}` for a plan that filled nothing, else key -> count.
                payload=None if applied is None else json.dumps(_as_applied(applied)),
            )

    async def fields_given(self, subject: Subject, local_id: str) -> dict[str, str]:
        """Which box filled in each field of one subject, newest first: field key to box id.

        What was written, not what holds now: a value somebody typed over it afterwards is still
        in here, and the caller compares the value with the box's own before it says "from" (see
        the links route). Read from the runs, which are the write-time record (`_FIELDS_GIVEN`).
        """
        given: dict[str, str] = {}
        for row in await self._db.fetch_all(_FIELDS_GIVEN, (subject.value, local_id)):
            given.setdefault(str(row["key"]), str(row["box_id"]))
        return given

    async def links_of(self, subject: Subject, local_id: str) -> list[Linked]:
        """Every stash-box record kept for one subject, newest first.

        Read from Sift's own table and never from the network. This is what the record draws from,
        so opening somebody's page must not depend on three public services being up.
        """
        table = _TABLES.get(subject)
        if table is None:
            return []
        names = {box.id: box.name for box in await self.boxes()}
        out: list[Linked] = []
        for row in await self._db.fetch_all(table.read, (local_id,)):
            records = from_json(str(row["payload"]))
            if not records:
                continue
            box_id = str(row["box_id"])
            out.append(
                Linked(
                    source_id=box_id,
                    source_name=names.get(box_id, box_id),
                    remote_id=str(row["remote_id"]),
                    record=records[0],
                    fetched_at=int(row["fetched_at"]),
                )
            )
        return out

    async def forget_link(self, subject: Subject, local_id: str, box_id: str) -> bool:
        """Forget that a box knows this subject. Nothing it filled in is taken back.

        Deliberately: a field somebody agreed to is Sift's own from that moment, and unpicking an
        agreement made a month ago would mean remembering which of today's values came from where
        and quietly reverting them. What goes is the link and the copy of what that box said.
        """
        table = _TABLES.get(subject)
        if table is None:
            return False
        if await self._db.fetch_one(table.read_one, (local_id, box_id)) is None:
            return False
        await self._say(table.forget, (local_id, box_id), About.LIBRARY)
        return True

    async def refresh(
        self, subject: Subject, local_id: str, box_id: str, master_key: bytes | None
    ) -> Linked | None:
        """Ask a box again about a subject it is already linked to.

        By hand, never on a timer. The cache beside this expires after thirty days and this does
        not: a link is a statement somebody made, and re-asking on a schedule would be background
        chatter at somebody else's service for a page nobody has open.
        """
        table = _TABLES.get(subject)
        if table is None:
            return None
        row = await self._db.fetch_one(table.read_one, (local_id, box_id))
        if row is None:
            return None
        return await self.link(subject, local_id, box_id, str(row["remote_id"]), master_key)

    async def linked_people(self, *, with_picture_lists: bool = False) -> list[str]:
        """Everybody linked to at least one box, by id, oldest link first. Read from Sift's own table."""
        rows = await self._db.fetch_all(
            "SELECT person_id FROM person_stash_box_links "
            "WHERE ? = 0 OR json_extract(payload, '$[0].pictures') IS NOT NULL "
            "GROUP BY person_id ORDER BY MIN(fetched_at), person_id",
            (int(with_picture_lists),),
        )
        return [str(row["person_id"]) for row in rows]

    async def any_link(self) -> bool:
        """Whether any subject has ever been linked to any box."""
        return await self._db.fetch_one(_ANY_LINK) is not None

    async def linked_subjects(self) -> list[tuple[Subject, str, str, FoundRecord]]:
        """Every subject a box has been agreed to know, with what that box said about it."""
        out: list[tuple[Subject, str, str, FoundRecord]] = []
        for subject, statement in _LINKED_OF.items():
            for row in await self._db.fetch_all(statement):
                records = from_json(str(row["payload"]))
                if records:
                    out.append((subject, str(row["local_id"]), str(row["box_id"]), records[0]))
        return out
