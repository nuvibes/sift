# SPDX-License-Identifier: AGPL-3.0-or-later
"""The wire shapes for the stash-box endpoints.

Nothing here carries a key. A key is write-only in the interface: typed in, sealed, and never
rendered back, so what a screen learns is whether one is set, never what it is.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from sift.kernel.records import Subject
from sift.kernel.wire import HistoryPiece, MadeBy, Wire


class BoxResponse(Wire):
    """One configured stash-box, as a screen reads it."""

    id: str
    name: str
    endpoint: str
    enabled: bool
    #: Whether a key is stored. Never the key.
    has_key: bool
    #: Whether that key can be opened right now, by the user asking.
    #:
    #: The master key that unseals a stored key is held for the length of a signed-in session; a
    #: restart leaves the browser's cookie working and every stored key unopenable. `has_key` stays
    #: true through that, so a screen reading only it would report a healthy box that cannot answer.
    key_ready: bool = True
    #: The tunnel this box's traffic goes through, or null for a direct connection.
    route: str | None = None
    requests_per_minute: int
    #: What this box's Sites are here: "site" or "person".
    #:
    #: On most stash-boxes the entries Sift reads as Sites are production companies, which are
    #: Sites here. On a stash-box whose creators are filed that way it is a Person, and asking
    #: the wrong half of the service comes back empty from a box that has them.
    #:
    #: Decided by Sift from the address, never sent in: it is a property of somebody else's service
    #: with one right answer, which the person configuring a box has no way to know (see
    #: `known_boxes`). Reported because it changes what a search does, and the screen says so.
    sites_are: str = "site"
    #: The word Sift knows this box by (`stashdb`, `fansdb`, `pmvstash`), or null for one it has
    #: never heard of.
    #:
    #: What the facets count under and the marks take their color from. The Enrich flyout writes it
    #: into a request, so a box with no word cannot be singled out there (the limit `known_boxes`
    #: records) rather than a typed name being used as an identifier.
    slug: str | None = None


class KeepPicture(Wire):
    """Keep one stash-box picture as the picture a subject here is shown with.

    ## No address

    A candidate's picture reaches the browser as Sift's own address (`/stash-boxes/{id}/
    picture?url=...`), so the page can draw it under a policy that allows no remote host, and the
    adapter rightly refuses that address because it is not on the stash-box's host. So nothing
    about the address comes from the browser: the entry has just been linked, the link holds what
    the box said, picture included, and the server reads the real address from its own copy.
    """

    subject: str = Field(min_length=1, max_length=32)
    local_id: str = Field(min_length=1, max_length=64)


class BoxList(Wire):
    boxes: list[BoxResponse] = Field(default=[])


class AddBox(Wire):
    """Configure a stash-box. The key is optional: an unkeyed box is simply not asked."""

    name: str = Field(min_length=1, max_length=80)
    endpoint: str = Field(min_length=1, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    route: str | None = None
    requests_per_minute: int = Field(default=240, ge=1, le=6000)


class EditBox(Wire):
    """Change what a box does. Only the fields actually sent are written."""

    enabled: bool | None = None
    route: str | None = None
    requests_per_minute: int | None = Field(default=None, ge=1, le=6000)


class EnrichEntities(Wire):
    """Ask the stash-boxes about some people, sites or tags.

    Ids rather than names, because a name is not an identity: two people can share one, and the
    thing being enriched is a row. The name is looked up on the server from the id, which also
    means a client cannot ask for one subject and have another one's name searched.
    """

    subject: str = Field(min_length=1, max_length=32)
    ids: list[str] = Field(min_length=1, max_length=500)

    #: Which stash-box to ask, by its own word; `all` for every switched-on one; empty for
    #: "whatever is set up", which is the `stash_boxes.auto_box` setting.
    #:
    #: The press's own answer, and it beats the setting. A flyout that named a box means that box,
    #: whatever the pane says; `all` is the flyout's "All stash-boxes" row; absent means "whatever
    #: is set up". See `settings.box_for`, the one reader of all three.
    box: str = Field(default="", max_length=40)


class LinkedEntity(Wire):
    """One person, site or tag a stash-box has been agreed to know."""

    subject: str
    id: str
    #: What THIS library calls them.
    name: str
    box_id: str
    box: str
    #: What the box calls them, which is the name that was matched.
    known_as: str
    fetched_at: int
    #: What that link filled in, said: ONE line the server built with every field AND ITS VALUES
    #: named ("FansDB filled in 7 aliases (...), gender (Female)"), the usernames and tags as links,
    #: drawn by the client's History drawing and built nowhere else. A link with no run says it
    #: filled nothing, and a run that filled nothing says that, so the two stay apart in words. A
    #: link made before Sift recorded what a box fills in says exactly that, with what the record
    #: agrees with it on today.
    said: list[HistoryPiece] = Field(default_factory=list)


class LinkedLedger(Wire):
    items: list[LinkedEntity]
    #: How many rows the whole ledger holds (of the kind asked for), for the pager.
    total: int = 0
    #: How many FILES carry an answer that was agreed to. A different count from the subjects
    #: listed (a file is enriched by a match, a person by a link), and both are the boxes' work,
    #: so the screen says both rather than letting one be read as the other.
    files: int = 0
    #: Where this page begins: what was asked for, except when the page was asked for by a row
    #: (`from`), where only the answer knows where that row is. The pager counts from here.
    offset: int = 0


class UndecidedEntity(Wire):
    """A subject the unattended enrichment could not choose for."""

    subject: str
    id: str
    name: str
    #: How many certain entries the boxes hold for the name between them.
    candidates: int
    seen_at: int


class UndecidedList(Wire):
    items: list[UndecidedEntity]
    total: int
    #: Where this page begins: what was asked for, except when the page was asked for by a row
    #: (`from`), where only the answer knows where that row is. The pager counts from here.
    offset: int = 0


class StudioQuestionView(Wire):
    """A Site a stash-box made that may be one person's own store, with the signs behind asking."""

    id: str
    name: str
    #: How many files the box filed under it, how many of their scenes credit her by its name,
    #: and how many other people they credit.
    files: int
    scenes: int
    credited: int
    others: int
    #: Where her files would go on Yes: the Site and her name there.
    site: str
    handle: str
    #: Whether that is a store page of hers (Clips4Sale, ManyVids and the like).
    store: bool


