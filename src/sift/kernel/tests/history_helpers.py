# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file carrying one of everything, and the acts that put each thing on it."""

from __future__ import annotations

from collections.abc import Sequence

# Imported for their side effect: registering the tables a feature owns, so a kernel database has
# them. The application always does (every install creates them whether or not the feature is
# switched on), and the reads guarded on those tables have the other case proved by dropping them.
import sift.slices.download.schema
import sift.slices.faces.schema
import sift.slices.media_edit.schema
import sift.slices.organize.schema
import sift.slices.semantic.schema
import sift.slices.stash_boxes.schema
import sift.slices.suggestions.schema
import sift.slices.watermarks.schema
import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Repository, Viewer
from sift.kernel.access.history import Event
from sift.kernel.db import Database
from sift.kernel.ids import new_id
from sift.testing.fixtures import Actors

#: A fixed clock, so every assertion below is about order rather than about when the test ran.
ADDED_AT = 1_700_000_000
NAMED_AT = ADDED_AT + 100
TAGGED_AT = ADDED_AT + 200
FILED_AT = ADDED_AT + 300
ENRICHED_AT = ADDED_AT + 400
SCANNED_AT = ADDED_AT + 500
MOVED_AT = ADDED_AT + 600
UNDONE_AT = ADDED_AT + 700
DECIDED_AT = ADDED_AT + 800
REVERSED_AT = ADDED_AT + 900
ASSET = "01HX0000000000000000000501"
PERSON = "01HX0000000000000000000502"
TAG = "01HX0000000000000000000503"
SITE = "01HX0000000000000000000504"
USERNAME = "01HX0000000000000000000505"
BOX = "01HX0000000000000000000506"
ROOT = "01HX0000000000000000000507"
MOVE = "01HX0000000000000000000508"
TRACK = "01HX0000000000000000000509"
DECISION = "01HX0000000000000000000510"
SOURCE = "01HX0000000000000000000511"
#: The library every `make_file` file sits loose in. Its own root rather than `ROOT`, which the move
#: tests write with a plain INSERT and would collide with.
LIBRARY = "01HX0000000000000000000590"


async def make_file(database: Database, asset_id: str = ASSET) -> None:
    """One file in the library, loose in one root, with nothing decided about it: a receipt is
    shown only to a reader who may see every file it names, and a file with no location to nobody."""
    await database.execute(
        "INSERT INTO assets (id, identity, identity_version, media_type, added_at)"
        " VALUES (?, ?, 1, 'video', ?)",
        (asset_id, f"digest-{asset_id}", ADDED_AT),
    )
    await database.execute(
        "INSERT OR IGNORE INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (LIBRARY, "files", "/files", ADDED_AT),
    )
    await database.execute(
        "INSERT INTO asset_locations (id, asset_id, root_id, folder_id, rel_path, filename,"
        " first_seen_at, last_seen_at) VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
        (new_id(), asset_id, LIBRARY, f"{asset_id}.mp4", f"{asset_id}.mp4", ADDED_AT, ADDED_AT),
    )


async def name_person(database: Database, *, source: str | None, at: int | None) -> None:
    await database.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)",
        (PERSON, "Neve Alder", ADDED_AT),
    )
    await database.execute(
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (ASSET, PERSON, source, at),
    )


async def tag_it(database: Database, *, source: str | None, at: int | None) -> None:
    await database.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)", (TAG, "poolside", ADDED_AT)
    )
    await database.execute(
        "INSERT INTO asset_tags (asset_id, tag_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (ASSET, TAG, source, at),
    )


async def file_under_site(
    database: Database,
    *,
    name: str = "harlowquin",
    site: str | None = "Studio",
    source: str | None = None,
) -> None:
    """A filing on the file. `source` is the pass that made it, which is what draws the `via` mark."""
    if site is not None:
        await database.execute("INSERT INTO sites (id, name) VALUES (?, ?)", (SITE, site))
    await database.execute(
        "INSERT INTO usernames (id, site_id, name, created_at) VALUES (?, ?, ?, ?)",
        (USERNAME, SITE if site is not None else None, name, ADDED_AT),
    )
    await database.execute(
        "INSERT INTO asset_usernames (asset_id, username_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (ASSET, USERNAME, source, FILED_AT),
    )


