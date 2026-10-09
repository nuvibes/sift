# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a person and a Site are read and written when a stash-box answers about them.

The kernel decides what to write; this only reaches the rows. Columns are read and written in one
transaction, since the statement names every column; aliases, links and tags only ever add; a
username is joined only where a file is already filed under it (`PersonWriter._attach`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sift.kernel.access.catalog import (
    by_sift,
    by_user,
    ensure_site,
    filed_username,
    seed_site_username,
    site_for,
)
from sift.kernel.enrichment import Creating, Missing, Naming, may_create
from sift.kernel.ledger import ACTOR_USER, Actor
from sift.kernel.log import get_logger
from sift.kernel.records import Subject
from sift.kernel.text import stripped_or_none
from sift.kernel.vocabulary import VIA_STASH
from sift.slices.people.service import PROMOTED, PeopleService, SiteLoop

log = get_logger(__name__)

#: The record columns a person carries, by field key, from the service's own pairing.
_PERSON_COLUMNS = frozenset(key for key, _column in PROMOTED)

#: How a stash-box's username entry arrives: its site, its name there, and its address. A blank
#: `site` is a host the icon pack does not know (`_site_of_entry`).
USERNAME_KEYS = ("site", "handle", "url")


def _usernames(value: object) -> list[dict[str, str]]:
    """The username entries in a value, ignoring anything that is not one, so one bad entry loses
    nothing else.
    """
    out: list[dict[str, str]] = []
    for one in value if isinstance(value, (list, tuple)) else ():
        if not isinstance(one, Mapping):
            continue
        site = str(one.get("site") or "").strip()
        name = str(one.get("handle") or "").strip()
        url = str(one.get("url") or "").strip()
        # A blank site is kept only with an address, which then says where it belongs.
        if not name or not (site or url):
            continue
        out.append({"site": site, "handle": name, "url": url})
    return out


def _names(value: object) -> list[str]:
    """A list field's entries as clean strings, blanks dropped."""
    if not isinstance(value, (list, tuple)):
        return []
    return [str(one).strip() for one in value if str(one).strip()]


class PersonWriter:
    """Reads and writes one person, for whoever is applying a stash-box's answer."""

    subject = Subject.PERSON

    def __init__(self, service: PeopleService, naming: Naming) -> None:
        self._service = service
        self._naming = naming

    async def current(self, local_id: str) -> Mapping[str, object]:
        """Everything a stash-box could offer about this person, as Sift holds it now, lists included."""
        held = (await self.current_many([local_id])).get(local_id)
        # A person no longer there holds nothing, their four lists included.
        return (
            held if held is not None else {"aliases": [], "links": [], "tags": [], "accounts": []}
        )

    async def current_many(self, local_ids: Sequence[str]) -> dict[str, Mapping[str, object]]:
        """The same for many people, in five reads whatever their number. See `ReadsMany`."""
        return dict(await self._service.enrichment_view_of_people(local_ids))

    async def missing(self, values: Mapping[str, object]) -> tuple[Missing, ...]:
        """The tags and sites in these values that this library has never heard of; never a person."""
        unknown: list[Missing] = []
        for name in _names(values.get("tags")):
            if await self._naming.tag_named(name, creating=False) is None:
                unknown.append(Missing(name=name, kind=Subject.TAG.value))
        # A person's usernames make nothing, so no Site is ever missing on their account.
        return tuple(dict.fromkeys(unknown))

    async def write(
        self, local_id: str, values: Mapping[str, object], *, creating: Creating, actor: Actor
    ) -> Mapping[str, int]:
        """Write the fields a plan decided on, and nothing else. Answers with what landed, not what was
        asked for, and how many rows each field gained.
        """
        written: dict[str, int] = {}
        columns = {key: value for key, value in values.items() if key in _PERSON_COLUMNS}
        name = stripped_or_none(values.get("name"))
        details = stripped_or_none(values.get("details"))
        if columns or name is not None or details is not None:
            await self._service.merge_person_record(
                local_id, name=name, details=details, columns=columns, actor=actor
            )
            written.update(dict.fromkeys(columns, 1))
            if name is not None:
                written["name"] = 1
            if details is not None:
                written["details"] = 1

        # `ensure_alias`, not `add_alias`: a spelling already held must not raise out of a batch.
        # Counted from what came back, which is None for a row already there.
        aliases = 0
        for alias in _names(values.get("aliases")):
            aliases += await self._service.ensure_alias(local_id, alias) is not None
        if aliases:
            written["aliases"] = aliases
        links = 0
        for url in _names(values.get("links")):
            links += await self._service.add_link(local_id, url) is not None
        if links:
            written["links"] = links
        await self._write_tags(local_id, values, written, creating=creating)
        await self._write_usernames(local_id, values, written, creating=creating, actor=actor)
        return written

    async def _write_tags(
        self,
        local_id: str,
        values: Mapping[str, object],
        written: dict[str, int],
        *,
        creating: Creating,
    ) -> None:
        """The tags of `write`, counted into `written` as they land."""
        tags = 0
        for name in _names(values.get("tags")):
            tag_id = await self._naming.tag_named(
                name, creating=may_create(creating, Subject.TAG.value, name)
            )
            if tag_id is not None:
                await self._service.tag_person(local_id, tag_id)
                tags += 1
        if tags:
            written["tags"] = tags

    async def _write_usernames(
        self,
        local_id: str,
        values: Mapping[str, object],
        written: dict[str, int],
        *,
        creating: Creating,
        actor: Actor,
    ) -> None:
        """The usernames of `write`, counted into `written`; an address on no site is a link."""
        usernames = 0
        for entry in _usernames(values.get("accounts")):
            site = await self._site_of_entry(entry)
            if site is None:
                # On no site Sift knows: kept as a link on the person, and counted with the links.
                if await self._service.add_link(local_id, entry["url"]) is not None:
                    written["links"] = written.get("links", 0) + 1
                continue
            landed = await self._attach(
                local_id, {**entry, "site": site}, creating=creating, actor=actor
            )
            if landed == "link":
                written["links"] = written.get("links", 0) + 1
            usernames += landed == "username"
        if usernames:
            written["accounts"] = usernames

    async def _site_of_entry(self, entry: Mapping[str, str]) -> str | None:
        """The Site a username entry goes under: the adapter's, else the library's
        (`catalog.site_for`), else None.
        """
        if entry["site"]:
            return entry["site"]
        return await site_for(self._service.database, entry["url"]) if entry["url"] else None

    async def _attach(
        self, person_id: str, entry: Mapping[str, str], *, creating: Creating, actor: Actor
    ) -> str | None:
        """One username joined to this person where a file is filed under it, else their link.

        Answers "username", "link" or None. An address alone makes no username
        (`catalog.filed_username`), so this never makes a row.
        """
        del creating
        site = entry["site"]
        found = await filed_username(self._service.database, site=site, name=entry["handle"])
        if found.username_id is None:
            if not entry["url"]:
                return None
            added = await self._service.add_link(person_id, entry["url"], site_id=found.site_id)
            return None if added is None else "link"
        if entry["url"]:
            # The row exists: this only fills a missing address (`seed_site_username`).
            await seed_site_username(
                self._service.database,
                site=found.site,
                name=found.username,
                url=entry["url"],
                made=by_sift(VIA_STASH),
            )
        await self._service.attach_username(found.username_id, person_id=person_id, actor=actor)
        log.info("enrich.username.attached", site=site)
        return "username"


class SiteWriter:
    """Reads and writes one site: a person's shapes, minus the usernames."""

    subject = Subject.SITE

    def __init__(self, service: PeopleService, naming: Naming) -> None:
        self._service = service
        self._naming = naming

    async def current(self, local_id: str) -> Mapping[str, object]:
        held = await self._service.site_record(local_id)
        held["links"] = list(await self._service.site_links(local_id))
        held["aliases"] = list(await self._service.site_aliases(local_id))
        held["tags"] = [str(row["name"]) for row in await self._service.tags_of_site(local_id)]
        return held

    async def missing(self, values: Mapping[str, object]) -> tuple[Missing, ...]:
        """The tags and the parent Site this answer names that this library does not have yet."""
        unknown = [
            Missing(name=name, kind=Subject.TAG.value)
            for name in _names(values.get("tags"))
            if await self._naming.tag_named(name, creating=False) is None
        ]
        parent = stripped_or_none(values.get("parent"))
        if parent and await self._naming.site_named(parent, creating=False) is None:
            unknown.append(Missing(name=parent, kind=Subject.SITE.value))
        return tuple(dict.fromkeys(unknown))

    async def _parent(
        self, local_id: str, parent: str | None, *, creating: Creating, actor: Actor
    ) -> str | None:
        """The id of the Site this one belongs to, made where it is new and that is allowed
        (`record_who_invented`).
        """
        if not parent:
            return None
        parent_id: str | None
        if actor.kind == ACTOR_USER and actor.id:
            parent_id = await ensure_site(self._service.database, parent, made=by_user(actor.id))
        else:
            parent_id = await self._naming.site_named(
                parent, creating=may_create(creating, Subject.SITE.value, parent)
            )
        # Never a loop of parents: the form's own rule; a box's loop is dropped, not raised.
        if parent_id == local_id:
            return None
        try:
            await self._service.refuse_a_site_loop(local_id, parent)
        except SiteLoop:
            return None
        return parent_id

    async def write(
        self, local_id: str, values: Mapping[str, object], *, creating: Creating, actor: Actor
    ) -> Mapping[str, int]:
        """Write what a plan decided; answer with the fields that landed, and how many rows."""
        written: dict[str, int] = {}
        # Named one at a time, so each of the three keys is visibly handled.
        name = stripped_or_none(values.get("name"))
        details = stripped_or_none(values.get("details"))
        parent_id = await self._parent(
            local_id,
            stripped_or_none(values.get("parent")),
            creating=creating,
            actor=actor,
        )
        await self._service.merge_site_record(
            local_id, name=name, details=details, parent_id=parent_id, actor=actor
        )
        written.update(
            {
                key: 1
                for key, value in (
                    ("name", name),
                    ("details", details),
                    ("parent", parent_id),
                )
                if value is not None
            }
        )
        links = 0
        for url in _names(values.get("links")):
            links += await self._service.add_site_link(local_id, url)
        if links:
            written["links"] = links
        aliases = 0
        for alias in _names(values.get("aliases")):
            aliases += await self._service.add_site_alias(local_id, alias)
        if aliases:
            written["aliases"] = aliases
        tags = 0
        for name in _names(values.get("tags")):
            tag_id = await self._naming.tag_named(
                name, creating=may_create(creating, Subject.TAG.value, name)
            )
            if tag_id is not None:
                await self._service.tag_site(local_id, tag_id)
                tags += 1
        if tags:
            written["tags"] = tags
        return written
