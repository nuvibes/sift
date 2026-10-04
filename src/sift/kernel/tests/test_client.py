# SPDX-License-Identifier: AGPL-3.0-or-later
"""The client of a request, the act it stamps, and a User's own history paused and cleared."""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

import sift.main  # noqa: F401 (every component registers its tables on import)
from sift.kernel import client, use_history
from sift.kernel.client import UNKNOWN, Client, client_of, device_from, kind_from, new_device
from sift.kernel.db import Connection, Database
from sift.kernel.ledger import Actor, record_event
from sift.kernel.vocabulary import Subject
from sift.slices.workbench import schema as workbench_schema

pytestmark = pytest.mark.unit

USER = "u-stamped"


@dataclass
class _Asked:
    """A request as `client_of` reads it: headers and cookies."""

    headers: Mapping[str, str] = field(default_factory=dict)
    cookies: Mapping[str, str] = field(default_factory=dict)


def test_the_kind_is_the_word_the_page_said_or_unknown() -> None:
    assert kind_from("phone") == "phone"
    assert kind_from(" Tablet ") == "tablet"
    assert kind_from("a-toaster") == "unknown"
    assert kind_from(None) == "unknown"


def test_a_device_is_the_shape_sift_mints_and_nothing_else() -> None:
    minted = new_device()
    assert device_from(minted) == minted
    assert device_from("short") is None
    assert device_from("has spaces in it and more") is None
    assert device_from(None) is None
    assert new_device() != minted


def test_the_client_of_a_request_reads_the_header_and_the_cookie() -> None:
    minted = new_device()
    asked = _Asked(
        headers={client.CLIENT_HEADER: "app"}, cookies={client.DEVICE_COOKIE_NAME: minted}
    )
    assert client_of(asked) == Client("app", minted)
    assert client_of(_Asked()) == UNKNOWN


async def test_entered_is_the_requests_own_and_none_outside_one() -> None:
    async def served() -> Client | None:
        client.enter(Client("phone", "a-device-of-sixteen"))
        return client.current()

    # Each request is served in a task of its own, so what one enters the next never reads.
    assert await asyncio.create_task(served(), context=contextvars.Context()) == Client(
        "phone", "a-device-of-sixteen"
    )
    assert client.current() is None


@pytest.fixture
async def database(temp_db: Database) -> Database:
    await temp_db.initialize_schema()
    async with temp_db.write() as connection:
        await connection.execute(
            "INSERT INTO users (id, username, password_hash, role, created_at)"
            " VALUES (?, ?, 'not-a-hash', 'admin', 1)",
            (USER, USER),
        )
    return temp_db


async def _act(database: Database, actor: Actor) -> dict[str, Any]:
    async with database.write() as connection:
        event = await record_event(
            connection,
            actor=actor,
            verb="edited",
            subject=Subject(kind="setting", id="a-setting", name="A setting"),
        )
    rows = await database.fetch_all(
        "SELECT client_kind, device_id FROM workbench_decisions WHERE id = ?", (event,)
    )
    return dict(rows[0])


async def test_an_act_somebody_takes_carries_the_window_and_device_it_came_from(
    database: Database,
) -> None:
    async def served() -> dict[str, Any]:
        client.enter(Client("tablet", "a-device-of-sixteen"))
        return await _act(database, Actor.user(USER))

    stamped = await asyncio.create_task(served(), context=contextvars.Context())
    assert stamped == {"client_kind": "tablet", "device_id": "a-device-of-sixteen"}


async def test_an_act_of_sifts_own_is_stamped_with_nothing(database: Database) -> None:
    async def served() -> dict[str, Any]:
        client.enter(Client("tablet", "a-device-of-sixteen"))
        return await _act(database, Actor.sift("update"))

    stamped = await asyncio.create_task(served(), context=contextvars.Context())
    assert stamped == {"client_kind": None, "device_id": None}
    assert await _act(database, Actor.user(USER)) == {"client_kind": None, "device_id": None}


async def test_the_workbench_step_adds_the_two_columns_once(temp_db: Database) -> None:
    async with temp_db.write() as connection:
        await connection.execute(
            "CREATE TABLE workbench_decisions (id TEXT PRIMARY KEY, queue TEXT NOT NULL,"
            " title TEXT NOT NULL, detail TEXT NOT NULL, payload TEXT NOT NULL,"
            " decided_at INTEGER NOT NULL)"
        )
        await workbench_schema.initialize_workbench(connection, 15)
        await workbench_schema.initialize_workbench(connection, 15)
    columns = {
        str(row["name"])
        for row in await temp_db.fetch_all(
            "SELECT name FROM pragma_table_info('workbench_decisions')"
        )
    }
    assert {"client_kind", "device_id"} <= columns


@dataclass
class _Preferences:
    keep: object

    async def get_app(self, key: str) -> Any:
        raise AssertionError(key)

    async def get_user(self, user_id: str, key: str) -> Any:
        assert key == use_history.RECORD_KEY
        return self.keep


async def test_the_pause_is_read_from_the_users_own_setting() -> None:
    assert await use_history.keeps_history(_Preferences(True), USER)
    assert not await use_history.keeps_history(_Preferences(False), USER)


def test_a_clearing_is_registered_once() -> None:
    async def clearing(connection: Connection, user_id: str) -> int:
        return 0

    with pytest.raises(ValueError, match="registered twice"):
        use_history.register_clearing("insights", clearing)
    assert {"insights", "search"} <= set(use_history.registered_clearings())


async def test_clearing_runs_every_part_in_one_write(database: Database) -> None:
    cleared = await use_history.clear_history_of(database, USER)
    assert set(cleared) == set(use_history.registered_clearings())
    assert all(gone == 0 for gone in cleared.values())
