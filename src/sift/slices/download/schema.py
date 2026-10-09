# SPDX-License-Identifier: AGPL-3.0-or-later
"""The download ledger, Site logins and what one Site does differently.

No comment goes inside a `CREATE TABLE`: SQLite keeps it after its column is dropped.
"""

from __future__ import annotations

import json

from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.log import get_logger
from sift.slices.download.sources.registry import site_key

log = get_logger(__name__)

DOWNLOAD_COMPONENT = "download"
DOWNLOAD_VERSION = 38


# `state` has no 'blocked': waiting for cookies is the job's state; `paused` survives a restart.
# `folder_id` is the folder the job resolved, with no key, so a removed folder is not re-answered.
# `username_from` is how the username was learned (`service.UsernameFrom`, held equal by a test).
# `error` is plain and redacted: no URL, no path. `via` and `via_address` record the way out taken.
# `hidden_at` is Remove from the list, the row kept for the ledger; an ending clears `seen_at`.
# `remember` is one paste's choice, on the row so a retry keeps it; NULL follows the setting.
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

# `expires_at` and `expires_last` are what was read of the jar at saving; `expires_last` is the
# date somebody can act on. `last_used_at` is written at the unseal, which is the use. No cookie.
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

# One row per item fetched from a link whose contents change (a story tray), so only new items
# are fetched. `media_key` is the item's path, not its signed address; a row is written once its
# file has landed, and `asset_id` clears with the file so it can be fetched again.
_CREATE_DOWNLOAD_ITEMS = """
CREATE TABLE IF NOT EXISTS download_items (
  url_hash   TEXT NOT NULL,
  media_key  TEXT NOT NULL,
  asset_id   TEXT REFERENCES assets(id) ON DELETE SET NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (url_hash, media_key)
)
"""

# What one Site does differently, apart from its route, plus the reserved scope for everything,
# shaped like `tunnel_routes`. `dest_folder_id` names an existing folder: Sift builds no tree.
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

# The picture a Site is shown with, fetched once: not a cover, so a chosen cover is never
# replaced. `path` is relative to the cache directory, and a missing file reads as no picture.
_CREATE_SITE_ART = """
CREATE TABLE IF NOT EXISTS site_art (
  scope     TEXT PRIMARY KEY,
  path      TEXT NOT NULL,
  stored_at INTEGER NOT NULL
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_downloads_urlhash ON downloads(url_hash)",
    "CREATE INDEX IF NOT EXISTS ix_downloads_job ON downloads(job_id)",
    # Where a file came from, for its History; only rows that name a file.
    "CREATE INDEX IF NOT EXISTS ix_downloads_asset ON downloads(asset_id) WHERE asset_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_site_connections_site ON site_connections(site_id)",
)


# Version 31: what the last file was named from, for the naming preview's real example.
_NAMED_FROM = (
    "ALTER TABLE downloads ADD COLUMN post_id TEXT",
    "ALTER TABLE downloads ADD COLUMN title TEXT",
    "ALTER TABLE downloads ADD COLUMN posted TEXT",
    "ALTER TABLE downloads ADD COLUMN original TEXT",
)


# Version 32: how many files a link offered and how many were left out for good.
_LEFT_OUT = (
    "ALTER TABLE downloads ADD COLUMN files_offered INTEGER",
    "ALTER TABLE downloads ADD COLUMN files_left_out INTEGER",
)


# Version 33: who asked for the download, for the file's History; NULL once the user is gone.
_REQUESTED_BY = (
    "ALTER TABLE downloads ADD COLUMN requested_by TEXT REFERENCES users(id) ON DELETE SET NULL",
)


# Version 34: a download names its Site and person by id, since a link by name broke on a rename;
# `download_sites` keeps the Site each site files under.
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

# Filled once by name; a name two people share is left empty.
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
#: Renames before this step, from a slice this component must not require.
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
#: Asked anyway, so a database built for one step's test is brought forward too.
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
    # Newest first: the Site a site was filed under last is the one it files under next.
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


# Version 35: a creator's picture keeps the id of its username (see `art.py`).
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
    # An older step's test database may have no such table.
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


# Version 36: a row whose file was deleted leads to the same file fetched again from the same link.
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
    # An older step's test database may have no such table.
    if not list(await connection.execute_fetchall(_HAS_ASSETS)):
        return
    relinked = list(await connection.execute_fetchall(_RELINK))
    log.info("download.relinked_to_the_same_link", rows=len(relinked))


# Version 37: nothing groups a gallery now, so the column goes.
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
    await _later_steps(connection, on_disk)


async def _later_steps(connection: Connection, on_disk: int) -> None:
    """The steps from version 34 on, each a function of its own."""
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


# The tables these keys name are built first.
register_schema_initializer(
    DOWNLOAD_COMPONENT,
    DOWNLOAD_VERSION,
    initialize_download,
    depends_on=["content", "library", "catalog", "jobs"],
    baseline=30,
)
