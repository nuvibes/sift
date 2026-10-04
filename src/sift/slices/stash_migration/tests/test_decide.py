# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which People, Sites and Tags come across with a run, and which wait with their files."""

from __future__ import annotations

from pathlib import Path

import pytest

from sift.slices.stash_migration import reader
from sift.slices.stash_migration.service import Decided, Stashed, _read_stash, decide
from sift.slices.stash_migration.tests.stash_fixture import (
    ALONE_PERSON,
    ALONE_SITE,
    ALONE_TAG,
    WAITING_PERSON,
    WAITING_SITE,
    WAITING_TAG,
    WORN_TAG,
    make_stash,
)

pytestmark = pytest.mark.unit


def _stashed(tmp_path: Path, *, extras: bool = False) -> Stashed:
    copy = tmp_path / f"copy-{extras}.sqlite"
    made = make_stash(tmp_path / f"stash-{extras}.sqlite", waiting=True, extras=extras)
    reader.copy_in(made, copy)
    connection = reader.open_copy(copy)
    try:
        return _read_stash(connection)
    finally:
        connection.close()


def test_only_what_a_landed_file_carries_comes_across_with_the_records_of_their_own(
    tmp_path: Path,
) -> None:
    decided = decide(_stashed(tmp_path), {("scene", 1)})

    # The first scene's People, Site (and its network, which is part of its record) and Tags,
    # with the tags of its markers and their parent; nothing only the missing scene or the missing
    # picture carries.
    assert decided.come == {
        ("person", "Jane Doe"),
        ("person", "Jane Roe"),
        ("site", "Another Studio"),
        ("site", "Jane Doe Videos"),
        ("tag", "Beach"),
        ("tag", "Evening"),
        ("tag", "Outdoors"),
        ("tag", "Time of day"),
    }
    # A network and a category are attached to what is filed under them, so neither is a record
    # attached to nothing.
    assert decided.without_files == set()
    for waits in (("person", WAITING_PERSON), ("site", WAITING_SITE), ("tag", WAITING_TAG)):
        assert waits not in decided.come


def test_when_nothing_lands_nothing_comes_but_what_stash_attached_to_nothing(
    tmp_path: Path,
) -> None:
    """A parent waits with what is filed under it; only a row nothing reaches comes on its own."""
    assert decide(_stashed(tmp_path), set()) == Decided(come=set(), without_files=set())

    decided = decide(_stashed(tmp_path, extras=True), set())
    alone = {("person", ALONE_PERSON), ("site", ALONE_SITE), ("tag", ALONE_TAG)}
    assert decided.come == decided.without_files == alone


def test_a_tag_a_person_wears_comes_with_the_person_and_is_not_alone(tmp_path: Path) -> None:
    """Stash files a tag on a performer; it is part of her record, so it comes when she does and
    is never listed as attached to nothing."""
    decided = decide(_stashed(tmp_path, extras=True), {("scene", 1)})
    assert ("tag", WORN_TAG) in decided.come
    assert ("tag", WORN_TAG) not in decided.without_files
