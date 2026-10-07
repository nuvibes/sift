# SPDX-License-Identifier: AGPL-3.0-or-later
"""How a person and a site are read and written when a stash-box answers about them.

The kernel decides WHAT is written, field by field, from the registry and the rule somebody chose
for each. These two are the other half: how to get at the rows once that is settled. Nothing here
decides anything, and that separation is the point.

Four kinds of field land in four places, and the ones with consequences are the list ones. An
import ADDS to an alias list; the form's save replaces it, because a form sends the whole list. A
writer that used the form's statement would delete every alias a stash-box did not happen to carry.

And usernames: an address a box lists joins the person to a username only where a file is already
filed under it; every other address is kept as a link on the person, and nothing is made for it.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from sift.kernel.access import Repository
from sift.kernel.access.catalog import (
    FiledAt,
    by_sift,
    ensure_site,
    filed_username,
    seed_site_username,
)
from sift.kernel.db import Database
from sift.kernel.enrichment import Missing
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor
from sift.kernel.records import Subject
from sift.slices.people.enrich import PersonWriter, SiteWriter
from sift.slices.people.service import PeopleService

pytestmark = pytest.mark.anyio


class _Naming:
    """Turns a name into a row. `known` is what this library already has."""

    def __init__(self, known: set[str] | None = None) -> None:
        self.known = set(known or ())
        self.made: list[str] = []
        self.marked: list[str] = []
        self.made_by: list[tuple[str, str, str]] = []

    async def _named(self, name: str, *, creating: bool) -> str | None:
        if name in self.known:
            return f"id-{name}"
        if not creating:
            return None
        self.made.append(name)
        self.known.add(name)
        return f"id-{name}"

    async def person_named(self, name: str, *, creating: bool) -> str | None:
        return await self._named(name, creating=creating)

    async def site_named(
        self, name: str, *, creating: bool, address: str | None = None
    ) -> str | None:
        return await self._named(name, creating=creating)

    async def tag_named(self, name: str, *, creating: bool) -> str | None:
        return await self._named(name, creating=creating)

    async def mark_pmv_creator(self, person_id: str) -> None:  # pragma: no cover (see below)
        """Never reached from here, and stood in for all the same.

        Who MAKES the edits is a claim a stash-box makes about a FILE it recognised, so the file's
        writer is the only one that says it. Leaving the verb off this double would have made that a
        fact about the double rather than about the writers, and mypy would have said so first.
        """
        self.marked.append(person_id)

    async def mark_created_by_box(  # pragma: no cover (see `mark_pmv_creator` above)
        self, kind: str, local_id: str, source_id: str
    ) -> None:
        """Never reached from here either, and stood in for the same reason.

        Which box INVENTED a row is learned while applying an answer about a FILE, so the file's
        writer is the only one that says it. The double carries the verb so that stays a fact about
        the writers rather than about the double.
        """
        self.made_by.append((kind, local_id, source_id))


def _list(held: Mapping[str, object], key: str) -> list[object]:
    """One list field out of a record. The record's values are `object`, because a record holds
    whatever the registry says a field is, so reading one for a comparison narrows it here rather
    than at every assertion."""
    value = held[key]
    assert isinstance(value, list)
    return value


def _texts(held: Mapping[str, object], key: str) -> list[str]:
    return sorted(str(one) for one in _list(held, key))


@pytest.fixture
async def service(temp_db: Database, access: Repository) -> PeopleService:
    await temp_db.initialize_schema()
    return PeopleService(temp_db, access)


async def _a_person(temp_db: Database, name: str = "Jane") -> str:
    person = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (person, name)
    )
    return person


async def _address(temp_db: Database, site: str, url: str) -> None:
    """A Site's address, which is the first of its links (`sites.SITE_ADDRESS`)."""
    await temp_db.execute(
        "INSERT INTO site_links (id, site_id, url, label, created_at) VALUES (?, ?, ?, NULL, 0)",
        (new_id(), site, url),
    )


async def _a_site(temp_db: Database, name: str = "SomeSite") -> str:
    site = new_id()
    await temp_db.execute("INSERT INTO sites (id, name) VALUES (?, ?)", (site, name))
    return site


async def _a_tag(temp_db: Database, tag_id: str, name: str) -> None:
    await temp_db.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, ?, 0)", (tag_id, name)
    )


