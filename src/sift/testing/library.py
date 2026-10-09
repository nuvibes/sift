# SPDX-License-Identifier: AGPL-3.0-or-later
"""Helpers for putting a library into a running application's database."""

from __future__ import annotations

import asyncio
import struct
import zlib
from pathlib import Path

from sift.kernel.db import Database
from sift.kernel.sorting import sort_key

#: One statement and the values it binds. What `_write` takes, and what `hidden_row` hands back.
_Statement = tuple[str, tuple[object, ...]]

#: A value rather than a NULL, which would mean something else.
_HIDDEN_AT = 1_700_000_000

_INSERT_ROOT = """
INSERT INTO library_roots (id, name, abs_path, kind, created_at)
VALUES (?, ?, ?, 'local', 0)
"""

_INSERT_FOLDER = """
INSERT INTO folders (id, root_id, parent_id, rel_path, name)
VALUES (?, ?, NULL, '', ?)
"""

_INSERT_SHARE = """
INSERT INTO acl_grants (id, object_type, object_id, subject_user_id, effect, created_at)
VALUES (?, 'folder', ?, ?, 'share', 0)
ON CONFLICT DO NOTHING
"""


def write_rows(db_path: Path, statements: list[tuple[str, tuple[object, ...]]]) -> None:
    """Seed rows a test needs and no endpoint can make."""
    _write(db_path, statements)


def _write(db_path: Path, statements: list[tuple[str, tuple[object, ...]]]) -> None:
    async def run() -> None:
        database = Database(db_path, readers=1)
        await database.connect()
        try:
            async with database.write() as connection:
                for sql, params in statements:
                    await connection.execute(sql, params)
        finally:
            await database.close()

    asyncio.run(run())


_INSERT_GRANT = """
INSERT INTO browse_grants (id, abs_path, granted_at)
VALUES (?, ?, 0)
"""


#: Replaced, not ignored: the watcher may have made the row first, and the test needs its own id.
_CLEAR_SUBFOLDER = "DELETE FROM folders WHERE root_id = ? AND rel_path = ?"

_INSERT_SUBFOLDER = """
INSERT INTO folders (id, root_id, parent_id, rel_path, name)
VALUES (?, ?, ?, ?, ?)
"""


def seed_root(
    db_path: Path,
    root_id: str,
    *,
    folder_id: str,
    path: Path,
    name: str = "Videos",
) -> None:
    """Put one library root, and the folder row standing for it, in a known place with known ids."""
    _write(
        db_path,
        [
            (_INSERT_ROOT, (root_id, name, str(path))),
            (_INSERT_FOLDER, (folder_id, root_id, name)),
        ],
    )


def seed_grant(db_path: Path, grant_id: str, *, path: Path) -> None:
    """One folder Sift has been given, with a known id."""
    _write(db_path, [(_INSERT_GRANT, (grant_id, str(path)))])


def seed_folder(
    db_path: Path,
    folder_id: str,
    *,
    root_id: str,
    parent_id: str,
    rel_path: str,
) -> None:
    """A folder inside a root, for a test that needs somewhere to move something to."""
    _write(
        db_path,
        [
            (_CLEAR_SUBFOLDER, (root_id, rel_path)),
            (
                _INSERT_SUBFOLDER,
                (folder_id, root_id, parent_id, rel_path, rel_path.rsplit("/", 1)[-1]),
            ),
        ],
    )


def share_folder(db_path: Path, folder_id: str, user_id: str, *, grant_id: str) -> None:
    """Let one user see one folder."""
    _write(db_path, [(_INSERT_SHARE, (grant_id, folder_id, user_id))])


_INSERT_ASSET = """
INSERT INTO assets (id, identity, identity_version, media_type, width, height, duration_ms,
                    size_bytes, original_filename, filename_sort, added_at)
VALUES (?, ?, 1, 'video', 640, 360, 1000, ?, ?, ?, 0)
"""

