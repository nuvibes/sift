# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asking the stash-boxes about the PEOPLE, Sites and tags in a library.

Between the Tagger (a pass over files) and Reconcile (what happens after a subject is linked) is
the first step: somebody looking up a person and saying "yes, that is them". Without a batch of it,
a library with four hundred people would have no way through them except four hundred pages.

Two things carry the weight here and neither is the happy path.

**The verb acts only where there is nothing to decide.** Exactly one entry, across every box asked,
matching every word of the name. Two is a judgement (which of the two people called Jane Doe is
this one), and a judgement belongs to whoever knows which one their files are of. Nought is a name
nobody has heard of. Those are DIFFERENT answers leading to opposite next moves, which is why the
outcome is named rather than a yes or no: as a `bool`, both common answers would be `False`, and a
Site with twenty candidates would decline correctly and say nothing at all. From the outside that
is a button that does not work.

**The queue is read through the SCOPED lists.** A count on a card is a disclosure in its own right,
and one taken from an unscoped query publishes the size of what somebody was kept out of.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from sift.kernel.access import Viewer
from sift.kernel.access.viewer import Role
from sift.kernel.enrichment import Decision, Missing, Plan
from sift.kernel.enrichment import Outcome as FieldOutcome
from sift.kernel.ledger import Actor, Object
from sift.kernel.records import FoundRecord, SourceAnswer, SourceLink, Subject
from sift.kernel.vocabulary import VIA_STASH
from sift.slices.stash_boxes.adapter import StashBoxUnreachable
from sift.slices.stash_boxes.entities import (
    EntityEnricher,
)
from sift.slices.stash_boxes.jobs import Outcome
from sift.slices.stash_boxes.service import KeptLocal
from sift.slices.stash_boxes.settings import invent_key

pytestmark = pytest.mark.anyio

VIEWER = Viewer(id="admin", role=Role.ADMIN)


@dataclass(frozen=True, slots=True)
class _Row:
    """A person, a Site or a tag, as the scoped lists hand one over."""

    id: str
    name: str
    asset_count: int = 0
    cover_asset_id: str | None = None
    cover_upload_id: str | None = None


@dataclass(frozen=True, slots=True)
class _Page:
    items: list[_Row]


class _Access:
    """The scoped lists, stood in for. It remembers what it was asked for and by whom."""

    def __init__(
        self,
        people: list[_Row] | None = None,
        sites: list[_Row] | None = None,
        tags: list[_Row] | None = None,
    ) -> None:
        self._people = people or []
        self._sites = sites or []
        self._tags = tags or []
        self.asked: list[tuple[str, Viewer, int]] = []

    async def suggest_people(self, viewer: Viewer, *, limit: int) -> _Page:
        self.asked.append(("people", viewer, limit))
        return _Page(self._people)

    async def list_sites(self, viewer: Viewer, *, limit: int) -> _Page:
        self.asked.append(("sites", viewer, limit))
        return _Page(self._sites)

    async def list_tags(self, viewer: Viewer, *, limit: int) -> _Page:
        self.asked.append(("tags", viewer, limit))
        return _Page(self._tags)


def _found(name: str = "Jane", *, every_word: bool = True, box: str = "b1") -> FoundRecord:
    return FoundRecord(
        source_id=box,
        remote_id=f"r-{name}",
        subject=Subject.PERSON,
        name=name,
        fields={"name": name},
        every_word=every_word,
    )


def _answer(*records: FoundRecord, problem: str | None = None) -> SourceAnswer:
    return SourceAnswer(
        source_id="b1",
        source_name="StashDB",
        records=list(records),
        fetched_at=0,
        fresh=True,
        problem=problem,
    )


