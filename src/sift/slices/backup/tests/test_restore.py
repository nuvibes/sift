# SPDX-License-Identifier: AGPL-3.0-or-later
"""Putting a backup back, and every way that is refused.

The first test is the promise the whole feature exists to keep: everything somebody spent their
time on comes back. The rest are the refusals, and they matter for the opposite reason: a
restore that half-worked is worse than one that did not run, because the copy it was restoring
from is often the last one.
"""

from __future__ import annotations

import asyncio
import sqlite3
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from sift.kernel.access import Repository
from sift.kernel.config import Settings
from sift.kernel.db import Database, registered_components
from sift.slices.backup import service as backup_service
from sift.slices.backup.service import (
    _ZIP_MAGIC,
    DATABASE_MEMBER,
    INCOMING_SUFFIX,
    MANIFEST_MEMBER,
    SUPERSEDED_SUFFIX,
    BackupService,
    BackupTooNew,
    NoRoomToUnpack,
    NotABackup,
    _member_target,
)
from sift.slices.settings_hub import SettingsService
from sift.testing.fixtures import Actors, World
from sift.testing.tools import ON_WINDOWS, WINDOWS_ONLY, junction

from .conftest import RecordedWorkers, database_in

pytestmark = pytest.mark.anyio


# Every table a person's own work lives in, in the order that respects the references between
# them. Written out as statements rather than looped over a list of names: query text assembled
# from a variable is exactly what the build refuses, and a fixed list is not an exception to that
# so much as a reason not to need one.
_WIPE = (
    "DELETE FROM asset_usernames",
    "DELETE FROM asset_people",
    "DELETE FROM asset_tags",
    "DELETE FROM collection_items",
    "DELETE FROM collections",
    "DELETE FROM usernames",
    "DELETE FROM sites",
    "DELETE FROM people",
    "DELETE FROM tags",
    "DELETE FROM asset_user_state",
    "DELETE FROM asset_locations",
    "DELETE FROM assets",
    "DELETE FROM folders",
    "DELETE FROM library_roots",
    "DELETE FROM app_settings",
)


async def wipe(database: Database) -> None:
    """Lose the library the way a failed disk does: every record gone, nothing to fall back on.

    Rows rather than the file, because the file is the thing the restore replaces and a test that
    deleted it would not be able to tell a restore from a database that had never existed.
    """
    for statement in _WIPE:
        await database.execute(statement)


async def test_a_restored_library_has_every_record_back(
    backup: BackupService,
    prepared_db: Database,
    world: World,
    access: Repository,
    actors: Actors,
    tmp_path: Path,
) -> None:
    """The headline. Export, lose everything, restore, and check each kind of record by name.

    Checked one kind at a time rather than by a row count, because a count is satisfied by any
    rows at all. What a person would miss is a particular tag on a particular file, the person
    they attributed it to, the collection they put it in, the site it came from, the rating they
    gave it, and the fingerprint that lets a re-scan find the file again, so each of those is
    named here.
    """
    await prepared_db.execute(
        "INSERT INTO asset_user_state (user_id, asset_id, rating, favorite, updated_at) "
        "VALUES (?, ?, 5, 1, 1)",
        (actors.admin.id, world.solo),
    )
    await prepared_db.execute("INSERT INTO app_settings (key, value) VALUES ('backup.keep', '4')")

    exported = tmp_path / "before-the-disaster.sqlite3"
    await backup.export_to(exported)

    await wipe(prepared_db)
    assert await prepared_db.fetch_one("SELECT id FROM assets") is None

    await backup.restore(exported)

    async def one(sql: str, params: tuple[object, ...] = ()) -> object:
        row = await prepared_db.fetch_one(sql, params)
        assert row is not None, f"nothing came back for: {sql}"
        return row[0]

    # The asset, and the fingerprint that reconnects it to a file on disk after a re-scan.
    assert await one("SELECT identity FROM assets WHERE id = ?", (world.solo,))
    # The tag, and the fact that it was on this file rather than merely existing.
    assert await one("SELECT name FROM tags WHERE id = ?", (world.tag,)) == "tag"
    assert await one("SELECT tag_id FROM asset_tags WHERE asset_id = ?", (world.solo,)) == world.tag
    # The person, and the attribution.
    assert await one("SELECT name FROM people WHERE id = ?", (world.person,)) == "person"
    assert (
        await one("SELECT person_id FROM asset_people WHERE asset_id = ?", (world.solo,))
        == world.person
    )
    # The Site and the username on it, and the link from the file to that username.
    assert await one("SELECT name FROM sites WHERE id = ?", (world.site,)) == "site"
    assert await one("SELECT name FROM usernames WHERE id = ?", (world.username,)) == "handle"
    assert (
        await one("SELECT username_id FROM asset_usernames WHERE asset_id = ?", (world.solo,))
        == world.username
    )
    # The collection, its cover, and what was in it.
    assert (
        await one("SELECT cover_asset_id FROM collections WHERE id = ?", (world.collection,))
        == world.solo
    )
    assert (
        await one(
            "SELECT asset_id FROM collection_items WHERE collection_id = ?", (world.collection,)
        )
        == world.solo
    )
    # The rating and the heart, which belong to one user and not to the library.
    assert (
        await one(
            "SELECT rating FROM asset_user_state WHERE user_id = ? AND asset_id = ?",
            (actors.admin.id, world.solo),
        )
        == 5
    )
    # The library's own shape, and the settings.
    assert await one("SELECT id FROM library_roots WHERE id = ?", (world.root,)) == world.root
    assert await one("SELECT value FROM app_settings WHERE key = 'backup.keep'") == "4"


