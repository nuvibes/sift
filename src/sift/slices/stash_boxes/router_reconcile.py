# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a box and this library disagree about a record, and settling one field of it."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from sift.kernel import wiring
from sift.kernel.access import Viewer
from sift.kernel.access.sentences import text_of
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.records import (
    Subject,
    value_said,
)
from sift.kernel.vocabulary import RECEIPT_KEPT, SubjectKind
from sift.kernel.vocabulary import Subject as DecisionSubject
from sift.kernel.wire import pieces_of
from sift.kernel.wiring import part_of
from sift.slices.auth import csrf_protect, require_admin
from sift.slices.stash_boxes.kept import kept_answers
from sift.slices.stash_boxes.match_words import _settled_line
from sift.slices.stash_boxes.models import (
    DisagreementList,
    DisagreementView,
    Settled,
    SettleDisagreement,
)
from sift.slices.stash_boxes.reconcile import (
    RECONCILE,
    RECONCILER,
    Disagreement,
    Reconciler,
    SetAside,
)
from sift.slices.stash_boxes.router_base import (
    _kept_local,
)
from sift.slices.stash_boxes.service import (
    KeptLocal,
)

router = APIRouter(tags=["stash-boxes"])

#: What a reconciled record is, said in the word the decision RECORD uses for the same thing.
#:
#: Two vocabularies meet here and they are the same six words. `Subject` is what a field can
#: belong to; `SubjectKind` is what an event can be about. Every one maps: the record is the ledger
#: every act is written to, and the whole-install feed draws events that name anything, whether or
#: not the subject has a History screen of its own.
#:
#: The values are tuples so a lookup that finds nothing is an empty answer rather than a None to
#: test for. What a decision was about is a list, and a subject with no word for it contributes
#: none: the same shape as one that contributes one, needing no branch.
_DECISION_SUBJECTS: dict[Subject, tuple[SubjectKind, ...]] = {
    Subject.ASSET: ("asset",),
    Subject.PERSON: ("person",),
    Subject.USERNAME: ("username",),
    Subject.SITE: ("site",),
    Subject.TAG: ("tag",),
    Subject.PHOTO_SET: ("photo_set",),
}

# --- where two answers disagree ---------------------------------------------------------------


def _reconciler(request: Request) -> Reconciler:
    return part_of(request, RECONCILER)


def _value_in_words(subject: Subject, key: str, value: object) -> str | None:
    """One side of a disagreement as the record's own lines say it: `records.value_said`, the one
    rule, handed the value as the JSON text it reads (a decoded "null" or "123" is not text it can
    tell from a number). A list is said item by item, since a line says one value and a table cell
    may hold several."""
    if isinstance(value, list | tuple):
        return (
            ", ".join(said for one in value if (said := _value_in_words(subject, key, one))) or None
        )
    return value_said(subject, key, json.dumps(value, default=str))


def _disagreement_view(one: Disagreement) -> DisagreementView:
    """The one wire shape of a disagreement, for the survey and for one record's panel alike."""
    return DisagreementView(
        subject=one.subject,
        local_id=one.local_id,
        name=one.name,
        box_id=one.box_id,
        box_name=one.box_name,
        key=one.key,
        mine=one.mine,
        theirs=one.theirs,
        mine_said=_value_in_words(one.subject, one.key, one.mine),
        theirs_said=_value_in_words(one.subject, one.key, one.theirs),
    )


