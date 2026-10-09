# SPDX-License-Identifier: AGPL-3.0-or-later
"""What happened to a site, a tag, a shelf, a Photo Set or a song, in order: one private shape over
five subjects."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from sift.kernel.access import sentences as say
from sift.kernel.access.history import (
    DEFAULT_LIMIT,
    MADE_BY_BOX,
    MAX_LIMIT,
    Actor,
    Detail,
    Event,
    Link,
    _decision_events,
    _names_of,
    _via_of_source,
    _Who,
    actor_of_source,
    by_of,
    files_of_decisions,
    files_of_filing,
    kept_events,
    ledger_events,
    maker_of,
    one_line_per_kept,
    ordered,
    runs_not_drawn,
    share_makers,
    stash_box_tables_in,
)
from sift.kernel.access.history_boxes import BOX_OF_A_ROW, thing_linked, unshown_said
from sift.kernel.access.history_events import NOTHING_HIDDEN, verdict_of
from sift.kernel.access.history_folds import (
    NO_RECEIPT,
    RECEIPT_OF_A_FILING,
    RECEIPT_OF_A_TAGGING,
    Counted,
    counted_apart_from_receipts,
    drawn_receipts,
)
from sift.kernel.access.history_sources import _folders_seen
from sift.kernel.access.history_usernames import site_username_lines
from sift.kernel.access.repository import Repository
from sift.kernel.access.sentences import (
    SIFT,
    VANTAGE_ENTITY,
    Line,
    Piece,
    files,
)
from sift.kernel.access.viewer import Viewer
from sift.kernel.access.visibility import FILE_SEEN_BY_VIEWER, seen_by
from sift.kernel.access.worded import decided_said, lines_under
from sift.kernel.db import Database, Row
from sift.kernel.records import Subject
from sift.kernel.sql_splice import splice
from sift.kernel.vocabulary import LEDGER_QUEUE
from sift.kernel.when import day_number
from sift.kernel.where import folder_said

if TYPE_CHECKING:  # pragma: no cover (the registry type, for the annotation only)
    from sift.kernel.workbench import Workbench

#: Tables a feature owns, which a process that never imported it does not have.
_FEATURE_TABLES = (
    "workbench_decisions",
    "workbench_decision_subjects",
    "tag_stash_box_links",
    "site_stash_box_links",
    "stash_boxes",
    # So a line names its box (`stash_box_tables_in`).
    "asset_stash_box_matches",
    "stash_box_kept",
)

_TABLES = "SELECT name FROM sqlite_master WHERE type = 'table'"


NAMES_A_FILE = """
   AND (:admin = 1 OR d.object_kind = 'asset' OR EXISTS (
         SELECT 1 FROM workbench_decision_subjects f
          WHERE f.decision_id = d.id AND f.kind = 'asset'))"""

#: Every bulk judgement that named one of these things, newest last. Never a ledger event
#: (`LEDGER_QUEUE`), and only a receipt whose every file this viewer may be shown (see
#: `history_person._DECIDED`).
_DECIDED = splice(
    """
SELECT d.id AS id, d.title AS title, d.queue AS queue, d.user_id AS user_id,
       d.actor_kind AS actor_kind, d.actor_id AS actor_id,
       d.decided_at AS decided_at, d.reversed_at AS reversed_at, d.verb AS verb,
       d.object_kind AS object_kind, d.object_id AS object_id, d.payload AS payload,
       d.detail AS detail
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = :kind AND s.subject_id = :subject AND d.queue <> :ledger
{{NAMES_A_FILE}}
{{NOTHING_HIDDEN}}
 ORDER BY d.decided_at ASC, d.id ASC
