# SPDX-License-Identifier: AGPL-3.0-or-later
"""Usernames: the list, one username changed, and joined to a person or taken apart."""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)

from sift.kernel import wiring
from sift.kernel.access import (
    ENTITY_SORT_KEYS,
    ENTITY_SORT_SEEN,
    NUMBER_TYPED,
    Repository,
    UsernameNumber,
    UsernameSuggestion,
    Viewer,
    set_username_number,
    username_number,
)
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.kernel.paging import MAX_PAGE_SIZE, resume_at
from sift.kernel.partial_write import UNCHANGED
from sift.kernel.seams import ReindexSeam
from sift.kernel.site_icons import icon_token as site_icon_token
from sift.kernel.workbench import Recorder
from sift.slices.auth import csrf_protect, current_viewer, require_admin
from sift.slices.people.models import (
    UsernameMerge,
    UsernamePageView,
    UsernameView,
    UsernameWrite,
)
from sift.slices.people.queue import join_receipt
from sift.slices.people.router_base import _missing, _service
from sift.slices.people.service import (
    PeopleService,
    UsernameFacts,
)

router = APIRouter(tags=["people"])

# --- usernames ---------------------------------------------------------------------------------


async def _visible_username_or_404(
    access: Repository, viewer: Viewer, username_id: str
) -> UsernameSuggestion:
    """One username this viewer may be shown, or 404. The same two-step every entity write uses."""
    found = await access.visible_username(viewer, username_id)
    if found is None:
        raise _missing()
    return found


def _username_view(found: UsernameSuggestion) -> UsernameView:
    return UsernameView(
        id=found.id,
        username=found.name,
        asset_count=found.asset_count,
        size_bytes=found.size_bytes,
        display_name=found.display_name,
        url=found.url,
        site_id=found.site_id,
        site_name=found.site_name,
        person_id=found.person_id,
        person_name=found.person_name,
        name_candidates=found.name_candidates,
    )


@router.get("/usernames")
async def list_usernames(
    access: Annotated[Repository, Depends(wiring.access)],
    service: Annotated[PeopleService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    q: str = "",
    site_id: str | None = None,
    person_id: str | None = None,
    unattached: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: str = ENTITY_SORT_SEEN,
    start: Annotated[str | None, Query(alias="from", max_length=200)] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
) -> UsernamePageView:
    """Usernames this viewer may know about."""
    # Refused rather than quietly ignored, as every sibling of this route does: a word nobody
    # declared would otherwise be answered in whatever order the statement liked.
    if sort not in ENTITY_SORT_KEYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown sort {sort!r}")
    if start is not None:
        at = await access.position_of_username(
            viewer,
            start,
            q,
            anywhere=True,
            sort=sort,
            site_id=site_id,
            person_id=person_id,
            unattached=unattached,
        )
        offset = resume_at(at, near)
    page = await access.list_usernames(
        viewer,
        q,
        limit=limit,
        offset=offset,
        anywhere=True,
        sort=sort,
        site_id=site_id,
        person_id=person_id,
        unattached=unattached,
        # Only the list of WAITING usernames asks how many people answer to each spelling: it is what
        # says whether a row is a judgement (several do) or an offer to make somebody (none does),
        # and it costs a subquery per row. Every other use of this route binds it off.
        name_candidates=unattached is True,
    )
    views = [_username_view(one) for one in page.items]
    # FILTERED TO ONE PERSON OR ONE SITE is how the two tabs that show a username ask (a person's
    # Sites tab, a site's People tab), and a username has no page of its own, so what that page
    # drew beside it travels with it here: the site's own number, how it was learned, and the site's
    # logo. Every other list leaves them out: one more statement per page for
    # columns no other screen draws. See `PeopleService.username_facts`.
    if person_id is not None or site_id is not None or unattached is True:
        facts = await service.username_facts([one.id for one in views])
        views = [_with_facts(one, facts.get(one.id)) for one in views]
    return UsernamePageView(items=views, total=page.total, offset=offset)


def _with_facts(view: UsernameView, facts: UsernameFacts | None) -> UsernameView:
    """One listed username with its number, the number's provenance and its site's logo drawn in."""
    if facts is None:
        return view
    return view.model_copy(
        update={
            "number": facts.number.number,
            "number_via": facts.number.via,
            "number_said": _number_said(facts.number),
            "site_icon": site_icon_token(facts.site_url, facts.site_name),
        }
    )


def _number_said(number: UsernameNumber) -> str | None:
    """How this library came by a username's number, in one sentence, or None where it cannot say."""
    if number.number is None or number.via is None:
        return None
    if number.via == NUMBER_TYPED:
        return "You typed this ID."
    if number.agreed is None:  # pragma: no cover (every learned number records its count)
        return None
    return (
        f"Sift read this ID from the Artist and ImageDescription fields of "
        f"{number.agreed} of this username's pictures."
    )


def _number_taken(other: str | None, site: str | None) -> str:
    """The 409 a typed ID answers when another username on the same Site already has it."""
    where = f"this {site} ID" if site else "this ID"
    who = other.strip() if other else ""
    holder = who if who else "Another username"
    return f"{holder} already has {where}. These may be the same person, renamed."


async def _type_number(
    database: Database,
    viewer: Viewer,
    username_id: str,
    number: str,
    replace: bool,
    site_name: str | None,
) -> None:
    """Write the ID somebody typed for a username, or refuse it with the sentence that says why."""
    # THE DOOR FOR A FACT NOBODY ELSE HOLDS. A filename shape can carry a Site's own username
    # number and no username, and until somebody says what that number is called the files it
    # names sit under no site at all. This is the only way in for the numbers Sift cannot read
    # off the pictures, typically the ones whose files are all videos.
    typed = await set_username_number(
        database,
        username_id=username_id,
        number=number,
        replace=replace,
        actor=Actor.user(viewer.id),
    )
    if typed.outcome == "held":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This username already has a different ID.",
        )
    if typed.outcome == "clash":
        # The uniqueness rule, answered as the fact it usually is: one person who renamed.
        # Answered here rather than as an unhandled database error (a 500). The other username
        # is named because it is what the person has to go and look at; the detail is a sentence
        # for them, read by the sheet as is.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_number_taken(typed.other_name, site_name),
        )
    if typed.outcome == "missing":  # pragma: no cover (resolved at the top of the route)
        raise _missing()