async def _a_filed_username(
    temp_db: Database, site: str, handle: str, url: str | None = None
) -> str:
    """A username on a Site with one file filed under it: the only kind an address joins."""
    _, username_id = await seed_site_username(
        temp_db, site=site, name=handle, url=url, made=by_sift("stash")
    )
    asset = new_id()
    await temp_db.execute(
        "INSERT INTO assets (id, identity, media_type, width, height, duration_ms, size_bytes,"
        " original_filename, added_at) VALUES (?, ?, 'video', 1920, 1080, 4000, 1, 'clip.mp4', 0)",
        (asset, f"digest-{asset}"),
    )
    await temp_db.execute(
        "INSERT INTO asset_usernames (asset_id, username_id) VALUES (?, ?)", (asset, username_id)
    )
    return username_id


# --- a person ------------------------------------------------------------------------------------


async def test_a_person_reads_their_lists_from_the_tables_the_lists_live_in(
    service: PeopleService, temp_db: Database
) -> None:
    """A plan built against a person whose aliases came back empty would offer to write every alias
    they already have."""
    person = await _a_person(temp_db)
    await service.add_alias(person, "JD")
    await service.add_link(person, "https://example.test/jane")
    writer = PersonWriter(service, _Naming())

    held = await writer.current(person)

    assert held["aliases"] == ["JD"]
    assert held["links"] == ["https://example.test/jane"]
    assert held["tags"] == []
    assert held["accounts"] == []


async def test_many_people_read_together_are_each_what_their_own_reads_say(
    service: PeopleService, temp_db: Database
) -> None:
    """The survey reads every linked person in five reads; a plan against one person reads them
    through the one-person statements. The two must agree field for field and in list order, or a
    disagreement would show on the panel and not on the wall filter."""
    first = await _a_person(temp_db, "Esme Wrenfield")
    second = await _a_person(temp_db, "Bryn Calloway")
    alone = await _a_person(temp_db, "Cassia Lynn")
    await service.add_alias(first, "Wren")
    await service.add_alias(first, "Elina Sorrel")
    await service.add_alias(second, "Calloway")
    await service.add_link(first, "https://example.test/b")
    await service.add_link(first, "https://example.test/a")
    await _a_tag(temp_db, "tag-1", "Outdoor")
    await _a_tag(temp_db, "tag-2", "Beach")
    await service.tag_person(first, "tag-1")
    await service.tag_person(first, "tag-2")
    await service.tag_person(second, "tag-2")
    writer = PersonWriter(service, _Naming({"SomeSite"}))
    await writer.write(
        second,
        {"accounts": [{"site": "SomeSite", "handle": "bryncalloway", "url": "https://x.test/b"}]},
        creating=False,
        actor=Actor.sift("stash"),
    )

    many = await writer.current_many([first, second, alone, "gone"])

    for person in (first, second, alone):
        one = dict(await service.person_record(person))
        one["aliases"] = [row.alias for row in await service.aliases_of(person)]
        one["links"] = [row.url for row in await service.links_of(person)]
        one["tags"] = [str(row["name"]) for row in await service.tags_of_person(person)]
        # The accounts are the one-person read's own: the batch and the one read must agree on
        # them, and the rest of the record is checked field by field above.
        one["accounts"] = (await writer.current(person))["accounts"]
        assert many[person] == one
        assert await writer.current(person) == one
    assert "gone" not in many
    assert _texts(many[first], "tags") == ["Beach", "Outdoor"]


async def test_a_persons_usernames_are_read_back_as_the_site_the_name_and_the_address(
    service: PeopleService, temp_db: Database
) -> None:
    person = await _a_person(temp_db)
    await _a_filed_username(temp_db, "SomeSite", "esmewrenfield", "https://x.test/k")
    writer = PersonWriter(service, _Naming({"SomeSite"}))
    await writer.write(
        person,
        {"accounts": [{"site": "SomeSite", "handle": "esmewrenfield", "url": "https://x.test/k"}]},
        creating=False,
        actor=Actor.sift("stash"),
    )

    held = await writer.current(person)

    assert held["accounts"] == [
        {"site": "SomeSite", "handle": "esmewrenfield", "url": "https://x.test/k"}
    ]