class _Service:
    """The stash-box service, stood in for: what it was asked, and what it linked."""

    def __init__(
        self,
        answers: list[SourceAnswer] | None = None,
        linked: SourceLink | None = None,
        links: dict[str, list[SourceLink]] | None = None,
        boxes: list[object] | None = None,
        picture: tuple[bytes, str] | None = None,
    ) -> None:
        self._answers = answers or []
        self.asked_about: list[tuple[str | None, str | None]] = []
        self.runs: list[tuple[Subject, str, str, bool, tuple[str, ...] | None]] = []
        self._linked = linked
        self._links = links or {}
        self._boxes = boxes if boxes is not None else [object()]
        self._picture = picture
        self.searched: list[tuple[str, Subject]] = []
        self.linkings: list[tuple[Subject, str, str, str]] = []
        self.fetched: list[tuple[str, str]] = []
        self.undecided: list[tuple[Subject, str, int]] = []

    async def search(
        self,
        term: str,
        master_key: bytes | None,
        *,
        subject: Subject = Subject.PERSON,
        about: str | None = None,
        only: str | None = None,
    ) -> list[SourceAnswer]:
        _ = master_key
        # WHAT IT WAS ASKED ABOUT, kept beside the term rather than dropped. `about` is what makes
        # the real service refuse a record kept local, and a double that swallowed it would let the
        # caller stop passing it with every test still green.
        self.asked_about.append((about, only))
        self.searched.append((term, subject))
        return self._answers

    async def link(
        self,
        subject: Subject,
        local_id: str,
        box_id: str,
        remote_id: str,
        master_key: bytes | None,
    ) -> SourceLink | None:
        _ = master_key
        self.linkings.append((subject, local_id, box_id, remote_id))
        return self._linked

    async def record_enrichment(
        self,
        subject: Subject,
        local_id: str,
        box_id: str,
        *,
        automatic: bool,
        applied: Sequence[str] | None = None,
    ) -> None:
        """What the real one writes down after a link: when, against which box, by whom, and
        which FIELDS the ask actually filled in, which is what the History line is made of.

        `applied` is kept as a tuple beside the rest so a test can read it back. None and the empty
        tuple are different answers all the way to the sentence and this must not fold them: None
        is a box that linked and planned nothing, `()` is a plan that ran and found nothing to
        fill. See `sentences.box_line`.
        """
        self.runs.append(
            (subject, local_id, box_id, automatic, None if applied is None else tuple(applied))
        )

    async def kept_local(self, subject: Subject, local_id: str) -> bool:
        """Nothing in these tests is kept local; the refusal is the service's own to test."""
        _ = (subject, local_id)
        return False

    async def links_of(self, subject: Subject, local_id: str) -> list[SourceLink]:
        return self._links.get(f"{subject.value}:{local_id}", [])

    async def picture(
        self, box_id: str, url: str, master_key: bytes | None, *, vector: bool = False
    ) -> tuple[bytes, str] | None:
        """The real one answers None for a picture that will not come, and so does this.

        None rather than raising, because that is the real service's answer for an unreachable box,
        a refused host and a body that is not a picture, and a double that raised instead would
        make the caller look robust against a case it has never met.
        """
        _ = master_key
        # Every fetch here is a COVER fetch, so every one may take a vector logo.
        assert vector
        self.fetched.append((box_id, url))
        return self._picture

    async def boxes(self) -> list[object]:
        return self._boxes

    async def note_undecided(self, subject: Subject, local_id: str, candidates: int) -> None:
        self.undecided.append((subject, local_id, candidates))


def _link(fields: dict[str, object] | None = None) -> SourceLink:
    return SourceLink(
        source_id="b1",
        source_name="StashDB",
        remote_id="r-Jane",
        record=FoundRecord(
            source_id="b1",
            remote_id="r-Jane",
            subject=Subject.PERSON,
            name="Jane",
            fields=fields or {"name": "Jane"},
        ),
        fetched_at=0,
    )


class _Enricher:
    """The planner, stood in for. It never decides anything here; it is asked and it answers."""

    def __init__(self, plan: Plan | None = None, missing: tuple[Missing, ...] = ()) -> None:
        self._plan = plan
        self._missing = missing
        self.applied: list[tuple[Plan, object]] = []

    async def plan_for(self, **asked: object) -> Plan | None:
        self.asked = asked
        return self._plan

    async def apply(self, decided: Plan, *, creating: object = False) -> tuple[str, ...]:
        self.applied.append((decided, creating))
        return tuple(decided.writes)

    async def missing_for(self, decided: Plan) -> tuple[Missing, ...]:
        _ = decided
        return self._missing


class _Settings:
    """The preferences, stood in for. Anything not named answers None, which every switch here
    reads as off, so a test says only what it is actually about."""

    def __init__(self, held: dict[str, object] | None = None) -> None:
        self._held = held or {}

    async def get_app(self, key: str) -> object:
        return self._held.get(key)


class _Covers:
    """The subject covers, remembering what landed and which subjects already had one.

    `filled` is what the unattended verb put on; `replaced` what the pressing verb did. `already`
    stands for a subject with a cover of either kind, which the real store answers `has_one` for
    and refuses to fill over, and so does this.
    """

    def __init__(self, already: set[tuple[Subject, str]] | None = None) -> None:
        self.filled: dict[tuple[Subject, str], bytes] = {}
        self.replaced: dict[tuple[Subject, str], bytes] = {}
        #: Who each write was said to be by and which box it names: what its event records.
        self.said: dict[tuple[Subject, str], tuple[Actor, Object | None]] = {}
        self._already = already or set()

    async def has_one(self, subject: Subject, local_id: str) -> bool:
        return (subject, local_id) in self._already or (subject, local_id) in self.filled

    async def fill(
        self, subject: Subject, local_id: str, blob: bytes, *, actor: Actor, box: Object | None
    ) -> bool:
        if await self.has_one(subject, local_id):
            return False
        self.filled[(subject, local_id)] = blob
        self.said[(subject, local_id)] = (actor, box)
        return True

    async def keep(
        self, subject: Subject, local_id: str, blob: bytes, *, actor: Actor, box: Object | None
    ) -> bool:
        self.replaced[(subject, local_id)] = blob
        self.said[(subject, local_id)] = (actor, box)
        return True


class _Naming:
    """The naming seam, stood in for: every name is a row called `<kind>:<name>`, and it remembers
    which rows it was told a box made."""

    def __init__(self) -> None:
        self.marked: list[tuple[str, str, str]] = []

    async def person_named(self, name: str, *, creating: bool) -> str | None:
        _ = creating
        return f"person:{name}"

    async def site_named(
        self, name: str, *, creating: bool, address: str | None = None
    ) -> str | None:
        _ = (creating, address)
        return f"site:{name}"

    async def tag_named(self, name: str, *, creating: bool) -> str | None:
        _ = creating
        return f"tag:{name}"

    async def mark_created_by_box(self, kind: str, local_id: str, source_id: str) -> None:
        self.marked.append((kind, local_id, source_id))


