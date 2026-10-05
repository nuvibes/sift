# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tables permission resolution reads.

Three components, and the reason they sit in the kernel rather than in the features that
manage them is the same reason `folders` does: **the resolver has to join them.**

Deciding whether someone may see an asset means reading who they are (`users`), walking the
folders the file sits in, and checking every tag, person, collection and site the asset
belongs to for a grant. A table the resolver joins cannot be created by a feature built on top
of the resolver: the schema registry would have to run a feature's tables before the kernel's,
and the permission tests could not run at all until that feature existed.

So the split is **tables here, behaviour there.** This module creates the rows; tagging,
people, collections and usernames are managed by the features that own those screens, and they
own the migrations too. The rule that decides what belongs here is short:

    if the resolver reads it, the kernel owns its table.

An access grant naming something is the usual way that happens, and not the only one.
`people_aliases` is another: no grant can name an alias, but the shared
term-to-person resolver lives here (one resolver, so a search and an autocomplete can never
disagree about who a name belongs to), and it joins the table. A kernel function reading a table
a feature creates is the same inversion the paragraph above rules out, arrived at from the other
direction.

`acl_grants` is **sparse**: a row exists only where a grant was actually made. No row is the
default, and the default is that a guest sees nothing.
"""

from __future__ import annotations

import json
import re

from sift.kernel import presses
from sift.kernel.access import creator_studios, default_covers, edited, schema_columns
from sift.kernel.content import songs
from sift.kernel.db import Connection, register_schema_initializer, register_schema_invariant
from sift.kernel.migrations import check_allows, column_exists, table_exists, widen_a_check

IDENTITY_COMPONENT = "identity"
IDENTITY_VERSION = 4

CATALOG_COMPONENT = "catalog"
CATALOG_VERSION = 90

ACCESS_COMPONENT = "access"
ACCESS_VERSION = 5


# No email column: a user who cannot be correlated with an address is one less thing an install
# leaks, and recovery is a console reset rather than a mail-out.
# `role` is read on every request, not copied into the session, so removing an admin's rights or
# disabling a user takes effect on their next request rather than at their next login.
# `cache_stamp` is how many times what this user may see has changed. It rides in the address of
# every generated picture alongside the picture's digest, so raising it (on any hide, unhide, share,
# unshare or delete, in the same transaction) makes every address this user was given unreachable
# at once: a copy served from the browser's own store is served with no permission check. On the
# USER and not the session, because on the session every sign-in would start with an empty cache.
# `renames` and `renames_since` are how many times this user has renamed themselves, and when that
# count started. A guest may change their own name a few times a day and no more: the refusal on a
# name that is taken is a question anybody may ask, and a rate is what turns "who has a sign-in
# here" from a list you can enumerate into one you cannot. A count and a window rather than a row
# per rename, which would be keeping a history of what somebody used to be called.
_CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
  id            TEXT PRIMARY KEY,
  username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  pin_hash      TEXT,
  role          TEXT NOT NULL CHECK(role IN ('admin','guest')),
  mk_wrapped    BLOB,
  mk_nonce      BLOB,
  mk_kdf_salt   BLOB,
  created_at    INTEGER NOT NULL,
  disabled      INTEGER NOT NULL DEFAULT 0,
  cache_stamp   INTEGER NOT NULL DEFAULT 0,
  renames       INTEGER NOT NULL DEFAULT 0,
  renames_since INTEGER
)
"""

# Values sealed under the master key, which only a user's password unwraps (`users.mk_*`, and see
# `kernel/secret_store.py`). The nonce and ciphertext are useless without it.
_CREATE_SECRETS = """
CREATE TABLE IF NOT EXISTS secrets (
  id         TEXT PRIMARY KEY,
  ciphertext BLOB NOT NULL,
  nonce      BLOB NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
)
"""


# `object_id` carries no foreign key, and it cannot: it names a folder, a tag, a person, a
# collection, a site, a Photo Set or an asset depending on `object_type`, and SQLite has one parent
# table per key. The consequence is real and has to be handled rather than hoped away: delete a tag
# without deleting the grants naming it and those grants keep applying to whatever id is reused.
# `Repository.forget_object()` is what deletes them, and deleting an object without calling it is
# a bug.
# The unique key includes `effect`, so a share and a restrict for the same person on the same
# object can both exist. That is not a contradiction to resolve later: it is resolved here and
# now, every time, and it resolves to restrict.
# `object_type` is a CHECK, which SQLite cannot alter, and this table is named by the body of every
# visibility trigger: a rebuild of it goes through `migrations.rebuild_in_place`, and a kind ADDED to
# it is written into the stored definition instead (`migrations.widen_a_check`), which moves no row.
_CREATE_ACL_GRANTS = """
CREATE TABLE IF NOT EXISTS acl_grants (
  id              TEXT PRIMARY KEY,
  object_type     TEXT NOT NULL CHECK(object_type IN (
                    'global','root','folder','item',
                    'tag','person','collection','site','photo_set','song')),
  object_id       TEXT,
  subject_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  effect          TEXT NOT NULL CHECK(effect IN ('share','restrict')),
  created_at      INTEGER NOT NULL,
  UNIQUE(object_type, object_id, subject_user_id, effect)
)
"""

#: Access version 5: a grant can name a SONG. The exact fragment of the stored definition the step
#: widens, and what it becomes; the closing parenthesis is what makes the old one match once.
_GRANT_KINDS_WAS = "'photo_set')"
_GRANT_KINDS_NOW = "'photo_set','song')"

_ACCESS_INDEXES = (
    # The resolver's first move on every request: every grant this person has.
    "CREATE INDEX IF NOT EXISTS ix_acl_subject ON acl_grants(subject_user_id)",
    # Revoking every grant on a deleted object.
    "CREATE INDEX IF NOT EXISTS ix_acl_object ON acl_grants(object_type, object_id)",
    # A global grant names no object, so its `object_id` is NULL, and SQLite counts every NULL as
    # distinct: the row-level UNIQUE never catches a second identical global grant. This partial
    # unique index closes it, and the insert names it as a second conflict target so a re-grant
    # updates in place instead of raising.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_acl_global "
    "ON acl_grants(object_type, subject_user_id, effect) WHERE object_id IS NULL",
)


#: THE NAMES THAT ARE ORDERED BY A STORED KEY, as `(table, name column, key column)`.
#: A name is ordered by `kernel.sorting.sort_key`, stored beside it, because SQLite's NOCASE folds
#: ASCII and nothing else. Every ORDER BY falls back to the name, so a row whose key has not been
#: written is in its old place rather than missing. Read by `tests/gates/test_one_ordering.py`,
#: which refuses a statement that writes the name without the key.
SORT_KEYS: tuple[tuple[str, str, str], ...] = (
    ("people", "name", "name_sort"),
    ("tags", "name", "name_sort"),
    ("collections", "name", "name_sort"),
    ("photo_sets", "name", "name_sort"),
    ("songs", "name", "name_sort"),
    ("artists", "name", "name_sort"),
    ("sites", "name", "name_sort"),
    ("usernames", "name", "name_sort"),
    ("people_aliases", "alias", "alias_sort"),
    ("site_aliases", "alias", "alias_sort"),
    ("tag_aliases", "alias", "alias_sort"),
)

#: WHEN A MEMBERSHIP WAS MADE, and when a site was, as `(table, column)`.
#:
#: Nullable, every one: NULL is "before this was recorded", which is drawn as such rather than
#: invented. A site is MADE, so it carries `created_at`; a membership ARRIVES, so it carries
#: `added_at`, the word `assets.added_at` already uses. Read by
#: `tests/gates/test_a_membership_says_when.py`, which refuses an insert that leaves it out.
_ADDED_AT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("sites", "created_at"),
    ("collection_items", "added_at"),
    ("photo_set_items", "added_at"),
    ("song_files", "added_at"),
    ("song_artists", "added_at"),
    ("person_tags", "added_at"),
    ("site_tags", "added_at"),
    ("collection_tags", "added_at"),
    ("photo_set_tags", "added_at"),
    ("loop_tags", "added_at"),
    ("people_aliases", "added_at"),
    ("site_aliases", "added_at"),
    ("tag_aliases", "added_at"),
)


# Where a release can be found, as a list: the same shape a person's and a site's links have.
#
# NOT `assets.download_url`, and the distinction is the reason this table exists rather than the
# column being reused. `download_url` is where SIFT fetched this copy from: a fact about the file
# on the disk, never imported, and true of exactly one address. A stash-box's addresses are a
# statement about the release and there are several of them, so writing one into that column would
# both overwrite a fact about the copy and throw the rest away.
_CREATE_ASSET_LINKS = """
CREATE TABLE IF NOT EXISTS asset_links (
  id         TEXT PRIMARY KEY,
  asset_id   TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  url        TEXT NOT NULL,
  label      TEXT,
  created_at INTEGER NOT NULL,
  UNIQUE(asset_id, url)
)
"""


#: WHEN each attribution was decided, in seconds since the epoch, beside the word saying how
#: (`asset_people`, `asset_tags`, `asset_usernames`). Written by every writer, including the ones
#: that leave `source` NULL: a person's own act is an event too. Seconds, which is what
#: `file_moves.moved_at`, `workbench_decisions.decided_at` and `assets.added_at` hold; the face
#: tables are the exception and store milliseconds. NULL is a row from before anything recorded a
#: time, drawn as "before this was recorded" and sorted oldest.
#:
#: WHICH STASH-BOX, where `source` says a box decided it: `box_id` names the box whose answer made
#: the filing, so a line about it can say "added by FansDB" rather than "added by a stash-box". NULL
#: on every other source, and on a box filing nothing could attribute to one box. No key into
#: `stash_boxes`, for the reason `enrichment_runs.box_id` has none; every reader JOINs the box.
_CREATE_ASSET_PEOPLE = """
CREATE TABLE IF NOT EXISTS asset_people (
  asset_id  TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  -- How this was decided. Null when a person put it there; a word when Sift did, so a screen can
  -- say which and an inference can be found again later.
  source    TEXT,
  -- WHEN it was decided, in seconds (see the note over `asset_people`).
  decided_at INTEGER,
  -- Which stash-box decided it, where `source` is a box's (see the note over `asset_people`).
  box_id    TEXT,
  PRIMARY KEY(asset_id, person_id)
)
"""


