# SPDX-License-Identifier: AGPL-3.0-or-later
"""The file record the viewer asks for on every open stays a handful of statements."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sift.kernel import db as db_module
from sift.kernel.log import Timing, timing_hook
from sift.slices.browse.tests.conftest import Library, sign_in

#: The most statements one read of a file's record may run, the session's own included.
MOST_STATEMENTS = 23


@pytest.fixture
def statements(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    """Every statement the database layer ran, by name."""
    heard: Counter[str] = Counter()

    @contextmanager
    def counted(stage: str, **fields: Any) -> Iterator[Timing]:
        with timing_hook(stage, **fields) as timing:
            yield timing
        if stage.startswith("db.") and "statement" in fields:
            heard[str(fields["statement"])] += 1

    monkeypatch.setattr(db_module, "timing_hook", counted)
    return heard


def test_a_files_record_is_read_in_a_handful_of_statements(
    client: TestClient, library: Library, statements: Counter[str]
) -> None:
    sign_in(client, "admin")
    client.get(f"/api/assets/{library.shared}")
    statements.clear()

    assert client.get(f"/api/assets/{library.shared}").status_code == 200

    assert sum(statements.values()) <= MOST_STATEMENTS, sorted(statements.items())
    # The History tab counts its own lines when it is asked; the record never walks them.
    assert not [name for name in statements if name.startswith("history.")]
    assert not [name for name in statements if "sqlite_master" in name]
