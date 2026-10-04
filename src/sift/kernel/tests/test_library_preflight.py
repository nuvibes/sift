# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a library folder without opening it, and the flag that asks from outside.

The desktop shell's library switcher rests entirely on this. It has to know whether a folder holds
a database this build can read BEFORE it stops the backend that is running, and it cannot find
out by opening the folder, because opening it is the migration it is trying to warn somebody about
first.

Every test here builds a real database file. What could go wrong (a read-only connection that
quietly creates a write-ahead log, a verdict that disagrees with the migration runner, a snapshot
taken as a file copy while rows sit in a side file) does not reproduce against a double.
"""

from __future__ import annotations

import json
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel (an old-schema file is built by hand here, on purpose)
import sys
from pathlib import Path
from typing import Any

import pytest

import sift.main as main_module
from sift.kernel.db import (
    DATABASE_FILENAME,
    VERDICT_CURRENT,
    VERDICT_EMPTY,
    VERDICT_NEWER,
    VERDICT_OLDER,
    VERDICT_UNREADABLE,
    adopt_database,
    copy_database_aside,
    copy_library_aside,
    inspect_database,
    inspect_library,
    library_database,
    registered_components,
)
from sift.main import (
    ADOPT_LIBRARY,
    BACK_UP_LIBRARY,
    INSPECT_LIBRARY,
    answer_about_a_library,
    main,
)
from sift.testing.schema_versions import wind_one_back


def _library(folder: Path, versions: dict[str, int]) -> Path:
    """A database file recording exactly these component versions, and nothing else."""
    folder.mkdir(parents=True, exist_ok=True)
    database = folder / DATABASE_FILENAME
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE schema_version (component TEXT PRIMARY KEY, version INTEGER NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_version (component, version) VALUES (?, ?)",
            sorted(versions.items()),
        )
        connection.commit()
    finally:
        connection.close()
    return database


def _current() -> dict[str, int]:
    return {name: one.version for name, one in registered_components().items()}


def test_a_folder_with_no_database_is_empty_rather_than_a_refusal(tmp_path: Path) -> None:
    # What a NEW library looks like. Reading it as a fault would mean the switcher could never be
    # pointed at a folder somebody had just made, which is half of what it is for.
    report = inspect_library(tmp_path)
    assert report.verdict == VERDICT_EMPTY
    assert report.on_disk == {}
    assert report.database == tmp_path / DATABASE_FILENAME


def test_a_library_at_this_builds_versions_is_current(tmp_path: Path) -> None:
    _library(tmp_path, _current())
    assert inspect_library(tmp_path).verdict == VERDICT_CURRENT


def test_one_component_behind_is_older(tmp_path: Path) -> None:
    versions = _current()
    behind = wind_one_back(versions)
    _library(tmp_path, versions)
    report = inspect_library(tmp_path)
    assert report.verdict == VERDICT_OLDER
    assert report.on_disk[behind] == versions[behind]
    assert report.expected[behind] == _current()[behind]


def test_one_component_ahead_is_newer_even_while_every_other_is_behind(tmp_path: Path) -> None:
    # The two verdicts are not symmetrical and this is why the order of the checks matters: a
    # library with one component ahead cannot be opened at all, whatever the rest of it says. A
    # check that reported "older" here would send the shell into an upgrade that destroys it.
    versions = {name: 1 for name in _current()}
    versions["catalog"] = _current()["catalog"] + 1
    _library(tmp_path, versions)
    assert inspect_library(tmp_path).verdict == VERDICT_NEWER


def test_a_component_this_build_has_never_heard_of_is_newer(tmp_path: Path) -> None:
    # The other shape of the same problem, and the one a version number cannot show: a feature that
    # did not exist when this build was made. `_refuse_if_newer` in the backup slice reads a
    # restored file the same way, and these two have to agree or a library would be openable
    # through one door and refused at the other.
    versions = _current()
    versions["a-feature-from-later"] = 1
    _library(tmp_path, versions)
    assert inspect_library(tmp_path).verdict == VERDICT_NEWER


def test_a_database_that_never_reached_its_first_migration_is_empty(tmp_path: Path) -> None:
    # No `schema_version` table at all. The ordinary boot path creates it and carries on, so this
    # is not a refusal: it is an interrupted first start.
    tmp_path.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(tmp_path / DATABASE_FILENAME)
    connection.close()
    assert inspect_library(tmp_path).verdict == VERDICT_EMPTY


def test_a_version_table_with_no_rows_is_empty(tmp_path: Path) -> None:
    """The table was made and the first migration never wrote its row: an interrupted first start,
    the same as no table at all."""
    _library(tmp_path, {})
    assert inspect_library(tmp_path).verdict == VERDICT_EMPTY


def test_a_database_that_cannot_be_opened_is_unreadable_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _library(tmp_path, _current())

    def refuse(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(sqlite3, "connect", refuse)
    report = inspect_library(tmp_path)
    assert report.verdict == VERDICT_UNREADABLE
    assert report.error == "unable to open database file"
    # SQLite's words are for a log: every door shows `detail` to a person, and has its own line
    # for a file that is no library at all.
    assert report.detail == ""


def test_a_file_that_is_not_a_database_is_unreadable_and_says_why(tmp_path: Path) -> None:
    (tmp_path / DATABASE_FILENAME).write_bytes(b"this is somebody's holiday photo")
    report = inspect_library(tmp_path)
    assert report.verdict == VERDICT_UNREADABLE
    assert report.error != ""
    assert report.detail == ""


def test_reading_a_library_leaves_no_write_ahead_log_behind(tmp_path: Path) -> None:
    # THE PROPERTY THE WHOLE PREFLIGHT RESTS ON. An ordinary connection to a WAL database creates
    # `-wal` and `-shm` beside it, and the shell would then have written into a library it had only
    # been asked to look at. `mode=ro` is what makes "changing nothing" true of the connection
    # rather than a promise about the statements.
    _library(tmp_path, _current())
    inspect_library(tmp_path)
    assert not (tmp_path / f"{DATABASE_FILENAME}-wal").exists()
    assert not (tmp_path / f"{DATABASE_FILENAME}-shm").exists()


def test_reading_a_library_opens_every_connection_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The test above sees the side files only while a connection is open: an ordinary one closed
    # last folds them back and removes them, so the files alone cannot tell the two apart. The
    # connection's own mode can.
    _library(tmp_path, _current())
    opened: list[tuple[str, bool]] = []
    connect = sqlite3.connect

    def watched(target: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        opened.append((str(target), bool(kwargs.get("uri"))))
        made: sqlite3.Connection = connect(target, *args, **kwargs)
        return made

    monkeypatch.setattr(sqlite3, "connect", watched)
    inspect_library(tmp_path)
    assert opened
    assert all(uri and target.endswith("?mode=ro") for target, uri in opened), opened


def test_the_snapshot_carries_rows_that_are_still_in_the_side_file(tmp_path: Path) -> None:
    # WHY `VACUUM INTO` AND NOT A FILE COPY, proved rather than asserted. In WAL mode the newest
    # committed rows sit in `-wal` while the main file is behind; a copy of the `.sqlite3` alone
    # hands back a snapshot missing them, which is the worst possible shape for a safety copy.
    _library(tmp_path, _current())
    live = sqlite3.connect(tmp_path / DATABASE_FILENAME)
    try:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("CREATE TABLE kept (word TEXT)")
        live.execute("INSERT INTO kept (word) VALUES ('sanderling')")
        live.commit()
        snapshot = copy_library_aside(tmp_path)
    finally:
        live.close()

    copy = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    try:
        assert copy.execute("SELECT word FROM kept").fetchall() == [("sanderling",)]
    finally:
        copy.close()


def test_a_second_snapshot_never_lands_on_the_first(tmp_path: Path) -> None:
    # Two in the same second. `VACUUM INTO` refuses a target that exists, and the refusal would
    # reach the shell as the copy having failed rather than as a name collision.
    _library(tmp_path, _current())
    first = copy_library_aside(tmp_path, now=1_700_000_000.0)
    second = copy_library_aside(tmp_path, now=1_700_000_000.0)
    assert first != second
    assert first.exists() and second.exists()


def test_the_snapshot_is_named_so_restore_will_take_it(tmp_path: Path) -> None:
    # A safety copy nothing can put back is not a safety copy. Restore accepts a bare `.sqlite3`
    # from before the archive format, so the name has to end that way.
    _library(tmp_path, _current())
    assert copy_library_aside(tmp_path).name.endswith(".sqlite3")


class Said:
    """What the entry point printed, in order."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)

    def answer(self) -> dict[str, object]:
        assert len(self.lines) == 1
        parsed = json.loads(self.lines[0])
        assert isinstance(parsed, dict)
        return parsed