#: Somebody taking a person off a file, remembered.
#:
#: Deleting the attribution says what is true NOW and nothing about what should happen next, which
#: is not enough on a library that keeps growing: a folder read as somebody's is re-read whenever
#: its faces change, and a name taken off by hand is put straight back. This is the memory that
#: makes the removal mean "she is not in this file" rather than "not at this moment".
#:
#: Its grain is the pairing, exactly like the attribution it undoes, which is why it is here beside
#: `asset_people` rather than inside the feature that reads folders: that feature reads this, it
#: does not own it, and the feature that writes it is the one that owns unassigning.
#:
#: Cleared when somebody attributes the same person again, so a change of mind needs no ceremony.
_CREATE_ASSET_PERSON_REFUSALS = """
CREATE TABLE IF NOT EXISTS asset_person_refusals (
  asset_id   TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  person_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  refused_at INTEGER NOT NULL,
  PRIMARY KEY(asset_id, person_id)
)
"""


_CREATE_ASSET_TAGS = """
CREATE TABLE IF NOT EXISTS asset_tags (
  asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  tag_id   TEXT NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
  -- How this got here. Null when a person put it on; a word when Sift did, naming what decided it.
  -- The same column `asset_people` carries, for the same reason and with the same meaning.
  --
  -- What it buys is the question somebody asks after a bulk import: which of these did I choose,
  -- and which arrived on their own. Without it the two are indistinguishable the moment the import
  -- finishes, so undoing one means undoing all of them or none. It also makes them filterable,
  -- which is the difference between "I can find what a stash-box added" and "I have to remember".
  source   TEXT,
  -- WHEN it was decided, in seconds (see the note over `asset_people`).
  decided_at INTEGER,
  -- Which stash-box decided it, where `source` is a box's (see the note over `asset_people`).
  box_id   TEXT,
  PRIMARY KEY(asset_id, tag_id)
)
"""


#: A file's usernames: an asset has no site of its own, it has usernames, and a username belongs to
#: a site, so a grant naming a site reaches an asset through two hops.
#:
#: `post_id` is the POST a file came down in, where a pass read it out of the file names: the
#: second the username's files came down in, as digits, because the site gives no id for a post and
#: this is the one thing every file of a post carries. Which files belong to one post is decided in
#: `suggestions.naming.one_post` and nowhere else. NULL is the honest answer for a filing some other
#: writer made (a person, a download, a stash-box): nothing there was read from a name. A second
#: kind of value here (a site's real post id) would be a design question, not an extension: two
#: kinds told apart by their length cannot be read back safely.
_CREATE_ASSET_USERNAMES = """
CREATE TABLE IF NOT EXISTS asset_usernames (
  asset_id    TEXT NOT NULL REFERENCES assets(id)    ON DELETE CASCADE,
  username_id TEXT NOT NULL REFERENCES usernames(id) ON DELETE CASCADE,
  -- How this filing was decided. The third table to carry this column and it means exactly what it
  -- means on the other two: null when a person put it there, a word when Sift did, so a file's
  -- SITE answers "who decided this" as its people and tags do.
  source     TEXT,
  -- WHEN it was decided, in seconds (see the note over `asset_people`).
  decided_at  INTEGER,
  -- The POST these files came down in, where a pass read one out of the names (see the note over
  -- this table).
  post_id     TEXT,
  -- Which stash-box decided it, where `source` is a box's (see the note over `asset_people`).
  box_id      TEXT,
  PRIMARY KEY(asset_id, username_id)
)
"""


# The full-text index over everything an asset can be found by, in the kernel because the resolver
# joins it: free text filters the scoped read inside the statement that decides visibility, so the
# page and its total come from one statement, and a count filtered afterwards would say whether a
# named file exists without showing it. The search language, the reindex job and the endpoints
# belong to the feature that owns the box.
#
# `tokenize='trigram'` makes a partial word match (every run of three characters); a term shorter
# than three characters matches nothing, on purpose. NOT contentless: re-indexing one asset on every
# tag or name change needs the original text, so the index keeps its own copy (a few megabytes); it
# stays a rebuildable cache. `asset_id UNINDEXED` rather than the rowid: `assets.id` is TEXT, so
# VACUUM (how a backup is taken) may renumber rowids, and the column costs nothing to search.
_CREATE_ASSETS_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS assets_fts USING fts5(
  asset_id UNINDEXED,
  title, filename, path, tags, people, usernames, collections, sites, music,
  tokenize='trigram')
