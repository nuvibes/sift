# SPDX-License-Identifier: AGPL-3.0-or-later
"""The stash-boxes somebody configured: adding, pacing, routing and unsealing them.

The first layer of `StashBoxService`. The key is write-only everywhere; what a screen reads is
`BoxView`, which says whether there is a key and never what it is.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from sift.kernel.access.catalog import (
    clear_created_by_box,
)
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, announce_now, telling
from sift.kernel.db import Database, PointRead, Row, point_read
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, Object, record_event
from sift.kernel.log import get_logger
from sift.kernel.records import SourceAnswer, SourceLink, Subject
from sift.kernel.secret_store import SecretStore
from sift.kernel.urls import stash_box_page
from sift.kernel.vocabulary import Subject as DecisionSubject
from sift.slices.stash_boxes.adapter import (
    ANSWER_SHAPE,
    DEFAULT_REQUESTS_PER_MINUTE,
    Box,
    StashBoxAdapter,
)
from sift.slices.stash_boxes.adapter import (
    EXACT as EXACT,
)
from sift.slices.stash_boxes.known_boxes import SITES_ARE_PEOPLE, sites_are_for, slug_for
from sift.slices.stash_boxes.settings import CHOSEN_BOX_OFF, EVERY_BOX_OFF

log = get_logger(__name__)

_LIST = "SELECT * FROM stash_boxes ORDER BY id"

_ONE = "SELECT * FROM stash_boxes WHERE id = ?"

_ADD = (
    "INSERT INTO stash_boxes (id, name, endpoint, secret_id, enabled, route,"
    " requests_per_minute, sites_are, slug, created_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)"
)

_FORGET = "DELETE FROM stash_boxes WHERE id = ?"

_SET_ENABLED = "UPDATE stash_boxes SET enabled = ? WHERE id = ?"

_SET_PACE = "UPDATE stash_boxes SET requests_per_minute = ? WHERE id = ?"

_SET_ROUTE = "UPDATE stash_boxes SET route = ? WHERE id = ?"

_SET_KEY = "UPDATE stash_boxes SET secret_id = ? WHERE id = ?"

_DROP_CACHE = "DELETE FROM stash_box_answers WHERE box_id = ?"

_DROP_EVERY_ANSWER = "DELETE FROM stash_box_answers"


@dataclass(frozen=True, slots=True)
class _LinkTable:
    """The four statements one subject's link table needs, all of them literal.

    Each subject has its OWN table with a real foreign key, so three near-identical sets are
    written out: no SQL is built from strings here (a gate holds it), and picking between literals
    keeps every statement greppable by its table.
    """

    write: str
    #: Every box's answer about ONE subject. A `PointRead` rather than text: it binds the subject,
    #: it seeks the primary key, and it is now asked on every entity page view. See
    #: `Reconciler.for_subject`. Declaring it is what puts its plan under the gate that proves a
    #: per-subject read stays a seek however many links a library accumulates.
    read: PointRead
    read_one: str
    forget: str


_PERSON_LINKS = _LinkTable(
    write=(
        "INSERT INTO person_stash_box_links (person_id, box_id, remote_id, payload, fetched_at)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(person_id, box_id) DO UPDATE SET remote_id = excluded.remote_id,"
        " payload = excluded.payload, fetched_at = excluded.fetched_at"
    ),
    read=point_read(
        "stash_boxes.person_links",
        "SELECT * FROM person_stash_box_links WHERE person_id = ? ORDER BY fetched_at DESC, box_id",
    ),
    read_one="SELECT * FROM person_stash_box_links WHERE person_id = ? AND box_id = ?",
    forget="DELETE FROM person_stash_box_links WHERE person_id = ? AND box_id = ?",
)

_SITE_LINKS = _LinkTable(
    write=(
        "INSERT INTO site_stash_box_links"
        " (site_id, box_id, remote_id, payload, fetched_at) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(site_id, box_id) DO UPDATE SET remote_id = excluded.remote_id,"
        " payload = excluded.payload, fetched_at = excluded.fetched_at"
    ),
    read=point_read(
        "stash_boxes.site_links",
        "SELECT * FROM site_stash_box_links WHERE site_id = ? ORDER BY fetched_at DESC, box_id",
    ),
    read_one="SELECT * FROM site_stash_box_links WHERE site_id = ? AND box_id = ?",
    forget="DELETE FROM site_stash_box_links WHERE site_id = ? AND box_id = ?",
)

_TAG_LINKS = _LinkTable(
    write=(
        "INSERT INTO tag_stash_box_links (tag_id, box_id, remote_id, payload, fetched_at)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(tag_id, box_id) DO UPDATE SET remote_id = excluded.remote_id,"
        " payload = excluded.payload, fetched_at = excluded.fetched_at"
    ),
    read=point_read(
        "stash_boxes.tag_links",
        "SELECT * FROM tag_stash_box_links WHERE tag_id = ? ORDER BY fetched_at DESC, box_id",
    ),
    read_one="SELECT * FROM tag_stash_box_links WHERE tag_id = ? AND box_id = ?",
    forget="DELETE FROM tag_stash_box_links WHERE tag_id = ? AND box_id = ?",
)

#: Which table holds which subject's link. A file has none: its answer is a match row, kept by the
#: fingerprint pass or from an id somebody gave it (`keep_known_scene`).
_TABLES: Mapping[Subject, _LinkTable] = {
    Subject.PERSON: _PERSON_LINKS,
    Subject.SITE: _SITE_LINKS,
    Subject.TAG: _TAG_LINKS,
}

#: Whether a one-time pass has run, and the record that it has. See `_CREATE_CATCH_UPS`.
_CATCH_UP_RAN = "SELECT 1 FROM stash_box_catch_ups WHERE name = ?"

_RECORD_CATCH_UP = (
    "INSERT INTO stash_box_catch_ups (name, ran_at) VALUES (?, ?) ON CONFLICT(name) DO NOTHING"
)


@dataclass(frozen=True, slots=True)
class BoxView:
    """One configured box, as a screen reads it. Never the key, only whether there is one."""

    id: str
    name: str
    endpoint: str
    enabled: bool
    has_key: bool
    route: str | None
    requests_per_minute: int
    #: What this box's Sites are here: 'site' or 'person'. See the column of the same name.
    sites_are: str = "site"
    #: The word this box is known by (`stashdb`, `fansdb`, `pmvstash`), or None for one Sift has
    #: never heard of. What the facets count under and what the marks take their color from.
    slug: str | None = None


#: The shelf of a stash-box's own web pages each kind of subject is kept on, in the box's words.
#:
#: The box's words and not Sift's, because this is an address on the box: a Site is a studio there.
#: A person on a box whose studios are the creators is the exception, and `entry_page` makes it.
_SHELVES: Mapping[Subject, str] = {
    Subject.PERSON: "performers",
    Subject.SITE: "studios",
    Subject.TAG: "tags",
}


def entry_page(box: BoxView, subject: Subject, remote_id: str) -> str | None:
    """The box's own page for one of its entries, or None for a box whose pages Sift does not know."""
    shelf = _SHELVES.get(subject)
    if shelf is None:
        return None
    if subject is Subject.PERSON and box.sites_are == SITES_ARE_PEOPLE:
        shelf = "studios"
    return stash_box_page(box.endpoint.strip(), shelf, remote_id)


