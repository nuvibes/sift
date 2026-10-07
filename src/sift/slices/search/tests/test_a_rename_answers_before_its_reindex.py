# SPDX-License-Identifier: AGPL-3.0-or-later
"""A rename answers before its files are reindexed: the request's work does not grow with them.

A person, tag or collection on thousands of files held every other write for seconds while their
search text was rewritten inside the request. Priced here in SQLite's own steps, which are the same
on any machine: a rename of a person on three files costs what one on one file costs, and no
statement of it touches the search index. The job that does is `test_reindex`'s.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from sift.kernel.db import StatementRun, statement_budget
from sift.slices.search.tests.conftest import World, db_path, sign_in, write

pytestmark = [pytest.mark.integration]

#: Room for a read that seeks a few more rows; a rewrite of one file's index is far more.
_SLACK_STEPS = 60


@pytest.fixture
def hearing() -> Iterator[list[StatementRun]]:
    """Opened before the app, so every connection it opens counts its steps."""
    heard: list[StatementRun] = []
    statement_budget().heard = heard
    try:
        yield heard
    finally:
        statement_budget().heard = None


def _context(run: StatementRun) -> bool:
    """A read of who is asking and what they set, kept by caches that lapse on a slow machine, so a
    request runs it or not by the clock: not part of what a rename costs."""
    return run.name.startswith(("auth.", "settings.")) or "cache_stamp FROM users" in run.sql


def _rename(
    client: TestClient, heard: list[StatementRun], person: str, name: str
) -> list[StatementRun]:
    # Warmed just before, so both measured renames find the request's caches in the same state.
    assert client.put(f"/api/people/{person}", json={"name": f"{name} first"}).status_code == 200
    heard.clear()
    assert client.put(f"/api/people/{person}", json={"name": name}).status_code == 200
    return [run for run in heard if not _context(run)]


def test_a_rename_costs_the_same_on_three_files_as_on_one_and_never_writes_the_index(
    hearing: list[StatementRun], client: TestClient, world: World
) -> None:
    sign_in(client)
    _rename(client, hearing, world.person, "Warm up")  # the first request's own reads, once
    on_one = _rename(client, hearing, world.person, "Bryn Calloway")
    write(
        db_path(client),
        [
            (
                "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)",
                (world.walk, world.person),
            ),
            (
                "INSERT INTO asset_people (asset_id, person_id) VALUES (?, ?)",
                (world.private, world.person),
            ),
        ],
    )
    on_three = _rename(client, hearing, world.person, "Cassia Lynn")

    for run in on_one + on_three:
        assert "assets_fts" not in run.sql, f"the rename rewrote the search index: {run.name}"
    assert len(on_three) == len(on_one), "the rename's statements grew with its files"
    one, three = sum(run.steps for run in on_one), sum(run.steps for run in on_three)
    assert three - one <= _SLACK_STEPS, f"the rename's work grew with its files: {one} to {three}"
