# SPDX-License-Identifier: AGPL-3.0-or-later
"""What one person thinks of one asset: favorite, rating, and when they last watched it.

Per user, not per asset. Two people looking at the same library keep their own hearts, their own
stars and their own history, and neither can see the other's. That is why this is a table of its
own rather than columns on `assets`: a column would make one person's opinion a property of the
file, and there would be nowhere to put the second person's.

It sits in the kernel because three separate features touch it (the grid reads recent history,
the player records a view, the rating control writes a heart or a star), and shared data that
lives in one feature makes the other two depend on that feature. Here they all depend on the
kernel instead, which is the direction dependencies are allowed to run.

The rows carry no permissions. A row exists because somebody once rated or watched something;
whether they may still see it is a question for the access layer, and every read here is expected
to be filtered by it. `recent` returns ids for exactly that reason: they are candidates, not
results.
"""

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

# A star rating out of TEN whatever scale the screens draw (five stars on a five-point screen
# arrive as ten, so the setting never rewrites a row), or NULL: zero is not "unrated", and a query
# for "rated 1 or more" must not find a cleared one.
MIN_RATING = 1
MAX_RATING = 10


# How many equal slices a file is cut into for the replay curve.
#
# A hundred, because the curve is drawn across the width of a scrubber and read at a glance, and a
# scrubber is a few hundred pixels wide: fewer slices and a rewatched moment smears across a
# noticeable fraction of the file, more and each slice holds too little time to tell a replay from a
# pause. It also makes a slice readable as a percentage, which is the unit anybody describing "the
# bit two thirds of the way in" is already using.
#
# A FRACTION of the file, never a duration. A slice of a three-minute clip is 1.8 seconds and a
# slice of a two-hour film is 72; the curve describes the same shape of either, and re-encoding a
# file to a slightly different length does not slide its history sideways.
HEAT_BUCKETS = 100


# --- Where to start from, and what "part-way through" means ------------------------------------
#
# THIS RULE LIVES IN THE KERNEL BECAUSE THREE FEATURES ASK IT: the player, a tile drawing a progress
# bar, and the query language answering "what am I part-way through". The grid may not import the
# player, the search may not import either, and a rule copied into three slices is three rules the
# moment one is edited. It sits beside the column it reads instead (`resume_ms`, a few lines down),
# which is the direction dependencies are allowed to run.

#: How far in a video must be stopped before the position is worth keeping. A fraction of its
#: length, capped: five seconds into a video is the beginning, and five seconds into a ten-second
#: clip is the middle of it.
_RESUME_FLOOR_FRACTION = 20
_RESUME_FLOOR_CAP_MS = 5_000

#: How close to the end counts as finished. Same shape, and the reason is the same: fifteen seconds
#: from the end of a video is the credits, and fifteen seconds from the end of a twenty-second clip
#: is most of it.
_RESUME_TAIL_FRACTION = 10
_RESUME_TAIL_CAP_MS = 15_000

#: The two preferences that parameterise the rule, by key.
#:
#: Declared here rather than in the player slice that REGISTERS them: its label, its help text and
#: its list of choices are the player's, and so is the settings screen. Only the two strings are
#: here, because the grid and the search read the same preferences and neither may import a
#: slice to learn how they are spelled. A key spelled two ways is a preference that is honoured on
#: one screen and silently ignored on the next.
RESUME_ENABLED_KEY = "playback.resume_enabled"
RESUME_MINIMUM_KEY = "playback.resume_minimum_seconds"


async def resume_minimum_ms(
    get_user: Callable[[str, str], Awaitable[Any]], user_id: str
) -> int | None:
    """One user's resume rule as a single number: the shortest video worth keeping a place in.

    `None` is OFF, and it is not the same answer as a very large number even though both come to
    "nothing resumes today". Off means no position is ever kept OR offered, which is what somebody
    who does not want a machine remembering what they were part-way through is asking for (see
    the two settings themselves). Everything downstream carries that distinction by carrying the
    `None`: the SQL below binds it as NULL, and a NULL minimum makes `RESUMING` false for every row
    rather than merely for the short ones.

    It takes the `get_user` call rather than the settings seam, and that is a layering point rather
    than a style: the seam's own module reads the access layer, the access layer reads this one, and
    importing it here would close the circle. A callable is the part of it this needs.
    """
    if not await get_user(user_id, RESUME_ENABLED_KEY):
        return None
    return max(0, int(await get_user(user_id, RESUME_MINIMUM_KEY))) * 1000


