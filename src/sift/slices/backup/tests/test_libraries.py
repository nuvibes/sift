# SPDX-License-Identifier: AGPL-3.0-or-later
"""Making libraries, importing one, and asking to be started on another.

The supervisor is a recorder: whether the process can be restarted, and whether it was asked. What
these pin is everything decided BEFORE that ask: that a refusal leaves no folder and no note, that
an older library gets its backup copy first, that a newer one is refused with the sentence every
other door uses, and that the note names the library the page chose and nothing a request carried.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from sift.kernel.access import Role, Viewer
from sift.kernel.config import Settings, libraries_folder
from sift.kernel.db import (
    DATABASE_FILENAME,
    Database,
    adopt_database,
    execute_blocking,
    fetch_blocking,
    too_old_to_bring_forward,
)
from sift.slices.backup.libraries import (
    HANDOFF_FILENAME,
    OPENS_AT_START,
    ORIGIN_FILENAME,
    REGISTRY_FILENAME,
    BadName,
    LibrariesService,
    NameTaken,
    NeedsUpgrade,
    NoSupervisor,
    NotALibrary,
    NotDeletable,
    UnknownLibrary,
    check_name,
    file_name_said,
    library_id,
)
from sift.slices.backup.recycle import NoRecycleBin
from sift.slices.backup.service import MANIFEST_TABLE, BackupService
from sift.testing.schema_versions import wind_below_a_baseline, wind_one_back

MAKER = Viewer(id="maker-1", role=Role.ADMIN)


@dataclass
class Supervisor:
    """What `sift.kernel.lifecycle` answers, recorded."""

    present: bool = True
    agrees: bool = True
    asked: list[bool] = field(default_factory=list)

    def can(self) -> bool:
        return self.present

    def ask(self) -> bool:
        self.asked.append(True)
        return self.present and self.agrees


@pytest.fixture
def supervisor() -> Supervisor:
    return Supervisor()


@pytest.fixture
async def service(
    prepared_db: Database, settings: Settings, backup: BackupService, supervisor: Supervisor
) -> LibrariesService:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await prepared_db.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at) "
        "VALUES (?, 'ada', 'hash-of-a-password', 'admin', 1)",
        (MAKER.id,),
    )
    return LibrariesService(
        prepared_db, settings, backup, can_restart=supervisor.can, ask_to_restart=supervisor.ask
    )


def a_library(prepared_db: Database, data_dir: Path) -> Path:
    """A copy of the prepared database as another library's, in `data_dir`."""
    data_dir.mkdir(parents=True)
    return adopt_database(prepared_db.path, data_dir)


def shift_a_component(database: Path, by: int) -> None:
    """Move one component's recorded version forward, so the file reads as newer than this build.

    Not backward: one step back from the pin can land below a component's baseline, which reads as
    unreadable rather than older. `wind_back` chooses a component for that.
    """
    execute_blocking(
        database,
        "UPDATE schema_version SET version = version + ? WHERE component = "
        "(SELECT component FROM schema_version WHERE version > 1 ORDER BY component LIMIT 1)",
        (by,),
    )


def recorded_versions(database: Path) -> dict[str, int]:
    """Each component's version as the file records it."""
    rows = fetch_blocking(database, "SELECT component, version FROM schema_version")
    return {str(component): int(str(version)) for component, version in rows}


def wind_back(database: Path, *, below_baseline: bool = False) -> str:
    """Record one component as an older Sift left it, and say which one.

    One version behind is a library this build brings forward; below a baseline is one it cannot.
    Which component gives which is the registry's answer, so it is chosen, never named.
    """
    versions = recorded_versions(database)
    name = wind_below_a_baseline(versions) if below_baseline else wind_one_back(versions)
    execute_blocking(
        database,
        "UPDATE schema_version SET version = ? WHERE component = ?",
        (versions[name], name),
    )
    return name


def the_refusal_for(database: Path) -> str:
    """The sentence the library reading gives for a database too old to bring forward."""
    versions = recorded_versions(database)
    said = too_old_to_bring_forward(versions)
    assert said is not None
    return said


def the_note(settings: Settings) -> dict[str, str]:
    written = json.loads((settings.data_dir / HANDOFF_FILENAME).read_text(encoding="utf-8"))
    assert isinstance(written, dict)
    return {str(key): str(value) for key, value in written.items()}


async def test_the_list_holds_the_running_library_first_then_the_folder(
    service: LibrariesService, settings: Settings, prepared_db: Database
) -> None:
    a_library(prepared_db, service.folder / "Work" / "data")
    listed = await service.listed()
    assert [one["name"] for one in listed][1:] == ["Work"]
    assert listed[0]["current"] is True
    assert listed[0]["data_dir"] == str(settings.data_dir)
    assert listed[1]["verdict"] == "current"
    assert listed[1]["in_folder"] is True