async def test_an_address_on_no_site_sift_can_name_is_kept_as_a_link_and_makes_nothing(
    service: PeopleService, temp_db: Database
) -> None:
    """A box's page with no Site name is no username anywhere: it stays a link on the person. The
    catalog's own answer for no Site is the same: nothing filed, nothing to join."""
    assert await filed_username(temp_db, site="  ", name="esmewrenfield") == FiledAt(None, None)
    person = await _a_person(temp_db)
    writer = PersonWriter(service, _Naming())

    await writer.write(
        person,
        {"accounts": [{"site": "  ", "handle": "esmewrenfield", "url": "https://x.test/k"}]},
        creating=False,
        actor=Actor.sift("stash"),
    )

    links = await temp_db.fetch_all(
        "SELECT url, site_id FROM people_links WHERE person_id = ?", (person,)
    )
    assert [dict(one) for one in links] == [{"url": "https://x.test/k", "site_id": None}]
    assert await temp_db.fetch_one("SELECT 1 FROM usernames") is None
    assert await temp_db.fetch_one("SELECT 1 FROM sites") is None


async def test_a_person_is_never_among_the_names_an_answer_would_invent(
    service: PeopleService,
) -> None:
    """Applying a stash-box's answer ABOUT somebody is a write to a person who already exists. The
    pass that would create people is the bulk one over files, and the file's writer counts those."""
    writer = PersonWriter(service, _Naming())

    unknown = await writer.missing({"name": "Somebody Else", "tags": ["blonde"]})

    assert unknown == (Missing(name="blonde", kind="tag"),)


async def test_a_persons_usernames_never_ask_for_a_site(
    service: PeopleService,
) -> None:
    """A username is joined only where a file is filed under it, and an address is otherwise a
    link: neither makes a Site, so none is ever missing on a person's account."""
    writer = PersonWriter(service, _Naming({"Known"}))

    unknown = await writer.missing(
        {
            "accounts": [
                {"site": "Known", "handle": "a"},
                {"site": "New", "handle": "b"},
                {"site": "New", "handle": "c"},
            ]
        }
    )

    assert unknown == ()


async def test_a_record_field_is_written_and_the_rest_of_the_record_is_left_alone(
    service: PeopleService, temp_db: Database
) -> None:
    person = await _a_person(temp_db)
    writer = PersonWriter(service, _Naming())
    await writer.write(
        person,
        {"birth_date": "1990-01-01", "country": "US"},
        creating=False,
        actor=Actor.sift("stash"),
    )

    await writer.write(person, {"country": "GB"}, creating=False, actor=Actor.sift("stash"))

    held = await writer.current(person)
    assert (held["birth_date"], held["country"]) == ("1990-01-01", "GB")


async def test_an_alias_is_added_rather_than_replacing_what_is_there(
    service: PeopleService, temp_db: Database
) -> None:
    """An import never removes what is already there. The form's save replaces the whole list,
    because a form sends the whole list, and using that statement here would delete every alias a
    stash-box did not happen to carry."""
    person = await _a_person(temp_db)
    await service.add_alias(person, "JD")
    writer = PersonWriter(service, _Naming())

    await writer.write(person, {"aliases": ["Jane Doe"]}, creating=False, actor=Actor.sift("stash"))

    held = await writer.current(person)
    assert _texts(held, "aliases") == ["JD", "Jane Doe"]


async def test_an_alias_this_person_already_answers_to_is_not_an_error(
    service: PeopleService, temp_db: Database
) -> None:
    """A stash-box repeating a name Sift already holds must not take the write down.

    The alias collation folds unaccented A-Z, so "JD" and "jd" on one person are one alias, and
    the planner cannot see that, because it compares the strings it was given. So a box that files
    somebody under a different case offers a spelling that looks new and inserts as a duplicate,
    every single time that person is enriched.

    Raised out of this writer, it would go up through the planner and out of the batch job,
    stopping it where it stood with the rest of the people never asked about. The service knows a
    duplicate is not an error (`ensure_alias`), and this goes through it.
    """
    person = await _a_person(temp_db)
    await service.add_alias(person, "JD")
    writer = PersonWriter(service, _Naming())

    written = await writer.write(
        person, {"aliases": ["jd", "Jane Doe"]}, creating=False, actor=Actor.sift("stash")
    )

    held = await writer.current(person)
    assert _texts(held, "aliases") == ["JD", "Jane Doe"]
    # And the field is still reported as written, because one of the two landed.
    assert "aliases" in written