def test_the_flag_answers_one_json_object_and_exits_nought(tmp_path: Path) -> None:
    versions = _current()
    wind_one_back(versions)
    _library(tmp_path, versions)
    said = Said()
    assert answer_about_a_library([INSPECT_LIBRARY, str(tmp_path)], said) == 0
    answer = said.answer()
    assert answer["verdict"] == VERDICT_OLDER
    # Exit 0 even for a library that needs an upgrade: the verdict is the answer, and an exit code
    # would be a second, coarser copy of it for the shell to keep in step with.


def test_no_flag_at_all_means_start_the_server(tmp_path: Path) -> None:
    # None rather than an exit code, because `main` reads it as "not asked" and goes on to boot.
    assert answer_about_a_library([], Said()) is None
    assert answer_about_a_library(["--something-else"], Said()) is None


def test_a_flag_with_no_folder_is_the_one_thing_that_exits_non_zero(tmp_path: Path) -> None:
    said = Said()
    assert answer_about_a_library([INSPECT_LIBRARY], said) == 2
    assert "error" in said.answer()


def test_the_back_up_flag_answers_where_the_copy_went(tmp_path: Path) -> None:
    _library(tmp_path, _current())
    said = Said()
    assert answer_about_a_library([BACK_UP_LIBRARY, str(tmp_path)], said) == 0
    answer = said.answer()
    assert answer["ok"] is True
    assert Path(str(answer["copy"])).exists()