class StudioQuestions(Wire):
    items: list[StudioQuestionView]
    total: int
    offset: int = 0


class StudioAnswered(Wire):
    """One answer about a studio, with the receipt its Undo is pressed on."""

    receipt_id: str
    #: The line the answer wrote, as the History shows it.
    said: str
    #: `said` as pieces, the Site or username it names as the way to it.
    pieces: list[HistoryPiece] = Field(default_factory=list)


class EnrichStarted(Wire):
    """What was queued, so a screen can say so and go and look at it."""

    job_id: str
    #: How many of the ids given were real, visible subjects. A screen says "asking about 12"
    #: rather than "asking about 14" when two of them have gone.
    asked: int


class KeyWrite(Wire):
    api_key: str = Field(min_length=1, max_length=500)


class CheckResult(Wire):
    """Whether a box answered. `problem` is the sentence to show when it did not."""

    ok: bool
    problem: str | None = None


class RecordFound(Wire):
    """One subject as a stash-box describes it, already in Sift's words."""

    source_id: str
    source_name: str
    remote_id: str
    subject: str
    name: str
    disambiguation: str | None = None
    image_url: str | None = None
    #: The shipped pack's word for this site, where this entry IS a site and the pack knows it.
    #:
    #: Read through `GET /api/sites/icons/{slug}`: a chooser of studios is a list of sites Sift may
    #: already ship a logo for, and the alternative is a picture fetched from the stash-box through
    #: Sift's own proxy: one trip off this machine per row for a mark already on disk.
    #:
    #: Computed here rather than sent by the box, because the pack is Sift's and a box knows
    #: nothing about it. Null on every other kind of entry and on a site the pack has never heard
    #: of, which is what leaves the box's own picture as the answer.
    icon_slug: str | None = None
    file_count: int | None = None
    #: Whether every word that was typed appears somewhere in this entry's names. False rows are
    #: the fuzzy half of the service's answer (real entries, matching one of the words), and the
    #: screen draws them behind a press rather than in the list.
    every_word: bool = True
    fields: dict[str, Any] = Field(default={})
    #: Everything the box said that Sift has no field for, under the box's own key. Drawn
    #: read-only, and only when "show every field" is on. See the adapter for why none of these is
    #: a column.
    extra: dict[str, Any] = Field(default={})
    confidence: float = 0.0