""",
    NAMES_A_FILE=NAMES_A_FILE,
    NOTHING_HIDDEN=NOTHING_HIDDEN,
)

#: Who did it, the name beside it, and the sentence: what `Event` takes.
_Said = Callable[[str | None, Piece, int, str | None], "tuple[Actor, str | None, Line]"]

#: `created_by_act` is the act whose copy a Sift-made tag came from (`sentences.FROM_ACT`).
_TAG = (
    "SELECT name, created_at, created_by_kind, created_by_via, created_by_user_id,"
    " created_by_box_id, created_by_act"
    " FROM tags WHERE id = ?"
)
_COLLECTION = "SELECT name, owner_id, created_at FROM collections WHERE id = ?"
_PHOTO_SET = (
    "SELECT s.name AS name, s.origin AS origin, s.created_at AS created_at,"
    " s.folder_id AS folder_id, f.root_id AS folder_root, f.rel_path AS folder_path,"
    " f.name AS folder_name"
    " FROM photo_sets s LEFT JOIN folders f ON f.id = s.folder_id WHERE s.id = ?"
)
_SONG = (
    "SELECT name, created_at, created_by_kind, created_by_via, created_by_user_id"
    " FROM songs WHERE id = ?"
)

# Files put in a shelf or a Photo Set that the ledger did not record, read off `added_at`, only
# those the viewer may see.
_SHELF_ADDITIONS = splice(
    """
SELECT link.asset_id AS id, COALESCE(NULLIF(a.title, ''), a.original_filename) AS name,
       link.added_at AS at
  FROM collection_items link
  JOIN assets a ON a.id = link.asset_id
 WHERE link.collection_id = :subject AND {{SEEN}}
   AND NOT EXISTS (SELECT 1 FROM workbench_decision_subjects s
                     JOIN workbench_decisions d ON d.id = s.decision_id
                    WHERE s.kind = 'asset' AND s.subject_id = link.asset_id
                      AND d.verb = 'linked' AND d.object_kind = 'collection'
                      AND d.object_id = link.collection_id)
 ORDER BY link.added_at, link.asset_id
""",
    SEEN=FILE_SEEN_BY_VIEWER,
)
_SET_ADDITIONS = splice(
    """
SELECT link.asset_id AS id, COALESCE(NULLIF(a.title, ''), a.original_filename) AS name,
       link.added_at AS at
  FROM photo_set_items link
  JOIN assets a ON a.id = link.asset_id
 WHERE link.photo_set_id = :subject AND {{SEEN}}
   AND NOT EXISTS (SELECT 1 FROM workbench_decision_subjects s
                     JOIN workbench_decisions d ON d.id = s.decision_id
                    WHERE s.kind = 'asset' AND s.subject_id = link.asset_id
                      AND d.verb = 'linked' AND d.object_kind = 'photo_set'
                      AND d.object_id = link.photo_set_id)
 ORDER BY link.added_at, link.asset_id
""",
    SEEN=FILE_SEEN_BY_VIEWER,
)

# Files a song was named on that the ledger did not record against the song, read off `song_files`.
# Only files the viewer may see.
_SONG_ADDITIONS = splice(
    """
SELECT link.asset_id AS id, COALESCE(NULLIF(a.title, ''), a.original_filename) AS name,
       link.added_at AS at, link.source AS source,
       CASE WHEN EXISTS (SELECT 1 FROM viewer_assets so
                          WHERE so.asset_id = link.from_asset_id AND so.user_id = :viewer
                            AND (:reveal = 1 OR so.concealed = 0))
            THEN link.from_asset_id END AS origin,
       COALESCE(NULLIF(o.title, ''), o.original_filename) AS origin_name
  FROM song_files link
  JOIN assets a ON a.id = link.asset_id
  LEFT JOIN assets o ON o.id = link.from_asset_id
 WHERE link.song_id = :subject AND {{SEEN}}
   AND NOT EXISTS (SELECT 1 FROM workbench_decision_subjects s
                     JOIN workbench_decisions d ON d.id = s.decision_id
                    WHERE s.kind = 'asset' AND s.subject_id = link.asset_id
                      AND d.verb = 'linked' AND d.object_kind = 'song'
                      AND d.object_id = link.song_id)
 ORDER BY link.added_at, link.asset_id
