# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pile of answers about files: what is waiting, what was answered, settling and taking back."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from sift.kernel.access.catalog import (
    art_of_files,
)
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.db import Row
from sift.kernel.ledger import Actor
from sift.kernel.records import FoundRecord, Subject
from sift.slices.stash_boxes.adapter import (
    EXACT as EXACT,
)
from sift.slices.stash_boxes.adapter import (
    from_json,
)
from sift.slices.stash_boxes.configured import _ONE, Linked
from sift.slices.stash_boxes.enrich import AssetWriter
from sift.slices.stash_boxes.grades import Grade, Match
from sift.slices.stash_boxes.linking import LinkingBoxes
from sift.slices.stash_boxes.taken_back import (
    AppliedAnswer,
    PutBack,
    TakenBack,
    put_back_answers_on,
    still_said,
    take_back_answer_on,
)

# WHICH WAITING ANSWERS ARE STILL QUESTIONS: none about a file kept local now.

#: What is still waiting, surest first. The order is the product: somebody working through a pile
#: should meet the answers that need the least thought at the top of it.
_MATCHES_WAITING = (
    "SELECT * FROM asset_stash_box_matches WHERE state = 'waiting'"
    " AND asset_id NOT IN (SELECT id FROM assets WHERE keep_local = 1)"  # nosemgrep: sift-no-asset-sql-outside-kernel
    " AND asset_id NOT IN ("
    "SELECT ap.asset_id FROM asset_people ap"
    " JOIN people p ON p.id = ap.person_id AND p.keep_local = 1"
    " UNION SELECT atg.asset_id FROM asset_tags atg"
    " JOIN tags t ON t.id = atg.tag_id AND t.keep_local = 1"
    " UNION SELECT aa.asset_id FROM asset_usernames aa"
    " JOIN usernames ac ON ac.id = aa.username_id"
    " JOIN ("
    "WITH RECURSIVE reach_up(site_id, ancestor_id) AS ("
    "SELECT id, id FROM sites"
    " UNION SELECT r.site_id, p.parent_id FROM reach_up r"
    " JOIN sites p ON p.id = r.ancestor_id WHERE p.parent_id IS NOT NULL)"
    " SELECT site_id, ancestor_id FROM reach_up"
    ") reach ON reach.site_id = ac.site_id"
    " JOIN sites pl ON pl.id = reach.ancestor_id AND pl.keep_local = 1"
    " UNION SELECT fl.asset_id FROM folders kf"  # nosemgrep: sift-no-asset-sql-outside-kernel
    " CROSS JOIN folder_ancestry fan ON fan.ancestor_id = kf.id"
    " CROSS JOIN asset_locations fl ON fl.folder_id = fan.folder_id"  # nosemgrep: sift-no-asset-sql-outside-kernel
    " WHERE kf.keep_local = 1"
    ")"
    " ORDER BY grade = 'certain' DESC, grade = 'likely' DESC, found_at ASC, asset_id ASC"
    " LIMIT ? OFFSET ?"
)

_COUNT_WAITING = (
    "SELECT COUNT(*) AS n FROM asset_stash_box_matches WHERE state = 'waiting'"
    " AND asset_id NOT IN (SELECT id FROM assets WHERE keep_local = 1)"  # nosemgrep: sift-no-asset-sql-outside-kernel
    " AND asset_id NOT IN ("
    "SELECT ap.asset_id FROM asset_people ap"
    " JOIN people p ON p.id = ap.person_id AND p.keep_local = 1"
    " UNION SELECT atg.asset_id FROM asset_tags atg"
    " JOIN tags t ON t.id = atg.tag_id AND t.keep_local = 1"
    " UNION SELECT aa.asset_id FROM asset_usernames aa"
    " JOIN usernames ac ON ac.id = aa.username_id"
    " JOIN ("
    "WITH RECURSIVE reach_up(site_id, ancestor_id) AS ("
    "SELECT id, id FROM sites"
    " UNION SELECT r.site_id, p.parent_id FROM reach_up r"
    " JOIN sites p ON p.id = r.ancestor_id WHERE p.parent_id IS NOT NULL)"
    " SELECT site_id, ancestor_id FROM reach_up"
    ") reach ON reach.site_id = ac.site_id"
    " JOIN sites pl ON pl.id = reach.ancestor_id AND pl.keep_local = 1"
    " UNION SELECT fl.asset_id FROM folders kf"  # nosemgrep: sift-no-asset-sql-outside-kernel
    " CROSS JOIN folder_ancestry fan ON fan.ancestor_id = kf.id"
    " CROSS JOIN asset_locations fl ON fl.folder_id = fan.folder_id"  # nosemgrep: sift-no-asset-sql-outside-kernel
    " WHERE kf.keep_local = 1"
    ")"
)

