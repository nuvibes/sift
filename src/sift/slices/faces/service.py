# SPDX-License-Identifier: AGPL-3.0-or-later
"""The face feature, as one thing other parts of Sift can ask to do something.

Everything below is behind one switch, and the switch is checked here rather than by each caller.
**With it off, nothing loads, nothing is fetched, and nothing is written, on any path.** That is
not a tidy default; it is the point. The models are not even imported until something asks for
them, which is why an install that never turns this on pays nothing for it at boot.

The order of what happens to a file is worth stating once, because it is the reason the feature is
affordable at all:

    look at a spread of moments -> find faces -> follow each across the moments -> refuse the poor
    ones -> describe the survivors -> keep the picture -> compare against everybody -> pile up
    whoever is left

and every description is **kept**, which is what makes adding a person later a few seconds of
arithmetic instead of a second pass over the library.
"""

from __future__ import annotations

from sift.kernel.jobs import JobQueue
from sift.kernel.jobs.tuning import BACKGROUND_PRIORITY
from sift.kernel.wiring import Part
from sift.slices.faces.receipts import (
    AGREED_WITH_MATCHES,
    AGREED_WITH_PROPOSALS,
    ASKED_ONLY_QUEUE,
    FACE_QUEUE,
    IDENTIFIED_QUEUE,
    NAMED_GROUPS,
    REFUSED_FACES,
    REFUSED_GROUPS,
    STARTERS_QUEUE,
    STOPPED_ASKING,
)
from sift.slices.faces.service_base import (
    FACES_SWITCHED_OFF,
    Configured,
    FacesDisabled,
    SettingsReader,
    status_of,
)
from sift.slices.faces.service_decisions import RunAnswered
from sift.slices.faces.service_disagreements import DisagreeingPerson, DisagreementsMixin
from sift.slices.faces.service_fingerprints import FingerprintsMixin
from sift.slices.faces.service_identified import AppearancesView, IdentifiedView, _attention_first
from sift.slices.faces.service_matching import _how_sure, _undo_asks
from sift.slices.faces.service_may_be import (
    LIKENESS_REASON,
    STARTER_REASON,
    GroupMayBe,
    GroupProposalView,
    GroupReason,
    ToCheckView,
)
from sift.slices.faces.service_packs import AliasClash, PackOutcome, PacksMixin
from sift.slices.faces.service_references import ReferencesMixin, Strength, Strengths
from sift.slices.faces.service_review import GroupView, ReviewMixin
from sift.slices.faces.service_runs import RunsMixin
from sift.slices.faces.service_scanning import ScanningMixin, _with_depth
from sift.slices.faces.service_starters import STARTERS_PER_PERSON, StartersMixin
from sift.slices.faces.service_sweep import SweepMixin
from sift.slices.faces.service_upkeep import UpkeepMixin
from sift.slices.faces.service_visibility import FaceVisibility, Sighting
from sift.slices.faces.service_waiting import WaitingMixin
from sift.slices.faces.service_weights import (
    MODELS_MISSING_REFUSAL,
    MODELS_NOT_INSTALLED,
    REMEASURE_GROWTH,
    REMEASURE_PAGE,
    REMEASURE_PAGE_MAX,
    REMEASURE_PAGE_MIN,
    REMEASURE_TARGET_SECONDS,
    Remeasure,
    next_remeasure_page,
)

#: What this module answers for: the service and its entry points, every public name of its
#: parts, and the private names callers already import from here.
__all__ = [
    "AGREED_WITH_MATCHES",
    "AGREED_WITH_PROPOSALS",
    "ASKED_ONLY_QUEUE",
    "FACES_SWITCHED_OFF",
    "FACE_QUEUE",
    "FACE_STARTERS",
    "IDENTIFIED_QUEUE",
    "LIKENESS_REASON",
    "LINKED_SINCE_STARTERS",
    "MODELS_MISSING_REFUSAL",
    "MODELS_NOT_INSTALLED",
    "NAMED_GROUPS",
    "REFUSED_FACES",
    "REFUSED_GROUPS",
    "REMEASURE_GROWTH",
    "REMEASURE_PAGE",
    "REMEASURE_PAGE_MAX",
    "REMEASURE_PAGE_MIN",
    "REMEASURE_TARGET_SECONDS",
    "SERVICE",
    "STARTERS_PER_PERSON",
    "STARTERS_QUEUE",
    "STARTER_REASON",
    "STOPPED_ASKING",
    "AliasClash",
    "AppearancesView",
    "Configured",
    "DisagreeingPerson",
    "FaceService",
    "FaceVisibility",
    "FacesDisabled",
    "GroupMayBe",
    "GroupProposalView",
    "GroupReason",
    "GroupView",
    "IdentifiedView",
    "PackOutcome",
    "Recognition",
    "Remeasure",
    "RunAnswered",
    "SettingsReader",
    "Sighting",
    "Strength",
    "Strengths",
    "ToCheckView",
    "_attention_first",
    "_how_sure",
    "_undo_asks",
    "_with_depth",
    "next_remeasure_page",
    "status_of",
]


