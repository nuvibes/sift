# SPDX-License-Identifier: AGPL-3.0-or-later
"""The grid's reads, and the record of what left the machine.

Every asset read goes through the access layer. Nothing here writes SQL against assets, locations
or derivatives: that is the rule that keeps permission-scoping honest, and a read-heavy feature
is exactly where it is most tempting to break it for one convenient query.

There is one read that goes to the content store instead, and it is named here rather than left to
be discovered: the scrub strip, which cannot be found through the scoped lookup because that lookup
matches on the settings a derivative was built with and a strip's settings are its own layout. The
permission question is still asked first, separately, through the access layer, and the store is
handed in at boot rather than reached for. `sprite_sheet` sets all of that out; a second exception
appearing without the same explanation is the thing to push back on.

The only tables this owns are its own: the save log.
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from sift.kernel.access import (
    DEFAULT_SORT,
    NO_FILTER,
    AssetFilter,
    AssetPage,
    AssetView,
    FacetCount,
    Repository,
    ServedDerivative,
    Viewer,
)
from sift.kernel.audience import Audience
from sift.kernel.changes import About, announce, current_mark, telling, who_may_see_a_file
from sift.kernel.content import (
    Asset,
    AssetUserState,
    ContentStore,
    Derivative,
    DerivativeKind,
    UserStateStore,
)
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, record_event
from sift.kernel.log import get_logger
from sift.kernel.memo import MarkedMemo
from sift.kernel.partial_write import UNCHANGED, Unchanged
from sift.kernel.vocabulary import Subject
from sift.kernel.wiring import Part

log = get_logger(__name__)

#: The fields a person types onto a file's record, as the `Asset` row calls them.
#:
#: Read off the row before and after the save rather than taken from the arguments, because the
#: store cleans what it is given (a pasted address loses its tracking parameters, a title of
#: spaces becomes nothing) and an event that reported what was TYPED would disagree with the
#: record for exactly the saves where the cleaning did something.
RECORD_FIELDS = (
    "title",
    "download_url",
    "release_date",
    "details",
    "production_date",
    "site_code",
    "music",
)


#: How far to walk from where the draw landed before giving up on finding something to show.
#:
#: Only ever more than one step where the row it landed on was a concealed placeholder or the file
#: already open, both of which are a small share of any library. Bounded so a library that is
#: entirely one of those cannot spin.
_RANDOM_TRIES = 8

_INSERT_SAVE = "INSERT INTO save_log (id, user_id, asset_id, saved_at) VALUES (?, ?, ?, ?)"

# The page and its total in one statement, for the same reason the asset grid does it: two
# statements can be separated by a write, and then the count describes a set the page is not a page
# of. A window function costs nothing here and removes the question.
_RECENT_SAVES = """
SELECT *, COUNT(*) OVER () AS total_count
  FROM save_log
 ORDER BY saved_at DESC, id DESC
 LIMIT ? OFFSET ?
