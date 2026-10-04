# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the boxes are agreed to know, read for the ledger, and the answers kept by hand."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from sift.kernel.access import sentences as say
from sift.kernel.access.history_boxes import agreeing_with_box, filled_named
from sift.kernel.access.sentences import Line
from sift.kernel.changes import About
from sift.kernel.db import Connection, point_read
from sift.kernel.records import FoundRecord, Subject
from sift.slices.stash_boxes.adapter import (
    EXACT as EXACT,
)
from sift.slices.stash_boxes.adapter import (
    from_json,
)
from sift.slices.stash_boxes.scanning import ScanningBoxes

_APPLIED_FILES = (
    "SELECT COUNT(DISTINCT asset_id) AS n FROM asset_stash_box_matches WHERE state = 'applied'"
)

#: The same files by id, for a count held to what one viewer may see.
_APPLIED_FILE_IDS = "SELECT DISTINCT asset_id FROM asset_stash_box_matches WHERE state = 'applied'"

#: Every person a box created who is not linked to that box, with the box's own word.
#:
#: `people.created_by_box_id` is the record of the creation (written the moment a row is made, see
#: `enrich.record_who_invented`). New rows are linked by id as they are made; rows created by an
#: older version are not, and have a monogram where a linked person has the box's picture. A box
#: that has since been removed has cleared its column (`clear_created_by_box`), so the join finds
#: only boxes that exist.
_INVENTED_UNLINKED = """
SELECT p.id AS id, p.name AS name, b.id AS box_id, b.slug AS box
  FROM people p
  JOIN stash_boxes b ON b.id = p.created_by_box_id
 WHERE NOT EXISTS (SELECT 1 FROM person_stash_box_links l
                    WHERE l.person_id = p.id AND l.box_id = p.created_by_box_id)
 ORDER BY p.id
"""

_ANY_INVENTED_UNLINKED = (
    "SELECT 1 FROM people p JOIN stash_boxes b ON b.id = p.created_by_box_id"
    " WHERE NOT EXISTS (SELECT 1 FROM person_stash_box_links l"
    " WHERE l.person_id = p.id AND l.box_id = p.created_by_box_id) LIMIT 1"
)

#: The same question about SITES: every Site a box made that is not linked to that box. A studio
#: or a network a box named before its id was kept wears a letter where a linked Site wears the
#: box's picture, and the linking pass asks that box about it by name: the box's own spelling of
#: a network where one is kept as an alias, since that is the name the box files it under.
_INVENTED_SITES_UNLINKED = """
SELECT s.id AS id,
       COALESCE((SELECT a.alias FROM site_aliases a
                  WHERE a.site_id = s.id AND a.alias LIKE '%(network)%'
                  ORDER BY a.id LIMIT 1), s.name) AS name,
       b.id AS box_id, b.slug AS box
  FROM sites s
  JOIN stash_boxes b ON b.id = s.created_by_box_id
 WHERE NOT EXISTS (SELECT 1 FROM site_stash_box_links l
                    WHERE l.site_id = s.id AND l.box_id = s.created_by_box_id)
 ORDER BY s.id
"""

_ANY_INVENTED_SITES_UNLINKED = (
    "SELECT 1 FROM sites s JOIN stash_boxes b ON b.id = s.created_by_box_id"
    " WHERE NOT EXISTS (SELECT 1 FROM site_stash_box_links l"
    " WHERE l.site_id = s.id AND l.box_id = s.created_by_box_id) LIMIT 1"
)

#: Every field somebody has kept their own answer to, with the pair of values they answered about.
#: Read whole, because the caller works out the whole library's disagreements in one pass; it is
#: bounded by what has been ANSWERED by hand.
_EVERY_KEPT = "SELECT subject, local_id, box_id, key, mine, theirs FROM stash_box_kept"

_KEPT_FOR = point_read(
    "stash_boxes.kept_for_subject",
    "SELECT box_id, key, mine, theirs FROM stash_box_kept WHERE subject = ? AND local_id = ?",
)

#: One answer, replacing whatever was answered about the same field before. A second answer is a
#: later judgement about a newer pair of values, not a second row.
_RECORD_KEPT = (
    "INSERT INTO stash_box_kept (subject, local_id, box_id, key, mine, theirs, decided_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?)"
    " ON CONFLICT(subject, local_id, box_id, key)"
    " DO UPDATE SET mine = excluded.mine, theirs = excluded.theirs,"
    " decided_at = excluded.decided_at"
)

#: What one answer was, so it can be taken back. See `Reconciler.forget_kept`.
_FORGET_KEPT = (
    "DELETE FROM stash_box_kept WHERE subject = ? AND local_id = ? AND box_id = ? AND key = ?"
)