async def test_an_export_taken_while_jobs_are_writing_restores_completely(
    backup: BackupService, prepared_db: Database, tmp_path: Path
) -> None:
    """The test `VACUUM INTO` exists for.

    A backup is meant to be taken daily on a machine that is doing something else, so the export
    runs while writes are landing. What must never happen is a file that opens cleanly and is
    internally inconsistent: a tag row whose asset is not there, a half-written pair.

    So the writers below write in pairs, an asset and its location together in one transaction, and
    the assertion is that the restored database contains no asset without a location and no
    location without an asset. How many pairs got in is not the point and is not asserted; whether
    a pair is ever split is.
    """

    async def writer(start: int, count: int) -> None:
        for n in range(start, start + count):
            asset_id = f"asset-{n:04d}"
            async with prepared_db.write() as connection:
                await connection.execute(
                    "INSERT INTO assets (id, identity, media_type, added_at) "
                    "VALUES (?, ?, 'video', 1)",
                    (asset_id, f"digest-{n}"),
                )
                await connection.execute(
                    "INSERT INTO asset_locations (id, asset_id, root_id, rel_path, filename, "
                    "first_seen_at, last_seen_at) VALUES (?, ?, '01R', ?, ?, 1, 1)",
                    (f"loc-{n:04d}", asset_id, f"{n}.mp4", f"{n}.mp4"),
                )
            await asyncio.sleep(0)

    await prepared_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES ('01R', 'r', '/r', 1)"
    )

    exported = tmp_path / "taken-under-load.sqlite3"
    writers = [asyncio.create_task(writer(base, 60)) for base in (0, 1000, 2000)]
    await asyncio.sleep(0)
    await backup.export_to(exported)
    await asyncio.gather(*writers)

    await wipe(prepared_db)
    await backup.restore(exported)

    orphan_locations = await prepared_db.fetch_all(
        "SELECT l.id FROM asset_locations l "
        "LEFT JOIN assets a ON a.id = l.asset_id WHERE a.id IS NULL"
    )
    assert orphan_locations == []
    assets_without_a_location = await prepared_db.fetch_all(
        "SELECT a.id FROM assets a "
        "LEFT JOIN asset_locations l ON l.asset_id = a.id WHERE l.id IS NULL"
    )
    assert assets_without_a_location == []
    # And it is not vacuously consistent: the export really did catch writes in flight.
    caught = await prepared_db.fetch_one("SELECT COUNT(*) AS n FROM assets")
    assert caught is not None and caught["n"] > 0


# --- the refusals ---------------------------------------------------------------------------


async def test_a_backup_from_a_newer_sift_is_refused(
    backup: BackupService, tmp_path: Path, prepared_db: Database
) -> None:
    """Never a downgrade. The file being carried backwards is the good copy somebody had left."""
    await backup.export_to(tmp_path / "from-the-future.zip")
    exported = database_in(tmp_path / "from-the-future.zip")

    component = next(iter(sorted(registered_components())))
    ahead = sqlite3.connect(exported)
    ahead.execute(
        "UPDATE schema_version SET version = version + 1 WHERE component = ?", (component,)
    )
    ahead.commit()
    ahead.close()

    with pytest.raises(BackupTooNew, match="newer version of Sift"):
        await backup.restore(exported)


