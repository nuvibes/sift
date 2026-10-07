# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Sites wall and the Usernames under it, scoped to the viewer asking."""

from __future__ import annotations

from collections.abc import Sequence

from sift.kernel.access.constraints import (
    NO_FILTER,
    NO_NARROWING,
    AssetFilter,
    EntityNarrowing,
)
from sift.kernel.access.repository.core import RepositoryCore
from sift.kernel.access.repository.entities import (
    _VISIBLE_USERNAMES,
    ENTITY_SORT_SEEN,
    SITE_BY_ID,
    SITES_BY_ID,
    _entity_sort,
    sites_position,
    sites_query,
    usernames_position,
    usernames_query,
)
from sift.kernel.access.repository.views import (
    FacetCount,
    SitePage,
    SiteSuggestion,
    UsernamePage,
    UsernameSuggestion,
    _is_object_id,
    _like_anywhere,
    _like_prefix,
    _site_from_row,
    _username_from_row,
)
from sift.kernel.access.repository.walls import plain_order
from sift.kernel.access.viewer import Viewer
from sift.kernel.paging import MAX_PAGE_SIZE


class SiteReads(RepositoryCore):
    """The scoped reads of Sites and usernames."""

    async def site_facets(
        self,
        viewer: Viewer,
        facet: str,
        *,
        limit: int = 24,
        prefix: str = "",
        anywhere: bool = False,
        parent: str | None = None,
        asset_filter: AssetFilter = NO_FILTER,
        narrowing: EntityNarrowing = NO_NARROWING,
    ) -> list[FacetCount]:
        """What the sites on this wall are made of, along one dimension. See `people_facets`."""
        return await self._entity_facets(
            "site",
            facet,
            asset_filter=asset_filter,
            narrowing=narrowing,
            params=self._site_params(
                viewer,
                site_id=None,
                prefix=prefix,
                anywhere=anywhere,
                limit=max(1, min(limit, MAX_PAGE_SIZE)),
                asset_filter=asset_filter,
                parent=parent,
            ),
        )

    # --- usernames-------------------------------------------------------------------------

    async def list_usernames(
        self,
        viewer: Viewer,
        prefix: str = "",
        *,
        limit: int = 20,
        offset: int = 0,
        anywhere: bool = False,
        sort: str = ENTITY_SORT_SEEN,
        site_id: str | None = None,
        person_id: str | None = None,
        unattached: bool | None = None,
        asset_filter: AssetFilter = NO_FILTER,
        name_candidates: bool = False,
    ) -> UsernamePage:
        """One page of usernames this viewer may know about, with the scoped total beside it.

        `site_id` filters to one site's usernames and `person_id` to one human's, which are the
        two ways anybody arrives at this list. `unattached` is the third and it is the Organize
        queue's: usernames nobody has said who they belong to. None means "either", so a wall that
        does not ask gets both: a filter that defaulted to False would make the ordinary list
        quietly exclude the rows the queue is about.

        Scoped through the same resolver every other wall uses. A username whose only files are ones
        this user was never shown is a username they are not told exists.
        """
        if limit < 1:
            raise ValueError("a suggestion list needs at least one row")
        limit = min(limit, MAX_PAGE_SIZE)
        if offset < 0:
            raise ValueError("a page cannot start before the first row")

        where, bound = asset_filter.predicate()
        params = self._username_params(
            viewer,
            username_id=None,
            site_id=site_id,
            person_id=person_id,
            unattached=unattached,
            prefix=prefix,
            anywhere=anywhere,
            limit=limit,
            offset=offset,
            sort=sort,
            asset_filter=asset_filter,
            name_candidates=name_candidates,
        )
        rows = await self._db.fetch_all(
            usernames_query(
                where,
                plain=plain_order(params, "username_id", "site_id", "person_id", "unattached"),
            ),
            bound | params,
        )
        total = int(rows[0]["total_count"]) if rows else 0
        return UsernamePage(items=[_username_from_row(row) for row in rows], total=total)

    async def position_of_username(
        self,
        viewer: Viewer,
        username_id: str,
        prefix: str = "",
        *,
        anywhere: bool = False,
        sort: str = ENTITY_SORT_SEEN,
        site_id: str | None = None,
        person_id: str | None = None,
        unattached: bool | None = None,
        asset_filter: AssetFilter = NO_FILTER,
    ) -> int | None:
        """How far into the usernames wall one username sits, counting from zero.

        None if it is not in it (joined to somebody since, which is how a row leaves the queue of
        waiting usernames, or never visible to this viewer); the two answer alike. Every argument
        `list_usernames` takes that decides WHICH rows and in what order, for the reason
        `position_of_photo_set` gives: a position only means anything in the list it was taken from.
        """
        # An id that is not an id matches nothing, and is never bound as NULL. See
        # `position_of_collection`.
        if not _is_object_id(username_id):
            return None

        where, bound = asset_filter.predicate()
        rows = await self._db.fetch_all(
            usernames_position(where),
            bound
            | self._username_params(
                viewer,
                # The wall is never filtered to one username, and this must not be either.
                username_id=None,
                site_id=site_id,
                person_id=person_id,
                unattached=unattached,
                prefix=prefix,
                anywhere=anywhere,
                # Not read by this statement; bound anyway. See `position_of_tag`.
                limit=1,
                sort=sort,
                asset_filter=asset_filter,
            )
            | {"position_of": username_id},
        )
        if not rows:
            return None
        # ROW_NUMBER counts from one and an offset counts from zero. Converted here, once.
        return int(rows[0]["position"]) - 1

    async def visible_username(self, viewer: Viewer, username_id: str) -> UsernameSuggestion | None:
        """One username, if this viewer may be shown it. See `visible_person`.

        Built from the statement the list uses, narrowed to one id. A by-id route deciding this for
        itself would be a second opinion about what exists, which is how a list comes to refuse
        something a detail route hands over.
        """
        if not _is_object_id(username_id):
            return None
        rows = await self._db.fetch_all(
            _VISIBLE_USERNAMES,
            self._unfiltered() | self._username_params(viewer, username_id=username_id, limit=1),
        )
        if not rows:
            self._log_denied(viewer, username_id)
            return None
        return _username_from_row(rows[0])

    def _username_params(
        self,
        viewer: Viewer,
        *,
        username_id: str | None,
        site_id: str | None = None,
        person_id: str | None = None,
        unattached: bool | None = None,
        prefix: str = "",
        anywhere: bool = False,
        limit: int,
        offset: int = 0,
        sort: str = ENTITY_SORT_SEEN,
        asset_filter: AssetFilter = NO_FILTER,
        name_candidates: bool = False,
    ) -> dict[str, object]:
        """What the usernames statement binds. One place; see `_tag_params`.

        `list_empty` is the ordinary rule and gets no exception for a by-id read. A username with
        nothing visible under it still has a page for an ADMIN, because that is what the wall's
        rule already says, and letting a by-id read bypass the rule would make a username
        readable by anybody holding its id, when the same user
        asking for the site it is on would be refused. A username's own row names a person, a site
        and a spelling; that is exactly the disclosure the rule exists to withhold.
        """
        return {
            "viewer": viewer.id,
            "is_admin": 1 if viewer.is_admin else 0,
            "list_empty": self._lists_empty_rows(viewer, asset_filter),
            "reveal": self._reveal_existence(viewer),
            "reveal_named": self._reveal_named(viewer),
            "username_id": username_id,
            "site_id": site_id,
            "person_id": person_id,
            "unattached": None if unattached is None else (1 if unattached else 0),
            "prefix": prefix,
            "like": _like_anywhere(prefix) if anywhere else _like_prefix(prefix),
            "entity_sort": _entity_sort(sort),
            # Whether to work out how many people answer to each username. Off for every caller but
            # the queue of waiting usernames: it is a correlated subquery and nothing else asks the
            # question. See the column in `_VISIBLE_USERNAMES`.
            "name_candidates": 1 if name_candidates else 0,
            "limit": limit,
            "offset": offset,
        }

    # --- sites -------------------------------------------------------------------------

    async def suggest_sites(
        self,
        viewer: Viewer,
        prefix: str = "",
        *,
        limit: int = 20,
        anywhere: bool = False,
        sort: str = ENTITY_SORT_SEEN,
    ) -> list[SiteSuggestion]:
        """Sites this viewer may know about, most-seen first.

        The one site suggester, for the reason `suggest_tags` gives. It matches the name, and
        it is scoped: a site nothing visible came from is a site this viewer is not told exists.

        `anywhere` is the same flag the other three carry, under the same rule (see
        `suggest_tags`).
        """
        if limit < 1:
            raise ValueError("a suggestion list needs at least one row")
        return (
            await self.list_sites(viewer, prefix, limit=limit, anywhere=anywhere, sort=sort)
        ).items

    async def list_sites(
        self,
        viewer: Viewer,
        prefix: str = "",
        *,
        limit: int = 20,
        offset: int = 0,
        anywhere: bool = False,
        sort: str = ENTITY_SORT_SEEN,
        asset_filter: AssetFilter = NO_FILTER,
        narrowing: EntityNarrowing = NO_NARROWING,
        parent: str | None = None,
        count_narrowed: bool = False,
    ) -> SitePage:
        """One page of the same list, with the scoped total beside it. See `list_tags`.

        `parent` filters to the sites directly under one site, which is what a network's Sites tab
        is a page of. It is a filter of ROWS rather than of files, so it sits beside the filter
        instead of inside it: "the labels of this network that this person is on" is both at once,
        and neither has to know about the other.

        `count_narrowed` says which of the two tallies the number on a row is. Off, it is every
        item under that row this viewer may see, which is what the plain wall and the row's own
        page mean by it. On, it is how many of them are on THIS wall, which is what a card whose
        press carries the page it was pressed from has to say, or the number and the page it opens
        describe different sets with nothing on screen saying so. The People wall's rule, the same
        here, on the tags wall and on the photo sets wall; see `suggest_people`.
        """
        if limit < 1:
            raise ValueError("a suggestion list needs at least one row")
        limit = min(limit, MAX_PAGE_SIZE)
        if offset < 0:
            raise ValueError("a page cannot start before the first row")

        where, bound = asset_filter.predicate()
        # ...and the wall's OWN rows, filtered by what the thing is rather than by its files. A
        # second seam beside the file filter rather than part of it; see `_row_narrowed`.
        narrowed, picked = narrowing.predicate()
        params = self._site_params(
            viewer,
            site_id=None,
            prefix=prefix,
            anywhere=anywhere,
            limit=limit,
            offset=offset,
            sort=sort,
            asset_filter=asset_filter,
            parent=parent,
            count_narrowed=count_narrowed,
        )
        rows = await self._db.fetch_all(
            sites_query(where, narrowed, plain=plain_order(params, "site_id", "parent_id")),
            bound | picked | params,
        )
        total = int(rows[0]["total_count"]) if rows else 0
        return SitePage(items=[_site_from_row(row) for row in rows], total=total)

    def _site_params(
        self,
        viewer: Viewer,
        *,
        site_id: str | None,
        prefix: str = "",
        anywhere: bool = False,
        limit: int,
        offset: int = 0,
        sort: str = ENTITY_SORT_SEEN,
        asset_filter: AssetFilter = NO_FILTER,
        parent: str | None = None,
        count_narrowed: bool = False,
    ) -> dict[str, object]:
        """What the site statement binds. One place; see `_tag_params`.

        `parent_id` is bound on every read of the statement, including the by-id one, because a
        parameter a statement names and a caller does not bind is an error at execution rather than
        a filter that quietly does nothing. NULL is "every site", which is what a wall asking
        nothing means.
        """
        return {
            "viewer": viewer.id,
            "is_admin": 1 if viewer.is_admin else 0,
            "list_empty": self._lists_empty_rows(viewer, asset_filter),
            "reveal": self._reveal_existence(viewer),
            "reveal_named": self._reveal_named(viewer),
            # A read naming its row by id keeps the name of a row the walls draw as a locked tile;
            # see `_LOCKED_TILE`. Worked out here, beside the id it is about, so no caller can
            # bind the id and forget the other.
            "by_id": 1 if site_id is not None else 0,
            "site_id": site_id,
            "parent_id": parent,
            "prefix": prefix,
            "like": _like_anywhere(prefix) if anywhere else _like_prefix(prefix),
            "entity_sort": _entity_sort(sort),
            # Which tally a card on this wall prints. See `list_sites`.
            "count_narrowed": 1 if count_narrowed else 0,
            "limit": limit,
            "offset": offset,
        }

    async def position_of_site(
        self,
        viewer: Viewer,
        site_id: str,
        prefix: str = "",
        *,
        anywhere: bool = False,
        sort: str = ENTITY_SORT_SEEN,
        asset_filter: AssetFilter = NO_FILTER,
        narrowing: EntityNarrowing = NO_NARROWING,
        parent: str | None = None,
        count_narrowed: bool = False,
    ) -> int | None:
        """How far into the Sites wall one site sits, counting from zero. None if it is not in it.

        Every argument `list_sites` takes, `parent` included: the sites under one network are a
        different wall from every site, and a position taken in the wrong one of those two lands
        somebody a long way from where they asked to be. See `position_of_photo_set`.
        """
        # An id that is not an id matches nothing, and is never bound as NULL: the statement reads
        # a NULL here as "no filter" and would answer with the position of an arbitrary row.
        if not _is_object_id(site_id):
            return None

        where, bound = asset_filter.predicate()
        narrowed, picked = narrowing.predicate()
        rows = await self._db.fetch_all(
            sites_position(where, narrowed),
            bound
            | picked
            | self._site_params(
                viewer,
                # The wall is never filtered to one site, and this must not be either.
                site_id=None,
                prefix=prefix,
                anywhere=anywhere,
                # Not read by this statement; bound anyway. See `position_of_tag`.
                limit=1,
                sort=sort,
                asset_filter=asset_filter,
                parent=parent,
                count_narrowed=count_narrowed,
            )
            | {"position_of": site_id},
        )
        if not rows:
            return None
        # ROW_NUMBER counts from one and an offset counts from zero. Converted here, once.
        return int(rows[0]["position"]) - 1

    async def visible_site(self, viewer: Viewer, site_id: str) -> SiteSuggestion | None:
        """One site, if this viewer may be shown it. See `visible_collection`."""
        if not _is_object_id(site_id):
            return None
        rows = await self._db.fetch_all(
            SITE_BY_ID,
            self._unfiltered() | self._site_params(viewer, site_id=site_id, limit=1),
        )
        if not rows:
            self._log_denied(viewer, site_id)
            return None
        return _site_from_row(rows[0])

    async def visible_sites(
        self, viewer: Viewer, site_ids: Sequence[str]
    ) -> dict[str, SiteSuggestion]:
        """The Sites among these ids that this viewer may be shown, keyed by id.

        The batched form of `visible_site`. Read off `SITES_BY_ID`, the wall's own statement with
        its counts read off the stored per-Site figures, rather than the whole wall's live counts
        for each Site asked about.
        """
        return {
            one.id: one
            for one in await self._by_ids(
                SITES_BY_ID,
                site_ids,
                lambda chunk: self._site_params(viewer, site_id=None, limit=chunk),
                _site_from_row,
            )
        }
