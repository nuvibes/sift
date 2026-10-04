# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proposed shoots as work waiting on somebody, and taking either decision back.

**A judgement a threshold cannot settle**, which is what the board's first rule asks of a queue. The
threshold decides which pictures LOOK like one sitting; whether they ARE one (whether a run of a
creator's pictures is a shoot or a month of similar posts) is not something a distance can answer,
and the measurement behind the distance says so plainly (see `clustering.DISTANCE`).

Two kinds of receipt come out of this pile and the reverser reads both, because they are two
different decisions about the same pictures: one grouped them, the other named them. Undoing either
leaves the other standing.
"""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from sift.kernel.access import Viewer
from sift.kernel.access.history import Link
from sift.kernel.log import get_logger
from sift.kernel.workbench import (
    ASSET,
    DOER,
    Band,
    Named,
    Preview,
    Recorded,
    Summary,
    Worded,
)
from sift.slices.shoots.service import QUEUE, ShootService

log = get_logger(__name__)

#: How many pictures a card draws. Enough to recognise a sitting; not the whole shoot.
PREVIEW = 4


class ShootQueue:
    """Shoots waiting to be agreed, refused, or left alone."""

    name = QUEUE
    title = "Shoots"
    #: What is here is a judgement: the rule says these pictures look alike, and only a person can
    #: say whether that makes them one sitting.
    band = Band.DECISION
    #: Stands alone. A shoot is not an alternative reading of anything else on the board.
    group = None
    group_title = None
    #: What the card on the board is for. See `Queue.purpose`.
    purpose = "Photos that look like one shoot, for you to create a Photo Set from."
    #: Both of its decisions can be taken back: a grouping is deleted, a naming is detached.
    reversible = True

    def __init__(self, service: ShootService) -> None:
        self._service = service

    async def available(self) -> bool:
        """Always.

        A card hidden until the pass has run (`total > 0`) is only right if something runs the pass;
        the card must never depend on a pass that nothing queues. The pass runs on every scan (see
        the scan's `settles_into`), which is what makes always the honest answer rather than merely
        the discoverable one: a library that has been scanned has been LOOKED at, so a zero here
        says "nothing found" (the state this screen is trying to reach), and the shoots task's
        own Run now under `Settings > Tasks` looks again. It is the same answer the duplicates and
        folders cards give, for the same reason: looking needs nothing switched on.
        """
        return True

    async def survey(self, viewer: Viewer) -> Summary:
        proposals, total = await self._service.waiting(limit=1)
        first = proposals[0] if proposals else None
        shown: tuple[Preview, ...] = ()
        if first is not None:
            full = await self._service.one(first.id)
            if full is not None:
                # Only the pictures THIS viewer may be shown: the proposal's own list is the
                # library's, from a pass that ran for nobody in particular, and a still of a file
                # in a locked vault would be the vault's contents drawn on the board.
                allowed = await self._service.visible_of(viewer, full.asset_ids)
                shown = tuple(
                    Preview(kind=ASSET, id=one, href=f"/asset/{one}")
                    for one in full.asset_ids
                    if one in allowed
                )[:PREVIEW]
        return Summary(
            name=QUEUE,
            title=self.title,
            verb="shoots to review",
            verb_one="shoot to review",
            decision=(
                "Confirm that a run of one creator's photos is one shoot, and Sift creates a "
                "Photo Set from it. These photos look alike; you decide whether they are one shoot."
            ),
            icon="photo_library",
            count=total,
            preview=shown,
        )

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """A few stills of what one decision was about, scoped to whoever is reading the record.

        Scoped rather than shown: a file restricted since the decision is one this user may no
        longer be shown, and having grouped it is not a licence to draw it.
        """
        wanted = _record(payload)[2][:PREVIEW]
        if not wanted:
            return ()
        allowed = await self._service.visible_of(viewer, wanted)
        return tuple(
            Preview(kind=ASSET, id=one, href=f"/asset/{one}") for one in wanted if one in allowed
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Put back what one decision did, whichever of the two it was.

        Every field is reached for rather than assumed. A record can outlive the version that wrote
        it, and a payload missing a key it once had is a decision this cannot reverse: answering
        "nothing was put back" is the honest reading, where reaching straight in would fail the
        request and read as Undo being broken.
        """
        kind, subject, assets = _record(payload)
        if kind == "shoot" and subject:
            return await self._service.take_back(subject, by=viewer)
        if kind == "named" and subject and assets:
            return await self._service.unname(subject, assets, proposal_id=_proposal(payload))
        return False

    def worded(self, recorded: Recorded) -> Worded | None:
        """This decision's line, worded from what it recorded. See `kernel.workbench.Recorded`.

        The stored titles said "Made a Photo Set for Cassia Lynn", and "made" is not a word this
        application uses for creating something. Both kinds of decision here recorded what they
        did with, and to how many pictures: the Photo Set (by id, and by the name it was given,
        where that was written down) and the files; the person and the files for a naming.
        """
        kind, subject, assets = _record(recorded.payload)
        if not subject or not assets:
            return None
        photos = "1 photo" if len(assets) == 1 else f"{len(assets):,} photos"
        if kind == "shoot":
            called = _called(recorded.payload)
            named = Named(kind="photo_set", id=subject, recorded=called, kind_said=True)
            return Worded(said=(DOER, " created ", named, f" from {photos}"))
        if kind == "named":
            person = Named(kind="person", id=subject)
            return Worded(said=(DOER, " named ", person, f" in {photos} of one shoot"))
        return None


class Asked(NamedTuple):
    """The two sentences a shoot is asked in, and the things they name."""

    question: str
    detail: str
    #: EVERY THING THE TWO SENTENCES NAME, and where it lives: today the one person the shoot is
    #: of, so the name is a link to them. Sent beside the words rather than spliced into them, the
    #: arrangement a history line already has: the name here is the same run of characters the
    #: sentences carry, so a client finds it and links it (`sentenceParts`), and a client that draws
    #: no links reads the same words. Written HERE, by the function that writes the name into the
    #: sentences, so the two cannot come to disagree. The card links it in the QUESTION only and
    #: draws `detail` plain: the same name twice on one card would be two links to one page.
    names: tuple[Link, ...]


def asking(person_id: str, name: str, pictures: int, unnamed: int) -> Asked:
    """The question a shoot asks, the line under it, and who they name.

    A card on the Shoots page asks "Do these 13 photos of X belong together?" over "13 photos of X
    that are in no Photo Set". The server words it, off numbers the caller has already scoped to
    what this viewer may be shown (see the router's list), rather than the page composing a copy of
    its own in the browser.

    A shoot with no person or no name names nobody, and its words are drawn plain: a link with an
    empty id goes to a page that does not exist, and an empty name matches nothing in a sentence.
    """
    files = "photo" if pictures == 1 else "photos"
    question = f"Do these {pictures} photos of {name} belong together?"
    detail = f"{pictures} {files} of {name} that are in no Photo Set"
    if unnamed:
        detail += f" \u2014 {unnamed} of them with no person named"
    names = (Link(kind="person", id=person_id, name=name),) if person_id and name else ()
    return Asked(question, detail, names)


def _called(payload: str) -> str | None:
    """The name a Photo Set was given, where the decision wrote it down. Older ones did not."""
    try:
        found: Any = json.loads(payload)
    except (TypeError, ValueError):
        return None
    name = found.get("name") if isinstance(found, dict) else None
    return str(name) if isinstance(name, str) and name else None


def _proposal(payload: str) -> str:
    """The shoot a naming was made from, or nothing for a record written before it was kept.

    Asked only of a payload `_record` has already read as a naming, so it is an object.
    """
    found: Any = json.loads(payload)
    return str(found.get("proposal_id") or "")


def _record(payload: str) -> tuple[str, str, list[str]]:
    """One decision read back out of its own record: which kind, what it was about, which files.

    ONE parse for both callers rather than one each, and one place that knows what an unreadable
    record answers. The middle value is the Photo Set for a grouping and the person for a naming:
    the two decisions have one subject each and it is never both.
    """
    try:
        found: Any = json.loads(payload)
    except (TypeError, ValueError):
        return "", "", []
    if not isinstance(found, dict):
        return "", "", []
    kind = str(found.get("kind") or "")
    subject = str(found.get("photo_set_id") or found.get("person_id") or "")
    assets = found.get("assets")
    return kind, subject, [str(one) for one in assets] if isinstance(assets, list) else []