def _enricher(
    *,
    access: _Access | None = None,
    service: _Service | None = None,
    planner: _Enricher | None = None,
    settings: _Settings | None = None,
    covers: _Covers | None = None,
    naming: _Naming | None = None,
) -> EntityEnricher:
    return EntityEnricher(
        service or _Service(),  # type: ignore[arg-type]
        access or _Access(),  # type: ignore[arg-type]
        planner or _Enricher(),  # type: ignore[arg-type]
        settings or _Settings(),  # type: ignore[arg-type]
        covers or _Covers(),  # type: ignore[arg-type]
        naming=naming or _Naming(),  # type: ignore[arg-type]
    )


# --- asking about one subject ----------------------------------------------------------------


async def test_one_match_across_every_box_is_linked_and_written() -> None:
    plan = Plan(subject=Subject.PERSON, local_id="p1", source_id="b1", decisions=())
    service = _Service(answers=[_answer(_found())], linked=_link())
    planner = _Enricher(plan=plan)

    outcome = await _enricher(service=service, planner=planner).enrich(
        Subject.PERSON, "p1", "Jane", None
    )

    assert service.linkings == [(Subject.PERSON, "p1", "b1", "r-Jane")]
    # No decisions means no writes, which is LINKED rather than WROTE. See the test below.
    assert outcome is Outcome.LINKED


async def test_two_matches_are_a_judgement_and_nothing_is_written() -> None:
    # It belongs to the person who knows which one their files are of. AMBIGUOUS rather than
    # UNKNOWN, because "there are twenty of these" and "nobody has heard of this" lead to
    # opposite next moves.
    service = _Service(answers=[_answer(_found("Jane"), _found("Jane Doe"))])

    outcome = await _enricher(service=service).enrich(Subject.PERSON, "p1", "Jane", None)

    assert outcome is Outcome.AMBIGUOUS
    assert service.linkings == []
    # And written down, so the person has a list to choose from rather than a number in a note.
    assert service.undecided == [(Subject.PERSON, "p1", 2)]


async def test_a_name_two_boxes_each_know_once_is_linked_to_both() -> None:
    """A second box that also knows them is a second LINK, not a second candidate. Counted across
    every box, a performer three boxes knew would be declined as "three candidates", and the
    people a confirmation had invented would sit unlinked with every box agreeing who they were."""
    plan = Plan(subject=Subject.PERSON, local_id="p1", source_id="b1", decisions=())
    service = _Service(answers=[_answer(_found()), _answer(_found(box="b2"))], linked=_link())

    outcome = await _enricher(service=service, planner=_Enricher(plan=plan)).enrich(
        Subject.PERSON, "p1", "Jane", None
    )

    assert service.linkings == [
        (Subject.PERSON, "p1", "b1", "r-Jane"),
        (Subject.PERSON, "p1", "b2", "r-Jane"),
    ]
    assert outcome is Outcome.LINKED


async def test_a_name_nobody_has_is_unknown_rather_than_ambiguous() -> None:
    service = _Service(answers=[_answer()])

    outcome = await _enricher(service=service).enrich(Subject.PERSON, "p1", "Jane", None)

    assert outcome is Outcome.UNKNOWN


async def test_a_fuzzy_match_is_not_a_match() -> None:
    # A stash-box's search completes a word somebody is typing, so a two-word name comes back with
    # everything that matches EITHER word. Acting on one of those files somebody else's record.
    service = _Service(answers=[_answer(_found("Jane Somebody", every_word=False))])

    outcome = await _enricher(service=service).enrich(Subject.PERSON, "p1", "Jane Doe", None)

    assert outcome is Outcome.UNKNOWN
    assert service.linkings == []


async def test_a_box_that_could_not_be_asked_contributes_nothing_and_stops_nothing() -> None:
    # One source failing never hides what the others said, and a match found by a box that answered
    # is still exactly one match.
    service = _Service(
        answers=[_answer(problem="that box is switched off"), _answer(_found())],
        linked=_link(),
    )

    outcome = await _enricher(service=service).enrich(Subject.PERSON, "p1", "Jane", None)

    assert outcome is not Outcome.UNKNOWN
    assert service.linkings == [(Subject.PERSON, "p1", "b1", "r-Jane")]


async def test_a_match_from_a_box_that_reported_a_problem_is_not_counted() -> None:
    # `problem` means the box could not be asked at all. Anything in `records` beside one is stale,
    # and counting it would turn a source being down into a match nobody made.
    # Nor is it "nobody has heard of this": the box never said so.
    service = _Service(answers=[_answer(_found(), problem="refused")])

    outcome = await _enricher(service=service).enrich(Subject.PERSON, "p1", "Jane", None)

    assert outcome is Outcome.FAILED
    assert service.linkings == []