class BoxAnswer(Wire):
    """What one box said. A box that could not be asked answers with its own sentence and no records."""

    box_id: str
    box_name: str
    records: list[RecordFound] = Field(default=[])
    fetched_at: int
    #: False when this came from the cache rather than from the service just now.
    fresh: bool
    problem: str | None = None


class LookUpResult(Wire):
    """Every box's answer to one question, in the order they are configured."""

    answers: list[BoxAnswer] = Field(default=[])


class LinkWrite(Wire):
    """Which entry in a stash-box a subject is. The id is theirs, not Sift's."""

    remote_id: str = Field(min_length=1, max_length=100)


class LinkView(Wire):
    """One stash-box's kept record of one subject, as a screen reads it."""

    box_id: str
    box_name: str
    #: The word Sift knows this box by (`stashdb`, `fansdb`, `pmvstash`), null for one it has
    #: never heard of. What the mark beside the name takes its color from; the slug rather than the
    #: name, because a name is whatever somebody typed when they added the box.
    box_slug: str | None = None
    remote_id: str
    #: The box's own page for this entry; null for a box whose pages Sift does not know, and the
    #: id is then drawn as text. Worked out on the server (`entry_page`), because a creator on a
    #: box whose studios are the creators is a studio there, which is a fact about the box the
    #: screen never holds.
    page_url: str | None = None
    fetched_at: int
    record: RecordFound
    #: The fields of the record THIS box gave, where the value it gave is still the value held: the
    #: record's fact rows say "From <box>, fetched <when>" on hover, and the Stash-boxes band says
    #: what each box filled in. Worked out on the server from the runs, which are the write-time
    #: record (`StashBoxService.fields_given`); a single value only, since a list is merged from
    #: every box that answered and no one box gave it.
    gave: list[str] = Field(default=[])


class LinkList(Wire):
    """Every stash-box that has been agreed to know this subject, newest first.

    And WHO MADE IT, which is a different fact and is carried here because it is read on the same
    screen from the same page load: a person five boxes know about may have been typed in by hand.
    Null wherever nothing recorded it: most rows made before Sift wrote this down, everything
    somebody created themselves, and a subject whose box has since been removed.
    """

    links: list[LinkView] = Field(default=[])
    made_by: MadeBy | None = None


class MatchView(Wire):
    """One stash-box's answer about one FILE, as the pile screen reads it."""

    asset_id: str
    box_id: str
    box_name: str
    remote_id: str
    #: certain when an exact fingerprint agreed, likely when it looked the same and the length
    #: agrees, unsure when the length could not be checked.
    grade: str
    state: str
    found_at: int
    #: When it was agreed to or refused; None while it waits (and on a row settled before
    #: the column carried the moment).
    decided_at: int | None = None
    record: RecordFound
    #: What applying this would change, field by field, with what is there beside what is offered.
    #: Empty when it would change nothing, which is a real answer and not a fault.
    changes: list[FieldChange] = Field(default=[])
    #: The people, tags and sites this would have to invent, each saying which it is. Listed above
    #: the button, never reported after it.
    creates: list[MissingView] = Field(default=[])
    #: The token to put on the end of this file's still, so the browser may keep it for a week
    #: rather than asking again on every visit. Null where nothing is yet known about the pictures
    #: to name them by, and the address is then left bare and checked each time, a slower row,
    #: never a broken one. The same field, for the same reason, as `FiledFromNameView.art`.
    art: str | None = None


class MissingView(Wire):
    """One row an answer would have to invent, and what kind of row it is.

    The kind is here because it is most of the decision. Thirty-one words in one alphabetical list
    (a performer, a hair colour, a studio) is a hard question; the same thirty-one under three
    headings is three easy ones.
    """

    name: str
    #: `person`, `tag` or `site`.
    kind: str