async def test_a_library_too_old_to_bring_forward_is_listed_with_the_reason(
    service: LibrariesService, prepared_db: Database
) -> None:
    """The mark alone reads the same for a stranger's file and for a library an older Sift can
    bring up to date, so the reading's own sentence travels with it."""
    database = a_library(prepared_db, service.folder / "Far" / "data")
    wind_back(database, below_baseline=True)
    stranger = service.folder / "Stranger" / "data"
    stranger.mkdir(parents=True)
    (stranger / DATABASE_FILENAME).write_bytes(b"this is not a database, it is a letter" * 100)

    listed = {one["name"]: one for one in await service.listed()}

    assert listed["Far"]["verdict"] == "unreadable"
    assert listed["Far"]["detail"] == the_refusal_for(database)
    assert (listed["Stranger"]["verdict"], listed["Stranger"]["detail"]) == ("unreadable", "")
    assert all(one["detail"] == "" for name, one in listed.items() if name != "Far")


async def test_create_makes_an_empty_library_with_its_maker_and_asks_to_restart(
    service: LibrariesService, settings: Settings, supervisor: Supervisor
) -> None:
    made = await service.create("Work", MAKER)

    database = service.folder / "Work" / "data" / DATABASE_FILENAME
    users = fetch_blocking(database, "SELECT id, username, role FROM users")
    components = int(str(fetch_blocking(database, "SELECT COUNT(*) FROM schema_version")[0][0]))
    sessions = int(str(fetch_blocking(database, "SELECT COUNT(*) FROM sessions")[0][0]))
    assert users == [(MAKER.id, "ada", "admin")]
    assert components > 0
    assert sessions == 0
    assert (service.folder / "Work" / "cache").is_dir()
    assert the_note(settings) == {
        "data_dir": str(service.folder / "Work" / "data"),
        "cache_dir": str(service.folder / "Work" / "cache"),
    }
    assert supervisor.asked == [True]
    assert made.id == library_id(service.folder / "Work" / "data")
    # The library being left is written down, so the way back is on the list from inside the new one.
    registry = json.loads((service.folder / REGISTRY_FILENAME).read_text(encoding="utf-8"))
    assert registry["elsewhere"] == [
        {"data_dir": str(settings.data_dir), "cache_dir": str(settings.cache_dir)}
    ]


async def test_a_member_finds_the_same_folder_from_inside_it(service: LibrariesService) -> None:
    await service.create("Work", MAKER)
    assert libraries_folder(service.folder / "Work" / "data") == service.folder


async def test_nothing_is_made_where_nothing_would_start_it_again(
    service: LibrariesService, settings: Settings, supervisor: Supervisor
) -> None:
    supervisor.present = False
    with pytest.raises(NoSupervisor):
        await service.create("Work", MAKER)
    assert not service.folder.exists()
    assert not (settings.data_dir / HANDOFF_FILENAME).exists()


async def test_a_name_already_taken_is_refused_whatever_its_case(
    service: LibrariesService, prepared_db: Database
) -> None:
    a_library(prepared_db, service.folder / "Work" / "data")
    with pytest.raises(NameTaken):
        await service.create("work", MAKER)


@pytest.mark.parametrize("name", ["", "../up", "a/b", "C:x", ".hidden", "con", "Nul.txt", "x" * 65])
def test_a_name_that_is_not_one_folder_name_is_refused(name: str) -> None:
    with pytest.raises(BadName):
        check_name(name)


async def test_a_newer_library_is_refused_and_nothing_is_written(
    service: LibrariesService, settings: Settings, prepared_db: Database, supervisor: Supervisor
) -> None:
    database = a_library(prepared_db, service.folder / "Later" / "data")
    shift_a_component(database, +1)
    with pytest.raises(NotALibrary, match="Update Sift, then open it again"):
        await service.open(library_id(database.parent), upgrade=True)
    assert not (settings.data_dir / HANDOFF_FILENAME).exists()
    assert supervisor.asked == []


async def test_an_older_library_is_asked_about_then_backed_up_before_the_switch(
    service: LibrariesService, settings: Settings, prepared_db: Database
) -> None:
    database = a_library(prepared_db, service.folder / "Earlier" / "data")
    wind_back(database)
    with pytest.raises(NeedsUpgrade):
        await service.open(library_id(database.parent))
    assert not (settings.data_dir / HANDOFF_FILENAME).exists()

    assert await service.open(library_id(database.parent), upgrade=True) is True
    copies = list(database.parent.glob("sift-before-upgrade-*.sqlite3"))
    assert len(copies) == 1
    assert the_note(settings)["data_dir"] == str(database.parent)


async def test_an_id_that_is_not_on_the_list_is_refused(service: LibrariesService) -> None:
    with pytest.raises(UnknownLibrary):
        await service.open("0123456789abcdef0123")


async def test_opening_the_running_library_asks_for_nothing(
    service: LibrariesService, settings: Settings, supervisor: Supervisor
) -> None:
    assert await service.open(library_id(settings.data_dir)) is False
    assert supervisor.asked == []


