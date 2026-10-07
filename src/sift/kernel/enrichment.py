# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deciding what a stash-box's answer is allowed to write, and writing it.

An external stash-box answers with a record: a name, some aliases, a birth date, a list of
addresses. Sift already holds some of that, sometimes differently. **What happens to each field is
a rule, and the rule is per field**, because the honest answer differs by field: another spelling of
somebody's name is always worth having, and a birth date that disagrees with the one already there
is a question rather than a correction.

Three rules, and they are the shape the same job takes in the software these stash-boxes were built
for, deliberately, because it is a shape people already understand:

- **Ignore.** Never write this field, whatever arrives.
- **Merge.** Add to a list; fill in a single value only where there is nothing there. The default.
- **Overwrite.** Replace what is there, when something real arrives to replace it with.

## The two rules that hold whatever the strategy says

**A blank never lands on a value.** A stash-box that does not know somebody's height sends nothing,
and nothing is not a correction, so an empty offer is dropped before any rule looks at it. Without
this, one thin entry on one stash-box would empty a record somebody typed by hand.

**Nothing is silently overwritten.** Under Merge, an offered value that disagrees with one already
there is a CONFLICT: recorded, shown, and not written. That is what the reconcile screen is a list
of. Overwrite is the strategy that says "yes, silently", and it says it once, in the open, in a
setting somebody chose.

## Why this is here rather than in a slice

Four different things need it: linking one subject by hand, the bulk pass over a whole library, the
screen that reconciles two answers, and the mapping of an address list into rows. They live in
different slices, and a slice may not import another slice.

So the kernel owns the DECISION and owns nothing else. Each area registers a writer for its own
subject: the same arrangement the workbench uses for queues, and for the same reason: this module
has no way to know what a person is made of, and it must not learn.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from sift.kernel.ledger import Actor
from sift.kernel.records import Kind, Subject, field, fields_of


class Strategy(StrEnum):
    """What may happen to one field when a stash-box offers a value for it."""

    #: Never write it. What somebody has is what they keep.
    IGNORE = "ignore"
    #: Add to a list; fill a blank. A disagreement is a conflict rather than a write.
    MERGE = "merge"
    #: Replace what is there. The only strategy that overwrites, and it says so.
    OVERWRITE = "overwrite"


#: What a field does when nobody has said. Merge, because it is the one that cannot lose anything:
#: it fills in what is missing and asks about what disagrees.
DEFAULT_STRATEGY = Strategy.MERGE

#: The field kinds that hold SEVERAL values, where Merge means union rather than fill-a-blank.
#:
#: Read from the record registry's own kinds rather than from a list of field keys beside it. A
#: field added later is a list or is not by what it was declared as, and a second list saying which
#: is which would be wrong the first time somebody adds one.
LIST_KINDS = frozenset({Kind.NAMES, Kind.LINKS, Kind.TAGS, Kind.ACCOUNTS})


class Outcome(StrEnum):
    """What the rule decided for one field."""

    #: There is something new to write.
    WRITE = "write"
    #: Nothing to do: no offer, the same value already there, or the field is switched off.
    KEEP = "keep"
    #: Two values that disagree, and neither wins here. This is what reconcile is a list of.
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class Decision:
    """What would happen to one field, before anything happens to it.

    Separate from the writing on purpose. A confirm screen has to be able to SHOW what a press would
    do (field by field, with the value that is there beside the value being offered) and a
    screen that had to perform the write to find out is a screen that cannot ask first.
    """

    key: str
    outcome: Outcome
    #: What is there now. `None` when nothing is.
    mine: object = None
    #: What the stash-box offered.
    theirs: object = None
    #: What would actually be written. Only meaningful when the outcome is `WRITE`.
    value: object = None


@dataclass(frozen=True, slots=True)
class Plan:
    """Every field of one subject, decided. Nothing here has been written."""

    subject: Subject
    local_id: str
    source_id: str
    decisions: tuple[Decision, ...] = ()

    @property
    def writes(self) -> dict[str, object]:
        """The fields that would change, as the writer takes them."""
        return {one.key: one.value for one in self.decisions if one.outcome is Outcome.WRITE}

    @property
    def conflicts(self) -> tuple[Decision, ...]:
        """The fields where two answers disagree and neither was taken."""
        return tuple(one for one in self.decisions if one.outcome is Outcome.CONFLICT)