@router.get("/stash-boxes/disagreements")
async def disagreements(
    reconciler: Annotated[Reconciler, Depends(_reconciler)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> DisagreementList:
    """Every field a linked stash-box disagrees with, worked out now rather than stored.

    A conflict is not an event: it is the state of two values, and either can change under it. A
    stored row would describe a disagreement that ended a week ago, and the whole job of this screen
    is to be true right now.
    """
    return DisagreementList(
        disagreements=[_disagreement_view(one) for one in await reconciler.disagreements(viewer)]
    )


@router.get("/stash-boxes/disagreements/{subject}/{local_id}")
async def disagreements_of(
    subject: str,
    local_id: str,
    reconciler: Annotated[Reconciler, Depends(_reconciler)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> DisagreementList:
    """The same question asked about ONE record, which is the only place it can be answered.

    The route above surveys everything that has ever been linked, which grows with the library;
    a record's page needs at most three rows. This asks what the page is about. See
    `Reconciler.for_subject`, where the cost is written down.

    An empty list for a record with nothing waiting, and the SAME empty list for a kind no box can
    know and for one this user may not be shown. That is deliberate: a route that told an admin
    apart from a stranger by the shape of its refusal would be a way of asking whether a record
    exists. The MARK on the tab strip needs the difference and reads it off the counts route, which
    answers about a strip this user is already being shown.
    """
    waiting = await reconciler.for_subject(viewer, subject, local_id)
    return DisagreementList(disagreements=[_disagreement_view(one) for one in waiting or []])


@router.post("/stash-boxes/disagreements/settle", dependencies=[Depends(csrf_protect)])
async def settle_disagreement(
    body: SettleDisagreement,
    request: Request,
    reconciler: Annotated[Reconciler, Depends(_reconciler)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Settled:
    """Take one of the two answers for one field.

    The row is looked up again rather than trusted from the body: a client naming a subject, a
    field and a value could otherwise write anything into any record. A receipt is written for
    either answer and records what it was, so the undo is exact. `fields` counts the fields
    written, nought for a keep.
    """
    # Kept local first, with its own words: taking a box's value is applying an answer, which
    # "Do not enrich" refuses. Keeping your own writes nothing a box said.
    if body.take_theirs:
        try:
            await reconciler.may_take(viewer, body.subject, body.local_id)
        except KeptLocal:
            raise _kept_local() from None
    found = await _waiting_row(reconciler, viewer, body)
    if not await reconciler.settle(  # pragma: no cover (the row was resolved a line ago)
        viewer, found, take_theirs=body.take_theirs
    ):
        return Settled()
    # Every OTHER box offering a different value for this field leaves with the pressed row when
    # the box's answer is taken, in the same transaction as the receipt whose Undo brings them back.
    aside = await reconciler.set_aside_by(viewer, found) if body.take_theirs else []
    line, detail = _settled_line(found, take_theirs=body.take_theirs, aside=aside)
    title = text_of(line)
    written = _receipt_of(found, take_theirs=body.take_theirs, aside=aside)
    recorder = part_of(request, wiring.RECORDER)
    # `telling` rather than a bare write: the set-aside answers change what the disagreement panel
    # lists on every other tab open on this record.
    async with telling(part_of(request, wiring.DATABASE), EVERY_ADMIN, About.LIBRARY) as connection:
        await reconciler.set_aside_on(connection, found, aside)
        decision = await recorder.record_on(
            connection,
            queue=RECONCILE,
            user_id=viewer.id,
            title=title,
            detail=detail,
            payload=json.dumps(written),
            subjects=[
                DecisionSubject(kind=kind, id=found.local_id)
                for kind in _DECISION_SUBJECTS.get(found.subject, ())
            ],
        )
    fields = 1 if body.take_theirs else 0
    return Settled(fields=fields, decision_id=decision, said=title, pieces=pieces_of(line))


async def _waiting_row(
    reconciler: Reconciler, viewer: Viewer, body: SettleDisagreement
) -> Disagreement:
    """The row the body names, looked up among this record's rows, or the 404 for one not waiting.

    The box as well as the field: a record two boxes disagree about has two rows under one field.
    """
    found = next(
        (
            one
            for one in await reconciler.for_subject(viewer, body.subject, body.local_id) or []
            if one.key == body.key and one.box_id == body.box_id
        ),
        None,
    )
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "that disagreement isn't waiting")
    return found


def _receipt_of(
    found: Disagreement, *, take_theirs: bool, aside: Sequence[SetAside]
) -> dict[str, object]:
    """What the decision carries so it can be taken back.

    Taking names the value it REPLACED and the box it came from (`from_box_id`); keeping names the
    BOX whose answer was refused. The kept answers ride under the key the History threads read.
    """
    recorded: dict[str, object] = (
        {
            "mine": found.mine,
            "theirs": found.theirs,
            "from_box_id": found.box_id,
            "set_aside": [
                {"box_id": one.box_id, "was": list(one.was) if one.was else None} for one in aside
            ],
        }
        if take_theirs
        else {"box_id": found.box_id, "mine": found.mine, "theirs": found.theirs}
    )
    written = {
        "subject": found.subject.value,
        "local_id": found.local_id,
        "key": found.key,
        **recorded,
    }
    written[RECEIPT_KEPT] = kept_answers(written)
    return written
