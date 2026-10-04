# SPDX-License-Identifier: AGPL-3.0-or-later
"""Enriching the PEOPLE, SITES and TAGS in a library from the stash-boxes.

## What this is, beside the two queues that already exist

The Tagger is a pass over FILES: three services are asked what each file is, and what comes back
waits to be agreed with. Reconcile is what happens AFTER a subject is linked: one row per field
where a box and this library disagree.

Between them is the first step: somebody looking up a person, a site or a tag and saying "yes,
that is them". On a subject's own page that is one subject at a time, behind a button, and a
library with four hundred people would have no way to work through them except to open four
hundred pages.

So: a queue of what has never been asked about, and a way to ask about a batch of it.

## What "enrich" means here, precisely

Ask the switched-on boxes about the subject's name, and act only where there is nothing to decide:

  - Exactly ONE entry, across every box asked, matches every word of the name. Two entries is a
    judgement (which of the two people called Jane Doe is this one), and a judgement is what the
    chooser on their own page is for. Nothing is written and the subject stays in the queue.
  - The link is kept, so from then on the subject has a record from that box and any disagreement
    becomes a Reconcile row rather than a silent overwrite.
  - The fields are applied through the SAME planner the reconcile screen uses, with the same
    per-field rules from Settings. The default for those rules is fill-what-is-blank, so a value
    somebody typed is not replaced by this unless they have said it may be.

That last point is what makes an unattended pass acceptable at all. This does not have its own idea
of what to write; it has no idea of what to write. It asks the planner, which reads the rules a
person set, and writes what the planner returns.
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

        ## Why this answers with a reason and not a yes or no

        The two commonest outcomes (nothing found, and too many candidates to choose from) would
        both be `False`, and a job that finishes green with the record unchanged looks like a button
        that does not work. Declining is by design: choosing between many possible matches is a
        judgement for the person who knows which one their files are of. But a decision not to act
        is still something that happened, so the outcome comes back named, the job counts them, and
        the note on the job says which. `Enrich`, which opens the chooser with the name already in
        it, is where an `AMBIGUOUS` goes next.
        """
        # `about` is what makes this refuse for a record kept local. It is the SUBJECT'S id and not
        # merely a hint: this is the one caller that searches a name it read off a row in this
        # library, so the name going out IS that row's data leaving, which is exactly what the
        # flag refuses. A term somebody typed into the chooser is a different case; see `search`.
        try:
            answers = await self._service.search(
                name, master_key, subject=subject, about=local_id, only=only
            )
        except KeptLocal:
            log.info("stashbox.enrich.kept_local", subject=subject.value, subject_id=local_id)
            return Outcome.KEPT_LOCAL
        # Certain matches, by box. The judgement is whether one box has exactly one entry for the
        # name; a second box that also knows them is not a second candidate, it is a second link,
        # and a person is linked to every box that knows them (the reconcile screen is built on
        # that). Counted across boxes, a performer three boxes agree on would read as three
        # candidates and be declined.
        by_box: dict[str, list[FoundRecord]] = {}
        for answer in answers:
            if answer.problem:
                continue
            for record in answer.records:
                if record.every_word:
                    by_box.setdefault(record.source_id, []).append(record)
        single = [
            only_one
            for records in by_box.values()
            if (only_one := _the_one(subject, name, records)) is not None
        ]
        if not single:
            # Two on one box is a judgement about which of two people this is, and nought is a
            # name nobody has. Both belong to the person looking at the chooser, so both stay in
            # the queue, but they are DIFFERENT answers and the person is told which one they
            # got. "Nobody has heard of this" and "there are twenty of these" lead to opposite
            # next moves.
            candidates = sum(len(records) for records in by_box.values())
            log.info("stashbox.enrich.undecided", subject=subject.value, candidates=candidates)
            if by_box:
                # Written down, so the ones a person has to choose for are a list on the stash-box
                # page rather than a number in a job's note. Cleared the moment the subject is linked.
                await self._service.note_undecided(subject, local_id, candidates)
                return Outcome.AMBIGUOUS
            # A box that refused the question has not said it never heard of this.
            if any(answer.problem for answer in answers):
                return Outcome.FAILED
            return Outcome.UNKNOWN

        first: tuple[FoundRecord, Linked] | None = None
        for found in single:
            # A box that could not be asked is the same outcome here as one that did not know the
            # entry: nothing is linked to it on this run. The two are told apart for the person
            # pressing a button (see `StashBoxService.link`); an unattended run has nobody to tell,
            # and the service has already logged which it was.
            try:
                linked = await self._service.link(
                    subject, local_id, found.source_id, found.remote_id, master_key
                )
            except StashBoxUnreachable:
                continue
            if linked is None:
                continue
            # The picture, into the blank, from the record just kept. It is the one thing a box
            # says about somebody that is not a field, so the planner below never sees it: a
            # linked person would keep their monogram until an admin opened their page and pressed
            # the button that fetches exactly this. `fill` stops at the first one that lands.
            await self._kept_picture(subject, local_id, linked, master_key, called=name)
            if first is None:
                first = (found, linked)
                continue
            # AUTOMATIC, written once per box that linked. This is the only path that links without
            # somebody pressing anything, and the row it leaves is identical to a hand-made link
            # everywhere else. See `record_enrichment` for why that one fact cannot be recovered
            # later.
            #
            # NO APPLIED LIST for these, and that is the honest shape rather than an omission: the
            # fields are planned from the FIRST box that linked and the others are links, so a
            # second box filled nothing in and its line must not say it did. The first box's run is
            # recorded BELOW, after the write, where what it wrote is known.
            await self._service.record_enrichment(
                subject, local_id, linked.source_id, automatic=True
            )
        if first is None:
            return Outcome.UNKNOWN
        # After every box has linked, so the one piece of work it asks for reads all of them.
        await self.person_linked(subject, local_id)
        # The fields are planned from the FIRST box that linked; the others are links, and what
        # they say that differs is a Reconcile row from here on.
        _found, linked = first
        return await self._fill(subject, local_id, linked, master_key)

    async def link_known(
        self,
        subject: Subject,
        local_id: str,
        box_id: str,
        remote_id: str,
        master_key: bytes | None,
    ) -> Outcome:
        """Link a subject to a box by the box's OWN id for it, then do what every link does.

        ## Why there is a second way in

        `enrich` starts from a name and has to judge whether exactly one entry answers it, which is
        the wrong shape for a row an answer has just created. That answer named the row by the
        box's own id (a credit on a scene the box recognized by its fingerprint), so there is
        nothing to judge, and asking about the name again would throw the id away.

        Everything after the link is `enrich`'s own: the picture into the blank (`_kept_picture`),
        then the fields planned from the record just kept (`_fill`). One path for what a link
        brings, whichever way the link was found.

        Kept local is refused by the service's door and answered as that. A box that cannot be asked
        raises `StashBoxUnreachable` rather than reading as "nobody has heard of this": this is an id
        the box gave, so its absence is a fault worth a line in the log and not a fact about them.
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
        """Say a PERSON has just been linked, for the face feature to take starter pictures from.

        Through `RecognitionSeam.linked`, so this slice never learns what is done with it: the face
        feature decides whether she needs starters (she has no reference yet), queues the work, and
        fetches the pictures back through `StarterPictures`, this slice's door, with its pacing.
        Once per subject per run, not per box: one piece of work reads every box she is linked to.
        Quiet for a site or a tag, and where nothing listens.
        """
        if subject is Subject.PERSON and self._recognition is not None:
            await self._recognition.linked(local_id)

    async def _fill(
        self, subject: Subject, local_id: str, linked: Linked, master_key: bytes | None
    ) -> Outcome:
        """Plan and write the fields from one box's record just kept, and record the run.

        A row the record invents (a studio's parent network) is recorded as that box's and linked
        to it by the box's own id, the way a confirmed file match does it, so it wears the box's
        picture rather than a letter.
        """
        plan = await self._enricher.plan_for(
            subject=subject,
            local_id=local_id,
            source_id=linked.source_id,
            offered=linked.record.fields,
            strategies=await strategies_for(self._settings, subject),
        )
        if plan is None or not plan.writes:
            # Linked and nothing to fill in: the box agrees with everything already here, or every
            # field's rule says leave it alone. The link is still the point: it is what turns a
            # future disagreement into a Reconcile row instead of a silent difference.
            #
            # The empty list, and only where a plan actually ran: a plan that decided nothing is a
            # real outcome and the History line says "nothing new to fill in". `plan is None` is a
            # subject nothing can write, which was never asked, so it records no list at all.
            await self._service.record_enrichment(
                subject,
                local_id,
                linked.source_id,
                automatic=True,
                applied=None if plan is None else (),
            )
            return Outcome.LINKED
        # Nothing is created by an unattended run unless that is switched on, per kind, and off is
        # the default for all three, the rule `jobs._apply` follows for a certain file match, read
        # from the same place.
        #
        # Asked before the write: once a row has been created it is no longer missing, so asking
        # afterwards would report nothing was needed on exactly the runs that created something.
        wanted = await self._enricher.missing_for(plan)
        inventable = await may_invent(self._settings)
        allowed = frozenset((one.kind, one.name) for one in wanted if one.kind in inventable)
        written = await self._enricher.apply(plan, creating=allowed)
        made = await record_who_invented(self._naming, invented=allowed, box_id=linked.source_id)
        # AFTER the write, which is the only place the answer exists. What it records is what the
        # WRITER said it wrote, never what the plan asked for. See `Writer.write` for why the two
        # are different numbers in both directions, and `sentences.box_line` for the line it makes.
        await self._service.record_enrichment(
            subject, local_id, linked.source_id, automatic=True, applied=written
        )
        log.info(
            "stashbox.enrich.applied",
            subject=subject.value,
            # What landed, not what was planned: `apply` answers with the fields it changed.
            fields=len(written),
            planned=len(plan.writes),
            created=sorted(f"{kind}:{name}" for kind, name in allowed),
            # And what was left out for want of a row to point at, by kind and by name, because
            # "it did nothing" and "it could not invent four tags" look identical from outside.
            not_created=sorted(
                f"{one.kind}:{one.name}" for one in wanted if one.kind not in inventable
            ),
        )
        # Last, as `jobs._apply` does: a link is a request to somebody else's service, and one that
        # fails must not undo the write or the run already recorded.
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
        """Put this box's picture of the subject where the subject has none, as their COVER.

        **Never asked for a Site the icon pack already draws well** (`_shipped_logo`): a Site with
        no cover falls through to the pack's logo, so a box's picture of a studio the pack holds at
        full quality is a request to somebody else's server for a picture nobody would see. The
        log says so, so a Site that did not get the box's picture reads as a decision.

        Never over one that is there: `SubjectCovers.fill` is the unattended verb and stops at a
        cover of either kind, so a still somebody chose from a clip is never replaced by whatever
        a public service holds. Asked BEFORE the fetch as well, because a picture that would be
        declined is a request to somebody else's server for nothing.

        Silent about everything except a picture that actually landed. A box with no picture for
        this entry, a fetch that will not come, and a subject that already has one are all
        ordinary: this runs unattended off the end of a link, and the link is the work. The bytes
        go through `SubjectCovers`, which is what draws a person.

        Sift is the actor, and the box is named as where the picture came from. Not `Actor.box`:
        that says a box wrote a value, and the value here is a column on Sift's own row pointing at
        bytes Sift re-encoded. `VIA_STASH` is the word every other row from a box's answer wears.
        The box goes onto the event beside it, so the line reads "Cover set to FansDB's picture".
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
    """Whether the icon pack draws this Site at full quality, so the box need not be asked.

    Asked by every key the pack could match the Site by: what the row is called here (where the
    caller knows it), what the box calls the studio, and each of the studio's own front doors
    among its links. A link to a database or an index names where the studio is written about,
    never the studio (`is_index_host`), so it is stepped over exactly as the chooser steps over it.
    Only for a SITE: the pack is a pack of site logos.
    """
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


def _the_one(subject: Subject, name: str, records: list[FoundRecord]) -> FoundRecord | None:
    """The one entry of a box's answer this name means, or None where that is a judgement.

    One entry is the answer. Several are a judgement, except for a Site: a studio search completes
    words, so a network's name also brings back the sub-studios carrying it, and a single entry
    called exactly that name, in any case, is the studio the name means. A person is not narrowed
    this way, because two people can share a name and their search is where that matters.
    """
    if len(records) == 1:
        return records[0]
    if subject is not Subject.SITE:
        return None
    # The box's spelling or Sift's for a network (`network_name`), so a network called by its own
    # name here and the flagship studio of that name are two answers, and neither is chosen.
    wanted = name.strip().casefold()
    exact = [
        one
        for one in records
        if wanted in {one.name.strip().casefold(), network_name(one.name).casefold()}
    ]
    return exact[0] if len(exact) == 1 else None


class StarterPictures:
    """A person's pictures from the boxes she is linked to, for the face feature's starters.

    `BoxPicturesSeam`, implemented where the door is: every request goes through the service
    (its pacing, its sealed keys, its refusal for somebody kept local) and the face feature is
    handed bytes and a box's name, never an address.
    """

    def __init__(self, service: StashBoxService) -> None:
        self._service = service

    async def linked_people(self, *, with_picture_lists: bool = False) -> list[str]:
        return await self._service.linked_people(with_picture_lists=with_picture_lists)

    async def pictures_of(
        self, person_id: str, master_key: bytes | None, *, most: int
    ) -> list[tuple[str, bytes]] | None:
        """Up to `most` of her pictures, largest first, the first box's before the next one's.

        From the record each link KEPT. A link kept before records carried their whole picture list
        is asked again first, the way Refresh asks (`StashBoxService.refresh`), so the list is kept
        from then on and never asked for twice; where that cannot be done now, the one picture it
        did keep is used. Kept local answers nothing and asks nothing: the door refuses her.

        None where she could not be asked about (kept local, a box that could not be reached, a
        picture that would not come) and nothing got; an empty list only where every box ANSWERED
        and none holds a picture of her. See `BoxPicturesSeam` for why the two must differ.
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
