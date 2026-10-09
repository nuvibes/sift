# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a stash-box disagrees with what this library already says.

One row per disagreeing field, only on a linked subject and only where that field's rule asks rather
than overwrites. A conflict is worked out on every read, never stored; an answer of "keep mine" is
stored with the pair of values it answered (`stash_box_kept`). A record's page asks `for_subject`,
the Stash-boxes pane the whole survey; both come out of `_conflicts`.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sift.kernel.access import Repository, Viewer
from sift.kernel.changes import current_mark
from sift.kernel.db import Connection
from sift.kernel.enrichment import Enricher, Outcome, Strategy
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.kernel.memo import MarkedMemo
from sift.kernel.records import FoundRecord, Subject, said_plainly, value_said
from sift.kernel.seams import SettingsSeam
from sift.kernel.wiring import Part
from sift.kernel.workbench import DOER, Named, Piece, Preview, Recorded, Worded
from sift.slices.stash_boxes.enrich import COLUMNS
from sift.slices.stash_boxes.jobs import strategies_for
from sift.slices.stash_boxes.service import StashBoxService

log = get_logger(__name__)

NAME = "reconcile"

#: The name the queue registers under, for whoever writes a receipt against it.
RECONCILE = NAME


@dataclass(frozen=True, slots=True)
class Disagreement:
    """One field where two answers differ, and neither has won."""

    subject: Subject
    local_id: str
    name: str
    box_id: str
    box_name: str
    key: str
    mine: object
    theirs: object


@dataclass(frozen=True, slots=True)
class SetAside:
    """Another box's answer to a field, set aside because somebody took a different box's answer.

    `was` is the answer already on file for this box and field, so an Undo puts that back.
    """

    box_id: str
    box_name: str
    mine: object
    theirs: object
    was: tuple[str, str] | None