async def test_a_refused_ask_takes_its_note_back(
    service: LibrariesService, settings: Settings, prepared_db: Database, supervisor: Supervisor
) -> None:
    database = a_library(prepared_db, service.folder / "Work" / "data")
    supervisor.agrees = False
    with pytest.raises(NoSupervisor):
        await service.open(library_id(database.parent))
    assert not (settings.data_dir / HANDOFF_FILENAME).exists()


async def test_an_imported_backup_becomes_a_new_library_without_its_stamp(
    service: LibrariesService, backup: BackupService, settings: Settings, tmp_path: Path
) -> None:
    exported = tmp_path / "exported.zip"
    await backup.export_to(exported)
    await service.import_file(exported, "Restored")

    database = service.folder / "Restored" / "data" / DATABASE_FILENAME
    stamped = fetch_blocking(
        database, "SELECT COUNT(*) FROM sqlite_master WHERE name = ?", (MANIFEST_TABLE,)
    )[0][0]
    assert stamped == 0
    assert the_note(settings)["data_dir"] == str(database.parent)


async def test_an_imported_bare_database_is_copied_in(
    service: LibrariesService, settings: Settings, prepared_db: Database, tmp_path: Path
) -> None:
    source = a_library(prepared_db, tmp_path / "given")
    before = source.read_bytes()
    await service.import_file(source, "Given")
    assert (service.folder / "Given" / "data" / DATABASE_FILENAME).is_file()
    assert source.read_bytes() == before


async def test_an_imported_file_that_is_not_sift_leaves_no_folder(
    service: LibrariesService, tmp_path: Path
) -> None:
    stranger = tmp_path / "stranger.sqlite3"
    execute_blocking(stranger, "CREATE TABLE t (x)")
    with pytest.raises(NotALibrary):
        await service.import_file(stranger, "Stranger")
    assert not (service.folder / "Stranger").exists()


# --- an imported library's first line: where it came from ----------------------------------------


async def first_lines(folder: Path, name: str, backup: BackupService) -> list[tuple[object, ...]]:
    """Open the library `name` as its own start would, let it record where it came from, and read
    back what its History says: every line of the two acts that say where a library came from."""
    data_dir = folder / name / "data"
    made = Database(data_dir / DATABASE_FILENAME)
    await made.connect()
    try:
        await made.initialize_schema()
        opened = LibrariesService(
            made, Settings(data_dir=data_dir, cache_dir=folder / name / "cache"), backup
        )
        await opened.record_origin()
        assert not (data_dir / ORIGIN_FILENAME).exists()
        # A second start says nothing more: the note went with the line.
        await opened.record_origin()
        rows = await made.fetch_all(
            "SELECT d.verb, d.actor_kind, d.actor_id, s.kind, s.name FROM workbench_decisions d"
            " JOIN workbench_decision_subjects s ON s.decision_id = d.id"
            " WHERE d.verb IN ('restored', 'adopted') ORDER BY d.id"
        )
        newest = await made.fetch_one("SELECT verb FROM workbench_decisions ORDER BY id DESC")
    finally:
        await made.close()
    assert newest is not None
    assert rows == [] or newest["verb"] == rows[-1][0]
    return [tuple(row) for row in rows]


async def test_an_imported_backup_says_it_was_restored_from_that_day(
    service: LibrariesService, backup: BackupService, tmp_path: Path
) -> None:
    """Import on the Database Switcher makes a new library of a backup, and its History says so
    first, by the backup's day, as the backup task's act: whoever pressed Import may have no row
    in the library it made."""
    exported = tmp_path / "exported.zip"
    await backup.export_to(exported)
    await service.import_file(exported, "Restored", chosen="exported.zip")

    assert await first_lines(service.folder, "Restored", backup) == [
        ("restored", "sift", "backup", "backup", "the backup from 14 November 2023")
    ]


async def test_an_imported_database_file_says_it_was_created_from_that_file(
    service: LibrariesService, backup: BackupService, prepared_db: Database, tmp_path: Path
) -> None:
    """A Sift database file is named by the name it was chosen under, never by the staged copy's,
    and only by the last part of whatever path the browser sent."""
    source = a_library(prepared_db, tmp_path / "given")
    await service.import_file(source, "Given", chosen="C:\\fakepath\\old-library.sqlite3")

    assert await first_lines(service.folder, "Given", backup) == [
        ("adopted", "sift", "backup", "database_file", "the database file old-library.sqlite3")
    ]


async def test_a_database_file_with_no_name_is_still_said(
    service: LibrariesService, backup: BackupService, prepared_db: Database, tmp_path: Path
) -> None:
    source = a_library(prepared_db, tmp_path / "given")
    await service.import_file(source, "Unnamed")

    assert await first_lines(service.folder, "Unnamed", backup) == [
        ("adopted", "sift", "backup", "database_file", "a database file")
    ]


