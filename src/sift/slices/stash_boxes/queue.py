# SPDX-License-Identifier: AGPL-3.0-or-later
"""Files a stash-box recognized, as a pile of answers to agree with a page at a time."""

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
LINKED = "linked"
UNDECIDED = "undecided"
STASH_GROUP = "stash"

#: Enough to recognise what a pile is about.
PREVIEW = 4


def _file_href(asset_id: str) -> str:
    """The client address of one recognised file, which is what its row is judged against."""
    return f"/asset/{asset_id}"


class TaggerQueue:
    name = NAME
    title = "Matches to review"
    #: An offer to accept, not a question to weigh: a page settles in one press.
    band = Band.CLEANUP
    group = STASH_GROUP
    group_title = "Files a stash-box recognized"
    purpose = "Check what a stash-box knows about your files before it is saved."
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
        # Asked late: the enrichment is wired after the board.
        self._writer = writer or (lambda: None)

    async def available(self) -> bool:
        """Whether the pass has ever found anything, so a never-scanned library shows no panel."""
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
            title=self.title,
            verb="files to review",
            verb_one="file to review",
            decision=(
                "Review what a stash-box found for each file where the match wasn't certain; "
                "Auto-enrich adds exact matches automatically. Its details appear beside yours, and "
                "nothing is saved until you confirm. You can confirm a whole page in one go."
            ),
            # Must be a glyph the client ships: an unknown one breaks the whole board.
            icon="shoppingmode",
            count=total,
            preview=tuple(pictures[:PREVIEW]),
        )

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Stills from the files one decision was about, if this user may still be shown them."""
        pictures: list[Preview] = []
        for one in _read(payload).get("matches", [])[:PREVIEW]:
            asset_id = str(one.get("asset_id", "")) if isinstance(one, dict) else ""
            if not asset_id or await self._access.get_asset(viewer, asset_id) is None:
                continue
            pictures.append(Preview(kind=ASSET, id=asset_id, href=_file_href(asset_id)))
        return tuple(pictures)

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Undo one decision: an applied answer comes off and asks again; a take-back returns."""
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
        """This decision's line, naming who, which boxes and the file."""
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
    """A take-back's receipt as a line, about this file or about how many files."""
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
    try:
        recorded: Any = json.loads(payload)
    except ValueError:
        return {}
    return recorded if isinstance(recorded, dict) else {}


async def name_of(
    access: Repository, viewer: Viewer, subject: Subject, local_id: str
) -> str | None:
    """What this subject is called here for this viewer, or None where they may not see it."""
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
    """What each subject is called for this viewer, one scoped read per kind; absent if unseen."""
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
    """Every person, site and tag a stash-box is agreed to know: a record tab, never a card."""

    name = LINKED
    title = "Enriched by a stash-box"
    band = Band.RECORD
    group = STASH_GROUP
    group_title = None
    purpose = None
    #: A link comes off from the subject's own page.
    reversible = False

    def __init__(self, service: StashBoxService, access: Repository) -> None:
        self._service = service
        self._access = access

    async def available(self) -> bool:
        """Wherever the pile is."""
        return await self._service.any_match() or await self._service.any_link()

    async def survey(self, viewer: Viewer) -> Summary:
        # Counted over what this viewer may be shown, as the page is.
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
        return ()

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        return False


class UndecidedQueue:
    """The names the unattended enrichment could not choose for, each opening the chooser."""

    name = UNDECIDED
    title = "Names with more than one entry"
    band = Band.RECORD
    group = STASH_GROUP
    group_title = None
    purpose = None
    reversible = False

    def __init__(self, service: StashBoxService, access: Repository) -> None:
        self._service = service
        self._access = access

    async def available(self) -> bool:
        return await self._service.any_match() or await self._service.any_link()

    async def survey(self, viewer: Viewer) -> Summary:
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
    """Sites a stash-box made that may be one creator's username: a tab with undoable answers."""

    name = STUDIOS
    title = "Sites that may be a username"
    band = Band.DECISION
    group = STASH_GROUP
    group_title = None
    purpose = "Sites a stash-box created that may be one person's username."
    reversible = True

    def __init__(self, studios: CreatorStudios, access: Repository) -> None:
        self._studios = studios
        self._access = access

    async def available(self) -> bool:
        """Only while there is something to ask."""
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
        """This decision's line, naming who, the Site and where the username is."""
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
