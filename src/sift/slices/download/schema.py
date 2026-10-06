# SPDX-License-Identifier: AGPL-3.0-or-later
"""The download ledger, site logins and what one site does differently.

`downloads` is the ledger. Its `url_hash` is what makes a re-dropped link cheap: a normalised URL
that has already been fetched is recognized and skipped rather than fetched again. Its `error` is
plain-language and already redacted by the time it is written: it is read on a screen and copied
into a diagnostics export, so it may carry no URL and no path.

`download_items` is the same ledger asked one level down, and it exists for the links whose contents
change. A story tray or a highlight keeps one address while what is behind it grows, so "have I
fetched this address" stops being the same question as "have I fetched what is behind it", and
answering the first loses everything added since. One row per piece of media actually fetched, so
such a link is always looked at again and only the new items are downloaded.

`site_connections` ties a Site to the secret that logs in to it. The secret itself is in the
kernel's `secrets`; this table holds only the link and a health status, never a cookie.

`tunnels` and the routes sites take through them are the kernel component `tunnels`
(`kernel/tunnels/schema.py`), because a swap is hosted on a tunnel as well as downloads going out
of one.

No comment is written inside these `CREATE TABLE` statements: SQLite stores a comment as part of the
table's text and keeps it there after the column it described is dropped, so the notes are above
each statement instead.
"""

from __future__ import annotations

import json

from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.log import get_logger
from sift.slices.download.sources.registry import site_key

log = get_logger(__name__)

DOWNLOAD_COMPONENT = "download"
DOWNLOAD_VERSION = 38


# `state` is a small closed set, and 'blocked' is deliberately not in it: waiting for cookies is a
# state of the job that runs the download, not of the ledger row, so the queue reads it from the
# job. `paused` IS in it: a pause is somebody's instruction about this row, and it has to survive
# the job being pruned and the process restarting.
#
# `dest_folder_id` is where a drop target said the file should land; a folder deleted later sets it
# null rather than taking the ledger row with it. `folder_id` is the folder the job RESOLVED (the one
# chosen, else its Site's own, else the one for everything), with no key on purpose: a key would
# set it NULL when the folder goes, and NULL is what a row that never recorded one carries, so a
# removed folder would be re-answered with a folder the file never went to.
#
# `site` and `username` are what the download was filed under; `username_from` is HOW the username
# was learned: from the link itself (`address`), from the site's answer while the link was resolved
# (`resolver`), or read off the page (`page`). The three are not equally sure, and the branch that
# decides it runs once, inside the job. See `service.UsernameFrom`, whose list the CHECK spells and
# a test holds equal.
#
# `error_code` is short and stable (`http-403`, `disk-full`), so a failure can be recognised without
# matching prose; `error_tier` says how much the sentence is worth. `via` is the way out the
# download actually took, as a person would read it, and `via_address` the tunnel's server address
# as it stood then: a record of what happened, not of what is true now.
#
# `finished_at` and `filename` keep a finished row readable after its file is deleted. `aimed_kind`,
# `aimed_id` and `aimed_by` say what a dropped link was aimed at; the filer dispatches on
# `aimed_kind` by name (`composition.file_under`). `job_id` is the job that runs this row, written
# when it is queued, and clears when the queue prunes the job.
#
# `hidden_at` is Remove from the list: the ledger row STAYS, because it is what stops a re-pasted
# link being fetched twice. `seen_at` is when somebody looked at how a download ended; every write
# that ENDS a row clears it, which is what lights the rail's dot.
#
# `remember` is the choice the Downloads page makes FOR ONE PASTE, on the row rather than in the
# job's payload so a retry keeps it. NULL means "not chosen for this download": the job reads the
# setting as it stands when it runs.
_CREATE_DOWNLOADS = """
CREATE TABLE IF NOT EXISTS downloads (
  id             TEXT PRIMARY KEY,
  url            TEXT NOT NULL,
  url_hash       TEXT NOT NULL,
  state          TEXT NOT NULL DEFAULT 'queued'
                 CHECK(state IN ('queued','running','paused','done','duplicate','failed','skipped',
                                 'canceled','quarantined')),
  dest_folder_id TEXT REFERENCES folders(id) ON DELETE SET NULL,
  site           TEXT,
  username       TEXT,
  asset_id       TEXT REFERENCES assets(id) ON DELETE SET NULL,
  error          TEXT,
  created_at     INTEGER NOT NULL,
  error_code     TEXT,
  error_tier     INTEGER,
  via            TEXT,
  finished_at    INTEGER,
  filename       TEXT,
  aimed_kind     TEXT,
  aimed_id       TEXT,
  aimed_by       TEXT,
  job_id         TEXT REFERENCES jobs(id) ON DELETE SET NULL,
  hidden_at      INTEGER,
  via_address    TEXT,
  folder_id      TEXT,
  username_from  TEXT CHECK(username_from IN ('address','resolver','page')),
  remember       INTEGER CHECK(remember IN (0,1)),
  seen_at        INTEGER
)
"""

