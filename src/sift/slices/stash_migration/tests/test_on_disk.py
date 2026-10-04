# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Stash library as Stash lays one out on disk, read as it spells its paths: a zip gallery's
pictures placed in this library, from a Stash on Windows as from one on Linux, and a picture
kept in its blobs folder."""

from __future__ import annotations

from pathlib import Path

from sift.slices.stash_migration import reader
from sift.slices.stash_migration.service import _map_path
from sift.slices.stash_migration.tests.stash_on_disk import (
    PICTURES,
    TAG_PICTURE,
    ZIP_GALLERY,
    a_picture,
    lay_out,
)


def test_a_stash_path_is_read_as_the_system_that_wrote_it_spells_it(tmp_path: Path) -> None:
    here = tmp_path / "Harbor"
    windows = _map_path("C:\\Stash\\Clips\\set.zip\\inside.jpg", {"c:\\stash\\clips": str(here)})
    posix = _map_path("/media/stash/Clips/set.zip/inside.jpg", {"/media/stash/Clips": str(here)})

    assert windows == posix == here / "set.zip" / "inside.jpg"
    assert reader.stash_path("C:\\Stash\\Clips").name == "Clips"
    assert reader.stash_path("/media/stash/Clips").name == "Clips"
    assert _map_path("D:\\Elsewhere\\one.jpg", {"C:\\Stash\\Clips": str(here)}) is None


def test_a_zip_gallery_and_a_blobs_folder_laid_out_by_stash_are_read_where_they_are(
    tmp_path: Path,
) -> None:
    top = tmp_path / "Library"
    database = lay_out(top)
    connection = reader.open_copy(database)
    try:
        images = list(reader.images(connection))
        galleries = reader.galleries(connection)
        blob = reader.picture_of(connection, TAG_PICTURE, database.parent / "blobs")
    finally:
        connection.close()

    assert [(one.name, len(one.images)) for one in galleries] == [("harbor", PICTURES)]
    assert all(one.files[0].in_zip for one in images)
    placed = {_map_path(one.files[0].path, {str(top): str(top)}) for one in images}
    assert placed == {top / ZIP_GALLERY / f"{number:02d}.png" for number in range(1, 13)}
    assert blob == a_picture(96, 96, 200)
