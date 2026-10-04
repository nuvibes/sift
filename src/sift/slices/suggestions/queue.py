# SPDX-License-Identifier: AGPL-3.0-or-later
"""Folder-led attribution, as the workbench sees it.

The workbench knows nothing about folders. What it knows is that something registered a queue with
a name, a count and a way to take a decision back, and this is that, for this feature.

Everything here is a translation. The decisions themselves, and every rule about who may be told
what, stay in the service beside it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sift.kernel.access import Viewer
from sift.kernel.access.sentences import many
from sift.kernel.workbench import (
    ASSET,
    Aside,
    Band,
    Preview,
    Recorded,
    Summary,
    Worded,
)
from sift.slices.suggestions.service import (
    FILED_QUEUE,
    FILENAMES_QUEUE,
    QUEUE,
    SILENT,
    TAKEN_BACK,
    SuggestionService,
    Written,
)
from sift.slices.suggestions.store import NameFiling
from sift.slices.suggestions.worded import filenames_said, folders_said

#: What this queue is called wherever it is stored. Declared beside the service, because the
#: service writes it into every receipt and the two must not disagree.
NAME = QUEUE

#: The record beside it: what a pass filed under somebody without asking. Its own name, so the
#: board can draw it as a tab of the folders page rather than as a list under the questions.
FILED = FILED_QUEUE

#: The two share one page. See `Queue.group`.
FOLDERS_GROUP = "folders"

#: How many files a card puts on screen before it says "and N more". The decision is one decision
#: whatever the number is; this is only how much of the pile is worth looking at to make it.
PREVIEW = 24


def _claim_anchor(claim_id: str) -> str:
    """Where one folder's question sits, for a still on the board to point at.

    **The folder, rather than the file the still came from.** What the card is about is the folder
    waiting for a yes or a no; a picture of one file inside it would lead away from the decision
    instead of into it.

    A fragment rather than an address of its own, and for the same reason the two duplicate queues
    give: a claim is answered where it sits and has no screen of its own to open, so the only place
    it exists is a card on its queue's page. The queue page marks each card with the same words
    (`FolderSuggestions`), and `$lib/organize/anchor` scrolls to it once the rows have arrived.

    A claim settled between the board being drawn and the page opening is a fragment nothing answers
    to, which leaves the page at the top: the right sub-page, and the honest fallback.
    """
    return f"/organize/{NAME}#claim-{claim_id}"


class FolderQueue:
    """Folders that look like they are somebody's, waiting for a yes or a no."""

    name = NAME
    title = "Folders to review"
    #: A folder that looks like somebody's is exactly the judgement no threshold
    #: settles: a name can be a person, a place or a theme, and only a person knows which.
    band = Band.DECISION
    #: One page with the record of what was filed without asking: the same folders, read the
    #: other way round, rather than a list of its own.
    group = FOLDERS_GROUP
    #: The first of its group, so the card on the board is this one's.
    group_title = "Folders"
    #: What the card on the board is for: this group's page, which this queue leads. See
    #: `Queue.purpose`.
    purpose = "Folders whose names look like a person or a Site, for you to confirm."
    #: A folder filed under somebody can be un-filed, and one set aside re-offered.
    reversible = True

    def __init__(self, service: SuggestionService) -> None:
        self._service = service

    async def available(self) -> bool:
        """Always. Reading folder names needs nothing switched on and no model downloaded.

        The count can be zero, and a zero here is the honest kind: there is nothing left to
        answer, which is the state this whole screen is trying to reach.
        """
        return True

    async def survey(self, viewer: Viewer) -> Summary:
        # The outline and not a page: a card draws a still per folder, and a page resolves every
        # row in full. See `SuggestionService.outline`.
        outline = await self._service.outline(viewer, limit=PREVIEW)
        return Summary(
            name=NAME,
            # The same words as the faces tabs' "Needs Your Input": the two sit on one board, and
            # two ways of saying "this one is yours to answer" would read as two different states.
            # The slug does not move: the address is in links and in every receipt ever written.
            title=self.title,
            verb="folders to name",
            verb_one="folder to name",
            decision=(
                "Is this folder the person it's named after? Yes adds every file in it to that "
                "person. No discards the suggestion."
            ),
            icon="folder",
            count=outline.total,
            # A still from each folder rather than the claim's own id, which addresses no picture,
            # and each one LEADS to the folder it was taken from. See `_claim_anchor`.
            preview=tuple(
                Preview(kind=ASSET, id=cover, href=_claim_anchor(claim_id))
                for claim_id, cover in outline.covers[:PREVIEW]
                if cover
            ),
        )

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Stills from the files the decision actually filed.

        `attributed` is what a confirmation wrote down: the files it put under somebody, paired with
        who. It is the only thing that still names them: by the time the record is read the claim
        has gone, which is what the decision did to it.

        Scoped through the resolver rather than trusted from the record, and that is the whole of
        why this asks rather than just listing them: a file that has been restricted since the
        decision is one this user may no longer be shown, and the record of having filed it is
        not a licence to draw it. A decision every file of which is now out of reach shows the
        decision and no pictures.
        """
        try:
            recorded: Any = json.loads(payload)
        except ValueError:
            return ()
        if not isinstance(recorded, dict):
            return ()
        wrote = recorded.get("written")
        pairs = _pairs(wrote, "attributed") if isinstance(wrote, dict) else ()
        wanted = [asset_id for asset_id, _person in pairs][:PREVIEW]
        if not wanted:
            return ()
        allowed = await self._service.visible_of(viewer, wanted)
        # Straight to the file itself, which is what was filed under somebody.
        return tuple(
            Preview(kind=ASSET, id=one, href=f"/asset/{one}") for one in wanted if one in allowed
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Put a decision back from what it wrote down about itself.

        Every field is reached for rather than assumed. A record can outlive the version that wrote
        it (a restored backup, an older release, a row somebody edited), and a payload missing a
        key it once had is a record this cannot reverse. Answering "nothing was put back" is the
        honest reading of that; reaching straight in would fail the request instead, which reads as
        the undo being broken rather than as the record being unreadable.
        """
        recorded = json.loads(payload)
        if not isinstance(recorded, dict):
            return False
        claim_id = str(recorded.get("claim_id", ""))
        if not claim_id:
            return False
        if recorded.get("kind") == "ignored":
            name_key = str(recorded.get("name_key", ""))
            if not name_key:
                return False
            return await self._service.unignore(claim_id=claim_id, name_key=name_key)
        written = recorded.get("written")
        if not isinstance(written, dict):
            return False
        return await self._service.take_back(_written(written), claim_id=claim_id)

    def worded(self, recorded: Recorded) -> Worded | None:
        """A Yes naming one person, worded when shown (see `worded.py` beside this file)."""
        return folders_said(recorded)