""",
    SEEN=FILE_SEEN_BY_VIEWER,
)

#: The most files one day's addition names before it counts them.
_ADDITIONS_NAMED = 50

#: How many of a counted day's files its "Show each" lists (`history_events.FEED_FOLD_SHOWN`).
_ADDITIONS_LISTED = 100
#: Read even with no moment: whether the row exists decides between an empty history and none.
_SITE = (
    "SELECT name, created_at, created_by_kind, created_by_via, created_by_user_id,"
    " created_by_box_id"
    " FROM sites WHERE id = ?"
)

#: Every file this tag was put on, grouped by what decided it and by the machine's local day; only
#: files this viewer may be shown.
_TAG_FILES_SQL = """
SELECT link.source AS source,
       {{BOX}} AS box,
       unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 AS day,
       {{RECEIPT}} AS receipt,
       COUNT(*) AS files,
       MAX(link.decided_at) AS at
  FROM asset_tags link
 WHERE link.tag_id = :subject AND {{SEEN}}
 GROUP BY source, box, day, receipt
"""
_TAG_FILES = splice(
    _TAG_FILES_SQL, BOX=BOX_OF_A_ROW, SEEN=FILE_SEEN_BY_VIEWER, RECEIPT=RECEIPT_OF_A_TAGGING
)
_TAG_FILES_NO_LEDGER = splice(
    _TAG_FILES_SQL, BOX=BOX_OF_A_ROW, SEEN=FILE_SEEN_BY_VIEWER, RECEIPT=NO_RECEIPT
)

#: Without the stash-box tables: NULL where the box would be.
_TAG_FILES_NO_BOX_SQL = """
SELECT link.source AS source,
       NULL AS box,
       unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 AS day,
       {{RECEIPT}} AS receipt,
       COUNT(*) AS files,
       MAX(link.decided_at) AS at
  FROM asset_tags link
 WHERE link.tag_id = :subject AND {{SEEN}}
 GROUP BY source, box, day, receipt
"""
_TAG_FILES_NO_BOX = splice(
    _TAG_FILES_NO_BOX_SQL, SEEN=FILE_SEEN_BY_VIEWER, RECEIPT=RECEIPT_OF_A_TAGGING
)
_TAG_FILES_NO_BOX_NO_LEDGER = splice(
    _TAG_FILES_NO_BOX_SQL, SEEN=FILE_SEEN_BY_VIEWER, RECEIPT=NO_RECEIPT
)

# A filing that is a download's own act, as its row says: the download's line already says it.
_NOT_A_DOWNLOAD = "(link.source IS NULL OR link.source <> 'download')"

#: Every filing made under this site, through its usernames.
_SITE_FILES_SQL = """
SELECT link.source AS source,
       {{BOX}} AS box,
       unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 AS day,
       {{RECEIPT}} AS receipt,
       COUNT(*) AS files,
       MAX(link.decided_at) AS at
  FROM usernames ac
  JOIN asset_usernames link ON link.username_id = ac.id
 WHERE ac.site_id = :subject AND {{SEEN}} AND {{DOWNLOADS}}
 GROUP BY source, box, day, receipt
"""
_SITE_FILES = splice(
    _SITE_FILES_SQL,
    BOX=BOX_OF_A_ROW,
    SEEN=FILE_SEEN_BY_VIEWER,
    DOWNLOADS=_NOT_A_DOWNLOAD,
    RECEIPT=RECEIPT_OF_A_FILING,
)
_SITE_FILES_NO_LEDGER = splice(
    _SITE_FILES_SQL,
    BOX=BOX_OF_A_ROW,
    SEEN=FILE_SEEN_BY_VIEWER,
    DOWNLOADS=_NOT_A_DOWNLOAD,
    RECEIPT=NO_RECEIPT,
)

#: Without the stash-box tables: NULL where the box would be.
_SITE_FILES_NO_BOX_SQL = """
SELECT link.source AS source,
       NULL AS box,
       unixepoch(link.decided_at, 'unixepoch', 'localtime') / 86400 AS day,
       {{RECEIPT}} AS receipt,
       COUNT(*) AS files,
       MAX(link.decided_at) AS at
  FROM usernames ac
  JOIN asset_usernames link ON link.username_id = ac.id
 WHERE ac.site_id = :subject AND {{SEEN}} AND {{DOWNLOADS}}
 GROUP BY source, box, day, receipt
