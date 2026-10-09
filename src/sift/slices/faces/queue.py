# SPDX-License-Identifier: AGPL-3.0-or-later
"""The faces, as the workbench sees them: one page, five tabs.

**Who is this, asked five ways.** Separate piles cannot say which holds the answer worth giving
first, and one list of thousands of rows holding three different questions cannot say which part of
it somebody has reached; a record hidden behind a control nobody opens is a record nobody reads.

So the page is a row of tabs, in the order the work is worth doing:
**Faces to confirm**: the people Sift is proposing; **Disagreements**: the names a pass filed
that the face in the file does not agree with; **Unnamed faces**: the groups nobody has named;
**Discarded**: the groups put aside; and **People Sift can recognize**, the record. Each is its
own queue registered here, as the other groups on this board are (Folders and Filed;
Duplicates and Exact copies), so the tab row is the group's own membership and nothing on the
client holds a list of these names.

**The page is `/organize/faces`,** because the queue that heads the group is named for the group,
not for a state.

Each tab reads ONE TIER of the same list rather than a list of its own (see `FaceService.to_check`),
so the order, the stranger floor and the scoping stay in one place and two tabs cannot come to
disagree about what is waiting.

**The two receipts keep their names on purpose.** A decision is on disk for as long as the library
is, and the name it was written under is how it is found again, so `ignored` and `identified` are
registered as reversers, which is what that half of the registry exists for, though no queue draws
cards under those names.

The whole file is translation. Every decision, and every rule about who may be told what, stays in
the service beside it.
"""

from __future__ import annotations

import json
from typing import Any

from sift.kernel.access import Viewer
from sift.kernel.access.sentences import (
    FACES_CONFIRMED,
    FACES_MATCHED,
    FACES_WAITING,
    faces_of_person,
)
from sift.kernel.jobs import JobQueue
from sift.kernel.workbench import (
    FACE,
    Band,
    Preview,
    Recorded,
    Reversal,
    Summary,
    Worded,
)
from sift.slices.faces.jobs import ask_for_rematching
from sift.slices.faces.models import Attribution, ToCheckKind, ToCheckShow
from sift.slices.faces.receipts import DISAGREEMENTS_QUEUE
from sift.slices.faces.service import (
    AGREED_WITH_MATCHES,
    AGREED_WITH_PROPOSALS,
    FACE_QUEUE,
    IDENTIFIED_QUEUE,
    NAMED_GROUPS,
    REFUSED_FACES,
    REFUSED_GROUPS,
    STOPPED_ASKING,
    FaceService,
    Sighting,
    ToCheckView,
)
from sift.slices.faces.service_disagreements import filed_rows
from sift.slices.faces.worded import identified_said, ignored_said

#: The people Sift is proposing, and first in the group, so the board's Faces card opens
#: `/organize/faces`. Named for the group rather than for the question because it is the address of
#: the whole page; *Faces to confirm* is what the tab says, named for what it holds so no two cards
#: on the board share a title. The state a single face is in is *Needs your input*.
SUGGESTIONS = "faces"

#: The names a pass filed that the face in the file disagrees with. Also the name a No taken on
#: them is recorded under, so the spelling is the receipts module's. See `DISAGREEMENTS_QUEUE`.
DISAGREEMENTS = DISAGREEMENTS_QUEUE

#: The groups of look-alike faces nobody has named yet.
TO_NAME = "faces-to-name"

#: The groups somebody discarded. A record rather than work. See `SetAsideQueue`.
#:
#: Not `ignored`, which is claimed already: the receipt a set-aside writes is recorded under that
#: name (`IGNORED` below). A queue and a reverser share one namespace, so the tab takes a name of
#: its own and the receipts keep theirs.
#:
#: **`discarded-faces`**, because the word on screen is Discard and the tab's name is its address;
#: the older `ignored-faces` address still reaches it through the client's `movedTo`.
#:
#: The stored word stays `ignored`: the pile status (`PileStatus.IGNORED`), the receipts'
#: queue (`IGNORED` below) and the table that remembers what was put aside. Nobody reads any of
#: them, and renaming a value on disk is a migration of every row for a word that is never drawn;
#: the screens translate it, which is what they already did.
SET_ASIDE = "discarded-faces"

