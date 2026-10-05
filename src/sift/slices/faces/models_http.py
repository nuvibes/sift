# SPDX-License-Identifier: AGPL-3.0-or-later
"""The shapes the face endpoints send and accept.

Nothing here carries an embedding: a face's numbers never leave the machine, so the wire has
nowhere to put them.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from sift.kernel.cover_frame import CoverFrame
from sift.kernel.reach import BulkWriteDone
from sift.kernel.wire import HistoryLink, Wire
from sift.slices.faces.models import Attribution, PileStatus, ToCheckKind

#: How many faces of one pile a listing carries; the rest are still counted.
MAX_PILE_FACES = 12

#: How many piles one page of the groups screen holds: each pile costs its own queries.
PILES_PER_PAGE = 24

#: A person's screen's first page, before it is measured: a starting size, not a ceiling
#: (what a route accepts is `MAX_PAGE_SIZE`).
MAX_APPEARANCES = 100

#: The most faces one decision may name: a backstop against a hand-written call.
MAX_FACES_PER_DECISION = 500

#: A pile's own screen's first page, before it is measured; a starting size, as above.
FACES_PER_PAGE = 60


class SightingView(Wire):
    """One appearance of one face, with the moment to seek to.

    `person_id` and `person_name` are absent together. An appearance attributed to somebody the
    viewer has hidden comes back as a face with no name: handing over the id while withholding
    the name would be handing over the fact that a hidden person is in this file.
    """

    track_id: str
    asset_id: str
    started_ms: int
    ended_ms: int
    #: When this face's PICTURE was taken, in milliseconds into the file: the moment of the clearest
    #: face in the appearance, which is the one its picture is cut from. Where pressing the face
    #: plays from: the frame somebody pressed, rather than `started_ms`, the first frame the face
    #: was seen in. Equal to `started_ms` on a still.
    picture_ms: int
    #: What to put on the end of this face's picture address, or absent when there is nothing to
    #: put. It says how many times what this user may see has changed (nothing about the crop,
    #: which never changes), and it is what stops a face staying readable after the person in it
    #: was hidden.
    art: str | None = None
    person_id: str | None = None
    person_name: str | None = None
    confidence: float | None = None
    attribution: Attribution | None = None
    #: Whether agreeing to this face would also teach Sift what the person looks like. False for a
    #: crop too poor to learn from; it still takes the name, it is simply not filed as evidence.
    teachable: bool = True
    #: Whether it actually became one of the pictures Sift matches against. `teachable` is the
    #: forecast and this is the outcome: agreeing OFFERS a face, and a poor crop, a near-copy of one
    #: already held, or an appearance that has given its share is declined, silently, until now.
    is_reference: bool = False
    #: Whether the file this face is on is in the vault, with the vault shut. True only for a
    #: viewer whose concealment mode keeps a placeholder: in the default mode the file is absent
    #: and so is the face. A screen with one of these draws a padlock and does not ask for the
    #: picture, which is refused for the same reason the file itself is.
    locked: bool = False
    #: The group of look-alike faces this one is waiting in, and what has happened to that group.
    #:
    #: Both absent for a face that carries a name, including one whose name was withheld: the pile
    #: is a resemblance fact about the person, so it is concealed with everything else about them.
    #: An id with no status is a pile that has since been rebuilt away: there is nothing to open.
    pile_id: str | None = None
    pile_status: PileStatus | None = None
    #: Whether the face is turned past the quality bar's angle: Sift may ask about it and never
    #: names it alone. Said on a file's own faces; False everywhere else.
    turned: bool = False


class GroupCard(Wire):
    """A pile of faces that resemble each other.

    `size` is how many of it this viewer may see, which is not always how many are in it. A pile
    they may see none of does not come back at all.
    """

    id: str
    status: PileStatus
    size: int
    faces: list[SightingView] = Field(default=[])


class GroupPage(Wire):
    """One page of piles, and how many of them this viewer may see altogether.

    `total` counts the piles with an unanswered question in them that this user may see any of,
    the same set the page is taken from, or the pager would describe a screen nobody is looking
    at.
    """

    groups: list[GroupCard] = Field(default=[])
    total: int = 0
    #: Where this page starts, as the server resolved it. The client needs telling because a
    #: request that named a row rather than an offset does not know the answer until it arrives,
    #: and a pager reading zero over rows that are really the four-hundredth is a readout that lies.
    offset: int = 0


class GroupDetail(Wire):
    """One pile, with a page of its faces and how many of them this viewer may see.

    Separate from `GroupCard` because the two answer different questions: a card says enough to
    recognise a pile among hundreds, this says everything needed to decide about part of one.
    """

    group: GroupCard
    total: int = 0
    #: Where this page starts, as the server resolved it. The client needs telling because a
    #: request that named a row rather than an offset does not know the answer until it arrives,
    #: and a pager reading zero over rows that are really the four-hundredth is a readout that lies.
    offset: int = 0


class IdentifiedCard(Wire):
    """Everything Sift has attached to one person, as one card.

    The counterpart of `GroupCard` on the other screen, and deliberately the same shape: a wall of
    one card per face says nothing about who is on it, so thirteen appearances of one person would
    read as thirteen separate answers to thirteen separate questions.

    `waiting` is how many of them are proposals rather than settled decisions. It is the number the
    card exists to show: a person with faces waiting is one press away from being finished, and
    that cannot be seen when every face is its own card.

    `matched` and `confirmed` are the other two, counted apart rather than folded into `size`,
    because the question this screen could not answer was which of these Sift attached BY ITSELF:
    "28 faces" reads the same whether a person agreed to every one of them or to none. `surest` is
    the best confidence among the matched ones, and belongs to those alone: a confirmed face was
    decided by somebody rather than by arithmetic, so a percentage beside it would be describing
    the wrong thing.
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
    """One reason a group may be somebody, on a `may_be` card.

    `kind` is `likeness` (the group as a whole comes close to their pictures; the group's own
    `likeness` is the number) or `folder`: most of a folder Sift filed as them is this group, and
    the four folder fields say which and by how much, counted as THIS viewer may see the files
    (the folder reader's proposal), or `stash-box`: they are known by a stash-box's starter pictures
    alone, and `box_names` names the boxes those pictures came from (empty where none is recorded).
    A reason leaves the other kinds' fields null.
    """

    kind: str
    folder_id: str | None = None
    folder_name: str | None = None
    in_folder: int | None = None
    group_files: int | None = None
    box_names: list[str] = Field(default_factory=list)


