# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pass over a whole library: ask every box about each file once, and keep what came back.

It must not ask a public service the same question twice, and it never writes to a file without
being told it may: a match is a row somebody can look at, which is also the row that can be taken
back.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from sift.kernel.access.catalog import (
    kept_local_among,
)
from sift.kernel.audience import EVERY_ADMIN
from sift.kernel.changes import About, telling
from sift.kernel.ids import new_id
from sift.kernel.ledger import Actor
from sift.kernel.records import FoundRecord, Subject
from sift.kernel.vocabulary import VIA_STASH
from sift.slices.stash_boxes.adapter import (
    EXACT as EXACT,
)
from sift.slices.stash_boxes.adapter import (
    as_json,
)
from sift.slices.stash_boxes.asking import _ask_of
from sift.slices.stash_boxes.configured import _ONE
from sift.slices.stash_boxes.enrich import AssetWriter
from sift.slices.stash_boxes.grades import Grade, Match, grade_of
from sift.slices.stash_boxes.matches import _MATCH_BY_ID, MatchPile, _Taking
from sift.slices.stash_boxes.taken_back import (
    still_said,
    take_back_answer_on,
)

# --- the bulk pass ------------------------------------------------------------------------------
#
# Every statement written out, like the rest of this file. What the pass remembers is two things:
# what a box said about a file, and the fact that a file has been asked about at all.

_REMEMBER_MATCH = """
INSERT INTO asset_stash_box_matches
  (asset_id, box_id, remote_id, payload, grade, state, found_at)
VALUES (?, ?, ?, ?, ?, 'waiting', ?)
ON CONFLICT(asset_id, box_id) DO UPDATE SET
    remote_id = excluded.remote_id,
    payload   = excluded.payload,
    grade     = excluded.grade,
    found_at  = excluded.found_at
WHERE asset_stash_box_matches.state = 'waiting'
"""

#: The same, for a file somebody asked about again on purpose: an answer that differs from the one
#: already settled (another record, or the same record read differently) is a question again. An
#: identical answer leaves the decision alone.
_REMEMBER_MATCH_AGAIN = """
INSERT INTO asset_stash_box_matches
  (asset_id, box_id, remote_id, payload, grade, state, found_at)
VALUES (?, ?, ?, ?, ?, 'waiting', ?)
ON CONFLICT(asset_id, box_id) DO UPDATE SET
    remote_id  = excluded.remote_id,
    payload    = excluded.payload,
    grade      = excluded.grade,
    found_at   = excluded.found_at,
    state      = 'waiting',
    decided_at = NULL
WHERE asset_stash_box_matches.state = 'waiting'
   OR asset_stash_box_matches.remote_id <> excluded.remote_id
   OR asset_stash_box_matches.payload <> excluded.payload
"""

#: Every ask is written, never replaced. The id is a ULID, so two asks in the same second still
#: have an order.
_REMEMBER_SCAN = """
INSERT INTO stash_box_scans (id, asset_id, box_id, scanned_at, found) VALUES (?, ?, ?, ?, ?)
"""

#: Which of these files this box has already been asked about. One statement per page rather than
#: one per file: a page of five hundred asked one at a time is five hundred round trips to answer a
#: question one `IN` clause settles.
_ALREADY_ASKED = """
SELECT DISTINCT asset_id FROM stash_box_scans
 WHERE box_id = ? AND asset_id IN (SELECT value FROM json_each(?))
"""