async def test_a_backup_naming_a_feature_this_build_has_never_heard_of_is_refused(
    backup: BackupService, tmp_path: Path
) -> None:
    """The other shape of "newer": a whole feature that did not exist when this build was made.

    Its tables would be carried along unread and its rows would mean nothing to anything here.
    Refusing is the same answer for the same reason.
    """
    await backup.export_to(tmp_path / "from-a-later-sift.zip")
    exported = database_in(tmp_path / "from-a-later-sift.zip")

    later = sqlite3.connect(exported)
    later.execute("INSERT INTO schema_version (component, version) VALUES ('time_machine', 1)")
    later.commit()
    later.close()

    with pytest.raises(BackupTooNew):
        await backup.restore(exported)


async def test_a_backup_refused_for_being_too_new_changes_nothing(
    backup: BackupService, prepared_db: Database, world: World, tmp_path: Path
) -> None:
    """A refusal has to leave the install exactly as it was, or it is not a refusal.

    This is the one that matters most about the ordering in `restore`: everything that can say no
    says no before a file moves.
    """
    await backup.export_to(tmp_path / "from-the-future.zip")
    exported = database_in(tmp_path / "from-the-future.zip")
    ahead = sqlite3.connect(exported)
    ahead.execute("INSERT INTO schema_version (component, version) VALUES ('time_machine', 1)")
    ahead.commit()
    ahead.close()

    with pytest.raises(BackupTooNew):
        await backup.restore(exported)

    still_there = await prepared_db.fetch_one("SELECT id FROM assets WHERE id = ?", (world.solo,))
    assert still_there is not None
    assert not prepared_db.path.with_name(prepared_db.path.name + SUPERSEDED_SUFFIX).exists()
    assert not prepared_db.path.with_name(prepared_db.path.name + INCOMING_SUFFIX).exists()


async def test_an_older_backup_restores_and_is_migrated_forward(
    backup: BackupService, prepared_db: Database, world: World, tmp_path: Path
) -> None:
    """A backup from an older Sift is the ordinary case, not an error.

    Its schema is brought up to date by the same code that migrates a database which was simply
    not opened for a while: there is no separate restore migration to drift from it. Proved by
    winding a component's recorded version back and watching the restore put it right.
    """
    await backup.export_to(tmp_path / "last-year.zip")
    exported = database_in(tmp_path / "last-year.zip")

    component = next(iter(sorted(registered_components())))
    current = registered_components()[component].version

    behind = sqlite3.connect(exported)
    behind.execute("UPDATE schema_version SET version = 0 WHERE component = ?", (component,))
    behind.commit()
    behind.close()

    await backup.restore(exported)

    assert await prepared_db.schema_version(component) == current
    assert await prepared_db.fetch_one("SELECT id FROM assets WHERE id = ?", (world.solo,))


async def test_a_file_that_is_not_a_database_is_refused(
    backup: BackupService, tmp_path: Path
) -> None:
    not_a_backup = tmp_path / "holiday.mp4"
    not_a_backup.write_bytes(b"\x00\x01\x02 this is a video")

    with pytest.raises(NotABackup):
        await backup.restore(not_a_backup)


