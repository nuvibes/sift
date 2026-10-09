# SPDX-License-Identifier: AGPL-3.0-or-later
"""Taking back what a stash-box answer wrote on a file, whole, with one History line and its Undo.

## What a take-back takes off, and what it leaves

An answer applied to a file writes through `AssetWriter.write`, and each writer there has its undo
here or beside it:

| What the answer wrote (`enrich.py`) | What a take-back does |
| --- | --- |
| the six columns (`title` ... `music`) | cleared where the file still holds exactly the value the answer offered and no answer still standing says it (`AssetWriter.take_back_fields`); the Undo writes each back where the field is still empty |
| the addresses (`links`) | the offered ones come off, unless a standing answer says them; the Undo adds them back |
| the people (`_attribute`) | every row marked `stash_box` that no standing answer names comes off |
| the tags (`_tag`) | the same |
| the Site (`_file_under`, the Site's nameless row) | the same |
| the usernames (`_file_under_accounts`) | the same |
| a username's person (`_file_under_accounts`) | goes with the username where the username is a shell; a username still standing keeps it, since nothing records who linked it |
| a person, a username the answer made, and what its box brought (`record_who_invented`, the box links, the starter faces, the creator picture) | removed where nothing else holds it (`catalog.holds_nothing_on`), and put back whole by the Undo |
| the creator mark (`_mark_creator`) | left: a flag on a person that may have been there before, and nothing records which answer set it; a person the answer made goes as a shell, flag and all |
| a Site or a tag the answer made, and what its box brought (other names, links, the box link, the Site's nameless row) | removed where nothing else holds it, as a person is, a network above a removed Site asked the same; put back whole by the Undo |

"Standing" is an answer about the same file still applied, read the way the writer read it. Only
rows marked `stash_box` are ever touched, so a person, a tag or a username somebody filed by hand,
or a folder pass filed, stays whatever an answer said.

## Why by what the file holds, and not by what the answer said

Looked up by the NAMES an answer gave, a take-back misses rows: a box that files a creator as a
studio names a Site ("wrenclips (Storefront)") that the write read as her username on Storefront, and a
name a library holds twice resolves to nobody. So this reads what the file HOLDS from the box and
keeps only what a standing answer names: what a refused answer wrote cannot outlive it, however it
was spelled.

## The repair (stash-box version 19)

Every file with a refused answer and none applied, that still holds a row marked `stash_box`,
loses those rows, and the shells they leave go too: one History line for the library with the
counts, an Undo that puts every row back, a log line. Safe to run twice: the second finds nothing.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from sift.kernel.access.catalog import (
    Filed,
    StillSaid,
    filed_by_on,
    filed_rows,
    forget_kept_on,
    holds_nothing_on,
    keep_for_undo_on,
    kept_for_undo_on,
    put_back_on,
    remove_shell_on,
    take_off_filed_on,
)
from sift.kernel.access.search_index import index_on
from sift.kernel.access.sentences import BY_HAND, PICTURE_ALONE, TOOK_BACK
from sift.kernel.db import Connection, Database
from sift.kernel.ledger import Actor, Object, Reversal, record_event
from sift.kernel.log import get_logger
from sift.kernel.migrations import table_exists
from sift.kernel.vocabulary import VIA_UPDATE
from sift.kernel.vocabulary import Subject as DecisionSubject
from sift.slices.stash_boxes.enrich import IMPORTED

log = get_logger(__name__)

#: The queue whose reverse is a take-back's Undo: the pile of recognised files (`queue.NAME`),
#: spelled here because that module builds on the service, which builds on this one.
RECEIPTS = "tagger"

#: The payload keys a take-back's receipt carries for its Undo, beside `TOOK_BACK` and `boxes`.
ANSWERS = "answers"
FIELDS = "fields"
LINKS = "links"
FILE = "asset_id"
#: Which boxes spoke about each file, by name, kept in the receipt when it is written. A file's
#: page names only these (`sentences.took_back_said`). Kept rather than read when the line is
#: drawn: the file's answers move on afterwards (asked again, applied, an Undo of this very
#: take-back), and a line that read them then would come to name a box it never took back.
BY_FILE = "by_file"


def still_said(answers: Iterable[Mapping[str, object]]) -> StillSaid:
    """What these standing answers' fields say, folded, as `StillSaid` compares it.

    Read as the writer reads them: `site` is a Site's name and `accounts` the usernames. A studio
    the writer read as a username is said both ways, so a row it filed stays whichever reading the
    file holds.
    """
    people: set[str] = set()
    tags: set[str] = set()
    sites: set[str] = set()
    accounts: set[tuple[str, str]] = set()
    for one in answers:
        people |= _names(one.get("people"))
        tags |= _names(one.get("tags"))
        site = str(one.get("site") or "").strip()
        if site:
            sites.add(site.casefold())
        held = one.get("accounts")
        for entry in held if isinstance(held, (list, tuple)) else ():
            if not isinstance(entry, Mapping):
                continue
            where = str(entry.get("site") or "").strip().casefold()
            handle = str(entry.get("handle") or "").strip().casefold()
            if where and handle:
                accounts.add((where, handle))
                sites.add(where)
    return StillSaid(
        people=frozenset(people),
        tags=frozenset(tags),
        accounts=frozenset(accounts),
        sites=frozenset(sites),
    )


def _names(value: object) -> set[str]:
    if not isinstance(value, (list, tuple)):
        return set()
    return {str(one).strip().casefold() for one in value if str(one).strip()}


@dataclass
class TakenBack:
    """What one take-back took: the rows, the shells, and what its Undo keeps."""

    files: list[str] = field(default_factory=list)
    rows: list[Filed] = field(default_factory=list)
    #: Each shell removed, as (kind, id, name).
    removed: list[tuple[str, str, str]] = field(default_factory=list)
    kept: list[dict[str, object]] = field(default_factory=list)
    #: The receipt its History line was written as, once it is.
    receipt_id: str | None = None

    def counts(self) -> dict[str, int]:
        """The counts a History line and a log line say.

        The usernames are counted under `accounts`: the log blanks a value under any key naming a
        username (`log._PERSONAL_KEY_PARTS`), and these are counts, not names.
        """
        return {
            "files": len(self.files),
            "people": sum(1 for one in self.rows if one.kind == "person"),
            "tags": sum(1 for one in self.rows if one.kind == "tag"),
            "accounts": sum(1 for one in self.rows if one.kind == "username"),
            "accounts_removed": sum(1 for one in self.removed if one[0] == "username"),
            "people_removed": sum(1 for one in self.removed if one[0] == "person"),
            "sites_removed": sum(1 for one in self.removed if one[0] == "site"),
            "tags_removed": sum(1 for one in self.removed if one[0] == "tag"),
        }


_USERNAME_OWNER = "SELECT name, person_id FROM usernames WHERE id = ?"
_PERSON_NAME = "SELECT name FROM people WHERE id = ?"
_SITE_NAME = "SELECT name, parent_id FROM sites WHERE id = ?"
_TAG_NAME = "SELECT name FROM tags WHERE id = ?"


async def take_off_on(
    connection: Connection, asset_id: str, still: StillSaid, taken: TakenBack
) -> None:
    """Take off this file every row a stash-box put there that no standing answer still says."""
    rows = [one for one in await filed_by_on(connection, asset_id, IMPORTED) if not still.says(one)]
    went = await take_off_filed_on(connection, rows)
    if went:
        taken.files.append(asset_id)
        taken.rows.extend(went)


async def clear_shells_on(connection: Connection, taken: TakenBack) -> None:
    """Remove each username, person, Site and tag these rows leave holding nothing, keeping them
    for the Undo.

    The usernames first: a username still standing holds the person it names and the Site it is
    on, and one that goes makes both a question too. A Site that goes makes the network above it a
    question. What the Undo puts back is kept in the order it is written back: the Sites (a network
    before what is under it), the tags, the people, the usernames that may name them, the filings.
    """
    people = dict.fromkeys(one.target_id for one in taken.rows if one.kind == "person")
    kept_usernames: list[dict[str, object]] = []
    for username_id in dict.fromkeys(
        one.target_id for one in taken.rows if one.kind == "username" and one.names
    ):
        row = await (await connection.execute(_USERNAME_OWNER, (username_id,))).fetchone()
        if row is None or not await holds_nothing_on(connection, "username", username_id):
            continue
        if row["person_id"] is not None:
            people.setdefault(str(row["person_id"]), None)
        kept_usernames.extend(await remove_shell_on(connection, "username", username_id))
        taken.removed.append(("username", username_id, str(row["name"])))
    kept_people: list[dict[str, object]] = []
    for person_id in people:
        row = await (await connection.execute(_PERSON_NAME, (person_id,))).fetchone()
        if row is None or not await holds_nothing_on(connection, "person", person_id):
            continue
        kept_people.extend(await remove_shell_on(connection, "person", person_id))
        taken.removed.append(("person", person_id, str(row["name"])))
    kept_sites = await _clear_sites_on(connection, taken)
    kept_tags: list[dict[str, object]] = []
    for tag_id in dict.fromkeys(one.target_id for one in taken.rows if one.kind == "tag"):
        row = await (await connection.execute(_TAG_NAME, (tag_id,))).fetchone()
        if row is None or not await holds_nothing_on(connection, "tag", tag_id):
            continue
        kept_tags.extend(await remove_shell_on(connection, "tag", tag_id))
        taken.removed.append(("tag", tag_id, str(row["name"])))
    networks_first = [one for chunk in reversed(kept_sites) for one in chunk]
    taken.kept = [
        *networks_first,
        *kept_tags,
        *kept_people,
        *kept_usernames,
        *filed_rows(taken.rows),
    ]


async def _clear_sites_on(
    connection: Connection, taken: TakenBack
) -> list[list[dict[str, object]]]:
    """Remove the Sites these usernames leave holding nothing, then the networks above them."""
    sites = list(
        dict.fromkeys(
            one.site_id for one in taken.rows if one.kind == "username" and one.site_id is not None
        )
    )
    kept_sites: list[list[dict[str, object]]] = []
    while sites:
        site_id = sites.pop(0)
        row = await (await connection.execute(_SITE_NAME, (site_id,))).fetchone()
        if row is None or not await holds_nothing_on(connection, "site", site_id):
            continue
        kept_sites.append(await remove_shell_on(connection, "site", site_id))
        taken.removed.append(("site", site_id, str(row["name"])))
        if row["parent_id"] is not None and str(row["parent_id"]) not in sites:
            sites.append(str(row["parent_id"]))
    return kept_sites


def _and(items: Sequence[str]) -> str:
    """ "A", "A and B", "A, B and C"."""
    return (
        items[0]
        if len(items) == 1
        else ", ".join(items[:-1]) + f" and {items[-1]}"
        if items
        else ""
    )


def _words(boxes: Sequence[str], taken: TakenBack) -> tuple[str, str]:
    """The receipt's title and detail. Composed now, as every receipt's are."""
    counts = taken.counts()
    which = _and(list(boxes))
    files = "1 file" if counts["files"] == 1 else f"{counts['files']:,} files"
    title = f"Took back what {which or 'a stash-box'} said about {files}"
    parts: list[str] = []
    for key, one, many in (
        ("people", "person", "people"),
        ("tags", "tag", "tags"),
        ("accounts", "username", "usernames"),
    ):
        if counts[key]:
            parts.append(f"{counts[key]:,} {one if counts[key] == 1 else many}")
    detail = f"Took off {_and(parts)}. " if parts else ""
    gone: list[str] = []
    for key, one, many in (
        ("accounts_removed", "username", "usernames"),
        ("people_removed", "person", "people"),
        ("sites_removed", "Site", "Sites"),
        ("tags_removed", "tag", "tags"),
    ):
        if counts[key]:
            gone.append(f"{counts[key]:,} {one if counts[key] == 1 else many}")
    if gone:
        detail += f"Removed {_and(gone)} that nothing else held. "
    return title, detail + "Undo puts them all back."


async def record_on(
    connection: Connection,
    taken: TakenBack,
    *,
    boxes: Sequence[tuple[str, str]],
    reason: str,
    actor: Actor,
    extra: Mapping[str, object] | None = None,
) -> str:
    """The one History line for a take-back, as a receipt, with what its Undo keeps beside it.

    About every file it took rows off and every shell it removed, so the line is on each of their
    pages; said the way every take-back of a stash-box's writes is said (`sentences.TOOK_BACK`),
    with the boxes named and the counts in the payload. `extra` carries `by_file` (`BY_FILE`):
    which boxes spoke about each file, so a file's own page names only those.
    """
    names = [name for _, name in boxes]
    payload: dict[str, object] = {TOOK_BACK: reason, "boxes": names, **taken.counts()}
    payload.update(extra or {})
    title, detail = _words(names, taken)
    subjects = [DecisionSubject(kind="asset", id=one) for one in taken.files]
    subjects += [DecisionSubject(kind=kind, id=one, name=name) for kind, one, name in taken.removed]  # type: ignore[arg-type]
    receipt_id = await record_event(
        connection,
        actor=actor,
        verb="removed",
        subject=subjects,
        object=Object(kind="box", id=boxes[0][0], name=boxes[0][1]) if boxes else None,
        payload=json.dumps(payload),
        receipt=Reversal(queue=RECEIPTS, title=title, detail=detail),
    )
    await keep_for_undo_on(connection, receipt_id, taken.kept)
    taken.receipt_id = receipt_id
    return receipt_id


# --- One answer, taken back --------------------------------------------------------------------

_SETTLE_APPLIED = """
UPDATE asset_stash_box_matches SET state = ?, decided_at = ?
 WHERE asset_id = ? AND box_id = ? AND state = 'applied'
