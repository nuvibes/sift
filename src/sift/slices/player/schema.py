# SPDX-License-Identifier: AGPL-3.0-or-later
"""One table: a row for every sitting somebody spent with a file.

`assets.view_count`, `watched_ms`, `o_count` and
`last_viewed_at` are counters, and a counter can answer "how much" and nothing else: not when,
not how often, not how long a sitting was, not what somebody watched in March. The whole family of
questions a person expects of something that has been watching them watch (the year in review,
the streak, the evening this library gets used most) needs one row per sitting and cannot be
derived from a number that only goes up.

The client sends the whole sitting: how long it lasted, where the playhead stopped, whether it
reached the end, and which slices of the file were on screen. This keeps it, in one insert.

## Why this is NOT in the ledger

**A play is a measurement, not an act.** The ledger records what was DONE to the library (a file
named, a share revoked, a person merged), and every row in it is something somebody or something
decided. Watching a file decides nothing and changes nothing, and it happens far more often than
everything else put together: folding it in would make the record of acts mostly a record of
viewing, and the one thing somebody opens that record to see would be buried under it. So it is its
own table, it is never joined into the ledger, and a file's history shows at most one folded line
drawn from it.

## Retention

For ever, with no horizon and no sweep: the value of this table is exactly that it goes back, and
a rolling window would quietly delete the years the stats are for. It is small (one short row
per sitting, against a hundred thousand files), so a horizon would be buying nothing with the one
thing the table is for.

The user key cascades: a user removed takes its history with it, which is what the deliberate
forget means. There is no FILE key. A play is not a fact about the file;
it is a fact about the USER, and it is the only record of one of the very few things a person does
here for its own sake. With a cascading file key, deleting a duplicate, tidying a folder or moving
a drive out would rewrite last year: "you watched 240 hours this year" would quietly become 180,
and nothing anywhere would say so. It is the ledger's rule (an event carries a snapshot and no
cascading key, so it outlives its subject), applied here too.

A play about a file that is gone still reads, because the question it answers was never about the
file: "you watched 14 files that are no longer in the library." What it deliberately does NOT carry
is the file's name. The ledger snapshots one because a ledger row is a sentence somebody reads; a
play is a number somebody counts, and a name here would be a copy of the library kept in a table
whose whole purpose is arithmetic.

`plays.asset_id` is therefore an asset id no foreign key reaches, which is a shape this codebase
refuses by default (see `tests/gates/test_every_store_lets_go.py`, where it is declared as meant).

## One row per sitting

A sitting arrives in PIECES: the player reports once when the sitting has earned its view and
again with the remainder on the way out, and a photograph reports an empty piece the moment it is
opened and the time when it is left. Without an id tying the pieces together the COUNT of sittings
would be high by one for every video left open long enough to earn a view, and doubled for every
picture.

`sitting` is that id: minted by the client when it opens a file, repeated on every piece. The
second piece finds the first and adds to it, so a sitting is one row however many pieces it
arrived in. A report with no sitting id writes its own row, which is what keeps the theater wall
and anything else that sends a report working unchanged.

## Inside a sitting

Where the playhead was when the sitting began (`start_ms`), each time it was moved by hand as a
pair of positions (`seek_log`), how long was played at each speed (`speeds`), how long the
sitting filled the screen (`fullscreen_ms`), how many times the file played through to its end
(`completions`, every pass under Repeat and not only the first), and whether a picture was
magnified (`magnified`; how long a picture was looked at is `duration_ms`, as it always was).

Carried on the sitting's own report, never a request per event: each piece brings what happened
during it and the later piece is added to the first. Packed into the row rather than spread over a
table of events, because a sitting is read whole and an event table would be a row per press of
an arrow key. `seek_log` is a JSON list of `[from_ms, to_ms]` pairs and keeps the first
`plays.MOST_SEEKS` of a sitting; `seeks` goes on counting past it, so a reader knows how many pairs
were let go. `speeds` is a JSON object of the rate (as a decimal string) to the milliseconds played
at it.

NULL in every one of these means "not recorded", which is what every row written before version 7
reads as and what a client that has not been rebuilt sends: never zero, which would be a claim.

## What a sitting was about

`about` is what the file carried when it was watched, as ids: a JSON object of `people`, `tags`
and `sites` (lists) and `song` (one id), only the ones the User could be shown, written with the
first piece. A later rename, unlink or delete leaves it as it was, which is the point: "the
evenings you spent on Ilva Brennan" must not change because somebody tidied her tags in May.

Their NAMES are kept beside it rather than in it, in `play_names`: one row each time a thing is
seen under a name it has not been seen under before, by that User. A name is the snapshot a reader
needs once the thing has gone (the ledger's own reason for keeping one) and it is the same name
for every sitting until it changes, so writing it into every row would be a copy of the library's
names per sitting. The name a thing had at a sitting is the latest of its rows at or before the
sitting began. `play_names` is the User's, cascades with them and is cleared with their history.

The FILE's name is still not kept, for the reason above: a sitting is counted, and a copy of the
library's file names in a table of arithmetic is what this table refuses to be.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.migrations import check_allows, column_exists, widen_a_check

COMPONENT = "player"
VERSION = 7

# `user_id` references `users`, a Sift sign-in. `asset_id` carries no key: a sitting outlives its
# file (see the module docstring).
#
# The CHECK lists on `screen`, `opened_from` and `kind` are written out rather than built from
# `plays.Screen` and friends, because a schema has to mean the same thing for every library that
# has it. `test_the_columns_agree_with_the_wire` holds each list equal to the one the report is
# validated against; a new value is a CHECK widened by `widen_a_check` in a step of its own.
_CREATE_PLAYS = """
CREATE TABLE IF NOT EXISTS plays (
  id              TEXT PRIMARY KEY,
  user_id         TEXT REFERENCES users(id) ON DELETE CASCADE,
  asset_id        TEXT,
  started_at      INTEGER NOT NULL,
  duration_ms     INTEGER NOT NULL,
  ended_at_ms     INTEGER,
  heat            TEXT,
  made_at         INTEGER NOT NULL,
  -- Which sitting a report is a piece of (see "One row per sitting"), and how often the playhead
  -- was moved by hand.
  sitting         TEXT,
  seeks           INTEGER NOT NULL DEFAULT 0,
  -- Where a sitting happened and what the file was, written with its first piece, because none
  -- of it can be worked out afterwards. NULL where it was not recorded; an invented value would be
  -- indistinguishable from a recorded one.
  screen          TEXT CHECK(screen IN ('panel','corner','theater')),
  opened_from     TEXT CHECK(opened_from IN (
                    'library','search','favorites','recent','loops','person','site','tag',
                    'collection','photo_set','song','organize','downloads','hidden','start',
                    'link','folder','insights','other'
                  )),
  kind            TEXT CHECK(kind IN ('video','image','gif')),
  -- What a sitting was opened from. No keys, for the reason `asset_id` has none.
  loop_id         TEXT,
  kept_filter_id  TEXT,
  theater_session TEXT,
  -- The length of the file, written with the sitting for the reason `kind` is: the view rule
  -- judges a sitting by its kind, its length and its time, and a sitting outlives its file.
  length_ms       INTEGER,
  -- Version 7. Which one thing the screen behind the file was about: the person, tag, Site,
  -- Collection, Photo Set, song or folder of `opened_from`, or the record of the search
  -- (`search_events.id`). No key, for the reason `loop_id` has none.
  opened_from_id  TEXT,
  -- Version 7: what happened inside the sitting (see "Inside a sitting").
  start_ms        INTEGER,
  seek_log        TEXT,
  speeds          TEXT,
  fullscreen_ms   INTEGER,
  completions     INTEGER,
  magnified       INTEGER,
  -- Version 7: the client it happened on (`kernel/client.py`), and what the file carried then.
  device_id       TEXT,
  client_kind     TEXT,
  about           TEXT
)
"""

#: Version 7: the name each thing a sitting was about had, kept once per name rather than once per
#: sitting (see "What a sitting was about"). One User's, so their history clears with them.
_CREATE_PLAY_NAMES = """
CREATE TABLE IF NOT EXISTS play_names (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind    TEXT NOT NULL CHECK(kind IN ('person','tag','site','song')),
  ref     TEXT NOT NULL,
  name    TEXT NOT NULL,
  since   INTEGER NOT NULL
)
"""

#: The name a thing had at a moment: the latest row at or before it, for one User.
_PLAY_NAMES_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_play_names_ref ON play_names(user_id, kind, ref, since)"
)

#: The columns version 7 adds to a library that already has the table, each only where it is
#: missing (a library made by this build has them all from the CREATE above).
_ADD_V7 = (
    ("opened_from_id", "ALTER TABLE plays ADD COLUMN opened_from_id TEXT"),
    ("start_ms", "ALTER TABLE plays ADD COLUMN start_ms INTEGER"),
    ("seek_log", "ALTER TABLE plays ADD COLUMN seek_log TEXT"),
    ("speeds", "ALTER TABLE plays ADD COLUMN speeds TEXT"),
    ("fullscreen_ms", "ALTER TABLE plays ADD COLUMN fullscreen_ms INTEGER"),
    ("completions", "ALTER TABLE plays ADD COLUMN completions INTEGER"),
    ("magnified", "ALTER TABLE plays ADD COLUMN magnified INTEGER"),
    ("device_id", "ALTER TABLE plays ADD COLUMN device_id TEXT"),
    ("client_kind", "ALTER TABLE plays ADD COLUMN client_kind TEXT"),
    ("about", "ALTER TABLE plays ADD COLUMN about TEXT"),
)

_INDEXES = (
    # A file's own folded line, and anything asked about one file over time.
    "CREATE INDEX IF NOT EXISTS ix_plays_asset ON plays(asset_id, started_at)",
    # A person's year: every sitting they had, in order. This is the one the stats screens walk,
    # and without it each of them is a scan of the whole table.
    "CREATE INDEX IF NOT EXISTS ix_plays_user ON plays(user_id, started_at)",
    # A sitting is one row, enforced rather than trusted. Scoped to the USER and the FILE as well
    # as the id: the id is minted in a browser, so keyed on it alone a client repeating one id
    # across two files would fold two files' watching into one row, and one user's id colliding
    # with another's would merge two people's history. Partial, because a report with no sitting id
    # writes its own row.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_plays_sitting"
    " ON plays(user_id, asset_id, sitting) WHERE sitting IS NOT NULL",
    # A Theater session's sittings, found from the session. Partial: nearly every sitting has none.
    "CREATE INDEX IF NOT EXISTS ix_plays_theater_session"
    " ON plays(user_id, theater_session) WHERE theater_session IS NOT NULL",
)


#: Version 6: a song's page is a screen a file can be opened from, so `opened_from` takes `song`.
#: Widened where the constraint lives (`migrations.widen_a_check`): nothing points at `plays`, but
#: the edit moves no row either way, and the fragment is the exact text versions 1 to 5 wrote.
_OPENED_FROM_WAS = "'collection','photo_set','organize'"
_OPENED_FROM_NOW = "'collection','photo_set','song','organize'"

#: Version 7: a folder and Insights are screens a file is opened from. Before it a file opened on a
#: folder's wall said `library` and one opened on Insights said `other`; those rows keep saying so.
_OPENED_FROM_V6 = "'link','other'"
_OPENED_FROM_V7 = "'link','folder','insights','other'"


async def initialize_player(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_PLAYS)
        for statement in _INDEXES:
            await connection.execute(statement)
    if 0 < on_disk < 6 and not await check_allows(connection, "plays", "song"):
        await widen_a_check(connection, "plays", was=_OPENED_FROM_WAS, now=_OPENED_FROM_NOW)
    if 0 < on_disk < 7:
        for column, statement in _ADD_V7:
            if not await column_exists(connection, "plays", column):
                await connection.execute(statement)
        if not await check_allows(connection, "plays", "insights"):
            await widen_a_check(connection, "plays", was=_OPENED_FROM_V6, now=_OPENED_FROM_V7)
    if on_disk < 7:
        await connection.execute(_CREATE_PLAY_NAMES)
        await connection.execute(_PLAY_NAMES_INDEX)


# `users` comes from the identity component and `assets` from content, so both are built before
# these keys name them.
register_schema_initializer(
    COMPONENT,
    VERSION,
    initialize_player,
    depends_on=["content", "identity"],
    baseline=5,
)
