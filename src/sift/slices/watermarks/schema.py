# SPDX-License-Identifier: AGPL-3.0-or-later
"""What this feature records about the library: which files it has looked at, and what it saw.

Three tables, and the first is the one that makes the pass affordable.

**Which files have been looked at is recorded, and it is not the same question as which files a
mark was found on.** Much of a library carries no watermark at all, and looking at one of
those costs exactly what looking at a marked one costs. A pass that asked "which files are not
filed under a site" (the question the file-name reader asks, correctly, because reading a name is
free) would read every unmarked file again on every pass, for ever. So the absence of a row here
is the only thing that means "never looked", and that is what the sweep selects on.

**The file's own identity travels with the row.** A mark is a property of the copy, not of the
work: the same clip re-encoded by somebody else carries a different mark or none. So a file whose
content has changed has not been read, however recently the path was looked at. The revision
travels too, for the reason the search index records one: a file read by a model that is no
longer the configured one is stale rather than done, and changing the models sweeps the library
again without anybody having to ask it to.

**What was read is kept even when it decided something.** The obvious design records only the
reads that matched nothing, since those are what a person is shown. But a filing made from a
watermark is a decision, and the string it was made from is the evidence for it: a file filed
under OnlyFans with no record of what was read is a decision nobody can check. One row per file,
the best mark on it: a frame carries one mark, and a table that could hold several would be
inventing a case to answer it.

**And what KIND of thing was read is stored beside it**, because not every reading is a site's
address: a Telegram channel's address names no site at all, a bare username does not say which site
it belongs to, and a distributor's band names none in its letters though it is filed under the
site its distributor re-hosts. The kind is what the file's History phrases its line from and what
says which of the other columns apply.

**And a refusal is a standing no.** Undoing a filing has to outlive the pass that made it, or the
next sweep puts back precisely what somebody has just removed, with nothing on any screen saying
why. The file-name reader follows the same rule.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer
from sift.kernel.log import get_logger

log = get_logger(__name__)

COMPONENT = "watermarks"
VERSION = 7

_CREATE_SCANS = """
CREATE TABLE IF NOT EXISTS watermark_scans (
  asset_id   TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  revision   TEXT NOT NULL,
  identity   TEXT NOT NULL,
  found      INTEGER NOT NULL,
  scanned_at INTEGER NOT NULL
)
"""

#: `kind` is what the row is a reading OF, and the column the others are read through. `site` is
#: that kind's payload and is NOT nullable: a band's row carries the site that band is filed under,
#: and a channel or a bare username leaves it empty, the way `usernames.name = ''` is the row that
#: means "from this site, poster unknown".
#:
#: `revision` is what read the letters, the model's own name for itself (`weights.REVISION`), so a
#: reading keeps it when its scan row is written again. `matcher_version` is what decided what they
#: meant (`signatures.MATCHER_VERSION`), so a better matcher re-decides only the rows below its
#: number from their stored text, without opening a file. A NULL in either satisfies no comparison,
#: so such a row is treated as needing reading again rather than as current.
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
    # The sweep's only question about what is already done: which files were read by something
    # other than the models configured now. On a library that is fully read and unchanged this
    # returns nothing, and it has to do so without reading every row.
    "CREATE INDEX IF NOT EXISTS ix_watermark_revision ON watermark_scans(revision)",
)


#: Version 6: a reading keeps the id of the Site it names, beside the word it read. `site` is the
#: word and stays (it is what was read); a join on that word would break when the Site is renamed.
#: Set when the reading is written (`Store.name_site_on`) and filled once here from the names, where
#: a Site of that name exists. Asked whether `sites` is there first: it is the catalog's table, and
#: this component declares no dependency on it.
_SITE_ID = (
    "ALTER TABLE watermark_reads ADD COLUMN site_id TEXT REFERENCES sites(id) ON DELETE SET NULL"
)
_HAS_SITES = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sites'"
_FILL_SITE_ID = (
    "UPDATE watermark_reads SET site_id = (SELECT s.id FROM sites s WHERE s.name = watermark_reads.site)"
    " WHERE site <> '' AND site_id IS NULL"
)
_COUNT_FILLED = "SELECT COUNT(site_id) AS filled, COUNT(*) AS readings FROM watermark_reads"

#: Version 7: a filing's receipt names what the file was filed under, the ledger's object, which is
#: what a file's History folds the receipt and the filing's own line on. Receipts written before
#: the object was recorded get it here, from their own payload: the username, or the Site's
#: "poster unknown" row where the read named nobody. Without it those files say their filing twice.
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


async def _fill_site_ids(connection: Connection) -> None:
    if list(await connection.execute_fetchall(_HAS_SITES)):
        await connection.execute(_FILL_SITE_ID)
    counted = next(iter(await connection.execute_fetchall(_COUNT_FILLED)))
    log.info(
        "watermarks.site_by_id",
        filled=int(counted["filled"]),
        readings=int(counted["readings"]),
    )


# It names `assets` in a foreign key without declaring a dependency on the component that creates
# that table: SQLite resolves a foreign key's parent by name when a row is written rather than when
# the table is made, so the reference holds whichever order the two were created in.
register_schema_initializer(COMPONENT, VERSION, initialize, baseline=5)
