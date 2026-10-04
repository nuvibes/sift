# SPDX-License-Identifier: AGPL-3.0-or-later
"""The feed's page and its total, a page past the end, an act forgotten between two reads, and the
first press of each box.
"""

from __future__ import annotations

from typing import cast

import pytest

# Imported for its side effect: registering the record's tables, so a kernel database has them.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access.history_events import first_presses
from sift.kernel.access.history_feed import (
    _FOLDED_OBJECTS,
    _FOLDED_PAGE,
    _FOLDED_SUBJECTS,
    press_of,
    presses_recent,
)
from sift.kernel.db import Database, Params, Row
from sift.kernel.ledger import Actor, record_event
from sift.kernel.vocabulary import Subject
from sift.testing.fixtures import Actors, World

pytestmark = pytest.mark.anyio

AT = 1_700_000_000
BOX = "01HX0000000000000000000951"


async def renamed(database: Database, actors: Actors, world: World) -> str:
    async with database.write() as connection:
        return await record_event(
            connection,
            actor=Actor.user(actors.admin.id),
            verb="renamed",
            subject=Subject(kind="person", id=world.person, name="person"),
        )


async def test_a_page_past_the_end_of_the_feed_still_says_how_long_it_is(
    temp_db: Database, world: World, actors: Actors
) -> None:
    await renamed(temp_db, actors, world)

    presses, total = await presses_recent(temp_db, actors.admin)
    assert (len(presses), total) == (1, 1)
    assert await presses_recent(temp_db, actors.admin, offset=5) == ([], 1)


class ForgetsBetweenReads:
    """A database a forget lands in between the feed's page and the acts it names."""

    def __init__(self, database: Database, forget: str) -> None:
        self._database = database
        self._forget: str | None = forget

    async def fetch_all(self, statement: str, params: Params = ()) -> list[Row]:
        rows = await self._database.fetch_all(statement, params)
        if statement is _FOLDED_PAGE and self._forget is not None:
            forgotten, self._forget = self._forget, None
            await self._database.execute(
                "DELETE FROM workbench_decisions WHERE id = ?", (forgotten,)
            )
        return rows


async def test_an_act_forgotten_between_the_page_and_its_rows_is_left_off_the_page(
    temp_db: Database, world: World, actors: Actors
) -> None:
    forgotten = await renamed(temp_db, actors, world)
    racing = cast(Database, ForgetsBetweenReads(temp_db, forgotten))

    presses, total = await presses_recent(racing, actors.admin)

    assert presses == []
    assert total == 1


async def test_the_first_press_of_each_box_is_its_first_enrichment(
    temp_db: Database, world: World
) -> None:
    for event_id, box, at in (
        ("01HX0000000000000000000961", None, AT),
        ("01HX0000000000000000000962", BOX, AT + 1),
        ("01HX0000000000000000000963", BOX, AT + 2),
    ):
        await temp_db.execute(
            "INSERT INTO workbench_decisions (id, queue, title, detail, payload, decided_at, verb,"
            " object_kind, object_id) VALUES (?, 'ledger', 't', '', '{}', ?, 'enriched', 'box', ?)",
            (event_id, at, box),
        )
        await temp_db.execute(
            "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
            " VALUES (?, 'person', ?)",
            (event_id, world.person),
        )

    assert await first_presses(temp_db, "person", world.person) == {
        BOX: "01HX0000000000000000000962"
    }


#: A task's acts in two presses a long gap apart, and a person's act inside the first press's
#: stretch of time: (id, moment, actor kind, actor, the tag it named).
TWO_PRESSES = (
    ("01HX0000000000000000000971", AT, "sift", "tagger", "01HX00000000000000000009T1"),
    ("01HX0000000000000000000972", AT + 1, "user", None, "01HX00000000000000000009T9"),
    ("01HX0000000000000000000973", AT + 2, "sift", "tagger", "01HX00000000000000000009T2"),
    ("01HX0000000000000000000974", AT + 300, "sift", "tagger", "01HX00000000000000000009T3"),
    ("01HX0000000000000000000975", AT + 301, "sift", "tagger", "01HX00000000000000000009T3"),
)


async def two_presses(database: Database, actors: Actors, world: World) -> None:
    for event_id, at, actor_kind, actor_id, tag in TWO_PRESSES:
        await database.execute(
            "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload,"
            " decided_at, verb, actor_kind, actor_id, object_kind, object_id)"
            " VALUES (?, 'ledger', ?, 't', '', '{}', ?, 'tagged', ?, ?, 'tag', ?)",
            (event_id, actors.admin.id, at, actor_kind, actor_id, tag),
        )
        await database.execute(
            "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
            " VALUES (?, 'person', ?)",
            (event_id, world.person if actor_kind == "sift" else event_id),
        )


async def test_a_folded_line_holds_its_own_press_and_nothing_inside_its_stretch(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """A folded line's things are its own key's acts from its oldest to its newest: not the same
    task's other press, and not another act that landed in between."""
    await two_presses(temp_db, actors, world)

    presses, total = await presses_recent(temp_db, actors.admin)

    assert total == 3
    newer, older, person = presses
    assert (newer.folded, [one.id for one in newer.objects]) == (2, ["01HX00000000000000000009T3"])
    assert newer.objects[0].acts == 2
    assert person.folded == 1
    assert older.folded == 2
    assert older.first is not None and older.first.id == "01HX0000000000000000000971"
    assert sorted(one.id for one in older.objects) == [
        "01HX00000000000000000009T1",
        "01HX00000000000000000009T2",
    ]
    assert [one.id for one in older.subjects] == [world.person]
    assert await press_of(temp_db, actors.admin, "01HX0000000000000000000971") == [
        ("01HX0000000000000000000973", "ledger"),
        ("01HX0000000000000000000971", "ledger"),
    ]


async def test_what_a_folded_line_holds_is_a_seek_on_its_moments(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """What a folded line was done with and about is read off the stretch of time its press spans,
    never by walking the record again: the walk is the page's, once."""
    await two_presses(temp_db, actors, world)
    values = {
        "viewer": actors.admin.id,
        "reveal": 0,
        "admin": 1,
        "kind": None,
        "verb": None,
        "decisions": 0,
        "ledger": "ledger",
        "lines": "[]",
    }
    for statement in (_FOLDED_OBJECTS, _FOLDED_SUBJECTS):
        plan = await temp_db.fetch_all(
            "EXPLAIN QUERY PLAN " + statement,  # nosemgrep: sift-no-string-built-sql
            values,
        )
        steps = [str(row["detail"]) for row in plan]
        assert any("USING INDEX ix_workbench_decided" in step for step in steps), steps
        assert not any(step.startswith("SCAN d") for step in steps), steps