async def test_a_database_that_is_not_a_sift_backup_is_refused(
    backup: BackupService, tmp_path: Path
) -> None:
    """A valid SQLite file with no manifest is somebody else's database, not a backup.

    Adopting it would replace a working library with a file that happens to be the right shape.
    """
    someone_elses = tmp_path / "notes.sqlite3"
    connection = sqlite3.connect(someone_elses)
    connection.execute("CREATE TABLE notes (body TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(NotABackup):
        await backup.restore(someone_elses)


def _archive_holding(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return path


async def test_an_archive_that_is_not_a_backup_is_refused_whatever_it_holds(
    backup: BackupService, workers: RecordedWorkers, tmp_path: Path
) -> None:
    """Every way an archive can fail to be a backup is refused before anything is moved: a member
    missing, a manifest that does not parse or is the wrong shape, and a zip cut short."""
    cut_short = tmp_path / "cut-short.zip"
    cut_short.write_bytes(_ZIP_MAGIC + b"\x00" * 40)
    candidates = [
        _archive_holding(tmp_path / "no-database.zip", {MANIFEST_MEMBER: b"{}"}),
        _archive_holding(tmp_path / "no-manifest.zip", {DATABASE_MEMBER: b"db"}),
        _archive_holding(
            tmp_path / "unparsed.zip", {MANIFEST_MEMBER: b"{not json", DATABASE_MEMBER: b"db"}
        ),
        _archive_holding(tmp_path / "a-list.zip", {MANIFEST_MEMBER: b"[]", DATABASE_MEMBER: b"db"}),
        _archive_holding(
            tmp_path / "no-carried.zip",
            {MANIFEST_MEMBER: b'{"carried": "faces"}', DATABASE_MEMBER: b"db"},
        ),
        cut_short,
    ]

    for candidate in candidates:
        with pytest.raises(NotABackup):
            await backup.restore(candidate)
    assert workers.calls == []


async def test_a_second_restore_replaces_the_undo_folder_the_first_left(
    backup: BackupService, settings: Settings, tmp_path: Path
) -> None:
    """The undo is the last restore's, not the first's: the folder kept aside by an earlier
    restore is replaced by what this one found, exactly as the database's undo is."""
    reference = settings.data_dir / "faces" / "references" / "01P" / "01R.jpg"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"REFERENCE-FACE")
    exported = tmp_path / "export.zip"
    await backup.export_to(exported)
    (reference.parent.parent / "first-stray.jpg").write_bytes(b"FIRST")
    await backup.restore(exported)
    (reference.parent.parent / "second-stray.jpg").write_bytes(b"SECOND")

    await backup.restore(exported)

    superseded = settings.data_dir / "faces" / ("references" + SUPERSEDED_SUFFIX)
    assert sorted(one.name for one in superseded.iterdir()) == ["01P", "second-stray.jpg"]
    assert reference.read_bytes() == b"REFERENCE-FACE"
    assert not (reference.parent.parent / "second-stray.jpg").exists()


async def test_a_folder_already_unpacked_is_discarded_when_a_later_one_is_refused(
    backup: BackupService, settings: Settings, tmp_path: Path
) -> None:
    """The folders are unpacked one after another beside their live counterparts. A refusal on
    the second leaves nothing of the first behind, and nothing live has been touched."""
    reference = settings.data_dir / "faces" / "references" / "01P" / "01R.jpg"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"REFERENCE-FACE")
    cover = settings.cache_dir / "covers" / "01C.jpg"
    cover.parent.mkdir(parents=True)
    cover.write_bytes(b"COVER")
    exported = tmp_path / "export.zip"
    await backup.export_to(exported)
    forged = tmp_path / "forged.zip"
    with zipfile.ZipFile(exported) as good, zipfile.ZipFile(forged, "w") as bad:
        for name in good.namelist():
            bad.writestr(name, good.read(name))
        bad.writestr("covers/../../escaped.jpg", b"OUT")

    with pytest.raises(NotABackup):
        await backup.restore(forged)

    references = settings.data_dir / "faces" / "references"
    assert not references.with_name(references.name + INCOMING_SUFFIX).exists()
    assert not cover.parent.with_name(cover.parent.name + INCOMING_SUFFIX).exists()
    assert reference.read_bytes() == b"REFERENCE-FACE"
    assert cover.read_bytes() == b"COVER"


# --- what a restore leaves behind -------------------------------------------------------------


async def test_the_workers_are_stopped_around_the_swap_and_started_again(
    backup: BackupService, workers: RecordedWorkers, tmp_path: Path
) -> None:
    """They hold jobs against the file being replaced, so they stop, and they come back."""
    exported = tmp_path / "export.sqlite3"
    await backup.export_to(exported)
    await backup.restore(exported)

    assert workers.calls == ["stop", "start"]


async def test_a_refused_restore_never_stops_the_workers(
    backup: BackupService, workers: RecordedWorkers, tmp_path: Path
) -> None:
    """Nothing was going to be replaced, so nothing should have been interrupted."""
    with pytest.raises(NotABackup):
        await backup.restore(tmp_path / "absent.sqlite3")

    assert workers.calls == []


