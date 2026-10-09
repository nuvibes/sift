# SPDX-License-Identifier: AGPL-3.0-or-later
"""The wire shapes for the stash-box endpoints. A key is write-only: a screen learns only whether
one is set."""

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
    has_key: bool
    #: False after a restart until someone signs in: the master key lives only in a session.
    key_ready: bool = True
    route: str | None = None
    requests_per_minute: int
    #: "site" or "person": what this box's Sites are here, decided from the address (`known_boxes`).
    sites_are: str = "site"
    #: The word Sift knows this box by, or null for one it has never heard of.
    slug: str | None = None


class KeepPicture(Wire):
    """Keep a stash-box picture for a subject; the server reads its address from its own copy."""

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
    enabled: bool | None = None
    route: str | None = None
    requests_per_minute: int | None = Field(default=None, ge=1, le=6000)


class EnrichEntities(Wire):
    """Ask the stash-boxes about some people, sites or tags, by id: a name is not an identity."""

    subject: str = Field(min_length=1, max_length=32)
    ids: list[str] = Field(min_length=1, max_length=500)

    #: A box's own word, `all`, or empty for the setting; see `settings.box_for`.
    box: str = Field(default="", max_length=40)


class LinkedEntity(Wire):
    """One person, site or tag a stash-box has been agreed to know."""

    subject: str
    id: str
    #: What THIS library calls them.
    name: str
    box_id: str
    box: str
    #: What the box calls them.
    known_as: str
    fetched_at: int
    #: What that link filled in, as one line with each field and its value.
    said: list[HistoryPiece] = Field(default_factory=list)


class LinkedLedger(Wire):
    items: list[LinkedEntity]
    total: int = 0
    #: Files carrying an agreed answer, a different count from the subjects listed.
    files: int = 0
    #: Where this page begins; only the answer knows where a `from` row is.
    offset: int = 0


class UndecidedEntity(Wire):
    """A subject the unattended enrichment could not choose for."""

    subject: str
    id: str
    name: str
    candidates: int
    seen_at: int


class UndecidedList(Wire):
    items: list[UndecidedEntity]
    total: int
    #: Where this page begins; only the answer knows where a `from` row is.
    offset: int = 0


class StudioQuestionView(Wire):
    """A Site a stash-box made that may be one person's own store."""

    id: str
    name: str
    files: int
    scenes: int
    credited: int
    others: int
    site: str
    handle: str
    #: Whether that is a store page of hers (Clips4Sale, ManyVids and the like).
    store: bool


class StudioQuestions(Wire):
    items: list[StudioQuestionView]
    total: int
    offset: int = 0


class StudioAnswered(Wire):
    receipt_id: str
    said: str
    pieces: list[HistoryPiece] = Field(default_factory=list)


class EnrichStarted(Wire):
    job_id: str
    #: How many of the ids given were real, visible subjects.
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
    #: The shipped pack's word for this site, so a known logo is drawn instead of a proxied picture.
    icon_slug: str | None = None
    file_count: int | None = None
    #: False for the fuzzy half of the service's answer, drawn behind a press.
    every_word: bool = True
    fields: dict[str, Any] = Field(default={})
    #: What the box said that Sift has no field for, under the box's own key.
    extra: dict[str, Any] = Field(default={})
    confidence: float = 0.0


class BoxAnswer(Wire):
    """What one box said; a box that could not be asked answers with its own sentence."""

    box_id: str
    box_name: str
    records: list[RecordFound] = Field(default=[])
    fetched_at: int
    #: False when this came from the cache.
    fresh: bool
    problem: str | None = None


class LookUpResult(Wire):
    answers: list[BoxAnswer] = Field(default=[])


class LinkWrite(Wire):
    remote_id: str = Field(min_length=1, max_length=100)


class LinkView(Wire):
    box_id: str
    box_name: str
    #: The box's word, not its typed name, picks the mark's color.
    box_slug: str | None = None
    remote_id: str
    #: The box's own page for this entry, or null when Sift does not know its pages.
    page_url: str | None = None
    fetched_at: int
    record: RecordFound
    #: The fields this box gave whose value is still the one held.
    gave: list[str] = Field(default=[])


class LinkList(Wire):
    """Every stash-box agreed to know this subject, newest first, and who made it."""

    links: list[LinkView] = Field(default=[])
    made_by: MadeBy | None = None


class MatchView(Wire):
    asset_id: str
    box_id: str
    box_name: str
    remote_id: str
    grade: str
    state: str
    found_at: int
    decided_at: int | None = None
    record: RecordFound
    #: Empty when it would change nothing, which is a real answer.
    changes: list[FieldChange] = Field(default=[])
    #: The people, tags and sites this would have to create.
    creates: list[MissingView] = Field(default=[])
    #: The cache token for this file's still; null leaves the address bare and uncached.
    art: str | None = None


class MissingView(Wire):
    """One row an answer would have to create, and its kind."""

    name: str
    kind: str