# `expires_at` and `expires_last` are what was understood of the jar when it was saved: derived
# facts, so the cookies screen can say when they run out without unsealing anything. BOTH, not one:
# the soonest expiry in a jar is nearly always a half-hour clearance cookie the site reissues on the
# next request, and `expires_last` is the point past which nothing in the jar is any use, which is
# the date somebody can act on. See `CookieSummary`.
#
# `last_used_at` is when Sift last unsealed these cookies to use them, written at the unseal because
# unsealing IS the use.
_CREATE_SITE_CONNECTIONS = """
CREATE TABLE IF NOT EXISTS site_connections (
  id           TEXT PRIMARY KEY,
  site_id      TEXT REFERENCES sites(id) ON DELETE CASCADE,
  secret_id    TEXT REFERENCES secrets(id) ON DELETE SET NULL,
  status       TEXT,
  updated_at   INTEGER,
  expires_at   INTEGER,
  expires_last INTEGER,
  last_used_at INTEGER
)
"""

# One row per piece of media actually fetched from an address, for the addresses whose contents
# change. The ledger above answers "has this link been fetched", which is the right question for a
# post and the wrong one for a story tray or a highlight: the link stays the same while what is
# behind it does, so answering it at the link level loses everything added since.
#
# `media_key` is the item's own stable name: the media's path at the site, not the signed address
# it arrived under, which is different every few minutes. Together with the link's hash it is the
# primary key, so recording the same item twice is not possible rather than merely unlikely.
#
# `asset_id` is what makes a deleted file fetchable again, exactly as it does in the ledger: the
# database clears it when the file goes, and a row that points at nothing is not a record of holding
# anything. A row is written only once its file has landed and been let in. Writing one at resolve
# time would mark a download that then failed as fetched, and nobody would ever see it.
_CREATE_DOWNLOAD_ITEMS = """
CREATE TABLE IF NOT EXISTS download_items (
  url_hash   TEXT NOT NULL,
  media_key  TEXT NOT NULL,
  asset_id   TEXT REFERENCES assets(id) ON DELETE SET NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (url_hash, media_key)
)
"""

