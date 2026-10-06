# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Tasks screen is answered in a fixed number of statements: one per relation, never one per
task, as the list grows with every feature that declares one."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sift.kernel import db
from sift.kernel.db_readers import PointRead
from sift.slices.tasks.tests.test_route import TASKS, app, client, sign_in

__all__ = ["app", "client"]

pytestmark = [pytest.mark.integration]


@contextmanager
def _counted(monkeypatch: pytest.MonkeyPatch) -> Iterator[Counter[str]]:
    seen: Counter[str] = Counter()
    judged = db._judged

    @contextmanager
    def counting(
        stage: str, statement: str | PointRead, *rest: Any, **options: Any
    ) -> Iterator[object]:
        if not isinstance(statement, PointRead):
            seen[db.statement_name(statement)] += 1
        with judged(stage, statement, *rest, **options) as timing:
            yield timing

    monkeypatch.setattr(db, "_judged", counting)
    yield seen


def test_no_statement_is_asked_once_per_task(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sign_in(client, "admin")
    client.get(TASKS)
    with _counted(monkeypatch) as seen:
        assert client.get(TASKS).status_code == 200
    # A task writing its own History line is read through History's own read, one per task.
    history = "select:workbench_decision_subjects#"
    assert [name for name, n in seen.items() if n > 1 and not name.startswith(history)] == []
