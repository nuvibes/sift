# SPDX-License-Identifier: AGPL-3.0-or-later
"""The People screen: the wall, its facets, and one person made, changed, hidden or deleted."""

from __future__ import annotations

import contextlib
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)

from sift.kernel import wiring
from sift.kernel.access import (
    ADMIN_ENTITY_FACETS,
    ENTITY_FACETS,
    ENTITY_SORT_KEYS,
    ENTITY_SORT_SEEN,
    EntityNarrowing,
    ObjectType,
    Repository,
    Viewer,
    asks_disagreements,
    related_filter,
)
from sift.kernel.ledger import Actor
from sift.kernel.paging import MAX_PAGE_SIZE, resume_at
from sift.kernel.seams import DisagreementSeam, ForgetGoneSeam, RecognitionSeam, ReindexSeam
from sift.kernel.serving import face_version
from sift.kernel.wire import FacetCounts, FacetValue
from sift.slices.auth import csrf_protect, current_viewer, require_admin, require_vault_pin
from sift.slices.people.models import (
    MAX_NAME,
    AliasMatchView,
    PeopleList,
    PersonView,
    PersonWrite,
    VaultWrite,
)
from sift.slices.people.router_base import (
    _disagreement_seam,
    _missing,
    _person_from_suggestion,
    _person_view,
    _recognition,
    _require_person,
    _service,
    _site_of,
)
from sift.slices.people.service import (
    DuplicateAlias,
    PeopleService,
)

router = APIRouter(tags=["people"])


class PeopleNarrowing:
    """What the PEOPLE on the wall are, as opposed to what their files are."""

    def __init__(
        self,
        gender: Annotated[list[str] | None, Query()] = None,
        hair_color: Annotated[list[str] | None, Query()] = None,
        eye_color: Annotated[list[str] | None, Query()] = None,
        ethnicity: Annotated[list[str] | None, Query()] = None,
        country: Annotated[list[str] | None, Query()] = None,
        breast_type: Annotated[list[str] | None, Query()] = None,
        height_cm: Annotated[list[str] | None, Query()] = None,
        age: Annotated[list[str] | None, Query()] = None,
        career_start_year: Annotated[list[str] | None, Query()] = None,
        pmv_creator: Annotated[list[str] | None, Query()] = None,
        tags: Annotated[list[str] | None, Query()] = None,
        linked: Annotated[list[str] | None, Query()] = None,
        # WHICH boxes have written to the row. `linked` beside it answers yes or no to the same
        # question, and is kept so a saved address that names it still filters.
        enriched: Annotated[list[str] | None, Query()] = None,
        cover: Annotated[list[str] | None, Query()] = None,
        # WHICH BOX MADE the row, by the box's own word. A different question from `linked`, which
        # is why it is a second parameter rather than a second value of that one (see
        # `_created_by_box` in the constraint table).
        # Where a linked stash-box disagrees with what this record says. Not a column of the table
        # like every other key here (see `PeopleNarrowing.of`).
        disagrees: Annotated[list[str] | None, Query()] = None,
        created: Annotated[list[str] | None, Query()] = None,
        # What has been shared and held back: admin only, refused to anybody else by the narrowing.
        sharing: Annotated[list[str] | None, Query()] = None,
    ) -> None:
        self.picks: dict[str, list[str] | None] = {
            "gender": gender,
            "hair_color": hair_color,
            "eye_color": eye_color,
            "ethnicity": ethnicity,
            "country": country,
            "breast_type": breast_type,
            "height_cm": height_cm,
            "age": age,
            "career_start_year": career_start_year,
            "pmv_creator": pmv_creator,
            "tags": tags,
            "linked": linked,
            "enriched": enriched,
            "cover": cover,
            "created": created,
            "disagrees": disagrees,
            "sharing": sharing,
        }

    async def of(
        self,
        viewer: Viewer,
        waiting: DisagreementSeam,
        *,
        counting: str = "",
    ) -> EntityNarrowing:
        """The picks as the one conjunct the statement takes. See `EntityNarrowing`."""
        disagreeing = (
            await waiting.subjects_with_disagreements(viewer, "person")
            if asks_disagreements(self.picks, counting)
            else None
        )
        return EntityNarrowing.of(
            "person", self.picks, is_admin=viewer.is_admin, disagreeing=disagreeing
        )


