# SPDX-License-Identifier: AGPL-3.0-or-later
"""What this feature records: one audio fingerprint per file, and the ones taken before there was
a file to hang them on.

## Its own table rather than a column on the asset

The row is optional: a photograph has none and a silent clip has an empty one. It holds a blob
the wide asset row should not be carrying through every read of the library, and the version that
made it belongs per row rather than per library, which is the rule every fingerprint here follows.
The cascade is right for the same reason: a fingerprint is a fact about the bytes, not part of
anybody's history of them, so it goes when the file does.

## A row with an empty fingerprint is an answer

A file with no audio, or whose audio ffmpeg will not read, gets a row with an empty blob and a
duration of nothing. Left with no row at all it would come back at the head of the pass for the
rest of the library's life; the empty row is the pass saying it has looked.

## And a second table, keyed by identity, for a fingerprint taken before its file existed

A downloaded, dropped, pasted or uploaded file sits on the local disk in staging before it is
copied into a library folder, and that is the one moment its audio can be read at local-disk
speed. See `kernel/landing.py`. At that moment there may be no asset row yet, and there may never
be one. So the fingerprint is kept under the file's IDENTITY, which is true of the bytes whatever
becomes of them, and the product claims it by identity the first time it runs for that file: a
move between two tables, with no decode.

A pending row nothing ever claims is harmless and tiny, and it is claimed the moment those exact
bytes are imported again. They are swept by age before each catch-up run rather than by a timer,
because that is the one moment anybody is reading this table at all.

## The one-row table beside them

`music_catchup` is whether an admin has been asked about reading a library that was already here
when this feature arrived, and until when the answer stands. One row, because it is one answer for
the install. See `slices/music/queue.py` for the card that writes it.

## And the files still waiting, as rows

`music_waiting` is every file with a sound track and no fingerprint row (the question the catch-up
card puts a number on), held as one row per file so the visibility component can count it per
user the way it counts a tag's files (`register_counted` below). Checking every file with audio
against the user's visible set on every draw of the board would cost tens of milliseconds on a
large library; read from the stored count it is one row.

**The database keeps it true, not the application**, and the KERNEL writes the statements that
do it, because whether a file waits reads the file's own row and a slice never writes SQL against
`assets`. This module declares the question as data (`WAITING`: the table, the table whose row
means done, the columns of the file's row that must hold a value) and `kernel/access/waiting.py`
composes the fill, the triggers that apply the rule wherever its inputs move (probing writing a
file's audio track or its read moment, a file arriving already read, a fingerprint row arriving by
any path (the pass, a claim from staging, an empty answer), one going or moving) and the boot
check that puts the triggers back and repairs the rows if a rebuild of either table took them
away. A file leaving the library takes its row by the cascade. So no writer, present or future,
has to know this table exists.

## Which files share a song

Three more things, all of them derived from the fingerprints and none of them a reading of any
file. See `slices/music/matching.py` for the rule and its measurements:

* `audio_fingerprint_keys` is the index: one row per distinct KEY of a file's fingerprint (its
  values' high 20 bits). A file's candidates are the files sharing enough of its keys, which is a
  range of this table per key rather than a comparison with every file in the library.
  `WITHOUT ROWID` with the key first, because that is the one way it is searched; the second index
  is for taking one file's keys away when it is indexed again.
* `audio_fingerprints.indexed_scheme` is which way the file's keys were cut, and NULL until they
  have been: the question the fingerprint task asks of a file that already has a fingerprint
  ("has one, and is it indexed under the scheme in force"). Stamped when the whole of the pairing
  is done rather than when the keys are written, so a failure part-way leaves the file unindexed
  and the next run finishes it.
* `music_pairs` is every pair of files verified to share a song, once, with the smaller id first
  (the CHECK is what makes it once), and what the verification measured. The second index reads a
  file's pairs from the other side.

And the two tables beside a song's name (`slices/music/names.py`): `music_name_refusals`, a name
somebody took off a file, which is never put back; and `music_lookups`, what was asked of AcoustID
for a file and what it answered. Both cascade with the file, because each is a fact about something
done to a file, and neither outlives it.

## Where a file's song is

Not here. A song is a thing of the catalog's (`songs`, and a file's is its row of `song_files`,
written only through the kernel's song door, `kernel/content/songs.py`), and that row says where
the song came from: AcoustID, a Site's page, or another file with the same music, with the file,
the recording and the score. `music_names` said the same of Sift's names until version 4, which
drops it once catalog step 81 has moved every row onto the songs, so one fact has one home. That is
why this component is brought up after the catalog: the move reads the table this step drops.
"""

