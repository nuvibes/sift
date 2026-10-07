# SPDX-License-Identifier: AGPL-3.0-or-later
"""The search endpoints: the dropdown, the parser, saved searches, and one user's history.

**Results are not here, and that is deliberate.** A search is a filtered view of the library, not a
different collection, so it is answered by the same address the grid is: one page shape, one
total, one set of query parameters, and no way for two endpoints to come to different conclusions
about who may see what. The box builds those parameters; the grid answers them.

Three rules run through everything below, and each is here because search is the one screen that
lets somebody guess.

**Nothing is logged.** No route in this module writes the query anywhere but the asking user's
own history. A structured log line saying how many terms a search had is still a record of when
somebody searched and how hard, and there is no operator problem it solves that is worth it.

**Every answer is scoped, including the ones that are not results.** The dropdown comes from the
same access layer the grid reads, so a restricted asset is absent from both. A suggester that
offered a hidden person's name would have answered the question before the search ran.

**History belongs to the user, not to the instance.** It is written for the person who searched,
read back only to them, and emptied by them. An admin has no route to anybody else's.
"""

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
    """One suggestion as the wire spells it: the dropdown's rows and the band's, written once.

    Four routes' worth of rows spelled out four times would be a field added to one and forgotten by
    three: a chip drawn from a row that cannot name its own cover. The cover's fields are copied
    off the scoped row the suggester already read (`Suggestion.cover`); `art` is the user's token,
    the same `face_version` every entity wall stamps its rows with, and it is sent only beside a
    cover, since a folder or a file row has no cover address for it to name.
    """
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
    """What to show under the box, given everything typed into it so far.

    ## The `field` and `prefix` form, which is not for the search box

    A record form has boxes that name a THING the library already knows about (a site's other
    names, the network it belongs to, a person's other names), and they complete from the same
    lists this answers with, because a second list of what is in the library is a second answer to
    one question.

    They cannot use `q`. Composing `sites:Northlight Raw` means knowing where a value has to be
    quoted, and that is the grammar: the one thing the client is not allowed a second copy of.
    So the field and the prefix arrive apart and are never parsed. A field this version does not
    know is an empty list rather than an error: an older client asking about a vocabulary that has
    gone gets no completions and still works.

    The whole line is sent rather than a token and a prefix worked out by the browser, so that the
    one parser decides where the caret is: a second implementation of "which token am I in" in
    the client would be a second grammar, and it would disagree about quoting first.

    Three things it can be showing, in order of how specific the answer is:

      - the caret is inside a token (`people:ja`): the matches for that one field;
      - the caret is on a bare word (`ja`): the matches for it from across the catalog, each
        carrying the field it would complete to, so a word alone becomes the right token;
      - neither: this user's recent searches, narrowed to what has been typed.
    """
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
        # Inside a token: that field only, and said even with no matches (`rating:`), since the
        # token is what the box needs to draw the filter as a chip.
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

    # Not inside a token, so a bare word could be the start of a filter's name, the name of
    # something in the library, or neither. All three are answered together and the client draws
    # them as three groups, which is what makes an empty box useful: clicking into one now lists the
    # filters that exist, and nothing else in the interface ever said the language was there.
    word = word_prefix(q)
    prefix = word.prefix if word else ""
    # With no word being typed, a chosen filter is inserted at the end rather than over anything.
    at = word.at if word else len(q)

    # Matched ANYWHERE in a name, as people type the part they remember (`solb` for Reya Solberg);
    # the noise is paid for in order, exactly as the results band beneath orders it.
    across = await service.suggest_across(viewer, prefix, anywhere=True) if word is not None else []

    # The whole trailing run of words FIRST, because names have spaces in them; the last word is
    # the fallback (`beach sunset` still offers `sunset`). Whichever answered sets `matched_from`,
    # the span a picked name replaces, apart from `replace_from`, which a picked filter replaces.
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
    """The things in the library a plain word NAMES, as against the files it appears in.

    Searching `reya` should answer with Reya Solberg as well as with the files whose names match,
    and this is that half. It is presentation of a query that already existed rather than a new
    one: the same scoped suggesters the dropdown reads, asked to match anywhere instead of only at
    the start, because a band answers "what is called this" and a dropdown completes a word.

    Nothing here is a second read of the library. A name offered by a list that a search would not
    return is a leak (the answer to "is there somebody called that" would have been the band
    rather than the results), so a restricted person is absent from this exactly as they are
    absent from the dropdown.
    """
    found = await service.suggest_across(viewer, q, limit=MAX_BAND, anywhere=True)
    art = face_version(viewer.cache_stamp)
    return Named(
        items=[
            _offered(row, field=row.field.value if row.field is not None else None, art=art)
            for row in found
        ]
    )


#: The kinds a screen keeps by id and names through `/search/names-now`, in the screens' own words
#: (the picker's and the swap drawer's), each to the filter field that resolves it.
_NAMED_KINDS: dict[str, Field] = {
    "tag": Field.TAGS,
    "person": Field.PEOPLE,
    "site": Field.SITES,
    "collection": Field.COLLECTIONS,
    "photo_set": Field.PHOTO_SETS,
    "song": Field.SONGS,
}

#: The most ids one ask names. A picker remembers at most fifty of a kind and the swap drawer
#: holds fewer; this is the ceiling that keeps one request from being a walk of the library.
_MOST_NAMED = 100