async def test_the_database_that_was_replaced_is_kept(
    backup: BackupService, prepared_db: Database, world: World, tmp_path: Path
) -> None:
    """The undo for the last restore. Somebody who restores the wrong file has one way back."""
    exported = tmp_path / "export.sqlite3"
    await backup.export_to(exported)
    await wipe(prepared_db)
    await backup.restore(exported)

    superseded = prepared_db.path.with_name(prepared_db.path.name + SUPERSEDED_SUFFIX)
    assert superseded.is_file()
    kept = sqlite3.connect(f"file:{superseded}?mode=ro", uri=True)
    try:
        # The wiped database, which is what was live at the moment of the restore.
        assert kept.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
    finally:
        kept.close()


async def test_the_restored_database_does_not_keep_the_backup_manifest(
    backup: BackupService, prepared_db: Database, tmp_path: Path
) -> None:
    """The manifest belongs to the artifact, not to a running Sift.

    Left behind it would be a table no feature declared, which the schema registry knows nothing
    about and no migration would ever touch.
    """
    exported = tmp_path / "export.sqlite3"
    await backup.export_to(exported)
    await backup.restore(exported)

    present = await prepared_db.fetch_one(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'backup_manifest'"
    )
    assert present is None


async def test_writes_made_to_the_database_being_replaced_do_not_survive_the_restore(
    backup: BackupService, prepared_db: Database, tmp_path: Path
) -> None:
    """What was in the library at the moment of the restore is gone, because that is the point."""
    exported = tmp_path / "export.sqlite3"
    await backup.export_to(exported)
    await prepared_db.execute("INSERT INTO tags (id, name, created_at) VALUES ('01Z', 'z', 1)")

    await backup.restore(exported)

    assert await prepared_db.fetch_one("SELECT id FROM tags WHERE id = '01Z'") is None


@pytest.mark.skipif(
    ON_WINDOWS,
    reason=(
        "The situation cannot be SET UP here. It is forced by holding a second connection open "
        "across the restore, so that closing the database cannot take the side file with it, and "
        "Windows will not delete a file another handle has open, so the restore refuses at that "
        "step instead of reaching the swap. The refusal is safe (the live database has not been "
        "touched yet) and it is not what this test is about. The guarantee itself is not "
        "site-specific and is proved on POSIX"
    ),
)
async def test_a_write_ahead_log_left_by_the_replaced_database_is_not_replayed_into_the_new_one(
    backup: BackupService, prepared_db: Database, tmp_path: Path
) -> None:
    """The quiet way a restore turns into a corrupted library, reproduced and then refused.

    In WAL mode the newest writes live in a side file. Swap the main file and leave that side file
    behind, and SQLite replays it over the top: the restored database opens perfectly and reads
    back as the one that was replaced. Not a crash, not an error: the wrong library, silently.

    Closing the database normally removes the side file, which is why this has to force the case.
    A second connection is held open across the restore, so the close cannot take the file with it
    and the stale log is still sitting there when the swap happens: the same state a restore
    interrupted half way through would leave.
    """
    exported = tmp_path / "export.sqlite3"
    await backup.export_to(exported)

    # A write that exists only in the side file, and a connection holding that file open.
    holder = sqlite3.connect(prepared_db.path)
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("INSERT INTO tags (id, name, created_at) VALUES ('01Z', 'only-in-the-log', 1)")
    holder.commit()
    try:
        assert prepared_db.path.with_name(prepared_db.path.name + "-wal").exists()

        await backup.restore(exported)

        # The restored library is the backup's. If the stale log had been replayed, this row,
        # which was never in the backup, would be back.
        assert await prepared_db.fetch_one("SELECT id FROM tags WHERE id = '01Z'") is None
    finally:
        holder.close()


async def test_the_restore_reports_what_the_backup_said_about_itself(
    backup: BackupService, tmp_path: Path
) -> None:
    exported = tmp_path / "export.sqlite3"
    await backup.export_to(exported)

    manifest = await backup.restore(exported)

    assert manifest["app_version"]
    assert manifest["created_at"] == int(backup.now())


async def test_the_staged_copy_is_not_left_behind(
    backup: BackupService, prepared_db: Database, settings: Settings, tmp_path: Path
) -> None:
    """A restore is not an excuse to leave a second copy of the database in the data directory."""
    exported = tmp_path / "export.sqlite3"
    await backup.export_to(exported)
    await backup.restore(exported)

    incoming = prepared_db.path.with_name(prepared_db.path.name + INCOMING_SUFFIX)
    assert not incoming.exists()