@router.get("/people")
async def list_people(
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    narrowed: Annotated[PeopleNarrowing, Depends()],
    waiting: Annotated[DisagreementSeam, Depends(_disagreement_seam)],
    prefix: Annotated[str, Query(max_length=MAX_NAME)] = "",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    anywhere: Annotated[bool, Query()] = False,
    sort: Annotated[str, Query()] = ENTITY_SORT_SEEN,
    start: Annotated[str | None, Query(alias="from")] = None,
    near: Annotated[int | None, Query(ge=0)] = None,
    tag: Annotated[str | None, Query()] = None,
    site: Annotated[str | None, Query()] = None,
    collection: Annotated[str | None, Query()] = None,
    photo_set: Annotated[str | None, Query()] = None,
    song: Annotated[str | None, Query()] = None,
    with_person: Annotated[str | None, Query()] = None,
    count: Annotated[Literal["whole", "narrowed"], Query()] = "whole",
) -> PeopleList:
    """One page of the people this viewer may know about, most-seen first."""
    # Refused rather than quietly ignored, exactly as the grid's sort is: a caller who asked for an
    # order and silently got another has a wall that looks wrong for no visible reason.
    if sort not in ENTITY_SORT_KEYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown sort {sort!r}")
    # The related filter. `with_person` is the "seen with" list: everybody on the files
    # this person is on, which includes them, so the route drops their own row below rather than
    # asking the statement to, because "not this id" is a display rule and not a permission one.
    narrowing = related_filter(
        tag=tag,
        site=site,
        collection=collection,
        photo_set=photo_set,
        song=song,
        person=with_person,
    )
    # Both filters are worked out BEFORE `from` is resolved, and handed to both reads: a position
    # only means anything in the list it was taken from: resolved against the whole wall, a link
    # into a Site's People tab, or a wall filtered by a facet, would open at the wrong row.
    rows = await narrowed.of(viewer, waiting)
    if start is not None:
        at = await access.position_of_person(
            viewer,
            start,
            prefix,
            anywhere=anywhere,
            sort=sort,
            asset_filter=narrowing,
            narrowing=rows,
            count_narrowed=count == "narrowed",
        )
        offset = resume_at(at, near)
    page = await service.list_people(
        viewer,
        prefix,
        limit=limit,
        offset=offset,
        anywhere=anywhere,
        sort=sort,
        asset_filter=narrowing,
        narrowing=rows,
        count_narrowed=count == "narrowed",
    )
    marks = await access.visible_marks(viewer, ObjectType.PERSON, [one.id for one in page.items])
    art = face_version(viewer.cache_stamp)
    listed = [one for one in page.items if one.id != with_person]
    # What each card draws beside its name, read for this page only. See `Repository.card_counts`.
    counts = await access.card_counts(viewer, "person", [one.id for one in listed])
    return PeopleList(
        items=[
            _person_from_suggestion(one, marks.get(one.id), art).model_copy(
                update={"counts": counts.get(one.id, {})}
            )
            for one in listed
        ],
        total=page.total - (len(page.items) - len(listed)),
        limit=limit,
        offset=offset,
    )


