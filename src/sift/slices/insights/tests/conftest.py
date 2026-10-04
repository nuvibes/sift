# SPDX-License-Identifier: AGPL-3.0-or-later
"""A real database with every table the application has, and a few rows written by hand.

Every component registered, because the statements under test read a dozen tables owned by other
slices (`plays`, `theater_sessions`, `opinions`, the ledger, the vault's stored verdict) and
the point is that they parse and answer against the real shapes, not against a copy of them.

The vault's verdict (`viewer_assets`) is written directly: it is kept by triggers from folders and
grants, and what is under test here is how Insights reads it, not how it is worked out.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pytest

import sift.main  # noqa: F401 (every component registers its tables on import)
from sift.kernel.db import Database
from sift.slices.insights.store import day_bounds

DAY = date(2026, 3, 11)
START, END = day_bounds(DAY)


def at(hour: int, minute: int = 0, day: date = DAY) -> int:
    """A moment of a day, on this device's clock, as the tables store it (seconds)."""
    return int(datetime(day.year, day.month, day.day, hour, minute).timestamp())


@dataclass
class World:
    """The database, and the writes the tests make to it."""

    db: Database
    user: str = "u-viewer"
    _n: int = 0
    #: Each file's kind and length, which a sitting of it writes down the way the player does.
    _files: dict[str, tuple[str, int | None]] = field(default_factory=dict)

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}-{self._n:04d}"

    async def run(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        async with self.db.write() as connection:
            await connection.execute(sql, params)

    async def add_user(self, user_id: str, *, created_at: int = 1) -> str:
        await self.run(
            "INSERT INTO users (id, username, password_hash, role, created_at)"
            " VALUES (?, ?, 'not-a-hash', 'admin', ?)",
            (user_id, "viewer-" + user_id, created_at),
        )
        return user_id

    async def add_file(
        self, media_type: str = "video", *, length_ms: int | None = 600_000, hidden: bool = False
    ) -> str:
        asset_id = self._id("a")
        await self.run(
            "INSERT INTO assets (id, identity, media_type, duration_ms, added_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (asset_id, "identity-" + asset_id, media_type, length_ms, at(1)),
        )
        self._files[asset_id] = (media_type, length_ms)
        await self.set_hidden(asset_id, hidden)
        return asset_id

    async def set_hidden(self, asset_id: str, hidden: bool) -> None:
        await self.run(
            "INSERT OR REPLACE INTO viewer_assets (user_id, asset_id, concealed) VALUES (?, ?, ?)",
            (self.user, asset_id, 1 if hidden else 0),
        )

    async def bump_stamp(self) -> None:
        await self.run("UPDATE users SET cache_stamp = cache_stamp + 1 WHERE id = ?", (self.user,))

    async def sit(
        self,
        asset_id: str,
        started_at: int,
        watched_ms: int,
        *,
        screen: str | None = None,
        theater_session: str | None = None,
        kind: str | None = None,
    ) -> str:
        play_id = self._id("p")
        # What the sitting wrote down about its file: the player records both from the file at
        # the moment the sitting began, and a reader of sittings reads them from the sitting.
        file_kind, length_ms = self._files.get(asset_id, ("video", None))
        await self.run(
            "INSERT INTO plays (id, user_id, asset_id, started_at, duration_ms, made_at,"
            " screen, kind, theater_session, length_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                play_id,
                self.user,
                asset_id,
                started_at,
                watched_ms,
                started_at + watched_ms // 1000,
                screen,
                kind if kind is not None else file_kind,
                theater_session,
                length_ms,
            ),
        )
        return play_id

    async def wall(
        self, session: str, started_at: int, ended_at: int | None, arrangement: str | None = None
    ) -> None:
        await self.run(
            "INSERT INTO theater_sessions (id, user_id, session, started_at, ended_at,"
            " arrangement_id, made_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self._id("t"), self.user, session, started_at, ended_at, arrangement, started_at),
        )

    async def person_on(self, asset_id: str, person_id: str, *, hidden: bool = False) -> None:
        await self.run(
            "INSERT OR IGNORE INTO people (id, name, created_at) VALUES (?, ?, 1)",
            (person_id, "Person " + person_id),
        )
        await self.run(
            "INSERT INTO asset_people (asset_id, person_id, source, decided_at)"
            " VALUES (?, ?, NULL, 1)",
            (asset_id, person_id),
        )
        await self.hide_person(person_id, hidden)

    async def hide_person(self, person_id: str, hidden: bool) -> None:
        await self.run(
            "INSERT INTO person_user_state (person_id, user_id, hidden, updated_at)"
            " VALUES (?, ?, ?, 1) ON CONFLICT DO UPDATE SET hidden = excluded.hidden",
            (person_id, self.user, 1 if hidden else 0),
        )


@pytest.fixture
async def world(temp_db: Database) -> AsyncIterator[World]:
    await temp_db.initialize_schema()
    made = World(temp_db)
    await made.add_user(made.user)
    yield made