class ScanningBoxes(MatchPile):
    """Ask about one file for the bulk pass, and say which files are still unasked."""

    async def keep_known_scene(
        self, asset_id: str, box_id: str, remote_id: str, master_key: bytes | None
    ) -> bool:
        """Fetch the scene somebody already said this file is, by its id, and keep it as the file's
        waiting answer, certain. False where the box cannot be opened or does not know the id."""
        box = await self._unsealed(box_id, master_key)
        if box is None:
            return False
        found = await self._fetched(box, Subject.ASSET, remote_id, about=(Subject.ASSET, asset_id))
        if found is None:
            return False
        answer = as_json([found])
        row = (asset_id, box_id, found.remote_id, answer, Grade.CERTAIN.value, self._now())
        await self._say(_REMEMBER_MATCH, row, About.LIBRARY)
        return True

    # --- the bulk pass -----------------------------------------------------------------------
    #
    # Everything above answers one question somebody asked. This is the pass over a whole library,
    # and the two differences are the whole design of it: it must not ask a public service the same
    # question twice, and it must never write without being told it may.

    async def scan_one(
        self,
        asset_id: str,
        hashes: Mapping[str, str],
        master_key: bytes | None,
        *,
        length_ms: int | None,
        tolerance_ms: int,
        only: str | None = None,
        again: bool = False,
        writer: AssetWriter | None = None,
        actor: Actor | None = None,
    ) -> list[Match]:
        """Ask every switched-on box about ONE file, keep what came back, and write down that it
        was asked.

        The asking-down is what makes a second pass cheap. A file no service has heard of produces
        nothing to keep, so a pass that remembered only its finds would ask about it again on every
        run, through a throttle that exists to stop exactly that.

        Only the first answer from each box is kept. The adapter has already thrown away the shape
        that means "no match" (a perceptual answer with a pile of entries in it), so what arrives
        is either one answer or a short list whose first entry is the best of them, and a screen
        offering four candidates per file per box is a screen nobody works through.

        Nothing is applied. A match is a row somebody can look at, which is also the row that can be
        taken back.

        ASKED AGAIN (`again`), an answer that differs from one already APPLIED turns it back into a
        question, and what the applied one wrote comes off the file with it, in the same write: a
        question has written nothing, so a file must not keep an answer's people, filings and
        fields once that answer is a question again. The columns go through the file's `writer`
        first, as a take-back's do, and the History line is `actor`'s, with an Undo that puts the
        rows, the columns and the answer it was back (`taken_back.take_back_answer_on`).

        What the boxes said is written in one transaction, once all of them have answered, so a
        file is never left half asked-down. The asking is still a box at a time: it is a network
        call, and holding a write open across one would keep the database locked for as long as a
        service takes to answer.
        """
        now = self._now()
        asked = await self._ask_each_box(asset_id, hashes, master_key, only)
        if not asked:
            return []
        reasked = await self._applied_and_asked_again(asset_id, asked, writer) if again else {}
        return await self._keep_answers(
            asset_id,
            asked,
            reasked,
            now=now,
            length_ms=length_ms,
            tolerance_ms=tolerance_ms,
            again=again,
            actor=actor,
        )

    async def _ask_each_box(
        self,
        asset_id: str,
        hashes: Mapping[str, str],
        master_key: bytes | None,
        only: str | None,
    ) -> list[tuple[str, str, FoundRecord | None]]:
        """Each switched-on box's first answer about this file; a box not reached is left out."""
        asked: list[tuple[str, str, FoundRecord | None]] = []
        for box_id in await self._enabled_ids(only):
            row = await self._db.fetch_one(_ONE, (box_id,))
            name = str(row["name"]) if row is not None else box_id
            answer = await self._one(
                box_id,
                "recognise",
                _ask_of(hashes),
                master_key,
                hashes=hashes,
                about=(Subject.ASSET, asset_id),
            )
            if answer.problem is not None:
                # An unreachable box has not been asked, so it is not written down as asked. The
                # next pass tries again, which is the honest reading of a service that was down.
                continue
            asked.append((box_id, name, answer.records[0] if answer.records else None))
        return asked

    async def _keep_answers(
        self,
        asset_id: str,
        asked: Sequence[tuple[str, str, FoundRecord | None]],
        reasked: Mapping[str, _Taking],
        *,
        now: int,
        length_ms: int | None,
        tolerance_ms: int,
        again: bool,
        actor: Actor | None,
    ) -> list[Match]:
        """Write down what each box said, and that it was asked, in one transaction."""
        kept: list[Match] = []
        async with telling(self._db, EVERY_ADMIN, About.LIBRARY) as connection:
            for box_id, name, found in asked:
                await connection.execute(
                    _REMEMBER_SCAN,
                    (new_id(), asset_id, box_id, now, 1 if found is not None else 0),
                )
                if found is None:
                    continue
                taking = reasked.get(box_id)
                if taking is not None:
                    await take_back_answer_on(
                        connection,
                        taking.answer,
                        still=still_said(taking.standing),
                        now=now,
                        reopen=True,
                        actor=actor or Actor.sift(VIA_STASH),
                        cleared=taking.cleared,
                        links=taking.links,
                        was=taking.was,
                    )
                grade = grade_of(found, length_ms=length_ms, tolerance_ms=tolerance_ms)
                await connection.execute(
                    _REMEMBER_MATCH_AGAIN if again else _REMEMBER_MATCH,
                    (asset_id, box_id, found.remote_id, as_json([found]), grade.value, now),
                )
                kept.append(
                    Match(
                        asset_id=asset_id,
                        box_id=box_id,
                        box_name=name,
                        remote_id=found.remote_id,
                        record=found,
                        grade=grade,
                        state="waiting",
                        found_at=now,
                    )
                )
        return kept

    async def _applied_and_asked_again(
        self,
        asset_id: str,
        asked: Sequence[tuple[str, str, FoundRecord | None]],
        writer: AssetWriter | None,
    ) -> dict[str, _Taking]:
        """The applied answers a re-ask turns back into questions, by box, each with its columns
        already cleared (`_fields_taken_back`): an answer whose record or reading differs from
        the one applied. An identical answer leaves the decision alone, as the statement does."""
        taking: dict[str, _Taking] = {}
        for box_id, _name, found in asked:
            if found is None:
                continue
            row = await self._db.fetch_one(_MATCH_BY_ID, (asset_id, box_id))
            if row is None or str(row["state"]) != "applied":
                continue
            if str(row["remote_id"]) == found.remote_id and str(row["payload"]) == as_json([found]):
                continue
            taking[box_id] = await self._fields_taken_back(row, writer)
        return taking

    async def unasked(self, asset_ids: Sequence[str], only: str | None = None) -> list[str]:
        """Which of these files at least one switched-on box has not been asked about yet.

        Asked of the page rather than of the file, in one statement per box: a page of five hundred
        checked one at a time is five hundred round trips to settle a question one `IN` clause
        answers.

        A box configured yesterday has been asked about nothing, so every file comes back for it,
        which is what makes adding a fourth stash-box sweep the library against it rather than only
        against what arrives afterwards.
        """
        boxes = await self._enabled_ids(only)
        if not asset_ids or not boxes:
            return []
        wanted = list(dict.fromkeys(asset_ids))
        # The files that must never be asked about come out of the list before any box is consulted.
        # Not the rule (the rule is the door, which refuses one of these however it is reached)
        # but the right place to spend nothing on them: a sweep that queued a job per kept-local file
        # would be a job list full of rows whose only outcome is a refusal.
        kept = await kept_local_among(self._db, wanted)
        if kept:
            wanted = [one for one in wanted if one not in kept]
            if not wanted:
                return []
        # Intersected across the boxes, so a file counts as done only once EVERY switched-on box
        # has been asked about it. The other way round (done as soon as one box has) would mean
        # a stash-box added later was never asked about anything already in the library.
        done = set(wanted)
        for box_id in boxes:
            rows = await self._db.fetch_all(_ALREADY_ASKED, (box_id, json.dumps(wanted)))
            done &= {str(row["asset_id"]) for row in rows}
            if not done:
                break
        return [one for one in wanted if one not in done]
