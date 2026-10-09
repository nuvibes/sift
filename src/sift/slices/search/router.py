# SPDX-License-Identifier: AGPL-3.0-or-later
"""The search endpoints: the dropdown, the parser, saved searches, and one user's history."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from sift.kernel.access import Viewer
from sift.kernel.client import client_of
from sift.kernel.serving import face_version
from sift.kernel.use_history import keeps_history
from sift.kernel.wiring import SETTINGS_HUB, part_of
from sift.slices.auth import csrf_protect, current_viewer
from sift.slices.search.filters import (
    SUGGESTED_FIELDS,
    Field,
    Term,
    clauses,
    filters_matching,
    parse,
    phrase_prefix,
    problems_in,
    token_prefix,
    word_prefix,
)
from sift.slices.search.models import (
    FilterOut,
    KeptNameOut,
    Named,
    NameOut,
    NamesNow,
    ParsedClause,
    ParsedProblem,
    ParsedQuery,
    RecentOut,
    RememberSearchRequest,
    RenameSearchRequest,
    SavedSearches,
    SavedSearchOut,
    SaveSearchRequest,
    SearchOpenedRequest,
    SuggestionOut,
    Suggestions,
)
from sift.slices.search.service import (
    MAX_BAND,
    QUERY_KIND,
    SERVICE,
    NameTaken,
    Remembered,
    SearchService,
    Suggestion,
    TooMany,
    is_a_wall,
    is_rememberable,
)

router = APIRouter(tags=["search"])


def _service(request: Request) -> SearchService:
    return part_of(request, SERVICE)


def _offered(row: Suggestion, *, field: str | None, art: str) -> SuggestionOut:
    """One suggestion as the wire spells it, written once for every route that sends rows."""
    cover = row.cover
    return SuggestionOut(
        field=field,
        opens=row.opens,
        value=row.value,
        detail=row.detail,
        count=row.count,
        id=row.entity_id,
        art=art if cover is not None else None,
        cover_asset_id=cover.asset_id if cover is not None else None,
        cover_upload_id=cover.upload_id if cover is not None else None,
        cover_at_ms=cover.at_ms if cover is not None else None,
        cover_frame=cover.frame if cover is not None else None,
        icon=cover.icon if cover is not None else None,
    )


@router.get("/search/suggest")
async def suggest(
    service: Annotated[SearchService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    q: Annotated[str, Query(max_length=1000)] = "",
    field: Annotated[str, Query(max_length=40)] = "",
    prefix: Annotated[str, Query(max_length=200)] = "",
) -> Suggestions:
    """What to show under the box for the whole line typed so far, parsed here, never in a client.
    `field` and `prefix` instead complete a record form's box for one field, unparsed."""
    art = face_version(viewer.cache_stamp)
    if field:
        named = next((one for one in SUGGESTED_FIELDS if one.value == field), None)
        matches = await service.suggest(viewer, named, prefix) if named is not None else []
        return Suggestions(
            token=field,
            for_query=prefix,
            matches=[_offered(row, field=field, art=art) for row in matches],
        )

    caret = token_prefix(q)
    if caret is not None:
        # Inside a token: that field only, said even with no matches so the box can draw the chip.
        matches = (
            await service.suggest(viewer, caret.field, caret.prefix)
            if caret.field in SUGGESTED_FIELDS
            else []
        )
        return Suggestions(
            token=caret.field.value,
            replace_from=caret.at,
            for_query=q,
            matches=[_offered(row, field=caret.field.value, art=art) for row in matches],
        )

    # A bare word: filters, library names and recents, answered together as three groups.
    word = word_prefix(q)
    prefix = word.prefix if word else ""
    # With no word being typed, a chosen filter is inserted at the end.
    at = word.at if word else len(q)

    # Matched anywhere in a name, as people type the part they remember.
    across = await service.suggest_across(viewer, prefix, anywhere=True) if word is not None else []

    # The whole trailing run of words first, since names have spaces; the last word is the fallback.
    phrase = phrase_prefix(q)
    matched_at = at
    if phrase is not None:
        wider = await service.suggest_across(viewer, phrase.prefix, anywhere=True)
        if wider:
            across = wider
            matched_at = phrase.at

    return Suggestions(
        replace_from=at,
        matched_from=matched_at,
        for_query=q,
        filters=[
            FilterOut(
                field=entry.field.value,
                label=entry.label,
                hint=entry.hint,
                example=entry.example,
                set_from=entry.set_from,
            )
            for entry in filters_matching(prefix)
        ],
        matches=[
            _offered(row, field=row.field.value if row.field is not None else None, art=art)
            for row in across
        ],
        recent=[_remembered(row) for row in await service.recent(viewer, q)],
    )