_INSERT_ASSET_LOCATION = """
INSERT INTO asset_locations
    (id, asset_id, root_id, folder_id, rel_path, filename, first_seen_at, last_seen_at)
VALUES (?, ?, ?, ?, ?, ?, 0, 0)
"""

_INSERT_DERIVATIVE = """
INSERT INTO derivatives (id, asset_id, kind, rel_cache_path, params, size_bytes, created_at)
VALUES (?, ?, ?, ?, ?, ?, 0)
"""

#: A strip without settings is filed where the application never looks; any numbers will do.
_SEEDED_SPRITE_LAYOUT = {"columns": 5, "rows": 6, "tile_width": 320}


def seed_asset(
    db_path: Path,
    asset_id: str,
    *,
    root_id: str,
    folder_id: str,
    root_path: Path,
    cache_dir: Path,
    filename: str = "clip.mp4",
    rel_path: str | None = None,
) -> None:
    """One asset that really exists: a row, a location, bytes on disk, and its derivatives."""
    from sift.kernel.content import DerivativeKind, derivative_relpath, params_key

    rel_path = rel_path or filename
    body = b"seeded bytes"

    (root_path / rel_path).parent.mkdir(parents=True, exist_ok=True)
    (root_path / rel_path).write_bytes(body)

    statements: list[tuple[str, tuple[object, ...]]] = [
        (_INSERT_ASSET, (asset_id, f"digest-{asset_id}", len(body), filename, sort_key(filename))),
        (
            _INSERT_ASSET_LOCATION,
            (f"{asset_id[:-1]}L", asset_id, root_id, folder_id, rel_path, filename),
        ),
    ]

    for index, (kind, extension, params) in enumerate(
        (
            (DerivativeKind.THUMB, "jpg", None),
            (DerivativeKind.PREVIEW, "mp4", None),
            (DerivativeKind.SPRITE, "jpg", _SEEDED_SPRITE_LAYOUT),
            (DerivativeKind.RENDITION, "jpg", None),
        )
    ):
        relative = derivative_relpath(asset_id, kind, extension=extension, params=params)
        written = cache_dir / relative
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_bytes(body)
        statements.append(
            (
                _INSERT_DERIVATIVE,
                (
                    f"{asset_id[:-1]}{index}",
                    asset_id,
                    kind.value,
                    relative,
                    params_key(params),
                    len(body),
                ),
            )
        )

    _write(db_path, statements)


_INSERT_MOVE = """
INSERT INTO file_moves
  (id, kind, location_id, asset_id, root_id, from_rel_path, from_folder_id, to_rel_path,
   to_folder_id, moved_by, moved_at, undone_at)
VALUES (?, 'rename', ?, NULL, ?, ?, NULL, ?, NULL, NULL, 0, NULL)
"""


_INSERT_CLAIM = """
INSERT INTO folder_claims
  (id, folder_id, kind, name_key, proposed, evidence, state, created_at)
VALUES (?, ?, 'person', ?, ?, 'name_only', 'pending', 0)
"""


def seed_claim(
    db_path: Path,
    claim_id: str,
    *,
    folder_id: str,
    proposed: str = "seeded person",
) -> None:
    """One folder claim waiting to be answered, so a route that takes a claim id finds one."""
    _write(db_path, [(_INSERT_CLAIM, (claim_id, folder_id, proposed.casefold(), proposed))])


def seed_move(
    db_path: Path,
    move_id: str,
    *,
    root_id: str,
    location_id: str = "01HX0000000000000000000099",
    from_rel_path: str = "before.mp4",
    to_rel_path: str = "after.mp4",
) -> None:
    """Record one move, so a route that takes a move id has something to find."""
    _write(
        db_path,
        [(_INSERT_MOVE, (move_id, location_id, root_id, from_rel_path, to_rel_path))],
    )


