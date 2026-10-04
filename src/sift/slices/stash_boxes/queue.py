# SPDX-License-Identifier: AGPL-3.0-or-later
"""Files a stash-box recognized, as work waiting on somebody.

The pass over the library asks three public services what each file is and keeps what came back.
None of it is written. What is left is a pile of answers, each one a claim about a file that
somebody has to agree with, and that is exactly what a queue is for.

**It is a judgement a threshold cannot settle.** Sift already threw away the answers a rule CAN
decide: a fingerprint that matched nothing leaves no row, a perceptual answer with fifty entries in
it is discarded as the no it really is, and an answer whose length disagrees with the file is graded
down before it ever reaches here. What is left is a real candidate, and whether a real candidate is
this file is not something another threshold is going to settle.

The decision unit is a PAGE rather than a file. Somebody with four thousand recognised files is not
going to press a button four thousand times, and the whole reason the confirm screen shows what each
answer would change is so that one press over a page is a decision rather than a leap.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from sift.kernel.access import Repository, Viewer
from sift.kernel.access.creator_studios import QUEUE as STUDIOS
from sift.kernel.access.sentences import TOOK_BACK, took_back_said
from sift.kernel.ledger import Actor
from sift.kernel.log import get_logger
from sift.kernel.records import Subject
from sift.kernel.workbench import (
    ASSET,
    DOER,
    Band,
    Named,
    Piece,
    Preview,
    Recorded,
    Summary,
    Worded,
)
from sift.slices.stash_boxes.enrich import AssetWriter
from sift.slices.stash_boxes.service import StashBoxService
from sift.slices.stash_boxes.studios import CreatorStudios

log = get_logger(__name__)

NAME = "tagger"
#: The ledger beside the pile: every subject a box has been agreed to know.
LINKED = "linked"
#: What the enrichment could not decide: the third tab of the same page.
UNDECIDED = "undecided"
#: The two share a page. The pile heads it and names the card; the ledger is its record tab.
STASH_GROUP = "stash"

#: How many stills a card draws. Enough to recognise what a pile is about; not a gallery.
PREVIEW = 4


def _file_href(asset_id: str) -> str:
    """Where one recognised file lives, for a still to point at.

    `/asset/<id>`, a client screen, not the API's spelling: a `Preview.href` is an address in the
    client, and both address spaces begin with a slash.

    The file rather than a row on the queue page, because that is what the sub-page itself links
    each row to (`MatchRow`): a claim about a file is judged by looking at the file.
    """
    return f"/asset/{asset_id}"


class TaggerQueue:
    """Files a stash-box recognized, waiting to be agreed with."""

    name = NAME
    title = "Matches to review"
    #: An offer waiting to be accepted rather than a question being weighed up. A stash-box
    #: has already decided what the file is; what is left is whether to take its word, and a
    #: whole page of that settles in one press.
    band = Band.CLEANUP
    #: One page with the ledger of what the boxes have enriched. See `LinkedQueue`.
    group = STASH_GROUP
    #: First in its group, so the card on the board wears this name.
    group_title = "Files a stash-box recognized"
    #: What the card on the board is for: this group's page, which this queue leads. See
    #: `Queue.purpose`.
    purpose = "Check what a stash-box knows about your files before it is saved."
    #: What a stash-box match wrote can be taken off again.
    reversible = True

    def __init__(
        self,
        service: StashBoxService,
        access: Repository,
        *,
        writer: Callable[[], AssetWriter | None] | None = None,
    ) -> None:
        self._service = service
        self._access = access
        # The file's writer, asked for when an Undo needs it: the columns and addresses a take-back
        # cleared are written back through it, the only thing that writes them. Asked late because
        # the enrichment is wired after the board.
        self._writer = writer or (lambda: None)

    async def available(self) -> bool:
        """Whether the pass has ever found anything on this install.

        Asked separately from the count, and the difference decides whether the panel is drawn at
        all. A library that has never been scanned has no business being offered a pile to work
        through: an empty panel reads as a feature that does not work rather than as one nobody
        has turned on. A library that has answered everything keeps its panel, at zero, which is
        the state this screen is trying to reach and is worth showing.
        """
        return await self._service.any_match()

    async def survey(self, viewer: Viewer) -> Summary:
        found, total = await self._service.waiting(limit=PREVIEW)
        pictures: list[Preview] = []
        for one in found:
            if await self._access.get_asset(viewer, one.asset_id) is None:
                continue
            pictures.append(Preview(kind=ASSET, id=one.asset_id, href=_file_href(one.asset_id)))
        return Summary(
            name=NAME,
            # Named for what is waiting and what to do with it, like the Faces group's first tab,
            # so no two cards on the board share a title.
            title=self.title,
            verb="files to review",
            verb_one="file to review",
            # The page's one lede: the pile below draws no second paragraph of its own.
            decision=(
                "Review what a stash-box found for each file where the match wasn't certain; "
                "Auto-enrich adds exact matches automatically. Its details appear beside yours, and "
                "nothing is saved until you confirm. You can confirm a whole page in one go."
            ),
            # The glyph the rest of Sift draws a tag with. It must be one of the icons the client
            # ships: an unknown name throws there and takes the whole workbench board down with it.
            icon="shoppingmode",
            count=total,
            preview=tuple(pictures[:PREVIEW]),
        )

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Stills from the files one decision was about, if this user may still be shown them.

        Scoped through the resolver rather than trusted from the record: a file restricted since the
        decision is one this user may no longer see, and having agreed to it is not a licence to
        draw it.
        """
        pictures: list[Preview] = []
        for one in _read(payload).get("matches", [])[:PREVIEW]:
            asset_id = str(one.get("asset_id", "")) if isinstance(one, dict) else ""
            if not asset_id or await self._access.get_asset(viewer, asset_id) is None:
                continue
            pictures.append(Preview(kind=ASSET, id=asset_id, href=_file_href(asset_id)))
        return tuple(pictures)

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Undo one decision of this pile, from what it wrote down about itself.

        **An answer applied: what it wrote comes off, and it is a question again.** A take-back
        that left the answer's people and filings on the file would be no take-back: they would go
        on drawing the file under a person and a Site nobody agreed to. A field somebody corrected
        since stays, because a field comes off only while it holds exactly what the answer offered,
        and a row only while it is the box's (`StashBoxService.take_back`, which writes the
        take-back's own line and Undo). An answer no longer applied is put back among the
        questions as it is.

        **A take-back: everything it took comes back** (`StashBoxService.put_back_taken`), the
        fields through the file's writer.

        Every field is reached for rather than assumed. A record can outlive the version that wrote
        it, and a payload missing a key it once had is a decision this cannot reverse, so answering
        "nothing was put back" is the honest reading, where reaching straight in would fail the
        request and read as the undo being broken.
        """
        recorded = _read(payload)
        writer = self._writer()
        if TOOK_BACK in recorded:
            put = await self._service.put_back_taken(receipt_id, recorded)
            if writer is not None and put.asset_id and (put.fields or put.links):
                await writer.put_back_fields(put.asset_id, put.fields, put.links)
            log.info("stashbox.taken_back.reversed", rows=put.rows)
            return put.rows > 0 or bool(put.fields or put.links)
        put_back = 0
        for one in recorded.get("matches", []):
            if not isinstance(one, dict):
                continue
            asset_id = str(one.get("asset_id", ""))
            box_id = str(one.get("box_id", ""))
            if not asset_id or not box_id:
                continue
            if await self._access.get_asset(viewer, asset_id) is None:
                continue
            if await self._service.take_back(
                asset_id, box_id, actor=Actor.user(viewer.id), reopen=True, writer=writer
            ) or await self._service.reopen(asset_id, box_id):
                put_back += 1
        log.info("stashbox.tagger.reversed", files=put_back)
        return put_back > 0

    def worded(self, recorded: Recorded) -> Worded | None:
        """This decision's line, worded when shown. See `kernel.workbench.Recorded`.

        The stored title said "Applied what the stash-boxes said about 1 file", with nobody doing
        it and neither the file nor the box named, though the payload records both: each match is a
        file and the box whose answer was applied. So it says who, which boxes, and the file where
        there was one ("You applied what StashDB said about beach.mp4").

        A take-back's receipt is said as its event is (`sentences.took_back_said`): who, the boxes,
        and why, about the file whose page it is drawn on or about how many files it took back. On
        a file's page only the boxes that spoke about that file are named.
        """
        page = recorded.page
        took = took_back_said(
            recorded.held(), page[1] if page is not None and page[0] == "asset" else None
        )
        if took is not None:
            return Worded(said=_took_back_words(recorded, *took))
        matches = [
            one
            for one in _read(recorded.payload).get("matches", [])
            if isinstance(one, dict) and one.get("asset_id")
        ]
        if not matches:
            return None
        boxes = [
            Named(kind="box", id=box)
            for box in dict.fromkeys(str(one.get("box_id") or "") for one in matches)
            if box
        ]
        what: tuple[Piece, ...] = (
            (Named(kind="asset", id=str(matches[0]["asset_id"])),)
            if len(matches) == 1
            else (f"{len(matches):,} files",)
        )
        said_boxes: list[Piece] = []
        for at, box in enumerate(boxes):
            if at:
                said_boxes.append(" and " if at == len(boxes) - 1 else ", ")
            said_boxes.append(box)
        return Worded(
            said=(
                DOER,
                " applied what ",
                *(said_boxes or ["the stash-boxes"]),
                " said about ",
                *what,
            )
        )


def _took_back_words(recorded: Recorded, boxes: list[str], why: str | None) -> tuple[Piece, ...]:
    """A take-back's receipt as a line: "Sift took back what FansDB said about this file, because it
    matched on the picture alone" on a file's page, and "... about 370 files" anywhere else."""
    files = [one for one in recorded.subjects if one.kind == "asset"]
    many = recorded.held().get("files")
    count = many if isinstance(many, int) else len(files)
    what: tuple[Piece, ...]
    if recorded.page is not None and recorded.page[0] == "asset":
        what = (Named(kind="asset", id=recorded.page[1]),)
    elif count == 1 and files:
        what = (Named(kind="asset", id=files[0].id),)
    else:
        what = ("1 file" if count == 1 else f"{count:,} files",)
    which: list[Piece] = []
    for at, box in enumerate(boxes):
        if at:
            which.append(" and " if at == len(boxes) - 1 else ", ")
        which.append(box)
    return (
        DOER,
        " took back what ",
        *(which or ["a stash-box"]),
        " said about ",
        *what,
        *((why,) if why else ()),
    )