class FiledQueue:
    """What a pass filed under somebody without asking: the record beside the questions.

    A RECORD rather than a decision: a folder whose faces were already named is filed under that
    person and nobody is asked, which is the whole point of not asking twice. On screen so that
    the sweep is visible after it happens, rather than findable only by noticing a name on a file
    and wondering where it came from. It cannot empty and it is not work, so it is a tab of the
    folders page and never a card on the board (see `Band.RECORD`).
    """

    name = FILED
    title = "Added without asking"
    band = Band.RECORD
    group = FOLDERS_GROUP
    #: Not the first of its group, so it names no card. See `Queue.group_title`.
    group_title = None
    #: A record is never a card. See `Queue.purpose`.
    purpose = None
    #: Each row takes its folder back with one press, and each folder the pass files writes one
    #: receipt whose Undo does the same: the person off the files the folder pass put them on, the
    #: standing answer, the may-be card, and a no for the pass. The press writes its own receipt,
    #: whose Undo puts all of it back.
    reversible = True

    def __init__(self, service: SuggestionService) -> None:
        self._service = service

    async def available(self) -> bool:
        """Wherever the questions are. See `FolderQueue.available`."""
        return True

    async def survey(self, viewer: Viewer) -> Summary:
        return Summary(
            name=FILED,
            title=self.title,
            verb="folders added",
            verb_one="folder added",
            decision=(
                "Folders whose faces or name already match a person in your library. Sift added "
                "their files to that person without asking. Press Undo on a folder to remove that "
                "person from the files Sift added. Sift won't add that folder to them again."
            ),
            icon="folder_supervised",
            # The rows and not their file counts, which the card never draws. See `filed_total`.
            count=await self._service.filed_total(viewer),
        )

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Stills from the files one silent write put the person on, scoped to whoever is reading
        the record, for the reason `FolderQueue.pictures_of` gives."""
        found = _folder_record(payload)
        wanted = found.assets[:PREVIEW] if found else []
        if not wanted:
            return ()
        allowed = await self._service.visible_of(viewer, wanted)
        return tuple(
            Preview(kind=ASSET, id=one, href=f"/asset/{one}") for one in wanted if one in allowed
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Two records, told apart by the KIND each says it is. A folder the pass filed is taken
        back (`SuggestionService.take_back_silent`, the same act as the row's press); a folder
        somebody took back from its row is put back (`put_folder_back`).

        Every field reached for, for the reason `FolderQueue.reverse` gives: a record this version
        cannot read is nothing to put back.
        """
        found = _folder_record(payload)
        if found is None:
            return False
        if found.kind == SILENT:
            return await self._service.take_back_silent(
                viewer, folder_id=found.folder_id, person_id=found.person_id
            )
        return await self._service.put_folder_back(
            viewer,
            folder_id=found.folder_id,
            person_id=found.person_id,
            asset_ids=found.assets,
            linked=found.linked,
            refused=found.refused,
        )


