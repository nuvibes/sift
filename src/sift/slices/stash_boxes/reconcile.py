# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a stash-box disagrees with what this library already says.

A queue of FIELDS rather than of subjects, and that distinction is the whole screen. "StashDB has
something to say about Jane" is not a question anybody can answer; "StashDB says 1991 and you have
1990" is. So what waits here is one row per disagreeing field, with both values on it and the box
that said so, and answering one row settles one field.

Nothing arrives here by accident. A disagreement exists only where somebody has already agreed
that a box knows this subject, and only where the rule for that field is the one that asks rather
than overwrites. Turn a field to Replace and its disagreements stop being questions; turn it to
Leave alone and they never were.

## Why it is worked out rather than stored

There is no conflicts table. A conflict is not an event but the state of two values, and either
can change under it: somebody edits the record, the box is asked again, the rule for that field
changes. A stored row would describe a disagreement that is already over.

The answer is stored, and that is a different thing. Taking the box's value writes the field, so
the disagreement ends because the library moved. Keeping your own writes nothing to the record, so
one row is kept per answered field carrying the two values it was an answer to (`stash_box_kept`),
or the next read would work the same conflict out again. The conflict is never stored; a judgement
about a pair of values is.

## What it costs

The survey is bounded by what has been linked, and links are made by a bulk pass as well as by
hand, so it grows with the library: about a thousand links take seconds even with visibility read
in one statement (`_disagreements`), because each link still costs one enrichment plan. So a
record's own page asks `for_subject`: one seeking read of the link table, one of the answers
already given about it, and one plan per box that knows it, three at most. The survey stays for
the Stash-boxes pane, which wants every row. Both come out of `_conflicts`, so there is one rule
for what a disagreement is.
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
    """Another box's answer to a field, set aside because somebody took a DIFFERENT box's answer.

    `mine` is the value the library holds now (the one just taken) and `theirs` is this box's,
    which is the pair its row would ask about. `was` is the answer already on file for this box
    and field before the press, if there was one, so an Undo puts THAT back rather than forgetting
    a judgement somebody made on another day.
    """

    box_id: str
    box_name: str
    mine: object
    theirs: object
    was: tuple[str, str] | None


