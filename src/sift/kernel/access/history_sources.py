# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a file's own tables record about it: the point reads its History is drawn from, and the
sources that need no user's name, fold or Undo to be said.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sift.kernel.access import sentences as say
from sift.kernel.access.history_actors import _Who
from sift.kernel.access.history_events import (
    NOTHING_HIDDEN,
)
from sift.kernel.access.history_line import Actor, Event, Undo, by_of
from sift.kernel.access.history_presses import (
    SAID_ON_A_FILE,
    Pressers,
    by_pressed,
    press_lines,
    pressers_of,
)
from sift.kernel.access.history_unread import _unread_sources
from sift.kernel.access.repository import Repository
from sift.kernel.access.sentences import SIFT, Piece
from sift.kernel.access.viewer import Viewer
from sift.kernel.db import Database, Row, in_clause, point_read
from sift.kernel.sql_splice import splice
from sift.kernel.where import folder_said, whereabouts_from

__all__ = [
    "LEDGER_SAID",
    "MOST_COPIES",
    "NOT_SAID_ON_A_FILE",
    "SAID_ON_A_FILE",
    "_ADDED",
    "_ASKED",
    "_CONFIRMED",
    "_DECIDED",
    "_ENRICHED",
    "_FACES_FOUND",
    "_FACE_SCAN",
    "_FILED",
    "_FIRST_PLACE",
    "_GRANTS",
    "_MADE_INTO",
    "_MOVES",
    "_NAMED",
    "_NAMING_FOLDER",
    "_PRODUCED",
    "_REJECTED",
    "_TAGGED",
    "Pressers",
    "_filed_site",
    "_folders_seen",
    "_move_events",
    "_unread_sources",
    "arrived_in",
    "by_pressed",
    "naming_folders",
    "nearest_naming_folders",
    "press_lines",
    "pressers_of",
]

# EVERY STATEMENT BELOW IS DECLARED A POINT READ, and that is a claim with a gate behind it rather
# than a label. Each answers about ONE file, so each may run on the event loop: twelve of these
# run to draw one pane, and a thread handoff apiece would spend most of the pane's cost on
# machinery rather than on reading.
#
# What makes it safe is that `tests/gates/test_a_point_read_is_not_a_library_read.py` plans every
# one of them on every run and fails any that walks a table growing with the library. That is not
# a formality here: `_ENRICHED` below is one unary plus away from being exactly such a walk, and
# nothing in its text says so. See `point_read`.
#: When the file was taken in, and under what name.
#:
#: The name is here and not on the record: a name the file ARRIVED under is not a property of the
#: file, it is something that HAPPENED to it, on a day, and this is the list of things that happened
#: to it.
_ADDED = point_read("history.added", "SELECT added_at, original_filename FROM assets WHERE id = ?")

#: Where a file was first found: its oldest copy's place. The folder its arrival line names when
#: no move says otherwise (`arrived_in`).
_FIRST_PLACE = point_read(
    "history.first_place",
    """
SELECT root_id, rel_path, archive_rel_path
  FROM asset_locations
 WHERE asset_id = ?
 ORDER BY first_seen_at ASC, id ASC
 LIMIT 1
""",
)

_MOVES = point_read(
    "history.moves",
    """
SELECT id, kind, root_id, from_rel_path, to_rel_path, moved_by, moved_by_sift, reason, moved_at,
       undone_at
  FROM file_moves
 WHERE asset_id = ?
 ORDER BY moved_at ASC, id ASC
""",
)

_NAMED = point_read(
    "history.named",
    """
SELECT p.id AS person_id, p.name AS name, link.source AS source,
       link.decided_at AS decided_at, link.box_id AS box_id
  FROM asset_people link
  JOIN people p ON p.id = link.person_id
 WHERE link.asset_id = ?
""",
)

_TAGGED = point_read(
    "history.tagged",
    """
SELECT t.id AS tag_id, t.name AS name, link.source AS source, link.decided_at AS decided_at,
       link.box_id AS box_id
  FROM asset_tags link
  JOIN tags t ON t.id = link.tag_id
 WHERE link.asset_id = ?
""",
)