async def test_a_list_field_whose_every_entry_was_already_held_is_not_counted_as_written(
    service: PeopleService, temp_db: Database
) -> None:
    """`write` answers with what LANDED, its own stated rule, for the two list fields too:
    counted from what was ASKED for, a person the box agrees with entirely would report both as
    written and the log line would say so."""
    person = await _a_person(temp_db)
    await service.add_alias(person, "JD")
    await service.add_link(person, "https://example.test/jane")
    writer = PersonWriter(service, _Naming())

    written = await writer.write(
        person,
        {"aliases": ["JD"], "links": ["https://example.test/jane"]},
        creating=False,
        actor=Actor.sift("stash"),
    )

    assert written == {}


async def test_a_tag_this_library_does_not_have_is_skipped_unless_creating_was_asked_for(
    service: PeopleService, temp_db: Database
) -> None:
    person = await _a_person(temp_db)
    naming = _Naming()
    writer = PersonWriter(service, naming)

    await writer.write(person, {"tags": ["blonde"]}, creating=False, actor=Actor.sift("stash"))

    assert naming.made == []
    assert (await writer.current(person))["tags"] == []


async def test_a_tag_is_made_and_put_on_when_creating_was_asked_for(
    service: PeopleService, temp_db: Database
) -> None:
    person = await _a_person(temp_db)
    tag = new_id()
    await _a_tag(temp_db, tag, "blonde")

    class _Real(_Naming):
        async def tag_named(self, name: str, *, creating: bool) -> str | None:
            self.made.append(name)
            return tag

    naming = _Real()
    writer = PersonWriter(service, naming)

    await writer.write(person, {"tags": ["blonde"]}, creating=True, actor=Actor.sift("stash"))

    assert naming.made == ["blonde"]
    assert (await writer.current(person))["tags"] == ["blonde"]


async def test_a_username_is_not_made_under_a_site_the_run_may_not_invent(
    service: PeopleService, temp_db: Database
) -> None:
    """The site is looked up BEFORE anything is written, so a run that may not create rows does not
    make a Username under a Site it just invented."""
    person = await _a_person(temp_db)
    writer = PersonWriter(service, _Naming())

    await writer.write(
        person,
        {"accounts": [{"site": "New Site", "handle": "esmewrenfield"}]},
        creating=False,
        actor=Actor.sift("stash"),
    )

    assert (await writer.current(person))["accounts"] == []


async def test_a_username_entry_missing_its_site_or_its_name_is_dropped_and_the_rest_kept(
    service: PeopleService, temp_db: Database
) -> None:
    """A tolerant reader in front of a strict writer. An entry missing its site is a fact about a
    stash-box's data rather than a fault here, and one bad entry must not lose the other nine."""
    person = await _a_person(temp_db)
    await _a_filed_username(temp_db, "SomeSite", "esmewrenfield")
    writer = PersonWriter(service, _Naming({"SomeSite"}))

    await writer.write(
        person,
        {
            "accounts": [
                "not an entry at all",
                {"handle": "nobody knows where"},
                {"site": "SomeSite"},
                {"site": "SomeSite", "handle": "esmewrenfield"},
            ]
        },
        creating=False,
        actor=Actor.sift("stash"),
    )

    held = await writer.current(person)
    assert [str(one["handle"]) for one in _list(held, "accounts") if isinstance(one, dict)] == [
        "esmewrenfield"
    ]


async def test_an_address_on_a_host_this_library_lives_at_is_a_username_there(
    service: PeopleService, temp_db: Database
) -> None:
    """A blank site is the adapter saying the icon pack does not know the host. The library may:
    a Site whose own address is on that host is where the username goes."""
    person = await _a_person(temp_db)
    site = new_id()
    await temp_db.execute("INSERT INTO sites (id, name) VALUES (?, ?)", (site, "Clipvault"))
    await _address(temp_db, site, "https://clipvault.example.test")
    await _a_filed_username(temp_db, "Clipvault", "esmewren")
    naming = _Naming({"Clipvault"})
    writer = PersonWriter(service, naming)
    values = {
        "accounts": [
            {"site": "", "handle": "esmewren", "url": "https://www.clipvault.example.test/esmewren"}
        ]
    }

    assert await writer.missing(values) == ()
    written = await writer.write(person, values, creating=False, actor=Actor.sift("stash"))

    assert written == {"accounts": 1}
    held = await writer.current(person)
    assert held["accounts"] == [
        {
            "site": "Clipvault",
            "handle": "esmewren",
            "url": "https://www.clipvault.example.test/esmewren",
        }
    ]
    assert naming.made == []