async def enrich(database: Database) -> None:
    await database.execute(
        "INSERT INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, ?)",
        (BOX, "StashDB", "https://example.invalid/graphql", ADDED_AT),
    )
    await database.execute(
        "INSERT INTO asset_stash_box_matches"
        " (asset_id, box_id, remote_id, payload, grade, state, found_at, decided_at)"
        " VALUES (?, ?, 'remote', '{}', 'certain', 'applied', ?, ?)",
        (ASSET, BOX, ENRICHED_AT, ENRICHED_AT),
    )


async def ask_a_box(database: Database, *, found: bool, at: int = ENRICHED_AT) -> None:
    """A box ASKED about the file, and what it answered, written by `scan_one` on every ask. Every
    ask is a row of its own, so the moment is a parameter."""
    await database.execute(
        "INSERT OR IGNORE INTO stash_boxes (id, name, endpoint, created_at) VALUES (?, ?, ?, ?)",
        (BOX, "StashDB", "https://example.invalid/graphql", ADDED_AT),
    )
    await database.execute(
        "INSERT INTO stash_box_scans (id, asset_id, box_id, scanned_at, found)"
        " VALUES (?, ?, ?, ?, ?)",
        (new_id(), ASSET, BOX, at, 1 if found else 0),
    )


async def scan_faces(
    database: Database,
    *,
    found: int,
    small: int | None = None,
    closer: int | None = None,
    why: tuple[int | None, int | None, int | None, int | None] = (None, None, None, None),
) -> None:
    """A face pass over the file. `scanned_at` is MILLISECONDS, as the face tables all are.

    `small` and `closer` are what the pass refused at each gate; None is a scan from before those
    were kept. `why` is the biggest face too small, then the closer look's blurred, turned-away and
    off-the-edge counts, None on a scan from before they were kept."""
    await database.execute(
        "INSERT INTO face_scans (asset_id, status, depth, coverage, frames_sampled, track_count,"
        " identified_count, detector, recognizer, settings_digest, scanned_at, refused_small,"
        " refused_closer, refused_largest, refused_blurred, refused_turned, refused_edge)"
        " VALUES (?, 'no_faces', 'fast', 1.0, 10, ?, 0, 'd', 'r', 'x', ?, ?, ?, ?, ?, ?, ?)",
        (ASSET, found, SCANNED_AT * 1000, small, closer, *why),
    )


async def landed_in(database: Database, to_rel_path: str) -> None:
    """The folders a move landed in, as rows, the way a library that holds the file has them.

    A move names its folder only as far as the reader may see it (`history._landed`), read off the
    folder rows, so a move into a folder with no row is said as one nobody may see.
    """
    parent: str | None = None
    parts = to_rel_path.split("/")[:-1]
    for depth in range(len(parts)):
        folder_id = new_id()
        await database.execute(
            "INSERT INTO folders (id, root_id, parent_id, rel_path, name) VALUES (?, ?, ?, ?, ?)",
            (folder_id, ROOT, parent, "/".join(parts[: depth + 1]), parts[depth]),
        )
        parent = folder_id


async def move_file(
    database: Database, *, kind: str, by: str | None, undone: int | None = None
) -> None:
    await database.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (ROOT, "library", "/library", ADDED_AT),
    )
    await landed_in(database, "new/clip.mp4")
    await database.execute(
        "INSERT INTO file_moves (id, kind, location_id, asset_id, root_id, from_rel_path,"
        " to_rel_path, moved_by, moved_at, undone_at) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)",
        (MOVE, kind, ASSET, ROOT, "old/clip.mp4", "new/clip.mp4", by, MOVED_AT, undone),
    )


async def move_it(database: Database, *, to: str, kind: str = "move") -> None:
    """A move landing at one path. `move_file` above fixes the path; these tests vary it."""
    await database.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES (?, ?, ?, ?)",
        (ROOT, "library", "/library", ADDED_AT),
    )
    await landed_in(database, to)
    await database.execute(
        "INSERT INTO file_moves (id, kind, location_id, asset_id, root_id, from_rel_path,"
        " to_rel_path, moved_by, moved_at, undone_at) VALUES (?, ?, NULL, ?, ?, ?, ?, NULL, ?,"
        " NULL)",
        (MOVE, kind, ASSET, ROOT, "old/clip.mp4", to, MOVED_AT),
    )


async def only_admin(temp_db: Database, access: Repository, actors: Actors) -> Viewer:
    """An admin, reloaded from the database so the role is whatever the row says."""
    loaded = await access.load_viewer(actors.admin.id)
    assert loaded is not None
    return loaded