def _empty(value: object) -> bool:
    """Whether an offer or a held value counts as nothing at all.

    Zero is NOT nothing, and that is the whole reason this is a function. A career that started in
    year zero is not a career start year, but a rating of 0 and a count of 0 are real values, and a
    truthiness test would drop both. So emptiness is about the SHAPE: absent, a blank string, or an
    empty list.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    return False


def _as_list(value: object) -> list[object]:
    """A list field's value, however it arrived. A bare string is one entry, not a pile of letters."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [one for one in value if not _empty(one)]
    return [value]


def _key_of(one: object) -> object:
    """What makes two entries in a list the same entry.

    Case-folded for a string, because `Jane` and `jane` are one alias and keeping both is how a
    chooser fills up with the same name twice. Anything else is compared as it is: a mapping is
    unhashable, so it falls back to its own text, which is enough to spot an exact repeat.
    """
    if isinstance(one, str):
        return one.strip().casefold()
    if isinstance(one, Mapping):
        return tuple(sorted((str(k), str(v)) for k, v in one.items()))
    return one


def _same(mine: object, theirs: object) -> bool:
    """Whether two single values say the same thing. Text is compared without case or padding."""
    if isinstance(mine, str) and isinstance(theirs, str):
        return mine.strip().casefold() == theirs.strip().casefold()
    return bool(mine == theirs)


def keep_both(mine: object, theirs: object) -> list[object]:
    """What is there, then what is new. Order is kept because a record is read in it.

    Published rather than private because somebody answering a conflict by hand asks for exactly
    this, and a second implementation of "both" would be a second answer to what both means.
    """
    held = _as_list(mine)
    seen = {_key_of(one) for one in held}
    out = list(held)
    for one in _as_list(theirs):
        if _key_of(one) not in seen:
            seen.add(_key_of(one))
            out.append(one)
    return out


def decide(
    *,
    key: str,
    mine: object,
    theirs: object,
    strategy: Strategy,
    is_list: bool,
) -> Decision:
    """One field, decided. Pure: it reads nothing and writes nothing.

    Every branch here is a rule somebody can be shown, which is why it is a function of its
    arguments and not a method on something holding a database.
    """
    if strategy is Strategy.IGNORE or _empty(theirs):
        # A field nobody wants, or a stash-box that does not know. Neither is a correction.
        return Decision(key=key, outcome=Outcome.KEEP, mine=mine, theirs=theirs)

    if is_list:
        # A list never conflicts. Two stash-boxes each knowing a different spelling of somebody's
        # name is not a disagreement: it is two spellings, and both of them find her.
        merged = keep_both(mine, theirs) if strategy is Strategy.MERGE else _as_list(theirs)
        if [_key_of(one) for one in merged] == [_key_of(one) for one in _as_list(mine)]:
            return Decision(key=key, outcome=Outcome.KEEP, mine=mine, theirs=theirs)
        return Decision(key=key, outcome=Outcome.WRITE, mine=mine, theirs=theirs, value=merged)

    if _empty(mine):
        return Decision(key=key, outcome=Outcome.WRITE, mine=mine, theirs=theirs, value=theirs)
    if _same(mine, theirs):
        return Decision(key=key, outcome=Outcome.KEEP, mine=mine, theirs=theirs)
    if strategy is Strategy.OVERWRITE:
        return Decision(key=key, outcome=Outcome.WRITE, mine=mine, theirs=theirs, value=theirs)
    # Merge, on a single value that is already filled in and disagrees. The one case where the
    # answer is a question. See the module docstring: nothing is silently overwritten.
    return Decision(key=key, outcome=Outcome.CONFLICT, mine=mine, theirs=theirs)