"""
_SITE_FILES_NO_BOX = splice(
    _SITE_FILES_NO_BOX_SQL,
    SEEN=FILE_SEEN_BY_VIEWER,
    DOWNLOADS=_NOT_A_DOWNLOAD,
    RECEIPT=RECEIPT_OF_A_FILING,
)
_SITE_FILES_NO_BOX_NO_LEDGER = splice(
    _SITE_FILES_NO_BOX_SQL, SEEN=FILE_SEEN_BY_VIEWER, DOWNLOADS=_NOT_A_DOWNLOAD, RECEIPT=NO_RECEIPT
)

#: Grants on the thing itself, the object type bound so the admin-only rule is written once.
_GRANTS = """
SELECT u.username AS username, g.effect AS effect, g.created_at AS created_at,
       g.subject_user_id AS user_id
  FROM acl_grants g
  JOIN users u ON u.id = g.subject_user_id
 WHERE g.object_type = ? AND g.object_id = ?
 ORDER BY g.id ASC
"""


def _tagged_sentence(
    source: str | None, counted: Piece, count: int, box: str | None = None
) -> tuple[Actor, str | None, Line]:
    """Who put this tag on a run of files; an unknown source word is still Sift."""
    actor, name = actor_of_source(source, box)
    return actor, name, say.put_on(by_of(actor, name), source, counted, count)


def _filed_sentence(
    source: str | None, counted: Piece, count: int, box: str | None = None
) -> tuple[Actor, str | None, Line]:
    """Who filed a run of files under this site."""
    actor, name = actor_of_source(source, box)
    return actor, name, say.filed_under(by_of(actor, name), source, counted, count)


#: How a Photo Set came to be, by `photo_sets.origin`, and who that makes the actor.
_ORIGINS: dict[str, tuple[Actor, str]] = {
    "manual": (Actor.SOMEBODY, ""),
    "download": (Actor.SIFT, " from a download"),
    "folder": (Actor.SIFT, " from a folder"),
    "archive": (Actor.SIFT, " from an archive"),
    # A post read out of file names says names, not a download.
    "filename": (Actor.SIFT, " from the files' names"),
    "shoot": (Actor.SIFT, " from a shoot"),
}


async def _from_the_folder(access: Repository, viewer: Viewer, found: Row) -> Line | None:
    """ " from the folder Beach", or None where the folder is gone or not fully visible
    (`kernel.where`)."""
    if found["folder_name"] is None:
        return None
    path, name = str(found["folder_path"] or ""), str(found["folder_name"])
    seen = await _folders_seen(access, viewer)
    if folder_said(path, seen=seen.get(str(found["folder_root"]), frozenset())) != path:
        return None
    return say.said(" from the folder ", say.folder_named(str(found["folder_id"]), name))


def _counted_events(
    rows: Sequence[Counted], kind: str, said_as: _Said, *, subject: str, parameter: str
) -> list[Event]:
    """One event per group of a count by source and day, its number linking to exactly the files it
    counted (`files_of_filing`)."""
    events: list[Event] = []
    for row in rows:
        source = None if row["source"] is None else str(row["source"])
        count = int(row["files"])
        box = None if row["box"] is None else str(row["box"])
        counted = say.thing(
            "files", subject, files(count), href=files_of_filing(parameter, subject, row)
        )
        actor, actor_name, line = said_as(source, counted, count, box)
        events.append(
            Event(
                at=None if row["at"] is None else int(row["at"]),
                actor=actor,
                actor_name=actor_name,
                kind=kind,
                pieces=line,
                via=_via_of_source(source),
            )
        )
    return events


#: How long after a task made a Photo Set a file going in is still that task's filling
#: (`history_events.FEED_FOLD_GAP`).
_FILLED_BY_THE_TASK = 60


def _addition_events(
    rows: Sequence[Row],
    *,
    actor: Actor = Actor.SOMEBODY,
    how: say.Part = "",
    until: int | None = None,
) -> list[Event]:
    """One line per day of files put in a shelf or Photo Set that the record did not say. Only a
    task's own filling names an actor."""
    days: dict[tuple[bool, int | None], list[Row]] = {}
    for row in rows:
        at = None if row["at"] is None else int(row["at"])
        theirs = until is not None and at is not None and at <= until
        days.setdefault((theirs, None if at is None else day_number(at)), []).append(row)
    events: list[Event] = []
    for (theirs, _day), day_rows in days.items():
        who = actor if theirs else Actor.SOMEBODY
        count = len(day_rows)
        what: Line = (
            say.listed(
                [(say.thing("asset", str(row["id"]), str(row["name"] or "")),) for row in day_rows]
            )
            if count <= _ADDITIONS_NAMED
            else say.said(files(count))
        )
        moments = [int(row["at"]) for row in day_rows if row["at"] is not None]
        # A counted day opens to its newest files under the line.
        listed = tuple(
            Link(kind="asset", id=str(row["id"]), name=str(row["name"] or ""))
            for row in reversed(day_rows[-_ADDITIONS_LISTED:])
        )
        heading = (
            files(count)
            if len(listed) == count
            else f"The newest {len(listed):,} of {files(count)}"
        )
        events.append(
            Event(
                at=max(moments) if moments else None,
                actor=who,
                actor_name=SIFT if who is Actor.SIFT else None,
                kind="added",
                pieces=say.members_added(by_of(who, None), what, count, how if theirs else ""),
                detail=(
                    (Detail(kind="asset", words=heading, links=listed),)
                    if count > _ADDITIONS_NAMED
                    else ()
                ),
            )
        )
    return events


