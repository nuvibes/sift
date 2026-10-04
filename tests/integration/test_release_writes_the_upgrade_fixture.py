# SPDX-License-Identifier: AGPL-3.0-or-later
"""A release writes the library the next release's upgrade test starts from, and keeps only that one.

The upgrade test (`test_a_library_from_the_last_release_comes_forward.py`) brings a library in the
shape the last release made forward in one boot. That library is a dump of a new one made by the
released code, and a dump written by hand once is a fixture that stops being the last release the
day the next one ships. So the release writes it (`write_upgrade_fixture`): the dump with its SQL
comments taken out, gzipped, under its own version, and every older one deleted.

What is proved here: the comments go and nothing else does, a string holding either comment mark
is left as it is, only this version's fixture stays, the test picks the newest by version and not
by spelling, and the fixture in the tree is in the shape the release writes. The last case makes a
library with this checkout's own interpreter, the way the release makes one with the runtime.
"""

from __future__ import annotations

import gzip
import importlib.util
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.integration

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release.py"
_DATA = Path(__file__).parent / "data"


def _load() -> ModuleType:
    """The script, imported by path: it is an operator tool rather than part of the package."""
    spec = importlib.util.spec_from_file_location("sift_release_fixture_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load()


def test_the_comments_go_and_nothing_else_does() -> None:
    dumped = (
        "CREATE TABLE t (\n"
        "  a TEXT, --what a is\n"
        "  b TEXT /* and b */ NOT NULL\n"
        ");\n"
        "INSERT INTO t VALUES('keep--this', 'and /* this */');\n"
        'CREATE TRIGGER "x--y" AFTER INSERT ON t BEGIN\n'
        "  --a whole line\n"
        "  SELECT 1;\n"
        "END;\n"
    )
    assert release.without_sql_comments(dumped) == (
        "CREATE TABLE t (\n"
        "  a TEXT,\n"
        "  b TEXT  NOT NULL\n"
        ");\n"
        "INSERT INTO t VALUES('keep--this', 'and /* this */');\n"
        'CREATE TRIGGER "x--y" AFTER INSERT ON t BEGIN\n'
        "\n"
        "  SELECT 1;\n"
        "END;\n"
    )


def test_only_this_release_s_fixture_stays(tmp_path: Path) -> None:
    for name in (
        "library-0.1.9.sql.gz",
        "library-0.1.10.sql.gz",
        "library-0.1.11.sql.gz",
        "notes.txt",
    ):
        (tmp_path / name).write_bytes(b"x")
    gone = release.keep_one_fixture(tmp_path, "0.1.11")
    assert sorted(one.name for one in gone) == ["library-0.1.10.sql.gz", "library-0.1.9.sql.gz"]
    assert sorted(one.name for one in tmp_path.iterdir()) == ["library-0.1.11.sql.gz", "notes.txt"]


def test_the_upgrade_test_reads_the_newest_by_version_not_by_spelling(tmp_path: Path) -> None:
    from tests.integration.test_a_library_from_the_last_release_comes_forward import (
        newest_release,
    )

    for name in ("library-0.1.9.sql.gz", "library-0.1.10.sql.gz"):
        (tmp_path / name).write_bytes(b"x")
    assert newest_release(tmp_path).name == "library-0.1.10.sql.gz"


def test_the_fixture_in_the_tree_is_the_one_the_release_writes() -> None:
    """One file, named for a version, a whole dump with no comment left to take out."""
    [fixture] = sorted(_DATA.glob("library-*.sql.gz"))
    assert release.FIXTURE.match(fixture.name)
    sql = gzip.decompress(fixture.read_bytes()).decode("utf-8")
    assert sql.startswith("BEGIN TRANSACTION;\n") and sql.rstrip().endswith("COMMIT;")
    assert release.without_sql_comments(sql) == sql


def test_a_release_makes_the_library_its_own_code_creates(tmp_path: Path) -> None:
    from sift.kernel.access.schema import CATALOG_VERSION

    (tmp_path / "library-0.0.1.sql.gz").write_bytes(b"older")
    fixture = release.write_upgrade_fixture(Path(sys.executable), tmp_path, "9.9.9")

    assert [one.name for one in tmp_path.iterdir()] == ["library-9.9.9.sql.gz"]
    sql = gzip.decompress(fixture.read_bytes()).decode("utf-8")
    assert "--" not in release.without_sql_comments(sql)
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(sql)
        [(version,)] = connection.execute(
            "SELECT version FROM schema_version WHERE component = 'catalog'"
        ).fetchall()
    finally:
        connection.close()
    assert version == CATALOG_VERSION