#: The people Sift has faces for, and what it learned from each of them.
#:
#: It is also the first half of the address `_person_href` builds, and that is why the two are not
#: both written here: see that function.
PEOPLE_KNOWN = "known-people"

#: Where a decision about a set-aside group is recorded, and now a reverser rather than a queue.
IGNORED = FACE_QUEUE

#: And where a decision a match pass took on its own is recorded. Also a reverser. See the module
#: docstring.
IDENTIFIED = IDENTIFIED_QUEUE

#: The page the two share. What needs your input and who Sift already knows are two answers to one
#: question (who is this), so they are tabs of one screen, with the record beside the work.
FACES_GROUP = "faces"

#: How many items a card puts on screen before it says "and N more".
PREVIEW = 24


def _pile_href(pile_id: str) -> str:
    """Where one group of faces lives, for a crop on a card to point at.

    **The group, never the face.** What a card is about is the group waiting for a name, and a crop
    is a picture of one appearance in it: leading to the single face would lead away from the
    decision rather than into it.

    One address whatever the group's status is: a group put aside and a group waiting are the same
    screen showing the same faces, so one reached through the Discarded tab opens the screen the
    other opens. Two addresses
    for it would be two screens to keep in step over one pile. The client says the same thing on its
    own side (`pileHref`); the two halves of an address are written where each half is known.
    """
    return f"/organize/{TO_NAME}/{pile_id}"


def _person_href(person_id: str, *, show: str = FACES_WAITING) -> str:
    """Where ONE person's faces are looked at, which is their own review screen.

    Still the address behind every crop and every *Show me*, because looking at one person's
    proposals one at a time is what that screen is for. The list says which of them is worth
    opening; the screen says whether this face is her.

    It does not spell the address itself: `sentences.faces_of_person` spells it for the history
    threads too, and two copies of one address fail silently when a screen moves on one side. That
    one also escapes the id. The queue keeps `PEOPLE_KNOWN` because it is also the queue's own
    name, which is a different fact that happens to share a word.
    """
    return faces_of_person(person_id, show)


class _FaceQueue:
    """What everything here shares: it is all absent unless recognition is switched on."""

    #: Declared here because the shared code READS it. Each queue and each reverser sets its own
    #: below; without the declaration the shared code is reaching for an attribute its own class
    #: does not have, which is a type error rather than a style point.
    name: str

    def __init__(self, service: FaceService) -> None:
        self._service = service

    async def available(self) -> bool:
        """Whether recognizing faces is switched on at all.

        Not a count of zero. An install that has never turned face recognition on has no groups
        because there is nothing to have found any, and offering a permanently empty panel would
        read as a feature that does not work rather than as one that is not on.
        """
        return await self._service.enabled()

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """The faces the decision was taken on, from the group it named.

        The group still exists after being set aside or brought back (that is what makes those
        reversible), so the faces are still there to look at, which is exactly the case a record
        needs to cover.
        """
        try:
            recorded: Any = json.loads(payload)
            pile_id = str(recorded["pile_id"])
        except (ValueError, KeyError, TypeError):
            return ()
        found = await self._service.pile(viewer, pile_id, limit=PREVIEW, offset=0)
        if found is None:
            return ()
        group, _total = found
        # The group the faces are still in. Its own screen is where a decision about a group is
        # reconsidered, which is what somebody pressing a face in the record is trying to reach.
        where = _pile_href(pile_id)
        return tuple(
            Preview(kind=FACE, id=face.track_id, href=where)
            for face in group.faces[:PREVIEW]
            if not face.locked
        )