# Every link on the ledger in the order it is paged, newest first, as the subject and box it is
# about. The subjects' own tables are joined, so a link whose subject is gone is on no page. A page
# is a slice of this list after the viewer's own scoping, which is what keeps a page full and its
# positions true for somebody who may not see every row.
_LEDGER_KEYS = """
SELECT subject, local_id, box_id FROM (
  SELECT 'person' AS subject, l.person_id AS local_id, l.box_id AS box_id, l.fetched_at AS fetched_at
    FROM person_stash_box_links l JOIN people p ON p.id = l.person_id
   WHERE :kind IN ('', 'person')
  UNION ALL
  SELECT 'site', l.site_id, l.box_id, l.fetched_at
    FROM site_stash_box_links l JOIN sites s ON s.id = l.site_id
   WHERE :kind IN ('', 'site')
  UNION ALL
  SELECT 'tag', l.tag_id, l.box_id, l.fetched_at
    FROM tag_stash_box_links l JOIN tags t ON t.id = l.tag_id
   WHERE :kind IN ('', 'tag')
)
 ORDER BY fetched_at DESC, local_id, box_id
"""

# The rows of one page of the ledger, in the order the keys were handed in (a JSON list of
# `[subject, local_id, box_id]`), with each subject's own name and what the last enrichment run
# filled in: the same correlated read and ordering the entity threads use, so the ledger row and
# the record's own History cannot disagree about one link.
_LEDGER_ROWS = """
SELECT link.subject, link.local_id, link.name, link.box_id, link.payload, link.fetched_at, (
         SELECT r.applied FROM enrichment_runs r
          WHERE r.subject = link.subject AND r.local_id = link.local_id AND r.box_id = link.box_id
          ORDER BY r.at DESC, r.id DESC LIMIT 1) AS applied, (
         SELECT r.at FROM enrichment_runs r
          WHERE r.subject = link.subject AND r.local_id = link.local_id AND r.box_id = link.box_id
          ORDER BY r.at DESC, r.id DESC LIMIT 1) AS run_at
  FROM json_each(:keys) k
  JOIN (
    SELECT 'person' AS subject, l.person_id AS local_id, p.name AS name, l.box_id AS box_id,
           l.payload AS payload, l.fetched_at AS fetched_at
      FROM person_stash_box_links l JOIN people p ON p.id = l.person_id
    UNION ALL
    SELECT 'site', l.site_id, s.name, l.box_id, l.payload, l.fetched_at
      FROM site_stash_box_links l JOIN sites s ON s.id = l.site_id
    UNION ALL
    SELECT 'tag', l.tag_id, t.name, l.box_id, l.payload, l.fetched_at
      FROM tag_stash_box_links l JOIN tags t ON t.id = l.tag_id
  ) AS link
    ON link.subject = json_extract(k.value, '$[0]')
   AND link.local_id = json_extract(k.value, '$[1]')
   AND link.box_id = json_extract(k.value, '$[2]')
 ORDER BY k.key
"""

# What the unattended enrichment could not decide, and the reader that sweeps it.
_NOTE_UNDECIDED = (
    "INSERT INTO stash_box_undecided (subject, local_id, candidates, seen_at) VALUES (?, ?, ?, ?)"
    " ON CONFLICT(subject, local_id) DO UPDATE SET candidates = excluded.candidates,"
    " seen_at = excluded.seen_at"
)

#: Every undecided name in the order the list is paged (newest first, the id last) over the
#: rows whose subject still has a name. A page is a slice of it after the viewer's own scoping.
_UNDECIDED_KEYS = """
SELECT u.subject, u.local_id FROM stash_box_undecided u
  LEFT JOIN people p ON u.subject = 'person' AND p.id = u.local_id
  LEFT JOIN sites s ON u.subject = 'site' AND s.id = u.local_id
  LEFT JOIN tags t ON u.subject = 'tag' AND t.id = u.local_id
 WHERE COALESCE(p.name, s.name, t.name) IS NOT NULL
 ORDER BY u.seen_at DESC, u.local_id
"""

#: The rows of one page of that list, in the order the keys were handed in (`[subject, local_id]`).
_UNDECIDED_ROWS = """
SELECT u.subject, u.local_id, u.candidates, u.seen_at,
       COALESCE(p.name, s.name, t.name) AS name
  FROM json_each(:keys) k
  JOIN stash_box_undecided u
    ON u.subject = json_extract(k.value, '$[0]') AND u.local_id = json_extract(k.value, '$[1]')
  LEFT JOIN people p ON u.subject = 'person' AND p.id = u.local_id
  LEFT JOIN sites s ON u.subject = 'site' AND s.id = u.local_id
  LEFT JOIN tags t ON u.subject = 'tag' AND t.id = u.local_id
 WHERE COALESCE(p.name, s.name, t.name) IS NOT NULL
 ORDER BY k.key
"""


