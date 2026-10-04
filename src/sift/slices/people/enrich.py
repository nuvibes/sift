# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a person and a Site are read and written when a stash-box answers about them.

The kernel decides WHAT should be written, field by field, from the record registry and the rule
somebody chose for each field. This is the other half: how to get at the rows once that is settled.
Nothing here decides anything, and that separation is the point. A writer that started applying
rules of its own would be a second copy of them, free to disagree with the first.

Four kinds of field, and they land in four different places:

- **The promoted columns** (a birth date, a height) are columns on the row itself.
- **Aliases, links and tags** are their own tables, and a write ADDS. An import never removes what
  is already there, which is why none of them goes through the form's save: that one replaces the
  whole list, because a form sends the whole list.
- **Usernames** are joined to the person only where a file is already filed under them; every
  other address a stash-box lists is kept as a link on the person, and no Site or username is made
  for it (`PersonWriter._attach`).

The promoted columns are read and written inside ONE transaction, deliberately. The statement that
writes them names every column, so writing a partial record would blank every field it was not
given: the failure that reads as a successful import until somebody opens the page. Reading first
and writing the merge is what makes a partial write safe on top of a full-row statement, and doing
both under one lock is what stops two imports racing each other into a half-written row.
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

#: The record columns a person carries, by field key. Read from the pairing the service already
#: holds rather than written out again. A second list of column names beside the first is exactly
#: the drift that pairing exists to prevent.
_PERSON_COLUMNS = frozenset(key for key, _column in PROMOTED)

#: How a stash-box's username entry arrives: where it is, what it is called there, and its address.
#:
#: A mapping rather than three parallel lists, since the three belong together: a username without
#: the site it is on names nothing, and two lists that have to stay the same length are two lists
#: that will not.
#:
#: `site` is what Sift calls the site the ADDRESS is on (the adapter's `_accounts`), and blank when
#: the icon pack does not know the host: the library may still have a Site living there, which only
#: this side can ask (`_site_of_entry`).
USERNAME_KEYS = ("site", "handle", "url")