def resume_point(
    duration_ms: int | None, position_ms: int | None, *, minimum_ms: int | None
) -> int | None:
    """Where reopening this should start, or None for the beginning.

    The one place that decides, so the answer is the same whether it is being written down at the
    end of a sitting, read back at the start of the next one, or drawn as a bar along the bottom of
    a tile. That matters: it means raising the minimum length takes effect on positions already
    stored, rather than only on ones saved after.

    None comes back for anything with no timeline (a photograph), anything shorter than the
    minimum, anything barely started, anything watched to the end, and anything at all when
    resuming is switched off, which between them are most of a library, and all of them mean
    "start at the beginning".
    """
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


#: The same decision as `resume_point`, written for the engine that cannot call it.
#:
#: Two engines have to answer one question. A tile is drawn from a row Python built, so it asks the
#: function above; a filter and a facet count are answered by ONE statement inside the permission
#: rules, so they have to ask SQLite: there is no way to run a page of rows past a Python
#: predicate without moving the filtering out of the statement that decides visibility, which is the
#: thing the access layer exists to prevent.
#:
#: So there are two forms and there is ONE set of numbers: every constant below is interpolated from
#: the names above, at import, so a threshold moved in one form cannot fail to move in the other.
#: What is left is the shape, and the shape is held to the function by a test that runs both over
#: the same table of edges (`test_content.py`). Integer division is what SQLite does with two
#: integers, which is what `//` does here.
#:
#: `{minimum}` is allowed to be NULL and that is how OFF arrives. Every conjunct is two-valued
#: (each column is tested for NULL before it is compared, and `FALSE AND NULL` is FALSE), so the
#: whole predicate is never NULL, which is what makes a NEGATED `viewed:continue` still mean
#: something. A predicate that can be NULL is a filter that silently stops applying under a NOT.
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
    #: Where to start from next time, in milliseconds, or None for the beginning. NOT `watched_ms`
    #: above: that is a running total across sittings and is not a place in the file.
    resume_ms: int | None = None
    last_viewed_at: int | None = None
    #: When this user first watched the whole thing, or None for never. NOT the opposite of
    #: `resume_ms` above, though it is easy to read it that way: a NULL resume point means "nowhere
    #: worth going back to", which is true of a file finished AND of one never started, and telling
    #: those two apart is the entire reason this field exists.
    completed_at: int | None = None
    #: Kept at the top of whatever wall this file is on, for this user. The sixth opinion, and
    #: the same one a person, a Site, a collection, a tag or a photo set carries on its own
    #: table (see `kernel.content.entity_state`, which is where that half lives and why).
    pinned: bool = False
    #: How many times this user has pressed the O mark on this file. The seventh opinion.
    #:
    #: Zero rather than None for "never", and that is not the same choice `rating` above made: a
    #: rating has to tell unrated apart from no stars, and a counter has nothing to tell apart:
    #: never pressed and pressed no times are one answer. So an absent row reads as zero here the
    #: way it reads as unhearted above, and nothing downstream has a NULL to carry.
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


# --- what somebody thought, and when they thought it -------------------------------------------
#
# Every write below this line REPLACES what was there. A heart comes off, a rating moves from seven
# to nine, an O press takes a counter from four to five, and the row afterwards says only what is
# true now, under one shared `updated_at`. The moment and the value it replaced are gone the instant
# the next write lands, and nothing later can invent them.
#
# So each of those writes also appends a row saying what it changed, in the same transaction as the
# change. `opinions` is that table; its DDL and the argument for its shape are in
# `kernel/content/schema.py`, beside the tables it is about.
#
# **In the same transaction, always.** An opinion written afterwards can be missing for a press that
# happened or present for one that did not, which is the argument the ledger's own door has made
# since it existed. There is one place in this module that owns the transaction for a single write
# and one that owns it for a list, so there are exactly two places that can forget.
#
# **One helper rather than a line in each writer**, for the reason the announcement next to it is
# one helper: six singular writers, three list writers and five concealment writers in other modules
# is fourteen chances to spell a subject kind differently, and a kind spelled two ways is a history
# that reads as two subjects.