def _read(payload: str) -> dict[str, Any]:
    """What a decision wrote down, or an empty record when it cannot be read."""
    try:
        recorded: Any = json.loads(payload)
    except ValueError:
        return {}
    return recorded if isinstance(recorded, dict) else {}


async def name_of(
    access: Repository, viewer: Viewer, subject: Subject, local_id: str
) -> str | None:
    """What this subject is CALLED here, for whoever is asking, or None where they may not see it.

    Resolved through the same scoped reads the router's `_may_see` uses, so a subject this viewer
    may not see has no name either, and every caller that wants a name asks only this one. A
    separate visibility check beside it would be a second read of the same row and a second refusal
    nothing could reach: for the first to pass and the second to fail, the subject would have to
    vanish between two statements of one request. Here rather than on the router because the
    ledger queue below counts by it too.
    """
    if subject is Subject.PERSON:
        person = await access.visible_person(viewer, local_id)
        return None if person is None else person.name
    if subject is Subject.SITE:
        site = await access.visible_site(viewer, local_id)
        return None if site is None else site.name
    tag = await access.visible_tag(viewer, local_id)
    return None if tag is None else tag.name


async def names_of(
    access: Repository, viewer: Viewer, subjects: Iterable[tuple[Subject, str]]
) -> dict[tuple[Subject, str], str]:
    """What each of these subjects is CALLED here, for whoever is asking: the ones they may see.

    The batched form of `name_of`: one scoped read per KIND rather than one per row, through the
    same batched reads the walls' own by-id reads are (`visible_people`, `visible_sites`,
    `visible_tags`). Absent means "may not see it" and "not there" together, as `name_of`'s None
    does.

    One read per kind whatever the page size: resolved one at a time, each Site would cost the
    whole Sites wall's counts, and that would dominate the undecided list and the ledger.
    """
    wanted: dict[Subject, list[str]] = {}
    for subject, local_id in subjects:
        wanted.setdefault(subject, []).append(local_id)
    named: dict[tuple[Subject, str], str] = {}
    if people := wanted.get(Subject.PERSON):
        for local_id, person in (await access.visible_people(viewer, people)).items():
            named[(Subject.PERSON, local_id)] = person.name
    if sites := wanted.get(Subject.SITE):
        for local_id, site in (await access.visible_sites(viewer, sites)).items():
            named[(Subject.SITE, local_id)] = site.name
    if tags := wanted.get(Subject.TAG):
        for local_id, tag in (await access.visible_tags(viewer, tags)).items():
            named[(Subject.TAG, local_id)] = tag.name
    return named


