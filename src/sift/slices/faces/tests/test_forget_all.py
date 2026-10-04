# SPDX-License-Identifier: AGPL-3.0-or-later
"""Delete face data a batch per write, as a job (`forget_all`, `FACE_FORGET`)."""

from __future__ import annotations

import asyncio
import itertools
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import sift.slices.workbench.schema  # noqa: F401  (registers the ledger's table)
from sift.kernel.access.history_events import events_of_entity
from sift.kernel.db import Connection, Database
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor
from sift.slices.auth import AuthService, Hasher, MasterKeyStore
from sift.slices.auth.crypto import resolve_argon2_params
from sift.slices.faces.forget_all import TABLES, TURN_SECONDS, Turns, forget_all
from sift.slices.faces.jobs import forget
from sift.slices.faces.service import FaceService
from sift.slices.faces.store import Store
from sift.slices.faces.tests.conftest import make_person
from sift.testing.fixtures import Actors

pytestmark = pytest.mark.integration

PILES = 12

# Strong enough for the password policy, and on no breach list.
PASSWORD = "Gr4vel-Lantern!orchid7"


async def _piles(db: Database) -> int:
    row = await db.fetch_one("SELECT COUNT(*) AS n FROM face_piles", ())
    return int(row["n"]) if row is not None else 0


async def _seed_piles(db: Database, count: int) -> None:
    async with db.write() as connection:
        await connection.executemany(
            "INSERT INTO face_piles (id, status, centroid, size, created_at, updated_at)"
            " VALUES (?, 'open', x'00', 1, 0, 0)",
            [(new_id(),) for _ in range(count)],
        )


def _ticking() -> itertools.count[float]:
    """A stand-in clock on which every write takes half a second, however few its rows."""
    return itertools.count(0.0, 0.5)


async def test_the_writer_is_given_back_between_batches(
    store: Store, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writes that measure slow shrink to a row each: a sign-in's write waits one batch, never a
    whole table."""
    await _seed_piles(temp_db, PILES)
    real = temp_db.write
    opened = 0

    @asynccontextmanager
    async def counting() -> AsyncIterator[Connection]:
        nonlocal opened
        opened += 1
        async with real() as connection:
            yield connection

    monkeypatch.setattr(temp_db, "write", counting)
    clock = _ticking()

    await forget_all(
        temp_db, actor=Actor.sift("faces"), roots=(), first=1, clock=lambda: next(clock)
    )

    assert opened > PILES + len(TABLES)
    assert await _piles(temp_db) == 0


def test_a_batch_is_sized_by_what_its_rows_cost() -> None:
    """Shrinks at once to what fits a turn, and grows by doubling at most."""
    turns = Turns(first=50, turn=0.25, most=5000)
    turns.took(50, 0.5)
    assert turns.size == 25
    turns.took(25, 0.0001)
    assert turns.size == 50
    turns.took(50, 0.0)
    assert turns.size == 100
    turns.took(100, 10.0)
    assert turns.size == 2
    turns.took(2, 10.0)
    assert turns.size == 1


def test_a_write_that_took_no_rows_says_nothing_about_what_a_row_costs() -> None:
    """Its time is the statement's alone; read as a cost per row it would shrink the batch to one."""
    turns = Turns(first=50, turn=0.25, most=5000)
    turns.took(0, 10.0)
    assert turns.size == 50


async def test_every_write_fits_a_turn_however_dear_its_rows(
    store: Store, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows that cost 4 ms each on a stand-in clock: no write after the first holds the writer past
    its turn, where a fixed count of rows would hold it for seconds on a slow machine."""
    await _seed_piles(temp_db, 1000)
    real = temp_db.write
    rows_per_write: list[int] = []
    changes = 0

    @asynccontextmanager
    async def counting() -> AsyncIterator[Connection]:
        nonlocal changes
        async with real() as connection:
            before = connection.total_changes
            yield connection
            rows_per_write.append(connection.total_changes - before)
            changes = connection.total_changes

    monkeypatch.setattr(temp_db, "write", counting)

    def clock() -> float:
        return changes * 0.004

    await forget_all(temp_db, actor=Actor.sift("faces"), roots=(), first=50, clock=clock)

    assert await _piles(temp_db) == 0
    held = [rows * 0.004 for rows in rows_per_write]
    assert max(held) <= TURN_SECONDS + 1e-9


async def test_the_search_index_write_counts_against_its_batch(
    store: Store, temp_db: Database
) -> None:
    """The files a batch took names off are written to the search index in a write of their own,
    and that write's cost sizes the next batch too: here it is 10 ms a file on a stand-in clock and
    the names themselves cost nothing, and no index write runs past its turn."""
    person = await make_person(temp_db, "Orrin Blythe")
    async with temp_db.write() as connection:
        for _ in range(300):
            asset = new_id()
            await connection.execute(
                "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
                " VALUES (?, ?, 1, 'video', 0)",
                (asset, f"digest-{asset}"),
            )
            await connection.execute(
                "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)", (asset, person)
            )
            await connection.execute(
                "INSERT INTO face_asset_people (asset_id, person_id, created_at) VALUES (?, ?, 0)",
                (asset, person),
            )
    now = 0.0
    told: list[int] = []

    async def touched(asset_ids: Sequence[str]) -> None:
        nonlocal now
        told.append(len(asset_ids))
        now += len(asset_ids) * 0.01

    unnamed = await forget_all(
        temp_db,
        actor=Actor.sift("faces"),
        roots=(),
        touched=touched,
        first=20,
        clock=lambda: now,
    )

    assert unnamed == 300
    assert sum(told) == 300
    assert max(told) * 0.01 <= TURN_SECONDS + 1e-9


async def test_a_sign_in_beside_the_job_answers_within_a_second(
    store: Store, temp_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each row deleted holds the writer 3 ms for real; sign-ins taken while the job runs each
    answer within a second, the password checked and the session written."""
    await _seed_piles(temp_db, 1500)
    auth = AuthService(
        temp_db,
        hasher=Hasher(resolve_argon2_params(None)),
        master_keys=MasterKeyStore(),
        queue=None,
        session_ttl_seconds=3600,
    )
    await auth.create_first_admin("orrin", PASSWORD)
    real = temp_db.write

    @asynccontextmanager
    async def dear() -> AsyncIterator[Connection]:
        async with real() as connection:
            before = connection.total_changes
            yield connection
            await asyncio.sleep((connection.total_changes - before) * 0.003)

    monkeypatch.setattr(temp_db, "write", dear)
    job = asyncio.create_task(forget_all(temp_db, actor=Actor.sift("faces"), roots=()))
    waits: list[float] = []
    while not job.done():
        started = time.perf_counter()
        await auth.login("orrin", PASSWORD)
        waits.append(time.perf_counter() - started)
        await asyncio.sleep(0.2)
    await job

    assert await _piles(temp_db) == 0
    assert len(waits) >= 3
    assert max(waits) < 1.0


async def test_the_job_deletes_in_the_name_of_whoever_pressed(
    service: FaceService, temp_db: Database, actors: Actors
) -> None:
    """History says who deleted face data: the presser the queue hands the job, not Sift."""
    fractions: list[float] = []

    async def told(fraction: float) -> None:
        fractions.append(fraction)

    pressed = SimpleNamespace(pressed_by=actors.admin.id, set_progress=told)
    await forget(pressed, service=service)  # type: ignore[arg-type]

    (event,) = await events_of_entity(temp_db, actors.admin, "setting", "faces.enabled")
    assert (event.verb, event.actor_kind) == ("forgot", "user")
    assert fractions[-1] == 1.0
