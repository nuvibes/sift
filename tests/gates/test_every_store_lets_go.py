# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every store that holds an asset id has a way of letting go of it.

A column holding an asset id is cleared when the asset ends by one of: a foreign key to `assets`
(`foreign_keys` is on for every connection); a registration in `kernel/forgetting.py`, for a store
no key can reach (a virtual table takes none); a place on `KEEPS_ITS_IDS`, for a row still true
after the file has gone; or a place on `SCRATCH`, for a table emptied inside the write that fills
it. Otherwise a removed file stays findable in an index while every total looks right.

Out of scope: `acl_grants.object_id`, polymorphic and cleared by `forget_object`; and files on a
disk, which a database gate cannot see.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import sift.main  # noqa: F401 (imported for its side effect: every store registers itself)
from sift.kernel.db import Database
from sift.kernel.forgetting import declared_tables

pytestmark = pytest.mark.gate

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src" / "sift"

#: Tables that hold an asset id on purpose and are not cleared when the asset ends: each entry says
#: why a row about a file is still TRUE after the file has gone. It stays short.
KEEPS_ITS_IDS: dict[str, str] = {
    "backlog_moved": (
        "A file marked as moved for the kept counts. A file that has ended is marked so the next "
        "read folds it out, so no key on purpose."
    ),
    "backlog_files": (
        "Which kept counts a file is in. The next read after the file ends takes its row and its "
        "counts out, which a cascade would skip."
    ),
    "plays": (
        "One sitting somebody spent with a file. It is the user's history rather than the "
        "file's, so a file being deleted is part of that history and not the end of it: there is "
        "no key on purpose, because a cascade would rewrite last year's hours watched with "
        "nothing to say so. A play carries no name, "
        "only an id, so a row about a file that has gone describes nothing: it is counted, never "
        "read. Registering a forgetting for it would put the cascade straight back, since a "
        "forgetting fires when an asset ends; see kernel/forgetting.py, which says so beside the "
        "registry."
    ),
    "file_departures": (
        "A file that left the library, written by a trigger as its row goes: when it arrived, its "
        "size, kind and length, and the Sites it was under. It exists BECAUSE the file is gone, so "
        "a day's arrivals and removals are still counted after the files of that day have been "
        "deleted (insights/schema.py). It carries no name and is counted, never read back."
    ),
    "file_departure_viewers": (
        "Who could see a departed file when it went, and whether it was hidden from them: the "
        "verdict a departed file no longer has, so its arrival and its removal are counted only "
        "for the people who could see it. Goes with its departure and with its User."
    ),
    "search_opens": (
        "A file somebody opened from the wall a search narrowed. Their history rather than the "
        "file's, for the reason a sitting in `plays` is: what was opened stays true after the file "
        "has gone. It carries no name; it goes with its search's record, with Forget, with a "
        "cleared history and with its User."
    ),
    "file_moves": (
        "A rename that happened is still something somebody did. The table's own docstring makes "
        "the argument: an entry must outlive what it names, or a history quietly loses its own "
        "reversals. Nothing can reach one once the asset has gone (it is read by asset id, from "
        "a file's own screen), so it is evidence rather than a leak."
    ),
}

#: Tables that hold an asset id only inside one write: staged and emptied before it commits. The
#: module that creates one must also hold an unconditional `DELETE FROM` it.
SCRATCH: dict[str, str] = {
    "visibility_pending": (
        "The pairs a visibility recompute is working on. Staged at the start of the write, read "
        "by each of its steps, and emptied on the way out (`_CLEAR_PENDING` in "
        "kernel/access/visibility.py). Sift has one writer, so nothing sees it in between."
    ),
}

_CREATES = "CREATE TABLE IF NOT EXISTS {table}"


def emptied_in(source: str, table: str) -> bool:
    """Whether this source empties the whole table: a `DELETE FROM` it that ends the statement (a
    quote or a semicolon), since a `WHERE` on the next line is not an emptying."""
    return re.search(rf"DELETE\s+FROM\s+{re.escape(table)}\s*[\"';]", source) is not None