class SettleField(Wire):
    """One conflict, answered on the row it was shown on.

    A conflict on a file is settled here because the reconcile screen reads linked people, sites
    and tags, never files.

    Which answer, never the value. The value is re-derived from the same plan the row was drawn
    from, so this cannot be used to write something neither side ever said.
    """

    asset_id: str = Field(min_length=1, max_length=100)
    box_id: str = Field(min_length=1, max_length=100)
    key: str = Field(min_length=1, max_length=80)
    #: `theirs` writes the stash-box's answer over what is there. `both` keeps what is there and
    #: adds theirs, which only a field that holds more than one value can do. Asked for on a
    #: field that holds one, it is refused rather than guessed at. Keeping your own answer is the
    #: default and sends nothing at all.
    take: Literal["theirs", "both"] = "theirs"


class MissingRef(Wire):
    """One row somebody ticked. Kind AND name, which is what names a row rather than a word.

    Not the name alone: ticking the person Orla Tennant must never also invent a tag spelled the
    same way, and a library that files anything by name has words in common between its kinds.
    """

    name: str = Field(min_length=1, max_length=500)
    kind: str = Field(min_length=1, max_length=50)


class FieldChange(Wire):
    """One field a match would change, before anything changes.

    `outcome` is `write` when there is something new to put there and `conflict` when the two
    disagree and neither wins, which is what the reconcile screen is a list of.
    """

    key: str
    outcome: str
    mine: Any = None
    theirs: Any = None
    #: The rows this field names that do not exist yet. The writer drops a name it may not make,
    #: so these are written only if they are ticked.
    needs: list[MissingView] = Field(default=[])
    #: Whether the field writes something with none of `needs` made. False for a field that names
    #: only new rows: the confirm button counts it only once one of them is ticked.
    stands: bool = True
    # No "keep both" flag here: it could never be true. `enrichment.decide` returns a CONFLICT
    # only after the list branch has already returned, so a field holding more than one value is
    # never among the conflicts to begin with, and a flag for it would describe an offer nobody
    # could ever be given. The server's refusal of `take="both"` stays, because that guards an
    # untrusted body rather than describing an offer.


class MatchList(Wire):
    """What the pass found and nobody has answered, surest first."""

    matches: list[MatchView] = Field(default=[])
    total: int = 0
    #: How many FILES carry an answer somebody already agreed to: the pile's settled half, so an
    #: empty pile can say "all N answered" instead of reading as a feature nobody switched on.
    #: The whole library, not one file's share: nought when the list is narrowed to one file.
    answered: int = 0
    #: Where this page begins: what was asked for, except when the page was asked for by a row
    #: (`from`), where only the answer knows where that row is. The pager counts from here.
    offset: int = 0


class RecordHeld(Wire):
    """What Sift holds for one subject now, by the record registry's own field keys.

    What the chooser draws on the left of every row. Read through the subject's WRITER (the same
    `current` every plan is compared against), so a sheet opened away from the record's own page
    shows exactly what taking a field would be measured against, not a second assembly of it.
    """

    values: dict[str, Any] = Field(default_factory=dict)


class TakeFields(Wire):
    """Which of a linked box's fields to take onto the record, by field key.

    Empty is a real answer: somebody linked the entry and took none of its fields, and the run is
    written down as having filled nothing in, which is the difference between "filled nothing"
    and "Sift has no record of this" on the ledger.
    """

    keys: list[str] = Field(default_factory=list, max_length=64)


class Taken(Wire):
    """What taking a box's fields actually wrote. Every number is a field that landed."""

    fields: int = 0


class ApplyMatches(Wire):
    """Yes, to these, and to exactly these names being invented.

    `create` is per press and is never remembered. A stored "always make the people" is exactly the
    automatic creation this feature refuses: the names are listed above the button, so it is a
    decision somebody takes while looking at what it would do.

    NAMES rather than a yes-or-no, and that is the whole point of it. One tick over a list of
    thirty-one entries would offer two answers (make all of them, or make none and lose the four
    that were wanted), so anybody who read the list carefully would be punished for it. Each name
    is its own answer. An empty list means invent nothing, which is what the button does before
    anybody ticks anything.

    Capped, because it arrives from outside. A page settles at most `MATCH_PAGE` files and no
    honest answer names thousands of entries; a longer list is a mistake or an attempt, and either
    way it is refused rather than walked.
    """

    matches: list[MatchRef] = Field(default_factory=list)
    create: list[MissingRef] = Field(default_factory=list, max_length=2000)
    #: The disagreements somebody answered while looking at both values. Anything not named here
    #: keeps what is already there, which is what the rules do on their own.
    settle: list[SettleField] = Field(default_factory=list, max_length=2000)