# What one site does differently from the rest, apart from its route. One row per site that has been
# given something of its own, plus the reserved scope for the answer everything else follows, the
# same shape as the routes table (`tunnel_routes`) and for the same reason: it is one question asked at two
# levels, and two tables would be two read paths that could disagree.
#
# A separate table from the routes rather than more columns on it, because a route is required and
# these are not: a site with a naming rule and no opinion about its way out would otherwise have to
# be given a route it never asked for, and an explicitly-chosen Direct is not the same thing as
# following the default.
#
# `dest_folder_id` is a folder that already exists. Sift does not build a tree of its own from a
# template: creating folders in somebody's library is the library's business, and the rule this
# whole feature sits under is that a person organises by dropping and dragging.
_CREATE_SITE_OPTIONS = """
CREATE TABLE IF NOT EXISTS site_options (
  scope          TEXT PRIMARY KEY,
  naming         TEXT,
  dest_folder_id TEXT REFERENCES folders(id) ON DELETE SET NULL,
  -- Which tool fetches from this Site, overriding what the catalog says. NULL is "whatever
  -- Sift would do", which is the answer nearly every row keeps. Not a foreign key and not a CHECK:
  -- the set of tools is a fact about the code, the route validates against it, and a CHECK would
  -- turn adding a third downloader into a table rebuild.
  downloader     TEXT,
  updated_at     INTEGER NOT NULL
)
"""

# The picture a site is shown with, fetched from the site once and kept. NOT a cover: a cover is a
# file in the library that somebody chose, and it lives on the platform or the username. Keeping the
# two in different places is what makes "a scraped picture never replaces a chosen one" true by
# construction rather than by a check somebody has to remember to write.
#
# `path` points into the cache directory, so clearing the cache loses pictures and nothing else,
# and a row whose file has gone reads as no picture, which makes the next download fetch it again.
#
# `path` is RELATIVE to the cache directory, like `derivatives.rel_cache_path`, so moving the cache
# folder breaks nothing.
_CREATE_SITE_ART = """
CREATE TABLE IF NOT EXISTS site_art (
  scope     TEXT PRIMARY KEY,
  path      TEXT NOT NULL,
  stored_at INTEGER NOT NULL
)
"""

_INDEXES = (
    # The ledger check on every submitted URL: has this hash already been fetched?
    "CREATE INDEX IF NOT EXISTS ix_downloads_urlhash ON downloads(url_hash)",
    # What a pruned job's key walks: its rows are looked up here rather than scanned.
    "CREATE INDEX IF NOT EXISTS ix_downloads_job ON downloads(job_id)",
    # Where a file came from, asked the other way round, so a file's History can say it. Partial,
    # on the rows that name a file: a queued or failed download has no file to be asked about.
    "CREATE INDEX IF NOT EXISTS ix_downloads_asset ON downloads(asset_id) WHERE asset_id IS NOT NULL",
    # One connection per Site is the common read.
    "CREATE INDEX IF NOT EXISTS ix_site_connections_site ON site_connections(site_id)",
)


# Version 31: what a finished download's last file was named FROM, beside the name it got: the
# post's ID, its title, when it was posted (ISO, a date or a moment) and the name it arrived with.
# The naming preview reads them to show a real name from a Site. Nothing to fill for the rows
# already there: none of the four was kept, and a row without them keeps its invented example.
_NAMED_FROM = (
    "ALTER TABLE downloads ADD COLUMN post_id TEXT",
    "ALTER TABLE downloads ADD COLUMN title TEXT",
    "ALTER TABLE downloads ADD COLUMN posted TEXT",
    "ALTER TABLE downloads ADD COLUMN original TEXT",
)


# Version 32: how many files a link offered and how many of them were left out for good, so a row
# for an album can say "218 of 219 files, 1 left out" rather than only the log saying it. NULL on
# the rows already there, which never recorded either: a row without them says nothing of the kind.
_LEFT_OUT = (
    "ALTER TABLE downloads ADD COLUMN files_offered INTEGER",
    "ALTER TABLE downloads ADD COLUMN files_left_out INTEGER",
)


# Version 33: WHO asked for the download, the user whose paste, drop or capture wrote the row, so a
# file's History can say "You downloaded this file from ..." instead of naming Sift for an act a
# person started. A deleted user's rows keep their downloads and lose the name (the key sets it
# NULL), which reads as Sift's again: the ledger's own rule for a user who is gone. NULL on the
# rows already there, which never recorded one, and on a download nobody pressed for.
_REQUESTED_BY = (
    "ALTER TABLE downloads ADD COLUMN requested_by TEXT REFERENCES users(id) ON DELETE SET NULL",
)


