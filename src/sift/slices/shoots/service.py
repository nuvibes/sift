# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proposing shoots, and what happens when somebody answers one.

A shoot is a run of one creator's pictures taken in one sitting (the same room, the same light,
the same outfit) that is in no Photo Set yet. Nothing in a catalog records that a sitting
happened. What does record it, indirectly, is the meaning index: pictures from one sitting describe
almost identically, and the index can say how far apart two descriptions sit. So the pass reads
neighbours and the rule in `clustering.py` turns them into groups.

Three things about the arrangement are decisions rather than details.

**It proposes; it does not file.** Every group the pass finds is written down and waits on the
Shoots card, because grouping somebody's library is a judgement and the evidence is a similarity
score. The switch that turns the asking off (`shoots.auto_file`) is off out of the box and is read
per pass rather than at boot, so it takes effect on the next run and not at the next restart.

**A set is made through the feature that owns Photo Sets, never here.** The seam hands this the
same derivation the downloader's galleries go through, so the cover and the order are decided in
one place. See `kernel.seams.PhotoSetSeam`. The floor (how few pictures are not a set) is that
feature's rule as well, and the composition root hands it in (`least`) rather than this slice
writing the number out a second time: two copies of one number drift apart.

**Every yes leaves a receipt and a link, in one transaction with the making.** The receipt is what
the decisions page offers Undo on; the link says which proposal became which Photo Set, so an undo
deletes the grouping that was made and nothing that was there before it. A receipt written
afterwards could be missing for a set that exists, and a record that is only usually right is worse
than none.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from sift.kernel.access import Repository, Viewer, catalog
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Database
from sift.kernel.ledger import Object as LedgerObject
from sift.kernel.log import get_logger
from sift.kernel.seams import PhotoSetSeam, SemanticSeam, SettingsSeam
from sift.kernel.vocabulary import VIA_SHOOT, Subject
from sift.kernel.wiring import Part
from sift.kernel.workbench import Recorder
from sift.slices.shoots.clustering import DISTANCE, REACH, among, shoots
from sift.slices.shoots.settings import AUTO_FILE_KEY
from sift.slices.shoots.store import Made, Proposal, Shoot, Store

log = get_logger(__name__)

#: The name this feature's queue and its receipts are filed under.
QUEUE = "shoots"

#: How many proposals one page of the panel carries.
PAGE = 20

#: How many of one creator's loose pictures a single pass looks at.
#:
#: A ceiling rather than "all of them", because the pass costs one index lookup per seed and a
#: creator with thousands of loose pictures would hold a worker for the length of it. What is left
#: over is not lost: the next pass reads the same list, and everything grouped or refused by this
#: one is out of it, so the pool moves.
PER_CREATOR = 2000


class ShootError(Exception):
    """Something the caller asked for cannot be done, with a sentence saying why."""


class NotFound(ShootError):
    """No such proposal, or none still waiting on an answer."""


class AlreadyFiled(ShootError):
    """The pictures of this proposal are in a Photo Set already, so making it would be a second one.

    A refusal rather than a making, and the proposal is marked answered as it is refused: the card
    was a question about loose pictures, and they are not loose any more.
    """


@dataclass(frozen=True, slots=True)
class Proposed:
    """What one pass found."""

    creators: int
    found: int
    filed: int


@dataclass(frozen=True, slots=True)
class CreatorShoots:
    """One creator's shoots, as a pass found them."""

    person_id: str
    name: str
    groups: tuple[Shoot, ...]


@dataclass(frozen=True, slots=True)
class ShootPlan:
    """What a pass now would write, worked out and not written."""

    #: False where nothing has been described by the model in use, so nothing can be compared.
    can_compare: bool
    #: Whether each shoot would be made into a Photo Set rather than proposed.
    automatic: bool
    #: How many creators have enough loose pictures to look at.
    creators: int
    found: tuple[CreatorShoots, ...] = ()

    @property
    def shoots(self) -> int:
        return sum(len(one.groups) for one in self.found)


@dataclass(frozen=True, slots=True)
class Named:
    """What one naming put the creator on."""

    files: int
    decision_id: str | None


@dataclass(frozen=True, slots=True)
class MadeSet:
    """What one yes produced."""

    photo_set_id: str
    pictures: int
    decision_id: str | None
    #: What the set was called when it was made.
    name: str


