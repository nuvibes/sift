# SPDX-License-Identifier: AGPL-3.0-or-later
"""The grid's reads, and the record of what left the machine.

Every asset read goes through the access layer; the one exception is the scrub strip, asked of
the content store after the access layer has said yes (`sprite_sheet`). It owns only the save log.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable, Mapping, Sequence
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
from sift.kernel.access.repository.read_files import WordMatches, words_of
from sift.kernel.access.repository.read_one_file import FileRecord
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

#: The fields a person types onto a record, read off the row after the store has cleaned them.
RECORD_FIELDS = (
    "title",
    "download_url",
    "release_date",
    "details",
    "production_date",
    "site_code",
    "music",
)


#: How far to walk from where the draw landed, so a library of placeholders cannot spin.
_RANDOM_TRIES = 8

_INSERT_SAVE = "INSERT INTO save_log (id, user_id, asset_id, saved_at) VALUES (?, ?, ?, ?)"

# The page and its total in one statement, so no write can fall between them.
_RECENT_SAVES = """
SELECT *, COUNT(*) OVER () AS total_count
  FROM save_log
 ORDER BY saved_at DESC, id DESC
 LIMIT ? OFFSET ?
"""

# Only for an empty page: an offset past the end has no row to carry the window count.
_COUNT_SAVES = "SELECT COUNT(*) AS total FROM save_log"

# The most rows the save log will hand back in one go. It is an audit view, read a page at a time.
MAX_SAVE_LOG_PAGE = 200

#: Users whose answers are kept; only this many other Users asking since can push mine out.
VIEWERS_KEPT = 16
#: Per User: facet answers, word lists, and the ids all their lists hold (about 28 bytes each).
FACETS_KEPT = 64
WORD_LISTS_KEPT = 8
WORD_IDS_KEPT = 100_000


class _WordLists:
    """One User's word lists under one mark; a list over the budget is used once, never kept."""

    def __init__(self) -> None:
        self._mark: str | None = None
        self._lists: OrderedDict[Hashable, WordMatches] = OrderedDict()
        self._asking: dict[Hashable, asyncio.Task[WordMatches | None]] = {}

    async def get(
        self,
        key: Hashable | None,
        mark: str | None,
        compute: Callable[[], Awaitable[WordMatches | None]],
    ) -> WordMatches | None:
        if key is None or mark is None:
            return await compute()
        if mark != self._mark:
            self._mark = mark
            self._lists.clear()
        held = self._lists.get(key)
        if held is not None:
            self._lists.move_to_end(key)
            return held
        # The panel's columns ask at the same time, so they wait on one match.
        asking = self._asking.get((key, mark))
        if asking is None:
            asking = self._asking[(key, mark)] = asyncio.ensure_future(compute())
            asking.add_done_callback(lambda _: self._asking.pop((key, mark), None))
        found = await asyncio.shield(asking)
        if found is not None and found.count <= WORD_IDS_KEPT and self._mark == mark:
            self._lists[key] = found
            while (
                len(self._lists) > WORD_LISTS_KEPT
                or sum(one.count for one in self._lists.values()) > WORD_IDS_KEPT
            ):
                self._lists.popitem(last=False)
        return found