class Reconciler:
    """Finds every field where a kept stash-box record and this library disagree."""

    def __init__(
        self,
        service: StashBoxService,
        enricher: Enricher,
        settings: SettingsSeam,
        access: Repository,
    ) -> None:
        self._service = service
        self._enricher = enricher
        self._settings = settings
        self._access = access
        # Kept under the change mark it was computed at (`MarkedMemo`); rarely hit when busy.
        self._kept: MarkedMemo[list[Disagreement]] = MarkedMemo()

    async def disagreements(self, viewer: Viewer) -> list[Disagreement]:
        """Every field a linked box disagrees with, as this user may be told about it, per user and
        mark.
        """
        return await self._kept.get(viewer.id, current_mark(), lambda: self._disagreements(viewer))

    async def _disagreements(self, viewer: Viewer) -> list[Disagreement]:
        """The same, computed, scoped so an unseen subject produces no row."""
        rules: dict[Subject, dict[str, Strategy]] = {}
        # Once, not per row.
        names = {box.id: box.name for box in await self._service.boxes()}
        # Every answer already given by keeping one's own value, read once.
        already = await self._service.kept_answers()
        linked = await self._service.linked_subjects()
        # Every linked person's visibility in one read; absent means not allowed or not there.
        people = await self._access.visible_people(
            viewer, [local_id for subject, local_id, _b, _r in linked if subject is Subject.PERSON]
        )
        # Sites and tags the same way: a by-id read costs about the whole wall.
        seen: dict[Subject, Mapping[str, object]] = {
            Subject.PERSON: people,
            Subject.SITE: await self._access.visible_sites(
                viewer,
                [local_id for subject, local_id, _b, _r in linked if subject is Subject.SITE],
            ),
            Subject.TAG: await self._access.visible_tags(
                viewer, [local_id for subject, local_id, _b, _r in linked if subject is Subject.TAG]
            ),
        }
        # What the library holds for every linked subject, read per kind rather than per link.
        kinds = dict.fromkeys(subject for subject, _l, _b, _r in linked)
        held = {
            kind: await self._enricher.currents(
                kind, [local_id for subject, local_id, _b, _r in linked if subject is kind]
            )
            for kind in kinds
        }
        found: list[Disagreement] = []
        for subject, local_id, box_id, record in linked:
            if local_id not in seen.get(subject, {}):
                continue
            # Kept local is not a question: neither answer could land. The door's own predicate.
            if await self._service.kept_local(subject, local_id):
                continue
            if subject not in rules:
                rules[subject] = await strategies_for(self._settings, subject)

            # By box and field; the defaults bind this row, where a closure would see the last.
            def already_here(
                box: str, key: str, _s: Subject = subject, _l: str = local_id
            ) -> tuple[str, str] | None:
                return already.get((_s.value, _l, box, key))

            found.extend(
                await self._conflicts(
                    subject,
                    local_id,
                    box_id,
                    names.get(box_id, box_id),
                    record,
                    rules[subject],
                    already_here,
                    mine=held[subject].get(local_id),
                )
            )
        return found

    async def for_subject(
        self, viewer: Viewer, subject: str, local_id: str
    ) -> list[Disagreement] | None:
        """Every field a box disagrees with about ONE record, or None where there is no question.

        One seeking read of the links, one of its answers, and one plan per box. None, not empty,
        for a user who is not an admin, a kind no stash-box knows, and a record this user may not be
        shown.
        """
        if not viewer.is_admin:
            return None
        kind = _LINKABLE.get(subject)
        if kind is None:
            return None
        if not await self._visible(viewer, kind, local_id):
            return None
        # Kept local: nothing to settle.
        if await self._service.kept_local(kind, local_id):
            return []
        # A file's link is the answer it was agreed to (`StashBoxService.agreed_answers`).
        if kind is Subject.ASSET:
            links = await self._service.agreed_answers(local_id)
        else:
            links = await self._service.links_of(kind, local_id)
        if not links:
            return []
        strategies = await strategies_for(self._settings, kind)
        answered = await self._service.kept_answers_for(kind.value, local_id)
        # A file is named by its filename, not the box's title, which a title disagreement is about.
        named = await self._file_name(viewer, local_id) if kind is Subject.ASSET else None

        def already_answered(box: str, key: str) -> tuple[str, str] | None:
            return answered.get((box, key))

        found: list[Disagreement] = []
        for link in links:
            found.extend(
                await self._conflicts(
                    kind,
                    local_id,
                    link.source_id,
                    link.source_name,
                    link.record,
                    strategies,
                    already_answered,
                    name=named,
                )
            )
        if kind is Subject.ASSET:
            # The file's own columns only: a site is a filing the writer adds, not a value "Take
            # theirs" could replace.
            found = [one for one in found if one.key in COLUMNS]
        return found

    async def disagreement_count(self, viewer: Viewer, subject: str, local_id: str) -> int | None:
        """How many of them there are, from `for_subject`'s own answer; None passes straight through."""
        waiting = await self.for_subject(viewer, subject, local_id)
        return None if waiting is None else len(waiting)

    async def disagreement_mark(
        self, viewer: Viewer, subject: str, local_id: str
    ) -> tuple[int | None, list[str]]:
        """The count and the boxes it is about, by name, from one `for_subject` answer."""
        waiting = await self.for_subject(viewer, subject, local_id)
        if waiting is None:
            return None, []
        return len(waiting), list(dict.fromkeys(one.box_name for one in waiting if one.box_name))

    async def subjects_with_disagreements(
        self, viewer: Viewer, subject: str
    ) -> tuple[str, ...] | None:
        """Every record of one kind with a question waiting on it: the wall facet and its filter.

        Answered from the survey and its memo, deduplicated in survey order; asked only where
        somebody opened or filtered on the column. None for a user who is not an admin or a kind no
        box knows.
        """
        if not viewer.is_admin:
            return None
        kind = _SURVEYED.get(subject)
        if kind is None:
            return None
        found = await self.disagreements(viewer)
        return tuple(dict.fromkeys(one.local_id for one in found if one.subject is kind))

    async def _conflicts(
        self,
        subject: Subject,
        local_id: str,
        box_id: str,
        box_name: str,
        record: FoundRecord,
        strategies: dict[str, Strategy],
        answered: Callable[[str, str], tuple[str, str] | None],
        *,
        name: str | None = None,
        mine: Mapping[str, object] | None = None,
    ) -> list[Disagreement]:
        """What one box and one record disagree about: the one rule, for both readers.

        `name` is what the rows call the record, `mine` is read here when not handed in, and
        `answered` is asked by box and field. No plan means no disagreement.
        """
        plan = (
            await self._enricher.plan_for(
                subject=subject,
                local_id=local_id,
                source_id=box_id,
                offered=record.fields,
                strategies=strategies,
            )
            if mine is None
            else self._enricher.plan_against(
                subject=subject,
                local_id=local_id,
                source_id=box_id,
                mine=mine,
                offered=record.fields,
                strategies=strategies,
            )
        )
        if plan is None:
            return []
        found: list[Disagreement] = []
        for one in plan.decisions:
            if one.outcome is not Outcome.CONFLICT:
                continue
            # Already answered about these two values; either side moving makes it a new question.
            if answered(box_id, one.key) == (_as_written(one.mine), _as_written(one.theirs)):
                continue
            found.append(
                Disagreement(
                    subject=subject,
                    local_id=local_id,
                    name=name if name is not None else record.name,
                    box_id=box_id,
                    box_name=box_name,
                    key=one.key,
                    mine=one.mine,
                    theirs=one.theirs,
                )
            )
        return found

    async def may_take(self, viewer: Viewer, subject: str, local_id: str) -> None:
        """Refuse, with `KeptLocal`, taking a box's value onto a record kept local now.

        Raises nothing for an unknown or unseen record, so it cannot be used to ask whether one
        exists.
        """
        kind = _LINKABLE.get(subject)
        if kind is None or not await self._visible(viewer, kind, local_id):
            return
        await self._service.nothing_applied(kind, local_id)

    async def settle(self, viewer: Viewer, one: Disagreement, *, take_theirs: bool) -> bool:
        """Take one of the two answers. Keeping your own writes the answer, with its pair, and not the
        field.
        """
        if not await self._visible(viewer, one.subject, one.local_id):
            return False
        if not take_theirs:
            await self._service.record_kept(
                one.subject.value,
                one.local_id,
                one.box_id,
                one.key,
                _as_written(one.mine),
                _as_written(one.theirs),
            )
            # The memo describes a library this request changed; the panel re-reads immediately.
            self._kept.forget()
            return True
        # Refused on a record kept local whoever asks; the writer's own check.
        await self._service.nothing_applied(one.subject, one.local_id)
        wrote = await self._enricher.write_one(
            one.subject, one.local_id, one.key, one.theirs, actor=Actor.user(viewer.id)
        )
        if wrote and one.subject is not Subject.ASSET:
            # Not for a file, whose one line is the route's receipt. Otherwise a run of that box
            # which filled in this one field, with its `enriched` event.
            await self._service.record_enrichment(
                one.subject,
                one.local_id,
                one.box_id,
                automatic=False,
                applied={one.key: 1},
                pressed_by=viewer.id,
            )
        self._kept.forget()
        return wrote

    async def set_aside_by(self, viewer: Viewer, taken: Disagreement) -> list[SetAside]:
        """Every other box's row about the same field, as it stands after `taken` was written.

        Asked after the write, against the value now held, so each is stored under the pair its row
        would ask about. What was on file for each box is read first, for the receipt.
        """
        answered = await self._service.kept_answers_for(taken.subject.value, taken.local_id)
        after = await self.for_subject(viewer, taken.subject.value, taken.local_id) or []
        return [
            SetAside(
                box_id=one.box_id,
                box_name=one.box_name,
                mine=one.mine,
                theirs=one.theirs,
                was=answered.get((one.box_id, one.key)),
            )
            for one in after
            if one.key == taken.key and one.box_id != taken.box_id
        ]

    async def set_aside_on(
        self, connection: Connection, taken: Disagreement, aside: list[SetAside]
    ) -> None:
        """Write those answers on the caller's transaction, the one that writes the receipt."""
        for one in aside:
            await self._service.record_kept_on(
                connection,
                taken.subject.value,
                taken.local_id,
                one.box_id,
                taken.key,
                _as_written(one.mine),
                _as_written(one.theirs),
            )
        self._kept.forget()

    async def put_back_kept(
        self,
        viewer: Viewer,
        subject: Subject,
        local_id: str,
        box_id: str,
        key: str,
        was: tuple[str, str],
    ) -> bool:
        """Restore the answer on file before a press set this box aside."""
        if not await self._visible(viewer, subject, local_id):
            return False
        await self._service.record_kept(subject.value, local_id, box_id, key, was[0], was[1])
        self._kept.forget()
        return True

    async def forget_kept(
        self, viewer: Viewer, subject: Subject, local_id: str, box_id: str, key: str
    ) -> bool:
        """Take back a "keep mine", so the field is a question again; False for a record not shown."""
        if not await self._visible(viewer, subject, local_id):
            return False
        await self._service.forget_kept(subject.value, local_id, box_id, key)
        self._kept.forget()
        return True

    async def _visible(self, viewer: Viewer, subject: Subject, local_id: str) -> bool:
        if subject is Subject.ASSET:
            # `open_asset`: a concealed file's placeholder is nothing to read or write a value on.
            return await self._access.open_asset(viewer, local_id) is not None
        if subject is Subject.PERSON:
            return await self._access.visible_person(viewer, local_id) is not None
        if subject is Subject.SITE:
            return await self._access.visible_site(viewer, local_id) is not None
        return await self._access.visible_tag(viewer, local_id) is not None

    async def _file_name(self, viewer: Viewer, asset_id: str) -> str:
        """A file's own name for a row about it: its first location's filename, as History has."""
        where = await self._access.locations(viewer, asset_id)
        return where[0].filename if where else "this file"