class MayBeGroup(Wire):
    """One unnamed group on a "these groups may be her" card.

    `size` is the group's unnamed faces this viewer may see. `likeness` is how close the group as
    a whole comes to the person, on the group scale and not the single-face one, null where it
    could not be measured. `ticked` is whether the card starts with the group chosen. `faces` are
    the ones the card shows, which are the ones a Yes confirms; the rest of the group is offered as
    questions.
    """

    pile_id: str
    size: int = 0
    likeness: float | None = None
    ticked: bool = False
    faces: list[SightingView] = Field(default=[])
    reasons: list[GroupReasonView] = Field(default=[])


class ToCheckCard(Wire):
    """One item of the review list: a person's proposals, a group of faces nobody has named, or a
    file whose one face is not the person already filed on it.

    One shape for all three because they are one list (what is left to check, in the order of how
    much one press settles), and `kind` says which of the three questions this card asks. Several
    shapes in one list would be a client working out which it was holding from which fields
    happened to be filled in, which is the reading that goes wrong the day a group carries a name.

    `size` is what one press settles, as this viewer may see it: the proposals standing for a
    person, or the faces in a pile. `best` belongs to a proposal alone (a pile carries no
    measurement of who it might be), and `status` to a pile alone.
    """

    kind: ToCheckKind
    id: str = ""
    size: int = 0
    person_name: str | None = None
    best: float | None = None
    status: PileStatus | None = None
    #: The person on the file, for a `mismatch` alone: that kind's `id` is the FILE, so the person
    #: the two answers are about is a second fact rather than the row's own name. Absent on the
    #: other two kinds, where the person either IS the id or does not exist yet.
    person_id: str | None = None
    #: Which pass filed that name (`folder`, `stash_box` and the rest), for a `mismatch` alone.
    #: A row that says where the name came from is a row somebody can weigh; "Sift put it there"
    #: is not.
    source: str | None = None
    faces: list[SightingView] = Field(default=[])
    #: The groups a `may_be` card asks about, closest first. See `MayBeGroup`. Empty on every
    #: other kind. The card's `id` is the PERSON, as on a `person` card; `size` is the faces in
    #: the groups listed and `best` the closest group's likeness.
    groups: list[MayBeGroup] = Field(default=[])


