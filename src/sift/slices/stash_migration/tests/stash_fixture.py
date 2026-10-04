# SPDX-License-Identifier: AGPL-3.0-or-later
"""A small database shaped like Stash's (schema 85), holding the few rows a test names.

Only the tables and columns the reader reads, with Stash's own names, so a test can say what a
Stash library holds without a copy of anybody's. The names are this project's invented cast.
"""

from __future__ import annotations

import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel (builds a foreign database file that is not a Sift library)
from pathlib import Path

_TABLES = (
    "CREATE TABLE schema_migrations (version INTEGER, dirty INTEGER)",
    "CREATE TABLE performers (id INTEGER PRIMARY KEY, name TEXT, disambiguation TEXT, gender TEXT,"
    " birthdate TEXT, ethnicity TEXT, country TEXT, eye_color TEXT, height INTEGER,"
    " measurements TEXT, fake_tits TEXT, tattoos TEXT, piercings TEXT, favorite INTEGER DEFAULT 0,"
    " hair_color TEXT, rating INTEGER, career_start TEXT, career_end TEXT, details TEXT,"
    " death_date TEXT, weight INTEGER, penis_length REAL, circumcised TEXT, image_blob TEXT)",
    "CREATE TABLE performer_aliases (performer_id INTEGER, alias TEXT)",
    "CREATE TABLE performer_urls (performer_id INTEGER, position INTEGER, url TEXT)",
    "CREATE TABLE studios (id INTEGER PRIMARY KEY, name TEXT, parent_id INTEGER, rating INTEGER,"
    " favorite INTEGER DEFAULT 0, details TEXT, organized INTEGER DEFAULT 0, image_blob TEXT)",
    "CREATE TABLE studio_aliases (studio_id INTEGER, alias TEXT)",
    "CREATE TABLE studio_urls (studio_id INTEGER, position INTEGER, url TEXT)",
    "CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT, description TEXT,"
    " favorite INTEGER DEFAULT 0, image_blob TEXT, sort_name TEXT)",
    "CREATE TABLE tag_aliases (tag_id INTEGER, alias TEXT)",
    "CREATE TABLE tags_relations (parent_id INTEGER, child_id INTEGER)",
    "CREATE TABLE folders (id INTEGER PRIMARY KEY, path TEXT, parent_folder_id INTEGER,"
    " zip_file_id INTEGER)",
    "CREATE TABLE files (id INTEGER PRIMARY KEY, basename TEXT, zip_file_id INTEGER,"
    " parent_folder_id INTEGER, size INTEGER)",
    "CREATE TABLE video_files (file_id INTEGER PRIMARY KEY, duration REAL)",
    "CREATE TABLE files_fingerprints (file_id INTEGER, type TEXT, fingerprint)",
    "CREATE TABLE scenes (id INTEGER PRIMARY KEY, title TEXT, details TEXT, date TEXT,"
    " rating INTEGER, studio_id INTEGER, code TEXT, organized INTEGER DEFAULT 0, director TEXT,"
    " resume_time REAL NOT NULL DEFAULT 0, play_duration REAL NOT NULL DEFAULT 0, cover_blob TEXT)",
    'CREATE TABLE scenes_files (scene_id INTEGER, file_id INTEGER, "primary" INTEGER)',
    "CREATE TABLE scene_urls (scene_id INTEGER, position INTEGER, url TEXT)",
    "CREATE TABLE scenes_o_dates (scene_id INTEGER, o_date TEXT)",
    "CREATE TABLE scenes_view_dates (scene_id INTEGER, view_date TEXT)",
    "CREATE TABLE performers_scenes (performer_id INTEGER, scene_id INTEGER)",
    "CREATE TABLE scenes_tags (scene_id INTEGER, tag_id INTEGER)",
    "CREATE TABLE images (id INTEGER PRIMARY KEY, title TEXT, rating INTEGER, studio_id INTEGER,"
    " o_counter INTEGER DEFAULT 0, date TEXT, code TEXT, details TEXT, organized INTEGER DEFAULT 0,"
    " photographer TEXT)",
    'CREATE TABLE images_files (image_id INTEGER, file_id INTEGER, "primary" INTEGER)',
    "CREATE TABLE performers_images (performer_id INTEGER, image_id INTEGER)",
    "CREATE TABLE images_tags (image_id INTEGER, tag_id INTEGER)",
    "CREATE TABLE galleries (id INTEGER PRIMARY KEY, folder_id INTEGER, title TEXT)",
    "CREATE TABLE galleries_images (gallery_id INTEGER, image_id INTEGER, cover INTEGER)",
    "CREATE TABLE scene_markers (id INTEGER PRIMARY KEY, title TEXT, seconds REAL,"
    " primary_tag_id INTEGER, scene_id INTEGER, end_seconds REAL)",
    "CREATE TABLE scene_markers_tags (scene_marker_id INTEGER, tag_id INTEGER)",
    "CREATE TABLE saved_filters (id INTEGER PRIMARY KEY, name TEXT, mode TEXT, find_filter BLOB,"
    " object_filter BLOB, ui_options BLOB)",
    'CREATE TABLE galleries_files (gallery_id INTEGER, file_id INTEGER, "primary" INTEGER)',
    "CREATE TABLE performer_stash_ids (performer_id INTEGER, endpoint TEXT, stash_id TEXT)",
    "CREATE TABLE studio_stash_ids (studio_id INTEGER, endpoint TEXT, stash_id TEXT)",
    "CREATE TABLE tag_stash_ids (tag_id INTEGER, endpoint TEXT, stash_id TEXT)",
    "CREATE TABLE scene_stash_ids (scene_id INTEGER, endpoint TEXT, stash_id TEXT)",
    "CREATE TABLE blobs (checksum TEXT PRIMARY KEY, blob BLOB)",
    "CREATE TABLE performers_tags (performer_id INTEGER, tag_id INTEGER)",
    "CREATE TABLE studios_tags (studio_id INTEGER, tag_id INTEGER)",
    "CREATE TABLE image_urls (image_id INTEGER, position INTEGER, url TEXT)",
    "CREATE TABLE video_captions (file_id INTEGER, language_code TEXT, filename TEXT,"
    " caption_type TEXT)",
    "CREATE TABLE groups (id INTEGER PRIMARY KEY, name TEXT NOT NULL, director TEXT,"
    " front_image_blob TEXT)",
    "CREATE TABLE groups_scenes (group_id INTEGER, scene_id INTEGER, scene_index INTEGER)",
    *(
        f"CREATE TABLE {kind}_custom_fields ({kind}_id INTEGER, field TEXT, value BLOB)"
        for kind in ("performer", "studio", "tag", "scene", "image")
    ),
)