class ReconcileReceipts:
    """How to take back a settled disagreement: a `Reverser` with no card on the Organize board."""

    name = NAME
    #: A field written from a stash-box can be put back to what it was.
    reversible = True

    def __init__(self, reconciler: Reconciler) -> None:
        self._reconciler = reconciler

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing: a field is not a picture."""
        _ = (viewer, payload)
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Put back the value that was there before this row was settled.

        Taking the box's value is undone by writing the old one; keeping your own (a payload with
        `box_id`) is undone by forgetting the answer.
        """
        _ = receipt_id
        recorded = _read(payload)
        try:
            # A stored payload may say the retired word for `site`; an undo reads both.
            said = str(recorded.get("subject", ""))
            subject = Subject("site" if said == "platform" else said)
        except ValueError:
            return False
        local_id = str(recorded.get("local_id", ""))
        key = str(recorded.get("key", ""))
        if not local_id or not key:
            return False
        box_id = str(recorded.get("box_id", ""))
        if box_id:
            return await self._reconciler.forget_kept(viewer, subject, local_id, box_id, key)
        if "mine" not in recorded:
            return False
        held = Disagreement(
            subject=subject,
            local_id=local_id,
            name="",
            box_id="",
            box_name="",
            key=key,
            mine=recorded["mine"],
            theirs=recorded["mine"],
        )
        if not await self._reconciler.settle(viewer, held, take_theirs=True):
            return False
        # And every other box the press set aside, each put back (`Reconciler.set_aside_by`).
        for one in _set_aside_of(recorded):
            aside_box, was = one
            if was is None:
                await self._reconciler.forget_kept(viewer, subject, local_id, aside_box, key)
            else:
                await self._reconciler.put_back_kept(viewer, subject, local_id, aside_box, key, was)
        return True

    def worded(self, recorded: Recorded) -> Worded | None:
        """This decision's line, worded from what it recorded. See `_settled_said`."""
        return _settled_said(recorded)