@router.get("/search/named")
async def named_by(
    service: Annotated[SearchService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    q: Annotated[str, Query(max_length=1000)] = "",
) -> Named:
    """The things in the library a plain word names, through the dropdown's scoped suggesters."""
    found = await service.suggest_across(viewer, q, limit=MAX_BAND, anywhere=True)
    art = face_version(viewer.cache_stamp)
    return Named(
        items=[
            _offered(row, field=row.field.value if row.field is not None else None, art=art)
            for row in found
        ]
    )


#: The kinds a screen keeps by id, in the screens' own words, each to the field that resolves it.
_NAMED_KINDS: dict[str, Field] = {
    "tag": Field.TAGS,
    "person": Field.PEOPLE,
    "site": Field.SITES,
    "collection": Field.COLLECTIONS,
    "photo_set": Field.PHOTO_SETS,
    "song": Field.SONGS,
}

#: A ceiling so one request is never a walk of the library.
_MOST_NAMED = 100


@router.get("/search/names-now")
async def names_now(
    service: Annotated[SearchService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    kind: Annotated[str, Query(max_length=20)],
    ids: Annotated[list[str], Query(alias="id")],
) -> NamesNow:
    """What each of these ids of one kind is called now; an unseen id is simply absent."""
    field_name = _NAMED_KINDS.get(kind)
    if field_name is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown kind {kind!r}.")
    wanted = list(dict.fromkeys(one for one in ids if 0 < len(one) <= 64))
    if len(wanted) > _MOST_NAMED:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"At most {_MOST_NAMED} ids at a time."
        )
    named = await service.names_now(viewer, field_name, wanted)
    return NamesNow(items=[NameOut(id=key, name=name) for key, name in named.items()])


@router.get("/search/parse")
async def parse_query(
    viewer: Annotated[Viewer, Depends(current_viewer)],
    q: Annotated[str, Query(max_length=1000)] = "",
) -> ParsedQuery:
    """What a typed query means, so the Filters modal can show what is in force without a parser."""
    parsed = parse({"q": q})
    terms: dict[str, list[str]] = {}
    for leaf in parsed.leaves():
        if isinstance(leaf, Term):
            terms.setdefault(leaf.field.value, []).append(leaf.value)
    assert viewer is not None  # noqa: S101 (the dependency refuses without one)
    return ParsedQuery(
        text=parsed.text or "",
        clauses=[
            ParsedClause(
                query=clause.query,
                field=clause.field.value if clause.field is not None else None,
                values=list(clause.values),
                negated=clause.negated,
                present=clause.present,
                match=clause.match.value,
            )
            for clause in clauses(parsed)
        ],
        problems=[
            ParsedProblem(field=one.field, value=one.value, reason=one.reason)
            for one in problems_in(parsed, now=int(time.time()))
        ],
        terms=terms,
    )


def _remembered(row: Remembered) -> RecentOut:
    """One row of the memory, on the wire; it is only ever shown through `suggest`."""
    return RecentOut(kind=row.kind, subject=row.subject, label=row.label)


@router.post("/search/history", status_code=status.HTTP_204_NO_CONTENT)
async def remember_search(
    body: RememberSearchRequest,
    request: Request,
    service: Annotated[SearchService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> Response:
    """Note that this user ran a search or picked something out of the dropdown."""
    if not is_rememberable(body.kind):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That is not a kind of thing the box remembers.",
        )
    # The client it came from, and whether the person's search record is paused.
    client = client_of(request)
    keep_record = await keeps_history(part_of(request, SETTINGS_HUB), viewer.id)
    if body.kind == QUERY_KIND:
        await service.remember(
            viewer, body.subject, results=body.results, client=client, keep_record=keep_record
        )
    else:
        await service.remember_pick(
            viewer,
            body.kind,
            body.subject,
            body.label,
            results=body.results,
            client=client,
            keep_record=keep_record,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/search/opened", status_code=status.HTTP_204_NO_CONTENT)
async def search_opened(
    body: SearchOpenedRequest,
    request: Request,
    service: Annotated[SearchService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> Response:
    """Note that this user opened a file from the wall a typed search narrowed."""
    await service.opened(
        viewer,
        body.query,
        body.asset_id,
        client=client_of(request),
        keep_record=await keeps_history(part_of(request, SETTINGS_HUB), viewer.id),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/search/history", status_code=status.HTTP_204_NO_CONTENT)
async def clear_history(
    service: Annotated[SearchService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    _: Annotated[None, Depends(csrf_protect)] = None,
    q: Annotated[str | None, Query(max_length=1000)] = None,
) -> Response:
    """Forget the lot, or one entry, and its record; scoped, so only the caller's own."""
    await service.forget(viewer, q)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/search/saved")
async def saved_searches(
    service: Annotated[SearchService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> SavedSearches:
    """This user's saved searches, newest first, under the names things have today."""
    saved = await service.kept_filters(viewer)
    return SavedSearches(
        items=[
            SavedSearchOut(
                id=item.id,
                name=item.name,
                query=item.query,
                kind=item.kind,
                named=[
                    KeptNameOut(field=one.spelled, value=one.value, name=one.name)
                    for one in item.noted
                ],
            )
            for item in saved
        ]
    )


@router.post("/search/saved", status_code=status.HTTP_204_NO_CONTENT)
async def save_search(
    body: SaveSearchRequest,
    service: Annotated[SearchService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> Response:
    """Keep a query under a name; saving under a used name replaces its query."""
    # Only a wall that exists: a filter is offered back only on its own wall.
    if not is_a_wall(body.kind):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "That is not a wall a filter can be kept on.",
        )
    try:
        await service.save_search(viewer, body.name, body.query, body.kind)
    except TooMany as full:
        # 409: the user's own state refuses it, not the request.
        raise HTTPException(status.HTTP_409_CONFLICT, str(full)) from full
    except ValueError as exc:
        # A whitespace-only name trims to nothing in the service: a bad request, not a fault.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/search/saved/{saved_id}", status_code=status.HTTP_204_NO_CONTENT)
async def rename_saved_search(
    saved_id: str,
    body: RenameSearchRequest,
    service: Annotated[SearchService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> Response:
    """Change a saved search's name, keeping the query it points at."""
    try:
        renamed = await service.rename_saved_search(viewer, saved_id, body.name)
    except NameTaken as taken:
        # This user already keeps a search under that name; merging would lose one.
        raise HTTPException(
            status.HTTP_409_CONFLICT, f'You already have a saved search called "{taken}".'
        ) from taken
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if not renamed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/search/saved/{saved_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_search(
    saved_id: str,
    service: Annotated[SearchService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> Response:
    """Drop one saved search of the caller's; another user's id touches nothing."""
    await service.delete_saved_search(viewer, saved_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
