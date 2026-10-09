# SPDX-License-Identifier: AGPL-3.0-or-later
"""Enriching the people, sites and tags in a library from the stash-boxes.

A subject is linked only where exactly one entry, across every box asked, matches every word of its
name; the fields then go through the same planner and per-field rules as Reconcile, so this writes
nothing a person has not allowed.
"""

from __future__ import annotations

from sift.kernel.access import Repository
from sift.kernel.covers import SubjectCovers
from sift.kernel.enrichment import Enricher, Naming
from sift.kernel.ledger import Actor, Object
from sift.kernel.log import get_logger
from sift.kernel.records import FoundRecord, Subject
from sift.kernel.seams import RecognitionSeam, SettingsSeam
from sift.kernel.site_icons import is_a_sites_own, is_index_host, ships_a_good_one
from sift.kernel.vocabulary import VIA_STASH
from sift.kernel.wiring import Part
from sift.slices.stash_boxes.adapter import StashBoxUnreachable, network_name
from sift.slices.stash_boxes.configured import Answer
from sift.slices.stash_boxes.enrich import linkable, record_who_invented
from sift.slices.stash_boxes.jobs import Outcome, strategies_for
from sift.slices.stash_boxes.service import KeptLocal, Linked, StashBoxService
from sift.slices.stash_boxes.settings import may_invent

log = get_logger(__name__)