async def test_a_database_with_an_empty_manifest_is_refused(
    backup: BackupService, tmp_path: Path
) -> None:
    """The table is there and says nothing, which is not a backup either.

    A manifest is what makes a file a backup, so an empty one is a file that has the shape and
    none of the content. It is refused for the same reason a file with no manifest at all is.
    """
    hollow = tmp_path / "hollow.sqlite3"
    connection = sqlite3.connect(hollow)
    connection.execute(
        "CREATE TABLE backup_manifest (format_version INTEGER, app_version TEXT, created_at INT)"
    )
    # And a schema table, so the refusal below is the empty manifest rather than the file simply
    # not looking like a database Sift wrote.
    connection.execute("CREATE TABLE schema_version (component TEXT PRIMARY KEY, version INT)")
    connection.commit()
    connection.close()

    with pytest.raises(NotABackup):
        await backup.restore(hollow)


async def test_a_restore_works_where_there_is_no_worker_pool_to_stop(
    prepared_db: Database,
    settings: Settings,
    preferences: SettingsService,
    world: World,
    tmp_path: Path,
) -> None:
    """Not every context that restores has workers running: a command-line recovery has none.

    The swap must not depend on there being a pool to pause, so a service built without one still
    restores rather than failing on the way to asking it to stop.
    """
    lone = BackupService(prepared_db, settings, preferences.get_app, preferences.apply)

    exported = tmp_path / "export.sqlite3"
    await lone.export_to(exported)
    await wipe(prepared_db)
    await lone.restore(exported)

    assert await prepared_db.fetch_one("SELECT id FROM assets WHERE id = ?", (world.solo,))


async def test_the_folders_a_backup_carries_come_back_with_it(
    backup: BackupService, prepared_db: Database, settings: Settings, tmp_path: Path
) -> None:
    """A reference face enrolled before the disaster is back after the restore, and what was in
    the folder meanwhile is kept aside as the undo, exactly as the database is."""
    reference = settings.data_dir / "faces" / "references" / "01P" / "01R.jpg"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"REFERENCE-FACE")
    exported = tmp_path / "export.zip"
    await backup.export_to(exported)

    reference.unlink()
    stray = settings.data_dir / "faces" / "references" / "stray.jpg"
    stray.write_bytes(b"AFTER-THE-BACKUP")

    manifest = await backup.restore(exported)

    assert manifest["carried"] == ["faces/references", "covers"]
    assert reference.read_bytes() == b"REFERENCE-FACE"
    assert not stray.exists()
    superseded = settings.data_dir / "faces" / ("references" + SUPERSEDED_SUFFIX)
    assert (superseded / "stray.jpg").read_bytes() == b"AFTER-THE-BACKUP"


async def test_a_backup_from_before_the_archive_still_restores(
    backup: BackupService, prepared_db: Database, tmp_path: Path
) -> None:
    """A bare database with the stamp inside it is what every export was until the archive."""
    await prepared_db.execute(
        "INSERT INTO tags (id, name, created_at) VALUES ('01T', 'from-before', 1)"
    )
    bare = tmp_path / "old-style.sqlite3"
    async with prepared_db.write() as connection:
        await connection.execute("VACUUM INTO ?", (str(bare),))
    await backup._stamp(bare)
    await wipe(prepared_db)

    manifest = await backup.restore(bare)

    assert manifest["carried"] == []
    row = await prepared_db.fetch_one("SELECT name FROM tags")
    assert row is not None and row["name"] == "from-before"


async def test_an_archive_member_that_climbs_out_of_its_folder_is_refused_whole(
    backup: BackupService, prepared_db: Database, settings: Settings, world: World, tmp_path: Path
) -> None:
    """A member named to land outside the folder it belongs to is not a backup Sift wrote, and
    nothing is changed by it: not the database, not the folder."""
    import json
    import zipfile

    exported = tmp_path / "export.zip"
    await backup.export_to(exported)
    forged = tmp_path / "forged.zip"
    with zipfile.ZipFile(exported) as good, zipfile.ZipFile(forged, "w") as bad:
        for name in good.namelist():
            bad.writestr(name, good.read(name))
        bad.writestr("faces/references/../../escaped.jpg", b"OUT")
    marker = settings.data_dir / "escaped.jpg"

    with pytest.raises(NotABackup):
        await backup.restore(forged)

    assert not marker.exists()
    assert json.loads(zipfile.ZipFile(forged).read("manifest.json"))["carried"]
    assert await prepared_db.fetch_one("SELECT id FROM assets WHERE id = ?", (world.solo,))