class ToCheckPage(Wire):
    """One page of the review list, how long it is, and how many groups the floor holds back.

    `small_groups` is the count of unnamed groups under the floor: not listed and not counted in
    `total`, said in one line at the foot of the list, and opened by asking for them. It is answered
    beside the page rather than by a second request so the line and the list are one reading of the
    library: a count taken a moment later would disagree with the list above it as soon as
    anything was decided.
    """

    items: list[ToCheckCard] = Field(default=[])
    total: int = 0
    #: Where this page starts, as the server resolved it. See `IdentifiedPage.offset`.
    offset: int = 0
    small_groups: int = 0


class IdentifiedPage(Wire):
    """One page of people Sift has decided about, and how many there are altogether."""

    people: list[IdentifiedCard] = Field(default=[])
    total: int = 0
    #: Where this page starts, as the server resolved it. The client needs telling because a
    #: request that named a row rather than an offset does not know the answer until it arrives,
    #: and a pager reading zero over rows that are really the four-hundredth is a readout that lies.
    offset: int = 0
    #: How many People on the whole wall Sift knows from starter pictures alone, and how many it
    #: knows otherwise, counted whatever `starters` the page was asked with: the two numbers the
    #: wall's control carries. See `StartersShow`.
    starters_only: int = 0
    others: int = 0


class FacesWrite(Wire):
    """Some faces, named by their appearances, that one decision is about."""

    track_ids: list[str] = Field(default_factory=list, max_length=MAX_FACES_PER_DECISION)


class RunScope(StrEnum):
    """Which of one person's faces a bulk answer on their own screen is about.

    The three a person's screen offers under its Yes and its No, in the order its menu draws them:
    the faces on the page somebody is looking at, the ones they picked, and every face on the tab.

    **A page is sent as its faces, not as an offset**, and that is the one judgement here worth
    stating. The screen sizes its pages to the window, so an offset means nothing the server could
    reproduce, and even a reproducible one would be read AFTER the press, by which time answering
    one face has moved every face behind it up a place. Re-reading "page three" would then act on
    faces nobody was shown. The faces that were drawn are the only honest description of what was
    drawn, so `page` and `picked` both carry ids and differ in what the ids ARE; `all` carries
    none, because the tab is the server's to know and a list sent back could be a stale one.
    """

    PAGE = "page"
    PICKED = "picked"
    ALL = "all"


class RunWrite(Wire):
    """A bulk answer about one person's faces, and which of them it is about.

    Every field has a default so an empty body is the whole tab: what the card on the People Sift
    can recognize wall sends.

    The ids are NARROWED by the server, never trusted: only a face standing on this tab for this
    person is acted on, so an id answered since the page was drawn, or one that was never theirs,
    is simply not part of the press.
    """

    scope: RunScope = RunScope.ALL
    track_ids: list[str] = Field(default_factory=list, max_length=MAX_FACES_PER_DECISION)

    @model_validator(mode="after")
    def _the_ids_are_what_the_scope_says(self) -> RunWrite:
        """A page or a pick names its faces; the whole tab names none.

        Refused rather than read generously either way. A `page` with no faces is a screen that
        drew nothing asking to act on it, and reading it as "all" would turn an empty page into the
        widest press there is. An `all` carrying faces is a caller that means one of the other two.
        """
        if self.scope is RunScope.ALL and self.track_ids:
            raise ValueError("the whole tab is named by the server; send no faces with it")
        if self.scope is not RunScope.ALL and not self.track_ids:
            raise ValueError(f"'{self.scope.value}' needs the faces it is about")
        return self


class DisagreeingPersonView(Wire):
    """One person the Disagreements tab is about, as its row reads her.

    `count` is how many of her files the tab holds; `source` is the word the pass wrote on the most
    of them (`folder`, `stash_box` and the rest), so the row says where the name came from.

    `filed` is that said in full, the line under her heading: the folders named where a folder's
    name gave it ("Added from the name of the folder ..."), each in `filed_links` as the folder and
    its way to the folder view, in the shape a History line's names travel in. A client draws the
    words as they come and links each name it is handed; it builds no sentence of its own.
    """

    person_id: str
    person_name: str
    count: int = 0
    source: str = ""
    filed: str = ""
    filed_links: list[HistoryLink] = Field(default=[])