# --- A settled disagreement, worded when it is shown ---------------------------------------------


def _settled_said(recorded: Recorded) -> Worded | None:
    """A settled disagreement as pieces, or None where the row recorded too little to say it.

    An older receipt kept its values in its detail in one of two formats (`_OLD_TOOK`, `_OLD_KEPT`),
    read back strictly.
    """
    held = _read(recorded.payload)
    said = str(held.get("subject", ""))
    try:
        subject = Subject("site" if said == "platform" else said)
    except ValueError:
        return None
    local_id, key = str(held.get("local_id") or ""), str(held.get("key") or "")
    if not local_id or not key:
        return None
    old = _OLD_TITLE.fullmatch(recorded.title)
    # No recorded name: an old title's "for <name>" sometimes named the box's offer.
    thing = Named(kind=subject.value, id=local_id)
    (words,) = said_plainly(subject, [key])
    if "box_id" in held:
        return _kept_said(held, recorded, subject, local_id, key, thing, words)
    return _took_said(held, recorded, subject, key, thing, words, old)


def _kept_said(
    held: dict[str, Any],
    recorded: Recorded,
    subject: Subject,
    local_id: str,
    key: str,
    thing: Named,
    words: str,
) -> Worded | None:
    """A "kept yours" receipt's line: the box's answer refused for the library's own."""
    box = Named(kind="box", id=str(held["box_id"]))
    if "mine" in held and "theirs" in held:
        mine, theirs = held["mine"], held["theirs"]
    else:
        parsed = _old_values(_OLD_KEPT, recorded.detail, key)
        if parsed is None:
            return None
        mine, theirs = parsed
    if key == "name":
        named = Named(kind=subject.value, id=local_id, recorded=str(mine), as_recorded=True)
        return Worded(
            said=(
                DOER,
                " kept the name ",
                named,
                " over ",
                box,
                "'s ",
                _said(subject, key, theirs),
            )
        )
    values = _both_said(subject, key, mine, theirs)
    if values is None:
        return Worded(said=(DOER, f" kept your {words} for ", thing, " over ", box, "'s"))
    return Worded(
        said=(
            DOER,
            f" kept your {words} for ",
            thing,
            ", ",
            values[0],
            ", over ",
            box,
            "'s ",
            values[1],
        )
    )