_INSERT_TAG = "INSERT INTO tags (id, name, name_sort, created_at) VALUES (?, ?, ?, 0)"


_LINK_ASSET_TAG = "INSERT OR IGNORE INTO asset_tags (asset_id, tag_id) VALUES (?, ?)"


def seed_tag(db_path: Path, tag_id: str, name: str = "seeded") -> None:
    """Put one tag where a route that takes a tag id can find it."""
    _write(db_path, [(_INSERT_TAG, (tag_id, name, sort_key(name)))])


_INSERT_PERSON = (
    "INSERT INTO people (id, name, name_sort, notes, created_at) VALUES (?, ?, ?, NULL, 0)"
)

_INSERT_LINK = "INSERT INTO people_links (id, person_id, url, created_at) VALUES (?, ?, ?, 0)"

_INSERT_ALIAS = (
    "INSERT INTO people_aliases (id, person_id, alias, alias_sort, added_at) VALUES (?, ?, ?, ?, 0)"
)

_INSERT_SITE = "INSERT INTO sites (id, name, name_sort, kind, created_at) VALUES (?, ?, ?, NULL, 0)"

_INSERT_USERNAME = (
    "INSERT INTO usernames (id, site_id, name, name_sort, display_name, url, created_at) "
    "VALUES (?, ?, ?, ?, NULL, NULL, 0)"
)


def seed_person(db_path: Path, person_id: str, name: str = "seeded person") -> None:
    """Put one person where a route that takes a person id can find it."""
    _write(db_path, [(_INSERT_PERSON, (person_id, name, sort_key(name)))])


_INSERT_COLLECTION = (
    "INSERT INTO collections (id, name, name_sort, cover_asset_id, owner_id, created_at) "
    "VALUES (?, ?, ?, NULL, NULL, 0)"
)

_LINK_ASSET_COLLECTION = (
    "INSERT OR IGNORE INTO collection_items (collection_id, asset_id, added_at) VALUES (?, ?, 0)"
)


def seed_collection(
    db_path: Path,
    collection_id: str,
    name: str = "seeded collection",
    *,
    holding: str | None = None,
) -> None:
    """Put one collection where a route that takes a collection id can find it."""
    statements: list[tuple[str, tuple[object, ...]]] = [
        (_INSERT_COLLECTION, (collection_id, name, sort_key(name)))
    ]
    if holding is not None:
        statements.append((_LINK_ASSET_COLLECTION, (collection_id, holding)))
    _write(db_path, statements)


_INSERT_PHOTO_SET = (
    "INSERT INTO photo_sets (id, name, name_sort, cover_asset_id, origin, created_at) "
    "VALUES (?, ?, ?, NULL, 'manual', 0)"
)

_LINK_ASSET_PHOTO_SET = (
    "INSERT OR IGNORE INTO photo_set_items (photo_set_id, asset_id, position, added_at)"
    " VALUES (?, ?, 0, 0)"
)


def seed_photo_set(
    db_path: Path,
    photo_set_id: str,
    name: str = "seeded photo set",
    *,
    holding: str | None = None,
) -> None:
    """Put one photo set where a route that takes a photo-set id can find it."""
    statements: list[tuple[str, tuple[object, ...]]] = [
        (_INSERT_PHOTO_SET, (photo_set_id, name, sort_key(name)))
    ]
    if holding is not None:
        statements.append((_LINK_ASSET_PHOTO_SET, (photo_set_id, holding)))
    _write(db_path, statements)


_INSERT_SONG = "INSERT INTO songs (id, name, name_sort, created_at, created_by_kind) VALUES (?, ?, ?, 0, 'user')"

#: `added_at` named and bound, as every membership insert names it. Through the song's own table:
#: the Music field follows by the kernel's trigger, exactly as it does for a song put on by hand.
_LINK_ASSET_SONG = (
    "INSERT OR IGNORE INTO song_files (asset_id, song_id, source, added_at) VALUES (?, ?, NULL, 0)"
)