class EntityEnricher:
    """Asking the stash-boxes about a subject, and what is still unasked."""

    def __init__(
        self,
        service: StashBoxService,
        access: Repository,
        enricher: Enricher,
        settings: SettingsSeam,
        covers: SubjectCovers,
        *,
        naming: Naming,
        recognition: RecognitionSeam | None = None,
    ) -> None:
        self._service = service
        self._naming = naming
        self._access = access
        self._enricher = enricher
        self._settings = settings
        self._covers = covers
        self._recognition = recognition

    async def enrich(
        self,
        subject: Subject,
        local_id: str,
        name: str,
        master_key: bytes | None,
        *,
        only: str | None = None,
    ) -> Outcome:
        """Ask the boxes about one subject and act only where there is nothing to decide.

        The outcome is named rather than a yes or no, so a job can say why nothing changed.
        """
        # `about` refuses for a record kept local: the name going out is that row's data.
        try:
            answers = await self._service.search(
                name, master_key, subject=subject, about=local_id, only=only
            )
        except KeptLocal:
            log.info("stashbox.enrich.kept_local", subject=subject.value, subject_id=local_id)
            return Outcome.KEPT_LOCAL
        by_box = _every_word_by_box(answers)
        single = [
            only_one
            for records in by_box.values()
            if (only_one := _the_one(subject, name, records)) is not None
        ]
        if not single:
            return await self._undecided(subject, local_id, answers, by_box)

        first: tuple[FoundRecord, Linked] | None = None
        for found in single:
            # A box that could not be asked links nothing on this run; the service has logged why.
            try:
                linked = await self._service.link(
                    subject, local_id, found.source_id, found.remote_id, master_key
                )
            except StashBoxUnreachable:
                continue
            if linked is None:
                continue
            # The picture into the blank: it is not a field, so the planner below never sees it.
            await self._kept_picture(subject, local_id, linked, master_key, called=name)
            if first is None:
                first = (found, linked)
                continue
            # Automatic, once per box that linked; no applied list, since only the first box's plan
            # writes and that run is recorded below.
            await self._service.record_enrichment(
                subject, local_id, linked.source_id, automatic=True
            )
        if first is None:
            return Outcome.UNKNOWN
        # After every box has linked, so the one piece of work it asks for reads all of them.
        await self.person_linked(subject, local_id)
        # Planned from the first box that linked; the others' differences become Reconcile rows.
        _found, linked = first
        return await self._fill(subject, local_id, linked, master_key)

    async def _undecided(
        self,
        subject: Subject,
        local_id: str,
        answers: list[Answer],
        by_box: dict[str, list[FoundRecord]],
    ) -> Outcome:
        """Why no single entry answered: many to choose from, a box that failed, or nobody knows."""
        # None and many are different answers, told apart for the person at the chooser.
        candidates = sum(len(records) for records in by_box.values())
        log.info("stashbox.enrich.undecided", subject=subject.value, candidates=candidates)
        if by_box:
            # Written down, so the subjects to choose for are a list; cleared once linked.
            await self._service.note_undecided(subject, local_id, candidates)
            return Outcome.AMBIGUOUS
        # A box that refused the question has not said it never heard of this.
        if any(answer.problem for answer in answers):
            return Outcome.FAILED
        return Outcome.UNKNOWN

    async def link_known(
        self,
        subject: Subject,
        local_id: str,
        box_id: str,
        remote_id: str,
        master_key: bytes | None,
    ) -> Outcome:
        """Link a subject to a box by the box's own id for it, then do what every link does.

        For a row an answer just created, where there is no name to judge; an unreachable box raises
        `StashBoxUnreachable`, since the id came from the box.
        """
        try:
            linked = await self._service.link(subject, local_id, box_id, remote_id, master_key)
        except KeptLocal:
            log.info("stashbox.link_known.kept_local", subject=subject.value, subject_id=local_id)
            return Outcome.KEPT_LOCAL
        if linked is None:
            return Outcome.UNKNOWN
        await self._kept_picture(subject, local_id, linked, master_key)
        await self.person_linked(subject, local_id)
        return await self._fill(subject, local_id, linked, master_key)

    async def person_linked(self, subject: Subject, local_id: str) -> None:
        """Say a person was just linked, through `RecognitionSeam.linked`, so the face feature can take
        starters.
        """
        if subject is Subject.PERSON and self._recognition is not None:
            await self._recognition.linked(local_id)

    async def _fill(
        self, subject: Subject, local_id: str, linked: Linked, master_key: bytes | None
    ) -> Outcome:
        """Plan and write the fields from one box's record just kept, and record the run."""
        plan = await self._enricher.plan_for(
            subject=subject,
            local_id=local_id,
            source_id=linked.source_id,
            offered=linked.record.fields,
            strategies=await strategies_for(self._settings, subject),
        )
        if plan is None or not plan.writes:
            # Linked, nothing to fill in. An empty list only where a plan ran; no plan records none.
            await self._service.record_enrichment(
                subject,
                local_id,
                linked.source_id,
                automatic=True,
                applied=None if plan is None else (),
            )
            return Outcome.LINKED
        # Nothing is created unattended unless switched on per kind; asked before the write.
        wanted = await self._enricher.missing_for(plan)
        inventable = await may_invent(self._settings)
        allowed = frozenset((one.kind, one.name) for one in wanted if one.kind in inventable)
        written = await self._enricher.apply(plan, creating=allowed)
        made = await record_who_invented(self._naming, invented=allowed, box_id=linked.source_id)
        # After the write: it records what the writer said it wrote, not what the plan asked for.
        await self._service.record_enrichment(
            subject, local_id, linked.source_id, automatic=True, applied=written
        )
        log.info(
            "stashbox.enrich.applied",
            subject=subject.value,
            fields=len(written),
            planned=len(plan.writes),
            created=sorted(f"{kind}:{name}" for kind, name in allowed),
            # And what was left out for want of a row to point at, by kind and name.
            not_created=sorted(
                f"{one.kind}:{one.name}" for one in wanted if one.kind not in inventable
            ),
        )
        # Last, as `jobs._apply` does: a failed link must not undo the write or the recorded run.
        for one in linkable(made, linked.record, linked.source_id):
            try:
                await self.link_known(
                    one.subject, one.local_id, one.box_id, one.remote_id, master_key
                )
            except StashBoxUnreachable:
                log.info(
                    "stashbox.link_invented.unreachable",
                    subject=one.subject.value,
                    subject_id=one.local_id,
                )
        return Outcome.WROTE

    async def _kept_picture(
        self,
        subject: Subject,
        local_id: str,
        linked: Linked,
        master_key: bytes | None,
        *,
        called: str | None = None,
    ) -> bool:
        """Put this box's picture of the subject where it has none, as its cover.

        Skipped for a Site the icon pack already draws, and never over a cover already there, asked
        before the fetch. Silent unless a picture landed. Sift is the actor, the box named as the
        source.
        """
        where = linked.record.image_url
        if not where or await self._covers.has_one(subject, local_id):
            return False
        if _shipped_logo(subject, linked.record, called):
            log.info(
                "stashbox.picture.not_asked",
                subject=subject.value,
                subject_id=local_id,
                why="the icon pack already holds a high-quality logo for this Site",
            )
            return False
        # A COVER fetch, so a studio's vector logo may come: the cover door draws it safely.
        got = await self._service.picture(linked.source_id, where, master_key, vector=True)
        if got is None:
            log.info("stashbox.picture.not_fetched", subject=subject.value, subject_id=local_id)
            return False
        blob, _content_type = got
        if await self._covers.fill(
            subject,
            local_id,
            blob,
            actor=Actor.sift(VIA_STASH),
            box=Object(kind="box", id=linked.source_id, name=linked.source_name),
        ):
            log.info("stashbox.picture.kept", subject=subject.value, subject_id=local_id)
            return True
        return False