"""

# Only reached when the page came back empty: an offset past the end returns no rows, and the
# window count rides on a row, so without this an empty last page would report a total of zero and
# contradict the pages before it.
_COUNT_SAVES = "SELECT COUNT(*) AS total FROM save_log"

# The most rows the save log will hand back at once. It is an audit view, read a page at a time.
MAX_SAVE_LOG_PAGE = 200


class BrowseService:
    """Reads for the grid, and the save log it owns."""

    def __init__(
        self,
        database: Database,
        access: Repository,
        state: UserStateStore,
        content: ContentStore,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = database
        self._access = access
        self._state = state
        self._content = content
        self._clock = clock
        # The counts under the filter panel, kept until the library moves. See `facets`.
        self._facets: MarkedMemo[list[FacetCount]] = MarkedMemo()

    def _now(self) -> int:
        return int(self._clock())

    async def facets(
        self,
        viewer: Viewer,
        facet: str,
        *,
        asset_filter: AssetFilter,
        hidden_only: bool,
        limit: int,
    ) -> list[FacetCount]:
        """How many of the files this query reaches carry each value of one dimension.

        Kept under the change bus's mark. A facet is a count over everything the query reaches,
        which for an un-narrowed wall is the whole visible library, and the panel asks for five
        dimensions on every change of the query, so the same numbers would be counted again
        and again for a library that had not moved. The mark moves on every announcement, so a
        kept answer is exactly as current as the last change anybody made.

        The key carries everything the answer depends on: who is asking and how much they may see,
        the dimension, the filter as its own text and bindings, and the hidden-only switch.
        """
        where, bound = asset_filter.predicate()
        # The user's stored counts are in the key beside the mark. The mark moves on every
        # announced change; the counts move on every change to what this user may see, whether
        # anything announced it or not, so a row written straight into the database still misses.
        key = (
            viewer.id,
            viewer.show_hidden,
            viewer.concealment,
            await self._access.visible_counts(viewer),
            facet,
            hidden_only,
            limit,
            where,
            tuple(sorted((name, repr(value)) for name, value in bound.items())),
        )

        async def count() -> list[FacetCount]:
            return await self._access.facet_counts(
                viewer, facet, asset_filter=asset_filter, hidden_only=hidden_only, limit=limit
            )

        return await self._facets.get(key, current_mark(), count)

    async def page(
        self,
        viewer: Viewer,
        *,
        limit: int,
        offset: int,
        hidden_only: bool = False,
        photo_set_id: str | None = None,
        pinned_first: bool = False,
        asset_filter: AssetFilter = NO_FILTER,
        sort: str = DEFAULT_SORT,
        seed: int | None = None,
        after: str | None = None,
    ) -> AssetPage:
        """A page of what this viewer may see. The scoping is the repository's, not ours.

        The filter is handed straight down rather than applied to what comes back. Narrowing a
        page here would leave the total describing a wider set than the rows do, and a paginator
        that disagrees with its own count is the first sign that filtering has moved out of the
        one statement that also decides visibility.

        `pinned_first` is whether the wall doing the asking honours the pin, handed down untouched
        for the same reason as everything else here: it belongs to the one statement that decides
        both what is in the page and what order it is in.

        `hidden_only` is the Hidden screen asking for the complement of the usual page. Handed down
        for the same reason and with more at stake: it is a question about concealment, and the one
        statement that decides concealment is the only place that may answer it.

        `seed` is WHICH shuffle, and it belongs to the same statement for the same reason the order
        does: a page and the count beside it have to describe one arrangement of one set.
        """
        return await self._access.visible_assets(
            viewer,
            limit=limit,
            offset=offset,
            hidden_only=hidden_only,
            photo_set_id=photo_set_id,
            pinned_first=pinned_first,
            asset_filter=asset_filter,
            sort=sort,
            seed=seed,
            after=after,
        )

    async def something_else(
        self,
        viewer: Viewer,
        *,
        avoiding: str | None = None,
        asset_filter: AssetFilter = NO_FILTER,
    ) -> AssetView | None:
        """One file at random out of everything this viewer may open, or None if there is nothing.

        The whole library rather than whatever page is on screen: that is the point of the control
        it answers, and a version that quietly picked from the visible rows would look identical
        until somebody noticed it never left the folder they were in.

        `asset_filter` is the caller's own narrowing, and it is the caller's to state rather than
        something read off whatever is on screen. The player names none, so it still reaches the
        whole library; a Theater cell set to one search names that search, and the draw comes from
        inside it. Stated rather than inferred is what keeps both honest: there is no ambient
        "current view" for this to read, so reaching past the screen is expressed by naming
        nothing rather than by the route being incapable of narrowing.

        **The count and the draw take the same predicate, in the same statement.** The offset is
        chosen inside the total, so a total counting a wider set than the draw reads from would
        land the draw past the end of it (silently, as a run of Nones) and a total counting a
        narrower one would make whole stretches of the set unreachable. Filtering afterwards has
        the same fault in a worse place: it would publish, in the gap between the count and what
        comes back, the existence of files this user was never shown.

        Scoped by the same statement the grid pages with, so it can only ever choose from what this
        user may see: an asset nobody shared with them is not merely skipped here, it is not in
        the set to be counted. Concealed rows are skipped as well: with the vault shut they are
        already absent, and in the mode that leaves placeholders on the grid a placeholder is not
        something anybody can play.

        Two reads rather than one clever statement. Asking the database to order a library at random
        is a sort of the whole table for one row; asking it for the count and then for one row at a
        known offset is two index reads whatever the library's size.

        **One draw, then a walk from where it landed**, rather than drawing again each time the
        first answer will not do. Drawing again is the obvious shape and it is a lottery: in a
        library of two, every draw has an even chance of landing back on the file already open, so
        "somewhere else" occasionally means "here again". Stepping forward one row is guaranteed to
        be a different one, so the small library behaves exactly as somebody would expect and the
        large one is unaffected. The cost is a faint bias towards the row after a skipped one, which
        is not a property anybody is relying on: this is a way of finding something to watch.
        """
        counted = await self._access.visible_assets(
            viewer, limit=1, offset=0, asset_filter=asset_filter
        )
        if counted.total == 0:
            return None

        # `secrets` rather than `random`: it costs nothing here, and it is one fewer weak generator
        # in a codebase where the difference matters elsewhere.
        landed = secrets.randbelow(counted.total)

        for step in range(min(_RANDOM_TRIES, counted.total)):
            offset = (landed + step) % counted.total
            # The same filter the count was taken under. See the note above.
            page = await self._access.visible_assets(
                viewer, limit=1, offset=offset, asset_filter=asset_filter
            )
            # Nothing there, or a placeholder: the same answer, which is to move along. An offset
            # inside the count is a row unless something was deleted between the two reads, and a
            # placeholder is not something anybody can play.
            view = page.items[0] if page.items else None
            if view is None or (view.concealed and not viewer.show_hidden):
                continue
            if avoiding is not None and view.asset.id == avoiding and counted.total > 1:
                continue
            return view
        return None

    async def state_for(
        self, viewer: Viewer, asset_ids: Sequence[str]
    ) -> dict[str, AssetUserState]:
        """This viewer's hearts and stars for a page of assets, keyed by asset.

        One query for the page rather than one per tile: a grid of fifty tiles asking fifty times
        is the shape that makes a fast page feel slow, and it is invisible until the library is
        big enough to matter.
        """
        if not asset_ids:
            return {}
        return await self._state.states_of(list(asset_ids), viewer.id)

    async def sprite_sheet(self, viewer: Viewer, asset_id: str) -> Derivative | None:
        """The scrub strip built for an asset this viewer may open, or None.

        This is the one read in the slice that goes to the content store rather than through the
        access layer, and it is worth saying exactly why and exactly what keeps it honest.

        The scoped lookup the thumbnail and the hover clip use matches on the settings a derivative
        was built with. Those two are always built with none, so the empty settings are the key and
        naming them costs nothing. A scrub strip's settings ARE its layout (how many frames across
        and down, and how wide one is), decided per file from its length, so nothing that has not
        already read the row can name them. There is no settings-blind scoped lookup to ask instead.

        So the permission question is asked first and separately, through the access layer, and only
        an asset that has passed it is looked up here. `open_asset` is the strict check: it refuses a
        concealed asset even in the mode that leaves a placeholder on the grid, which is the same
        check the scoped lookup makes. Nothing below it decides who may see anything: by then the
        answer is already yes.

        The store is handed in at boot rather than reached for, the same way the two file-mutating
        features get theirs. That is not a formality: a build rule refuses any part of the app
        outside the kernel that helps itself to the content store, because the store reads any asset
        with no permission check at all.

        Newest wins where a file has been rebuilt at a different density, matching the rule the
        scoped lookup applies to the same situation.
        """
        if await self._access.open_asset(viewer, asset_id) is None:
            return None
        sheets = [
            derivative
            for derivative in await self._content.derivatives(asset_id)
            if derivative.kind is DerivativeKind.SPRITE
        ]
        if not sheets:
            return None
        return max(sheets, key=lambda derivative: (derivative.created_at, derivative.id))

    async def sprite_served(self, viewer: Viewer, asset_id: str) -> ServedDerivative | None:
        """The strip to send, with what deciding its caching rule needs.

        The two reads are not one because the strip cannot be found by the scoped derivative lookup.
        See `sprite_sheet` for why its settings make it unaddressable that way. So the strict
        check happens there, and this asks the ordinary scoped read for the two facts a response
        needs: whether the asset is in this user's vault, and the token its pictures are
        addressed by.

        Affordable here in a way it would not be on a grid tile: a strip is fetched once, when
        somebody opens a video, rather than a hundred times while scrolling.
        """
        # The ordinary scoped read first, and the strict one after it. The other way round the
        # second question can never answer no (`sprite_sheet` refuses everything this refuses and
        # more), so the guard on it would be a line no test could reach and no reader could trust.
        view = await self._access.get_asset(viewer, asset_id)
        if view is None:
            return None
        sheet = await self.sprite_sheet(viewer, asset_id)
        if sheet is None:
            return None
        path = await self.sprite_bytes(sheet)
        if path is None:
            return None
        # A strip with no recorded digest is not named by the asset's token, so rebuilding it would
        # not change its address and a browser told to keep it would show the old one for a week.
        # Careful until the catch-up pass has read it: the same rule the still and the clip
        # follow, decided per picture rather than per asset for the same reason.
        keepable = sheet.content_hash is not None
        return ServedDerivative(
            path=path,
            version=view.art_version if keepable else None,
            concealed=view.concealed,
        )

    async def sprite_bytes(self, sheet: Derivative) -> Path | None:
        """Where a strip's bytes are, or None if they are not readable.

        Two different nothings arrive here as one. A row naming a path that is not inside the cache
        at all (a restored backup, or one written before the check that now refuses it), raises,
        and is worth a line in the log because somebody should look at it. A path inside the cache
        with no file at it is the ordinary case of something having been cleared out, which is what
        a cache is for. To whoever asked for a picture they are the same answer.
        """
        try:
            return await self._content.derivative_at(sheet.rel_cache_path)
        except ValueError:
            log.warning(
                "browse.derivative_unusable", asset_id=sheet.asset_id, kind=sheet.kind.value
            )
            return None

    async def record_save(self, viewer: Viewer, asset_id: str) -> None:
        """Note that somebody kept a copy. Called only after the save has been allowed.

        Two records of one act, in ONE transaction: the save log's row, which an admin's
        maintenance pane lists, and a `saved` event with the file as its subject, which is what
        puts the save on the file's own history and in the Settings feed. The name is the file's
        as it is now, which the ledger's door reads in the file page's order (`NAME_NOW`) as it
        writes: the snapshot every event here takes.

        Who may read the event is the history's rule, not this writer's: a save is the user's
        own act, drawn for that user and for an admin only (`history._drawn_elsewhere`).
        """
        audience = Audience.of_user(viewer.id)
        async with telling(self._db, audience, About.MINE) as connection:
            await connection.execute(_INSERT_SAVE, (new_id(), viewer.id, asset_id, self._now()))
            await record_event(
                connection,
                actor=Actor.user(viewer.id),
                verb="saved",
                # No name handed: the door names the file in the file page's order (`NAME_NOW`).
                subject=Subject(kind="asset", id=asset_id),
            )
            # The History pane re-reads on the library's bell; the saver's and every admin's.
            announce(audience.widened_to_admins(), About.LIBRARY)

    async def set_record(
        self,
        viewer: Viewer,
        asset_id: str,
        *,
        title: str | Unchanged | None = UNCHANGED,
        download_url: str | Unchanged | None = UNCHANGED,
        release_date: str | Unchanged | None = UNCHANGED,
        details: str | Unchanged | None = UNCHANGED,
        production_date: str | Unchanged | None = UNCHANGED,
        site_code: str | Unchanged | None = UNCHANGED,
        music: str | Unchanged | None = UNCHANGED,
        links: Sequence[str] | Unchanged | None = UNCHANGED,
    ) -> bool:
        """Write the editable half of a file's record. False when this viewer has no such file.

        IMPORTANT: `UNCHANGED` is not the same as None, and the distinction is the whole signature. None
        means "clear this field"; `UNCHANGED` means "the caller did not mention it". Two optional
        arguments both defaulting to None would make a caller who names one silently blank the
        other, which is a full-row writer pretending to be a partial one.

        The scoped read comes first and it is doing real work rather than being polite: it is what
        makes a file this user may not be shown answer "no such file" instead of being written
        to. The same two-step the entity covers use.

        The writes go to the content store rather than to SQL here, which is the rule this slice is
        built on: nothing in `browse` writes against `assets` directly.
        """
        if await self._access.get_asset(viewer, asset_id) is None:
            return False
        before = await self._content.get(asset_id)
        was_links = [] if isinstance(links, Unchanged) else await self._content.links_of(asset_id)
        changed: list[dict[str, object]] = []

        def note(field: str, was: object, now: object) -> None:
            """Remember a field the save actually moved. Unchanged fields are not an edit."""
            if was != now:
                changed.append({"field": field, "before": was, "after": now})

        if not isinstance(title, Unchanged):
            await self._content.set_title(asset_id, title)
        if not isinstance(download_url, Unchanged):
            await self._content.set_download_url(asset_id, download_url)
        if not isinstance(release_date, Unchanged):
            await self._content.set_release_date(asset_id, release_date)
        if not isinstance(details, Unchanged):
            await self._content.set_details(asset_id, details)
        if not isinstance(production_date, Unchanged):
            await self._content.set_production_date(asset_id, production_date)
        if not isinstance(site_code, Unchanged):
            await self._content.set_site_code(asset_id, site_code)
        if not isinstance(music, Unchanged):
            # A song somebody typed is made by them where no song is called that yet.
            await self._content.set_music(asset_id, music, by_user=viewer.id)
        if not isinstance(links, Unchanged):
            # A list is replaced as a whole and None is an empty one, because a form that cleared
            # every row sends nothing rather than a list of nothing.
            await self._content.set_links(asset_id, links or [])
        after = await self._content.get(asset_id)
        if before is not None and after is not None:
            for field in RECORD_FIELDS:
                note(field, getattr(before, field), getattr(after, field))
        if not isinstance(links, Unchanged):
            note("links", was_links, await self._content.links_of(asset_id))
        if changed:
            await self._record_edit(viewer, asset_id, after or before, changed)
        return True

    async def _record_edit(
        self,
        viewer: Viewer,
        asset_id: str,
        asset: Asset | None,
        changed: Sequence[Mapping[str, object]],
    ) -> None:
        """One event for one save, naming every field it moved.

        ONE event and not one per field, because one press of Save is one act: seven events for a
        form somebody filled in would bury the six other things that happened that minute, and the
        record exists to be read.

        In its own transaction, and that is a compromise worth naming rather than hiding. The
        record's eight fields are written through eight statements in the content store, each
        opening its own write, so there is no single transaction here to put the event inside,
        and the writes guard is not re-entrant, so wrapping them would deadlock rather than group.
        A crash between the last field and this line loses the event and keeps the edit. The
        durable shape is one transaction for a save, which means a content-store writer that takes
        a connection; until there is one, this is the honest placement.

        It ANNOUNCES, to two screens with one bell. The History pane re-reads on the library's
        bell and on nothing else, so an event written in silence would sit unseen on every other
        tab; and the edit itself (the title a wall draws, the details a record page shows), told
        to NOBODY, would leave every other tab drawing the old name until reloaded. The
        audience is `who_may_see_a_file`: every admin (the pane is theirs, and an admin sees every
        file) and every user who has been given anything, the same wide question a file
        arriving asks, since "who might be drawing this file" is exactly that question. The exact
        answer is the permission resolver's, bound to one viewer at a time; each client re-reads
        its own page through the ordinary door and answers it for itself.
        """
        async with self._db.write() as connection:
            await record_event(
                connection,
                actor=Actor.user(viewer.id),
                verb="edited",
                subject=Subject(kind="asset", id=asset_id),
                payload=json.dumps({"fields": list(changed)}),
            )
            announce(await who_may_see_a_file(connection), About.LIBRARY)

    async def links_of(self, asset_id: str) -> list[str]:
        """A file's links, for the detail view.

        Unscoped on purpose and safe because of where it is called: the caller has already resolved
        the asset through the scoped read, so a file this user may not see never reaches here.
        A second scoping rule written against a links table would be a second place to get it wrong.
        """
        return await self._content.links_of(asset_id)

    async def saves(self, *, limit: int, offset: int) -> tuple[list[dict[str, object]], int]:
        """The save log, newest first, with how many rows there are in all."""
        limit = min(limit, MAX_SAVE_LOG_PAGE)
        rows = await self._db.fetch_all(_RECENT_SAVES, (limit, offset))
        if rows:
            total = int(rows[0]["total_count"])
        else:
            counted = await self._db.fetch_one(_COUNT_SAVES)
            total = int(counted["total"]) if counted is not None else 0
        return [
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "asset_id": row["asset_id"],
                "saved_at": row["saved_at"],
            }
            for row in rows
        ], total


#: The grid's reads.
SERVICE: Part[BrowseService] = Part("browse")
