# SPDX-License-Identifier: AGPL-3.0-or-later
"""The person's record: made, changed, hidden, deleted, and the aliases, links, marks and cover on it."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace

from sift.kernel.access import (
    ENTITY_SORT_SEEN,
    NO_FILTER,
    NO_NARROWING,
    AssetFilter,
    EntityNarrowing,
    ObjectType,
    PeoplePage,
    Viewer,
)
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.content.user_state import OpinionKind
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.covers import ChosenCover, chosen_from_row
from sift.kernel.db import Row
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, Object, record_event
from sift.kernel.log import get_logger
from sift.kernel.sorting import sort_key
from sift.kernel.vocabulary import Subject
from sift.slices.people.service_base import (
    Alias,
    DuplicateAlias,
    EntityState,
    Link,
    PeopleBase,
    Person,
    _check_rating,
    _held_from_row,
    _link_from_row,
    _record_params,
    _state_from_row,
    _unscoped_person,
    person_from_row,
)
from sift.slices.people.service_statements import (
    _ALIASES_OF_PERSON,
    _DELETE_LINK,
    _DELETE_PERSON,
    _INSERT_ALIAS,
    _INSERT_LINK,
    _INSERT_PERSON,
    _LINKS_OF_PEOPLE,
    _PERSON_BY_ID,
    _PERSON_COVER,
    _SET_PERSON_COVER,
    _SET_PERSON_FAVORITE,
    _SET_PERSON_RATING,
    _SET_PERSON_VAULT,
    _SITE_COVER,
    _TAG_PERSON,
    _TAGS_OF_PERSON,
    _UNTAG_PERSON,
    _UPDATE_PERSON,
    _UPDATE_PERSON_RECORD,
)

log = get_logger(__name__)


class PersonRecordMixin(PeopleBase):
    """Writes and reads one person's own record."""

    # --- people -------------------------------------------------------------------------

    async def list_people(
        self,
        viewer: Viewer,
        prefix: str = "",
        *,
        limit: int = 50,
        offset: int = 0,
        anywhere: bool = False,
        sort: str = ENTITY_SORT_SEEN,
        asset_filter: AssetFilter = NO_FILTER,
        narrowing: EntityNarrowing = NO_NARROWING,
        count_narrowed: bool = False,
    ) -> PeoplePage:
        """One page of the people list, scoped, with the scoped total."""
        return await self._access.suggest_people(
            viewer,
            prefix,
            limit=limit,
            offset=offset,
            anywhere=anywhere,
            sort=sort,
            asset_filter=asset_filter,
            narrowing=narrowing,
            count_narrowed=count_narrowed,
        )

    async def create_person(
        self, viewer: Viewer, name: str, *, vault: bool = False, notes: str | None
    ) -> Person:
        """Add somebody, optionally straight out of sight."""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            rows = list(
                await connection.execute_fetchall(
                    _INSERT_PERSON,
                    (new_id(), name, sort_key(name), notes, self._now(), viewer.id),
                )
            )
            await record_event(
                connection,
                actor=Actor.user(viewer.id),
                verb="added",
                subject=Subject(kind="person", id=str(rows[0]["id"]), name=name),
            )
        person = _unscoped_person(rows[0])
        if not vault:
            return person
        await self.set_person_vault(viewer, person.id, vault=True)
        return replace(person, vault=True)

    async def update_person(
        self,
        viewer: Viewer,
        person_id: str,
        name: str,
        *,
        vault: bool,
        notes: str | None,
        record: Mapping[str, object] | None = None,
    ) -> Person | None:
        """Write a person's name, their details, and (when one is given) their whole record."""
        before = await self._db.fetch_one(_PERSON_BY_ID, (viewer.id, person_id))
        was_called = None if before is None else str(before["name"])
        # WHAT THIS SAVE MOVES, by the record's own keys, so the History line can name it: the form
        # sends every field on every press, and a line must say exactly what happened, not "Edited"
        # alone. Read before the write, compared after it; a key whose value is what it already was
        # is not an edit.
        held = await self.person_record(person_id) if before is not None else {}
        # What this user had already decided about hiding them. The form sends the checkbox on
        # every save, so re-asserting it is most of what reaches here (see the vault write below).
        was_vaulted = before is not None and bool(before["vault"])
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            rows = list(
                await connection.execute_fetchall(
                    _UPDATE_PERSON, (name, sort_key(name), notes, person_id)
                )
            )
            if not rows:
                return None
            if record is not None:
                rows = list(
                    await connection.execute_fetchall(
                        _UPDATE_PERSON_RECORD, (*_record_params(record), person_id)
                    )
                )
            # A rename is its own word and the rest is an edit, because they are read differently:
            # "Ilva Brennan was renamed" needs the name it had, and that name is the one thing a
            # row overwritten in place cannot give back. The subject carries the NEW name and the
            # payload the old one, so a reader has both without a second lookup.
            moved = [
                key
                for key, value in (record or {}).items()
                if (held.get(key) or None) != (value or None)
            ]
            if (held.get("details") or None) != (notes or None):
                moved.append("details")
            renamed = was_called is not None and was_called != name
            await record_event(
                connection,
                actor=Actor.user(viewer.id),
                verb="renamed" if renamed else "edited",
                subject=Subject(kind="person", id=person_id, name=name),
                payload=(
                    json.dumps({"before": was_called})
                    if renamed
                    else (
                        json.dumps({"fields": [{"field": one} for one in moved]}) if moved else None
                    )
                ),
            )
        # ONLY when it moves. The record editor sends `vault` on every save, and re-writing the same
        # concealment on every rename would append an opinion saying what somebody decided about
        # hiding them, which a rename is not. A press that changes nothing still records, exactly as
        # the heart on a file does; a save that was never about the vault does not reach the writer
        # at all.
        if vault != was_vaulted:
            await self.set_person_vault(viewer, person_id, vault=vault)
        return replace(_unscoped_person(rows[0]), vault=vault)

    async def set_person_vault(self, viewer: Viewer, person_id: str, *, vault: bool) -> None:
        """Hide somebody, or bring them back, for this user only."""
        now = self._now()
        await self._write_for(
            viewer.id,
            _SET_PERSON_VAULT,
            (person_id, viewer.id, int(vault), now if vault else None, now),
            about="person",
            records=OpinionKind.HIDE,
        )

    async def delete_person(self, viewer: Viewer, person_id: str) -> bool:
        """Delete a person, their aliases, their assignments, and every grant that named them."""
        await self._access.forget_object(ObjectType.PERSON, person_id)
        # Read BEFORE the row goes. A name looked up afterwards is nothing at all, and the one
        # question somebody brings to a record of deletions is who it was.
        existing = await self._db.fetch_one(_PERSON_BY_ID, (viewer.id, person_id))
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            rows = list(await connection.execute_fetchall(_DELETE_PERSON, (person_id,)))
            if rows:
                await record_event(
                    connection,
                    actor=Actor.user(viewer.id),
                    verb="deleted",
                    subject=Subject(
                        kind="person",
                        id=person_id,
                        name=None if existing is None else str(existing["name"]),
                    ),
                )
        deleted = bool(rows)
        if deleted:  # pragma: no cover (the router resolves the person first, so this holds)
            # The id and nothing else. A name in a log file is the one copy of it that none of
            # the rules protecting this table reach.
            log.info("people.deleted", person_id=person_id)
        return deleted

    async def get_person(self, viewer: Viewer, person_id: str) -> Person | None:
        row = await self._db.fetch_one(_PERSON_BY_ID, (viewer.id, person_id))
        return person_from_row(row) if row else None

    # --- aliases ------------------------------------------------------------------------

    async def add_alias(self, person_id: str, alias: str) -> Alias:
        """Add an explicit "also known as"."""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            rows = list(
                await connection.execute_fetchall(
                    _INSERT_ALIAS, (new_id(), person_id, alias, sort_key(alias), self._now())
                )
            )
        if not rows:
            raise DuplicateAlias(person_id)
        row = rows[0]
        return Alias(id=row["id"], person_id=row["person_id"], alias=row["alias"])

    async def ensure_alias(self, person_id: str, alias: str) -> Alias | None:
        """Make sure this person answers to that spelling. None if they already did."""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            return await self._ensure_alias_on(connection, person_id, alias)

    async def remove_alias(self, person_id: str, alias_id: str, *, actor: Actor) -> bool:
        """Both ids, so an alias can only be removed through the person it belongs to."""
        name = await self._person_name(person_id)
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            return await self._remove_alias_on(connection, person_id, alias_id, name, actor=actor)

    # --- links ---------------------------------------------------------------------------

    async def add_link(
        self, person_id: str, url: str, *, site_id: str | None = None, label: str | None = None
    ) -> Link | None:
        """Record somewhere this person can be found. None if they already hold that address."""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            rows = list(
                await connection.execute_fetchall(
                    _INSERT_LINK,
                    (new_id(), person_id, url, site_id, label, int(self._now())),
                )
            )
        if not rows:
            return None
        return await self._one_link(str(rows[0]["id"]))

    async def remove_link(self, person_id: str, link_id: str, *, actor: Actor) -> bool:
        """Both ids, so a link can only be removed through the person it belongs to."""
        name = await self._person_name(person_id)
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            rows = list(await connection.execute_fetchall(_DELETE_LINK, (link_id, person_id)))
            if rows:
                await record_event(
                    connection,
                    actor=actor,
                    verb="unlinked",
                    subject=Subject(kind="person", id=person_id, name=name or None),
                    payload=json.dumps({"url": str(rows[0]["url"])}),
                )
        return bool(rows)

    async def links_of(self, person_id: str) -> list[Link]:
        return [
            _link_from_row(row)
            for row in await self._db.fetch_all(_LINKS_OF_PEOPLE, (json.dumps([person_id]),))
        ]

    async def _one_link(self, link_id: str) -> Link | None:
        row = await self._db.fetch_one(
            "SELECT l.*, p.name AS site_name FROM people_links l "
            "LEFT JOIN sites p ON p.id = l.site_id WHERE l.id = ?",
            (link_id,),
        )
        return None if row is None else _link_from_row(row)

    async def aliases_of(self, person_id: str) -> list[Alias]:
        rows = await self._db.fetch_all(_ALIASES_OF_PERSON, (person_id,))
        return [Alias(id=row["id"], person_id=row["person_id"], alias=row["alias"]) for row in rows]

    # --- what a viewer thinks of a person or a site ---------------------------------

    async def set_person_favorite(
        self, person_id: str, user_id: str, favorite: bool
    ) -> EntityState:
        """Heart a person, or take the heart off. This viewer's, not the person's."""
        rows = await self._write_mine(
            user_id,
            _SET_PERSON_FAVORITE,
            (person_id, user_id, int(favorite), self._now()),
            about="person",
            records=OpinionKind.FAVORITE,
        )
        return _state_from_row(rows[0])

    async def set_person_rating(
        self, person_id: str, user_id: str, rating: int | None
    ) -> EntityState:
        """Set stars, or clear them with None."""
        _check_rating(rating)
        rows = await self._write_mine(
            user_id,
            _SET_PERSON_RATING,
            (person_id, user_id, rating, self._now()),
            about="person",
            records=OpinionKind.RATING,
        )
        return _state_from_row(rows[0])

    # --- tags on a person or a site -------------------------------------------------

    async def tags_of_person(self, person_id: str, viewer: Viewer | None = None) -> list[Row]:
        """The tags on one person the viewer may be shown, as rows: a tag's shape is the tags
        slice's."""
        admin = viewer is None or viewer.is_admin
        return await self._db.fetch_all(
            _TAGS_OF_PERSON,
            {
                "subject": person_id,
                "is_admin": int(admin),
                "viewer": None if viewer is None else viewer.id,
            },
        )

    async def tag_person(self, person_id: str, tag_id: str, *, add: bool = True) -> None:
        if add:
            await self._write_shared(_TAG_PERSON, (person_id, tag_id, self._now()))
        else:
            await self._write_shared(_UNTAG_PERSON, (person_id, tag_id))

    # --- the picture a person or a site is shown as ---------------------------------

    async def set_person_cover(
        self,
        person_id: str,
        asset_id: str | None,
        at_ms: int | None = None,
        upload_id: str | None = None,
        *,
        actor: Actor,
        box: Object | None = None,
        frame: CoverFrame | None = None,
    ) -> bool:
        """Point a person at the still they are drawn as, and optionally at which moment of it."""
        return await self._cover_set(
            _SET_PERSON_COVER,
            _PERSON_COVER,
            "person",
            person_id,
            (asset_id, at_ms, upload_id, frame),
            actor,
            box,
        )

    async def chosen_cover(self, kind: str, entity_id: str) -> ChosenCover:
        """What a cover names, for one of the two kinds here."""
        statement = {
            "person": _PERSON_COVER,
            "site": _SITE_COVER,
        }[kind]
        row = await self._db.fetch_one(statement, (entity_id,))
        if row is None:  # pragma: no cover (the route resolved the entity a line ago)
            return ChosenCover()
        return chosen_from_row(row)

    async def person_record(self, person_id: str) -> dict[str, object]:
        """A person's record fields, by field key, including the two that are not promoted columns."""
        row = await self._db.fetch_one("SELECT * FROM people WHERE id = ?", (person_id,))
        return {} if row is None else _held_from_row(row)
