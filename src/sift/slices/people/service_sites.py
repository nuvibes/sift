# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Site's record: made, changed, hidden, deleted, and the links, aliases, parent, marks and cover
on it."""

from __future__ import annotations

import json
from collections.abc import Sequence

from sift.kernel.access import (
    ObjectType,
    Viewer,
    ensure_site,
)
from sift.kernel.access.catalog import by_user as made_by_user
from sift.kernel.access.sites import SITE_WITHIN
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.content.user_state import OpinionKind
from sift.kernel.cover_frame import CoverFrame
from sift.kernel.db import Connection, Row
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor, Object, record_event
from sift.kernel.log import get_logger
from sift.kernel.sorting import sort_key
from sift.kernel.text import clean_token_text
from sift.kernel.vocabulary import Subject
from sift.slices.people.service_base import (
    EntityState,
    PeopleBase,
    Site,
    SiteLoop,
    _check_rating,
    _made_by,
    _state_from_row,
    site_links_in_order,
)
from sift.slices.people.service_statements import (
    _CLEAR_SITE_ALIASES,
    _CLEAR_SITE_LINKS,
    _DELETE_SITE,
    _DELETE_SITE_USERNAMES,
    _INSERT_SITE_ALIAS,
    _INSERT_SITE_LINK,
    _LINKS_OF_SITE,
    _SET_SITE_COVER,
    _SET_SITE_FAVORITE,
    _SET_SITE_PARENT,
    _SET_SITE_RATING,
    _SET_SITE_VAULT,
    _SITE_ALIAS_MOMENTS,
    _SITE_ALIASES,
    _SITE_BY_ID,
    _SITE_COVER,
    _SITE_PARENT,
    _TAG_SITE,
    _TAGS_OF_SITE,
    _UNTAG_SITE,
    _UPDATE_SITE,
    _UPDATE_SITE_DETAILS,
)

log = get_logger(__name__)


class SiteRecordMixin(PeopleBase):
    """Writes and reads one Site's own record."""

    async def create_site(self, viewer: Viewer, name: str) -> Site:
        """Add a site, or return the one already carrying that name."""
        # A PERSON is making it, and the viewer is right here to name, not 'sift', the shared
        # upsert's own word, which would make a site added on the Sites screen indistinguishable on
        # its own page from one a download invented.
        site_id = await ensure_site(self._db, name, made=made_by_user(viewer.id))
        created = await self.get_site(viewer, site_id)
        assert created is not None  # noqa: S101 (just written, in the same call)
        return created

    async def site_named(self, name: str) -> str | None:
        """The id of a site with this name, or None. Matched case-insensitively, never created."""
        row = await self._db.fetch_one("SELECT id FROM sites WHERE name = ?", (name,))
        return None if row is None else str(row["id"])

    async def get_site(self, viewer: Viewer, site_id: str) -> Site | None:
        row = await self._db.fetch_one(_SITE_BY_ID, {"viewer": viewer.id, "site_id": site_id})
        if row is None:
            return None
        return Site(
            id=row["id"],
            name=row["name"],
            vault=bool(row["vault"]),
            keep_local=bool(row["keep_local"]),
            keep_from_swaps=bool(row["keep_from_swaps"]),
            created_at=None if row["created_at"] is None else int(row["created_at"]),
            site_url=row["site_url"],
        )

    async def update_site(self, viewer: Viewer, site_id: str, name: str) -> Site:
        """Rename a site. Returns the site unchanged if the name is already taken."""
        was_called = (await self._site_names([site_id])).get(site_id)
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            rows = list(
                await connection.execute_fetchall(_UPDATE_SITE, (name, sort_key(name), site_id))
            )
            # Only where the write took. `UPDATE OR IGNORE` does nothing when the name belongs to
            # another site, and a record saying a site was renamed when it was not is worse than
            # silence: it is the one kind of wrong a record cannot be checked against.
            if rows and was_called != name:
                await record_event(
                    connection,
                    actor=Actor.user(viewer.id),
                    verb="renamed",
                    subject=Subject(kind="site", id=site_id, name=name),
                    payload=json.dumps({"before": was_called}),
                )
        refreshed = await self.get_site(viewer, site_id)
        assert refreshed is not None  # noqa: S101 (the caller resolved it one statement ago)
        return refreshed

    async def set_site_vault(self, viewer: Viewer, site_id: str, *, vault: bool) -> bool:
        """Hide a site, or bring it back, for this user only. False when there is no such site."""
        if await self.get_site(viewer, site_id) is None:
            return False
        now = self._now()
        await self._write_for(
            viewer.id,
            _SET_SITE_VAULT,
            (site_id, viewer.id, int(vault), now if vault else None, now),
            about="site",
            records=OpinionKind.HIDE,
        )
        return True

    async def delete_site(self, viewer: Viewer, site_id: str) -> bool:
        """Delete a site, every grant that named it, and what it recorded about where files came from.

        **No file is touched and nobody goes.**
        """
        attached = await self._count_usernames(site_id)
        await self._access.forget_object(ObjectType.SITE, site_id)
        # Read before the row goes; see `delete_person` for why a name looked up afterwards is no
        # name at all.
        was_called = (await self._site_names([site_id])).get(site_id)
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            await connection.execute(_DELETE_SITE_USERNAMES, (site_id,))
            rows = list(await connection.execute_fetchall(_DELETE_SITE, (site_id,)))
            if rows:
                await record_event(
                    connection,
                    actor=Actor.user(viewer.id),
                    verb="deleted",
                    subject=Subject(kind="site", id=site_id, name=was_called),
                )
        deleted = bool(rows)
        if deleted:
            log.info("sites.deleted", site_id=site_id, usernames=attached)
        return deleted

    async def site_links(self, site_id: str) -> list[str]:
        """Where a site can be found, oldest first. Addresses only; a site's link has no label yet."""
        rows = await self._db.fetch_all(_LINKS_OF_SITE, (site_id,))
        return [str(row["url"]) for row in rows]

    async def set_site_links(self, site_id: str, urls: Sequence[str], *, actor: Actor) -> None:
        """Write a site's whole list of addresses, replaced whole. See the statement's own note."""
        named = await self._site_names([site_id])
        cleaned = site_links_in_order(urls, named.get(site_id))
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            await self._rewrite_site_links(connection, site_id, cleaned)
            # The whole list is replaced, so the rows that went carry no tombstone at all: without
            # this, an address somebody deleted is gone along with the fact that it was ever there.
            # The payload holds the list as it stands after the save rather than a diff, because a
            # diff of two lists that ARE the before and the after is work with a wrong answer in it.
            await record_event(
                connection,
                actor=actor,
                verb="edited",
                subject=Subject(kind="site", id=site_id, name=named.get(site_id)),
                payload=json.dumps({"links": cleaned}),
            )

    async def add_site_link(self, site_id: str, url: str) -> bool:
        """Record one more address for a site. False when it already holds that one.

        **NO EVENT OF ITS OWN, and that is a decision rather than a gap.**
        """
        cleaned = url.strip()
        if not cleaned:
            return False
        named = await self._site_names([site_id])
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            held = [
                str(row["url"])
                for row in await connection.execute_fetchall(_LINKS_OF_SITE, (site_id,))
            ]
            if cleaned in held:
                return False
            # THROUGH THE SAME ORDER THE FORM'S SAVE WRITES (`site_links_in_order`). Appended as it
            # came, a stash-box answer that lists another site's page ahead of the site's own home
            # would keep that order, and the first link is the Site's address, so the address would
            # be the wrong one until somebody happened to save the record, when the list would jump
            # into a different order with nothing typed.
            wanted = site_links_in_order([*held, cleaned], named.get(site_id))
            if wanted == [*held, cleaned]:
                # The common case: this one belongs at the end, so the rows already there keep
                # their ids and only one is written.
                await connection.execute_fetchall(
                    _INSERT_SITE_LINK, (new_id(), site_id, cleaned, None, self._now())
                )
            else:
                await self._rewrite_site_links(connection, site_id, wanted)
        return True

    async def _rewrite_site_links(
        self, connection: Connection, site_id: str, urls: Sequence[str]
    ) -> None:
        """Replace a site's list with these, in this order. The ids ARE the order: minted in turn,
        the first minted is the Site's address (`sites.SITE_ADDRESS`)."""
        await connection.execute(_CLEAR_SITE_LINKS, (site_id,))
        for url in urls:
            await connection.execute_fetchall(
                _INSERT_SITE_LINK, (new_id(), site_id, url, None, self._now())
            )

    async def set_site_favorite(self, site_id: str, user_id: str, favorite: bool) -> EntityState:
        rows = await self._write_mine(
            user_id,
            _SET_SITE_FAVORITE,
            (site_id, user_id, int(favorite), self._now()),
            about="site",
            records=OpinionKind.FAVORITE,
        )
        return _state_from_row(rows[0])

    async def set_site_rating(self, site_id: str, user_id: str, rating: int | None) -> EntityState:
        _check_rating(rating)
        rows = await self._write_mine(
            user_id,
            _SET_SITE_RATING,
            (site_id, user_id, rating, self._now()),
            about="site",
            records=OpinionKind.RATING,
        )
        return _state_from_row(rows[0])

    async def tags_of_site(self, site_id: str) -> list[Row]:
        return await self._db.fetch_all(_TAGS_OF_SITE, (site_id,))

    async def tag_site(self, site_id: str, tag_id: str, *, add: bool = True) -> None:
        if add:
            await self._write_shared(_TAG_SITE, (site_id, tag_id, self._now()))
        else:
            await self._write_shared(_UNTAG_SITE, (site_id, tag_id))

    async def set_site_cover(
        self,
        site_id: str,
        asset_id: str | None,
        at_ms: int | None = None,
        upload_id: str | None = None,
        *,
        actor: Actor,
        box: Object | None = None,
        frame: CoverFrame | None = None,
    ) -> bool:
        """The same for a site. See `set_person_cover` and `_cover_set`."""
        return await self._cover_set(
            _SET_SITE_COVER,
            _SITE_COVER,
            "site",
            site_id,
            (asset_id, at_ms, upload_id, frame),
            actor,
            box,
        )

    async def update_site_details(self, site_id: str, notes: str | None, *, actor: Actor) -> bool:
        """What somebody wrote about a site. Its address is its first link: see `set_site_links`."""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            rows = list(await connection.execute_fetchall(_UPDATE_SITE_DETAILS, (notes, site_id)))
            if rows:
                # The payload says what the site ended up with, under the record's own key (the
                # notes are its details), so the History line can name the field rather than say
                # "Edited" alone. The links say themselves: `set_site_links`.
                await record_event(
                    connection,
                    actor=actor,
                    verb="edited",
                    subject=Subject(kind="site", id=site_id, name=str(rows[0]["name"])),
                    payload=json.dumps({"details": notes}),
                )
        return bool(rows)

    async def site_record(self, site_id: str) -> dict[str, object]:
        """A site's record fields, by field key, for the page that draws one."""
        aliases = [str(row["alias"]) for row in await self._db.fetch_all(_SITE_ALIASES, (site_id,))]
        parent = await self._db.fetch_one(_SITE_PARENT, (site_id,))
        record: dict[str, object] = {}
        if aliases:
            record["aliases"] = aliases
        # Every address, not the first one: a site can be found at several, and this is where the
        # page's list comes from.
        links = await self.site_links(site_id)
        if links:
            record["links"] = links
        if parent is not None:
            record["parent"] = str(parent["name"])
            # Beside the name, never instead of it. The value of the field IS the name: it is
            # what is typed, what is completed, what an import sends and what is saved, and this
            # is the address the drawn name links to. A screen with one and not the other either
            # cannot draw the row or cannot open it.
            record["parent_id"] = str(parent["id"])
        return record

    async def site_aliases(self, site_id: str) -> list[str]:
        """A site's other names, in reading order."""
        rows = await self._db.fetch_all(_SITE_ALIASES, (site_id,))
        return [str(row["alias"]) for row in rows]

    async def add_site_alias(self, site_id: str, alias: str) -> bool:
        """One more name for a site. False when it already carries that one."""
        cleaned = alias.strip()
        if not cleaned:
            return False
        if cleaned.casefold() in {one.casefold() for one in await self.site_aliases(site_id)}:
            return False
        await self._write_shared(
            _INSERT_SITE_ALIAS,
            (new_id(), site_id, cleaned, sort_key(cleaned), self._now()),
        )
        return True

    async def set_site_record(
        self, site_id: str, *, aliases: Sequence[str], parent: str | None, actor: Actor
    ) -> None:
        """Write a site's other names and its parent, both replaced whole."""
        wanted = parent.strip() if parent else ""
        parent_id = None
        if wanted:
            # Never itself, and never a Site already part of it. Refused rather than dropped: a
            # parent quietly left off is the field looking as though it had worked.
            await self.refuse_a_site_loop(site_id, wanted)
            # The shared upsert, which is find-or-create under the same case-insensitive name every
            # other route uses, so typing a network that already exists attaches to it rather than
            # making a second one beside it. Made by the person who typed it, named where the save
            # says who that was, so the new Site reads "Created by you" rather than "somebody".
            parent_id = await ensure_site(self._db, wanted, made=_made_by(actor))
        now = self._now()
        named = await self._site_names([site_id])
        held = {
            str(row["alias"]).casefold(): row["added_at"]
            for row in await self._db.fetch_all(_SITE_ALIAS_MOMENTS, (site_id,))
        }
        was = await self._db.fetch_one(_SITE_PARENT, (site_id,))
        # What the save moved, and only that: the line names each field in it, so a field that
        # stayed as it was would be said as edited ("the parent site" on a Site that has none).
        kept = [one for one in aliases if one.strip()]
        moved: dict[str, object] = {}
        if {one.strip().casefold() for one in kept} != set(held):
            moved["aliases"] = kept
        if parent_id != (str(was["id"]) if was is not None else None):
            moved["parent"] = wanted or None
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            await connection.execute(_CLEAR_SITE_ALIASES, (site_id,))
            for alias in dict.fromkeys(one.strip() for one in aliases):
                if alias:
                    await connection.execute(
                        _INSERT_SITE_ALIAS,
                        (
                            new_id(),
                            site_id,
                            alias,
                            sort_key(alias),
                            # An alias that survives the save keeps the moment it arrived at. Only
                            # one that was not on the row before is new.
                            held.get(alias.casefold(), now) or now,
                        ),
                    )
            await connection.execute(_SET_SITE_PARENT, (parent_id, site_id))
            # Both lists are replaced whole, so what a save took away leaves nothing behind. The
            # payload says what each moved field ended up with; a save that moved nothing says
            # nothing.
            if moved:
                await record_event(
                    connection,
                    actor=actor,
                    verb="edited",
                    subject=Subject(kind="site", id=site_id, name=named.get(site_id)),
                    payload=json.dumps(moved),
                )

    async def refuse_a_site_loop(self, site_id: str, wanted: str) -> None:
        """Raise `SiteLoop` when filing this Site under `wanted` would make a loop. Writes nothing."""
        found = await self.site_named(clean_token_text(wanted).strip())
        if found is not None and await self._db.fetch_one(SITE_WITHIN, (found, site_id)):
            raise SiteLoop(wanted)