@pytest.mark.parametrize(
    "written",
    [
        '{"from": "elsewhere"}',
        "not json at all",
        '["backup"]',
        '{"from": "backup", "created_at": "yesterday", "app_version": "1", "carried": []}',
        '{"from": "backup", "created_at": 1, "app_version": "1", "carried": [7]}',
        '{"from": "database_file", "name": 7}',
    ],
)
async def test_an_origin_note_that_cannot_be_read_is_removed_and_says_nothing(
    service: LibrariesService, settings: Settings, prepared_db: Database, written: str
) -> None:
    """Each way a note can fail to be one: no JSON, no object, a backup's fields of the wrong kind,
    a file's name that is not text. A line written from any of them would say something untrue."""
    (settings.data_dir / ORIGIN_FILENAME).write_text(written, encoding="utf-8")
    await service.record_origin()
    assert not (settings.data_dir / ORIGIN_FILENAME).exists()
    assert await prepared_db.fetch_all("SELECT id FROM workbench_decisions") == []


@pytest.mark.parametrize(
    ("chosen", "said"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("old.sqlite3", "old.sqlite3"),
        ("folder/inner/old.sqlite3", "old.sqlite3"),
        ("C:\\fakepath\\old.sqlite3", "old.sqlite3"),
        ("old\x00\x1b.sqlite3", "old.sqlite3"),
        ("a" * 400, "a" * 255),
    ],
)
def test_a_chosen_file_is_said_by_its_last_name(chosen: str | None, said: str | None) -> None:
    assert file_name_said(chosen) == said


async def test_a_note_nothing_acted_on_is_removed_at_start(
    service: LibrariesService, settings: Settings
) -> None:
    (settings.data_dir / HANDOFF_FILENAME).write_text("{}", encoding="utf-8")
    await service.forget_stale_note()
    assert not (settings.data_dir / HANDOFF_FILENAME).exists()


# --- the list, read from a folder in every state it can be found in -----------------------------


async def test_a_library_kept_elsewhere_is_listed_from_the_folders_note(
    service: LibrariesService, tmp_path: Path
) -> None:
    """The way BACK: a library outside the folder is known only from the note written as it was
    left, and is listed from it, once, and not in the folder."""
    away = tmp_path / "away" / "data"
    service.folder.mkdir(parents=True)
    (service.folder / REGISTRY_FILENAME).write_text(
        json.dumps({"format": 1, "elsewhere": [{"data_dir": str(away), "cache_dir": "c"}]}),
        encoding="utf-8",
    )

    listed = await service.listed()

    assert [(one["data_dir"], one["in_folder"]) for one in listed[1:]] == [(str(away), False)]


@pytest.mark.parametrize("written", [{"format": 1, "elsewhere": 5}, [1, 2], {"format": 1}])
async def test_a_note_that_does_not_hold_a_list_lists_nothing_from_it(
    service: LibrariesService, written: object
) -> None:
    service.folder.mkdir(parents=True)
    (service.folder / REGISTRY_FILENAME).write_text(json.dumps(written), encoding="utf-8")

    assert len(await service.listed()) == 1


async def test_a_library_running_from_inside_the_folder_is_listed_once_and_left_off_the_note(
    prepared_db: Database, backup: BackupService, supervisor: Supervisor, tmp_path: Path
) -> None:
    """The running library IS one of the folder's members, so it is not listed a second time; and
    leaving it writes nothing on the note, which is only for libraries the folder cannot show."""
    folder = tmp_path / "libraries"
    folder.mkdir()
    (folder / REGISTRY_FILENAME).write_text(
        json.dumps({"format": 1, "elsewhere": []}), encoding="utf-8"
    )
    home = folder / "Home"
    a_library(prepared_db, home / "data")
    await prepared_db.execute(
        "INSERT INTO users (id, username, password_hash, role, created_at) "
        "VALUES (?, 'ada', 'hash-of-a-password', 'admin', 1)",
        (MAKER.id,),
    )
    inside = LibrariesService(
        prepared_db,
        Settings(data_dir=home / "data", cache_dir=home / "cache"),
        backup,
        can_restart=supervisor.can,
        ask_to_restart=supervisor.ask,
    )
    assert inside.folder == folder

    assert [one["name"] for one in await inside.listed()] == ["Home"]

    await inside.create("Work", MAKER)
    registry = json.loads((folder / REGISTRY_FILENAME).read_text(encoding="utf-8"))
    assert registry["elsewhere"] == []


async def test_a_second_library_made_keeps_the_note_the_first_wrote(
    service: LibrariesService, settings: Settings
) -> None:
    await service.create("Work", MAKER)
    await service.create("Play", MAKER)

    registry = json.loads((service.folder / REGISTRY_FILENAME).read_text(encoding="utf-8"))
    assert registry["elsewhere"] == [
        {"data_dir": str(settings.data_dir), "cache_dir": str(settings.cache_dir)}
    ]


# --- opening one, and each thing that refuses it ------------------------------------------------


async def test_a_library_whose_database_has_gone_is_refused_as_not_there(
    service: LibrariesService, supervisor: Supervisor
) -> None:
    """A drive or a share that is not connected: the folder is listed, its database is not there."""
    (service.folder / "Unplugged" / "data").mkdir(parents=True)

    with pytest.raises(NotALibrary, match="isn't there any more"):
        await service.open(library_id(service.folder / "Unplugged" / "data"))
    assert supervisor.asked == []