#: The stash-box every id in the fixture names, by its address.
BOX = "https://stash-box.example/graphql"

#: The zip the fixture's zipped picture sits in, beside the scenes.
ZIP = "set.zip"

#: The folder Stash scans, as Stash spells it.
TOP = "/media/stash/Clips"

#: The OSHash of the one scene a test finds by fingerprint alone.
OSHASH = "00ffee1122334455"

#: The third scene's fingerprints, for the file that arrives after a run (`waiting=True`): its
#: OSHash, and its video fingerprint as Stash stores one (a signed 64-bit number) and as Sift does.
WAITING_OSHASH = "1234abcd5678ef90"
WAITING_PHASH = -2
WAITING_PHASH_HEX = "fffffffffffffffe"

#: The one person, Site and tag only the waiting scene carries.
WAITING_PERSON = "Jane Moe"
WAITING_SITE = "Moe Studio"
WAITING_TAG = "Rooftop"


#: The gallery `gallery=N` adds, and the name of its Nth picture's file.
GALLERY = "Poolside"


def picture(number: int) -> str:
    return f"pool-{number:02d}.jpg"


#: The person, Site and tag `extras=True` adds that Stash attaches to nothing, and the group.
ALONE_PERSON = "Jane Alone"
ALONE_SITE = "Lone Studio"
ALONE_TAG = "Unused"
#: The tag only the first person wears, on no file.
WORN_TAG = "Freckles"
GROUP = "Poolside Movie"