async def test_an_address_alone_makes_no_username_and_is_kept_as_a_link_on_its_site(
    service: PeopleService, temp_db: Database
) -> None:
    """THE RULE: a username on a Site exists only when a file is filed under it. A box listing a
    person's model page on a Site the library knows, with nothing filed under that name, gives
    the person a link on that Site; no username is made, and no Site either."""
    person = await _a_person(temp_db)
    site = await _a_site(temp_db, "Clipvault")
    naming = _Naming({"Clipvault"})
    writer = PersonWriter(service, naming)
    url = "https://clipvault.example.test/model/esmewren"

    written = await writer.write(
        person,
        {"accounts": [{"site": "Clipvault", "handle": "esmewren", "url": url}]},
        creating=True,
        actor=Actor.sift("stash"),
    )

    assert written == {"links": 1}
    assert (await writer.current(person))["accounts"] == []
    assert await temp_db.fetch_all("SELECT id FROM usernames") == []
    links = await temp_db.fetch_all("SELECT url, site_id FROM people_links")
    assert [(one["url"], one["site_id"]) for one in links] == [(url, site)]
    assert naming.made == []


async def test_an_address_on_a_site_nobody_has_made_makes_no_site(
    service: PeopleService, temp_db: Database
) -> None:
    """The box names a Site this library has never had: still only a link, and no Site."""
    person = await _a_person(temp_db)
    naming = _Naming()
    writer = PersonWriter(service, naming)

    written = await writer.write(
        person,
        {
            "accounts": [
                {"site": "New Site", "handle": "esmewren", "url": "https://n.test/esmewren"}
            ]
        },
        creating=True,
        actor=Actor.sift("stash"),
    )

    assert written == {"links": 1}
    assert naming.made == []
    assert await temp_db.fetch_all("SELECT id FROM sites") == []


async def test_an_address_on_no_site_anybody_knows_is_a_link_and_makes_no_site(
    service: PeopleService, temp_db: Database
) -> None:
    """A person's own domain, an agency: a plain link on the person, counted with the links,
    never a Site called by the box's word for the kind of link."""
    person = await _a_person(temp_db)
    naming = _Naming()
    writer = PersonWriter(service, naming)
    values = {
        "accounts": [
            {"site": "", "handle": "esmewren", "url": "https://agency.example.test/talent/esmewren"}
        ]
    }

    assert await writer.missing(values) == ()
    written = await writer.write(person, values, creating=True, actor=Actor.sift("stash"))

    assert written == {"links": 1}
    held = await writer.current(person)
    assert held["accounts"] == []
    assert held["links"] == ["https://agency.example.test/talent/esmewren"]
    assert naming.made == []
    assert await temp_db.fetch_all("SELECT id FROM sites") == []


@pytest.mark.parametrize(
    ("name", "address"),
    [
        # A PAGE on the host is not the Site's own address: a box lists a studio's page on another
        # site among its links, and matching it would file that site's addresses under the studio.
        ("Clipvault", "https://agency.example.test/about/clipvault"),
        # A label is never a site, wherever it says it lives.
        ("Modeling Agency", "https://agency.example.test"),
    ],
)
async def test_a_page_about_a_site_or_a_label_is_not_where_a_username_goes(
    service: PeopleService, temp_db: Database, name: str, address: str
) -> None:
    person = await _a_person(temp_db)
    site = new_id()
    await temp_db.execute("INSERT INTO sites (id, name) VALUES (?, ?)", (site, name))
    await _address(temp_db, site, address)
    writer = PersonWriter(service, _Naming({name}))
    url = "https://agency.example.test/talent/esmewren"

    written = await writer.write(
        person,
        {"accounts": [{"site": "", "handle": "esmewren", "url": url}]},
        creating=True,
        actor=Actor.sift("stash"),
    )

    assert written == {"links": 1}
    assert (await writer.current(person))["accounts"] == []


