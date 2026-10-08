# SPDX-License-Identifier: AGPL-3.0-or-later
"""The feed's page and its total, a page past the end, an act forgotten between two reads, and the
first press of each box.
"""

from __future__ import annotations

import re
from typing import cast

import pytest

# Imported for its side effect: registering the record's tables, so a kernel database has them.
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access.history_events import first_presses
from sift.kernel.access.history_feed import (
    _FOLDED_KIND_PAGE,
    _FOLDED_MEMBERS,
    _FOLDED_PAGE,
    _FOLDED_THINGS,
    _FOLDED_TOTAL,
    press_of,
    presses_recent,
)
from sift.kernel.access.history_presses import keep_presses
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
    """What a folded line was done with and about is a seek on its key and the stretch of time its
    press spans, never a walk of the record."""
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
    for statement in (_FOLDED_THINGS,):
        plan = await temp_db.fetch_all(
            "EXPLAIN QUERY PLAN " + statement,  # nosemgrep: sift-no-string-built-sql
            values,
        )
        steps = [str(row["detail"]) for row in plan]
        assert any("USING INDEX ix_workbench_press" in step for step in steps), steps
        assert not any(step.startswith("SCAN d") for step in steps), steps


#: Every act's marks as the window over its key reads them: what the triggers must keep.
_BY_WINDOW = """
SELECT id,
       CASE WHEN decided_at - LAG(decided_at) OVER w <= fold_gap THEN 0 ELSE 1 END AS opens,
       CASE WHEN LEAD(decided_at - fold_gap) OVER w <= decided_at THEN 0 ELSE 1 END AS closes
  FROM workbench_decisions WINDOW w AS (PARTITION BY fold_key ORDER BY decided_at, id)
"""


async def _marks(
    database: Database,
) -> tuple[dict[str, tuple[int, int]], dict[str, tuple[int, int]]]:
    """The marks stored, and the marks the window reads."""
    kept = await database.fetch_all("SELECT id, opens, closes FROM workbench_decisions")
    read = await database.fetch_all(_BY_WINDOW)
    return (
        {str(row["id"]): (int(row["opens"]), int(row["closes"])) for row in kept},
        {str(row["id"]): (int(row["opens"]), int(row["closes"])) for row in read},
    )


async def _tagged(database: Database, actors: Actors, event_id: str, at: int) -> None:
    await database.execute(
        "INSERT INTO workbench_decisions (id, queue, user_id, title, detail, payload, decided_at,"
        " verb, actor_kind, actor_id, object_kind, object_id)"
        " VALUES (?, 'ledger', ?, 't', '', '{}', ?, 'tagged', 'sift', 'tagger', 'tag', 'x')",
        (event_id, actors.admin.id, at),
    )


