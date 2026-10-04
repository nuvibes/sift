# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ways organizing can fail that are not somebody's mistake: a folder that stopped being
writable, a library removed mid-screen, a file the index names and the disk lacks. Each state is
set up directly, and each answer is a refusal somebody can act on rather than an OS error.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from sift.kernel.access import Viewer
from sift.kernel.content import LibraryStore
from sift.kernel.db import Database
from sift.kernel.library_write import LibraryWriteRefused
from sift.slices.organize import schema
from sift.slices.organize import service as organize_service
from sift.slices.organize.service import (
    NotFound,
    Organizer,
    OrganizeRefused,
    VaultLocked,
    claim_and_move,
)
from sift.testing.fixtures import hide

from .conftest import Library

pytestmark = pytest.mark.anyio


# --- a folder that will not take the write -----------------------------------------------------


@pytest.mark.skipif(
    # `os.geteuid` is absent on Windows, and this runs at import time.
    sys.platform == "win32" or os.geteuid() == 0,
    reason="root ignores the permission bits this relies on, and the permission bits this relies on are accepted and then ignored by Windows, so the folder stays readable and the refusal being tested never happens on Windows",
)
async def test_a_folder_sift_cannot_write_to_is_refused_in_a_sentence(
    organizer: Organizer, managed: Library, add_file: Any, admin: Viewer
) -> None:
    """A folder Sift cannot write to is refused in a sentence about permissions."""
    added = await add_file(managed, "clip.mp4")
    mode = managed.path.stat().st_mode
    os.chmod(managed.path, 0o500)
    try:
        with pytest.raises(OrganizeRefused, match="not allowed to write"):
            await organizer.rename(added.asset.id, new_name="holiday.mp4", actor=admin)
        assert (managed.path / "clip.mp4").exists()
    finally:
        os.chmod(managed.path, mode)