async def test_a_list_field_that_is_not_a_list_writes_nothing(
    service: PeopleService, temp_db: Database
) -> None:
    person = await _a_person(temp_db)
    writer = PersonWriter(service, _Naming())

    await writer.write(
        person,
        {"aliases": "JD", "links": 7, "accounts": "nowhere"},
        creating=False,
        actor=Actor.sift("stash"),
    )

    held = await writer.current(person)
    assert (held["aliases"], held["links"], held["accounts"]) == ([], [], [])


# --- a site --------------------------------------------------------------------------------------


async def test_a_site_reads_its_lists_from_their_own_tables(
    service: PeopleService, temp_db: Database
) -> None:
    site = await _a_site(temp_db)
    await service.add_site_link(site, "https://example.test/site")
    await service.add_site_alias(site, "Elsewhere")
    writer = SiteWriter(service, _Naming())

    held = await writer.current(site)

    assert held["links"] == ["https://example.test/site"]
    assert held["aliases"] == ["Elsewhere"]
    assert held["tags"] == []


async def test_a_sites_tags_are_the_only_thing_it_could_have_to_invent(
    service: PeopleService,
) -> None:
    writer = SiteWriter(service, _Naming({"known"}))

    assert await writer.missing({"tags": ["known", "new", "new"]}) == (
        Missing(name="new", kind="tag"),
    )


async def test_a_sites_record_is_written_and_its_lists_are_added_to(
    service: PeopleService, temp_db: Database
) -> None:
    site = await _a_site(temp_db)
    await service.add_site_alias(site, "Elsewhere")
    writer = SiteWriter(service, _Naming())

    await writer.write(
        site,
        {"details": "A note.", "aliases": ["Otherwhere"], "links": ["https://example.test/site"]},
        creating=False,
        actor=Actor.sift("stash"),
    )

    held = await writer.current(site)
    assert _texts(held, "aliases") == ["Elsewhere", "Otherwhere"]
    assert held["links"] == ["https://example.test/site"]
    # The notes are not a record field: they are the site's own column, and the record is what a
    # stash-box could have an opinion on. Read from the row itself.
    row = await temp_db.fetch_one("SELECT notes FROM sites WHERE id = ?", (site,))
    assert row is not None
    assert row["notes"] == "A note."


async def test_a_persons_addresses_are_added_rather_than_replacing_what_is_there(
    service: PeopleService, temp_db: Database
) -> None:
    person = await _a_person(temp_db)
    await service.add_link(person, "https://example.test/one")
    writer = PersonWriter(service, _Naming())

    await writer.write(
        person, {"links": ["https://example.test/two"]}, creating=False, actor=Actor.sift("stash")
    )

    held = await writer.current(person)
    assert _texts(held, "links") == [
        "https://example.test/one",
        "https://example.test/two",
    ]


async def test_a_tag_a_person_already_carries_is_not_counted_as_one_to_invent(
    service: PeopleService, temp_db: Database
) -> None:
    """The count is what the confirm button shows, so a name this library already has must not be
    in it, twice over for a name that turns up under two fields."""
    writer = PersonWriter(service, _Naming({"blonde"}))

    assert await writer.missing({"tags": ["blonde", "blonde"]}) == ()


async def test_a_sites_tag_is_made_and_put_on_when_creating_was_asked_for(
    service: PeopleService, temp_db: Database
) -> None:
    site = await _a_site(temp_db)
    tag = new_id()
    await _a_tag(temp_db, tag, "adult")

    class _Real(_Naming):
        async def tag_named(self, name: str, *, creating: bool) -> str | None:
            self.made.append(name)
            return tag

    writer = SiteWriter(service, _Real())

    await writer.write(site, {"tags": ["adult"]}, creating=True, actor=Actor.sift("stash"))

    assert (await writer.current(site))["tags"] == ["adult"]


async def test_a_sites_tag_is_skipped_unless_creating_was_asked_for(
    service: PeopleService, temp_db: Database
) -> None:
    site = await _a_site(temp_db)
    naming = _Naming()
    writer = SiteWriter(service, naming)

    await writer.write(site, {"tags": ["adult"]}, creating=False, actor=Actor.sift("stash"))

    assert (await writer.current(site))["tags"] == []


