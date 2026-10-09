# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shapes the face endpoints send and accept; no embedding ever leaves the machine."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from sift.kernel.cover_frame import CoverFrame
from sift.kernel.reach import BulkWriteDone
from sift.kernel.wire import HistoryLink, Wire
from sift.slices.faces.models import Attribution, PileStatus, ToCheckKind

MAX_PILE_FACES = 12

#: Piles on one page of the groups screen: each pile costs its own queries.
PILES_PER_PAGE = 24

#: A person's screen's first page: a starting size, not a ceiling.
MAX_APPEARANCES = 100

#: A backstop against a hand-written call.
MAX_FACES_PER_DECISION = 500

FACES_PER_PAGE = 60


class SightingView(Wire):
    """One appearance of one face; `person_id` and `person_name` are withheld together."""

    track_id: str
    asset_id: str
    started_ms: int
    ended_ms: int
    #: When this face's picture was taken, which pressing it plays from.
    picture_ms: int
    #: The picture address's suffix, which changes when what this user may see changes.
    art: str | None = None
    person_id: str | None = None
    person_name: str | None = None
    confidence: float | None = None
    attribution: Attribution | None = None
    #: Whether agreeing would also teach Sift; False for a crop too poor to learn from.
    teachable: bool = True
    is_reference: bool = False
    #: Behind a shut vault for a viewer shown placeholders: drawn as a padlock, no picture.
    locked: bool = False
    #: The group it waits in; both absent for a named face, as the pile is a fact about her.
    pile_id: str | None = None
    pile_status: PileStatus | None = None
    #: Turned past the bar's angle: asked about, never named alone.
    turned: bool = False


class GroupCard(Wire):
    """A pile of faces that resemble each other; `size` counts what this viewer may see."""

    id: str
    status: PileStatus
    size: int
    faces: list[SightingView] = Field(default=[])


class GroupPage(Wire):
    """One page of piles, and how many of them this viewer may see altogether."""

    groups: list[GroupCard] = Field(default=[])
    total: int = 0
    #: Where this page starts, as the server resolved it from a named row.
    offset: int = 0


class GroupDetail(Wire):
    """One pile, with a page of its faces and how many of them this viewer may see."""

    group: GroupCard
    total: int = 0
    #: Where this page starts, as the server resolved it from a named row.
    offset: int = 0


class IdentifiedCard(Wire):
    """Everything Sift has attached to one person, as one card.

    `waiting`, `matched` and `confirmed` count apart; `surest` belongs to the matched alone.
    """

    person_id: str | None = None
    person_name: str | None = None
    size: int = 0
    waiting: int = 0
    matched: int = 0
    confirmed: int = 0
    surest: float | None = None
    faces: list[SightingView] = Field(default=[])


class GroupReasonView(Wire):
    """One reason a group may be somebody: `likeness`, `folder` or `stash-box`, each its fields."""

    kind: str
    folder_id: str | None = None
    folder_name: str | None = None
    in_folder: int | None = None
    group_files: int | None = None
    box_names: list[str] = Field(default_factory=list)


class MayBeGroup(Wire):
    """One unnamed group on a "these groups may be her" card; a Yes confirms the faces shown."""

    pile_id: str
    size: int = 0
    likeness: float | None = None
    ticked: bool = False
    faces: list[SightingView] = Field(default=[])
    reasons: list[GroupReasonView] = Field(default=[])


class ToCheckCard(Wire):
    """One item of the review list, its `kind` saying which of the questions it asks."""

    kind: ToCheckKind
    id: str = ""
    size: int = 0
    person_name: str | None = None
    best: float | None = None
    status: PileStatus | None = None
    #: The person on the file, for a `mismatch` alone, whose `id` is the file.
    person_id: str | None = None
    #: Which pass filed that name, for a `mismatch` alone.
    source: str | None = None
    faces: list[SightingView] = Field(default=[])
    #: The groups a `may_be` card asks about, closest first; its `id` is the person.
    groups: list[MayBeGroup] = Field(default=[])


class ToCheckPage(Wire):
    """One page of the review list, its total, and the groups under the floor counted beside it."""

    items: list[ToCheckCard] = Field(default=[])
    total: int = 0
    offset: int = 0
    small_groups: int = 0


class IdentifiedPage(Wire):
    """One page of people Sift has decided about, and how many there are altogether."""

    people: list[IdentifiedCard] = Field(default=[])
    total: int = 0
    #: Where this page starts, as the server resolved it from a named row.
    offset: int = 0
    #: People on the whole wall known by starters alone, and the rest.
    starters_only: int = 0
    others: int = 0


class FacesWrite(Wire):
    """Some faces, named by their appearances, that one decision is about."""

    track_ids: list[str] = Field(default_factory=list, max_length=MAX_FACES_PER_DECISION)


class RunScope(StrEnum):
    """Which of one person's faces a bulk answer is about: a page and a pick send their ids.

    The ids drawn are the honest description of what was drawn; the whole tab sends none.
    """

    PAGE = "page"
    PICKED = "picked"
    ALL = "all"