async def test_a_link_that_could_not_be_kept_is_unknown_rather_than_a_silent_success() -> None:
    service = _Service(answers=[_answer(_found())], linked=None)

    outcome = await _enricher(service=service).enrich(Subject.PERSON, "p1", "Jane", None)

    assert outcome is Outcome.UNKNOWN


async def test_nothing_to_fill_in_is_still_a_link_worth_keeping() -> None:
    # The link is what turns a future disagreement into a Reconcile row instead of a silent
    # difference, so it is the point even when the box agrees with everything already here.
    service = _Service(answers=[_answer(_found())], linked=_link())
    planner = _Enricher(plan=None)

    outcome = await _enricher(service=service, planner=planner).enrich(
        Subject.PERSON, "p1", "Jane", None
    )

    assert outcome is Outcome.LINKED
    assert planner.applied == []


async def test_a_plan_with_writes_is_applied_and_creates_nothing() -> None:
    # `creating=False` is the whole of what makes an unattended pass acceptable: it fills fields in
    # and never invents a person, a Site or a tag off the back of somebody else's vocabulary.
    plan = Plan(
        subject=Subject.PERSON,
        local_id="p1",
        source_id="b1",
        decisions=(
            Decision(
                key="name", outcome=FieldOutcome.WRITE, mine=None, theirs="Jane", value="Jane"
            ),
        ),
    )
    service = _Service(answers=[_answer(_found())], linked=_link())
    planner = _Enricher(plan=plan)

    outcome = await _enricher(service=service, planner=planner).enrich(
        Subject.PERSON, "p1", "Jane", None
    )

    assert outcome is Outcome.WROTE
    assert planner.applied == [(plan, frozenset())]


async def test_a_run_nobody_is_watching_invents_only_the_kinds_that_were_switched_on() -> None:
    """The switch decides what Auto-enrich may create, per kind, and off is the default.

    Asserted on what `apply` is HANDED rather than on what a log line said: the permission is the
    thing that decides whether a row appears in somebody's library, and a test reading the log would
    pass with the permission left at nothing.

    Three names of two kinds, with only people turned on, because one kind on and one off is the
    case the whole shape exists for: a run that invents every person a box names and none of its
    tag vocabulary. A single kind proves nothing about the second.
    """
    plan = Plan(
        subject=Subject.PERSON,
        local_id="p1",
        source_id="b1",
        decisions=(
            Decision(
                key="name", outcome=FieldOutcome.WRITE, mine=None, theirs="Jane", value="Jane"
            ),
        ),
    )
    wanted = (
        Missing(name="Jane Doe", kind=Subject.PERSON.value),
        Missing(name="beach", kind=Subject.TAG.value),
        Missing(name="Northlight", kind=Subject.SITE.value),
    )
    service = _Service(answers=[_answer(_found())], linked=_link())
    planner = _Enricher(plan=plan, missing=wanted)

    await _enricher(
        service=service,
        planner=planner,
        settings=_Settings({invent_key(Subject.PERSON): True}),
    ).enrich(Subject.PERSON, "p1", "Jane", None)

    assert planner.applied == [(plan, frozenset({(Subject.PERSON.value, "Jane Doe")}))]


async def test_nothing_is_invented_by_a_run_nobody_is_watching_unless_it_was_switched_on() -> None:
    """The default, said out loud. Every switch off means every name is declined, whatever a box
    offered, which is what stops Auto-enrich populating a library nobody asked it to."""
    plan = Plan(
        subject=Subject.PERSON,
        local_id="p1",
        source_id="b1",
        decisions=(
            Decision(
                key="name", outcome=FieldOutcome.WRITE, mine=None, theirs="Jane", value="Jane"
            ),
        ),
    )
    wanted = (Missing(name="Jane Doe", kind=Subject.PERSON.value),)
    service = _Service(answers=[_answer(_found())], linked=_link())
    planner = _Enricher(plan=plan, missing=wanted)

    await _enricher(service=service, planner=planner).enrich(Subject.PERSON, "p1", "Jane", None)

    assert planner.applied == [(plan, frozenset())]


# --- what the run wrote down about what it FILLED IN ---------------------------------------------
#
# The History line is made of this. `enrichment_runs.applied` is version 52 of the catalog, and
# these three tests are the three answers it can hold. See `sentences.box_line`, where the
# sentence for each is written.


def _plan_writing(*keys: str) -> Plan:
    """A plan that would write these fields. The values are not the point; the KEYS are."""
    return Plan(
        subject=Subject.PERSON,
        local_id="p1",
        source_id="b1",
        decisions=tuple(
            Decision(key=key, outcome=FieldOutcome.WRITE, mine=None, theirs="x", value="x")
            for key in keys
        ),
    )


async def test_a_run_writes_down_the_fields_it_actually_filled_in() -> None:
    """From `apply`'s own answer: what the WRITER says it wrote, never what the plan asked for.

    The two are not the same list in either direction, which is written up at `Writer.write`: a
    field with nothing behind it to write it, and a name this run had no permission to invent, are
    both asked for and neither lands. A count taken from the ask can only ever agree with the ask.
    """
    plan = _plan_writing("birth_date", "height")
    service = _Service(answers=[_answer(_found())], linked=_link())

    await _enricher(service=service, planner=_Enricher(plan=plan)).enrich(
        Subject.PERSON, "p1", "Jane", None
    )

    assert service.runs == [(Subject.PERSON, "p1", "b1", True, ("birth_date", "height"))]