def plan(
    *,
    subject: Subject,
    local_id: str,
    source_id: str,
    mine: Mapping[str, object],
    theirs: Mapping[str, object],
    strategies: Mapping[str, Strategy],
) -> Plan:
    """What one stash-box's record would do to one subject, field by field.

    Walks the RECORD REGISTRY rather than the offered keys, so the answer covers every field the
    subject has and reads in the order the record is drawn in. A key the stash-box sent that Sift
    has never declared is not written anywhere: an adapter is supposed to answer in Sift's own field
    keys, and a key nothing declared is a translation that did not happen.

    A field no stash-box may fill in is never written, whatever the strategy says. What a file
    measures is not an opinion, and a size on disk offered by a stash-box is not a correction to
    the one on the disk.
    """
    decisions: list[Decision] = []
    for declared in fields_of(subject):
        if not declared.imported:
            continue
        decisions.append(
            decide(
                key=declared.key,
                mine=mine.get(declared.key),
                theirs=theirs.get(declared.key),
                strategy=strategies.get(declared.key, DEFAULT_STRATEGY),
                is_list=declared.kind in LIST_KINDS,
            )
        )
    return Plan(subject=subject, local_id=local_id, source_id=source_id, decisions=tuple(decisions))


@dataclass(frozen=True)
class Missing:
    """One row an answer would have to invent, and WHAT it would be.

    The kind travels with the name because it is known exactly where the name is discovered and
    nowhere afterwards. A bare list of thirty-one words ("Orla Tennant", "Blond Hair", "Beach",
    "Northlight Media Group") asks somebody to decide which are worth creating without saying
    which are people, which are tags and which is a site, and that is most of the decision.
    """

    name: str
    #: `person`, `tag` or `site`. A `Subject` value, kept as its own string so this shape can
    #: cross to the wire without the enum going with it.
    kind: str


#: Which rows one write is allowed to invent.
#:
#: `False` is none of them and `True` is every name the plan carries. A frozenset of `(kind, name)`
#: is exactly those rows and nothing else, which is what somebody ticking a list one at a time is
#: saying: "make the person Orla Tennant, do not make the tag Beach". A plain bool can only ask
#: the question the button asks (all of them, or none), so a page offering thirty-one new entries
#: would have two answers and neither the one anybody wanted.
#:
#: KIND and name, not the name alone, so ticking a person can never also invent a tag that happens
#: to be spelled the same. Every writer knows the kind at the call site; none of them has to guess.
#:
#: ONE shape rather than a bool beside a list, because two parameters can disagree and every writer
#: would then have to decide which of them wins.
Creating = bool | frozenset[tuple[str, str]]


def may_create(creating: Creating, kind: str, name: str) -> bool:
    """Whether THIS row may be invented. The one place the two shapes are told apart.

    Every writer asks this per name rather than testing the type itself: a second place that
    unpacked it would be a second place for `True` and `frozenset()` to come to mean different
    things.
    """
    if isinstance(creating, bool):
        return creating
    return (kind, name) in creating


class Naming(Protocol):
    """Turning a name a stash-box said into a row in this library, or finding the row it means.

    An adapter answers in names because a name is all a stash-box can honestly give: its ids belong
    to its own database. So somewhere between "they said Jane Doe" and "this file carries person
    01H..." something has to look Jane Doe up, and possibly make her.

    Declared in the kernel and implemented at the wiring, because the three lookups belong to two
    different areas and neither may import the other. `creating` is passed through rather than
    assumed: making a person is a decision somebody takes on a button, and a seam that always
    created would make it silently.

    The fourth verb is not a lookup and belongs here all the same. It is the one thing a writer
    learns about a PERSON from an answer about a file (that they made it rather than appear in it)
    and the row it writes is the row `person_named` creates. Putting it on `Filing` instead would
    have filed a fact about a person under "what is on a file"; asking the writer to reach the
    catalog itself would have taught it what a person is made of, which is the one thing that file
    says it does not know.
    """

    async def person_named(self, name: str, *, creating: bool) -> str | None: ...

    async def site_named(
        self, name: str, *, creating: bool, address: str | None = None
    ) -> str | None: ...

    async def tag_named(self, name: str, *, creating: bool) -> str | None: ...

    async def mark_pmv_creator(self, person_id: str) -> None:
        """Say that this person makes the edits, rather than appearing in them.

        No answer, on purpose. The statement underneath only ever SETS and is written to be safe to
        repeat, so "was it already there" is a fact about the last time somebody was recognised and
        not about this write, and a caller that reported it would count a creator as unwritten on
        every file of theirs after the first.
        """
        ...

    async def mark_created_by_box(self, kind: str, local_id: str, source_id: str) -> None:
        """Say that this row was INVENTED by the source whose answer is being applied.

        The fifth verb, and it is here for the reason the fourth one is: it is a thing the writer
        learns while applying an answer about a FILE (that a name in it did not exist a moment ago
        and does now) and the row it writes belongs to a table the writer is not allowed to know
        the shape of.

        `kind` is the subject's own word, the one `Missing` carries. No answer, for the reason above
        it: the statement underneath writes only where nothing is recorded yet, so "did it move" is
        a fact about whether this row had already been claimed rather than about this write.
        """
        ...