class DisagreeingPeople(Wire):
    """Everybody the Disagreements tab is about, the most files first, and the tab's own total.

    `total` is the sum of the counts, which is the number on the tab: the same rows, gathered.
    """

    people: list[DisagreeingPersonView] = Field(default=[])
    total: int = 0


class DisagreementsWrite(Wire):
    """A Yes or a No over one person's disagreements, and which of them it is about.

    The scope is `RunScope`'s, word for word, with the rows named by their FILE: a disagreement is
    a row about a file, and the file is what a No takes her off. A page or a pick names its files;
    all of hers names none, because that set is the server's to know. The files are narrowed by the
    server, never trusted (`FaceService.answer_disagreements`).
    """

    yes: bool
    scope: RunScope = RunScope.ALL
    asset_ids: list[str] = Field(default_factory=list, max_length=MAX_FACES_PER_DECISION)

    @model_validator(mode="after")
    def _the_files_are_what_the_scope_says(self) -> DisagreementsWrite:
        """A page or a pick names its files; all of hers names none. See `RunWrite`."""
        if self.scope is RunScope.ALL and self.asset_ids:
            raise ValueError("all of hers is named by the server; send no files with it")
        if self.scope is not RunScope.ALL and not self.asset_ids:
            raise ValueError(f"'{self.scope.value}' needs the files it is about")
        return self


class GroupsWrite(Wire):
    """An answer to a "these groups may be her" card: which groups, and for a Yes the faces shown.

    `pile_ids` are the groups the press is about: the ones left ticked for a Yes, every one on
    the card for a No. `track_ids` are the faces the card SHOWED of those groups, which a Yes
    confirms: the server confirms only faces inside a group it still offers for this person, so a
    stale card cannot confirm anything else, and it offers the rest of each group as questions.
    A No sends none; it refuses every face of the groups.
    """

    pile_ids: list[str] = Field(min_length=1, max_length=MAX_FACES_PER_DECISION)
    track_ids: list[str] = Field(default_factory=list, max_length=MAX_FACES_PER_DECISION)


class MoveFacesWrite(FacesWrite):
    """Some faces, and the group they belong in.

    `pile_id` null means a group of exactly these: a split. A pile id means merge them into that
    one. The two are one operation with two destinations, so they are one request rather than two
    endpoints that would drift apart.
    """

    pile_id: str | None = None


class MovedFaces(BulkWriteDone):
    """Where the moved faces ended up, so the screen can go there.

    The counts and the reason come from `BulkWriteDone`, so a face left behind by a locked vault is
    reported here in the same words every other bulk write uses. `pile_id` is this route's own.
    """

    pile_id: str = ""


class NameFacesWrite(FacesWrite):
    """Some faces, and who they are.

    Either an existing person by id, or a name to create one under: exactly one of the two. A
    call carrying both is refused rather than resolved by precedence, because the two orders of
    precedence are equally defensible and whichever is chosen will surprise somebody.
    """

    person_id: str | None = None
    name: str | None = None
    #: Also offer this name for every other unnamed face GROUPED with these.
    #:
    #: A flag rather than the behaviour, and the reason is that the two surfaces asking are asking
    #: different questions. From a file, or from a whole group's own card, "this face is Marion"
    #: means the group is Marion: the grouping already claims they are one person and it made that
    #: claim at import. From INSIDE a group, picking three faces out of forty and naming them is a
    #: deliberate act of separating them from the rest, which is exactly what the over-splitting
    #: grouping needs somebody to be able to do, so that surface sends false and names what was
    #: picked and nothing else.
    #:
    #: The extras are SUGGESTED, never confirmed. See `FaceService.name_with_their_group`.
    whole_group: bool = False


class RecognitionStrength(Wire):
    """How reliably Sift can recognize one person.

    The target and the floor travel with the count. A screen that held its own copy of either would
    go on drawing the same verdict after the number behind it moved.

    `verdict` is a token and not a sentence: `none`, `weak`, `fair`, `good`, `strong`. The bands
    belong here, beside the numbers that decide them; the wording a person reads belongs to the
    screen drawing it, which is the same split every other reading in Sift uses.
    """

    references: int = 0
    target: int = 0
    floor: int = 0
    strong: int = 0
    fraction: float = 0.0
    verdict: str = "none"
    #: Her STARTER pictures from a stash-box in use, which make Sift ask about her and never name
    #: her; never part of `references`. Then the ones retired, and the boxes, by name.
    starters: int = 0
    starters_retired: int = 0
    starters_from: list[str] = Field(default_factory=list)