_FILED = point_read(
    "history.filed",
    """
SELECT ac.name AS name, pl.id AS site_id, pl.name AS site, link.source AS source,
       link.decided_at AS decided_at, link.username_id AS username_id,
       ac.person_id AS person_id, link.box_id AS box_id
  FROM asset_usernames link
  JOIN usernames ac ON ac.id = link.username_id
  LEFT JOIN sites pl ON pl.id = ac.site_id
 WHERE link.asset_id = ?
""",
)

#: Only the APPLIED matches, which is the line `enriched_stash` draws too: a match still waiting is
#: a question nobody has answered and a refused one is an answer of no, and neither wrote anything
#: to the file.
#:
#: THE `+` IN FRONT OF `m.state` IS LOAD-BEARING and is not a typo. Written plainly, SQLite
#: plans this as a seek on `ix_stash_matches_state (state=?)` (every applied match in the whole
#: library, filtered afterwards to the one file), which on a library a stash-box has swept is a
#: read that grows with the media. The unary plus makes that term unusable as an index lookup, so
#: the planner takes the primary key instead and the plan becomes
#: `SEARCH m USING PRIMARY KEY (asset_id=?)` (EXPLAIN QUERY PLAN shows both).
#: `enrichment_runs` carries the half of the fact this table cannot: whether somebody pressed it.
#:
#: NOT A LEFT JOIN: every ask is kept (v51 of the catalog), so a file enriched against one box three
#: times would join three rows and the pane would draw the
#: same line three times. What is wanted is the LAST of them, which is one row off
#: `ix_enrichment_runs_subject`: the index carries `at` behind the three, so this reads one entry
#: and never opens the table. An applied match written by the switch that accepts exact matches and
#: one somebody agreed to on the confirm screen leave identical rows here, so the answer is stored
#: beside them rather than read out of them. NULL for a match applied before that was recorded,
#: which the sentence draws as saying nothing rather than as a guess.
_ENRICHED = point_read(
    "history.enriched",
    """
SELECT b.name AS box, m.decided_at AS decided_at, m.grade AS grade, m.remote_id AS remote_id,
       b.endpoint AS endpoint,
       (SELECT r.automatic FROM enrichment_runs r
         WHERE r.subject = 'asset' AND r.local_id = m.asset_id AND r.box_id = m.box_id
         ORDER BY r.at DESC, r.id DESC LIMIT 1) AS automatic,
       (SELECT r.applied FROM enrichment_runs r
         WHERE r.subject = 'asset' AND r.local_id = m.asset_id AND r.box_id = m.box_id
         ORDER BY r.at DESC, r.id DESC LIMIT 1) AS applied
  FROM asset_stash_box_matches m
  JOIN stash_boxes b ON b.id = m.box_id
 WHERE m.asset_id = ? AND +m.state = 'applied'
""",
)

#: WHICH BOXES WERE ASKED ABOUT THIS FILE AND FOUND NOTHING.
#:
#: The other half of the enrichment line. `_ENRICHED` above says which box recognised the file; a
#: box that was asked and had never heard of it leaves a row saying so (`stash_box_scans`, written
#: by `service.scan_one` on every ask, found or not). Without this a file put to three public
#: services and matched by none of them has a history identical to a file nobody ever asked about.
#:
#: `found = 0` ONLY. A box that found something is already told by `_ENRICHED` where the match was
#: applied, and where it is still waiting the answer is under Organize rather than on this file:
#: either way "nothing matched" would be false, which is the one thing a history line may not be.
#:
#: ONE LINE PER BOX, at the LAST ask, and the count where there was more than one. Every ask is
#: kept (version 12 of the stash-box component), so a file a sweep has put to one box five times
#: has five rows, and five lines saying the same thing is exactly the repetition this pane's folds
#: exist to end. What somebody wants is that the box has been asked, how lately, and how doggedly.
#:
#: A point read: `ix_stash_scans_asset (asset_id, box_id, scanned_at)` is sought on its first
#: column, so the grouping is over one file's own rows however many asks the library holds.
_ASKED = point_read(
    "history.asked",
    "SELECT b.name AS box, COUNT(*) AS asks, MAX(s.scanned_at) AS scanned_at"
    " FROM stash_box_scans s"
    " JOIN stash_boxes b ON b.id = s.box_id"
    " WHERE s.asset_id = ? AND s.found = 0"
    " GROUP BY s.box_id, b.name",
)