async def test_a_run_that_found_nothing_to_fill_writes_down_the_empty_list() -> None:
    """A plan that RAN and decided nothing is a different fact from no plan at all.

    The empty list reads as "nothing new to fill in" and None reads as the bare link it has always
    been. Folding the two would leave somebody looking at a box that appears to have done nothing.
    """
    service = _Service(answers=[_answer(_found())], linked=_link())
    planner = _Enricher(plan=_plan_writing())

    await _enricher(service=service, planner=planner).enrich(Subject.PERSON, "p1", "Jane", None)

    assert service.runs == [(Subject.PERSON, "p1", "b1", True, ())]


async def test_a_subject_nothing_can_write_records_no_list_at_all() -> None:
    """`plan_for` answering None is a subject no writer is registered for.

    It decided nothing because it was never asked, which is not the same as deciding there was
    nothing to do, so the row says nothing rather than saying "nothing was filled in".
    """
    service = _Service(answers=[_answer(_found())], linked=_link())

    await _enricher(service=service, planner=_Enricher(plan=None)).enrich(
        Subject.PERSON, "p1", "Jane", None
    )

    assert service.runs == [(Subject.PERSON, "p1", "b1", True, None)]


async def test_the_name_is_asked_of_the_right_half_of_the_service() -> None:
    # A Site looked up among people comes back empty from a box that has it, and an empty
    # answer is also what "genuinely not there" looks like.
    service = _Service(answers=[_answer()])

    await _enricher(service=service).enrich(Subject.SITE, "s1", "Northlight", None)

    assert service.searched == [("Northlight", Subject.SITE)]


# --- the picture a link brings with it ---------------------------------------------------------


def _link_with_a_picture(url: str | None = "https://box.example/jane.jpg") -> SourceLink:
    held = _link()
    return SourceLink(
        source_id=held.source_id,
        source_name=held.source_name,
        remote_id=held.remote_id,
        record=FoundRecord(
            source_id="b1",
            remote_id="r-Jane",
            subject=Subject.PERSON,
            name="Jane",
            fields={"name": "Jane"},
            image_url=url,
        ),
        fetched_at=0,
    )


async def test_a_link_brings_the_picture_with_it_as_the_subjects_cover() -> None:
    # A person linked by an unattended run gets the photograph from the record just kept, as their
    # cover: the one column every wall reads.
    service = _Service(
        answers=[_answer(_found())],
        linked=_link_with_a_picture(),
        picture=(b"\x89PNG\r\n\x1a\n", "image/png"),
    )
    covers = _Covers()

    await _enricher(service=service, covers=covers).enrich(Subject.PERSON, "p1", "Jane Doe", None)

    # Fetched through the BOX, so a box behind a tunnel stays behind it and a key is carried.
    assert service.fetched == [("b1", "https://box.example/jane.jpg")]
    # Put on the ROW this library keeps for them, by id, never by the box's spelling of the name.
    assert covers.filled == {(Subject.PERSON, "p1"): b"\x89PNG\r\n\x1a\n"}
    assert covers.replaced == {}
    # Sift's act, by the pass that fetches one, and the BOX named as where the picture came from:
    # the line reads "Cover set to StashDB's picture", not "a new picture".
    assert covers.said[(Subject.PERSON, "p1")] == (
        Actor.sift(VIA_STASH),
        Object(kind="box", id="b1", name="StashDB"),
    )


async def test_a_subject_that_already_has_a_cover_keeps_it_and_is_not_fetched_for() -> None:
    # `fill` and never `keep`: this runs behind somebody's back, and a still they chose from a clip
    # must not be replaced by whatever a box happens to hold. Nor is the box asked: a picture
    # that would be declined is a request to somebody else's server for nothing.
    service = _Service(
        answers=[_answer(_found())],
        linked=_link_with_a_picture(),
        picture=(b"\x89PNG\r\n\x1a\n", "image/png"),
    )
    covers = _Covers(already={(Subject.PERSON, "p1")})

    await _enricher(service=service, covers=covers).enrich(Subject.PERSON, "p1", "Jane Doe", None)

    assert service.fetched == []
    assert covers.filled == {}
    assert covers.replaced == {}


async def test_an_entry_with_no_picture_is_not_fetched_from_at_all() -> None:
    service = _Service(answers=[_answer(_found())], linked=_link_with_a_picture(url=None))
    covers = _Covers()

    outcome = await _enricher(service=service, covers=covers).enrich(
        Subject.PERSON, "p1", "Jane Doe", None
    )

    assert service.fetched == []
    assert covers.filled == {}
    # And the link still happened. A box with no picture is an ordinary box.
    assert outcome is Outcome.LINKED