"""


# Which asset each indexed row is, and where it sits. An ordinary table, and it exists because an
# FTS5 table cannot answer either question quickly.
#
# `assets_fts.asset_id` is UNINDEXED (it has to be, or the ids would be searchable as text), so
# every lookup by it is a full scan of the index. On fifty thousand rows, replacing one asset's row
# costs a scan (a rebuild goes quadratic), and asking which assets are not yet indexed takes
# minutes. Both are ordinary things this feature has to do constantly.
#
# So the join key is the FTS row's own rowid, kept here beside the asset id with a real index on
# it. An FTS5 rowid is the INTEGER PRIMARY KEY of its backing table, so unlike `assets.rowid` it is
# stable across VACUUM, which is what makes it safe to store, and is exactly the hazard that made
# storing `assets.rowid` unacceptable.
#
# It is still a cache. Both tables are rebuilt together from the tables that are the truth, and a
# row here without its FTS row (or the reverse) is corrected by the next rebuild.
_CREATE_ASSETS_FTS_ROWS = """
CREATE TABLE IF NOT EXISTS assets_fts_rows (
  asset_id TEXT PRIMARY KEY,
  fts_rowid INTEGER NOT NULL
)
"""


_CREATE_COLLECTION_ITEMS = """
CREATE TABLE IF NOT EXISTS collection_items (
  collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  asset_id      TEXT NOT NULL REFERENCES assets(id)      ON DELETE CASCADE,
  position      INTEGER,
  added_at      INTEGER,
  PRIMARY KEY(collection_id, asset_id)
)
"""


# And on a collection, for the reason above: a tag means the same thing wherever it is put, so a
# collection that cannot carry one is the odd exception rather than a decision anybody made.
_CREATE_COLLECTION_TAGS = """
CREATE TABLE IF NOT EXISTS collection_tags (
  collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  tag_id        TEXT NOT NULL REFERENCES tags(id)        ON DELETE CASCADE,
  added_at      INTEGER,
  PRIMARY KEY(collection_id, tag_id)
)
"""


# A collection and a tag can be hidden, hearted and rated, and every one of those is personal to a
# user, so each needs a row per (thing, user) exactly as a person and a site have. Named
# `*_user_state`, because what makes them one family is the shape of the relationship rather than
# the columns: keyed on the thing and the user, cascading from both, sparse. A row exists only where
# somebody said something.
#
# Separate tables for the reason given above `person_user_state`, which has not changed: a foreign
# key cannot be polymorphic, and the cascade is what stops a deleted tag's rows outliving it and
# attaching themselves to whatever reuses the id.
_CREATE_COLLECTION_USER_STATE = """
CREATE TABLE IF NOT EXISTS collection_user_state (
  collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  user_id       TEXT NOT NULL REFERENCES users(id)       ON DELETE CASCADE,
  favorite      INTEGER NOT NULL DEFAULT 0,
  rating        INTEGER CHECK(rating IS NULL OR rating BETWEEN 1 AND 10),
  hidden        INTEGER NOT NULL DEFAULT 0,
  hidden_at     INTEGER,
  pinned        INTEGER NOT NULL DEFAULT 0,
  updated_at    INTEGER NOT NULL,
  PRIMARY KEY (collection_id, user_id)
)
"""


_CREATE_COLLECTIONS = """
CREATE TABLE IF NOT EXISTS collections (
  id             TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  cover_asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
  owner_id       TEXT REFERENCES users(id) ON DELETE CASCADE,
  created_at     INTEGER NOT NULL,
  cover_at_ms INTEGER,
  name_sort TEXT,
  cover_upload_id TEXT,
  created_by_kind TEXT,
  created_by_user_id TEXT,
  created_by_via TEXT,
  cover_frame TEXT,
  cover_cleared_at INTEGER,
  cover_by_default TEXT,
  edited_at INTEGER
)
"""


#: A COVER PICTURE SOMEBODY UPLOADED, which is the one cover that is not a file in the library.
#:
#: Pick-a-frame answers "which of my files", and it cannot answer "this photograph of her that is
#: not in my library". So an entity's cover is one of two things and never both: a pointer at an
#: asset, or a pointer at a row here. The pointer columns are written by ONE statement per entity,
#: so choosing either clears the other and there is no arrangement in which both are set.
#:
#: THE UPLOADED BYTES ARE NEVER STORED AND NEVER TOUCH THE DISK. What this row names is Sift's
#: OWN JPEG, re-encoded from what arrived by piping it into ffmpeg on standard input (see
#: `kernel.covers.receive_cover_picture`). That is the security property of the whole feature and it
#: is stronger than deleting the original afterwards, because the original is never a file: it
#: disposes of camera metadata, of polyglot files that are a picture and an archive at once, and of
#: every decoder bug in every browser that will ever open what is served.
#:
#: KEPT OUT OF THE LIBRARY on purpose, and that is not tidiness. An uploaded cover must never become
#: an asset: `assets` is what a scan found on a disk, and a row there that no disk has would be a
#: file the library claims to hold and cannot produce. It is also what makes "a scraped picture
#: never replaces a chosen one" true by construction rather than by every writer remembering it.
#:
#: `rel_cache_path` is relative to the cache directory for the same reason a derivative's is: the
#: cache is the one directory Sift owns, it is safe to delete, and a stored absolute path is a row
#: that stops resolving the day somebody moves their data folder.
_CREATE_COVER_PICTURES = """
CREATE TABLE IF NOT EXISTS cover_pictures (
  id             TEXT PRIMARY KEY,
  rel_cache_path TEXT NOT NULL,
  size_bytes     INTEGER,
  created_at     INTEGER NOT NULL
)
"""


#: THE LAST ENRICHMENT of one subject against one box, and whether a person pressed it.
#:
#: One row per subject per box, replaced rather than appended, and that is the whole shape of it:
#: what a history line and a menu's "Last:" both want is the LAST one, and a table that grew a row
#: per run would make the commonest question ("when was this last enriched") a scan of every
#: run ever made. What is lost is the history of runs, which nothing asks for: the history pane
#: already draws the applied match itself, and that row carries its own decision time.
#:
#: `automatic` is the fact that cannot be recovered afterwards. A link written by Auto-enrich and
#: one written by somebody pressing Enrich leave identical rows everywhere else in this database,
#: and "did I do this or did the machine" is the first question anybody asks of a field they did
#: not expect.
#:
#: NO `REFERENCES stash_boxes(id)`, for the reason `people.created_by_box_id` has none: the
#: stash-box tables belong to a component initialised AFTER this one, and SQLite resolves a key's
#: parent when the CHILD is written, so the constraint would make every write here fail on a
#: database whose stash-box component has not been brought up. What the key would have bought is
#: bought where it is read: every reader JOINs `stash_boxes`, so a row naming a box that has been
#: removed simply has nothing to say.
#:
#: NOR a key into the subject's own table, and that is the same problem said about four parents: a
#: subject is a file, a person, a Site or a tag, so there is no single table to point at. What
#: a key would have bought (no row outliving its subject) is bought by the same JOIN.
_CREATE_ENRICHMENT_RUNS = """
CREATE TABLE IF NOT EXISTS enrichment_runs (
  id        TEXT PRIMARY KEY,
  subject   TEXT NOT NULL CHECK(subject IN ('asset','person','site','tag')),
  local_id  TEXT NOT NULL,
  box_id    TEXT NOT NULL,
  at        INTEGER NOT NULL,
  automatic INTEGER NOT NULL DEFAULT 0,
  -- What the run filled in: nothing for a bare link, `{}` for a plan that filled nothing, else
  -- field -> count.
  applied   TEXT
) WITHOUT ROWID
"""


# A loop carries its OWN tags, and that is the point of the feature rather than a detail of it: a
# twenty-second moment is one thing while the file around it is many, and a tag that could only be
# put on the whole file cannot say which part of it is being described.
#
# It carries no people of its own, deliberately. The people in a moment are the people in the file,
# and two answers to "who is in this" is a fault rather than a feature, so the person chips on a
# loop come from its source. If per-loop people are ever genuinely wanted they are an edge table
# added then, not a column guessed at now.
_CREATE_LOOP_TAGS = """
CREATE TABLE IF NOT EXISTS loop_tags (
  loop_id TEXT NOT NULL REFERENCES loops(id) ON DELETE CASCADE,
  tag_id  TEXT NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
  added_at INTEGER,
  PRIMARY KEY(loop_id, tag_id)
)
"""


# --- loops --------------------------------------------------------------------------------------
#
# A stretch of one video, kept so it can be gone back to. The twenty seconds worth watching in a
# forty-minute file.
#
# ## Why a row and not a file
#
# `media_edit` can already cut a range out of a video and stream-copy it into a new asset, so a loop
# COULD have been a produced file. It is a row instead, because of what that costs: a file takes
# disk, takes time, and has to be built before it can be looked at. Keeping a moment has to be as
# cheap as a bookmark or nobody keeps two hundred of them, and a Loops screen with nothing on it is
# the feature failing. Exporting one to a real file is a button that calls the operation that
# already exists, so both readings are available and only one of them is paid for by default.
#
# ## Why there is no grant, no vault flag and no visibility rule of its own
#
# A loop is a pointer into a file, so it is visible exactly when that file is and concealed exactly
# when that file is. `_VISIBLE_LOOPS` joins the resolver's visible set and asks nothing else, which
# makes "you cannot be shown a loop of something you cannot be shown" true by construction rather
# than true because a second rule was kept in step with the first. It is also why `ObjectType` has
# no member for a loop: there is nothing to grant that granting the file would not already do.
_CREATE_LOOPS = """
CREATE TABLE IF NOT EXISTS loops (
  id         TEXT PRIMARY KEY,
  asset_id   TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  -- Where it starts and where it ends, in milliseconds from the beginning of the file. Both are
  -- required and the end must be after the start: a loop with no length is not a loop, and the
  -- check is here rather than only in the service so that no write path can produce one.
  start_ms   INTEGER NOT NULL CHECK(start_ms >= 0),
  end_ms     INTEGER NOT NULL,
  name       TEXT,
  created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at INTEGER NOT NULL,
  edited_at  INTEGER,
  CHECK(end_ms > start_ms)
)
"""


#: A cover is one of three things and never two: a frame of a file (`cover_asset_id`, with
#: `cover_at_ms` for the moment and `cover_frame` for how it is cropped), a face found in that same
#: file (`cover_track_id`, stored ALONGSIDE the asset so whether it may be shown is decided on the
#: asset, and carrying no key because faces are a feature built on top of this component), or a
#: picture somebody uploaded (`cover_upload_id`, see `cover_pictures`).
#:
#: WHO MADE THIS ROW: `created_by_kind` is 'box', 'sift' or 'account'. `created_by_box_id` says
#: WHICH box, and carries no key for the reason `enrichment_runs.box_id` has none; `created_by_via`
#: says which of Sift's passes (in the vocabulary the filings use, see `catalog.py`); and
#: `created_by_user_id` names the user where the write path knew one. A NULL user means "the row
#: does not say", not "Sift", so a reader decides on the KIND and uses the id only to tell "you"
#: from "somebody else". The same five columns are on sites, tags, collections and Photo Sets.
#:
#: `name_sort` is the order the name is read in (see `SORT_KEYS`), and `keep_local` is the refusal
#: to send anything about this person outside the machine: "kept local" is the word on screen.
#:
#: `cover_cleared_at` is when a person took the cover away by hand, NULL once anybody chooses one
#: again, and `cover_by_default` the file Sift's own rule gave as the picture where there was none:
#: see `default_covers`, which is the rule and says why both are columns. The same two are on
#: sites, tags, collections and Photo Sets; on a Site and a tag the rule leaves (catalog 76),
#: `cover_by_default` stays NULL, and a Site's `drawn_by_pack` is read by nothing.
_CREATE_PEOPLE = """
CREATE TABLE IF NOT EXISTS people (
  id             TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  cover_asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
  notes          TEXT,
  created_at     INTEGER NOT NULL,
  cover_track_id TEXT,
  -- What a stash-box says about a person, where one was asked.
  disambiguation TEXT,
  gender TEXT,
  birth_date TEXT,
  country TEXT,
  ethnicity TEXT,
  eye_color TEXT,
  hair_color TEXT,
  height_cm INTEGER,
  measurements TEXT,
  breast_type TEXT,
  career_start_year INTEGER,
  career_end_year INTEGER,
  tattoos TEXT,
  piercings TEXT,
  cover_at_ms INTEGER,
  name_sort TEXT,
  cover_upload_id TEXT,
  pmv_creator INTEGER NOT NULL DEFAULT 0,
  created_by_box_id TEXT,
  created_by_kind TEXT,
  created_by_user_id TEXT,
  created_by_via TEXT,
  keep_local INTEGER NOT NULL DEFAULT 0,
  cover_frame TEXT,
  keep_from_swaps INTEGER NOT NULL DEFAULT 0,
  cover_cleared_at INTEGER,
  cover_by_default TEXT,
  edited_at INTEGER
)
"""


# The explicit "also known as" list. A person is findable by three things (the name on their
# row, an alias here, and any username linked to them), and this is the only one of
# the three that exists to be typed in by hand.
#
# `alias` is NOCASE in the unique key for the same reason a platform name is: "Jane" and "jane"
# added to the same person are one alias rather than a near-duplicate sitting in a list with
# nothing to tell them apart.
#
# SQLite's NOCASE folds unaccented A-Z and nothing else, which is worth knowing rather than
# discovering. Two spellings of a name differing only in the case of a plain Latin letter are one
# alias; two differing in the case of an accented letter, or of any letter outside A-Z, are two.
# Fixing that means folding in Python on the way in and storing a separate key, which is a real
# change and not this one, so the limit is written down here instead of being implied away.
#
# No uniqueness ACROSS people, deliberately. Two people really can be known by the same name, and
# refusing the second one would be asserting something about the world that is not true. The
# resolver returns every person a term matches and the caller disambiguates.
_CREATE_PEOPLE_ALIASES = """
CREATE TABLE IF NOT EXISTS people_aliases (
  id        TEXT PRIMARY KEY,
  person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  alias     TEXT NOT NULL,
  alias_sort TEXT,
  added_at  INTEGER,
  UNIQUE(person_id, alias COLLATE NOCASE)
)
"""


# Where somebody can be found: a profile page, a site, anything with an address.
#
# A row per link rather than a column on the person, because there is no useful maximum. Somebody
# has a page on four sites and somebody else has none, and a fixed set of columns would have to
# guess which four.
#
# `site_id` is the site the link is on when Sift recognizes it, and null when it does not. Not
# required, deliberately: refusing a link because its site is unknown would make the field useless
# for exactly the addresses somebody most wants to keep.
#
# Unique on (person, url) so importing the same set twice adds nothing the second time. The URL is
# stored as it was given: normalizing it would be deciding that two spellings of an address are
# the same thing, which is true often enough to be tempting and wrong often enough to lose a link.
_CREATE_PEOPLE_LINKS = """
CREATE TABLE IF NOT EXISTS people_links (
  id          TEXT PRIMARY KEY,
  person_id   TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  url         TEXT NOT NULL,
  site_id     TEXT REFERENCES sites(id) ON DELETE SET NULL,
  label       TEXT,
  created_at  INTEGER NOT NULL,
  UNIQUE(person_id, url)
)
"""


# The same tags, on people and sites. The same `tags` table deliberately: a tag called
# "archive" means the same thing wherever it is put, and two tag vocabularies that cannot see each
# other is the thing a shared table exists to prevent.
_CREATE_PERSON_TAGS = """
CREATE TABLE IF NOT EXISTS person_tags (
  person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  tag_id    TEXT NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
  added_at  INTEGER,
  PRIMARY KEY(person_id, tag_id)
)
"""


# A person and a site can be hearted, rated and tagged, exactly as a file can.
#
# ## Why these are separate tables rather than one polymorphic one
#
# The obvious economy is a single `entity_user_state(entity_type, entity_id, ...)` covering both,
# and it was rejected for one reason: a foreign key cannot be polymorphic. `ON DELETE CASCADE` is
# what stops a deleted person's stars outliving them and attaching themselves to whatever reuses
# the id, and a shared table can only be kept tidy by remembering to sweep it by hand from every
# delete path. That is the kind of cleanup that is correct on the day it is written.
#
# So each table names the thing it belongs to, cascades from it, and mirrors `asset_user_state`
# line for line. Three tables in one shape beats one table in a shape nobody has seen before.
#
# ## Per user, like the asset's
#
# A rating is somebody's opinion, not a property of the person being rated. Two users sharing an
# install rate independently, and the resolver scopes reads to whoever is asking: the same rule
# the grid's hearts and stars already follow, and the reason `user_id` is in the key.
_CREATE_PERSON_USER_STATE = """
CREATE TABLE IF NOT EXISTS person_user_state (
  person_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  user_id    TEXT NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
  favorite   INTEGER NOT NULL DEFAULT 0,
  rating     INTEGER CHECK(rating IS NULL OR rating BETWEEN 1 AND 10),
  hidden     INTEGER NOT NULL DEFAULT 0,
  hidden_at  INTEGER,
  pinned     INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (person_id, user_id)
)
"""


# `position` for the reason a collection has one: a shoot is a sequence, and showing it shuffled is
# showing something else. Filled from the filename order when a set is derived, rearrangeable after.
_CREATE_PHOTO_SET_ITEMS = """
CREATE TABLE IF NOT EXISTS photo_set_items (
  photo_set_id TEXT NOT NULL REFERENCES photo_sets(id) ON DELETE CASCADE,
  asset_id     TEXT NOT NULL REFERENCES assets(id)     ON DELETE CASCADE,
  position     INTEGER,
  added_at     INTEGER,
  PRIMARY KEY(photo_set_id, asset_id)
)
"""


_CREATE_PHOTO_SET_TAGS = """
CREATE TABLE IF NOT EXISTS photo_set_tags (
  photo_set_id TEXT NOT NULL REFERENCES photo_sets(id) ON DELETE CASCADE,
  tag_id       TEXT NOT NULL REFERENCES tags(id)       ON DELETE CASCADE,
  added_at     INTEGER,
  PRIMARY KEY(photo_set_id, tag_id)
)
"""


# The heart, the stars and the hidden flag, per user: the same sparse per-user row every
# other kind of thing in the catalog carries, and a table of its own for the reason theirs are: a
# foreign key cannot be polymorphic, and cascade from both sides is what stops a deleted set's rows
# outliving it and attaching themselves to whatever reuses the id.
_CREATE_PHOTO_SET_USER_STATE = """
CREATE TABLE IF NOT EXISTS photo_set_user_state (
  photo_set_id TEXT NOT NULL REFERENCES photo_sets(id) ON DELETE CASCADE,
  user_id      TEXT NOT NULL REFERENCES users(id)      ON DELETE CASCADE,
  hidden       INTEGER NOT NULL DEFAULT 0,
  hidden_at    INTEGER,
  favorite     INTEGER NOT NULL DEFAULT 0,
  rating       INTEGER CHECK(rating IS NULL OR rating BETWEEN 1 AND 10),
  pinned       INTEGER NOT NULL DEFAULT 0,
  updated_at   INTEGER NOT NULL,
  PRIMARY KEY (photo_set_id, user_id)
)
"""


# --- photo sets ---------------------------------------------------------------------------------
#
# A set of pictures that arrived together and belongs together: one gallery fetched from a site, or
# one folder of stills. Not a collection with a flag: a collection is what a person put together, a
# photo set is derived from where its files came from, and the two carry different columns. In the
# kernel because the permission resolver joins it, as it does `collections`.
_CREATE_PHOTO_SETS = """
CREATE TABLE IF NOT EXISTS photo_sets (
  id             TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  cover_asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
  -- Where it came from, as a word and an address. `origin` is HOW it was made (a download, a
  -- folder, somebody's hands), and it is what lets a later pass tell a set it may refresh from one
  -- a person assembled by hand. `origin_url` is the page it was fetched from, null for the rest.
  origin         TEXT NOT NULL DEFAULT 'manual'
                 CHECK(origin IN ('manual','download','folder','archive','filename','shoot','stash_library')),
  origin_url     TEXT,
  -- The folder it was derived from, when it was derived from one. A real reference rather than a
  -- path string, so a folder disappearing takes the link with it instead of leaving a set claiming
  -- to mirror somewhere that is not there. SET NULL rather than CASCADE: the set is still a true
  -- grouping of files after the folder it was read from has gone.
  folder_id      TEXT REFERENCES folders(id) ON DELETE SET NULL,
  -- And the ARCHIVE it was derived from, when it was derived from one: which library, and where
  -- the `.zip` sits inside it. The PAIR is the identity, because a path alone cannot tell two
  -- libraries apart and both may hold a `galleries/100200.zip`. It is what lets a rescan of the
  -- same archive find the set it made last time rather than making another beside it.
  archive_root_id  TEXT REFERENCES library_roots(id) ON DELETE SET NULL,
  archive_rel_path TEXT,
  notes          TEXT,
  created_at     INTEGER NOT NULL,
  cover_at_ms INTEGER,
  name_sort TEXT,
  cover_upload_id TEXT,
  created_by_kind TEXT,
  created_by_user_id TEXT,
  created_by_via TEXT,
  cover_frame TEXT,
  cover_cleared_at INTEGER,
  cover_by_default TEXT,
  edited_at INTEGER
)
"""


# --- songs ---------------------------------------------------------------------------------------
#
# ONE PIECE OF MUSIC, and the files that carry it, named from a Site's page, AcoustID, another file
# with the same music, or by hand. Its identity is the AcoustID recording where it has one (held by
# the partial unique index), its name where it has none. In the kernel because the per-viewer
# counts and the walls read it and kernel code writes it, every write through
# `kernel/content/songs.py`, whose triggers keep `assets.music` equal to the song's name. No
# `owner_id`: a song is seen through its files, and hidden and shared on its own like every other
# thing with a page (`kernel/access/visibility.py`). Its artists are rows of their own (`artists`,
# `song_artists`) in AcoustID's order; the name keeps them too ("Title - Artists").
_CREATE_SONGS = """
CREATE TABLE IF NOT EXISTS songs (
  id                 TEXT PRIMARY KEY,
  name               TEXT NOT NULL,
  name_sort          TEXT,
  recording_id       TEXT,
  notes              TEXT,
  cover_asset_id     TEXT REFERENCES assets(id) ON DELETE SET NULL,
  cover_at_ms        INTEGER,
  cover_upload_id    TEXT,
  cover_frame        TEXT,
  cover_cleared_at   INTEGER,
  cover_by_default   TEXT,
  created_at         INTEGER NOT NULL,
  created_by_kind    TEXT,
  created_by_user_id TEXT,
  created_by_via     TEXT,
  edited_at          INTEGER
)
"""

# A file carries at most one song, so the file is the key. `source` is how the song came to be on
# it: `acoustid`, `site` (a Site's page), `shared` (another file with the same music, named in
# `from_asset_id`), `swap` (the song it arrived with from another install, catalog 82), or NULL for
# a person's own hand, which is the word every membership table here
# uses for that. `score` is AcoustID's confidence where it answered.
_CREATE_SONG_FILES = """
CREATE TABLE IF NOT EXISTS song_files (
  asset_id      TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  song_id       TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  source        TEXT CHECK (source IS NULL OR source IN ('acoustid', 'site', 'shared', 'swap')),
  from_asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
  score         REAL,
  added_at      INTEGER
)
"""

# The hide, the heart, the stars and the pin, per user: the same sparse per-user row every other
# kind of thing in the catalog carries, and a table of its own for the reason theirs are. The hide
# arrived with catalog 82 (see the note over `songs`).
_CREATE_SONG_USER_STATE = """
CREATE TABLE IF NOT EXISTS song_user_state (
  song_id    TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  hidden     INTEGER NOT NULL DEFAULT 0,
  hidden_at  INTEGER,
  favorite   INTEGER NOT NULL DEFAULT 0,
  rating     INTEGER CHECK(rating IS NULL OR rating BETWEEN 1 AND 10),
  pinned     INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (song_id, user_id)
)
"""

_SONG_INDEXES = (
    # One recording is one song.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_songs_recording"
    " ON songs(recording_id) WHERE recording_id IS NOT NULL",
    # The song a name names, which is how a name with no recording finds its row.
    "CREATE INDEX IF NOT EXISTS ix_songs_name ON songs(name COLLATE NOCASE)",
    # A song's files, which its page, its counts and a rename read.
    "CREATE INDEX IF NOT EXISTS ix_song_files_song ON song_files(song_id, asset_id)",
    "CREATE INDEX IF NOT EXISTS ix_song_user_state_favorite"
    " ON song_user_state(user_id, favorite) WHERE favorite = 1",
)

#: Catalog 82 lets a song arrive by swap: the exact fragment of `song_files`' stored CHECK it
#: widens, and what it becomes.
_SONG_SOURCES_WAS = "source IN ('acoustid', 'site', 'shared')"
_SONG_SOURCES_NOW = "source IN ('acoustid', 'site', 'shared', 'swap')"

#: Catalog 86: a Photo Set a Stash library's gallery made says so, by the pass word that made it.
_PHOTO_SET_ORIGINS_WAS = "origin IN ('manual','download','folder','archive','filename','shoot')"
_PHOTO_SET_ORIGINS_NOW = (
    "origin IN ('manual','download','folder','archive','filename','shoot','stash_library')"
)

#: Catalog 82's two columns on a library that has `song_user_state` without them.
_ADD_SONG_HIDDEN = "ALTER TABLE song_user_state ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0"
_ADD_SONG_HIDDEN_AT = "ALTER TABLE song_user_state ADD COLUMN hidden_at INTEGER"

#: ONE ARTIST, by name: AcoustID says an artist by name, and a person types one, so the name is what
#: makes two credits one artist (case aside, the unique index below). The five columns every row in
#: the catalog carries say who made it: the lookup, or the user who typed it.
_CREATE_ARTISTS = """
CREATE TABLE IF NOT EXISTS artists (
  id                 TEXT PRIMARY KEY,
  name               TEXT NOT NULL,
  name_sort          TEXT,
  created_at         INTEGER NOT NULL,
  created_by_kind    TEXT,
  created_by_user_id TEXT,
  created_by_via     TEXT
)
"""

#: A song's credits, in order (`position` from nought, AcoustID's order where it said them).
#: `source` is `acoustid` for a credit the lookup wrote, `swap` for one a file arrived with from
#: another install, and NULL for a person's own hand, the word
#: every membership table here uses for that. An artist goes when its last credit does (the door in
#: `kernel/content/songs.py`), so no artist is left credited with nothing.
_CREATE_SONG_ARTISTS = """
CREATE TABLE IF NOT EXISTS song_artists (
  song_id   TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  artist_id TEXT NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
  position  INTEGER NOT NULL,
  source    TEXT CHECK (source IS NULL OR source IN ('acoustid', 'swap')),
  added_at  INTEGER,
  PRIMARY KEY (song_id, artist_id)
) WITHOUT ROWID
"""

_CATALOG_82_INDEXES = (
    # One user's hidden songs, which the Hidden screen lists. Partial, as every kind's is.
    "CREATE INDEX IF NOT EXISTS ix_song_user_state_hidden"
    " ON song_user_state(user_id, hidden) WHERE hidden = 1",
    # One artist per name, case aside.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_artists_name ON artists(name COLLATE NOCASE)",
    # One artist's songs: the Music wall narrowed to an artist, and the artist facet.
    "CREATE INDEX IF NOT EXISTS ix_song_artists_artist ON song_artists(artist_id, song_id)",
)


#: HOW MANY FILES THE SEARCH INDEX HAS NOT REACHED, as one stored number.
#:
#: "Is anything unindexed?" is asked on requests, and its honest statement is an anti-join from
#: `assets` to `assets_fts_rows` stopping at the first file with no row. When there IS such a file
#: that is quick; when there is none (the normal state of a library at rest) it has to look at
#: every file to be sure, and no index can shorten "prove there is no row missing". It costs tens of
#: milliseconds on a large library, and grows with the library.
#:
#: So the answer is kept. Four triggers move it by one, and each asks exactly the question that
#: decides whether the file's state changed, so the number is the anti-join's COUNT at every commit:
#:
#: * a file arrives with no map row: one more waiting;
#: * a file leaves that had no map row: one fewer (one that HAD a row was never counted);
#: * a map row is written for a file that exists: one fewer;
#: * a map row is removed for a file that exists: one more (the file is waiting again).
#:
#: The map is written by `search_index._write_page` alone, which removes a file's row before writing
#: its new one, so a plain INSERT is all the triggers ever see: an `INSERT OR REPLACE` would
#: delete the old row without firing a delete trigger unless recursive triggers are on, and the
#: number would drift by one with no error. The writer uses a plain INSERT so a second row for one
#: file is a loud failure rather than a quiet one.
#:
#: One row, pinned to id 1 by a CHECK, so there is nowhere for a second number to come from.
_CREATE_SEARCH_UNINDEXED = """
CREATE TABLE IF NOT EXISTS search_unindexed (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  n  INTEGER NOT NULL
)
"""


#: Other names a site goes by.
#:
#: Its own table for the reason a person's aliases are: an alias is SEARCHED, so it is a row that
#: has to be indexed and joined rather than a list inside a column. `people_aliases` is the shape
#: this copies, down to the case-insensitive uniqueness: two spellings of one alias on one site
#: are one alias, and the second is a mistake rather than a second name.
_CREATE_SITE_ALIASES = """
CREATE TABLE IF NOT EXISTS site_aliases (
  id          TEXT PRIMARY KEY,
  site_id     TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  alias       TEXT NOT NULL,
  alias_sort  TEXT,
  added_at    INTEGER,
  UNIQUE(site_id, alias COLLATE NOCASE)
)
"""


# Where a SITE can be found, in the same shape and for the same reason.
#
# A site has more than one address: its own page, its page on the service that hosts it, a mirror.
# A single column could hold the first and lose the rest, which is exactly what a stash-box
# answering with five of them would do.
#
# The same shape as a person's rather than one polymorphic table over both, and that is the schema's
# standing answer: a foreign key cannot point at "whichever kind of thing this is", so nothing
# cascades and the only way to keep a shared table tidy is to remember to sweep it from every delete
# path. Two small tables that clean themselves up beat one that needs a sweep nobody will write.
#
# Unique on (site, url), so importing the same set twice adds nothing the second time.
_CREATE_SITE_LINKS = """
CREATE TABLE IF NOT EXISTS site_links (
  id          TEXT PRIMARY KEY,
  site_id     TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  url         TEXT NOT NULL,
  label       TEXT,
  created_at  INTEGER NOT NULL,
  UNIQUE(site_id, url)
)
"""


_CREATE_SITE_TAGS = """
CREATE TABLE IF NOT EXISTS site_tags (
  site_id  TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  tag_id   TEXT NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
  added_at INTEGER,
  PRIMARY KEY(site_id, tag_id)
)
"""


_CREATE_SITE_USER_STATE = """
CREATE TABLE IF NOT EXISTS site_user_state (
  site_id     TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  favorite    INTEGER NOT NULL DEFAULT 0,
  rating      INTEGER CHECK(rating IS NULL OR rating BETWEEN 1 AND 10),
  hidden      INTEGER NOT NULL DEFAULT 0,
  hidden_at   INTEGER,
  pinned      INTEGER NOT NULL DEFAULT 0,
  updated_at  INTEGER NOT NULL,
  PRIMARY KEY (site_id, user_id)
)
"""


#: A Site: where files come from. `parent_id` is the network a site hangs under. The cover, the
#: sort key, the maker and `keep_local` columns mean what they mean on `people`.
_CREATE_SITES = """
CREATE TABLE IF NOT EXISTS sites (
  id    TEXT PRIMARY KEY,
  name  TEXT NOT NULL UNIQUE COLLATE NOCASE,
  -- UNUSED, and kept on purpose: no statement reads or writes this column. Dropping it would mean
  -- rewriting the table on every library that has one. A later use for the name would be a new
  -- column, not this one.
  kind  TEXT,
  cover_asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
  notes TEXT,
  parent_id TEXT REFERENCES sites(id) ON DELETE SET NULL,
  cover_at_ms INTEGER,
  name_sort TEXT,
  cover_upload_id TEXT,
  created_by_box_id TEXT,
  created_at INTEGER,
  created_by_kind TEXT,
  created_by_user_id TEXT,
  created_by_via TEXT,
  keep_local INTEGER NOT NULL DEFAULT 0,
  cover_frame TEXT,
  keep_from_swaps INTEGER NOT NULL DEFAULT 0,
  cover_cleared_at INTEGER,
  cover_by_default TEXT,
  -- UNUSED since catalog 76, and kept for the reason `kind` above is: whether the shipped pack
  -- draws the Site, which only the default-cover rule read, and the rule leaves Sites alone.
  drawn_by_pack INTEGER NOT NULL DEFAULT 0,
  edited_at INTEGER
)
"""


#: Other names a tag goes by. Same shape and same reason as a site's.
_CREATE_TAG_ALIASES = """
CREATE TABLE IF NOT EXISTS tag_aliases (
  id     TEXT PRIMARY KEY,
  tag_id TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  alias  TEXT NOT NULL,
  alias_sort TEXT,
  added_at INTEGER,
  UNIQUE(tag_id, alias COLLATE NOCASE)
)
"""


_CREATE_TAG_USER_STATE = """
CREATE TABLE IF NOT EXISTS tag_user_state (
  tag_id     TEXT NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  favorite   INTEGER NOT NULL DEFAULT 0,
  rating     INTEGER CHECK(rating IS NULL OR rating BETWEEN 1 AND 10),
  hidden     INTEGER NOT NULL DEFAULT 0,
  hidden_at  INTEGER,
  pinned     INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (tag_id, user_id)
)
"""


_CREATE_TAGS = """
CREATE TABLE IF NOT EXISTS tags (
  id             TEXT PRIMARY KEY,
  name           TEXT NOT NULL UNIQUE COLLATE NOCASE,
  cover_asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
  created_at     INTEGER NOT NULL,
  description TEXT,
  category TEXT,
  cover_at_ms INTEGER,
  name_sort TEXT,
  cover_upload_id TEXT,
  created_by_box_id TEXT,
  created_by_kind TEXT,
  created_by_user_id TEXT,
  created_by_via TEXT,
  keep_local INTEGER NOT NULL DEFAULT 0,
  cover_frame TEXT,
  parent_id TEXT REFERENCES tags(id) ON DELETE SET NULL,
  keep_from_swaps INTEGER NOT NULL DEFAULT 0,
  cover_cleared_at INTEGER,
  cover_by_default TEXT,
  edited_at INTEGER,
  created_by_act TEXT
)
"""

#: A tag's parent, for a library made before tags had one. One parent at most, so the tags form a
#: tree and never a graph: a tag is filed in one place, and a wall can draw where it is. Deleting a
#: parent leaves its children where they are, at the top.
_ADD_TAG_PARENT = (
    "ALTER TABLE tags ADD COLUMN parent_id TEXT REFERENCES tags(id) ON DELETE SET NULL"
)
_INDEX_TAG_PARENT = "CREATE INDEX IF NOT EXISTS ix_tags_parent ON tags(parent_id)"

#: The People, Sites and Tags a Stash library imported, given their own maker word. They were made
#: under the stash-box word, which read as "Created by a stash-box" for rows no box ever made.
#:
#: A box's own creations mostly carry 'box'. The ones that do not are Sites made by a username a
#: box's answer brought (`catalog._record_arrival`): that arrival is an 'added' line by Sift's
#: stash-box pass, about the Site, written in the same moment the Site was made, and a Stash
#: library writes no such line. So a row with the pair and no such line came from a Stash library.
#: `{table}` and `{kind}` are this module's own words, never input.
_STASH_LIBRARY_MADE = (
    "UPDATE {table} SET created_by_via = 'stash_library'"
    " WHERE created_by_kind = 'sift' AND created_by_via = 'stash' AND created_by_box_id IS NULL"
    " AND NOT EXISTS (SELECT 1 FROM workbench_decisions d WHERE d.object_kind = '{kind}'"
    " AND d.object_id = {table}.id AND d.verb = 'added' AND d.actor_kind = 'sift'"
    " AND d.actor_id = 'stash'"
    " AND d.decided_at BETWEEN COALESCE({table}.created_at, 0) - 5"
    " AND COALESCE({table}.created_at, 0) + 5)"
)
_STASH_LIBRARY_MADE_WHERE_NOTHING_IS_RECORDED = (
    "UPDATE {table} SET created_by_via = 'stash_library'"
    " WHERE created_by_kind = 'sift' AND created_by_via = 'stash' AND created_by_box_id IS NULL"
)
_MADE_BY_KIND = (("people", "person"), ("sites", "site"), ("tags", "tag"))

#: The people a stash-box put on files, given the first of those files as their picture where they
#: have none of either kind. The stash-box's filing did not give one, which every other filing Sift
#: makes does (`catalog.give_person_a_cover_on`), so each of them was drawn as a letter on the People
#: wall beside files of their own. The writer gives one now; this is the same gap filled for the
#: people it was left open on. Only the stash-box's own rows, so it repairs exactly that writer and
#: decides nothing for a person somebody filed by hand.
_COVER_FROM_A_STASH_BOX_FILING = """
UPDATE people
   SET cover_asset_id = (
       SELECT ap.asset_id FROM asset_people ap
        WHERE ap.person_id = people.id AND ap.source = 'stash_box'
        ORDER BY ap.decided_at, ap.asset_id
        LIMIT 1)
 WHERE cover_asset_id IS NULL
   AND cover_upload_id IS NULL
   AND EXISTS (
       SELECT 1 FROM asset_people ap WHERE ap.person_id = people.id AND ap.source = 'stash_box')
"""

#: The picture every kind is drawn as when nobody chose one, for a library made before the rule:
#: the clear mark and the default's own file on each of the five tables that carry a cover, and on
#: a Site whether the shipped pack draws it. `default_covers.start` then writes the triggers and
#: gives every entity with no cover and a file its first file's picture, of the kinds this build's
#: rule reaches (a tag and a Site no longer: see step 76). `{table}` is one of this module's own
#: words, never input.
_COVER_TABLES = ("people", "sites", "tags", "collections", "photo_sets")
_ADD_COVER_CLEARED_AT = "ALTER TABLE {table} ADD COLUMN cover_cleared_at INTEGER"
_ADD_COVER_BY_DEFAULT = "ALTER TABLE {table} ADD COLUMN cover_by_default TEXT"
_ADD_SITES_DRAWN_BY_PACK = "ALTER TABLE sites ADD COLUMN drawn_by_pack INTEGER NOT NULL DEFAULT 0"

#: When a thing was last edited, on each table that holds a record: see `edited`. `{table}` is one
#: of `edited.EDITED_TABLES`, never input.
_ADD_EDITED_AT = "ALTER TABLE {table} ADD COLUMN edited_at INTEGER"

#: WHICH ACT made a tag the `produced` pass made (`vocabulary.MADE_ACTS`): Compress or the editor.
#: Null on every other tag. The backfill reads it off the files the tag was put on by that pass:
#: each one's own row in `produced_files` says which operation made it, and every editor operation
#: is one act. The earliest such file answers, since it is the one whose making created the tag.
#: A tag none of whose files is still there stays null and keeps the general mark.
_ADD_TAGS_CREATED_BY_ACT = "ALTER TABLE tags ADD COLUMN created_by_act TEXT"

_TAGS_CREATED_BY_ACT = """
UPDATE tags SET created_by_act = (
  SELECT CASE made.operation WHEN 'compress' THEN 'compress' ELSE 'edit' END
    FROM asset_tags filed
    JOIN produced_files made ON made.asset_id = filed.asset_id
   WHERE filed.tag_id = tags.id AND filed.source = 'produced'
   ORDER BY made.produced_at, made.asset_id
   LIMIT 1
)
 WHERE created_by_kind = 'sift' AND created_by_via = 'produced' AND created_by_act IS NULL
"""


#: A USERNAME: one person's name on one site. `name_sort` is the order the name is read in (see
#: `SORT_KEYS`).
_CREATE_USERNAMES = """
CREATE TABLE IF NOT EXISTS usernames (
  id           TEXT PRIMARY KEY,
  site_id      TEXT REFERENCES sites(id) ON DELETE SET NULL,
  name         TEXT NOT NULL,
  display_name TEXT,
  url          TEXT,
  person_id    TEXT REFERENCES people(id) ON DELETE SET NULL,
  created_at   INTEGER NOT NULL,
  -- The number the SITE calls this username, where anything knew it.
  --
  -- A name is what somebody is called and it changes; this does not. Without it a username that
  -- was renamed is a NEW username, and everything written on the old one (who it belongs to, what
  -- it holds) stays behind on a row nothing points at any more. That is the same fault a folder
  -- had before it was recognised by its contents rather than by its name.
  --
  -- It also REFUSES, which is the half worth having and the half no guess can do. Two names a
  -- letter apart look like one person misspelled, and a near-miss would offer to merge them; two
  -- different numbers say plainly that they are two people.
  --
  -- Nullable, because most of what is known about a username is only ever its name.
  number TEXT,
  -- HOW that number got here: 'metadata' where Sift read it off the pictures' own fields, 'typed'
  -- where an admin put it there, NULL where it arrived by a path that records nothing about itself
  -- (the filename hunt, a download, a stash-box seed). NULL is an answer here ("the row does not
  -- say") and not a gap to be filled in later. See `catalog.NUMBER_VIAS`.
  number_via TEXT,
  -- And how many of that username's pictures agreed on the name, where pictures were read. The one
  -- half of that sentence nothing could recover afterwards: which pictures were read is deliberately
  -- not written down, so the count has to be.
  number_agreed INTEGER,
  name_sort    TEXT,
  edited_at    INTEGER,
  UNIQUE(site_id, name)
)
"""


_CATALOG_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_asset_links_asset ON asset_links(asset_id)",
    "CREATE INDEX IF NOT EXISTS ix_asset_people_person ON asset_people(person_id)",
    "CREATE INDEX IF NOT EXISTS ix_asset_people_source_decided ON asset_people(source, decided_at)",
    "CREATE INDEX IF NOT EXISTS ix_asset_person_refusals_person ON asset_person_refusals(person_id)",
    # Every membership lookup in the resolver starts from an asset.
    "CREATE INDEX IF NOT EXISTS ix_asset_tags_tag ON asset_tags(tag_id)",
    # The files of one post, as a seek: `username_id` first because a post is only a post under a
    # username, `asset_id` last so the whole answer comes out of the index. Partial, because a
    # filing with no post is every row a person, a download or a stash-box wrote.
    "CREATE INDEX IF NOT EXISTS ix_asset_usernames_post"
    " ON asset_usernames(username_id, post_id, asset_id) WHERE post_id IS NOT NULL",
    # How a pass of Sift's own reaches what IT filed: `source` first, because every question here
    # starts "what did this pass do"; the username second, because one card asks for one
    # username's files; `asset_id` last, so the rows arrive sorted and a capped read stops reading.
    "CREATE INDEX IF NOT EXISTS ix_asset_usernames_source"
    " ON asset_usernames(source, username_id, asset_id)",
    "CREATE INDEX IF NOT EXISTS ix_asset_usernames_username ON asset_usernames(username_id)",
    "CREATE INDEX IF NOT EXISTS ix_collection_items_asset ON collection_items(asset_id)",
    "CREATE INDEX IF NOT EXISTS ix_collection_tags_tag ON collection_tags(tag_id)",
    "CREATE INDEX IF NOT EXISTS ix_cus_favorite"
    " ON collection_user_state(user_id, favorite) WHERE favorite = 1",
    "CREATE INDEX IF NOT EXISTS ix_cus_hidden"
    " ON collection_user_state(user_id, hidden) WHERE hidden = 1",
    "CREATE INDEX IF NOT EXISTS ix_enrichment_runs_at ON enrichment_runs(subject, at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_enrichment_runs_subject"
    " ON enrichment_runs(subject, local_id, box_id, at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_loop_tags_tag ON loop_tags(tag_id)",
    # Every loop on one file, which the player asks for each time a video is opened.
    "CREATE INDEX IF NOT EXISTS ix_loops_asset ON loops(asset_id, start_ms)",
    # The kept-local rows, partial on the one value anybody looks for: the facet and the outbound
    # door ask it per subject, and nobody asks it of the zeros.
    "CREATE INDEX IF NOT EXISTS ix_people_kept_local ON people(id) WHERE keep_local = 1",
    # The same for "Do not swap", for the same reason.
    schema_columns.INDEX_PEOPLE_KEPT_FROM_SWAPS,
    # The resolver's direction of travel: a typed term, looking for who it belongs to.
    "CREATE INDEX IF NOT EXISTS ix_people_aliases_alias ON people_aliases(alias COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS ix_people_aliases_person ON people_aliases(person_id)",
    # One person's links, which is how they are read: opening somebody asks for theirs.
    "CREATE INDEX IF NOT EXISTS ix_people_links_person ON people_links(person_id)",
    # And the other direction, for a site being deleted: which links point at it.
    "CREATE INDEX IF NOT EXISTS ix_people_links_site ON people_links(site_id)",
    # The one lookup that decides whether an imported link is new: this address, on anybody.
    "CREATE INDEX IF NOT EXISTS ix_people_links_url ON people_links(url)",
    # And the direction a tag screen travels: given a tag, who carries it.
    "CREATE INDEX IF NOT EXISTS ix_person_tags_tag ON person_tags(tag_id)",
    # "Who have I hearted" is a screen, the same way favorite assets are. Partial, because the
    # rows nobody has hearted are most of the table and none of that query.
    "CREATE INDEX IF NOT EXISTS ix_pus_favorite"
    " ON person_user_state(user_id, favorite) WHERE favorite = 1",
    # One user's hidden set, which is what the Hidden screen lists. Partial for the reason the
    # favorite indexes are: the rows nobody hid are most of the table and none of that query.
    "CREATE INDEX IF NOT EXISTS ix_pus_hidden"
    " ON person_user_state(user_id, hidden) WHERE hidden = 1",
    # Which sets a file is in, asked once per item detail and once per related list.
    "CREATE INDEX IF NOT EXISTS ix_psi_asset ON photo_set_items(asset_id)",
    # A set's contents, in the arranged order.
    "CREATE INDEX IF NOT EXISTS ix_psi_set ON photo_set_items(photo_set_id, position)",
    "CREATE INDEX IF NOT EXISTS ix_pst_tag ON photo_set_tags(tag_id)",
    # One user's hidden sets, which the Hidden screen lists. Partial, because the rows nobody
    # hid are most of the table and none of that query.
    "CREATE INDEX IF NOT EXISTS ix_psus_hidden"
    " ON photo_set_user_state(user_id, hidden) WHERE hidden = 1",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_photo_sets_archive"
    " ON photo_sets(archive_root_id, archive_rel_path) WHERE archive_rel_path IS NOT NULL",
    # And the set derived from one folder, so a second pass over that folder finds the set it
    # already made rather than making another beside it.
    #
    # UNIQUE, not merely indexed. The lookup-before-create in the service is what normally stops a
    # second set being made, but a rule living in one place in the code and nowhere in the
    # database can be missing from one path (an archive's as well as a folder's). Enforced here, an
    # omission is an error at the moment it is made rather than a duplicate found on a wall weeks
    # later.
    # Partial, so it reaches only the sets that were derived from a folder.
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_photo_sets_folder"
    " ON photo_sets(folder_id) WHERE folder_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_site_aliases_alias ON site_aliases(alias COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS ix_site_aliases_site ON site_aliases(site_id)",
    "CREATE INDEX IF NOT EXISTS ix_site_links_site ON site_links(site_id)",
    "CREATE INDEX IF NOT EXISTS ix_site_tags_tag ON site_tags(tag_id)",
    "CREATE INDEX IF NOT EXISTS ix_site_user_state_favorite ON site_user_state(user_id, favorite)",
    "CREATE INDEX IF NOT EXISTS ix_site_user_state_hidden ON site_user_state(user_id, hidden)",
    "CREATE INDEX IF NOT EXISTS ix_sites_kept_local ON sites(id) WHERE keep_local = 1",
    schema_columns.INDEX_SITES_KEPT_FROM_SWAPS,
    "CREATE INDEX IF NOT EXISTS ix_sites_parent ON sites(parent_id)",
    "CREATE INDEX IF NOT EXISTS ix_tag_aliases_alias ON tag_aliases(alias COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS ix_tag_aliases_tag ON tag_aliases(tag_id)",
    "CREATE INDEX IF NOT EXISTS ix_tus_favorite"
    " ON tag_user_state(user_id, favorite) WHERE favorite = 1",
    "CREATE INDEX IF NOT EXISTS ix_tus_hidden ON tag_user_state(user_id, hidden) WHERE hidden = 1",
    "CREATE INDEX IF NOT EXISTS ix_tags_kept_local ON tags(id) WHERE keep_local = 1",
    schema_columns.INDEX_TAGS_KEPT_FROM_SWAPS,
    # A tag's children, read by its page and by the filter that takes in everything under it.
    _INDEX_TAG_PARENT,
    # What the people suggester needs to offer somebody by a username: "which usernames start with
    # this", and "which of them belong to this person". NOCASE on the name, matching how it is
    # compared: an index under another collation is one the query cannot use.
    "CREATE INDEX IF NOT EXISTS ix_usernames_name ON usernames(name COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS ix_usernames_person ON usernames(person_id)",
    "CREATE INDEX IF NOT EXISTS ix_usernames_site ON usernames(site_id)",
    # One username per number a site knows it by.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_usernames_number"
    " ON usernames(site_id, number) WHERE number IS NOT NULL",
)


#: A filename, folded, so a username's own files can be found by the name it opens with.
#:
#: It exists for exactly one statement, the read in `catalog` that learns the number a site knows
#: each username by, and the expression is spelled the way that statement spells it, because an index on an expression is matched by the expression and not by
#: the column. The sort index on file names is the same shape for the same reason.
#:
#: **On a table this component does not own, so it is a schema INVARIANT and not a migration step.**
#: `asset_locations` belongs to the content component, and SQLite drops an index with its table,
#: so a rebuild over there takes this away silently while nothing about the catalog's own version
#: moves. That is the case the invariant registry is for; see the note over
#: `register_schema_invariant`, which says the same thing about a trigger. An initializer would not
#: do: it is called only when its component's recorded version differs, so on a library already at
#: this version it never runs at all, and the read without the index takes minutes.
#:
#: Declared in THIS package, because the one statement that reads it is `catalog`'s and an index
#: nothing reads is an index nobody deletes.
#:
#: On a library of a hundred thousand locations it builds in well under a second, and the read it
#: answers goes from minutes to milliseconds. What it costs is one more B-tree entry per location written, once per file a scan
#: finds, orders of magnitude behind a read of the file itself.
_FILENAME_FOLDED_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_loc_filename_folded ON asset_locations(lower(filename))"
)

_SEED_SEARCH_UNINDEXED = """
INSERT OR REPLACE INTO search_unindexed (id, n)
SELECT 1, COUNT(*) FROM assets a
 WHERE NOT EXISTS (SELECT 1 FROM assets_fts_rows m WHERE m.asset_id = a.id)
"""

_SEARCH_UNINDEXED_TRIGGERS = (
    """
    CREATE TRIGGER IF NOT EXISTS search_unindexed_asset_in AFTER INSERT ON assets
    WHEN NOT EXISTS (SELECT 1 FROM assets_fts_rows m WHERE m.asset_id = NEW.id)
    BEGIN UPDATE search_unindexed SET n = n + 1 WHERE id = 1; END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS search_unindexed_asset_out AFTER DELETE ON assets
    WHEN NOT EXISTS (SELECT 1 FROM assets_fts_rows m WHERE m.asset_id = OLD.id)
    BEGIN UPDATE search_unindexed SET n = n - 1 WHERE id = 1; END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS search_unindexed_row_in AFTER INSERT ON assets_fts_rows
    WHEN EXISTS (SELECT 1 FROM assets a WHERE a.id = NEW.asset_id)
    BEGIN UPDATE search_unindexed SET n = n - 1 WHERE id = 1; END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS search_unindexed_row_out AFTER DELETE ON assets_fts_rows
    WHEN EXISTS (SELECT 1 FROM assets a WHERE a.id = OLD.asset_id)
    BEGIN UPDATE search_unindexed SET n = n + 1 WHERE id = 1; END
    """,
)


async def _keep_the_filename_index(connection: Connection) -> None:
    """Put the folded-filename index back, whatever any other component did to its table.

    Cheap when nothing is wrong (one look in `sqlite_master`) and idempotent when something is,
    which is what an invariant has to be. Guarded on the table because the schema tests build a
    catalog with no content component under it, and an index on a table that is not there would
    stop the boot rather than skip.
    """
    if await table_exists(connection, "asset_locations"):
        await connection.execute(_FILENAME_FOLDED_INDEX)


register_schema_invariant("catalog_filename_index", _keep_the_filename_index)


#: The four triggers that keep `search_unindexed`, as SQLite names them.
_SEARCH_UNINDEXED_TRIGGER_NAMES = (
    "search_unindexed_asset_in",
    "search_unindexed_asset_out",
    "search_unindexed_row_in",
    "search_unindexed_row_out",
)

_TRIGGERS_PRESENT = (
    "SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'trigger'"
    " AND name IN (SELECT value FROM json_each(?))"
)


async def _keep_the_unindexed_count(connection: Connection) -> None:
    """Put `search_unindexed`'s triggers back, and re-count, whatever another component did.

    A trigger lives and dies with its table: a later step anywhere that rebuilds `assets` the
    SQLite way (make a new table, copy, drop the old, rename) takes two of these four with it,
    and the number would then stop moving with no error at all while search quietly stopped
    finding new files. So every boot looks (one read of `sqlite_master`), and a missing trigger
    means the number cannot be trusted either: all four are made again and it is counted afresh.
    Guarded on the tables for `_keep_the_filename_index`'s reason.
    """
    for table in ("assets", "assets_fts_rows", "search_unindexed"):
        if not await table_exists(connection, table):
            return
    (row,) = list(
        await connection.execute_fetchall(
            _TRIGGERS_PRESENT, (json.dumps(_SEARCH_UNINDEXED_TRIGGER_NAMES),)
        )
    )
    if int(row[0]) == len(_SEARCH_UNINDEXED_TRIGGER_NAMES):
        return
    for trigger in _SEARCH_UNINDEXED_TRIGGERS:
        await connection.execute(trigger)
    await connection.execute(_SEED_SEARCH_UNINDEXED)


register_schema_invariant("catalog_unindexed_count", _keep_the_unindexed_count)


async def initialize_identity(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_USERS)
        await connection.execute(_CREATE_SECRETS)


async def initialize_access(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_ACL_GRANTS)
        for index in _ACCESS_INDEXES:
            await connection.execute(index)
    # Version 5: a song is shared and held back like every other thing with a page. Only the CHECK
    # widens, in the stored definition, so no grant moves and no trigger naming the table is lost.
    if 0 < on_disk < 5 and not await check_allows(connection, "acl_grants", "song"):
        await widen_a_check(connection, "acl_grants", was=_GRANT_KINDS_WAS, now=_GRANT_KINDS_NOW)


#: The catalog's tables in the order they are made: a table another names in a foreign key first.
_CATALOG_TABLES = (
    _CREATE_TAGS,
    _CREATE_ASSET_TAGS,
    _CREATE_PEOPLE,
    _CREATE_ASSET_PEOPLE,
    _CREATE_ASSET_PERSON_REFUSALS,
    _CREATE_PEOPLE_ALIASES,
    _CREATE_SITES,
    _CREATE_USERNAMES,
    _CREATE_ASSET_USERNAMES,
    _CREATE_PEOPLE_LINKS,
    _CREATE_SITE_LINKS,
    _CREATE_SITE_ALIASES,
    _CREATE_TAG_ALIASES,
    _CREATE_PERSON_TAGS,
    _CREATE_SITE_TAGS,
    _CREATE_PERSON_USER_STATE,
    _CREATE_SITE_USER_STATE,
    _CREATE_TAG_USER_STATE,
    _CREATE_COLLECTIONS,
    _CREATE_COLLECTION_ITEMS,
    _CREATE_COLLECTION_TAGS,
    _CREATE_COLLECTION_USER_STATE,
    _CREATE_PHOTO_SETS,
    _CREATE_PHOTO_SET_ITEMS,
    _CREATE_PHOTO_SET_TAGS,
    _CREATE_PHOTO_SET_USER_STATE,
    _CREATE_SONGS,
    _CREATE_SONG_FILES,
    _CREATE_SONG_USER_STATE,
    _CREATE_ARTISTS,
    _CREATE_SONG_ARTISTS,
    _CREATE_LOOPS,
    _CREATE_LOOP_TAGS,
    edited.CREATE_ASSET_EDITS,
    _CREATE_ASSET_LINKS,
    _CREATE_COVER_PICTURES,
    _CREATE_ENRICHMENT_RUNS,
    _CREATE_ASSETS_FTS,
    _CREATE_ASSETS_FTS_ROWS,
    _CREATE_SEARCH_UNINDEXED,
)


#: WHAT AN UNDO PUTS BACK, kept beside the receipt that took it (catalog 83): one row per thing a
#: take-back removed, in the order its Undo writes them back, each a JSON object read by
#: `catalog.put_back_on`. Beside the receipt rather than in its payload, because a person a take-back
#: removed comes back with her other names, links and starter faces, and every History page that
#: draws the line reads its payload whole. Keyed by the receipt's id, which is a ULID, and let go
#: once the Undo has run; no foreign key, because the ledger's table is another component's.
_CREATE_UNDO_ROWS = """
CREATE TABLE IF NOT EXISTS undo_rows (
  receipt_id TEXT NOT NULL,
  seq        INTEGER NOT NULL,
  row        TEXT NOT NULL,
  PRIMARY KEY (receipt_id, seq)
) WITHOUT ROWID
"""


async def file_tags_in_a_tree(connection: Connection) -> None:
    """Step 71: the one parent a tag may be filed under, and its index. Named so the step can be
    shown on the tags table alone, which the later steps (reading people, Sites and songs) cannot."""
    await connection.execute(_ADD_TAG_PARENT)
    await connection.execute(_INDEX_TAG_PARENT)


async def tag_what_an_act_made(connection: Connection) -> None:
    """Step 78: which act made each tag Sift put on a file it produced, read off those files.
    `produced_files` is the file-editing feature's, so a library that never ran it has none."""
    if not await column_exists(connection, "tags", "created_by_act"):
        await connection.execute(_ADD_TAGS_CREATED_BY_ACT)
    if await table_exists(connection, "produced_files"):
        await connection.execute(_TAGS_CREATED_BY_ACT)


#: Every table the catalog makes, read off the statements that make them.
_CATALOG_TABLE_NAMES = tuple(
    re.findall(
        r"CREATE TABLE IF NOT EXISTS (\w+)",
        "\n".join((*_CATALOG_TABLES, _CREATE_UNDO_ROWS, creator_studios.CREATE_CREATOR_STUDIOS)),
    )
)
_BROKEN_KEYS = "SELECT DISTINCT fkid FROM pragma_foreign_key_check(?)"
_KEY_COLUMNS = (
    'SELECT "table", "from", "to", upper(on_delete) FROM pragma_foreign_key_list(?)'
    " WHERE id = ? ORDER BY seq"
)
_BROKEN_ROWS = "{present} AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE {same})"
_LET_GO = {"SET NULL": "UPDATE {table} SET {cleared} WHERE {where}"}
_LET_GO_ROWS = "DELETE FROM {table} WHERE {where}"


def _named(identifier: object) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


async def let_go_of_broken_references(connection: Connection) -> None:
    """Step 90: a catalog row whose parent is gone, by SQLite's own check, gets what its key's
    ON DELETE would have done had the parent gone with foreign keys on."""
    for table in _CATALOG_TABLE_NAMES:
        for broken in await connection.execute_fetchall(_BROKEN_KEYS, (table,)):
            key = list(await connection.execute_fetchall(_KEY_COLUMNS, (table, broken[0])))
            own = [f"{_named(table)}.{_named(one[1])}" for one in key]
            present = " AND ".join(f"{mine} IS NOT NULL" for mine in own)
            pairs = zip(key, own, strict=True)
            same = " AND ".join(f"p.{_named(one[2])} = {mine}" for one, mine in pairs)
            where = _BROKEN_ROWS.format(present=present, parent=_named(key[0][0]), same=same)
            cleared = ", ".join(f"{_named(one[1])} = NULL" for one in key)
            change = _LET_GO.get(str(key[0][3]), _LET_GO_ROWS)
            statement = change.format(table=_named(table), cleared=cleared, where=where)
            # nosemgrep: sift-no-string-built-sql (names off this library's own keys, quoted)
            await connection.execute(statement)


async def initialize_catalog(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        for statement in (
            *_CATALOG_TABLES,
            *_CATALOG_INDEXES,
            *_SONG_INDEXES,
            *_CATALOG_82_INDEXES,
            *_SEARCH_UNINDEXED_TRIGGERS,
        ):
            await connection.execute(statement)
        await connection.execute(_SEED_SEARCH_UNINDEXED)
        await default_covers.start(connection)
        await connection.execute(creator_studios.CREATE_CREATOR_STUDIOS)
        await songs.start(connection)
    if 0 < on_disk < 71:
        await file_tags_in_a_tree(connection)
    if 0 < on_disk < 72:
        await schema_columns.keep_from_swaps(connection)
    if 0 < on_disk < 73:
        recorded = await table_exists(connection, "workbench_decisions")
        shape = _STASH_LIBRARY_MADE if recorded else _STASH_LIBRARY_MADE_WHERE_NOTHING_IS_RECORDED
        for table, kind in _MADE_BY_KIND:
            # Two module constants filled with two more: nothing from run time reaches the text.
            # nosemgrep: sift-no-string-built-sql
            await connection.execute(shape.format(table=table, kind=kind))
    if 0 < on_disk < 74:
        await connection.execute(_COVER_FROM_A_STASH_BOX_FILING)
    if 0 < on_disk < 75:
        # Each column only where it is missing, so a step run again brings nothing twice.
        for table in _COVER_TABLES:
            if not await column_exists(connection, table, "cover_cleared_at"):
                # nosemgrep: sift-no-string-built-sql
                await connection.execute(_ADD_COVER_CLEARED_AT.format(table=table))
            if not await column_exists(connection, table, "cover_by_default"):
                # nosemgrep: sift-no-string-built-sql
                await connection.execute(_ADD_COVER_BY_DEFAULT.format(table=table))
        if not await column_exists(connection, "sites", "drawn_by_pack"):
            await connection.execute(_ADD_SITES_DRAWN_BY_PACK)
        await default_covers.start(connection)
    if 0 < on_disk < 76:
        # The default-cover rule leaves tags and Sites: their triggers and given covers go back.
        await default_covers.take_back_tags_and_sites(connection)
    if 0 < on_disk < 77:
        # When each thing was last edited, for "Recently edited" (`edited`): the column where it is
        # missing, a file's table, and the first moments off the ledger. The triggers that keep it
        # are written by `edited.keep_true`, which runs at every boot after every step.
        for table in edited.EDITED_TABLES:
            if not await column_exists(connection, table, "edited_at"):
                # nosemgrep: sift-no-string-built-sql
                await connection.execute(_ADD_EDITED_AT.format(table=table))
        await connection.execute(edited.CREATE_ASSET_EDITS)
        await edited.backfill(connection)
    if 0 < on_disk < 78:
        await tag_what_an_act_made(connection)
    if 0 < on_disk < 79:
        # A cover nobody chose is the whole first picture, never a face: every face Sift made
        # somebody's cover goes back to the rule (`default_covers`), which logs how many.
        await default_covers.faces_back_to_the_rule(connection)
    if 0 < on_disk < 80:
        # A stash-box studio that is one creator's own store is her username, never a Site: every
        # Site a box made that reads so has its files moved, with a History line and an Undo each
        # (`creator_studios`), and the table that remembers each answer is made.
        await creator_studios.repair(connection)
    if 0 < on_disk < 81:
        # A song is a thing of its own: the three tables, the triggers that keep each file's Music
        # field equal to its song's name, and every song a file already carries moved onto a row,
        # with one History line saying how many (`kernel/content/songs.py`).
        for statement in (
            _CREATE_SONGS,
            _CREATE_SONG_FILES,
            _CREATE_SONG_USER_STATE,
            *_SONG_INDEXES,
        ):
            await connection.execute(statement)
        await songs.start(connection)
        await songs.move_named_songs(connection)
    if 0 < on_disk < 82:
        # A song is hidden like every other thing with a page and credits its artists as rows (the
        # note over `songs`); the music feature's own step fills the credits AcoustID already gave.
        if not await column_exists(connection, "song_user_state", "hidden"):
            await connection.execute(_ADD_SONG_HIDDEN)
        if not await column_exists(connection, "song_user_state", "hidden_at"):
            await connection.execute(_ADD_SONG_HIDDEN_AT)
        for statement in (_CREATE_ARTISTS, _CREATE_SONG_ARTISTS, *_CATALOG_82_INDEXES):
            await connection.execute(statement)
        # A file's song may arrive by swap: only the CHECK widens, in the stored definition, so no
        # membership moves and the triggers on `song_files` stay where they are.
        if not await check_allows(connection, "song_files", "swap"):
            await widen_a_check(
                connection, "song_files", was=_SONG_SOURCES_WAS, now=_SONG_SOURCES_NOW
            )
    if on_disk < 83:
        # What an Undo of a take-back puts back (`catalog.keep_for_undo_on`); the same for a new library.
        await connection.execute(_CREATE_UNDO_ROWS)
    if 0 < on_disk < 85:
        # Who pressed a pass over one file is an act in the event ledger, one per press and kept
        # for ever (`kernel.presses`). The table that kept only the latest press of each pass goes,
        # and each press it holds is carried into the ledger first, where its file's History reads
        # it. A library that never had the table has nothing to carry.
        await presses.carry_into_the_ledger(connection)
    if 0 < on_disk < 86 and not await check_allows(connection, "photo_sets", "stash_library"):
        # Only the CHECK widens, in the stored definition, so no set or membership moves.
        await widen_a_check(
            connection, "photo_sets", was=_PHOTO_SET_ORIGINS_WAS, now=_PHOTO_SET_ORIGINS_NOW
        )
    if 0 < on_disk < 87:
        await schema_columns.name_the_box_on_filings(connection)
    if on_disk < 88:
        await schema_columns.mark_folders(connection)
    if 0 < on_disk < 89:
        # A cover nobody chose is never a Hidden file while one nobody hides is filed there.
        await default_covers.out_of_hidden(connection)
    if 0 < on_disk < 90:
        await let_go_of_broken_references(connection)


register_schema_initializer(IDENTITY_COMPONENT, IDENTITY_VERSION, initialize_identity, baseline=4)
register_schema_initializer(
    CATALOG_COMPONENT,
    CATALOG_VERSION,
    initialize_catalog,
    depends_on=["content", IDENTITY_COMPONENT],
    baseline=70,
)
register_schema_initializer(
    ACCESS_COMPONENT,
    ACCESS_VERSION,
    initialize_access,
    depends_on=[IDENTITY_COMPONENT],
    baseline=4,
)