from __future__ import annotations

from sift.kernel.access import waiting
from sift.kernel.access.visibility import Counted, register_counted
from sift.kernel.access.waiting import Waiting, register_waiting
from sift.kernel.content import songs
from sift.kernel.content.backlog import marks
from sift.kernel.db import Connection, register_schema_initializer

COMPONENT = "music"
VERSION = 6

_CREATE_FINGERPRINTS = """
CREATE TABLE IF NOT EXISTS audio_fingerprints (
  asset_id     TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  algorithm    INTEGER NOT NULL,
  tool         TEXT NOT NULL,
  duration_ms  INTEGER NOT NULL,
  offset_ms    INTEGER NOT NULL DEFAULT 0,
  fingerprint  BLOB NOT NULL,
  computed_at  INTEGER NOT NULL,
  indexed_scheme INTEGER
)
"""

_CREATE_PENDING = """
CREATE TABLE IF NOT EXISTS audio_fingerprints_pending (
  identity     TEXT PRIMARY KEY,
  algorithm    INTEGER NOT NULL,
  tool         TEXT NOT NULL,
  duration_ms  INTEGER NOT NULL,
  offset_ms    INTEGER NOT NULL DEFAULT 0,
  fingerprint  BLOB NOT NULL,
  computed_at  INTEGER NOT NULL
)
"""

# `CHECK (id = 1)` is what makes this one answer rather than a log of answers. A second row would
# be a second opinion about the same question with nothing to say which is current.
_CREATE_CATCHUP = """
CREATE TABLE IF NOT EXISTS music_catchup (
  id       INTEGER PRIMARY KEY CHECK (id = 1),
  until    INTEGER NOT NULL,
  asked_at INTEGER NOT NULL
)
"""

_INDEXES = (
    # The sweep that drops pending rows nothing claimed reads them oldest first.
    "CREATE INDEX IF NOT EXISTS ix_audio_pending_age ON audio_fingerprints_pending(computed_at)",
    # Taking one file's keys away before it is indexed again, and the cascade when it goes.
    "CREATE INDEX IF NOT EXISTS ix_audio_keys_asset ON audio_fingerprint_keys(asset_id)",
    # A file's pairs from the other side: the primary key reads them where it is `a_id`.
    "CREATE INDEX IF NOT EXISTS ix_music_pairs_b ON music_pairs(b_id)",
)


#: The kind the files still waiting are counted under, per user, and the one object they are
#: counted against: the whole library, since the card asks one question of all of it.
WAITING_KIND = "music_waiting"
WAITING_SCOPE = "library"

_CREATE_WAITING = """
CREATE TABLE IF NOT EXISTS music_waiting (
  asset_id TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE
) WITHOUT ROWID
"""

#: The question, declared to the kernel as data: a file waits for a fingerprint when probing has
#: read it (`probed_at`) and found a sound track (`acodec`), and `audio_fingerprints` has no row for
#: it: a fingerprint or the empty answer. The kernel writes every statement that reads the file's
#: row from this: the fill, the triggers, the comparison and the boot check.
WAITING = Waiting(
    table="music_waiting",
    done="audio_fingerprints",
    filled=("acodec", "probed_at"),
)


_CREATE_KEYS = """
CREATE TABLE IF NOT EXISTS audio_fingerprint_keys (
  key       INTEGER NOT NULL,
  asset_id  TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  PRIMARY KEY (key, asset_id)
) WITHOUT ROWID
"""

_CREATE_PAIRS = """
CREATE TABLE IF NOT EXISTS music_pairs (
  a_id        TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  b_id        TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  ber         REAL NOT NULL,
  offset_s    REAL NOT NULL,
  windows     INTEGER NOT NULL,
  matching    INTEGER NOT NULL,
  computed_at INTEGER NOT NULL,
  PRIMARY KEY (a_id, b_id),
  CHECK (a_id < b_id)
) WITHOUT ROWID
"""

#: Version 4: the record of where Sift's name for a file came from, moved onto the songs by catalog
#: step 81 (see the module's note) and dropped here, after it.
_DROP_NAMES = "DROP TABLE IF EXISTS music_names"

