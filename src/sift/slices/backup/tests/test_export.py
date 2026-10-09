# SPDX-License-Identifier: AGPL-3.0-or-later
"""What an export is, what it is not, and what rotation is allowed to delete.

The first test in this file is the one worth reading. It is the difference between a backup and a
file that looks like one, it fails if `VACUUM INTO` is ever swapped for a copy, and the bug it
guards against is invisible for as long as nobody needs the backup.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from sift.kernel.config import Settings
from sift.kernel.content import LibraryStore
from sift.kernel.db import Database
from sift.slices.backup import naming
from sift.slices.backup import service as backup_service
from sift.slices.backup.naming import (
    filename_for,
    is_backup_filename,
    moment_in_name,
    the_backup_from,
)
from sift.slices.backup.service import (
    FOLDER_KEY,
    INCLUDE_DETECTED_KEY,
    KEEP_KEY,
    BackupService,
    DestinationRefused,
)
from sift.slices.backup.tests.conftest import a_full_path, database_in
from sift.slices.settings_hub import SettingsService
from sift.testing.fixtures import Actors, FakeClock
from sift.testing.tools import POSIX_ONLY

pytestmark = pytest.mark.anyio


def rows_in(path: Path, sql: str) -> list[tuple[object, ...]]:
    """Read a file directly, as a stranger with a SQLite client would. No Sift involved."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return list(connection.execute(sql))
    finally:
        connection.close()


def saw_nothing(path: Path, sql: str) -> bool:
    """Whether a file holds none of what the query asks for.

    A database behind its write-ahead log can be short of the row or short of the whole table,
    depending on what has been checkpointed, and both mean the same thing here: the copy did not
    see the write.
    """
    try:
        return rows_in(path, sql) == []
    except sqlite3.OperationalError:
        return True


async def test_an_export_taken_from_a_wal_database_holds_the_newest_writes(
    backup: BackupService, prepared_db: Database, tmp_path: Path
) -> None:
    """The test that says why this feature does not copy a file.

    In WAL mode a committed write lives in the side file until a checkpoint moves it into the main
    one, and a checkpoint has not necessarily happened. So the `.sqlite3` file on its own is behind,
    and often, for a small database, empty. Copying it produces a backup that opens without
    complaint, restores without complaint, and is missing everything recent.

    Both halves are asserted, and the second is what makes the first mean anything: the export is
    complete, AND a plain copy of the same database taken at the same moment is not. Without the
    second half this passes just as happily against a copy on any machine that happened to
    checkpoint.

    The control copy is taken *before* the export, not after, and that ordering is load-bearing:
    `VACUUM INTO` checkpoints the database it read, so a copy taken afterwards has caught up and
    the control silently stops being one.
    """
    await prepared_db.execute(
        "INSERT INTO tags (id, name, created_at) VALUES ('01T', 'written-just-now', 1)"
    )

    naive = tmp_path / "naive-copy.sqlite3"
    shutil.copyfile(prepared_db.path, naive)

    exported = tmp_path / "export.sqlite3"
    await backup.export_to(exported)

    assert rows_in(database_in(exported), "SELECT name FROM tags") == [("written-just-now",)]
    assert saw_nothing(naive, "SELECT name FROM tags"), (
        "a plain copy of this database saw the new row, so this test can no longer tell a "
        "snapshot from a copy: it has stopped proving anything"
    )


async def test_an_export_refuses_to_write_over_a_file_that_is_already_there(
    backup: BackupService, tmp_path: Path
) -> None:
    """Rotation keeps the last few backups. Silently overwriting one would make that a lie."""
    destination = tmp_path / "export.zip"
    await backup.export_to(destination)

    with pytest.raises(FileExistsError):
        await backup.export_to(destination)