async def agree_to_a_face(
    database: Database, *, at: int, person: str = PERSON, asset: str = ASSET
) -> None:
    """A face on this file agreed to be somebody. Milliseconds, as the face tables all are."""
    await database.execute(
        "INSERT INTO face_confirmations (id, asset_id, person_id, embedding, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (new_id(), asset, person, b"\x00", at * 1000),
    )


async def matched_a_face(
    database: Database,
    *,
    at: int,
    sure: float | None = 0.92,
    person: str = PERSON,
    track: str | None = None,
) -> str:
    """An appearance Sift attached to somebody on its own. Milliseconds, as the face tables all are.

    `attribution` is what tells the three apart and it is the whole point of the fixture: a
    `suggested` row is a question nobody has answered and a `confirmed` one is somebody's answer,
    and neither may draw as a thing Sift decided.
    """
    track_id = track or new_id()
    await database.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, person_id,"
        " confidence, attribution, attributed_at, created_at)"
        " VALUES (?, ?, 0, 1, 1, 1.0, ?, ?, 'matched', ?, ?)",
        (track_id, ASSET, person, sure, at * 1000, ADDED_AT * 1000),
    )
    return track_id


async def make_findable(database: Database, asset_id: str, filename: str) -> None:
    """A file with somewhere to be, which is what makes it NAMEABLE: the name comes from where the
    bytes are, so this RENAMES the one location `make_file` gave it rather than adding a second
    whose name the read might choose; a file with no location is `test_a_copy_whose_original_is_gone`."""
    await database.execute(
        "UPDATE asset_locations SET rel_path = ?, filename = ? WHERE asset_id = ?",
        (filename, filename, asset_id),
    )


async def make_copy(
    database: Database, *, copy: str, source: str, by: str, operation: str = "compress"
) -> None:
    """One row of `produced_files`: `copy` was made out of `source`, by that verb."""
    await database.execute(
        "INSERT INTO produced_files (id, asset_id, source_asset_id, operation, produced_by,"
        " produced_at) VALUES (?, ?, ?, ?, ?, ?)",
        (new_id(), copy, source, operation, by, MOVED_AT),
    )


async def decide(
    database: Database,
    *,
    queue: str = "folders",
    title: str = "Ilva Brennan - 47 files",
    by: str | None,
    reversed_at: int | None = None,
    about: str = ASSET,
    decision_id: str = DECISION,
    verb: str = "decided",
    object_kind: str | None = None,
    object_id: str | None = None,
    payload: str = "{}",
) -> None:
    """One bulk judgement, and the link saying it named this file: both rows, since either alone
    is a state the application cannot produce; `verb` and the object are what the pane folds on."""
    await database.execute(
        "INSERT INTO workbench_decisions"
        " (id, queue, user_id, title, detail, payload, decided_at, reversed_at,"
        " verb, object_kind, object_id)"
        " VALUES (?, ?, ?, ?, 'It did something.', ?, ?, ?, ?, ?, ?)",
        (
            decision_id,
            queue,
            by,
            title,
            payload,
            DECIDED_AT,
            reversed_at,
            verb,
            object_kind,
            object_id,
        ),
    )
    await database.execute(
        "INSERT INTO workbench_decision_subjects (decision_id, kind, subject_id)"
        " VALUES (?, 'asset', ?)",
        (decision_id, about),
    )


#: What the filing line says on its own, and the title the filename pass writes about the same act.
#:
#: TWO STRINGS, and that they differ is the point: the fold keys on the receipt's id, so a title
#: stored long ago and a sentence improved since are still one act. `FILENAME_TITLE` is the stored
#: one, "own" and all, exactly as a stored receipt carries it.
FILED_SENTENCE = "Filed under harlowquin on Studio"
FILENAME_TITLE = f"{FILED_SENTENCE} from the file's own name"
#: The filing's own line, the actor first and the task at the back, and the ONE sentence the fold
#: keeps: the line built from the rows, with the receipt's Undo on it.
FILED_LINE = "Sift filed this file under harlowquin on Studio from the file's name"
#: A second person, tag and box, for the presses that touch more than one thing.
OTHER_PERSON = "01HX0000000000000000000512"
OTHER_TAG = "01HX0000000000000000000513"
OTHER_BOX = "01HX0000000000000000000514"
PILE = "01HX0000000000000000000515"
OTHER_PILE = "01HX0000000000000000000516"


