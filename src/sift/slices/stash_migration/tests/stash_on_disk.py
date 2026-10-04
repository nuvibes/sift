# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Stash library laid out on disk as Stash lays one out: a zip gallery of real pictures beside
its database, and a tag's picture kept as a file in its blobs folder. For a scan and a run to
read as they would a person's; the names are this project's invented cast.

`python -m sift.slices.stash_migration.tests.stash_on_disk <folder>` writes it into a folder.
"""

from __future__ import annotations

import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel (builds a foreign database file that is not a Sift library)
import struct
import sys
import zipfile
import zlib
from pathlib import Path

from sift.slices.stash_migration.tests.stash_fixture import _TABLES

#: The zip gallery, its pictures, and the tag they wear whose picture Stash keeps as a file.
ZIP_GALLERY = "harbor.zip"
PICTURES = 12
TAG = "Seaglass"
TAG_PICTURE = "5e1a6b2c3d4e5f60718293a4b5c6d7e8"


def a_picture(width: int, height: int, shade: int) -> bytes:
    """A real PNG of a gradient, different for every shade, so no two are one file."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(
        b"\x00" + bytes((x * 4 + shade) % 256 for x in range(width) for _ in range(3))
        for _ in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def lay_out(top: Path) -> Path:
    """Write the zip, Stash's database (`top/stash/stash-go.sqlite`) and its blobs folder under
    `top`, Stash's paths spelt as this system spells them. Answers the database's path."""
    top.mkdir(parents=True, exist_ok=True)
    names = [f"{number:02d}.png" for number in range(1, PICTURES + 1)]
    with zipfile.ZipFile(top / ZIP_GALLERY, "w") as writing:
        for number, name in enumerate(names, start=1):
            writing.writestr(name, a_picture(64 + number * 8, 48, number * 19))
    stash = top / "stash"
    blob = stash / "blobs" / TAG_PICTURE[:2] / TAG_PICTURE[2:4] / TAG_PICTURE
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(a_picture(96, 96, 200))
    database = stash / "stash-go.sqlite"
    database.unlink(missing_ok=True)
    rows: list[tuple[str, tuple[object, ...]]] = [
        ("INSERT INTO schema_migrations VALUES (85, 0)", ()),
        ("INSERT INTO folders VALUES (1, ?, NULL, NULL)", (str(top),)),
        ("INSERT INTO files (id, basename, parent_folder_id) VALUES (1, ?, 1)", (ZIP_GALLERY,)),
        ("INSERT INTO folders VALUES (2, ?, 1, 1)", (str(top / ZIP_GALLERY),)),
        ("INSERT INTO galleries (id, folder_id, title) VALUES (1, NULL, NULL)", ()),
        ("INSERT INTO galleries_files VALUES (1, 1, 1)", ()),
        ("INSERT INTO tags (id, name, image_blob) VALUES (1, ?, ?)", (TAG, TAG_PICTURE)),
        ("INSERT INTO blobs VALUES (?, NULL)", (TAG_PICTURE,)),
    ]
    for number, name in enumerate(names, start=1):
        rows += [
            (
                "INSERT INTO files (id, basename, zip_file_id, parent_folder_id) VALUES (?, ?, 1, 2)",
                (10 + number, name),
            ),
            ("INSERT INTO images (id, title) VALUES (?, NULL)", (number,)),
            ("INSERT INTO images_files VALUES (?, ?, 1)", (number, 10 + number)),
            ("INSERT INTO galleries_images VALUES (1, ?, 0)", (number,)),
            ("INSERT INTO images_tags VALUES (?, 1)", (number,)),
        ]
    connection = sqlite3.connect(database)
    try:
        for statement in _TABLES:
            connection.execute(statement)
        for sql, params in rows:
            # Every statement is a literal from the list above, its values bound as `?`.
            # nosemgrep: sift-no-string-built-sql
            connection.execute(sql, params)
        connection.commit()
    finally:
        connection.close()
    return database


if __name__ == "__main__":
    print(lay_out(Path(sys.argv[1])))
