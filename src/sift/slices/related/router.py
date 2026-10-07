# SPDX-License-Identifier: AGPL-3.0-or-later
"""One request for every number on an entity page's tab strip.

## Why this exists at all

A person's page has seven tabs. Filled one at a time as each is opened, six of them have no number
until somebody presses them, so the strip says "Files 214" and then six bare words, and there is
no way to tell a tab with nothing behind it from one nobody has looked at yet. That is the whole
reason for this route: the numbers are a MAP of what an entity reaches, and a map with six blanks on
it is not one.

## Why it is not six requests

Because it is one question, taken at one moment: the subject's own row (its files), one statement
for every tab its card carries (`card_counts`), and a listing at one row for the rest.

## The number and the wall answer ONE question

A number here is the `total` of the very listing its tab draws, or the stored answer the cards read,
which the gate below holds to that listing. Never `facet_counts` or a count written here: two
populations drift apart with nothing on screen to say which one is lying. A subject this viewer may
not be shown is counted off the listings alone.

History is not counted here. Its number is the length of the thread its own request answers, which
the page asks beside this one, so the strip never waits on the walk a thread is.

The gate `test_every_count_hides_what_the_vault_hides` fetches each of these numbers over HTTP
beside the list its own tab draws, with the vault shut, and refuses any that is larger.

## Almost nothing here decides who may see anything

Every LISTING is the scoped one, so a count is over what this viewer may be shown and nothing else.
A tag on Jane's page reading "12" is twelve of the files this user may see: reporting the
library's number instead would publish the size of the set the whole model is keeping back.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from sift.kernel import wiring
from sift.kernel.access import (
    CollectionView,
    EntityNarrowing,
    PersonSuggestion,
    PhotoSetView,
    Repository,
    SiteSuggestion,
    SongView,
    TagSuggestion,
    Viewer,
    related_filter,
)
from sift.kernel.seams import DisagreementSeam
from sift.slices.auth import current_viewer
from sift.slices.related.models import RelatedCounts

router = APIRouter(tags=["related"])

#: The walls each kind of entity page can show, which is the same list the client's tab strip is
#: built from. Written out rather than derived, because it is a product decision rather than a
#: technical one: nothing here prevents any page from showing any wall.
#:
#: A collection has a Loops tab: a collection of clips is where somebody looks for the moments
#: marked in them.
#:
#: A photo set is deliberately NOT given one. It is stills that arrived together, so the tab would
#: be a nought on nearly every album, and this table is where that kind of judgement is
#: supposed to be made rather than inherited.
#:
#: **This must agree with `TABS_FOR` in the client's `related.svelte.ts`.** Two lists that must
#: agree drift apart, so there is a test that reads both files and compares them rather than a
#: comment asking somebody to remember.
TABS_FOR: dict[str, tuple[str, ...]] = {
    #: MUSIC (`songs`) stands right after Collections on a person's, a tag's and a Site's page, and
    #: last on a Collection's: every page carries the songs its files carry, and a song's page
    #: every tab but Photo Sets.
    "person": ("files", "photo_sets", "loops", "tags", "sites", "collections", "songs", "people"),
    #: A tag can hold other tags too (`tags.parent_id`), so its page has `tags_within`: the tags
    #: filed directly under it, the twin of a site's `sites_within` below.
    "tag": (
        "files",
        "photo_sets",
        "loops",
        "tags_within",
        "people",
        "sites",
        "collections",
        "songs",
    ),
    #: A SITE IS THE ONE KIND OF THING HERE THAT CAN HOLD ANOTHER OF ITS OWN KIND, which is why
    #: `sites_within` is on this row and nowhere else. The note above says nothing lists its own kind, and
    #: that still holds: "the sites this site's files came from" would be this site. This is a
    #: different question and it is answered by a column rather than by the files: `sites.
    #: parent_id` says a label is part of a network, and without reading it in that direction a
    #: network's page would be a site with nothing on it.
    "site": (
        "files",
        "photo_sets",
        "loops",
        "sites_within",
        "people",
        "tags",
        "collections",
        "songs",
    ),
    # Loops sits where it sits on every other page: straight after the files, ahead of the walls of
    # other entities. A collection has no Photo Sets tab, so that is the second slot here.
    "collection": ("files", "loops", "people", "tags", "sites", "songs"),
    "photo_set": ("files", "people", "tags", "sites"),
    # A song: the files that carry it, the marks cut from them, and who, what, where and which
    # Collections those files reach. Every tab but Photo Sets (a Photo Set is stills, which carry no
    # song), in the order the other pages put them.
    "song": ("files", "loops", "people", "tags", "sites", "collections"),
}

#: One wall's number, asked for only if this page has that wall.
Ask = Callable[[], Awaitable[int]]

#: How one kind of subject is resolved to "may this user be shown it at all".
#:
#: A mapping rather than a chain of `elif`s, for the reason `walls` below is one: a kind added to
#: `TABS_FOR` and forgotten here raises instead of quietly answering. The answer itself is thrown
#: away (only whether it is None is read) because every one of these means the same thing by it:
#: "not allowed" and "not there" together, which is the whole point of that shape.
_RESOLVE: dict[str, Callable[[Repository, Viewer, str], Awaitable[object | None]]] = {
    "person": lambda access, viewer, one: access.visible_person(viewer, one),
    "tag": lambda access, viewer, one: access.visible_tag(viewer, one),
    "site": lambda access, viewer, one: access.visible_site(viewer, one),
    "collection": lambda access, viewer, one: access.visible_collection(viewer, one),
    "photo_set": lambda access, viewer, one: access.visible_photo_set(viewer, one),
    "song": lambda access, viewer, one: access.visible_song(viewer, one),
}

#: One row is enough. The listings return the scoped total beside the page, so asking for a single
#: row buys the number without carrying six pages of cards that nothing draws.
_ONE = 1


def _disagreement_seam(request: Request) -> DisagreementSeam:
    """Whoever can say what a stash-box disagrees with about one record.

    A SHAPE and not the reconciler itself: the strip is the related slice's and the disagreements
    are the stash-box slice's, and a slice may not import another. What comes back is a NUMBER:
    the two values, the box that said so and the buttons that settle them are the panel's business,
    and a row carries a name beside a birth date, which has no place in an answer drawn on every
    entity page.

    Required, like every other dependency in this file, rather than `part_or_none`: the
    composition root wires it beside the reconciler it already builds, so an absent case would be
    a branch nothing can take, and a line nothing can ever make fail says something untrue about
    what this route needs.
    """
    return wiring.part_of(request, wiring.DISAGREEMENTS)


@router.get("/related/{kind}/{entity_id}")
async def related_counts(
    kind: str,
    entity_id: str,
    access: Annotated[Repository, Depends(wiring.access)],
    viewer: Annotated[Viewer, Depends(current_viewer)],
    waiting: Annotated[DisagreementSeam, Depends(_disagreement_seam)],
) -> RelatedCounts:
    """Every number on one entity page's tab strip.

    An unknown kind is refused rather than answered with an empty object. A strip drawn from an
    empty answer is a strip of bare words, with no way to tell a tab with nothing behind it from
    one nobody has looked at, so a client asking the wrong question is told.

    A `entity_id` naming nothing is not an error. Every narrowing built from it matches no files, so
    every count is nought, which is the honest answer for a thing that is not there and the same
    answer somebody gets for one they may not see.
    """
    wanted = TABS_FOR.get(kind)
    if wanted is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown kind {kind!r}")

    subject = await _RESOLVE[kind](access, viewer, entity_id)
    counts = RelatedCounts()
    if subject is None:
        await _count_by_listing(counts, wanted, kind, entity_id, access, viewer)
    else:
        # The subject's own row carries its files and their size, off the stored count the cards
        # read, and one statement answers every other tab its card carries.
        counts.files, counts.files_bytes = _files_of(subject)
        cells = (await access.card_counts(viewer, kind, [entity_id]))[entity_id]
        for wall in wanted:
            if wall in cells:
                setattr(counts, wall, cells[wall])
        rest = [wall for wall in wanted if wall != "files" and wall not in cells]
        await _count_by_listing(counts, rest, kind, entity_id, access, viewer, subject=subject)
    # The mark beside History, deliberately not one of the `walls`: each of those is counted off
    # the listing its tab draws. This is a question about the RECORD (where a stash-box disagrees
    # with it), asked through a seam because it is another slice's.
    counts.disagreements, counts.disagreement_boxes = await waiting.disagreement_mark(
        viewer, kind, entity_id
    )
    return counts


def _files_of(subject: object) -> tuple[int, int]:
    """The files a visible subject's row counts, and their size: its Files tab's two numbers."""
    match subject:
        case PersonSuggestion() | SiteSuggestion() | TagSuggestion():
            return subject.asset_count, subject.size_bytes
        case CollectionView() | PhotoSetView() | SongView():
            return subject.item_count, subject.size_bytes
    raise TypeError(f"no files count on {type(subject).__name__}")


async def _count_by_listing(
    counts: RelatedCounts,
    wanted: tuple[str, ...] | list[str],
    kind: str,
    entity_id: str,
    access: Repository,
    viewer: Viewer,
    *,
    subject: object | None = None,
) -> None:
    """Each wanted tab's number off the listing that tab draws, asked at one row."""
    # The filtering every wall on this page gets. `people` is the one exception and it is handled
    # where it is counted. See `_seen_with`.
    narrowing = related_filter(**{kind: entity_id})
    # The files wall is read once and answers two fields: its total and the size of those files,
    # which are one read's two halves (`AssetPage.total_bytes`), so the two cannot describe
    # different sets.

    async def files() -> int:
        page = await access.visible_assets(viewer, limit=_ONE, asset_filter=narrowing)
        counts.files_bytes = page.total_bytes
        return page.total

    walls: dict[str, Ask] = {
        "files": files,
        "photo_sets": lambda: _total(
            access.list_photo_sets(viewer, limit=_ONE, asset_filter=narrowing)
        ),
        # The one wall whose filtering is not only the filter. A tag reaches a mark directly as well
        # as through the video it was cut from, so on a tag's page the tag is handed over as `tag`
        # and INSTEAD of the filter, never as well: in the filter it would filter to the tagged
        # videos and lose the marks tagged in their own right. The count still comes from the very
        # listing the tab draws, which is the rule this file keeps. See `loops_query`.
        "loops": lambda: _total(
            access.list_loops(
                viewer,
                limit=_ONE,
                asset_filter=related_filter() if kind == "tag" else narrowing,
                tag=entity_id if kind == "tag" else None,
            )
        ),
        # The one wall here that is NOT the page's filter applied to a listing, and it is drawn
        # from the same listing all the same. A site's children are named by a column, so the
        # filtering is `parent` rather than the asset filter, and the number still comes off the
        # very page the tab will fetch, which is the rule this file keeps.
        "sites_within": lambda: _total(access.list_sites(viewer, "", limit=_ONE, parent=entity_id)),
        # The same for a tag: the tags filed directly under it, by the wall's own `parent` facet.
        "tags_within": lambda: _total(
            access.list_tags(
                viewer,
                limit=_ONE,
                narrowing=EntityNarrowing.of(
                    "tag", {"parent": [entity_id]}, is_admin=viewer.is_admin
                ),
            )
        ),
        "tags": lambda: _total(access.list_tags(viewer, limit=_ONE, asset_filter=narrowing)),
        "sites": lambda: _total(access.list_sites(viewer, "", limit=_ONE, asset_filter=narrowing)),
        "collections": lambda: _total(
            access.list_collections(viewer, limit=_ONE, asset_filter=narrowing)
        ),
        # The Music tab: the songs this page's files carry, off the very listing the tab draws.
        "songs": lambda: _total(access.list_songs(viewer, limit=_ONE, asset_filter=narrowing)),
        "people": lambda: _people(access, viewer, kind, entity_id, narrowing, subject),
    }

    for wall in wanted:
        setattr(counts, wall, await walls[wall]())