@router.get("/people/facets")
async def people_facets(
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    narrowed: Annotated[PeopleNarrowing, Depends()],
    waiting: Annotated[DisagreementSeam, Depends(_disagreement_seam)],
    facet: Annotated[str, Query()],
    limit: Annotated[int, Query(ge=1, le=200)] = 24,
    prefix: Annotated[str, Query(max_length=MAX_NAME)] = "",
    anywhere: Annotated[bool, Query()] = False,
    tag: Annotated[str | None, Query()] = None,
    site: Annotated[str | None, Query()] = None,
    collection: Annotated[str | None, Query()] = None,
    photo_set: Annotated[str | None, Query()] = None,
    song: Annotated[str | None, Query()] = None,
    with_person: Annotated[str | None, Query()] = None,
) -> FacetCounts:
    """What the people this wall reaches are made of, along one dimension, with counts."""
    if facet not in ENTITY_FACETS["person"] or (
        facet in ADMIN_ENTITY_FACETS and not viewer.is_admin
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown facet {facet!r}")
    counted = await access.people_facets(
        viewer,
        facet,
        limit=limit,
        prefix=prefix,
        anywhere=anywhere,
        # `site` goes through the FILE filter here exactly as it does on the listing, rather
        # than through the statement's own `site_id`. Two ways of asking one question is how a
        # count comes to describe a different set from the wall it is drawn beside.
        asset_filter=related_filter(
            tag=tag,
            site=site,
            collection=collection,
            photo_set=photo_set,
            song=song,
            person=with_person,
        ),
        narrowing=await narrowed.of(viewer, waiting, counting=facet),
    )
    return FacetCounts(
        facet=facet,
        values=[FacetValue(value=one.value, count=one.count, label=one.label) for one in counted],
    )


@router.post("/people", status_code=status.HTTP_201_CREATED, dependencies=[Depends(csrf_protect)])
async def create_person(
    body: PersonWrite,
    request: Request,
    service: Annotated[PeopleService, Depends(_service)],
    recognition: Annotated[RecognitionSeam, Depends(_recognition)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> PersonView:
    """Add a person."""
    if body.vault:
        await require_vault_pin(request, viewer)
    made = await service.create_person(viewer, body.name, vault=body.vault, notes=body.notes)
    # The rest of the record rides on the same request: the body has already refused an address
    # that is not one, so the New page is one write and a refusal leaves no bare person behind.
    if body.record is not None:
        updated = await service.update_person(
            viewer, made.id, body.name, vault=body.vault, notes=body.notes, record=body.record
        )
        if updated is None:  # pragma: no cover (made a statement ago); a concurrent delete only
            raise _missing()
        made = updated
    if body.aliases is not None:
        await _replace_aliases(service, viewer, made.id, body.aliases)
    if body.links is not None:
        await _replace_links(service, viewer, made.id, body.links)
    # Anything a pack was holding under this name is theirs now. Nothing to press and nothing to
    # remember: the commonest way somebody ends up with an unclaimed entry is importing a stranger's
    # pack and adding the people from it over the following weeks, one at a time, by hand.
    claimed = await recognition.claim_for(made.id, body.name)
    view = _person_view(made, art=face_version(viewer.cache_stamp))
    return view.model_copy(update={"faces_claimed": claimed})


@router.put("/people/{person_id}", dependencies=[Depends(csrf_protect)])
async def update_person(
    person_id: str,
    body: PersonWrite,
    request: Request,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> PersonView:
    """Rename, re-note, and set or clear the vault flag."""
    existing = await _require_person(service, viewer, person_id)
    # `vault` is left alone when it is not sent, for the reason `notes` is and then some. A screen
    # that renames somebody sends a name; treating the field's absence as "and take them out of the
    # vault" would make every rename an un-hiding, silently, in the one direction that cannot be
    # noticed from the screen that did it: the row simply reappears for everybody.
    vault = body.vault if "vault" in body.model_fields_set else existing.vault
    if vault and not existing.vault:
        await require_vault_pin(request, viewer)
    notes = body.notes if "notes" in body.model_fields_set else existing.notes
    person = await service.update_person(
        viewer, person_id, body.name, vault=vault, notes=notes, record=body.record
    )
    if person is None:  # pragma: no cover (resolved a statement ago); a concurrent delete only
        raise _missing()
    # The lists ride on the same write as the name, so a refused entry (checked by the body model)
    # refuses the whole save before anything above was written.
    sent = body.model_fields_set
    if "aliases" in sent:
        await _replace_aliases(service, viewer, person_id, body.aliases or [])
    if "links" in sent:
        await _replace_links(service, viewer, person_id, body.links or [])
    # A person's name and every alias they answer to is indexed on each file they are on, and this
    # route CAN name those files, so it rewrites exactly them, rather than queuing a rebuild of the
    # whole index: tens of seconds of the write lock on a library of a hundred thousand files,
    # paid the same whether the person was on ten thousand files or none. Read after the write on
    # purpose: a rename moves no `asset_people` rows, so the answer is the same either side of it.
    await reindexer.touched_many(await service.assets_of_person(person_id))
    # The mark comes back with them. An edit never moves a grant, so this is the same answer the
    # list gives, but the screen replaces its row with this reply, so leaving it out is the screen
    # forgetting a restrict that is still in force.
    marks = await access.visible_marks(viewer, ObjectType.PERSON, [person.id])
    return _person_view(person, mark=marks.get(person.id), art=face_version(viewer.cache_stamp))


async def _replace_aliases(
    service: PeopleService, viewer: Viewer, person_id: str, wanted: list[str]
) -> None:
    """Make this person's other names exactly `wanted`: the ones gone removed, the new ones added."""
    held = await service.aliases_of(person_id)
    for gone in [one for one in held if one.alias not in wanted]:
        await service.remove_alias(person_id, gone.id, actor=Actor.user(viewer.id))
    kept = {one.alias for one in held}
    for added in dict.fromkeys(one for one in wanted if one not in kept):
        with contextlib.suppress(DuplicateAlias):
            await service.add_alias(person_id, added)


async def _replace_links(
    service: PeopleService, viewer: Viewer, person_id: str, wanted: list[str]
) -> None:
    """Make this person's addresses exactly `wanted`, each filed under its Site where one is known."""
    held = await service.links_of(person_id)
    for gone in [one for one in held if one.url not in wanted]:
        await service.remove_link(person_id, gone.id, actor=Actor.user(viewer.id))
    kept = {one.url for one in held}
    for added in dict.fromkeys(one for one in wanted if one not in kept):
        await service.add_link(person_id, added, site_id=await _site_of(service, added))


@router.put(
    "/people/{person_id}/vault",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def set_person_vault(
    person_id: str,
    body: VaultWrite,
    request: Request,
    service: Annotated[PeopleService, Depends(_service)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> Response:
    """Hide somebody, or bring them back, for this user."""
    if body.vault:
        await require_vault_pin(request, viewer)
    await _require_person(service, viewer, person_id)
    await service.set_person_vault(viewer, person_id, vault=body.vault)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/people/{person_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(csrf_protect)],
)
async def delete_person(
    person_id: str,
    service: Annotated[PeopleService, Depends(_service)],
    reindexer: Annotated[ReindexSeam, Depends(wiring.reindexer)],
    recognition: Annotated[RecognitionSeam, Depends(_recognition)],
    forgets: Annotated[ForgetGoneSeam, Depends(wiring.forget_gone)],
    viewer: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """Delete a person, their aliases, their assignments, and every grant naming them."""
    person = await _require_person(service, viewer, person_id)
    # BEFORE the delete, and that is the whole of the difference between this and the rename above:
    # the cascade takes the `asset_people` rows with the person, so asking afterwards which files
    # they were on answers nothing at all and the index would keep their name for ever.
    was_on = await service.assets_of_person(person_id)
    if not await service.delete_person(viewer, person_id):  # pragma: no cover (as above)
        raise _missing()
    # The cascade took the joins, so every asset they were on indexes without them now: those
    # files, and not the library.
    await reindexer.touched_many(was_on)
    # And whatever was recognized as them goes back to being a question. Said through the seam, so
    # this stays a slice that does not know face recognition exists: their faces were cut loose by
    # the key but left in no pile, which is a state no screen shows (neither identified nor
    # waiting) until somebody happened to rebuild the piles by hand.
    await recognition.released()
    await forgets.forget_gone("person", person_id, name=person.name, by=viewer)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/people/resolve")
async def resolve_term(
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    term: Annotated[str, Query(max_length=MAX_NAME)],
) -> AliasMatchView:
    """Who this term names: by name, by an alias, or by the username of a linked username."""
    match = await service.resolve(term, viewer)
    art = face_version(viewer.cache_stamp)
    # Through the same scoped read the person page uses, so the card carries the count and the
    # cover the list shows. Built any other way it could answer `asset_count: 0` and no cover for
    # somebody the list shows with thousands of files, a lie to any client reading more than
    # "is the list empty".
    found = [await access.visible_person(viewer, person_id) for person_id in match.person_ids]
    people = sorted((one for one in found if one is not None), key=lambda one: one.name)
    marks = await access.visible_marks(viewer, ObjectType.PERSON, [one.id for one in people])
    return AliasMatchView(
        term=match.term,
        people=[_person_from_suggestion(one, marks.get(one.id), art) for one in people],
    )


@router.get("/people/{person_id}")
async def read_person(
    person_id: str,
    service: Annotated[PeopleService, Depends(_service)],
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
) -> PersonView:
    """One person, as their own page reads them."""
    found = await access.visible_person(viewer, person_id)
    if found is None:
        raise _missing()
    marks = await access.visible_marks(viewer, ObjectType.PERSON, [found.id])
    view = _person_from_suggestion(found, marks.get(found.id), face_version(viewer.cache_stamp))
    # The record is read separately, and on purpose. Whether somebody may be SHOWN is one question
    # with one answer, and it is answered above by the same statement the wall uses, so this route
    # does not get its own opinion about it. What it does after that answer is yes is ask the slice
    # that owns those columns for them, which keeps fifteen fields off every row of the wall that
    # shares that statement.
    held = await service.get_person(viewer, person_id)
    # And this viewer's own O tally over their files, which the wall does not carry: it is a sum
    # over somebody's files, so a page of sixty cards would be sixty of these sums for a number no
    # card draws. Scoped to what this viewer may see (see `o_count_of_person`).
    update: dict[str, object] = {"o_count": await access.o_count_of_person(viewer, found.id)}
    # And the card's counts, read for this one person by the call the wall makes for its page, so
    # the card and the page cannot say two different numbers for one person.
    cells = await access.card_counts(viewer, "person", [found.id])
    update["counts"] = cells.get(found.id, {})
    if held is not None:
        update["record"] = dict(held.record)
    return view.model_copy(update=update)
