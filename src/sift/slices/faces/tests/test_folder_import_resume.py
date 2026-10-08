# SPDX-License-Identifier: AGPL-3.0-or-later
"""A person's folder an earlier import read whole is passed over without a picture read.

What a stopped import held stays held, so starting it again costs the pictures it never reached and
nothing more. A folder changed since, or one whose waiting person was forgotten, is read again.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import sift.slices.workbench.schema  # noqa: F401
from sift.kernel.config import Settings
from sift.kernel.db import Database
from sift.slices.faces import schema as faces_schema
from sift.slices.faces.folder_import import Tally
from sift.slices.faces.references import PersonReport
from sift.slices.faces.service import FaceService
from sift.slices.faces.store import Store
from sift.slices.faces.tests.conftest import FakeDetector, draw_face, noisy_frame
from sift.slices.faces.tests.test_schema_baseline import _parents

pytestmark = pytest.mark.integration


@pytest.fixture
def decoded(detector: FakeDetector, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Every picture the import decodes, in order; each holds one face."""
    frame = noisy_frame(400, 400, seed=2)
    detector.placed = {0: [(draw_face(frame, x=80, y=80, size=120), 0.9)]}
    seen: list[Path] = []

    async def decode(path: Path, settings: Settings, **_: object) -> np.ndarray:
        seen.append(path)
        return frame

    async def encode(chips: list[np.ndarray], settings: Settings) -> list[bytes]:
        return [f"picture-{index}".encode() for index in range(len(chips))]

    monkeypatch.setattr("sift.slices.faces.frames.decode_image", decode)
    monkeypatch.setattr("sift.slices.faces.crop.encode", encode)
    return seen


def _folder(tmp_path: Path, pictures: int) -> Path:
    folder = tmp_path / "references" / "Bryn Calloway"
    folder.mkdir(parents=True, exist_ok=True)
    for index in range(pictures):
        (folder / f"{index}.jpg").write_bytes(f"stand-in {index}".encode())
    return folder


async def test_a_folder_read_whole_before_is_passed_over_without_a_picture_read(
    service: FaceService, decoded: list[Path], tmp_path: Path
) -> None:
    folder = _folder(tmp_path, 2)
    first = await service.import_person_folder(folder, source=None)
    read = len(decoded)

    again = await service.import_person_folder(folder, source=None)

    assert (first.already, first.added, read) == (False, 2, 2)
    assert (again.already, again.added, len(decoded)) == (True, 0, read)


async def test_a_picture_added_since_reads_the_folder_again(
    service: FaceService, decoded: list[Path], tmp_path: Path
) -> None:
    await service.import_person_folder(_folder(tmp_path, 2), source=None)

    again = await service.import_person_folder(_folder(tmp_path, 3), source=None)

    assert again.already is False
    assert len(decoded) == 5


async def test_forgetting_the_waiting_person_lets_the_folder_be_read_again(
    service: FaceService, store: Store, decoded: list[Path], tmp_path: Path
) -> None:
    folder = _folder(tmp_path, 2)
    await service.import_person_folder(folder, source=None)
    (entry,) = await store.unclaimed_entries()
    async with store.database.write() as connection:
        assert await store.remove_entry_on(connection, str(entry["id"])) is not None

    again = await service.import_person_folder(folder, source=None)

    assert (again.already, again.added) == (False, 2)


async def test_a_folder_with_no_pictures_is_never_said_to_be_imported_before(
    service: FaceService, decoded: list[Path], tmp_path: Path
) -> None:
    folder = _folder(tmp_path, 0)
    await service.import_person_folder(folder, source=None)

    again = await service.import_person_folder(folder, source=None)

    assert again.already is False


def test_the_report_says_the_folders_passed_over() -> None:
    tally = Tally()
    tally.add(PersonReport(name="Bryn Calloway", already=True))
    tally.add(PersonReport(name="Cassia Lynn", already=True))

    assert tally.people == 0
    assert tally.to_check == []
    assert "2 folders imported before, not read again." in tally.said()


async def test_a_library_before_43_gains_the_read_pictures_and_twice_is_once(
    temp_db: Database,
) -> None:
    tables = "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'face_folder_read'"
    async with temp_db.write() as connection:
        await _parents(connection)
        await faces_schema.initialize(connection, on_disk=0)
        await connection.execute("DROP TABLE face_folder_read")

        await faces_schema.initialize(connection, on_disk=42)
        await faces_schema.initialize(connection, on_disk=42)

        assert len(list(await connection.execute_fetchall(tables))) == 1