def seed_song(
    db_path: Path,
    song_id: str,
    name: str = "seeded song",
    *,
    holding: str | None = None,
) -> None:
    """Put one song where a route that takes a song id can find it."""
    statements: list[tuple[str, tuple[object, ...]]] = [
        (_INSERT_SONG, (song_id, name, sort_key(name)))
    ]
    if holding is not None:
        statements.append((_LINK_ASSET_SONG, (holding, song_id)))
    _write(db_path, statements)


_INSERT_LOOP = (
    "INSERT INTO loops (id, asset_id, start_ms, end_ms, name, created_by, created_at) "
    "VALUES (?, ?, ?, ?, ?, ?, 0)"
)


def seed_loop(
    db_path: Path,
    loop_id: str,
    asset_id: str,
    *,
    created_by: str | None = None,
    name: str = "seeded loop",
) -> None:
    """Put one marked stretch on a seeded file."""
    _write(db_path, [(_INSERT_LOOP, (loop_id, asset_id, 1000, 4000, name, created_by))])


_INSERT_LOOP_STILL = (
    "INSERT INTO derivatives"
    " (id, asset_id, kind, rel_cache_path, params, content_hash, created_at)"
    " VALUES (?, ?, 'thumb', ?, ?, 'seeded', 0)"
)


def seed_loop_still(db_path: Path, cache_dir: Path, asset_id: str, at_ms: int = 1000) -> None:
    """The picture a marked moment is drawn as: a file in the cache and the row that names it."""
    from sift.kernel.content import DerivativeKind, derivative_relpath, params_key
    from sift.kernel.ids import new_id

    relative = derivative_relpath(
        asset_id, DerivativeKind.THUMB, extension="jpg", params={"at_ms": at_ms}
    )
    destination = cache_dir / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"a seeded still")
    _write(
        db_path,
        [(_INSERT_LOOP_STILL, (new_id(), asset_id, relative, params_key({"at_ms": at_ms})))],
    )


_LINK_ASSET_PERSON = "INSERT OR IGNORE INTO asset_people (asset_id, person_id) VALUES (?, ?)"
_LINK_ASSET_USERNAME = "INSERT OR IGNORE INTO asset_usernames (asset_id, username_id) VALUES (?, ?)"


def attach_person(db_path: Path, asset_id: str, person_id: str) -> None:
    """Put a seeded person on a seeded asset."""
    _write(db_path, [(_LINK_ASSET_PERSON, (asset_id, person_id))])


def attach_tag(db_path: Path, asset_id: str, tag_id: str) -> None:
    """Put a seeded tag on a seeded asset."""
    _write(db_path, [(_LINK_ASSET_TAG, (asset_id, tag_id))])


def attach_username(db_path: Path, asset_id: str, username_id: str) -> None:
    """Put a seeded username on a seeded asset, so the site behind it has something visible."""
    _write(db_path, [(_LINK_ASSET_USERNAME, (asset_id, username_id))])


def seed_alias(db_path: Path, alias_id: str, person_id: str, alias: str = "seeded alias") -> None:
    """One "also known as" on a seeded person, so removing one describes the rule on the route."""
    _write(db_path, [(_INSERT_ALIAS, (alias_id, person_id, alias, sort_key(alias)))])


def seed_link(
    db_path: Path, link_id: str, person_id: str, url: str = "https://seeded.example/x"
) -> None:
    """One address on a seeded person, so removing one describes the rule on the route."""
    _write(db_path, [(_INSERT_LINK, (link_id, person_id, url))])


def seed_site(db_path: Path, site_id: str, name: str = "seeded site") -> None:
    """One site. Left with no usernames on it, so deleting it is not refused for a second reason."""
    _write(db_path, [(_INSERT_SITE, (site_id, name, sort_key(name)))])