RETURNING decided_at
"""
_DECIDED_AT = (
    "SELECT decided_at FROM asset_stash_box_matches"
    " WHERE asset_id = ? AND box_id = ? AND state = 'applied'"
)
_PUT_ANSWER_BACK = """
UPDATE asset_stash_box_matches SET state = ?, decided_at = ?
 WHERE asset_id = ? AND box_id = ? AND state = ?
"""


#: The same, for an answer a re-ask replaced: its record and its stored reading go back with it.
_PUT_ANSWER_BACK_AS_IT_WAS = """
UPDATE asset_stash_box_matches SET state = ?, decided_at = ?, remote_id = ?, payload = ?
 WHERE asset_id = ? AND box_id = ? AND state = ?
"""


@dataclass(frozen=True, slots=True)
class AppliedAnswer:
    """One applied answer to take back: the file, the box, and the box's name for the line."""

    asset_id: str
    box_id: str
    box_name: str


async def take_back_answer_on(
    connection: Connection,
    answer: AppliedAnswer,
    *,
    still: StillSaid,
    now: int,
    reopen: bool,
    actor: Actor,
    cleared: Mapping[str, object] | None = None,
    links: Sequence[str] = (),
    was: tuple[str, str] | None = None,
) -> TakenBack | None:
    """Take back one applied answer on the caller's connection. None where it was not applied.

    The answer is refused, or put back among the questions (`reopen`: the Undo of the press that
    applied it, or the box asked again and answering differently); what it filed comes off, its
    shells go, and the file's History gains one line with an Undo that puts the rows, the fields
    `cleared` and the addresses `links` (taken off by the writer just before) and the answer's
    state back. `was` is the answer's record and stored reading where a re-ask is about to write
    a new one over them, so the Undo puts back the answer that was applied and not the question
    that replaced it. The search index is told in the same write.
    """
    held = await (
        await connection.execute(_DECIDED_AT, (answer.asset_id, answer.box_id))
    ).fetchone()
    if held is None:
        return None
    state = "waiting" if reopen else "refused"
    await connection.execute_fetchall(
        _SETTLE_APPLIED,
        (state, None if reopen else now, answer.asset_id, answer.box_id),
    )
    taken = TakenBack()
    await take_off_on(connection, answer.asset_id, still, taken)
    await clear_shells_on(connection, taken)
    if answer.asset_id not in taken.files:
        taken.files.append(answer.asset_id)
    await record_on(
        connection,
        taken,
        boxes=[(answer.box_id, answer.box_name)],
        reason=BY_HAND,
        actor=actor,
        extra={
            FILE: answer.asset_id,
            ANSWERS: [
                [answer.asset_id, answer.box_id, "applied", held["decided_at"], state]
                + ([] if was is None else list(was))
            ],
            FIELDS: dict(cleared or {}),
            LINKS: list(links),
            BY_FILE: {answer.asset_id: [answer.box_name]},
        },
    )
    await index_on(connection, [answer.asset_id])
    log.info("stashbox.answer.taken_back", reopened=reopen, **taken.counts())
    return taken


