# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a stash-box's run filled in, named from the box's kept answer and the record.

A run writes down which fields a box filled and how many rows each list gained, never the values;
the line reads the values back from the answer kept on the link and from the record as it is. Each
case here is one shape of that reading: a link said by its site's address, a parent said as the
Site it is, a list given for a single field, a value with no words, a run it cannot read, and the
events the feed names.
"""

from __future__ import annotations

import json

import pytest

# Imported for their side effect: registering the stash-boxes' and the record's tables.
import sift.slices.stash_boxes.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access.history_boxes import (
    _Held,
    agreeing_with_box,
    filled_named,
    named_of_event,
    named_of_events,
)
from sift.kernel.access.history_events import LedgerEvent, Thing
from sift.kernel.access.sentences import FilledField, text_of
from sift.kernel.db import Database
from sift.kernel.sorting import sort_key

pytestmark = pytest.mark.anyio

AT = 1_700_000_000
SITE = "01HX0000000000000000000901"
BOX = "01HX0000000000000000000902"
PARENT = "01HX0000000000000000000903"
PERSON = "01HX0000000000000000000904"
SHOP = "https://www.quillhouse-shop.invalid/wrenna"

_ANSWER = {
    "person": "INSERT OR REPLACE INTO person_stash_box_links"
    " (person_id, box_id, remote_id, payload, fetched_at) VALUES (?, ?, 'r-1', ?, 0)",
    "site": "INSERT OR REPLACE INTO site_stash_box_links"
    " (site_id, box_id, remote_id, payload, fetched_at) VALUES (?, ?, 'r-1', ?, 0)",
}


@pytest.fixture
async def db(temp_db: Database) -> Database:
    await temp_db.initialize_schema()
    await temp_db.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, 'FansDB', ?, 0)",
        (BOX, "https://stash-box.invalid/graphql"),
    )
    for site_id, name in ((PARENT, "Cedar Vale"), (SITE, "Quillhouse")):
        await temp_db.execute(
            "INSERT INTO sites (id, name, name_sort, created_at) VALUES (?, ?, ?, ?)",
            (site_id, name, sort_key(name), AT),
        )
    await temp_db.execute(
        "INSERT INTO people (id, name, name_sort, created_at) VALUES (?, 'Wrenna Sable', ?, 0)",
        (PERSON, sort_key("Wrenna Sable")),
    )
    return temp_db


async def _answered(db: Database, subject: str, local_id: str, fields: object) -> None:
    """The box's answer kept on the link, as the stash-box slice keeps it."""
    payload = fields if isinstance(fields, str) else json.dumps([{"fields": fields}])
    await db.execute(_ANSWER[subject], (local_id, BOX, payload))


def _said(field: FilledField) -> tuple[str, ...]:
    return tuple(text_of(one) for one in field.values)


async def test_the_links_a_run_added_are_said_by_their_sites_address(db: Database) -> None:
    """Only the rows added in the run's moment that the answer lists, by host without `www.`."""
    await _answered(db, "site", SITE, {"links": [], "accounts": [{"url": SHOP}, "not a page"]})
    for at, (url, when) in enumerate(((SHOP, AT), ("https://elsewhere.invalid/q", AT))):
        await db.execute(
            "INSERT INTO site_links (id, site_id, url, created_at) VALUES (?, ?, ?, ?)",
            (f"link-{at}", SITE, url, when),
        )

    fields = await filled_named(db, "site", SITE, BOX, at=AT, stored='{"links": 1}')

    assert fields is not None and len(fields) == 1
    assert _said(fields[0]) == ("quillhouse-shop.invalid",)


async def test_a_parent_is_said_as_the_site_it_is_and_one_not_here_by_its_words(
    db: Database,
) -> None:
    await db.execute("UPDATE sites SET parent_id = ? WHERE id = ?", (PARENT, SITE))
    await _answered(db, "site", SITE, {"parent": "Cedar Vale"})

    held = await filled_named(db, "site", SITE, BOX, at=AT, stored='["parent"]')
    agreed = await agreeing_with_box(db, "site", SITE, BOX)

    assert held is not None
    assert (_said(held[0]), held[0].changed) == (("Cedar Vale",), False)
    assert [_said(one) for one in agreed] == [("Cedar Vale",)]

    await _answered(db, "site", SITE, {"parent": "Harbor Lane"})

    moved = await filled_named(db, "site", SITE, BOX, at=AT, stored='["parent"]')

    assert moved is not None
    assert (_said(moved[0]), moved[0].changed) == (("Harbor Lane",), True)
    assert await agreeing_with_box(db, "site", SITE, BOX) == ()