async def _forged_with(backup: BackupService, tmp_path: Path, member: str) -> Path:
    """A real export of this library with one more member written into it."""
    exported = tmp_path / "export.zip"
    await backup.export_to(exported)
    forged = tmp_path / "forged.zip"
    with zipfile.ZipFile(exported) as good, zipfile.ZipFile(forged, "w") as bad:
        for name in good.namelist():
            bad.writestr(name, good.read(name))
        bad.writestr(member, b"OUT")
    return forged


@pytest.mark.parametrize(
    "member",
    [
        "covers//evil.txt",
        "covers/\\evil.txt",
        "covers/C:evil.txt",
        "covers/C:\\evil.txt",
        "covers//server/share/evil.txt",
        "covers/./evil.txt",
    ],
)
async def test_a_member_named_as_a_root_a_drive_or_a_share_is_refused_whole(
    backup: BackupService, settings: Settings, tmp_path: Path, member: str
) -> None:
    """Names that are harmless on one system and a root, a drive or a network share on another.

    `covers//evil.txt` is not absolute and has no `..` in it, and on Windows it joins to the root
    of the drive. Nothing is written anywhere, and the live folder is as it was.
    """
    cover = settings.cache_dir / "covers" / "01C.jpg"
    cover.parent.mkdir(parents=True)
    cover.write_bytes(b"COVER")
    at_the_root = Path(tmp_path.anchor) / "evil.txt"
    assert not at_the_root.exists()
    forged = await _forged_with(backup, tmp_path, member)

    with pytest.raises(NotABackup):
        await backup.restore(forged)

    assert not at_the_root.exists()
    assert cover.read_bytes() == b"COVER"
    assert not cover.parent.with_name("covers" + INCOMING_SUFFIX).exists()


@WINDOWS_ONLY
def test_a_member_that_resolves_out_of_its_folder_is_refused(tmp_path: Path) -> None:
    """The name is checked as text and the place it resolves to is confined as well, so a folder
    that leads somewhere else is no way out either."""
    into = tmp_path / "covers"
    outside = tmp_path / "elsewhere"
    into.mkdir()
    outside.mkdir()
    junction(into / "link", outside)

    assert _member_target(into, "a/01C.jpg") == (into / "a" / "01C.jpg").resolve()
    with pytest.raises(NotABackup):
        _member_target(into, "link/evil.txt")