async def test_a_cover_that_landed_between_the_two_reads_is_not_overwritten() -> None:
    """`fill` is the AUTHORITY and the check before the fetch is only an optimisation.

    The subject is asked whether it has a cover before the box is called at all, because a picture
    that would be declined is a request to somebody else's server for nothing. That answer can be
    stale by the time the bytes arrive: this runs behind somebody's back, and a person choosing a
    still from a clip in the meantime is exactly the case. So `fill` refuses, and the refusal is
    taken: the link stands and the cover they chose is left alone.

    Without this the second read is a line nothing runs, and "never over one that is there" would
    rest entirely on a check made before the window it has to hold across.
    """

    class _FilledSinceWeLooked(_Covers):
        async def fill(
            self, subject: Subject, local_id: str, blob: bytes, *, actor: Actor, box: Object | None
        ) -> bool:
            _ = (subject, local_id, blob, actor, box)
            return False

    service = _Service(
        answers=[_answer(_found())],
        linked=_link_with_a_picture(),
        picture=(b"\x89PNG\r\n\x1a\n", "image/png"),
    )
    covers = _FilledSinceWeLooked()

    outcome = await _enricher(service=service, covers=covers).enrich(
        Subject.PERSON, "p1", "Jane Doe", None
    )

    # It was fetched (the stale answer said there was no cover) and then not written.
    assert service.fetched == [("b1", "https://box.example/jane.jpg")]
    assert covers.filled == {}
    assert covers.replaced == {}
    assert outcome is Outcome.LINKED


async def test_a_picture_that_will_not_come_does_not_stop_the_link() -> None:
    # The box answers None for an unreachable service, a refused host and a body that is not a
    # picture. None of those is a reason to throw away a link that was made.
    service = _Service(answers=[_answer(_found())], linked=_link_with_a_picture(), picture=None)
    covers = _Covers()

    outcome = await _enricher(service=service, covers=covers).enrich(
        Subject.PERSON, "p1", "Jane Doe", None
    )

    assert service.fetched == [("b1", "https://box.example/jane.jpg")]
    assert covers.filled == {}
    assert outcome is Outcome.LINKED


# --- linking a row by the id the box gave for it ------------------------------------------------


async def test_a_row_linked_by_the_boxs_own_id_brings_its_cover_and_its_fields() -> None:
    """What a person a confirmed match created gets: the link by the id the answer carried,
    and then everything a link found by name brings: the picture into the blank, and the fields
    planned from the record just kept. No search: the id is the box's own statement of who it is."""
    plan = Plan(
        subject=Subject.PERSON,
        local_id="p1",
        source_id="b1",
        decisions=(Decision(key="birthdate", outcome=FieldOutcome.WRITE, value="1990-01-01"),),
    )
    service = _Service(linked=_link_with_a_picture(), picture=(b"\x89PNG\r\n\x1a\n", "image/png"))
    planner = _Enricher(plan=plan)
    covers = _Covers()

    outcome = await _enricher(service=service, planner=planner, covers=covers).link_known(
        Subject.PERSON, "p1", "b1", "r-Jane", None
    )

    assert service.searched == [], "the id is known; the name is not asked about"
    assert service.linkings == [(Subject.PERSON, "p1", "b1", "r-Jane")]
    assert covers.filled == {(Subject.PERSON, "p1"): b"\x89PNG\r\n\x1a\n"}
    assert outcome is Outcome.WROTE
    assert service.runs == [(Subject.PERSON, "p1", "b1", True, ("birthdate",))]


async def test_an_id_the_box_no_longer_knows_links_nothing_and_fetches_nothing() -> None:
    service = _Service(linked=None, picture=(b"x", "image/png"))
    covers = _Covers()

    outcome = await _enricher(service=service, covers=covers).link_known(
        Subject.PERSON, "p1", "b1", "r-gone", None
    )

    assert outcome is Outcome.UNKNOWN
    assert service.fetched == [] and covers.filled == {}


async def test_a_row_kept_local_is_not_linked_by_an_id_either() -> None:
    """The service's door refuses a record kept local whichever way the link was found, and the
    answer is that decision rather than a failure."""

    class _Refusing(_Service):
        async def link(self, *args: object, **kwargs: object) -> SourceLink | None:
            raise KeptLocal()

    covers = _Covers()
    outcome = await _enricher(service=_Refusing(), covers=covers).link_known(
        Subject.PERSON, "p1", "b1", "r-Jane", None
    )

    assert outcome is Outcome.KEPT_LOCAL
    assert covers.filled == {}


# --- a subject kept local, and a box that goes while it is being linked -----------------------------


async def test_a_subject_kept_local_is_not_searched_for_and_says_so() -> None:
    """The door refuses a name read off a row kept local; the outcome is named rather than being
    the green "nothing happened" of a record left unchanged."""

    class _KeptLocal(_Service):
        async def search(self, *args: object, **kwargs: object) -> list[SourceAnswer]:
            raise KeptLocal()

    service = _KeptLocal(linked=_link())

    outcome = await _enricher(service=service).enrich(Subject.PERSON, "p1", "Jane", None)

    assert outcome is Outcome.KEPT_LOCAL
    assert service.linkings == []