async def test_a_list_given_for_a_single_field_is_said_item_by_item(db: Database) -> None:
    """Tattoos are one field of the record holding several words; agreement is the same words in
    any order and case, and a record changed since is said as changed (and not as agreeing)."""
    await db.execute("UPDATE people SET tattoos = ? WHERE id = ?", ('["rose", "anchor"]', PERSON))
    await _answered(db, "person", PERSON, {"name": "Wrenna Sable", "tattoos": ["Anchor", "rose"]})

    filled = await filled_named(db, "person", PERSON, BOX, at=AT, stored='["tattoos"]')
    agreed = await agreeing_with_box(db, "person", PERSON, BOX)

    assert filled is not None
    assert (filled[0].count, _said(filled[0]), filled[0].changed) == (2, ("Anchor", "rose"), False)
    assert [_said(one) for one in agreed] == [("Anchor", "rose")], "the name was said as agreeing"

    await db.execute("UPDATE people SET tattoos = ? WHERE id = ?", ('["star"]', PERSON))

    changed = await filled_named(db, "person", PERSON, BOX, at=AT, stored='["tattoos"]')

    assert changed is not None and changed[0].changed
    assert await agreeing_with_box(db, "person", PERSON, BOX) == ()


async def test_a_paragraph_or_a_value_with_no_words_is_said_by_its_field_alone(
    db: Database,
) -> None:
    """A paragraph is never read out into a line, and a blank has nothing to read out; neither is
    something the record can be said to agree with."""
    await _answered(db, "person", PERSON, {"details": "A long paragraph.", "measurements": ""})

    filled = await filled_named(
        db, "person", PERSON, BOX, at=AT, stored='["details", "measurements"]'
    )

    assert filled is not None
    assert [(one.count, one.values) for one in filled] == [(1, ()), (1, ())]
    assert await agreeing_with_box(db, "person", PERSON, BOX) == ()


async def test_a_list_the_run_did_not_count_is_counted_from_the_rows_it_added(
    db: Database,
) -> None:
    """A run that named its fields without counts: the rows added in its moment are the count. A
    Site's usernames are not read from a run, so they are counted by the run's own number."""
    await _answered(db, "site", SITE, {"aliases": ["Quill House", "QH"]})
    for at, (alias, when) in enumerate((("Quill House", AT), ("QH", AT - 1000))):
        await db.execute(
            "INSERT INTO site_aliases (id, site_id, alias, added_at) VALUES (?, ?, ?, ?)",
            (f"alias-{at}", SITE, alias, when),
        )

    aliases = await filled_named(db, "site", SITE, BOX, at=AT, stored='["aliases"]')
    accounts = await filled_named(db, "site", SITE, BOX, at=AT, stored='{"accounts": 2}')

    assert aliases is not None and (aliases[0].count, _said(aliases[0])) == (1, ("Quill House",))
    assert accounts is not None and [(one.count, one.values) for one in accounts] == [(2, ())]


async def test_a_run_or_an_answer_it_cannot_read_says_nothing_it_does_not_know(
    db: Database,
) -> None:
    for stored in ("not json", "3", '"parent"'):
        assert await filled_named(db, "site", SITE, BOX, at=AT, stored=stored) is None, stored
    assert await agreeing_with_box(db, "asset", SITE, BOX) == ()
    await _answered(db, "site", SITE, "not json")

    fields = await filled_named(db, "site", SITE, BOX, at=AT, stored='["parent"]')

    assert fields is not None and [(one.count, one.values) for one in fields] == [(1, ())]
    assert await agreeing_with_box(db, "site", SITE, BOX) == ()


async def test_a_record_deleted_between_the_two_reads_holds_no_value(db: Database) -> None:
    """The answer and the record are two reads; a record gone between them reads as holding
    nothing rather than failing the page."""
    held = _Held(db, "site", "01HX0000000000000000000999")
    await held.load()

    assert held.value("parent") is None


def _event(event_id: str, box: Thing | None, *subjects: Thing) -> LedgerEvent:
    return LedgerEvent(
        id=event_id,
        at=AT,
        verb="enriched",
        actor_kind="box",
        actor_id=BOX,
        user_id=None,
        object=box,
        count=None,
        queue="ledger",
        payload='["parent"]',
        title="",
        detail="",
        reversed_at=None,
        subjects=subjects,
    )


async def test_the_feed_names_only_what_a_box_filled_in_on_a_record(db: Database) -> None:
    await db.execute("UPDATE sites SET parent_id = ? WHERE id = ?", (PARENT, SITE))
    await _answered(db, "site", SITE, {"parent": "Cedar Vale"})
    box = Thing(kind="box", id=BOX, name="FansDB")
    on_site = _event("e-site", box, Thing(kind="site", id=SITE))
    on_username = _event("e-username", box, Thing(kind="username", id="u-1"))

    named = await named_of_events(db, [on_site, on_username])

    assert list(named) == ["e-site"]
    assert [_said(one) for one in named["e-site"]] == [("Cedar Vale",)]
    assert await named_of_event(db, _event("e-none", None), "site", SITE) is None
    assert (
        await named_of_event(db, _event("e-bare", Thing(kind="box", id="")), "site", SITE) is None
    )
