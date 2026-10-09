# SPDX-License-Identifier: AGPL-3.0-or-later
"""The tables permission resolution reads: if the resolver joins a table, the kernel owns it."""

from __future__ import annotations

import json

from sift.kernel.access import creator_studios, default_covers, edited
from sift.kernel.access.schema_indexes import (
    _ACCESS_INDEXES,
    _CATALOG_82_INDEXES,
    _CATALOG_INDEXES,
    _INDEX_TAG_PARENT,
    _SONG_INDEXES,
)
from sift.kernel.content import songs
from sift.kernel.db import Connection, register_schema_initializer, register_schema_invariant
from sift.kernel.migrations import check_allows, table_exists, widen_a_check

IDENTITY_COMPONENT = "identity"
IDENTITY_VERSION = 4

CATALOG_COMPONENT = "catalog"
CATALOG_VERSION = 93

ACCESS_COMPONENT = "access"
ACCESS_VERSION = 5


# No email column, so an install leaks one thing less. `cache_stamp` rides in every picture's
# address, so raising it on any change to what this user may see makes every old address
# unreachable. `renames` and `renames_since` rate-limit a guest's renames, so taken names cannot be
# enumerated.
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

# Values sealed under the master key, which only a user's password unwraps
# (`kernel/secret_store.py`).
_CREATE_SECRETS = """
CREATE TABLE IF NOT EXISTS secrets (
  id         TEXT PRIMARY KEY,
  ciphertext BLOB NOT NULL,
  nonce      BLOB NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
)
"""


# `object_id` has no foreign key (it names one of several tables), so `Repository.forget_object()`
# deletes the grants of a deleted object. A share and a restrict may both exist, and restrict wins.
# `object_type` is a CHECK widened in place (`migrations.widen_a_check`), since every visibility
# trigger names this table.
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

#: Access version 5: the fragment of the stored CHECK widened so a grant can name a song.
_GRANT_KINDS_WAS = "'photo_set')"
_GRANT_KINDS_NOW = "'photo_set','song')"


#: Names ordered by a stored `sort_key`, as `(table, name column, key column)`: NOCASE folds ASCII
#: only. Read by `tests/gates/test_one_ordering.py`.
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

#: When a membership arrived or a site was made, as `(table, column)`; NULL is before this was
#: recorded. Read by `tests/gates/test_a_membership_says_when.py`.
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


# Where a release can be found, as a list. Not `assets.download_url`, which is where Sift fetched
# this copy.
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


#: `decided_at` is when each attribution was made, in seconds; NULL is before anything recorded it.
#: `box_id` names the stash-box whose answer made the filing, with no key into `stash_boxes`.
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


#: Somebody taking a person off a file, remembered, so a re-read of the folder does not put her
#: back. Cleared when the same person is attributed again.
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


#: A file's usernames: a grant naming a site reaches a file through its usernames. `post_id` is the
#: post a pass read out of the file names (`suggestions.naming.one_post`); NULL for any other
#: writer.
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


# The full-text index over everything a file can be found by, here because the visibility statement
# filters on it. Trigram, so a partial word matches; `asset_id UNINDEXED` because VACUUM may
# renumber rowids.
_CREATE_ASSETS_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS assets_fts USING fts5(
  asset_id UNINDEXED,
  title, filename, path, tags, people, usernames, collections, sites, music,
  tokenize='trigram')
"""


# Which file each indexed row is: the FTS rowid is stable across VACUUM, and `asset_id` in the index
# cannot be sought. A cache, rebuilt with the index.
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
  added_at      INTEGER,
  PRIMARY KEY(collection_id, asset_id)
)
"""


_CREATE_COLLECTION_TAGS = """
CREATE TABLE IF NOT EXISTS collection_tags (
  collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  tag_id        TEXT NOT NULL REFERENCES tags(id)        ON DELETE CASCADE,
  added_at      INTEGER,
  PRIMARY KEY(collection_id, tag_id)
)
"""


# Per user, sparse, cascading from both sides: a foreign key cannot be polymorphic.
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


#: A cover picture somebody uploaded. The bytes are never stored: the row names Sift's own
#: re-encoded JPEG (`kernel.covers.receive_cover_picture`), kept out of `assets` because no disk
#: holds it. `rel_cache_path` is relative to the cache directory.
_CREATE_COVER_PICTURES = """
CREATE TABLE IF NOT EXISTS cover_pictures (
  id             TEXT PRIMARY KEY,
  rel_cache_path TEXT NOT NULL,
  size_bytes     INTEGER,
  created_at     INTEGER NOT NULL
)
"""


#: The last enrichment of one subject against one stash-box, replaced rather than appended;
#: `automatic` says whether a person pressed it. No keys: the stash-box tables come up after this
#: component, and the subject may be one of four tables.
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