def seed_username(
    db_path: Path, username_id: str, site_id: str, name: str = "seeded_username"
) -> None:
    """One username on one site."""
    _write(db_path, [(_INSERT_USERNAME, (username_id, site_id, name, sort_key(name)))])


_INSERT_GUEST = """
INSERT INTO users (id, username, password_hash, pin_hash, role, mk_wrapped, mk_nonce,
                   mk_kdf_salt, created_at, disabled)
VALUES (?, ?, 'x', NULL, 'guest', NULL, NULL, NULL, 0, 0)
"""


def seed_guest_user(db_path: Path, user_id: str, username: str = "seeded guest") -> None:
    """One guest user at a known id, for a route that takes one."""
    _write(db_path, [(_INSERT_GUEST, (user_id, username))])


# Hiding is an upsert: the row may already carry a heart, a rating or a resume point.

_HIDE_ASSET = """
INSERT INTO asset_user_state (asset_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(asset_id, user_id) DO UPDATE SET
  hidden = excluded.hidden, hidden_at = excluded.hidden_at, updated_at = excluded.updated_at
"""

_HIDE_FOLDER = """
INSERT INTO folder_user_state (folder_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(folder_id, user_id) DO UPDATE SET
  hidden = excluded.hidden, hidden_at = excluded.hidden_at, updated_at = excluded.updated_at
"""

_HIDE_ROOT = """
INSERT INTO root_user_state (root_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(root_id, user_id) DO UPDATE SET
  hidden = excluded.hidden, hidden_at = excluded.hidden_at, updated_at = excluded.updated_at
"""

_HIDE_PERSON = """
INSERT INTO person_user_state (person_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(person_id, user_id) DO UPDATE SET
  hidden = excluded.hidden, hidden_at = excluded.hidden_at, updated_at = excluded.updated_at
"""

_HIDE_COLLECTION = """
INSERT INTO collection_user_state (collection_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(collection_id, user_id) DO UPDATE SET
  hidden = excluded.hidden, hidden_at = excluded.hidden_at, updated_at = excluded.updated_at
"""

_HIDE_TAG = """
INSERT INTO tag_user_state (tag_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(tag_id, user_id) DO UPDATE SET
  hidden = excluded.hidden, hidden_at = excluded.hidden_at, updated_at = excluded.updated_at
"""

_HIDE_SITE = """
INSERT INTO site_user_state (site_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(site_id, user_id) DO UPDATE SET
  hidden = excluded.hidden, hidden_at = excluded.hidden_at, updated_at = excluded.updated_at
"""

#: The kinds of thing a user can hide, by the name the tests use for each.
_HIDE_PHOTO_SET = """
INSERT INTO photo_set_user_state (photo_set_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(photo_set_id, user_id) DO UPDATE SET
  hidden = excluded.hidden, hidden_at = excluded.hidden_at, updated_at = excluded.updated_at
"""

_HIDE_SONG = """
INSERT INTO song_user_state (song_id, user_id, hidden, hidden_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(song_id, user_id) DO UPDATE SET
  hidden = excluded.hidden, hidden_at = excluded.hidden_at, updated_at = excluded.updated_at
"""


HIDE_STATEMENTS: dict[str, str] = {
    "asset": _HIDE_ASSET,
    "folder": _HIDE_FOLDER,
    "root": _HIDE_ROOT,
    "person": _HIDE_PERSON,
    "collection": _HIDE_COLLECTION,
    "tag": _HIDE_TAG,
    "site": _HIDE_SITE,
    "photo_set": _HIDE_PHOTO_SET,
    "song": _HIDE_SONG,
}


def hidden_row(kind: str, object_id: str, user_id: str, *, hidden: bool = True) -> _Statement:
    """The statement and parameters that hide one thing from one user."""
    return (
        HIDE_STATEMENTS[kind],
        (object_id, user_id, int(hidden), _HIDDEN_AT if hidden else None, _HIDDEN_AT),
    )


