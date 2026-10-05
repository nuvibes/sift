# SPDX-License-Identifier: AGPL-3.0-or-later
"""The face tables: what was found, what it was matched against, and how far a scan got.

Its own schema component, not an extension of the content one. Faces are optional, most installs
will never turn them on, and an install that does must not share a migration counter with the
tables every install depends on.

The shape follows one rule, and everything else here follows from it: **the unit is a face, not a
file.** A photograph of three people is three faces, in three tracks, matched to three People
independently. So nothing found by this feature is keyed by asset alone, and there is no "this
file's person" column anywhere.

`face_asset_people` is the one table keyed by a file, and it is not an exception to that rule so
much as the seam where the rule stops applying. Sift as a whole *does* say that a person is on a
file (that is `asset_people`, which this feature does not own), and attributing a face means
writing there. What is kept here is only which of those pairs this feature put there, so that
withdrawing an attribution takes back its own claim and never a name somebody added by hand. It
holds no numbers, no crop and no decision; it is a record of authorship.

A track is one appearance: the same face continuing across the frames a video was sampled at,
carrying a time range. It is also the unit that gets counted, which is what makes a thirty-frame
clip of one person read as one person rather than thirty faces and one match.

Only faces that cleared the quality bar get a `face_detections` row, because that row exists to
hold an embedding and a crop, and a face below the bar is never embedded or cropped. The count of
frames a face was seen in lives on the track, so nothing is lost by not writing the rest down.

Crops live under the data directory rather than the cache. The cache is disposable by design and
these are not: rebuilding a detected crop costs a decode of the file it came from, and a reference
crop cannot be rebuilt at all.
"""

from __future__ import annotations

from sift.kernel.access.visibility import WAITING_FACES_KIND, Counted, register_counted
from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.migrations import check_allows, column_exists, widen_a_check

COMPONENT = "faces"
VERSION = 41

# Where a scan got to, per asset. The fifth status (never scanned) is the absence of a row,
# so it cannot drift out of step with reality by being written down somewhere and not updated.
#
# `settings_digest` is the tuning that produced the numbers, alongside the models that produced
# them. Both matter: a different model makes stored embeddings incomparable, and a different
# quality bar or sampling preset makes stored coverage incomparable. Without them there is no way
# to tell which files are stale after a change.
#
# `quality_version` and `sampling_version` are the two code versions the digest folds, kept as
# numbers as well, so a bump to either is a set a pass can be pointed at (`quality_version < N`)
# rather than the whole library. `settings_shape` is the tuning WITHOUT how hard it looked, and
# `settings_density` how many moments a pass was asked for: together they let the comparison be "is
# the stored pass at least as thorough", so turning the effort down does not re-read the library.
# A NULL in any of them never satisfies a comparison, so such a row is offered again.
#
# `reached_ms` is where a pass stopped, so the next one carries on; `cut_short` is whether the time
# limit stopped it, the one stop that leaves work in the file (a pass whose last moments would not
# decode keeps a position too, and a press means look again).
#
# `refused_small` and `refused_closer` are how many faces a pass found and REFUSED at each of its
# two gates, per MOMENT (`Outcome.refused_small`, `Outcome.refused_closer`), so a file's History
# can say why it found nobody. A reason, not a head count; NULL is not known, never zero.
#
# `refused_blurred`, `refused_turned` and `refused_edge` split `refused_closer` by the reason the
# closer look gave, and `refused_largest` is the long side, in the file's own pixels, of the biggest
# face refused for size. Together they let the History line say which reason and, for size, how
# big: "too unclear" alone left a person unable to tell a blurred face from one cut off by the edge.
# NULL on a scan made before version 35, which reads as the older, plainer line.
_CREATE_SCANS = """
CREATE TABLE IF NOT EXISTS face_scans (
  asset_id         TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  status           TEXT NOT NULL
                   CHECK(status IN ('no_faces','none_identified','some_identified','all_identified')),
  depth            TEXT NOT NULL CHECK(depth IN ('fast','deep')),
  coverage         REAL NOT NULL,
  frames_sampled   INTEGER NOT NULL,
  track_count      INTEGER NOT NULL,
  identified_count INTEGER NOT NULL,
  detector         TEXT NOT NULL,
  recognizer       TEXT NOT NULL,
  settings_digest  TEXT NOT NULL,
  settings_density REAL,
  quality_version  INTEGER,
  sampling_version INTEGER,
  scanned_at       INTEGER NOT NULL,
  reached_ms       INTEGER,
  settings_shape   TEXT,
  refused_small    INTEGER,
  refused_closer   INTEGER,
  cut_short        INTEGER,
  refused_largest  INTEGER,
  refused_blurred  INTEGER,
  refused_turned   INTEGER,
  refused_edge     INTEGER
)
"""


# What the mark on a screen reads, and what a rescan asks for its own file. Both narrow by a stored
# value rather than by a join through the crops, which is the whole of what v24 buys.
_INDEX_REFERENCE_TRACK = (
    "CREATE INDEX IF NOT EXISTS ix_face_references_track ON face_references(track_id)"
)
_INDEX_REFERENCE_ASSET = (
    "CREATE INDEX IF NOT EXISTS ix_face_references_asset ON face_references(person_id, asset_id)"
)