class RunWrite(Wire):
    """A bulk answer about one person's faces; the server narrows the ids, never trusts them."""

    scope: RunScope = RunScope.ALL
    track_ids: list[str] = Field(default_factory=list, max_length=MAX_FACES_PER_DECISION)

    @model_validator(mode="after")
    def _the_ids_are_what_the_scope_says(self) -> RunWrite:
        """A page or a pick names its faces; the whole tab names none."""
        if self.scope is RunScope.ALL and self.track_ids:
            raise ValueError("the whole tab is named by the server; send no faces with it")
        if self.scope is not RunScope.ALL and not self.track_ids:
            raise ValueError(f"'{self.scope.value}' needs the faces it is about")
        return self


class DisagreeingPersonView(Wire):
    """One person the Disagreements tab is about, with the line saying where her name came from."""

    person_id: str
    person_name: str
    count: int = 0
    source: str = ""
    filed: str = ""
    filed_links: list[HistoryLink] = Field(default=[])


class DisagreeingPeople(Wire):
    """Everybody the Disagreements tab is about, the most files first, and the tab's total."""

    people: list[DisagreeingPersonView] = Field(default=[])
    total: int = 0


class DisagreementsWrite(Wire):
    """A Yes or a No over one person's disagreements, scoped as `RunWrite`, by file."""

    yes: bool
    scope: RunScope = RunScope.ALL
    asset_ids: list[str] = Field(default_factory=list, max_length=MAX_FACES_PER_DECISION)

    @model_validator(mode="after")
    def _the_files_are_what_the_scope_says(self) -> DisagreementsWrite:
        """A page or a pick names its files; all of hers names none."""
        if self.scope is RunScope.ALL and self.asset_ids:
            raise ValueError("all of hers is named by the server; send no files with it")
        if self.scope is not RunScope.ALL and not self.asset_ids:
            raise ValueError(f"'{self.scope.value}' needs the files it is about")
        return self


class GroupsWrite(Wire):
    """An answer to a may-be card: the groups, and for a Yes the faces the card showed."""

    pile_ids: list[str] = Field(min_length=1, max_length=MAX_FACES_PER_DECISION)
    track_ids: list[str] = Field(default_factory=list, max_length=MAX_FACES_PER_DECISION)


class MoveFacesWrite(FacesWrite):
    """Some faces and the group they belong in; a null `pile_id` splits them into a new one."""

    pile_id: str | None = None


class MovedFaces(BulkWriteDone):
    """Where the moved faces ended up, so the screen can go there."""

    pile_id: str = ""


class NameFacesWrite(FacesWrite):
    """Some faces and who they are: an existing person or a new name, never both."""

    person_id: str | None = None
    name: str | None = None
    #: Also offer this name, as a suggestion, for the faces grouped with these.
    whole_group: bool = False


class StrengthBasis(Wire):
    """What a person's strength rests on: her pictures by origin, and her faces by outcome."""

    imported: int = 0
    confirmed: int = 0
    learned: int = 0
    turned: int = 0
    matched: int = 0
    asked: int = 0
    yes: int = 0
    no: int = 0


class RecognitionStrength(Wire):
    """How reliably Sift can recognize one person; `verdict` is a token the screen words."""

    rate: float | None = None
    basis: StrengthBasis = Field(default_factory=lambda: StrengthBasis())
    references: int = 0
    target: int = 0
    floor: int = 0
    strong: int = 0
    fraction: float = 0.0
    verdict: str = "none"
    #: Her starter pictures in use and retired, never part of `references`, and their boxes.
    starters: int = 0
    starters_retired: int = 0
    starters_from: list[str] = Field(default_factory=list)


class StarterPerson(Wire):
    """One of the People the starters press is for."""

    id: str
    name: str


class StartersOffer(Wire):
    """How many People "Use stash-box pictures as starters" would act on, shown first."""

    people: int = 0
    #: Who they are, held to the People wall's visibility.
    who: list[StarterPerson] = Field(default_factory=list)


class StartersQueued(Wire):
    """The press, done: the task that fetches the pictures, and how many People it has."""

    job_id: str
    people: int


class WorkLeft(Wire):
    """What a running scan still has to get through: files, and the moments they cost."""

    files: int = 0
    moments: int = 0


class ReferenceStrengths(Wire):
    """How many reference faces every person has, in one answer for a screen of several."""

    people: dict[str, int] = Field(default={})
    verdicts: dict[str, str] = Field(default={})
    rates: dict[str, float] = Field(default={})
    basis: dict[str, StrengthBasis] = Field(default={})
    target: int = 0
    floor: int = 0
    strong: int = 0


class FacesDecided(BulkWriteDone):
    """What a decision about several faces changed and what it left alone."""

    person_id: str | None = None
    #: The person's name as the viewer may read it.
    person_name: str | None = None
    #: Other faces of the same group offered the name, not decided.
    offered: int = 0
    #: The receipt this press wrote, so the screen can offer Undo.
    decision_id: str | None = None