class Filing(Protocol):
    """What is on a FILE, and how to put something on it.

    Its own shape rather than more methods on `Naming`, because it answers a different question.
    `Naming` turns a word into a row; this puts a row onto a file and reads back what is already
    there. A stash-box recognising a video says five things about it and three of them are rows in
    tables that belong to two different areas, so the wiring builds one of these out of both, and
    the writer that uses it imports neither.

    Every attach carries a `source`, which is how a tag a stash-box applied can be told from one
    somebody chose: counted, filtered, and taken back off without touching the other kind. And,
    where the source is a stash-box's, `box_id`: WHICH box, so a line about the filing can name it.
    """

    async def people_on(self, asset_id: str) -> tuple[str, ...]: ...

    async def tags_on(self, asset_id: str) -> tuple[str, ...]: ...

    async def site_of(self, asset_id: str) -> str | None: ...

    async def attribute(
        self, asset_id: str, person_id: str, *, source: str, box_id: str | None = None
    ) -> None: ...

    async def attach_tag(
        self, asset_id: str, tag_id: str, *, source: str, box_id: str | None = None
    ) -> None: ...

    async def file_under_site(
        self, asset_id: str, site: str, *, source: str, box_id: str | None = None
    ) -> None:
        """Put this file under this site, by name, marked with how that was decided.

        By NAME rather than by id, like every other verb here, because a name is all an answer from
        outside can honestly give. What the name resolves to is the wiring's business.
        """
        ...

    async def accounts_on(self, asset_id: str) -> tuple[Mapping[str, str], ...]:
        """The named usernames a file is filed under, as `accounts` entries (site, handle, url).

        The nameless "poster unknown" row is not one: it names nobody, and `site_of` answers for it.
        """
        ...

    async def file_under_username(
        self,
        asset_id: str,
        *,
        site: str,
        handle: str,
        url: str | None,
        source: str,
        person_id: str | None = None,
        box_id: str | None = None,
    ) -> bool:
        """Put this file under a NAMED username on a site. True when the filing is new.

        By name, like `file_under_site`, and never through the site's nameless row: an answer that
        says who posted a file says it with a name. `person_id` is who the username is, written only
        where the username names nobody yet.
        """
        ...