# The tuning one sweep runs under, captured when it starts.
#
# Every scan re-reads the settings for itself, so a change made while a sweep is going takes effect
# on the very next file: a run started on the processor and switched to a graphics card would fail
# every remaining file, and a run started deep and turned down would finish as a library part deep
# and part not. Nothing is corrupted by that (each result records what produced it), but the run
# stops meaning one thing, and the list of files the sweep decided to queue was decided under the
# settings at the moment it started.
#
# Only what decides WHAT IS SCANNED and HOW is kept here. The limits on how hard the machine may be
# worked are deliberately left out and stay live: "stop taking my whole processor" is not a request
# about some future run.
#
# One JSON column rather than a column per setting. This is a snapshot to be handed back whole, not
# something to be queried across, and a column per field is a migration every time the tuning
# gains one.
_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS face_runs (
  id         TEXT PRIMARY KEY,
  tuning     TEXT NOT NULL,
  created_at INTEGER NOT NULL
)
"""


# A pile of unidentified faces that resemble each other. Grouping them is what turns forty
# sightings of a stranger into one question.
#
# `ignored` is a status rather than a deletion, and that is the point of it: a pile somebody put
# aside has to stay listed and has to be reversible, or ignoring one becomes a trapdoor.
#
# `by_hand` marks a pile somebody built rather than one the arithmetic produced, by merging two
# groups, or by splitting some faces out of one. It exists because re-grouping deletes every open
# pile and rebuilds them from scratch, so without it a merge would be silently undone by the next
# grouping pass. Regrouping leaves these alone and keeps their faces out of the clustering, exactly
# as it does for a pile somebody set aside.
_CREATE_PILES = """
CREATE TABLE IF NOT EXISTS face_piles (
  id         TEXT PRIMARY KEY,
  status     TEXT NOT NULL CHECK(status IN ('open','ignored')),
  centroid   BLOB NOT NULL,
  size       INTEGER NOT NULL,
  by_hand    INTEGER NOT NULL DEFAULT 0,
  recognizer TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
)
"""


# One appearance of one face. `started_ms` and `ended_ms` are equal for a still.
#
# `person_id` is where an attribution is held while this feature is the only thing that knows
# about it. `attribution` says how it got there, because the three cases are not interchangeable:
# `matched` was decided by arithmetic above the auto-apply confidence, `suggested` is waiting for
# somebody to agree, and `confirmed` is somebody having agreed, which is also what makes the
# face eligible to become a reference.
_CREATE_TRACKS = """
CREATE TABLE IF NOT EXISTS face_tracks (
  id           TEXT PRIMARY KEY,
  asset_id     TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  started_ms   INTEGER NOT NULL,
  ended_ms     INTEGER NOT NULL,
  seen_in      INTEGER NOT NULL,
  quality      REAL NOT NULL,
  person_id    TEXT REFERENCES people(id) ON DELETE SET NULL,
  confidence   REAL,
  attribution  TEXT CHECK(attribution IN ('matched','suggested','confirmed')),
  pile_id      TEXT REFERENCES face_piles(id) ON DELETE SET NULL,
  attributed_at INTEGER,
  created_at   INTEGER NOT NULL,
  asked_by     TEXT CHECK(asked_by IN ('match','group','undone','box'))
)
"""

# v32, beside the word above: which face each old appearance was found again as.
#
# A rescan deletes every appearance a file had and finds its faces again under new ids, and every
# receipt in History names the faces it decided by those ids, so without this an Undo after a
# rescan would reach nothing, leaving the face named and its picture filed for the next re-match to
# learn from. The five memories beside this one put a decision back on the face found again
# (`FaceService._name_again` and its siblings); this says
# which face it is, so anything that names the old one (an Undo, a scan deciding whether a name is
# new) reaches the new one. Paired by description within the file, by the rule the memories use
# (`FaceService._already_decided`).
#
# One row per face found again, pointed at the newest id whenever there is a newer one
# (`Store.write_successors`), so the answer is one read however many rescans there have been.
# Cascades from the file; a face not found again keeps a row naming an id that no longer exists,
# which is the honest answer to an Undo of it.
_CREATE_SUCCESSORS = """
CREATE TABLE IF NOT EXISTS face_successors (
  track_id     TEXT PRIMARY KEY,
  successor_id TEXT NOT NULL,
  asset_id     TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  created_at   INTEGER NOT NULL
)
"""

_INDEX_SUCCESSORS_SUCCESSOR = (
    "CREATE INDEX IF NOT EXISTS ix_face_successors_successor ON face_successors(successor_id)"
)
# v33. Somebody every one of whose stash-box pictures Sift already refused as a starter.
#
# "Use stash-box pictures as starters for N people" counts everybody linked to a box with no
# reference row, so without this a person whose every box picture was a group photo, turned away or
# unreadable would stay in N for ever, and each Run would fetch the same pictures and refuse them
# again (`faces.starter.refused` is only a log line).
#
# One row per person, under the model that judged them: a different recognizer is a different
# judgement, so a row from another model does not count and the person is offered again. A person
# named by a press of their own (a link made by hand queues a run for exactly them) is looked at
# again whatever this says, and the row goes the moment anything of theirs is filed.
#
# Somebody no box holds a single picture of is a row too, with `pictures` 0: every box answered and
# there was nothing to refuse, and asking again would get the same nothing.
_CREATE_STARTER_REFUSALS = """
CREATE TABLE IF NOT EXISTS face_starter_refusals (
  person_id   TEXT PRIMARY KEY REFERENCES people(id) ON DELETE CASCADE,
  recognizer  TEXT NOT NULL,
  pictures    INTEGER NOT NULL,
  refused_at  INTEGER NOT NULL
)
"""

_INDEX_SUCCESSORS_ASSET = (
    "CREATE INDEX IF NOT EXISTS ix_face_successors_asset ON face_successors(asset_id)"
)


# The numbers and the picture behind one face, at one moment. Several per track (the best few
# frames of it), never all of them.
#
# The embedding is stored so that adding a person later is arithmetic over numbers already held
# rather than a re-decode of the library. The crop is stored for the same reason one step further
# out: changing the recognizer then costs re-embedding stored images instead of decoding the media
# again.
#
# `quality` is the one number the bar reduces to, and the three beside it are what it was reduced
# from: how much face there is, how sharp it is, how square-on it is. They are measured for every
# face anyway, and keeping them costs three numbers per face. What they are for is
# the question the combined score cannot answer: when somebody says a crop is no use, whether the
# bar let through something it should have refused, and on which of the three. Without them the
# only way to move the bar is to guess and rescan the library.
_CREATE_DETECTIONS = """
CREATE TABLE IF NOT EXISTS face_detections (
  id           TEXT PRIMARY KEY,
  track_id     TEXT NOT NULL REFERENCES face_tracks(id) ON DELETE CASCADE,
  timestamp_ms INTEGER NOT NULL,
  box_x        INTEGER NOT NULL,
  box_y        INTEGER NOT NULL,
  box_w        INTEGER NOT NULL,
  box_h        INTEGER NOT NULL,
  score        REAL NOT NULL,
  quality      REAL NOT NULL,
  pixels       INTEGER,
  sharpness    REAL,
  frontality   REAL,
  containment  REAL,
  strength     REAL,
  agreement    REAL,
  crop_path    TEXT NOT NULL,
  crop_digest  TEXT NOT NULL,
  embedding    BLOB NOT NULL,
  created_at   INTEGER NOT NULL
)
"""


# Faces somebody has said are no use: a hand, a logo, a pattern in a curtain, or a real face too
# blurred to be worth anything. Removed rather than hidden: there is no screen these come back on.
#
# The row outlives the face it removed, and that is the entire reason it exists. A face lives in
# `face_detections`, which a rescan of its file replaces wholesale, so removing one without
# remembering means the next scan finds it again and asks the same question. What is kept is the
# description and the file, so a face found again in the same file can be recognized as the one
# already answered.
#
# The three measurements come with it because they are the only evidence about where the quality bar
# should sit. A crop somebody calls useless got THROUGH the bar, so each of these is a case the bar
# was wrong about, and whether they cluster just above it or scatter across the whole range is the
# difference between "raise it" and "the bar is not the problem".
_CREATE_REMOVED = """
CREATE TABLE IF NOT EXISTS face_removals (
  id           TEXT PRIMARY KEY,
  asset_id     TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  embedding    BLOB NOT NULL,
  recognizer   TEXT NOT NULL,
  quality      REAL NOT NULL,
  pixels       INTEGER,
  sharpness    REAL,
  frontality   REAL,
  created_at   INTEGER NOT NULL
)
"""

_INDEX_REMOVED_ASSET = (
    "CREATE INDEX IF NOT EXISTS ix_face_removals_asset ON face_removals(asset_id)"
)

# v11. Faces somebody set aside, remembered the same way removals are and for the same reason.
#
# **A rescan deletes every track a file has**, so a face set aside recorded only as the pile it sat
# in would come back among the open piles while the pile survived as a row claiming faces it no
# longer held. This keeps the face's own description somewhere a rescan does not reach, the way a
# removal is kept.
#
# `pile_id` is remembered rather than derived, so faces set aside together come back together
# instead of collapsing into one heap of everything ever ignored in that file.
_CREATE_IGNORED = """
CREATE TABLE IF NOT EXISTS face_ignored (
  id           TEXT PRIMARY KEY,
  asset_id     TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  pile_id      TEXT NOT NULL,
  embedding    BLOB NOT NULL,
  centroid     BLOB NOT NULL,
  recognizer   TEXT,
  created_at   INTEGER NOT NULL
)
"""

_INDEX_IGNORED_ASSET = "CREATE INDEX IF NOT EXISTS ix_face_ignored_asset ON face_ignored(asset_id)"

# v11, and the half that is easy to leave out. Building the table protects decisions made from then
# on; a pile set aside earlier has nothing written down for it, so the first rescan after the
# upgrade would undo it. The piles already set aside are therefore written down as part of the step.
#
# One row per appearance, carrying its best face's description: the same face every other part of
# the feature treats as that appearance, so what is remembered is what matching would compare.
_CREATE_CONFIRMATIONS = """
CREATE TABLE IF NOT EXISTS face_confirmations (
  id           TEXT PRIMARY KEY,
  asset_id     TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  person_id    TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  embedding    BLOB NOT NULL,
  recognizer   TEXT,
  created_at   INTEGER NOT NULL
)
"""

_INDEX_CONFIRMED_ASSET = (
    "CREATE INDEX IF NOT EXISTS ix_face_confirmations_asset ON face_confirmations(asset_id)"
)

# v22, and the other end of the same table. A rescan asks "what was already agreed about THIS
# FILE", which the index above answers; a person's history asks "what was agreed about THIS
# PERSON", counted by the day it happened, and nothing could answer that without walking every
# confirmation in the library.
#
# `created_at` and `asset_id` are in the index as well as `person_id`, which is what makes the read
# never touch the table at all: the statement selects the day, the count, the newest moment and the
# files (each checked against what the viewer may see), and the whole answer is in the index. A
# one-column index would seek to the right rows and then fetch each of them from the table, which
# is the work this is meant to remove. The file column is why it is three columns and not two:
# a two-column index stops covering the moment the statement reads the file.
_INDEX_CONFIRMED_PERSON = (
    "CREATE INDEX IF NOT EXISTS ix_face_confirmations_person"
    " ON face_confirmations(person_id, created_at, asset_id)"
)


# v13, and the same mechanism a fourth time, for the grouping itself rather than for a decision
# about who somebody is.
#
# Merging two groups, or splitting some faces out of one, is a person saying "these belong
# together". It has two ways to be undone and needed guarding against both. Re-grouping deletes
# every open pile and rebuilds them, which `by_hand` on the pile handles. A rescan is the harder
# one: it deletes every track the file had, so the pile is left claiming faces that no longer
# exist and the new ones come back among whatever the clustering makes of them.
#
# So the membership is written down as descriptions, against the file each face was found in, the
# way setting aside is. `pile_id` is remembered rather than derived for the same reason it is
# there: faces put together by hand have to come back together, and not as one heap of everything
# ever moved in that file.
_CREATE_GROUPING = """
CREATE TABLE IF NOT EXISTS face_grouping (
  id           TEXT PRIMARY KEY,
  asset_id     TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  pile_id      TEXT NOT NULL,
  embedding    BLOB NOT NULL,
  centroid     BLOB NOT NULL,
  recognizer   TEXT,
  created_at   INTEGER NOT NULL
)
"""

_INDEX_GROUPING_ASSET = (
    "CREATE INDEX IF NOT EXISTS ix_face_grouping_asset ON face_grouping(asset_id)"
)


# An installed pack, found by the library that made it, or by its name where the file names none.
# `name` then holds a key, since the UNIQUE cannot be dropped from a table others reference.
_CREATE_PACKS = """
CREATE TABLE IF NOT EXISTS face_packs (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL UNIQUE COLLATE NOCASE,
  version      TEXT NOT NULL,
  recognizer   TEXT NOT NULL,
  dimension    INTEGER NOT NULL,
  digest       TEXT NOT NULL,
  installed_at INTEGER NOT NULL,
  library      TEXT
)
"""

_INDEX_PACKS_LIBRARY = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_face_packs_library ON face_packs(library)"
    " WHERE library IS NOT NULL"
)

# This library's own id for the files it makes: random, so a move keeps it and a file names no path.
_CREATE_OWN_LIBRARY = """
CREATE TABLE IF NOT EXISTS face_own_library (
  one INTEGER PRIMARY KEY CHECK (one = 1),
  id  TEXT NOT NULL
)
"""


_CREATE_REFERENCES = """
CREATE TABLE IF NOT EXISTS face_references (
  id          TEXT PRIMARY KEY,
  person_id   TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  crop_path   TEXT,
  crop_digest TEXT NOT NULL,
  embedding   BLOB NOT NULL,
  quality     REAL NOT NULL,
  origin      TEXT NOT NULL CHECK(origin IN ('added','pack','confirmed','seed','recognized')),
  pack_id     TEXT REFERENCES face_packs(id) ON DELETE CASCADE,
  recognizer  TEXT NOT NULL,
  pixels      INTEGER,
  track_id    TEXT,
  asset_id    TEXT,
  created_at  INTEGER NOT NULL,
  source      TEXT,
  retired_at  INTEGER,
  UNIQUE(person_id, crop_digest)
)
"""

# "This face is not that person." Written when somebody rejects a suggestion, read by every
# subsequent match, and never expired: a suggestion that returns next week after being turned
# down is how a review queue gets abandoned.
#
# Keyed by the appearance, which a rescan of its file deletes, so on its own this table forgets
# every "no" the next time the file is looked at. What carries it across is `face_rejected` below
# (v27), the same memory the other four decisions keep; this table stays the one every match reads.
_CREATE_REJECTIONS = """
CREATE TABLE IF NOT EXISTS face_rejections (
  track_id   TEXT NOT NULL REFERENCES face_tracks(id) ON DELETE CASCADE,
  person_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(track_id, person_id)
)
"""

# v22, and the refusals to the confirmations' agreements. The primary key begins with the track, so
# it answers "which people has this face been refused as" and cannot be sought by a person at all,
# and a person's history asks exactly the other question. Covering for the same reason as the
# one above: the read wants the moment and the face's file (through the track) off the row.
_INDEX_REJECTED_PERSON = (
    "CREATE INDEX IF NOT EXISTS ix_face_rejections_person"
    " ON face_rejections(person_id, created_at, track_id)"
)

# v27, and the fifth decision of the shape `face_confirmations` has: "this face is not that
# person", written against the face's own description and the file it was found in, so the next
# scan of that file can say which of the faces it has just found was the one refused.
#
# `face_rejections` is keyed by the appearance and cascades from it, so without this every "no"
# said about a file would be wiped the next time the file was looked at, and the person would come
# straight back as a question about it. Carrying a no onto a face found again is the same question
# the scan answers for naming, setting aside, grouping and removing.
#
# A second table rather than new columns on the first, because that is how the four already
# divide it: the live decision sits where every match reads it (an attribution, a pile's status,
# this table), and the memory sits beside it keyed by the file. `created_at` is the moment the
# refusal was made and it is carried back onto the restored row, so a person's history does not
# show an old "no" as said again on the day the file was rescanned.
#
# Nullable `recognizer`, for the reason v23 gives: a null is "nothing knows", which every read
# treats as incomparable rather than as agreement.
_CREATE_REJECTED = """
CREATE TABLE IF NOT EXISTS face_rejected (
  id           TEXT PRIMARY KEY,
  asset_id     TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  person_id    TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  embedding    BLOB NOT NULL,
  recognizer   TEXT,
  created_at   INTEGER NOT NULL
)
"""

_INDEX_REJECTED_ASSET = (
    "CREATE INDEX IF NOT EXISTS ix_face_rejected_asset ON face_rejected(asset_id)"
)


# Which names on a file this feature put there.
#
# Attributing a face to somebody also attaches that person to the file, in the shared table every
# other part of Sift reads: that is the whole point of recognizing a face, and a name only this
# feature could see would be no use to the search box or the person's own page.
#
# The shared table records the pair and nothing else: there is no column saying who decided it, and
# there must not be one, because it is a table this feature does not own. So the provenance is kept
# here instead. Without it, withdrawing an attribution has no safe move: taking the pair out would
# remove a name somebody dragged on by hand, and leaving it would let a rejected match sit on the
# file for ever. With it, this side removes only what it wrote.
#
# A row is the claim, not the attribution. Several faces in one file can point at one person, so the
# pair survives until the last of them stops naming them, which is why this is keyed by the pair
# rather than by the track.
#
# `ON DELETE CASCADE` from `assets` and from `people` because a claim about a row that has gone is
# not a claim about anything, and a stale one would make the next attribution think it had already
# written what it had not.
_CREATE_CLAIMS = """
CREATE TABLE IF NOT EXISTS face_asset_people (
  asset_id   TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  person_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(asset_id, person_id)
)
"""

# Which model files are on this machine. No weights ship with Sift, so this is the record of what
# the operator fetched or pointed at, and the digest is what proves the file has not changed under
# it between runs.
_CREATE_WEIGHTS = """
CREATE TABLE IF NOT EXISTS face_weights (
  id           TEXT PRIMARY KEY,
  revision     TEXT NOT NULL,
  digest       TEXT NOT NULL,
  size_bytes   INTEGER NOT NULL,
  installed_at INTEGER NOT NULL
)
"""

_ATTRIBUTED_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_face_tracks_attributed"
    " ON face_tracks(attributed_at DESC, id DESC) WHERE person_id IS NOT NULL"
)

# A person's appearances across the library, newest decision first: their own page, the first
# faces of each card on the People Sift can recognize wall, and the re-match that runs when their references
# change. Partial, because most tracks in a library nobody has curated yet have no person. It
# replaced `ix_face_tracks_person` (the person alone) in v25: every read that one served, this one
# serves, and it also serves the order, so a card's first face is a short range rather than a sort
# of every face the person has.
_PERSON_RECENT_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_face_tracks_person_recent"
    " ON face_tracks(person_id, attributed_at, id) WHERE person_id IS NOT NULL"
)

# The faces a task found on a day: Insights adds each finished day up by `created_at`. Without it
# every day added up would read every track once.
_TRACKS_BY_DAY_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_face_tracks_created ON face_tracks(created_at)"
)

# The groups by status: the review list reads the ones still open.
_PILES_BY_STATUS_INDEX = "CREATE INDEX IF NOT EXISTS ix_face_piles_status ON face_piles(status)"

_INDEXES = (
    _ATTRIBUTED_INDEX,
    _TRACKS_BY_DAY_INDEX,
    # "Who is in this file" and "what has this file already had done to it".
    "CREATE INDEX IF NOT EXISTS ix_face_tracks_asset ON face_tracks(asset_id)",
    # A person's appearances across the library, and the re-match that runs when their references
    # change. Partial, because most tracks in a library nobody has curated yet have no person.
    _PERSON_RECENT_INDEX,
    # Listing a pile's faces. Partial for the same reason: a track that has been identified has
    # left its pile.
    "CREATE INDEX IF NOT EXISTS ix_face_tracks_pile ON face_tracks(pile_id) "
    "WHERE pile_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_face_detections_track ON face_detections(track_id)",
    "CREATE INDEX IF NOT EXISTS ix_face_references_person ON face_references(person_id)",
    _INDEX_REFERENCE_TRACK,
    _INDEX_REFERENCE_ASSET,
    # Removing a pack: everything it brought, by pack.
    "CREATE INDEX IF NOT EXISTS ix_face_references_pack ON face_references(pack_id) "
    "WHERE pack_id IS NOT NULL",
    _PILES_BY_STATUS_INDEX,
)

# Everything this feature claimed about one person, which is what a re-match has to reconsider when
# their references change. The other direction is the primary key and needs no index of its own.
_INDEX_CLAIMS_PERSON = (
    "CREATE INDEX IF NOT EXISTS ix_face_asset_people_person ON face_asset_people(person_id)"
)

# v28. "This group may be that person", for a reason this feature did not work out itself.
#
# The reason today is a FOLDER: the folder reader files a folder under somebody whose name it
# already is, and one unnamed group makes up most of the folder's files with a face: the rule the
# Folders card uses to say a group is a folder. The reader may not import this feature,
# so it hands the conclusion across the seam and it is kept here, where the review list reads it.
#
# **Stored, not worked out on each read**, because the answer needs the folder tree and the
# folder's filing, which live in another slice, and the pass that already computes it runs whenever
# a folder's faces move. Worked out on read it would be a cross-slice read of every filed folder per
# page of the review list.
#
# **An answer is kept rather than deleted.** `refused` is what stops the pass making the same
# proposal again the next time the folder moves; `accepted` is the same for a yes. Only a
# `pending` row is ever rewritten by the pass.
#
# `files` and `of_files` are what the pass measured when it proposed: the group's files in the
# folder, out of the folder's files with a face. They are the pass's evidence and are never shown
# as they stand: they count files some viewers may not be told about, so a screen counts again as
# the reader may see it.
#
# `ON DELETE CASCADE` from all three parents, because a proposal about a group that was rebuilt, a
# person who was deleted or a folder that went is not a proposal about anything.
_CREATE_PILE_PROPOSALS = """
CREATE TABLE IF NOT EXISTS face_pile_proposals (
  pile_id    TEXT NOT NULL REFERENCES face_piles(id) ON DELETE CASCADE,
  person_id  TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  reason     TEXT NOT NULL CHECK(reason IN ('folder')),
  folder_id  TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  files      INTEGER NOT NULL,
  of_files   INTEGER NOT NULL,
  state      TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','refused','accepted')),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(pile_id, person_id)
)
"""

# The key begins with the pile, which is how the review list asks ("what is proposed for these
# groups"). The pass asks the other way round ("what did this folder propose for this person")
# to take back a proposal the folder no longer makes.
_INDEX_PILE_PROPOSALS_FOLDER = (
    "CREATE INDEX IF NOT EXISTS ix_face_pile_proposals_folder "
    "ON face_pile_proposals(folder_id, person_id)"
)


# What a pack brought that this library could not place, held without being a Person, so somebody
# is made when RECOGNIZED here rather than on import and re-importing loses nothing.
#
# `claimed_person_id` is who the entry turned into, once it turns into anybody. The row is kept
# rather than deleted, so a second import of the same pack recognizes what it already gave, and
# the key is ON DELETE SET NULL, so deleting that person puts the entry back in the queue, which is
# the honest answer rather than a dangling claim.
_CREATE_PACK_ENTRIES = """
CREATE TABLE IF NOT EXISTS pack_entries (
  id                TEXT PRIMARY KEY,
  pack_id           TEXT NOT NULL REFERENCES face_packs(id) ON DELETE CASCADE,
  name              TEXT NOT NULL,
  aliases           TEXT NOT NULL DEFAULT '[]',
  links             TEXT NOT NULL DEFAULT '[]',
  claimed_person_id TEXT REFERENCES people(id) ON DELETE SET NULL,
  created_at        INTEGER NOT NULL,
  declined_at       INTEGER,
  source            TEXT,
  confirmed         INTEGER,
  UNIQUE(pack_id, name COLLATE NOCASE)
)
"""

# The faces under an unclaimed entry, in the shape a reference is stored in, because that is what
# each becomes the moment somebody claims it. The claim is a copy, not a conversion.
#
# Keyed on the picture's identity within the entry, so re-importing the same pack writes nothing.
_CREATE_PACK_ENTRY_FACES = """
CREATE TABLE IF NOT EXISTS pack_entry_faces (
  id          TEXT PRIMARY KEY,
  entry_id    TEXT NOT NULL REFERENCES pack_entries(id) ON DELETE CASCADE,
  crop_path   TEXT,
  crop_digest TEXT NOT NULL,
  embedding   BLOB NOT NULL,
  quality     REAL NOT NULL,
  recognizer  TEXT NOT NULL,
  UNIQUE(entry_id, crop_digest)
)
"""

# What the last folder import left out or kept as a near copy, keyed by file so a rerun adds nothing.
_CREATE_FOLDER_LEFT_OUT = """
CREATE TABLE IF NOT EXISTS face_folder_left_out (
  job_id TEXT NOT NULL,
  file   TEXT NOT NULL,
  reason TEXT NOT NULL,
  PRIMARY KEY (job_id, file)
)
"""

_PACK_ENTRY_INDEXES = (
    # "Who has this pack not placed yet": the list an import reports and a new person is matched
    # against. Partial, because once a library is settled most entries are claimed.
    "CREATE INDEX IF NOT EXISTS ix_pack_entries_unclaimed ON pack_entries(name COLLATE NOCASE)"
    " WHERE claimed_person_id IS NULL",
    "CREATE INDEX IF NOT EXISTS ix_pack_entry_faces_entry ON pack_entry_faces(entry_id)",
)

# Every table, piles before tracks because a track names one; the rest are independent.
_TABLES = (
    _CREATE_SCANS,
    _CREATE_PILES,
    _CREATE_TRACKS,
    _CREATE_DETECTIONS,
    _CREATE_PACKS,
    _CREATE_REFERENCES,
    _CREATE_REJECTIONS,
    _CREATE_WEIGHTS,
    _CREATE_CLAIMS,
    _CREATE_PACK_ENTRIES,
    _CREATE_PACK_ENTRY_FACES,
    _CREATE_REMOVED,
    _CREATE_IGNORED,
    _CREATE_CONFIRMATIONS,
    _CREATE_GROUPING,
    _CREATE_RUNS,
    _CREATE_REJECTED,
    _CREATE_PILE_PROPOSALS,
    _CREATE_SUCCESSORS,
    _CREATE_STARTER_REFUSALS,
    _CREATE_FOLDER_LEFT_OUT,
    _CREATE_OWN_LIBRARY,
)

# The indexes kept beside the tables they serve.
_MORE_INDEXES = (
    _INDEX_PACKS_LIBRARY,
    _INDEX_CLAIMS_PERSON,
    *_PACK_ENTRY_INDEXES,
    _INDEX_REMOVED_ASSET,
    _INDEX_IGNORED_ASSET,
    _INDEX_CONFIRMED_ASSET,
    _INDEX_CONFIRMED_PERSON,
    _INDEX_GROUPING_ASSET,
    _INDEX_REJECTED_PERSON,
    _INDEX_REJECTED_ASSET,
    _INDEX_PILE_PROPOSALS_FOLDER,
    _INDEX_SUCCESSORS_SUCCESSOR,
    _INDEX_SUCCESSORS_ASSET,
)

#: Version 36's step: the words a question's asker may be, before and after a stash-box joined
#: them. The fragment both a library made at the baseline and one that gained the column later
#: carry exactly once.
_ASKED_BY_WAS = "asked_by IN ('match','group','undone')"
_ASKED_BY_NOW = "asked_by IN ('match','group','undone','box')"

#: Version 37's step: a reference may be a face Sift recognized (`Origin.RECOGNIZED`).
_ORIGIN_WAS = "origin IN ('added','pack','confirmed','seed')"
_ORIGIN_NOW = "origin IN ('added','pack','confirmed','seed','recognized')"

#: Version 38's step: an entry whose person an Undo took back, which the pass over facial
#: fingerprints leaves held from then on (`pack_entries.declined_at`).
_ENTRY_DECLINED = "ALTER TABLE pack_entries ADD COLUMN declined_at INTEGER"

#: Version 39's step: the folder an entry was read from where it is not its pack's own name, and
#: how many confirmed faces the file said the person had.
_ENTRY_SOURCE_AND_COUNT = {
    "source": "ALTER TABLE pack_entries ADD COLUMN source TEXT",
    "confirmed": "ALTER TABLE pack_entries ADD COLUMN confirmed INTEGER",
}

#: Version 41's step: the library a pack came from.
_PACKS_LIBRARY = "ALTER TABLE face_packs ADD COLUMN library TEXT"

#: Version 34's step. See `initialize`.
_INDEXES_A_LIBRARY_MAY_LACK = (_TRACKS_BY_DAY_INDEX, _PILES_BY_STATUS_INDEX)

#: Version 35's step: the refusal split by reason, and the size of the biggest face too small.
_REFUSAL_REASONS = {
    "refused_largest": "ALTER TABLE face_scans ADD COLUMN refused_largest INTEGER",
    "refused_blurred": "ALTER TABLE face_scans ADD COLUMN refused_blurred INTEGER",
    "refused_turned": "ALTER TABLE face_scans ADD COLUMN refused_turned INTEGER",
    "refused_edge": "ALTER TABLE face_scans ADD COLUMN refused_edge INTEGER",
}


# `assets` and `people` are named in foreign keys without a declared dependency on the components
# that create them. SQLite resolves a foreign key's parent by name when a row is written rather
# than when the table is made, so the reference holds whichever order the two were created in.
async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        for statement in (*_TABLES, *_INDEXES, *_MORE_INDEXES):
            await connection.execute(statement)
    # Version 34: two of `_INDEXES` that a library brought up through the steps before the baseline
    # never received. `IF NOT EXISTS`, so a library that has them is left alone.
    if 0 < on_disk < 34:
        for statement in _INDEXES_A_LIBRARY_MAY_LACK:
            await connection.execute(statement)
    # Version 35: the columns a scan's refusals are told apart by. NULL on every older row, which
    # the History line reads as "not known" and words as it always did.
    if 0 < on_disk < 35:
        await _add_columns(connection, "face_scans", _REFUSAL_REASONS)
    # Version 36: a stash-box may ask about a face (`AskedBy.BOX`). Only the CHECK widens, in the
    # stored definition, so no row moves and nothing that points at a face is touched.
    if 0 < on_disk < 36 and not await check_allows(connection, "face_tracks", "box"):
        await widen_a_check(connection, "face_tracks", was=_ASKED_BY_WAS, now=_ASKED_BY_NOW)
    # Version 37: a face Sift recognized may become a reference (`Origin.RECOGNIZED`). The CHECK
    # widens in the stored definition alone, as version 36's did.
    if 0 < on_disk < 37 and not await check_allows(connection, "face_references", "recognized"):
        await widen_a_check(connection, "face_references", was=_ORIGIN_WAS, now=_ORIGIN_NOW)
    # Version 38: an entry may be declined. NULL on every older row: nothing was declined before.
    if 0 < on_disk < 38:
        await _add_columns(connection, "pack_entries", {"declined_at": _ENTRY_DECLINED})
    # Version 39: an entry's own folder and its confirmed count. NULL on every older row, which
    # reads as the pack's name and as a count nobody gave.
    if 0 < on_disk < 39:
        await _add_columns(connection, "pack_entries", _ENTRY_SOURCE_AND_COUNT)
    # Version 40: the folder import's left-out pictures, by file. Empty until the next import.
    if 0 < on_disk < 40:
        await connection.execute(_CREATE_FOLDER_LEFT_OUT)
    # Version 41: a pack's library, NULL on an older one, which stays keyed by its name.
    if 0 < on_disk < 41:
        await _add_columns(connection, "face_packs", {"library": _PACKS_LIBRARY})
        await connection.execute(_INDEX_PACKS_LIBRARY)
        await connection.execute(_CREATE_OWN_LIBRARY)


async def _add_columns(connection: Connection, table: str, columns: dict[str, str]) -> None:
    """Add each column the table lacks, so a step taken twice is taken once."""
    for column, statement in columns.items():
        if not await column_exists(connection, table, column):
            await connection.execute(statement)


register_schema_initializer(COMPONENT, VERSION, initialize, baseline=33)


# The faces still waiting for a name, counted per user and group by the kernel's visibility
# component alongside the tags and people. Every face, not every file: a group's number on the
# wall is how many of its faces the user may see. Declared here because this slice owns the
# table; the kernel then brings its triggers up after this component's tables exist.
register_counted(
    Counted(
        WAITING_FACES_KIND,
        "(SELECT asset_id, pile_id FROM face_tracks WHERE person_id IS NULL AND pile_id IS NOT NULL)",
        "pile_id",
        "face_tracks",
        "UPDATE OF asset_id, person_id, pile_id",
        keys=(("id",),),
    ),
    component=COMPONENT,
)


#: The kind under which a person's attributed faces are counted per user, one object per
#: (person, how the face came to carry the name). What the People Sift can recognize wall orders and counts
#: by: read as a range of `viewer_entity_counts`, it is the three figures a card draws without a
#: single face being assembled.
FACE_BAND_KIND = "face_band"

#: Between the person and the band in the object id. Neither half can hold it: a person id is
#: minted from an alphabet without it and a band is one of the `Attribution` words, or empty for a
#: face given a person with no word for how: still the person's face, counted in the card's size.
FACE_BAND_SEPARATOR = ":"

#: A face with a person on it, as (file, person and band). The separator is written into the text
#: rather than joined in, so the statement is a constant; the check below holds the two together.
_FACE_BAND_SOURCE = (
    "(SELECT asset_id, person_id || ':' || COALESCE(attribution, '') AS band"
    " FROM face_tracks WHERE person_id IS NOT NULL)"
)
if f"|| '{FACE_BAND_SEPARATOR}' ||" not in _FACE_BAND_SOURCE:  # pragma: no cover (an edit)
    raise RuntimeError("the face band source and its separator disagree")

# Kept by the same recompute as every other count, so the vault holds it back exactly as it holds
# back the files: `permitted` over every face on a file this user may see, `concealed` over the
# ones the vault is keeping. Over the same table as the waiting groups, and so one set of triggers
# for both (`visibility._watching`): every write that changes a face's person or its attribution
# (the automatic attach, a confirm or a refusal one at a time or in bulk, naming a group, the
# forget-everything wipe, a rescan dropping a file's faces) moves this without any of them
# having to know it exists. `attribution` is watched as well as `person_id` although every writer
# today sets the two together, so a writer that changes one alone is still counted.
register_counted(
    Counted(
        FACE_BAND_KIND,
        _FACE_BAND_SOURCE,
        "band",
        "face_tracks",
        "UPDATE OF asset_id, person_id, attribution",
        keys=(("id",),),
    ),
    component=COMPONENT,
)