_FACE_SCAN = point_read(
    "history.face_scan",
    "SELECT track_count, scanned_at, refused_small, refused_closer, refused_largest,"
    " refused_blurred, refused_turned, refused_edge"
    " FROM face_scans WHERE asset_id = ?",
)

#: EVERY FACE THE SCAN FOUND, so the line about it can name them instead of counting them.
#:
#: A count alone is the one thing nobody needs from that sentence: "found 7" is a number somebody
#: then has to go and look up, and most of the seven are usually people this library already knows.
#: So the known are named, and a face nobody has named links to the group it waits in.
#:
#: `ix_face_tracks_asset` is what makes it a point read. The join is `LEFT` because the row that
#: matters most here is the one with no person on it (a face waiting to be named), and an inner
#: join would answer with exactly the faces this sentence does not need to name.
#:
#: A SUGGESTED face joins its person too, with the attribution beside it: the line says it as a
#: face that may be them (`history_faces._faces_found`), never as a face nobody named.
_FACES_FOUND = point_read(
    "history.faces_found",
    """
SELECT t.person_id AS person_id, p.name AS name, t.pile_id AS pile_id,
       t.attribution AS attribution
  FROM face_tracks t
  LEFT JOIN people p ON p.id = t.person_id
   AND t.attribution IN ('matched', 'confirmed', 'suggested')
 WHERE t.asset_id = ?
 ORDER BY t.started_ms ASC, t.id ASC
""",
)

_CONFIRMED = point_read(
    "history.confirmed",
    """
SELECT p.id AS person_id, p.name AS name, c.created_at AS created_at
  FROM face_confirmations c
  JOIN people p ON p.id = c.person_id
 WHERE c.asset_id = ?
""",
)

_REJECTED = point_read(
    "history.rejected",
    """
SELECT p.id AS person_id, p.name AS name, r.created_at AS created_at
  FROM face_rejections r
  JOIN face_tracks ft ON ft.id = r.track_id
  JOIN people p ON p.id = r.person_id
 WHERE ft.asset_id = ?
""",
)

_PRODUCED = point_read(
    "history.produced",
    "SELECT source_asset_id, operation, produced_by, produced_at"
    " FROM produced_files WHERE asset_id = ?",
)

#: The other end of the same row: what was made OUT of this file. Newest first and capped, because a
#: file somebody has trimmed thirty times is a list rather than a fact: the same reasoning, and
#: the same ceiling, the editing feature's own read of this table uses.
#:
#: `ix_produced_source` is what makes it a point read rather than a walk of the table.
_MADE_INTO = point_read(
    "history.made_into",
    """
SELECT asset_id, operation, produced_by, produced_at
  FROM produced_files WHERE source_asset_id = ?
 ORDER BY produced_at DESC
 LIMIT ?
""",
)

#: How many copies one file's history will name. See `_MADE_INTO`.
MOST_COPIES = 12

#: The decisions somebody took that named this file. See the module docstring of `history` for the
#: link table.
#:
#: The SEARCH is on `ix_workbench_subject (kind, subject_id)`, which is the whole reason that index
#: exists: without it this is a walk of every link row in the library (a table that grows with
#: every press anybody makes) to draw one file's pane.
#:
#: The kind is written into the statement rather than bound, because this read is about a FILE and
#: nothing else: a bound kind would make it a general link lookup that happens to be called with
#: `asset`, which is a wider question living in a function that cannot answer it.
#:
#: AND IT MUST NOT DRAW AN EVENT. The event ledger is this very table, so every act a writer records
#: lands here, and read straight out, an event would draw as a bulk judgement with an Undo on it,
#: offering a button the workbench refuses because an event under `LEDGER_QUEUE` belongs to no queue
#: and has no reverser. The word is
#: reserved so that no queue may claim it, which makes this exclusion exact rather than a guess. An
#: event that carries a RECEIPT is written under that receipt's own queue and still draws, which is
#: right: it is a decision and it can be taken back.
#:
#: AND IT IS SHOWN ONLY WHEN THE READER MAY SEE EVERY FILE IT NAMES. A receipt's title is written
#: with its number the moment it happens ("matched 300 more faces"), so the number is a count
#: taken over files this reader may never have been shown, and it cannot be recounted per reader
#: without rewording what was stored. Reached through the one file the reader CAN see, the title
#: would say how much there is around it. The same rule, from the one place it is written
#: (`history_events.NOTHING_HIDDEN`, the event ledger's), so the three threads and the ledger cannot
#: disagree about which receipt a reader is shown.
_DECIDED = point_read(
    "history.decided",
    splice(
        """
SELECT d.id AS id, d.title AS title, d.queue AS queue, d.user_id AS user_id,
       d.actor_kind AS actor_kind, d.actor_id AS actor_id,
       d.decided_at AS decided_at, d.reversed_at AS reversed_at, d.verb AS verb,
       d.object_kind AS object_kind, d.object_id AS object_id, d.payload AS payload,
       d.detail AS detail
  FROM workbench_decision_subjects s
  JOIN workbench_decisions d ON d.id = s.decision_id
 WHERE s.kind = 'asset' AND s.subject_id = :subject AND d.queue <> :ledger
{{NOTHING_HIDDEN}}
 ORDER BY d.decided_at ASC, d.id ASC
""",
        NOTHING_HIDDEN=NOTHING_HIDDEN,
    ),
)