@dataclass(frozen=True, slots=True)
class PutBack:
    """What an Undo put back, and what is left for the file's writer: its fields and addresses."""

    rows: int
    asset_id: str | None
    fields: Mapping[str, object]
    links: Sequence[str]


async def put_back_answers_on(
    connection: Connection, receipt_id: str, payload: Mapping[str, object]
) -> PutBack:
    """Undo a take-back on the caller's connection, from its receipt: every row it kept, and each
    answer's state where nothing has settled it differently since. The fields and addresses are
    answered for the file's writer, which is the only thing that writes them."""
    kept = await kept_for_undo_on(connection, receipt_id)
    rows = await put_back_on(connection, kept)
    await forget_kept_on(connection, receipt_id)
    raw = payload.get(ANSWERS)
    for one in raw if isinstance(raw, list) else ():
        if isinstance(one, list) and len(one) == 5:
            asset_id, box_id, before, decided_at, after = one
            await connection.execute(
                _PUT_ANSWER_BACK, (before, decided_at, asset_id, box_id, after)
            )
        elif isinstance(one, list) and len(one) == 7:
            asset_id, box_id, before, decided_at, after, remote_id, answer = one
            await connection.execute(
                _PUT_ANSWER_BACK_AS_IT_WAS,
                (before, decided_at, remote_id, answer, asset_id, box_id, after),
            )
    files: set[str] = set()
    for one in kept:
        row = one.get("row")
        if isinstance(row, Mapping) and row.get("asset_id"):
            files.add(str(row["asset_id"]))
    await index_on(connection, sorted(files))
    fields = payload.get(FIELDS)
    links = payload.get(LINKS)
    asset_id = payload.get(FILE)
    return PutBack(
        rows=rows,
        asset_id=str(asset_id) if isinstance(asset_id, str) and asset_id else None,
        fields=dict(fields) if isinstance(fields, Mapping) else {},
        links=[str(one) for one in links] if isinstance(links, list) else [],
    )


