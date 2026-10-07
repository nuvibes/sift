# SPDX-License-Identifier: AGPL-3.0-or-later
"""The People wall and its lookups, scoped to the viewer asking."""

from __future__ import annotations

import json
from collections.abc import Sequence

from sift.kernel.access.constraints import (
    NO_FILTER,
    NO_NARROWING,
    AllOf,
    AssetFilter,
    EntityNarrowing,
    Where,
)
from sift.kernel.access.repository.core import _POINT_WHERE, RepositoryCore
from sift.kernel.access.repository.entities import (
    _ALIAS_TARGETS,
    ENTITY_SORT_SEEN,
    PERSON_BY_ID,
    _entity_sort,
    people_position,
    people_query,
)
from sift.kernel.access.repository.views import (
    AliasMatch,
    FacetCount,
    PeoplePage,
    PersonSuggestion,
    _is_object_id,
    _like_anywhere,
    _like_prefix,
    _suggested,
)
from sift.kernel.access.repository.walls import _one_seam, plain_order
from sift.kernel.access.viewer import Viewer
from sift.kernel.paging import MAX_PAGE_SIZE


def username_sites_of(asset_filter: AssetFilter) -> str | None:
    """The Sites a People wall is filtered to, as the JSON array it binds, when that is ALL it is.

    A Site's People are those with a file there OR a username there
    (`_A_USERNAME_SHOWS_ITS_PERSON`). Read off the file filter every reader of the wall already
    carries, so the list, its count and its facets describe the same people. Exactly one `sites`
    condition and nothing else, or None: anything else falls back to files, which can only show
    fewer people, never more.
    """
    node = asset_filter.where
    # The one condition, bare or the only part of an AllOf (what `related_filter` builds).
    leaf = node.parts[0] if isinstance(node, AllOf) and len(node.parts) == 1 else node
    if not (isinstance(leaf, Where) and leaf.key == "sites" and leaf.values):
        return None
    # ...and nothing else set anywhere: the same filter rebuilt from that condition alone.
    if asset_filter != AssetFilter(where=node):
        return None
    return json.dumps([str(one) for one in leaf.values])


#: A handful of people by id, as the unfiltered People wall draws them, off the stored counts, each
#: sought by its id: the wall's own `:person_ids` test reads every person to keep a few.
_PEOPLE_BY_IDS = _one_seam(
    people_query(_POINT_WHERE),
    "people_query",
    "AND (:person_ids IS NULL OR p.id IN (SELECT value FROM json_each(:person_ids)))",
    "AND p.id IN (SELECT value FROM json_each(:person_ids))",
)