async def _total(page: Coroutine[Any, Any, Any]) -> int:
    """The scoped total off any of the listings. They all answer with one, which is what makes a
    single reader possible rather than seven."""
    return int((await page).total)


async def _people(
    access: Repository,
    viewer: Viewer,
    kind: str,
    entity_id: str,
    narrowing: object,
    subject: object | None,
) -> int:
    """How many people this page's files reach, and on a PERSON's page, how many others.

    "Seen with" is the people on the files this person is on, which includes them, because they
    are on every one of those files. The wall drops their own card, so the number beside the word
    has to drop them too or the strip says one more than the wall shows.

    Subtracted from the TOTAL rather than by filtering a page, which is what the wall itself does.
    At one row that would be wrong: the wall removes whichever of the rows it fetched is this
    person, and a page of one usually is not them, so the count would come back unreduced. The exact
    question is "is this person in the set at all", and they are in it exactly when they are visible
    to this viewer and the set is not empty, because a person is on every file of their own.
    """
    if kind != "person":
        page = await access.suggest_people(viewer, "", limit=_ONE, asset_filter=narrowing)  # type: ignore[arg-type]
        return page.total
    page = await access.suggest_people(
        viewer, "", limit=_ONE, asset_filter=related_filter(person=entity_id)
    )
    if page.total == 0:
        return 0
    return page.total - 1 if subject is not None else page.total
