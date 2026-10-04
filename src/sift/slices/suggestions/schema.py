# SPDX-License-Identifier: AGPL-3.0-or-later
"""What is still to be answered, what has been answered, and where the last pass got to.

Seven tables, and the shape of each one is the feature's memory.

`folder_claims` is the outstanding work. A row arrives `pending` and leaves `confirmed` or
`rejected`; nothing is deleted. **It is not a log.** It holds one row per claim a folder makes, and
a pass that runs again over an unchanged folder writes nothing at all.

It deliberately holds no asset ids and no file counts. Those are worked out when somebody reads the
list, through the resolver, against the user asking, because a suggestion is generated from the
whole library and the library contains concealed things. A count stored at scan time would be a
count of things the reader may not be told about, and it would be right there in the row.

`claim_rejections` is the permanent no. Keyed by the folded spelling rather than by the folder,
because a folder is not stable: renaming one on disk makes it a different row here, and a rejection
tied to the old row would come straight back under the new one. Saying no to `Sandbar runways` is a
statement about that name, and it holds wherever the name turns up.

`folder_people` is the standing part of a yes. Confirming a folder attributes the files that are in
it, and this is what attributes the ones that arrive afterwards: without it the feature re-asks
for ever as a library grows, which is the failure somebody notices first. It does not follow a file
that is moved out: a confirmation is a decision about files, and a file taken somewhere else keeps
what was decided about it.

`folder_passes` is what makes a pass incremental. It holds a cheap signature per folder, so a pass
over two hundred thousand files reads the folders whose contents or faces have moved and leaves the
rest alone.

`filename_refusals` is the permanent no for the OTHER reader, the one that applies itself. Filing
a file from its own name is undone from that file's own History, and the pass that wrote it reads
every file under no site at all, so without this the undo would last exactly until the next pass and
nothing on any screen would say why. It is the same load-bearing memory `folder_people` is, read the
other way round.

`folder_refusals` is the same memory for the folder reader's silent writes: a folder filed under
somebody without asking, taken back from its record, is never filed under them that way again.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer

COMPONENT = "suggestions"
VERSION = 11

# `kind` is what the row proposes rather than how it was worked out.
#
#   person:     this folder is somebody. The ordinary case.
#   site:       every filename here opens with the same word, so that word is a Site and the names
#               are per file. One row for the folder, the names offered underneath it.
#   username:   the folder's own name says a username and the site it is on:
#               `harlowquin (RedGifs)`. Answering yes files the folder under that username and
#               names NOBODY, which is what makes it a third kind rather than a variation on either
#               of the two above. `is_username` says whether `proposed` is a username on `site`.
#
# `group_id` is the face group corroborating the claim, and NULL means there is none, which is a
# real answer and not a missing one. A folder of body-only pictures carries no face, and the row
# says so on screen rather than being withheld.
#
# `evidence` is which rung of the ladder produced it, kept because "why am I being asked this"
# is the first thing somebody wants from a screen that asks them things.
#
# Nothing references this table, and nothing answered is deleted from it: a `rejected` row is
# somebody having said no, and losing one means asking again. A pending one the reader no longer
# asks is taken back when its folder is read again (`SuggestionService._unasked`), so a new reading
# of a folder does not leave the old question standing beside the new.
_CREATE_CLAIMS = """
CREATE TABLE IF NOT EXISTS folder_claims (
  id          TEXT PRIMARY KEY,
  folder_id   TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL CHECK(kind IN ('person','site','username')),
  name_key    TEXT NOT NULL,
  proposed    TEXT NOT NULL,
  person_id   TEXT REFERENCES people(id) ON DELETE CASCADE,
  group_id    TEXT,
  site        TEXT,
  is_username INTEGER NOT NULL DEFAULT 0,
  evidence    TEXT NOT NULL CHECK(evidence IN ('face_group','name_only','filenames',
              'known_in_filenames','username_folder','by_hand')),
  state       TEXT NOT NULL DEFAULT 'pending'
              CHECK(state IN ('pending','confirmed','rejected')),
  created_at  INTEGER NOT NULL,
  decided_at  INTEGER,
  UNIQUE(folder_id, name_key)
)
"""

# The permanent no. One column and a timestamp: there is nothing to say about a rejection except
# that it happened, and a reason field nobody fills in is a column that reads as missing data.
_CREATE_REJECTIONS = """
CREATE TABLE IF NOT EXISTS claim_rejections (
  name_key   TEXT PRIMARY KEY,
  created_at INTEGER NOT NULL
)
"""

# A folder that has been answered, and who it was answered as. Cascades from both sides, because a
# standing rule about a folder that has gone, or about a person who has been deleted, is not a rule
# about anything, and a stale one would attribute tomorrow's files to nobody.
_CREATE_FOLDER_PEOPLE = """
CREATE TABLE IF NOT EXISTS folder_people (
  folder_id  TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  person_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(folder_id, person_id)
)
"""

# Where the last pass left each folder. `signature` is opaque on purpose: it is compared for
# equality and never read, so what goes into it can change without a migration.
_CREATE_PASSES = """
CREATE TABLE IF NOT EXISTS folder_passes (
  folder_id  TEXT PRIMARY KEY REFERENCES folders(id) ON DELETE CASCADE,
  signature  TEXT NOT NULL,
  scanned_at INTEGER NOT NULL
)
"""

# The permanent no for a filing a pass made from a file's own name. One column and a timestamp, for
# the reason `claim_rejections` gives: there is nothing to say about a refusal except that it
# happened.
#
# **Keyed by the FILE, where the folder rejection is keyed by the NAME, and the two are opposite on
# purpose.** Saying no to `Sandbar runways` is a statement about a spelling, and a folder renamed on
# disk is a different row, so a rejection tied to the row would come straight back under the new
# name. Taking a filing back is a statement about THIS FILE: that its name said Instagram and the
# file is not that username's. A file keeps its id when it is renamed or moved, so the id is the
# thing that holds, and keying on the filename would refuse every other file a tool happened to
# name the same way.
#
# Cascades from `assets`, because a refusal about a file that has been removed from the library is
# not a refusal about anything.
_CREATE_FILENAME_REFUSALS = """
CREATE TABLE IF NOT EXISTS filename_refusals (
  asset_id   TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL
)
"""

# THE USERNAME NUMBERS THIS LIBRARY CANNOT PUT A NAME TO.
#
# A filename shape that carries only the site's own number for a username files nothing until
# something teaches the library what that number is called (the pictures' own metadata, or
# somebody typing it in). Until then the files sit under no site at all, and this table is how
# that is SAID: a card that reads "4,000 files filed" while 4,000 more are waiting on a fact
# nobody has been asked for is a card that reports the half that worked.
#
# Written by the pass and read by the card, because the card cannot work it out for itself: the
# answer is a parse of every filename in the library (about half a second for a hundred thousand),
# which is fine once per pass and is not fine on a board survey somebody is waiting for.
#
# `files` is what makes it converge rather than grow. A number whose file count has not moved since
# the last pass has nothing new to read, so the pass leaves its pictures shut, which matters most
# for numbers whose files are all videos and carry no metadata at all.
#
# Keyed by the site and the number rather than given an id: the pair IS the thing, and a row per
# pass would be a log nobody reads. The whole table is replaced each pass, so a number that has
# been named simply stops being in it.
_CREATE_WAITING_NUMBERS = """
CREATE TABLE IF NOT EXISTS username_numbers_waiting (
  site     TEXT NOT NULL,
  number   TEXT NOT NULL,
  files    INTEGER NOT NULL,
  seen_at  INTEGER NOT NULL,
  PRIMARY KEY(site, number)
)
"""

# THE STANDING NO TO A SILENT WRITE (step 11). The pass files a folder under somebody without asking
# where faces already named or the folder's own name say whose it is, and that write has an Undo on
# its record. Taking it back takes the person off the files it wrote and forgets the standing yes;
# this is what keeps it taken back, because the faces and the name that decided it are still there
# on the next pass. It binds only the pass: a person answering the folder on its card is a yes of
# their own, and the standing yes it leaves is carried as any other.
#
# Keyed by the folder and the person, not by a name: the decision being refused was about THIS
# folder's files and THIS person, and a folder that is renamed on disk keeps its row. Cascades from
# both, for the reason `folder_people` gives.
_CREATE_FOLDER_REFUSALS = """
CREATE TABLE IF NOT EXISTS folder_refusals (
  folder_id  TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  person_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(folder_id, person_id)
)
"""

_INDEXES = (
    # The screen's only question: what is still waiting, oldest first. By id, a ULID minted under a
    # floor that never goes down (`kernel.ids.new_id`), because a wall-clock second steps backwards
    # on a machine correcting its time.
    "CREATE INDEX IF NOT EXISTS ix_folder_claims_state_by_id ON folder_claims(state, id)",
    # The alias short-circuit and the rejection check both arrive by name rather than by folder.
    "CREATE INDEX IF NOT EXISTS ix_folder_claims_name ON folder_claims(name_key)",
    # Attributing a file as it lands: which people does the folder it arrived in already carry.
    "CREATE INDEX IF NOT EXISTS ix_folder_people_person ON folder_people(person_id)",
)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        for statement in (
            _CREATE_CLAIMS,
            _CREATE_REJECTIONS,
            _CREATE_FOLDER_PEOPLE,
            _CREATE_PASSES,
            _CREATE_FILENAME_REFUSALS,
            _CREATE_WAITING_NUMBERS,
            *_INDEXES,
        ):
            await connection.execute(statement)
    if on_disk < 11:
        await connection.execute(_CREATE_FOLDER_REFUSALS)


# It names `folders`, `people` and `assets` in foreign keys without declaring a dependency on the
# components that create them: SQLite resolves a foreign key's parent by name when a row is written
# rather than when the table is made, and nothing here writes a row while the schema is built.
register_schema_initializer(COMPONENT, VERSION, initialize, baseline=10)
