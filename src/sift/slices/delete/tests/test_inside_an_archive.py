# SPDX-License-Identifier: AGPL-3.0-or-later
"""A picture that lives inside an archive: a delete never says it did what it did not.

Sift does not rewrite a person's archive, so Delete from disk is refused for one, in words that
say what works instead, and nothing is touched. Remove from Sift works, and is written where the
scan reads it, or the next scan would bring the picture back under a new id. The scan's half is
asserted in `library_roots/tests/test_archive_member_removed.py`, against a real scan.
"""

from __future__ import annotations

import json
import zipfile
from typing import Any

import pytest

import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.access import Viewer
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore, Ingested
from sift.kernel.db import Database
from sift.kernel.ingress import Origin, verify_ingress
from sift.slices.delete.service import (
    INSIDE_AN_ARCHIVE,
    INSIDE_AN_ARCHIVE_MANY,
    Deleter,
    InsideAnArchive,
)
from sift.slices.library_roots.service import REMOVED_FROM_SIFT, LibraryService

from .conftest import CORPUS, Library


async def add_member(
    library: Library,
    content_store: ContentStore,
    settings: Settings,
    *,
    archive: str = "shoot.zip",
    member: str = "01.png",
    source: str = "accepted.png",
    folder_id: str | None = None,
) -> Ingested:
    """A real picture written into a real ZIP, indexed as a scan indexes a member of one."""
    target = library.path / archive
    target.parent.mkdir(parents=True, exist_ok=True)
    data = (CORPUS / source).read_bytes()
    with zipfile.ZipFile(target, "a") as writing:
        writing.writestr(member, data)
    scratch = library.path.parent / f"scratch-{member}"
    scratch.write_bytes(data)
    checked = verify_ingress(scratch, origin=Origin.SCAN, settings=settings)
    try:
        return await content_store.ingest(
            checked,
            root_id=library.root.id,
            rel_path=f"{archive}/{member}",
            folder_id=folder_id,
            archive_rel_path=archive,
            member_path=member,
        )
    finally:
        scratch.unlink()


def memory(database: Database) -> Any:
    return database.fetch_all(
        "SELECT rel_path, size_bytes, reason FROM scan_rejections ORDER BY rel_path", ()
    )


async def test_a_disk_delete_of_a_member_is_refused_and_nothing_changes(
    deleter: Deleter,
    content_store: ContentStore,
    temp_db: Database,
    settings: Settings,
    managed: Library,
    admin: Viewer,
) -> None:
    """The copy pulled out into the cache must not go while the press says it worked.

    The picture is read out into the cache first, so there IS a writable copy for a delete to be
    fooled by; it stays, the archive is byte for byte what it was, the file is still in Sift, and
    no History line claims a delete."""
    added = await add_member(managed, content_store, settings)
    location = (await content_store.locations(added.asset.id))[0]
    cached = await content_store.path_of(location)
    archive = (managed.path / "shoot.zip").read_bytes()

    with pytest.raises(InsideAnArchive, match="can't be deleted from disk") as refused:
        await deleter.remove(added.asset.id, mode="disk", actor=admin)

    assert str(refused.value) == INSIDE_AN_ARCHIVE
    assert cached.is_file()
    assert (managed.path / "shoot.zip").read_bytes() == archive
    assert await content_store.get(added.asset.id) is not None
    assert await content_store.locations(added.asset.id) == [location]
    assert await memory(temp_db) == []
    assert await temp_db.fetch_all("SELECT id FROM workbench_decisions", ()) == []


async def test_removing_a_member_from_sift_is_remembered_for_the_scan(
    deleter: Deleter,
    content_store: ContentStore,
    temp_db: Database,
    settings: Settings,
    managed: Library,
    admin: Viewer,
) -> None:
    """Gone from Sift, the archive untouched, the scan told, and History says which it was."""
    added = await add_member(managed, content_store, settings)
    archive = (managed.path / "shoot.zip").read_bytes()

    await deleter.remove(added.asset.id, mode="sift", actor=admin)

    assert await content_store.get(added.asset.id) is None
    assert (managed.path / "shoot.zip").read_bytes() == archive
    rows = await memory(temp_db)
    assert [tuple(row) for row in rows] == [
        ("shoot.zip/01.png", added.asset.size_bytes, REMOVED_FROM_SIFT)
    ]
    scanner = LibraryService(temp_db, deleter._library, deleter._access)
    assert await scanner.removed_inside(root_id=managed.root.id, archive_rel_path="shoot.zip") == {
        "shoot.zip/01.png": added.asset.size_bytes
    }
    said = json.loads(
        str((await temp_db.fetch_all("SELECT payload FROM workbench_decisions", ()))[0]["payload"])
    )
    assert (said["from"], said["archive"]) == ("sift", True)