def _took_said(
    held: dict[str, Any],
    recorded: Recorded,
    subject: Subject,
    key: str,
    thing: Named,
    words: str,
    old: re.Match[str] | None,
) -> Worded | None:
    """A "took theirs" receipt's line, naming any other box the press set aside."""
    if "mine" not in held:
        return None
    mine = held["mine"]
    if "theirs" in held and held.get("from_box_id"):
        taken: Named = Named(kind="box", id=str(held["from_box_id"]))
        theirs = held["theirs"]
    else:
        parsed = _old_values(_OLD_TOOK, recorded.detail, key)
        if parsed is None or old is None or not old["box"]:
            return None
        _before, theirs = parsed
        taken = Named(kind="box", id="", recorded=old["box"])
    values = _both_said(subject, key, mine, theirs)
    line: tuple[Piece, ...] = (
        (DOER, " chose ", taken, f"'s {words} for ", thing)
        if values is None
        else (
            DOER,
            " chose ",
            taken,
            f"'s {words} for ",
            thing,
            ", ",
            values[1],
            ", over your ",
            values[0],
        )
    )
    aside = [Named(kind="box", id=box_id) for box_id, _was in _set_aside_of(held)]
    if not aside:
        return Worded(said=line)
    more: list[Piece] = []
    for at, one in enumerate(aside):
        if at:
            more.append(" and " if at == len(aside) - 1 else ", ")
        more.append(one)
    more.append(
        "'s answer was discarded too" if len(aside) == 1 else "'s answers were discarded too"
    )
    return Worded(said=line, more=tuple(more))