class FacesRefused(BulkWriteDone):
    """What one No over a person's faces refused, and the receipt that takes it back."""

    decision_id: str | None = None


class MatchesAgreed(Wire):
    """What agreeing with everything Sift matched to one person did: faces and pictures learned."""

    confirmed: int = 0
    references: int = 0


class AppearancePage(Wire):
    """One person's appearances, and their counts over everything this viewer may see."""

    items: list[SightingView] = Field(default=[])
    total: int = 0
    waiting: int = 0
    matched: int = 0
    confirmed: int = 0
    unnamed_from_folder: int = 0
    #: Where this page starts, as the server resolved it from a named row.
    offset: int = 0
    #: Who the page is about, carried since an empty tab has no first face.
    person_name: str | None = None


class FetchStarted(Wire):
    """The job now downloading the models."""

    job_id: str


class KnownPerson(Wire):
    """One person Sift can already recognize, and how many reference faces say so."""

    id: str
    name: str
    faces: int
    verdict: str = "none"
    #: Starter pictures in use, counted apart from her own `faces`.
    starters: int = 0
    #: Her cover, as the People wall carries it and under the same visibility rule.
    cover_asset_id: str | None = None
    cover_upload_id: str | None = None
    cover_at_ms: int | None = None
    cover_frame: CoverFrame | None = None
    cover_track_id: str | None = None
    #: The face crops' version for this viewer (`kernel/serving.py face_version`).
    art: str | None = None
    #: The two marks that keep her out of every swap.
    keep_local: bool = False
    keep_from_swaps: bool = False
    #: The file or folder facial fingerprints created her from, while that History line stands.
    from_fingerprints: str | None = None


class PileTracks(Wire):
    """Every face in one pile this user may see, by id: what a verb over the whole pile acts on."""

    track_ids: list[str]
    total: int


class GroupSetAside(Wire):
    """What setting a group of faces aside did, and the record it can be taken back from."""

    settled: bool = Field(description="Whether this was the press that set it aside.")
    decision_id: str = Field(
        default="",
        description="The record this decision wrote, so it can be taken back from the toast.",
    )


class KnownPeople(Wire):
    """Who is already covered, as a list to search; no picture travels here."""

    items: list[KnownPerson] = Field(default=[])
    total: int = 0


class PackExportRequest(Wire):
    """Which People and waiting entries go in the file (both empty is everybody), and whether
    their pictures do, which is opt-in."""

    name: str = Field(min_length=1, max_length=120)
    version: str = Field(default="1", max_length=40)
    person_ids: list[str] = Field(default_factory=list)
    entry_ids: list[str] = Field(default_factory=list)
    include_pictures: bool = False


class PackImported(Wire):
    """What taking in a facial fingerprints file added, and how many people it names."""

    added: int = 0
    people: int = 0
    #: Whether recognition is on; a file taken in while off waits.
    recognizing: bool = True


class FingerprintOfferView(Wire):
    """A group that looks like somebody a fingerprints file holds; a Yes sends `entry_id`."""

    entry_id: str
    name: str
    pile_id: str
    faces: int = 0
    confirmed: int | None = None
    source: str = ""


class FingerprintOffers(Wire):
    """Every group's question about facial fingerprints, one group per person, closest first."""

    items: list[FingerprintOfferView] = Field(default=[])


class WaitingEntry(Wire):
    """Somebody a facial fingerprints file or a folder brought whom no face here matches yet."""

    entry_id: str
    name: str
    faces: int
    confirmed: int | None = None
    source: str
    added_at: int
    exportable: bool = False


class WaitingFingerprints(Wire):
    """Every waiting entry, newest first."""

    items: list[WaitingEntry] = Field(default=[])
    exportable: int = 0


class MadeFromFingerprints(Wire):
    """The person a Yes made, so the screen can link to her."""

    person_id: str


class FolderImportStarted(Wire):
    """A folder import queued as a task: the job a screen follows to its end."""

    job_id: str


class FolderByPath(Wire):
    """A folder of people on the machine Sift runs on, named by its full path."""

    path: str = Field(min_length=1, max_length=4096)


class ConfirmWrite(Wire):
    """Agreeing that an appearance is somebody."""

    person_id: str


class RejectWrite(Wire):
    """Saying an appearance is not somebody."""

    person_id: str


class FaceSettingsView(Wire):
    """What the settings screen draws; `ready` is apart from `enabled`, as models may be missing."""

    enabled: bool
    ready: bool
    family: str
    device: str
    depth: str
    #: Why recognition cannot run on its device, or null.
    device_problem: str | None = None
    #: When the library was last swept, or null.
    last_run_at: int | None = None
    last_run_canceled: bool = False
    #: Files still described by a model other than the one set.
    measured_by_another_model: int = 0
    #: Reference faces of another model with no picture, so out of matching for good.
    references_without_pictures: int = 0
    never_scanned: int = 0
    scanned_under_older_rules: int = 0
    unread_files: int = 0
    installed: list[str] = Field(default=[])