# Version 34: a download names its Site and its person by ID, and the library remembers which Site
# each site Sift downloads from files under.
#
# `site` and `username` are the words the address gave at the time, and they stay: they are what
# the row was, and what a search of the list matches. But joined BY NAME they were a link that a
# rename broke: rename the Site and every row from it stopped leading there, and the next download
# from it, looking the Site up by the catalog's name for it, made a SECOND Site under the old name
# and could not find the cookies saved on the first. So the row keeps the ids it was filed under,
# and `download_sites` keeps, per site (`registry.site_key`), the Site this library files it
# under. A Site deleted takes its key with it, and the next download makes one again, as the first
# ever download from a site does.
_BY_ID = (
    "ALTER TABLE downloads ADD COLUMN site_id TEXT REFERENCES sites(id) ON DELETE SET NULL",
    "ALTER TABLE downloads ADD COLUMN person_id TEXT REFERENCES people(id) ON DELETE SET NULL",
    """
CREATE TABLE IF NOT EXISTS download_sites (
  key       TEXT PRIMARY KEY,
  site_id   TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  filed_at  INTEGER NOT NULL
)
""",
    "CREATE INDEX IF NOT EXISTS ix_download_sites_site ON download_sites(site_id)",
)

# The one-time fill, from the names each row kept: the Site of that name (a Site's name is unique
# without case), and the person the username on it belongs to, else the ONE person of that name.
# A name two people share is left empty rather than given to either: the row said a name, not who.
_FILL_SITES = (
    "UPDATE downloads SET site_id = (SELECT s.id FROM sites s WHERE s.name = downloads.site)"
    " WHERE site IS NOT NULL AND site_id IS NULL"
)
_FILL_PEOPLE = """
UPDATE downloads SET person_id = COALESCE(
  (SELECT u.person_id FROM usernames u
    WHERE u.site_id = downloads.site_id AND u.name = downloads.username COLLATE NOCASE
      AND u.person_id IS NOT NULL
    ORDER BY u.id LIMIT 1),
  (SELECT MIN(p.id) FROM people p WHERE p.name = downloads.username COLLATE NOCASE
   HAVING COUNT(*) = 1))
 WHERE username IS NOT NULL AND person_id IS NULL
"""
#: A Site renamed BEFORE this step gave its old name up, and rows from it name nothing by that
#: name any more. The record of renames says which Site carried it. That record belongs to a slice
#: this component must not require, so the step asks whether its table is there.
_HAS_RECORD = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workbench_decisions'"
_SITE_RENAMES = (
    "SELECT s.subject_id AS id, d.payload AS payload"
    "  FROM workbench_decisions d"
    "  JOIN workbench_decision_subjects s ON s.decision_id = d.id"
    "  JOIN sites alive ON alive.id = s.subject_id"
    " WHERE d.verb = 'renamed' AND s.kind = 'site'"
    " ORDER BY d.decided_at, d.id"
)
_UNFILED_NAMES = "SELECT DISTINCT site FROM downloads WHERE site IS NOT NULL AND site_id IS NULL"
_FILL_RENAMED = "UPDATE downloads SET site_id = ? WHERE site = ? COLLATE NOCASE AND site_id IS NULL"
_FILED = "SELECT site_id, url FROM downloads WHERE site_id IS NOT NULL ORDER BY id DESC"
_KEEP_KEY = (
    "INSERT INTO download_sites (key, site_id, filed_at) VALUES (?, ?, 0)"
    " ON CONFLICT(key) DO NOTHING"
)
_COUNT = "SELECT COUNT(site_id) AS sites, COUNT(person_id) AS people FROM downloads"
#: The catalog's three tables the fill reads. Always there in a library (this component depends on
#: the catalog); asked anyway, so a database built for one step's test is brought forward too.
_HAS_CATALOG = (
    "SELECT COUNT(*) AS held FROM sqlite_master"
    " WHERE type = 'table' AND name IN ('sites', 'people', 'usernames')"
)