class Writer(Protocol):
    """How one area reads and writes the subject it owns.

    Three questions and no more. Everything about WHAT to write is decided above, from the record
    registry and the strategies; this is only how to get at the rows. A writer that started deciding
    things would be a second copy of the rules, free to disagree with the first.
    """

    @property
    def subject(self) -> Subject: ...

    async def current(self, local_id: str) -> Mapping[str, object]:
        """What Sift holds for this subject now, by the record registry's own field keys."""
        ...

    async def missing(self, values: Mapping[str, object]) -> tuple[Missing, ...]:
        """The rows in these values that name nothing in this library yet, each with its kind.

        What the confirm button lists. Somebody agreeing to a page of matches has to be able to see
        that it would also invent eleven people, before they press it and not after, so this is
        asked of the plan rather than reported by the write.
        """
        ...

    async def write(
        self, local_id: str, values: Mapping[str, object], *, creating: Creating, actor: Actor
    ) -> Mapping[str, int]:
        """Write these fields, and only these. Answers with the ones it ACTUALLY wrote, and how many.

        A PARTIAL write. The values are the fields that changed, never the whole record: a writer
        that rebuilt the row from what it was handed would blank every field the plan left alone,
        which is the failure that looks like a successful import right up until somebody opens a
        page.

        `creating` is permission, not intent: a name it does not cover and that matches nothing in
        the library is dropped rather than made into a new row. Ask it through `may_create`: it is
        a set of names as often as it is a yes or a no.

        The ANSWER is what makes the count on the confirm toast true. The keys handed IN are not the
        same number in either direction: a writer with no setter for a field would count it anyway,
        and a list of names this run was not allowed to invent is a field that changed nothing. Only
        the writer knows which, so only the writer can say.

        A MAPPING RATHER THAN A LIST OF KEYS, and the value is HOW MANY ROWS that field gained: one
        for a scalar, and for the list fields (links, aliases, tags, usernames) the number that
        actually landed. "FansDB filled in their links" is true of one address and of nine and
        useful for neither; the History line says "3 links". Only the
        writer knows this number for the same reason it is the only thing that knows which fields
        landed at all, so it is answered from the same place rather than counted again afterwards
        from the plan, where it would be the number OFFERED.

        The key set is what most readers use: `key in answer`, `len`, and `if answer`.

        `actor` is WHO is doing it, carried down so the writers can record what they did through the
        ledger's door in their own transactions. A box answering, and somebody settling a
        disagreement by hand, reach the same writer and leave rows that are indistinguishable
        afterwards, which is the one fact about a record fill nothing else can recover. It is
        required rather than defaulted for that reason: a default would be a wrong answer on
        whichever half of the callers did not think about it.
        """
        ...


@runtime_checkable
class ReadsMany(Protocol):
    """A writer that can read what it holds for many subjects in one go.

    For a survey that plans against every linked subject of a kind, where one read per subject
    per list is the whole cost. Optional: a writer without it is read one subject at a time.
    """

    async def current_many(self, local_ids: Sequence[str]) -> Mapping[str, Mapping[str, object]]:
        """`current` for each id, keyed by id; an id with no row may be absent."""
        ...