async def name_another(database: Database, *, source: str | None, at: int | None) -> None:
    """A second person on the same file, so a press can be more than one row."""
    await database.execute(
        "INSERT INTO people (id, name, created_at) VALUES (?, ?, ?)",
        (OTHER_PERSON, "Ada Lumen", ADDED_AT),
    )
    await database.execute(
        "INSERT INTO asset_people (asset_id, person_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (ASSET, OTHER_PERSON, source, at),
    )


async def tag_again(database: Database, *, source: str | None, at: int | None) -> None:
    """A second tag on the same file."""
    await database.execute(
        "INSERT INTO tags (id, name, created_at) VALUES (?, ?, ?)",
        (OTHER_TAG, "split screen", ADDED_AT),
    )
    await database.execute(
        "INSERT INTO asset_tags (asset_id, tag_id, source, decided_at) VALUES (?, ?, ?, ?)",
        (ASSET, OTHER_TAG, source, at),
    )


async def a_face(
    database: Database,
    *,
    track: str,
    person: str | None,
    pile: str | None,
    at: int,
    question: bool = False,
) -> None:
    """One appearance of one face on the file: named, waiting to be, or (a question) only asked."""
    if pile is not None:
        await database.execute(
            "INSERT OR IGNORE INTO face_piles (id, status, centroid, size, created_at, updated_at)"
            " VALUES (?, 'open', ?, 1, ?, ?)",
            (pile, b"\x00", ADDED_AT, ADDED_AT),
        )
    # A named face here is one Sift recognized: a question (attribution suggested) does not name
    # the file, and the "found" line names only what names the file.
    await database.execute(
        "INSERT INTO face_tracks (id, asset_id, started_ms, ended_ms, seen_in, quality, person_id,"
        " pile_id, created_at, attribution) VALUES (?, ?, ?, ?, 1, 1.0, ?, ?, ?,"
        " CASE WHEN ? IS NULL THEN NULL WHEN ? THEN 'suggested' ELSE 'matched' END)",
        (track, ASSET, at, at, person, pile, ADDED_AT * 1000, person, question),
    )


def said_of(events: Sequence[Event], kind: str) -> Event:
    """The one event of a kind, so a test can assert about it without counting rows first."""
    found = [one for one in events if one.kind == kind]
    assert len(found) == 1
    return found[0]


async def record_the_ask(database: Database, *, applied: str | None) -> None:
    """What an ask FILLED IN, which is the half of the fact the file's own rows cannot carry.

    `enrichment_runs.applied`, version 52 of the catalog. Seeded as SQL beside `enrich` above,
    which writes the match this hangs off.
    """
    await database.execute(
        "INSERT INTO enrichment_runs (id, subject, local_id, box_id, at, automatic, applied)"
        " VALUES ('run-1', 'asset', ?, ?, ?, 1, ?)",
        (ASSET, BOX, ENRICHED_AT, applied),
    )


async def read_a_watermark(
    database: Database,
    *,
    found: bool,
    kind: str = "site",
    text: str = "studio.example/harlowquin",
    site: str = "Studio",
    username: str | None = "harlowquin",
) -> None:
    """A look for a site's mark on the picture, and what it read. A site's address by default."""
    await database.execute(
        "INSERT INTO watermark_scans (asset_id, revision, identity, found, scanned_at)"
        " VALUES (?, 'r1', 'i1', ?, ?)",
        (ASSET, 1 if found else 0, SCANNED_AT),
    )
    if found:
        await database.execute(
            "INSERT INTO watermark_reads (asset_id, text, kind, site, username, confidence, read_at)"
            " VALUES (?, ?, ?, ?, ?, 0.9, ?)",
            (ASSET, text, kind, site, username, SCANNED_AT),
        )


async def download_it(database: Database) -> None:
    await database.execute(
        "INSERT INTO downloads (id, url, url_hash, state, site, username, asset_id,"
        " created_at, finished_at) VALUES (?, 'https://example.invalid/x', 'h', 'done',"
        " 'Studio', 'harlowquin', ?, ?, ?)",
        (new_id(), ASSET, ADDED_AT, ADDED_AT + 5),
    )


async def build_pictures(database: Database) -> None:
    for kind, when in (("thumb", ADDED_AT + 10), ("preview", ADDED_AT + 20)):
        await database.execute(
            "INSERT INTO derivatives (id, asset_id, kind, rel_cache_path, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (new_id(), ASSET, kind, f"{kind}/x", when),
        )
