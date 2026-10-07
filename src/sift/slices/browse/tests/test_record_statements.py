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
from sift.kernel.db_readers import PointRead
from sift.slices.browse.tests.conftest import Library, sign_in

#: The most statements one read of a file's record may run, the session's own included.
MOST_STATEMENTS = 17


@pytest.fixture
def statements(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    """Every statement the database layer ran, by name."""
    heard: Counter[str] = Counter()
    judged = db_module._judged

    @contextmanager
    def counted(
        stage: str, statement: str | PointRead, *rest: Any, **options: Any
    ) -> Iterator[Any]:
        heard[db_module.statement_name(statement)] += 1
        with judged(stage, statement, *rest, **options) as timing:
            yield timing

    monkeypatch.setattr(db_module, "_judged", counted)
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
