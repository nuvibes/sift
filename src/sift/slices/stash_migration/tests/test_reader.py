# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a Stash database: the copy, the version it must be, and what is counted and read."""

from __future__ import annotations

import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel (the reader's own connection type)
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from sift.slices.stash_migration import reader
from sift.slices.stash_migration.tests.stash_fixture import (
    BOX,
    GALLERY,
    GROUP,
    IN_DATABASE,
    IN_FOLDER,
    OSHASH,
    TOP,
    WAITING_OSHASH,
    WAITING_PHASH_HEX,
    WORN_TAG,
    ZIP,
    a_png,
    blobs_folder,
    make_stash,
)

pytestmark = pytest.mark.unit


def _read(tmp_path: Path, **made: Any) -> reader.Summary:
    copy = tmp_path / "copy.sqlite"
    reader.copy_in(make_stash(tmp_path / "stash.sqlite", **made), copy)
    connection = reader.open_copy(copy)
    try:
        return reader.summarize(connection)
    finally:
        connection.close()


def test_the_copy_can_be_read_from_another_thread(tmp_path: Path) -> None:
    """A run reads the copy through the worker pool, which may hand each call its own thread."""
    copy = tmp_path / "copy.sqlite"
    reader.copy_in(make_stash(tmp_path / "stash.sqlite"), copy)
    connection = reader.open_copy(copy)
    try:
        with ThreadPoolExecutor(max_workers=1) as other:
            summary = other.submit(reader.summarize, connection).result()
        assert summary.version == reader.version_of(connection)
    finally:
        connection.close()


def test_a_copy_is_read_and_every_count_is_the_databases_own(tmp_path: Path) -> None:
    summary = _read(tmp_path)

    assert (summary.version, summary.people, summary.sites, summary.sites_with_a_parent) == (
        85,
        2,
        2,
        1,
    )
    assert (summary.tags, summary.tags_with_one_parent, summary.tags_with_several_parents) == (
        4,
        1,
        1,
    )
    assert (summary.scenes, summary.images, summary.rated_scenes) == (2, 2, 2)
    assert (summary.images_in_zips, summary.galleries) == (1, 1)
    assert (summary.scenes_with_os, summary.scenes_viewed) == (1, 1)
    assert (summary.markers_with_an_end, summary.markers_without_an_end) == (2, 1)
    # The zip is a file of the folder; the picture inside it is not counted as one.
    assert summary.folders == [reader.TopFolder(TOP, 4)]
    assert (summary.favorite_people, summary.favorite_sites, summary.favorite_tags) == (1, 1, 1)
    assert (summary.rated_people, summary.rated_sites) == (1, 1)
    assert (
        summary.people_with_a_box_id,
        summary.sites_with_a_box_id,
        summary.tags_with_a_box_id,
        summary.scenes_with_a_box_id,
    ) == (1, 1, 0, 1)
    assert (summary.saved_filters, summary.filters_over_files) == (3, 2)


@pytest.mark.parametrize(
    ("made", "said"), [({"version": 60}, "version 60"), ({"dirty": 1}, "part way")]
)
def test_a_stash_this_does_not_read_is_refused_with_why(
    tmp_path: Path, made: dict[str, int], said: str
) -> None:
    with pytest.raises(reader.NotAStashDatabase, match=said):
        _read(tmp_path, **made)


def test_a_tag_keeps_a_parent_only_where_stash_gives_it_exactly_one(tmp_path: Path) -> None:
    copy = tmp_path / "copy.sqlite"
    reader.copy_in(make_stash(tmp_path / "stash.sqlite"), copy)
    connection = reader.open_copy(copy)
    try:
        parents = {one.name: one.parent for one in reader.tags(connection)}
        several = reader.tags_with_several_parents(connection)
    finally:
        connection.close()

    assert parents == {
        "Time of day": None,
        "Evening": "Time of day",
        "Outdoors": None,
        "Beach": None,
    }
    assert several == ["Beach"]


def test_a_scene_is_read_in_the_words_a_file_is_written_in(tmp_path: Path) -> None:
    copy = tmp_path / "copy.sqlite"
    reader.copy_in(make_stash(tmp_path / "stash.sqlite"), copy)
    connection = reader.open_copy(copy)
    try:
        first, second = reader.scenes(connection)
    finally:
        connection.close()

    assert first.fields == {
        "title": "By the pool",
        "release_date": "2021-05-04",
        "site": "Another Studio",
        "people": ["Jane Doe", "Jane Roe"],
        "tags": ["Beach", "Evening"],
    }
    assert (first.rating, first.o_count, first.views) == (10, 2, 1)
    assert first.files[0].path == f"{TOP}/first.mp4"
    assert (second.rating, second.files[0].oshash) == (2, OSHASH)


def test_ratings_and_fingerprints_are_spelt_as_sift_spells_them() -> None:
    assert [reader.stars(one) for one in (None, 1, 20, 55, 100)] == [None, 1, 2, 6, 10]
    assert reader.phash_hex(-1) == "ffffffffffffffff"
    assert reader.phash_hex(255) == "00000000000000ff"


def _open(tmp_path: Path) -> sqlite3.Connection:
    copy = tmp_path / "copy.sqlite"
    reader.copy_in(make_stash(tmp_path / "stash.sqlite"), copy)
    return reader.open_copy(copy)


def test_a_marker_is_read_with_its_end_or_as_a_moment_and_its_tags_by_name(
    tmp_path: Path,
) -> None:
    connection = _open(tmp_path)
    try:
        found = list(reader.markers(connection))
    finally:
        connection.close()

    assert found[0] == reader.Marker(1, "", 12.0, None, ("Evening",))
    assert found[1] == reader.Marker(1, "The jump", 30.0, 45.5, ("Outdoors", "Beach"))
    assert [one.scene_id for one in found] == [1, 1, 3]


def test_the_stash_box_ids_are_read_per_kind_with_the_box_by_its_address(tmp_path: Path) -> None:
    connection = _open(tmp_path)
    try:
        people = reader.box_ids(connection, "performer")
        tags = reader.box_ids(connection, "tag")
        scenes = reader.box_ids(connection, "scene")
    finally:
        connection.close()

    assert people == [reader.BoxId(1, BOX, "remote-person")]
    assert (tags, scenes) == ([], [reader.BoxId(1, BOX, "remote-scene")])


def test_a_picture_in_a_zip_is_at_the_zips_path_with_its_own_name_under_it(
    tmp_path: Path,
) -> None:
    connection = _open(tmp_path)
    try:
        zipped = next(one for one in reader.images(connection) if one.stash_id == 2)
    finally:
        connection.close()

    assert zipped.files[0] == reader.StashFile(f"{TOP}/{ZIP}/inside.jpg", in_zip=True)


def test_a_saved_filter_is_read_with_its_words_and_its_criteria(tmp_path: Path) -> None:
    connection = _open(tmp_path)
    try:
        first = next(reader.saved_filters(connection))
    finally:
        connection.close()

    assert (first.mode, first.name, first.find["sort"]) == ("SCENES", "Beach days", "date")
    assert set(first.criteria) == {"tags", "rating100"}


def test_a_file_is_read_with_its_size_and_its_length(tmp_path: Path) -> None:
    copy = tmp_path / "copy.sqlite"
    reader.copy_in(make_stash(tmp_path / "stash.sqlite", waiting=True), copy)
    connection = reader.open_copy(copy)
    try:
        third = next(one for one in reader.scenes(connection) if one.stash_id == 3)
    finally:
        connection.close()

    assert third.files[0] == reader.StashFile(
        f"{TOP}/third.mp4",
        oshash=WAITING_OSHASH,
        phash=WAITING_PHASH_HEX,
        size_bytes=3000,
        duration_ms=61_500,
    )


def test_a_gallery_is_read_with_its_pictures_and_a_zip_gallery_is_named_by_its_zip(
    tmp_path: Path,
) -> None:
    copy = tmp_path / "copy.sqlite"
    reader.copy_in(make_stash(tmp_path / "stash.sqlite", gallery=2), copy)
    connection = reader.open_copy(copy)
    try:
        found = reader.galleries(connection)
    finally:
        connection.close()

    assert found == [reader.Gallery(1, "set", (2,)), reader.Gallery(2, GALLERY, (101, 102))]


def _connection(tmp_path: Path, **made: Any) -> sqlite3.Connection:
    copy = tmp_path / "copy-extras.sqlite"
    reader.copy_in(make_stash(tmp_path / "stash-extras.sqlite", **made), copy)
    return reader.open_copy(copy)


def test_what_sift_has_no_field_for_is_said_on_one_from_stash_line(tmp_path: Path) -> None:
    """A director, organized, captions and a custom field on a scene; a weight and a custom field
    on a person, after her own details: each said in plain words on one line, never dropped."""
    connection = _connection(tmp_path, extras=True)
    try:
        scene = next(one for one in reader.scenes(connection) if one.stash_id == 1)
        person = next(one for one in reader.performers(connection) if one.name == "Jane Doe")
    finally:
        connection.close()

    assert scene.fields["details"] == (
        "From Stash: director Ada Byron; organized; captions in en; mood: calm"
    )
    assert person.fields["details"] == "Swims.\n\nFrom Stash: weight 55 kg; shoe: 38"
    assert person.fields["tags"] == [WORN_TAG]
    assert person.picture == IN_DATABASE
    # Where it was stopped and how long it was watched in all, in milliseconds; never a date.
    assert (scene.resume_ms, scene.played_ms) == (42_500, 300_000)


def test_a_row_with_nothing_to_say_gets_no_line(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        scene = next(one for one in reader.scenes(connection) if one.stash_id == 1)
    finally:
        connection.close()
    assert "details" not in scene.fields
    assert (scene.resume_ms, scene.played_ms) == (None, 0)


def test_a_group_is_read_with_its_scenes_in_its_own_order(tmp_path: Path) -> None:
    connection = _connection(tmp_path, extras=True)
    try:
        found = reader.groups(connection)
    finally:
        connection.close()
    assert found == [reader.Group(1, GROUP, (2, 1))]


def test_a_picture_is_read_from_the_database_or_from_the_blobs_folder(tmp_path: Path) -> None:
    """Stash keeps a picture in its database or, set another way, as a file two folders deep by
    its checksum; a checksum that is not plain hexadecimal names no path at all."""
    folder = blobs_folder(tmp_path / "blobs")
    connection = _connection(tmp_path, extras=True)
    try:
        assert reader.picture_of(connection, IN_DATABASE, None) == a_png()
        assert reader.picture_of(connection, IN_FOLDER, None) is None
        assert reader.picture_of(connection, IN_FOLDER, folder) == a_png()
        (tmp_path / "secret").write_bytes(b"not a picture")
        assert reader.picture_of(connection, "../secret", folder) is None
        assert reader.picture_of(connection, "..\\..\\secret", folder) is None
        summary = reader.summarize(connection)
    finally:
        connection.close()
    assert (
        summary.people_with_a_picture,
        summary.sites_with_a_picture,
        summary.pictures_in_a_folder,
        summary.groups,
        summary.scenes_with_a_resume_point,
    ) == (1, 1, 1, 1, 1)


def _with(tmp_path: Path, *statements: str) -> sqlite3.Connection:
    """The fixture with a few more rows written into Stash's own file before it is copied."""
    stash = make_stash(tmp_path / "stash-more.sqlite")
    written = sqlite3.connect(stash)
    try:
        for statement in statements:
            written.execute(statement)
        written.commit()
    finally:
        written.close()
    copy = tmp_path / "copy-more.sqlite"
    reader.copy_in(stash, copy)
    return reader.open_copy(copy)


def test_a_file_that_cannot_be_opened_or_is_no_database_is_refused_with_why(
    tmp_path: Path,
) -> None:
    """The copy is the first thing a read does, so a missing file or one that is not a database
    at all is refused there with a sentence a person can act on, never a raw database error."""
    with pytest.raises(reader.NotAStashDatabase, match="couldn't open"):
        reader.copy_in(tmp_path / "nowhere" / "stash.sqlite", tmp_path / "copy.sqlite")
    text = tmp_path / "notes.sqlite"
    text.write_bytes(b"These are notes, not a database. " * 200)
    with pytest.raises(reader.NotAStashDatabase, match="isn't a database"):
        reader.copy_in(text, tmp_path / "copy.sqlite")


def test_a_database_that_is_not_stashs_or_names_no_version_is_refused(tmp_path: Path) -> None:
    """Stash's own database alone says its version in `schema_migrations`: one without the table
    is another program's, and one with the table empty cannot be held to a version."""
    other = tmp_path / "other.sqlite"
    connection = sqlite3.connect(other)
    connection.execute("CREATE TABLE notes (text TEXT)")
    connection.commit()
    connection.close()
    with pytest.raises(reader.NotAStashDatabase, match="isn't a Stash database"):
        reader.open_copy(other)
    blank = tmp_path / "blank.sqlite"
    connection = sqlite3.connect(blank)
    connection.execute("CREATE TABLE schema_migrations (version INTEGER, dirty INTEGER)")
    connection.commit()
    connection.close()
    with pytest.raises(reader.NotAStashDatabase, match="doesn't say which version"):
        reader.open_copy(blank)


def test_an_empty_alias_or_custom_field_is_not_read_and_a_quoted_value_is_unwrapped(
    tmp_path: Path,
) -> None:
    """Stash can keep an empty alias, a custom field with no name or no value, and a value kept
    as a JSON string. None of the empty ones is said, and a JSON string is said as its words."""
    connection = _with(
        tmp_path,
        "INSERT INTO performer_aliases VALUES (2, '')",
        "INSERT INTO performer_custom_fields VALUES (2, 'shoe', '\"38\"')",
        "INSERT INTO performer_custom_fields VALUES (2, 'mood', '\"calm')",
        "INSERT INTO performer_custom_fields VALUES (2, 'hat', '\"bad\\q\"')",
        "INSERT INTO performer_custom_fields VALUES (2, '', 'nameless')",
        "INSERT INTO performer_custom_fields VALUES (2, 'empty', '')",
    )
    try:
        person = next(one for one in reader.performers(connection) if one.stash_id == 2)
    finally:
        connection.close()
    assert "aliases" not in person.fields or person.fields["aliases"] == []
    assert person.fields["details"] == 'From Stash: hat: "bad\\q"; mood: "calm; shoe: 38'


def test_a_time_or_a_fingerprint_stash_kept_as_words_is_read_as_none() -> None:
    """A damaged copy can hold text where Stash keeps a number: a time reads as no time and a
    fingerprint as no fingerprint, rather than stopping the read."""
    assert reader._ms("soon") == 0
    assert reader.phash_hex("not a number") is None


def test_a_picture_in_a_flattened_blobs_folder_is_found_and_one_in_neither_is_not(
    tmp_path: Path,
) -> None:
    """Somebody may move every blob to the top of the folder; the top is tried after Stash's own
    two folders deep. A checksum in neither place is no picture."""
    folder = tmp_path / "flat"
    folder.mkdir()
    (folder / IN_FOLDER).write_bytes(a_png())
    connection = _connection(tmp_path, extras=True)
    try:
        assert reader.picture_of(connection, IN_FOLDER, folder) == a_png()
        assert reader.picture_of(connection, IN_FOLDER, tmp_path / "empty") is None
    finally:
        connection.close()


def test_a_stash_without_one_kind_of_stash_box_id_reads_none_of_that_kind(
    tmp_path: Path,
) -> None:
    """An older Stash keeps stash-box ids for fewer kinds: a missing table is none, not a
    failed read."""
    connection = _with(tmp_path, "DROP TABLE tag_stash_ids")
    try:
        assert reader.box_ids(connection, "tag") == []
        assert reader.box_ids(connection, "performer") != []
    finally:
        connection.close()


def test_a_saved_filter_whose_criteria_are_not_json_reads_as_asking_nothing(
    tmp_path: Path,
) -> None:
    connection = _with(
        tmp_path,
        "INSERT INTO saved_filters (id, name, mode, find_filter, object_filter)"
        " VALUES (9, 'Broken', 'SCENES', '{not json', '[1, 2]')",
    )
    try:
        broken = [one for one in reader.saved_filters(connection) if one.name == "Broken"]
    finally:
        connection.close()
    assert broken == [reader.SavedFilter("SCENES", "Broken", {}, {})]


def test_names_are_read_only_from_the_three_tables_that_hold_them(tmp_path: Path) -> None:
    """The table is spelt into the statement, so anything but the three is refused outright."""
    connection = _connection(tmp_path)
    try:
        with pytest.raises(ValueError, match="scenes"):
            reader.names_of(connection, "scenes")
    finally:
        connection.close()