async def test_a_backup_that_would_not_fit_on_the_drive_is_refused_before_anything_is_written(
    backup: BackupService,
    prepared_db: Database,
    world: World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sizes an archive declares are what is checked, and they are also the most zipfile will
    write, so an archive that unpacks to more than the drive has cannot fill it."""
    exported = tmp_path / "export.zip"
    await backup.export_to(exported)
    monkeypatch.setattr(backup_service, "free_bytes", lambda _place: 0)

    with pytest.raises(NoRoomToUnpack):
        await backup.restore(exported)

    assert not prepared_db.path.with_name(prepared_db.path.name + INCOMING_SUFFIX).exists()
    assert await prepared_db.fetch_one("SELECT id FROM assets WHERE id = ?", (world.solo,))


async def test_a_database_whose_views_call_what_sqlite_calls_unsafe_is_refused(
    backup: BackupService, prepared_db: Database, tmp_path: Path
) -> None:
    """An arriving database is opened with its schema distrusted, so a view or trigger in it runs
    only what SQLite knows to be harmless. `MATCH` stands in for anything that is not."""
    bare = tmp_path / "crafted.sqlite3"
    async with prepared_db.write() as connection:
        await connection.execute("VACUUM INTO ?", (str(bare),))
    crafted = sqlite3.connect(bare)
    crafted.executescript(
        """
        CREATE VIRTUAL TABLE words USING fts5(word);
        INSERT INTO words (word) VALUES ('stamp');
        CREATE VIEW backup_manifest AS
          SELECT 2 AS format_version, 'x' AS app_version, 1 AS created_at
          FROM words WHERE words MATCH 'stamp';
        """
    )
    crafted.close()

    with pytest.raises(NotABackup):
        await backup.inspect(bare)


def _damage(archive: Path, member: str) -> None:
    """Break one member's own header, leaving the archive's directory (and every other member,
    the manifest included) readable. A backup cut short or hit by a bad sector looks like this."""
    with zipfile.ZipFile(archive) as reading:
        offset = reading.getinfo(member).header_offset
    with archive.open("r+b") as handle:
        handle.seek(offset)
        handle.write(b"PK\x03\x05")


async def test_a_backup_whose_database_is_damaged_is_refused_and_nothing_changes(
    backup: BackupService, prepared_db: Database, tmp_path: Path
) -> None:
    """Refused as not a backup, the sentence every other unreadable file gets, before anything is
    moved, never a failure half way through a restore."""
    await prepared_db.execute("INSERT INTO tags (id, name, created_at) VALUES ('01T', 'kept', 1)")
    exported = tmp_path / "export.zip"
    await backup.export_to(exported)
    _damage(exported, DATABASE_MEMBER)

    with pytest.raises(NotABackup):
        await backup.restore(exported)

    row = await prepared_db.fetch_one("SELECT name FROM tags")
    assert row is not None and row["name"] == "kept"


async def test_a_backup_whose_carried_folder_is_damaged_is_refused_whole(
    backup: BackupService, settings: Settings, tmp_path: Path
) -> None:
    reference = settings.data_dir / "faces" / "references" / "01P" / "01R.jpg"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"REFERENCE-FACE")
    exported = tmp_path / "export.zip"
    await backup.export_to(exported)
    _damage(exported, "faces/references/01P/01R.jpg")
    reference.write_bytes(b"AFTER-THE-BACKUP")

    with pytest.raises(NotABackup):
        await backup.restore(exported)

    assert reference.read_bytes() == b"AFTER-THE-BACKUP"


async def test_a_backup_from_before_the_archive_becomes_a_new_library_as_it_is(
    backup: BackupService, prepared_db: Database, tmp_path: Path
) -> None:
    """Import's unpacking of a bare database: copied in, its stamp taken out, nothing carried,
    and a data folder that already holds a library is refused rather than written over."""
    bare = tmp_path / "old-style.sqlite3"
    async with prepared_db.write() as connection:
        await connection.execute("VACUUM INTO ?", (str(bare),))
    await backup._stamp(bare)
    data_dir, cache_dir = tmp_path / "new" / "data", tmp_path / "new" / "cache"

    manifest = await backup.unpack_into(bare, data_dir, cache_dir)

    assert manifest["carried"] == []
    made = sqlite3.connect(data_dir / DATABASE_MEMBER)
    try:
        assert made.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] > 0
    finally:
        made.close()
    with pytest.raises(FileExistsError):
        await backup.unpack_into(bare, data_dir, cache_dir)


async def test_a_restore_is_on_the_restored_library_s_history_as_whoever_pressed_it(
    backup: BackupService, prepared_db: Database, tmp_path: Path, actors: Actors
) -> None:
    """A restore is said where a person reads, not only in the application log. It is written into
    the library that came back, naming the backup by its day and the admin who pressed Restore, a
    user that library has a row for."""
    exported = tmp_path / "export.sqlite3"
    await backup.export_to(exported)

    await backup.restore(exported, actors.admin)

    rows = await prepared_db.fetch_all(
        "SELECT d.actor_kind AS actor_kind, d.actor_id AS actor_id, s.kind AS kind,"
        " s.name AS name FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id WHERE d.verb = 'restored'"
    )
    assert [tuple(row) for row in rows] == [
        ("user", actors.admin.id, "backup", "the backup from 14 November 2023")
    ]


async def test_a_restore_by_somebody_the_backup_never_knew_is_the_backup_task_s(
    backup: BackupService, prepared_db: Database, tmp_path: Path, actors: Actors
) -> None:
    """A backup from another install has its own users: the line names nobody it has no row for."""
    exported = tmp_path / "export.sqlite3"
    await backup.export_to(exported)
    stranger = replace(actors.admin, id="01HX00000000000000000000ZZ")

    await backup.restore(exported, stranger)

    rows = await prepared_db.fetch_all(
        "SELECT actor_kind, actor_id FROM workbench_decisions WHERE verb = 'restored'"
    )
    assert [tuple(row) for row in rows] == [("sift", "backup")]
