# SPDX-License-Identifier: AGPL-3.0-or-later
"""A username's ID (the Site's own number for it), typed by a person (`catalog.set_username_number`).

What is pinned here, at the catalog and without a server in front of it:

- A typed ID fills a blank, and typing the one it already has is no change.
- It REPLACES one only where replacing was asked for, and then it is recorded as typed with nothing
  counted: a person has now vouched for it, whatever the row said before.
- An ID ANOTHER username on the same Site already has is refused and names that username, onto a
  blank and over an ID alike, rather than reaching the uniqueness rule and a 500. The same ID on
  another Site is not a clash: a number is the Site's.
"""

from __future__ import annotations

import json

import pytest

import sift.slices.workbench.schema  # noqa: F401 (the ledger's tables, for the History line)
from sift.kernel.access import (
    MADE_BY_A_PERSON,
    seed_site_username,
    set_username_number,
    username_number,
)
from sift.kernel.db import Database
from sift.kernel.ledger import Actor
from sift.testing.fixtures import Actors

pytestmark = pytest.mark.anyio


async def _username(db: Database, site: str, name: str) -> str:
    _, username_id = await seed_site_username(db, site=site, name=name, made=MADE_BY_A_PERSON)
    return username_id


async def test_a_typed_id_fills_a_blank_and_the_same_one_is_no_change(temp_db: Database) -> None:
    await temp_db.initialize_schema()
    wren = await _username(temp_db, "Instagram", "wrenly")

    assert (
        await set_username_number(temp_db, username_id=wren, number="4242")
    ).outcome == "written"
    assert (await set_username_number(temp_db, username_id=wren, number="4242")).outcome == "same"
    held = await username_number(temp_db, wren)
    assert (held.number, held.via, held.agreed) == ("4242", "typed", None)


async def test_a_typed_id_replaces_one_only_when_asked_and_is_then_typed(
    temp_db: Database,
) -> None:
    await temp_db.initialize_schema()
    wren = await _username(temp_db, "Instagram", "wrenly")
    await temp_db.execute(
        "UPDATE usernames SET number = '4815162342', number_via = 'metadata', number_agreed = 3"
        " WHERE id = ?",
        (wren,),
    )

    refused = await set_username_number(temp_db, username_id=wren, number="12345678")
    assert refused.outcome == "held"
    assert (await username_number(temp_db, wren)).number == "4815162342"

    fixed = await set_username_number(temp_db, username_id=wren, number="12345678", replace=True)
    assert fixed.outcome == "written"
    held = await username_number(temp_db, wren)
    assert (held.number, held.via, held.agreed) == ("12345678", "typed", None)


async def test_an_id_another_username_on_the_site_has_is_refused_by_name(
    temp_db: Database,
) -> None:
    await temp_db.initialize_schema()
    old_name = await _username(temp_db, "Instagram", "wrenly")
    new_name = await _username(temp_db, "Instagram", "wrenly_official")
    elsewhere = await _username(temp_db, "Fansly", "wrenly")
    await set_username_number(temp_db, username_id=old_name, number="12345678")

    # Another Site's number is its own: the same digits there are no clash.
    assert (
        await set_username_number(temp_db, username_id=elsewhere, number="12345678")
    ).outcome == "written"

    onto_a_blank = await set_username_number(temp_db, username_id=new_name, number="12345678")
    assert (onto_a_blank.outcome, onto_a_blank.other_id, onto_a_blank.other_name) == (
        "clash",
        old_name,
        "wrenly",
    )
    await set_username_number(temp_db, username_id=new_name, number="11")
    over_one = await set_username_number(
        temp_db, username_id=new_name, number="12345678", replace=True
    )
    assert (over_one.outcome, over_one.other_name) == ("clash", "wrenly")
    assert (await username_number(temp_db, new_name)).number == "11"


async def test_a_typed_id_is_said_in_history_with_the_one_it_replaced(
    temp_db: Database, actors: Actors
) -> None:
    """The row keeps only the current number, so the line is the one place the old one is kept:
    on the username, and on the person it belongs to. A number typed with nobody behind it (a
    caller passing no actor) and a refusal write nothing."""
    wren = await _username(temp_db, "Instagram", "wrenly")
    person = "person-wren"
    await temp_db.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, 'Wren Halloway', 0)", (person,)
    )
    await temp_db.execute("UPDATE usernames SET person_id = ? WHERE id = ?", (person, wren))
    by = Actor.user(actors.admin.id)

    await set_username_number(temp_db, username_id=wren, number="4242", actor=by)
    await set_username_number(temp_db, username_id=wren, number="99", actor=by)
    await set_username_number(temp_db, username_id=wren, number="12345678", replace=True, actor=by)

    rows = await temp_db.fetch_all(
        "SELECT object_kind, object_id, payload FROM workbench_decisions"
        " WHERE verb = 'edited' ORDER BY decided_at, rowid"
    )
    assert [(row["object_kind"], row["object_id"]) for row in rows] == [("person", person)] * 2
    said = [json.loads(row["payload"]) for row in rows]
    assert [(one["number_before"], one["number_after"]) for one in said] == [
        (None, "4242"),
        ("4242", "12345678"),
    ]
    assert {one["number_site"] for one in said} == {"Instagram"}