class StarterPerson(Wire):
    """One of the People the starters press is for: the name to read and the page it opens."""

    id: str
    name: str


class StartersOffer(Wire):
    """How many People the press "Use stash-box pictures as starters" would act on, shown first.

    Linked to a stash-box and holding no reference of any kind. Zero is an answer the pane draws as
    nothing to offer, not an error.
    """

    people: int = 0
    #: Who they are, by name, so the count can be read as people before the press reaches out to
    #: a stash-box for every one of them. Only the People this viewer may be shown, held to the
    #: People wall as the known-people list is, so a person the vault holds back is not named.
    who: list[StarterPerson] = Field(default_factory=list)


class StartersQueued(Wire):
    """The press, done: the task that fetches and checks the pictures, and how many People it has."""

    job_id: str
    people: int


class WorkLeft(Wire):
    """What a running scan still has to get through.

    Two numbers because the second cannot be got from the first. Files left is what a bar counts
    down; moments are what those files COST (one seek, one decode, one look), and unlike a file,
    one moment costs about the same as the next whatever it was cut from. An estimate of the time
    remaining built on files lurches every time the queue reaches a run of long videos.
    """

    files: int = 0
    moments: int = 0


class ReferenceStrengths(Wire):
    """How many reference faces every person has, for a screen drawing several of them at once.

    The counts are keyed by person id and hold only the people who have any. A picker asking per
    row would be one request per candidate per keystroke; this is the same numbers in one answer.
    """

    people: dict[str, int] = Field(default={})
    #: The same person's verdict token (`none`, `weak`, `fair`, `good`, `strong`), from the one rule
    #: the person's own reading uses. A screen maps the token to words and never bands the count.
    verdicts: dict[str, str] = Field(default={})
    target: int = 0
    floor: int = 0
    strong: int = 0


class FacesDecided(BulkWriteDone):
    """What a decision about several faces actually changed, and what it left alone.

    `changed`, `skipped` and `reason` come from `BulkWriteDone` rather than being declared again,
    so a face skipped for a locked vault is reported in the same words as a file skipped for one.
    The two fields below are this route's own and have no counterpart anywhere else.
    """

    person_id: str | None = None
    #: The person's name as the viewer may read it, so the screen says who the faces were added
    #: to ("4 faces have been added to Wren Halloway") in the name the row holds, which for a name
    #: typed in a different case is the existing person's spelling. None where it is not told.
    person_name: str | None = None
    #: How many OTHER faces the same name was offered for, because they were grouped with these.
    #:
    #: Zero unless the caller asked for the group. See `NameFacesWrite.whole_group`. These are
    #: offered rather than decided, so the screen says "and 19 more to check" rather than counting
    #: them as named, and they are answered on the person's own wall.
    offered: int = 0
    #: The receipt this press wrote, so the surface that pressed can offer Undo at once. See
    #: `service.RunAnswered`. None where nothing changed, or on a route that writes no receipt.
    decision_id: str | None = None


class FacesRefused(BulkWriteDone):
    """What one No over a person's run of faces refused, and the receipt that takes it back.

    `BulkWriteDone` plus the receipt, for the reason `FacesDecided` carries one: the screen that
    pressed offers Undo off the reply, which a count alone cannot give it.
    """

    decision_id: str | None = None


class MatchesAgreed(Wire):
    """What agreeing with everything Sift matched to one person did.

    Two numbers rather than one, because they answer different questions and the second is the one
    that is not guessable from the first. `confirmed` is how many appearances now carry somebody's
    own answer; `references` is how many pictures of that person Sift learned from them, which is
    always fewer: one appearance files one picture at most, a picture already held is refused by
    its own identity, and a crop that cannot be read files none.

    Its own shape rather than `FacesDecided`, whose `changed` would have to mean one of the two and
    leave the other nameless. Nothing was skipped for the caller to be told about either: this press
    names no faces, so there is no list of them to come back short.
    """

    confirmed: int = 0
    references: int = 0