class ShootService:
    """Finding proposed shoots, and answering them."""

    def __init__(
        self,
        *,
        database: Database,
        store: Store,
        access: Repository,
        semantic: SemanticSeam,
        photo_sets: PhotoSetSeam,
        preferences: SettingsSeam,
        recorder: Recorder,
        least: int,
        longest: int | None = None,
    ) -> None:
        self._db = database
        self._store = store
        self._access = access
        self._semantic = semantic
        self._photo_sets = photo_sets
        self._preferences = preferences
        self._recorder = recorder
        # THE PHOTO SETS' FLOOR, handed across the seam rather than declared here. A slice may not
        # import another slice, so the composition root reads `photo_sets.MIN_PICTURES` and hands
        # it in; two numbers written out separately would drift apart.
        self._least = least
        # AND ITS LONGEST NAME, for the same reason and by the same road: "Create with a name..."
        # hands a typed name to the Photo Sets, and how long one may be is their rule
        # (`photo_sets.MAX_PHOTO_SET_NAME`), not this slice's. None is an INTERIM, and it means
        # the composition root has not handed the number in yet: the name is still tidied and
        # refused blank by `MakeWrite`, only its length goes unchecked here.
        self._longest = longest

    # --- finding them ---------------------------------------------------------------------------

    async def auto_file(self) -> bool:
        """Whether a shoot is made without anybody being asked. Read per pass, never cached."""
        return bool(await self._preferences.get_app(AUTO_FILE_KEY))

    async def find(self) -> Proposed:
        """Look for shoots among every creator with enough loose pictures, and write what is found.

        Per creator rather than over the library, and that is the rule rather than an optimisation:
        a shoot belongs to one person, and two people photographed in the same room on the same day
        are two shoots however alike their pictures describe.

        **It asks ONCE whether there is anything to compare against, and stops there if not.** The
        evidence a shoot is made of is the meaning index, and a library where nothing has been
        described by the model in use can produce no shoots at all, and asked one picture at a
        time that is found out only at the end, because a file the index has never described and
        an install that cannot use the index give the same answer: on a large library, minutes of
        index lookups to find nothing and write nothing. This is one read (see `SemanticSeam.can_answer`) and it
        costs no measurable time.

        Declining writes nothing: no proposal is replaced, no receipt is recorded, and what was
        proposed by an earlier pass stays exactly as it was. A pass that cannot see the evidence has
        no opinion about a proposal made when it could.
        """
        planned = await self.plan()
        if not planned.can_compare:
            log.info("shoots.declined", reason="nothing has been described by the model in use")
            return Proposed(creators=0, found=0, filed=0)
        # THE FLOOR, TAKEN TO WHAT IS ALREADY STANDING. A pass replaces one creator's proposals and
        # never touches a creator it does not look at, so a card written under a lower floor would
        # otherwise stand on the queue for ever. Here rather than in the job so a pressed Look
        # again clears them too. A proposal is no loose picture, so the plan read above is the same.
        dissolved = await self._store.dissolve_under(self._least)
        settled = await self._settle_standing()
        filed = 0
        for creator in planned.found:
            # WRITTEN AS PROPOSALS EITHER WAY, and the automatic filing then answers each one.
            # Without this write, what an earlier pass proposed about the same pictures stands on
            # the Shoots page after they are in a Photo Set, and pressing it makes a second set.
            # Answering the proposal is the mark a pressed yes writes, so both roads take the card
            # off the page the same way.
            written = await self._store.replace_for(
                creator.person_id, creator.name, list(creator.groups)
            )
            if planned.automatic:
                filed += await self._file_all(
                    creator.name, zip(written, creator.groups, strict=True)
                )
        log.info(
            "shoots.found",
            creators=planned.creators,
            shoots=planned.shoots,
            filed=filed,
            dissolved=dissolved,
            settled=settled,
            floor=self._least,
        )
        return Proposed(creators=planned.creators, found=planned.shoots, filed=filed)

    async def plan(self) -> ShootPlan:
        """The shoots a pass now would find, by creator, and whether it would file them. Writes
        nothing: `find` carries this out, so a dry run and a pass cannot disagree."""
        if not await self._semantic.can_answer():
            return ShootPlan(can_compare=False, automatic=False, creators=0)
        automatic = await self.auto_file()
        creators = await catalog.creators_with_loose_pictures(self._db, least=self._least)
        found: list[CreatorShoots] = []
        for creator in creators:
            groups = await self._for_creator(creator.person_id)
            if groups:
                found.append(CreatorShoots(creator.person_id, creator.name, tuple(groups)))
        return ShootPlan(
            can_compare=True, automatic=automatic, creators=len(creators), found=tuple(found)
        )

    async def _for_creator(self, person_id: str) -> list[Shoot]:
        """One creator's loose pictures, grouped, with what has been refused left out.

        Each group is then widened ONCE with the pictures of the same sitting that carry nobody at
        all. Those are the ordinary state of a file that arrived without a caption, they belong in
        the grouping, and they are the evidence for the second offer the card makes. See
        `_unnamed_near`. One extra lookup per SHOOT rather than per picture, which is what makes
        widening affordable at all.
        """
        pool = await catalog.loose_pictures_of(self._db, person_id, limit=PER_CREATOR)
        refused = await self._store.refused_among(pool)
        waiting = [asset_id for asset_id in pool if asset_id not in refused]
        if len(waiting) < self._least:
            return []

        # ONE read of the pool's own numbers, then every comparison in memory. See `among`. A
        # library-wide nearest search per loose picture would throw most of each answer away.
        described = await self._semantic.describe_many(waiting)
        if not described:
            return []
        found = []
        known = set(waiting)
        neighbours = await among(waiting, described)
        for group in await shoots(waiting, neighbours, least=self._least):
            unnamed = await self._unnamed_near(group[0], known | refused)
            found.append(Shoot(asset_ids=tuple(group) + tuple(unnamed), unnamed=frozenset(unnamed)))
        return found

    async def _unnamed_near(self, seed: str, known: set[str]) -> list[str]:
        """Pictures of the same sitting as this seed that carry nobody at all.

        Asked of the SEED for the reason a group is grown from its seed: the seed is the sitting,
        and asking every member would be a lookup per picture for an answer that is the same one.

        `known` is everything already accounted for (this creator's own pool and what has been
        refused), so the only thing that can come back is a file carrying no person, which the
        kernel then confirms is also a picture and in no Photo Set. A file carrying somebody ELSE
        is deliberately not here: disagreeing with an attribution that exists is a different
        decision from filling in one that does not.
        """
        near = await self._semantic.like_asset(seed, limit=REACH)
        if not near:
            return []
        candidates = []
        for asset_id, apart in near:
            if apart > DISTANCE:
                break
            if asset_id not in known:
                candidates.append(asset_id)
        if not candidates:
            return []
        unnamed = await catalog.unnamed_pictures_among(self._db, candidates)
        return [asset_id for asset_id in candidates if asset_id in unnamed]

    async def _file_all(self, name: str, groups: Iterable[tuple[str, Shoot]]) -> int:
        """Make every one of these proposals into a Photo Set, with nobody asked. See `auto_file`.

        Each one separately, so one that cannot be made (a picture deleted between the read and
        the write) does not take the rest of the creator's shoots with it. Each keeps its own
        receipt, which is what makes them undoable one at a time rather than as an evening's work.

        Each is answered through its proposal, so the link a pressed yes writes is written here as
        well and the card leaves the Shoots page. One the Photo Sets decline stays there as an
        ordinary question, which is the honest state of it: nothing was made.
        """
        filed = 0
        for proposal_id, group in groups:
            made = await self._make(
                name=name,
                called=name,
                asset_ids=list(group.asset_ids),
                proposal_id=proposal_id,
                user_id=None,
            )
            if made is not None:
                filed += 1
        return filed

    async def _settle_standing(self) -> int:
        """Mark answered every waiting proposal whose pictures are in a Photo Set already.

        A pass replaces the proposals of the creators it looks at, and a creator whose pictures
        are all filed has no loose ones and is not looked at, so a card about them stands on
        the page for ever and pressing it makes a second set of the same pictures. Here rather
        than in `make` alone so the page is right without anybody pressing anything.
        """
        standing = await self._store.waiting_pictures()
        if not standing:
            return 0
        held = await catalog.photo_sets_holding(
            self._db, [one for pictures in standing.values() for one in pictures]
        )
        filed = {}
        for proposal_id, pictures in standing.items():
            holder = _holder(pictures, held)
            if holder is not None:
                filed[proposal_id] = holder
        return await self._store.settle_filed(filed)

    # --- answering them -------------------------------------------------------------------------

    async def waiting(self, *, limit: int = PAGE, offset: int = 0) -> tuple[list[Proposal], int]:
        return await self._store.waiting(limit=limit, offset=offset)

    async def position_of(self, proposal_id: str) -> int | None:
        """Where one proposal sits in the queue, counting from zero, or None.

        None for a proposal that has been answered and for one that is not there at all: the
        same answer, and on a queue the first is the ordinary case: answering a question is exactly
        what takes it off the list. The list is not scoped per user (only an admin reads it, and the
        pictures on each card are resolved per viewer by the route), so neither is this.
        """
        return await self._store.position_of(proposal_id)

    async def one(self, proposal_id: str) -> Proposal | None:
        return await self._store.one(proposal_id)

    async def make(self, viewer: Viewer, proposal_id: str, *, name: str | None = None) -> MadeSet:
        """Yes: these pictures are a shoot, so make the Photo Set.

        The pictures are resolved against whoever is asking before anything is written. A proposal
        holding one file this user may not be shown is a proposal this user may not act on:
        acting on it would file a picture they cannot see, and a set they cannot see all of.

        `name` is what "Create with a name..." typed, already tidied by `MakeWrite`; None is the
        ordinary press and the set takes the proposal's own name, which is the creator's. The
        length is refused here, before anything is written, rather than left to the other feature
        to trip over halfway through a making.
        """
        if name is not None and self._longest is not None and len(name) > self._longest:
            raise ShootError(f"a Photo Set's name can be at most {self._longest} characters")
        proposal = await self._store.one(proposal_id)
        if proposal is None:
            raise NotFound("that shoot isn't one Sift is still asking about")
        if await self._store.made_from(proposal_id) is not None:
            raise NotFound("that shoot has already been made into a Photo Set")
        allowed = await self._access.visible_of(viewer, proposal.asset_ids)
        if len(allowed) != len(proposal.asset_ids):
            raise NotFound("that shoot isn't one Sift is still asking about")
        # A card that stood while its pictures were filed somewhere else. Refused, and answered by
        # the set they are in, so the card goes and the press does not make a second set. Asked
        # after the visibility read, so a card this viewer may not act on says nothing about where
        # its pictures are.
        holder = _holder(
            proposal.asset_ids,
            await catalog.photo_sets_holding(self._db, proposal.asset_ids),
        )
        if holder is not None:
            await self._store.settle_filed({proposal.id: holder})
            raise AlreadyFiled(
                "Those pictures are already in a Photo Set, so nothing new was made."
            )
        made = await self._make(
            name=proposal.name,
            called=proposal.name if name is None else name,
            asset_ids=list(proposal.asset_ids),
            proposal_id=proposal.id,
            user_id=viewer.id,
        )
        if made is None:
            raise ShootError("those pictures aren't a set any more")
        return made

    async def _make(
        self,
        *,
        name: str,
        called: str,
        asset_ids: list[str],
        proposal_id: str,
        user_id: str | None,
    ) -> MadeSet | None:
        """Make the Photo Set, then write the receipt and the link in one transaction.

        `name` is the creator's, which is who the receipt says the set is FOR; `called` is what
        the set is named, which is the same thing unless somebody typed another. Two arguments
        rather than one because they are two facts that usually coincide, and the receipt says the
        second only where it differs: a line reading "called Esme Wrenfield" under "Made a Photo
        Set for Esme Wrenfield" says nothing.

        The making comes first and is NOT in the transaction, because it is the other feature's
        write and reaching into it from here is what the seam exists to prevent. What the
        transaction holds is this feature's own record of it: the receipt somebody undoes from, and
        the link that says which grouping to take back. Both or neither.

        Through `telling`, because both of those rows are LISTED somewhere: the receipt is a line on
        the decisions page, and the link is what takes the proposal off the Shoots page. The Photo
        Set's own creation announces for itself, inside the feature that owns it: this is the half
        of the change that feature knows nothing about, and without it a second tab goes on offering
        a shoot that has already been made.
        """
        photo_set_id = await self._photo_sets.make(asset_ids, name=called)
        if photo_set_id is None:
            return None
        pictures = "picture" if len(asset_ids) == 1 else "pictures"
        grouped = f"{len(asset_ids)} {pictures} grouped as one shoot"
        # A double quote cannot be in `called` (`clean_name` refuses one), so quoting it here
        # cannot produce a sentence with its own quotes unbalanced.
        detail = f'{grouped}, called "{called}".' if called != name else f"{grouped}."
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            decision_id = await self._recorder.record_on(
                connection,
                queue=QUEUE,
                user_id=user_id,
                # Which task, where nobody pressed anything: the pass that groups one sitting.
                via=VIA_SHOOT,
                title=f"Created a Photo Set for {name}",
                detail=detail,
                payload=_payload(photo_set_id, asset_ids, called),
                subjects=[Subject(kind="asset", id=one) for one in asset_ids],
            )
            await self._store.link_on(
                connection,
                proposal_id=proposal_id,
                photo_set_id=photo_set_id,
                decision_id=decision_id,
            )
        log.info("shoots.made", photo_set=photo_set_id, pictures=len(asset_ids))
        return MadeSet(
            photo_set_id=photo_set_id,
            pictures=len(asset_ids),
            decision_id=decision_id,
            name=called,
        )

    async def refuse(self, viewer: Viewer, proposal_id: str) -> int:
        """Not a set. Remembered against the PICTURES, so no rearrangement of them comes back.

        See the schema: the rule that finds a shoot is greedy and its grouping moves, so a no tied
        to one grouping would be a no the next pass could route around.
        """
        proposal = await self._store.one(proposal_id)
        if proposal is None:
            raise NotFound("that shoot isn't one Sift is still asking about")
        allowed = await self._access.visible_of(viewer, proposal.asset_ids)
        if len(allowed) != len(proposal.asset_ids):
            raise NotFound("that shoot isn't one Sift is still asking about")
        return await self._store.refuse(proposal.id, proposal.asset_ids)

    async def name_the_rest(self, viewer: Viewer, proposal_id: str) -> Named:
        """Put the creator on the pictures of this shoot that carry nobody. A proposal, never a pass.

        **Separate from making the set, and that separation is the whole of it.** The two answer
        different questions: these pictures belong together, and these pictures are of this person,
        and a yes to the first is not a yes to the second. So this is its own press, its own
        receipt and its own undo, and either can be taken back without the other.

        Nothing is attributed that already carries somebody. The insert keeps the first answer, so a
        file somebody filed by hand keeps their row and is simply not part of what this reports.
        """
        proposal = await self._store.one(proposal_id)
        if proposal is None:
            raise NotFound("that shoot isn't one Sift is still asking about")
        if not proposal.unnamed_ids:
            raise ShootError("every picture of that shoot already carries somebody")
        allowed = await self._access.visible_of(viewer, proposal.unnamed_ids)
        wanted = [one for one in proposal.unnamed_ids if one in allowed]
        if len(wanted) != len(proposal.unnamed_ids):
            raise NotFound("that shoot isn't one Sift is still asking about")
        # Told, because this is the write in the whole feature that moves the most screens: a file's
        # own people, the creator's wall of files, every search narrowed by that person, and the
        # decisions page. `telling` says nothing when no row moved, which is the ordinary case for a
        # picture that had already been named by hand between the read and the press.
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            written = await catalog.attribute_assets_recording_on(
                connection, asset_ids=wanted, person_id=proposal.person_id
            )
            decision_id = None
            if written:
                files = "file" if len(written) == 1 else "files"
                decision_id = await self._recorder.record_on(
                    connection,
                    queue=QUEUE,
                    user_id=viewer.id,
                    title=f"Named the rest of a shoot as {proposal.name}",
                    detail=f"{len(written)} {files} of one shoot put under {proposal.name}.",
                    payload=_named_payload(proposal.person_id, written, proposal_id),
                    subjects=[Subject(kind="asset", id=one) for one in written]
                    + [Subject(kind="person", id=proposal.person_id)],
                    # WHAT THIS DECISION WAS, in the ledger's own words, rather than the vague
                    # `decided` a receipt carries when its area has not said: a person was named on
                    # these files: the act the ledger's `named` verb exists for. The person is the
                    # object (what the files were named WITH), as a filing's username is.
                    verb="named",
                    object=LedgerObject(kind="person", id=proposal.person_id, name=proposal.name),
                )
        if written:
            await self._store.mark_named(proposal_id, written)
        log.info("shoots.named", person=proposal.person_id, files=len(written))
        return Named(files=len(written), decision_id=decision_id)

    # --- taking one back ------------------------------------------------------------------------

    async def unname(
        self, person_id: str, asset_ids: Sequence[str], *, proposal_id: str = ""
    ) -> bool:
        """Undo one naming: the creator comes off exactly the files that naming put them on.

        And the shoot's card offers to name those pictures again (`proposal_id`, which the naming
        recorded): the naming marked them as carrying somebody, and an undo that left the mark
        would leave a card that can never offer the naming again. A record written before the
        proposal was recorded names none, and only the people come off.
        """
        if not person_id or not asset_ids:
            return False
        # The same screens the naming moved, moving back. Announced on the commit and only where a
        # row actually went, so an undo pressed twice tells nobody the second time.
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            removed = await catalog.detach_person_on(
                connection, asset_ids=list(asset_ids), person_id=person_id
            )
            if removed and proposal_id:
                await Store.unmark_named_on(connection, proposal_id, asset_ids)
        return bool(removed)

    async def take_back(self, photo_set_id: str, *, by: Viewer) -> bool:
        """Undo one making: the grouping goes, every picture stays exactly where it was.

        `by` is who pressed Undo, and the set's deletion is recorded as theirs."""
        await self._photo_sets.forget(photo_set_id, by=by)
        await self._store.forget_link(photo_set_id)
        return True

    async def made_from(self, proposal_id: str) -> Made | None:
        return await self._store.made_from(proposal_id)

    async def visible_of(self, viewer: Viewer, asset_ids: Sequence[str]) -> set[str]:
        """Which of these files this user may be shown. For the record's pictures."""
        return await self._access.visible_of(viewer, asset_ids)