#: What one box said, and one box's kept record of a subject.
#:
#: The kernel's types, under this slice's own names: a second slice reads them, and a seam that
#: speaks one slice's dataclass could only be implemented by that slice. The aliases stay because
#: "box" is what this slice's own code calls a source.
Answer = SourceAnswer
Linked = SourceLink


class ConfiguredBoxes:
    """Which boxes there are, how each is asked, and the key that opens each one."""

    def __init__(
        self,
        database: Database,
        secrets: SecretStore,
        adapter: StashBoxAdapter,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = database
        self._secrets = secrets
        self._adapter = adapter
        self._clock = clock
        # Whether the cache has been checked against how this adapter reads. See `_answers_read_now`.
        self._shape_checked = False

    def _now(self) -> int:
        return int(self._clock())

    # --- Configuring -----------------------------------------------------------------------

    async def boxes(self) -> list[BoxView]:
        """Every configured box. `has_key` and never the key: a key is write-only, everywhere."""
        rows = await self._db.fetch_all(_LIST)
        return [
            BoxView(
                id=str(row["id"]),
                name=str(row["name"]),
                endpoint=str(row["endpoint"]),
                enabled=bool(row["enabled"]),
                has_key=row["secret_id"] is not None,
                route=row["route"],
                requests_per_minute=int(row["requests_per_minute"]),
                sites_are=str(row["sites_are"]),
                slug=None if row["slug"] is None else str(row["slug"]),
            )
            for row in rows
        ]

    async def _say(self, sql: str, params: tuple[object, ...], about: About) -> None:
        """Write, and tell the screens that draw what changed.

        Two kinds of thing live in here and they reach different screens. Which stash-boxes are
        configured, and how each is paced and routed, is a setting the whole installation shares.
        What a file is linked to, and the answers kept about it, is what a record draws. Both are
        admin-only, and the screens that show them refuse a guest.
        """
        await self._db.execute(sql, params)
        announce_now(EVERY_ADMIN, about)

    async def add(
        self,
        *,
        name: str,
        endpoint: str,
        api_key: str | None,
        master_key: bytes | None,
        route: str | None = None,
        requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
    ) -> str:
        """Configure a box. Returns its id."""
        secret_id = None
        if api_key and master_key:
            secret_id = await self._secrets.seal(api_key.encode("utf-8"), master_key)
        box_id = new_id()
        sites_are = sites_are_for(endpoint)
        # The second thing derived from the address and stored beside the first. NULL for a box
        # nothing here has a word for, which is the honest answer and what the screens draw as the
        # ordinary accent.
        slug = slug_for(endpoint)
        await self._say(
            _ADD,
            (
                box_id,
                name,
                endpoint,
                secret_id,
                route,
                requests_per_minute,
                sites_are,
                slug,
                self._now(),
            ),
            About.SETTINGS,
        )
        log.info("stashbox.added", box=name)
        return box_id

    async def forget(self, box_id: str) -> bool:
        """Remove a box. Its cached answers go with it, by the foreign key."""
        await clear_created_by_box(self._db, box_id)
        await self._say(_FORGET, (box_id,), About.SETTINGS)
        return True

    async def set_enabled(self, box_id: str, enabled: bool) -> None:
        """Switch a box on or off. Off is absent, not broken: nothing asks it and nothing complains."""
        await self._say(_SET_ENABLED, (1 if enabled else 0, box_id), About.SETTINGS)

    async def set_pace(self, box_id: str, requests_per_minute: int) -> None:
        """How fast this box may be asked. Configuration because a service may change its mind."""
        await self._say(_SET_PACE, (max(1, requests_per_minute), box_id), About.SETTINGS)

    async def set_route(self, box_id: str, route: str | None) -> None:
        """Send this box's traffic through a tunnel, or straight out. Direct is the default."""
        await self._say(_SET_ROUTE, (route, box_id), About.SETTINGS)

    async def set_key(self, box_id: str, api_key: str, master_key: bytes) -> None:
        """Replace the key. Sealed on the way in; the old secret is left for the store to reap."""
        secret_id = await self._secrets.seal(api_key.encode("utf-8"), master_key)
        await self._say(_SET_KEY, (secret_id, box_id), About.SETTINGS)
        await self._say(_DROP_CACHE, (box_id,), About.SETTINGS)

    async def forget_answers(self, box_id: str) -> None:
        """Throw away what a box has said, so the next question is asked for real.

        No bell: no screen draws the answer cache (see `_one`).
        """
        await self._db.execute(_DROP_CACHE, (box_id,))

    async def _answers_read_now(self) -> None:
        """Forget every cached answer an older adapter read, once, before the cache is next read.

        The cache holds records already read into Sift's words, so an answer read the old way would
        be applied the old way for a month (`adapter.ANSWER_SHAPE`). Remembered as a one-time pass
        under the shape it was for, so a restart keeps what the current adapter read.
        """
        if self._shape_checked:
            return
        name = f"answers_read_as:{ANSWER_SHAPE}"
        if not await self.catch_up_ran(name):
            await self._db.execute(_DROP_EVERY_ANSWER)
            await self.record_catch_up(name)
        self._shape_checked = True

    async def catch_up_ran(self, name: str) -> bool:
        """Whether this one-time pass has already run. See `stash_box_catch_ups`."""
        return await self._db.fetch_one(_CATCH_UP_RAN, (name,)) is not None

    async def record_catch_up(self, name: str) -> None:
        """Remember that it has, so it never runs again. `DO NOTHING` on a second write, because
        two runs that both finish is one pass having happened rather than an error."""
        await self._db.execute(_RECORD_CATCH_UP, (name, self._now()))

    async def key_ready(self, box_id: str, master_key: bytes | None) -> bool:
        """Whether this box's key can be opened right now, which is not the same as it existing."""
        return await self._unsealed(box_id, master_key) is not None

    async def _sealed_sentence(self, box_id: str) -> str:
        """Why a box `_unsealed` refused cannot be asked, in the words the screen shows."""
        row = await self._db.fetch_one(_ONE, (box_id,))
        if row is None:
            return "This stash-box is no longer set up."
        return (
            "This stash-box's key is locked because Sift restarted. Enter your password under "
            "Unlock in Settings > Stash-boxes."
        )

    async def _unsealed(self, box_id: str, master_key: bytes | None) -> Box | None:
        """One box with its key opened, or None when there is nothing usable to ask with."""
        row = await self._db.fetch_one(_ONE, (box_id,))
        if row is None:
            return None
        api_key = None
        if row["secret_id"] is not None:
            # IMPORTANT: A box that HAS a key is never asked without it. With no master key (a session
            # resumed from a cookie after a restart) the key cannot be opened, and asking anyway
            # would send an unauthenticated query whose refusal comes back as a 200 with an
            # `errors` array. That reads as "this person is not in there", which is the one wrong
            # answer nobody would go looking for.
            if master_key is None:
                return None
            opened = await self._secrets.open(str(row["secret_id"]), master_key)
            if opened is None:
                return None
            api_key = opened.decode("utf-8")
        return Box(
            id=str(row["id"]),
            name=str(row["name"]),
            endpoint=str(row["endpoint"]),
            api_key=api_key,
            route=row["route"],
            requests_per_minute=int(row["requests_per_minute"]),
            studios_are_people=str(row["sites_are"]) == "person",
        )

    async def _enabled_ids(self, only: str | None = None) -> list[str]:
        """Which boxes may be asked, narrowed to one where the caller named one.

        `only` is a box's own WORD (`stashdb`, `fansdb`), never its id or its name, for the
        reason written over `AUTO_BOX_KEY`. A word naming no configured box answers with an empty
        list rather than with every box: "ask only FansDB" on an install where FansDB has been
        removed must ask nobody, and falling back to all of them would be the one reading nobody
        intended: it would send out exactly what somebody narrowed the pass to stop sending.
        """
        return [str(row["id"]) for row in await self._enabled_rows(only)]

    async def _enabled_rows(self, only: str | None = None) -> list[Row]:
        """The switched-on boxes' rows, narrowed as `_enabled_ids` narrows them. The one reader
        of the slug rule, so a caller wanting the name beside the id cannot drift from one wanting
        the id alone on what a word matches."""
        rows = await self._db.fetch_all(_LIST)
        enabled = [row for row in rows if row["enabled"]]
        if only is not None:
            enabled = [
                row for row in enabled if row["slug"] is not None and str(row["slug"]) == only
            ]
        return enabled

    async def asks_anyone(self, box: str = "") -> bool:
        """Whether a press resolved to `box` would ask any stash-box at all.

        `box` is what `settings.box_for` resolves a press to: "" is every switched-on box, a word is
        that box alone. False when every box it names is turned off, the state in which a pass
        would walk the library and ask nobody.
        """
        return bool(await self._enabled_ids(box or None))

    async def names_asked(self, box: str = "") -> list[str]:
        """The names of the boxes a press resolved to `box` would ask, read as `asks_anyone` is."""
        return [str(row["name"]) for row in await self._enabled_rows(box or None)]

    async def cannot_ask(self, box: str = "") -> str | None:
        """Why a press resolved to `box` may not start, or None when it would ask some stash-box.

        The one rule for every press that queues questions (Run now on the Stash-boxes pane, the
        Enrich press on files, folders and the whole library, and the one on people, sites and
        tags), so no two of them answer the same state differently, and none answers "started"
        for work that would ask nobody.
        """
        if await self.asks_anyone(box):
            return None
        return CHOSEN_BOX_OFF if box and await self.asks_anyone() else EVERY_BOX_OFF

    async def record_asking(self, *, actor: Actor, count: int, only: str | None) -> None:
        """Write down that a sweep queued `count` questions: one event per box it asked."""
        if count < 1:
            return
        boxes = await self._enabled_rows(only)
        if not boxes:
            return
        # Told to every admin as a library change, because that is the bell Settings > History
        # listens for: a line written in silence sits unseen on an open feed until a reload.
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            for row in boxes:
                box = Object(kind="box", id=str(row["id"]), name=str(row["name"]))
                await record_event(
                    connection,
                    actor=actor,
                    verb="asked",
                    subject=DecisionSubject(kind="box", id=box.id, name=box.name),
                    object=box,
                    count=count,
                )