@dataclass(frozen=True, slots=True)
class LinkedSubject:
    """One row of the ledger: a subject, the box that knows it, and when that was agreed."""

    subject: Subject
    local_id: str
    #: What THIS library calls them, read in the same statement as the link.
    name: str
    box_id: str
    record: FoundRecord
    fetched_at: int
    #: What the last enrichment run against this box filled in, exactly as `enrichment_runs.applied`
    #: stored it. None where no run was recorded (a bare link, or one made before the column
    #: existed), which is a different answer from a run that filled nothing in. `fields_filled`
    #: is the one reader of it; see `kernel.records`.
    applied: str | None = None
    #: When that last run was written, which is the moment the rows it added carry; None where no
    #: run was ever written for this link (made before Sift recorded what a box fills in).
    run_at: int | None = None


@dataclass(frozen=True, slots=True)
class LedgerKey:
    """One link on the ledger, as what it is about: its subject and the box that knows it."""

    subject: Subject
    local_id: str
    box_id: str


@dataclass(frozen=True, slots=True)
class Undecided:
    """A subject the unattended enrichment could not choose for: one box holds more than one
    certain entry for the name, and which of them is a person's call."""

    subject: Subject
    local_id: str
    name: str
    candidates: int
    seen_at: int


class BoxLedger(ScanningBoxes):
    """The ledger's pages, the undecided names, the rows a box made, and the kept answers."""

    async def ledger_keys(self, *, subject: Subject | None = None) -> list[LedgerKey]:
        """Every link the ledger pages, in its order, as the subject and box it is about."""
        rows = await self._db.fetch_all(
            _LEDGER_KEYS, {"kind": subject.value if subject is not None else ""}
        )
        return [
            LedgerKey(Subject(str(row["subject"])), str(row["local_id"]), str(row["box_id"]))
            for row in rows
        ]

    async def ledger_rows(self, keys: Sequence[LedgerKey]) -> list[LinkedSubject]:
        """These links as ledger rows, in the order given. One statement for the page, names
        included. A row whose kept answer will not read is skipped, as `linked_subjects` skips it.
        """
        if not keys:
            return []
        wanted = json.dumps([[one.subject.value, one.local_id, one.box_id] for one in keys])
        out: list[LinkedSubject] = []
        for row in await self._db.fetch_all(_LEDGER_ROWS, {"keys": wanted}):
            records = from_json(str(row["payload"]))
            if not records:
                continue
            out.append(
                LinkedSubject(
                    subject=Subject(str(row["subject"])),
                    local_id=str(row["local_id"]),
                    name=str(row["name"]),
                    box_id=str(row["box_id"]),
                    record=records[0],
                    fetched_at=int(row["fetched_at"]),
                    applied=None if row["applied"] is None else str(row["applied"]),
                    run_at=None if row["run_at"] is None else int(row["run_at"]),
                )
            )
        return out

    async def what_it_filled(self, one: LinkedSubject, box: str) -> Line:
        """What this link's box filled in, as ONE line with every field and its values named.

        The line an entity's own History says for the same run (`history_boxes.linked_line`), in
        the ledger's vantage: the row already names the thing. A link with no run was made before
        Sift recorded what a box fills in, and says so, with what the record agrees with it on.
        """
        subject = one.subject.value
        if one.run_at is None:
            matching = await agreeing_with_box(self._db, subject, one.local_id, one.box_id)
            return say.linked_before_recorded(box, matching)
        fields = await filled_named(
            self._db, subject, one.local_id, one.box_id, at=one.run_at, stored=one.applied
        )
        return say.box_filled_named(box, fields)

    async def undecided_keys(self) -> list[tuple[Subject, str]]:
        """Every name `undecided` pages, in its order, as what it is about."""
        rows = await self._db.fetch_all(_UNDECIDED_KEYS)
        return [(Subject(str(row["subject"])), str(row["local_id"])) for row in rows]

    async def undecided_rows(self, keys: Sequence[tuple[Subject, str]]) -> list[Undecided]:
        """These undecided names as rows of the list, in the order given."""
        if not keys:
            return []
        wanted = json.dumps([[subject.value, local_id] for subject, local_id in keys])
        return [
            Undecided(
                subject=Subject(str(row["subject"])),
                local_id=str(row["local_id"]),
                name=str(row["name"]),
                candidates=int(row["candidates"]),
                seen_at=int(row["seen_at"]),
            )
            for row in await self._db.fetch_all(_UNDECIDED_ROWS, {"keys": wanted})
        ]

    async def note_undecided(self, subject: Subject, local_id: str, candidates: int) -> None:
        """Remember that the unattended enrichment could not choose for this subject."""
        await self._say(
            _NOTE_UNDECIDED,
            (subject.value, local_id, candidates, self._now()),
            About.LIBRARY,
        )

    async def applied_file_ids(self) -> list[str]:
        """The files `applied_files` counts, by id."""
        return [str(row["asset_id"]) for row in await self._db.fetch_all(_APPLIED_FILE_IDS)]

    async def applied_files(self) -> int:
        """How many files carry an answer somebody agreed to: the FILE half of what the boxes
        have enriched, which the Browse column counts and the ledger of subjects does not."""
        row = await self._db.fetch_one(_APPLIED_FILES)
        return int(row["n"]) if row else 0

    async def any_invented_unlinked(self) -> bool:
        """Whether any person a box invented is not linked to it. See `_INVENTED_UNLINKED`."""
        return await self._db.fetch_one(_ANY_INVENTED_UNLINKED) is not None

    async def invented_unlinked(self) -> list[tuple[str, str, str, str]]:
        """Each person a box invented and never linked to it: id, name, the box and its word."""
        return [
            (str(row["id"]), str(row["name"]), str(row["box_id"]), str(row["box"] or ""))
            for row in await self._db.fetch_all(_INVENTED_UNLINKED)
        ]

    async def any_invented_sites_unlinked(self) -> bool:
        """Whether any Site a box made is not linked to it. See `_INVENTED_SITES_UNLINKED`."""
        return await self._db.fetch_one(_ANY_INVENTED_SITES_UNLINKED) is not None

    async def invented_sites_unlinked(self) -> list[tuple[str, str, str, str]]:
        """Each Site a box made and never linked to it: id, name, the box and its word."""
        return [
            (str(row["id"]), str(row["name"]), str(row["box_id"]), str(row["box"] or ""))
            for row in await self._db.fetch_all(_INVENTED_SITES_UNLINKED)
        ]

    async def kept_answers(self) -> dict[tuple[str, str, str, str], tuple[str, str]]:
        """Every field somebody kept their own answer to, and the pair they answered about.

        Keyed by the field (subject, record, box, key) and holding the two values as JSON text,
        which is how they were written and how they are compared. The comparison is on the TEXT
        rather than on the decoded values deliberately: this is asking "are these still the two
        answers that were settled", and two spellings of one value are two answers to somebody
        reading the panel.
        """
        return {
            (
                str(row["subject"]),
                str(row["local_id"]),
                str(row["box_id"]),
                str(row["key"]),
            ): (str(row["mine"]), str(row["theirs"]))
            for row in await self._db.fetch_all(_EVERY_KEPT)
        }

    async def kept_answers_for(
        self, subject: str, local_id: str
    ) -> dict[tuple[str, str], tuple[str, str]]:
        """The same, about ONE record, keyed by the box and the field."""
        return {
            (str(row["box_id"]), str(row["key"])): (str(row["mine"]), str(row["theirs"]))
            for row in await self._db.fetch_all(_KEPT_FOR, (subject, local_id))
        }

    async def record_kept(
        self, subject: str, local_id: str, box_id: str, key: str, mine: str, theirs: str
    ) -> None:
        """Remember that this pair of values was settled by keeping the library's own."""
        await self._say(
            _RECORD_KEPT, (subject, local_id, box_id, key, mine, theirs, self._now()), About.LIBRARY
        )

    async def record_kept_on(
        self,
        connection: Connection,
        subject: str,
        local_id: str,
        box_id: str,
        key: str,
        mine: str,
        theirs: str,
    ) -> None:
        """The same answer, written on a connection the caller already holds.

        For the one caller whose answer is PART of a larger write: taking one box's value sets
        every other box's different value aside, and those answers go in the same transaction as
        the receipt that can take them all back. See `settle_disagreement`. Two separate writes
        would leave a window where the rows are gone and no receipt exists to bring them back.
        The caller announces, because it holds the transaction (`telling`).
        """
        await connection.execute(
            _RECORD_KEPT, (subject, local_id, box_id, key, mine, theirs, self._now())
        )

    async def forget_kept(self, subject: str, local_id: str, box_id: str, key: str) -> None:
        """Take that answer back, so the field is a question again. What the Undo does.

        Told the same way `record_kept` is, for the same panel.
        """
        await self._say(_FORGET_KEPT, (subject, local_id, box_id, key), About.LIBRARY)