async def _grant_events(
    database: Database, viewer: Viewer, object_type: str, object_id: str
) -> list[Event]:
    """Who this was shared with or kept from. Admin only: the caller decides."""
    granted = list(await database.fetch_all(_GRANTS, (object_type, object_id)))
    makers = await share_makers(database, viewer, object_type, object_id, granted)
    return [
        Event(
            at=int(row["created_at"]),
            actor=maker,
            actor_name=maker_name,
            kind="shared",
            pieces=say.shared_with(
                by_of(maker, maker_name),
                str(row["username"]),
                share=str(row["effect"]) == "share",
                here="it",
            ),
        )
        for row, (maker, maker_name) in zip(granted, makers, strict=True)
    ]


async def _who_made(
    database: Database, viewer: Viewer, row: Row, *, here: set[str]
) -> tuple[Actor, str | None]:
    """The maker of one row, with the box's name looked up only when the row names a box."""
    box = row["created_by_box_id"]
    name = None
    if box is not None and "stash_boxes" in here:
        found = await database.fetch_one(MADE_BY_BOX, (str(box),))
        name = None if found is None else str(found["name"])
    return maker_of(row, viewer, name)


def _made_event(
    kind: str,
    subject_id: str,
    name: str,
    at: int | None,
    actor: Actor,
    how: say.Part = "",
    actor_name: str | None = None,
    *,
    created: bool = False,
    via: str | None = None,
    act: str | None = None,
) -> Event:
    """The arrival, with the thing itself linked."""
    made = say.entity_created if created else say.entity_added
    return Event(
        at=at,
        actor=actor,
        actor_name=actor_name or (SIFT if actor is Actor.SIFT else None),
        kind="added",
        pieces=made(by_of(actor, actor_name), say.thing(kind, subject_id, name), how),
        via=via,
        how=act,
    )


async def _ledger_events(
    database: Database,
    viewer: Viewer,
    kind: str,
    entity_id: str,
    kept: int,
    linked: Sequence[Event] = (),
    *,
    name_now: str | None = None,
) -> list[Event]:
    """What the ledger recorded about one of these things (`history.ledger_events`), with the link
    table's box lines beside it."""
    ledger = await ledger_events(
        database,
        viewer,
        here=VANTAGE_ENTITY,
        kind=kind,
        subject_id=entity_id,
        grants_as=kind,
        limit=kept,
        box_said=bool(linked),
        name_now=name_now,
    )
    return [*runs_not_drawn(linked, ledger), *ledger]


async def _decided_events(
    database: Database,
    viewer: Viewer,
    kind: str,
    entity_id: str,
    here: set[str],
    final_queues: Sequence[str],
    bench: Workbench | None = None,
) -> list[Event]:
    """The receipts a queue wrote about one of these things, with their Undo and reversal: the
    person thread's body."""
    if not {"workbench_decisions", "workbench_decision_subjects"} <= here:
        return []
    decided = list(
        await database.fetch_all(
            _DECIDED,
            {**verdict_of(viewer), "kind": kind, "subject": entity_id, "ledger": LEDGER_QUEUE},
        )
    )
    # Read before the actors resolve, so one lookup names everybody.
    who = _Who(
        viewer=viewer,
        names=await _names_of(
            database, [str(row["user_id"]) for row in decided if row["user_id"] is not None]
        ),
    )
    said = await decided_said(
        database, bench, viewer, decided, here=(kind, entity_id, say.HERE[say.VANTAGE_ENTITY])
    )
    return _decision_events(
        decided,
        who,
        final=frozenset(final_queues),
        files=await files_of_decisions(database, decided),
        lines={key: line.pieces for key, line in said.items()},
        under=lines_under(said),
    )