#: Grants on the FILE itself. A grant on a tag or a person reaches this file too, and it is not an
#: event in this file's history: it happened to the tag.
_GRANTS = point_read(
    "history.grants",
    """
SELECT u.username AS username, g.effect AS effect, g.created_at AS created_at,
       g.subject_user_id AS user_id
  FROM acl_grants g
  JOIN users u ON u.id = g.subject_user_id
 WHERE g.object_type = 'item' AND g.object_id = ?
 ORDER BY g.id ASC
""",
)

#: WHICH FOLDER NAMED THIS PERSON ON THIS FILE: the nearest folder above the file (or the library's
#: own top) that was answered as the person, the standing rule the folder read keeps per folder
#: (`folder_people`). Read from the person's end, so `ix_folder_people_person` is the seek, then
#: each answered folder by its key and the file's places by the file. A folder deleted since has
#: no row here, and the line says what it can without one.
_NAMING_FOLDER = point_read(
    "history.naming_folder",
    """
SELECT f.id AS folder_id, f.root_id AS root_id, f.rel_path AS path, f.name AS name
  FROM folder_people fp
  JOIN folders f ON f.id = fp.folder_id
  JOIN asset_locations loc ON loc.asset_id = ? AND loc.root_id = f.root_id
 WHERE fp.person_id = ?
   AND (f.rel_path = '' OR substr(loc.rel_path, 1, length(f.rel_path) + 1) = f.rel_path || '/')
 ORDER BY length(f.rel_path) DESC
 LIMIT 1
""",
)

#: The same rule for a page of files at once: every folder answered as the person
#: (`folder_people`) that holds each file, the library's own top included; the caller keeps the
#: nearest, the longest path, as `_NAMING_FOLDER` does with its ORDER BY. The locations lead
#: (`CROSS JOIN` fixes the order), so each file is a seek on `ix_loc_asset` and the person's few
#: answered folders a seek on `ix_folder_people_person`, never a walk of a library's locations
#: under a folder.
_NAMING_FOLDERS = (
    "SELECT loc.asset_id AS asset_id, f.id AS folder_id, f.root_id AS root_id, "
    "f.rel_path AS path, f.name AS name "
    "FROM asset_locations AS loc "
    "CROSS JOIN folder_people AS fp "
    "JOIN folders AS f ON f.id = fp.folder_id AND f.root_id = loc.root_id "
    "WHERE loc.asset_id IN (?*) AND fp.person_id = ? "
    "  AND (f.rel_path = '' OR substr(loc.rel_path, 1, length(f.rel_path) + 1) = f.rel_path || '/')"
)

#: How many files one read of `_NAMING_FOLDERS` names, well under any bound on parameters.
_FILES_PER_READ = 500


