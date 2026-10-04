# SPDX-License-Identifier: AGPL-3.0-or-later
"""What one pass filed from filenames: the card's count, the page of usernames, the Undo offered."""

from __future__ import annotations

from collections.abc import Sequence

from sift.kernel.access.viewer import Viewer
from sift.kernel.access.visibility import FILE_SEEN_BY_VIEWER, seen_by
from sift.kernel.db import Database, in_clause, point_read
from sift.kernel.serving import art_version
from sift.kernel.sql_splice import splice

#: How many files one pass filed, each counted once, over only the files the viewer may be shown:
#: counting the vault's would tell a shut vault's size (`visibility.FILE_SEEN_BY_VIEWER`).
_FILED_FROM = splice(
    "SELECT COUNT(DISTINCT link.asset_id) AS total FROM asset_usernames link"
    " WHERE link.source IN (:first, :second) AND {{SEEN}}",
    SEEN=FILE_SEEN_BY_VIEWER,
)


async def count_files_filed_from(db: Database, viewer: Viewer, sources: tuple[str, str]) -> int:
    """How many files this viewer may be shown carry a filing a pass wrote with these words.

    Two words, bound into a fixed pair of placeholders: a filing read off a name carries one or the
    other, they are one page and one Undo, and a fixed pair keeps the statement one constant
    string. Scoped through the stored verdict, so a shut vault is not counted.
    """
    row = await db.fetch_one(_FILED_FROM, _filed_by(viewer, sources))
    return 0 if row is None else int(row["total"])


#: How many usernames one pass filed files under: the page's total, counted over the groups.
_FILING_USERNAMES = splice(
    """
SELECT COUNT(*) AS total
  FROM (SELECT 1 FROM asset_usernames link
         WHERE link.source IN (:first, :second) AND {{SEEN}}
         GROUP BY link.username_id)
""",
    SEEN=FILE_SEEN_BY_VIEWER,
)

#: One page of the usernames a pass filed under, biggest first (where a misreading cost most), the
#: id breaking ties so pages are stable. A LEFT JOIN to `sites`, because a site can be deleted
#: while its username still holds files. Counted over the files the viewer may be shown.
_FILING_GROUPS = splice(
    """
SELECT link.username_id AS username_id, a.name AS name, p.name AS site,
       a.person_id AS person_id, COUNT(DISTINCT link.asset_id) AS files
  FROM asset_usernames link
  JOIN usernames a ON a.id = link.username_id
  LEFT JOIN sites p ON p.id = a.site_id
 WHERE link.source IN (:first, :second) AND {{SEEN}}
 GROUP BY link.username_id, a.name, p.name, a.person_id
 ORDER BY files DESC, link.username_id
 LIMIT :limit OFFSET :offset
""",
    SEEN=FILE_SEEN_BY_VIEWER,
)

#: Where one username sits on that list, counting from zero, or no row when it is not on this
#: viewer's list: the same groups in the same order (`kernel.paging.resume_at`).
_FILING_POSITION = splice(
    """
WITH filed AS (
  SELECT link.username_id AS username_id, COUNT(DISTINCT link.asset_id) AS files
    FROM asset_usernames link
    JOIN usernames a ON a.id = link.username_id
   WHERE link.source IN (:first, :second) AND {{SEEN}}
   GROUP BY link.username_id
)
SELECT (SELECT COUNT(*) FROM filed o
         WHERE o.files > t.files
            OR (o.files = t.files AND o.username_id < t.username_id)) AS at
  FROM filed t
 WHERE t.username_id = :username
""",
    SEEN=FILE_SEEN_BY_VIEWER,
)

#: The files one username holds from one pass, capped, read in `ix_asset_usernames_source` order so
#: the LIMIT stops reading. Grouped by the file, because a file at two paths is two location rows
#: and a doubled id breaks the panel; `MIN` picks a name stably. The join stays INNER: a file with
#: no location has no name to show. `art_marks` feeds the picture token, as the grid's does.
_FILES_FILED_UNDER = point_read(
    "catalog.files_filed_under",
    """
SELECT f.asset_id AS asset_id, MIN(l.filename) AS filename,
       (SELECT group_concat(ordered.mark, '|') FROM (
          SELECT d.kind || ':' || COALESCE(d.content_hash, '') AS mark
            FROM derivatives d WHERE d.asset_id = f.asset_id ORDER BY d.kind, d.params
        ) ordered) AS art_marks
  FROM asset_usernames f
  JOIN asset_locations l ON l.asset_id = f.asset_id
 WHERE f.username_id = ? AND f.source IN (?, ?)
 GROUP BY f.asset_id
 ORDER BY f.asset_id
 LIMIT ?
""",
)

#: The picture token for a page of files, asked once for the page: the fold `_FILES_FILED_UNDER`
#: does inline, so screens drawing stills off rows that are not the grid's share one reader. A
#: cache key, never a capability: whether a picture is handed over is decided where it is served.
_ART_OF_FILES = """
SELECT a.id AS asset_id,
       (SELECT group_concat(ordered.mark, '|') FROM (
          SELECT d.kind || ':' || COALESCE(d.content_hash, '') AS mark
            FROM derivatives d WHERE d.asset_id = a.id ORDER BY d.kind, d.params
        ) ordered) AS art_marks
  FROM assets a
 WHERE a.id IN (?*)
"""


