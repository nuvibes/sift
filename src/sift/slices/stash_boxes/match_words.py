# SPDX-License-Identifier: AGPL-3.0-or-later
"""The words and wire shapes of the pile: what an answer would change, and what a press did."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from sift.kernel.access.sentences import Line, said, text_of, thing
from sift.kernel.enrichment import (
    Decision,
    Enricher,
    Missing,
    Outcome,
    Plan,
    Strategy,
)
from sift.kernel.ledger import Actor
from sift.kernel.records import (
    Kind,
    Subject,
    fields_of,
    said_plainly,
)
from sift.slices.stash_boxes.models import (
    FieldChange,
    MatchView,
    MissingView,
)
from sift.slices.stash_boxes.reconcile import (
    Disagreement,
    SetAside,
)
from sift.slices.stash_boxes.router_base import _record
from sift.slices.stash_boxes.service import (
    Match,
)


def _settled_line(
    found: Disagreement, *, take_theirs: bool, aside: Sequence[SetAside] = ()
) -> tuple[Line, str]:
    """What a settled disagreement's receipt says: the title, then the detail under it."""
    (field,) = said_plainly(found.subject, [found.key])
    mine, theirs = _value_said(found.mine), _value_said(found.theirs)
    who = thing(found.subject.value, found.local_id, found.name)
    if take_theirs:
        named = _and([f"{one.box_name}'s {_cut(_value_said(one.theirs))}" for one in aside])
        line = said(
            f"Took {found.box_name}'s {field} for ",
            who,
            f": {_cut(theirs)}, where you had {_cut(mine)}",
            f", and set aside {named}" if aside else None,
        )
        detail = (
            f"{field[:1].upper()}{field[1:]} was {mine} and is now {theirs}, from {found.box_name}."
        )
        detail += "".join(
            f" {one.box_name} said {_value_said(one.theirs)}, and that is set aside."
            for one in aside
        )
        return line, detail
    return (
        said(
            f"Kept your {field} for ", who, f": {_cut(mine)}, not {found.box_name}'s {_cut(theirs)}"
        ),
        f"{field[:1].upper()}{field[1:]} stays {mine}. {found.box_name} said {theirs}.",
    )


def _settled_words(
    found: Disagreement, *, take_theirs: bool, aside: Sequence[SetAside] = ()
) -> tuple[str, str]:
    """`_settled_line`'s title as words, and its detail."""
    line, detail = _settled_line(found, take_theirs=take_theirs, aside=aside)
    return text_of(line), detail


#: The longest a value is drawn in a receipt's TITLE before it is cut. A title is one line of a
#: History thread; the detail under it carries the value whole.
_TITLE_VALUE = 60


def _value_said(value: object) -> str:
    """One side of a disagreement as words.

    Never nothing: a disagreement is a CONFLICT, which `kernel.enrichment.decide` makes only where
    both sides hold a value, and every box set aside beside it is a conflict by the same rule.
    """
    if isinstance(value, list | tuple):
        return ", ".join(str(one) for one in value)
    return str(value)


def _cut(said: str) -> str:
    return said if len(said) <= _TITLE_VALUE else said[: _TITLE_VALUE - 1].rstrip() + "\u2026"


def _and(parts: Sequence[str]) -> str:
    """ "a", "a and b", "a, b and c"."""
    if len(parts) < 2:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


#: The kinds of field that hold a LIST, where "14 tags" is a count of rows and means something.
#: A single-valued field written on three files is three titles, and "3 title" is not a sentence,
#: so a single field is named bare and the file count at the head of the line carries the number.
_LISTS = frozenset({Kind.NAMES, Kind.LINKS, Kind.TAGS, Kind.ACCOUNTS, Kind.SOURCES})


def _confirmed(count: int, boxes: Sequence[str], written: Mapping[str, int]) -> str:
    """What confirming a page of matches did: WHOSE answers, to how many files, and what they wrote.

    Every History line says what happened: the boxes are the ones whose matches were settled, in
    the order first met; the
    fields are summed over every file's write (`Enricher.apply` answers each field's row count),
    named with the registry's words so the line renames itself with the record.
    """
    files = "file" if count == 1 else "files"
    whose = _and([f"{name}'s" for name in boxes]) or "the stash-boxes'"
    head = f"Applied {whose} {'answer' if count == 1 else 'answers'} to {count} {files}"
    if not written:
        return f"{head}; nothing on {'it' if count == 1 else 'them'} changed"
    lists = {one.key for one in fields_of(Subject.ASSET) if one.kind in _LISTS}
    counted = {key: many for key, many in written.items() if key in lists}
    return f"{head}: {_and(list(said_plainly(Subject.ASSET, written, counted)))}"


