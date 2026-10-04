# SPDX-License-Identifier: AGPL-3.0-or-later
"""The retired scan history: a new library never gains its table, and the component stays.

The history it kept is the work ledger's now, which is what the Performance screen reads. The
component is still registered, at the version a library records, so a library that has been through
it reads as one this build knows rather than as one from a newer Sift.
"""

from __future__ import annotations

import pytest

from sift.kernel import benchmarks
from sift.kernel.db import Database, registered_components

pytestmark = pytest.mark.anyio

_TABLES = "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'scan_runs'"
_EVERYTHING = "SELECT type, name FROM sqlite_master ORDER BY type, name"


async def _everything(database: Database) -> list[tuple[str, str]]:
    return [(str(row["type"]), str(row["name"])) for row in await database.fetch_all(_EVERYTHING)]


async def test_a_fresh_library_never_makes_it(temp_db: Database) -> None:
    await temp_db.initialize_schema()

    assert await temp_db.fetch_all(_TABLES) == []


async def test_the_step_creates_nothing_at_any_version(temp_db: Database) -> None:
    before = await _everything(temp_db)
    async with temp_db.write() as connection:
        await benchmarks.initialize_benchmarks(connection, 0)
        await benchmarks.initialize_benchmarks(connection, benchmarks.VERSION)

    assert await _everything(temp_db) == before


def test_the_component_is_registered_where_a_library_records_it() -> None:
    """A name missing from the registry reads as a component this build has never heard of, which
    is the newer-library refusal; the baseline is the version itself, since no step remains."""
    component = registered_components()[benchmarks.COMPONENT]

    assert (component.version, component.baseline) == (benchmarks.VERSION, benchmarks.VERSION)
