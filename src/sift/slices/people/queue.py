# SPDX-License-Identifier: AGPL-3.0-or-later
"""Usernames nobody has said who they belong to, as work waiting on somebody."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from sift.kernel.access import Repository, UsernameSuggestion, Viewer
from sift.kernel.access.sentences import username_opens
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.kernel.workbench import ASSET, Band, Preview, Recorder, Summary
from sift.slices.people.service import JoinReceipt, PeopleService

log = get_logger(__name__)

#: Also the queue's address on the client.
NAME = "usernames"

#: Enough to recognise what a pile is about.
PREVIEW = 4

Touched = Callable[[Sequence[str]], Awaitable[None]]


class UsernameQueue:
    name = NAME
    title = "Usernames to assign"
    #: What is left is undecidable by rule: two people answer to the spelling, or nobody does.
    band = Band.DECISION
    group = None
    group_title = None
    purpose = "Usernames without a person, for you to choose who posts under them."
    reversible = True

    def __init__(self, service: PeopleService, access: Repository, *, touched: Touched) -> None:
        self._service = service
        self._access = access
        # Required: an undo without it leaves the search index answering the person for the name.
        self._touched = touched

    async def available(self) -> bool:
        """Whether this install has any username at all, so a scanned library shows no panel."""
        return await self._service.any_username()

    async def survey(self, viewer: Viewer) -> Summary:
        # `name_candidates` is drawn per row, not on the card, and costs a subquery per row.
        page = await self._access.list_usernames(viewer, limit=PREVIEW, unattached=True)
        # The newest file under each username this viewer may see; usernames carry no cover.
        newest = await self._access.newest_under_usernames(viewer, [one.id for one in page.items])
        return Summary(
            name=NAME,
            title=self.title,
            verb="usernames without a person",
            verb_one="username without a person",
            # The two kinds of waiting are not counted apart: that is drawn per row.
            decision=(
                "Choose who posts under each username. Sift couldn't decide these: two people "
                "share the name, or no one has it yet."
            ),
            icon="person_add",
            count=page.total,
            preview=tuple(
                _still(newest[one.id], one) for one in page.items[:PREVIEW] if one.id in newest
            ),
        )

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """A still from the username a decision was about, if this viewer may still be shown one."""
        recorded = _read(payload)
        username_id = str(recorded.get("username_id", ""))
        if not username_id:
            return ()
        found = await self._access.visible_username(viewer, username_id)
        if found is None:
            return ()
        newest = await self._access.newest_under_usernames(viewer, [username_id])
        if username_id not in newest:
            return ()
        return (_still(newest[username_id], found),)

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Take the join off: pointer, filed files and an alias it added; a created person stays."""
        recorded = _read(payload)
        username_id = str(recorded.get("username_id", ""))
        if not username_id:
            return False
        if await self._access.visible_username(viewer, username_id) is None:
            return False
        person_id = str(recorded.get("person_id") or "")
        alias_id = str(recorded.get("alias_id") or "")
        undone = await self._service.detach_username(
            username_id,
            actor=Actor.user(viewer.id),
            alias=(person_id, alias_id) if person_id and alias_id else None,
        )
        if not undone:  # pragma: no cover (the username was resolved a few lines above)
            return False
        files = await self._service.assets_of_username(username_id)
        if person_id:
            files += await self._service.assets_of_person(person_id)
        await self._touched(list(dict.fromkeys(files)))
        return True


def join_receipt(recorder: Recorder, user_id: str) -> JoinReceipt:
    """The receipt a "Who is this?" answer writes, counted as a decision and undoable."""
    return JoinReceipt(
        recorder=recorder,
        queue=NAME,
        user_id=user_id,
        title=lambda username, person: f"You said {username} is {person}",
        detail=(
            "The files under this username are now theirs. Taking this back takes the username"
            " and its files off this person, and the other name it gave them. The person stays."
        ),
    )


def _still(asset_id: str, username: UsernameSuggestion) -> Preview:
    """A file under a username, pressed through to where the username goes."""
    return Preview(kind=ASSET, id=asset_id, href=username_opens(username.id, username.person_id))


def _read(payload: str) -> dict[str, Any]:
    try:
        recorded: Any = json.loads(payload)
    except ValueError:
        return {}
    return recorded if isinstance(recorded, dict) else {}
