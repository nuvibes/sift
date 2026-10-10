# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file's read goes ahead of the work made from files already read, and Activity says so."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from sift.kernel import lanes
from sift.kernel.config import Settings, get_settings
from sift.kernel.hardware import HardwareReport
from sift.kernel.http import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from sift.kernel.jobs import JobContext
from sift.kernel.jobs.worker_pool import WorkerPool
from sift.main import create_app
from sift.slices.media_jobs import probing, read_first
from sift.slices.media_jobs.activity_families import (
    _AFTER_THE_READ,
    PACE_WINDOW_SECONDS,
    WAITING_FOR_QUIET_HOURS,
    _joined,
)
from sift.slices.media_jobs.activity_wire import FamilyOfWork
from sift.slices.media_jobs.read_first import READ_FIRST_ON_SHARE, after_the_read_first
from sift.testing.auth import establish_session

ROUTER = sys.modules["sift.slices.media_jobs.router"]


async def test_a_probe_reads_as_a_files_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lanes, "READS_FIRST", True)
    kinds: list[int] = []

    async def read(_context: JobContext, **_handed: object) -> None:
        kinds.append(lanes._RANK.get())

    monkeypatch.setattr(probing, "_probe", read)
    await probing.probe(
        cast(JobContext, None), settings=cast(Settings, None), hardware=cast(HardwareReport, None)
    )

    assert kinds == [lanes.READ]
    assert lanes._RANK.get() == lanes.ORDINARY


def _row(label: str, *, outstanding: int = 5, **more: Any) -> FamilyOfWork:
    return FamilyOfWork(label=label, types=[], outstanding=outstanding, task=None, **more)


class _Folders:
    def __init__(self, *where: str) -> None:
        self.where = where
        self.asked = 0

    async def roots(self) -> list[SimpleNamespace]:
        self.asked += 1
        return [SimpleNamespace(name=path.rsplit("/", 1)[-1], abs_path=path) for path in self.where]


@pytest.fixture
def share_read_first(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    keys = {"nas"}
    monkeypatch.setattr(
        lanes, "installed", lambda: SimpleNamespace(reading_first=lambda _within: keys)
    )
    monkeypatch.setattr(
        lanes,
        "storage_for",
        lambda path: SimpleNamespace(key="nas" if path.parts[1] == "nas" else "disk"),
    )
    return keys


async def _said(library: _Folders | None, **rows: FamilyOfWork) -> dict[str, FamilyOfWork]:
    return await after_the_read_first(dict(rows), cast(Any, library), _AFTER_THE_READ, _joined)


@pytest.mark.usefixtures("share_read_first")
async def test_a_pass_after_the_read_says_the_share_reads_its_files_first() -> None:
    said = await _said(
        _Folders("/nas/Films", "/disk/Local", "/nas/Shows"),
        scan=_row("Scan"),
        generate=_row("Generate"),
        fingerprint=_row("Fingerprint", outstanding=0),
        identify=_row("Identify", reason=WAITING_FOR_QUIET_HOURS),
        semantic=_row("Smart Search", pace="Already said."),
    )

    assert said["generate"].pace == READ_FIRST_ON_SHARE.format(folders="Films and Shows")
    assert said["scan"].pace is None, "the read is not after itself"
    assert said["fingerprint"].pace is None, "nothing under way"
    assert said["identify"].pace is None, "a pass with a reason says that"
    assert said["semantic"].pace == "Already said."


async def test_nothing_is_said_where_no_share_reads_first(
    monkeypatch: pytest.MonkeyPatch, share_read_first: set[str]
) -> None:
    on_disk = _Folders("/disk/Local")
    assert (await _said(on_disk, generate=_row("Generate")))["generate"].pace is None
    assert (await _said(None, generate=_row("Generate")))["generate"].pace is None

    share_read_first.clear()
    quiet = _Folders("/nas/Films")
    assert (await _said(quiet, generate=_row("Generate")))["generate"].pace is None
    assert quiet.asked == 0, "the folders are not asked for while no share reads first"

    monkeypatch.setattr(lanes, "installed", lambda: None)
    assert (await _said(quiet, generate=_row("Generate")))["generate"].pace is None


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("SIFT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIFT_CACHE_DIR", str(tmp_path / "cache"))

    async def _no_workers(self: WorkerPool) -> None: ...

    monkeypatch.setattr(WorkerPool, "start", _no_workers)
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


@pytest.mark.integration
def test_activity_carries_the_sentence(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def said(answer: dict[str, FamilyOfWork], *_: object) -> dict[str, FamilyOfWork]:
        answer["generate"] = answer["generate"].model_copy(update={"pace": "Read first."})
        return answer

    monkeypatch.setattr(ROUTER, "after_the_read_first", said)
    _user, token, csrf = establish_session(
        client.app.state.database.path,  # type: ignore[attr-defined]
        role="admin",
        username="reads-first",
        password="Reads-First-Passw0rd!",
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    client.headers[CSRF_HEADER_NAME] = csrf

    assert client.get("/api/jobs").json()["families"]["generate"]["pace"] == "Read first."
    assert read_first.WINDOW_SECONDS == PACE_WINDOW_SECONDS