@pytest.mark.skipif(
    # `os.geteuid` is absent on Windows, and this runs at import time.
    sys.platform == "win32" or os.geteuid() == 0,
    reason="root ignores the permission bits this relies on, and the permission bits this relies on are accepted and then ignored by Windows, so the folder stays readable and the refusal being tested never happens on Windows",
)
async def test_a_destination_folder_sift_cannot_write_to_is_refused_before_anything_moves(
    organizer: Organizer,
    library_store: LibraryStore,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """A move checks the destination folder itself, before anything moves."""
    added = await add_file(managed, "clip.mp4")
    shut = managed.path / "sorted"
    shut.mkdir()
    destination = await library_store.upsert_folder(managed.root.id, "sorted")
    mode = shut.stat().st_mode
    os.chmod(shut, 0o500)
    try:
        with pytest.raises(OrganizeRefused, match="not allowed to write"):
            await organizer.move(added.asset.id, folder_id=destination.id, actor=admin)
    finally:
        os.chmod(shut, mode)

    assert (managed.path / "clip.mp4").exists()


async def test_a_file_the_index_names_and_the_disk_does_not_is_refused(
    organizer: Organizer, managed: Library, add_file: Any, admin: Viewer
) -> None:
    """A file deleted from under Sift is refused by the move itself, with no check-then-act gap."""
    added = await add_file(managed, "clip.mp4")
    (managed.path / "clip.mp4").unlink()

    with pytest.raises(OrganizeRefused, match="couldn't move that file"):
        await organizer.rename(added.asset.id, new_name="holiday.mp4", actor=admin)

    assert not (managed.path / "holiday.mp4").exists()


async def _nothing() -> None:
    """An awaitable that answers None, for a lookup made to find nothing."""
    return None


async def test_a_file_whose_FOLDER_has_left_the_library_is_refused_as_a_sentence(
    organizer: Organizer,
    managed: Library,
    add_file: Any,
    admin: Viewer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file whose library root was removed is refused as an organize sentence, from the kernel's
    lookup, never an `AttributeError`."""
    added = await add_file(managed, "clip.mp4")
    # The root gone and the location row kept, which a delete cannot arrange: faked at the
    # lookup `require_root` reads.
    monkeypatch.setattr(organizer._library, "get_root", lambda _root_id: _nothing())

    with pytest.raises(OrganizeRefused, match="no longer part of your library"):
        await organizer.rename(added.asset.id, new_name="holiday.mp4", actor=admin)

    assert (managed.path / "clip.mp4").exists(), "the file was touched before the refusal"


async def test_a_rename_derived_from_a_bad_stored_path_is_refused_before_the_disk(
    organizer: Organizer,
    temp_db: Database,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """A rename of a stored path that walks out of its root is refused before the disk."""
    added = await add_file(managed, "clip.mp4")
    await temp_db.execute(
        "UPDATE asset_locations SET rel_path = ? WHERE id = ?",
        ("../escape.mp4", added.location.id),
    )

    with pytest.raises(OrganizeRefused, match="a name Sift can store"):
        await organizer.rename(added.asset.id, new_name="holiday.mp4", actor=admin)

    assert not (managed.path.parent / "holiday.mp4").exists()


async def test_a_move_of_a_file_whose_stored_path_walks_out_of_its_root_is_refused(
    organizer: Organizer,
    library_store: LibraryStore,
    temp_db: Database,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """A move of such a row is refused when the current path is validated on the way out."""
    added = await add_file(managed, "clip.mp4")
    (managed.path / "sorted").mkdir()
    destination = await library_store.upsert_folder(managed.root.id, "sorted")
    await temp_db.execute(
        "UPDATE asset_locations SET rel_path = ? WHERE id = ?",
        ("../escape.mp4", added.location.id),
    )

    with pytest.raises(OrganizeRefused, match="can't find that file"):
        await organizer.move(added.asset.id, folder_id=destination.id, actor=admin)

    assert not (managed.path / "sorted" / "clip.mp4").exists()


# --- moving a folder ---------------------------------------------------------------------------


# --- the two file operations, called directly ---------------------------------------------------
#
# The last steps before the disk, with failure states a request cannot produce, built by hand.


def test_claiming_a_name_that_cannot_be_created_is_refused(tmp_path: Path) -> None:
    """The destination's parent is not a directory, so the name cannot be taken at all."""
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"bytes")
    (tmp_path / "notadir").write_bytes(b"a file, not a folder")

    with pytest.raises(OrganizeRefused, match="couldn't put the file there"):
        claim_and_move(source, tmp_path / "notadir" / "clip.mp4")

    assert source.exists()


def test_a_move_that_fails_after_the_name_is_claimed_clears_the_claim(tmp_path: Path) -> None:
    """A move that fails after claiming the name frees the name again. The source is a vanished
    file, as in production; a directory source would behave differently on Windows."""
    source = tmp_path / "gone.mp4"
    destination = tmp_path / "clip.mp4"

    with pytest.raises(OrganizeRefused, match="couldn't move that file"):
        claim_and_move(source, destination)

    assert not destination.exists()
    assert not source.exists()


# --- the table ----------------------------------------------------------------------------------


async def test_the_table_is_not_rebuilt_when_it_is_already_there(temp_db: Database) -> None:
    """The initializer does not rebuild a table already at this version: a row put in survives."""
    await temp_db.initialize_schema()
    await temp_db.execute(
        "INSERT INTO file_moves (id, kind, root_id, from_rel_path, to_rel_path, moved_at) "
        "VALUES (?, 'rename', ?, ?, ?, 0)",
        ("01HX0000000000000000000301", "01HX0000000000000000000302", "before.mp4", "after.mp4"),
    )

    async with temp_db.write() as connection:
        await schema.initialize(connection, schema.VERSION)

    survivor = await temp_db.fetch_one(
        "SELECT from_rel_path FROM file_moves WHERE id = ?", ("01HX0000000000000000000301",)
    )
    assert survivor is not None
    assert survivor["from_rel_path"] == "before.mp4"


async def test_the_initializer_makes_the_table_on_a_fresh_database(temp_db: Database) -> None:
    await temp_db.initialize_schema()

    row = await temp_db.fetch_one(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'file_moves'"
    )

    assert row is not None


async def test_a_folder_the_write_door_refuses_is_a_sentence_rather_than_a_failed_rename(
    organizer: Organizer, managed: Library, add_file: Any, admin: Viewer, monkeypatch: Any
) -> None:
    """The write door's refusal reaches the person as a sentence, faked since Windows ignores
    `chmod`."""
    added = await add_file(managed, "clip.mp4")

    async def refuse(_directory: Any) -> None:
        raise LibraryWriteRefused("Sift is not allowed to write to that folder.")

    monkeypatch.setattr(organize_service, "check_folder_may_change", refuse)

    with pytest.raises(OrganizeRefused, match="not allowed to write"):
        await organizer.rename(added.asset.id, new_name="holiday.mp4", actor=admin)

    assert (managed.path / "clip.mp4").exists(), "the file was renamed anyway"


def test_a_file_concealed_by_the_vault_answers_locked_rather_than_gone() -> None:
    """A file concealed by the vault answers 423, not 404; every other refusal keeps its status."""
    from fastapi import status

    from sift.kernel.reach import ConcealedByVault
    from sift.slices.organize.router import _refusal
    from sift.slices.organize.service import NotAllowed, NotFound

    # `ConcealedByVault` is the kernel's, so this mapper tells it from `OrganizeRefused`.
    assert _refusal(cast(Any, ConcealedByVault("shut"))).status_code == status.HTTP_423_LOCKED
    assert _refusal(NotFound("no such file")).status_code == status.HTTP_404_NOT_FOUND
    assert _refusal(NotAllowed("not yours")).status_code == status.HTTP_403_FORBIDDEN


async def test_the_asker_own_vault_is_told_it_is_locked_rather_than_that_it_is_gone(
    organizer: Organizer,
    managed: Library,
    add_file: Callable[..., Any],
    admin: Viewer,
    temp_db: Database,
) -> None:
    """The asker's own concealed file is told locked. Only a file that appears when the vault opens
    was concealed, so a stranger's id gets no 423."""
    ingested = await add_file(managed, "clip.mp4")
    await hide(temp_db, "asset", ingested.asset.id, admin.id)

    refused = await organizer._unreachable(admin, ingested.asset.id)

    assert isinstance(refused, VaultLocked)


async def test_a_file_that_is_simply_not_there_stays_an_undifferentiated_absence(
    organizer: Organizer, admin: Viewer
) -> None:
    """A file simply not there stays an undifferentiated absence."""
    refused = await organizer._unreachable(admin, "01M0NOSUCHASSETIDATALL0000")

    assert isinstance(refused, NotFound)