def _emptied_where_created(table: str) -> bool:
    """The module that creates the table is the one that empties it: elsewhere it would be a wipe,
    not the scratch discipline."""
    creates = _CREATES.format(table=table)
    for path in SOURCE.rglob("*.py"):
        if "tests" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if creates in source:
            return emptied_in(source, table)
    return False


def _holds_an_asset_id(column: str) -> bool:
    """Whether a column name says it holds an asset id: `asset_id`, or ending `_asset_id`."""
    return column == "asset_id" or column.endswith("_asset_id")


_TABLES = (
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
)

# The pragma FUNCTION, which takes `?`: the `PRAGMA table_info(x)` statement takes no bound
# parameter, and no SQL in Sift pastes a name into a string.
_KEYS_OF = 'SELECT "from", "table" FROM pragma_foreign_key_list(?)'
_COLUMNS_OF = "SELECT name FROM pragma_table_info(?)"


async def keyless_columns(database: Database) -> dict[str, list[str]]:
    """Every table in this database holding an asset id no foreign key reaches, read off a real
    database: a table rebuilt by a later migration can lose a key its `CREATE TABLE` shows."""
    found: dict[str, list[str]] = {}
    tables = await database.fetch_all(_TABLES)
    for row in tables:
        name = str(row["name"])
        keys = await database.fetch_all(_KEYS_OF, (name,))
        keyed = {str(key["from"]) for key in keys if str(key["table"]) == "assets"}
        columns = await database.fetch_all(_COLUMNS_OF, (name,))
        holding = [
            str(column["name"])
            for column in columns
            if _holds_an_asset_id(str(column["name"])) and str(column["name"]) not in keyed
        ]
        if holding:
            found[name] = holding
    return found


#: A virtual table's definition in the source: `semantic_frames` is made on first need, not at boot
#: (its extension may be absent), so a fresh database does not hold it.
_VIRTUAL_TABLE = re.compile(
    r"CREATE\s+VIRTUAL\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+USING\s+\w+\((.*?)\)",
    re.IGNORECASE | re.DOTALL,
)


def virtual_tables_in(source: str) -> dict[str, list[str]]:
    """Virtual tables declared in this source and their asset-id columns: a virtual table can carry
    no foreign key, so each is in scope by construction."""
    found: dict[str, list[str]] = {}
    for name, body in _VIRTUAL_TABLE.findall(source):
        columns = [part.strip().split()[0] for part in body.split(",") if part.strip()]
        holding = [column for column in columns if _holds_an_asset_id(column)]
        if holding:
            found[name] = holding
    return found


def _virtual_tables_in_the_tree() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(SOURCE.rglob("*.py")):
        if "tests" in path.parts:
            continue
        found.update(virtual_tables_in(path.read_text(encoding="utf-8")))
    return found


async def _fresh(tmp_path: Path) -> Database:
    database = Database(tmp_path / "shape.sqlite3")
    await database.connect()
    await database.initialize_schema()
    return database


async def test_every_keyless_asset_id_is_somebody_s_job(tmp_path: Path) -> None:
    """The rule itself, over a database Sift really built."""
    database = await _fresh(tmp_path)
    try:
        found = await keyless_columns(database)
    finally:
        await database.close()

    # Known positive: these three are keyless, `file_moves` on purpose, so the finder finds.
    assert set(found) >= {"assets_fts", "assets_fts_rows", "file_moves"}, (
        f"the check found {sorted(found)}, which is fewer than the columns known to be keyless,"
        " so it is no longer looking at what it claims to look at"
    )

    owned = declared_tables()
    orphaned = {
        table: columns
        for table, columns in found.items()
        if table not in owned and table not in KEEPS_ITS_IDS and table not in SCRATCH
    }
    assert not orphaned, (
        f"these hold an asset id that no foreign key reaches, and nothing clears them: {orphaned}."
        " Give the column a REFERENCES assets(id), register a forgetting for the table in"
        " kernel/forgetting.py, add it to KEEPS_ITS_IDS with the reason it stays true, or to"
        " SCRATCH if it is emptied inside the write that fills it."
    )
    kept = sorted(table for table in SCRATCH if not _emptied_where_created(table))
    assert not kept, (
        f"these are listed as scratch and the module creating them does not empty them: {kept}."
        " A scratch table that is no longer cleared is a store that keeps asset ids, and belongs"
        " with the other rules."
    )