async def _by_id(connection: Connection) -> None:
    for statement in _BY_ID:
        await connection.execute(statement)
    await _fill_by_id(connection)


async def _fill_by_id(connection: Connection) -> None:
    """Fill the ids from the names once, and the Site each site files under from the rows."""
    if int(next(iter(await connection.execute_fetchall(_HAS_CATALOG)))["held"]) < 3:
        return
    await connection.execute(_FILL_SITES)
    if list(await connection.execute_fetchall(_HAS_RECORD)):
        renamed: dict[str, set[str]] = {}
        for row in await connection.execute_fetchall(_SITE_RENAMES):
            try:
                before = json.loads(str(row["payload"] or "{}")).get("before")
            except (ValueError, AttributeError):
                continue
            if isinstance(before, str) and before.strip():
                renamed.setdefault(before.strip().casefold(), set()).add(str(row["id"]))
        for row in await connection.execute_fetchall(_UNFILED_NAMES):
            ids = renamed.get(str(row["site"]).strip().casefold(), set())
            if len(ids) == 1:
                await connection.execute(_FILL_RENAMED, (next(iter(ids)), row["site"]))
    await connection.execute(_FILL_PEOPLE)
    # Newest first, and the first answer for a key stands: where one site's downloads were filed
    # under two Sites over time, the Site it was filed under last is the one it files under next.
    keys = 0
    seen: set[str] = set()
    for row in await connection.execute_fetchall(_FILED):
        key = site_key(str(row["url"]))
        if key is None or key in seen:
            continue
        seen.add(key)
        await connection.execute(_KEEP_KEY, (key, row["site_id"]))
        keys += 1
    counted = next(iter(await connection.execute_fetchall(_COUNT)))
    log.info(
        "download.filed_by_id",
        sites=int(counted["sites"]),
        people=int(counted["people"]),
        keys=keys,
    )


# Version 35: a creator's picture keeps the id of the username it belongs to. See `art.py`, where
# every read by name goes through it. Filled once from each scope (`<site key>:<username>`): that
# name on the Site the key files under (version 34's `download_sites`).
_ART_BY_ID = (
    "ALTER TABLE site_art ADD COLUMN username_id TEXT REFERENCES usernames(id) ON DELETE SET NULL",
    "CREATE INDEX IF NOT EXISTS ix_site_art_username ON site_art(username_id)",
)
_FILL_ART = """
UPDATE site_art SET username_id = (
  SELECT u.id FROM usernames u JOIN download_sites k ON k.site_id = u.site_id
   WHERE k.key = substr(site_art.scope, 1, instr(site_art.scope, ':') - 1)
     AND u.name = substr(site_art.scope, instr(site_art.scope, ':') + 1) COLLATE NOCASE
   ORDER BY u.id LIMIT 1)
 WHERE username_id IS NULL AND instr(scope, ':') > 0
"""
_COUNT_ART = "SELECT COUNT(username_id) AS linked, COUNT(*) AS kept FROM site_art"


_HAS_ART = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'site_art'"


async def _art_by_id(connection: Connection) -> None:
    # A library always has the table (version 1 made it); a database built for one older step's
    # test may not, and has no pictures to link.
    if not list(await connection.execute_fetchall(_HAS_ART)):
        return
    for statement in _ART_BY_ID:
        await connection.execute(statement)
    await _fill_art(connection)


async def _fill_art(connection: Connection) -> None:
    if int(next(iter(await connection.execute_fetchall(_HAS_CATALOG)))["held"]) < 3:
        return
    await connection.execute(_FILL_ART)
    counted = next(iter(await connection.execute_fetchall(_COUNT_ART)))
    log.info("download.art_by_id", linked=int(counted["linked"]), kept=int(counted["kept"]))