#: A picture Stash keeps in its database, and one it keeps as a file in its blobs folder.
IN_DATABASE = "aa11bb22cc33dd44ee55ff6677889900"
IN_FOLDER = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"


def a_png() -> bytes:
    """The smallest picture ffmpeg reads: one pixel, as a PNG."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\x80\x00")
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")
    )


def blobs_folder(folder: Path) -> Path:
    """Stash's blobs folder with the one picture `extras=True` keeps as a file, where Stash puts
    it (two folders named by the checksum's first two pairs of characters)."""
    place = folder / IN_FOLDER[:2] / IN_FOLDER[2:4]
    place.mkdir(parents=True, exist_ok=True)
    (place / IN_FOLDER).write_bytes(a_png())
    return folder


def make_stash(
    path: Path,
    *,
    version: int = 85,
    dirty: int = 0,
    waiting: bool = False,
    gallery: int = 0,
    extras: bool = False,
) -> Path:
    """Write the fixture: two People, a Site under a network, four tags (one with two parents),
    two scenes, a picture and a picture in a zip, three markers, three saved filters and the
    stash-box ids of a person, a Site and a scene. Answers the path.

    `waiting` adds a third scene whose file no test library holds at first, with a person, a Site
    and a tag nothing else carries: the third marker is on it. `gallery` adds a gallery of that
    many pictures beside the scenes (`picture(n)`, numbered from one). `extras` adds what Sift has
    no field for (a director, a weight, custom fields, captions), a resume point and time watched,
    a tag a person wears, pictures (one in the database, one in the blobs folder), a group of the
    two scenes, and a person, a Site and a tag Stash attaches to nothing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        for statement in _TABLES:
            connection.execute(statement)
        rows: list[tuple[str, tuple[object, ...]]] = [
            ("INSERT INTO schema_migrations VALUES (?, ?)", (version, dirty)),
            (
                "INSERT INTO performers (id, name, gender, country, height, fake_tits, favorite,"
                " rating, career_start) VALUES (1, 'Jane Doe', 'FEMALE', 'NZ', 170, 'Natural', 1,"
                " 80, '2019-01-01')",
                (),
            ),
            ("INSERT INTO performers (id, name) VALUES (2, 'Jane Roe')", ()),
            ("INSERT INTO performer_aliases VALUES (1, 'Jane Doh')", ()),
            ("INSERT INTO studios (id, name) VALUES (1, 'Jane Doe Videos')", ()),
            (
                "INSERT INTO studios (id, name, parent_id, favorite, rating) VALUES"
                " (2, 'Another Studio', 1, 1, 60)",
                (),
            ),
            (
                "INSERT INTO tags (id, name) VALUES (1, 'Time of day'), (2, 'Evening'), (3, 'Outdoors')",
                (),
            ),
            ("INSERT INTO tags (id, name, favorite) VALUES (4, 'Beach', 1)", ()),
            ("INSERT INTO tags_relations VALUES (1, 2), (3, 4), (1, 4)", ()),
            ("INSERT INTO tag_aliases VALUES (2, 'Eventide')", ()),
            ("INSERT INTO folders VALUES (1, ?, NULL, NULL)", (TOP,)),
            (
                "INSERT INTO files (id, basename, zip_file_id, parent_folder_id, size) VALUES"
                " (1, 'first.mp4', NULL, 1, 1000), (2, 'second.mp4', NULL, 1, 2000)",
                (),
            ),
            (
                "INSERT INTO files (id, basename, zip_file_id, parent_folder_id)"
                " VALUES (3, 'still.jpg', NULL, 1)",
                (),
            ),
            # A zip beside the scenes, with one picture in it: Stash files the picture under a
            # folder whose path is the zip's own.
            (
                "INSERT INTO files (id, basename, zip_file_id, parent_folder_id)"
                " VALUES (4, ?, NULL, 1)",
                (ZIP,),
            ),
            ("INSERT INTO folders VALUES (2, ?, 1, 4)", (f"{TOP}/{ZIP}",)),
            (
                "INSERT INTO files (id, basename, zip_file_id, parent_folder_id)"
                " VALUES (5, 'inside.jpg', 4, 2)",
                (),
            ),
            ("INSERT INTO images (id, title) VALUES (2, 'Zipped')", ()),
            ("INSERT INTO images_files VALUES (2, 5, 1)", ()),
            ("INSERT INTO galleries (id, folder_id, title) VALUES (1, NULL, NULL)", ()),
            ("INSERT INTO galleries_files VALUES (1, 4, 1)", ()),
            ("INSERT INTO galleries_images VALUES (1, 2, 0)", ()),
            ("INSERT INTO files_fingerprints VALUES (2, 'oshash', ?)", (OSHASH,)),
            (
                "INSERT INTO scenes (id, title, date, rating, studio_id) VALUES"
                " (1, 'By the pool', '2021-05-04', 100, 2), (2, 'Found by its hash', NULL, 20, NULL)",
                (),
            ),
            ("INSERT INTO scenes_files VALUES (1, 1, 1), (2, 2, 1)", ()),
            ("INSERT INTO scenes_o_dates VALUES (1, '2021-01-01'), (1, '2021-01-02')", ()),
            ("INSERT INTO scenes_view_dates VALUES (1, '2021-01-01')", ()),
            ("INSERT INTO performers_scenes VALUES (1, 1), (2, 1)", ()),
            ("INSERT INTO scenes_tags VALUES (1, 2), (1, 4)", ()),
            ("INSERT INTO images (id, title) VALUES (1, 'A still')", ()),
            ("INSERT INTO images_files VALUES (1, 3, 1)", ()),
            ("INSERT INTO images_tags VALUES (1, 3)", ()),
            # A moment (no end) and a stretch, both on the scene the library holds, and one on the
            # scene it does not.
            ("INSERT INTO scene_markers VALUES (1, '', 12.0, 2, 1, NULL)", ()),
            ("INSERT INTO scene_markers VALUES (2, 'The jump', 30.0, 3, 1, 45.5)", ()),
            ("INSERT INTO scene_markers_tags VALUES (2, 4)", ()),
            ("INSERT INTO scene_markers VALUES (3, 'Elsewhere', 5.0, 2, 3, 9.0)", ()),
            (
                "INSERT INTO saved_filters (id, name, mode, find_filter, object_filter) VALUES"
                " (1, 'Beach days', 'SCENES', ?, ?),"
                " (2, 'Not organized', 'SCENES', NULL, ?),"
                " (3, 'Favorite people', 'PERFORMERS', NULL, ?)",
                (
                    b'{"q":"","sort":"date"}',
                    b'{"tags":{"modifier":"INCLUDES","value":{"depth":0,"items":'
                    b'[{"id":4,"label":"Beach"}]}},"rating100":{"modifier":"GREATER_THAN",'
                    b'"value":{"value":60}}}',
                    b'{"organized":{"modifier":"EQUALS","value":"false"}}',
                    b'{"filter_favorites":{"modifier":"EQUALS","value":"true"}}',
                ),
            ),
            ("INSERT INTO performer_stash_ids VALUES (1, ?, 'remote-person')", (BOX,)),
            ("INSERT INTO studio_stash_ids VALUES (2, ?, 'remote-site')", (BOX,)),
            ("INSERT INTO scene_stash_ids VALUES (1, ?, 'remote-scene')", (BOX,)),
        ]
        if waiting:
            rows += [
                (
                    "INSERT INTO performers (id, name, favorite, rating) VALUES (3, ?, 1, 60)",
                    (WAITING_PERSON,),
                ),
                ("INSERT INTO performer_aliases VALUES (3, 'Jane Mow')", ()),
                ("INSERT INTO performer_stash_ids VALUES (3, ?, 'remote-waiting')", (BOX,)),
                ("INSERT INTO studios (id, name) VALUES (3, ?)", (WAITING_SITE,)),
                ("INSERT INTO tags (id, name) VALUES (5, ?)", (WAITING_TAG,)),
                (
                    "INSERT INTO files (id, basename, zip_file_id, parent_folder_id, size)"
                    " VALUES (6, 'third.mp4', NULL, 1, 3000)",
                    (),
                ),
                ("INSERT INTO video_files VALUES (6, 61.5)", ()),
                ("INSERT INTO files_fingerprints VALUES (6, 'oshash', ?)", (WAITING_OSHASH,)),
                ("INSERT INTO files_fingerprints VALUES (6, 'phash', ?)", (WAITING_PHASH,)),
                (
                    "INSERT INTO scenes (id, title, date, rating, studio_id) VALUES"
                    " (3, 'Waiting on the roof', '2022-02-02', 90, 3)",
                    (),
                ),
                ("INSERT INTO scenes_files VALUES (3, 6, 1)", ()),
                ("INSERT INTO scenes_o_dates VALUES (3, '2022-02-03')", ()),
                ("INSERT INTO performers_scenes VALUES (3, 3)", ()),
                ("INSERT INTO scenes_tags VALUES (3, 5)", ()),
            ]
        if gallery:
            rows.append(
                ("INSERT INTO galleries (id, folder_id, title) VALUES (2, NULL, ?)", (GALLERY,))
            )
            for number in range(1, gallery + 1):
                rows += [
                    (
                        "INSERT INTO files (id, basename, zip_file_id, parent_folder_id)"
                        " VALUES (?, ?, NULL, 1)",
                        (100 + number, picture(number)),
                    ),
                    ("INSERT INTO images (id, title) VALUES (?, NULL)", (100 + number,)),
                    ("INSERT INTO images_files VALUES (?, ?, 1)", (100 + number, 100 + number)),
                    ("INSERT INTO galleries_images VALUES (2, ?, 0)", (100 + number,)),
                ]
        if extras:
            rows += [
                (
                    "UPDATE scenes SET director = 'Ada Byron', organized = 1, resume_time = 42.5,"
                    " play_duration = 300.0 WHERE id = 1",
                    (),
                ),
                ("INSERT INTO scene_custom_fields VALUES (1, 'mood', 'calm')", ()),
                ("INSERT INTO video_captions VALUES (1, 'en', 'first.en.srt', 'srt')", ()),
                (
                    "UPDATE performers SET weight = 55, details = 'Swims.', image_blob = ?"
                    " WHERE id = 1",
                    (IN_DATABASE,),
                ),
                ("INSERT INTO performer_custom_fields VALUES (1, 'shoe', '38')", ()),
                ("INSERT INTO tags (id, name) VALUES (10, ?)", (WORN_TAG,)),
                ("INSERT INTO performers_tags VALUES (1, 10)", ()),
                ("INSERT INTO blobs VALUES (?, ?)", (IN_DATABASE, a_png())),
                ("INSERT INTO blobs VALUES (?, NULL)", (IN_FOLDER,)),
                ("UPDATE studios SET image_blob = ? WHERE id = 2", (IN_FOLDER,)),
                ("INSERT INTO groups (id, name) VALUES (1, ?)", (GROUP,)),
                ("INSERT INTO groups_scenes VALUES (1, 2, 0), (1, 1, 1)", ()),
                (
                    "INSERT INTO performers (id, name, favorite, rating) VALUES (9, ?, 1, 40)",
                    (ALONE_PERSON,),
                ),
                ("INSERT INTO performer_stash_ids VALUES (9, ?, 'remote-alone')", (BOX,)),
                ("INSERT INTO studios (id, name, favorite) VALUES (9, ?, 1)", (ALONE_SITE,)),
                ("INSERT INTO tags (id, name) VALUES (9, ?)", (ALONE_TAG,)),
            ]
        for sql, params in rows:
            # Every statement is a literal from the fixture's own list above, its values bound as `?`.
            # nosemgrep: sift-no-string-built-sql
            connection.execute(sql, params)
        connection.commit()
    finally:
        connection.close()
    return path