async def test_a_box_that_goes_while_it_is_linked_costs_that_link_and_not_the_others() -> None:
    """Two boxes each know the name once; the first cannot be asked by the time the link is made.
    The second is still linked, so the run is not the nothing-found of an unknown name."""

    class _FirstBoxGone(_Service):
        async def link(
            self,
            subject: Subject,
            local_id: str,
            box_id: str,
            remote_id: str,
            master_key: bytes | None,
        ) -> SourceLink | None:
            linked = await super().link(subject, local_id, box_id, remote_id, master_key)
            if box_id == "b1":
                raise StashBoxUnreachable("StashDB could not be asked.")
            return linked

    service = _FirstBoxGone(
        answers=[_answer(_found(box="b1")), _answer(_found(box="b2"))], linked=_link()
    )

    outcome = await _enricher(service=service).enrich(Subject.PERSON, "p1", "Jane", None)

    assert [box for _subject, _local, box, _remote in service.linkings] == ["b1", "b2"]
    assert outcome is not Outcome.UNKNOWN


# --- a Site's parent, and a studio called exactly its name ---------------------------------------


def _studio(name: str, *, parent: str | None = None, parent_id: str | None = None) -> FoundRecord:
    """A box's studio entry, with the id it gave for the parent where it gave one."""
    return FoundRecord(
        source_id="b1",
        remote_id=f"r-{name}",
        subject=Subject.SITE,
        name=name,
        fields={"name": name, **({"parent": parent} if parent else {})},
        refs={Subject.SITE.value: {parent: parent_id}} if parent and parent_id else {},
    )


class _Once(_Enricher):
    """A planner whose missing names are missing only until the first write, as real rows are."""

    async def missing_for(self, decided: Plan) -> tuple[Missing, ...]:
        _ = decided
        return () if self.applied else self._missing


async def test_a_parent_network_a_studio_names_is_the_boxs_and_is_linked_by_its_id() -> None:
    """Filling a studio's record invents its network. The network is recorded as the box's (not as
    somebody typing it) and linked to the box's own studio by id, so it gets that record and
    picture rather than a letter and "Created by somebody"."""
    plan = Plan(
        subject=Subject.SITE,
        local_id="site:Northlight",
        source_id="b1",
        decisions=(
            Decision(
                key="parent",
                outcome=FieldOutcome.WRITE,
                mine=None,
                theirs="Harbour Network",
                value="Harbour Network",
            ),
        ),
    )
    studio = _studio("Northlight", parent="Harbour Network", parent_id="r-harbour")
    linked = SourceLink(
        source_id="b1",
        source_name="StashDB",
        remote_id=studio.remote_id,
        record=studio,
        fetched_at=0,
    )
    service = _Service(answers=[_answer(studio)], linked=linked)
    naming = _Naming()
    planner = _Once(plan=plan, missing=(Missing(name="Harbour Network", kind=Subject.SITE.value),))

    await _enricher(
        service=service,
        planner=planner,
        settings=_Settings({invent_key(Subject.SITE): True}),
        naming=naming,
    ).enrich(Subject.SITE, "site:Northlight", "Northlight", None)

    assert ("site", "site:Harbour Network", "b1") in naming.marked
    assert (Subject.SITE, "site:Harbour Network", "b1", "r-harbour") in service.linkings


async def test_a_parent_the_box_will_not_link_now_is_left_made_and_unlinked() -> None:
    """The link of an invented row is a request to somebody else's service: one that fails is a
    line in the log, and the Site's own write and its run stand."""
    from sift.slices.stash_boxes.adapter import StashBoxUnreachable

    plan = Plan(
        subject=Subject.SITE,
        local_id="site:Northlight",
        source_id="b1",
        decisions=(
            Decision(
                key="parent",
                outcome=FieldOutcome.WRITE,
                mine=None,
                theirs="Harbour Network",
                value="Harbour Network",
            ),
        ),
    )
    studio = _studio("Northlight", parent="Harbour Network", parent_id="r-harbour")
    linked = SourceLink(
        source_id="b1",
        source_name="StashDB",
        remote_id=studio.remote_id,
        record=studio,
        fetched_at=0,
    )

    class _ParentDown(_Service):
        async def link(self, subject: Subject, local_id: str, *args: Any, **kwargs: Any) -> Any:
            if local_id == "site:Harbour Network":
                raise StashBoxUnreachable("the box did not answer")
            return await super().link(subject, local_id, *args, **kwargs)

    service = _ParentDown(answers=[_answer(studio)], linked=linked)
    naming = _Naming()
    planner = _Once(plan=plan, missing=(Missing(name="Harbour Network", kind=Subject.SITE.value),))

    outcome = await _enricher(
        service=service,
        planner=planner,
        settings=_Settings({invent_key(Subject.SITE): True}),
        naming=naming,
    ).enrich(Subject.SITE, "site:Northlight", "Northlight", None)

    assert outcome is not Outcome.FAILED
    assert ("site", "site:Harbour Network", "b1") in naming.marked
    assert [one[1] for one in service.linkings] == ["site:Northlight"]


async def test_a_parent_nobody_allowed_to_be_invented_is_neither_marked_nor_linked() -> None:
    """With Sites switched off the network is not made, so there is nothing to claim or link."""
    plan = Plan(subject=Subject.SITE, local_id="site:Northlight", source_id="b1", decisions=())
    studio = _studio("Northlight", parent="Harbour Network", parent_id="r-harbour")
    linked = SourceLink(
        source_id="b1",
        source_name="StashDB",
        remote_id=studio.remote_id,
        record=studio,
        fetched_at=0,
    )
    service = _Service(answers=[_answer(studio)], linked=linked)
    naming = _Naming()
    planner = _Once(plan=plan, missing=(Missing(name="Harbour Network", kind=Subject.SITE.value),))

    await _enricher(service=service, planner=planner, naming=naming).enrich(
        Subject.SITE, "site:Northlight", "Northlight", None
    )

    assert naming.marked == []
    assert [one[1] for one in service.linkings] == ["site:Northlight"]