def test_a_copy_that_cannot_be_made_is_reported_rather_than_raised(tmp_path: Path) -> None:
    # The caller is a shell reading stdout. A traceback on stderr reaches it as an empty answer
    # with no reason in it, which is indistinguishable from the process never having run.
    said = Said()
    assert answer_about_a_library([BACK_UP_LIBRARY, str(tmp_path / "nowhere")], said) == 0
    answer = said.answer()
    assert answer["ok"] is False
    assert answer["detail"] != ""


@pytest.mark.parametrize("flag", [INSPECT_LIBRARY, BACK_UP_LIBRARY, ADOPT_LIBRARY])
def test_an_empty_path_is_refused_rather_than_read_as_the_working_directory(flag: str) -> None:
    said = Said()
    assert answer_about_a_library([flag, ""], said) == 2
    assert "error" in said.answer()


# --- the file form: a database somebody chose, rather than a library folder -----------------------


def _loose_file(folder: Path, name: str, versions: dict[str, int]) -> Path:
    """A database under a name that is not a library's own: a backup copy, a file handed over."""
    made = _library(folder / "made", versions)
    loose = folder / name
    made.rename(loose)
    return loose


def test_a_path_ending_in_sqlite3_names_that_file_and_anything_else_names_a_folder(
    tmp_path: Path,
) -> None:
    # Decided by the NAME: the folder form is asked about folders that do not exist yet (a new
    # library), and a reading that looked at the disk would turn "not there" into a file.
    assert library_database(tmp_path / "copy.sqlite3") == tmp_path / "copy.sqlite3"
    assert library_database(tmp_path / "copy.SQLITE3") == tmp_path / "copy.SQLITE3"
    assert library_database(tmp_path / "data") == tmp_path / "data" / DATABASE_FILENAME
    assert library_database(tmp_path / "new") == tmp_path / "new" / DATABASE_FILENAME
    oddly_named = tmp_path / "archive.sqlite3"
    oddly_named.mkdir()
    assert library_database(oddly_named) == oddly_named / DATABASE_FILENAME


def test_a_chosen_file_is_read_by_the_same_verdicts_as_a_folder(tmp_path: Path) -> None:
    versions = _current()
    wind_one_back(versions)
    loose = _loose_file(tmp_path, "sift-before-upgrade-20260901-120000.sqlite3", versions)
    report = inspect_database(loose)
    assert report.verdict == VERDICT_OLDER
    assert report.database == loose


def test_a_name_with_a_hash_in_it_is_read_as_that_file(tmp_path: Path) -> None:
    # Pasted raw into a `file:` URI, `#` ends the path and SQLite opens `backup ` instead: a
    # different file, which read-only and absent is "unable to open", or worse, somebody else's.
    loose = _loose_file(tmp_path, "backup #2.sqlite3", _current())
    assert inspect_database(loose).verdict == VERDICT_CURRENT


def test_the_inspect_flag_takes_a_file_as_well_as_a_folder(tmp_path: Path) -> None:
    loose = _loose_file(tmp_path, "given.sqlite3", _current())
    said = Said()
    assert answer_about_a_library([INSPECT_LIBRARY, str(loose)], said) == 0
    answer = said.answer()
    assert answer["verdict"] == VERDICT_CURRENT
    assert answer["database"] == str(loose)


def test_a_file_snapshot_lands_beside_the_file(tmp_path: Path) -> None:
    loose = _loose_file(tmp_path, "given.sqlite3", _current())
    snapshot = copy_database_aside(loose)
    assert snapshot.parent == tmp_path
    assert snapshot.name.startswith("sift-before-upgrade-")