class SuggestionsQueue(_FaceQueue):
    """The people Sift is proposing, and the tab the Faces page opens on.

    **One press settles every face standing for somebody**, which is why this leads the row: agreeing
    names them all together and every one of them becomes a picture Sift learns from, so the next
    pass is better for this answer having been given. Nothing else in the group compounds that way.

    Ranked by how sure Sift is of each person's best match, the surest first: the order the list
    itself is in, which this reads a tier of rather than re-deriving. How many faces stand behind a
    question does not rank it (see `FaceService.look_alikes`).

    A DECISION, because the tab empties by being answered, which is the promise the judgement band
    makes.
    """

    name = SUGGESTIONS
    title = "Faces to confirm"
    #: Who a group of faces is cannot be settled by a threshold: that is what a face model
    #: already tried.
    band = Band.DECISION
    #: See `FACES_GROUP`.
    group = FACES_GROUP
    #: And what the whole page is called, because the board draws a group as one card. Declared by
    #: this queue and no other, because the queue that heads the group names the card.
    group_title = "Faces"
    #: What the card on the board is for: this group's page, which this queue leads. See
    #: `Queue.purpose`.
    purpose = "Confirm who is in your files, and name the faces Sift doesn't know."
    #: An agreement can be taken back face by face, through the receipts written under `IDENTIFIED`.
    reversible = True

    async def survey(self, viewer: Viewer) -> Summary:
        items, total, _small = await self._service.to_check(
            # Her standing questions and the groups that may be her: the two tiers this tab draws,
            # counted as one (see `ToCheckKind.MAY_BE`).
            viewer,
            kind=(ToCheckKind.PERSON, ToCheckKind.MAY_BE),
            limit=PREVIEW,
        )
        return Summary(
            name=SUGGESTIONS,
            title=self.title,
            # What the board's Faces card counts, and it counts the group: every pending tab added
            # up (`BoardCard`), so people, files and groups together, not a number of people Sift
            # is proposing, which is only the Faces to confirm tab.
            verb="questions about faces",
            verb_one="question about faces",
            decision=(
                "Confirm faces that match people you've already named, or open a group to review "
                "each face."
            ),
            icon="person",
            count=total,
            advice=(
                "Work from the top. The first is the match Sift is surest of, and each answer "
                "helps Sift recognize that person."
            ),
            preview=tuple(
                shown
                for item in items[:PREVIEW]
                if (shown := _picture(item, _suggestion_href(item))) is not None
            ),
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Nothing. An agreement's receipt is written under `IDENTIFIED`, which takes it back."""
        return False


def _drawn(item: ToCheckView) -> Sighting | None:
    """The face a card draws for this row: the first one not behind a shut vault. A locked face has
    no picture (its crop is refused), so a row whose faces are all locked draws none."""
    return next((face for face in item.faces if not face.locked), None)


def _picture(item: ToCheckView, href: str | None) -> Preview | None:
    """The row's picture on the board, leading to `href`, or None when it has none to draw."""
    face = _drawn(item)
    return None if face is None else Preview(kind=FACE, id=face.track_id, href=href)


def _suggestion_href(item: ToCheckView) -> str:
    """Where a crop on the Needs your input strip leads: her own screen for her questions, and the
    group the crop is from for a "these groups may be her" card: those faces are in a group, not
    on her page."""
    if item.kind is ToCheckKind.MAY_BE and item.groups:
        return _pile_href(item.groups[0].pile_id)
    return _person_href(item.id)


class DisagreementsQueue(_FaceQueue):
    """The names a pass filed that the face in the file does not agree with.

    **The one question here that runs backwards**, and the reason it sits second rather than last:
    everything else on this page is work that has not been done, and each of these is work that was
    done WRONGLY and is live in the library. Somebody's name sits on a file whose only face is not
    hers, and it is on her page, in her count and in every search for her until somebody says
    otherwise. One press settles one file, which is the least of anything here, and correcting a
    wrong answer still comes before giving a missing one.

    It leads the row only in the sense of urgency, not of size; Suggestions leads because a press
    there settles more, and this is ordinarily the smaller pile by a long way.
    """

    name = DISAGREEMENTS
    title = "Disagreements"
    band = Band.DECISION
    #: See `FACES_GROUP`.
    group = FACES_GROUP
    #: Not first in its group, so it names no card. See `Queue.group_title`.
    group_title = None
    #: What this tab is for, said by its group's card only where this queue leads it. See
    #: `Queue.purpose`.
    purpose = "Files whose face and name disagree, for you to choose which is right."
    #: A Yes is written elsewhere (naming under `IDENTIFIED`) and taken back there. A No over a
    #: run of her files is written under this queue's own name and taken back here (`reverse`).
    reversible = True

    async def survey(self, viewer: Viewer) -> Summary:
        items, total, _small = await self._service.to_check(
            viewer, kind=ToCheckKind.MISMATCH, limit=PREVIEW
        )
        return Summary(
            name=DISAGREEMENTS,
            title=self.title,
            verb="files where the face and the name disagree",
            verb_one="file where the face and the name disagree",
            decision=(
                "Each file already has a name, and its face is named as someone else. Choose "
                "which is right."
            ),
            icon="person_alert",
            count=total,
            advice=(
                "Each of these names is already in use in your library. Correcting one fixes the "
                "name everywhere."
            ),
            preview=tuple(
                shown
                for item in items[:PREVIEW]
                if (shown := _picture(item, _file_href(item.id))) is not None
            ),
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Put her back on the files a No took her off, each row as it was. True if any moved.

        A payload this build cannot read is not a decision it can put back, and says so.
        """
        read = filed_rows(payload)
        if read is None:
            return False
        person_id, rows = read
        return await self._service.put_filed_back(person_id, rows) > 0


class ToNameQueue(_FaceQueue):
    """The groups of look-alike faces nobody has named.

    **The floor is why this reads as work rather than as a wall.** Most of a swept library is groups
    of one or two strangers; listed among the real questions they bury them. Groups under
    `tuning.STRANGER_FLOOR` are not listed and not counted, and how many there are is one quiet line
    at the foot of the tab that opens them. They are people in the library and they are there for a
    reason, so nothing is deleted and nothing is unreachable.

    Largest first, because naming a large group gives the person a picture Sift can learn from, and
    the smaller groups of that same face then join them on their own.
    """

    name = TO_NAME
    title = "Unnamed faces"
    band = Band.DECISION
    #: See `FACES_GROUP`.
    group = FACES_GROUP
    #: Not first in its group, so it names no card. See `Queue.group_title`.
    group_title = None
    #: What this tab is for, said by its group's card only where this queue leads it. See
    #: `Queue.purpose`.
    purpose = "Groups of look-alike faces that Sift can't name yet."
    #: A group named or put aside goes back to unanswered.
    reversible = True

    async def survey(self, viewer: Viewer) -> Summary:
        items, total, _small = await self._service.to_check(
            viewer, kind=ToCheckKind.GROUP, limit=PREVIEW
        )
        return Summary(
            name=TO_NAME,
            title=self.title,
            verb="groups of faces to name",
            verb_one="group of faces to name",
            decision=("Name a group of look-alike faces. One answer names every face in it."),
            icon="group",
            count=total,
            advice=(
                "The largest group is first. Naming it teaches Sift the most, and smaller groups "
                "of the same face join it on their own."
            ),
            preview=tuple(
                shown
                for item in items[:PREVIEW]
                if (shown := _picture(item, _pile_href(item.id))) is not None
            ),
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Put a group back among the ones waiting."""
        recorded: Any = json.loads(payload)
        return await self._service.restore(str(recorded["pile_id"]))


class SetAsideQueue(_FaceQueue):
    """The groups somebody put aside, as a tab rather than as a dropdown on the work.

    A RECORD: its count is of questions that have been answered, so it can never go down by being
    worked through, and a card on the board is a promise that answering it empties something.

    **A tab, not a filter behind a `Select`.** Being put aside is the same question answered no,
    but a control has to be opened to be read, and the one state somebody reaches for when they
    think they hid something by mistake would be the hardest to reach. A tab says its own name and
    carries its own count without being touched.
    """

    name = SET_ASIDE
    title = "Discarded"
    #: See the class docstring: a count of answered questions is not work.
    band = Band.RECORD
    #: See `FACES_GROUP`.
    group = FACES_GROUP
    #: Not first in its group, so it names no card. See `Queue.group_title`.
    group_title = None
    #: A record is never a card. See `Queue.purpose`.
    purpose = None
    #: A group put aside can be brought back.
    reversible = True

    async def survey(self, viewer: Viewer) -> Summary:
        items, total, _small = await self._service.to_check(
            viewer, show=ToCheckShow.IGNORED, limit=PREVIEW
        )
        return Summary(
            name=SET_ASIDE,
            title=self.title,
            verb="groups you discarded",
            verb_one="group you discarded",
            decision="Groups you discarded. Sift doesn't ask about them again. Restore one at any time.",
            # The Discard glyph, the minus, as on the verbs (`DISCARD_ICON` in the client).
            icon="remove",
            count=total,
            preview=tuple(
                shown
                for item in items[:PREVIEW]
                if (shown := _picture(item, _pile_href(item.id))) is not None
            ),
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Put a group back among the ones waiting."""
        recorded: Any = json.loads(payload)
        return await self._service.restore(str(recorded["pile_id"]))


def _file_href(asset_id: str) -> str:
    """Where one file lives, for a crop on a mismatched row to point at.

    The FILE and not the person or the group, because that is what the row is about: a name sits
    on this file and the face in it does not agree, so what somebody opens to decide is the file.
    `/asset/<id>` is the client's spelling of it, which is the same address the stash-box queue's
    stills point at: a different address space from an API path, told apart by where it sits.
    """
    return f"/asset/{asset_id}"


class PeopleKnownQueue(_FaceQueue):
    """The people Sift has faces for, gathered by person and most recent first.

    A record rather than a question, which is why it counts towards nothing on the rail. It is here
    because a feature that attaches people's names to files without being asked owes somebody a
    place to see what it concluded, and gathered BY PERSON, because one card per face says nothing
    about who is on it: thirteen appearances of one person would read as thirteen separate answers
    to thirteen separate questions.

    What each card says is what Sift knows about that person and what it is waiting to be told:
    what you confirmed, what it named on its own, and what needs your input. The two presses are
    on the card because this is where somebody is looking when they decide to answer them.
    """

    name = PEOPLE_KNOWN
    title = "People Sift can recognize"
    #: A record: the count never goes down, so it can never be a card on a screen whose whole
    #: promise is that it empties. It is a tab beside the work instead.
    band = Band.RECORD
    #: See `FACES_GROUP`.
    group = FACES_GROUP
    #: Not first in its group, so it names no card. See `Queue.group_title`.
    group_title = None
    #: A record is never a card. See `Queue.purpose`.
    purpose = None
    #: The decisions taken here are taken back through the receipts written under `IDENTIFIED`.
    reversible = True

    async def survey(self, viewer: Viewer) -> Summary:
        # The wall's own order and count, off the stored per-user figures, and one face per
        # card: the board draws one crop per person and nothing else about them.
        firsts, total = await self._service.known_people_preview(viewer, most=PREVIEW)
        return Summary(
            name=PEOPLE_KNOWN,
            title=self.title,
            verb="people",
            verb_one="person",
            decision="Who Sift has faces for, what you confirmed, and what needs your input.",
            icon="supervised_user_circle",
            count=total,
            preview=tuple(Preview(kind=FACE, id=track_id) for track_id in firsts),
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Nothing. The receipts belong to `IdentifiedRecords`, which is registered beside this."""
        return False


class IgnoredRecords(_FaceQueue):
    """How a group set aside is put back. A reverser with no card of its own.

    Not a queue: a tab counting groups nobody wants to be asked about again could never go down,
    which is the one thing a card on this screen must be able to do. What was set aside is a filter
    on the list. The receipts stay under this name, so a decision taken a month ago is still
    findable and still reversible.
    """

    #: See `FACES_GROUP`: a record with no card of its own still decides under the faces card,
    #: so a decision it reverses counts toward that card.
    group = FACES_GROUP

    name = IGNORED
    #: A group set aside can be put back.
    reversible = True

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        recorded: Any = json.loads(payload)
        return await self._service.restore(str(recorded["pile_id"]))

    def worded(self, recorded: Recorded) -> Worded | None:
        """This decision's line, worded when shown. See `worded.py` beside this file."""
        return ignored_said(recorded)


async def _as_found_now(service: FaceService, recorded: dict[str, Any]) -> dict[str, Any]:
    """A receipt's payload with every face it names by the id that face goes by now.

    The faces are in three places a payload of this queue keeps them: the list, the offered list
    and the keys of the per-face maps. All three are read through one ask (`FaceService.live_ids`)
    and written back in the same shape, so every act below reads its payload exactly as before.

    The reference rows a press filed (`references`) are keyed by face too, and follow it; the rows
    themselves keep their ids across a rescan. The `cover` is NOT read through: a person's cover
    goes on naming the face it was cut from after a rescan replaces that face, so the id written
    down is the one the cover still holds.
    """
    named = [str(one) for one in recorded.get("track_ids") or []]
    offered = recorded.get("offered")
    offered_ids = [str(one) for one in offered] if isinstance(offered, list) else []
    maps = [
        key
        for key in ("confidence", "attribution", "references", "named_before")
        if isinstance(recorded.get(key), dict)
    ]
    keys = [str(one) for key in maps for one in recorded[key]]
    live = await service.live_ids([*named, *offered_ids, *keys])
    out = dict(recorded)
    out["track_ids"] = [live.get(one, one) for one in named]
    if isinstance(offered, list):
        out["offered"] = [live.get(one, one) for one in offered_ids]
    for key in maps:
        out[key] = {live.get(str(one), str(one)): value for one, value in recorded[key].items()}
    return out


def _named_before(
    held: object,
) -> list[tuple[str, dict[str, float | None], dict[str, str | None]]]:
    """A receipt's `named_before` read back as `unreject` takes it: per person the face was named
    as, each face's confidence and state. Nothing for a map this build cannot read."""
    if not isinstance(held, dict):
        return []
    by_person: dict[str, tuple[dict[str, float | None], dict[str, str | None]]] = {}
    for track_id, was in held.items():
        if not isinstance(was, list) or len(was) != 3 or not isinstance(was[0], str):
            continue
        person_id, state, sure = was
        sures, states = by_person.setdefault(person_id, ({}, {}))
        sures[str(track_id)] = float(sure) if isinstance(sure, (int, float)) else None
        states[str(track_id)] = str(state) if isinstance(state, str) else None
    return [(person_id, sures, states) for person_id, (sures, states) in sorted(by_person.items())]


class IdentifiedRecords(_FaceQueue):
    """How a match pass's own decision is taken back. A reverser with no card of its own.

    A pass that matches a face above the attach line decides something (it puts a person on a
    file without anybody being asked, which is the one thing this feature does while nobody is
    looking), so it writes a receipt, or the only way to take it back would be to find each face
    and take the name off by hand.
    """

    #: See `FACES_GROUP`: a record with no card of its own still decides under the faces card,
    #: so a decision it reverses counts toward that card.
    group = FACES_GROUP

    name = IDENTIFIED
    #: A re-match's receipts are written under this name, and each can be taken back.
    reversible = True

    def __init__(self, service: FaceService, *, queue: JobQueue | None = None) -> None:
        super().__init__(service)
        #: Where a re-match is asked for after an Undo that took reference pictures away. None where
        #: nothing runs work (a test, a tool): the next re-match anything asks for sees the change.
        self._queue = queue

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """The faces a re-match attached, from the appearances it wrote down.

        Its own rather than the shared one above, which reads a `pile_id`: these faces are in no
        group at all, because being attributed to somebody is what takes a face out of one. They
        lead to the person's page, which is where a match is reconsidered.
        """
        try:
            recorded: Any = json.loads(payload)
            person_id = str(recorded["person_id"])
            track_ids = [str(one) for one in recorded["track_ids"]]
        except (ValueError, KeyError, TypeError):
            return ()
        # WHERE THOSE FACES ARE NOW, which is not the same tab for the acts recorded here. A run
        # Sift matched on its own is still `matched`; a run somebody agreed with (a run of matches
        # or a run of proposals) is `confirmed`, so sending an agreement's record to the matched
        # tab opens a screen the faces have already left, an empty list under a receipt saying hundreds
        # of them. A REFUSED run is on no tab of hers at all: the name came off, so the crops are
        # drawn and lead nowhere rather than to a screen that cannot hold them.
        act = recorded.get("act")
        if act in (REFUSED_FACES, STOPPED_ASKING, REFUSED_GROUPS):
            where = None
        elif act in (AGREED_WITH_MATCHES, AGREED_WITH_PROPOSALS, NAMED_GROUPS):
            where = _person_href(person_id, show=FACES_CONFIRMED)
        else:
            where = _person_href(person_id, show=FACES_MATCHED)
        # Scoped, exactly as every other picture of a decision is: `touchable_faces` answers with
        # the faces on files this user may still be shown, so a record of a decision about files
        # that have since moved out of reach shows the decision and none of them.
        actionable = await self._service.touchable_faces(viewer, track_ids[:PREVIEW])
        return tuple(Preview(kind=FACE, id=track_id, href=where) for track_id in actionable.allowed)

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool | Reversal:
        """Take back one of the two acts recorded under this name. True if anything moved.

        **Two acts, taken back differently**, which is what the payload's own `act` says. A run of
        matches Sift made on its own comes OFF the files (nobody ever said those were her), and
        each face is asked about instead, so the next re-match cannot simply make it again (see
        `FaceService.unmatch`). Somebody agreeing with a run of them goes back to being a match, because what is being
        taken back is the agreement and not the match: Sift still says those appearances are this
        person, exactly as it did before the button was pressed, and a face that fell all the way to
        nobody would turn "I take my agreement back" into "Sift was wrong". An older receipt carries
        no `act` at all and is the first kind, which is what it was.

        From the payload rather than from the state, which is what keeps an undo to what the
        decision touched: a face somebody has since agreed to, or one a later pass moved to
        somebody else, is left exactly where it is. See `FaceService.unmatch`.
        """
        try:
            recorded: Any = json.loads(payload)
            person_id = str(recorded["person_id"])
            track_ids = [str(one) for one in recorded["track_ids"]]
        except (ValueError, KeyError, TypeError):
            # A payload this build cannot read is not a decision it can put back, and saying so is
            # the honest answer, the same one the shared picture reader gives.
            return False
        # Every face by the id it goes by now. A rescan of a file replaces its appearances, and the
        # receipt names the ones it decided; read through `Store.live_ids`, the face the rescan found
        # again is the one taken back, or an Undo after a rescan would reach nothing.
        recorded = await _as_found_now(self._service, recorded)
        track_ids = [str(one) for one in recorded["track_ids"]]
        pictures = set(await self._service.own_reference_ids(person_id))
        moved = await self._reverse_act(recorded, person_id, track_ids)
        # What the press did beyond its faces, once they are back, each only while it still stands
        # as the press left it (`FaceService.take_back_what_it_taught`): the cover a face of this
        # decision filled, the starters her first own picture retired, and a folder's proposal a
        # Yes accepted. After the faces, so the rule for an empty cover sees the files she is still
        # on, and her own pictures are already gone when the starters ask whether any are left.
        cover = recorded.get("cover")
        starters = recorded.get("starters")
        proposals = recorded.get("proposals")
        if await self._service.take_back_what_it_taught(
            viewer,
            person_id,
            cover=cover if isinstance(cover, str) else None,
            starters=[str(one) for one in starters] if isinstance(starters, list) else [],
            proposals=[str(one) for one in proposals] if isinstance(proposals, list) else [],
        ):
            moved = True
        # The pictures this Undo took away, and with them every recognition of her that rested on
        # nothing else: taken back in the same press, and their own lines marked taken back
        # (`Reversal.along`), since a match on pictures that are gone is a match on nothing.
        removed = pictures - set(await self._service.own_reference_ids(person_id))
        along, _faces = await self._service.take_back_recognitions(
            person_id, removed, since=receipt_id
        )
        # And where this Undo took her LAST picture, the questions Sift asked about her rest on
        # nothing either: back to nobody, with their own line in History and an Undo of their own
        # (`FaceService.return_questions`). A picture that stays keeps them asked.
        offered = recorded.get("offered")
        own = (
            [*track_ids, *(str(one) for one in offered)] if isinstance(offered, list) else track_ids
        )
        if removed and await self._service.return_questions(person_id, leaving=own):
            moved = True
        # A gallery that lost pictures matches differently: asked for exactly as the routes ask
        # after any change to somebody's references.
        if self._queue is not None and removed:
            await ask_for_rematching(self._queue)
        if along:
            return Reversal(put_back=1, of=1, along=tuple(along))
        return moved

    async def _reverse_act(
        self, recorded: dict[str, Any], person_id: str, track_ids: list[str]
    ) -> bool:
        """The faces of one receipt put back, by the act it records. See `reverse`.

        `references` is the rows each confirmed face's press created, where the receipt kept them;
        None for a receipt that kept none, whose Undo removes by the face's pictures.
        """
        act = recorded.get("act")
        kept_ids = recorded.get("references")
        made: dict[str, list[str]] | None = (
            {
                str(one): [str(row) for row in rows]
                for one, rows in kept_ids.items()
                if isinstance(rows, list)
            }
            if isinstance(kept_ids, dict)
            else None
        )
        if act == REFUSED_FACES:
            kept = recorded.get("confidence")
            held: dict[str, float | None] = (
                {str(one): value for one, value in kept.items()}
                if isinstance(kept, dict) and kept
                else dict.fromkeys(track_ids)
            )
            states = recorded.get("attribution")
            was: dict[str, str | None] = (
                {str(one): (None if value is None else str(value)) for one, value in states.items()}
                if isinstance(states, dict)
                else {}
            )
            return await self._service.unreject(person_id, held, was) > 0
        if act in (AGREED_WITH_MATCHES, AGREED_WITH_PROPOSALS):
            # The confidences the receipt kept, so each face goes back carrying the number the
            # comparison gave it rather than the certainty agreeing wrote over it. The FALLBACK is
            # not a nicety: the undo walks the confidence map, so a receipt written without one (an
            # older release, or a run where nothing had a recorded number) would take nothing back
            # and report that it had. Each falls back to no confidence, which is what the column
            # says for an offer made from a group rather than from a match.
            agreed = recorded.get("confidence")
            sure: dict[str, float | None] = (
                {str(one): value for one, value in agreed.items()}
                if isinstance(agreed, dict) and agreed
                else dict.fromkeys(track_ids)
            )
            # Back to what the act says each face WAS. A proposal somebody agreed with goes back to
            # waiting for an answer; a match goes back to being a match. One receipt cannot hold
            # both acts, so the word is read once for the run.
            back = await self._service.unconfirm_matches(
                person_id,
                sure,
                back_to=(
                    Attribution.SUGGESTED if act == AGREED_WITH_PROPOSALS else Attribution.MATCHED
                ),
                made=made,
            )
            # And the rest of their groups, which agreeing with proposals offered as her: each goes
            # back to nobody, where it was before the press, exactly as a group's Yes taken back
            # sends its offered faces (`unname_groups`, with nothing confirmed to take off). An
            # older receipt names none, and only the agreement comes back.
            offered = recorded.get("offered")
            if isinstance(offered, list) and offered:
                back += await self._service.unname_groups(
                    person_id, [], [str(one) for one in offered]
                )
            return back > 0
        if act == NAMED_GROUPS:
            # A Yes on a "these groups may be her" card: every face it confirmed or offered goes
            # back to nobody, which is where each was before the press. A Yes on a disagreement
            # took somebody else's name off first (`named_before`), and that name goes back on.
            offered = recorded.get("offered")
            back = await self._service.unname_groups(
                person_id,
                track_ids,
                [str(one) for one in offered] if isinstance(offered, list) else [],
                made=made,
            )
            before = recorded.get("named_before")
            for other, sure, was in _named_before(before):
                back += await self._service.unreject(other, sure, was)
            return back > 0
        if act == REFUSED_GROUPS:
            # And a No on it: the refusals are forgotten, and the faces stay nobody's.
            return await self._service.unrefuse_groups(person_id, track_ids) > 0
        if act == STOPPED_ASKING:
            # Questions a re-match stopped asking go back to being asked, each at the confidence it
            # had been put at. The receipt names every face it withdrew, so the fallback for a map
            # this build cannot read is "no number", which is what an offer from a group carries.
            kept = recorded.get("confidence")
            asked: dict[str, float | None] = (
                {str(one): value for one, value in kept.items()}
                if isinstance(kept, dict) and kept
                else dict.fromkeys(track_ids)
            )
            return await self._service.ask_again(person_id, asked) > 0
        # A run Sift matched on its own. A re-match's receipt says what each face WAS before it ran,
        # and a face that had been a question goes back at the confidence it had been put at; a
        # receipt from before that (or a scan's, which attaches faces nobody was on) carries no
        # states, and each face is asked about at the confidence the match gave it.
        states = recorded.get("attribution")
        numbers = recorded.get("confidence")
        was_asked: dict[str, float | None] = (
            {
                str(one): (numbers.get(one) if isinstance(numbers, dict) else None)
                for one, value in states.items()
                if value == Attribution.SUGGESTED.value
            }
            if isinstance(states, dict)
            else {}
        )
        return await self._service.unmatch(track_ids, person_id, was_asked=was_asked) > 0

    def worded(self, recorded: Recorded) -> Worded | None:
        """This decision's line, worded when shown. See `worded.py` beside this file."""
        return identified_said(recorded)
