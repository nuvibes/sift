# SPDX-License-Identifier: AGPL-3.0-or-later
"""Writing PART of a person's or a site's record, which is what an import does.

The forms next door send a whole record and get a statement that replaces one: right for a form,
because it drew every field and sends them all back. A stash-box sends whatever it happened to
carry, so an import needs a writer that leaves every field it did not name exactly as it was.

Nothing else in the application uses these two, so a fault in them is one the edit forms would
never show. A RETURNING statement whose rows are never read makes both raise on the first call (see
`tests/gates/test_returning_is_read.py`, the gate that holds that rule).

Driven against the service rather than over HTTP, because the route that reaches them is the
reconcile screen's and it is tested where that screen is tested. What is under test here is the
writer itself: what it changes, and what it leaves alone.
"""

from __future__ import annotations

import pytest

from sift.kernel.access import Repository
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor
from sift.slices.people.service import PeopleService

pytestmark = pytest.mark.anyio


@pytest.fixture
async def service(temp_db: Database, access: Repository) -> PeopleService:
    await temp_db.initialize_schema()
    return PeopleService(temp_db, access)


async def _a_person(temp_db: Database) -> str:
    person = new_id()
    await temp_db.execute(
        "INSERT INTO people (id, name, notes, birth_date, country, created_at)"
        " VALUES (?, 'Jane', 'a note', '1990-01-01', 'US', 0)",
        (person,),
    )
    return person


async def _a_site(temp_db: Database) -> str:
    site = new_id()
    await temp_db.execute(
        "INSERT INTO sites (id, name, kind, notes) VALUES (?, 'SomeSite', 'site', 'a note')",
        (site,),
    )
    return site


async def test_a_persons_record_is_written_a_field_at_a_time(
    service: PeopleService, temp_db: Database
) -> None:
    person = await _a_person(temp_db)

    assert (
        await service.merge_person_record(
            person, columns={"birth_date": "1991-02-02"}, actor=Actor.sift("stash")
        )
        is True
    )

    held = await service.person_record(person)
    assert held["birth_date"] == "1991-02-02"
    assert held["country"] == "US", "a field the write did not name is left exactly as it was"


async def test_a_persons_name_and_notes_are_left_alone_unless_they_are_named(
    service: PeopleService, temp_db: Database
) -> None:
    """They are not record columns and they are not sent by an import. Reading silence as a value
    would make an ordinary import blank the notes somebody typed."""
    person = await _a_person(temp_db)

    await service.merge_person_record(person, columns={"country": "GB"}, actor=Actor.sift("stash"))

    row = await temp_db.fetch_one("SELECT name, notes FROM people WHERE id = ?", (person,))
    assert row is not None
    assert (row["name"], row["notes"]) == ("Jane", "a note")


async def test_a_persons_name_is_written_when_it_is_named(
    service: PeopleService, temp_db: Database
) -> None:
    person = await _a_person(temp_db)

    await service.merge_person_record(
        person, name="Jane Doe", details="another note", actor=Actor.sift("stash")
    )

    row = await temp_db.fetch_one("SELECT name, notes FROM people WHERE id = ?", (person,))
    assert row is not None
    assert (row["name"], row["notes"]) == ("Jane Doe", "another note")


async def test_a_sites_record_is_written_a_field_at_a_time(
    service: PeopleService, temp_db: Database
) -> None:
    site = await _a_site(temp_db)

    assert (
        await service.merge_site_record(site, details="A better note.", actor=Actor.sift("stash"))
        is True
    )

    row = await temp_db.fetch_one("SELECT name, kind, notes FROM sites WHERE id = ?", (site,))
    assert row is not None
    assert row["notes"] == "A better note."
    assert (row["name"], row["kind"]) == ("SomeSite", "site"), "what it did not name is untouched"


async def test_merging_onto_something_that_is_not_there_says_so_rather_than_raising(
    service: PeopleService,
) -> None:
    """An import can be applying a decision taken before somebody removed what it names."""
    gone = new_id()

    assert (
        await service.merge_person_record(
            gone, columns={"country": "US"}, actor=Actor.sift("stash")
        )
        is False
    )
    assert (
        await service.merge_site_record(gone, details="a note", actor=Actor.sift("stash")) is False
    )