_CREATE_NAME_REFUSALS = """
CREATE TABLE IF NOT EXISTS music_name_refusals (
  asset_id   TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  song       TEXT NOT NULL,
  refused_at INTEGER NOT NULL,
  PRIMARY KEY (asset_id, song)
) WITHOUT ROWID
"""

# `lengths_sent` is a JSON list of the durations tried, in order.
_CREATE_LOOKUPS = """
CREATE TABLE IF NOT EXISTS music_lookups (
  asset_id     TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  looked_up_at INTEGER NOT NULL,
  lengths_sent TEXT NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('named', 'nothing', 'refused', 'failed')),
  recording_id TEXT,
  title        TEXT,
  artists      TEXT,
  score        REAL
)
"""

#: Where the AcoustID application key's sealed id lives: one row, never a setting. A registered
#: setting is drawn on a screen and any admin settings write can change it: pointed at another
#: feature's secret id, the Check would send that secret to AcoustID. The key itself is sealed
#: through the secret store like a stash-box's; this row only remembers which secret is the key.
_CREATE_LOOKUP_KEY = """
CREATE TABLE IF NOT EXISTS music_lookup_key (
  id        INTEGER PRIMARY KEY CHECK (id = 1),
  secret_id TEXT NOT NULL,
  set_at    INTEGER NOT NULL
)
"""


#: Every answer that named a recording and its artists, oldest first: what version 5 credits.
_KEPT_ARTISTS = """
SELECT recording_id, artists FROM music_lookups
 WHERE status = 'named' AND recording_id IS NOT NULL AND artists IS NOT NULL AND artists <> ''
 ORDER BY looked_up_at, asset_id
"""


#: Version 6: a fingerprint kept or a file starting to wait marks it for the passes' kept counts.
_MARKS = (
    *marks("audio_fingerprints", watched=("asset_id", "algorithm", "indexed_scheme")),
    *marks("music_waiting"),
)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        for statement in (
            _CREATE_FINGERPRINTS,
            _CREATE_PENDING,
            _CREATE_CATCHUP,
            _CREATE_WAITING,
            _CREATE_KEYS,
            _CREATE_PAIRS,
            _CREATE_NAME_REFUSALS,
            _CREATE_LOOKUPS,
            _CREATE_LOOKUP_KEY,
            *_INDEXES,
        ):
            await connection.execute(statement)
        # The triggers that keep `music_waiting` true, written by the kernel from the rule.
        await waiting.start(connection, WAITING)
    if 0 < on_disk < 4:
        await connection.execute(_DROP_NAMES)
    # Version 5: the artists of every answer AcoustID already gave, credited on the song that is
    # that recording (catalog 82 made the tables). Once, with one History line and a log line
    # saying how many; a song credited already is left as it is, so twice is once.
    if 0 < on_disk < 5:
        rows = await connection.execute_fetchall(_KEPT_ARTISTS)
        await songs.credit_kept_answers(
            connection, [(str(row["recording_id"]), row["artists"]) for row in rows]
        )
    if on_disk < 6:
        for statement in _MARKS:
            await connection.execute(statement)


# It names the asset table in a foreign key and the kernel watches that table with triggers for it,
# and a trigger cannot be made on a table that is not there, so it depends on the component that
# creates `assets`. And on the catalog, whose step 81 reads `music_names` before version 4 here
# drops it (see the module's note).
register_schema_initializer(
    COMPONENT, VERSION, initialize, depends_on=["content", "catalog"], baseline=3
)
register_waiting(WAITING)

#: The waiting files as memberships of the one scope. The scope is written into the text so the
#: statement is a constant; the check holds the two together.
_WAITING_SOURCE = "(SELECT asset_id, 'library' AS scope FROM music_waiting)"
if f"'{WAITING_SCOPE}'" not in _WAITING_SOURCE:  # pragma: no cover (an edit)
    raise RuntimeError("the waiting source and its scope disagree")

# The files still waiting, counted per user and against the whole library by the visibility
# component, beside the tags and the people, so the count the card draws hides what the vault
# hides by the same recompute that decides the files.
register_counted(
    Counted(
        WAITING_KIND,
        _WAITING_SOURCE,
        "scope",
        "music_waiting",
        keys=(("asset_id",),),
    ),
    component=COMPONENT,
)
