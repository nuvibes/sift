# SPDX-License-Identifier: AGPL-3.0-or-later
"""What each decision was about, and the migration that made room for it on an existing library."""

from __future__ import annotations

import pytest

from sift.kernel.access import Viewer
from sift.kernel.db import Connection, Database
from sift.kernel.vocabulary import Subject, SubjectKind
from sift.slices.workbench.store import Store

pytestmark = pytest.mark.unit

_EPOCH = 1_700_000_000
_ASSET = "01HX0000000000000000000601"
_OTHER = "01HX0000000000000000000602"
_PERSON = "01HX0000000000000000000603"


def _named(kind: SubjectKind, subject_id: str) -> Subject:
    """A subject as its writer hands it: with what it is called. None of these ids is a row in this
    database, and the record refuses a subject it can neither be told nor look up the name of:
    an event about an unnamed nothing. The links, which are what this file is about, do not read
    the name at all."""
    return Subject(kind=kind, id=subject_id, name=f"{kind} {subject_id[-3:]}")


async def _subjects_of(database: Database, decision_id: str) -> list[tuple[str, str]]:
    rows = await database.fetch_all(
        "SELECT kind, subject_id FROM workbench_decision_subjects"
        " WHERE decision_id = ? ORDER BY kind, subject_id",
        (decision_id,),
    )
    return [(str(row["kind"]), str(row["subject_id"])) for row in rows]


async def _record(store: Store, admin: Viewer, subjects: list[Subject]) -> str:
    async with store.database.write() as connection:
        return await store.record_on(
            connection,
            queue="folders",
            user_id=admin.id,
            title="Ilva Brennan - 47 files",
            detail="47 files filed under Ilva Brennan.",
            payload="{}",
            subjects=subjects,
        )


async def test_a_decision_writes_down_what_it_was_about(store: Store, admin: Viewer) -> None:
    """The whole point of the table: a file can be asked which decisions named it."""
    decision = await _record(
        store,
        admin,
        [_named("asset", _ASSET), _named("person", _PERSON)],
    )

    assert await _subjects_of(store.database, decision) == [
        ("asset", _ASSET),
        ("person", _PERSON),
    ]


async def test_naming_the_same_thing_twice_is_one_row(store: Store, admin: Viewer) -> None:
    """A caller passing what it knows from two directions is saying one true thing twice.

    Refusing it would fail the decision itself (a real write undone by a bookkeeping duplicate),
    and keeping both would draw the same event twice in one file's history.
    """
    decision = await _record(store, admin, [_named("asset", _ASSET), _named("asset", _ASSET)])

    assert await _subjects_of(store.database, decision) == [("asset", _ASSET)]


async def test_a_decision_about_nothing_writes_no_links(store: Store, admin: Viewer) -> None:
    """Empty is a real answer. A quarantined file was never imported, so there is no row to name."""
    decision = await _record(store, admin, [])

    assert await _subjects_of(store.database, decision) == []


async def test_a_decision_taken_back_keeps_its_links(store: Store, admin: Viewer) -> None:
    """`reversed_at` is the fact, and the history draws the reversal as an event of its own.

    Deleting the links would make a decision that was taken back one that never happened, which is
    the half of the record somebody is reading it for.
    """
    decision = await _record(store, admin, [_named("asset", _ASSET)])

    assert await store.mark_reversed(decision)

    assert await _subjects_of(store.database, decision) == [("asset", _ASSET)]


async def test_links_go_with_the_decision_they_belong_to(store: Store, admin: Viewer) -> None:
    """The cascade. A link to a decision that is gone is not a fact about anything."""
    decision = await _record(store, admin, [_named("asset", _ASSET)])
    kept = await _record(store, admin, [_named("asset", _OTHER)])

    async with store.database.write() as connection:
        await connection.execute("DELETE FROM workbench_decisions WHERE id = ?", (decision,))

    assert await _subjects_of(store.database, decision) == []
    assert await _subjects_of(store.database, kept) == [("asset", _OTHER)]


# --- the step that added the table ---------------------------------------------------------------


async def _library_at_v1(connection: Connection) -> None:
    """A workbench before this step, written out: the initializer would build today's shape."""
    await connection.execute(
        "CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT NOT NULL)"
    )
    await connection.execute(
        "CREATE TABLE IF NOT EXISTS workbench_decisions ("
        " id TEXT PRIMARY KEY, queue TEXT NOT NULL, user_id TEXT REFERENCES users(id),"
        " title TEXT NOT NULL, detail TEXT NOT NULL, payload TEXT NOT NULL,"
        " decided_at INTEGER NOT NULL, reversed_at INTEGER)"
    )
    await connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_workbench_decided"
        " ON workbench_decisions(decided_at DESC, id DESC)"
    )
    await connection.execute(
        "INSERT INTO workbench_decisions"
        " (id, queue, user_id, title, detail, payload, decided_at, reversed_at)"
        " VALUES ('old', 'folders', NULL, 'Something', 'It did something.', '{}', ?, NULL)",
        (_EPOCH,),
    )