class AppearancePage(Wire):
    """One person's appearances, and how many files they are on that this viewer may see.

    `waiting`, `matched` and `confirmed` are the three ways a face came to carry this name, counted
    over the WHOLE of what this viewer may see rather than over the page. They are what the tab row
    on that screen is drawn from, and they travel with the page because the screen asks one
    question: counts that arrived one at a time, as somebody pressed each tab, would leave two of
    the three looking empty.

    `total` is still the narrowing THIS page is of (what the pager needs), which is one of the
    three, or their sum where nothing was narrowed.
    """

    items: list[SightingView] = Field(default=[])
    total: int = 0
    waiting: int = 0
    matched: int = 0
    confirmed: int = 0
    #: Their folder-filed files with a face still unnamed: the Files wall's `unnamed_face:` total.
    unnamed_from_folder: int = 0
    #: Where this page starts, as the server resolved it. The client needs telling because a
    #: request that named a row rather than an offset does not know the answer until it arrives,
    #: and a pager reading zero over rows that are really the four-hundredth is a readout that lies.
    offset: int = 0
    #: Who the page is about, as this viewer may know them. Carried on the page rather than read
    #: off its first face, because a tab with nothing on it has no first face, and the screen
    #: would then call somebody "Identified faces" in its title and its trail. None for a person this
    #: viewer may not be told about, which is the same withholding `SightingView` makes.
    person_name: str | None = None


class FetchStarted(Wire):
    """The job now downloading the models.

    The id and nothing else. What a screen does with it is watch the bar the dashboard already
    draws for every job, and offer the cancel that already exists, so there is nothing here for a
    second progress mechanism to be built out of.
    """

    job_id: str


class KnownPerson(Wire):
    """One person Sift can already recognize, and how many reference faces say so.

    The id is here so the screen can link the name to that person's own page: "do I have them" is
    one question short of what somebody reading it wants next.
    """

    id: str
    name: str
    faces: int
    #: The band her page draws `faces` in (`Strength.verdict`); a chooser marks the thin by it.
    verdict: str = "none"
    #: STARTER pictures from a stash-box in use (Sift only asks about her), counted apart from
    #: `faces`, her own. Somebody listed with no faces of her own is known by starters alone.
    starters: int = 0
    #: The picture she is drawn by everywhere else, so a picker of these people draws her as the
    #: People picker does. The same columns, spelled the same way, as a person on the People wall,
    #: and read through the same visibility rule (`Repository.visible_people`): a cover naming a
    #: file this viewer may not open is withheld, and so is the face cut from it.
    cover_asset_id: str | None = None
    cover_upload_id: str | None = None
    cover_at_ms: int | None = None
    cover_frame: CoverFrame | None = None
    cover_track_id: str | None = None
    #: The face crops' version for this viewer, which a face cover's address carries so the
    #: browser keeps it. See `kernel/serving.py face_version`.
    art: str | None = None
    #: The two marks that keep her out of every swap, as her People row carries them, so a
    #: swap's chooser of these people draws her refused rather than offering what will not go.
    keep_local: bool = False
    keep_from_swaps: bool = False
    #: The file or folder she was created from, where facial fingerprints created her: read from
    #: her History line while it stands, so an Undo takes the mark away with her.
    from_fingerprints: str | None = None


class PileTracks(Wire):
    """Every face in one pile this user may see, by id: what a verb over the WHOLE pile acts on.

    The pile's own page holds two hundred faces at a time and the wall's card a handful; a
    decision about all of a pile of two thousand needs the ids and nothing else about them.
    """

    track_ids: list[str]
    total: int


class GroupSetAside(Wire):
    """What setting a group of faces aside did, and the record it can be taken back from.

    Named for the thing it is about rather than for the verb. Two response models called `Ignored`
    cannot both be named that in the API description, and what a generator falls back to is the
    module path, which publishes how this application is laid out inside.
    """

    settled: bool = Field(description="Whether this was the press that set it aside.")
    decision_id: str = Field(
        default="",
        description="The record this decision wrote, so it can be taken back from the toast.",
    )