def _did(*, files: int, fields: int, created: int) -> str:
    """What the decision changed, written when it happens rather than worked out when it is read.

    A sentence assembled later from the current state describes the library as it is now, which is
    exactly what somebody reading a list of past decisions is trying to look behind.
    """
    parts = [
        f"{fields} {'field' if fields == 1 else 'fields'} on {files} {'file' if files == 1 else 'files'}"
    ]
    if created:
        parts.append(f"{created} new {'entry' if created == 1 else 'entries'} made")
    return ", ".join(parts)


async def _match_view(
    held: Match,
    *,
    enricher: Enricher,
    strategies: Mapping[str, Strategy],
    art: str | None = None,
) -> MatchView:
    """One kept answer, with what applying it would do worked out but not done."""
    plan = await enricher.plan_for(
        subject=Subject.ASSET,
        local_id=held.asset_id,
        source_id=held.box_id,
        offered=held.record.fields,
        strategies=strategies,
    )
    changes: list[FieldChange] = []
    creates: tuple[Missing, ...] = ()
    if plan is not None:
        changes = await _field_changes(enricher, plan)
        creates = await enricher.missing_for(plan)
    return MatchView(
        asset_id=held.asset_id,
        box_id=held.box_id,
        box_name=held.box_name,
        remote_id=held.remote_id,
        grade=held.grade.value,
        state=held.state,
        found_at=held.found_at,
        decided_at=held.decided_at,
        record=_record(held.record, held.box_name),
        changes=changes,
        creates=[MissingView(name=one.name, kind=one.kind) for one in creates],
        art=art,
    )


async def _field_changes(enricher: Enricher, plan: Plan) -> list[FieldChange]:
    """What applying a plan would change, each field with the new rows it depends on.

    The press writes a field only where a name in it resolves, and a name nobody ticked is dropped,
    so the count above the button has to know which fields hang on which ticks. Asked of the same
    writer the press reaches, one field at a time, so the two can never disagree about a name.
    """
    changes: list[FieldChange] = []
    for one in plan.decisions:
        if one.outcome is Outcome.KEEP:
            continue
        needs: tuple[Missing, ...] = ()
        if one.outcome is Outcome.WRITE:
            needs = await enricher.missing_for(replace(plan, decisions=(one,)))
        changes.append(
            FieldChange(
                key=one.key,
                outcome=one.outcome.value,
                mine=one.mine,
                theirs=one.theirs,
                needs=[MissingView(name=need.name, kind=need.kind) for need in needs],
                stands=_stands_without(one, needs),
            )
        )
    return changes


def _stands_without(decision: Decision, needs: Sequence[Missing]) -> bool:
    """Whether a field writes something when none of the new rows it names is made.

    The writer's own rule: a name, a tag or a Site lands when it resolves to a row that is already
    here, and a username lands when its Site does and the file is not already filed under it.
    """
    if not needs:
        return True
    missing = {need.name.strip() for need in needs}
    value = decision.value
    entries = value if isinstance(value, (list, tuple)) else [value]
    held = decision.mine if isinstance(decision.mine, (list, tuple)) else [decision.mine]
    for entry in entries:
        if isinstance(entry, Mapping):
            if entry in held:
                continue
            entry = entry.get("site")
        if isinstance(entry, str) and entry.strip() and entry.strip() not in missing:
            return True
    return False


async def _answered(
    enricher: Enricher,
    plan: Plan,
    answers: Mapping[str, str],
    actor: Actor,
) -> int:
    """Write the answers somebody gave to the fields the rules refused to decide.

    Only fields the plan itself calls a conflict. An answer naming anything else is dropped rather
    than obeyed: the row somebody was looking at is the offer, and a key that was not on it is a
    request to write a field nobody was shown.
    """
    done = 0
    for one in plan.conflicts:
        take = answers.get(one.key)
        if take is None:
            continue
        if take == "both":
            # Refused outright; it can only arrive from a client that decided for itself.
            #
            # A conflict only ever happens on a field that holds one value: a list is merged and
            # both answers are kept without anybody being asked (`decide`). So the planner cannot
            # have offered "both" on anything in `conflicts`, and obeying it would write a list into
            # a single value. "both" is still a word the wire accepts (`SettleField.take`), so this
            # refusal is what guards an untrusted body.
            continue
        # Counted rather than checked, and that is not optimism. `write_one` answers False only
        # where nothing can write this kind of subject, and a plan only exists where something
        # can, because `plan_for` returns None otherwise. Reaching here with a plan in hand is
        # therefore already the proof, and a branch on it was one nothing could take.
        await enricher.write_one(plan.subject, plan.local_id, one.key, one.theirs, actor=actor)
        done += 1
    return done