class _Kept:
    """One User's kept answers, apart from every other User's (`VIEWERS_KEPT`)."""

    def __init__(self) -> None:
        self.facets: MarkedMemo[list[FacetCount]] = MarkedMemo(kept=FACETS_KEPT)
        self.words = _WordLists()


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
        self._kept: OrderedDict[str, _Kept] = OrderedDict()

    def _now(self) -> int:
        return int(self._clock())

    def _kept_for(self, viewer: Viewer) -> _Kept:
        kept = self._kept.pop(viewer.id, None) or _Kept()
        self._kept[viewer.id] = kept
        while len(self._kept) > VIEWERS_KEPT:
            self._kept.popitem(last=False)
        return kept

    async def _words(self, viewer: Viewer, asset_filter: AssetFilter) -> WordMatches | None:
        """The viewer's own files holding the filter's words, matched once; None for an admin."""
        words = words_of(asset_filter)
        if viewer.is_admin or words is None:
            return None
        stands_on = await self._access.words_stand_on(viewer)

        async def matched() -> WordMatches | None:
            return await self._access.word_matches(viewer, asset_filter)

        key = None if stands_on[2] is None else (words, viewer.cache_stamp, stands_on)
        return await self._kept_for(viewer).words.get(key, current_mark(), matched)

    async def facets(
        self,
        viewer: Viewer,
        facet: str,
        *,
        asset_filter: AssetFilter,
        hidden_only: bool,
        limit: int,
    ) -> list[FacetCount]:
        """How many files this query reaches carry each value of one dimension, cached."""
        where, bound = asset_filter.predicate()
        # What this user may see and the word index move with or without an announcement.
        key = (
            viewer.id,
            viewer.cache_stamp,
            viewer.show_hidden,
            viewer.concealment,
            await self._access.words_stand_on(viewer),
            facet,
            hidden_only,
            limit,
            where,
            tuple(sorted((name, repr(value)) for name, value in bound.items())),
        )

        async def count() -> list[FacetCount]:
            return await self._access.facet_counts(
                viewer,
                facet,
                asset_filter=asset_filter,
                hidden_only=hidden_only,
                limit=limit,
                words=await self._words(viewer, asset_filter),
            )

        return await self._kept_for(viewer).facets.get(key, current_mark(), count)

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
        """A page of what this viewer may see; every narrowing goes down to the one statement."""
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
            words=await self._words(viewer, asset_filter),
        )

    async def something_else(
        self,
        viewer: Viewer,
        *,
        avoiding: str | None = None,
        asset_filter: AssetFilter = NO_FILTER,
    ) -> AssetView | None:
        """One random file of everything this viewer may open (or in `asset_filter`), or None.

        The count and the draw take one predicate in one statement, then one draw at an offset and
        a walk forward from it, so a library of two never lands back on the file already open.
        """
        words = await self._words(viewer, asset_filter)
        counted = await self._access.visible_assets(
            viewer, limit=1, offset=0, asset_filter=asset_filter, words=words
        )
        if counted.total == 0:
            return None

        # `secrets`: one fewer weak generator.
        landed = secrets.randbelow(counted.total)

        for step in range(min(_RANDOM_TRIES, counted.total)):
            offset = (landed + step) % counted.total
            # The same filter the count was taken under. See the note above.
            page = await self._access.visible_assets(
                viewer, limit=1, offset=offset, asset_filter=asset_filter, words=words
            )
            # Nothing there, or a placeholder: move along.
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
        """This viewer's hearts and stars for a page of assets, keyed by asset, in one query."""
        if not asset_ids:
            return {}
        return await self._state.states_of(list(asset_ids), viewer.id)

    async def sprite_sheet(self, viewer: Viewer, asset_id: str) -> Derivative | None:
        """The scrub strip of an asset this viewer may open, or None.

        Its settings are its layout, so no scoped lookup can name it: `open_asset` decides first,
        and only then is the content store (handed in at boot) asked. Newest wins.
        """
        if await self._access.open_asset(viewer, asset_id) is None:
            return None
        return await self._newest_sheet(asset_id)

    async def sprite_of(self, viewer: Viewer, record: FileRecord) -> Derivative | None:
        """`sprite_sheet` for a file whose record this viewer was just given."""
        if record.view.concealed and not viewer.show_hidden:
            return None
        return await self._newest_sheet(record.view.asset.id)

    async def _newest_sheet(self, asset_id: str) -> Derivative | None:
        sheets = [
            derivative
            for derivative in await self._content.derivatives(asset_id)
            if derivative.kind is DerivativeKind.SPRITE
        ]
        if not sheets:
            return None
        return max(sheets, key=lambda derivative: (derivative.created_at, derivative.id))

    async def sprite_served(self, viewer: Viewer, asset_id: str) -> ServedDerivative | None:
        """The strip to send, with what its caching rule needs: vaulted or not, and its token."""
        # The ordinary scoped read first: the strict one refuses everything this refuses.
        view = await self._access.get_asset(viewer, asset_id)
        if view is None:
            return None
        sheet = await self.sprite_sheet(viewer, asset_id)
        if sheet is None:
            return None
        path = await self.sprite_bytes(sheet)
        if path is None:
            return None
        # A strip with no recorded digest keeps its address when rebuilt, so it is not cached.
        keepable = sheet.content_hash is not None
        return ServedDerivative(
            path=path,
            version=view.art_version if keepable else None,
            concealed=view.concealed,
        )

    async def sprite_bytes(self, sheet: Derivative) -> Path | None:
        """Where a strip's bytes are, or None; a path outside the cache is logged as well."""
        try:
            return await self._content.derivative_at(sheet.rel_cache_path)
        except ValueError:
            log.warning(
                "browse.derivative_unusable", asset_id=sheet.asset_id, kind=sheet.kind.value
            )
            return None

    async def record_save(self, viewer: Viewer, asset_id: str) -> None:
        """Note a kept copy, after the save was allowed: the save log's row and a `saved` event."""
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

        `UNCHANGED` is "not mentioned", None is "clear it": the distinction is the signature.
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

        await self._write_fields(
            viewer,
            asset_id,
            title=title,
            download_url=download_url,
            release_date=release_date,
            details=details,
            production_date=production_date,
            site_code=site_code,
            music=music,
            links=links,
        )
        after = await self._content.get(asset_id)
        if before is not None and after is not None:
            for field in RECORD_FIELDS:
                note(field, getattr(before, field), getattr(after, field))
        if not isinstance(links, Unchanged):
            note("links", was_links, await self._content.links_of(asset_id))
        if changed:
            await self._record_edit(viewer, asset_id, after or before, changed)
        return True

    async def _write_fields(
        self,
        viewer: Viewer,
        asset_id: str,
        *,
        title: str | Unchanged | None,
        download_url: str | Unchanged | None,
        release_date: str | Unchanged | None,
        details: str | Unchanged | None,
        production_date: str | Unchanged | None,
        site_code: str | Unchanged | None,
        music: str | Unchanged | None,
        links: Sequence[str] | Unchanged | None,
    ) -> None:
        """Write each field the caller named, through the content store."""
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
            # A form that cleared every row sends nothing, so None is an empty list.
            await self._content.set_links(asset_id, links or [])

    async def _record_edit(
        self,
        viewer: Viewer,
        asset_id: str,
        asset: Asset | None,
        changed: Sequence[Mapping[str, object]],
    ) -> None:
        """One event for one save, naming every field it moved, announced to every screen.

        Its own transaction: the store's eight writes each open their own, and the guard is not
        re-entrant, so a crash after the last field keeps the edit and loses the event.
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
        """A file's links, for the detail view; the caller has resolved the asset, scoped."""
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