class LinkedQueue:
    """Every person, site and tag a stash-box has been agreed to know: the record beside the pile.

    A RECORD rather than a decision, for the reason the filed folders are one: nothing here is a
    question. Auto-enrich and Enrich write a link and fill what is blank, and this is the screen
    that lists what they have done: a name on a person's page says a box knows them, one at a
    time, and only this says how many. A tab of the stash-box page, never a card on
    the board. See `Band.RECORD`.
    """

    name = LINKED
    title = "Enriched by a stash-box"
    band = Band.RECORD
    group = STASH_GROUP
    #: Not first in its group, so it names no card. See `Queue.group_title`.
    group_title = None
    #: A record is never a card. See `Queue.purpose`.
    purpose = None
    #: A link is taken off from the subject's own page, not from a receipt nobody wrote.
    reversible = False

    def __init__(self, service: StashBoxService, access: Repository) -> None:
        self._service = service
        self._access = access

    async def available(self) -> bool:
        """Wherever the pile is: a library never asked has no ledger worth a tab, and an empty
        ledger beside a pile that exists is the state the pile is trying to reach."""
        return await self._service.any_match() or await self._service.any_link()

    async def survey(self, viewer: Viewer) -> Summary:
        # Counted over what this viewer may be shown, as the page is: a count that took in the
        # names a shut vault hides would say they are there. One scoped read per kind (`names_of`).
        keys = await self._service.ledger_keys()
        seen = await names_of(self._access, viewer, ((one.subject, one.local_id) for one in keys))
        return Summary(
            name=LINKED,
            title=self.title,
            verb="records linked to a stash-box",
            verb_one="record linked to a stash-box",
            decision=(
                "Every person, Site and tag Auto-enrich or Enrich linked to a stash-box. There's "
                "nothing to decide here. To remove a link, open the record."
            ),
            icon="inventory_2",
            count=sum(1 for one in keys if (one.subject, one.local_id) in seen),
        )

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Nothing: this pile records no decisions, so nothing ever asks it for pictures."""
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Nothing to put back. The links were agreed to one at a time and come off the same way."""
        return False