class FiledFromFilenamesQueue:
    """What a file's OWN NAME said about where it came from: a report, with a way in.

    A LOG and never a judgement. Nothing here is waiting on anybody: the pass reads one shape, its
    rare false reads are refused at the reader, and it applies itself. What the card is for is that
    the sweep should be VISIBLE after it happens rather than findable only by noticing a site on a
    file and wondering how it got there, which is the same reason `FiledQueue` exists beside the
    folder questions.

    **It stands alone rather than joining the folders page**, and that is the honest grouping: a
    group is one PAGE and its members are alternatives to each other. These files are not an
    alternative reading of a folder claim (they were filed from a filename, under a site, with
    nobody named), and a tab of the folders page would put them where somebody goes to answer
    questions about people.

    **THE WAY IN IS A PAGE OF ITS OWN, not the Files wall.** The thing this pass decided is not a
    file. It decided a USERNAME (these came off this username on this site) once per username,
    applied to everything that matched. A flat wall of four thousand files is that conclusion said
    four thousand times: a misreading is visible in the group it made and invisible in the list,
    which is precisely the checking the card exists to make possible.

    So the page is grouped by username (the name, the Site, the count, a few of the files),
    and the wall is still one press further on, per group, where a wall is the right screen. It is
    the ordinary queue panel at `/organize/filenames`, which is why `Summary.opens` is not declared
    here: a queue that has a panel already has a way in, and declaring both would be two answers to
    one question. `opens` remains for the queue that genuinely has no panel.

    **No "undo the last N".** A bulk undo over a count is a button whose effect nobody can see
    before pressing it. Any single file is corrected on its own screen, because a filing writes one
    decision PER FILE and that decision is what the file's own History hands an Undo for. See
    `SuggestionService._record_filings`. The one answer about many files is a username's own No on
    its row, where the group is named and its files are on screen: one decision with its own Undo
    (`SuggestionService.take_back_username`).
    """

    name = FILENAMES_QUEUE
    title = "Enriched from filenames"
    #: See the note above: a report, drawn quietly, never a question. `Band.LOG`.
    band = Band.LOG
    #: Alone. See the note above.
    group = None
    group_title = None
    #: What the card on the board is for. See `Queue.purpose`.
    purpose = "Files Sift added to a username because of their names."
    #: Every sweep wrote a decision naming the files it filed, and `reverse` puts one back.
    reversible = True

    def __init__(self, service: SuggestionService) -> None:
        self._service = service

    async def available(self) -> bool:
        """Always. Reading a filename needs nothing switched on and no model downloaded.

        A zero here is the honest kind: no file in this library carries a shape Sift recognises,
        which is a true thing to say about a library and not a feature that failed to start.
        """
        return True

    async def survey(self, viewer: Viewer) -> Summary:
        filed = await self._service.filed_from_filenames(viewer)
        # And what the pass could NOT do. A filename shape that carries only the site's own number
        # for a username files nothing until something says what that number is called, so a library
        # can have thousands of files waiting on one fact nobody has been asked for, and the only
        # place to ask is where a username is edited. A count that is zero says nothing extra, which
        # is the ordinary case.
        waiting = await self._service.numbers_waiting()
        return Summary(
            name=FILENAMES_QUEUE,
            title=self.title,
            verb="files enriched from names",
            verb_one="file enriched from its name",
            decision=(
                "These files have a Site's username, post or ID in their names. Sift added each "
                "file to that username, not to a person. A username tells you where a file came "
                "from, not who is in it."
            ),
            icon=GLYPH,
            count=filed,
            # Read the biggest usernames first, for the reason the faces piles are read biggest
            # first: a wrong reading is worth the most where the pass filed the most.
            advice=FILED_ADVICE,
            aside=_filed_aside(waiting),
        )

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Stills from the files one sweep filed, scoped to whoever is reading the record.

        The same shape `FolderQueue.pictures_of` has and for the same reason: a file restricted
        since the decision is one this user may no longer be shown, and a record of having filed
        it is not a licence to draw it.
        """
        declined = _declined_record(payload)
        filed = [one.asset_id for one in declined[1]] if declined else _filed_record(payload)[1]
        wanted = filed[:PREVIEW]
        if not wanted:
            return ()
        allowed = await self._service.visible_of(viewer, wanted)
        return tuple(
            Preview(kind=ASSET, id=one, href=f"/asset/{one}") for one in wanted if one in allowed
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Take one sweep's filings off the files it put them on, or unmake one Photo Set it made.

        **Two kinds of record in one pile, told apart by what the record SAYS it is.** This pass
        writes a decision per file it files, and a decision per Photo Set it derives from a post;
        they are the same pass and belong on the same card, and they undo differently: one
        removes filings, the other removes a grouping and leaves every filing standing. The kind is
        read off the payload rather than guessed from which keys are present, because guessing is
        how a record written by a later version gets acted on as if it were an older one.

        Every field is reached for rather than assumed, for the reason `FolderQueue.reverse` gives:
        a record can outlive the version that wrote it, and a payload missing a key it once had is
        a record this cannot reverse. Answering "nothing was put back" is the honest reading.
        """
        photo_set_id = _set_record(payload)
        if photo_set_id:
            return await self._service.take_back_set(photo_set_id, by=viewer)
        # A person's No on a whole username: its Undo writes the filings back.
        declined = _declined_record(payload)
        if declined is not None:
            username_id, filings, refused = declined
            return await self._service.put_username_back(
                viewer, username_id=username_id, filings=filings, refused=refused
            )
        username_id, assets = _filed_record(payload)
        if not username_id or not assets:
            return False
        return await self._service.take_back_filings(username_id=username_id, asset_ids=assets)

    def worded(self, recorded: Recorded) -> Worded | None:
        """This decision's line, worded when shown (see `worded.py` beside this file)."""
        return filenames_said(recorded)