async def _ordered(
    database: Database, viewer: Viewer, events: list[Event], kept: int, access: Repository | None
) -> list[Event]:
    """The newest `kept` in the one order (`history.ordered`)."""
    events = ordered(one_line_per_kept(events))[-kept:]
    return await unshown_said(database, access, viewer, events)


def _bounded(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


async def _here(database: Database) -> set[str]:
    present = {str(row["name"]) for row in await database.fetch_all(_TABLES)}
    return {name for name in _FEATURE_TABLES if name in present}


async def history_of_tag(
    database: Database,
    viewer: Viewer,
    tag_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
    final_queues: Sequence[str] = (),
    bench: Workbench | None = None,
) -> list[Event]:
    """Everything that happened to one tag, oldest first. Unscoped: the route resolves the tag
    first; the viewer only gates the sharing."""
    kept = _bounded(limit)
    here = await _here(database)
    tag = await database.fetch_one(_TAG, (tag_id,))
    if tag is None:
        return []

    actor, actor_name = await _who_made(database, viewer, tag, here=here)
    made_a_copy = actor is Actor.SIFT and tag["created_by_via"] == "produced"
    events = [
        _made_event(
            "tag",
            tag_id,
            str(tag["name"]),
            int(tag["created_at"]),
            actor,
            say.from_maker(tag["created_by_via"], tag["created_by_act"])
            if actor is Actor.SIFT
            else "",
            actor_name,
            via="produced" if made_a_copy else None,
            act=tag["created_by_act"] if made_a_copy else None,
        )
    ]
    # The receipts first, so a tagging a card says leaves the count.
    decided = await _decided_events(database, viewer, "tag", tag_id, here, final_queues, bench)
    events.extend(
        _counted_events(
            counted_apart_from_receipts(
                await database.fetch_all(
                    _tag_files(here),
                    {**seen_by(viewer), "subject": tag_id, "ledger": LEDGER_QUEUE},
                ),
                drawn_receipts(decided),
            ),
            "tagged",
            _tagged_sentence,
            subject=tag_id,
            parameter="tagged",
        )
    )
    linked = (
        await thing_linked(database, Subject.TAG, tag_id, viewer)
        if {"tag_stash_box_links", "stash_boxes"} <= here
        else []
    )
    if {"stash_box_kept", "stash_boxes"} <= here:
        events.extend(await kept_events(database, "tag", tag_id))
    if viewer.is_admin:
        events.extend(await _grant_events(database, viewer, "tag", tag_id))
    events.extend(decided)
    events.extend(await _ledger_events(database, viewer, "tag", tag_id, kept, linked))
    return await _ordered(database, viewer, events, kept, None)


def _receipts_in(here: set[str]) -> bool:
    """Whether the record of decisions is here, so a counted row can name its receipt."""
    return {"workbench_decisions", "workbench_decision_subjects"} <= here


def _tag_files(here: set[str]) -> str:
    """Which of the four tag-files statements this process can run."""
    if stash_box_tables_in(here):
        return _TAG_FILES if _receipts_in(here) else _TAG_FILES_NO_LEDGER
    return _TAG_FILES_NO_BOX if _receipts_in(here) else _TAG_FILES_NO_BOX_NO_LEDGER


def _site_files(here: set[str]) -> str:
    """Which of the four Site-files statements this process can run."""
    if stash_box_tables_in(here):
        return _SITE_FILES if _receipts_in(here) else _SITE_FILES_NO_LEDGER
    return _SITE_FILES_NO_BOX if _receipts_in(here) else _SITE_FILES_NO_BOX_NO_LEDGER


async def history_of_site(
    database: Database,
    viewer: Viewer,
    site_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
    final_queues: Sequence[str] = (),
    bench: Workbench | None = None,
) -> list[Event]:
    """Everything that happened to one site, oldest first."""
    kept = _bounded(limit)
    here = await _here(database)
    site = await database.fetch_one(_SITE, (site_id,))
    if site is None:
        return []

    events: list[Event] = []
    if site["created_at"] is not None or site["created_by_kind"] is not None:
        actor, actor_name = await _who_made(database, viewer, site, here=here)
        events.append(
            _made_event(
                "site",
                site_id,
                str(site["name"]),
                None if site["created_at"] is None else int(site["created_at"]),
                actor,
                say.from_pass(site["created_by_via"]) if actor is Actor.SIFT else "",
                actor_name,
            )
        )
    events.extend(await site_username_lines(database, viewer, site_id, here))
    decided = await _decided_events(database, viewer, "site", site_id, here, final_queues, bench)
    events.extend(
        _counted_events(
            counted_apart_from_receipts(
                await database.fetch_all(
                    _site_files(here),
                    {**seen_by(viewer), "subject": site_id, "ledger": LEDGER_QUEUE},
                ),
                drawn_receipts(decided),
            ),
            "filed",
            _filed_sentence,
            subject=site_id,
            parameter="filed",
        )
    )
    linked = (
        await thing_linked(database, Subject.SITE, site_id, viewer)
        if {"site_stash_box_links", "stash_boxes"} <= here
        else []
    )
    if {"stash_box_kept", "stash_boxes"} <= here:
        events.extend(await kept_events(database, "site", site_id))
    if viewer.is_admin:
        events.extend(await _grant_events(database, viewer, "site", site_id))
    events.extend(decided)
    events.extend(
        await _ledger_events(
            database, viewer, "site", site_id, kept, linked, name_now=str(site["name"])
        )
    )
    return await _ordered(database, viewer, events, kept, None)


async def history_of_collection(
    database: Database,
    viewer: Viewer,
    collection_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
    final_queues: Sequence[str] = (),
    bench: Workbench | None = None,
) -> list[Event]:
    """Everything that happened to one shelf, oldest first."""
    kept = _bounded(limit)
    here = await _here(database)
    shelf = await database.fetch_one(_COLLECTION, (collection_id,))
    if shelf is None:
        return []

    # The shelf's maker is named only to an admin.
    owner = None if shelf["owner_id"] is None else str(shelf["owner_id"])
    mine = owner is not None and owner == viewer.id
    events = [
        _made_event(
            "collection",
            collection_id,
            str(shelf["name"]),
            int(shelf["created_at"]),
            Actor.YOU if mine else Actor.SOMEBODY,
            created=True,
        )
    ]
    if "workbench_decisions" in here:
        events.extend(
            _addition_events(
                await database.fetch_all(
                    _SHELF_ADDITIONS, {**seen_by(viewer), "subject": collection_id}
                ),
            )
        )
    if viewer.is_admin:
        events.extend(await _grant_events(database, viewer, "collection", collection_id))
    events.extend(
        await _decided_events(
            database, viewer, "collection", collection_id, here, final_queues, bench
        )
    )
    events.extend(await _ledger_events(database, viewer, "collection", collection_id, kept))
    return await _ordered(database, viewer, events, kept, None)


async def history_of_photo_set(
    database: Database,
    viewer: Viewer,
    photo_set_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
    final_queues: Sequence[str] = (),
    bench: Workbench | None = None,
    access: Repository | None = None,
) -> list[Event]:
    """Everything that happened to one Photo Set, oldest first."""
    kept = _bounded(limit)
    here = await _here(database)
    found = await database.fetch_one(_PHOTO_SET, (photo_set_id,))
    if found is None:
        return []

    actor, worded = _ORIGINS.get(str(found["origin"]), (Actor.SOMEBODY, ""))
    how: say.Part = worded
    if found["origin"] == "folder" and access is not None:
        how = await _from_the_folder(access, viewer, found) or worded
    events = [
        _made_event(
            "photo_set",
            photo_set_id,
            str(found["name"]),
            int(found["created_at"]),
            actor,
            how,
            created=True,
        )
    ]
    if "workbench_decisions" in here:
        events.extend(
            _addition_events(
                await database.fetch_all(
                    _SET_ADDITIONS, {**seen_by(viewer), "subject": photo_set_id}
                ),
                actor=actor,
                how=how,
                until=(
                    None
                    if actor is not Actor.SIFT
                    else int(found["created_at"]) + _FILLED_BY_THE_TASK
                ),
            )
        )
    if viewer.is_admin:
        events.extend(await _grant_events(database, viewer, "photo_set", photo_set_id))
    events.extend(
        await _decided_events(
            database, viewer, "photo_set", photo_set_id, here, final_queues, bench
        )
    )
    events.extend(await _ledger_events(database, viewer, "photo_set", photo_set_id, kept))
    return await _ordered(database, viewer, events, kept, access)


def _song_events(rows: Sequence[Row]) -> list[Event]:
    """One line per day, source and source file of the files a song was named on."""
    groups: dict[tuple[str | None, str | None, int | None], list[Row]] = {}
    for row in rows:
        at = None if row["at"] is None else int(row["at"])
        source = None if row["source"] is None else str(row["source"])
        origin = None if row["origin"] is None else str(row["origin"])
        groups.setdefault((source, origin, None if at is None else day_number(at)), []).append(row)
    events: list[Event] = []
    for (source, origin, _day), day_rows in groups.items():
        count = len(day_rows)
        what: Line = (
            say.listed(
                [(say.thing("asset", str(row["id"]), str(row["name"] or "")),) for row in day_rows]
            )
            if count <= _ADDITIONS_NAMED
            else say.said(files(count))
        )
        came_from = (
            None
            if origin is None
            else say.thing("asset", origin, str(day_rows[0]["origin_name"] or ""))
        )
        sift = source is not None
        moments = [int(row["at"]) for row in day_rows if row["at"] is not None]
        listed = tuple(
            Link(kind="asset", id=str(row["id"]), name=str(row["name"] or ""))
            for row in reversed(day_rows[-_ADDITIONS_LISTED:])
        )
        heading = (
            files(count)
            if len(listed) == count
            else f"The newest {len(listed):,} of {files(count)}"
        )
        events.append(
            Event(
                at=max(moments) if moments else None,
                actor=Actor.SIFT if sift else Actor.SOMEBODY,
                actor_name=SIFT if sift else None,
                kind="added",
                pieces=say.song_named_here(SIFT if sift else None, what, count, source, came_from),
                detail=(
                    (Detail(kind="asset", words=heading, links=listed),)
                    if count > _ADDITIONS_NAMED
                    else ()
                ),
            )
        )
    return events


#: Who made a song, as its own row says.
def _song_maker(viewer: Viewer, row: Row) -> tuple[Actor, str]:
    kind = row["created_by_kind"]
    if kind == "sift":
        return Actor.SIFT, say.from_pass(row["created_by_via"])
    if kind == "user" and row["created_by_user_id"] == viewer.id:
        return Actor.YOU, ""
    return Actor.SOMEBODY, ""


async def history_of_song(
    database: Database,
    viewer: Viewer,
    song_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
    final_queues: Sequence[str] = (),
    bench: Workbench | None = None,
) -> list[Event]:
    """Everything that happened to one song, oldest first. Unscoped: the route resolves the song
    first."""
    kept = _bounded(limit)
    here = await _here(database)
    found = await database.fetch_one(_SONG, (song_id,))
    if found is None:
        return []
    actor, how = _song_maker(viewer, found)
    events = [
        _made_event(
            "song",
            song_id,
            str(found["name"]),
            int(found["created_at"]),
            actor,
            how,
            created=True,
            via=None if found["created_by_via"] is None else str(found["created_by_via"]),
        )
    ]
    if "workbench_decisions" in here:
        events.extend(
            _song_events(
                await database.fetch_all(_SONG_ADDITIONS, {**seen_by(viewer), "subject": song_id})
            )
        )
    events.extend(
        await _decided_events(database, viewer, "song", song_id, here, final_queues, bench)
    )
    events.extend(await _ledger_events(database, viewer, "song", song_id, kept))
    return await _ordered(database, viewer, events, kept, None)