async def test_a_studio_called_exactly_the_sites_name_is_the_one_among_its_sub_studios() -> None:
    """A studio search completes words, so a network's name also brings back its sub-studios. The
    one entry called exactly that name is the studio; a person is never narrowed this way."""
    answers = [_answer(_studio("Northlight Media"), _studio("Northlight Media Group"))]
    service = _Service(answers=answers, linked=None)

    await _enricher(service=service).enrich(Subject.SITE, "s1", "northlight media", None)

    assert [one[3] for one in service.linkings] == ["r-Northlight Media"]

    people = _Service(answers=[_answer(_found("Jane"), _found("Jane Doe"))])
    outcome = await _enricher(service=people).enrich(Subject.PERSON, "p1", "Jane", None)
    assert outcome is Outcome.AMBIGUOUS
    assert people.linkings == []


async def test_a_box_that_refused_the_question_is_a_failure_and_not_an_unknown_name() -> None:
    """ "Nobody has heard of this" would close the question; a refusal must leave it open."""
    service = _Service(answers=[_answer(problem="StashDB refused the question")])

    outcome = await _enricher(service=service).enrich(Subject.SITE, "s1", "Harbor", None)

    assert outcome is Outcome.FAILED


# --- a Site the icon pack already draws is never asked for a picture ----------------------------


def _packed(quality: str) -> tuple[str, str]:
    """A name and a host of one entry the shipped pack holds at this quality.

    Taken from the manifest at run time, so no real site is named in a public test.
    """
    from sift.kernel.site_icons import _by_slug, every

    for icon in every():
        if icon.hosts and _by_slug()[icon.slug].quality == quality:
            return icon.name, sorted(icon.hosts)[0]
    pytest.skip(f"the pack ships no {quality} entry with a host in this tree")  # pragma: no cover


def _site_link(name: str, links: list[str]) -> SourceLink:
    return SourceLink(
        source_id="b1",
        source_name="StashDB",
        remote_id="r-site",
        record=FoundRecord(
            source_id="b1",
            remote_id="r-site",
            subject=Subject.SITE,
            name=name,
            fields={"name": name, "links": links},
            image_url="https://box.example/logo.png",
        ),
        fetched_at=0,
    )


async def _linked_site(name: str, links: list[str]) -> _Service:
    service = _Service(linked=_site_link(name, links), picture=(b"\x89PNG\r\n\x1a\n", "image/png"))
    await _enricher(service=service).link_known(Subject.SITE, "s1", "b1", "r-site", None)
    return service


async def test_a_site_the_pack_draws_well_by_name_is_not_asked_for_a_picture() -> None:
    """A Site with no cover falls through to the pack's logo, so the box's is a request for
    nothing: the box is never asked, and the log says why."""
    name, _ = _packed("high")

    assert (await _linked_site(name, [])).fetched == []


async def test_a_site_the_pack_draws_well_by_its_own_address_is_not_asked_either() -> None:
    _, host = _packed("high")

    assert (await _linked_site("Quillhouse", [f"https://{host}/"])).fetched == []


async def test_a_database_link_never_counts_as_the_studios_own_address() -> None:
    """A studio's page on an index names where it is written about, never the studio: a Site the
    pack knows only through such a link is still asked for its picture."""
    service = await _linked_site("Quillhouse", ["https://theporndb.net/", "https://stashdb.org/"])

    assert service.fetched == [("b1", "https://box.example/logo.png")]


async def test_a_site_the_pack_draws_only_small_still_gets_the_boxes_better_picture() -> None:
    name, _ = _packed("low")

    assert (await _linked_site(name, [])).fetched == [("b1", "https://box.example/logo.png")]


async def test_a_site_outside_the_pack_is_asked_as_before() -> None:
    service = await _linked_site("Quillhouse", ["https://quillhouse.invalid/"])

    assert service.fetched == [("b1", "https://box.example/logo.png")]


async def test_the_pack_is_asked_about_sites_only_never_a_person() -> None:
    """A person whose name is a site's is still a person: the pack holds no pictures of people."""
    name, _ = _packed("high")
    held = _link_with_a_picture()
    record = FoundRecord(
        source_id="b1",
        remote_id="r-Jane",
        subject=Subject.PERSON,
        name=name,
        fields={"name": name},
        image_url="https://box.example/jane.jpg",
    )
    service = _Service(
        answers=[_answer(_found(name))],
        linked=SourceLink(
            source_id=held.source_id,
            source_name=held.source_name,
            remote_id=held.remote_id,
            record=record,
            fetched_at=0,
        ),
        picture=(b"\x89PNG\r\n\x1a\n", "image/png"),
    )

    await _enricher(service=service).enrich(Subject.PERSON, "p1", name, None)

    assert service.fetched == [("b1", "https://box.example/jane.jpg")]
