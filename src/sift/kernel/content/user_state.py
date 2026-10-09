# SPDX-License-Identifier: AGPL-3.0-or-later
"""What one person thinks of one asset: heart, stars, pin, O count and viewing history.

Rows carry no permissions: every read here is filtered by the access layer."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sift.kernel.changes import AssetOpinion, announce_opinion
from sift.kernel.db import Connection, Database, Row, point_read
from sift.kernel.ids import is_id, new_id

# Out of ten whatever the screens draw, or NULL: zero is not "unrated".
MIN_RATING = 1
MAX_RATING = 10


# A hundred equal fractions of the file, never a duration, so a re-encode keeps its history.
HEAT_BUCKETS = 100


# The resume rule lives here because the player, the grid and search all ask it.

#: A fraction of the length, capped, before a position is worth keeping.
_RESUME_FLOOR_FRACTION = 20
_RESUME_FLOOR_CAP_MS = 5_000

#: How close to the end counts as finished; the same shape.
_RESUME_TAIL_FRACTION = 10
_RESUME_TAIL_CAP_MS = 15_000

#: Spelled here, as the grid and search read the player's preferences and may not import it.
RESUME_ENABLED_KEY = "playback.resume_enabled"
RESUME_MINIMUM_KEY = "playback.resume_minimum_seconds"


async def resume_minimum_ms(
    get_user: Callable[[str, str], Awaitable[Any]], user_id: str
) -> int | None:
    """One user's resume minimum in ms, or None when resuming is off, which keeps nothing."""
    if not await get_user(user_id, RESUME_ENABLED_KEY):
        return None
    return max(0, int(await get_user(user_id, RESUME_MINIMUM_KEY))) * 1000


