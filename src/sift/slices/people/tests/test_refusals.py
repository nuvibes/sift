# SPDX-License-Identifier: AGPL-3.0-or-later
"""The answers this slice gives when what it was asked about is not there, or is not a value.

Every one of these is a refusal or an early return, and each is reached by calling the service or
the function directly. The routes above them resolve their ids first, which is right, and it is
also what makes these unreachable over HTTP, so a test that only drove the routes would leave the
guards behind them unwatched.

They are worth having rather than deleting. A service is called by jobs and by an import as well as
by a route, and the row a route resolved can be gone by the time the write runs.
"""

from __future__ import annotations

import pytest

from sift.kernel.access import Repository
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor
from sift.slices.people.merge import merge_many
from sift.slices.people.service import PeopleService

pytestmark = pytest.mark.anyio

NOBODY = "01HX0000000000000000000099"


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


async def _a_site(temp_db: Database, name: str = "SomeSite") -> str:
    site = new_id()
    await temp_db.execute("INSERT INTO sites (id, name) VALUES (?, ?)", (site, name))
    return site


# --- a value that is not one ---------------------------------------------------------------------


async def test_a_sites_address_that_is_only_spaces_is_not_an_address(
    service: PeopleService, temp_db: Database
) -> None:
    site = await _a_site(temp_db)

    assert await service.add_site_link(site, "   ") is False
    assert await service.site_links(site) == []


async def test_a_sites_address_it_already_holds_is_not_added_again(
    service: PeopleService, temp_db: Database
) -> None:
    """For an import, which adds what a stash-box said without touching what is there, unlike the
    form's save, which replaces the list because it sent the whole of it."""
    site = await _a_site(temp_db)
    assert await service.add_site_link(site, "https://example.test/site") is True

    assert await service.add_site_link(site, "https://example.test/site") is False
    assert await service.site_links(site) == ["https://example.test/site"]


async def test_an_also_known_as_that_is_only_spaces_is_not_a_name(
    service: PeopleService, temp_db: Database
) -> None:
    site = await _a_site(temp_db)

    assert await service.add_site_alias(site, "  ") is False
    assert await service.site_aliases(site) == []


async def test_a_site_is_not_given_a_name_it_already_answers_to(
    service: PeopleService, temp_db: Database
) -> None:
    """Folded, so `Elsewhere` and `elsewhere` are one name. Two spellings of one word in a list of
    other names is a list that reads as a mistake."""
    site = await _a_site(temp_db)
    await service.add_site_alias(site, "Elsewhere")

    assert await service.add_site_alias(site, "elsewhere") is False
    assert await service.site_aliases(site) == ["Elsewhere"]


async def test_a_sites_names_are_written_whole_and_a_blank_among_them_is_dropped(
    service: PeopleService, temp_db: Database
) -> None:
    """The form sends the whole list every time, so a stray empty box arrives with the rest of it."""
    site = await _a_site(temp_db)

    await service.set_site_record(
        site, aliases=["Elsewhere", "   ", ""], parent=None, actor=Actor.sift("stash")
    )

    assert await service.site_aliases(site) == ["Elsewhere"]


# --- something that is not there -------------------------------------------------------------------


async def test_joining_a_username_that_is_not_there_lands_on_nobody(
    service: PeopleService,
) -> None:
    assert (
        await service.attach_username(NOBODY, person_id=NOBODY, actor=Actor.sift("stash")) is None
    )


async def test_making_a_person_from_a_username_with_no_name_is_refused(
    service: PeopleService, temp_db: Database
) -> None:
    """One of the two branches CREATES a row in People, so it has to be asked for on purpose, and
    a nameless person is a row nobody could ever find again."""
    site_id, username_id = new_id(), new_id()
    await temp_db.execute("INSERT INTO sites (id, name) VALUES (?, 'SomeSite')", (site_id,))
    await temp_db.execute(
        "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, '', 0)",
        (username_id, site_id),
    )

    with pytest.raises(ValueError, match="needs a name"):
        await service.attach_username(username_id, new_person_name="   ", actor=Actor.sift("stash"))


async def test_taking_the_join_off_a_username_that_is_not_there_says_so(
    service: PeopleService,
) -> None:
    assert await service.detach_username(NOBODY, actor=Actor.sift("stash")) is False


async def test_the_record_of_something_that_is_not_there_is_empty_rather_than_missing(
    service: PeopleService,
) -> None:
    """A screen drawing a record it cannot find draws an empty one, which is what a record with
    nothing in it looks like anyway."""
    assert await service.person_record(NOBODY) == {}
    assert await service.site_record(NOBODY) == {}


async def test_a_site_given_a_parent_points_at_it_and_never_at_itself(
    service: PeopleService, temp_db: Database
) -> None:
    """The merge is handed a Site that exists and invents none: who makes a missing parent is the
    writer's to say (`SiteWriter`). A parent that is the Site itself is a cycle and is dropped."""
    site = await _a_site(temp_db, "SomeSite")
    network = await _a_site(temp_db, "A Network")

    assert (
        await service.merge_site_record(site, parent_id=network, actor=Actor.sift("stash")) is True
    )
    assert (await service.site_record(site))["parent"] == "A Network"

    assert await service.merge_site_record(network, parent_id=network, actor=Actor.sift("stash"))
    assert "parent" not in await service.site_record(network)


async def test_a_merge_of_somebody_into_themselves_moves_nothing(
    temp_db: Database, access: Repository
) -> None:
    """It would delete them. Refused in the function as well as at the route, because an import or
    a job could reach this without going through one."""
    await temp_db.initialize_schema()
    person = await _a_person(temp_db)

    assert (
        await merge_many(
            temp_db, access, losing=[person], keeping=person, actor=Actor.sift("stash")
        )
        is None
    )
    row = await temp_db.fetch_one("SELECT id FROM people WHERE id = ?", (person,))
    assert row is not None


async def test_taking_the_join_off_a_username_that_had_nobody_is_still_a_write(
    service: PeopleService, temp_db: Database
) -> None:
    """The unfile step is skipped when there was nobody to unfile from, which is the ordinary
    state of a username nobody has answered for, not an unusual one."""
    site_id, username_id = new_id(), new_id()
    await temp_db.execute("INSERT INTO sites (id, name) VALUES (?, 'SomeSite')", (site_id,))
    await temp_db.execute(
        "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, 'esmewrenfield', 0)",
        (username_id, site_id),
    )

    assert await service.detach_username(username_id, actor=Actor.sift("stash")) is True


async def test_an_added_address_takes_the_same_order_the_form_save_writes(
    service: PeopleService, temp_db: Database
) -> None:
    """An enrichment adds one address at a time; the form's save writes the whole list. Both put
    the Site's own address first, so its address is the same whichever of them touched it last.
    Appended as it came, another site's page would stay first until somebody saved the record."""
    site = await _a_site(temp_db, name="Marrowvale")
    assert await service.add_site_link(site, "https://theporndb.net/sites/marrowvale") is True
    assert await service.add_site_link(site, "https://www.marrowvale.example/") is True
    assert await service.add_site_link(site, "https://x.com/marrowvale") is True

    held = await service.site_links(site)
    assert held == [
        "https://www.marrowvale.example/",
        "https://theporndb.net/sites/marrowvale",
        "https://x.com/marrowvale",
    ]