class OpinionKind(StrEnum):
    """The five things a user can think, as the `opinions.kind` CHECK spells them.

    A closed set rather than a string at each call site, so a writer cannot invent a sixth word that
    the table would then refuse at the last moment, and so the five are readable in one place.
    """

    RATING = "rating"
    FAVORITE = "favorite"
    PIN = "pin"
    #: The O press. Spelled out here because a member called `O` reads as a zero and the linter
    #: refuses it; the stored value is the one word the table's CHECK holds.
    O_COUNT = "o"
    #: Concealment, for every kind of thing that can be concealed. A file's hide already writes a
    #: ledger event; a person's, a Site's, a folder's, a tag's, a collection's and a photo set's
    #: write nothing at all, which is the half this kind is here for.
    HIDE = "hide"


#: What an opinion is ABOUT, in the vocabulary the rest of Sift already uses for these.
SUBJECT_ASSET = "asset"

_RECORD_OPINION = """
INSERT INTO opinions (id, user_id, subject_kind, subject_id, kind, before, after, at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

#: What this user thought BEFORE the write about to happen, read in the write's own transaction.
#:
#: It cannot come from the upsert's `RETURNING`, and that is worth writing down because it reads as
#: though it could: SQLite's RETURNING answers with the row AFTER the statement, so on an update it
#: hands back exactly the value that was just written. For the O counter the old value is derivable
#: (one less than the new one) and for a heart, a pin and a rating it is not derivable at all: the
#: new value is whatever the caller passed, and says nothing about what it replaced.
#:
#: No row at all means this user had never thought anything about this file, which is NULL rather
#: than zero: "it was off" and "there was nothing" are different answers and only one of them is
#: true here.
_OPINION_BEFORE = (
    "SELECT favorite, pinned, rating, o_count FROM asset_user_state"
    " WHERE asset_id = ? AND user_id = ?"
)

#: The same question over a list, in one statement. `json_each` for the reason the list writers
#: themselves use it: a VALUES group per id would be a statement assembled from parts.
_OPINION_BEFORE_MANY = """
SELECT asset_id, favorite, pinned, rating, o_count FROM asset_user_state
WHERE user_id = :user_id AND asset_id IN (SELECT value FROM json_each(:asset_ids))
"""

#: Which column of an asset's row each kind of opinion is about. One mapping, read from both
#: directions (the row before the write and the state after it), so the two can never disagree
#: about which number an opinion of this kind is.
_OPINION_COLUMN: dict[OpinionKind, str] = {
    OpinionKind.FAVORITE: "favorite",
    OpinionKind.PIN: "pinned",
    OpinionKind.RATING: "rating",
    OpinionKind.O_COUNT: "o_count",
}


def _opinion_value(row: Row | None, kind: OpinionKind) -> int | None:
    """One kind's value out of an `asset_user_state` row, as the integer the table stores.

    A flag arrives as 0 or 1 and a rating as NULL or 1 to 10, so the only conversion is that a flag
    is made an integer rather than left as whatever the driver handed back.
    """
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
    """Append one row saying what a user thought, on the caller's own connection.

    The caller's connection and never a fresh one: this is the second half of a write that has
    already begun, and an opinion in a transaction of its own could land for a press that was rolled
    back. Every caller in this module is inside `self._db.write()`; a caller in another module is
    inside whatever transaction its own write opened.

    `subject_id` carries no foreign key, so nothing here checks that the subject exists: it is the
    user's history, and a file deleted afterwards does not make what somebody thought of it
    untrue. `user_id` DOES cascade, which is the deliberate forget: a user removed takes
    everything it ever thought with it.

    The id is a ULID, so the rows are in the order they happened whatever the wall clock did between
    two of them: a wall clock can step backwards, and `at` is a wall-clock reading.
    """
    await connection.execute(
        _RECORD_OPINION,
        (new_id(), user_id, subject_kind, subject_id, kind.value, before, after, at),
    )


# Every write is an upsert on the natural key, because the first heart and the hundredth are the
# same statement and a caller should not have to know which one it is making. The column list is
# written out per statement rather than assembled, so nothing here is ever built from parts.
_SET_FAVORITE = """
INSERT INTO asset_user_state (asset_id, user_id, favorite, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(asset_id, user_id) DO UPDATE SET
    favorite   = excluded.favorite,
    updated_at = excluded.updated_at
RETURNING *
"""

# The pin. Written out longhand beside the heart above rather than assembled, for the reason given
# over `_SET_FAVORITE`: nothing here is ever built from parts.
#
# It touches ONE column and leaves every other alone, which is what an upsert on the natural key
# gives for free: pinning a file does not disturb its rating, its resume point or its view count.
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

# The O counter, in three statements, because it has three things to do and they are not one
# statement with a value in it.
#
# Written out longhand beside the heart and the pin above, for the reason given over
# `_SET_FAVORITE`: nothing here is ever built from parts. A single statement taking a delta was the
# obvious shape and it cannot be written honestly: the insert arm has to clamp the delta and the
# update arm has to clamp the SUM, and `excluded` can only carry one of those two numbers, so one
# arm would always be reading a value that was clamped for the other.
#
# The arithmetic is in SQL rather than read-then-written, exactly as `view_count` is and for the
# same reason: two presses arriving at once from two tabs would otherwise both read the same number
# and both write it back, and one of the two would be lost with nothing to say so.
#
# Each touches ONE column and leaves every other alone, which the upsert on the natural key gives
# for free: pressing the mark does not disturb a rating, a heart, a pin or a resume point.
_BUMP_O_COUNT = """
INSERT INTO asset_user_state (asset_id, user_id, o_count, updated_at)
VALUES (?, ?, 1, ?)
ON CONFLICT(asset_id, user_id) DO UPDATE SET
    o_count    = asset_user_state.o_count + 1,
    updated_at = excluded.updated_at
RETURNING *
"""

# Counts brought across from another library (a Stash database): the O count and the view count
# are RAISED to what it says and never lowered, so running an import twice, or over counts this
# library has already added to, leaves the larger. The dates behind them are not brought; see
# `carry_counts`.
_CARRY_COUNTS = """
INSERT INTO asset_user_state (asset_id, user_id, o_count, view_count, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(asset_id, user_id) DO UPDATE SET
    o_count    = MAX(asset_user_state.o_count, excluded.o_count),
    view_count = MAX(asset_user_state.view_count, excluded.view_count),
    updated_at = excluded.updated_at
RETURNING *
"""

# One off, and never below nothing. `MAX(0, ...)` rather than a check in Python, because the check
# would be read in one transaction and acted on in another, and a counter that can go negative is
# a number no screen has a way to draw.
#
# A row that does not exist yet is INSERTED at zero rather than left alone. That is deliberate: the
# alternative is a statement with nothing to return, and every write here answers with the whole
# opinion so that the control which was pressed settles onto the row the server actually holds.
_LOWER_O_COUNT = """
INSERT INTO asset_user_state (asset_id, user_id, o_count, updated_at)
VALUES (?, ?, 0, ?)
ON CONFLICT(asset_id, user_id) DO UPDATE SET
    o_count    = MAX(0, asset_user_state.o_count - 1),
    updated_at = excluded.updated_at
RETURNING *
"""

# Back to nothing. The one of the three that is a REPLACEMENT rather than arithmetic, so it reads
# like the heart above it: what is written does not depend on what was there.
_RESET_O_COUNT = """
INSERT INTO asset_user_state (asset_id, user_id, o_count, updated_at)
VALUES (?, ?, 0, ?)
ON CONFLICT(asset_id, user_id) DO UPDATE SET
    o_count    = 0,
    updated_at = excluded.updated_at
RETURNING *
"""

# The same three writes over a LIST of files, in one statement each.
#
# ## Why they exist at all
#
# Hearting, rating or pinning a selection one request PER FILE, each awaited before the next
# began, would make a hundred files a hundred round trips and a hundred write transactions, each
# taking and releasing the write lock, each announcing its own opinion to the user's other tabs.
# The work is never the cost. One request for the selection is what bulk tagging, bulk delete and
# bulk move do too.
#
# ## Why `json_each` and not a VALUES list built at runtime
#
# A multi-row VALUES needs one `(?, ?, ?, ?)` group per id, which means assembling the statement
# from parts, the one thing the note over `_SET_FAVORITE` says is never done here. The id list
# arrives as a JSON array in a single parameter instead, so the text below is as fixed as the text
# above it and takes five hundred files exactly as it takes one. It is the same idiom the access
# layer's own list queries are driven by.
#
# `WHERE true` is not decoration and must not be tidied away. SQLite cannot tell an upsert clause
# from a `SELECT ... ON CONFLICT` join hint without a WHERE between them, and refuses to parse the
# statement at all: the failure is at prepare time, so it is loud, but it reads like a typo.
#
# Each touches ONE column and leaves every other alone, exactly as its singular does: rating a
# selection does not disturb their hearts, their pins, their resume points or their view counts.
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

# view_count and watched_ms accumulate; they are the only columns here that read their own
# previous value, so the increment happens in SQL rather than as a read-then-write that two
# concurrent views could interleave and lose.
#
# resume_ms is the opposite: it is REPLACED, including with NULL. Every report states where the
# sitting ended, and "nowhere worth going back to" is one of the answers, so a video watched to
# the end clears the place it was stopped at last time rather than leaving a stale one behind.
#
# completed_at is a THIRD behaviour and it is neither of those two: it is written once and then left
# alone for ever. `COALESCE(asset_user_state.completed_at, excluded.completed_at)` keeps whatever is
# already there and only fills a NULL, so the stored value is when this user FIRST saw it
# through, watching it again does not move the date, and a later sitting that stops halfway cannot
# take the fact away. Reaching the end is a thing that happened.
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

# Time watched, WITHOUT counting a view.
#
# Apart from the view statement, because the two answer to different rules. A
# sitting too short to count (a second on a photograph, ten seconds of a feature film) is still
# time that really passed, and throwing it away would make `watched_ms` describe only the sittings
# that happened to clear a threshold. So every sitting adds its time; only some of them are views.
#
# What it deliberately does NOT touch is `last_viewed_at`. That column is the history rail and the
# Viewed facet, and a file glanced at for a second has not been viewed: putting it in the history
# would fill "Recently viewed" with everything the arrow keys passed over on the way somewhere else.
#
# `resume_ms` IS written, and that is not an inconsistency. It is the point. Where somebody got to
# and whether they watched enough for it to count are two different questions, and the first one
# does not wait on the second: opening a film, skipping two minutes in and leaving is exactly the
# sitting somebody wants picked up again, and it is under the threshold by a long way. The resume
# rule has its own floor for deciding what is worth keeping, applied before this is called.
# `completed_at` is here for the same reason it is on `_RECORD_VIEW`. Reaching the end and earning a
# view happen at two different moments: the view is earned by the report that CROSSES the
# threshold, thirty seconds into anything two minutes or longer, and the end is reported by the one
# that arrives when the file finishes. A video watched straight through sends both, so without this
# every video of two minutes or more would be watched, counted, and never marked finished, and the
# "watched" filter (`kernel/access/constraints.py`) would be wrong about all of them.
#
# COALESCE, exactly as above: written once, then left alone for ever. Watching the first minute of
# something again does not unwatch the rest of it.
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

# One slice of one file for one user, added to rather than replaced, so a moment played in
# three separate sittings accumulates instead of reading as whatever the last one happened to be.
#
# In SQL rather than as a read-add-write, for the reason written over `_RECORD_VIEW`: the player
# supports two elements on one clip at once, and two browsers merging a list in Python would keep
# one of the two.
_ADD_HEAT = """
INSERT INTO asset_replay_heat (asset_id, user_id, bucket, watched_ms, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(asset_id, user_id, bucket) DO UPDATE SET
    watched_ms = asset_replay_heat.watched_ms + excluded.watched_ms,
    updated_at = ?
"""

# The whole curve for one file, for one user. Ordered so the caller can walk it without sorting,
# though it fills a fixed-width list by index and does not depend on the order.
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
        """Keep this file at the top of its wall, or take the pin off.

        The same shape as the heart above and independent of it: a pinned file need not be a
        favourite, and hearting one does not move it.
        """
        _check_ids(asset_id, user_id)
        return await self._write_state(
            _SET_PINNED,
            (asset_id, user_id, int(pinned), self._now()),
            records=OpinionKind.PIN,
        )

    async def set_rating(self, asset_id: str, user_id: str, rating: int | None) -> AssetUserState:
        """Set a star rating, or clear it with None.

        Zero is refused rather than quietly treated as "unrated": a caller that means to clear a
        rating says so, and one that sends a 0 by accident hears about it.
        """
        _check_ids(asset_id, user_id)
        if rating is not None and not MIN_RATING <= rating <= MAX_RATING:
            raise ValueError(f"a rating is {MIN_RATING} to {MAX_RATING}, or None to clear it")
        return await self._write_state(
            _SET_RATING, (asset_id, user_id, rating, self._now()), records=OpinionKind.RATING
        )

    async def bump_o_count(self, asset_id: str, user_id: str) -> AssetUserState:
        """One more. The press, and the only one of the three anybody makes often.

        Per user, like the heart and the stars: what somebody else has pressed is not a fact
        about the file.
        """
        _check_ids(asset_id, user_id)
        return await self._write_state(
            _BUMP_O_COUNT, (asset_id, user_id, self._now()), records=OpinionKind.O_COUNT
        )

    async def carry_counts(
        self, asset_id: str, user_id: str, *, o_count: int, views: int
    ) -> AssetUserState:
        """Raise the O count and the view count to what another library says, never lowering.

        For an import: the counts come across and the dates do not, because a library that kept
        only counts once and invented a date for each is not a record of when anything happened.
        Recorded as an O count opinion, as a press is.
        """
        _check_ids(asset_id, user_id)
        return await self._write_state(
            _CARRY_COUNTS,
            (asset_id, user_id, max(0, o_count), max(0, views), self._now()),
            records=OpinionKind.O_COUNT,
        )

    async def lower_o_count(self, asset_id: str, user_id: str) -> AssetUserState:
        """One fewer, and never below nothing.

        A press is easy to make by accident on a control that costs one click, so taking one back
        has to be as cheap as making it. Clamped in the statement (see `_LOWER_O_COUNT`).
        """
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
        """Heart a whole selection, or take the heart off all of it. Answers how many rows moved.

        Every id in ONE statement and one transaction. The per-file form above is what a single
        tile uses; this is what a selection uses, and the difference it makes is not the SQL: it
        is the round trips, the write-lock turns and the live-channel messages that a loop over
        the singular would pay, one of each per file.

        The count rather than the rows. A caller acting on a selection reports a number, and the
        per-file opinions it would otherwise have to carry are already on their way to every screen
        this user has open (see `_write_many`).

        **The ids must be ones the caller has already resolved.** Nothing here decides who may
        touch what; this store holds opinions and has never held a permission. An id naming no file
        is refused by the foreign key and takes the whole statement with it, which is the honest
        failure for a caller that skipped the access layer rather than a silent partial write.
        """
        written = await self._write_many(
            _SET_FAVORITE_MANY,
            asset_ids,
            user_id,
            {"favorite": int(favorite)},
            records=OpinionKind.FAVORITE,
        )
        return len(written)

    async def set_pinned_many(self, asset_ids: Sequence[str], user_id: str, pinned: bool) -> int:
        """Pin a whole selection, or take the pins off. Answers how many rows moved.

        The same shape as the heart above and independent of it, exactly as the singulars are.
        """
        written = await self._write_many(
            _SET_PINNED_MANY, asset_ids, user_id, {"pinned": int(pinned)}, records=OpinionKind.PIN
        )
        return len(written)

    async def set_rating_many(
        self, asset_ids: Sequence[str], user_id: str, rating: int | None
    ) -> int:
        """Set one rating across a selection, or clear it across all of it.

        One value for the whole set, because that is what the stars mean over a selection: "three
        stars" is a choice with five answers and not a nudge each. Zero is refused here as it is in
        the singular: a caller that means to clear a rating says None.
        """
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
        """Count a view, add to the time watched, stamp the history, and set where to resume.

        `watch_ms` is how long this particular sitting lasted; it accumulates.

        `resume_ms` is REPLACED, not accumulated, and every report states it. `None` means there is
        nowhere worth going back to (never played far enough, watched to the end, or not the kind
        of file that has a place in it), and it CLEARS whatever was stored before. That is why the
        default is None: a caller that only means to count a view is saying, correctly, that it
        knows of no resume point, and the alternative reading (leave the old one) would resume
        somebody at a position from a sitting that has since finished.

        `completed` says this sitting reached the end. It only ever ADDS the fact: passing False
        against a file already finished leaves it finished, because watching the first minute of
        something again does not unwatch the rest of it.

        Whether a sitting is a view at all is not decided here. This store is told; the rule lives
        in one place above it, where the length of the file and the user's own settings are, and
        `record_watch_time` is what a sitting that did not qualify calls instead.
        """
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
        """Add to the time watched and set where to resume, WITHOUT counting a view.

        For the sitting that really happened and did not earn a view: a photograph passed over on
        the way to the next one, two minutes of a feature film. The time is true and is kept, and so
        is the place: what somebody wants picked up again is not decided by whether they watched
        long enough for the library to call it a viewing. Only the view count and the history stamp
        wait on that.

        `completed` says this sitting reached the end, and it means here exactly what it means on
        `record_view`: it only ever ADDS the fact, and passing False against a file already finished
        leaves it finished. It is on BOTH because the two reports of one sitting go to different
        statements: the piece that crosses the threshold earns the view and goes to `record_view`,
        and the piece that arrives when the file finishes earns no second view and comes here.
        Without it, the only report that could ever say a file was finished was the one that
        happened to cross the threshold, which is never the one that reaches the end.

        A sitting with nothing in it at all (no time, nowhere to resume and nothing finished)
        writes nothing rather than an all-defaults row. A row here means somebody has done something
        with the file, and creating one to record that they did not would put every tile the pointer
        crossed into a table whose whole design is that it holds only the few files that were
        actually touched. A sitting that reached the END is never nothing, however short it was.
        """
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
        """Add time to the slices of a file that were on screen during one sitting.

        `buckets` maps a slice index (0 to `HEAT_BUCKETS - 1`, a fraction of the file rather than
        a moment in it) to the milliseconds spent in it. Only the slices that were actually played
        appear, which is what keeps a ten-second look at a two-hour film to a single row.

        Every slice is its own upsert, and the addition happens in SQL for the reason the view
        counter's does: two players can be on one clip at once, and a read-add-write from both would
        keep one of the two.

        Out-of-range indexes and non-positive times are dropped rather than raised on. This is
        called with arithmetic done in a browser against a duration the browser measured, and a
        rounding that lands one past the last slice is not a reason to lose the whole sitting: the
        endpoint above has already refused anything that is not a number at all.

        Nothing is announced. The curve is read when a file is opened and drawn once; there is no
        tile anywhere showing it, so there is no screen for a change to go stale on, and a message
        per sitting per file, to say a shape moved by a pixel, would be noise on the one channel
        that carries hearts and stars.
        """
        _check_ids(asset_id, user_id)
        now = self._now()
        wanted = [
            (asset_id, user_id, bucket, milliseconds, now, now)
            for bucket, milliseconds in sorted(buckets.items())
            if 0 <= bucket < HEAT_BUCKETS and milliseconds > 0
        ]
        if not wanted:
            return
        # One trip through the writer for the whole sitting, not one per slice. A sitting that
        # played straight through touches every slice it crossed, and taking and releasing the write
        # lock a hundred times to record one viewing would put a hundred commits behind whatever
        # else wants to write.
        async with self._db.write() as connection:
            await connection.executemany(_ADD_HEAT, wanted)

    async def replay_heat(self, asset_id: str, user_id: str) -> list[int]:
        """How long each slice of a file has been on screen for this user, oldest sitting on.

        Always `HEAT_BUCKETS` long, zeros included, because the caller is drawing a curve across the
        whole width of a scrubber and a sparse map would make it invent the gaps. A file nobody has
        played comes back as all zeros rather than as an error or an empty list: "no replays yet"
        is a real answer and it draws as a flat line.
        """
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
        """This person's state for this asset. Never None: an untouched asset reads as defaults.

        Returning a default rather than None is deliberate: almost nothing in a library has been
        rated, so a caller that had to handle None everywhere would branch on the common case.
        """
        if not is_id(asset_id) or not is_id(user_id):
            return AssetUserState(asset_id=asset_id, user_id=user_id)
        row = await self._db.fetch_one(_STATE_OF, (asset_id, user_id))
        if row is None:
            return AssetUserState(asset_id=asset_id, user_id=user_id)
        return state_from_row(row)

    async def states_of(self, asset_ids: Sequence[str], user_id: str) -> dict[str, AssetUserState]:
        """One person's state for a page of assets, keyed by asset id.

        Assets with no row are simply absent from the result: a caller drawing a grid wants the
        few that were rated, and inventing a default row for every unrated tile would allocate a
        page of objects to say nothing. Ask `state_of` when a default is what you want.

        One statement for the page. Fifty tiles asking one at a time is the shape that makes a
        fast page feel slow, and it does not show up until the library is big.
        """
        wanted = [asset_id for asset_id in asset_ids if is_id(asset_id)]
        if not wanted or not is_id(user_id):
            return {}
        rows = await self._db.fetch_all(_STATES_OF, (json.dumps(wanted), user_id))
        return {row["asset_id"]: state_from_row(row) for row in rows}

    async def _write_state(
        self, sql: str, params: tuple[object, ...], *, records: OpinionKind | None = None
    ) -> AssetUserState:
        """Write one of these rows, and tell this user's other screens what it now says.

        All three writes go through here, so all three are announced, which is the whole reason
        it exists rather than each of them ending in a conversion of its own. A heart is set on the
        file's own screen, which opens over the grid; a view is counted by the player; a star can
        be set from either. Each of those leaves a tile somewhere else drawing what it last read.

        Only ever to the user that owns the row. Nothing here is a permission, and nothing about
        it is anybody else's: two people looking at the same library keep their own hearts, their
        own stars and their own history.

        `records` names the kind of opinion this write is, for the writers that are one, and the
        row saying what it changed is appended in this same transaction, before it can be lost. The
        writes that are a MEASUREMENT rather than an opinion pass nothing: a view counted and a
        stretch of time watched are things that happened to a file, not things anybody decided
        about it, and they already have a table of their own (`slices/player/plays.py`).

        The asset and the user are read off the first two bound values, which every statement in
        this module binds in that order. That is a positional coupling and it is deliberate over the
        alternative: the statements are twelve lines above this one, written out longhand for the
        reason the note over `_SET_FAVORITE` gives, and a second pair of arguments repeating what is
        already in `params` would be two places for one caller to disagree with itself.
        """
        async with self._db.write() as connection:
            before: int | None = None
            if records is not None:
                # Read INSIDE the write, and before the upsert: this is the value the statement
                # below is about to destroy, and after it there is nothing that remembers it. See
                # `_OPINION_BEFORE` for why the upsert's own RETURNING cannot answer this.
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
        """Write one of these rows for every id, in one statement, and announce each one.

        All three list writes go through here for the reason all three singles go through
        `_write_state`: the announcement is what stops a tile on another screen drawing what it
        last read, and a write that forgets it is a write nobody else is told about.

        A message per row and not one per call, because the shape that travels is an OPINION about
        a named file (see `changes.AssetOpinion`), and there is no shape for "some files
        changed" that a screen could apply to the row it is holding. A selection larger than the
        live channel will carry in one beat is capped there rather than here, and the screens
        holding the rest re-read; see `changes.MAX_OPINIONS_PER_BEAT`.

        De-duplicated first. Two mentions of one id in a selection are one row, and `json_each`
        would otherwise hand the same row to the statement twice.

        An empty selection writes nothing and announces nothing. `json_each` over an empty array
        yields no rows, so the statement itself would be harmless. But a transaction and a turn
        of the write lock bought to do nothing is the shape this whole form exists to stop.

        `records` is the singular's argument in the plural (required here, because every list
        write is one of the three opinions), and the prior values are read the same way: one
        statement over the same id list, inside the same transaction, before the upsert
        that replaces them. One opinion row per file, because hearting a hundred files is a
        hundred opinions: the count is what the CALLER reports, and a
        history that held "some files were hearted" could answer nothing about any of them.
        """
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
    """Refuse a malformed id before it reaches a statement.

    The foreign keys would catch an unknown id, but not a caller passing something that is not an
    id at all, and the write path should not be the place that discovers it.
    """
    if not is_id(asset_id):
        raise ValueError("not an asset id")
    if not is_id(user_id):
        raise ValueError("not a user id")