def resume_point(
    duration_ms: int | None, position_ms: int | None, *, minimum_ms: int | None
) -> int | None:
    """Where reopening this should start, or None for the beginning; the one place that decides."""
    if minimum_ms is None:
        return None
    if position_ms is None or position_ms <= 0:
        return None
    if not duration_ms or duration_ms <= 0:
        return None
    if duration_ms < minimum_ms:
        return None

    floor_ms = min(_RESUME_FLOOR_CAP_MS, duration_ms // _RESUME_FLOOR_FRACTION)
    tail_ms = min(_RESUME_TAIL_CAP_MS, duration_ms // _RESUME_TAIL_FRACTION)
    if position_ms <= floor_ms or position_ms >= duration_ms - tail_ms:
        return None
    return position_ms


#: `resume_point` in SQL for filters, from the same constants and held to it by a test; a NULL
#: minimum is OFF, and every conjunct is two-valued so a negation still means something.
RESUMING = (
    "{minimum} IS NOT NULL"
    " AND {duration} IS NOT NULL AND {duration} > 0"
    " AND {duration} >= {minimum}"
    " AND {position} IS NOT NULL AND {position} > 0"
    f" AND {{position}} > MIN({_RESUME_FLOOR_CAP_MS}, {{duration}} / {_RESUME_FLOOR_FRACTION})"
    f" AND {{position}} < {{duration}} - MIN({_RESUME_TAIL_CAP_MS},"
    f" {{duration}} / {_RESUME_TAIL_FRACTION})"
)


@dataclass(frozen=True, slots=True)
class AssetUserState:
    """One person's opinion of one asset. Absent rows read as this, all-defaults."""

    asset_id: str
    user_id: str
    favorite: bool = False
    rating: int | None = None
    view_count: int = 0
    watched_ms: int = 0
    #: Where to start next time, or None; not `watched_ms`, a running total.
    resume_ms: int | None = None
    last_viewed_at: int | None = None
    #: When first watched through; a NULL resume point cannot tell finished from never started.
    completed_at: int | None = None
    #: Kept at the top of its wall; other kinds' pins are in `entity_state`.
    pinned: bool = False
    #: Times this user pressed the O mark; zero for never, as there is nothing to tell apart.
    o_count: int = 0

    @property
    def finished(self) -> bool:
        """Whether this user has seen all of it, at least once."""
        return self.completed_at is not None

    @property
    def started(self) -> bool:
        """Whether this user has opened it but never seen it through."""
        return self.last_viewed_at is not None and self.completed_at is None


def state_from_row(row: Row) -> AssetUserState:
    return AssetUserState(
        asset_id=row["asset_id"],
        user_id=row["user_id"],
        favorite=bool(row["favorite"]),
        rating=row["rating"],
        view_count=row["view_count"],
        watched_ms=row["watched_ms"],
        resume_ms=row["resume_ms"],
        last_viewed_at=row["last_viewed_at"],
        completed_at=row["completed_at"],
        pinned=bool(row["pinned"]),
        o_count=row["o_count"],
    )


# Each write below replaces what was there, so it also appends an `opinions` row in the same
# transaction, through one helper so a subject kind is spelled one way.


class OpinionKind(StrEnum):
    """The five things a user can think, as the `opinions.kind` CHECK spells them."""

    RATING = "rating"
    FAVORITE = "favorite"
    PIN = "pin"
    #: Spelled out, as a member called `O` reads as a zero.
    O_COUNT = "o"
    #: Concealment of anything; most hides write no ledger event, so this is their record.
    HIDE = "hide"


SUBJECT_ASSET = "asset"

_RECORD_OPINION = """
INSERT INTO opinions (id, user_id, subject_kind, subject_id, kind, before, after, at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

#: The value before the write, read in its transaction: RETURNING gives only the new row.
#: No row is NULL, not zero.
_OPINION_BEFORE = (
    "SELECT favorite, pinned, rating, o_count FROM asset_user_state"
    " WHERE asset_id = ? AND user_id = ?"
)

#: The same over a list; `json_each`, so the statement is never assembled from parts.
_OPINION_BEFORE_MANY = """
SELECT asset_id, favorite, pinned, rating, o_count FROM asset_user_state
WHERE user_id = :user_id AND asset_id IN (SELECT value FROM json_each(:asset_ids))
"""

#: Which column each kind is, read in both directions so the two cannot disagree.
_OPINION_COLUMN: dict[OpinionKind, str] = {
    OpinionKind.FAVORITE: "favorite",
    OpinionKind.PIN: "pinned",
    OpinionKind.RATING: "rating",
    OpinionKind.O_COUNT: "o_count",
}


def _opinion_value(row: Row | None, kind: OpinionKind) -> int | None:
    """One kind's value out of an `asset_user_state` row, as the integer the table stores."""
    if row is None:
        return None
    value = row[_OPINION_COLUMN[kind]]
    return None if value is None else int(value)


async def record_opinion(
    connection: Connection,
    *,
    user_id: str,
    subject_kind: str,
    subject_id: str,
    kind: OpinionKind,
    before: int | None,
    after: int | None,
    at: int,
) -> None:
    """Append one opinion row on the caller's connection, so it rolls back with the press."""
    await connection.execute(
        _RECORD_OPINION,
        (new_id(), user_id, subject_kind, subject_id, kind.value, before, after, at),
    )


# Upserts on the natural key, written out longhand: nothing here is built from parts.
_SET_FAVORITE = """
INSERT INTO asset_user_state (asset_id, user_id, favorite, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(asset_id, user_id) DO UPDATE SET
    favorite   = excluded.favorite,
    updated_at = excluded.updated_at
RETURNING *
"""

# Touches one column only, so pinning disturbs nothing else.
_SET_PINNED = """
INSERT INTO asset_user_state (asset_id, user_id, pinned, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(asset_id, user_id) DO UPDATE SET
    pinned     = excluded.pinned,
    updated_at = excluded.updated_at
RETURNING *
"""

_SET_RATING = """
INSERT INTO asset_user_state (asset_id, user_id, rating, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(asset_id, user_id) DO UPDATE SET
    rating     = excluded.rating,
    updated_at = excluded.updated_at
RETURNING *
"""

# Three statements, as one taking a delta cannot clamp both arms; arithmetic in SQL so two
# tabs pressing together lose nothing.
_BUMP_O_COUNT = """
INSERT INTO asset_user_state (asset_id, user_id, o_count, updated_at)
VALUES (?, ?, 1, ?)
ON CONFLICT(asset_id, user_id) DO UPDATE SET
    o_count    = asset_user_state.o_count + 1,
    updated_at = excluded.updated_at
RETURNING *
"""

# Counts from another library are raised to it, never lowered, so a rerun is harmless.
_CARRY_COUNTS = """
INSERT INTO asset_user_state (asset_id, user_id, o_count, view_count, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(asset_id, user_id) DO UPDATE SET
    o_count    = MAX(asset_user_state.o_count, excluded.o_count),
    view_count = MAX(asset_user_state.view_count, excluded.view_count),
    updated_at = excluded.updated_at
RETURNING *
"""

# Never below nothing, in SQL; a missing row is inserted at zero so the write still answers.
_LOWER_O_COUNT = """
INSERT INTO asset_user_state (asset_id, user_id, o_count, updated_at)
VALUES (?, ?, 0, ?)
ON CONFLICT(asset_id, user_id) DO UPDATE SET
    o_count    = MAX(0, asset_user_state.o_count - 1),
    updated_at = excluded.updated_at
RETURNING *
"""

# Back to nothing: a replacement, not arithmetic.
_RESET_O_COUNT = """
INSERT INTO asset_user_state (asset_id, user_id, o_count, updated_at)
VALUES (?, ?, 0, ?)
ON CONFLICT(asset_id, user_id) DO UPDATE SET
    o_count    = 0,
    updated_at = excluded.updated_at
RETURNING *
"""

# The same writes over a list, one statement each, the ids as one JSON parameter.
# `WHERE true` is required: without it SQLite cannot parse an upsert after a SELECT.
_SET_FAVORITE_MANY = """
INSERT INTO asset_user_state (asset_id, user_id, favorite, updated_at)
SELECT value, :user_id, :favorite, :now FROM json_each(:asset_ids)
WHERE true
ON CONFLICT(asset_id, user_id) DO UPDATE SET
    favorite   = excluded.favorite,
    updated_at = excluded.updated_at
RETURNING *
"""

_SET_PINNED_MANY = """
INSERT INTO asset_user_state (asset_id, user_id, pinned, updated_at)
SELECT value, :user_id, :pinned, :now FROM json_each(:asset_ids)
WHERE true
ON CONFLICT(asset_id, user_id) DO UPDATE SET
    pinned     = excluded.pinned,
    updated_at = excluded.updated_at
RETURNING *
"""

_SET_RATING_MANY = """
INSERT INTO asset_user_state (asset_id, user_id, rating, updated_at)
SELECT value, :user_id, :rating, :now FROM json_each(:asset_ids)
WHERE true
ON CONFLICT(asset_id, user_id) DO UPDATE SET
    rating     = excluded.rating,
    updated_at = excluded.updated_at
RETURNING *
"""

# Counts accumulate in SQL; resume_ms is replaced, NULL included; completed_at is set once.
_RECORD_VIEW = """
INSERT INTO asset_user_state
    (asset_id, user_id, view_count, watched_ms, resume_ms, last_viewed_at, completed_at, updated_at)
VALUES (?, ?, 1, ?, ?, ?, ?, ?)
ON CONFLICT(asset_id, user_id) DO UPDATE SET
    view_count     = asset_user_state.view_count + 1,
    watched_ms     = asset_user_state.watched_ms + excluded.watched_ms,
    resume_ms      = excluded.resume_ms,
    last_viewed_at = excluded.last_viewed_at,
    completed_at   = COALESCE(asset_user_state.completed_at, excluded.completed_at),
    updated_at     = excluded.updated_at
RETURNING *
"""

# Time watched without counting a view, so short sittings still add time and a resume point;
# `last_viewed_at` is left alone, and `completed_at` is set once as above.
_RECORD_WATCH_TIME = """
INSERT INTO asset_user_state
    (asset_id, user_id, watched_ms, resume_ms, completed_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(asset_id, user_id) DO UPDATE SET
    watched_ms   = asset_user_state.watched_ms + excluded.watched_ms,
    resume_ms    = excluded.resume_ms,
    completed_at = COALESCE(asset_user_state.completed_at, excluded.completed_at),
    updated_at   = excluded.updated_at
RETURNING *
"""

# One slice for one user, added to in SQL, as two players on one clip would lose a merge.
_ADD_HEAT = """
INSERT INTO asset_replay_heat (asset_id, user_id, bucket, watched_ms, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(asset_id, user_id, bucket) DO UPDATE SET
    watched_ms = asset_replay_heat.watched_ms + excluded.watched_ms,
    updated_at = ?
"""

_HEAT_OF = """
SELECT bucket, watched_ms FROM asset_replay_heat
WHERE asset_id = ? AND user_id = ?
ORDER BY bucket
"""

_STATE_OF = point_read(
    "content.asset_state", "SELECT * FROM asset_user_state WHERE asset_id = ? AND user_id = ?"
)
_STATES_OF = point_read(
    "content.asset_states",
    "SELECT s.* FROM json_each(?) w"
    " JOIN asset_user_state s ON s.asset_id = w.value AND s.user_id = ?",
)


class UserStateStore:
    """One person's favorites, ratings and watch history. One per database."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = database
        self._clock = clock

    def _now(self) -> int:
        return int(self._clock())

    async def set_favorite(self, asset_id: str, user_id: str, favorite: bool) -> AssetUserState:
        """Heart it, or take the heart off."""
        _check_ids(asset_id, user_id)
        return await self._write_state(
            _SET_FAVORITE,
            (asset_id, user_id, int(favorite), self._now()),
            records=OpinionKind.FAVORITE,
        )

    async def set_pinned(self, asset_id: str, user_id: str, pinned: bool) -> AssetUserState:
        """Keep this file at the top of its wall, or take the pin off; independent of the heart."""
        _check_ids(asset_id, user_id)
        return await self._write_state(
            _SET_PINNED,
            (asset_id, user_id, int(pinned), self._now()),
            records=OpinionKind.PIN,
        )

    async def set_rating(self, asset_id: str, user_id: str, rating: int | None) -> AssetUserState:
        """Set a star rating, or clear it with None; zero is refused, never read as unrated."""
        _check_ids(asset_id, user_id)
        if rating is not None and not MIN_RATING <= rating <= MAX_RATING:
            raise ValueError(f"a rating is {MIN_RATING} to {MAX_RATING}, or None to clear it")
        return await self._write_state(
            _SET_RATING, (asset_id, user_id, rating, self._now()), records=OpinionKind.RATING
        )

    async def bump_o_count(self, asset_id: str, user_id: str) -> AssetUserState:
        """One more O press, per user like the heart and the stars."""
        _check_ids(asset_id, user_id)
        return await self._write_state(
            _BUMP_O_COUNT, (asset_id, user_id, self._now()), records=OpinionKind.O_COUNT
        )

    async def carry_counts(
        self, asset_id: str, user_id: str, *, o_count: int, views: int
    ) -> AssetUserState:
        """Raise the O and view counts to another library's, never lowering; no dates come."""
        _check_ids(asset_id, user_id)
        return await self._write_state(
            _CARRY_COUNTS,
            (asset_id, user_id, max(0, o_count), max(0, views), self._now()),
            records=OpinionKind.O_COUNT,
        )

    async def lower_o_count(self, asset_id: str, user_id: str) -> AssetUserState:
        """One fewer, never below nothing, as cheap as the press it takes back."""
        _check_ids(asset_id, user_id)
        return await self._write_state(
            _LOWER_O_COUNT, (asset_id, user_id, self._now()), records=OpinionKind.O_COUNT
        )

    async def reset_o_count(self, asset_id: str, user_id: str) -> AssetUserState:
        """Back to nothing, in one act rather than in as many presses as it took to get here."""
        _check_ids(asset_id, user_id)
        return await self._write_state(
            _RESET_O_COUNT, (asset_id, user_id, self._now()), records=OpinionKind.O_COUNT
        )

    async def set_favorite_many(
        self, asset_ids: Sequence[str], user_id: str, favorite: bool
    ) -> int:
        """Heart or unheart a selection in one statement; the ids must already be access-checked."""
        written = await self._write_many(
            _SET_FAVORITE_MANY,
            asset_ids,
            user_id,
            {"favorite": int(favorite)},
            records=OpinionKind.FAVORITE,
        )
        return len(written)

    async def set_pinned_many(self, asset_ids: Sequence[str], user_id: str, pinned: bool) -> int:
        """Pin a whole selection, or take the pins off. Answers how many rows moved."""
        written = await self._write_many(
            _SET_PINNED_MANY, asset_ids, user_id, {"pinned": int(pinned)}, records=OpinionKind.PIN
        )
        return len(written)

    async def set_rating_many(
        self, asset_ids: Sequence[str], user_id: str, rating: int | None
    ) -> int:
        """Set one rating across a selection, or clear it with None; zero is refused."""
        if rating is not None and not MIN_RATING <= rating <= MAX_RATING:
            raise ValueError(f"a rating is {MIN_RATING} to {MAX_RATING}, or None to clear it")
        written = await self._write_many(
            _SET_RATING_MANY, asset_ids, user_id, {"rating": rating}, records=OpinionKind.RATING
        )
        return len(written)

    async def record_view(
        self,
        asset_id: str,
        user_id: str,
        *,
        watch_ms: int = 0,
        resume_ms: int | None = None,
        completed: bool = False,
    ) -> AssetUserState:
        """Count a view and add time; `resume_ms` replaces (None clears), `completed` only adds."""
        _check_ids(asset_id, user_id)
        if watch_ms < 0:
            raise ValueError("time watched cannot be negative")
        if resume_ms is not None and resume_ms < 0:
            raise ValueError("a resume position cannot be negative")
        now = self._now()
        return await self._write_state(
            _RECORD_VIEW,
            (asset_id, user_id, watch_ms, resume_ms, now, now if completed else None, now),
        )

    async def record_watch_time(
        self,
        asset_id: str,
        user_id: str,
        *,
        watch_ms: int,
        resume_ms: int | None = None,
        completed: bool = False,
    ) -> AssetUserState:
        """Add time and a resume point without counting a view; an empty sitting writes nothing."""
        _check_ids(asset_id, user_id)
        if watch_ms < 0:
            raise ValueError("time watched cannot be negative")
        if resume_ms is not None and resume_ms < 0:
            raise ValueError("a resume position cannot be negative")
        if watch_ms == 0 and resume_ms is None and not completed:
            return await self.state_of(asset_id, user_id)
        now = self._now()
        return await self._write_state(
            _RECORD_WATCH_TIME,
            (asset_id, user_id, watch_ms, resume_ms, now if completed else None, now),
        )

    async def add_replay_heat(
        self, asset_id: str, user_id: str, buckets: Mapping[int, int]
    ) -> None:
        """Add time to the slices on screen in one sitting; out-of-range slices are dropped."""
        _check_ids(asset_id, user_id)
        now = self._now()
        wanted = [
            (asset_id, user_id, bucket, milliseconds, now, now)
            for bucket, milliseconds in sorted(buckets.items())
            if 0 <= bucket < HEAT_BUCKETS and milliseconds > 0
        ]
        if not wanted:
            return
        # One write for the whole sitting, never a commit per slice.
        async with self._db.write() as connection:
            await connection.executemany(_ADD_HEAT, wanted)

    async def replay_heat(self, asset_id: str, user_id: str) -> list[int]:
        """Time on screen per slice for this user, always `HEAT_BUCKETS` long, zeros included."""
        if not is_id(asset_id) or not is_id(user_id):
            return [0] * HEAT_BUCKETS
        rows = await self._db.fetch_all(_HEAT_OF, (asset_id, user_id))
        heat = [0] * HEAT_BUCKETS
        for row in rows:
            bucket = int(row["bucket"])
            if 0 <= bucket < HEAT_BUCKETS:
                heat[bucket] = int(row["watched_ms"])
        return heat

    async def state_of(self, asset_id: str, user_id: str) -> AssetUserState:
        """This person's state for this asset; an untouched asset reads as defaults, never None."""
        if not is_id(asset_id) or not is_id(user_id):
            return AssetUserState(asset_id=asset_id, user_id=user_id)
        row = await self._db.fetch_one(_STATE_OF, (asset_id, user_id))
        if row is None:
            return AssetUserState(asset_id=asset_id, user_id=user_id)
        return state_from_row(row)

    async def states_of(self, asset_ids: Sequence[str], user_id: str) -> dict[str, AssetUserState]:
        """One person's state for a page of assets in one statement; rows absent are left out."""
        wanted = [asset_id for asset_id in asset_ids if is_id(asset_id)]
        if not wanted or not is_id(user_id):
            return {}
        rows = await self._db.fetch_all(_STATES_OF, (json.dumps(wanted), user_id))
        return {row["asset_id"]: state_from_row(row) for row in rows}

    async def _write_state(
        self, sql: str, params: tuple[object, ...], *, records: OpinionKind | None = None
    ) -> AssetUserState:
        """Write one row, record its opinion in one transaction, tell this user's screens."""
        # The asset and user are the first two bound values of every statement here.
        async with self._db.write() as connection:
            before: int | None = None
            if records is not None:
                # Before the upsert, which destroys this value; RETURNING cannot give it.
                was = list(
                    await connection.execute_fetchall(_OPINION_BEFORE, (params[0], params[1]))
                )
                before = _opinion_value(was[0] if was else None, records)
            rows = list(await connection.execute_fetchall(sql, params))
            state = state_from_row(rows[0])
            if records is not None:
                await record_opinion(
                    connection,
                    user_id=state.user_id,
                    subject_kind=SUBJECT_ASSET,
                    subject_id=state.asset_id,
                    kind=records,
                    before=before,
                    after=_opinion_value(rows[0], records),
                    at=self._now(),
                )
            opinion = AssetOpinion(
                asset_id=state.asset_id,
                favorite=state.favorite,
                rating=state.rating,
                views=state.view_count,
                pinned=state.pinned,
                o_count=state.o_count,
            )
            announce_opinion(state.user_id, opinion)
        return state

    async def _write_many(
        self,
        sql: str,
        asset_ids: Sequence[str],
        user_id: str,
        value: Mapping[str, object],
        *,
        records: OpinionKind,
    ) -> list[AssetUserState]:
        """`_write_state` over a de-duplicated list in one statement, one opinion per row."""
        wanted = list(dict.fromkeys(asset_ids))
        for asset_id in wanted:
            _check_ids(asset_id, user_id)
        if not wanted:
            return []
        params: dict[str, object] = {
            "asset_ids": json.dumps(wanted),
            "user_id": user_id,
            "now": self._now(),
            **value,
        }
        async with self._db.write() as connection:
            was: dict[str, Row] = {
                str(row["asset_id"]): row
                for row in await connection.execute_fetchall(
                    _OPINION_BEFORE_MANY,
                    {"asset_ids": params["asset_ids"], "user_id": user_id},
                )
            }
            rows = list(await connection.execute_fetchall(sql, params))
            written = [state_from_row(row) for row in rows]
            at = self._now()
            for row in rows:
                await record_opinion(
                    connection,
                    user_id=user_id,
                    subject_kind=SUBJECT_ASSET,
                    subject_id=str(row["asset_id"]),
                    kind=records,
                    before=_opinion_value(was.get(str(row["asset_id"])), records),
                    after=_opinion_value(row, records),
                    at=at,
                )
            for state in written:
                announce_opinion(
                    state.user_id,
                    AssetOpinion(
                        asset_id=state.asset_id,
                        favorite=state.favorite,
                        rating=state.rating,
                        views=state.view_count,
                        pinned=state.pinned,
                        o_count=state.o_count,
                    ),
                )
        return written


def _check_ids(asset_id: str, user_id: str) -> None:
    """Refuse a malformed id before it reaches a statement."""
    if not is_id(asset_id):
        raise ValueError("not an asset id")
    if not is_id(user_id):
        raise ValueError("not a user id")