class UndecidedQueue:
    """The names the unattended enrichment could not choose for: a record beside the ledger.

    One box holding two certain entries for a name is a judgement about which of two people this
    is, and the enrichment declines it; this is the list to work from. Each row opens the chooser
    on the queue's own page with the name already typed, so linking never leaves the page. A row
    clears itself when the subject is linked, by whatever linked it.
    """

    name = UNDECIDED
    title = "Names with more than one entry"
    band = Band.RECORD
    group = STASH_GROUP
    group_title = None
    #: A record is never a card. See `Queue.purpose`.
    purpose = None
    reversible = False

    def __init__(self, service: StashBoxService, access: Repository) -> None:
        self._service = service
        self._access = access

    async def available(self) -> bool:
        return await self._service.any_match() or await self._service.any_link()

    async def survey(self, viewer: Viewer) -> Summary:
        # Over what this viewer may be shown, as the list is. See `LinkedQueue.survey`.
        keys = await self._service.undecided_keys()
        seen = await names_of(self._access, viewer, keys)
        return Summary(
            name=UNDECIDED,
            title=self.title,
            verb="names to choose for",
            verb_one="name to choose for",
            decision=(
                "A stash-box has more than one entry for each of these names. Open a row to "
                "choose the right one."
            ),
            icon="inventory_2",
            count=sum(1 for one in keys if one in seen),
        )

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        return False