@router.get("/search/names-now")
async def names_now(
    service: Annotated[SearchService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    kind: Annotated[str, Query(max_length=20)],
    ids: Annotated[list[str], Query(alias="id")],
) -> NamesNow:
    """What each of these ids of one kind is called now, for a screen that keeps things by id.

    A kept id is the thing whatever it is called later, and a kept NAME is a copy that goes stale
    at the first rename: this is how a screen that keeps ids draws today's names. An id this viewer
    may not be shown is answered exactly as one that names nothing, by its absence.
    """
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
    """What a typed query means, so the Filters modal can show what is already in force.

    Pure: it reads no rows and takes no viewer beyond requiring one, because the answer is a
    property of the text rather than of the library. It is a route only so that the client does not
    have to parse, which is the one thing the client must never do, since a second parser
    disagrees with this one and there is then nothing to say which is right.

    Authenticated, like everything else here. It discloses nothing about the library, but an
    endpoint that will parse arbitrary text for anybody is still not something to leave open.
    """
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
    """One row of the memory, on the wire.

    THE DROPDOWN IS THE ONLY PLACE THIS LIST IS SHOWN, and that is a decision rather than a gap.
    There is no read route of its own: recent searches are answered by `suggest`, filtered by
    whatever is in the box, which is the only moment anybody wants them.
    """
    return RecentOut(kind=row.kind, subject=row.subject, label=row.label)


@router.post("/search/history", status_code=status.HTTP_204_NO_CONTENT)
async def remember_search(
    body: RememberSearchRequest,
    request: Request,
    service: Annotated[SearchService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    _: Annotated[None, Depends(csrf_protect)] = None,
) -> Response:
    """Note that this user did this: ran a search, or picked something out of the dropdown.

    A submission rather than a page view, and that is the whole reason it is a route of its own.
    The read behind it answers every screen made of tiles, so recording there would fill the list
    with an entry for opening the library, and a history of blanks is one nobody looks at twice.

    Both kinds through one address, because the memory is one list and the dropdown draws it as
    one. An entry need not be typeable to be re-run: a picked person is re-run by going back to
    them, which is what picking them did.

    An unknown kind is refused rather than stored. The client is what turns a kind into somewhere
    to go, so a kind nothing recognises is a row that can never be drawn, taking a place in a
    fifty-row memory from something that works.

    Written for the user who asked, read back only to them, and emptied by them. It is a
    feature; nothing about a query reaches the structured log.
    """
    if not is_rememberable(body.kind):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That is not a kind of thing the box remembers.",
        )
    # The window and device it came from, and whether the person's history is kept at all: the
    # record of the search is theirs to pause (`kernel/use_history.py`); the box's Recent list is not
    # the record and goes on working.
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
    """Note that this user opened a file from the wall a typed search narrowed.

    The other click a search leads to. A pick out of the dropdown is written down on its own, and
    without this "which of the results did I open" would have no answer for a search somebody
    typed and entered, which is most of them. Written for the user who asked, about a file they
    may open, and never while their history is paused. Answers nothing either way: a note about
    what somebody did is not worth a refusal on their screen.
    """
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
    """Forget the lot, or forget one entry, and the record of it with it.

    A guest may clear their own history and only their own, so this is not admin-gated: it is
    scoped, which is stronger: there is no id in the request that could name another user's row.
    """
    await service.forget(viewer, q)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/search/saved")
async def saved_searches(
    service: Annotated[SearchService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> SavedSearches:
    """This user's saved searches, newest first, under the names things have today. There is no
    route to anybody else's."""
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
    """Keep a query under a name. Saving under a name already used replaces its query, so this is
    both 'save' and 'update'. Not admin-gated: a guest saves their own searches and only their own,
    which is scoped rather than merely permitted: nothing in the request names another user."""
    # A wall that exists, for the reason the memory refuses a kind nothing draws: a kept filter is
    # offered back only on the wall its kind names, so a word no wall answers to is a filter stored
    # against somebody's cap and shown to them nowhere.
    if not is_a_wall(body.kind):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "That is not a wall a filter can be kept on.",
        )
    try:
        await service.save_search(viewer, body.name, body.query, body.kind)
    except TooMany as full:
        # 409 rather than 422: nothing about the request is wrong, and sending it again with a
        # different name will not help. The user's own state is what refuses it.
        raise HTTPException(status.HTTP_409_CONFLICT, str(full)) from full
    except ValueError as exc:
        # A name that is whitespace only passes the length check on the raw string and trims to
        # nothing in the service. That is a bad request, not a server fault.
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
    """Change a saved search's name, keeping the query it points at.

    The half that saving cannot do: saving under a name already used replaces that name's query,
    so the query can be edited by saving, and this is how a search is called something else.
    Scoped to the asker, exactly as delete is: an id belonging to another user names no row, and
    comes back as a plain not-found rather than as a refusal that would confirm the row exists.
    """
    try:
        renamed = await service.rename_saved_search(viewer, saved_id, body.name)
    except NameTaken as taken:
        # An ordinary collision, not a fault: this user already keeps a search under that name,
        # and merging two would lose one of them. The sentence is what somebody acts on.
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
    """Drop one saved search. Scoped to the asker: an id belonging to another user names no row
    this touches, so a guessed id is a no-op rather than a way to delete somebody else's."""
    await service.delete_saved_search(viewer, saved_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