def _holder(pictures: Sequence[str], held: Mapping[str, Sequence[str]]) -> str | None:
    """The Photo Set holding the most of these pictures, or None when none of them is in one.

    ANY picture filed is enough to make the proposal stale: making it would put that picture in a
    second set. The set holding the most of them is the one the answer points at, the smaller id
    breaking a tie so two reads of one library agree.
    """
    counted = Counter(one for picture in pictures for one in held.get(picture, ()))
    if not counted:
        return None
    return min(counted, key=lambda photo_set_id: (-counted[photo_set_id], photo_set_id))


def _payload(photo_set_id: str, asset_ids: Sequence[str], name: str) -> str:
    """What one making writes down about itself, so it can be taken back.

    The Photo Set's id is the whole of what undo needs: deleting it ungroups exactly the pictures
    that were grouped. The files travel too, and only so the record can draw a few of them: a
    receipt saying twenty pictures were grouped answers how many and not which, which is the one
    question somebody opens the list to ask.

    And the NAME it was made with, because "Create with a name..." makes that a choice:
    the set can be renamed later on its own page, and the record says what was chosen at the
    moment of the yes rather than what the set is called now. Reading it back is optional: the
    reverser needs only the id, so a record written before this key existed undoes exactly as it
    did.
    """
    return json.dumps(
        {"kind": "shoot", "photo_set_id": photo_set_id, "name": name, "assets": list(asset_ids)}
    )


def _named_payload(person_id: str, asset_ids: Sequence[str], proposal_id: str) -> str:
    """What one naming writes down: who, exactly which files the row actually landed on, and the
    shoot they were named from.

    The files that LANDED and not the files that were asked about, for the reason every bulk
    attribution in Sift records the same thing: a naming over eight files that writes six, because
    two already carried that person, can only be taken back cleanly if it wrote down the six. The
    shoot, because the naming marks those pictures on its card, and the undo unmarks them.
    """
    return json.dumps(
        {
            "kind": "named",
            "person_id": person_id,
            "assets": list(asset_ids),
            "proposal_id": proposal_id,
        }
    )


#: What proposes shoots, for the routes and the queue.
SERVICE: Part[ShootService] = Part("shoots_service")