async def test_an_ordinary_file_removed_from_sift_leaves_the_scan_nothing_to_remember(
    deleter: Deleter,
    temp_db: Database,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """An ordinary file comes back on the next scan, and that is its way back: unchanged."""
    added = await add_file(managed, "clip.mp4")

    await deleter.remove(added.asset.id, mode="sift", actor=admin)

    assert await memory(temp_db) == []
    said = json.loads(
        str((await temp_db.fetch_all("SELECT payload FROM workbench_decisions", ()))[0]["payload"])
    )
    assert said["from"] == "sift"
    assert "archive" not in said


async def test_a_selection_deletes_the_ordinary_file_and_refuses_the_member(
    deleter: Deleter,
    content_store: ContentStore,
    temp_db: Database,
    settings: Settings,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """One refusal does not stop the rest, and the counts say which went, in the archive's words."""
    member = await add_member(managed, content_store, settings)
    loose = await add_file(managed, "clip.mp4")
    archive = (managed.path / "shoot.zip").read_bytes()

    done = await deleter.remove_many([member.asset.id, loose.asset.id], mode="disk", actor=admin)

    assert (done.removed, done.skipped) == (1, 1)
    assert (done.reason, done.reason_many) == (INSIDE_AN_ARCHIVE, INSIDE_AN_ARCHIVE_MANY)
    assert not (managed.path / "clip.mp4").exists()
    assert (managed.path / "shoot.zip").read_bytes() == archive
    assert await content_store.get(member.asset.id) is not None
    assert await memory(temp_db) == []


async def test_a_selection_removed_from_sift_remembers_its_members(
    deleter: Deleter,
    content_store: ContentStore,
    temp_db: Database,
    settings: Settings,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    member = await add_member(managed, content_store, settings)
    loose = await add_file(managed, "clip.mp4")

    done = await deleter.remove_many([member.asset.id, loose.asset.id], mode="sift", actor=admin)

    assert (done.removed, done.skipped) == (2, 0)
    assert [str(row["rel_path"]) for row in await memory(temp_db)] == ["shoot.zip/01.png"]
    rows = await temp_db.fetch_all("SELECT payload FROM workbench_decisions", ())
    marked = sorted(json.loads(str(row["payload"])).get("archive", False) for row in rows)
    assert marked == [False, True]


async def test_the_sheet_is_told_how_many_are_inside_an_archive(
    deleter: Deleter,
    content_store: ContentStore,
    settings: Settings,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    member = await add_member(managed, content_store, settings)
    loose = await add_file(managed, "clip.mp4")

    assert await deleter.inside_archives([member.asset.id, loose.asset.id], actor=admin) == 1
    assert await deleter.inside_archives([loose.asset.id], actor=admin) == 0


async def test_a_folder_delete_leaves_the_pictures_of_an_archive_with_it(
    deleter: Deleter,
    content_store: ContentStore,
    settings: Settings,
    managed: Library,
    add_file: Any,
    admin: Viewer,
) -> None:
    """The loose file goes; the archive is not Sift's to change, so its pictures stay in Sift and
    the folder stays standing, which the answer says."""
    folder = await deleter._library.upsert_folder(managed.root.id, "sets")
    member = await add_member(
        managed, content_store, settings, archive="sets/shoot.zip", folder_id=folder.id
    )
    loose = await add_file(managed, "sets/clip.mp4", folder_id=folder.id)
    archive = (managed.path / "sets" / "shoot.zip").read_bytes()

    cleared = await deleter.remove_folder(folder.id, actor=admin)

    assert (cleared.files, cleared.left_behind) == (1, True)
    assert not (managed.path / "sets" / "clip.mp4").exists()
    assert await content_store.get(loose.asset.id) is None
    assert (managed.path / "sets" / "shoot.zip").read_bytes() == archive
    assert await content_store.get(member.asset.id) is not None
