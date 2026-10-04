# SPDX-License-Identifier: AGPL-3.0-or-later
"""What every stash-box endpoint shares: the service, the refusals, and the wire shapes."""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from fastapi import HTTPException, Request, status

from sift.kernel.access import Repository, Viewer
from sift.kernel.access.catalog import KEPT_LOCAL_KINDS, refused_here, refused_over, set_refused_on
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now
from sift.kernel.db import Database
from sift.kernel.ledger import Actor, record_event
from sift.kernel.reach import (
    OUT_OF_REACH,
)
from sift.kernel.records import (
    FoundRecord,
    Subject,
)
from sift.kernel.site_icons import is_a_sites_own, is_index_host
from sift.kernel.site_icons import path_of as site_icon_path
from sift.kernel.site_icons import slug_for as site_icon_slug
from sift.kernel.site_icons import slug_for_name as site_icon_slug_for_name
from sift.kernel.vocabulary import Subject as DecisionSubject
from sift.kernel.wiring import part_of
from sift.slices.stash_boxes.models import (
    BoxAnswer,
    EnrichmentState,
    RecordFound,
)
from sift.slices.stash_boxes.service import (
    KEPT_LOCAL,
    SERVICE,
    Answer,
    StashBoxService,
)

#: How many answers one page of the pile carries, and the most one press may settle.
#:
#: The same number for both on purpose: the button says "apply everything on this page", so a press
#: that could reach past the page would be a decision somebody could not see the whole of.
MATCH_PAGE = 100

#: How many rows a page of the ledger, or of the undecided list, carries.
LEDGER_PAGE = 50


def _service(request: Request) -> StashBoxService:
    return part_of(request, SERVICE)


def _missing() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "no such stash-box")


def _no_such_file() -> HTTPException:
    """The 404 for an ASSET, which is a different thing from a stash-box that is not there."""
    return HTTPException(status.HTTP_404_NOT_FOUND, OUT_OF_REACH)


def _kept_local() -> HTTPException:
    """The refusal for something that must never be sent outside this machine."""
    return HTTPException(status.HTTP_409_CONFLICT, KEPT_LOCAL)


def _kept_local_kind(subject: str) -> Subject:
    """One of the four kinds a stash-box is ever told about, or a 404. A folder is never told
    about and has no record; its mark is `folder_enrichment_state`'s and `set_folder_kept_local`'s."""
    try:
        found = Subject(subject)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such subject") from None
    if found.value not in KEPT_LOCAL_KINDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such subject")
    return found


def _needs_a_key(key: bytes | None) -> bytes:
    """A stash-box key is sealed under the master key, which exists only while somebody is signed in
    with their password. A session resumed from a cookie after a restart has none yet."""
    if key is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Your saved keys are locked. Enter your password in the box at the top of this page to "
            "unlock them, then try again.",
        )
    return key


async def _exists(service: StashBoxService, box_id: str) -> bool:
    return any(one.id == box_id for one in await service.boxes())


def _answer(answer: Answer) -> BoxAnswer:
    return BoxAnswer(
        box_id=answer.source_id,
        box_name=answer.source_name,
        records=[_record(one, answer.source_name) for one in answer.records],
        fetched_at=answer.fetched_at,
        fresh=answer.fresh,
        problem=answer.problem,
    )


def _pack_slug(found: FoundRecord) -> str | None:
    """The shipped pack's word for this entry, where it is a site the pack knows."""
    if found.subject is not Subject.SITE:
        return None
    links = found.fields.get("links")
    for link in links if isinstance(links, list) else []:
        # A box lists a studio's page on the databases first (ThePornDB, the box itself), and its
        # profiles elsewhere (a page on X): each names where the studio is, never the studio, and
        # the pack knows each such host as a site of its own. Only a front door counts, the rule a
        # Site row's own address is held to (`is_a_sites_own`), so a studio wears its logo or none.
        if not isinstance(link, str) or is_index_host(link) or not is_a_sites_own(link):
            continue
        slug = site_icon_slug(link)
        if slug is not None:
            return _shipped(slug)
    named = site_icon_slug_for_name(found.name) if found.name else None
    return _shipped(named)


def _shipped(slug: str | None) -> str | None:
    # A withheld entry (a photograph, a mascot, a link kind) is still RECOGNISED by the pack so a
    # row called "Studio" is not handed another site's logo, but it has no picture: naming it
    # here would cost the chooser a 404 per row before it fell back to the box's own picture.
    return slug if slug is not None and site_icon_path(slug) is not None else None