#: The work that files a stash-box's pictures of somebody as STARTER references. Here rather than in
#: `jobs.py` for the reason `IDENTIFIED_QUEUE` gives: `Recognition.linked` below queues it, and
#: `jobs` imports this module.
FACE_STARTERS = "face_starters"

#: The payload of that work when a LINK asked for it rather than the press: everybody whose link was
#: made since links kept the box's picture list, and who has no reference row. The press names its
#: People instead (the count it showed), which is what keeps the People linked before starters
#: existed out of every run but the one somebody chose to start.
LINKED_SINCE_STARTERS = {"linked": True}


class FaceService(
    DisagreementsMixin,
    ReviewMixin,
    RunsMixin,
    ScanningMixin,
    SweepMixin,
    FingerprintsMixin,
    ReferencesMixin,
    PacksMixin,
    StartersMixin,
    UpkeepMixin,
    WaitingMixin,
):
    """What the rest of Sift asks. Holds the models once they have been loaded, and nothing else.

    Each responsibility is a mixin in a module of its own beside this one, and each mixin names the
    parts it relies on as its bases. This class puts them together; every caller imports it from
    here.
    """


class Recognition:
    """The face feature as the rest of Sift asks about a newly created person.

    A thin thing on purpose. It exists so the slice that creates People can say "somebody was made
    with this name" without knowing recognition exists, and so that an install with the feature
    switched off gets a quiet zero rather than a refusal: not having turned it on is not an error,
    and a person created on such an install must simply be created.
    """

    def __init__(self, service: FaceService, *, queue: JobQueue | None = None) -> None:
        self._service = service
        self._queue = queue

    async def linked(self, person_id: str) -> None:
        """Somebody was linked to a stash-box: queue her starter pictures, if she needs any.

        Only for somebody with no reference row at all (`FaceService.wants_starters`), and quiet
        with the feature off, like the rest of this class. Queued rather than done here: the
        pictures are fetched through the stash-box door at its own pace, and the checks need the
        models, which the work waits for (`jobs.starters`) the way a scan does.
        """
        if self._queue is None:
            return
        try:
            wanted = await self._service.wants_starters([person_id])
        except FacesDisabled:
            return
        if wanted:
            # COLLAPSED, not one task per person: an Auto-enrich over four hundred People links
            # four hundred of them in a minute, and four hundred tasks in Activity would be the
            # noise, not the news. The work this asks for reads who needs starters when it runs
            # (`jobs.starters`, `LINKED_SINCE_STARTERS`), so one run covers the whole burst.
            await self._queue.enqueue_when_settled(
                FACE_STARTERS, LINKED_SINCE_STARTERS, priority=BACKGROUND_PRIORITY
            )

    async def claim_for(self, person_id: str, name: str) -> int:
        """Give them what was held, then ask for the library's faces to be matched again.

        The re-match is asked for here, once, for every caller: new references change who the
        unnamed faces look like, and a claim that waited for the next import to take effect would
        leave the person unrecognized until then.
        """
        try:
            arrived = await self._service.claim_for(person_id, name)
        except FacesDisabled:
            return 0
        if arrived and self._queue is not None:
            # Imported here rather than at the top: the jobs module imports this one.
            from sift.slices.faces.jobs import ask_for_rematching

            await ask_for_rematching(self._queue)
        return arrived

    async def waiting_for(self, name: str) -> list[str]:
        try:
            return await self._service.waiting_for(name)
        except FacesDisabled:
            return []

    async def held_for(self, person_id: str, name: str) -> int:
        try:
            return await self._service.held_for(person_id, name)
        except FacesDisabled:
            return 0

    async def released(self) -> None:
        """Somebody was deleted, so put whatever they were holding back into the open piles.

        Quiet when the feature is off, exactly as the two above are: a person can be deleted on an
        install that has never recognized a face, and that is not an error to report.
        """
        try:
            await self._service.release_deleted()
        except FacesDisabled:
            return


#: Recognizing faces.
SERVICE: Part[FaceService] = Part("faces")
