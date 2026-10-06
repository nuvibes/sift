# SPDX-License-Identifier: AGPL-3.0-or-later
"""The statement ledger's rules: a rise, a new statement, a library walk and growth are caught."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sift.kernel.access import Repository
from sift.kernel.config import Settings
from sift.kernel.content import ContentStore
from sift.kernel.db import Database, StatementRun, statement_budget
from sift.testing import statement_ledger as ledger
from sift.testing.fixture_library import fixture_library, questions

SMALL, LARGE = ledger.SIZES


def _seen(steps: int, *, stage: str = "db.read", scans: list[str] | None = None) -> ledger.Seen:
    return ledger.Seen(
        stage=stage, steps=steps, count=1, requests={"admin GET /x"}, scans=scans or []
    )


def _walks(
    small: int, large: int, *, stage: str = "db.read", scans: list[str] | None = None
) -> dict[int, dict[str, ledger.Seen]]:
    return {
        SMALL: {"select:a#1": _seen(small, stage=stage, scans=scans)},
        LARGE: {"select:a#1": _seen(large, stage=stage, scans=scans)},
    }


def _recorded(small: int, large: int, **more: object) -> dict[str, object]:
    return {
        "select:a#1": {
            "stage": "db.read",
            "steps": {str(SMALL): small, str(LARGE): large},
            "count": 1,
            **more,
        }
    }


def test_a_statement_costlier_than_its_record_is_caught() -> None:
    assert ledger.rises(_recorded(1000, 1000), _walks(1000, 1301)) != []
    # The noise allowance: a tenth and two hundred steps.
    assert ledger.rises(_recorded(1000, 1000), _walks(1000, 1300)) == []


def test_a_statement_the_record_never_saw_is_caught() -> None:
    assert ledger.unrecorded({}, _walks(10, 10)) != []
    assert ledger.unrecorded(_recorded(10, 10), _walks(10, 10)) == []


def test_a_statement_growing_with_the_library_is_caught_unless_priced() -> None:
    assert ledger.growth({}, _walks(1000, 4000)) != []
    assert ledger.growth(_recorded(1000, 4000, per="person"), _walks(1000, 4000)) == []
    assert ledger.growth({}, _walks(1000, 1600)) == []
    assert ledger.growth({}, _walks(1000, 9000, stage=ledger.SWEEP)) == []
    # A priced statement may grow as its record does, and no faster.
    assert ledger.growth(_recorded(1000, 16000, per="file"), _walks(1000, 16000)) == []
    assert ledger.growth(_recorded(1000, 4000, per="file"), _walks(1000, 16000)) != []


def test_a_new_library_walk_is_caught_and_a_recorded_one_is_not() -> None:
    walked = _walks(10, 10, scans=["assets"])
    assert ledger.scans(_recorded(10, 10), walked) != []
    assert ledger.scans(_recorded(10, 10, scans=["assets"]), walked) == []


def test_a_plan_names_the_library_tables_it_walks(tmp_path: Path) -> None:
    path = tmp_path / "plan.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE assets (id TEXT PRIMARY KEY, size INTEGER)")
    connection.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, size INTEGER)")
    connection.close()
    walking = "SELECT a.id FROM assets a WHERE a.size > ?"
    assert ledger.scanned(ledger.plan_of(path, walking, (1,)), walking) == ["assets"]
    seeking = "SELECT id FROM assets WHERE id = ?"
    assert ledger.scanned(ledger.plan_of(path, seeking, ("x",)), seeking) == []
    small = "SELECT id FROM notes WHERE size > ?"
    assert ledger.scanned(ledger.plan_of(path, small, (1,)), small) == []
    assert ledger.plan_of(path, "SELECT nothing FROM nowhere", ()) == []


def test_the_record_only_falls_on_its_own() -> None:
    kept = _recorded(1000, 1000)
    fell, refused = ledger.updated(kept, _walks(500, 500), {})
    assert refused == [] and fell["select:a#1"]["steps"] == {str(SMALL): 500, str(LARGE): 500}
    _same, refused = ledger.updated(kept, _walks(1000, 2000), {})
    assert refused != []
    rose, refused = ledger.updated(
        kept, _walks(1000, 2000), {"select:a#1": ledger.Raise("why", "file")}
    )
    assert refused == [] and rose["select:a#1"]["steps"][str(LARGE)] == 2000
    assert rose["select:a#1"]["why"] == "why" and rose["select:a#1"]["per"] == "file"
    _new, refused = ledger.updated({}, _walks(10, 10), {})
    assert refused != []
    new, refused = ledger.updated({}, _walks(10, 10), {"select:a#1": ledger.Raise("new")})
    assert refused == [] and "select:a#1" in new
    gone, refused = ledger.updated(
        {**kept, "select:b#2": kept["select:a#1"]}, _walks(1000, 1000), {}
    )
    assert refused == [] and "select:b#2" not in gone
    _scan, refused = ledger.updated(kept, _walks(1000, 1000, scans=["assets"]), {})
    assert refused != []
    _unseen, refused = ledger.updated(kept, _walks(1000, 1000), {"select:z#9": ledger.Raise("x")})
    assert refused != []


def test_a_walk_before_ready_is_caught_unless_its_reason_is_recorded() -> None:
    walked = _walks(10, 10, scans=["folders"])
    walked[SMALL]["select:a#1"].requests.add(ledger.BOOT)
    assert ledger.boot_scans(_recorded(10, 10), walked) != []
    exempt, refused = ledger.updated(
        _recorded(10, 10, scans=["folders"]), walked, {"select:a#1": ledger.Raise("why", boot=True)}
    )
    assert refused == [] and exempt["select:a#1"]["boot_why"] == "why"
    assert ledger.boot_scans(exempt, walked) == []
    assert ledger.boot_scans(_recorded(10, 10), _walks(10, 10, scans=["folders"])) == []
    # The boot's own housekeeping is judged by its walks alone.
    booting = _walks(10, 9000)
    for seen in booting.values():
        seen["select:a#1"].requests = {ledger.BOOT}
    assert ledger.unrecorded({}, booting) == []
    assert ledger.growth({}, booting) == []
    assert ledger.rises(_recorded(1, 1), booting) == []
    added, refused = ledger.updated({}, booting, {})
    assert refused == [] and "select:a#1" in added


def test_the_first_record_marks_what_already_grows_or_walks() -> None:
    block = ledger.first_block(_walks(1000, 4000, scans=["assets"]))
    assert block["select:a#1"]["per"] == "library"
    assert block["select:a#1"]["scans"] == ["assets"]
    assert block["select:a#1"]["why"] == ledger.STARTING_WHY


async def test_a_dropped_index_shows_as_a_rise(tmp_path: Path) -> None:
    """The ledger end to end on a small library: the same question, priced before and after the
    index it seeks by is dropped, is a rise the gate refuses."""
    lib = await fixture_library(2_000, 3, tmp_path / "small.sqlite3")
    heard: list[StatementRun] = []
    statement_budget().heard = heard
    try:
        before = await _newest_page(tmp_path, lib)
        connection = sqlite3.connect(lib.path)
        connection.execute("DROP INDEX ix_loc_asset")
        connection.close()
        after = await _newest_page(tmp_path, lib)
    finally:
        statement_budget().heard = None
    name = before.name
    assert after.name == name
    recorded = {name: {"stage": before.stage, "steps": {str(SMALL): before.steps}, "count": 1}}
    walked = {SMALL: {name: ledger.Seen(stage=after.stage, steps=after.steps, requests={"probe"})}}
    assert ledger.rises(recorded, walked) != []


async def _newest_page(tmp_path: Path, lib: object) -> StatementRun:
    database = Database(lib.path, readers=1)  # type: ignore[attr-defined]
    await database.connect()
    heard = statement_budget().heard
    assert heard is not None
    try:
        settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
        access = Repository(database, ContentStore(database, settings))
        asked = await questions(database, access, lib)  # type: ignore[arg-type]
        heard.clear()
        await asked["admin page, newest"]()
        return heard[-1]
    finally:
        await database.close()