# --- What a refused answer left behind, repaired once (stash-box version 19) --------------------

#: Every file with a refused answer and none applied that still holds a row a box filed. From the
#: three join tables' rows marked `stash_box` (two of them sought on their source index), each kept
#: only where its file qualifies.
_OWED = """
SELECT x.asset_id AS asset_id FROM (
  SELECT asset_id FROM asset_people WHERE source = 'stash_box'
  UNION SELECT asset_id FROM asset_tags WHERE source = 'stash_box'
  UNION SELECT asset_id FROM asset_usernames WHERE source = 'stash_box') x
 WHERE EXISTS (SELECT 1 FROM asset_stash_box_matches m
                WHERE m.asset_id = x.asset_id AND m.state = 'refused')
   AND NOT EXISTS (SELECT 1 FROM asset_stash_box_matches m
                    WHERE m.asset_id = x.asset_id AND m.state = 'applied')
 ORDER BY x.asset_id
"""

#: The same, for an applied answer a box asked again turned back into a question without taking
#: back what it wrote (stash-box version 20): a waiting answer its box has found more than once.
#: The second find is the re-ask's mark, so rows a Stash import filed before it had its own word
#: (`stash_library`), on a file whose only answer is a first question, are not touched.
_OWED_BY_A_REASK = """
SELECT x.asset_id AS asset_id FROM (
  SELECT asset_id FROM asset_people WHERE source = 'stash_box'
  UNION SELECT asset_id FROM asset_tags WHERE source = 'stash_box'
  UNION SELECT asset_id FROM asset_usernames WHERE source = 'stash_box') x
 WHERE EXISTS (SELECT 1 FROM asset_stash_box_matches m
                WHERE m.asset_id = x.asset_id AND m.state = 'waiting'
                  AND (SELECT COUNT(*) FROM stash_box_scans s WHERE s.asset_id = m.asset_id
                        AND s.box_id = m.box_id AND s.found = 1) > 1)
   AND NOT EXISTS (SELECT 1 FROM asset_stash_box_matches m
                    WHERE m.asset_id = x.asset_id AND m.state = 'applied')
 ORDER BY x.asset_id
"""

