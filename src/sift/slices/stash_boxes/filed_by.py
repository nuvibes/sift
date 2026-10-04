# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which stash-box made each filing a box made, written onto filings from before the column.

Every row a box files carries the box (`box_id` on `asset_people`, `asset_tags` and
`asset_usernames`), so a line about it says "added by FansDB" rather than "added by a stash-box".
Rows filed before the column existed are given theirs once, from the answers applied on their file:

- a file with ONE applied answer: every box row on it is that box's;
- a file with several: each row goes to the first answer, in the order they were applied, whose
  fields name it (read as the take-back reads them, `taken_back.still_said`), because the filing
  keeps the first answer and a later box agreeing never restamps it;
- a username Sift turned out of a Site a box made (`creator_studios.turn_into_username`) is named
  by none of them as a username: it goes to the first answer whose `site` names the Site it was
  turned from, which is the answer that filed it;
- a row no applied answer names stays without a box, and is said as "a stash-box": a box picked
  between two would be a guess dressed as a fact.

Nothing is taken off anything, so there is no Undo; one History line says how many files gained a
box, and one log line the counts. Safe to run twice: a row that has its box is never read again.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace

from sift.kernel.access.catalog import Filed, filed_by_on
from sift.kernel.db import Connection
from sift.kernel.ledger import Actor, record_event
from sift.kernel.log import get_logger
from sift.kernel.migrations import column_exists, table_exists
from sift.kernel.vocabulary import BOXES_RECORDED, VIA_UPDATE, Subject
from sift.slices.stash_boxes.adapter import from_json
from sift.slices.stash_boxes.enrich import IMPORTED
from sift.slices.stash_boxes.matches import _creator_not_a_site
from sift.slices.stash_boxes.taken_back import still_said

log = get_logger(__name__)

#: Every file holding a box's row that names no box.
_OWED = """
SELECT asset_id FROM asset_people WHERE source = 'stash_box' AND box_id IS NULL
UNION SELECT asset_id FROM asset_tags WHERE source = 'stash_box' AND box_id IS NULL
UNION SELECT asset_id FROM asset_usernames WHERE source = 'stash_box' AND box_id IS NULL
ORDER BY asset_id
"""

#: The answers applied on one file, the first applied first, with how the box reads its studios.
_APPLIED = """
SELECT m.box_id AS box_id, m.payload AS payload, b.sites_are AS sites_are
  FROM asset_stash_box_matches m JOIN stash_boxes b ON b.id = m.box_id
 WHERE m.asset_id = ? AND m.state = 'applied'
 ORDER BY m.decided_at, m.box_id
"""

#: One row given its box, by kind, only while it still names none.
_GIVE_THE_BOX: Mapping[str, str] = {
    "person": "UPDATE asset_people SET box_id = ?"
    " WHERE asset_id = ? AND person_id = ? AND source = 'stash_box' AND box_id IS NULL",
    "tag": "UPDATE asset_tags SET box_id = ?"
    " WHERE asset_id = ? AND tag_id = ? AND source = 'stash_box' AND box_id IS NULL",
    "username": "UPDATE asset_usernames SET box_id = ?"
    " WHERE asset_id = ? AND username_id = ? AND source = 'stash_box' AND box_id IS NULL",
}


#: Each username Sift turned out of a Site a box made, with the Site's name, from the move's receipt
#: (`creator_studios.turn_into_username`) where its Undo has not put the Site back. Read once.
_TURNED_FROM = """
SELECT json_extract(payload, '$.username_id') AS username_id,
       json_extract(payload, '$.site_name') AS site
  FROM workbench_decisions
 WHERE verb = 'moved' AND reversed_at IS NULL AND json_valid(payload)
   AND json_extract(payload, '$.kind') = 'turned'
"""


async def _turned_from(connection: Connection) -> dict[str, frozenset[str]]:
    """Each turned username's Site names, folded as the take-back folds them. The receipts' table
    is there to read: its schema leads every other step (`workbench.schema`)."""
    turned: dict[str, set[str]] = {}
    for row in await connection.execute_fetchall(_TURNED_FROM):
        turned.setdefault(str(row["username_id"]), set()).add(str(row["site"]).strip().casefold())
    return {one: frozenset(names) for one, names in turned.items()}


def _fields(payload: str, sites_are: object) -> Mapping[str, object]:
    """One kept answer's fields, read the way the box reads them now (`matches._match`)."""
    records = from_json(payload)
    if not records:
        return {}
    record = records[0]
    if str(sites_are) == "person":
        record = _creator_not_a_site(record)
    return record.fields


def box_of(
    row: Filed,
    answers: Sequence[tuple[str, Mapping[str, object]]],
    turned_from: frozenset[str] = frozenset(),
) -> str | None:
    """The box a row is said to be from: the only answer on its file, or the first that names it;
    a username turned out of a Site, the first whose `site` names that Site (`turned_from`)."""
    if len(answers) == 1:
        return answers[0][0]
    for box_id, fields in answers:
        if still_said([fields]).says(row):
            return box_id
    if row.kind == "username" and turned_from:
        as_the_site = replace(row, names=frozenset(), sites=turned_from)
        for box_id, fields in answers:
            if still_said([fields]).says(as_the_site):
                return box_id
    return None


async def backfill(connection: Connection) -> dict[str, int]:
    """Give every box row from before the column its box. The counts: rows given one, rows left
    without, and files that gained one."""
    counts = {"rows": 0, "left": 0, "files": 0}
    # The catalog's box columns come first at boot (the kernel's schema leads the slices'); a
    # catalog that has not reached them yet has nothing to give a box to.
    boxed = [
        await column_exists(connection, table, "box_id")
        for table in ("asset_people", "asset_tags", "asset_usernames")
    ]
    if not all(boxed) or not await table_exists(connection, "asset_stash_box_matches"):
        log.info("stashbox.filings.boxed", **counts)
        return counts
    owed = list(await connection.execute_fetchall(_OWED))
    turned = await _turned_from(connection) if owed else {}
    first: str | None = None
    for file in owed:
        asset_id = str(file["asset_id"])
        answers = [
            (str(one["box_id"]), _fields(str(one["payload"]), one["sites_are"]))
            for one in await connection.execute_fetchall(_APPLIED, (asset_id,))
        ]
        given = 0
        for row in await filed_by_on(connection, asset_id, IMPORTED):
            if row.box_id is not None:
                continue
            turned_from = (
                turned.get(row.target_id, frozenset()) if row.kind == "username" else frozenset()
            )
            box_id = box_of(row, answers, turned_from) if answers else None
            if box_id is None:
                counts["left"] += 1
                continue
            cursor = await connection.execute(
                _GIVE_THE_BOX[row.kind], (box_id, asset_id, row.target_id)
            )
            given += int(cursor.rowcount or 0)
        counts["rows"] += given
        if given:
            counts["files"] += 1
            first = first or asset_id
    # One line, on the first file given a box, counting them all: what the update did is said once,
    # never once per file.
    if first is not None:
        await record_event(
            connection,
            actor=Actor.sift(VIA_UPDATE),
            verb="added",
            subject=Subject(kind="asset", id=first),
            count=counts["files"],
            payload=json.dumps({BOXES_RECORDED: counts["files"]}),
        )
    log.info("stashbox.filings.boxed", **counts)
    return counts