async def art_of_files(
    db: Database, asset_ids: Sequence[str], *, stamp: int
) -> dict[str, str | None]:
    """The picture token for each of these files, keyed by id. See `_ART_OF_FILES`.

    A file with nothing known about its pictures is absent, and its address is left bare. `stamp` is
    the viewer's, so the token moves with what this user may see.
    """
    if not asset_ids:
        return {}
    query, params = in_clause(_ART_OF_FILES, list(dict.fromkeys(asset_ids)))
    rows = await db.fetch_all(query, params)
    found: dict[str, str | None] = {}
    for row in rows:
        version = art_version(row["art_marks"], stamp)
        if version is not None:
            found[str(row["asset_id"])] = version
    return found


#: The decision to offer an Undo on for each file of a page: the decision rather than the filing,
#: since the decision knows how to put itself back. The newest one still standing wins.
#:
#: `CROSS JOIN` pins the subjects as the outer loop: at most a page of bound ids, against a walk of
#: every decision the planner otherwise picks, which `ANALYZE` does not mend.
_DECISIONS_FOR_FILES = """
SELECT s.subject_id AS asset_id, d.id AS decision_id, d.decided_at AS decided_at
  FROM workbench_decision_subjects s
  CROSS JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = 'asset' AND s.subject_id IN (?*)
   AND d.queue = ? AND d.reversed_at IS NULL
 ORDER BY d.decided_at ASC, d.id ASC
"""

#: Whether the workbench has ever been installed here. See `decisions_for_files`.
_DECISION_TABLES = (
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
    "('workbench_decisions', 'workbench_decision_subjects')"
)


def _filed_by(viewer: Viewer, sources: tuple[str, str]) -> dict[str, object]:
    """What the three filed-from reads bind: the pass's two words and the verdict's two values."""
    return {**seen_by(viewer), "first": sources[0], "second": sources[1]}


async def count_usernames_filed_from(db: Database, viewer: Viewer, sources: tuple[str, str]) -> int:
    """How many usernames carry a filing this pass wrote on a file this viewer may be shown."""
    row = await db.fetch_one(_FILING_USERNAMES, _filed_by(viewer, sources))
    return 0 if row is None else int(row["total"])


async def usernames_filed_from(
    db: Database, viewer: Viewer, sources: tuple[str, str], *, limit: int, offset: int
) -> list[tuple[str, str, str | None, str | None, int]]:
    """One page of `(username_id, name, site, person_id, files)`, biggest first.

    `person_id` is where a press on the username goes (`sentences.username_opens`).
    """
    rows = await db.fetch_all(
        _FILING_GROUPS, {**_filed_by(viewer, sources), "limit": limit, "offset": offset}
    )
    return [
        (
            str(row["username_id"]),
            str(row["name"]),
            None if row["site"] is None else str(row["site"]),
            None if row["person_id"] is None else str(row["person_id"]),
            int(row["files"]),
        )
        for row in rows
    ]


async def position_filed_from(
    db: Database, viewer: Viewer, sources: tuple[str, str], username_id: str
) -> int | None:
    """Where one username sits on the list `usernames_filed_from` pages, or None when it is not on
    this viewer's list. See `_FILING_POSITION`."""
    row = await db.fetch_one(
        _FILING_POSITION, {**_filed_by(viewer, sources), "username": username_id}
    )
    return None if row is None else int(row["at"])


async def files_filed_under(
    db: Database, *, username_id: str, sources: tuple[str, str], limit: int, stamp: int
) -> list[tuple[str, str, str | None]]:
    """`(asset_id, filename, art)` for the files one pass put under one username, capped.

    `art` is the still's picture token, or None (`art_of_files`): a cache key, never a capability.
    """
    rows = await db.fetch_all(_FILES_FILED_UNDER, (username_id, *sources, limit))
    return [
        (str(row["asset_id"]), str(row["filename"]), art_version(row["art_marks"], stamp))
        for row in rows
    ]


async def decisions_for_files(
    db: Database, asset_ids: Sequence[str], *, queue: str
) -> dict[str, str]:
    """The decision to offer an Undo on for each of these files, where there is one.

    Empty where the workbench, whose tables are a slice's, has never run here.
    """
    if not asset_ids:
        return {}
    present = {str(row["name"]) for row in await db.fetch_all(_DECISION_TABLES)}
    if {"workbench_decisions", "workbench_decision_subjects"} - present:
        return {}
    query, params = in_clause(_DECISIONS_FOR_FILES, list(asset_ids))
    rows = await db.fetch_all(query, [*params, queue])
    # Oldest first out of the statement, so the last write per file is the newest, which is what
    # an Undo on this page means: take back the filing that is showing.
    found: dict[str, str] = {}
    for row in rows:
        found[str(row["asset_id"])] = str(row["decision_id"])
    return found