class MatchRef(Wire):
    """Which answer. Theirs and ours together, which is what names one."""

    asset_id: str = Field(min_length=1, max_length=100)
    box_id: str = Field(min_length=1, max_length=100)


class Applied(Wire):
    """What a confirmation actually did. Every number is a write that landed."""

    files: int = 0
    fields: int = 0
    created: int = 0
    decision_id: str = ""
    #: Answers left waiting because the file is kept local now. A confirmation sends nothing, so
    #: the door never sees it; `nothing_applied` is what holds these back. See `apply_matches`.
    kept_local: int = 0


class Settled(Applied):
    """What settling one disagreement did, and the sentence its receipt says.

    `said` is the receipt's own title, handed back so the toast that confirms the press says what
    the History line says: which value was taken, what it replaced, and (when a second box offered
    a third value) which box's answer the same press set aside. Only the server knows that last
    part, because the second box's row exists only once the first value has been written.
    """

    said: str = ""
    #: `said` as pieces, the person, Site or tag it names as the way to it.
    pieces: list[HistoryPiece] = Field(default_factory=list)


class ScanWhat(Wire):
    """What a sweep should cover. Absent means the whole library, which is what it always was.

    Optional rather than a second route: asking the stash-boxes about a folder and asking them
    about everything is one verb with a scope, and two routes would be two sets of rules about
    who may ask, drifting apart quietly.
    """

    #: One folder, and everything under it. Null or missing sweeps the whole library.
    folder: str | None = None

    #: Which stash-box to ask, by its own word; `all` for every switched-on one; empty for
    #: whatever is set up. See `EnrichEntities.box`, which carries the same answer for the other
    #: verb, and `settings.box_for`, which reads both.
    box: str = Field(default="", max_length=40)

    #: Whether this press was Auto-enrich, which accepts what is certain without asking.
    #:
    #: The press is the consent. True applies an exact-hash match the
    #: moment it comes back (anything less certain still waits in the pile, and a field the file
    #: already carries differently is still a question on the confirm screen). False is Enrich,
    #: the verb that asks and lets somebody choose, so everything waits. Neither reads
    #: `stash_boxes.apply_certain`, which is only for the runs nobody pressed.
    auto: bool = False

    #: Asked again on purpose, about files a box has already answered (the chooser's Identify
    #: again). A settled answer is otherwise never touched by a later ask, so pressing it would
    #: change nothing. With this, an answer that differs from the settled one comes back as a
    #: question.
    again: bool = False

    #: These files and no others, for the verb on the grid's own menu. Named files win over a
    #: folder and over the whole library: asking about a selection is the narrowest thing anybody
    #: can ask for here, so it is the one that decides.
    #:
    #: A list rather than one id because the grid's verbs all take a selection: right-clicking one
    #: file and right-clicking forty are the same act on that screen, and a verb that only worked on
    #: one would be the only one there that did.
    assets: list[str] | None = None


class ScanStarted(Wire):
    """The sweep that was queued, so a screen can watch it, and what was left out of it.

    The last four fields are about the named-files form of the request, where somebody pointed at a
    selection rather than asking for the whole library. A file this user cannot open is not asked
    about, and the reply says so: silence would look exactly like a file that was sent and matched
    nothing.

    They stay at their defaults for a whole-library or one-folder sweep, which has nothing to
    report yet: it has not worked out which files it will ask about.
    """

    job_id: str
    #: False when the feature is switched off, in which case nothing was queued.
    started: bool = True
    #: How many of the named files were actually queued.
    asked: int = 0
    #: How many were left out. Zero for a sweep, which names no files.
    skipped: int = 0
    #: Why the first of them was left out, worded for ONE file and written to be read on screen.
    reason: str | None = None
    #: The same reason worded for more than one. Set exactly when `reason` is.
    #:
    #: The screen pairs this with a count it worked out itself ("Asking about 3 of 5"), so a
    #: sentence written about one file would print "3 of 5. It is in your vault." Same shape and
    #: same reasoning as `kernel.reach.BulkWriteDone.reason`, which this reply deliberately mirrors
    #: field for field.
    reason_many: str | None = None
    #: Whether that reason is the viewer's OWN locked vault, the one they can undo. A flag rather
    #: than the screen matching on `reason`, for the reason `BulkWriteDone` gives for its own.
    vault_locked: bool = False
    #: Whether that reason is "Do not enrich". Nothing failed and nothing is missing, so a screen
    #: says it in the tone of a decision being honoured rather than of an error. Read off this
    #: flag, never off the sentence, for the same reason as the one above.
    kept_local: bool = False