class _Minting(_Naming):
    """Makes a real Site when creating is allowed, as the wiring's naming does: Sift's pass word,
    which `record_who_invented` then turns into the box's."""

    def __init__(self, database: Database, known: set[str] | None = None) -> None:
        super().__init__(known)
        self._db = database

    async def site_named(
        self, name: str, *, creating: bool, address: str | None = None
    ) -> str | None:
        row = await self._db.fetch_one("SELECT id FROM sites WHERE name = ?", (name,))
        if row is not None:
            return str(row["id"])
        if not creating:
            return None
        self.made.append(name)
        return await ensure_site(self._db, name, made=by_sift("stash"))


async def _maker_of(temp_db: Database, name: str) -> tuple[object, object]:
    row = await temp_db.fetch_one(
        "SELECT created_by_kind, created_by_user_id FROM sites WHERE name = ?", (name,)
    )
    assert row is not None
    return row["created_by_kind"], row["created_by_user_id"]


async def test_a_parent_a_box_names_is_one_more_site_it_could_invent(
    service: PeopleService,
) -> None:
    """Listed where the permission is asked for, like any Site a box names."""
    writer = SiteWriter(service, _Naming({"Northlight Group"}))

    assert await writer.missing({"parent": "Harbour Network"}) == (
        Missing(name="Harbour Network", kind="site"),
    )
    assert await writer.missing({"parent": "Northlight Group"}) == ()


async def test_a_parent_a_box_names_is_made_through_the_naming_and_only_when_allowed(
    service: PeopleService, temp_db: Database
) -> None:
    """Never made as if somebody typed it: through the naming seam, so the run records the box as
    its maker, and not at all where inventing Sites was not allowed."""
    site = await _a_site(temp_db)
    naming = _Minting(temp_db)
    writer = SiteWriter(service, naming)

    refused = await writer.write(
        site, {"parent": "Harbour Network"}, creating=False, actor=Actor.box("b1")
    )
    assert "parent" not in refused
    assert "parent" not in await service.site_record(site)

    written = await writer.write(
        site,
        {"parent": "Harbour Network"},
        creating=frozenset({("site", "Harbour Network")}),
        actor=Actor.box("b1"),
    )
    assert written == {"parent": 1}
    assert naming.made == ["Harbour Network"]
    assert (await service.site_record(site))["parent"] == "Harbour Network"
    assert await _maker_of(temp_db, "Harbour Network") == ("sift", None)


async def test_a_parent_a_box_names_that_would_make_a_loop_is_dropped(
    service: PeopleService, temp_db: Database
) -> None:
    """A box naming a Site's own child as its parent is refused by the rule the form's save uses,
    and the answer is dropped rather than raised: nothing else the box said is lost."""
    parent = await _a_site(temp_db, "Harbour Network")
    child = await _a_site(temp_db, "Harbour Films")
    await temp_db.execute("UPDATE sites SET parent_id = ? WHERE id = ?", (parent, child))
    writer = SiteWriter(service, _Minting(temp_db))

    written = await writer.write(
        parent,
        {"parent": "Harbour Films"},
        creating=frozenset({("site", "Harbour Films")}),
        actor=Actor.box("b1"),
    )
    assert written == {}
    assert "parent" not in await service.site_record(parent)

    itself = await writer.write(
        parent,
        {"parent": "Harbour Network"},
        creating=frozenset({("site", "Harbour Network")}),
        actor=Actor.box("b1"),
    )
    assert itself == {}
    assert "parent" not in await service.site_record(parent)


async def test_a_parent_somebody_takes_on_reconcile_is_theirs(
    service: PeopleService, temp_db: Database
) -> None:
    """A person choosing the box's parent made it, and the row says who."""
    site = await _a_site(temp_db)
    writer = SiteWriter(service, _Minting(temp_db))

    await writer.write(site, {"parent": "Quill House"}, creating=False, actor=Actor.user("u1"))

    assert await _maker_of(temp_db, "Quill House") == ("user", "u1")


async def test_each_writer_says_which_subject_it_owns(service: PeopleService) -> None:
    """One writer per subject, and which one it is has to be readable without running it."""
    assert PersonWriter(service, _Naming()).subject is Subject.PERSON
    assert SiteWriter(service, _Naming()).subject is Subject.SITE


