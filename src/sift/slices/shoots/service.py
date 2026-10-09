# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proposing shoots from the meaning index, and answering them."""

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

#: A ceiling, since the pass costs one index lookup per seed; the rest is read by the next pass.
PER_CREATOR = 2000


class ShootError(Exception):
    """Something the caller asked for cannot be done, with a sentence saying why."""


class NotFound(ShootError):
    """No such proposal, or none still waiting on an answer."""


class AlreadyFiled(ShootError):
    """Its pictures are in a Photo Set already; refused, and the proposal marked answered."""


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
        # The Photo Sets' own rules, handed in so no second copy of the number drifts. None means
        # the composition root has not handed the length in: only the length goes unchecked.
        self._least = least
        self._longest = longest

    # --- finding them ---------------------------------------------------------------------------

    async def auto_file(self) -> bool:
        """Whether a shoot is made without anybody being asked. Read per pass, never cached."""
        return bool(await self._preferences.get_app(AUTO_FILE_KEY))

    async def find(self) -> Proposed:
        """Find and write shoots per creator; writes nothing when nothing can be compared."""
        planned = await self.plan()
        if not planned.can_compare:
            log.info("shoots.declined", reason="nothing has been described by the model in use")
            return Proposed(creators=0, found=0, filed=0)
        # A card written under a lower floor would otherwise stand on the queue for ever.
        dissolved = await self._store.dissolve_under(self._least)
        settled = await self._settle_standing()
        filed = 0
        for creator in planned.found:
            # Written as proposals either way, so an older card on these pictures is replaced.
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
        """What a pass now would find, written by nothing, so a dry run and `find` agree."""
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
        """One creator's loose pictures grouped, less refusals, each widened by `_unnamed_near`."""
        pool = await catalog.loose_pictures_of(self._db, person_id, limit=PER_CREATOR)
        refused = await self._store.refused_among(pool)
        waiting = [asset_id for asset_id in pool if asset_id not in refused]
        if len(waiting) < self._least:
            return []

        # One read of the pool's numbers, then every comparison in memory. See `among`.
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
        """Pictures of the seed's sitting that carry nobody; somebody else's are left alone."""
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
        """File each proposal on its own, so one failure spares the rest and each has its undo."""
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
        """Answer every waiting proposal whose pictures a Photo Set already holds."""
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
        """Where one proposal sits in the queue from zero, or None when answered or absent."""
        return await self._store.position_of(proposal_id)

    async def one(self, proposal_id: str) -> Proposal | None:
        return await self._store.one(proposal_id)

    async def make(self, viewer: Viewer, proposal_id: str, *, name: str | None = None) -> MadeSet:
        """Create the Photo Set; refused if this viewer may not see every picture."""
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
        # After the visibility read, so a hidden card says nothing about where its pictures are.
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
        """Create the Photo Set through its seam, then its receipt and link in one transaction."""
        photo_set_id = await self._photo_sets.make(asset_ids, name=called)
        if photo_set_id is None:
            return None
        pictures = "picture" if len(asset_ids) == 1 else "pictures"
        grouped = f"{len(asset_ids)} {pictures} grouped as one shoot"
        # `clean_name` refuses a double quote, so this quoting stays balanced.
        detail = f'{grouped}, called "{called}".' if called != name else f"{grouped}."
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            decision_id = await self._recorder.record_on(
                connection,
                queue=QUEUE,
                user_id=user_id,
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
        """Not a set: remembered per picture, so no regrouping of them comes back."""
        proposal = await self._store.one(proposal_id)
        if proposal is None:
            raise NotFound("that shoot isn't one Sift is still asking about")
        allowed = await self._access.visible_of(viewer, proposal.asset_ids)
        if len(allowed) != len(proposal.asset_ids):
            raise NotFound("that shoot isn't one Sift is still asking about")
        return await self._store.refuse(proposal.id, proposal.asset_ids)

    async def name_the_rest(self, viewer: Viewer, proposal_id: str) -> Named:
        """Put the creator on this shoot's nameless pictures: its own press, receipt and undo."""
        proposal = await self._store.one(proposal_id)
        if proposal is None:
            raise NotFound("that shoot isn't one Sift is still asking about")
        if not proposal.unnamed_ids:
            raise ShootError("every picture of that shoot already carries somebody")
        allowed = await self._access.visible_of(viewer, proposal.unnamed_ids)
        wanted = [one for one in proposal.unnamed_ids if one in allowed]
        if len(wanted) != len(proposal.unnamed_ids):
            raise NotFound("that shoot isn't one Sift is still asking about")
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
        """Undo one naming, and unmark its pictures on the card so it can offer them again."""
        if not person_id or not asset_ids:
            return False
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            removed = await catalog.detach_person_on(
                connection, asset_ids=list(asset_ids), person_id=person_id
            )
            if removed and proposal_id:
                await Store.unmark_named_on(connection, proposal_id, asset_ids)
        return bool(removed)

    async def take_back(self, photo_set_id: str, *, by: Viewer) -> bool:
        """Undo one set: the grouping goes, the pictures stay; recorded as `by`'s."""
        await self._photo_sets.forget(photo_set_id, by=by)
        await self._store.forget_link(photo_set_id)
        return True

    async def made_from(self, proposal_id: str) -> Made | None:
        return await self._store.made_from(proposal_id)

    async def visible_of(self, viewer: Viewer, asset_ids: Sequence[str]) -> set[str]:
        """Which of these files this user may be shown."""
        return await self._access.visible_of(viewer, asset_ids)


def _holder(pictures: Sequence[str], held: Mapping[str, Sequence[str]]) -> str | None:
    """The Photo Set holding the most of these pictures, the smaller id breaking a tie, or None."""
    counted = Counter(one for picture in pictures for one in held.get(picture, ()))
    if not counted:
        return None
    return min(counted, key=lambda photo_set_id: (-counted[photo_set_id], photo_set_id))


def _payload(photo_set_id: str, asset_ids: Sequence[str], name: str) -> str:
    """The undo record: the id undoes it, the files and the chosen name are for the record."""
    return json.dumps(
        {"kind": "shoot", "photo_set_id": photo_set_id, "name": name, "assets": list(asset_ids)}
    )


def _named_payload(person_id: str, asset_ids: Sequence[str], proposal_id: str) -> str:
    """The undo record: the files that landed, not those asked about, and the shoot."""
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
