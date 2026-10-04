# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `dedup_candidates` table: pairs a person still has to look at.

**Near duplicates only.** An exact duplicate never appears here, and the reason is not a rule this
table enforces: it is that an exact duplicate cannot exist as two rows in the first place.
Identical bytes are one asset with several locations, settled at import by the content model, so
there is no pair to write down. They are a space problem, answered by the reclaim query, and
putting them in front of a person to be judged would be asking a question that has an answer.

`status` is the whole point of the table rather than a field on it. A pair arrives `pending`; it
leaves `confirmed` or `dismissed`; and it is never deleted. **`dismissed` is the load-bearing
one.** "These two are different, stop asking" has to survive the next scan, and the only thing that
can make it survive is a row that outlives the comparison which produced it: the next scan will
compute exactly the same distance and want to file exactly the same pair. A queue that re-asks a
question somebody has already answered gets abandoned, and an abandoned queue is worse than none
because it hides the pairs that do matter behind the ones that never did.

`UNIQUE(asset_a, asset_b, method)` is what makes a re-scan idempotent, and it only holds if the
pair is always written in the same order: the service sorts the two ids before it writes, so a
pair discovered as (b, a) collides with the row stored as (a, b) instead of becoming its twin.

`method` is in the key rather than beside it because each kind of asset is judged by different
evidence: a photograph by one frame, a GIF by thirty, a video by a single number covering the whole
video. The same two files can legitimately be near on one and not on another, and those are two
different findings about the same pair.

It is also why the distance means nothing without it. Bits out of 63, frames out of 30 and bits out
of 64 are three scales, and a screen that printed the figure without the method would be printing a
number whose units the reader cannot know.

The cascade is deliberate: delete an asset and the questions about it go with it, because a pair
is only a question while both halves of it still exist.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer

COMPONENT = "dedup"
VERSION = 4

_CREATE_CANDIDATES = """
CREATE TABLE IF NOT EXISTS dedup_candidates (
  id              TEXT PRIMARY KEY,
  asset_a         TEXT REFERENCES assets(id) ON DELETE CASCADE,
  asset_b         TEXT REFERENCES assets(id) ON DELETE CASCADE,
  method          TEXT CHECK(method IN ('phash','videohash','video_phash')),
  distance        INTEGER,
  -- How far apart the two run. NULL is the matcher's word for "nobody can say", and the queue
  -- shows such a pair rather than hiding it when the length control is touched.
  duration_gap_ms INTEGER,
  status          TEXT NOT NULL DEFAULT 'pending'
                  CHECK(status IN ('pending','confirmed','dismissed')),
  created_at      INTEGER NOT NULL,
  UNIQUE(asset_a, asset_b, method)
)
"""

_INDEXES = (
    # The queue's only question: what is still waiting to be looked at, closest pairs first. The
    # status is in the index because a library that has been reviewed once is mostly rows that are
    # settled, and reading past all of them to find the few that are not is the case that gets
    # slower the longer somebody uses Sift.
    "CREATE INDEX IF NOT EXISTS ix_dedup_status ON dedup_candidates(status, distance)",
    # A scan asks "have I already filed this pair" once per candidate it finds. The unique
    # constraint answers that from its own index for the exact key, but a resolve arriving by
    # asset (everything still open about this file) reads this one.
    "CREATE INDEX IF NOT EXISTS ix_dedup_asset_a ON dedup_candidates(asset_a)",
    "CREATE INDEX IF NOT EXISTS ix_dedup_asset_b ON dedup_candidates(asset_b)",
)


async def initialize(connection: Connection, on_disk: int) -> None:
    if on_disk < 1:
        await connection.execute(_CREATE_CANDIDATES)
        for index in _INDEXES:
            await connection.execute(index)


# It names `assets` in a foreign key without declaring a dependency on the component that creates
# that table: SQLite resolves a foreign key's parent by name when a row
# is written rather than when the table is made, so the reference holds whichever order the two
# were created in.
register_schema_initializer(COMPONENT, VERSION, initialize, baseline=4)