async def test_the_press_marks_follow_an_act_arriving_moving_and_going(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """An act landing between two presses joins them, one leaving splits them again, and one moved
    away is its own press: the stored marks read what the window reads after each."""
    await two_presses(temp_db, actors, world)
    for event_id, at in (
        ("01HX00000000000000000009B1", AT + 60),
        ("01HX00000000000000000009B2", AT + 120),
        ("01HX00000000000000000009B3", AT + 180),
        ("01HX00000000000000000009B4", AT + 240),
    ):
        await _tagged(temp_db, actors, event_id, at)
    kept, read = await _marks(temp_db)
    assert kept == read
    assert (await presses_recent(temp_db, actors.admin))[1] == 2

    await temp_db.execute("DELETE FROM workbench_decisions WHERE id = '01HX00000000000000000009B2'")
    kept, read = await _marks(temp_db)
    assert kept == read
    assert (await presses_recent(temp_db, actors.admin))[1] == 3

    await temp_db.execute(
        "UPDATE workbench_decisions SET decided_at = ? WHERE id = '01HX0000000000000000000971'",
        (AT + 10_000,),
    )
    kept, read = await _marks(temp_db)
    assert kept == read
    assert (await presses_recent(temp_db, actors.admin))[1] == 4


async def test_a_record_from_before_the_marks_is_marked_once(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """A library from before the marks gains them on its next boot; a boot that finds them changes
    nothing, and a key generated by an older rule is generated again."""
    from sift.slices.workbench.schema import initialize_workbench

    async with temp_db.write() as connection:
        for statement in (
            "DROP TRIGGER workbench_press_arrives",
            "DROP TRIGGER workbench_press_moves",
            "DROP TRIGGER workbench_press_goes",
            "DROP INDEX ix_workbench_fold",
            "DROP INDEX ix_workbench_openings",
            "DROP INDEX ix_workbench_closings",
            "DROP INDEX ix_workbench_closing_keys",
            "ALTER TABLE workbench_decisions DROP COLUMN fold_key",
            "ALTER TABLE workbench_decisions DROP COLUMN fold_gap",
            "ALTER TABLE workbench_decisions DROP COLUMN opens",
            "ALTER TABLE workbench_decisions DROP COLUMN closes",
        ):
            await connection.execute(statement)
    await two_presses(temp_db, actors, world)

    async with temp_db.write() as connection:
        await initialize_workbench(connection, 16)
    kept, read = await _marks(temp_db)
    assert kept == read
    assert (await presses_recent(temp_db, actors.admin))[1] == 3

    await temp_db.execute("UPDATE workbench_decisions SET closes = 0")
    async with temp_db.write() as connection:
        await keep_presses(connection)
    assert {one for _opens, one in (await _marks(temp_db))[0].values()} == {0}

    async with temp_db.write() as connection:
        for statement in (
            "DROP TRIGGER workbench_press_arrives",
            "DROP TRIGGER workbench_press_moves",
            "DROP TRIGGER workbench_press_goes",
            "DROP INDEX ix_workbench_fold",
            "DROP INDEX ix_workbench_openings",
            "DROP INDEX ix_workbench_closing_keys",
            "ALTER TABLE workbench_decisions DROP COLUMN fold_key",
            "ALTER TABLE workbench_decisions ADD COLUMN fold_key TEXT GENERATED ALWAYS AS (id) VIRTUAL",
        ):
            await connection.execute(statement)
        await keep_presses(connection)
    kept, read = await _marks(temp_db)
    assert kept == read
    assert (await presses_recent(temp_db, actors.admin))[1] == 3


async def test_a_page_of_the_feed_reads_the_presses_it_draws(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """The page, its count, a narrowing to one kind and Undo all each seek on the marks: none walks
    the record, and the only walk is down the closings."""
    await two_presses(temp_db, actors, world)
    values = {
        "viewer": actors.admin.id,
        "reveal": 0,
        "admin": 1,
        "kind": "person",
        "verb": None,
        "decisions": 0,
        "ledger": "ledger",
        "limit": 50,
        "offset": 0,
        "event": "01HX0000000000000000000971",
    }
    for statement in (_FOLDED_PAGE, _FOLDED_TOTAL, _FOLDED_KIND_PAGE, _FOLDED_MEMBERS):
        plan = await temp_db.fetch_all(
            "EXPLAIN QUERY PLAN " + statement,  # nosemgrep: sift-no-string-built-sql
            values,
        )
        steps = [str(row["detail"]) for row in plan]
        walks = [step for step in steps if re.match(r"SCAN (c|d|t|o|x|me)\b", step)]
        assert all("USING INDEX ix_workbench_closings" in step for step in walks), steps


async def test_a_narrowed_feed_keeps_a_press_whole(
    temp_db: Database, world: World, actors: Actors
) -> None:
    """An act a narrowing leaves out does not split the press it fell in: the press is one line,
    counting the acts the narrowing holds."""
    for event_id, at, kind in (
        ("01HX00000000000000000009N1", AT, "person"),
        ("01HX00000000000000000009N2", AT + 50, "site"),
        ("01HX00000000000000000009N3", AT + 100, "person"),
    ):
        await _tagged(temp_db, actors, event_id, at)
        await temp_db.execute(
            "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
            " VALUES (?, ?, ?)",
            (event_id, kind, world.person),
        )

    presses, total = await presses_recent(temp_db, actors.admin, kind="person")

    assert total == 1
    assert presses[0].folded == 2
    assert presses[0].first is not None and presses[0].first.id == "01HX00000000000000000009N1"
    assert await press_of(temp_db, actors.admin, "01HX00000000000000000009N3", kind="person") == [
        ("01HX00000000000000000009N3", "ledger"),
        ("01HX00000000000000000009N1", "ledger"),
    ]
    # The act the narrowing leaves out is no line's, so nothing is undone from it.
    assert await press_of(temp_db, actors.admin, "01HX00000000000000000009N2", kind="person") == []
