# SPDX-License-Identifier: AGPL-3.0-or-later
"""Writing a sitting down, and reading back what a file has been watched.

The table and the argument for it are in `schema.py`. This is the two statements over it.

Plain functions rather than a store class, and that is the whole of it: there is one insert and one
aggregate, neither holds anything between calls, and a class here would be a part to register in
the composition root for no state at all. The handle is passed in, as it is to every read in the
kernel.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Literal

from sift.kernel.db import Connection, Database
from sift.kernel.ids import new_id
from sift.kernel.use_history import register_clearing

#: WHICH SCREEN a sitting happened on. Declared once, here; the column's CHECK in `schema.py`
#: spells the same list and a test holds the two equal, because a migration step has to mean for
#: ever what it meant when it shipped and so cannot be built from a list that may grow.
#:
#: Three, because there are three places a file is actually on screen. `panel` is the file opened
#: over a screen, which is also what a file's own address opens, since there is no page form of
#: a file. `corner` is the small player that carries a clip on while somebody moves elsewhere.
#: `theater` is a cell of a wall. WHERE the panel was opened from is a separate question with a
#: separate column (`OpenedFrom`), because folding the two into one list would make "a Theater
#: cell playing a Loop" and "a Loop opened from its wall" the same answer.
Screen = Literal["panel", "corner", "theater"]

#: The screen a panel was opened OVER, when it was opened over one. `link` is an address arrived at
#: with nothing behind it; `other` is a screen this list does not name yet, which the client's own
#: test refuses for any screen that can open a file, so it is a gap somebody sees, not a guess.
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

#: What the file WAS when it was watched: the library's own three media types, taken from the file
#: at the moment the sitting began rather than looked up later, because a sitting outlives its file
#: and a file can be converted from one kind into another.
Kind = Literal["video", "image", "gif"]


@dataclass(frozen=True, slots=True)
class Place:
    """Where one sitting happened, as facts written once with its first piece.

    Every field is nullable, and NULL is the honest answer for every row written before these
    existed and for a client that has not been rebuilt: none of it can be worked out afterwards.
    """

    screen: Screen | None = None
    opened_from: OpenedFrom | None = None
    kind: Kind | None = None
    #: The saved Loop the file was opened from. An id no key reaches, like `asset_id`: the sitting
    #: outlives the Loop, and a reader joins it scoped to the user or not at all.
    loop_id: str | None = None
    #: The kept filter (a saved search) the screen behind the panel was showing, if exactly one.
    kept_filter_id: str | None = None
    #: The Theater session a cell's sitting belongs to: the client's name for it, which is the
    #: `session` column of `theater_sessions` for the same user.
    theater_session: str | None = None
    #: Which one thing the screen behind the file was about: the person, tag, Site, Collection,
    #: Photo Set, song or folder `opened_from` names, or the record of the search. An id no key
    #: reaches, like `loop_id`.
    opened_from_id: str | None = None
    #: The client the sitting happened on (`kernel/client.py`): the device's id, None before the
    #: browser has one, and the kind of window.
    device_id: str | None = None
    client_kind: str | None = None


NOWHERE = Place()

#: The most seeks one sitting keeps as a pair of positions. A sitting scrubbed back and forth for
#: an hour is still one row: past this the pairs are let go and `seeks` goes on counting them, so
#: a reader knows how many there were. Sixty-four is several times what a person does with a film
#: (a dozen is a lot) and about a kilobyte at most.
MOST_SEEKS = 64

#: The most speeds one sitting keeps. A player offers a handful; this bounds what a browser can
#: send, not what a person does.
MOST_SPEEDS = 16


@dataclass(frozen=True, slots=True)
class Inside:
    """What happened inside one piece of a sitting, as the client measured it.

    Every field None where the client did not say, which is what a client that has not been rebuilt
    sends and what a screen that does not measure one of them sends: an unrecorded fact is NULL,
    never zero. Each piece carries its own and the server adds a later piece to the first, exactly
    as the time and the replay map are added.
    """

    #: Where the playhead was when the sitting began. Written by the first piece only.
    start_ms: int | None = None
    #: Each move of the playhead by hand during this piece, as (from, to) in milliseconds.
    seek_log: tuple[tuple[int, int], ...] | None = None
    #: How long this piece played at each speed, keyed by the rate.
    speeds: dict[float, int] | None = None
    #: How long this piece filled the screen.
    fullscreen_ms: int | None = None
    #: How many times the file played through to its end during this piece.
    completions: int | None = None
    #: Whether a picture was magnified during this piece.
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

#: The piece of this sitting that is already here, if any. One row by construction (see the
#: partial unique index in `schema.py`).
_SITTING_SO_FAR = (
    "SELECT id, duration_ms, heat, seeks, seek_log, speeds FROM plays"
    " WHERE user_id = ? AND asset_id = ? AND sitting = ?"
)

#: Whether a sitting already has its first piece here: what decides, before the write, whether the
#: route has to read what the file carries.
_SITTING_EXISTS = "SELECT 1 FROM plays WHERE user_id = ? AND asset_id = ? AND sitting = ?"

#: One more piece of a sitting already written down.
#:
#: The time ADDS, because each piece carries its own and never a running total. The position
#: REPLACES, because where the sitting ended is wherever the last piece says. `started_at` is not
#: touched at all: the sitting began when its first piece said it did, and the derivation that
#: works that out is right for the first piece and wrong for every one after it.
#:
#: What happened inside it adds the same way, and a piece that did not say leaves the column as it
#: was: a NULL stays NULL until some piece says, and then counts from that piece. Magnified is an
#: OR: once a picture has been magnified in a sitting it was.
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

#: A thing's name, written for this User when it is not already the latest name they have for it.
#: One statement, so a name seen at every sitting costs one indexed read and no row.
_NOTE_NAME = (
    "INSERT INTO play_names (user_id, kind, ref, name, since)"
    " SELECT :user_id, :kind, :ref, :name, :since"
    " WHERE COALESCE((SELECT name FROM play_names"
    "   WHERE user_id = :user_id AND kind = :kind AND ref = :ref"
    "   ORDER BY since DESC, rowid DESC LIMIT 1), '') <> :name"
)

#: A User's part of their history, cleared on the Privacy pane's Clear. The names go with the
#: sittings: they are kept only because a sitting needed them. Their Theater sessions go too: a
#: session is the wall a Theater cell's sittings were part of (`Place.theater_session`), so it is
#: what they watched as much as the sittings are, and clearing one without the other would leave
#: "you sat in front of a wall for two hours on Sunday" behind a Clear that said it took it.
_CLEAR = (
    "DELETE FROM plays WHERE user_id = ?",
    "DELETE FROM play_names WHERE user_id = ?",
    "DELETE FROM theater_sessions WHERE user_id = ?",
)

#: What one file has been watched, by one user. See `plays_of_asset`.
_OF_ASSET = (
    "SELECT COUNT(*) AS sittings, COALESCE(SUM(duration_ms), 0) AS watched_ms,"
    " MAX(started_at) AS last_at"
    " FROM plays WHERE asset_id = ? AND user_id = ?"
)

_MS_PER_SECOND = 1000


@dataclass(frozen=True, slots=True)
class Watched:
    """How much of one file's viewing this user has behind it.

    Three numbers rather than the rows, because the one line History will draw from this is three
    numbers: how many times, how long altogether, and when last. Handing back the sittings would
    be handing back a table so that the caller could count it.
    """

    sittings: int
    watched_ms: int
    #: When the most recent sitting began, or None where there has never been one.
    last_at: int | None


def _heat_json(heat: dict[int, int]) -> str | None:
    """The replay map as it is stored: sparse, keyed by slice index, NULL where there was none.

    Sparse, so a glance at a long film is one entry rather than a hundred zeroes. NULL rather than
    `{}` where there was none, so "nothing was played" and "no map was sent" are not the same bytes.
    """
    return json.dumps({str(index): ms for index, ms in sorted(heat.items())}) if heat else None


def _merged_heat(stored: str | None, arriving: dict[int, int]) -> dict[int, int]:
    """One sitting's replay map so far, plus the piece that has just arrived.

    ADDED slice by slice rather than replaced, and that is the whole of why the merge is done here
    in Python rather than in the statement: a sitting's second piece carries the slices IT played,
    so keeping the later map would throw away the first ten minutes of a film and keeping the
    earlier one would throw away the last.

    An unreadable stored map is treated as none. It cannot happen (this module is the only thing
    that writes the column), and if it ever did, losing the map of one sitting is better than
    losing the sitting.
    """
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
    """The sitting's seeks so far, with this piece's added at the end, up to `MOST_SEEKS`.

    NULL stays NULL until a piece says something, so a sitting from a client that does not measure
    seeks reads as "not recorded" rather than "none". An unreadable stored list is started again
    from this piece, for the reason an unreadable replay map is (see `_merged_heat`).
    """
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
    """Write down one sitting, exactly as it was reported.

    Facts only, like the report it comes from. Nothing here decides whether the sitting was a view,
    whether it finished, or whether it was worth keeping: those are conclusions and the route
    beside this draws them, because three screens send the same report and a rule enforced in one
    of them is not a rule.

    `length_ms` is the file's length at the moment of the sitting, written with it for the reason
    `Place.kind` is: the view rule judges a sitting by its kind, its length and its time, and a
    sitting outlives its file. Anything that reads a sitting later reads these two from the
    sitting, never from the file, which may be gone, converted, or out of that reader's reach.

    `started_at` is DERIVED and is the one number here that was not reported: the client sends how
    long the sitting was and not when it began, so it is the moment this report landed less the
    whole sitting's time. The error is the flight of one request, which is nothing beside a sitting,
    and the alternative, storing no start at all, makes every question this table exists for
    ("what did I watch on Sunday", "when do I use this") unanswerable.

    **A sitting delivered in several reports is one row.** The player sends one report the moment
    a sitting has earned a view and another with the remainder on the way out, and a photograph
    sends an empty piece when it is opened and the time when it is left. `sitting` is minted when
    the player opens a file and repeated on every piece of it; a piece that finds its sitting
    already here adds to it, so one sitting is one row however many pieces it arrived in.

    **A report with no sitting id writes its own row.** Not every screen that sends one has a
    sitting to speak of, and a client that has not been rebuilt is not a client whose watching
    should be dropped.

    `started_at` is only ever set by the FIRST piece. The derivation above is right for a whole
    sitting and for the first piece of one, and wrong for every piece after it: the second piece
    arrives minutes later and knows only its own share of the time.

    Read then written, inside one transaction. Sift has one writer, so nothing can slip between the
    two (the same property the settings hub's own read-before-write rests on), and the
    alternative, an upsert, cannot add two replay maps together in SQL without reading one of them
    anyway.

    **`place` is written by the FIRST piece and never by a later one**, exactly as `started_at` is.
    Where a sitting happened and what the file was are facts about its beginning; a later piece
    repeats them, and a client that changed its mind half-way would be rewriting a fact rather than
    adding to one.

    **What happened inside it** (`inside`) is added piece by piece like the time; its start
    position is the first piece's, as `started_at` is. **What it was about** (`about`) is the first
    piece's too: the route reads it only when it is about to write one (`sitting_is_here`), and a
    later piece's would be what the file carried minutes later rather than when it began. Its names
    go to `play_names` in the same write, only where they are not already the latest kept.
    """
    made_at = int(time.time())
    whole_ms = (already_reported_ms or 0) + watch_ms
    async with database.write() as connection:
        so_far = (
            None
            if sitting is None
            else next(
                iter(
                    await connection.execute_fetchall(_SITTING_SO_FAR, (user_id, asset_id, sitting))
                ),
                None,
            )
        )
        if so_far is not None:
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