# Version 36: a row whose file was deleted, when the same link was fetched again and the same file
# landed, leads to that file. Kept on from here by `service.mark_done`; this is the once for the
# rows from before. The same address AND the same name, so a link whose contents change never
# hands an old row a file it never fetched.
_RELINK = """
UPDATE downloads SET asset_id = (
  SELECT e.asset_id FROM downloads e
   WHERE e.url_hash = downloads.url_hash AND e.id <> downloads.id
     AND e.filename = downloads.filename AND e.asset_id IS NOT NULL
   ORDER BY e.id DESC LIMIT 1)
 WHERE state IN ('done', 'duplicate') AND asset_id IS NULL AND filename IS NOT NULL
   AND EXISTS (
     SELECT 1 FROM downloads e
      WHERE e.url_hash = downloads.url_hash AND e.id <> downloads.id
        AND e.filename = downloads.filename AND e.asset_id IS NOT NULL)
RETURNING id
"""


_HAS_ASSETS = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'assets'"


async def _relink(connection: Connection) -> None:
    # A library always has the files' table (this component depends on it); a database built for
    # one step's test may not, and has no rows to give a file. The write names the key's table, so
    # SQLite refuses it outright where that table is missing.
    if not list(await connection.execute_fetchall(_HAS_ASSETS)):
        return
    relinked = list(await connection.execute_fetchall(_RELINK))
    log.info("download.relinked_to_the_same_link", rows=len(relinked))


# Version 37: a downloaded gallery lands as files in its folder and nothing groups it, so the
# paste's own answer to grouping one has nothing left to decide. The column goes.
_DROPS_PHOTO_SETS = "SELECT 1 FROM pragma_table_info('downloads') WHERE name = 'photo_sets'"
_DROP_PHOTO_SETS = "ALTER TABLE downloads DROP COLUMN photo_sets"


async def _drop_photo_sets(connection: Connection) -> None:
    if list(await connection.execute_fetchall(_DROPS_PHOTO_SETS)):
        await connection.execute(_DROP_PHOTO_SETS)


# Version 38: the reads after a landed file that its tunnel refused (`attempt.Reach.said`).
_HAS_READS_REFUSED = "SELECT 1 FROM pragma_table_info('downloads') WHERE name = 'reads_refused'"
_READS_REFUSED = "ALTER TABLE downloads ADD COLUMN reads_refused TEXT"


async def _add_reads_refused(connection: Connection) -> None:
    if not list(await connection.execute_fetchall(_HAS_READS_REFUSED)):
        await connection.execute(_READS_REFUSED)


async def initialize_download(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        for statement in (
            _CREATE_DOWNLOADS,
            _CREATE_SITE_CONNECTIONS,
            _CREATE_DOWNLOAD_ITEMS,
            _CREATE_SITE_OPTIONS,
            _CREATE_SITE_ART,
            *_INDEXES,
        ):
            await connection.execute(statement)
    for version, statements in ((31, _NAMED_FROM), (32, _LEFT_OUT), (33, _REQUESTED_BY)):
        if on_disk < version:
            for statement in statements:
                await connection.execute(statement)
    if on_disk < 34:
        await _by_id(connection)
    if on_disk < 35:
        await _art_by_id(connection)
    if on_disk < 36:
        await _relink(connection)
    if on_disk < 37:
        await _drop_photo_sets(connection)
    if on_disk < 38:
        await _add_reads_refused(connection)


# `folders` and `assets` come from the kernel's library and content components, `sites` and
# `secrets` from the catalog and identity, `jobs` from the queue: declaring them means those tables
# are built before these keys name them.
register_schema_initializer(
    DOWNLOAD_COMPONENT,
    DOWNLOAD_VERSION,
    initialize_download,
    depends_on=["content", "library", "catalog", "jobs"],
    baseline=30,
)