#: The two formats older receipts kept their values in, each value a `repr` read back with
#: `ast.literal_eval`; every split is tried for a value holding the joining words.
_OLD_TOOK = " became "
_OLD_KEPT = " was kept over "
_OLD_TITLE = re.compile(r"(?:Took (?P<box>.+?)'s answer|Kept your answer) for (?P<name>.+)")


def _old_values(joiner: str, detail: str, key: str) -> tuple[object, object] | None:
    """The two values an old receipt wrote into its detail, or None where it does not read."""
    text = detail.strip()
    if not text.startswith(f"{key}: "):
        return None
    parts = text[len(key) + 2 :].split(joiner)
    for at in range(1, len(parts)):
        left, right = joiner.join(parts[:at]), joiner.join(parts[at:])
        if joiner == _OLD_KEPT:
            _box, apostrophe, right = right.partition("'s ")
            if not apostrophe:
                continue
        try:
            return ast.literal_eval(left), ast.literal_eval(right)
        except (ValueError, SyntaxError):
            continue
    return None


def _both_said(subject: Subject, key: str, mine: object, theirs: object) -> tuple[str, str] | None:
    """Both values as words, or None where either is too long for a line."""
    said = (_said(subject, key, mine), _said(subject, key, theirs))
    return None if any(len(one) > _WORDED_VALUE for one in said) else said


def _said(subject: Subject, key: str, value: object) -> str:
    """One value as the record draws it (`records.value_said`), short enough for a line."""
    said = value_said(subject, key, value)
    if said is not None:
        return said
    if value is None or value == "" or value == []:
        return "nothing"
    if isinstance(value, list | tuple):
        return ", ".join(str(one) for one in value)
    return str(value)


#: The longest a value is said in a worded line, as the stored titles cut (`router._TITLE_VALUE`).
_WORDED_VALUE = 60


def _as_written(value: object) -> str:
    """One side of a disagreement as the text an answer is remembered by: JSON with sorted keys, else
    `repr`.
    """
    try:
        return json.dumps(value, sort_keys=True)
    except TypeError:
        return repr(value)


def _set_aside_of(recorded: dict[str, Any]) -> list[tuple[str, tuple[str, str] | None]]:
    """The boxes a receipt set aside, each with the answer on file before it; unreadable entries
    skipped.
    """
    out: list[tuple[str, tuple[str, str] | None]] = []
    listed = recorded.get("set_aside")
    for one in listed if isinstance(listed, list) else []:
        if not isinstance(one, dict) or not isinstance(one.get("box_id"), str) or not one["box_id"]:
            continue
        was = one.get("was")
        pair = (
            (str(was[0]), str(was[1])) if isinstance(was, list | tuple) and len(was) == 2 else None
        )
        out.append((one["box_id"], pair))
    return out


def _read(payload: str) -> dict[str, Any]:
    """What a decision wrote down, or an empty record when it cannot be read."""
    try:
        recorded: Any = json.loads(payload)
    except ValueError:
        return {}
    return recorded if isinstance(recorded, dict) else {}


#: The three kinds a stash-box can be asked about, by the routes' word for them.
_LINKABLE: dict[str, Subject] = {
    Subject.PERSON.value: Subject.PERSON,
    Subject.SITE.value: Subject.SITE,
    Subject.TAG.value: Subject.TAG,
    # A file, whose link is the answer it was agreed to; without it a file's page would say nothing.
    Subject.ASSET.value: Subject.ASSET,
}

#: The kinds the library-wide survey covers, those with link tables; a file is asked on its page.
_SURVEYED: dict[str, Subject] = {
    kind: subject for kind, subject in _LINKABLE.items() if subject is not Subject.ASSET
}


#: Where two answers about one field disagree, built at the wiring.
RECONCILER: Part[Reconciler] = Part("reconciler")
