# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deciding what a stash-box's answer may write, field by field, and writing it.

A blank never lands on a value, and under Merge a disagreement is a conflict, never a write."""

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

    IGNORE = "ignore"
    #: Add to a list; fill a blank; a disagreement is a conflict.
    MERGE = "merge"
    OVERWRITE = "overwrite"


#: Merge cannot lose anything: it fills what is missing and asks about what disagrees.
DEFAULT_STRATEGY = Strategy.MERGE

#: Read from the registry's kinds, so a field added later is a list by its declaration.
LIST_KINDS = frozenset({Kind.NAMES, Kind.LINKS, Kind.TAGS, Kind.ACCOUNTS})


class Outcome(StrEnum):
    """What the rule decided for one field."""

    WRITE = "write"
    KEEP = "keep"
    #: Two values that disagree; what reconcile is a list of.
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class Decision:
    """What would happen to one field, decided apart from writing so a screen can ask first."""

    key: str
    outcome: Outcome
    mine: object = None
    theirs: object = None
    #: Only meaningful when the outcome is `WRITE`.
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
    """Whether a value counts as nothing: absent, blank or empty, never zero."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    return False


def _as_list(value: object) -> list[object]:
    """A list field's value however it arrived; a bare string is one entry."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [one for one in value if not _empty(one)]
    return [value]


def _key_of(one: object) -> object:
    """What makes two list entries the same; strings case-folded, so one alias is never two."""
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
    """What is there, then what is new, in order; public so a hand answer means the same."""
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
    """One field, decided; pure, so every branch is a rule somebody can be shown."""
    if strategy is Strategy.IGNORE or _empty(theirs):
        # A field nobody wants, or a stash-box that does not know. Neither is a correction.
        return Decision(key=key, outcome=Outcome.KEEP, mine=mine, theirs=theirs)

    if is_list:
        # A list never conflicts: two spellings both find her.
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
    # Merge on a filled single value that disagrees: a question, never a silent overwrite.
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
    """What one stash-box's record would do to one subject, walking the registry's fields."""
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
    """One row an answer would have to invent, with its kind, which is most of the decision."""

    name: str
    #: A `Subject` value as a string, so this can cross the wire without the enum.
    kind: str


#: Which rows one write may invent: none, every one, or exactly these `(kind, name)` pairs.
Creating = bool | frozenset[tuple[str, str]]


def may_create(creating: Creating, kind: str, name: str) -> bool:
    """Whether this row may be invented; the one place the two shapes are told apart."""
    if isinstance(creating, bool):
        return creating
    return (kind, name) in creating


class Naming(Protocol):
    """Finding or, with permission, creating the row a stash-box's name means."""

    async def person_named(self, name: str, *, creating: bool) -> str | None: ...

    async def site_named(
        self, name: str, *, creating: bool, address: str | None = None
    ) -> str | None: ...

    async def tag_named(self, name: str, *, creating: bool) -> str | None: ...

    async def mark_pmv_creator(self, person_id: str) -> None:
        """Say that this person makes the edits rather than appearing in them; it only sets."""
        ...

    async def mark_created_by_box(self, kind: str, local_id: str, source_id: str) -> None:
        """Say that this row was invented by the source whose answer is being applied."""
        ...


class Filing(Protocol):
    """What is on a file and how to put something on it, each attach marked with its source."""

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
        """Put this file under this site by name, marked with how that was decided."""
        ...

    async def accounts_on(self, asset_id: str) -> tuple[Mapping[str, str], ...]:
        """The named usernames a file is filed under, as `accounts` entries (site, handle, url)."""
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
        """Put this file under a named username on a site. True when the filing is new."""
        ...


class Writer(Protocol):
    """How one area reads and writes the subject it owns; it decides nothing."""

    @property
    def subject(self) -> Subject: ...

    async def current(self, local_id: str) -> Mapping[str, object]:
        """What Sift holds for this subject now, by the record registry's own field keys."""
        ...

    async def missing(self, values: Mapping[str, object]) -> tuple[Missing, ...]:
        """The rows these values name that do not exist yet, each with its kind."""
        ...

    async def write(
        self, local_id: str, values: Mapping[str, object], *, creating: Creating, actor: Actor
    ) -> Mapping[str, int]:
        """Write only these fields; answer which actually landed and how many rows each gained."""
        # Partial: rebuilding the row would blank every field the plan left alone.
        # `creating` is permission, read through `may_create`; `actor` is required, never guessed.
        ...


@runtime_checkable
class ReadsMany(Protocol):
    """A writer that can read what it holds for many subjects in one go."""

    async def current_many(self, local_ids: Sequence[str]) -> Mapping[str, Mapping[str, object]]:
        """`current` for each id, keyed by id; an id with no row may be absent."""
        ...


@dataclass
class Enricher:
    """Every registered writer, held per application, and the one way to apply a plan."""

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
        """What Sift holds now for each of these subjects, by id; empty where nothing writes it."""
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
        """`plan_for` with what Sift holds already read (`currents`), or None."""
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
        """Write what a plan decided, limited to `only`; returns what the writer says landed."""
        writer = self.writers.get(decided.subject)
        if writer is None:
            return {}
        values = decided.writes
        if only is not None:
            wanted = set(only)
            values = {key: value for key, value in values.items() if key in wanted}
        if not values:
            return {}
        # The box is the actor, from the plan, unless a person pressed it (`write_one`).
        return await writer.write(
            decided.local_id,
            values,
            creating=creating,
            actor=actor or Actor.box(decided.source_id),
        )

    async def write_one(
        self, subject: Subject, local_id: str, key: str, value: object, *, actor: Actor
    ) -> bool:
        """Write one field chosen by hand; False when nothing could write it or it did not land."""
        writer = self.writers.get(subject)
        if writer is None:
            return False
        # The user, not the box: the value came from a box, the choice to take it did not.
        return key in await writer.write(local_id, {key: value}, creating=False, actor=actor)


def strategy_key(subject: Subject, key: str) -> str:
    """The settings key that holds one field's rule, named from the registry."""
    return f"enrich.{subject.value}.{key}"


def enrichable(subject: Subject) -> tuple[str, ...]:
    """The fields of one subject a stash-box may write, in the order the record draws them."""
    return tuple(one.key for one in fields_of(subject) if one.imported)


def declared_field_kind(subject: Subject, key: str) -> Kind | None:
    """What kind one field is, for a caller that has a key and no registry of its own."""
    found = field(subject, key)
    return found.kind if found is not None else None