class StudioQueue:
    """Sites a stash-box made that may be one creator's username: a question, and its record.

    A tab of the stash-box page, never a card: the board's card for the page is the pile it leads
    (`TaggerQueue`), and a question about a studio is asked where somebody is already looking at
    what the boxes said. The rule and the move are the kernel's (`kernel.access.creator_studios`);
    this is where the ones the rule could not settle are asked, and where every move (the
    catalog's own and a person's) is taken back.
    """

    name = STUDIOS
    title = "Sites that may be a username"
    #: A judgement the signs could not settle: the studio's name is or is not hers.
    band = Band.DECISION
    group = STASH_GROUP
    #: Not first in its group, so it names no card. See `Queue.group_title`.
    group_title = None
    #: What its pile is for, said once on the class. Not first in its group, so the board draws no
    #: card of its own for it. See `Queue.purpose`.
    purpose = "Sites a stash-box created that may be one person's username."
    #: Both answers, and the repair's own moves, are put back by their Undo.
    reversible = True

    def __init__(self, studios: CreatorStudios, access: Repository) -> None:
        self._studios = studios
        self._access = access

    async def available(self) -> bool:
        """Only while there is something to ask: a tab that is always empty is noise on the page."""
        return bool(await self._studios.questions())

    async def survey(self, viewer: Viewer) -> Summary:
        asked = await self._studios.questions()
        seen = await names_of(self._access, viewer, ((Subject.SITE, one.site_id) for one in asked))
        return Summary(
            name=STUDIOS,
            title=self.title,
            verb="possible usernames",
            verb_one="possible username",
            decision=(
                "A stash-box created each of these as a Site, and each may be one person's own "
                "store. Say whether it's a username or a Site."
            ),
            icon="public",
            count=sum(1 for one in asked if (Subject.SITE, one.site_id) in seen),
        )

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Stills from the files one answer moved, if this user may still be shown them."""
        pictures: list[Preview] = []
        for asset_id in _read(payload).get("moved", [])[:PREVIEW]:
            if await self._access.get_asset(viewer, str(asset_id)) is None:
                continue
            pictures.append(Preview(kind=ASSET, id=str(asset_id), href=_file_href(str(asset_id))))
        return tuple(pictures)

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Put one answer back from its receipt (`creator_studios.put_back`)."""
        _ = (viewer, receipt_id)
        return await self._studios.put_back(payload)

    def worded(self, recorded: Recorded) -> Worded | None:
        """This decision's line, worded when shown. See `kernel.workbench.Recorded`.

        A move says who did it, the Site by the name it had (it is usually gone) and the Site the
        username is on: "Sift filed the Site Cedar Vale as a username on Storefront". An answer
        that kept it says "You kept Cedar Vale as a Site". The username itself has no page to
        lead to, so it is not a piece of the line.
        """
        held = _read(recorded.payload)
        name = held.get("site_name")
        if not isinstance(name, str) or not name:
            return None
        site_id = str(held.get("site_id") or "")
        if held.get("kind") == "kept":
            return Worded(
                said=(DOER, " kept ", Named(kind="site", id=site_id, recorded=name), " as a Site")
            )
        host = held.get("host_id")
        if held.get("kind") != "turned" or not isinstance(host, str) or not host:
            return None
        return Worded(
            said=(
                DOER,
                " filed ",
                Named(kind="site", id=site_id, recorded=name, kind_said=True),
                " as a username on ",
                Named(kind="site", id=host),
            )
        )