def hide_for(
    db_path: Path, kind: str, object_id: str, user_id: str, *, hidden: bool = True
) -> None:
    """Hide one thing from one user, in a database an application is already running against."""
    _write(db_path, [hidden_row(kind, object_id, user_id, hidden=hidden)])


_INSERT_FACE_SCAN = """
INSERT INTO face_scans
  (asset_id, status, depth, coverage, frames_sampled, track_count, identified_count, detector,
   recognizer, settings_digest, scanned_at)
VALUES (?, 'none_identified', 'fast', 1.0, 1, 1, 0, 'seeded-detector', 'seeded-recognizer',
        'seeded-digest', 0)
ON CONFLICT(asset_id) DO NOTHING
"""

_INSERT_FACE_TRACK = """
INSERT INTO face_tracks
  (id, asset_id, started_ms, ended_ms, seen_in, quality, person_id, confidence, attribution,
   pile_id, created_at)
VALUES (?, ?, 0, 0, 1, 0.8, NULL, NULL, NULL, NULL, 0)
"""

_INSERT_FACE_DETECTION = """
INSERT INTO face_detections
  (id, track_id, timestamp_ms, box_x, box_y, box_w, box_h, score, quality, crop_path, crop_digest,
   embedding, created_at)
VALUES (?, ?, 0, 0, 0, 64, 64, 0.9, 0.8, ?, 'seeded-digest', X'0000803F', 0)
"""


def seed_face(db_path: Path, track_id: str, asset_id: str, *, data_dir: Path) -> None:
    """Put one found face on an asset, with a picture on the disk behind it."""
    stored = f"detected/{track_id[:2]}/{track_id}.jpg"
    picture = data_dir / "faces" / stored
    picture.parent.mkdir(parents=True, exist_ok=True)
    picture.write_bytes(b"\xff\xd8\xff\xdb seeded face")
    _write(
        db_path,
        [
            (_INSERT_FACE_SCAN, (asset_id,)),
            (_INSERT_FACE_TRACK, (track_id, asset_id)),
            (_INSERT_FACE_DETECTION, (f"{track_id}D", track_id, stored)),
        ],
    )


def set_media_shape(
    db_path: Path,
    asset_id: str,
    *,
    width: int,
    height: int,
    duration_ms: int,
    fps: float,
    size_bytes: int,
    vcodec: str = "hevc",
    acodec: str | None = "aac",
    container: str = "mp4",
) -> None:
    """Give a seeded asset the shape of a real video, without a real video."""
    _write(
        db_path,
        [
            (
                "UPDATE assets SET width = ?, height = ?, duration_ms = ?, fps = ?,"
                " size_bytes = ?, vcodec = ?, acodec = ?, container = ? WHERE id = ?",
                (
                    width,
                    height,
                    duration_ms,
                    fps,
                    size_bytes,
                    vcodec,
                    acodec,
                    container,
                    asset_id,
                ),
            )
        ],
    )


_INSERT_DECISION = (
    "INSERT INTO workbench_decisions"
    " (id, queue, user_id, title, detail, payload, decided_at, reversed_at)"
    " VALUES (?, 'folders', NULL, 'Seeded', 'Seeded.', '{}', 0, NULL)"
)


def seed_decision(db_path: Path, decision_id: str) -> None:
    """Record one decision, so a route that takes a decision id has something to find."""
    _write(db_path, [(_INSERT_DECISION, (decision_id,))])


_INSERT_ART = "INSERT INTO site_art (scope, path, stored_at) VALUES (?, ?, ?)"


def seed_art(db_path: Path, scope: str, *, cache_dir: Path) -> None:
    """Keep a picture for one site or one creator, with a file on the disk behind it."""
    name = f"{scope.replace(':', '_')}.jpg"
    picture = cache_dir / "covers" / name
    picture.parent.mkdir(parents=True, exist_ok=True)
    picture.write_bytes(b"\xff\xd8\xff seeded art")
    _write(db_path, [(_INSERT_ART, (scope, f"covers/{name}", 0))])