class Reconciler:
    """Finds every field where a kept stash-box record and this library disagree.

    Built at the wiring because it needs three things from three places: the boxes' own kept
    records, the rules somebody chose per field, and the writers that own each kind of subject.
    """

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
        # The answer, kept under the change mark it was computed at (`MarkedMemo`): planning an
        # enrichment per linked subject grows with the links, for a number that moves only when
        # something in the library does.
        # It rarely answers on a busy library: the mark moves on every announcement anywhere (a
        # scan writing a file, a view being recorded), so with background work in flight two asks
        # a minute apart see two different marks. The memo is not a reason for the computation to
        # be allowed to be expensive.
        self._kept: MarkedMemo[list[Disagreement]] = MarkedMemo()

    async def disagreements(self, viewer: Viewer) -> list[Disagreement]:
        """Every field a linked box disagrees with, as this user may be told about it.

        Answered from memory while the library stands still: the mark moves on every announced
        change, and a different mark is a different library. Kept per user, because the answer
        is scoped to who is asking.
        """
        return await self._kept.get(viewer.id, current_mark(), lambda: self._disagreements(viewer))

    async def _disagreements(self, viewer: Viewer) -> list[Disagreement]:
        """The same, computed. See `disagreements` for why it is not called on every ask.

        Scoped, and it matters more here than on most screens: a row carries a person's NAME and
        their birth date side by side, which is the most identifying pair in the database. A subject
        this user may not be shown produces no row at all.
        """
        rules: dict[Subject, dict[str, Strategy]] = {}
        # Once, not per row: a screen with forty disagreements would otherwise read the whole
        # `stash_boxes` table forty times to print the same three names.
        names = {box.id: box.name for box in await self._service.boxes()}
        # Every answer somebody has already given by keeping their own value. Once, not per row,
        # for the reason `names` above is read once: this is a whole-table read of a small table,
        # and asking it per conflict would be a per-link read, the cost this survey exists to
        # avoid, made again one level down.
        already = await self._service.kept_answers()
        linked = await self._service.linked_subjects()
        # Every linked person's visibility in one read. Narrowing the People wall to one id does
        # not make it cheaper: the by-id condition sits after the counts, so resolving one person
        # costs about what resolving all of them costs. `visible_people` is that statement asked
        # once.
        # A person absent from the answer is one this user may not be shown, which is the same
        # thing `visible_person` returning None means. See `Repository.visible_people`, where
        # absent covers "not allowed" and "not there" together.
        people = await self._access.visible_people(
            viewer, [local_id for subject, local_id, _b, _r in linked if subject is Subject.PERSON]
        )
        # Sites and tags the same way. A Site's by-id read costs about what the whole wall does,
        # so one per link would be a large share of this survey wherever many Sites are linked.
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
        # What the library holds for every linked subject, read per kind rather than per link:
        # one read per table for people, whose links are most of any library's. Read per link, the
        # round trips would be the whole of this survey's cost (seconds on a thousand links).
        kinds = dict.fromkeys(subject for subject, _l, _b, _r in linked)
        held = {
            kind: await self._enricher.currents(
                kind, [local_id for subject, local_id, _b, _r in linked if subject is kind]
            )
            for kind in kinds
        }
        found: list[Disagreement] = []
        for subject, local_id, box_id, record in linked:
            # Every kind a link is kept for is read above; a kind without a read is shown nobody.
            if local_id not in seen.get(subject, {}):
                continue
            # KEPT LOCAL IS NOT A QUESTION. Either answer to a disagreement on something kept local
            # is refused or means nothing (taking the box's value is applying a stored answer,
            # which `StashBoxService.nothing_applied` refuses, and keeping yours is what already
            # happens), so a row for it would be a question with no answer. Asked through the
            # door's own predicate, a point read, so the survey and the refusal cannot disagree.
            if await self._service.kept_local(subject, local_id):
                continue
            if subject not in rules:
                rules[subject] = await strategies_for(self._settings, subject)

            # The library-wide answers, asked the way `_conflicts` asks: by box and field, about the
            # row in hand. The subject and the record ride in as defaults rather than being closed
            # over, because a closure made in a loop reads whatever the loop variable holds when it
            # is finally called, which is the last row every time.
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

        ## Why it is one record's work and not the library's

        `disagreements` above surveys everything that has ever been linked, which grows with the
        library (see the module header). This asks the narrow question the page has: one
        link-table read that seeks, one read of the answers already given about this record, and one
        enrichment plan per box that knows it, three at most because that is how many a link table
        can hold per subject.

        It is the same rule, not a second copy: every row either list produces comes out of
        `_conflicts`, so a field that is a question on the panel is counted by the mark beside the
        tab, and a settled field by neither.

        ## The three answers that are None rather than an empty list

        Empty means this record was asked about and nothing disagrees. None means there is no
        question to ask at all, which is a different fact and is what the strip draws no mark for,
        the same thing it does for a wall a page has not got. Three cases reach it: a user that
        is not an admin, because what a box wrote is an admin's to settle and every control that
        settles it is behind that door; a kind no stash-box can know, which is a collection or a
        Photo Set; and a record this user may not be shown, which is the answer an id that was
        never minted gets too.
        """
        if not viewer.is_admin:
            return None
        kind = _LINKABLE.get(subject)
        if kind is None:
            return None
        if not await self._visible(viewer, kind, local_id):
            return None
        # Asked about and nothing to settle, for the reason the survey above gives: a record kept
        # local has no box answer that may land on it, so it has no disagreement either.
        if await self._service.kept_local(kind, local_id):
            return []
        # A FILE's link is the answer it was agreed to. See `StashBoxService.agreed_answers` for
        # why that is the same statement and not a second table saying it.
        if kind is Subject.ASSET:
            links = await self._service.agreed_answers(local_id)
        else:
            links = await self._service.links_of(kind, local_id)
        if not links:
            return []
        strategies = await strategies_for(self._settings, kind)
        answered = await self._service.kept_answers_for(kind.value, local_id)
        # What the row is called. A person's row is named by the box's record, their name as the box
        # spells it. A file is named by its own filename (`history._file_named`), not by the box's
        # title, which is the very value a title disagreement is about.
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
            # THE FILE'S OWN COLUMNS ONLY, because those are the fields "Take theirs" can honour
            # with a plain write. A file's SITE is the other single-valued field a box can disagree
            # about, and it is not a column: the writer files the file under the box's site only
            # where that site already exists here, and ADDS the filing rather than replacing the
            # one held, so a row offering it would be a button that, on most libraries, does
            # nothing, or files the file under two sites. Which of those a person means is a
            # decision about filing, not about this panel.
            found = [one for one in found if one.key in COLUMNS]
        return found

    async def disagreement_count(self, viewer: Viewer, subject: str, local_id: str) -> int | None:
        """How many of them there are. The `DisagreementSeam`, and the mark beside the History tab.

        The length of `for_subject`'s own answer rather than a statement that counts, the rule the
        counts route keeps for every number on that strip: a number taken from a different question
        than the list under it can stop agreeing with it.

        None passes straight through, and it has to: the strip draws no mark for a record there is
        no question to ask about, and a nought there would be a claim that somebody had looked.
        """
        waiting = await self.for_subject(viewer, subject, local_id)
        return None if waiting is None else len(waiting)

    async def disagreement_mark(
        self, viewer: Viewer, subject: str, local_id: str
    ) -> tuple[int | None, list[str]]:
        """The count and the boxes it is about, by name, from one `for_subject` answer, so the mark
        names only boxes the panel shows. None and no boxes where `disagreement_count` is None."""
        waiting = await self.for_subject(viewer, subject, local_id)
        if waiting is None:
            return None, []
        return len(waiting), list(dict.fromkeys(one.box_name for one in waiting if one.box_name))

    async def subjects_with_disagreements(
        self, viewer: Viewer, subject: str
    ) -> tuple[str, ...] | None:
        """Every record of one KIND with a question waiting on it. The wall facet, and its filter.

        ## Why it is the survey and not `for_subject` in a loop

        `for_subject` once per row of a wall would be one link-table seek, one answer read and one
        enrichment plan per person. The survey above already asks the wide question the wide way
        (one read of each link table, one of every answer ever kept, one of every linked person's
        visibility), so this is answered from `disagreements` and shares the memo the Stash-boxes
        pane fills. The panel, the mark beside the tab and the facet all count what `_conflicts`
        produced.

        It costs what the survey costs (about a second cold on a library with a thousand links,
        milliseconds from the memo), so it is asked only where somebody has opened this column or
        filtered on it (see the routes) and never on an ordinary page of a wall. A survey
        narrowed to one kind would be a second statement and a second memo for the same question.

        The ids come back in the order the survey found them, deduplicated: a record disagreeing
        about three fields is one row of a wall, not three.

        None where there is no question to ask, and it is the same three cases `for_subject` names,
        less the one about a single record: a user that is not an admin, and a kind no stash-box
        has ever heard of.
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
        """What one box and one record disagree about. The one rule, for both readers.

        `name` is what the rows call the record, where the box's name for it is the wrong word:
        a file, whose record the box names by the title under dispute. See `for_subject`.

        `mine` is what the library holds, where the caller has read it already (the survey reads
        every linked subject of a kind at once); absent, it is read here.


        `answered` is asked by box and field rather than handed in as a mapping, because the two
        callers hold that judgement under two different keys: the survey has every answer in the
        library under a four-part key, and the count has this record's under a two-part one.
        Re-keying either to suit the other would be a pass over the wrong table at the wrong moment.

        A plan the enricher will not make is not a disagreement. That is the case where nothing is
        linked, or where every field's rule is Replace or Leave alone. Turn a field to Replace and
        its disagreements stop being questions, which is the module header's own sentence.
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
            # Already answered, and answered about THESE two values. A field whose answer was
            # "keep mine" is settled for as long as neither side moves; the moment either does
            # it is a different question and comes straight back, which is what storing the
            # pair rather than the field buys.
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

        Asked by the settle route before it looks the row up: the survey lists no disagreement on
        something kept local, so a press from a panel drawn a minute earlier would otherwise find no
        row and answer "not waiting", which does not say why the question went. A kind no box can
        know, or a
        record this user may not be shown, raises nothing here; the lookup after it answers those
        the way it always has, so this cannot be used to ask whether a record exists.
        """
        kind = _LINKABLE.get(subject)
        if kind is None or not await self._visible(viewer, kind, local_id):
            return
        await self._service.nothing_applied(kind, local_id)

    async def settle(self, viewer: Viewer, one: Disagreement, *, take_theirs: bool) -> bool:
        """Take one of the two answers. Keeping your own writes the answer and not the field.

        Keeping what is already there is the commonest answer, and the field is not re-written: a
        field written back with the value it already holds is a change in every log that watches
        for one. But the row is worked out from the two values on every read, so the answer is
        recorded with the pair of values it was an answer to, or the row would come straight back.
        See `_CREATE_KEPT` in the schema: the conflict is never stored, the judgement about it is.
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
            # The memo is now describing a library this very request has changed. Thrown away here
            # rather than left for the change mark to move, because nothing announces a row in this
            # table, and because the caller re-reads IMMEDIATELY: the panel asks again the moment
            # the button returns, which is well inside the window where the mark has not moved.
            self._kept.forget()
            return True
        # A box's value is a stored answer, so it is refused on a record kept local whoever asks.
        # The route asks first to say so; this is the writer's own check, so no caller can skip it.
        await self._service.nothing_applied(one.subject, one.local_id)
        # The USER who pressed it, not the box that offered the value.
        wrote = await self._enricher.write_one(
            one.subject, one.local_id, one.key, one.theirs, actor=Actor.user(viewer.id)
        )
        if wrote and one.subject is not Subject.ASSET:
            # Not for a file: the one line a file gets is the receipt the route writes. A file's
            # History draws the box's line off its agreed answer and the box's latest run
            # (`history._ENRICHED`), so a run written here for one field would rewrite that line to
            # say the box filled in only this field. The receipt names the field, both values and
            # the box.
            # A box's value, taken by hand: a run of that box which filled in this one field, and
            # the ledger's `enriched` event beside it, written with the run (`record_enrichment`).
            # This is where "FansDB filled in their birthdate when you applied its answer" comes
            # from. One row: a disagreement is only ever about a field that holds one value.
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
        """Every OTHER box's row about the same field, as it stands after `taken` was written.

        ## Why taking one answer answers the others

        Two boxes can disagree with each other as well as with the library: StashDB says FAKE,
        FansDB says NATURAL. Taking FansDB's value ends FansDB's row and, because "yours" is now
        NATURAL, starts StashDB's, which did not exist a moment before. A pressed row leaves and
        stays gone: choosing FansDB's value is choosing it over StashDB's.

        So this is asked after the write and not before, and that order is the whole of it: the rows
        to set aside are the ones `_conflicts` produces against the value now held, the same rule,
        so the pair each is stored under is exactly the pair its row would be asked about, and the
        row is gone for as long as neither side moves. A different value from either side later is
        a different question and comes back, which is what storing the pair buys.

        What was already on file for each box is read FIRST, so the receipt can put it back.
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
        """Restore the answer that was on file before a press set this box aside. The Undo's half
        for a box that had been answered on another day: forgetting it would lose that day."""
        if not await self._visible(viewer, subject, local_id):
            return False
        await self._service.record_kept(subject.value, local_id, box_id, key, was[0], was[1])
        self._kept.forget()
        return True

    async def forget_kept(
        self, viewer: Viewer, subject: Subject, local_id: str, box_id: str, key: str
    ) -> bool:
        """Take back a "keep mine", so the field is a question again. What the Undo does.

        Scoped like `settle` is, and for the same reason: an undo is a write, and a write about a
        record this user may not be shown is one they may not make. It answers False there rather
        than raising, which is what the reverser reports as a decision it could not take back.
        """
        if not await self._visible(viewer, subject, local_id):
            return False
        await self._service.forget_kept(subject.value, local_id, box_id, key)
        self._kept.forget()
        return True

    async def _visible(self, viewer: Viewer, subject: Subject, local_id: str) -> bool:
        if subject is Subject.ASSET:
            # `open_asset` and not `get_asset`: a concealed file comes back from the second as a
            # locked placeholder with the real row behind it, and a placeholder is not something
            # to read a disagreement off or to write a box's value onto.
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
    """How to take back a settled disagreement. Not a card, and that is the point of it.

    A disagreement is shown on the record it is about, and in full under Settings > Stash-boxes,
    not as a card on the Organize board. But settle receipts are on disk with an Undo, and an Undo
    answering "nothing in this version knows how to take that decision back" would be untrue. So
    the reversal is kept without a card: see `kernel.workbench.Reverser`, which is separate from
    `Queue` for exactly this.
    """

    name = NAME
    #: A field written from a stash-box can be put back to what it was.
    reversible = True

    def __init__(self, reconciler: Reconciler) -> None:
        self._reconciler = reconciler

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing. A field is not a picture, and a row of stills under "1990 or 1991" would be
        decoration standing in for the thing the decision was actually about."""
        _ = (viewer, payload)
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Put back the value that was there before this row was settled.

        Reversible where the merge next door is not: this wrote one field and wrote down what it
        replaced, so putting it back is exact.

        Two kinds of decision arrive here. Taking the box's value wrote a field, and taking it back
        writes the old one. Keeping your own value wrote no field. What it wrote is the answer, so
        taking it back forgets that answer, and the question is waiting again the next time the
        panel is drawn. A payload says which by carrying `box_id`, which only the second
        kind has: the first kind has to name the value it replaced and the second has nothing to
        put back.
        """
        _ = receipt_id
        recorded = _read(payload)
        try:
            # A STORED payload, so it can say either word: every receipt written before
            # 0.1.153 says the retired word where this one says `site`, and an undo reads both.
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
        # And every other box the same press set aside, each put back to what it was: forgotten
        # where nothing was on file, restored where somebody had answered it on another day. One
        # Undo, because it was one press. See `Reconciler.set_aside_by`.
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
#
# The stored titles said "Kept your answer for Ada Byron" (about a person called Esme Wrenfield, since
# Ada Byron was the name the box offered) and "breast_type: 'FAKE' became 'NATURAL'", the field's key
# and the raw values. The line is now worded from the facts: the doer, the field in the record's
# own word, both values as the record draws them, the box, and the thing, named as it is now.