#: How to work through the pile: the biggest usernames first, and an undo for any file that is wrong.
FILED_ADVICE = (
    "Grouped by username, largest first. Check the largest groups, and undo any file that doesn't "
    "belong."
)


def _filed_aside(waiting: int) -> Aside | None:
    """What the pass is still waiting on, as a line of its own with its link, or None.

    Not a second count on the card, because it is not a second queue: nothing is on a screen
    waiting to be answered, and an ID with no username is a thing to go and type onto a username
    where it is edited. Its own line rather than the end of the advice, because it is a thing to
    do somewhere else. Said in the plural or the singular, because a card reading "1 IDs are"
    is a card nobody trusts the rest of.
    """
    if not waiting:
        return None
    # "ID", the one word the screen uses for a Site's number for a username.
    numbers = "ID is" if waiting == 1 else "IDs are"
    return Aside(
        said=(
            f"{many(waiting)} {numbers} waiting for a username. Add each ID to its username on "
            "the Site's People tab."
        ),
        link="Open Sites",
        href="/sites",
    )


#: The glyph on the card and on every mark this pass writes. A page passing under a reader, which
#: is what happened here: the file's name was READ. It is the one the client draws for
#: `enriched:filename` so the card and the mark say the same thing.
#:
#: Not `drive_file_rename_outline`, a file with a pen: that shows a file being RENAMED, which is the
#: one thing this pass never does, and there is a glyph for reading.
GLYPH = "document_scanner"


@dataclass(frozen=True, slots=True)
class _FolderRecord:
    """A record about one folder and one person, read back: a silent write or a take back."""

    kind: str
    folder_id: str
    person_id: str
    #: The files it put the person on, or took them off.
    assets: list[str]
    #: Whether it made (or forgot) the folder's standing answer.
    linked: bool
    #: Whether it said the pass's no (a take back only).
    refused: bool