class KnownPeople(Wire):
    """Who is already covered. Read before adding somebody, to answer "do I have them".

    Names, counts and where each person's picture is. It is a list to search, not a gallery: a
    page of face crops for several hundred People is a great deal of picture to send to answer a
    yes-or-no question, so no picture travels here, only the cover columns a row is drawn from,
    and the browser asks for a picture only as its row comes into view.
    """

    items: list[KnownPerson] = Field(default=[])
    total: int = 0


class PackExportRequest(Wire):
    """Which People and which people waiting for a matching face go in the file, and whether
    their pictures do: both lists empty is everybody. Pictures are opt-in, so by default no
    photograph of anybody leaves this machine.
    """

    #: The file's own name, which the other library keys it by: this library's name.
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(default="1", max_length=40)
    person_ids: list[str] = Field(default_factory=list)
    entry_ids: list[str] = Field(default_factory=list)
    include_pictures: bool = False


class PackImported(Wire):
    """What taking in a facial fingerprints file did: how many faces it brought that were not held
    already, and how many people it names. Nobody is made or given anything here: the pass after it
    places each person by face, and says so in History. Importing the same file twice brings
    nothing the second time, so a zero is the honest answer rather than a failure.
    """

    added: int = 0
    people: int = 0
    #: Whether recognition is switched on. A file taken in while it is off is stored and waits;
    #: the screen says that nothing is recognized until the switch is on, with the switch beside it.
    recognizing: bool = True


class FingerprintOfferView(Wire):
    """A group that looks like somebody a facial fingerprints file holds: the question it asks
    while making people from fingerprints is off ("This group looks like Liora Fenwick, from a
    fingerprints file. Make her a person?"). `entry_id` is what the Yes sends."""

    entry_id: str
    name: str
    pile_id: str
    #: How many faces the file or folder brought for them.
    faces: int = 0
    #: How many confirmed faces the file said they had where it was made; None where it said none.
    confirmed: int | None = None
    #: The file's or the folder's name.
    source: str = ""


class FingerprintOffers(Wire):
    """Every group's question about facial fingerprints, one group per person, closest first."""

    items: list[FingerprintOfferView] = Field(default=[])


class WaitingEntry(Wire):
    """Somebody a facial fingerprints file or a folder brought whom no face here matches yet."""

    entry_id: str
    name: str
    #: How many faces the file or folder brought for them.
    faces: int
    #: The file's confirmed count of them where it was made; None from a folder or an older file.
    confirmed: int | None = None
    #: The file's or the folder's name.
    source: str
    #: When they were taken in, in milliseconds.
    added_at: int
    #: Whether a facial fingerprints file exported now would carry them.
    exportable: bool = False


class WaitingFingerprints(Wire):
    """Every waiting entry, newest first."""

    items: list[WaitingEntry] = Field(default=[])
    #: How many of them an export of everybody carries as people of their own.
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
    """Saying an appearance is not somebody. Remembered, so it is not offered again."""

    person_id: str


class FaceSettingsView(Wire):
    """What the settings screen draws, and what the feature is currently able to do.

    `ready` is not the same as `enabled` and the screen needs both: switched on with no model files
    present is the ordinary state right after somebody turns it on, and it needs to read as "fetch
    the models" rather than as a broken feature.
    """

    enabled: bool
    ready: bool
    family: str
    device: str
    depth: str
    #: Why recognition cannot run on the device it is set to, in words, or null when it can. A card
    #: that is not there fails every scan into the job log, so this screen must not say "Ready".
    device_problem: str | None = None
    #: When the library was last swept, in milliseconds, or null if it never has been. Not the same
    #: as when a file was last scanned, which one import moves.
    last_run_at: int | None = None
    #: Whether that last pass was stopped or canceled rather than finished, so the screen can
    #: say so beside the time.
    last_run_canceled: bool = False
    #: How many files' faces are still described by a model other than the one set. Nonzero
    #: after the family changes, until the pass that measures them again has been through.
    measured_by_another_model: int = 0
    #: Reference faces another model described that arrived as numbers alone and so cannot be
    #: measured again. Out of matching for good; the person who imported them decides what to do.
    references_without_pictures: int = 0
    #: Files never looked at for faces, and files looked at under an older rule or other settings
    #: (or by a pass that did not finish): the two reasons a file wants a look. Both zero while
    #: the feature is off. See `FaceService.backlog`.
    never_scanned: int = 0
    scanned_under_older_rules: int = 0
    installed: list[str] = Field(default=[])