#: What was answered, newest answer first: the other state of the same pile, one press away on the
#: tab line. `decided_at` is the moment the press landed; a row settled before the column carried
#: it falls to when it was found.
_MATCHES_ANSWERED = """
SELECT * FROM asset_stash_box_matches WHERE state <> 'waiting'
 ORDER BY COALESCE(decided_at, found_at) DESC, asset_id ASC
 LIMIT ? OFFSET ?
"""

_COUNT_ANSWERED = "SELECT COUNT(*) AS n FROM asset_stash_box_matches WHERE state <> 'waiting'"

# The same settled half for ONE file, which the chooser on a file's own menu reads so a file a box
# already matched says so rather than "nothing recognized it".
_MATCHES_ANSWERED_FOR = """
SELECT * FROM asset_stash_box_matches WHERE state <> 'waiting' AND asset_id = ?
 ORDER BY COALESCE(decided_at, found_at) DESC, asset_id ASC
 LIMIT ? OFFSET ?
"""

_COUNT_ANSWERED_FOR = (
    "SELECT COUNT(*) AS n FROM asset_stash_box_matches WHERE state <> 'waiting' AND asset_id = ?"
)

# The same list, for ONE file. A separate statement rather than an optional clause spliced into the
# one above: query text in this codebase is never built from anything, and two fixed strings is the
# price of that rule. The order is the same one, so a row that leads the pile leads here too.
_MATCHES_WAITING_FOR = """
SELECT * FROM asset_stash_box_matches WHERE state = 'waiting' AND asset_id = ?
 ORDER BY grade = 'certain' DESC, grade = 'likely' DESC, found_at ASC, asset_id ASC
 LIMIT ? OFFSET ?
"""

_COUNT_WAITING_FOR = (
    "SELECT COUNT(*) AS n FROM asset_stash_box_matches WHERE state = 'waiting' AND asset_id = ?"
)

# WHERE ONE WAITING MATCH SITS ON THE PILE, counting from zero: how many waiting rows come before it
# in the pile's own order, as one comparable rank. The kept-local arms are the pile's own, byte for
# byte (`test_the_pile_carries_the_kernels_kept_local_rule`). No row when it is not waiting.
_MATCH_WAITING_POSITION = (
    "SELECT (SELECT COUNT(*) FROM asset_stash_box_matches WHERE state = 'waiting'"
    " AND asset_id NOT IN (SELECT id FROM assets WHERE keep_local = 1)"  # nosemgrep: sift-no-asset-sql-outside-kernel
    " AND asset_id NOT IN ("
    "SELECT ap.asset_id FROM asset_people ap"
    " JOIN people p ON p.id = ap.person_id AND p.keep_local = 1"
    " UNION SELECT atg.asset_id FROM asset_tags atg"
    " JOIN tags t ON t.id = atg.tag_id AND t.keep_local = 1"
    " UNION SELECT aa.asset_id FROM asset_usernames aa"
    " JOIN usernames ac ON ac.id = aa.username_id"
    " JOIN ("
    "WITH RECURSIVE reach_up(site_id, ancestor_id) AS ("
    "SELECT id, id FROM sites"
    " UNION SELECT r.site_id, p.parent_id FROM reach_up r"
    " JOIN sites p ON p.id = r.ancestor_id WHERE p.parent_id IS NOT NULL)"
    " SELECT site_id, ancestor_id FROM reach_up"
    ") reach ON reach.site_id = ac.site_id"
    " JOIN sites pl ON pl.id = reach.ancestor_id AND pl.keep_local = 1"
    " UNION SELECT fl.asset_id FROM folders kf"  # nosemgrep: sift-no-asset-sql-outside-kernel
    " CROSS JOIN folder_ancestry fan ON fan.ancestor_id = kf.id"
    " CROSS JOIN asset_locations fl ON fl.folder_id = fan.folder_id"  # nosemgrep: sift-no-asset-sql-outside-kernel
    " WHERE kf.keep_local = 1"
    ")"
    " AND (CASE grade WHEN 'certain' THEN 0 WHEN 'likely' THEN 1 ELSE 2 END, found_at, asset_id)"
    " < (CASE here.grade WHEN 'certain' THEN 0 WHEN 'likely' THEN 1 ELSE 2 END,"
    " here.found_at, here.asset_id)"
    ") AS at FROM asset_stash_box_matches here"
    " WHERE here.asset_id = ? AND here.box_id = ? AND here.state = 'waiting'"
)