async def test_the_export_carries_no_media_bytes(
    backup: BackupService, prepared_db: Database, tmp_path: Path
) -> None:
    """A backup holds the library's records and not the library.

    Asserted two ways because either alone is weak. The distinctive bytes of a real file on disk
    are nowhere in the export, and the export is metadata-scale rather than media-scale: a
    megabyte of video and an export smaller than it cannot both be true if the video went in.
    """
    marker = b"THIS-IS-MEDIA-CONTENT-NOT-METADATA"
    media = tmp_path / "clip.mp4"
    media.write_bytes(marker + b"\x00" * (4 * 1024 * 1024))

    await prepared_db.execute(
        "INSERT INTO assets (id, identity, media_type, added_at) VALUES ('01A', 'digest', 'video', 1)"
    )
    await prepared_db.execute(
        "INSERT INTO library_roots (id, name, abs_path, created_at) VALUES ('01R', 'r', ?, 1)",
        (str(tmp_path),),
    )
    await prepared_db.execute(
        "INSERT INTO asset_locations "
        "(id, asset_id, root_id, rel_path, filename, first_seen_at, last_seen_at) "
        "VALUES ('01L', '01A', '01R', 'clip.mp4', 'clip.mp4', 1, 1)"
    )

    exported = tmp_path / "export.sqlite3"
    await backup.export_to(exported)

    assert marker not in exported.read_bytes()
    assert exported.stat().st_size < media.stat().st_size
    # The record of the file is there. It is the bytes that are not, which is the whole point:
    # re-scan the folder and this row finds its file again.
    assert rows_in(database_in(exported), "SELECT filename FROM asset_locations") == [("clip.mp4",)]


async def test_the_export_says_what_wrote_it(backup: BackupService, tmp_path: Path) -> None:
    """A backup that cannot say what it is has to be guessed at by whatever restores it. Both
    the archive and the database inside it say so, so the database on its own is still Sift's."""
    exported = tmp_path / "export.zip"
    await backup.export_to(exported)

    with zipfile.ZipFile(exported) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["format"] == 2
    assert manifest["app_version"]
    assert manifest["carried"] == ["faces/references", "covers"]
    stamped = rows_in(
        database_in(exported), "SELECT format_version, app_version FROM backup_manifest"
    )
    assert len(stamped) == 1
    assert stamped[0][0] == 2
    assert stamped[0][1]


async def test_the_export_carries_the_folders_nothing_can_rebuild(
    backup: BackupService,
    settings: Settings,
    tmp_path: Path,
    preferences: SettingsService,
    actors: Actors,
) -> None:
    """The reference faces somebody enrolled and the covers somebody uploaded go in every
    backup; the detected faces, which a Build finds again, go in only when asked for."""
    reference = settings.data_dir / "faces" / "references" / "01P" / "01R.jpg"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"REFERENCE-FACE")
    cover = settings.cache_dir / "covers" / "01U.jpg"
    cover.parent.mkdir(parents=True)
    cover.write_bytes(b"UPLOADED-COVER")
    detected = settings.data_dir / "faces" / "detected" / "01T.jpg"
    detected.parent.mkdir(parents=True)
    detected.write_bytes(b"DETECTED-FACE")

    exported = tmp_path / "export.zip"
    await backup.export_to(exported)
    with zipfile.ZipFile(exported) as archive:
        names = set(archive.namelist())
    assert "faces/references/01P/01R.jpg" in names
    assert "covers/01U.jpg" in names
    assert not any(name.startswith("faces/detected/") for name in names), "not unless asked"

    await preferences.apply(actors.admin, {INCLUDE_DETECTED_KEY: True})
    asked = tmp_path / "asked.zip"
    await backup.export_to(asked)
    with zipfile.ZipFile(asked) as archive:
        assert "faces/detected/01T.jpg" in set(archive.namelist())
    told = await backup.contents()
    assert [(one["name"], one["files"], one["included"]) for one in told] == [
        ("faces/references", 1, True),
        ("covers", 1, True),
        ("faces/detected", 1, True),
    ]


