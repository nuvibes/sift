# SPDX-License-Identifier: AGPL-3.0-or-later
"""Writing a sitting down, and reading back what a file has been watched."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Literal

from sift.kernel.db import Connection, Database, Row
from sift.kernel.ids import new_id
from sift.kernel.use_history import register_clearing

#: Which screen a sitting happened on; `schema.py`'s CHECK spells the same list, held by a test.
#: Where the panel was opened from is `OpenedFrom`.
Screen = Literal["panel", "corner", "theater"]

#: The screen a panel was opened over; `link` is an address with nothing behind it.
OpenedFrom = Literal[
    "library",
    "search",
    "favorites",
    "recent",
    "loops",
    "person",
    "site",
    "tag",
    "collection",
    "photo_set",
    "song",
    "organize",
    "downloads",
    "hidden",
    "start",
    "link",
    "folder",
    "insights",
    "other",
]

#: What the file was when watched, taken then, since a sitting outlives its file.
Kind = Literal["video", "image", "gif"]


@dataclass(frozen=True, slots=True)
class Place:
    """Where one sitting happened, written once with its first piece; NULL where never reported."""

    screen: Screen | None = None
    opened_from: OpenedFrom | None = None
    kind: Kind | None = None
    #: The saved Loop it was opened from: an id no key reaches, since the sitting outlives the Loop.
    loop_id: str | None = None
    kept_filter_id: str | None = None
    #: The Theater session, the `session` column of `theater_sessions`.
    theater_session: str | None = None
    #: The one thing the screen behind the file was about, as `opened_from` names it.
    opened_from_id: str | None = None
    #: The client the sitting happened on (`kernel/client.py`).
    device_id: str | None = None
    client_kind: str | None = None


NOWHERE = Place()

#: The most seeks one sitting keeps as pairs; `seeks` goes on counting past it.
MOST_SEEKS = 64

#: The most speeds one sitting keeps: a bound on what a browser can send.
MOST_SPEEDS = 16


@dataclass(frozen=True, slots=True)
class Inside:
    """What happened inside one piece of a sitting; None where the client did not say."""

    #: Written by the first piece only.
    start_ms: int | None = None
    seek_log: tuple[tuple[int, int], ...] | None = None
    speeds: dict[float, int] | None = None
    fullscreen_ms: int | None = None
    completions: int | None = None
    magnified: bool | None = None


NOTHING_INSIDE = Inside()


@dataclass(frozen=True, slots=True)
class Named:
    """One thing a sitting was about: its id and what it was called then."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class About:
    """Who and what the file carried when the sitting began, as this User could be shown it."""

    people: tuple[Named, ...] = ()
    tags: tuple[Named, ...] = ()
    sites: tuple[Named, ...] = ()
    song: Named | None = None

    def packed(self) -> str:
        """The ids, as `plays.about` keeps them: a kind with nothing on it is left out."""
        lists = {"people": self.people, "tags": self.tags, "sites": self.sites}
        packed: dict[str, object] = {
            key: [one.id for one in got] for key, got in lists.items() if got
        }
        if self.song is not None:
            packed["song"] = self.song.id
        return json.dumps(packed, separators=(",", ":"))

    def names(self) -> list[tuple[str, Named]]:
        """Every thing named, with the kind `play_names` keeps it under."""
        named = [("person", one) for one in self.people]
        named += [("tag", one) for one in self.tags]
        named += [("site", one) for one in self.sites]
        if self.song is not None:
            named.append(("song", self.song))
        return named