def _shipped_logo(subject: Subject, record: FoundRecord, called: str | None) -> bool:
    """Whether the icon pack draws this Site at full quality, by any key it could match on."""
    if subject is not Subject.SITE:
        return False
    names = [one for one in (called, record.name) if one]
    if any(ships_a_good_one(None, one) for one in names):
        return True
    links = record.fields.get("links")
    return any(
        isinstance(link, str)
        and not is_index_host(link)
        and is_a_sites_own(link)
        and ships_a_good_one(link)
        for link in (links if isinstance(links, list) else [])
    )


def _every_word_by_box(answers: list[Answer]) -> dict[str, list[FoundRecord]]:
    """The entries matching every word of the name, by box: one on a box is certain."""
    by_box: dict[str, list[FoundRecord]] = {}
    for answer in answers:
        if answer.problem:
            continue
        for record in answer.records:
            if record.every_word:
                by_box.setdefault(record.source_id, []).append(record)
    return by_box


def _the_one(subject: Subject, name: str, records: list[FoundRecord]) -> FoundRecord | None:
    """The one entry of a box's answer this name means, or None where that is a judgement.

    For a Site only, a single entry with exactly the name wins among the sub-studios a search
    brings.
    """
    if len(records) == 1:
        return records[0]
    if subject is not Subject.SITE:
        return None
    # The box's or Sift's spelling of a network, so a network and its flagship both stay unchosen.
    wanted = name.strip().casefold()
    exact = [
        one
        for one in records
        if wanted in {one.name.strip().casefold(), network_name(one.name).casefold()}
    ]
    return exact[0] if len(exact) == 1 else None


class StarterPictures:
    """A person's pictures from the boxes she is linked to, for the face feature's starters."""

    def __init__(self, service: StashBoxService) -> None:
        self._service = service

    async def linked_people(self, *, with_picture_lists: bool = False) -> list[str]:
        return await self._service.linked_people(with_picture_lists=with_picture_lists)

    async def pictures_of(
        self, person_id: str, master_key: bytes | None, *, most: int
    ) -> list[tuple[str, bytes]] | None:
        """Up to `most` of her pictures, largest first, box by box, from the records each link kept.

        None where she could not be asked and nothing came; an empty list only where every box
        answered.
        """
        if most <= 0 or await self._service.kept_local(Subject.PERSON, person_id):
            return None
        answered = True
        wanted: list[tuple[str, str, str]] = []
        for linked in await self._service.links_of(Subject.PERSON, person_id):
            record = linked.record
            if not record.pictures:
                try:
                    fresh = await self._service.refresh(
                        Subject.PERSON, person_id, linked.source_id, master_key
                    )
                except (StashBoxUnreachable, KeptLocal):
                    fresh = None
                    answered = False
                if fresh is not None:
                    record = fresh.record
            urls = record.pictures or ((record.image_url,) if record.image_url else ())
            wanted.extend((linked.source_id, linked.source_name, url) for url in urls)
        got: list[tuple[str, bytes]] = []
        for box_id, box_name, url in wanted:
            if len(got) >= most:
                break
            try:
                picture = await self._service.picture(box_id, url, master_key)
            except StashBoxUnreachable:
                picture = None
            if picture is None:
                log.info("stashbox.starter.not_fetched", person_id=person_id, box=box_name)
                answered = False
                continue
            got.append((box_name, picture[0]))
        return got if got or answered else None


#: The enricher, for the router and the job to reach.
ENTITIES: Part[EntityEnricher] = Part("stash_box_entities")