async def test_a_picture_taken_away_while_the_screen_measures_is_left_out_not_a_failure(
    backup: BackupService, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The faces pass rewrites the detected pictures while `Settings > Backup` measures them, so a
    file listed a moment ago can be gone by the time its size is read."""
    detected = settings.data_dir / "faces" / "detected"
    detected.mkdir(parents=True)
    (detected / "01K.jpg").write_bytes(b"KEPT-FACE")
    listed = backup_service._files_under

    def listed_then_one_taken(folder: Path) -> list[Path]:
        return [*listed(folder), folder / "01G.jpg"]

    monkeypatch.setattr(backup_service, "_files_under", listed_then_one_taken)

    told = {one["name"]: (one["files"], one["bytes"]) for one in await backup.contents()}

    assert told["faces/detected"] == (1, len(b"KEPT-FACE"))


# --- where it goes --------------------------------------------------------------------------


async def test_the_default_destination_is_a_folder_sift_owns(
    backup: BackupService, settings: Settings
) -> None:
    """It works everywhere with nothing configured, and the screen says why that is not enough."""
    assert await backup.destination() == settings.data_dir / "backups"
    assert (settings.data_dir / "backups").is_dir()


async def test_a_chosen_folder_inside_the_media_area_is_used(
    backup: BackupService, preferences: SettingsService, elsewhere: Path, actors: Actors
) -> None:
    await preferences.apply(actors.admin, {FOLDER_KEY: str(elsewhere)})
    assert await backup.destination() == elsewhere.resolve()


async def test_a_chosen_folder_is_granted_on_its_first_use(
    backup: BackupService,
    preferences: SettingsService,
    prepared_db: Database,
    settings: Settings,
    tmp_path: Path,
    actors: Actors,
) -> None:
    """A backup folder is a use of a granted folder, like a library: choosing one hands it over.

    The boundary that stays is the library one (below), and writability. A rule that the folder
    be on a second list of given folders first would make every chosen folder a two-step chore and
    the list a screen of its own.
    """
    outside = tmp_path / "not-media"
    outside.mkdir()
    await preferences.apply(actors.admin, {FOLDER_KEY: str(outside)})

    assert await backup.destination() == outside.resolve()
    granted = [Path(grant.abs_path) for grant in await LibraryStore(prepared_db, settings).grants()]
    assert any(outside.resolve() == one or one in outside.resolve().parents for one in granted)


async def test_the_check_on_a_folder_makes_no_grant(
    backup: BackupService, prepared_db: Database, settings: Settings, tmp_path: Path
) -> None:
    """The settings door checks a folder without handing it over: a check makes nothing."""
    chosen = tmp_path / "drive-d" / "sift-backups"
    chosen.mkdir(parents=True)
    store = LibraryStore(prepared_db, settings)
    before = len(await store.grants())

    assert await backup.resolve_folder(str(chosen)) == chosen.resolve()
    assert len(await store.grants()) == before
    assert await backup.resolve_folder(str(chosen), ensuring=True) == chosen.resolve()
    assert len(await store.grants()) == before + 1


async def test_a_folder_inside_a_library_is_refused(
    backup: BackupService, prepared_db: Database, settings: Settings, tmp_path: Path
) -> None:
    """A backup is a `.zip`, and a zip in a watched library is indexed in place as a gallery."""
    given = tmp_path / "drive-d"
    library = given / "videos"
    (library / "sift-backups").mkdir(parents=True)
    store = LibraryStore(prepared_db, settings)
    await store.grant(given)
    await store.create_root(name="Videos", abs_path=library)

    with pytest.raises(DestinationRefused, match="part of your library"):
        await backup.resolve_folder(str(library / "sift-backups"))
    # Beside the library, in the same granted folder, is fine.
    beside = given / "sift-backups"
    beside.mkdir()
    assert await backup.resolve_folder(str(beside)) == beside.resolve()


async def test_a_folder_reached_by_climbing_out_of_the_media_area_is_refused(
    backup: BackupService, settings: Settings
) -> None:
    """A relative climb is refused by the same resolve-and-compare, not by looking at the string."""
    with pytest.raises(DestinationRefused):
        await backup.resolve_folder(str(settings.data_dir.parent / "media" / ".." / "data"))


async def test_a_folder_that_is_not_there_is_refused(
    backup: BackupService, settings: Settings
) -> None:
    with pytest.raises(DestinationRefused):
        await backup.resolve_folder(str(settings.data_dir.parent / "media" / "unmounted"))


@POSIX_ONLY
async def test_a_folder_that_cannot_be_written_to_is_refused(
    backup: BackupService, elsewhere: Path
) -> None:
    """Caught here, in a sentence, rather than as a failed write in tonight's job log."""
    elsewhere.chmod(0o500)
    try:
        with pytest.raises(DestinationRefused):
            await backup.resolve_folder(str(elsewhere))
    finally:
        elsewhere.chmod(0o700)


# --- rotation -------------------------------------------------------------------------------


async def test_rotation_keeps_exactly_the_last_n(
    backup: BackupService,
    preferences: SettingsService,
    fake_clock: FakeClock,
    elsewhere: Path,
    actors: Actors,
) -> None:
    """The whole of the "keep last N" promise, asserted on the names that survive.

    Five backups a day apart, keeping three: the two oldest go and the three newest stay, and
    which three is checked rather than merely how many. A rotation that kept the wrong end would
    pass a count.
    """
    await preferences.apply(actors.admin, {FOLDER_KEY: str(elsewhere), KEEP_KEY: 3})

    written = []
    for _ in range(5):
        written.append(await backup.run_scheduled())
        fake_clock.advance(24 * 3600)

    survivors = sorted(entry.name for entry in elsewhere.iterdir())
    assert survivors == sorted(path.name for path in written[2:])


async def test_the_plan_names_what_the_run_writes_and_deletes_and_does_neither(
    backup: BackupService,
    preferences: SettingsService,
    fake_clock: FakeClock,
    elsewhere: Path,
    actors: Actors,
) -> None:
    """A dry run reads `plan` and the run carries the same plan out, so the file it names is the
    file written and the old ones it names are the ones deleted. Planning writes nothing."""
    await preferences.apply(actors.admin, {FOLDER_KEY: str(elsewhere), KEEP_KEY: 2})
    written = [await backup.run_scheduled()]
    fake_clock.advance(24 * 3600)
    written.append(await backup.run_scheduled())
    fake_clock.advance(24 * 3600)
    before = sorted(entry.name for entry in elsewhere.iterdir())

    planned = await backup.plan()
    assert sorted(entry.name for entry in elsewhere.iterdir()) == before
    assert planned.drop == (written[0],)

    assert await backup.run_scheduled() == planned.file
    assert sorted(entry.name for entry in elsewhere.iterdir()) == sorted(
        [written[1].name, planned.file.name]
    )


async def test_the_plan_of_the_default_folder_makes_no_folder(
    backup: BackupService, settings: Settings
) -> None:
    """The folder Sift owns is made by a backup, never by asking what one would do."""
    planned = await backup.plan()
    assert planned.drop == ()
    assert not (settings.data_dir / "backups").exists()


async def test_rotation_never_touches_a_file_this_feature_did_not_write(
    backup: BackupService,
    preferences: SettingsService,
    fake_clock: FakeClock,
    elsewhere: Path,
    actors: Actors,
) -> None:
    """The destination belongs to whoever chose it, and may hold anything.

    "Keep the last few backups" must never become "delete the oldest file in a folder". The
    stranger's file below is older than every backup and is the first thing a rotation working on
    ages rather than on names would take.
    """
    stranger = elsewhere / "holiday-photos.zip"
    stranger.write_bytes(b"not a backup")
    lookalike = elsewhere / "sift-backup-notes.txt"
    lookalike.write_text("also not a backup")

    await preferences.apply(actors.admin, {FOLDER_KEY: str(elsewhere), KEEP_KEY: 1})
    for _ in range(3):
        await backup.run_scheduled()
        fake_clock.advance(24 * 3600)

    assert stranger.is_file()
    assert lookalike.is_file()
    mine = await backup.library_mark()
    assert len([e for e in elsewhere.iterdir() if is_backup_filename(e.name, library=mine)]) == 1


_MINE = "0123456789ab"
_THEIRS = "ba9876543210"


def test_only_a_name_this_library_produces_is_ever_deletable() -> None:
    """The predicate rotation is built on, put to the names that are and are not this library's."""
    assert is_backup_filename(filename_for(1_700_000_000.0, library=_MINE), library=_MINE)
    assert filename_for(1_700_000_000.0, library=_MINE).endswith(".zip")
    # Another library's backup in the same folder is never this one's to rotate.
    assert not is_backup_filename(filename_for(1_700_000_000.0, library=_THEIRS), library=_MINE)
    # A name with no library's mark says nothing about whose it is, so nobody's rotation takes it.
    assert not is_backup_filename("sift-backup-20260720-141500-1.0.0.zip", library=_MINE)
    assert not is_backup_filename("sift-backup-20260720-141500-1.0.0.sqlite3", library=_MINE)
    assert not is_backup_filename("sift-backup.sqlite3", library=_MINE)
    assert not is_backup_filename("holiday.mp4", library=_MINE)
    assert not is_backup_filename("sift-backup-notes.txt", library=_MINE)


def test_a_backup_is_named_on_the_machines_clock_with_its_offset(
    machine_zone: Callable[[str], None],
) -> None:
    """The name reads the clock on the wall where the backup was taken, and still says the exact
    moment: a name written in UTC before names carried an offset is read back as the same moment."""
    machine_zone("EST5EDT")
    evening = 1_789_529_400  # 23:30 on 15 September 2026 in New York, 03:30 on the 16th in UTC
    name = filename_for(evening, library=_MINE)
    assert f"sift-backup-{_MINE}-20260915-233000-0400-" in name
    assert is_backup_filename(name, library=_MINE)
    assert moment_in_name(name) == evening
    older = f"sift-backup-{_MINE}-20260916-033000-0.1.203.zip"
    assert is_backup_filename(older, library=_MINE)
    assert moment_in_name(older) == evening
    assert moment_in_name("holiday.mp4") is None
    assert the_backup_from(evening) == "the backup from 15 September 2026"


def test_rotation_orders_by_the_moment_not_the_spelling(
    machine_zone: Callable[[str], None],
) -> None:
    """The hour the clocks go back is spelled twice: 01:30 daylight time comes before 01:10
    standard time, though it sorts after it as text. Rotation keeps the newest by the moment."""
    machine_zone("EST5EDT")
    first = Path(f"sift-backup-{_MINE}-20261101-013000-0400-0.1.211.zip")
    second = Path(f"sift-backup-{_MINE}-20261101-011000-0500-0.1.211.zip")
    assert sorted([second, first]) == [second, first]
    assert sorted([second, first], key=naming._by_age) == [first, second]


def test_the_name_sorts_by_age() -> None:
    """Rotation sorts names rather than asking the filesystem for dates, so the names must sort."""
    early = filename_for(1_700_000_000.0, library=_MINE)
    later = filename_for(1_700_086_400.0, library=_MINE)
    assert early < later


async def test_saving_while_only_a_press_runs_it_does_not_ask_where_the_backups_would_go(
    backup: BackupService, preferences: SettingsService, actors: Actors
) -> None:
    """Saving has to work even when the folder it was pointing at has gone, while nothing starts
    a backup on its own (the When is Only when I press it, which is how a failing schedule is
    stopped).

    Otherwise the one action that stops a failing schedule is refused by the same thing that is
    making it fail, and there is no way out of it from the screen.
    """
    gone = a_full_path("somewhere", "unmounted")
    await backup.set_schedule(actors.admin, keep=3, folder=gone, every_days=2)

    assert await preferences.get_app("backup.every_days") == 2
    assert await preferences.get_app("backup.folder") == gone


async def test_a_folder_the_filesystem_will_not_take_a_write_into_is_refused(
    backup: BackupService,
    preferences: SettingsService,
    elsewhere: Path,
    actors: Actors,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal itself, on a site whose permission bits cannot produce it.

    The test above arranges the situation with `chmod`, which Windows accepts and ignores. What is
    checked here is what happens when the answer is no, whatever produced it: a sentence naming the
    two reasons somebody can act on (read-only, or somebody else's) rather than a backup that
    starts and fails partway.
    """
    # A folder Sift can reach, so the refusal under test is the one about the WRITE rather than
    # the boundary check one line above it.
    await preferences.apply(actors.admin, {FOLDER_KEY: str(elsewhere)})
    monkeypatch.setattr(backup_service, "is_writable", lambda _folder: False)

    with pytest.raises(DestinationRefused, match="read-only, or it belongs to another user"):
        await backup.destination()


async def test_each_backup_the_rotation_deletes_is_on_history(
    backup: BackupService,
    prepared_db: Database,
    preferences: SettingsService,
    fake_clock: FakeClock,
    elsewhere: Path,
    actors: Actors,
) -> None:
    """A backup is the way back to a day, and one the rotation deleted went with nothing saying
    so. Each deletion is a `deleted` line naming the backup by its day, Sift's backup task the
    actor."""
    await preferences.apply(actors.admin, {FOLDER_KEY: str(elsewhere), KEEP_KEY: 2})
    for _ in range(3):
        await backup.run_scheduled()
        fake_clock.advance(24 * 3600)

    rows = await prepared_db.fetch_all(
        "SELECT d.actor_kind AS actor_kind, d.actor_id AS actor_id, s.kind AS kind,"
        " s.name AS name FROM workbench_decisions d"
        " JOIN workbench_decision_subjects s ON s.decision_id = d.id WHERE d.verb = 'deleted'"
    )
    assert [tuple(row) for row in rows] == [
        ("sift", "backup", "backup", "the backup from 14 November 2023")
    ]


def test_a_backup_being_packed_is_not_listed_until_it_is_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backup still being written is not listed: Delete pressed on it would be refused by
    Windows, and the list would offer it at a fraction of its size."""
    folder = tmp_path / "backups"
    folder.mkdir()
    snapshot = tmp_path / "database.snapshot"
    snapshot.write_bytes(b"database")
    covers = tmp_path / "covers"
    covers.mkdir()
    (covers / "cover.jpg").write_bytes(b"cover")
    mark = "0123456789ab"
    destination = folder / f"sift-backup-{mark}-20261002-083558-0400-0.1.215-saved.zip"
    listed_while_packing: list[list[str]] = []
    files_under = backup_service._files_under

    def watching(path: Path) -> list[Path]:
        listed = naming._unmarked_in(folder, mark)
        listed_while_packing.append([one.name for one in listed])
        return files_under(path)

    monkeypatch.setattr(backup_service, "_files_under", watching)
    carried = [backup_service.Carried("covers", "Uploaded covers", covers, True)]

    backup_service._pack(destination, snapshot, carried, {})

    assert listed_while_packing == [[]]
    assert [one.name for one in naming._unmarked_in(folder, mark)] == [destination.name]
    assert sorted(entry.name for entry in folder.iterdir()) == [destination.name]


def test_a_carried_file_taken_away_while_packing_is_left_out_and_the_backup_is_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The faces pass deletes a detected picture between the listing and its packing: the backup
    is still made, of every file that is there."""
    snapshot = tmp_path / "database.snapshot"
    snapshot.write_bytes(b"database")
    detected = tmp_path / "detected"
    detected.mkdir()
    (detected / "kept.jpg").write_bytes(b"face")
    files_under = backup_service._files_under
    monkeypatch.setattr(
        backup_service, "_files_under", lambda path: [*files_under(path), path / "gone.jpg"]
    )
    destination = tmp_path / "backup.zip"
    carried = [backup_service.Carried("detected", "Detected faces", detected, False)]

    backup_service._pack(destination, snapshot, carried, {})

    with zipfile.ZipFile(destination) as archive:
        packed = [name for name in archive.namelist() if name.startswith("detected/")]
    assert packed == ["detected/kept.jpg"]