#: The boxes whose answers in that state those were, by name, and whether any was certain (an
#: exact match somebody refused, which no line may call a picture match).
_REFUSED_BY = """
SELECT b.id AS id, b.name AS name, MAX(m.grade = 'certain') AS certain
  FROM asset_stash_box_matches m JOIN stash_boxes b ON b.id = m.box_id
 WHERE m.state = ? AND m.asset_id IN (SELECT value FROM json_each(?))
 GROUP BY b.id, b.name ORDER BY b.name, b.id
"""

#: Each of those files with the boxes whose answer in that state it holds, by name.
_REFUSED_ON_EACH = """
SELECT m.asset_id AS asset_id, b.name AS name
  FROM asset_stash_box_matches m JOIN stash_boxes b ON b.id = m.box_id
 WHERE m.state = ? AND m.asset_id IN (SELECT value FROM json_each(?))
 ORDER BY m.asset_id, b.name, b.id
"""

#: THE GATE: how many rows a box filed are on files with an answer and none of them applied: every
#: one refused, or a question again (a box asked again turns an applied answer back into one, and
#: a question has written nothing). Nought on a library where a refusal and a re-ask each take
#: back what the answer wrote.
LEFT_BY_A_REFUSAL = """
SELECT COUNT(*) AS n FROM (
  SELECT asset_id FROM asset_people WHERE source = 'stash_box'
  UNION ALL SELECT asset_id FROM asset_tags WHERE source = 'stash_box'
  UNION ALL SELECT asset_id FROM asset_usernames WHERE source = 'stash_box') x
 WHERE EXISTS (SELECT 1 FROM asset_stash_box_matches m
                WHERE m.asset_id = x.asset_id AND m.state IN ('refused', 'waiting'))
   AND NOT EXISTS (SELECT 1 FROM asset_stash_box_matches m
                    WHERE m.asset_id = x.asset_id AND m.state = 'applied')
"""