def _record(found: FoundRecord, box_name: str) -> RecordFound:
    # The picture is handed out as SIFT's address, not the stash-box's. Two things follow: the page
    # can draw it under `img-src 'self'`, and a client never learns a remote address it could ask
    # the proxy above to fetch on its behalf.
    through_sift = (
        f"/api/stash-boxes/{found.source_id}/picture?url={quote(found.image_url, safe='')}"
        if found.image_url
        else None
    )
    return RecordFound(
        source_id=found.source_id,
        source_name=box_name,
        remote_id=found.remote_id,
        subject=str(found.subject),
        name=found.name,
        disambiguation=found.disambiguation,
        image_url=through_sift,
        icon_slug=_pack_slug(found),
        file_count=found.file_count,
        every_word=found.every_word,
        fields=dict(found.fields),
        extra=dict(found.extra),
        confidence=found.confidence,
    )


# --- Links ------------------------------------------------------------------------------------

#: Which subjects can be linked, by the word that appears in the address.
#:
#: A file is deliberately absent. What a stash-box knows about a file is answered by its
#: fingerprints rather than by its name, which is a different question and a different pass.
_SUBJECTS = {
    "person": Subject.PERSON,
    "site": Subject.SITE,
    "tag": Subject.TAG,
}


def _subject(subject: str) -> Subject:
    known = _SUBJECTS.get(subject)
    if known is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "nothing of that kind can be linked")
    return known


async def _may_see(access: Repository, viewer: Viewer, subject: Subject, local_id: str) -> bool:
    """Whether this viewer may be shown the thing being asked about.

    404 for a subject they may not see and 404 for one that was never minted, which is the same
    answer to both, so a link route cannot become a way to ask whether somebody exists.
    """
    if subject is Subject.ASSET:
        # `open_asset`, never `get_asset`, and the difference is the one `jobs.scan` pays for in
        # full: a concealed row comes back from `get_asset` as a locked PLACEHOLDER carrying the
        # real row behind it, so a screen in placeholder mode could read and set the flag on a file
        # in a shut vault. A placeholder is not something to act on.
        return await access.open_asset(viewer, local_id) is not None
    if subject is Subject.PERSON:
        return await access.visible_person(viewer, local_id) is not None
    if subject is Subject.SITE:
        return await access.visible_site(viewer, local_id) is not None
    return await access.visible_tag(viewer, local_id) is not None


# --- A folder's "Don't enrich" -----------------------------------------------------------------

#: The word the routes take for a folder. Not a `Subject`: a folder has no record and no box is
#: ever asked about one. Its mark keeps every file under it at home.
FOLDER: Literal["folder"] = "folder"

#: Why a folder is kept local when its own switch is not the reason.
KEPT_LOCAL_BY_A_FOLDER_ABOVE = "Kept local by a folder it's inside"


async def folder_name(access: Repository, viewer: Viewer, folder_id: str) -> str | None:
    """What this folder is called, for this viewer, or None where they may not see it: the scoped
    read the folder pages use, so a folder in a shut vault is not there."""
    folder = await access.get_folder(viewer, folder_id)
    return None if folder is None else folder.name


async def folder_enrichment_state(
    database: Database, folder_id: str, kept: bool | None = None
) -> EnrichmentState:
    """Where a folder stands with enrichment, in the shape the other subjects answer with. No runs:
    no box is ever asked about a folder. `kept` is what a write has just set, as for the others."""
    own = await refused_here(database, "enrich", FOLDER, folder_id) if kept is None else kept
    refused = own or await refused_over(database, "enrich", FOLDER, folder_id)
    return EnrichmentState(
        subject=FOLDER,
        id=folder_id,
        kept_local=own,
        refused=refused,
        why="" if not refused else "Kept local" if own else KEPT_LOCAL_BY_A_FOLDER_ABOVE,
        runs=[],
    )


async def set_folder_kept_local(
    database: Database, viewer: Viewer, folder_id: str, kept: bool, *, name: str
) -> bool:
    """Put "Don't enrich" on a folder or take it off, and record who did, in one transaction. False
    when there is no such folder. Recorded only when the column moved, as "Don't swap" is."""
    moved = await refused_here(database, "enrich", FOLDER, folder_id) != kept
    async with database.write() as connection:
        if not await set_refused_on(connection, "enrich", FOLDER, folder_id, kept):
            return False
        if moved:
            await record_event(
                connection,
                actor=Actor.user(viewer.id),
                verb="kept_local" if kept else "allowed",
                subject=DecisionSubject(kind=FOLDER, id=folder_id, name=name),
            )
    if moved:
        announce_now(EVERY_ADMIN, About.LIBRARY)
    return True