@router.put("/usernames/{username_id}", dependencies=[Depends(csrf_protect)])
async def update_username(
    username_id: str,
    body: UsernameWrite,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    database: Annotated[Database, Depends(wiring.database)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> UsernameView:
    """Write the editable half of a username: what it is shown as, its page, its ID, who holds it."""
    found = await _visible_username_or_404(access, viewer, username_id)
    sent = body.model_fields_set
    touched: list[str] = []
    if "number" in sent and body.number:
        await _type_number(
            database, viewer, username_id, body.number, body.replace_number, found.site_name
        )
    await service.set_username_details(
        username_id,
        display_name=body.display_name if "display_name" in sent else UNCHANGED,
        url=body.url if "url" in sent else UNCHANGED,
    )
    if "person_id" in sent:
        if body.person_id is None:
            await service.detach_username(username_id, actor=Actor.user(viewer.id))
        else:
            # Resolved through the scoped read first, exactly as the join route next door does it.
            # Without it a person who is not there reaches the write and the foreign key refuses,
            # which is a 500 on a request whose honest answer is "no such person".
            if not await access.visible_person(viewer, body.person_id):
                raise _missing()
            landed = await service.attach_username(
                username_id, person_id=body.person_id, actor=Actor.user(viewer.id)
            )
            if landed is None:  # pragma: no cover (both were resolved a line ago)
                raise _missing()
            # WIDER than this username's files, and the reason is easy to miss: `attach_username`
            # takes `as_alias` and it defaults to TRUE, so joining a person here writes the username
            # onto them as an also-known-as. An alias is indexed on every file that person is on,
            # which is a different set from the files under the username. The username's own files are
            # gathered below, for both branches together; this is the half only a join adds.
            touched = await service.assets_of_person(landed)
    if sent & {"person_id", "display_name"}:
        # The words indexed on the files under this username changed: the display name is indexed
        # beside the username, and detaching a person unfiles them from the same set. Named and
        # rewritten rather than the whole library rebuilt (twice over, when a caller sent both).
        # Read after the write, because neither branch moves a file off the username.
        touched += await service.assets_of_username(username_id)
        await reindexer.touched_many(list(dict.fromkeys(touched)))
    # Read back rather than echoed, the number included: a form that has just written one has to be
    # shown what the row actually holds, which is the only thing that can say a fill landed.
    saved = _username_view(await _visible_username_or_404(access, viewer, username_id))
    number = await username_number(database, username_id)
    return saved.model_copy(
        update={
            "number": number.number,
            "number_via": number.via,
            "number_said": _number_said(number),
        }
    )


@router.post("/usernames/{username_id}/person", dependencies=[Depends(csrf_protect)])
async def merge_username_into_person(
    username_id: str,
    body: UsernameMerge,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    recorder: Annotated[Recorder, Depends(wiring.recorder)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> UsernameView:
    """Say who a username belongs to: somebody who already exists, or somebody new."""
    await _visible_username_or_404(access, viewer, username_id)
    if body.person_id is not None and not await access.visible_person(viewer, body.person_id):
        raise _missing()
    try:
        landed = await service.attach_username(
            username_id,
            person_id=body.person_id,
            new_person_name=body.new_person_name,
            as_alias=body.as_alias,
            # Who is making them, where this call makes somebody. The enrichment path next door
            # passes nothing, because a pass runs for nobody in particular.
            by_user=viewer.id,
            actor=Actor.user(viewer.id),
            # The usernames queue's answer, written as its receipt: counted, and with Undo.
            receipt=join_receipt(recorder, viewer.id),
        )
    except ValueError as refusal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(refusal)) from refusal
    if landed is None:  # pragma: no cover (the username was resolved a few lines above)
        raise _missing()
    # A username joining a person changes the indexed text of every file under it. An alias may have
    # been written onto the person as well (this is the one route that asks for that), and an
    # alias is indexed on every file that person is on, which is a WIDER set than the username's. So
    # both are named and the two are rewritten together, rather than the whole library being rebuilt
    # because one of the two sets was awkward to state.
    await reindexer.touched_many(
        list(
            dict.fromkeys(
                [
                    *await service.assets_of_username(username_id),
                    *await service.assets_of_person(landed),
                ]
            )
        )
    )
    return _username_view(await _visible_username_or_404(access, viewer, username_id))


@router.delete("/usernames/{username_id}/person", dependencies=[Depends(csrf_protect)])
async def detach_username_from_person(
    username_id: str,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> None:
    """Take the pointer off a username. The person stays, and so does any alias that was written."""
    await _visible_username_or_404(access, viewer, username_id)
    detached = await service.detach_username(username_id, actor=Actor.user(viewer.id))
    if not detached:  # pragma: no cover (resolved a line ago)
        raise _missing()
    # The pointer and the attributions it made both went, and both of them only ever reached files
    # under this username: the undo is filtered to them by the statement that writes it. So those
    # files are what needs rewriting. The alias stays, so nothing outside them changed.
    await reindexer.touched_many(await service.assets_of_username(username_id))