def _usernames(value: object) -> list[dict[str, str]]:
    """The username entries in a value, ignoring anything that is not one.

    A tolerant reader in front of a strict writer, and that is a shape worth being careful about:
    what it drops it drops silently. It is right here because the value comes off a stash-box's
    answer through a JSON round trip, where an entry missing its site is a fact about their data
    rather than a fault in this code, and one bad entry must not lose the other nine.
    """
    out: list[dict[str, str]] = []
    for one in value if isinstance(value, (list, tuple)) else ():
        if not isinstance(one, Mapping):
            continue
        site = str(one.get("site") or "").strip()
        name = str(one.get("handle") or "").strip()
        url = str(one.get("url") or "").strip()
        # A blank site is kept only with an address, because the address is then the whole of
        # what says where it belongs; with neither there is nothing to file it under or link to.
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
        """Everything a stash-box could offer about this person, as Sift holds it now.

        The three list fields are read from their own tables rather than from the row, because that
        is where they live, and a plan built against a person whose aliases came back empty would
        offer to write every alias they already have.
        """
        held = (await self.current_many([local_id])).get(local_id)
        # A person no longer there holds nothing, their four lists included.
        return (
            held if held is not None else {"aliases": [], "links": [], "tags": [], "accounts": []}
        )

    async def current_many(self, local_ids: Sequence[str]) -> dict[str, Mapping[str, object]]:
        """The same for many people, in five reads whatever their number. See `ReadsMany`.

        `current` is this with one id, so the survey and a single plan cannot read a person two ways.
        """
        return dict(await self._service.enrichment_view_of_people(local_ids))

    async def missing(self, values: Mapping[str, object]) -> tuple[Missing, ...]:
        """The tags and sites in these values that this library has never heard of.

        A person is not in this list. Applying a stash-box's answer about somebody is a write to a
        person who already exists. The one that would CREATE people is the bulk pass over files,
        and it is the file's writer that counts those.
        """
        unknown: list[Missing] = []
        for name in _names(values.get("tags")):
            if await self._naming.tag_named(name, creating=False) is None:
                unknown.append(Missing(name=name, kind=Subject.TAG.value))
        # A person's usernames make nothing: a username is joined only where a file is already
        # filed under it, and every other address is kept as a link (`_attach`). So no Site is
        # ever missing on their account.
        return tuple(dict.fromkeys(unknown))

    async def write(
        self, local_id: str, values: Mapping[str, object], *, creating: Creating, actor: Actor
    ) -> Mapping[str, int]:
        """Write the fields a plan decided on, and nothing else. Answers with what it wrote.

        The answer is what the count on a confirm screen is made of, and it is taken from what
        LANDED rather than from what was asked for. A run with no permission to invent can be handed
        a person's five new tags and write none of them; counting the ask would report five.

        AND HOW MANY ROWS each field gained, which is the same rule applied one level further down:
        four of a person's fields are lists, and "filled in their links" is the same sentence for
        one address and for nine. Counted from what landed, one row at a time, for the reason the
        keys are.
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

        # `ensure_alias` and not `add_alias`. The raising form is for somebody typing a name into
        # the box on a person's page, who is owed the answer that they already have it; nothing
        # here can read an answer. A box offering a spelling this person already holds in a
        # different case (the alias collation folds unaccented A-Z) would raise `DuplicateAlias`
        # out of this loop, up through the planner, and out of the batch job, stopping it partway
        # through the people it was asked about. The service is the one place that knows a
        # duplicate is not an error; both unattended writers go through it.
        #
        # And the field is counted from what LANDED, this function's own stated rule two docstrings
        # up: a person whose every alias and link is already held has written neither field.
        # `ensure_alias` and `add_link` both answer None for a row that was already there, so the
        # count is simply what came back.
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
        usernames = 0
        for entry in _usernames(values.get("accounts")):
            site = await self._site_of_entry(entry)
            if site is None:
                # On no site Sift knows: the address is a plain link on the person, exactly as a
                # reference database's page is, and counted with the links for the same reason.
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
        return written

    async def _site_of_entry(self, entry: Mapping[str, str]) -> str | None:
        """The Site a username entry goes under, or None when it belongs under no site at all.

        The adapter's answer where it gave one: the icon pack's name for the address's host. A
        blank one is asked again of the library itself (`catalog.site_for`), which knows the Sites
        that live at a host the pack has never heard of. None means neither knows it, and the
        caller keeps the address as a link.
        """
        if entry["site"]:
            return entry["site"]
        return await site_for(self._service.database, entry["url"]) if entry["url"] else None

    async def _attach(
        self, person_id: str, entry: Mapping[str, str], *, creating: Creating, actor: Actor
    ) -> str | None:
        """One username joined to this person where a file is filed under it, else their link.

        Answers "username", "link" (a link that is new on the person), or None (nothing landed).

        **AN ADDRESS ALONE MAKES NO USERNAME** (`catalog.filed_username`). A box lists the pages a
        person can be found at, and writing each as a Site and a username under it would grow a
        library thousands of usernames on no file, and Sites made for nothing but a model page. A
        username is joined only where the library already files something under it;
        every other address is kept as a link on the person, on the Site it belongs to where that
        Site exists, and nothing is made. `creating` is kept for the writer's shape: this path
        never makes a row, so there is nothing for it to permit.
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
            # The row exists, so this makes nothing: it fills the username's address where the row
            # has none, and never replaces one it holds (`seed_site_username`).
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
    """Reads and writes one site. The same three shapes a person has, minus the usernames."""

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
        """The tags and the parent Site this answer names that this library does not have yet.

        The parent is a Site like any other a box names, so it is invented only with the same
        permission and listed where that permission is asked for.
        """
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
        """The id of the Site this one belongs to, made where it is new and that is allowed.

        A person taking the box's parent on Reconcile made it, and it is theirs. Otherwise it is
        the box's answer: made through the naming seam, so the run that applied it records the box
        as its maker and links it to the box's own studio (`record_who_invented`).
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
        # Never a loop: a Site that is its own parent, or the parent of a Site already part of it,
        # is a cycle every reader walking up the parents would have to know. The same question the
        # form's save asks, so a box's answer and a typed one are refused by one rule. Dropped, not
        # raised: a box's opinion is a suggestion and a loop is one Sift cannot take.
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
        """Write what a plan decided, and answer with the fields that landed, and how many rows.

        The two list fields are counted from what LANDED, never from the ask: reported written
        whenever the plan named any, whether or not a row was new, they would break the shared rule
        `PersonWriter.write` next door follows. Both `add_site_link` and `add_site_alias` answer
        False for something this site already holds, so the number is simply what came back.
        """
        written: dict[str, int] = {}
        # Named one at a time rather than gathered by a loop over a list of keys. The three are the
        # whole of a site's record, and a key that only ever appears as a loop variable is a key
        # nothing reading this file, or checking it, can see is handled.
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
