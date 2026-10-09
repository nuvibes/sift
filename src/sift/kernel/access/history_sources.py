# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a file's own tables record about it: the point reads its History is drawn from."""

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

# Every statement below is a point read on one file; `test_a_point_read_is_not_a_library_read` plans
# each and fails any that walks the library.
_ADDED = point_read("history.added", "SELECT added_at, original_filename FROM assets WHERE id = ?")

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

#: Applied matches only. The `+` on `m.state` keeps the planner on the primary key rather than every
#: applied match in the library; the subquery takes the last run only.
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

#: Boxes asked about this file that found nothing, one line per box at its last ask.
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

#: Every face the scan found, so the line can name them; `LEFT` because an unnamed face matters
#: most.
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

#: What was made out of this file, newest first and capped as the editing feature caps it.
_MADE_INTO = point_read(
    "history.made_into",
    """
SELECT asset_id, operation, produced_by, produced_at
  FROM produced_files WHERE source_asset_id = ?
 ORDER BY produced_at DESC
 LIMIT ?
""",
)

MOST_COPIES = 12

#: The decisions that named this file, never an event (`LEDGER_QUEUE`), and only where the reader
#: may see every file it names, since the stored title's count cannot be scoped.
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

#: A grant on a tag or person reaches this file too, but happened to the tag, not the file.
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

#: The nearest folder above the file that was answered as the person.
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

#: The same for a page of files; `CROSS JOIN` leads with the locations so each file is a seek.
_NAMING_FOLDERS = (
    "SELECT loc.asset_id AS asset_id, f.id AS folder_id, f.root_id AS root_id, "
    "f.rel_path AS path, f.name AS name "
    "FROM asset_locations AS loc "
    "CROSS JOIN folder_people AS fp "
    "JOIN folders AS f ON f.id = fp.folder_id AND f.root_id = loc.root_id "
    "WHERE loc.asset_id IN (?*) AND fp.person_id = ? "
    "  AND (f.rel_path = '' OR substr(loc.rel_path, 1, length(f.rel_path) + 1) = f.rel_path || '/')"
)

_FILES_PER_READ = 500


async def nearest_naming_folders(
    database: Database, person_id: str, asset_ids: Sequence[str]
) -> dict[str, tuple[str, str, str, str]]:
    """The folder that named this person on each of these files, by file id."""
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
    """The folder each person was named from on this file, where the reader may see it."""
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
    """The folders this reader may see now, by library (`kernel.where`)."""
    folders = await access.visible_folders(viewer)
    return whereabouts_from(viewer, folders, root_paths={}, profile=None).seen


def arrived_in(
    moves: Sequence[Row], first: Row | None, seen: Mapping[str, frozenset[str]]
) -> tuple[str, str | None] | None:
    """The folder a file was in when Sift took it in, and how the reader may be told it."""
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
    """Each (library, folder path) a file's lines name; the top of a library is left out."""
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
    """Each folder's id by (library, path), for the ones still here."""
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
    """A rename or a move, plus the moment it was taken back where it was."""
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
            actor, name = Actor.SIFT, SIFT
        where = str(row["to_rel_path"])
        events.append(
            Event(
                at=int(row["moved_at"]),
                actor=actor,
                actor_name=name,
                kind="renamed" if renamed else "moved",
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
        # Somebody, never the mover: nothing records who put a file back.
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
    """The site a filing names, as somewhere to go; a username has no page and stays words."""
    if row["site_id"] is None:
        return None
    return say.thing("site", str(row["site_id"]), str(row["site"]))


NOT_SAID_ON_A_FILE: Mapping[str, str] = {
    "generate": "walks the files a Generate run chose and hands each its products; each says itself",
    "identify": "walks the files an Identify run chose and hands each its products; each says itself",
    "generate_file": "carries the products a Generate press chose; each says itself",
    "identify_file": "carries the products an Identify press chose; each says itself",
}

LEDGER_SAID = "ledger:"
