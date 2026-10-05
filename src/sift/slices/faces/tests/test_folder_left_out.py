# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which pictures a folder import left out: kept by file as it reads, answered by reason."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sift.kernel.db import Database
from sift.slices.faces import schema as faces_schema
from sift.slices.faces.folder_import import import_folder, left_out_files
from sift.slices.faces.models import Finding
from sift.slices.faces.references import Candidate, PersonReport
from sift.slices.faces.router_folder import folder_left_out
from sift.slices.faces.store import Store
from sift.slices.faces.store_left_out import LeftOutStore
from sift.slices.faces.tests.test_folder_import import Context, Faces, gallery, granted

pytestmark = pytest.mark.anyio


class _Job(Context):
    def __init__(self, job_id: str, payload: dict[str, Any], *, given: Path) -> None:
        super().__init__(payload, given=given)
        self.job = SimpleNamespace(id=job_id)


def _reports(root: Path) -> dict[str, PersonReport]:
    """Two people's folders: every reason once or more, one picture kept and one near copy."""

    def person(name: str, *pictures: tuple[str, tuple[Finding, ...]]) -> PersonReport:
        report = PersonReport(name=name, added=1)
        report.candidates = [
            Candidate(path=root / name / file, findings=found) for file, found in pictures
        ]
        return report

    return {
        "Bryn Calloway": person(
            "Bryn Calloway",
            ("a.jpg", (Finding.NO_FACE,)),
            ("b.jpg", (Finding.NO_FACE,)),
            ("c.jpg", (Finding.UNREADABLE,)),
            ("d.jpg", ()),
            ("e.jpg", (Finding.NEAR_DUPLICATE,)),
            ("f.jpg", (Finding.NEAR_DUPLICATE, Finding.ODD_ONE_OUT)),
        ),
        "Elina Sorrel": person(
            "Elina Sorrel",
            ("g.jpg", (Finding.TURNED_AWAY,)),
            ("h.jpg", (Finding.NO_FACE,)),
        ),
    }


async def _import(store: Store, tmp_path: Path, job_id: str) -> _Job:
    root = tmp_path / job_id
    if not root.exists():
        gallery(root, "Bryn Calloway", "Elina Sorrel")
    root = root.resolve()
    context = _Job(job_id, granted(root), given=root)
    faces = Faces(reports=_reports(root))

    async def ask(queue: object) -> None:
        return None

    await import_folder(
        context,  # type: ignore[arg-type]
        service=faces,  # type: ignore[arg-type]
        ask=ask,
        left_out=LeftOutStore(store.database),
    )
    return context


async def test_each_fold_lists_as_many_files_as_the_report_counts(
    store: Store, tmp_path: Path
) -> None:
    context = await _import(store, tmp_path, "j1")

    said = context.notes[-1]
    answer = await folder_left_out("j1", viewer=None, database=store.database)  # type: ignore[arg-type]

    folds = {one.reason: one.files for one in answer.left_out}
    assert folds == {
        "no_face": ["Bryn Calloway/a.jpg", "Bryn Calloway/b.jpg", "Elina Sorrel/h.jpg"],
        "odd_one_out": ["Bryn Calloway/f.jpg"],
        "turned_away": ["Elina Sorrel/g.jpg"],
        "unreadable": ["Bryn Calloway/c.jpg"],
    }
    assert answer.left_out[0].reason == "no_face"
    for one in answer.left_out:
        assert f"{len(one.files)} {one.words}" in said
    assert answer.near_copies == ["Bryn Calloway/e.jpg"]
    assert "1 photo kept though almost the same as another." in said


async def test_a_run_read_again_writes_nothing_twice_and_a_new_import_clears_the_last(
    store: Store, tmp_path: Path
) -> None:
    count = "SELECT job_id, COUNT(*) AS n FROM face_folder_left_out GROUP BY job_id"
    await _import(store, tmp_path, "j1")
    await _import(store, tmp_path, "j1")
    first = [tuple(row) for row in await store.database.fetch_all(count, ())]

    await _import(store, tmp_path, "j2")
    after = [tuple(row) for row in await store.database.fetch_all(count, ())]

    assert (first, after) == ([("j1", 7)], [("j2", 7)])


async def test_the_rows_kept_stop_at_the_cap(store: Store) -> None:
    left_out = LeftOutStore(store.database)
    rows = [(f"Bryn Calloway/{index}.jpg", "no_face") for index in range(5)]

    await left_out.keep("j1", rows[:2], most=3)
    await left_out.keep("j1", rows[2:], most=3)
    await left_out.keep("j1", [], most=3)

    assert len(await left_out.of("j1")) == 3


def test_a_picture_reached_outside_the_chosen_folder_is_named_by_its_file(tmp_path: Path) -> None:
    report = PersonReport(name="Bryn Calloway")
    report.candidates = [Candidate(path=Path("/elsewhere/x.jpg"), findings=(Finding.TOO_SMALL,))]

    assert left_out_files(report, tmp_path) == [("x.jpg", "too_small")]


async def test_a_library_before_40_gains_the_table_and_twice_is_once(temp_db: Database) -> None:
    tables = "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'face_folder_left_out'"
    async with temp_db.write() as connection:
        await connection.execute("CREATE TABLE people (id TEXT PRIMARY KEY)")
        await connection.execute("CREATE TABLE assets (id TEXT PRIMARY KEY)")
        await faces_schema.initialize(connection, on_disk=0)
        await connection.execute("DROP TABLE face_folder_left_out")

        await faces_schema.initialize(connection, on_disk=39)
        await faces_schema.initialize(connection, on_disk=39)

        assert len(list(await connection.execute_fetchall(tables))) == 1