async def test_a_folder_holding_a_file_that_is_no_database_is_refused(
    service: LibrariesService, supervisor: Supervisor
) -> None:
    """Somebody's file under the library's name: unreadable as a database, so there is nothing
    this copy of Sift can open there."""
    data_dir = service.folder / "Stranger" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / DATABASE_FILENAME).write_bytes(b"this is not a database, it is a letter" * 100)

    with pytest.raises(NotALibrary, match="no Sift library there"):
        await service.open(library_id(data_dir))
    assert supervisor.asked == []


async def test_a_library_too_old_to_bring_forward_is_refused_with_the_reason(
    service: LibrariesService, prepared_db: Database, supervisor: Supervisor
) -> None:
    """The reading says which release brings it up to date; the refusal is that sentence."""
    database = a_library(prepared_db, service.folder / "Far" / "data")
    wind_back(database, below_baseline=True)

    with pytest.raises(NotALibrary) as refused:
        await service.open(library_id(database.parent), upgrade=True)
    assert str(refused.value) == the_refusal_for(database)
    assert supervisor.asked == []


async def test_a_library_sift_cannot_write_to_is_refused_before_anything_is_asked(
    service: LibrariesService,
    prepared_db: Database,
    supervisor: Supervisor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sift.slices.backup import libraries

    database = a_library(prepared_db, service.folder / "ReadOnly" / "data")
    monkeypatch.setattr(libraries, "is_writable", lambda _path: False)

    with pytest.raises(NotALibrary, match="can't write"):
        await service.open(library_id(database.parent))
    assert supervisor.asked == []


async def test_an_older_library_whose_backup_copy_cannot_be_made_is_not_opened(
    service: LibrariesService,
    settings: Settings,
    prepared_db: Database,
    supervisor: Supervisor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The promise on the screen is a way back; a one-way upgrade with nothing behind it is the
    one thing this must never do quietly."""
    from sift.slices.backup import libraries

    database = a_library(prepared_db, service.folder / "Earlier" / "data")
    wind_back(database)

    def no_room(_database: Path) -> Path:
        raise OSError("no space left on the drive")

    monkeypatch.setattr(libraries, "copy_database_aside", no_room)

    with pytest.raises(NotALibrary, match="couldn't make a backup copy"):
        await service.open(library_id(database.parent), upgrade=True)
    assert not (settings.data_dir / HANDOFF_FILENAME).exists()
    assert supervisor.asked == []


# --- making one, and what a failure leaves --------------------------------------------------------


async def test_only_an_admin_of_this_library_can_make_another(service: LibrariesService) -> None:
    with pytest.raises(NotALibrary, match="Only an admin"):
        await service.create("Work", Viewer(id="nobody-here", role=Role.ADMIN))
    assert not (service.folder / "Work").exists()


async def test_a_library_that_fails_part_way_through_being_made_leaves_no_folder(
    service: LibrariesService,
    settings: Settings,
    supervisor: Supervisor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Half a library is worse than none: it lists, and it cannot be opened."""
    from sift.slices.backup import libraries

    def fails(_database: Path, _row: tuple[object, ...]) -> None:
        raise OSError("the drive went away")

    monkeypatch.setattr(libraries, "_write_maker", fails)

    with pytest.raises(OSError, match="went away"):
        await service.create("Work", MAKER)
    assert not (service.folder / "Work").exists()
    assert supervisor.asked == []


async def test_a_seed_fills_the_new_library_before_it_is_opened(
    service: LibrariesService, settings: Settings, supervisor: Supervisor
) -> None:
    """A caller that makes a library with something in it (a Stash import) has it written into
    the new database before the switch is asked for, so the first start finds it there."""
    seen: list[tuple[Path, Path, bool]] = []

    async def seed(database: Path, data_dir: Path) -> None:
        seen.append((database, data_dir, (settings.data_dir / HANDOFF_FILENAME).exists()))
        execute_blocking(database, "CREATE TABLE seeded (x)")

    made = await service.create("Work", MAKER, seed=seed)

    database = made.data_dir / DATABASE_FILENAME
    assert seen == [(database, made.data_dir, False)]
    assert fetch_blocking(database, _HAS_SEEDED) == [("seeded",)]
    assert supervisor.asked == [True]


_HAS_SEEDED = "SELECT name FROM sqlite_master WHERE name = 'seeded'"


async def test_an_imported_database_from_a_newer_sift_is_refused_and_leaves_no_folder(
    service: LibrariesService, prepared_db: Database, tmp_path: Path
) -> None:
    source = a_library(prepared_db, tmp_path / "given")
    shift_a_component(source, +1)

    with pytest.raises(NotALibrary, match="Update Sift, then open it again"):
        await service.import_file(source, "Later")
    assert not (service.folder / "Later").exists()


async def test_an_imported_database_too_old_to_bring_forward_says_so_and_leaves_no_folder(
    service: LibrariesService, prepared_db: Database, tmp_path: Path
) -> None:
    """It IS a Sift library, so "not a Sift library" would send somebody the wrong way."""
    source = a_library(prepared_db, tmp_path / "given")
    wind_back(source, below_baseline=True)

    with pytest.raises(NotALibrary) as refused:
        await service.import_file(source, "Far")
    assert str(refused.value) == the_refusal_for(source)
    assert not (service.folder / "Far").exists()
    # And no libraries folder or mark either: a refused upload made nothing.
    assert not (service.folder / REGISTRY_FILENAME).exists()


async def test_a_name_that_answers_to_a_folder_the_listing_does_not_show_is_refused(
    service: LibrariesService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows answers to a folder's short 8.3 alias as well as to its name, and a listing shows
    only the name, so the name is also asked of the filesystem itself before a folder is made."""
    (service.folder / "Work").mkdir(parents=True)

    async def shows_nothing(_folder: Path) -> list[Path]:
        return []

    monkeypatch.setattr(LibrariesService, "_listing", staticmethod(shows_nothing))

    with pytest.raises(NameTaken, match="already a folder"):
        await service.create("Work", MAKER)


async def test_an_imported_backup_arrives_with_the_work_it_had_waiting_canceled(
    service: LibrariesService, backup: BackupService, prepared_db: Database, tmp_path: Path
) -> None:
    """The backup was taken with work queued about files the original still looks after; the new
    library must not pick it up and do it a second time."""
    from sift.kernel.jobs import JobQueue
    from sift.slices.backup.libraries import COPIED_NOTE

    waiting = await JobQueue(prepared_db).enqueue("a_job_nobody_runs", require_handler=False)
    exported = tmp_path / "exported.zip"
    await backup.export_to(exported)
    await service.import_file(exported, "Restored")

    database = service.folder / "Restored" / "data" / DATABASE_FILENAME
    assert fetch_blocking(database, "SELECT state, note FROM jobs WHERE id = ?", (waiting,)) == [
        ("canceled", COPIED_NOTE)
    ]


async def test_a_database_the_desktop_app_adopts_is_settled_like_every_other_copy(
    service: LibrariesService, prepared_db: Database, tmp_path: Path
) -> None:
    """The third door (a database file chosen in the app's own picker) goes through the same
    `make_its_own_library` as an import and a duplicate."""
    from sift.kernel.jobs import JobQueue
    from sift.main import ADOPT_LIBRARY, answer_about_a_library
    from sift.slices.backup.libraries import COPIED_NOTE

    waiting = await JobQueue(prepared_db).enqueue("a_job_nobody_runs", require_handler=False)
    await prepared_db.execute(
        "INSERT INTO sessions (id, user_id, token_hash, created_at, last_seen_at, expires_at)"
        " VALUES ('s', ?, 'h', 1, 1, 2)",
        (MAKER.id,),
    )
    source = a_library(prepared_db, tmp_path / "given")
    said: list[str] = []
    assert (
        answer_about_a_library([ADOPT_LIBRARY, str(source), str(tmp_path / "made")], said.append)
        == 0
    )
    database = tmp_path / "made" / DATABASE_FILENAME
    assert json.loads(said[-1])["ok"] is True
    assert fetch_blocking(database, "SELECT COUNT(*) FROM sessions") == [(0,)]
    assert fetch_blocking(database, "SELECT state, note FROM jobs WHERE id = ?", (waiting,)) == [
        ("canceled", COPIED_NOTE)
    ]


async def test_a_backup_too_old_to_bring_forward_is_refused_before_anything_is_unpacked(
    service: LibrariesService, backup: BackupService, tmp_path: Path
) -> None:
    """The archive's own database is read with the same baseline rule a library folder is, so a
    restore cannot swap in a file the next start refuses, and an import cannot make one."""
    import zipfile

    from sift.slices.backup.service import DATABASE_MEMBER, BackupTooOld

    exported = tmp_path / "exported.zip"
    await backup.export_to(exported)
    inner = tmp_path / "inner.sqlite3"
    with zipfile.ZipFile(exported) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    inner.write_bytes(members[DATABASE_MEMBER])
    wind_back(inner, below_baseline=True)
    members[DATABASE_MEMBER] = inner.read_bytes()
    aged = tmp_path / "aged.zip"
    with zipfile.ZipFile(aged, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)

    with pytest.raises(BackupTooOld) as refused:
        await backup.inspect(aged)
    assert str(refused.value) == the_refusal_for(inner)
    with pytest.raises(BackupTooOld):
        await service.import_file(aged, "Aged")
    assert not (service.folder / "Aged").exists()


def test_an_imported_database_from_before_sign_ins_is_already_signed_out(tmp_path: Path) -> None:
    """An imported file can come from a Sift old enough to have no sign-in table, and a library
    with no sign-ins is already signed out, not a failed import."""
    from sift.slices.backup.libraries import sign_everyone_out

    database = tmp_path / "old.sqlite3"
    execute_blocking(database, "CREATE TABLE t (x)")

    sign_everyone_out(database)

    assert fetch_blocking(database, "SELECT name FROM sqlite_master") == [("t",)]


def test_a_copied_database_with_no_jobs_table_has_no_work_to_cancel(tmp_path: Path) -> None:
    """A file from before the queue holds no work waiting; settling it is not a failed import."""
    from sift.slices.backup.libraries import COPIED_NOTE, make_its_own_library

    database = tmp_path / "old.sqlite3"
    execute_blocking(database, "CREATE TABLE t (x)")

    make_its_own_library(database, now=1, note=COPIED_NOTE)

    assert fetch_blocking(database, "SELECT name FROM sqlite_master") == [("t",)]


# --- what a duplicate walks ---------------------------------------------------------------------


def test_a_walk_counts_every_file_in_nested_folders_and_never_follows_a_link(
    tmp_path: Path,
) -> None:
    from sift.slices.backup.libraries import _walk
    from sift.testing.tools import ON_WINDOWS, junction

    root = tmp_path / "cache"
    (root / "a" / "b").mkdir(parents=True)
    (root / "top.jpg").write_bytes(b"12")
    (root / "a" / "b" / "deep.jpg").write_bytes(b"1234")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "not-ours.jpg").write_bytes(b"x" * 100)
    if ON_WINDOWS:
        junction(root / "pointed", elsewhere)
    else:
        (root / "pointed").symlink_to(elsewhere, target_is_directory=True)

    walked = {path.relative_to(root).as_posix(): size for path, size in _walk(root)}

    assert walked == {"top.jpg": 2, "a/b/deep.jpg": 4}
    assert list(_walk(tmp_path / "nothing-here")) == []


def test_a_folder_the_walk_cannot_read_is_passed_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder somebody locked is not Sift's to copy, and the rest of the cache still is."""
    from sift.slices.backup import libraries

    root = tmp_path / "cache"
    (root / "locked").mkdir(parents=True)
    (root / "kept.jpg").write_bytes(b"123")
    (root / "locked" / "hidden.jpg").write_bytes(b"1")
    real = os.scandir

    def refusing(path: object) -> object:
        if Path(str(path)).name == "locked":
            raise PermissionError("access is denied")
        return real(path)  # type: ignore[call-overload]

    monkeypatch.setattr(os, "scandir", refusing)

    assert [(path.name, size) for path, size in libraries._walk(root)] == [("kept.jpg", 3)]


# --- deleting one, taking one off the list, and which opens at start ----------------------------


class Bin:
    """What was sent to the Recycle Bin, taken out of the way as the real move would."""

    def __init__(self) -> None:
        self.took: list[Path] = []

    def __call__(self, folder: Path) -> None:
        self.took.append(folder)
        shutil.rmtree(folder)


def _member(service: LibrariesService, prepared_db: Database, name: str) -> str:
    a_library(prepared_db, service.folder / name / "data")
    return library_id(service.folder / name / "data")


async def test_a_library_in_the_folder_goes_to_the_bin_by_its_typed_name(
    service: LibrariesService, prepared_db: Database
) -> None:
    other = _member(service, prepared_db, "Holiday")
    recycle = Bin()

    await service.delete(other, "Holiday", recycle=recycle)

    assert recycle.took == [service.folder / "Holiday"]
    assert [one["id"] for one in await service.listed()][1:] == []


async def test_a_name_typed_wrong_moves_nothing(
    service: LibrariesService, prepared_db: Database
) -> None:
    other = _member(service, prepared_db, "Holiday")
    recycle = Bin()

    with pytest.raises(NotDeletable, match="Type Holiday exactly"):
        await service.delete(other, "holiday", recycle=recycle)

    assert recycle.took == []
    assert (service.folder / "Holiday" / "data").is_dir()


async def test_the_open_library_and_one_kept_elsewhere_are_never_deleted(
    service: LibrariesService, prepared_db: Database, tmp_path: Path
) -> None:
    listed = await service.listed()
    away = tmp_path / "away" / "data"
    service.folder.mkdir(parents=True, exist_ok=True)
    (service.folder / REGISTRY_FILENAME).write_text(
        json.dumps({"format": 1, "elsewhere": [{"data_dir": str(away), "cache_dir": "c"}]}),
        encoding="utf-8",
    )
    recycle = Bin()

    with pytest.raises(NotDeletable, match="has open"):
        await service.delete(listed[0]["id"], listed[0]["name"], recycle=recycle)
    with pytest.raises(NotDeletable, match="Remove it from this list"):
        await service.delete(library_id(away), "away", recycle=recycle)
    assert recycle.took == []


async def test_a_machine_without_a_bin_deletes_nothing_and_says_so(
    service: LibrariesService, prepared_db: Database
) -> None:
    other = _member(service, prepared_db, "Holiday")

    def no_bin(_folder: Path) -> None:
        raise NoRecycleBin("no bin here")

    with pytest.raises(NotDeletable, match="no bin here"):
        await service.delete(other, "Holiday", recycle=no_bin)
    assert (service.folder / "Holiday" / "data").is_dir()


async def test_the_library_chosen_to_open_at_start_is_marked_and_kept_through_a_switch(
    service: LibrariesService, prepared_db: Database
) -> None:
    other = _member(service, prepared_db, "Holiday")

    await service.choose_opening(other)

    marked = {one["id"]: one["opens_at_start"] for one in await service.listed()}
    assert marked[other] is True
    assert sum(marked.values()) == 1
    written = json.loads((service.folder / REGISTRY_FILENAME).read_text(encoding="utf-8"))
    assert written[OPENS_AT_START]["data_dir"] == str(service.folder / "Holiday" / "data")
    # Leaving for another library rewrites the mark with the list of libraries kept elsewhere;
    # the choice of which opens at start is not the switch's to change.
    service._remember_current()
    assert (
        json.loads((service.folder / REGISTRY_FILENAME).read_text(encoding="utf-8"))[OPENS_AT_START]
        == written[OPENS_AT_START]
    )


async def test_deleting_the_library_that_opens_at_start_leaves_whichever_opened_last(
    service: LibrariesService, prepared_db: Database
) -> None:
    other = _member(service, prepared_db, "Holiday")
    await service.choose_opening(other)

    await service.delete(other, "Holiday", recycle=Bin())

    written = json.loads((service.folder / REGISTRY_FILENAME).read_text(encoding="utf-8"))
    assert OPENS_AT_START not in written


async def test_a_library_kept_elsewhere_is_taken_off_the_list_and_left_alone(
    service: LibrariesService, tmp_path: Path
) -> None:
    away = tmp_path / "away" / "data"
    away.mkdir(parents=True)
    service.folder.mkdir(parents=True)
    (service.folder / REGISTRY_FILENAME).write_text(
        json.dumps({"format": 1, "elsewhere": [{"data_dir": str(away), "cache_dir": "c"}]}),
        encoding="utf-8",
    )

    await service.forget(library_id(away))

    assert len(await service.listed()) == 1
    assert away.is_dir()


def _kept_elsewhere(service: LibrariesService, tmp_path: Path) -> str:
    """A library outside the folder, on the folder's note, by the id the list gives it."""
    away = tmp_path / "away" / "data"
    away.mkdir(parents=True)
    service.folder.mkdir(parents=True, exist_ok=True)
    (service.folder / REGISTRY_FILENAME).write_text(
        json.dumps({"format": 1, "elsewhere": [{"data_dir": str(away), "cache_dir": "c"}]}),
        encoding="utf-8",
    )
    return library_id(away)


async def test_forgetting_the_library_that_opens_at_start_leaves_whichever_opened_last(
    service: LibrariesService, tmp_path: Path
) -> None:
    """A library off the list cannot be the one Sift opens at start: the choice goes with it."""
    away = _kept_elsewhere(service, tmp_path)
    await service.choose_opening(away)

    await service.forget(away)

    written = json.loads((service.folder / REGISTRY_FILENAME).read_text(encoding="utf-8"))
    assert written == {"format": 1, "elsewhere": []}


async def test_forgetting_another_library_keeps_the_choice_of_which_opens_at_start(
    service: LibrariesService, prepared_db: Database, tmp_path: Path
) -> None:
    away = _kept_elsewhere(service, tmp_path)
    other = _member(service, prepared_db, "Holiday")
    await service.choose_opening(other)

    await service.forget(away)

    marked = {one["id"]: one["opens_at_start"] for one in await service.listed()}
    assert away not in marked
    assert marked[other] is True


async def test_only_a_library_kept_elsewhere_can_be_taken_off_the_list(
    service: LibrariesService, prepared_db: Database
) -> None:
    """The open library is the one running, and one in the folder is deleted, never forgotten."""
    running = (await service.listed())[0]["id"]
    other = _member(service, prepared_db, "Holiday")

    for library in (running, other):
        with pytest.raises(NotDeletable, match="folder of its own"):
            await service.forget(library)

    assert {one["id"] for one in await service.listed()} == {running, other}


async def test_an_id_on_no_list_is_refused_by_each_door_and_changes_nothing(
    service: LibrariesService,
) -> None:
    """The page sends ids it read from the list; one that names nothing now (a list read before
    a delete elsewhere) is refused rather than taken as some other library."""
    gone = "0123456789abcdef0123"
    recycle = Bin()

    with pytest.raises(UnknownLibrary, match="Read the list again"):
        await service.choose_opening(gone)
    with pytest.raises(UnknownLibrary, match="Read the list again"):
        await service.delete(gone, "Holiday", recycle=recycle)
    with pytest.raises(UnknownLibrary, match="Read the list again"):
        await service.forget(gone)

    assert recycle.took == []
    assert not (service.folder / REGISTRY_FILENAME).exists()
