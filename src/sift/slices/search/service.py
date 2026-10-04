# SPDX-License-Identifier: AGPL-3.0-or-later
"""Running a search, offering suggestions, and remembering what somebody asked for."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from starlette.datastructures import QueryParams

from sift.kernel.access import (
    ENTITY_FACETS,
    AssetFilter,
    Repository,
    Viewer,
    title_filter,
)
from sift.kernel.audience import Audience
from sift.kernel.changes import About, telling
from sift.kernel.client import UNKNOWN, Client
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.db import Connection, Database
from sift.kernel.ids import is_id, new_id
from sift.kernel.jobs import JobQueue
from sift.kernel.site_icons import icon_token
from sift.kernel.use_history import register_clearing
from sift.kernel.wiring import Part
from sift.slices.search.filters import OFFERED_VALUES, Field, FilterCompiler
from sift.slices.search.jobs import catch_up_if_behind
from sift.slices.search.stored import Noted, as_kept, as_shown


class NameTaken(Exception):
    """This user already has a saved search under that name."""


class TooMany(Exception):
    """This user is holding as many saved searches as they may."""


#: The most searches one user's dropdown remembers. A memory that grew without bound would stop
#: being a convenience and start being the query log this deliberately is not.
MAX_HISTORY = 50

#: The most saved searches one user may keep.
MAX_SAVED_SEARCHES = 200

#: The most suggestions any one dropdown offers.
MAX_SUGGESTIONS = 20

#: The most a RESULTS band offers, before its "see all".
MAX_BAND = 60

# Re-running a search moves the row it already has rather than adding a second one, so the dropdown
# shows a query once however many times it was typed.
_REMEMBER = """
INSERT INTO search_history (id, user_id, kind, subject, label, created_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(user_id, kind, subject) DO UPDATE SET id = excluded.id,
  label = excluded.label,
  created_at = excluded.created_at
"""

# The same search, kept as a fact rather than as a row in a list. See `_CREATE_SEARCH_EVENTS`.
_RECORD_EVENT = """
INSERT INTO search_events
  (id, user_id, kind, subject, results, opened_id, at, device_id, client_kind)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# A file opened from the wall a typed search narrowed, linked to the latest record of that search
# by this User, where there is one. See `_CREATE_SEARCH_OPENS`.
_LATEST_EVENT = """
SELECT id FROM search_events
 WHERE user_id = ? AND kind = ? AND subject = ?
 ORDER BY id DESC
 LIMIT 1
"""

_RECORD_OPEN = """
INSERT INTO search_opens (id, user_id, event_id, subject, asset_id, at, device_id, client_kind)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

# Ordered by the ID and not by the clock, and the two are not interchangeable. `created_at` is a
# wall-clock reading, and a machine corrects its clock while Sift is running, so a search made
# after a correction carries a SMALLER number than the one before it and sorts to the bottom of its
# own history. The ID is minted by `new_id` under a floor that never goes down, which exists for
# exactly this, so it is the only field here that really is in the order things happened.
_RECENT = """
SELECT kind, subject, label FROM search_history
 WHERE user_id = :viewer AND label LIKE :like ESCAPE '\\'
 ORDER BY id DESC
 LIMIT :limit
"""

_FORGET_ALL = "DELETE FROM search_history WHERE user_id = ?"

# By the SUBJECT, which for a search that was typed is the query itself, so what this route
# forgets is named the way the box names it.
_FORGET_ONE = "DELETE FROM search_history WHERE user_id = ? AND subject = ?"

# FORGET TAKES THE RECORD TOO. A search forgotten from the box and still counted in Insights would
# be a Forget that forgot only the half somebody can see. The opens go with their records (the key
# cascades), and an open written where there was no record goes by its words.
_FORGET_EVENTS_ALL = (
    "DELETE FROM search_opens WHERE user_id = ?",
    "DELETE FROM search_events WHERE user_id = ?",
)
_FORGET_EVENTS_ONE = (
    "DELETE FROM search_opens WHERE user_id = ? AND subject = ?",
    "DELETE FROM search_events WHERE user_id = ? AND subject = ?",
)

# Saving under a name already used updates that name's query rather than adding a second row: the
# id and timestamp move too, so an edited search sorts as freshly saved.
#: How many this user already keeps, and whether the name being saved is one of them. Both in one
#: read, because the cap is about ADDING a row: saving over a name already held replaces one.
#: The cap counts the user's whole store and `mine` counts one WALL's name, because those are two
#: different questions: the cap is about how much one user may store, and the replacement is
#: about whether this save adds a row or overwrites one, which it does only for the same wall.
_SAVED_COUNT = """
SELECT COUNT(*) AS held,
       COALESCE(SUM(CASE WHEN kind = ? AND name = ? THEN 1 ELSE 0 END), 0) AS mine
  FROM saved_searches WHERE user_id = ?
"""

_SAVE_SEARCH = """
INSERT INTO saved_searches (id, user_id, kind, name, query, created_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(user_id, kind, name) DO UPDATE SET
  id = excluded.id,
  query = excluded.query,
  created_at = excluded.created_at
"""

_LIST_SAVED = """
SELECT id, kind, name, query FROM saved_searches
 WHERE user_id = :viewer
 ORDER BY id DESC
"""

_DELETE_SAVED = "DELETE FROM saved_searches WHERE user_id = ? AND id = ?"

# Change a saved search's NAME and leave its query alone, which is the half saving cannot do.
# Saving under an existing name already replaces that name's query, so "update the query" is a
# save, and "keep the query, call it something else" is this.
_RENAME_SAVED = """
UPDATE saved_searches SET name = ?
 WHERE user_id = ? AND id = ?
RETURNING id
"""

# Whether this user already keeps a DIFFERENT search under the wanted name.
_NAME_IS_TAKEN = """
SELECT 1 FROM saved_searches
 WHERE user_id = ? AND name = ? AND id <> ?
   AND kind = (SELECT kind FROM saved_searches WHERE user_id = ? AND id = ?)
 LIMIT 1
"""

# Everything past the cap, for this user only. Written as "keep the newest N" rather than
# "delete the oldest one" so that a history which somehow grew past the cap (a restored database,
# a changed constant) comes back down to it in one pass instead of one row per search.
_TRIM = """
DELETE FROM search_history
 WHERE user_id = ?
   AND id NOT IN (
     SELECT id FROM search_history
      WHERE user_id = ?
      ORDER BY id DESC
      LIMIT ?
   )
"""


#: What a row of the memory is called when it is a search somebody typed, rather than a thing they
#: picked. One name, used by the service, the route's vocabulary check and the client.
QUERY_KIND = "query"

#: The kinds of row that are not one of the query language's fields.
_KINDS_THAT_ARE_NOT_FIELDS = frozenset({QUERY_KIND, "file"})

#: The kinds of row the box remembers by the thing's ID, whose words are read live (see
#: `SearchService._named_now`). `platforms` is the older spelling of `sites`, kept for rows
#: remembered under it.
_NAMED_BY_ID: dict[str, Field] = {
    "people": Field.PEOPLE,
    "sites": Field.SITES,
    "platforms": Field.SITES,
    "collections": Field.COLLECTIONS,
    "photo_sets": Field.PHOTO_SETS,
    "songs": Field.SONGS,
}

#: The kinds the box is handed by NAME, because they are re-run as a filter written in names. Kept
#: by the id of what the name named, and given back under the name it has today.
_NAMED_BY_VALUE: dict[str, Field] = {"tags": Field.TAGS, "in": Field.IN}


#: What a kept filter's wall is called when it is the library itself.
ASSET_WALL = "asset"


def is_a_wall(kind: str) -> bool:
    """Whether a kept filter may say it is about this wall."""
    return kind == ASSET_WALL or kind in ENTITY_FACETS


def is_rememberable(kind: str) -> bool:
    """Whether a row of this kind may be written into somebody's memory."""
    return kind in _KINDS_THAT_ARE_NOT_FIELDS or kind in set(Field)


@dataclass(frozen=True, slots=True)
class Remembered:
    """One row of the box's memory: what kind of thing it is, which one, and what it says."""

    kind: str
    subject: str
    label: str


@dataclass(frozen=True, slots=True)
class SavedSearch:
    """One kept search: its id, the wall it is about, the name given to it, and the query."""

    id: str
    name: str
    query: str
    #: WHICH WALL this filter is a question about: `asset` for the library, otherwise the noun of
    #: the wall it was kept on. A filter is spelled in the vocabulary of its wall, so this is what
    #: lets the panel offer somebody their People filters on People and nothing else there.
    kind: str = "asset"
    #: What the chips need that the address cannot say: a name kept beside an id, or a thing gone.
    #: Empty on the stored form; filled only when the list is read to be SHOWN (see `stored`).
    noted: tuple[Noted, ...] = ()


@dataclass(frozen=True, slots=True)
class Suggestion:
    """One thing a dropdown can offer, and how much of it this viewer can see."""

    value: str
    detail: str | None = None
    count: int | None = None
    #: What KIND of thing this row is, when picking it opens something rather than completing a
    #: token. `file` is the only one so far.
    opens: str | None = None
    #: Which field this row completes to. Set when the dropdown offers rows from more than one at
    #: once: a bare word matches People and Tags and Sites together, and each row has to say
    #: which so it completes to the right token. Left None in the single-field form, where every row
    #: shares the one token the caret is in.
    field: Field | None = None
    #: WHICH one of them this is, so picking the row can go straight to it.
    entity_id: str | None = None
    #: What names this thing's COVER, for a kind that has one: the chosen file and its moment, or an
    #: uploaded picture, and (for a Site nobody has chosen one for) the shipped logo's token.
    cover: CoverNamed | None = None


@dataclass(frozen=True, slots=True)
class CoverNamed:
    """Which picture an entity's cover address answers with, as the row it came from says."""

    asset_id: str | None = None
    upload_id: str | None = None
    at_ms: int | None = None
    #: The window of the picture it is drawn as, which the address names too
    #: (`kernel/covers.py names_its_cover`).
    frame: CoverFrame | None = None
    #: The shipped logo's token (`site_icons.icon_token`); only ever set on a Site.
    icon: str | None = None


class _Covered(Protocol):
    """Any scoped entity row that carries a cover: a person, a tag, a Site, a set, a shelf."""

    @property
    def cover_asset_id(self) -> str | None: ...
    @property
    def cover_upload_id(self) -> str | None: ...
    @property
    def cover_at_ms(self) -> int | None: ...
    @property
    def cover_frame(self) -> CoverFrame | None: ...


def _cover_of(row: _Covered, *, icon: str | None = None) -> CoverNamed:
    """The cover a scoped row names, read off it and never looked up again."""
    return CoverNamed(
        asset_id=row.cover_asset_id,
        upload_id=row.cover_upload_id,
        at_ms=row.cover_at_ms,
        frame=row.cover_frame,
        icon=icon,
    )


class SearchService:
    """Search, suggestions and history, for whoever is asking."""

    def __init__(
        self,
        database: Database,
        access: Repository,
        compiler: FilterCompiler,
        *,
        queue: JobQueue | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = database
        # Held so a submitted search can notice the index is behind and ask for a catch-up. Optional
        # because nothing else here needs one, and a test building this by hand should not have to
        # invent a queue in order to record a search.
        self._queue = queue
        self._access = access
        # The one engine. Held rather than constructed per call, and shared with the grid through
        # `app.state.filter_engine`, so the two cannot come to hold different opinions about what a
        # query means.
        self._compiler = compiler
        self._clock = clock

    def _now(self) -> int:
        return int(self._clock())

    # --- history -----------------------------------------------------------------------------

    async def remember_pick(
        self,
        viewer: Viewer,
        kind: str,
        subject: str,
        label: str,
        *,
        results: int | None = None,
        client: Client = UNKNOWN,
        keep_record: bool = True,
    ) -> None:
        """Note that this user picked this thing straight out of the dropdown."""
        cleaned_kind = kind.strip()
        cleaned_subject = subject.strip()
        cleaned_label = " ".join(label.split())
        if not cleaned_kind or not cleaned_subject or not cleaned_label:
            return
        # A tag or a folder is picked by its NAME (it is re-run as a filter, and a filter is
        # written in names), and a name goes stale the day the thing is renamed. So the row keeps
        # the id of the one thing the name named, and `recent` gives it back under today's name.
        # A name that names several things, or nothing, is kept as it came.
        spelled = _NAMED_BY_VALUE.get(cleaned_kind)
        if spelled is not None and not is_id(cleaned_subject):
            named = (await self._compiler.resolve(viewer, spelled, [cleaned_subject])).get(
                cleaned_subject, ()
            )
            if len(named) == 1:
                cleaned_subject = named[0]
        # A pick IS the opening: the thing picked is the thing gone to, so the id that was opened
        # and the subject are one value. Said explicitly rather than left for a reader to work out
        # from the kind, because "what was opened" is the question the column is there to answer.
        await self._keep(
            viewer,
            cleaned_kind,
            cleaned_subject,
            cleaned_label,
            results=results,
            opened_id=cleaned_subject,
            client=client,
            keep_record=keep_record,
        )

    async def _keep(
        self,
        viewer: Viewer,
        kind: str,
        subject: str,
        label: str,
        *,
        results: int | None = None,
        opened_id: str | None = None,
        client: Client = UNKNOWN,
        keep_record: bool = True,
    ) -> None:
        """Put one row at the top of this user's memory, and write down that it happened."""
        now = self._now()
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            await connection.execute(_REMEMBER, (new_id(), viewer.id, kind, subject, label, now))
            await connection.execute(_TRIM, (viewer.id, viewer.id, MAX_HISTORY))
            if keep_record:
                await connection.execute(
                    _RECORD_EVENT,
                    (
                        new_id(),
                        viewer.id,
                        kind,
                        subject,
                        results,
                        opened_id,
                        now,
                        client.device,
                        client.kind,
                    ),
                )

    async def remember(
        self,
        viewer: Viewer,
        query: str,
        *,
        results: int | None = None,
        client: Client = UNKNOWN,
        keep_record: bool = True,
    ) -> None:
        """Note that this user made this search. Theirs, and only ever shown back to them."""
        cleaned = " ".join(query.split())
        if not cleaned:
            return
        # A typed search is its own subject and its own label: the words are what identifies it and
        # the words are what the row shows.
        await self._keep(
            viewer,
            QUERY_KIND,
            cleaned,
            cleaned,
            results=results,
            client=client,
            keep_record=keep_record,
        )
        if self._queue is not None:
            await catch_up_if_behind(queue=self._queue, database=self._db)

    async def recent(
        self, viewer: Viewer, prefix: str = "", *, limit: int = MAX_HISTORY
    ) -> list[Remembered]:
        """What this user's box remembers, newest first."""
        limit = max(1, min(limit, MAX_HISTORY))
        # The whole memory (it is capped at fifty) and then the words: a row's words are the name
        # the thing has NOW, so they are known only after the names are read, and matching the
        # stored words would find a renamed tag by the name it no longer has.
        rows = await self._db.fetch_all(
            _RECENT, {"viewer": viewer.id, "like": _like(""), "limit": MAX_HISTORY}
        )
        named = await self._named_now(
            viewer,
            [
                Remembered(kind=row["kind"], subject=row["subject"], label=row["label"])
                for row in rows
            ],
        )
        folded = prefix.casefold()
        return [one for one in named if one.label.casefold().startswith(folded)][:limit]

    async def _named_now(self, viewer: Viewer, rows: list[Remembered]) -> list[Remembered]:
        """The memory under the names things have today."""
        wanted: dict[Field, set[str]] = {}
        for row in rows:
            spelled = _NAMED_BY_ID.get(row.kind) or _NAMED_BY_VALUE.get(row.kind)
            if spelled is not None and is_id(row.subject):
                wanted.setdefault(spelled, set()).add(row.subject)
        names = {
            spelled: await self._compiler.names_of(viewer, spelled, sorted(ids))
            for spelled, ids in wanted.items()
        }
        out: list[Remembered] = []
        for row in rows:
            spelled = _NAMED_BY_ID.get(row.kind) or _NAMED_BY_VALUE.get(row.kind)
            if spelled is None or not is_id(row.subject):
                out.append(row)
                continue
            name = names[spelled].get(row.subject)
            if name is None:
                continue
            subject = name if row.kind in _NAMED_BY_VALUE else row.subject
            out.append(Remembered(kind=row.kind, subject=subject, label=name))
        return out

    async def forget(self, viewer: Viewer, query: str | None = None) -> None:
        """Empty this user's history, or drop one entry from it, with the record of it."""
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            if query is None:
                await clear_searches(connection, viewer.id)
                return
            subject = " ".join(query.split())
            await connection.execute(_FORGET_ONE, (viewer.id, subject))
            for statement in _FORGET_EVENTS_ONE:
                await connection.execute(statement, (viewer.id, subject))

    async def opened(
        self,
        viewer: Viewer,
        query: str,
        asset_id: str,
        *,
        client: Client = UNKNOWN,
        keep_record: bool = True,
    ) -> bool:
        """Write down that this user opened this file from the wall this typed search narrowed."""
        cleaned = " ".join(query.split())
        if not cleaned or not keep_record or not await self._access.can_view(viewer, asset_id):
            return False
        async with self._db.write() as connection:
            found = list(
                await connection.execute_fetchall(_LATEST_EVENT, (viewer.id, QUERY_KIND, cleaned))
            )
            await connection.execute(
                _RECORD_OPEN,
                (
                    new_id(),
                    viewer.id,
                    str(found[0]["id"]) if found else None,
                    cleaned,
                    asset_id,
                    self._now(),
                    client.device,
                    client.kind,
                ),
            )
        return True

    # --- saved searches ----------------------------------------------------------------------

    async def save_search(
        self, viewer: Viewer, name: str, query: str, kind: str = ASSET_WALL
    ) -> None:
        """Keep a query under a name, for this user and for one wall."""
        cleaned_name = " ".join(name.split())
        cleaned_query = " ".join(query.split())
        # `saved_searches.kind` holds the wall's own noun, and since catalog version 50 that is
        # the same word everything else says. A filter kept on the Sites wall before 0.1.153 is a
        # row carrying the old word, and this component's version 5 step rewrote every one of them,
        # so there is one kind per wall rather than two that would offer each filter on neither.
        cleaned_kind = kind.strip() or ASSET_WALL
        if not cleaned_name:
            raise ValueError("a saved search needs a name")
        # Kept by id: a filter over files names each tag, person, Site, collection, Photo Set and
        # folder by the id of the one thing it names, so a rename cannot leave it asking for a name
        # nobody has (see `stored`). Resolved BEFORE the write opens, because a lookup is a read
        # and a read inside the write would hold the lock while it runs. The other walls are
        # already spelled in ids: their facets write one.
        if cleaned_kind == ASSET_WALL:
            cleaned_query = await as_kept(self._compiler, viewer, cleaned_query)
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            # Counted and written in one transaction, so two saves arriving together cannot both
            # read a count under the cap and both add a row.
            rows = list(
                await connection.execute_fetchall(
                    _SAVED_COUNT, (cleaned_kind, cleaned_name, viewer.id)
                )
            )
            held, mine = int(rows[0]["held"]), int(rows[0]["mine"])
            # Replacing one of their own is always allowed: it adds nothing.
            if not mine and held >= MAX_SAVED_SEARCHES:
                raise TooMany(
                    f"You already have {MAX_SAVED_SEARCHES} saved searches. "
                    "Delete one to save another."
                )
            await connection.execute(
                _SAVE_SEARCH,
                (new_id(), viewer.id, cleaned_kind, cleaned_name, cleaned_query, self._now()),
            )

    async def saved_filter(self, viewer: Viewer, saved_id: str) -> AssetFilter | None:
        """One of this user's saved searches as the bound filter its words compile to, or None
        where no saved search of theirs has that id or it is not one over files."""
        for kept in await self.saved_searches(viewer):
            if kept.id == saved_id and kept.kind == ASSET_WALL:
                return await self._compiler.constrain(viewer, QueryParams(kept.query))
        return None

    async def saved_searches(self, viewer: Viewer) -> list[SavedSearch]:
        """This user's saved searches, newest first. Filtered by `user_id` in the statement, so
        there is no path here that reads another user's row and then declines to return it."""
        rows = await self._db.fetch_all(_LIST_SAVED, {"viewer": viewer.id})
        return [
            SavedSearch(
                id=row["id"],
                name=row["name"],
                query=row["query"],
                kind=str(row["kind"]),
            )
            for row in rows
        ]

    async def names_now(self, viewer: Viewer, field_name: Field, ids: list[str]) -> dict[str, str]:
        """What each of these ids of one kind is called NOW, for the ones this viewer may be shown."""
        return await self._compiler.names_of(viewer, field_name, ids)

    async def kept_filters(self, viewer: Viewer) -> list[SavedSearch]:
        """This user's saved searches as they READ today: each id put back as the name it goes by
        now, with a note for whatever the address cannot say (see `stored.as_shown`)."""
        shown: list[SavedSearch] = []
        for kept in await self.saved_searches(viewer):
            if kept.kind != ASSET_WALL:
                shown.append(kept)
                continue
            query, noted = await as_shown(self._compiler, viewer, kept.query)
            shown.append(
                SavedSearch(id=kept.id, name=kept.name, query=query, kind=kept.kind, noted=noted)
            )
        return shown

    async def rename_saved_search(self, viewer: Viewer, saved_id: str, name: str) -> bool:
        """Give a saved search a different name, keeping the query it points at."""
        cleaned = " ".join(name.split())
        if not cleaned:
            raise ValueError("a saved search needs a name")
        # Through a write connection, because this is one: `fetch_all` refuses a statement that
        # writes, which is what keeps a read path from quietly becoming a write path.
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            taken = list(
                await connection.execute_fetchall(
                    _NAME_IS_TAKEN, (viewer.id, cleaned, saved_id, viewer.id, saved_id)
                )
            )
            if taken:
                # Nothing has been written yet, so leaving here changes nothing.
                raise NameTaken(cleaned)
            rows = list(
                await connection.execute_fetchall(_RENAME_SAVED, (cleaned, viewer.id, saved_id))
            )
        return bool(rows)

    async def delete_saved_search(self, viewer: Viewer, saved_id: str) -> None:
        """Drop one saved search. Scoped to the asker in the statement: an id belonging to another
        user names no row this deletes, so it is a no-op rather than a way to reach across."""
        async with telling(self._db, Audience.of_user(viewer.id), About.MINE) as connection:
            await connection.execute(_DELETE_SAVED, (viewer.id, saved_id))

    # --- suggestions -------------------------------------------------------------------------

    async def suggest(
        self, viewer: Viewer, token: Field, prefix: str, *, limit: int = MAX_SUGGESTIONS
    ) -> list[Suggestion]:
        """What could follow this token, scoped to what the person asking may know about."""
        limit = max(1, min(limit, MAX_SUGGESTIONS))

        # A filter with a fixed set of answers offers them, read off the table beside the parser:
        # nothing to look up and nothing to scope, because the words are the language's and not
        # the library's.
        fixed = OFFERED_VALUES.get(token)
        if fixed is not None:
            folded = prefix.strip().casefold()
            return [Suggestion(value=one) for one in fixed if one.startswith(folded)][:limit]

        if token is Field.TAGS:
            return [
                Suggestion(
                    value=tag.name,
                    detail=tag.matched_as,
                    count=tag.asset_count,
                    entity_id=tag.id,
                    cover=_cover_of(tag),
                )
                for tag in await self._access.suggest_tags(viewer, prefix, limit=limit)
            ]
        if token is Field.PEOPLE:
            return [
                Suggestion(
                    value=person.name,
                    detail=person.matched_as,
                    count=person.asset_count,
                    entity_id=person.id,
                    cover=_cover_of(person),
                )
                for person in (await self._access.suggest_people(viewer, prefix, limit=limit)).items
            ]
        if token is Field.SITES:
            return [
                Suggestion(
                    value=site.name,
                    detail=site.matched_as,
                    count=site.asset_count,
                    entity_id=site.id,
                    cover=_cover_of(site, icon=icon_token(site.site_url, site.name)),
                )
                for site in await self._access.suggest_sites(viewer, prefix, limit=limit)
            ]
        if token is Field.SONGS:
            sung = await self._access.list_songs(viewer, prefix.strip(), limit=limit, anywhere=True)
            return [
                Suggestion(
                    value=one.name, count=one.item_count, entity_id=one.id, cover=_cover_of(one)
                )
                for one in sung.items
            ]
        if token is Field.PHOTO_SETS:
            folded = prefix.strip().casefold()
            page = await self._access.list_photo_sets(viewer, limit=limit)
            return [
                Suggestion(
                    value=one.name, count=one.item_count, entity_id=one.id, cover=_cover_of(one)
                )
                for one in page.items
                if not folded or one.name.casefold().startswith(folded)
            ]
        if token is Field.COLLECTIONS:
            folded = prefix.strip().casefold()
            return [
                Suggestion(
                    value=collection.name,
                    count=collection.item_count,
                    entity_id=collection.id,
                    cover=_cover_of(collection),
                )
                for collection in await self._access.visible_collections(viewer)
                if collection.name.casefold().startswith(folded)
            ][:limit]

        return await self._folders_by_path(viewer, prefix, limit=limit)

    async def _folders_by_path(
        self, viewer: Viewer, prefix: str, *, limit: int
    ) -> list[Suggestion]:
        """What could follow `in:`: the folders this viewer may see, by path or by name."""
        # `in:`: the folders this viewer may see, offered by path so that two folders with the
        # same name can be told apart.
        folded = prefix.strip().casefold()
        offered: list[Suggestion] = []
        seen: set[str] = set()
        for folder in await self._access.visible_folders(viewer):
            if not (
                folder.rel_path.casefold().startswith(folded)
                or folder.name.casefold().startswith(folded)
            ):
                continue
            value = folder.rel_path or folder.name
            if value in seen:
                continue
            seen.add(value)
            offered.append(Suggestion(value=value))
            if len(offered) >= limit:
                break
        return offered

    #: A bare word is matched only once it is this long. One letter matches most of the catalog and
    #: still costs a scoped lookup per field to say so; two is where the answer starts to mean
    #: something, and it is where a person is far enough into a name to have meant it.
    MIN_WORD = 2

    async def suggest_across(
        self,
        viewer: Viewer,
        prefix: str,
        *,
        limit: int = MAX_SUGGESTIONS,
        anywhere: bool = False,
    ) -> list[Suggestion]:
        """Everything in the catalog whose name matches a bare word being typed."""
        limit = max(1, min(limit, MAX_BAND if anywhere else MAX_SUGGESTIONS))
        word = prefix.strip()
        if len(word) < self.MIN_WORD:
            return []

        found = await self._entities_named(viewer, word, limit=limit, anywhere=anywhere)
        folded = word.casefold()
        folded = word.casefold()
        for collection in await self._access.visible_collections(viewer):
            if _matches(collection.name, folded, anywhere=anywhere):
                found.append(
                    Suggestion(
                        field=Field.COLLECTIONS,
                        value=collection.name,
                        count=collection.item_count,
                        entity_id=collection.id,
                        cover=_cover_of(collection),
                    )
                )
        await self._folders_named(viewer, folded, found, anywhere=anywhere)

        # The SONG a file carries, when the word is part of its name.
        songs = await self._access.list_songs(
            viewer, word, limit=min(limit, self.TRACKS), anywhere=True
        )
        for song in songs.items:
            found.append(
                Suggestion(
                    field=Field.SONGS,
                    value=song.name,
                    count=song.item_count,
                    entity_id=song.id,
                    cover=_cover_of(song),
                )
            )

        # Files whose TITLE is what was typed.
        for asset in await self._titled(viewer, word, limit=min(limit, self.TITLED)):
            found.append(Suggestion(value=asset[1], opens="file", entity_id=asset[0]))

        found.sort(key=_by_closeness(folded))
        return found[:limit]

    #: How many files one dropdown offers by title. Three: a word is much more often a tag or a
    #: person, and the file rows are there to be noticed rather than to be the answer.
    TITLED = 3

    #: How many SONGS one dropdown offers. Held to the same handful as the file rows and for the
    #: same reason: a word is far more often a tag or a person than a song, and a dropdown whose
    #: top half is music has stopped answering the usual question.
    TRACKS = 3

    async def _entities_named(
        self, viewer: Viewer, word: str, *, limit: int, anywhere: bool
    ) -> list[Suggestion]:
        """The tags, people and Sites a bare word names, each with the spelling that answered."""
        found: list[Suggestion] = []
        for tag in await self._access.suggest_tags(viewer, word, limit=limit, anywhere=anywhere):
            found.append(
                Suggestion(
                    field=Field.TAGS,
                    value=tag.name,
                    # Which of its other names answered, when the tag's own name did not.
                    detail=tag.matched_as,
                    count=tag.asset_count,
                    entity_id=tag.id,
                    cover=_cover_of(tag),
                )
            )
        people = await self._access.suggest_people(viewer, word, limit=limit, anywhere=anywhere)
        for person in people.items:
            found.append(
                Suggestion(
                    field=Field.PEOPLE,
                    value=person.name,
                    # Which spelling brought this row back, when it was not the name. A username
                    # typed off one of Sift's own screens offers the person it belongs to, and this
                    # is what says why: without it the row looks like an unrelated answer.
                    detail=person.matched_as,
                    count=person.asset_count,
                    entity_id=person.id,
                    cover=_cover_of(person),
                )
            )
        for site in await self._access.suggest_sites(viewer, word, limit=limit, anywhere=anywhere):
            found.append(
                Suggestion(
                    field=Field.SITES,
                    value=site.name,
                    # Which of its other names answered, when the site's own name did not.
                    detail=site.matched_as,
                    count=site.asset_count,
                    entity_id=site.id,
                    cover=_cover_of(site, icon=icon_token(site.site_url, site.name)),
                )
            )
        return found

    async def _folders_named(
        self, viewer: Viewer, folded: str, found: list[Suggestion], *, anywhere: bool
    ) -> None:
        """Add the folders a bare word names to `found`, once per value."""
        # Folders are an entity field like the rest (`in:` filters on one), so a word that names
        # a folder belongs in the same answer, in the DROPDOWN as well as in the band. A bare word
        # may rarely mean a folder, but that is about how often it is true rather than about whether
        # it is useful, and in a library where every name has a folder, typing that name and being
        # offered it is the fastest way in there is.
        for folder in await self._access.visible_folders(viewer):
            # Matched on the NAME rather than on the whole path. A path is mostly the names of its
            # parents, so matching against it offers every child of a folder whose name was typed
            # (dozens of rows that all look wrong), while the folder actually named is buried among
            # them.
            if not _matches(folder.name, folded, anywhere=anywhere):
                continue
            # A library root has an empty relative path: it IS the root, so there is no path under
            # it to name, and offered by path it would be `in:` with nothing after it.
            #
            # Skipping it is wrong too. A root is a folder somebody named, and often the one they
            # would type: a library called "redgifs archive" would offer no folder at all for that
            # word. `in:` resolves a folder by its path OR its name, so the name is a value that
            # works, and it is the only thing a root can be called.
            value = folder.rel_path or folder.name
            # Once per value, for the same reason the `in:` list is: a name shared by two roots.
            if any(one.field is Field.IN and one.value == value for one in found):
                continue
            found.append(Suggestion(field=Field.IN, value=value))

    async def _titled(self, viewer: Viewer, word: str, *, limit: int) -> list[tuple[str, str]]:
        """Files this viewer may see whose title matches, as (id, title)."""
        page = await self._access.visible_assets(
            viewer, limit=limit, asset_filter=title_filter(word)
        )
        # `one.asset.title`, not `one.title`. A scoped page holds VIEWS (the file plus what this
        # viewer may know about it), and the file is one level in. A `getattr` with a default would
        # read a wrong name as nothing and return an empty list on every call: no error, no empty
        # state, just a dropdown that never offered a file. A defensive default turns a wrong name
        # into silence, so there is none here.
        return [(one.asset.id, one.asset.title) for one in page.items if one.asset.title]


def _matches(name: str, folded: str, *, anywhere: bool) -> bool:
    """Whether a name answers this word. One rule, so the lists that are filtered here agree with
    the ones the database filters."""
    lowered = name.casefold()
    return folded in lowered if anywhere else lowered.startswith(folded)


def _by_closeness(folded: str) -> Callable[[Suggestion], tuple[object, ...]]:
    """Exact name, then what starts with the word, then the rest, and within each, what already
    names the most files."""

    def key(row: Suggestion) -> tuple[object, ...]:
        lowered = row.value.casefold()
        rank = 0 if lowered == folded else 1 if lowered.startswith(folded) else 2
        return (rank, -(row.count or 0), lowered)

    return key


def _like(prefix: str) -> str:
    """A LIKE pattern matching this prefix literally."""
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


async def clear_searches(connection: Connection, user_id: str) -> int:
    """Empty one User's search history: the box's Recent list, the record of every search and what
    was opened from one. Their saved searches are kept: each is something they chose to keep."""
    gone = 0
    for statement in (_FORGET_ALL, *_FORGET_EVENTS_ALL):
        cursor = await connection.execute(statement, (user_id,))
        gone += int(cursor.rowcount)
    return gone


register_clearing("search", clear_searches)

#: Searching.
SERVICE: Part[SearchService] = Part("search")