async def left_by_a_refusal(database: Database) -> int:
    """How many rows a box filed still stand on a file whose answers were refused. See the gate."""
    row = await database.fetch_one(LEFT_BY_A_REFUSAL)
    return 0 if row is None else int(row["n"])


async def repair(connection: Connection, *, asked_again: bool = False) -> dict[str, int]:
    """Stash-box version 19: what refused answers left on their files, taken back once; with
    `asked_again`, version 20: what applied answers a re-ask reopened left (`_OWED_BY_A_REASK`).

    One History line for the library, said by Sift as it updated, with an Undo that puts every row
    back, and a log line with the counts. Nothing is recorded where nothing was owed, or where the
    ledger's table is not there yet (a library that has never written a decision has applied no
    answer either), so a second run finds nothing and says nothing.
    """
    event = "stashbox.reasked.repaired" if asked_again else "stashbox.refused.repaired"
    state = "waiting" if asked_again else "refused"
    taken = TakenBack()
    if not await table_exists(connection, "workbench_decisions"):
        log.info(event, **taken.counts())
        return taken.counts()
    owing = _OWED_BY_A_REASK if asked_again else _OWED
    owed = [str(row["asset_id"]) for row in await connection.execute_fetchall(owing)]
    nobody = StillSaid()
    for asset_id in owed:
        await take_off_on(connection, asset_id, nobody, taken)
    if not taken.rows:
        log.info(event, **taken.counts())
        return taken.counts()
    await clear_shells_on(connection, taken)
    files = json.dumps(taken.files)
    boxes = list(await connection.execute_fetchall(_REFUSED_BY, (state, files)))
    certain = any(int(row["certain"] or 0) for row in boxes)
    reason = BY_HAND if asked_again or certain else PICTURE_ALONE
    by_file: dict[str, list[str]] = {}
    for row in await connection.execute_fetchall(_REFUSED_ON_EACH, (state, files)):
        by_file.setdefault(str(row["asset_id"]), []).append(str(row["name"]))
    await record_on(
        connection,
        taken,
        boxes=[(str(row["id"]), str(row["name"])) for row in boxes],
        reason=reason,
        actor=Actor.sift(VIA_UPDATE),
        extra={BY_FILE: by_file},
    )
    await index_on(connection, taken.files)
    log.info(event, **taken.counts())
    return taken.counts()