def _folder_record(payload: str) -> _FolderRecord | None:
    """One of the two folder records read back out of its payload. None for any other record, or
    one missing the folder or the person."""
    try:
        recorded: Any = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(recorded, dict) or recorded.get("kind") not in (SILENT, TAKEN_BACK):
        return None
    folder_id = str(recorded.get("folder_id") or "")
    person_id = str(recorded.get("person_id") or "")
    if not folder_id or not person_id:
        return None
    found = recorded.get("assets")
    return _FolderRecord(
        kind=str(recorded["kind"]),
        folder_id=folder_id,
        person_id=person_id,
        assets=[str(one) for one in found] if isinstance(found, list) else [],
        linked=recorded.get("linked") is True,
        refused=recorded.get("refused") is True,
    )


def _set_record(payload: str) -> str:
    """The Photo Set one record made, or empty where the record is not about a set.

    The KIND is what decides it, not the presence of a key: a record that carries a set id and does
    not say it is about a set is one this version does not understand, and acting on it would be
    this code deciding what an older (or newer) version meant.

    Empty for an unreadable payload, exactly as `_filed_record` answers `("", [])`, and the caller
    falls through to the filing reading rather than failing the request.
    """
    try:
        recorded: Any = json.loads(payload)
    except ValueError:
        return ""
    if not isinstance(recorded, dict) or recorded.get("kind") != "post_set":
        return ""
    return str(recorded.get("photo_set_id", ""))


def _declined_record(payload: str) -> tuple[str, list[NameFiling], list[str]] | None:
    """A whole username taken back, read out of its record: the username, every filing as it
    stood, and the refusals it added. None for any other record.

    Told apart by the KIND it says it is, as `_set_record` is, and every field reached for: a row
    that does not read as four values is left out rather than guessed at, and a record naming no
    filing at all reads as nothing to put back.
    """
    try:
        recorded: Any = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(recorded, dict) or recorded.get("kind") != "declined":
        return None
    rows = recorded.get("files")
    filings = [
        NameFiling(
            asset_id=str(row[0]),
            source=str(row[1]),
            decided_at=row[2] if isinstance(row[2], int) else None,
            post_id=None if row[3] is None else str(row[3]),
        )
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, list) and len(row) == 4 and row[0] and row[1]
    ]
    refused = recorded.get("refused")
    return (
        str(recorded.get("username_id", "")),
        filings,
        [str(one) for one in refused] if isinstance(refused, list) else [],
    )


def _filed_record(payload: str) -> tuple[str, list[str]]:
    """One filing decision read back out of its own record: the username, and the files it filed.

    ONE parse for both halves rather than one each, and one place that knows what an unreadable
    record answers. Every field is reached for rather than assumed: a record can outlive the
    version that wrote it (a restored backup, an older release, a row somebody edited), and a
    payload missing a key it once had is a record this cannot act on. `("", [])` is the honest
    reading of that, and both callers refuse on it rather than failing the request, which would read
    as the undo being broken rather than as the record being unreadable.
    """
    try:
        recorded: Any = json.loads(payload)
    except ValueError:
        return "", []
    if not isinstance(recorded, dict):
        return "", []
    found = recorded.get("assets")
    assets = [str(one) for one in found] if isinstance(found, list) else []
    return str(recorded.get("username_id", "")), assets


def _pairs(recorded: Any, key: str) -> tuple[tuple[str, str], ...]:
    return tuple((str(one), str(two)) for one, two in recorded.get(key, ()))


def _pair(recorded: Any, key: str) -> tuple[str, str] | None:
    found = recorded.get(key)
    return None if found is None else (str(found[0]), str(found[1]))


def _written(recorded: Any) -> Written:
    """What a confirmation wrote, read back out of the record.

    Tuples rather than the lists JSON hands back, so what comes out of the record is the same shape
    as what went in, and a later reader cannot be caught out by the difference.
    """
    return Written(
        attributed=_pairs(recorded, "attributed"),
        filed=_pairs(recorded, "filed"),
        faces=tuple(str(one) for one in recorded.get("faces", ())),
        created_people=tuple(str(one) for one in recorded.get("created_people", ())),
        alias=_pair(recorded, "alias"),
        username_linked=_pair(recorded, "username_linked"),
        remembered=_pairs(recorded, "remembered"),
        namesakes=_pairs(recorded, "namesakes"),
    )
