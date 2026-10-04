# SPDX-License-Identifier: AGPL-3.0-or-later
"""Usernames nobody has said who they belong to, as work waiting on somebody.

Every downloaded file records the username that posted it. A username is a name on a site, not a human,
so on its own it answers nothing: two strangers can share a spelling on two sites, one person holds
five, and a library full of them is a library that knows where everything came from and not who is
in it.

Joining one to a person is what turns that into an answer. It makes every file under the username
count under them, and (because the username is written on as an also-known-as name) it makes them
findable by the spelling that is actually on the files.

A migration does this once, and only for the five sites where a username IS the person who
posted; a forum's username is the board it went to, and turning that into somebody would put a
message board in the People list under a human's name. Everywhere else, this queue is the way.

**A queue is for judgements a threshold cannot settle**, which is exactly what this is: no rule can
tell whether `esmewrenfield` on one site and `Neve Arbor` in the library are the same person. What a rule
CAN do is stop asking about usernames that have already been answered, and that is the only filtering
here.
"""

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

#: The queue's name, which is also its address on the client. A receipt written under the older
#: name `handles` is rewritten by the workbench's v11 step.
NAME = "usernames"

#: How many pictures a card draws. Enough to recognise what a pile is about; not a gallery.
PREVIEW = 4

#: Telling the search index that these files' words changed (`ReindexSeam.touched_many`).
Touched = Callable[[Sequence[str]], Awaitable[None]]


class UsernameQueue:
    """Usernames waiting to be told who they belong to."""

    name = NAME
    title = "Usernames to assign"
    #: What is left here is undecidable by rule: either two people answer to the
    #: spelling or nobody does. See the survey, which says so in the card's own words.
    band = Band.DECISION
    #: Stands alone: a username is not an alternative reading of anything else on the board.
    group = None
    #: Not the first of its group, so it names no card. See `Queue.group_title`.
    group_title = None
    #: What the card on the board is for. See `Queue.purpose`.
    purpose = "Usernames without a person, for you to choose who posts under them."
    #: Joining a username to a person can be undone: the username goes back to unanswered.
    reversible = True

    def __init__(self, service: PeopleService, access: Repository, *, touched: Touched) -> None:
        self._service = service
        self._access = access
        # Required rather than defaulted to nothing: an undo wired without it takes the name off
        # the person and leaves the search index answering the person for it, which is the fault
        # this is here to end, and a default would make that the quiet case.
        self._touched = touched

    async def available(self) -> bool:
        """Whether this install has any username at all, answered or not.

        Asked separately from the count, and the difference matters here more than most. A library
        that was scanned rather than downloaded has no usernames ever, and a panel offering to sort
        out something that does not exist reads as a broken feature rather than an unused one. A
        library that has answered all of them keeps the panel, at zero, which is the state this
        screen is trying to reach and worth showing.
        """
        return await self._service.any_username()

    async def survey(self, viewer: Viewer) -> Summary:
        # `name_candidates` is not asked for: the card says nothing about which kind of waiting a
        # username is (that is drawn per row on the queue's own page), and asking costs a subquery
        # per row.
        page = await self._access.list_usernames(viewer, limit=PREVIEW, unattached=True)
        # A STILL OF A FILE UNDER EACH USERNAME, not a cover: a username has none since v58
        # (`_DROP_ACCOUNT_COVER`), so there is none to read. The newest file filed
        # under it that THIS viewer may see, read through the wall's own statement for the whole
        # strip at once (see `Repository.newest_under_usernames`). A username with nothing they may
        # see draws nothing, which is also why the strip can be shorter than the count.
        newest = await self._access.newest_under_usernames(viewer, [one.id for one in page.items])
        return Summary(
            name=NAME,
            title=self.title,
            verb="usernames without a person",
            verb_one="username without a person",
            # What is left here is what Sift would not decide, and the wording says so.
            #
            # A download settles the unambiguous usernames itself, so everything remaining is
            # genuinely undecidable: two people answer to the spelling, or nobody does.
            # Short, because the card is scanned rather than read: what pressing it does comes
            # first, not an explanation of the rule that fills the pile.
            #
            # The card says both cases and does not COUNT them separately, and that is the design
            # rather than a shortfall.
            #
            # A count answers one question (is it worth opening this?), and both kinds of waiting
            # username answer it the same way: yes. What differs is what you DO when you get there,
            # and that is a per-row fact, so it is drawn per row (`UsernameSuggestion.name_candidates`,
            # read by the panel). Splitting the number here would need a whole-set aggregate over
            # the scoped usernames statement for a figure nobody acts on, while the rows themselves
            # went on looking identical, which is the half that actually costs somebody time.
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
        """A still from the username a decision was about, if this viewer may still be shown one.

        The newest file under the username that THIS reader may see, read now rather than trusted
        from the record: a file restricted since the decision is one they may no longer be shown,
        and having joined the username is not a licence to draw it. The username itself is asked about
        first for the same reason, and a username that is gone draws nothing.
        """
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
        """Take the join back off, from what the decision wrote down about itself.

        Everything the join wrote comes off, in one transaction (`detach_username`): the pointer,
        the files it filed under the person, the alias when the receipt says this join ADDED it,
        and a History line for each. An alias the person already carried is not named in the
        receipt, so it stays. The search index is then told about the same files the join told it
        about (the route that joins, `merge_username_into_person`): the username's files and every
        file of the person's, because an alias is indexed on every file its person is on.

        A person this decision CREATED is left, and that one is deliberate: somebody may have
        edited that person in the time since, and an undo that deletes a row somebody has been
        working on is worse than one that leaves a person with nothing filed under them.

        The alias is NOT left, though leaving it can look like the careful choice (somebody may
        have come to rely on the spelling). An alias left behind keeps the join alive in three
        places: the search box answers the person for the username, the card asks "Nobody goes by
        this yet" while one person answers to it, and the next download under the username files
        itself to them again without asking.

        Every field is reached for rather than assumed. A record can outlive the version that wrote
        it, and a payload missing a key it once had is a decision this cannot reverse: answering
        "nothing was put back" is the honest reading, where reaching straight in would fail the
        request and read as the undo being broken.
        """
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
    """The receipt a person answering "Who is this?" writes: this queue's, in its words.

    Through the recorder every other queue writes through, so the join is counted as a decision
    (the Organize counts, Insights' decided-by-queue figure, the quests that read them) and comes
    back off with Undo (`reverse`, below).
    """
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
    """A file under a username, drawn as that username: pressing it goes where a press on the username
    goes (`sentences.username_opens`), to its person or the files under it, not to the one file."""
    return Preview(kind=ASSET, id=asset_id, href=username_opens(username.id, username.person_id))


def _read(payload: str) -> dict[str, Any]:
    """What a decision wrote down, or an empty record when it cannot be read."""
    try:
        recorded: Any = json.loads(payload)
    except ValueError:
        return {}
    return recorded if isinstance(recorded, dict) else {}