_RECORD = (
    "INSERT INTO plays"
    " (id, user_id, asset_id, sitting, started_at, duration_ms, ended_at_ms, heat, seeks,"
    " made_at, screen, opened_from, kind, loop_id, kept_filter_id, theater_session, length_ms,"
    " opened_from_id, start_ms, seek_log, speeds, fullscreen_ms, completions, magnified,"
    " device_id, client_kind, about)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

#: One row by construction (the partial unique index in `schema.py`).
_SITTING_SO_FAR = (
    "SELECT id, duration_ms, heat, seeks, seek_log, speeds FROM plays"
    " WHERE user_id = ? AND asset_id = ? AND sitting = ?"
)

#: Whether the route has to read what the file carries before the write.
_SITTING_EXISTS = "SELECT 1 FROM plays WHERE user_id = ? AND asset_id = ? AND sitting = ?"

#: One more piece: time and inside counts add, position replaces, `started_at` stays, magnified ORs.
_EXTEND = (
    "UPDATE plays"
    " SET duration_ms = duration_ms + :watch_ms, ended_at_ms = :position_ms, heat = :heat,"
    " seeks = seeks + :seeks, made_at = :made_at, seek_log = :seek_log, speeds = :speeds,"
    " fullscreen_ms = CASE WHEN :fullscreen_ms IS NULL THEN fullscreen_ms"
    "   ELSE COALESCE(fullscreen_ms, 0) + :fullscreen_ms END,"
    " completions = CASE WHEN :completions IS NULL THEN completions"
    "   ELSE COALESCE(completions, 0) + :completions END,"
    " magnified = CASE WHEN :magnified IS NULL THEN magnified"
    "   ELSE MAX(COALESCE(magnified, 0), :magnified) END"
    " WHERE id = :id"
)

#: A thing's name for this User, written only when not already their latest for it.
_NOTE_NAME = (
    "INSERT INTO play_names (user_id, kind, ref, name, since)"
    " SELECT :user_id, :kind, :ref, :name, :since"
    " WHERE COALESCE((SELECT name FROM play_names"
    "   WHERE user_id = :user_id AND kind = :kind AND ref = :ref"
    "   ORDER BY since DESC, rowid DESC LIMIT 1), '') <> :name"
)

#: A User's part of their history, cleared on Privacy's Clear: sittings, names and Theater sessions.
_CLEAR = (
    "DELETE FROM plays WHERE user_id = ?",
    "DELETE FROM play_names WHERE user_id = ?",
    "DELETE FROM theater_sessions WHERE user_id = ?",
)

_OF_ASSET = (
    "SELECT COUNT(*) AS sittings, COALESCE(SUM(duration_ms), 0) AS watched_ms,"
    " MAX(started_at) AS last_at"
    " FROM plays WHERE asset_id = ? AND user_id = ?"
)

_MS_PER_SECOND = 1000


@dataclass(frozen=True, slots=True)
class Watched:
    """How much of one file's viewing this user has behind it."""

    sittings: int
    watched_ms: int
    last_at: int | None


def _heat_json(heat: dict[int, int]) -> str | None:
    """The replay map as stored: sparse by slice index, NULL where there was none."""
    return json.dumps({str(index): ms for index, ms in sorted(heat.items())}) if heat else None


def _merged_heat(stored: str | None, arriving: dict[int, int]) -> dict[int, int]:
    """One sitting's replay map so far plus the arriving piece, added slice by slice."""
    merged: dict[int, int] = {}
    if stored:
        try:
            for index, spent in json.loads(stored).items():
                merged[int(index)] = int(spent)
        except (ValueError, TypeError, AttributeError):
            merged = {}
    for index, spent in arriving.items():
        merged[index] = merged.get(index, 0) + spent
    return merged


def _rate_key(rate: float) -> str:
    """A speed as `speeds` keys it: the shortest decimal, so 1.0 and 1 are one key."""
    return f"{rate:g}"


def _merged_seeks(stored: str | None, arriving: tuple[tuple[int, int], ...] | None) -> str | None:
    """The sitting's seeks so far plus this piece's, up to `MOST_SEEKS`; NULL until a piece says."""
    if arriving is None:
        return stored
    kept: list[list[int]] = []
    if stored:
        try:
            kept = [[int(pair[0]), int(pair[1])] for pair in json.loads(stored)]
        except (ValueError, TypeError, KeyError, IndexError):
            kept = []
    room = max(0, MOST_SEEKS - len(kept))
    kept += [[start, end] for start, end in arriving[:room]]
    return json.dumps(kept, separators=(",", ":"))


def _merged_speeds(stored: str | None, arriving: dict[float, int] | None) -> str | None:
    """The time at each speed so far, plus this piece's, added speed by speed."""
    if arriving is None:
        return stored
    merged: dict[str, int] = {}
    if stored:
        try:
            merged = {str(key): int(spent) for key, spent in json.loads(stored).items()}
        except (ValueError, TypeError, AttributeError):
            merged = {}
    for rate, spent in arriving.items():
        key = _rate_key(rate)
        if key in merged or len(merged) < MOST_SPEEDS:
            merged[key] = merged.get(key, 0) + spent
    return json.dumps(merged, separators=(",", ":"), sort_keys=True)


def _flag(value: bool | None) -> int | None:
    return None if value is None else int(value)


async def sitting_is_here(database: Database, user_id: str, asset_id: str, sitting: str) -> bool:
    """Whether this sitting's first piece is already written, so a later piece is not the first."""
    return await database.fetch_one(_SITTING_EXISTS, (user_id, asset_id, sitting)) is not None


async def clear_plays(connection: Connection, user_id: str) -> int:
    """The player's part of clearing a User's history (`kernel/use_history.py`): their sittings and
    the names kept for them. Answers how many rows went."""
    gone = 0
    for statement in _CLEAR:
        cursor = await connection.execute(statement, (user_id,))
        gone += int(cursor.rowcount)
    return gone


async def record_play(
    database: Database,
    *,
    user_id: str,
    asset_id: str,
    watch_ms: int,
    already_reported_ms: int | None,
    position_ms: int | None,
    heat: dict[int, int],
    sitting: str | None = None,
    seeks: int = 0,
    place: Place = NOWHERE,
    length_ms: int | None = None,
    inside: Inside = NOTHING_INSIDE,
    about: About | None = None,
) -> None:
    """Write down one sitting, exactly as it was reported: facts only, the route draws conclusions.

    A sitting in several pieces is one row; `started_at`, `place` and `about` come from the first.
    """
    made_at = int(time.time())
    whole_ms = (already_reported_ms or 0) + watch_ms
    async with database.write() as connection:
        so_far = await _so_far(connection, user_id, asset_id, sitting)
        if so_far is not None:
            await _extend(
                connection,
                so_far,
                watch_ms=watch_ms,
                position_ms=position_ms,
                heat=heat,
                seeks=seeks,
                made_at=made_at,
                inside=inside,
            )
            return
        await connection.execute(
            _RECORD,
            (
                new_id(),
                user_id,
                asset_id,
                sitting,
                made_at - whole_ms // _MS_PER_SECOND,
                watch_ms,
                position_ms,
                _heat_json(heat),
                max(0, seeks),
                made_at,
                place.screen,
                place.opened_from,
                place.kind,
                place.loop_id,
                place.kept_filter_id,
                place.theater_session,
                length_ms,
                place.opened_from_id,
                inside.start_ms,
                _merged_seeks(None, inside.seek_log),
                _merged_speeds(None, inside.speeds),
                inside.fullscreen_ms,
                inside.completions,
                _flag(inside.magnified),
                place.device_id,
                place.client_kind,
                None if about is None else about.packed(),
            ),
        )
        await _note_names(connection, user_id, about, made_at, whole_ms)


async def _so_far(
    connection: Connection, user_id: str, asset_id: str, sitting: str | None
) -> Row | None:
    return (
        None
        if sitting is None
        else next(
            iter(await connection.execute_fetchall(_SITTING_SO_FAR, (user_id, asset_id, sitting))),
            None,
        )
    )


async def _note_names(
    connection: Connection, user_id: str, about: About | None, made_at: int, whole_ms: int
) -> None:
    for kind, named in [] if about is None else about.names():
        await connection.execute(
            _NOTE_NAME,
            {
                "user_id": user_id,
                "kind": kind,
                "ref": named.id,
                "name": named.name,
                "since": made_at - whole_ms // _MS_PER_SECOND,
            },
        )


async def _extend(
    connection: Connection,
    so_far: Row,
    *,
    watch_ms: int,
    position_ms: int | None,
    heat: dict[int, int],
    seeks: int,
    made_at: int,
    inside: Inside,
) -> None:
    await connection.execute(
        _EXTEND,
        {
            "watch_ms": watch_ms,
            "position_ms": position_ms,
            "heat": _heat_json(_merged_heat(so_far["heat"], heat)),
            "seeks": max(0, seeks),
            "made_at": made_at,
            "seek_log": _merged_seeks(so_far["seek_log"], inside.seek_log),
            "speeds": _merged_speeds(so_far["speeds"], inside.speeds),
            "fullscreen_ms": inside.fullscreen_ms,
            "completions": inside.completions,
            "magnified": _flag(inside.magnified),
            "id": so_far["id"],
        },
    )


async def plays_of_asset(database: Database, user_id: str, asset_id: str) -> Watched:
    """How many sittings this user has had with one file, how long, and when last.

    Per user, like the heart, the stars and the replay curve, and for the reason the curve gives:
    what somebody else has watched is not a fact about the file.

    Unscoped about the file, and safe for the same reason `history_of_asset` is: the route that asks
    has already resolved it through a scoped read. Nothing here can name a second file, which is
    what made the ledger's reads need a rule of their own.
    """
    row = await database.fetch_one(_OF_ASSET, (asset_id, user_id))
    if row is None:  # pragma: no cover (an aggregate always answers with one row)
        return Watched(sittings=0, watched_ms=0, last_at=None)
    mapping = dict(row)
    return Watched(
        sittings=int(mapping["sittings"]),
        watched_ms=int(mapping["watched_ms"]),
        last_at=None if mapping["last_at"] is None else int(mapping["last_at"]),
    )


# What the Privacy pane's Clear takes from the player: every sitting and the names kept for them.
# Registered once, at import, as every part of the history is (`kernel/use_history.py`).
register_clearing("player", clear_plays)