@dataclass
class Enricher:
    """Every registered writer, and the one way to apply a plan.

    Held on the application rather than in a module-level dictionary, for the reason the workbench
    gives: a writer is built from a feature's own service, which exists only once the application
    has been assembled, so a process-global would be shared between every application a test
    builds.
    """

    writers: dict[Subject, Writer] = dataclass_field(default_factory=dict)

    def register(self, writer: Writer) -> None:
        """Claim a subject. Registering the same one twice is a bug, not an override."""
        if writer.subject in self.writers:
            raise ValueError(f"a writer for {writer.subject.value} is already registered")
        self.writers[writer.subject] = writer

    def can_write(self, subject: Subject) -> bool:
        return subject in self.writers

    async def plan_for(
        self,
        *,
        subject: Subject,
        local_id: str,
        source_id: str,
        offered: Mapping[str, object],
        strategies: Mapping[str, Strategy],
    ) -> Plan | None:
        """What applying this record would do. None when nothing can write this kind of subject."""
        writer = self.writers.get(subject)
        if writer is None:
            return None
        return plan(
            subject=subject,
            local_id=local_id,
            source_id=source_id,
            mine=await writer.current(local_id),
            theirs=offered,
            strategies=strategies,
        )

    async def currents(
        self, subject: Subject, local_ids: Sequence[str]
    ) -> Mapping[str, Mapping[str, object]]:
        """What Sift holds now for each of these subjects, by id. Empty where nothing writes the kind.

        One read per table where the writer reads many in one go (`ReadsMany`), else one subject at
        a time. An id the batch had no row for is read alone, so every id asked is answered.
        """
        writer = self.writers.get(subject)
        if writer is None or not local_ids:
            return {}
        held: dict[str, Mapping[str, object]] = {}
        if isinstance(writer, ReadsMany):
            held.update(await writer.current_many(local_ids))
        for one in local_ids:
            if one not in held:
                held[one] = await writer.current(one)
        return held

    def plan_against(
        self,
        *,
        subject: Subject,
        local_id: str,
        source_id: str,
        mine: Mapping[str, object],
        offered: Mapping[str, object],
        strategies: Mapping[str, Strategy],
    ) -> Plan | None:
        """`plan_for` with what Sift holds already read (`currents`). None where nothing writes it."""
        if subject not in self.writers:
            return None
        return plan(
            subject=subject,
            local_id=local_id,
            source_id=source_id,
            mine=mine,
            theirs=offered,
            strategies=strategies,
        )

    async def missing_for(self, decided: Plan) -> tuple[Missing, ...]:
        """The rows this plan would have to invent. What the confirm button lists above itself."""
        writer = self.writers.get(decided.subject)
        if writer is None:
            return ()
        return await writer.missing(decided.writes)

    async def apply(
        self,
        decided: Plan,
        *,
        only: Sequence[str] | None = None,
        creating: Creating = False,
        actor: Actor | None = None,
    ) -> Mapping[str, int]:
        """Write what a plan decided. Returns the fields that actually changed, and how many rows
        each of them gained (see `Writer.write`).

        `only` filters it to the fields somebody ticked, which is what the confirm screen sends: the
        plan is the offer and the ticks are the answer. Nothing outside the plan can be written by
        naming it here: a field that was a conflict stays a conflict, because the tick says which
        of the plan's own writes to take rather than adding one.

        What comes back is the WRITER's answer and not the keys handed to it. They are not the same
        fact: a field with nothing behind it to
        write it, and a list of names this run had no permission to invent, would both count as
        written because they were asked for. See `Writer.write`.
        """
        writer = self.writers.get(decided.subject)
        if writer is None:
            return {}
        values = decided.writes
        if only is not None:
            wanted = set(only)
            values = {key: value for key, value in values.items() if key in wanted}
        if not values:
            return {}
        # THE BOX IS THE ACTOR unless the caller says otherwise, and it is taken from the plan
        # rather than asked of the four call sites: `source_id` is which box answered, the plan
        # cannot exist without one, and a caller free to name the actor is a caller free to name
        # the wrong one. `actor` is for the path where a PERSON pressed something. See
        # `write_one`, which is that path.
        return await writer.write(
            decided.local_id,
            values,
            creating=creating,
            actor=actor or Actor.box(decided.source_id),
        )

    async def write_one(
        self, subject: Subject, local_id: str, key: str, value: object, *, actor: Actor
    ) -> bool:
        """Write ONE field, chosen by hand, on purpose, against the rules.

        This is what settling a disagreement is: somebody has looked at two answers and picked one,
        and the per-field rule (which exists to decide what happens when NOBODY is looking) has
        nothing to say about it. Going through the writer anyway is what keeps a person's birthday
        written by exactly one piece of code whether it was typed, imported or reconciled.

        False when nothing can write that kind of subject (an older build meeting a newer one
        rather than a failure), and also when the writer took the field and wrote nothing for it.
        The two are one answer here on purpose: the caller is a screen reporting whether a
        disagreement is SETTLED, and a field that did not land is not settled either way.
        """
        writer = self.writers.get(subject)
        if writer is None:
            return False
        # The USER and not the box, which is the whole difference this path records: the value
        # came from a box and the choice to take it did not.
        return key in await writer.write(local_id, {key: value}, creating=False, actor=actor)


def strategy_key(subject: Subject, key: str) -> str:
    """The settings key that holds one field's rule.

    One setting per editable field, named from the registry rather than written out by hand. The
    screen that draws them is generated from the same registry, so a field added next year arrives
    with a control and a stored rule and nobody has to remember either.
    """
    return f"enrich.{subject.value}.{key}"


def enrichable(subject: Subject) -> tuple[str, ...]:
    """The fields of one subject a stash-box may write, in the order the record draws them."""
    return tuple(one.key for one in fields_of(subject) if one.imported)


def declared_field_kind(subject: Subject, key: str) -> Kind | None:
    """What kind one field is, for a caller that has a key and no registry of its own."""
    found = field(subject, key)
    return found.kind if found is not None else None