async def test_a_name_and_notes_are_counted_beside_the_record_columns(
    service: PeopleService, temp_db: Database
) -> None:
    """The two fields that are not record columns still have to appear in what was written.

    They live on the person's own row and are written by their own statement, so nothing in the
    column list names them, and the count on a confirm screen is made of this answer. A writer
    that wrote a name and did not say so would report one field where three landed.
    """
    person = await _a_person(temp_db)
    writer = PersonWriter(service, _Naming())

    written = await writer.write(
        person,
        {"name": "Jane Doe", "details": "A note.", "country": "US"},
        creating=False,
        actor=Actor.sift("stash"),
    )

    assert sorted(written) == ["country", "details", "name"]
    held = await writer.current(person)
    assert (held["name"], held["details"], held["country"]) == ("Jane Doe", "A note.", "US")


async def test_two_tags_landing_on_a_person_are_still_one_field_written(
    service: PeopleService, temp_db: Database
) -> None:
    """`written` names FIELDS and not rows. Both tags go on; "tags" is said once, because the
    confirm screen counts the fields a plan decided rather than the rows underneath them."""
    person = await _a_person(temp_db)
    ids = {"blonde": new_id(), "freckled": new_id()}
    for name, tag_id in ids.items():
        await _a_tag(temp_db, tag_id, name)

    class _Real(_Naming):
        async def tag_named(self, name: str, *, creating: bool) -> str | None:
            return ids.get(name)

    writer = PersonWriter(service, _Real())

    written = await writer.write(
        person, {"tags": list(ids)}, creating=True, actor=Actor.sift("stash")
    )

    # One FIELD, counted twice: the key is what the confirm toast counts and the number is what
    # the History line says: "2 tags" rather than "tags".
    assert written == {"tags": 2}
    assert _texts(await writer.current(person), "tags") == ["blonde", "freckled"]


async def test_two_tags_landing_on_a_site_are_still_one_field_written(
    service: PeopleService, temp_db: Database
) -> None:
    """The same for a site, and asserted separately because it is a second copy of the loop."""
    site = await _a_site(temp_db)
    ids = {"adult": new_id(), "amateur": new_id()}
    for name, tag_id in ids.items():
        await _a_tag(temp_db, tag_id, name)

    class _Real(_Naming):
        async def tag_named(self, name: str, *, creating: bool) -> str | None:
            return ids.get(name)

    writer = SiteWriter(service, _Real())

    written = await writer.write(
        site, {"tags": list(ids)}, creating=True, actor=Actor.sift("stash")
    )

    assert written == {"tags": 2}
    assert _texts(await writer.current(site), "tags") == ["adult", "amateur"]


async def test_an_address_the_person_already_holds_is_not_counted_again(
    service: PeopleService, temp_db: Database
) -> None:
    """A second fetch from the same box offers the same address: nothing is added, and the count
    says so rather than claiming a link that was already there."""
    person = await _a_person(temp_db)
    writer = PersonWriter(service, _Naming())
    values = {
        "accounts": [
            {"site": "", "handle": "esmewren", "url": "https://agency.example.test/talent/esmewren"}
        ]
    }
    assert await writer.write(person, values, creating=False, actor=Actor.sift("stash")) == {
        "links": 1
    }

    again = await writer.write(person, values, creating=False, actor=Actor.sift("stash"))

    assert "links" not in again
    assert (await writer.current(person))["links"] == [
        "https://agency.example.test/talent/esmewren"
    ]


async def test_asking_about_nobody_reads_nothing(
    service: PeopleService, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A survey with no linked people is answered without a read: five statements over an empty
    list would each scan their table's index for nothing."""

    async def _no_reads(*_: object) -> object:
        raise AssertionError("a read was made for an empty list")

    monkeypatch.setattr(temp_db, "fetch_all", _no_reads)

    assert await PersonWriter(service, _Naming()).current_many([]) == {}


def test_a_site_invented_by_an_act_of_neither_a_user_nor_a_named_pass_says_so_as_unsaid() -> None:
    """A user's act is theirs and a named pass of Sift's is that pass's; anything else, a box or a
    pass with no name, is recorded as unsaid rather than claimed for somebody."""
    from sift.kernel.access.catalog import MADE_UNSAID, by_sift
    from sift.kernel.access.catalog import by_user as made_by_user
    from sift.slices.people.service_base import _made_by

    assert _made_by(Actor.user("u-1")) == made_by_user("u-1")
    assert _made_by(Actor.sift("stash")) == by_sift("stash")
    assert _made_by(Actor.sift()) == MADE_UNSAID
    assert _made_by(Actor.box("a-box")) == MADE_UNSAID