# And where one ANSWERED match sits on the answered half: newest answer first, the file's id last.
_MATCH_ANSWERED_POSITION = """
SELECT (SELECT COUNT(*) FROM asset_stash_box_matches o
         WHERE o.state <> 'waiting'
           AND (COALESCE(o.decided_at, o.found_at) > COALESCE(here.decided_at, here.found_at)
                OR (COALESCE(o.decided_at, o.found_at) = COALESCE(here.decided_at, here.found_at)
                    AND o.asset_id < here.asset_id))) AS at
  FROM asset_stash_box_matches here
 WHERE here.asset_id = ? AND here.box_id = ? AND here.state <> 'waiting'
"""

_MATCH_BY_ID = "SELECT * FROM asset_stash_box_matches WHERE asset_id = ? AND box_id = ?"


@dataclass(frozen=True, slots=True)
class _Taking:
    """One applied answer on its way to being taken back: what the transaction needs from the
    half of it that runs before (`StashBoxService._fields_taken_back`)."""

    answer: AppliedAnswer
    #: The other answers still applied on the file, read the way the writer reads them.
    standing: Sequence[Mapping[str, object]]
    #: The columns and addresses the writer cleared, for the Undo to write back.
    cleared: Mapping[str, object]
    links: Sequence[str]
    #: The answer as it was applied (its record and its stored reading), for the Undo of a re-ask.
    was: tuple[str, str]


#: A file's AGREED answers: what a file has where a person has a link. Sought on the key's first
#: column, so it is one file's rows (a row per box, three at most) however large the pile.
_AGREED_FOR = "SELECT * FROM asset_stash_box_matches WHERE asset_id = ? AND state = 'applied'"

_SETTLE_MATCH = """
UPDATE asset_stash_box_matches SET state = ?, decided_at = ?
 WHERE asset_id = ? AND box_id = ? AND state = 'waiting'
RETURNING asset_id
"""

#: Putting a settled match back among the questions, for a decision being taken back. The mirror of
#: the settle above: reopening one that is already open is nothing, not a second undo.
_REOPEN_MATCH = """
UPDATE asset_stash_box_matches SET state = 'waiting', decided_at = NULL
 WHERE asset_id = ? AND box_id = ? AND state != 'waiting'
RETURNING asset_id
"""

_ANY_MATCH = "SELECT 1 FROM asset_stash_box_matches LIMIT 1"


def _creator_not_a_site(record: FoundRecord) -> FoundRecord:
    """The same answer with the studio it names read as the creator, not as a site."""
    if "site" not in record.fields:
        return record
    fields = {key: value for key, value in record.fields.items() if key != "site"}
    creator = str(record.fields["site"] or "").strip()
    if creator and not str(fields.get("creator") or "").strip():
        held = fields.get("people")
        # An answer naming a studio and nobody else is the ordinary shape on a box like this, so
        # the list has to be allowed to be absent rather than assumed.
        people = [str(one) for one in held] if isinstance(held, list) else []
        fields["creator"] = creator
        fields["people"] = [
            creator,
            *(one for one in people if one.strip().casefold() != creator.casefold()),
        ]
    # And its id goes with it. A payload kept while this box's studios were read as sites carries
    # the studio's id under `site`; read as the creator now, the id is the creator's, and left
    # under `site` the creator a confirmation creates would go unlinked (`FoundRecord.refs`).
    refs = {kind: dict(named) for kind, named in record.refs.items()}
    moved = refs.pop(Subject.SITE.value, {})
    if creator and moved:
        remote_id = next(
            (one for name, one in moved.items() if name.strip().casefold() == creator.casefold()),
            None,
        )
        if remote_id:
            refs.setdefault(Subject.PERSON.value, {}).setdefault(creator, remote_id)
    return replace(record, fields=fields, refs=refs)


def _nothing(box_id: str) -> FoundRecord:
    """A placeholder for a kept row whose payload will not read.

    A record that outlives the version that wrote it is a row a screen still has to draw. Answering
    with an empty record rather than raising keeps one unreadable payload from breaking the whole
    pile, and it is visibly empty, so nobody mistakes it for an answer.
    """
    return FoundRecord(source_id=box_id, remote_id="", subject=Subject.ASSET, name="")