# A loop carries its own tags but no people: the people in a moment are the people in the file.
_CREATE_LOOP_TAGS = """
CREATE TABLE IF NOT EXISTS loop_tags (
  loop_id TEXT NOT NULL REFERENCES loops(id) ON DELETE CASCADE,
  tag_id  TEXT NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
  added_at INTEGER,
  PRIMARY KEY(loop_id, tag_id)
)
"""


# --- loops: a stretch of one video, kept as a row so keeping one costs no disk. Visible exactly
# when its file is, so it has no grant of its own.
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


#: A cover is a frame of a file, a face found in that file, or an uploaded picture, never two.
#: `created_by_*` say who made the row (a NULL user means the row does not say); the same columns
#: are on sites, tags, collections and Photo Sets. `cover_cleared_at` and `cover_by_default` are
#: `default_covers`'s.
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


# The typed "also known as" list. NOCASE in the key folds only A-Z. No uniqueness across people: two
# people can share a name.
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


# Where somebody can be found. `site_id` is null where Sift does not recognize the site; the URL is
# kept as given.
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


# The same `tags` table on people and sites, so there is one vocabulary.
_CREATE_PERSON_TAGS = """
CREATE TABLE IF NOT EXISTS person_tags (
  person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  tag_id    TEXT NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
  added_at  INTEGER,
  PRIMARY KEY(person_id, tag_id)
)
"""


# Per user, like the file's own: one table per kind, because a foreign key cannot be polymorphic and
# the cascade keeps it clean.
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


# Per user and sparse, for the reason the other kinds' tables are.
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


# --- photo sets: pictures that arrived together, derived from where they came from rather than put
# together by a person.
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


# --- songs: one piece of music and the files carrying it, every write through
# `kernel/content/songs.py`. Hidden and shared like every thing with a page.
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

# A file carries at most one song, so the file is the key. `source` NULL is a person's own hand.
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

#: One artist by name, case aside.
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

#: A song's credits, in order. An artist goes with its last credit (`kernel/content/songs.py`).
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


#: How many files the search index has not reached, kept by four triggers so the question needs no
#: anti-join over every file. The map is written by plain INSERT: OR REPLACE would delete without
#: firing a trigger.
_CREATE_SEARCH_UNINDEXED = """
CREATE TABLE IF NOT EXISTS search_unindexed (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  n  INTEGER NOT NULL
)
"""


#: Other names a site goes by: searched, so rows, as a person's aliases are.
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


# Where a site can be found: a table of its own, as a person's links have.
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


#: A Site: where files come from. `parent_id` is the network it hangs under.
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

#: A tag's one parent, so the tags form a tree.
_ADD_TAG_PARENT = (
    "ALTER TABLE tags ADD COLUMN parent_id TEXT REFERENCES tags(id) ON DELETE SET NULL"
)

#: The default cover's columns on the five tables that carry a cover. `{table}` is this module's own
#: word.
_COVER_TABLES = ("people", "sites", "tags", "collections", "photo_sets")


#: A username: one person's name on one site.
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


#: A folded filename index for the one read in `catalog` that learns a username's number. An
#: invariant, not a step: `asset_locations` is another component's table, and a rebuild there drops
#: it.
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
    """Put the folded-filename index back, whatever any other component did to its table."""
    if await table_exists(connection, "asset_locations"):
        await connection.execute(_FILENAME_FOLDED_INDEX)


register_schema_invariant("catalog_filename_index", _keep_the_filename_index)


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
    """Put `search_unindexed`'s triggers back and re-count: a rebuild of `assets` drops them
    silently."""
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
    # Version 5: only the stored CHECK widens, so no grant moves and no trigger is lost.
    if 0 < on_disk < 5 and not await check_allows(connection, "acl_grants", "song"):
        await widen_a_check(connection, "acl_grants", was=_GRANT_KINDS_WAS, now=_GRANT_KINDS_NOW)


#: In the order they are made: a table another names in a foreign key first.
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


#: What an Undo puts back (catalog 83), beside the receipt rather than in its payload, which History
#: reads whole. No key: the ledger is another component's.
_CREATE_UNDO_ROWS = """
CREATE TABLE IF NOT EXISTS undo_rows (
  receipt_id TEXT NOT NULL,
  seq        INTEGER NOT NULL,
  row        TEXT NOT NULL,
  PRIMARY KEY (receipt_id, seq)
) WITHOUT ROWID
"""


async def file_tags_in_a_tree(connection: Connection) -> None:
    """Step 71: the one parent a tag may be filed under, and its index."""
    await connection.execute(_ADD_TAG_PARENT)
    await connection.execute(_INDEX_TAG_PARENT)


async def initialize_catalog(connection: Connection, on_disk: int) -> None:
    from sift.kernel.access import schema_steps

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
    await schema_steps.from_71(connection, on_disk)
    await schema_steps.from_76(connection, on_disk)
    await schema_steps.from_81(connection, on_disk)
    await schema_steps.from_90(connection, on_disk)


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