async def nearest_naming_folders(
    database: Database, person_id: str, asset_ids: Sequence[str]
) -> dict[str, tuple[str, str, str, str]]:
    """The folder that named this person on each of these files, by file id: the folder's id, its
    library, its path inside it and its name.

    The NEAREST folder answered as the person above each file, the one a file's own History line
    names (`_NAMING_FOLDER`). A file no answered folder holds any more (the folder deleted, the
    answer taken back) is not in the answer. Unscoped: who may be told a folder is the caller's
    question (`folder_said`), asked of the path this hands back.
    """
    nearest: dict[str, tuple[int, str]] = {}
    about: dict[str, tuple[str, str, str, str]] = {}
    wanted = list(dict.fromkeys(asset_ids))
    for begin in range(0, len(wanted), _FILES_PER_READ):
        query, params = in_clause(_NAMING_FOLDERS, wanted[begin : begin + _FILES_PER_READ])
        for row in await database.fetch_all(query, [*params, person_id]):
            path, folder_id = str(row["path"]), str(row["folder_id"])
            about[folder_id] = (folder_id, str(row["root_id"]), path, str(row["name"]))
            depth = (len(path), folder_id)
            held = nearest.get(str(row["asset_id"]))
            if held is None or depth > held:
                nearest[str(row["asset_id"])] = depth
    return {asset_id: about[folder_id] for asset_id, (_depth, folder_id) in nearest.items()}


async def naming_folders(
    database: Database,
    access: Repository,
    viewer: Viewer,
    asset_id: str,
    person_ids: Sequence[str],
    seen: Mapping[str, frozenset[str]] | None = None,
) -> dict[str, Piece]:
    """The folder each person was named from on this file, by person id: only one the reader may
    see all the way down, a library's top included; any other line says "from a folder name"."""
    found: dict[str, Piece] = {}
    for person_id in dict.fromkeys(person_ids):
        row = await database.fetch_one(_NAMING_FOLDER, (asset_id, person_id))
        if row is None:
            continue
        if seen is None:
            seen = await _folders_seen(access, viewer)
        path, name = str(row["path"]), str(row["name"])
        if folder_said(path, seen=seen.get(str(row["root_id"]), frozenset())) != path:
            continue
        found[person_id] = say.folder_named(str(row["folder_id"]), name)
    return found


async def _folders_seen(access: Repository, viewer: Viewer) -> Mapping[str, frozenset[str]]:
    """The folders this reader may see now, by library, under the rule every place a location is
    said reads (`kernel.where`)."""
    folders = await access.visible_folders(viewer)
    return whereabouts_from(viewer, folders, root_paths={}, profile=None).seen


def arrived_in(
    moves: Sequence[Row], first: Row | None, seen: Mapping[str, frozenset[str]]
) -> tuple[str, str | None] | None:
    """The folder a file was in when Sift took it in, and how the reader may be told it.

    Recorded twice over, and neither is the file's place now: the first move's old path is where
    it was before anybody moved it, and with no move the oldest copy's place still is. A picture
    in an archive arrived in the archive's folder. None where neither is known, or where it came
    in at the top of the library, which the line says nothing about.
    """
    if moves:
        root_id, path = str(moves[0]["root_id"]), str(moves[0]["from_rel_path"])
    elif first is not None:
        root_id = str(first["root_id"])
        path = str(first["archive_rel_path"] or first["rel_path"])
    else:
        return None
    folder = say.moved_into(path)
    if folder == say.THE_TOP:
        return None
    return path, folder_said(folder, seen=seen.get(root_id, frozenset()))


def _landed(row: Row, seen: Mapping[str, frozenset[str]]) -> str | None:
    """The folder a move landed in, as the reader may be told it, or None to name it as it is."""
    folder = say.moved_into(str(row["to_rel_path"]))
    if folder == say.THE_TOP:
        return None
    return folder_said(folder, seen=seen.get(str(row["root_id"]), frozenset()))


def landing_folders(moves: Sequence[Row], arrived: tuple[str, str] | None) -> list[tuple[str, str]]:
    """Each (library, folder path) a file's lines name: where each move landed and where it arrived
    (`arrived` is the library and the path it arrived at). The top of a library is a phrase, not a
    folder, and is left out."""
    places = [(str(row["root_id"]), str(row["to_rel_path"])) for row in moves]
    if arrived is not None:
        places.append(arrived)
    folders = [(root, say.moved_into(path)) for root, path in places]
    return list(dict.fromkeys(one for one in folders if one[1] != say.THE_TOP))


_FOLDER_AT = point_read(
    "history.folder_at", "SELECT id FROM folders WHERE root_id = ? AND rel_path = ?"
)