def test_a_virtual_table_holding_an_asset_id_is_somebody_s_job() -> None:
    owned = declared_tables()
    orphaned = {
        table: columns
        for table, columns in _virtual_tables_in_the_tree().items()
        if table not in owned and table not in KEEPS_ITS_IDS
    }
    assert not orphaned, (
        f"these virtual tables hold an asset id and nothing clears them: {orphaned}. A virtual"
        " table can carry no foreign key, so a registration in kernel/forgetting.py is the only"
        " way one is ever cleared."
    )


async def test_nothing_is_declared_that_does_not_exist(tmp_path: Path) -> None:
    """Every declaration names a table that exists, or a renamed one leaves a claim about
    nothing."""
    database = await _fresh(tmp_path)
    try:
        real = {
            str(row["name"])
            for row in await database.fetch_all(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        await database.close()
    real |= set(_virtual_tables_in_the_tree())

    missing = sorted(set(declared_tables()) - real)
    assert not missing, (
        f"these are declared in kernel/forgetting.py and are not tables anywhere: {missing}."
        " A registration naming nothing reads as coverage and is not."
    )

    stale = sorted(set(KEEPS_ITS_IDS) - real)
    assert not stale, f"KEEPS_ITS_IDS names tables that do not exist: {stale}"

    stale = sorted(set(SCRATCH) - real)
    assert not stale, f"SCRATCH names tables that do not exist: {stale}"


def test_the_check_catches_a_table_nobody_clears() -> None:
    """A virtual table nobody clears is caught."""
    planted = virtual_tables_in(
        "CREATE VIRTUAL TABLE IF NOT EXISTS lookalikes USING vec0(\n"
        "  asset_id TEXT,\n  embedding float[8]\n)"
    )
    assert planted == {"lookalikes": ["asset_id"]}
    assert "lookalikes" not in declared_tables()


def test_the_scratch_proof_needs_a_clear_all() -> None:
    """A conditional delete is not an emptying."""
    assert emptied_in('_CLEAR = "DELETE FROM visibility_pending"', "visibility_pending")
    assert emptied_in("DELETE FROM visibility_pending;", "visibility_pending")
    assert not emptied_in("DELETE FROM visibility_pending WHERE asset_id = ?", "visibility_pending")
    assert not emptied_in("DELETE FROM visibility_pending_rows", "visibility_pending")
    assert not emptied_in("INSERT INTO visibility_pending", "visibility_pending")
    # A statement continued on the next line is not a clear-all.
    assert not emptied_in("DELETE FROM assets\nWHERE id = ?", "assets")
    # The one scratch table is emptied where it is made; a real store is not.
    assert _emptied_where_created("visibility_pending")
    assert not _emptied_where_created("assets")


def test_a_column_that_does_not_hold_an_asset_id_is_left_alone() -> None:
    """The rule is not simply "any column"."""
    assert _holds_an_asset_id("asset_id")
    assert _holds_an_asset_id("cover_asset_id")
    assert not _holds_an_asset_id("asset_ids")
    assert not _holds_an_asset_id("id")
    assert not _holds_an_asset_id("person_id")


def test_two_stores_cannot_claim_one_table() -> None:
    """A table registered twice is refused: two owners and neither can be relied on."""
    from sift.kernel.forgetting import register_forgetting

    with pytest.raises(ValueError, match="already cleared by"):
        register_forgetting("a-second-opinion", ("assets_fts",), lambda database: None)  # type: ignore[arg-type,return-value]


def test_a_forgetting_that_names_no_table_is_refused() -> None:
    """A registration naming no table is refused."""
    from sift.kernel.forgetting import register_forgetting

    with pytest.raises(ValueError, match="names no table"):
        register_forgetting("says-nothing", (), lambda database: None)  # type: ignore[arg-type,return-value]