class DisagreementView(Wire):
    """One field two answers differ about, with both of them on it."""

    #: Which kind of thing the field belongs to, from the record registry's own list. A bare
    #: string here would leave the screen free to be handed a kind it has no form for.
    subject: Subject
    local_id: str
    #: What the subject is called, so a row reads without opening anything.
    name: str
    box_id: str
    box_name: str
    key: str
    mine: Any = None
    theirs: Any = None
    #: Both values in words, by the rule a History line says them in (`records.value_said`): a
    #: box's constant is a word ("Natural", never "NATURAL") and a length says "cm", so the
    #: table and the line beside it say one value one way. Null where there is nothing to say in
    #: words (nothing, an object), and the raw value above is drawn instead.
    mine_said: str | None = None
    theirs_said: str | None = None


class DisagreementList(Wire):
    disagreements: list[DisagreementView] = Field(default=[])


class SettleDisagreement(Wire):
    """Which answer to keep, for ONE box's row. Keeping your own writes no field.

    A row is a field on a record as one box sees it, so the box is part of what names it: a record
    two boxes disagree about carries two rows under one field. Required rather than defaulted, since
    a press that does not say which row it is could only be guessed at.
    """

    subject: str = Field(min_length=1, max_length=40)
    local_id: str = Field(min_length=1, max_length=100)
    box_id: str = Field(min_length=1, max_length=100)
    key: str = Field(min_length=1, max_length=80)
    take_theirs: bool = False


class EnrichmentRunView(Wire):
    """The last time ONE stash-box enriched one thing, and whether anybody pressed it.

    What the "Last:" line under a menu item reads, and what the History's enrichment line says out
    loud. The box's name is null where the box has since been removed, which is the honest drawing
    of a row that says a box did this and no longer has one to name.
    """

    box_id: str
    box_name: str | None = None
    #: The box's own word (`stashdb`, `fansdb`), or null for a box Sift has never heard of. What
    #: the mark beside the line takes its colour from.
    box_slug: str | None = None
    at: int
    #: True where a run nobody was watching did it. False where somebody pressed a button.
    automatic: bool = False


class EnrichmentState(Wire):
    """Where one file or record stands with enrichment from outside this machine.

    Both halves of one question asked by one screen at one moment (may this be sent, and when was
    it last sent), so they are one answer rather than two calls a menu has to wait on in turn.
    """

    subject: str
    id: str
    #: True where THIS ROW says it must never be sent to a stash-box. Sift's own passes ignore it.
    #:
    #: The row's own switch, which is what the menu's label reverses on: a press here turns this
    #: one off and on. Whether anything would actually be sent is `refused` below.
    kept_local: bool = False
    #: True where nothing about this may leave the machine, for any reason.
    #:
    #: `kept_local` or (for a file) something it is filed under being kept local. Two fields
    #: rather than one because a menu needs both and they answer
    #: different questions: this one decides whether the Enrich rows are drawn refused, and the one
    #: above decides which way the switch beside them reads. Collapsed into one, a file under a
    #: kept-local Site would offer "Allow enrichment": a press that changes the file's own row
    #: and changes nothing about what leaves.
    refused: bool = False
    #: Why, in the words the row draws when it is refused. Empty where nothing refuses it.
    why: str = ""
    #: Every box that has enriched it, newest first. Empty where none ever has, which is what makes
    #: the menu draw no "Last:" line at all rather than one saying never.
    runs: list[EnrichmentRunView] = Field(default=[])


class KeepLocal(Wire):
    """Keep this thing local, or let it be enriched again."""

    kept_local: bool