class MatchPile(LinkingBoxes):
    """Read the pile, settle a match, reopen one, and take an applied answer back off its file."""

    async def waiting(
        self, *, limit: int, offset: int = 0, asset_id: str | None = None
    ) -> tuple[list[Match], int]:
        """What the pass found and nobody has answered, surest first, with how many there are.

        `asset_id` narrows it to one file, which is what the chooser on a file's own menu asks for.
        The whole pile and one file's share of it are the same question with the same order and the
        same rules, so they are one method: two would be two places to remember that a settled
        match is not waiting.
        """
        if asset_id is None:
            rows = await self._db.fetch_all(_MATCHES_WAITING, (limit, offset))
            counted = await self._db.fetch_one(_COUNT_WAITING)
        elif await self.kept_local(Subject.ASSET, asset_id):
            # One file's share of the pile, under the same rule the whole pile's statement carries:
            # an answer about a file kept local now is not a question anybody can say yes to. Asked
            # through the one predicate rather than a third copy of the statement, because it is
            # one file and the door's own point read answers it.
            return [], 0
        else:
            rows = await self._db.fetch_all(_MATCHES_WAITING_FOR, (asset_id, limit, offset))
            counted = await self._db.fetch_one(_COUNT_WAITING_FOR, (asset_id,))
        return [await self._match(row) for row in rows], int(counted["n"]) if counted else 0

    async def match_position(self, asset_id: str, box_id: str, *, answered: bool) -> int | None:
        """Where one match sits on the pile (`waiting`) or on its answered half (`answered`), or
        None when it is not on that list any more. What a page asked for by its first row starts
        at. See `_MATCH_WAITING_POSITION`."""
        statement = _MATCH_ANSWERED_POSITION if answered else _MATCH_WAITING_POSITION
        row = await self._db.fetch_one(statement, (asset_id, box_id))
        return None if row is None else int(row["at"])

    async def answered(
        self, *, limit: int, offset: int = 0, asset_id: str | None = None
    ) -> tuple[list[Match], int]:
        """What was agreed to or refused, newest answer first, with how many there are.

        The settled half of the pile `waiting` pages, so the tab can show it as a state of its
        own rather than as a number in an empty state's sentence. `asset_id` narrows it to one
        file, as it does for `waiting`.
        """
        if asset_id is None:
            rows = await self._db.fetch_all(_MATCHES_ANSWERED, (limit, offset))
            counted = await self._db.fetch_one(_COUNT_ANSWERED)
        else:
            rows = await self._db.fetch_all(_MATCHES_ANSWERED_FOR, (asset_id, limit, offset))
            counted = await self._db.fetch_one(_COUNT_ANSWERED_FOR, (asset_id,))
        return [await self._match(row) for row in rows], int(counted["n"]) if counted else 0

    async def art_of(self, asset_ids: Sequence[str], *, stamp: int) -> dict[str, str | None]:
        """The picture token for each of these files, so the pile's stills may be kept.

        Passed straight to the kernel's one reader of it. Here rather than in the router because
        the router has no database handle and should not grow one: what it has is this service,
        and "what are these files' pictures now" is a question about the rows it just handed over.
        """
        return await art_of_files(self._db, asset_ids, stamp=stamp)

    async def any_match(self) -> bool:
        """Whether the pass has ever found anything on this install: whether a panel is drawn at
        all, where a library that answered everything keeps its panel at zero."""
        return await self._db.fetch_one(_ANY_MATCH) is not None

    async def match(self, asset_id: str, box_id: str) -> Match | None:
        """One answer, or None. What a confirm screen reads before it writes anything."""
        row = await self._db.fetch_one(_MATCH_BY_ID, (asset_id, box_id))
        return None if row is None else await self._match(row)

    async def agreed_answers(self, asset_id: str) -> list[Linked]:
        """Every box answer somebody (or the exact-match switch) agreed to about ONE file."""
        out: list[Linked] = []
        for row in await self._db.fetch_all(_AGREED_FOR, (asset_id,)):
            held = await self._match(row)
            out.append(
                Linked(
                    held.box_id,
                    held.box_name,
                    held.remote_id,
                    held.record,
                    held.decided_at if held.decided_at is not None else held.found_at,
                )
            )
        return out

    async def settle(self, asset_id: str, box_id: str, *, applied: bool) -> bool:
        """Mark one answer as dealt with. False when it was already settled.

        The statement only moves a row that is still waiting, so answering the same match twice is
        nothing rather than a second decision, which is what makes a bulk confirm safe to press
        again after it half-failed.
        """
        rows = await self._write_rows(
            _SETTLE_MATCH,
            ("applied" if applied else "refused", self._now(), asset_id, box_id),
        )
        return bool(rows)

    async def reopen(self, asset_id: str, box_id: str) -> bool:
        """Put a settled answer back among the questions, for a decision being taken back."""
        return bool(await self._write_rows(_REOPEN_MATCH, (asset_id, box_id)))

    async def take_back(
        self,
        asset_id: str,
        box_id: str,
        *,
        actor: Actor,
        reopen: bool,
        writer: AssetWriter | None,
    ) -> TakenBack | None:
        """Take back an APPLIED answer whole: refused (or put back among the questions where
        `reopen`), with everything it wrote taken off the file. None where it was not applied.

        What it wrote is read off the file, against what the answers still applied on it say, read
        the way the writer read them (`taken_back`): the fields through the file's writer first,
        then the rows, the shells and the History line with its Undo in one transaction.
        """
        row = await self._db.fetch_one(_MATCH_BY_ID, (asset_id, box_id))
        if row is None or str(row["state"]) != "applied":
            return None
        taking = await self._fields_taken_back(row, writer)
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            return await take_back_answer_on(
                connection,
                taking.answer,
                still=still_said(taking.standing),
                now=self._now(),
                reopen=reopen,
                actor=actor,
                cleared=taking.cleared,
                links=taking.links,
            )

    async def _fields_taken_back(self, row: Row, writer: AssetWriter | None) -> _Taking:
        """The first half of taking back one applied answer, before its transaction: the answers
        still standing on the file, read the way the writer reads them, and the columns and
        addresses only this one said, cleared through the file's writer where there is one (the
        writer has a write of its own, so it cannot join the transaction that follows)."""
        held = await self._match(row)
        asset_id, box_id = str(row["asset_id"]), str(row["box_id"])
        offered: Mapping[str, object] = held.record.fields
        standing: list[Mapping[str, object]] = [
            one.record.fields
            for one in await self.agreed_answers(asset_id)
            if one.source_id != box_id
        ]
        cleared: Mapping[str, object] = {}
        links: list[str] = []
        if writer is not None:
            offered = await writer.read_as_written(offered)
            standing = [await writer.read_as_written(one) for one in standing]
            cleared, links = await writer.take_back_fields(asset_id, offered, still_said=standing)
        return _Taking(
            answer=AppliedAnswer(asset_id=asset_id, box_id=box_id, box_name=held.box_name),
            standing=standing,
            cleared=cleared,
            links=links,
            was=(str(row["remote_id"]), str(row["payload"])),
        )

    async def put_back_taken(self, receipt_id: str, payload: Mapping[str, object]) -> PutBack:
        """Undo a take-back from its receipt: the rows and the answers' states, in one transaction.
        The fields are the caller's to write back, through the file's writer."""
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            return await put_back_answers_on(connection, receipt_id, payload)

    async def _match(self, row: Row) -> Match:
        """One kept answer, in the shape a screen and the enrichment rules both read."""
        records = from_json(str(row["payload"]))
        box_id = str(row["box_id"])
        found = await self._db.fetch_one(_ONE, (box_id,))
        record = records[0] if records else _nothing(box_id)
        # A kept answer is the mapper's output on the day it was kept, and an older mapper put a
        # creator-as-studio box's creator under `site`. Read the way the box reads now, or a confirm
        # pressed today (and the catch-up over old matches) would file a video under a site named
        # after a person.
        if found is not None and str(found["sites_are"]) == "person":
            record = _creator_not_a_site(record)
        return Match(
            asset_id=str(row["asset_id"]),
            box_id=box_id,
            box_name=str(found["name"]) if found is not None else box_id,
            remote_id=str(row["remote_id"]),
            record=record,
            grade=Grade(str(row["grade"])),
            state=str(row["state"]),
            found_at=int(row["found_at"]),
            decided_at=None if row["decided_at"] is None else int(row["decided_at"]),
        )

    async def _write_rows(self, sql: str, params: tuple[object, ...]) -> list[Row]:
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            return list(await connection.execute_fetchall(sql, params))