#: An upload, because a chosen frame is built later and answers 404 until it is.
_INSERT_COVER_PICTURE = (
    "INSERT INTO cover_pictures (id, rel_cache_path, size_bytes, created_at) VALUES (?, ?, ?, 0)"
)

#: Written out, never formatted from the kind: no SQL is built from strings.
_SET_COVER_UPLOAD = {
    "people": "UPDATE people SET cover_upload_id = ? WHERE id = ?",
    "sites": "UPDATE sites SET cover_upload_id = ? WHERE id = ?",
    "tags": "UPDATE tags SET cover_upload_id = ? WHERE id = ?",
    "collections": "UPDATE collections SET cover_upload_id = ? WHERE id = ?",
    "photo_sets": "UPDATE photo_sets SET cover_upload_id = ? WHERE id = ?",
    "songs": "UPDATE songs SET cover_upload_id = ? WHERE id = ?",
}


def seed_cover_picture(
    db_path: Path, upload_id: str, *, cache_dir: Path, wearing: dict[str, str] | None = None
) -> None:
    """An uploaded cover, and optionally the rows that wear it."""
    relative = Path("covers") / f"{upload_id}.jpg"
    picture = cache_dir / relative
    picture.parent.mkdir(parents=True, exist_ok=True)
    picture.write_bytes(b"\xff\xd8\xff\xe0 seeded cover")
    # Annotated rather than inferred: a list takes its type from its first element, and the
    # pointer rows below bind two values where this one binds three.
    writes: list[tuple[str, tuple[object, ...]]] = [
        (_INSERT_COVER_PICTURE, (upload_id, relative.as_posix(), picture.stat().st_size))
    ]
    for table, row_id in (wearing or {}).items():
        writes.append((_SET_COVER_UPLOAD[table], (upload_id, row_id)))
    _write(db_path, writes)


def a_png(width: int = 8, height: int = 8) -> bytes:
    """A real, minimal PNG, built here rather than shipped as a binary fixture."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit truecolour
    rows = b"".join(b"\x00" + b"\x7f\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


_INSERT_STASH_BOX = (
    "INSERT INTO stash_boxes"
    " (id, name, endpoint, secret_id, enabled, route, requests_per_minute, created_at)"
    " VALUES (?, ?, ?, NULL, 1, NULL, 240, 0)"
)


_INSERT_PERSON_STASH_BOX_LINK = (
    "INSERT INTO person_stash_box_links (person_id, box_id, remote_id, payload, fetched_at)"
    " VALUES (?, ?, ?, ?, 0) ON CONFLICT(person_id, box_id) DO NOTHING"
)


def seed_stash_box_link(
    db_path: Path,
    person_id: str,
    box_id: str,
    *,
    remote_id: str = "seeded-remote-id",
) -> None:
    """Agree that one box knows one person, without asking the box."""
    _write(db_path, [(_INSERT_PERSON_STASH_BOX_LINK, (person_id, box_id, remote_id, "{}"))])


def seed_stash_box(
    db_path: Path,
    box_id: str,
    *,
    name: str = "seeded stash-box",
    endpoint: str = "https://stash-box.invalid/graphql",
) -> None:
    """Configure one stash-box, with no key."""
    _write(db_path, [(_INSERT_STASH_BOX, (box_id, name, endpoint))])


def seed_quarantined(data_dir: Path, name: str, *, reason: str = "not_decodable") -> Path:
    """One file in the quarantine directory, with the note the ingress gate writes beside it."""
    import json

    from sift.kernel.ingress import NOTE_SUFFIX

    directory = data_dir / "quarantine"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_bytes(b"refused bytes")
    target.with_name(target.name + NOTE_SUFFIX).write_text(
        json.dumps({"original_name": name, "reason": reason, "origin": "download"}),
        encoding="utf-8",
    )
    return target