async def folder_ids(
    database: Database, places: Sequence[tuple[str, str]]
) -> dict[tuple[str, str], str]:
    """Each folder's id by (library, path), for the ones still here: a line links a folder by its
    id (`sentences.folder_named`), and one that has gone is said in words."""
    found: dict[tuple[str, str], str] = {}
    for place in places:
        row = await database.fetch_one(_FOLDER_AT, place)
        if row is not None:
            found[place] = str(row["id"])
    return found


def _move_events(
    rows: Sequence[Row],
    who: _Who,
    seen: Mapping[str, frozenset[str]],
    folders: Mapping[tuple[str, str], str] | None = None,
) -> list[Event]:
    """A rename or a move, plus the moment it was taken back where it was.

    `seen` is the folders the reader may see, by library (`kernel.where`): a move names the folder
    it landed in only as far as they may see it, so a Hidden folder is never named while Hidden is
    shut.

    Only the LAST move that has not been undone carries an `undo`, and only for an admin. Both
    halves are the organizer's own rules rather than a guess made here: it refuses an undo out of
    order (a file renamed twice would otherwise go back to a name it only ever had in passing),
    and it refuses one from a guest. An affordance the server would refuse is worse than none,
    because the only way to find out is to press it.
    """
    events: list[Event] = []
    undoable = ""
    if who.viewer.is_admin:
        for row in rows:
            if row["undone_at"] is None:
                undoable = str(row["id"])
    for row in rows:
        renamed = str(row["kind"]) == "rename"
        actor, name = who.of(None if row["moved_by"] is None else str(row["moved_by"]))
        if row["moved_by"] is None and int(row["moved_by_sift"] or 0):
            # A move nobody asked for is Sift's own repair, not "Somebody renamed".
            actor, name = Actor.SIFT, SIFT
        where = str(row["to_rel_path"])
        events.append(
            Event(
                at=int(row["moved_at"]),
                actor=actor,
                actor_name=name,
                kind="renamed" if renamed else "moved",
                # The folder is placed where it sits (`say.folder_of`): a way there, or plain
                # words for the top of the library, which is a phrase and not a folder.
                pieces=(
                    say.renamed(by_of(actor, name), where, row["reason"], row["from_rel_path"])
                    if renamed
                    else say.moved(
                        by_of(actor, name),
                        where,
                        _landed(row, seen),
                        (folders or {}).get((str(row["root_id"]), say.moved_into(where))),
                    )
                ),
                undo=Undo(kind="move", id=str(row["id"])) if str(row["id"]) == undoable else None,
                reversed=row["undone_at"] is not None,
            )
        )
        if row["undone_at"] is None:
            continue
        # SOMEBODY, never the user who made the move. `file_moves` records who moved a file
        # and the MOMENT it was put back, and nothing at all about who put it back, so carrying
        # the mover across would say a named user did something it may not have done. That is
        # the one direction an attribution may not be wrong in.
        events.append(
            Event(
                at=int(row["undone_at"]),
                actor=Actor.SOMEBODY,
                actor_name=None,
                kind="undone",
                pieces=say.undone("rename" if renamed else "move"),
            )
        )
    return events


def _filed_site(row: Row) -> Piece | None:
    """The site a filing names, as somewhere to go.

    The SITE and never the username, and that asymmetry is the tables' rather than a choice made
    here: a site is a row with a page of its own, and a username is a name on a site with no
    page anywhere in Sift. So the username stays words. A filing whose site has been deleted has
    none to link (`usernames.site_id` is `ON DELETE SET NULL`), and the sentence already says
    "Filed under <username>" with no site in it, so there is nothing to find either.
    """
    if row["site_id"] is None:
        return None
    return say.thing("site", str(row["site_id"]), str(row["site"]))


#: The passes over a file that say nothing on its History of their own, and why.
NOT_SAID_ON_A_FILE: Mapping[str, str] = {
    "generate": "walks the files a Generate run chose and hands each its products; each says itself",
    "identify": "walks the files an Identify run chose and hands each its products; each says itself",
    "generate_file": "carries the products a Generate press chose; each says itself",
    "identify_file": "carries the products an Identify press chose; each says itself",
}

#: The prefix a ledger act is named by in a declaration (`ledger:scanned`).
LEDGER_SAID = "ledger:"
