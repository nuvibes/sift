# SPDX-License-Identifier: AGPL-3.0-or-later
"""Folder-led attribution as workbench queues; the decisions live in the service."""

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

#: The service writes it into every receipt.
NAME = QUEUE

#: A tab of the folders page, not a list under the questions.
FILED = FILED_QUEUE

FOLDERS_GROUP = "folders"

PREVIEW = 24


def _claim_anchor(claim_id: str) -> str:
    """Where one folder's question sits on its queue's page, for a still on the board to lead to."""
    return f"/organize/{NAME}#claim-{claim_id}"


class FolderQueue:
    name = NAME
    title = "Folders to review"
    #: A name can be a person, a place or a theme; only a person knows which.
    band = Band.DECISION
    group = FOLDERS_GROUP
    group_title = "Folders"
    purpose = "Folders whose names look like a person or a Site, for you to confirm."
    reversible = True

    def __init__(self, service: SuggestionService) -> None:
        self._service = service

    async def available(self) -> bool:
        """Always: reading folder names needs nothing switched on."""
        return True

    async def survey(self, viewer: Viewer) -> Summary:
        # The outline, not a page: a page resolves every row in full.
        outline = await self._service.outline(viewer, limit=PREVIEW)
        return Summary(
            name=NAME,
            # Worded like the faces tabs' "Needs Your Input"; the slug stays for links and receipts.
            title=self.title,
            verb="folders to name",
            verb_one="folder to name",
            decision=(
                "Is this folder the person it's named after? Yes adds every file in it to that "
                "person. No discards the suggestion."
            ),
            icon="folder",
            count=outline.total,
            preview=tuple(
                Preview(kind=ASSET, id=cover, href=_claim_anchor(claim_id))
                for claim_id, cover in outline.covers[:PREVIEW]
                if cover
            ),
        )

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Stills from the files the decision filed, scoped to this viewer now."""
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
        return tuple(
            Preview(kind=ASSET, id=one, href=f"/asset/{one}") for one in wanted if one in allowed
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Put a decision back; an unreadable record answers False, never an error."""
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
        return folders_said(recorded)


class FiledQueue:
    """What a pass filed under somebody without asking: a record, never a card on the board."""

    name = FILED
    title = "Added without asking"
    band = Band.RECORD
    group = FOLDERS_GROUP
    group_title = None
    purpose = None
    #: Undone per folder; the take-back press writes its own receipt that restores it.
    reversible = True

    def __init__(self, service: SuggestionService) -> None:
        self._service = service

    async def available(self) -> bool:
        """Wherever the questions are."""
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
            # The rows, not their file counts (see `filed_total`).
            count=await self._service.filed_total(viewer),
        )

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Stills from the files one silent write filed, scoped as `FolderQueue.pictures_of`."""
        found = _folder_record(payload)
        wanted = found.assets[:PREVIEW] if found else []
        if not wanted:
            return ()
        allowed = await self._service.visible_of(viewer, wanted)
        return tuple(
            Preview(kind=ASSET, id=one, href=f"/asset/{one}") for one in wanted if one in allowed
        )

    async def reverse(self, viewer: Viewer, receipt_id: str, payload: str) -> bool:
        """Take back a folder the pass filed, or restore one taken back, by the record's kind."""
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
    """What a file's own name said about where it came from: a report, grouped by username."""

    name = FILENAMES_QUEUE
    title = "Enriched from filenames"
    band = Band.LOG
    group = None
    group_title = None
    purpose = "Files Sift added to a username because of their names."
    reversible = True

    def __init__(self, service: SuggestionService) -> None:
        self._service = service

    async def available(self) -> bool:
        """Always: reading a filename needs nothing switched on."""
        return True

    async def survey(self, viewer: Viewer) -> Summary:
        filed = await self._service.filed_from_filenames(viewer)
        # Files waiting on a Site's number nobody has named yet.
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
            # Biggest first: a wrong reading costs most where the pass filed most.
            advice=FILED_ADVICE,
            aside=_filed_aside(waiting),
        )

    async def pictures_of(self, viewer: Viewer, payload: str) -> tuple[Preview, ...]:
        """Stills from the files one sweep filed, scoped to whoever is reading the record."""
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
        """Undo one sweep's filings, one Photo Set, or a username's No, by the record's kind."""
        photo_set_id = _set_record(payload)
        if photo_set_id:
            return await self._service.take_back_set(photo_set_id, by=viewer)
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
        return filenames_said(recorded)


FILED_ADVICE = (
    "Grouped by username, largest first. Check the largest groups, and undo any file that doesn't "
    "belong."
)


def _filed_aside(waiting: int) -> Aside | None:
    """The waiting IDs as their own line with a link, or None; singular or plural."""
    if not waiting:
        return None
    # "ID" is the screen's word for a Site's number for a username.
    numbers = "ID is" if waiting == 1 else "IDs are"
    return Aside(
        said=(
            f"{many(waiting)} {numbers} waiting for a username. Add each ID to its username on "
            "the Site's People tab."
        ),
        link="Open Sites",
        href="/sites",
    )


#: A page under a reader: the name was read, never renamed. Matches `enriched:filename`.
GLYPH = "document_scanner"


@dataclass(frozen=True, slots=True)
class _FolderRecord:
    """A record about one folder and one person, read back: a silent write or a take back."""

    kind: str
    folder_id: str
    person_id: str
    assets: list[str]
    linked: bool
    refused: bool


def _folder_record(payload: str) -> _FolderRecord | None:
    """One of the two folder records from its payload, or None for any other or a partial one."""
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
    """The Photo Set one record made, by its kind, or empty."""
    try:
        recorded: Any = json.loads(payload)
    except ValueError:
        return ""
    if not isinstance(recorded, dict) or recorded.get("kind") != "post_set":
        return ""
    return str(recorded.get("photo_set_id", ""))


def _declined_record(payload: str) -> tuple[str, list[NameFiling], list[str]] | None:
    """A username's No from its record: the username, its filings and refusals; None otherwise."""
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
    """One filing decision's username and files, or `("", [])` for an unreadable record."""
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
    """What a confirmation wrote, as tuples like what went in."""
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