class PeopleReads(RepositoryCore):
    """The scoped reads of people."""

    async def people_facets(
        self,
        viewer: Viewer,
        facet: str,
        *,
        limit: int = 24,
        prefix: str = "",
        anywhere: bool = False,
        site_id: str | None = None,
        asset_filter: AssetFilter = NO_FILTER,
        narrowing: EntityNarrowing = NO_NARROWING,
    ) -> list[FacetCount]:
        """What the people on this wall are made of, along one dimension, under every filtering
        the listing takes, so the panel describes the wall as it is being looked at."""
        return await self._entity_facets(
            "person",
            facet,
            asset_filter=asset_filter,
            narrowing=narrowing,
            params=self._person_params(
                viewer,
                person_id=None,
                site_id=site_id,
                prefix=prefix,
                anywhere=anywhere,
                limit=max(1, min(limit, MAX_PAGE_SIZE)),
                asset_filter=asset_filter,
            ),
        )

    async def suggest_people(
        self,
        viewer: Viewer,
        prefix: str = "",
        *,
        limit: int = 20,
        offset: int = 0,
        site_id: str | None = None,
        anywhere: bool = False,
        sort: str = ENTITY_SORT_SEEN,
        asset_filter: AssetFilter = NO_FILTER,
        narrowing: EntityNarrowing = NO_NARROWING,
        count_narrowed: bool = False,
    ) -> PeoplePage:
        """People this viewer may know about, most-seen first: the one people suggester.

        The name only; an alias or a username is `resolve_alias_targets`. `anywhere` matches inside
        the name, for the box that filters the wall, where a surname must find somebody. `site_id`
        is a Site's People. `count_narrowed` makes a row's number how many of theirs are on THIS
        wall, which a card that opens the filtered wall has to say; off, every file of theirs.
        """
        if limit < 1:
            raise ValueError("a suggestion list needs at least one row")
        limit = min(limit, MAX_PAGE_SIZE)

        where, bound = asset_filter.predicate()
        # ...and the wall's OWN rows, filtered by what the thing is rather than by its files. A
        # second seam beside the file filter rather than part of it; see `_row_narrowed`.
        narrowed, picked = narrowing.predicate()
        params = self._person_params(
            viewer,
            person_id=None,
            site_id=site_id,
            prefix=prefix,
            anywhere=anywhere,
            limit=limit,
            offset=max(0, offset),
            sort=sort,
            asset_filter=asset_filter,
            count_narrowed=count_narrowed,
        )
        rows = await self._db.fetch_all(
            people_query(
                where,
                narrowed,
                plain=plain_order(params, "person_id", "person_ids", "site_id", "username_sites"),
            ),
            bound | picked | params,
        )
        # Nothing on the page means nothing to read a total from: nobody, or past the end.
        total = int(rows[0]["total_count"]) if rows else 0
        return PeoplePage(items=[_suggested(row) for row in rows], total=total)

    def _person_params(
        self,
        viewer: Viewer,
        *,
        person_id: str | None,
        person_ids: str | None = None,
        site_id: str | None = None,
        prefix: str = "",
        anywhere: bool = False,
        limit: int,
        offset: int = 0,
        sort: str = ENTITY_SORT_SEEN,
        asset_filter: AssetFilter = NO_FILTER,
        count_narrowed: bool = False,
    ) -> dict[str, object]:
        """What the people statement binds. One place; see `_tag_params`. `count_narrowed` is off
        for a by-id read: a person's own page counts every file of theirs this viewer may see."""
        return {
            "viewer": viewer.id,
            "is_admin": 1 if viewer.is_admin else 0,
            "list_empty": self._lists_empty_rows(viewer, asset_filter),
            "reveal": self._reveal_existence(viewer),
            "reveal_named": self._reveal_named(viewer),
            # A read naming its row by id keeps the name of a row the walls draw as a locked tile;
            # see `_LOCKED_TILE`. Worked out here, beside the id it is about, so no caller can
            # bind the id and forget the other.
            "by_id": 1 if person_id is not None or person_ids is not None else 0,
            "person_id": person_id,
            # A set of ids, for the batched read; None is no filter. See `visible_people`.
            "person_ids": person_ids,
            "site_id": site_id,
            # A wall filtered to one Site and nothing else also lists the people with a USERNAME
            # there. Off the same filter the statement filters by, so no caller can bind one and
            # not the other. See `username_sites_of`.
            "username_sites": username_sites_of(asset_filter),
            "prefix": prefix,
            "like": _like_anywhere(prefix) if anywhere else _like_prefix(prefix),
            "entity_sort": _entity_sort(sort),
            # Which tally a card on this wall prints. See `suggest_people`.
            "count_narrowed": 1 if count_narrowed else 0,
            "limit": limit,
            "offset": offset,
        }

    async def position_of_person(
        self,
        viewer: Viewer,
        person_id: str,
        prefix: str = "",
        *,
        site_id: str | None = None,
        anywhere: bool = False,
        sort: str = ENTITY_SORT_SEEN,
        asset_filter: AssetFilter = NO_FILTER,
        narrowing: EntityNarrowing = NO_NARROWING,
        count_narrowed: bool = False,
    ) -> int | None:
        """How far into the People wall somebody sits, counting from zero. None if they are not in it.

        What `position_of` is for the grid: the address carries the person, and this turns it back
        into a place. Every argument matches `suggest_people`, the file filter, the row filter and
        `count_narrowed` included, since a position only means anything in the list it came from.
        None for somebody this viewer may not be shown and for an id never minted, alike.
        """
        # An id that is not an id matches nothing, and is never bound as NULL: the statement reads
        # a NULL here as "no filter" and would answer with the position of an arbitrary row.
        if not _is_object_id(person_id):
            return None

        where, bound = asset_filter.predicate()
        narrowed, picked = narrowing.predicate()
        rows = await self._db.fetch_all(
            people_position(where, narrowed),
            bound
            | picked
            | self._person_params(
                viewer,
                # The wall is never filtered to one person, and this must not be either: binding an
                # id here would rank a list of one and every answer would be position zero. Neither
                # by-id filtering is bound, for that one reason.
                person_id=None,
                site_id=site_id,
                prefix=prefix,
                anywhere=anywhere,
                # Not read by this statement: it ranks the whole wall and picks one row out of the
                # ranking. Bound anyway, because `_person_params` binds everything the people
                # statement can take and that is the whole point of there being one place. Writing
                # this dictionary out by hand would leave a parameter added there unbound here, and
                # the failure is a hard error at the driver.
                limit=1,
                sort=sort,
                asset_filter=asset_filter,
                count_narrowed=count_narrowed,
            )
            | {"position_of": person_id},
        )
        if not rows:
            return None
        # ROW_NUMBER counts from one and an offset counts from zero. Converted here, once, rather
        # than at each caller.
        return int(rows[0]["position"]) - 1

    async def visible_person(self, viewer: Viewer, person_id: str) -> PersonSuggestion | None:
        """One person, if this viewer may be shown them; None is "no such person" and "not for you"
        alike. The list's own query, filtered to one id, never a second opinion about who exists."""
        # A non-id is never bound: the statement reads a NULL as "no filter".
        if not _is_object_id(person_id):
            return None
        rows = await self._db.fetch_all(
            PERSON_BY_ID,
            self._unfiltered() | self._person_params(viewer, person_id=person_id, limit=1),
        )
        return _suggested(rows[0]) if rows else None

    async def visible_people(
        self, viewer: Viewer, person_ids: Sequence[str]
    ) -> dict[str, PersonSuggestion]:
        """The people among these ids that this viewer may be shown, keyed by id: the batched
        `visible_person`. Absent is "not allowed" and "not there" alike. Bounded by the caller's
        list, never a page. No `_is_object_id` guard: the ids bind as a JSON array, where a non-id
        simply matches nothing.
        """
        wanted = sorted(set(person_ids))
        found: dict[str, PersonSuggestion] = {}
        for start in range(0, len(wanted), MAX_PAGE_SIZE):
            chunk = wanted[start : start + MAX_PAGE_SIZE]
            rows = await self._db.fetch_all(
                _PEOPLE_BY_IDS,
                self._unfiltered()
                | self._person_params(
                    viewer, person_id=None, person_ids=json.dumps(chunk), limit=len(chunk)
                ),
            )
            for row in rows:
                one = _suggested(row)
                found[one.id] = one
        return found

    async def resolve_alias_targets(
        self, viewer: Viewer, term: str, *, limit: int = 20
    ) -> AliasMatch:
        """Everyone this term names: by their name, by an alias, or by a linked username. The one
        resolver, for search and the People screen alike. Scoped, since an answer says somebody by
        that name is here: a vaulted person is named to nobody."""
        if limit < 1:
            raise ValueError("a suggestion list needs at least one row")
        limit = min(limit, MAX_PAGE_SIZE)

        cleaned = term.strip()
        if not cleaned:
            # No query for an empty box, which would match everybody.
            return AliasMatch(term=cleaned, person_ids=())

        rows = await self._db.fetch_all(
            _ALIAS_TARGETS,
            {
                "viewer": viewer.id,
                "is_admin": 1 if viewer.is_admin else 0,
                "reveal": self._reveal_existence(viewer),
                "reveal_named": self._reveal_named(viewer),
                "term": cleaned,
                "limit": limit,
            },
        )
        return AliasMatch(term=cleaned, person_ids=tuple(row["id"] for row in rows))
