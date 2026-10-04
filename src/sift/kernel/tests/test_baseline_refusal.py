# SPDX-License-Identifier: AGPL-3.0-or-later
"""A library older than a component's baseline is refused whole, before anything is changed.

No step in this build can bring such a library forward, so opening it must leave it exactly as it
was: a library with some components brought forward and others refused could be opened neither by
this build nor by the release the refusal names.
"""

from __future__ import annotations

import re

# The file is read raw, on purpose: the proof is that the boot changed nothing in it.
import sqlite3  # nosemgrep: sift-no-database-driver-outside-kernel
from pathlib import Path

import pytest

import sift.main  # noqa: F401 (imported so every component registers itself)
from sift.kernel.db import (
    STEPS_LAST_SHIPPED_IN,
    VERDICT_UNREADABLE,
    Database,
    DatabaseError,
    inspect_database,
    registered_components,
    too_old_to_bring_forward,
)

pytestmark = pytest.mark.anyio


def _a_library_below_the_faces_baseline(target: Path) -> int:
    """A library whose faces are recorded one version below where this build starts them."""
    baseline = registered_components()["faces"].baseline
    assert baseline is not None and baseline > 1
    connection = sqlite3.connect(target)
    try:
        connection.execute(
            "CREATE TABLE schema_version (component TEXT PRIMARY KEY, version INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO schema_version VALUES ('faces', ?)", (baseline - 1,))
        connection.commit()
    finally:
        connection.close()
    return baseline - 1


async def test_the_boot_refuses_it_and_changes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "old.sqlite3"
    recorded = _a_library_below_the_faces_baseline(target)

    database = Database(target)
    await database.connect()
    try:
        with pytest.raises(DatabaseError, match=re.escape(f"Sift {STEPS_LAST_SHIPPED_IN}")):
            await database.initialize_schema()
    finally:
        await database.close()

    connection = sqlite3.connect(target)
    try:
        tables = [
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        ]
        versions = connection.execute("SELECT component, version FROM schema_version").fetchall()
    finally:
        connection.close()
    assert tables == ["schema_version"], "a component was brought up before the refusal"
    assert versions == [("faces", recorded)]


def test_the_library_reading_refuses_it_in_the_same_words(tmp_path: Path) -> None:
    """The switcher asks before it stops the running library; it must hear the boot's answer."""
    target = tmp_path / "old.sqlite3"
    recorded = _a_library_below_the_faces_baseline(target)

    report = inspect_database(target)

    assert report.verdict == VERDICT_UNREADABLE
    assert report.detail == too_old_to_bring_forward({"faces": recorded})