def _settled_said(recorded: Recorded) -> Worded | None:
    """A settled disagreement as pieces, or None where the row recorded too little to say it.

    Two answers and two generations of receipt. The current payload carries both values (`mine`,
    `theirs`) and the box by id: `box_id` for "kept yours", `from_box_id` for "took theirs", plus
    every other box the press discarded. An older receipt carried the field, the thing and one of
    the two (the value replaced, or the box refused), and the rest only in its detail, in one of two
    fixed formats (`_OLD_TOOK`, `_OLD_KEPT`). Those are read back strictly: a detail that does not
    match its format exactly is a row that recorded nothing more, and it keeps its stored words.
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
    # No recorded name for the thing: the old titles' "for <name>" was not always its name: the
    # three "Kept your answer for Ada Byron" named the box's offer, about a person called Esme Wrenfield.
    thing = Named(kind=subject.value, id=local_id)
    (words,) = said_plainly(subject, [key])
    if "box_id" in held:
        box = Named(kind="box", id=str(held["box_id"]))
        if "mine" in held and "theirs" in held:
            mine, theirs = held["mine"], held["theirs"]
        else:
            parsed = _old_values(_OLD_KEPT, recorded.detail, key)
            if parsed is None:
                return None
            mine, theirs = parsed
        if key == "name":
            # The name IS the fact: say the one kept, with the thing it names now after it.
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


#: The two formats older receipts kept their values in, and the title's.
#:
#: "<key>: <before> became <after>" for a box's answer taken, and "<key>: <mine> was kept over
#: <Box>'s <theirs>" for your own kept. The writer printed each value with Python's `repr`
#: (`'FAKE'`, `177`, `'34-B-22-33'`), so each side is read back with `ast.literal_eval`, which
#: evaluates a literal and nothing else. A value that itself contains the joining words makes the
#: split ambiguous; every split is tried and the one that reads as two literals is taken. None is a
#: row with nothing more recorded, and it keeps its stored words.
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
            # "<Box>'s <theirs>": the box's name, then its value.
            _box, apostrophe, right = right.partition("'s ")
            if not apostrophe:
                continue
        try:
            return ast.literal_eval(left), ast.literal_eval(right)
        except (ValueError, SyntaxError):
            continue
    return None


def _both_said(subject: Subject, key: str, mine: object, theirs: object) -> tuple[str, str] | None:
    """Both values as words, or None where either is too long for a line: a description the
    length of a paragraph. The line then names the field and the thing and leaves the values to the
    record, which draws them whole."""
    said = (_said(subject, key, mine), _said(subject, key, theirs))
    return None if any(len(one) > _WORDED_VALUE for one in said) else said


def _said(subject: Subject, key: str, value: object) -> str:
    """One value as the record draws it, short enough for a line.

    The one rule for showing a stored value is `records.value_said` (a stash-box's constant said
    as a word, a length in centimeters), so the kept-answer line on a page and a decision worded
    here cannot come to say one value two ways. What that rule declines (a
    list, a bool, nothing) is said here: nothing is "nothing", a list is its items. Cut at the
    receipt title's own limit by the caller, because a description the length of a paragraph is
    not a line.
    """
    said = value_said(subject, key, value)
    if said is not None:
        return said
    if value is None or value == "" or value == []:
        return "nothing"
    if isinstance(value, list | tuple):
        return ", ".join(str(one) for one in value)
    return str(value)


#: The longest a value is said in a worded line. The same limit the stored titles cut at
#: (`router._TITLE_VALUE`), for the same reason: a line is one line.
_WORDED_VALUE = 60


def _as_written(value: object) -> str:
    """One side of a disagreement as the text an answer is remembered by.

    JSON with its keys in order, so the same value always writes the same string: the comparison
    that decides whether a question has already been answered is on this text, and a mapping whose
    keys came out in a different order would read as a different value and put the row back.

    Anything JSON cannot carry is written as its `repr`. That is a shape nothing here has ever
    produced (a conflict only happens on a field holding ONE value, and every one of those comes
    off a stash-box's JSON), so this is the branch that must never lose the row rather than one
    with a case behind it: a value that cannot be written down is one whose answer cannot be
    remembered, and it is better for the question to come back than for it to be matched by
    accident.
    """
    try:
        return json.dumps(value, sort_keys=True)
    except TypeError:
        return repr(value)


def _set_aside_of(recorded: dict[str, Any]) -> list[tuple[str, tuple[str, str] | None]]:
    """The boxes a receipt set aside, each with the answer on file before it. An older receipt has
    none, and an entry this cannot read is skipped rather than guessed at."""
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


#: The three kinds a stash-box can be asked about, under the word the strip and the routes use.
#:
#: A mapping rather than a membership test, because the caller hands in a STRING (the related
#: route's own word for the kind) and what is wanted back is the enum every read below is keyed
#: by. A collection and a Photo Set are absent and that is the whole of the answer for them: they
#: are Sift's own arrangements and no stash-box has ever heard of one.
_LINKABLE: dict[str, Subject] = {
    Subject.PERSON.value: Subject.PERSON,
    Subject.SITE.value: Subject.SITE,
    Subject.TAG.value: Subject.TAG,
    # A FILE, whose "link" is the answer it was agreed to. See `StashBoxService.agreed_answers`.
    # Without it, a box disagreeing with a file's title or date would be worked out by the plan
    # that applied the answer and dropped, and the file's page would say nothing at all.
    Subject.ASSET.value: Subject.ASSET,
}

#: The kinds the library-wide SURVEY covers: the three with link tables. A file is asked about on
#: its own page only. Surveying every agreed file answer would be one enrichment plan per matched
#: file (a library's worth) on a read the Stash-boxes pane and the walls' facet make, for a kind
#: no wall has a facet for.
_SURVEYED: dict[str, Subject] = {
    kind: subject for kind, subject in _LINKABLE.items() if subject is not Subject.ASSET
}


#: Where two answers about one field disagree. Its own part rather than a field on the service,
#: because it needs the writers and the rules as well as the boxes: three things from three
#: places, which is what the wiring is for.
RECONCILER: Part[Reconciler] = Part("reconciler")
