# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proposed shoots as work waiting on somebody; a grouping and a naming are undone apart."""

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
    #: A distance says pictures look alike; only a person can say they are one sitting.
    band = Band.DECISION
    group = None
    group_title = None
    purpose = "Photos that look like one shoot, for you to create a Photo Set from."
    reversible = True

    def __init__(self, service: ShootService) -> None:
        self._service = service

    async def available(self) -> bool:
        """Always: every scan runs the pass, so a zero means nothing was found."""
        return True

    async def survey(self, viewer: Viewer) -> Summary:
        proposals, total = await self._service.waiting(limit=1)
        first = proposals[0] if proposals else None
        shown: tuple[Preview, ...] = ()
        if first is not None:
            full = await self._service.one(first.id)
            if full is not None:
                # The proposal is the library's, so only what this viewer may be shown is drawn.
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
        """A few stills of what one decision was about, scoped to whoever is reading the record."""
        wanted = _record(payload)[2][:PREVIEW]
        if not wanted:
            return ()
        allowed = await self._service.visible_of(viewer, wanted)
        return tuple(
            Preview(kind=ASSET, id=one, href=f"/asset/{one}") for one in wanted if one in allowed
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Undo either kind of decision; a payload missing a key answers False."""
        kind, subject, assets = _record(payload)
        if kind == "shoot" and subject:
            return await self._service.take_back(subject, by=viewer)
        if kind == "named" and subject and assets:
            return await self._service.unname(subject, assets, proposal_id=_proposal(payload))
        return False

    def worded(self, recorded: Recorded) -> Worded | None:
        """This decision's line, worded from what it recorded. See `kernel.workbench.Recorded`."""
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
    #: Written beside the name in the sentences so the two cannot disagree; linked in `question`
    #: only, as one name twice on a card would be two links to one page.
    names: tuple[Link, ...]


def asking(person_id: str, name: str, pictures: int, unnamed: int) -> Asked:
    """The question a shoot asks, the line under it, and who they name, worded here."""
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
    """The shoot a naming was made from, or nothing for an older record."""
    found: Any = json.loads(payload)
    return str(found.get("proposal_id") or "")


def _record(payload: str) -> tuple[str, str, list[str]]:
    """One decision's record read back: kind, subject (Photo Set or person) and files."""
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
