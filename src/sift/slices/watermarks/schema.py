# SPDX-License-Identifier: AGPL-3.0-or-later
"""The watermark tables: which files were looked at (marked or not), the reading kept, and
refusals."""

from __future__ import annotations

from sift.kernel.content.backlog import marks
from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.log import get_logger

log = get_logger(__name__)

COMPONENT = "watermarks"
VERSION = 8

_CREATE_SCANS = """
CREATE TABLE IF NOT EXISTS watermark_scans (
  asset_id   TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  revision   TEXT NOT NULL,
  identity   TEXT NOT NULL,
  found      INTEGER NOT NULL,
  scanned_at INTEGER NOT NULL
)
"""

#: `site` is never NULL: empty for a kind that names no site; NULL versions mean read again.
_CREATE_READS = """
CREATE TABLE IF NOT EXISTS watermark_reads (
  asset_id        TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  text            TEXT NOT NULL,
  kind            TEXT NOT NULL DEFAULT 'site',
  site            TEXT NOT NULL,
  username        TEXT,
  confidence      REAL NOT NULL,
  frame_ms        INTEGER,
  revision        TEXT,
  matcher_version INTEGER,
  read_at         INTEGER NOT NULL
)
"""

_CREATE_REFUSALS = """
CREATE TABLE IF NOT EXISTS watermark_refusals (
  asset_id   TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL
)
"""

_INDEXES = (
    # Must return nothing on a fully read library without reading every row.
    "CREATE INDEX IF NOT EXISTS ix_watermark_revision ON watermark_scans(revision)",
)


#: Version 6: the Site's id beside the word read, which a rename would break.
_SITE_ID = (
    "ALTER TABLE watermark_reads ADD COLUMN site_id TEXT REFERENCES sites(id) ON DELETE SET NULL"
)
_HAS_SITES = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sites'"
_FILL_SITE_ID = (
    "UPDATE watermark_reads SET site_id = (SELECT s.id FROM sites s WHERE s.name = watermark_reads.site)"
    " WHERE site <> '' AND site_id IS NULL"
)
_COUNT_FILLED = "SELECT COUNT(site_id) AS filled, COUNT(*) AS readings FROM watermark_reads"

#: Version 7: old filing receipts get their object from their own payload.
_OBJECT_ON_OLD_RECEIPTS = """
UPDATE workbench_decisions AS d
   SET object_kind = 'username', object_id = o.under, object_name = o.named
  FROM (SELECT r.id AS id,
               COALESCE(json_extract(r.payload, '$.username_id'), u.id) AS under,
               CASE WHEN json_extract(r.payload, '$.username_id') IS NULL
                    THEN json_extract(r.payload, '$.site') END AS named
          FROM workbench_decisions r
          LEFT JOIN sites s ON s.name = json_extract(r.payload, '$.site')
          LEFT JOIN usernames u ON u.site_id = s.id AND u.name = ''
         WHERE r.queue = 'watermarks' AND r.verb = 'filed' AND r.object_kind IS NULL
           AND json_valid(r.payload)) AS o
 WHERE d.id = o.id AND o.under IS NOT NULL
RETURNING id
"""


_MARKS = (
    *marks("watermark_scans"),
    *marks("watermark_reads", watched=("asset_id", "matcher_version")),
    *marks("watermark_refusals"),
)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_SCANS)
        await connection.execute(_CREATE_READS)
        await connection.execute(_CREATE_REFUSALS)
        for index in _INDEXES:
            await connection.execute(index)
    if on_disk < 6:
        await connection.execute(_SITE_ID)
        await _fill_site_ids(connection)
    if 0 < on_disk < 7 and list(await connection.execute_fetchall(_HAS_SITES)):
        given = list(await connection.execute_fetchall(_OBJECT_ON_OLD_RECEIPTS))
        log.info("watermarks.receipts_given_their_object", receipts=len(given))
    if on_disk < 8:
        for statement in _MARKS:
            await connection.execute(statement)


async def _fill_site_ids(connection: Connection) -> None:
    if list(await connection.execute_fetchall(_HAS_SITES)):
        await connection.execute(_FILL_SITE_ID)
    counted = next(iter(await connection.execute_fetchall(_COUNT_FILLED)))
    log.info(
        "watermarks.site_by_id",
        filled=int(counted["filled"]),
        readings=int(counted["readings"]),
    )


register_schema_initializer(COMPONENT, VERSION, initialize, depends_on=["content"], baseline=5)