class SettleField(Wire):
    """One conflict, answered by naming the side; the value is re-derived on the server."""

    asset_id: str = Field(min_length=1, max_length=100)
    box_id: str = Field(min_length=1, max_length=100)
    key: str = Field(min_length=1, max_length=80)
    #: `both` only for a field that holds more than one value; keeping yours sends nothing.
    take: Literal["theirs", "both"] = "theirs"


class MissingRef(Wire):
    """One ticked row, by kind and name, so a person and a tag of the same name stay apart."""

    name: str = Field(min_length=1, max_length=500)
    kind: str = Field(min_length=1, max_length=50)


class FieldChange(Wire):
    """One field a match would change; `conflict` when the two disagree and neither wins."""

    key: str
    outcome: str
    mine: Any = None
    theirs: Any = None
    #: The rows this names that do not exist yet; written only if ticked.
    needs: list[MissingView] = Field(default=[])
    #: False for a field that names only new rows.
    stands: bool = True
    # No "keep both" flag: a list field is never a conflict.


class MatchList(Wire):
    matches: list[MatchView] = Field(default=[])
    total: int = 0
    #: Files with an agreed answer in the whole library, so an empty pile can say "all N answered".
    answered: int = 0
    #: Where this page begins; only the answer knows where a `from` row is.
    offset: int = 0


class RecordHeld(Wire):
    """What Sift holds for one subject now, read through the subject's writer."""

    values: dict[str, Any] = Field(default_factory=dict)


class TakeFields(Wire):
    """Which of a linked box's fields to take onto the record; empty records that none was."""

    keys: list[str] = Field(default_factory=list, max_length=64)


class Taken(Wire):
    fields: int = 0


class ApplyMatches(Wire):
    """Yes, to these matches and to exactly these names being created; never remembered."""

    matches: list[MatchRef] = Field(default_factory=list)
    create: list[MissingRef] = Field(default_factory=list, max_length=2000)
    #: Anything not named keeps what is already there.
    settle: list[SettleField] = Field(default_factory=list, max_length=2000)


class MatchRef(Wire):
    asset_id: str = Field(min_length=1, max_length=100)
    box_id: str = Field(min_length=1, max_length=100)


class Applied(Wire):
    files: int = 0
    fields: int = 0
    created: int = 0
    decision_id: str = ""
    #: Answers left waiting because the file is kept local (see `apply_matches`).
    kept_local: int = 0


class Settled(Applied):
    """What settling one disagreement did, and the receipt's sentence."""

    said: str = ""
    pieces: list[HistoryPiece] = Field(default_factory=list)


class ScanWhat(Wire):
    """What a sweep should cover; absent means the whole library."""

    folder: str | None = None

    #: As `EnrichEntities.box`.
    box: str = Field(default="", max_length=40)

    #: True for Auto-enrich, which applies exact-hash matches immediately.
    auto: bool = False

    #: Ask again about files already answered; a difference comes back as a question.
    again: bool = False

    #: These files only; they win over a folder.
    assets: list[str] | None = None


class ScanStarted(Wire):
    """The sweep that was queued, and which named files were left out of it."""

    job_id: str
    started: bool = True
    asked: int = 0
    skipped: int = 0
    #: Why the first was left out, worded for one file.
    reason: str | None = None
    #: The same reason worded for more than one, as `kernel.reach.BulkWriteDone.reason`.
    reason_many: str | None = None
    #: A flag rather than matching on `reason`.
    vault_locked: bool = False
    #: "Do not enrich": a decision honoured, not an error.
    kept_local: bool = False


class DisagreementView(Wire):
    subject: Subject
    local_id: str
    name: str
    box_id: str
    box_name: str
    key: str
    mine: Any = None
    theirs: Any = None
    #: Both values in words (`records.value_said`); null where there are none.
    mine_said: str | None = None
    theirs_said: str | None = None


class DisagreementList(Wire):
    disagreements: list[DisagreementView] = Field(default=[])


class SettleDisagreement(Wire):
    """Which answer to keep for one box's row; keeping your own writes nothing."""

    subject: str = Field(min_length=1, max_length=40)
    local_id: str = Field(min_length=1, max_length=100)
    box_id: str = Field(min_length=1, max_length=100)
    key: str = Field(min_length=1, max_length=80)
    take_theirs: bool = False


class EnrichmentRunView(Wire):
    """The last time one stash-box enriched one thing, and whether anybody pressed it."""

    box_id: str
    box_name: str | None = None
    box_slug: str | None = None
    at: int
    automatic: bool = False


class EnrichmentState(Wire):
    """Where one file or record stands with enrichment from outside this machine."""

    subject: str
    id: str
    #: This row's own switch; Sift's own passes ignore it.
    kept_local: bool = False
    #: Whether anything would be sent, counting what the file is filed under.
    refused: bool = False
    why: str = ""
    #: Empty draws no "Last:" line.
    runs: list[EnrichmentRunView] = Field(default=[])


class KeepLocal(Wire):
    kept_local: bool