def test_a_snapshot_of_a_folder_with_no_database_is_refused_rather_than_made_empty(
    tmp_path: Path,
) -> None:
    # `sqlite3.connect` CREATES a file that is not there, and the snapshot of that empty database
    # would be reported as a good safety copy. It is a refusal, and nothing is left behind.
    said = Said()
    assert answer_about_a_library([BACK_UP_LIBRARY, str(tmp_path)], said) == 0
    assert said.answer()["ok"] is False
    assert not (tmp_path / DATABASE_FILENAME).exists()


def test_adopting_copies_the_file_in_and_leaves_the_chosen_one_exactly_as_it_was(
    tmp_path: Path,
) -> None:
    loose = _loose_file(tmp_path, "given.sqlite3", _current())
    before = loose.read_bytes()
    library = tmp_path / "given" / "data"

    made = adopt_database(loose, library)

    assert made == library / DATABASE_FILENAME
    assert inspect_library(library).verdict == VERDICT_CURRENT
    assert loose.read_bytes() == before
    # Read-only: looking at somebody's file must not leave a side file beside it.
    assert sorted(one.name for one in tmp_path.iterdir()) == ["given", "given.sqlite3", "made"]


def test_adopting_carries_rows_still_in_the_side_file(tmp_path: Path) -> None:
    loose = _loose_file(tmp_path, "given.sqlite3", _current())
    live = sqlite3.connect(loose)
    try:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("CREATE TABLE kept (word TEXT)")
        live.execute("INSERT INTO kept (word) VALUES ('sanderling')")
        live.commit()
        made = adopt_database(loose, tmp_path / "given" / "data")
    finally:
        live.close()
    copy = sqlite3.connect(made)
    try:
        assert copy.execute("SELECT word FROM kept").fetchall() == [("sanderling",)]
    finally:
        copy.close()


def test_adopting_never_writes_over_a_library_that_is_already_there(tmp_path: Path) -> None:
    loose = _loose_file(tmp_path, "given.sqlite3", _current())
    existing = _library(tmp_path / "given" / "data", {"catalog": 1})
    before = existing.read_bytes()
    with pytest.raises(FileExistsError):
        adopt_database(loose, existing.parent)
    assert existing.read_bytes() == before


def test_the_adopt_flag_answers_where_the_library_database_went(tmp_path: Path) -> None:
    loose = _loose_file(tmp_path, "given.sqlite3", _current())
    library = tmp_path / "given" / "data"
    said = Said()
    assert answer_about_a_library([ADOPT_LIBRARY, str(loose), str(library)], said) == 0
    answer = said.answer()
    assert answer["ok"] is True
    assert answer["database"] == str(library / DATABASE_FILENAME)


def test_a_copy_that_could_not_be_made_its_own_library_is_taken_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy still holding another library's sign-ins and waiting work must never be opened as
    it stands: it is removed, and the refusal is said rather than raised."""
    loose = _loose_file(tmp_path, "given.sqlite3", _current())
    library = tmp_path / "given" / "data"

    def cannot_settle(made: Path, **_kwargs: object) -> None:
        assert made.exists()
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(main_module, "make_its_own_library", cannot_settle)
    said = Said()

    assert answer_about_a_library([ADOPT_LIBRARY, str(loose), str(library)], said) == 0

    assert said.answer() == {"ok": False, "detail": "database is locked"}
    assert not (library / DATABASE_FILENAME).exists()
    assert loose.exists(), "the file handed over is left as it was"


def test_the_adopt_flag_reports_a_refusal_rather_than_raising(tmp_path: Path) -> None:
    said = Said()
    missing = tmp_path / "gone.sqlite3"
    assert answer_about_a_library([ADOPT_LIBRARY, str(missing), str(tmp_path / "x")], said) == 0
    answer = said.answer()
    assert answer["ok"] is False
    assert answer["detail"] != ""


def test_the_adopt_flag_without_a_destination_is_refused(tmp_path: Path) -> None:
    said = Said()
    assert answer_about_a_library([ADOPT_LIBRARY, str(tmp_path / "given.sqlite3")], said) == 2
    assert "error" in said.answer()


def test_the_program_exits_with_the_answer_before_it_reads_any_setting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Asked a question with no path, the program says so and exits 2, before the settings are
    read, so a machine with no valid SIFT_DATA_DIR is answered too."""
    monkeypatch.setattr(sys, "argv", ["sift", INSPECT_LIBRARY])
    monkeypatch.setenv("SIFT_DATA_DIR", "")
    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == 2
    assert "error" in json.loads(capsys.readouterr().out)
